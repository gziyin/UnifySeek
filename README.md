# AI Dev Researcher

面向 AI / Python / Agent 开发者的深度调研系统。灵感来自
[didilili/deepsearch-agents](https://github.com/didilili/deepsearch-agents)，
本仓库为洁净重构的独立实现：不复制其源码、Prompt、样式或测试数据。

## 当前进度

- 已完成：后端无 Agent 纵向切片（Fake Executor）+ 最小 React 研究台
  - Session / Upload / Run / Events / WebSocket / Report Artifact
  - SQLite 元数据 + 会话目录存储
  - 前端可上传、启动、观察时间线、查看来源与报告、下载 Markdown

## 快速开始

### 后端

```powershell
$venv = "E:\04Programming\CodingEnvironment\venvs\ai_dev_researcher"
cd d:\code\Projects\DeepSearch_Agent\ai_dev_researcher\backend
& "$venv\Scripts\python.exe" -m uvicorn ai_dev_researcher.main:app --host 127.0.0.1 --port 8000
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
& "E:\04Programming\CodingEnvironment\venvs\ai_dev_researcher\Scripts\python.exe" -m pytest -q
```

## 主线流程（当前 Fake 模式）

1. 打开研究台，自动创建 session
2. 上传 PDF/DOCX/MD/TXT（可选）
3. 填写问题并启动研究
4. 中间栏观察 Todo / 子智能体 / 事件
5. 右侧查看来源账本与 Markdown 报告并下载

后续：接入真实 DeepAgents + DeepSeek + Tavily，替换 Fake Executor。
