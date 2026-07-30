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
        self.assertIn("card.draggable = true", javascript)
        for event_name in ("dragstart", "dragend", "dragover", "dragleave", "drop"):
            self.assertIn(f'addEventListener("{event_name}"', javascript)
        self.assertIn('id="board-status-alert"', html)
        self.assertIn('id="board-status-retry"', html)
        self.assertIn("requestTaskStatus(", javascript)
        self.assertIn("editableTaskStatuses", javascript)
        self.assertIn("Move ${task.title || task.summary} to another status", javascript)

    def test_board_same_status_drop_is_a_silent_no_op(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        move_body = javascript[
            javascript.index("async function moveBoardTask")
            : javascript.index("async function saveTaskStatus")
        ]

        self.assertIn("if (task.status === status) return", move_body)
        self.assertLess(
            move_body.index("if (task.status === status) return"),
            move_body.index('phase: "saving"'),
        )

    def test_status_success_reconciles_authoritative_task_without_full_reload(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        move_body = javascript[
            javascript.index("async function moveBoardTask")
            : javascript.index("function nextActionErrorMessage")
        ]

        self.assertIn("result.receipt?.task", javascript)
        self.assertIn("reconcileVerifiedTask(receipt.task)", move_body)
        self.assertNotIn("await loadTasks()", move_body)
        self.assertIn("rebuildDerivedTaskViews", javascript)

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

    def test_goals_have_verified_create_pause_and_delete_confirmations(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="new-goal-dialog"', html)
        self.assertIn('id="new-goal-title"', html)
        self.assertIn('id="new-goal-target"', html)
        self.assertIn('id="goal-pause-button"', html)
        self.assertIn('id="goal-delete-button"', html)
        self.assertIn('id="goal-confirm-dialog"', html)
        self.assertIn("New Goal", javascript)
        self.assertIn('fetch("/api/goals"', javascript)
        self.assertIn("openGoalConfirmation", javascript)
        self.assertIn("soft-deleted and recoverable in GBrain for 72 hours", javascript)
        self.assertIn("linked tasks", javascript)
        self.assertIn('goal.status === "paused"', javascript)

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

    def test_logs_are_adjacent_read_only_filtered_and_accessible(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        store_end = html.index("</div>", html.index('class="store-card"'))
        logs_button = html.index('id="logs-button"')
        about_button = html.index('id="about-button"')
        self.assertGreater(logs_button, store_end)
        self.assertLess(logs_button, about_button)
        for element_id in (
            "logs-dialog",
            "logs-close",
            "logs-severity",
            "logs-component",
            "logs-refresh",
            "operational-log-list",
            "logs-load-more",
            "queue-reader-status",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn('aria-labelledby="logs-title"', html)
        self.assertIn("no event payloads", html)
        self.assertIn("private task content", html)
        self.assertIn("Inbox warning", html)
        self.assertIn('fetch(`/api/logs?', javascript)
        self.assertIn("URLSearchParams", javascript)
        self.assertIn("state.logsNextCursor", javascript)
        self.assertIn("renderQueueReaderStatus", javascript)
        self.assertIn("GTasks remains available", javascript)
        self.assertIn(
            'event.key === "Escape" && elements.logsDialog.open',
            javascript,
        )
        self.assertNotIn('fetch("/api/logs", {\n      method:', javascript)

    def test_task_detail_has_an_accessible_next_action_editor(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="task-next-action-input"', html)
        self.assertIn('maxlength="240"', html)
        self.assertIn('id="task-next-action-save"', html)
        self.assertIn('id="task-next-action-error"', html)
        self.assertIn("single next concrete physical or actionable step", html)
        self.assertIn("/next-action`", javascript)
        self.assertIn("saveTaskNextAction", javascript)
        self.assertIn('event.key === "Enter"', javascript)

    def test_auto_refresh_is_visible_coalesced_and_visibility_aware(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="auto-refresh-label"', html)
        self.assertIn("Auto-refresh every 30 minutes", html)
        self.assertIn("const AUTO_REFRESH_MINUTES = 30", javascript)
        self.assertIn(
            "AUTO_REFRESH_INTERVAL_MS = AUTO_REFRESH_MINUTES * 60 * 1000",
            javascript,
        )
        self.assertIn(
            "if (state.tasksLoadPromise) return state.tasksLoadPromise",
            javascript,
        )
        self.assertIn('reason: "automatic"', javascript)
        self.assertIn('document.addEventListener("visibilitychange"', javascript)
        self.assertIn("document.hidden", javascript)
        self.assertIn("state.refreshDeferred", javascript)
        self.assertIn("scheduleAutoRefresh", javascript)

    def test_projects_have_durable_create_and_separate_assignment_flows(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="new-project-dialog"', html)
        self.assertIn('id="new-project-title"', html)
        self.assertIn("Create project", html)
        self.assertIn('id="task-project-select"', html)
        self.assertIn('id="task-project-save"', html)
        self.assertIn("Assignment is separate from project creation", html)
        self.assertIn('fetch("/api/projects"', javascript)
        self.assertIn("submitNewProject", javascript)
        self.assertIn("/project`", javascript)
        self.assertIn("No tasks assigned yet", javascript)
        self.assertIn("state.projectIssues", javascript)
        self.assertIn("payload.issues", javascript)
        projects_body = javascript[
            javascript.index("function renderProjectsView()")
            : javascript.index("async function loadProjects()")
        ]
        self.assertNotIn("Needs Attention", projects_body)
        self.assertNotIn("projectIssues", projects_body)
        self.assertIn("typed <code>member_of</code>", html)
        self.assertIn("first creation only", html)

    def test_needs_attention_is_inbox_only_durable_and_recoverable(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertNotIn('id="issue-notice"', html)
        self.assertIn('id="warning-dismiss-dialog"', html)
        self.assertIn('id="warning-dismiss-confirm"', html)
        self.assertIn("renderNeedsAttention", javascript)
        self.assertIn("task_visible", javascript)
        self.assertIn("repair_active_membership", javascript)
        self.assertIn("/relationships/active-membership", javascript)
        self.assertIn('view === "inbox" ? renderNeedsAttention() : null', javascript)
        self.assertIn('fetch("/api/warnings/dismiss"', javascript)
        self.assertIn('fetch("/api/warnings/restore"', javascript)
        self.assertIn("Show dismissed warnings", javascript)
        self.assertIn("Restore warning", javascript)
        self.assertIn("never hides or changes the task or project", javascript)
        self.assertIn("Needs Attention", javascript)


if __name__ == "__main__":
    unittest.main()
