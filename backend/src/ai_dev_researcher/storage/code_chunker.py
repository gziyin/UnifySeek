"""Structure-aware code chunking for the local knowledge base index.

WP-A (A2): splits source files into semantic chunks so that the RAG index
can retrieve code by function/class/method boundaries instead of hard line
windows.

- ``.py`` files are parsed with :mod:`ast` (recursive walk of
  ``FunctionDef``/``AsyncFunctionDef``/``ClassDef``, including nested
  functions).
- ``.js/.ts/.tsx/.jsx`` files use a conservative lightweight regex extractor
  covering ``export default``, ``export {}``, class methods and arrow
  functions (tree-sitter/babel evaluation is deferred; .py via ast remains
  the source of truth).
- Other text files (md/txt/json/yaml/toml) fall back to paragraph chunking
  reusing :func:`ai_dev_researcher.storage.vector_store.split_text_into_chunks`.

Output chunks are ``ChunkInfo`` 6-tuples
``(text, symbol, kind, parent_symbol, start_line, end_line)``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import NamedTuple

from ai_dev_researcher.storage.vector_store import split_text_into_chunks

CODE_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx"}
DOC_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml", ".toml"}
SUPPORTED_EXTENSIONS = CODE_EXTENSIONS | DOC_EXTENSIONS

DEFAULT_MAX_TOKENS = 512
_CHARS_PER_TOKEN = 4


class ChunkInfo(NamedTuple):
    """A single indexable chunk of source text."""

    text: str
    symbol: str
    kind: str
    parent_symbol: str
    start_line: int
    end_line: int


# ---------------------------------------------------------------------------
# generic helpers
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Approximate token count (4 chars per token, same heuristic as the doc store)."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _window_chars(max_tokens: int) -> int:
    return max(1, max_tokens * _CHARS_PER_TOKEN)


def _char_split(text: str, window: int) -> list[str]:
    """Sliding-window split with 1/8 overlap; only used as a last resort."""
    if not text.strip():
        return []
    step = window * 7 // 8
    pieces: list[str] = []
    offset = 0
    while offset < len(text):
        end = min(offset + window, len(text))
        piece = text[offset:end]
        if piece.strip():
            pieces.append(piece)
        if end == len(text):
            break
        offset = end - step
    return pieces


def _pack_segments(segments: list[str], window: int) -> list[str]:
    """Greedily pack logical segments into chunks not exceeding ``window`` chars."""
    chunks: list[str] = []
    current = ""
    for seg in segments:
        if len(seg) > window:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_char_split(seg, window))
        elif len(current) + len(seg) <= window:
            current += seg
        else:
            if current:
                chunks.append(current)
            current = seg
    if current:
        chunks.append(current)
    return chunks


def _break_lines(lines: list[str]) -> set[int]:
    """Logical break positions: blank lines and comment-section starts."""
    breaks: set[int] = {0, len(lines)}
    for i, line in enumerate(lines):
        if not line.strip():
            breaks.add(i + 1)
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(("#", "//", "/*")):
            prev = lines[i - 1] if i > 0 else ""
            if not prev.strip() or not prev.lstrip().startswith(("#", "//", "/*")):
                breaks.add(i)
    return breaks


def _split_long_text(text: str, max_tokens: int) -> list[str]:
    """Split long text at logical breakpoints (blank lines / comment sections)."""
    lines = text.splitlines(keepends=True)
    if not lines:
        return []
    window = _window_chars(max_tokens)
    breaks = _break_lines(lines)
    segments: list[str] = []
    for start, end in zip(sorted(breaks), sorted(breaks)[1:]):
        seg = "".join(lines[start:end])
        if seg.strip():
            segments.append(seg)
    chunks = _pack_segments(segments, window)
    return chunks or ([text] if text.strip() else [])


def _part_end_line(start_line: int, part: str) -> int:
    """Inclusive end line for a text part starting at ``start_line``."""
    count = part.count("\n")
    if part.endswith("\n"):
        return start_line + max(0, count - 1)
    return start_line + count


# ---------------------------------------------------------------------------
# Python (ast-based)
# ---------------------------------------------------------------------------


def _node_start_line(node: ast.AST) -> int:
    """First source line of an AST node, including decorators when present."""
    lineno = int(getattr(node, "lineno", 1))
    decorators = getattr(node, "decorator_list", None) or []
    if decorators:
        return min(int(getattr(d, "lineno", lineno)) for d in decorators)
    return lineno


def _source_segment(source: str, node: ast.AST) -> str:
    """Full-line source segment for an AST node.

    Unlike :func:`ast.get_source_segment`, this keeps inline trailing comments
    on the last statement line (``end_col_offset`` would cut them) and appends
    trailing blank/comment lines that still belong to the node's indented block.
    """
    lines = source.splitlines()
    if not lines:
        return ""
    start = _node_start_line(node)
    end = int(getattr(node, "end_lineno", start))
    end = max(end, min(end + 1, len(lines)))  # include the full end line

    # Body indentation used to bound the trailing comment/blank scan.
    indent = ""
    for stmt in getattr(node, "body", None) or []:
        line_text = lines[stmt.lineno - 1] if 0 < stmt.lineno <= len(lines) else ""
        indent = line_text[: len(line_text) - len(line_text.lstrip())]
        if indent:
            break
    while end < len(lines):
        line_text = lines[end]
        if not line_text.strip():
            end += 1
            continue
        leading = line_text[: len(line_text) - len(line_text.lstrip())]
        if line_text.lstrip().startswith("#") and len(leading) >= len(indent):
            end += 1
            continue
        break
    return "\n".join(lines[start - 1 : end])


def _split_long_segment(text: str, node: ast.AST | None, max_tokens: int) -> list[str]:
    """Split a long function/class body at logical breakpoints.

    Breakpoints are blank lines, comment-section starts, and the end of each
    direct child statement block of ``node`` (derived from AST ``end_lineno``),
    so chunks never cut a statement in half when a boundary is available.
    """
    lines = text.splitlines(keepends=True)
    if not lines:
        return []
    window = _window_chars(max_tokens)
    breaks = _break_lines(lines)
    if node is not None:
        base = _node_start_line(node)
        body = getattr(node, "body", None)
        if body:
            for stmt in body:
                end = getattr(stmt, "end_lineno", None)
                if end is not None:
                    rel = int(end) - base + 1
                    if 0 < rel < len(lines):
                        breaks.add(rel)
    segments: list[str] = []
    for start, end in zip(sorted(breaks), sorted(breaks)[1:]):
        seg = "".join(lines[start:end])
        if seg.strip():
            segments.append(seg)
    chunks = _pack_segments(segments, window)
    return chunks or ([text] if text.strip() else [])


def _classify_py(node: ast.AST, parent_kind: str) -> str:
    """Classify a Python definition node into a chunk kind."""
    if isinstance(node, ast.ClassDef):
        return "nested_class" if parent_kind else "class"
    is_async = isinstance(node, ast.AsyncFunctionDef)
    if parent_kind in {"class", "nested_class"}:
        return "async_method" if is_async else "method"
    if parent_kind in {
        "function",
        "async_function",
        "nested_function",
        "async_nested_function",
        "method",
        "async_method",
    }:
        return "async_nested_function" if is_async else "nested_function"
    return "async_function" if is_async else "function"


def chunk_python(
    source: str,
    file_name: str = "",
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[ChunkInfo]:
    """Chunk a Python source file by AST symbol boundaries.

    Recursively walks every ``FunctionDef``/``AsyncFunctionDef``/``ClassDef``
    (including nested functions and methods), emits one or more chunks per
    symbol, and records the enclosing symbol as ``parent_symbol``.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return chunk_paragraph(source, file_name, kind="paragraph", max_tokens=max_tokens)

    chunks: list[ChunkInfo] = []

    # Module docstring as its own chunk.
    if tree.body:
        first = tree.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(getattr(first, "value", None), ast.Constant)
            and isinstance(first.value.value, str)
            and first.value.value.strip()
        ):
            seg = ast.get_source_segment(source, first) or ""
            if seg.strip():
                chunks.append(
                    ChunkInfo(
                        text=seg,
                        symbol="",
                        kind="module_docstring",
                        parent_symbol="",
                        start_line=first.lineno,
                        end_line=first.end_lineno or first.lineno,
                    )
                )

    def walk(node: ast.AST, parent_symbol: str = "", parent_kind: str = "") -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = f"{parent_symbol}.{child.name}" if parent_symbol else child.name
                kind = _classify_py(child, parent_kind)
                seg = _source_segment(source, child)
                if not seg or not seg.strip():
                    walk(child, qual, kind)
                    continue
                base = _node_start_line(child)
                parts = _split_long_segment(seg, child, max_tokens)
                cursor = base
                for part in parts:
                    end_line = _part_end_line(cursor, part)
                    chunks.append(
                        ChunkInfo(
                            text=part,
                            symbol=qual,
                            kind=kind,
                            parent_symbol=parent_symbol,
                            start_line=cursor,
                            end_line=end_line,
                        )
                    )
                    cursor = end_line + 1
                walk(child, qual, kind)
            else:
                walk(child, parent_symbol, parent_kind)

    walk(tree)
    if not chunks:
        return chunk_paragraph(source, file_name, kind="paragraph", max_tokens=max_tokens)
    return chunks


# ---------------------------------------------------------------------------
# JS / TS (lightweight regex, conservative)
# ---------------------------------------------------------------------------

_JS_DECL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "export_default",
        re.compile(r"export\s+default\s+(?:class\s+|(?:async\s+)?function\s+)?([A-Za-z_$][\w$]*)?"),
    ),
    (
        "export_function",
        re.compile(r"export\s+(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"),
    ),
    (
        "export_const",
        re.compile(r"export\s+(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*="),
    ),
    (
        "export_block",
        re.compile(r"export\s*\{([^}]*)\}"),
    ),
    (
        "class",
        re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)"),
    ),
    (
        "function",
        re.compile(r"\b(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\("),
    ),
    (
        "arrow_function",
        re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"),
    ),
]
_JS_KEYWORD_GUARD = r"(?!(?:if|for|while|switch|catch|with|function|return|else)\b)"
_METHOD_RE = re.compile(
    r"\b" + _JS_KEYWORD_GUARD + r"(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)[^;{]*\{"
)


def _line_of(source: str, pos: int) -> int:
    return source.count("\n", 0, pos) + 1


def _collect_js_symbols(source: str) -> list[tuple[str, str, str, int, int]]:
    """Return [(symbol, kind, parent_symbol, start, end)] for JS/TS declarations."""
    matches: list[tuple[int, int, str, str]] = []
    for kind, pattern in _JS_DECL_PATTERNS:
        for m in pattern.finditer(source):
            if kind == "export_block":
                name = "export_block"
            else:
                name = m.group(1) if m.lastindex else ("default" if kind == "export_default" else "")
            matches.append((m.start(), m.end(), name, kind))
    matches.sort(key=lambda item: (item[0], item[1]))
    deduped: list[tuple[int, int, str, str]] = []
    for match in matches:
        if deduped and match[0] < deduped[-1][1]:
            continue
        deduped.append(match)

    spans: list[tuple[str, str, str, int, int]] = []
    for idx, (start, _end, name, kind) in enumerate(deduped):
        nxt = deduped[idx + 1][0] if idx + 1 < len(deduped) else len(source)
        if kind == "class" and name:
            body = source[start:nxt]
            method_matches = list(_METHOD_RE.finditer(body))
            first_method = method_matches[0].start() if method_matches else len(body)
            # Class header chunk (up to the first method) plus one chunk per method.
            spans.append((name, "class", "", start, start + first_method))
            for i, m in enumerate(method_matches):
                mname = m.group(1)
                mstart = start + m.start()
                mend = start + (method_matches[i + 1].start() if i + 1 < len(method_matches) else len(body))
                spans.append((f"{name}.{mname}", "method", name, mstart, mend))
        else:
            spans.append((name, kind, "", start, nxt))
    return spans


def chunk_js_ts(
    source: str,
    file_name: str = "",
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[ChunkInfo]:
    """Chunk a JS/TS/JSX/TSX source file with a conservative regex extractor."""
    spans = _collect_js_symbols(source)
    if not spans:
        return chunk_paragraph(source, file_name, kind="paragraph", max_tokens=max_tokens)
    chunks: list[ChunkInfo] = []
    for symbol, kind, parent, start, end in spans:
        seg = source[start:end].strip("\n")
        if not seg.strip():
            continue
        parts = _split_long_text(seg, max_tokens)
        cursor = _line_of(source, start)
        for part in parts:
            end_line = _part_end_line(cursor, part)
            chunks.append(
                ChunkInfo(
                    text=part,
                    symbol=symbol,
                    kind=kind,
                    parent_symbol=parent,
                    start_line=cursor,
                    end_line=end_line,
                )
            )
            cursor = end_line + 1
    if not chunks:
        return chunk_paragraph(source, file_name, kind="paragraph", max_tokens=max_tokens)
    return chunks


# ---------------------------------------------------------------------------
# paragraph fallback + dispatcher
# ---------------------------------------------------------------------------


def chunk_paragraph(
    source: str,
    file_name: str = "",
    kind: str = "paragraph",
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[ChunkInfo]:
    """Paragraph-based chunking for documents/notes, with line numbers."""
    if not source:
        return []
    symbol = Path(file_name).stem if file_name else ""
    results: list[ChunkInfo] = []
    for text, start_char, end_char in split_text_into_chunks(source, max_tokens=max_tokens):
        start_line = source.count("\n", 0, start_char) + 1
        end_line = source.count("\n", 0, end_char) + 1
        if end_char <= start_char:
            end_line = start_line
        results.append(
            ChunkInfo(
                text=text,
                symbol=symbol,
                kind=kind,
                parent_symbol="",
                start_line=start_line,
                end_line=end_line,
            )
        )
    return results


def chunk_file(
    source: str,
    file_name: str = "",
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[ChunkInfo]:
    """Route a file to the appropriate chunker by extension."""
    ext = Path(file_name).suffix.lower()
    if ext == ".py":
        return chunk_python(source, file_name, max_tokens=max_tokens)
    if ext in {".js", ".ts", ".tsx", ".jsx"}:
        return chunk_js_ts(source, file_name, max_tokens=max_tokens)
    kind = "markdown" if ext == ".md" else "text"
    return chunk_paragraph(source, file_name, kind=kind, max_tokens=max_tokens)


# ---------------------------------------------------------------------------
# P0 dual-encoding summary (heuristic, no LLM)
# ---------------------------------------------------------------------------

_DOCSTRING_RE = re.compile(r'("""|\'\'\')(.*?)\1', re.DOTALL)


def _first_docstring_line(text: str) -> str:
    match = _DOCSTRING_RE.search(text)
    if not match:
        return ""
    content = match.group(2).strip()
    if not content:
        return ""
    return content.splitlines()[0][:160]


def _first_comment_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()[:160]
    return ""


def generate_summary(
    text: str,
    symbol: str,
    kind: str,
    parent_symbol: str = "",
    file_name: str = "",
) -> str:
    """Lightweight natural-language summary for the P0 dual-encoding retrieval.

    Uses the symbol name, docstring first line, or first comment line only;
    never calls an LLM (HyDE-style rewriting is deferred to P1).
    """
    parts: list[str] = []
    context = symbol or (Path(file_name).stem if file_name else "")
    if symbol and parent_symbol:
        context = f"{parent_symbol}.{symbol}"
    if context:
        parts.append(f"{kind} {context}")
    doc = _first_docstring_line(text)
    if doc:
        parts.append(doc)
    else:
        comment = _first_comment_line(text)
        if comment:
            parts.append(comment)
        else:
            first_line = text.strip().splitlines()[0][:120] if text.strip() else ""
            if first_line:
                parts.append(first_line)
    summary = " ".join(part for part in parts if part)
    return summary[:300] or (text[:120] if text else "")
