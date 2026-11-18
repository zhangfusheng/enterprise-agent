# ADR 0004：结构化 Input Requirement 与确定性 Resolver

状态：accepted

## 背景

单一 `missing_information: list[str]` 只能呈现提示文本，不能表达需求类型、关联目标、最少数量、满足来源或等待前后的稳定身份。若让模型直接决定需求是否满足，调用方已经提供的材料可能被忽略，也可能被提示注入伪造成已满足。

## 决策

`GoalSpec.input_requirements` 是输入依赖的权威结构。当前支持：

- `material`
- `comparison_subject`
- `calculation_operand`
- `time_range`
- `template`
- `destination`
- `clarification`

LLM 或规则只负责提出 requirement 意图；Compiler 形成合法领域对象；Resolver 使用原始请求和调用方 `InputMaterial` 重新计算 `status` 与 `satisfied_by`。Resolver 不信任旧状态，不修改输入，结果必须确定、幂等、顺序稳定。

```text
Candidate/Rules -> InputRequirement(intent)
                         |
request + materials ----+--> Resolver
                                  |
                                  +--> resolved requirements
                                  +--> required missing descriptions
```

`missing_information` 保留为用户界面和 interrupt 的派生投影，只包含 `required=true` 且 `status=missing` 的 description，并按 requirement 顺序去重。它不是需求事实的唯一存储。

## 所有权

| 字段 | 所有者 |
| --- | --- |
| kind、description、goal_ids、required、minimum_items | Candidate/规则提出，Compiler 校验 |
| source | Compiler/规则适配器 |
| status、satisfied_by | Resolver |
| inputs、constraints | 调用方/服务端 |

## 替代方案

- 继续只保存提示字符串：拒绝，因为无法可靠 resume、评测和审计。
- 由模型输出满足状态：拒绝，因为模型看见的是不可信文本视图，不拥有调用方事实。
- 发现材料后删除 requirement：拒绝，因为等待前后领域身份会变化。

## 兼容与限制

规则 Parser 与 LLM legacy adapter 共用调用方资料推导函数。旧候选没有 requirements 时仍可运行，但生成的 requirement 明确标记来源。当前 Resolver 使用保守的确定性匹配器；扩大自然语言覆盖必须先增加数据集场景，不能用不可解释的宽松猜测替换。

## 验证

`evals/datasets/requirements.yaml` 为七类需求各提供 missing/satisfied 场景。离线基线要求 `requirement_accuracy=1.000`；interrupt/resume 集成测试验证 requirement 身份和状态持久化。
