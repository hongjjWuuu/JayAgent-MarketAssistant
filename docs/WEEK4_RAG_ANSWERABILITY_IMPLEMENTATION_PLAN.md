# 第 4 周实施计划：政策版本、RAG 与 Answerability Gate

> 编排日期：2026-10-25  
> 前置基线：第 1–3 周已验收，Alembic `20261024_0003`  
> 本周目标：把“能检索政策”升级为“检索质量可测、回答证据可重放、证据不足必拒答”

## 一、目标与完成标准

本周不扩展新 Agent，也不追求 LLM 措辞效果。重点证明：系统只能依据当前租户、当前有效且已发布的政策回答，每次回答都能解释“检索了什么、为什么允许回答、引用了哪个版本”。

完成后应具备：

- 草稿政策不会进入在线索引；发布操作原子完成版本切换与索引激活；
- 已发布版本不可原地修改，修改必须产生新草稿版本；
- 政策按 tenant、scope、商品/品类、地域、渠道、客户等级和生效时间过滤；
- PostgreSQL 词法检索与 pgvector 语义检索分别排名，再用真正的 RRF 分数融合；
- Answerability Gate 综合问题类型、证据覆盖、来源有效性、冲突和最小命中数；
- 回答持久化检索快照、Gate 决策、政策版本和 chunk 引用；
- Gold Set 评测真实执行并保存逐案例结果，不再返回硬编码数字；
- 新政策发布前后可比较 Recall@K、MRR、引用正确率与拒答质量；
- 一键脚本在真实 PostgreSQL/pgvector 上完成质量与安全验收。

## 二、当前实现审计

### 2.1 已经具备

- `policy_version`、`policy_document`、`policy_chunk` 数据模型；
- draft、published、superseded 与生效/失效时间；
- 同租户、同 scope 只允许一个 published 版本；
- PostgreSQL FTS + pgvector 两路候选与 RRF 排序骨架；
- 可替换的 Embedding Provider 边界和确定性 Hash Embedding；
- tenant、published、effective period 检索过滤；
- Answerability Gate 与 citation 基础结构；
- `evaluation_case/run/result` 表骨架。

### 2.2 必须修复的缺口

1. `save_policy` 创建草稿时立即切块和生成向量，草稿索引与发布状态边界不清晰。
2. 发布没有显式 `indexing → published` 过程，索引失败时无法阻止半发布。
3. chunk 缺少 ordinal、content hash、标题路径、metadata/ACL 和 embedding model/version。
4. Gate 收到的是 lexical/semantic 原分数最大值，不是真正 RRF 分数，固定阈值没有统一量纲。
5. 中文 `simple` FTS 效果有限；没有查询规范化以及金额、天数等关键实体覆盖判断。
6. Gate 只判断 top-1，没有证据数量、字段覆盖、冲突、问题类型和 scope 一致性。
7. 回复虽保存 citation，但没有独立查询、过滤条件、候选排名和 Gate 版本快照。
8. Evaluation Run 使用固定统计，没有读取 `evaluation_case` 或写入 `evaluation_result`。
9. 管理 API 缺少草稿查询、版本历史、案例导入和逐案例结果。
10. 前端无法查看检索命中、Gate 拒答原因或版本对比。

## 三、固定技术决策

### 3.1 政策生命周期

```text
draft → indexing → published → superseded / expired / archived
                 ↘ index failed → draft + index_error
```

- draft 可以修改，published 内容与 ACL 不可原地修改；
- 发布事务锁定同 tenant/scope，验证时间范围与 ACL，确认索引完成后替换旧版本；
- 旧版本保留历史 chunk 与检索快照；
- 在线检索只接受 `published` 且位于有效期内的版本；
- `effective_to` 到期后检索自动排除，定时任务可补写 expired 供运营展示。

### 3.2 Embedding 边界

- 保留 `HashEmbeddingProvider`，仅用于确定性测试和离线回归；
- 新增 `EmbeddingProvider` Protocol：`embed_documents()`、`embed_query()`、`dimension`、`model_id`；
- 增加可配置的 OpenAI-compatible Embedding 实现，未配置时绝不联网；
- chunk 保存 `embedding_model`、`embedding_dimension`、`content_hash`；
- 模型或维度变化创建 reindex task，不混合比较不同向量空间。

### 3.3 混合检索与 RRF

```text
normalize_query
→ resolve_filters
→ lexical_top_30 + vector_top_30
→ RRF(k=60)
→ ACL/effective filters
→ deduplicate and diversify by policy/scope
→ top_k
```

命中结果必须返回 chunk ID、政策版本、标题路径、lexical rank、semantic rank、RRF score、匹配实体和有效期。Gate 只使用标准化后的 RRF 分数、覆盖度和业务信号，不再混用 cosine similarity 与 ts_rank。

### 3.4 Answerability Gate

Gate 输入包括：问题类型、required facts、有效候选数量、top RRF、实体覆盖、版本/scope 一致性、政策冲突、ACL/有效期以及 retriever/index/gate 版本。

Gate 输出：

```json
{
  "decision": "answer | clarify | handoff",
  "reason_code": "ANSWERABLE | NO_EVIDENCE | LOW_COVERAGE | CONFLICTING_POLICIES | MISSING_CONTEXT | UNSUPPORTED_SCOPE",
  "confidence": 0.0,
  "required_follow_up": ["商品签收日期"],
  "evidence_chunk_ids": ["chunk_..."],
  "gate_version": "v1"
}
```

规则：无证据、过期/未发布、ACL 不匹配不得回答；金额、时限、资格问题缺少对应事实必须 clarify/handoff；有效政策冲突必须 handoff；只有 `answer` 可产生确定性政策答复。

## 四、数据库与迁移设计

新增 Alembic revision，不能修改前三周已执行迁移。

### 4.1 `policy_version` 增加

- `index_status`: pending/indexing/ready/failed；
- `index_error`、`published_by`、`content_hash`、`acl_json`、`updated_at`；
- 唯一键 `(tenant_id, scope, version)`。

### 4.2 `policy_chunk` 增加

- `ordinal`、`title_path` JSON、`metadata` JSON、`content_hash`；
- `embedding_model`、`embedding_dimension`；
- 唯一键 `(tenant_id, policy_document_id, ordinal)`；
- tenant/document、content hash 与向量查询索引。

### 4.3 新增 `retrieval_snapshot`

- tenant、conversation、message、脱敏 query/query hash；
- filters、retriever/embedding/gate 版本；
- candidates/results JSON；
- decision、reason、confidence、latency 和 created_at。

快照保存当次排名和引用摘要，即使政策随后 superseded，历史回答仍可审计。

### 4.4 评测表补强

`evaluation_case` 增加 dataset/version、case_type、tags、active。Expected 声明 expected decision、relevant policy/scope/version/chunk、forbidden versions、required citations/entities。

`evaluation_run` 增加 dataset 和检索配置快照；`evaluation_result` 保存 ranks、decision、latency 与 pass/fail reasons。

## 五、Repository、Service 与 API

Repository 增加草稿更新、版本列表、原子发布、重建索引、带 filters 检索、保存快照、导入案例、保存结果和比较 run 等接口。

| API | 用途 |
| --- | --- |
| `GET /api/v1/admin/policies` | 按 scope/status 查看版本 |
| `GET /api/v1/admin/policies/{id}` | 查看内容、ACL、索引状态 |
| `PATCH /api/v1/admin/policies/{id}` | 仅修改 draft |
| `POST /api/v1/admin/policies/{id}/publish` | 原子索引与发布 |
| `POST /api/v1/admin/policies/{id}/reindex` | 模型变化后重建索引 |
| `POST /api/v1/admin/retrieval/debug` | 查看脱敏候选和 Gate 决策 |
| `POST /api/v1/admin/evaluation-cases/import` | 导入版本化 Gold Set |
| `GET /api/v1/admin/evaluation-cases` | 查询案例与标签 |
| `POST /api/v1/admin/evaluations/run` | 创建异步评测任务 |
| `GET /api/v1/admin/evaluations/{id}` | 汇总与逐案例失败 |
| `GET /api/v1/admin/evaluations/compare` | 比较两个 run |

管理 API 仅 tenant admin 可用，继续执行第 3 周租户隔离和审计规则。

## 六、评测设计与发布门槛

首批 Gold Set 至少 50 条：20 条正常政策问答、10 条改写/口语、5 条需追问、5 条超范围、5 条旧版本诱导、5 条跨租户/ACL/提示注入。

指标：Recall@1/3/5、MRR、citation policy/version/chunk correctness、answerability precision/recall、false-answer rate、拒答原因准确率、旧版本命中率、租户/ACL 泄漏率和检索 P50/P95。

通过门槛：

- Recall@5 ≥ 90%，MRR ≥ 0.80；
- 引用政策版本正确率 100%；
- 应答场景放行率 ≥ 90%；
- 不可回答场景正确拒答/追问率 ≥ 95%；
- false-answer、旧版本命中、租户/ACL 泄漏均为 0；
- 10,000 chunks 基准下本地检索 P95 < 500 ms；
- 同一数据集重复运行结果确定且可比较。

指标未过线时不能靠调低 Gate 阈值掩盖问题，必须归因到切块、词法、向量、过滤、融合或 Gate。

## 七、5 天实施顺序

### Day 1：政策生命周期、迁移与索引边界

- 新增迁移字段、约束、索引和 retrieval snapshot；
- 建立 EmbeddingProvider Protocol；
- 草稿创建不再生成在线索引；
- 发布采用 `indexing → ready → published` 原子流程；
- published 不可修改；
- 完成 InMemory/PostgreSQL 契约测试。

验收：索引失败不会发布；并发发布同 scope 只有一个 current；草稿、过期和旧版本不能成为候选。

### Day 2：结构化切块、ACL 与混合检索

- 标题/段落优先、稳定 ordinal/content hash 的切块器；
- 写入 title path、ACL、embedding model/version；
- 查询规范化与实体抽取；
- 两路 Top-N、RRF、去重与 scope diversification；
- EXPLAIN 查询计划与 10,000 chunks 延迟基准。

验收：RRF 可解释；重复索引不产生重复 chunk；tenant/ACL/有效期在 SQL 候选阶段过滤。

### Day 3：Answerability Gate 与检索快照

- 实现 question type、required facts、覆盖度、冲突检测和 reason code；
- answer/clarify/handoff 三态；
- 每次问答保存 retrieval snapshot；
- 消息 facts 保存快照 ID 与最终引用；
- 客服接管上下文加入拒答原因和候选证据。

验收：缺证据不回答；冲突政策转人工；历史回答可从 snapshot 重放。

### Day 4：Gold Set 与真实评测执行器

- 案例导入、校验、版本与标签；
- 评测 API 入队、Worker 执行；
- 保存 candidate ranks、Gate 决策和失败原因；
- 计算 Recall@K、MRR、引用正确率、Gate 混淆矩阵和延迟；
- 实现 run 对比并导入 50 条中文 Gold Set。

验收：删除硬编码 `3/3`；政策或配置变化后能生成可解释差异报告。

### Day 5：管理页、安全回归与一键验收

- 管理页增加版本历史、ACL、索引状态、Retrieval Debug；
- 评测页显示指标、失败案例和 run 对比；
- 覆盖跨租户、提示注入、旧版本、ACL 篡改和并发发布；
- 新增 `scripts/verify_week4.ps1`；
- 更新 README、ARCHITECTURE、RUNBOOK、IMPLEMENTATION_STATUS。

验收：一键完成迁移、Seed、后端、PostgreSQL、50 案例评测、前端和安全回归。

## 八、自动化测试清单

1. draft/indexing/failed/superseded/expired 不可进入在线检索；
2. 并发发布不会产生两个当前版本；
3. published 版本不能 PATCH；
4. 相同 content hash 不重复生成 chunk；
5. embedding model/dimension 不匹配时拒绝混检；
6. tenant、商品、品类、地域、渠道、客户等级 ACL 在候选阶段过滤；
7. lexical-only、vector-only 和双路命中正确进入 RRF；
8. 返回 score 是 RRF score，rank/source 可审计；
9. 时限/金额问题缺少关键事实时不得 answer；
10. 冲突政策必须 handoff；无命中必须 NO_EVIDENCE；
11. snapshot 包含配置、候选排名、过滤和 Gate 决策；
12. superseded 后历史 snapshot 仍可读；
13. 评测真实读取 case 并写入 result；
14. Recall@K、MRR 与 Gate 混淆矩阵公式测试；
15. 两个 run 的 regression 比较正确；
16. 其他租户不能查看政策、快照、案例或结果；
17. 提示注入不能改变 tenant、ACL 或 published/effective 过滤；
18. 10,000 chunks 查询计划使用预期索引；
19. 第 1–3 周所有测试回归通过。

## 九、一键验收目标

```powershell
& .\scripts\verify_week4.ps1 -KeepServicesRunning
```

```text
Alembic upgrade: passed
Policy lifecycle and atomic publish: passed
Tenant / ACL / effective filters: passed
Hybrid retrieval and RRF: passed
Answerability Gate: passed
Gold Set 50 cases: passed
Recall@5 >= 0.90, MRR >= 0.80
False answer / stale policy / tenant leak: 0
PostgreSQL 10k-chunk P95 < 500 ms
Week 1-3 regression: passed
Frontend tests/build/E2E: passed
```

## 十、明确范围边界

- 只处理纯文本政策，不做 PDF/OCR、网页抓取和附件解析；
- Hash Embedding 用于测试，真实 Embedding 可配置但不作为离线验收前提；
- 先做可审计的 extractive answer，不让 LLM 自由综合多份政策；
- Prompt/模型对比、Token 成本和完整 LangGraph 属于第 5 周；
- 不做跨租户共享知识库；
- Redis 不缓存长期授权、发布状态或检索事实；
- 不为了提高回答率降低安全门槛，证据不足时宁可追问或转人工。
