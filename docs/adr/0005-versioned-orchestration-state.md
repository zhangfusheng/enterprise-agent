# ADR 0005：持久化 Orchestration State 显式版本化

状态：accepted

## 背景

Run 可以跨进程、部署版本和 interrupt 恢复。未经版本标识的 JSON 状态无法区分历史字段缺失、数据损坏和未来应用写入，直接按当前结构读取会产生隐式迁移和不可预测恢复。

## 决策

顶层持久化状态包含整数 `state_version`。当前版本为 1。Repository 创建新 Run 时写入当前版本；状态进入执行器前统一经过 `migrate_orchestration_state()`；正常、等待输入和失败结果都写回当前版本。

迁移规则：

- 缺少版本视为 legacy v0；
- v0 → v1 只增加版本字段，不猜测或重写 GoalSpec、requirements、provenance 和用户输入；
- 迁移返回深拷贝且必须幂等；
- 非整数、负数和未来版本失败关闭；
- 每次新增版本必须提供逐版本迁移和测试。

数据库 Run state 与 LangGraph checkpoint 仍遵守 ADR 0002 的双所有权。版本字段描述应用可解释的持久化业务状态，不把两个存储合并为同一事务。

## 替代方案

- 依赖 Pydantic 默认值自动补字段：拒绝，因为这不是可审计迁移，也无法拒绝未来版本。
- 原地修改 Repository 返回对象：拒绝，因为会造成调用方不可见副作用。
- 尝试自动降级未来版本：拒绝，因为旧应用无法知道新字段的安全语义。

## 回滚与演进

应用可以读取所有已实现迁移的旧版本，但不得读取更高版本。部署回滚前必须确认数据库中没有新版本状态，或由新版本提供显式向下迁移工具；运行时不会自动降级。

## 验证

`tests/unit/test_state_schema.py` 和架构 eval 覆盖 legacy、current、非法及未来版本；离线 resume 测试验证版本字段随 interrupt 状态持久化。
