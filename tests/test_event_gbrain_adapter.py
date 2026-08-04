import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from gtasks.event_queue.contract import parse_event
from gtasks.event_queue.gbrain_adapter import (
    GBrainJobAppliedAdapter,
    GBrainPageNotFound,
    SubprocessCommandRunner,
)
from gtasks.event_queue.handler import HandlerFailure, JobAppliedHandler, application_slug
from gtasks.gbrain import GBrainCommandError
from tests.test_event_contract import valid_event


TASK_SLUG = "tasks/apply-to-five-jobs-2026-07-30"
NOW = datetime(2026, 7, 30, 18, 0, tzinfo=timezone.utc)


def parsed_event(index: int = 42):
    raw = valid_event()
    raw["event_id"] = f"evt_adapter_{index}"
    raw["idempotency_key"] = f"career-path:linkedin:job-{index}:applied"
    raw["payload"]["application_identity"]["job_id"] = f"job-{index}"
    raw["payload"]["job_snapshot"]["url"] = (
        f"https://www.linkedin.com/jobs/view/job-{index}"
    )
    return parse_event(json.dumps(raw).encode(), "gtasks.events.job_applied.v1")


class StatefulRunner:
    def __init__(self) -> None:
        self.pages = {
            TASK_SLUG: {
                "slug": TASK_SLUG,
                "type": "task",
                "title": "Apply to five jobs",
                "frontmatter": {
                    "type": "task",
                    "title": "Apply to five jobs",
                    "summary": "Apply to five jobs",
                    "detail": "Daily job-application quota.",
                    "status": "active",
                    "priority": "normal",
                    "next_action": "",
                    "due_day": "2026-07-30",
                    "scheduled_day": None,
                    "inbox": False,
                    "completed_at": None,
                    "links": [
                        {
                            "to": "collections/tonys-tasks",
                            "type": "member_of",
                        }
                    ],
                    "progress_metric": {
                        "kind": "count",
                        "label": "Five applications today",
                        "unit": "job_application",
                        "target": 5,
                        "current": 0,
                        "event_binding": "job_applied",
                        "auto_complete": True,
                        "task_day": "2026-07-30",
                        "timezone": "America/Los_Angeles",
                    },
                    "event_progress": {
                        "evidence_slugs": [],
                        "receipt_ids": [],
                    },
                },
                "compiled_truth": "# Apply to five jobs\n",
            }
        }
        self.links = {
            (TASK_SLUG, "collections/tonys-tasks", "member_of")
        }
        self.link_sources = {}
        self.calls = []
        self.raise_for_missing_page = False

    def run(self, tool: str, params: dict):
        self.calls.append((tool, params))
        if tool == "get_page":
            page = self.pages.get(params["slug"])
            if (
                page is None
                and self.raise_for_missing_page
                and params["slug"] == "tasks/deleted-unrelated-task"
            ):
                raise GBrainPageNotFound("page_not_found")
            return page
        if tool == "get_backlinks":
            slug = params["slug"]
            return [
                {
                    "from_slug": source,
                    "to_slug": target,
                    "link_type": link_type,
                    "link_source": self.link_sources.get((source, target, link_type)),
                }
                for source, target, link_type in sorted(self.links)
                if target == slug
            ]
        if tool == "get_links":
            slug = params["slug"]
            return [
                {
                    "from_slug": source,
                    "to_slug": target,
                    "link_type": link_type,
                    "link_source": self.link_sources.get((source, target, link_type)),
                }
                for source, target, link_type in sorted(self.links)
                if source == slug or target == slug
            ]
        if tool == "add_link":
            key = (
                params.get("from", params.get("from_slug")),
                params.get("to", params.get("to_slug")),
                params["link_type"],
            )
            self.links.add(key)
            self.link_sources[key] = params.get("link_source")
            return {}
        if tool == "remove_link":
            key = (
                params.get("from", params.get("from_slug")),
                params.get("to", params.get("to_slug")),
                params["link_type"],
            )
            self.links.discard(key)
            self.link_sources.pop(key, None)
            return {}
        if tool == "put_page":
            content = params["content"]
            lines = content.splitlines()
            end = lines.index("---", 1)
            frontmatter = dict(
                self.pages.get(params["slug"], {}).get("frontmatter", {})
            )
            for line in lines[1:end]:
                if line.startswith(" ") or ": " not in line:
                    continue
                key, raw = line.split(": ", 1)
                try:
                    parsed_key = json.loads(key)
                except json.JSONDecodeError:
                    parsed_key = key
                try:
                    parsed_value = json.loads(raw)
                except json.JSONDecodeError:
                    parsed_value = raw
                frontmatter[parsed_key] = parsed_value
            body = "\n".join(lines[end + 1 :]).lstrip()
            self.pages[params["slug"]] = {
                "slug": params["slug"],
                "type": frontmatter.get("type"),
                "title": frontmatter.get("title"),
                "frontmatter": frontmatter,
                "compiled_truth": body,
            }
            return {"slug": params["slug"]}
        raise AssertionError(f"unexpected tool: {tool}")


class GBrainJobAppliedAdapterTests(unittest.TestCase):
    def make_handler(self, root: Path, runner: StatefulRunner) -> JobAppliedHandler:
        return JobAppliedHandler(
            adapter=GBrainJobAppliedAdapter(runner),
            clock=lambda: NOW,
            task_slug=TASK_SLUG,
        )

    def test_opted_in_metric_mutation_and_exact_readback_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runner = StatefulRunner()
            handler = self.make_handler(Path(temp), runner)

            handler.handle(parsed_event())
            handler.handle(parsed_event())

        slug = application_slug("linkedin", "job-42")
        frontmatter = runner.pages[TASK_SLUG]["frontmatter"]
        self.assertEqual(frontmatter["progress_metric"]["current"], 1)
        self.assertEqual(
            frontmatter["progress_metric"]["label"],
            "Five applications today",
        )
        self.assertEqual(frontmatter["event_progress"]["evidence_slugs"], [slug])
        self.assertEqual(frontmatter["event_progress"]["receipt_ids"], ["evt_adapter_42"])
        self.assertIn((slug, TASK_SLUG, "evidence_for"), runner.links)
        self.assertIn((TASK_SLUG, slug, "has_evidence"), runner.links)
        self.assertEqual(
            {tool for tool, _params in runner.calls},
            {"get_page", "get_links", "put_page", "add_link"},
        )

    def test_dangling_unrelated_active_link_does_not_block_bound_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runner = StatefulRunner()
            runner.raise_for_missing_page = True
            runner.links.add(
                (
                    "tasks/deleted-unrelated-task",
                    "collections/tonys-tasks",
                    "member_of",
                )
            )

            self.make_handler(Path(temp), runner).handle(parsed_event())

        self.assertEqual(
            runner.pages[TASK_SLUG]["frontmatter"]["progress_metric"]["current"],
            1,
        )

    def test_fifth_distinct_event_uses_canonical_completion_readback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runner = StatefulRunner()
            handler = self.make_handler(Path(temp), runner)

            for index in range(1, 6):
                handler.handle(parsed_event(index))

        frontmatter = runner.pages[TASK_SLUG]["frontmatter"]
        self.assertEqual(frontmatter["progress_metric"]["current"], 5)
        self.assertEqual(frontmatter["status"], "completed")
        self.assertEqual(frontmatter["completed_at"], NOW.isoformat())
        self.assertEqual(len(frontmatter["event_progress"]["evidence_slugs"]), 5)
        self.assertEqual(len(frontmatter["event_progress"]["receipt_ids"]), 5)
        self.assertIn("put_page", {tool for tool, _params in runner.calls})

    def test_delayed_event_preserves_custom_target_and_manual_baseline(self) -> None:
        runner = StatefulRunner()
        frontmatter = runner.pages[TASK_SLUG]["frontmatter"]
        frontmatter["due_day"] = "2026-08-05"
        frontmatter["progress_metric"].update(
            {
                "target": 30,
                "current": 24,
                "task_day": "2026-08-05",
            }
        )
        frontmatter["event_progress"] = {
            "baseline_count": 24,
            "evidence_slugs": [],
            "receipt_ids": [],
        }
        handler = JobAppliedHandler(
            adapter=GBrainJobAppliedAdapter(runner),
            clock=lambda: datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc),
            task_slug=TASK_SLUG,
        )

        effect = handler.handle(parsed_event())

        updated = runner.pages[TASK_SLUG]["frontmatter"]
        self.assertEqual(effect.task_day.isoformat(), "2026-08-05")
        self.assertEqual(effect.baseline_count, 24)
        self.assertEqual(effect.resulting_progress, 25)
        self.assertEqual(effect.target, 30)
        self.assertEqual(updated["progress_metric"]["current"], 25)
        self.assertEqual(updated["progress_metric"]["target"], 30)
        self.assertEqual(updated["event_progress"]["baseline_count"], 24)
        self.assertEqual(updated["event_progress"]["receipt_ids"], ["evt_adapter_42"])

    def test_malformed_opt_in_metric_fails_without_title_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runner = StatefulRunner()
            runner.pages[TASK_SLUG]["frontmatter"]["progress_metric"]["target"] = 0
            handler = self.make_handler(Path(temp), runner)

            with self.assertRaisesRegex(HandlerFailure, "quota_task_contract_invalid"):
                handler.handle(parsed_event())

    def test_unbound_manual_metric_is_ignored_and_never_auto_completes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runner = StatefulRunner()
            metric = runner.pages[TASK_SLUG]["frontmatter"]["progress_metric"]
            for key in ("event_binding", "task_day", "timezone"):
                metric.pop(key)
            metric["current"] = 5
            metric["label"] = "Manual count"
            runner.pages[TASK_SLUG]["frontmatter"].pop("event_progress")
            handler = self.make_handler(Path(temp), runner)

            with self.assertRaisesRegex(HandlerFailure, "quota_task_contract_invalid"):
                handler.handle(parsed_event())

        frontmatter = runner.pages[TASK_SLUG]["frontmatter"]
        self.assertEqual(frontmatter["status"], "active")
        self.assertEqual(frontmatter["progress_metric"]["current"], 5)

    def test_existing_application_identity_collision_fails_without_write(self) -> None:
        runner = StatefulRunner()
        record = parsed_event().payload
        slug = application_slug(
            record.application_identity.job_source,
            record.application_identity.job_id,
        )
        runner.pages[slug] = {
            "slug": slug,
            "type": "concept",
            "title": "User note",
            "frontmatter": {"type": "concept", "title": "User note"},
            "compiled_truth": "Do not overwrite me.\n",
        }

        with self.assertRaisesRegex(HandlerFailure, "application_identity_conflict"):
            self.make_handler(Path("."), runner).handle(parsed_event())

        self.assertEqual(runner.pages[slug]["compiled_truth"], "Do not overwrite me.\n")
        self.assertNotIn("put_page", [tool for tool, _ in runner.calls])

    def test_existing_application_update_preserves_user_body_and_extra_fields(self) -> None:
        runner = StatefulRunner()
        event = parsed_event()
        handler = self.make_handler(Path("."), runner)
        handler.handle(event)
        slug = application_slug("linkedin", "job-42")
        runner.pages[slug]["frontmatter"]["custom_user_field"] = "keep"
        runner.pages[slug]["frontmatter"]["location"] = "Old"
        runner.pages[slug]["compiled_truth"] = "# User-maintained evidence\n\nKeep this body.\n"

        updated = parsed_event()
        handler.handle(updated)

        page = runner.pages[slug]
        self.assertEqual(page["frontmatter"]["custom_user_field"], "keep")
        self.assertEqual(page["frontmatter"]["location"], "San Francisco, CA")
        self.assertEqual(page["compiled_truth"].rstrip("\n"), "# User-maintained evidence\n\nKeep this body.")

    def test_canonical_command_error_is_not_retyped(self) -> None:
        class Failing:
            def run(self, _tool, _params):
                raise GBrainCommandError("write readback failed")

        runner = SubprocessCommandRunner()
        runner._runner = Failing()

        with self.assertRaisesRegex(GBrainCommandError, "write readback failed"):
            runner.run("put_page", {"slug": "applications/test", "content": "x"})


if __name__ == "__main__":
    unittest.main()
