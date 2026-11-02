# 核心契约

- `GoalSpec`：目标 ID、输入资料、约束、交付物、验收条件和缺失信息；解析不把模型 confidence 当概率。
- `RouteDecision`：`direct`、`single`、`multi`、所需能力、single 角色和说明。`direct` 不做工具或外部操作。
- `GlobalPlan`/`TaskSpec`：计划版本、Run ID、目标关联、角色、输入引用、依赖、预期输出、验收条件和状态。校验 ID、引用、角色和无环性。
- `AgentResult`：`status`、摘要、结构化数据、来源、产物引用、未满足要求和错误；父 Agent 只接收此结果，不接收隐藏思维链。
- `IdentityContext`：服务端产生 tenant/user；本地开发身份明确标记为 development，生产环境默认拒绝未配置认证适配器。
- `ExecutionContext`：Run、父子 Agent、委派 ID、深度、角色/工具/工作区和预算。子上下文是父权限与子角色权限的交集。
- `ToolResult`：`SUCCESS`、`FAILED`、`UNKNOWN`。有副作用的超时不能直接重试。
- `RunEvent`：持久化 sequence，可用 `after` 游标继续读；事件只有阶段、动作、结果和错误摘要。

