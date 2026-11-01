"""Idempotently seed published demo policies and a 50-case Chinese Week 4 Gold Set."""

from uuid import uuid4

from app.domain.models import EvaluationCase, PolicyVersion
from app.repositories.factory import get_repository
from app.services.policy_chunker import content_hash

POLICIES = [
    (
        "returns",
        "week4-v1",
        "七天退货政策",
        "# 退货期限\n商品签收后七天内可申请无理由退货。超过七天须转人工核验。",
    ),
    (
        "damaged_goods",
        "week4-v1",
        "破损商品售后政策",
        "# 破损凭证\n商品破损时，客户应在签收后四十八小时内上传照片并创建售后工单。",
    ),
    (
        "shipping_delay",
        "week4-v1",
        "物流延误政策",
        "# 延误处理\n物流超过承诺送达时间二十四小时，可创建查询工单；补偿方案必须人工审批。",
    ),
    (
        "membership",
        "week4-v1",
        "会员售后政策",
        "# 金卡会员\n金卡会员退货审核时限为一个工作日，普通会员为三个工作日。",
    ),
]


def ensure_policies(repository) -> dict[str, str]:
    ids: dict[str, str] = {}
    existing = repository.list_policy_versions("tenant_demo")
    for scope, version, title, content in POLICIES:
        found = next(
            (item for item in existing if item.scope == scope and item.version == version), None
        )
        if found is None:
            found = PolicyVersion(
                policy_version_id=f"policy_{uuid4().hex}",
                tenant_id="tenant_demo",
                scope=scope,
                version=version,
                title=title,
                content=content,
                content_hash=content_hash(content),
                acl={"channel": ["web"]},
            )
            repository.save_policy(found)
            found = repository.publish_policy(found.policy_version_id, "tenant_demo", "seed")
        elif found.status == "draft":
            found = repository.publish_policy(found.policy_version_id, "tenant_demo", "seed")
        if found is None:
            raise RuntimeError(f"Unable to seed policy {scope}")
        ids[scope] = found.policy_version_id
    return ids


def build_cases(policy_ids: dict[str, str]) -> list[EvaluationCase]:
    answer_templates = {
        "returns": ["退货期限是几天", "签收后多久能无理由退货", "七天退货规则是什么"],
        "damaged_goods": ["商品破损如何售后", "破损照片应在多久内上传", "破损商品怎么建工单"],
        "shipping_delay": ["物流延误多久可以建工单", "超过承诺时间怎么处理", "延误补偿能自动发放吗"],
        "membership": ["金卡会员审核要多久", "普通会员退货审核时限", "会员售后审核时间"],
    }
    cases: list[EvaluationCase] = []
    for index in range(40):
        scope = list(answer_templates)[index % 4]
        query = answer_templates[scope][(index // 4) % 3]
        cases.append(
            EvaluationCase(
                evaluation_case_id=f"eval_case_{uuid4().hex}",
                tenant_id="tenant_demo",
                dataset="week4_gold",
                dataset_version="v1",
                name=f"answer-{scope}-{index + 1:02d}",
                case_type="answerable",
                input_data={"query": query, "filters": {"channel": "web"}},
                expected={"decision": "answer", "policy_version_ids": [policy_ids[scope]]},
                tags=[scope, "zh-CN"],
            )
        )
    for index, query in enumerate(
        ["火星移民签证", "量子计算机保修", "宠物医疗报销", "机票改签费用", "房屋贷款利率"], 1
    ):
        cases.append(
            EvaluationCase(
                evaluation_case_id=f"eval_case_{uuid4().hex}",
                tenant_id="tenant_demo",
                dataset="week4_gold",
                dataset_version="v1",
                name=f"no-evidence-{index:02d}",
                case_type="unanswerable",
                input_data={"query": query, "filters": {"channel": "web"}},
                expected={"decision": "handoff", "policy_version_ids": []},
                tags=["no-evidence", "zh-CN"],
            )
        )
    for index in range(1, 6):
        cases.append(
            EvaluationCase(
                evaluation_case_id=f"eval_case_{uuid4().hex}",
                tenant_id="tenant_demo",
                dataset="week4_gold",
                dataset_version="v1",
                name=f"missing-context-{index:02d}",
                case_type="clarification",
                input_data={"query": "这个商品退货期限是多久", "filters": {"channel": "web"}},
                expected={"decision": "clarify", "policy_version_ids": []},
                tags=["missing-context", "zh-CN"],
            )
        )
    return cases


def main() -> None:
    repository = get_repository()
    policy_ids = ensure_policies(repository)
    cases = build_cases(policy_ids)
    dataset = repository.get_evaluation_dataset("tenant_demo", "week4_gold", "v1")
    imported = 0 if dataset and dataset.status == "frozen" else repository.import_evaluation_cases(cases)
    repository.freeze_evaluation_dataset("tenant_demo", "week4_gold", "v1")
    print(f"Week 4 seed ready: {len(policy_ids)} policies, {imported} cases")


if __name__ == "__main__":
    main()
