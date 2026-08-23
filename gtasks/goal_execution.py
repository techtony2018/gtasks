"""Pure, deterministic planning for safe Goal-derived Codex Agent work."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol

from .domain import AgentProfile, Goal, Project, Task
from .gbrain import PartialMutationError


PLANNER_VERSION = "goal-execution-v1"
AUTOMATIC_ACTION_KIND = "goal_progress_review"
AUTO_WIP_LIMIT = 1
DERIVED_TASK_NAMESPACE = uuid.UUID("d90827ae-4529-44c4-9c4c-e86eeb19764a")
EXPECTED_EVIDENCE = (
    "One internal progress brief with evidence, one bounded next step, "
    "and no external action."
)


@dataclass(frozen=True, slots=True)
class GoalExecutionCandidate:
    goal_slug: str
    project_slug: str | None
    agent_slug: str
    action_kind: str
    title: str
    detail: str
    expected_evidence: str
    fingerprint: str

    def to_dict(self) -> dict[str, object]:
        return {
            "goal_slug": self.goal_slug,
            "project_slug": self.project_slug,
            "agent_slug": self.agent_slug,
            "action_kind": self.action_kind,
            "title": self.title,
            "detail": self.detail,
            "expected_evidence": self.expected_evidence,
            "fingerprint": self.fingerprint,
            "task_slug": derived_task_slug(self.fingerprint),
        }


@dataclass(frozen=True, slots=True)
class GoalExecutionDecision:
    goal_slug: str
    reason: str
    candidate: GoalExecutionCandidate | None = None
    existing_task_slug: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "goal_slug": self.goal_slug,
            "reason": self.reason,
            "candidate": self.candidate.to_dict() if self.candidate else None,
            "existing_task_slug": self.existing_task_slug,
        }


@dataclass(frozen=True, slots=True)
class GoalExecutionSnapshot:
    goals: tuple[Goal, ...]
    projects: tuple[Project, ...]
    agents: tuple[AgentProfile, ...]
    tasks: tuple[Task, ...]
    route_health: Mapping[str, bool]


@dataclass(frozen=True, slots=True)
class GoalExecutionPlan:
    planner_version: str
    decisions: tuple[GoalExecutionDecision, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "planner_version": self.planner_version,
            "decisions": [decision.to_dict() for decision in self.decisions],
        }


def derived_task_slug(fingerprint: str) -> str:
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise ValueError("derived task fingerprint must be sha256")
    try:
        int(fingerprint, 16)
    except ValueError as exc:
        raise ValueError("derived task fingerprint must be sha256") from exc
    return f"tasks/{uuid.uuid5(DERIVED_TASK_NAMESPACE, fingerprint)}"


def _is_canonical_goal_slug(slug: str) -> bool:
    if not slug.startswith("goals/"):
        return False
    suffix = slug.split("/", 1)[1]
    try:
        parsed = uuid.UUID(suffix)
    except (ValueError, AttributeError):
        return False
    return str(parsed) == suffix.lower() and parsed.version in {4, 5}


def _candidate_fingerprint(
    *,
    goal_slug: str,
    project_slug: str | None,
    title: str,
    expected_evidence: str,
) -> str:
    value = {
        "planner_version": PLANNER_VERSION,
        "goal_slug": goal_slug,
        "project_slug": project_slug,
        "action_kind": AUTOMATIC_ACTION_KIND,
        "title": title,
        "expected_evidence": expected_evidence,
    }
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _candidate(
    goal: Goal,
    project: Project | None,
    agent: AgentProfile,
) -> GoalExecutionCandidate:
    title = (
        f"Review {goal.title} progress and publish one bounded next-step brief"
    )
    project_context = (
        f" Use canonical Project {project.slug} as the immediate scope."
        if project is not None
        else ""
    )
    detail = (
        f"Review canonical Goal {goal.slug}.{project_context} Compare its "
        "outcome, success criteria, strategy, current linked work, and "
        "available evidence. Publish one Agent Artifact containing verified "
        "progress, gaps, and one bounded next step. Do not send, publish, "
        "purchase, delete, change permissions, or mutate Tony Tasks."
    )
    fingerprint = _candidate_fingerprint(
        goal_slug=goal.slug,
        project_slug=project.slug if project else None,
        title=title,
        expected_evidence=EXPECTED_EVIDENCE,
    )
    return GoalExecutionCandidate(
        goal_slug=goal.slug,
        project_slug=project.slug if project else None,
        agent_slug=agent.slug,
        action_kind=AUTOMATIC_ACTION_KIND,
        title=title,
        detail=detail,
        expected_evidence=EXPECTED_EVIDENCE,
        fingerprint=fingerprint,
    )


class GoalExecutionPlanner:
    """Classify one bounded review candidate per available Codex Agent."""

    def plan(self, snapshot: GoalExecutionSnapshot) -> GoalExecutionPlan:
        decisions: list[GoalExecutionDecision] = []
        auto_agents: set[str] = set()
        for goal in sorted(snapshot.goals, key=lambda value: value.slug):
            if not _is_canonical_goal_slug(goal.slug):
                decisions.append(
                    GoalExecutionDecision(goal.slug, "legacy_alias_suppressed")
                )
                continue
            if goal.status not in {"planned", "active"}:
                reason = "goal_paused" if goal.status == "paused" else "goal_terminal"
                decisions.append(GoalExecutionDecision(goal.slug, reason))
                continue

            owners = tuple(
                agent
                for agent in snapshot.agents
                if goal.slug in agent.default_goal_slugs
            )
            if not owners:
                decisions.append(GoalExecutionDecision(goal.slug, "owner_missing"))
                continue
            if len(owners) != 1:
                decisions.append(GoalExecutionDecision(goal.slug, "owner_ambiguous"))
                continue
            owner = owners[0]
            if owner.runtime != "codex":
                decisions.append(
                    GoalExecutionDecision(goal.slug, "runtime_not_allowed")
                )
                continue

            existing = next(
                (
                    task
                    for task in snapshot.tasks
                    if task.goal == goal.slug
                    and task.status in {"planned", "active", "blocked"}
                ),
                None,
            )
            if existing is not None:
                decisions.append(
                    GoalExecutionDecision(
                        goal.slug,
                        "duplicate",
                        existing_task_slug=existing.slug,
                    )
                )
                continue

            projects = tuple(
                project
                for project in snapshot.projects
                if project.status == "active"
                and goal.slug in project.supporting_goal_slugs
            )
            if len(projects) > 1:
                decisions.append(
                    GoalExecutionDecision(goal.slug, "project_ambiguous")
                )
                continue
            selected_project = projects[0] if projects else None
            candidate = _candidate(goal, selected_project, owner)
            exact = next(
                (
                    task
                    for task in snapshot.tasks
                    if task.goal_derivation is not None
                    and task.goal_derivation.fingerprint == candidate.fingerprint
                ),
                None,
            )
            if exact is not None:
                decisions.append(
                    GoalExecutionDecision(
                        goal.slug,
                        "duplicate",
                        existing_task_slug=exact.slug,
                    )
                )
                continue

            active_wip = sum(
                1
                for task in snapshot.tasks
                if task.owner_agent == owner.slug and task.status == "active"
            )
            if active_wip >= AUTO_WIP_LIMIT:
                decisions.append(GoalExecutionDecision(goal.slug, "wip_full"))
                continue
            if snapshot.route_health.get(owner.slug) is not True:
                decisions.append(
                    GoalExecutionDecision(goal.slug, "route_unavailable")
                )
                continue
            if owner.slug in auto_agents:
                decisions.append(GoalExecutionDecision(goal.slug, "cycle_limit"))
                continue

            auto_agents.add(owner.slug)
            decisions.append(
                GoalExecutionDecision(
                    goal.slug,
                    "auto_eligible",
                    candidate=candidate,
                )
            )
        return GoalExecutionPlan(
            planner_version=PLANNER_VERSION,
            decisions=tuple(decisions),
        )


class GoalExecutionAdapter(Protocol):
    def read_goal_execution_snapshot(
        self, route_health: Mapping[str, bool]
    ) -> GoalExecutionSnapshot: ...

    def create_or_adopt_derived_agent_task(
        self, candidate: GoalExecutionCandidate, now: datetime
    ) -> Any: ...

    def set_task_status(self, task_slug: str, status: str, now: datetime) -> Any: ...


class GoalExecutionBridge(Protocol):
    dispatcher: Any

    def after_verified_mutation(
        self, before: object, after: object, receipt: object, now: datetime
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class GoalExecutionRun:
    mode: str
    ran_at: datetime
    planner_version: str
    decisions: tuple[GoalExecutionDecision, ...]
    public_reason: str
    task_slug: str | None = None
    task_title: str | None = None
    task_status: str | None = None
    agent_slug: str | None = None
    handoff_id: str | None = None
    handoff_status: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "ran_at": self.ran_at.isoformat(),
            "planner_version": self.planner_version,
            "decisions": [decision.to_dict() for decision in self.decisions],
            "public_reason": self.public_reason,
            "task": (
                {
                    "slug": self.task_slug,
                    "title": self.task_title,
                    "status": self.task_status,
                    "agent_slug": self.agent_slug,
                }
                if self.task_slug is not None
                else None
            ),
            "handoff": (
                {
                    "id": self.handoff_id,
                    "status": self.handoff_status,
                }
                if self.handoff_id is not None
                else None
            ),
        }

    @classmethod
    def from_plan(
        cls,
        plan: GoalExecutionPlan,
        *,
        mode: str,
        ran_at: datetime,
        goal_slug: str | None = None,
    ) -> "GoalExecutionRun":
        selected = next(
            (
                decision
                for decision in plan.decisions
                if goal_slug is None or decision.goal_slug == goal_slug
            ),
            None,
        )
        if mode == "off":
            reason = "off"
        elif mode == "shadow":
            reason = "shadow"
        elif selected is not None:
            reason = selected.reason
        else:
            reason = "no_eligible_work"
        return cls(
            mode=mode,
            ran_at=ran_at,
            planner_version=plan.planner_version,
            decisions=plan.decisions,
            public_reason=reason,
            task_slug=selected.existing_task_slug if selected else None,
        )

    @classmethod
    def for_task(
        cls,
        plan: GoalExecutionPlan,
        *,
        mode: str,
        ran_at: datetime,
        task: Task,
        public_reason: str,
        handoff: object | None = None,
    ) -> "GoalExecutionRun":
        return cls(
            mode=mode,
            ran_at=ran_at,
            planner_version=plan.planner_version,
            decisions=plan.decisions,
            public_reason=public_reason,
            task_slug=task.slug,
            task_title=task.title,
            task_status=task.status,
            agent_slug=task.owner_agent,
            handoff_id=(
                str(getattr(handoff, "handoff_id"))
                if handoff is not None and getattr(handoff, "handoff_id", None)
                else None
            ),
            handoff_status=(
                str(getattr(handoff, "status"))
                if handoff is not None and getattr(handoff, "status", None)
                else None
            ),
        )


class GoalExecutionEngine:
    """Activate at most one configured Goal through a verified fixed route."""

    _HANDOFF_ACCEPTED = frozenset(
        {
            "queued",
            "leased",
            "received",
            "execution_started",
            "active",
            "actively_executing",
        }
    )

    def __init__(
        self,
        *,
        adapter: GoalExecutionAdapter,
        bridge: GoalExecutionBridge,
        planner: GoalExecutionPlanner | None = None,
        mode: str = "shadow",
        canary_goal_slug: str | None = None,
    ) -> None:
        if mode not in {"off", "shadow", "canary"}:
            raise ValueError("goal execution mode must be off, shadow, or canary")
        if mode == "canary" and (
            not isinstance(canary_goal_slug, str)
            or not _is_canonical_goal_slug(canary_goal_slug)
        ):
            raise ValueError("canary mode requires one canonical Goal slug")
        self.adapter = adapter
        self.bridge = bridge
        self.planner = planner or GoalExecutionPlanner()
        self.mode = mode
        self.canary_goal_slug = canary_goal_slug

    def route_health(self) -> dict[str, bool]:
        registrations = tuple(
            getattr(getattr(self.bridge, "dispatcher", None), "registrations", ())
        )
        agent_slugs = {
            str(registration.agent_slug)
            for registration in registrations
            if isinstance(getattr(registration, "agent_slug", None), str)
        }
        route_agents: dict[str, set[str]] = {}
        for registration in registrations:
            if not getattr(registration, "verified", False):
                continue
            agent_slug = getattr(registration, "agent_slug", None)
            route = getattr(registration, "route", None)
            if isinstance(agent_slug, str) and isinstance(route, str):
                route_agents.setdefault(route, set()).add(agent_slug)
        health: dict[str, bool] = {}
        for agent_slug in agent_slugs:
            matches = tuple(
                registration
                for registration in registrations
                if getattr(registration, "verified", False)
                and getattr(registration, "agent_slug", None) == agent_slug
                and isinstance(getattr(registration, "route", None), str)
            )
            health[agent_slug] = (
                len(matches) == 1
                and len(route_agents.get(str(matches[0].route), set())) == 1
            )
        return health

    def run_once(self, now: datetime) -> GoalExecutionRun:
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("Goal execution run time must include a timezone")
        route_health = self.route_health()
        snapshot = self.adapter.read_goal_execution_snapshot(route_health)
        plan = self.planner.plan(snapshot)
        if self.mode != "canary":
            return GoalExecutionRun.from_plan(
                plan,
                mode=self.mode,
                ran_at=now,
                goal_slug=self.canary_goal_slug,
            )
        eligible = next(
            (
                value
                for value in plan.decisions
                if value.reason == "auto_eligible"
                and value.goal_slug == self.canary_goal_slug
            ),
            None,
        )
        if eligible is None or eligible.candidate is None:
            return GoalExecutionRun.from_plan(
                plan,
                mode=self.mode,
                ran_at=now,
                goal_slug=self.canary_goal_slug,
            )
        try:
            planned = self.adapter.create_or_adopt_derived_agent_task(
                eligible.candidate,
                now,
            ).task
        except PartialMutationError:
            return GoalExecutionRun(
                mode=self.mode,
                ran_at=now,
                planner_version=plan.planner_version,
                decisions=plan.decisions,
                public_reason="system_repair_required",
                task_slug=derived_task_slug(eligible.candidate.fingerprint),
                agent_slug=eligible.candidate.agent_slug,
            )
        if planned.status != "planned":
            return GoalExecutionRun.for_task(
                plan,
                mode=self.mode,
                ran_at=now,
                task=planned,
                public_reason="adopted",
            )
        try:
            activated = self.adapter.set_task_status(planned.slug, "active", now)
        except PartialMutationError:
            return GoalExecutionRun.for_task(
                plan,
                mode=self.mode,
                ran_at=now,
                task=planned,
                public_reason="system_repair_required",
            )
        if activated.verified is not True or activated.task.status != "active":
            return GoalExecutionRun.for_task(
                plan,
                mode=self.mode,
                ran_at=now,
                task=activated.task,
                public_reason="system_repair_required",
            )
        handoff = self.bridge.after_verified_mutation(
            planned.to_dict(),
            activated.task.to_dict(),
            {
                **activated.to_dict(),
                "mutation_kind": "task_status",
                "verified": True,
            },
            now,
        )
        reason = (
            "activated"
            if getattr(handoff, "status", None) in self._HANDOFF_ACCEPTED
            else "handoff_needs_repair"
        )
        return GoalExecutionRun.for_task(
            plan,
            mode=self.mode,
            ran_at=now,
            task=activated.task,
            public_reason=reason,
            handoff=handoff,
        )
