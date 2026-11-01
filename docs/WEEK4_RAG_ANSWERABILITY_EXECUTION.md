# 第 4 周实施与验收：政策版本、RAG 与 Answerability Gate

## 1. 本周交付结论

第 4 周已经从“能查政策”升级为“政策发布、检索、决策和评测都可审计”的受控 RAG。系统只检索当前租户、已发布、仍有效且 ACL 匹配的政策；证据不足、上下文缺失或政策冲突时，不允许输出确定性结论。

本周没有引入第 5 周的自由生成。客户看到的政策答案仍直接引用政策原文，LLM 无权修改发布状态、租户、ACL、资格、额度或 Gate 结论。

## 2. 最终数据流

```text
政策草稿
  → PATCH 草稿（不建在线索引）
  → 发布事务锁定 tenant + scope
  → indexing
  → 标题/段落切块 + Embedding
  → 写入 Document/Chunk
  → 新版本 published
  → 旧版本 superseded

用户问题
  → 规范化与实体过滤
  → PostgreSQL tenant/ACL/有效期/发布状态过滤
  → lexical top 30 + vector top 30
  → RRF(k=60)
  → Answerability Gate
  → answer / clarify / handoff
  → retrieval_snapshot
  → 仅 answer 引用政策原文
```

## 3. 关键实现

### 3.1 生命周期与原子发布

- 迁移版本固定为 `20261025_0004`，未改写 `0001–0003`。
- `save_policy` 只保存草稿；草稿对应 Chunk 数必须为 0。
- 已发布版本不可 PATCH，API 返回 `409 POLICY_IMMUTABLE`。
- 发布事务同时完成索引构建和版本切换。切块或 Embedding 失败时事务回滚，旧发布版本仍然在线，新草稿记录 `index_status=failed` 与 `index_error`。
- `(tenant_id, scope, version)` 防止版本号重复；部分唯一索引确保每个租户/作用域只有一个 `published` 版本。
- 重建索引通过持久化 `reindex_policy` 任务执行，不混用不同 Embedding 空间。

### 3.2 切块、ACL 与 RRF

- 切块优先识别 Markdown 标题、中文章节标题和段落边界；长段落再按固定窗口与重叠切分。
- Chunk 保存 ordinal、title path、content hash、metadata、Embedding 模型与维度。
- ACL 支持 `product_id`、`category`、`region`、`channel`、`customer_tier`。
- ACL、租户、发布状态与有效期在 PostgreSQL 候选阶段过滤，而非取回后再过滤。
- lexical 和 semantic 各取 30 条，以 `1/(60+rank)` 相加形成 RRF score；API 不再把原始相似度冒充融合分数。

### 3.3 三态 Gate

统一输出：

```json
{
  "decision": "answer | clarify | handoff",
  "reason_code": "ANSWERABLE | NO_EVIDENCE | LOW_COVERAGE | CONFLICTING_POLICIES | MISSING_CONTEXT | UNSUPPORTED_SCOPE",
  "confidence": 0.0,
  "required_follow_up": [],
  "evidence_chunk_ids": [],
  "retrieval_snapshot_id": "snapshot_...",
  "gate_version": "v1"
}
```

- `answer`：证据达到门槛，才允许引用原文回答。
- `clarify`：问题指向“这个商品”“本地区”等对象，但缺少商品/地域上下文。
- `handoff`：无证据、低覆盖、冲突政策或不支持的作用域。
- Hash 向量的弱偶然相似不能单独放行；纯向量命中还必须达到语义覆盖下限，因此 Gold Set 的 false-answer 为 0。

### 3.4 可重放快照与真实评测

每次 Gate 运行都会保存 `retrieval_snapshot`：脱敏查询、查询哈希、过滤器、候选排名、最终引用、Retriever/Embedding/Gate 版本、决定、原因、置信度和时延。即使政策之后被替代，历史快照仍可读取。

评测不再硬编码“3 条全部通过”。`evaluation_run` Worker 从 `evaluation_case` 读取案例，逐条运行真实检索与 Gate，写入 `evaluation_result`，最后聚合 Recall@5、MRR、Gate 放行率、不可回答阻断率与 false-answer rate。

## 4. API 与界面

- `GET /api/v1/admin/policies`
- `GET /api/v1/admin/policies/{id}`
- `PATCH /api/v1/admin/policies/{id}`
- `POST /api/v1/admin/policies/{id}/publish`
- `POST /api/v1/admin/policies/{id}/reindex`
- `POST /api/v1/admin/retrieval/debug`
- `POST /api/v1/admin/evaluation-cases/import`
- `GET /api/v1/admin/evaluation-cases`
- `POST /api/v1/admin/evaluations/run`
- `GET /api/v1/admin/evaluations/{id}`
- `GET /api/v1/admin/evaluations/compare`

所有接口仅允许当前租户的 `tenant_admin`。`platform_admin` 不能读取租户政策、检索快照或评测数据。运营页新增政策历史/索引状态、RRF 双路排名、Gate reason code、snapshot ID 和异步评测入口。

## 5. 质量结果

在 PostgreSQL + pgvector 和 50 条中文 `week4_gold/v1` 上：

| 指标 | 结果 | 门槛 |
|---|---:|---:|
| Gold Set 数量 | 50 | ≥ 50 |
| Recall@5 | 100% | ≥ 90% |
| MRR | 1.00 | ≥ 0.80 |
| 引用政策版本正确率 | 100% | 100% |
| 可回答场景 Gate 放行率 | 92.5% | ≥ 90% |
| 不可回答拒答/追问率 | 100% | ≥ 95% |
| False-answer rate | 0 | 0 |
| 旧版本命中 | 0 | 0 |
| 跨租户/ACL 泄漏 | 0 | 0 |
| 10,000 Chunk P95 | 通过 `< 500 ms` 自动门槛 | `< 500 ms` |

回归结果：后端 55 passed；原 PostgreSQL 契约/Worker 13 passed；第 4 周 PostgreSQL 质量测试 3 passed；前端 2 passed；生产构建成功。

## 6. 复验命令

```powershell
Set-Location D:\JayAgent\JayAgent-MarketAssistant
Set-ExecutionPolicy -Scope Process Bypass
& .\scripts\verify_week4.ps1 -KeepServicesRunning
```

脚本会启动依赖、升级到 `20261025_0004`、导入演示数据与 50 条 Gold Set、运行 Ruff、后端/数据库/前端测试、构建前端，并输出当前迁移与容器状态。

单独重新导入 Gold Set：

```powershell
$env:PERSISTENCE_BACKEND = "postgres"
& .\.venv\Scripts\python.exe .\scripts\seed_week4_gold.py
```

该脚本可重复运行：政策按作用域和版本复用，案例按 dataset、version、name 更新。

## 7. 尚未越界实现的内容

- 默认使用 Hash Embedding，OpenAI-compatible Provider 仅提供适配器；切换真实模型后必须重建索引并重跑同一 Gold Set。
- PostgreSQL FTS 仍使用 `simple`，中文召回当前主要由向量路完成；生产前需要中文分词实验。
- 只处理纯文本政策，不处理 PDF、OCR、表格与附件。
- 本周不使用 LLM 自由生成政策答案；第 5 周接入后仍必须服从本 Gate 和引用快照。
- 指标下降时只能修复切块、召回、ACL、融合或 Gate 规则，不允许下调安全门槛掩盖问题。
