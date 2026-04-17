#!/usr/bin/env python3
"""DLP-1.4 Linter — valida e parseia mensagens do Duo-Link Pidgin.

Suporta modos:
  strict  — DLP-1.4 puro (header linha 1, corpo linha 2+, sem C:)
  compat  — aceita C: monolinha legado (default — backward-compatible com 1.3)

Usage:
    dlp lint "P:DO B:you D:+5m U:mid A:check T:dia3 X:d3-01 S:run N:report"
    dlp lint --strict "..."
    echo "..." | dlp lint --stdin
"""

from __future__ import annotations

import argparse
import json
import re
import sys

# --- DLP-1.4 Grammar ---

PERFORMATIVES = {
    "ASK",
    "TELL",
    "DO",
    "DONE",
    "ERR",
    "HOLD",
    "PLAN",
    "ACK",
    "CLOSE",
    "CANCEL",
}
BALL = {"me", "you", "none", "both"}
URGENCY = {"low", "mid", "high"}
# S: in 1.4 is pure execution state — result lives in E:
STATES = {"run", "wait", "blocked", "done", "skip"}
# 1.3 values still accepted in compat mode
STATES_LEGACY = {"ok", "partial"}

# Deadline: +Nm, +Nh, HH:MM, ISO-8601, none
DEADLINE_RELATIVE_RE = re.compile(r"^\+\d+[smh]$")
DEADLINE_HHMM_RE = re.compile(r"^\d{1,2}:\d{2}$")
DEADLINE_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{1,2}:\d{2}(:\d{2})?([+-]\d{2}:?\d{2}|Z)?$"
)

# Correlation id: <prefix>-<NN> or any \w[\w-]*
CORRELATION_RE = re.compile(r"^[\w][\w.-]{0,31}$")

# Field: KEY:VALUE with no spaces in value (except C: which is legacy free-text)
FIELD_RE = re.compile(r"(\b[A-Z]):(\S+)")

REQUIRED_FIELDS = {"P", "B", "D", "A", "T", "S"}
OPTIONAL_FIELDS = {"U", "E", "N", "X", "C"}
ALL_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS

C_BUDGET = 120


def parse_dlp(msg: str) -> tuple[dict, str]:
    """Parse a DLP message.

    Returns (fields, body). Header = line 1; body = line 2+ (natural language).
    Legacy `| C:` monolinha also captured into fields['C'].
    """
    lines = msg.splitlines()
    header = lines[0] if lines else ""
    body = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""

    fields: dict[str, str] = {}

    # Legacy monolinha: split ` | C:` or trailing ` | `
    c_legacy: str | None = None
    if " | C:" in header:
        header, c_legacy = header.split(" | C:", 1)
        c_legacy = c_legacy.strip()
    elif " | " in header:
        header, c_legacy = header.split(" | ", 1)
        c_legacy = c_legacy.strip()

    for match in FIELD_RE.finditer(header):
        key, value = match.group(1), match.group(2)
        if key in ALL_FIELDS:
            fields[key] = value

    if c_legacy is not None:
        fields["C"] = c_legacy

    return fields, body


def validate_deadline(value: str) -> str | None:
    if value == "none":
        return None
    if DEADLINE_RELATIVE_RE.match(value):
        return None
    if DEADLINE_HHMM_RE.match(value):
        return None
    if DEADLINE_ISO_RE.match(value):
        return None
    return f"D:{value} invalido — use +Nm, +Nh, HH:MM, ISO-8601 ou none"


def validate_dlp(
    fields: dict, body: str = "", strict: bool = False
) -> tuple[list[str], list[str]]:
    """Validate DLP fields. Returns (errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []

    # required fields
    for f in REQUIRED_FIELDS:
        if f not in fields:
            errors.append(f"campo obrigatorio ausente: {f}")

    p = fields.get("P")
    if p and p not in PERFORMATIVES:
        errors.append(f"P:{p} invalido — use {sorted(PERFORMATIVES)}")

    b = fields.get("B")
    if b and b not in BALL:
        errors.append(f"B:{b} invalido — use {sorted(BALL)}")

    d = fields.get("D")
    if d:
        err = validate_deadline(d)
        if err:
            errors.append(err)
        elif DEADLINE_HHMM_RE.match(d):
            warnings.append(
                f"D:{d} absoluto curto — use +Nm/+Nh em ciclo curto, ou ISO-8601 se houver ambiguidade"
            )

    u = fields.get("U")
    if u and u not in URGENCY:
        errors.append(f"U:{u} invalido — use {sorted(URGENCY)}")

    s = fields.get("S")
    if s:
        if s in STATES:
            pass
        elif s in STATES_LEGACY:
            if strict:
                errors.append(
                    f"S:{s} eh legado 1.3 — em strict use S:done + E:{s} (1.4 separa execucao de resultado)"
                )
            else:
                warnings.append(
                    f"S:{s} eh legado 1.3 — prefira S:done com E:{s}=... em 1.4"
                )
        else:
            errors.append(f"S:{s} invalido — use {sorted(STATES)}")

    x = fields.get("X")
    if x and not CORRELATION_RE.match(x):
        errors.append(
            f"X:{x} invalido — correlation id deve ser \\w[\\w.-]{{0,31}} (ex: pf-002)"
        )

    # C: legacy — strict rejeita
    if "C" in fields:
        if strict:
            errors.append(
                "C: nao permitido em strict — use corpo multilinha (linha 2+)"
            )
        elif len(fields["C"]) > C_BUDGET:
            errors.append(
                f"C-budget excedido: {len(fields['C'])} chars (max {C_BUDGET}). Use corpo multilinha."
            )

    # Coherence / contextual obligations
    if p == "DO":
        if b == "none":
            errors.append("P:DO com B:none — pedido sem dono")
        if "N" not in fields:
            warnings.append("P:DO sem N: — handoff fica ambiguo")
    if p == "ERR":
        if "E" not in fields:
            errors.append("P:ERR exige E: (motivo do erro)")
        if "N" not in fields:
            errors.append("P:ERR exige N: (proximo passo)")
        if s and s != "blocked":
            warnings.append(f"P:ERR com S:{s} — geralmente S:blocked")
    if p == "DONE":
        if "E" not in fields:
            errors.append("P:DONE exige E: (evidencia do resultado)")
        if s == "run":
            errors.append("P:DONE com S:run — incoerente (use S:done)")
    if p == "HOLD":
        if "N" not in fields:
            errors.append("P:HOLD exige N: (quando/como retomar)")
        if d == "none":
            warnings.append("P:HOLD com D:none — declare ate quando")
    if p == "CLOSE":
        if b not in ("none", None):
            errors.append(f"P:CLOSE exige B:none (tinha B:{b})")
        if "E" not in fields:
            errors.append(
                "P:CLOSE exige E: (window-elapsed, mutual-consent ou user-release)"
            )
    if p == "CANCEL":
        if "E" not in fields:
            errors.append("P:CANCEL exige E: (motivo da invalidacao)")
    if p == "ACK":
        if "C" in fields and len(fields.get("C", "")) > 60:
            warnings.append("P:ACK com C: longo — ACK deve ser curto")

    return errors, warnings


def format_result(
    fields: dict, body: str, errors: list[str], warnings: list[str]
) -> str:
    lines = []
    if errors:
        lines.append(f"INVALID ({len(errors)} erro(s))")
        for e in errors:
            lines.append(f"  ERR: {e}")
    else:
        lines.append("VALID")
    for w in warnings:
        lines.append(f"  WARN: {w}")

    lines.append("Campos:")
    labels = {
        "P": "performative",
        "B": "ball",
        "D": "deadline",
        "U": "urgency",
        "A": "action",
        "T": "target",
        "X": "correlation",
        "S": "state",
        "E": "evidence",
        "N": "next",
        "C": "content(legacy)",
    }
    for k in "P B D U A T X S E N C".split():
        if k in fields:
            lines.append(f"  {k} ({labels[k]}): {fields[k]}")
    if body:
        lines.append("Body:")
        for bline in body.splitlines():
            lines.append(f"  {bline}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="DLP-1.4 Linter")
    parser.add_argument("message", nargs="?", help="Mensagem DLP")
    parser.add_argument(
        "--strict", action="store_true", help="Modo strict (rejeita C: legado)"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--stdin", action="store_true", help="Le do stdin")
    args = parser.parse_args()

    if args.stdin:
        msg = sys.stdin.read().rstrip("\n")
    elif args.message:
        msg = args.message
    else:
        parser.print_help()
        return 1

    fields, body = parse_dlp(msg)
    errors, warnings = validate_dlp(fields, body, strict=args.strict)

    if args.json:
        print(
            json.dumps(
                {
                    "valid": len(errors) == 0,
                    "strict": args.strict,
                    "fields": fields,
                    "body": body,
                    "errors": errors,
                    "warnings": warnings,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(format_result(fields, body, errors, warnings))

    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
