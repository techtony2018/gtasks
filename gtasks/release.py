from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from .releases import version_parts


DEFAULT_CATALOG_PATH = Path(__file__).with_name("releases.json")


def next_patch_version(version: str) -> str:
    major, minor, patch = version_parts(version)
    return f"V{major}.{minor}.{patch + 1}"


def bump_patch_release(
    catalog_path: Path,
    *,
    title: str,
    summary: str,
    release_date: str,
) -> str:
    clean_title = title.strip()
    clean_summary = summary.strip()
    if not clean_title or not clean_summary:
        raise ValueError("release title and summary are required")
    try:
        date.fromisoformat(release_date)
    except ValueError as exc:
        raise ValueError("release date must use YYYY-MM-DD") from exc

    raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    releases = raw.get("releases")
    current = raw.get("current_version")
    if (
        not isinstance(releases, list)
        or not releases
        or not isinstance(releases[-1], dict)
        or releases[-1].get("version") != current
        or not isinstance(current, str)
    ):
        raise ValueError("release catalog current_version does not match its history")

    new_version = next_patch_version(current)
    entry: dict[str, Any] = {
        "version": new_version,
        "date": release_date,
        "title": clean_title,
        "summary": clean_summary,
    }
    updated = dict(raw)
    updated["current_version"] = new_version
    updated["releases"] = [*releases, entry]

    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=catalog_path.parent,
            prefix=f".{catalog_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(updated, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = handle.name
        os.replace(temporary_path, catalog_path)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)
    return new_version


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Append the next sequential GTasks patch release.",
    )
    parser.add_argument("--title", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG_PATH,
        help=argparse.SUPPRESS,
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    version = bump_patch_release(
        args.catalog,
        title=args.title,
        summary=args.summary,
        release_date=args.date,
    )
    print(version)


if __name__ == "__main__":
    main()
