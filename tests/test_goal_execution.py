import unittest
import threading
import time
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from gtasks.domain import (
    AgentProfile,
    Goal,
    GoalDerivationReceipt,
    Project,
    TodoItem,
    new_task,
)
from gtasks.goal_execution import (
    GoalExecutionDecision,
    GoalExecutionEngine,
    GoalExecutionPlanner,
    GoalExecutionScheduler,
    GoalExecutionSnapshot,
    _goal_execution_summary,
    derived_task_slug,
)
from gtasks.gbrain import PartialMutationError, StatusMutationReceipt, TaskEditReceipt
from gtasks.handoff import TaskHandoff
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

    def test_existing_goal_task_waiting_for_tony_is_blocked_not_missing_next_action(self) -> None:
        waiting = replace(
            agent_task(
                slug_identity="existing-goal-work-waiting-for-tony",
                goal_slug=GOAL,
                status="blocked",
            ),
            next_action="Which scope should the Agent use next?",
            blockers=("people/tony-guan",),
            handoff=TaskHandoff(
                state="waiting_for_input",
                question_todo="todos/question-round-1",
                waiting_on="people/tony-guan",
                resume_owner=AGENT,
                resume_action="Resume after Tony chooses the scope.",
                requested_at=NOW,
                answered_at=None,
                acknowledged_at=None,
                round=1,
            ),
        )

        plan = GoalExecutionPlanner().plan(snapshot(tasks=(waiting,)))

        self.assertEqual(plan.decisions[0].reason, "waiting_for_tony")
        self.assertEqual(plan.decisions[0].existing_task_slug, waiting.slug)

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
    def test_goal_execution_private_credential_questions_do_not_get_synthetic_answer_templates(self) -> None:
        rendered = _goal_execution_summary(
            (
                GoalExecutionDecision(
                    GOAL,
                    "waiting_for_tony",
                    existing_task_slug="tasks/family",
                ),
            ),
            "waiting_for_tony",
            blocking_questions=(
                {
                    "goal_slug": GOAL,
                    "task_slug": "tasks/private-token",
                    "todo_slug": "todos/private-token",
                    "todo_updated_at": NOW.isoformat(),
                    "agent_slug": AGENT,
                    "question": "Please provide the Tammy artifact publisher token for this fixed Codex worker.",
                    "detail": "This requires Tony's private credential input.",
                },
            ),
        )

        self.assertEqual(
            rendered["action_queue"],
            [
                {
                    "owner": "tony",
                    "kind": "answer_question",
                    "label": "Answer Agent question",
                    "goal_slug": GOAL,
                    "task_slug": "tasks/private-token",
                    "todo_slug": "todos/private-token",
                    "todo_updated_at": NOW.isoformat(),
                    "agent_slug": AGENT,
                    "summary": "Please provide the Tammy artifact publisher token for this fixed Codex worker.",
                    "detail": "This requires Tony's private credential input.",
                    "private_input_required": True,
                }
            ],
        )

    def test_goal_execution_next_action_names_private_input_blockers(self) -> None:
        rendered = _goal_execution_summary(
            (
                GoalExecutionDecision(
                    GOAL,
                    "waiting_for_tony",
                    existing_task_slug="tasks/family",
                ),
                GoalExecutionDecision(
                    "goals/d837ac94-36f5-4735-93bb-d84c69b45435",
                    "owner_missing",
                ),
            ),
            "waiting_for_tony",
            blocking_questions=(
                {
                    "goal_slug": GOAL,
                    "task_slug": "tasks/family",
                    "todo_slug": "todos/family",
                    "todo_updated_at": NOW.isoformat(),
                    "agent_slug": "agents/toddy",
                    "question": "Which family-care scope should Toddy use next?",
                    "detail": "Choose the scope and first bounded action.",
                },
                {
                    "goal_slug": OTHER_GOAL,
                    "task_slug": "tasks/private-token",
                    "todo_slug": "todos/private-token",
                    "todo_updated_at": NOW.isoformat(),
                    "agent_slug": "agents/tammy",
                    "question": "Please provide the Tammy artifact publisher token for this fixed Codex worker.",
                    "detail": "This requires Tony's private credential input.",
                },
            ),
            missing_owners=(
                {
                    "goal_slug": "goals/d837ac94-36f5-4735-93bb-d84c69b45435",
                    "goal_title": "Entrepreneurship",
                    "required_relationship": "default_agent_for",
                    "candidate_owners": [
                        {
                            "agent_slug": AGENT,
                            "agent_name": "Timmy",
                            "default_goal_count": 1,
                            "recommended": True,
                            "recommendation": "recommended: lowest verified Codex Goal load",
                        }
                    ],
                },
            ),
        )

        self.assertIn(
            "Answer the Toddy question for Which family-care scope should Toddy use next?",
            rendered["next_action"],
        )
        self.assertIn(
            "provide private input for the Tammy question",
            rendered["next_action"],
        )
        self.assertIn(
            "Please provide the Tammy artifact publisher token for this fixed Codex worker",
            rendered["next_action"],
        )
        self.assertNotIn(". and assign", rendered["next_action"])
        self.assertIn("; assign", rendered["next_action"])

    def test_goal_execution_groups_duplicate_private_input_blockers_before_owner_actions(self) -> None:
        rendered = _goal_execution_summary(
            (
                GoalExecutionDecision(
                    GOAL,
                    "waiting_for_tony",
                    existing_task_slug="tasks/family",
                ),
                GoalExecutionDecision(
                    OTHER_GOAL,
                    "waiting_for_tony",
                    existing_task_slug="tasks/faith-token",
                ),
                GoalExecutionDecision(
                    "goals/840b3122-b299-5991-96be-30364c7f2e12",
                    "waiting_for_tony",
                    existing_task_slug="tasks/finance-token",
                ),
                GoalExecutionDecision(
                    "goals/d837ac94-36f5-4735-93bb-d84c69b45435",
                    "owner_missing",
                ),
            ),
            "waiting_for_tony",
            blocking_questions=(
                {
                    "goal_slug": GOAL,
                    "task_slug": "tasks/family",
                    "todo_slug": "todos/family",
                    "todo_updated_at": NOW.isoformat(),
                    "agent_slug": "agents/toddy",
                    "question": "Which family-care scope should Toddy use next?",
                    "detail": "Choose the scope and first bounded action.",
                },
                {
                    "goal_slug": OTHER_GOAL,
                    "task_slug": "tasks/faith-token",
                    "todo_slug": "todos/faith-token",
                    "todo_updated_at": NOW.isoformat(),
                    "agent_slug": "agents/tammy",
                    "question": "Please provide the Tammy artifact publisher token for this fixed Codex worker.",
                    "detail": "This requires Tony's private credential input.",
                },
                {
                    "goal_slug": "goals/840b3122-b299-5991-96be-30364c7f2e12",
                    "task_slug": "tasks/finance-token",
                    "todo_slug": "todos/finance-token",
                    "todo_updated_at": NOW.isoformat(),
                    "agent_slug": "agents/tammy",
                    "question": "Please provide the Tammy artifact publisher token for this fixed Codex worker.",
                    "detail": "This requires Tony's private credential input for Finance.",
                },
            ),
            missing_owners=(
                {
                    "goal_slug": "goals/d837ac94-36f5-4735-93bb-d84c69b45435",
                    "goal_title": "Entrepreneurship",
                    "required_relationship": "default_agent_for",
                    "candidate_owners": [
                        {
                            "agent_slug": AGENT,
                            "agent_name": "Timmy",
                            "default_goal_count": 1,
                            "recommended": True,
                            "recommendation": "recommended: lowest verified Codex Goal load",
                        }
                    ],
                },
            ),
        )

        self.assertEqual(
            [item["kind"] for item in rendered["action_queue"]],
            ["answer_question", "answer_question", "assign_goal_owner"],
        )
        private_action = rendered["action_queue"][1]
        self.assertTrue(private_action["private_input_required"])
        self.assertEqual(private_action["blocked_goal_count"], 2)
        self.assertEqual(
            [item["task_slug"] for item in private_action["related_questions"]],
            ["tasks/faith-token", "tasks/finance-token"],
        )
        self.assertIn("2 Tammy private-input blockers", rendered["next_action"])
        self.assertNotIn(".; assign", rendered["next_action"])
        self.assertIn("; assign Entrepreneurship", rendered["next_action"])

    def test_goal_execution_routes_artifact_identity_mismatch_to_system_repair_action(self) -> None:
        rendered = _goal_execution_summary(
            (
                GoalExecutionDecision(
                    GOAL,
                    "waiting_for_tony",
                    existing_task_slug="tasks/family",
                ),
                GoalExecutionDecision(
                    OTHER_GOAL,
                    "waiting_for_tony",
                    existing_task_slug="tasks/faith-token",
                ),
                GoalExecutionDecision(
                    "goals/840b3122-b299-5991-96be-30364c7f2e12",
                    "waiting_for_tony",
                    existing_task_slug="tasks/finance-token",
                ),
                GoalExecutionDecision(
                    "goals/d837ac94-36f5-4735-93bb-d84c69b45435",
                    "owner_missing",
                ),
            ),
            "waiting_for_tony",
            blocking_questions=(
                {
                    "goal_slug": GOAL,
                    "task_slug": "tasks/family",
                    "todo_slug": "todos/family",
                    "todo_updated_at": NOW.isoformat(),
                    "agent_slug": "agents/toddy",
                    "question": "Which family-care scope should Toddy use next?",
                    "detail": "Choose the scope and first bounded action.",
                },
                {
                    "goal_slug": OTHER_GOAL,
                    "task_slug": "tasks/faith-token",
                    "todo_slug": "todos/faith-token",
                    "todo_updated_at": NOW.isoformat(),
                    "agent_slug": "agents/tammy",
                    "question": "Please provide the Tammy artifact publisher token for this fixed Codex worker.",
                    "detail": "Artifact publication failed with `artifact_identity_mismatch`; provision dashboard Artifact publisher credentials.",
                },
                {
                    "goal_slug": "goals/840b3122-b299-5991-96be-30364c7f2e12",
                    "task_slug": "tasks/finance-token",
                    "todo_slug": "todos/finance-token",
                    "todo_updated_at": NOW.isoformat(),
                    "agent_slug": "agents/tammy",
                    "question": "Please provide the Tammy artifact publisher token for this fixed Codex worker.",
                    "detail": "Finance publication failed with `artifact_identity_mismatch`; provision dashboard Artifact publisher credentials.",
                },
            ),
            missing_owners=(
                {
                    "goal_slug": "goals/d837ac94-36f5-4735-93bb-d84c69b45435",
                    "goal_title": "Entrepreneurship",
                    "required_relationship": "default_agent_for",
                    "candidate_owners": [
                        {
                            "agent_slug": AGENT,
                            "agent_name": "Timmy",
                            "default_goal_count": 1,
                            "recommended": True,
                            "recommendation": "recommended: lowest verified Codex Goal load",
                        }
                    ],
                },
            ),
        )

        self.assertEqual(
            [item["kind"] for item in rendered["action_queue"]],
            [
                "answer_question",
                "repair_artifact_publisher_identity",
                "assign_goal_owner",
            ],
        )
        repair_action = rendered["action_queue"][1]
        self.assertEqual(repair_action["owner"], "system")
        self.assertEqual(repair_action["agent_slug"], "agents/tammy")
        self.assertEqual(repair_action["blocked_goal_count"], 2)
        self.assertNotIn("private_input_required", repair_action)
        self.assertNotIn("provide private input", rendered["next_action"])
        self.assertIn("repair Tammy Artifact publisher identity for 2 blocked Goals", rendered["next_action"])
        self.assertIn("; assign Entrepreneurship", rendered["next_action"])

    class Adapter:
        def __init__(
            self,
            *,
            tasks=(),
            activation_error: Exception | None = None,
            artifact_tasks: tuple[str, ...] = (),
            snapshot_value: GoalExecutionSnapshot | None = None,
        ):
            self.tasks = list(tasks)
            self.activation_error = activation_error
            self.artifact_tasks = set(artifact_tasks)
            self.snapshot_value = snapshot_value
            self.calls: list[tuple[str, object]] = []

        def read_goal_execution_snapshot(self, route_health):
            self.calls.append(("snapshot", dict(route_health)))
            if self.snapshot_value is not None:
                return self.snapshot_value
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

        def list_agent_artifacts(self, *, task: str, limit: int = 1):
            self.calls.append(("artifacts", task))
            artifacts = (
                (
                    SimpleNamespace(
                        slug="artifacts/11111111-1111-4111-8111-111111111111",
                        produced_for=task,
                    ),
                )
                if task in self.artifact_tasks
                else ()
            )
            return SimpleNamespace(artifacts=artifacts)

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
            self.latest_delivery_state: dict[str, object] | None = None
            self.recovery_status: str = "retrying"
            self.calls: list[tuple[dict, dict, dict]] = []
            self.recovery_calls: list[tuple[str, str]] = []

        def after_verified_mutation(self, before, after, receipt, now):
            self.calls.append((before, after, receipt))
            return SimpleNamespace(
                handoff_id="handoffs/goal-execution-canary",
                status=self.status,
                reason="delivery failed" if self.status == "dead_letter" else "queued",
            )

        def latest_task_handoff_status(self, task_slug):
            return self.latest_status

        def latest_task_handoff_delivery_state(self, task_slug):
            return self.latest_delivery_state

        def retry_task_handoff_recovery(self, task_slug, *, mutation_id, summary, now):
            self.recovery_calls.append((task_slug, mutation_id))
            return SimpleNamespace(
                handoff_id="handoffs/goal-execution-canary",
                status=self.recovery_status,
                reason="system_dependency_recovered",
            )

    def engine(self, adapter=None, bridge=None):
        return GoalExecutionEngine(
            adapter=adapter or self.Adapter(),
            bridge=bridge or self.Bridge(),
            mode="canary",
            canary_goal_slug=GOAL,
        )

    def test_run_summary_counts_actionable_goal_states_for_readers(self) -> None:
        active_task = replace(
            agent_task(slug_identity="active", goal_slug=GOAL, status="active"),
            next_action="Publish the bounded Goal progress brief.",
        )
        waiting_base = agent_task(
            slug_identity="waiting",
            goal_slug=OTHER_GOAL,
            status="blocked",
        )
        waiting_task = replace(
            waiting_base,
            handoff=TaskHandoff(
                state="waiting_for_input",
                waiting_on="people/tony-guan",
                question_todo="todos/question",
                resume_owner=AGENT,
                resume_action="Use Tony's answer.",
                requested_at=NOW,
                answered_at=None,
                acknowledged_at=None,
                round=1,
            ),
            todos=(
                TodoItem(
                    slug="todos/question",
                    parent_task=waiting_base.slug,
                    text="Which family-care scope should Toddy use next?",
                    detail="Choose the scope and first bounded action.",
                    status="not_done",
                    kind="question",
                    created_at=NOW,
                    updated_at=NOW,
                    creator=AGENT,
                    source="agent",
                ),
            ),
        )
        run = GoalExecutionEngine(
            adapter=self.Adapter(
                snapshot_value=snapshot(
                    goals=(
                        goal(GOAL),
                        goal(OTHER_GOAL, title="Faith: daily review"),
                        goal(
                            "goals/d837ac94-36f5-4735-93bb-d84c69b45435",
                            title="Entrepreneurship",
                        ),
                    ),
                    agents=(agent(goals=(GOAL, OTHER_GOAL)),),
                    tasks=(
                        active_task,
                        waiting_task,
                    ),
                    route_health={AGENT: True},
                )
            ),
            bridge=self.Bridge(),
            mode="shadow",
            canary_goal_slug=GOAL,
        ).run_once(NOW)

        rendered = run.to_dict()

        self.assertEqual(rendered["summary"]["total_goals"], 3)
        self.assertEqual(rendered["summary"]["needs_attention"], 2)
        self.assertEqual(rendered["summary"]["waiting_for_tony"], 1)
        self.assertEqual(rendered["summary"]["owner_missing"], 1)
        self.assertEqual(
            rendered["summary"]["missing_owners"],
            [
                {
                    "goal_slug": "goals/d837ac94-36f5-4735-93bb-d84c69b45435",
                    "goal_title": "Entrepreneurship",
                    "required_relationship": "default_agent_for",
                    "message": "Assign exactly one Codex Agent with a verified default_agent_for link before Mission Control can derive work from this Goal.",
                    "candidate_owners": [
                        {
                            "agent_slug": AGENT,
                            "agent_name": "Timmy",
                            "default_goal_count": 2,
                            "recommended": True,
                            "recommendation": "recommended: lowest verified Codex Goal load",
                        }
                    ],
                }
            ],
        )
        self.assertEqual(
            rendered["summary"]["action_queue"],
            [
                {
                    "owner": "tony",
                    "kind": "answer_question",
                    "label": "Answer Agent question",
                    "goal_slug": OTHER_GOAL,
                    "task_slug": waiting_task.slug,
                    "todo_slug": "todos/question",
                    "todo_updated_at": NOW.isoformat(),
                    "agent_slug": AGENT,
                    "summary": "Which family-care scope should Toddy use next?",
                    "detail": "Choose the scope and first bounded action.",
                    "answer_template": (
                        "Scope categories: accepted\n"
                        "Desired outcomes: accepted\n"
                        "Constraints: accepted\n"
                        "First action: approved\n"
                        "Notes: Keep the work bounded to the stated scope, outcomes, constraints, and first action."
                    ),
                },
                {
                    "owner": "tony",
                    "kind": "assign_goal_owner",
                    "label": "Assign Goal owner",
                    "goal_slug": "goals/d837ac94-36f5-4735-93bb-d84c69b45435",
                    "agent_slug": None,
                    "candidate_owners": [
                        {
                            "agent_slug": AGENT,
                            "agent_name": "Timmy",
                            "default_goal_count": 2,
                            "recommended": True,
                            "recommendation": "recommended: lowest verified Codex Goal load",
                        }
                    ],
                    "summary": "Entrepreneurship — add default_agent_for",
                },
            ],
        )
        self.assertEqual(
            rendered["summary"]["blocking_questions"],
            [
                {
                    "goal_slug": OTHER_GOAL,
                    "task_slug": waiting_task.slug,
                    "todo_slug": "todos/question",
                    "todo_updated_at": NOW.isoformat(),
                    "agent_slug": AGENT,
                    "question": "Which family-care scope should Toddy use next?",
                    "detail": "Choose the scope and first bounded action.",
                }
            ],
        )
        self.assertEqual(
            rendered["summary"]["next_action"],
            "Answer the Timmy question for Which family-care scope should Toddy use next?; assign Entrepreneurship to Timmy (recommended: lowest verified Codex Goal load); executing or delivered Agent work can continue.",
        )

    def test_auto_canary_selects_next_eligible_goal_when_fixed_goal_completed(self) -> None:
        primary_plan = GoalExecutionPlanner(cycle_day=NOW.date()).plan(
            snapshot(projects=(project(),))
        )
        primary_candidate = primary_plan.decisions[0].candidate
        self.assertIsNotNone(primary_candidate)
        completed_primary = replace(
            agent_task(
                slug_identity="completed-primary-goal",
                goal_slug=GOAL,
                status="completed",
            ),
            slug=derived_task_slug(primary_candidate.fingerprint),
            title="Completed primary Goal Review",
            summary="Completed primary Goal Review",
            goal_derivation=GoalDerivationReceipt(
                planner_version="goal-execution-v1",
                fingerprint=primary_candidate.fingerprint,
                action_kind=primary_candidate.action_kind,
                authority_class="auto_eligible",
                goal_slug=primary_candidate.goal_slug,
                project_slug=primary_candidate.project_slug,
                expected_evidence=primary_candidate.expected_evidence,
            ),
        )
        adapter = self.Adapter(
            tasks=(completed_primary,),
            snapshot_value=snapshot(
                goals=(goal(), goal(OTHER_GOAL, title="Faith: daily review")),
                projects=(
                    project(),
                    project(
                        "projects/9df00c10-0000-4000-8000-000000000002",
                        goals=(OTHER_GOAL,),
                    ),
                ),
                agents=(agent(goals=(GOAL, OTHER_GOAL)),),
                tasks=(completed_primary,),
                route_health={AGENT: True},
            ),
        )

        result = GoalExecutionEngine(
            adapter=adapter,
            bridge=self.Bridge(),
            mode="canary",
            canary_goal_slug="auto",
        ).run_once(NOW)

        self.assertEqual(result.public_reason, "activated")
        self.assertEqual(result.agent_slug, AGENT)
        self.assertNotEqual(result.task_slug, completed_primary.slug)
        self.assertIn(("status", (result.task_slug, "active")), adapter.calls)
        selected = [
            decision
            for decision in result.decisions
            if decision.goal_slug == OTHER_GOAL
        ]
        self.assertEqual(selected[0].reason, "auto_eligible")

    def test_auto_canary_surfaces_active_handoff_before_unrelated_waiting_goal(self) -> None:
        secondary_plan = GoalExecutionPlanner(cycle_day=NOW.date()).plan(
            snapshot(
                goals=(goal(OTHER_GOAL, title="Faith: daily review"),),
                projects=(
                    project(
                        "projects/9df00c10-0000-4000-8000-000000000002",
                        goals=(OTHER_GOAL,),
                    ),
                ),
                agents=(agent(goals=(OTHER_GOAL)),),
            )
        )
        secondary_candidate = secondary_plan.decisions[0].candidate
        self.assertIsNotNone(secondary_candidate)
        waiting = replace(
            agent_task(
                slug_identity="waiting-primary-goal",
                goal_slug=GOAL,
                status="blocked",
            ),
            next_action="Which scope should the Agent use next?",
            blockers=("people/tony-guan",),
            handoff=TaskHandoff(
                state="waiting_for_input",
                question_todo="todos/question-round-1",
                waiting_on="people/tony-guan",
                resume_owner=AGENT,
                resume_action="Resume after Tony chooses the scope.",
                requested_at=NOW,
                answered_at=None,
                acknowledged_at=None,
                round=1,
            ),
        )
        executing = replace(
            agent_task(
                slug_identity="executing-secondary-goal",
                goal_slug=OTHER_GOAL,
                status="active",
            ),
            slug=derived_task_slug(secondary_candidate.fingerprint),
            next_action="Publish the bounded Goal progress brief.",
            goal_derivation=GoalDerivationReceipt(
                planner_version="goal-execution-v1",
                fingerprint=secondary_candidate.fingerprint,
                action_kind=secondary_candidate.action_kind,
                authority_class="auto_eligible",
                goal_slug=secondary_candidate.goal_slug,
                project_slug=secondary_candidate.project_slug,
                expected_evidence=secondary_candidate.expected_evidence,
            ),
        )
        bridge = self.Bridge()
        bridge.latest_status = "actively_executing"
        result = GoalExecutionEngine(
            adapter=self.Adapter(
                snapshot_value=snapshot(
                    goals=(goal(), goal(OTHER_GOAL, title="Faith: daily review")),
                    projects=(
                        project(),
                        project(
                            "projects/9df00c10-0000-4000-8000-000000000002",
                            goals=(OTHER_GOAL,),
                        ),
                    ),
                    agents=(agent(goals=(GOAL, OTHER_GOAL)),),
                    tasks=(waiting, executing),
                    route_health={AGENT: True},
                )
            ),
            bridge=bridge,
            mode="canary",
            canary_goal_slug="auto",
        ).run_once(NOW)

        self.assertEqual(result.public_reason, "actively_executing")
        self.assertEqual(result.task_slug, executing.slug)
        self.assertEqual(result.handoff_status, "actively_executing")

    def test_auto_canary_surfaces_waiting_goal_before_completed_history(self) -> None:
        primary_plan = GoalExecutionPlanner(cycle_day=NOW.date()).plan(
            snapshot(projects=(project(),))
        )
        primary_candidate = primary_plan.decisions[0].candidate
        secondary_plan = GoalExecutionPlanner(cycle_day=NOW.date()).plan(
            snapshot(
                goals=(goal(OTHER_GOAL, title="Faith: daily review"),),
                projects=(
                    project(
                        "projects/9df00c10-0000-4000-8000-000000000002",
                        goals=(OTHER_GOAL,),
                    ),
                ),
                agents=(agent(goals=(OTHER_GOAL)),),
            )
        )
        secondary_candidate = secondary_plan.decisions[0].candidate
        self.assertIsNotNone(primary_candidate)
        self.assertIsNotNone(secondary_candidate)
        waiting = replace(
            agent_task(
                slug_identity="waiting-primary-goal",
                goal_slug=GOAL,
                status="blocked",
            ),
            next_action="Which scope should the Agent use next?",
            blockers=("people/tony-guan",),
            handoff=TaskHandoff(
                state="waiting_for_input",
                question_todo="todos/question-round-1",
                waiting_on="people/tony-guan",
                resume_owner=AGENT,
                resume_action="Resume after Tony chooses the scope.",
                requested_at=NOW,
                answered_at=None,
                acknowledged_at=None,
                round=1,
            ),
        )
        older_completed = replace(
            agent_task(
                slug_identity="older-completed-goal",
                goal_slug=GOAL,
                status="completed",
            ),
            slug=derived_task_slug(primary_candidate.fingerprint),
            completed_at=NOW - timedelta(hours=1),
            goal_derivation=GoalDerivationReceipt(
                planner_version="goal-execution-v1",
                fingerprint=primary_candidate.fingerprint,
                action_kind=primary_candidate.action_kind,
                authority_class="auto_eligible",
                goal_slug=primary_candidate.goal_slug,
                project_slug=primary_candidate.project_slug,
                expected_evidence=primary_candidate.expected_evidence,
            ),
        )
        latest_completed = replace(
            agent_task(
                slug_identity="latest-completed-goal",
                goal_slug=OTHER_GOAL,
                status="completed",
            ),
            slug=derived_task_slug(secondary_candidate.fingerprint),
            completed_at=NOW,
            goal_derivation=GoalDerivationReceipt(
                planner_version="goal-execution-v1",
                fingerprint=secondary_candidate.fingerprint,
                action_kind=secondary_candidate.action_kind,
                authority_class="auto_eligible",
                goal_slug=secondary_candidate.goal_slug,
                project_slug=secondary_candidate.project_slug,
                expected_evidence=secondary_candidate.expected_evidence,
            ),
        )

        result = GoalExecutionEngine(
            adapter=self.Adapter(
                snapshot_value=snapshot(
                    goals=(goal(), goal(OTHER_GOAL, title="Faith: daily review")),
                    projects=(
                        project(),
                        project(
                            "projects/9df00c10-0000-4000-8000-000000000002",
                            goals=(OTHER_GOAL,),
                        ),
                    ),
                    agents=(agent(goals=(GOAL, OTHER_GOAL)),),
                    tasks=(waiting, older_completed, latest_completed),
                    route_health={AGENT: True},
                )
            ),
            bridge=self.Bridge(),
            mode="canary",
            canary_goal_slug="auto",
        ).run_once(NOW)

        self.assertEqual(result.public_reason, "waiting_for_tony")
        self.assertEqual(result.task_slug, waiting.slug)

    def test_auto_canary_recovers_repairable_handoff_before_waiting_goal(self) -> None:
        primary_plan = GoalExecutionPlanner(cycle_day=NOW.date()).plan(
            snapshot(projects=(project(),))
        )
        primary_candidate = primary_plan.decisions[0].candidate
        secondary_plan = GoalExecutionPlanner(cycle_day=NOW.date()).plan(
            snapshot(
                goals=(goal(OTHER_GOAL, title="Faith: daily review"),),
                projects=(
                    project(
                        "projects/9df00c10-0000-4000-8000-000000000002",
                        goals=(OTHER_GOAL,),
                    ),
                ),
                agents=(agent(goals=(OTHER_GOAL)),),
            )
        )
        secondary_candidate = secondary_plan.decisions[0].candidate
        self.assertIsNotNone(primary_candidate)
        self.assertIsNotNone(secondary_candidate)
        waiting = replace(
            agent_task(
                slug_identity="waiting-primary-goal",
                goal_slug=GOAL,
                status="blocked",
            ),
            next_action="Which scope should the Agent use next?",
            blockers=("people/tony-guan",),
            handoff=TaskHandoff(
                state="waiting_for_input",
                question_todo="todos/question-round-1",
                waiting_on="people/tony-guan",
                resume_owner=AGENT,
                resume_action="Resume after Tony chooses the scope.",
                requested_at=NOW,
                answered_at=None,
                acknowledged_at=None,
                round=1,
            ),
        )
        repairable = replace(
            agent_task(
                slug_identity="repairable-secondary-goal",
                goal_slug=OTHER_GOAL,
                status="active",
            ),
            slug=derived_task_slug(secondary_candidate.fingerprint),
            next_action="Publish the bounded Goal progress brief.",
            goal_derivation=GoalDerivationReceipt(
                planner_version="goal-execution-v1",
                fingerprint=secondary_candidate.fingerprint,
                action_kind=secondary_candidate.action_kind,
                authority_class="auto_eligible",
                goal_slug=secondary_candidate.goal_slug,
                project_slug=secondary_candidate.project_slug,
                expected_evidence=secondary_candidate.expected_evidence,
            ),
        )
        bridge = self.Bridge()
        bridge.latest_status = "suppressed"
        bridge.latest_delivery_state = {
            "status": "suppressed",
            "terminal_state": "checkpointed",
            "claimed_at": (NOW - timedelta(minutes=10)).isoformat(),
        }

        result = GoalExecutionEngine(
            adapter=self.Adapter(
                snapshot_value=snapshot(
                    goals=(goal(), goal(OTHER_GOAL, title="Faith: daily review")),
                    projects=(
                        project(),
                        project(
                            "projects/9df00c10-0000-4000-8000-000000000002",
                            goals=(OTHER_GOAL,),
                        ),
                    ),
                    agents=(agent(goals=(GOAL, OTHER_GOAL)),),
                    tasks=(waiting, repairable),
                    route_health={AGENT: True},
                )
            ),
            bridge=bridge,
            mode="canary",
            canary_goal_slug="auto",
        ).run_once(NOW)

        self.assertEqual(result.public_reason, "activated")
        self.assertEqual(result.task_slug, repairable.slug)
        self.assertEqual(result.handoff_status, "retrying")
        self.assertEqual(bridge.recovery_calls, [(repairable.slug, bridge.recovery_calls[0][1])])

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
        first_plan = GoalExecutionPlanner(cycle_day=NOW.date()).plan(snapshot())
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

    def test_waiting_for_tony_goal_task_reports_title_status_and_agent(self) -> None:
        waiting = replace(
            agent_task(
                slug_identity="waiting-goal-work",
                goal_slug=GOAL,
                status="blocked",
            ),
            title="Waiting Goal Review",
            summary="Waiting Goal Review",
            blockers=("people/tony-guan",),
            handoff=TaskHandoff(
                state="waiting_for_input",
                question_todo="todos/question-round-1",
                waiting_on="people/tony-guan",
                resume_owner=AGENT,
                resume_action="Resume after Tony chooses the scope.",
                requested_at=NOW,
                answered_at=None,
                acknowledged_at=None,
                round=1,
            ),
        )
        adapter = self.Adapter(tasks=(waiting,))

        result = self.engine(adapter, self.Bridge()).run_once(NOW)

        self.assertEqual(result.public_reason, "waiting_for_tony")
        self.assertEqual(result.task_slug, waiting.slug)
        self.assertEqual(result.task_title, "Waiting Goal Review")
        self.assertEqual(result.task_status, "blocked")
        self.assertEqual(result.agent_slug, AGENT)

    def test_prior_cycle_completed_task_does_not_permanently_suppress_next_goal_review(self) -> None:
        previous_plan = GoalExecutionPlanner(cycle_day=date(2026, 8, 17)).plan(snapshot())
        previous_candidate = previous_plan.decisions[0].candidate
        self.assertIsNotNone(previous_candidate)
        completed_previous_cycle = replace(
            agent_task(
                slug_identity="previous-cycle-goal-work",
                goal_slug=GOAL,
                status="completed",
            ),
            slug=derived_task_slug(previous_candidate.fingerprint),
            title="Previous Weekly Goal Review",
            summary="Previous Weekly Goal Review",
            completed_at=NOW - timedelta(days=6),
            goal_derivation=GoalDerivationReceipt(
                planner_version="goal-execution-v1",
                fingerprint=previous_candidate.fingerprint,
                action_kind=previous_candidate.action_kind,
                authority_class="auto_eligible",
                goal_slug=previous_candidate.goal_slug,
                project_slug=previous_candidate.project_slug,
                expected_evidence=previous_candidate.expected_evidence,
            ),
        )
        adapter = self.Adapter(tasks=(completed_previous_cycle,))
        bridge = self.Bridge()

        result = GoalExecutionEngine(
            adapter=adapter,
            bridge=bridge,
            mode="canary",
            canary_goal_slug=GOAL,
            planner=GoalExecutionPlanner(cycle_day=date(2026, 8, 24)),
        ).run_once(NOW + timedelta(days=1))

        self.assertEqual(result.public_reason, "activated")
        self.assertNotEqual(result.task_slug, completed_previous_cycle.slug)
        self.assertIn(("status", (result.task_slug, "active")), adapter.calls)

    def test_canary_reconciles_completed_handoff_with_verified_artifact(self) -> None:
        first_plan = GoalExecutionPlanner().plan(snapshot())
        candidate = first_plan.decisions[0].candidate
        self.assertIsNotNone(candidate)
        active = replace(
            agent_task(
                slug_identity="completed-handoff-goal-work",
                goal_slug=GOAL,
                status="active",
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
        adapter = self.Adapter(tasks=(active,), artifact_tasks=(active.slug,))
        bridge = self.Bridge()
        bridge.latest_status = "completed"

        result = GoalExecutionEngine(
            adapter=adapter,
            bridge=bridge,
            mode="canary",
            canary_goal_slug=GOAL,
        ).run_once(NOW)

        self.assertEqual(result.public_reason, "completed_after_verified_handoff")
        self.assertEqual(result.task_slug, active.slug)
        self.assertEqual(result.task_status, "completed")
        self.assertIn(("artifacts", active.slug), adapter.calls)
        self.assertIn(("status", (active.slug, "completed")), adapter.calls)

    def test_canary_reconciles_checkpointed_suppressed_handoff_with_verified_artifact(self) -> None:
        first_plan = GoalExecutionPlanner(cycle_day=NOW.date()).plan(snapshot())
        candidate = first_plan.decisions[0].candidate
        self.assertIsNotNone(candidate)
        active = replace(
            agent_task(
                slug_identity="checkpointed-handoff-goal-work",
                goal_slug=GOAL,
                status="active",
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
        adapter = self.Adapter(tasks=(active,), artifact_tasks=(active.slug,))
        bridge = self.Bridge()
        bridge.latest_status = "suppressed"
        bridge.latest_delivery_state = {
            "status": "suppressed",
            "terminal_state": "checkpointed",
        }

        result = GoalExecutionEngine(
            adapter=adapter,
            bridge=bridge,
            mode="canary",
            canary_goal_slug=GOAL,
        ).run_once(NOW)

        self.assertEqual(result.public_reason, "completed_after_verified_handoff")
        self.assertEqual(result.task_slug, active.slug)
        self.assertEqual(result.task_status, "completed")
        self.assertIn(("artifacts", active.slug), adapter.calls)
        self.assertIn(("status", (active.slug, "completed")), adapter.calls)

    def test_canary_reconciles_suppressed_handoff_with_verified_artifact(self) -> None:
        first_plan = GoalExecutionPlanner(cycle_day=NOW.date()).plan(snapshot())
        candidate = first_plan.decisions[0].candidate
        self.assertIsNotNone(candidate)
        active = replace(
            agent_task(
                slug_identity="suppressed-handoff-goal-work",
                goal_slug=GOAL,
                status="active",
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
        adapter = self.Adapter(tasks=(active,), artifact_tasks=(active.slug,))
        bridge = self.Bridge()
        bridge.latest_status = "suppressed"

        result = GoalExecutionEngine(
            adapter=adapter,
            bridge=bridge,
            mode="canary",
            canary_goal_slug=GOAL,
        ).run_once(NOW)

        self.assertEqual(result.public_reason, "completed_after_verified_handoff")
        self.assertEqual(result.task_slug, active.slug)
        self.assertEqual(result.task_status, "completed")
        self.assertIn(("artifacts", active.slug), adapter.calls)
        self.assertIn(("status", (active.slug, "completed")), adapter.calls)

    def test_auto_canary_reconciles_artifact_backed_terminal_wip_before_in_flight_work(self) -> None:
        toddy = agent(
            slug="agents/toddy",
            goals=(OTHER_GOAL,),
        )
        repair_plan = GoalExecutionPlanner(cycle_day=NOW.date()).plan(
            snapshot(
                goals=(goal(slug=OTHER_GOAL, title="Family"), goal()),
                agents=(toddy, agent()),
                route_health={"agents/toddy": True, AGENT: True},
            )
        )
        repair_candidate = next(
            decision.candidate
            for decision in repair_plan.decisions
            if decision.goal_slug == GOAL
        )
        self.assertIsNotNone(repair_candidate)
        in_flight = replace(
            agent_task(
                slug_identity="active-family-work",
                goal_slug=OTHER_GOAL,
                status="active",
                owner="agents/toddy",
            ),
            next_action="Continue the already executing family work.",
        )
        waiting_base = agent_task(
            slug_identity="waiting-family-question-control",
            goal_slug="goals/waiting-question-control",
            status="blocked",
            owner="agents/toddy",
        )
        waiting = replace(
            waiting_base,
            blockers=("people/tony-guan",),
            handoff=TaskHandoff(
                state="waiting_for_input",
                question_todo="todos/family-question",
                waiting_on="people/tony-guan",
                resume_owner="agents/toddy",
                resume_action="Resume family work after Tony answers.",
                requested_at=NOW,
                answered_at=None,
                acknowledged_at=None,
                round=1,
            ),
        )
        repairable = replace(
            agent_task(
                slug_identity="suppressed-terminal-civic-work",
                goal_slug=GOAL,
                status="active",
            ),
            slug=derived_task_slug(repair_candidate.fingerprint),
            goal_derivation=GoalDerivationReceipt(
                planner_version="goal-execution-v1",
                fingerprint=repair_candidate.fingerprint,
                action_kind=repair_candidate.action_kind,
                authority_class="auto_eligible",
                goal_slug=repair_candidate.goal_slug,
                project_slug=repair_candidate.project_slug,
                expected_evidence=repair_candidate.expected_evidence,
            ),
        )

        class Adapter(self.Adapter):
            def read_goal_execution_snapshot(self, route_health):
                self.calls.append(("snapshot", dict(route_health)))
                return snapshot(
                    goals=(
                        goal(slug=OTHER_GOAL, title="Family"),
                        goal(),
                        goal(slug="goals/waiting-question-control", title="Waiting Control"),
                    ),
                    agents=(toddy, agent()),
                    tasks=(in_flight, repairable, waiting),
                    route_health={"agents/toddy": True, AGENT: True},
                )

        class Bridge(self.Bridge):
            def latest_task_handoff_status(self, task_slug):
                if task_slug == repairable.slug:
                    return "suppressed"
                if task_slug == in_flight.slug:
                    return "actively_executing"
                return None

        adapter = Adapter(tasks=(in_flight, repairable, waiting), artifact_tasks=(repairable.slug,))

        result = GoalExecutionEngine(
            adapter=adapter,
            bridge=Bridge(),
            mode="canary",
            canary_goal_slug="auto",
        ).run_once(NOW)

        self.assertEqual(result.public_reason, "completed_after_verified_handoff")
        self.assertEqual(result.task_slug, repairable.slug)
        self.assertEqual(result.task_status, "completed")
        self.assertIn(("status", (repairable.slug, "completed")), adapter.calls)
        decisions = {
            decision["goal_slug"]: decision["reason"]
            for decision in result.to_dict()["decisions"]
        }
        self.assertEqual(decisions[OTHER_GOAL], "duplicate")
        self.assertEqual(decisions[GOAL], "completed_after_verified_handoff")

    def test_canary_does_not_complete_handoff_without_verified_artifact(self) -> None:
        first_plan = GoalExecutionPlanner().plan(snapshot())
        candidate = first_plan.decisions[0].candidate
        self.assertIsNotNone(candidate)
        active = replace(
            agent_task(
                slug_identity="completed-handoff-without-artifact",
                goal_slug=GOAL,
                status="active",
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
        adapter = self.Adapter(tasks=(active,))
        bridge = self.Bridge()
        bridge.latest_status = "completed"

        result = GoalExecutionEngine(
            adapter=adapter,
            bridge=bridge,
            mode="canary",
            canary_goal_slug=GOAL,
        ).run_once(NOW)

        self.assertEqual(result.public_reason, "duplicate")
        self.assertEqual(result.task_status, "active")
        self.assertIn(("artifacts", active.slug), adapter.calls)
        self.assertNotIn(("status", (active.slug, "completed")), adapter.calls)

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

    def test_canary_requeues_recoverable_existing_handoff_repair(self) -> None:
        adapter = self.Adapter()
        bridge = self.Bridge()
        first = self.engine(adapter, bridge).run_once(NOW)
        bridge.latest_status = "suppressed"
        bridge.latest_delivery_state = {
            "status": "suppressed",
            "terminal_state": "checkpointed",
            "claimed_at": (NOW - timedelta(minutes=10)).isoformat(),
        }

        recovered = self.engine(adapter, bridge).run_once(NOW + timedelta(minutes=11))

        self.assertEqual(first.task_slug, recovered.task_slug)
        self.assertEqual(recovered.public_reason, "activated")
        self.assertEqual(recovered.handoff_status, "retrying")
        self.assertEqual(
            recovered.to_dict()["decisions"][0]["reason"],
            "duplicate",
        )
        self.assertEqual(len(bridge.recovery_calls), 1)
        self.assertEqual(bridge.recovery_calls[0][0], str(first.task_slug))
        self.assertEqual([name for name, _value in adapter.calls].count("status"), 1)

    def test_existing_suppressed_handoff_with_active_claim_remains_delivering(self) -> None:
        adapter = self.Adapter()
        bridge = self.Bridge()
        first = self.engine(adapter, bridge).run_once(NOW)
        bridge.latest_status = "suppressed"
        bridge.latest_delivery_state = {
            "status": "suppressed",
            "executor_agent": AGENT,
            "permanent_owner": AGENT,
            "claimed_at": NOW.isoformat(),
            "expires_at": (NOW + timedelta(minutes=30)).isoformat(),
            "terminal_state": None,
        }

        second = self.engine(adapter, bridge).run_once(NOW + timedelta(minutes=1))

        self.assertEqual(first.task_slug, second.task_slug)
        self.assertEqual(second.public_reason, "delivering")
        self.assertEqual(second.handoff_status, "queued")
        self.assertEqual(
            second.to_dict()["decisions"][0]["reason"],
            "duplicate",
        )
        self.assertEqual(len(bridge.recovery_calls), 0)

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

    def test_shadow_mode_reports_stale_queued_handoff_as_worker_attention(self) -> None:
        adapter = self.Adapter()
        bridge = self.Bridge()
        canary = self.engine(adapter, bridge).run_once(NOW)
        bridge.latest_status = "queued"
        bridge.latest_delivery_state = {
            "status": "queued",
            "claimed_at": (NOW - timedelta(minutes=7)).isoformat(),
            "terminal_state": None,
        }

        shadow = GoalExecutionEngine(
            adapter=adapter,
            bridge=bridge,
            mode="shadow",
            canary_goal_slug=GOAL,
        ).run_once(NOW)

        self.assertEqual(shadow.task_slug, canary.task_slug)
        self.assertEqual(shadow.public_reason, "handoff_worker_unavailable")
        self.assertEqual(shadow.handoff_status, "queued")
        self.assertEqual(
            shadow.to_dict()["decisions"][0]["reason"],
            "handoff_worker_unavailable",
        )

    def test_shadow_mode_reports_stale_retrying_handoff_as_worker_attention(self) -> None:
        adapter = self.Adapter()
        bridge = self.Bridge()
        canary = self.engine(adapter, bridge).run_once(NOW)
        bridge.latest_status = "retrying"
        bridge.latest_delivery_state = {
            "status": "retrying",
            "claimed_at": (NOW - timedelta(minutes=7)).isoformat(),
            "terminal_state": None,
        }

        shadow = GoalExecutionEngine(
            adapter=adapter,
            bridge=bridge,
            mode="shadow",
            canary_goal_slug=GOAL,
        ).run_once(NOW)

        self.assertEqual(shadow.task_slug, canary.task_slug)
        self.assertEqual(shadow.public_reason, "handoff_worker_unavailable")
        self.assertEqual(shadow.handoff_status, "retrying")
        self.assertEqual(
            shadow.to_dict()["decisions"][0]["reason"],
            "handoff_worker_unavailable",
        )

    def test_shadow_mode_keeps_fresh_queued_handoff_delivering(self) -> None:
        adapter = self.Adapter()
        bridge = self.Bridge()
        canary = self.engine(adapter, bridge).run_once(NOW)
        bridge.latest_status = "queued"
        bridge.latest_delivery_state = {
            "status": "queued",
            "claimed_at": (NOW - timedelta(minutes=1)).isoformat(),
            "terminal_state": None,
        }

        shadow = GoalExecutionEngine(
            adapter=adapter,
            bridge=bridge,
            mode="shadow",
            canary_goal_slug=GOAL,
        ).run_once(NOW)

        self.assertEqual(shadow.task_slug, canary.task_slug)
        self.assertEqual(shadow.public_reason, "delivering")
        self.assertEqual(shadow.handoff_status, "queued")
        self.assertEqual(shadow.to_dict()["decisions"][0]["reason"], "duplicate")

    def test_shadow_mode_reports_active_duplicate_handoff_as_executing(self) -> None:
        adapter = self.Adapter()
        bridge = self.Bridge()
        canary = self.engine(adapter, bridge).run_once(NOW)
        bridge.latest_status = "actively_executing"

        shadow = GoalExecutionEngine(
            adapter=adapter,
            bridge=bridge,
            mode="shadow",
            canary_goal_slug=GOAL,
        ).run_once(NOW)

        self.assertEqual(shadow.task_slug, canary.task_slug)
        self.assertEqual(shadow.public_reason, "actively_executing")
        self.assertEqual(shadow.handoff_status, "actively_executing")
        self.assertEqual(shadow.to_dict()["handoff"]["status"], "actively_executing")

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

    def test_status_reports_cold_run_as_loading_instead_of_blank(self) -> None:
        class BlockingEngine:
            mode = "canary"

            def __init__(self) -> None:
                self.started = threading.Event()
                self.release = threading.Event()

            def run_once(self, now):
                self.started.set()
                self.release.wait(timeout=2)
                return SimpleNamespace(
                    to_dict=lambda: {
                        "mode": "canary",
                        "ran_at": now.isoformat(),
                        "planner_version": "goal-execution-v1",
                        "public_reason": "shadow",
                    }
                )

        engine = BlockingEngine()
        scheduler = GoalExecutionScheduler(engine)

        scheduler.start()
        self.assertTrue(engine.started.wait(timeout=1))
        status = scheduler.status()
        engine.release.set()
        scheduler.stop()

        self.assertIsNone(status["last_run"])
        self.assertEqual(
            status["read_state"],
            {
                "surface": "goal_execution",
                "status": "loading",
                "refreshing": True,
                "last_valid_at": None,
            },
        )

    def test_status_projects_latest_public_decision_for_readers(self) -> None:
        class Engine:
            mode = "canary"

            def run_once(self, now):
                return SimpleNamespace(
                    to_dict=lambda: {
                        "mode": "canary",
                        "ran_at": now.isoformat(),
                        "planner_version": "goal-execution-v1",
                        "public_reason": "duplicate",
                        "task": {
                            "slug": "tasks/current",
                            "title": "Current Goal task",
                            "status": "active",
                            "agent_slug": "agents/toddy",
                        },
                        "handoff": {"status": "execution_started"},
                        "summary": {
                            "total_goals": 7,
                            "needs_attention": 2,
                            "next_action": "Answer Tony questions.",
                        },
                    }
                )

        engine = Engine()
        scheduler = GoalExecutionScheduler(engine)

        scheduler.start()
        time.sleep(0.05)
        status = scheduler.status()
        scheduler.stop()

        self.assertEqual(status["public_reason"], "duplicate")
        self.assertEqual(status["task_slug"], "tasks/current")
        self.assertEqual(status["handoff"], {"status": "execution_started"})
        self.assertEqual(
            status["summary"],
            {
                "total_goals": 7,
                "needs_attention": 2,
                "next_action": "Answer Tony questions.",
            },
        )

if __name__ == "__main__":
    unittest.main()
