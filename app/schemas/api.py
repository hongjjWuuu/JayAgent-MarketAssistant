from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    customer_id: str = Field(min_length=1)
    channel: Literal["web"] = "web"


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class HandoffCreate(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class ActionPlanCreate(BaseModel):
    conversation_id: str
    action_type: Literal["create_ticket", "submit_compensation"]
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=8, max_length=128)


class ActionConfirm(BaseModel):
    confirmation: Literal["confirm"]


class HandoffReply(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class HandoffResolve(BaseModel):
    resume_ai: bool = False


class HandoffTransfer(BaseModel):
    queue_id: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=3, max_length=500)


class PolicyCreate(BaseModel):
    scope: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=60)
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=10, max_length=100_000)
    acl: dict[str, list[str]] = Field(default_factory=dict)
    effective_from: datetime | None = None
    effective_to: datetime | None = None


class PolicyUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    content: str | None = Field(default=None, min_length=10, max_length=100_000)
    acl: dict[str, list[str]] | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None


class RetrievalDebugRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    filters: dict[str, str] = Field(default_factory=dict)
    top_k: int = Field(default=5, ge=1, le=20)


class EvaluationCaseInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    case_type: str = Field(min_length=1, max_length=60)
    input_data: dict[str, Any]
    expected: dict[str, Any]
    tags: list[str] = Field(default_factory=list)


class EvaluationExpected(BaseModel):
    decision: Literal["answer", "clarify", "handoff"]
    reason_codes: list[str] = Field(min_length=1)
    relevant_policy_ids: list[str] = Field(default_factory=list)
    relevant_policy_version_ids: list[str] = Field(default_factory=list)
    relevant_chunk_ids: list[str] = Field(default_factory=list)
    required_citation_policy_ids: list[str] = Field(default_factory=list)
    required_citation_policy_version_ids: list[str] = Field(default_factory=list)
    required_citation_chunk_ids: list[str] = Field(default_factory=list)
    forbidden_policy_ids: list[str] = Field(default_factory=list)
    forbidden_policy_version_ids: list[str] = Field(default_factory=list)
    forbidden_chunk_ids: list[str] = Field(default_factory=list)
    required_entities: list[str] = Field(default_factory=list)
    forbidden_entities: list[str] = Field(default_factory=list)


class StrictEvaluationCaseInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    case_type: str = Field(min_length=1, max_length=60)
    input_data: dict[str, Any]
    expected: EvaluationExpected
    tags: list[str] = Field(default_factory=list)


class EvaluationCaseImport(BaseModel):
    dataset: str = Field(min_length=1, max_length=120)
    dataset_version: str = Field(min_length=1, max_length=60)
    cases: list[EvaluationCaseInput | StrictEvaluationCaseInput] = Field(
        min_length=1, max_length=500
    )


class EvaluationRunRequest(BaseModel):
    dataset: str = "week4_gold"
    dataset_version: str = "v1"
    retrieval_profile_id: str | None = None
    evaluator: Literal["retrieval", "agent"] = "retrieval"
    split: Literal["all", "calibration", "holdout", "live_smoke"] = "all"
    provider: Literal["fake", "configured"] = "fake"


class RetrievalProfileCreate(BaseModel):
    retrieval_profile_id: str = Field(min_length=3, max_length=80)
    provider: Literal["hash", "openai_compatible"]
    embedding_model: str = Field(min_length=1, max_length=160)
    embedding_dimension: int = Field(ge=1, le=4096)
    parent_profile_id: str | None = Field(default=None, min_length=3, max_length=80)


class RetrievalProfileActivate(BaseModel):
    evaluation_run_id: str


class RetrievalProfileRollback(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class RetrievalProfileCalibration(BaseModel):
    evaluation_run_id: str


class RetrievalProfileValidation(BaseModel):
    gold_run_id: str
    holdout_run_id: str


class TaskReplay(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class ReconciliationResolve(BaseModel):
    outcome: Literal["succeeded", "failed", "compensation_required"]
    reason: str = Field(min_length=3, max_length=500)
    receipt: dict[str, Any] = Field(default_factory=dict)
