#!/usr/bin/env python3
"""Operate the Mission Control Buzz coordination boundary.

Secrets stay in the host environment used by ``buzz``. This script accepts
only canonical references and privacy-safe structured coordination payloads.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from gtasks.buzz_coordination import (
    BuzzCoordinationMessage,
    BuzzCoordinationOutbox,
    classify_inbound_coordination,
)


def _payload(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("coordination payload must be one JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    send = subparsers.add_parser("send")
    send.add_argument("payload", type=Path)
    send.add_argument("--outbox", required=True, type=Path)
    inbound = subparsers.add_parser("record-inbound")
    inbound.add_argument("payload", type=Path)
    inbound.add_argument("--sender-pubkey", required=True)
    inbound.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args(argv)

    if args.command == "send":
        value = _payload(args.payload)
        message = BuzzCoordinationMessage(
            task_slug=value.get("mc_task"),
            canonical_event_id=value.get("canonical_event_id"),
            canonical_version=value.get("canonical_version"),
            owner=value.get("owner"),
            agent=value.get("agent"),
            state=value.get("state"),
            next_action=value.get("next_action"),
            evidence=tuple(value.get("evidence") or ()),
            needs=value.get("needs", ""),
        )
        print(json.dumps(BuzzCoordinationOutbox(args.outbox).deliver(message), sort_keys=True))
        return 0

    proposal = classify_inbound_coordination(
        sender_pubkey=args.sender_pubkey,
        payload=_payload(args.payload),
    )
    args.receipt.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.receipt.write_text(json.dumps(proposal, sort_keys=True) + "\n", encoding="utf-8")
    args.receipt.chmod(0o600)
    print(json.dumps(proposal, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        raise SystemExit(1)
