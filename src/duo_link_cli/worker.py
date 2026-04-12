"""Autonomous worker that claims and executes tasks from the SQLite queue."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from .channel import Channel
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


def emit_worker_event(
    *,
    channel: Channel | None,
    sender: str,
    recipient: str | None,
    text: str,
    session: str | None = None,
    priority: str = "normal",
) -> None:
    if channel is None or not recipient:
        return
    channel.send(
        sender,
        recipient,
        text,
        session=session,
        priority=priority,
        msg_type="status",
    )


def worker_loop(
    store: TaskStore | str | Path,
    target: str,
    worker_name: str,
    poll_interval: float = 1.0,
    max_iterations: int = 0,
    notify_to: str | None = None,
    notify_channel: Channel | str | Path | None = None,
    notify_session: str | None = None,
) -> int:
    """Run the worker loop. Returns count of tasks executed."""
    if not isinstance(store, TaskStore):
        store = TaskStore(Path(store) if not isinstance(store, Path) else store)
    store.init_db()
    if notify_channel is None and notify_to:
        channel = Channel(store.root)
    elif isinstance(notify_channel, Channel):
        channel = notify_channel
    elif notify_channel is not None:
        channel = Channel(Path(notify_channel))
    else:
        channel = None
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
            emit_worker_event(
                channel=channel,
                sender=worker_name,
                recipient=notify_to,
                session=notify_session,
                text=(
                    f"task {task.id} claimed target={task.target} "
                    f"cmd={task.command} {' '.join(task.args)}".strip()
                ),
            )

            rc, stdout, stderr = run_task(task)

            if rc == 0:
                new_tasks = store.mark_done(task.id, exit_code=rc, stdout=stdout, stderr=stderr)
                status_msg = "done"
                if new_tasks:
                    status_msg += f", enqueued {len(new_tasks)} next task(s)"
                emit_worker_event(
                    channel=channel,
                    sender=worker_name,
                    recipient=notify_to,
                    session=notify_session,
                    text=f"task {task.id} done rc={rc} target={task.target}",
                )
            else:
                requeued = store.requeue_if_retryable(
                    task.id, exit_code=rc, stdout=stdout, stderr=stderr
                )
                if requeued is None:
                    failed_task = store.mark_failed(
                        task.id, exit_code=rc, stdout=stdout, stderr=stderr
                    )
                    status_msg = failed_task.status
                    emit_worker_event(
                        channel=channel,
                        sender=worker_name,
                        recipient=notify_to,
                        session=notify_session,
                        text=f"task {task.id} failed rc={rc} target={task.target}",
                        priority="high",
                    )
                else:
                    status_msg = "pending (requeued)"
                    emit_worker_event(
                        channel=channel,
                        sender=worker_name,
                        recipient=notify_to,
                        session=notify_session,
                        text=(
                            f"task {task.id} requeued rc={rc} target={task.target} "
                            f"attempts={requeued.attempts}/{requeued.max_attempts}"
                        ),
                        priority="high",
                    )

            print(f"[worker] task {task.id} rc={rc} -> {status_msg}")
            sys.stdout.flush()
            executed += 1

    except KeyboardInterrupt:
        print(f"\n[worker] {worker_name} stopped after {executed} task(s)")

    return executed
