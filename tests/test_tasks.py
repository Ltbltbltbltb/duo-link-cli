"""Tests for the SQLite task queue (TaskStore API)."""

from __future__ import annotations

import tempfile
import threading
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
        self.assertEqual(reloaded.worker_name, "w-a")

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
        t = self.store.add_task(
            target="terminal_a",
            command="echo",
            args=["s1"],
            next_on_success=[
                {"target": "terminal_b", "command": "echo", "args": ["s2"]}
            ],
        )
        self.store.claim_next_task(target="terminal_a", worker_name="w")
        self.store.mark_done(t.id, exit_code=0, stdout="", stderr="")
        # next_on_success should have created a new pending task
        tasks = self.store.list_tasks(status="pending")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].target, "terminal_b")

    def test_mark_failed_retries(self) -> None:
        t = self.store.add_task(target="any", command="false", max_attempts=3)
        self.store.claim_next_task(target="any", worker_name="w")
        self.store.mark_failed(t.id, exit_code=1, stdout="", stderr="err")
        reloaded = self.store.get_task(t.id)
        self.assertEqual(reloaded.status, "pending")

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


if __name__ == "__main__":
    unittest.main()
