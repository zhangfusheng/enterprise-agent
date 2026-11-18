# 架构决策记录

| ADR | 状态 | 决策 |
| --- | --- | --- |
| [0001](0001-hybrid-orchestration.md) | accepted | 混合 LangGraph + Deep Agents 编排 |
| [0002](0002-state-ownership.md) | accepted | 应用数据库与 checkpoint 双状态所有权 |
| [0003](0003-untrusted-candidate-compilation.md) | accepted | 不可信 Candidate 必须经服务端 Compiler 形成 GoalSpec |
| [0004](0004-structured-input-requirements.md) | accepted | 输入需求使用结构化 Requirement 与确定性 Resolver |
| [0005](0005-versioned-orchestration-state.md) | accepted | 持久化运行状态必须显式版本化和迁移 |
| [0006](0006-explicit-provenance.md) | accepted | Parser/Planner provenance 必须与结果原子返回 |

ADR 记录已经实施且通过离线架构矩阵验证的决策。变更决策时新增 ADR
并标记被替代关系，不直接重写历史背景。
