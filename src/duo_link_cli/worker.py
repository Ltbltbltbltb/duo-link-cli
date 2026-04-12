"""Autonomous worker that claims and executes tasks from the SQLite queue."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from .tasks import TaskStore


def run_task(task) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            [task.command, *task.args],
            capture_output=True,
            text=True,
            timeout=300,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"command not found: {task.command}"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout: command exceeded 300s"
    except Exception as exc:
        return 1, "", str(exc)


def worker_loop(
    store: TaskStore | str | Path,
    target: str,
    worker_name: str,
    poll_interval: float = 1.0,
    max_iterations: int = 0,
) -> int:
    """Run the worker loop. Returns count of tasks executed."""
    if not isinstance(store, TaskStore):
        store = TaskStore(Path(store) if not isinstance(store, Path) else store)
    store.init_db()
    executed = 0

    print(f"[worker] {worker_name} started, target={target}")
    sys.stdout.flush()

    try:
        while max_iterations == 0 or executed < max_iterations:
            task = store.claim_next_task(target=target, worker_name=worker_name)
            if task is None:
                if max_iterations > 0:
                    break
                time.sleep(poll_interval)
                continue

            print(
                f"[worker] claimed task {task.id}: {task.command} {' '.join(task.args)}"
            )
            sys.stdout.flush()

            rc, stdout, stderr = run_task(task)

            if rc == 0:
                store.mark_done(task.id, exit_code=rc, stdout=stdout, stderr=stderr)
                status_msg = "done"
            else:
                requeued = store.requeue_if_retryable(
                    task.id, exit_code=rc, stdout=stdout, stderr=stderr
                )
                if requeued is None:
                    failed_task = store.mark_failed(
                        task.id, exit_code=rc, stdout=stdout, stderr=stderr
                    )
                    status_msg = failed_task.status
                else:
                    status_msg = "pending (requeued)"

            print(f"[worker] task {task.id} rc={rc} -> {status_msg}")
            sys.stdout.flush()
            executed += 1

    except KeyboardInterrupt:
        print(f"\n[worker] {worker_name} stopped after {executed} task(s)")

    return executed
