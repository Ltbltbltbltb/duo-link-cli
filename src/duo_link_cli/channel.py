from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

DEFAULT_CONTEXT_AGENTS = ("codex", "claude")
MESSAGE_RE = re.compile(
    r"^\[(?P<ts>.+?)\] (?P<sender>.+?) -> (?P<recipient>.+?): (?P<text>.*)$"
)
HAS_INOTIFY = shutil.which("inotifywait") is not None


@dataclass(frozen=True)
class Message:
    id: int
    ts: str
    sender: str
    recipient: str
    text: str
    raw: str
    acked: bool = False
    reply_to: int | None = None
    session: str | None = None

    def as_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "id": self.id,
            "ts": self.ts,
            "from": self.sender,
            "to": self.recipient,
            "msg": self.text,
            "acked": self.acked,
        }
        d["reply_to"] = self.reply_to
        if self.session is not None:
            d["session"] = self.session
        return d


class Channel:
    def __init__(self, root: Path):
        self.root = root
        self.log_path = root / "chat.log"
        self.lock_path = root / ".chat.lock"
        self.acks_path = root / ".acks"

    @classmethod
    def resolve(
        cls,
        explicit: str | None = None,
        cwd: Path | None = None,
        create_if_missing: bool = False,
    ) -> "Channel":
        if explicit:
            return cls(Path(explicit).expanduser().resolve())

        for env_name in ("DUO_CHANNEL", "DUO_LINK_DIR"):
            env_value = os.environ.get(env_name)
            if env_value:
                return cls(Path(env_value).expanduser().resolve())

        start = (cwd or Path.cwd()).resolve()
        for candidate in (start, *start.parents):
            nested = candidate / "duo-link"
            if nested.is_dir() or (nested / "chat.log").exists():
                return cls(nested)
            if (candidate / "chat.log").exists():
                return cls(candidate)

        if create_if_missing:
            return cls(start / "duo-link")

        raise FileNotFoundError(
            "no channel found. Use --channel, set $DUO_CHANNEL/$DUO_LINK_DIR, or run 'duo-link init'."
        )

    def init(self, context_agents: tuple[str, ...] = DEFAULT_CONTEXT_AGENTS) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.log_path.touch(exist_ok=True)
        self.lock_path.touch(exist_ok=True)
        for agent in context_agents:
            path = self.context_path(agent)
            if not path.exists():
                path.write_text(
                    f"## Contexto {agent}\n\nPreencha aqui o contexto relevante desta sessao.\n",
                    encoding="utf-8",
                )

    def require_log(self) -> None:
        if not self.log_path.exists():
            raise FileNotFoundError(
                f"channel log not found: {self.log_path}. Run 'duo-link init' first."
            )

    def cursor_path(self, agent: str) -> Path:
        return self.root / f".cursor.{agent}"

    def read_cursor(self, agent: str) -> int:
        path = self.cursor_path(agent)
        if path.exists():
            try:
                return int(path.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                return 0
        return 0

    def write_cursor(self, agent: str, position: int) -> None:
        self.cursor_path(agent).write_text(str(position), encoding="utf-8")

    def get_acked_ids(self) -> set[int]:
        if not self.acks_path.exists():
            return set()
        ids = set()
        for line in self.acks_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    ids.add(int(line))
                except ValueError:
                    pass
        return ids

    def ack(self, msg_id: int, agent: str) -> None:
        self.require_log()
        with self.lock_path.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            with self.acks_path.open("a", encoding="utf-8") as f:
                f.write(f"{msg_id}\n")
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def context_path(self, agent: str) -> Path:
        return self.root / f"context.{agent}.md"

    def write_context(self, agent: str, content: str) -> Path:
        self.init()
        path = self.context_path(agent)
        path.write_text(content, encoding="utf-8")
        return path

    def read_context(self, agent: str) -> str:
        path = self.context_path(agent)
        if not path.exists():
            raise FileNotFoundError(f"context not found for agent '{agent}': {path}")
        return path.read_text(encoding="utf-8")

    def send(
        self,
        sender: str,
        recipient: str,
        text: str,
        reply_to: int | None = None,
        session: str | None = None,
    ) -> Message:
        self.init()
        ts = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.lock_path.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            msg_id = len(self.log_path.read_text(encoding="utf-8").splitlines()) + 1
            record: dict[str, object] = {
                "id": msg_id,
                "ts": ts,
                "from": sender,
                "to": recipient,
                "text": text,
            }
            if reply_to is not None:
                record["reply_to"] = reply_to
            if session is not None:
                record["session"] = session
            with self.log_path.open("a", encoding="utf-8") as log_handle:
                log_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        return Message(
            id=msg_id,
            ts=ts,
            sender=sender,
            recipient=recipient,
            text=text,
            session=session,
            raw=f"[{ts}] {sender} -> {recipient}: {text}",
        )

    def rotate(self) -> Path:
        """Archive current chat.log and start fresh. Returns archive path."""
        self.require_log()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive = self.root / f"chat.log.{ts}"
        with self.lock_path.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            self.log_path.rename(archive)
            self.log_path.touch()
            # reset all cursors
            for cursor_file in self.root.glob(".cursor.*"):
                cursor_file.write_text("0", encoding="utf-8")
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        return archive

    def read_lines(self) -> list[str]:
        self.require_log()
        return self.log_path.read_text(encoding="utf-8").splitlines()

    def export_jsonl(self, session: str | None = None) -> str:
        """Export all messages as clean JSONL string."""
        messages = self.history(session=session)
        lines = [json.dumps(m.as_dict(), ensure_ascii=False) for m in messages]
        return "\n".join(lines) + "\n" if lines else ""

    def import_jsonl(self, data: str) -> int:
        """Import JSONL data into the channel. Returns count of imported messages."""
        self.init()
        count = 0
        with self.lock_path.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            existing = len(self.log_path.read_text(encoding="utf-8").splitlines())
            with self.log_path.open("a", encoding="utf-8") as log_handle:
                for line in data.strip().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        existing += 1
                        obj["id"] = existing
                        log_handle.write(json.dumps(obj, ensure_ascii=False) + "\n")
                        count += 1
                    except (json.JSONDecodeError, TypeError):
                        continue
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        return count

    def purge(self, keep: int = 0) -> int:
        """Remove old messages, keeping the last N. Returns count of purged messages."""
        self.require_log()
        with self.lock_path.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            lines = self.log_path.read_text(encoding="utf-8").splitlines()
            total = len(lines)
            if keep <= 0 or keep >= total:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                return 0
            kept = lines[-keep:]
            # renumber IDs
            renumbered = []
            for i, line in enumerate(kept, start=1):
                try:
                    obj = json.loads(line)
                    obj["id"] = i
                    renumbered.append(json.dumps(obj, ensure_ascii=False))
                except (json.JSONDecodeError, TypeError):
                    renumbered.append(line)
            self.log_path.write_text("\n".join(renumbered) + "\n", encoding="utf-8")
            # reset all cursors
            for cursor_file in self.root.glob(".cursor.*"):
                cursor_file.write_text("0", encoding="utf-8")
            # clear acks (IDs changed)
            if self.acks_path.exists():
                self.acks_path.write_text("", encoding="utf-8")
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        return total - keep

    def stats(self) -> dict[str, object]:
        """Per-agent statistics."""
        messages = self.history()
        acked_ids = self.get_acked_ids()
        by_sender: dict[str, int] = {}
        by_recipient: dict[str, int] = {}
        for m in messages:
            by_sender[m.sender] = by_sender.get(m.sender, 0) + 1
            by_recipient[m.recipient] = by_recipient.get(m.recipient, 0) + 1
        agents = sorted(set(by_sender) | set(by_recipient))
        per_agent = {}
        for a in agents:
            per_agent[a] = {
                "sent": by_sender.get(a, 0),
                "received": by_recipient.get(a, 0),
            }
        return {
            "total_messages": len(messages),
            "total_acked": len(acked_ids),
            "agents": per_agent,
        }

    def history(
        self,
        limit: int = 0,
        agent: str | None = None,
        session: str | None = None,
        sender: str | None = None,
        recipient: str | None = None,
        reply_to: int | None = None,
    ) -> list[Message]:
        acked_ids = self.get_acked_ids()
        messages = []
        for line in self.read_lines():
            message = self.parse_line(line)
            if message is None:
                continue
            if agent and agent not in (message.sender, message.recipient):
                continue
            if session is not None and message.session != session:
                continue
            if sender is not None and message.sender != sender:
                continue
            if recipient is not None and message.recipient != recipient:
                continue
            if reply_to is not None and message.reply_to != reply_to:
                continue
            if message.id in acked_ids:
                message = Message(
                    id=message.id,
                    ts=message.ts,
                    sender=message.sender,
                    recipient=message.recipient,
                    text=message.text,
                    raw=message.raw,
                    acked=True,
                    reply_to=message.reply_to,
                    session=message.session,
                )
            messages.append(message)
        return messages[-limit:] if limit else messages

    def status(self, session: str | None = None) -> dict[str, object]:
        messages = self.history(session=session)
        agents = sorted(
            {
                side
                for message in messages
                for side in (message.sender, message.recipient)
            }
        )
        acked_count = sum(1 for m in messages if m.acked)
        return {
            "channel": str(self.root),
            "messages": len(messages),
            "acked_messages": acked_count,
            "pending_messages": len(messages) - acked_count,
            "agents": agents,
            "last": messages[-1].raw if messages else None,
            "has_inotify": HAS_INOTIFY,
        }

    def recv(
        self,
        recipient: str,
        timeout: float = 60,
        poll_interval: float = 0.5,
        session: str | None = None,
    ) -> Message | None:
        self.require_log()
        cursor_key = f"{recipient}.{session}" if session else recipient
        cursor = self.read_cursor(cursor_key)
        lines = self.read_lines()

        def _matches(msg: Message) -> bool:
            if msg.recipient != recipient:
                return False
            if session is not None and msg.session != session:
                return False
            return True

        # scan backlog from cursor position
        for idx, line in enumerate(lines[cursor:], start=cursor):
            message = self.parse_line(line)
            if message and _matches(message):
                self.write_cursor(cursor_key, idx + 1)
                return message

        # no backlog — wait for new messages
        seen = len(lines)
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = max(poll_interval, min(5.0, deadline - time.time()))
            self.wait_for_change(remaining)
            lines = self.read_lines()
            for idx, line in enumerate(lines[seen:], start=seen):
                message = self.parse_line(line)
                if message and _matches(message):
                    self.write_cursor(cursor_key, idx + 1)
                    return message
            seen = len(lines)
        return None

    def drain(self, recipient: str) -> list[Message]:
        """Consume and auto-ack all pending messages for recipient."""
        self.require_log()
        cursor = self.read_cursor(recipient)
        lines = self.read_lines()
        messages = []
        last_idx = cursor
        for idx, line in enumerate(lines[cursor:], start=cursor):
            message = self.parse_line(line)
            if message and message.recipient == recipient:
                self.ack(message.id, recipient)
                message = Message(
                    id=message.id,
                    ts=message.ts,
                    sender=message.sender,
                    recipient=message.recipient,
                    text=message.text,
                    raw=message.raw,
                    acked=True,
                )
                messages.append(message)
            last_idx = idx + 1
        if lines[cursor:]:
            self.write_cursor(recipient, last_idx)
        return messages

    def pending(self, recipient: str) -> list[Message]:
        """List pending (unconsumed) messages for recipient without advancing cursor."""
        self.require_log()
        cursor = self.read_cursor(recipient)
        lines = self.read_lines()
        messages = []
        for line in lines[cursor:]:
            message = self.parse_line(line)
            if message and message.recipient == recipient:
                messages.append(message)
        return messages

    def stream(
        self,
        agent: str | None = None,
        include_all: bool = False,
        poll_interval: float = 0.5,
    ) -> Iterator[Message]:
        self.require_log()
        seen = len(self.read_lines())
        while True:
            self.wait_for_change(10 if HAS_INOTIFY else poll_interval)
            lines = self.read_lines()
            for line in lines[seen:]:
                message = self.parse_line(line)
                if message is None:
                    continue
                if (
                    include_all
                    or agent is None
                    or agent in (message.sender, message.recipient)
                ):
                    yield message
            seen = len(lines)

    def wait_for_change(self, timeout_s: float) -> None:
        if HAS_INOTIFY:
            subprocess.run(
                [
                    "inotifywait",
                    "-qq",
                    "-t",
                    str(max(1, int(timeout_s))),
                    "-e",
                    "modify",
                    str(self.log_path),
                ],
                capture_output=True,
                check=False,
            )
            return
        time.sleep(timeout_s)

    @staticmethod
    def parse_line(line: str, line_number: int = 0) -> Message | None:
        stripped = line.strip()
        if not stripped:
            return None
        # try JSONL first
        try:
            obj = json.loads(stripped)
            msg_id = obj.get("id", line_number)
            ts = obj.get("ts", "")
            sender = obj.get("from", "")
            recipient = obj.get("to", "")
            text = obj.get("text", "")
            reply_to = obj.get("reply_to")
            session = obj.get("session")
            return Message(
                id=msg_id,
                ts=ts,
                sender=sender,
                recipient=recipient,
                text=text,
                raw=f"[{ts}] {sender} -> {recipient}: {text}",
                reply_to=reply_to,
                session=session,
            )
        except (json.JSONDecodeError, TypeError):
            pass
        # fallback: legacy string format
        match = MESSAGE_RE.match(stripped)
        if not match:
            return None
        return Message(
            id=line_number,
            ts=match.group("ts"),
            sender=match.group("sender"),
            recipient=match.group("recipient"),
            text=match.group("text"),
            raw=stripped,
        )
