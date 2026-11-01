# 第 3 周执行记录：Keycloak OIDC 与资源级授权

> 验收日期：2026-10-24  
> Alembic revision：`20261024_0003`  
> 状态：真实 Keycloak、真实 JWT、真实 PostgreSQL 与全栈 Compose 验收通过

## 1. 本周解决的问题

本周不把“登录成功”当作安全完成，而是明确区分三层身份：

```text
Keycloak JWT sub             稳定的登录主体与审计 actor
        ↓
tenant_member                应用数据库中的租户成员、角色和 active 状态
        ↓
channel_identity / queue     客户业务身份或客服队列授权
        ↓
AuthorizationService        资源所有权与允许操作
```

JWT 中的用户名和 Realm Role 都不能直接取得业务权限；`preferred_username` 修改不会改变客户资源归属，Token 中的角色也不能覆盖应用数据库角色。

## 2. 身份与 Token 数据流

```text
React Web
  → Keycloak Authorization Code + PKCE
  → Bearer Access Token
  → AuthenticationMiddleware
       1. 只接受 Bearer
       2. RS256 + JWKS 验签
       3. 校验 issuer / audience / exp / iat / sub
       4. tenant_id 只取已验证 Token，忽略 X-Tenant-ID
       5. (tenant_id, sub) 查询 active tenant_member
       6. role 只取应用数据库
       7. customer 用 (tenant_id, oidc, sub) 解析 customer_id
  → AuthContext(subject, tenant_id, role, username, customer_id)
  → AuthorizationService
  → Repository 的 tenant 条件与原子资源更新
```

未知 `kid` 会触发一次新的 JWKS 获取，以支持密钥轮换；刷新失败按认证失败处理，不会退回开发请求头。日志、错误响应和审计均不保存完整 Token。

## 3. 权限矩阵

| 资源/操作 | customer | agent | tenant_admin | platform_admin |
| --- | --- | --- | --- | --- |
| 会话、订单、动作、工单 | 仅本人 | 本租户业务只读 | 本租户 | 禁止 |
| 创建会话/发送客户消息 | 仅本人 | 禁止 | 禁止 | 禁止 |
| Handoff 列表与领取 | 禁止 | 仅授权队列 | 本租户全部队列 | 禁止 |
| 政策、评测、任务治理 | 禁止 | 禁止 | 本租户 | 禁止 |
| Worker 详情 | 禁止 | 禁止 | 禁止 | 全局只读 |
| 运行摘要 | 禁止 | 禁止 | 本租户任务摘要 | 仅全局 Worker 摘要 |

同租户内的他人资源、跨租户资源和未授权队列尽量返回 404，避免资源枚举；角色本身不允许某类功能时返回 403。

## 4. 数据库与并发边界

迁移新增：

- `tenant_member.role` 合法角色检查约束；
- `support_queue_member(tenant_id, queue_id, subject, active)`；
- `(tenant_id, subject, active)` 队列成员索引。

已有 `channel_identity` 被正式用于 OIDC 客户绑定。演示数据新增客户 Keycloak subject → `customer_001`、客服 subject → `queue_general`，以及不加入业务租户的独立 `tenant_platform` 与 `platform_admin_001`。

客服领取不是“先查权限再更新”：PostgreSQL `SELECT ... FOR UPDATE` 同时包含 `tenant_id` 和允许的 `queue_id`，避免权限检查与领取之间的竞态绕过。

## 5. 后端与前端变化

后端：

- `AuthContext` 不再混用用户名、subject 和 customer ID；
- 新增集中式 `AuthorizationService`；
- 新增 `GET /api/v1/auth/me`，返回后端最终身份和 capabilities；
- OIDC 模式完全忽略 `X-Tenant-ID`、`X-User-ID`、`X-Role`；
- platform admin 只能访问 Worker 与平台健康信息；
- 租户治理接口统一只允许 tenant admin。

前端：

- OIDC 登录后先调用 `/auth/me`；
- 导航与工作区只依赖 `/auth/me`，不解析 Token Role；
- OIDC 模式隐藏手工角色切换；开发模式仍保留切换；
- 401 清理 Token 并重新登录，403 显示明确权限提示；
- 新增 platform admin 的只读 Worker 运维视图。

前端隐藏不是安全边界，所有权限仍由后端独立校验。

## 6. 演示账号

| 用户名 | 密码 | Token tenant | 应用角色 | 工作区 |
| --- | --- | --- | --- | --- |
| `customer_001` | `demo1234` | `tenant_demo` | customer | 客户服务中心 |
| `agent_001` | `demo1234` | `tenant_demo` | agent | 授权客服队列 |
| `admin_001` | `demo1234` | `tenant_demo` | tenant_admin | 租户治理 |
| `platform_admin_001` | `demo1234` | `tenant_platform` | platform_admin | 平台运维 |

Web Client 使用 Authorization Code + PKCE。Direct Access Grant 仅为自动化测试保留，真实生产 Realm 必须关闭。

## 7. 自动化测试与验收证据

已通过：

- Ruff；
- 49 项普通后端单元/API/JWT 测试；
- 13 项真实 PostgreSQL Repository/Worker 测试；
- 5 项真实 Keycloak/API 测试（四角色 Token + 平台业务隔离）；
- 2 项 Vitest；
- 4 项 Playwright 真实 Keycloak 浏览器登录测试；
- React/TypeScript 生产构建；
- 全栈 Compose：PostgreSQL、Redis、Keycloak、API、Worker、Web 全部启动。

JWT 测试覆盖正确 Token、错误签名、错误 issuer、错误 audience、过期 Token 和未知 `kid` 单次 JWKS 刷新。API 测试覆盖缺少 tenant、未绑定 customer、请求头篡改、数据库角色优先、跨队列和 platform admin 业务隔离。

一键复验：

```powershell
Set-Location D:\JayAgent\JayAgent-MarketAssistant
Set-ExecutionPolicy -Scope Process Bypass
& .\scripts\verify_week3.ps1 -KeepServicesRunning
```

## 8. 运维 Runbook

成员禁用：将对应 `tenant_member.active` 设为 false。后端每次请求查询 PostgreSQL，所以下一次请求立即失去访问权；不依赖 Realm Role 缓存。

密钥轮换：Keycloak 发布新 `kid` 后，API 首次遇到未知 `kid` 会重新拉取 JWKS。若仍失败，返回 401。轮换时观察 401 比例和 JWKS 网络错误，不允许临时开启开发认证。

Keycloak 故障：已有 Token 若本地 JWKS 缓存仍可用可继续验签；新登录和未知 key 会失败。恢复后验证 Realm 元数据、certs 和四账号 `/auth/me`。

## 9. 当前边界与下一步

本周完成的是身份真实性与资源授权基线，不包括成员管理 UI、SCIM、MFA、多 issuer、字段级脱敏或平台跨租户客服。下一阶段应进入政策 RAG/Answerability Gate 的真实质量评测，导入 Gold Set 并计算 Recall@K、MRR、引用正确率和过期政策命中率。
