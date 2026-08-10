#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo


DEFAULT_GTASKS_REPO_CANDIDATES = (
    Path("/Users/tony/work/gtasks"),
    Path.home() / "work/gtasks",
    Path.home() / "gtasks",
)
TONY_PROFILE = "people/tony-guan"
AGENT_ALIASES = {
    "tammy": "agents/tammy",
    "agents/tammy": "agents/tammy",
    "timmy": "agents/timmy",
    "agents/timmy": "agents/timmy",
    "toddy": "agents/toddy",
    "agents/toddy": "agents/toddy",
    "tammy-oc": "agents/tammy-oc",
    "tammy oc": "agents/tammy-oc",
    "tammy openclaw": "agents/tammy-oc",
    "agents/tammy-oc": "agents/tammy-oc",
    "timmy-oc": "agents/timmy-oc",
    "timmy oc": "agents/timmy-oc",
    "timmy openclaw": "agents/timmy-oc",
    "agents/timmy-oc": "agents/timmy-oc",
    "toddy-oc": "agents/toddy-oc",
    "toddy oc": "agents/toddy-oc",
    "toddy openclaw": "agents/toddy-oc",
    "agents/toddy-oc": "agents/toddy-oc",
}


def ensure_supported_python() -> None:
    if sys.version_info >= (3, 10):
        return
    candidates = (
        "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3",
        "/opt/homebrew/bin/python3.14",
        "/usr/local/opt/python@3.14/bin/python3.14",
        "/opt/homebrew/bin/python3.13",
        "/usr/local/opt/python@3.13/bin/python3.13",
        "/opt/homebrew/bin/python3.12",
        "/usr/local/opt/python@3.12/bin/python3.12",
        "/opt/homebrew/bin/python3.11",
        "/usr/local/opt/python@3.11/bin/python3.11",
        "/opt/homebrew/bin/python3.10",
        "/usr/local/opt/python@3.10/bin/python3.10",
    )
    current = Path(sys.executable).resolve()
    for candidate in candidates:
        path = Path(candidate)
        if not path.exists() or not os.access(path, os.X_OK):
            continue
        if path.resolve() == current:
            continue
        os.execv(str(path), [str(path), *sys.argv])
    raise SystemExit(
        "Mission Control task creation requires Python 3.10+; no supported "
        "Homebrew Python was found."
    )


def parse_due_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("due day must be YYYY-MM-DD") from exc


def resolve_owner_agent(raw: str | None) -> str | None:
    if raw is None or not raw.strip() or raw.strip().lower() in {"tony", TONY_PROFILE}:
        return None
    key = " ".join(raw.strip().lower().split())
    try:
        return AGENT_ALIASES[key]
    except KeyError as exc:
        allowed = ", ".join(sorted(set(AGENT_ALIASES)))
        raise argparse.ArgumentTypeError(
            f"unknown owner agent {raw!r}; allowed aliases: {allowed}"
        ) from exc


def stargraph_url(slug: str) -> str:
    return "http://127.0.0.1:8788/?slug=" + quote(slug, safe="")


def default_gtasks_repo() -> Path:
    for candidate in DEFAULT_GTASKS_REPO_CANDIDATES:
        if (candidate / "gtasks" / "gbrain.py").exists():
            return candidate
    return DEFAULT_GTASKS_REPO_CANDIDATES[0]


def main() -> int:
    ensure_supported_python()
    parser = argparse.ArgumentParser(description="Create and verify one Mission Control task.")
    parser.add_argument("--title", required=True)
    parser.add_argument("--detail", required=True)
    parser.add_argument("--due-day", required=True, type=parse_due_day)
    parser.add_argument("--owner-agent", type=resolve_owner_agent, default=None)
    parser.add_argument("--priority", default="normal", choices=["low", "normal", "high", "urgent"])
    parser.add_argument("--next-action", default="")
    parser.add_argument("--identity", default="codex-mc-add-task")
    parser.add_argument("--gtasks-repo", default=str(default_gtasks_repo()))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    repo = Path(args.gtasks_repo).expanduser().resolve()
    if not (repo / "gtasks" / "gbrain.py").exists():
        raise SystemExit(f"GTasks repo not found: {repo}")
    sys.path.insert(0, str(repo))

    from gtasks.domain import AGENT_SCOPES, new_task
    from gtasks.gbrain import GBrainAdapter, SubprocessCommandRunner
    from gtasks.markdown_policy import (
        MarkdownContractError,
        extract_system_ticket_slugs,
        reference_is_explicitly_labeled_system_ticket,
        render_task_body,
        validate_generated_markdown,
    )

    now = datetime.now(ZoneInfo("America/Los_Angeles"))
    task = new_task(
        title=args.title,
        detail=args.detail,
        due_day=args.due_day,
        priority=args.priority,
        next_action=args.next_action,
        now=now,
        identity=args.identity,
    )

    owner = "Tony"
    owner_agent = args.owner_agent
    if owner_agent:
        work_root = dict(AGENT_SCOPES).get(owner_agent)
        if work_root is None:
            raise SystemExit(f"Unsupported owner agent: {owner_agent}")
        task = replace(task, lifecycle_root=work_root, owner_agent=owner_agent)
        owner = owner_agent

    detail_values = (task.detail,)
    dry_run_references = {
        slug: None
        for slug in extract_system_ticket_slugs(task.detail)
        if reference_is_explicitly_labeled_system_ticket(slug, detail_values)
    }
    if args.dry_run:
        validate_generated_markdown(task.detail)
        try:
            rendered_body = render_task_body(
                task.title, task.detail, dry_run_references
            )
        except MarkdownContractError as exc:
            if "internal route is not an exact verified canonical System Ticket" not in str(exc):
                raise
            print(json.dumps({
                "ok": True,
                "dry_run": True,
                "verification_required": True,
                "unverified_system_ticket_slugs": list(
                    extract_system_ticket_slugs(task.detail)
                ),
                "markdown_contract": "unified-task-ticket-v1",
                "slug": task.slug,
                "title": task.title,
                "detail": task.detail,
                "rendered_body": None,
                "owner": owner,
                "lifecycle_root": task.lifecycle_root,
                "message": (
                    "Live canonical System Ticket title and membership "
                    f"verification is required before rendering: {exc}"
                ),
            }, indent=2, sort_keys=True))
            return 0
        print(json.dumps({
            "ok": True,
            "dry_run": True,
            "verification_required": False,
            "markdown_contract": "unified-task-ticket-v1",
            "slug": task.slug,
            "title": task.title,
            "summary": task.summary,
            "detail": task.detail,
            "rendered_body": rendered_body,
            "status": task.status,
            "priority": task.priority,
            "due_day": task.due_day.isoformat() if task.due_day else None,
            "owner": owner,
            "lifecycle_root": task.lifecycle_root,
            "next_action": task.next_action,
        }, indent=2, sort_keys=True))
        return 0

    adapter = GBrainAdapter(runner=SubprocessCommandRunner())
    if owner_agent:
        receipt = adapter.create_agent_task(task, owner_agent)
    else:
        receipt = adapter.create_task(task)

    if not receipt.verified:
        raise SystemExit(f"Task write returned unverified receipt for {receipt.slug}")

    page = adapter.runner.run("get_page", {"slug": task.slug})
    links = adapter.runner.run("get_links", {"slug": task.slug})
    if not isinstance(page, dict):
        raise SystemExit(
            f"Task title verification returned no canonical page for {task.slug}"
        )
    page_title = page.get("title")
    if page_title != task.title:
        raise SystemExit(
            "Task write completed but canonical title verification failed for "
            f"{task.slug}: expected {task.title!r}, got {page_title!r}. "
            "Inspect and repair this slug before retrying; do not create a replacement."
        )
    compiled_body = page.get("compiled_markdown")
    if not isinstance(compiled_body, str) or not compiled_body.strip():
        raise SystemExit(
            "Task write completed but compiled Markdown body verification failed for "
            f"{task.slug}. Inspect and repair this slug before retrying; do not create "
            "a replacement."
        )
    rendered_body = compiled_body
    typed = [
        {
            "from_slug": edge.get("from_slug"),
            "to_slug": edge.get("to_slug"),
            "link_type": edge.get("link_type"),
        }
        for edge in links
        if isinstance(edge, dict)
        and edge.get("from_slug") == task.slug
        and edge.get("link_type") in {"member_of", "assigned_to"}
    ]
    expected_links = {(task.slug, task.lifecycle_root, "member_of")}
    if owner_agent:
        expected_links.add((task.slug, owner_agent, "assigned_to"))
    actual_links = {
        (edge["from_slug"], edge["to_slug"], edge["link_type"])
        for edge in typed
    }
    if not expected_links.issubset(actual_links):
        raise SystemExit(
            "Task write completed but relationship verification failed for "
            f"{task.slug}: expected {sorted(expected_links)!r}, got "
            f"{sorted(actual_links)!r}. Inspect and repair this slug before retrying; "
            "do not create a replacement."
        )
    print(json.dumps({
        "ok": True,
        "verified": True,
        "markdown_contract": "unified-task-ticket-v1",
        "slug": task.slug,
        "title": task.title,
        "summary": task.summary,
        "status": task.status,
        "priority": task.priority,
        "due_day": task.due_day.isoformat() if task.due_day else None,
        "owner": owner,
        "lifecycle_root": task.lifecycle_root,
        "rendered_body": rendered_body,
        "stargraph_url": stargraph_url(task.slug),
        "links": typed,
        "page_title": page_title,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
