import json
from pathlib import Path
import subprocess
import sys
import unittest

from gtasks.markdown_policy import render_task_body


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = REPO_ROOT / "skills" / "mc-add-task" / "SKILL.md"
HELPER_PATH = REPO_ROOT / "skills" / "mc-add-task" / "scripts" / "mc_add_task.py"
DETAIL = """### 用户请求

Keep the exact task wording.

### 日期说明

- Due today.

### 相关链接

- [Example](https://example.com/work)
"""
TICKET_SLUG = "tasks/fad23bf2-571f-4db0-b9f5-07ab52ae8620"


class McAddTaskSourceContractTests(unittest.TestCase):
    def test_source_skill_requires_the_unified_detail_sections(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("### 用户请求", skill)
        self.assertIn("### 日期说明", skill)
        self.assertIn("### 相关链接", skill)

    def test_source_skill_requires_canonical_ticket_readback_and_internal_route(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("read back that exact Ticket", skill)
        self.assertIn("#system-ticket/tasks%2F<ticket-uuid>", skill)
        self.assertIn("must not link to Memory Stargraph", skill)

    def test_source_skill_requires_helper_markdown_evidence(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn('markdown_contract == "unified-task-ticket-v1"', skill)
        self.assertIn("rendered_body", skill)
        self.assertIn("compiled-body equality", skill)

    def test_live_helper_reuses_adapter_verified_compiled_body(self):
        helper = HELPER_PATH.read_text(encoding="utf-8")
        self.assertNotIn("adapter._verified_system_ticket_references", helper)
        self.assertIn("rendered_body = compiled_body", helper)

    def test_live_helper_accepts_canonical_compiled_truth_projection(self):
        helper = HELPER_PATH.read_text(encoding="utf-8")
        self.assertIn('compiled_body = page.get("compiled_markdown")', helper)
        self.assertIn('compiled_body = page.get("compiled_truth")', helper)


class McAddTaskHelperDryRunTests(unittest.TestCase):
    def _dry_run(self, *arguments: str, detail: str = DETAIL) -> dict:
        result = subprocess.run(
            [
                sys.executable,
                str(HELPER_PATH),
                "--title",
                "Review unified contract",
                "--detail",
                detail,
                "--due-day",
                "2026-08-10",
                "--gtasks-repo",
                str(REPO_ROOT),
                "--dry-run",
                *arguments,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    def test_tony_dry_run_reports_shared_markdown_contract_and_rendered_body(self):
        detail = DETAIL + f"\nSystem Ticket: {TICKET_SLUG}\n"
        result = self._dry_run(detail=detail)
        self.assertEqual(result["owner"], "Tony")
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["markdown_contract"], "unified-task-ticket-v1")
        self.assertEqual(
            result["rendered_body"],
            render_task_body(
                "Review unified contract", result["detail"], {TICKET_SLUG: None}
            ),
        )
        self.assertIn(f"System Ticket unavailable: {TICKET_SLUG}", result["rendered_body"])
        self.assertNotIn("#system-ticket/", result["rendered_body"])

    def test_retired_openclaw_owner_alias_is_rejected(self):
        result = subprocess.run(
            [
                sys.executable,
                str(HELPER_PATH),
                "--title",
                "Review unified contract",
                "--detail",
                DETAIL,
                "--due-day",
                "2026-08-10",
                "--gtasks-repo",
                str(REPO_ROOT),
                "--dry-run",
                "--owner-agent",
                "tammy-oc",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown owner agent", result.stderr)

    def test_dry_run_internal_route_reports_live_verification_required(self):
        detail = (
            DETAIL
            + f"\n[Title requires canonical readback](#system-ticket/{TICKET_SLUG.replace('/', '%2F')})\n"
        )
        result = subprocess.run(
            [
                sys.executable,
                str(HELPER_PATH),
                "--title",
                "Review unified contract",
                "--detail",
                detail,
                "--due-day",
                "2026-08-10",
                "--gtasks-repo",
                str(REPO_ROOT),
                "--dry-run",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["dry_run"])
        self.assertTrue(payload["verification_required"])
        self.assertEqual(payload["unverified_system_ticket_slugs"], [TICKET_SLUG])
        self.assertIsNone(payload["rendered_body"])

    def test_dry_run_does_not_relabel_an_unsafe_link_as_ticket_verification(self):
        result = subprocess.run(
            [
                sys.executable,
                str(HELPER_PATH),
                "--title",
                "Reject unsafe Markdown",
                "--detail",
                DETAIL + "\n[unsafe](javascript:alert(1))\n",
                "--due-day",
                "2026-08-10",
                "--gtasks-repo",
                str(REPO_ROOT),
                "--dry-run",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('"verification_required": true', result.stdout)

    def test_dry_run_validates_targets_after_an_internal_route_before_deferring(self):
        route = f"#system-ticket/{TICKET_SLUG.replace('/', '%2F')}"
        detail = (
            DETAIL
            + f"\n[Needs live verification]({route})\n"
            + "\n[unsafe later](javascript:alert(1))\n"
        )
        result = subprocess.run(
            [
                sys.executable,
                str(HELPER_PATH),
                "--title",
                "Validate every target",
                "--detail",
                detail,
                "--due-day",
                "2026-08-10",
                "--gtasks-repo",
                str(REPO_ROOT),
                "--dry-run",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('"verification_required": true', result.stdout)


if __name__ == "__main__":
    unittest.main()
