import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FrontendContractTests(unittest.TestCase):

    def test_agent_artifacts_have_safe_browsing_and_task_discovery(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        stylesheet = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('data-view="artifacts"', html)
        self.assertIn('<span class="nav-label">Artifacts</span>', html)
        self.assertIn('id="artifact-agent-filter"', html)
        self.assertIn('id="artifact-detail-content"', html)
        self.assertIn('id="task-artifacts"', html)
        self.assertIn("function renderArtifactsView()", javascript)
        self.assertIn(
            "function selectArtifact(artifactSlug, originControl = null)",
            javascript,
        )
        self.assertIn("function loadArtifacts(", javascript)
        self.assertIn("No artifacts yet", javascript)
        self.assertIn("renderSafeMarkdown(elements.artifactDetailMarkdown", javascript)
        artifact_selector = javascript[
            javascript.index("function selectArtifact(artifactSlug, originControl = null)") :
            javascript.index("function selectTask(")
        ]
        self.assertNotIn("innerHTML", artifact_selector)
        self.assertIn(".artifact-detail-content", stylesheet)
        mobile = stylesheet[stylesheet.index("@media (max-width: 760px)") :]
        self.assertIn(".detail-panel", mobile)
        self.assertIn("position: fixed", mobile)
        self.assertIn("inset: 0", mobile)
        self.assertIn("overflow-x: hidden", mobile)

    def test_artifact_git_links_use_the_same_explicit_commit_allowlist(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        renderer = javascript[
            javascript.index("function safeGitCommitUrl") :
            javascript.index("function selectArtifact")
        ]

        self.assertIn('host === "github.com"', renderer)
        self.assertIn('host === "gitlab.com"', renderer)
        self.assertIn('host === "bitbucket.org"', renderer)
        self.assertIn("safeGitCommitUrl(artifact.git_url)", renderer)
        self.assertIn('node("p", "artifact-unsupported-reference", artifact.git_url)', renderer)
        self.assertNotIn("artifact.git_url && /^https", renderer)

    def test_artifact_media_uses_stargraph_origin_and_only_safe_preview_types(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        renderer = javascript[
            javascript.index("function renderArtifactAttachments") :
            javascript.index("function selectArtifact")
        ]

        self.assertIn(
            'const MEMORY_STARGRAPH_ORIGIN = "http://127.0.0.1:8788"',
            javascript,
        )
        self.assertIn("safeStargraphMediaUrl(reference)", renderer)
        self.assertIn("image.src = mediaUrl.href", renderer)
        self.assertIn("link.href = mediaUrl.href", renderer)
        self.assertIn('node("p", "artifact-unsupported-reference", reference)', renderer)
        self.assertNotIn('"Open attachment"', renderer)

        validator = javascript[
            javascript.index("function safeStargraphMediaUrl") :
            javascript.index("function renderArtifactAttachments")
        ]
        self.assertIn("new URL(reference, MEMORY_STARGRAPH_ORIGIN)", validator)
        self.assertIn("resolved.origin !== MEMORY_STARGRAPH_ORIGIN", validator)
        self.assertIn('!resolved.pathname.startsWith("/media/")', validator)
        self.assertIn("/%2f|%5c/i.test(reference)", validator)
        self.assertIn("decodeURIComponent(resolved.pathname)", validator)
        self.assertIn("resolved.search || resolved.hash", validator)

    def test_artifact_close_restores_task_detail_and_focus_context(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        selector = javascript[
            javascript.index("function selectArtifact") :
            javascript.index("function renderTaskArtifacts")
        ]
        close_details = javascript[
            javascript.index("function closeDetails") :
            javascript.index("async function saveTaskGoal")
        ]

        self.assertIn("artifactTaskReturn", javascript)
        self.assertIn('state.selectedKind === "task"', selector)
        self.assertIn("taskSlug: state.selectedSlug", selector)
        self.assertIn("state.detailReturnFocus", selector)
        self.assertIn('state.selectedKind === "artifact"', close_details)
        self.assertIn("selectTask(artifactReturn.taskSlug)", close_details)
        self.assertIn("state.detailReturnFocus = artifactReturn.detailReturnFocus", close_details)
        self.assertIn("artifactReturn.element", close_details)
        self.assertIn("button.dataset.slug = artifact.slug", javascript)

    def test_artifact_list_close_restores_the_rerendered_origin_card(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        card = javascript[
            javascript.index("function artifactCard") :
            javascript.index("function renderArtifactsView")
        ]
        close_details = javascript[
            javascript.index("function closeDetails") :
            javascript.index("async function saveTaskGoal")
        ]

        self.assertIn("selectArtifact(artifact.slug, button)", card)
        self.assertIn('document.querySelectorAll(".artifact-card")', close_details)
        self.assertIn("candidate.dataset.slug === returnFocus.slug", close_details)

    def test_safe_markdown_formats_fences_tables_and_contains_long_tokens(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        stylesheet = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        renderer = javascript[
            javascript.index("function renderSafeMarkdown") :
            javascript.index("const AGENT_TASKS_PREFERENCE_KEY")
        ]

        self.assertIn('document.createElement("pre")', renderer)
        self.assertIn('document.createElement("table")', renderer)
        self.assertIn('node("div", "markdown-table-wrap")', renderer)
        self.assertIn("code.textContent = fencedLines.join", renderer)
        self.assertIn("appendInline(cell, value)", renderer)
        self.assertNotIn("innerHTML", renderer)
        self.assertIn(".detail-copy code", stylesheet)
        self.assertIn("overflow-wrap: anywhere", stylesheet)
        self.assertIn(".markdown-table-wrap", stylesheet)

    def test_artifact_unavailable_state_is_stacked_and_readable(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        stylesheet = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        artifacts_view = javascript[
            javascript.index("function renderArtifactsView") :
            javascript.index("async function loadArtifacts")
        ]

        self.assertIn('node("div", "section-empty artifact-error-state")', artifacts_view)
        self.assertIn(".artifact-error-state", stylesheet)
        self.assertIn("display: grid", stylesheet[stylesheet.index(".artifact-error-state") :])
        self.assertIn("grid-template-columns: minmax(0, 1fr)", stylesheet)

    def test_latest_artifact_filter_request_wins_over_stale_response(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        loader = javascript[
            javascript.index("async function loadArtifacts") :
            javascript.index("function renderArtifactAttachments")
        ]

        self.assertIn("artifactRequestToken", javascript)
        self.assertIn("const requestToken = ++state.artifactRequestToken", loader)
        self.assertIn("if (requestToken !== state.artifactRequestToken) return", loader)
        self.assertLess(
            loader.index("if (requestToken !== state.artifactRequestToken) return"),
            loader.index("state.artifacts ="),
        )
        self.assertNotIn("if (state.artifactsLoading) return", loader)

    def test_artifact_does_not_render_publisher_asserted_readback_metadata(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertNotIn('id="artifact-detail-sha-row"', html)
        self.assertNotIn('id="artifact-detail-hash-row"', html)
        self.assertNotIn('id="artifact-detail-verified-row"', html)
        self.assertNotIn("function renderArtifactReadbackMetadata", javascript)
        self.assertNotIn("renderArtifactReadbackMetadata(artifact)", javascript)

    def test_task_detail_markdown_keeps_blocks_and_inline_formatting(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        renderer = javascript[
            javascript.index("function renderSafeMarkdown")
            : javascript.index("const AGENT_TASKS_PREFERENCE_KEY")
        ]

        self.assertIn('<div class="detail-copy" id="detail-copy"></div>', html)
        self.assertIn('document.createElement(`h${heading[1].length}`)', renderer)
        self.assertIn('document.createElement(listType)', renderer)
        self.assertIn('document.createElement("strong")', renderer)
        self.assertIn('document.createElement("code")', renderer)
        self.assertIn('appendInline(item, listItem[3])', renderer)
        self.assertNotIn('document.createElement("strong"); appendInline(h', renderer)

    def test_agent_blocked_tasks_are_visible_in_today_and_blocked_views(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function visibleBlockedTasks()", javascript)
        visible_blocked = javascript[
            javascript.index("function visibleBlockedTasks()")
            : javascript.index("function rebuildDerivedTaskViews()")
        ]
        render_today = javascript[
            javascript.index("function renderToday()")
            : javascript.index("function simpleEmpty")
        ]
        render_list = javascript[
            javascript.index("function renderListView")
            : javascript.index("function renderWeekView")
        ]
        set_view = javascript[
            javascript.index("function setView(view)")
            : javascript.index("function setConnection")
        ]

        self.assertIn('task.status === "blocked"', visible_blocked)
        self.assertIn("state.snapshot.views.blocked", visible_blocked)
        self.assertIn("state.agentTasks", visible_blocked)
        self.assertIn("visibleBlockedTasks()", render_today)
        self.assertIn('view === "blocked" ? visibleBlockedTasks()', render_list)
        self.assertIn('view === "today" || view === "blocked"', set_view)
        self.assertIn("loadAgentWork();\nloadTasks", javascript)
        self.assertIn("blocked: visibleBlockedTasks().length", javascript)

    def test_slow_reads_keep_surfaces_independent_and_last_valid_content_visible(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        render_body = javascript[
            javascript.index("function render()") : javascript.index("function renderSystemTicketsView")
        ]
        proposal_renderer = javascript[
            javascript.index("function renderProposedWork") : javascript.index("async function submitProposalReview")
        ]

        self.assertNotIn("if (state.loading) return;", render_body)
        self.assertIn("renderTaskSurfaceLoading", render_body)
        self.assertIn("payload.read_state", javascript)
        self.assertIn("scheduleSurfacePoll", javascript)
        self.assertIn("state.proposals.length", proposal_renderer)
        self.assertIn("Last verified proposals", proposal_renderer)

    def test_narrow_desktop_calendar_keeps_detail_panel_in_layout_flow(self) -> None:
        stylesheet = (PROJECT_ROOT / "static" / "styles.css").read_text(
            encoding="utf-8"
        )

        breakpoint = stylesheet[stylesheet.index("@media (max-width: 1080px)") : stylesheet.index("@media (max-width: 760px)")]
        self.assertIn(
            "grid-template-columns: 92px minmax(0, 1fr) minmax(300px, 360px)",
            breakpoint,
        )
        self.assertIn(".detail-panel", breakpoint)
        self.assertIn("position: sticky", breakpoint)
        self.assertNotIn("position: fixed", breakpoint)
        self.assertIn("overscroll-behavior-inline: contain", stylesheet)
        self.assertIn('.detail-panel[aria-hidden="true"]', stylesheet)
        self.assertIn("contain: paint", stylesheet)
        mobile = stylesheet[stylesheet.index("@media (max-width: 760px)") :]
        self.assertIn(".week-view { min-width: 0; max-width: 100%; overflow-x: hidden; }", mobile)
        self.assertIn(".week-grid", mobile)
        self.assertIn("overflow-x: auto", mobile)
    def test_board_and_status_editor_are_first_class_navigation(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('data-view="board"', html)
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

    def test_automatic_job_metric_accepts_custom_target_and_seeded_current(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        metric_payload = javascript[
            javascript.index("function taskEditorMetricPayload()")
            : javascript.index("async function submitTaskEditor")
        ]
        binding_listener = javascript[
            javascript.index('elements.taskMetricEventBinding.addEventListener("change"')
            : javascript.index("elements.refreshButton.addEventListener")
        ]

        self.assertNotIn("target !== 5", metric_payload)
        self.assertNotIn("current !== 0", metric_payload)
        self.assertNotIn('elements.taskMetricTarget.value = "5"', binding_listener)
        self.assertNotIn('elements.taskMetricCurrent.value = "0"', binding_listener)
        self.assertNotIn("requires target 5 and current 0", javascript)
        self.assertIn("increments progress by 1", javascript)

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
            : javascript.index("function setView")
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

        store_end = html.index("</div>", html.index('class="store-card'))
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

    def test_sidebar_icons_match_primary_control_size_and_version_moves_to_artwork(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        sidebar = html[html.index('<aside class="sidebar"') : html.index("</aside>")]
        art_footer = html[html.index('<div class="mission-art-footer"') : html.index("</main>")]
        self.assertNotIn("Read-only", sidebar)
        self.assertNotIn('id="sidebar-version"', sidebar)
        self.assertIn('id="sidebar-version"', art_footer)
        self.assertIn('href="https://github.com/techtony2018/gtasks"', art_footer)
        self.assertLess(art_footer.index("mission-word-art"), art_footer.index("sidebar-version"))
        self.assertIn(".nav-icon,\n.rail-icon {\n  width: 44px;", css)
        self.assertIn("font-size: 28px", css)

    def test_inbox_reject_action_is_a_red_accessible_icon(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        proposal_card = javascript[
            javascript.index("function proposalCard") : javascript.index("function renderProposedWork")
        ]

        self.assertIn('actionIcon("❌", `Reject ${proposal.title}`', proposal_card)
        self.assertIn('className: "proposal-reject-button"', proposal_card)
        self.assertNotIn('node("button", "danger-button", "Reject")', proposal_card)
        self.assertIn(".proposal-reject-button", css)

    def test_logs_are_adjacent_read_only_filtered_and_accessible(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        store_end = html.index("</div>", html.index('class="store-card'))
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

    def test_task_detail_exposes_per_item_todos_without_legacy_next_action_editor(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="task-todo-list"', html)
        self.assertIn('id="task-todo-show-completed"', html)
        self.assertNotIn('id="task-todo-filter"', html)
        self.assertIn('id="task-todo-add-form"', html)
        self.assertIn('id="task-todo-text"', html)
        self.assertIn('id="task-todo-detail"', html)
        self.assertIn('id="task-todo-error"', html)
        self.assertIn('id="task-edit-button"', html)
        self.assertIn('id="task-duplicate-button"', html)
        self.assertIn('id="task-editor-initial-todo"', html)
        self.assertNotIn('id="task-editor-next-action"', html)
        self.assertNotIn('id="task-next-action-value"', html)
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
        self.assertIn("showAgentTasks: readAgentTasksPreference()", javascript)
        self.assertIn('AGENT_TASKS_PREFERENCE_KEY = "mission-control.show-agent-tasks"', javascript)
        self.assertIn('AGENT_TASKS_PREFERENCE_COOKIE = "mission-control-show-agent-tasks"', javascript)
        self.assertLess(
            javascript.index("const AGENT_TASKS_PREFERENCE_COOKIE"),
            javascript.index("const state ="),
        )
        self.assertIn("function agentTasksPreferenceCookie()", javascript)
        self.assertIn("function setAgentTasksVisible(visible)", javascript)
        self.assertIn("window.localStorage.setItem", javascript)
        self.assertIn('fetch("/api/agents"', javascript)
        self.assertIn('fetch("/api/agent-work"', javascript)
        self.assertIn("function agentBoardCard", javascript)
        self.assertIn("function ownerBadge", javascript)
        self.assertIn("ownerBadge(state.snapshot?.owner", javascript)
        self.assertIn("agent-owner-badge", javascript)
        self.assertIn("owner.name", javascript)
        self.assertIn("owner.avatar", javascript)
        self.assertNotIn("showAgentTasks", javascript[javascript.index("body: JSON.stringify") : javascript.index("document.querySelectorAll")])

    def test_board_agent_filter_restores_and_loads_on_board_navigation(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        render = javascript[javascript.index("function render()") : javascript.index("function setConnection")]
        set_view = javascript[javascript.index("function setView(view)") : javascript.index("function setConnection")]
        listener = javascript[
            javascript.index("elements.showAgentTasks.addEventListener")
            : javascript.index("elements.detailClose.addEventListener")
        ]

        self.assertIn("elements.showAgentTasks.checked = state.showAgentTasks", render)
        self.assertIn('view === "board" && state.showAgentTasks', set_view)
        self.assertIn("void loadAgentWork();", set_view)
        self.assertIn("setAgentTasksVisible(elements.showAgentTasks.checked)", listener)
        self.assertIn("document.cookie = `${AGENT_TASKS_PREFERENCE_COOKIE}", javascript)
        self.assertIn('task.status !== "proposed"', javascript)

    def test_board_keeps_tony_work_visible_when_agent_profiles_are_unavailable(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function agentWorkUnavailableMessage()", javascript)
        self.assertIn('issue.slug.startsWith("agents/")', javascript)
        self.assertIn("Agent work could not be read from GBrain. Tony’s Board is unchanged", javascript)
        render_board = javascript[
            javascript.index("function renderBoard()") : javascript.index("function openAgentProfile")
        ]
        self.assertLess(render_board.index(": unavailable"), render_board.index(": state.agentTasks.length"))

    def test_agent_board_card_keeps_its_detail_payload_for_mobile_selection(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        card = javascript[
            javascript.index("function agentBoardCard") : javascript.index("function updateBoardStatus")
        ]
        self.assertIn("selectTask(task.slug, task)", card)
        self.assertIn("function selectTask(slug, taskFallback = null, returnFocus = null)", javascript)
        self.assertIn("findTaskBySlug(slug) || taskFallback", javascript)

    def test_mobile_task_detail_is_a_visible_focused_sheet(self) -> None:
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('.detail-panel[aria-hidden="false"]', css)
        detail_sheet = css[css.index('.detail-panel[aria-hidden="false"]', css.index("On a phone")) :]
        self.assertIn("position: fixed", detail_sheet[:300])
        self.assertIn("inset: 0", detail_sheet[:300])
        self.assertIn("height: 100dvh", detail_sheet[:300])

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
        self.assertIn('actionIcon("✎", `Edit ${proposal.title}`)', proposal_card)
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

    def test_proposal_decisions_remain_visible_with_status_and_timeline(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="proposal-decision-timeline"', html)
        self.assertIn("function proposalStateLabel(proposal)", javascript)
        self.assertIn('"Approved · Planned"', javascript)
        self.assertIn('"Rejected · Cancelled"', javascript)
        self.assertIn('"Pending review"', javascript)
        self.assertIn('"Recent decisions"', javascript)
        self.assertIn("task.decision_events", javascript)
        self.assertIn("task.proposal_decision", javascript)
        self.assertIn("task.proposal_decided_at", javascript)
        self.assertIn("renderProposalDecisionTimeline(task)", javascript)

    def test_mobile_task_selection_reveals_and_focuses_detail_panel(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        body = javascript[javascript.index("function selectTask") : javascript.index("function goalTaskLinks")]

        self.assertIn('id="detail-title" tabindex="-1"', html)
        self.assertIn('window.matchMedia("(max-width: 760px)").matches', body)
        self.assertIn('elements.detailPanel.scrollIntoView', body)
        self.assertIn('elements.detailTitle.focus({ preventScroll: true })', body)

    def test_proposal_keyboard_detail_focuses_heading_and_returns_to_origin(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        proposal_card = javascript[
            javascript.index("function proposalCard")
            : javascript.index("function renderProposedWork")
        ]
        select_task = javascript[
            javascript.index("function selectTask")
            : javascript.index("function goalTaskLinks")
        ]
        close_details = javascript[
            javascript.index("function closeDetails")
            : javascript.index("async function saveTaskGoal")
        ]

        self.assertIn("card.dataset.slug = proposal.slug", proposal_card)
        self.assertIn("selectTask(proposal.slug, null, card)", proposal_card)
        self.assertIn("state.detailReturnFocus", select_task)
        self.assertIn("elements.detailTitle.focus({ preventScroll: true })", select_task)
        self.assertIn('document.querySelectorAll(".proposal-card")', close_details)
        self.assertIn("candidate.dataset.slug === returnFocus.slug", close_details)
        self.assertIn("target?.focus({ preventScroll: true })", close_details)

    def test_task_keyboard_detail_returns_to_origin_after_list_rerender(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        task_row = javascript[
            javascript.index("function taskRow")
            : javascript.index("function section")
        ]
        close_details = javascript[
            javascript.index("function closeDetails")
            : javascript.index("async function saveTaskGoal")
        ]

        self.assertIn(
            'button.addEventListener("click", () => selectTask(task.slug, null, button))',
            task_row,
        )
        self.assertIn('document.querySelectorAll(".task-row-open")', close_details)
        self.assertIn("candidate.dataset.slug === returnFocus.slug", close_details)
        self.assertIn("target?.focus({ preventScroll: true })", close_details)

    def test_proposal_decision_notes_are_not_styled_as_completed_actions(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        timeline = javascript[
            javascript.index("function renderProposalDecisionTimeline")
            : javascript.index("function keepSelectedCalendarTaskVisible")
        ]

        self.assertIn('const item = node("li", "is-decision")', timeline)
        self.assertNotIn('node("li", "is-completed")', timeline)
        self.assertIn(".next-action-timeline li.is-decision", css)
        decision_style = css[css.index(".next-action-timeline li.is-decision") :]
        self.assertIn("text-decoration: none", decision_style[:300])

    def test_static_asset_cache_keys_match_current_release(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")

        self.assertIn('href="/styles.css?v=0.0.70"', html)
        self.assertIn('src="/app.js?v=0.0.70"', html)

    def test_overdue_tasks_use_canonical_day_and_red_treatment_in_today_and_calendar(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("function isOverdueExecutable", javascript)
        self.assertIn('!["completed", "cancelled"].includes(task.status)', javascript)
        self.assertIn('row.classList.toggle("is-overdue-task", isOverdueExecutable(task))', javascript)
        self.assertIn('taskButton.classList.toggle("is-overdue-task", isOverdueExecutable(task))', javascript)
        self.assertIn(".task-row.is-overdue-task", css)
        self.assertIn(".month-task.is-overdue-task", css)

    def test_calendar_selection_marks_the_same_task_in_week_and_month(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('row.classList.toggle("is-selected", state.selectedSlug === task.slug)', javascript)
        self.assertIn('button.setAttribute("aria-current", state.selectedSlug === task.slug ? "true" : "false")', javascript)
        self.assertIn('taskButton.classList.toggle("is-selected", state.selectedSlug === task.slug)', javascript)
        self.assertIn('taskButton.setAttribute("aria-current", state.selectedSlug === task.slug ? "true" : "false")', javascript)
        self.assertIn(".month-task.is-selected", css)
        self.assertIn(".week-task-list .task-row.is-selected", css)

    def test_week_selection_scrolls_into_view_after_detail_panel_opens(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function keepSelectedCalendarTaskVisible(taskSlug)", javascript)
        self.assertIn('document.querySelectorAll(".week-grid .task-row-open")', javascript)
        self.assertIn('scrollIntoView({ block: "nearest", inline: "nearest" })', javascript)
        self.assertIn('window.matchMedia("(max-width: 760px)").matches', javascript)
        self.assertIn('selected.closest(".week-day")', javascript)
        self.assertIn("grid.scrollLeft = Math.min(day.offsetLeft, maxScroll)", javascript)
        self.assertIn("keepSelectedCalendarTaskVisible(task.slug)", javascript)

    def test_task_detail_renders_ordered_todos_comments_and_audit_history(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('aria-labelledby="task-todo-heading"', html)
        self.assertIn("function renderTaskTodos(task)", javascript)
        self.assertIn("function todoCard(todo)", javascript)
        self.assertIn("todo.comments", javascript)
        self.assertIn("todo.events", javascript)
        self.assertIn('"Not Done"', javascript)
        self.assertIn('"Done"', javascript)
        self.assertIn('id="task-todo-show-completed" type="checkbox"', html)
        self.assertIn("Show completed ones", html)
        self.assertNotIn('data-todo-filter="all"', html)
        self.assertNotIn('data-todo-filter="not_done"', html)
        self.assertNotIn('data-todo-filter="done"', html)
        self.assertIn("showCompletedTodos: false", javascript)
        self.assertIn('todo.status === "not_done"', javascript)
        self.assertIn("elements.taskTodoShowCompleted.checked", javascript)
        self.assertIn(".task-todo-list", css)
        self.assertIn(".task-todo-card", css)

    def test_todo_ui_calls_item_apis_and_restores_keyboard_focus(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('role="status" id="task-todo-loading"', html)
        self.assertIn('role="alert" id="task-todo-error"', html)
        self.assertIn("async function createTaskTodo", javascript)
        self.assertIn("async function editTaskTodo", javascript)
        self.assertIn("async function commentOnTodo", javascript)
        self.assertIn("async function changeTodoStatus", javascript)
        self.assertIn('`/api/tasks/${encodeURIComponent(taskSlug)}/todos`', javascript)
        self.assertIn('`/api/todos/${encodeURIComponent(todo.slug)}/comments`', javascript)
        self.assertIn('`/api/todos/${encodeURIComponent(todo.slug)}/status`', javascript)
        self.assertIn("crypto.randomUUID()", javascript)
        self.assertIn("state.todoReturnFocus", javascript)
        self.assertIn("candidate.dataset.todoSlug", javascript)
        self.assertIn("target?.focus({ preventScroll: true })", javascript)

    def test_agent_handoff_is_clear_atomic_and_keeps_question_history_read_only(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="task-handoff-panel"', html)
        self.assertIn('id="task-handoff-answer"', html)
        self.assertIn('id="task-handoff-submit"', html)
        self.assertIn(">Answer and Hand Back<", html)
        self.assertIn("Waiting for your answer", javascript)
        self.assertIn("Answer recorded — waiting for", javascript)
        self.assertIn("is working.", javascript)
        self.assertIn("function agentDisplayName(slug, task = null)", javascript)
        self.assertIn("taskOwner?.slug === slug && taskOwner?.name", javascript)
        self.assertIn("agentDisplayName(handoff.resume_owner, task)", javascript)
        self.assertIn("agentDisplayName(receipt.next_owner, receipt.task)", javascript)
        self.assertIn("async function answerAndHandBack", javascript)
        self.assertIn(
            "`/api/todos/${encodeURIComponent(todo.slug)}/answer`",
            javascript,
        )
        self.assertIn("function isActiveHandoffQuestion", javascript)
        self.assertIn("elements.taskHandoffPanel.focus", javascript)
        self.assertIn("Blocked by ${blocker}: ${task.next_action", javascript)
        self.assertIn("elements.taskHandoffError.textContent = message", javascript)
        self.assertIn(".task-handoff-panel", css)
        self.assertNotIn('status: "waiting"', javascript)
        self.assertNotIn("waiting for Tony", javascript)

    def test_task_todo_add_form_is_read_only_until_explicit_plus_action(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn(
            'id="task-todo-add-toggle" type="button" aria-label="Add a To Do"',
            html,
        )
        self.assertIn('data-tooltip="Add a To Do"', html)
        self.assertIn(
            'class="task-todo-add-form is-hidden" id="task-todo-add-form"',
            html,
        )
        self.assertIn('id="task-todo-add-cancel" type="button"', html)
        self.assertIn("todoAddOpen: false", javascript)
        self.assertIn("function setTodoAddOpen(open, { focus = true } = {})", javascript)
        self.assertIn("elements.taskTodoAddToggle.setAttribute", javascript)
        self.assertIn("elements.taskTodoText.focus", javascript)
        self.assertIn("elements.taskTodoAddToggle.focus", javascript)
        self.assertIn('todos.length ? "No open To Dos." : "No To Do yet"', javascript)

    def test_projects_open_edit_and_restore_focus_through_the_detail_sidebar(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        for identifier in (
            "project-detail-content",
            "project-detail-title",
            "project-detail-summary",
            "project-detail-goals",
            "project-detail-tasks",
            "project-detail-created",
            "project-detail-updated",
            "project-detail-slug",
            "project-edit-button",
            "project-detail-close",
            "new-project-summary",
            "new-project-status",
        ):
            self.assertIn(f'id="{identifier}"', html)
        self.assertIn('id="project-detail-title" tabindex="-1"', html)

        projects_view = javascript[
            javascript.index("function renderProjectsView()")
            : javascript.index("async function loadAgents()")
        ]
        select_project = javascript[
            javascript.index("function selectProject")
            : javascript.index("async function loadAgents()")
        ]
        close_details = javascript[
            javascript.index("function closeDetails")
            : javascript.index("async function saveTaskGoal")
        ]
        edit_project = javascript[
            javascript.index("function openEditProject")
            : javascript.index("async function submitNewProject")
        ]
        new_project = javascript[
            javascript.index("function openNewProject")
            : javascript.index("function populateProjectGoalChoices")
        ]
        submit_project = javascript[
            javascript.index("async function submitNewProject")
            : javascript.index("function openNewGoal")
        ]

        self.assertIn('const open = node("button", "project-card-open")', projects_view)
        self.assertIn("open.dataset.slug = project.slug", projects_view)
        self.assertIn("selectProject(project.slug, open)", projects_view)
        self.assertIn('state.selectedKind = "project"', select_project)
        self.assertIn('elements.detailPanel.setAttribute("aria-label", "Project details")', select_project)
        self.assertIn("renderSafeMarkdown(elements.projectDetailSummary, project.summary)", select_project)
        self.assertIn("elements.projectDetailTitle.focus({ preventScroll: true })", select_project)
        self.assertIn('document.querySelectorAll(".project-card-open")', close_details)
        self.assertIn("elements.newProjectSummary.value = project.summary", edit_project)
        self.assertIn("elements.newProjectStatus.value = project.status", edit_project)
        self.assertIn(
            'elements.newProjectClose.setAttribute("aria-label", "Close New Project")',
            new_project,
        )
        self.assertIn(
            'elements.newProjectClose.setAttribute("aria-label", "Close Project editor")',
            edit_project,
        )
        self.assertIn("summary: elements.newProjectSummary.value", submit_project)
        self.assertIn("status: elements.newProjectStatus.value", submit_project)
        self.assertIn("selectProject(result.project.slug)", submit_project)

    def test_initial_load_does_not_contend_with_offscreen_canonical_collections(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        bootstrap = javascript[javascript.index("bindHudTooltipEvents();") :]

        self.assertIn('loadTasks({ reason: "initial" })', bootstrap)
        self.assertNotIn("loadProjects();", bootstrap)
        self.assertNotIn("loadAgents();", bootstrap)
        self.assertNotIn("loadProposals();", bootstrap)
        self.assertNotIn("loadSystemTickets();", bootstrap)
        self.assertNotIn("loadSystemTickets({ force: true })", bootstrap)
        self.assertIn('view === "projects" && !state.projectsLoaded', javascript)
        self.assertIn('view === "inbox" && !state.proposalsLoaded', javascript)
        self.assertIn('view === "agent-work" && !state.agentsLoaded', javascript)

    def test_verified_todo_mutation_receipt_updates_ui_without_duplicate_read(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        create = javascript[javascript.index("async function createTaskTodo") : javascript.index("async function editTaskTodo")]
        edit = javascript[javascript.index("async function editTaskTodo") : javascript.index("async function commentOnTodo")]
        comment = javascript[javascript.index("async function commentOnTodo") : javascript.index("async function changeTodoStatus")]
        status = javascript[javascript.index("async function changeTodoStatus") : javascript.index("function renderProposalDecisionTimeline")]

        self.assertIn("function applyVerifiedTodoMutation", javascript)
        for mutation in (create, edit, comment, status):
            self.assertIn("applyVerifiedTodoMutation", mutation)
            self.assertNotIn("await refreshTaskTodos", mutation.split("catch", 1)[0])

    def test_cold_canonical_reads_poll_accepted_state_until_terminal_response(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        task_load = javascript[javascript.index("async function performTaskLoad") : javascript.index("function loadTasks")]
        proposal_load = javascript[javascript.index("async function performProposalLoad") : javascript.index("function loadProposals")]

        self.assertIn('response.status === 202', task_load)
        self.assertIn('scheduleSurfacePoll("tasks")', task_load)
        self.assertIn('["initial", "poll"].includes(reason)', task_load)
        self.assertIn('payload.read_state?.refreshing', proposal_load)
        self.assertIn('scheduleSurfacePoll("proposals")', proposal_load)

    def test_task_list_board_calendar_and_agent_surfaces_use_todo_terminology(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        task_row = javascript[javascript.index("function taskRow") : javascript.index("function section")]
        board = javascript[javascript.index("function boardCard") : javascript.index("function renderBoard")]
        calendar = javascript[javascript.index("function renderWeekView") : javascript.index("function boardCard")]
        agent_work = javascript[javascript.index("function renderAgentWorkView") : javascript.index("function renderCoordinatorSummary")]

        self.assertIn("function todoSummary(task)", javascript)
        self.assertIn("todoSummary(task)", task_row)
        self.assertIn("todoSummary(task)", board)
        self.assertIn("todoSummary(task)", calendar)
        self.assertIn("open_todos", agent_work)
        self.assertIn("To Do", agent_work)

    def test_calendar_has_default_on_ical_events_filter(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("showIcalEvents: true", javascript)
        self.assertIn("function calendarEventsFilter", javascript)
        self.assertIn('"Show iCal Events"', javascript)
        self.assertIn("input.checked = state.showIcalEvents", javascript)
        self.assertIn("Connect Calendar", javascript)
        self.assertIn("Manage calendars", javascript)
        self.assertIn('fetch("/api/ical-access", { method: "POST"', javascript)
        self.assertIn('fetch("/api/ical-calendars"', javascript)
        self.assertIn('fetch("/api/ical-preferences"', javascript)
        self.assertIn("Calendar permission was not granted", javascript)
        self.assertIn("Local Calendar is unavailable", javascript)
        self.assertIn("icalEventsForDay", javascript)
        self.assertIn("Full Access to Calendar", html)

    def test_calendar_picker_is_compact_and_saves_with_verified_feedback(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="calendar-picker-saving"', html)
        self.assertIn('id="calendar-picker-submit"', html)
        self.assertIn("Saving calendar selection…", html)
        self.assertIn("Calendar selection saved and verified.", javascript)
        self.assertIn("await loadCalendarPicker()", javascript)
        self.assertIn("Calendar selection readback did not match", javascript)
        self.assertIn("calendarPreferencesNotice", javascript)
        self.assertIn(".calendar-picker-option input", css)
        self.assertIn("width: 14px", css)
        self.assertIn("grid-template-columns: 14px minmax(0, 1fr)", css)
        self.assertIn("flex: 1 0 100%", css)

    def test_calendar_failure_stops_retry_loop_and_always_offers_reauthorization(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        calendar_filter = javascript[
            javascript.index("function calendarEventsFilter") : javascript.index("function openCalendarAccessDialog")
        ]
        event_loader = javascript[
            javascript.index("async function ensureIcalEvents") : javascript.index("function icalEventsForDay")
        ]

        self.assertIn('const wrapper = node("div", "calendar-events-filter")', calendar_filter)
        self.assertIn('const checkboxLabel = node("label", "calendar-events-toggle")', calendar_filter)
        self.assertIn('state.icalStatus !== "authorized"', calendar_filter)
        self.assertIn('"Reauthorize Calendar"', calendar_filter)
        self.assertIn("state.icalRange = range", event_loader)
        self.assertIn('if (state.icalStatus !== "authorized") return', event_loader)
        self.assertLess(event_loader.index("state.icalRange = range"), event_loader.index('state.icalStatus = "unavailable"'))

    def test_inbox_task_rows_and_proposals_open_read_only_details_without_edit(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        task_row = javascript[javascript.index("function taskRow") : javascript.index("function section")]
        proposal_card = javascript[javascript.index("function proposalCard") : javascript.index("function renderProposedWork")]

        self.assertIn(
            'button.addEventListener("click", () => selectTask(task.slug, null, button))',
            task_row,
        )
        self.assertIn('row.addEventListener("click", (event) =>', task_row)
        self.assertIn('if (event.target.closest(".task-row-actions")) return;', task_row)
        self.assertIn(
            'const open = () => selectTask(proposal.slug, null, card);',
            proposal_card,
        )
        self.assertIn('card.addEventListener("click", open);', proposal_card)

    def test_proposed_tasks_are_inbox_only_grouped_by_agent_and_confirmation_bound(
        self,
    ) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("Proposed Tasks", javascript)
        self.assertIn("All Agents", javascript)
        self.assertIn('actionIcon("✓", `Approve ${proposal.title}`, { primary: true })', javascript)
        self.assertIn('actionIcon("❌", `Reject ${proposal.title}`', javascript)
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

        self.assertIn('actionIcon("⋯", `Open ${agent.name} profile`', agent_work)
        self.assertIn("openAgentProfile(agent)", agent_work)
        self.assertNotIn("node(\"details\"", agent_work)
        self.assertNotIn("Open Agent Profile", agent_work)
        self.assertIn('node("h3", "", "Current work")', agent_work)
        self.assertIn("No authorized work yet", agent_work)
        self.assertIn("No current task or open To Do recorded.", agent_work)

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

    def test_standard_actions_use_accessible_icon_controls_with_tooltips(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('rel="icon" href="/favicon.svg"', html)
        self.assertIn('class="mc-mark"', html)
        self.assertIn('data-tooltip="Create Task"', html)
        self.assertIn('aria-label="Edit task"', html)
        self.assertIn('aria-label="Duplicate task"', html)
        self.assertIn('aria-label="Approve task"', html)
        self.assertIn("function actionIcon", javascript)
        self.assertIn("setHudTooltip(button, label)", javascript)
        self.assertIn("actionIcon(\"✎\"", javascript)
        self.assertIn("actionIcon(\"⧉\"", javascript)
        self.assertIn("actionIcon(\"✓\"", javascript)
        self.assertIn(".hud-tooltip", css)
        self.assertNotIn(".has-tooltip::after", css)

    def test_navigation_uses_a_narrow_accessible_rail_and_expands_labels_on_mobile(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('aria-label="Inbox" data-tooltip="Inbox"', html)
        self.assertIn('class="nav-icon" aria-hidden="true"', html)
        self.assertIn('class="nav-label">Inbox</span>', html)
        self.assertIn("grid-template-columns: 92px minmax(520px, 1fr) 0", css)
        self.assertIn("grid-template-columns: 92px minmax(500px, 1fr) 360px", css)
        mobile = css[css.index("@media (max-width: 760px)") :]
        self.assertIn(".nav-label,", mobile)
        self.assertIn(".rail-label { display: inline; }", mobile)
        self.assertIn("overflow-x: clip", mobile)
        self.assertIn(".nav-list {", mobile)
        self.assertIn("max-width: 100%", mobile)
        self.assertIn(".app-shell {", css)
        self.assertIn("overflow-x: clip", css)

    def test_mission_control_uses_the_dark_stargraph_family_brand(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('<meta name="color-scheme" content="dark">', html)
        self.assertIn('<meta name="theme-color" content="#020816">', html)
        self.assertIn('/assets/mission-control-command-mark.svg', html)
        self.assertIn('/assets/mission-control-word-art.png', html)
        self.assertIn('class="mission-word-art"', html)
        self.assertIn('--canvas: #020816', css)
        self.assertIn('color-scheme: dark', css)
        self.assertIn('/* Memory Stargraph family dark theme. */', css)
        self.assertIn('grid-template-columns: 92px minmax(520px, 1fr) 0', css)
        mobile = css[css.rindex("@media (max-width: 760px)") :]
        self.assertIn("min-width: 44px", mobile)
        self.assertIn("min-height: 44px", mobile)
        self.assertIn("font-size: 14px", mobile)

    def test_navigation_has_no_counts_and_pages_show_in_context_counts(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertNotIn("nav-count", html)
        self.assertNotIn("data-count=", html)
        self.assertNotIn("system-tickets-count", html)
        self.assertIn('id="view-count"', html)
        self.assertIn("function inContextCountLabel", javascript)
        self.assertIn("elements.viewCount.textContent", javascript)
        self.assertIn('"system-tickets": state.systemTickets.length + (', javascript)

    def test_first_view_cards_keep_long_detail_in_the_detail_panel(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        tickets = javascript[javascript.index("function renderSystemTicketsView") : javascript.index("function openSystemTicketDialog")]
        self.assertIn('node("button", "system-ticket-card")', tickets)
        self.assertIn("selectSystemTicket(ticket.slug)", tickets)
        self.assertNotIn("ticket.verbatim_request", tickets)
        self.assertIn(".system-ticket-card", css)
        self.assertIn(".system-ticket-detail-content", css)

    def test_system_tickets_have_a_separate_planned_task_surface_above_logs(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        tickets_at = html.index('id="system-tickets-button"')
        logs_at = html.index('id="logs-button"')
        self.assertLess(tickets_at, logs_at)
        self.assertIn("Mission Control System Tickets", html)
        self.assertIn('id="system-ticket-dialog"', html)
        self.assertIn('id="system-ticket-request"', html)
        self.assertIn('id="system-ticket-target"', html)
        self.assertIn('id="system-ticket-criteria"', html)
        self.assertIn('id="system-ticket-priority"', html)
        self.assertIn("function renderSystemTicketsView()", javascript)
        self.assertIn('fetch("/api/system-tickets?include_completed=0"', javascript)
        self.assertIn("Nightly work processes every Planned ticket", javascript)
        self.assertIn("state.systemTicketIssues", javascript)
        self.assertIn("state.systemTicketsLoadPromise", javascript)
        self.assertIn("loadSystemTickets({ force: true })", javascript)
        self.assertIn("Ticket data needs attention", javascript)
        self.assertIn("Inspect in Memory Stargraph", javascript)
        self.assertIn("No valid System Tickets are ready to display", javascript)
        self.assertNotIn('value="proposed"', html[html.index('id="system-ticket-dialog"'):html.index('id="task-editor-dialog"')])

    def test_system_ticket_selection_uses_detail_panel_and_ticket_safe_editor(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="system-ticket-detail-content"', html)
        self.assertIn('id="system-ticket-edit-button"', html)
        self.assertIn('id="system-ticket-editor-status"', html)
        self.assertIn("function selectSystemTicket(ticketSlug)", javascript)
        self.assertIn('state.selectedKind = "system-ticket"', javascript)
        self.assertIn('card.setAttribute("aria-current", state.selectedSlug === ticket.slug ? "true" : "false")', javascript)
        self.assertIn("function openEditSystemTicket()", javascript)
        self.assertIn('method: state.systemTicketEditorSlug ? "PATCH" : "POST"', javascript)
        self.assertIn("implementation_receipts", javascript)
        self.assertIn("qa_receipts", javascript)

    def test_completed_system_tickets_are_lazy_and_revealed_five_at_a_time(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        tickets = javascript[
            javascript.index("function renderSystemTicketsView")
            : javascript.index("function openSystemTicketDialog")
        ]
        loader = javascript[
            javascript.index("function loadSystemTickets")
            : javascript.index("async function submitSystemTicket")
        ]
        self.assertIn('"Show Completed Tickets"', tickets)
        self.assertIn('checkbox.type = "checkbox"', tickets)
        self.assertIn('"Show 5 More"', tickets)
        self.assertIn("state.completedSystemTickets", tickets)
        self.assertIn('fetch("/api/system-tickets?include_completed=0"', loader)
        self.assertIn("completed_only=1", loader)
        self.assertIn("limit=5", loader)
        self.assertLess(
            loader.index('fetch("/api/system-tickets?include_completed=0"'),
            loader.index("completed_only=1"),
        )

    def test_tooltips_use_one_fixed_hud_without_native_title_sources(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertNotIn(" title=", html)
        self.assertNotIn('setAttribute("title"', javascript)
        self.assertIn("function setHudTooltip(element, text)", javascript)
        self.assertIn("function bindHudTooltipEvents()", javascript)
        self.assertIn('element.removeAttribute("title")', javascript)
        self.assertIn('className = "hud-tooltip"', javascript)
        render_function = javascript[javascript.index("function render()") : javascript.index("function renderSystemTicketsView()")]
        self.assertIn('document.activeElement?.closest?.(".has-tooltip")', render_function)
        self.assertIn("focusedTooltipTarget?.isConnected", render_function)
        self.assertIn("showHudTooltip(focusedTooltipTarget)", render_function)
        self.assertIn("hideHudTooltip();", render_function)
        self.assertIn(".hud-tooltip", css)
        self.assertIn("position: fixed", css[css.index(".hud-tooltip"):css.index(".hud-tooltip[hidden]")])
        self.assertNotIn(".has-tooltip::after", css)

    def test_footer_controls_group_around_centered_word_art_without_duplicates(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        footer = html[html.index('<div class="mission-art-footer">'):html.index('</main>')]

        self.assertIn('class="footer-controls footer-controls-left"', footer)
        self.assertIn('class="mission-art-center"', footer)
        self.assertIn('class="footer-controls footer-controls-right"', footer)
        self.assertLess(footer.index('id="store-label"'), footer.index('id="system-tickets-button"'))
        self.assertLess(footer.index('id="system-tickets-button"'), footer.index('class="mission-word-art"'))
        self.assertLess(footer.index('id="sidebar-version"'), footer.index('id="logs-button"'))
        self.assertLess(footer.index('id="logs-button"'), footer.index('id="about-button"'))
        for identifier in ("store-label", "system-tickets-button", "logs-button", "about-button"):
            self.assertEqual(html.count(f'id="{identifier}"'), 1)
        self.assertIn("grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr)", css)
        mobile_footer = css[css.rindex("@media (max-width: 760px)") :]
        self.assertIn("padding-bottom: 28px", mobile_footer)
        self.assertIn("top: 100%", mobile_footer)
        self.assertIn("bottom: auto", mobile_footer)

    def test_inbox_uses_one_source_controlled_envelope_check_svg(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        asset = PROJECT_ROOT / "static" / "assets" / "inbox-check.svg"

        self.assertTrue(asset.exists())
        self.assertEqual(html.count('src="/assets/inbox-check.svg"'), 1)
        self.assertIn('aria-label="Inbox"', html)
        self.assertIn(".inbox-nav-icon", css)
        self.assertIn(".nav-item.is-active .inbox-nav-icon", css)

    def test_week_view_groups_canonical_due_dates_without_a_write_path(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('data-view="week"', html)
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

    def test_agent_profile_progressive_controls_and_readable_metadata(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="agent-avatar-toggle"', html)
        self.assertIn('aria-controls="agent-avatar-controls"', html)
        self.assertIn(
            'id="agent-avatar-controls" class="agent-progressive-controls is-hidden"',
            html,
        )
        self.assertIn(
            'id="agent-goal-controls" class="agent-progressive-controls is-hidden"',
            html,
        )
        self.assertNotIn(
            "These assignments use the same canonical GBrain relationship shown in Goal details.",
            html,
        )
        self.assertIn("agentAvatarControlsOpen: false", javascript)
        self.assertIn("agentGoalControlsOpen: false", javascript)
        self.assertIn("function setAgentAvatarControlsOpen", javascript)
        self.assertIn("function setAgentGoalControlsOpen", javascript)
        self.assertIn('actionIcon("-", `Unassign ${goal.title}`)', javascript)
        self.assertIn('actionIcon("+", "Add a goal")', javascript)
        self.assertNotIn('node("button", "row-action-button", "Unassign")', javascript)
        code_styles = css[
            css.index(".agent-profile-summary code")
            : css.index(".agent-current-avatar")
        ]
        self.assertIn("color: #07111f", code_styles)
        goal_styles = css[css.index(".agent-profile-goal-row") :]
        self.assertIn("min-width: 44px", goal_styles)

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
            html.index('data-view="agent-work"')
            : html.index("</button>", html.index('data-view="agent-work"'))
        ]
        self.assertIn('<span class="nav-label">Agents</span>', agent_nav)
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
