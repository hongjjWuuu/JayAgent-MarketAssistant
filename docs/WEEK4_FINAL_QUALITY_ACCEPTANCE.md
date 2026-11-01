# 第四周最终质量验收：Gate v4 与 BGE-M3 v10

> 验收日期：2026-10-28  
> Alembic Head：`20261030_0018`  
> 当前 Active Profile：`bge-m3-v10`

## 一、结论

第四周已从“功能完成、质量优化中”推进为 **质量验收完成**。

本次没有覆盖失败历史：`bge-m3-v1` 至 `bge-m3-v9` 继续保持 `rejected` 且不可重建、校准或激活。最终版本 `bge-m3-v10` 使用相同的 `BAAI/bge-m3` 1024 维模型，通过改进数据标注、Query Analysis、scope 匹配、单政策事实覆盖、冲突分组和 Gate 语义后，完成三套冻结数据集验收。

当前状态：

```text
bge-m3-v10              active，9/9 published chunks
hash-v1:tenant_demo     ready，16/16 chunks，可应急回滚
bge-m3-v1 ... v9        rejected，不可变历史
```

## 二、正式评测证据

| 数据集 | 样例 | 结果 | 核心指标 |
| --- | ---: | --- | --- |
| `week4_gate_calibration/v2` | 80 | 80/80 passed | Answerability Recall 100%，False Answer 0，P95 235ms |
| `week4_gold/v5` | 100 | 100/100 passed | Recall@5 100%，MRR 1.00，三层引用准确率 100%，P95 221ms |
| `week4_hard_negative/v3` | 60 | 60/60 passed | Block Rate 100%，Reason Accuracy 100%，False Answer 0，P95 219ms |

三组 Run 共同满足：

- 旧版本命中率 0；
- 跨租户泄漏率 0；
- ACL 泄漏率 0；
- Profile、模型、Retriever、Gate、Normalizer、配置哈希和政策语料哈希一致；
- published Chunk 覆盖率 100%。

绑定 Run：

```text
Calibration  eval_v4_f15ef95d3ce14610a26fce2d22c7f06b
Gold         eval_v4_4ac4176e2bbd44f2b069ee853c1cc03c
Holdout      eval_v4_aa60031225a04a48869199de895bc39b
```

冻结 Gate 配置：

```json
{
  "gate_version": "v4",
  "min_rrf": 0.02,
  "semantic_only_min_score": 0.65,
  "min_top1_top2_margin": 0.1,
  "require_lexical_anchor_for_unknown_domain": true,
  "require_single_policy_fact_coverage": true,
  "weak_lexical_score": 0.1
}
```

## 三、相对 v1–v3 的关键进展

失败不是靠放宽阈值解决，而是逐层消除评测和规则歧义：

1. 校准集同时包含正例和负例，安全 Holdout 不参与调参；
2. `domain_intent` 从关键词判断升级为业务对象、操作和咨询目标联合判断；
3. Query scope 与 Candidate scope 不兼容时不能回答；
4. required facts 必须由同一政策族和有效版本覆盖，禁止跨政策拼接；
5. 冲突检测按具体政策主题分组，避免不同 scope 互相制造伪冲突；
6. ACL 缺失、显式冲突和不适用政策在候选或 Gate 阶段阻断；
7. 引用准确率与 Answerability Recall 分开计算，避免“拒答”被错误记作引用错误；
8. 每个 Run 固化政策语料哈希，避免不同语料状态的结果被误比较。

## 四、性能与回归

10,000 Chunk、50 次查询：

```text
冷查询                    356.50ms  < 800ms
排除 5 次预热后稳态 P50   333.87ms
排除 5 次预热后稳态 P95   364.26ms  < 450ms
Gold BGE 端到端 P95        221ms    < 3s
```

自动化结果：

- Ruff 与 Python compile：通过；
- 后端非集成测试：92 passed；
- PostgreSQL/Worker/RAG 集成：17 passed，25 个需要额外外部条件的用例按配置跳过；
- 前端 Vitest：2 passed；
- TypeScript 与 Vite 生产构建：通过；
- Alembic：`20261030_0018 (head)`。

## 五、激活与回滚演练

激活前 `activation-readiness` 返回：

```text
activatable = true
failed_gates = []
config_hash_match = true
index_coverage = 1.0
```

随后完成真实 PostgreSQL 状态切换：

```text
bge-m3-v10 active
→ 应急回滚到 hash-v1:tenant_demo active
→ 使用正式 Gold Run 重新激活 bge-m3-v10
```

最终状态为 `bge-m3-v10=active`、`hash-v1:tenant_demo=ready`。新增 Hash 回滚接口只接受本租户固定 Hash 基线、完整索引和 `ready` 状态，并记录操作者与回滚理由；普通 Profile 不能借此绕过质量门禁。

## 六、下一阶段

第五周可以恢复，但继续遵守：LLM 只做结构化理解和基于已验证事实的表达；政策资格、权限、风险、写操作和引用边界仍由 Gate、Policy Engine、Tool Gateway 与数据库控制。

下一步顺序：

1. 运行 Week 5 Fake Provider 80 条场景执行器；
2. 配置 DeepSeek 后运行固定 20 条 Smoke Set；
3. 验证模型故障时确定性订单流程与转人工降级；
4. 再进入客户 Widget、SSE 与真实业务 Connector 产品化。
