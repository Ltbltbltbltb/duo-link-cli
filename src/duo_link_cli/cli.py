from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

from .channel import Channel, DEFAULT_CONTEXT_AGENTS

VERSION = "0.4.0"


def resolve_channel(explicit: str | None, create_if_missing: bool = False) -> Channel:
    try:
        return Channel.resolve(
            explicit=explicit, cwd=Path.cwd(), create_if_missing=create_if_missing
        )
    except FileNotFoundError as exc:
        raise SystemExit(f"error: {exc}") from exc


def resolve_identity(explicit: str | None) -> str:
    if explicit:
        return explicit
    for env_name in ("DUO_ID", "DUO_AGENT"):
        value = __import__("os").environ.get(env_name)
        if value:
            return value
    raise SystemExit("error: no identity. Use --as or set $DUO_ID/$DUO_AGENT")


def is_pair_message(message, self_id: str, peer_id: str) -> bool:
    return (message.sender == self_id and message.recipient == peer_id) or (
        message.sender == peer_id and message.recipient == self_id
    )


def is_repl_incoming(message, self_id: str, peer_id: str) -> bool:
    return message.sender == peer_id and message.recipient == self_id


def build_parser() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--as", dest="as_id", metavar="ID", help="identidade do agente local"
    )
    shared.add_argument("--channel", metavar="DIR", help="diretorio do canal")
    shared.add_argument(
        "--session", metavar="NAME", default=None, help="sessao nomeada"
    )
    shared.add_argument("--json", action="store_true", help="saida em JSON")

    parser = argparse.ArgumentParser(
        prog="duo-link",
        description="Ferramenta CLI para comunicacao local entre agentes em um diretorio compartilhado.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"duo-link {VERSION}")
    sub = parser.add_subparsers(dest="cmd")

    init_parser = sub.add_parser(
        "init", help="cria ou inicializa um canal", parents=[shared]
    )
    init_parser.add_argument(
        "dir", nargs="?", help="diretorio do canal (default: ./duo-link)"
    )
    init_parser.add_argument(
        "--agents", nargs="+", default=list(DEFAULT_CONTEXT_AGENTS)
    )

    send_parser = sub.add_parser("send", help="envia uma mensagem", parents=[shared])
    send_parser.add_argument("to", help="destinatario")
    send_parser.add_argument("msg", nargs="+", help="texto da mensagem")
    send_parser.add_argument(
        "--reply-to", type=int, default=None, help="ID da mensagem sendo respondida"
    )

    recv_parser = sub.add_parser(
        "recv", help="aguarda mensagem para voce", parents=[shared]
    )
    recv_parser.add_argument(
        "--timeout", type=float, default=60, help="timeout em segundos"
    )
    recv_parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.5,
        help="intervalo de polling quando necessario",
    )

    watch_parser = sub.add_parser(
        "watch", help="acompanha mensagens novas", parents=[shared]
    )
    watch_parser.add_argument(
        "--all", action="store_true", help="mostra todas as mensagens"
    )

    history_parser = sub.add_parser(
        "history", help="mostra historico", parents=[shared]
    )
    history_parser.add_argument(
        "-n", type=int, default=0, help="ultimas N mensagens (0=todas)"
    )
    history_parser.add_argument(
        "--from", dest="from_filter", default=None, help="filtrar por remetente"
    )
    history_parser.add_argument(
        "--to", dest="to_filter", default=None, help="filtrar por destinatario"
    )
    history_parser.add_argument(
        "--reply-to",
        dest="reply_to_filter",
        type=int,
        default=None,
        help="filtrar por reply_to ID",
    )

    sub.add_parser("status", help="resume o canal", parents=[shared])
    sub.add_parser("rotate", help="arquiva chat.log e inicia novo", parents=[shared])

    ack_parser = sub.add_parser(
        "ack", help="confirma recebimento de mensagem", parents=[shared]
    )
    ack_parser.add_argument("msg_id", type=int, help="ID da mensagem a confirmar")

    sub.add_parser(
        "drain", help="consome todas as mensagens pendentes", parents=[shared]
    )
    sub.add_parser(
        "pending", help="lista mensagens pendentes sem consumir", parents=[shared]
    )

    export_parser = sub.add_parser(
        "export", help="exporta historico como JSONL", parents=[shared]
    )
    export_parser.add_argument(
        "-o", "--output", type=Path, help="arquivo de saida (default: stdout)"
    )

    sub.add_parser("stats", help="estatisticas por agente", parents=[shared])

    import_parser = sub.add_parser(
        "import", help="importa historico JSONL", parents=[shared]
    )
    import_source = import_parser.add_mutually_exclusive_group(required=True)
    import_source.add_argument(
        "-i", "--input", type=Path, help="arquivo JSONL de entrada"
    )
    import_source.add_argument("--stdin", action="store_true", help="ler de stdin")

    purge_parser = sub.add_parser(
        "purge", help="remove mensagens antigas", parents=[shared]
    )
    purge_parser.add_argument(
        "--keep", type=int, required=True, help="manter as ultimas N mensagens"
    )

    repl_parser = sub.add_parser(
        "repl", help="abre um chat interativo simples", parents=[shared]
    )
    repl_parser.add_argument("to", help="destinatario")
    repl_parser.add_argument(
        "--history",
        type=int,
        default=10,
        help="mensagens anteriores para exibir ao abrir",
    )
    repl_parser.add_argument("--poll-interval", type=float, default=0.5)

    context_parser = sub.add_parser(
        "context", help="le ou escreve contexto", parents=[shared]
    )
    context_sub = context_parser.add_subparsers(dest="context_cmd")

    context_show = context_sub.add_parser("show", help="exibe o contexto de um agente")
    context_show.add_argument("agent")

    context_set = context_sub.add_parser("set", help="salva o contexto de um agente")
    context_set.add_argument("agent")
    source = context_set.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--file", type=Path)

    return parser


def cmd_init(args: argparse.Namespace) -> int:
    if args.dir and args.channel:
        print("error: use 'dir' or '--channel', not both", file=sys.stderr)
        return 2
    if args.dir:
        channel = Channel(Path(args.dir).expanduser().resolve())
    else:
        channel = resolve_channel(args.channel, create_if_missing=True)
    channel.init(tuple(args.agents))
    if args.json:
        print(
            json.dumps(
                {"channel": str(channel.root), "initialized": True},
                ensure_ascii=False,
            )
        )
    else:
        print(channel.root)
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    channel = resolve_channel(args.channel)
    sender = resolve_identity(args.as_id)
    message = channel.send(
        sender,
        args.to,
        " ".join(args.msg),
        reply_to=args.reply_to,
        session=args.session,
    )
    if args.json:
        print(json.dumps({"status": "sent", **message.as_dict()}, ensure_ascii=False))
    else:
        print(f"[sent] {message.raw}")
    return 0


def cmd_recv(args: argparse.Namespace) -> int:
    if args.timeout <= 0:
        print("error: --timeout must be positive", file=sys.stderr)
        return 2
    if args.poll_interval <= 0:
        print("error: --poll-interval must be positive", file=sys.stderr)
        return 2
    channel = resolve_channel(args.channel)
    recipient = resolve_identity(args.as_id)
    message = channel.recv(
        recipient,
        timeout=args.timeout,
        poll_interval=args.poll_interval,
        session=args.session,
    )
    if message is None:
        print(
            f"[timeout] no message for {recipient} in {args.timeout}s", file=sys.stderr
        )
        return 1
    if args.json:
        print(json.dumps(message.as_dict(), ensure_ascii=False))
    else:
        print(message.raw)
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    channel = resolve_channel(args.channel)
    agent = None if args.all else resolve_identity(args.as_id)
    if not args.json:
        print(f"[watching] {'all' if args.all else agent}  (ctrl+c to stop)")
    try:
        for message in channel.stream(agent=agent, include_all=args.all):
            if args.json:
                print(json.dumps(message.as_dict(), ensure_ascii=False), flush=True)
            else:
                print(message.raw, flush=True)
    except KeyboardInterrupt:
        if not args.json:
            print("\n[stopped]")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    if args.n < 0:
        print("error: -n limit must be >= 0", file=sys.stderr)
        return 2
    channel = resolve_channel(args.channel)
    agent = resolve_identity(args.as_id) if args.as_id else None
    for message in channel.history(
        limit=args.n,
        agent=agent,
        session=args.session,
        sender=args.from_filter,
        recipient=args.to_filter,
        reply_to=args.reply_to_filter,
    ):
        if args.json:
            print(json.dumps(message.as_dict(), ensure_ascii=False))
        else:
            print(message.raw)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    channel = resolve_channel(args.channel)
    status = channel.status(session=args.session)
    if args.json:
        print(json.dumps(status, ensure_ascii=False))
        return 0
    print(f"channel:  {status['channel']}")
    print(f"messages: {status['messages']}")
    print(f"agents:   {', '.join(status['agents']) if status['agents'] else '-'}")
    print(f"notify:   {'inotifywait' if status['has_inotify'] else 'poll'}")
    print(f"last:     {status['last'] if status['last'] else '-'}")
    return 0


def cmd_rotate(args: argparse.Namespace) -> int:
    channel = resolve_channel(args.channel)
    archive = channel.rotate()
    if args.json:
        print(
            json.dumps({"archived": str(archive), "rotated": True}, ensure_ascii=False)
        )
    else:
        print(f"[rotated] archived to {archive}")
    return 0


def cmd_ack(args: argparse.Namespace) -> int:
    channel = resolve_channel(args.channel)
    agent = resolve_identity(args.as_id)
    channel.ack(args.msg_id, agent)
    if args.json:
        print(
            json.dumps(
                {"acked": True, "msg_id": args.msg_id, "by": agent},
                ensure_ascii=False,
            )
        )
    else:
        print(f"[ack] message {args.msg_id} acknowledged by {agent}")
    return 0


def cmd_drain(args: argparse.Namespace) -> int:
    channel = resolve_channel(args.channel)
    agent = resolve_identity(args.as_id)
    messages = channel.drain(agent)
    if args.json:
        for m in messages:
            print(json.dumps(m.as_dict(), ensure_ascii=False))
    else:
        if not messages:
            print(f"[drain] no pending messages for {agent}")
        for m in messages:
            print(m.raw)
    return 0


def cmd_pending(args: argparse.Namespace) -> int:
    channel = resolve_channel(args.channel)
    agent = resolve_identity(args.as_id)
    messages = channel.pending(agent)
    if args.json:
        for m in messages:
            print(json.dumps(m.as_dict(), ensure_ascii=False))
    else:
        if not messages:
            print(f"[pending] no pending messages for {agent}")
        for m in messages:
            print(m.raw)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    channel = resolve_channel(args.channel)
    data = channel.export_jsonl(session=args.session)
    if args.output:
        args.output.write_text(data, encoding="utf-8")
        print(f"[export] {args.output}")
    else:
        print(data, end="")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    channel = resolve_channel(args.channel)
    data = channel.stats()
    if args.json:
        print(json.dumps(data, ensure_ascii=False))
    else:
        print(f"total:  {data['total_messages']} messages, {data['total_acked']} acked")
        for agent, counts in data["agents"].items():
            print(f"  {agent}: {counts['sent']} sent, {counts['received']} received")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    channel = resolve_channel(args.channel, create_if_missing=True)
    if args.input:
        data = args.input.read_text(encoding="utf-8")
    else:
        data = sys.stdin.read()
    count = channel.import_jsonl(data)
    if args.json:
        print(json.dumps({"imported": count}, ensure_ascii=False))
    else:
        print(f"[import] {count} messages imported")
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    if args.keep < 0:
        print("error: --keep must be >= 0", file=sys.stderr)
        return 2
    channel = resolve_channel(args.channel)
    purged = channel.purge(keep=args.keep)
    if args.json:
        print(json.dumps({"purged": purged, "kept": args.keep}, ensure_ascii=False))
    else:
        print(f"[purge] {purged} messages removed, {args.keep} kept")
    return 0


def cmd_context_show(args: argparse.Namespace) -> int:
    channel = resolve_channel(args.channel)
    content = channel.read_context(args.agent)
    if args.json:
        print(
            json.dumps(
                {"agent": args.agent, "content": content},
                ensure_ascii=False,
            )
        )
    else:
        print(content, end="")
    return 0


def cmd_context_set(args: argparse.Namespace) -> int:
    channel = resolve_channel(args.channel)
    if args.text is not None:
        content = args.text
    else:
        content = args.file.read_text(encoding="utf-8")
    path = channel.write_context(args.agent, content)
    if args.json:
        print(
            json.dumps(
                {"agent": args.agent, "path": str(path), "updated": True},
                ensure_ascii=False,
            )
        )
    else:
        print(path)
    return 0


def cmd_repl(args: argparse.Namespace) -> int:
    channel = resolve_channel(args.channel)
    sender = resolve_identity(args.as_id)
    stop_event = threading.Event()

    history = [
        message
        for message in channel.history()
        if is_pair_message(message, sender, args.to)
    ]
    for message in history[-args.history :]:
        print(message.raw)
    print(
        f"REPL ativo para {sender} <-> {args.to}. Use /quit para sair.", file=sys.stderr
    )

    def receive_loop() -> None:
        try:
            for message in channel.stream(
                agent=sender, poll_interval=args.poll_interval
            ):
                if stop_event.is_set():
                    break
                if not is_repl_incoming(message, sender, args.to):
                    continue
                print(f"\n{message.raw}")
                print("> ", end="", flush=True)
        except FileNotFoundError:
            return

    watcher = threading.Thread(target=receive_loop, daemon=True)
    watcher.start()

    try:
        while True:
            line = input("> ").strip()
            if not line:
                continue
            if line == "/quit":
                break
            channel.send(sender, args.to, line)
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        stop_event.set()
        watcher.join(timeout=1)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.cmd:
        parser.print_help()
        return 1

    try:
        if args.cmd == "init":
            return cmd_init(args)
        if args.cmd == "send":
            return cmd_send(args)
        if args.cmd == "recv":
            return cmd_recv(args)
        if args.cmd == "watch":
            return cmd_watch(args)
        if args.cmd == "history":
            return cmd_history(args)
        if args.cmd == "status":
            return cmd_status(args)
        if args.cmd == "rotate":
            return cmd_rotate(args)
        if args.cmd == "ack":
            return cmd_ack(args)
        if args.cmd == "drain":
            return cmd_drain(args)
        if args.cmd == "pending":
            return cmd_pending(args)
        if args.cmd == "export":
            return cmd_export(args)
        if args.cmd == "stats":
            return cmd_stats(args)
        if args.cmd == "import":
            return cmd_import(args)
        if args.cmd == "purge":
            return cmd_purge(args)
        if args.cmd == "repl":
            return cmd_repl(args)
        if args.cmd == "context":
            if args.context_cmd == "show":
                return cmd_context_show(args)
            if args.context_cmd == "set":
                return cmd_context_set(args)
            parser.error("context exige show ou set")
        parser.error(f"comando invalido: {args.cmd}")
        return 2
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
