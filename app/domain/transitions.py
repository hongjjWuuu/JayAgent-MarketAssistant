from __future__ import annotations

from enum import StrEnum

from app.domain.models import ActionStatus, ConversationStatus, RuntimeTaskStatus


class InvalidStateTransition(ValueError):
    def __init__(self, entity: str, current: StrEnum, target: StrEnum) -> None:
        self.entity = entity
        self.current = current
        self.target = target
        super().__init__(f"{entity} cannot transition from {current.value} to {target.value}")


class LeaseLostError(RuntimeError):
    pass


CONVERSATION_TRANSITIONS: dict[ConversationStatus, frozenset[ConversationStatus]] = {
    ConversationStatus.NEW: frozenset({ConversationStatus.AI_ACTIVE}),
    ConversationStatus.AI_ACTIVE: frozenset(
        {
            ConversationStatus.WAITING_USER,
            ConversationStatus.APPROVAL_PENDING,
            ConversationStatus.HANDOFF_QUEUED,
            ConversationStatus.RESOLVED,
        }
    ),
    ConversationStatus.WAITING_USER: frozenset(
        {
            ConversationStatus.AI_ACTIVE,
            ConversationStatus.HANDOFF_QUEUED,
            ConversationStatus.RESOLVED,
        }
    ),
    ConversationStatus.APPROVAL_PENDING: frozenset(
        {
            ConversationStatus.AI_ACTIVE,
            ConversationStatus.HANDOFF_QUEUED,
            ConversationStatus.RESOLVED,
        }
    ),
    ConversationStatus.HANDOFF_QUEUED: frozenset(
        {ConversationStatus.HUMAN_ACTIVE, ConversationStatus.AI_ACTIVE}
    ),
    ConversationStatus.HUMAN_ACTIVE: frozenset(
        {
            ConversationStatus.RESOLVED,
            ConversationStatus.HANDOFF_QUEUED,
            ConversationStatus.AI_ACTIVE,
        }
    ),
    ConversationStatus.RESOLVED: frozenset(
        {ConversationStatus.CLOSED, ConversationStatus.AI_ACTIVE}
    ),
    ConversationStatus.CLOSED: frozenset(),
}

ACTION_TRANSITIONS: dict[ActionStatus, frozenset[ActionStatus]] = {
    ActionStatus.DRAFT: frozenset({ActionStatus.VALIDATED}),
    ActionStatus.VALIDATED: frozenset({ActionStatus.APPROVAL_REQUIRED}),
    ActionStatus.APPROVAL_REQUIRED: frozenset({ActionStatus.APPROVED, ActionStatus.QUEUED}),
    ActionStatus.APPROVED: frozenset({ActionStatus.QUEUED}),
    ActionStatus.QUEUED: frozenset({ActionStatus.EXECUTING, ActionStatus.CANCELLED}),
    ActionStatus.EXECUTING: frozenset(
        {
            ActionStatus.SUCCEEDED,
            ActionStatus.RETRYING,
            ActionStatus.FAILED,
            ActionStatus.UNKNOWN_NEEDS_RECONCILIATION,
        }
    ),
    ActionStatus.RETRYING: frozenset({ActionStatus.QUEUED, ActionStatus.FAILED}),
    ActionStatus.UNKNOWN_NEEDS_RECONCILIATION: frozenset(
        {ActionStatus.SUCCEEDED, ActionStatus.FAILED, ActionStatus.COMPENSATION_REQUIRED}
    ),
    ActionStatus.SUCCEEDED: frozenset(),
    ActionStatus.FAILED: frozenset(),
    ActionStatus.COMPENSATION_REQUIRED: frozenset(),
    ActionStatus.CANCELLED: frozenset(),
}

TASK_TRANSITIONS: dict[RuntimeTaskStatus, frozenset[RuntimeTaskStatus]] = {
    RuntimeTaskStatus.QUEUED: frozenset({RuntimeTaskStatus.RUNNING, RuntimeTaskStatus.CANCELLED}),
    RuntimeTaskStatus.RUNNING: frozenset(
        {
            RuntimeTaskStatus.COMPLETED,
            RuntimeTaskStatus.RETRY_WAIT,
            RuntimeTaskStatus.DEAD_LETTER,
        }
    ),
    RuntimeTaskStatus.RETRY_WAIT: frozenset(
        {RuntimeTaskStatus.RUNNING, RuntimeTaskStatus.CANCELLED}
    ),
    RuntimeTaskStatus.COMPLETED: frozenset(),
    RuntimeTaskStatus.DEAD_LETTER: frozenset(),
    RuntimeTaskStatus.CANCELLED: frozenset(),
}


def _ensure(entity: str, current: StrEnum, target: StrEnum, policy: dict) -> None:
    if current == target:
        return
    if target not in policy[current]:
        raise InvalidStateTransition(entity, current, target)


def ensure_conversation_transition(current: ConversationStatus, target: ConversationStatus) -> None:
    _ensure("conversation", current, target, CONVERSATION_TRANSITIONS)


def ensure_action_transition(current: ActionStatus, target: ActionStatus) -> None:
    _ensure("action_execution", current, target, ACTION_TRANSITIONS)


def ensure_task_transition(current: RuntimeTaskStatus, target: RuntimeTaskStatus) -> None:
    _ensure("runtime_task", current, target, TASK_TRANSITIONS)
