from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

from .channel import Channel, DEFAULT_CONTEXT_AGENTS

VERSION = "0.2.0"


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


def build_parser() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--as", dest="as_id", metavar="ID", help="identidade do agente local"
    )
    shared.add_argument("--channel", metavar="DIR", help="diretorio do canal")
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

    sub.add_parser("status", help="resume o canal", parents=[shared])

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
    if args.dir:
        channel = Channel(Path(args.dir).expanduser().resolve())
    else:
        channel = resolve_channel(args.channel, create_if_missing=True)
    channel.init(tuple(args.agents))
    print(channel.root)
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    channel = resolve_channel(args.channel)
    sender = resolve_identity(args.as_id)
    message = channel.send(sender, args.to, " ".join(args.msg))
    if args.json:
        print(json.dumps({"status": "sent", **message.as_dict()}, ensure_ascii=False))
    else:
        print(f"[sent] {message.raw}")
    return 0


def cmd_recv(args: argparse.Namespace) -> int:
    channel = resolve_channel(args.channel)
    recipient = resolve_identity(args.as_id)
    message = channel.recv(
        recipient, timeout=args.timeout, poll_interval=args.poll_interval
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
    channel = resolve_channel(args.channel)
    agent = resolve_identity(args.as_id) if args.as_id else None
    for message in channel.history(limit=args.n, agent=agent):
        if args.json:
            print(json.dumps(message.as_dict(), ensure_ascii=False))
        else:
            print(message.raw)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    channel = resolve_channel(args.channel)
    status = channel.status()
    if args.json:
        print(json.dumps(status, ensure_ascii=False))
        return 0
    print(f"channel:  {status['channel']}")
    print(f"messages: {status['messages']}")
    print(f"agents:   {', '.join(status['agents']) if status['agents'] else '-'}")
    print(f"notify:   {'inotifywait' if status['has_inotify'] else 'poll'}")
    print(f"last:     {status['last'] if status['last'] else '-'}")
    return 0


def cmd_context_show(args: argparse.Namespace) -> int:
    channel = resolve_channel(args.channel)
    print(channel.read_context(args.agent), end="")
    return 0


def cmd_context_set(args: argparse.Namespace) -> int:
    channel = resolve_channel(args.channel)
    if args.text is not None:
        content = args.text
    else:
        content = args.file.read_text(encoding="utf-8")
    path = channel.write_context(args.agent, content)
    print(path)
    return 0


def cmd_repl(args: argparse.Namespace) -> int:
    channel = resolve_channel(args.channel)
    sender = resolve_identity(args.as_id)
    stop_event = threading.Event()

    history = channel.history(limit=args.history, agent=sender)
    for message in history:
        print(message.raw)
    print(
        f"REPL ativo para {sender} <-> {args.to}. Use /quit para sair.", file=sys.stderr
    )

    def receive_loop() -> None:
        while not stop_event.is_set():
            message = channel.recv(
                sender, timeout=args.poll_interval, poll_interval=args.poll_interval
            )
            if message is None:
                continue
            print(f"\n{message.raw}")
            print("> ", end="", flush=True)

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
