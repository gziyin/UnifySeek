# AI Dev Researcher

面向 AI / Python / Agent 开发者的深度调研系统。灵感来自
[didilili/deepsearch-agents](https://github.com/didilili/deepsearch-agents)，
本仓库为洁净重构的独立实现：不复制其源码、Prompt、样式或测试数据。

## 当前进度

- 已完成：后端无 Agent 纵向切片（Fake Executor）+ 最小 React 研究台
  - Session / Upload / Run / Events / WebSocket / Report Artifact
  - SQLite 元数据 + 会话目录存储
  - 前端可上传、启动、观察时间线、查看来源与报告、下载 Markdown
- 已完成：真实 Agent 闭环（DeepAgents + DeepSeek + Tavily），带引用的 Markdown 报告
- 已完成：本地知识库读取（K 类证据）、RAG 语义检索（docling + Chroma）、
  报告证据校验（degraded 降级）、事件流增强（URL/路径/行号）
- 待办（求职打磨）：README 决策叙事、架构图、demo 资料集

## 快速开始

### 一键启动（推荐）

双击 `start.bat`，同时启动后端（FastAPI）与前端（Vite）。

### 手动启动

```powershell
cd d:\code\Projects\DeepSearch_Agent\ai_dev_researcher\backend
.venv\Scripts\python.exe -m ai_dev_researcher.main
```

健康检查：`GET http://127.0.0.1:8000/api/health`

### 前端

```powershell
cd d:\code\Projects\DeepSearch_Agent\ai_dev_researcher\frontend
npm install
npm run dev
```

打开：`http://127.0.0.1:5173`

### 测试

```powershell
cd d:\code\Projects\DeepSearch_Agent\ai_dev_researcher\backend
.venv\Scripts\python.exe -m pytest tests/unit tests/e2e tests/integration -q
```

## 依赖组

```powershell
cd backend
uv sync --extra dev            # 基础 + 测试
uv sync --extra agent          # + DeepAgents/LangGraph/DeepSeek/Tavily（真实 Agent）
uv sync --extra rag            # + docling/sentence-transformers/chromadb（RAG 语义检索）
uv sync --extra agent --extra rag   # 全功能（真实 Agent + RAG）
```

> 注意：RAG 依赖包含 torch（约 2GB），首次加载 embedding 模型
> （all-MiniLM-L6-v2，约 80MB）会从 HF_HUB_CACHE 指向的目录离线加载。
> 模型已预置于 `E:/04Programming/Models`。

## 主线流程

1. 打开研究台，自动创建 session
2. 上传 PDF/DOCX/MD/TXT（可选）
3. 填写问题并启动研究（配置 `.env` 中的 DeepSeek/Tavily key 后为真实 Agent）
4. 中间栏观察 Todo / 子智能体 / 事件（时间线可展开查看工具输入、URL、行号）
5. 右侧查看来源账本（S/D/K 标签、完整 URL/路径、可复制）与报告（固定高度 + 导出）

## 知识库与 RAG

- 本地知识库：`knowledge_base/`（相对项目根），document-analyst 可读取
  `.md/.txt/.py/.json/.yaml/.toml` 文件并记录 K 类证据。
- 向量检索：上传文档解析后（docling 优先，失败回退 pypdf/python-docx），
  自动分块 embedding 入 Chroma；document-analyst 可用 `search_run_documents`
  语义定位片段并反查行号。RAG 依赖缺失时优雅降级为关键字读取。
