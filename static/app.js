const AUTO_REFRESH_MINUTES = 30;
const AUTO_REFRESH_INTERVAL_MS = AUTO_REFRESH_MINUTES * 60 * 1000;
const MEMORY_STARGRAPH_ORIGIN = "http://127.0.0.1:8788";

function renderSafeMarkdown(container, value) {
  const source = typeof value === "string" ? value : "";
  container.replaceChildren();
  const lines = source.split(/\r?\n/);
  const appendInline = (target, text) => {
    const pattern = /\[([^\]]{1,240})\]\(([^)\s]+)\)|\*\*([^*\n]+)\*\*|`([^`\n]+)`/g;
    let cursor = 0;
    for (const match of text.matchAll(pattern)) {
      target.append(document.createTextNode(text.slice(cursor, match.index)));
      if (match[3] !== undefined) {
        const strong = document.createElement("strong");
        strong.textContent = match[3];
        target.append(strong);
      } else if (match[4] !== undefined) {
        const code = document.createElement("code");
        code.textContent = match[4];
        target.append(code);
      } else {
        const url = match[2];
        let href = null;
        if (url.startsWith("/media/")) {
          const mediaUrl = safeStargraphMediaUrl(url);
          if (mediaUrl && /\.(?:png|jpe?g|gif|webp|pdf)$/i.test(mediaUrl.pathname)) {
            href = mediaUrl.href;
          }
        } else if (/^(https:\/\/|http:\/\/127\.0\.0\.1(?::\d+)?\/)/.test(url)) {
          href = url;
        }
        if (href) {
          const link = document.createElement("a");
          link.href = href;
          link.textContent = match[1];
          link.target = "_blank";
          link.rel = "noreferrer";
          target.append(link);
        } else target.append(document.createTextNode(match[0]));
      }
      cursor = match.index + match[0].length;
    }
    target.append(document.createTextNode(text.slice(cursor)));
  };
  let paragraph = [];
  let list = null;
  let listType = null;
  const flushParagraph = () => {
    if (!paragraph.length) return;
    const p = document.createElement("p");
    appendInline(p, paragraph.join("\n"));
    container.append(p);
    paragraph = [];
  };
  const flushList = () => {
    if (list) container.append(list);
    list = null;
    listType = null;
  };
  const flushBlocks = () => {
    flushParagraph();
    flushList();
  };
  const parseTableRow = (line) => {
    const trimmed = line.trim();
    if (!trimmed.startsWith("|") || !trimmed.endsWith("|")) return null;
    return trimmed.slice(1, -1).split("|").map((cell) => cell.trim());
  };
  const isTableSeparator = (cells) => Boolean(
    cells?.length && cells.every((cell) => /^:?-{3,}:?$/.test(cell)),
  );
  const appendTableRow = (row, values, tagName) => {
    values.forEach((value) => {
      const cell = document.createElement(tagName);
      appendInline(cell, value);
      row.append(cell);
    });
  };
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (!line.trim()) { flushBlocks(); continue; }
    const fence = line.match(/^```([A-Za-z0-9_+-]*)\s*$/);
    if (fence) {
      flushBlocks();
      const fencedLines = [];
      index += 1;
      while (index < lines.length && !/^```\s*$/.test(lines[index])) {
        fencedLines.push(lines[index]);
        index += 1;
      }
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      code.textContent = fencedLines.join("\n");
      if (fence[1]) code.dataset.language = fence[1];
      pre.append(code);
      container.append(pre);
      continue;
    }
    const tableHeader = parseTableRow(line);
    const tableSeparator = parseTableRow(lines[index + 1] || "");
    if (
      tableHeader && isTableSeparator(tableSeparator) &&
      tableHeader.length === tableSeparator.length
    ) {
      flushBlocks();
      const table = document.createElement("table");
      const head = document.createElement("thead");
      const headRow = document.createElement("tr");
      appendTableRow(headRow, tableHeader, "th");
      head.append(headRow);
      table.append(head);
      const body = document.createElement("tbody");
      index += 2;
      while (index < lines.length) {
        const values = parseTableRow(lines[index]);
        if (!values || values.length !== tableHeader.length) break;
        const row = document.createElement("tr");
        appendTableRow(row, values, "td");
        body.append(row);
        index += 1;
      }
      index -= 1;
      if (body.childNodes.length) table.append(body);
      const wrap = node("div", "markdown-table-wrap");
      wrap.append(table);
      container.append(wrap);
      continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushBlocks();
      const h = document.createElement(`h${heading[1].length}`);
      appendInline(h, heading[2]);
      container.append(h);
      continue;
    }
    const listItem = line.match(/^\s*(?:([-+*])|(\d+)[.)])\s+(.+)$/);
    if (listItem) {
      flushParagraph();
      const nextListType = listItem[2] ? "ol" : "ul";
      if (listType !== nextListType) {
        flushList();
        listType = nextListType;
        list = document.createElement(listType);
      }
      const item = document.createElement("li");
      appendInline(item, listItem[3]);
      list.append(item);
      continue;
    }
    flushList();
    paragraph.push(line);
  }
  flushBlocks();
  if (!container.childNodes.length) container.textContent = "No additional detail yet.";
}

const AGENT_TASKS_PREFERENCE_KEY = "mission-control.show-agent-tasks";
const AGENT_TASKS_PREFERENCE_COOKIE = "mission-control-show-agent-tasks";
const DETAIL_WIDTH_PREFERENCE_KEY = "mission-control.detail-panel-width";
const DETAIL_WIDTH_DEFAULT = 344;
const DETAIL_WIDTH_MIN = 292;
const DETAIL_WIDTH_MAX = 720;

const state = {
  snapshot: null,
  activeView: "today",
  selectedSlug: null,
  selectedKind: null,
  detailReturnFocus: null,
  artifactTaskReturn: null,
  showCompletedTodos: false,
  allTaskSearch: "",
  showAllTaskDates: false,
  todoAddOpen: false,
  todoReturnFocus: null,
  todoLoadingTask: null,
  weekStart: null,
  calendarMode: "week",
  calendarMonth: null,
  showIcalEvents: true,
  icalEvents: [],
  icalStatus: "not_determined",
  icalConnectionLoaded: false,
  icalConnectionLoading: false,
  icalConnectionError: "",
  calendarPreferencesNotice: "",
  icalRange: "",
  icalLoading: false,
  icalEventsError: "",
  selectedCalendarIds: [],
  availableCalendars: [],
  systemTickets: [],
  completedSystemTickets: [],
  showCompletedSystemTickets: false,
  completedSystemTicketsLoading: false,
  completedSystemTicketsError: "",
  completedSystemTicketsOffset: 0,
  completedSystemTicketsHasMore: false,
  systemTicketIssues: [],
  systemTicketsLoading: false,
  systemTicketsError: "",
  systemTicketsLoadPromise: null,
  systemTicketsReadState: null,
  systemTicketSurfacePollTimer: null,
  systemTicketEditorSlug: null,
  hudTooltip: null,
  hudTooltipTarget: null,
  boardMove: null,
  loading: true,
  releases: null,
  aboutReturnFocus: null,
  logsReturnFocus: null,
  logEvents: [],
  logsNextCursor: null,
  logsLoading: false,
  tasksLoadPromise: null,
  tasksReadState: null,
  taskSurfacePollTimer: null,
  autoRefreshTimer: null,
  autoRefreshDueAt: null,
  refreshDeferred: false,
  lastSyncedAt: null,
  projects: [],
  projectIssues: [],
  projectWarningStateError: "",
  projectsLoaded: false,
  projectsLoading: false,
  projectsError: "",
  projectEditorSlug: null,
  goalAction: null,
  goalEditorSlug: null,
  pendingWarning: null,
  showDismissedWarnings: false,
  taskEditorMode: "create",
  taskEditorSourceSlug: null,
  agents: [],
  agentsLoaded: false,
  agentsLoading: false,
  agentTasks: [],
  agentIssues: [],
  agentWorkLoaded: false,
  agentWorkLoading: false,
  agentWorkError: "",
  showAgentTasks: readAgentTasksPreference(),
  proposals: [],
  proposalIssues: [],
  proposalsLoaded: false,
  proposalsLoading: false,
  proposalsLoadPromise: null,
  proposalsReadState: null,
  proposalSurfacePollTimer: null,
  proposalsError: "",
  proposalAgentFilter: "all",
  proposalAction: null,
  profileAgentSlug: null,
  agentAvatarControlsOpen: false,
  agentGoalControlsOpen: false,
  avatarPreviewUrl: null,
  artifacts: [],
  artifactIssues: [],
  artifactsLoaded: false,
  artifactsLoading: false,
  artifactsError: "",
  artifactsNextCursor: null,
  artifactAgentFilter: "all",
  artifactViewMode: "hierarchy",
  artifactExpanded: new Set(),
  artifactHierarchyInitialized: false,
  artifactTaskFilter: null,
  artifactRequestToken: 0,
  taskArtifacts: new Map(),
};

function setHudTooltip(element, text) {
  if (!element) return;
  const value = String(text || "").trim();
  element.removeAttribute("title");
  if (!value) {
    element.classList.remove("has-tooltip");
    delete element.dataset.tooltip;
    return;
  }
  element.dataset.tooltip = value;
  element.classList.add("has-tooltip");
}

function detailPanelWidthBounds() {
  const maximum = Math.max(
    DETAIL_WIDTH_MIN,
    Math.min(DETAIL_WIDTH_MAX, window.innerWidth - 92 - 320),
  );
  return { minimum: DETAIL_WIDTH_MIN, maximum };
}

function storedDetailPanelWidth() {
  try {
    const stored = window.localStorage.getItem(DETAIL_WIDTH_PREFERENCE_KEY);
    if (stored === null) return null;
    const saved = Number(stored);
    return Number.isFinite(saved) ? saved : null;
  } catch (_) {
    return null;
  }
}

function readDetailPanelWidth() {
  const stored = storedDetailPanelWidth();
  if (stored === null) return DETAIL_WIDTH_DEFAULT;
  return stored;
}

function prepareDetailPanelWidth(kind) {
  const stored = storedDetailPanelWidth();
  if (stored !== null) {
    setDetailPanelWidth(stored, { persist: false });
    return;
  }
  setDetailPanelWidth(
    kind === "artifact" ? Math.round(window.innerWidth * 0.68) : DETAIL_WIDTH_DEFAULT,
    { persist: false },
  );
}

function setDetailPanelWidth(value, { persist = true } = {}) {
  const { minimum, maximum } = detailPanelWidthBounds();
  const numeric = Number(value);
  const width = Math.round(Math.min(
    maximum,
    Math.max(minimum, Number.isFinite(numeric) ? numeric : DETAIL_WIDTH_DEFAULT),
  ));
  document.documentElement.style.setProperty("--detail-panel-width", `${width}px`);
  elements.detailResizeHandle.setAttribute("aria-valuemin", String(minimum));
  elements.detailResizeHandle.setAttribute("aria-valuemax", String(maximum));
  elements.detailResizeHandle.setAttribute("aria-valuenow", String(width));
  if (persist) {
    try {
      window.localStorage.setItem(DETAIL_WIDTH_PREFERENCE_KEY, String(width));
    } catch (_) {
      // A local layout preference must never affect canonical data or details.
    }
  }
  return width;
}

function initializeDetailPanelResize() {
  const handle = elements.detailResizeHandle;
  setDetailPanelWidth(readDetailPanelWidth());
  handle.addEventListener("pointerdown", (event) => {
    if (window.matchMedia("(max-width: 760px)").matches) return;
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = Number(handle.getAttribute("aria-valuenow")) || DETAIL_WIDTH_DEFAULT;
    handle.setPointerCapture?.(event.pointerId);
    document.body.classList.add("is-resizing-detail");
    const move = (moveEvent) => setDetailPanelWidth(
      startWidth + startX - moveEvent.clientX,
      { persist: false },
    );
    const stop = (upEvent) => {
      handle.releasePointerCapture?.(upEvent.pointerId);
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", stop);
      handle.removeEventListener("pointercancel", stop);
      document.body.classList.remove("is-resizing-detail");
      setDetailPanelWidth(handle.getAttribute("aria-valuenow"));
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", stop);
    handle.addEventListener("pointercancel", stop);
  });
  handle.addEventListener("keydown", (event) => {
    const current = Number(handle.getAttribute("aria-valuenow")) || DETAIL_WIDTH_DEFAULT;
    const step = event.shiftKey ? 48 : 16;
    let next = null;
    if (event.key === "ArrowLeft") next = current + step;
    if (event.key === "ArrowRight") next = current - step;
    if (event.key === "Home") next = DETAIL_WIDTH_MIN;
    if (event.key === "End") next = detailPanelWidthBounds().maximum;
    if (event.key === "0") next = DETAIL_WIDTH_DEFAULT;
    if (next === null) return;
    event.preventDefault();
    setDetailPanelWidth(next);
  });
  handle.addEventListener("dblclick", () => setDetailPanelWidth(DETAIL_WIDTH_DEFAULT));
  window.addEventListener("resize", () => setDetailPanelWidth(readDetailPanelWidth()));
}

function syncMobileDetailModalState() {
  const isModal =
    elements.detailPanel.getAttribute("aria-hidden") === "false" &&
    window.matchMedia("(max-width: 760px)").matches;
  [document.querySelector(".sidebar"), document.querySelector(".main-content")]
    .filter(Boolean)
    .forEach((surface) => {
      surface.inert = isModal;
    });
  if (isModal) {
    elements.detailPanel.setAttribute("role", "dialog");
    elements.detailPanel.setAttribute("aria-modal", "true");
  } else {
    elements.detailPanel.removeAttribute("role");
    elements.detailPanel.removeAttribute("aria-modal");
  }
}

function mobileDetailFocusableElements() {
  return Array.from(elements.detailPanel.querySelectorAll(
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )).filter((element) => (
    !element.hidden &&
    !element.closest(".is-hidden") &&
    element.getClientRects().length > 0
  ));
}

function trapMobileDetailFocus(event) {
  if (
    event.key !== "Tab" ||
    elements.detailPanel.getAttribute("aria-modal") !== "true"
  ) return;
  const focusable = mobileDetailFocusableElements();
  if (!focusable.length) {
    event.preventDefault();
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  const active = document.activeElement;
  if (event.shiftKey && (active === first || !elements.detailPanel.contains(active))) {
    event.preventDefault();
    last.focus({ preventScroll: true });
  } else if (!event.shiftKey && (active === last || !elements.detailPanel.contains(active))) {
    event.preventDefault();
    first.focus({ preventScroll: true });
  }
}

function initializeMobileDetailSheet() {
  elements.detailPanel.addEventListener("keydown", trapMobileDetailFocus);
  window.addEventListener("resize", syncMobileDetailModalState);
  syncMobileDetailModalState();
}

function ensureHudTooltipElement() {
  if (state.hudTooltip || !document.body) return state.hudTooltip;
  document.documentElement.classList.add("js-hud-tooltips");
  state.hudTooltip = document.createElement("div");
  state.hudTooltip.className = "hud-tooltip";
  state.hudTooltip.setAttribute("role", "tooltip");
  state.hudTooltip.hidden = true;
  document.body.append(state.hudTooltip);
  return state.hudTooltip;
}

function tooltipTargetFromEvent(event) {
  if (!event.target?.closest) return null;
  const target = event.target.closest(
    ".has-tooltip, button[aria-label], a[aria-label]",
  );
  if (!target) return null;
  setHudTooltip(target, target.dataset.tooltip || target.getAttribute("aria-label"));
  return target;
}

function positionHudTooltip(target) {
  const tooltip = ensureHudTooltipElement();
  if (!tooltip || !target?.isConnected) return;
  const rect = target.getBoundingClientRect();
  const width = tooltip.offsetWidth || 220;
  const height = tooltip.offsetHeight || 36;
  const margin = 10;
  let left = rect.left + rect.width / 2 - width / 2;
  let top = rect.bottom + 8;
  if (top + height + margin > window.innerHeight) top = rect.top - height - 8;
  left = Math.min(window.innerWidth - width - margin, Math.max(margin, left));
  top = Math.min(window.innerHeight - height - margin, Math.max(margin, top));
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

function hideHudTooltip(target = null) {
  if (target && state.hudTooltipTarget && target !== state.hudTooltipTarget) return;
  if (state.hudTooltip) {
    state.hudTooltip.hidden = true;
    state.hudTooltip.textContent = "";
  }
  state.hudTooltipTarget = null;
}

function showHudTooltip(target) {
  const tooltip = ensureHudTooltipElement();
  const tooltipText = String(target?.dataset?.tooltip || "").trim();
  if (!tooltip || !target?.isConnected || !tooltipText) {
    hideHudTooltip();
    return;
  }
  state.hudTooltipTarget = target;
  target.removeAttribute("title");
  tooltip.textContent = tooltipText;
  tooltip.hidden = false;
  positionHudTooltip(target);
}

function bindHudTooltipEvents() {
  ensureHudTooltipElement();
  document.querySelectorAll("[data-tooltip], button[aria-label], a[aria-label]").forEach(
    (element) => setHudTooltip(element, element.dataset.tooltip || element.getAttribute("aria-label")),
  );
  document.addEventListener("pointerover", (event) => {
    const target = tooltipTargetFromEvent(event);
    if (!target || target.contains(event.relatedTarget)) return;
    showHudTooltip(target);
  });
  document.addEventListener("pointerout", (event) => {
    const target = tooltipTargetFromEvent(event);
    if (!target || target.contains(event.relatedTarget)) return;
    hideHudTooltip(target);
  });
  document.addEventListener("focusin", (event) => {
    const target = tooltipTargetFromEvent(event);
    if (target) showHudTooltip(target);
  });
  document.addEventListener("focusout", (event) => {
    const target = tooltipTargetFromEvent(event);
    if (target) hideHudTooltip(target);
  });
  window.addEventListener("scroll", () => hideHudTooltip(), true);
  window.addEventListener("resize", () => hideHudTooltip());
}

function agentTasksPreferenceCookie() {
  const prefix = `${AGENT_TASKS_PREFERENCE_COOKIE}=`;
  return document.cookie
    .split(";")
    .map((entry) => entry.trim())
    .find((entry) => entry.startsWith(prefix))
    ?.slice(prefix.length);
}

function readAgentTasksPreference() {
  // Cookies are the durable fallback for browser surfaces that deliberately
  // isolate localStorage across reloads. This remains a local view preference;
  // it never reaches GBrain or any Mission Control write route.
  const cookieValue = agentTasksPreferenceCookie();
  if (cookieValue === "true" || cookieValue === "false") {
    return cookieValue === "true";
  }
  try {
    return window.localStorage.getItem(AGENT_TASKS_PREFERENCE_KEY) === "true";
  } catch (_) {
    return false;
  }
}

function setAgentTasksVisible(visible) {
  state.showAgentTasks = Boolean(visible);
  elements.showAgentTasks.checked = state.showAgentTasks;
  try {
    window.localStorage.setItem(
      AGENT_TASKS_PREFERENCE_KEY,
      String(state.showAgentTasks),
    );
  } catch (_) {
    // A view preference is optional; unavailable browser storage must not
    // affect GBrain reads or board rendering for this page session.
  }
  document.cookie = `${AGENT_TASKS_PREFERENCE_COOKIE}=${state.showAgentTasks}; Path=/; Max-Age=31536000; SameSite=Lax`;
  if (state.showAgentTasks && !state.agentWorkLoaded) {
    void loadAgentWork();
  }
  render();
}

const viewMeta = {
  inbox: {
    title: "Inbox",
    emptyTitle: "Your inbox is clear",
    emptyCopy: "Create Task opens the full canonical task form, including due date, assignee, and optional progress tracking.",
  },
  today: {
    title: "Today’s Action List",
  },
  all: {
    title: "All Tasks",
    emptyTitle: "No tasks match this view",
    emptyCopy: "Adjust the search or show tasks outside the default date range.",
  },
  week: {
    title: "Calendar",
  },
  board: {
    title: "Board",
  },
  "agent-work": {
    title: "Agents",
    emptyTitle: "No agent work yet",
    emptyCopy: "No active agent has typed work items in its canonical GBrain collection.",
  },
  artifacts: {
    title: "Artifacts",
    emptyTitle: "No Agent Artifacts yet",
    emptyCopy: "Durable Agent deliverables linked to authorized Tasks will appear here after canonical GBrain readback.",
  },
  "system-tickets": { title: "Mission Control System Tickets" },
  blocked: {
    title: "Blocked",
    emptyTitle: "No blocked work",
    emptyCopy: "Tasks that need an unblock will stay visible here and in Today’s Action List.",
  },
  projects: {
    title: "Projects",
    emptyTitle: "No project tasks yet",
    emptyCopy: "Project membership comes directly from each task’s typed GBrain relationships.",
  },
  goals: {
    title: "Goals",
    emptyTitle: "No goals yet",
    emptyCopy: "Goals linked to Tony’s Goals in GBrain will appear here automatically.",
  },
  completed: {
    title: "Completed",
    emptyTitle: "No completed tasks yet",
    emptyCopy: "Completed tasks keep their identity and history when they move to the completed collection.",
  },
};

const elements = {
  appShell: document.querySelector(".app-shell"),
  viewSurface: document.querySelector("#view-surface"),
  viewTitle: document.querySelector("#view-title"),
  viewCount: document.querySelector("#view-count"),
  dateLabel: document.querySelector("#date-label"),
  syncLabel: document.querySelector("#sync-label"),
  autoRefreshLabel: document.querySelector("#auto-refresh-label"),
  refreshButton: document.querySelector("#refresh-button"),
  boardAgentFilter: document.querySelector("#board-agent-filter"),
  showAgentTasks: document.querySelector("#show-agent-tasks"),
  artifactAgentFilter: document.querySelector("#artifact-agent-filter"),
  artifactAgentSelect: document.querySelector("#artifact-agent-select"),
  boardStatusAlert: document.querySelector("#board-status-alert"),
  boardStatusMessage: document.querySelector("#board-status-message"),
  boardStatusRetry: document.querySelector("#board-status-retry"),
  storeDot: document.querySelector("#store-dot"),
  storeLabel: document.querySelector("#store-label"),
  createTaskButton: document.querySelector("#create-task-button"),
  systemTicketsButton: document.querySelector("#system-tickets-button"),
  systemTicketDialog: document.querySelector("#system-ticket-dialog"),
  systemTicketForm: document.querySelector("#system-ticket-form"),
  systemTicketClose: document.querySelector("#system-ticket-close"),
  systemTicketHeading: document.querySelector("#system-ticket-heading"),
  systemTicketTitle: document.querySelector("#system-ticket-title"),
  systemTicketRequest: document.querySelector("#system-ticket-request"),
  systemTicketTarget: document.querySelector("#system-ticket-target"),
  systemTicketPriority: document.querySelector("#system-ticket-priority"),
  systemTicketCriteria: document.querySelector("#system-ticket-criteria"),
  systemTicketError: document.querySelector("#system-ticket-error"),
  systemTicketEditorMode: document.querySelector("#system-ticket-editor-mode"),
  systemTicketEditorStatusField: document.querySelector("#system-ticket-editor-status-field"),
  systemTicketEditorStatus: document.querySelector("#system-ticket-editor-status"),
  systemTicketSubmit: document.querySelector("#system-ticket-submit"),
  taskEditorDialog: document.querySelector("#task-editor-dialog"),
  taskEditorForm: document.querySelector("#task-editor-form"),
  taskEditorMode: document.querySelector("#task-editor-mode"),
  taskEditorHeading: document.querySelector("#task-editor-heading"),
  taskEditorClose: document.querySelector("#task-editor-close"),
  taskEditorTitle: document.querySelector("#task-editor-title"),
  taskEditorDetail: document.querySelector("#task-editor-detail"),
  taskEditorPriority: document.querySelector("#task-editor-priority"),
  taskEditorStatusField: document.querySelector("#task-editor-status-field"),
  taskEditorStatus: document.querySelector("#task-editor-status"),
  taskEditorDue: document.querySelector("#task-editor-due"),
  taskEditorInitialTodoField: document.querySelector("#task-editor-initial-todo-field"),
  taskEditorInitialTodo: document.querySelector("#task-editor-initial-todo"),
  taskEditorAssigneeField: document.querySelector("#task-editor-assignee-field"),
  taskEditorAssignee: document.querySelector("#task-editor-assignee"),
  taskEditorHandoffField: document.querySelector("#task-editor-handoff-field"),
  taskEditorHandoffReason: document.querySelector("#task-editor-handoff-reason"),
  taskEditorProject: document.querySelector("#task-editor-project"),
  taskEditorGoal: document.querySelector("#task-editor-goal"),
  taskTrackMetric: document.querySelector("#task-track-metric"),
  taskMetricFields: document.querySelector("#task-metric-fields"),
  taskMetricLabel: document.querySelector("#task-metric-label"),
  taskMetricTarget: document.querySelector("#task-metric-target"),
  taskMetricCurrent: document.querySelector("#task-metric-current"),
  taskMetricEventBinding: document.querySelector("#task-metric-event-binding"),
  taskMetricBindingCopy: document.querySelector("#task-metric-binding-copy"),
  taskMetricPreview: document.querySelector("#task-metric-preview"),
  taskEditorError: document.querySelector("#task-editor-error"),
  taskEditorSubmit: document.querySelector("#task-editor-submit"),
  taskEditorSaveApprove: document.querySelector("#task-editor-save-approve"),
  taskEditorSafety: document.querySelector("#task-editor-safety"),
  agentProfileDialog: document.querySelector("#agent-profile-dialog"),
  agentProfileHeading: document.querySelector("#agent-profile-heading"),
  agentProfileSummary: document.querySelector("#agent-profile-summary"),
  agentProfileClose: document.querySelector("#agent-profile-close"),
  agentProfileGoals: document.querySelector("#agent-profile-goals"),
  agentAvatarToggle: document.querySelector("#agent-avatar-toggle"),
  agentAvatarControls: document.querySelector("#agent-avatar-controls"),
  agentGoalControls: document.querySelector("#agent-goal-controls"),
  agentGoalSelect: document.querySelector("#agent-goal-select"),
  agentGoalAdd: document.querySelector("#agent-goal-add"),
  agentGoalError: document.querySelector("#agent-goal-error"),
  agentAvatarForm: document.querySelector("#agent-avatar-form"),
  agentCurrentAvatar: document.querySelector("#agent-current-avatar"),
  agentCurrentAvatarImage: document.querySelector("#agent-current-avatar-image"),
  agentCurrentAvatarInitials: document.querySelector("#agent-current-avatar-initials"),
  agentCurrentAvatarFilename: document.querySelector("#agent-current-avatar-filename"),
  agentAvatarFile: document.querySelector("#agent-avatar-file"),
  agentAvatarPreview: document.querySelector("#agent-avatar-preview"),
  agentAvatarFilename: document.querySelector("#agent-avatar-filename"),
  agentAvatarError: document.querySelector("#agent-avatar-error"),
  agentAvatarSubmit: document.querySelector("#agent-avatar-submit"),
  agentAvatarState: document.querySelector("#agent-avatar-state"),
  proposalReviewDialog: document.querySelector("#proposal-review-dialog"),
  proposalReviewForm: document.querySelector("#proposal-review-form"),
  proposalReviewClose: document.querySelector("#proposal-review-close"),
  proposalReviewCancel: document.querySelector("#proposal-review-cancel"),
  proposalReviewName: document.querySelector("#proposal-review-name"),
  proposalReviewRationale: document.querySelector("#proposal-review-rationale"),
  proposalReviewNextStep: document.querySelector("#proposal-review-next-step"),
  proposalReviewDue: document.querySelector("#proposal-review-due"),
  proposalReviewError: document.querySelector("#proposal-review-error"),
  proposalReviewSubmit: document.querySelector("#proposal-review-submit"),
  proposalDecisionDialog: document.querySelector("#proposal-decision-dialog"),
  proposalDecisionTitle: document.querySelector("#proposal-decision-title"),
  proposalDecisionCopy: document.querySelector("#proposal-decision-copy"),
  proposalDecisionNote: document.querySelector("#proposal-decision-note"),
  proposalDecisionError: document.querySelector("#proposal-decision-error"),
  proposalDecisionRepair: document.querySelector("#proposal-decision-repair"),
  proposalDecisionClose: document.querySelector("#proposal-decision-close"),
  proposalDecisionCancel: document.querySelector("#proposal-decision-cancel"),
  proposalDecisionSubmit: document.querySelector("#proposal-decision-submit"),
  calendarAccessDialog: document.querySelector("#calendar-access-dialog"),
  calendarAccessClose: document.querySelector("#calendar-access-close"),
  calendarAccessCancel: document.querySelector("#calendar-access-cancel"),
  calendarAccessRequest: document.querySelector("#calendar-access-request"),
  calendarAccessError: document.querySelector("#calendar-access-error"),
  calendarPickerDialog: document.querySelector("#calendar-picker-dialog"),
  calendarPickerForm: document.querySelector("#calendar-picker-form"),
  calendarPickerClose: document.querySelector("#calendar-picker-close"),
  calendarPickerCancel: document.querySelector("#calendar-picker-cancel"),
  calendarPickerList: document.querySelector("#calendar-picker-list"),
  calendarPickerSaving: document.querySelector("#calendar-picker-saving"),
  calendarPickerSubmit: document.querySelector("#calendar-picker-submit"),
  calendarPickerError: document.querySelector("#calendar-picker-error"),
  detailPanel: document.querySelector("#detail-panel"),
  detailResizeHandle: document.querySelector("#detail-resize-handle"),
  detailEmpty: document.querySelector("#detail-empty"),
  detailContent: document.querySelector("#detail-content"),
  artifactDetailContent: document.querySelector("#artifact-detail-content"),
  artifactDetailClose: document.querySelector("#artifact-detail-close"),
  artifactDetailKind: document.querySelector("#artifact-detail-kind"),
  artifactDetailTitle: document.querySelector("#artifact-detail-title"),
  artifactDetailMeta: document.querySelector("#artifact-detail-meta"),
  artifactDetailMarkdown: document.querySelector("#artifact-detail-markdown"),
  artifactDetailAttachments: document.querySelector("#artifact-detail-attachments"),
  artifactDetailAgent: document.querySelector("#artifact-detail-agent"),
  artifactDetailTask: document.querySelector("#artifact-detail-task"),
  artifactDetailProject: document.querySelector("#artifact-detail-project"),
  artifactDetailGoal: document.querySelector("#artifact-detail-goal"),
  artifactDetailCreated: document.querySelector("#artifact-detail-created"),
  artifactDetailGbrainLink: document.querySelector("#artifact-detail-gbrain-link"),
  artifactDetailSlug: document.querySelector("#artifact-detail-slug"),
  goalDetailContent: document.querySelector("#goal-detail-content"),
  systemTicketDetailContent: document.querySelector("#system-ticket-detail-content"),
  projectDetailContent: document.querySelector("#project-detail-content"),
  projectDetailStatus: document.querySelector("#project-detail-status"),
  projectEditButton: document.querySelector("#project-edit-button"),
  projectDetailClose: document.querySelector("#project-detail-close"),
  projectDetailTitle: document.querySelector("#project-detail-title"),
  projectDetailSummary: document.querySelector("#project-detail-summary"),
  projectDetailCreated: document.querySelector("#project-detail-created"),
  projectDetailUpdated: document.querySelector("#project-detail-updated"),
  projectDetailGoals: document.querySelector("#project-detail-goals"),
  projectDetailTasks: document.querySelector("#project-detail-tasks"),
  projectDetailGbrainLink: document.querySelector("#project-detail-gbrain-link"),
  projectDetailSlug: document.querySelector("#project-detail-slug"),
  systemTicketDetailStatus: document.querySelector("#system-ticket-detail-status"),
  systemTicketEditButton: document.querySelector("#system-ticket-edit-button"),
  systemTicketDetailClose: document.querySelector("#system-ticket-detail-close"),
  systemTicketDetailTitle: document.querySelector("#system-ticket-detail-title"),
  systemTicketDetailPriority: document.querySelector("#system-ticket-detail-priority"),
  systemTicketDetailTarget: document.querySelector("#system-ticket-detail-target"),
  systemTicketDetailCreated: document.querySelector("#system-ticket-detail-created"),
  systemTicketDetailUpdated: document.querySelector("#system-ticket-detail-updated"),
  systemTicketDetailRequest: document.querySelector("#system-ticket-detail-request"),
  systemTicketDetailCriteria: document.querySelector("#system-ticket-detail-criteria"),
  systemTicketDetailEvidence: document.querySelector("#system-ticket-detail-evidence"),
  systemTicketDetailImplementation: document.querySelector("#system-ticket-detail-implementation"),
  systemTicketDetailQa: document.querySelector("#system-ticket-detail-qa"),
  systemTicketDetailError: document.querySelector("#system-ticket-detail-error"),
  systemTicketDetailGbrainLink: document.querySelector("#system-ticket-detail-gbrain-link"),
  systemTicketDetailSlug: document.querySelector("#system-ticket-detail-slug"),
  calendarEventDetail: document.querySelector("#calendar-event-detail"),
  calendarEventDetailClose: document.querySelector("#calendar-event-detail-close"),
  calendarEventDetailTitle: document.querySelector("#calendar-event-detail-title"),
  calendarEventDetailList: document.querySelector("#calendar-event-detail-list"),
  calendarEventDetailNotesSection: document.querySelector("#calendar-event-detail-notes-section"),
  calendarEventDetailNotes: document.querySelector("#calendar-event-detail-notes"),
  calendarEventDetailUrl: document.querySelector("#calendar-event-detail-url"),
  detailClose: document.querySelector("#detail-close"),
  taskDetailStatus: document.querySelector("#task-detail-status"),
  taskEditButton: document.querySelector("#task-edit-button"),
  taskApproveButton: document.querySelector("#task-approve-button"),
  taskRejectButton: document.querySelector("#task-reject-button"),
  taskDuplicateButton: document.querySelector("#task-duplicate-button"),
  taskOwner: document.querySelector("#task-owner"),
  taskOwnerAvatar: document.querySelector("#task-owner-avatar"),
  taskOwnerName: document.querySelector("#task-owner-name"),
  taskHandoffPanel: document.querySelector("#task-handoff-panel"),
  taskHandoffHeading: document.querySelector("#task-handoff-heading"),
  taskHandoffCopy: document.querySelector("#task-handoff-copy"),
  taskHandoffQuestion: document.querySelector("#task-handoff-question"),
  taskHandoffAnswerForm: document.querySelector("#task-handoff-answer-form"),
  taskHandoffAnswer: document.querySelector("#task-handoff-answer"),
  taskHandoffSubmit: document.querySelector("#task-handoff-submit"),
  taskHandoffError: document.querySelector("#task-handoff-error"),
  taskTodoShowCompleted: document.querySelector("#task-todo-show-completed"),
  taskTodoAddToggle: document.querySelector("#task-todo-add-toggle"),
  taskTodoAddForm: document.querySelector("#task-todo-add-form"),
  taskTodoText: document.querySelector("#task-todo-text"),
  taskTodoDetail: document.querySelector("#task-todo-detail"),
  taskTodoKind: document.querySelector("#task-todo-kind"),
  taskTodoAdd: document.querySelector("#task-todo-add"),
  taskTodoAddCancel: document.querySelector("#task-todo-add-cancel"),
  taskTodoLoading: document.querySelector("#task-todo-loading"),
  taskTodoEmpty: document.querySelector("#task-todo-empty"),
  taskTodoError: document.querySelector("#task-todo-error"),
  taskTodoList: document.querySelector("#task-todo-list"),
  taskProgressDetail: document.querySelector("#task-progress-detail"),
  taskProgressLabel: document.querySelector("#task-progress-label"),
  taskProgressValue: document.querySelector("#task-progress-value"),
  taskProgressBar: document.querySelector("#task-progress-bar"),
  taskProgressBinding: document.querySelector("#task-progress-binding"),
  detailTitle: document.querySelector("#detail-title"),
  detailCopy: document.querySelector("#detail-copy"),
  proposalDetailMeta: document.querySelector("#proposal-detail-meta"),
  proposalDecisionHistory: document.querySelector("#proposal-decision-history"),
  proposalDecisionTimeline: document.querySelector("#proposal-decision-timeline"),
  detailPriority: document.querySelector("#detail-priority"),
  detailDue: document.querySelector("#detail-due"),
  detailGbrainLink: document.querySelector("#detail-gbrain-link"),
  detailSlug: document.querySelector("#detail-slug"),
  taskGoalNav: document.querySelector("#task-goal-nav"),
  taskGoalValue: document.querySelector("#task-goal-value"),
  taskProjectValue: document.querySelector("#task-project-value"),
  taskArtifacts: document.querySelector("#task-artifacts"),
  taskArtifactsState: document.querySelector("#task-artifacts-state"),
  taskArtifactList: document.querySelector("#task-artifact-list"),
  goalDetailClose: document.querySelector("#goal-detail-close"),
  goalDetailStatus: document.querySelector("#goal-detail-status"),
  goalDetailTitle: document.querySelector("#goal-detail-title"),
  goalDetailOutcome: document.querySelector("#goal-detail-outcome"),
  goalDefaultAgent: document.querySelector("#goal-default-agent"),
  goalDefaultAgentAvatar: document.querySelector("#goal-default-agent-avatar"),
  goalDefaultAgentName: document.querySelector("#goal-default-agent-name"),
  goalDefaultAgentLink: document.querySelector("#goal-default-agent-link"),
  goalDetailTarget: document.querySelector("#goal-detail-target"),
  goalDetailCadence: document.querySelector("#goal-detail-cadence"),
  goalDetailSuccess: document.querySelector("#goal-detail-success"),
  goalProgressLabel: document.querySelector("#goal-progress-label"),
  goalProgressCount: document.querySelector("#goal-progress-count"),
  goalProgressBar: document.querySelector("#goal-progress-bar"),
  goalRelationshipNotice: document.querySelector("#goal-relationship-notice"),
  goalActiveTasks: document.querySelector("#goal-active-tasks"),
  goalCompletedTasks: document.querySelector("#goal-completed-tasks"),
  goalGbrainLink: document.querySelector("#goal-gbrain-link"),
  goalDetailSlug: document.querySelector("#goal-detail-slug"),
  toast: document.querySelector("#toast"),
  aboutButton: document.querySelector("#about-button"),
  sidebarVersion: document.querySelector("#sidebar-version"),
  aboutDialog: document.querySelector("#about-dialog"),
  aboutClose: document.querySelector("#about-close"),
  aboutCurrentVersion: document.querySelector("#about-current-version"),
  releaseHistory: document.querySelector("#release-history"),
  logsButton: document.querySelector("#logs-button"),
  logsDialog: document.querySelector("#logs-dialog"),
  logsClose: document.querySelector("#logs-close"),
  logsFilterForm: document.querySelector("#logs-filter-form"),
  logsSeverity: document.querySelector("#logs-severity"),
  logsComponent: document.querySelector("#logs-component"),
  logsRefresh: document.querySelector("#logs-refresh"),
  logsError: document.querySelector("#logs-error"),
  logsList: document.querySelector("#operational-log-list"),
  logsLoadMore: document.querySelector("#logs-load-more"),
  logsRetention: document.querySelector("#logs-retention"),
  queueReaderStatus: document.querySelector("#queue-reader-status"),
  newProjectDialog: document.querySelector("#new-project-dialog"),
  newProjectForm: document.querySelector("#new-project-form"),
  newProjectTitle: document.querySelector("#new-project-title"),
  newProjectSummaryField: document.querySelector("#new-project-summary-field"),
  newProjectSummary: document.querySelector("#new-project-summary"),
  newProjectStatusField: document.querySelector("#new-project-status-field"),
  newProjectStatus: document.querySelector("#new-project-status"),
  newProjectMode: document.querySelector("#new-project-mode"),
  newProjectHeading: document.querySelector("#new-project-heading"),
  newProjectGoals: document.querySelector("#new-project-goals"),
  newProjectSubmit: document.querySelector("#new-project-submit"),
  newProjectClose: document.querySelector("#new-project-close"),
  newProjectError: document.querySelector("#new-project-error"),
  newGoalDialog: document.querySelector("#new-goal-dialog"),
  newGoalForm: document.querySelector("#new-goal-form"),
  newGoalTitle: document.querySelector("#new-goal-title"),
  newGoalOutcome: document.querySelector("#new-goal-outcome"),
  newGoalSuccess: document.querySelector("#new-goal-success"),
  newGoalStrategy: document.querySelector("#new-goal-strategy"),
  newGoalConstraints: document.querySelector("#new-goal-constraints"),
  newGoalCadence: document.querySelector("#new-goal-cadence"),
  newGoalTarget: document.querySelector("#new-goal-target"),
  newGoalSubmit: document.querySelector("#new-goal-submit"),
  newGoalHeading: document.querySelector("#new-goal-heading"),
  goalEditorMode: document.querySelector("#goal-editor-mode"),
  newGoalClose: document.querySelector("#new-goal-close"),
  newGoalError: document.querySelector("#new-goal-error"),
  goalPauseButton: document.querySelector("#goal-pause-button"),
  goalEditButton: document.querySelector("#goal-edit-button"),
  goalDeleteButton: document.querySelector("#goal-delete-button"),
  goalActionError: document.querySelector("#goal-action-error"),
  goalConfirmDialog: document.querySelector("#goal-confirm-dialog"),
  goalConfirmTitle: document.querySelector("#goal-confirm-title"),
  goalConfirmCopy: document.querySelector("#goal-confirm-copy"),
  goalConfirmError: document.querySelector("#goal-confirm-error"),
  goalConfirmClose: document.querySelector("#goal-confirm-close"),
  goalConfirmCancel: document.querySelector("#goal-confirm-cancel"),
  goalConfirmSubmit: document.querySelector("#goal-confirm-submit"),
  warningDismissDialog: document.querySelector("#warning-dismiss-dialog"),
  warningDismissCopy: document.querySelector("#warning-dismiss-copy"),
  warningDismissError: document.querySelector("#warning-dismiss-error"),
  warningDismissClose: document.querySelector("#warning-dismiss-close"),
  warningDismissCancel: document.querySelector("#warning-dismiss-cancel"),
  warningDismissConfirm: document.querySelector("#warning-dismiss-confirm"),
};

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function parseDay(value) {
  if (!value) return null;
  const [year, month, day] = value.slice(0, 10).split("-").map(Number);
  return new Date(year, month - 1, day);
}

function isoDay(day) {
  const year = day.getFullYear();
  const month = String(day.getMonth() + 1).padStart(2, "0");
  const date = String(day.getDate()).padStart(2, "0");
  return `${year}-${month}-${date}`;
}

function weekStartFor(value) {
  const day = parseDay(value);
  if (!day) return null;
  const offset = (day.getDay() + 6) % 7;
  day.setDate(day.getDate() - offset);
  return isoDay(day);
}

function shiftWeek(start, amount) {
  const day = parseDay(start);
  if (!day) return null;
  day.setDate(day.getDate() + amount * 7);
  return isoDay(day);
}

function formatDay(value, style = "short") {
  const day = parseDay(value);
  if (!day) return "No date";
  return new Intl.DateTimeFormat(undefined, {
    month: style === "long" ? "long" : "short",
    day: "numeric",
    year: style === "long" ? "numeric" : undefined,
  }).format(day);
}

function taskProgressLabel(task) {
  const metric = task?.progress_metric;
  if (!metric) return "";
  const label = metric.label ||
    (metric.unit === "job_application" ? "Job applications" : metric.unit);
  return `${label}: ${metric.current} / ${metric.target}`;
}

function appendTaskProgress(container, task) {
  const label = taskProgressLabel(task);
  if (!label) return;
  container.append(node("span", "metric-progress", label));
}

function dayAfter(value) {
  const day = parseDay(value);
  if (!day) return "";
  day.setDate(day.getDate() + 1);
  const year = day.getFullYear();
  const month = String(day.getMonth() + 1).padStart(2, "0");
  const date = String(day.getDate()).padStart(2, "0");
  return `${year}-${month}-${date}`;
}

function relativeDue(task) {
  if (!task.due_day || !state.snapshot) return { label: "No date", className: "" };
  if (task.due_day === state.snapshot.as_of) {
    return { label: "Today", className: "is-today" };
  }
  if (
    task.due_day < state.snapshot.as_of &&
    !["completed", "cancelled"].includes(task.status)
  ) {
    return { label: formatDay(task.due_day), className: "is-overdue" };
  }
  return { label: formatDay(task.due_day), className: "" };
}

function isOverdueExecutable(task) {
  return Boolean(
    state.snapshot && task.due_day && task.due_day < state.snapshot.as_of &&
    !["completed", "cancelled"].includes(task.status),
  );
}

function taskUiStatus(task) {
  return task.status;
}

function showToast(message) {
  elements.toast.classList.remove(
    "mutation-status", "is-pending", "is-success", "is-error",
  );
  elements.toast.setAttribute("role", "status");
  elements.toast.setAttribute("aria-live", "polite");
  elements.toast.textContent = message;
  elements.toast.classList.remove("is-hidden");
  window.clearTimeout(showToast.timeout);
  showToast.timeout = window.setTimeout(() => {
    elements.toast.classList.add("is-hidden");
  }, 3400);
}

function showMutationStatus(message, phase, { persistent = false } = {}) {
  window.clearTimeout(showToast.timeout);
  elements.toast.classList.remove("is-hidden", "is-pending", "is-success", "is-error");
  elements.toast.classList.add("mutation-status", `is-${phase}`);
  elements.toast.setAttribute("role", phase === "error" ? "alert" : "status");
  elements.toast.setAttribute("aria-live", phase === "error" ? "assertive" : "polite");
  elements.toast.textContent = message;
  if (!persistent) {
    showToast.timeout = window.setTimeout(() => {
      elements.toast.classList.add("is-hidden");
    }, 4200);
  }
}

function renderReleaseHistory() {
  if (!state.releases) return;
  elements.sidebarVersion.textContent = state.releases.current_version;
  elements.aboutCurrentVersion.textContent = state.releases.current_version;
  const fragment = document.createDocumentFragment();
  state.releases.releases.forEach((release) => {
    const article = node("article", "release-entry");
    const heading = node("div", "release-entry-heading");
    heading.append(
      node("strong", "", release.version),
      node("time", "", formatDay(release.date, "long")),
    );
    article.append(
      heading,
      node("h3", "", release.title),
      node("p", "", release.summary),
    );
    fragment.append(article);
  });
  elements.releaseHistory.replaceChildren(fragment);
}

async function loadReleases() {
  try {
    const response = await fetch("/api/releases", {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Release history unavailable.");
    state.releases = payload;
    renderReleaseHistory();
  } catch (_error) {
    elements.releaseHistory.replaceChildren(
      node("p", "release-loading", "Release history is temporarily unavailable."),
    );
  }
}

function openAboutDialog() {
  state.aboutReturnFocus = document.activeElement;
  elements.aboutDialog.showModal();
  window.setTimeout(() => elements.aboutClose.focus(), 0);
}

function closeAboutDialog() {
  elements.aboutDialog.close();
}

function componentLabel(component) {
  const labels = {
    gtasks: "Mission Control",
    queue_reader: "Event Queue Reader",
    broker: "Event Queue Broker",
    consumer: "Durable Consumer",
    handler: "Event Handler",
  };
  return (
    labels[component] ||
    component
      .split("_")
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ")
  );
}

function formatLogTimestamp(timestamp) {
  const value = new Date(timestamp);
  if (Number.isNaN(value.valueOf())) return "Unknown time";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  }).format(value);
}

function renderOperationalLogs() {
  if (!state.logEvents.length) {
    const empty = node("div", "logs-empty");
    empty.append(
      node("span", "logs-empty-mark", "✓"),
      node("h3", "", "No matching operational events"),
      node(
        "p",
        "",
        "There are no privacy-safe log entries for these filters yet.",
      ),
    );
    elements.logsList.replaceChildren(empty);
    return;
  }
  const fragment = document.createDocumentFragment();
  state.logEvents.forEach((event) => {
    const article = node("article", "operational-log-entry");
    const heading = node("div", "operational-log-heading");
    const identity = node("div", "operational-log-identity");
    identity.append(
      node(
        "span",
        `log-severity ${event.severity}`,
        event.severity,
      ),
      node("strong", "", componentLabel(event.component)),
    );
    const time = node("time", "", formatLogTimestamp(event.timestamp));
    time.dateTime = event.timestamp;
    heading.append(identity, time);
    article.append(heading, node("p", "", event.message));
    fragment.append(article);
  });
  elements.logsList.replaceChildren(fragment);
}

function updateLogComponentOptions(components) {
  const current = elements.logsComponent.value;
  const options = [node("option", "", "All components")];
  options[0].value = "";
  components.forEach((component) => {
    const option = node("option", "", componentLabel(component));
    option.value = component;
    options.push(option);
  });
  elements.logsComponent.replaceChildren(...options);
  elements.logsComponent.value = components.includes(current) ? current : "";
}

function renderQueueReaderStatus(status) {
  const stateName = status?.status || "unavailable";
  elements.queueReaderStatus.className =
    `queue-reader-status ${stateName}`;
  const message =
    status?.message ||
    "Event Queue Reader status is unavailable. Mission Control remains available.";
  const counts =
    stateName === "connected"
      ? ` ${status.pending || 0} waiting · ${status.ack_pending || 0} processing · ${status.redelivered || 0} redelivered.`
      : "";
  elements.queueReaderStatus.textContent = `${message}${counts}`;
}

async function loadOperationalLogs({ append = false } = {}) {
  if (state.logsLoading) return;
  state.logsLoading = true;
  elements.logsRefresh.disabled = true;
  elements.logsLoadMore.disabled = true;
  elements.logsError.classList.add("is-hidden");
  elements.logsList.setAttribute("aria-busy", "true");
  if (!append) {
    state.logEvents = [];
    state.logsNextCursor = null;
    elements.logsList.replaceChildren(
      node("p", "release-loading", "Reading operational logs…"),
    );
  }
  const parameters = new URLSearchParams({ limit: "25" });
  if (elements.logsSeverity.value) {
    parameters.set("severity", elements.logsSeverity.value);
  }
  if (elements.logsComponent.value) {
    parameters.set("component", elements.logsComponent.value);
  }
  if (append && state.logsNextCursor !== null) {
    parameters.set("cursor", String(state.logsNextCursor));
  }
  try {
    const response = await fetch(`/api/logs?${parameters.toString()}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || "Operational logs are unavailable.");
    }
    state.logEvents = append
      ? [...state.logEvents, ...result.events]
      : result.events;
    state.logsNextCursor = result.next_cursor;
    updateLogComponentOptions(result.components || []);
    renderQueueReaderStatus(result.queue_reader);
    renderOperationalLogs();
    elements.logsRetention.textContent =
      `Newest first · showing ${state.logEvents.length} of ${result.total} matching events · retains up to ${result.retention_limit}`;
    elements.logsLoadMore.classList.toggle(
      "is-hidden",
      state.logsNextCursor === null,
    );
    if (result.source_errors?.length) {
      elements.logsError.textContent = result.source_errors
        .map((issue) => issue.message)
        .join(" ");
      elements.logsError.classList.remove("is-hidden");
    }
  } catch (error) {
    if (!append) renderOperationalLogs();
    elements.logsError.textContent =
      `${error.message || "Operational logs are unavailable."} Mission Control remains available.`;
    elements.logsError.classList.remove("is-hidden");
    renderQueueReaderStatus(null);
  } finally {
    state.logsLoading = false;
    elements.logsRefresh.disabled = false;
    elements.logsLoadMore.disabled = false;
    elements.logsList.removeAttribute("aria-busy");
  }
}

function openLogsDialog() {
  state.logsReturnFocus = document.activeElement;
  elements.logsDialog.showModal();
  loadOperationalLogs();
  window.setTimeout(() => elements.logsClose.focus(), 0);
}

function closeLogsDialog() {
  elements.logsDialog.close();
}

function allTodayTasks() {
  if (!state.snapshot) return [];
  const groups = state.snapshot.today;
  return [
    ...groups.in_progress,
    ...groups.todays_actions,
    ...visibleBlockedTasks(),
    ...groups.overdue,
  ];
}

function visibleBlockedTasks() {
  if (!state.snapshot) return [];
  const blocked = [
    ...state.snapshot.views.blocked,
    ...state.agentTasks.filter((task) => task.status === "blocked"),
  ];
  return Array.from(
    new Map(blocked.map((task) => [task.slug, task])).values(),
  );
}

function rebuildDerivedTaskViews() {
  const asOf = state.snapshot.as_of;
  const active = state.snapshot.tasks.filter(
    (task) => task.lifecycle_root === "collections/tonys-tasks",
  );
  const unfinished = (task) =>
    !["completed", "cancelled"].includes(task.status);
  const inProgress = active.filter((task) => task.status === "active");
  state.snapshot.today = {
    in_progress: inProgress.slice(0, 3),
    in_progress_overflow: Math.max(0, inProgress.length - 3),
    todays_actions: active.filter(
      (task) =>
        !["active", "blocked", "completed", "cancelled"].includes(
          task.status,
        ) &&
        (task.due_day === asOf || task.scheduled_day === asOf),
    ),
    waiting_and_blocked: active.filter((task) => task.status === "blocked"),
    overdue: active.filter(
      (task) =>
        !["active", "blocked", "completed", "cancelled"].includes(
          task.status,
        ) &&
        task.due_day &&
        task.due_day < asOf,
    ),
  };
  state.snapshot.views = {
    inbox: active.filter((task) => task.inbox && unfinished(task)),
    blocked: active.filter((task) =>
      task.status === "blocked"),
    projects: active.filter((task) => task.project),
    completed: state.snapshot.tasks.filter(
      (task) =>
        task.status === "completed" ||
        task.lifecycle_root === "collections/tonys-completed-tasks",
    ),
  };
  state.snapshot.goals = state.snapshot.goals.map((goal) => {
    const linked = state.snapshot.tasks.filter(
      (task) => task.goal === goal.slug,
    );
    const activeTasks = linked.filter(unfinished);
    const completedTasks = linked.filter(
      (task) => task.status === "completed",
    );
    return {
      ...goal,
      active_tasks: activeTasks,
      completed_tasks: completedTasks,
      progress: {
        active: activeTasks.length,
        completed: completedTasks.length,
        linked: linked.length,
        percent: linked.length
          ? Math.round((completedTasks.length / linked.length) * 100)
          : 0,
      },
    };
  });
}

function findTaskBySlug(slug) {
  const canonical = (
    state.snapshot?.tasks.find((candidate) => candidate.slug === slug) ||
    state.agentTasks.find((candidate) => candidate.slug === slug)
  );
  if (canonical) return canonical;
  const proposal = state.proposals.find((candidate) => candidate.slug === slug);
  if (!proposal) return null;
  // Proposal reads are deliberately compact for Inbox.  This projection makes
  // a non-action row click immediately useful even while Agent Work is still
  // loading; later canonical agent-work reads reconcile the same slug.
  return {
    ...proposal,
    summary: proposal.title,
    detail: proposal.rationale || "",
    next_action: proposal.proposed_next_step || "",
    priority: "normal",
    due_day: proposal.due_day,
    goal: proposal.linked_goal || null,
    project: null,
    owner_agent: proposal.proposing_agent || null,
    owner: state.agents.find((agent) => agent.slug === proposal.proposing_agent) || null,
    lifecycle_root: null,
    inbox: false,
  };
}

function reconcileVerifiedTask(task) {
  if (!task || typeof task.slug !== "string" || typeof task.status !== "string") {
    const error = new Error(
      "GBrain acknowledged the write, but authoritative task readback was missing.",
    );
    error.code = "ambiguous_readback";
    throw error;
  }
  const index = state.snapshot.tasks.findIndex(
    (candidate) => candidate.slug === task.slug,
  );
  if (index >= 0) {
    state.snapshot.tasks.splice(index, 1, task);
    rebuildDerivedTaskViews();
    render();
    return;
  }
  const agentIndex = state.agentTasks.findIndex(
    (candidate) => candidate.slug === task.slug,
  );
  if (agentIndex >= 0) {
    const previous = state.agentTasks[agentIndex];
    const owner = previous.owner || {
      slug: task.owner_agent,
      name:
        state.agents.find((agent) => agent.slug === task.owner_agent)?.name ||
        task.owner_agent,
      avatar:
        state.agents.find((agent) => agent.slug === task.owner_agent)?.avatar ||
        { kind: "initials", value: "A" },
    };
    state.agentTasks.splice(agentIndex, 1, {
      ...task,
      owner,
      agent_work: true,
      read_only: false,
    });
    render();
    return;
  }
  {
    const error = new Error(
      "GBrain returned a task that is not present in the current Mission Control snapshot.",
    );
    error.code = "ambiguous_readback";
    throw error;
  }
}

function navCounts() {
  if (!state.snapshot) return {
    artifacts: state.artifacts.length,
    "system-tickets": state.systemTickets.length,
  };
  return {
    inbox: state.snapshot.views.inbox.length,
    today: new Set(allTodayTasks().map((task) => task.slug)).size,
    all: filteredAllTasks().length,
    week: currentWeekTasks().length,
    board: state.snapshot.tasks.length,
    "agent-work": state.agentTasks.length,
    artifacts: state.artifacts.length,
    blocked: visibleBlockedTasks().length,
    projects: state.projects.length,
    goals: state.snapshot.goals.length,
    completed: state.snapshot.views.completed.length,
    "system-tickets": state.systemTickets.length + (
      state.showCompletedSystemTickets ? state.completedSystemTickets.length : 0
    ),
  };
}

function systemTicketsColdLoading() {
  const readState = state.systemTicketsReadState;
  return (
    !state.systemTickets.length &&
    !state.systemTicketIssues.length &&
    (
      state.systemTicketsLoading ||
      readState?.status === "loading" ||
      readState?.refreshing === true
    )
  );
}

function currentWeekStart() {
  if (!state.snapshot) return null;
  return state.weekStart || weekStartFor(state.snapshot.as_of);
}

function currentWeekTasks() {
  const start = currentWeekStart();
  if (!start || !state.snapshot) return [];
  const end = shiftWeek(start, 1);
  return state.snapshot.tasks.filter((task) =>
    task.due_day &&
    task.due_day >= start &&
    task.due_day < end &&
    !["completed", "cancelled"].includes(task.status));
}

function renderNavigation() {
  document.querySelectorAll(".nav-item").forEach((button) => {
    const view = button.dataset.view;
    button.classList.toggle("is-active", view === state.activeView);
    button.setAttribute("aria-current", view === state.activeView ? "page" : "false");
  });
}

function inContextCountLabel(view) {
  if (view === "system-tickets" && systemTicketsColdLoading()) {
    return "Reading System Tickets…";
  }
  const count = navCounts()[view] || 0;
  const noun = {
    inbox: "in Inbox",
    today: "tasks today",
    all: count === 1 ? "task shown" : "tasks shown",
    week: "tasks this week",
    board: "tasks on Board",
    "agent-work": "agent work items",
    artifacts: "Agent Artifacts",
    blocked: "blocked tasks",
    projects: "projects",
    goals: "goals",
    completed: "completed tasks",
    "system-tickets": "System Tickets",
  }[view] || "items";
  return `${count} ${noun}`;
}

function actionIcon(symbol, label, { primary = false, className = "" } = {}) {
  const button = node(
    "button",
    `action-icon-button has-tooltip${primary ? " action-icon-primary" : ""}${className ? ` ${className}` : ""}`,
  );
  button.type = "button";
  button.setAttribute("aria-label", label);
  setHudTooltip(button, label);
  const glyph = node("span", "action-icon-glyph", symbol);
  glyph.setAttribute("aria-hidden", "true");
  button.append(glyph);
  return button;
}

function todoSummary(task) {
  const todos = Array.isArray(task.open_todos)
    ? task.open_todos
    : (Array.isArray(task.todos) ? task.todos : []).filter(
      (todo) => todo.status === "not_done",
    );
  if (!todos.length) return "No open TODOs";
  return `TODO: ${todos[0].text}${todos.length > 1 ? ` · +${todos.length - 1} more` : ""}`;
}

function taskRow(task, {
  todayActions = false,
  calendarWeek = false,
  displayRelevantDate = false,
} = {}) {
  const row = node("div", "task-row");
  row.setAttribute("role", "listitem");
  row.classList.toggle("is-selected", state.selectedSlug === task.slug);
  row.classList.toggle("is-overdue-task", isOverdueExecutable(task));
  const button = node("button", "task-row-open");
  button.type = "button";
  button.dataset.slug = task.slug;
  button.setAttribute("aria-label", `Open ${task.title || task.summary}`);
  button.setAttribute("aria-current", state.selectedSlug === task.slug ? "true" : "false");

  const titleWrap = node("span", "task-title-wrap");
  const dot = node("span", `task-state-dot ${taskUiStatus(task)}`);
  dot.setAttribute("aria-hidden", "true");
  const titleText = node("span");
  titleText.append(
    node("span", "task-title", task.title || task.summary),
    node("span", "task-project", task.project || (task.inbox ? "Inbox · No project" : "No project")),
  );
  if (calendarWeek && task.goal) {
    const goal = state.snapshot?.goals.find((item) => item.slug === task.goal);
    titleText.append(
      node("span", "task-goal", `Goal: ${goal?.title || task.goal}`),
    );
  }
  titleWrap.append(dot, titleText);

  const nextAction = node(
    "span",
    "task-next",
    todoSummary(task),
  );
  appendTaskProgress(nextAction, task);
  const end = node("span", "task-end");
  end.append(node("span", `priority-badge ${task.priority}`, task.priority));
  const due = displayRelevantDate
    ? relativeTaskDisplayDate(task)
    : relativeDue(task);
  end.append(node("span", `due-badge ${due.className}`, due.label));

  button.append(titleWrap, nextAction, end);
  button.addEventListener("click", () => selectTask(task.slug, null, button));
  row.append(button);
  // Inbox rows have intentional whitespace around their compact metadata. A
  // click anywhere except an explicit row action is still a detail-selection
  // action, so ordinary and proposed Inbox work do not require Edit to reveal
  // their read-only context.
  row.addEventListener("click", (event) => {
    if (event.target.closest(".task-row-actions")) return;
    if (event.target !== row) return;
    selectTask(task.slug, null, button);
  });
  if (todayActions) {
    const actions = node("div", "task-row-actions");
    const edit = actionIcon("✎", `Edit ${task.title || task.summary}`, { className: "row-action-button" });
    edit.addEventListener("click", () => {
      selectTask(task.slug);
      openEditTask();
    });
    const duplicate = actionIcon("⧉", `Duplicate ${task.title || task.summary}`, { className: "row-action-button" });
    duplicate.addEventListener("click", () => {
      selectTask(task.slug);
      openDuplicateTask();
    });
    actions.append(edit, duplicate);
    row.append(actions);
  }
  return row;
}

function section(title, tasks, emptyCopy, overflow = 0, options = {}) {
  const wrapper = node("section", "task-section");
  const heading = node("div", "section-heading");
  heading.append(
    node("h2", "", title),
    node(
      "span",
      "",
      overflow > 0 ? `${tasks.length} shown · ${overflow} more` : String(tasks.length),
    ),
  );
  wrapper.append(heading);

  if (!tasks.length) {
    wrapper.append(node("div", "section-empty", emptyCopy));
    return wrapper;
  }
  const list = node("div", "task-list");
  list.setAttribute("role", "list");
  tasks.forEach((task) => list.append(taskRow(task, options)));
  wrapper.append(list);
  return wrapper;
}

function emptyActionState() {
  const wrapper = node("section", "action-empty");
  const copy = node("div");
  copy.append(
    node("h2", "", "Your day has room to breathe."),
    node(
      "p",
      "",
      "There are no started, due-today, blocked, or overdue tasks. Add what matters now, or choose something from the Inbox.",
    ),
  );
  const actions = node("div", "inline-actions");
  const addButton = actionIcon("+", "Create Task", { primary: true });
  addButton.addEventListener("click", openCreateTask);
  const inboxButton = node("button", "secondary-button", "Choose from Inbox");
  inboxButton.type = "button";
  inboxButton.addEventListener("click", () => setView("inbox"));
  actions.append(addButton, inboxButton);
  copy.append(actions);
  wrapper.append(copy, node("div", "empty-orbit"));
  return wrapper;
}

function creationEntry(view) {
  const entry = node("section", "create-task-entry");
  entry.append(
    node(
      "p",
      "",
      view === "today"
        ? "Create a task with its due date, assignee, and optional progress tracking."
        : "Create a canonical task with its due date, assignee, and optional progress tracking.",
    ),
  );
  const button = actionIcon("+", "Create Task", { primary: true });
  button.addEventListener("click", openCreateTask);
  entry.append(button);
  return entry;
}

function goalProgressText(goal) {
  const { active, completed, linked } = goal.progress;
  if (!linked) return "No linked tasks";
  if (completed) return `${completed} of ${linked} completed`;
  return `${active} active task${active === 1 ? "" : "s"}`;
}

function goalMiniCard(goal) {
  const button = node("button", "goal-mini-card");
  button.type = "button";
  button.append(
    node("strong", "", goal.title),
  );
  const meta = node("span", "goal-mini-meta");
  meta.append(
    node("span", "", goalProgressText(goal)),
    node("span", "", formatDay(goal.target_day)),
  );
  button.append(meta);
  button.addEventListener("click", () => selectGoal(goal.slug));
  return button;
}

function goalsHomeSection() {
  const activeGoals = state.snapshot.goals.filter((goal) =>
    ["planned", "active"].includes(goal.status));
  const wrapper = node("section", "goals-home-section");
  const heading = node("div", "goals-home-heading");
  const copy = node("div");
  copy.append(
    node("h2", "", "Goals progress"),
    node("p", "", "A compact view after today’s actions."),
  );
  const viewAll = node("button", "goals-link-button", `View all ${state.snapshot.goals.length}`);
  viewAll.type = "button";
  viewAll.addEventListener("click", () => setView("goals"));
  heading.append(copy, viewAll);
  wrapper.append(heading);
  if (!activeGoals.length) {
    wrapper.append(node("div", "section-empty", "No GBrain goals are linked to Tony’s Goals."));
    return wrapper;
  }
  const rail = node("div", "goals-rail");
  activeGoals.forEach((goal) => rail.append(goalMiniCard(goal)));
  wrapper.append(rail);
  return wrapper;
}

function renderToday() {
  const fragment = document.createDocumentFragment();
  const groups = state.snapshot.today;
  const blocked = visibleBlockedTasks();
  fragment.append(creationEntry("today"));
  if (!allTodayTasks().length) fragment.append(emptyActionState());
  fragment.append(
    section(
      "In Progress",
      groups.in_progress,
      "Start one task when you’re ready to focus.",
      groups.in_progress_overflow,
      { todayActions: true },
    ),
    section(
      "Today’s Actions",
      groups.todays_actions,
      "No unstarted task is scheduled or due today.",
      0,
      { todayActions: true },
    ),
    section(
      "Blocked",
      blocked,
      "Nothing is blocked.",
      0,
      { todayActions: true },
    ),
    section(
      "Overdue",
      groups.overdue,
      "No unstarted task is past its due date.",
      0,
      { todayActions: true },
    ),
    goalsHomeSection(),
  );
  return fragment;
}

function simpleEmpty(meta) {
  const wrapper = node("section", "simple-empty");
  const content = node("div");
  content.append(
    node("h2", "", meta.emptyTitle),
    node("p", "", meta.emptyCopy),
  );
  const addButton = actionIcon("+", "Create Task", { primary: true });
  addButton.addEventListener("click", openCreateTask);
  content.append(addButton);
  wrapper.append(content);
  return wrapper;
}

function renderListView(view) {
  const tasks = view === "blocked" ? visibleBlockedTasks() : state.snapshot.views[view] || [];
  if (!tasks.length) {
    const fragment = document.createDocumentFragment();
    if (view === "inbox") fragment.append(creationEntry("inbox"));
    fragment.append(simpleEmpty(viewMeta[view]));
    return fragment;
  }
  const fragment = document.createDocumentFragment();
  if (view === "inbox") fragment.append(creationEntry("inbox"));
  fragment.append(section(viewMeta[view].title, tasks, ""));
  return fragment;
}

function relativeTaskDisplayDate(task) {
  const relevantDay = task.scheduled_day || task.due_day;
  if (!relevantDay || !state.snapshot) {
    return { label: "Date unavailable", className: "" };
  }
  if (task.scheduled_day) {
    return {
      label: `Scheduled ${formatDay(task.scheduled_day)}`,
      className: task.scheduled_day < state.snapshot.as_of ? "is-overdue" : "",
    };
  }
  return relativeDue(task);
}

function taskInDefaultDisplayWindow(task) {
  if (typeof task.in_default_display_window === "boolean") {
    return task.in_default_display_window;
  }
  if (["active", "blocked"].includes(task.status)) return true;
  const relevantDay = task.scheduled_day || task.due_day;
  if (!relevantDay) return true;
  const scope = state.snapshot?.task_display_scope;
  if (!scope?.start_day || !scope?.end_day) return true;
  return relevantDay >= scope.start_day && relevantDay <= scope.end_day;
}

function taskMatchesSearch(task) {
  const query = state.allTaskSearch.trim().toLocaleLowerCase();
  if (!query) return true;
  const goal = state.snapshot?.goals.find((item) => item.slug === task.goal);
  const project = state.projects.find((item) => item.slug === task.project);
  const todoText = (Array.isArray(task.todos) ? task.todos : [])
    .map((todo) => `${todo.text || ""} ${todo.detail || ""}`)
    .join(" ");
  return [
    task.title,
    task.summary,
    task.detail,
    task.status,
    task.priority,
    task.project,
    project?.title,
    task.goal,
    goal?.title,
    todoText,
  ].some((value) => String(value || "").toLocaleLowerCase().includes(query));
}

function allTasksMatchingSearch() {
  if (!state.snapshot) return [];
  return state.snapshot.tasks
    .filter(taskMatchesSearch)
    .slice()
    .sort((left, right) => {
      const leftDay = left.scheduled_day || left.due_day || "9999-12-31";
      const rightDay = right.scheduled_day || right.due_day || "9999-12-31";
      return leftDay.localeCompare(rightDay) || left.title.localeCompare(right.title);
    });
}

function filteredAllTasks() {
  return allTasksMatchingSearch().filter(
    (task) => state.showAllTaskDates || taskInDefaultDisplayWindow(task),
  );
}

function renderAllTaskResults(container) {
  const matching = allTasksMatchingSearch();
  const visible = filteredAllTasks();
  const outsideCount = matching.filter(
    (task) => !taskInDefaultDisplayWindow(task),
  ).length;
  container.replaceChildren();
  if (!state.showAllTaskDates && outsideCount) {
    const notice = node(
      "p",
      "all-tasks-filter-notice",
      `${outsideCount} matching tasks outside the default range are hidden. Enable Show all dates to include them.`,
    );
    notice.setAttribute("role", "status");
    container.append(notice);
  }
  if (!visible.length) {
    container.append(node(
      "div",
      "section-empty",
      outsideCount && !state.showAllTaskDates
        ? "Matching tasks exist outside the default range. Enable Show all dates to include them."
        : "No tasks match the current search and date scope.",
    ));
  } else {
    const list = node("div", "task-list all-tasks-list");
    list.setAttribute("role", "list");
    visible.forEach((task) => list.append(taskRow(task, { displayRelevantDate: true })));
    container.append(list);
  }
  elements.viewCount.textContent = inContextCountLabel("all");
}

function renderAllTasksView() {
  const wrapper = node("section", "all-tasks-view");
  const toolbar = node("div", "all-tasks-toolbar");
  const searchLabel = document.createElement("label");
  searchLabel.htmlFor = "all-task-search";
  searchLabel.textContent = "Search tasks";
  const input = document.createElement("input");
  input.id = "all-task-search";
  input.type = "search";
  input.value = state.allTaskSearch;
  input.placeholder = "Title, detail, status, project, goal, or TODO";
  input.setAttribute("aria-label", "Search tasks");
  searchLabel.append(input);
  const dateLabel = node("label", "all-tasks-date-toggle");
  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.checked = state.showAllTaskDates;
  toggle.setAttribute("aria-label", "Show tasks outside the default date range");
  dateLabel.append(toggle, node("span", "", "Show all dates"));
  const scope = state.snapshot?.task_display_scope;
  const rangeCopy = scope?.start_day && scope?.end_day
    ? `Default: ${formatDay(scope.start_day)} through ${formatDay(scope.end_day)} · ${scope.timezone}`
    : "Default rolling date range is unavailable; no task is hidden by date.";
  toolbar.append(searchLabel, dateLabel, node("p", "all-tasks-range", rangeCopy));
  const results = node("div", "all-tasks-results");
  input.addEventListener("input", () => {
    state.allTaskSearch = input.value;
    renderAllTaskResults(results);
  });
  toggle.addEventListener("change", () => {
    state.showAllTaskDates = toggle.checked;
    renderAllTaskResults(results);
  });
  wrapper.append(toolbar, results);
  renderAllTaskResults(results);
  return wrapper;
}

function renderWeekView() {
  const start = currentWeekStart();
  if (!start) return simpleEmpty({
    emptyTitle: "Week is unavailable",
    emptyCopy: "Refresh to read the current GBrain task dates.",
  });
  const startDay = parseDay(start);
  const endDay = parseDay(shiftWeek(start, 1));
  const wrapper = node("section", "week-view");
  const controls = node("div", "week-controls");
  const weekMode = node("button", "secondary-button", "Week");
  weekMode.type = "button";
  weekMode.disabled = state.calendarMode === "week";
  weekMode.addEventListener("click", () => { state.calendarMode = "week"; render(); });
  const monthMode = node("button", "secondary-button", "Month");
  monthMode.type = "button";
  monthMode.disabled = state.calendarMode === "month";
  monthMode.addEventListener("click", () => { state.calendarMode = "month"; render(); });
  const previous = node("button", "secondary-button", "Previous week");
  previous.type = "button";
  previous.addEventListener("click", () => {
    state.weekStart = shiftWeek(start, -1);
    render();
  });
  const current = node("button", "secondary-button", "This week");
  current.type = "button";
  current.disabled = start === weekStartFor(state.snapshot.as_of);
  current.addEventListener("click", () => {
    state.weekStart = null;
    render();
  });
  const next = node("button", "secondary-button", "Next week");
  next.type = "button";
  next.addEventListener("click", () => {
    state.weekStart = shiftWeek(start, 1);
    render();
  });
  const icalFilter = calendarEventsFilter();
  controls.append(
    node("p", "week-range", `${formatDay(isoDay(startDay), "long")} – ${formatDay(isoDay(new Date(endDay.getFullYear(), endDay.getMonth(), endDay.getDate() - 1)), "long")}`),
    weekMode,
    monthMode,
    previous,
    current,
    next,
    icalFilter,
  );
  wrapper.append(controls);
  void ensureIcalEvents(start, shiftWeek(start, 1));

  const grid = node("div", "week-grid");
  const tasks = currentWeekTasks();
  for (let offset = 0; offset < 7; offset += 1) {
    const day = new Date(startDay);
    day.setDate(day.getDate() + offset);
    const key = isoDay(day);
    const column = node("section", "week-day");
    if (key === state.snapshot.as_of) column.classList.add("is-today");
    const heading = node("div", "week-day-heading");
    heading.append(
      node("h2", "", new Intl.DateTimeFormat(undefined, { weekday: "short" }).format(day)),
      node("span", "", new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(day)),
    );
    column.append(heading);
    const due = tasks.filter((task) => task.due_day === key);
    if (!due.length) {
      column.append(node("p", "week-day-empty", "No tasks due."));
    } else {
      const list = node("div", "week-task-list");
      list.setAttribute("role", "list");
      due.forEach((task) => list.append(taskRow(task, { calendarWeek: true })));
      column.append(list);
    }
    icalEventsForDay(key).forEach((event) => column.append(calendarEventItem(event, key)));
    grid.append(column);
  }
  wrapper.append(grid);
  return wrapper;
}

function calendarEventsFilter() {
  const wrapper = node("div", "calendar-events-filter");
  const checkboxLabel = node("label", "calendar-events-toggle");
  const input = node("input");
  input.type = "checkbox";
  input.checked = state.showIcalEvents;
  input.addEventListener("change", () => {
    state.showIcalEvents = input.checked;
    if (input.checked) state.icalRange = "";
    render();
  });
  checkboxLabel.append(input, node("span", "", "Show iCal Events"));
  wrapper.append(checkboxLabel);
  const calendarStatus = state.icalConnectionLoading
    ? "Checking Calendar access…"
    : state.icalLoading
      ? "Reading local Calendar…"
      : state.icalStatus === "authorized"
        ? (state.icalEventsError || (state.selectedCalendarIds.length ? `${state.selectedCalendarIds.length} selected read-only calendar${state.selectedCalendarIds.length === 1 ? "" : "s"}` : "Connected · choose calendars to show events"))
        : state.icalStatus === "denied" || state.icalStatus === "restricted"
          ? "Calendar permission was not granted"
          : state.icalStatus === "unavailable"
            ? (state.icalConnectionError || "Local Calendar is unavailable")
            : "Calendar is not connected";
  wrapper.append(node("small", "calendar-events-status", calendarStatus));
  if (state.calendarPreferencesNotice) {
    const notice = node("p", "calendar-preferences-notice", state.calendarPreferencesNotice);
    notice.setAttribute("role", "status");
    wrapper.append(notice);
  }
  if (state.icalConnectionLoading) {
    return wrapper;
  }
  if (state.icalStatus !== "authorized") {
    const connect = node("button", "secondary-button", state.icalStatus === "not_determined" ? "Connect Calendar" : "Reconnect");
    connect.type = "button";
    connect.addEventListener("click", state.icalStatus === "not_determined" ? openCalendarAccessDialog : reconnectCalendar);
    wrapper.append(connect);
  } else {
    const manage = node("button", "secondary-button", "Manage calendars");
    manage.type = "button";
    manage.addEventListener("click", openCalendarPicker);
    wrapper.append(manage);
  }
  return wrapper;
}

async function reconnectCalendar() {
  state.icalConnectionLoaded = false;
  await loadCalendarConnectionState();
  if (state.icalStatus === "authorized") await openCalendarPicker();
  else openCalendarAccessDialog();
}

function openCalendarAccessDialog() {
  elements.calendarAccessError.classList.add("is-hidden");
  elements.calendarAccessRequest.textContent = state.icalStatus === "not_determined" ? "Continue to Apple permission" : "Retry Calendar access";
  elements.calendarAccessDialog.showModal();
  window.setTimeout(() => elements.calendarAccessRequest.focus(), 0);
}

async function loadCalendarPicker() {
  const response = await fetch("/api/ical-calendars", { headers: { Accept: "application/json" }, cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Calendars could not be read.");
  state.icalStatus = payload.status || "unavailable";
  state.availableCalendars = Array.isArray(payload.calendars) ? payload.calendars : [];
  state.selectedCalendarIds = Array.isArray(payload.selected_calendar_ids) ? payload.selected_calendar_ids : [];
  state.icalConnectionLoaded = true;
  state.icalConnectionError = "";
}

async function loadCalendarConnectionState() {
  if (state.icalConnectionLoading) return;
  state.icalConnectionLoading = true;
  if (state.snapshot) render();
  try {
    await loadCalendarPicker();
    if (state.icalStatus === "authorized") state.icalRange = "";
  } catch (error) {
    state.icalStatus = "unavailable";
    state.icalConnectionLoaded = true;
    state.icalConnectionError = error.message || "Local Calendar status could not be read.";
  } finally {
    state.icalConnectionLoading = false;
    if (state.snapshot) render();
  }
}

function renderCalendarPicker() {
  elements.calendarPickerList.replaceChildren();
  if (!state.availableCalendars.length) {
    elements.calendarPickerList.append(node("p", "", "No readable calendars are available."));
    return;
  }
  state.availableCalendars.forEach((calendar) => {
    const label = node("label", "calendar-picker-option");
    const input = node("input"); input.type = "checkbox"; input.value = calendar.id; input.checked = state.selectedCalendarIds.includes(calendar.id);
    label.append(input, node("span", "", calendar.title || "Untitled calendar"));
    elements.calendarPickerList.append(label);
  });
}

async function openCalendarPicker() {
  elements.calendarPickerError.classList.add("is-hidden");
  elements.calendarPickerSaving.classList.add("is-hidden");
  try {
    await loadCalendarPicker();
    renderCalendarPicker();
    elements.calendarPickerDialog.showModal();
    window.setTimeout(() => elements.calendarPickerList.querySelector("input")?.focus(), 0);
  } catch (error) {
    state.icalStatus = "unavailable";
    render();
  }
}

async function ensureIcalEvents(start, end) {
  if (!state.showIcalEvents) return;
  if (state.icalStatus !== "authorized") return;
  const range = `${start}/${end}`;
  if (state.icalRange === range || state.icalLoading) return;
  state.icalLoading = true;
  try {
    const response = await fetch(`/api/ical-events?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`, { headers: { Accept: "application/json" }, cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Calendar is unavailable.");
    state.icalRange = range;
    state.icalStatus = payload.status || "unavailable";
    state.icalEvents = Array.isArray(payload.events) ? payload.events : [];
    state.icalEventsError = "";
    state.selectedCalendarIds = Array.isArray(payload.selected_calendar_ids) ? payload.selected_calendar_ids : state.selectedCalendarIds;
  } catch (error) {
    state.icalRange = range;
    state.icalStatus = "unavailable";
    state.icalEventsError = `${error.message || "Calendar events could not be refreshed."} Previously verified events remain visible.`;
  }
  finally { state.icalLoading = false; render(); }
}

function icalEventsForDay(day) {
  const hasVerifiedStaleEvents = Boolean(state.icalEventsError && state.icalEvents.length);
  if (!state.showIcalEvents || (state.icalStatus !== "authorized" && !hasVerifiedStaleEvents)) return [];
  return state.icalEvents.filter((event) => {
    const startDay = event.start_day || event.day;
    const endDay = event.end_day || startDay;
    return startDay && startDay <= day && day <= endDay;
  });
}

function localEventDate(value) {
  if (typeof value !== "string" || !value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function calendarEventTimeLabel(event, day) {
  if (event.all_day) return "All day";
  const start = localEventDate(event.start);
  const end = localEventDate(event.end);
  if (!start) return "Time unavailable";
  const time = (value) => new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
  }).format(value);
  const startDay = event.start_day || event.day;
  const endDay = event.end_day || startDay;
  if (startDay !== endDay) {
    if (day === startDay) return `${time(start)} →`;
    if (day === endDay && end) return `→ ${time(end)}`;
    return "Continues";
  }
  return end ? `${time(start)}–${time(end)}` : time(start);
}

function calendarEventItem(event, day) {
  const button = node("button", "ical-event");
  button.type = "button";
  button.dataset.slug = event.id || `${event.title || "event"}-${event.start || day}`;
  button.setAttribute("aria-label", `${event.title || "Calendar event"}, ${calendarEventTimeLabel(event, day)}`);
  button.append(
    node("span", "ical-event-time", calendarEventTimeLabel(event, day)),
    node("span", "ical-event-title", event.title || "Calendar event"),
  );
  button.addEventListener("click", () => selectCalendarEvent(event, button));
  return button;
}

function safeCalendarEventUrl(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const parsed = new URL(value);
    if (parsed.protocol === "https:") return parsed.href;
    if (parsed.protocol === "http:" && ["127.0.0.1", "localhost"].includes(parsed.hostname)) return parsed.href;
  } catch (_) {
    return null;
  }
  return null;
}

function appendCalendarDetailRow(label, value) {
  if (value === null || value === undefined || String(value).trim() === "") return;
  const row = document.createElement("div");
  row.append(node("dt", "", label), node("dd", "", String(value)));
  elements.calendarEventDetailList.append(row);
}

function eventDateTimeLabel(event) {
  if (event.all_day) {
    const start = event.start_day || event.day;
    const end = event.end_day || start;
    return start === end ? `${formatDay(start, "long")} · All day` : `${formatDay(start, "long")} – ${formatDay(end, "long")} · All day`;
  }
  const start = localEventDate(event.start);
  const end = localEventDate(event.end);
  if (!start) return "Time unavailable";
  const formatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
  return end ? `${formatter.format(start)} – ${formatter.format(end)}` : formatter.format(start);
}

function selectCalendarEvent(event, origin) {
  const identity = event.id || `${event.title || "event"}-${event.start || event.day || ""}`;
  state.selectedSlug = identity;
  state.selectedKind = "ical-event";
  prepareDetailPanelWidth("calendar-event");
  state.detailReturnFocus = { element: origin, slug: identity };
  elements.detailPanel.setAttribute("aria-hidden", "false");
  elements.detailPanel.setAttribute("aria-label", "Calendar event details");
  elements.detailEmpty.classList.add("is-hidden");
  elements.detailContent.classList.add("is-hidden");
  elements.artifactDetailContent.classList.add("is-hidden");
  elements.goalDetailContent.classList.add("is-hidden");
  elements.projectDetailContent.classList.add("is-hidden");
  elements.systemTicketDetailContent.classList.add("is-hidden");
  elements.calendarEventDetail.classList.remove("is-hidden");
  elements.calendarEventDetailTitle.textContent = event.title || "Calendar event";
  elements.calendarEventDetailList.replaceChildren();
  appendCalendarDetailRow("Calendar", event.calendar_title);
  appendCalendarDetailRow("When", eventDateTimeLabel(event));
  appendCalendarDetailRow("Timezone", event.timezone);
  appendCalendarDetailRow("Location", event.location);
  appendCalendarDetailRow("Recurrence", event.recurrence);
  appendCalendarDetailRow("Availability", event.availability);
  const notes = typeof event.notes === "string" ? event.notes.trim() : "";
  elements.calendarEventDetailNotesSection.classList.toggle("is-hidden", !notes);
  elements.calendarEventDetailNotes.textContent = notes;
  const safeUrl = safeCalendarEventUrl(event.url);
  elements.calendarEventDetailUrl.classList.toggle("is-hidden", !safeUrl);
  if (safeUrl) elements.calendarEventDetailUrl.href = safeUrl;
  else elements.calendarEventDetailUrl.removeAttribute("href");
  render();
  window.requestAnimationFrame(() => elements.calendarEventDetailTitle.focus({ preventScroll: true }));
}

function renderMonthCalendar() {
  const anchor = state.calendarMonth ? parseDay(state.calendarMonth) : parseDay(state.snapshot.as_of);
  const monthStart = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
  const first = new Date(monthStart);
  first.setDate(first.getDate() - ((first.getDay() + 6) % 7));
  const wrapper = node("section", "week-view");
  const controls = node("div", "week-controls");
  const week = node("button", "secondary-button", "Week"); week.type = "button"; week.addEventListener("click", () => { state.calendarMode = "week"; render(); });
  const month = node("button", "secondary-button", "Month"); month.type = "button"; month.disabled = true;
  const previous = node("button", "secondary-button", "Previous month"); previous.type = "button"; previous.addEventListener("click", () => { state.calendarMonth = isoDay(new Date(monthStart.getFullYear(), monthStart.getMonth() - 1, 1)); render(); });
  const current = node("button", "secondary-button", "This month"); current.type = "button"; current.disabled = monthStart.getFullYear() === parseDay(state.snapshot.as_of).getFullYear() && monthStart.getMonth() === parseDay(state.snapshot.as_of).getMonth(); current.addEventListener("click", () => { state.calendarMonth = null; render(); });
  const next = node("button", "secondary-button", "Next month"); next.type = "button"; next.addEventListener("click", () => { state.calendarMonth = isoDay(new Date(monthStart.getFullYear(), monthStart.getMonth() + 1, 1)); render(); });
  controls.append(node("p", "week-range", new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" }).format(monthStart)), week, month, previous, current, next, calendarEventsFilter());
  wrapper.append(controls);
  void ensureIcalEvents(isoDay(first), isoDay(new Date(first.getFullYear(), first.getMonth(), first.getDate() + 42)));
  const grid = node("div", "month-grid");
  for (let index = 0; index < 42; index += 1) {
    const day = new Date(first); day.setDate(day.getDate() + index);
    const key = isoDay(day);
    const cell = node("section", "month-day");
    cell.classList.toggle("is-outside-month", day.getMonth() !== monthStart.getMonth());
    cell.append(node("span", "", String(day.getDate())));
    state.snapshot.tasks.filter((task) => task.due_day === key && !["completed", "cancelled"].includes(task.status)).forEach((task) => {
      const taskButton = node("button", "month-task", task.title || task.summary);
      taskButton.classList.toggle("is-overdue-task", isOverdueExecutable(task));
      taskButton.classList.toggle("is-selected", state.selectedSlug === task.slug);
      taskButton.setAttribute("aria-current", state.selectedSlug === task.slug ? "true" : "false");
      taskButton.setAttribute("aria-description", todoSummary(task));
      taskButton.type = "button"; taskButton.addEventListener("click", () => selectTask(task.slug)); cell.append(taskButton);
    });
    icalEventsForDay(key).forEach((event) => cell.append(calendarEventItem(event, key)));
    grid.append(cell);
  }
  wrapper.append(grid); return wrapper;
}

function renderCalendarView() { return state.calendarMode === "month" ? renderMonthCalendar() : renderWeekView(); }

const boardColumns = [
  {
    title: "Planned",
    status: "planned",
    empty: "No planned tasks.",
  },
  {
    title: "In Progress",
    status: "active",
    empty: "No task is in progress.",
  },
  {
    title: "Blocked",
    status: "blocked",
    empty: "Nothing is blocked.",
  },
  {
    title: "Completed",
    status: "completed",
    empty: "No completed tasks.",
  },
  {
    title: "Cancelled",
    status: "cancelled",
    empty: "No cancelled tasks.",
  },
];

const editableTaskStatuses = [
  { value: "planned", label: "Planned" },
  { value: "active", label: "In Progress" },
  { value: "blocked", label: "Blocked" },
  { value: "completed", label: "Completed" },
  { value: "cancelled", label: "Cancelled" },
];

function boardCard(task) {
  const card = node("article", "board-card");
  card.draggable = true;
  card.dataset.slug = task.slug;
  card.dataset.status = taskUiStatus(task);
  card.classList.toggle("is-selected", state.selectedSlug === task.slug);
  const button = node("button", "board-card-open");
  button.type = "button";
  const isSaving =
    state.boardMove?.phase === "saving" &&
    state.boardMove.taskSlug === task.slug;
  card.classList.toggle("is-saving", isSaving);
  button.disabled = isSaving;
  card.setAttribute("aria-grabbed", "false");
  const heading = node("span", "board-card-heading");
  heading.append(
    node("span", `task-state-dot ${taskUiStatus(task)}`),
    node("strong", "", task.title || task.summary),
  );
  const meta = node("span", "board-card-meta");
  meta.append(
    node("span", "", task.project || (task.inbox ? "Inbox" : "No project")),
    node("span", `priority-badge ${task.priority}`, task.priority),
  );
  button.append(
    ownerBadge(state.snapshot?.owner || {
      name: "Tony",
      avatar: { kind: "initials", value: "T" },
    }),
    heading,
    node("span", "board-card-next", todoSummary(task)),
    meta,
    node("span", `due-badge ${relativeDue(task).className}`, relativeDue(task).label),
  );
  appendTaskProgress(button, task);
  button.addEventListener("click", () => selectTask(task.slug));

  const moveControl = node("label", "board-card-move");
  moveControl.append(node("span", "", "Move to"));
  const statusSelect = node("select");
  statusSelect.setAttribute(
    "aria-label",
    `Move ${task.title || task.summary} to another status`,
  );
  editableTaskStatuses.forEach((status) => {
    const option = node("option", "", status.label);
    option.value = status.value;
    statusSelect.append(option);
  });
  statusSelect.value = taskUiStatus(task);
  statusSelect.disabled = isSaving;
  statusSelect.addEventListener("change", () => {
    moveBoardTask(task.slug, statusSelect.value);
  });
  moveControl.append(statusSelect);
  card.append(button, moveControl);
  if (isSaving) card.append(node("span", "board-card-saving", "Saving in GBrain…"));

  card.addEventListener("dragstart", (event) => {
    if (!event.dataTransfer || isSaving) {
      event.preventDefault();
      return;
    }
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", task.slug);
    card.classList.add("is-dragging");
    card.setAttribute("aria-grabbed", "true");
  });
  card.addEventListener("dragend", () => {
    card.classList.remove("is-dragging");
    card.setAttribute("aria-grabbed", "false");
    document
      .querySelectorAll(".board-column.is-drop-target")
      .forEach((column) => column.classList.remove("is-drop-target"));
  });
  return card;
}

function ownerBadge(owner) {
  const badge = node("span", "agent-owner-badge");
  const avatar = owner.avatar?.kind === "attachment"
    ? document.createElement("img")
    : node("span", `agent-avatar ${owner.avatar?.kind || "initials"}`, owner.avatar?.value || owner.name.slice(0, 1));
  if (owner.avatar?.kind === "attachment") {
    avatar.className = "agent-avatar attachment";
    avatar.src = `http://127.0.0.1:8788${owner.avatar.value}`;
    avatar.alt = `${owner.name} avatar`;
  } else {
    avatar.setAttribute("aria-hidden", "true");
  }
  badge.append(avatar, node("span", "", owner.name));
  return badge;
}

function setCompactAgentAvatar(element, owner) {
  element.classList.remove("attachment");
  element.style.backgroundImage = "";
  if (owner.avatar?.kind === "attachment") {
    element.textContent = "";
    element.classList.add("attachment");
    element.style.backgroundImage = `url("http://127.0.0.1:8788${owner.avatar.value}")`;
    element.setAttribute("aria-label", `${owner.name} avatar`);
    return;
  }
  element.textContent = owner.avatar?.value || owner.name.slice(0, 1);
  element.removeAttribute("aria-label");
}

function agentBoardCard(task) {
  const card = node("article", "board-card agent-board-card");
  card.draggable = true;
  card.dataset.slug = task.slug;
  card.dataset.status = taskUiStatus(task);
  card.classList.toggle("is-selected", state.selectedSlug === task.slug);
  const button = node("button", "board-card-open");
  button.type = "button";
  const isSaving =
    state.boardMove?.phase === "saving" &&
    state.boardMove.taskSlug === task.slug;
  button.disabled = isSaving;
  card.classList.toggle("is-saving", isSaving);
  card.setAttribute("aria-grabbed", "false");
  const heading = node("span", "board-card-heading");
  heading.append(
    node("span", `task-state-dot ${taskUiStatus(task)}`),
    node("strong", "", task.title || task.summary),
  );
  const meta = node("span", "board-card-meta");
  meta.append(
    node("span", "", task.project || "Agent work"),
    node("span", `priority-badge ${task.priority}`, task.priority),
  );
  button.append(
    ownerBadge(task.owner),
    heading,
    node("span", "board-card-next", todoSummary(task)),
    meta,
    node("span", `due-badge ${relativeDue(task).className}`, relativeDue(task).label),
  );
  appendTaskProgress(button, task);
  // Agent work can be refreshed independently of the Board. Keep the exact
  // canonical card payload as a selection fallback so a mobile tap cannot be
  // lost if a read finishes between render and click.
  button.addEventListener("click", () => selectTask(task.slug, task));

  const moveControl = node("label", "board-card-move");
  moveControl.append(node("span", "", "Move to"));
  const statusSelect = node("select");
  statusSelect.setAttribute(
    "aria-label",
    `Move ${task.title || task.summary} for ${task.owner.name} to another status`,
  );
  editableTaskStatuses.forEach((status) => {
    const option = node("option", "", status.label);
    option.value = status.value;
    statusSelect.append(option);
  });
  statusSelect.value = taskUiStatus(task);
  statusSelect.disabled = isSaving;
  statusSelect.addEventListener("change", () => {
    moveBoardTask(task.slug, statusSelect.value);
  });
  moveControl.append(statusSelect);
  card.append(button, moveControl);
  if (isSaving) {
    card.append(node("span", "board-card-saving", "Saving in GBrain…"));
  }
  card.addEventListener("dragstart", (event) => {
    if (!event.dataTransfer || isSaving) {
      event.preventDefault();
      return;
    }
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", task.slug);
    card.classList.add("is-dragging");
    card.setAttribute("aria-grabbed", "true");
  });
  card.addEventListener("dragend", () => {
    card.classList.remove("is-dragging");
    card.setAttribute("aria-grabbed", "false");
  });
  return card;
}

function updateBoardStatus() {
  const move = state.boardMove;
  if (!move) {
    elements.boardStatusAlert.classList.add("is-hidden");
    elements.boardStatusRetry.classList.add("is-hidden");
    return;
  }
  elements.boardStatusAlert.classList.remove("is-hidden");
  if (move.phase === "saving") {
    elements.boardStatusMessage.textContent =
      `Saving ${move.taskTitle} as ${move.statusLabel} in GBrain…`;
    elements.boardStatusRetry.classList.add("is-hidden");
    return;
  }
  elements.boardStatusMessage.textContent = move.message;
  elements.boardStatusRetry.classList.remove("is-hidden");
}

function agentWorkUnavailableMessage() {
  const unavailableProfile = state.agentIssues.find(
    (issue) =>
      issue?.task_visible === false &&
      issue?.category === "core_data" &&
      typeof issue.slug === "string" &&
      issue.slug.startsWith("agents/"),
  );
  return unavailableProfile
    ? "Agent work could not be read from GBrain. Tony’s Board is unchanged; use Refresh after GBrain recovers."
    : "";
}

function renderBoard() {
  const wrapper = node("section", "agent-board-wrapper");
  if (state.showAgentTasks) {
    const unavailable = agentWorkUnavailableMessage();
    wrapper.append(
      node(
        "p",
        "agent-board-note",
        state.agentWorkLoading
          ? "Reading typed agent task collections…"
          : state.agentWorkError
            ? `Agent work is unavailable: ${state.agentWorkError}`
            : unavailable
              ? unavailable
            : state.agentTasks.length
              ? "Agent work is visible with an owner badge and remains distinct from Tony’s personal tasks."
              : "No typed agent work exists yet. Tony’s Board is unchanged.",
      ),
    );
  }
  const board = node("section", "board-grid");
  board.setAttribute("aria-label", "Task status board");
  boardColumns.forEach((definition) => {
    const tasks = state.snapshot.tasks.filter((task) =>
      taskUiStatus(task) === definition.status);
    const agentTasks = state.showAgentTasks
      ? state.agentTasks.filter(
        (task) => task.status !== "proposed" && taskUiStatus(task) === definition.status,
      )
      : [];
    const column = node("section", "board-column");
    column.dataset.status = definition.status;
    column.setAttribute("aria-label", `${definition.title} status lane`);
    const heading = node("div", "board-column-heading");
    heading.append(
      node("h2", "", definition.title),
      node("span", "", String(tasks.length + agentTasks.length)),
    );
    column.append(heading);
    if (tasks.length || agentTasks.length) {
      const cards = node("div", "board-card-list");
      tasks.forEach((task) => cards.append(boardCard(task)));
      agentTasks.forEach((task) => cards.append(agentBoardCard(task)));
      column.append(cards);
    } else {
      column.append(node("div", "board-empty", definition.empty));
    }
    column.addEventListener("dragover", (event) => {
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
      column.classList.add("is-drop-target");
    });
    column.addEventListener("dragleave", (event) => {
      if (!column.contains(event.relatedTarget)) {
        column.classList.remove("is-drop-target");
      }
    });
    column.addEventListener("drop", (event) => {
      event.preventDefault();
      column.classList.remove("is-drop-target");
      const taskSlug = event.dataTransfer?.getData("text/plain");
      if (taskSlug) moveBoardTask(taskSlug, definition.status);
    });
    board.append(column);
  });
  wrapper.append(board);
  return wrapper;
}

function setAgentAvatarControlsOpen(open, { focus = true } = {}) {
  state.agentAvatarControlsOpen = Boolean(open);
  elements.agentAvatarControls.classList.toggle(
    "is-hidden",
    !state.agentAvatarControlsOpen,
  );
  elements.agentAvatarToggle.setAttribute(
    "aria-expanded",
    state.agentAvatarControlsOpen ? "true" : "false",
  );
  const label = state.agentAvatarControlsOpen
    ? "Hide avatar replacement controls"
    : "Show avatar replacement controls";
  elements.agentAvatarToggle.setAttribute("aria-label", label);
  setHudTooltip(elements.agentAvatarToggle, label);
  if (!state.agentAvatarControlsOpen) clearAgentAvatarPreview();
  if (!focus) return;
  window.setTimeout(() => {
    if (state.agentAvatarControlsOpen) elements.agentAvatarFile.focus();
    else elements.agentAvatarToggle.focus({ preventScroll: true });
  }, 0);
}

function setAgentGoalControlsOpen(open, { focus = true } = {}) {
  state.agentGoalControlsOpen = Boolean(open);
  elements.agentGoalControls.classList.toggle(
    "is-hidden",
    !state.agentGoalControlsOpen,
  );
  const toggle = document.querySelector("#agent-goal-toggle");
  toggle?.setAttribute(
    "aria-expanded",
    state.agentGoalControlsOpen ? "true" : "false",
  );
  if (!focus) return;
  window.setTimeout(() => {
    if (state.agentGoalControlsOpen) elements.agentGoalSelect.focus();
    else toggle?.focus({ preventScroll: true });
  }, 0);
}

function openAgentProfile(agent) {
  state.profileAgentSlug = agent.slug;
  setAgentAvatarControlsOpen(false, { focus: false });
  setAgentGoalControlsOpen(false, { focus: false });
  elements.agentProfileHeading.textContent = agent.name;
  renderAgentProfileSummary(agent);
  elements.agentAvatarForm.reset();
  clearAgentAvatarPreview();
  renderCurrentAgentAvatar(agent);
  elements.agentAvatarError.classList.add("is-hidden");
  elements.agentGoalError.classList.add("is-hidden");
  renderAgentProfileGoals(agent);
  elements.agentAvatarState.textContent = agent.avatar?.kind === "attachment"
    ? "A verified avatar is currently attached. Uploading a different image requires confirmation."
    : "Initials remain in use until a verified replacement succeeds.";
  elements.agentProfileDialog.showModal();
  window.setTimeout(() => elements.agentProfileHeading.focus(), 0);
}

function appendAgentProfileInlineMarkdown(container, source) {
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)]+\))/g;
  let cursor = 0;
  for (const match of source.matchAll(pattern)) {
    if (match.index > cursor) {
      container.append(document.createTextNode(source.slice(cursor, match.index)));
    }
    const token = match[0];
    if (token.startsWith("`")) {
      container.append(node("code", "", token.slice(1, -1)));
    } else if (token.startsWith("**")) {
      container.append(node("strong", "", token.slice(2, -2)));
    } else if (token.startsWith("*")) {
      container.append(node("em", "", token.slice(1, -1)));
    } else {
      const link = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      const href = link?.[2] || "";
      if (link && /^(https?:\/\/|http:\/\/127\.0\.0\.1:)/.test(href)) {
        const anchor = node("a", "", link[1]);
        anchor.href = href;
        anchor.target = "_blank";
        anchor.rel = "noreferrer";
        container.append(anchor);
      } else {
        container.append(document.createTextNode(token));
      }
    }
    cursor = match.index + token.length;
  }
  if (cursor < source.length) {
    container.append(document.createTextNode(source.slice(cursor)));
  }
}

function renderAgentProfileSummary(agent) {
  const source = agent.summary?.trim() || "";
  if (!source) {
    elements.agentProfileSummary.replaceChildren(
      node("p", "agent-profile-empty", "No profile summary is available."),
    );
    return;
  }
  const lines = source.split(/\r?\n/);
  const content = [];
  let paragraph = [];
  let list = null;
  const flushParagraph = () => {
    if (!paragraph.length) return;
    const element = node("p");
    appendAgentProfileInlineMarkdown(element, paragraph.join(" "));
    content.push(element);
    paragraph = [];
  };
  const flushList = () => {
    if (!list) return;
    content.push(list);
    list = null;
  };
  for (const line of lines) {
    if (/^##\s+Attachments\s*$/i.test(line)) {
      flushParagraph();
      flushList();
      break;
    }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      if (
        heading[1].length === 1 &&
        heading[2].trim().toLowerCase() === `agent ${agent.name}`.toLowerCase()
      ) {
        continue;
      }
      const element = node(`h${Math.min(heading[1].length + 2, 4)}`);
      appendAgentProfileInlineMarkdown(element, heading[2].trim());
      content.push(element);
      continue;
    }
    const listItem = line.match(/^\s*(?:[-*]|\d+\.)\s+(.+)$/);
    if (listItem) {
      flushParagraph();
      if (!list) list = node(/^\s*\d+\./.test(line) ? "ol" : "ul");
      const item = node("li");
      appendAgentProfileInlineMarkdown(item, listItem[1]);
      list.append(item);
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }
    flushList();
    paragraph.push(line.trim());
  }
  flushParagraph();
  flushList();
  elements.agentProfileSummary.replaceChildren(...content);
}

function renderCurrentAgentAvatar(agent) {
  const avatar = agent.avatar || {};
  const hasAttachment =
    avatar.kind === "attachment" &&
    typeof avatar.value === "string" &&
    avatar.value.startsWith("/media/");
  elements.agentCurrentAvatarImage.classList.toggle("is-hidden", !hasAttachment);
  elements.agentCurrentAvatarInitials.classList.toggle("is-hidden", hasAttachment);
  if (hasAttachment) {
    elements.agentCurrentAvatarImage.src = `http://127.0.0.1:8788${avatar.value}`;
    elements.agentCurrentAvatarImage.alt = `Current avatar for ${agent.name}`;
    elements.agentCurrentAvatarInitials.textContent = "";
    const encodedName = avatar.value.split("/").pop() || "avatar";
    let filename = encodedName;
    try {
      filename = decodeURIComponent(encodedName);
    } catch (_error) {
      filename = encodedName;
    }
    elements.agentCurrentAvatarFilename.textContent = filename;
  } else {
    elements.agentCurrentAvatarImage.removeAttribute("src");
    elements.agentCurrentAvatarImage.alt = "";
    elements.agentCurrentAvatarInitials.textContent =
      avatar.value || agent.name.slice(0, 2).toUpperCase();
    elements.agentCurrentAvatarFilename.textContent =
      "Using initials until an image is uploaded.";
  }
}

function renderAgentProfileGoals(agent) {
  const assigned = new Set(agent.default_goal_slugs);
  const goals = state.snapshot?.goals || [];
  const list = node("div", "agent-profile-goal-list");
  const current = goals.filter((goal) => assigned.has(goal.slug));
  if (!current.length) {
    list.append(node("p", "agent-goal-empty", "No default goals assigned."));
  }
  current.forEach((goal) => {
    const row = node("div", "agent-profile-goal-row");
    row.append(node("span", "", goal.title));
    const remove = actionIcon("-", `Unassign ${goal.title}`);
    remove.classList.add("agent-goal-minus");
    remove.addEventListener("click", () => {
      if (window.confirm(`Remove ${agent.name} as the default agent for “${goal.title}”? The goal and its tasks will not change.`)) {
        saveAgentGoalAssignment(goal.slug, "remove");
      }
    });
    row.append(remove);
    list.append(row);
  });
  const add = actionIcon("+", "Add a goal");
  add.id = "agent-goal-toggle";
  add.classList.add("agent-goal-plus");
  add.setAttribute("aria-controls", "agent-goal-controls");
  add.setAttribute("aria-expanded", state.agentGoalControlsOpen ? "true" : "false");
  add.addEventListener("click", () => {
    setAgentGoalControlsOpen(!state.agentGoalControlsOpen);
  });
  list.append(add);
  elements.agentProfileGoals.replaceChildren(list);
  elements.agentGoalControls.classList.toggle(
    "is-hidden",
    !state.agentGoalControlsOpen,
  );
  elements.agentGoalSelect.replaceChildren();
  const placeholder = node("option", "", "Choose an eligible goal");
  placeholder.value = "";
  elements.agentGoalSelect.append(placeholder);
  goals
    .filter((goal) => ["planned", "active"].includes(goal.status) && !assigned.has(goal.slug))
    .forEach((goal) => {
      const option = node("option", "", goal.title);
      option.value = goal.slug;
      elements.agentGoalSelect.append(option);
    });
  elements.agentGoalAdd.disabled = elements.agentGoalSelect.options.length === 1;
}

async function saveAgentGoalAssignment(goalSlug, action) {
  const agent = state.agents.find((item) => item.slug === state.profileAgentSlug);
  if (!agent || !goalSlug) return;
  elements.agentGoalError.classList.add("is-hidden");
  elements.agentGoalAdd.disabled = true;
  try {
    const response = await fetch(`/api/agents/${encodeURIComponent(agent.slug)}/default-goals`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ goal_slug: goalSlug, action }),
    });
    const result = await response.json();
    if (!response.ok || !result.verified || !result.agent) throw new Error(result.error || "Goal assignment did not receive canonical readback.");
    await loadAgents();
    const refreshed = state.agents.find((item) => item.slug === agent.slug);
    if (!refreshed) throw new Error("The updated agent profile could not be read back.");
    renderAgentProfileGoals(refreshed);
    render();
    showToast(action === "assign" ? "Default goal assignment verified in GBrain." : "Default goal assignment cleared in GBrain.");
  } catch (error) {
    elements.agentGoalError.textContent = error.message || "Goal assignment could not be saved.";
    elements.agentGoalError.classList.remove("is-hidden");
  } finally {
    elements.agentGoalAdd.disabled = false;
  }
}

function clearAgentAvatarPreview() {
  if (state.avatarPreviewUrl) URL.revokeObjectURL(state.avatarPreviewUrl);
  state.avatarPreviewUrl = null;
  elements.agentAvatarPreview.removeAttribute("src");
  elements.agentAvatarPreview.classList.add("is-hidden");
  elements.agentAvatarFilename.textContent = "";
  elements.agentAvatarFilename.classList.add("is-hidden");
}

function previewAgentAvatar() {
  const file = elements.agentAvatarFile.files?.[0];
  elements.agentAvatarError.classList.add("is-hidden");
  if (!file) {
    clearAgentAvatarPreview();
    return;
  }
  if (!["image/png", "image/jpeg", "image/gif", "image/webp"].includes(file.type) || file.size > 5 * 1024 * 1024) {
    elements.agentAvatarError.textContent = "Choose a PNG, JPEG, GIF, or WebP image no larger than 5 MB.";
    elements.agentAvatarError.classList.remove("is-hidden");
    clearAgentAvatarPreview();
    return;
  }
  clearAgentAvatarPreview();
  state.avatarPreviewUrl = URL.createObjectURL(file);
  elements.agentAvatarPreview.src = state.avatarPreviewUrl;
  elements.agentAvatarPreview.alt = `Preview of selected avatar: ${file.name}`;
  elements.agentAvatarPreview.classList.remove("is-hidden");
  elements.agentAvatarFilename.textContent = `Selected image: ${file.name}`;
  elements.agentAvatarFilename.classList.remove("is-hidden");
}

async function submitAgentAvatar(event) {
  event.preventDefault();
  const agent = state.agents.find((item) => item.slug === state.profileAgentSlug);
  const file = elements.agentAvatarFile.files?.[0];
  if (!agent || !file) return;
  if (agent.avatar?.kind === "attachment" && !window.confirm("Replace the current avatar with this selected image? The prior attachment is preserved by Memory Stargraph.")) return;
  elements.agentAvatarSubmit.disabled = true;
  elements.agentAvatarError.classList.add("is-hidden");
  try {
    const body = new FormData(); body.append("file", file);
    const response = await fetch(`/api/agents/${encodeURIComponent(agent.slug)}/avatar`, { method: "POST", body });
    const result = await response.json();
    if (!response.ok || !result.verified || !result.agent) throw new Error(result.error || "Avatar upload did not receive verified readback.");
    state.agents = state.agents.map((item) => item.slug === agent.slug ? result.agent : item);
    await loadAgentWork();
    clearAgentAvatarPreview();
    elements.agentProfileDialog.close();
    render();
    showToast(`Avatar for ${agent.name} was stored and verified through Memory Stargraph.`);
  } catch (error) {
    elements.agentAvatarError.textContent = error.message || "Avatar upload is unavailable.";
    elements.agentAvatarError.classList.remove("is-hidden");
  } finally { elements.agentAvatarSubmit.disabled = false; }
}

function renderAgentWorkView() {
  const wrapper = node("section", "agent-work-view");
  const intro = node("div", "agent-work-intro");
  intro.append(
    node("h2", "", "Agent Directory"),
    node(
      "p",
      "",
      "Goal ownership is read from typed default_agent_for edges. Canonical work uses Planned, Active, Blocked, Completed, and Cancelled; Proposed is reserved for work awaiting Tony's approval. No proposal or task is auto-approved here.",
    ),
  );
  wrapper.append(intro);
  wrapper.append(renderCoordinatorSummary());
  if (!state.agents.length) {
    wrapper.append(
      node(
        "div",
        "section-empty",
        "No verified GBrain agent profiles are available.",
      ),
    );
    return wrapper;
  }
  const grid = node("div", "agent-profile-grid");
  state.agents.forEach((agent) => {
    const card = node("article", "agent-profile-card");
    const profile = actionIcon("⋯", `Open ${agent.name} profile`, { className: "agent-card-profile-button" });
    profile.addEventListener("click", () => openAgentProfile(agent));
    const heading = node("div", "agent-profile-heading");
    heading.append(
      ownerBadge({
        name: agent.name,
        avatar: agent.avatar,
      }),
      node(
        "span",
        "agent-work-count",
        `${state.agentTasks.filter((task) => task.owner.slug === agent.slug).length} work items`,
      ),
      profile,
    );
    const goals = agent.default_goal_slugs
      .map((slug) => state.snapshot.goals.find((goal) => goal.slug === slug))
      .filter(Boolean);
    const goalList = node("ul", "agent-goal-list");
    goals.forEach((goal) => {
      const item = node("li");
      const button = node("button", "", goal.title);
      button.type = "button";
      button.addEventListener("click", () => selectGoal(goal.slug));
      item.append(button);
      goalList.append(item);
    });
    const work = state.agentTasks.filter((task) => task.owner?.slug === agent.slug && task.status !== "proposed");
    const working = work.filter((task) => task.status === "active");
    const blocked = work.filter((task) => task.status === "blocked");
    const latest = work.slice().sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")))[0];
    const workSummary = node("div", "agent-work-summary");
    workSummary.append(node("h3", "", "Current work"));
    workSummary.append(node("strong", "", work.length ? `${working.length} working · ${blocked.length} blocked` : "No authorized work yet"));
    const openTodoCount = work.reduce(
      (count, task) => count + (Array.isArray(task.open_todos) ? task.open_todos.length : 0),
      0,
    );
    workSummary.append(node("span", "", latest ? `${todoSummary(latest)} · ${openTodoCount} open TODO${openTodoCount === 1 ? "" : "s"} · Updated ${formatDay(latest.updated_at || latest.due_day)}` : "No current task or open TODO recorded."));
    card.append(
      heading,
      node(
        "p",
        "",
        goals.length
          ? `${goals.length} default goal${goals.length === 1 ? "" : "s"}`
          : "No default goals linked",
      ),
      goalList,
      workSummary,
    );
    if (agent.chat_url) {
      const chat = node("a", "secondary-button", "Open Codex chat");
      chat.href = agent.chat_url;
      chat.target = "_blank";
      chat.rel = "noreferrer";
      card.append(chat);
    }
    grid.append(card);
  });
  wrapper.append(grid);
  return wrapper;
}

function renderCoordinatorSummary() {
  const section = node("section", "coordinator-summary");
  section.setAttribute("aria-labelledby", "coordinator-summary-title");
  section.append(
    node("h2", "", "Coordinator"),
    node("p", "", "Read-only triage across the three canonical agent work roots. The Coordinator does not execute, approve, edit, reassign, or claim work; shared GBrain state and evidence remain the only coordination channel."),
  );
  const list = node("div", "coordinator-agent-grid");
  state.agents.forEach((agent) => {
    const work = state.agentTasks.filter((task) => task.owner?.slug === agent.slug);
    const counts = {
      active: work.filter((task) => task.status === "active").length,
      proposed: work.filter((task) => task.status === "proposed").length,
      blocked: work.filter((task) => task.status === "blocked").length,
      completed: work.filter((task) => task.status === "completed").length,
    };
    const card = node("article", "coordinator-agent-card");
    card.append(ownerBadge({ name: agent.name, avatar: agent.avatar }));
    card.append(node("p", "", `${counts.active} active · ${counts.proposed} proposed · ${counts.blocked} blocked · ${counts.completed} completed`));
    const latest = work.filter((task) => task.status === "completed").sort((a,b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")))[0];
    card.append(node("small", "", latest ? `Recent verified completion: ${latest.title || latest.summary}` : "No verified completion yet."));
    list.append(card);
  });
  section.append(list);
  const blockers = state.agentTasks.filter((task) => task.status === "blocked");
  const proposals = state.agentTasks.filter((task) => task.status === "proposed");
  const issueCount = state.agentIssues.length;
  section.append(node("p", "coordinator-notice", blockers.length || proposals.length || issueCount ? `${blockers.length} blocked item${blockers.length === 1 ? "" : "s"}; ${proposals.length} proposal${proposals.length === 1 ? "" : "s"}; ${issueCount} malformed or missing-state issue${issueCount === 1 ? "" : "s"}. Details stay in Inbox Needs Attention.` : "No blocked items, proposals, or malformed agent-work issues."));
  return section;
}

function goalCard(goal) {
  const button = node("button", "goal-card");
  button.type = "button";
  button.dataset.slug = goal.slug;
  button.append(
    node("h2", "", goal.title),
    node("p", "", goal.outcome),
  );
  const progress = node("div", "goal-card-progress");
  const bar = node("span");
  bar.style.width = `${goal.progress.percent}%`;
  progress.append(bar);
  const meta = node("span", "goal-card-meta");
  meta.append(
    node("span", "", goalProgressText(goal)),
    node("span", "", `${goal.review_cadence} · ${formatDay(goal.target_day)}`),
  );
  button.append(progress, meta);
  button.addEventListener("click", () => selectGoal(goal.slug, button));
  return button;
}

function renderGoalsView() {
  const fragment = document.createDocumentFragment();
  const heading = node("div", "projects-view-heading");
  const copy = node("div");
  copy.append(
    node("h2", "", "Tony’s Goals"),
    node("p", "", "Active goals stay prominent; paused goals remain available below."),
  );
  const create = actionIcon("+", "New Goal", { primary: true });
  create.addEventListener("click", openNewGoal);
  heading.append(copy, create);
  fragment.append(heading);
  const note = node("div", "notice");
  note.textContent =
    "Goal target dates default to the final calendar day of their creation quarter when omitted.";
  fragment.append(note);
  const active = state.snapshot.goals.filter((goal) =>
    ["planned", "active"].includes(goal.status));
  const paused = state.snapshot.goals.filter((goal) => goal.status === "paused");
  const finished = state.snapshot.goals.filter((goal) =>
    ["completed", "cancelled"].includes(goal.status));
  const appendGroup = (title, goals, emptyCopy) => {
    const section = node("section", "goal-view-section");
    section.append(node("h2", "", title));
    if (!goals.length) {
      section.append(node("div", "section-empty", emptyCopy));
    } else {
      const grid = node("div", "goals-grid");
      goals.forEach((goal) => grid.append(goalCard(goal)));
      section.append(grid);
    }
    fragment.append(section);
  };
  appendGroup("Active goals", active, "No active goals. Create one when an outcome is ready.");
  if (paused.length) appendGroup("Paused goals", paused, "");
  if (finished.length) appendGroup("Completed and cancelled goals", finished, "");
  return fragment;
}

function renderProjectsView() {
  const fragment = document.createDocumentFragment();
  const heading = node("div", "projects-view-heading");
  const copy = node("div");
  copy.append(
    node("h2", "", "Durable projects"),
    node(
      "p",
      "",
      "Projects remain here even before a task is assigned.",
    ),
  );
  const create = actionIcon("+", "New Project", { primary: true });
  create.addEventListener("click", openNewProject);
  heading.append(copy, create);
  fragment.append(heading);
  if (state.projectsLoading) {
    fragment.append(node("div", "section-empty", "Reading Tony’s Projects…"));
    return fragment;
  }
  if (state.projectsError) {
    const error = node("div", "section-empty", state.projectsError);
    const retry = node("button", "secondary-button", "Try again");
    retry.type = "button";
    retry.addEventListener("click", loadProjects);
    error.append(retry);
    fragment.append(error);
    return fragment;
  }
  if (!state.projects.length) {
    fragment.append(
      node(
        "div",
        "section-empty",
        "No durable Mission Control projects yet. Create one, then assign tasks separately.",
      ),
    );
    return fragment;
  }
  const grid = node("div", "projects-grid");
  state.projects.forEach((project) => {
    const tasks = state.snapshot.tasks.filter(
      (task) => task.project === project.slug,
    );
    const card = node("article", "project-card");
    card.classList.toggle(
      "is-selected",
      state.selectedKind === "project" && state.selectedSlug === project.slug,
    );
    const open = node("button", "project-card-open");
    open.type = "button";
    open.dataset.slug = project.slug;
    open.setAttribute("aria-label", `Open Project ${project.title}`);
    open.setAttribute(
      "aria-current",
      state.selectedKind === "project" && state.selectedSlug === project.slug
        ? "true"
        : "false",
    );
    open.append(
      node("span", "project-card-status", project.status),
      node("h2", "", project.title),
      node(
        "p",
        "",
        tasks.length
          ? `${tasks.length} assigned task${tasks.length === 1 ? "" : "s"}`
          : "No tasks assigned yet",
      ),
    );
    const goalNames = (project.supporting_goal_slugs || [])
      .map((slug) => state.snapshot.goals.find((goal) => goal.slug === slug)?.title)
      .filter(Boolean);
    open.append(node("p", "", goalNames.length ? `Supports: ${goalNames.join(", ")}` : "No supporting goals selected."));
    open.addEventListener("click", () => selectProject(project.slug, open));
    card.append(open);
    grid.append(card);
  });
  fragment.append(grid);
  return fragment;
}

function renderProjectRelationList(container, entries, emptyCopy) {
  container.replaceChildren();
  if (!entries.length) {
    container.append(node("li", "is-empty", emptyCopy));
    return;
  }
  entries.forEach((entry) => container.append(node("li", "", entry)));
}

function selectProject(slug, returnFocus = undefined) {
  const project = state.projects.find((item) => item.slug === slug);
  if (!project) return;
  if (returnFocus !== undefined) {
    state.detailReturnFocus = returnFocus
      ? { element: returnFocus, slug }
      : null;
  }
  state.selectedSlug = slug;
  state.selectedKind = "project";
  prepareDetailPanelWidth("project");
  elements.detailPanel.setAttribute("aria-hidden", "false");
  elements.detailPanel.setAttribute("aria-label", "Project details");
  elements.detailEmpty.classList.add("is-hidden");
  elements.detailContent.classList.add("is-hidden");
  elements.artifactDetailContent.classList.add("is-hidden");
  elements.goalDetailContent.classList.add("is-hidden");
  elements.projectDetailContent.classList.remove("is-hidden");
  elements.systemTicketDetailContent.classList.add("is-hidden");
  elements.calendarEventDetail.classList.add("is-hidden");
  elements.projectDetailStatus.textContent = project.status;
  elements.projectDetailTitle.textContent = project.title;
  renderSafeMarkdown(elements.projectDetailSummary, project.summary);
  elements.projectDetailCreated.textContent = project.created_at
    ? new Date(project.created_at).toLocaleString()
    : "Not recorded";
  elements.projectDetailUpdated.textContent = project.updated_at
    ? new Date(project.updated_at).toLocaleString()
    : "Not recorded";
  const goals = (project.supporting_goal_slugs || []).map((goalSlug) =>
    state.snapshot.goals.find((goal) => goal.slug === goalSlug)?.title || goalSlug);
  const tasks = state.snapshot.tasks
    .filter((task) => task.project === project.slug)
    .map((task) => `${task.title || task.summary} · ${taskUiStatus(task)}`);
  renderProjectRelationList(
    elements.projectDetailGoals,
    goals,
    "No supporting Goals linked.",
  );
  renderProjectRelationList(
    elements.projectDetailTasks,
    tasks,
    "No Tasks assigned.",
  );
  elements.projectDetailGbrainLink.href =
    `http://127.0.0.1:8788/?slug=${encodeURIComponent(project.slug)}`;
  elements.projectDetailSlug.textContent = project.slug;
  render();
  window.requestAnimationFrame(() => {
    if (window.matchMedia("(max-width: 760px)").matches) {
      elements.detailPanel.scrollIntoView({ block: "start", behavior: "auto" });
    }
    elements.projectDetailTitle.focus({ preventScroll: true });
  });
}

async function loadAgents() {
  if (state.agentsLoading) return;
  state.agentsLoading = true;
  try {
    const response = await fetch("/api/agents", {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Agent profiles could not be read.");
    }
    state.agents = Array.isArray(payload.agents) ? payload.agents : [];
    state.agentsLoaded = true;
    render();
  } catch (_error) {
    state.agents = [];
  } finally {
    state.agentsLoading = false;
  }
}

async function loadAgentWork() {
  if (state.agentWorkLoading) return;
  state.agentWorkLoading = true;
  state.agentWorkError = "";
  render();
  try {
    const response = await fetch("/api/agent-work", {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Agent work could not be read.");
    }
    state.agentTasks = Array.isArray(payload.tasks) ? payload.tasks : [];
    state.agentIssues = Array.isArray(payload.issues) ? payload.issues : [];
    state.agentWorkLoaded = true;
  } catch (error) {
    state.agentWorkError = error.message || "Agent work could not be read.";
  } finally {
    state.agentWorkLoading = false;
    render();
  }
}

function scheduleSurfacePoll(surface) {
  const timerKey = surface === "tasks"
    ? "taskSurfacePollTimer"
    : surface === "proposals"
      ? "proposalSurfacePollTimer"
      : "systemTicketSurfacePollTimer";
  if (state[timerKey] !== null) return;
  state[timerKey] = window.setTimeout(() => {
    state[timerKey] = null;
    if (document.hidden) return;
    if (surface === "tasks") void loadTasks({ reason: "poll" });
    else if (surface === "proposals") void loadProposals({ poll: true });
    else void loadSystemTickets({ poll: true });
  }, 1000);
}

async function performProposalLoad({ refresh = false } = {}) {
  state.proposalsLoading = true;
  state.proposalsError = "";
  render();
  try {
    const requestOptions = {
      headers: { Accept: "application/json" },
      cache: "no-store",
    };
    const response = refresh
      ? await fetch("/api/proposals?refresh=1", requestOptions)
      : await fetch("/api/proposals", requestOptions);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Proposed tasks could not be read.");
    }
    state.proposalsReadState = payload.read_state || null;
    if (response.status === 200) {
      state.proposals = Array.isArray(payload.proposals)
        ? payload.proposals.filter(isActionableProposal)
        : [];
      state.proposalIssues = Array.isArray(payload.issues)
        ? payload.issues
        : [];
      state.proposalsLoaded = true;
    }
    if (payload.read_state?.error) {
      state.proposalsError = payload.read_state.error;
    }
    if (payload.read_state?.refreshing) scheduleSurfacePoll("proposals");
  } catch (error) {
    state.proposalsError =
      error.message || "Proposed tasks could not be read.";
  } finally {
    state.proposalsLoading = false;
    render();
  }
}

function loadProposals(options = {}) {
  if (state.proposalsLoadPromise) return state.proposalsLoadPromise;
  state.proposalsLoadPromise = performProposalLoad(options).finally(() => {
    state.proposalsLoadPromise = null;
  });
  return state.proposalsLoadPromise;
}

async function loadProjects() {
  state.projectsLoading = true;
  state.projectsError = "";
  if (state.snapshot) render();
  try {
    const response = await fetch("/api/projects", {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Projects could not be read from GBrain.");
    }
    state.projects = payload.projects;
    state.projectIssues = Array.isArray(payload.issues) ? payload.issues : [];
    state.projectWarningStateError = payload.warning_state_error || "";
    state.projectsLoaded = true;
  } catch (error) {
    state.projectsError =
      error.message || "Projects could not be read from GBrain.";
  } finally {
    state.projectsLoading = false;
    if (state.snapshot) render();
  }
}

function openNewProject() {
  state.projectEditorSlug = null;
  elements.newProjectForm.reset();
  elements.newProjectSummaryField.classList.add("is-hidden");
  elements.newProjectStatusField.classList.add("is-hidden");
  elements.newProjectStatus.value = "active";
  populateProjectGoalChoices();
  elements.newProjectError.classList.add("is-hidden");
  elements.newProjectMode.textContent = "New durable project";
  elements.newProjectHeading.textContent = "Create a Project";
  elements.newProjectSubmit.textContent = "Create project";
  elements.newProjectClose.setAttribute("aria-label", "Close New Project");
  elements.newProjectDialog.showModal();
  window.setTimeout(() => elements.newProjectTitle.focus(), 0);
}

function populateProjectGoalChoices(selected = []) {
  const chosen = new Set(selected);
  elements.newProjectGoals.replaceChildren();
  (state.snapshot?.goals || []).forEach((goal) => {
    const option = node("option", "", goal.title);
    option.value = goal.slug;
    option.selected = chosen.has(goal.slug);
    elements.newProjectGoals.append(option);
  });
}

function openEditProject(project) {
  state.projectEditorSlug = project.slug;
  elements.newProjectTitle.value = project.title;
  elements.newProjectSummary.value = project.summary;
  elements.newProjectStatus.value = project.status;
  elements.newProjectSummaryField.classList.remove("is-hidden");
  elements.newProjectStatusField.classList.remove("is-hidden");
  populateProjectGoalChoices(project.supporting_goal_slugs || []);
  elements.newProjectMode.textContent = "Existing durable project";
  elements.newProjectHeading.textContent = "Edit";
  elements.newProjectSubmit.textContent = "Save changes";
  elements.newProjectClose.setAttribute("aria-label", "Close Project editor");
  elements.newProjectError.classList.add("is-hidden");
  elements.newProjectDialog.showModal();
  window.setTimeout(() => elements.newProjectTitle.focus(), 0);
}

async function submitNewProject(event) {
  event.preventDefault();
  elements.newProjectError.classList.add("is-hidden");
  elements.newProjectSubmit.disabled = true;
  const editing = Boolean(state.projectEditorSlug);
  elements.newProjectSubmit.textContent = editing ? "Saving in GBrain…" : "Creating in GBrain…";
  const projectStatus = editing ? "Saving Project changes in GBrain…" : "Creating Project in GBrain…";
  showMutationStatus(projectStatus, "pending", { persistent: true });
  try {
    const projectPayload = {
      title: elements.newProjectTitle.value,
      supporting_goal_slugs: [...elements.newProjectGoals.selectedOptions].map(
        (option) => option.value,
      ),
    };
    if (editing) {
      Object.assign(projectPayload, {
        summary: elements.newProjectSummary.value,
        status: elements.newProjectStatus.value,
      });
    }
    const response = await fetch(editing ? `/api/projects/${encodeURIComponent(state.projectEditorSlug)}` : "/api/projects", {
      method: editing ? "PATCH" : "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(projectPayload),
    });
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(result.error || "Project could not be created.");
      error.code = result.code;
      error.slug = result.slug;
      throw error;
    }
    if (!result.receipt?.verified || !result.project) {
      throw new Error("GBrain project readback was not verified.");
    }
    await loadProjects();
    const created = state.projects.some(
      (project) => project.slug === result.project.slug,
    );
    if (!created) {
      throw new Error(
        "GBrain accepted the project, but it was not present after collection refresh.",
      );
    }
    elements.newProjectDialog.close();
    state.activeView = "projects";
    if (editing) selectProject(result.project.slug);
    showMutationStatus(
      editing ? "Project changes verified in GBrain." : "Project created, linked, and verified in GBrain.",
      "success",
    );
    if (!editing) render();
  } catch (error) {
    elements.newProjectError.textContent =
      error.code === "partial_write" && error.slug
        ? `${error.message} Inspect ${error.slug}; do not retry yet.`
        : error.message;
    elements.newProjectError.classList.remove("is-hidden");
    showMutationStatus(elements.newProjectError.textContent, "error");
  } finally {
    elements.newProjectSubmit.disabled = false;
    elements.newProjectSubmit.textContent = editing ? "Save changes" : "Create project";
  }
}

function openNewGoal() {
  state.goalEditorSlug = null;
  elements.newGoalForm.reset();
  elements.newGoalHeading.textContent = "Create a goal";
  elements.goalEditorMode.textContent = "New canonical goal";
  elements.newGoalCadence.value = "weekly";
  elements.newGoalError.classList.add("is-hidden");
  elements.newGoalDialog.showModal();
  window.setTimeout(() => elements.newGoalTitle.focus(), 0);
}

function openEditGoal() {
  const goal = state.snapshot?.goals.find((item) => item.slug === state.selectedSlug);
  if (!goal) return;
  state.goalEditorSlug = goal.slug;
  elements.newGoalTitle.value = goal.title;
  elements.newGoalOutcome.value = goal.outcome;
  elements.newGoalSuccess.value = goal.success_criteria;
  elements.newGoalStrategy.value = goal.strategy;
  elements.newGoalConstraints.value = goal.constraints;
  elements.newGoalCadence.value = goal.review_cadence;
  elements.newGoalTarget.value = goal.target_day;
  elements.newGoalHeading.textContent = "Edit goal";
  elements.goalEditorMode.textContent = "Canonical GBrain goal";
  elements.newGoalSubmit.textContent = "Save changes";
  elements.newGoalError.classList.add("is-hidden");
  elements.newGoalDialog.showModal();
  window.setTimeout(() => elements.newGoalTitle.focus(), 0);
}

async function submitNewGoal(event) {
  event.preventDefault();
  elements.newGoalError.classList.add("is-hidden");
  elements.newGoalSubmit.disabled = true;
  const editing = Boolean(state.goalEditorSlug);
  elements.newGoalSubmit.textContent = editing ? "Saving in GBrain…" : "Creating in GBrain…";
  const payload = {
    title: elements.newGoalTitle.value,
    outcome: elements.newGoalOutcome.value,
    success_criteria: elements.newGoalSuccess.value,
    strategy: elements.newGoalStrategy.value,
    review_cadence: elements.newGoalCadence.value,
    constraints: elements.newGoalConstraints.value,
  };
  if (elements.newGoalTarget.value) {
    payload.target_day = elements.newGoalTarget.value;
  }
  if (editing && !payload.target_day) {
    elements.newGoalError.textContent = "A target date is required when editing a goal.";
    elements.newGoalError.classList.remove("is-hidden");
    elements.newGoalSubmit.disabled = false;
    elements.newGoalSubmit.textContent = "Save changes";
    return;
  }
  const goalStatus = editing ? "Saving Goal changes in GBrain…" : "Creating Goal in GBrain…";
  showMutationStatus(goalStatus, "pending", { persistent: true });
  try {
    const response = await fetch(editing ? `/api/goals/${encodeURIComponent(state.goalEditorSlug)}` : "/api/goals", {
      method: editing ? "PATCH" : "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(result.error || "Goal could not be created.");
      error.code = result.code;
      error.slug = result.slug;
      throw error;
    }
    await loadTasks();
    const saved = state.snapshot.goals.find((goal) => goal.slug === result.goal.slug);
    if (!saved || saved.title !== result.goal.title) {
      throw new Error(
        "GBrain accepted the goal, but it was not present after Tony’s Goals refresh.",
      );
    }
    elements.newGoalDialog.close();
    state.activeView = "goals";
    showMutationStatus(
      editing ? "Goal changes verified in GBrain." : "Goal created, linked, and verified in GBrain.",
      "success",
    );
    selectGoal(result.goal.slug);
  } catch (error) {
    elements.newGoalError.textContent =
      error.code === "partial_write" && error.slug
        ? `${error.message} Inspect ${error.slug}; do not retry yet.`
        : error.message;
    elements.newGoalError.classList.remove("is-hidden");
    showMutationStatus(elements.newGoalError.textContent, "error");
  } finally {
    elements.newGoalSubmit.disabled = false;
    elements.newGoalSubmit.textContent = editing ? "Save changes" : "Create goal";
  }
}

function openGoalConfirmation(action) {
  if (state.selectedKind !== "goal" || !state.selectedSlug) return;
  const goal = state.snapshot.goals.find(
    (candidate) => candidate.slug === state.selectedSlug,
  );
  if (!goal) return;
  state.goalAction = { action, goalSlug: goal.slug };
  elements.goalConfirmError.classList.add("is-hidden");
  if (action === "pause") {
    elements.goalConfirmTitle.textContent = `Pause “${goal.title}”?`;
    elements.goalConfirmCopy.textContent =
      "The goal and every linked task relationship will be retained. Its status becomes Paused, and it leaves active-goal Home, selection, and progress workflows until resumed.";
    elements.goalConfirmSubmit.textContent = "Pause goal";
    elements.goalConfirmSubmit.classList.remove("is-destructive");
  } else {
    elements.goalConfirmTitle.textContent = `Delete “${goal.title}”?`;
    elements.goalConfirmCopy.textContent =
      "Mission Control will remove only this goal’s paired advances_goal and advanced_by links, without deleting or changing the status/content of linked tasks. The goal page is then soft-deleted and recoverable in GBrain for 72 hours.";
    elements.goalConfirmSubmit.textContent = "Delete goal";
    elements.goalConfirmSubmit.classList.add("is-destructive");
  }
  elements.goalConfirmDialog.showModal();
  window.setTimeout(() => elements.goalConfirmSubmit.focus(), 0);
}

function closeGoalConfirmation() {
  elements.goalConfirmDialog.close();
  state.goalAction = null;
}

async function confirmGoalAction() {
  const pending = state.goalAction;
  if (!pending) return;
  elements.goalConfirmError.classList.add("is-hidden");
  elements.goalConfirmSubmit.disabled = true;
  const originalLabel = elements.goalConfirmSubmit.textContent;
  elements.goalConfirmSubmit.textContent =
    pending.action === "pause" ? "Pausing…" : "Deleting…";
  try {
    const response = await fetch(
      pending.action === "pause"
        ? `/api/goals/${encodeURIComponent(pending.goalSlug)}/status`
        : `/api/goals/${encodeURIComponent(pending.goalSlug)}`,
      {
        method: pending.action === "pause" ? "PATCH" : "DELETE",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        ...(pending.action === "pause"
          ? { body: JSON.stringify({ status: "paused" }) }
          : {}),
      },
    );
    const result = await response.json();
    if (!response.ok || !result.receipt?.verified) {
      const error = new Error(
        result.error || "GBrain did not verify the goal change.",
      );
      error.code = result.code;
      error.slug = result.slug;
      throw error;
    }
    if (pending.action === "pause") {
      const index = state.snapshot.goals.findIndex(
        (goal) => goal.slug === pending.goalSlug,
      );
      if (index < 0 || !result.receipt.goal) {
        throw new Error("Verified paused goal readback was missing.");
      }
      state.snapshot.goals[index] = {
        ...state.snapshot.goals[index],
        ...result.receipt.goal,
      };
      elements.goalConfirmDialog.close();
      state.goalAction = null;
      render();
      selectGoal(pending.goalSlug);
      showToast("Goal paused and verified in GBrain.");
    } else {
      state.snapshot.goals = state.snapshot.goals.filter(
        (goal) => goal.slug !== pending.goalSlug,
      );
      state.snapshot.tasks = state.snapshot.tasks.map((task) =>
        task.goal === pending.goalSlug ? { ...task, goal: null } : task);
      rebuildDerivedTaskViews();
      elements.goalConfirmDialog.close();
      state.goalAction = null;
      closeDetails();
      showToast("Goal soft-deleted and linked tasks preserved.");
    }
  } catch (error) {
    elements.goalConfirmError.textContent =
      error.code === "partial_write" && error.slug
        ? `${error.message} Inspect ${error.slug}; do not retry yet.`
        : error.message;
    elements.goalConfirmError.classList.remove("is-hidden");
  } finally {
    elements.goalConfirmSubmit.disabled = false;
    elements.goalConfirmSubmit.textContent = originalLabel;
  }
}

async function saveTaskProject() {
  if (state.selectedKind !== "task" || !state.selectedSlug) return;
  const taskSlug = state.selectedSlug;
  const projectSlug = elements.taskProjectSelect.value;
  elements.taskProjectError.classList.add("is-hidden");
  elements.taskProjectSave.disabled = true;
  elements.taskProjectSave.textContent = "Saving…";
  try {
    const response = await fetch(
      `/api/tasks/${encodeURIComponent(taskSlug)}/project`,
      {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({ project_slug: projectSlug }),
      },
    );
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(result.error || "Project assignment failed.");
      error.code = result.code;
      error.slug = result.slug;
      throw error;
    }
    showToast(
      projectSlug
        ? "Task project assignment verified in GBrain."
        : "Task removed from its project in GBrain.",
    );
    await loadTasks();
    selectTask(taskSlug);
  } catch (error) {
    elements.taskProjectError.textContent =
      error.code === "partial_write" && error.slug
        ? `${error.message} Inspect ${error.slug} before retrying.`
        : error.message;
    elements.taskProjectError.classList.remove("is-hidden");
  } finally {
    elements.taskProjectSave.textContent = "Save";
    elements.taskProjectSave.disabled =
      elements.taskProjectSelect.value ===
      elements.taskProjectSelect.dataset.currentProject;
  }
}

async function repairActiveMembership(taskSlug, button) {
  button.disabled = true;
  button.textContent = "Repairing…";
  try {
    const response = await fetch(
      `/api/tasks/${encodeURIComponent(taskSlug)}/relationships/active-membership`,
      {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({}),
      },
    );
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(result.error || "Membership repair failed.");
      error.code = result.code;
      error.slug = result.slug;
      throw error;
    }
    showToast("Active Tony’s Tasks membership repaired and verified in GBrain.");
    await loadTasks();
  } catch (error) {
    button.disabled = false;
    button.textContent = "Repair membership";
    const item = button.closest(".attention-item");
    const message = item?.querySelector(".attention-repair-error");
    if (message) {
      message.textContent =
        error.code === "partial_write" && error.slug
          ? `${error.message} Inspect ${error.slug} before retrying.`
          : error.message;
      message.classList.remove("is-hidden");
    }
  }
}

function renderNeedsAttention() {
  const taskIssues = (state.snapshot?.issues || []).map((issue) => ({
    ...issue,
    source: "task",
  }));
  const projectIssues = state.projectIssues.map((issue) => ({
    ...issue,
    source: "project",
  }));
  const agentIssues = state.agentIssues.map((issue) => ({
    ...issue,
    source: "agent",
  }));
  const proposalIssues = state.proposalIssues.map((issue) => ({
    ...issue,
    source: "proposal",
  }));
  const seen = new Set();
  const issues = [
    ...taskIssues,
    ...projectIssues,
    ...agentIssues,
    ...proposalIssues,
  ].filter((issue) => {
    const identity =
      issue.fingerprint ||
      `${issue.source}:${issue.slug}:${issue.message}:${issue.impact || ""}`;
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
  const activeIssues = issues.filter((issue) => !issue.dismissed);
  const dismissedIssues = issues.filter((issue) => issue.dismissed);
  const stateError =
    state.snapshot?.warning_state_error || state.projectWarningStateError;
  if (!issues.length && !stateError) return null;
  const details = node("details", "needs-attention");
  details.open =
    activeIssues.length > 0 ||
    Boolean(stateError) ||
    state.showDismissedWarnings;
  const summary = node("summary");
  summary.append(
    node(
      "span",
      "",
      dismissedIssues.length
        ? `Needs Attention · ${dismissedIssues.length} dismissed`
        : "Needs Attention",
    ),
    node("strong", "", String(activeIssues.length)),
  );
  details.append(
    summary,
    node(
      "p",
      "attention-intro",
      "Warnings live only in Inbox. Dismissing one changes only this warning display; it never hides or changes the task or project.",
    ),
  );
  if (stateError) {
    details.append(
      node(
        "p",
        "attention-state-error",
        `Warning preferences are temporarily unavailable: ${stateError}`,
      ),
    );
  }
  const list = node("div", "attention-list");
  const appendIssue = (issue, { dismissed = false } = {}) => {
    const item = node(
      "article",
      dismissed ? "attention-item is-dismissed" : "attention-item",
    );
    const task = state.snapshot.tasks.find(
      (candidate) => candidate.slug === issue.slug,
    );
    const project = state.projects.find(
      (candidate) => candidate.slug === issue.slug,
    );
    const heading = node("div", "attention-item-heading");
    heading.append(
      node(
        "strong",
        "",
        task?.title || task?.summary || project?.title || issue.slug,
      ),
      node(
        "span",
        `attention-severity ${issue.severity || "error"}`,
        dismissed ? "Dismissed" : issue.task_visible === false ? "Not shown" : "Shown",
      ),
    );
    item.append(
      heading,
      node("p", "attention-reason", issue.message),
      node("p", "attention-impact", issue.impact),
    );
    const actions = node("div", "attention-actions");
    if (issue.repair_action === "repair_active_membership") {
      const repair = node("button", "secondary-button", "Repair membership");
      repair.type = "button";
      repair.addEventListener(
        "click",
        () => repairActiveMembership(issue.slug, repair),
      );
      actions.append(repair);
    }
    if (task) {
      const open = node("button", "secondary-button", "Open task");
      open.type = "button";
      open.addEventListener("click", () => selectTask(task.slug));
      actions.append(open);
    } else {
      const inspect = node("a", "secondary-button", "Inspect in GBrain");
      inspect.href =
        `http://127.0.0.1:8788/?slug=${encodeURIComponent(issue.slug)}`;
      inspect.target = "_blank";
      inspect.rel = "noreferrer";
      actions.append(inspect);
    }
    if (dismissed) {
      const restore = node("button", "secondary-button", "Restore warning");
      restore.type = "button";
      restore.disabled = !issue.fingerprint || Boolean(stateError);
      restore.addEventListener("click", () => restoreWarning(issue, restore));
      actions.append(restore);
    } else {
      const dismiss = node("button", "secondary-button", "Dismiss");
      dismiss.type = "button";
      dismiss.disabled = !issue.fingerprint || Boolean(stateError);
      dismiss.addEventListener("click", () => openWarningDismiss(issue));
      actions.append(dismiss);
    }
    item.append(actions);
    item.append(node("p", "attention-repair-error is-hidden"));
    list.append(item);
  };
  activeIssues.forEach((issue) => appendIssue(issue));
  if (!activeIssues.length) {
    list.append(
      node(
        "p",
        "attention-empty",
        dismissedIssues.length
          ? "No active warnings. Dismissed warnings stay recoverable below."
          : "No warnings need attention.",
      ),
    );
  }
  if (dismissedIssues.length) {
    const toggle = node(
      "button",
      "dismissed-warning-toggle",
      state.showDismissedWarnings
        ? "Hide dismissed warnings"
        : `Show dismissed warnings (${dismissedIssues.length})`,
    );
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", String(state.showDismissedWarnings));
    toggle.addEventListener("click", () => {
      state.showDismissedWarnings = !state.showDismissedWarnings;
      render();
    });
    list.append(toggle);
    if (state.showDismissedWarnings) {
      dismissedIssues.forEach((issue) =>
        appendIssue(issue, { dismissed: true }),
      );
    }
  }
  details.append(list);
  return details;
}

function updateWarningDismissal(fingerprint, dismissed) {
  const update = (issues) =>
    (issues || []).map((issue) =>
      issue.fingerprint === fingerprint ? { ...issue, dismissed } : issue,
    );
  if (state.snapshot) state.snapshot.issues = update(state.snapshot.issues);
  state.projectIssues = update(state.projectIssues);
}

function openWarningDismiss(issue) {
  state.pendingWarning = issue;
  elements.warningDismissCopy.textContent =
    `Dismiss “${issue.message}” for ${issue.slug}? It will stay dismissed while this exact issue is unchanged.`;
  elements.warningDismissError.classList.add("is-hidden");
  elements.warningDismissDialog.showModal();
  window.setTimeout(() => elements.warningDismissConfirm.focus(), 0);
}

function closeWarningDismiss() {
  elements.warningDismissDialog.close();
}

async function confirmWarningDismiss() {
  const issue = state.pendingWarning;
  if (!issue?.fingerprint) return;
  elements.warningDismissError.classList.add("is-hidden");
  elements.warningDismissConfirm.disabled = true;
  elements.warningDismissConfirm.textContent = "Dismissing…";
  try {
    const response = await fetch("/api/warnings/dismiss", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({ fingerprint: issue.fingerprint }),
    });
    const result = await response.json();
    if (!response.ok || !result.verified || !result.dismissed) {
      throw new Error(result.error || "Warning dismissal could not be verified.");
    }
    updateWarningDismissal(issue.fingerprint, true);
    elements.warningDismissDialog.close();
    render();
    showToast("Warning dismissed. The task or project was not changed.");
  } catch (error) {
    elements.warningDismissError.textContent =
      error.message || "Warning dismissal failed.";
    elements.warningDismissError.classList.remove("is-hidden");
  } finally {
    elements.warningDismissConfirm.disabled = false;
    elements.warningDismissConfirm.textContent = "Dismiss warning";
  }
}

async function restoreWarning(issue, button) {
  const item = button.closest(".attention-item");
  const message = item?.querySelector(".attention-repair-error");
  button.disabled = true;
  button.textContent = "Restoring…";
  if (message) message.classList.add("is-hidden");
  try {
    const response = await fetch("/api/warnings/restore", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({ fingerprint: issue.fingerprint }),
    });
    const result = await response.json();
    if (!response.ok || !result.verified || result.dismissed) {
      throw new Error(result.error || "Warning restore could not be verified.");
    }
    updateWarningDismissal(issue.fingerprint, false);
    render();
    showToast("Warning restored in Inbox.");
  } catch (error) {
    button.disabled = false;
    button.textContent = "Restore warning";
    if (message) {
      message.textContent = error.message || "Warning restore failed.";
      message.classList.remove("is-hidden");
    }
  }
}

function proposalAgent(proposal) {
  return state.agents.find(
    (agent) => agent.slug === proposal.proposing_agent,
  );
}

function proposalRelationLabel(proposal) {
  if (proposal.linked_goal) {
    const goal = state.snapshot?.goals.find(
      (candidate) => candidate.slug === proposal.linked_goal,
    );
    return goal ? `Goal · ${goal.title}` : `Goal · ${proposal.linked_goal}`;
  }
  const task = state.snapshot?.tasks.find(
    (candidate) => candidate.slug === proposal.linked_task,
  );
  return task
    ? `Tony task · ${task.title || task.summary}`
    : `Tony task · ${proposal.linked_task}`;
}

function proposalStateLabel(proposal) {
  if (proposal.status === "approved" || proposal.decision === "approve" || proposal.proposal_decision === "approve") {
    return "Approved · Planned";
  }
  if (proposal.status === "rejected" || proposal.decision === "reject" || proposal.proposal_decision === "reject") {
    return "Rejected · Cancelled";
  }
  return "Proposed";
}

function isActionableProposal(proposal) {
  if (!proposal || proposal.status !== "proposed") return false;
  const decision = proposal.proposal_decision ?? proposal.decision;
  return decision === undefined || decision === null || decision === "" || decision === "pending";
}

function openProposalReview(proposal) {
  state.proposalAction = { proposal, action: "review" };
  elements.proposalReviewName.value = proposal.title;
  elements.proposalReviewRationale.value = proposal.rationale;
  elements.proposalReviewNextStep.value = proposal.proposed_next_step;
  elements.proposalReviewDue.value = proposal.due_day;
  elements.proposalReviewError.classList.add("is-hidden");
  elements.proposalReviewDialog.showModal();
  window.setTimeout(() => elements.proposalReviewName.focus(), 0);
}

function openProposalDecision(proposal, action) {
  const agent = proposalAgent(proposal);
  const recipient = `this exact canonical ${proposal.recipient === "tony" ? "Tony" : "agent-owned"} task`;
  state.proposalAction = { proposal, action };
  elements.proposalDecisionTitle.textContent =
    action === "approve"
      ? `Approve “${proposal.title}”?`
      : `Reject “${proposal.title}”?`;
  elements.proposalDecisionCopy.textContent =
    action === "approve"
      ? `Approval changes ${recipient} in place to planned only after exact GBrain page and relationship readback. It does not authorize unrelated external side effects.`
      : "Rejection records a durable decision and retains this canonical proposal, its links, and its audit history. It does not delete task, goal, or agent data.";
  elements.proposalDecisionNote.value = "";
  elements.proposalDecisionError.classList.add("is-hidden");
  elements.proposalDecisionRepair.classList.add("is-hidden");
  elements.proposalDecisionRepair.removeAttribute("href");
  elements.proposalDecisionSubmit.textContent =
    action === "approve" ? "Approve this task" : "Reject proposal";
  elements.proposalDecisionSubmit.classList.toggle(
    "is-destructive",
    action === "reject",
  );
  elements.proposalDecisionDialog.showModal();
  window.setTimeout(() => elements.proposalDecisionSubmit.focus(), 0);
}

function proposalCard(proposal) {
  const agent = proposalAgent(proposal);
  const card = node("article", "proposal-card");
  card.dataset.slug = proposal.slug;
  card.classList.toggle("is-selected", state.selectedSlug === proposal.slug);
  card.tabIndex = 0;
  card.setAttribute("role", "button");
  card.setAttribute("aria-label", `Open proposed task ${proposal.title}`);
  const open = () => selectTask(proposal.slug, null, card);
  card.addEventListener("click", open);
  card.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); }
  });
  card.append(
    node("h4", "", proposal.title),
    node("span", "proposal-status", proposalStateLabel(proposal)),
  );
  if (proposal.source_kind === "task" && proposal.status === "proposed") {
    const actions = node("div", "proposal-actions");
    const edit = actionIcon("✎", `Edit ${proposal.title}`);
    edit.addEventListener("click", (event) => { event.stopPropagation(); selectTask(proposal.slug); openEditTask(); });
    const approve = actionIcon("✓", `Approve ${proposal.title}`, { primary: true });
    approve.addEventListener("click", (event) => { event.stopPropagation(); openProposalDecision(proposal, "approve"); });
    const reject = actionIcon("❌", `Reject ${proposal.title}`, { className: "proposal-reject-button" });
    reject.addEventListener("click", (event) => { event.stopPropagation(); openProposalDecision(proposal, "reject"); });
    actions.append(edit, approve, reject); card.append(actions);
  }
  return card;
}

function renderProposedWork() {
  const section = node("section", "proposed-work");
  const heading = node("div", "proposed-work-heading");
  const title = node("div");
  title.append(
    node("h2", "", "Proposed Tasks"),
    node(
      "p",
      "",
      "Grouped by proposing agent. Every proposal stays unapproved until Tony explicitly decides.",
    ),
  );
  const filterLabel = node("label", "proposal-agent-filter");
  filterLabel.append(node("span", "", "Agent"));
  const filter = node("select");
  filter.setAttribute("aria-label", "Filter proposed tasks by agent");
  [
    ["all", "All Agents"],
    ["agents/toddy", "Toddy"],
    ["agents/timmy", "Timmy"],
    ["agents/tammy", "Tammy"],
  ].forEach(([value, label]) => {
    const option = node("option", "", label);
    option.value = value;
    filter.append(option);
  });
  filter.value = state.proposalAgentFilter;
  filter.addEventListener("change", () => {
    state.proposalAgentFilter = filter.value;
    render();
  });
  filterLabel.append(filter);
  heading.append(title, filterLabel);
  section.append(heading);
  if (
    !state.proposalsLoaded &&
    (state.proposalsLoading || state.proposalsReadState?.status === "loading")
  ) {
    section.append(node("div", "section-empty", "Reading proposed work…"));
    return section;
  }
  if (state.proposalsError && !state.proposalsLoaded) {
    const error = node("div", "section-empty", state.proposalsError);
    const retry = node("button", "secondary-button", "Try again");
    retry.type = "button";
    retry.addEventListener("click", () => loadProposals({ refresh: true }));
    error.append(retry);
    section.append(error);
    return section;
  }
  if (state.proposalsLoaded && (state.proposalsLoading || state.proposalsReadState?.stale)) {
    const status = node(
      "p",
      "surface-read-state",
      state.proposalsError
        ? `Last verified proposals remain visible. ${state.proposalsError}`
        : "Last verified proposals remain visible while GBrain refreshes.",
    );
    section.append(status);
  }
  const visible = state.proposals.filter(isActionableProposal).filter(
    (proposal) =>
      state.proposalAgentFilter === "all" ||
      proposal.proposing_agent === state.proposalAgentFilter,
  );
  if (!visible.length) {
    section.append(
      node(
        "div",
        "section-empty",
        state.proposals.length
          ? "No proposals from this agent."
          : "No agent has submitted proposed work. Nothing is auto-generated or approved.",
      ),
    );
    return section;
  }
  const agentOrder = [
    "agents/toddy",
    "agents/timmy",
    "agents/tammy",
  ];
  const appendGroups = (items, headingText) => {
    if (!items.length) return;
    section.append(node("h3", "", headingText));
    agentOrder.forEach((agentSlug) => {
      const group = items.filter(
        (proposal) => proposal.proposing_agent === agentSlug,
      );
      if (!group.length) return;
      const agent = state.agents.find((item) => item.slug === agentSlug);
      const wrapper = node("section", "proposal-agent-group");
      wrapper.append(
        node("h3", "", agent?.name || agentSlug),
        ...group.map(proposalCard),
      );
      section.append(wrapper);
    });
  };
  appendGroups(visible, "Pending review");
  return section;
}

async function submitProposalReview(event) {
  event.preventDefault();
  const pending = state.proposalAction;
  if (!pending || pending.action !== "review") return;
  elements.proposalReviewError.classList.add("is-hidden");
  elements.proposalReviewSubmit.disabled = true;
  elements.proposalReviewSubmit.textContent = "Verifying in GBrain…";
  try {
    const response = await fetch(
      `/api/proposals/${encodeURIComponent(pending.proposal.slug)}/review`,
      {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({
          title: elements.proposalReviewName.value,
          rationale: elements.proposalReviewRationale.value,
          proposed_next_step: elements.proposalReviewNextStep.value,
          due_day: elements.proposalReviewDue.value,
        }),
      },
    );
    const result = await response.json();
    if (!response.ok || !result.receipt?.verified) {
      const error = new Error(result.error || "Proposal review was not verified.");
      error.code = result.code;
      error.slug = result.slug;
      throw error;
    }
    state.proposals = state.proposals.map((proposal) =>
      proposal.slug === result.receipt.proposal.slug
        ? result.receipt.proposal
        : proposal).filter(isActionableProposal);
    elements.proposalReviewDialog.close();
    state.proposalAction = null;
    render();
    showToast("Proposal review saved and verified. It remains unapproved.");
  } catch (error) {
    elements.proposalReviewError.textContent =
      error.code === "partial_write" && error.slug
        ? `${error.message} Inspect ${error.slug}; do not retry yet.`
        : error.message;
    elements.proposalReviewError.classList.remove("is-hidden");
  } finally {
    elements.proposalReviewSubmit.disabled = false;
    elements.proposalReviewSubmit.textContent = "Save reviewed proposal";
  }
}

async function submitProposalDecision() {
  const pending = state.proposalAction;
  if (!pending || !["approve", "reject"].includes(pending.action)) return;
  elements.proposalDecisionError.classList.add("is-hidden");
  elements.proposalDecisionSubmit.disabled = true;
  const original = elements.proposalDecisionSubmit.textContent;
  elements.proposalDecisionSubmit.textContent = "Verifying in GBrain…";
  try {
    const response = await fetch(
      `/api/proposals/${encodeURIComponent(pending.proposal.slug)}/decision`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({
          action: pending.action,
          decision_note: elements.proposalDecisionNote.value,
        }),
      },
    );
    const result = await response.json();
    if (!response.ok || !result.receipt?.verified) {
      const error = new Error(result.error || "Proposal decision was not verified.");
      error.code = result.code;
      error.slug = result.slug;
      error.repairUrl = result.repair_url;
      throw error;
    }
    state.proposals = state.proposals.map((proposal) =>
      proposal.slug === result.receipt.proposal.slug
        ? result.receipt.proposal
        : proposal).filter(isActionableProposal);
    elements.proposalDecisionDialog.close();
    state.proposalAction = null;
    if (state.selectedSlug === pending.proposal.slug) closeDetails();
    if (pending.action === "approve") {
      await Promise.all([loadTasks(), loadAgentWork()]);
    } else {
      render();
    }
    showToast(
      pending.action === "approve"
        ? "Proposal approved in place; canonical task readback verified."
        : "Proposal rejection recorded; canonical data retained.",
    );
  } catch (error) {
    elements.proposalDecisionError.textContent =
      error.code === "partial_write" && error.slug
        ? `${error.message} Inspect ${error.slug}; do not retry yet.`
        : error.message;
    if (error.code === "lifecycle_membership_needs_attention" && error.repairUrl) {
      elements.proposalDecisionRepair.href = error.repairUrl;
      elements.proposalDecisionRepair.classList.remove("is-hidden");
    }
    elements.proposalDecisionError.classList.remove("is-hidden");
  } finally {
    elements.proposalDecisionSubmit.disabled = false;
    elements.proposalDecisionSubmit.textContent = original;
  }
}

function renderTaskSurfaceLoading(view) {
  const wrapper = node("section", "section-empty task-surface-loading");
  const message = state.tasksReadState?.error
    ? state.tasksReadState.error
    : "Reading canonical task data in the background…";
  wrapper.append(node("h2", "", viewMeta[view]?.title || "Tasks"), node("p", "", message));
  if (state.tasksReadState?.error) {
    const retry = node("button", "secondary-button", "Try again");
    retry.type = "button";
    retry.addEventListener("click", () => loadTasks({ reason: "manual" }));
    wrapper.append(retry);
  }
  return wrapper;
}

function render() {
  const focusedTooltipTarget = document.activeElement?.closest?.(".has-tooltip") || null;
  const focusedSystemTicketSlug = document.activeElement?.closest?.(".system-ticket-card")?.dataset.slug || null;
  hideHudTooltip();
  window.requestAnimationFrame(() => {
    if (focusedSystemTicketSlug && document.activeElement === document.body) {
      document.querySelector(
        `.system-ticket-card[data-slug="${CSS.escape(focusedSystemTicketSlug)}"]`,
      )?.focus({ preventScroll: true });
    }
    if (
      focusedTooltipTarget?.isConnected &&
      document.activeElement === focusedTooltipTarget
    ) {
      showHudTooltip(focusedTooltipTarget);
    }
  });
  renderNavigation();
  updateBoardStatus();
  elements.viewTitle.textContent = viewMeta[state.activeView].title;
  elements.viewCount.textContent = inContextCountLabel(state.activeView);
  elements.boardAgentFilter.classList.toggle(
    "is-hidden",
    state.activeView !== "board",
  );
  elements.artifactAgentFilter.classList.toggle(
    "is-hidden",
    state.activeView !== "artifacts",
  );
  elements.artifactAgentSelect.value = state.artifactAgentFilter;
  // The filter is a persisted, client-only preference. Rendering can happen
  // during navigation and while agent work is loading, so keep the control in
  // lockstep with state rather than relying on the previous DOM value.
  elements.showAgentTasks.checked = state.showAgentTasks;
  const view = state.activeView;
  const content = view === "artifacts"
    ? renderArtifactsView()
    : !state.snapshot
    ? view === "system-tickets"
      ? renderSystemTicketsView()
      : renderTaskSurfaceLoading(view)
    : view === "today"
      ? renderToday()
      : view === "all"
        ? renderAllTasksView()
      : view === "week"
        ? renderCalendarView()
      : view === "board"
        ? renderBoard()
      : view === "agent-work"
        ? renderAgentWorkView()
      : view === "system-tickets"
        ? renderSystemTicketsView()
      : view === "projects"
        ? renderProjectsView()
      : view === "goals"
        ? renderGoalsView()
        : renderListView(view);
  const attention = view === "inbox" ? renderNeedsAttention() : null;
  const proposals = view === "inbox" ? renderProposedWork() : null;
  elements.viewSurface.replaceChildren(
    ...[
      ...(attention ? [attention] : []),
      ...(proposals ? [proposals] : []),
      content,
    ],
  );
  if (view === "artifacts") {
    elements.dateLabel.textContent = "Canonical GBrain deliverables";
  } else if (state.snapshot) {
    const date = parseDay(state.activeView === "week" ? currentWeekStart() : state.snapshot.as_of);
    elements.dateLabel.textContent = new Intl.DateTimeFormat(undefined, {
      weekday: "long",
      month: "long",
      day: "numeric",
    }).format(date);
  } else {
    elements.dateLabel.textContent = "Waiting for verified task data";
  }
  syncMobileDetailModalState();
}

function renderSystemTicketsView() {
  const section = node("section", "projects-view");
  const heading = node("div", "projects-view-heading");
  const copy = node("div");
  copy.append(node("h2", "", "Mission Control System Tickets"), node("p", "", "Separate canonical change requests. Nightly work processes every Planned ticket; safe batching can sequence overlaps, and each ticket receives an execution or blocking outcome. Implementation and independent QA receipts remain with the ticket."));
  const controls = node("div", "system-ticket-view-controls");
  const completedLabel = node("label", "system-ticket-completed-toggle");
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = state.showCompletedSystemTickets;
  checkbox.setAttribute("aria-label", "Show Completed Tickets");
  checkbox.addEventListener("change", () => {
    state.showCompletedSystemTickets = checkbox.checked;
    state.completedSystemTickets = [];
    state.completedSystemTicketsOffset = 0;
    state.completedSystemTicketsHasMore = false;
    state.completedSystemTicketsError = "";
    render();
    if (state.showCompletedSystemTickets) {
      void loadCompletedSystemTickets({ reset: true });
    }
  });
  completedLabel.append(checkbox, node("span", "", "Show Completed Tickets"));
  const create = actionIcon("+", "New Ticket", { primary: true }); create.addEventListener("click", openSystemTicketDialog);
  controls.append(completedLabel, create);
  heading.append(copy, controls); section.append(heading);
  if (systemTicketsColdLoading()) {
    section.setAttribute("aria-busy", "true");
    section.append(node("div", "section-empty", "Reading System Tickets…"));
    return section;
  }
  if (
    state.systemTicketsError &&
    !state.systemTickets.length &&
    !state.systemTicketIssues.length
  ) {
    section.append(node("div", "section-empty", state.systemTicketsError));
    return section;
  }
  if (state.systemTicketsError && state.systemTickets.length) {
    section.append(node(
      "p",
      "surface-read-state is-error",
      `Last verified System Tickets remain visible because the canonical refresh is delayed: ${state.systemTicketsError}`,
    ));
  }
  if (state.systemTicketsReadState?.refreshing && state.systemTickets.length) {
    section.append(node("p", "surface-read-state", "Last verified System Tickets remain visible while GBrain refreshes."));
  }
  if (state.systemTicketIssues.length) {
    const issues = node("section", "needs-attention system-ticket-issues");
    issues.append(node("h3", "", "Ticket data needs attention"), node("p", "attention-intro", "These tickets remain separate from Tony and agent work. Inspect the canonical page before any repair; Mission Control will not rewrite it automatically."));
    const issueList = node("div", "attention-list");
    state.systemTicketIssues.forEach((issue) => {
      const item = node("article", "attention-item");
      item.append(node("strong", "", issue.slug || "Unknown System Ticket"), node("p", "", issue.message || "This System Ticket has invalid canonical data."), node("p", "", issue.impact || "Nightly dispatch is blocked until the ticket is repaired."));
      if (issue.slug) {
        const inspect = node("a", "detail-link", "Inspect in Memory Stargraph");
        inspect.href = `http://127.0.0.1:8788/?slug=${encodeURIComponent(issue.slug)}`;
        inspect.target = "_blank";
        inspect.rel = "noreferrer";
        item.append(inspect);
      }
      issueList.append(item);
    });
    issues.append(issueList); section.append(issues);
  }
  const visibleTickets = state.showCompletedSystemTickets
    ? [...state.systemTickets, ...state.completedSystemTickets]
    : state.systemTickets;
  if (!visibleTickets.length && !state.completedSystemTicketsLoading) {
    section.append(node("div", "section-empty", state.systemTicketIssues.length ? "No valid System Tickets are ready to display until the ticket data above is repaired." : state.showCompletedSystemTickets ? "No completed System Tickets yet." : "No open System Tickets. Enable Show Completed Tickets to read completed work."));
  }
  const list = node("div", "task-list");
  visibleTickets.forEach((ticket) => {
    const card = node("button", "system-ticket-card");
    card.type = "button";
    card.dataset.slug = ticket.slug;
    card.setAttribute("aria-label", `Open System Ticket ${ticket.title}`);
    card.setAttribute("aria-current", state.selectedSlug === ticket.slug ? "true" : "false");
    card.classList.toggle("is-selected", state.selectedSlug === ticket.slug);
    const header = node("div", "system-ticket-card-header");
    header.append(node("strong", "", ticket.title), node("span", `priority-badge ${ticket.priority}`, ticket.status));
    const meta = node("p", "system-ticket-card-meta", `${ticket.target_subsystem.replace(/_/g, " ")} · ${ticket.priority} priority · ${ticket.implementation_receipts.length} implementation / ${ticket.qa_receipts.length} QA receipts`);
    card.append(header, meta);
    card.addEventListener("click", () => selectSystemTicket(ticket.slug, card));
    list.append(card);
  });
  if (list.children.length) section.append(list);
  if (state.completedSystemTicketsLoading) {
    section.append(node("div", "section-empty", "Reading five completed System Tickets…"));
  }
  if (state.completedSystemTicketsError) {
    section.append(node("div", "section-empty", state.completedSystemTicketsError));
  }
  if (
    state.showCompletedSystemTickets &&
    state.completedSystemTicketsHasMore &&
    !state.completedSystemTicketsLoading
  ) {
    const more = node("button", "secondary-button system-ticket-show-more", "Show 5 More");
    more.type = "button";
    more.addEventListener("click", () => void loadCompletedSystemTickets());
    section.append(more);
  }
  return section;
}

function openSystemTicketDialog() {
  state.systemTicketEditorSlug = null;
  elements.systemTicketError.classList.add("is-hidden");
  elements.systemTicketForm.reset();
  elements.systemTicketEditorMode.textContent = "New canonical System Ticket";
  elements.systemTicketHeading.textContent = "New Mission Control System Ticket";
  elements.systemTicketEditorStatusField.classList.add("is-hidden");
  elements.systemTicketEditorStatus.classList.add("is-hidden");
  elements.systemTicketSubmit.textContent = "Create Ticket";
  elements.systemTicketDialog.showModal();
  window.setTimeout(() => elements.systemTicketTitle.focus(), 0);
}

function renderSystemTicketList(container, entries, emptyCopy) {
  container.replaceChildren();
  if (!Array.isArray(entries) || !entries.length) {
    container.append(node("li", "is-empty", emptyCopy));
    return;
  }
  entries.forEach((entry) => container.append(node("li", "", entry)));
}

function findSystemTicket(ticketSlug) {
  return [...state.systemTickets, ...state.completedSystemTickets].find(
    (item) => item.slug === ticketSlug,
  );
}

function selectSystemTicket(ticketSlug, originControl = null) {
  const ticket = findSystemTicket(ticketSlug);
  if (!ticket) return;
  if (originControl instanceof HTMLElement) {
    state.detailReturnFocus = { element: originControl, slug: ticket.slug };
  }
  state.selectedSlug = ticket.slug;
  state.selectedKind = "system-ticket";
  prepareDetailPanelWidth("system-ticket");
  elements.detailPanel.setAttribute("aria-hidden", "false");
  elements.detailPanel.setAttribute("aria-label", "System Ticket details");
  elements.detailEmpty.classList.add("is-hidden");
  elements.detailContent.classList.add("is-hidden");
  elements.artifactDetailContent.classList.add("is-hidden");
  elements.goalDetailContent.classList.add("is-hidden");
  elements.projectDetailContent.classList.add("is-hidden");
  elements.calendarEventDetail.classList.add("is-hidden");
  elements.systemTicketDetailContent.classList.remove("is-hidden");
  elements.systemTicketDetailStatus.textContent = ticket.status;
  elements.systemTicketDetailTitle.textContent = ticket.title;
  elements.systemTicketDetailPriority.textContent = ticket.priority;
  elements.systemTicketDetailTarget.textContent = ticket.target_subsystem.replace(/_/g, " ");
  elements.systemTicketDetailCreated.textContent = ticket.created_at ? new Date(ticket.created_at).toLocaleString() : "Not recorded";
  elements.systemTicketDetailUpdated.textContent = ticket.updated_at ? new Date(ticket.updated_at).toLocaleString() : "Not recorded";
  elements.systemTicketDetailRequest.textContent = ticket.verbatim_request || "No verbatim request recorded.";
  elements.systemTicketDetailCriteria.textContent = ticket.acceptance_criteria || "No acceptance criteria recorded.";
  renderSystemTicketList(elements.systemTicketDetailEvidence, ticket.linked_evidence, "No linked evidence recorded.");
  renderSystemTicketList(elements.systemTicketDetailImplementation, ticket.implementation_receipts, "No implementation receipt recorded.");
  renderSystemTicketList(elements.systemTicketDetailQa, ticket.qa_receipts, "No independent QA receipt recorded.");
  elements.systemTicketDetailError.classList.add("is-hidden");
  elements.systemTicketDetailGbrainLink.href = `http://127.0.0.1:8788/?slug=${encodeURIComponent(ticket.slug)}`;
  elements.systemTicketDetailSlug.textContent = ticket.slug;
  render();
  window.requestAnimationFrame(() => {
    if (window.matchMedia("(max-width: 760px)").matches) {
      elements.detailPanel.scrollIntoView({ block: "start", behavior: "auto" });
    }
    elements.systemTicketDetailTitle.focus({ preventScroll: true });
  });
}

function openEditSystemTicket() {
  const ticket = findSystemTicket(state.selectedSlug);
  if (state.selectedKind !== "system-ticket" || !ticket) return;
  state.systemTicketEditorSlug = ticket.slug;
  elements.systemTicketError.classList.add("is-hidden");
  elements.systemTicketEditorMode.textContent = "Editing canonical System Ticket";
  elements.systemTicketHeading.textContent = "Edit Mission Control System Ticket";
  elements.systemTicketTitle.value = ticket.title;
  elements.systemTicketRequest.value = ticket.verbatim_request;
  elements.systemTicketTarget.value = ticket.target_subsystem;
  elements.systemTicketPriority.value = ticket.priority;
  elements.systemTicketCriteria.value = ticket.acceptance_criteria;
  elements.systemTicketEditorStatus.value = ticket.status;
  elements.systemTicketEditorStatusField.classList.remove("is-hidden");
  elements.systemTicketEditorStatus.classList.remove("is-hidden");
  elements.systemTicketSubmit.textContent = "Save Ticket";
  elements.systemTicketDialog.showModal();
  window.setTimeout(() => elements.systemTicketTitle.focus(), 0);
}

function loadSystemTickets({ force = false, poll = false } = {}) {
  if (state.systemTicketsLoadPromise) return state.systemTicketsLoadPromise;
  state.systemTicketsLoadPromise = performSystemTicketLoad({ force, poll }).finally(() => {
    state.systemTicketsLoadPromise = null;
  });
  return state.systemTicketsLoadPromise;
}

async function performSystemTicketLoad({ force = false } = {}) {
  state.systemTicketsLoading = !state.systemTickets.length;
  try {
    const options = { headers: { Accept: "application/json" }, cache: "no-store" };
    const response = force
      ? await fetch("/api/system-tickets?include_completed=0&refresh=1", options)
      : await fetch("/api/system-tickets?include_completed=0", options);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "System Tickets could not be read.");
    state.systemTicketsReadState = payload.read_state || null;
    if (response.status === 200) {
      state.systemTickets = Array.isArray(payload.tickets) ? payload.tickets : [];
      state.systemTicketIssues = Array.isArray(payload.issues) ? payload.issues : [];
    }
    state.systemTicketsError = payload.read_state?.error || "";
    if (payload.read_state?.refreshing) scheduleSurfacePoll("system_tickets");
  }
  catch (error) { state.systemTicketsError = error.message || "System Tickets could not be read."; }
  finally { state.systemTicketsLoading = false; if (state.activeView === "system-tickets") render(); }
}

async function loadCompletedSystemTickets({ reset = false } = {}) {
  if (state.completedSystemTicketsLoading || !state.showCompletedSystemTickets) return;
  if (reset) {
    state.completedSystemTickets = [];
    state.completedSystemTicketsOffset = 0;
    state.completedSystemTicketsHasMore = false;
  }
  state.completedSystemTicketsLoading = true;
  state.completedSystemTicketsError = "";
  if (state.activeView === "system-tickets") render();
  const offset = state.completedSystemTicketsOffset;
  try {
    const response = await fetch(
      `/api/system-tickets?completed_only=1&offset=${offset}&limit=5`,
      { headers: { Accept: "application/json" }, cache: "no-store" },
    );
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Completed System Tickets could not be read.");
    }
    const page = Array.isArray(payload.tickets) ? payload.tickets : [];
    const bySlug = new Map(
      [...state.completedSystemTickets, ...page].map((ticket) => [ticket.slug, ticket]),
    );
    state.completedSystemTickets = [...bySlug.values()];
    state.completedSystemTicketsOffset = offset + page.length;
    state.completedSystemTicketsHasMore = Boolean(payload.pagination?.has_more);
  } catch (error) {
    state.completedSystemTicketsError =
      error.message || "Completed System Tickets could not be read.";
  } finally {
    state.completedSystemTicketsLoading = false;
    if (state.activeView === "system-tickets") render();
  }
}

async function submitSystemTicket(event) {
  event.preventDefault(); elements.systemTicketError.classList.add("is-hidden");
  elements.systemTicketSubmit.disabled = true;
  const editing = Boolean(state.systemTicketEditorSlug);
  try {
    const ticketPayload = {title:elements.systemTicketTitle.value,verbatim_request:elements.systemTicketRequest.value,target_subsystem:elements.systemTicketTarget.value,priority:elements.systemTicketPriority.value,acceptance_criteria:elements.systemTicketCriteria.value};
    if (editing) ticketPayload.status = elements.systemTicketEditorStatus.value;
    const endpoint = editing ? `/api/system-tickets/${encodeURIComponent(state.systemTicketEditorSlug)}` : "/api/system-tickets";
    const response = await fetch(endpoint, {
      method: state.systemTicketEditorSlug ? "PATCH" : "POST",
      headers:{"Content-Type":"application/json",Accept:"application/json"},
      body:JSON.stringify(ticketPayload),
    });
    const payload=await response.json();
    if(!response.ok || !payload.receipt?.verified || !payload.ticket) throw new Error(payload.error || "Ticket save was not verified.");
    const savedSlug = payload.ticket.slug;
    elements.systemTicketDialog.close();
    state.systemTicketEditorSlug = null;
    await loadSystemTickets({ force: true });
    if (state.showCompletedSystemTickets) {
      await loadCompletedSystemTickets({ reset: true });
    }
    state.activeView="system-tickets";
    selectSystemTicket(savedSlug);
    showToast(editing ? "System Ticket updated and verified in GBrain." : "System Ticket created and verified in GBrain.");
  }
  catch(error){ elements.systemTicketError.textContent=error.message; elements.systemTicketError.classList.remove("is-hidden"); }
  finally { elements.systemTicketSubmit.disabled = false; }
}

function setTodoAddOpen(open, { focus = true } = {}) {
  state.todoAddOpen = Boolean(open);
  elements.taskTodoAddForm.classList.toggle("is-hidden", !state.todoAddOpen);
  elements.taskTodoAddToggle.setAttribute(
    "aria-expanded",
    state.todoAddOpen ? "true" : "false",
  );
  if (!state.todoAddOpen) elements.taskTodoAddForm.reset();
  if (!focus) return;
  window.setTimeout(() => {
    if (state.todoAddOpen) elements.taskTodoText.focus({ preventScroll: true });
    else elements.taskTodoAddToggle.focus({ preventScroll: true });
  }, 0);
}

function todoStatusLabel(todo) {
  return todo.status === "done" ? "Done" : "Not Done";
}

function isActiveHandoffQuestion(todo, task = findTaskBySlug(todo?.parent_task)) {
  return Boolean(
    todo && task?.handoff?.state === "waiting_for_input" &&
    task.handoff.question_todo === todo.slug && todo.status === "not_done",
  );
}

function agentDisplayName(slug, task = null) {
  const taskOwner = task?.owner;
  if (taskOwner?.slug === slug && taskOwner?.name) return taskOwner.name;
  const directoryName = state.agents.find((agent) => agent.slug === slug)?.name;
  if (directoryName) return directoryName;
  if (!slug) return "the assigned Agent";
  return slug.split("/").pop().replaceAll("-", " ").replace(
    /(^|\s)\S/g,
    (letter) => letter.toUpperCase(),
  );
}

function blockerDisplayName(slug) {
  if (slug === "people/tony-guan") return "Tony";
  return agentDisplayName(slug);
}

function todoCard(todo) {
  const card = node("article", "task-todo-card");
  card.dataset.todoSlug = todo.slug;
  card.setAttribute("role", "listitem");
  card.tabIndex = -1;
  const heading = node("div", "task-todo-card-heading");
  const title = node("div");
  title.append(
    node("h4", "", todo.text),
    node("span", `task-todo-status ${todo.status}`, todoStatusLabel(todo)),
  );
  const isHandoffQuestion = isActiveHandoffQuestion(todo);
  heading.append(title);
  if (!isHandoffQuestion) {
    const changeStatus = node(
      "button",
      "secondary-button",
      todo.status === "done" ? "Reopen" : "Mark Done",
    );
    changeStatus.type = "button";
    changeStatus.setAttribute(
      "aria-label",
      `${todo.status === "done" ? "Reopen" : "Mark Done"} TODO: ${todo.text}`,
    );
    changeStatus.addEventListener("click", () => changeTodoStatus(todo, changeStatus));
    heading.append(changeStatus);
  }

  const details = node("details", "task-todo-details");
  const summary = node("summary", "", `Open ${todo.kind || "action"} details, comments, and history`);
  const detailCopy = node(
    "p",
    "task-todo-detail-copy",
    todo.detail || "No additional detail.",
  );
  const provenance = node(
    "p",
    "task-todo-provenance",
    `${todo.kind || "action"} · ${todo.creator || "Creator unavailable"} · ${todo.source || "Source unavailable"}`,
  );

  const editForm = node("form", "task-todo-edit-form");
  const editText = document.createElement("input");
  editText.type = "text";
  editText.maxLength = 240;
  editText.required = true;
  editText.value = todo.text;
  editText.setAttribute("aria-label", `Edit TODO text: ${todo.text}`);
  const editDetail = document.createElement("textarea");
  editDetail.rows = 2;
  editDetail.maxLength = 5000;
  editDetail.value = todo.detail || "";
  editDetail.setAttribute("aria-label", `Edit TODO detail: ${todo.text}`);
  const editSubmit = node("button", "secondary-button", "Save TODO");
  editSubmit.type = "submit";
  editForm.append(editText, editDetail, editSubmit);
  editForm.addEventListener("submit", (event) => {
    event.preventDefault();
    editTaskTodo(todo, editText.value, editDetail.value, editSubmit);
  });

  const commentsHeading = node("h5", "", "Comments");
  const comments = node("ol", "task-todo-comments");
  (Array.isArray(todo.comments) ? todo.comments : []).forEach((comment) => {
    const item = node("li");
    item.append(
      node("p", "", comment.body),
      node(
        "small",
        "",
        `${comment.author || "Author unavailable"} · ${comment.created_at ? new Date(comment.created_at).toLocaleString() : "Time unavailable"}`,
      ),
    );
    comments.append(item);
  });
  if (!comments.children.length) {
    comments.append(node("li", "is-empty", "No comments yet."));
  }
  const commentForm = node("form", "task-todo-comment-form");
  const commentInput = document.createElement("textarea");
  commentInput.rows = 2;
  commentInput.maxLength = 4000;
  commentInput.required = true;
  commentInput.placeholder = "Reply to this TODO";
  commentInput.setAttribute("aria-label", `Comment on TODO: ${todo.text}`);
  const commentSubmit = node("button", "secondary-button", "Add Comment");
  commentSubmit.type = "submit";
  commentForm.append(commentInput, commentSubmit);
  commentForm.addEventListener("submit", (event) => {
    event.preventDefault();
    commentOnTodo(todo, commentInput.value, commentSubmit);
  });

  const historyHeading = node("h5", "", "History");
  const history = node("ol", "task-todo-history");
  (Array.isArray(todo.events) ? todo.events : []).forEach((item) => {
    const row = node("li");
    row.append(
      node("strong", "", item.event_type.replaceAll("_", " ")),
      node(
        "small",
        "",
        `${item.actor || "Actor unavailable"} · ${item.occurred_at ? new Date(item.occurred_at).toLocaleString() : "Time unavailable"}`,
      ),
    );
    history.append(row);
  });
  if (!history.children.length) {
    history.append(node("li", "is-empty", "No canonical history available."));
  }
  details.append(summary, provenance, detailCopy);
  if (!isHandoffQuestion) details.append(editForm);
  details.append(commentsHeading, comments);
  if (!isHandoffQuestion) details.append(commentForm);
  details.append(historyHeading, history);
  card.append(heading, details);
  return card;
}

function renderTaskHandoff(task) {
  const handoff = task?.handoff;
  const blockers = Array.isArray(task?.blockers) ? task.blockers : [];
  const show = Boolean(handoff || task?.status === "blocked");
  elements.taskHandoffPanel.classList.toggle("is-hidden", !show);
  elements.taskHandoffAnswerForm.classList.add("is-hidden");
  elements.taskHandoffQuestion.classList.add("is-hidden");
  elements.taskHandoffError.classList.add("is-hidden");
  if (!show) return;

  if (!handoff) {
    const blocker = blockers.length
      ? blockers.map(blockerDisplayName).join(", ")
      : "a recorded blocker";
    elements.taskHandoffHeading.textContent = `Blocked by ${blocker}: ${task.next_action || "No next action is recorded yet."}`;
    elements.taskHandoffCopy.textContent = "Resolve this blocker to continue.";
    elements.taskHandoffQuestion.textContent = "";
    return;
  }

  const question = (Array.isArray(task.todos) ? task.todos : []).find(
    (todo) => todo.slug === handoff.question_todo,
  );
  const agentName = agentDisplayName(handoff.resume_owner, task);
  if (handoff.state === "waiting_for_input") {
    elements.taskHandoffHeading.textContent = "Waiting for your answer";
    elements.taskHandoffCopy.textContent = `${agentName} will resume with: ${handoff.resume_action}`;
    elements.taskHandoffQuestion.textContent = question?.detail || question?.text || "The canonical question is loading.";
    elements.taskHandoffQuestion.classList.remove("is-hidden");
    elements.taskHandoffAnswerForm.classList.remove("is-hidden");
    return;
  }
  if (handoff.state === "ready_for_agent") {
    elements.taskHandoffHeading.textContent = `Answer recorded — waiting for ${agentName}'s next hourly scan.`;
    elements.taskHandoffCopy.textContent = `Verified next action: ${handoff.resume_action}`;
    elements.taskHandoffQuestion.textContent = "";
    return;
  }
  elements.taskHandoffHeading.textContent = `${agentName} is working.`;
  elements.taskHandoffCopy.textContent = `Acknowledged next action: ${handoff.resume_action}`;
  elements.taskHandoffQuestion.textContent = "";
}

function renderTaskTodos(task) {
  renderTaskHandoff(task);
  const todos = Array.isArray(task.todos) ? task.todos : [];
  const filtered = state.showCompletedTodos
    ? todos
    : todos.filter((todo) => todo.status === "not_done");
  elements.taskTodoList.replaceChildren(...filtered.map(todoCard));
  elements.taskTodoEmpty.textContent = todos.length ? "No open TODOs." : "No TODO yet";
  elements.taskTodoEmpty.classList.toggle("is-hidden", filtered.length > 0);
  elements.taskTodoAddForm.classList.toggle("is-hidden", !state.todoAddOpen);
  elements.taskTodoAddToggle.setAttribute(
    "aria-expanded",
    state.todoAddOpen ? "true" : "false",
  );
  elements.taskTodoLoading.classList.toggle(
    "is-hidden",
    state.todoLoadingTask !== task.slug,
  );
  elements.taskTodoShowCompleted.checked = state.showCompletedTodos;
}

function todoErrorMessage(error) {
  if (error.code === "todo_changed") {
    return "This TODO changed in GBrain. Its latest canonical version has been reloaded; review it before retrying.";
  }
  if (error.code === "partial_write" && error.slug) {
    return `${error.message} Inspect ${error.slug} before retrying.`;
  }
  return error.message || "The TODO change could not be verified.";
}

function replaceTaskTodos(taskSlug, todos) {
  const task = findTaskBySlug(taskSlug);
  if (!task) return null;
  task.todos = Array.isArray(todos) ? todos : [];
  return task;
}

function restoreTodoFocus() {
  const returnFocus = state.todoReturnFocus;
  state.todoReturnFocus = null;
  if (!returnFocus) return;
  window.requestAnimationFrame(() => {
    const candidate = Array.from(
      elements.taskTodoList.querySelectorAll(".task-todo-card"),
    ).find((candidate) => candidate.dataset.todoSlug === returnFocus.slug);
    const target = (
      returnFocus.control === "summary"
        ? candidate?.querySelector("summary")
        : candidate
    ) || elements.taskTodoShowCompleted;
    target?.focus({ preventScroll: true });
  });
}

function applyVerifiedTodoMutation(taskSlug, todo, returnFocus) {
  const task = findTaskBySlug(taskSlug);
  if (!task) return null;
  const todos = Array.isArray(task.todos) ? [...task.todos] : [];
  const index = todos.findIndex((candidate) => candidate.slug === todo.slug);
  if (index === -1) todos.push(todo);
  else todos[index] = todo;
  todos.sort((left, right) =>
    Number(left.status === "done") - Number(right.status === "done") ||
    String(left.created_at).localeCompare(String(right.created_at)) ||
    left.slug.localeCompare(right.slug));
  task.todos = todos;
  state.todoReturnFocus = returnFocus;
  if (state.selectedKind === "task" && state.selectedSlug === taskSlug) {
    renderTaskTodos(task);
    restoreTodoFocus();
  }
  return task;
}

async function refreshTaskTodos(taskSlug, returnFocus = null) {
  state.todoLoadingTask = taskSlug;
  state.todoReturnFocus = returnFocus;
  const current = findTaskBySlug(taskSlug);
  if (current) renderTaskTodos(current);
  try {
    const response = await fetch(
      `/api/tasks/${encodeURIComponent(taskSlug)}/todos`,
      { headers: { Accept: "application/json" }, cache: "no-store" },
    );
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(result.error || "Canonical TODOs could not be read.");
      error.code = result.code;
      throw error;
    }
    const task = replaceTaskTodos(taskSlug, result.todos);
    if (task && state.selectedKind === "task" && state.selectedSlug === taskSlug) {
      renderTaskTodos(task);
      restoreTodoFocus();
    }
    return task;
  } finally {
    state.todoLoadingTask = null;
    const task = findTaskBySlug(taskSlug);
    if (task && state.selectedKind === "task" && state.selectedSlug === taskSlug) {
      renderTaskTodos(task);
    }
  }
}

async function todoMutation(endpoint, method, body) {
  const response = await fetch(endpoint, {
    method,
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok || !result.receipt?.verified || !result.receipt?.todo) {
    const error = new Error(result.error || "Canonical TODO readback was missing.");
    error.code = result.code || "ambiguous_readback";
    error.slug = result.slug;
    throw error;
  }
  return result.receipt.todo;
}

async function answerAndHandBack(event) {
  event.preventDefault();
  const task = findTaskBySlug(state.selectedSlug);
  const todo = (Array.isArray(task?.todos) ? task.todos : []).find(
    (candidate) => isActiveHandoffQuestion(candidate, task),
  );
  if (!task || !todo) return;
  elements.taskHandoffError.classList.add("is-hidden");
  elements.taskHandoffSubmit.disabled = true;
  try {
    const response = await fetch(
      `/api/todos/${encodeURIComponent(todo.slug)}/answer`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          answer: elements.taskHandoffAnswer.value,
          expected_updated_at: todo.updated_at,
          actor: "people/tony-guan",
          source: "mission_control",
          idempotency_key: crypto.randomUUID(),
        }),
      },
    );
    const receipt = await response.json();
    if (!response.ok || !receipt.verified || !receipt.task || !receipt.todo) {
      const error = new Error(receipt.error || "Answer readback was not verified in GBrain.");
      error.code = receipt.code || "ambiguous_readback";
      throw error;
    }
    const current = findTaskBySlug(task.slug);
    const todos = Array.isArray(current?.todos) ? [...current.todos] : [];
    const todoIndex = todos.findIndex((candidate) => candidate.slug === receipt.todo.slug);
    if (todoIndex === -1) todos.push(receipt.todo);
    else todos[todoIndex] = receipt.todo;
    reconcileVerifiedTask({ ...receipt.task, todos });
    state.selectedSlug = receipt.task.slug;
    state.selectedKind = "task";
    elements.taskHandoffAnswer.value = "";
    selectTask(receipt.task.slug);
    window.requestAnimationFrame(() => elements.taskHandoffPanel.focus({ preventScroll: true }));
    showToast(`Answer verified. ${agentDisplayName(receipt.next_owner, receipt.task)} can resume this task.`);
  } catch (error) {
    const message = todoErrorMessage(error);
    elements.taskHandoffError.textContent = message;
    elements.taskHandoffError.classList.remove("is-hidden");
    if (error.code === "todo_changed") {
      await refreshTaskTodos(task.slug, { slug: todo.slug, control: "summary" });
      elements.taskHandoffError.textContent = message;
      elements.taskHandoffError.classList.remove("is-hidden");
    }
  } finally {
    elements.taskHandoffSubmit.disabled = false;
  }
}

async function createTaskTodo(event) {
  event.preventDefault();
  if (state.selectedKind !== "task" || !state.selectedSlug) return;
  const taskSlug = state.selectedSlug;
  elements.taskTodoError.classList.add("is-hidden");
  elements.taskTodoAdd.disabled = true;
  try {
    const todo = await todoMutation(
      `/api/tasks/${encodeURIComponent(taskSlug)}/todos`,
      "POST",
      {
        text: elements.taskTodoText.value,
        detail: elements.taskTodoDetail.value,
        kind: elements.taskTodoKind.value,
        actor: "people/tony-guan",
        source: "mission_control",
        idempotency_key: crypto.randomUUID(),
      },
    );
    setTodoAddOpen(false, { focus: false });
    applyVerifiedTodoMutation(taskSlug, todo, { slug: todo.slug, control: "summary" });
    showToast("TODO created and verified in GBrain.");
  } catch (error) {
    elements.taskTodoError.textContent = todoErrorMessage(error);
    elements.taskTodoError.classList.remove("is-hidden");
  } finally {
    elements.taskTodoAdd.disabled = false;
  }
}

async function editTaskTodo(todo, text, detail, submit) {
  elements.taskTodoError.classList.add("is-hidden");
  submit.disabled = true;
  try {
    const updated = await todoMutation(`/api/todos/${encodeURIComponent(todo.slug)}`, "PATCH", {
      text,
      detail,
      expected_updated_at: todo.updated_at,
      actor: "people/tony-guan",
      source: "mission_control",
      idempotency_key: crypto.randomUUID(),
    });
    applyVerifiedTodoMutation(todo.parent_task, updated, { slug: todo.slug, control: "summary" });
    showToast("TODO edit verified in GBrain.");
  } catch (error) {
    elements.taskTodoError.textContent = todoErrorMessage(error);
    elements.taskTodoError.classList.remove("is-hidden");
    if (error.code === "todo_changed") {
      await refreshTaskTodos(todo.parent_task, { slug: todo.slug, control: "summary" });
    }
  } finally {
    submit.disabled = false;
  }
}

async function commentOnTodo(todo, body, submit) {
  elements.taskTodoError.classList.add("is-hidden");
  submit.disabled = true;
  try {
    const updated = await todoMutation(
      `/api/todos/${encodeURIComponent(todo.slug)}/comments`,
      "POST",
      {
        body,
        expected_updated_at: todo.updated_at,
        author: "people/tony-guan",
        source: "mission_control",
        idempotency_key: crypto.randomUUID(),
      },
    );
    applyVerifiedTodoMutation(todo.parent_task, updated, { slug: todo.slug, control: "summary" });
    showToast("Comment appended and verified in GBrain.");
  } catch (error) {
    elements.taskTodoError.textContent = todoErrorMessage(error);
    elements.taskTodoError.classList.remove("is-hidden");
    if (error.code === "todo_changed") {
      await refreshTaskTodos(todo.parent_task, { slug: todo.slug, control: "summary" });
    }
  } finally {
    submit.disabled = false;
  }
}

async function changeTodoStatus(todo, button) {
  elements.taskTodoError.classList.add("is-hidden");
  button.disabled = true;
  try {
    const updated = await todoMutation(
      `/api/todos/${encodeURIComponent(todo.slug)}/status`,
      "PATCH",
      {
        status: todo.status === "done" ? "not_done" : "done",
        expected_updated_at: todo.updated_at,
        actor: "people/tony-guan",
        source: "mission_control",
        idempotency_key: crypto.randomUUID(),
      },
    );
    applyVerifiedTodoMutation(todo.parent_task, updated, { slug: todo.slug, control: "summary" });
    showToast(`TODO marked ${todo.status === "done" ? "Not Done" : "Done"}.`);
  } catch (error) {
    elements.taskTodoError.textContent = todoErrorMessage(error);
    elements.taskTodoError.classList.remove("is-hidden");
    if (error.code === "todo_changed") {
      await refreshTaskTodos(todo.parent_task, { slug: todo.slug, control: "summary" });
    }
  } finally {
    button.disabled = false;
  }
}

function renderProposalDecisionTimeline(task) {
  elements.proposalDecisionTimeline.replaceChildren();
  const submitted = task.proposal_submitted_at || task.submitted_at || task.created_at;
  if (submitted) {
    const item = node("li", "is-current");
    item.append(
      node("span", "next-action-state", "Submitted"),
      node("strong", "", "Proposed for review"),
      node("time", "", new Date(submitted).toLocaleString()),
    );
    elements.proposalDecisionTimeline.append(item);
  }
  let events = Array.isArray(task.proposal_decision_events)
    ? task.proposal_decision_events
    : Array.isArray(task.decision_events) ? task.decision_events : [];
  const projectedDecision = task.proposal_decision || task.decision;
  const projectedAt = task.proposal_decided_at || task.decision_at;
  if (!events.length && ["approve", "reject"].includes(projectedDecision) && projectedAt) {
    events = [{
      decision: projectedDecision,
      decision_note: task.proposal_decision_note || task.decision_note || "",
      occurred_at: projectedAt,
      resulting_status: task.resulting_status || (projectedDecision === "approve" ? "planned" : "cancelled"),
      legacy_projection: true,
    }];
  }
  events.forEach((event) => {
    const item = node("li", "is-decision");
    const label = event.decision === "approve" ? "Approved" : "Rejected";
    item.append(
      node("span", "next-action-state", `${label} · ${event.resulting_status}${event.legacy_projection ? " · Legacy projection" : ""}`),
      node("strong", "", event.decision_note || `${label} without a note`),
      node("time", "", event.occurred_at ? new Date(event.occurred_at).toLocaleString() : "Decision time unavailable"),
    );
    elements.proposalDecisionTimeline.append(item);
  });
  if (!submitted && !events.length) {
    elements.proposalDecisionTimeline.append(
      node("li", "is-empty", "No canonical proposal timeline evidence is available."),
    );
  }
}

function artifactAgent(artifact) {
  const profile = state.agents.find((agent) => agent.slug === artifact.created_by);
  if (profile) return profile;
  const name = String(artifact.created_by || "Unknown Agent").split("/").pop();
  return {
    slug: artifact.created_by,
    name: name ? `${name[0].toUpperCase()}${name.slice(1)}` : "Unknown Agent",
    avatar: { kind: "initials", value: name?.slice(0, 2).toUpperCase() || "A" },
  };
}

function artifactTask(artifact) {
  return findTaskBySlug(artifact.produced_for);
}

function artifactCard(artifact) {
  const button = node("button", "artifact-card");
  button.type = "button";
  button.dataset.slug = artifact.slug;
  button.setAttribute("aria-label", `Open Artifact ${artifact.title}`);
  button.setAttribute("aria-current", state.selectedKind === "artifact" && state.selectedSlug === artifact.slug ? "true" : "false");
  button.classList.toggle("is-selected", state.selectedKind === "artifact" && state.selectedSlug === artifact.slug);
  const owner = artifactAgent(artifact);
  const ownerRow = node("div", "artifact-owner-row");
  const avatar = node("span", "agent-avatar");
  avatar.setAttribute("aria-hidden", "true");
  setCompactAgentAvatar(avatar, owner);
  ownerRow.append(avatar, node("span", "", owner.name), node("span", "artifact-kind", artifact.artifact_kind));
  const task = artifactTask(artifact);
  const project = state.projects.find((item) => item.slug === artifact.project);
  button.append(
    node("strong", "artifact-card-title", artifact.title),
    ownerRow,
    node("span", "artifact-card-task", task?.title || artifact.produced_for),
    node("span", "artifact-card-meta", `${project?.title || artifact.project || "No project"} · ${new Date(artifact.created_at).toLocaleString()}`),
  );
  button.addEventListener("click", () => selectArtifact(artifact.slug, button));
  return button;
}

function artifactLoadedCountLabel(count) {
  return `${count} loaded Artifact${count === 1 ? "" : "s"}`;
}

function artifactHierarchyLabel(kind, slug) {
  if (kind === "agent") return artifactAgent({ created_by: slug }).name;
  if (kind === "goal") {
    if (!slug) return "No Goal";
    return state.snapshot?.goals?.find((goal) => goal.slug === slug)?.title || slug;
  }
  if (kind === "project") {
    if (!slug) return "No Project";
    return state.projects.find((project) => project.slug === slug)?.title || slug;
  }
  if (!slug) return "No producing Task";
  return findTaskBySlug(slug)?.title || slug;
}

function artifactHierarchyKey(kind, slug, parentKey = "") {
  return `${parentKey}${parentKey ? "/" : ""}${kind}:${slug || "none"}`;
}

function buildArtifactHierarchy() {
  const loadedArtifacts = [...state.artifacts];
  const agentSlugs = new Set(loadedArtifacts.map((artifact) => artifact.created_by));
  state.agents
    .filter((agent) => state.artifactAgentFilter === "all" || agent.slug === state.artifactAgentFilter)
    .forEach((agent) => agentSlugs.add(agent.slug));
  if (state.artifactAgentFilter !== "all") agentSlugs.add(state.artifactAgentFilter);

  const buildLevel = (kind, artifacts, parentKey) => {
    const field = { goal: "goal", project: "project", task: "produced_for" }[kind];
    const groups = new Map();
    artifacts.forEach((artifact) => {
      const slug = artifact[field] || "";
      if (!groups.has(slug)) groups.set(slug, []);
      groups.get(slug).push(artifact);
    });
    if (!groups.size) groups.set("", []);
    return [...groups.entries()]
      .sort(([left], [right]) => artifactHierarchyLabel(kind, left).localeCompare(artifactHierarchyLabel(kind, right)))
      .map(([slug, groupedArtifacts]) => {
        const key = artifactHierarchyKey(kind, slug, parentKey);
        const children = kind === "task"
          ? groupedArtifacts
          : buildLevel(kind === "goal" ? "project" : "task", groupedArtifacts, key);
        return {
          kind,
          slug,
          key,
          label: artifactHierarchyLabel(kind, slug),
          count: new Set(groupedArtifacts.map((artifact) => artifact.slug)).size,
          children,
        };
      });
  };

  const roots = [...agentSlugs]
    .filter(Boolean)
    .sort((left, right) => artifactHierarchyLabel("agent", left).localeCompare(artifactHierarchyLabel("agent", right)))
    .map((agentSlug) => {
      const artifacts = loadedArtifacts.filter((artifact) => artifact.created_by === agentSlug);
      const key = artifactHierarchyKey("agent", agentSlug);
      return {
        kind: "agent",
        slug: agentSlug,
        key,
        label: artifactHierarchyLabel("agent", agentSlug),
        count: new Set(artifacts.map((artifact) => artifact.slug)).size,
        children: buildLevel("goal", artifacts, key),
      };
    });
  const validKeys = new Set();
  const collectKeys = (nodes) => nodes.forEach((entry) => {
    validKeys.add(entry.key);
    if (entry.kind !== "task") collectKeys(entry.children);
  });
  collectKeys(roots);
  [...state.artifactExpanded].forEach((key) => {
    if (!validKeys.has(key)) state.artifactExpanded.delete(key);
  });
  if (!state.artifactHierarchyInitialized) {
    roots.forEach((entry) => state.artifactExpanded.add(entry.key));
    state.artifactHierarchyInitialized = true;
  }
  return roots;
}

function artifactHierarchyNode(entry, level = 1) {
  const section = node("section", `artifact-hierarchy-node level-${level}`);
  const contentId = `artifact-hierarchy-${entry.key.replace(/[^a-z0-9_-]+/gi, "-")}`;
  const expanded = state.artifactExpanded.has(entry.key);
  const toggle = node("button", "artifact-hierarchy-toggle");
  toggle.type = "button";
  toggle.setAttribute("aria-expanded", String(expanded));
  toggle.setAttribute("aria-controls", contentId);
  toggle.append(
    node("span", "artifact-hierarchy-chevron", expanded ? "−" : "+"),
    node("strong", "artifact-hierarchy-label", entry.label),
    node("span", "artifact-hierarchy-count", artifactLoadedCountLabel(entry.count)),
  );
  const content = node("div", "artifact-hierarchy-children");
  content.id = contentId;
  content.hidden = !expanded;
  if (entry.kind === "task") {
    if (!entry.children.length) {
      content.append(node("p", "artifact-hierarchy-empty", "No canonical Artifacts in this branch."));
    } else {
      const grid = node("div", "artifact-grid");
      entry.children.forEach((artifact) => grid.append(artifactCard(artifact)));
      content.append(grid);
    }
  } else {
    entry.children.forEach((child) => content.append(artifactHierarchyNode(child, level + 1)));
  }
  toggle.addEventListener("click", () => {
    if (state.artifactExpanded.has(entry.key)) state.artifactExpanded.delete(entry.key);
    else state.artifactExpanded.add(entry.key);
    render();
  });
  section.append(toggle, content);
  return section;
}

function artifactViewModeButton(label, mode) {
  const button = node("button", "secondary-button artifact-view-mode", label);
  button.type = "button";
  button.setAttribute("aria-pressed", String(state.artifactViewMode === mode));
  button.addEventListener("click", () => {
    state.artifactViewMode = mode;
    render();
  });
  return button;
}

function renderArtifactsView() {
  const section = node("section", "artifacts-view");
  if (state.artifactsLoading && !state.artifacts.length) {
    section.append(node("div", "section-empty", "Reading canonical Agent Artifacts…"));
    return section;
  }
  if (state.artifactsError && !state.artifacts.length) {
    const error = node("div", "section-empty artifact-error-state");
    error.append(node("h2", "", "Artifacts are temporarily unavailable"), node("p", "", state.artifactsError));
    const retry = node("button", "secondary-button", "Try again");
    retry.type = "button";
    retry.addEventListener("click", () => void loadArtifacts({ reset: true }));
    error.append(retry);
    section.append(error);
    return section;
  }
  if (state.artifactIssues.length) {
    const issues = node("section", "needs-attention artifact-issues");
    issues.append(node("h2", "", "Artifact data needs attention"));
    state.artifactIssues.forEach((issue) => {
      const item = node("article", "attention-item");
      item.append(node("strong", "", issue.slug || "Unknown Artifact"), node("p", "", issue.message || "Canonical Artifact data is invalid."));
      issues.append(item);
    });
    section.append(issues);
  }
  if (!state.artifacts.length) {
    const empty = node("section", "simple-empty");
    const copy = node("div");
    copy.append(
      node("h2", "", viewMeta.artifacts.emptyTitle),
      node("p", "", viewMeta.artifacts.emptyCopy),
    );
    empty.append(copy);
    section.append(empty);
    return section;
  }
  const viewControls = node("div", "artifact-view-controls");
  viewControls.append(
    artifactViewModeButton("Hierarchy", "hierarchy"),
    artifactViewModeButton("Recent", "recent"),
    node("span", "artifact-view-summary", artifactLoadedCountLabel(new Set(state.artifacts.map((artifact) => artifact.slug)).size)),
  );
  section.append(viewControls);
  if (state.artifactViewMode === "hierarchy") {
    const hierarchy = node("div", "artifact-hierarchy");
    buildArtifactHierarchy().forEach((entry) => hierarchy.append(artifactHierarchyNode(entry)));
    section.append(hierarchy);
  } else {
    const grid = node("div", "artifact-grid");
    [...state.artifacts]
      .sort((left, right) => String(right.created_at || "").localeCompare(String(left.created_at || "")))
      .forEach((artifact) => grid.append(artifactCard(artifact)));
    section.append(grid);
  }
  if (state.artifactsLoading) section.append(node("p", "artifact-load-state", "Reading newer canonical results…"));
  if (state.artifactsNextCursor !== null && !state.artifactsLoading) {
    const more = node("button", "secondary-button artifact-load-more", "Load more");
    more.type = "button";
    more.addEventListener("click", () => void loadArtifacts({ append: true }));
    section.append(more);
  }
  return section;
}

async function loadArtifacts({ reset = false, append = false } = {}) {
  const requestToken = ++state.artifactRequestToken;
  if (reset) {
    state.artifactsNextCursor = null;
    state.artifactTaskFilter = null;
  }
  state.artifactsLoading = true;
  state.artifactsError = "";
  if (state.activeView === "artifacts") render();
  const params = new URLSearchParams({
    cursor: String(append ? state.artifactsNextCursor || 0 : 0),
    limit: "25",
  });
  if (state.artifactAgentFilter !== "all") params.set("agent", state.artifactAgentFilter);
  if (state.artifactTaskFilter) params.set("task", state.artifactTaskFilter);
  try {
    const response = await fetch(`/api/artifacts?${params}`, { headers: { Accept: "application/json" }, cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Artifacts could not be read.");
    if (requestToken !== state.artifactRequestToken) return;
    const incoming = Array.isArray(payload.artifacts) ? payload.artifacts : [];
    state.artifacts = append ? [...state.artifacts, ...incoming] : incoming;
    state.artifactIssues = Array.isArray(payload.issues) ? payload.issues : [];
    state.artifactsNextCursor = payload.next_cursor ?? null;
    state.artifactsLoaded = true;
  } catch (error) {
    if (requestToken !== state.artifactRequestToken) return;
    state.artifactsError = error.message || "Artifacts could not be read.";
  } finally {
    if (requestToken === state.artifactRequestToken) {
      state.artifactsLoading = false;
      if (state.activeView === "artifacts") render();
    }
  }
}

function safeGitCommitUrl(value) {
  if (typeof value !== "string" || !value || value.includes("%")) return false;
  try {
    const parsed = new URL(value);
    if (
      parsed.protocol !== "https:" || parsed.username || parsed.password ||
      parsed.port || parsed.search || parsed.hash
    ) return false;
    const host = parsed.hostname.toLowerCase();
    const parts = parsed.pathname.split("/").filter(Boolean);
    const commitId = parts.at(-1) || "";
    if (!/^[0-9a-f]{7,64}$/i.test(commitId)) return false;
    if (host === "github.com") return parts.length === 4 && parts[2] === "commit";
    if (host === "gitlab.com") {
      return parts.length >= 5 && parts.at(-3) === "-" && parts.at(-2) === "commit";
    }
    if (host === "bitbucket.org") return parts.length === 4 && parts[2] === "commits";
    return false;
  } catch (_) {
    return false;
  }
}

function safeStargraphMediaUrl(reference) {
  if (
    typeof reference !== "string" ||
    !reference.startsWith("/media/") ||
    /%2f|%5c/i.test(reference)
  ) return null;
  try {
    const resolved = new URL(reference, MEMORY_STARGRAPH_ORIGIN);
    if (
      resolved.origin !== MEMORY_STARGRAPH_ORIGIN ||
      !resolved.pathname.startsWith("/media/") ||
      resolved.search || resolved.hash
    ) return null;
    const decodedPath = decodeURIComponent(resolved.pathname);
    const decodedReferencePath = decodeURIComponent(reference);
    const hasUnsafeSegment = (path) => path
      .split("/")
      .slice(2)
      .some((segment) => !segment || segment === "." || segment === "..");
    if (
      !decodedPath.startsWith("/media/") ||
      !decodedReferencePath.startsWith("/media/") ||
      /[\\\u0000-\u001f]/.test(decodedPath) ||
      /[\\\u0000-\u001f]/.test(decodedReferencePath) ||
      hasUnsafeSegment(decodedPath) ||
      hasUnsafeSegment(decodedReferencePath)
    ) return null;
    return resolved;
  } catch (_) {
    return null;
  }
}

function renderArtifactAttachments(artifact) {
  elements.artifactDetailAttachments.replaceChildren();
  const references = Array.isArray(artifact.attachments) ? artifact.attachments : [];
  references.forEach((reference) => {
    const mediaUrl = safeStargraphMediaUrl(reference);
    if (!mediaUrl) {
      elements.artifactDetailAttachments.append(node("p", "artifact-unsupported-reference", reference));
      return;
    }
    if (/\.(?:png|jpe?g|gif|webp)$/i.test(mediaUrl.pathname)) {
      const image = document.createElement("img");
      image.src = mediaUrl.href;
      image.alt = `${artifact.title} attachment`;
      image.loading = "lazy";
      elements.artifactDetailAttachments.append(image);
      return;
    }
    if (/\.pdf$/i.test(mediaUrl.pathname)) {
      const link = node("a", "detail-link", "Open PDF");
      link.href = mediaUrl.href;
      link.target = "_blank";
      link.rel = "noreferrer";
      elements.artifactDetailAttachments.append(link);
      return;
    }
    elements.artifactDetailAttachments.append(
      node("p", "artifact-unsupported-reference", reference),
    );
  });
  if (artifact.git_url && safeGitCommitUrl(artifact.git_url)) {
    const link = node("a", "detail-link", "Open Git commit");
    link.href = artifact.git_url;
    link.target = "_blank";
    link.rel = "noreferrer";
    elements.artifactDetailAttachments.append(link);
  } else if (artifact.git_url) {
    elements.artifactDetailAttachments.append(
      node("p", "artifact-unsupported-reference", artifact.git_url),
    );
  }
}

function selectArtifact(artifactSlug, originControl = null) {
  const taskEntries = Array.from(state.taskArtifacts.values()).flatMap((entry) => entry.artifacts || []);
  const artifact = [...state.artifacts, ...taskEntries].find((item) => item.slug === artifactSlug);
  if (!artifact) return;
  const returnFocus = originControl instanceof HTMLElement
    ? originControl
    : document.activeElement instanceof HTMLElement
      ? document.activeElement
    : null;
  state.artifactTaskReturn = state.selectedKind === "task" && state.selectedSlug
    ? {
      taskSlug: state.selectedSlug,
      element: returnFocus,
      artifactSlug,
      detailReturnFocus: state.detailReturnFocus,
    }
    : null;
  if (!state.artifactTaskReturn && returnFocus) {
    state.detailReturnFocus = { element: returnFocus, slug: artifactSlug };
  }
  state.selectedSlug = artifactSlug;
  state.selectedKind = "artifact";
  prepareDetailPanelWidth("artifact");
  elements.detailPanel.setAttribute("aria-hidden", "false");
  elements.detailPanel.setAttribute("aria-label", "Artifact details");
  elements.detailEmpty.classList.add("is-hidden");
  elements.detailContent.classList.add("is-hidden");
  elements.goalDetailContent.classList.add("is-hidden");
  elements.projectDetailContent.classList.add("is-hidden");
  elements.systemTicketDetailContent.classList.add("is-hidden");
  elements.calendarEventDetail.classList.add("is-hidden");
  elements.artifactDetailContent.classList.remove("is-hidden");
  const owner = artifactAgent(artifact);
  const task = artifactTask(artifact);
  const project = state.projects.find((item) => item.slug === artifact.project);
  const goal = state.snapshot?.goals.find((item) => item.slug === artifact.goal);
  elements.artifactDetailKind.textContent = artifact.artifact_kind;
  elements.artifactDetailTitle.textContent = artifact.title;
  elements.artifactDetailMeta.textContent = "Immutable canonical Agent deliverable";
  renderSafeMarkdown(elements.artifactDetailMarkdown, artifact.markdown || "");
  renderArtifactAttachments(artifact);
  elements.artifactDetailAgent.textContent = owner.name;
  elements.artifactDetailTask.textContent = task?.title || artifact.produced_for;
  elements.artifactDetailProject.textContent = project?.title || artifact.project || "No project";
  elements.artifactDetailGoal.textContent = goal?.title || artifact.goal || "No goal";
  elements.artifactDetailCreated.textContent = new Date(artifact.created_at).toLocaleString();
  elements.artifactDetailGbrainLink.href = `http://127.0.0.1:8788/?slug=${encodeURIComponent(artifact.slug)}`;
  elements.artifactDetailSlug.textContent = artifact.slug;
  render();
  window.requestAnimationFrame(() => {
    if (window.matchMedia("(max-width: 760px)").matches) elements.detailPanel.scrollIntoView({ block: "start", behavior: "auto" });
    elements.artifactDetailTitle.focus({ preventScroll: true });
  });
}

function renderTaskArtifacts(taskSlug) {
  const entry = state.taskArtifacts.get(taskSlug);
  elements.taskArtifactList.replaceChildren();
  if (!entry || entry.loading) {
    elements.taskArtifactsState.textContent = "Reading artifacts…";
    return;
  }
  if (entry.error) {
    elements.taskArtifactsState.textContent = `Artifacts unavailable: ${entry.error}`;
    return;
  }
  if (!entry.artifacts.length) {
    elements.taskArtifactsState.textContent = "No artifacts yet";
    return;
  }
  elements.taskArtifactsState.textContent = `${entry.artifacts.length} canonical Artifact${entry.artifacts.length === 1 ? "" : "s"}`;
  entry.artifacts.forEach((artifact) => {
    const button = node("button", "task-artifact-row", artifact.title);
    button.type = "button";
    button.dataset.slug = artifact.slug;
    button.setAttribute("aria-label", `Open Artifact ${artifact.title}`);
    button.addEventListener("click", () => selectArtifact(artifact.slug, button));
    elements.taskArtifactList.append(button);
  });
}

async function loadTaskArtifacts(taskSlug) {
  state.taskArtifacts.set(taskSlug, { loading: true, error: "", artifacts: [] });
  if (state.selectedKind === "task" && state.selectedSlug === taskSlug) renderTaskArtifacts(taskSlug);
  try {
    const params = new URLSearchParams({ task: taskSlug, limit: "10", cursor: "0" });
    const response = await fetch(`/api/artifacts?${params}`, { headers: { Accept: "application/json" }, cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Artifacts could not be read.");
    state.taskArtifacts.set(taskSlug, { loading: false, error: "", artifacts: Array.isArray(payload.artifacts) ? payload.artifacts : [] });
  } catch (error) {
    state.taskArtifacts.set(taskSlug, { loading: false, error: error.message || "read failed", artifacts: [] });
  }
  if (state.selectedKind === "task" && state.selectedSlug === taskSlug) renderTaskArtifacts(taskSlug);
}

function keepSelectedCalendarTaskVisible(taskSlug) {
  if (state.activeView !== "week" || state.calendarMode !== "week") return;
  window.requestAnimationFrame(() => {
    const selected = Array.from(
      document.querySelectorAll(".week-grid .task-row-open"),
    ).find((button) => button.dataset.slug === taskSlug);
    if (selected && window.matchMedia("(max-width: 760px)").matches) {
      const day = selected.closest(".week-day");
      const grid = day?.closest(".week-grid");
      if (day && grid) {
        const maxScroll = Math.max(0, grid.scrollWidth - grid.clientWidth);
        grid.scrollLeft = Math.min(day.offsetLeft, maxScroll);
        return;
      }
    }
    selected?.scrollIntoView({ block: "nearest", inline: "nearest" });
  });
}

function selectTask(slug, taskFallback = null, returnFocus = null) {
  const task = findTaskBySlug(slug) || taskFallback;
  if (!task) return;
  state.detailReturnFocus = returnFocus
    ? { element: returnFocus, slug }
    : null;
  state.selectedSlug = slug;
  state.selectedKind = "task";
  prepareDetailPanelWidth("task");
  state.showCompletedTodos = false;
  setTodoAddOpen(false, { focus: false });
  elements.detailPanel.setAttribute("aria-hidden", "false");
  elements.detailPanel.setAttribute("aria-label", "Task details");
  elements.detailEmpty.classList.add("is-hidden");
  elements.detailContent.classList.remove("is-hidden");
  elements.artifactDetailContent.classList.add("is-hidden");
  elements.goalDetailContent.classList.add("is-hidden");
  elements.projectDetailContent.classList.add("is-hidden");
  elements.systemTicketDetailContent.classList.add("is-hidden");
  elements.calendarEventDetail.classList.add("is-hidden");
  elements.taskDetailStatus.textContent = taskUiStatus(task) === "active" ? "In Progress" : taskUiStatus(task);
  const isProposed = task.status === "proposed";
  elements.taskApproveButton.classList.toggle("is-hidden", !isProposed);
  elements.taskRejectButton.classList.toggle("is-hidden", !isProposed);
  elements.taskDuplicateButton.classList.toggle("is-hidden", isProposed);
  elements.detailTitle.textContent = task.title || task.summary;
  renderSafeMarkdown(elements.detailCopy, task.detail || "");
  const isProposal = Boolean(
    task.status === "proposed" || task.proposal_submitted_at || task.decision ||
    (Array.isArray(task.proposal_decision_events) && task.proposal_decision_events.length),
  );
  elements.proposalDetailMeta.classList.toggle("is-hidden", !isProposal);
  elements.proposalDecisionHistory.classList.toggle("is-hidden", !isProposal);
  if (isProposal) {
    const ownerName = task.owner?.name || state.agents.find((agent) => agent.slug === task.owner_agent)?.name || task.owner_agent || "Unknown agent";
    const target = state.snapshot.goals.find((goal) => goal.slug === task.goal)?.title || task.goal || "No linked primary goal";
    const submitted = task.proposal_submitted_at || task.created_at;
    const proposalState = proposalStateLabel(task);
    elements.proposalDetailMeta.textContent = `${proposalState} · Proposed by ${ownerName} · Goal: ${target}${submitted ? ` · Submitted ${new Date(submitted).toLocaleString()}` : ""}${task.updated_at ? ` · Updated ${new Date(task.updated_at).toLocaleString()}` : ""}`;
    renderProposalDecisionTimeline(task);
  } else elements.proposalDetailMeta.textContent = "";
  const owner = task.owner || (
    task.owner_agent
      ? {
        name:
          state.agents.find((agent) => agent.slug === task.owner_agent)?.name ||
          task.owner_agent,
        avatar:
          state.agents.find((agent) => agent.slug === task.owner_agent)?.avatar ||
          { value: "A" },
      }
      : null
  );
  elements.taskOwner.classList.toggle("is-hidden", !owner);
  if (owner) {
    setCompactAgentAvatar(elements.taskOwnerAvatar, owner);
    elements.taskOwnerName.textContent = owner.name;
  }
  elements.taskTodoError.classList.add("is-hidden");
  renderTaskTodos(task);
  renderTaskArtifacts(task.slug);
  if (state.artifactTaskReturn?.taskSlug !== task.slug) {
    void loadTaskArtifacts(task.slug);
  }
  elements.detailPriority.textContent = task.priority;
  elements.detailDue.textContent = formatDay(task.due_day, "long");
  const metric = task.progress_metric;
  elements.taskProgressDetail.classList.toggle("is-hidden", !metric);
  if (metric) {
    const metricLabel =
      metric.label ||
      (metric.unit === "job_application" ? "Job applications" : metric.unit);
    const percent = Math.min(100, Math.round((metric.current / metric.target) * 100));
    elements.taskProgressLabel.textContent = metricLabel;
    elements.taskProgressValue.textContent = `${metric.current} / ${metric.target}`;
    elements.taskProgressBar.style.width = `${percent}%`;
    elements.taskProgressBinding.textContent =
      metric.event_binding === "job_applied"
        ? "Updated by distinct verified job-applied events. At 5 / 5, Mission Control completes this task after canonical readback."
        : "Manual count metric. Reaching the target does not automatically change task status.";
  }
  elements.detailGbrainLink.href = `http://127.0.0.1:8788/?slug=${encodeURIComponent(task.slug)}`;
  elements.detailSlug.textContent = task.slug;
  const project = state.projects.find((item) => item.slug === task.project);
  elements.taskProjectValue.textContent = project?.title || (task.project ? task.project : "No project");
  const linkedGoal = state.snapshot.goals.find((goal) => goal.slug === task.goal);
  if (linkedGoal) {
    elements.taskGoalNav.textContent = linkedGoal.title;
    elements.taskGoalNav.classList.remove("is-hidden");
    elements.taskGoalNav.onclick = () => selectGoal(linkedGoal.slug);
    elements.taskGoalValue.textContent = "";
  } else {
    elements.taskGoalNav.classList.add("is-hidden");
    elements.taskGoalNav.onclick = null;
    elements.taskGoalValue.textContent = task.goal ? task.goal : "No associated goal";
  }
  render();
  keepSelectedCalendarTaskVisible(task.slug);
  window.requestAnimationFrame(() => {
    if (window.matchMedia("(max-width: 760px)").matches) {
      elements.detailPanel.scrollIntoView({ block: "start", behavior: "auto" });
    }
    elements.detailTitle.focus({ preventScroll: true });
  });
}

function goalTaskLinks(container, tasks, emptyCopy) {
  container.replaceChildren();
  if (!tasks.length) {
    container.append(node("div", "goal-task-empty", emptyCopy));
    return;
  }
  tasks.forEach((task) => {
    const button = node("button", "goal-task-link");
    button.type = "button";
    button.append(
      node("span", "", task.title || task.summary),
      node("span", "", relativeDue(task).label),
    );
    button.addEventListener("click", () => selectTask(task.slug));
    container.append(button);
  });
}

function renderGoalRelationshipTasks(goal, reciprocalTaskSlugs) {
  const reciprocal = new Set(reciprocalTaskSlugs);
  const explicitTasks = state.snapshot.tasks.filter((task) =>
    reciprocal.has(task.slug));
  const legacyOneWayTasks = state.snapshot.tasks.filter(
    (task) => task.goal === goal.slug && !reciprocal.has(task.slug));
  const linked = [];
  const seen = new Set();
  [...explicitTasks, ...legacyOneWayTasks].forEach((task) => {
    if (!seen.has(task.slug)) {
      seen.add(task.slug);
      linked.push(task);
    }
  });
  const active = linked.filter(
    (task) => !["completed", "cancelled"].includes(task.status));
  const completed = linked.filter((task) => task.status === "completed");
  const percent = linked.length
    ? Math.round((completed.length / linked.length) * 100)
    : 0;
  elements.goalProgressLabel.textContent = `${percent}% task completion`;
  elements.goalProgressCount.textContent =
    `${completed.length} completed · ${active.length} active`;
  elements.goalProgressBar.style.width = `${percent}%`;
  goalTaskLinks(
    elements.goalActiveTasks,
    active,
    "No active task advances this goal yet.",
  );
  goalTaskLinks(
    elements.goalCompletedTasks,
    completed,
    "No completed task advances this goal yet.",
  );
  goal.legacy_one_way_tasks = legacyOneWayTasks;
  if (legacyOneWayTasks.length) {
    const count = legacyOneWayTasks.length;
    elements.goalRelationshipNotice.textContent =
      `${count} legacy one-way task link${count === 1 ? "" : "s"} remains visible. ` +
      "Open a task below, choose Edit, and save its current goal to repair both relationship directions.";
    elements.goalRelationshipNotice.classList.remove("is-hidden");
  } else {
    elements.goalRelationshipNotice.classList.add("is-hidden");
  }
}

async function hydrateGoalRelationships(goal) {
  elements.goalRelationshipNotice.textContent =
    "Reading explicit reciprocal task links from GBrain…";
  elements.goalRelationshipNotice.classList.remove("is-hidden");
  try {
    const response = await fetch(
      `/api/goals/${encodeURIComponent(goal.slug)}/relationships`,
      {
        headers: { Accept: "application/json" },
        cache: "no-store",
      },
    );
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || "Goal relationships could not be read.");
    }
    if (state.selectedKind !== "goal" || state.selectedSlug !== goal.slug) return;
    renderGoalRelationshipTasks(goal, result.task_slugs);
  } catch (error) {
    if (state.selectedKind !== "goal" || state.selectedSlug !== goal.slug) return;
    elements.goalRelationshipNotice.textContent =
      `Could not verify reciprocal goal links. ${error.message}`;
    elements.goalRelationshipNotice.classList.remove("is-hidden");
  }
}

function selectGoal(slug, returnFocus = undefined) {
  const goal = state.snapshot?.goals.find((item) => item.slug === slug);
  if (!goal) return;
  if (returnFocus !== undefined) {
    state.detailReturnFocus = returnFocus
      ? { element: returnFocus, slug }
      : null;
  }
  state.selectedSlug = slug;
  state.selectedKind = "goal";
  prepareDetailPanelWidth("goal");
  elements.detailPanel.setAttribute("aria-hidden", "false");
  elements.detailPanel.setAttribute("aria-label", "Goal details");
  elements.detailEmpty.classList.add("is-hidden");
  elements.detailContent.classList.add("is-hidden");
  elements.artifactDetailContent.classList.add("is-hidden");
  elements.goalDetailContent.classList.remove("is-hidden");
  elements.projectDetailContent.classList.add("is-hidden");
  elements.systemTicketDetailContent.classList.add("is-hidden");
  elements.calendarEventDetail.classList.add("is-hidden");
  elements.goalDetailStatus.textContent = goal.status;
  elements.goalPauseButton.disabled = goal.status === "paused";
  elements.goalPauseButton.textContent =
    goal.status === "paused" ? "Paused" : "Pause";
  elements.goalEditButton.disabled = false;
  elements.goalActionError.classList.add("is-hidden");
  elements.goalDetailTitle.textContent = goal.title;
  renderSafeMarkdown(elements.goalDetailOutcome, goal.outcome || "");
  const defaultAgent = state.agents.find((agent) =>
    agent.default_goal_slugs.includes(goal.slug));
  elements.goalDefaultAgent.classList.toggle("is-hidden", !defaultAgent);
  if (defaultAgent) {
    setCompactAgentAvatar(elements.goalDefaultAgentAvatar, defaultAgent);
    elements.goalDefaultAgentName.textContent = defaultAgent.name;
    elements.goalDefaultAgentLink.href =
      `http://127.0.0.1:8788/?slug=${encodeURIComponent(defaultAgent.slug)}`;
  }
  elements.goalDetailTarget.textContent = formatDay(goal.target_day, "long");
  elements.goalDetailCadence.textContent = goal.review_cadence;
  elements.goalDetailSuccess.textContent = goal.success_criteria;
  elements.goalProgressLabel.textContent = `${goal.progress.percent}% task completion`;
  elements.goalProgressCount.textContent =
    `${goal.progress.completed} completed · ${goal.progress.active} active`;
  elements.goalProgressBar.style.width = `${goal.progress.percent}%`;
  goalTaskLinks(
    elements.goalActiveTasks,
    goal.active_tasks,
    "No active task advances this goal yet.",
  );
  goalTaskLinks(
    elements.goalCompletedTasks,
    goal.completed_tasks,
    "No completed task advances this goal yet.",
  );
  elements.goalGbrainLink.href =
    `http://127.0.0.1:8788/?slug=${encodeURIComponent(goal.slug)}`;
  elements.goalDetailSlug.textContent = goal.slug;
  render();
  hydrateGoalRelationships(goal);
  window.requestAnimationFrame(() => {
    if (window.matchMedia("(max-width: 760px)").matches) {
      elements.detailPanel.scrollIntoView({ block: "start", behavior: "auto" });
    }
    elements.goalDetailTitle.focus({ preventScroll: true });
  });
}

function closeDetails() {
  const artifactReturn = state.selectedKind === "artifact"
    ? state.artifactTaskReturn
    : null;
  if (artifactReturn) {
    selectTask(artifactReturn.taskSlug);
    state.detailReturnFocus = artifactReturn.detailReturnFocus;
    state.artifactTaskReturn = null;
    window.requestAnimationFrame(() => {
      const target = artifactReturn.element?.isConnected
        ? artifactReturn.element
        : Array.from(document.querySelectorAll(".task-artifact-row")).find(
          (candidate) => candidate.dataset.slug === artifactReturn.artifactSlug,
        );
      (target || elements.detailTitle)?.focus({ preventScroll: true });
    });
    return;
  }
  const returnFocus = state.detailReturnFocus;
  state.artifactTaskReturn = null;
  state.detailReturnFocus = null;
  state.selectedSlug = null;
  state.selectedKind = null;
  elements.detailPanel.setAttribute("aria-hidden", "true");
  elements.detailContent.classList.add("is-hidden");
  elements.artifactDetailContent.classList.add("is-hidden");
  elements.goalDetailContent.classList.add("is-hidden");
  elements.projectDetailContent.classList.add("is-hidden");
  elements.systemTicketDetailContent.classList.add("is-hidden");
  elements.calendarEventDetail.classList.add("is-hidden");
  elements.detailEmpty.classList.remove("is-hidden");
  render();
  if (returnFocus) {
    window.requestAnimationFrame(() => {
      const target = returnFocus.element?.isConnected
        ? returnFocus.element
        : [
          ...document.querySelectorAll(".proposal-card"),
          ...document.querySelectorAll(".task-row-open"),
          ...document.querySelectorAll(".project-card-open"),
          ...document.querySelectorAll(".goal-card"),
          ...document.querySelectorAll(".artifact-card"),
          ...document.querySelectorAll(".system-ticket-card"),
          ...document.querySelectorAll(".ical-event"),
        ].find(
          (candidate) => candidate.dataset.slug === returnFocus.slug,
        );
      target?.focus({ preventScroll: true });
    });
  }
}

async function saveTaskGoal() {
  if (state.selectedKind !== "task" || !state.selectedSlug) return;
  const taskSlug = state.selectedSlug;
  const goalSlug = elements.taskGoalSelect.value || null;
  elements.taskGoalError.classList.add("is-hidden");
  elements.taskGoalSave.disabled = true;
  elements.taskGoalSave.textContent = "Saving…";
  try {
    const response = await fetch(
      `/api/tasks/${encodeURIComponent(taskSlug)}/goal`,
      {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({ goal_slug: goalSlug }),
      },
    );
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Goal link could not be saved.");
    showToast(goalSlug ? "Goal relationship saved in GBrain." : "Goal relationship cleared.");
    await loadTasks();
    selectTask(taskSlug);
  } catch (error) {
    elements.taskGoalError.textContent = error.message;
    elements.taskGoalError.classList.remove("is-hidden");
  } finally {
    elements.taskGoalSave.disabled = false;
    elements.taskGoalSave.textContent = "Save";
  }
}

async function requestTaskStatus(taskSlug, status) {
  const response = await fetch(
    `/api/tasks/${encodeURIComponent(taskSlug)}/status`,
    {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({ status }),
    },
  );
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(result.error || "Task status could not be saved.");
    error.code = result.code;
    error.slug = result.slug;
    throw error;
  }
  if (!result.receipt?.verified || !result.receipt?.task) {
    const error = new Error(
      "Status write did not include verified canonical task readback.",
    );
    error.code = "ambiguous_readback";
    error.slug = taskSlug;
    throw error;
  }
  return result.receipt;
}

function statusErrorMessage(error) {
  if (error.code === "partial_write" && error.slug) {
    return `${error.message} Do not retry yet; inspect ${error.slug} first.`;
  }
  return error.message;
}

async function moveBoardTask(taskSlug, status) {
  const task = findTaskBySlug(taskSlug);
  const definition = boardColumns.find((column) => column.status === status);
  if (!task || !definition || state.boardMove?.phase === "saving") return;
  if (task.status === status) return;
  const move = {
    taskSlug,
    taskTitle: task.title || task.summary,
    status,
    statusLabel: definition.title,
    phase: "saving",
  };
  state.boardMove = move;
  render();
  try {
    const receipt = await requestTaskStatus(taskSlug, status);
    reconcileVerifiedTask(receipt.task);
    state.boardMove = null;
    render();
    if (state.selectedKind === "task" && state.selectedSlug === taskSlug) {
      selectTask(taskSlug);
    }
    showToast(`${move.taskTitle} saved as ${move.statusLabel} in GBrain.`);
  } catch (error) {
    state.boardMove = {
      ...move,
      phase: "error",
      message: (
        `${move.taskTitle} could not be reconciled with GBrain. ` +
        `${statusErrorMessage(error)} Use Refresh before retrying.`
      ),
    };
    render();
  }
}

async function saveTaskStatus() {
  if (state.selectedKind !== "task" || !state.selectedSlug) return;
  const taskSlug = state.selectedSlug;
  const status = elements.taskStatusSelect.value;
  elements.taskStatusError.classList.add("is-hidden");
  elements.taskStatusSave.disabled = true;
  elements.taskStatusSave.textContent = "Saving…";
  try {
    const currentTask = findTaskBySlug(taskSlug);
    if (currentTask?.status === status) return;
    const receipt = await requestTaskStatus(taskSlug, status);
    reconcileVerifiedTask(receipt.task);
    selectTask(taskSlug);
    showToast(`Status saved as ${status} in GBrain.`);
  } catch (error) {
    elements.taskStatusError.textContent = statusErrorMessage(error);
    elements.taskStatusError.classList.remove("is-hidden");
  } finally {
    elements.taskStatusSave.textContent = "Save";
    elements.taskStatusSave.disabled =
      elements.taskStatusSelect.value ===
      elements.taskStatusSelect.dataset.currentStatus;
  }
}

function setView(view) {
  if (!viewMeta[view]) return;
  state.activeView = view;
  render();
  if (
    (
      view === "agent-work" ||
      view === "today" || view === "blocked" ||
      (view === "board" && state.showAgentTasks)
    ) &&
    !state.agentWorkLoaded
  ) {
    void loadAgentWork();
  }
  if (view === "projects" && !state.projectsLoaded && !state.projectsLoading) {
    void loadProjects();
  }
  if (view === "all" && !state.projectsLoaded && !state.projectsLoading) {
    void loadProjects();
  }
  if (view === "inbox" && !state.proposalsLoaded && !state.proposalsLoading) {
    void loadProposals();
  }
  if (view === "artifacts" && !state.artifactsLoaded && !state.artifactsLoading) {
    void loadArtifacts({ reset: true });
  }
  if (view === "artifacts" && !state.agentsLoaded && !state.agentsLoading) {
    void loadAgents();
  }
  if (view === "artifacts" && !state.projectsLoaded && !state.projectsLoading) {
    void loadProjects();
  }
  if (view === "agent-work" && !state.agentsLoaded && !state.agentsLoading) {
    void loadAgents();
  }
  if (view === "week" && !state.icalConnectionLoaded && !state.icalConnectionLoading) {
    void loadCalendarConnectionState();
  }
}

function setConnection(status, label) {
  elements.storeDot.classList.toggle("is-connected", status === "connected");
  elements.storeDot.classList.toggle("is-error", status === "error");
  elements.storeLabel.textContent = label;
}

function showLoadError(error) {
  const wrapper = node("section", "simple-empty");
  const content = node("div");
  content.append(
    node("span", "simple-empty-mark", "!"),
    node("h2", "", "GBrain is out of reach"),
    node(
      "p",
      "",
      error.message || "Mission Control could not read the approved task collections.",
    ),
  );
  const retry = node("button", "submit-button", "Try again");
  retry.type = "button";
  retry.addEventListener("click", loadTasks);
  content.append(retry);
  wrapper.append(content);
  elements.viewSurface.replaceChildren(wrapper);
}

function formatSyncTime(timestamp) {
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(timestamp));
}

function updateAutoRefreshLabel(copy = "") {
  if (copy) {
    elements.autoRefreshLabel.textContent = copy;
    return;
  }
  const suffix = state.lastSyncedAt
    ? ` · last sync ${formatSyncTime(state.lastSyncedAt)}`
    : "";
  elements.autoRefreshLabel.textContent =
    `Auto-refresh every ${AUTO_REFRESH_MINUTES} minutes${suffix}`;
}

function clearAutoRefreshTimer() {
  if (state.autoRefreshTimer !== null) {
    window.clearTimeout(state.autoRefreshTimer);
    state.autoRefreshTimer = null;
  }
}

function scheduleAutoRefresh({ reset = false } = {}) {
  clearAutoRefreshTimer();
  if (reset || state.autoRefreshDueAt === null) {
    state.autoRefreshDueAt = Date.now() + AUTO_REFRESH_INTERVAL_MS;
  }
  if (document.hidden) {
    updateAutoRefreshLabel(
      `Auto-refresh every ${AUTO_REFRESH_MINUTES} minutes · paused while hidden`,
    );
    return;
  }
  updateAutoRefreshLabel();
  const delay = Math.max(0, state.autoRefreshDueAt - Date.now());
  state.autoRefreshTimer = window.setTimeout(async () => {
    state.autoRefreshTimer = null;
    if (document.hidden) {
      state.refreshDeferred = true;
      updateAutoRefreshLabel(
        `Auto-refresh every ${AUTO_REFRESH_MINUTES} minutes · refresh deferred`,
      );
      return;
    }
    await loadTasks({ reason: "automatic" });
  }, delay);
}

async function performTaskLoad(reason) {
  const previousSnapshot = state.snapshot;
  const hasVerifiedSnapshot = Boolean(previousSnapshot);
  state.loading = !hasVerifiedSnapshot;
  if (!hasVerifiedSnapshot) {
    elements.syncLabel.textContent = "Reading GBrain…";
    elements.refreshButton.disabled = true;
    setConnection("loading", "Connecting");
    render();
  } else if (reason === "manual") {
    elements.syncLabel.textContent = "Refreshing verified data…";
    elements.refreshButton.disabled = true;
  }
  try {
    const response = await fetch(
      ["initial", "poll"].includes(reason) ? "/api/tasks" : "/api/tasks?refresh=1",
      {
      headers: { Accept: "application/json" },
      cache: "no-store",
      },
    );
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to read GBrain.");
    state.tasksReadState = payload.read_state || null;
    if (response.status === 202) scheduleSurfacePoll("tasks");
    if (response.status === 200 && Array.isArray(payload.tasks)) {
      state.snapshot = payload;
      state.lastSyncedAt = payload.read_state?.last_valid_at
        ? payload.read_state.last_valid_at * 1000
        : Date.now();
    }
    state.refreshDeferred = false;
    if (state.snapshot) {
      setConnection("connected", "GBrain connected");
      const suffix = payload.read_state?.refreshing
        ? " · refreshing in background"
        : payload.read_state?.error
          ? " · refresh delayed; last verified data kept"
          : "";
      elements.syncLabel.textContent =
        `Synced ${state.snapshot.tasks.length} task${state.snapshot.tasks.length === 1 ? "" : "s"} ` +
        `at ${formatSyncTime(state.lastSyncedAt)}${suffix}`;
      scheduleAutoRefresh({ reset: !payload.read_state?.refreshing });
    } else {
      setConnection(
        payload.read_state?.status === "error" ? "error" : "loading",
        payload.read_state?.status === "error" ? "GBrain refresh delayed" : "Connecting",
      );
      elements.syncLabel.textContent = payload.read_state?.error || "Reading GBrain in the background…";
    }
    if (payload.read_state?.refreshing) scheduleSurfacePoll("tasks");
  } catch (error) {
    if (previousSnapshot) {
      state.snapshot = previousSnapshot;
      setConnection("connected", "GBrain connected");
      elements.syncLabel.textContent =
        reason === "automatic" ? "Auto-refresh delayed" : "Refresh delayed";
      updateAutoRefreshLabel(
        `Auto-refresh every ${AUTO_REFRESH_MINUTES} minutes · last data kept`,
      );
      state.autoRefreshDueAt = Date.now() + AUTO_REFRESH_INTERVAL_MS;
      scheduleAutoRefresh();
    } else {
      state.snapshot = null;
      state.tasksReadState = {
        surface: "tasks",
        status: "error",
        refreshing: false,
        stale: false,
        error: error.message || "Unable to read GBrain.",
      };
      setConnection("error", "GBrain unavailable");
      elements.syncLabel.textContent = "Read failed";
    }
  } finally {
    state.loading = false;
    elements.refreshButton.disabled = false;
    render();
  }
}

function loadTasks({ reason = "manual" } = {}) {
  if (state.tasksLoadPromise) return state.tasksLoadPromise;
  state.tasksLoadPromise = performTaskLoad(reason).finally(() => {
    state.tasksLoadPromise = null;
  });
  return state.tasksLoadPromise;
}

function populateTaskEditorRelationships(task = null) {
  elements.taskEditorProject.replaceChildren();
  const noProject = node("option", "", "No project");
  noProject.value = "";
  elements.taskEditorProject.append(noProject);
  state.projects.forEach((project) => {
    const option = node("option", "", project.title);
    option.value = project.slug;
    elements.taskEditorProject.append(option);
  });
  elements.taskEditorProject.value = task?.project || "";

  elements.taskEditorGoal.replaceChildren();
  const noGoal = node("option", "", "No linked goal");
  noGoal.value = "";
  elements.taskEditorGoal.append(noGoal);
  state.snapshot?.goals
    .filter((goal) => ["planned", "active"].includes(goal.status) || goal.slug === task?.goal)
    .forEach((goal) => {
      const option = node("option", "", goal.title);
      option.value = goal.slug;
      elements.taskEditorGoal.append(option);
    });
  elements.taskEditorGoal.value = task?.goal || "";
}

function populateTaskEditorAssignees(selected = "tony") {
  elements.taskEditorAssignee.replaceChildren();
  const personal = node("option", "", "Tony — personal task");
  personal.value = "tony";
  elements.taskEditorAssignee.append(personal);
  state.agents.forEach((agent) => {
    const option = node("option", "", `${agent.name} — agent work`);
    option.value = agent.slug;
    elements.taskEditorAssignee.append(option);
  });
  elements.taskEditorAssignee.value =
    [...elements.taskEditorAssignee.options].some((option) => option.value === selected)
      ? selected
      : "tony";
}

function updateTaskMetricPreview() {
  const enabled = elements.taskTrackMetric.checked;
  elements.taskMetricFields.classList.toggle("is-hidden", !enabled);
  if (!enabled) return;
  const label = elements.taskMetricLabel.value.trim();
  const target = Number(elements.taskMetricTarget.value);
  const current = Number(elements.taskMetricCurrent.value || 0);
  const binding = elements.taskMetricEventBinding.value;
  elements.taskMetricBindingCopy.textContent =
    binding === "job_applied"
      ? "Your current value is saved as the starting baseline. Each distinct verified job-applied queue event increments progress by 1, and reaching the target completes the task after GBrain readback."
      : "Manual metrics never change task status just because current equals target.";
  elements.taskMetricPreview.textContent =
    label && Number.isInteger(target) && target > 0 && Number.isInteger(current)
      ? `${label}: ${current} / ${target}`
      : "Add a name and target to preview progress.";
}

function resetTaskEditorMetric(metric = null) {
  elements.taskTrackMetric.checked = Boolean(metric);
  elements.taskMetricLabel.value = metric?.label ||
    (metric?.unit === "job_application" ? "Job applications" : "");
  elements.taskMetricTarget.value = metric?.target || "";
  elements.taskMetricCurrent.value = metric ? String(metric.current) : "0";
  elements.taskMetricEventBinding.value = metric?.event_binding || "";
  updateTaskMetricPreview();
}

async function loadTaskEditorReferenceData() {
  const loads = [];
  if (!state.projectsLoaded && !state.projectsLoading) loads.push(loadProjects());
  if (!state.agentsLoaded && !state.agentsLoading) loads.push(loadAgents());
  await Promise.all(loads);
}

async function openCreateTask() {
  await loadTaskEditorReferenceData();
  state.taskEditorMode = "create";
  state.taskEditorSourceSlug = null;
  elements.taskEditorForm.reset();
  elements.taskEditorMode.textContent = "New canonical task";
  elements.taskEditorHeading.textContent = "Create Task";
  elements.taskEditorSubmit.textContent = "Create Task";
  elements.taskEditorSafety.textContent =
    "Mission Control reports success only after exact GBrain page and relationship readback.";
  elements.taskEditorDue.value = state.snapshot?.as_of || "";
  elements.taskEditorPriority.value = "normal";
  elements.taskEditorInitialTodoField.classList.remove("is-hidden");
  elements.taskEditorInitialTodo.classList.remove("is-hidden");
  elements.taskEditorInitialTodo.value = "";
  populateTaskEditorAssignees();
  elements.taskEditorAssigneeField.classList.remove("is-hidden");
  elements.taskEditorStatusField.classList.add("is-hidden");
  elements.taskEditorHandoffField.classList.add("is-hidden");
  elements.taskEditorHandoffReason.classList.add("is-hidden");
  populateTaskEditorRelationships();
  resetTaskEditorMetric();
  elements.taskEditorError.classList.add("is-hidden");
  elements.taskEditorDialog.showModal();
  window.setTimeout(() => elements.taskEditorTitle.focus(), 0);
}

async function openDuplicateTask() {
  if (state.selectedKind !== "task" || !state.selectedSlug) return;
  const task = state.snapshot?.tasks.find((item) => item.slug === state.selectedSlug);
  if (!task) return;
  await loadTaskEditorReferenceData();
  state.taskEditorMode = "duplicate";
  state.taskEditorSourceSlug = task.slug;
  elements.taskEditorForm.reset();
  elements.taskEditorMode.textContent = "Review a safe copy";
  elements.taskEditorHeading.textContent = "Duplicate";
  elements.taskEditorSubmit.textContent = "Create Duplicate";
  elements.taskEditorSafety.textContent =
    "The copy starts Planned with no completion time, prior progress, evidence, or event receipts.";
  elements.taskEditorTitle.value = task.title || task.summary;
  elements.taskEditorDetail.value = task.detail || "";
  elements.taskEditorPriority.value = task.priority;
  elements.taskEditorInitialTodoField.classList.remove("is-hidden");
  elements.taskEditorInitialTodo.classList.remove("is-hidden");
  elements.taskEditorInitialTodo.value = (task.todos || []).find(
    (todo) => todo.status === "not_done",
  )?.text || "";
  populateTaskEditorAssignees(task.owner_agent || "tony");
  elements.taskEditorAssigneeField.classList.remove("is-hidden");
  elements.taskEditorStatusField.classList.add("is-hidden");
  elements.taskEditorHandoffField.classList.add("is-hidden");
  elements.taskEditorHandoffReason.classList.add("is-hidden");
  elements.taskEditorDue.value = dayAfter(state.snapshot?.as_of);
  populateTaskEditorRelationships(task);
  resetTaskEditorMetric(task.progress_metric);
  if (task.progress_metric) {
    elements.taskMetricCurrent.value = "0";
    updateTaskMetricPreview();
  }
  elements.taskEditorError.classList.add("is-hidden");
  elements.taskEditorDialog.showModal();
  window.setTimeout(() => elements.taskEditorTitle.focus(), 0);
}

async function openEditTask() {
  if (state.selectedKind !== "task" || !state.selectedSlug) return;
  const task = findTaskBySlug(state.selectedSlug);
  if (!task) return;
  await loadTaskEditorReferenceData();
  state.taskEditorMode = "edit";
  state.taskEditorSourceSlug = task.slug;
  elements.taskEditorForm.reset();
  elements.taskEditorMode.textContent = "Review and save one canonical change";
  elements.taskEditorHeading.textContent = "Edit Task";
  elements.taskEditorSubmit.textContent = "Save changes";
  elements.taskEditorSaveApprove.classList.toggle("is-hidden", task.status !== "proposed");
  elements.taskEditorSafety.textContent = "Every saved field and typed relationship is read back from GBrain before Mission Control reports success.";
  elements.taskEditorTitle.value = task.title || task.summary;
  elements.taskEditorDetail.value = task.detail || "";
  elements.taskEditorPriority.value = task.priority;
  const isProposed = task.status === "proposed";
  elements.taskEditorStatus.value = isProposed ? "planned" : taskUiStatus(task);
  elements.taskEditorStatusField.classList.toggle("is-hidden", isProposed);
  elements.taskEditorDue.value = task.due_day || "";
  elements.taskEditorInitialTodoField.classList.add("is-hidden");
  elements.taskEditorInitialTodo.classList.add("is-hidden");
  elements.taskEditorInitialTodo.value = "";
  elements.taskEditorAssigneeField.classList.toggle("is-hidden", isProposed);
  populateTaskEditorAssignees(task.owner_agent || "tony");
  elements.taskEditorHandoffField.classList.toggle("is-hidden", isProposed);
  elements.taskEditorHandoffReason.classList.toggle("is-hidden", isProposed);
  elements.taskEditorHandoffReason.value = "";
  populateTaskEditorRelationships(task);
  resetTaskEditorMetric(task.progress_metric);
  elements.taskEditorError.classList.add("is-hidden");
  elements.taskEditorDialog.showModal();
  window.setTimeout(() => elements.taskEditorTitle.focus(), 0);
}

function taskEditorMetricPayload() {
  if (!elements.taskTrackMetric.checked) return null;
  const label = elements.taskMetricLabel.value.trim();
  const target = Number(elements.taskMetricTarget.value);
  const current = Number(elements.taskMetricCurrent.value);
  const eventBinding = elements.taskMetricEventBinding.value || null;
  if (!label) throw new Error("Metric name is required.");
  if (!Number.isInteger(target) || target <= 0) {
    throw new Error("Metric target must be a whole number greater than 0.");
  }
  if (!Number.isInteger(current) || current < 0 || current > target) {
    throw new Error("Current progress must be a whole number from 0 through the target.");
  }
  return {
    kind: "count",
    label,
    target,
    current,
    event_binding: eventBinding,
    auto_complete: eventBinding === "job_applied",
  };
}

async function submitTaskEditor(event) {
  event.preventDefault();
  elements.taskEditorError.classList.add("is-hidden");
  elements.taskEditorSubmit.disabled = true;
  const originalLabel = state.taskEditorMode === "duplicate" ? "Create Duplicate" : state.taskEditorMode === "edit" ? "Save changes" : "Create Task";
  elements.taskEditorSubmit.textContent = "Saving in GBrain…";
  const taskStatus = state.taskEditorMode === "edit"
    ? "Saving Task changes in GBrain…"
    : state.taskEditorMode === "duplicate"
      ? "Creating duplicate Task in GBrain…"
      : "Creating Task in GBrain…";
  try {
    const payload = {
      title: elements.taskEditorTitle.value,
      detail: elements.taskEditorDetail.value,
      priority: elements.taskEditorPriority.value,
      due_day: elements.taskEditorDue.value,
      project_slug: elements.taskEditorProject.value || null,
      goal_slug: elements.taskEditorGoal.value || null,
      progress_metric: taskEditorMetricPayload(),
      ...(state.taskEditorMode === "create"
        || state.taskEditorMode === "duplicate"
        ? {
          assignee_slug: elements.taskEditorAssignee.value,
          initial_todo: elements.taskEditorInitialTodo.value,
        }
        : {}),
      ...(state.taskEditorMode === "edit" ? {
        status: findTaskBySlug(state.taskEditorSourceSlug)?.status === "proposed" ? "proposed" : elements.taskEditorStatus.value,
        assignee_slug: elements.taskEditorAssignee.value,
        handoff_reason: elements.taskEditorHandoffReason.value,
      } : {}),
    };
    if (
      state.taskEditorMode === "edit" && payload.progress_metric &&
      payload.progress_metric.current >= payload.progress_metric.target &&
      !["completed", "cancelled"].includes(payload.status)
    ) {
      if (!window.confirm("This metric has reached its target. Mark the task completed when saving?")) {
        return;
      }
      payload.complete_when_target_reached = true;
    }
    const endpoint = state.taskEditorMode === "edit"
      ? `/api/tasks/${encodeURIComponent(state.taskEditorSourceSlug)}`
      : state.taskEditorMode === "duplicate"
        ? `/api/tasks/${encodeURIComponent(state.taskEditorSourceSlug)}/duplicate`
        : "/api/tasks";
    showMutationStatus(taskStatus, "pending", { persistent: true });
    const response = await fetch(endpoint, {
      method: state.taskEditorMode === "edit" ? "PATCH" : "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    const savedTask = result.task || result.receipt?.task;
    if (!response.ok || !result.receipt?.verified || !savedTask) {
      const error = new Error(
        result.error || "Task creation did not include verified GBrain readback.",
      );
      error.code = result.code;
      error.slug = result.slug;
      throw error;
    }
    elements.taskEditorDialog.close();
    if (savedTask.owner_agent) {
      await Promise.all([
        loadAgentWork(),
        ...(state.taskEditorMode === "edit" ? [loadTasks()] : []),
      ]);
      setAgentTasksVisible(true);
      state.activeView = "board";
      render();
      selectTask(savedTask.slug);
      const agent = state.agents.find(
        (candidate) => candidate.slug === savedTask.owner_agent,
      );
      showMutationStatus(
        state.taskEditorMode === "edit"
          ? `Saved “${savedTask.title}” and verified its assignment in GBrain.`
          : `Created queued work for ${agent?.name || "the selected agent"} and verified its assignment in GBrain.`,
        "success",
      );
    } else {
      showMutationStatus(
        state.taskEditorMode === "duplicate"
          ? `Created a clean copy of “${savedTask.title}” in GBrain.`
          : state.taskEditorMode === "edit"
            ? `Saved “${savedTask.title}” and verified it in GBrain.`
            : `Created “${savedTask.title}” in GBrain.`,
        "success",
      );
      await Promise.all([
        loadTasks(),
        ...(state.taskEditorMode === "edit" ? [loadAgentWork()] : []),
      ]);
      selectTask(savedTask.slug);
    }
  } catch (error) {
    elements.taskEditorError.textContent =
      error.code === "partial_write" && error.slug
        ? `${error.message} Do not retry yet; inspect ${error.slug} first.`
        : error.message;
    elements.taskEditorError.classList.remove("is-hidden");
    showMutationStatus(elements.taskEditorError.textContent, "error");
  } finally {
    elements.taskEditorSubmit.disabled = false;
    elements.taskEditorSubmit.textContent = originalLabel;
  }
}

async function saveAndApproveProposedTask() {
  const task = findTaskBySlug(state.taskEditorSourceSlug);
  if (!task || task.status !== "proposed") return;
  if (!window.confirm("Save these edits and approve this same proposed task as planned work?")) return;
  // Save first with status proposed.  A failed approval is visible and leaves
  // the exact same task safely proposed rather than claiming authorization.
  await submitTaskEditor({ preventDefault() {} });
  if (elements.taskEditorDialog.open) return;
  openProposalDecision({
    slug: task.slug, title: elements.taskEditorTitle.value || task.title,
    proposing_agent: task.owner_agent, recipient: task.proposal_recipient || "agent",
  }, "approve");
}

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => setView(button.dataset.view));
});
elements.artifactAgentSelect.addEventListener("change", () => {
  state.artifactAgentFilter = elements.artifactAgentSelect.value;
  state.artifacts = [];
  state.artifactsLoaded = false;
  void loadArtifacts({ reset: true });
});
elements.createTaskButton.addEventListener("click", openCreateTask);
elements.taskEditorClose.addEventListener("click", () => {
  elements.taskEditorDialog.close();
});
elements.taskEditorForm.addEventListener("submit", submitTaskEditor);
elements.taskTodoAddToggle.addEventListener("click", () => {
  setTodoAddOpen(true);
});
elements.taskTodoAddCancel.addEventListener("click", () => {
  setTodoAddOpen(false);
});
elements.taskTodoAddForm.addEventListener("submit", createTaskTodo);
elements.taskHandoffAnswerForm.addEventListener("submit", answerAndHandBack);
elements.taskTodoShowCompleted.addEventListener("change", () => {
  state.showCompletedTodos = elements.taskTodoShowCompleted.checked;
  const task = findTaskBySlug(state.selectedSlug);
  if (task) renderTaskTodos(task);
});
elements.taskEditorSaveApprove.addEventListener("click", saveAndApproveProposedTask);
elements.agentProfileClose.addEventListener("click", () => {
  elements.agentProfileDialog.close();
});
elements.agentProfileDialog.addEventListener("close", () => {
  setAgentAvatarControlsOpen(false, { focus: false });
  setAgentGoalControlsOpen(false, { focus: false });
});
elements.agentAvatarToggle.addEventListener("click", () => {
  setAgentAvatarControlsOpen(!state.agentAvatarControlsOpen);
});
elements.agentAvatarFile.addEventListener("change", previewAgentAvatar);
elements.agentAvatarForm.addEventListener("submit", submitAgentAvatar);
elements.agentGoalAdd.addEventListener("click", () => {
  saveAgentGoalAssignment(elements.agentGoalSelect.value, "assign");
});
elements.taskTrackMetric.addEventListener("change", updateTaskMetricPreview);
[
  elements.taskMetricLabel,
  elements.taskMetricTarget,
  elements.taskMetricCurrent,
].forEach((input) => input.addEventListener("input", updateTaskMetricPreview));
elements.taskMetricEventBinding.addEventListener("change", () => {
  if (elements.taskMetricEventBinding.value === "job_applied") {
    if (!elements.taskMetricLabel.value.trim()) {
      elements.taskMetricLabel.value = "Job applications";
    }
  }
  updateTaskMetricPreview();
});
elements.refreshButton.addEventListener("click", () => {
  loadTasks({ reason: "manual" });
  if (
    state.agentWorkLoaded ||
    state.showAgentTasks ||
    state.activeView === "today" ||
    state.activeView === "blocked"
  ) loadAgentWork();
  loadProposals({ refresh: true });
  if (state.activeView === "artifacts" || state.artifactsLoaded) {
    void loadArtifacts({ reset: true });
  }
  if (state.activeView === "system-tickets" || state.systemTickets.length) {
    void loadSystemTickets({ force: true });
  }
});
elements.showAgentTasks.addEventListener("change", () => {
  setAgentTasksVisible(elements.showAgentTasks.checked);
});
elements.detailClose.addEventListener("click", closeDetails);
elements.artifactDetailClose.addEventListener("click", closeDetails);
elements.goalDetailClose.addEventListener("click", closeDetails);
elements.projectDetailClose.addEventListener("click", closeDetails);
elements.systemTicketDetailClose.addEventListener("click", closeDetails);
elements.calendarEventDetailClose.addEventListener("click", closeDetails);
elements.projectEditButton.addEventListener("click", () => {
  const project = state.projects.find((item) => item.slug === state.selectedSlug);
  if (state.selectedKind === "project" && project) openEditProject(project);
});
elements.systemTicketEditButton.addEventListener("click", openEditSystemTicket);
elements.taskEditButton.addEventListener("click", openEditTask);
elements.taskDuplicateButton.addEventListener("click", openDuplicateTask);
elements.taskApproveButton.addEventListener("click", () => {
  const task = findTaskBySlug(state.selectedSlug);
  if (task?.status === "proposed") openProposalDecision({ slug: task.slug, title: task.title, proposing_agent: task.owner_agent, recipient: task.proposal_recipient || "agent" }, "approve");
});
elements.taskRejectButton.addEventListener("click", () => {
  const task = findTaskBySlug(state.selectedSlug);
  if (task?.status === "proposed") openProposalDecision({ slug: task.slug, title: task.title, proposing_agent: task.owner_agent, recipient: task.proposal_recipient || "agent" }, "reject");
});
elements.newProjectClose.addEventListener("click", () => {
  elements.newProjectDialog.close();
});
elements.newProjectForm.addEventListener("submit", submitNewProject);
elements.newGoalClose.addEventListener("click", () => {
  elements.newGoalDialog.close();
});
elements.newGoalForm.addEventListener("submit", submitNewGoal);
elements.goalEditButton.addEventListener("click", openEditGoal);
elements.goalPauseButton.addEventListener("click", () => {
  openGoalConfirmation("pause");
});
elements.goalDeleteButton.addEventListener("click", () => {
  openGoalConfirmation("delete");
});
elements.goalConfirmClose.addEventListener("click", closeGoalConfirmation);
elements.goalConfirmCancel.addEventListener("click", closeGoalConfirmation);
elements.goalConfirmSubmit.addEventListener("click", confirmGoalAction);
elements.goalConfirmDialog.addEventListener("close", () => {
  state.goalAction = null;
});
elements.warningDismissClose.addEventListener("click", closeWarningDismiss);
elements.warningDismissCancel.addEventListener("click", closeWarningDismiss);
elements.warningDismissConfirm.addEventListener("click", confirmWarningDismiss);
elements.warningDismissDialog.addEventListener("close", () => {
  state.pendingWarning = null;
  elements.warningDismissError.classList.add("is-hidden");
});
elements.proposalReviewForm.addEventListener("submit", submitProposalReview);
[
  elements.proposalReviewClose,
  elements.proposalReviewCancel,
].forEach((button) => {
  button.addEventListener("click", () => {
    elements.proposalReviewDialog.close();
    state.proposalAction = null;
  });
});
elements.proposalReviewDialog.addEventListener("close", () => {
  state.proposalAction = null;
  elements.proposalReviewError.classList.add("is-hidden");
});
[
  elements.proposalDecisionClose,
  elements.proposalDecisionCancel,
].forEach((button) => {
  button.addEventListener("click", () => {
    elements.proposalDecisionDialog.close();
    state.proposalAction = null;
  });
});
elements.proposalDecisionSubmit.addEventListener(
  "click",
  submitProposalDecision,
);
elements.proposalDecisionDialog.addEventListener("close", () => {
  state.proposalAction = null;
  elements.proposalDecisionError.classList.add("is-hidden");
  elements.proposalDecisionRepair.classList.add("is-hidden");
  elements.proposalDecisionRepair.removeAttribute("href");
});
[
  elements.calendarAccessClose,
  elements.calendarAccessCancel,
].forEach((button) => button.addEventListener("click", () => elements.calendarAccessDialog.close()));
elements.calendarAccessRequest.addEventListener("click", async () => {
  elements.calendarAccessError.classList.add("is-hidden");
  elements.calendarAccessRequest.disabled = true;
  try {
    const response = await fetch("/api/ical-access", { method: "POST", headers: { Accept: "application/json" } });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Calendar permission could not be requested.");
    state.icalStatus = payload.status || "unavailable";
    if (state.icalStatus !== "authorized") throw new Error("Calendar access was not granted. You can manage this in macOS Privacy & Security settings and try again.");
    state.icalRange = "";
    elements.calendarAccessDialog.close();
    await openCalendarPicker();
    render();
  } catch (error) {
    elements.calendarAccessError.textContent = error.message || "Calendar permission could not be requested.";
    elements.calendarAccessError.classList.remove("is-hidden");
  } finally { elements.calendarAccessRequest.disabled = false; }
});
[
  elements.calendarPickerClose,
  elements.calendarPickerCancel,
].forEach((button) => button.addEventListener("click", () => elements.calendarPickerDialog.close()));
elements.calendarPickerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const selected = Array.from(elements.calendarPickerList.querySelectorAll("input:checked"), (input) => input.value);
  elements.calendarPickerError.classList.add("is-hidden");
  elements.calendarPickerSaving.classList.remove("is-hidden");
  elements.calendarPickerSubmit.disabled = true;
  elements.calendarPickerSubmit.textContent = "Saving…";
  elements.calendarPickerClose.disabled = true;
  elements.calendarPickerCancel.disabled = true;
  try {
    const response = await fetch("/api/ical-preferences", { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ selected_calendar_ids: selected }) });
    const payload = await response.json();
    if (!response.ok || !payload.verified) throw new Error(payload.error || "Calendar selections could not be saved.");
    const saved = Array.isArray(payload.selected_calendar_ids) ? payload.selected_calendar_ids : [];
    await loadCalendarPicker();
    const expected = [...new Set(selected)];
    if (JSON.stringify(state.selectedCalendarIds) !== JSON.stringify(expected) || JSON.stringify(saved) !== JSON.stringify(expected)) {
      throw new Error("Calendar selection readback did not match the saved preference. Review the selection and try again.");
    }
    state.icalRange = "";
    state.calendarPreferencesNotice = `Calendar selection saved and verified. ${saved.length} read-only calendar${saved.length === 1 ? "" : "s"} selected.`;
    elements.calendarPickerDialog.close();
    render();
  } catch (error) {
    elements.calendarPickerError.textContent = error.message || "Calendar selections could not be saved.";
    elements.calendarPickerError.classList.remove("is-hidden");
  } finally {
    elements.calendarPickerSaving.classList.add("is-hidden");
    elements.calendarPickerSubmit.disabled = false;
    elements.calendarPickerSubmit.textContent = "Save selected calendars";
    elements.calendarPickerClose.disabled = false;
    elements.calendarPickerCancel.disabled = false;
  }
});
elements.boardStatusRetry.addEventListener("click", () => {
  const move = state.boardMove;
  if (move?.phase === "error") moveBoardTask(move.taskSlug, move.status);
});
elements.aboutButton.addEventListener("click", openAboutDialog);
elements.aboutClose.addEventListener("click", closeAboutDialog);
elements.aboutDialog.addEventListener("close", () => {
  if (state.aboutReturnFocus instanceof HTMLElement) {
    state.aboutReturnFocus.focus();
  }
  state.aboutReturnFocus = null;
});
elements.logsButton.addEventListener("click", openLogsDialog);
elements.systemTicketsButton.addEventListener("click", () => {
  state.activeView = "system-tickets";
  state.showCompletedSystemTickets = false;
  state.completedSystemTickets = [];
  state.completedSystemTicketsOffset = 0;
  state.completedSystemTicketsHasMore = false;
  state.completedSystemTicketsError = "";
  render();
  loadSystemTickets();
});
elements.systemTicketClose.addEventListener("click", () => {
  state.systemTicketEditorSlug = null;
  elements.systemTicketDialog.close();
});
elements.systemTicketForm.addEventListener("submit", submitSystemTicket);
elements.logsClose.addEventListener("click", closeLogsDialog);
elements.logsRefresh.addEventListener("click", () => loadOperationalLogs());
elements.logsLoadMore.addEventListener("click", () => {
  loadOperationalLogs({ append: true });
});
elements.logsSeverity.addEventListener("change", () => loadOperationalLogs());
elements.logsComponent.addEventListener("change", () => loadOperationalLogs());
elements.logsFilterForm.addEventListener("submit", (event) => {
  event.preventDefault();
});
elements.logsDialog.addEventListener("close", () => {
  if (state.logsReturnFocus instanceof HTMLElement) {
    state.logsReturnFocus.focus();
  }
  state.logsReturnFocus = null;
});
document.addEventListener("visibilitychange", () => {
  clearAutoRefreshTimer();
  if (document.hidden) {
    updateAutoRefreshLabel(
      `Auto-refresh every ${AUTO_REFRESH_MINUTES} minutes · paused while hidden`,
    );
    return;
  }
  if (
    state.refreshDeferred ||
    state.autoRefreshDueAt === null ||
    Date.now() >= state.autoRefreshDueAt
  ) {
    state.refreshDeferred = false;
    loadTasks({ reason: "automatic" });
    return;
  }
  scheduleAutoRefresh();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && elements.proposalReviewDialog.open) {
    state.proposalAction = null;
    return;
  }
  if (event.key === "Escape" && elements.proposalDecisionDialog.open) {
    state.proposalAction = null;
    return;
  }
  if (event.key === "Escape" && elements.logsDialog.open) {
    event.preventDefault();
    closeLogsDialog();
    return;
  }
  if (event.key === "Escape" && elements.aboutDialog.open) {
    event.preventDefault();
    closeAboutDialog();
    return;
  }
  const editing =
    event.target instanceof HTMLInputElement ||
    event.target instanceof HTMLTextAreaElement ||
    event.target instanceof HTMLSelectElement;
  if (!editing && event.key.toLowerCase() === "n" && !event.metaKey && !event.ctrlKey) {
    event.preventDefault();
    openCreateTask();
  }
});

bindHudTooltipEvents();
initializeDetailPanelResize();
initializeMobileDetailSheet();
loadReleases();
loadAgentWork();
loadCalendarConnectionState();
loadTasks({ reason: "initial" });
