#!/usr/bin/env python3
"""DLP-1.2 Linter — valida e parseia mensagens no formato Duo-Link Pidgin.

Usage:
    python3 dlp_lint.py "P:DO B:you D:+5m U:mid A:check T:dia3 S:run N:report"
    python3 dlp_lint.py --parse "P:TELL B:you D:none U:low A:test T:pf S:ok E:logged_in=true"
    echo "P:ERR B:me D:+10m U:high A:patch T:xb S:blocked E:timeout N:retry" | python3 dlp_lint.py --stdin
"""

import argparse
import json
import re
import sys

# --- DLP-1.2 Grammar ---

PERFORMATIVES = {"ASK", "TELL", "DO", "DONE", "ERR", "HOLD", "PLAN", "ACK", "CLOSE"}
BALL = {"me", "you", "none", "both"}
URGENCY = {"low", "mid", "high"}
STATES = {"ok", "run", "wait", "blocked", "partial", "done", "skip"}

# Deadline: +Nm, +Nh, HH:MM, none
DEADLINE_RE = re.compile(r"^(\+\d+[mh]|\d{1,2}:\d{2}|none)$")

# Field pattern: KEY:VALUE (no spaces in value, except C: which is free text)
FIELD_RE = re.compile(r"([A-Z]):(\S+)")

REQUIRED_FIELDS = {"P", "B", "D", "A", "T", "S"}
OPTIONAL_FIELDS = {"U", "E", "N", "C"}
ALL_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS

# --- Glossary (DLP-1.2c compressed mode) ---
GLOSSARY = {
    "co": "claude-opus",
    "cx": "codex",
    "rpt": "report",
    "chk": "check",
    "nav": "navigate",
    "pub": "publish",
    "srch": "search",
    "cfg": "config",
    "pf": "preflight",
}


def parse_dlp(msg: str) -> dict:
    """Parse a DLP-1.2 message into fields dict."""
    result = {}

    # Split header | content
    if " | C:" in msg:
        header, content = msg.split(" | C:", 1)
        result["C"] = content.strip()
    elif " | " in msg:
        header, content = msg.split(" | ", 1)
        result["C"] = content.strip()
    else:
        header = msg

    # Parse fields from header
    for match in FIELD_RE.finditer(header):
        key, value = match.group(1), match.group(2)
        if key in ALL_FIELDS:
            result[key] = value

    return result


def validate_dlp(fields: dict) -> list[str]:
    """Validate parsed DLP fields. Returns list of errors (empty = valid)."""
    errors = []

    # Check required fields
    for f in REQUIRED_FIELDS:
        if f not in fields:
            errors.append(f"campo obrigatorio ausente: {f}")

    # Validate performative
    if "P" in fields and fields["P"] not in PERFORMATIVES:
        errors.append(f"P:{fields['P']} invalido — use {PERFORMATIVES}")

    # Validate ball
    if "B" in fields and fields["B"] not in BALL:
        errors.append(f"B:{fields['B']} invalido — use {BALL}")

    # Validate deadline
    if "D" in fields and not DEADLINE_RE.match(fields["D"]):
        errors.append(f"D:{fields['D']} invalido — use +Nm, +Nh, HH:MM ou none")

    # Validate urgency
    if "U" in fields and fields["U"] not in URGENCY:
        errors.append(f"U:{fields['U']} invalido — use {URGENCY}")

    # Validate state
    if "S" in fields and fields["S"] not in STATES:
        errors.append(f"S:{fields['S']} invalido — use {STATES}")

    # DLP-1.3: C-budget (max 120 chars for DLP-ops profile)
    C_BUDGET = 120
    if "C" in fields and len(fields["C"]) > C_BUDGET:
        errors.append(
            f"C-budget excedido: {len(fields['C'])} chars (max {C_BUDGET} em DLP-ops). use DLP-explain ou mande NL separado"
        )

    # Coherence checks
    if fields.get("P") == "DO" and fields.get("B") == "none":
        errors.append("P:DO com B:none — pedido sem dono")
    if fields.get("P") == "CLOSE" and fields.get("B") not in ("none", None):
        errors.append(f"P:CLOSE deveria usar B:none, nao B:{fields.get('B')}")

    return errors


def expand_glossary(fields: dict) -> dict:
    """Expand compressed aliases to full form."""
    expanded = {}
    for k, v in fields.items():
        if k in ("A", "T", "E", "N") and v in GLOSSARY:
            expanded[k] = f"{v} ({GLOSSARY[v]})"
        else:
            expanded[k] = v
    return expanded


def format_result(fields: dict, errors: list[str], expand: bool = False) -> str:
    """Format validation result."""
    if expand:
        fields = expand_glossary(fields)

    lines = []
    if errors:
        lines.append(f"INVALID ({len(errors)} erro(s)):")
        for e in errors:
            lines.append(f"  - {e}")
    else:
        lines.append("VALID")

    lines.append("Campos:")
    for k in "P B D U A T S E N C".split():
        if k in fields:
            v = fields[k]
            label = {
                "P": "performative",
                "B": "ball",
                "D": "deadline",
                "U": "urgency",
                "A": "action",
                "T": "target",
                "S": "state",
                "E": "evidence",
                "N": "next",
                "C": "content",
            }.get(k, k)
            lines.append(f"  {k} ({label}): {v}")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="DLP-1.2 Linter")
    parser.add_argument("message", nargs="?", help="Mensagem DLP")
    parser.add_argument("--parse", action="store_true", help="Parse e mostra campos")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument(
        "--expand", action="store_true", help="Expande aliases do glossario"
    )
    parser.add_argument("--stdin", action="store_true", help="Le do stdin")
    args = parser.parse_args()

    if args.stdin:
        msg = sys.stdin.read().strip()
    elif args.message:
        msg = args.message
    else:
        parser.print_help()
        return 1

    fields = parse_dlp(msg)
    errors = validate_dlp(fields)

    if args.json:
        print(
            json.dumps(
                {"valid": len(errors) == 0, "fields": fields, "errors": errors},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(format_result(fields, errors, expand=args.expand))

    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
