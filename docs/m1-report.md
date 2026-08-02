# DeepAgents 与 LangGraph 在 AI Agent 编排上的主要差异对比

## 执行摘要

- DeepAgents（deepagents 库）不是 LangGraph 的同类竞品，而是 LangChain 官方在 LangGraph 之上构建的高层 agent harness：LangChain 提供模型/工具/提示词等构建块，LangGraph 提供可持久化、有状态的图运行时，deepagents 在两者之上提供开箱即用的智能体封装。 (`S5`, `S19`, `S17`, `S4`; confidence=high)
- DeepAgents 通过内置的 task 工具实现子智能体委派：主智能体可创建临时（ephemeral）子智能体来处理隔离的、长时间运行、多步骤或可并行的任务，每次调用都会实例化新的智能体实例。 (`S7`, `S2`, `S6`; confidence=high)
- LangGraph 提供专门的持久化层：checkpointer 提供短期记忆（在每个节点/步骤后保存状态快照，支持故障恢复与 durable execution），store 提供跨会话的长期记忆。 (`S14`, `S19`, `S21`; confidence=high)
- DeepAgents 的子智能体默认不保留 checkpoint 状态：GitHub issue #573 报告子代理缺乏 checkpoint 持久化与状态查询能力，且每次 task 调用创建新实例意味着子代理状态在调用结束后即丢失，与 LangGraph 原生子图的持久化能力存在差距。 (`S11`, `S7`; confidence=medium)
- DeepAgents 内置成套工具：task（委派子代理）、文件系统读写工具、规划工具等开箱即用，模型在'推理→调用工具→观察→重复'循环中自主决定调用顺序与内容。 (`S7`, `S9`, `S2`; confidence=high)
- 核心范式差异是自主性与确定性：LangGraph 要求先定义工作流再执行（确定性控制流），DeepAgents 让模型在运行时逐步摸索工作流（自主编排），前者可控可测，后者灵活但有不可预测性。 (`S1`, `S4`, `S18`; confidence=medium)

## 定位与架构关系

- DeepAgents（deepagents 库）不是 LangGraph 的同类竞品，而是 LangChain 官方在 LangGraph 之上构建的高层 agent harness：LangChain 提供模型/工具/提示词等构建块，LangGraph 提供可持久化、有状态的图运行时，deepagents 在两者之上提供开箱即用的智能体封装。 (`S5`, `S19`, `S17`, `S4`; confidence=high)
- 定位差异决定了抽象层级：LangGraph 提供低层原语（节点、边、状态图），开发者需自行组装；DeepAgents 则内置规划、子代理委派、文件系统等'电池'能力，属于'有主见的(opinionated)'封装。 (`S2`, `S7`, `S9`; confidence=medium)

## 子智能体委派方式

- DeepAgents 通过内置的 task 工具实现子智能体委派：主智能体可创建临时（ephemeral）子智能体来处理隔离的、长时间运行、多步骤或可并行的任务，每次调用都会实例化新的智能体实例。 (`S7`, `S2`, `S6`; confidence=high)
- DeepAgents 子智能体每次调用都获得全新上下文（fresh context），与主智能体上下文隔离，这是其控制上下文膨胀的核心机制，但代价是子智能体执行细节不会回灌主上下文。 (`S7`, `S2`, `S3`; confidence=medium)
- 委派决策权差异：DeepAgents 中是否委派、委派给谁由模型在运行时自主决定（模型驱动、动态规划）；LangGraph 中多智能体拓扑（如子图 subgraph、Send API 动态扇出）由开发者预先以图结构定义，控制流更显式、可审计。 (`S1`, `S13`, `S18`; confidence=medium)

## 状态持久化

- LangGraph 提供专门的持久化层：checkpointer 提供短期记忆（在每个节点/步骤后保存状态快照，支持故障恢复与 durable execution），store 提供跨会话的长期记忆。 (`S14`, `S19`, `S21`; confidence=high)
- LangGraph 子图（subgraph）可以配置自己的 checkpointer 以拥有独立记忆，并可通过 get_state 查询子图状态；记忆范围可在 per-invocation（每次调用新状态，适合多智能体独立请求）与 per-thread（保留多轮对话记忆）之间选择。 (`S11`, `S13`; confidence=medium)
- DeepAgents 的子智能体默认不保留 checkpoint 状态：GitHub issue #573 报告子代理缺乏 checkpoint 持久化与状态查询能力，且每次 task 调用创建新实例意味着子代理状态在调用结束后即丢失，与 LangGraph 原生子图的持久化能力存在差距。 (`S11`, `S7`; confidence=medium)
- DeepAgents 本身依赖 LangGraph 运行时获得 durable execution、streaming、human-in-the-loop 等能力，但高层默认封装未向用户暴露子代理级持久化配置，持久化控制权下沉到 LangGraph 层。 (`S5`, `S21`, `S11`; confidence=medium)

## 工具调用机制

- DeepAgents 内置成套工具：task（委派子代理）、文件系统读写工具、规划工具等开箱即用，模型在'推理→调用工具→观察→重复'循环中自主决定调用顺序与内容。 (`S7`, `S9`, `S2`; confidence=high)
- LangGraph 本身不绑定具体工具：工具调用以节点（node）形式或通过 LangChain 工具集成生态接入，开发者需自行构建 ReAct 式循环或选用预构建 agent 抽象，工具只是图中被编排的一等对象。 (`S19`, `S16`, `S20`; confidence=medium)
- 两者底层的工具循环机制同源：deepagents 把 LangGraph 的'模型推理→工具调用'循环封装进 harness 并叠加规划/子代理工具，而 LangGraph 要求开发者把该循环显式建模为图的节点与边。 (`S21`, `S1`; confidence=medium)

## 编排范式与取舍

- 核心范式差异是自主性与确定性：LangGraph 要求先定义工作流再执行（确定性控制流），DeepAgents 让模型在运行时逐步摸索工作流（自主编排），前者可控可测，后者灵活但有不可预测性。 (`S1`, `S4`, `S18`; confidence=medium)
- DeepAgents 的便利有 token/上下文成本：其主智能体输入为固定大小（存在'上下文地板'，每轮交易都携带固定前缀），有社区观点估算相比裸 LangGraph 便捷成本可达 20 倍，但该数字为单篇观点文章，缺乏可复现基准。 (`S3`, `S1`; confidence=low)

## 资料冲突

### DeepAgents 子智能体是否具备持久化/跨任务记忆能力
- DeepAgents 子代理默认缺乏 checkpoint 持久化与状态查询（GitHub issue #573），每次调用创建新实例、状态不保留 (`S11`, `S7`)
- DeepAgents 可'维护跨复杂任务的上下文'，且底层 LangGraph 子图支持 per-thread 多轮记忆，持久化能力取决于配置方式 (`S6`, `S13`)

## 未知项

- GitHub issue #573 报告的子代理 checkpoint 缺失问题是否已在后续版本修复，无法从当前证据验证（证据为问题报告，非版本状态快照）。
- LangGraph 与 DeepAgents 在工具调用上的延迟/吞吐等性能基准，公开来源中缺乏可对比的权威数据。
- S1 中'20x 便捷成本'的具体计算口径与复现方法未公开，无法核实其准确性。
- DeepAgents 最新版本内置工具集（task/file/plan 之外）的完整清单与变化，超出本次 5 个网页来源的覆盖范围。

## 行动建议

- 需要快速交付具备规划、子代理委派、文件系统能力的生产级 agent 时优先选 DeepAgents；需要精细控制图结构、状态 schema 与持久化策略时选 LangGraph。 (`S2`, `S4`, `S18`; confidence=high)
- 若业务要求子代理级持久化（如子代理需要多轮对话记忆或可恢复的长任务），不要依赖 DeepAgents 默认子代理行为，应在 LangGraph 层使用带独立 checkpointer 的子图或 per-thread 记忆方案。 (`S11`, `S13`; confidence=medium)
- 选型前应量化上下文/token 成本：DeepAgents 的固定输入'上下文地板'与自主子代理委派可能带来额外 token 开销，对高并发、低成本敏感场景需评估裸 LangGraph 的显式编排。 (`S3`, `S1`; confidence=low)
