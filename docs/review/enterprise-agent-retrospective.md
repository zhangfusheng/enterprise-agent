# EnterpriseAgent 深度复盘记录

> 本文档只记录经学习者回答、纠错和验收后的复盘进度。
> “Codex 已讲解”不等于“学习者已掌握”。

## 当前进度

- 当前阶段：Phase 4 进行中
- 当前知识点：Phase 4 工程异常场景——Planner fallback 与服务端 Bug 隐藏风险
- 记录启用日期：2026-11-20
- 验证口径：源码静态调查 + 学习者问答验收
- 注意：Phase 0 未运行测试；当时工作区存在未提交修改，结论对应当前工作区快照。

## Phase 0：项目源码调查与复盘计划

### 调查结论

- 系统是通用 Agent 应用底座，不是已完成的游戏运营业务系统。
- 运行时为模块化单体代码、API/Worker 分进程。
- 主链路：`POST /runs -> submit_run -> Run/WorkItem -> claim_work -> execute_run -> LangGraph -> commit_outcome`。
- 已找到 GoalSpec、Router、Planner、DAG、Supervisor、Worker、ToolGateway、checkpoint、lease/fencing、Trace 和 eval 的实现。
- 当前未完成的生产能力包括真实生产认证、密钥管理、HA、合规审计归档和容量压测。

### 关键源码

- `apps/api/main.py`
- `apps/api/routes/runs.py`
- `apps/worker/main.py`
- `src/enterprise_agent/application/execute_run.py`
- `src/enterprise_agent/orchestration/graph.py`
- `src/enterprise_agent/bootstrap/container.py`
- `src/enterprise_agent/infrastructure/repositories/runs.py`
- `src/enterprise_agent/tools/gateway.py`

### 验证状态

- 已完成静态源码调查。
- 未在 Phase 0 执行单元、集成、恢复、安全或真实模型测试。
- 文档中的真实模型结果是历史记录，未在本次复盘中复验。

## Phase 1：项目背景、业务需求与系统定位

### 学习者已掌握

- EnterpriseAgent 是通用、可运行、可验证、可恢复、受控扩展的 Agent 应用底座，不是具体企业业务成品。
- 游戏运营、客服等可作为潜在上层场景，但需要额外接入业务数据源、领域工具、权限和审计规则。
- LangGraph 提供图执行、checkpoint 和 interrupt；EnterpriseAgent 补充业务状态、执行所有权、领域契约、权限预算、副作用治理、应用恢复和证据链。
- `UNKNOWN` 属于 Tool operation 结果，不是 Run/WorkItem 状态。
- 稳定 `operation_id` 提供逻辑操作身份和去重依据，但无法单独证明外部副作用是否成功。
- lease 确立有时限的执行所有权；fencing token 阻止过期 Worker 写入应用状态，但不能撤销已发生的外部副作用。
- 对副作用重放测试，核心断言应是实际工具 `call_count == 1`，而不只是工单状态正确。

### 曾出现的理解错误

- 曾将项目误述为已实现玩家、充值和游戏规则查询的业务系统。
- 曾将 LangGraph 误述为不支持持久化。
- 曾混淆 Run/WorkItem 状态与 Tool operation `UNKNOWN`。
- 曾把稳定 operation ID 误解为可以自行确认外部操作结果。

### Codex 已讲解但尚未独立验收

- 外部系统幂等键、状态查询、补偿事务与人工对账的方案选型。
- 生产审计归档与当前 Event/Trace 可观测证据的合规差异。

### 阶段评分

- 原理与业务理解：19/20
- 架构设计与技术取舍：19/20
- 代码实现理解：17/20
- 异常处理与工程可靠性：18/20
- 测试验证与技术表达：17/20
- 总分：90/100，通过

## Phase 2：整体架构、核心组件与请求生命周期

### 学习者已掌握

- API 负责身份上下文、HTTP/请求校验、模型参数校验和持久化提交，不在请求处理函数中执行 Agent 图。
- Worker 通过 `claim_work` 获得带有时限的执行权，然后调用 `execute_run`。
- `RunRow` 是业务执行事实；`WorkItemRow` 是调度与执行所有权记录。
- 正常状态链：
  - 创建：Run `PENDING`，WorkItem `QUEUED`，无 lease。
  - 领取：WorkItem `RUNNING`，写入 Worker、token、过期时间，generation 递增，获得执行权。
  - `mark_running`：验证所有权后 Run `PENDING -> RUNNING`。
  - `commit_outcome`：Run 转终态，WorkItem `DONE`，清理 lease，释放执行权。
- `_RunExecution.run()` 管理 lease/heartbeat、执行尝试和 lease-loss 边界；`_execute()` 管理业务状态与失败收敛；`_invoke()` 恢复状态、装配每 Run 依赖并调用图。
- 普通异常发生时，当前 Worker 仍持有合法 lease，可尝试提交本次执行失败；`LeaseLost` 必须停止写入并传播。
- LangGraph checkpoint 是图状态快照与恢复游标；应用数据库中的 Run、结果、事件、产物及 WorkItem `DONE` 是对外业务事实。
- 旧 Worker 即使持有成功 checkpoint，使用过期 token 提交仍必须得到 `LeaseLost`；最终断言应检查 Run 保留新 Worker 的 `fresh` 结果。
- API/Worker 分进程用于隔离长任务与短 HTTP 请求，支持独立扩缩容、故障隔离和持久化恢复，代价是异步一致性和调度复杂度。
- 容量基础关系：`Worker 吞吐 = Worker 数 / 平均服务时间`；已能计算到达率高于服务率时的净积压。

### 曾出现的理解错误

- 曾将 API HTTP 路由与 Agent Goal/Route 解析混淆。
- 曾把 Run 与 WorkItem 说成彼此创建的同一类记录，未区分业务与调度生命周期。
- 曾混淆 `lease_token=None` 与 `lease_generation=0`。
- 曾认为 checkpoint 应决定 Run 最终业务状态。
- 曾只断言 WorkItem 的 Worker ID，没有断言旧 token 提交被拒绝且业务结果未被覆盖。
- 曾在容量计算中混淆“每秒 20 个”与“每分钟 20 个”。

### Codex 已讲解但尚未独立验收

- 数据库轮询队列与 Kafka、RabbitMQ、Redis Streams 等专业消息系统的具体选型。
- 600 级理论 Worker 并发下的模型限流、连接池、外部工具限制与成本预算的联合容量模型。
- checkpoint 与应用数据库跨事务窗口的完整故障矩阵。

### 阶段评分

- 原理与业务理解：18/20
- 架构设计与技术取舍：18/20
- 代码实现理解：18/20
- 异常处理与工程可靠性：18/20
- 测试验证与技术表达：16/20
- 总分：88/100，通过

## Phase 3：Goal Candidate、GoalSpec、Parser、Requirement Resolver、Validator 与 Router

### 已验收知识点：Candidate 与 GoalSpec 的状态所有权

- 模型只提出不可信 `GoalIntentCandidate`，不拥有最终解析和路由决策权。
- 模型可提出 goal 的 ID、描述和所需 capability，deliverable、acceptance criterion，以及 input requirement 的描述性部分。
- Compiler 只接受 capability catalog 中的能力；未知能力触发 `CandidatePolicyError`。能力合法不等于已获得实际工具或联网权限。
- 调用方传入的 inputs 和 constraints 是权威数据；用户材料中的指令不能修改系统约束。
- requirement 的 `status`、`satisfied_by`、`source` 由服务端拥有，Candidate 尝试输出这些字段会被拒绝。
- Resolver 根据请求文本、实际材料引用数和 `minimum_items` 确定 `satisfied/missing`。
- `missing_information` 是所有必需且未满足 requirement 描述的去重投影。
- Pydantic schema 通过只证明结构合法，不代表已满足语义、policy 和领域 invariant。

### 本知识点评分

- 最终字段归属题：96/100，通过
- 曾混淆“模型提出 capability/requirement”与“模型拥有权威状态”，经场景题纠正后已通过。

### 已验收知识点：Parser 失败分类与 fallback 边界

- schema 与 Candidate semantic 错误可做一次有界模型修正，仍失败则 fallback。
- 模型 transport/timeout 异常当前不做模型重试，直接使用确定性规则 Parser fallback。
- capability/policy 拒绝不允许模型通过重试争取权限，而是丢弃 Candidate 并 fallback。
- `GoalSpecInvariantError` 表示服务端 Compiler 或契约产生内部不一致，必须失败关闭，不能用 fallback 隐藏服务端 Bug。
- 已通过复合场景验收：首次 schema 错误触发一次修正，第二次 Candidate 合法但 Compiler 抛 invariant 时，最终 Run 失败而非 fallback。

### 本知识点评分

- 最终复合失败链判断：94/100，通过

### 已验收知识点：Input Requirement Resolver

- 用户已表达的 goal 与该 goal 当前是否可执行必须分离；缺少材料不会删除“分析资料”目标。
- 补充前：inputs 为空，material requirement 为 `missing`，`satisfied_by=[]`，`missing_information` 包含该必需 requirement 的 description。
- 补充后：材料进入 inputs，requirement 为 `satisfied`，`satisfied_by` 记录实际 material 引用，`missing_information` 清空。
- goal 的业务意图及 requirement 的描述性定义应稳定；inputs、requirement 解析状态和缺失信息投影允许重算。
- 该分离使暂停原因、补充材料与 requirement 的满足关系可恢复、可解释、可审计。

### 本知识点评分

- 最终前后状态题：91/100，通过

### 已验收知识点：GoalSpec Validator 与 Router

- `validate_goal_spec()` 校验/规范化 goal、capability、artifact deliverable、各类 ID 及 requirement 的 goal 引用，不负责选择执行路由。
- `Router.decide()` 的输入前提是 GoalSpec 合法且不再等待输入。
- 无特殊 capability 且无 artifact 需求时为 direct；单角色覆盖所有需求时为 single；无单角色完全覆盖、但多角色联合可覆盖时为 multi。
- `validate_route()` 再次验证 RouteDecision 与 policy 一致，包括 single 角色覆盖及 multi 联合覆盖。
- 未知 `shell` capability 在进入 Router 前由 GoalSpec Validator 拒绝；`web_search + analysis` 在当前角色策略下选择 multi。
- Router 不应静默删除非法 capability，否则会隐藏未完成需求并破坏结果完整性与可解释性。

### 本知识点评分

- 最终分层场景题：88/100，通过

### 已验收知识点：Capability Demand 与 Permission

- GoalSpec/Router 中的 `web_search` 表示任务需求，不表示已获得联网许可。
- 用户请求文本是不可信业务数据，无法打开系统级联网权限或放宽调用方的权威约束。
- 系统联网禁用时，`parse_route` 可持久化 `WAITING_INPUT`，但 `awaiting_user_input=False`，图直接结束而非进入用户输入 interrupt。
- 缺失业务材料时，用户是信息所有者，可通过补充 input 恢复；缺失联网许可时，只有部署配置或调用方权威约束变化才可解除阻塞。
- ToolGateway 在最终执行边界重新检查权限和联网许可，用于防止错误路由、绕过 Graph 的直接调用和执行上下文不一致，属于纵深防御。

### 本知识点评分

- 最终权威信息来源题：92/100，通过

### 已验收知识点：Parser/Compiler/Resolver/Validator/Router 调用链

- 对“搜索最新资料并分析，生成 Markdown 报告”，Candidate 应表达 `web_search`、`analysis` 和 Markdown artifact deliverable，而不是生成调用方 material requirement。
- Compiler 验证 Candidate capability，并注入调用方权威 inputs/constraints；Resolver 在无必需调用方输入时产生空 `missing_information`。
- GoalSpec Validator 验证 capability、ID/引用及 Markdown artifact 对 `save_report` 能力的映射。
- Router 将 `web_search + analysis + save_report` 视为执行需求；因无单角色覆盖全部需求、但 Research/Analysis 联合可覆盖，选择 multi。
- Route Validator 验证 multi 必要性与联合覆盖；联网许可有效时，Graph state 写入 `route=multi`、`awaiting_user_input=False`、空缺失信息、parse provenance 和 `RUNNING`，进入 plan。
- 已能区分“外部搜索资料”与“调用方必须提供的材料”：前者是授权 Worker 在执行期获取的数据，后者是执行前置条件。

### 本知识点评分

- 最终端到端解析/路由链：95/100，通过

### 已验收异常场景：Policy Rejection 后的安全 Fallback

- 模型为普通解释请求伪造未注册 `shell` capability 时，Compiler 必须拒绝该 Candidate。
- 丢弃 Candidate 后，使用原始 text 及调用方权威 inputs/constraints 执行规则 Parser，得到无特殊 capability 的合法目标并路由为 direct，不属于权限绕过。
- fallback 不得传递 Candidate 中的 capability、伪造约束或服务端状态。
- provenance 必须记录 `parser=fallback`、model/prompt version、attempts 和 `fallback_reason=policy`，不得伪装成 LLM 正常解析。
- 规则 Parser 也无法产生合法 GoalSpec 时必须失败关闭，不得强制 direct。

### 异常场景评分

- 93/100，通过

### 已验收测试设计：恶意 Capability 不污染权威 GoalSpec

- fake completion 可返回一个绕过 provider schema 的 Candidate，其 goal 声称需要未允许的 `shell`。
- `LLMGoalParser` 的允许 capability 集合不包含 `shell`；调用 `parse_result()` 时显式传入合法原始 text、空 inputs 和 `allow_external_network=False` 权威约束。
- 核心断言：最终 GoalSpec 无特殊 capability、RouteDecision 为 direct、provenance 为 fallback/policy，且最终 GoalSpec 保留调用方 `allow_external_network=False`。
- 该测试同时验证了“拒绝模型伪造权限”与“保留用户合法原始目标”。

### 测试设计评分

- 94/100，通过

### Phase 3 最终验收

- 已能从不可信 Candidate 串联 Compiler、Resolver、GoalSpec Validator、Router 和 Route Validator。
- 已能区分模型提出的意图、调用方权威 inputs/constraints、服务端解析状态及 policy 能力目录。
- 已掌握 schema/semantic/policy/transport/invariant 的当前重试、fallback 和 fail-closed 边界。
- 已能解释 direct/single/multi 的能力覆盖规则，以及 capability demand 与 permission 分离。
- 已通过缺材料恢复、联网禁止、恶意 capability fallback 和相应测试设计验收。

### Phase 3 阶段评分

- 原理与业务理解：18/20
- 架构设计与技术取舍：17/20
- 代码实现理解：17/20
- 异常处理与工程可靠性：18/20
- 测试验证与技术表达：16/20
- 总分：86/100，通过

### Phase 3 仍需改进

- 面试表达时应先定义 Candidate 为“模型提出的结构化、不可信意图”，不能只说“通过结构化调用生成”。
- direct 的完整条件是无特殊 capability 且无 artifact deliverable。
- 测试证据表达还应主动包含 provenance fallback reason 和调用方 constraints 未被污染的断言。

## Phase 4：GlobalPlanner、TaskSpec、DAG、Supervisor 与 Worker

### 已验收知识点：GlobalPlan/TaskSpec 的权威状态

- 自然语言 Todo 不能提供稳定任务身份、目标引用、角色约束、可校验依赖、验收标准和服务端状态转移，因此不能作为全局调度事实。
- `task_id` 标识具体执行记录；`task_key` 是跨 plan version 的稳定逻辑身份。
- 模型 draft 可提出任务身份/描述、goal 引用、role、capability、input、dependency、expected output 和 acceptance criteria，但必须经过 Compiler 与 Validator。
- `status`、`attempts` 和预算使用量属于服务端状态；模型伪造 `SUCCEEDED` 或 attempts 不得成为权威值。
- Compiler 依据已完成逻辑任务设置初始成功状态，其他任务为 `PENDING`；Supervisor 在真实执行开始时递增 attempts，并根据 AgentResult 更新任务状态。
- 未知 goal/dependency/input 引用、未注册角色、越权 capability、超出任务/剩余预算限制、DAG 环及 multi 中 analysis 不依赖 research 都必须被拒绝。
- DAG 解决依赖、就绪判断、环检测、阻塞传播和结果引用；恢复来自持久化图状态、plan history、task results 和应用数据库，不是 DAG 本身。
- Deep Agents Todo 是 Worker 单次执行内部的局部进度，不是全局调度、跨 Worker 任务身份和最终验收的权威状态。
- replan 必须保留可复用证据、避免重复执行/副作用，并在上游任务变化时使下游旧结果失效，不能无条件丢弃旧计划。

### 本知识点评分

- 最终字段所有权与校验分层题：88/100，通过

### 已验收知识点：DAG 就绪与阻塞传播

- `ready_tasks()` 只返回 `PENDING` 且全部 dependency 已为 `SUCCEEDED` 的任务。
- 在 A 成功、B/C 依赖 A、D 依赖 B/C 的场景中，ready 轮次为 `[A] -> [B,C] -> []`。
- 当前 `execute` 节点对同一 ready 快照中的 B/C 顺序执行，并非并行调度；B 失败不会阻止已在同一快照中就绪的 C 执行。
- B 失败后 D 的 dependency 未全部成功，无法就绪；`skip_blocked()` 将剩余 `PENDING` 任务标记为 `SKIPPED`。
- 失败不必然触发 replan；只有任务状态为 `FAILED/PARTIAL`、`decide_recovery()` 返回 `RETRY`，且未超过 `max_replans` 时才进入 replan。

### 本知识点评分

- 88/100，通过

### 已验收知识点：Replan 匹配与结果复用

- `task_key`/ID/语义签名用于匹配新旧任务，但“匹配到同一逻辑任务”不等于“旧结果仍可复用”。
- 结果复用还要求任务定义兼容，且旧 AgentResult 的 recovery action 为 `PROCEED`。
- 新计划中 A2 匹配并复用旧 A 时，`task_results[A2]=previous_results[A]`，并记录 `task_mapping[A]=A2`；A2 初始状态为 `SUCCEEDED`。
- 相同 `task_key` 的 B2 如修改 expected output，或旧结果需要 retry，则 action 为 `repaired`、初始状态为 `PENDING`。
- 新增 C2 的 action 为 `new`、初始状态为 `PENDING`。
- 上游任务为 new/repaired 时，依赖它的下游旧结果基于已失效证据，即使下游自身定义兼容也必须传播为 repaired。

### 本知识点评分

- 96/100，通过

### 已验收知识点：Supervisor、Worker 与 Recovery Policy

- `ready_tasks()` 负责确定可执行任务；Supervisor 是 GlobalPlan 任务状态转移的权威所有者；Worker 只执行业务并返回 `AgentResult`。
- Supervisor 在 Worker 调用前将 Task 标为 `RUNNING` 并递增 attempts，然后根据 AgentResult 与 RecoveryDecision 更新 TaskStatus。
- WorkerRegistry 不只注册 Worker，还负责按 `agent_role` 分派任务，并为当前 Run 绑定共享的预算 ledger、cancellation token 与持久化 operation repository。
- Recovery Policy 将 AgentResult 分类为 `PROCEED/RETRY/FAIL`，不盲目把所有 `PARTIAL` 视为失败。
- `PARTIAL` 如果同时具有非空 summary、可用 sources/artifact/structured data，且无 unmet requirements，RecoveryAction 为 `PROCEED`；Task 可标为 `SUCCEEDED`，但原 AgentResult 仍保留 `PARTIAL` 供最终汇总显示。
- 无可用输出的 `PARTIAL` 为 `RETRY`；特定的无证据 research fetch budget exhausted 失败为 `FAIL`，其他当前 `FAILED` 结果默认可 retry。
- 执行控制与业务执行分离，防止 Worker 自行修改全局事实，同时提高角色扩展、单元测试、恢复决策和可观测性。

### 本知识点评分

- 90/100，通过

### 已验收知识点：Research -> Analysis 证据边界

- Worker 通过结构化 `AgentResult` 交换 summary、structured data、sources、artifact refs、unmet requirements 和 errors，不传递隐藏思维链或局部 Todo。
- AnalysisWorker 只遍历当前 Analysis Task `depends_on` 指定的上游结果，不扫描全局 task results；未声明依赖的 Research B 对 Analysis 不可见。
- `_research_bundle()` 从 dependency result 中提取 research metadata、structured evidence、sources、source assessments、evidence index 和 unresolved questions，并组装 `ResearchEvidenceBundle`。
- sources 按 document ID/规范化 URL 去重，source assessments 按 candidate ID 去重，无效结构和缺少有效 URL 的项被忽略。
- 允许 Analysis 读取全局共享内存会破坏依赖隔离、状态持久化/版本迁移、来源可验证性，并扩大不可信网页内容与提示注入的传播面。
- replan 中 A2 为 repaired 时，旧 A AgentResult 迁移为 `task_results[A2]`，作为下一次 ResearchRequest 的 historical evidence；历史 URL 进入 excluded URLs，但 A2 仍为 `PENDING`，旧证据不是完成证明。

### 本知识点评分

- 最终依赖隔离/历史证据题：92/100，通过

## 尚未解决的问题

- Phase 0 发现的当前工作区未提交变更尚未固定为可复现基线。
- 尚未对当前工作区执行完整测试套件。
- 复盘过程尚未进入 Goal/Parser/Router、Planner/DAG、Tool Runtime、Memory、Streaming/Cancel、Observability/Evaluation 的深入代码验收。

## 下一步

- Phase 3：Goal Candidate、GoalSpec、Parser、Requirement Resolver、Validator 与 Router。
- 首个验收点：区分“模型候选意图”、“服务端编译后领域契约”和“确定性输入需求解析”的状态所有权。
