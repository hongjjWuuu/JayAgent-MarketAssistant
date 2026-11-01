import pytest

from app.domain.models import ActionStatus, ConversationStatus, RuntimeTaskStatus
from app.domain.transitions import (
    InvalidStateTransition,
    ensure_action_transition,
    ensure_conversation_transition,
    ensure_task_transition,
)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ConversationStatus.AI_ACTIVE, ConversationStatus.WAITING_USER),
        (ConversationStatus.WAITING_USER, ConversationStatus.AI_ACTIVE),
        (ConversationStatus.HANDOFF_QUEUED, ConversationStatus.HUMAN_ACTIVE),
        (ConversationStatus.HUMAN_ACTIVE, ConversationStatus.RESOLVED),
        (ConversationStatus.RESOLVED, ConversationStatus.CLOSED),
    ],
)
def test_valid_conversation_transitions(current, target):
    ensure_conversation_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ActionStatus.QUEUED, ActionStatus.EXECUTING),
        (ActionStatus.EXECUTING, ActionStatus.UNKNOWN_NEEDS_RECONCILIATION),
        (ActionStatus.UNKNOWN_NEEDS_RECONCILIATION, ActionStatus.SUCCEEDED),
    ],
)
def test_valid_action_transitions(current, target):
    ensure_action_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (RuntimeTaskStatus.QUEUED, RuntimeTaskStatus.RUNNING),
        (RuntimeTaskStatus.RUNNING, RuntimeTaskStatus.RETRY_WAIT),
        (RuntimeTaskStatus.RETRY_WAIT, RuntimeTaskStatus.RUNNING),
        (RuntimeTaskStatus.RUNNING, RuntimeTaskStatus.DEAD_LETTER),
    ],
)
def test_valid_task_transitions(current, target):
    ensure_task_transition(current, target)


@pytest.mark.parametrize(
    ("checker", "current", "target"),
    [
        (ensure_conversation_transition, ConversationStatus.CLOSED, ConversationStatus.AI_ACTIVE),
        (ensure_action_transition, ActionStatus.SUCCEEDED, ActionStatus.EXECUTING),
        (ensure_task_transition, RuntimeTaskStatus.COMPLETED, RuntimeTaskStatus.RUNNING),
    ],
)
def test_terminal_states_cannot_be_reversed(checker, current, target):
    with pytest.raises(InvalidStateTransition):
        checker(current, target)
