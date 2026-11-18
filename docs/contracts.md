# 核心契约

- `GoalSpec`：目标 ID、输入资料、约束、交付物、验收条件、结构化输入需求和缺失信息投影；解析不把模型 confidence 当概率。
- `GoalSpec`：描述用户明确要求的目标。`required_capabilities` 只表示不可缺少的专门执行能力；普通语言推理不属于 capability。
- 目标解析分为三层：模型只生成不可信的意图 Candidate；服务端 Compiler 注入调用方材料和约束并校验能力；确定性 Resolver 计算 requirement 的满足状态。缺少材料只影响可执行性，不会删除已识别的目标或 requirement。
- `InputRequirement`：描述 material、comparison subject、calculation operand、time range、template、destination 或 clarification。`status`、`satisfied_by` 由服务端 Resolver 拥有；`missing_information` 是必需且未满足需求的用户界面投影。
- 有效的 `GoalSpec` 必须包含至少一个目标，或者是显式的“请用户补充目标”澄清状态。已有目标但缺少材料时，目标与 `missing_information` 同时保留。
- `Deliverable`：仅表示用户明确要求的交付成果。`inline` 在对话中呈现，不隐式产生能力；`artifact` 是独立持久化产物，需要注册的产物能力。普通聊天回复不是 deliverable。
- `RouteDecision`：`direct`、`single`、`multi`、所需能力、single 角色和说明。路由是执行策略而非业务目标。`direct` 不做工具或外部操作，但可以产生 inline 输出。
- `AnswerAgent`：direct 路径中的无工具回答 Agent；只接收用户请求和补充材料，返回 `AgentResult`，不访问实时信息、不执行副作用。
- `GlobalPlan`/`TaskSpec`：计划版本、Run ID、目标关联、角色、输入引用、依赖、预期输出、验收条件和状态。校验 ID、引用、角色和无环性。
- `AgentResult`：`status`、摘要、结构化数据、来源、产物引用、未满足要求和错误；父 Agent 只接收此结果，不接收隐藏思维链。
- `IdentityContext`：服务端产生 tenant/user；本地开发身份明确标记为 development，生产环境默认拒绝未配置认证适配器。
- `ExecutionContext`：Run、父子 Agent、委派 ID、深度、角色/工具/工作区和预算。子上下文是父权限与子角色权限的交集。
- `ToolResult`：`SUCCESS`、`FAILED`、`UNKNOWN`。有副作用的超时不能直接重试。
- `RunEvent`：持久化 sequence，可用 `after` 游标继续读；事件只有阶段、动作、结果和错误摘要。
- 等待用户输入时，图必须先提交已验证的 `GoalSpec` 和解析来源，再进入 interrupt。持久化层使用该 checkpoint 事实，不得为生成快照而重新解析请求。
- 持久化 orchestration state 必须携带 `state_version`，读取时经逐版本迁移；未来版本失败关闭。
- Parser 和 Planner 的 provenance 必须与领域结果原子返回并写入图状态，不得通过 ContextVar 或共享 `last_trace` 传递业务来源。

