#!/usr/bin/env python3
"""Guarda de fechamento para sessoes duo-link.

Bloqueia encerramento prematuro quando existe janela ativa e nao houve
consenso explicito bilateral.

Usage:
    python3 -m duo_link_cli.close_guard
    python3 -m duo_link_cli.close_guard --json
    python3 -m duo_link_cli.close_guard --chat <project>/duo-link/chat.log
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

WINDOW_PATTERNS = (
    re.compile(r"\bTW=(\d{1,2}:\d{2})-(\d{1,2}:\d{2})\b"),
    re.compile(r"\bjanela:\s*(\d{1,2}:\d{2})-(\d{1,2}:\d{2})\b", re.I),
    re.compile(r"\bjanela\s+(\d{1,2}:\d{2})-(\d{1,2}:\d{2})\b", re.I),
)
CONSENT_PATTERNS = (
    re.compile(r"\bE:(mutual-consent|user-release)\b"),
    re.compile(r"\bA:(accept-close|joint-close)\b"),
    re.compile(r"\bconsenso explicito\b", re.I),
    re.compile(r"\bsaida conjunta\b", re.I),
    re.compile(r"\bclose-ok\b", re.I),
)
USER_RELEASE_PATTERNS = (
    re.compile(r"\bencerrem\b", re.I),
    re.compile(r"\bpodem encerrar\b", re.I),
    re.compile(r"\bpodem sair\b", re.I),
    re.compile(r"\bliberados\b", re.I),
    re.compile(r"\bpode sair\b", re.I),
    re.compile(r"\bfechem\b", re.I),
)
AGENTS = {"codex", "claude-opus"}


@dataclass
class SessionWindow:
    opener_id: int | None
    opener_ts: datetime
    start: datetime
    end: datetime
    source_text: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="duo-link close guard")
    parser.add_argument(
        "--chat",
        default="duo-link/chat.log",
        help="Path do chat.log (default: ./duo-link/chat.log)",
    )
    parser.add_argument("--json", action="store_true", help="Output estruturado")
    return parser.parse_args()


def load_messages(path: Path) -> list[dict]:
    messages = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return messages


def extract_window(text: str) -> tuple[str, str] | None:
    for pattern in WINDOW_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1), match.group(2)
    return None


def parse_hhmm(value: str) -> tuple[int, int]:
    hour, minute = value.split(":")
    return int(hour), int(minute)


def resolve_window(opener_ts: datetime, start_hhmm: str, end_hhmm: str) -> SessionWindow:
    start_h, start_m = parse_hhmm(start_hhmm)
    end_h, end_m = parse_hhmm(end_hhmm)

    start = opener_ts.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    if start - opener_ts > timedelta(hours=6):
        start -= timedelta(days=1)

    end = start.replace(hour=end_h, minute=end_m)
    if end <= start:
        end += timedelta(days=1)

    return SessionWindow(
        opener_id=None,
        opener_ts=opener_ts,
        start=start,
        end=end,
        source_text=f"{start_hhmm}-{end_hhmm}",
    )


def find_active_window(messages: list[dict]) -> SessionWindow | None:
    for msg in reversed(messages):
        if msg.get("from") not in AGENTS:
            continue
        text = msg.get("text", "")
        found = extract_window(text)
        if not found:
            continue
        opener_ts = datetime.fromisoformat(msg["ts"])
        window = resolve_window(opener_ts, found[0], found[1])
        window.opener_id = msg.get("id")
        return window
    return None


def has_consent_marker(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in CONSENT_PATTERNS)


def has_user_release(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in USER_RELEASE_PATTERNS)


def evaluate(messages: list[dict], window: SessionWindow) -> dict:
    now = datetime.now(window.end.tzinfo)
    session_msgs = []
    consent_from = set()
    user_release = None

    for msg in messages:
        ts_raw = msg.get("ts")
        if not ts_raw:
            continue
        try:
            ts = datetime.fromisoformat(ts_raw)
        except ValueError:
            continue
        if ts < window.opener_ts:
            continue
        session_msgs.append(msg)
        sender = msg.get("from")
        if sender in AGENTS and has_consent_marker(msg.get("text", "")):
            consent_from.add(sender)
        elif sender not in AGENTS and has_user_release(msg.get("text", "")):
            user_release = msg

    if user_release is not None:
        return {
            "allowed": True,
            "reason": "user-release",
            "window_end": window.end.isoformat(),
            "window_source": window.source_text,
            "consent_from": sorted(consent_from),
            "remaining_seconds": max(0, int((window.end - now).total_seconds())),
        }

    if now >= window.end:
        return {
            "allowed": True,
            "reason": "window-elapsed",
            "window_end": window.end.isoformat(),
            "window_source": window.source_text,
            "consent_from": sorted(consent_from),
            "remaining_seconds": 0,
        }

    remaining = int((window.end - now).total_seconds())
    if consent_from == AGENTS:
        return {
            "allowed": True,
            "reason": "mutual-consent",
            "window_end": window.end.isoformat(),
            "window_source": window.source_text,
            "consent_from": sorted(consent_from),
            "remaining_seconds": remaining,
        }

    return {
        "allowed": False,
        "reason": "window-active-no-consent",
        "window_end": window.end.isoformat(),
        "window_source": window.source_text,
        "consent_from": sorted(consent_from),
        "remaining_seconds": remaining,
    }


def format_human(result: dict, chat_path: Path) -> str:
    lines = [
        f"chat: {chat_path}",
        f"window: {result['window_source']} (fim {result['window_end']})",
        f"reason: {result['reason']}",
        f"consent_from: {','.join(result['consent_from']) or 'none'}",
    ]
    if result["allowed"]:
        lines.append("close: ALLOWED")
    else:
        lines.append(
            f"close: BLOCKED (faltam {result['remaining_seconds']}s ou consenso bilateral)"
        )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    chat_path = Path(args.chat).resolve()
    if not chat_path.exists():
        payload = {
            "allowed": False,
            "reason": "chat-missing",
            "chat": str(chat_path),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"chat: {chat_path}\nclose: BLOCKED (chat.log ausente)")
        return 2

    messages = load_messages(chat_path)
    window = find_active_window(messages)
    if not window:
        payload = {
            "allowed": False,
            "reason": "window-not-found",
            "chat": str(chat_path),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(
                f"chat: {chat_path}\nclose: BLOCKED (nenhuma janela explicita encontrada no canal)"
            )
        return 2

    result = evaluate(messages, window)
    if args.json:
        result["chat"] = str(chat_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_human(result, chat_path))

    return 0 if result["allowed"] else 1


if __name__ == "__main__":
    sys.exit(main())
