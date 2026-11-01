from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import timedelta
from typing import Any, TypedDict
from uuid import uuid4

import httpx
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app.core.config import settings
from app.core.security import AuthContext
from app.domain.models import AgentRun, AuditEvent, CompensationProposal, LlmTrace, utc_now
from app.llm.provider import LlmProvider, OpenAICompatibleProvider
from app.repositories.contracts import MarketRepository
from app.services.policy_service import PolicyService
from app.services.query_analyzer import analyze_query, extract_facts
from app.tools.gateway import OrderLookupInput, ToolGateway


class AgentState(TypedDict, total=False):
    tenant_id: str
    subject: str
    username: str
    request_id: str
    customer_id: str
    conversation_id: str | None
    message_id: str | None
    task_id: str | None
    content: str
    intent: str
    risk: str
    order_id: str | None
    filters: dict[str, str]
    understanding: dict[str, Any]
    precheck_action: str
    fallback_used: bool
    fallback_reason: str | None
    agent_run_id: str
    input_tokens: int
    output_tokens: int
    llm_calls: int
    tool_calls: int
    steps: int
    started_at: float
    answerability: dict[str, Any]
    order: dict[str, Any]
    result: dict[str, Any]


class MarketOrchestrator:
    """Bounded graph: models interpret language but never authorize writes."""

    def __init__(
        self,
        repository: MarketRepository,
        answerability_threshold: float,
        *,
        llm: LlmProvider | None = None,
        policies: PolicyService | None = None,
    ) -> None:
        self.repository = repository
        self.tools = ToolGateway(repository)
        self.policies = policies or PolicyService(repository, answerability_threshold)
        self.llm = llm or OpenAICompatibleProvider()
        self.graph = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(AgentState)
        graph.add_node("load_context", self._load_context)
        graph.add_node("precheck_security", self._precheck_security)
        graph.add_node("precheck_result", self._precheck_result)
        graph.add_node("understand_request", self._understand_request)
        graph.add_node("validate_parameters", self._validate_parameters)
        graph.add_node("assess_risk", self._assess_risk)
        graph.add_node("query_order", self._query_order)
        graph.add_node("build_fact_answer", self._build_fact_answer)
        graph.add_node("retrieve_policy", self._retrieve_policy)
        graph.add_node("answerability_gate", self._answerability_gate)
        graph.add_node("compose_grounded_answer", self._compose_grounded_answer)
        graph.add_node("require_clarification", self._require_clarification)
        graph.add_node("require_handoff", self._require_handoff)
        graph.add_node("preview_action", self._preview_action)
        graph.add_node("validate_action", self._validate_action)
        graph.add_node("require_confirmation", self._require_confirmation)
        graph.add_node("prepare_handoff", self._prepare_handoff)
        graph.add_node("retrieve_compensation_policy", self._retrieve_compensation_policy)
        graph.add_node("propose_compensation", self._propose_compensation)
        graph.add_node("supported_scope", self._supported_scope)
        graph.add_node("persist_result", self._persist_result)
        graph.add_node("record_agent_run", self._record_agent_run)
        graph.add_edge(START, "load_context")
        graph.add_edge("load_context", "precheck_security")
        graph.add_conditional_edges(
            "precheck_security",
            lambda state: state["precheck_action"],
            {"continue": "understand_request", "block": "precheck_result"},
        )
        graph.add_edge("precheck_result", "persist_result")
        graph.add_edge("understand_request", "validate_parameters")
        graph.add_edge("validate_parameters", "assess_risk")
        graph.add_conditional_edges(
            "assess_risk",
            lambda state: state["intent"],
            {
                "order": "query_order",
                "policy": "retrieve_policy",
                "ticket": "preview_action",
                "compensation": "query_order",
                "handoff": "prepare_handoff",
                "unsupported": "supported_scope",
            },
        )
        graph.add_conditional_edges(
            "query_order",
            lambda state: "stop" if state.get("result") else state["intent"],
            {
                "stop": "persist_result",
                "order": "build_fact_answer",
                "compensation": "retrieve_compensation_policy",
            },
        )
        graph.add_edge("build_fact_answer", "persist_result")
        graph.add_edge("retrieve_policy", "answerability_gate")
        for node in ("compose_grounded_answer", "require_clarification", "require_handoff"):
            graph.add_edge(node, "persist_result")
        graph.add_edge("preview_action", "validate_action")
        graph.add_edge("validate_action", "require_confirmation")
        graph.add_edge("require_confirmation", "persist_result")
        graph.add_edge("prepare_handoff", "persist_result")
        graph.add_edge("retrieve_compensation_policy", "answerability_gate")
        graph.add_conditional_edges(
            "answerability_gate",
            lambda state: (
                "propose" if state.get("intent") == "compensation"
                and state["answerability"]["decision"] == "answer"
                else state["answerability"]["decision"]
            ),
            {
                "propose": "propose_compensation",
                "answer": "compose_grounded_answer",
                "clarify": "require_clarification",
                "handoff": "require_handoff",
            },
        )
        graph.add_edge("propose_compensation", "persist_result")
        graph.add_edge("supported_scope", "persist_result")
        graph.add_edge("persist_result", "record_agent_run")
        graph.add_edge("record_agent_run", END)
        return graph.compile()

    def invoke(
        self,
        auth: AuthContext,
        content: str,
        *,
        conversation_id: str | None = None,
        message_id: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        agent_run_id = f"agent_run_{uuid4().hex}"
        initial: AgentState = {
                "tenant_id": auth.tenant_id,
                "subject": auth.subject,
                "username": auth.username,
                "request_id": auth.request_id,
                "customer_id": auth.customer_id or "",
                "conversation_id": conversation_id,
                "message_id": message_id,
                "task_id": task_id,
                "content": content[:12_000],
                "llm_calls": 0,
                "tool_calls": 0,
                "steps": 0,
                "started_at": time.monotonic(),
                "fallback_used": False,
                "fallback_reason": None,
                "agent_run_id": agent_run_id,
                "input_tokens": 0,
                "output_tokens": 0,
            }
        try:
            state = self.graph.invoke(
                initial, config={"recursion_limit": settings.agent_max_steps + 4}
            )
            result = state["result"]
        except Exception as error:  # noqa: BLE001 - graph boundary must degrade safely
            error_code = self._llm_error_code(error)
            if isinstance(error, RuntimeError) and "BUDGET" in str(error):
                error_code = "AGENT_BUDGET_EXCEEDED"
                safe_result = self._budget_result()
            else:
                safe_result = {
                    "kind": "handoff_recommended",
                    "reply": "当前自动处理服务暂时不可用，我没有执行任何业务写操作。建议稍后重试或转人工处理。",
                    "error_code": error_code,
                }
            state = {
                **initial,
                "intent": "unsupported",
                "risk": "forbidden",
                "steps": settings.agent_max_steps,
                "fallback_used": True,
                "fallback_reason": error_code,
                "result": safe_result,
            }
            result = state["result"]
            self._save_agent_run(state, int((time.monotonic() - started) * 1000))
        return result

    def _step(self, state: AgentState) -> dict[str, int]:
        steps = state.get("steps", 0) + 1
        if steps > settings.agent_max_steps or (
            time.monotonic() - state["started_at"] > settings.agent_total_timeout_seconds
        ):
            raise RuntimeError("AGENT_BUDGET_EXCEEDED")
        return {"steps": steps}

    def _load_context(self, state: AgentState) -> dict[str, Any]:
        return self._step(state)

    def _precheck_security(self, state: AgentState) -> dict[str, Any]:
        updates: dict[str, Any] = self._step(state)
        analysis = analyze_query(state["content"])
        dangerous = bool(
            analysis.prompt_injection
            or re.search(
                r"(?:忽略|无视).{0,8}(权限|确认|规则|安全)|绕过.{0,8}(权限|确认)|"
                r"查询.{0,8}(其他租户|别人|他人).{0,8}订单|伪造.{0,8}(订单|政策)",
                analysis.normalized_query,
            )
        )
        if dangerous:
            self.repository.append_audit(
                AuditEvent(
                    event_id=f"audit_{uuid4().hex}",
                    tenant_id=state["tenant_id"],
                    actor_id=state["subject"],
                    action="agent.security_precheck.blocked",
                    resource_type="conversation",
                    resource_id=str(state.get("conversation_id") or "unknown"),
                    request_id=state["request_id"],
                    data={"reason": "SECURITY_PRECHECK_BLOCKED"},
                )
            )
            updates.update(
                precheck_action="block",
                intent="unsupported",
                risk="forbidden",
                fallback_used=True,
                fallback_reason="SECURITY_PRECHECK_BLOCKED",
            )
        else:
            updates["precheck_action"] = "continue"
        return updates

    def _precheck_result(self, state: AgentState) -> dict[str, Any]:
        updates: dict[str, Any] = self._step(state)
        updates["result"] = {
            "kind": "forbidden",
            "reply": "该请求无法在当前权限和安全边界内执行。如需进一步处理，请联系人工客服。",
            "error_code": "SECURITY_PRECHECK_BLOCKED",
        }
        return updates

    def _understand_request(self, state: AgentState) -> dict[str, Any]:
        updates: dict[str, Any] = self._step(state)
        started = time.perf_counter()
        try:
            if state.get("llm_calls", 0) >= settings.agent_max_llm_calls:
                raise RuntimeError("AGENT_LLM_BUDGET_EXCEEDED")
            value = self.llm.understand_request(
                state["content"], {"customer_id": state.get("customer_id")}
            )
            payload = value.model_dump()
            updates.update(
                understanding=payload,
                intent=value.intent,
                order_id=value.order_id,
                llm_calls=state.get("llm_calls", 0) + 1,
                input_tokens=state.get("input_tokens", 0) + self.llm.last_usage.input_tokens,
                output_tokens=state.get("output_tokens", 0) + self.llm.last_usage.output_tokens,
            )
            self._trace(state, "understand_request", started, payload)
            return updates
        except Exception as error:  # noqa: BLE001
            fallback = self._deterministic_understanding(state["content"])
            updates.update(
                understanding=fallback,
                intent=fallback["intent"],
                order_id=fallback.get("order_id"),
                fallback_used=True,
                fallback_reason=self._llm_error_code(error),
            )
            self._trace(
                state, "understand_request", started, fallback, error=error, fallback=True
            )
            return updates

    def _validate_parameters(self, state: AgentState) -> dict[str, Any]:
        updates: dict[str, Any] = self._step(state)
        understanding = state.get("understanding") or {}
        # The pilot has one explicit delivery channel: the authenticated Web client.
        # Supplying it is required for published policies whose ACL is channel=web.
        filters: dict[str, str] = {"channel": "web"}
        if understanding.get("region"):
            filters["region"] = str(understanding["region"])
        if understanding.get("customer_tier"):
            filters["customer_tier"] = str(understanding["customer_tier"])
        updates["filters"] = filters
        if float(understanding.get("confidence") or 0) < 0.6:
            updates.update(intent="unsupported", fallback_used=True, fallback_reason="LOW_CONFIDENCE")
        return updates

    def _assess_risk(self, state: AgentState) -> dict[str, Any]:
        updates: dict[str, Any] = self._step(state)
        updates["risk"] = {
            "ticket": "medium",
            "compensation": "high",
            "unsupported": "forbidden",
        }.get(state["intent"], "read_only")
        return updates

    def _query_order(self, state: AgentState) -> dict[str, Any]:
        updates: dict[str, Any] = self._step(state)
        if not state.get("order_id"):
            updates["result"] = {
                "kind": "clarification",
                "reply": "请提供订单号，例如 order_1001。",
            }
            return updates
        if state.get("tool_calls", 0) >= settings.agent_max_tool_calls:
            updates["result"] = self._budget_result()
            return updates
        auth = AuthContext(
            tenant_id=state["tenant_id"],
            subject=state["subject"],
            role="customer",
            request_id=state["request_id"],
            username=state["username"],
            customer_id=state["customer_id"],
        )
        updates["order"] = self.tools.get_order(
            auth,
            OrderLookupInput(order_id=state["order_id"], customer_id=state["customer_id"]),
        )
        updates["tool_calls"] = state.get("tool_calls", 0) + 1
        return updates

    def _build_fact_answer(self, state: AgentState) -> dict[str, Any]:
        updates: dict[str, Any] = self._step(state)
        if state.get("result"):
            updates["result"] = state["result"]
            return updates
        order = state["order"]
        updates["result"] = {
            "kind": "order_fact",
            "reply": (
                f"订单 {order['order_id']} 当前为 {order['status']}，物流状态为 "
                f"{order['shipment_status']}，物流单号 {order['tracking_number']}。"
            ),
            "order": order,
        }
        return updates

    def _retrieve_policy(self, state: AgentState) -> dict[str, Any]:
        updates: dict[str, Any] = self._step(state)
        if state.get("tool_calls", 0) >= settings.agent_max_tool_calls:
            updates["answerability"] = {
                "decision": "handoff",
                "reason_code": "AGENT_BUDGET_EXCEEDED",
                "required_follow_up": [],
                "evidence_chunk_ids": [],
                "hits": [],
            }
            return updates
        updates["tool_calls"] = state.get("tool_calls", 0) + 1
        return updates

    def _retrieve_compensation_policy(self, state: AgentState) -> dict[str, Any]:
        updates: dict[str, Any] = self._step(state)
        if state.get("tool_calls", 0) >= settings.agent_max_tool_calls:
            updates["answerability"] = {
                "decision": "handoff",
                "reason_code": "AGENT_BUDGET_EXCEEDED",
                "required_follow_up": [],
                "evidence_chunk_ids": [],
                "hits": [],
            }
            return updates
        updates["tool_calls"] = state.get("tool_calls", 0) + 1
        return updates

    def _answerability_gate(self, state: AgentState) -> dict[str, Any]:
        updates: dict[str, Any] = self._step(state)
        if state.get("answerability"):
            updates["answerability"] = state["answerability"]
            return updates
        decision = self.policies.answerability_gate(
            state["tenant_id"],
            state["content"],
            filters=state.get("filters") or {},
            conversation_id=state.get("conversation_id"),
            message_id=state.get("message_id"),
        )
        updates["answerability"] = decision.to_dict()
        return updates

    def _propose_compensation(self, state: AgentState) -> dict[str, Any]:
        updates: dict[str, Any] = self._step(state)
        decision = state["answerability"]
        hits = decision.get("hits") or []
        amount_values: list[float] = []
        for hit in hits[:3]:
            for raw in extract_facts(str(hit.get("excerpt") or ""))["amount"]:
                match = re.search(r"\d+(?:\.\d+)?", raw)
                if match:
                    amount_values.append(float(match.group()))
        if not amount_values:
            updates["result"] = {
                "kind": "handoff_recommended",
                "reply": "当前政策证据不足以计算补偿建议，已建议转人工确认。",
                "answerability": decision,
            }
            return updates
        top = hits[0]
        proposal = CompensationProposal(
            proposal_id=f"proposal_{uuid4().hex}",
            tenant_id=state["tenant_id"],
            customer_id=state["customer_id"],
            order_id=str(state["order_id"]),
            conversation_id=str(state.get("conversation_id") or ""),
            retrieval_snapshot_id=decision.get("retrieval_snapshot_id"),
            policy_version_id=top.get("policy_version_id"),
            recommended_amount=min(50.0, max(amount_values)),
            reason=state["content"][:500],
            expires_at=utc_now() + timedelta(minutes=30),
        )
        self.repository.save_compensation_proposal(proposal)
        updates["result"] = {
            "kind": "compensation_requested",
            "reply": (
                f"根据当前有效政策，可提交 {proposal.recommended_amount:.2f} 元补偿申请。"
                "确认后只会创建人工审批任务，不会自动退款或发放。"
            ),
            "proposal": proposal.model_dump(mode="json"),
            "answerability": decision,
        }
        return updates

    def _compose_grounded_answer(self, state: AgentState) -> dict[str, Any]:
        updates: dict[str, Any] = self._step(state)
        decision = state["answerability"]
        hits = decision["hits"][:3]
        allowed = list(decision["evidence_chunk_ids"])
        started = time.perf_counter()
        try:
            if state.get("llm_calls", 0) >= settings.agent_max_llm_calls:
                raise RuntimeError("AGENT_LLM_BUDGET_EXCEEDED")
            answer = self.llm.compose_grounded_answer(state["content"], hits, allowed)
            if not answer.citation_chunk_ids or not set(answer.citation_chunk_ids).issubset(
                allowed
            ):
                raise ValueError("LLM returned citations outside allowed evidence")
            evidence_facts = extract_facts("\n".join(str(hit["excerpt"]) for hit in hits))
            answer_facts = extract_facts(answer.reply)
            if any(
                not values.issubset(evidence_facts.get(key, set()))
                for key, values in answer_facts.items()
            ):
                raise ValueError("LLM introduced facts absent from allowed evidence")
            updates["llm_calls"] = state.get("llm_calls", 0) + 1
            updates["input_tokens"] = (
                state.get("input_tokens", 0) + self.llm.last_usage.input_tokens
            )
            updates["output_tokens"] = (
                state.get("output_tokens", 0) + self.llm.last_usage.output_tokens
            )
            reply = answer.reply
            citation_ids = answer.citation_chunk_ids
            self._trace(
                state,
                "compose_grounded_answer",
                started,
                answer.model_dump(),
                allowed_citations=allowed,
            )
        except Exception as error:  # noqa: BLE001 - grounded extractive fallback
            reply = str(hits[0]["excerpt"])
            citation_ids = allowed
            self._trace(
                state,
                "compose_grounded_answer",
                started,
                {"reply": reply, "citation_chunk_ids": citation_ids},
                error=error,
                fallback=True,
                allowed_citations=allowed,
            )
            updates["fallback_used"] = True
            updates["fallback_reason"] = self._llm_error_code(error)
        updates["result"] = {
            "kind": "policy_answer",
            "reply": reply,
            "citations": [hit for hit in hits if hit["chunk_id"] in citation_ids],
            "answerability": decision,
        }
        return updates

    def _require_clarification(self, state: AgentState) -> dict[str, Any]:
        updates: dict[str, Any] = self._step(state)
        decision = state["answerability"]
        updates["result"] = {
            "kind": "clarification",
            "reply": "还缺少必要信息：" + "；".join(decision["required_follow_up"]),
            "answerability": decision,
        }
        return updates

    def _require_handoff(self, state: AgentState) -> dict[str, Any]:
        updates: dict[str, Any] = self._step(state)
        updates["result"] = {
            "kind": "handoff_recommended",
            "reply": "当前没有足够且一致的已发布政策证据，我不能给出确定性结论。建议转人工确认。",
            "answerability": state["answerability"],
        }
        return updates

    def _preview_action(self, state: AgentState) -> dict[str, Any]:
        updates: dict[str, Any] = self._step(state)
        updates["result"] = {
            "preview": {"action_type": "create_ticket", "description": state["content"]}
        }
        return updates

    def _validate_action(self, state: AgentState) -> dict[str, Any]:
        updates: dict[str, Any] = self._step(state)
        # Service and Tool Gateway retain authorization, idempotency and write authority.
        updates["result"] = state["result"]
        return updates

    def _require_confirmation(self, state: AgentState) -> dict[str, Any]:
        updates: dict[str, Any] = self._step(state)
        updates["result"] = {
            "kind": "ticket_requested",
            "reply": "可以创建售后工单，请先确认工单预览。",
            **state["result"],
        }
        return updates

    def _prepare_handoff(self, state: AgentState) -> dict[str, Any]:
        updates: dict[str, Any] = self._step(state)
        updates["result"] = {
            "kind": "handoff_requested",
            "reply": "正在为你转接人工客服。",
        }
        return updates

    def _supported_scope(self, state: AgentState) -> dict[str, Any]:
        updates: dict[str, Any] = self._step(state)
        updates["result"] = {
            "kind": "supported_scope",
            "reply": "我目前可以帮助查询订单物流、回答已发布政策、创建售后工单或转接人工客服。",
        }
        return updates

    def _persist_result(self, state: AgentState) -> dict[str, Any]:
        updates: dict[str, Any] = self._step(state)
        updates["result"] = state.get("result") or self._budget_result()
        return updates

    def _record_agent_run(self, state: AgentState) -> dict[str, Any]:
        updates: dict[str, Any] = self._step(state)
        result = {
            **(state.get("result") or self._budget_result()),
            "agent_run_id": state["agent_run_id"],
        }
        updates["result"] = result
        self._save_agent_run(
            {**state, **updates, "result": result},
            int((time.monotonic() - state["started_at"]) * 1000),
        )
        return updates

    def _save_agent_run(self, state: AgentState, latency_ms: int) -> None:
        result = state.get("result") or self._budget_result()
        answerability = result.get("answerability") or {}
        self.repository.save_agent_run(
            AgentRun(
                agent_run_id=state["agent_run_id"],
                tenant_id=state["tenant_id"],
                conversation_id=state.get("conversation_id"),
                message_id=state.get("message_id"),
                task_id=state.get("task_id"),
                request_id=state["request_id"],
                provider=self.llm.provider_name,
                model=self.llm.model_id,
                prompt_version=settings.llm_prompt_version,
                input_hash=hashlib.sha256(state["content"].encode()).hexdigest(),
                intent=state.get("intent", ""),
                risk=state.get("risk", ""),
                result_kind=str(result.get("kind") or ""),
                steps=state.get("steps", 0),
                llm_calls=state.get("llm_calls", 0),
                tool_calls=state.get("tool_calls", 0),
                input_tokens=state.get("input_tokens", 0),
                output_tokens=state.get("output_tokens", 0),
                latency_ms=latency_ms,
                fallback_used=state.get("fallback_used", False),
                fallback_reason=state.get("fallback_reason"),
                error_code=result.get("error_code"),
                retrieval_snapshot_id=answerability.get("retrieval_snapshot_id"),
                action_plan_id=(result.get("action_plan") or {}).get("action_plan_id"),
                handoff_id=(result.get("handoff") or {}).get("handoff_id"),
                estimated_cost=(
                    state.get("input_tokens", 0) * settings.llm_input_cost_per_million
                    + state.get("output_tokens", 0) * settings.llm_output_cost_per_million
                ) / 1_000_000,
                input_redacted={"content": self._redact(state["content"])},
                output_redacted=self._redact_value(result),
            )
        )

    @staticmethod
    def _deterministic_understanding(content: str) -> dict[str, Any]:
        from app.llm.provider import FakeLlmProvider

        return FakeLlmProvider().understand_request(content).model_dump()

    @staticmethod
    def _llm_error_code(error: Exception) -> str:
        if isinstance(error, (httpx.TimeoutException, TimeoutError)):
            return "LLM_TIMEOUT"
        if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 429:
            return "LLM_RATE_LIMITED"
        if isinstance(error, ValidationError):
            return "LLM_SCHEMA_INVALID"
        if isinstance(error, json.JSONDecodeError):
            return "LLM_INVALID_JSON"
        if isinstance(error, ValueError) and "citation" in str(error).lower():
            return "LLM_CITATION_VIOLATION"
        if isinstance(error, ValueError) and "facts" in str(error).lower():
            return "LLM_FACT_VIOLATION"
        if isinstance(error, ValueError):
            return "LLM_INVALID_RESPONSE"
        if isinstance(error, RuntimeError) and "configured" in str(error):
            return "LLM_NOT_CONFIGURED"
        return "LLM_PROVIDER_ERROR"

    @staticmethod
    def _budget_result() -> dict[str, Any]:
        return {
            "kind": "handoff_recommended",
            "reply": "本次处理已达到安全预算上限，建议转人工继续处理。",
            "error_code": "AGENT_BUDGET_EXCEEDED",
        }

    def _trace(
        self,
        state: AgentState,
        call_type: str,
        started: float,
        output: dict[str, Any],
        *,
        error: Exception | None = None,
        fallback: bool = False,
        allowed_citations: list[str] | None = None,
    ) -> None:
        trace = LlmTrace(
            llm_trace_id=f"trace_{uuid4().hex}",
            tenant_id=state["tenant_id"],
            conversation_id=state.get("conversation_id"),
            message_id=state.get("message_id"),
            task_id=state.get("task_id"),
            request_id=state["request_id"],
            provider=self.llm.provider_name,
            model=self.llm.model_id,
            prompt_version=settings.llm_prompt_version,
            prompt_hash=hashlib.sha256(
                f"{settings.llm_prompt_version}:{call_type}".encode()
            ).hexdigest() if not getattr(self.llm, "last_prompt_hash", "") else self.llm.last_prompt_hash,
            call_type=call_type,
            status="failed" if error else "succeeded",
            error_code=self._llm_error_code(error) if error else None,
            input_redacted={"content": self._redact(state["content"])},
            output_redacted=self._redact_value(output),
            input_tokens=self.llm.last_usage.input_tokens,
            output_tokens=self.llm.last_usage.output_tokens,
            latency_ms=int((time.perf_counter() - started) * 1000),
            fallback_used=fallback,
            fallback_reason=self._llm_error_code(error) if error else None,
            agent_run_id=state.get("agent_run_id"),
            budget={
                "max_llm_calls": settings.agent_max_llm_calls,
                "max_tool_calls": settings.agent_max_tool_calls,
                "max_steps": settings.agent_max_steps,
                "used_llm_calls": state.get("llm_calls", 0),
                "used_tool_calls": state.get("tool_calls", 0),
                "used_steps": state.get("steps", 0),
            },
            allowed_citation_ids=allowed_citations or [],
        )
        self.repository.save_llm_trace(trace)

    @staticmethod
    def _redact(value: str) -> str:
        value = re.sub(r"(?<!\d)1\d{10}(?!\d)", "***PHONE***", value)
        return re.sub(r"[\w.+-]+@[\w.-]+", "***EMAIL***", value)

    @classmethod
    def _redact_value(cls, value: Any) -> Any:
        if isinstance(value, str):
            return cls._redact(value)
        if isinstance(value, dict):
            return {key: cls._redact_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._redact_value(item) for item in value]
        return value
