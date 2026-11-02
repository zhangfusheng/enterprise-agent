# ADR 0001：混合 LangGraph + Deep Agents 编排

## 决策

用 LangGraph 作为入口图、恢复和状态边界；用 Deep Agents 作为 Worker 的局部 harness；用自建 Planner/Supervisor/Executor 处理全局 DAG 和治理。

## 原因

全局依赖、租约、预算和租户边界需要服务端确定性校验，不能让模型 Todo 充当权威状态。Worker 内部的长任务规划、文件能力和子任务隔离适合 Deep Agents。这样可替换模型、Worker harness 或存储而不改变核心契约。

## 限制

Deep Agents 默认 general-purpose 子 Agent 的单次隔离返回不是递归工作流；受控嵌套由 `DelegationService` 自建，默认深度 2。checkpoint 和外部副作用不构成 exactly-once。真实认证、容量、审计和 HA 不在第一版。

