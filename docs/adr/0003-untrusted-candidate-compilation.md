# ADR 0003：不可信 Candidate 经服务端 Compiler 形成 GoalSpec

状态：accepted

## 背景

路由模型能够识别自然语言目标，但不能成为调用方材料、约束、能力目录或执行状态的事实来源。直接把模型结构反序列化为 `GoalSpec` 会允许模型伪造约束、选择未知能力，或在缺少材料时删除目标。模型供应商还可能产生 object/array 等结构漂移。

## 决策

解析分为两个明确阶段：

1. 模型只输出 `GoalIntentCandidate`。Candidate 包含目标、明确交付物、验收条件和输入需求意图。
2. 服务端 Compiler 校验 Candidate，注入调用方材料和约束，过滤能力目录，并生成领域 `GoalSpec`。

Candidate 是不可信提案，不包含调用方拥有的 constraints，也不能设置 requirement 的 `status`、`satisfied_by` 或 `source`。Compiler 对未知能力执行 policy rejection，对无法形成合法 GoalSpec 的情况执行 invariant failure。Schema/semantic 错误允许一次受控重试；policy 错误不重试；服务端 invariant 不降级为规则推断。

数据流为：

```text
request + caller materials
        |
        v
LLM GoalIntentCandidate -- schema/semantic validation
        |
        v
server Compiler -- policy/invariant validation
        |
        v
GoalSpec
```

## 替代方案

- 直接让模型输出 `GoalSpec`：拒绝，因为混合了模型提案与服务端事实。
- 所有请求只使用关键词规则：拒绝，因为不能稳定表达多目标和复杂输入需求。
- 对所有错误静默 fallback：拒绝，因为会掩盖服务端契约缺陷；invariant 必须失败关闭。

## 兼容与演进

Provider schema 的业务版本为 `goal-candidate-v2`，Prompt 为 `goal-parser-v5`。旧模型未输出 `input_requirements` 时由明确的 legacy adapter 补充调用方资料需求；适配结果标记 `source=legacy`。

Legacy adapter 的退场条件是：受支持模型在真实评测矩阵中稳定输出结构化 requirements，并且线上观测窗口内不再出现依赖 adapter 的候选。退场必须单独形成 ADR 或迁移说明。

## 验证

`tests/evals/test_architecture_matrix.py` 覆盖 schema、semantic、policy 和 invariant 四个错误边界；`tests/real/test_llm_parser.py` 是显式启用的真实供应商验证。
