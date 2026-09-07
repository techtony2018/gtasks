"""Synthetic HTTP integration for the canonical task-edit revision guard."""
from dataclasses import replace
from datetime import datetime, timezone
from urllib.parse import quote
import unittest

from gtasks.domain import EventProgress, ProgressMetric, new_task
from gtasks.job_application_binding import JOB_APPLIED_BOUND_TASK_SLUG
from tests.test_server import FakeAdapter, ServerHarness


class TaskEditRevisionApiTests(unittest.TestCase):
    def setUp(self):
        self.task = new_task(title="Synthetic revision task", identity="editguard",
            now=datetime(2026, 9, 7, tzinfo=timezone.utc))

        class RecordingAdapter(FakeAdapter):
            def edit_task(adapter, slug, **payload):
                adapter.edit_calls += 1
                return super().edit_task(slug, **payload)

        self.adapter = RecordingAdapter(active=(self.task,))
        self.adapter.edit_calls = 0
        self.harness = ServerHarness(self, self.adapter)
        self.path = "/api/tasks/" + quote(self.task.slug, safe="")

    def payload(self, revision):
        return {"title": "My retained draft", "detail": "My description",
            "priority": "normal", "due_day": "2026-09-07", "status": "planned",
            "assignee_slug": "tony", "expected_revision": revision}

    def test_exact_read_provides_revision_for_editor(self):
        status, result, _ = self.harness.request("GET", self.path)
        self.assertEqual(status, 200)
        self.assertRegex(result["task"].get("edit_revision", ""), r"^[a-f0-9]{64}$")

    def test_stale_edit_returns_conflict_without_write(self):
        status, result, _ = self.harness.request("PATCH", self.path, self.payload("0" * 64))
        self.assertEqual(status, 409)
        self.assertEqual(result["code"], "task_edit_conflict")
        self.assertEqual(result["current_task"]["title"], self.task.title)
        self.assertEqual(self.adapter.edit_calls, 0)
        self.assertEqual(self.adapter.active, (self.task,))

    def test_two_editors_second_token_cannot_overwrite_first_edit(self):
        _, opened, _ = self.harness.request("GET", self.path)
        token = opened["task"].get("edit_revision", "")
        status, _, _ = self.harness.request("PATCH", self.path, self.payload(token))
        self.assertEqual(status, 200)
        stale = {**self.payload(token), "title": "Second editor stale draft"}
        status, result, _ = self.harness.request("PATCH", self.path, stale)
        self.assertEqual(status, 409)
        self.assertEqual(self.adapter.edit_calls, 1)
        self.assertEqual(self.adapter.active[0].title, "My retained draft")

    def test_field_change_without_timestamp_change_still_conflicts(self):
        _, opened, _ = self.harness.request("GET", self.path)
        self.adapter.active = (replace(self.task, priority="high"),)
        status, _, _ = self.harness.request("PATCH", self.path,
            self.payload(opened["task"].get("edit_revision", "")))
        self.assertEqual(status, 409)
        self.assertEqual(self.adapter.edit_calls, 0)

    def test_exact_read_progress_revision_allows_unchanged_bound_metric_edit(self):
        metric = ProgressMetric.from_value({
            "kind": "count", "label": "Applications", "unit": "job_application",
            "target": 5, "current": 1, "event_binding": "job_applied",
            "auto_complete": True, "task_day": "2026-09-07",
            "timezone": "America/Los_Angeles",
        })
        task = replace(self.task, slug=JOB_APPLIED_BOUND_TASK_SLUG,
            progress_metric=metric, event_progress=EventProgress(baseline_count=1))
        self.adapter.active = (task,)
        path = "/api/tasks/" + quote(task.slug, safe="")

        _, opened, _ = self.harness.request("GET", path)
        revision = opened["task"].get("progress_metric_revision")
        payload = {
            "title": task.title, "detail": task.detail, "priority": task.priority,
            "due_day": "2026-09-07", "status": task.status,
            "assignee_slug": "tony", "expected_revision": opened["task"]["edit_revision"],
            "progress_metric_revision": revision,
            "progress_metric": {"kind": "count", "label": "Applications",
                "target": 5, "current": 1, "event_binding": "job_applied",
                "auto_complete": True},
        }
        status, _, _ = self.harness.request("PATCH", path, payload)
        self.assertRegex(revision or "", r"^[a-f0-9]{64}$")
        self.assertEqual(status, 200)

    def test_changed_bound_progress_rejects_opened_progress_revision(self):
        metric = ProgressMetric.from_value({
            "kind": "count", "label": "Applications", "unit": "job_application",
            "target": 5, "current": 1, "event_binding": "job_applied",
            "auto_complete": True, "task_day": "2026-09-07",
            "timezone": "America/Los_Angeles",
        })
        task = replace(self.task, slug=JOB_APPLIED_BOUND_TASK_SLUG,
            progress_metric=metric, event_progress=EventProgress(baseline_count=1))
        self.adapter.active = (task,)
        path = "/api/tasks/" + quote(task.slug, safe="")
        _, opened, _ = self.harness.request("GET", path)
        self.adapter.active = (replace(task, event_progress=EventProgress(
            baseline_count=1, receipt_ids=("new-event",))),)
        status, result, _ = self.harness.request("PATCH", path, {
            "title": task.title, "detail": task.detail, "priority": task.priority,
            "due_day": "2026-09-07", "status": task.status,
            "assignee_slug": "tony",
            "progress_metric_revision": opened["task"]["progress_metric_revision"],
            "progress_metric": {"kind": "count", "label": "Applications",
                "target": 5, "current": 1, "event_binding": "job_applied",
                "auto_complete": True},
        })
        self.assertEqual(status, 422)
        self.assertIn("progress changed after Edit opened", result["error"])
