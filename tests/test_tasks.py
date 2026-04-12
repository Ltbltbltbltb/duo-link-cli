"""Tests for the SQLite task queue (TaskStore API)."""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from duo_link_cli.tasks import TaskStore


class TaskStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "duo-link"
        self.store = TaskStore(self.root)
        self.store.init_db()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_init_db_creates_table(self) -> None:
        with self.store.connect() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
            ).fetchall()
        self.assertEqual(len(tables), 1)

    def test_add_task_returns_task(self) -> None:
        t = self.store.add_task(target="terminal_a", command="echo", args=["hello"])
        self.assertEqual(t.id, 1)
        self.assertEqual(t.status, "pending")

    def test_claim_next_returns_task_for_target(self) -> None:
        self.store.add_task(target="terminal_a", command="echo", args=["hi"])
        task = self.store.claim_next_task(target="terminal_a", worker_name="w-a")
        self.assertIsNotNone(task)
        self.assertEqual(task.command, "echo")
        reloaded = self.store.get_task(task.id)
        self.assertEqual(reloaded.status, "running")
        self.assertEqual(reloaded.claimed_by, "w-a")

    def test_claim_respects_target(self) -> None:
        self.store.add_task(target="terminal_b", command="echo", args=["b"])
        task = self.store.claim_next_task(target="terminal_a", worker_name="w-a")
        self.assertIsNone(task)

    def test_claim_picks_any_target(self) -> None:
        self.store.add_task(target="any", command="echo", args=["x"])
        task = self.store.claim_next_task(target="terminal_a", worker_name="w-a")
        self.assertIsNotNone(task)

    def test_claim_atomic_no_double(self) -> None:
        self.store.add_task(target="any", command="echo", args=["single"])
        results: list = [None, None]

        def claim(idx: int, tgt: str) -> None:
            s = TaskStore(self.root)
            results[idx] = s.claim_next_task(target=tgt, worker_name=f"w-{idx}")

        t1 = threading.Thread(target=claim, args=(0, "terminal_a"))
        t2 = threading.Thread(target=claim, args=(1, "terminal_b"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        claimed = [r for r in results if r is not None]
        self.assertEqual(len(claimed), 1)

    def test_mark_done_transitions(self) -> None:
        t = self.store.add_task(target="any", command="echo", args=["ok"])
        self.store.claim_next_task(target="any", worker_name="w")
        self.store.mark_done(t.id, exit_code=0, stdout="out", stderr="")
        reloaded = self.store.get_task(t.id)
        self.assertEqual(reloaded.status, "done")
        self.assertEqual(reloaded.exit_code, 0)

    def test_mark_done_enqueues_next(self) -> None:
        next_spec = [{"target": "terminal_b", "command": "echo", "args": ["s2"]}]
        t = self.store.add_task(
            target="terminal_a",
            command="echo",
            args=["s1"],
            next_on_success=next_spec,
        )
        self.store.claim_next_task(target="terminal_a", worker_name="w")
        # mark_done returns list[Task] of newly created next tasks
        created = self.store.mark_done(t.id, exit_code=0, stdout="", stderr="")
        # original task is now done
        done = self.store.get_task(t.id)
        self.assertEqual(done.status, "done")
        self.assertEqual(done.next_on_success, next_spec)
        # next tasks were auto-enqueued by mark_done
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].target, "terminal_b")

    def test_mark_failed_retries(self) -> None:
        t = self.store.add_task(target="any", command="false", max_attempts=3)
        self.store.claim_next_task(target="any", worker_name="w")
        requeued = self.store.requeue_if_retryable(
            t.id, exit_code=1, stdout="", stderr="err"
        )
        self.assertIsNotNone(requeued)
        self.assertEqual(requeued.status, "pending")

    def test_mark_failed_final(self) -> None:
        t = self.store.add_task(target="any", command="false", max_attempts=1)
        self.store.claim_next_task(target="any", worker_name="w")
        self.store.mark_failed(t.id, exit_code=1, stdout="", stderr="err")
        reloaded = self.store.get_task(t.id)
        self.assertEqual(reloaded.status, "failed")

    def test_retry_task(self) -> None:
        t = self.store.add_task(target="any", command="false", max_attempts=1)
        self.store.claim_next_task(target="any", worker_name="w")
        self.store.mark_failed(t.id, exit_code=1, stdout="", stderr="")
        ok = self.store.retry_task(t.id)
        self.assertTrue(ok)
        reloaded = self.store.get_task(t.id)
        self.assertEqual(reloaded.status, "pending")

    def test_list_tasks_filters(self) -> None:
        self.store.add_task(target="any", command="echo", args=["a"])
        t2 = self.store.add_task(target="any", command="echo", args=["b"])
        self.store.claim_next_task(target="any", worker_name="w")
        self.store.claim_next_task(target="any", worker_name="w")
        self.store.mark_done(t2.id, exit_code=0, stdout="", stderr="")
        done = self.store.list_tasks(status="done")
        self.assertEqual(len(done), 1)

    def test_wait_for_task_returns_when_task_reaches_done(self) -> None:
        task = self.store.add_task(target="any", command="echo", args=["ok"])
        claimed = self.store.claim_next_task(target="any", worker_name="w")

        def finish() -> None:
            time.sleep(0.1)
            self.store.mark_done(claimed.id, exit_code=0, stdout="ok", stderr="")

        thread = threading.Thread(target=finish)
        thread.start()
        final = self.store.wait_for_task(task.id, timeout=1.0, poll_interval=0.05)
        thread.join(timeout=1)

        self.assertIsNotNone(final)
        self.assertEqual(final.status, "done")
        self.assertEqual(final.exit_code, 0)

    def test_wait_for_task_times_out_when_task_stays_non_final(self) -> None:
        task = self.store.add_task(target="any", command="echo", args=["ok"])
        self.store.claim_next_task(target="any", worker_name="w")
        final = self.store.wait_for_task(task.id, timeout=0.15, poll_interval=0.05)
        self.assertIsNone(final)


if __name__ == "__main__":
    unittest.main()
