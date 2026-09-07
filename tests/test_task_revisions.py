from copy import deepcopy
from datetime import datetime, timezone
import unittest
import yaml

import gtasks.gbrain as gbrain_module
from gtasks.domain import (
    ACTIVE_ROOT, QA_FIXTURES_ROOT, AgentProfile, new_inbox_task, new_project,
)
from gtasks.task_revisions import (
    TASK_EDIT_REVISION_FIELDS,
    TaskEditConflict,
    task_edit_revision,
)


class RecordingRunner:
    def __init__(self, page: dict, links: list[dict]) -> None:
        self.page = page
        self.links = links
        self.calls: list[tuple[str, dict]] = []

    def run(self, tool: str, params: dict):
        self.calls.append((tool, params))
        if tool == "get_page":
            return deepcopy(self.page)
        if tool == "get_links":
            return deepcopy(self.links)
        raise AssertionError(f"unexpected write: {tool}")


class SuccessfulEditRunner(RecordingRunner):
    def __init__(self, page: dict, links: list[dict], final_page: dict) -> None:
        super().__init__(page, links)
        self.final_page = final_page
        self.page_reads = 0

    def run(self, tool: str, params: dict):
        self.calls.append((tool, params))
        if tool == "get_page":
            self.page_reads += 1
            return deepcopy(self.page if self.page_reads == 1 else self.final_page)
        if tool == "get_links":
            return deepcopy(self.links)
        if tool == "put_page":
            return {"slug": self.page["slug"]}
        raise AssertionError(f"unexpected operation: {tool}")


class PersistingEditRunner(RecordingRunner):
    """Persist the adapter's real page/link mutations for honest readback."""

    def run(self, tool: str, params: dict):
        self.calls.append((tool, params))
        if tool == "get_page":
            return deepcopy(self.page)
        if tool == "get_links":
            return deepcopy(self.links)
        if tool == "put_page":
            content = params["content"]
            frontmatter = yaml.safe_load(content.split("---", 2)[1])
            self.page["frontmatter"] = frontmatter
            self.page["title"] = frontmatter.get("title", frontmatter.get("summary", self.page["title"]))
            self.page["compiled_truth"] = content.split("\n---\n", 1)[1].strip()
            return {"slug": self.page["slug"]}
        if tool == "add_link":
            self.links.append({"from_slug": params["from"], "to_slug": params["to"],
                "link_type": params["link_type"]})
            return {}
        if tool == "remove_link":
            self.links = [edge for edge in self.links if not (
                edge.get("from_slug") == params["from"]
                and edge.get("to_slug") == params["to"]
                and edge.get("link_type") == params["link_type"])]
            return {}
        raise AssertionError(f"unexpected operation: {tool}")


class RelationshipEditAdapter(gbrain_module.GBrainAdapter):
    def __init__(self, runner, projects=()):
        super().__init__(runner)
        self._projects = tuple(projects)

    def list_projects(self):
        return gbrain_module.ProjectRead(projects=self._projects)

    def list_agent_profiles(self):
        return gbrain_module.AgentRead(agents=(AgentProfile(
            slug="agents/tammy", name="Tammy", title="Codex Agent", summary="",
            work_root="collections/tammys-tasks", default_goal_slugs=(),
        ),))


def task_page() -> tuple[object, dict, list[dict]]:
    now = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
    task = new_inbox_task("Preserve my draft", now, "revision-guard")
    page = {
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
            "scheduled_day": None,
            "inbox": task.inbox,
            "completed_at": None,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
            "links": [{"to": ACTIVE_ROOT, "type": "member_of"}],
        },
    }
    links = [
        {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
    ]
    return task, page, links


class TaskEditRevisionGuardTests(unittest.TestCase):
    def test_revision_is_stable_and_ignores_display_and_derived_fields(self) -> None:
        payload = {field: f"value-{field}" for field in TASK_EDIT_REVISION_FIELDS}
        baseline = task_edit_revision(payload)
        reordered = dict(reversed(list(payload.items())))
        reordered.update(
            {
                "display_markdown": "<h1>different display</h1>",
                "todos": [{"text": "derived enrichment"}],
                "editor_open": True,
                "revision": "old-token",
            }
        )

        self.assertEqual(task_edit_revision(reordered), baseline)
        self.assertEqual(len(baseline), 64)

    def test_each_managed_field_changes_revision_without_updated_at_change(self) -> None:
        payload = {field: None for field in TASK_EDIT_REVISION_FIELDS}
        payload.update(
            {
                "slug": "tasks/revision-guard",
                "title": "Title",
                "summary": "Summary",
                "detail": "Detail",
                "status": "active",
                "priority": "normal",
                "next_action": "Act",
                "due_day": "2026-09-07",
                "due_at": "2026-09-07T17:00:00+00:00",
                "scheduled_day": "2026-09-07",
                "project": "projects/one",
                "goal": "goals/one",
                "parent": "tasks/parent",
                "owner_agent": "agents/tammy",
                "lifecycle_root": "collections/tammys-tasks",
                "progress_metric": {"current": 1, "target": 3},
                "event_progress": {"baseline_current": 1, "events": []},
                "completed_at": None,
                "updated_at": "2026-09-07T12:00:00+00:00",
            }
        )
        baseline = task_edit_revision(payload)

        for field in TASK_EDIT_REVISION_FIELDS:
            with self.subTest(field=field):
                changed = deepcopy(payload)
                changed[field] = f"changed-{field}"
                self.assertNotEqual(task_edit_revision(changed), baseline)

    def test_stale_revision_rejects_before_any_write(self) -> None:
        task, page, links = task_page()
        runner = RecordingRunner(page, links)
        adapter = gbrain_module.GBrainAdapter(runner)

        try:
            adapter.edit_task(
                task.slug,
                title=task.title,
                detail=task.detail,
                priority=task.priority,
                due_day=task.due_day,
                next_action=task.next_action,
                project_slug=None,
                goal_slug=None,
                status=task.status,
                assignee_slug="tony",
                progress_metric=None,
                event_progress=None,
                handoff_reason="",
                now=datetime(2026, 9, 7, 12, 5, tzinfo=timezone.utc),
                expected_revision="stale-revision",
            )
        except Exception as exc:
            conflict = exc
        else:
            self.fail("stale revision was accepted")

        self.assertIsInstance(conflict, TaskEditConflict)
        self.assertIn("draft", str(conflict).casefold())
        self.assertIn("refresh", str(conflict).casefold())
        self.assertEqual(
            [tool for tool, _params in runner.calls],
            ["get_page", "get_links"],
        )

    def test_normalized_readback_revision_allows_verified_existing_edit(self) -> None:
        task, page, links = task_page()
        now = datetime(2026, 9, 7, 12, 5, tzinfo=timezone.utc)
        final_page = deepcopy(page)
        final_page["frontmatter"].update(
            {
                "type": "task",
                "title": task.title,
                "summary": task.title,
                "detail": task.detail,
                "priority": task.priority,
                "due_day": task.due_day.isoformat(),
                "next_action": task.next_action,
                "next_action_history": [],
                "progress_metric": None,
                "event_progress": None,
                "updated_at": now.isoformat(),
            }
        )
        runner = SuccessfulEditRunner(page, links, final_page)
        adapter = gbrain_module.GBrainAdapter(runner)
        read_adapter = gbrain_module.GBrainAdapter(RecordingRunner(page, links))
        expected_revision = task_edit_revision(
            read_adapter.get_task_api_payload(task.slug)
        )

        receipt = adapter.edit_task(
            task.slug,
            title=task.title,
            detail=task.detail,
            priority=task.priority,
            due_day=task.due_day,
            next_action=task.next_action,
            project_slug=None,
            goal_slug=None,
            status=task.status,
            assignee_slug="tony",
            progress_metric=None,
            event_progress=None,
            handoff_reason="",
            now=now,
            expected_revision=expected_revision,
        )

        self.assertTrue(receipt.verified)
        self.assertEqual(
            [tool for tool, _params in runner.calls],
            ["get_page", "get_links", "put_page", "get_page", "get_links"],
        )

    def test_unverified_legacy_project_state_is_actually_removed_without_losing_other_links(self) -> None:
        for explicit_project_field in (False, True):
            with self.subTest(explicit_project_field=explicit_project_field):
                task, page, links = task_page()
                page["frontmatter"]["links"].extend([
                    {"to": "projects/obsolete", "type": "member_of"},
                    {"to": "tasks/keep-me", "type": "blocks"},
                ])
                if explicit_project_field:
                    page["frontmatter"]["project"] = "projects/obsolete"
                links.append({"from_slug": task.slug, "to_slug": "tasks/keep-me",
                    "link_type": "blocks"})
                expected_revision = task_edit_revision(
                    gbrain_module.GBrainAdapter(RecordingRunner(page, links))
                    .get_task_api_payload(task.slug)
                )
                runner = PersistingEditRunner(deepcopy(page), deepcopy(links))
                receipt = gbrain_module.GBrainAdapter(runner).edit_task(
                    task.slug, title=task.title, detail=task.detail, priority=task.priority,
                    due_day=task.due_day, next_action=task.next_action, project_slug=None,
                    goal_slug=None, status=task.status, assignee_slug="tony",
                    progress_metric=None, event_progress=None, handoff_reason="",
                    now=datetime(2026, 9, 7, 12, 5, tzinfo=timezone.utc),
                    expected_revision=expected_revision,
                )
                self.assertTrue(receipt.verified)
                self.assertIsNone(runner.page["frontmatter"].get("project"))
                self.assertNotIn("projects/obsolete", str(runner.page["frontmatter"]["links"]))
                self.assertIn({"to": "tasks/keep-me", "type": "blocks"},
                    runner.page["frontmatter"]["links"])
                self.assertIn({"from_slug": task.slug, "to_slug": "tasks/keep-me",
                    "link_type": "blocks"}, runner.links)

    def test_full_edit_preserves_agent_and_qa_scope_page_and_graph_membership(self) -> None:
        cases = (
            ("collections/tammys-tasks", "agents/tammy"),
            (QA_FIXTURES_ROOT, "tony"),
        )
        for scope, assignee in cases:
            with self.subTest(scope=scope):
                task, page, links = task_page()
                page["frontmatter"]["links"][0]["to"] = scope
                links[0]["to_slug"] = scope
                if scope == QA_FIXTURES_ROOT:
                    page["frontmatter"].update({
                        "qa_fixture": True, "qa_owner": "iteration2",
                        "qa_release": "V231",
                    })
                else:
                    page["frontmatter"]["links"].append(
                        {"to": "agents/tammy", "type": "assigned_to"}
                    )
                    links.append({"from_slug": task.slug, "to_slug": "agents/tammy",
                        "link_type": "assigned_to"})
                adapter = RelationshipEditAdapter(PersistingEditRunner(page, links))
                expected_revision = task_edit_revision(adapter.get_task_api_payload(task.slug))
                receipt = adapter.edit_task(
                    task.slug, title=task.title, detail=task.detail,
                    priority=task.priority, due_day=task.due_day,
                    next_action=task.next_action, project_slug=None, goal_slug=None,
                    status=task.status, assignee_slug=assignee, progress_metric=None,
                    event_progress=None, handoff_reason="",
                    now=datetime(2026, 9, 7, 12, 5, tzinfo=timezone.utc),
                    expected_revision=expected_revision,
                )
                self.assertTrue(receipt.verified)
                self.assertIn({"to": scope, "type": "member_of"},
                    adapter.runner.page["frontmatter"]["links"])
                self.assertIn({"from_slug": task.slug, "to_slug": scope,
                    "link_type": "member_of"}, adapter.runner.links)

    def test_verified_project_transitions_reconcile_exact_page_and_graph_state(self) -> None:
        now = datetime(2026, 9, 7, 12, 5, tzinfo=timezone.utc)
        old_project = new_project("Old", now, "old-project")
        new_project_value = new_project("New", now, "new-project")
        for requested in (None, new_project_value.slug):
            with self.subTest(requested=requested):
                task, page, links = task_page()
                assignee = "tony"
                scope = ACTIVE_ROOT
                if requested is not None:
                    scope = "collections/tammys-tasks"
                    assignee = "agents/tammy"
                    page["frontmatter"]["links"][0]["to"] = scope
                    links[0]["to_slug"] = scope
                    page["frontmatter"]["links"].append(
                        {"to": assignee, "type": "assigned_to"}
                    )
                    links.append({"from_slug": task.slug, "to_slug": assignee,
                        "link_type": "assigned_to"})
                page["frontmatter"]["project"] = old_project.slug
                page["frontmatter"]["links"].extend([
                    {"to": old_project.slug, "type": "member_of"},
                    {"to": "tasks/keep-me", "type": "blocks"},
                ])
                links.extend([
                    {"from_slug": task.slug, "to_slug": old_project.slug,
                        "link_type": "member_of"},
                    {"from_slug": task.slug, "to_slug": "tasks/keep-me",
                        "link_type": "blocks"},
                ])
                runner = PersistingEditRunner(page, links)
                adapter = RelationshipEditAdapter(
                    runner, projects=(old_project, new_project_value))
                expected_revision = task_edit_revision(adapter.get_task_api_payload(task.slug))
                receipt = adapter.edit_task(
                    task.slug, title=task.title, detail=task.detail,
                    priority=task.priority, due_day=task.due_day,
                    next_action=task.next_action, project_slug=requested, goal_slug=None,
                    status=task.status, assignee_slug=assignee, progress_metric=None,
                    event_progress=None, handoff_reason="", now=now,
                    expected_revision=expected_revision,
                )
                expected_projects = [requested] if requested else []
                page_projects = [link["to"] for link in runner.page["frontmatter"]["links"]
                    if link.get("type") == "member_of" and link.get("to", "").startswith("projects/")]
                graph_projects = [edge["to_slug"] for edge in runner.links
                    if edge.get("from_slug") == task.slug
                    and edge.get("link_type") == "member_of"
                    and edge.get("to_slug", "").startswith("projects/")]
                self.assertTrue(receipt.verified)
                self.assertEqual(runner.page["frontmatter"].get("project"), requested)
                self.assertEqual(page_projects, expected_projects)
                self.assertEqual(graph_projects, expected_projects)
                self.assertIn({"to": scope, "type": "member_of"},
                    runner.page["frontmatter"]["links"])
                self.assertIn({"from_slug": task.slug, "to_slug": scope,
                    "link_type": "member_of"}, runner.links)
                self.assertIn({"to": "tasks/keep-me", "type": "blocks"},
                    runner.page["frontmatter"]["links"])
                self.assertIn({"from_slug": task.slug, "to_slug": "tasks/keep-me",
                    "link_type": "blocks"}, runner.links)


if __name__ == "__main__":
    unittest.main()
