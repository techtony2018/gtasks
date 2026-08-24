"""Pure, deterministic planning for safe Goal-derived Codex Agent work."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from threading import Condition, Thread
from time import monotonic
from types import SimpleNamespace
from typing import Any, Mapping, Protocol

from .domain import AgentProfile, Goal, Project, Task
from .gbrain import PartialMutationError


PLANNER_VERSION = "goal-execution-v1"
AUTOMATIC_ACTION_KIND = "goal_progress_review"
AUTO_WIP_LIMIT = 1
HANDOFF_WORKER_ATTENTION_AFTER = timedelta(minutes=5)
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
    cycle_day: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "goal_slug": self.goal_slug,
            "project_slug": self.project_slug,
            "agent_slug": self.agent_slug,
            "action_kind": self.action_kind,
            "title": self.title,
            "detail": self.detail,
            "expected_evidence": self.expected_evidence,
            "cycle_day": self.cycle_day,
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
    cycle_day: str | None,
) -> str:
    value = {
        "planner_version": PLANNER_VERSION,
        "goal_slug": goal_slug,
        "project_slug": project_slug,
        "action_kind": AUTOMATIC_ACTION_KIND,
        "title": title,
        "expected_evidence": expected_evidence,
        "cycle_day": cycle_day,
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
    *,
    cycle_day: date | None = None,
) -> GoalExecutionCandidate:
    cycle_text = cycle_day.isoformat() if cycle_day is not None else None
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
    if cycle_text is not None:
        detail = f"{detail} Review cycle starts {cycle_text}."
    fingerprint = _candidate_fingerprint(
        goal_slug=goal.slug,
        project_slug=project.slug if project else None,
        title=title,
        expected_evidence=EXPECTED_EVIDENCE,
        cycle_day=cycle_text,
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
        cycle_day=cycle_text,
    )


def _is_passive_scheduled_wait_task(task: Task) -> bool:
    """Return true only for explicit, non-actionable scheduled wait records."""
    next_action = task.next_action.strip().lower()
    return (
        task.status in {"planned", "active", "blocked"}
        and not task.todos
        and not task.blockers
        and not task.dependencies
        and next_action.startswith("wait for the next ")
        and ("scheduled" in next_action or " run" in next_action)
    )


def _needs_next_action(task: Task) -> bool:
    """Identify Agent work that blocks a Goal without an actionable handoff."""
    return (
        task.status in {"planned", "active"}
        and task.goal_derivation is None
        and not task.next_action.strip()
        and task.handoff is None
        and not task.blockers
        and not task.dependencies
        and not any(todo.status != "done" for todo in task.todos)
    )


def _parse_aware_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _stale_queued_handoff(delivery_state: object, now: datetime) -> bool:
    if not isinstance(delivery_state, Mapping):
        return False
    status = delivery_state.get("status")
    terminal_state = delivery_state.get("terminal_state")
    if status != "queued" or terminal_state not in {None, ""}:
        return False
    claimed_at = _parse_aware_datetime(delivery_state.get("claimed_at"))
    if claimed_at is None:
        return False
    return now - claimed_at >= HANDOFF_WORKER_ATTENTION_AFTER


class GoalExecutionPlanner:
    """Classify one bounded review candidate per available Codex Agent."""

    def __init__(self, *, cycle_day: date | None = None) -> None:
        self.cycle_day = cycle_day

    def plan(
        self,
        snapshot: GoalExecutionSnapshot,
        *,
        cycle_day: date | None = None,
    ) -> GoalExecutionPlan:
        effective_cycle_day = (
            cycle_day if cycle_day is not None else self.cycle_day
        )
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
                    and not _is_passive_scheduled_wait_task(task)
                ),
                None,
            )
            if existing is not None:
                reason = (
                    "task_needs_next_action"
                    if _needs_next_action(existing)
                    else "duplicate"
                )
                decisions.append(
                    GoalExecutionDecision(
                        goal.slug,
                        reason,
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
            candidate = _candidate(
                goal,
                selected_project,
                owner,
                cycle_day=effective_cycle_day,
            )
            exact = next(
                (
                    task
                    for task in snapshot.tasks
                    if task.goal_derivation is not None
                    and task.goal_derivation.fingerprint == candidate.fingerprint
                    and task.status in {"planned", "active", "blocked"}
                ),
                None,
            )
            if exact is not None:
                reason = (
                    "task_needs_next_action"
                    if _needs_next_action(exact)
                    else "duplicate"
                )
                decisions.append(
                    GoalExecutionDecision(
                        goal.slug,
                        reason,
                        existing_task_slug=exact.slug,
                    )
                )
                continue
            completed_exact = next(
                (
                    task
                    for task in snapshot.tasks
                    if task.goal_derivation is not None
                    and task.goal_derivation.fingerprint == candidate.fingerprint
                    and task.status == "completed"
                ),
                None,
            )
            if completed_exact is not None:
                decisions.append(
                    GoalExecutionDecision(
                        goal.slug,
                        "recently_completed",
                        existing_task_slug=completed_exact.slug,
                    )
                )
                continue

            active_wip = sum(
                1
                for task in snapshot.tasks
                if task.owner_agent == owner.slug and task.status == "active"
                and not _is_passive_scheduled_wait_task(task)
                and not _needs_next_action(task)
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

    def list_agent_artifacts(self, *, task: str, limit: int = 1) -> Any: ...

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
        public_decisions = []
        for decision in self.decisions:
            task_slug = decision.existing_task_slug
            if task_slug is None and decision.candidate is not None:
                task_slug = derived_task_slug(decision.candidate.fingerprint)
            public_decisions.append(
                {
                    "goal_slug": decision.goal_slug,
                    "reason": decision.reason,
                    "task_slug": task_slug,
                }
            )
        return {
            "mode": self.mode,
            "ran_at": self.ran_at.isoformat(),
            "planner_version": self.planner_version,
            "decisions": public_decisions,
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
                {"status": self.handoff_status}
                if self.handoff_status is not None
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
    _HANDOFF_ATTENTION = frozenset({"dead_letter", "suppressed", "handed_back"})

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
        health: dict[str, bool] = {}
        for agent_slug in agent_slugs:
            matches = tuple(
                registration
                for registration in registrations
                if getattr(registration, "verified", False)
                and getattr(registration, "agent_slug", None) == agent_slug
                and isinstance(getattr(registration, "route", None), str)
                and bool(getattr(registration, "route", "").strip())
            )
            health[agent_slug] = len(matches) == 1
        return health

    def run_once(self, now: datetime) -> GoalExecutionRun:
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("Goal execution run time must include a timezone")
        if self.mode == "off":
            return GoalExecutionRun.from_plan(
                GoalExecutionPlan(PLANNER_VERSION, ()),
                mode=self.mode,
                ran_at=now,
            )
        route_health = self.route_health()
        snapshot = self.adapter.read_goal_execution_snapshot(route_health)
        plan = self.planner.plan(snapshot, cycle_day=now.date())
        plan, handoff_status_by_task = self._annotate_handoff_attention(
            plan,
            snapshot,
            now=now,
        )
        if self.mode != "canary":
            return self._run_from_plan(
                plan,
                snapshot=snapshot,
                mode=self.mode,
                ran_at=now,
                goal_slug=self.canary_goal_slug,
                handoff_status_by_task=handoff_status_by_task,
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
            reconciled = self._reconcile_selected_completed_handoff(
                plan,
                snapshot=snapshot,
                ran_at=now,
                goal_slug=self.canary_goal_slug,
                handoff_status_by_task=handoff_status_by_task,
            )
            if reconciled is not None:
                return reconciled
            return self._run_from_plan(
                plan,
                snapshot=snapshot,
                mode=self.mode,
                ran_at=now,
                goal_slug=self.canary_goal_slug,
                handoff_status_by_task=handoff_status_by_task,
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

    def _task_has_verified_artifact(self, task_slug: str) -> bool:
        list_artifacts = getattr(self.adapter, "list_agent_artifacts", None)
        if not callable(list_artifacts):
            return False
        result = list_artifacts(task=task_slug, limit=1)
        artifacts = getattr(result, "artifacts", None)
        if artifacts is None and isinstance(result, Mapping):
            artifacts = result.get("artifacts")
        if not isinstance(artifacts, (tuple, list)):
            return False
        return any(getattr(artifact, "produced_for", None) == task_slug for artifact in artifacts)

    def _reconcile_selected_completed_handoff(
        self,
        plan: GoalExecutionPlan,
        *,
        snapshot: GoalExecutionSnapshot,
        ran_at: datetime,
        goal_slug: str | None,
        handoff_status_by_task: Mapping[str, str | None],
    ) -> GoalExecutionRun | None:
        selected = next(
            (
                decision
                for decision in plan.decisions
                if goal_slug is None or decision.goal_slug == goal_slug
            ),
            None,
        )
        if (
            selected is None
            or selected.reason != "duplicate"
            or selected.existing_task_slug is None
            or handoff_status_by_task.get(selected.existing_task_slug) != "completed"
        ):
            return None
        task = next(
            (
                item
                for item in snapshot.tasks
                if item.slug == selected.existing_task_slug
            ),
            None,
        )
        if (
            task is None
            or task.status != "active"
            or task.goal_derivation is None
            or not self._task_has_verified_artifact(task.slug)
        ):
            return None
        try:
            receipt = self.adapter.set_task_status(task.slug, "completed", ran_at)
        except PartialMutationError:
            return GoalExecutionRun.for_task(
                plan,
                mode="canary",
                ran_at=ran_at,
                task=task,
                public_reason="system_repair_required",
                handoff=SimpleNamespace(status="completed"),
            )
        if receipt.verified is not True or receipt.task.status != "completed":
            return GoalExecutionRun.for_task(
                plan,
                mode="canary",
                ran_at=ran_at,
                task=receipt.task,
                public_reason="system_repair_required",
                handoff=SimpleNamespace(status="completed"),
            )
        revised_plan = GoalExecutionPlan(
            plan.planner_version,
            tuple(
                replace(decision, reason="completed_after_verified_handoff")
                if decision is selected
                else decision
                for decision in plan.decisions
            ),
        )
        return GoalExecutionRun.for_task(
            revised_plan,
            mode="canary",
            ran_at=ran_at,
            task=receipt.task,
            public_reason="completed_after_verified_handoff",
            handoff=SimpleNamespace(status="completed"),
        )

    def _annotate_handoff_attention(
        self,
        plan: GoalExecutionPlan,
        snapshot: GoalExecutionSnapshot,
        *,
        now: datetime,
    ) -> tuple[GoalExecutionPlan, dict[str, str | None]]:
        latest_status = getattr(self.bridge, "latest_task_handoff_status", None)
        if not callable(latest_status):
            return plan, {}
        latest_delivery_state = getattr(
            self.bridge,
            "latest_task_handoff_delivery_state",
            None,
        )
        tasks_by_slug = {task.slug: task for task in snapshot.tasks}
        revised: list[GoalExecutionDecision] = []
        handoff_status_by_task: dict[str, str | None] = {}
        changed = False
        for decision in plan.decisions:
            task_slug = decision.existing_task_slug
            if decision.reason != "duplicate" or task_slug is None:
                revised.append(decision)
                continue
            status = latest_status(task_slug)
            if isinstance(status, Mapping):
                status = status.get("status")
            handoff_status_by_task[task_slug] = status if isinstance(status, str) else None
            if isinstance(status, str) and status in self._HANDOFF_ATTENTION:
                revised.append(replace(decision, reason="handoff_needs_repair"))
                changed = True
                continue
            if (
                status == "queued"
                and callable(latest_delivery_state)
                and _stale_queued_handoff(latest_delivery_state(task_slug), now)
            ):
                revised.append(replace(decision, reason="handoff_worker_unavailable"))
                changed = True
                continue
            task = tasks_by_slug.get(task_slug)
            if (
                status is None
                and task is not None
                and task.goal_derivation is not None
                and task.status in {"planned", "active"}
            ):
                revised.append(replace(decision, reason="handoff_missing"))
                changed = True
                continue
            revised.append(decision)
        if not changed:
            return plan, handoff_status_by_task
        return GoalExecutionPlan(plan.planner_version, tuple(revised)), handoff_status_by_task

    @staticmethod
    def _run_from_plan(
        plan: GoalExecutionPlan,
        *,
        snapshot: GoalExecutionSnapshot,
        mode: str,
        ran_at: datetime,
        goal_slug: str | None,
        handoff_status_by_task: Mapping[str, str | None],
    ) -> GoalExecutionRun:
        selected = next(
            (
                decision
                for decision in plan.decisions
                if goal_slug is None or decision.goal_slug == goal_slug
            ),
            None,
        )
        if (
            selected is not None
            and selected.reason in {
                "handoff_needs_repair",
                "handoff_missing",
                "task_needs_next_action",
                "handoff_worker_unavailable",
            }
            and selected.existing_task_slug is not None
        ):
            task = next(
                (
                    item
                    for item in snapshot.tasks
                    if item.slug == selected.existing_task_slug
                ),
                None,
            )
            if task is not None:
                status = handoff_status_by_task.get(task.slug)
                if selected.reason == "handoff_missing":
                    status = "missing"
                handoff = (
                    SimpleNamespace(status=status)
                    if status is not None
                    else None
                )
                return GoalExecutionRun.for_task(
                    plan,
                    mode=mode,
                    ran_at=ran_at,
                    task=task,
                    public_reason=selected.reason,
                    handoff=handoff,
                )
        if (
            selected is not None
            and selected.reason == "duplicate"
            and selected.existing_task_slug is not None
        ):
            task = next(
                (
                    item
                    for item in snapshot.tasks
                    if item.slug == selected.existing_task_slug
                ),
                None,
            )
            if (
                task is not None
                and task.goal_derivation is not None
                and task.status in {"planned", "active"}
                and task.slug in handoff_status_by_task
                and handoff_status_by_task[task.slug] is None
            ):
                revised_plan = GoalExecutionPlan(
                    plan.planner_version,
                    tuple(
                        replace(decision, reason="handoff_missing")
                        if decision is selected
                        else decision
                        for decision in plan.decisions
                    ),
                )
                return GoalExecutionRun.for_task(
                    revised_plan,
                    mode=mode,
                    ran_at=ran_at,
                    task=task,
                    public_reason="handoff_missing",
                    handoff=SimpleNamespace(status="missing"),
                )
        if (
            goal_slug is not None
            and selected is not None
            and selected.reason in {"duplicate", "recently_completed"}
            and selected.existing_task_slug is not None
        ):
            task = next(
                (
                    item
                    for item in snapshot.tasks
                    if item.slug == selected.existing_task_slug
                ),
                None,
            )
            if task is not None:
                return GoalExecutionRun.for_task(
                    plan,
                    mode=mode,
                    ran_at=ran_at,
                    task=task,
                    public_reason=selected.reason,
                )
        return GoalExecutionRun.from_plan(
            plan,
            mode=mode,
            ran_at=ran_at,
            goal_slug=goal_slug,
        )


class GoalExecutionScheduler:
    """One coalescing worker with bounded retry and reconciliation intervals."""

    def __init__(
        self,
        engine: GoalExecutionEngine,
        *,
        clock: Any | None = None,
        monotonic_clock: Any | None = None,
        minimum_interval_seconds: float = 30,
        reconcile_interval_seconds: float = 1800,
    ) -> None:
        if minimum_interval_seconds < 30:
            raise ValueError("Goal execution minimum interval must be at least 30 seconds")
        if not (
            minimum_interval_seconds <= reconcile_interval_seconds <= 1800
        ):
            raise ValueError(
                "Goal execution reconciliation interval must be bounded by 30 minutes"
            )
        self.engine = engine
        self.minimum_interval_seconds = minimum_interval_seconds
        self.reconcile_interval_seconds = reconcile_interval_seconds
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._monotonic = monotonic_clock or monotonic
        self._condition = Condition()
        self._thread: Thread | None = None
        self._stopping = False
        self._pending = True
        self._last_started_mono: float | None = None
        self._next_reconcile_mono = self._monotonic()
        self._last_run: dict[str, object] | None = None
        self._last_error: str | None = None

    @property
    def is_running(self) -> bool:
        with self._condition:
            return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._condition:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stopping = False
            self._pending = True
            self._thread = Thread(
                target=self._loop,
                name="mission-control-goal-execution",
                daemon=True,
            )
            self._thread.start()

    def wake(self, reason: str) -> None:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Goal execution wake reason is required")
        with self._condition:
            self._pending = True
            self._condition.notify_all()

    def stop(self, timeout_seconds: float = 5) -> None:
        with self._condition:
            self._stopping = True
            thread = self._thread
            self._condition.notify_all()
        if thread is not None:
            thread.join(timeout=timeout_seconds)
            if thread.is_alive():
                raise RuntimeError("Goal execution scheduler did not stop")
        with self._condition:
            self._thread = None

    def status(self) -> dict[str, object]:
        with self._condition:
            now = self._monotonic()
            if self._last_started_mono is None:
                next_in = 0.0
            elif self._pending:
                next_in = max(
                    0.0,
                    self.minimum_interval_seconds
                    - (now - self._last_started_mono),
                )
            else:
                next_in = max(0.0, self._next_reconcile_mono - now)
            return {
                "mode": self.engine.mode,
                "planner_version": PLANNER_VERSION,
                "running": self._thread is not None and self._thread.is_alive(),
                "last_run": dict(self._last_run) if self._last_run else None,
                "last_error": self._last_error,
                "next_run_in_seconds": round(next_in, 3),
            }

    def _loop(self) -> None:
        while True:
            with self._condition:
                while True:
                    if self._stopping:
                        return
                    now = self._monotonic()
                    minimum_due = (
                        self._last_started_mono is None
                        or now - self._last_started_mono
                        >= self.minimum_interval_seconds
                    )
                    reconcile_due = now >= self._next_reconcile_mono
                    if minimum_due and (self._pending or reconcile_due):
                        self._pending = False
                        self._last_started_mono = now
                        self._next_reconcile_mono = (
                            now + self.reconcile_interval_seconds
                        )
                        break
                    waits = [max(0.01, self._next_reconcile_mono - now)]
                    if self._pending and self._last_started_mono is not None:
                        waits.append(
                            max(
                                0.01,
                                self.minimum_interval_seconds
                                - (now - self._last_started_mono),
                            )
                        )
                    self._condition.wait(timeout=min(waits))
            try:
                result = self.engine.run_once(self._clock())
                rendered = result.to_dict()
                if not isinstance(rendered, Mapping):
                    raise TypeError("Goal execution run did not return a mapping")
            except Exception as exc:
                with self._condition:
                    self._last_error = type(exc).__name__
                    self._pending = True
            else:
                with self._condition:
                    self._last_run = dict(rendered)
                    self._last_error = None
