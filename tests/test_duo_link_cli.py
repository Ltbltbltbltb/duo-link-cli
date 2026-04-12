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
from duo_link_cli.cli import is_pair_message, is_repl_incoming, main


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

    def test_init_json_reports_channel_path(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "init",
            "--json",
            str(self.channel_dir),
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["channel"], str(self.channel_dir))
        self.assertTrue(payload["initialized"])

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

    def test_send_persists_jsonl_record_with_message_id(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        self.run_cli(
            "send",
            "--as",
            "codex",
            "--channel",
            str(self.channel_dir),
            "claude",
            "ola",
            "jsonl",
        )

        raw_line = (self.channel_dir / "chat.log").read_text(encoding="utf-8").strip()
        payload = json.loads(raw_line)

        self.assertIn("id", payload)
        self.assertEqual(payload["from"], "codex")
        self.assertEqual(payload["to"], "claude")
        self.assertEqual(payload["text"], "ola jsonl")

    def test_send_and_history_preserve_multiline_message(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        message = "linha 1\nlinha 2"
        exit_code, _, _ = self.run_cli(
            "send",
            "--as",
            "codex",
            "--channel",
            str(self.channel_dir),
            "claude",
            message,
        )
        self.assertEqual(exit_code, 0)

        raw_line = (self.channel_dir / "chat.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(raw_line), 1)
        payload = json.loads(raw_line[0])
        self.assertEqual(payload["text"], message)

        history = Channel(self.channel_dir).history()
        self.assertEqual(history[0].text, message)

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

    def test_recv_consumes_pending_backlog_for_agent(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        channel = Channel(self.channel_dir)
        channel.send("codex", "claude", "mensagem pendente")

        exit_code, stdout, stderr = self.run_cli(
            "recv",
            "--as",
            "claude",
            "--channel",
            str(self.channel_dir),
            "--timeout",
            "0.2",
            "--poll-interval",
            "0.05",
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("codex -> claude: mensagem pendente", stdout)
        self.assertEqual(stderr, "")

    def test_recv_persists_progress_per_agent_across_calls(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        channel = Channel(self.channel_dir)
        channel.send("codex", "claude", "primeira pendente")
        channel.send("codex", "claude", "segunda pendente")

        first_exit, first_stdout, first_stderr = self.run_cli(
            "recv",
            "--as",
            "claude",
            "--channel",
            str(self.channel_dir),
            "--timeout",
            "0.2",
            "--poll-interval",
            "0.05",
        )
        second_exit, second_stdout, second_stderr = self.run_cli(
            "recv",
            "--as",
            "claude",
            "--channel",
            str(self.channel_dir),
            "--timeout",
            "0.2",
            "--poll-interval",
            "0.05",
        )

        self.assertEqual(first_exit, 0)
        self.assertIn("codex -> claude: primeira pendente", first_stdout)
        self.assertEqual(first_stderr, "")

        self.assertEqual(second_exit, 0)
        self.assertIn("codex -> claude: segunda pendente", second_stdout)
        self.assertEqual(second_stderr, "")

    def test_recv_handles_two_messages_sent_in_same_burst(self) -> None:
        self.run_cli("init", str(self.channel_dir))

        def delayed_burst() -> None:
            time.sleep(0.2)
            channel = Channel(self.channel_dir)
            channel.send("codex", "claude", "burst 1")
            channel.send("codex", "claude", "burst 2")

        thread = threading.Thread(target=delayed_burst)
        thread.start()

        first_exit, first_stdout, first_stderr = self.run_cli(
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
        second_exit, second_stdout, second_stderr = self.run_cli(
            "recv",
            "--as",
            "claude",
            "--channel",
            str(self.channel_dir),
            "--timeout",
            "0.2",
            "--poll-interval",
            "0.05",
        )
        thread.join(timeout=1)

        self.assertEqual(first_exit, 0)
        self.assertIn("codex -> claude: burst 1", first_stdout)
        self.assertEqual(first_stderr, "")

        self.assertEqual(second_exit, 0)
        self.assertIn("codex -> claude: burst 2", second_stdout)
        self.assertEqual(second_stderr, "")

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

    def test_history_rejects_negative_limit(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        exit_code, stdout, stderr = self.run_cli(
            "history",
            "--channel",
            str(self.channel_dir),
            "-n",
            "-1",
        )
        self.assertNotEqual(exit_code, 0)
        self.assertEqual(stdout, "")
        self.assertIn("limit", stderr.lower())

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

    def test_status_does_not_create_channel_when_missing(self) -> None:
        missing_channel = self.channel_dir
        exit_code, stdout, stderr = self.run_cli(
            "status",
            "--channel",
            str(missing_channel),
        )
        self.assertNotEqual(exit_code, 0)
        self.assertEqual(stdout, "")
        self.assertIn("channel", stderr.lower())
        self.assertFalse(missing_channel.exists())

    def test_recv_rejects_non_positive_timeout(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        exit_code, stdout, stderr = self.run_cli(
            "recv",
            "--as",
            "claude",
            "--channel",
            str(self.channel_dir),
            "--timeout",
            "0",
        )
        self.assertNotEqual(exit_code, 0)
        self.assertEqual(stdout, "")
        self.assertIn("timeout", stderr.lower())

    def test_recv_rejects_non_positive_poll_interval(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        exit_code, stdout, stderr = self.run_cli(
            "recv",
            "--as",
            "claude",
            "--channel",
            str(self.channel_dir),
            "--poll-interval",
            "0",
        )
        self.assertNotEqual(exit_code, 0)
        self.assertEqual(stdout, "")
        self.assertIn("poll", stderr.lower())

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

    def test_context_set_and_show_support_json_output(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        exit_code, stdout, stderr = self.run_cli(
            "context",
            "--channel",
            str(self.channel_dir),
            "--json",
            "set",
            "codex",
            "--text",
            "contexto json",
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["agent"], "codex")
        self.assertEqual(payload["path"], str(self.channel_dir / "context.codex.md"))

        exit_code, stdout, stderr = self.run_cli(
            "context",
            "--channel",
            str(self.channel_dir),
            "--json",
            "show",
            "codex",
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["agent"], "codex")
        self.assertEqual(payload["content"], "contexto json")

    def test_context_show_does_not_create_channel_when_missing(self) -> None:
        missing_channel = self.channel_dir
        exit_code, stdout, stderr = self.run_cli(
            "context",
            "--channel",
            str(missing_channel),
            "show",
            "codex",
        )
        self.assertNotEqual(exit_code, 0)
        self.assertEqual(stdout, "")
        self.assertIn("context", stderr.lower())
        self.assertFalse(missing_channel.exists())

    def test_repl_scope_helpers_filter_only_the_selected_peer(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        channel = Channel(self.channel_dir)
        channel.send("codex", "claude", "ida valida")
        channel.send("claude", "codex", "volta valida")
        channel.send("bot", "codex", "intrusa")
        channel.send("codex", "bot", "outra intrusa")

        history = channel.history()
        pair_messages = [message.text for message in history if is_pair_message(message, "codex", "claude")]
        incoming_messages = [message.text for message in history if is_repl_incoming(message, "codex", "claude")]

        self.assertEqual(pair_messages, ["ida valida", "volta valida"])
        self.assertEqual(incoming_messages, ["volta valida"])

    def test_init_rejects_dir_and_channel_together(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "init",
            str(self.channel_dir),
            "--channel",
            str(self.channel_dir / "outro"),
        )
        self.assertNotEqual(exit_code, 0)
        self.assertEqual(stdout, "")
        self.assertIn("channel", stderr.lower())


if __name__ == "__main__":
    unittest.main()
