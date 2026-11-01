from __future__ import annotations

import hashlib
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, ClassVar
from uuid import uuid4

from app.domain.models import RetrievalSnapshot
from app.repositories.contracts import MarketRepository
from app.services.embeddings import (
    EmbeddingProvider,
    embedding_provider_for,
    get_embedding_provider,
)
from app.services.query_analyzer import (
    NORMALIZATION_VERSION,
    QueryAnalysis,
    analyze_query,
    extract_facts,
)


@dataclass(frozen=True)
class PolicyHit:
    chunk_id: str
    tenant_id: str
    policy_version_id: str
    title: str
    version: str
    scope: str
    excerpt: str
    rrf_score: float
    policy_id: str = ""
    policy_status: str = "published"
    lexical_score: float = 0.0
    semantic_score: float = 0.0
    lexical_rank: int | None = None
    semantic_rank: int | None = None
    title_path: list[str] | None = None
    ordinal: int = 0
    content_hash: str = ""
    acl: dict[str, list[str]] | None = None
    matched_entities: list[str] | None = None
    effective_from: Any = None
    effective_to: Any = None
    score: float = 0.0
    retrieval_timings: dict[str, float] | None = None

    def __post_init__(self) -> None:
        if not self.score:
            object.__setattr__(self, "score", self.rrf_score)


@dataclass(frozen=True)
class AnswerabilityDecision:
    decision: str
    reason_code: str
    confidence: float
    required_follow_up: list[str]
    evidence_chunk_ids: list[str]
    retrieval_snapshot_id: str
    gate_version: str
    hits: list[PolicyHit]
    analysis: dict[str, Any] | None = None

    @property
    def answerable(self) -> bool:
        return self.decision == "answer"

    @property
    def score(self) -> float:
        return self.confidence

    @property
    def reason(self) -> str:
        return self.reason_code

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for hit in value["hits"]:
            for key in ("effective_from", "effective_to"):
                if hit[key] is not None:
                    hit[key] = str(hit[key])
        value.update(answerable=self.answerable, score=self.score, reason=self.reason)
        return value


class PolicyService:
    """Tenant-scoped RRF retrieval with a deterministic, auditable safety gate."""

    gate_version = "v4"
    retriever_version = "hybrid-rrf-v4"
    default_gate_config: ClassVar[dict[str, Any]] = {
        "gate_version": "v4",
        "min_rrf": 0.015,
        "semantic_only_min_score": 0.35,
        "min_top1_top2_margin": 0.02,
        "require_lexical_anchor_for_unknown_domain": True,
        "require_single_policy_fact_coverage": True,
        "weak_lexical_score": 0.10,
    }

    def __init__(
        self,
        repository: MarketRepository,
        threshold: float = 0.015,
        *,
        embeddings: EmbeddingProvider | None = None,
        retrieval_profile_id: str | None = None,
    ) -> None:
        self.repository = repository
        self.threshold = threshold
        self.embeddings = embeddings or get_embedding_provider()
        self.retrieval_profile_id = retrieval_profile_id

    def _profile(self, tenant_id: str):
        profiles = self.repository.list_retrieval_profiles(tenant_id)
        if self.retrieval_profile_id:
            return next(
                (item for item in profiles if item.retrieval_profile_id == self.retrieval_profile_id),
                None,
            )
        return next((item for item in profiles if item.status == "active"), None)

    def _gate_config(self, tenant_id: str) -> dict[str, Any]:
        profile = self._profile(tenant_id)
        return {**self.default_gate_config, **((profile.gate_config or {}) if profile else {})}

    def _resolved_retrieval(self, tenant_id: str) -> tuple[EmbeddingProvider, str | None]:
        if self.retrieval_profile_id:
            return self.embeddings, self.retrieval_profile_id
        active = next(
            (
                profile
                for profile in self.repository.list_retrieval_profiles(tenant_id)
                if profile.status == "active" and profile.provider != "hash"
            ),
            None,
        )
        if active is None:
            return self.embeddings, None
        return (
            embedding_provider_for(
                active.provider,
                active.embedding_model,
                active.embedding_dimension,
            ),
            active.retrieval_profile_id,
        )

    def search(
        self,
        tenant_id: str,
        query: str,
        limit: int = 5,
        filters: dict | None = None,
    ) -> list[PolicyHit]:
        embeddings, profile_id = self._resolved_retrieval(tenant_id)
        rows = self.repository.search_policy_chunks(
            tenant_id,
            analyze_query(query, filters).normalized_query,
            embeddings.embed_query(query),
            max(limit * 4, 20),
            filters or {},
            embeddings.model_id,
            embeddings.dimensions,
            profile_id,
        )
        return self._diversify([PolicyHit(**row) for row in rows], limit)

    def answerability_gate(
        self,
        tenant_id: str,
        query: str,
        *,
        filters: dict | None = None,
        conversation_id: str | None = None,
        message_id: str | None = None,
    ) -> AnswerabilityDecision:
        started = time.perf_counter()
        filters = filters or {}
        analysis = analyze_query(query, filters)
        embeddings, profile_id = self._resolved_retrieval(tenant_id)
        incompatible = self.repository.count_incompatible_policy_chunks(
            tenant_id,
            embeddings.model_id,
            embeddings.dimensions,
            profile_id,
        )
        preblocked = analysis.prompt_injection or analysis.domain_intent == "unsupported" or analysis.acl_conflict
        embedding_started = time.perf_counter()
        query_embedding = embeddings.embed_query(query) if not incompatible and not preblocked else []
        embedding_ms = (time.perf_counter() - embedding_started) * 1000
        rows = (
            self.repository.search_policy_chunks(
                tenant_id,
                analysis.normalized_query,
                query_embedding,
                20,
                filters,
                embeddings.model_id,
                embeddings.dimensions,
                profile_id,
            )
            if not incompatible and not preblocked
            else []
        )
        hits = self._diversify([PolicyHit(**row) for row in rows], 5)
        compatible_hits = [hit for hit in hits if self._scope_matches(analysis, hit)]
        if compatible_hits:
            hits = compatible_hits + [hit for hit in hits if hit not in compatible_hits]
        if analysis.required_facts:
            coverage = self._fact_coverage_by_family(analysis.required_facts, hits)
            complete_families = {
                family for family, value in coverage.items() if value.get("complete")
            }
            if complete_families:
                hits = [
                    *[hit for hit in hits if (hit.policy_id or hit.scope) in complete_families],
                    *[hit for hit in hits if (hit.policy_id or hit.scope) not in complete_families],
                ]
        confidence = min(1.0, (hits[0].rrf_score / (2 / 61))) if hits else 0.0
        gate_config = self._gate_config(tenant_id)
        decision, reason, follow_up, rule_id = self._decide(
            analysis, hits, incompatible, gate_config
        )
        cited = (
            [
                hit
                for hit in hits
                if (hit.policy_id or hit.scope) == (hits[0].policy_id or hits[0].scope)
            ][:3]
            if decision == "answer" and hits
            else []
        )
        top1 = hits[0] if hits else None
        top2 = hits[1] if len(hits) > 1 else None
        semantic_only = bool(top1 and top1.lexical_rank is None)
        score_margin = self._score_margin(top1, top2, semantic_only)
        family_coverage = self._fact_coverage_by_family(analysis.required_facts, hits)
        snapshot = RetrievalSnapshot(
            retrieval_snapshot_id=f"snapshot_{uuid4().hex}",
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            message_id=message_id,
            query_redacted=self._redact(query),
            query_hash=hashlib.sha256(query.encode("utf-8")).hexdigest(),
            filters=filters,
            candidates=[self._json_hit(hit) for hit in hits],
            citations=[self._json_hit(hit) for hit in cited],
            decision=decision,
            reason_code=reason,
            confidence=round(confidence, 4),
            retriever_version=self.retriever_version,
            embedding_model=embeddings.model_id,
            embedding_dimension=embeddings.dimensions,
            normalization_version=NORMALIZATION_VERSION,
            diagnostics={
                "incompatible_chunk_count": incompatible,
                "question_type": analysis.question_type,
                "required_facts": analysis.required_facts,
                "entities": analysis.entities,
                "missing_facts": analysis.missing_facts,
                "domain_intent": analysis.domain_intent,
                "business_anchors": analysis.business_anchors,
                "lexical_anchor_count": len(analysis.business_anchors),
                "requested_policy_scope": analysis.requested_policy_scope,
                "explicit_acl_context": analysis.explicit_acl_context,
                "negative_or_exclusion_intent": analysis.negative_or_exclusion_intent,
                "business_object": analysis.business_object,
                "requested_operation": analysis.requested_operation,
                "required_facts_by_scope": analysis.required_facts_by_scope,
                "unsupported_intent": analysis.unsupported_intent,
                "semantic_only": semantic_only,
                "top1_score": self._candidate_score(top1, semantic_only),
                "top2_score": self._candidate_score(top2, semantic_only),
                "top1_top2_margin": score_margin,
                "fact_coverage_by_policy_family": family_coverage,
                "acl_filtered_count": 0,
                "query_scope": analysis.requested_policy_scope,
                "candidate_scope": top1.scope if top1 else None,
                "scope_compatibility": self._scope_matches(analysis, top1),
                "lexical_anchor_quality": self._lexical_anchor_quality(analysis, top1),
                "score_type": "semantic" if semantic_only else "rrf",
                "conflict_groups": self._conflict_groups(hits, analysis),
                "acl_rejection_reason": "explicit_context_conflict" if analysis.acl_conflict else None,
                "gate_rule_id": rule_id,
                "gate_config": gate_config,
                "timings": {
                    "embedding_rpc_ms": round(embedding_ms, 3),
                    **((top1.retrieval_timings or {}) if top1 else {}),
                },
            },
            gate_version=self.gate_version,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        self.repository.save_retrieval_snapshot(snapshot)
        return AnswerabilityDecision(
            decision=decision,
            reason_code=reason,
            confidence=snapshot.confidence,
            required_follow_up=follow_up,
            evidence_chunk_ids=[hit.chunk_id for hit in cited],
            retrieval_snapshot_id=snapshot.retrieval_snapshot_id,
            gate_version=self.gate_version,
            hits=hits,
            analysis={
                "question_type": analysis.question_type,
                "required_facts": analysis.required_facts,
                "entities": analysis.entities,
                "missing_facts": analysis.missing_facts,
                "domain_intent": analysis.domain_intent,
                "business_anchors": analysis.business_anchors,
                "requested_policy_scope": analysis.requested_policy_scope,
                "business_object": analysis.business_object,
                "requested_operation": analysis.requested_operation,
                "required_facts_by_scope": analysis.required_facts_by_scope,
                "gate_rule_id": rule_id,
                "semantic_only": semantic_only,
                "top1_top2_margin": score_margin,
                "fact_coverage_by_policy_family": family_coverage,
            },
        )

    def _decide(
        self,
        analysis: QueryAnalysis,
        hits: list[PolicyHit],
        incompatible: int,
        config: dict[str, Any],
    ) -> tuple[str, str, list[str], str]:
        if incompatible:
            return "handoff", "INDEX_MODEL_MISMATCH", [], "GATE-V4-001"
        if analysis.prompt_injection:
            return "handoff", "UNSUPPORTED_SCOPE", [], "GATE-V4-002"
        if analysis.domain_intent == "unsupported":
            return "handoff", "UNSUPPORTED_SCOPE", [], "GATE-V4-003"
        if re.search(r"(?:冲突|同时.{0,20}(?:\d+\s*(?:元|天|日|小时)).{0,20}(?:\d+\s*(?:元|天|日|小时)))", analysis.normalized_query):
            return "handoff", "CONFLICTING_POLICIES", [], "GATE-V4-003A"
        if self._explicit_acl_mismatch(analysis):
            return "handoff", "ACL_CONTEXT_MISMATCH", [], "GATE-V4-004"
        if analysis.missing_facts:
            labels = {
                "product_or_category": "请提供商品或商品品类",
                "region": "请提供所在地区",
                "customer_tier": "请提供会员等级",
            }
            return (
                "clarify",
                "MISSING_CONTEXT",
                [labels[item] for item in analysis.missing_facts],
                "GATE-V4-005",
            )
        if not hits:
            return "handoff", "NO_EVIDENCE", [], "GATE-V4-006"
        if not self._scope_matches(analysis, hits[0]):
            return "handoff", "UNSUPPORTED_SCOPE", [], "GATE-V4-007"
        missing = self._missing_single_policy_facts(analysis.required_facts, hits)
        if missing:
            return "clarify", "MISSING_FACT", missing, "GATE-V4-008"
        min_rrf = float(config.get("min_rrf", self.threshold))
        if hits[0].rrf_score < min_rrf:
            return "handoff", "LOW_COVERAGE", [], "GATE-V4-009"
        semantic_only = hits[0].lexical_rank is None
        if semantic_only:
            if analysis.domain_intent != "supported":
                return "handoff", "UNSUPPORTED_SCOPE", [], "GATE-V4-010"
            if hits[0].semantic_score < float(config.get("semantic_only_min_score", 0.35)):
                return "handoff", "LOW_COVERAGE", [], "GATE-V4-011"
            if self._score_margin(hits[0], hits[1] if len(hits) > 1 else None, True) < float(
                config.get("min_top1_top2_margin", 0.02)
            ):
                return "handoff", "LOW_SCORE_MARGIN", [], "GATE-V4-012"
        elif hits[0].lexical_score < float(config.get("weak_lexical_score", 0.10)) and self._score_margin(
            hits[0], hits[1] if len(hits) > 1 else None, False
        ) < float(config.get("min_top1_top2_margin", 0.02)):
            return "handoff", "LOW_SCORE_MARGIN", [], "GATE-V4-013"
        if re.search(r"专享|仅限|限定|本地区", analysis.normalized_query) and not any(
            self._acl_evidence_matches(hit, analysis.explicit_acl_context) for hit in hits[:3]
        ):
            return "handoff", "ACL_CONTEXT_MISMATCH", [], "GATE-V4-014"
        if self._has_conflict(hits, analysis):
            return "handoff", "CONFLICTING_POLICIES", [], "GATE-V4-015"
        return "answer", "ANSWERABLE", [], "GATE-V4-016"

    @staticmethod
    def _explicit_acl_mismatch(analysis: QueryAnalysis) -> bool:
        return any(
            analysis.entities.get(key)
            and explicit != analysis.entities[key]
            for key, explicit in analysis.explicit_acl_context.items()
        )

    @staticmethod
    def _candidate_score(hit: PolicyHit | None, semantic_only: bool) -> float:
        if hit is None:
            return 0.0
        return float(hit.semantic_score if semantic_only else hit.rrf_score)

    @classmethod
    def _score_margin(
        cls, top1: PolicyHit | None, top2: PolicyHit | None, semantic_only: bool
    ) -> float:
        return max(
            0.0,
            cls._candidate_score(top1, semantic_only) - cls._candidate_score(top2, semantic_only),
        )

    @staticmethod
    def _scope_matches(analysis: QueryAnalysis, hit: PolicyHit | None) -> bool:
        if hit is None:
            return False
        candidate_scope = PolicyService._candidate_business_scope(hit)
        if not analysis.requested_policy_scope:
            if not analysis.business_anchors:
                return True
            return candidate_scope in analysis.business_anchors or (
                candidate_scope == "补偿"
                and "物流" in analysis.business_anchors
                and analysis.requested_operation in {"ask_amount", "ask_eligibility", "ask_policy"}
            )
        return candidate_scope == analysis.requested_policy_scope or (
            analysis.requested_policy_scope == "物流"
            and candidate_scope == "补偿"
            and analysis.requested_operation in {"ask_amount", "ask_eligibility", "ask_policy"}
        ) or (
            analysis.requested_policy_scope == "订单"
            and candidate_scope == "补偿"
            and analysis.requested_operation in {"ask_amount", "ask_eligibility", "ask_policy"}
            and bool(re.search(r"奖励|补偿|赔付|赔偿", analysis.normalized_query))
        )

    @staticmethod
    def _candidate_business_scope(hit: PolicyHit) -> str | None:
        value = f"{hit.scope} {hit.title}".lower()
        if any(term in value for term in ("membership", "member", "会员")):
            return "会员"
        if any(term in value for term in ("compensation", "bonus", "补偿", "赔付", "奖励")):
            return "补偿"
        if any(term in value for term in ("shipping", "shipment", "logistics", "物流", "配送")):
            return "物流"
        if any(term in value for term in ("return", "damaged", "exchange", "after_sale", "售后", "退货", "换货", "保修")):
            return "售后"
        if any(term in value for term in ("order", "订单")):
            return "订单"
        return None

    @staticmethod
    def _candidate_policy_topic(hit: PolicyHit) -> str:
        value = f"{hit.scope} {hit.title}".lower()
        topics = (
            ("auto_bonus", ("auto_bonus", "自动奖励")),
            ("warranty", ("warranty", "保修")),
            ("damaged_goods", ("damaged", "破损")),
            ("returns", ("return", "退货", "换货", "exchange")),
            ("shipping_delay", ("shipping", "shipment", "物流", "配送", "延误")),
            ("membership", ("membership", "member", "会员")),
            ("compensation", ("compensation", "补偿", "赔付")),
        )
        return next((name for name, terms in topics if any(term in value for term in terms)), hit.scope)

    @classmethod
    def _lexical_anchor_quality(cls, analysis: QueryAnalysis, hit: PolicyHit | None) -> str:
        if hit is None or hit.lexical_rank is None:
            return "none"
        if not cls._scope_matches(analysis, hit):
            return "generic"
        return "strong" if hit.lexical_score >= 0.10 else "weak"

    @classmethod
    def _conflict_groups(cls, hits: list[PolicyHit], analysis: QueryAnalysis) -> list[dict[str, Any]]:
        compatible = [hit for hit in hits if cls._scope_matches(analysis, hit)]
        groups: dict[str, set[str]] = {}
        for hit in compatible:
            family = cls._candidate_policy_topic(hit)
            values = extract_facts(hit.excerpt)
            for fact in analysis.required_facts:
                for value in values.get(fact, set()):
                    groups.setdefault(f"{family}:{fact}", set()).add(value)
        return [
            {"group": key, "values": sorted(values)}
            for key, values in groups.items()
            if len(values) > 1
        ]

    @staticmethod
    def _fact_coverage_by_family(required: list[str], hits: list[PolicyHit]) -> dict[str, Any]:
        coverage: dict[str, dict[str, Any]] = {}
        for hit in hits[:5]:
            family = hit.policy_id or hit.scope
            entry = coverage.setdefault(family, {"policy_version_ids": [], "facts": {}})
            if hit.policy_version_id not in entry["policy_version_ids"]:
                entry["policy_version_ids"].append(hit.policy_version_id)
            for fact, values in extract_facts(hit.excerpt).items():
                entry["facts"].setdefault(fact, [])
                entry["facts"][fact] = sorted(set(entry["facts"][fact]) | set(values))
        for entry in coverage.values():
            entry["complete"] = all(entry["facts"].get(fact) for fact in required)
        return coverage

    @staticmethod
    def _acl_evidence_matches(hit: PolicyHit, entities: dict[str, str]) -> bool:
        acl = hit.acl or {}
        constrained = {
            key: value for key, value in entities.items() if key != "channel"
        }
        return bool(constrained) and all(value in acl.get(key, []) for key, value in constrained.items())

    @staticmethod
    def _has_conflict(hits: list[PolicyHit], analysis: QueryAnalysis) -> bool:
        query_bigrams = {
            analysis.normalized_query[index : index + 2]
            for index in range(max(0, len(analysis.normalized_query) - 1))
            if " " not in analysis.normalized_query[index : index + 2]
        }
        grouped: dict[str, dict[str, dict[str, set[str]]]] = {}
        for hit in hits[:5]:
            if not PolicyService._scope_matches(analysis, hit):
                continue
            if not any(token in hit.excerpt for token in query_bigrams):
                continue
            family = PolicyService._candidate_policy_topic(hit)
            grouped.setdefault(family, {})[hit.policy_version_id] = extract_facts(hit.excerpt)
        for policies in grouped.values():
            if len(policies) < 2:
                continue
            for fact in analysis.required_facts:
                values = {
                    value for facts in policies.values() for value in facts.get(fact, set())
                }
                if len(values) > 1:
                    return True
        return False

    @staticmethod
    def _missing_single_policy_facts(required: list[str], hits: list[PolicyHit]) -> list[str]:
        labels = {
            "duration": "政策证据中缺少明确时限",
            "amount": "政策证据中缺少明确金额",
            "eligibility": "政策证据中缺少明确资格或允许条件",
        }
        if not required:
            return []
        grouped: dict[str, dict[str, set[str]]] = {}
        for hit in hits[:5]:
            family = hit.policy_id or hit.scope
            facts = grouped.setdefault(
                family, {"duration": set(), "amount": set(), "eligibility": set()}
            )
            for key, values in extract_facts(hit.excerpt).items():
                facts[key].update(values)
        if any(all(facts.get(fact) for fact in required) for facts in grouped.values()):
            return []
        available = {fact for facts in grouped.values() for fact in required if facts.get(fact)}
        return [labels[fact] for fact in required if fact not in available] or [
            "所需事实分散在不同政策，无法形成单一可审计依据"
        ]

    @staticmethod
    def _diversify(hits: list[PolicyHit], limit: int) -> list[PolicyHit]:
        ordered = sorted(
            hits,
            key=lambda hit: (
                -hit.rrf_score,
                hit.policy_version_id,
                hit.ordinal,
                hit.chunk_id,
            ),
        )
        values: list[PolicyHit] = []
        hashes: set[str] = set()
        policy_counts: dict[str, int] = {}
        scope_counts: dict[str, int] = {}
        for hit in ordered:
            if hit.content_hash and hit.content_hash in hashes:
                continue
            if policy_counts.get(hit.policy_version_id, 0) >= 2:
                continue
            if scope_counts.get(hit.scope, 0) >= 3:
                continue
            values.append(hit)
            hashes.add(hit.content_hash)
            policy_counts[hit.policy_version_id] = policy_counts.get(hit.policy_version_id, 0) + 1
            scope_counts[hit.scope] = scope_counts.get(hit.scope, 0) + 1
            if len(values) >= limit:
                break
        return values

    @staticmethod
    def _json_hit(hit: PolicyHit) -> dict[str, Any]:
        value = asdict(hit)
        for key in ("effective_from", "effective_to"):
            if value[key] is not None:
                value[key] = str(value[key])
        return value

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _redact(value: str) -> str:
        value = re.sub(r"(?<!\d)1\d{10}(?!\d)", "1**********", value)
        return re.sub(r"[\w.+-]+@[\w.-]+", "***@***", value)
