from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

NORMALIZATION_VERSION = "query-normalizer-v4"
ACL_KEYS = ("product_id", "category", "region", "channel", "customer_tier")

_CN_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


@dataclass(frozen=True)
class QueryAnalysis:
    normalized_query: str
    question_type: str
    required_facts: list[str] = field(default_factory=list)
    entities: dict[str, str] = field(default_factory=dict)
    missing_facts: list[str] = field(default_factory=list)
    prompt_injection: bool = False
    domain_intent: str = "ambiguous"
    business_anchors: list[str] = field(default_factory=list)
    requested_policy_scope: str | None = None
    explicit_acl_context: dict[str, str] = field(default_factory=dict)
    negative_or_exclusion_intent: bool = False
    business_object: str | None = None
    requested_operation: str | None = None
    required_facts_by_scope: dict[str, list[str]] = field(default_factory=dict)
    unsupported_intent: str | None = None
    acl_conflict: bool = False


_BUSINESS_ANCHORS = {
    "订单": ("订单", "下单", "购买"),
    "物流": ("物流", "快递", "发货", "签收", "配送", "到货", "延误"),
    "售后": ("售后", "退货", "换货", "退款", "破损", "工单", "维修"),
    "补偿": ("补偿", "赔付", "优惠券", "赔偿"),
    "会员": ("会员", "金卡", "银卡", "黑金", "等级"),
}
_UNSUPPORTED_ANCHORS = (
    "天气", "股票", "基金", "写代码", "翻译", "旅游", "菜谱", "医疗", "法律咨询", "法律意见",
    "system prompt", "数据库密码", "代码", "诗", "摄影", "健身", "投资",
    "量子", "星座",
)


def normalize_query(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    value = re.sub(r"[，。！？；：、,.!?;:]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    replacements = {
        "东西": "商品",
        "货物": "商品",
        "到手": "签收",
        "坏了": "破损",
        "咋办": "如何售后",
        "快递": "物流",
        "晚到": "延误",
        "审完": "审核完成",
        "能赔多少钱": "补偿金额多少元",
        "还能退": "可以退货",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)

    def replace_number(match: re.Match[str]) -> str:
        raw = match.group(1)
        if raw == "十":
            number = 10
        elif raw.startswith("十") and len(raw) == 2:
            number = 10 + _CN_DIGITS.get(raw[1], 0)
        elif raw.endswith("十") and len(raw) == 2:
            number = _CN_DIGITS.get(raw[0], 0) * 10
        elif "十" in raw and len(raw) == 3:
            number = _CN_DIGITS.get(raw[0], 0) * 10 + _CN_DIGITS.get(raw[2], 0)
        else:
            number = _CN_DIGITS.get(raw, 0)
        return f"{number}{match.group(2)}"

    return re.sub(
        r"([零一二两三四五六七八九十]{1,3})(天|日|小时|个?工作日|元)",
        replace_number,
        value,
    )


def analyze_query(query: str, filters: dict[str, str] | None = None) -> QueryAnalysis:
    filters = filters or {}
    normalized = normalize_query(query)
    required: list[str] = []
    missing: list[str] = []
    entities = {key: str(filters[key]) for key in ACL_KEYS if filters.get(key)}
    anchors = sorted(
        {family for family, terms in _BUSINESS_ANCHORS.items() if any(term in normalized for term in terms)}
    )
    explicit_acl = dict(entities)
    for tier in ("普通会员", "银卡", "金卡", "黑金"):
        if tier in normalized:
            explicit_acl["customer_tier"] = tier
    region_match = re.search(r"(上海|北京|广州|深圳|杭州|成都|武汉|南京)(?:地区|区域|本地)?", normalized)
    if region_match:
        explicit_acl["region"] = region_match.group(1)
    requested_scope = anchors[0] if len(anchors) == 1 else None
    unsupported_term = next((term for term in _UNSUPPORTED_ANCHORS if term in normalized), None)
    unsupported = unsupported_term is not None
    domain_intent = "unsupported" if unsupported else "supported" if anchors else "ambiguous"
    negative_or_exclusion = bool(re.search(r"不适用|不属于|排除|除外|不是.{0,6}(订单|物流|售后|会员)", normalized))

    object_map = {"订单": "order", "物流": "shipment", "售后": "after_sale", "补偿": "compensation", "会员": "membership"}
    business_object = object_map.get(requested_scope) if requested_scope else ("mixed" if anchors else None)
    if unsupported:
        requested_operation = "unsupported"
    elif re.search(r"创建|提交|申请|办理|发起", normalized):
        requested_operation = "create_action"
    elif re.search(r"金额|多少钱|额度|多少元", normalized):
        requested_operation = "ask_amount"
    elif re.search(r"多久|几天|期限|时限|多少小时|工作日", normalized):
        requested_operation = "ask_duration"
    elif re.search(r"资格|能否|是否|可以|能不能|支持|允许|禁止", normalized):
        requested_operation = "ask_eligibility"
    elif re.search(r"状态|到哪|进度|什么时候到", normalized):
        requested_operation = "query_status"
    elif anchors:
        requested_operation = "ask_policy"
    else:
        requested_operation = None

    if re.search(r"多久|几天|多少天|期限|时限|多少小时|几个工作日|\d+\s*(?:天|日|小时|工作日)", normalized):
        required.append("duration")
    if re.search(r"多少钱|金额|额度|多少元", normalized):
        required.append("amount")
    if re.search(r"能否|是否|可以|能不能|资格|支持|允许|禁止|自动发放", normalized):
        required.append("eligibility")

    if re.search(r"这个商品|该商品|我的商品", normalized) and not (
        filters.get("product_id") or filters.get("category")
    ):
        missing.append("product_or_category")
    if re.search(r"本地|所在地区|本地区|当地", normalized) and not filters.get("region"):
        missing.append("region")
    if re.search(r"我的会员|会员审核|会员售后", normalized) and not (
        filters.get("customer_tier")
        or re.search(r"金卡|普通会员|银卡|黑金", normalized)
    ):
        missing.append("customer_tier")

    prompt_injection = bool(
        re.search(
            r"忽略.{0,12}(规则|指令|政策|权限|确认|租户|acl)|越过.{0,8}(权限|租户|acl)|绕过.{0,8}(权限|确认|租户|acl)|显示其他租户|system prompt",
            normalized,
        )
    )
    acl_conflict = any(
        entities.get(key) and value != entities[key]
        for key, value in explicit_acl.items()
    )
    if prompt_injection:
        question_type = "prompt_injection"
    elif missing:
        question_type = "missing_context"
    elif required:
        question_type = "fact"
    else:
        question_type = "general"
    return QueryAnalysis(
        normalized_query=normalized,
        question_type=question_type,
        required_facts=sorted(set(required)),
        entities=entities,
        missing_facts=missing,
        prompt_injection=prompt_injection,
        domain_intent=domain_intent,
        business_anchors=anchors,
        requested_policy_scope=requested_scope,
        explicit_acl_context=explicit_acl,
        negative_or_exclusion_intent=negative_or_exclusion,
        business_object=business_object,
        requested_operation=requested_operation,
        required_facts_by_scope={requested_scope: sorted(set(required))} if requested_scope and required else {},
        unsupported_intent=unsupported_term,
        acl_conflict=acl_conflict,
    )


def extract_facts(text: str) -> dict[str, set[str]]:
    normalized = normalize_query(text)
    durations = set(re.findall(r"\d+\s*(?:天|日|小时|个?工作日)", normalized))
    amounts = set(re.findall(r"\d+(?:\.\d+)?\s*(?:元|人民币)", normalized))
    eligibility: set[str] = set()
    if re.search(r"不支持|不可|禁止|不能|不得", normalized):
        eligibility.add("deny")
    if re.search(r"支持|可以|允许|可.{0,16}申请|可创建|能|符合资格|具备.{0,20}资格", normalized):
        eligibility.add("allow")
    return {"duration": durations, "amount": amounts, "eligibility": eligibility}
