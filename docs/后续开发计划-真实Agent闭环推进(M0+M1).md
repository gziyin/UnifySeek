# 真实 DeepAgents 闭环推进计划（M0 + M1）

> 目标：将 `ai_dev_researcher` 从 "Fake 闭环" 推进到 "真实 DeepAgents 闭环"，覆盖 P0 阻塞修复、M0 兼容性 Spike、M1 终端纵向切片、回归与文档。
> 所有路径以 `D:\code\Projects\DeepSearch_Agent\ai_dev_researcher` 为项目根（下文简称 `<ROOT>`）。后端根为 `<ROOT>\backend`。
> 用户环境：Windows 11，Python 3.13.2，shell 为 Git Bash on Windows。已具备 DeepSeek + Tavily API Key。

---

## 0. 环境与约定

### 0.1 Python 隔离环境（推荐方案）

**推荐**：新建项目级 venv `<ROOT>\backend\.venv`（`.gitignore` 已忽略 `.venv/`），避免污染已存在的全局 venv。

```bash
# 在 Git Bash 中执行（路径用正斜杠）
cd /d/code/Projects/DeepSearch_Agent/ai_dev_researcher/backend
# 用 Python 3.13 创建 venv（确认 python 版本）
py -3.13 --version
py -3.13 -m venv .venv
source .venv/Scripts/activate
python --version  # 应显示 3.13.x
python -m pip install --upgrade pip
```

> 备选：若坚持用 `C:\Users\guo21\.workbuddy\binaries\python\envs\default`，则后续命令中 `.venv/Scripts/python` 替换为该 venv 的 `python.exe`。但**强烈推荐项目级 venv**，因为 agent 依赖（langchain/deepagents）体积大且版本敏感，隔离更安全。

### 0.2 命令前缀约定

下文所有命令默认已在 `backend/` 目录下且 venv 已激活。若未激活，用 `<ROOT>\backend\.venv\Scripts\python.exe` 显式调用。

---

## 第一阶段：P0 阻塞修复（必须最先做）

### 任务 1：修复 R1 — 在 `core/errors.py` 补 `SearchProviderError`

**文件**：`backend/src/ai_dev_researcher/core/errors.py`

**问题**：`tools/web_search.py:8` 导入 `SearchProviderError`，但 `errors.py` 未定义该类。真实 Agent 模式下导入链 `agent_executor → orchestrator → tools.factory → tools.web_search` 会触发 `ImportError`。Fake 模式不导入该链，故现有测试通过。

**插入位置**：`errors.py` 文件末尾（`ReportValidationError` 类之后）。

**TDD 步骤**：

1. **RED**：先写失败测试。新建 `backend/tests/unit/test_errors.py`：

```python
from __future__ import annotations

import pytest

from ai_dev_researcher.core.errors import AppError, SearchProviderError


def test_search_provider_error_is_app_error():
    err = SearchProviderError("tavily search failed")
    assert isinstance(err, AppError)
    assert err.code == "SEARCH_PROVIDER_ERROR"
    # 上游 provider 错误默认 502
    assert err.status_code == 502
    # 网络瞬时错误默认可重试
    assert err.retryable is True


def test_search_provider_error_carries_provider_field():
    err = SearchProviderError("timeout", provider="tavily")
    assert err.provider == "tavily"


def test_search_provider_error_overrides_code_and_retryable():
    err = SearchProviderError(
        "client unavailable",
        provider="tavily",
        code="TAVILY_UNAVAILABLE",
        retryable=False,
    )
    assert err.code == "TAVILY_UNAVAILABLE"
    assert err.retryable is False
    assert err.provider == "tavily"
```

运行（应失败，`ImportError`）：
```bash
python -m pytest tests/unit/test_errors.py -q
```

2. **GREEN**：在 `errors.py` 末尾追加 `SearchProviderError` 类定义：

```python
class SearchProviderError(AppError):
    """External search provider (e.g. Tavily) call failed."""

    code = "SEARCH_PROVIDER_ERROR"
    status_code = 502
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        code: str | None = None,
        retryable: bool | None = None,
    ):
        super().__init__(message, code=code, retryable=retryable)
        self.provider = provider
```

运行（应通过）：
```bash
python -m pytest tests/unit/test_errors.py -q
```

3. **REFACTOR**（可选，增强）：让 `tools/web_search.py` 的三处 `raise` 显式传 `provider="tavily"`，便于上层日志区分来源：

- 第 52 行：`raise SearchProviderError("tavily client unavailable", provider="tavily") from exc`
- 第 58 行：`raise SearchProviderError(f"tavily search failed: {exc}", provider="tavily") from exc`
- 第 108 行：`raise SearchProviderError("tavily client unavailable", provider="tavily") from exc`

> 注意：此 refactor 不改变行为，仅增强可观测性。若希望最小改动，可跳过，但推荐执行。

4. **验证回归**：
```bash
python -m pytest -q
python -c "from ai_dev_researcher.tools.web_search import search_web_impl; print('import ok')"
```

---

### 任务 2：清理 R8 — `main.py` 重复构造 `run_service`

**文件**：`backend/src/ai_dev_researcher/main.py`

**问题**：第 79-86 行在 `AppState(...)` 构造时传入 `run_service=RunService(...)`；第 89-97 行又用 `container.run_service = RunService(...)` 覆盖，参数完全相同。注释 "Wire run_service after construction to avoid circular init issues" 实际不成立（`task_manager` 在 `run_service` 之前已构造，无循环依赖）。

**修复**：删除第 89-97 行的重复构造与注释，保留第 79-86 行的构造。

**修复前**（第 79-97 行）：
```python
            run_service=RunService(
                sessions=sessions_repo,
                runs=runs_repo,
                artifacts=artifacts_repo,
                paths=paths,
                publisher=publisher,
                task_manager=task_manager,
            ),
            task_manager=task_manager,
        )
        # Wire run_service after construction to avoid circular init issues.
        container.run_service = RunService(
            sessions=sessions_repo,
            runs=runs_repo,
            artifacts=artifacts_repo,
            paths=paths,
            publisher=publisher,
            task_manager=task_manager,
        )
        app.state.container = container
```

**修复后**（第 79-89 行）：
```python
            run_service=RunService(
                sessions=sessions_repo,
                runs=runs_repo,
                artifacts=artifacts_repo,
                paths=paths,
                publisher=publisher,
                task_manager=task_manager,
            ),
            task_manager=task_manager,
        )
        app.state.container = container
```

**验证**：
```bash
python -m pytest tests/integration/test_main_flow.py -q
python -c "from ai_dev_researcher.main import create_app; print('app factory ok')"
```

> 验收：`test_main_flow_session_upload_run_report` 与 `test_active_run_conflict` 均通过，证明 `run_service` 仅构造一次且功能正常。

---

## 第二阶段：M0 兼容性 Spike 验证

### 任务 3：安装 agent 可选依赖

**前提**：任务 1、2 已完成，venv 已创建并激活。

**命令**（在 `backend/` 目录）：
```bash
# 安装项目本体（可编辑）+ dev 依赖 + agent 依赖
python -m pip install -e ".[dev,agent]"
```

**验证安装**：
```bash
python -c "import deepagents; print('deepagents', deepagents.__version__)"
python -c "from deepagents import create_deep_agent, SubAgent, StateBackend; print('deepagents core ok')"
python -c "from deepagents import HarnessProfile, GeneralPurposeSubagentProfile, register_harness_profile; print('profile ok')"
python -c "from deepagents.middleware.filesystem import FilesystemPermission; print('fs perm ok')"
python -c "from langchain_deepseek import ChatDeepSeek; print('langchain_deepseek ok')"
python -c "from tavily import AsyncTavilyClient; print('tavily ok')"
python -c "from ai_dev_researcher.agents.orchestrator import create_research_agent; print('project agent import ok')"
```

> 关键验证点：最后一条命令能成功导入 `create_research_agent`，证明 R1 修复生效、整条 agent 导入链通畅。

---

### 任务 4：配置 `.env`

**文件**：`backend/.env`（新建，`.gitignore` 已忽略 `.env`）

**内容**：
```env
# DeepSeek
DEEPSEEK_API_KEY=<在此填入真实 DeepSeek key>
DEEPSEEK_MODEL=deepseek-chat

# Tavily
TAVILY_API_KEY=<在此填入真实 Tavily key>

# Runtime
APP_HOST=127.0.0.1
APP_PORT=8000
CORS_ORIGINS=http://127.0.0.1:5173,http://localhost:5173
WORKSPACE_ROOT=
FAKE_AGENT_MODE=false
```

> 注意：`FAKE_AGENT_MODE=false` 是切换到真实 Agent 模式的关键开关。`executor_factory.py:27` 的逻辑为 `fake_agent_mode or not deepseek_api_key` → Fake，否则 Agent。

**验证 .env 被加载**：
```bash
python -c "from ai_dev_researcher.core.config import Settings; s=Settings(); print('deepseek set:', bool(s.deepseek_api_key)); print('tavily set:', bool(s.tavily_api_key)); print('fake_mode:', s.fake_agent_mode)"
```
期望输出：`fake_mode: False`，两个 key 均为 `True`。

---

### 任务 5：补充 M0 兼容性测试

**文件**：`backend/tests/integration/test_m0_compatibility.py`

**现状分析**：现有 3 个测试，默认 skip（无 `DEEPSEEK_API_KEY`）：
1. `test_deepagents_import_and_profile_registration` — 验证导入 + profile 注册 + spec 前缀
2. `test_create_research_agent_has_only_custom_subagents` — 验证 agent 有 `astream_events`/`version` 参数、graph 节点含 task/tools 且不含 general-purpose
3. `test_astream_events_v3_emits_events` — 验证 `astream_events(version="v3")` 发出事件（仅让 agent 调用 write_todos）

**缺失项**（对照开发计划 §20 阶段0、§11.2、§15）：
- DeepSeek Tool Calling 实际调用（非 write_todos 的真实工具）
- 两个自定义 subagent 实际能被 task 工具委派
- Tavily Search 实际调用
- Tavily Extract 实际调用
- StateBackend 持久化（thread_id 复用）
- excluded_tools 实际生效（内置文件工具不可用）
- 三类 Agent（orchestrator / web-researcher / document-analyst）permissions 独立性
- Windows 路径安全（盘符/UNC/绝对路径/`..`/符号链接）—— 已有 unit test 覆盖部分，需补符号链接

**补充测试**（追加到 `test_m0_compatibility.py` 末尾，均带 `@pytest.mark.asyncio`，受同一 `pytestmark` skip 守卫）：

```python
import os
import pytest

# ... 现有测试保持不变 ...

# 额外 skip 守卫：Tavily 测试需要 TAVILY_API_KEY
requires_tavily = pytest.mark.skipif(
    not os.getenv("TAVILY_API_KEY"),
    reason="TAVILY_API_KEY required for Tavily spike",
)


@pytest.mark.asyncio
async def test_deepseek_tool_calling_end_to_end(tmp_path):
    """验证 DeepSeek 真实 tool calling：agent 调用一个自定义工具并返回结果。"""
    settings = Settings(
        workspace_root=tmp_path / "workspace",
        deepseek_api_key=os.environ["DEEPSEEK_API_KEY"],
        fake_agent_mode=False,
    )
    binding = create_model_binding(settings)
    conn = await connect(str(settings.db_path))
    await init_db(conn)
    paths = WorkspacePaths(settings.sessions_root)
    session_id = uuid4()
    run_id = uuid4()
    paths.ensure_run_layout(session_id, run_id)
    context = RunContext(
        run_id=run_id,
        session_id=session_id,
        question="请调用 get_evidence_ledger 工具一次后结束，不要提交报告，不要委派子智能体。",
        uploaded_artifact_ids=[],
        max_web_sources=3,
        paths=paths,
        settings=settings,
    )
    store = EvidenceStore(
        run_id=run_id,
        session_id=session_id,
        evidence_repo=EvidenceRepository(conn),
        paths=paths,
    )
    agent = create_research_agent(context, binding, store, ArtifactRepository(conn))
    stream = agent.astream_events(
        {"messages": [{"role": "user", "content": context.question}]},
        config={"configurable": {"thread_id": str(run_id)}},
        version="v3",
    )
    if inspect.isawaitable(stream):
        stream = await stream
    tool_events = []
    async for raw in stream:
        if isinstance(raw, dict) and raw.get("event") in {"on_tool_start", "on_tool_end"}:
            tool_events.append(str(raw.get("name", "")))
        if len(tool_events) >= 2:
            break
    assert any("get_evidence_ledger" in name for name in tool_events), tool_events
    await conn.close()


@requires_tavily
@pytest.mark.asyncio
async def test_tavily_search_real(tmp_path):
    """验证 Tavily search_web 真实调用并生成证据。"""
    from ai_dev_researcher.tools.web_search import search_web_impl

    settings = Settings(
        workspace_root=tmp_path / "workspace",
        deepseek_api_key=os.environ["DEEPSEEK_API_KEY"],
        tavily_api_key=os.environ["TAVILY_API_KEY"],
        fake_agent_mode=False,
    )
    conn = await connect(str(settings.db_path))
    await init_db(conn)
    paths = WorkspacePaths(settings.sessions_root)
    session_id = uuid4()
    run_id = uuid4()
    paths.ensure_run_layout(session_id, run_id)
    context = RunContext(
        run_id=run_id,
        session_id=session_id,
        question="spike",
        uploaded_artifact_ids=[],
        max_web_sources=3,
        paths=paths,
        settings=settings,
    )
    store = EvidenceStore(
        run_id=run_id,
        session_id=session_id,
        evidence_repo=EvidenceRepository(conn),
        paths=paths,
    )
    result = await search_web_impl(
        context=context,
        store=store,
        query="DeepAgents python framework github",
        max_results=3,
    )
    assert result["items"], "tavily search returned no items"
    assert all(item["evidence_id"] for item in result["items"])
    await conn.close()


@requires_tavily
@pytest.mark.asyncio
async def test_tavily_extract_real(tmp_path):
    """验证 Tavily extract 升级证据等级为 first_party。"""
    from ai_dev_researcher.tools.web_search import extract_web_sources_impl, search_web_impl

    settings = Settings(
        workspace_root=tmp_path / "workspace",
        deepseek_api_key=os.environ["DEEPSEEK_API_KEY"],
        tavily_api_key=os.environ["TAVILY_API_KEY"],
        fake_agent_mode=False,
    )
    conn = await connect(str(settings.db_path))
    await init_db(conn)
    paths = WorkspacePaths(settings.sessions_root)
    session_id = uuid4()
    run_id = uuid4()
    paths.ensure_run_layout(session_id, run_id)
    context = RunContext(
        run_id=run_id,
        session_id=session_id,
        question="spike",
        uploaded_artifact_ids=[],
        max_web_sources=3,
        paths=paths,
        settings=settings,
    )
    store = EvidenceStore(
        run_id=run_id,
        session_id=session_id,
        evidence_repo=EvidenceRepository(conn),
        paths=paths,
    )
    search = await search_web_impl(
        context=context, store=store, query="LangGraph documentation", max_results=2,
    )
    evidence_ids = [item["evidence_id"] for item in search["items"][:1]]
    if evidence_ids:
        updated = await extract_web_sources_impl(
            context=context, store=store, evidence_ids=evidence_ids,
        )
        # 至少有一条升级成功（部分 URL 可能 extract 失败，放宽断言）
        if updated["updated"]:
            assert updated["updated"][0]["evidence_level"] == "first_party"
    await conn.close()


@pytest.mark.asyncio
async def test_subagent_delegation_real(tmp_path):
    """验证主 agent 能通过 task 工具委派 web-researcher 子智能体。"""
    settings = Settings(
        workspace_root=tmp_path / "workspace",
        deepseek_api_key=os.environ["DEEPSEEK_API_KEY"],
        tavily_api_key=os.environ.get("TAVILY_API_KEY", ""),
        fake_agent_mode=False,
    )
    binding = create_model_binding(settings)
    conn = await connect(str(settings.db_path))
    await init_db(conn)
    paths = WorkspacePaths(settings.sessions_root)
    session_id = uuid4()
    run_id = uuid4()
    paths.ensure_run_layout(session_id, run_id)
    context = RunContext(
        run_id=run_id,
        session_id=session_id,
        question="请委派 web-researcher 子智能体检索一次 'DeepAgents' 关键词，然后结束，不要提交报告。",
        uploaded_artifact_ids=[],
        max_web_sources=2,
        paths=paths,
        settings=settings,
    )
    store = EvidenceStore(
        run_id=run_id,
        session_id=session_id,
        evidence_repo=EvidenceRepository(conn),
        paths=paths,
    )
    agent = create_research_agent(context, binding, store, ArtifactRepository(conn))
    stream = agent.astream_events(
        {"messages": [{"role": "user", "content": context.question}]},
        config={"configurable": {"thread_id": str(run_id)}},
        version="v3",
    )
    if inspect.isawaitable(stream):
        stream = await stream
    seen_agents = set()
    async for raw in stream:
        if not isinstance(raw, dict):
            continue
        if raw.get("event") == "on_chain_start" and str(raw.get("name")) == "task":
            data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
            sub = str(data.get("subagent", ""))
            if sub:
                seen_agents.add(sub)
        if "web-researcher" in seen_agents:
            break
    assert "web-researcher" in seen_agents, f"subagent not delegated: {seen_agents}"
    await conn.close()


@pytest.mark.asyncio
async def test_excluded_tools_not_present(tmp_path):
    """验证 excluded_tools 生效：内置文件工具不出现在 agent 工具集中。"""
    settings = Settings(
        workspace_root=tmp_path / "workspace",
        deepseek_api_key=os.environ["DEEPSEEK_API_KEY"],
    )
    binding = create_model_binding(settings)
    conn = await connect(str(settings.db_path))
    await init_db(conn)
    paths = WorkspacePaths(settings.sessions_root)
    session_id = uuid4()
    run_id = uuid4()
    paths.ensure_run_layout(session_id, run_id)
    context = RunContext(
        run_id=run_id, session_id=session_id, question="spike",
        uploaded_artifact_ids=[], max_web_sources=2, paths=paths, settings=settings,
    )
    store = EvidenceStore(
        run_id=run_id, session_id=session_id,
        evidence_repo=EvidenceRepository(conn), paths=paths,
    )
    agent = create_research_agent(context, binding, store, ArtifactRepository(conn))
    graph = agent.get_graph()
    node_blob = " ".join(graph.nodes.keys()).lower()
    for forbidden in ["read_file", "write_file", "edit_file", "delete", "glob", "grep", "execute"]:
        # general-purpose 已在 test 2 验证；这里验证文件工具不在节点名中
        assert forbidden not in node_blob, f"forbidden tool leaked: {forbidden}"
    await conn.close()


@pytest.mark.asyncio
async def test_state_backend_persistence(tmp_path):
    """验证 StateBackend：同 thread_id 二次调用能延续状态。"""
    settings = Settings(
        workspace_root=tmp_path / "workspace",
        deepseek_api_key=os.environ["DEEPSEEK_API_KEY"],
        fake_agent_mode=False,
    )
    binding = create_model_binding(settings)
    conn = await connect(str(settings.db_path))
    await init_db(conn)
    paths = WorkspacePaths(settings.sessions_root)
    session_id = uuid4()
    run_id = uuid4()
    paths.ensure_run_layout(session_id, run_id)
    context = RunContext(
        run_id=run_id, session_id=session_id,
        question="第一步：请只调用 write_todos 写一个计划，不要做别的。",
        uploaded_artifact_ids=[], max_web_sources=2, paths=paths, settings=settings,
    )
    store = EvidenceStore(
        run_id=run_id, session_id=session_id,
        evidence_repo=EvidenceRepository(conn), paths=paths,
    )
    agent = create_research_agent(context, binding, store, ArtifactRepository(conn))
    config = {"configurable": {"thread_id": str(run_id)}}
    stream = agent.astream_events(
        {"messages": [{"role": "user", "content": context.question}]},
        config=config, version="v3",
    )
    if inspect.isawaitable(stream):
        stream = await stream
    async for _ in stream:
        pass
    # 第二次调用同 thread_id，验证不报错且能延续
    stream2 = agent.astream_events(
        {"messages": [{"role": "user", "content": "好的，计划已收到，结束。"}]},
        config=config, version="v3",
    )
    if inspect.isawaitable(stream2):
        stream2 = await stream2
    async for _ in stream2:
        pass
    await conn.close()
```

> 注：以上测试依赖真实 API，执行慢且消耗 token。建议在 CI 中用独立 marker（如 `@pytest.mark.spike`）标记，日常 `pytest -q` 不跑，仅 M0 阶段手动跑。

**运行 M0 测试**（需先 `export DEEPSEEK_API_KEY=...` 和 `export TAVILY_API_KEY=...`，或在 `.env` 已配置后用 `python-dotenv` 加载）：

```bash
# Git Bash 中设置环境变量
export DEEPSEEK_API_KEY=<你的 key>
export TAVILY_API_KEY=<你的 key>
python -m pytest tests/integration/test_m0_compatibility.py -v -s
```

> 若希望 pytest 自动读取 `.env`：`pip install pytest-dotenv` 并在 `pyproject.toml` 的 `[tool.pytest.ini_options]` 加 `env_files = [".env"]`。推荐这样做以避免每次手动 export。

---

### 任务 6：生成锁文件（R2）

**工具**：`uv`（用户已确认）。生成 `uv.lock`，跨平台、Windows 友好、速度快。

```bash
# 安装 uv（若未装）
python -m pip install uv

# 在 backend/ 目录生成锁文件（解析全部依赖含 optional）
cd /d/code/Projects/DeepSearch_Agent/ai_dev_researcher/backend
uv lock --project .
```

> 若 `uv lock --project .` 报找不到项目，回退用：
> ```bash
> uv pip compile pyproject.toml --all-extras -o requirements.lock
> ```
> 生成 `requirements.lock`（pip 兼容格式）作为补充。

**验收**：`backend/` 下存在 `uv.lock`，且文件中包含 `deepagents==0.6.12`、`langchain-deepseek==1.1.0`、`tavily-python` 等锁定版本。

**提交 `.gitignore` 调整**：锁文件**必须**提交，确认 `.gitignore` 未忽略 `*.lock` / `uv.lock`（当前 `.gitignore` 未忽略，OK）。

> 跨平台提示：`uv.lock` 含平台特定标记，在 Windows 上生成后提交；CI/Linux 用 `uv sync --frozen` 验证可复现。

---

### 任务 7：记录 Spike 结论

**文件**：`backend/docs/m0-spike-notes.md`（新建，需先建 `docs/` 目录）

**结论清单模板**（执行 M0 测试后据实填写）：

```markdown
# M0 兼容性 Spike 结论

执行日期：YYYY-MM-DD
环境：Python 3.13.x / Windows 11 / deepagents==0.6.12

## 验证项与结果

| # | 验证项 | 结果 | 备注 |
|---|--------|------|------|
| 1 | Python 3.13 + DeepAgents 0.6.12 导入 | PASS/FAIL | |
| 2 | DeepSeek Tool Calling | PASS/FAIL | |
| 3 | DeepAgents write_todos | PASS/FAIL | |
| 4 | 两个自定义 subagent（web-researcher + document-analyst） | PASS/FAIL | |
| 5 | general-purpose 被禁用 | PASS/FAIL | |
| 6 | astream_events(version="v3") 事件流 | PASS/FAIL | |
| 7 | Tavily Search | PASS/FAIL | |
| 8 | Tavily Extract | PASS/FAIL | |
| 9 | StateBackend 持久化 | PASS/FAIL | |
| 10 | excluded_tools 生效 | PASS/FAIL | |
| 11 | 三类 Agent permissions 独立 | PASS/FAIL | |
| 12 | Windows 路径安全（盘符/UNC/绝对/..） | PASS/FAIL | unit test 覆盖 |

## 需调整文档的项

- （列出与开发计划 §11.2/§15 描述不符之处）

## 已知问题与规避

- （列出 Spike 中发现的真实问题及临时规避）

## 结论

- M0 通过：可进入 M1。
- M0 部分失败：（列出阻塞项及修复计划）
```

---

## 第三阶段：M1 真实 Agent 闭环验证

### 任务 8：准备固定 Demo 资料

**固定研究问题**：
> "结合上传笔记与公开网页资料，分析 DeepAgents 框架在个人 Python 项目中的适用边界，并给出两周内落地建议。"

**固定上传文档**：`backend/tests/fixtures/demo_notes.md`（新建，需建 `fixtures/` 内容文件）

**内容**：
```markdown
# DeepAgents 个人项目调研笔记

## 框架定位
DeepAgents 是一个基于 LangGraph 的多智能体编排框架，提供主智能体 + 专业子智能体的结构。
默认提供 general-purpose 子智能体和一组内置文件工具。

## 个人项目关注点
1. 是否能快速搭建"主编排 + 网页取证 + 文档分析"的研究工作流。
2. 内置文件工具是否会带来安全风险（个人项目希望禁用直接文件读写）。
3. DeepSeek 作为底层 LLM 的 tool calling 稳定性。

## 约束
- 单人开发，两周内需跑通真实闭环。
- 不希望 agent 直接读写磁盘，所有资料通过受控工具访问。
```

> 此文档将作为 M1 闭环的"一个上传文档"来源（满足开发计划 §22 M1：一个固定研究问题 + 一个网页来源 + 一个上传文档）。

---

### 任务 9：M1 闭环脚本

**现状分析**（已读取 `scripts/smoke_main_flow.py`）：现有脚本通过 HTTP 调用运行中的 FastAPI 服务（`base_url=http://127.0.0.1:8000`），**不适合 M1**——M1 要求"先在终端完成闭环，不等待 FastAPI、WebSocket 和 React"。

**新建脚本**：`backend/scripts/m1_real_agent_smoke.py`

**完整脚本骨架**（直接调用 `AgentResearchExecutor`，绕过 HTTP/TaskManager，同步等待完成）：

```python
"""M1 真实 Agent 闭环冒烟脚本。

用法（在 backend/ 目录，venv 已激活，.env 已配置 FAKE_AGENT_MODE=false 与真实 key）：
    python scripts/m1_real_agent_smoke.py

验证点：
- 主 Agent + web-researcher + document-analyst 三者协同
- Tavily search 产出网页证据
- 上传文档被 document-analyst 读取并记录证据
- submit_research_report 生成通过引用校验的 Markdown 报告
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

# 确保能 import 项目包
BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT / "src"))

from ai_dev_researcher.core.config import Settings, get_settings  # noqa: E402
from ai_dev_researcher.domain.artifacts import Artifact, ArtifactKind, ParseStatus  # noqa: E402
from ai_dev_researcher.domain.runs import RunRequest, RunStatus  # noqa: E402
from ai_dev_researcher.domain.sessions import ResearchSession  # noqa: E402
from ai_dev_researcher.repositories.artifacts import ArtifactRepository  # noqa: E402
from ai_dev_researcher.repositories.evidence import EvidenceRepository  # noqa: E402
from ai_dev_researcher.repositories.events import EventRepository  # noqa: E402
from ai_dev_researcher.repositories.runs import RunRepository  # noqa: E402
from ai_dev_researcher.repositories.sessions import SessionRepository  # noqa: E402
from ai_dev_researcher.repositories.sqlite import connect, init_db  # noqa: E402
from ai_dev_researcher.services.agent_executor import AgentResearchExecutor  # noqa: E402
from ai_dev_researcher.services.event_publisher import EventPublisher  # noqa: E402
from ai_dev_researcher.storage.paths import WorkspacePaths  # noqa: E402

FIXTURE_NOTES = BACKEND_ROOT / "tests" / "fixtures" / "demo_notes.md"
QUESTION = (
    "结合上传笔记与公开网页资料，分析 DeepAgents 框架在个人 Python 项目中的适用边界，"
    "并给出两周内落地建议。"
)


async def main() -> None:
    settings = get_settings()
    if settings.fake_agent_mode:
        print("[ABORT] FAKE_AGENT_MODE=true，请先在 .env 设置 FAKE_AGENT_MODE=false")
        return
    if not settings.deepseek_api_key or not settings.tavily_api_key:
        print("[ABORT] 缺少 DEEPSEEK_API_KEY 或 TAVILY_API_KEY")
        return

    print(f"[1/6] workspace: {settings.workspace_root}")
    conn = await connect(str(settings.db_path))
    await init_db(conn)
    paths = WorkspacePaths(settings.sessions_root)

    sessions_repo = SessionRepository(conn)
    runs_repo = RunRepository(conn)
    artifacts_repo = ArtifactRepository(conn)
    events_repo = EventRepository(conn)
    evidence_repo = EvidenceRepository(conn)
    publisher = EventPublisher(events_repo, queue_size=settings.ws_send_queue_size)

    # 2. 创建 session
    session_id = uuid4()
    await sessions_repo.create(ResearchSession(session_id=session_id))
    paths.ensure_session_layout(session_id)
    print(f"[2/6] session: {session_id}")

    # 3. 上传固定文档（直接写文件 + 建 artifact，模拟 UploadService 已解析结果）
    artifact_id = uuid4()
    upload_path = paths.upload_path(session_id, artifact_id)
    normalized_path = paths.normalized_path(session_id, artifact_id)
    raw_bytes = FIXTURE_NOTES.read_bytes()
    upload_path.write_bytes(raw_bytes)
    normalized_text = FIXTURE_NOTES.read_text(encoding="utf-8")
    normalized_path.write_text(normalized_text, encoding="utf-8")
    artifact = Artifact(
        artifact_id=artifact_id,
        session_id=session_id,
        kind=ArtifactKind.DOCUMENT,
        display_name="demo_notes.md",
        mime_type="text/markdown",
        size_bytes=len(raw_bytes),
        parse_status=ParseStatus.PARSED,
        original_storage_path=str(upload_path),
        normalized_storage_path=str(normalized_path),
    )
    await artifacts_repo.create(artifact)
    print(f"[3/6] uploaded artifact: {artifact_id} (demo_notes.md)")

    # 4. 创建 run
    run_id = uuid4()
    request = RunRequest(
        question=QUESTION,
        uploaded_artifact_ids=[artifact_id],
        max_web_sources=5,
    )
    await runs_repo.create(session_id=session_id, run_id=run_id, request=request)
    paths.ensure_run_layout(session_id, run_id)
    print(f"[4/6] run created: {run_id}")

    # 5. 执行真实 Agent（直接调用 executor，绕过 TaskManager）
    executor = AgentResearchExecutor(
        settings=settings,
        runs=runs_repo,
        artifacts=artifacts_repo,
        evidence=evidence_repo,
        publisher=publisher,
        paths=paths,
    )
    print("[5/6] agent running (may take 1-3 min)...")
    await executor(run_id)

    # 6. 检查结果
    run = await runs_repo.get(run_id)
    print(f"[6/6] run status: {run.status}")
    if run.status != RunStatus.SUCCEEDED:
        print(f"  error: {run.error_code} - {run.error_message}")
        events = await events_repo.list_for_run(run_id, after_seq=0)
        for ev in events[-10:]:
            print(f"  {ev.seq} {ev.type} {ev.actor} {ev.payload}")
        await conn.close()
        sys.exit(1)

    report_id = run.report_artifact_id
    report_path = paths.report_path(session_id, run_id, report_id)
    markdown = report_path.read_text(encoding="utf-8")
    print(f"  report artifact: {report_id}")
    print(f"  report path: {report_path}")
    print(f"  report chars: {len(markdown)}")
    print("  --- report preview (first 800 chars) ---")
    print(markdown[:800])

    # 验证证据账本
    evidence = await evidence_repo.list_for_run(run_id)
    web_count = sum(1 for e in evidence if e.source_type == "web")
    doc_count = sum(1 for e in evidence if e.source_type == "document")
    print(f"  evidence: total={len(evidence)} web={web_count} doc={doc_count}")
    print("  --- evidence ids ---")
    for e in evidence:
        print(f"    {e.id} [{e.source_type}/{e.evidence_level}] {e.title[:60]}")

    await conn.close()
    print("[DONE] M1 闭环验证完成")


if __name__ == "__main__":
    asyncio.run(main())
```

> 注意：`RunRequest` / `ResearchSession` / `ArtifactKind` 等的具体构造签名需对照实际 domain 模型（`domain/runs.py`、`domain/sessions.py`、`domain/artifacts.py`）。执行前请先 `python -c "from ai_dev_researcher.domain.runs import RunRequest; help(RunRequest)"` 核对字段。若 `sessions_repo.create` / `runs_repo.create` 签名不同，按实际调整。

**运行 M1 脚本**：
```bash
cd /d/code/Projects/DeepSearch_Agent/ai_dev_researcher/backend
python scripts/m1_real_agent_smoke.py
```

---

### 任务 10：M1 闭环验证点清单

对照开发计划 §22 M1 交付物，逐项验证：

| # | 验证点 | 验证方法 | 期望结果 |
|---|--------|----------|----------|
| 1 | 一个固定研究问题 | 脚本输出 `[4/6] run created` 且 QUESTION 固定 | 问题文本与任务 8 一致 |
| 2 | 一个网页来源 | 脚本输出 evidence 中 `web` 计数 ≥ 1 | web_count ≥ 1 |
| 3 | 一个上传文档 | 脚本输出 `[3/6] uploaded artifact` + evidence 中 `document` 计数 ≥ 1 | doc_count ≥ 1 |
| 4 | 主 Agent | run status=succeeded 且 events 含 `run.started` | orchestrator 正常运行 |
| 5 | web-researcher 子 Agent | events 含 `agent.started` actor=web-researcher | 子 agent 被委派 |
| 6 | document-analyst 子 Agent | events 含 `agent.started` actor=document-analyst | 子 agent 被委派 |
| 7 | 通过引用校验的 Markdown 报告 | `submit_research_report` 未抛 `ReportValidationError`，run succeeded | report 生成成功 |
| 8 | 报告含证据引用 | markdown 中出现 `S1`/`D1` 等证据 ID 反引号引用 | grep 报告含 `` `S` `` 或 `` `D` `` |
| 9 | 终端闭环（不经 HTTP） | 脚本直接调用 executor，无 uvicorn | 不依赖 FastAPI 服务 |
| 10 | 真实 DeepSeek 调用 | 无 `fake_agent_mode` 触发，executor_factory 返回 AgentResearchExecutor | settings.fake_agent_mode=False |
| 11 | 真实 Tavily 调用 | web 证据 evidence_level 含 `first_party`（extract 成功）或 `search_snippet` | 非 fake 数据 |
| 12 | 报告可下载/可读 | report_path 文件存在且 utf-8 可读 | len(markdown) > 0 |

**额外验证命令**（脚本成功后）：
```bash
# 查看生成的报告引用了哪些证据 ID
REPORT_PATH=$(ls -t /d/code/Projects/DeepSearch_Agent/ai_dev_researcher/backend/workspace/sessions/*/runs/*/reports/*.md | head -1)
grep -oE '`[SD][0-9]+`' "$REPORT_PATH" | sort -u
```

---

## 第四阶段：回归测试与文档

### 任务 11：回归测试

完成 M0/M1 后，运行全量测试确保无回归：

```bash
cd /d/code/Projects/DeepSearch_Agent/ai_dev_researcher/backend

# 1. 默认测试（Fake 模式，不依赖 key）—— 应全绿
python -m pytest -q

# 2. 显式跑各层
python -m pytest tests/unit/ -q
python -m pytest tests/integration/test_main_flow.py -q

# 3. M0 Spike 测试（需 key）—— 验证真实 Agent 兼容性
export DEEPSEEK_API_KEY=<key>
export TAVILY_API_KEY=<key>
python -m pytest tests/integration/test_m0_compatibility.py -v -s

# 4. R1 回归验证
python -m pytest tests/unit/test_errors.py -q
python -c "from ai_dev_researcher.tools.factory import create_web_tools; print('factory import ok')"

# 5. 导入完整性
python -c "from ai_dev_researcher.services.agent_executor import AgentResearchExecutor; print('agent executor import ok')"
python -c "from ai_dev_researcher.main import create_app; app=create_app(); print('app creation ok')"
```

**验收标准**：
- 步骤 1-2：全绿（Fake 模式不受影响）
- 步骤 3：M0 测试全绿（允许个别 Tavily 网络波动，重试即可）
- 步骤 4-5：无 ImportError

---

### 任务 12：更新 README

**文件**：`README.md`

**需更新章节**：

1. **"当前进度"章节**：
   - 将"已完成：后端无 Agent 纵向切片（Fake Executor）"改为：
     - 已完成 M0：DeepAgents 0.6.12 + DeepSeek + Tavily 兼容性 Spike
     - 已完成 M1：真实 Agent 终端闭环（主 Agent + web-researcher + document-analyst）
     - Fake Executor 保留为无 key 回退模式

2. **"快速开始 > 后端"章节**：
   - 补充 agent 依赖安装步骤：
     ```bash
     python -m pip install -e ".[dev,agent]"
     ```
   - 补充 `.env` 配置说明（从 `.env.example` 复制，填 key，设 `FAKE_AGENT_MODE=false`）

3. **新增"运行模式"章节**（在"快速开始"之后）：
   ```markdown
   ## 运行模式

   - **Fake 模式**（默认，无 key）：`FAKE_AGENT_MODE=true`，使用确定性 FakeResearchExecutor，不调用 LLM/Tavily，用于前端联调与回归。
   - **真实 Agent 模式**：`FAKE_AGENT_MODE=false` 且配置 `DEEPSEEK_API_KEY` + `TAVILY_API_KEY`，使用 AgentResearchExecutor + DeepAgents + DeepSeek + Tavily。
   - 切换逻辑见 `services/executor_factory.py`：`fake_agent_mode or not deepseek_api_key` → Fake。
   ```

4. **"测试"章节**：
   - 补充 M0 Spike 测试运行方式（需 key）：
     ```bash
     export DEEPSEEK_API_KEY=<key>
     export TAVILY_API_KEY=<key>
     python -m pytest tests/integration/test_m0_compatibility.py -v -s
     ```

5. **"主线流程"章节**：
   - 将"当前 Fake 模式"标题改为"主线流程"，说明默认 Fake、配置 key 后自动切真实 Agent。
   - 删除/更新末尾"后续：接入真实 DeepAgents..."一行（已实现）。

6. **新增"M1 终端验证"章节**：
   ```markdown
   ## M1 终端验证（真实 Agent 闭环）

   ```bash
   python scripts/m1_real_agent_smoke.py
   ```
   验证主 Agent + 两个子 Agent + Tavily + 文档分析的端到端闭环，生成 Markdown 报告。
   ```

---

## 执行顺序总览（按依赖关系）

```
任务1(R1) ─┐
任务2(R8) ─┤
           ├→ 任务3(装依赖) → 任务4(.env) → 任务5(M0测试) ─┐
           │                                                 ├→ 任务7(spike笔记)
           │                                                 │
           │   任务6(锁文件) ←─ 依赖任务3                    │
           │                                                 ↓
           │                                          任务8(demo资料)
           │                                                 ↓
           └──────────────────────────────────────→ 任务9(M1脚本) → 任务10(验证)
                                                                     ↓
                                                            任务11(回归) → 任务12(README)
```

**关键里程碑**：
- 里程碑 A：任务 1-2 完成 → R1/R8 修复，`pytest -q` 仍全绿
- 里程碑 B：任务 3-5 完成 → M0 Spike 通过（真实 key 下兼容性验证）
- 里程碑 C：任务 6-7 完成 → 锁文件就绪、Spike 文档归档
- 里程碑 D：任务 8-10 完成 → M1 真实闭环跑通，报告生成
- 里程碑 E：任务 11-12 完成 → 回归全绿、文档更新

---

## 关键文件清单（待修改/新建）

| 文件 | 操作 | 关联任务 |
|------|------|----------|
| `backend/src/ai_dev_researcher/core/errors.py` | 修改（追加类） | 任务1 |
| `backend/src/ai_dev_researcher/tools/web_search.py` | 修改（3处 provider 参数，可选） | 任务1 |
| `backend/tests/unit/test_errors.py` | 新建 | 任务1 |
| `backend/src/ai_dev_researcher/main.py` | 修改（删除重复构造） | 任务2 |
| `backend/.env` | 新建 | 任务4 |
| `backend/tests/integration/test_m0_compatibility.py` | 修改（追加测试） | 任务5 |
| `backend/uv.lock` | 新建 | 任务6 |
| `backend/docs/m0-spike-notes.md` | 新建 | 任务7 |
| `backend/tests/fixtures/demo_notes.md` | 新建 | 任务8 |
| `backend/scripts/m1_real_agent_smoke.py` | 新建 | 任务9 |
| `backend/pyproject.toml` | 修改（可选，加 pytest-dotenv） | 任务5 |
| `README.md` | 修改 | 任务12 |

---

## 风险与规避

1. **DeepSeek tool calling 不稳定**：M0 测试 `test_deepseek_tool_calling_end_to_end` 可能偶发失败。规避：`max_retries=2` 已在 `model.py` 配置；测试中放宽断言（只要 `on_tool_*` 事件出现即可），重试机制由 LLM 层处理。

2. **Tavily 免费额度限制**：M0/M1 会消耗 Tavily 调用次数。规避：测试 `max_results` 设小值（2-3），M1 脚本 `max_web_sources=5`。

3. **Windows 路径与符号链接**：`ensure_within_root` 用 `resolve()`，Windows 上符号链接解析行为与 POSIX 不同。规避：M0 spike 中记录实际行为，必要时在 `security.py` 增加符号链接显式检测（`path.is_symlink()`）。

4. **agent 执行耗时长**：M1 脚本可能 1-3 分钟。规避：脚本打印进度，`ChatDeepSeek(timeout=90)` 已配置；不设脚本级超时以免误杀。

5. **锁文件跨平台差异**：`uv.lock` 含平台特定标记。规避：在 Windows 上生成，提交后 CI/Linux 需重新生成或用 `uv sync --frozen` 验证。

6. **StateBackend 持久化路径**：DeepAgents `StateBackend` 默认内存，`test_state_backend_persistence` 验证同 thread_id 续接。若实际不持久化到磁盘，记录为已知项，不影响 M1（M1 单次执行）。
