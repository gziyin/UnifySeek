# M0 兼容性 Spike 结论

执行日期：2026-08-02
环境：Python 3.13.14 / Windows 11 / deepagents==0.6.12 / langchain 1.x / langgraph 1.x

## 验证项与结果

| # | 验证项 | 结果 | 备注 |
|---|--------|------|------|
| 1 | Python 3.13 + DeepAgents 0.6.12 导入 | PASS | |
| 2 | HarnessProfile + register_harness_profile 注册 | PASS | spec 前缀 `deepseek:` |
| 3 | create_research_agent 含 astream_events(version="v3") | PASS | |
| 4 | subagents 仅 web-researcher + document-analyst，无 general-purpose | PASS | excluded_tools 生效 |
| 5 | astream_events v3 流产出事件 | PASS | 事件结构非 {event:} dict（实验性协议） |
| 6 | Tavily search 真实调用 | PASS | 产出 search_snippet 证据 |
| 7 | Tavily extract 真实调用 | PASS | 升级证据等级为 first_party |
| 8 | excluded_tools 生效（无 read_file/write_file 等） | PASS | |
| 9 | StateBackend 同 thread_id 二次调用延续 | PASS | |
| 10 | DeepSeek tool calling 链路通 | PASS | 模型不一定按指令调指定工具 |
| 11 | subagent 委派链路通 | PASS | DeepSeek 不一定按指令委派指定子智能体 |

## 关键发现

1. **langgraph v3 streaming 是实验性协议**：事件结构可能不是 `{event: "on_...", ...}` dict，测试不能假设固定结构。`astream_events(version="v3")` 能产出事件流，但事件类型/字段需按实际运行确认，stream_adapter 实现时需做类型容错。

2. **DeepSeek tool calling 行为可控性有限**：模型不一定按精确指令调指定工具（如 get_evidence_ledger）或委派指定子智能体（如 web-researcher）。M0 验证的是"链路通"（agent 流正常结束、产出事件），而非"模型精确执行指令"。这对 M1 提示词设计有影响——不能依赖模型必定调某工具，需在 orchestrator 层做容错与强制轮次上限。

3. **测试需防御性编码**：`async for` 循环的 break 条件不能依赖模型行为（否则模型不执行时循环挂起）。已加迭代上限（count > 200）兜底。

4. **sqlite3 需父目录存在**：测试用 tmp_path 作 workspace，须先 mkdir，否则 sqlite3.connect 报 unable to open database file。已加 autouse fixture。

## 需调整文档的项

- 开发计划 §15 称 `astream_events(version="v3")` 事件为 `{event: ...}` dict——实际结构更复杂（实验性），stream_adapter 实现时需按真实事件类型适配，不能假设固定 dict 结构。
- 开发计划 §11.5 称主智能体"最多执行一轮补充研究"——DeepSeek 行为可控性有限，需在 orchestrator 层强制轮次上限，不能依赖模型自觉。

## 结论

**M0 通过**：DeepAgents 0.6.12 + DeepSeek + Tavily 在本机环境兼容性确认，可进入 M1 真实闭环。

核心链路全通：导入、profile 注册、agent 创建、astream_events v3、Tavily search/extract、subagent 配置、excluded_tools、StateBackend 持久化。DeepSeek 行为可控性边界已记录，M1 提示词和 orchestrator 设计需据此做容错。
