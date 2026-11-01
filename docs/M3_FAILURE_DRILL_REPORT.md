# M3 故障演练报告

> 生成时间：2026-11-01T12:15:52.612534+00:00  
> Compose 项目：`jayagent-marketassistant-drill`  
> Alembic Head：`20261031_0019`

## 演练结论

| 演练 | 结果 | 证据 |
| --- | --- | --- |
| `postgres-outage` | passed | readiness=503; recovery=ready |
| `redis-outage` | passed | core read succeeded with X-RateLimit-Mode=degraded-open |
| `connector-response-loss` | passed | execution=succeeded; external_ticket_count=1 |
| `api-worker-restart` | passed | conversation and durable execution recovered |
| `llm-safe-degradation` | passed | timeout, 429, 500 and invalid JSON preserved deterministic order lookup |
| `forced-worker-termination` | passed | lease expired; second worker recovered; recovered event persisted |
| `transactional-migration-failure` | passed | revision unchanged; partial table absent |

## 数据恢复证据

- PostgreSQL 备份恢复：通过，最新备份为 `market-agent-20261101-200559.dump`。
- Worker 在 PostgreSQL 短暂中断后会重新建立 Repository 与任务循环，不会永久退出。
- Connector 写成功但响应丢失时仅通过幂等键查询回执，不重放原始写操作。

## 尚未覆盖的发布门禁

本报告只记录故障恢复证据；OIDC、Fake Provider 与 DeepSeek 的最终结果见 `output/m3/m3-release-report.json`。
