"""Integration tests for the worker loop and task chaining."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from duo_link_cli.tasks import TaskStore
from duo_link_cli.worker import worker_loop


class WorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "duo-link"
        self.store = TaskStore(self.root)
        self.store.init_db()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_worker_executes_single_task(self) -> None:
        self.store.add_task(target="terminal_a", command="echo", args=["hello"])
        executed = worker_loop(self.store, "terminal_a", "w-a", max_iterations=1)
        self.assertEqual(executed, 1)
        task = self.store.get_task(1)
        self.assertEqual(task.status, "done")
        self.assertEqual(task.exit_code, 0)
        self.assertIn("hello", task.stdout)

    def test_worker_retries_on_failure(self) -> None:
        self.store.add_task(target="any", command="false", max_attempts=2)
        worker_loop(self.store, "any", "w", max_iterations=1)
        task = self.store.get_task(1)
        self.assertEqual(task.status, "pending")

        worker_loop(self.store, "any", "w", max_iterations=1)
        task = self.store.get_task(1)
        self.assertEqual(task.status, "failed")

    def test_worker_chains_next_on_success(self) -> None:
        self.store.add_task(
            target="terminal_a",
            command="echo",
            args=["step1"],
            next_on_success=[
                {"target": "terminal_b", "command": "echo", "args": ["step2"]}
            ],
        )
        worker_loop(self.store, "terminal_a", "w-a", max_iterations=1)
        tasks = self.store.list_tasks()
        self.assertEqual(len(tasks), 2)
        step2 = tasks[1]  # ordered by id ASC; task 2 is the chained one
        self.assertEqual(step2.target, "terminal_b")
        self.assertEqual(step2.status, "pending")

    def test_pipeline_a_b_a(self) -> None:
        self.store.add_task(
            target="terminal_a",
            command="echo",
            args=["phase1"],
            next_on_success=[
                {
                    "target": "terminal_b",
                    "command": "echo",
                    "args": ["phase2"],
                    "next_on_success": [
                        {"target": "terminal_a", "command": "echo", "args": ["phase3"]}
                    ],
                }
            ],
        )
        worker_loop(self.store, "terminal_a", "w-a", max_iterations=1)
        self.assertEqual(self.store.get_task(1).status, "done")

        worker_loop(self.store, "terminal_b", "w-b", max_iterations=1)
        self.assertEqual(self.store.get_task(2).status, "done")

        worker_loop(self.store, "terminal_a", "w-a", max_iterations=1)
        t3 = self.store.get_task(3)
        self.assertEqual(t3.status, "done")
        self.assertIn("phase3", t3.stdout)

    def test_two_workers_no_double_claim(self) -> None:
        for i in range(5):
            self.store.add_task(target="any", command="echo", args=[str(i)])

        results = [0, 0]

        def run_w(idx: int) -> None:
            s = TaskStore(self.root)
            results[idx] = worker_loop(
                s, "any", f"w-{idx}", poll_interval=0.05, max_iterations=5
            )

        t1 = threading.Thread(target=run_w, args=(0,))
        t2 = threading.Thread(target=run_w, args=(1,))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(results[0] + results[1], 5)
        done = self.store.list_tasks(status="done")
        self.assertEqual(len(done), 5)

    def test_worker_handles_missing_command(self) -> None:
        self.store.add_task(target="any", command="nonexistent_cmd_xyz", max_attempts=1)
        worker_loop(self.store, "any", "w", max_iterations=1)
        task = self.store.get_task(1)
        self.assertEqual(task.status, "failed")
        self.assertIn("not found", task.stderr)


if __name__ == "__main__":
    unittest.main()
