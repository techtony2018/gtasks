const AUTO_REFRESH_MINUTES = 30;
const AUTO_REFRESH_INTERVAL_MS = AUTO_REFRESH_MINUTES * 60 * 1000;

const state = {
  snapshot: null,
  activeView: "today",
  selectedSlug: null,
  selectedKind: null,
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
  goalAction: null,
  pendingWarning: null,
  showDismissedWarnings: false,
};

const viewMeta = {
  inbox: {
    title: "Inbox",
    emptyTitle: "Your inbox is clear",
    emptyCopy: "Quick Add puts a title here first, with today as its due date unless you choose another.",
  },
  today: {
    title: "Today’s Action List",
  },
  board: {
    title: "Board",
  },
  upcoming: {
    title: "Upcoming",
    emptyTitle: "Nothing is waiting ahead",
    emptyCopy: "Tasks with a due date after today will collect here.",
  },
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
  dateLabel: document.querySelector("#date-label"),
  syncLabel: document.querySelector("#sync-label"),
  autoRefreshLabel: document.querySelector("#auto-refresh-label"),
  refreshButton: document.querySelector("#refresh-button"),
  boardStatusAlert: document.querySelector("#board-status-alert"),
  boardStatusMessage: document.querySelector("#board-status-message"),
  boardStatusRetry: document.querySelector("#board-status-retry"),
  storeDot: document.querySelector("#store-dot"),
  storeLabel: document.querySelector("#store-label"),
  quickAddButton: document.querySelector("#quick-add-button"),
  quickAddDialog: document.querySelector("#quick-add-dialog"),
  quickAddForm: document.querySelector("#quick-add-form"),
  quickAddSubmit: document.querySelector("#quick-add-submit"),
  quickAddClose: document.querySelector("#quick-add-close"),
  quickAddError: document.querySelector("#quick-add-error"),
  taskTitle: document.querySelector("#task-title"),
  taskDueDay: document.querySelector("#task-due-day"),
  dueDefaultCopy: document.querySelector("#due-default-copy"),
  detailPanel: document.querySelector("#detail-panel"),
  detailEmpty: document.querySelector("#detail-empty"),
  detailContent: document.querySelector("#detail-content"),
  goalDetailContent: document.querySelector("#goal-detail-content"),
  detailClose: document.querySelector("#detail-close"),
  taskStatusSelect: document.querySelector("#task-status-select"),
  taskStatusSave: document.querySelector("#task-status-save"),
  taskStatusError: document.querySelector("#task-status-error"),
  taskNextActionInput: document.querySelector("#task-next-action-input"),
  taskNextActionSave: document.querySelector("#task-next-action-save"),
  taskNextActionError: document.querySelector("#task-next-action-error"),
  detailTitle: document.querySelector("#detail-title"),
  detailCopy: document.querySelector("#detail-copy"),
  detailPriority: document.querySelector("#detail-priority"),
  detailDue: document.querySelector("#detail-due"),
  detailGbrainLink: document.querySelector("#detail-gbrain-link"),
  detailSlug: document.querySelector("#detail-slug"),
  taskGoalSelect: document.querySelector("#task-goal-select"),
  taskGoalSave: document.querySelector("#task-goal-save"),
  taskGoalError: document.querySelector("#task-goal-error"),
  taskGoalNav: document.querySelector("#task-goal-nav"),
  taskProjectSelect: document.querySelector("#task-project-select"),
  taskProjectSave: document.querySelector("#task-project-save"),
  taskProjectError: document.querySelector("#task-project-error"),
  goalDetailClose: document.querySelector("#goal-detail-close"),
  goalDetailStatus: document.querySelector("#goal-detail-status"),
  goalDetailTitle: document.querySelector("#goal-detail-title"),
  goalDetailOutcome: document.querySelector("#goal-detail-outcome"),
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
  newGoalClose: document.querySelector("#new-goal-close"),
  newGoalError: document.querySelector("#new-goal-error"),
  goalPauseButton: document.querySelector("#goal-pause-button"),
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

function formatDay(value, style = "short") {
  const day = parseDay(value);
  if (!day) return "No date";
  return new Intl.DateTimeFormat(undefined, {
    month: style === "long" ? "long" : "short",
    day: "numeric",
    year: style === "long" ? "numeric" : undefined,
  }).format(day);
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

function taskUiStatus(task) {
  return task.status === "waiting" ? "blocked" : task.status;
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
    gtasks: "GTasks",
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
    "Event Queue Reader status is unavailable. GTasks remains available.";
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
      `${error.message || "Operational logs are unavailable."} GTasks remains available.`;
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
        !["active", "waiting", "blocked", "completed", "cancelled"].includes(
          task.status,
        ) &&
        (task.due_day === asOf || task.scheduled_day === asOf),
    ),
    waiting_and_blocked: active.filter((task) =>
      ["waiting", "blocked"].includes(task.status)),
    overdue: active.filter(
      (task) =>
        !["active", "waiting", "blocked", "completed", "cancelled"].includes(
          task.status,
        ) &&
        task.due_day &&
        task.due_day < asOf,
    ),
  };
  state.snapshot.views = {
    inbox: active.filter((task) => task.inbox && unfinished(task)),
    upcoming: active.filter(
      (task) => task.due_day && task.due_day > asOf && unfinished(task),
    ),
    blocked: active.filter((task) =>
      ["waiting", "blocked"].includes(task.status)),
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
  if (index < 0) {
    const error = new Error(
      "GBrain returned a task that is not present in the current GTasks snapshot.",
    );
    error.code = "ambiguous_readback";
    throw error;
  }
  state.snapshot.tasks.splice(index, 1, task);
  rebuildDerivedTaskViews();
  render();
}

function navCounts() {
  if (!state.snapshot) return {};
  return {
    inbox: state.snapshot.views.inbox.length,
    today: new Set(allTodayTasks().map((task) => task.slug)).size,
    board: state.snapshot.tasks.length,
    upcoming: state.snapshot.views.upcoming.length,
    blocked: state.snapshot.views.blocked.length,
    projects: state.projects.length,
    goals: state.snapshot.goals.length,
    completed: state.snapshot.views.completed.length,
  };
}

function renderNavigation() {
  const counts = navCounts();
  document.querySelectorAll(".nav-item").forEach((button) => {
    const view = button.dataset.view;
    button.classList.toggle("is-active", view === state.activeView);
    button.setAttribute("aria-current", view === state.activeView ? "page" : "false");
  });
  Object.entries(counts).forEach(([view, count]) => {
    const target = document.querySelector(`[data-count="${view}"]`);
    if (target) target.textContent = String(count);
  });
}

function taskRow(task) {
  const button = node("button", "task-row");
  button.type = "button";
  button.dataset.slug = task.slug;
  button.classList.toggle("is-selected", state.selectedSlug === task.slug);

  const titleWrap = node("span", "task-title-wrap");
  const dot = node("span", `task-state-dot ${taskUiStatus(task)}`);
  dot.setAttribute("aria-hidden", "true");
  const titleText = node("span");
  titleText.append(
    node("span", "task-title", task.title || task.summary),
    node("span", "task-project", task.project || (task.inbox ? "Inbox · No project" : "No project")),
  );
  titleWrap.append(dot, titleText);

  const nextAction = node(
    "span",
    "task-next",
    task.next_action || "Next action not set",
  );
  const end = node("span", "task-end");
  end.append(node("span", `priority-badge ${task.priority}`, task.priority));
  const due = relativeDue(task);
  end.append(node("span", `due-badge ${due.className}`, due.label));

  button.append(titleWrap, nextAction, end);
  button.addEventListener("click", () => selectTask(task.slug));
  return button;
}

function section(title, tasks, emptyCopy, overflow = 0) {
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
  tasks.forEach((task) => list.append(taskRow(task)));
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
  const addButton = node("button", "submit-button", "Create a task");
  addButton.type = "button";
  addButton.addEventListener("click", openQuickAdd);
  const inboxButton = node("button", "secondary-button", "Choose from Inbox");
  inboxButton.type = "button";
  inboxButton.addEventListener("click", () => setView("inbox"));
  actions.append(addButton, inboxButton);
  copy.append(actions);
  wrapper.append(copy, node("div", "empty-orbit"));
  return wrapper;
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
  if (!allTodayTasks().length) fragment.append(emptyActionState());
  fragment.append(
    section(
      "In Progress",
      groups.in_progress,
      "Start one task when you’re ready to focus.",
      groups.in_progress_overflow,
    ),
    section(
      "Today’s Actions",
      groups.todays_actions,
      "No unstarted task is scheduled or due today.",
    ),
    section(
      "Blocked",
      groups.waiting_and_blocked,
      "Nothing is blocked.",
    ),
    section(
      "Overdue",
      groups.overdue,
      "No unstarted task is past its due date.",
    ),
    goalsHomeSection(),
  );
  return fragment;
}

function simpleEmpty(meta) {
  const wrapper = node("section", "simple-empty");
  const content = node("div");
  content.append(
    node("span", "simple-empty-mark", "G"),
    node("h2", "", meta.emptyTitle),
    node("p", "", meta.emptyCopy),
  );
  const addButton = node("button", "submit-button", "Quick Add");
  addButton.type = "button";
  addButton.addEventListener("click", openQuickAdd);
  content.append(addButton);
  wrapper.append(content);
  return wrapper;
}

function renderListView(view) {
  const tasks = state.snapshot.views[view] || [];
  if (!tasks.length) return simpleEmpty(viewMeta[view]);
  const fragment = document.createDocumentFragment();
  fragment.append(section(viewMeta[view].title, tasks, ""));
  return fragment;
}

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
    heading,
    node("span", "board-card-next", task.next_action || "Next action not set"),
    meta,
    node("span", `due-badge ${relativeDue(task).className}`, relativeDue(task).label),
  );
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

function renderBoard() {
  const board = node("section", "board-grid");
  board.setAttribute("aria-label", "Task status board");
  boardColumns.forEach((definition) => {
    const tasks = state.snapshot.tasks.filter((task) =>
      taskUiStatus(task) === definition.status);
    const column = node("section", "board-column");
    column.dataset.status = definition.status;
    column.setAttribute("aria-label", `${definition.title} status lane`);
    const heading = node("div", "board-column-heading");
    heading.append(
      node("h2", "", definition.title),
      node("span", "", String(tasks.length)),
    );
    column.append(heading);
    if (tasks.length) {
      const cards = node("div", "board-card-list");
      tasks.forEach((task) => cards.append(boardCard(task)));
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
  return board;
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
  const create = node("button", "submit-button", "New Goal");
  create.type = "button";
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
  const create = node("button", "submit-button", "New Project");
  create.type = "button";
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
        "No durable GTasks projects yet. Create one, then assign tasks separately.",
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
      node("code", "", project.slug),
    );
    grid.append(card);
  });
  fragment.append(grid);
  return fragment;
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
  elements.newProjectForm.reset();
  elements.newProjectError.classList.add("is-hidden");
  elements.newProjectDialog.showModal();
  window.setTimeout(() => elements.newProjectTitle.focus(), 0);
}

async function submitNewProject(event) {
  event.preventDefault();
  elements.newProjectError.classList.add("is-hidden");
  elements.newProjectSubmit.disabled = true;
  elements.newProjectSubmit.textContent = "Creating in GBrain…";
  try {
    const response = await fetch("/api/projects", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({ title: elements.newProjectTitle.value }),
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
    showToast("Project created, linked, and verified in GBrain.");
    render();
  } catch (error) {
    elements.newProjectError.textContent =
      error.code === "partial_write" && error.slug
        ? `${error.message} Inspect ${error.slug}; do not retry yet.`
        : error.message;
    elements.newProjectError.classList.remove("is-hidden");
  } finally {
    elements.newProjectSubmit.disabled = false;
    elements.newProjectSubmit.textContent = "Create project";
  }
}

function openNewGoal() {
  elements.newGoalForm.reset();
  elements.newGoalCadence.value = "weekly";
  elements.newGoalError.classList.add("is-hidden");
  elements.newGoalDialog.showModal();
  window.setTimeout(() => elements.newGoalTitle.focus(), 0);
}

async function submitNewGoal(event) {
  event.preventDefault();
  elements.newGoalError.classList.add("is-hidden");
  elements.newGoalSubmit.disabled = true;
  elements.newGoalSubmit.textContent = "Creating in GBrain…";
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
  try {
    const response = await fetch("/api/goals", {
      method: "POST",
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
    const created = state.snapshot.goals.some(
      (goal) => goal.slug === result.goal.slug,
    );
    if (!created) {
      throw new Error(
        "GBrain accepted the goal, but it was not present after Tony’s Goals refresh.",
      );
    }
    elements.newGoalDialog.close();
    state.activeView = "goals";
    showToast("Goal created, linked, and verified in GBrain.");
    selectGoal(result.goal.slug);
  } catch (error) {
    elements.newGoalError.textContent =
      error.code === "partial_write" && error.slug
        ? `${error.message} Inspect ${error.slug}; do not retry yet.`
        : error.message;
    elements.newGoalError.classList.remove("is-hidden");
  } finally {
    elements.newGoalSubmit.disabled = false;
    elements.newGoalSubmit.textContent = "Create goal";
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
      "GTasks will remove only this goal’s paired advances_goal and advanced_by links, without deleting or changing the status/content of linked tasks. The goal page is then soft-deleted and recoverable in GBrain for 72 hours.";
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
  const seen = new Set();
  const issues = [...taskIssues, ...projectIssues].filter((issue) => {
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

function render() {
  renderNavigation();
  updateBoardStatus();
  elements.viewTitle.textContent = viewMeta[state.activeView].title;
  if (state.loading) return;
  if (!state.snapshot) return;

  const view = state.activeView;
  const content =
    view === "today"
      ? renderToday()
      : view === "board"
        ? renderBoard()
      : view === "projects"
        ? renderProjectsView()
      : view === "goals"
        ? renderGoalsView()
        : renderListView(view);
  const attention = view === "inbox" ? renderNeedsAttention() : null;
  elements.viewSurface.replaceChildren(
    ...(attention ? [attention, content] : [content]),
  );
  const date = parseDay(state.snapshot.as_of);
  elements.dateLabel.textContent = new Intl.DateTimeFormat(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  }).format(date);
}

function selectTask(slug) {
  const task = state.snapshot?.tasks.find((item) => item.slug === slug);
  if (!task) return;
  state.selectedSlug = slug;
  state.selectedKind = "task";
  elements.detailPanel.setAttribute("aria-hidden", "false");
  elements.detailPanel.setAttribute("aria-label", "Task details");
  elements.detailEmpty.classList.add("is-hidden");
  elements.detailContent.classList.remove("is-hidden");
  elements.goalDetailContent.classList.add("is-hidden");
  elements.taskStatusSelect.value = taskUiStatus(task);
  elements.taskStatusSelect.dataset.currentStatus = task.status;
  elements.taskStatusSave.disabled =
    elements.taskStatusSelect.value === task.status;
  elements.taskStatusError.classList.add("is-hidden");
  elements.taskNextActionInput.value = task.next_action || "";
  elements.taskNextActionInput.dataset.currentValue = task.next_action || "";
  elements.taskNextActionSave.disabled = true;
  elements.taskNextActionError.classList.add("is-hidden");
  elements.detailTitle.textContent = task.title || task.summary;
  elements.detailCopy.textContent = task.detail || "No additional detail yet.";
  elements.detailPriority.textContent = task.priority;
  elements.detailDue.textContent = formatDay(task.due_day, "long");
  elements.detailGbrainLink.href = `http://127.0.0.1:8788/?slug=${encodeURIComponent(task.slug)}`;
  elements.detailSlug.textContent = task.slug;
  elements.taskGoalError.classList.add("is-hidden");
  elements.taskProjectError.classList.add("is-hidden");
  elements.taskProjectSelect.replaceChildren();
  const noProjectOption = node("option", "", "No project");
  noProjectOption.value = "";
  elements.taskProjectSelect.append(noProjectOption);
  state.projects.forEach((project) => {
    const option = node("option", "", project.title);
    option.value = project.slug;
    elements.taskProjectSelect.append(option);
  });
  elements.taskProjectSelect.value = task.project || "";
  elements.taskProjectSelect.dataset.currentProject = task.project || "";
  elements.taskProjectSave.disabled = true;
  elements.taskGoalSelect.replaceChildren();
  const emptyOption = node("option", "", "No linked goal");
  emptyOption.value = "";
  elements.taskGoalSelect.append(emptyOption);
  state.snapshot.goals
    .filter(
      (goal) =>
        ["planned", "active"].includes(goal.status) ||
        goal.slug === task.goal,
    )
    .forEach((goal) => {
    const option = node("option", "", goal.title);
    option.value = goal.slug;
    elements.taskGoalSelect.append(option);
    });
  elements.taskGoalSelect.value = task.goal || "";
  const linkedGoal = state.snapshot.goals.find((goal) => goal.slug === task.goal);
  if (linkedGoal) {
    elements.taskGoalNav.textContent = `View goal · ${linkedGoal.title}`;
    elements.taskGoalNav.classList.remove("is-hidden");
    elements.taskGoalNav.onclick = () => selectGoal(linkedGoal.slug);
  } else {
    elements.taskGoalNav.classList.add("is-hidden");
    elements.taskGoalNav.onclick = null;
  }
  render();
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
      "Open a task below and Save its current goal to repair both relationship directions.";
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
  elements.goalDetailStatus.textContent = goal.status;
  elements.goalPauseButton.disabled = goal.status === "paused";
  elements.goalPauseButton.textContent =
    goal.status === "paused" ? "Paused" : "Pause";
  elements.goalActionError.classList.add("is-hidden");
  elements.goalDetailTitle.textContent = goal.title;
  elements.goalDetailOutcome.textContent = goal.outcome;
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
  const task = state.snapshot?.tasks.find((item) => item.slug === taskSlug);
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
    const currentTask = state.snapshot.tasks.find(
      (task) => task.slug === taskSlug,
    );
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
      error.message || "GTasks could not read the approved task collections.",
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

function openQuickAdd() {
  elements.quickAddForm.reset();
  elements.quickAddError.classList.add("is-hidden");
  const today = state.snapshot?.as_of;
  elements.dueDefaultCopy.textContent = today
    ? `Leave blank and GTasks will use today, ${formatDay(today, "long")}.`
    : "Leave blank and GTasks will use Tony’s local creation day.";
  elements.quickAddDialog.showModal();
  window.setTimeout(() => elements.taskTitle.focus(), 0);
}

async function submitQuickAdd(event) {
  event.preventDefault();
  elements.quickAddError.classList.add("is-hidden");
  elements.quickAddSubmit.disabled = true;
  elements.quickAddSubmit.textContent = "Adding…";

  const payload = { title: elements.taskTitle.value };
  if (elements.taskDueDay.value) payload.due_day = elements.taskDueDay.value;

  try {
    const response = await fetch("/api/tasks", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(result.error || "Task could not be added.");
      error.code = result.code;
      error.slug = result.slug;
      throw error;
    }
    elements.quickAddDialog.close();
    const dueCopy =
      result.due_day_source === "task_creation_day"
        ? `due today (${formatDay(result.task.due_day)})`
        : `due ${formatDay(result.task.due_day)}`;
    showToast(`Added “${result.task.title}” to GBrain, ${dueCopy}.`);
    await loadTasks();
    selectTask(result.task.slug);
  } catch (error) {
    let message = error.message;
    if (error.code === "partial_write" && error.slug) {
      message = `${message} Do not retry yet; inspect ${error.slug} first.`;
    }
    elements.quickAddError.textContent = message;
    elements.quickAddError.classList.remove("is-hidden");
  } finally {
    elements.quickAddSubmit.disabled = false;
    elements.quickAddSubmit.textContent = "Add to Inbox";
  }
}

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => setView(button.dataset.view));
});
elements.quickAddButton.addEventListener("click", openQuickAdd);
elements.quickAddClose.addEventListener("click", () => elements.quickAddDialog.close());
elements.quickAddForm.addEventListener("submit", submitQuickAdd);
elements.refreshButton.addEventListener("click", () => {
  loadTasks({ reason: "manual" });
});
elements.detailClose.addEventListener("click", closeDetails);
elements.goalDetailClose.addEventListener("click", closeDetails);
elements.taskGoalSave.addEventListener("click", saveTaskGoal);
elements.taskStatusSave.addEventListener("click", saveTaskStatus);
elements.taskNextActionSave.addEventListener("click", saveTaskNextAction);
elements.taskProjectSave.addEventListener("click", saveTaskProject);
elements.taskProjectSelect.addEventListener("change", () => {
  elements.taskProjectError.classList.add("is-hidden");
  elements.taskProjectSave.disabled =
    elements.taskProjectSelect.value ===
    elements.taskProjectSelect.dataset.currentProject;
});
elements.newProjectClose.addEventListener("click", () => {
  elements.newProjectDialog.close();
});
elements.newProjectForm.addEventListener("submit", submitNewProject);
elements.newGoalClose.addEventListener("click", () => {
  elements.newGoalDialog.close();
});
elements.newGoalForm.addEventListener("submit", submitNewGoal);
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
elements.boardStatusRetry.addEventListener("click", () => {
  const move = state.boardMove;
  if (move?.phase === "error") moveBoardTask(move.taskSlug, move.status);
});
elements.taskStatusSelect.addEventListener("change", () => {
  elements.taskStatusError.classList.add("is-hidden");
  elements.taskStatusSave.disabled =
    elements.taskStatusSelect.value ===
    elements.taskStatusSelect.dataset.currentStatus;
});
elements.taskNextActionInput.addEventListener("input", () => {
  elements.taskNextActionError.classList.add("is-hidden");
  elements.taskNextActionSave.disabled =
    elements.taskNextActionInput.value.trim() ===
    elements.taskNextActionInput.dataset.currentValue;
});
elements.taskNextActionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !elements.taskNextActionSave.disabled) {
    event.preventDefault();
    saveTaskNextAction();
  }
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
    openQuickAdd();
  }
});

loadReleases();
loadTasks({ reason: "initial" });
loadProjects();
