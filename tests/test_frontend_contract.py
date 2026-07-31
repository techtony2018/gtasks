import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FrontendContractTests(unittest.TestCase):

    def test_narrow_desktop_calendar_keeps_detail_panel_in_layout_flow(self) -> None:
        stylesheet = (PROJECT_ROOT / "static" / "styles.css").read_text(
            encoding="utf-8"
        )

        breakpoint = stylesheet[stylesheet.index("@media (max-width: 1080px)") : stylesheet.index("@media (max-width: 760px)")]
        self.assertIn(
            "grid-template-columns: 210px minmax(0, 1fr) minmax(300px, 360px)",
            breakpoint,
        )
        self.assertIn(".detail-panel", breakpoint)
        self.assertIn("position: sticky", breakpoint)
        self.assertNotIn("position: fixed", breakpoint)
        self.assertIn("overscroll-behavior-inline: contain", stylesheet)
    def test_board_and_status_editor_are_first_class_navigation(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('data-view="board"', html)
        self.assertIn('data-count="board"', html)
        self.assertIn('id="task-edit-button"', html)
        self.assertIn('id="task-editor-status"', html)
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
        self.assertIn('`/api/tasks/${encodeURIComponent(state.taskEditorSourceSlug)}`', javascript)
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
        self.assertNotIn('task.status === "waiting" ? "blocked"', javascript)
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
        self.assertIn('id="task-editor-goal"', html)
        self.assertIn('id="task-edit-button"', html)
        self.assertIn("legacy_one_way_tasks", javascript)
        self.assertIn("choose Edit, and save its current goal", javascript)
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
        self.assertIn('"/api/goals"', javascript)
        self.assertIn("function openEditGoal()", javascript)
        self.assertIn("goal-edit-button", html)
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
        self.assertIn("Mission Control remains available", javascript)
        self.assertIn(
            'event.key === "Escape" && elements.logsDialog.open',
            javascript,
        )
        self.assertNotIn('fetch("/api/logs", {\n      method:', javascript)

    def test_task_detail_is_read_only_until_the_full_editor_opens(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="task-next-action-value"', html)
        self.assertIn('id="task-edit-button"', html)
        self.assertIn('id="task-duplicate-button"', html)
        self.assertIn('id="task-editor-next-action"', html)
        self.assertNotIn('id="task-next-action-save"', html)
        self.assertIn("function openEditTask", javascript)
        self.assertIn("elements.taskDuplicateButton.addEventListener", javascript)
        self.assertIn('method: state.taskEditorMode === "edit" ? "PATCH" : "POST"', javascript)

    def test_full_create_and_duplicate_offer_optional_progress_metrics(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        for element_id in (
            "create-task-button",
            "task-editor-dialog",
            "task-editor-form",
            "task-editor-title",
            "task-track-metric",
            "task-metric-fields",
            "task-metric-label",
            "task-metric-target",
            "task-metric-current",
            "task-metric-preview",
            "task-metric-event-binding",
            "task-editor-status",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("Track a metric", html)
        self.assertIn("Count is the initial supported metric type", html)
        self.assertIn("Current progress starts at 0", html)
        self.assertIn("function openCreateTask", javascript)
        self.assertIn("function openDuplicateTask", javascript)
        self.assertIn('task-duplicate-button', html)
        self.assertIn("todayActions: true", javascript)
        self.assertIn("row-action-button", javascript)
        self.assertIn("openDuplicateTask();", javascript)
        self.assertIn("function updateTaskMetricPreview", javascript)
        self.assertIn('"/api/tasks"', javascript)
        self.assertIn("/duplicate`", javascript)
        self.assertIn('elements.taskMetricCurrent.value = "0"', javascript)
        self.assertIn("updateTaskMetricPreview();", javascript)
        self.assertIn('populateTaskEditorAssignees(task.owner_agent || "tony")', javascript)
        self.assertIn("event_binding", javascript)
        self.assertIn("Job applications", javascript)

    def test_metric_progress_is_rendered_on_cards_rows_and_task_detail(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="task-progress-detail"', html)
        self.assertIn('id="task-progress-label"', html)
        self.assertIn('id="task-progress-value"', html)
        self.assertIn("function taskProgressLabel", javascript)
        self.assertIn("metric-progress", javascript)
        self.assertIn("taskProgressLabel(task)", javascript)

    def test_agent_profiles_and_board_filter_are_scoped_and_default_off(
        self,
    ) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('data-view="agent-work"', html)
        self.assertIn('id="show-agent-tasks"', html)
        self.assertIn("Show agent tasks", html)
        self.assertIn("showAgentTasks: false", javascript)
        self.assertIn('fetch("/api/agents"', javascript)
        self.assertIn('fetch("/api/agent-work"', javascript)
        self.assertIn("function agentBoardCard", javascript)
        self.assertIn("function ownerBadge", javascript)
        self.assertIn("ownerBadge(state.snapshot?.owner", javascript)
        self.assertIn("agent-owner-badge", javascript)
        self.assertIn("owner.name", javascript)
        self.assertIn("owner.avatar", javascript)
        self.assertNotIn("showAgentTasks", javascript[javascript.index("body: JSON.stringify") : javascript.index("document.querySelectorAll")])

    def test_agent_surfaces_use_the_shared_owner_badge_renderer(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        agent_work = javascript[
            javascript.index("function renderAgentWorkView")
            : javascript.index("function goalCard")
        ]
        proposal_card = javascript[
            javascript.index("function proposalCard")
            : javascript.index("function renderProposedWork")
        ]

        self.assertIn("ownerBadge({", agent_work)
        self.assertNotIn("ownerBadge({", proposal_card)
        self.assertIn('node("h4", "", proposal.title)', proposal_card)
        self.assertIn('node("button", "secondary-button", "Edit")', proposal_card)
        self.assertIn('card.addEventListener("click", open)', proposal_card)
        self.assertIn("event.stopPropagation(); selectTask(proposal.slug); openEditTask()", proposal_card)
        self.assertNotIn("agentOwnerBadge", javascript)

    def test_proposed_task_cards_open_details_and_use_two_columns_on_desktop(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        finder = javascript[javascript.index("function findTaskBySlug") : javascript.index("function reconcileVerifiedTask")]

        self.assertIn("state.proposals.find", finder)
        self.assertIn("detail: proposal.rationale", finder)
        self.assertIn("next_action: proposal.proposed_next_step", finder)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr))", css)
        self.assertIn(".proposal-agent-group { grid-template-columns: 1fr; }", css)

    def test_overdue_tasks_use_canonical_day_and_red_treatment_in_today_and_calendar(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("function isOverdueExecutable", javascript)
        self.assertIn('!["completed", "cancelled"].includes(task.status)', javascript)
        self.assertIn('row.classList.toggle("is-overdue-task", isOverdueExecutable(task))', javascript)
        self.assertIn('taskButton.classList.toggle("is-overdue-task", isOverdueExecutable(task))', javascript)
        self.assertIn(".task-row.is-overdue-task", css)
        self.assertIn(".month-task.is-overdue-task", css)

    def test_calendar_has_default_on_ical_events_filter(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("showIcalEvents: true", javascript)
        self.assertIn("function calendarEventsFilter", javascript)
        self.assertIn('"Show iCal Events"', javascript)
        self.assertIn("input.checked = state.showIcalEvents", javascript)
        self.assertIn("Connect Calendar", javascript)
        self.assertIn("Calendar permission was not granted", javascript)
        self.assertIn("Local Calendar is unavailable", javascript)
        self.assertIn("icalEventsForDay", javascript)

    def test_proposed_tasks_are_inbox_only_grouped_by_agent_and_confirmation_bound(
        self,
    ) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("Proposed Tasks", javascript)
        self.assertIn("All Agents", javascript)
        self.assertIn('node("button", "submit-button", "Approve")', javascript)
        self.assertIn('node("button", "danger-button", "Reject")', javascript)
        self.assertIn('fetch("/api/proposals"', javascript)
        self.assertIn('id="proposal-review-dialog"', html)
        self.assertIn('id="proposal-decision-dialog"', html)
        self.assertIn("same proposed task", javascript)
        self.assertIn("Rejection records a durable decision", javascript)
        self.assertIn(
            'view === "inbox" ? renderProposedWork() : null',
            javascript,
        )

    def test_calendar_and_project_edit_layout_contracts(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('id="new-project-heading"', html)
        self.assertIn('elements.newProjectHeading.textContent = "Edit"', javascript)
        self.assertIn('"Create a Project"', javascript)
        self.assertIn("grid-template-columns: repeat(7, minmax(0, 1fr))", css)
        self.assertIn("overflow-wrap: anywhere", css)
        self.assertIn(".proposal-decision-field", css)
        self.assertIn("container-type: inline-size", css)
        self.assertIn("@container (max-width: 105px)", css)
        self.assertIn(".week-task-list .task-title {", css)
        self.assertIn(".week-task-list .task-project {", css)
        self.assertIn(".week-task-list .task-goal {", css)
        self.assertIn("repeat(7, minmax(135px, 1fr))", css)
        self.assertIn("grid-auto-columns: minmax(230px, 78vw)", css)
        self.assertIn("calendarWeek: true", javascript)
        self.assertIn("Goal: ${goal?.title || task.goal}", javascript)
        self.assertIn("font-size: 10px", css)

    def test_agent_cards_have_one_direct_profile_control_and_structured_current_work(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        agent_work = javascript[
            javascript.index("function renderAgentWorkView") : javascript.index("function renderCoordinatorSummary")
        ]

        self.assertIn('node("button", "agent-card-profile-button", "⋯")', agent_work)
        self.assertIn("openAgentProfile(agent)", agent_work)
        self.assertNotIn("node(\"details\"", agent_work)
        self.assertNotIn("Open Agent Profile", agent_work)
        self.assertIn('node("h3", "", "Current work")', agent_work)
        self.assertIn("No authorized work yet", agent_work)
        self.assertIn("No current task or next step recorded.", agent_work)

    def test_task_write_paths_use_fail_closed_type_preservation(self) -> None:
        adapter = (PROJECT_ROOT / "gtasks" / "gbrain.py").read_text(encoding="utf-8")

        self.assertIn("def _render_preserved_task_page", adapter)
        self.assertIn("refusing to change canonical page type", adapter)
        self.assertIn("_render_preserved_task_page(raw_page, changed)", adapter)
        self.assertGreaterEqual(adapter.count("_render_preserved_task_page(raw_page"), 8)

    def test_goal_detail_has_default_agent_profile_link(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="goal-default-agent"', html)
        self.assertIn('id="goal-default-agent-name"', html)
        self.assertIn('id="goal-default-agent-link"', html)
        self.assertIn("default_goal_slugs", javascript)
        self.assertIn("openAgentProfile(agent)", javascript)

    def test_sidebar_is_text_first_and_does_not_offer_upcoming(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertNotIn('data-view="upcoming"', html)
        self.assertNotIn("Upcoming", html)
        self.assertNotIn("upcoming:", javascript)
        self.assertNotIn("nav-glyph", html)
        self.assertNotIn("brand-mark", html)

    def test_week_view_groups_canonical_due_dates_without_a_write_path(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('data-view="week"', html)
        self.assertIn('data-count="week"', html)
        self.assertIn("function renderWeekView()", javascript)
        self.assertIn("function weekStartFor(value)", javascript)
        self.assertIn("function currentWeekTasks()", javascript)
        self.assertIn("task.due_day >= start", javascript)
        self.assertIn("task.due_day < end", javascript)
        self.assertIn('!["completed", "cancelled"].includes(task.status)', javascript)
        self.assertIn("state.weekStart = shiftWeek(start, -1)", javascript)
        self.assertIn("state.weekStart = shiftWeek(start, 1)", javascript)

    def test_agent_avatar_profile_uses_only_the_local_stargraph_boundary(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="agent-profile-dialog"', html)
        self.assertIn('id="agent-avatar-section"', html)
        self.assertIn('id="agent-current-avatar"', html)
        self.assertIn('id="agent-current-avatar-image"', html)
        self.assertIn('id="agent-current-avatar-filename"', html)
        self.assertIn('id="agent-avatar-file"', html)
        self.assertIn('id="agent-avatar-filename"', html)
        self.assertIn("PNG, JPEG, GIF, or WebP", html)
        self.assertIn("/api/agents/${encodeURIComponent(agent.slug)}/avatar", javascript)
        self.assertIn("Memory Stargraph", javascript)
        self.assertIn("URL.createObjectURL", javascript)
        self.assertIn("clearAgentAvatarPreview", javascript)
        self.assertIn("renderCurrentAgentAvatar(agent)", javascript)
        self.assertIn('id="agent-profile-goals"', html)
        self.assertIn('id="agent-goal-select"', html)
        self.assertIn('id="agent-goal-add"', html)
        self.assertIn("/default-goals", javascript)
        self.assertIn("saveAgentGoalAssignment", javascript)

        avatar_section = html[
            html.index('<section class="agent-avatar-editor"')
            : html.index("</section>", html.index('<section class="agent-avatar-editor"'))
        ]
        self.assertIn('id="agent-avatar-submit"', avatar_section)
        self.assertNotIn(
            '<div class="dialog-footer"><p id="agent-avatar-state">',
            html,
        )

    def test_agent_profile_renders_markdown_as_safe_profile_content(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('class="agent-profile-summary"', html)
        self.assertIn("function renderAgentProfileSummary", javascript)
        self.assertIn("renderAgentProfileSummary(agent)", javascript)
        self.assertIn("document.createTextNode", javascript)
        self.assertIn(r"line.match(/^(#{1,3})\s+(.+)$/)", javascript)
        self.assertNotIn(
            "elements.agentProfileSummary.textContent = agent.summary",
            javascript,
        )

    def test_agent_work_navigation_is_named_agents(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        agent_nav = html[
            html.index('<button class="nav-item" data-view="agent-work"')
            : html.index("</button>", html.index('<button class="nav-item" data-view="agent-work"'))
        ]
        self.assertIn("<span>Agents</span>", agent_nav)
        self.assertNotIn("<span>Agent Work</span>", agent_nav)
        self.assertIn('"agent-work": {\n    title: "Agents"', javascript)

    def test_full_task_creation_is_the_only_visible_creation_flow(
        self,
    ) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="task-editor-assignee"', html)
        self.assertIn('value="tony">Tony — personal task', html)
        self.assertIn("function populateTaskEditorAssignees", javascript)
        self.assertIn("state.agents.forEach", javascript)
        self.assertIn("creates one authorized, queued agent work item", html)
        self.assertIn("assignee_slug", javascript)
        self.assertIn("savedTask.owner_agent", javascript)
        self.assertNotIn('id="quick-add-button"', html)
        self.assertNotIn('id="quick-add-dialog"', html)
        self.assertNotIn("openQuickAdd", javascript)
        self.assertNotIn("submitQuickAdd", javascript)
        self.assertIn('id="create-task-button"', html)
        self.assertIn("function creationEntry", javascript)
        self.assertIn('creationEntry("today")', javascript)
        self.assertIn('creationEntry("inbox")', javascript)
        self.assertIn('openCreateTask();', javascript)

    def test_agent_board_cards_have_owner_badges_and_safe_status_controls(
        self,
    ) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        body = javascript[
            javascript.index("function agentBoardCard")
            : javascript.index("function updateBoardStatus")
        ]

        self.assertIn("ownerBadge(task.owner)", body)
        self.assertIn("Move to", body)
        self.assertIn("moveBoardTask(task.slug", body)
        self.assertIn("card.draggable = true", body)
        self.assertNotIn("Read-only in GTasks", body)

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
        self.assertIn('id="task-editor-project"', html)
        self.assertIn('id="task-edit-button"', html)
        self.assertNotIn('id="task-project-save"', html)
        self.assertIn('fetch("/api/projects"', javascript)
        self.assertIn("submitNewProject", javascript)
        self.assertIn("project_slug", javascript)
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
