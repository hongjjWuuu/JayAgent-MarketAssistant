# 第四周质量收口执行记录

## 结论

本轮没有通过改低阈值掩盖 BGE 的误答，而是把安全判定、评测语义和 Profile 激活资格收紧为可重放的确定性门禁。

当前结论仍是：

- `hash-v1:tenant_demo` 保持 active；
- `bge-m3-v1` 保持 rejected，代码和激活事务都禁止重新激活；
- `bge-m3-v2` 是唯一允许继续校准的新实验 Profile；
- 第五周扩展暂停；
- 在真实质量与性能命令通过前，第四周状态为“质量优化中”。

## 已落地内容

### 1. Gate v3

`QueryAnalysis` 新增业务领域、业务锚点、请求作用域、显式 ACL 上下文及排除意图。Gate 按以下顺序执行：

```text
索引兼容性
→ Prompt 注入和业务范围
→ 显式 ACL 冲突
→ 缺少上下文
→ 候选与最低 RRF
→ semantic-only 分数和 top1/top2 margin
→ 限定政策 ACL 证据
→ 单一政策族事实完整度
→ 有效政策冲突
→ answer
```

required facts 不再跨无关政策拼接。回答引用也只从首个证据政策族中选择。

快照新增 `domain_intent`、业务锚点、semantic-only、top1/top2 分差、按政策族事实覆盖、scope match、Gate rule ID、Embedding/lexical/vector/RRF 分段时延。

### 2. 数据集与失败归因

- `week4_gold/v3` 保持冻结不变；
- 新增 `week4_hard_negative/v1` 60 条；
- 案例 001–040 固定为 calibration；
- 案例 041–060 固定为 holdout；
- 运行时不得随机重分；
- 新增失败报告器，按七类原因输出完整候选、分数、ACL、事实覆盖和 Gate rule。

### 3. Profile 不可变门禁

迁移 `20261028_0009` 为 Profile 增加：

- `gate_config`；
- `quality_report`；
- `rejected_reason`；
- `parent_profile_id`；
- `calibration_run_id`；
- `holdout_run_id`。

校准使用固定阈值网格，先保证 calibration False Answer 为 0，再最大化 Answerability Recall，同分取更严格阈值。配置冻结后只在 holdout 验收，不再调参。

激活必须同时满足：索引覆盖率 100%、Gold v3 passed、hard-negative holdout passed、calibration 已绑定、版本完全一致、安全指标为 0。

### 4. 检索与性能

PostgreSQL Repository 将 lexical top 30 与 vector top 30 拆为独立 SQL，再执行 RRF。两路 SQL 和 RRF 分别计时。

迁移增加 Hash 1536 维 HNSW 索引和仅限 `bge-m3-v2` 的 1024 维 cast 部分 HNSW 索引。性能脚本固定执行 10,000 Chunk、50 次查询，前 5 次预热不计入稳态 P95，并保存 `EXPLAIN (ANALYZE, BUFFERS)`。

## 验证入口

```powershell
& .\scripts\analyze_week4_failures.ps1 -Profile bge-m3-v1
& .\scripts\verify_week4_quality_v3.ps1 -KeepServicesRunning
& .\scripts\verify_retrieval_performance.ps1 -Chunks 10000 -Queries 50
```

`verify_week4_quality_v3.ps1` 会启动 PostgreSQL、Redis 和 Infinity，迁移到 `0009`，导入冻结数据，重建 BGE v2，执行 calibration、Gold、holdout，然后由后端事务决定能否激活。

## 通过标准

```text
False Answer = 0
旧版本、跨租户、ACL 泄漏 = 0
Policy / Version Accuracy = 100%
Chunk Accuracy >= 95%
Recall@5 >= 90%
MRR >= 0.80
Answerability Recall >= 90%
Unanswerable Block Rate = 100%
10,000 Chunk DB-only steady P95 < 450ms
BGE warmed end-to-end P95 < 3s
```

任一门禁失败时，脚本失败、Profile 保持或转为 rejected、Hash 保持 active，不修改业务数据和历史评测。

## 2026-10-27 实测结果

- Alembic `0008 → 0009`：通过；
- PostgreSQL 拆分检索集成测试：通过；
- 10,000 Chunk 冷查询：344.37ms；
- 45 次稳态查询 P95：345.30ms，通过 450ms 门槛；
- BGE v2 初次 calibration：未通过，19 个失败；
- 失败构成：8 个范围漏判、9 个不足证据标注过宽、2 个冲突决策差异；
- 激活结果：未激活，Hash 保持 active；
- 修复动作：扩充确定性 unsupported-domain 识别，并收紧新环境 hard-negative 的不足证据问题；
- 历史处理：失败 Profile 和 Run 保留，不回写为 passed。
