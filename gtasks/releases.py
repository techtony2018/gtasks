from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_CATALOG_PATH = Path(__file__).with_name("releases.json")
_VERSION_PATTERN = re.compile(r"^V\d+\.\d+\.\d+$")


def _load_catalog() -> tuple[dict[str, Any], ...]:
    raw = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    releases = raw.get("releases")
    current = raw.get("current_version")
    if not isinstance(releases, list) or not releases:
        raise RuntimeError("release catalog must contain at least one release")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for release in releases:
        if not isinstance(release, dict):
            raise RuntimeError("release catalog entries must be objects")
        version = release.get("version")
        if not isinstance(version, str) or not _VERSION_PATTERN.fullmatch(version):
            raise RuntimeError("release version must use Vmajor.minor.patch")
        if version in seen:
            raise RuntimeError(f"duplicate release version: {version}")
        seen.add(version)
        for field in ("date", "title", "summary"):
            if not isinstance(release.get(field), str) or not release[field].strip():
                raise RuntimeError(f"release {version} is missing {field}")
        normalized.append(
            {
                "version": version,
                "date": release["date"],
                "title": release["title"],
                "summary": release["summary"],
            }
        )
    if current != normalized[-1]["version"]:
        raise RuntimeError("current_version must match the latest release entry")
    return tuple(normalized)


RELEASES = _load_catalog()
CURRENT_RELEASE = RELEASES[-1]
CURRENT_VERSION = str(CURRENT_RELEASE["version"])


def release_payload() -> dict[str, Any]:
    return {
        "current_version": CURRENT_VERSION,
        "releases": [dict(release) for release in reversed(RELEASES)],
    }
