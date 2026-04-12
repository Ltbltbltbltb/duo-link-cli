"""SQLite-backed autonomous task queue for duo-link."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TASK_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,
    status TEXT NOT NULL,
    command TEXT NOT NULL,
    args_json TEXT NOT NULL DEFAULT '[]',
    next_on_success_json TEXT NOT NULL DEFAULT '[]',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    claimed_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    finished_at TEXT,
    exit_code INTEGER,
    stdout TEXT,
    stderr TEXT
);
"""

TASK_INDEX = """
CREATE INDEX IF NOT EXISTS idx_tasks_status_target_created
ON tasks(status, target, created_at, id);
"""


@dataclass(frozen=True)
class Task:
    id: int
    target: str
    status: str
    command: str
    args: list[str]
    next_on_success: list[dict[str, Any]]
    attempts: int
    max_attempts: int
    claimed_by: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    exit_code: int | None
    stdout: str | None
    stderr: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Task":
        return cls(
            id=int(row["id"]),
            target=str(row["target"]),
            status=str(row["status"]),
            command=str(row["command"]),
            args=_loads_json_list(row["args_json"]),
            next_on_success=_loads_json_object_list(row["next_on_success_json"]),
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
            claimed_by=row["claimed_by"],
            created_at=str(row["created_at"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            exit_code=row["exit_code"],
            stdout=row["stdout"],
            stderr=row["stderr"],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "target": self.target,
            "status": self.status,
            "command": self.command,
            "args": list(self.args),
            "next_on_success": list(self.next_on_success),
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "claimed_by": self.claimed_by,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


def _loads_json_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _loads_json_object_list(raw: str | None) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _normalize_next_on_success(
    raw: list[dict[str, Any]] | dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [raw]
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _connection_db_path(conn: sqlite3.Connection) -> Path:
    row = conn.execute("PRAGMA database_list").fetchone()
    if row is None or not row[2]:
        raise ValueError("connection is not bound to a file-backed database")
    return Path(str(row[2])).resolve()


def _store_from_source(source: TaskStore | sqlite3.Connection | str | Path) -> "TaskStore":
    if isinstance(source, TaskStore):
        return source
    if isinstance(source, sqlite3.Connection):
        return TaskStore(_connection_db_path(source))
    return TaskStore(Path(source))


class TaskStore:
    def __init__(self, root: Path | str):
        path = Path(root).expanduser().resolve()
        if path.suffix == ".db" or path.name == "tasks.db":
            self.root = path.parent
            self.db_path = path
        else:
            self.root = path
            self.db_path = self.root / "tasks.db"

    @classmethod
    def resolve(
        cls,
        explicit: str | None = None,
        cwd: Path | None = None,
        create_if_missing: bool = False,
    ) -> "TaskStore":
        if explicit:
            return cls(Path(explicit).expanduser().resolve())

        for env_name in ("DUO_CHANNEL", "DUO_LINK_DIR"):
            env_value = os.environ.get(env_name)
            if env_value:
                return cls(Path(env_value).expanduser().resolve())

        start = (cwd or Path.cwd()).resolve()
        for candidate in (start, *start.parents):
            nested = candidate / "duo-link"
            if (
                nested.is_dir()
                or (nested / "chat.log").exists()
                or (nested / "tasks.db").exists()
            ):
                return cls(nested)
            if (candidate / "chat.log").exists() or (candidate / "tasks.db").exists():
                return cls(candidate)

        if create_if_missing:
            return cls(start / "duo-link")

        raise FileNotFoundError(
            "no task store found. Use --channel, set $DUO_CHANNEL/$DUO_LINK_DIR, "
            "or create the queue with 'duo-link task add'."
        )

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def init_db(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute(TASK_SCHEMA)
            conn.execute(TASK_INDEX)

    def require_db(self) -> None:
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"task database not found: {self.db_path}. Run 'duo-link task add' first."
            )

    def _insert_task_row(
        self,
        conn: sqlite3.Connection,
        *,
        target: str,
        command: str,
        args: list[str] | None = None,
        next_on_success: list[dict[str, Any]] | None = None,
        max_attempts: int = 3,
    ) -> int:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be >= 1")
        cursor = conn.execute(
            """
            INSERT INTO tasks (
                target,
                status,
                command,
                args_json,
                next_on_success_json,
                max_attempts
            ) VALUES (?, 'pending', ?, ?, ?, ?)
            """,
            (
                target,
                command,
                json.dumps(args or [], ensure_ascii=False),
                json.dumps(next_on_success or [], ensure_ascii=False),
                max_attempts,
            ),
        )
        return int(cursor.lastrowid)

    def _load_task(self, conn: sqlite3.Connection, task_id: int) -> Task | None:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return Task.from_row(row) if row is not None else None

    def add_task(
        self,
        *,
        target: str,
        command: str,
        args: list[str] | None = None,
        next_on_success: list[dict[str, Any]] | dict[str, Any] | None = None,
        max_attempts: int = 3,
    ) -> Task:
        self.init_db()
        with self.connect() as conn:
            task_id = self._insert_task_row(
                conn,
                target=target,
                command=command,
                args=args,
                next_on_success=_normalize_next_on_success(next_on_success),
                max_attempts=max_attempts,
            )
            task = self._load_task(conn, task_id)
        if task is None:
            raise RuntimeError(f"task {task_id} was inserted but could not be loaded")
        return task

    def add_tasks_from_specs(self, specs: list[dict[str, Any]]) -> list[Task]:
        self.init_db()
        created_ids: list[int] = []
        with self.connect() as conn:
            for spec in specs:
                command = spec.get("command")
                if not isinstance(command, str) or not command:
                    continue
                task_id = self._insert_task_row(
                    conn,
                    target=str(spec.get("target", "any")),
                    command=command,
                    args=[str(item) for item in spec.get("args", [])],
                    next_on_success=_normalize_next_on_success(
                        spec.get("next_on_success")
                    ),
                    max_attempts=int(spec.get("max_attempts", 3)),
                )
                created_ids.append(task_id)
            tasks = [self._load_task(conn, task_id) for task_id in created_ids]
        return [task for task in tasks if task is not None]

    def get_task(self, task_id: int) -> Task | None:
        self.require_db()
        with self.connect() as conn:
            return self._load_task(conn, task_id)

    def list_tasks(
        self,
        *,
        status: str | None = None,
        target: str | None = None,
        limit: int = 0,
    ) -> list[Task]:
        self.require_db()
        query = "SELECT * FROM tasks"
        conditions: list[str] = []
        params: list[Any] = []
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        if target is not None:
            conditions.append("target = ?")
            params.append(target)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id"
        if limit > 0:
            query += " LIMIT ?"
            params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [Task.from_row(row) for row in rows]

    def claim_next_task(
        self,
        *,
        target: str,
        worker_name: str | None = None,
        claimed_by: str | None = None,
    ) -> Task | None:
        claimant = worker_name or claimed_by
        if not claimant:
            raise TypeError("claim_next_task requires worker_name or claimed_by")
        self.init_db()
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT *
                FROM tasks
                WHERE status = 'pending'
                  AND target IN (?, 'any')
                ORDER BY id
                LIMIT 1
                """,
                (target,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            conn.execute(
                """
                UPDATE tasks
                SET status = 'running',
                    claimed_by = ?,
                    started_at = CURRENT_TIMESTAMP,
                    attempts = attempts + 1
                WHERE id = ?
                """,
                (claimant, row["id"]),
            )
            updated = self._load_task(conn, int(row["id"]))
            conn.execute("COMMIT")
            return updated
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def mark_done(
        self,
        task_id: int,
        *,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> list[Task]:
        self.require_db()
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = self._load_task(conn, task_id)
            if current is None:
                raise RuntimeError(f"task {task_id} not found")
            conn.execute(
                """
                UPDATE tasks
                SET status = 'done',
                    finished_at = CURRENT_TIMESTAMP,
                    exit_code = ?,
                    stdout = ?,
                    stderr = ?
                WHERE id = ?
                """,
                (exit_code, stdout, stderr, task_id),
            )
            created_ids: list[int] = []
            for spec in current.next_on_success:
                command = spec.get("command")
                if not isinstance(command, str) or not command:
                    continue
                next_id = self._insert_task_row(
                    conn,
                    target=str(spec.get("target", "any")),
                    command=command,
                    args=[str(item) for item in spec.get("args", [])],
                    next_on_success=_normalize_next_on_success(
                        spec.get("next_on_success")
                    ),
                    max_attempts=int(spec.get("max_attempts", 3)),
                )
                created_ids.append(next_id)
            new_tasks = [self._load_task(conn, next_id) for next_id in created_ids]
            conn.execute("COMMIT")
            return [task for task in new_tasks if task is not None]
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def mark_failed(
        self,
        task_id: int,
        *,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> Task:
        self.require_db()
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = self._load_task(conn, task_id)
            if current is None:
                raise RuntimeError(f"task {task_id} not found")
            if current.attempts < current.max_attempts:
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'pending',
                        claimed_by = NULL,
                        started_at = NULL,
                        finished_at = NULL,
                        exit_code = ?,
                        stdout = ?,
                        stderr = ?
                    WHERE id = ?
                    """,
                    (exit_code, stdout, stderr, task_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'failed',
                        finished_at = CURRENT_TIMESTAMP,
                        exit_code = ?,
                        stdout = ?,
                        stderr = ?
                    WHERE id = ?
                    """,
                    (exit_code, stdout, stderr, task_id),
                )
            updated = self._load_task(conn, task_id)
            conn.execute("COMMIT")
            if updated is None:
                raise RuntimeError(f"task {task_id} missing after mark_failed")
            return updated
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def requeue_if_retryable(
        self,
        task_id: int,
        *,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> Task | None:
        self.require_db()
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = self._load_task(conn, task_id)
            if current is None or current.attempts >= current.max_attempts:
                conn.execute("ROLLBACK")
                return None
            conn.execute(
                """
                UPDATE tasks
                SET status = 'pending',
                    claimed_by = NULL,
                    started_at = NULL,
                    finished_at = NULL,
                    exit_code = ?,
                    stdout = ?,
                    stderr = ?
                WHERE id = ?
                """,
                (exit_code, stdout, stderr, task_id),
            )
            updated = self._load_task(conn, task_id)
            conn.execute("COMMIT")
            return updated
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def retry_task(self, task_id: int) -> Task | None:
        self.require_db()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = 'pending',
                    claimed_by = NULL,
                    started_at = NULL,
                    finished_at = NULL
                WHERE id = ?
                """,
                (task_id,),
            )
            return self._load_task(conn, task_id)


def init_db(source: TaskStore | str | Path) -> sqlite3.Connection:
    store = _store_from_source(source)
    store.init_db()
    return store.connect()


def add_task(
    source: TaskStore | sqlite3.Connection | str | Path,
    target: str,
    command: str,
    args: list[str] | None = None,
    next_on_success: list[dict[str, Any]] | dict[str, Any] | None = None,
    max_attempts: int = 3,
) -> dict[str, Any]:
    task = _store_from_source(source).add_task(
        target=target,
        command=command,
        args=args,
        next_on_success=next_on_success,
        max_attempts=max_attempts,
    )
    return task.as_dict()


def get_task(
    source: TaskStore | sqlite3.Connection | str | Path,
    task_id: int,
) -> dict[str, Any] | None:
    task = _store_from_source(source).get_task(task_id)
    return task.as_dict() if task is not None else None


def list_tasks(
    source: TaskStore | sqlite3.Connection | str | Path,
    *,
    status: str | None = None,
    target: str | None = None,
    limit: int = 0,
) -> list[dict[str, Any]]:
    return [
        task.as_dict()
        for task in _store_from_source(source).list_tasks(
            status=status,
            target=target,
            limit=limit,
        )
    ]


__all__ = [
    "TASK_INDEX",
    "TASK_SCHEMA",
    "Task",
    "TaskStore",
    "add_task",
    "get_task",
    "init_db",
    "list_tasks",
]
