# 单机试点运行手册

## 启动与验收

1. 复制 `.env.example` 为本地私密配置，仅在非 Compose 开发时需要。
2. 执行 `docker compose up --build`。
3. 确认 `/health/live` 为 200，`/health/ready` 为 200。
4. 用四个演示账号分别验证客户、客服、租户管理和平台运维视图。
5. 运行订单查询 → 政策引用 → 工单确认 → 人工接管 → 评测完整链路。

## 备份与恢复

备份前记录当前镜像版本和 Alembic revision。数据库使用 `pg_dump -Fc` 生成自包含备份；Redis 不需要作为业务恢复来源。恢复到空数据库后执行 `alembic upgrade head`，再恢复 dump，并核对会话、任务、审计和政策数量。

不要只备份 pgvector 表：向量、政策版本与业务数据必须保持同一快照。

已提供可执行入口：

```powershell
& .\scripts\backup_postgres.ps1
& .\scripts\restore_postgres.ps1 -BackupFile .\backups\market-agent-YYYYMMDD-HHMMSS.dump -ConfirmRestore
& .\scripts\verify_m3_closeout.ps1
```

恢复是破坏性操作，脚本要求显式 `-ConfirmRestore`。恢复后必须重新执行迁移、租户隔离、幂等写和任务恢复回归。

## 升级与回滚

1. 先备份 PostgreSQL；
2. 在副本运行迁移与回归集；
3. 停止接收新的写任务，等待 running 任务结束或租约过期；
4. 更新镜像并运行迁移；
5. 验证 ready、队列积压、死信、错误动作和租户隔离指标；
6. 代码可回滚到旧镜像，数据库迁移只在确认 downgrade 安全后回退。

## 故障处理

- PostgreSQL 不可用：ready 返回 503，API/Worker 不应继续写；恢复后任务仍在数据库。
- Redis 不可用：响应头 `X-RateLimit-Mode: degraded-open`；核心读写继续，监控应告警。
- LLM 超时：确定性订单查询继续；其余分类走回退或人工。
- Worker 崩溃：租约过期后其他 Worker 重新领取；幂等执行阻止重复业务结果。
- 外部写超时：状态必须为 `unknown_needs_reconciliation`，先查外部回执再处理。
- Keycloak 不可用：已有 Token 在签名键缓存有效期内可能继续；新登录失败，禁止切回开发认证救火。

## M3 故障演练清单

每次演练记录开始/恢复时间、关联 ID、预期、实际结果和遗留问题：

1. 暂停 PostgreSQL，确认 ready=503 且恢复后业务事实仍在；
2. 暂停 Redis，确认核心读写可用且响应标记 `degraded-open`；
3. 注入 LLM timeout/429/5xx，确认订单确定性链路或人工降级；
4. 强杀正在执行任务的 Worker，确认租约过期后恢复且旧 Worker 不能提交；
5. 让 Connector 写成功但丢失响应，确认进入 unknown 并通过幂等键查询回执；
6. 重启 API 与 Worker，确认会话、任务和 ActionExecution 均可恢复；
7. 执行一次 PostgreSQL 备份、恢复并核对 Alembic revision；
8. 在独立验证数据库执行 `verify_fresh_migration.ps1`。

Prometheus UI 位于 `http://localhost:9090`。告警规则在 `deploy/prometheus/alerts.yml`，当前覆盖 API 不可用、5xx 比例和 P95；Worker/队列业务告警下一步从数据库导出器补充。

## OIDC 与权限故障处理

```powershell
& .\scripts\verify_week3.ps1 -KeepServicesRunning
```

- 用户改名不应改变业务资源归属；客户绑定由 `channel_identity` 中的 JWT subject 决定。
- 禁用成员应修改 `tenant_member.active=false`，下一次请求立即返回 403。
- Keycloak 轮换 key 后，未知 `kid` 会触发一次 JWKS 刷新；持续 401 时检查 issuer、audience、JWKS URL 与时间同步。
- 不允许通过 `X-Tenant-ID`、`X-Role` 或 `X-User-ID` 修复 OIDC 问题。
- platform admin 不属于业务租户，只能读取 Worker 与平台健康信息。
- Direct Access Grant 仅用于本地自动化测试，生产 Realm 必须关闭。

## Worker 故障与死信操作

一键可靠性验收：

```powershell
& .\scripts\verify_week2.ps1 -WorkerCount 4 -KeepPostgresRunning
```

运行多 Worker：

```powershell
docker compose up -d --scale worker=4 worker
```

排查顺序：

1. 使用 platform_admin 查询 `/api/v1/admin/workers`，确认 last heartbeat 和 current task；
2. 使用租户管理员查询 `/api/v1/admin/runtime-tasks`，按 running、retry_wait、dead_letter 筛选；
3. 查看任务详情事件时间线，区分 claimed、recovered、retry_wait 和 dead_letter；
4. 不要直接修改死信状态；修复根因后使用 replay API 创建关联子任务并填写理由；
5. 对 unknown execution 只核验外部回执。无法确认时保持 unknown，不得重放原写操作；
6. 人工结论必须包含理由与回执摘要。

Worker 被终止时不需要手工解锁任务。等待租约过期，由健康 Worker 自动 recovered。严禁通过 SQL 把 running 直接改回 queued，避免旧 Worker 与新 Worker 同时提交。

## 生产前必须替换

- Keycloak 演示密码与 API client secret；
- 哈希 Embedding 为真实中文向量服务；
- 高保真模拟 Connector 为真实订单/物流/工单适配器；
- 单机默认日志为集中日志采集与告警；
- 补齐数据留存周期、隐私删除与备份加密策略。
