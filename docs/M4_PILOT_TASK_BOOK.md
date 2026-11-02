# M4 内部受控试点任务书

## 1. 目标与边界

M4 用 4 周验证现有 Agent 是否能被内部用户和客服稳定使用，不增加 Agent 数量或业务渠道。试点只使用 `m4_pilot/v1` 仿真数据，参与者以 `P01–P08` 标识，不录入真实姓名、手机号、邮箱或地址。

范围包括订单/物流查询、政策问答、售后建单和人工接管；退款、发券、改价、语音、新渠道和自动高风险补偿不在范围内。

## 2. 角色与任务

| 编号 | 角色 | 数量 | 固定任务 |
| --- | --- | ---: | --- |
| P01–P05 | customer | 5 | 订单、物流、政策、工单、人工接管各 1 项 |
| P06–P07 | agent | 2 | 领取、回复、释放、转派、解决 |
| P08 | tenant_admin | 1 | 冻结版本、每日安全复核、关闭试点 |

每个客户至少完成 5 项任务；连续运行 3–7 天，累计会话不少于 50。每项反馈记录完成状态、1–5 分、困惑标签和脱敏意见。

## 3. 四周节奏

1. 第 1 周：启动隔离 Compose、安装局域网 HTTPS、Seed 仿真数据、冻结 `v0.7.0-m4-rc1`。
2. 第 2 周：客户和客服按任务卡执行第一轮，管理员每日生成指标快照。
3. 第 3 周：按 MODEL、KNOWLEDGE、RULE、TOOL、PERMISSION、INFRASTRUCTURE、PRODUCT、SUPPORT_OPERATION 归因并定点修复。
4. 第 4 周：连续运行，执行备份/恢复/回滚复验并生成 GO/NO-GO。

## 4. 启动步骤

```powershell
Copy-Item .env.m4-pilot.example .env.m4-pilot
# 填写随机密码、DeepSeek Key 和实际 Token 单价
& .\scripts\setup_m4_pilot.ps1 -InstallCertificate -ConfigureHosts -KeepServicesRunning
# 参与者明确同意后，设置人数与 PILOT_CONSENT_CONFIRMED=true，再冻结：
& .\scripts\setup_m4_pilot.ps1 -FreezeRelease -KeepServicesRunning
& .\scripts\verify_m4_readiness.ps1 -RunLiveDeepSeekSmoke
```

第二台 Windows 客户端使用管理员 PowerShell：

```powershell
& .\scripts\configure_m4_client.ps1 `
  -ServerAddress <试点主机局域网IP> `
  -CertificatePath .\caddy-root.crt
```

只有 Caddy 的 443 端口对局域网开放。PostgreSQL 与 Embedding 的回环端口仅供主机初始化，Redis、Worker、Simulator 控制面和 Prometheus 不对局域网暴露。

M4 使用项目内 `deploy/caddy/Dockerfile.hardened` 构建反向代理，固定 Caddy 源码版本并显式升级已知高风险 Go 依赖；发布前由 `verify_m4_readiness.ps1` 对 API、Web、Simulator 和 Caddy 的实际运行镜像执行安全门禁。

## 5. 每日操作

```powershell
& .\scripts\snapshot_m4_daily_metrics.ps1 -PilotId <pilot_id>
```

管理员每天检查：跨租户、未确认写入、自动高风险补偿、重复外部工单、死信、待对账、P95、任务完成和反馈。

## 6. 停止条件

出现以下任一情况立即停止，不继续采样：跨租户/他人订单可见、未确认写入、自动高风险补偿、重复外部工单、秘密或非匿名信息入日志、数据库无法恢复。

## 7. 关闭与结论

```powershell
& .\scripts\close_m4_pilot.ps1 -PilotId <pilot_id> -StopServices
```

GO 必须同时满足全部安全硬门槛，以及任务完成率 ≥85%、上下文完整率 ≥90%、满意度 ≥4.0、重复询问 ≤20%、客服首次响应中位数 ≤60 秒。现场数据不足时结论保持 PENDING，不得人工改写为 GO。
