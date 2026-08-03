# W3 M2 真实 Agent 端到端验收报告（2026-08-03）

## 验收结论：通过 ✅

真实 DeepSeek + Tavily 跑通"上传笔记 → 多智能体调研 → 带引用 Markdown 报告"，
run 状态 `succeeded`，报告非 degraded，52 秒收敛。

- 运行脚本：`backend/scripts/e2e_verify.py`（httpx 封装，可重复执行）
- run 时长：52s（聚焦问题）
- 证据：20 条 web 证据（S1-S20），含 extract_web_sources 升级的 first_party 正文
- 报告：1939 字符，执行摘要 / 分节 / 未知项 / 行动建议齐全，全部 claim 带 citation_ids
- 事件流：run.started → source.discovered → evidence.recorded → artifact.created →
  report.ready → run.succeeded

## 验收报告内容（节选）

标题：DeepAgents（LangChain Deep Agents）与 LangGraph 智能体编排核心差异

- LangGraph 是低层、有状态、基于图的编排运行时（持久化/流式/中断/状态管理）；
  DeepAgents 构建在 LangGraph 之上，加入有主见的 agent 规划与执行模式（S3,S5,S11,S13）
- 核心差异：LangGraph 要求预先定义工作流（显式编排），DeepAgents 让模型执行中
  自主决定工作流（自主编排）（S1,S6）
- DeepAgents 内置 task 工具，可动态创建上下文隔离的临时子代理（S17,S20）
- 未知项：DeepAgents 精确 API 细节与性能数据未获完整官方原文验证；S1 提及
  "约 20 倍成本便利性代价"但缺可核验量化数据
- 行动建议：确定性流程选 LangGraph；自主长时程任务选 DeepAgents；两者可组合

## 测试全量回归

- 后端：66 passed, 9 skipped（无 key 时 M0 自动 skip），0 failed
- 前端：vitest 4 passed；tsc + vite build 通过

## 过程中发现并修复的问题

1. **Windows torch DLL 顺序坑**：langchain_core 顶层导入 transformers→torch，若
   tokenizers/transformers 先于 torch 加载，c10.dll 初始化失败（WinError 1114）。
   解法：包入口 `__init__.py` 顶层调 `storage/torch_guard.ensure_torch_loaded()`。
   transformers 需降级 4.57.x（5.14.1 与 torch 2.13 冲突）。
2. **write_todos 事件缺失**：deepagents 内置 write_todos 工具未被 stream_adapter
   映射为 plan.updated 事件（前端 Todo 不更新）。已修复 + 单测。
3. **验收问题要聚焦**：开放问题（如"结合知识库源码说明"）会触发模型逐文件读取
   deepagents 源码漫游 10min+ 不收敛（无迭代上限保护）。聚焦小问题 52s 收敛。
4. **真实模式偶发"模型提前收尾"**：3 次搜索后直接结束对话不调 submit_research_report，
   重试或调大 max_web_sources 可解。

## 关键交付

| 里程碑 | 状态 |
|---|---|
| M2.1 知识库工具（K 类证据） | ✅ |
| M2.2 事件增强（url/行号/excerpt/tool_input） | ✅ |
| M2.3 证据校验增强 + 注入/权限测试 | ✅ |
| M2.4/M2.5 RAG（docling + Chroma + 语义检索） | ✅ |
| M2.6 前端交互（时间线展开/账本详情/报告预览） | ✅ |
| M2.0 start.bat 一键启动 | ✅ |
| M2.7 全量回归 + 真实 Agent 验收 | ✅ |

## 后续可选项

- orchestrator 增加 max_steps/迭代上限，防真�� Agent 发散漫游
- GGUF embedding provider 接入（模型已就绪：E:/04Programming/Models/Qwen3-Embedding-0.6B-GGUF）
- issues.txt 新增 issue：研究问题输入字数限制解除；DEGRADED 报告样例归因
