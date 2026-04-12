from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from duo_link_cli.channel import Channel
from duo_link_cli.cli import main


class DuoLinkCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.channel_dir = Path(self.temp_dir.name) / "duo-link"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                exit_code = main(list(args))
            except SystemExit as exc:
                exit_code = exc.code if isinstance(exc.code, int) else 1
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_init_creates_expected_files(self) -> None:
        exit_code, _, _ = self.run_cli("init", str(self.channel_dir))
        self.assertEqual(exit_code, 0)
        self.assertTrue((self.channel_dir / "chat.log").exists())
        self.assertTrue((self.channel_dir / ".chat.lock").exists())
        self.assertTrue((self.channel_dir / "context.codex.md").exists())
        self.assertTrue((self.channel_dir / "context.claude.md").exists())

    def test_send_updates_history_and_status(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        exit_code, stdout, _ = self.run_cli(
            "send",
            "--as",
            "codex",
            "--channel",
            str(self.channel_dir),
            "claude",
            "ola",
            "cli",
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("[sent]", stdout)

        channel = Channel(self.channel_dir)
        history = channel.history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].sender, "codex")
        self.assertEqual(history[0].recipient, "claude")
        self.assertEqual(history[0].text, "ola cli")

        status = channel.status()
        self.assertEqual(status["messages"], 1)
        self.assertEqual(status["agents"], ["claude", "codex"])

    def test_recv_returns_message_sent_later(self) -> None:
        self.run_cli("init", str(self.channel_dir))

        def delayed_send() -> None:
            time.sleep(0.2)
            Channel(self.channel_dir).send("codex", "claude", "ping atrasado")

        thread = threading.Thread(target=delayed_send)
        thread.start()
        exit_code, stdout, stderr = self.run_cli(
            "recv",
            "--as",
            "claude",
            "--channel",
            str(self.channel_dir),
            "--timeout",
            "2",
            "--poll-interval",
            "0.1",
        )
        thread.join(timeout=1)
        self.assertEqual(exit_code, 0)
        self.assertIn("codex -> claude: ping atrasado", stdout)
        self.assertEqual(stderr, "")

    def test_history_json_filters_messages_for_agent(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        channel = Channel(self.channel_dir)
        channel.send("codex", "claude", "primeira")
        channel.send("claude", "codex", "segunda")
        channel.send("bot", "claude", "terceira")

        exit_code, stdout, _ = self.run_cli(
            "history",
            "--as",
            "codex",
            "--channel",
            str(self.channel_dir),
            "--json",
        )
        self.assertEqual(exit_code, 0)

        lines = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["msg"], "primeira")
        self.assertEqual(lines[1]["msg"], "segunda")

    def test_status_json_reports_last_message(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        Channel(self.channel_dir).send("codex", "claude", "ultima")

        exit_code, stdout, _ = self.run_cli(
            "status",
            "--channel",
            str(self.channel_dir),
            "--json",
        )
        self.assertEqual(exit_code, 0)

        payload = json.loads(stdout)
        self.assertEqual(payload["messages"], 1)
        self.assertEqual(payload["agents"], ["claude", "codex"])
        self.assertIn("codex -> claude: ultima", payload["last"])

    def test_context_set_and_show_roundtrip(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        exit_code, stdout, _ = self.run_cli(
            "context",
            "--channel",
            str(self.channel_dir),
            "set",
            "codex",
            "--text",
            "contexto de teste",
        )
        self.assertEqual(exit_code, 0)
        self.assertIn(str(self.channel_dir / "context.codex.md"), stdout)

        exit_code, stdout, _ = self.run_cli(
            "context",
            "--channel",
            str(self.channel_dir),
            "show",
            "codex",
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout, "contexto de teste")


if __name__ == "__main__":
    unittest.main()
