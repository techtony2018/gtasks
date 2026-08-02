import unittest
from dataclasses import replace
from datetime import date, datetime, timezone

import gtasks.domain as domain

from gtasks.domain import (
    ACTIVE_ROOT,
    AgentProfile,
    PROPOSALS_ROOT,
    TaskProposal,
    DomainValidationError,
    GOALS_ROOT,
    Goal,
    Task,
    QA_FIXTURES_ROOT,
    default_goal_target_day,
    group_today,
    new_inbox_task,
)


def task_page(
    slug: str,
    *,
    status: str = "planned",
    summary: str = "Write the proposal",
    due_day: str = "2026-07-30",
    scheduled_day: str = "none",
    priority: str = "normal",
    inbox: bool = False,
    links: list[dict] | None = None,
) -> dict:
    return {
        "slug": slug,
        "type": "task",
        "title": summary,
        "compiled_truth": f"# {summary}\n\nDetails",
        "frontmatter": {
            "status": status,
            "summary": summary,
            "detail": "Details",
            "due_day": due_day,
            "scheduled_day": scheduled_day,
            "priority": priority,
            "next_action": "Draft the first section",
            "inbox": inbox,
            "links": links
            or [
                {
                    "to": ACTIVE_ROOT,
                    "type": "member_of",
                }
            ],
        },
    }


def todo_page(
    slug: str = "todos/11111111-1111-5111-8111-111111111111",
    *,
    parent_task: str = "tasks/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    status: str = "not_done",
    updated_at: str = "2026-08-01T10:05:00-07:00",
) -> dict:
    return {
        "slug": slug,
        "type": "todo",
        "title": "Confirm the deployment window",
        "compiled_truth": "# Confirm the deployment window",
        "frontmatter": {
            "type": "todo",
            "text": "Confirm the deployment window",
            "detail": "Tony should confirm whether 17:00 works.",
            "status": status,
            "kind": "question",
            "parent_task": parent_task,
            "created_at": "2026-08-01T10:00:00-07:00",
            "updated_at": updated_at,
            "creator": "agents/toddy",
            "source": "agent",
            "comment_slugs": [
                "todo-comments/22222222-2222-5222-8222-222222222222"
            ],
            "event_slugs": [
                "todo-events/33333333-3333-5333-8333-333333333333"
            ],
            "legacy_provenance": None,
        },
    }


class QaFixtureTaskContractTests(unittest.TestCase):
    def test_parses_explicit_qa_owned_task_only_in_qa_scope(self) -> None:
        page = task_page(
            "tasks/11111111-1111-4111-8111-111111111111",
            links=[{"to": QA_FIXTURES_ROOT, "type": "member_of"}],
        )
        page["frontmatter"].update(
            {
                "qa_fixture": True,
                "qa_owner": "independent_ui_qa",
                "qa_release": "V0.0.65",
            }
        )

        task = Task.from_page(page)

        self.assertTrue(task.qa_fixture)
        self.assertEqual(task.qa_owner, "independent_ui_qa")
        self.assertEqual(task.lifecycle_root, QA_FIXTURES_ROOT)

    def test_rejects_qa_marker_on_tony_task_scope(self) -> None:
        page = task_page("tasks/22222222-2222-4222-8222-222222222222")
        page["frontmatter"].update(
            {"qa_fixture": True, "qa_owner": "independent_ui_qa"}
        )

        with self.assertRaisesRegex(
            DomainValidationError,
            "QA fixture metadata requires the QA fixture collection",
        ):
            Task.from_page(page)

    def test_rejects_unmarked_task_in_qa_scope(self) -> None:
        page = task_page(
            "tasks/33333333-3333-4333-8333-333333333333",
            links=[{"to": QA_FIXTURES_ROOT, "type": "member_of"}],
        )

        with self.assertRaisesRegex(
            DomainValidationError,
            "QA fixture collection requires explicit QA ownership",
        ):
            Task.from_page(page)


class TodoDomainTests(unittest.TestCase):
    def test_stable_todo_comment_and_event_records_validate_typed_parents(self) -> None:
        self.assertTrue(hasattr(domain, "TodoItem"))
        self.assertTrue(hasattr(domain, "TodoComment"))
        self.assertTrue(hasattr(domain, "TodoEvent"))
        todo_slug = "todos/11111111-1111-5111-8111-111111111111"
        comment_slug = "todo-comments/22222222-2222-5222-8222-222222222222"
        event_slug = "todo-events/33333333-3333-5333-8333-333333333333"
        comment = domain.TodoComment.from_page(
            {
                "slug": comment_slug,
                "type": "todo_comment",
                "frontmatter": {
                    "type": "todo_comment",
                    "todo_slug": todo_slug,
                    "body": "17:00 works. Proceed.",
                    "author": "people/tony-guan",
                    "source": "mission_control",
                    "created_at": "2026-08-01T10:04:00-07:00",
                    "idempotency_key": "reply-1",
                },
            },
            edges=[
                {
                    "from_slug": comment_slug,
                    "to_slug": todo_slug,
                    "link_type": "comment_on",
                }
            ],
        )
        event = domain.TodoEvent.from_page(
            {
                "slug": event_slug,
                "type": "todo_event",
                "frontmatter": {
                    "type": "todo_event",
                    "todo_slug": todo_slug,
                    "event_type": "comment_added",
                    "actor": "people/tony-guan",
                    "source": "mission_control",
                    "occurred_at": "2026-08-01T10:05:00-07:00",
                    "idempotency_key": "reply-1",
                    "before": None,
                    "after": {"comment_slug": comment_slug},
                    "comment_slug": comment_slug,
                },
            },
            edges=[
                {
                    "from_slug": event_slug,
                    "to_slug": todo_slug,
                    "link_type": "event_for",
                }
            ],
        )
        todo = domain.TodoItem.from_page(
            todo_page(),
            edges=[
                {
                    "from_slug": todo_slug,
                    "to_slug": "tasks/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "link_type": "todo_for",
                }
            ],
            comments=(comment,),
            events=(event,),
        )

        self.assertEqual(todo.status, "not_done")
        self.assertEqual(todo.status_label, "Not Done")
        self.assertEqual(todo.kind, "question")
        self.assertEqual(todo.comments, (comment,))
        self.assertEqual(todo.events, (event,))
        self.assertEqual(todo.to_dict()["comments"][0]["body"], "17:00 works. Proceed.")

    def test_todo_requires_exactly_one_matching_parent_relationship(self) -> None:
        self.assertTrue(hasattr(domain, "TodoItem"))
        page = todo_page()
        with self.assertRaisesRegex(
            DomainValidationError,
            "exactly one todo_for relationship",
        ):
            domain.TodoItem.from_page(
                page,
                edges=[
                    {
                        "from_slug": page["slug"],
                        "to_slug": "tasks/one",
                        "link_type": "todo_for",
                    },
                    {
                        "from_slug": page["slug"],
                        "to_slug": "tasks/two",
                        "link_type": "todo_for",
                    },
                ],
            )

    def test_todo_rejects_invalid_lifecycle_timezone_and_history_order(self) -> None:
        self.assertTrue(hasattr(domain, "TodoItem"))
        page = todo_page(status="completed")
        with self.assertRaisesRegex(DomainValidationError, "todo status"):
            domain.TodoItem.from_page(
                page,
                edges=[
                    {
                        "from_slug": page["slug"],
                        "to_slug": page["frontmatter"]["parent_task"],
                        "link_type": "todo_for",
                    }
                ],
            )

        page = todo_page(updated_at="2026-08-01T10:05:00")
        with self.assertRaisesRegex(DomainValidationError, "timezone"):
            domain.TodoItem.from_page(
                page,
                edges=[
                    {
                        "from_slug": page["slug"],
                        "to_slug": page["frontmatter"]["parent_task"],
                        "link_type": "todo_for",
                    }
                ],
            )

    def test_task_read_model_exposes_todos_without_serializing_them_to_parent_page(self) -> None:
        self.assertTrue(hasattr(domain, "TodoItem"))
        task = Task.from_page(task_page("tasks/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"))
        todo = domain.TodoItem.from_page(
            todo_page(),
            edges=[
                {
                    "from_slug": "todos/11111111-1111-5111-8111-111111111111",
                    "to_slug": task.slug,
                    "link_type": "todo_for",
                }
            ],
        )
        enriched = replace(task, todos=(todo,))

        self.assertEqual(enriched.to_dict()["todos"][0]["slug"], todo.slug)


class TaskParsingTests(unittest.TestCase):
    def test_waiting_for_input_requires_blocked_status_and_matching_blocker(self) -> None:
        page = task_page(
            "tasks/agent-question",
            status="active",
            links=[
                {"to": "collections/tammys-tasks", "type": "member_of"},
                {"to": "agents/tammy", "type": "assigned_to"},
                {"to": "people/tony-guan", "type": "blocked_by"},
            ],
        )
        page["frontmatter"]["handoff"] = {
            "state": "waiting_for_input",
            "question_todo": "todos/question-1",
            "waiting_on": "people/tony-guan",
            "resume_owner": "agents/tammy",
            "resume_action": "Draft the seven-day plan.",
            "requested_at": "2026-08-02T10:00:00-07:00",
            "answered_at": None,
            "acknowledged_at": None,
            "round": 1,
        }

        with self.assertRaisesRegex(
            DomainValidationError,
            "waiting_for_input requires blocked",
        ):
            Task.from_page(
                page,
                edges=[
                    {
                        "from_slug": page["slug"],
                        "to_slug": "agents/tammy",
                        "link_type": "assigned_to",
                    }
                ],
            )

    def test_ready_for_agent_requires_active_status_and_resume_next_action(self) -> None:
        page = task_page(
            "tasks/agent-ready",
            status="active",
            links=[
                {"to": "collections/tammys-tasks", "type": "member_of"},
                {"to": "agents/tammy", "type": "assigned_to"},
            ],
        )
        page["frontmatter"]["next_action"] = "Draft the seven-day plan."
        page["frontmatter"]["handoff"] = {
            "state": "ready_for_agent",
            "question_todo": "todos/question-1",
            "waiting_on": None,
            "resume_owner": "agents/tammy",
            "resume_action": "Draft the seven-day plan.",
            "requested_at": "2026-08-02T10:00:00-07:00",
            "answered_at": "2026-08-02T10:29:22-07:00",
            "acknowledged_at": None,
            "round": 1,
        }

        task = Task.from_page(
            page,
            edges=[
                {
                    "from_slug": page["slug"],
                    "to_slug": "agents/tammy",
                    "link_type": "assigned_to",
                }
            ],
        )

        self.assertIsInstance(task.handoff, domain.TaskHandoff)
        self.assertEqual(task.next_action, task.handoff.resume_action)
        self.assertEqual(task.to_dict()["handoff"]["state"], "ready_for_agent")

    def test_handoff_rejects_invalid_identities_timestamps_and_round(self) -> None:
        valid = {
            "state": "waiting_for_input",
            "question_todo": "todos/question-1",
            "waiting_on": "people/tony-guan",
            "resume_owner": "agents/tammy",
            "resume_action": "Draft the seven-day plan.",
            "requested_at": "2026-08-02T10:00:00-07:00",
            "answered_at": None,
            "acknowledged_at": None,
            "round": 1,
        }
        invalid_cases = (
            ({"question_todo": "tasks/not-a-todo"}, "question_todo"),
            ({"resume_owner": "people/tammy"}, "resume_owner"),
            ({"requested_at": "2026-08-02T10:00:00"}, "timezone"),
            ({"round": 0}, "round"),
            ({"resume_action": "line one\nline two"}, "one concise line"),
        )

        for changes, expected in invalid_cases:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(DomainValidationError, expected):
                    domain.TaskHandoff.from_value({**valid, **changes})

    def test_parses_canonical_proposal_decision_timeline_events(self) -> None:
        page = task_page(
            "tasks/decision-history",
            status="planned",
            links=[
                {"to": "collections/toddys-tasks", "type": "member_of"},
                {"to": "agents/toddy", "type": "assigned_to"},
            ],
        )
        page["frontmatter"].update(
            {
                "proposal_recipient": "agent",
                "proposal_submitted_at": "2026-08-01T09:00:00-07:00",
                "proposal_decision": "approve",
                "proposal_decided_at": "2026-08-01T10:00:00-07:00",
                "proposal_decision_note": "Proceed with the bounded task.",
                "proposal_decision_events": [
                    {
                        "event_id": "proposal-decision:decision-history:approve",
                        "event_type": "proposal_decision",
                        "occurred_at": "2026-08-01T10:00:00-07:00",
                        "actor": "people/tony-guan",
                        "source": "mission_control",
                        "decision": "approve",
                        "decision_note": "Proceed with the bounded task.",
                        "previous_status": "proposed",
                        "resulting_status": "planned",
                        "proposal_slug": "tasks/decision-history",
                    }
                ],
            }
        )
        page["frontmatter"]["status"] = "planned"

        task = Task.from_page(
            page,
            edges=[
                {
                    "from_slug": "tasks/decision-history",
                    "to_slug": "agents/toddy",
                    "link_type": "assigned_to",
                }
            ],
        )

        self.assertEqual(task.proposal_decision, "approve")
        self.assertEqual(len(task.proposal_decision_events), 1)
        event = task.proposal_decision_events[0]
        self.assertEqual(event.resulting_status, "planned")
        self.assertEqual(
            task.to_dict()["proposal_decision_events"][0]["event_id"],
            "proposal-decision:decision-history:approve",
        )

    def test_rejects_proposal_decision_event_for_another_task(self) -> None:
        page = task_page("tasks/decision-history")
        page["frontmatter"]["proposal_decision_events"] = [
            {
                "event_id": "proposal-decision:other:reject",
                "event_type": "proposal_decision",
                "occurred_at": "2026-08-01T10:00:00-07:00",
                "actor": "people/tony-guan",
                "source": "mission_control",
                "decision": "reject",
                "decision_note": "No.",
                "previous_status": "proposed",
                "resulting_status": "cancelled",
                "proposal_slug": "tasks/other",
            }
        ]

        with self.assertRaisesRegex(
            DomainValidationError,
            "proposal decision event must reference its own task",
        ):
            Task.from_page(page)

    def test_compiled_markdown_does_not_override_structured_task_frontmatter(self) -> None:
        page = task_page("tasks/compiled-task")
        page["compiled_truth"] = "\n".join(
            [
                "---",
                "type: task",
                "title: Compiled task",
                "links:",
                f"  - to: {ACTIVE_ROOT}",
                "    type: member_of",
                "---",
                "",
                "# Compiled task",
            ]
        )

        task = Task.from_page(page)

        self.assertEqual(task.lifecycle_root, ACTIVE_ROOT)

    def test_unmetered_task_has_no_progress_metric(self) -> None:
        task = Task.from_page(task_page("tasks/unmetered"))

        self.assertIn("progress_metric", task.to_dict())
        self.assertIsNone(task.to_dict()["progress_metric"])

    def test_existing_task_without_next_action_history_remains_readable(self) -> None:
        task = Task.from_page(task_page("tasks/legacy-next-action"))

        self.assertEqual(task.next_action_history, ())
        self.assertEqual(task.to_dict()["next_action_history"], [])

    def test_parses_completed_next_action_history(self) -> None:
        page = task_page("tasks/next-action-history")
        page["frontmatter"]["next_action_history"] = [
            {
                "action": "Collect the source material",
                "completed_at": "2026-07-30T14:15:00-07:00",
            }
        ]

        task = Task.from_page(page)

        self.assertEqual(len(task.next_action_history), 1)
        self.assertEqual(
            task.next_action_history[0].action,
            "Collect the source material",
        )
        self.assertEqual(
            task.to_dict()["next_action_history"],
            [
                {
                    "action": "Collect the source material",
                    "completed_at": "2026-07-30T14:15:00-07:00",
                }
            ],
        )

    def test_parses_an_opt_in_count_progress_metric(self) -> None:
        page = task_page("tasks/job-quota")
        page["frontmatter"]["progress_metric"] = {
            "kind": "count",
            "label": "Job applications",
            "unit": "job_application",
            "target": 5,
            "current": 2,
            "event_binding": "job_applied",
            "auto_complete": True,
            "task_day": "2026-07-30",
            "timezone": "America/Los_Angeles",
        }
        page["frontmatter"]["event_progress"] = {
            "receipt_ids": ["evt-1", "evt-2"],
            "evidence_slugs": ["applications/one", "applications/two"],
        }

        task = Task.from_page(page)

        self.assertEqual(
            task.to_dict()["progress_metric"],
            page["frontmatter"]["progress_metric"],
        )
        self.assertEqual(
            task.to_dict()["event_progress"],
            page["frontmatter"]["event_progress"],
        )

    def test_tolerates_rollout_metric_without_optional_display_label(self) -> None:
        page = task_page("tasks/legacy-job-quota")
        page["frontmatter"]["progress_metric"] = {
            "kind": "count",
            "unit": "job_application",
            "target": 5,
            "current": 0,
            "event_binding": "job_applied",
            "auto_complete": True,
            "task_day": "2026-07-30",
            "timezone": "America/Los_Angeles",
        }
        page["frontmatter"]["event_progress"] = {
            "receipt_ids": [],
            "evidence_slugs": [],
        }

        task = Task.from_page(page)

        self.assertIsNone(task.progress_metric.label)

    def test_agent_profile_reads_default_goal_edges_and_safe_avatar_default(
        self,
    ) -> None:
        profile = AgentProfile.from_page(
            {
                "slug": "agents/toddy",
                "type": "agent",
                "title": "Agent Toddy",
                "compiled_truth": "# Agent Toddy\n\nCoordinates approved work.",
                "frontmatter": {},
            },
            work_root="collections/toddys-tasks",
            edges=[
                {
                    "from_slug": "agents/toddy",
                    "to_slug": "goals/happier-and-healthier",
                    "link_type": "default_agent_for",
                }
            ],
        )

        self.assertEqual(profile.name, "Toddy")
        self.assertEqual(profile.avatar_kind, "initials")
        self.assertEqual(profile.avatar_value, "TO")
        self.assertEqual(
            profile.default_goal_slugs,
            ("goals/happier-and-healthier",),
        )
        self.assertIsNone(profile.chat_url)

    def test_task_proposal_requires_typed_agent_and_collection_links(self) -> None:
        proposal = TaskProposal.from_page(
            {
                "slug": "proposals/toddy-wellbeing-check-in",
                "type": "task_proposal",
                "title": "Schedule a wellbeing check-in",
                "frontmatter": {
                    "status": "proposed",
                    "recipient": "tony",
                    "proposing_agent": "agents/toddy",
                    "rationale": "A check-in supports the wellbeing goal.",
                    "proposed_next_step": "Choose a 20-minute time tomorrow.",
                    "due_day": "2026-07-31",
                    "submitted_at": "2026-07-30T14:00:00-07:00",
                    "updated_at": "2026-07-30T14:00:00-07:00",
                },
            },
            edges=[
                {
                    "from_slug": "proposals/toddy-wellbeing-check-in",
                    "to_slug": PROPOSALS_ROOT,
                    "link_type": "member_of",
                },
                {
                    "from_slug": "proposals/toddy-wellbeing-check-in",
                    "to_slug": "agents/toddy",
                    "link_type": "proposed_by",
                },
                {
                    "from_slug": "proposals/toddy-wellbeing-check-in",
                    "to_slug": "goals/happier-and-healthier",
                    "link_type": "serves_goal",
                },
            ],
        )
        self.assertEqual(proposal.recipient, "tony")
        self.assertEqual(proposal.proposing_agent, "agents/toddy")
        self.assertEqual(proposal.linked_goal, "goals/happier-and-healthier")

    def test_agent_owned_proposed_task_is_a_normal_task(self) -> None:
        page = task_page(
            "collections/timmys-tasks/proposed-checklist",
            status="proposed",
            links=[{"to": "collections/timmys-tasks", "type": "member_of"}],
        )
        page["frontmatter"].update({
            "proposal_recipient": "agent",
            "proposal_submitted_at": "2026-07-31T10:00:00-07:00",
        })
        task = Task.from_page(page, edges=[
            {"from_slug": page["slug"], "to_slug": "collections/timmys-tasks", "link_type": "member_of"},
            {"from_slug": page["slug"], "to_slug": "agents/timmy", "link_type": "assigned_to"},
        ])
        self.assertEqual(task.status, "proposed")
        self.assertEqual(task.owner_agent, "agents/timmy")

    def test_personal_proposed_task_is_rejected(self) -> None:
        with self.assertRaisesRegex(DomainValidationError, "agent work collection"):
            Task.from_page(task_page("tasks/not-an-agent-proposal", status="proposed"))


    def test_agent_task_requires_matching_scope_and_assigned_to_relation(
        self,
    ) -> None:
        page = task_page(
            "tasks/agent-work",
            links=[
                {
                    "to": "collections/toddys-tasks",
                    "type": "member_of",
                }
            ]
        )
        page["frontmatter"]["links"] = [
            {
                "to": "collections/toddys-tasks",
                "type": "member_of",
            }
        ]
        assigned = Task.from_page(
            page,
            edges=[
                {
                    "from_slug": page["slug"],
                    "to_slug": "collections/toddys-tasks",
                    "link_type": "member_of",
                },
                {
                    "from_slug": page["slug"],
                    "to_slug": "agents/toddy",
                    "link_type": "assigned_to",
                },
            ],
        )
        self.assertEqual(assigned.owner_agent, "agents/toddy")
        self.assertEqual(
            assigned.lifecycle_root,
            "collections/toddys-tasks",
        )

        with self.assertRaisesRegex(
            DomainValidationError,
            "matching its work collection",
        ):
            Task.from_page(
                page,
                edges=[
                    {
                        "from_slug": page["slug"],
                        "to_slug": "agents/timmy",
                        "link_type": "assigned_to",
                    },
                ],
            )

    def test_rejects_invalid_progress_metric_contracts(self) -> None:
        valid = {
            "kind": "count",
            "label": "Job applications",
            "unit": "job_application",
            "target": 5,
            "current": 0,
            "event_binding": None,
            "auto_complete": False,
            "task_day": None,
            "timezone": None,
        }
        invalid_values = (
            ({"label": " "}, "label"),
            ({"target": 0}, "target"),
            ({"current": -1}, "current"),
            ({"current": 6}, "current"),
            ({"kind": "duration"}, "kind"),
            (
                {"event_binding": None, "auto_complete": True},
                "event binding",
            ),
        )

        for changes, expected in invalid_values:
            with self.subTest(changes=changes):
                page = task_page("tasks/invalid-metric")
                page["frontmatter"]["progress_metric"] = {**valid, **changes}
                with self.assertRaisesRegex(DomainValidationError, expected):
                    Task.from_page(page)

    def test_job_applied_binding_requires_the_exact_daily_quota_contract(self) -> None:
        page = task_page("tasks/wrong-job-quota")
        page["frontmatter"]["progress_metric"] = {
            "kind": "count",
            "label": "Job applications",
            "unit": "job_application",
            "target": 4,
            "current": 0,
            "event_binding": "job_applied",
            "auto_complete": True,
            "task_day": "2026-07-30",
            "timezone": "America/Los_Angeles",
        }
        page["frontmatter"]["event_progress"] = {
            "receipt_ids": [],
            "evidence_slugs": [],
        }

        with self.assertRaisesRegex(DomainValidationError, "job_applied"):
            Task.from_page(page)

    def test_parses_supported_fields_and_relationships(self) -> None:
        page = task_page(
            "tasks/2026/2026-07-30-write-proposal-a1b2c3",
            due_day="2026-07-30",
            links=[
                {"to": ACTIVE_ROOT, "type": "member_of"},
                {"to": "projects/launch", "type": "member_of"},
                {"to": "tasks/parent", "type": "child_of"},
                {"to": "tasks/prerequisite", "type": "depends_on"},
                {"to": "people/reviewer", "type": "blocked_by"},
            ],
        )

        task = Task.from_page(
            page,
            edges=[
                {
                    "from_slug": page["slug"],
                    "to_slug": "goals/ship-product",
                    "link_type": "advances_goal",
                }
            ],
        )

        self.assertEqual(task.project, "projects/launch")
        self.assertEqual(task.parent, "tasks/parent")
        self.assertEqual(task.dependencies, ("tasks/prerequisite",))
        self.assertEqual(task.blockers, ("people/reviewer",))
        self.assertEqual(task.goal, "goals/ship-product")
        self.assertEqual(task.due_day, date(2026, 7, 30))

    def test_rejects_multiple_goal_relationships(self) -> None:
        page = task_page("tasks/two-goals")
        with self.assertRaisesRegex(DomainValidationError, "one goal"):
            Task.from_page(
                page,
                edges=[
                    {
                        "from_slug": page["slug"],
                        "to_slug": "goals/one",
                        "link_type": "advances_goal",
                    },
                    {
                        "from_slug": page["slug"],
                        "to_slug": "goals/two",
                        "link_type": "advances_goal",
                    },
                ],
            )

    def test_rejects_missing_required_summary(self) -> None:
        page = task_page("tasks/missing-summary")
        page["frontmatter"]["summary"] = ""

        with self.assertRaisesRegex(DomainValidationError, "summary"):
            Task.from_page(page)

    def test_rejects_unknown_status(self) -> None:
        with self.assertRaisesRegex(DomainValidationError, "status"):
            Task.from_page(task_page("tasks/bad-status", status="someday"))

    def test_parses_waiting_as_a_legacy_compatible_status(self) -> None:
        task = Task.from_page(task_page("tasks/legacy-waiting", status="waiting"))

        self.assertEqual(task.status, "blocked")

    def test_rejects_unknown_priority(self) -> None:
        with self.assertRaisesRegex(DomainValidationError, "priority"):
            Task.from_page(task_page("tasks/bad-priority", priority="critical"))

    def test_rejects_task_without_due_day(self) -> None:
        with self.assertRaisesRegex(DomainValidationError, "due_day"):
            Task.from_page(task_page("tasks/no-due-date", due_day="none"))

    def test_accepts_existing_midnight_iso_due_day(self) -> None:
        task = Task.from_page(
            task_page(
                "tasks/existing-iso-due",
                due_day="2026-07-30T00:00:00.000Z",
            )
        )

        self.assertEqual(task.due_day, date(2026, 7, 30))

    def test_rejects_self_referential_task_relationship(self) -> None:
        slug = "tasks/self-reference"
        with self.assertRaisesRegex(DomainValidationError, "itself"):
            Task.from_page(
                task_page(
                    slug,
                    links=[
                        {"to": ACTIVE_ROOT, "type": "member_of"},
                        {"to": slug, "type": "depends_on"},
                    ],
                )
            )

    def test_rejects_task_without_a_lifecycle_root(self) -> None:
        with self.assertRaisesRegex(DomainValidationError, "lifecycle root"):
            Task.from_page(
                task_page(
                    "tasks/no-root",
                    links=[{"to": "projects/launch", "type": "member_of"}],
                )
            )


class TodayProjectionTests(unittest.TestCase):
    def test_groups_tasks_in_required_precedence_order(self) -> None:
        tasks = [
            Task.from_page(task_page("tasks/active", status="active", due_day="2026-07-29")),
            Task.from_page(task_page("tasks/today", due_day="2026-07-30")),
            Task.from_page(task_page("tasks/scheduled", scheduled_day="2026-07-30")),
            Task.from_page(task_page("tasks/waiting", status="waiting", due_day="2026-07-28")),
            Task.from_page(task_page("tasks/blocked", status="blocked")),
            Task.from_page(task_page("tasks/overdue", due_day="2026-07-29")),
            Task.from_page(task_page("tasks/future", due_day="2026-08-02")),
        ]

        result = group_today(tasks, date(2026, 7, 30))

        self.assertEqual([task.slug for task in result.in_progress], ["tasks/active"])
        self.assertEqual(
            [task.slug for task in result.todays_actions],
            ["tasks/today", "tasks/scheduled"],
        )
        self.assertEqual(
            [task.slug for task in result.waiting_and_blocked],
            ["tasks/waiting", "tasks/blocked"],
        )
        self.assertEqual([task.slug for task in result.overdue], ["tasks/overdue"])

    def test_caps_in_progress_at_three_and_reports_overflow(self) -> None:
        tasks = [
            Task.from_page(task_page(f"tasks/active-{index}", status="active"))
            for index in range(5)
        ]

        result = group_today(tasks, date(2026, 7, 30))

        self.assertEqual(len(result.in_progress), 3)
        self.assertEqual(result.in_progress_overflow, 2)


class NewInboxTaskTests(unittest.TestCase):
    def test_title_only_quick_add_has_safe_inbox_defaults(self) -> None:
        task = new_inbox_task(
            "  Book dentist appointment  ",
            now=datetime(2026, 7, 30, 16, 45, tzinfo=timezone.utc),
            identity="a1b2c3",
        )

        self.assertEqual(task.summary, "Book dentist appointment")
        self.assertEqual(task.status, "planned")
        self.assertEqual(task.priority, "normal")
        self.assertTrue(task.inbox)
        self.assertEqual(task.due_day, date(2026, 7, 30))
        self.assertEqual(task.lifecycle_root, ACTIVE_ROOT)
        self.assertRegex(task.slug, r"^tasks/[0-9a-f-]{36}$")
        self.assertNotIn("dentist", task.slug)

    def test_quick_add_preserves_an_explicit_due_date(self) -> None:
        task = new_inbox_task(
            "Book dentist appointment",
            now=datetime(2026, 7, 30, 16, 45, tzinfo=timezone.utc),
            identity="a1b2c3",
            due_day=date(2026, 8, 4),
        )

        self.assertEqual(task.due_day, date(2026, 8, 4))

    def test_quick_add_rejects_blank_title(self) -> None:
        with self.assertRaisesRegex(DomainValidationError, "title"):
            new_inbox_task(
                "   ",
                now=datetime(2026, 7, 30, tzinfo=timezone.utc),
                identity="a1b2c3",
            )


class FullTaskCreationTests(unittest.TestCase):
    def test_full_task_factory_is_available(self) -> None:
        self.assertTrue(callable(getattr(domain, "new_task", None)))

    def test_full_task_keeps_metric_opt_in_independent_of_lifecycle(self) -> None:
        metric = domain.ProgressMetric.from_value(
            {
                "kind": "count",
                "label": "Job applications",
                "unit": "job_application",
                "target": 5,
                "current": 5,
                "event_binding": None,
                "auto_complete": False,
                "task_day": None,
                "timezone": None,
            }
        )

        task = domain.new_task(
            title="Apply for five companies",
            detail="Use the approved resume.",
            priority="high",
            next_action="Choose the next company",
            due_day=date(2026, 7, 31),
            project="projects/job-search",
            goal="goals/get-a-job",
            progress_metric=metric,
            now=datetime(2026, 7, 30, 16, 45, tzinfo=timezone.utc),
            identity="a1b2c3",
        )

        self.assertIsInstance(task, Task)
        self.assertEqual(task.status, "planned")
        self.assertIsNone(task.completed_at)
        self.assertTrue(task.inbox)
        self.assertEqual(task.progress_metric.current, 5)
        self.assertEqual(task.project, "projects/job-search")
        self.assertEqual(task.goal, "goals/get-a-job")
        self.assertRegex(task.slug, r"^tasks/[0-9a-f-]{36}$")
        self.assertNotIn("companies", task.slug)

    def test_duplicate_task_factory_is_available(self) -> None:
        self.assertTrue(callable(getattr(domain, "duplicate_task", None)))

    def test_duplicate_resets_status_progress_evidence_and_completion(self) -> None:
        source_metric = domain.ProgressMetric.from_value(
            {
                "kind": "count",
                "label": "Job applications",
                "unit": "job_application",
                "target": 5,
                "current": 4,
                "event_binding": "job_applied",
                "auto_complete": True,
                "task_day": "2026-07-30",
                "timezone": "America/Los_Angeles",
            }
        )
        source_progress = domain.EventProgress.from_value(
            {
                "receipt_ids": ["evt-1", "evt-2", "evt-3", "evt-4"],
                "evidence_slugs": [
                    "applications/one",
                    "applications/two",
                    "applications/three",
                    "applications/four",
                ],
            }
        )
        source = replace(
            domain.new_task(
                title="Apply for five companies",
                detail="Use the approved resume.",
                priority="high",
                next_action="Choose the next company",
                due_day=date(2026, 7, 30),
                project="projects/job-search",
                goal="goals/get-a-job",
                progress_metric=source_metric,
                event_progress=source_progress,
                now=datetime(2026, 7, 30, 8, tzinfo=timezone.utc),
                identity="source1",
            ),
            status="completed",
            inbox=False,
            completed_at=datetime(2026, 7, 30, 17, tzinfo=timezone.utc),
        )

        duplicate = domain.duplicate_task(
            source,
            due_day=date(2026, 7, 31),
            now=datetime(2026, 7, 30, 18, tzinfo=timezone.utc),
            identity="copy123",
        )

        self.assertIsInstance(duplicate, Task)
        self.assertEqual(duplicate.status, "planned")
        self.assertTrue(duplicate.inbox)
        self.assertIsNone(duplicate.completed_at)
        self.assertEqual(duplicate.due_day, date(2026, 7, 31))
        self.assertEqual(duplicate.project, source.project)
        self.assertEqual(duplicate.goal, source.goal)
        self.assertEqual(duplicate.progress_metric.current, 0)
        self.assertEqual(duplicate.progress_metric.task_day, date(2026, 7, 31))
        self.assertEqual(duplicate.event_progress.receipt_ids, ())
        self.assertEqual(duplicate.event_progress.evidence_slugs, ())


class GoalTests(unittest.TestCase):
    def test_parses_the_live_goal_contract(self) -> None:
        page = {
                "slug": "goals/engineering-manager-job",
                "type": "goal",
                "title": "Secure an Engineering Manager job in high tech",
                "compiled_truth": "# Secure an Engineering Manager job in high tech",
                "frontmatter": {
                    "status": "planned",
                    "outcome": "Secure a job as an Engineering Manager.",
                    "success_criteria": "Accept a suitable offer.",
                    "target_day": "2026-09-30T00:00:00.000Z",
                    "strategy": "Run a focused search.",
                    "review_cadence": "weekly",
                    "constraints": "Use truthful application materials.",
                    "collection": GOALS_ROOT,
                },
            }
        goal = Goal.from_page(
            page,
            edges=[
                {
                    "from_slug": page["slug"],
                    "to_slug": "tasks/apply-to-company",
                    "link_type": "advanced_by",
                }
            ],
        )

        self.assertEqual(goal.target_day, date(2026, 9, 30))
        self.assertEqual(goal.review_cadence, "weekly")
        self.assertEqual(goal.advanced_by, ("tasks/apply-to-company",))

    def test_parses_compiled_goal_when_raw_storage_type_is_concept(self) -> None:
        page = {
            "slug": "goals/renamed-stable-goal",
            "type": "concept",
            "title": "Career: New presentation label",
            "frontmatter": {
                "type": "goal", "collection": GOALS_ROOT, "status": "active",
                "outcome": "Outcome.", "success_criteria": "Criteria.",
                "target_day": "2026-09-30", "strategy": "Strategy.",
                "review_cadence": "weekly", "constraints": "Constraints.",
            },
        }
        goal = Goal.from_page(page, edges=[{"from_slug": page["slug"], "to_slug": "tasks/stable-task", "link_type": "advanced_by"}])
        self.assertEqual(goal.slug, "goals/renamed-stable-goal")
        self.assertEqual(goal.title, "Career: New presentation label")
        self.assertEqual(goal.advanced_by, ("tasks/stable-task",))

    def test_rejects_goal_outside_tonys_goals_collection(self) -> None:
        with self.assertRaisesRegex(DomainValidationError, "collection"):
            Goal.from_page(
                {
                    "slug": "goals/wrong-root",
                    "type": "goal",
                    "title": "Wrong root",
                    "frontmatter": {
                        "status": "planned",
                        "outcome": "Wrong root.",
                        "success_criteria": "None.",
                        "target_day": "2026-09-30",
                        "strategy": "None.",
                        "review_cadence": "weekly",
                        "constraints": "None.",
                        "collection": "collections/other-goals",
                    },
                }
            )

    def test_default_goal_target_is_end_of_creation_quarter(self) -> None:
        self.assertEqual(default_goal_target_day(date(2026, 7, 30)), date(2026, 9, 30))
        self.assertEqual(default_goal_target_day(date(2026, 12, 1)), date(2026, 12, 31))

    def test_new_goals_projects_and_system_tickets_use_opaque_identities(self) -> None:
        now = datetime(2026, 8, 1, 9, tzinfo=timezone.utc)
        goal = domain.new_goal(
            title="Career: Mutable label",
            outcome="Reach the outcome.",
            success_criteria="Verify it.",
            strategy="Work deliberately.",
            review_cadence="weekly",
            constraints="Preserve identity.",
            now=now,
            identity="opaque1",
        )
        project = domain.new_project("Mutable project label", now, "opaque2")
        ticket = domain.new_system_ticket(
            title="Mutable ticket label",
            verbatim_request="Keep this exact request.",
            target_subsystem="mission_control",
            priority="normal",
            now=now,
            identity="opaque3",
        )

        self.assertRegex(goal.slug, r"^goals/[0-9a-f-]{36}$")
        self.assertRegex(project.slug, r"^projects/[0-9a-f-]{36}$")
        self.assertRegex(ticket.slug, r"^tasks/[0-9a-f-]{36}$")
        self.assertNotIn("mutable", " ".join((goal.slug, project.slug, ticket.slug)))


if __name__ == "__main__":
    unittest.main()
