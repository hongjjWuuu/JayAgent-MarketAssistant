"""Claim one task and remain alive until the reliability test terminates this process."""

from __future__ import annotations

import os
import time

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.repositories.postgres import PostgresRepository


def main() -> None:
    engine = create_engine(
        os.environ["CRASH_TEST_DATABASE_URL"],
        connect_args={"options": f"-csearch_path={os.environ['CRASH_TEST_SCHEMA']},public"},
    )
    repository = PostgresRepository(
        sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    )
    task = repository.claim_task("crash-worker", 2)
    print(task.task_id if task else "none", flush=True)
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
