"""Unit tests for WP-A structure-aware code chunking (storage/code_chunker.py)."""

from __future__ import annotations

from ai_dev_researcher.storage.code_chunker import (
    chunk_file,
    chunk_js_ts,
    chunk_paragraph,
    chunk_python,
    generate_summary,
)

PY_SAMPLE = '''\
"""Module docstring for the sample module."""

import os


def top_level(a, b):
    """Add two numbers."""
    return a + b


class Calculator:
    """A tiny calculator."""

    def add(self, a, b):
        return self.top(a, b)

    def top(self, a, b):
        def inner_helper(x):
            return x * 2

        async def inner_async(x):
            return x + 1

        return inner_helper(a) + inner_async(b)
'''


def test_python_symbols_and_nested_functions():
    chunks = chunk_python(PY_SAMPLE)
    by_symbol = {c.symbol: c for c in chunks}

    assert by_symbol["top_level"].kind == "function"
    assert by_symbol["top_level"].parent_symbol == ""
    assert by_symbol["Calculator"].kind == "class"
    assert by_symbol["Calculator.add"].kind == "method"
    assert by_symbol["Calculator.add"].parent_symbol == "Calculator"
    assert by_symbol["Calculator.top.inner_helper"].kind == "nested_function"
    assert by_symbol["Calculator.top.inner_helper"].parent_symbol == "Calculator.top"
    assert by_symbol["Calculator.top.inner_async"].kind == "async_nested_function"
    assert by_symbol["Calculator.top.inner_async"].parent_symbol == "Calculator.top"

    # Line numbers must point at the def/class line.
    assert by_symbol["top_level"].start_line == 6
    assert by_symbol["Calculator"].start_line == 11
    assert by_symbol["Calculator.add"].start_line == 14
    assert by_symbol["Calculator.top.inner_helper"].start_line == 18
    assert by_symbol["Calculator.top.inner_async"].start_line == 21


def test_python_module_docstring_chunk():
    chunks = chunk_python(PY_SAMPLE)
    doc_chunks = [c for c in chunks if c.kind == "module_docstring"]
    assert doc_chunks
    assert "Module docstring" in doc_chunks[0].text
    assert doc_chunks[0].start_line == 1


def test_python_long_function_logical_split():
    body: list[str] = []
    for i in range(40):
        body.append(f"    value_{i} = {'x' * 60}  # payload {i}")
        body.append("")
        body.append("    # section comment")
        body.append("")
    source = "def huge():\n" + "\n".join(body)

    chunks = chunk_python(source, max_tokens=128)
    huge = [c for c in chunks if c.symbol == "huge"]
    assert len(huge) > 1
    for c in huge:
        assert len(c.text) <= 128 * 4
        assert c.parent_symbol == ""
    # Splitting preserves all non-blank content in order (logical breakpoints,
    # never a hard line cut).
    joined = "".join(c.text for c in huge)
    assert " ".join(joined.split()) == " ".join(source.split())
    # Every chunk starts at a statement or comment line, not mid-line.
    for c in huge:
        first = c.text.lstrip().splitlines()[0] if c.text.strip() else ""
        assert first.startswith(("value_", "#", "def"))


def test_python_long_function_with_decorator_lines():
    source = (
        "@decorator\n"
        "@other\n"
        "def big():\n"
        + "".join(f"    x_{i} = {i}\n\n" for i in range(30))
    )
    chunks = chunk_python(source, max_tokens=64)
    big = [c for c in chunks if c.symbol == "big"]
    assert len(big) > 1
    # First chunk begins at the decorator line.
    assert big[0].start_line == 1
    assert big[0].text.lstrip().startswith("@decorator")


def test_python_syntax_error_falls_back_to_paragraph():
    chunks = chunk_python("def broken(:\n    return 1\n")
    assert chunks
    assert chunks[0].kind == "paragraph"


TS_SAMPLE = '''\
import { useState } from "react";

export default function App() {
  return <div>hello</div>;
}

export const helper = (x: number) => x * 2;

export { helper as helperAlias };

const localArrow = (name: string) => `hi ${name}`;

class Service {
  async run(name: string): Promise<string> {
    return `run ${name}`;
  }

  stop() {
    return "stopped";
  }
}
'''


def test_js_ts_extraction():
    chunks = chunk_js_ts(TS_SAMPLE)
    by_symbol = {c.symbol: c.kind for c in chunks}

    assert by_symbol.get("App") == "export_default"
    assert by_symbol.get("helper") == "export_const"
    assert by_symbol.get("localArrow") == "arrow_function"
    assert by_symbol.get("Service") == "class"
    assert by_symbol.get("Service.run") == "method"
    assert by_symbol.get("Service.stop") == "method"
    assert by_symbol.get("export_block") == "export_block"

    run_chunks = [c for c in chunks if c.symbol == "Service.run"]
    assert run_chunks
    assert run_chunks[0].parent_symbol == "Service"
    assert "run" in run_chunks[0].text


def test_document_paragraph_chunking():
    md = "# Title\n\nSome paragraph about embeddings.\n\nAnother paragraph with more words.\n"
    chunks = chunk_paragraph(md, file_name="notes.md", kind="markdown")
    assert chunks
    assert all(c.kind == "markdown" for c in chunks)
    assert chunks[0].symbol == "notes"
    assert chunks[0].start_line == 1

    routed = chunk_file("hello world\n\nsecond para\n", file_name="notes.txt")
    assert routed
    assert routed[0].kind == "text"
    assert routed[0].start_line == 1


def test_generate_summary_heuristic():
    summary = generate_summary(
        '"""Adds two numbers."""\ndef add(a, b):\n    return a + b',
        "add",
        "function",
        "",
    )
    assert "function add" in summary
    assert "Adds two numbers" in summary

    summary2 = generate_summary("# helper comment\nx = 1", "helper", "function", "mod")
    assert "mod.helper" in summary2
    assert "helper comment" in summary2

    summary3 = generate_summary("plain first line\nsecond line", "", "text", "", "notes.txt")
    assert "notes" in summary3
    assert "plain first line" in summary3
