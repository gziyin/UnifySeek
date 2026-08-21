from __future__ import annotations

import logging
import warnings
from pathlib import Path

logger = logging.getLogger(__name__)


def parse_with_docling(path: Path) -> str | None:
    """使用 docling 解析 PDF/DOCX，输出带 [PAGE n] / 段落标记的规范化文本。

    失败时返回 None（不抛异常），由上层回退到 pypdf/python-docx。
    """
    try:
        # Windows DLL 加载顺序防护：torch 必须先于 transformers 加载。
        from ai_dev_researcher.storage.torch_guard import ensure_torch_loaded

        ensure_torch_loaded()
        from docling.document_converter import (
            DocumentConverter,
            PdfFormatOption,
        )
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            PdfPipelineOptions,
            RapidOcrOptions,
        )
    except ImportError:
        logger.info("docling not installed; falling back to pypdf/python-docx")
        return None

    ext = path.suffix.lower()
    if ext not in {".pdf", ".docx"}:
        return None

    try:
        import rapidocr

        rapidocr_model_dir = Path(rapidocr.__file__).resolve().parent / "models"
        rapidocr_options = RapidOcrOptions(
            backend="onnxruntime",
            rapidocr_params={
                "Global.model_root_dir": str(rapidocr_model_dir),
            },
        )
        pipeline_options = PdfPipelineOptions(
            do_ocr=True,
            ocr_options=rapidocr_options,
        )

        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                category=UserWarning,
                message=(
                    r"^update\(\) merge flag is is not specified, defaulting to False\.\n"
                    r"For more details, see https://github\.com/omry/omegaconf/issues/367$"
                ),
            )
            converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_options=pipeline_options,
                    )
                }
            )
            result = converter.convert(str(path))
        document = result.document
    except Exception as exc:  # noqa: BLE001
        logger.warning("docling parse failed for %s: %s", path.name, exc)
        return None

    try:
        # docling 2.x：按页输出文本，保留页码与表格结构。
        chunks: list[str] = []
        for page_no, page in enumerate(document.pages.values(), start=1):
            page_text = page.text
            if page_text is None:
                continue
            content = page_text.strip()
            if not content:
                continue
            chunks.append(f"[PAGE {page_no}]\n{content}")
        if not chunks:
            return None
        return "\n\n".join(chunks)
    except Exception as exc:  # noqa: BLE001
        logger.warning("docling text extraction failed for %s: %s", path.name, exc)
        return None
