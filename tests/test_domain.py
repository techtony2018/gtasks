import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

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
    task_display_window,
    task_is_in_default_display_window,
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

    def test_completed_qa_fixture_may_record_one_executing_agent(self) -> None:
        slug = "tasks/44444444-4444-4444-8444-444444444444"
        page = task_page(
            slug,
            status="completed",
            links=[{"to": QA_FIXTURES_ROOT, "type": "member_of"}],
        )
        page["frontmatter"].update(
            {
                "qa_fixture": True,
                "qa_owner": "mission_control_release_canary",
                "qa_release": "V0.0.70",
                "completed_at": "2026-08-03T09:00:00-07:00",
            }
        )
        edges = [
            {
                "from_slug": slug,
                "to_slug": QA_FIXTURES_ROOT,
                "link_type": "member_of",
            },
            {
                "from_slug": slug,
                "to_slug": "agents/tammy",
                "link_type": "assigned_to",
            },
        ]

        task = Task.from_page(page, edges=edges)

        self.assertEqual(task.owner_agent, "agents/tammy")
        self.assertTrue(task.qa_fixture)
        self.assertEqual(task.lifecycle_root, QA_FIXTURES_ROOT)

    def test_qa_fixture_rejects_multiple_executing_agents(self) -> None:
        slug = "tasks/55555555-5555-4555-8555-555555555555"
        page = task_page(
            slug,
            status="completed",
            links=[{"to": QA_FIXTURES_ROOT, "type": "member_of"}],
        )
        page["frontmatter"].update(
            {
                "qa_fixture": True,
                "qa_owner": "mission_control_release_canary",
                "qa_release": "V0.0.70",
                "completed_at": "2026-08-03T09:00:00-07:00",
            }
        )
        edges = [
            {
                "from_slug": slug,
                "to_slug": QA_FIXTURES_ROOT,
                "link_type": "member_of",
            },
            {
                "from_slug": slug,
                "to_slug": "agents/tammy",
                "link_type": "assigned_to",
            },
            {
                "from_slug": slug,
                "to_slug": "agents/toddy",
                "link_type": "assigned_to",
            },
        ]

        with self.assertRaisesRegex(
            DomainValidationError,
            "QA fixture permits at most one executing Agent",
        ):
            Task.from_page(page, edges=edges)


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

    def test_job_applied_binding_accepts_any_positive_target_and_seeded_current(self) -> None:
        page = task_page("tasks/custom-job-quota")
        page["frontmatter"]["progress_metric"] = {
            "kind": "count",
            "label": "Job applications",
            "unit": "job_application",
            "target": 4,
            "current": 2,
            "event_binding": "job_applied",
            "auto_complete": True,
            "task_day": "2026-07-30",
            "timezone": "America/Los_Angeles",
        }
        page["frontmatter"]["event_progress"] = {
            "baseline_count": 2,
            "receipt_ids": [],
            "evidence_slugs": [],
        }

        task = Task.from_page(page)

        self.assertEqual(task.progress_metric.target, 4)
        self.assertEqual(task.progress_metric.current, 2)
        self.assertEqual(task.event_progress.baseline_count, 2)

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


class RollingTaskDisplayWindowTests(unittest.TestCase):
    def test_calendar_month_window_clamps_month_end_and_leap_year(self) -> None:
        self.assertEqual(
            task_display_window(date(2026, 3, 31)),
            (date(2026, 2, 28), date(2026, 4, 30)),
        )
        self.assertEqual(
            task_display_window(date(2024, 3, 31)),
            (date(2024, 2, 29), date(2024, 4, 30)),
        )

    def test_uses_scheduled_day_before_due_day_and_includes_boundaries(self) -> None:
        as_of = date(2026, 8, 3)
        due_outside_but_scheduled_inside = Task.from_page(
            task_page(
                "tasks/scheduled-precedence",
                due_day="2026-10-03",
                scheduled_day="2026-07-03",
            )
        )
        start_boundary = Task.from_page(
            task_page("tasks/start-boundary", due_day="2026-07-03")
        )
        end_boundary = Task.from_page(
            task_page("tasks/end-boundary", due_day="2026-09-03")
        )

        self.assertTrue(
            task_is_in_default_display_window(due_outside_but_scheduled_inside, as_of)
        )
        self.assertTrue(task_is_in_default_display_window(start_boundary, as_of))
        self.assertTrue(task_is_in_default_display_window(end_boundary, as_of))

    def test_keeps_active_blocked_and_undated_tasks_but_scopes_other_outliers(self) -> None:
        as_of = date(2026, 8, 3)
        outside = Task.from_page(
            task_page("tasks/outside", due_day="2027-01-01")
        )

        self.assertFalse(task_is_in_default_display_window(outside, as_of))
        self.assertTrue(
            task_is_in_default_display_window(replace(outside, status="active"), as_of)
        )
        self.assertTrue(
            task_is_in_default_display_window(replace(outside, status="blocked"), as_of)
        )
        self.assertTrue(
            task_is_in_default_display_window(
                replace(outside, due_day=None, scheduled_day=None),
                as_of,
            )
        )


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


class AgentScopeDeclarationTests(unittest.TestCase):
    def test_openclaw_declaration_and_domain_scope_tables_remain_in_parity(self) -> None:
        config_path = (
            Path(__file__).resolve().parents[1]
            / "config"
            / "openclaw-agents"
            / "agents.json"
        )
        declaration = json.loads(config_path.read_text(encoding="utf-8"))
        expected_openclaw = {
            "agents/tammy-oc": (
                "hosts/tammy",
                "collections/tammy-oc-tasks",
                "collections/tammy-oc-artifacts",
            ),
            "agents/timmy-oc": (
                "hosts/timmy",
                "collections/timmy-oc-tasks",
                "collections/timmy-oc-artifacts",
            ),
            "agents/toddy-oc": (
                "hosts/toddy",
                "collections/toddy-oc-tasks",
                "collections/toddy-oc-artifacts",
            ),
        }

        self.assertEqual(declaration["schema_version"], 1)
        self.assertEqual(
            {
                item["slug"]: (
                    item["route"],
                    item["task_collection"],
                    item["artifact_collection"],
                )
                for item in declaration["agents"]
            },
            expected_openclaw,
        )
        self.assertTrue(
            all(item["runtime"] == "openclaw" for item in declaration["agents"])
        )

        task_scopes = dict(domain.AGENT_SCOPES)
        artifact_scopes = dict(domain.ARTIFACT_AGENT_SCOPES)
        self.assertEqual(len(task_scopes), 6)
        self.assertEqual(len(artifact_scopes), 6)
        self.assertEqual(len(set(task_scopes.values())), 6)
        self.assertEqual(len(set(artifact_scopes.values())), 6)
        self.assertEqual(
            {
                slug: (task_scopes[slug], artifact_scopes[slug])
                for slug in expected_openclaw
            },
            {
                slug: (task_collection, artifact_collection)
                for slug, (_route, task_collection, artifact_collection)
                in expected_openclaw.items()
            },
        )
        self.assertEqual(
            {slug: domain.AGENT_RUNTIME_BY_SLUG[slug] for slug in expected_openclaw},
            {slug: "openclaw" for slug in expected_openclaw},
        )
        self.assertEqual(
            domain.EXISTING_CODEX_AGENT_SCOPES,
            (
                ("agents/toddy", "collections/toddys-tasks"),
                ("agents/timmy", "collections/timmys-tasks"),
                ("agents/tammy", "collections/tammys-tasks"),
            ),
        )
        self.assertEqual(
            domain.EXISTING_CODEX_ARTIFACT_AGENT_SCOPES,
            (
                ("agents/tammy", "collections/tammys-artifacts"),
                ("agents/timmy", "collections/timmys-artifacts"),
                ("agents/toddy", "collections/toddys-artifacts"),
            ),
        )


class AgentArtifactContractTests(unittest.TestCase):
    def artifact_page(self, **overrides: object) -> dict:
        slug = str(
            overrides.pop(
                "slug", "artifacts/72a4d170-978f-4a37-bd92-b9d3bdde9339"
            )
        )
        frontmatter = {
            "type": "artifact",
            "title": "Family care weekly review brief",
            "artifact_kind": "markdown",
            "created_by": "agents/toddy",
            "produced_for": "tasks/561640dd-8e34-43e1-a03e-e3f3f270033d",
            "created_at": "2026-08-02T14:00:00-07:00",
            "attachments": [],
            "links": [
                {"to": "collections/toddys-artifacts", "type": "member_of"},
                {"to": "agents/toddy", "type": "created_by"},
                {
                    "to": "tasks/561640dd-8e34-43e1-a03e-e3f3f270033d",
                    "type": "produced_for",
                },
            ],
        }
        frontmatter.update(overrides.pop("frontmatter", {}))
        page = {
            "slug": slug,
            "type": "concept",
            "frontmatter": frontmatter,
            "compiled_markdown": "# Weekly review\n\nCanonical content.",
        }
        page.update(overrides)
        return page

    def test_agent_artifact_requires_one_agent_collection_and_task_link(self) -> None:
        artifact = domain.AgentArtifact.from_page(self.artifact_page(), edges=[])

        self.assertEqual(artifact.agent_collection, "collections/toddys-artifacts")
        self.assertEqual(artifact.created_by, "agents/toddy")
        self.assertEqual(artifact.artifact_kind, "markdown")
        self.assertEqual(artifact.markdown, "# Weekly review\n\nCanonical content.")

    def test_agent_artifact_accepts_gbrain_normalized_top_level_shape(self) -> None:
        page = self.artifact_page()
        page["type"] = "artifact"
        page["title"] = page["frontmatter"].pop("title")
        page["frontmatter"].pop("type")
        page["compiled_truth"] = page.pop("compiled_markdown")

        artifact = domain.AgentArtifact.from_page(page, edges=[])

        self.assertEqual(artifact.title, "Family care weekly review brief")
        self.assertEqual(artifact.markdown, "# Weekly review\n\nCanonical content.")

    def test_agent_artifact_rejects_normalized_type_and_title_conflicts(self) -> None:
        raw_type_conflict = self.artifact_page(type="task")
        normalized_type_conflict = self.artifact_page(type="artifact")
        normalized_type_conflict["frontmatter"]["type"] = "task"
        title_conflict = self.artifact_page(
            type="artifact", title="Different top-level title"
        )

        for label, page in (
            ("raw_type_conflict", raw_type_conflict),
            ("normalized_type_conflict", normalized_type_conflict),
            ("title_conflict", title_conflict),
        ):
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    DomainValidationError, "canonical artifact|title"
                ):
                    domain.AgentArtifact.from_page(page, edges=[])

    def test_agent_artifact_accepts_canonical_uuid5_task_identity(self) -> None:
        page = self.artifact_page()
        task_slug = "tasks/41fb50e0-e1d7-592b-b2c3-ff1f7aacff10"
        page["frontmatter"]["produced_for"] = task_slug
        page["frontmatter"]["links"][2]["to"] = task_slug

        artifact = domain.AgentArtifact.from_page(page, edges=[])

        self.assertEqual(artifact.produced_for, task_slug)

    def test_agent_artifact_rejects_non_v4_v5_canonical_references(self) -> None:
        invalid_task_slugs = (
            "tasks/6ba7b810-9dad-11d1-80b4-00c04fd430c8",
            "tasks/3d813cbb-47fb-32ba-91df-831e1593ac29",
        )
        for task_slug in invalid_task_slugs:
            with self.subTest(task_slug=task_slug):
                page = self.artifact_page()
                page["frontmatter"]["produced_for"] = task_slug
                page["frontmatter"]["links"][2]["to"] = task_slug
                with self.assertRaisesRegex(DomainValidationError, "UUIDv4 or UUIDv5"):
                    domain.AgentArtifact.from_page(page, edges=[])

        for link_type, target in (
            ("supports_project", "projects/6ba7b810-9dad-11d1-80b4-00c04fd430c8"),
            ("supports_goal", "goals/3d813cbb-47fb-32ba-91df-831e1593ac29"),
        ):
            with self.subTest(link_type=link_type):
                page = self.artifact_page()
                page["frontmatter"]["links"].append(
                    {"to": target, "type": link_type}
                )
                with self.assertRaisesRegex(DomainValidationError, "UUIDv4 or UUIDv5"):
                    domain.AgentArtifact.from_page(page, edges=[])

    def test_artifact_and_supersedes_identities_remain_uuid4_only(self) -> None:
        with self.assertRaisesRegex(DomainValidationError, "opaque UUID"):
            domain.AgentArtifact.from_page(
                self.artifact_page(
                    slug="artifacts/41fb50e0-e1d7-592b-b2c3-ff1f7aacff10"
                ),
                edges=[],
            )

        page = self.artifact_page()
        page["frontmatter"]["links"].append(
            {
                "to": "artifacts/41fb50e0-e1d7-592b-b2c3-ff1f7aacff10",
                "type": "supersedes",
            }
        )
        with self.assertRaisesRegex(DomainValidationError, "opaque UUID"):
            domain.AgentArtifact.from_page(page, edges=[])

    def test_agent_artifact_rejects_title_derived_slug(self) -> None:
        with self.assertRaisesRegex(DomainValidationError, "opaque UUID"):
            domain.AgentArtifact.from_page(
                self.artifact_page(slug="artifacts/family-care-weekly-review"),
                edges=[],
            )

    def test_agent_artifact_rejects_two_collection_memberships(self) -> None:
        page = self.artifact_page()
        page["frontmatter"]["links"].append(
            {"to": "collections/tammys-artifacts", "type": "member_of"}
        )
        with self.assertRaisesRegex(DomainValidationError, "exactly one.*member_of"):
            domain.AgentArtifact.from_page(page, edges=[])

    def test_agent_artifact_rejects_agent_collection_mismatch(self) -> None:
        page = self.artifact_page()
        page["frontmatter"]["created_by"] = "agents/tammy"
        page["frontmatter"]["links"][1] = {
            "to": "agents/tammy",
            "type": "created_by",
        }
        with self.assertRaisesRegex(DomainValidationError, "Agent collection"):
            domain.AgentArtifact.from_page(page, edges=[])

    def test_agent_artifact_requires_produced_for_relationship(self) -> None:
        page = self.artifact_page()
        page["frontmatter"]["links"] = page["frontmatter"]["links"][:2]
        with self.assertRaisesRegex(DomainValidationError, "produced_for"):
            domain.AgentArtifact.from_page(page, edges=[])

    def test_agent_artifact_requires_each_frontmatter_link_even_with_graph_edges(self) -> None:
        page = self.artifact_page()
        page["frontmatter"]["links"] = page["frontmatter"]["links"][:2]
        edges = [
            {
                "from_slug": page["slug"],
                "to_slug": "collections/toddys-artifacts",
                "link_type": "member_of",
            },
            {
                "from_slug": page["slug"],
                "to_slug": "agents/toddy",
                "link_type": "created_by",
            },
            {
                "from_slug": page["slug"],
                "to_slug": "tasks/561640dd-8e34-43e1-a03e-e3f3f270033d",
                "link_type": "produced_for",
            },
        ]

        with self.assertRaisesRegex(DomainValidationError, "frontmatter.*produced_for"):
            domain.AgentArtifact.from_page(page, edges=edges)

    def test_agent_artifact_requires_exact_graph_links_when_graph_edges_are_supplied(self) -> None:
        page = self.artifact_page()
        edges = [
            {
                "from_slug": page["slug"],
                "to_slug": "collections/toddys-artifacts",
                "link_type": "member_of",
            },
            {
                "from_slug": page["slug"],
                "to_slug": "agents/toddy",
                "link_type": "created_by",
            },
        ]

        with self.assertRaisesRegex(DomainValidationError, "graph.*produced_for"):
            domain.AgentArtifact.from_page(page, edges=edges)

    def test_agent_artifact_rejects_optional_graph_link_missing_from_frontmatter(self) -> None:
        page = self.artifact_page()
        edges = [
            {
                "from_slug": page["slug"],
                "to_slug": link["to"],
                "link_type": link["type"],
            }
            for link in page["frontmatter"]["links"]
        ]
        edges.append(
            {
                "from_slug": page["slug"],
                "to_slug": "projects/11111111-1111-4111-8111-111111111111",
                "link_type": "supports_project",
            }
        )

        with self.assertRaisesRegex(DomainValidationError, "graph.*supports_project"):
            domain.AgentArtifact.from_page(page, edges=edges)

    def test_agent_artifact_requires_canonical_uuid_optional_targets(self) -> None:
        for link_type, target in (
            ("supports_project", "projects/title-derived"),
            ("supports_goal", "goals/title-derived"),
        ):
            with self.subTest(link_type=link_type):
                page = self.artifact_page()
                page["frontmatter"]["links"].append(
                    {"to": target, "type": link_type}
                )
                with self.assertRaisesRegex(DomainValidationError, "canonical UUID"):
                    domain.AgentArtifact.from_page(page, edges=[])

    def test_agent_artifact_rejects_unsafe_attachment_references(self) -> None:
        for reference in (
            "javascript:alert(1)",
            "file:///Users/tony/private.png",
            "https://example.com/unverified.png",
            "/media/%2e%2e/api/health.png",
            "/media/artifacts%2Fsecret.png",
            "/media/artifacts%5Csecret.png",
            "/media/artifacts/brief.png?download=1",
            "/media/artifacts/brief.png#preview",
        ):
            with self.subTest(reference=reference):
                page = self.artifact_page(
                    frontmatter={"attachments": [reference]}
                )
                with self.assertRaisesRegex(
                    DomainValidationError, "attachments.*verified /media"
                ):
                    domain.AgentArtifact.from_page(page, edges=[])

    def test_agent_artifact_accepts_safe_url_encoded_media_filename(self) -> None:
        reference = "/media/artifacts/family%20brief.png"
        artifact = domain.AgentArtifact.from_page(
            self.artifact_page(frontmatter={"attachments": [reference]}),
            edges=[],
        )

        self.assertEqual(artifact.attachments, (reference,))

    def test_agent_artifact_rejects_non_https_git_reference(self) -> None:
        page = self.artifact_page(
            frontmatter={"artifact_kind": "git", "git_url": "file:///tmp/repo"}
        )
        with self.assertRaisesRegex(DomainValidationError, "HTTPS commit URL"):
            domain.AgentArtifact.from_page(page, edges=[])

    def test_agent_artifact_allows_only_known_host_commit_urls(self) -> None:
        allowed = (
            "https://github.com/openai/codex/commit/0123456789abcdef0123456789abcdef01234567",
            "https://gitlab.com/example/group/repo/-/commit/0123456789abcdef0123456789abcdef01234567",
            "https://bitbucket.org/example/repo/commits/0123456789abcdef0123456789abcdef01234567",
        )
        for git_url in allowed:
            with self.subTest(git_url=git_url):
                artifact = domain.AgentArtifact.from_page(
                    self.artifact_page(
                        frontmatter={"artifact_kind": "git", "git_url": git_url}
                    ),
                    edges=[],
                )
                self.assertEqual(artifact.git_url, git_url)

        page = self.artifact_page(
            frontmatter={
                "artifact_kind": "git",
                "git_url": "https://example.com/repo/commit/0123456789abcdef0123456789abcdef01234567",
            }
        )
        with self.assertRaisesRegex(DomainValidationError, "allowlisted HTTPS commit URL"):
            domain.AgentArtifact.from_page(page, edges=[])

    def test_agent_artifact_does_not_trust_publisher_readback_claims(self) -> None:
        artifact = domain.AgentArtifact.from_page(
            self.artifact_page(
                frontmatter={
                    "sha": "0123456789abcdef0123456789abcdef01234567",
                    "hash": "sha256:0123456789abcdef",
                    "verified": True,
                }
            ),
            edges=[],
        )

        self.assertFalse(hasattr(artifact, "sha"))
        self.assertFalse(hasattr(artifact, "hash"))
        self.assertFalse(hasattr(artifact, "verified"))
        self.assertNotIn("sha", artifact.to_dict())
        self.assertNotIn("hash", artifact.to_dict())
        self.assertNotIn("verified", artifact.to_dict())

    def test_new_agent_artifact_uses_opaque_identity_and_dedupes_attachments(self) -> None:
        artifact = domain.new_agent_artifact(
            title="Family care weekly review brief",
            artifact_kind="markdown",
            created_by="agents/toddy",
            produced_for="tasks/561640dd-8e34-43e1-a03e-e3f3f270033d",
            markdown="# Weekly review\n\nCanonical content.",
            attachments=("/media/artifacts/brief.png", "/media/artifacts/brief.png"),
            now=datetime(2026, 8, 2, 14, tzinfo=timezone.utc),
        )

        self.assertRegex(artifact.slug, r"^artifacts/[0-9a-f-]{36}$")
        self.assertEqual(artifact.agent_collection, "collections/toddys-artifacts")
        self.assertEqual(artifact.attachments, ("/media/artifacts/brief.png",))

    def test_delegation_reference_shape_parser_rejects_noncanonical_values(self) -> None:
        for invalid in (
            "delegation-secret-token",
            "agent-delegations/title-derived",
            "agent-delegations/6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(
                    DomainValidationError,
                    "delegation_ref.*canonical UUID",
                ):
                    domain.new_agent_artifact(
                        title="Unsafe provenance",
                        artifact_kind="markdown",
                        created_by="agents/tammy-oc",
                        produced_for=(
                            "tasks/561640dd-8e34-43e1-a03e-e3f3f270033d"
                        ),
                        markdown="# Evidence",
                        delegation_ref=invalid,
                        now=datetime(2026, 8, 8, 17, tzinfo=timezone.utc),
                    )


if __name__ == "__main__":
    unittest.main()
