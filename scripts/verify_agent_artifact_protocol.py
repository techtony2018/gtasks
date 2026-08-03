#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from string import Template


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_ROOT = ROOT / "config" / "agent-artifact-protocol"
BEGIN = "Mission Control Agent Artifact publication identity v1"


def load_instance(key: str) -> dict[str, str]:
    path = PROTOCOL_ROOT / "instances" / f"{key}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "key",
        "name",
        "agent_slug",
        "task_collection",
        "artifact_collection",
        "daytime_automation_id",
        "nighttime_automation_id",
    }
    if set(payload) != required or payload["key"] != key or not all(
        isinstance(value, str) and value for value in payload.values()
    ):
        raise ValueError("Agent Artifact identity instance is invalid")
    return payload


def render_identity(key: str) -> str:
    template = Template(
        (PROTOCOL_ROOT / "prompt-template.txt").read_text(encoding="utf-8")
    )
    return template.substitute(load_instance(key))


def render(key: str, mode: str) -> str:
    if mode not in {"daytime", "nighttime"}:
        raise ValueError("Automation mode must be daytime or nighttime")
    instance = load_instance(key)
    template = Template(
        (PROTOCOL_ROOT / f"{mode}-template.txt").read_text(encoding="utf-8")
    )
    return template.substitute(instance).rstrip() + "\n\n" + render_identity(key)


def verify_rendered(key: str, mode: str) -> dict[str, object]:
    expected = render(key, mode)
    path = PROTOCOL_ROOT / "rendered" / f"{key}-{mode}.txt"
    actual = path.read_text(encoding="utf-8")
    if actual != expected:
        raise ValueError("Rendered Agent Artifact instruction has drifted")
    return {
        "agent": key,
        "mode": mode,
        "rendered_path": str(path),
        "verified": True,
    }


def verify_automation(key: str, mode: str, path: Path) -> dict[str, object]:
    instance = load_instance(key)
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    expected_id = instance[f"{mode}_automation_id"]
    if payload.get("id") != expected_id:
        raise ValueError("Automation id does not match this isolated Agent contract")
    prompt = payload.get("prompt")
    expected = render(key, mode).strip()
    if not isinstance(prompt, str) or prompt.count(BEGIN) != 1 or prompt != expected:
        raise ValueError("Installed Agent Artifact instruction is missing or has drifted")
    required_preserved = ("status", "rrule", "target_thread_id")
    if any(
        not isinstance(payload.get(field), str) or not payload[field]
        for field in required_preserved
    ):
        raise ValueError("Installed automation is missing preserved routing or schedule fields")
    return {
        "agent": key,
        "mode": mode,
        "automation_id": payload["id"],
        "status": payload.get("status"),
        "rrule": payload.get("rrule"),
        "target_thread_id": payload.get("target_thread_id"),
        "verified": True,
    }


def emit_update_input(key: str, mode: str, path: Path) -> dict[str, object]:
    """Emit reviewed inputs for the supported automation_update boundary.

    This deliberately never edits automation.toml.  The operator must submit
    the returned prompt through Codex's automation tool, preserving the
    installed scheduling and fixed-task routing values shown in ``preserve``.
    """
    instance = load_instance(key)
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    expected_id = instance[f"{mode}_automation_id"]
    if payload.get("id") != expected_id:
        raise ValueError("Automation id does not match this isolated Agent contract")
    required_preserved = ("status", "rrule", "target_thread_id")
    if any(not isinstance(payload.get(field), str) or not payload[field] for field in required_preserved):
        raise ValueError("Installed automation is missing preserved routing or schedule fields")
    return {
        "operation": "automation_update",
        "automation_id": expected_id,
        "fields": {"prompt": render(key, mode).strip()},
        "preserve": {field: payload[field] for field in required_preserved},
        "requires_supported_tool": True,
        "writes_automation_toml": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render or verify one isolated Agent Artifact instruction."
    )
    parser.add_argument("agent", choices=("tammy", "timmy", "toddy"))
    parser.add_argument("mode", choices=("daytime", "nighttime"))
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--automation-file", type=Path)
    parser.add_argument("--emit-update-input", action="store_true")
    args = parser.parse_args()
    try:
        if args.render:
            sys.stdout.write(render(args.agent, args.mode))
            return 0
        if args.emit_update_input:
            if not args.automation_file:
                raise ValueError("--emit-update-input requires --automation-file")
            receipt = emit_update_input(args.agent, args.mode, args.automation_file)
        else:
            receipt = (
                verify_automation(args.agent, args.mode, args.automation_file)
                if args.automation_file
                else verify_rendered(args.agent, args.mode)
            )
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
