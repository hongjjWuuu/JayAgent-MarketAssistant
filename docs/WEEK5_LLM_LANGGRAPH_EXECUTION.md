# Week 5 受控 LLM、LangGraph 与端到端评测实施记录

> 更新日期：2026-10-31  
> Alembic Head：`20261031_0019`  
> 阶段状态：**验收完成**

## 一、结论

第五周已经从“能调用模型”升级为“模型只能在受控边界内参与”的 Agent 执行系统：

```text
用户消息
→ 确定性安全预判
→ 一次结构化请求理解
→ 参数与预算校验
→ LangGraph 业务路由
→ Tool Gateway / Policy Gate / Policy Engine
→ 有证据回答、ActionPlan、追问或转人工
→ PostgreSQL 持久化
→ LLM Trace、Agent Run 与场景评测
```

模型不拥有租户、权限、订单归属、风险、政策资格、补偿金额或写操作决定权。2026-10-31 已使用现有 Jaycode 本地 DeepSeek 配置完成固定20条真实端到端 Smoke。

## 二、已实现能力

### 1. 统一结构化理解

- `understand_request()` 一次返回 intent、业务对象、操作、订单号、ACL 上下文、缺失字段和置信度。
- Pydantic 使用严格 Schema；未知字段、未知枚举和非法参数整体拒绝。
- 旧分类/参数接口只作兼容包装，正式图只调用统一接口。
- 每条消息最多两次 LLM：请求理解一次、政策表达一次。

### 2. LLM 前置安全预判

- 提示注入、忽略规则、跨租户、查询他人订单、绕过确认和伪造事实会在模型调用前阻断。
- 阻断结论写入审计；模型无法覆盖。
- 安全回复不暴露内部规则、Prompt 或权限实现。

### 3. 单一受控 LangGraph

正式图已经拆分订单、政策、工单、补偿和人工分支，旧重复 `_execute` 路径已删除。固定预算为 12 步、2 次 LLM、3 次工具、15 秒总耗时、800 Token 输出和最多 3 个引用。

- 订单事实只来自 Tool Gateway。
- 政策回答只使用 Gate 允许的 Chunk；越界引用或新增关键事实会退回抽取式回答。
- 工单只生成待确认 ActionPlan。
- 补偿只生成 Proposal 和人工审批 ActionPlan。
- 外部写超时进入既有对账流程，不直接重放写操作。

### 4. 补偿提案与人工审批

迁移 `0019` 新增 `compensation_proposal`。建议金额从已发布政策证据中由服务端计算并限制在 50 元以内，Proposal 30 分钟过期。客户端金额和模型金额都不是权威来源。

```text
补偿诉求
→ 本人订单校验
→ 补偿政策 Gate
→ 服务端计算 Proposal
→ 用户确认
→ Worker 幂等创建人工审批工单
```

系统不会自动退款、发券或到账。

### 5. Trace、Agent Run 与成本

- 每次模型调用记录脱敏 LLM Trace、真实 Prompt Hash、Token、时延、预算、允许引用和标准错误码。
- `agent_run` 记录整次图运行的意图、风险、结果、步骤、模型/工具调用、回退、错误、Snapshot、ActionPlan/Handoff 关联和估算成本。
- 单价通过本地环境变量配置，默认值为 0；密钥、Bearer Token、完整联系方式和思维链不进入数据库。
- 租户管理员只能读取本租户脱敏记录，platform admin 不能读取租户对话。

## 三、评测数据修订

`week5_agent/v2` 首次运行暴露了数据标注问题：它把所有政策问题都标为“必须回答”，把所有补偿问题都标为“必须生成 Proposal”。在补偿政策冲突、ACL 不匹配、缺会员等级和低分差场景下，这与第四周 Gate 的安全规则矛盾。

处理方式：

- v2 保持冻结，不篡改历史；
- 修复配置和路由后 v2 为 76/80，错误写操作 0；
- 新建 reviewed `week5_agent/v3`，只修正 4 条错误预期；
- v3 保持 80 个不同表达和固定 20 条 `live-smoke`；
- v3 在真实 PostgreSQL、BGE-M3 v10 和完整图上取得 80/80，错误写操作 0。

这种处理保留了失败证据，也避免为了“通过”而放宽 Gate。

## 四、实测证据

| 验收项 | 结果 |
| --- | --- |
| Alembic | `20261031_0019 (head)` |
| Week 5 Fake / reviewed v3 | 80/80 passed |
| 错误业务写操作 | 0 |
| Fake 全场景 Agent Run P95 | 390 ms |
| 确定性订单查询 P95 | 78 ms |
| 后端单元与 API | 97 passed |
| PostgreSQL/Worker/OIDC/RAG 集成 | 17 passed，5 skipped，20 deselected |
| 前端 Vitest | 2 passed |
| 前端生产构建 | passed |
| Ruff | passed |
| DeepSeek live smoke | 20/20 passed，错误业务写操作 0 |
| DeepSeek 模型 | `deepseek-v4-flash` |
| 真实模型调用 | 17个运行、22次调用；22条 Trace 全部成功 |
| 真实结构化失败/模型回退 | 0 / 0 |
| 安全前置阻断 | 3条，未发送给模型 |
| DeepSeek Token | 输入 16,614；输出 5,943 |
| DeepSeek Agent P95 / 最大值 | 4.109s / 4.297s |

## 五、验收入口

```powershell
# 80 条 Fake Provider 端到端评测
& .\scripts\verify_week5_fake.ps1

# PostgreSQL 与可靠性回归
& .\scripts\verify_week5_postgres.ps1

# 后端、前端与可选真实模型总验收
& .\scripts\verify_week5.ps1

# 配置本地 DeepSeek 后执行固定 20 条完整业务链路
$env:LLM_BASE_URL = "https://api.deepseek.com"
$env:LLM_MODEL = "<本地确认的模型名>"
$env:LLM_API_KEY = "<仅本地设置>"
& .\scripts\verify_week5.ps1 -RunLiveDeepSeekSmoke
```

脚本不会打印或持久化 API Key。真实 Smoke 调用的是 Agent Evaluation Worker，不再是单独的意图分类测试。

## 六、最终验收结论

最终 Run `eval_61fa5f813d734b01bbde7772e48bf7e2` 取得20/20。第一次真实运行虽然业务结果20/20，但15个结构化输出触发回退，因此没有被作为完成证据；强化允许枚举和完整 JSON Schema 后复跑，22次 Trace 全部成功。

- DeepSeek 固定20条端到端 Smoke 全部没有错误业务动作；
- 未确认写操作为0；
- 高风险补偿自动执行为0；
- Citation 越界、模型结构错误和模型回退均为0；
- 3条注入/越权请求由安全预判阻断，未进入模型；
- Agent P95为4.109秒，低于8秒目标，总耗时低于15秒预算。

第五周状态正式更新为：**验收完成**。
