import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FrontendContractTests(unittest.TestCase):
    def test_board_and_status_editor_are_first_class_navigation(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('data-view="board"', html)
        self.assertIn('data-count="board"', html)
        self.assertIn('id="task-status-select"', html)
        self.assertIn('id="task-status-save"', html)
        for status in (
            "planned",
            "active",
            "blocked",
            "completed",
            "cancelled",
        ):
            self.assertIn(f'<option value="{status}">', html)
        self.assertNotIn('<option value="waiting">', html)

        self.assertIn("function renderBoard()", javascript)
        self.assertIn("/status`", javascript)
        self.assertIn("renderBoard()", javascript)

    def test_board_has_five_exact_drop_destinations_and_retry(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        board_definition = javascript[javascript.index("const boardColumns") :]

        lane_titles = [
            board_definition.index('title: "Planned"'),
            board_definition.index('title: "In Progress"'),
            board_definition.index('title: "Blocked"'),
            board_definition.index('title: "Completed"'),
            board_definition.index('title: "Cancelled"'),
        ]
        self.assertEqual(lane_titles, sorted(lane_titles))
        self.assertIn('status: "blocked"', javascript)
        self.assertIn('task.status === "waiting" ? "blocked"', javascript)
        self.assertIn(
            '`task-state-dot ${taskUiStatus(task)}`',
            javascript,
        )
        self.assertIn("button.draggable = true", javascript)
        for event_name in ("dragstart", "dragend", "dragover", "dragleave", "drop"):
            self.assertIn(f'addEventListener("{event_name}"', javascript)
        self.assertIn('id="board-status-alert"', html)
        self.assertIn('id="board-status-retry"', html)
        self.assertIn("requestTaskStatus(", javascript)

    def test_goal_details_explain_bidirectional_links_and_explicit_repair(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn('id="goal-relationship-notice"', html)
        self.assertIn("<code>advances_goal</code>", html)
        self.assertIn("<code>advanced_by</code>", html)
        self.assertIn("legacy_one_way_tasks", javascript)
        self.assertIn("Save its current goal", javascript)
        self.assertIn("/relationships`", javascript)
        self.assertIn("advanced_by", readme)
        self.assertIn("Saving the current goal selection", readme)

    def test_about_modal_is_beneath_gbrain_status_with_release_history(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        store_end = html.index("</div>", html.index('class="store-card"'))
        about_button = html.index('id="about-button"')
        self.assertGreater(about_button, store_end)
        self.assertIn('id="sidebar-version"', html)
        self.assertIn('id="about-dialog"', html)
        self.assertIn('role="dialog"', html)
        self.assertIn('aria-modal="true"', html)
        self.assertIn('id="about-close"', html)
        self.assertIn('id="release-history"', html)
        self.assertIn('"/api/releases"', javascript)
        self.assertIn("openAboutDialog", javascript)
        self.assertIn("closeAboutDialog", javascript)
        self.assertIn('event.key === "Escape"', javascript)
        self.assertIn("release-history", javascript)

    def test_needs_attention_keeps_visible_tasks_and_offers_safe_repair(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="issue-notice"', html)
        self.assertIn("renderNeedsAttention", javascript)
        self.assertIn("task_visible", javascript)
        self.assertIn("repair_active_membership", javascript)
        self.assertIn("/relationships/active-membership", javascript)
        self.assertIn("Needs Attention", javascript)


if __name__ == "__main__":
    unittest.main()
