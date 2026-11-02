# enterprise-agent 第一版架构

## 边界

仓库是模块化单体，但 API 和 Worker 是两个进程。API 只负责身份上下文、校验、写入 Run/WorkItem/事件并返回；Worker 通过数据库任务表领取 Run，创建 LangGraph 入口图并执行。应用数据库是业务事实来源，LangGraph checkpoint 是图状态快照和恢复游标；两者不是同一事务，Worker 在图完成后对账写回应用状态，失败时可依据 checkpoint 和租约重新领取。

```
HTTP -> application use case -> SQLAlchemy repositories -> PostgreSQL/SQLite
                                      |
                                      v
Worker -> LangGraph parse/route/plan/execute/aggregate
             |                 |
             v                 v
       Deep Agents harness   ToolGateway -> governed tools
```

## 四种职责

| 组件 | 职责 | 第一版实现 |
| --- | --- | --- |
| Router | 根据 GoalSpec 的能力、实时/外部操作和交付要求选择 direct/single/multi | `orchestration/routing`；不是关键词演示路由，也不使用 confidence 概率 |
| Planner | 生成并校验全局任务 DAG，有限版本修订 | `GlobalPlanner` + `validate_plan` |
| Supervisor | 组织依赖任务、处理 Worker 结果、委派子 Agent、汇总验收 | LangGraph `execute`/`aggregate` 节点和 `Supervisor` |
| Executor | 确定性地选 ready task，按依赖顺序执行，记录结果 | `ready_tasks` 和 `execute_run` |

Worker 内部的 Local Planner 由 Deep Agents 的显式 `TodoListMiddleware` 提供；它是局部进度，不改变全局 DAG 的权威状态。

## 状态和恢复

Run 状态迁移由 `runtime/lifecycle.py` 校验。任务领取使用 `work_items` 的租约、Worker ID 和过期时间；提交终态前再次检查租约，因此失去执行权的旧 Worker 不能提交有效状态。取消先持久化，再由 Worker 在节点/工具边界检查并传播给子上下文。

LangGraph 以 `run_id` 作为 `thread_id`。SQLite 开发环境使用 `SqliteSaver`，PostgreSQL 部署使用 `PostgresSaver`。checkpoint 不等于外部副作用 exactly-once：报告保存依赖稳定 operation ID，超时的副作用工具返回 UNKNOWN，不能盲目重试；数据库状态与 checkpoint 对账是应用层责任。

Deep Agents 0.7.28 的 `create_deep_agent` 叠加在 LangChain `create_agent`/LangGraph 之上。本项目通过 `DeepAgentsHarness` 组装局部文件后端、显式 Todo middleware 和可配置 subagents；应用工具仍必须经过 ToolGateway。当前默认 general-purpose 子 Agent 是隔离、单次返回的上下文，不把 `max_depth` 误称为递归委派能力。真正的受控 Worker→Subagent 路径由 `DelegationService` 创建 child context，检查深度/总量/角色/工具收敛后调用 Analysis Agent；因此深度是服务端实际执行约束。

官方能力核对：

- [Deep Agents subagents](https://docs.langchain.com/oss/python/deepagents/subagents)：支持字典式自定义子 Agent 与 `CompiledSubAgent`，同步 `task` 返回一次结果；
- [Deep Agents tools](https://docs.langchain.com/oss/python/deepagents/tools)：`write_todos` 通过 `TodoListMiddleware` 显式加入；内置文件工具由 backend/permissions 控制；
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)：checkpoint 是 thread-scoped 图状态；
- [LangGraph subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)：子图有 per-invocation、per-thread、stateless 模式并携带 namespace；
- [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)：interrupt 使节点重跑，前置副作用必须幂等或放在 interrupt 之后。

第一版当前使用 per-invocation 语义的子 Agent 组合；父图 checkpoint 承担 Run 恢复。复杂多轮子 Agent 记忆和 Store 不是本版本范围。

## 网络和不可信内容

`web_fetch` 只接受 HTTP(S)，预解析 DNS 并拒绝 loopback/private/link-local/reserved/metadata 地址，逐跳校验重定向、内容类型、大小和超时。HTTP 客户端的 DNS 预检查与实际连接之间仍存在理论 TOCTOU/DNS rebinding 窗口；第一版没有自定义连接器把解析 IP 与连接原子绑定，生产网络层应再加 egress proxy 或连接级 IP pinning。网页正文永远只进入资料字段，不进入授权、配置或系统提示词。

