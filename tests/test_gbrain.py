import json
import subprocess
import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from gtasks.domain import (
    ACTIVE_ROOT,
    COMPLETED_ROOT,
    EventProgress,
    GOALS_ROOT,
    PROJECTS_ROOT,
    PROPOSALS_ROOT,
    SYSTEM_TICKETS_ROOT,
    SystemTicket,
    ProgressMetric,
    new_goal,
    new_inbox_task,
    new_project,
    new_task,
)
from gtasks.gbrain import (
    GBrainAdapter,
    GBrainCommandError,
    GBrainProtocolError,
    GoalLinkReceipt,
    NextActionMutationReceipt,
    PartialMutationError,
    SubprocessCommandRunner,
    _render_preserved_page,
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


class EntityTypePreservationTests(unittest.TestCase):
    def test_generic_preserved_update_refuses_task_to_concept_downgrade(self) -> None:
        task = new_inbox_task(
            "Keep canonical task type",
            datetime(2026, 7, 31, tzinfo=timezone.utc),
            "type01",
        )
        page = stored_page(task)
        changed = deepcopy(page["frontmatter"])
        changed["type"] = "concept"

        with self.assertRaisesRegex(
            GBrainProtocolError,
            "refusing to change canonical page type",
        ):
            _render_preserved_page(page, changed)

    def test_generic_preserved_update_serializes_the_read_task_type(self) -> None:
        task = new_inbox_task(
            "Preserve canonical task type",
            datetime(2026, 7, 31, tzinfo=timezone.utc),
            "type02",
        )
        page = stored_page(task)

        content = _render_preserved_page(page, page["frontmatter"])

        self.assertIn('"type": "task"', content)


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
            "links": [{"to": PROJECTS_ROOT, "type": "member_of"}],
        },
    }


def stored_projects_root() -> dict:
    return {
        "slug": PROJECTS_ROOT,
        "type": "collection",
        "title": "Tony's Projects",
        "compiled_truth": "# Tony's Projects",
        "frontmatter": {
            "owner": "people/tony-guan",
            "status": "active",
            "visibility": "private",
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


class StatefulTaskRunner:
    def __init__(self, page: dict, links: list[dict]) -> None:
        self.page = deepcopy(page)
        self.links = deepcopy(links)
        self.calls: list[tuple[str, dict]] = []

    def run(self, tool: str, params: dict) -> object:
        self.calls.append((tool, deepcopy(params)))
        if tool == "get_page":
            return deepcopy(self.page)
        if tool == "get_links":
            return deepcopy(self.links)
        if tool == "put_page":
            content = params["content"]
            lines = content.splitlines()
            end = lines.index("---", 1)
            frontmatter = {}
            for line in lines[1:end]:
                key, raw = line.split(": ", 1)
                frontmatter[json.loads(key)] = json.loads(raw)
            self.page = {
                **self.page,
                "type": frontmatter.get("type"),
                "title": frontmatter.get("title", self.page.get("title")),
                "frontmatter": frontmatter,
                "compiled_truth": "\n".join(lines[end + 1 :]).lstrip(),
            }
            return {"slug": params["slug"]}
        raise AssertionError(f"unexpected tool: {tool}")


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


class AgentProfileReadTests(unittest.TestCase):
    def test_reads_tony_board_avatar_from_canonical_person_attachment(self) -> None:
        runner = FakeRunner(
            {
                "get_page": [
                    {
                        "slug": "people/tony-guan",
                        "type": "person",
                        "title": "Tony Guan",
                        "compiled_truth": (
                            "# Tony Guan\n\n## Attachments\n\n"
                            "![Tony Profile](people/tony-guan/Tony Profile.jpeg)"
                        ),
                        "frontmatter": {},
                    }
                ]
            }
        )

        profile = GBrainAdapter(runner).get_tony_profile()

        self.assertEqual(profile["slug"], "people/tony-guan")
        self.assertEqual(profile["avatar"], {
            "kind": "attachment",
            "value": "/media/people/tony-guan/Tony%20Profile.jpeg",
        })

    def test_timmy_uses_the_exact_lowercase_slug_and_rejects_a_non_agent_page(self) -> None:
        timmy_page = {
            "slug": "agents/timmy",
            "type": "concept",
            "title": "Agent Timmy",
            "compiled_truth": "# Agent Timmy",
            "frontmatter": {},
        }
        runner = FakeRunner(
            {
                "list_pages": [[]],
                "get_page": [timmy_page],
                "get_links": [[]],
            }
        )

        with self.assertRaisesRegex(Exception, "agents/timmy is not an agent page"):
            GBrainAdapter(runner).get_agent_profile("agents/timmy")

        self.assertIn(("get_page", {"slug": "agents/timmy"}), runner.calls)
        self.assertNotIn(("get_page", {"slug": "agents/Timmy"}), runner.calls)

    def test_avatar_write_reasserts_agent_type_after_attachment_snapshot(self) -> None:
        page = {
            "slug": "agents/timmy",
            "type": "agent",
            "title": "Agent Timmy",
            "compiled_truth": "# Agent Timmy",
            "frontmatter": {"work_root": "collections/timmys-tasks"},
        }
        stored = deepcopy(page)
        stored["frontmatter"] = {
            "work_root": "collections/timmys-tasks",
            "avatar": {"kind": "attachment", "value": "/media/agents/timmy/avatar.jpg"},
        }
        runner = FakeRunner(
            {
                "list_pages": [[page]],
                "get_page": [page, page, stored],
                "get_links": [[], [], []],
                "put_page": [{"slug": "agents/timmy"}],
            }
        )

        profile = GBrainAdapter(runner).set_agent_avatar(
            "agents/timmy", "/media/agents/timmy/avatar.jpg"
        )

        self.assertEqual(profile.avatar_value, "/media/agents/timmy/avatar.jpg")
        content = next(
            params["content"]
            for tool, params in runner.calls
            if tool == "put_page"
        )
        self.assertIn('"type": "agent"', content)


class ProjectPersistenceTests(unittest.TestCase):
    def test_lists_only_typed_g_tasks_scope_members_without_tasks(self) -> None:
        project = new_project(
            "Interview preparation",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        edge = {
            "from_slug": project.slug,
            "to_slug": PROJECTS_ROOT,
            "link_type": "member_of",
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

    def test_reports_malformed_typed_scope_members_for_projects_attention(self) -> None:
        project = new_project(
            "Malformed scoped project",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        edge = {
            "from_slug": project.slug,
            "to_slug": PROJECTS_ROOT,
            "link_type": "member_of",
        }
        malformed_page = stored_project(project)
        malformed_page["type"] = "note"
        runner = FakeRunner(
            {
                "get_backlinks": [[edge]],
                "get_page": [malformed_page],
                "get_links": [[edge]],
            }
        )

        result = GBrainAdapter(runner).list_projects()

        self.assertEqual(result.projects, ())
        self.assertEqual([issue.slug for issue in result.issues], [project.slug])
        self.assertIn("not a project page", result.issues[0].message)

    def test_excludes_old_projects_without_typed_scope_membership(self) -> None:
        scoped = new_project(
            "Scoped project",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        old = new_project(
            "Old unrelated project",
            datetime(2025, 1, 1, tzinfo=timezone.utc),
            "d4e5f6",
        )
        scoped_edge = {
            "from_slug": scoped.slug,
            "to_slug": PROJECTS_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        scoped_edge,
                        {
                            "from_slug": old.slug,
                            "to_slug": PROJECTS_ROOT,
                            "link_type": "involved_in",
                        },
                        {
                            "from_slug": old.slug,
                            "to_slug": "collections/other-projects",
                            "link_type": "member_of",
                        },
                    ]
                ],
                "get_page": [stored_project(scoped)],
                "get_links": [[scoped_edge]],
            }
        )

        result = GBrainAdapter(runner).list_projects()

        self.assertEqual([item.slug for item in result.projects], [scoped.slug])
        self.assertNotIn(old.slug, [item.slug for item in result.projects])

    def test_create_project_requires_page_and_collection_link_readback(self) -> None:
        project = new_project(
            "Interview preparation",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        edge = {
            "from_slug": project.slug,
            "to_slug": PROJECTS_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "put_page": [{"slug": project.slug}],
                "get_page": [stored_projects_root(), stored_project(project)],
                "add_link": [{}],
                "get_links": [[edge]],
            }
        )

        receipt = GBrainAdapter(runner).create_project(project)

        self.assertTrue(receipt.verified)
        self.assertIn("type: project", runner.calls[1][1]["content"])
        self.assertIn(("add_link", {
            "from": project.slug,
            "to": PROJECTS_ROOT,
            "link_type": "member_of",
            "context": "This project is explicitly owned by GTasks.",
            "link_source": "gtasks",
        }), runner.calls)

    def test_new_project_initializes_missing_scope_only_on_explicit_create(self) -> None:
        project = new_project(
            "ERFA PAC",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        edge = {
            "from_slug": project.slug,
            "to_slug": PROJECTS_ROOT,
            "link_type": "member_of",
        }
        missing = GBrainCommandError(
            "GBrain tool get_page failed: page_not_found"
        )
        runner = FakeRunner(
            {
                "get_page": [
                    missing,
                    stored_projects_root(),
                    stored_project(project),
                ],
                "put_page": [
                    {"slug": PROJECTS_ROOT},
                    {"slug": project.slug},
                ],
                "add_link": [{}],
                "get_links": [[edge]],
            }
        )

        receipt = GBrainAdapter(runner).create_project(project)

        self.assertTrue(receipt.verified)
        self.assertEqual(
            runner.calls[:3],
            [
                ("get_page", {"slug": PROJECTS_ROOT}),
                (
                    "put_page",
                    {
                        "slug": PROJECTS_ROOT,
                        "content": runner.calls[1][1]["content"],
                    },
                ),
                ("get_page", {"slug": PROJECTS_ROOT}),
            ],
        )
        self.assertIn("type: collection", runner.calls[1][1]["content"])
        self.assertIn("title: Tony's Projects", runner.calls[1][1]["content"])
        self.assertIn("type: member_of", runner.calls[3][1]["content"])

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


class GoalMutationTests(unittest.TestCase):
    def test_create_goal_writes_and_reads_typed_collection_membership(self) -> None:
        goal = new_goal(
            title="Launch the pilot",
            outcome="The pilot is live.",
            success_criteria="Ten users complete the workflow.",
            strategy="Ship one validated slice each week.",
            review_cadence="weekly",
            constraints="Keep customer data local.",
            now=datetime(2026, 7, 30, tzinfo=timezone.utc),
            identity="a1b2c3",
        )
        page = stored_goal(goal.slug, goal.title)
        page["frontmatter"].update(
            {
                "outcome": goal.outcome,
                "success_criteria": goal.success_criteria,
                "target_day": goal.target_day.isoformat(),
                "strategy": goal.strategy,
                "review_cadence": goal.review_cadence,
                "constraints": goal.constraints,
                "links": [{"to": GOALS_ROOT, "type": "member_of"}],
            }
        )
        edge = {
            "from_slug": goal.slug,
            "to_slug": GOALS_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "put_page": [{"slug": goal.slug}],
                "get_page": [page],
                "add_link": [{}],
                "get_links": [[edge]],
            }
        )

        receipt = GBrainAdapter(runner).create_goal(goal)

        self.assertTrue(receipt.verified)
        self.assertIn("type: goal", runner.calls[0][1]["content"])
        self.assertIn("type: member_of", runner.calls[0][1]["content"])

    def test_pause_preserves_goal_type_content_and_relationships(self) -> None:
        page = stored_goal("goals/pause-me", "Pause me")
        edge = {
            "from_slug": page["slug"],
            "to_slug": "tasks/linked",
            "link_type": "advanced_by",
        }
        paused_page = deepcopy(page)
        paused_page["frontmatter"]["status"] = "paused"
        runner = FakeRunner(
            {
                "get_page": [page, paused_page],
                "get_links": [[edge], [edge]],
                "put_page": [{"slug": page["slug"]}],
            }
        )

        receipt = GBrainAdapter(runner).set_goal_paused(page["slug"])

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.goal.status, "paused")
        written = next(
            params["content"] for tool, params in runner.calls if tool == "put_page"
        )
        self.assertIn('"type": "goal"', written)
        self.assertIn('"status": "paused"', written)

    def test_delete_without_linked_tasks_is_verified_recoverable_soft_delete(self) -> None:
        page = stored_goal("goals/delete-me", "Delete me")
        deleted_page = {**page, "deleted_at": "2026-07-30T20:00:00Z"}
        runner = FakeRunner(
            {
                "get_page": [page, deleted_page],
                "get_links": [[], []],
                "get_backlinks": [[], []],
                "delete_page": [{"slug": page["slug"]}],
            }
        )

        receipt = GBrainAdapter(runner).delete_goal(page["slug"])

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.recoverable_until_hours, 72)
        self.assertIn(
            (
                "get_page",
                {"slug": page["slug"], "include_deleted": True},
            ),
            runner.calls,
        )

    def test_delete_unlinks_both_directions_without_deleting_linked_task(self) -> None:
        page = stored_goal("goals/delete-linked", "Delete linked")
        task_slug = "tasks/keep-me"
        outgoing = {
            "from_slug": page["slug"],
            "to_slug": task_slug,
            "link_type": "advanced_by",
        }
        incoming = {
            "from_slug": task_slug,
            "to_slug": page["slug"],
            "link_type": "advances_goal",
        }
        deleted_page = {**page, "deleted_at": "2026-07-30T20:00:00Z"}
        runner = FakeRunner(
            {
                "get_page": [page, deleted_page],
                "get_links": [[outgoing], []],
                "get_backlinks": [[incoming], []],
                "delete_page": [{"slug": page["slug"]}],
            }
        )

        class DeleteAdapter(GBrainAdapter):
            def __init__(self) -> None:
                super().__init__(runner)
                self.goal_updates: list[tuple[str, str | None]] = []

            def set_task_goal(
                self,
                task_slug: str,
                goal_slug: str | None,
            ) -> GoalLinkReceipt:
                self.goal_updates.append((task_slug, goal_slug))
                return GoalLinkReceipt(
                    task_slug=task_slug,
                    goal_slug=goal_slug,
                    verified=True,
                )

        adapter = DeleteAdapter()
        receipt = adapter.delete_goal(page["slug"])

        self.assertEqual(adapter.goal_updates, [(task_slug, None)])
        self.assertEqual(receipt.removed_task_links, (task_slug,))
        self.assertNotIn(
            "delete_page",
            [
                tool
                for tool, params in runner.calls
                if params.get("slug") == task_slug
            ],
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
    def test_verified_full_task_creation_path_is_available(self) -> None:
        self.assertTrue(
            callable(getattr(GBrainAdapter(FakeRunner({})), "create_task", None))
        )

    def test_verified_duplicate_creation_path_is_available(self) -> None:
        self.assertTrue(
            callable(getattr(GBrainAdapter(FakeRunner({})), "duplicate_task", None))
        )

    def test_full_creation_serializes_and_reads_back_optional_metric(self) -> None:
        metric = ProgressMetric.from_value(
            {
                "kind": "count",
                "label": "Job applications",
                "unit": "job_application",
                "target": 5,
                "current": 2,
                "event_binding": None,
                "auto_complete": False,
                "task_day": None,
                "timezone": None,
            }
        )
        task = new_task(
            title="Apply for five companies",
            progress_metric=metric,
            now=datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
            identity="metric1",
        )
        page = stored_page(task)
        page["frontmatter"]["progress_metric"] = metric.to_dict()
        edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "put_page": [{"slug": task.slug}],
                "get_page": [page, page],
                "add_link": [edge],
                "get_links": [[edge], [edge]],
            }
        )

        receipt = GBrainAdapter(runner).create_task(task)

        self.assertTrue(receipt.verified)
        content = runner.calls[0][1]["content"]
        self.assertIn("progress_metric:", content)
        self.assertIn('"label": "Job applications"', content)
        self.assertIn('"unit": "job_application"', content)
        self.assertIn('"current": 2', content)
        self.assertIn("event_progress: null", content)

    def test_full_creation_verifies_project_and_bidirectional_goal_links(self) -> None:
        project = new_project(
            "Job Search",
            datetime(2026, 7, 30, 9, tzinfo=timezone.utc),
            "project1",
        )
        goal_page = stored_goal("goals/get-a-job", "Get a job")
        task = new_task(
            title="Apply for five companies",
            project=project.slug,
            goal=goal_page["slug"],
            now=datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
            identity="linked1",
        )
        page = stored_page(task)
        page["frontmatter"]["project"] = project.slug
        page["frontmatter"]["links"].append(
            {"to": project.slug, "type": "member_of"}
        )

        class CreationRunner:
            def __init__(self) -> None:
                self.calls = []
                self.links = {
                    (project.slug, PROJECTS_ROOT, "member_of"),
                }

            def run(self, tool: str, params: dict) -> object:
                self.calls.append((tool, params))
                if tool == "put_page":
                    return {"slug": task.slug}
                if tool == "get_page":
                    if params["slug"] == project.slug:
                        return stored_project(project)
                    if params["slug"] == goal_page["slug"]:
                        return goal_page
                    if params["slug"] == task.slug:
                        return page
                if tool == "add_link":
                    self.links.add(
                        (
                            params["from"],
                            params["to"],
                            params["link_type"],
                        )
                    )
                    return {}
                if tool == "get_links":
                    slug = params["slug"]
                    return [
                        {
                            "from_slug": source,
                            "to_slug": target,
                            "link_type": link_type,
                        }
                        for source, target, link_type in sorted(self.links)
                        if source == slug or target == slug
                    ]
                raise AssertionError(f"unexpected {tool}: {params}")

        runner = CreationRunner()

        receipt = GBrainAdapter(runner).create_task(task)

        self.assertTrue(receipt.verified)
        self.assertIn((task.slug, project.slug, "member_of"), runner.links)
        self.assertIn((task.slug, goal_page["slug"], "advances_goal"), runner.links)
        self.assertIn((goal_page["slug"], task.slug, "advanced_by"), runner.links)

    def test_agent_creation_verifies_one_scope_and_one_assigned_to_edge(
        self,
    ) -> None:
        now = datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc)
        base = new_task(
            title="Prepare a wellbeing update",
            next_action="Draft three bullets",
            now=now,
            identity="agent12",
        )
        agent_slug = "agents/toddy"
        work_root = "collections/toddys-tasks"
        task = replace(
            base,
            lifecycle_root=work_root,
            owner_agent=agent_slug,
        )
        page = stored_page(task)
        page["frontmatter"]["links"] = [
            {"to": work_root, "type": "member_of"},
            {"to": agent_slug, "type": "assigned_to"},
        ]
        page["frontmatter"]["created_at"] = now.isoformat()
        page["frontmatter"]["updated_at"] = now.isoformat()
        agent_page = {
            "slug": agent_slug,
            "type": "agent",
            "title": "Agent Toddy",
            "compiled_truth": "# Agent Toddy",
            "frontmatter": {},
        }

        class AgentCreationRunner:
            def __init__(self) -> None:
                self.links: set[tuple[str, str, str]] = set()

            def run(self, tool: str, params: dict) -> object:
                if tool == "list_pages":
                    return [agent_page]
                if tool == "put_page":
                    return {"slug": task.slug}
                if tool == "get_page":
                    return agent_page if params["slug"] == agent_slug else page
                if tool == "get_links":
                    slug = params["slug"]
                    return [
                        {
                            "from_slug": source,
                            "to_slug": target,
                            "link_type": link_type,
                        }
                        for source, target, link_type in sorted(self.links)
                        if source == slug or target == slug
                    ]
                if tool == "add_link":
                    self.links.add(
                        (
                            params["from"],
                            params["to"],
                            params["link_type"],
                        )
                    )
                    return {}
                raise AssertionError(f"unexpected {tool}: {params}")

        runner = AgentCreationRunner()

        receipt = GBrainAdapter(runner).create_agent_task(task, agent_slug)

        self.assertTrue(receipt.verified)
        self.assertEqual(
            {
                edge
                for edge in runner.links
                if edge[2] == "member_of"
                and edge[1] in {
                    ACTIVE_ROOT,
                    COMPLETED_ROOT,
                    "collections/toddys-tasks",
                    "collections/timmys-tasks",
                    "collections/tammys-tasks",
                }
            },
            {(task.slug, work_root, "member_of")},
        )
        self.assertIn((task.slug, agent_slug, "assigned_to"), runner.links)

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


class AgentReadTests(unittest.TestCase):
    def test_fails_closed_and_reports_non_task_proposed_agent_work(self) -> None:
        agent_pages = [
            {
                "slug": f"agents/{name}",
                "type": "agent",
                "title": f"Agent {name.title()}",
                "compiled_truth": "",
                "frontmatter": {},
            }
            for name in ("toddy", "timmy", "tammy")
        ]
        malformed = {
            "slug": "collections/toddys-tasks/malformed-proposal",
            "type": "concept",
            "title": "Malformed proposed work",
            "frontmatter": {"status": "proposed"},
        }
        runner = FakeRunner(
            {
                "get_page": [*agent_pages, malformed],
                "get_links": [[], [], [], []],
                "get_backlinks": [
                    [{
                        "from_slug": malformed["slug"],
                        "to_slug": "collections/toddys-tasks",
                        "link_type": "member_of",
                    }],
                    [],
                    [],
                ],
            }
        )

        result = GBrainAdapter(runner).list_agent_work()

        self.assertEqual(result.tasks, ())
        self.assertEqual(result.issues[0].slug, malformed["slug"])
        self.assertIn("proposed agent task must have canonical type task", result.issues[0].message)
        self.assertIn("not shown on Board", result.issues[0].impact)

    def test_reads_profiles_and_typed_agent_work_without_title_guessing(self) -> None:
        now = datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc)
        task = new_task(
            title="Draft wellbeing check-in",
            now=now,
            identity="agent01",
        )
        task_page = stored_page(task)
        task_page["frontmatter"]["links"] = [
            {"to": "collections/toddys-tasks", "type": "member_of"},
            {"to": "agents/toddy", "type": "assigned_to"},
        ]
        agent_pages = [
            {
                "slug": f"agents/{name}",
                "type": "agent",
                "title": f"Agent {name.title()}",
                "compiled_truth": f"# Agent {name.title()}",
                "frontmatter": {},
            }
            for name in ("toddy", "timmy", "tammy")
        ]
        runner = FakeRunner(
            {
                "get_page": [
                    agent_pages[0],
                    agent_pages[1],
                    agent_pages[2],
                    task_page,
                ],
                "get_links": [
                    [
                        {
                            "from_slug": "agents/toddy",
                            "to_slug": "goals/happier-and-healthier",
                            "link_type": "default_agent_for",
                        }
                    ],
                    [],
                    [],
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": "collections/toddys-tasks",
                            "link_type": "member_of",
                        },
                        {
                            "from_slug": task.slug,
                            "to_slug": "agents/toddy",
                            "link_type": "assigned_to",
                        },
                    ],
                ],
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": "collections/toddys-tasks",
                            "link_type": "member_of",
                        },
                        {
                            "from_slug": "tasks/ignored-untyped",
                            "to_slug": "collections/toddys-tasks",
                            "link_type": "",
                        },
                    ],
                    [],
                    [],
                ],
            }
        )

        result = GBrainAdapter(runner).list_agent_work()

        self.assertEqual(len(result.tasks), 1)
        self.assertEqual(result.tasks[0]["slug"], task.slug)
        self.assertEqual(result.tasks[0]["owner"]["name"], "Toddy")
        self.assertEqual(
            result.tasks[0]["lifecycle_root"],
            "collections/toddys-tasks",
        )
        self.assertFalse(result.tasks[0]["read_only"])
        self.assertNotIn(
            "tasks/ignored-untyped",
            [item["slug"] for item in result.tasks],
        )

    def test_reports_malformed_typed_agent_member_without_hiding_other_work(
        self,
    ) -> None:
        agent_pages = [
            {
                "slug": f"agents/{name}",
                "type": "agent",
                "title": f"Agent {name.title()}",
                "compiled_truth": "",
                "frontmatter": {},
            }
            for name in ("toddy", "timmy", "tammy")
        ]
        runner = FakeRunner(
            {
                "get_page": [
                    agent_pages[0],
                    agent_pages[1],
                    agent_pages[2],
                    {
                        "slug": "notes/not-a-task",
                        "type": "concept",
                        "title": "Malformed",
                        "frontmatter": {},
                    },
                ],
                "get_links": [[], [], [], []],
                "get_backlinks": [
                    [
                        {
                            "from_slug": "notes/not-a-task",
                            "to_slug": "collections/toddys-tasks",
                            "link_type": "member_of",
                        }
                    ],
                    [],
                    [],
                ],
            }
        )

        result = GBrainAdapter(runner).list_agent_work()

        self.assertEqual(result.tasks, ())
        self.assertEqual(result.issues[0].slug, "notes/not-a-task")
        self.assertIn("not shown on Board", result.issues[0].impact)


class ProposalReadTests(unittest.TestCase):
    def test_excludes_decided_legacy_proposals_from_active_review(self) -> None:
        slug = "proposals/tammy-decided-legacy"
        page = {
            "slug": slug,
            "type": "task_proposal",
            "title": "Historical Tammy proposal",
            "compiled_truth": "# Historical Tammy proposal",
            "frontmatter": {
                "status": "approved",
                "recipient": "agent",
                "proposing_agent": "agents/tammy",
                "rationale": "Historical compatibility record.",
                "proposed_next_step": "Use the linked canonical task.",
                "due_day": "2026-07-31",
                "submitted_at": "2026-07-30T14:00:00-07:00",
                "updated_at": "2026-07-30T15:00:00-07:00",
            },
        }
        edges = [
            {"from_slug": slug, "to_slug": PROPOSALS_ROOT, "link_type": "member_of"},
            {"from_slug": slug, "to_slug": "agents/tammy", "link_type": "proposed_by"},
        ]
        runner = FakeRunner(
            {
                "get_backlinks": [[edges[0]]],
                "get_page": [page],
                "get_links": [edges],
            }
        )

        result = GBrainAdapter(runner).list_proposals()

        self.assertEqual(result.proposals, ())

    def test_reads_only_typed_proposals_and_keeps_malformed_items_visible(self) -> None:
        slug = "proposals/toddy-wellbeing-check-in"
        page = {
            "slug": slug,
            "type": "task_proposal",
            "title": "Schedule a wellbeing check-in",
            "compiled_truth": "# Schedule a wellbeing check-in",
            "frontmatter": {
                "status": "proposed",
                "recipient": "tony",
                "proposing_agent": "agents/toddy",
                "rationale": "This supports the wellbeing goal.",
                "proposed_next_step": "Choose a time tomorrow.",
                "due_day": "2026-07-31",
                "submitted_at": "2026-07-30T14:00:00-07:00",
                "updated_at": "2026-07-30T14:00:00-07:00",
                "links": [
                    {"to": PROPOSALS_ROOT, "type": "member_of"},
                    {"to": "agents/toddy", "type": "proposed_by"},
                    {
                        "to": "goals/happier-and-healthier",
                        "type": "serves_goal",
                    },
                ],
            },
        }
        edges = [
            {
                "from_slug": slug,
                "to_slug": PROPOSALS_ROOT,
                "link_type": "member_of",
            },
            {
                "from_slug": slug,
                "to_slug": "agents/toddy",
                "link_type": "proposed_by",
            },
            {
                "from_slug": slug,
                "to_slug": "goals/happier-and-healthier",
                "link_type": "serves_goal",
            },
        ]
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        edges[0],
                        {
                            "from_slug": "proposals/legacy-untyped",
                            "to_slug": PROPOSALS_ROOT,
                            "link_type": "",
                        },
                    ]
                ],
                "get_page": [page],
                "get_links": [edges],
            }
        )

        result = GBrainAdapter(runner).list_proposals()

        self.assertEqual([item.slug for item in result.proposals], [slug])
        self.assertEqual(result.issues, ())


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

    def test_agent_status_update_preserves_owner_scope_and_task_identity(
        self,
    ) -> None:
        now = datetime(
            2026,
            7,
            30,
            9,
            15,
            tzinfo=timezone(timedelta(hours=-7)),
        )
        base = new_task(
            title="Prepare a wellbeing update",
            now=now,
            identity="agent34",
        )
        task = replace(
            base,
            lifecycle_root="collections/toddys-tasks",
            owner_agent="agents/toddy",
        )
        page = stored_page(task)
        page["frontmatter"].update(
            {
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "links": [
                    {
                        "to": "collections/toddys-tasks",
                        "type": "member_of",
                    },
                    {"to": "agents/toddy", "type": "assigned_to"},
                ],
            }
        )
        links = [
            {
                "from_slug": task.slug,
                "to_slug": "collections/toddys-tasks",
                "link_type": "member_of",
            },
            {
                "from_slug": task.slug,
                "to_slug": "agents/toddy",
                "link_type": "assigned_to",
            },
        ]
        runner = StatefulTaskRunner(page, links)

        receipt = GBrainAdapter(runner).set_task_status(
            task.slug,
            "active",
            now + timedelta(minutes=5),
        )

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.status, "active")
        self.assertEqual(receipt.task.owner_agent, "agents/toddy")
        self.assertEqual(
            receipt.task.lifecycle_root,
            "collections/toddys-tasks",
        )
        self.assertEqual(runner.page["type"], "task")
        self.assertEqual(runner.links, links)

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
        self.assertEqual(receipt.task.status, "completed")
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


class SystemTicketAdapterTests(unittest.TestCase):
    def test_reads_pre_ui_system_ticket_from_its_existing_task_detail(self) -> None:
        page = {
            "slug": "tasks/calendar-view-selected-task-highlight",
            "type": "task",
            "title": "Highlight selected task in Calendar View",
            "frontmatter": {
                "status": "planned", "priority": "normal",
                "detail": "Tony requested a visible selected state.",
                "target_subsystem": "mission-control-calendar",
                "acceptance_criteria": "The state is accessible.",
            },
            "created_at": "2026-07-31T17:00:00+00:00",
        }
        edge = {"from_slug": page["slug"], "to_slug": SYSTEM_TICKETS_ROOT, "link_type": "member_of"}

        ticket = SystemTicket.from_page(page, [edge])

        self.assertEqual(ticket.status, "planned")
        self.assertEqual(ticket.target_subsystem, "mission_control")
        self.assertEqual(ticket.verbatim_request, "Tony requested a visible selected state.")
    def test_create_ticket_writes_task_type_typed_membership_and_exact_readback(self) -> None:
        now = datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc)
        ticket = SystemTicket(
            slug="tasks/system-tickets/calendar-selection-a1b2c3",
            title="Improve Calendar selection",
            status="planned",
            verbatim_request="Highlight the selected Calendar task.",
            target_subsystem="mission_control",
            priority="high",
            acceptance_criteria="The selected task is clear.",
            created_at=now,
            updated_at=now,
        )
        root = {"slug": SYSTEM_TICKETS_ROOT, "type": "collection"}
        stored = {
            "slug": ticket.slug,
            "type": "task",
            "title": ticket.title,
            "frontmatter": {
                "type": "task", "title": ticket.title, "status": "planned",
                "priority": "high", "verbatim_request": ticket.verbatim_request,
                "target_subsystem": "mission_control",
                "acceptance_criteria": ticket.acceptance_criteria,
                "linked_evidence": [], "implementation_receipts": [], "qa_receipts": [],
                "created_at": now.isoformat(), "updated_at": now.isoformat(),
                "links": [{"to": SYSTEM_TICKETS_ROOT, "type": "member_of"}],
            },
        }
        edge = {"from_slug": ticket.slug, "to_slug": SYSTEM_TICKETS_ROOT, "link_type": "member_of"}
        runner = FakeRunner({
            "get_page": [root, stored], "put_page": [{"slug": ticket.slug}],
            "add_link": [{}], "get_links": [[edge]],
        })

        receipt = GBrainAdapter(runner).create_system_ticket(ticket)

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.slug, ticket.slug)
        content = next(params["content"] for tool, params in runner.calls if tool == "put_page")
        self.assertIn("type: task", content)
        self.assertIn(SYSTEM_TICKETS_ROOT, content)
        self.assertEqual(
            next(params for tool, params in runner.calls if tool == "add_link")["link_type"],
            "member_of",
        )

    def test_nightly_selector_returns_only_the_oldest_planned_ticket(self) -> None:
        first = SystemTicket("tasks/system-tickets/first-aaaaaa", "First", "planned", "A request", "mission_control", "normal", created_at=datetime(2026, 7, 30, tzinfo=timezone.utc))
        active = SystemTicket("tasks/system-tickets/active-bbbbbb", "Active", "active", "Another request", "mission_control", "normal", created_at=datetime(2026, 7, 29, tzinfo=timezone.utc))
        second = SystemTicket("tasks/system-tickets/second-cccccc", "Second", "planned", "Later request", "career_path", "normal", created_at=datetime(2026, 7, 31, tzinfo=timezone.utc))
        def page(ticket: SystemTicket) -> dict:
            return {"slug": ticket.slug, "type": "task", "title": ticket.title, "frontmatter": {"type":"task", "title":ticket.title, "status":ticket.status, "priority":ticket.priority, "verbatim_request":ticket.verbatim_request, "target_subsystem":ticket.target_subsystem, "acceptance_criteria":"", "linked_evidence":[], "implementation_receipts":[], "qa_receipts":[], "created_at":ticket.created_at.isoformat(), "links":[{"to":SYSTEM_TICKETS_ROOT,"type":"member_of"}]}}
        edges = lambda ticket: [{"from_slug":ticket.slug,"to_slug":SYSTEM_TICKETS_ROOT,"link_type":"member_of"}]
        runner = FakeRunner({
            "get_backlinks": [[*edges(first), *edges(active), *edges(second)]],
            "get_page": [page(first), page(active), page(second)],
            "get_links": [edges(first), edges(active), edges(second)],
        })

        selected = GBrainAdapter(runner).next_planned_system_ticket()

        self.assertEqual(selected.slug, first.slug)


class TaskProgressMetricMutationTests(unittest.TestCase):
    def test_verified_metric_mutation_path_is_available(self) -> None:
        adapter = GBrainAdapter(FakeRunner({}))

        self.assertTrue(
            callable(getattr(adapter, "set_task_progress_metric", None))
        )

    def test_event_progress_mutation_path_is_available(self) -> None:
        adapter = GBrainAdapter(FakeRunner({}))

        self.assertTrue(
            callable(getattr(adapter, "apply_task_progress_event", None))
        )

    def test_sets_metric_with_exact_readback_and_preserves_task_identity(self) -> None:
        now = datetime(
            2026,
            7,
            30,
            14,
            15,
            tzinfo=timezone(timedelta(hours=-7)),
        )
        task = new_inbox_task("Apply for five companies", now, "a1b2c3")
        page = stored_page(task)
        edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = StatefulTaskRunner(page, [edge])
        metric = ProgressMetric.from_value(
            {
                "kind": "count",
                "label": "Job applications",
                "unit": "job_application",
                "target": 5,
                "current": 0,
                "event_binding": "job_applied",
                "auto_complete": True,
                "task_day": "2026-07-30",
                "timezone": "America/Los_Angeles",
            }
        )
        event_progress = EventProgress()

        receipt = GBrainAdapter(runner).set_task_progress_metric(
            task.slug,
            metric,
            event_progress,
            now,
        )

        self.assertIsNotNone(receipt)
        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.task.progress_metric, metric)
        self.assertEqual(receipt.task.event_progress, event_progress)
        self.assertEqual(runner.page["type"], "task")
        self.assertEqual(runner.page["frontmatter"]["links"], page["frontmatter"]["links"])
        self.assertEqual(
            runner.links,
            [edge],
        )

    def test_rejects_event_metric_for_a_different_task_day_before_write(
        self,
    ) -> None:
        now = datetime(
            2026,
            7,
            30,
            14,
            15,
            tzinfo=timezone(timedelta(hours=-7)),
        )
        task = new_task(
            title="Apply for five companies",
            due_day=date(2026, 7, 30),
            now=now,
            identity="a1b2c3",
        )
        page = stored_page(task)
        edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = StatefulTaskRunner(page, [edge])
        metric = ProgressMetric.from_value(
            {
                "kind": "count",
                "label": "Job applications",
                "unit": "job_application",
                "target": 5,
                "current": 0,
                "event_binding": "job_applied",
                "auto_complete": True,
                "task_day": "2026-07-31",
                "timezone": "America/Los_Angeles",
            }
        )

        with self.assertRaisesRegex(ValueError, "must match the task due day"):
            GBrainAdapter(runner).set_task_progress_metric(
                task.slug,
                metric,
                EventProgress(),
                now,
            )

        self.assertEqual(
            [tool for tool, _params in runner.calls].count("put_page"),
            0,
        )

    def test_five_distinct_events_increment_once_and_then_complete(self) -> None:
        now = datetime(
            2026,
            7,
            30,
            14,
            15,
            tzinfo=timezone(timedelta(hours=-7)),
        )
        metric = ProgressMetric.from_value(
            {
                "kind": "count",
                "label": "Job applications",
                "unit": "job_application",
                "target": 5,
                "current": 0,
                "event_binding": "job_applied",
                "auto_complete": True,
                "task_day": "2026-07-30",
                "timezone": "America/Los_Angeles",
            }
        )
        task = new_task(
            title="Apply for five companies",
            progress_metric=metric,
            event_progress=EventProgress(),
            now=now,
            identity="a1b2c3",
        )
        page = stored_page(task)
        page["frontmatter"]["progress_metric"] = metric.to_dict()
        page["frontmatter"]["event_progress"] = EventProgress().to_dict()
        edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = StatefulTaskRunner(page, [edge])
        adapter = GBrainAdapter(runner)

        for index in range(1, 5):
            receipt = adapter.apply_task_progress_event(
                task.slug,
                event_binding="job_applied",
                evidence_slug=f"applications/{index}",
                receipt_id=f"evt-{index}",
                now=now + timedelta(minutes=index),
            )
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt.task.progress_metric.current, index)
            self.assertEqual(receipt.task.status, "planned")

        writes_before_duplicate = len(
            [tool for tool, _params in runner.calls if tool == "put_page"]
        )
        duplicate = adapter.apply_task_progress_event(
            task.slug,
            event_binding="job_applied",
            evidence_slug="applications/4",
            receipt_id="evt-4",
            now=now + timedelta(minutes=5),
        )
        writes_after_duplicate = len(
            [tool for tool, _params in runner.calls if tool == "put_page"]
        )
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(writes_before_duplicate, writes_after_duplicate)

        completed = adapter.apply_task_progress_event(
            task.slug,
            event_binding="job_applied",
            evidence_slug="applications/5",
            receipt_id="evt-5",
            now=now + timedelta(minutes=6),
        )

        self.assertTrue(completed.verified)
        self.assertFalse(completed.duplicate)
        self.assertEqual(completed.task.progress_metric.current, 5)
        self.assertEqual(completed.task.status, "completed")
        self.assertIsNotNone(completed.task.completed_at)
        self.assertEqual(
            completed.task.event_progress.receipt_ids,
            ("evt-1", "evt-2", "evt-3", "evt-4", "evt-5"),
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
