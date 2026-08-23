"""Pure, deterministic planning for safe Goal-derived Codex Agent work."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Mapping

from .domain import AgentProfile, Goal, Project, Task


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
