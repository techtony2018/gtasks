from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from zoneinfo import ZoneInfo


TONY_PROFILE_SLUG = "people/tony-guan"
DISPLAY_TIMEZONE = "America/Los_Angeles"
MINIMUM_DURATION = timedelta(minutes=15)
MAXIMUM_DURATION = timedelta(days=7)
_PAIRED_OPENCLAW_AGENTS = {
    "agents/tammy": "agents/tammy-oc",
    "agents/timmy": "agents/timmy-oc",
    "agents/toddy": "agents/toddy-oc",
}


class DelegationState(StrEnum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"
    REVOKED = "revoked"


def _utc_instant(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be an aware UTC instant")
    return value.astimezone(timezone.utc)


def _canonical_delegation_slug(value: str) -> str:
    prefix = "agent-delegations/"
    if not isinstance(value, str) or not value.startswith(prefix):
        raise ValueError("lease slug must use a canonical UUID slug")
    suffix = value[len(prefix):]
    parts = suffix.split("-")
    if [len(part) for part in parts] != [8, 4, 4, 4, 12]:
        raise ValueError("lease slug must use a canonical UUID slug")
    compact = "".join(parts)
    if compact.lower() != compact:
        raise ValueError("lease slug must use a canonical UUID slug")
    try:
        int(compact, 16)
    except ValueError as exc:
        raise ValueError("lease slug must use a canonical UUID slug") from exc
    if compact[12] not in {"4", "5"} or compact[16] not in {"8", "9", "a", "b"}:
        raise ValueError("lease slug must use a canonical UUID slug")
    return value


def paired_openclaw_agent(source_agent: str) -> str:
    try:
        return _PAIRED_OPENCLAW_AGENTS[source_agent]
    except KeyError as exc:
        raise ValueError("source_agent must be a paired Codex Agent") from exc


@dataclass(frozen=True, slots=True)
class AgentDelegationLease:
    slug: str
    source_agent: str
    executor_agent: str
    authorized_by: str
    starts_at: datetime
    ends_at: datetime
    display_timezone: str
    allowed_operations: tuple[str, ...]
    state: DelegationState
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _canonical_delegation_slug(self.slug)
        paired_executor = paired_openclaw_agent(self.source_agent)
        if self.executor_agent != paired_executor:
            raise ValueError("executor_agent must be the fixed paired OpenClaw Agent")
        if self.authorized_by != TONY_PROFILE_SLUG:
            raise ValueError("delegation must be authorized by Tony")
        if self.display_timezone != DISPLAY_TIMEZONE:
            raise ValueError(f"display_timezone must be {DISPLAY_TIMEZONE}")
        ZoneInfo(self.display_timezone)
        if not isinstance(self.allowed_operations, tuple) or not self.allowed_operations or any(
            not isinstance(operation, str) or not operation or operation != operation.strip()
            for operation in self.allowed_operations
        ):
            raise ValueError("allowed_operations must be a non-empty tuple of operations")
        if not isinstance(self.state, DelegationState):
            raise ValueError("state must be a DelegationState")

        starts_at = _utc_instant(self.starts_at, "starts_at")
        ends_at = _utc_instant(self.ends_at, "ends_at")
        created_at = _utc_instant(self.created_at, "created_at")
        updated_at = _utc_instant(self.updated_at, "updated_at")
        if ends_at <= starts_at:
            raise ValueError("ends_at must be strictly after starts_at")
        duration = ends_at - starts_at
        if not MINIMUM_DURATION <= duration <= MAXIMUM_DURATION:
            raise ValueError("lease duration must be 15 minutes through 7 days")
        if updated_at < created_at:
            raise ValueError("updated_at must not precede created_at")

        object.__setattr__(self, "starts_at", starts_at)
        object.__setattr__(self, "ends_at", ends_at)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)


def lease_state_at(lease: AgentDelegationLease, now: datetime) -> DelegationState:
    current = _utc_instant(now, "now")
    if lease.state in {
        DelegationState.COMPLETED,
        DelegationState.EXPIRED,
        DelegationState.REVOKED,
    }:
        return lease.state
    if current < lease.starts_at:
        return DelegationState.SCHEDULED
    if current >= lease.ends_at:
        return DelegationState.EXPIRED
    return DelegationState.ACTIVE


def delegated_work_is_eligible(
    *,
    owned_work_ready: bool,
    task_status: str,
    task_owner: str,
    lease: AgentDelegationLease,
    now: datetime,
) -> bool:
    return (
        not owned_work_ready
        and task_status == "planned"
        and task_owner == lease.source_agent
        and lease_state_at(lease, now) == DelegationState.ACTIVE
    )
