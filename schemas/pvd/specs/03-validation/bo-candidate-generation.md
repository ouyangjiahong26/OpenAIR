# 贝叶斯模型推荐候选参数（PVD 工艺参数优化）

## 用户价值

按冻结的模型档案（模型配置、基线数据集与分层批次策略），每次执行现场训练高斯过程代理模型，以置信上界采集函数为当前实验体系确定性推荐候选工艺参数组：首轮基于基线数据集训练，反馈轮叠加同体系真实实验反馈重新训练。本职责是候选生成器，不拥有最终推荐权威，产物必须经可行性核验后才能进入本轮推荐。

## 机制

- 输入是当前 ScientificState 事实（实验体系上下文 + 实验历史）；技能内部解析同体系上下文并做门禁；
- 仅 `executed 且 valid` 的真实执行与 `final 且 valid` 的目标指标测量进入训练观测；跨体系历史、计划值、推荐值永不混入；
- 体系与模型档案定义域不符时如实返回"不适用"（空候选、带诊断），不阻塞下游。

## Input

- `scientific_state_facts`

## Output

- `bo_candidates`

## Acceptance

- `same_system_gate_applied_before_training`
- `base_dataset_and_profile_integrity_verified`
- `only_real_executed_valid_observations_trained`
- `candidates_carry_prediction_and_uncertainty`
- `batch_diversity_constraints_enforced`
- `not_applicable_reported_when_system_mismatched`
- `candidates_are_not_executed_facts`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: deterministic
```
