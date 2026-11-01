from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings


class IntentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Literal["handoff", "ticket", "order", "policy", "unsupported"]
    order_id: str | None = None


class ParameterResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str | None = None
    product_or_category: str | None = None
    region: str | None = None
    customer_tier: str | None = None


class RequestUnderstanding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Literal["handoff", "ticket", "compensation", "order", "policy", "unsupported"]
    business_object: Literal[
        "order", "shipment", "return", "ticket", "compensation", "membership", "unsupported"
    ]
    requested_operation: Literal["query", "explain", "create", "propose", "handoff", "unsupported"]
    order_id: str | None = Field(default=None, pattern=r"^order[_-]?\d+$")
    product_or_category: str | None = None
    region: str | None = None
    customer_tier: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class GroundedAnswerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: str = Field(min_length=1, max_length=4000)
    citation_chunk_ids: list[str] = Field(default_factory=list, max_length=3)


class LlmUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class LlmProvider(Protocol):
    provider_name: str
    model_id: str
    last_usage: LlmUsage
    last_prompt_hash: str

    def understand_request(
        self, content: str, context: dict[str, Any] | None = None
    ) -> RequestUnderstanding: ...
    def classify_intent(self, content: str) -> IntentResult: ...
    def extract_parameters(self, content: str, intent: str) -> ParameterResult: ...
    def compose_grounded_answer(
        self, query: str, evidence: list[dict], allowed_citation_ids: list[str]
    ) -> GroundedAnswerResult: ...


class OpenAICompatibleProvider:
    """Structured boundary; model output never authorizes tools or business writes."""

    provider_name = "openai_compatible"

    def __init__(self) -> None:
        self.model_id = settings.llm_model
        self.last_usage = LlmUsage()
        self.last_prompt_hash = ""

    def _chat(self, system: str, user: str) -> dict:
        self.last_usage = LlmUsage()
        self.last_prompt_hash = hashlib.sha256(
            f"{settings.llm_prompt_version}\n{system}".encode()
        ).hexdigest()
        if not settings.llm_base_url or not settings.llm_api_key or not settings.llm_model:
            raise RuntimeError("LLM provider is not configured")
        response = httpx.post(
            f"{settings.llm_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json={
                "model": settings.llm_model,
                "temperature": 0,
                "max_tokens": settings.llm_max_output_tokens,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user[:12_000]},
                ],
            },
            timeout=httpx.Timeout(
                settings.llm_timeout_seconds,
                connect=settings.llm_connect_timeout_seconds,
            ),
        )
        response.raise_for_status()
        payload = response.json()
        usage = payload.get("usage") or {}
        self.last_usage = LlmUsage(
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )
        raw = payload["choices"][0]["message"]["content"]
        if not raw:
            raise ValueError("LLM returned empty content")
        return json.loads(raw)

    def understand_request(
        self, content: str, context: dict[str, Any] | None = None
    ) -> RequestUnderstanding:
        schema = json.dumps(RequestUnderstanding.model_json_schema(), ensure_ascii=False)
        return RequestUnderstanding.model_validate(
            self._chat(
                "Understand one Chinese ecommerce customer-service request and return one JSON "
                "object that validates against the supplied JSON Schema. Do not add fields. "
                "Use only these intent values: order, policy, ticket, compensation, handoff, "
                "unsupported. Use only these business_object values: order, shipment, return, "
                "ticket, compensation, membership, unsupported. Use only these "
                "requested_operation values: query, explain, create, propose, handoff, "
                "unsupported. Use JSON null for unknown optional values. Never invent an order "
                "ID, region, customer tier, product, amount, role, permission, tool, or execution "
                "result. Normalize an explicit order ID to order_<digits>. A compensation request "
                "means propose for human review, never direct payment. If an order/compensation "
                "request lacks order_id, include order_id in missing_fields. Schema: "
                + schema,
                json.dumps({"message": content, "context": context or {}}, ensure_ascii=False),
            )
        )

    def classify_intent(self, content: str) -> IntentResult:
        understood = self.understand_request(content)
        legacy_intent = "ticket" if understood.intent == "compensation" else understood.intent
        return IntentResult(
            intent=legacy_intent,
            order_id=understood.order_id,
        )

    def classify(self, content: str) -> IntentResult:
        return self.classify_intent(content)

    def extract_parameters(self, content: str, intent: str) -> ParameterResult:
        understood = self.understand_request(content, {"intent": intent})
        return ParameterResult(
            order_id=understood.order_id,
            product_or_category=understood.product_or_category,
            region=understood.region,
            customer_tier=understood.customer_tier,
        )

    def compose_grounded_answer(
        self, query: str, evidence: list[dict], allowed_citation_ids: list[str]
    ) -> GroundedAnswerResult:
        result = GroundedAnswerResult.model_validate(
            self._chat(
                "Write a concise Chinese answer using only supplied evidence. Return JSON only: "
                '{"reply":"...","citation_chunk_ids":["..."]}. '
                "Do not add facts absent from evidence. Citation IDs must be allowed.",
                json.dumps(
                    {
                        "query": query,
                        "allowed_citation_ids": allowed_citation_ids,
                        "evidence": evidence,
                    },
                    ensure_ascii=False,
                ),
            )
        )
        if not result.citation_chunk_ids or not set(result.citation_chunk_ids).issubset(
            allowed_citation_ids
        ):
            raise ValueError("LLM returned citations outside the allowed evidence set")
        return result


class FakeLlmProvider:
    provider_name = "fake"
    model_id = "fake-deterministic-v1"

    def __init__(self) -> None:
        self.last_usage = LlmUsage()
        self.last_prompt_hash = hashlib.sha256(b"fake-deterministic-v2").hexdigest()

    def understand_request(
        self, content: str, context: dict[str, Any] | None = None
    ) -> RequestUnderstanding:
        del context
        lowered = content.lower()
        match = re.search(r"order[_-]?\d+", lowered, flags=re.IGNORECASE)
        order_id = match.group(0).replace("-", "_") if match else None
        region = re.search(r"(?:地区|区域)[:：]?\s*([\u4e00-\u9fa5]{2,8})", content)
        tier = re.search(r"(普通会员|银卡会员|金卡会员|黑金会员)", content)
        policy_question = any(
            word in lowered
            for word in (
                "政策", "规则", "规定", "期限", "时限", "多久", "几天", "多少小时",
                "工作日", "资格", "哪些凭证", "怎么处理", "如何处理", "怎么规定",
            )
        )
        create_ticket = any(
            word in lowered
            for word in ("创建", "提交", "登记", "生成工单", "工单预览", "申请售后", "处理单")
        )
        if any(word in lowered for word in ("补偿", "赔付", "赔偿", "退款", "发券")):
            intent, obj, operation = "compensation", "compensation", "propose"
        elif any(
            word in lowered
            for word in ("转人工", "真人处理", "客服接管", "转接人工", "人工沟通", "联系在线客服", "人工专员", "客服接手", "人工服务")
        ):
            intent, obj, operation = "handoff", "unsupported", "handoff"
        elif (create_ticket and not policy_question) or (
            any(word in lowered for word in ("损坏", "破损", "工单", "裂痕", "坏掉", "damaged"))
            and not policy_question
        ):
            intent, obj, operation = "ticket", "ticket", "create"
        elif policy_question or any(word in lowered for word in ("退货", "换货", "售后", "会员")):
            intent, operation = "policy", "explain"
            obj = "membership" if "会员" in lowered else "return"
        elif any(
            word in lowered
            for word in ("订单", "物流", "配送", "快递", "包裹", "发货", "到货", "什么时候到", "送达", "延误", "order", "track")
        ):
            intent = "order"
            obj = "shipment" if any(
                word in lowered for word in ("物流", "配送", "快递", "包裹", "发货", "到货", "什么时候到", "送达", "延误", "track")
            ) else "order"
            operation = "query"
        else:
            intent, obj, operation = "unsupported", "unsupported", "unsupported"
        missing = ["order_id"] if intent in {"order", "compensation"} and not order_id else []
        return RequestUnderstanding(
            intent=intent,
            business_object=obj,
            requested_operation=operation,
            order_id=order_id,
            region=region.group(1) if region else None,
            customer_tier=tier.group(1) if tier else None,
            missing_fields=missing,
            confidence=0.99,
        )

    def classify_intent(self, content: str) -> IntentResult:
        value = self.understand_request(content)
        return IntentResult(
            intent="ticket" if value.intent == "compensation" else value.intent,
            order_id=value.order_id,
        )

    def compose_grounded_answer(
        self, query: str, evidence: list[dict], allowed_citation_ids: list[str]
    ) -> GroundedAnswerResult:
        del query
        if not evidence or not allowed_citation_ids:
            raise ValueError("Grounded answer requires evidence")
        return GroundedAnswerResult(
            reply=str(evidence[0]["excerpt"]),
            citation_chunk_ids=allowed_citation_ids[:3],
        )

    def extract_parameters(self, content: str, intent: str) -> ParameterResult:
        value = self.understand_request(content, {"intent": intent})
        return ParameterResult(
            order_id=value.order_id,
            product_or_category=value.product_or_category,
            region=value.region,
            customer_tier=value.customer_tier,
        )
