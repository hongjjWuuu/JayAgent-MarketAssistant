# JayAgent Market Assistant

面向电商售后场景的、可治理的多租户客户 Agent 试点。它不是“聊天 Demo”：订单事实、政策证据、写动作、人工接管和任务恢复分别由确定性边界负责。

## 已落地的纵向链路

- FastAPI 模块化单体，API 与 Worker 为同一代码库的两个进程；
- SQLAlchemy 2 + Alembic + PostgreSQL/pgvector，覆盖计划中的 21 张业务、运行时、治理和评测表；
- PostgreSQL `FOR UPDATE SKIP LOCKED` 任务领取、动态 Worker、续租心跳、旧执行者栅栏、指数退避、不可变死信重放和取消；
- 动作确认在单事务内创建执行记录与任务，`(tenant_id, idempotency_key)` 防止重复执行；
- Keycloak OIDC/JWKS 校验，Token 的 subject 必须在应用数据库中拥有有效租户成员关系；
- 政策草稿/发布/生效期，发布时切块并写入向量；PostgreSQL FTS + pgvector + RRF 检索；
- 稳定 Policy/Version/Chunk 三层身份与 v3 独立指标，多 Retrieval Profile 非破坏性索引和质量门禁激活；
- Answerability Gate：无已发布有效证据时拒绝确定回答并建议转人工；
- 受控 LangGraph：上下文 → 意图 → 参数 → 风险 → 工具/检索 → 回答/计划；具备步骤、LLM、工具和总时延预算；
- OpenAI-compatible/DeepSeek 结构化 Provider、严格 Citation 白名单、脱敏 LLM Trace 和确定性降级；
- Tool Gateway 元数据、输入 Schema、角色、风险、确认、重试和对账规则；
- 客户会话、SSE 事件、人工队列/领取/回复/解决、动作执行与工单接口；
- React/Vite 三视图：客户 Widget、人工客服 Console、政策评测与任务运行中心；
- Prometheus 指标、结构化请求日志、Redis 限流（Redis 故障时明确降级开放，不影响业务事实）；
- Docker Compose 编排 API、Worker、Web、PostgreSQL、Redis、Keycloak、迁移和演示数据。
- 可切换 Repository/HTTP 业务 Connector，外部写结果不确定时只按幂等键对账；
- 客户 SSE/刷新恢复、事实与动作卡片，客服 SLA/转派/上下文侧栏；
- 试点运营指标、Prometheus 告警、备份恢复与全新迁移验收脚本。

## 运行方式

### 完整 OIDC 试点

需要 Docker Desktop：

```powershell
Set-Location D:\JayAgent\JayAgent-MarketAssistant
docker compose up --build
```

入口：

- Web：http://localhost:5173
- API 文档：http://localhost:8000/docs
- Keycloak 管理台：http://localhost:8080（`admin/admin`，仅演示）
- 指标：http://localhost:8000/metrics
- Prometheus：http://localhost:9090

演示账号密码均为 `demo1234`：`customer_001`、`agent_001`、`admin_001`、`platform_admin_001`。

### 快速本地开发

默认配置使用内存 Repository 和显式开发认证，适合单测与接口开发：

```powershell
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
$env:INLINE_WORKER = "true"
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

另一个终端启动 Web：

```powershell
Set-Location .\web
npm install
npm run dev
```

开发请求头只能在 `APP_ENV=dev`、`AUTH_MODE=development_headers` 且 `ALLOW_DEV_AUTH=true` 时使用。生产环境检测到开发认证会拒绝启动。

## 关键 API 行为

`POST /api/v1/conversations/{id}/messages` 只持久化消息并返回 `202`、`message_id`、`task_id`。Worker 产生的回复通过会话查询或 SSE 获取。

`POST /api/v1/action-plans/{id}/confirm` 只产生幂等执行记录并入队。外部写超时进入 `unknown_needs_reconciliation`，必须先按幂等键对账，不能直接重试。

补偿不会直接发放：`submit_compensation` 只创建高优先级人工审批工单。

完整接口和状态机见 [架构说明](docs/ARCHITECTURE.md)，运维步骤见 [试点运行手册](docs/RUNBOOK.md)，当前完成度和下一阶段安排见 [实施进度与优化路线](docs/IMPLEMENTATION_STATUS.md)。阶段资料包括 [第一周 PostgreSQL 验收](docs/WEEK1_POSTGRESQL_EXECUTION_PLAN.md)、[第二周 Worker 可靠性验收](docs/WEEK2_WORKER_RELIABILITY_EXECUTION.md)、[第三周 OIDC 授权验收](docs/WEEK3_OIDC_AUTHORIZATION_EXECUTION.md)、[第四周 RAG/Answerability 实施计划](docs/WEEK4_RAG_ANSWERABILITY_IMPLEMENTATION_PLAN.md) 与 [第四周实施验收](docs/WEEK4_RAG_ANSWERABILITY_EXECUTION.md)。

## 验证

```powershell
& .\.venv\Scripts\python.exe -m ruff check .
& .\.venv\Scripts\python.exe -m pytest -q
Set-Location .\web
npm audit --audit-level=high
npm run test
npm run build
```

PostgreSQL/Keycloak/Compose 集成测试需要本机 Docker；纯单元和 API 测试不依赖 Docker。

第 2 周一键验收：

```powershell
& .\scripts\verify_week2.ps1 -WorkerCount 4 -KeepPostgresRunning
```

第 3 周一键验收：

```powershell
& .\scripts\verify_week3.ps1 -KeepServicesRunning
```

Week 4.2、BGE 对照和 Week 5 验收：

```powershell
& .\scripts\verify_week42.ps1 -KeepServicesRunning
& .\scripts\verify_embedding_experiment.ps1 -Profile bge-m3-v1
& .\scripts\verify_week5.ps1
```

M3 试点交付验收：

```powershell
Copy-Item .env.m3-drill.example .env.m3-drill
# 编辑本地 .env.m3-drill，为 SIMULATOR_CONTROL_TOKEN 设置随机值；该文件不会提交到 Git。
& .\scripts\verify_m3_release.ps1
& .\scripts\verify_m3_closeout.ps1
& .\scripts\demo_pilot.ps1
```

## 当前诚实边界

- 默认 Connector 使用 Repository 高保真模拟；已提供 HTTP 适配边界，但仍需按目标商家 OMS/WMS/工单 API 完成字段映射；
- 向量默认保留可重复 Hash 基线；BGE-M3 Profile 只有在 100 条 v3 对照达标后才能激活；
- Week 4 最终质量门禁和 Week 5 Fake 80/80、DeepSeek 20/20 已完成；
- 未实现语音、企业微信、计费、Kubernetes 与跨区域容灾；
- Docker 集成验证需在安装 Docker Desktop 的机器上执行。
