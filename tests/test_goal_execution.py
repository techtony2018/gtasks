import unittest
from dataclasses import replace
from datetime import date, datetime, timezone

from gtasks.domain import AgentProfile, Goal, Project, new_task
from gtasks.goal_execution import (
    GoalExecutionPlanner,
    GoalExecutionSnapshot,
    derived_task_slug,
)


GOAL = "goals/41fb50e0-e1d7-592b-b2c3-ff1f7aacff10"
OTHER_GOAL = "goals/755548a3-d556-513a-900c-45f90da5702e"
AGENT = "agents/timmy"
WORK_ROOT = "collections/timmys-tasks"
PROJECT = "projects/97b3214e-53d3-5506-beb1-0705816484f9"
NOW = datetime(2026, 8, 23, 9, tzinfo=timezone.utc)


def goal(
    slug: str = GOAL,
    *,
    status: str = "planned",
    title: str = "Civic: Help California be better through political action",
) -> Goal:
    return Goal(
        slug=slug,
        title=title,
        status=status,
        outcome="Help California be better through political action.",
        success_criteria="Maintain evidence-backed civic progress.",
        target_day=date(2026, 12, 31),
        strategy="Review current work and choose one bounded next step.",
        review_cadence="weekly",
        constraints="No external action without Tony approval.",
    )


def agent(
    *,
    slug: str = AGENT,
    goals: tuple[str, ...] = (GOAL,),
    runtime: str = "codex",
) -> AgentProfile:
    return AgentProfile(
        slug=slug,
        name="Timmy",
        title="Agent Timmy",
        summary="Civic and systems research.",
        work_root=WORK_ROOT,
        default_goal_slugs=goals,
        runtime=runtime,
    )


def project(
    slug: str = PROJECT,
    *,
    goals: tuple[str, ...] = (GOAL,),
) -> Project:
    return Project(
        slug=slug,
        title="ERFA PAC",
        status="active",
        summary="Maintain the internal evidence and next-action ledger.",
        supporting_goal_slugs=goals,
    )


def agent_task(
    *,
    slug_identity: str,
    goal_slug: str | None,
    status: str,
    owner: str = AGENT,
):
    task = new_task(
        title="Existing Agent work",
        detail="Existing bounded work.",
        now=NOW,
        identity=slug_identity,
        goal=goal_slug,
    )
    return replace(
        task,
        status=status,
        lifecycle_root=WORK_ROOT,
        owner_agent=owner,
    )


def snapshot(
    *,
    goals: tuple[Goal, ...] | None = None,
    projects: tuple[Project, ...] = (),
    agents: tuple[AgentProfile, ...] | None = None,
    tasks=(),
    route_health: dict[str, bool] | None = None,
) -> GoalExecutionSnapshot:
    return GoalExecutionSnapshot(
        goals=goals if goals is not None else (goal(),),
        projects=projects,
        agents=agents if agents is not None else (agent(),),
        tasks=tuple(tasks),
        route_health=route_health if route_health is not None else {AGENT: True},
    )


class GoalExecutionPlannerTests(unittest.TestCase):
    def test_plans_one_internal_review_for_owned_goal_without_open_goal_work(self) -> None:
        plan = GoalExecutionPlanner().plan(snapshot())

        self.assertEqual(len(plan.decisions), 1)
        decision = plan.decisions[0]
        self.assertEqual(decision.reason, "auto_eligible")
        self.assertEqual(decision.candidate.goal_slug, GOAL)
        self.assertEqual(decision.candidate.agent_slug, AGENT)
        self.assertEqual(decision.candidate.action_kind, "goal_progress_review")
        self.assertIn("Do not send", decision.candidate.detail)
        self.assertEqual(
            derived_task_slug(decision.candidate.fingerprint),
            derived_task_slug(decision.candidate.fingerprint),
        )

    def test_fingerprint_and_task_slug_are_stable_across_input_order(self) -> None:
        first = GoalExecutionPlanner().plan(
            snapshot(projects=(project(),))
        ).decisions[0].candidate
        second = GoalExecutionPlanner().plan(
            snapshot(
                projects=(project(),),
                agents=(agent(),),
                goals=(goal(),),
            )
        ).decisions[0].candidate

        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(len(first.fingerprint), 64)
        self.assertEqual(derived_task_slug(first.fingerprint), derived_task_slug(second.fingerprint))
        self.assertEqual(first.project_slug, PROJECT)

    def test_existing_open_task_for_goal_suppresses_duplicate(self) -> None:
        plan = GoalExecutionPlanner().plan(
            snapshot(
                tasks=(
                    agent_task(
                        slug_identity="existing-goal-work",
                        goal_slug=GOAL,
                        status="active",
                    ),
                )
            )
        )

        self.assertEqual(plan.decisions[0].reason, "duplicate")
        self.assertTrue(plan.decisions[0].existing_task_slug.startswith("tasks/"))

    def test_active_agent_wip_suppresses_new_activation(self) -> None:
        plan = GoalExecutionPlanner().plan(
            snapshot(
                tasks=(
                    agent_task(
                        slug_identity="other-active",
                        goal_slug=OTHER_GOAL,
                        status="active",
                    ),
                )
            )
        )

        self.assertEqual(plan.decisions[0].reason, "wip_full")

    def test_unrelated_planned_work_does_not_consume_active_wip(self) -> None:
        plan = GoalExecutionPlanner().plan(
            snapshot(
                tasks=(
                    agent_task(
                        slug_identity="other-planned",
                        goal_slug=None,
                        status="planned",
                    ),
                )
            )
        )

        self.assertEqual(plan.decisions[0].reason, "auto_eligible")

    def test_missing_and_duplicate_goal_owner_never_infer_agent(self) -> None:
        missing = GoalExecutionPlanner().plan(snapshot(agents=()))
        duplicate = GoalExecutionPlanner().plan(
            snapshot(
                agents=(
                    agent(),
                    agent(
                        slug="agents/tammy",
                        goals=(GOAL,),
                    ),
                )
            )
        )

        self.assertEqual(missing.decisions[0].reason, "owner_missing")
        self.assertEqual(duplicate.decisions[0].reason, "owner_ambiguous")

    def test_openclaw_owner_is_not_eligible(self) -> None:
        plan = GoalExecutionPlanner().plan(
            snapshot(agents=(agent(runtime="openclaw"),))
        )

        self.assertEqual(plan.decisions[0].reason, "runtime_not_allowed")

    def test_unhealthy_fixed_route_is_system_repair(self) -> None:
        plan = GoalExecutionPlanner().plan(
            snapshot(route_health={AGENT: False})
        )

        self.assertEqual(plan.decisions[0].reason, "route_unavailable")

    def test_legacy_alias_goal_is_suppressed_before_owner_or_task_derivation(self) -> None:
        plan = GoalExecutionPlanner().plan(
            snapshot(
                goals=(goal(slug="goals/california-political-action"),),
                agents=(),
            )
        )

        self.assertEqual(plan.decisions[0].reason, "legacy_alias_suppressed")
        self.assertIsNone(plan.decisions[0].candidate)

    def test_one_agent_receives_at_most_one_eligible_candidate_per_cycle(self) -> None:
        second_goal = "goals/840b3122-b299-5991-96be-30364c7f2e12"
        plan = GoalExecutionPlanner().plan(
            snapshot(
                goals=(goal(), goal(slug=second_goal, title="Finance")),
                agents=(agent(goals=(GOAL, second_goal)),),
            )
        )

        self.assertEqual(
            [decision.reason for decision in plan.decisions],
            ["auto_eligible", "cycle_limit"],
        )

    def test_multiple_active_projects_for_goal_require_attention(self) -> None:
        plan = GoalExecutionPlanner().plan(
            snapshot(
                projects=(
                    project(),
                    project(slug="projects/06dafbd8-9a92-59dd-b77d-cacb09f5a22e"),
                )
            )
        )

        self.assertEqual(plan.decisions[0].reason, "project_ambiguous")
        self.assertIsNone(plan.decisions[0].candidate)

    def test_completed_and_cancelled_goals_are_not_actionable(self) -> None:
        plan = GoalExecutionPlanner().plan(
            snapshot(goals=(goal(status="completed"), goal(slug=OTHER_GOAL, status="cancelled")))
        )

        self.assertEqual(
            [decision.reason for decision in plan.decisions],
            ["goal_terminal", "goal_terminal"],
        )


if __name__ == "__main__":
    unittest.main()
