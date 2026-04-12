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

    def test_send_supports_reply_to_and_history_exposes_it(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        self.run_cli(
            "send",
            "--as",
            "codex",
            "--channel",
            str(self.channel_dir),
            "claude",
            "mensagem raiz",
        )
        exit_code, stdout, stderr = self.run_cli(
            "send",
            "--as",
            "claude",
            "--channel",
            str(self.channel_dir),
            "--reply-to",
            "1",
            "codex",
            "resposta encadeada",
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("[sent]", stdout)

        exit_code, stdout, _ = self.run_cli(
            "history",
            "--as",
            "codex",
            "--channel",
            str(self.channel_dir),
            "--json",
        )
        self.assertEqual(exit_code, 0)
        payloads = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        self.assertEqual(payloads[0]["reply_to"], None)
        self.assertEqual(payloads[1]["reply_to"], 1)
        self.assertEqual(payloads[1]["msg"], "resposta encadeada")

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

    def test_drain_returns_all_pending_messages_in_order_as_json(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        self.run_cli(
            "send",
            "--as",
            "codex",
            "--channel",
            str(self.channel_dir),
            "claude",
            "pendente 1",
        )
        self.run_cli(
            "send",
            "--as",
            "codex",
            "--channel",
            str(self.channel_dir),
            "claude",
            "pendente 2",
        )

        exit_code, stdout, stderr = self.run_cli(
            "drain",
            "--as",
            "claude",
            "--channel",
            str(self.channel_dir),
            "--json",
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        payloads = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        self.assertEqual([item["msg"] for item in payloads], ["pendente 1", "pendente 2"])

    def test_drain_advances_cursor_and_clears_pending_view(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        self.run_cli(
            "send",
            "--as",
            "codex",
            "--channel",
            str(self.channel_dir),
            "claude",
            "pendente unica",
        )

        first_exit, first_stdout, _ = self.run_cli(
            "drain",
            "--as",
            "claude",
            "--channel",
            str(self.channel_dir),
            "--json",
        )
        second_exit, second_stdout, second_stderr = self.run_cli(
            "drain",
            "--as",
            "claude",
            "--channel",
            str(self.channel_dir),
            "--json",
        )
        self.assertEqual(first_exit, 0)
        self.assertIn("pendente unica", first_stdout)
        self.assertEqual(second_exit, 0)
        self.assertEqual(second_stdout, "")
        self.assertEqual(second_stderr, "")

        exit_code, stdout, stderr = self.run_cli(
            "pending",
            "--as",
            "claude",
            "--channel",
            str(self.channel_dir),
            "--json",
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")

    def test_named_sessions_isolate_history_and_status(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        self.run_cli(
            "send",
            "--as",
            "codex",
            "--channel",
            str(self.channel_dir),
            "--session",
            "alpha",
            "claude",
            "mensagem alpha",
        )
        self.run_cli(
            "send",
            "--as",
            "codex",
            "--channel",
            str(self.channel_dir),
            "--session",
            "beta",
            "claude",
            "mensagem beta",
        )

        exit_code, stdout, stderr = self.run_cli(
            "history",
            "--as",
            "codex",
            "--channel",
            str(self.channel_dir),
            "--session",
            "alpha",
            "--json",
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        payloads = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["msg"], "mensagem alpha")

        exit_code, stdout, stderr = self.run_cli(
            "status",
            "--channel",
            str(self.channel_dir),
            "--session",
            "alpha",
            "--json",
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["messages"], 1)
        self.assertIn("codex -> claude: mensagem alpha", payload["last"])

    def test_named_sessions_keep_recv_cursor_separate(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        self.run_cli(
            "send",
            "--as",
            "codex",
            "--channel",
            str(self.channel_dir),
            "--session",
            "alpha",
            "claude",
            "pendente alpha",
        )
        self.run_cli(
            "send",
            "--as",
            "codex",
            "--channel",
            str(self.channel_dir),
            "--session",
            "beta",
            "claude",
            "pendente beta",
        )

        beta_exit, beta_stdout, beta_stderr = self.run_cli(
            "recv",
            "--as",
            "claude",
            "--channel",
            str(self.channel_dir),
            "--session",
            "beta",
            "--timeout",
            "0.2",
            "--poll-interval",
            "0.05",
        )
        alpha_exit, alpha_stdout, alpha_stderr = self.run_cli(
            "recv",
            "--as",
            "claude",
            "--channel",
            str(self.channel_dir),
            "--session",
            "alpha",
            "--timeout",
            "0.2",
            "--poll-interval",
            "0.05",
        )
        self.assertEqual(beta_exit, 0)
        self.assertEqual(beta_stderr, "")
        self.assertIn("pendente beta", beta_stdout)
        self.assertEqual(alpha_exit, 0)
        self.assertEqual(alpha_stderr, "")
        self.assertIn("pendente alpha", alpha_stdout)

    def test_history_filters_by_sender(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        channel = Channel(self.channel_dir)
        channel.send("codex", "claude", "primeira")
        channel.send("claude", "codex", "segunda")
        channel.send("bot", "claude", "terceira")

        exit_code, stdout, stderr = self.run_cli(
            "history",
            "--channel",
            str(self.channel_dir),
            "--from",
            "claude",
            "--json",
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        payloads = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["from"], "claude")
        self.assertEqual(payloads[0]["msg"], "segunda")

    def test_history_filters_by_recipient(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        channel = Channel(self.channel_dir)
        channel.send("codex", "claude", "primeira")
        channel.send("claude", "codex", "segunda")
        channel.send("bot", "claude", "terceira")

        exit_code, stdout, stderr = self.run_cli(
            "history",
            "--channel",
            str(self.channel_dir),
            "--to",
            "claude",
            "--json",
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        payloads = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        self.assertEqual([item["msg"] for item in payloads], ["primeira", "terceira"])

    def test_history_filters_by_reply_to_with_session(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        channel = Channel(self.channel_dir)
        channel.send("codex", "claude", "raiz alpha", session="alpha")
        channel.send(
            "claude",
            "codex",
            "resposta alpha",
            reply_to=1,
            session="alpha",
        )
        channel.send("codex", "claude", "raiz beta", session="beta")
        channel.send(
            "claude",
            "codex",
            "resposta beta",
            reply_to=3,
            session="beta",
        )

        exit_code, stdout, stderr = self.run_cli(
            "history",
            "--channel",
            str(self.channel_dir),
            "--session",
            "alpha",
            "--reply-to",
            "1",
            "--json",
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        payloads = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["msg"], "resposta alpha")
        self.assertEqual(payloads[0]["reply_to"], 1)
        self.assertEqual(payloads[0]["session"], "alpha")

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

    def test_ack_marks_message_as_confirmed_in_history_json(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        self.run_cli(
            "send",
            "--as",
            "codex",
            "--channel",
            str(self.channel_dir),
            "claude",
            "precisa ack",
        )

        exit_code, stdout, stderr = self.run_cli(
            "ack",
            "--as",
            "claude",
            "--channel",
            str(self.channel_dir),
            "1",
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("ack", stdout.lower())

        exit_code, stdout, _ = self.run_cli(
            "history",
            "--as",
            "codex",
            "--channel",
            str(self.channel_dir),
            "--json",
        )
        self.assertEqual(exit_code, 0)
        payloads = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        self.assertEqual(payloads[0]["id"], 1)
        self.assertTrue(payloads[0]["acked"])

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

    def test_status_json_reports_pending_and_acked_counts(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        self.run_cli(
            "send",
            "--as",
            "codex",
            "--channel",
            str(self.channel_dir),
            "claude",
            "msg 1",
        )
        self.run_cli(
            "send",
            "--as",
            "codex",
            "--channel",
            str(self.channel_dir),
            "claude",
            "msg 2",
        )
        self.run_cli(
            "ack",
            "--as",
            "claude",
            "--channel",
            str(self.channel_dir),
            "1",
        )

        exit_code, stdout, _ = self.run_cli(
            "status",
            "--channel",
            str(self.channel_dir),
            "--json",
        )
        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["messages"], 2)
        self.assertEqual(payload["acked_messages"], 1)
        self.assertEqual(payload["pending_messages"], 1)

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

    def test_export_stdout_respects_session_filter(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        channel = Channel(self.channel_dir)
        channel.send("codex", "claude", "alpha 1", session="alpha")
        channel.send("codex", "claude", "beta 1", session="beta")

        exit_code, stdout, stderr = self.run_cli(
            "export",
            "--channel",
            str(self.channel_dir),
            "--session",
            "alpha",
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        payloads = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["msg"], "alpha 1")
        self.assertEqual(payloads[0]["session"], "alpha")

    def test_export_writes_jsonl_file(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        channel = Channel(self.channel_dir)
        channel.send("codex", "claude", "linha exportada")
        output = Path(self.temp_dir.name) / "export.jsonl"

        exit_code, stdout, stderr = self.run_cli(
            "export",
            "--channel",
            str(self.channel_dir),
            "--output",
            str(output),
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertIn(str(output), stdout)
        payloads = [
            json.loads(line)
            for line in output.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["msg"], "linha exportada")

    def test_stats_json_reports_per_agent_and_acked_counts(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        channel = Channel(self.channel_dir)
        channel.send("codex", "claude", "m1")
        channel.send("claude", "codex", "m2")
        channel.send("bot", "claude", "m3")

        ack_exit, _, ack_stderr = self.run_cli(
            "ack",
            "--as",
            "claude",
            "--channel",
            str(self.channel_dir),
            "1",
        )
        self.assertEqual(ack_exit, 0)
        self.assertEqual(ack_stderr, "")

        exit_code, stdout, stderr = self.run_cli(
            "stats",
            "--channel",
            str(self.channel_dir),
            "--json",
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["total_messages"], 3)
        self.assertEqual(payload["total_acked"], 1)
        self.assertEqual(payload["agents"]["codex"]["sent"], 1)
        self.assertEqual(payload["agents"]["codex"]["received"], 1)
        self.assertEqual(payload["agents"]["claude"]["sent"], 1)
        self.assertEqual(payload["agents"]["claude"]["received"], 2)
        self.assertEqual(payload["agents"]["bot"]["sent"], 1)
        self.assertEqual(payload["agents"]["bot"]["received"], 0)

    def test_import_restores_history_from_exported_jsonl(self) -> None:
        source_dir = Path(self.temp_dir.name) / "source"
        dest_dir = Path(self.temp_dir.name) / "dest"
        export_file = Path(self.temp_dir.name) / "roundtrip.jsonl"

        self.run_cli("init", str(source_dir))
        source = Channel(source_dir)
        source.send("codex", "claude", "raiz", session="alpha")
        source.send(
            "claude",
            "codex",
            "resposta",
            reply_to=1,
            session="alpha",
        )

        export_exit, _, export_stderr = self.run_cli(
            "export",
            "--channel",
            str(source_dir),
            "--output",
            str(export_file),
        )
        self.assertEqual(export_exit, 0)
        self.assertEqual(export_stderr, "")

        self.run_cli("init", str(dest_dir))
        import_exit, import_stdout, import_stderr = self.run_cli(
            "import",
            "--channel",
            str(dest_dir),
            "--input",
            str(export_file),
        )
        self.assertEqual(import_exit, 0)
        self.assertEqual(import_stderr, "")
        self.assertIn("import", import_stdout.lower())

        history_exit, history_stdout, history_stderr = self.run_cli(
            "history",
            "--channel",
            str(dest_dir),
            "--json",
        )
        self.assertEqual(history_exit, 0)
        self.assertEqual(history_stderr, "")
        payloads = [json.loads(line) for line in history_stdout.splitlines() if line.strip()]
        self.assertEqual([item["msg"] for item in payloads], ["raiz", "resposta"])
        self.assertEqual(payloads[0]["session"], "alpha")
        self.assertEqual(payloads[1]["reply_to"], 1)

    def test_import_updates_status_after_roundtrip(self) -> None:
        source_dir = Path(self.temp_dir.name) / "source-status"
        dest_dir = Path(self.temp_dir.name) / "dest-status"
        export_file = Path(self.temp_dir.name) / "status-roundtrip.jsonl"

        self.run_cli("init", str(source_dir))
        source = Channel(source_dir)
        source.send("codex", "claude", "m1")
        source.send("claude", "codex", "m2")

        export_exit, _, export_stderr = self.run_cli(
            "export",
            "--channel",
            str(source_dir),
            "--output",
            str(export_file),
        )
        self.assertEqual(export_exit, 0)
        self.assertEqual(export_stderr, "")

        self.run_cli("init", str(dest_dir))
        import_exit, _, import_stderr = self.run_cli(
            "import",
            "--channel",
            str(dest_dir),
            "--input",
            str(export_file),
        )
        self.assertEqual(import_exit, 0)
        self.assertEqual(import_stderr, "")

        status_exit, status_stdout, status_stderr = self.run_cli(
            "status",
            "--channel",
            str(dest_dir),
            "--json",
        )
        self.assertEqual(status_exit, 0)
        self.assertEqual(status_stderr, "")
        payload = json.loads(status_stdout)
        self.assertEqual(payload["messages"], 2)
        self.assertEqual(payload["agents"], ["claude", "codex"])

    def test_purge_keeps_only_last_n_messages(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        channel = Channel(self.channel_dir)
        channel.send("codex", "claude", "m1")
        channel.send("codex", "claude", "m2")
        channel.send("codex", "claude", "m3")

        purge_exit, purge_stdout, purge_stderr = self.run_cli(
            "purge",
            "--channel",
            str(self.channel_dir),
            "--keep",
            "2",
        )
        self.assertEqual(purge_exit, 0)
        self.assertEqual(purge_stderr, "")
        self.assertIn("2 kept", purge_stdout)

        history_exit, history_stdout, history_stderr = self.run_cli(
            "history",
            "--channel",
            str(self.channel_dir),
            "--json",
        )
        self.assertEqual(history_exit, 0)
        self.assertEqual(history_stderr, "")
        payloads = [json.loads(line) for line in history_stdout.splitlines() if line.strip()]
        self.assertEqual([item["msg"] for item in payloads], ["m2", "m3"])
        self.assertEqual([item["id"] for item in payloads], [1, 2])

    def test_purge_clears_acked_state_after_renumbering(self) -> None:
        self.run_cli("init", str(self.channel_dir))
        channel = Channel(self.channel_dir)
        channel.send("codex", "claude", "m1")
        channel.send("codex", "claude", "m2")
        channel.send("codex", "claude", "m3")

        ack_exit, _, ack_stderr = self.run_cli(
            "ack",
            "--as",
            "claude",
            "--channel",
            str(self.channel_dir),
            "2",
        )
        self.assertEqual(ack_exit, 0)
        self.assertEqual(ack_stderr, "")

        purge_exit, _, purge_stderr = self.run_cli(
            "purge",
            "--channel",
            str(self.channel_dir),
            "--keep",
            "1",
        )
        self.assertEqual(purge_exit, 0)
        self.assertEqual(purge_stderr, "")

        status_exit, status_stdout, status_stderr = self.run_cli(
            "status",
            "--channel",
            str(self.channel_dir),
            "--json",
        )
        self.assertEqual(status_exit, 0)
        self.assertEqual(status_stderr, "")
        payload = json.loads(status_stdout)
        self.assertEqual(payload["messages"], 1)
        self.assertEqual(payload["acked_messages"], 0)

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
