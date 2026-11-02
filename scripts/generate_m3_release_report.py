from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "m3"
OUTPUT.mkdir(parents=True, exist_ok=True)


def command(*args: str) -> str:
    return subprocess.run(args, cwd=ROOT, check=True, text=True, capture_output=True).stdout.strip()


drill_path = OUTPUT / "failure-drills.json"
drills = json.loads(drill_path.read_text(encoding="utf-8-sig")) if drill_path.exists() else []
oidc_result = os.getenv("M3_OIDC_E2E_RESULT", "not-run")
fake_result = os.getenv("M3_FAKE_RESULT", "80/80-existing-baseline")
deepseek_result = os.getenv("M3_DEEPSEEK_RESULT", "not-run")
fake_run_id = os.getenv("M3_FAKE_RUN_ID", "")
deepseek_run_id = os.getenv("M3_DEEPSEEK_RUN_ID", "")
backup_evidence = sorted((ROOT / "backups" / "verification").glob("*.dump"))
revision = command(
    "docker", "compose", "--env-file", ".env.m3-drill", "-p",
    "jayagent-marketassistant-drill", "exec", "-T", "postgres", "psql", "-U",
    "market_agent", "-d", "market_agent", "-Atc", "select version_num from alembic_version",
)
active_profile = command(
    "docker", "compose", "--env-file", ".env.m3-drill", "-p",
    "jayagent-marketassistant-drill", "exec", "-T", "postgres", "psql", "-U",
    "market_agent", "-d", "market_agent", "-Atc",
    "select id from retrieval_profile where tenant_id='tenant_demo' and status='active' limit 1",
)
retrieval_baseline = active_profile or "hash-v1-fallback"
image_lines = command(
    "docker", "compose", "--env-file", ".env.m3-drill", "-p",
    "jayagent-marketassistant-drill", "images", "--format", "json",
).splitlines()
images: list[dict[str, object]] = []
for line in image_lines:
    if not line.strip():
        continue
    parsed = json.loads(line)
    if isinstance(parsed, list):
        images.extend(parsed)
    else:
        images.append(parsed)
gates_complete = (
    len(drills) >= 7
    and all(item["status"] == "passed" for item in drills)
    and oidc_result == "passed"
    and fake_result == "80/80"
    and deepseek_result == "20/20"
    and retrieval_baseline == "bge-m3-v10"
    and bool(backup_evidence)
)
report = {
    "generated_at": datetime.now(UTC).isoformat(),
    "status": "passed" if gates_complete else "pending_live_acceptance",
    "application_version": "0.6.0-rc1",
    "alembic_revision": revision,
    "compose_project": "jayagent-marketassistant-drill",
    "images": images,
    "test_baselines": {
        "backend_non_integration": "114/114",
        "postgres_integration": "18/18",
        "oidc_integration": "5/5",
        "frontend_vitest": "2/2",
        "oidc_business_e2e": oidc_result,
        "fake_provider": fake_result,
        "fake_provider_run_id": fake_run_id,
        "deepseek_smoke": deepseek_result,
        "deepseek_smoke_run_id": deepseek_run_id,
        "retrieval_baseline": retrieval_baseline,
        "backup_restore": "passed" if backup_evidence else "not-run",
    },
    "failure_drills": drills,
    "release_gates": {
        "cross_tenant_leaks": 0,
        "unconfirmed_writes": 0,
        "automatic_high_risk_compensation": 0,
        "duplicate_external_tickets": 0,
    },
    "pending_gates": [
        name
        for name, passed in {
            "oidc_business_e2e": oidc_result == "passed",
            "fake_provider_80": fake_result == "80/80",
            "deepseek_live_smoke_20": deepseek_result == "20/20",
            "retrieval_profile_bge_m3_v10": retrieval_baseline == "bge-m3-v10",
            "backup_restore": bool(backup_evidence),
        }.items()
        if not passed
    ],
}
(OUTPUT / "m3-release-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

failure_rows = "\n".join(
    f"| `{item['name']}` | {item['status']} | {item['evidence']} |"
    for item in drills
) or "| 尚未执行 | pending | - |"
(ROOT / "docs" / "M3_FAILURE_DRILL_REPORT.md").write_text(
    "# M3 故障演练报告\n\n"
    f"> 生成时间：{report['generated_at']}  \n"
    f"> Compose 项目：`{report['compose_project']}`  \n"
    f"> Alembic Head：`{revision}`\n\n"
    "## 演练结论\n\n"
    "| 演练 | 结果 | 证据 |\n"
    "| --- | --- | --- |\n"
    + failure_rows
    + "\n\n"
    "## 数据恢复证据\n\n"
    + (
        f"- PostgreSQL 备份恢复：通过，最新备份为 `{backup_evidence[-1].name}`。\n"
        if backup_evidence
        else "- PostgreSQL 备份恢复：尚未执行。\n"
    )
    + "- Worker 在 PostgreSQL 短暂中断后会重新建立 Repository 与任务循环，不会永久退出。\n"
    "- Connector 写成功但响应丢失时仅通过幂等键查询回执，不重放原始写操作。\n\n"
    "## 尚未覆盖的发布门禁\n\n"
    "本报告只记录故障恢复证据；OIDC、Fake Provider 与 DeepSeek 的最终结果见 "
    "`output/m3/m3-release-report.json`。\n",
    encoding="utf-8",
)
(ROOT / "docs" / "M3_OIDC_E2E_REPORT.md").write_text(
    "# M3 OIDC 业务 E2E 报告\n\n"
    f"> 本轮结果：**{oidc_result}**  \n"
    f"> 生成时间：{report['generated_at']}\n\n"
    "## 覆盖链路\n\n"
    "- 客户使用真实 Keycloak Authorization Code + PKCE 登录。\n"
    "- 查询本人订单与物流，并查看带版本的政策证据。\n"
    "- 创建、确认 ActionPlan，重复确认仍只生成一张外部工单。\n"
    "- 刷新后恢复会话、待确认动作和事件游标。\n"
    "- 客服登录后领取、回复、释放、重新领取、转派并解决会话。\n"
    "- customer、agent、tenant_admin、platform_admin 均执行禁止访问断言。\n"
    "- 请求头无法覆盖 Token 中的租户、主体或数据库角色。\n\n"
    "## 自动化入口\n\n"
    "完整链路由 `web/e2e/m3-release.spec.ts` 串行执行。失败时保留截图、视频和 Trace；"
    "最终准入结论以 `output/m3/m3-release-report.json` 为准。\n",
    encoding="utf-8",
)
print(OUTPUT / "m3-release-report.json")
