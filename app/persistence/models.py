from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domain.models import utc_now


class Base(DeclarativeBase):
    pass


class TenantRow(Base):
    __tablename__ = "tenant"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TenantMemberRow(Base):
    __tablename__ = "tenant_member"
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), primary_key=True
    )
    subject: Mapped[str] = mapped_column(String(160), primary_key=True)
    role: Mapped[str] = mapped_column(String(40))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        CheckConstraint(
            "role IN ('customer', 'agent', 'tenant_admin', 'platform_admin')",
            name="ck_tenant_member_role",
        ),
    )


class CustomerRow(Base):
    __tablename__ = "customer"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("tenant_id", "id", name="uq_customer_tenant_id"),)


class ChannelIdentityRow(Base):
    __tablename__ = "channel_identity"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customer.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(40))
    channel_user_id: Mapped[str] = mapped_column(String(160))
    __table_args__ = (
        UniqueConstraint("tenant_id", "channel", "channel_user_id", name="uq_channel_identity"),
    )


class ConversationRow(Base):
    __tablename__ = "conversation"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customer.id"), index=True)
    channel: Mapped[str] = mapped_column(String(40), default="web")
    status: Mapped[str] = mapped_column(String(50), index=True)
    owner_type: Mapped[str] = mapped_column(String(40), default="ai")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __table_args__ = (
        Index("ix_conversation_tenant_customer_updated", "tenant_id", "customer_id", "updated_at"),
    )


class MessageRow(Base):
    __tablename__ = "message"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversation.id", ondelete="CASCADE"), index=True
    )
    sender_type: Mapped[str] = mapped_column(String(40))
    content: Mapped[str] = mapped_column(Text)
    facts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    task_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __table_args__ = (
        Index("ix_message_conversation_created", "conversation_id", "created_at"),
        Index(
            "uq_message_task_sender",
            "tenant_id",
            "task_id",
            "sender_type",
            unique=True,
            postgresql_where=text("task_id IS NOT NULL"),
        ),
    )


class ConversationEventRow(Base):
    __tablename__ = "conversation_event"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversation.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )


class SupportQueueRow(Base):
    __tablename__ = "support_queue"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class SupportQueueMemberRow(Base):
    __tablename__ = "support_queue_member"
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), primary_key=True
    )
    queue_id: Mapped[str] = mapped_column(
        ForeignKey("support_queue.id", ondelete="CASCADE"), primary_key=True
    )
    subject: Mapped[str] = mapped_column(String(160), primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        Index("ix_support_queue_member_subject", "tenant_id", "subject", "active"),
    )


class HandoffRow(Base):
    __tablename__ = "handoff"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversation.id"), index=True)
    queue_id: Mapped[str | None] = mapped_column(ForeignKey("support_queue.id"), nullable=True)
    reason: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(40), index=True)
    assigned_agent_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=50)
    context_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __table_args__ = (
        Index("ix_handoff_queue_order", "tenant_id", "status", "priority", "created_at"),
        Index(
            "uq_handoff_active_conversation",
            "tenant_id",
            "conversation_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'claimed')"),
        ),
    )


class OrderRow(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    customer_id: Mapped[str] = mapped_column(String(80), index=True)
    external_order_id: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(60))
    total_amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(10), default="CNY")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __table_args__ = (UniqueConstraint("tenant_id", "external_order_id", name="uq_order_external"),)


class OrderItemRow(Base):
    __tablename__ = "order_item"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    sku: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(300))
    quantity: Mapped[int] = mapped_column(Integer)


class ShipmentRow(Base):
    __tablename__ = "shipment"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(60))
    tracking_number: Mapped[str] = mapped_column(String(160))
    events: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class TicketRow(Base):
    __tablename__ = "ticket"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    customer_id: Mapped[str] = mapped_column(String(80), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversation.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __table_args__ = (
        Index("ix_ticket_tenant_customer_created", "tenant_id", "customer_id", "created_at"),
    )


class ActionPlanRow(Base):
    __tablename__ = "action_plan"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    customer_id: Mapped[str] = mapped_column(String(80), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversation.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(80))
    risk_level: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    preview: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ActionExecutionRow(Base):
    __tablename__ = "action_execution"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    action_plan_id: Mapped[str] = mapped_column(ForeignKey("action_plan.id"), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(60), index=True)
    receipt: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    task_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    reconciliation_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_execution_idempotency"),
    )


class ToolCallLogRow(Base):
    __tablename__ = "tool_call_log"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(100))
    risk_level: Mapped[str] = mapped_column(String(30))
    input_redacted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_redacted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(40))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PolicyVersionRow(Base):
    __tablename__ = "policy_version"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    policy_id: Mapped[str] = mapped_column(String(80), index=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    scope: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(40), index=True)
    index_status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    index_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    acl_json: Mapped[dict[str, Any]] = mapped_column("acl", JSON, default=dict)
    published_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __table_args__ = (
        UniqueConstraint("tenant_id", "scope", "version", name="uq_policy_scope_version"),
        Index(
            "uq_policy_current_scope",
            "tenant_id",
            "scope",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
    )


class PolicyDocumentRow(Base):
    __tablename__ = "policy_document"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    policy_version_id: Mapped[str] = mapped_column(
        ForeignKey("policy_version.id", ondelete="CASCADE")
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class PolicyChunkRow(Base):
    __tablename__ = "policy_chunk"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    policy_document_id: Mapped[str] = mapped_column(
        ForeignKey("policy_document.id", ondelete="CASCADE")
    )
    content: Mapped[str] = mapped_column(Text)
    ordinal: Mapped[int] = mapped_column(Integer)
    title_path: Mapped[list[str]] = mapped_column(JSON, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    embedding_model: Mapped[str] = mapped_column(String(160), default="hash-embedding-v1")
    embedding_dimension: Mapped[int] = mapped_column(Integer, default=1536)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "policy_document_id", "ordinal", name="uq_policy_chunk_ordinal"
        ),
        Index("ix_policy_chunk_tenant_document", "tenant_id", "policy_document_id"),
    )


class RetrievalProfileRow(Base):
    __tablename__ = "retrieval_profile"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    embedding_model: Mapped[str] = mapped_column(String(160))
    embedding_dimension: Mapped[int] = mapped_column(Integer)
    retriever_version: Mapped[str] = mapped_column(String(80), default="hybrid-rrf-v3")
    gate_version: Mapped[str] = mapped_column(String(80), default="v2")
    normalization_version: Mapped[str] = mapped_column(String(80), default="query-normalizer-v2")
    status: Mapped[str] = mapped_column(String(20), index=True, default="building")
    indexed_chunks: Mapped[int] = mapped_column(Integer, default=0)
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    evaluation_run_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    gate_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quality_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_profile_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    calibration_run_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    holdout_run_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    calibration_dataset: Mapped[str] = mapped_column(String(200), default="")
    validation_datasets: Mapped[list[str]] = mapped_column(JSON, default=list)
    config_hash: Mapped[str] = mapped_column(String(64), default="")
    activated_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_retrieval_profile_tenant_id"),
        Index(
            "uq_retrieval_profile_active_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )


class PolicyChunkEmbeddingRow(Base):
    __tablename__ = "policy_chunk_embedding"
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    chunk_id: Mapped[str] = mapped_column(
        ForeignKey("policy_chunk.id", ondelete="CASCADE"), index=True
    )
    retrieval_profile_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_profile.id", ondelete="CASCADE"), index=True
    )
    embedding_model: Mapped[str] = mapped_column(String(160))
    embedding_dimension: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ready")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "chunk_id", "retrieval_profile_id", name="uq_chunk_embedding_profile"
        ),
    )


class RetrievalSnapshotRow(Base):
    __tablename__ = "retrieval_snapshot"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    message_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    query_redacted: Mapped[str] = mapped_column(Text)
    query_hash: Mapped[str] = mapped_column(String(64), index=True)
    filters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    decision: Mapped[str] = mapped_column(String(30), index=True)
    reason_code: Mapped[str] = mapped_column(String(80))
    confidence: Mapped[float] = mapped_column(Float)
    retriever_version: Mapped[str] = mapped_column(String(80))
    embedding_model: Mapped[str] = mapped_column(String(160))
    embedding_dimension: Mapped[int] = mapped_column(Integer, default=1536)
    normalization_version: Mapped[str] = mapped_column(
        String(80), default="query-normalizer-v1"
    )
    diagnostics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    gate_version: Mapped[str] = mapped_column(String(80))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RuntimeTaskRow(Base):
    __tablename__ = "runtime_task"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    task_type: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(40), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    lease_owner: Mapped[str | None] = mapped_column(String(160), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    replay_of_task_id: Mapped[str | None] = mapped_column(
        ForeignKey("runtime_task.id"), nullable=True
    )
    replayed_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    replay_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __table_args__ = (
        UniqueConstraint("replay_of_task_id", name="uq_runtime_task_replay_once"),
        Index(
            "ix_runtime_task_claim_ready",
            "available_at",
            "created_at",
            postgresql_where=text("status IN ('queued', 'retry_wait')"),
        ),
        Index(
            "ix_runtime_task_recover_lease",
            "lease_expires_at",
            postgresql_where=text("status = 'running'"),
        ),
    )


class RuntimeTaskEventRow(Base):
    __tablename__ = "runtime_task_event"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("runtime_task.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(80))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RuntimeWorkerRow(Base):
    __tablename__ = "runtime_worker"
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    current_task_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    hostname: Mapped[str] = mapped_column(String(255), default="")
    process_id: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class AuditEventRow(Base):
    __tablename__ = "audit_event"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    actor_id: Mapped[str] = mapped_column(String(160), index=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    resource_type: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(160))
    request_id: Mapped[str] = mapped_column(String(160), index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class LlmTraceRow(Base):
    __tablename__ = "llm_trace"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    request_id: Mapped[str] = mapped_column(String(160), default="")
    provider: Mapped[str] = mapped_column(String(80), default="")
    model: Mapped[str] = mapped_column(String(160))
    prompt_version: Mapped[str] = mapped_column(String(80))
    prompt_hash: Mapped[str] = mapped_column(String(64), default="")
    call_type: Mapped[str] = mapped_column(String(80), default="classify_intent")
    status: Mapped[str] = mapped_column(String(30), default="succeeded")
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    input_redacted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_redacted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    fallback_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    budget_json: Mapped[dict[str, Any]] = mapped_column("budget", JSON, default=dict)
    allowed_citation_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CompensationProposalRow(Base):
    __tablename__ = "compensation_proposal"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    customer_id: Mapped[str] = mapped_column(String(80), index=True)
    order_id: Mapped[str] = mapped_column(String(80), index=True)
    conversation_id: Mapped[str] = mapped_column(String(80), index=True)
    retrieval_snapshot_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    policy_version_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    recommended_amount: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(30), default="proposed")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AgentRunRow(Base):
    __tablename__ = "agent_run"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    message_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    request_id: Mapped[str] = mapped_column(String(160), default="", index=True)
    provider: Mapped[str] = mapped_column(String(80), default="")
    model: Mapped[str] = mapped_column(String(160), default="")
    prompt_version: Mapped[str] = mapped_column(String(80), default="")
    graph_version: Mapped[str] = mapped_column(String(80), default="week5-v1")
    input_hash: Mapped[str] = mapped_column(String(64))
    intent: Mapped[str] = mapped_column(String(40), default="")
    risk: Mapped[str] = mapped_column(String(40), default="")
    result_kind: Mapped[str] = mapped_column(String(80), default="")
    steps: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    fallback_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    retrieval_snapshot_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    action_plan_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    handoff_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)
    input_redacted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_redacted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EvaluationCaseRow(Base):
    __tablename__ = "evaluation_case"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(200))
    dataset: Mapped[str] = mapped_column(String(120), default="week4_gold")
    dataset_version: Mapped[str] = mapped_column(String(60), default="v1")
    case_type: Mapped[str] = mapped_column(String(60), default="answerable")
    input_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    expected: Mapped[dict[str, Any]] = mapped_column(JSON)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "dataset", "dataset_version", "name", name="uq_evaluation_case_name"
        ),
    )


class EvaluationDatasetRow(Base):
    __tablename__ = "evaluation_dataset"
    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    dataset: Mapped[str] = mapped_column(String(120), primary_key=True)
    version: Mapped[str] = mapped_column(String(60), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    case_count: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'frozen')", name="ck_evaluation_dataset_status"),
    )


class EvaluationRunRow(Base):
    __tablename__ = "evaluation_run"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(40))
    dataset: Mapped[str] = mapped_column(String(120), default="week4_gold")
    dataset_version: Mapped[str] = mapped_column(String(60), default="v1")
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quality_status: Mapped[str] = mapped_column(String(20), default="pending")
    quality_failures: Mapped[list[str]] = mapped_column(JSON, default=list)
    metric_schema_version: Mapped[str] = mapped_column(String(20), default="v2")
    query_analyzer_version: Mapped[str] = mapped_column(String(40), default="")
    scope_match_version: Mapped[str] = mapped_column(String(40), default="")
    threshold_grid_version: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvaluationResultRow(Base):
    __tablename__ = "evaluation_result"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_run.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[str] = mapped_column(ForeignKey("evaluation_case.id"))
    passed: Mapped[bool] = mapped_column(Boolean)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
