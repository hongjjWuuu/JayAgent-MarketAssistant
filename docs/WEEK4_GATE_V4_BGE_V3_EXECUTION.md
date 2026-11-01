# 第四周继续推进执行记录：Gate v4 与 BGE-M3 v3

更新时间：2026-10-28

## 当前结论

本文保留 `bge-m3-v3` 的历史失败记录。第四周后续经过 v4–v10 的不可变 Profile 迭代，已由 `bge-m3-v10` 完成质量验收并受控激活；最终证据见 `WEEK4_FINAL_QUALITY_ACCEPTANCE.md`。

固定资产状态：

- `hash-v1:tenant_demo` 保持 active，作为可回滚基线；
- `bge-m3-v1`、`bge-m3-v2` 为不可变 rejected Profile；
- `week4_gold/v3`、`week4_hard_negative/v1` 保持冻结，只用于历史复盘；
- 新实验只使用 `bge-m3-v3`、`week4_gate_calibration/v1` 和 `week4_hard_negative/v2`。

## 已实现内容

### Query Analysis / Gate v4

- 领域判断同时保存业务对象、请求操作、政策 scope、按 scope 绑定的 required facts、显式 ACL 上下文和 unsupported intent；
- “写订单系统代码”“物流股票分析”等业务词包装的非业务任务优先拦截；
- unsupported、Prompt 注入和显式 ACL 冲突在调用 Embedding/检索前阻断；
- query scope 与候选 scope 不兼容时不得回答；
- semantic-only 与弱 lexical 命中都执行 margin 检查；
- required facts 必须由单一政策族覆盖，不允许跨无关政策拼接；
- 冲突比较限制在兼容 scope 与同一政策族；
- Snapshot 保存对象、操作、query/candidate scope、anchor 质量、score 类型、事实覆盖、冲突组、ACL 拒绝原因及最终 Gate Rule。

### 评测与 Profile 生命周期

- 新增平衡校准集 `week4_gate_calibration/v1` 80 条：30 个 answer 正例和 50 个 clarify/handoff 负例；
- 新增未参与调参的安全 holdout `week4_hard_negative/v2` 60 条；
- 校准只能读取平衡校准集，Gold 与 holdout 不参与阈值选择；
- Profile 冻结保存 calibration dataset、validation datasets、Gate 配置哈希；
- Gold 与 holdout Run 必须绑定相同 Profile、模型、Retriever、Gate、Normalizer 和配置哈希；
- `activation-readiness` 由后端计算，前端不能提交阈值绕过门禁。

### 迁移与索引

`20261029_0010` 增加：

- Profile 的 `calibration_dataset`、`validation_datasets`、`config_hash`；
- Evaluation Run 的 Query Analyzer、scope match 和阈值网格版本；
- 只服务 `bge-m3-v3` 的 1024 维 HNSW 部分索引。

由于本机在 Evaluation Run 三个版本字段加入 `0010` 源码之前已经执行过该 revision，追加兼容迁移 `20261029_0011`，使用 `ADD COLUMN IF NOT EXISTS` 补齐旧安装；新数据库重复执行时不会产生重复列。当前 Alembic Head 为 `0011`。

## 执行入口

```powershell
& .\scripts\review_week4_failures.ps1 -Profile bge-m3-v2
& .\scripts\verify_week4_datasets.ps1
& .\scripts\verify_week4_profile.ps1 -Profile bge-m3-v3
& .\scripts\verify_retrieval_performance.ps1 -Chunks 10000 -Queries 50
```

只有准备正式激活时才执行：

```powershell
& .\scripts\verify_week4_profile.ps1 -Profile bge-m3-v3 -Activate
```

任一质量门禁失败都会拒绝 v3 并保留 Hash active；不得修改 rejected Profile 继续调参。

## 2026-10-28 实测结果

- `0009 → 0010 → 0011` 真实 PostgreSQL 迁移：通过；
- calibration 80 条、hard-negative v2 60 条冻结与结构自检：通过；
- `bge-m3-v3` published Chunk 覆盖：14/14，100%；
- calibration：Recall@5 100%、MRR 0.8778、Answerability Recall 100%，但 False Answer 14%、Unanswerable Block Rate 86%，未通过；
- 安全数据：旧版本、跨租户、ACL 泄漏均为 0；
- 失败集中于 5 个注入表达漏识别和 2 个冲突表达未被前置规则识别；
- v3 已按不可变规则转为 rejected；Gold 与 hard-negative 正式 Run 没有继续执行；
- `hash-v1:tenant_demo` 经数据库检查仍为 active；
- Gate v4 增量测试 28 项通过，前端 Vitest 2 项与生产构建通过。
- PostgreSQL/Worker/RAG 集成回归 17 项通过（25 项依赖额外外部环境的用例跳过）；
- 10,000 Chunk 性能复测：冷查询 353.25ms、稳态 P95 360.25ms，通过 800ms/450ms 门槛。

实测后已补强直接“忽略权限/确认/ACL”注入识别和用户显式声明的同 scope 金额/期限冲突识别，但不会重新运行或修改 rejected 的 v3。下一轮必须创建新的 Profile 和新的、经过更严格语义审定的数据集版本。

## 未完成的真实证据

- 新 Profile 的 Calibration、Gold、hard-negative 三组真实 Run；
- 10,000 Chunk 性能复测；
- 前三周、Worker、OIDC、前端与 E2E 全量回归；
- 新 Profile 受控激活及 Hash 回滚演练。

上述缺口已由后续 v10 验收闭环补齐。本文不改写 v3 的失败事实，仅作为问题演进和不可变实验记录。
