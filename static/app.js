const state = {
  snapshot: null,
  activeView: "today",
  selectedSlug: null,
  selectedKind: null,
  loading: true,
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
  refreshButton: document.querySelector("#refresh-button"),
  issueNotice: document.querySelector("#issue-notice"),
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
  detailTitle: document.querySelector("#detail-title"),
  detailCopy: document.querySelector("#detail-copy"),
  detailProject: document.querySelector("#detail-project"),
  detailPriority: document.querySelector("#detail-priority"),
  detailNextAction: document.querySelector("#detail-next-action"),
  detailDue: document.querySelector("#detail-due"),
  detailGbrainLink: document.querySelector("#detail-gbrain-link"),
  detailSlug: document.querySelector("#detail-slug"),
  taskGoalSelect: document.querySelector("#task-goal-select"),
  taskGoalSave: document.querySelector("#task-goal-save"),
  taskGoalError: document.querySelector("#task-goal-error"),
  taskGoalNav: document.querySelector("#task-goal-nav"),
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
  goalActiveTasks: document.querySelector("#goal-active-tasks"),
  goalCompletedTasks: document.querySelector("#goal-completed-tasks"),
  goalGbrainLink: document.querySelector("#goal-gbrain-link"),
  goalDetailSlug: document.querySelector("#goal-detail-slug"),
  toast: document.querySelector("#toast"),
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

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.remove("is-hidden");
  window.clearTimeout(showToast.timeout);
  showToast.timeout = window.setTimeout(() => {
    elements.toast.classList.add("is-hidden");
  }, 3400);
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

function navCounts() {
  if (!state.snapshot) return {};
  return {
    inbox: state.snapshot.views.inbox.length,
    today: new Set(allTodayTasks().map((task) => task.slug)).size,
    board: state.snapshot.tasks.length,
    upcoming: state.snapshot.views.upcoming.length,
    blocked: state.snapshot.views.blocked.length,
    projects: state.snapshot.views.projects.length,
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
  const dot = node("span", `task-state-dot ${task.status}`);
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
      "There are no started, due-today, waiting, blocked, or overdue tasks. Add what matters now, or choose something from the Inbox.",
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
  if (!state.snapshot.goals.length) {
    wrapper.append(node("div", "section-empty", "No GBrain goals are linked to Tony’s Goals."));
    return wrapper;
  }
  const rail = node("div", "goals-rail");
  state.snapshot.goals.forEach((goal) => rail.append(goalMiniCard(goal)));
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
      "Waiting and Blocked",
      groups.waiting_and_blocked,
      "Nothing is waiting on someone or something else.",
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
    statuses: ["planned"],
    empty: "No planned tasks.",
  },
  {
    title: "In Progress",
    statuses: ["active"],
    empty: "No task is in progress.",
  },
  {
    title: "Waiting / Blocked",
    statuses: ["waiting", "blocked"],
    empty: "Nothing is waiting or blocked.",
  },
  {
    title: "Completed / Cancelled",
    statuses: ["completed", "cancelled"],
    empty: "No finished tasks are shown.",
  },
];

function boardCard(task) {
  const button = node("button", "board-card");
  button.type = "button";
  button.dataset.slug = task.slug;
  button.classList.toggle("is-selected", state.selectedSlug === task.slug);
  const heading = node("span", "board-card-heading");
  heading.append(
    node("span", `task-state-dot ${task.status}`),
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
  return button;
}

function renderBoard() {
  const board = node("section", "board-grid");
  board.setAttribute("aria-label", "Task status board");
  boardColumns.forEach((definition) => {
    const tasks = state.snapshot.tasks.filter((task) =>
      definition.statuses.includes(task.status));
    const column = node("section", "board-column");
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
  if (!state.snapshot.goals.length) return simpleEmpty(viewMeta.goals);
  const fragment = document.createDocumentFragment();
  const note = node("div", "notice");
  note.textContent =
    "Goal target dates default to the final calendar day of their creation quarter when omitted.";
  fragment.append(note);
  const grid = node("div", "goals-grid");
  state.snapshot.goals.forEach((goal) => grid.append(goalCard(goal)));
  fragment.append(grid);
  return fragment;
}

function render() {
  renderNavigation();
  elements.viewTitle.textContent = viewMeta[state.activeView].title;
  if (state.loading) return;
  if (!state.snapshot) return;

  const view = state.activeView;
  elements.viewSurface.replaceChildren(
    view === "today"
      ? renderToday()
      : view === "board"
        ? renderBoard()
      : view === "goals"
        ? renderGoalsView()
        : renderListView(view),
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
  elements.taskStatusSelect.value = task.status;
  elements.taskStatusSelect.dataset.currentStatus = task.status;
  elements.taskStatusSave.disabled = true;
  elements.taskStatusError.classList.add("is-hidden");
  elements.detailTitle.textContent = task.title || task.summary;
  elements.detailCopy.textContent = task.detail || "No additional detail yet.";
  elements.detailProject.textContent = task.project || "No project";
  elements.detailPriority.textContent = task.priority;
  elements.detailNextAction.textContent = task.next_action || "Not set";
  elements.detailDue.textContent = formatDay(task.due_day, "long");
  elements.detailGbrainLink.href = `http://127.0.0.1:8788/?slug=${encodeURIComponent(task.slug)}`;
  elements.detailSlug.textContent = task.slug;
  elements.taskGoalError.classList.add("is-hidden");
  elements.taskGoalSelect.replaceChildren();
  const emptyOption = node("option", "", "No linked goal");
  emptyOption.value = "";
  elements.taskGoalSelect.append(emptyOption);
  state.snapshot.goals.forEach((goal) => {
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

async function saveTaskStatus() {
  if (state.selectedKind !== "task" || !state.selectedSlug) return;
  const taskSlug = state.selectedSlug;
  const status = elements.taskStatusSelect.value;
  elements.taskStatusError.classList.add("is-hidden");
  elements.taskStatusSave.disabled = true;
  elements.taskStatusSave.textContent = "Saving…";
  try {
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
    showToast(`Status saved as ${status} in GBrain.`);
    await loadTasks();
    selectTask(taskSlug);
  } catch (error) {
    let message = error.message;
    if (error.code === "partial_write" && error.slug) {
      message = `${message} Do not retry yet; inspect ${error.slug} first.`;
    }
    elements.taskStatusError.textContent = message;
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

async function loadTasks() {
  state.loading = true;
  elements.syncLabel.textContent = "Reading GBrain…";
  elements.refreshButton.disabled = true;
  setConnection("loading", "Connecting");
  if (!state.snapshot) {
    const loading = node("div", "loading-state");
    loading.append(node("span"), node("span"), node("span"));
    elements.viewSurface.replaceChildren(loading);
  }
  try {
    const response = await fetch("/api/tasks", {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to read GBrain.");
    state.snapshot = payload;
    setConnection("connected", "GBrain connected");
    elements.syncLabel.textContent = `Read ${payload.tasks.length} task${payload.tasks.length === 1 ? "" : "s"}`;
    if (payload.issues.length) {
      elements.issueNotice.textContent =
        `${payload.issues.length} linked task ${payload.issues.length === 1 ? "needs" : "need"} attention and was not shown.`;
      elements.issueNotice.classList.remove("is-hidden");
    } else {
      elements.issueNotice.classList.add("is-hidden");
    }
  } catch (error) {
    state.snapshot = null;
    setConnection("error", "GBrain unavailable");
    elements.syncLabel.textContent = "Read failed";
    showLoadError(error);
  } finally {
    state.loading = false;
    elements.refreshButton.disabled = false;
    if (state.snapshot) render();
  }
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
elements.refreshButton.addEventListener("click", loadTasks);
elements.detailClose.addEventListener("click", closeDetails);
elements.goalDetailClose.addEventListener("click", closeDetails);
elements.taskGoalSave.addEventListener("click", saveTaskGoal);
elements.taskStatusSave.addEventListener("click", saveTaskStatus);
elements.taskStatusSelect.addEventListener("change", () => {
  elements.taskStatusError.classList.add("is-hidden");
  elements.taskStatusSave.disabled =
    elements.taskStatusSelect.value ===
    elements.taskStatusSelect.dataset.currentStatus;
});

document.addEventListener("keydown", (event) => {
  const editing =
    event.target instanceof HTMLInputElement ||
    event.target instanceof HTMLTextAreaElement ||
    event.target instanceof HTMLSelectElement;
  if (!editing && event.key.toLowerCase() === "n" && !event.metaKey && !event.ctrlKey) {
    event.preventDefault();
    openQuickAdd();
  }
});

loadTasks();
