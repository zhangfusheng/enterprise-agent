# Agent Patterns

| 模式 | 所在层 | 组件 | 框架来源 | 默认/显式配置 | 自建补充 | 对应测试 |
| --- | --- | --- | --- | --- | --- | --- |
| Routing | orchestration/routing | GoalParser、Router、route validation | LangGraph 节点承载；决策逻辑自建 | 显式 route 与能力校验 | 无外部操作 direct 禁止规则 | `tests/unit/test_routing.py` |
| Planning | orchestration/planning + agents/harness | GlobalPlanner；Worker local todo | Deep Agents/LangChain `TodoListMiddleware` | Todo middleware 显式加入，GlobalPlan 总是服务端校验 | 全局 DAG 与局部 Todo 分离 | `tests/unit/test_plans.py`、`tests/integration/test_offline_runs.py` |
| Replanning | orchestration/planning/revision | `bounded_revision`、plan history | LangGraph checkpoint 保存状态 | `EA_MAX_REPLANS=2` | 已完成任务不自动重做，新版本保留旧计划 | `tests/unit/test_plans.py` |
| Tool Use | tools | ToolRegistry、ToolGateway | LangChain tool schema；Deep Agents 可承载工具循环 | timeout、结果大小、作用域、预算显式 | UNKNOWN/幂等、SSRF、脱敏和操作 ID | `tests/unit/test_tools.py` |
| Delegation | agents/delegation | DelegationService、child context | Deep Agents `create_deep_agent`/子 Agent 概念 | 角色、深度、Agent 总量、工具范围显式 | 权限只能收敛，父子结果用 AgentResult 交接 | `tests/unit/test_governance.py`、`tests/integration/test_offline_runs.py` |
| Context Management | contracts/context、tools/workspace | IdentityContext、ExecutionContext、Workspace | Deep Agents backend 是局部工作区能力 | Run/租户目录显式隔离 | 服务端身份不信任模型输入；符号链接/路径穿越拒绝 | `tests/security/test_tenant_isolation.py`、`tests/unit/test_tools.py` |
| Recovery | runtime、infrastructure/checkpoints、dispatch | lifecycle、lease、checkpoint factory | LangGraph checkpoint/interrupt | PostgreSQL 生产、SQLite 开发；租约显式 | 应用 DB 与 checkpoint 对账，副作用不承诺 exactly-once | `tests/recovery/test_lease.py` |

这些测试验证指定行为和边界，不等价于覆盖所有模型意图、提示注入或生产容量。

