from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import Engine, text

from app.core.config import settings
from app.domain.models import RuntimeTask, RuntimeTaskStatus, utc_now
from app.domain.transitions import LeaseLostError
from app.repositories.postgres import PostgresRepository
from app.runtime.worker import LeaseGuard, TaskWorker

pytestmark = pytest.mark.integration


def _finish_ready_tasks(repository: PostgresRepository) -> None:
    with repository.session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE runtime_task SET status = 'completed', lease_owner = NULL, "
                "lease_expires_at = NULL WHERE status IN ('queued', 'retry_wait', 'running')"
            )
        )


def test_four_workers_complete_one_hundred_tasks_once(
    postgres_repository: PostgresRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    _finish_ready_tasks(postgres_repository)
    count = int(os.getenv("WORKER_TEST_COUNT", "4"))
    total = 100
    seen: set[str] = set()
    duplicates: list[str] = []
    lock = threading.Lock()

    def handle(task: RuntimeTask, guard: LeaseGuard) -> None:
        guard.ensure_owned()
        with lock:
            if task.task_id in seen:
                duplicates.append(task.task_id)
            seen.add(task.task_id)

    for index in range(total):
        postgres_repository.enqueue_task(
            RuntimeTask(
                task_id=f"stress_{index}_{time.time_ns()}",
                tenant_id="tenant_contract",
                task_type="stress",
                payload={"index": index},
            )
        )
    monkeypatch.setattr(settings, "task_lease_seconds", 3)
    workers = [
        TaskWorker(
            PostgresRepository(postgres_repository.session_factory),
            f"stress-worker-{index}",
            handlers={"stress": handle},
        )
        for index in range(count)
    ]

    def drain(worker: TaskWorker) -> None:
        while worker.run_once():
            pass

    with ThreadPoolExecutor(max_workers=count) as pool:
        list(pool.map(drain, workers))

    assert len(seen) == total
    assert duplicates == []
    tasks = postgres_repository.list_tasks("tenant_contract", "completed", "stress")
    assert len(tasks) == total
    assert all(task.attempts == 1 for task in tasks)


def test_long_task_heartbeat_prevents_lease_theft(
    postgres_repository: PostgresRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    _finish_ready_tasks(postgres_repository)
    monkeypatch.setattr(settings, "task_lease_seconds", 1)
    started = threading.Event()

    def slow(_task: RuntimeTask, guard: LeaseGuard) -> None:
        started.set()
        time.sleep(2.2)
        guard.ensure_owned()

    task = RuntimeTask(
        task_id=f"long_{time.time_ns()}",
        tenant_id="tenant_contract",
        task_type="slow",
        payload={},
    )
    postgres_repository.enqueue_task(task)
    first = TaskWorker(
        PostgresRepository(postgres_repository.session_factory),
        "long-worker-a",
        handlers={"slow": slow},
    )
    second = PostgresRepository(postgres_repository.session_factory)
    thread = threading.Thread(target=first.run_once)
    thread.start()
    assert started.wait(2)
    for _ in range(6):
        time.sleep(0.35)
        assert second.claim_task("long-worker-b", 1) is None
    thread.join(timeout=5)
    persisted = postgres_repository.get_task(task.task_id, task.tenant_id)
    assert persisted is not None
    assert persisted.status == RuntimeTaskStatus.COMPLETED
    assert persisted.attempts == 1


def test_old_worker_cannot_commit_after_lease_loss(
    postgres_repository: PostgresRepository,
) -> None:
    _finish_ready_tasks(postgres_repository)
    task = RuntimeTask(
        task_id=f"fence_{time.time_ns()}",
        tenant_id="tenant_contract",
        task_type="fence",
        payload={},
    )
    postgres_repository.enqueue_task(task)
    old_claim = postgres_repository.claim_task("old-worker", 30)
    assert old_claim is not None
    with postgres_repository.session_factory.begin() as session:
        session.execute(
            text("UPDATE runtime_task SET lease_expires_at = :expired WHERE id = :task_id"),
            {"expired": utc_now().replace(year=2000), "task_id": task.task_id},
        )
    new_claim = postgres_repository.claim_task("new-worker", 30)
    assert new_claim is not None
    with pytest.raises(LeaseLostError):
        postgres_repository.complete_task(old_claim, "old-worker")
    completed = postgres_repository.complete_task(new_claim, "new-worker")
    assert completed.status == RuntimeTaskStatus.COMPLETED
    assert completed.attempts == 2


def test_concurrent_dead_letter_replay_creates_one_child(
    postgres_repository: PostgresRepository,
) -> None:
    _finish_ready_tasks(postgres_repository)
    original = RuntimeTask(
        task_id=f"dead_{time.time_ns()}",
        tenant_id="tenant_contract",
        task_type="broken",
        payload={"value": 1},
        max_attempts=1,
    )
    postgres_repository.enqueue_task(original)
    claimed = postgres_repository.claim_task("dead-worker", 30)
    assert claimed is not None
    postgres_repository.fail_task(claimed, "expected failure", "dead-worker")

    def replay(index: int):
        repository = PostgresRepository(postgres_repository.session_factory)
        return repository.replay_dead_letter(
            original.task_id, original.tenant_id, f"admin-{index}", "fixed dependency"
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(replay, (1, 2)))
    assert first.task_id == second.task_id
    persisted = postgres_repository.get_task(original.task_id, original.tenant_id)
    assert persisted is not None
    assert persisted.status == RuntimeTaskStatus.DEAD_LETTER
    assert persisted.last_error == "expected failure"


def test_forced_process_termination_recovers_after_lease_expiry(
    postgres_repository: PostgresRepository, postgres_engine: Engine
) -> None:
    _finish_ready_tasks(postgres_repository)
    task = RuntimeTask(
        task_id=f"crash_{time.time_ns()}",
        tenant_id="tenant_contract",
        task_type="crash",
        payload={},
    )
    postgres_repository.enqueue_task(task)
    with postgres_engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
    environment = os.environ.copy()
    environment["CRASH_TEST_DATABASE_URL"] = postgres_engine.url.render_as_string(
        hide_password=False
    )
    environment["CRASH_TEST_SCHEMA"] = str(schema)
    process = subprocess.Popen(
        [sys.executable, "-u", "tests/integration/crash_worker_helper.py"],
        cwd=os.getcwd(),
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 8
        claimed = None
        while time.monotonic() < deadline:
            claimed = postgres_repository.get_task(task.task_id, task.tenant_id)
            if claimed and claimed.lease_owner == "crash-worker":
                break
            if process.poll() is not None:
                break
            time.sleep(0.1)
        if not claimed or claimed.lease_owner != "crash-worker":
            assert process.stderr is not None
            pytest.fail(f"crash worker did not claim task: {process.stderr.read()}")
        process.terminate()
        process.wait(timeout=5)
        time.sleep(2.2)
        recovered = postgres_repository.claim_task("recovery-worker", 30)
        assert recovered is not None
        assert recovered.task_id == task.task_id
        assert recovered.attempts == 2
        events = postgres_repository.list_task_events(task.task_id, task.tenant_id)
        assert "recovered" in [event["event_type"] for event in events]
        completed = postgres_repository.complete_task(recovered, "recovery-worker")
        assert completed.status == RuntimeTaskStatus.COMPLETED
    finally:
        if process.poll() is None:
            process.kill()
