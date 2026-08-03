"""torch 先加载防护。

Windows 下存在已知 DLL 加载顺序冲突：若 transformers/tokenizers（Rust 原生库）
先于 torch 加载，则 torch 的 c10.dll 初始化失败（WinError 1114）。
本模块提供 ensure_torch_loaded()，所有会触发 torch 导入的 RAG 入口
（docling_parser / embedding_provider）必须在导入 transformers 之前调用。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_LOADED = False


def ensure_torch_loaded() -> None:
    global _LOADED
    if _LOADED:
        return
    try:
        import torch  # noqa: F401
        _LOADED = True
        logger.debug("torch preloaded before transformers/tokenizers")
    except ImportError:
        # torch 未安装：RAG 依赖不完整，调用方自行降级。
        logger.info("torch not installed; RAG torch-dependent features degrade")
