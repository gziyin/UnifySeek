from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader
from docx import Document


SUPPORTED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".txt": "text/plain",
}


def guess_mime(filename: str) -> str | None:
    ext = Path(filename).suffix.lower()
    return SUPPORTED_EXTENSIONS.get(ext)


def normalize_document(path: Path, *, max_chars: int, max_pages: int = 100) -> str:
    ext = path.suffix.lower()
    if ext == ".bin":
        # Storage uses .bin; sniff by magic or caller should pass original name.
        raise ValueError("normalize_document requires typed source path")
    if ext == ".pdf":
        text = _normalize_pdf(path, max_pages=max_pages)
    elif ext == ".docx":
        text = _normalize_docx(path)
    elif ext in {".md", ".txt"}:
        text = _normalize_text(path)
    else:
        raise ValueError(f"unsupported file type: {ext}")
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[TRUNCATED]"
    return text


def normalize_bytes(
    data: bytes,
    *,
    filename: str,
    max_chars: int,
    max_pages: int = 100,
) -> str:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        tmp = Path("_tmp_normalize.pdf")
        # Caller should prefer path-based normalize; this helper is for tests.
        raise NotImplementedError("use path-based normalize for PDF")
    if ext == ".docx":
        raise NotImplementedError("use path-based normalize for DOCX")
    if ext in {".md", ".txt"}:
        text = data.decode("utf-8", errors="replace")
        return _number_lines(text, max_chars=max_chars)
    raise ValueError(f"unsupported file type: {ext}")


def _normalize_pdf(path: Path, *, max_pages: int) -> str:
    reader = PdfReader(str(path))
    if len(reader.pages) > max_pages:
        raise ValueError(f"PDF exceeds {max_pages} pages")
    chunks: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        page_text = (page.extract_text() or "").strip()
        chunks.append(f"[PAGE {index}]\n{page_text}")
    return "\n\n".join(chunks)


def _normalize_docx(path: Path) -> str:
    document = Document(str(path))
    chunks: list[str] = []
    for index, paragraph in enumerate(document.paragraphs, start=1):
        text = paragraph.text.strip()
        if text:
            chunks.append(f"[PARAGRAPH {index}]\n{text}")
    return "\n\n".join(chunks)


def _normalize_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    return _number_lines(text, max_chars=None)


def _number_lines(text: str, *, max_chars: int | None) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    numbered = [f"[LINE {i}] {line}" for i, line in enumerate(lines, start=1)]
    result = "\n".join(numbered)
    if max_chars is not None and len(result) > max_chars:
        return result[:max_chars] + "\n\n[TRUNCATED]"
    return result


_SAFE_NAME = re.compile(r"[^\w.\-()+ ]+", re.UNICODE)


def sanitize_display_name(name: str) -> str:
    cleaned = _SAFE_NAME.sub("_", Path(name).name).strip() or "upload.bin"
    return cleaned[:200]
