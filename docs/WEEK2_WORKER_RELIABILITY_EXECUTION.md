# 第 2 周执行记录：完整状态机与持久化 Worker

> 完成日期：2026-10-22  
> 对应版本：0.3.0  
> 验收入口：`scripts/verify_week2.ps1`

## 1. 本周结论

第 2 周已从“核心代码存在”推进到“真实 PostgreSQL 故障场景已验证”。系统现在能够用 PostgreSQL 协调多个 Worker，并通过租约、心跳和提交栅栏防止重复或过期执行者写回结果。

已证明的关键性质：

- 4 个 Worker 并发处理 100 个任务，每个任务只完成一次；
- 任务执行时间超过初始租约时，后台心跳持续续租，其他 Worker 无法抢占；
- 独立 Worker 进程被终止后，第二个 Worker 可在租约过期后恢复任务；
- 旧 Worker 即使随后恢复，也会因 `task_id + lease_owner + running` 校验失败而失去提交资格；
- 原死信永远保留，重放会创建带来源关系的新任务；
- 两个并发重放请求由数据库唯一约束收敛为一个子任务；
- 外部写超时只创建查询型对账任务，不重放原写操作；
- 管理员可查看任务事件、取消待执行任务、重放死信并人工处理未知执行。

## 2. 实际架构

```text
FastAPI
  └─ 原子创建业务事实 + runtime_task + queued event
                           ↓
PostgreSQL runtime_task
  ├─ FOR UPDATE SKIP LOCKED 并发领取
  ├─ lease_owner / lease_expires_at 执行权
  ├─ runtime_task_event 不可变时间线
  └─ replay_of_task_id 死信血缘
                           ↓
动态 Worker <hostname>:<pid>:<uuid>
  ├─ Worker heartbeat（最长 5 秒）
  ├─ Task heartbeat（租约时长 1/3，最长 5 秒）
  ├─ LeaseGuard 执行前后检查
  └─ owner compare-and-set 完成/失败
                           ↓
agent_message | execute_action | reconcile_execution
```

Redis 不参与任务事实、领取、租约或恢复。Redis 丢失不会改变任务真实状态。

## 3. 状态转换实现

状态规则集中在 `app/domain/transitions.py`。Service 和 Worker 调用统一校验函数，非法转换抛出 `InvalidStateTransition`；HTTP 层统一返回：

```json
{
  "error_code": "INVALID_STATE_TRANSITION",
  "message": "entity cannot transition from x to y",
  "request_id": "req_...",
  "details": {"entity": "...", "current": "x", "target": "y"}
}
```

拒绝的 HTTP 转换会尽力写入 `transition.rejected` 审计；审计存储失败不应覆盖原始 409 响应。

### RuntimeTask

```text
queued → running → completed
             └──→ retry_wait → running
             └──→ dead_letter
queued / retry_wait → cancelled
```

### ActionExecution / ActionPlan

```text
approval_required → queued → executing → succeeded
                               ├─→ retrying → queued
                               ├─→ failed
                               └─→ unknown_needs_reconciliation
                                      ├─→ succeeded
                                      ├─→ failed
                                      └─→ compensation_required
```

动作处理失败时，任务和 Action 状态同步进入 retrying 或 failed；重试领取后先回到 queued，再进入 executing。

### Conversation

Conversation 的 AI、等待用户、待审批、排队接管、人工处理、解决与关闭状态都通过同一策略校验。closed、succeeded、failed、cancelled、completed 等终态不可逆转。

## 4. 数据库变更

新增迁移：`20261022_0002_worker_reliability.py`。

主要字段：

- `runtime_task.replay_of_task_id`、`replayed_by`、`replay_reason`；
- `runtime_task.dead_lettered_at`、`cancel_requested_at`；
- `runtime_worker.started_at`、`current_task_id`、`hostname`、`process_id`；
- `action_execution.updated_at`、`reconciliation_attempts`、`last_reconciled_at`。

主要约束：

- `uq_runtime_task_replay_once`：一个死信只允许一个直接重放子任务；
- `uq_message_task_sender`：同一租户、任务、发送者只保存一条对应消息，避免恢复后重复回复；
- 已有任务领取和过期租约部分索引继续用于 Worker 热路径。

开发数据库已迁移至 `20261022_0002 (head)`。

## 5. Worker 执行协议

### 5.1 领取

Worker 使用 `FOR UPDATE SKIP LOCKED` 领取 ready task。正常领取记录 `claimed`；领取已过期的 running task 记录 `recovered`，attempts 增加。

### 5.2 续租与栅栏

处理器运行时开启独立心跳线程。续租更新必须同时匹配：

```text
task_id = 当前任务
lease_owner = 当前 Worker
status = running
```

任一条件不匹配会触发 `lease_lost`。处理器返回后还会执行一次 `LeaseGuard.ensure_owned()`；最终 complete/fail 再在数据库事务中检查 owner。因此应用层检查和数据库提交栅栏共同生效。

### 5.3 处理器注册表

生产注册三个处理器：

- `agent_message`：执行有界 Agent 流程并幂等写入助手消息；
- `execute_action`：执行已确认动作；
- `reconcile_execution`：仅按幂等键查询外部结果。

测试通过可注入 handler 构造阻塞型长任务，不向生产任务类型中加入测试分支。

## 6. 死信与重放

死信重放不修改原记录：

1. 锁定原任务；
2. 验证状态为 dead_letter；
3. 复制租户、会话、任务类型、payload 和最大尝试次数；
4. 新任务 attempts 为 0、状态 queued；
5. 写入 `replay_of_task_id`、操作者与理由；
6. 原任务记录 replayed 事件，新任务记录 queued 事件。

非死信任务重放返回 `409 TASK_NOT_REPLAYABLE`。列表只展示 payload key；详情也会屏蔽 token、secret、password、address、phone 和 api_key。

## 7. 对账规则

外部写发生“可能提交成功但响应丢失”时：

```text
execution = unknown_needs_reconciliation
→ 延迟创建 reconcile_execution
→ ReconciliationProvider.lookup(idempotency_key)
→ succeeded / failed / compensation_required / pending
```

pending 只会重新安排查询。达到最大尝试次数后，对账任务进入 dead_letter，而 ActionExecution 保持 unknown，等待管理员核验。任何自动路径都不会再次调用原写接口。

人工处理要求填写结论、理由和回执摘要，并写入审计事件。

## 8. 管理 API 与页面

新增接口：

- `GET /api/v1/admin/runtime-tasks`
- `GET /api/v1/admin/runtime-tasks/{id}`
- `POST /api/v1/admin/runtime-tasks/{id}/replay`
- `POST /api/v1/admin/runtime-tasks/{id}/cancel`
- `GET /api/v1/admin/workers`（仅 platform_admin）
- `GET /api/v1/admin/runtime-summary`（租户任务计数与脱敏 Worker 健康摘要）
- `GET /api/v1/admin/reconciliations`
- `POST /api/v1/admin/action-executions/{id}/resolve-reconciliation`

运营页新增任务运行中心：状态筛选、数量摘要、任务详情、错误信息、事件时间线、取消、重放、待对账列表和人工结论。前端隐藏不构成权限边界，所有接口仍由后端校验角色和租户。

## 9. 自动化证据

当前验证结果：

| 检查 | 结果 |
| --- | --- |
| Ruff | 通过 |
| 后端单元/API | 37 项通过 |
| PostgreSQL 集成/可靠性 | 12 项通过 |
| 4 Worker / 100 task | 无重复，全部 attempts=1 |
| 长任务续租 | 无租约窃取 |
| 旧 Worker 栅栏 | LeaseLostError |
| 强制终止恢复 | attempts=2，存在 recovered event |
| 并发死信重放 | 只产生一个子任务 |
| 对账 | lookup-only 成功与 pending 重试路径已覆盖 |
| Vitest | 2 项通过 |
| Playwright | 运营页筛选入口、任务中心与待对账视图通过 |
| React 生产构建 | 通过 |

## 10. 如何执行验收

```powershell
Set-Location D:\JayAgent\JayAgent-MarketAssistant
Set-ExecutionPolicy -Scope Process Bypass
& .\scripts\verify_week2.ps1 -WorkerCount 4 -KeepPostgresRunning
```

脚本依次执行：PostgreSQL 启动与健康检查、Alembic、重复 Seed、可靠性测试、Ruff、普通后端回归、前端测试/构建和 revision 检查。

## 11. 仍然属于后续周的工作

- Worker 在线数据目前可由 platform_admin API 查看，尚未接 Prometheus 告警与历史趋势；
- 模拟对账 Provider 需要在真实 Connector 到位后替换；
- 任务优先级、队列配额、跨数据库容灾和 Kubernetes 不在本周范围；
- 还需在完整 Compose 环境执行 API/Worker 容器重启与 Keycloak 联调；
- 生产应建立死信处理 SLA、原因分类和审批流程，而不只是技术重放按钮。
