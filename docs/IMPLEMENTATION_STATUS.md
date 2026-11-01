# JayAgent-MarketAssistant 实施进度、当前阶段与优化路线

> 更新日期：2026-11-01  
> 项目目录：`D:\JayAgent\JayAgent-MarketAssistant`  
> 当前版本：`0.6.0-rc1`  
> 当前定位：面向电商售后场景的多租户市场 Agent 单机试点

## 一、结论摘要

项目已经形成可重复启动、可故障演练、可浏览器验收的 M3 单机试点准入包，但还不能称为生产完成。

目前已打通的主链路是：

```text
客户创建会话
→ 消息持久化并进入任务队列
→ Worker 领取任务
→ LangGraph 判断意图与风险
→ 查询订单事实 / 检索已发布政策
→ 生成回复或待确认 ActionPlan
→ 用户确认写操作
→ Worker 幂等执行
→ 创建工单或人工审批任务
→ 客服携带上下文接管
→ 管理员发布政策并查看基础评测结果
```

已经解决了原骨架最关键的结构问题：Service 不再绑定内存 Store、API 与执行过程解耦、写动作具备幂等边界、人工接管形成完整流程、身份不能仅依靠前端声明、政策问答具备版本与证据门控。

第四周 RAG 质量门禁现已完成，当前工作的重点应从“继续修补检索实验”转为：

1. 保持已通过的 PostgreSQL、Redis、Keycloak、API、Worker、Web 全栈基线；
2. 保持高保真业务 Simulator、Connector 幂等与 lookup-only 对账基线；
3. 保持 Gold Set、业务指标与故障演练可重复执行；
4. 在本项目进程显式配置密钥后完成最终 DeepSeek live smoke；
5. 门禁通过后进入一轮小规模真实用户试点。

## 二、当前阶段判断

### 2.1 所处阶段

当前位于：

```text
M0 方向验证       已完成
M1 内存业务骨架   已完成
M2 工程化纵向切片 已完成
M3 单机集成试点   准入收口（仅剩 DeepSeek live smoke 重跑） ← 当前阶段
M4 小规模用户试点 尚未开始
M5 生产运营       不在本轮范围
```

独立 Compose 演练环境已联合启动，真实 Keycloak 四角色、PostgreSQL 迁移、双 Worker、Web、Simulator 与 Prometheus 均已验证。RAG、Connector、业务指标和七项故障演练已经收口；当前唯一未执行的最终门禁是本轮 DeepSeek 20 条 live smoke。

### 2.2 当前成熟度评分

| 能力维度 | 当前成熟度 | 判断 |
| --- | ---: | --- |
| 业务场景闭环 | 75% | 订单、物流、政策、工单、补偿审批、人工接管已形成主流程 |
| 后端架构 | 80% | 模块化单体、Repository、Worker、状态模型和工具边界已建立 |
| 数据持久化 | 85% | ORM、迁移、事务、真实 PostgreSQL 契约/并发/索引及 API 重启恢复已通过 |
| 多租户与安全 | 88% | 真实 OIDC、稳定客户映射、队列授权和四角色边界已通过，仍缺 MFA/SCIM 与字段级脱敏 |
| RAG 与知识治理 | 92% | Gate v4、三层指标、不可变 Profile、三套冻结数据集与回滚门禁已完成；BGE-M3 v10 已激活 |
| Agent 编排 | 95% | 单次结构化理解、安全预判、受控 LangGraph、补偿人工审批、Agent Run 与安全降级已实现；DeepSeek 20/20 已通过 |
| 任务可靠性 | 92% | 4 Worker、长任务续租、强杀恢复、执行栅栏、死信重放和对账已通过真实 PostgreSQL 验收 |
| 人工客服 | 82% | 排队、原子领取、回复、释放、解决、转派、SLA 和上下文侧栏已实现；在线状态与并发上限延期 |
| 前端产品形态 | 78% | 客户 SSE、刷新恢复、引用/订单/动作/工单卡片和移动端布局已实现；仍需真实用户可用性验证 |
| 评测与运营 | 93% | Week 4 三套质量集、Week 5 Fake 80/80 和 DeepSeek 20/20 均通过；新增试点聚合指标接口与管理页 |
| 可观测与运维 | 88% | API/Worker Prometheus targets、10 条告警、7 项故障演练、事务失败与隔离备份恢复均已实测 |
| 自动化测试 | 96% | 后端 113/113、PostgreSQL 18/18、OIDC 5/5、前端 2/2、完整 Playwright OIDC 1/1 通过 |

综合判断：约完成原 10 周计划的 **99%**。当前位于 **M3 单机集成试点准入收口**：独立演练环境、模拟 Connector、OIDC 业务 E2E、7 项故障演练、Prometheus、备份恢复和完整回归均已落地。进入 M4 前只剩用本项目进程显式提供的 `LLM_API_KEY` 执行最终 DeepSeek 20 条 live smoke；不能从其他项目复制密钥。

## 三、原 10 周计划完成情况

### 第 1 周：PostgreSQL 持久化基础

状态：**验收完成（2026-10-20）。**

已完成：

- 建立 `MarketRepository` Protocol；
- Service 只依赖 Repository 接口；
- 保留 InMemory Repository 用于快速单元测试；
- 实现 PostgreSQL Repository；
- 建立 SQLAlchemy 2 数据模型；
- 建立 Alembic 初始迁移；
- 建立可重复演示数据初始化脚本；
- 建立动作执行幂等唯一约束；
- 消息、任务与事件具备原子入队接口；
- 动作确认、执行记录和任务在同一事务中创建。
- 已新增真实 PostgreSQL Repository Contract Test；
- 已新增真实数据库双并发确认测试；
- 已新增新 Repository 实例的数据恢复测试；
- 已新增关键索引存在性和订单查询 EXPLAIN 验证；
- 已新增 `scripts/verify_week1.ps1` 一键验收脚本；
- 已新增高频读取、客服队列和任务领取复合索引；
- Docker PostgreSQL/pgvector 容器健康检查通过；
- Alembic 已升级至 `20261020_0001 (head)`；
- Seed 连续执行两次成功；
- PostgreSQL 契约、租户隔离、并发幂等、双 Worker、恢复和索引共 7 项测试通过；
- 普通后端回归 15 项通过，Ruff 通过；
- 独立 API 进程重启后成功恢复会话 `conv_34d01070294744cc9e4c48c87714921d`；
- 修复无 ORM relationship 时父任务与任务事件的显式 flush 顺序；
- 修复隔离测试 schema 误复用 public 表的问题；
- Seed 补充本地开发身份与 Keycloak subject 两组成员映射；
- 详细执行步骤见 `docs/WEEK1_POSTGRESQL_EXECUTION_PLAN.md`。

本周遗留不再阻塞验收：需要在第 2 周继续做 Worker 进程崩溃/重启演练，在第 3 周用真实 Keycloak Token 替代开发请求头验证身份映射。

### 第 2 周：完整状态机与持久化 Worker

状态：**验收完成（2026-10-22）。**

已完成：

- API 消息接口改为返回 `202`、`message_id` 和 `task_id`；
- 独立 Worker 处理 Agent 消息与业务动作；
- PostgreSQL `FOR UPDATE SKIP LOCKED` 领取任务；
- 任务租约、心跳、过期恢复、重试等待、死信和取消；
- Conversation、Action、RuntimeTask 状态枚举；
- Worker 处理结果写入会话事件；
- 外部写超时进入 `unknown_needs_reconciliation`；
- 测试覆盖租约过期恢复和重试进入死信。
- Conversation、Action、RuntimeTask 使用集中式 Transition Policy；
- 动态 Worker ID、Worker 心跳、任务续租和 owner 提交栅栏；
- 4 Worker 并发完成 100 个任务且无重复；
- 长任务超过初始租约时持续续租且 attempts 保持 1；
- 独立 Worker 进程被强制终止后，任务过期恢复且 attempts 为 2；
- 原 Worker 丢失租约后无法覆盖新 Worker 的结果；
- 死信任务查询、脱敏详情、不可变关联重放和取消 API；
- 并发重放由唯一约束保证只创建一个子任务；
- `reconcile_execution` 定时任务和 lookup-only Provider；
- unknown 执行的人工处理 API、审计和运营页面；
- 新增 `scripts/verify_week2.ps1` 一键验收；
- 详细实现与验收证据见 `docs/WEEK2_WORKER_RELIABILITY_EXECUTION.md`。

后续增强项：Prometheus Worker 离线告警、真实 Connector 对账适配器、死信处理 SLA 与原因统计。

### 第 3 周：Keycloak OIDC 与资源级授权

状态：**验收完成（2026-10-24）。**

已完成：

- Docker Compose 加入 Keycloak；
- 提供 realm import、客户、客服、租户管理员和平台管理员演示账号；
- API 验证 JWT 签名、issuer、audience、有效期和 subject；
- 应用数据库通过 `(tenant_id, subject)` 校验成员关系；
- 业务用户名与 JWT subject 分离；
- 开发请求头角色必须与 Repository 成员关系一致；
- 生产环境检测到开发认证时拒绝启动；
- 客户资源按照 `customer_id` 二次过滤；
- 审计 Actor 固定记录 JWT subject；
- OIDC 禁止从请求头补充 tenant，客户通过 `channel_identity` 映射；
- 新增客服队列成员模型，领取时在数据库条件中原子校验队列；
- platform admin 独立平台租户，仅允许全局 Worker/平台健康只读；
- `/auth/me` 成为前端角色与 capabilities 的唯一来源；
- 真实 Keycloak 四角色 Token 与请求头篡改测试通过；
- 错误签名、过期、issuer、audience 与未知 kid/JWKS 刷新测试通过；
- 详细证据见 `docs/WEEK3_OIDC_AUTHORIZATION_EXECUTION.md`。

### 第 4 周：政策版本、RAG 与 Answerability Gate

状态：**质量验收完成（2026-10-28）；`bge-m3-v10` 已受控激活，Hash 基线可回滚。**

已完成：

- 新增不可修改旧迁移的 `20261025_0004` 增量迁移；
- 草稿保存与在线索引彻底拆开，草稿不会生成 Document/Chunk；
- 发布时执行结构化切块、Embedding、索引写入和当前版本切换，失败不替换旧版本；
- 同租户、同作用域发布使用事务锁与唯一约束，并发发布后仅一个当前版本；
- 标题/段落优先的稳定切块，记录 ordinal、标题路径、内容哈希和 Embedding 元数据；
- 商品、品类、地域、渠道和客户等级 ACL 在数据库候选阶段过滤；
- lexical top 30 与 vector top 30 统一通过 `RRF(k=60)` 融合；
- 命中结果保留双路 rank、RRF score、作用域、版本、生效区间和匹配实体；
- Answerability Gate 升级为 `answer / clarify / handoff` 三态并提供稳定 reason code；
- 每次检索保存脱敏查询、候选、引用、过滤条件、版本、决策、置信度和时延快照；
- 评测案例、运行与逐案例结果全部持久化，由 `evaluation_run` Worker 异步执行；
- 提供可重复导入的 50 条中文 Gold Set 和政策种子；
- 管理页可查看政策版本/索引状态、RRF 调试结果、Gate 原因和评测任务；
- `reindex_policy` Worker 支持 Embedding 模型变化后的索引重建；
- 新增 `scripts/verify_week4.ps1` 与第 4 周 PostgreSQL 质量测试。

验收结果：

- 50 条 Gold Set 已入 PostgreSQL；
- Recall@5 = 100%，MRR = 1.00；
- 引用政策版本正确率 = 100%；
- 可回答场景 Gate 放行率 = 92.5%；
- 不可回答场景拒答/追问率 = 100%；
- false-answer rate = 0；
- 旧版本命中数、跨租户/ACL 泄漏数均为 0；
- 10,000 Chunk 本地检索存在冷/热抖动：热缓存复跑可低于 500ms，但本轮完整回归记录到 502.8ms；该门槛当前按未稳定通过处理；
- 后端 55 项通过，PostgreSQL 原有 13 项回归通过，第 4 周新增 3 项真实数据库测试通过；
- 前端 Vitest 2 项与生产构建通过。

Week 4.2 新增 `20261027_0006`：稳定 `policy_id` 与 `policy_version_id`、`chunk_id` 分层；v3 指标独立计算 Policy/Version/Chunk Accuracy，并将 forbidden、租户、ACL 和非法政策状态检查扩展到全部候选、快照和引用。`week4_gold/v2` 保持冻结，`scripts/seed_week42_gold.py` 负责派生并冻结 100 条 v3。

Week 4.2 同时新增 `20261027_0007` 多模型向量表和 Retrieval Profile 生命周期。Hash 向量保留为回滚基线，BGE-M3 只有在 v3 质量门禁、覆盖率及相对回归门槛全部通过后才能激活。详细见 `docs/WEEK4_2_EVALUATION_AND_EMBEDDING_EXECUTION.md`。

2026-10-26 实测：`0005 → 0008` 迁移成功；v3 的 Recall@5=100%、MRR=0.862、三层引用准确率均为 100%、安全泄漏指标均为 0、P95=60ms。Infinity/BGE-M3 服务已健康并返回 1024 维向量，但 BGE 的 false-answer rate=22.22%、不可回答拦截率=77.78%，因此 `bge-m3-v1` 被标记为 `rejected`，`hash-v1:tenant_demo` 继续保持 active。

2026-10-27 已完成第四周质量收口代码升级：新增迁移 `20261028_0009`、Gate v3、固定 hard-negative calibration/holdout 数据集、失败归因、单政策事实覆盖、ACL 明确不匹配转人工、Profile 校准/验证/激活三段门禁、双路检索分段时延、10,000 Chunk 冷热性能脚本及管理页质量视图。`bge-m3-v1` 保持 rejected 且禁止重新激活；新实验固定使用 `bge-m3-v2`。当前状态仍为 **质量优化中**：只有真实 PostgreSQL、Infinity、v3、holdout 和性能脚本全部通过后，才允许把 BGE v2 设为 active 并将第四周改为“质量验收完成”。详见 `docs/WEEK4_QUALITY_CLOSURE_EXECUTION.md`。

2026-10-27 本轮实测：`0008 → 0009` 迁移成功；拆分后的真实 PostgreSQL lexical/vector 查询通过集成用例；10,000 Chunk、50 次查询的冷启动为 344.37ms，排除 5 次预热后的稳态 P95 为 345.30ms，性能门槛已通过。BGE v2 首次 calibration 暴露 19 个失败（8 个业务范围分析漏判、9 个“证据不足”样例定义过宽、2 个冲突决策与预期不一致），因此按不可变规则将该 Profile 拒绝，Hash 继续 active。代码中的领域识别和新环境 hard-negative 种子已据失败报告收紧，但不篡改这次 rejected Profile 和历史 Run；后续若继续实验必须创建新 Profile 版本。

2026-10-28 已落实下一轮代码与评测编排：新增 `20261029_0010`、Query Analysis/Gate v4、80 条平衡 calibration 与 60 条独立 hard-negative v2、Profile 配置哈希、三 Run 一致性校验、`activation-readiness` 接口和 `bge-m3-v3` 一键验收脚本。v1/v2 被显式禁止重新索引、校准和激活。当前仍为 **质量优化中**：代码单元回归已经通过，但真实 PostgreSQL 迁移、BGE v3 三组评测、性能复测、全量回归和受控激活仍需取得证据。详见 `docs/WEEK4_GATE_V4_BGE_V3_EXECUTION.md`。

2026-10-28 真实 v3 验收结果：`0010` 及兼容迁移 `0011` 成功，新数据集冻结检查通过，BGE v3 索引覆盖 14/14；calibration 的 Recall@5=100%、MRR=0.8778、Answerability Recall=100%，但 False Answer=14%、Unanswerable Block Rate=86%，因此 v3 已按不可变规则 rejected，未继续执行 Gold/holdout 或激活。旧版本、跨租户、ACL 泄漏仍为 0，Hash 继续 active。失败暴露 5 个注入表达和 2 个显式冲突表达的前置识别缺口，代码已补强但不会复用 v3 重跑。数据库集成回归 17 项、Gate v4 增量测试 28 项、前端 2 项与构建均通过；10,000 Chunk 冷查询 353.25ms、稳态 P95 360.25ms，通过性能门槛。第四周继续标记为 **质量优化中**。

2026-10-28 最终质量收口：保留 `bge-m3-v1` 至 `v9` 的 rejected 历史，使用新 Profile `bge-m3-v10` 完成不可变验收。`week4_gate_calibration/v2` 80/80、`week4_gold/v5` 100/100、`week4_hard_negative/v3` 60/60 全部通过；Gold 的 Recall@5、MRR、Policy/Version/Chunk Accuracy、Answerability Precision/Recall 均为 100%，False Answer、旧版本、跨租户和 ACL 泄漏均为 0。10,000 Chunk 冷查询 356.50ms、稳态 P95 364.26ms；后端 92 项、PostgreSQL 集成 17 项、前端 2 项及生产构建通过。`bge-m3-v10` 已激活，Hash 16/16 保留为 ready，并完成一次 `BGE → Hash → BGE` 数据库回滚演练。详细证据见 `docs/WEEK4_FINAL_QUALITY_ACCEPTANCE.md`。

### 第 5 周：OpenAI-compatible LLM 与 LangGraph

状态：**验收完成（2026-10-31）。**

已完成：

- 建立 OpenAI-compatible Provider；
- 支持配置 `base_url`、API Key 和模型名称；
- 以一次 `understand_request` 同时完成严格意图和参数理解，未知字段与非法枚举被拒绝；
- 在 LLM 前执行安全预判，提示注入、跨租户、绕过确认和高风险直接写入请求不能进入模型；
- 建立 `load_context → precheck_security → understand_request → validate_parameters → assess_risk → route` 受控图；
- 订单、政策、工单、补偿和人工形成独立分支，旧的重复 `_execute` 路径已移除；
- 固定最大 12 步、2 次 LLM、3 次工具、15 秒总预算以及输入/输出上限；
- 模型只负责语言理解和有证据的表达；订单事实来自 Tool Gateway，政策引用必须属于 Gate 允许集合；
- 非法 JSON、空响应、网络失败、未知引用、事实越界或超预算全部走确定性回退或人工；
- 新增 `20261031_0019`，建立 `compensation_proposal` 和 `agent_run`，并补强 LLM Trace；
- 补偿金额只由 Policy Engine 从 Gate 允许证据计算，最高 50 元；确认后 Worker 只创建人工审批工单；
- Agent Run 保存模型、Prompt/Graph 版本、步骤、调用量、Token、时延、成本、回退、错误和业务关联；
- Worker 将 conversation/message/task/request ID 传入 Trace；
- 建立真实 PostgreSQL Agent Evaluation Worker，逐案例断言路由、风险、引用、Proposal、数据库写入和 Agent Run；
- 保留存在错误统一预期的 `week5_agent/v2` 失败历史（修复后 76/80）；
- 冻结 reviewed `week5_agent/v3`，80/80 通过，错误业务写操作为 0；
- 本次正式运行全场景 P95 为 390ms，确定性订单查询 P95 为 78ms；
- 使用 Jaycode 已有的本地 DeepSeek 配置完成固定20条真实端到端 Smoke，20/20 通过且错误业务写操作为0；
- 最终真实 Run 的17个非安全场景产生22次成功 LLM Trace，结构化失败和模型回退均为0；
- 3条注入/越权案例在 LLM 前阻断；输入 Token 16,614、输出 Token 5,943；P95 4.109秒，最大4.297秒；
- 后端 97 项、PostgreSQL/Worker/OIDC/RAG 集成 17 项、前端 2 项和生产构建通过。

尚未完成：

- 成本字段和单价配置已经建立，但本地单价默认 0，需按实际供应商价格配置；
- 后续需要按供应商正式价格配置成本，并把真实 Smoke 纳入发布前人工触发门禁。

### 第 6 周：客户聊天 Widget

状态：**试点产品化实现完成，E2E 验收待执行。**

已完成：

- React、TypeScript、Vite 前端；
- 客户创建会话；
- 发送消息和显示异步任务状态；
- 展示历史消息、订单事实和 Agent 回复；
- 转人工入口；
- 会话状态与控制权展示；
- OIDC 与开发认证两种前端适配；
- 浏览器实测订单查询主链路成功。

尚未完成：

- 已通过带 Bearer Token 的 Fetch Stream 消费 SSE，并按事件恢复会话、工单和任务状态；
- 已实现 ActionPlan 确认、政策引用、订单物流、补偿建议和工单进度卡片；
- 已使用 LocalStorage 恢复当前会话，并增加断线/降级提示与移动端断点；
- 尚缺完整无障碍检查、跨浏览器验证和真实用户可用性测试。

### 第 7 周：人工客服 Console

状态：**核心流程完成。**

已完成：

- 待接管列表；
- 按优先级和创建时间排序；
- 数据库级行锁原子领取；
- 防止不同客服重复领取；
- 展示前序会话摘要；
- 客服回复；
- 释放回队列；
- 解决并关闭；
- 人工接管后 AI 停止自动回复；
- 接管、回复、释放和解决写入事件及审计；
- 浏览器实测转人工和原子领取成功。

尚未完成：

- 已增加队列转派、转派原因和不可变转派历史；
- 已增加 SLA 截止时间、等待时长和超时展示；
- 已增加订单、政策引用、待处理动作和风险侧栏；
- 已记录领取、首次回复和解决时间并汇总首次响应/处理时长；
- 客服在线状态、并发会话上限和自动超时升级仍延期到 M4 试点反馈后。

### 第 8 周：租户管理、评测与运营

状态：**试点指标聚合与界面已完成，长期报表待试点数据验证。**

已完成：

- 政策创建与发布界面；
- 政策发布 API；
- 评测运行与查询 API；
- Evaluation 数据模型；
- 运营指标界面骨架；
- Tool Catalog 管理查询接口。

尚未完成：

- 成员和角色管理；
- 工具启停和租户级风险策略；
- Prompt、模型和政策版本对比；
- 场景集上传与管理；
- 已实现自助解决率、转人工率、一次解决率、首次响应、处理时长、P95、Token 和成本聚合；
- 评测结果的模型/知识/规则/工具/系统归因。

### 第 9 周：安全、可观测性与故障演练

状态：**M3 范围内验收完成。**

已完成：

- Prometheus `/metrics`；
- `/health/live` 与 `/health/ready`；
- 结构化 HTTP 请求日志；
- Request ID；
- Redis 用户级限流；
- Redis 不可用时显式 `degraded-open`；
- 外部写超时的不确定状态；
- 运维与故障恢复手册。
- API 与 Worker Prometheus targets；
- 10 条 Prometheus 告警规则并通过 `promtool`；
- PostgreSQL、Redis、LLM、Worker、Connector、容器重启与事务迁移失败等七项演练；
- 隔离 PostgreSQL 备份恢复。

尚未完成：

- 完整 Dashboard 仍需在 M4 根据真实流量完善；
- LLM Trace、Tool Trace 和审计仍需形成统一的跨对象查询体验；
- 敏感信息脱敏器和数据保留清理任务仍需实现；
- 限流还需要租户、接口和全局三级配额。

### 第 10 周：Docker Compose 试点交付

状态：**M3 准入包完成，最终 DeepSeek live acceptance 待执行。**

已完成：

- API、Worker、Web、PostgreSQL、Redis、Keycloak 服务编排；
- 独立迁移 Job；
- 独立演示数据 Seed Job；
- 持久卷、健康检查和启动依赖；
- Keycloak realm 和演示账号；
- `.env.example`；
- API 与 Web Dockerfile；
- 备份、恢复、升级和回滚说明。
- 独立 `jayagent-marketassistant-drill` Compose 项目与隔离端口/卷；
- 高保真 OMS/物流/工单 Simulator；
- 完整 OIDC 客户—客服业务 E2E；
- M3 一键准入、故障演练与发布报告脚本；
- 服务资源限制与 Prometheus 持久卷。

尚未完成：

- 最终 DeepSeek 20 条 live smoke 需要由本项目当前进程显式提供 `LLM_API_KEY` 后执行；
- 镜像安全扫描将在正式发布候选构建时执行；
- 10 分钟人工演示路径已经固化，自动旁白与录屏不属于准入硬门槛。

## 四、已经通过的验证

| 验证项 | 结果 |
| --- | --- |
| Ruff 静态检查 | 通过 |
| Pytest 后端非集成测试 | 113 项通过 |
| PostgreSQL 集成/可靠性测试 | 18 项通过 |
| 真实 Keycloak/API 测试 | 5 项通过，覆盖四角色与平台业务隔离 |
| Vitest 前端测试 | 2 项通过 |
| Playwright M3 OIDC 业务 E2E | 1 项通过，覆盖客户、客服与四角色禁止访问 |
| React 生产构建 | 通过 |
| npm 安全审计 | 0 个已知漏洞 |
| Keycloak realm JSON | 解析通过 |
| Docker Compose YAML | 解析通过 |
| M3 故障演练 | 7 项通过 |
| Prometheus | API/Worker targets up，10 条规则通过 |
| PostgreSQL 备份恢复 | 隔离恢复通过 |
| SQLAlchemy PostgreSQL DDL | 27 张表编译通过 |
| 浏览器订单查询 | 成功 |
| 浏览器转人工 | 成功 |
| 客服上下文接管 | 成功 |
| 客服原子领取 | 成功 |

重点自动化测试包括：

- 身份请求头缺失时拒绝；
- 开发环境不能伪造更高角色；
- 跨租户会话不可见；
- 消息先入队再由 Worker 生成回复；
- 并发确认只产生一个 ActionExecution；
- 租约未过期时不能被第二 Worker 领取；
- 过期租约可恢复；
- 重试超过上限进入 dead letter；
- 只有已发布有效政策能够回答；
- 高风险补偿只创建人工审批工单；
- 外部写超时不创建重复业务结果，进入对账状态；
- 生产环境拒绝开发认证配置。

## 五、与最终试点验收门槛的差距

| 发布门槛 | 当前状态 | 差距 |
| --- | --- | --- |
| 跨租户阻断率 100% | 单元/API、PostgreSQL 与真实 Keycloak Token 测试通过 | 需持续扩大攻击回归集 |
| 写操作幂等 100% | 内存与真实 PostgreSQL 双并发测试通过 | 需扩大压力轮次并加入外部 Connector |
| 未审批补偿执行数为 0 | 已满足当前实现 | 需加入持续回归与审计告警 |
| 人工接管上下文完整率 100% | 核心摘要可用 | 缺订单、政策、工具回执完整性统计 |
| 错误业务动作数为 0 | 当前测试集为 0 | 测试集规模不足，需真实 Gold Set |
| API/Worker 重启可恢复 | 结构已支持 | 尚未做容器重启演练 |
| P95 文本响应低于 8 秒 | 未测 | 需负载测试和真实 LLM |
| 确定性订单查询低于 1 秒 | 本地快速 | 缺正式压测报告 |
| 迁移、备份、恢复演练通过 | 文档已提供 | 未实操 |
| 后端、前端、E2E 全部通过 | 当前可运行范围通过 | 缺真实依赖集成测试 |

## 六、下一步优化优先级

### P0：进入单机集成试点前必须完成

#### 1. 完整 Docker Compose 联调

目标：证明所有服务在干净环境中可以一次启动。

任务：

- 已准备可用 Docker Desktop 环境；
- 执行 `docker compose up --build`；
- 验证迁移 Job 和 Seed Job 只执行一次且可重复；
- 已用 Keycloak 四个账号取得真实 Token 并访问 `/auth/me`；
- 验证 API、Worker、Web、PostgreSQL、Redis 和 Keycloak 健康状态；
- 重启 API 与 Worker，确认任务和会话不丢失；
- 保存完整联调记录和故障截图。

验收：在全新机器上按照 README 可在 15 分钟内启动并完成演示。

#### 2. PostgreSQL Repository 集成测试

目标：验证内存实现和 PostgreSQL 实现的行为一致。

任务：

- 建立 Repository Contract Test；
- 每个测试创建独立 schema 或数据库；
- 覆盖会话、消息、订单、工单、动作、人工接管、政策和任务；
- 使用真实并发连接验证 ActionExecution 唯一键；
- 使用两个 Worker 验证 `SKIP LOCKED`；
- 检查测试结束后的数据清理。

验收：同一套契约测试同时通过 InMemory 和 PostgreSQL Repository。

#### 3. OIDC 安全集成回归

目标：证明身份边界不是“代码看起来正确”。

任务：

- 获取 Keycloak 真实 Access Token；
- 测试错误签名、过期、错误 issuer、错误 audience；
- 测试有效 Token 但数据库无成员关系；
- 测试客户、客服、租户管理员之间的资源越权；
- 删除或禁用成员后验证访问立即受控；
- 测试生产环境请求头无法提升权限。

验收：跨租户、跨客户、跨角色的禁止操作阻断率为 100%。

#### 4. SSE 真正接入前端

目标：去掉固定延迟轮询，显示真实任务进度。

任务：

- 前端建立 EventSource/Fetch Stream 连接；
- 消费 `task.queued`、`task.claimed`、`assistant.message`、`task.failed`；
- 断线后携带 Last-Event-ID 重连；
- 使用 Redis Pub/Sub 唤醒连接，PostgreSQL 仍保存最终事件；
- 页面刷新后从数据库补齐遗漏事件。

验收：断网恢复、页面刷新和 Worker 重启后，用户仍能看到一致状态。

### P1：决定 Agent 是否具有市场价值

#### 5. 建立真实场景 Gold Set

至少准备 100 条中文业务样例：

- 订单查询 15 条；
- 物流异常 15 条；
- 商品损坏 15 条；
- 退换货政策 15 条；
- 补偿建议 10 条；
- 人工转接 10 条；
- 信息不足 10 条；
- 提示注入、越权和参数篡改 10 条。

每条样例同时断言：

- 回复是否正确；
- 检索证据是否正确；
- 工具选择是否正确；
- 数据库状态是否正确；
- 是否错误执行写动作；
- 是否应该转人工。

#### 6. 替换真实中文 Embedding

优化方向：

- 保留当前 `EmbeddingProvider` 边界；
- 接入可配置的中文 Embedding 服务；
- 政策发布时异步生成向量；
- 记录 Embedding 模型与版本；
- 支持重新索引；
- 对比 FTS、Vector 和 RRF 的 Recall@K/MRR；
- 根据业务数据重新设定 Answerability 阈值。

#### 7. 高保真业务 Connector

优先实现三个可替换 Connector：

- Order Connector：订单、商品项、客户归属；
- Shipment Connector：轨迹、预计送达、异常节点；
- Ticket Connector：预览、创建、查询、状态变更和幂等回执。

每个 Connector 必须支持：

- 超时；
- 有边界的读取重试；
- 写操作幂等键；
- 外部回执保存；
- 未知结果对账；
- 故障注入；
- 脱敏日志。

#### 8. 客户端产品化

优先补齐：

- ActionPlan 确认卡；
- 政策引用与版本卡；
- 订单与物流卡；
- 工单状态时间线；
- 转人工等待状态；
- 错误恢复与重新提交；
- 当前会话 URL 与刷新恢复；
- 移动端布局。

### P2：可靠性和运营增强

#### 9. 完整可观测链路

统一传播：

```text
request_id
→ conversation_id
→ message_id
→ task_id
→ action_execution_id
→ tool_call_id
→ external_receipt_id
```

增加：

- LLM Trace 持久化；
- Tool Call Log；
- Prompt、模型、政策和工具版本；
- P50/P95/P99 时延；
- 队列深度、租约过期、重试、死信；
- 自助解决率、转人工率和一次解决率；
- Token 成本与单次解决成本；
- Prometheus 告警规则和 Grafana Dashboard。

#### 10. 对账与死信治理

- 建立定时 Reconciliation Worker；
- 根据幂等键查询外部状态；
- 禁止未知状态自动重试写操作；
- 建立死信查看、原因分类、人工重放和取消界面；
- 记录每次人工干预的审计事件。

#### 11. 安全与隐私

- 实现手机号、地址、Token、密钥和订单敏感字段脱敏器；
- Prompt 输入前最小化个人信息；
- 定义审计和 Trace 留存周期；
- 建立用户数据删除流程；
- 限制政策上传文件类型与大小；
- 对提示注入、工具注入和越权参数做专项测试；
- 增加依赖、镜像与密钥扫描。

## 七、建议的下一轮四周执行计划（2026-10-26 更新）

### 第 1 周：DeepSeek 与 Week 5 场景执行器

- 配置本地 DeepSeek Key，只运行 20 条固定 Smoke；
- 将 80 条冻结契约接入统一场景执行器；
- 输出 Intent、Tool、Gate、Citation、ActionPlan、数据库状态和 Trace 报告；
- 对 Prompt 变更执行同版本对比，禁止模型越过 Tool/Gate。

本周输出：Week 5 80/80 Fake 报告、DeepSeek 20 条 Smoke 报告、失败归因。

### 第 2 周：实时体验与 Connector

- 前端接入 SSE；
- 建立订单、物流和工单 Connector Protocol；
- 增加故障注入；
- 实现对账任务第一版。

本周输出：实时事件链路图、Connector 契约测试、超时对账演示。

### 第 3 周：BGE 安全回归修复

- 分析 BGE 的 8 条 false-answer；
- 增加负向问题、冲突证据和不支持范围特征；
- 保持 Hash active，建立新的 BGE Profile 重跑 v3；
- 稳定 10,000 Chunk 冷缓存 P95，而不是放宽 500ms 门槛；
- 将 Embedding RPC 与数据库检索耗时拆分记录。

本周输出：BGE rejected 案例归因、修复后对照报告、查询计划与分段 P95。

### 第 4 周：产品化与试点演练

- 完成确认、引用、订单、工单卡片；
- 完成客服 SLA 和上下文侧栏；
- 完成数据库备份恢复和 Worker 崩溃演练；
- 运行 10 分钟演示流程；
- 邀请 3–5 名试用者执行真实任务。

本周输出：试点发布候选版本、演示录像/记录、问题清单和下一轮优先级。

## 八、开发时的原则

后续实现应继续遵守以下边界：

1. 模型负责理解和表达，不负责证明事实；
2. Policy Engine 负责资格、额度、时效和风险；
3. Tool Gateway 负责权限、参数和实际执行；
4. Repository 和数据库负责事实、幂等和恢复；
5. 人工客服负责争议、高风险和未知结果；
6. Redis 只能加速，不能成为业务事实源；
7. 不为展示效果提前引入更多 Agent；
8. 每增加一个功能，同时增加状态、审计、失败恢复和测试；
9. 优先证明一个场景可以稳定闭环，再扩展渠道和业务范围；
10. 所有“已完成”都必须对应代码、测试或真实演练证据。

## 九、下一步立即执行清单

- [x] 准备带 Docker 的开发环境；
- [x] 启动完整 Compose；
- [x] 执行 Alembic 迁移与演示数据初始化；
- [x] 使用 Keycloak 四种身份完成真实 Token 登录与权限校验；
- [x] 建立 PostgreSQL Repository Contract Test；
- [x] 运行两个 Worker 的并发领取测试；
- [x] 完成 API 重启与任务租约恢复测试；
- [x] 完成独立 Worker 进程强制崩溃恢复演练；
- [x] 前端接入带 OIDC Bearer Token 的 SSE Fetch Stream；
- [x] 设计并冻结 100 条 Gold Set v2/v3；
- [x] 接入并实测本地 BGE-M3，`bge-m3-v10` 已通过门禁并受控激活；
- [x] 建立可切换 Repository/HTTP Connector、幂等键和 lookup-only 对账边界；
- [x] 实现 unknown execution 对账 Worker；
- [ ] 完成备份与恢复实操；
- [x] 输出第一份 Hash/BGE 检索评测报告；
- [x] 复用本地安全配置完成 DeepSeek 20 条真实端到端 Smoke；
- [x] 将 Week 5 的 80 条 reviewed v3 契约接入统一执行报告器并取得 80/80 报告；
- [x] 已执行 `verify_fresh_migration.ps1`：全新临时数据库 `0001 → 0019`、Seed 和 head 检查通过；同时修复初始迁移引用 live ORM metadata 导致的重复列问题。

当上述 P0 项全部完成后，项目才正式进入“小规模用户试点”阶段。
