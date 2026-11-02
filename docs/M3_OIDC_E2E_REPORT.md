# M3 OIDC 业务 E2E 报告

> 本轮结果：**passed**  
> 生成时间：2026-11-02T06:10:49.938334+00:00

## 覆盖链路

- 客户使用真实 Keycloak Authorization Code + PKCE 登录。
- 查询本人订单与物流，并查看带版本的政策证据。
- 创建、确认 ActionPlan，重复确认仍只生成一张外部工单。
- 刷新后恢复会话、待确认动作和事件游标。
- 客服登录后领取、回复、释放、重新领取、转派并解决会话。
- customer、agent、tenant_admin、platform_admin 均执行禁止访问断言。
- 请求头无法覆盖 Token 中的租户、主体或数据库角色。

## 自动化入口

完整链路由 `web/e2e/m3-release.spec.ts` 串行执行。失败时保留截图、视频和 Trace；最终准入结论以 `output/m3/m3-release-report.json` 为准。
