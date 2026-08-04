# DeepAgents 与 LangGraph 对比笔记

## DeepAgents（本项目的 Agent 框架）
- 基于 DeepSeek 模型与结构化子代理（sub-agent）编排。
- 子代理包括：research（搜索）、document-analyst（文档/知识库分析）、writer（报告撰写）。
- 使用 streaming events 逐条推送工具调用与状态变更，前端可实时渲染。
- 报告通过 `submit_research_report` 结构化提交，支持质量校验与降级提示。

## LangGraph（LangChain 生态）
- 以图（graph）为核心，节点（node）与边（edge）显式定义工作流。
- 状态（state）贯穿整图，支持条件分支、循环、Checkpoint 持久化。
- 偏向底层控制流，需要开发者显式编排每一步。

## 核心差异（要点）
1. **抽象层次**：DeepAgents 面向研究任务封装子代理与工具，开箱即用；LangGraph 提供底层图原语，灵活但需自行搭建。
2. **事件与可观测性**：DeepAgents 原生 streaming 事件流，适合对话式前端；LangGraph 通过 Checkpoint/回调实现类似能力。
3. **报告产出**：DeepAgents 内置报告校验与降级机制；LangGraph 需自行实现产出链路。
