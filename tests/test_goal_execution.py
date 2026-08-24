import unittest
import threading
import time
from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace

from gtasks.domain import (
    AgentProfile,
    Goal,
    GoalDerivationReceipt,
    Project,
    new_task,
)
from gtasks.goal_execution import (
    GoalExecutionEngine,
    GoalExecutionPlanner,
    GoalExecutionScheduler,
    GoalExecutionSnapshot,
    derived_task_slug,
)
from gtasks.gbrain import PartialMutationError, StatusMutationReceipt, TaskEditReceipt
from gtasks.handoff_dispatcher import AgentRegistration


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
                    replace(
                        agent_task(
                            slug_identity="existing-goal-work",
                            goal_slug=GOAL,
                            status="active",
                        ),
                        next_action="Publish the bounded Goal progress brief.",
                    ),
                )
            )
        )

        self.assertEqual(plan.decisions[0].reason, "duplicate")
        self.assertTrue(plan.decisions[0].existing_task_slug.startswith("tasks/"))

    def test_existing_goal_task_without_next_action_requires_attention(self) -> None:
        stalled = agent_task(
            slug_identity="existing-goal-work-without-next-action",
            goal_slug=GOAL,
            status="active",
        )

        plan = GoalExecutionPlanner().plan(snapshot(tasks=(stalled,)))

        self.assertEqual(plan.decisions[0].reason, "task_needs_next_action")
        self.assertEqual(plan.decisions[0].existing_task_slug, stalled.slug)

    def test_passive_scheduled_wait_task_does_not_suppress_goal_review(self) -> None:
        passive = replace(
            agent_task(
                slug_identity="weekly-passive-wait",
                goal_slug=GOAL,
                status="active",
            ),
            next_action="Wait for the next weekly scheduled run.",
        )

        plan = GoalExecutionPlanner().plan(snapshot(tasks=(passive,)))

        self.assertEqual(plan.decisions[0].reason, "auto_eligible")
        self.assertIsNotNone(plan.decisions[0].candidate)

    def test_terminal_derived_goal_task_does_not_suppress_new_candidate(self) -> None:
        first = GoalExecutionPlanner().plan(snapshot())
        candidate = first.decisions[0].candidate
        self.assertIsNotNone(candidate)
        terminal = replace(
            agent_task(
                slug_identity="terminal-goal-work",
                goal_slug=GOAL,
                status="cancelled",
            ),
            slug=derived_task_slug(candidate.fingerprint),
            goal_derivation=GoalDerivationReceipt(
                planner_version="goal-execution-v1",
                fingerprint=candidate.fingerprint,
                action_kind=candidate.action_kind,
                authority_class="auto_eligible",
                goal_slug=candidate.goal_slug,
                project_slug=candidate.project_slug,
                expected_evidence=candidate.expected_evidence,
            ),
        )

        plan = GoalExecutionPlanner().plan(snapshot(tasks=(terminal,)))

        self.assertEqual(plan.decisions[0].reason, "auto_eligible")
        self.assertIsNotNone(plan.decisions[0].candidate)

    def test_completed_exact_derived_goal_task_suppresses_repeat_review(self) -> None:
        first = GoalExecutionPlanner().plan(snapshot())
        candidate = first.decisions[0].candidate
        self.assertIsNotNone(candidate)
        completed = replace(
            agent_task(
                slug_identity="completed-goal-work",
                goal_slug=GOAL,
                status="completed",
            ),
            slug=derived_task_slug(candidate.fingerprint),
            goal_derivation=GoalDerivationReceipt(
                planner_version="goal-execution-v1",
                fingerprint=candidate.fingerprint,
                action_kind=candidate.action_kind,
                authority_class="auto_eligible",
                goal_slug=candidate.goal_slug,
                project_slug=candidate.project_slug,
                expected_evidence=candidate.expected_evidence,
            ),
        )

        plan = GoalExecutionPlanner().plan(snapshot(tasks=(completed,)))

        self.assertEqual(plan.decisions[0].reason, "recently_completed")
        self.assertEqual(plan.decisions[0].existing_task_slug, completed.slug)
        self.assertIsNone(plan.decisions[0].candidate)

    def test_active_agent_wip_suppresses_new_activation(self) -> None:
        plan = GoalExecutionPlanner().plan(
            snapshot(
                tasks=(
                    replace(
                        agent_task(
                            slug_identity="other-active",
                            goal_slug=OTHER_GOAL,
                            status="active",
                        ),
                        next_action="Publish the bounded Goal progress brief.",
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

    def test_unrelated_active_task_needing_next_action_does_not_consume_active_wip(self) -> None:
        stalled = agent_task(
            slug_identity="other-active-without-next-action",
            goal_slug=OTHER_GOAL,
            status="active",
        )

        plan = GoalExecutionPlanner().plan(snapshot(tasks=(stalled,)))

        self.assertEqual(plan.decisions[0].reason, "auto_eligible")
        self.assertIsNotNone(plan.decisions[0].candidate)

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


class GoalExecutionEngineTests(unittest.TestCase):
    class Adapter:
        def __init__(self, *, tasks=(), activation_error: Exception | None = None):
            self.tasks = list(tasks)
            self.activation_error = activation_error
            self.calls: list[tuple[str, object]] = []

        def read_goal_execution_snapshot(self, route_health):
            self.calls.append(("snapshot", dict(route_health)))
            return snapshot(tasks=tuple(self.tasks), route_health=dict(route_health))

        def create_or_adopt_derived_agent_task(self, candidate, now):
            self.calls.append(("create", candidate.fingerprint))
            existing = next(
                (
                    item
                    for item in self.tasks
                    if item.goal_derivation is not None
                    and item.goal_derivation.fingerprint == candidate.fingerprint
                ),
                None,
            )
            if existing is None:
                base = new_task(
                    title=candidate.title,
                    detail=candidate.detail,
                    now=now,
                    identity=candidate.fingerprint,
                    next_action="Publish the verified internal progress brief and one bounded next step.",
                    project=candidate.project_slug,
                    goal=candidate.goal_slug,
                )
                existing = replace(
                    base,
                    slug=derived_task_slug(candidate.fingerprint),
                    lifecycle_root=WORK_ROOT,
                    owner_agent=AGENT,
                    goal_derivation=GoalDerivationReceipt(
                        planner_version="goal-execution-v1",
                        fingerprint=candidate.fingerprint,
                        action_kind=candidate.action_kind,
                        authority_class="auto_eligible",
                        goal_slug=candidate.goal_slug,
                        project_slug=candidate.project_slug,
                        expected_evidence=candidate.expected_evidence,
                    ),
                )
                self.tasks.append(existing)
            return TaskEditReceipt(existing.slug, existing, True)

        def set_task_status(self, task_slug, status, now):
            self.calls.append(("status", (task_slug, status)))
            if self.activation_error is not None:
                raise self.activation_error
            task = next(item for item in self.tasks if item.slug == task_slug)
            task = replace(task, status=status, updated_at=now)
            self.tasks = [task if item.slug == task_slug else item for item in self.tasks]
            return StatusMutationReceipt(
                task_slug=task.slug,
                status=task.status,
                lifecycle_root=task.lifecycle_root,
                completed_at=task.completed_at,
                task=task,
                verified=True,
            )

    class Bridge:
        def __init__(self, *, status: str = "queued", verified: bool = True):
            self.dispatcher = SimpleNamespace(
                registrations=(
                    AgentRegistration(
                        registration_id="private-registration-timmy",
                        agent_slug=AGENT,
                        route="hosts/timmy",
                        verified=verified,
                    ),
                )
            )
            self.status = status
            self.latest_status: str | None = None
            self.calls: list[tuple[dict, dict, dict]] = []

        def after_verified_mutation(self, before, after, receipt, now):
            self.calls.append((before, after, receipt))
            return SimpleNamespace(
                handoff_id="handoffs/goal-execution-canary",
                status=self.status,
                reason="delivery failed" if self.status == "dead_letter" else "queued",
            )

        def latest_task_handoff_status(self, task_slug):
            return self.latest_status

    def engine(self, adapter=None, bridge=None):
        return GoalExecutionEngine(
            adapter=adapter or self.Adapter(),
            bridge=bridge or self.Bridge(),
            mode="canary",
            canary_goal_slug=GOAL,
        )

    def test_run_once_creates_planned_reads_back_activates_and_dispatches(self) -> None:
        adapter = self.Adapter()
        bridge = self.Bridge()

        result = self.engine(adapter, bridge).run_once(NOW)

        self.assertEqual(
            [name for name, _value in adapter.calls],
            ["snapshot", "create", "status"],
        )
        self.assertEqual(len(bridge.calls), 1)
        before, after, receipt = bridge.calls[0]
        self.assertEqual(before["status"], "planned")
        self.assertEqual(after["status"], "active")
        self.assertTrue(receipt["verified"])
        self.assertEqual(receipt["mutation_kind"], "task_status")
        self.assertEqual(result.task_slug, adapter.tasks[0].slug)
        self.assertEqual(result.task_status, "active")
        self.assertEqual(result.handoff_id, "handoffs/goal-execution-canary")
        self.assertEqual(result.public_reason, "activated")

        public = result.to_dict()
        self.assertEqual(
            set(public["decisions"][0]),
            {"goal_slug", "reason", "task_slug"},
        )
        self.assertEqual(public["handoff"], {"status": "queued"})
        self.assertNotIn("hosts/", repr(public))
        self.assertNotIn("Do not send", repr(public))

    def test_run_once_does_not_activate_when_route_is_unhealthy(self) -> None:
        adapter = self.Adapter()
        bridge = self.Bridge(verified=False)

        result = self.engine(adapter, bridge).run_once(NOW)

        self.assertEqual(result.public_reason, "route_unavailable")
        self.assertEqual([name for name, _value in adapter.calls], ["snapshot"])
        self.assertEqual(bridge.calls, [])

    def test_run_once_does_not_create_when_wip_is_full(self) -> None:
        adapter = self.Adapter(
            tasks=(
                replace(
                    agent_task(
                        slug_identity="other-active",
                        goal_slug=OTHER_GOAL,
                        status="active",
                    ),
                    next_action="Publish the bounded Goal progress brief.",
                ),
            )
        )

        result = self.engine(adapter, self.Bridge()).run_once(NOW)

        self.assertEqual(result.public_reason, "wip_full")
        self.assertEqual([name for name, _value in adapter.calls], ["snapshot"])

    def test_repeat_run_adopts_same_task_and_does_not_redeliver(self) -> None:
        adapter = self.Adapter()
        bridge = self.Bridge()
        engine = self.engine(adapter, bridge)

        first = engine.run_once(NOW)
        second = engine.run_once(NOW)

        self.assertEqual(first.task_slug, second.task_slug)
        self.assertEqual(
            [name for name, _value in adapter.calls].count("status"),
            1,
        )
        self.assertEqual(len(bridge.calls), 1)
        self.assertEqual(first.handoff_id, "handoffs/goal-execution-canary")

    def test_existing_completed_goal_task_reports_title_status_and_agent(self) -> None:
        first_plan = GoalExecutionPlanner().plan(snapshot())
        candidate = first_plan.decisions[0].candidate
        self.assertIsNotNone(candidate)
        completed = replace(
            agent_task(
                slug_identity="completed-goal-work",
                goal_slug=GOAL,
                status="completed",
            ),
            slug=derived_task_slug(candidate.fingerprint),
            title="Completed Goal Review",
            summary="Completed Goal Review",
            goal_derivation=GoalDerivationReceipt(
                planner_version="goal-execution-v1",
                fingerprint=candidate.fingerprint,
                action_kind=candidate.action_kind,
                authority_class="auto_eligible",
                goal_slug=candidate.goal_slug,
                project_slug=candidate.project_slug,
                expected_evidence=candidate.expected_evidence,
            ),
        )
        adapter = self.Adapter(tasks=(completed,))

        result = self.engine(adapter, self.Bridge()).run_once(NOW)

        self.assertEqual(result.public_reason, "recently_completed")
        self.assertEqual(result.task_slug, completed.slug)
        self.assertEqual(result.task_title, "Completed Goal Review")
        self.assertEqual(result.task_status, "completed")
        self.assertEqual(result.agent_slug, AGENT)

    def test_existing_goal_task_with_terminal_handoff_reports_repair_not_duplicate(self) -> None:
        adapter = self.Adapter()
        bridge = self.Bridge()
        engine = self.engine(adapter, bridge)
        first = engine.run_once(NOW)
        bridge.latest_status = "dead_letter"

        second = engine.run_once(NOW)

        self.assertEqual(first.task_slug, second.task_slug)
        self.assertEqual(second.public_reason, "handoff_needs_repair")
        self.assertEqual(second.handoff_status, "dead_letter")
        self.assertEqual(
            second.to_dict()["decisions"][0]["reason"],
            "handoff_needs_repair",
        )
        self.assertEqual(
            [name for name, _value in adapter.calls],
            ["snapshot", "create", "status", "snapshot"],
        )
        self.assertEqual(len(bridge.calls), 1)

    def test_existing_active_goal_task_without_handoff_reports_missing_handoff(self) -> None:
        first_plan = GoalExecutionPlanner().plan(snapshot(projects=(project(),)))
        candidate = first_plan.decisions[0].candidate
        self.assertIsNotNone(candidate)
        existing = replace(
            agent_task(
                slug_identity="existing-active-goal-work",
                goal_slug=GOAL,
                status="active",
            ),
            slug=derived_task_slug(candidate.fingerprint),
            title="Active Goal Review",
            summary="Active Goal Review",
            project=PROJECT,
            goal_derivation=GoalDerivationReceipt(
                planner_version="goal-execution-v1",
                fingerprint=candidate.fingerprint,
                action_kind=candidate.action_kind,
                authority_class="auto_eligible",
                goal_slug=candidate.goal_slug,
                project_slug=candidate.project_slug,
                expected_evidence=candidate.expected_evidence,
            ),
        )
        adapter = self.Adapter(tasks=(existing,))
        bridge = self.Bridge()

        result = self.engine(adapter, bridge).run_once(NOW)

        self.assertEqual(result.public_reason, "handoff_missing")
        self.assertEqual(result.task_slug, existing.slug)
        self.assertEqual(result.task_title, "Active Goal Review")
        self.assertEqual(result.task_status, "active")
        self.assertEqual(result.agent_slug, AGENT)
        self.assertEqual(result.handoff_status, "missing")
        self.assertEqual(
            result.to_dict()["decisions"][0]["reason"],
            "handoff_missing",
        )
        self.assertEqual([name for name, _value in adapter.calls], ["snapshot"])
        self.assertEqual(bridge.calls, [])

    def test_missing_handoff_is_projected_for_each_derived_duplicate_decision(self) -> None:
        other_agent = agent(
            slug="agents/toddy",
            goals=(OTHER_GOAL,),
        )
        first_plan = GoalExecutionPlanner().plan(
            snapshot(
                goals=(goal(), goal(slug=OTHER_GOAL, title="Faith")),
                agents=(agent(), other_agent),
                projects=(project(goals=(GOAL,)),),
                route_health={AGENT: True, "agents/toddy": True},
            )
        )
        other_candidate = next(
            decision.candidate
            for decision in first_plan.decisions
            if decision.goal_slug == OTHER_GOAL
        )
        self.assertIsNotNone(other_candidate)
        existing = replace(
            agent_task(
                slug_identity="other-active-goal-work",
                goal_slug=OTHER_GOAL,
                status="active",
                owner="agents/toddy",
            ),
            slug=derived_task_slug(other_candidate.fingerprint),
            title="Other Active Goal Review",
            summary="Other Active Goal Review",
            goal_derivation=GoalDerivationReceipt(
                planner_version="goal-execution-v1",
                fingerprint=other_candidate.fingerprint,
                action_kind=other_candidate.action_kind,
                authority_class="auto_eligible",
                goal_slug=other_candidate.goal_slug,
                project_slug=other_candidate.project_slug,
                expected_evidence=other_candidate.expected_evidence,
            ),
        )

        class Adapter(self.Adapter):
            def read_goal_execution_snapshot(self, route_health):
                self.calls.append(("snapshot", dict(route_health)))
                return snapshot(
                    goals=(goal(), goal(slug=OTHER_GOAL, title="Faith")),
                    agents=(agent(), other_agent),
                    projects=(project(goals=(GOAL,)),),
                    tasks=(existing,),
                    route_health={**dict(route_health), "agents/toddy": True},
                )

        result = GoalExecutionEngine(
            adapter=Adapter(tasks=(existing,)),
            bridge=self.Bridge(),
            mode="shadow",
            canary_goal_slug=GOAL,
        ).run_once(NOW)

        decisions = {
            decision["goal_slug"]: decision["reason"]
            for decision in result.to_dict()["decisions"]
        }
        self.assertEqual(decisions[OTHER_GOAL], "handoff_missing")

    def test_shadow_mode_reports_existing_terminal_handoff_attention(self) -> None:
        adapter = self.Adapter()
        bridge = self.Bridge()
        canary = self.engine(adapter, bridge).run_once(NOW)
        bridge.latest_status = "suppressed"
        shadow = GoalExecutionEngine(
            adapter=adapter,
            bridge=bridge,
            mode="shadow",
            canary_goal_slug=GOAL,
        ).run_once(NOW)

        self.assertEqual(shadow.task_slug, canary.task_slug)
        self.assertEqual(shadow.public_reason, "handoff_needs_repair")
        self.assertEqual(shadow.handoff_status, "suppressed")
        self.assertEqual(
            shadow.to_dict()["decisions"][0]["reason"],
            "handoff_needs_repair",
        )

    def test_activation_partial_write_returns_attention_without_success(self) -> None:
        adapter = self.Adapter(
            activation_error=PartialMutationError(
                "tasks/goal-canary",
                "status readback failed",
            )
        )

        result = self.engine(adapter, self.Bridge()).run_once(NOW)

        self.assertEqual(result.public_reason, "system_repair_required")
        self.assertTrue(result.task_slug.startswith("tasks/"))
        self.assertIsNone(result.handoff_id)

    def test_dispatch_failure_keeps_verified_active_task_and_reports_recovery(self) -> None:
        adapter = self.Adapter()

        result = self.engine(adapter, self.Bridge(status="dead_letter")).run_once(NOW)

        self.assertEqual(adapter.tasks[0].status, "active")
        self.assertEqual(result.task_status, "active")
        self.assertEqual(result.public_reason, "handoff_needs_repair")
        self.assertEqual(result.handoff_status, "dead_letter")

    def test_route_health_accepts_approved_codex_openclaw_route_pair(self) -> None:
        bridge = self.Bridge()
        bridge.dispatcher.registrations = (
            AgentRegistration(
                registration_id="timmy-codex",
                agent_slug=AGENT,
                route="hosts/timmy",
                verified=True,
            ),
            AgentRegistration(
                registration_id="timmy-openclaw",
                agent_slug="agents/timmy-oc",
                route="hosts/timmy",
                verified=True,
            ),
        )

        health = self.engine(self.Adapter(), bridge).route_health()

        self.assertTrue(health[AGENT])
        self.assertTrue(health["agents/timmy-oc"])

    def test_route_health_rejects_duplicate_registration_for_one_agent(self) -> None:
        bridge = self.Bridge()
        bridge.dispatcher.registrations = (
            AgentRegistration(
                registration_id="timmy-one",
                agent_slug=AGENT,
                route="hosts/shared",
                verified=True,
            ),
            AgentRegistration(
                registration_id="timmy-two",
                agent_slug=AGENT,
                route="hosts/timmy-two",
                verified=True,
            ),
            AgentRegistration(
                registration_id="tammy-shared",
                agent_slug="agents/tammy",
                route="hosts/shared",
                verified=True,
            ),
        )

        health = self.engine(self.Adapter(), bridge).route_health()

        self.assertFalse(health[AGENT])
        self.assertTrue(health["agents/tammy"])

    def test_shadow_mode_returns_plan_without_mutation(self) -> None:
        adapter = self.Adapter()
        engine = GoalExecutionEngine(
            adapter=adapter,
            bridge=self.Bridge(),
            mode="shadow",
        )

        result = engine.run_once(NOW)

        self.assertEqual(result.public_reason, "shadow")
        self.assertEqual([name for name, _value in adapter.calls], ["snapshot"])

    def test_off_mode_performs_no_canonical_read_or_write(self) -> None:
        adapter = self.Adapter()
        engine = GoalExecutionEngine(
            adapter=adapter,
            bridge=self.Bridge(),
            mode="off",
        )

        result = engine.run_once(NOW)

        self.assertEqual(result.public_reason, "off")
        self.assertEqual(adapter.calls, [])


class GoalExecutionSchedulerTests(unittest.TestCase):
    class Engine:
        def __init__(self, *, error: Exception | None = None) -> None:
            self.error = error
            self.run_count = 0
            self.started = threading.Event()
            self.mode = "shadow"

        def run_once(self, now):
            self.run_count += 1
            self.started.set()
            if self.error is not None:
                raise self.error
            return SimpleNamespace(
                to_dict=lambda: {
                    "mode": "shadow",
                    "ran_at": now.isoformat(),
                    "public_reason": "shadow",
                }
            )

    def test_event_burst_coalesces_and_shutdown_joins_one_worker(self) -> None:
        engine = self.Engine()
        scheduler = GoalExecutionScheduler(engine)

        scheduler.start()
        self.assertTrue(engine.started.wait(timeout=1))
        for _index in range(25):
            scheduler.wake("fixture burst")
        time.sleep(0.05)
        scheduler.stop()

        self.assertEqual(engine.run_count, 1)
        self.assertFalse(scheduler.is_running)
        self.assertGreaterEqual(scheduler.minimum_interval_seconds, 30)
        self.assertLessEqual(scheduler.reconcile_interval_seconds, 1800)

    def test_exception_is_reported_without_zero_delay_retry(self) -> None:
        engine = self.Engine(error=RuntimeError("fixture failure"))
        scheduler = GoalExecutionScheduler(engine)

        scheduler.start()
        self.assertTrue(engine.started.wait(timeout=1))
        time.sleep(0.05)
        status = scheduler.status()
        scheduler.stop()

        self.assertEqual(engine.run_count, 1)
        self.assertEqual(status["last_error"], "RuntimeError")
        self.assertIsNone(status["last_run"])
        self.assertGreaterEqual(status["next_run_in_seconds"], 29)


if __name__ == "__main__":
    unittest.main()
