# M4 内部受控试点最终报告

当前状态：**等待真实试点数据，尚未形成 GO/NO-GO。**

## 冻结基线

- Release：`v0.7.0-m4-rc1`
- 数据集：`m4_pilot/v1`
- Retrieval Profile：`bge-m3-v10`
- 数据库 Head：`20261102_0020`
- Git SHA / 镜像摘要 / 模型 / Prompt /政策哈希：由 `pilot_release` 冻结记录提供

## 现场规模

- 参与客户：待执行（目标 3–5）
- 客服：待执行（目标 1–2）
- 连续运行：待执行（目标 3–7 天）
- 会话数：待执行（目标 ≥50）

## 安全与可靠性结论

由关闭接口和 `pilot_metric_snapshot` 自动生成，禁止手工用文字覆盖失败门禁。

## 产品指标

| 指标 | 门槛 | 结果 |
| --- | ---: | ---: |
| 用户任务完成率 | ≥85% | 待执行 |
| 接管上下文完整率 | ≥90% | 待执行 |
| 平均满意度 | ≥4.0/5 | 待执行 |
| 重复询问核心信息 | ≤20% | 待执行 |
| 首次响应中位数 | ≤60 秒 | 待执行 |

## 失败归因

失败按 MODEL、KNOWLEDGE、RULE、TOOL、PERMISSION、INFRASTRUCTURE、PRODUCT、SUPPORT_OPERATION 分类。每个真实失败必须关联脱敏会话/任务、修复版本和新增回归案例。

## 最终决定

PENDING。完成现场执行后，由 `close_m4_pilot.ps1` 生成 JSON 报告和 ADR。
