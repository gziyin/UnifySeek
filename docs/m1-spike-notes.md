# M1 真实闭环 Spike 结论

执行日期：2026-08-02
环境：Python 3.13.12 / Windows 11 / deepagents==0.6.12 / langchain==1.3.14 / langchain-core==1.5.3 / langgraph==1.2.10 / DeepSeek (deepseek-v4-flash) / Tavily

## 验证项与结果

| # | 验证项 | 结果 | 备注 |
|---|--------|------|------|
| 1 | 底层 agent `ainvoke` 完整链路（search_web → extract_web_sources → submit_research_report） | PASS | 生成非降级带引用报告 |
| 2 | DeepSeek 是否自觉先搜索再提交 | 不稳定 | 曾 3 次直接 submit（引用不存在的 C1）被校验拒绝，改 prompt + 直连 search_web 后跑通 |
| 3 | submit_research_report schema 一次提交成功率 | 低 | 首次提交经历 2 次 ValidationError 自我修正才成功 → 已加 args_schema |
| 4 | 校验失败时降级报告 | PASS | 不抛异常，生成 `[DEGRADED]` 报告保闭环 |
| 5 | service 层 executor 事件流 | FAIL→已修 | 见关键发现 1 |

## 关键发现

1. **langgraph 1.2.10 `astream_events(version="v3")` 与 stream_adapter 的 v2 解析不兼容（M1 最大坑）**。
   - v3 是**实验性 run-stream 协议**：事件形如 `{"type":"event","method":"messages"|"values","params":{...},"seq":N}`，工具执行事件需走 `tool_calls` 投影（`run.tool_calls`）而非标准 `{event:"on_tool_end"}`。
   - 我们的 `stream_adapter.map_framework_event` 按经典 v2 格式（`{event,name,data,run_id}`）解析 → executor 永远收不到 submit 完成事件 → "agent finished without submit_research_report"。
   - **修复**：executor 统一 `version="v2"`（稳定默认协议）。离线验证 langchain-core 1.5.3 的 tool `astream_events(version="v2")` 确实产出 `on_tool_start/on_tool_end`。
   - **教训**：M0 的 v3 测试只断言 `count>0`，未校验事件结构 → "v3 可用"是虚结论。测试必须断言"能拿到想要的事件类型"，不能只数事件。

2. **DeepSeek 行为可控性有限（延续 M0 发现，M1 实测确认）**：
   - 模型会跳过搜索直接提交报告，且提交结构经常不合法。
   - 对策分三层：① orchestrator 直连 `search_web`/`extract_web_sources`（不依赖委派 web-researcher）；② prompt 明确"必须先搜后交"；③ 校验失败降级为 `[DEGRADED]` 报告，保证 run 能终结（标 FAILED 但保留 artifact_id）。
   - 追加对策（M1 收尾）：给 `submit_research_report` 加结构化 `args_schema`（顶层扁平字段 + 嵌套模型 + 字段描述），模型第一次就能按结构提交，省 token、少自我修正轮数。

3. **降级报告设计决策**：校验失败（ValidationError / ReportValidationError）不抛异常，生成 `[DEGRADED]` 标题的报告并附原始提交 JSON，便于定位。executor 检测到 `degraded=True` 将 run 标 FAILED 但保留 `report_artifact_id`——"闭环能走完，但不误报成功"。

4. **`ainvoke` vs `astream_events(v2)` 的选择**：`ainvoke` 最稳（一次跑完整图，从最终 messages 提取工具结果），但无增量事件、UI 体验差；`astream_events(v2)` 能实时出 `on_tool_start/on_tool_end`，适配器直接可用。M1 选 v2——保实时事件的同时闭环可控。真实 smoke 用 service 层 executor（v2 流）验收。

## 需调整文档的项

- 开发计划/技术栈中所有 "astream_events v3" 表述应改为 "astream_events v2"（v3 实验性协议不用于生产）。
- 开发计划 §11.5 "主智能体最多一轮补充研究" 等依赖模型自觉的表述，需补充"不依赖模型自觉，orchestrator 层强制轮次上限 + 工具直连"。

## 结论

**M1 代码层完成**：事件协议修复（v3→v2）+ 降级报告 + orchestrator 直连搜索 + args_schema，离线回归 19 passed 9 skipped。真实 service 层 smoke 待 DeepSeek 配额恢复（20:08:55）后执行（已挂自动化 20:12 跑 `scripts/m1_service_smoke.py`）。
