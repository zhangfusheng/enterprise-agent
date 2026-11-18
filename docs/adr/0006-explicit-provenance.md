# ADR 0006：Parser/Planner Provenance 与结果原子返回

状态：accepted

## 背景

早期实现用 ContextVar 保存“最近一次”Parser 或 Planner trace，再由 Graph 或执行器读取。该方式把业务数据绑定到隐式调用上下文，在连续请求、嵌套调用、异常和并发任务中可能读取到错误来源；结果与来源也无法保证来自同一次调用。

## 决策

Parser 返回 `ParsedRequest(spec, provenance)`；Planner 返回 `PlanResult(plan, provenance)`。Graph 将 `parse_provenance` 和 `plan_provenance` 写入状态，日志、版本解析和持久化只读取这些显式值。

Provenance 至少记录策略名称、模型版本、Prompt 版本及适用的重试/fallback 信息。业务 routing/planning 模块不得使用 ContextVar 或共享实例属性传递“最后一次调用”状态。

旧 Parser 只有 `parse()` 时，边界适配为 `parser=legacy`；旧 Planner 只有 `create()` 时，边界适配为 `planner=legacy`。适配器不重建或猜测模型版本。

## 替代方案

- 调用后立即读取 ContextVar：拒绝，因为异步或嵌套调用仍可能污染。
- 在共享 Parser/Planner 实例上保存 `last_trace`：拒绝，因为并发请求存在数据竞争。
- 只写日志而不持久化来源：拒绝，因为 resume、审计和版本归因需要与状态一起恢复。

## 兼容与退场

`parse()` 与 `create()` 仍作为只返回领域结果的兼容入口；生产 Graph 使用原子结果接口。Legacy adapter 的使用应通过 resolved versions 可见。所有受控 Parser/Planner 实现迁移完成并经过一个兼容窗口后，可单独移除 legacy adapter。

## 验证

架构 eval 验证连续 LLM/fallback 结果不会互相改写；Graph、trace replay 和执行器测试验证 provenance 随计划、interrupt 和最终状态传递。routing/planning 目录禁止残留业务 `parser_trace`、`plan_trace` 或 ContextVar。
