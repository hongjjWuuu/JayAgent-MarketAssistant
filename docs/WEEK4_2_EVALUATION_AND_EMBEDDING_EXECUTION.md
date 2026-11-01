# Week 4.2 评测语义与 BGE-M3 对照实施记录

## 结论

本轮没有重写 Week 4 RAG，而是修正“评测看似通过但语义不够严格”的问题，并为 Hash/BGE 非破坏性对照建立基础设施。

代码与真实环境状态：Week 4.2 门禁通过；Infinity/BGE-M3 对照已执行，但未达到安全激活门槛。

## 已实现

- 迁移 `20261027_0006` 新增稳定 `policy_id`；同租户、同 scope 的版本共享政策族 ID。
- `week4_gold/v2` 永久保留，`seed_week42_gold.py` 从 100 条 v2 派生 v3。
- Policy、Version、Chunk 引用准确率独立计算，不能再互相代替。
- forbidden policy/version/chunk、跨租户、ACL 和非法状态检查应用到所有案例。
- ACL 语义改为“政策无限制，或请求明确给出且匹配”；缺失上下文不会放入候选。
- 迁移 `20261027_0007` 新增 `retrieval_profile` 与 `policy_chunk_embedding`，旧列继续保留用于回滚。
- Profile 采用 `building → ready → active/rejected/archived`，每租户数据库级只允许一个 active。
- 激活必须绑定本 Profile 的 `week4_gold/v3 + metric_schema v3 + passed` Run。
- Docker Compose 增加 Infinity CPU + `BAAI/bge-m3`，模型缓存使用独立持久卷。
- `run_embedding_experiment.py` 运行同集 Hash/BGE 对照；不通过时标记 BGE rejected，不改变 Hash 业务数据。

## 验证入口

```powershell
& .\scripts\verify_week42.ps1 -KeepServicesRunning
& .\scripts\verify_embedding_experiment.ps1 -Profile bge-m3-v1
```

第二条命令首次运行需要下载数 GB 模型。只有报告满足安全指标为零、Recall/MRR/三层引用门槛、Recall@1 相对下降不超过 2 个百分点和 P95 门槛时，脚本才允许激活 BGE。

## 回滚边界

- `0006–0007` 只追加字段和表，不删除旧 Chunk 向量。
- BGE 构建或评测失败不会覆盖 Hash。
- 线上选择 active Profile；若没有可用非 Hash Profile，PolicyService 继续使用旧 Hash 路径。
- 不通过降低 Answerability Gate 阈值来换取召回率。

## 2026-10-26 实测结果

真实 PostgreSQL 已从 `20261026_0005` 升级到 `20261027_0008`。100 条 v3 Hash 门禁结果：

| 指标 | Hash |
| --- | ---: |
| Recall@1 / Recall@5 | 76.56% / 100% |
| MRR | 0.862 |
| Policy / Version / Chunk Accuracy | 100% / 100% / 100% |
| False Answer / 旧版本 / 跨租户 / ACL 泄漏 | 0 / 0 / 0 / 0 |
| P95 | 60ms |

Infinity `0.0.77-cpu` 已下载、预热并通过健康检查，BGE-M3 返回归一化的 1024 维向量。相同 v3 数据集对照：

| 指标 | Hash | BGE-M3 |
| --- | ---: | ---: |
| Recall@1 | 76.56% | 75.00% |
| Recall@5 | 100% | 100% |
| MRR | 0.862 | 0.867 |
| 三层引用准确率 | 100% | 100% |
| False Answer | 0% | 22.22% |
| 不可回答拦截率 | 100% | 77.78% |
| P95（含本地 Embedding） | 56ms | 235ms |

BGE 的召回和延迟达标，但安全 Gate 回归不达标。系统按设计将 `bge-m3-v1` 标记为 `rejected`，没有降低 Gate 阈值；`hash-v1:tenant_demo` 保持 `active`。

## 后续优化方向

- 分析 8 条 BGE false-answer 案例，重点检查语义召回引入的“相似但不可作答”证据。
- 为负向/越界问题增加检索前分类和证据矛盾特征，不直接收紧全局召回。
- 将数据库检索耗时与 Embedding RPC 耗时分开记录。
- 10,000 Chunk 基线存在冷/热缓存抖动：热复跑低于 500ms，完整回归曾记录 502.8ms；在查询计划或索引稳定前，不把该性能门槛标为完成。
- 修复后必须重新创建新 Profile 或重跑同一 Profile 的 v3 门禁，禁止人工强制激活 rejected Profile。

DeepSeek 不属于本阶段，真实模型 Smoke 状态记录在 Week 5 文档中。
