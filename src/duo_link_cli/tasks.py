"""Task queue backed by SQLite WAL for autonomous inter-terminal execution."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """\
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    command TEXT NOT NULL,
    args_json TEXT NOT NULL DEFAULT '[]',
    next_on_success_json TEXT NOT NULL DEFAULT '[]',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    claimed_by TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now', 'localtime')),
    started_at TEXT,
    finished_at TEXT,
    exit_code INTEGER,
    stdout TEXT,
    stderr TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_status_target
ON tasks(status, target, id);
"""


def init_db(db_path: str | Path = "tasks.db") -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.executescript(SCHEMA)
    return conn


def add_task(
    conn: sqlite3.Connection,
    target: str,
    command: str,
    args: list[str] | None = None,
    next_on_success: list[dict] | None = None,
    max_attempts: int = 3,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO tasks (target, status, command, args_json, next_on_success_json, max_attempts)
        VALUES (?, 'pending', ?, ?, ?, ?)
        """,
        (
            target,
            command,
            json.dumps(args or [], ensure_ascii=False),
            json.dumps(next_on_success or [], ensure_ascii=False),
            max_attempts,
        ),
    )
    return cur.lastrowid


def claim_next(
    conn: sqlite3.Connection, target: str, worker_name: str
) -> sqlite3.Row | None:
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        """
        SELECT * FROM tasks
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
            started_at = strftime('%Y-%m-%dT%H:%M:%f', 'now', 'localtime'),
            attempts = attempts + 1
        WHERE id = ?
        """,
        (worker_name, row["id"]),
    )
    conn.execute("COMMIT")
    return row


def mark_done(
    conn: sqlite3.Connection,
    task_id: int,
    exit_code: int,
    stdout: str,
    stderr: str,
) -> list[int]:
    """Mark task as done and enqueue next_on_success tasks. Returns new task IDs."""
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        return []

    next_tasks = json.loads(row["next_on_success_json"])
    new_ids: list[int] = []

    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """
        UPDATE tasks
        SET status = 'done',
            finished_at = strftime('%Y-%m-%dT%H:%M:%f', 'now', 'localtime'),
            exit_code = ?,
            stdout = ?,
            stderr = ?
        WHERE id = ?
        """,
        (exit_code, stdout, stderr, task_id),
    )

    for task_spec in next_tasks:
        cur = conn.execute(
            """
            INSERT INTO tasks (target, status, command, args_json, next_on_success_json, max_attempts)
            VALUES (?, 'pending', ?, ?, ?, ?)
            """,
            (
                task_spec.get("target", "any"),
                task_spec["command"],
                json.dumps(task_spec.get("args", []), ensure_ascii=False),
                json.dumps(task_spec.get("next_on_success", []), ensure_ascii=False),
                task_spec.get("max_attempts", 3),
            ),
        )
        new_ids.append(cur.lastrowid)

    conn.execute("COMMIT")
    return new_ids


def mark_failed(
    conn: sqlite3.Connection,
    task_id: int,
    exit_code: int,
    stdout: str,
    stderr: str,
) -> None:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        return

    conn.execute("BEGIN IMMEDIATE")
    if row["attempts"] < row["max_attempts"]:
        conn.execute(
            """
            UPDATE tasks
            SET status = 'pending',
                claimed_by = NULL,
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
                finished_at = strftime('%Y-%m-%dT%H:%M:%f', 'now', 'localtime'),
                exit_code = ?,
                stdout = ?,
                stderr = ?
            WHERE id = ?
            """,
            (exit_code, stdout, stderr, task_id),
        )
    conn.execute("COMMIT")


def retry_task(conn: sqlite3.Connection, task_id: int) -> bool:
    """Reset a failed task back to pending. Returns True if successful."""
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None or row["status"] not in ("failed", "done"):
        return False
    conn.execute(
        """
        UPDATE tasks
        SET status = 'pending',
            claimed_by = NULL,
            attempts = 0
        WHERE id = ?
        """,
        (task_id,),
    )
    return True


def list_tasks(
    conn: sqlite3.Connection,
    status: str | None = None,
    target: str | None = None,
    limit: int = 50,
) -> list[sqlite3.Row]:
    query = "SELECT * FROM tasks WHERE 1=1"
    params: list = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if target:
        query += " AND target IN (?, 'any')"
        params.append(target)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return conn.execute(query, params).fetchall()


def get_task(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()


def task_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "target": row["target"],
        "status": row["status"],
        "command": row["command"],
        "args": json.loads(row["args_json"]),
        "next_on_success": json.loads(row["next_on_success_json"]),
        "attempts": row["attempts"],
        "max_attempts": row["max_attempts"],
        "claimed_by": row["claimed_by"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "exit_code": row["exit_code"],
    }
