from datetime import timedelta

from app.domain.models import RuntimeTask, RuntimeTaskStatus, utc_now


def test_expired_lease_is_reclaimed_without_double_claim(store):
    task = RuntimeTask(task_id="task_lease", tenant_id="tenant_demo", task_type="noop", payload={})
    store.enqueue_task(task)
    first = store.claim_task("worker-a", 30)
    assert first and first.lease_owner == "worker-a"
    assert store.claim_task("worker-b", 30) is None
    store.runtime_tasks[task.task_id].lease_expires_at = utc_now() - timedelta(seconds=1)
    recovered = store.claim_task("worker-b", 30)
    assert recovered and recovered.lease_owner == "worker-b"
    assert recovered.attempts == 2


def test_failed_task_retries_then_dead_letters(store):
    task = RuntimeTask(
        task_id="task_retry", tenant_id="tenant_demo", task_type="noop", payload={}, max_attempts=2
    )
    store.enqueue_task(task)
    claimed = store.claim_task("worker", 30)
    failed = store.fail_task(claimed, "temporary", "worker")
    assert failed.status == RuntimeTaskStatus.RETRY_WAIT
    failed.available_at = utc_now()
    claimed = store.claim_task("worker", 30)
    failed = store.fail_task(claimed, "permanent", "worker")
    assert failed.status == RuntimeTaskStatus.DEAD_LETTER
