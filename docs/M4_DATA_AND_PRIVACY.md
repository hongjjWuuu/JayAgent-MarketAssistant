# M4 数据、隐私与留存说明

## 数据集

`m4_pilot/v1` 由代码确定性生成：5 个匿名客户、每人 6 张订单，共 30 张。订单覆盖待发货、运输中、延迟、已签收、商品损坏和超期售后，并带物流、商品和客户归属。`dataset_hash` 在发布冻结时保存，重复 Seed 必须产生相同哈希。

这些数据均为仿真事实，不导入真实 OMS/WMS，不包含真实个人信息。外部工单由 Simulator SQLite 卷保存，相同 `Idempotency-Key` 始终对应同一工单；未知写结果仅通过 `lookup_write` 对账。

## 数据分类

| 类别 | 示例 | 处理 |
| --- | --- | --- |
| 冻结发布证据 | Git SHA、镜像摘要、模型、Prompt、政策哈希 | 不可修改，创建新 Release 替代 |
| 匿名身份 | P01–P08、Keycloak subject | 仅用于受控授权，不记录真实姓名 |
| 业务仿真事实 | 订单、物流、工单 | PostgreSQL/Simulator 持久化与租户隔离 |
| 用户反馈 | 评分、困惑标签、意见 | 入库前脱敏 |
| Trace | LLM/Tool/Agent Run | 最小化、脱敏、14 天内容留存 |
| 聚合指标 | 完成率、P95、安全计数 | 可长期保留，不含可关联文本 |

## 脱敏

反馈会屏蔽中国大陆手机号、邮箱、常见 API Token 和显式地址字段。禁止在反馈中填写真实姓名、联系方式、密钥或真实订单。LLM Key 只存在于忽略提交的 `.env.m4-pilot` 进程环境中。

## 留存

- LLM Trace：试点关闭 14 天后删除；
- 会话文本、反馈意见和任务备注：关闭 30 天后清除或不可逆假名化；
- 审计事件：关闭 90 天后清理；
- 聚合 `pilot_metric_snapshot`：可长期保留；
- 最终备份：试点关闭时生成只读备份并按运维审批保存。

默认只预览清理范围：

```powershell
python -m scripts.cleanup_m4_retention --pilot-id <pilot_id>
python -m scripts.cleanup_m4_retention --pilot-id <pilot_id> --execute
```

执行要求 Pilot 已关闭。30 天清理保留结构化任务/指标证据，但移除消息内容、意见、标签和可关联 subject；快照不被该任务删除。

## 运维与访问

所有客户端通过 Caddy 本地 CA 的 HTTPS 访问。证书只用于受控局域网，不能用于公网。Caddy 根证书只安装到参与设备；离开试点后应从信任根中移除。数据库每日备份；固定窗口外不发布变更。
