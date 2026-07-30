import json
import subprocess
import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from gtasks.domain import (
    ACTIVE_ROOT,
    COMPLETED_ROOT,
    GOALS_ROOT,
    PROJECTS_ROOT,
    new_inbox_task,
    new_project,
)
from gtasks.gbrain import (
    GBrainAdapter,
    GBrainCommandError,
    NextActionMutationReceipt,
    PartialMutationError,
    SubprocessCommandRunner,
)


def stored_page(task) -> dict:
    return {
        "slug": task.slug,
        "type": "task",
        "title": task.title,
        "compiled_truth": f"# {task.title}",
        "frontmatter": {
            "status": task.status,
            "summary": task.summary,
            "detail": task.detail,
            "due_day": task.due_day.isoformat(),
            "priority": task.priority,
            "next_action": task.next_action,
            "scheduled_day": "none",
            "inbox": task.inbox,
            "completed_at": None,
            "links": [{"to": ACTIVE_ROOT, "type": "member_of"}],
        },
    }


def stored_goal(slug: str, title: str) -> dict:
    return {
        "slug": slug,
        "type": "goal",
        "title": title,
        "compiled_truth": f"# {title}",
        "frontmatter": {
            "status": "planned",
            "outcome": f"{title}.",
            "success_criteria": "Define during weekly review.",
            "target_day": "2026-09-30T00:00:00.000Z",
            "strategy": "Define during weekly review.",
            "review_cadence": "weekly",
            "constraints": "Define during weekly review.",
            "collection": GOALS_ROOT,
        },
    }


def stored_project(project) -> dict:
    return {
        "slug": project.slug,
        "type": "project",
        "title": project.title,
        "compiled_truth": f"# {project.title}",
        "frontmatter": {
            "status": project.status,
            "summary": project.summary,
            "created_at": (
                project.created_at.isoformat() if project.created_at else None
            ),
            "updated_at": (
                project.updated_at.isoformat() if project.updated_at else None
            ),
            "links": [{"to": PROJECTS_ROOT, "type": "involved_in"}],
        },
    }


class FakeRunner:
    def __init__(self, responses: dict[str, list[object]]) -> None:
        self.responses = {name: list(values) for name, values in responses.items()}
        self.calls: list[tuple[str, dict]] = []

    def run(self, tool: str, params: dict) -> object:
        self.calls.append((tool, params))
        result = self.responses[tool].pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class CollectionReadTests(unittest.TestCase):
    def test_loads_only_direct_member_backlinks_from_the_approved_root(self) -> None:
        task = new_inbox_task(
            "Real task",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        },
                        {
                            "from_slug": "notes/unrelated",
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "mentions",
                        },
                    ]
                ],
                "get_page": [stored_page(task)],
                "get_links": [[]],
            }
        )

        result = GBrainAdapter(runner).list_collection_tasks(ACTIVE_ROOT)

        self.assertEqual([item.slug for item in result.tasks], [task.slug])
        self.assertEqual(result.issues, ())
        self.assertNotIn("list_pages", [tool for tool, _ in runner.calls])
        self.assertEqual(
            runner.calls,
            [
                ("get_backlinks", {"slug": ACTIVE_ROOT}),
                ("get_page", {"slug": task.slug}),
                ("get_links", {"slug": task.slug}),
            ],
        )


class ProjectPersistenceTests(unittest.TestCase):
    def test_lists_project_nodes_from_tonys_projects_without_tasks(self) -> None:
        project = new_project(
            "Interview preparation",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        edge = {
            "from_slug": project.slug,
            "to_slug": PROJECTS_ROOT,
            "link_type": "involved_in",
        }
        runner = FakeRunner(
            {
                "get_backlinks": [[edge]],
                "get_page": [stored_project(project)],
                "get_links": [[edge]],
            }
        )

        result = GBrainAdapter(runner).list_projects()

        self.assertEqual([item.slug for item in result.projects], [project.slug])

    def test_create_project_requires_page_and_collection_link_readback(self) -> None:
        project = new_project(
            "Interview preparation",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        edge = {
            "from_slug": project.slug,
            "to_slug": PROJECTS_ROOT,
            "link_type": "involved_in",
        }
        runner = FakeRunner(
            {
                "put_page": [{"slug": project.slug}],
                "get_page": [stored_project(project)],
                "add_link": [{}],
                "get_links": [[edge]],
            }
        )

        receipt = GBrainAdapter(runner).create_project(project)

        self.assertTrue(receipt.verified)
        self.assertIn("type: project", runner.calls[0][1]["content"])
        self.assertIn(("add_link", {
            "from": project.slug,
            "to": PROJECTS_ROOT,
            "link_type": "involved_in",
            "context": "GTasks durable project membership.",
            "link_source": "gtasks",
        }), runner.calls)

    def test_reports_invalid_linked_pages_without_hiding_valid_tasks(self) -> None:
        valid = new_inbox_task(
            "Valid task",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        invalid_page = stored_page(valid)
        invalid_page["slug"] = "tasks/missing-due"
        invalid_page["frontmatter"]["due_day"] = "none"
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": valid.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        },
                        {
                            "from_slug": "tasks/missing-due",
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        },
                    ]
                ],
                "get_page": [stored_page(valid), invalid_page],
                "get_links": [[], []],
            }
        )

        result = GBrainAdapter(runner).list_collection_tasks(ACTIVE_ROOT)

        self.assertEqual([item.slug for item in result.tasks], [valid.slug])
        self.assertEqual(result.issues[0].slug, "tasks/missing-due")
        self.assertIn("due_day", result.issues[0].message)

    def test_loads_legacy_untyped_membership_when_collection_matches_root(self) -> None:
        task = new_inbox_task(
            "Apply for five more companies",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        page = stored_page(task)
        page["frontmatter"].pop("links")
        page["frontmatter"]["collection"] = ACTIVE_ROOT
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "",
                        }
                    ]
                ],
                "get_page": [page],
                "get_links": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "",
                        }
                    ]
                ],
            }
        )

        result = GBrainAdapter(runner).list_collection_tasks(ACTIVE_ROOT)

        self.assertEqual([item.slug for item in result.tasks], [task.slug])
        self.assertEqual(result.tasks[0].lifecycle_root, ACTIVE_ROOT)
        self.assertEqual(result.issues[0].severity, "warning")
        self.assertIn("legacy untyped", result.issues[0].message.lower())

    def test_typed_membership_wins_over_duplicate_legacy_backlinks(self) -> None:
        task = new_inbox_task(
            "One canonical task",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "",
                        },
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        },
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        },
                    ]
                ],
                "get_page": [stored_page(task)],
                "get_links": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        }
                    ]
                ],
            }
        )

        result = GBrainAdapter(runner).list_collection_tasks(ACTIVE_ROOT)

        self.assertEqual([item.slug for item in result.tasks], [task.slug])
        self.assertEqual(result.issues, ())
        self.assertEqual(
            [tool for tool, _params in runner.calls].count("get_page"),
            1,
        )

    def test_does_not_accept_untyped_backlink_without_matching_collection(self) -> None:
        task = new_inbox_task(
            "Unrelated mention",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "",
                        }
                    ]
                ],
                "get_page": [stored_page(task)],
                "get_links": [[]],
            }
        )

        result = GBrainAdapter(runner).list_collection_tasks(ACTIVE_ROOT)

        self.assertEqual(result.tasks, ())
        self.assertEqual(result.issues, ())

    def test_shows_task_shaped_legacy_page_with_wrong_type_as_warning(self) -> None:
        task = new_inbox_task(
            "Complete the Career Upbeat Project",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        page = stored_page(task)
        page["type"] = "concept"
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        }
                    ]
                ],
                "get_page": [page],
                "get_links": [[]],
            }
        )

        result = GBrainAdapter(runner).list_collection_tasks(ACTIVE_ROOT)

        self.assertEqual([item.slug for item in result.tasks], [task.slug])
        self.assertEqual(result.issues[0].severity, "warning")
        self.assertIn("concept", result.issues[0].message)

    def test_optional_goal_read_failure_does_not_hide_core_valid_task(self) -> None:
        task = new_inbox_task(
            "Core-valid task",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        }
                    ]
                ],
                "get_page": [stored_page(task)],
                "get_links": [GBrainCommandError("relationship service unavailable")],
            }
        )

        result = GBrainAdapter(runner).list_collection_tasks(ACTIVE_ROOT)

        self.assertEqual([item.slug for item in result.tasks], [task.slug])
        self.assertEqual(result.tasks[0].goal, None)
        self.assertEqual(result.issues[0].severity, "warning")
        self.assertIn("relationships", result.issues[0].message)

    def test_multiple_optional_goal_edges_warn_and_do_not_hide_task(self) -> None:
        task = new_inbox_task(
            "Task with malformed optional goals",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        }
                    ]
                ],
                "get_page": [stored_page(task)],
                "get_links": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": "goals/one",
                            "link_type": "advances_goal",
                        },
                        {
                            "from_slug": task.slug,
                            "to_slug": "goals/two",
                            "link_type": "advances_goal",
                        },
                    ]
                ],
            }
        )

        result = GBrainAdapter(runner).list_collection_tasks(ACTIVE_ROOT)

        self.assertEqual([item.slug for item in result.tasks], [task.slug])
        self.assertIsNone(result.tasks[0].goal)
        self.assertEqual(result.issues[0].severity, "warning")
        self.assertIn("multiple", result.issues[0].message.lower())

    def test_rejects_an_unapproved_collection_root(self) -> None:
        with self.assertRaisesRegex(ValueError, "approved"):
            GBrainAdapter(FakeRunner({})).list_collection_tasks("index")


class LifecycleRepairTests(unittest.TestCase):
    def test_repairs_unambiguous_legacy_active_membership_with_readback(self) -> None:
        task = new_inbox_task(
            "Repair active membership",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        legacy_page = stored_page(task)
        legacy_page["frontmatter"].pop("links")
        legacy_page["frontmatter"]["collection"] = ACTIVE_ROOT
        legacy_edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "",
        }
        typed_edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "get_page": [legacy_page, stored_page(task)],
                "get_links": [[legacy_edge], [typed_edge]],
                "put_page": [{"slug": task.slug}],
                "add_link": [typed_edge],
                "remove_link": [{"removed": True}],
            }
        )

        receipt = GBrainAdapter(runner).repair_active_membership(task.slug)

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.task_slug, task.slug)
        self.assertIn(
            ("add_link", {
                "from": task.slug,
                "to": ACTIVE_ROOT,
                "link_type": "member_of",
                "context": "GTasks active task membership repair.",
                "link_source": "gtasks",
            }),
            runner.calls,
        )
        written = next(
            params["content"]
            for tool, params in runner.calls
            if tool == "put_page"
        )
        self.assertIn('"type": "member_of"', written)
        self.assertIn(ACTIVE_ROOT, written)

    def test_refuses_repair_without_exact_legacy_collection_contract(self) -> None:
        task = new_inbox_task(
            "Not eligible",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        runner = FakeRunner(
            {
                "get_page": [stored_page(task)],
                "get_links": [[]],
            }
        )

        with self.assertRaisesRegex(ValueError, "not eligible"):
            GBrainAdapter(runner).repair_active_membership(task.slug)

        self.assertNotIn(
            "put_page",
            [tool for tool, _params in runner.calls],
        )


class GoalReadTests(unittest.TestCase):
    def test_discovers_every_direct_goal_backlink_dynamically(self) -> None:
        first = stored_goal("goals/one", "First goal")
        sixth = stored_goal("goals/political-action", "Help California through action")
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": first["slug"],
                            "to_slug": GOALS_ROOT,
                            "link_type": "",
                        },
                        {
                            "from_slug": sixth["slug"],
                            "to_slug": GOALS_ROOT,
                            "link_type": "",
                        },
                        {
                            "from_slug": "notes/not-a-goal",
                            "to_slug": GOALS_ROOT,
                            "link_type": "mentions",
                        },
                    ]
                ],
                "get_page": [first, sixth],
            }
        )

        result = GBrainAdapter(runner).list_goals()

        self.assertEqual(
            [goal.slug for goal in result.goals],
            ["goals/one", "goals/political-action"],
        )
        self.assertNotIn("list_pages", [tool for tool, _ in runner.calls])

    def test_reads_reciprocal_task_slugs_only_for_selected_goal_detail(self) -> None:
        goal = stored_goal("goals/one", "First goal")
        runner = FakeRunner(
            {
                "get_page": [goal],
                "get_links": [
                    [
                        {
                            "from_slug": goal["slug"],
                            "to_slug": "tasks/first",
                            "link_type": "advanced_by",
                        }
                    ]
                ],
            }
        )

        result = GBrainAdapter(runner).read_goal_relationships(goal["slug"])

        self.assertEqual(result.task_slugs, ("tasks/first",))
        self.assertEqual(
            runner.calls,
            [
                ("get_page", {"slug": goal["slug"]}),
                ("get_links", {"slug": goal["slug"]}),
            ],
        )


class InboxMutationTests(unittest.TestCase):
    def test_writes_page_and_edge_then_verifies_both(self) -> None:
        task = new_inbox_task(
            "Create the launch brief",
            datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
            "a1b2c3",
        )
        edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
            "link_source": "gtasks",
        }
        runner = FakeRunner(
            {
                "put_page": [{"slug": task.slug}],
                "get_page": [stored_page(task)],
                "add_link": [edge],
                "get_links": [[edge]],
            }
        )

        result = GBrainAdapter(runner).create_inbox(task)

        self.assertEqual(result.slug, task.slug)
        self.assertTrue(result.verified)
        tools = [tool for tool, _ in runner.calls]
        self.assertEqual(tools, ["put_page", "get_page", "add_link", "get_links"])
        content = runner.calls[0][1]["content"]
        self.assertIn('due_day: "2026-07-30"', content)
        self.assertNotIn("due_day: none", content)
        self.assertEqual(
            runner.calls[2],
            (
                "add_link",
                {
                    "from": task.slug,
                    "to": ACTIVE_ROOT,
                    "link_type": "member_of",
                    "context": "GTasks active task membership.",
                    "link_source": "gtasks",
                },
            ),
        )

    def test_surfaces_a_partial_write_if_edge_readback_fails(self) -> None:
        task = new_inbox_task(
            "Create the launch brief",
            datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
            "a1b2c3",
        )
        runner = FakeRunner(
            {
                "put_page": [{"slug": task.slug}],
                "get_page": [stored_page(task)],
                "add_link": [{}],
                "get_links": [[]],
            }
        )

        with self.assertRaises(PartialMutationError) as raised:
            GBrainAdapter(runner).create_inbox(task)

        self.assertEqual(raised.exception.slug, task.slug)
        self.assertIn("membership", str(raised.exception))


class GoalLinkMutationTests(unittest.TestCase):
    def test_adds_and_verifies_both_goal_edges_for_approved_nodes(self) -> None:
        task = new_inbox_task(
            "Create the launch brief",
            datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
            "a1b2c3",
        )
        goal = stored_goal("goals/ship-product", "Ship the product")
        goal_edge = {
            "from_slug": task.slug,
            "to_slug": goal["slug"],
            "link_type": "advances_goal",
            "link_source": "gtasks",
        }
        reciprocal_edge = {
            "from_slug": goal["slug"],
            "to_slug": task.slug,
            "link_type": "advanced_by",
            "link_source": "gtasks",
        }
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        }
                    ],
                    [
                        {
                            "from_slug": goal["slug"],
                            "to_slug": GOALS_ROOT,
                            "link_type": "",
                        }
                    ],
                ],
                "get_page": [stored_page(task), goal],
                "get_links": [[], [], [goal_edge], [reciprocal_edge]],
                "add_link": [goal_edge, reciprocal_edge],
            }
        )

        receipt = GBrainAdapter(runner).set_task_goal(task.slug, goal["slug"])

        self.assertTrue(receipt.verified)
        self.assertTrue(receipt.reciprocal_verified)
        self.assertEqual(receipt.goal_slug, goal["slug"])
        self.assertIn(
            (
                "add_link",
                {
                    "from": task.slug,
                    "to": goal["slug"],
                    "link_type": "advances_goal",
                    "context": "This task advances the linked Tony goal.",
                    "link_source": "gtasks",
                },
            ),
            runner.calls,
        )
        self.assertIn(
            (
                "add_link",
                {
                    "from": goal["slug"],
                    "to": task.slug,
                    "link_type": "advanced_by",
                    "context": "This goal is advanced by the linked GTasks task.",
                    "link_source": "gtasks",
                },
            ),
            runner.calls,
        )

    def test_unchanged_selection_repairs_a_missing_reciprocal_edge(self) -> None:
        task = new_inbox_task(
            "Create the launch brief",
            datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
            "a1b2c3",
        )
        goal = stored_goal("goals/ship-product", "Ship the product")
        forward = {
            "from_slug": task.slug,
            "to_slug": goal["slug"],
            "link_type": "advances_goal",
        }
        reverse = {
            "from_slug": goal["slug"],
            "to_slug": task.slug,
            "link_type": "advanced_by",
        }
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        }
                    ],
                    [
                        {
                            "from_slug": goal["slug"],
                            "to_slug": GOALS_ROOT,
                            "link_type": "",
                        }
                    ],
                ],
                "get_page": [stored_page(task), goal],
                "get_links": [[forward], [], [forward], [reverse]],
                "add_link": [reverse],
            }
        )

        receipt = GBrainAdapter(runner).set_task_goal(task.slug, goal["slug"])

        self.assertTrue(receipt.reconciled)
        self.assertEqual(
            [call for call in runner.calls if call[0] == "add_link"],
            [
                (
                    "add_link",
                    {
                        "from": goal["slug"],
                        "to": task.slug,
                        "link_type": "advanced_by",
                        "context": "This goal is advanced by the linked GTasks task.",
                        "link_source": "gtasks",
                    },
                )
            ],
        )

    def test_clears_both_relationship_directions_and_verifies_removal(self) -> None:
        task = new_inbox_task(
            "Create the launch brief",
            datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
            "a1b2c3",
        )
        goal_edge = {
            "from_slug": task.slug,
            "to_slug": "goals/ship-product",
            "link_type": "advances_goal",
        }
        goal = stored_goal("goals/ship-product", "Ship the product")
        reciprocal_edge = {
            "from_slug": goal["slug"],
            "to_slug": task.slug,
            "link_type": "advanced_by",
        }
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        }
                    ],
                    [
                        {
                            "from_slug": goal["slug"],
                            "to_slug": GOALS_ROOT,
                            "link_type": "",
                        }
                    ],
                ],
                "get_page": [stored_page(task), goal],
                "get_links": [[goal_edge], [reciprocal_edge], [], []],
                "remove_link": [{}, {}],
            }
        )

        receipt = GBrainAdapter(runner).set_task_goal(task.slug, None)

        self.assertTrue(receipt.verified)
        self.assertIsNone(receipt.goal_slug)
        self.assertIn(
            (
                "remove_link",
                {
                    "from": task.slug,
                    "to": "goals/ship-product",
                    "link_type": "advances_goal",
                },
            ),
            runner.calls,
        )
        self.assertIn(
            (
                "remove_link",
                {
                    "from": goal["slug"],
                    "to": task.slug,
                    "link_type": "advanced_by",
                },
            ),
            runner.calls,
        )

    def test_replaces_both_directions_after_new_pair_is_added(self) -> None:
        task = new_inbox_task(
            "Create the launch brief",
            datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
            "a1b2c3",
        )
        old_goal = stored_goal("goals/old", "Old goal")
        new_goal = stored_goal("goals/new", "New goal")
        old_forward = {
            "from_slug": task.slug,
            "to_slug": old_goal["slug"],
            "link_type": "advances_goal",
        }
        old_reverse = {
            "from_slug": old_goal["slug"],
            "to_slug": task.slug,
            "link_type": "advanced_by",
        }
        new_forward = {
            "from_slug": task.slug,
            "to_slug": new_goal["slug"],
            "link_type": "advances_goal",
        }
        new_reverse = {
            "from_slug": new_goal["slug"],
            "to_slug": task.slug,
            "link_type": "advanced_by",
        }
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        }
                    ],
                    [
                        {
                            "from_slug": old_goal["slug"],
                            "to_slug": GOALS_ROOT,
                            "link_type": "",
                        },
                        {
                            "from_slug": new_goal["slug"],
                            "to_slug": GOALS_ROOT,
                            "link_type": "",
                        },
                    ],
                ],
                "get_page": [stored_page(task), old_goal, new_goal],
                "get_links": [
                    [old_forward],
                    [old_reverse],
                    [],
                    [new_forward],
                    [],
                    [new_reverse],
                ],
                "add_link": [new_forward, new_reverse],
                "remove_link": [{}, {}],
            }
        )

        receipt = GBrainAdapter(runner).set_task_goal(task.slug, new_goal["slug"])

        self.assertEqual(receipt.goal_slug, new_goal["slug"])
        mutation_tools = [
            tool for tool, _ in runner.calls if tool in {"add_link", "remove_link"}
        ]
        self.assertEqual(
            mutation_tools,
            ["add_link", "add_link", "remove_link", "remove_link"],
        )

    def test_rolls_back_a_partial_pair_add_and_reports_verification(self) -> None:
        task = new_inbox_task(
            "Create the launch brief",
            datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
            "a1b2c3",
        )
        goal = stored_goal("goals/ship-product", "Ship the product")
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        }
                    ],
                    [
                        {
                            "from_slug": goal["slug"],
                            "to_slug": GOALS_ROOT,
                            "link_type": "",
                        }
                    ],
                ],
                "get_page": [stored_page(task), goal],
                "get_links": [[], [], [], []],
                "add_link": [
                    {},
                    GBrainCommandError("reciprocal write failed"),
                ],
                "remove_link": [{}],
            }
        )

        with self.assertRaises(PartialMutationError) as raised:
            GBrainAdapter(runner).set_task_goal(task.slug, goal["slug"])

        self.assertIn("Rollback verified", str(raised.exception))
        self.assertIn(
            (
                "remove_link",
                {
                    "from": task.slug,
                    "to": goal["slug"],
                    "link_type": "advances_goal",
                },
            ),
            runner.calls,
        )


class TaskStatusMutationTests(unittest.TestCase):
    def test_rejects_legacy_waiting_as_a_new_status_update(self) -> None:
        runner = FakeRunner({})

        with self.assertRaisesRegex(ValueError, "status must be one of"):
            GBrainAdapter(runner).set_task_status(
                "tasks/legacy-waiting",
                "waiting",
                datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
            )

        self.assertEqual(runner.calls, [])

    def test_completion_sets_local_timestamp_and_keeps_active_membership(self) -> None:
        now = datetime(2026, 7, 30, 9, 15, tzinfo=timezone(timedelta(hours=-7)))
        task = new_inbox_task("Finish GTasks", now, "a1b2c3")
        initial_page = stored_page(task)
        initial_page["frontmatter"]["captured_via"] = "capture-cli"
        active_edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        final_page = deepcopy(initial_page)
        final_page["frontmatter"]["status"] = "completed"
        final_page["frontmatter"]["completed_at"] = now.isoformat()
        final_page["frontmatter"]["updated_at"] = now.isoformat()
        runner = FakeRunner(
            {
                "get_page": [initial_page, final_page],
                "get_links": [[active_edge], [active_edge]],
                "put_page": [{"slug": task.slug}],
            }
        )

        receipt = GBrainAdapter(runner).set_task_status(task.slug, "completed", now)

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.status, "completed")
        self.assertEqual(receipt.lifecycle_root, ACTIVE_ROOT)
        self.assertEqual(receipt.completed_at, now)
        written = runner.calls[2][1]["content"]
        self.assertIn('"type": "task"', written)
        self.assertIn('"type": "member_of"', written)
        self.assertIn('"captured_via": "capture-cli"', written)
        self.assertIn("# Finish GTasks", written)
        self.assertNotIn("add_link", [tool for tool, _ in runner.calls])
        self.assertNotIn("remove_link", [tool for tool, _ in runner.calls])

    def test_status_edit_refuses_unexpected_non_task_type_before_write(self) -> None:
        task = new_inbox_task(
            "Misclassified task",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        page = stored_page(task)
        page["type"] = "concept"
        runner = FakeRunner(
            {
                "get_page": [page],
                "get_links": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        }
                    ]
                ],
            }
        )

        with self.assertRaisesRegex(ValueError, "unexpected page type concept"):
            GBrainAdapter(runner).set_task_status(
                task.slug,
                "active",
                datetime(2026, 7, 30, 12, tzinfo=timezone.utc),
            )

        self.assertNotIn("put_page", [tool for tool, _params in runner.calls])

    def test_status_edit_reconstructs_missing_frontmatter_membership(self) -> None:
        now = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
        task = new_inbox_task(
            "Legacy graph-only membership",
            now,
            "a1b2c3",
        )
        before = stored_page(task)
        before["frontmatter"].pop("links")
        after_task = replace(task, status="active")
        after = stored_page(after_task)
        after["frontmatter"]["updated_at"] = now.isoformat()
        edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "get_page": [before, after],
                "get_links": [[edge], [edge]],
                "put_page": [{"slug": task.slug}],
            }
        )

        receipt = GBrainAdapter(runner).set_task_status(
            task.slug,
            "active",
            now,
        )

        self.assertTrue(receipt.verified)
        content = next(
            params["content"]
            for tool, params in runner.calls
            if tool == "put_page"
        )
        self.assertIn('"type": "task"', content)
        self.assertIn('"type": "member_of"', content)

    def test_reopening_an_archived_task_restores_active_membership(self) -> None:
        now = datetime(2026, 7, 30, 9, 15, tzinfo=timezone(timedelta(hours=-7)))
        completed_at = datetime(2026, 7, 28, 17, 0, tzinfo=timezone(timedelta(hours=-7)))
        task = replace(
            new_inbox_task("Reopen GTasks", now, "a1b2c3"),
            status="completed",
            lifecycle_root=COMPLETED_ROOT,
            completed_at=completed_at,
        )
        initial_page = stored_page(task)
        initial_page["frontmatter"]["links"] = [
            {"to": COMPLETED_ROOT, "type": "member_of"}
        ]
        initial_page["frontmatter"]["completed_at"] = completed_at.isoformat()
        archived_edge = {
            "from_slug": task.slug,
            "to_slug": COMPLETED_ROOT,
            "link_type": "member_of",
        }
        active_edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        final_page = deepcopy(initial_page)
        final_page["frontmatter"]["status"] = "active"
        final_page["frontmatter"]["completed_at"] = None
        final_page["frontmatter"]["updated_at"] = now.isoformat()
        final_page["frontmatter"]["links"] = [
            {"to": ACTIVE_ROOT, "type": "member_of"}
        ]
        runner = FakeRunner(
            {
                "get_page": [initial_page, final_page],
                "get_links": [[archived_edge], [active_edge]],
                "put_page": [{"slug": task.slug}],
                "add_link": [active_edge],
                "remove_link": [{}],
            }
        )

        receipt = GBrainAdapter(runner).set_task_status(task.slug, "active", now)

        self.assertEqual(receipt.lifecycle_root, ACTIVE_ROOT)
        self.assertIsNone(receipt.completed_at)
        self.assertIn(("add_link", {
            "from": task.slug,
            "to": ACTIVE_ROOT,
            "link_type": "member_of",
            "context": "GTasks active task membership.",
            "link_source": "gtasks",
        }), runner.calls)
        self.assertIn(("remove_link", {
            "from": task.slug,
            "to": COMPLETED_ROOT,
            "link_type": "member_of",
        }), runner.calls)

    def test_status_write_requires_matching_page_and_link_readback(self) -> None:
        now = datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc)
        task = new_inbox_task("Finish GTasks", now, "a1b2c3")
        page = stored_page(task)
        active_edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "get_page": [page, page],
                "get_links": [[active_edge], [active_edge]],
                "put_page": [{"slug": task.slug}],
            }
        )

        with self.assertRaises(PartialMutationError) as raised:
            GBrainAdapter(runner).set_task_status(task.slug, "blocked", now)

        self.assertEqual(raised.exception.slug, task.slug)
        self.assertIn("readback", str(raised.exception).lower())


class TaskNextActionMutationTests(unittest.TestCase):
    def test_sets_next_action_and_preserves_task_identity_and_relationships(self) -> None:
        now = datetime(2026, 7, 30, 14, 15, tzinfo=timezone(timedelta(hours=-7)))
        task = new_inbox_task("Prepare interview", now, "a1b2c3")
        initial_page = stored_page(task)
        initial_page["frontmatter"]["captured_via"] = "capture-cli"
        active_edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        goal_edge = {
            "from_slug": task.slug,
            "to_slug": "goals/find-next-role",
            "link_type": "advances_goal",
        }
        final_page = deepcopy(initial_page)
        final_page["frontmatter"]["next_action"] = "Draft three STAR examples"
        final_page["frontmatter"]["updated_at"] = now.isoformat()
        runner = FakeRunner(
            {
                "get_page": [initial_page, final_page],
                "get_links": [
                    [active_edge, goal_edge],
                    [active_edge, goal_edge],
                ],
                "put_page": [{"slug": task.slug}],
            }
        )

        receipt = GBrainAdapter(runner).set_task_next_action(
            task.slug,
            "  Draft three STAR examples  ",
            now,
        )

        self.assertIsInstance(receipt, NextActionMutationReceipt)
        self.assertEqual(receipt.next_action, "Draft three STAR examples")
        self.assertTrue(receipt.verified)
        written = next(
            params["content"]
            for tool, params in runner.calls
            if tool == "put_page"
        )
        self.assertIn('"type": "task"', written)
        self.assertIn('"type": "member_of"', written)
        self.assertIn('"captured_via": "capture-cli"', written)
        self.assertIn("# Prepare interview", written)
        self.assertNotIn("add_link", [tool for tool, _params in runner.calls])
        self.assertNotIn("remove_link", [tool for tool, _params in runner.calls])

    def test_can_clear_next_action(self) -> None:
        now = datetime(2026, 7, 30, 14, 15, tzinfo=timezone.utc)
        task = replace(
            new_inbox_task("Prepare interview", now, "a1b2c3"),
            next_action="Draft three STAR examples",
        )
        initial_page = stored_page(task)
        final_page = deepcopy(initial_page)
        final_page["frontmatter"]["next_action"] = ""
        final_page["frontmatter"]["updated_at"] = now.isoformat()
        active_edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "get_page": [initial_page, final_page],
                "get_links": [[active_edge], [active_edge]],
                "put_page": [{"slug": task.slug}],
            }
        )

        receipt = GBrainAdapter(runner).set_task_next_action(task.slug, "", now)

        self.assertEqual(receipt.next_action, "")
        self.assertTrue(receipt.verified)

    def test_rolls_back_when_next_action_readback_does_not_match(self) -> None:
        now = datetime(2026, 7, 30, 14, 15, tzinfo=timezone.utc)
        task = replace(
            new_inbox_task("Prepare interview", now, "a1b2c3"),
            next_action="Review role notes",
        )
        initial_page = stored_page(task)
        mismatched_page = deepcopy(initial_page)
        mismatched_page["frontmatter"]["next_action"] = "Unexpected value"
        active_edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "get_page": [initial_page, mismatched_page, initial_page],
                "get_links": [[active_edge], [active_edge], [active_edge]],
                "put_page": [
                    {"slug": task.slug},
                    {"slug": task.slug},
                ],
            }
        )

        with self.assertRaises(PartialMutationError) as raised:
            GBrainAdapter(runner).set_task_next_action(
                task.slug,
                "Draft three STAR examples",
                now,
            )

        self.assertEqual(raised.exception.slug, task.slug)
        self.assertIn("Rollback verified", str(raised.exception))
        self.assertEqual(
            [tool for tool, _params in runner.calls].count("put_page"),
            2,
        )


class SubprocessRunnerTests(unittest.TestCase):
    @patch("gtasks.gbrain.subprocess.run")
    def test_invokes_gbrain_without_a_shell(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"slug": ACTIVE_ROOT}),
            stderr="",
        )

        result = SubprocessCommandRunner().run("get_page", {"slug": ACTIVE_ROOT})

        self.assertEqual(result, {"slug": ACTIVE_ROOT})
        positional, keyword = run.call_args
        self.assertEqual(
            positional[0],
            [
                "gbrain",
                "call",
                "get_page",
                json.dumps({"slug": ACTIVE_ROOT}, separators=(",", ":")),
            ],
        )
        self.assertNotIn("shell", keyword)


if __name__ == "__main__":
    unittest.main()
