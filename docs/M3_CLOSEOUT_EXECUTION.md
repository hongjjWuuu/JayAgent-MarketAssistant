# M3 单机试点收口执行记录

> 基线日期：2026-11-01  
> 目标：把已经通过技术验收的 Agent 内核包装为可交付、可运营、可恢复的单机试点产品。

## 1. 阶段结论

当前阶段为 **M3 单机集成试点准入收口**，不是 M4 用户试点。独立 Compose、故障演练、完整 OIDC E2E、全新数据库迁移与隔离备份恢复均已取得真实证据。最终发布门禁仅剩在本项目进程显式提供密钥后重跑 DeepSeek 20 条 live smoke。

已经冻结的技术基线：

- Alembic head：`20261031_0019`；
- RAG：`bge-m3-v10`、Gate v4，Hash 基线可回滚；
- Agent：Fake 80/80；开发阶段真实 DeepSeek 20/20 且无错误业务动作，但 M3 隔离发布环境仍要求再执行一次 live smoke；
- 权限：Keycloak 四角色、资源级授权、客服队列授权；
- 可靠性：PostgreSQL 任务队列、租约、心跳、恢复、死信、重放、lookup-only 对账。

## 2. 本轮已经落地

### 客户 Widget

- 使用携带 OIDC Bearer Token 的 Fetch Stream 消费 SSE；
- 保存并恢复当前会话；
- 显示订单/物流事实、政策版本引用、补偿建议、ActionPlan 和工单状态；
- ActionPlan 确认仍进入后端幂等执行队列；
- 增加加载、实时进度、安全降级和移动端布局。

### 客服 Console

- 待办显示优先级、等待分钟数和 SLA；
- 原子领取后可回复、释放、解决或转派；
- 转派保存目标队列、理由、时间和原客服；
- 上下文同时展示会话摘要、最近订单、政策引用、风险和待处理动作；
- 首次回复和解决时间写入持久化上下文，用于运营聚合。

### Connector

- 新增 `BusinessConnector` Protocol；
- 默认 `RepositoryBusinessConnector` 保持 CI 稳定；
- 新增 `HttpBusinessConnector`，支持认证、超时和幂等键；
- 外部写超时、网络失败或 5xx 进入 `unknown_needs_reconciliation`；
- `ConnectorReconciliationProvider` 只按幂等键查询回执，绝不重放原写操作。
- 新增独立 FastAPI OMS/物流/工单模拟服务，SQLite 持久化并以唯一键保证外部幂等；
- 支持延迟、429、500、提交后 500、提交后超时和 pending→succeeded 故障注入；
- `BusinessConnector` 已补齐强类型 `get_shipment()`；外部工单响应丢失演练最终只产生一张工单。

### 运营与运维

- 新增 `/api/v1/admin/pilot-metrics` 与管理页指标卡；
- PostgreSQL 单条聚合查询计算自助解决率、转人工率、一次解决率、首次响应、处理时长、P95、Token 和成本；本地真实库只读验证通过；
- 新增 Prometheus 抓取配置和基础告警；
- 新增备份、恢复、全新迁移、M3 验收和 10 分钟演示脚本；
- Compose 增加服务资源上限与 Prometheus 持久卷。

## 3. M3 发布门禁

| 门禁 | 当前判断 | 证据入口 |
| --- | --- | --- |
| Ruff | 已通过 | `python -m ruff check app tests` |
| 前端 TypeScript/构建 | 已通过 | `npm run build` |
| 前端 Vitest | 已通过 | `npm test -- --run` |
| 新增 M3 领域测试 | 已通过 | `tests/test_m3_closeout.py`、`tests/test_m3_release_package.py`、`tests/test_m3_llm_failure_drills.py` |
| 全量非集成回归 | 已通过：113/113 | `pytest tests --ignore=tests/integration` |
| PostgreSQL 集成 | 已通过：18/18 | 隔离数据库 Schema |
| OIDC 集成 | 已通过：5/5 | 隔离 Keycloak/API |
| Compose 配置 | 已通过 | `docker compose config --quiet` |
| 全新 0001→0019 迁移 | 已通过 | `verify_fresh_migration.ps1`；发现并修复 live metadata 引起的重复列问题 |
| 备份与恢复 | 隔离恢复演练已通过 | `verify_backup_restore.ps1`；主库备份恢复到临时库并核对 revision/tenant |
| 故障演练 | 已通过：7 项 | `run_m3_failure_drills.ps1`、`output/m3/failure-drills.json` |
| 备份恢复 | 已通过 | `backups/verification/market-agent-20261101-200559.dump` |
| Prometheus | 已通过 | API/Worker targets up；10 条规则通过 promtool |
| 完整 OIDC E2E | 已通过：1/1 | 客户→外部工单→人工接管→四角色禁止访问 |
| DeepSeek 最终 live smoke | 待重跑 | 当前项目未配置 `LLM_API_KEY`；不得复制其他项目密钥 |

因此当前结论是 **M3 准入代码与非密钥门禁完成，最终 live acceptance 待执行**。未取得真实 DeepSeek 重跑证据前，不把状态改成“M3 验收完成”。

## 4. M4 准入判定

只有以下条件同时成立才允许进入小规模用户试点：

1. 全新环境启动、迁移和 Seed 可重复；
2. 跨租户、未确认写动作和自动高风险补偿均为 0；
3. Connector 超时不产生重复工单，对账只查询回执；
4. 客户到客服再回客户的完整 E2E 通过；
5. API/Worker 强杀、Redis/LLM/Connector 故障和 PostgreSQL 恢复均完成演练；
6. 运营页能用真实试点数据解释自助解决率、转人工率、时延和成本；
7. `verify_m3_release.ps1 -RunLiveDeepSeekSmoke` 与安全回归全部通过。

## 5. 明确延期

本阶段不实现自动退款/发券/改价、语音、多渠道、多 Agent、长期自主记忆、微服务、Kubernetes 或多区域容灾。真实业务系统只通过 Connector 替换，不允许业务 SDK 渗透到 Agent、Service 或 Worker 状态机。

