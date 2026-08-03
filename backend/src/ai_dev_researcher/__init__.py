"""AI Dev Researcher package."""

__version__ = "0.1.0"

# Windows DLL 加载顺序防护：langchain_core 顶层会导入 transformers，
# 而 transformers/tokenizers 若先于 torch 加载会导致 c10.dll 初始化失败
# （WinError 1114）。必须在任何 transformers 导入前强制先加载 torch。
# 未安装 torch（纯 dev/agent 环境）时该调用为 no-op。
try:
    from ai_dev_researcher.storage.torch_guard import ensure_torch_loaded

    ensure_torch_loaded()
except Exception:  # noqa: BLE001 - 包导入绝不因 RAG 可选依赖失败
    pass

