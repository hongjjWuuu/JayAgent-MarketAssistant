# Week 4.1 实施记录：RAG 质量闭环

> 实施日期：2026-10-26  
> Alembic revision：`20261026_0005`  
> Metric schema：`v2`  
> Gate / Retriever：`v2` / `hybrid-rrf-v2`

## 1. 本轮结论

第四周遗留的评测硬编码已删除。评测结果现在由逐案例候选、引用、决策、原因、泄漏标记和延迟动态聚合；安全指标非零或质量指标低于门槛时，Run 明确标记为 `failed`。

`week4_gold/v1` 保留为冻结历史基线；`week4_gold/v2` 固定为 100 条，覆盖正常回答、改写、精确事实、追问、无证据、旧版本、ACL、跨租户、提示注入和冲突政策。

## 2. 关键变化

- 新增 `evaluation_dataset`，数据集支持 `draft → frozen`，冻结后导入返回 `409 DATASET_FROZEN`。
- Retrieval Snapshot 增加 Embedding 维度、规范化版本和诊断信息。
- SQL 候选阶段强制匹配 Embedding 模型与维度；不兼容时 Gate 返回 `INDEX_MODEL_MISMATCH`。
- Query Analyzer 执行 NFKC、中文数字/单位规范化、必要事实与上下文提取。
- Gate v2 固定输出 `answer / clarify / handoff`，覆盖缺上下文、缺事实、低覆盖、冲突、提示注入和索引不兼容。
- RRF 后按内容哈希去重，并限制单政策与单 scope 的候选数量。
- 引用 Policy/Version/Chunk 正确率独立计算，不再复用 Recall@5。
- 旧版本、租户、ACL 与 false-answer 指标均来自逐案例真实结果，不再写死为 0。
- Compare API 只比较相同 dataset/version 与 metric schema 的 Run。
- PostgreSQL 中文检索在原生 FTS 之外增加 NFKC 后的双字词覆盖率，并把 Chunk 标题路径纳入词法排序；这使中文短问句不再完全依赖 Hash Embedding 的偶然相似度。

## 3. v2 数据集分布

| 类型 | 数量 |
| --- | ---: |
| 直接回答 | 25 |
| 口语改写 | 15 |
| 金额/时限/资格事实 | 10 |
| 缺上下文 | 10 |
| 无证据/超范围 | 10 |
| 旧版本诱导 | 8 |
| ACL 边界 | 8 |
| 跨租户 | 6 |
| 提示注入 | 4 |
| 冲突政策 | 4 |

## 4. 质量门禁

- Recall@1/3/5：`≥ 0.75 / 0.85 / 0.90`
- MRR：`≥ 0.80`
- Citation Policy/Version：`100%`
- Citation Chunk：`≥ 95%`
- Answerability Recall：`≥ 90%`
- 不可回答阻断率：`≥ 95%`
- Reason Code 准确率：`≥ 90%`
- False answer、旧版本、跨租户、ACL 泄漏：全部为 `0`
- P95：`< 500 ms`

## 5. 复验

### 5.1 2026-10-26 实测结果

| 指标 | PostgreSQL Gold Set v2 实测 | 门槛 | 结果 |
| --- | ---: | ---: | --- |
| 逐案例通过 | 100 / 100 | 100 条完成 | 通过 |
| Recall@1 | 76.56% | ≥ 75% | 通过 |
| Recall@3 | 100% | ≥ 85% | 通过 |
| Recall@5 | 100% | ≥ 90% | 通过 |
| MRR | 0.862 | ≥ 0.80 | 通过 |
| Citation Policy / Version / Chunk | 100% / 100% / 100% | 100% / 100% / ≥95% | 通过 |
| Answerability Precision / Recall | 100% / 100% | Recall ≥90% | 通过 |
| Clarify / Handoff 准确率 | 100% / 100% | — | 通过 |
| 不可回答阻断率 | 100% | ≥95% | 通过 |
| Reason Code 准确率 | 100% | ≥90% | 通过 |
| False Answer / 旧版本 / 跨租户 / ACL 泄漏 | 0 / 0 / 0 / 0 | 必须为 0 | 通过 |
| 评测查询 P95 | 54 ms | <500 ms | 通过 |
| 10,000 Chunk PostgreSQL P95 | <500 ms | <500 ms | 通过 |

回归测试结果：后端非集成测试 `62 passed`；PostgreSQL RAG、Repository、Worker 集成测试 `16 passed`；真实 Keycloak OIDC 测试 `5 passed`；前端测试 `2 passed` 且生产构建成功。

### 5.2 一键复验

```powershell
Set-Location D:\JayAgent\JayAgent-MarketAssistant
Set-ExecutionPolicy -Scope Process Bypass
& .\scripts\verify_week4.ps1 -KeepServicesRunning
```

脚本升级到 `20261026_0005`，导入并冻结 v1/v2，运行真实 PostgreSQL 质量门禁、后端回归、Keycloak、前端测试和生产构建。任何质量门禁失败都会使脚本以非零状态退出。

## 6. 保留边界

- 默认仍使用确定性 Hash Embedding；真实模型切换后必须重建索引并重跑 v2。
- 未加入 PDF/OCR、附件解析、外部知识抓取或 LLM 自由生成。
- PostgreSQL 原生 FTS 配置仍为 `simple`，当前以确定性中文双字词覆盖率补足；若后续引入专用中文分词器，必须升级 Retriever 版本并使用同一冻结 v2 做回归比较。
