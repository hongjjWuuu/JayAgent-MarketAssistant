from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class ConversationStatus(StrEnum):
    NEW = "new"
    AI_ACTIVE = "ai_active"
    WAITING_USER = "waiting_user"
    APPROVAL_PENDING = "approval_pending"
    HANDOFF_QUEUED = "handoff_queued"
    HUMAN_ACTIVE = "human_active"
    RESOLVED = "resolved"
    CLOSED = "closed"


class ActionStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    APPROVAL_REQUIRED = "approval_required"
    APPROVED = "approved"
    QUEUED = "queued"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    RETRYING = "retrying"
    UNKNOWN_NEEDS_RECONCILIATION = "unknown_needs_reconciliation"
    FAILED = "failed"
    COMPENSATION_REQUIRED = "compensation_required"
    CANCELLED = "cancelled"


class RuntimeTaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    COMPLETED = "completed"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"


class Order(BaseModel):
    order_id: str
    tenant_id: str
    customer_id: str
    status: str
    total_amount: float
    shipment_status: str
    tracking_number: str
    updated_at: datetime = Field(default_factory=utc_now)


class Shipment(BaseModel):
    order_id: str
    tenant_id: str
    customer_id: str
    status: str
    tracking_number: str
    carrier: str = "demo-logistics"
    updated_at: datetime = Field(default_factory=utc_now)


class Message(BaseModel):
    message_id: str
    sender_type: str
    content: str
    created_at: datetime = Field(default_factory=utc_now)
    facts: dict[str, Any] = Field(default_factory=dict)
    task_id: str | None = None


class Conversation(BaseModel):
    conversation_id: str
    tenant_id: str
    customer_id: str
    channel: str = "web"
    status: ConversationStatus = ConversationStatus.AI_ACTIVE
    owner_type: str = "ai"
    messages: list[Message] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Handoff(BaseModel):
    handoff_id: str
    tenant_id: str
    conversation_id: str
    queue_id: str = "queue_general"
    reason: str
    status: str = "queued"
    assigned_agent_id: str | None = None
    priority: int = 50
    context_snapshot: dict[str, Any]
    created_at: datetime = Field(default_factory=utc_now)


class ActionPlan(BaseModel):
    action_plan_id: str
    tenant_id: str
    customer_id: str
    conversation_id: str
    action_type: str
    risk_level: str
    payload: dict[str, Any]
    preview: str
    idempotency_key: str
    status: ActionStatus = ActionStatus.APPROVAL_REQUIRED
    created_at: datetime = Field(default_factory=utc_now)


class ActionExecution(BaseModel):
    execution_id: str
    tenant_id: str
    action_plan_id: str
    idempotency_key: str
    status: ActionStatus
    receipt: dict[str, Any] = Field(default_factory=dict)
    task_id: str | None = None
    error_code: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    reconciliation_attempts: int = 0
    last_reconciled_at: datetime | None = None


class CompensationProposal(BaseModel):
    proposal_id: str
    tenant_id: str
    customer_id: str
    order_id: str
    conversation_id: str
    retrieval_snapshot_id: str | None = None
    policy_version_id: str | None = None
    recommended_amount: float = Field(gt=0, le=50)
    reason: str
    status: str = "proposed"
    expires_at: datetime
    created_at: datetime = Field(default_factory=utc_now)


class Ticket(BaseModel):
    ticket_id: str
    tenant_id: str
    customer_id: str
    conversation_id: str
    title: str
    description: str
    priority: str = "normal"
    status: str = "open"
    created_at: datetime = Field(default_factory=utc_now)


class AuditEvent(BaseModel):
    event_id: str
    tenant_id: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    request_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ConversationEvent(BaseModel):
    event_id: str
    tenant_id: str
    conversation_id: str
    event_type: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class RuntimeTask(BaseModel):
    task_id: str
    tenant_id: str
    task_type: str
    payload: dict[str, Any]
    conversation_id: str | None = None
    status: RuntimeTaskStatus = RuntimeTaskStatus.QUEUED
    attempts: int = 0
    max_attempts: int = 3
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    available_at: datetime = Field(default_factory=utc_now)
    last_error: str | None = None
    replay_of_task_id: str | None = None
    replayed_by: str | None = None
    replay_reason: str | None = None
    dead_lettered_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class PolicyVersion(BaseModel):
    policy_version_id: str
    tenant_id: str
    scope: str
    version: str
    title: str
    status: str = "draft"
    index_status: str = "pending"
    index_error: str | None = None
    content: str
    policy_id: str = ""
    content_hash: str = ""
    acl: dict[str, Any] = Field(default_factory=dict)
    published_by: str | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    published_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class RetrievalSnapshot(BaseModel):
    retrieval_snapshot_id: str
    tenant_id: str
    conversation_id: str | None = None
    message_id: str | None = None
    query_redacted: str
    query_hash: str
    filters: dict[str, Any] = Field(default_factory=dict)
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    decision: str
    reason_code: str
    confidence: float
    retriever_version: str = "hybrid-rrf-v1"
    embedding_model: str = "hash-embedding-v1"
    embedding_dimension: int = 1536
    normalization_version: str = "query-normalizer-v1"
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    gate_version: str = "v1"
    latency_ms: int = 0
    created_at: datetime = Field(default_factory=utc_now)


class EvaluationCase(BaseModel):
    evaluation_case_id: str
    tenant_id: str
    dataset: str
    dataset_version: str
    name: str
    case_type: str
    input_data: dict[str, Any]
    expected: dict[str, Any]
    tags: list[str] = Field(default_factory=list)
    active: bool = True


class EvaluationDataset(BaseModel):
    tenant_id: str
    dataset: str
    version: str
    status: str = "draft"
    case_count: int = 0
    content_hash: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    frozen_at: datetime | None = None


class EvaluationResult(BaseModel):
    evaluation_result_id: str
    tenant_id: str
    run_id: str
    case_id: str
    passed: bool
    metrics: dict[str, Any] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)


class EvaluationRun(BaseModel):
    evaluation_run_id: str
    tenant_id: str
    status: str = "queued"
    dataset: str = "week4_gold"
    dataset_version: str = "v1"
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    quality_status: str = "pending"
    quality_failures: list[str] = Field(default_factory=list)
    metric_schema_version: str = "v2"
    query_analyzer_version: str = ""
    scope_match_version: str = ""
    threshold_grid_version: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class RetrievalProfile(BaseModel):
    retrieval_profile_id: str
    tenant_id: str
    provider: str
    embedding_model: str
    embedding_dimension: int
    retriever_version: str = "hybrid-rrf-v3"
    gate_version: str = "v2"
    normalization_version: str = "query-normalizer-v2"
    status: str = "building"
    indexed_chunks: int = 0
    total_chunks: int = 0
    evaluation_run_id: str | None = None
    gate_config: dict[str, Any] = Field(default_factory=dict)
    quality_report: dict[str, Any] = Field(default_factory=dict)
    rejected_reason: str | None = None
    parent_profile_id: str | None = None
    calibration_run_id: str | None = None
    holdout_run_id: str | None = None
    calibration_dataset: str = ""
    validation_datasets: list[str] = Field(default_factory=list)
    config_hash: str = ""
    activated_by: str | None = None
    activated_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class LlmTrace(BaseModel):
    llm_trace_id: str
    tenant_id: str
    conversation_id: str | None = None
    message_id: str | None = None
    task_id: str | None = None
    request_id: str = ""
    provider: str = ""
    model: str
    prompt_version: str
    prompt_hash: str = ""
    call_type: str = "classify_intent"
    status: str = "succeeded"
    error_code: str | None = None
    input_redacted: dict[str, Any] = Field(default_factory=dict)
    output_redacted: dict[str, Any] = Field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    fallback_used: bool = False
    fallback_reason: str | None = None
    agent_run_id: str | None = None
    budget: dict[str, Any] = Field(default_factory=dict)
    allowed_citation_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class AgentRun(BaseModel):
    agent_run_id: str
    tenant_id: str
    conversation_id: str | None = None
    message_id: str | None = None
    task_id: str | None = None
    request_id: str = ""
    provider: str = ""
    model: str = ""
    prompt_version: str = ""
    graph_version: str = "week5-v1"
    input_hash: str
    intent: str = ""
    risk: str = ""
    result_kind: str = ""
    steps: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    fallback_used: bool = False
    fallback_reason: str | None = None
    error_code: str | None = None
    retrieval_snapshot_id: str | None = None
    action_plan_id: str | None = None
    handoff_id: str | None = None
    estimated_cost: float = 0.0
    input_redacted: dict[str, Any] = Field(default_factory=dict)
    output_redacted: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
