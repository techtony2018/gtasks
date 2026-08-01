const AUTO_REFRESH_MINUTES = 30;
const AUTO_REFRESH_INTERVAL_MS = AUTO_REFRESH_MINUTES * 60 * 1000;

function renderSafeMarkdown(container, value) {
  const source = typeof value === "string" ? value : "";
  container.replaceChildren();
  const lines = source.split(/\r?\n/);
  const appendInline = (target, text) => {
    const pattern = /\[([^\]]{1,240})\]\(([^)\s]+)\)/g;
    let cursor = 0;
    for (const match of text.matchAll(pattern)) {
      target.append(document.createTextNode(text.slice(cursor, match.index)));
      const url = match[2];
      if (/^(https:\/\/|http:\/\/127\.0\.0\.1(?::\d+)?\/|\/media\/)/.test(url)) {
        const link = document.createElement("a");
        link.href = url;
        link.textContent = match[1];
        link.target = "_blank";
        link.rel = "noreferrer";
        target.append(link);
      } else target.append(document.createTextNode(match[0]));
      cursor = match.index + match[0].length;
    }
    target.append(document.createTextNode(text.slice(cursor)));
  };
  let paragraph = [];
  const flush = () => {
    if (!paragraph.length) return;
    const p = document.createElement("p");
    appendInline(p, paragraph.join("\n"));
    container.append(p);
    paragraph = [];
  };
  for (const line of lines) {
    if (!line.trim()) { flush(); continue; }
    if (/^#{1,3}\s+/.test(line)) {
      flush(); const h = document.createElement("strong"); appendInline(h, line.replace(/^#{1,3}\s+/, "")); container.append(h); continue;
    }
    paragraph.push(line);
  }
  flush();
  if (!container.childNodes.length) container.textContent = "No additional detail yet.";
}

const AGENT_TASKS_PREFERENCE_KEY = "mission-control.show-agent-tasks";
const AGENT_TASKS_PREFERENCE_COOKIE = "mission-control-show-agent-tasks";

const state = {
  snapshot: null,
  activeView: "today",
  selectedSlug: null,
  selectedKind: null,
  weekStart: null,
  calendarMode: "week",
  calendarMonth: null,
  showIcalEvents: true,
  icalEvents: [],
  icalStatus: "not_determined",
  icalRange: "",
  icalLoading: false,
  selectedCalendarIds: [],
  availableCalendars: [],
  systemTickets: [],
  systemTicketIssues: [],
  systemTicketsLoading: false,
  systemTicketsError: "",
  systemTicketsLoadPromise: null,
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
  autoRefreshTimer: null,
  autoRefreshDueAt: null,
  refreshDeferred: false,
  lastSyncedAt: null,
  projects: [],
  projectIssues: [],
  projectWarningStateError: "",
  projectsLoading: true,
  projectsError: "",
  projectEditorSlug: null,
  goalAction: null,
  goalEditorSlug: null,
  pendingWarning: null,
  showDismissedWarnings: false,
  taskEditorMode: "create",
  taskEditorSourceSlug: null,
  agents: [],
  agentTasks: [],
  agentIssues: [],
  agentWorkLoaded: false,
  agentWorkLoading: false,
  agentWorkError: "",
  showAgentTasks: readAgentTasksPreference(),
  proposals: [],
  proposalIssues: [],
  proposalsLoading: true,
  proposalsError: "",
  proposalAgentFilter: "all",
  proposalAction: null,
  profileAgentSlug: null,
  avatarPreviewUrl: null,
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
  taskEditorNextAction: document.querySelector("#task-editor-next-action"),
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
  calendarPickerError: document.querySelector("#calendar-picker-error"),
  detailPanel: document.querySelector("#detail-panel"),
  detailEmpty: document.querySelector("#detail-empty"),
  detailContent: document.querySelector("#detail-content"),
  goalDetailContent: document.querySelector("#goal-detail-content"),
  systemTicketDetailContent: document.querySelector("#system-ticket-detail-content"),
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
  detailClose: document.querySelector("#detail-close"),
  taskDetailStatus: document.querySelector("#task-detail-status"),
  taskEditButton: document.querySelector("#task-edit-button"),
  taskApproveButton: document.querySelector("#task-approve-button"),
  taskRejectButton: document.querySelector("#task-reject-button"),
  taskDuplicateButton: document.querySelector("#task-duplicate-button"),
  taskOwner: document.querySelector("#task-owner"),
  taskOwnerAvatar: document.querySelector("#task-owner-avatar"),
  taskOwnerName: document.querySelector("#task-owner-name"),
  taskNextActionValue: document.querySelector("#task-next-action-value"),
  taskNextActionTimeline: document.querySelector("#task-next-action-timeline"),
  taskProgressDetail: document.querySelector("#task-progress-detail"),
  taskProgressLabel: document.querySelector("#task-progress-label"),
  taskProgressValue: document.querySelector("#task-progress-value"),
  taskProgressBar: document.querySelector("#task-progress-bar"),
  taskProgressBinding: document.querySelector("#task-progress-binding"),
  detailTitle: document.querySelector("#detail-title"),
  detailCopy: document.querySelector("#detail-copy"),
  proposalDetailMeta: document.querySelector("#proposal-detail-meta"),
  detailPriority: document.querySelector("#detail-priority"),
  detailDue: document.querySelector("#detail-due"),
  detailGbrainLink: document.querySelector("#detail-gbrain-link"),
  detailSlug: document.querySelector("#detail-slug"),
  taskGoalNav: document.querySelector("#task-goal-nav"),
  taskGoalValue: document.querySelector("#task-goal-value"),
  taskProjectValue: document.querySelector("#task-project-value"),
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
  elements.toast.textContent = message;
  elements.toast.classList.remove("is-hidden");
  window.clearTimeout(showToast.timeout);
  showToast.timeout = window.setTimeout(() => {
    elements.toast.classList.add("is-hidden");
  }, 3400);
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
    ...groups.waiting_and_blocked,
    ...groups.overdue,
  ];
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
  if (!state.snapshot) return {};
  return {
    inbox: state.snapshot.views.inbox.length,
    today: new Set(allTodayTasks().map((task) => task.slug)).size,
    week: currentWeekTasks().length,
    board: state.snapshot.tasks.length,
    "agent-work": state.agentTasks.length,
    blocked: state.snapshot.views.blocked.length,
    projects: state.projects.length,
    goals: state.snapshot.goals.length,
    completed: state.snapshot.views.completed.length,
    "system-tickets": state.systemTickets.length,
  };
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
  const count = navCounts()[view] || 0;
  const noun = {
    inbox: "in Inbox",
    today: "tasks today",
    week: "tasks this week",
    board: "tasks on Board",
    "agent-work": "agent work items",
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

function taskRow(task, { todayActions = false, calendarWeek = false } = {}) {
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
    task.next_action || "Next action not set",
  );
  appendTaskProgress(nextAction, task);
  const end = node("span", "task-end");
  end.append(node("span", `priority-badge ${task.priority}`, task.priority));
  const due = relativeDue(task);
  end.append(node("span", `due-badge ${due.className}`, due.label));

  button.append(titleWrap, nextAction, end);
  button.addEventListener("click", () => selectTask(task.slug));
  row.append(button);
  // Inbox rows have intentional whitespace around their compact metadata. A
  // click anywhere except an explicit row action is still a detail-selection
  // action, so ordinary and proposed Inbox work do not require Edit to reveal
  // their read-only context.
  row.addEventListener("click", (event) => {
    if (event.target.closest(".task-row-actions")) return;
    if (event.target !== row) return;
    selectTask(task.slug);
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
      groups.waiting_and_blocked,
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
  const tasks = state.snapshot.views[view] || [];
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
    icalEventsForDay(key).forEach((event) => column.append(node("p", "ical-event", event.title || "Calendar event")));
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
  const calendarStatus = state.icalLoading ? "Reading local Calendar…" : state.icalStatus === "authorized" ? (state.selectedCalendarIds.length ? `${state.selectedCalendarIds.length} selected read-only calendar${state.selectedCalendarIds.length === 1 ? "" : "s"}` : "Choose calendars to show events") : state.icalStatus === "denied" || state.icalStatus === "restricted" ? "Calendar permission was not granted" : state.icalStatus === "unavailable" ? "Local Calendar is unavailable" : "Calendar permission needed";
  wrapper.append(node("small", "calendar-events-status", calendarStatus));
  if (state.icalStatus !== "authorized") {
    const connect = node("button", "secondary-button", state.icalStatus === "not_determined" ? "Connect Calendar" : "Reauthorize Calendar");
    connect.type = "button";
    connect.addEventListener("click", openCalendarAccessDialog);
    wrapper.append(connect);
  } else {
    const manage = node("button", "secondary-button", "Manage calendars");
    manage.type = "button";
    manage.addEventListener("click", openCalendarPicker);
    wrapper.append(manage);
  }
  return wrapper;
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
    state.selectedCalendarIds = Array.isArray(payload.selected_calendar_ids) ? payload.selected_calendar_ids : state.selectedCalendarIds;
  } catch (_) {
    state.icalRange = range;
    state.icalStatus = "unavailable";
    state.icalEvents = [];
  }
  finally { state.icalLoading = false; render(); }
}

function icalEventsForDay(day) {
  if (!state.showIcalEvents || state.icalStatus !== "authorized") return [];
  return state.icalEvents.filter((event) => event.day === day);
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
      taskButton.type = "button"; taskButton.addEventListener("click", () => selectTask(task.slug)); cell.append(taskButton);
    });
    icalEventsForDay(key).forEach((event) => cell.append(node("p", "ical-event", event.title || "Calendar event")));
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
    node("span", "board-card-next", task.next_action || "Next action not set"),
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
    node("span", "board-card-next", task.next_action || "Next action not set"),
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

function openAgentProfile(agent) {
  state.profileAgentSlug = agent.slug;
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
    const remove = node("button", "row-action-button", "Unassign");
    remove.type = "button";
    remove.addEventListener("click", () => {
      if (window.confirm(`Remove ${agent.name} as the default agent for “${goal.title}”? The goal and its tasks will not change.`)) {
        saveAgentGoalAssignment(goal.slug, "remove");
      }
    });
    row.append(remove);
    list.append(row);
  });
  elements.agentProfileGoals.replaceChildren(list);
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
      "Goal ownership is read from typed default_agent_for edges. Work states reserved for later are Queued, Working, Waiting for Tony, Blocked, Completed, and Failed. No proposal or task is auto-approved here.",
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
    workSummary.append(node("span", "", latest ? `Next: ${latest.next_action || "No next step set"} · Updated ${formatDay(latest.updated_at || latest.due_day)}` : "No current task or next step recorded."));
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
    card.append(node("p", "", `${counts.active} active · ${counts.proposed} waiting for Tony · ${counts.blocked} blocked · ${counts.completed} completed`));
    const latest = work.filter((task) => task.status === "completed").sort((a,b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")))[0];
    card.append(node("small", "", latest ? `Recent verified completion: ${latest.title || latest.summary}` : "No verified completion yet."));
    list.append(card);
  });
  section.append(list);
  const blockers = state.agentTasks.filter((task) => ["blocked", "proposed"].includes(task.status));
  const issueCount = state.agentIssues.length;
  section.append(node("p", "coordinator-notice", blockers.length || issueCount ? `${blockers.length} material blocker or waiting-for-Tony item${blockers.length === 1 ? "" : "s"}; ${issueCount} malformed or missing-state issue${issueCount === 1 ? "" : "s"}. Details stay in Inbox Needs Attention.` : "No material blockers, waiting-for-Tony items, or malformed agent-work issues."));
  return section;
}

function goalCard(goal) {
  const button = node("button", "goal-card");
  button.type = "button";
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
  button.addEventListener("click", () => selectGoal(goal.slug));
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
    card.append(
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
    card.append(node("p", "", goalNames.length ? `Supports: ${goalNames.join(", ")}` : "No supporting goals selected."));
    const edit = actionIcon("✎", `Edit ${project.title}`);
    edit.addEventListener("click", () => openEditProject(project));
    card.append(edit);
    grid.append(card);
  });
  fragment.append(grid);
  return fragment;
}

async function loadAgents() {
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
    render();
  } catch (_error) {
    state.agents = [];
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

async function loadProposals() {
  state.proposalsLoading = true;
  state.proposalsError = "";
  if (state.snapshot) render();
  try {
    const response = await fetch("/api/proposals", {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Proposed tasks could not be read.");
    }
    state.proposals = Array.isArray(payload.proposals)
      ? payload.proposals
      : [];
    state.proposalIssues = Array.isArray(payload.issues)
      ? payload.issues
      : [];
  } catch (error) {
    state.proposalsError =
      error.message || "Proposed tasks could not be read.";
  } finally {
    state.proposalsLoading = false;
    if (state.snapshot) render();
  }
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
  populateProjectGoalChoices();
  elements.newProjectError.classList.add("is-hidden");
  elements.newProjectMode.textContent = "New durable project";
  elements.newProjectHeading.textContent = "Create a Project";
  elements.newProjectSubmit.textContent = "Create project";
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
  populateProjectGoalChoices(project.supporting_goal_slugs || []);
  elements.newProjectMode.textContent = "Existing durable project";
  elements.newProjectHeading.textContent = "Edit";
  elements.newProjectSubmit.textContent = "Save changes";
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
  try {
    const response = await fetch(editing ? `/api/projects/${encodeURIComponent(state.projectEditorSlug)}` : "/api/projects", {
      method: editing ? "PATCH" : "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({ title: elements.newProjectTitle.value, supporting_goal_slugs: [...elements.newProjectGoals.selectedOptions].map((option) => option.value) }),
    });
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(result.error || "Project could not be created.");
      error.code = result.code;
      error.slug = result.slug;
      throw error;
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
    showToast(editing ? "Project changes verified in GBrain." : "Project created, linked, and verified in GBrain.");
    render();
  } catch (error) {
    elements.newProjectError.textContent =
      error.code === "partial_write" && error.slug
        ? `${error.message} Inspect ${error.slug}; do not retry yet.`
        : error.message;
    elements.newProjectError.classList.remove("is-hidden");
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
    showToast(editing ? "Goal changes verified in GBrain." : "Goal created, linked, and verified in GBrain.");
    selectGoal(result.goal.slug);
  } catch (error) {
    elements.newGoalError.textContent =
      error.code === "partial_write" && error.slug
        ? `${error.message} Inspect ${error.slug}; do not retry yet.`
        : error.message;
    elements.newGoalError.classList.remove("is-hidden");
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
  card.classList.toggle("is-selected", state.selectedSlug === proposal.slug);
  card.tabIndex = 0;
  card.setAttribute("role", "button");
  card.setAttribute("aria-label", `Open proposed task ${proposal.title}`);
  const open = () => selectTask(proposal.slug);
  card.addEventListener("click", open);
  card.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); }
  });
  card.append(
    node("h4", "", proposal.title),
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
  if (state.proposalsLoading) {
    section.append(node("div", "section-empty", "Reading proposed work…"));
    return section;
  }
  if (state.proposalsError) {
    const error = node("div", "section-empty", state.proposalsError);
    const retry = node("button", "secondary-button", "Try again");
    retry.type = "button";
    retry.addEventListener("click", loadProposals);
    error.append(retry);
    section.append(error);
    return section;
  }
  const visible = state.proposals.filter(
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
  agentOrder.forEach((agentSlug) => {
    const group = visible.filter(
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
        : proposal);
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
        : proposal);
    elements.proposalDecisionDialog.close();
    state.proposalAction = null;
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

function render() {
  const focusedTooltipTarget = document.activeElement?.closest?.(".has-tooltip") || null;
  hideHudTooltip();
  window.requestAnimationFrame(() => {
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
  // The filter is a persisted, client-only preference. Rendering can happen
  // during navigation and while agent work is loading, so keep the control in
  // lockstep with state rather than relying on the previous DOM value.
  elements.showAgentTasks.checked = state.showAgentTasks;
  if (state.loading) return;
  if (!state.snapshot) return;

  const view = state.activeView;
  const content =
    view === "today"
      ? renderToday()
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
  const date = parseDay(state.activeView === "week" ? currentWeekStart() : state.snapshot.as_of);
  elements.dateLabel.textContent = new Intl.DateTimeFormat(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  }).format(date);
}

function renderSystemTicketsView() {
  const section = node("section", "projects-view");
  const heading = node("div", "projects-view-heading");
  const copy = node("div");
  copy.append(node("h2", "", "Mission Control System Tickets"), node("p", "", "Separate canonical change requests. Nightly work processes every Planned ticket; safe batching can sequence overlaps, and each ticket receives an execution or blocking outcome. Implementation and independent QA receipts remain with the ticket."));
  const create = actionIcon("+", "New Ticket", { primary: true }); create.addEventListener("click", openSystemTicketDialog);
  heading.append(copy, create); section.append(heading);
  if (state.systemTicketsLoading) { section.append(node("div", "section-empty", "Reading System Tickets…")); return section; }
  if (state.systemTicketsError) { section.append(node("div", "section-empty", state.systemTicketsError)); return section; }
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
  if (!state.systemTickets.length) { section.append(node("div", "section-empty", state.systemTicketIssues.length ? "No valid System Tickets are ready to display until the ticket data above is repaired." : "No System Tickets yet. Create a request when a change should be collected for nightly work.")); return section; }
  const list = node("div", "task-list");
  state.systemTickets.forEach((ticket) => {
    const card = node("button", "system-ticket-card");
    card.type = "button";
    card.setAttribute("aria-label", `Open System Ticket ${ticket.title}`);
    card.setAttribute("aria-current", state.selectedSlug === ticket.slug ? "true" : "false");
    card.classList.toggle("is-selected", state.selectedSlug === ticket.slug);
    const header = node("div", "system-ticket-card-header");
    header.append(node("strong", "", ticket.title), node("span", `priority-badge ${ticket.priority}`, ticket.status));
    const meta = node("p", "system-ticket-card-meta", `${ticket.target_subsystem.replace(/_/g, " ")} · ${ticket.priority} priority · ${ticket.implementation_receipts.length} implementation / ${ticket.qa_receipts.length} QA receipts`);
    card.append(header, meta);
    card.addEventListener("click", () => selectSystemTicket(ticket.slug));
    list.append(card);
  });
  section.append(list); return section;
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

function selectSystemTicket(ticketSlug) {
  const ticket = state.systemTickets.find((item) => item.slug === ticketSlug);
  if (!ticket) return;
  state.selectedSlug = ticket.slug;
  state.selectedKind = "system-ticket";
  elements.detailPanel.setAttribute("aria-hidden", "false");
  elements.detailPanel.setAttribute("aria-label", "System Ticket details");
  elements.detailEmpty.classList.add("is-hidden");
  elements.detailContent.classList.add("is-hidden");
  elements.goalDetailContent.classList.add("is-hidden");
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
  if (window.matchMedia("(max-width: 760px)").matches) {
    window.requestAnimationFrame(() => {
      elements.detailPanel.scrollIntoView({ block: "start", behavior: "auto" });
      elements.systemTicketDetailTitle.focus({ preventScroll: true });
    });
  }
}

function openEditSystemTicket() {
  const ticket = state.systemTickets.find((item) => item.slug === state.selectedSlug);
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

function loadSystemTickets({ force = false } = {}) {
  if (state.systemTicketsLoadPromise && !force) return state.systemTicketsLoadPromise;
  state.systemTicketsLoadPromise = performSystemTicketLoad().finally(() => {
    state.systemTicketsLoadPromise = null;
  });
  return state.systemTicketsLoadPromise;
}

async function performSystemTicketLoad() {
  state.systemTicketsLoading = true;
  try { const response = await fetch("/api/system-tickets", { headers: { Accept: "application/json" }, cache: "no-store" }); const payload = await response.json(); if (!response.ok) throw new Error(payload.error || "System Tickets could not be read."); state.systemTickets = Array.isArray(payload.tickets) ? payload.tickets : []; state.systemTicketIssues = Array.isArray(payload.issues) ? payload.issues : []; state.systemTicketsError = ""; }
  catch (error) { state.systemTicketsError = error.message || "System Tickets could not be read."; }
  finally { state.systemTicketsLoading = false; if (state.activeView === "system-tickets") render(); }
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
    state.activeView="system-tickets";
    selectSystemTicket(savedSlug);
    showToast(editing ? "System Ticket updated and verified in GBrain." : "System Ticket created and verified in GBrain.");
  }
  catch(error){ elements.systemTicketError.textContent=error.message; elements.systemTicketError.classList.remove("is-hidden"); }
  finally { elements.systemTicketSubmit.disabled = false; }
}

function renderNextActionTimeline(task) {
  elements.taskNextActionTimeline.replaceChildren();
  if (task.next_action) {
    const current = node("li", "is-current");
    current.append(
      node("span", "next-action-state", "Current"),
      node("strong", "", task.next_action),
    );
    elements.taskNextActionTimeline.append(current);
  }
  const history = Array.isArray(task.next_action_history)
    ? [...task.next_action_history].reverse()
    : [];
  history.forEach((entry) => {
    const item = node("li", "is-completed");
    item.append(
      node("span", "next-action-state", "Completed"),
      node("strong", "", entry.action),
      node(
        "time",
        "",
        entry.completed_at
          ? new Date(entry.completed_at).toLocaleString()
          : "Completion time unavailable",
      ),
    );
    elements.taskNextActionTimeline.append(item);
  });
  if (!task.next_action && !history.length) {
    elements.taskNextActionTimeline.append(
      node("li", "is-empty", "No Next Action history recorded yet."),
    );
  }
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

function selectTask(slug, taskFallback = null) {
  const task = findTaskBySlug(slug) || taskFallback;
  if (!task) return;
  state.selectedSlug = slug;
  state.selectedKind = "task";
  elements.detailPanel.setAttribute("aria-hidden", "false");
  elements.detailPanel.setAttribute("aria-label", "Task details");
  elements.detailEmpty.classList.add("is-hidden");
  elements.detailContent.classList.remove("is-hidden");
  elements.goalDetailContent.classList.add("is-hidden");
  elements.systemTicketDetailContent.classList.add("is-hidden");
  elements.taskDetailStatus.textContent = taskUiStatus(task) === "active" ? "In Progress" : taskUiStatus(task);
  const isProposed = task.status === "proposed";
  elements.taskApproveButton.classList.toggle("is-hidden", !isProposed);
  elements.taskRejectButton.classList.toggle("is-hidden", !isProposed);
  elements.taskDuplicateButton.classList.toggle("is-hidden", isProposed);
  elements.detailTitle.textContent = task.title || task.summary;
  renderSafeMarkdown(elements.detailCopy, task.detail || "");
  elements.proposalDetailMeta.classList.toggle("is-hidden", task.status !== "proposed");
  if (task.status === "proposed") {
    const ownerName = task.owner?.name || state.agents.find((agent) => agent.slug === task.owner_agent)?.name || task.owner_agent || "Unknown agent";
    const target = state.snapshot.goals.find((goal) => goal.slug === task.goal)?.title || task.goal || "No linked primary goal";
    const submitted = task.proposal_submitted_at || task.created_at;
    elements.proposalDetailMeta.textContent = `Proposed by ${ownerName} · Goal: ${target}${submitted ? ` · Submitted ${new Date(submitted).toLocaleString()}` : ""}${task.updated_at ? ` · Updated ${new Date(task.updated_at).toLocaleString()}` : ""}`;
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
  elements.taskNextActionValue.textContent = task.next_action || "No next action set.";
  renderNextActionTimeline(task);
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
  if (window.matchMedia("(max-width: 760px)").matches) {
    window.requestAnimationFrame(() => {
      elements.detailPanel.scrollIntoView({ block: "start", behavior: "auto" });
      elements.detailTitle.focus({ preventScroll: true });
    });
  }
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

function selectGoal(slug) {
  const goal = state.snapshot?.goals.find((item) => item.slug === slug);
  if (!goal) return;
  state.selectedSlug = slug;
  state.selectedKind = "goal";
  elements.detailPanel.setAttribute("aria-hidden", "false");
  elements.detailPanel.setAttribute("aria-label", "Goal details");
  elements.detailEmpty.classList.add("is-hidden");
  elements.detailContent.classList.add("is-hidden");
  elements.goalDetailContent.classList.remove("is-hidden");
  elements.systemTicketDetailContent.classList.add("is-hidden");
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
}

function closeDetails() {
  state.selectedSlug = null;
  state.selectedKind = null;
  elements.detailPanel.setAttribute("aria-hidden", "true");
  elements.detailContent.classList.add("is-hidden");
  elements.goalDetailContent.classList.add("is-hidden");
  elements.systemTicketDetailContent.classList.add("is-hidden");
  elements.detailEmpty.classList.remove("is-hidden");
  render();
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

function nextActionErrorMessage(error) {
  if (error.code === "partial_write" && error.slug) {
    return `${error.message} Inspect ${error.slug} before retrying.`;
  }
  return error.message || "Next action could not be saved.";
}

async function saveTaskNextAction() {
  if (state.selectedKind !== "task" || !state.selectedSlug) return;
  const taskSlug = state.selectedSlug;
  const nextAction = elements.taskNextActionInput.value;
  elements.taskNextActionError.classList.add("is-hidden");
  elements.taskNextActionSave.disabled = true;
  elements.taskNextActionSave.textContent = "Saving…";
  try {
    const response = await fetch(
      `/api/tasks/${encodeURIComponent(taskSlug)}/next-action`,
      {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({ next_action: nextAction }),
      },
    );
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(result.error || "Next action could not be saved.");
      error.code = result.code;
      error.slug = result.slug;
      throw error;
    }
    showToast(
      result.receipt.next_action
        ? "Next action saved and verified in GBrain."
        : "Next action cleared and verified in GBrain.",
    );
    await loadTasks();
    selectTask(taskSlug);
  } catch (error) {
    elements.taskNextActionError.textContent = nextActionErrorMessage(error);
    elements.taskNextActionError.classList.remove("is-hidden");
  } finally {
    elements.taskNextActionSave.textContent = "Save";
    elements.taskNextActionSave.disabled =
      elements.taskNextActionInput.value.trim() ===
      elements.taskNextActionInput.dataset.currentValue;
  }
}

function setView(view) {
  if (!viewMeta[view]) return;
  state.activeView = view;
  render();
  if (
    (view === "agent-work" || (view === "board" && state.showAgentTasks)) &&
    !state.agentWorkLoaded
  ) {
    void loadAgentWork();
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
  state.loading = true;
  elements.syncLabel.textContent =
    reason === "automatic" ? "Refreshing from GBrain…" : "Reading GBrain…";
  elements.refreshButton.disabled = true;
  setConnection("loading", "Connecting");
  if (!state.snapshot) {
    const loading = node("div", "loading-state");
    loading.append(node("span"), node("span"), node("span"));
    elements.viewSurface.replaceChildren(loading);
  }
  try {
    const response = await fetch(
      reason === "initial" ? "/api/tasks" : "/api/tasks?refresh=1",
      {
      headers: { Accept: "application/json" },
      cache: "no-store",
      },
    );
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to read GBrain.");
    state.snapshot = payload;
    state.lastSyncedAt = Date.now();
    state.refreshDeferred = false;
    setConnection("connected", "GBrain connected");
    elements.syncLabel.textContent =
      `Synced ${payload.tasks.length} task${payload.tasks.length === 1 ? "" : "s"} ` +
      `at ${formatSyncTime(state.lastSyncedAt)}`;
    scheduleAutoRefresh({ reset: true });
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
      setConnection("error", "GBrain unavailable");
      elements.syncLabel.textContent = "Read failed";
      showLoadError(error);
    }
  } finally {
    state.loading = false;
    elements.refreshButton.disabled = false;
    if (state.snapshot) render();
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
      ? "Only distinct verified job-applied queue events increment this daily 5-application quota. The fifth completes the task after GBrain readback."
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

function openCreateTask() {
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

function openDuplicateTask() {
  if (state.selectedKind !== "task" || !state.selectedSlug) return;
  const task = state.snapshot?.tasks.find((item) => item.slug === state.selectedSlug);
  if (!task) return;
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
  elements.taskEditorNextAction.value = task.next_action || "";
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

function openEditTask() {
  if (state.selectedKind !== "task" || !state.selectedSlug) return;
  const task = findTaskBySlug(state.selectedSlug);
  if (!task) return;
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
  elements.taskEditorNextAction.value = task.next_action || "";
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
  if (eventBinding === "job_applied" && (target !== 5 || current !== 0)) {
    throw new Error(
      "Automatic job-applied tracking requires target 5 and current 0 because only verified queue events count.",
    );
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
  try {
    const payload = {
      title: elements.taskEditorTitle.value,
      detail: elements.taskEditorDetail.value,
      priority: elements.taskEditorPriority.value,
      next_action: elements.taskEditorNextAction.value,
      due_day: elements.taskEditorDue.value,
      project_slug: elements.taskEditorProject.value || null,
      goal_slug: elements.taskEditorGoal.value || null,
      progress_metric: taskEditorMetricPayload(),
      ...(state.taskEditorMode === "create"
        || state.taskEditorMode === "duplicate"
        ? { assignee_slug: elements.taskEditorAssignee.value }
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
      showToast(
        state.taskEditorMode === "edit"
          ? `Saved “${savedTask.title}” and verified its assignment in GBrain.`
          : `Created queued work for ${agent?.name || "the selected agent"} and verified its assignment in GBrain.`,
      );
    } else {
      showToast(
        state.taskEditorMode === "duplicate"
          ? `Created a clean copy of “${savedTask.title}” in GBrain.`
          : state.taskEditorMode === "edit"
            ? `Saved “${savedTask.title}” and verified it in GBrain.`
            : `Created “${savedTask.title}” in GBrain.`,
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
elements.createTaskButton.addEventListener("click", openCreateTask);
elements.taskEditorClose.addEventListener("click", () => {
  elements.taskEditorDialog.close();
});
elements.taskEditorForm.addEventListener("submit", submitTaskEditor);
elements.taskEditorSaveApprove.addEventListener("click", saveAndApproveProposedTask);
elements.agentProfileClose.addEventListener("click", () => {
  elements.agentProfileDialog.close();
});
elements.agentProfileDialog.addEventListener("close", clearAgentAvatarPreview);
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
    elements.taskMetricTarget.value = "5";
    elements.taskMetricCurrent.value = "0";
  }
  updateTaskMetricPreview();
});
elements.refreshButton.addEventListener("click", () => {
  loadTasks({ reason: "manual" });
  if (state.agentWorkLoaded || state.showAgentTasks) loadAgentWork();
  loadProposals();
});
elements.showAgentTasks.addEventListener("change", () => {
  setAgentTasksVisible(elements.showAgentTasks.checked);
});
elements.detailClose.addEventListener("click", closeDetails);
elements.goalDetailClose.addEventListener("click", closeDetails);
elements.systemTicketDetailClose.addEventListener("click", closeDetails);
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
  try {
    const response = await fetch("/api/ical-preferences", { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ selected_calendar_ids: selected }) });
    const payload = await response.json();
    if (!response.ok || !payload.verified) throw new Error(payload.error || "Calendar selections could not be saved.");
    state.selectedCalendarIds = payload.selected_calendar_ids || [];
    state.icalRange = "";
    elements.calendarPickerDialog.close();
    render();
  } catch (error) {
    elements.calendarPickerError.textContent = error.message || "Calendar selections could not be saved.";
    elements.calendarPickerError.classList.remove("is-hidden");
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
elements.systemTicketsButton.addEventListener("click", () => { state.activeView = "system-tickets"; render(); loadSystemTickets(); });
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
loadReleases();
loadTasks({ reason: "initial" }).finally(() => loadSystemTickets({ force: true }));
loadProjects();
loadAgents();
loadProposals();
loadSystemTickets();
