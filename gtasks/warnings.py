from __future__ import annotations

import getpass
import hashlib
import json
import os
import tempfile
from pathlib import Path
from threading import Lock
from typing import Any, Iterable, Mapping


FINGERPRINT_FIELDS = (
    "slug",
    "message",
    "severity",
    "category",
    "impact",
    "repair_action",
)


def warning_fingerprint(issue: Mapping[str, Any]) -> str:
    identity = {
        "version": 1,
        **{field: issue.get(field) for field in FINGERPRINT_FIELDS},
    }
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def default_warning_state_path() -> Path:
    configured = os.environ.get("GTASKS_WARNING_STATE_FILE")
    if configured:
        return Path(configured).expanduser()
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "GTasks"
        / "warning-dismissals.json"
    )


class WarningDismissalStore:
    def __init__(
        self,
        path: Path | None = None,
        *,
        user_id: str | None = None,
    ) -> None:
        self.path = path or default_warning_state_path()
        self.user_id = user_id or getpass.getuser()
        self._lock = Lock()

    def _read_unlocked(self) -> set[str]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return set()
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"warning dismissal state could not be read: {exc}"
            ) from exc
        if not isinstance(raw, Mapping) or raw.get("version") != 1:
            raise RuntimeError("warning dismissal state has an unsupported format")
        if raw.get("user") != self.user_id:
            raise RuntimeError("warning dismissal state belongs to another user")
        fingerprints = raw.get("dismissed_fingerprints")
        if not isinstance(fingerprints, list) or not all(
            isinstance(value, str) and len(value) == 64 for value in fingerprints
        ):
            raise RuntimeError("warning dismissal state is malformed")
        return set(fingerprints)

    def dismissed(self) -> set[str]:
        with self._lock:
            return self._read_unlocked()

    def _write_unlocked(self, fingerprints: set[str]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "user": self.user_id,
            "dismissed_fingerprints": sorted(fingerprints),
        }
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            text=True,
        )
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, self.path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    def dismiss(self, fingerprint: str) -> bool:
        self._validate_fingerprint(fingerprint)
        with self._lock:
            fingerprints = self._read_unlocked()
            fingerprints.add(fingerprint)
            self._write_unlocked(fingerprints)
            return fingerprint in self._read_unlocked()

    def restore(self, fingerprint: str) -> bool:
        self._validate_fingerprint(fingerprint)
        with self._lock:
            fingerprints = self._read_unlocked()
            fingerprints.discard(fingerprint)
            self._write_unlocked(fingerprints)
            return fingerprint not in self._read_unlocked()

    @staticmethod
    def _validate_fingerprint(fingerprint: str) -> None:
        if len(fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in fingerprint
        ):
            raise ValueError("warning fingerprint must be a SHA-256 hex digest")

    def decorate(
        self,
        issues: Iterable[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        dismissed = self.dismissed()
        result: list[dict[str, Any]] = []
        for issue in issues:
            fingerprint = warning_fingerprint(issue)
            result.append(
                {
                    **dict(issue),
                    "fingerprint": fingerprint,
                    "dismissed": fingerprint in dismissed,
                }
            )
        return result
