# M4 候选版本安全扫描报告

日期：2026-11-02

## 结论

- `pip-audit`：0 个已知漏洞；
- `npm audit`：0 个已知漏洞；
- 仓库密钥模式扫描：未发现凭据；
- API、Web、Simulator：Docker Scout `--ignore-base --only-fixed` Critical/High 为 0；
- 加固 Caddy：Docker Scout 未发现漏洞，Critical/High/Medium/Low 均为 0；
- `.env.m4-pilot`、证书和 `output/m4` 运行证据均由 Git 忽略。

## 代理镜像加固

官方旧镜像在扫描时包含可修复的 Go 与 Alpine 依赖问题，因此 M4 不再直接使用旧标签。项目通过 `deploy/caddy/Dockerfile.hardened`：

1. 使用 Go 1.26.9 构建 Caddy 2.11.7；
2. 显式升级 `golang.org/x/net` 至 0.60.0；
3. 显式升级 `go-jose/v4` 至 4.1.6；
4. 使用升级后的 Alpine 3.23 最小运行时；
5. 由 Compose 构建并扫描实际候选镜像，而不是扫描无关标签。

## 门禁语义

M4 自动门禁阻断存在官方修复方案的 Critical/High 问题；完整报告仍保留用于审查尚无修复版本的基础镜像风险。任何扫描失败都不得冻结新的 `pilot_release`。
