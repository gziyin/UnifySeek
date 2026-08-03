from __future__ import annotations

from pathlib import Path

from ai_dev_researcher.storage.docling_parser import parse_with_docling
from ai_dev_researcher.storage.normalized_docs import (
    _normalize_docx,
    _normalize_pdf,
    normalize_document,
)


def test_parse_with_docling_returns_none_when_not_installed(monkeypatch, tmp_path: Path):
    """docling 未安装时返回 None（触发上层回退），不抛异常。"""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("docling"):
            raise ImportError("docling not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    assert parse_with_docling(pdf) is None


def test_parse_with_docling_ignores_unsupported_ext(tmp_path: Path):
    txt = tmp_path / "note.txt"
    txt.write_text("hello", encoding="utf-8")
    assert parse_with_docling(txt) is None


def test_normalize_pdf_keeps_page_markers(tmp_path: Path):
    """pypdf 回退路径：输出应保留 [PAGE n] 标记。"""
    import pypdf
    from pypdf.generic import DecodedStreamObject, NameObject

    pdf_path = tmp_path / "two.pdf"
    writer = pypdf.PdfWriter()
    for text in ["first page", "second page"]:
        page = writer.add_blank_page(width=72, height=72)
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 10 50 Td ({text}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = stream
    with pdf_path.open("wb") as fh:
        writer.write(fh)

    text = _normalize_pdf(pdf_path, max_pages=10)
    assert "[PAGE 1]" in text
    assert "[PAGE 2]" in text


def test_normalize_docx_keeps_paragraph_markers(tmp_path: Path):
    from docx import Document

    docx_path = tmp_path / "doc.docx"
    doc = Document()
    doc.add_paragraph("alpha")
    doc.add_paragraph("beta")
    doc.save(str(docx_path))

    text = _normalize_docx(docx_path)
    assert "[PARAGRAPH 1]" in text
    assert "alpha" in text
    assert "beta" in text


def test_normalize_document_docling_fallback_to_pypdf(tmp_path: Path, monkeypatch):
    """docling 不可用时，normalize_document 应回退到 pypdf 且不抛异常。"""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("docling"):
            raise ImportError("docling not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    import pypdf
    from pypdf.generic import DecodedStreamObject, NameObject

    pdf_path = tmp_path / "doc.pdf"
    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=72, height=72)
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 10 50 Td (fallback works) Tj ET")
    page[NameObject("/Contents")] = stream
    with pdf_path.open("wb") as fh:
        writer.write(fh)

    text = normalize_document(pdf_path, max_chars=10_000)
    assert "[PAGE 1]" in text
