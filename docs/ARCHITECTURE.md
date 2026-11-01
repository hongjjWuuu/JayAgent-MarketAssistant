# 架构与关键决策

## 1. 请求与执行边界

```text
React Web → Keycloak OIDC → FastAPI
                              ├─ Repository → PostgreSQL/pgvector
                              ├─ Redis（限流/通知加速，不保存业务事实）
                              └─ runtime_task
                                      ↓ lease + heartbeat
                                  Worker → LangGraph / Tool Gateway
```

API 进程不在 HTTP 请求内执行 Agent 或业务写动作。消息和确认先与状态一起持久化，再由 Worker 领取。这样 API 重启、Worker 崩溃或浏览器断线不会丢失事实。

## 2. 领域状态机

会话：`new → ai_active → waiting_user | approval_pending → handoff_queued → human_active → resolved | closed`。

动作：`draft → validated → approval_required → approved → queued → executing → succeeded | retrying | unknown_needs_reconciliation | failed | compensation_required | cancelled`。

任务：`queued → running → completed`；失败后 `running → retry_wait → running`，超过次数进入 `dead_letter`。queued/retry_wait 可取消，终态不可逆。

所有转换通过 `app/domain/transitions.py` 的 Transition Policy 校验；非法 API 转换返回 `409 INVALID_STATE_TRANSITION` 并记录审计。模型输出不能直接更改业务事实。

## 3. 多租户与授权

- JWT `sub` 是身份主键；`preferred_username` 只作为业务显示/客户映射。
- 后端使用 `(tenant_id, sub)` 查询 `tenant_member`，不信任前端传入角色。
- 客户资源还要通过 `customer_id` 做对象级过滤；越权与不存在统一返回 404，避免枚举。
- 核心表含 `tenant_id`；Repository 所有读取都显式带租户条件。
- 生产配置若启用开发请求头，应用启动失败。

## 4. 动作幂等与不确定结果

确认动作时，ActionPlan 行被锁定；ActionExecution 与 runtime_task 在同一事务中创建。唯一键 `(tenant_id, idempotency_key)` 是最后防线。

写操作超时不能证明失败，因此进入 `unknown_needs_reconciliation`。系统创建延迟 `reconcile_execution` 任务，通过 `ReconciliationProvider` 按幂等键只查询外部状态，再决定成功、失败、补偿或继续等待，绝不直接重放原写操作。

## 5. Worker 租约与死信治理

Worker ID 由 hostname、process ID 和随机后缀构成。任务心跳最长每 5 秒执行，并以 task ID、lease owner、running 状态做条件更新。处理器前后使用 LeaseGuard，完成或失败提交时 Repository 再检查 owner；旧 Worker 无法提交过期结果。

Worker 领取过期 running 任务会写入 recovered 事件。死信不会被改回 queued；管理员重放时创建新的关联任务，数据库唯一约束保证同一死信只有一个直接子任务。原错误、attempts 和事件时间线保持不变。

## 6. RAG 与 Answerability Gate

政策发布时：

1. 校验租户、作用域、版本与生效期；
2. 同一作用域旧发布版本变为 `superseded`；
3. 文档重叠切块并写入 `policy_chunk`；
4. FTS 与 pgvector 分别排名；
5. RRF 融合后交给 Answerability Gate；
6. 命中不足、版本失效或证据不完整时只能追问/转人工。

回复 facts 保存政策 ID、版本、片段和门控分数，使后续审计可重放。

## 7. LangGraph 与模型职责

首版只有一个有界图：`classify_intent → assess_risk → execute`，递归上限为 8。生产扩展节点应保持原计划的 context、retrieval、gate、plan、confirmation 和 persist 边界。

模型仅做意图/参数提取与措辞；Policy Engine 判定资格和风险；Tool Gateway 做权限与执行。模型不可声明订单事实或写操作成功。

## 8. 从 Jaycode/JayAgent-review 继承的模式

- Jaycode：Repository Protocol、API/Worker 解耦、任务租约/心跳/恢复、事件序列；
- JayAgent-review：Webhook 型任务幂等、due-task claim、过期锁恢复、重试退避与可观测回执；
- 没有把两个项目作为运行时依赖，避免发布与数据模型互相绑死。
