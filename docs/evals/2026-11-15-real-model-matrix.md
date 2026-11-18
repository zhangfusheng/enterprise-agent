# 2026-11-15 真实模型评测

## 范围

评测在显式 `EA_REAL_TESTS=1` 下执行。默认绑定模型运行完整 Parser/Planner
矩阵；其他已配置模型运行跨供应商 smoke matrix。凭证内容未进入日志或测试参数。

| Provider/Profile | Model | Parser | Planner | 结论 |
| --- | --- | --- | --- | --- |
| DashScope / qwen-default | qwen3.8-max | 7/7 | 7/7 | 默认 Router/Planner 准入通过 |
| DeepSeek / deepseek-default | deepseek-v4-flash | 1/1 | 1/1 | 跨供应商 smoke 通过；尚未运行完整 14 场景 |
| Doubao / doubao-default | doubao-seed-2-1-pro-260915 | 0/1，fallback | 1/1 | Planner smoke 通过；Router 暂不满足稳定结构化输出准入 |

另外，默认 Qwen + Tavily 的真实 Research Worker 链路为 1/1：研究计划、
ToolGateway、真实搜索、来源质量门禁和 durable operation 记录均通过。

## Qwen 完整矩阵

Parser 覆盖：

- 短文本规则路径；
- 否定能力；
- 多目标分离；
- material requirement 从 missing 到 satisfied；
- 最新变化触发搜索；
- JSON deliverable；
- 调用方约束不被注入文本覆盖。

Planner 覆盖：

- 单能力模板路径；
- multi Research→Analysis DAG；
- 多目标覆盖；
- 复合能力；
- 未知角色注入防护；
- 增量 replan 保留已完成任务；
- 显式 Plan provenance。

## Doubao Router 发现

同一 `goal-candidate-v2` schema 和“请分析我的资料”请求在独立诊断调用中可以成功，
但跨模型矩阵连续两次运行均出现两次 schema retry 后 fallback。错误被归类为
`CandidateSchemaError`，fallback 行为正确，Planner structured output 正常。

因此判断为 Router 结构化输出高波动，而不是固定不支持 schema。当前不放宽 Candidate
领域约束，也不通过重复调用刷绿。Doubao 可用于本次 Planner smoke，但在增加 provider-specific
结构化适配或达到稳定性样本门槛前，不应绑定到生产 Router。

## 测试装配修正

Research 真实测试最初暴露两项 fixture 问题：

1. 测试上下文没有传递 `external_network_allowed`，被 ToolGateway 正确拒绝；
2. production 设置下的新临时 SQLite 不自动建表，durable operation 写入失败。

修正后 fixture 显式继承网络许可、执行测试数据库初始化，并将 SQLite 研究并发限制为 1。
这些修改不改变生产“部署先迁移、进程不自动建表”的规则，也不降低来源质量门禁。

## 后续准入条件

- DeepSeek：运行与 Qwen 相同的完整 Parser/Planner 矩阵后再提升准入等级。
- Doubao Router：增加至少 20 次结构化稳定性样本，记录 schema rejection/fallback 比率；
  如仍不稳定，实施 provider adapter，而不是放宽领域模型。
- PostgreSQL：真实并发 Research 验证应在 PostgreSQL 环境单独执行；SQLite 测试不代表生产并发能力。
