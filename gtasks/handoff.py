from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping


class DomainValidationError(ValueError):
    """Raised when canonical Mission Control data violates its domain contract."""


def _timestamp(value: Any, field: str, *, required: bool) -> datetime | None:
    if value is None:
        if required:
            raise DomainValidationError(f"handoff {field} is required")
        return None
    if not isinstance(value, str):
        raise DomainValidationError(f"handoff {field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DomainValidationError(
            f"handoff {field} must be an ISO timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise DomainValidationError(f"handoff {field} must include a timezone")
    return parsed


@dataclass(frozen=True, slots=True)
class TaskHandoff:
    state: str
    question_todo: str
    waiting_on: str | None
    resume_owner: str
    resume_action: str
    requested_at: datetime
    answered_at: datetime | None
    acknowledged_at: datetime | None
    round: int

    STATES = frozenset({"waiting_for_input", "ready_for_agent", "agent_working"})

    @classmethod
    def from_value(cls, value: Mapping[str, Any]) -> "TaskHandoff":
        if not isinstance(value, Mapping):
            raise DomainValidationError("handoff must be an object")
        state = value.get("state")
        if state not in cls.STATES:
            raise DomainValidationError("handoff state is invalid")
        question_todo = value.get("question_todo")
        if not isinstance(question_todo, str) or not question_todo.startswith("todos/"):
            raise DomainValidationError("handoff question_todo must be a canonical todos/ slug")
        resume_owner = value.get("resume_owner")
        if not isinstance(resume_owner, str) or not resume_owner.startswith("agents/"):
            raise DomainValidationError("handoff resume_owner must be a canonical agents/ slug")
        waiting_on = value.get("waiting_on")
        if waiting_on is not None and (
            not isinstance(waiting_on, str) or "/" not in waiting_on
        ):
            raise DomainValidationError("handoff waiting_on must be a canonical slug or null")
        resume_action = value.get("resume_action")
        if (
            not isinstance(resume_action, str)
            or not resume_action.strip()
            or len(resume_action.strip()) > 240
            or "\n" in resume_action
            or "\r" in resume_action
        ):
            raise DomainValidationError(
                "handoff resume_action must be one concise line of 240 characters or fewer"
            )
        round_number = value.get("round")
        if (
            isinstance(round_number, bool)
            or not isinstance(round_number, int)
            or round_number < 1
        ):
            raise DomainValidationError("handoff round must be a positive whole number")
        requested_at = _timestamp(value.get("requested_at"), "requested_at", required=True)
        answered_at = _timestamp(value.get("answered_at"), "answered_at", required=False)
        acknowledged_at = _timestamp(
            value.get("acknowledged_at"), "acknowledged_at", required=False
        )
        assert requested_at is not None
        if answered_at is not None and answered_at < requested_at:
            raise DomainValidationError("handoff answered_at cannot precede requested_at")
        if acknowledged_at is not None and (
            answered_at is None or acknowledged_at < answered_at
        ):
            raise DomainValidationError(
                "handoff acknowledged_at requires and cannot precede answered_at"
            )
        if state == "waiting_for_input":
            if waiting_on is None or answered_at is not None or acknowledged_at is not None:
                raise DomainValidationError(
                    "waiting_for_input requires waiting_on and no answer or acknowledgement"
                )
        elif state == "ready_for_agent":
            if waiting_on is not None or answered_at is None or acknowledged_at is not None:
                raise DomainValidationError(
                    "ready_for_agent requires an answer and no waiting_on or acknowledgement"
                )
        elif waiting_on is not None or answered_at is None or acknowledged_at is None:
            raise DomainValidationError(
                "agent_working requires an answer and acknowledgement with no waiting_on"
            )
        return cls(
            state=str(state),
            question_todo=question_todo,
            waiting_on=waiting_on,
            resume_owner=resume_owner,
            resume_action=resume_action.strip(),
            requested_at=requested_at,
            answered_at=answered_at,
            acknowledged_at=acknowledged_at,
            round=round_number,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "question_todo": self.question_todo,
            "waiting_on": self.waiting_on,
            "resume_owner": self.resume_owner,
            "resume_action": self.resume_action,
            "requested_at": self.requested_at.isoformat(),
            "answered_at": self.answered_at.isoformat() if self.answered_at else None,
            "acknowledged_at": (
                self.acknowledged_at.isoformat() if self.acknowledged_at else None
            ),
            "round": self.round,
        }


def validate_task_handoff(task: Any) -> None:
    handoff = task.handoff
    if handoff is None:
        return
    if task.owner_agent != handoff.resume_owner:
        raise DomainValidationError(
            "handoff resume_owner must match the task's assigned Agent"
        )
    if handoff.state == "waiting_for_input":
        if task.status != "blocked":
            raise DomainValidationError(
                "waiting_for_input requires blocked task status"
            )
        if handoff.waiting_on not in task.blockers:
            raise DomainValidationError(
                "waiting_for_input requires its waiting_on blocked_by edge"
            )
        return
    if task.status != "active":
        raise DomainValidationError("agent-ready handoff requires active task status")
    if task.next_action.strip() != handoff.resume_action:
        raise DomainValidationError(
            "agent-ready handoff requires the resume action as next_action"
        )
