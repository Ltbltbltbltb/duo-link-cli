from __future__ import annotations

import fcntl
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
MESSAGE_RE = re.compile(r"^\[(?P<ts>.+?)\] (?P<sender>.+?) -> (?P<recipient>.+?): (?P<text>.*)$")
HAS_INOTIFY = shutil.which("inotifywait") is not None


@dataclass(frozen=True)
class Message:
    ts: str
    sender: str
    recipient: str
    text: str
    raw: str

    def as_dict(self) -> dict[str, str]:
        return {
            "ts": self.ts,
            "from": self.sender,
            "to": self.recipient,
            "msg": self.text,
        }


class Channel:
    def __init__(self, root: Path):
        self.root = root
        self.log_path = root / "chat.log"
        self.lock_path = root / ".chat.lock"

    @classmethod
    def resolve(cls, explicit: str | None = None, cwd: Path | None = None, create_if_missing: bool = False) -> "Channel":
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

    def context_path(self, agent: str) -> Path:
        return self.root / f"context.{agent}.md"

    def write_context(self, agent: str, content: str) -> Path:
        self.init()
        path = self.context_path(agent)
        path.write_text(content, encoding="utf-8")
        return path

    def read_context(self, agent: str) -> str:
        self.init()
        return self.context_path(agent).read_text(encoding="utf-8")

    def send(self, sender: str, recipient: str, text: str) -> Message:
        self.init()
        raw = f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] {sender} -> {recipient}: {text}"
        with self.lock_path.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            with self.log_path.open("a", encoding="utf-8") as log_handle:
                log_handle.write(raw + "\n")
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        return self.parse_line(raw)

    def read_lines(self) -> list[str]:
        self.init()
        return self.log_path.read_text(encoding="utf-8").splitlines()

    def history(self, limit: int = 0, agent: str | None = None) -> list[Message]:
        messages = []
        for line in self.read_lines():
            message = self.parse_line(line)
            if message is None:
                continue
            if agent and agent not in (message.sender, message.recipient):
                continue
            messages.append(message)
        return messages[-limit:] if limit else messages

    def status(self) -> dict[str, object]:
        messages = self.history()
        agents = sorted({side for message in messages for side in (message.sender, message.recipient)})
        return {
            "channel": str(self.root),
            "messages": len(messages),
            "agents": agents,
            "last": messages[-1].raw if messages else None,
            "has_inotify": HAS_INOTIFY,
        }

    def recv(self, recipient: str, timeout: float = 60, poll_interval: float = 0.5) -> Message | None:
        self.init()
        seen = len(self.read_lines())
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = max(poll_interval, min(5.0, deadline - time.time()))
            self.wait_for_change(remaining)
            lines = self.read_lines()
            for line in lines[seen:]:
                message = self.parse_line(line)
                if message and message.recipient == recipient:
                    return message
            seen = len(lines)
        return None

    def stream(self, agent: str | None = None, include_all: bool = False, poll_interval: float = 0.5) -> Iterator[Message]:
        self.init()
        seen = len(self.read_lines())
        while True:
            self.wait_for_change(10 if HAS_INOTIFY else poll_interval)
            lines = self.read_lines()
            for line in lines[seen:]:
                message = self.parse_line(line)
                if message is None:
                    continue
                if include_all or agent is None or agent in (message.sender, message.recipient):
                    yield message
            seen = len(lines)

    def wait_for_change(self, timeout_s: float) -> None:
        if HAS_INOTIFY:
            subprocess.run(
                ["inotifywait", "-qq", "-t", str(max(1, int(timeout_s))), "-e", "modify", str(self.log_path)],
                capture_output=True,
                check=False,
            )
            return
        time.sleep(timeout_s)

    @staticmethod
    def parse_line(line: str) -> Message | None:
        match = MESSAGE_RE.match(line.strip())
        if not match:
            return None
        return Message(
            ts=match.group("ts"),
            sender=match.group("sender"),
            recipient=match.group("recipient"),
            text=match.group("text"),
            raw=line.strip(),
        )
