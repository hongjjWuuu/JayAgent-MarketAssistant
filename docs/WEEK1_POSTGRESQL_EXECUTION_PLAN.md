# 第 1 周执行计划：PostgreSQL 持久化验收

> 目标：把“PostgreSQL 代码已经实现”转变为“真实数据库环境已经验证”。  
> 建议投入：5 天，每天 2–4 小时。  
> 状态：已于 2026-10-20 完成真实 PostgreSQL 验收，容器保留运行。

## 验收结果

```text
PostgreSQL/pgvector: healthy
Alembic: 20261020_0001 (head)
Seed: 连续执行两次成功
PostgreSQL integration: 7 passed
Backend regression: 15 passed, 7 integration tests deselected
Ruff: passed
API process restart: conversation restored successfully
```

真实数据库验收期间发现并修复了三类仅靠内存测试难以发现的问题：无 ORM relationship 时外键父子记录的 flush 顺序、隔离 schema 对 public 可见导致的测试污染，以及 Seed 身份 subject 与开发客户 ID 不一致。

## 一、本周最终交付物

本周结束时应得到以下证据，而不只是代码：

1. 一份成功的 Alembic 迁移日志；
2. 一份重复执行两次仍成功的 Seed 日志；
3. PostgreSQL Repository Contract Test 全部通过；
4. 两个并发确认只产生一个 ActionExecution；
5. 两个 Worker 不会同时领取同一任务；
6. 新建 Repository/API 进程后仍能读取原会话和工单；
7. 关键查询的 EXPLAIN 使用预期索引；
8. API 重启前后的数据 ID 对照记录；
9. 第一周验收结果写回 `IMPLEMENTATION_STATUS.md`。

## 二、本轮已经开始实施的内容

本轮已经新增：

- `tests/integration/conftest.py`：为每次测试创建唯一 PostgreSQL schema；
- `tests/integration/test_repository_contract.py`：真实 Repository 契约测试；
- 双线程数据库并发确认测试；
- 新 Repository 实例恢复会话和工单测试；
- 关键唯一索引和部分索引存在性测试；
- 订单查询 `EXPLAIN` 索引验证；
- `scripts/verify_week1.ps1`：一键启动 PostgreSQL、迁移、Seed 和测试；
- `PostgresRepository` 支持注入独立 Session Factory；
- 会话、消息、工单、人工队列和任务领取的复合索引。

不带数据库运行测试的结果：

```text
15 passed, 7 skipped
```

7 项 skipped 是真实 PostgreSQL 专用测试；真实数据库验收中已经全部执行并通过。

## 三、开始前准备

### 3.1 你需要做什么

本机已经安装 Docker Desktop。更换开发机时，先安装并启动 Docker Desktop，然后在 PowerShell 中确认：

```powershell
docker version
docker compose version
```

两个命令都必须成功，Docker Desktop 状态必须显示 Engine Running。

不需要单独安装 PostgreSQL 或 psql，项目会使用 `pgvector/pgvector:pg16` 容器。

### 3.2 验证项目 Python 环境

```powershell
Set-Location D:\JayAgent\JayAgent-MarketAssistant
& .\.venv\Scripts\python.exe --version
& .\.venv\Scripts\python.exe -m pip show psycopg sqlalchemy alembic pgvector
```

如果 `.venv` 不存在：

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## 四、最短执行路径

Docker 可用后，优先直接运行：

```powershell
Set-Location D:\JayAgent\JayAgent-MarketAssistant
Set-ExecutionPolicy -Scope Process Bypass
& .\scripts\verify_week1.ps1 -KeepPostgresRunning
```

脚本会依次执行：

```text
启动 PostgreSQL/pgvector
→ 等待健康检查
→ Alembic upgrade head
→ 连续执行两次 Seed
→ PostgreSQL 集成测试
→ Ruff 与全部后端回归
→ 输出 Alembic 当前版本
```

成功标志：最后输出“第 1 周 PostgreSQL 验收完成”。

如果失败，不要直接删除 Volume。先执行：

```powershell
docker compose ps
docker compose logs postgres
```

保留日志，根据失败步骤定位问题。

## 五、每日详细安排

## Day 1：数据库环境、迁移与 Seed

### 目标

建立可重复启动的真实 PostgreSQL/pgvector 环境。

### 操作

```powershell
Set-Location D:\JayAgent\JayAgent-MarketAssistant
docker compose up -d postgres
docker compose ps postgres
```

容器进入 healthy 后：

```powershell
$env:DATABASE_URL = "postgresql+psycopg://market_agent:market_agent@localhost:5433/market_agent"
$env:PERSISTENCE_BACKEND = "postgres"
& .\.venv\Scripts\python.exe -m alembic upgrade head
& .\.venv\Scripts\python.exe -m alembic current
```

期望迁移版本：

```text
20261020_0001
```

连续执行两次 Seed：

```powershell
& .\.venv\Scripts\python.exe scripts\seed_demo.py
& .\.venv\Scripts\python.exe scripts\seed_demo.py
```

第二次应提示演示数据已经存在，而不是报唯一约束错误。

### 当日验收

- PostgreSQL 容器为 healthy；
- vector extension 创建成功；
- Alembic 当前版本正确；
- Seed 可以重复执行；
- 业务数据保存在 Docker Volume 中。

## Day 2：Repository 契约和租户隔离

### 目标

证明 InMemory 和 PostgreSQL 的核心行为没有发生分叉。

### 操作

```powershell
$env:RUN_POSTGRES_TESTS = "1"
$env:TEST_DATABASE_URL = "postgresql+psycopg://market_agent:market_agent@localhost:5433/market_agent"
& .\.venv\Scripts\python.exe -m pytest tests\integration\test_repository_contract.py -m integration -vv
```

测试会自动创建类似下面的隔离 schema：

```text
test_market_<随机ID>
```

结束时自动删除测试 schema，不会清空 `public` 中的演示数据。

重点观察：

- 会话能正确 round-trip；
- 消息、任务、事件在一个事务中出现；
- 其他租户无法读取会话或订单；
- 新 Repository 实例仍能读取旧数据。

### 当日验收

- 集成测试没有 skipped；
- 所有测试通过；
- 测试完成后没有遗留 `test_market_*` schema。

## Day 3：并发确认与任务领取

### 目标

证明数据库唯一约束和行锁确实能抵抗并发，而不是仅在内存锁中有效。

### 当前已有测试

`test_two_concurrent_confirmations_create_one_execution` 使用两个线程和两个数据库 Session 同时确认同一 ActionPlan。

预期：

- 两个调用最终返回相同 `execution_id`；
- 数据库只有一个 ActionExecution；
- 数据库只有一个有效的幂等键；
- 不会创建两张工单。

### 已实现的任务领取测试

测试会运行两个 Worker，同时调用 `claim_task`：

```text
Worker A ─┐
          ├─ 同时竞争一条 queued task
Worker B ─┘
```

预期只有一个 Worker 得到任务，另一个返回空。

然后人工把 `lease_expires_at` 调整到过去，再由第二个 Worker 领取，验证崩溃恢复。

### 当日验收

- 并发确认 20 轮没有重复记录；
- 双 Worker 20 轮没有重复领取；
- 过期租约能够恢复；
- running 且租约未过期的任务不能被抢占。

## Day 4：API/Worker 重启恢复

### 目标

验证进程消失不会造成业务事实丢失。

### 操作流程

启动 API 和 Worker：

```powershell
$env:PERSISTENCE_BACKEND = "postgres"
$env:DATABASE_URL = "postgresql+psycopg://market_agent:market_agent@localhost:5433/market_agent"
$env:AUTH_MODE = "development_headers"
$env:ALLOW_DEV_AUTH = "true"

& .\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

另一个终端：

```powershell
$env:PERSISTENCE_BACKEND = "postgres"
$env:DATABASE_URL = "postgresql+psycopg://market_agent:market_agent@localhost:5433/market_agent"
& .\.venv\Scripts\python.exe -m app.runtime.worker
```

创建会话、查询订单、创建工单后记录：

```text
conversation_id
task_id
action_execution_id
ticket_id
```

停止 API 和 Worker，但不要停止 PostgreSQL。重新启动两个进程，再通过 GET 接口读取这些 ID。

### 当日验收

- 会话消息仍存在；
- 工单仍存在；
- ActionExecution 仍存在；
- queued/retry_wait 任务可以继续执行；
- running 任务只能在租约过期后恢复；
- 不产生第二张相同工单。

## Day 5：索引、查询计划与周验收

### 目标

确认数据库不是“能查到”，而是使用了正确的访问路径。

### 已建立的关键索引

| 索引 | 用途 |
| --- | --- |
| `uq_order_external` | 按租户和外部订单号查询 |
| `uq_execution_idempotency` | 写动作幂等 |
| `uq_handoff_active_conversation` | 每会话最多一个 active handoff |
| `uq_policy_current_scope` | 每作用域最多一个当前政策 |
| `ix_conversation_tenant_customer_updated` | 客户会话列表 |
| `ix_message_conversation_created` | 会话消息时间线 |
| `ix_handoff_queue_order` | 客服待办队列排序 |
| `ix_ticket_tenant_customer_created` | 客户工单列表 |
| `ix_runtime_task_claim_ready` | queued/retry_wait 任务领取 |
| `ix_runtime_task_recover_lease` | running 任务租约恢复 |

集成测试会对订单查询执行 EXPLAIN，并要求计划出现 `uq_order_external`。

真实数据量增大后还应对以下 SQL 执行 `EXPLAIN (ANALYZE, BUFFERS)`：

- 客户最近会话列表；
- 客服 handoff 队列；
- Worker claim task；
- 过期租约恢复；
- ActionExecution 幂等查询；
- 当前发布政策查询。

注意：`ANALYZE` 会真实执行查询。对写 SQL 只使用 EXPLAIN，不要随意使用 EXPLAIN ANALYZE。

### 当日验收

```powershell
& .\scripts\verify_week1.ps1 -KeepPostgresRunning
```

保存输出，并将进度文档中的第一周状态更新为“验收完成”。

## 六、常见失败及处理

### 1. 5433 端口被占用

检查：

```powershell
Get-NetTCPConnection -LocalPort 5433 -ErrorAction SilentlyContinue
```

不要直接结束未知进程。确认占用者后，选择停止已有 PostgreSQL，或修改 Compose 的宿主机端口并同步修改数据库 URL。

### 2. vector extension 创建失败

确认镜像为：

```text
pgvector/pgvector:pg16
```

不要临时换成普通 `postgres:16`，否则迁移中的 Vector 类型会失败。

### 3. 集成测试全部 skipped

确认当前终端设置了：

```powershell
$env:RUN_POSTGRES_TESTS = "1"
```

### 4. 跨线程并发测试失败

先检查是否错误地把测试切换成 SQLite。并发验收必须使用真实 PostgreSQL，SQLite 不能替代行锁、部分索引或 `SKIP LOCKED` 行为。

### 5. Seed 第二次执行失败

Seed 必须幂等。不要通过删除 Volume 掩盖错误，应修复具体实体的 upsert 或存在性判断。

## 七、本周完成定义

以下项目必须全部打勾：

- [x] Docker PostgreSQL/pgvector healthy；
- [x] Alembic 从空数据库升级成功；
- [x] Seed 连续执行两次成功；
- [x] PostgreSQL 集成测试没有 skipped；
- [x] 跨租户读取全部被阻断；
- [x] 双并发确认只创建一个执行记录；
- [x] 双 Worker 不重复领取任务；
- [x] 新 Repository 与 API 进程重启后数据恢复；
- [x] 过期任务租约能够被新 Worker 恢复；
- [x] 关键索引全部存在；
- [x] 关键查询 EXPLAIN 使用预期索引；
- [x] 全部单元测试和 Ruff 通过；
- [x] 验收结果已经写回进度文档。

第一周现已标记为“验收完成”。Worker 独立进程的强制崩溃演练归入第 2 周可靠性任务。

---

## 八、学习解读：第一周实际上完成了什么

本章用于解释本周工作背后的工程目的和技术术语。第一周不是为了给 Agent 增加模型能力、RAG 或更多渠道，而是把项目从“进程内 Demo”升级为“以数据库为事实源、可并发、可恢复”的服务基础。

### 8.1 从内存 Demo 到可恢复服务

项目最初使用 InMemory Repository：订单、会话、工单、动作计划、人工转接和审计记录都保存在 Python 进程内存中。

```text
服务启动
  -> 数据存在
服务重启
  -> 数据消失
```

第一周将核心数据放入 PostgreSQL 后，链路变为：

```text
API / Worker
  -> Repository
  -> PostgreSQL
  -> Docker Volume
  -> API / Worker 重启后仍能读取会话、工单、动作和任务
```

这意味着客户创建工单后，即使 API 重启、Worker 崩溃或部署新版本，系统仍能找到原会话、用户消息、ActionExecution、Ticket、待处理任务和审计事件。

### 8.2 本周按五天分别做了什么

| 天数    | 主要工作                                        | 验证的核心问题                   |
| ----- | ------------------------------------------- | ------------------------- |
| Day 1 | 启动 PostgreSQL/pgvector、执行 Alembic、连续两次 Seed | 数据库能否可重复初始化并保存数据          |
| Day 2 | PostgreSQL Repository 契约测试、隔离 schema、租户测试   | 内存与数据库实现是否行为一致，数据会不会串租户   |
| Day 3 | 并发确认、双 Worker 领取、过期租约恢复                     | 重复点击和多个 Worker 会不会重复执行写动作 |
| Day 4 | API/Worker 重启恢复                             | 进程消失后会话、工单和任务会不会丢失或重复     |
| Day 5 | 索引、EXPLAIN、全量验收                             | 数据量增长后查询是否走正确路径，所有证据是否完整  |

### 8.3 PostgreSQL、pgvector、Docker Volume

**PostgreSQL** 是关系型数据库，适合保存存在强关联和强约束的市场 Agent 业务数据：

```text
Tenant
  -> User / Membership
  -> Conversation
  -> Message
  -> Ticket
  -> ActionPlan
  -> ActionExecution
  -> AuditEvent
```

它提供外键、唯一约束、事务、行锁和索引。它们保证的不是“能把数据存进去”，而是防止不合法和相互矛盾的数据出现。

例如：

- 一个订单必须属于一个租户；
- 一个工单必须关联一个合法会话；
- 同一个租户不能用同一个幂等键重复执行动作；
- 两个 Worker 不能同时处理同一条任务。

**pgvector** 是 PostgreSQL 的向量扩展。普通 SQL 擅长按订单号、租户 ID 等精确值查询；政策 RAG 则需要按语义相似度找资料。pgvector 使 PostgreSQL 可以保存文本的 Embedding 向量并进行相似度检索，因此会话、工单、政策文本、向量索引和审计可以在同一数据库体系中管理。

**Docker** 是可复制运行环境。镜像是安装好的 PostgreSQL/pgvector 模板，容器是启动后的实际数据库进程；**Docker Volume** 是容器外部的持久磁盘空间。没有 Volume 时删除容器会删除数据；使用 Volume 后，停止或重建容器不等于丢失数据库。

### 8.4 Alembic、Migration 与 Seed

**Alembic** 是 SQLAlchemy 项目的数据库迁移工具。它将数据库结构变化版本化，例如从只有 Conversation 表逐步演进为 Ticket、ActionExecution、Handoff、RuntimeTask、PolicyVersion 等表。

```text
空数据库
  -> alembic upgrade head
  -> 20261020_0001
```

- **Migration**：将数据库结构从旧版本升级到新版本的脚本；
- **head**：最新数据库结构版本；
- `alembic current`：查看当前数据库实际版本；
- `20261020_0001`：本项目第一版数据库结构的版本标识。

**Seed** 是初始化演示数据，例如 `tenant_demo`、`customer_001`、`order_1001` 和演示政策。Seed 必须是 **幂等的（Idempotent）**：执行一次和执行十次，最终数据库状态相同。

```text
第一次 Seed -> 创建演示租户、用户和订单
第二次 Seed -> 识别数据已存在，不重复插入，也不触发唯一约束错误
```

这使项目能够安全地用于容器重启、开发环境初始化、部署流程和自动化测试。

### 8.5 Repository Contract 与 Schema Isolation

**Repository** 是业务服务访问数据的统一门面。业务代码调用 `get_conversation`、`save_ticket`、`claim_task` 等方法，而不是在每个业务函数里直接拼 SQL。

这样做使业务规则不依赖特定存储实现：

```text
MarketAssistantService
  -> Repository Contract
     -> InMemory Repository（快速单测）
     -> PostgreSQL Repository（真实试点）
```

**Repository Contract Test** 验证两种底层实现的核心行为不分叉：保存会话后可以读取、跨租户读取被阻断、幂等键不重复执行，以及新的 Repository 实例能够读取旧数据。

**Schema** 是 PostgreSQL 数据库内部的逻辑命名空间，可理解为数据库内的文件夹：

```text
database
├── public
├── test_market_a1b2
├── test_market_c3d4
└── ...
```

集成测试为每次测试创建唯一的 `test_market_<随机ID>` schema，并在结束后删除它。这样测试不会破坏 `public` 中的演示数据，也不会被其他测试影响。

本周发现的“隔离 schema 对 public 可见”问题，意味着测试虽然创建了自己的空间，却仍可能读到公共演示数据，导致测试假通过或相互污染。修复后，测试只能看到自己的隔离数据。

### 8.6 Tenant Isolation：为什么每次查询都要带租户

**Tenant（租户）** 是多商家、多组织 SaaS 中的数据边界。市场 Agent 中，商家 A 的用户不能看到商家 B 的订单、会话、工单、政策、任务或工具凭证。

错误的查询只按订单号取数据：

```sql
WHERE order_id = ?
```

正确的查询至少包含租户范围：

```sql
WHERE tenant_id = ?
  AND order_id = ?
```

用户级资源还应增加客户归属条件。租户隔离是市场 Agent 的安全底线：模型回复再准确，只要跨租户读取发生，就可能泄露其他商家的客户数据。

### 8.7 并发、事务、幂等与唯一约束

真实服务会遇到重复点击、网络重试、Webhook 重复投递和多个 Worker 同时运行。

```text
请求 A ─┐
        ├─ 同时确认同一个 ActionPlan
请求 B ─┘
```

如果没有保护，可能产生两张工单、两次补偿或两笔退款申请。

**Idempotency Key（幂等键）** 是客户端或服务端为同一业务动作生成的稳定标识。项目使用：

```text
(tenant_id, idempotency_key)
```

作为唯一边界。相同租户中，相同幂等键只能对应一条 ActionExecution。

**Unique Constraint / Unique Index（唯一约束/唯一索引）** 是数据库级保证。即使业务代码出现并发竞争，数据库也会拒绝第二次相同写入。`uq_execution_idempotency` 就是防止动作重复执行的关键保护。

**Transaction（事务）** 表示一组数据库操作要么全部成功，要么全部回滚。例如创建执行记录、创建任务和写审计事件应处在同一个事务中，不能出现“工单已创建但没有执行记录”或“执行记录存在但任务未入队”的半完成状态。

### 8.8 Worker、行锁、SKIP LOCKED 与 Lease

**Worker** 是处理耗时后台任务的独立进程。API 负责快速校验和入队，Worker 负责调用外部系统、检索、模型推理、创建工单和发送通知。

**行锁（Row Lock）** 是数据库对特定记录的临时独占保护。项目使用 PostgreSQL 的：

```sql
FOR UPDATE SKIP LOCKED
```

含义是：

- `FOR UPDATE`：Worker 领取任务时锁住该任务行；
- `SKIP LOCKED`：若另一 Worker 已锁住某条任务，不等待，直接跳过并尝试领取其他可执行任务。

结果是：

```text
Worker A ─┐
          ├─ 竞争同一条 queued task
Worker B ─┘

Worker A -> 获得任务
Worker B -> 不会重复获得同一任务
```

**Lease（租约）** 是 Worker 的限时执行权。任务记录 Worker ID、租约到期时间和心跳时间：

```text
worker_id
lease_expires_at
heartbeat_at
```

Worker 正常工作时会发送 **Heartbeat（心跳）** 续租；若 Worker 崩溃，租约到期后其他 Worker 才可以恢复该任务。这样既能恢复故障任务，又避免仍在运行的 Worker 被错误抢占。

SQLite 不能替代这项验收，因为它无法真实模拟 PostgreSQL 的行锁、`SKIP LOCKED`、部分索引和多 Worker 竞争行为。

### 8.9 重启恢复与 Unknown Needs Reconciliation

Day 4 验证的是：进程可以消失，业务事实不能消失。

```text
API + Worker 正常运行
  -> 创建会话、查询订单、创建工单
  -> 记录 conversation_id / task_id / execution_id / ticket_id
  -> 停止 API 和 Worker
  -> 保持 PostgreSQL 运行
  -> 重启 API 和 Worker
  -> 用原 ID 读取原事实并恢复可执行任务
```

**Unknown Needs Reconciliation** 是外部写操作超时后的重要状态。例如系统向外部工单服务发送创建请求后，外部服务可能已经成功创建，但网络超时导致本系统没有收到响应。

```text
请求发出
  -> 外部可能已成功
  -> 本系统收到超时
  -> 结果未知
```

此时不能直接重试，因为可能创建第二张工单。正确行为是用幂等键向外部系统查询或人工对账，确认真实结果后再进入成功、失败或补偿状态。

### 8.10 索引、EXPLAIN 与性能验证

**Index（索引）** 可以理解为数据库目录。没有索引时，数据库可能逐行扫描整张表；有合适索引时，可以快速定位目标记录。

本周的索引不仅为了更快，也是业务正确性的一部分：

| 索引 | 解决的问题 |
| --- | --- |
| `uq_order_external` | 在租户范围内按外部订单号定位订单 |
| `uq_execution_idempotency` | 相同动作不能重复执行 |
| `uq_handoff_active_conversation` | 一个会话不能出现多个 active handoff |
| `uq_policy_current_scope` | 一个作用域不能同时出现多个当前政策 |
| `ix_conversation_tenant_customer_updated` | 快速加载客户最近会话 |
| `ix_message_conversation_created` | 按时间加载会话消息 |
| `ix_handoff_queue_order` | 客服队列按优先级和时间排序 |
| `ix_ticket_tenant_customer_created` | 查询客户工单列表 |
| `ix_runtime_task_claim_ready` | Worker 快速领取 queued/retry_wait 任务 |
| `ix_runtime_task_recover_lease` | 快速找到租约过期任务 |

**EXPLAIN** 让 PostgreSQL 展示计划使用怎样的查询路径。订单查询应使用 `uq_order_external`，而不是执行 `Seq Scan`（全表扫描）。

**EXPLAIN ANALYZE, BUFFERS** 会实际运行 SQL 并报告耗时和内存/磁盘页读取情况：

- `EXPLAIN`：只展示计划；
- `ANALYZE`：实际执行并统计；
- `BUFFERS`：显示读取了多少缓存页和磁盘页。

因为 `EXPLAIN ANALYZE` 会真实执行 SQL，对写 SQL 应谨慎使用；查询 SQL 可以在安全环境中使用它做性能验证。

### 8.11 本周发现的三个真实数据库问题

| 问题 | 含义 | 为什么内存测试难以发现 |
| --- | --- | --- |
| 无 ORM relationship 时外键父子记录 flush 顺序错误 | ORM 不知道父子对象依赖顺序，可能先写子记录导致外键失败 | 内存字典没有外键约束 |
| 隔离 schema 对 public 可见 | 测试意外读到公共演示数据，造成污染或假通过 | 内存测试没有 PostgreSQL schema/search path 概念 |
| Seed 身份 subject 与客户 ID 不一致 | OIDC Token 的 `sub` 与应用客户/成员映射不一致，登录后仍可能查不到资源 | 开发 Header 不会暴露真实身份映射问题 |

其中：

- **ORM**：Object-Relational Mapping，对象关系映射。SQLAlchemy 将 Python 对象转换为 SQL；
- **Relationship**：ORM 中声明父子对象关系的方式；
- **Foreign Key（外键）**：限制子记录必须引用存在的父记录；
- **Flush**：ORM 将当前内存改动发送给数据库、但尚未最终提交的过程；
- **OIDC subject / sub**：身份提供方为用户签发的稳定唯一 ID，应与应用中的成员映射正确对应。

### 8.12 第一周对市场 Agent 的意义

第一周建立的不是单纯数据库功能，而是市场 Agent 的安全地基：

```text
可信业务事实
+ 多租户隔离
+ 可审计写动作
+ 数据库级幂等保护
+ 多 Worker 并发安全
+ 进程重启恢复
+ 可验证查询性能
```

如果缺少这些能力，即使模型回答流畅，也可能发生：

- 重复建工单或重复补偿；
- 跨商家泄露订单；
- Worker 崩溃后任务永久卡住；
- API 重启后客户会话消失；
- 数据量增长后客服查询变慢；
- 外部系统超时后因盲目重试造成重复业务动作。

因此，第一周的成果可以概括为：

> 将 JayAgent-MarketAssistant 从“重启就忘、并发可能重复执行的 Agent Demo”，升级为“以 PostgreSQL 为事实源、可验证租户隔离、可抵抗并发、可恢复任务状态的市场 Agent 服务基础”。
