# Grok Build → ai_dev_researcher 融合开发计划

> 基于 Grok Build (SpXAI 终端 AI 编程助手) 的架构分析，针对 ai_dev_researcher 当前架构弱点，
> 提炼的融合改进方案。按优先级排列，每项含具体实现步骤和验收标准。

---

## P0-1：LLM Provider 抽象层

**当前问题**：`agents/model.py` 写死 `ChatDeepSeek`，无法切换模型。

**Grok Build 做法**：`xai-grok-sampler` 定义 `ApiBackend` 枚举（`ChatCompletions | Responses | Messages`），`ConversationRequest` 统一转换为各后端格式。同一个 agent 可以挂不同 LLM。

### 实现步骤

**1. 新建 `agents/providers/` 包：**

```
agents/providers/
├── __init__.py
├── base.py          # ModelProvider Protocol
├── deepseek.py      # DeepSeek provider（迁移现有代码）
├── anthropic.py     # Claude provider
└── openai_compat.py # 通用 OpenAI 兼容 provider（Qwen 等）
```

**2. refactor `agents/model.py`：**

- `ModelBinding` 保持 dataclass，`instance` 改为 `ModelProvider`
- `create_model_binding()` 根据 `Settings.model_provider` 字段工厂化选择
- 新增 `Settings` 字段：

```python
model_provider: str = "deepseek"     # deepseek | anthropic | openai_compat
llm_api_key: str = ""                # 通用 API key
llm_base_url: str = ""               # OpenAI 兼容的自定义 endpoint
llm_model: str = "deepseek-chat"
llm_temperature: float = 0.0
llm_max_retries: int = 2
llm_timeout: int = 90
```

- 废弃旧的 `deepseek_api_key` / `deepseek_model`（保持向后兼容，自动映射到新字段）

**3. `ModelProvider` 接口：**

所有 provider 返回 langchain `BaseChatModel` 子类实例，确保与 `create_deep_agent(model=...)` 兼容：
- DeepSeek → `ChatDeepSeek`
- Anthropic → `ChatAnthropic`
- OpenAI Compat → `ChatOpenAI`

**4. 更新 `.env.example`：**

```env
# 废弃（保持兼容）
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_MODEL=deepseek-chat

# 新增
MODEL_PROVIDER=deepseek       # deepseek | anthropic | openai_compat
LLM_API_KEY=sk-xxx
LLM_BASE_URL=                 # OpenAI 兼容自定义 endpoint
LLM_MODEL=deepseek-chat
LLM_TEMPERATURE=0.0
LLM_MAX_RETRIES=2
LLM_TIMEOUT=90
```

**5. 向后兼容逻辑：**

`create_model_binding()` 中：如果 `LLM_API_KEY` 为空但 `DEEPSEEK_API_KEY` 存在，自动将 `model_provider` 设为 `"deepseek"` 并使用旧字段值。

### 不改动的范围

- `agents/orchestrator.py`（只接收 `model_binding.instance`）
- `services/agent_executor.py`
- 前端

### 验收标准

- `MODEL_PROVIDER=deepseek` 时行为与重构前完全一致
- `MODEL_PROVIDER=anthropic` 时能正确创建 `ChatAnthropic` 并传给 agent
- `MODEL_PROVIDER=openai_compat` + `LLM_BASE_URL` 指向 Qwen API 时正常工作
- 旧的 `DEEPSEEK_API_KEY` 环境变量仍然生效
- Fake 模式不受影响
- `MODEL_PROVIDER` 为无效值时抛出 `ConfigurationError`

---

## P0-2：密钥/敏感信息脱敏

**当前问题**：Agent 输出通过 `event_publisher.publish()` 直接广播到 WebSocket 前端，无脱敏处理。用户上传的文档或网页搜索结果中的 API key / token 会直接展示在 UI 中。

**Grok Build 做法**：`xai-grok-secrets/src/sanitizer.rs` 覆盖 10 种平台令牌的正则脱敏，`\b` 锚定避免误匹配，在输出进入日志/遥测/UI 前自动遮蔽为 `***REDACTED***`。

### 实现步骤

**1. 新建 `core/sanitizer.py`：**

预编译以下正则模式（`\b` 锚定防止误匹配）：

| 令牌类型 | 正则模式 | 替换为 |
|----------|---------|--------|
| GitHub PAT | `ghp_[A-Za-z0-9_]{36}` | `***GITHUB_TOKEN***` |
| GitHub OAuth | `gho_[A-Za-z0-9_]{36}` | `***GITHUB_TOKEN***` |
| AWS Access Key | `AKIA[0-9A-Z]{16}` | `***AWS_KEY***` |
| AWS Secret Key | `[A-Za-z0-9/+]{40}`（前后非 base64 字符） | `***AWS_SECRET***` |
| OpenAI API Key | `sk-[A-Za-z0-9]{32,}` | `***OPENAI_KEY***` |
| Slack Bot Token | `xox[baprs]-[0-9A-Za-z-]+` | `***SLACK_TOKEN***` |
| JWT | `eyJ...eyJ...` 三段式 | `***JWT***` |
| PEM 私钥 | `-----BEGIN ... PRIVATE KEY----- ... -----END ... PRIVATE KEY-----` | `***PEM_KEY***` |
| Bearer Token | `Bearer [A-Za-z0-9\-._~+/]+=*` | `Bearer ***TOKEN***` |

核心函数：

```python
def sanitize_text(text: str) -> str:
    """对所有已知令牌格式做正则替换。"""

def sanitize_dict(data: dict) -> dict:
    """递归处理 dict，对所有字符串值做脱敏。"""

def sanitize_event_payload(payload: dict) -> dict:
    """对事件 payload 脱敏，跳过 event_type / tool_name 等元信息字段。"""
```

**2. 集成到 `event_publisher.py`：**

在 `publish()` 方法开头插入一行：

```python
payload = sanitize_event_payload(payload)
```

**3. （可选）集成到存储层：**

在 `EvidenceStore.add()` 中对 `excerpt` 字段脱敏。

### 验收标准

- `sanitize_text("my key is sk-proj-abc123def456ghi789")` → `"my key is ***OPENAI_KEY***"`
- 函数名中含 `sk-` 的字符串不被误匹配（`\b` 边界）
- JWT 被脱敏但普通 base64 内容不被误匹配
- 深层嵌套 dict 的字符串值也被脱敏
- `event_type` / `tool_name` 等元字段不被脱敏
- sanitize 对 <1KB payload 耗时 <1ms
- 单元测试覆盖所有 9 种令牌格式

---

## P1-1：事件流增强 — 暴露 LLM 思考过程

**当前问题**：`stream_adapter.py` 只映射了 `on_tool_start/end/error` 和 `on_chain_start/end(name="task")` 共 5 种事件。缺失了 LLM 逐 token 输出和中间推理事件，前端只能看到工具调用而看不到模型的思考过程。

**Grok Build 做法**：TUI 中 LLM 思考过程实时流式展示 — 不仅有 tool call，还有 reasoning 中间文本。

### 实现步骤

**1. 增强 `stream_adapter.py` 的 `map_framework_event()`：**

新增处理以下 LangGraph v3 事件类型：

| LangGraph 事件 | 映射为 | payload |
|---------------|--------|---------|
| `on_chat_model_stream` | `llm.token` | `content`, `agent` |
| `on_chat_model_start` | `llm.started` | `agent`, `messages_count` |
| `on_chat_model_end` | `llm.completed` | `agent`, `token_count` |
| `on_chain_stream`（非 task） | `agent.thinking` | `agent`, `content` |

**2. Token 事件节流：**

`llm.token` 频率极高（每 token 一个事件），在 `agent_executor.py` 中合并推送：

```python
# 方案：buffer 满 50 token 或距上次推送超 100ms 时合并发送
token_buffer: list[str] = []
last_flush = time.monotonic()

async for raw in stream:
    event_type, actor, payload = map_framework_event(raw)
    if event_type == "llm.token":
        token_buffer.append(payload["content"])
        if len(token_buffer) >= 50 or (time.monotonic() - last_flush) > 0.1:
            await publisher.publish(..., event_type="llm.tokens",
                payload={"content": "".join(token_buffer)})
            token_buffer.clear(); last_flush = time.monotonic()
    else:
        await publisher.publish(...)  # 其他事件直接推送
```

**3. 前端展示（可选）：**

在 `AgentTimeline` 组件中新增可折叠的"思考过程"区域，显示 `llm.tokens` 聚合文本。

### 验收标准

- `llm.token` 事件能正确捕获
- 节流后前端推送频率 < 5 次/秒
- `on_chat_model_end` 的 `token_count` 正确
- 不破坏现有事件映射逻辑
- Fake 模式不受影响

---

## P1-2：Tool 系统标准化

**当前问题**：工具通过 `StructuredTool.from_function(coroutine=...)` 临时创建，无统一接口、输入 schema、错误处理模式。7 个工具还能手工管理，再扩展会失控。

**Grok Build 做法**：`xai-tool-runtime` 的 `Tool` trait — 标准 JSON Schema 输入/输出、流式执行、生命周期钩子、注册表统一管理。

### 实现步骤

**1. 新建 `tools/base.py`：**

```python
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

@dataclass
class ToolResult:
    """统一的工具返回格式。"""
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    evidence_ids: list[str] = field(default_factory=list)

@runtime_checkable
class ResearchTool(Protocol):
    """研究工具的标准接口。"""
    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema

    async def execute(self, **kwargs: Any) -> ToolResult: ...
```

**2. 将现有工具改造为实现 `ResearchTool` 协议：**

每个工具从工厂函数改为类，示例：

```python
class WebSearchTool:
    name = "search_web"
    description = "Search the public web..."
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "default": 5, "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
    }

    def __init__(self, context: RunContext, store: EvidenceStore):
        self._context = context; self._store = store

    async def execute(self, query: str, max_results: int = 5) -> ToolResult:
        try:
            result = await search_web_impl(...)
            evidence_ids = [item["evidence_id"] for item in result.get("items", [])]
            return ToolResult(success=True, data=result, evidence_ids=evidence_ids)
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))
```

**3. 新建 `tools/registry.py`：**

```python
class ToolRegistry:
    def register(self, tool: ResearchTool) -> None: ...
    def get(self, name: str) -> ResearchTool | None: ...
    def to_langchain_tools(self) -> list: ...  # 转换为 DeepAgents 可用的格式
```

**4. 更新 `tools/factory.py`：**

保留工厂函数接口，内部使用新的 Tool 类和 ToolRegistry。

### 验收标准

- 所有 7 个现有工具实现 `ResearchTool` 协议
- `ToolRegistry.to_langchain_tools()` 返回的工具能被 DeepAgents 正常使用
- `ToolResult.evidence_ids` 被 `agent_executor.py` 正确读取
- Fake 模式不受影响
- 现有测试通过

---

## P1-3：SSRF 防护增强

**当前问题**：`tools/web_search.py` 的 `_is_private_host()` 只有 4 条硬编码规则，无 DNS rebinding 防护、无 IPv6 覆盖、无云元数据端点阻断。当前靠 Tavily 代理规避了风险，但未来直接 fetch URL 时就会暴露。

**Grok Build 做法**：`web_fetch/ssrf.rs` — 六层防护：私有网段（含 CGNAT）、云元数据端点（169.254.169.254）、DNS 解析后二次 IP 校验、IPv6 link-local/unique local、`no_proxy` 联动、可配置黑名单。

### 实现步骤

**1. 新建 `core/ssrf.py`：**

```python
import ipaddress, socket
from urllib.parse import urlparse

PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),    # 云元数据端点
    ipaddress.ip_network("100.64.0.0/10"),     # CGNAT
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("224.0.0.0/4"),       # 多播
    ipaddress.ip_network("240.0.0.0/4"),       # 保留
]

def is_private_ip(ip_str: str) -> bool:
    """解析 IP 字符串，检查是否属于私有/保留网段。无法解析则拒绝。"""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return any(ip in net for net in PRIVATE_NETWORKS)

def is_private_host(host: str) -> bool:
    """检查主机名：先匹配特殊 hostname，再解析为 IP 校验。"""
    lowered = host.lower().strip()
    if lowered in {"localhost", "0.0.0.0", "[::1]", "metadata.google.internal"}:
        return True
    try:
        return is_private_ip(str(ipaddress.ip_address(lowered)))
    except ValueError:
        return False

def validate_url(url: str) -> str:
    """校验 URL 安全。DNS 解析后二次校验（防 DNS rebinding）。"""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"unsupported scheme: {parsed.scheme}")
    host = parsed.hostname
    if not host:
        raise ValueError("missing hostname")
    if is_private_host(host):
        raise ValueError(f"private host rejected: {host}")

    # DNS 解析后二次校验
    try:
        for _, _, _, _, sockaddr in socket.getaddrinfo(host, None):
            if is_private_ip(sockaddr[0]):
                raise ValueError(f"DNS resolved to private IP: {host} -> {sockaddr[0]}")
    except socket.gaierror as exc:
        raise ValueError(f"DNS resolution failed: {host}") from exc

    return parsed.geturl()
```

**2. 集成到 `tools/web_search.py`：**

替换现有 `_is_private_host()`，导入 `from ai_dev_researcher.core.ssrf import validate_url`。

**3. 增加单元测试：**

覆盖所有私有网段、云元数据端点、DNS rebinding、IPv6 地址共 10+ 场景。

### 验收标准

- `validate_url("http://169.254.169.254/latest/meta-data")` → ValueError
- `validate_url("http://10.0.0.1/admin")` → ValueError
- `validate_url("http://172.20.0.1/api")` → ValueError
- `validate_url("http://192.168.1.1/")` → ValueError
- `validate_url("https://example.com")` → 返回 canonical URL
- DNS rebinding 场景被正确拦截

---

## P2-1：检查点/恢复系统

**当前问题**：Run 失败后所有中间状态丢失，必须从头重跑（重新搜索 + 重新提取 + 重新分析）。`EvidenceStore` 已经持久化了证据，但 Agent 的内部决策状态（已搜索了哪些 query、已到达哪个分析阶段）没有保存。

**Grok Build 做法**：Agent 执行中定期保存检查点，失败后从最后检查点恢复，不从头开始。

### 实现步骤

**1. 新建 `repositories/checkpoints.py` + SQLite 表：**

```sql
CREATE TABLE IF NOT EXISTS checkpoints (
    run_id TEXT PRIMARY KEY,
    completed_steps TEXT NOT NULL,  -- JSON 数组
    last_evidence_snapshot TEXT,    -- JSON: 证据账本快照
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
```

每条 `completed_step` 记录：
```json
{"tool": "search_web", "args": {"query": "Rust async drop"}, "evidence_ids": ["S1", "S2"]}
```

**2. 在 `agent_executor.py` 中记录检查点：**

每个 `tool.completed` 事件后：

```python
await checkpoint_repo.upsert(run_id=run_id, completed_step={
    "tool": tool_name,
    "args": payload.get("input_summary"),
    "evidence_ids": payload.get("discovered", {}).get("evidence_id", []),
})
```

**3. 在 `agent_executor.py` 中实现恢复逻辑：**

Run 开始时检查：

```python
checkpoint = await checkpoint_repo.get(run_id)
if checkpoint and checkpoint.completed_steps:
    context.completed_steps = checkpoint.completed_steps
```

**4. 更新系统提示词 `prompts.py`：**

```python
def build_orchestrator_prompt(context: RunContext) -> str:
    prompt = "..."
    if context.completed_steps:
        steps_text = "\n".join(
            f"- {s['tool']}: {s['args']} → {len(s.get('evidence_ids', []))} 条证据"
            for s in context.completed_steps
        )
        prompt += (
            f"\n\n## 已完成的步骤（恢复模式）\n{steps_text}\n"
            "请基于已有证据继续分析，不要重复已完成的操作。"
        )
    return prompt
```

### 验收标准

- 每个 tool 完成后检查点被持久化
- 模拟崩溃恢复：在 extract_web_sources 后中断，重新启动后跳过已完成的搜索
- 恢复后不重复搜索同一 query
- 首次执行不受影响
- Fake 模式正常记录检查点

---

## P2-2：MCP 协议支持

**当前问题**：所有工具硬编码在 `tools/` 目录，每接入一个新数据源（ArXiv、GitHub API、内部知识库）就要写一个新 tool。

**Grok Build 做法**：`xai-grok-mcp` — 标准 MCP 客户端，动态连接外部 tool server 获取工具能力。

### 实现步骤

**1. 安装依赖：**

```toml
# pyproject.toml
"mcp>=1.0.0",
```

**2. 新建 `tools/mcp_client.py`：**

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

class MCPToolBridge:
    """将 MCP tool 桥接为 ResearchTool。"""
    def __init__(self, session: ClientSession, tool_schema: dict):
        self._session = session
        self.name = tool_schema["name"]
        self.description = tool_schema.get("description", "")
        self.input_schema = tool_schema.get("inputSchema", {})

    async def execute(self, **kwargs) -> ToolResult:
        try:
            result = await self._session.call_tool(self.name, arguments=kwargs)
            return ToolResult(
                success=True,
                data={"content": [c.text for c in result.content if c.type == "text"]},
            )
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))

class MCPToolLoader:
    """管理 MCP 连接生命周期。"""
    def __init__(self):
        self._connections: dict[str, ClientSession] = {}

    async def connect(self, name: str, command: str, args: list[str]) -> None:
        params = StdioServerParameters(command=command, args=args)
        transport = await stdio_client(params)
        session = await ClientSession(transport[0], transport[1])
        await session.initialize()
        self._connections[name] = session

    async def list_tools(self, name: str) -> list[MCPToolBridge]:
        session = self._connections[name]
        tools = await session.list_tools()
        return [MCPToolBridge(session, tool) for tool in tools.tools]

    async def close_all(self) -> None:
        for session in self._connections.values():
            await session.__aexit__(None, None, None)
```

**3. 集成到 `tools/factory.py`：**

```python
async def create_mcp_tools(settings: Settings) -> list[StructuredTool]:
    loader = MCPToolLoader()
    tools = []
    for server in settings.mcp_servers:
        await loader.connect(server["name"], server["command"], server["args"])
        for bridge in await loader.list_tools(server["name"]):
            tools.append(StructuredTool.from_function(
                coroutine=bridge.execute,
                name=bridge.name,
                description=bridge.description,
            ))
    return tools
```

**4. 新增配置项：**

```python
# Settings
mcp_servers: list[dict] = Field(default_factory=list)
# 格式：[{"name": "arxiv", "command": "npx", "args": ["-y", "@anthropic/mcp-server-arxiv"]}]
```

### 验收标准

- 能连接一个示例 MCP server 并列出工具
- MCP 工具能被注册到 ToolRegistry 并传给 agent
- MCP 工具调用失败时返回 `ToolResult(success=False)` 而非崩溃
- 应用关闭时正确清理所有 MCP 连接
- 未配置 MCP servers 时不影响现有流程

---

## 附录：快速修复项

以下两项阻塞 M0 真实 Agent 闭环推进，与 Grok 参考无关：

### 修复 R1：`SearchProviderError` 未定义

`tools/web_search.py:8` 导入了 `SearchProviderError`，但 `core/errors.py` 未定义。Fake 模式不触发，真实 Agent 模式会 ImportError。

**修复**：在 `core/errors.py` 中新增：

```python
class SearchProviderError(AppError):
    """Search provider (e.g. Tavily) returned an error."""
    code: str = "SEARCH_PROVIDER_ERROR"
```

### 修复 R8：`main.py` 重复构造 `run_service`

`main.py:79-97` 行，`AppState` 构造时传入一次 `run_service`，之后又覆盖一次。

**修复**：删除第 79-97 行的重复构造代码。

---

## 执行顺序

```
P0-1 (LLM Provider 抽象) ── 2-3h ──┐
                                     ├── 并行执行
P0-2 (密钥脱敏) ────────── 30min ───┘
         ↓
P1-1 (事件流增强) ──────── 1-2h
P1-2 (Tool 标准化) ─────── 3-4h
P1-3 (SSRF 增强) ───────── 1-2h
         ↓
P2-1 (检查点/恢复) ─────── 4-6h
P2-2 (MCP 支持) ────────── 1-2d
```

> P0 解决扩展性瓶颈和安全漏洞，应优先完成。P1 提升可观测性和代码质量。P2 是长期架构投资。
