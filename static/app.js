const AUTO_REFRESH_MINUTES = 30;
const AUTO_REFRESH_INTERVAL_MS = AUTO_REFRESH_MINUTES * 60 * 1000;
const TASK_DETAIL_READ_TIMEOUT_MS = 15 * 1000;
const MEMORY_STARGRAPH_ORIGIN = "http://127.0.0.1:8788";
const HANDOFF_EVENT_PAGE_SIZE = 50;
const MISSING_LINKED_TASK_ERROR = "Linked Task could not be read from canonical or Agent work.";
const HANDOFF_STATUS_FILTER_OPTIONS = [
  ["", "All statuses"],
  ["queued", "Queued"],
  ["suppressed", "Suppressed"],
  ["leased", "Leased"],
  ["received", "Received"],
  ["actively_executing", "Actively executing"],
  ["still_blocked", "Still blocked"],
  ["completed", "Completed"],
  ["retrying", "Retrying"],
  ["dead_letter", "Dead letter"],
];
const HANDOFF_EVENT_FILTER_OPTIONS = [
  ["", "All events"],
  ["handoff_queued", "Handoff queued"],
  ["handoff_suppressed", "Handoff suppressed"],
  ["handoff_leased", "Handoff leased"],
  ["acknowledgement", "Acknowledgement"],
  ["delivery_retry", "Delivery retry"],
  ["delivery_terminal", "Delivery terminal"],
  ["capability_rotated", "Capability rotated"],
  ["lease_expired", "Lease expired"],
  ["correction", "Correction"],
];

function safeSystemTicketMarkdownRoute(value) {
  const match = String(value || "").match(/^#system-ticket\/(tasks%2F[0-9a-f-]{36})$/i);
  if (!match) return null;
  const slug = decodeURIComponent(match[1]);
  return /^tasks\/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(slug)
    ? slug
    : null;
}

function safeExternalMarkdownUrl(value) {
  const source = String(value || "");
  try {
    const url = new URL(source);
    if (url.protocol === "https:") return source;
    if (url.protocol === "http:" && ["127.0.0.1", "localhost"].includes(url.hostname)) return source;
  } catch (_error) {
    // Invalid authored URLs remain authored text.
  }
  return null;
}

function splitBareMarkdownUrl(value) {
  let url = String(value || "");
  let trailing = "";
  while (".,;:!?)}]>".includes(url.slice(-1))) {
    trailing = url.slice(-1) + trailing;
    url = url.slice(0, -1);
  }
  return { url, trailing };
}

function decodeSafeMarkdownText(value) {
  const named = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" };
  return String(value || "")
    .replace(/\\([!-/:-@[-`{-~])/g, "$1")
    .replace(
      /&(?:#([0-9]{1,7})|#x([0-9a-f]{1,6})|(amp|lt|gt|quot|apos));/gi,
      (entity, decimal, hexadecimal, name) => {
        if (name) return named[name.toLowerCase()];
        const codePoint = Number.parseInt(decimal || hexadecimal, hexadecimal ? 16 : 10);
        if (
          !Number.isInteger(codePoint) || codePoint < 1 || codePoint > 0x10ffff ||
          (codePoint >= 0xd800 && codePoint <= 0xdfff)
        ) return entity;
        return String.fromCodePoint(codePoint);
      },
    );
}

async function openMarkdownSystemTicketReference(ticketSlug, originControl = null) {
  const sourceKind = originControl?.dataset?.systemTicketReferenceSourceKind || "";
  const sourceSlug = originControl?.dataset?.systemTicketReferenceSourceSlug || "";
  const referenceKey = originControl?.dataset?.systemTicketReferenceKey || "";
  const markdownReturn = sourceKind && sourceSlug && referenceKey
    ? {
      sourceKind,
      sourceSlug,
      destinationTicketSlug: ticketSlug,
      referenceKey,
      detailReturnFocus: state.detailReturnFocus,
      parent: state.systemTicketMarkdownReturn,
    }
    : null;
  const ticket = await loadCorrelatedSystemTicket(ticketSlug);
  if (ticket) {
    selectSystemTicket(ticketSlug, originControl);
    if (markdownReturn) state.systemTicketMarkdownReturn = markdownReturn;
    return;
  }
  showToast(MISSING_LINKED_TASK_ERROR);
  if (originControl instanceof HTMLElement) originControl.focus();
}

function renderSafeMarkdown(container, value, systemTicketReference = null) {
  const source = typeof value === "string" ? value : "";
  let internalReferenceIndex = 0;
  container.replaceChildren();
  const lines = source.split(/\r?\n/);
  const appendInline = (target, text) => {
    const pattern = /\[([^\]]{1,240})\]\(([^)\s]+)\)|\*\*([^*\n]+)\*\*|(?<!`)(`+)(?!`)([^\n]*?)(?<!`)\4(?!`)|(https:\/\/[^\s<]*|http:\/\/[^\s<]*)/g;
    let cursor = 0;
    for (const match of text.matchAll(pattern)) {
      target.append(document.createTextNode(decodeSafeMarkdownText(text.slice(cursor, match.index))));
      if (match[3] !== undefined) {
        const strong = document.createElement("strong");
        strong.textContent = decodeSafeMarkdownText(match[3]);
        target.append(strong);
      } else if (match[4] !== undefined) {
        const code = document.createElement("code");
        code.textContent = match[5];
        target.append(code);
      } else {
        const url = match[2] || match[6];
        const ticketSlug = safeSystemTicketMarkdownRoute(url);
        const bareUrl = match[6] ? splitBareMarkdownUrl(url) : null;
        let href = null;
        if (url.startsWith("/media/")) {
          const mediaUrl = safeStargraphMediaUrl(url);
          if (mediaUrl && /\.(?:png|jpe?g|gif|webp|pdf)$/i.test(mediaUrl.pathname)) {
            href = mediaUrl.href;
          }
        } else if (!ticketSlug) {
          href = safeExternalMarkdownUrl(bareUrl?.url || url);
        }
        if (ticketSlug) {
          const link = document.createElement("a");
          link.href = url;
          link.textContent = decodeSafeMarkdownText(match[1]);
          if (
            systemTicketReference?.sourceKind &&
            systemTicketReference?.sourceSlug &&
            systemTicketReference.referenceScope
          ) {
            link.dataset.systemTicketReferenceSourceKind = systemTicketReference.sourceKind;
            link.dataset.systemTicketReferenceSourceSlug = systemTicketReference.sourceSlug;
            link.dataset.systemTicketReferenceKey =
              `${systemTicketReference.referenceScope}:${internalReferenceIndex}`;
            internalReferenceIndex += 1;
          }
          link.addEventListener("click", (event) => {
            event.preventDefault();
            void openMarkdownSystemTicketReference(ticketSlug, link);
          });
          target.append(link);
        } else if (href) {
          const link = document.createElement("a");
          link.href = href;
          link.textContent = match[1]
            ? decodeSafeMarkdownText(match[1])
            : bareUrl?.url || href;
          link.target = "_blank";
          link.rel = "noreferrer";
          target.append(link);
          if (bareUrl?.trailing) target.append(document.createTextNode(bareUrl.trailing));
        } else target.append(document.createTextNode(match[0]));
      }
      cursor = match.index + match[0].length;
    }
    target.append(document.createTextNode(decodeSafeMarkdownText(text.slice(cursor))));
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
    const fence = line.match(/^[ \t]{0,3}(`{3,}|~{3,})(.*)$/);
    if (fence) {
      flushBlocks();
      const fencedLines = [];
      const fenceCharacter = fence[1][0];
      const fenceLength = fence[1].length;
      const closingFence = new RegExp(
        `^[ \\t]{0,3}${fenceCharacter}{${fenceLength},}[ \\t]*$`,
      );
      index += 1;
      while (index < lines.length && !closingFence.test(lines[index])) {
        fencedLines.push(lines[index]);
        index += 1;
      }
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      code.textContent = fencedLines.join("\n");
      const language = fence[2].trim().split(/\s+/, 1)[0];
      if (/^[A-Za-z0-9_+-]+$/.test(language)) code.dataset.language = language;
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
const COMPLETION_CELEBRATION_PREFERENCE_KEY = "mission-control.completion-celebration";
const DEFAULT_LANDING_VIEW_PREFERENCE_KEY = "mission-control.default-landing-view";
const DEFAULT_LANDING_VIEW = "board";
const BOARD_DATE_WINDOW_SESSION_KEY = "mission-control.board-date-window";
const BOARD_DATE_WINDOW_DEFAULT_DAYS = 3;
const BOARD_DATE_WINDOW_VALUES = new Set(["3", "7", "14", "30", "all"]);
const DETAIL_WIDTH_PREFERENCE_KEY = "mission-control.detail-panel-width";
const DETAIL_WIDTH_DEFAULT = 344;
const DETAIL_WIDTH_MIN = 292;
const DETAIL_WIDTH_MAX = 720;
const COMPLETION_CELEBRATION_COOLDOWN_MS = 8000;
const COMPLETION_CELEBRATION_MAX_VISIBLE = 3;

const LANDING_VIEW_VALUES = new Set([
  "today", "week", "board", "inbox", "agent-work", "artifacts", "blocked",
  "completed", "all", "projects", "goals", "system-tickets",
]);

function storedDefaultLandingView() {
  try {
    const saved = window.localStorage.getItem(DEFAULT_LANDING_VIEW_PREFERENCE_KEY);
    return LANDING_VIEW_VALUES.has(saved) ? saved : DEFAULT_LANDING_VIEW;
  } catch (_) {
    return DEFAULT_LANDING_VIEW;
  }
}

function setDefaultLandingView(value) {
  const view = LANDING_VIEW_VALUES.has(value) ? value : DEFAULT_LANDING_VIEW;
  try {
    window.localStorage.setItem(DEFAULT_LANDING_VIEW_PREFERENCE_KEY, view);
  } catch (_) {
    // This display preference never reaches GBrain.
  }
  return view;
}

function explicitViewFromLocation(locationLike = window.location) {
  try {
    const params = new URLSearchParams(locationLike?.search || "");
    const queryView = params.get("view");
    if (LANDING_VIEW_VALUES.has(queryView)) return queryView;
    const hashView = String(locationLike?.hash || "").match(/^#view=([a-z-]+)$/)?.[1];
    if (LANDING_VIEW_VALUES.has(hashView)) return hashView;
  } catch (_) {
    // Invalid browser location state falls through to the stored preference.
  }
  return null;
}

function resolveInitialView(locationLike = window.location) {
  // Explicit route and deep-link selection wins over the local landing preference.
  return explicitViewFromLocation(locationLike) || storedDefaultLandingView();
}

function readBoardDateWindowPreference() {
  try {
    const value = window.sessionStorage?.getItem(BOARD_DATE_WINDOW_SESSION_KEY);
    return BOARD_DATE_WINDOW_VALUES.has(value) ? value : "3";
  } catch (_) {
    return "3";
  }
}

function setBoardDateWindowPreference(value) {
  const windowValue = BOARD_DATE_WINDOW_VALUES.has(value) ? value : "3";
  state.boardDateWindow = windowValue;
  try {
    window.sessionStorage?.setItem(BOARD_DATE_WINDOW_SESSION_KEY, windowValue);
  } catch (_) {
    // A session-only view filter never mutates canonical Task data.
  }
}

const state = {
  snapshot: null,
  activeView: "board",
  selectedSlug: null,
  selectedKind: null,
  detailReturnFocus: null,
  detailFocusReturnAnchor: null,
  systemTicketMarkdownReturn: null,
  artifactTaskReturn: null,
  artifactProducingTaskReturn: null,
  goalTaskReturn: null,
  goalTaskFocusSlug: null,
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
  boardDateWindow: readBoardDateWindowPreference(),
  completionCelebrations: [],
  completionCelebrationLastFullAt: 0,
  completionCelebrationSequence: 0,
  loading: true,
  releases: null,
  aboutReturnFocus: null,
  logsReturnFocus: null,
  logEvents: [],
  logsNextCursor: null,
  logsLoading: false,
  tasksLoadPromise: null,
  tasksReadState: null,
  taskDetailReadSlug: null,
  taskDetailReadPromise: null,
  taskDetailReadToken: 0,
  taskDetailReadController: null,
  taskDetailReadWatchdogTimer: null,
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
  projectsLoadPromise: null,
  projectsReadState: null,
  projectSurfacePollTimer: null,
  projectsError: "",
  projectEditorSlug: null,
  goalAction: null,
  goalEditorSlug: null,
  pendingWarning: null,
  showDismissedWarnings: false,
  taskEditorMode: "create",
  taskEditorSourceSlug: null,
  taskEditorMetricContext: null,
  agents: [],
  agentsLoaded: false,
  agentsLoading: false,
  agentsLoadPromise: null,
  delegations: [],
  delegationsLoaded: false,
  delegationsLoading: false,
  delegationsError: "",
  agentTasks: [],
  agentProfileIssues: [],
  agentWorkIssues: [],
  agentIssues: [],
  agentWorkLoaded: false,
  agentWorkLoading: false,
  agentWorkLoadPromise: null,
  agentWorkReadState: null,
  agentWorkSurfacePollTimer: null,
  agentWorkError: "",
  goalExecution: null,
  goalExecutionLoaded: false,
  goalExecutionLoading: false,
  goalExecutionLoadPromise: null,
  goalExecutionError: "",
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
  taskHandoffEvents: new Map(),
  handoffLogEvents: [],
  handoffLogTotal: 0,
  handoffLogSnapshotTotal: 0,
  handoffLogNextSequence: null,
  handoffLogLoading: false,
  handoffLogError: "",
  handoffLogStale: false,
  handoffLogRequestToken: 0,
  handoffLogFocusKey: null,
  handoffLogTimeRange: null,
  agentHandoffHistoryOpen: false,
  handoffLogFilters: {
    time: "all",
    agent_slug: "",
    status: "",
    event_type: "",
    failure: "",
    correlation_id: "",
  },
};

function syncAgentIssues() {
  const seen = new Set();
  state.agentIssues = [
    ...state.agentProfileIssues,
    ...state.agentWorkIssues,
  ].filter((issue) => {
    const identity = issue?.fingerprint ||
      `${issue?.slug || ""}:${issue?.category || ""}:${issue?.message || ""}:${issue?.impact || ""}`;
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
}

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

function detailPanelWidthBounds(kind = state.selectedKind) {
  const minimumMainWidth = kind === "artifact" ? 220 : 320;
  const configuredMaximum = kind === "artifact"
    ? window.innerWidth - 92 - minimumMainWidth
    : DETAIL_WIDTH_MAX;
  const maximum = Math.max(
    DETAIL_WIDTH_MIN,
    Math.min(configuredMaximum, window.innerWidth - 92 - minimumMainWidth),
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
    kind === "artifact" ? Math.round(window.innerWidth * 0.78) : DETAIL_WIDTH_DEFAULT,
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
  setDetailPanelWidth(readDetailPanelWidth(), { persist: false });
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
  window.addEventListener("resize", () => setDetailPanelWidth(
    readDetailPanelWidth(),
    { persist: false },
  ));
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
  settings: {
    title: "Settings",
  },
};

state.activeView = resolveInitialView();

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
  taskMetricUseMinimum: document.querySelector("#task-metric-use-minimum"),
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
  artifactDetailTaskLink: document.querySelector("#artifact-detail-task-link"),
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
  projectDetailExecution: document.querySelector("#project-detail-execution"),
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
  systemTicketDetailMarkdownSection: document.querySelector("#system-ticket-detail-markdown-section"),
  systemTicketDetailMarkdown: document.querySelector("#system-ticket-detail-markdown"),
  systemTicketDetailStructured: document.querySelector("#system-ticket-detail-structured"),
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
  taskTemporaryExecutor: document.querySelector("#task-temporary-executor"),
  taskExecutorAvatar: document.querySelector("#task-executor-avatar"),
  taskExecutorName: document.querySelector("#task-executor-name"),
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
  taskDetailRetry: document.querySelector("#task-detail-retry"),
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
  taskHandoffTimeline: document.querySelector("#task-handoff-timeline"),
  taskHandoffTimelineHeading: document.querySelector("#task-handoff-timeline-heading"),
  taskHandoffTotal: document.querySelector("#task-handoff-total"),
  taskHandoffEventState: document.querySelector("#task-handoff-event-state"),
  taskHandoffEventList: document.querySelector("#task-handoff-event-list"),
  taskHandoffLoadMore: document.querySelector("#task-handoff-load-more"),
  goalDetailClose: document.querySelector("#goal-detail-close"),
  goalDetailStatus: document.querySelector("#goal-detail-status"),
  goalDetailTitle: document.querySelector("#goal-detail-title"),
  goalDetailOutcome: document.querySelector("#goal-detail-outcome"),
  goalDetailExecution: document.querySelector("#goal-detail-execution"),
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
  completionCelebrationPreference: document.querySelector("#completion-celebration-preference"),
  completionCelebrationRegion: document.querySelector("#completion-celebration-region"),
  toast: document.querySelector("#toast"),
  aboutButton: document.querySelector("#about-button"),
  settingsButton: document.querySelector("#settings-button"),
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

function completionCelebrationStoredPreference() {
  try {
    const value = window.localStorage.getItem(COMPLETION_CELEBRATION_PREFERENCE_KEY);
    if (["full", "reduced", "off"].includes(value)) return value;
  } catch (_) {
    // Browser-local preferences are optional and must not affect canonical Task writes.
  }
  return "full";
}

function setCompletionCelebrationPreference(value) {
  const preference = ["full", "reduced", "off"].includes(value) ? value : "full";
  try {
    window.localStorage.setItem(COMPLETION_CELEBRATION_PREFERENCE_KEY, preference);
  } catch (_) {
    // The current page session can continue even when local storage is unavailable.
  }
  const control =
    elements.completionCelebrationPreference ||
    document.querySelector("#completion-celebration-preference");
  if (control) {
    control.value = preference;
  }
}

function completionCelebrationMode() {
  const preference = completionCelebrationStoredPreference();
  if (preference === "off") return "off";
  const reducedMotion = Boolean(
    window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches,
  );
  if (preference === "reduced" || reducedMotion) return "reduced";
  return "full";
}

function renderCompletionCelebrations() {
  const region = elements.completionCelebrationRegion;
  if (!region) return;
  region.replaceChildren();
  if (!state.completionCelebrations.length) {
    region.classList.add("is-hidden");
    region.textContent = "";
    return;
  }
  region.classList.remove("is-hidden");
  const list = node("div", "completion-celebration-stack");
  state.completionCelebrations.forEach((celebration) => {
    const card = node("article", `completion-celebration-card is-${celebration.mode}`);
    const scan = node("span", "completion-celebration-scan");
    scan.setAttribute("aria-hidden", "true");
    const icon = node("span", "completion-celebration-icon", "✓");
    icon.setAttribute("aria-hidden", "true");
    const copy = node("span", "completion-celebration-copy", celebration.message);
    card.append(scan, icon, copy);
    list.append(card);
  });
  region.append(list);
}

function clearCompletionCelebration(sequence) {
  state.completionCelebrations = state.completionCelebrations.filter(
    (celebration) => celebration.sequence !== sequence,
  );
  renderCompletionCelebrations();
}

function recordCompletionCelebration(task, { bulkCount = 1 } = {}) {
  const mode = completionCelebrationMode();
  if (mode === "off") return;
  const now = Date.now();
  const canUseFull =
    mode === "full" &&
    now - state.completionCelebrationLastFullAt >= COMPLETION_CELEBRATION_COOLDOWN_MS;
  const visualMode = canUseFull ? "full" : "reduced";
  if (canUseFull) state.completionCelebrationLastFullAt = now;
  state.completionCelebrationSequence += 1;
  const title = task?.title || task?.summary || task?.slug || "Task";
  const message = bulkCount > 1
    ? `Mission accomplished — ${bulkCount} tasks completed`
    : `Mission accomplished — ${title}`;
  const celebration = {
    sequence: state.completionCelebrationSequence,
    taskSlug: task.slug,
    mode: visualMode,
    message,
    createdAt: now,
  };
  state.completionCelebrations = [
    celebration,
    ...state.completionCelebrations,
  ].slice(0, COMPLETION_CELEBRATION_MAX_VISIBLE);
  renderCompletionCelebrations();
  window.setTimeout(
    () => clearCompletionCelebration(celebration.sequence),
    visualMode === "full" ? 5200 : 3600,
  );
}

function maybeCelebrateVerifiedTaskCompletion(previousTask, verifiedTask, { requestedStatus } = {}) {
  if (
    !previousTask ||
    !verifiedTask ||
    previousTask.slug !== verifiedTask.slug ||
    previousTask.status === "completed" ||
    requestedStatus !== "completed" ||
    verifiedTask.status !== "completed"
  ) return;
  recordCompletionCelebration(verifiedTask);
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
  window.setTimeout(() => {
    elements.aboutClose.focus();
    setAppShellModalIsolation(true);
    window.requestAnimationFrame(() => {
      if (elements.aboutDialog.open) setAppShellModalIsolation(true);
    });
  }, 0);
}

function closeAboutDialog() {
  elements.aboutDialog.close();
}

function setAppShellModalIsolation(isModal) {
  elements.appShell.inert = isModal;
  elements.appShell.setAttribute("aria-hidden", isModal ? "true" : "false");
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
  window.setTimeout(() => {
    elements.logsClose.focus();
    setAppShellModalIsolation(true);
    window.requestAnimationFrame(() => {
      if (elements.logsDialog.open) setAppShellModalIsolation(true);
    });
  }, 0);
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
  const snapshotTask = state.snapshot?.tasks.find((candidate) => candidate.slug === slug) || null;
  const agentTask = state.agentTasks.find((candidate) => candidate.slug === slug) || null;
  const canonical = (
    snapshotTask && agentTask
      ? { ...snapshotTask, ...agentTask }
      : snapshotTask || agentTask
  );
  if (canonical) return canonical;
  const lastRunTask = state.goalExecution?.last_run?.task;
  if (lastRunTask?.slug === slug) {
    const ownerAgent = lastRunTask.owner_agent || lastRunTask.agent_slug || null;
    return {
      ...lastRunTask,
      summary: lastRunTask.summary || lastRunTask.title || slug,
      owner_agent: ownerAgent,
      owner: ownerAgent
        ? state.agents.find((agent) => agent.slug === ownerAgent) || lastRunTask.owner || null
        : lastRunTask.owner || null,
      todos: Array.isArray(lastRunTask.todos) ? lastRunTask.todos : [],
      open_todos: Array.isArray(lastRunTask.open_todos) ? lastRunTask.open_todos : [],
      artifacts: Array.isArray(lastRunTask.artifacts) ? lastRunTask.artifacts : [],
    };
  }
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

function projectsColdLoading() {
  const readState = state.projectsReadState;
  return (
    !state.projectsLoaded &&
    !state.projects.length &&
    (
      state.projectsLoading ||
      readState?.status === "loading" ||
      readState?.refreshing === true
    )
  );
}

function agentWorkColdLoading() {
  const readState = state.agentWorkReadState;
  return (
    !state.agentWorkLoaded &&
    !state.agentTasks.length &&
    (
      state.agentWorkLoading ||
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
  const settingsActive = state.activeView === "settings";
  elements.settingsButton.classList.toggle("is-active", settingsActive);
  elements.settingsButton.setAttribute("aria-current", settingsActive ? "page" : "false");
  elements.settingsButton.setAttribute("aria-pressed", settingsActive ? "true" : "false");
}

function inContextCountLabel(view) {
  if (view === "system-tickets" && systemTicketsColdLoading()) {
    return "Reading System Tickets…";
  }
  if (view === "agent-work" && agentWorkColdLoading()) {
    return "Reading Agent Work…";
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

function verifiedProjectForTask(task) {
  if (!task?.project) return null;
  return state.projects.find((project) => project.slug === task.project) || null;
}

function taskProjectLabel(task) {
  if (!task?.project) return task?.inbox ? "Inbox · No project" : "No project";
  return verifiedProjectForTask(task)?.title || "Project unavailable";
}

function compareNewestUpdated(left, right) {
  const leftTimestamp = String(left.updated_at || left.created_at || "");
  const rightTimestamp = String(right.updated_at || right.created_at || "");
  return rightTimestamp.localeCompare(leftTimestamp)
    || String(left.title || left.summary || "").localeCompare(String(right.title || right.summary || ""))
    || String(left.slug || "").localeCompare(String(right.slug || ""));
}

function snapshotHasProjectReferences(snapshot) {
  if (!snapshot) return false;
  const taskLists = [
    snapshot.tasks || [],
    snapshot.today?.in_progress || [],
    snapshot.today?.todays_actions || [],
    snapshot.today?.overdue || [],
    snapshot.views?.blocked || [],
    snapshot.views?.completed || [],
  ];
  return taskLists.some((tasks) => tasks.some((task) => Boolean(task?.project)));
}

function taskRow(task, {
  todayActions = false,
  calendarWeek = false,
  displayRelevantDate = false,
  showStatus = false,
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
    node("span", "task-project", taskProjectLabel(task)),
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
  if (showStatus) {
    const status = String(task.status || "unknown");
    const statusLabel = {
      planned: "Planned",
      active: "In Progress",
      blocked: "Blocked",
      completed: "Completed",
      cancelled: "Cancelled",
    }[status] || "Status unavailable";
    end.append(node("span", `task-status-badge ${status}`, statusLabel));
  }
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
    .sort(compareNewestUpdated);
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
    visible.forEach((task) => list.append(taskRow(task, {
      displayRelevantDate: true,
      showStatus: true,
    })));
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
  const controls = node("div", "week-controls calendar-toolbar");
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
  const primary = node("div", "calendar-toolbar-primary");
  const navControls = node("div", "calendar-nav-controls");
  navControls.append(weekMode, monthMode, previous, current, next);
  primary.append(
    node("p", "week-range", `${formatDay(isoDay(startDay), "long")} – ${formatDay(isoDay(new Date(endDay.getFullYear(), endDay.getMonth(), endDay.getDate() - 1)), "long")}`),
    navControls,
  );
  controls.append(primary, calendarEventsFilter());
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
  const healthyConnectedCalendar =
    state.icalStatus === "authorized" &&
    state.selectedCalendarIds.length > 0 &&
    !state.icalEventsError;
  const calendarStatus = state.icalConnectionLoading
    ? "Checking Calendar access…"
    : state.icalLoading
      ? "Reading local Calendar…"
      : state.icalStatus === "authorized"
        ? (state.icalEventsError || (state.selectedCalendarIds.length ? "" : "Connected · choose calendars to show events"))
        : state.icalStatus === "denied" || state.icalStatus === "restricted"
          ? "Calendar permission was not granted"
          : state.icalStatus === "unavailable"
            ? (state.icalConnectionError || "Local Calendar is unavailable")
            : "Calendar is not connected";
  if (!healthyConnectedCalendar && calendarStatus) {
    wrapper.append(node("small", "calendar-events-status", calendarStatus));
  }
  if (!healthyConnectedCalendar && state.calendarPreferencesNotice) {
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
    const manage = node("button", "secondary-button", "Manage");
    manage.type = "button";
    manage.setAttribute("aria-label", "Manage connected calendars");
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
  const controls = node("div", "week-controls calendar-toolbar");
  const week = node("button", "secondary-button", "Week"); week.type = "button"; week.addEventListener("click", () => { state.calendarMode = "week"; render(); });
  const month = node("button", "secondary-button", "Month"); month.type = "button"; month.disabled = true;
  const previous = node("button", "secondary-button", "Previous month"); previous.type = "button"; previous.addEventListener("click", () => { state.calendarMonth = isoDay(new Date(monthStart.getFullYear(), monthStart.getMonth() - 1, 1)); render(); });
  const current = node("button", "secondary-button", "This month"); current.type = "button"; current.disabled = monthStart.getFullYear() === parseDay(state.snapshot.as_of).getFullYear() && monthStart.getMonth() === parseDay(state.snapshot.as_of).getMonth(); current.addEventListener("click", () => { state.calendarMonth = null; render(); });
  const next = node("button", "secondary-button", "Next month"); next.type = "button"; next.addEventListener("click", () => { state.calendarMonth = isoDay(new Date(monthStart.getFullYear(), monthStart.getMonth() + 1, 1)); render(); });
  const primary = node("div", "calendar-toolbar-primary");
  const navControls = node("div", "calendar-nav-controls");
  navControls.append(week, month, previous, current, next);
  primary.append(
    node("p", "week-range", new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" }).format(monthStart)),
    navControls,
  );
  controls.append(primary, calendarEventsFilter());
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
      taskButton.dataset.slug = task.slug;
      taskButton.classList.toggle("is-overdue-task", isOverdueExecutable(task));
      taskButton.classList.toggle("is-selected", state.selectedSlug === task.slug);
      taskButton.setAttribute("aria-current", state.selectedSlug === task.slug ? "true" : "false");
      taskButton.setAttribute("aria-description", todoSummary(task));
      taskButton.type = "button"; taskButton.addEventListener("click", () => selectTask(task.slug, null, taskButton)); cell.append(taskButton);
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
  button.dataset.slug = task.slug;
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
    node("span", "", taskProjectLabel(task)),
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
  button.addEventListener("click", () => selectTask(task.slug, null, button));

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
  statusSelect.disabled = isSaving || Boolean(task.read_only);
  statusSelect.addEventListener("change", () => {
    moveBoardTask(task.slug, statusSelect.value);
  });
  moveControl.append(statusSelect);
  card.append(button, moveControl);
  if (isSaving) card.append(node("span", "board-card-saving", "Saving in GBrain…"));

  card.addEventListener("dragstart", (event) => {
    if (!event.dataTransfer || isSaving || task.read_only) {
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
  button.dataset.slug = task.slug;
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
    node("span", "", taskProjectLabel(task)),
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
  button.addEventListener("click", () => selectTask(task.slug, task, button));

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
  statusSelect.disabled = isSaving || Boolean(task.read_only);
  statusSelect.addEventListener("change", () => {
    moveBoardTask(task.slug, statusSelect.value);
  });
  moveControl.append(statusSelect);
  card.append(button, moveControl);
  if (isSaving) {
    card.append(node("span", "board-card-saving", "Saving in GBrain…"));
  }
  card.addEventListener("dragstart", (event) => {
    if (!event.dataTransfer || isSaving || task.read_only) {
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

function boardTaskIsVisible(task, asOf = state.snapshot?.as_of) {
  if (!task || !asOf || state.boardDateWindow === "all") return Boolean(task);
  if (task.status === "active" || task.status === "blocked") return true;
  const relevantDay = task.scheduled_day || task.due_day;
  // Undated actionable tasks stay visible rather than disappearing silently.
  if (!relevantDay) return true;
  const days = Number.parseInt(state.boardDateWindow, 10) || BOARD_DATE_WINDOW_DEFAULT_DAYS;
  const center = parseDay(asOf);
  const start = new Date(center.getFullYear(), center.getMonth(), center.getDate() - days);
  const end = new Date(center.getFullYear(), center.getMonth(), center.getDate() + days);
  return relevantDay >= isoDay(start) && relevantDay <= isoDay(end);
}

function boardDateWindowSummary(allTasks, visibleTasks) {
  const hidden = Math.max(0, allTasks.length - visibleTasks.length);
  if (state.boardDateWindow === "all") {
    return `${visibleTasks.length} shown · all dates`;
  }
  const days = Number.parseInt(state.boardDateWindow, 10) || BOARD_DATE_WINDOW_DEFAULT_DAYS;
  return `${visibleTasks.length} shown · ${hidden} outside ±${days} days hidden · active, blocked, and undated remain visible`;
}

function renderBoardDateWindowControl(allTasks, visibleTasks) {
  const toolbar = node("div", "board-date-window-toolbar");
  const label = node("label", "board-date-window-field");
  label.append(node("span", "", "Date range"));
  const select = document.createElement("select");
  select.id = "board-date-window";
  select.setAttribute("aria-label", "Board task date range");
  [
    ["3", "3 Days Before and After Today"],
    ["7", "One Week Before and After Today"],
    ["14", "Two weeks before and after today"],
    ["30", "Thirty days before and after today"],
    ["all", "All dates"],
  ].forEach(([value, text]) => {
    const option = node("option", "", text);
    option.value = value;
    select.append(option);
  });
  select.value = state.boardDateWindow;
  select.addEventListener("change", () => {
    setBoardDateWindowPreference(select.value);
    render();
  });
  label.append(select);
  const reset = node("button", "secondary-button board-date-window-reset", "Reset");
  reset.type = "button";
  reset.addEventListener("click", () => {
    setBoardDateWindowPreference("3");
    render();
  });
  toolbar.append(label, reset, node("p", "board-date-window-summary", boardDateWindowSummary(allTasks, visibleTasks)));
  return toolbar;
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
  const allTonyTasks = state.snapshot.tasks;
  const visibleTonyTasks = allTonyTasks.filter((task) => boardTaskIsVisible(task));
  wrapper.append(renderBoardDateWindowControl(allTonyTasks, visibleTonyTasks));
  const board = node("section", "board-grid");
  board.setAttribute("aria-label", "Task status board");
  boardColumns.forEach((definition) => {
    const tasks = visibleTonyTasks.filter((task) =>
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

async function assignGoalOwnerFromSummary(goalSlug, agentSlug) {
  const agent = state.agents.find((item) => item.slug === agentSlug && item.runtime !== "openclaw");
  if (!agent || !goalSlug) return;
  const response = await fetch(`/api/agents/${encodeURIComponent(agent.slug)}/default-goals`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ goal_slug: goalSlug, action: "assign" }),
  });
  const result = await response.json();
  if (!response.ok || !result.verified || !result.agent) {
    throw new Error(result.error || "Goal owner assignment did not receive canonical readback.");
  }
  await loadAgents();
  await loadGoalExecution({ force: true });
  render();
  showToast(`Default Goal owner verified: ${agent.name}.`);
}

function goalOwnerAssignmentCandidates(goalSlug, candidateOwners = null) {
  if (Array.isArray(candidateOwners) && candidateOwners.length) {
    return candidateOwners
      .filter((candidate) => {
        const agentSlug = String(candidate?.agent_slug || "").trim();
        const agent = state.agents.find((item) => item.slug === agentSlug);
        return agentSlug && agent && agent.runtime !== "openclaw";
      })
      .map((candidate) => {
        const agentSlug = String(candidate.agent_slug);
        const agent = state.agents.find((item) => item.slug === agentSlug);
        return {
          slug: agentSlug,
          name: String(candidate.agent_name || agent?.name || agentSlug),
          recommended: Boolean(candidate.recommended),
          recommendation: String(candidate.recommendation || "").trim(),
        };
      });
  }
  return state.agents
    .filter((agent) =>
      agent.runtime !== "openclaw" &&
      Array.isArray(agent.default_goal_slugs) &&
      !agent.default_goal_slugs.includes(goalSlug))
    .map((agent) => ({
      slug: agent.slug,
      name: agent.name,
      recommended: false,
      recommendation: "",
    }));
}

function appendGoalOwnerAssignmentButtons(parent, goalSlug, className, candidateOwners = null) {
  goalOwnerAssignmentCandidates(goalSlug, candidateOwners).forEach((agent) => {
    const label = agent.recommended
      ? `Assign to ${agent.name} (${agent.recommendation || "recommended"})`
      : `Assign to ${agent.name}`;
    const assign = node("button", className, label);
    assign.type = "button";
    assign.dataset.slug = goalSlug;
    assign.dataset.agentSlug = agent.slug;
    assign.addEventListener("click", async () => {
      assign.disabled = true;
      try {
        await assignGoalOwnerFromSummary(goalSlug, agent.slug);
      } catch (error) {
        showToast(error.message || "Goal owner assignment failed.");
        assign.disabled = false;
      }
    });
    parent.append(document.createTextNode(" "), assign);
  });
}

function recommendedGoalExecutionUnblockPlan(summary) {
  const actions = Array.isArray(summary?.action_queue)
    ? summary.action_queue
    : [];
  const answerAction = actions.find((action) =>
    action?.kind === "answer_question" &&
    action?.todo_slug &&
    action?.todo_updated_at &&
    String(action?.answer_template || "").trim());
  const ownerAction = actions.find((action) =>
    action?.kind === "assign_goal_owner" &&
    action?.goal_slug);
  if (!answerAction || !ownerAction) return null;
  const recommendedOwner = goalOwnerAssignmentCandidates(
    String(ownerAction.goal_slug),
    ownerAction.candidate_owners,
  ).find((candidate) => candidate.recommended);
  if (!recommendedOwner) return null;
  return { answerAction, ownerAction, recommendedOwner };
}

async function applyGoalExecutionRecommendedActions(summary, button = null, errorNode = null) {
  const plan = recommendedGoalExecutionUnblockPlan(summary);
  if (!plan) throw new Error("No complete recommended Goal execution unblock plan is available.");
  if (errorNode) {
    errorNode.textContent = "";
    errorNode.classList.add("is-hidden");
  }
  if (button) {
    button.disabled = true;
    button.textContent = "Running recommended plan…";
  }
  try {
    await answerGoalExecutionQuestionFromSummary(
      plan.answerAction,
      plan.answerAction.answer_template,
    );
    await assignGoalOwnerFromSummary(
      String(plan.ownerAction.goal_slug),
      plan.recommendedOwner.slug,
    );
    showToast("Recommended Goal execution unblock plan verified.");
  } catch (error) {
    if (errorNode) {
      errorNode.textContent = error.message || "Recommended Goal execution unblock plan failed.";
      errorNode.classList.remove("is-hidden");
    }
    throw error;
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "Run recommended unblock plan";
    }
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

function agentWorkFor(agent) {
  return state.agentTasks.filter((task) => task.owner?.slug === agent.slug);
}

function agentStatusCounts(work) {
  return {
    planned: work.filter((task) => task.status === "planned").length,
    active: work.filter((task) => task.status === "active").length,
    proposed: work.filter((task) => task.status === "proposed").length,
    blocked: work.filter((task) => task.status === "blocked").length,
    completed: work.filter((task) => task.status === "completed").length,
  };
}

function taskDispatcherAttention(task) {
  const status = task?.dispatcher_handoff?.status;
  if (!["planned", "active", "blocked"].includes(task?.status)) return null;
  if (["dead_letter", "suppressed", "handed_back"].includes(status)) {
    return {
      state: "Needs attention",
      summary: "Verified Agent handoff needs system review",
      detail: `Latest dispatcher status: ${status}. Inspect Handoff History for execution recovery evidence before retrying.`,
    };
  }
  return null;
}

function latestTaskByStatus(work, status) {
  return work
    .filter((task) => task.status === status)
    .slice()
    .sort((left, right) => String(right.updated_at || "").localeCompare(String(left.updated_at || "")))[0] || null;
}

function taskDetailLink(task, label = null) {
  const button = node("button", "inline-task-link", label || task.title || task.summary || task.slug);
  button.type = "button";
  button.dataset.slug = task.slug;
  button.addEventListener("click", () => selectTask(task.slug, task, button));
  return button;
}

function verifiedHandoffEventsForAgent(agent) {
  return state.handoffLogEvents.filter((event) => event.agent_slug && agent.slug === event.agent_slug);
}

function handoffDeliveryLabel(event) {
  const status = String(event?.status || "").replaceAll("_", " ");
  const type = String(event?.event_type || "handoff").replaceAll("_", " ");
  if (!event) return "No verified handoff delivery state.";
  if (event.status === "queued") return `Queued · ${type}`;
  if (event.status === "received" || event.status === "acknowledged") return `Received · ${type}`;
  if (event.status === "processing" || event.status === "agent_working") return `Actively executing · ${type}`;
  if (event.status === "blocked") return `Still blocked · ${type}`;
  if (event.status === "retrying") return `Retrying · ${type}`;
  if (event.status === "dead_letter") return `Dead letter · ${type}`;
  if (event.status === "completed" || event.status === "delivered") return `Completed · ${type}`;
  return `${privacySafeEventText(status || "Verified")} · ${type}`;
}

function renderAgentHandoffStatus(agent) {
  const events = verifiedHandoffEventsForAgent(agent)
    .slice()
    .sort((left, right) => Number(right.sequence || 0) - Number(left.sequence || 0));
  const latest = events[0] || null;
  const status = node("div", "agent-handoff-status");
  status.append(node("h3", "", "Handoff delivery"));
  status.append(node("strong", "", handoffDeliveryLabel(latest)));
  status.append(node(
    "span",
    "",
    latest
      ? `${events.length} verified event${events.length === 1 ? "" : "s"} · ${latest.occurred_at ? new Date(latest.occurred_at).toLocaleString() : "time unavailable"}`
      : "No immutable dispatcher event is currently attributed to this Agent.",
  ));
  return status;
}

function agentRuntimeLabel(agent) {
  if (agent.runtime !== "openclaw") return "Codex";
  const latest = verifiedHandoffEventsForAgent(agent)
    .filter((event) => (
      event.executor_agent === agent.slug &&
      ["execution_started", "acknowledgement"].includes(event.event_type)
    ))
    .slice()
    .sort((left, right) => Number(right.sequence || 0) - Number(left.sequence || 0))[0];
  if (!latest) return "OpenClaw · Session health unavailable";
  return `OpenClaw · verified fixed-session activity ${latest.occurred_at ? new Date(latest.occurred_at).toLocaleString() : "time unavailable"}`;
}

function renderSystemHandoffAttention() {
  const verifiedAgents = new Set(state.agents.map((agent) => agent.slug));
  const problematic = state.handoffLogEvents.filter((event) => (
    !event.agent_slug ||
    !verifiedAgents.has(event.agent_slug) ||
    event.status === "routing_error" ||
    event.status === "identity_conflict"
  ));
  const section = node("section", "system-handoff-attention");
  section.setAttribute("aria-label", "System handoff attention");
  if (!problematic.length) {
    section.classList.add("is-empty");
    section.append(node("p", "", "No ambiguous or unassigned handoff routing issues."));
    return section;
  }
  section.append(node("h2", "", "System attention"));
  section.append(node(
    "p",
    "",
    `${problematic.length} handoff event${problematic.length === 1 ? "" : "s"} need verified identity before routing. These are not attached to an arbitrary Agent.`,
  ));
  return section;
}

function renderUnifiedHandoffHistory({ historyOpen = false } = {}) {
  const details = node("details", "agent-handoff-history");
  const shouldOpen = historyOpen || state.agentHandoffHistoryOpen;
  details.open = Boolean(shouldOpen);
  details.addEventListener("toggle", () => {
    state.agentHandoffHistoryOpen = details.open;
  });
  const summary = node("summary", "", "Handoff History");
  summary.addEventListener("click", () => {
    state.agentHandoffHistoryOpen = !details.open;
  });
  const meta = node("span", "handoff-history-count", `${state.handoffLogTotal} event${state.handoffLogTotal === 1 ? "" : "s"}`);
  summary.append(meta);
  const filters = handoffLogFilters();
  const form = node("form", "handoff-filter-grid");
  form.append(
    handoffFilterSelect("Time", "time", [["all", "All loaded time"], ["hour", "Past hour"], ["day", "Past day"], ["week", "Past week"]], filters.time),
    handoffFilterSelect("Agent", "agent_slug", [["", "All Agents"], ["agents/tammy", "Tammy"], ["agents/timmy", "Timmy"], ["agents/toddy", "Toddy"]], filters.agent_slug),
    handoffFilterSelect("Status", "status", HANDOFF_STATUS_FILTER_OPTIONS, filters.status),
    handoffFilterSelect("Event", "event_type", HANDOFF_EVENT_FILTER_OPTIONS, filters.event_type),
    handoffFilterSelect("Failure", "failure", [["", "All outcomes"], ["retrying", "Retrying"], ["dead_letter", "Dead letter"]], filters.failure),
  );
  const correlation = node("label", "handoff-filter-control");
  correlation.append(node("span", "", "Correlation"));
  const correlationInput = document.createElement("input");
  correlationInput.name = "correlation_id";
  correlationInput.maxLength = 60;
  correlationInput.autocomplete = "off";
  correlationInput.placeholder = filters.correlation_id
    ? redactedCorrelationLabel(filters.correlation_id)
    : "Exact safe correlation ID";
  correlationInput.dataset.preserveActive = filters.correlation_id ? "true" : "false";
  correlationInput.setAttribute("aria-label", "Correlation filter");
  markHandoffFocus(correlationInput, "filter:correlation_id");
  correlation.append(correlationInput);
  const apply = node("button", "secondary-button", "Apply filters");
  apply.type = "submit";
  markHandoffFocus(apply, "filter-submit");
  form.append(correlation, apply);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void loadHandoffLog({ reset: true, filters: handoffLogFilters(form) });
  });
  form.querySelectorAll("select").forEach((select) => {
    select.addEventListener("change", () => {
      const statusFilter = form.elements.namedItem("status");
      const failureFilter = form.elements.namedItem("failure");
      if (select.name === "status" && select.value && failureFilter) failureFilter.value = "";
      if (select.name === "failure" && select.value && statusFilter) statusFilter.value = "";
      void loadHandoffLog({ reset: true, filters: handoffLogFilters(form) });
    });
  });
  if (filters.correlation_id) {
    const clearCorrelation = node("button", "secondary-button", "Clear correlation");
    clearCorrelation.type = "button";
    markHandoffFocus(clearCorrelation, "filter-clear-correlation");
    clearCorrelation.addEventListener("click", () => {
      void loadHandoffLog({ reset: true, filters: { correlation_id: "" } });
    });
    form.append(clearCorrelation);
  }
  const stateMessage = node("p", "handoff-surface-state");
  stateMessage.tabIndex = -1;
  markHandoffFocus(stateMessage, "load-status");
  stateMessage.setAttribute("role", state.handoffLogError && !state.handoffLogEvents.length ? "alert" : "status");
  stateMessage.setAttribute("aria-live", "polite");
  const list = node("ol", "handoff-event-list");
  list.setAttribute("aria-label", "Handoff audit events");
  const visible = renderHandoffEvents(state.handoffLogEvents, {
    list,
    timeFilter: "all",
  });
  if (state.handoffLogLoading && !state.handoffLogEvents.length) {
    stateMessage.textContent = "Loading handoff events…";
  } else if (state.handoffLogStale) {
    stateMessage.textContent = "Last verified handoff events remain visible; the latest read failed.";
    stateMessage.classList.add("is-stale");
  } else if (state.handoffLogError) {
    stateMessage.textContent = state.handoffLogError === MISSING_LINKED_TASK_ERROR
      ? state.handoffLogError
      : "Handoff events are unavailable. Try again without changing any task.";
    stateMessage.classList.add("is-error");
  } else if (!visible) {
    stateMessage.textContent = "No handoff events match these filters.";
  } else {
    stateMessage.textContent = `${visible} displayed of ${state.handoffLogTotal} matching events · canonical sequence order.`;
  }
  const loadMore = node("button", "secondary-button handoff-load-more", "Load more events");
  loadMore.type = "button";
  markHandoffFocus(loadMore, "load-more");
  loadMore.disabled = state.handoffLogLoading;
  loadMore.classList.toggle("is-hidden", state.handoffLogNextSequence === null);
  loadMore.addEventListener("click", () => void loadHandoffLog({ reset: false }));
  details.append(summary, form, stateMessage, list, loadMore);
  return details;
}

const OPENCLAW_PAIR_BY_SOURCE = {
  "agents/tammy": "agents/tammy-oc",
  "agents/timmy": "agents/timmy-oc",
  "agents/toddy": "agents/toddy-oc",
};
const SOURCE_BY_OPENCLAW_PAIR = Object.fromEntries(
  Object.entries(OPENCLAW_PAIR_BY_SOURCE).map(([source, executor]) => [executor, source]),
);

function activeDelegationForSource(sourceSlug) {
  return state.delegations.find((lease) => (
    lease.source_agent === sourceSlug &&
    ["active", "scheduled"].includes(lease.state) &&
    Number.isFinite(new Date(lease.ends_at).getTime()) &&
    new Date(lease.ends_at).getTime() > Date.now()
  )) || null;
}

function activeTemporaryExecution(task) {
  const execution = task?.temporary_execution;
  const ownerSlug = task?.owner_agent || task?.owner?.slug || null;
  const lease = execution ? activeDelegationForSource(ownerSlug) : null;
  if (
    !execution ||
    !lease ||
    lease.slug !== execution.delegation_slug ||
    execution.permanent_owner !== ownerSlug ||
    execution.executor_agent !== lease.executor_agent ||
    !Number.isFinite(new Date(execution.expires_at).getTime()) ||
    new Date(execution.expires_at).getTime() <= Date.now()
  ) return null;
  return execution;
}

function delegationRemainingLabel(lease) {
  if (!lease) return "No temporary delegation is active.";
  const end = new Date(lease.ends_at);
  const remaining = Math.max(0, end.getTime() - Date.now());
  if (!Number.isFinite(end.getTime()) || remaining <= 0 || lease.state === "expired") {
    return "Authorization expired; no delegated work can start.";
  }
  const minutes = Math.ceil(remaining / 60000);
  const duration = minutes >= 1440
    ? `${Math.ceil(minutes / 1440)}d`
    : minutes >= 60 ? `${Math.ceil(minutes / 60)}h` : `${minutes}m`;
  return `${lease.state === "scheduled" ? "Starts later" : "Active"} · ${duration} remaining · ends ${formatPacificDisplay(end)}`;
}

const PACIFIC_TIME_ZONE = "America/Los_Angeles";
const PACIFIC_INPUT_FORMATTER = new Intl.DateTimeFormat("en-CA", {
  timeZone: PACIFIC_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function formatPacificInstant(date) {
  if (!(date instanceof Date) || !Number.isFinite(date.getTime())) return "";
  const parts = Object.fromEntries(
    PACIFIC_INPUT_FORMATTER.formatToParts(date)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

function formatPacificDisplay(date) {
  if (!(date instanceof Date) || !Number.isFinite(date.getTime())) return "time unavailable";
  return date.toLocaleString("en-US", { timeZone: PACIFIC_TIME_ZONE });
}

function parsePacificLocalDateTime(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(String(value || ""));
  if (!match) return new Date(Number.NaN);
  const [, year, month, day, hour, minute] = match;
  const target = `${year}-${month}-${day}T${hour}:${minute}`;
  const localAsUtc = Date.UTC(
    Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute),
  );
  const candidates = [];
  for (let offsetMinutes = -12 * 60; offsetMinutes <= 14 * 60; offsetMinutes += 15) {
    const candidate = new Date(localAsUtc - offsetMinutes * 60000);
    if (formatPacificInstant(candidate) === target) candidates.push(candidate);
  }
  if (!candidates.length) return new Date(Number.NaN);
  candidates.sort((left, right) => left.getTime() - right.getTime());
  return candidates[0];
}

function delegationMutationError(section, message) {
  const error = section.querySelector(".delegation-error");
  error.textContent = message;
  error.classList.remove("is-hidden");
  error.focus({ preventScroll: true });
}

function restoreDelegationFocus(agentSlug) {
  window.requestAnimationFrame(() => {
    document.querySelector(
      `[data-delegation-agent="${CSS.escape(agentSlug)}"] .delegation-status`,
    )?.focus({ preventScroll: true });
  });
}

async function createTemporaryDelegation(event, sourceAgent, executorAgent) {
  event.preventDefault();
  const form = event.currentTarget;
  const section = form.closest(".delegation-card");
  const endInput = form.querySelector('[name="delegation-end"]');
  const endsAt = parsePacificLocalDateTime(endInput.value);
  if (!Number.isFinite(endsAt.getTime()) || endsAt <= new Date()) {
    delegationMutationError(section, "Choose a future end time between 15 minutes and 7 days.");
    return;
  }
  const ownerName = state.agents.find((agent) => agent.slug === sourceAgent)?.name || sourceAgent;
  const executorName = state.agents.find((agent) => agent.slug === executorAgent)?.name || executorAgent;
  const confirmation = [
    `Temporarily delegate eligible ${ownerName} work to ${executorName} until ${formatPacificDisplay(endsAt)} (${PACIFIC_TIME_ZONE})?`,
    "Allowed: task status, TODOs, comments, and Artifacts.",
    "No account access, external actions, trading, or scope expansion.",
    "Permanent ownership will not change.",
  ].join("\n");
  if (!window.confirm(confirmation)) return;
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    if (!await loadAgentDelegations()) throw new Error("Canonical authorization could not be refreshed. No change was made.");
    if (activeDelegationForSource(sourceAgent)) throw new Error("A current authorization already exists. Refresh before changing it.");
    const response = await fetch("/api/agent-delegations", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        source_agent: sourceAgent,
        executor_agent: executorAgent,
        starts_at: new Date().toISOString(),
        ends_at: endsAt.toISOString(),
        display_timezone: "America/Los_Angeles",
        allowed_operations: ["task_status", "todo", "comment", "artifact"],
      }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.receipt?.verified) throw new Error(payload.error || "Delegation was not verified.");
    if (!await loadAgentDelegations()) throw new Error("Verified mutation succeeded, but its refreshed authorization could not be displayed yet.");
    render();
    restoreDelegationFocus(executorAgent);
    showToast("Temporary delegation was saved after canonical readback.");
  } catch (error) {
    delegationMutationError(section, error.message || "Temporary delegation was not saved.");
  } finally {
    button.disabled = false;
  }
}

async function changeTemporaryDelegation(lease, action, section, requestedEnd = null) {
  const verb = action === "extend" ? "Extend" : action === "revoke" ? "Revoke" : "End Early";
  const requested = requestedEnd ? parsePacificLocalDateTime(requestedEnd) : null;
  const exactEnd = action === "extend" && requested && Number.isFinite(requested.getTime())
    ? ` The requested new end is ${formatPacificDisplay(requested)} (${PACIFIC_TIME_ZONE}).`
    : "";
  if (!window.confirm(`${verb} this temporary authorization?${exactEnd} Allowed: task status, TODOs, comments, and Artifacts. No account access, external actions, trading, or scope expansion. Permanent ownership will not change.`)) return;
  try {
    if (!await loadAgentDelegations()) throw new Error("Canonical authorization could not be refreshed. No change was made.");
    const current = state.delegations.find((item) => item.slug === lease.slug);
    if (!current || !["active", "scheduled"].includes(current.state)) {
      throw new Error("This authorization is no longer active. No change was made.");
    }
    const extendedEnd = requestedEnd ? parsePacificLocalDateTime(requestedEnd) : null;
    if (action === "extend" && (!extendedEnd || !Number.isFinite(extendedEnd.getTime()))) {
      throw new Error("Choose a valid later end time.");
    }
    const body = action === "extend"
      ? { ends_at: extendedEnd.toISOString(), expected_version: current.version }
      : { action: action === "revoke" ? "revoke" : "complete", expected_version: current.version };
    const response = await fetch(`/api/agent-delegations/${encodeURIComponent(current.slug)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok || !payload.receipt?.verified) throw new Error(payload.error || "Delegation change was not verified.");
    if (!await loadAgentDelegations()) throw new Error("Verified mutation succeeded, but its refreshed authorization could not be displayed yet.");
    render();
    restoreDelegationFocus(current.executor_agent);
    showToast(`${verb} was verified.`);
  } catch (error) {
    delegationMutationError(section, error.message || "Delegation could not be changed.");
  }
}

function renderDelegationControls(agent) {
  const section = node("section", "delegation-card");
  section.dataset.delegationAgent = agent.slug;
  const sourceSlug = OPENCLAW_PAIR_BY_SOURCE[agent.slug]
    ? agent.slug
    : SOURCE_BY_OPENCLAW_PAIR[agent.slug];
  const executorSlug = OPENCLAW_PAIR_BY_SOURCE[sourceSlug];
  const lease = sourceSlug ? activeDelegationForSource(sourceSlug) : null;
  section.append(node("h3", "", "Temporarily delegate work"));
  const status = node("p", "delegation-status", delegationRemainingLabel(lease));
  status.setAttribute("role", "status");
  status.tabIndex = -1;
  section.append(status);
  if (state.delegationsError) {
    section.append(node("p", "delegation-stale", "Last verified authorization remains visible; refresh failed."));
  }
  const error = node("p", "form-error delegation-error is-hidden");
  error.setAttribute("role", "alert");
  error.tabIndex = -1;
  if (lease) {
    const actions = node("div", "delegation-actions");
    const endInput = document.createElement("input");
    endInput.type = "datetime-local";
    endInput.setAttribute("aria-label", "New delegation end time (America/Los_Angeles)");
    const minimumExtension = new Date(new Date(lease.ends_at).getTime() + 15 * 60000);
    const maximumExtension = new Date(new Date(lease.starts_at).getTime() + 7 * 86400000);
    endInput.min = formatPacificInstant(minimumExtension);
    endInput.max = formatPacificInstant(maximumExtension);
    endInput.value = formatPacificInstant(new Date(Math.min(
      new Date(lease.ends_at).getTime() + 60 * 60000,
      maximumExtension.getTime(),
    )));
    const extend = node("button", "secondary-button", "Extend");
    extend.type = "button";
    if (minimumExtension > maximumExtension) {
      endInput.disabled = true;
      extend.disabled = true;
    }
    extend.addEventListener("click", () => void changeTemporaryDelegation(lease, "extend", section, endInput.value));
    const end = node("button", "secondary-button", "End Early");
    end.type = "button";
    end.addEventListener("click", () => void changeTemporaryDelegation(lease, "end", section));
    const revoke = node("button", "danger-button", "Revoke");
    revoke.type = "button";
    revoke.addEventListener("click", () => void changeTemporaryDelegation(lease, "revoke", section));
    actions.append(extend, end, revoke);
    section.append(endInput, actions, error);
    return section;
  }
  if (agent.runtime === "openclaw") {
    section.append(node("p", "delegation-owner-copy", "Start temporary delegation from the paired Codex Agent card."), error);
    return section;
  }
  const form = node("form", "delegation-form");
  const label = node("label", "", "Authorization ends (America/Los_Angeles)");
  const input = document.createElement("input");
  input.type = "datetime-local";
  input.name = "delegation-end";
  input.required = true;
  input.min = formatPacificInstant(new Date(Date.now() + 15 * 60000));
  input.max = formatPacificInstant(new Date(Date.now() + 7 * 86400000));
  input.value = formatPacificInstant(new Date(Date.now() + 8 * 3600000));
  label.append(input);
  const shortcuts = node("div", "delegation-shortcuts");
  [["1 hour", 1], ["8 hours", 8], ["24 hours", 24]].forEach(([copy, hours]) => {
    const button = node("button", "secondary-button", copy);
    button.type = "button";
    button.addEventListener("click", () => { input.value = formatPacificInstant(new Date(Date.now() + hours * 3600000)); });
    shortcuts.append(button);
  });
  const submit = node("button", "primary-button", "Temporarily delegate work");
  submit.type = "submit";
  form.addEventListener("submit", (event) => void createTemporaryDelegation(event, sourceSlug, executorSlug));
  form.append(label, shortcuts, node("p", "delegation-owner-copy", "Permanent owner does not change. Delegated work is additional and only starts when this Agent has no owned work ready."), submit);
  section.append(form, error);
  return section;
}

const GOAL_EXECUTION_ATTENTION_REASONS = new Set([
  "owner_missing",
  "owner_ambiguous",
  "project_ambiguous",
  "route_unavailable",
  "system_repair_required",
  "handoff_needs_repair",
  "handoff_missing",
  "task_needs_next_action",
  "handoff_worker_unavailable",
]);

function goalExecutionTask(decision) {
  const slug = decision?.task_slug || null;
  if (!slug) return null;
  return findTaskBySlug(slug);
}

function goalExecutionAgent(decision) {
  const task = goalExecutionTask(decision);
  const lastTask = state.goalExecution?.last_run?.task;
  const agentSlug = task?.owner_agent || task?.owner?.slug || (
    lastTask?.slug === decision?.task_slug ? lastTask.agent_slug : null
  );
  if (agentSlug) {
    return state.agents.find((agent) => agent.slug === agentSlug) || null;
  }
  const owners = state.agents.filter((agent) =>
    Array.isArray(agent.default_goal_slugs) &&
    agent.default_goal_slugs.includes(decision?.goal_slug));
  return owners.length === 1 ? owners[0] : null;
}

function goalExecutionProject(decision) {
  const task = goalExecutionTask(decision);
  if (task?.project) {
    return state.projects.find((project) => project.slug === task.project) || null;
  }
  const projects = state.projects.filter((project) =>
    project.status === "active" &&
    Array.isArray(project.supporting_goal_slugs) &&
    project.supporting_goal_slugs.includes(decision?.goal_slug));
  return projects.length === 1 ? projects[0] : null;
}

function goalExecutionState(decision) {
  if (state.goalExecution?.last_error) return "Needs attention";
  if (!decision) return "Ready";
  const reason = decision.reason;
  if (GOAL_EXECUTION_ATTENTION_REASONS.has(reason)) return "Needs attention";
  if (reason === "recently_completed" || reason === "completed_after_verified_handoff") return "Recently completed";
  if (reason === "waiting_for_tony") return "Blocked";
  if (reason === "wip_full" || reason === "goal_paused") return "Blocked";
  const task = goalExecutionTask(decision);
  const lastTask = state.goalExecution?.last_run?.task;
  const handoff = state.goalExecution?.last_run?.handoff;
  if (lastTask?.slug === decision?.task_slug) {
    if (["queued", "leased", "retrying"].includes(handoff?.status)) return "Delivering";
    if (["received", "acknowledged", "processing", "agent_working", "actively_executing"].includes(handoff?.status)) {
      return "Executing";
    }
    if (lastTask?.status === "blocked") return "Blocked";
    if (lastTask?.status === "active") return "Executing";
  }
  if (task?.status === "blocked") return "Blocked";
  if (task?.status === "active") return "Executing";
  return "Ready";
}

function goalExecutionReasonCopy(decision) {
  if (!decision) {
    return "No owned Goal is currently eligible for bounded automatic work.";
  }
  const reason = decision.reason;
  if (reason === "duplicate") {
    const lastTask = state.goalExecution?.last_run?.task;
    const handoff = state.goalExecution?.last_run?.handoff;
    if (lastTask?.slug === decision?.task_slug) {
      if (["queued", "leased", "retrying"].includes(handoff?.status)) {
        return "Mission Control has delivered this Goal work to the assigned Agent and is waiting for a verified worker lease.";
      }
      if (["received", "acknowledged", "processing", "agent_working", "actively_executing"].includes(handoff?.status)) {
        return "The assigned Agent is actively executing this Goal work.";
      }
    }
  }
  const copy = {
    auto_eligible: "One bounded internal review is eligible; no external action is authorized.",
    duplicate: "A canonical task already represents this Goal work.",
    wip_full: "The assigned Agent is already at the automatic work-in-progress limit.",
    route_unavailable: "The verified fixed Agent route needs system repair.",
    owner_missing: "Assign exactly one Codex Agent with a verified default_agent_for link before Mission Control can derive work from this Goal.",
    owner_ambiguous: "This Goal has more than one default Agent and needs attention.",
    project_ambiguous: "More than one active Project supports this Goal.",
    runtime_not_allowed: "Automatic execution is limited to Codex Agents in this rollout.",
    legacy_alias_suppressed: "Legacy Goal aliases are read-only and cannot derive work.",
    goal_paused: "This Goal is paused.",
    goal_terminal: "This Goal is already terminal.",
    system_repair_required: "Canonical task state needs safe system reconciliation.",
    handoff_needs_repair: "The canonical task is active, but verified Agent handoff or execution needs system review.",
    handoff_missing: "The canonical task is active, but no verified Agent handoff is recorded yet.",
    task_needs_next_action: "The canonical task is active, but it has no explicit next action for the assigned Agent.",
    waiting_for_tony: "The canonical task is blocked waiting for Tony's answer before the assigned Agent can continue.",
    handoff_worker_unavailable: "The canonical task is active and queued or retrying, but no verified Agent worker has leased it yet. Verify the Agent host dispatcher and private route.",
    recently_completed: "The assigned Agent completed this Goal work recently; Mission Control is waiting for the next cycle before deriving another task.",
    completed_after_verified_handoff: "Mission Control completed the canonical task after verified Agent handoff and Artifact readback.",
    activated: "The canonical task is active and entering the fixed Agent handoff.",
    shadow: "Shadow mode evaluates safe work without creating or activating a task.",
    off: "Automatic Goal execution is off.",
    no_eligible_work: "No bounded automatic work is currently eligible.",
  };
  return copy[reason] || "Waiting for the next verified Goal execution readback.";
}

function goalExecutionQuestionTodo(task) {
  const questionSlug = task?.handoff?.question_todo;
  if (!questionSlug || !Array.isArray(task?.todos)) return null;
  return task.todos.find((todo) =>
    todo?.slug === questionSlug &&
    todo.status !== "done" &&
    todo.kind === "question" &&
    typeof todo.text === "string" &&
    todo.text.trim()) || null;
}

function goalExecutionDetailCopy(decision, task) {
  const parts = [goalExecutionReasonCopy(decision)];
  if (decision?.reason === "waiting_for_tony") {
    const question = goalExecutionQuestionTodo(task);
    if (question) {
      parts.push(`Answer: ${question.text.trim()}`);
    }
  }
  return parts.join(" ");
}

function goalExecutionButton(label, className, slug, activate, originKey = "") {
  const button = node("button", `goal-execution-link ${className}`, label);
  button.type = "button";
  button.dataset.slug = slug;
  if (originKey) button.dataset.goalExecutionOrigin = originKey;
  button.addEventListener("click", () => activate(button));
  return button;
}

function goalExecutionRow(decision, preferredAgent = null, originKey = "surface") {
  const item = node("li", "goal-execution-row");
  const agent = preferredAgent || goalExecutionAgent(decision);
  const goal = state.snapshot?.goals.find((value) => value.slug === decision?.goal_slug) || null;
  const project = goalExecutionProject(decision);
  const task = goalExecutionTask(decision);
  const stateLabel = goalExecutionState(decision);
  const summary = node("div", "goal-execution-summary");
  if (agent) {
    summary.append(goalExecutionButton(agent.name, "agent", agent.slug, () => openAgentProfile(agent)));
  } else {
    summary.append(node("span", "goal-execution-unavailable", "Agent unavailable"));
  }
  summary.append(document.createTextNode(" — "));
  if (goal) {
    summary.append(goalExecutionButton(
      goal.title,
      "goal",
      goal.slug,
      (origin) => selectGoal(goal.slug, origin),
      `${originKey}:goal:${goal.slug}`,
    ));
  } else {
    summary.append(node("span", "goal-execution-unavailable", "Goal unavailable"));
  }
  if (project) {
    summary.append(
      document.createTextNode(" — "),
      goalExecutionButton(
        project.title,
        "project",
        project.slug,
        (origin) => selectProject(project.slug, origin),
        `${originKey}:project:${project.slug}`,
      ),
    );
  }
  summary.append(document.createTextNode(" — "), node("strong", `goal-execution-state is-${stateLabel.toLowerCase().replace(" ", "-")}`, stateLabel));
  const detail = node("p", "goal-execution-copy");
  if (task) {
    const taskLink = taskDetailLink(task, task.title || task.summary || "Open canonical Task");
    taskLink.dataset.goalExecutionOrigin = originKey;
    detail.append(taskLink, document.createTextNode(" · "));
  }
  detail.append(document.createTextNode(goalExecutionDetailCopy(decision, task)));
  item.append(summary, detail);
  return item;
}

function goalExecutionRows(decisions) {
  const rows = [];
  const used = new Set();
  state.agents
    .filter((agent) => agent.runtime !== "openclaw")
    .forEach((agent) => {
      const decision = decisions.find((candidate, index) => {
        if (used.has(index)) return false;
        const match = goalExecutionAgent(candidate)?.slug === agent.slug ||
          agent.default_goal_slugs?.includes(candidate.goal_slug);
        if (match) used.add(index);
        return match;
      }) || null;
      rows.push(goalExecutionRow(
        decision,
        agent,
        `surface:${agent.slug}:${decision?.goal_slug || "none"}`,
      ));
    });
  decisions.forEach((decision, index) => {
    if (!used.has(index)) rows.push(goalExecutionRow(
      decision,
      null,
      `surface:unassigned:${decision.goal_slug}`,
    ));
  });
  return rows;
}

function goalExecutionDecisionForAgent(agent) {
  const decisions = Array.isArray(state.goalExecution?.last_run?.decisions)
    ? state.goalExecution.last_run.decisions
    : [];
  return decisions.find((decision) =>
    goalExecutionAgent(decision)?.slug === agent.slug ||
    agent.default_goal_slugs?.includes(decision.goal_slug)) || null;
}

function renderAgentGoalExecution(agent) {
  const compact = node("div", "agent-goal-execution-compact");
  const decision = goalExecutionDecisionForAgent(agent);
  compact.append(
    node("span", "agent-work-kind", "Goal execution"),
    node("strong", `goal-execution-state is-${goalExecutionState(decision).toLowerCase().replace(" ", "-")}`, goalExecutionState(decision)),
  );
  const task = goalExecutionTask(decision);
  if (task) {
    const taskLink = taskDetailLink(task, task.title || task.summary || "Open canonical Task");
    taskLink.dataset.goalExecutionOrigin = `card:${agent.slug}:${decision?.goal_slug || "none"}`;
    compact.append(taskLink);
    compact.append(node("span", "goal-execution-copy", goalExecutionDetailCopy(decision, task)));
  } else {
    compact.append(node("span", "goal-execution-copy", goalExecutionReasonCopy(decision)));
  }
  return compact;
}

function goalExecutionSummaryPayload() {
  const summary = state.goalExecution?.summary || state.goalExecution?.last_run?.summary;
  return summary && typeof summary === "object" ? summary : null;
}

function pluralizeCount(count, singular, plural = `${singular}s`) {
  return `${count} ${count === 1 ? singular : plural}`;
}

function goalExecutionActionOwnerLabel(owner) {
  if (owner === "tony") return "Tony action required";
  if (owner === "agent") return "Agent active";
  if (owner === "system") return "System action required";
  return "Action";
}

function openGoalExecutionQuestionAction(taskSlug, todoSlug, originControl = null) {
  if (!taskSlug || !todoSlug) return;
  selectTask(taskSlug, null, originControl, {
    focusTarget: "handoff-answer",
    todoSlug,
  });
}

async function answerGoalExecutionQuestionFromSummary(action, answer, submit = null, errorNode = null) {
  const todoSlug = String(action?.todo_slug || "").trim();
  const expectedUpdatedAt = String(action?.todo_updated_at || "").trim();
  const responseText = String(answer || "").trim();
  if (!todoSlug || !expectedUpdatedAt) {
    throw new Error("Exact question readback is required before answering.");
  }
  if (!responseText) {
    throw new Error("Answer is required before the Agent can resume.");
  }
  if (errorNode) {
    errorNode.textContent = "";
    errorNode.classList.add("is-hidden");
  }
  if (submit) submit.disabled = true;
  try {
    const response = await fetch(
      `/api/todos/${encodeURIComponent(todoSlug)}/answer`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          answer: responseText,
          expected_updated_at: expectedUpdatedAt,
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
    reconcileVerifiedTask(mergeVerifiedTodoIntoTask(receipt.task, receipt.todo));
    await Promise.all([loadGoalExecution({ force: true }), loadAgentWork()]);
    showToast(`Answer verified. ${agentDisplayName(receipt.next_owner, receipt.task)} can resume this task.`);
    return receipt;
  } catch (error) {
    if (errorNode) {
      errorNode.textContent = todoErrorMessage(error);
      errorNode.classList.remove("is-hidden");
    }
    throw error;
  } finally {
    if (submit) submit.disabled = false;
  }
}

function renderGoalExecutionActionQueue(summary) {
  const actions = Array.isArray(summary.action_queue)
    ? summary.action_queue
    : [];
  if (!actions.length) return null;
  const wrapper = node("div", "goal-execution-action-queue");
  wrapper.append(node("strong", "", "Action queue:"));
  const list = node("ul", "");
  actions.slice(0, 4).forEach((action) => {
    const item = node("li", "");
    const label = String(action?.label || "Action");
    item.append(
      node("span", "goal-execution-action-owner", goalExecutionActionOwnerLabel(action?.owner)),
      document.createTextNode(" · "),
    );
    if (action?.kind === "answer_question" && action?.task_slug && action?.todo_slug) {
      const answer = node("button", "goal-execution-action-label goal-execution-answer-action", label);
      answer.type = "button";
      answer.dataset.taskSlug = action.task_slug;
      answer.dataset.todoSlug = action.todo_slug;
      answer.dataset.slug = action.task_slug;
      answer.dataset.goalExecutionOrigin = `summary:action:answer_question:${CSS.escape(action.task_slug)}:${CSS.escape(action.todo_slug)}`;
      answer.setAttribute("aria-label", `${label}: ${action.summary || action.task_slug}`);
      answer.addEventListener("click", () => openGoalExecutionQuestionAction(
        action.task_slug,
        action.todo_slug,
        answer,
      ));
      item.append(answer);
    } else {
      item.append(node("span", "goal-execution-action-label", label));
    }
    const summaryText = String(action?.summary || "").trim();
    if (summaryText) {
      item.append(document.createTextNode(` — ${summaryText}`));
    }
    const detailText = String(action?.detail || "").trim();
    if (detailText) {
      item.append(node("p", "goal-execution-action-detail", detailText));
    }
    if (action?.kind === "answer_question" && action?.private_input_required) {
      const blockedGoalCount = Number(action?.blocked_goal_count) || 0;
      if (blockedGoalCount > 1) {
        item.append(
          node(
            "p",
            "goal-execution-private-input",
            pluralizeCount(blockedGoalCount, "related private-input blocker"),
          ),
        );
      }
      item.append(
        node(
          "p",
          "goal-execution-private-input",
          "Private input required. Open the task and answer directly; Mission Control will not generate or prefill private credentials.",
        ),
      );
    } else if (action?.kind === "answer_question" && action?.todo_slug && action?.todo_updated_at) {
      const form = node("form", "goal-execution-answer-form");
      const textarea = document.createElement("textarea");
      textarea.className = "goal-execution-answer-input";
      textarea.rows = 2;
      textarea.maxLength = 4000;
      textarea.required = true;
      textarea.placeholder = "Answer so the Agent can resume";
      textarea.setAttribute("aria-label", `Answer Agent question: ${summaryText || action.todo_slug}`);
      const submit = node("button", "secondary-button goal-execution-answer-submit", "Submit answer");
      submit.type = "submit";
      const error = node("p", "form-error is-hidden");
      error.setAttribute("role", "alert");
      const answerTemplateText = String(action?.answer_template || "");
      if (answerTemplateText.trim()) {
        const templateButton = node(
          "button",
          "secondary-button goal-execution-answer-template",
          "Insert answer template",
        );
        templateButton.type = "button";
        templateButton.addEventListener("click", () => {
          textarea.value = answerTemplateText;
          textarea.focus();
        });
        form.append(textarea, templateButton, submit, error);
      } else {
        form.append(textarea, submit, error);
      }
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
          await answerGoalExecutionQuestionFromSummary(action, textarea.value, submit, error);
          textarea.value = "";
        } catch (_error) {
          // Error copy is rendered in the inline alert.
        }
      });
      item.append(form);
    }
    if (action?.kind === "assign_goal_owner" && action?.goal_slug) {
      appendGoalOwnerAssignmentButtons(
        item,
        String(action.goal_slug),
        "goal-execution-action-owner-assign",
        action.candidate_owners,
      );
    }
    list.append(item);
  });
  wrapper.append(list);
  return wrapper;
}

function renderGoalExecutionSummary() {
  const summary = goalExecutionSummaryPayload();
  if (!summary) return null;
  const nextAction = String(summary.next_action || "").trim();
  const blockingQuestions = Array.isArray(summary.blocking_questions)
    ? summary.blocking_questions
    : [];
  const missingOwners = Array.isArray(summary.missing_owners)
    ? summary.missing_owners
    : [];
  const metrics = [
    pluralizeCount(Number(summary.total_goals) || 0, "total goal"),
    pluralizeCount(Number(summary.needs_attention) || 0, "need attention", "need attention"),
    pluralizeCount(Number(summary.waiting_for_tony) || 0, "waiting for Tony", "waiting for Tony"),
    pluralizeCount(Number(summary.owner_missing) || 0, "missing owner"),
    pluralizeCount(Number(summary.in_flight) || 0, "in flight", "in flight"),
    pluralizeCount(Number(summary.recently_completed) || 0, "recently completed", "recently completed"),
  ];
  const panel = node("div", "goal-execution-reader-summary");
  panel.setAttribute("aria-label", "Goal execution next action summary");
  if (nextAction) {
    panel.append(node("p", "goal-execution-next-action", `Next action: ${nextAction}`));
  }
  panel.append(node("p", "goal-execution-metrics", metrics.join(" · ")));
  const actionQueue = renderGoalExecutionActionQueue(summary);
  if (actionQueue) panel.append(actionQueue);
  blockingQuestions.slice(0, 2).forEach((item) => {
    const question = String(item?.question || "").trim();
    const taskSlug = String(item?.task_slug || "").trim();
    if (!question) return;
    const row = node("p", "goal-execution-blocking-question");
    row.append(document.createTextNode("Question: "));
    if (taskSlug) {
      const taskLink = taskDetailLink({ slug: taskSlug, title: question }, question);
      taskLink.dataset.goalExecutionOrigin = `summary:blocking-question:${taskSlug}`;
      row.append(taskLink);
    } else {
      row.append(document.createTextNode(question));
    }
    panel.append(row);
  });
  missingOwners.slice(0, 2).forEach((item) => {
    const title = String(item?.goal_title || item?.goal_slug || "").trim();
    const goalSlug = String(item?.goal_slug || "").trim();
    const relationship = String(item?.required_relationship || "default_agent_for").trim();
    if (!title) return;
    const row = node("p", "goal-execution-missing-owner");
    row.append(document.createTextNode("Missing owner: "));
    if (goalSlug) {
      row.append(goalExecutionButton(
        title,
        "goal",
        goalSlug,
        (origin) => selectGoal(goalSlug, origin),
        `summary:missing-owner:${goalSlug}`,
      ));
    } else {
      row.append(document.createTextNode(title));
    }
    row.append(document.createTextNode(` — add ${relationship}`));
    appendGoalOwnerAssignmentButtons(row, goalSlug, "goal-execution-owner-assign", item.candidate_owners);
    panel.append(row);
  });
  return panel;
}

function renderGoalExecutionInboxActions() {
  const summary = goalExecutionSummaryPayload();
  const actions = Array.isArray(summary?.action_queue)
    ? summary.action_queue
    : [];
  if (!actions.length) return null;
  const details = node("details", "needs-attention goal-execution-inbox-actions");
  details.open = true;
  const summaryRow = node("summary");
  summaryRow.append(
    node("span", "", "Goal execution actions"),
    node("strong", "", String(actions.length)),
  );
  details.append(
    summaryRow,
    node(
      "p",
      "attention-intro",
      "These Tony actions unblock Goal-derived Agent work. Template insertion is local until Submit answer is pressed.",
    ),
  );
  const nextAction = String(summary.next_action || "").trim();
  if (nextAction) {
    details.append(node("p", "goal-execution-next-action", `Next action: ${nextAction}`));
  }
  const plan = recommendedGoalExecutionUnblockPlan(summary);
  if (plan) {
    const planActions = node("div", "attention-actions goal-execution-plan-actions");
    const runPlan = node("button", "secondary-button goal-execution-unblock-plan", "Run recommended unblock plan");
    runPlan.type = "button";
    const planError = node("p", "form-error is-hidden");
    planError.setAttribute("role", "alert");
    runPlan.addEventListener("click", async () => {
      try {
        await applyGoalExecutionRecommendedActions(summary, runPlan, planError);
      } catch (_error) {
        // Error copy is rendered inline.
      }
    });
    planActions.append(runPlan);
    details.append(planActions, planError);
  }
  const queue = renderGoalExecutionActionQueue(summary);
  if (queue) details.append(queue);
  return details;
}

function renderGoalExecutionSurface() {
  const section = node("section", "goal-execution-surface");
  section.id = "agent-goal-execution";
  section.setAttribute("aria-labelledby", "agent-goal-execution-heading");
  const heading = node("h3", "", "Goal execution");
  heading.id = "agent-goal-execution-heading";
  const status = node("p", "goal-execution-read-state");
  status.id = "agent-goal-execution-state";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const list = node("ol", "goal-execution-list");
  list.id = "agent-goal-execution-list";
  const summary = renderGoalExecutionSummary();
  const lastRun = state.goalExecution?.last_run;
  const goalExecutionReadState = state.goalExecution?.read_state;
  if (
    !lastRun &&
    goalExecutionReadState?.surface === "goal_execution" &&
    goalExecutionReadState?.status === "loading"
  ) {
    status.textContent = "Reading Goal execution…";
  } else if (state.goalExecutionLoading && !state.goalExecution) {
    status.textContent = "Reading Goal execution…";
  } else if (state.goalExecutionError && state.goalExecution) {
    status.textContent = "Last verified Goal execution remains visible; refresh failed.";
  } else if (state.goalExecutionError) {
    status.textContent = "Goal execution is temporarily unavailable.";
    const retry = node("button", "secondary-button goal-execution-retry", "Try again");
    retry.type = "button";
    retry.addEventListener("click", () => void loadGoalExecution({ force: true }));
    section.append(heading, status, retry, list);
    return section;
  } else if (state.goalExecutionLoading) {
    status.textContent = "Refreshing Goal execution; last verified state remains visible.";
  } else if (!lastRun) {
    status.textContent = state.goalExecution?.mode === "off"
      ? "Automatic Goal execution is off."
      : "Waiting for the first bounded Goal execution readback.";
  } else {
    status.textContent = `Last verified ${new Date(lastRun.ran_at).toLocaleString()} · ${state.goalExecution.mode}`;
  }
  const decisions = Array.isArray(lastRun?.decisions) ? lastRun.decisions : [];
  goalExecutionRows(decisions).forEach((row) => list.append(row));
  if (!list.children.length) {
    list.append(node("li", "goal-execution-empty", "No bounded Goal-derived work is currently visible."));
  }
  section.append(heading, status);
  if (summary) section.append(summary);
  section.append(list);
  return section;
}

function renderGoalExecutionDetail(container, { goalSlug = null, projectSlug = null } = {}) {
  if (!container) return;
  const decisions = Array.isArray(state.goalExecution?.last_run?.decisions)
    ? state.goalExecution.last_run.decisions
    : [];
  const scoped = decisions.filter((decision) => {
    if (goalSlug) return decision.goal_slug === goalSlug;
    if (!projectSlug) return false;
    return goalExecutionProject(decision)?.slug === projectSlug;
  });
  const heading = node("h3", "", "Goal execution");
  const list = node("ol", "goal-execution-list compact");
  scoped.forEach((decision) => list.append(goalExecutionRow(
    decision,
    null,
    `detail:${goalSlug || projectSlug}:${decision.goal_slug}`,
  )));
  if (!scoped.length) {
    list.append(node("li", "goal-execution-empty", state.goalExecutionLoading
      ? "Reading Goal execution…"
      : "No derived execution state is linked here yet."));
  }
  container.replaceChildren(heading, list);
}

async function loadGoalExecution({ force = false } = {}) {
  if (state.goalExecutionLoadPromise && !force) return state.goalExecutionLoadPromise;
  state.goalExecutionLoading = true;
  state.goalExecutionError = "";
  if (state.activeView === "agent-work") render();
  state.goalExecutionLoadPromise = (async () => {
    try {
      const response = await fetch("/api/goal-execution", {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Goal execution could not be read.");
      state.goalExecution = payload;
      state.goalExecutionLoaded = true;
    } catch (error) {
      state.goalExecutionError = error.message || "Goal execution could not be read.";
    } finally {
      state.goalExecutionLoading = false;
      if (state.activeView === "agent-work") render();
      if (state.selectedKind === "goal") {
        renderGoalExecutionDetail(elements.goalDetailExecution, { goalSlug: state.selectedSlug });
      } else if (state.selectedKind === "project") {
        renderGoalExecutionDetail(elements.projectDetailExecution, { projectSlug: state.selectedSlug });
      }
    }
    return state.goalExecution;
  })().finally(() => {
    state.goalExecutionLoadPromise = null;
  });
  return state.goalExecutionLoadPromise;
}

function renderAgentWorkView({ historyOpen = false } = {}) {
  const wrapper = node("section", "agent-work-view");
  if (!state.agents.length) {
    wrapper.append(
      node(
        "div",
        "section-empty",
        "No verified GBrain agent profiles are available.",
      ),
    );
    wrapper.append(renderGoalExecutionSurface(), renderSystemHandoffAttention(), renderUnifiedHandoffHistory({ historyOpen }));
    return wrapper;
  }
  const grid = node("div", "agent-profile-grid");
  state.agents.forEach((agent) => {
    const card = node("article", "agent-profile-card");
    const profile = actionIcon("⋯", `Open ${agent.name} profile`, { className: "agent-card-profile-button" });
    profile.addEventListener("click", () => openAgentProfile(agent));
    const allWork = agentWorkFor(agent);
    const coldAgentWorkLoading = !state.agentWorkLoaded &&
      !allWork.length &&
      (state.agentWorkLoading || state.agentWorkReadState?.refreshing);
    const heading = node("div", "agent-profile-heading");
    heading.append(
      ownerBadge({
        name: agent.name,
        avatar: agent.avatar,
      }),
      node(
        "span",
        "agent-work-count",
        coldAgentWorkLoading ? "Reading work…" : `${allWork.length} work items`,
      ),
      profile,
    );
    const goals = agent.default_goal_slugs
      .map((slug) => (state.snapshot?.goals || []).find((goal) => goal.slug === slug))
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
    const delegatedWork = state.agentTasks.filter(
      (task) => activeTemporaryExecution(task)?.executor_agent === agent.slug,
    );
    const counts = agentStatusCounts(allWork);
    const work = allWork.filter((task) => task.status !== "proposed");
    const latest = work.slice().sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")))[0];
    const recentCompletion = latestTaskByStatus(allWork, "completed");
    const dispatcherAttention = work.find((task) => taskDispatcherAttention(task));
    const dispatcherAttentionCopy = taskDispatcherAttention(dispatcherAttention);
    const nextWork = work.find((task) => Array.isArray(task.open_todos) && task.open_todos.length) || latest;
    const workSummary = node("div", "agent-work-summary");
    workSummary.append(node("h3", "", "Current work"));
    workSummary.append(node("span", "agent-work-kind", "Owned work"));
    workSummary.append(node(
      "strong",
      "",
      dispatcherAttentionCopy
        ? `${dispatcherAttentionCopy.state} · ${dispatcherAttentionCopy.summary}`
        : coldAgentWorkLoading
          ? "Reading typed agent task collections…"
          : work.length
            ? `${counts.active} active · ${counts.proposed} proposed · ${counts.blocked} blocked · ${counts.completed} completed`
            : "No authorized work yet",
    ));
    const openTodoCount = work.reduce(
      (count, task) => count + (Array.isArray(task.open_todos) ? task.open_todos.length : 0),
      0,
    );
    const nextLine = node("span", "");
    if (nextWork) {
      nextLine.append(
        document.createTextNode(`${todoSummary(nextWork)} · ${openTodoCount} open TODO${openTodoCount === 1 ? "" : "s"} · `),
        taskDetailLink(nextWork, nextWork.title || nextWork.summary || nextWork.slug),
      );
    } else if (coldAgentWorkLoading) {
      nextLine.textContent = "Reading current tasks and open TODOs from GBrain…";
    } else {
      nextLine.textContent = "No current task or open TODO recorded.";
    }
    workSummary.append(nextLine);
    workSummary.append(node("span", "", dispatcherAttentionCopy ? dispatcherAttentionCopy.detail : coldAgentWorkLoading ? "Reading verified completion history…" : recentCompletion ? `Recent verified completion: ${recentCompletion.title || recentCompletion.summary}` : "No verified completion yet."));
    const delegatedSummary = node("div", "agent-work-summary delegated-work-summary");
    delegatedSummary.append(
      node("h3", "", "Additional delegated work"),
      node("strong", "", delegatedWork.length ? `${delegatedWork.length} eligible planned item${delegatedWork.length === 1 ? "" : "s"}` : "No additional delegated work"),
    );
    card.append(
      heading,
      node(
        "p",
        "",
        goals.length
          ? `${goals.length} default goal${goals.length === 1 ? "" : "s"}`
          : "No goals assigned yet",
      ),
      node("p", "agent-runtime-label", agentRuntimeLabel(agent)),
      goalList,
      workSummary,
      delegatedSummary,
      ...(agent.runtime === "openclaw" ? [] : [renderAgentGoalExecution(agent)]),
      renderAgentHandoffStatus(agent),
    );
    if (agent.runtime === "openclaw" || OPENCLAW_PAIR_BY_SOURCE[agent.slug]) {
      card.append(renderDelegationControls(agent));
    }
    if (agent.chat_url) {
      const chat = node("a", "secondary-button", "Open Codex chat");
      chat.href = agent.chat_url;
      chat.target = "_blank";
      chat.rel = "noreferrer";
      card.append(chat);
    }
    grid.append(card);
  });
  wrapper.append(grid, renderGoalExecutionSurface());
  wrapper.append(renderSystemHandoffAttention(), renderUnifiedHandoffHistory({ historyOpen }));
  return wrapper;
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
  if (projectsColdLoading()) {
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
      ? detailReturnFocusAnchor(returnFocus, slug)
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
  renderGoalExecutionDetail(elements.projectDetailExecution, { projectSlug: project.slug });
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
  if (state.agentsLoadPromise) return state.agentsLoadPromise;
  state.agentsLoadPromise = (async () => {
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
      state.agentProfileIssues = Array.isArray(payload.issues)
        ? payload.issues
        : [];
      syncAgentIssues();
      state.agentsLoaded = true;
      await loadAgentDelegations();
      render();
    } catch (_error) {
      // Keep the last verified cards visible when a refresh is transiently unavailable.
    } finally {
      state.agentsLoading = false;
    }
  })().finally(() => {
    state.agentsLoadPromise = null;
  });
  return state.agentsLoadPromise;
}

async function loadAgentDelegations() {
  if (state.delegationsLoading) return false;
  state.delegationsLoading = true;
  state.delegationsError = "";
  try {
    const response = await fetch("/api/agent-delegations", {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Temporary delegation could not be read.");
    state.delegations = Array.isArray(payload.delegations) ? payload.delegations : [];
    state.delegationsLoaded = true;
    return true;
  } catch (error) {
    state.delegationsError = error.message || "Temporary delegation could not be read.";
    return false;
  } finally {
    state.delegationsLoading = false;
  }
}

function loadAgentWork() {
  if (state.agentWorkLoadPromise) return state.agentWorkLoadPromise;
  state.agentWorkLoadPromise = (async () => {
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
      state.agentWorkReadState = payload.read_state || null;
      if (response.status === 200) {
        state.agentTasks = Array.isArray(payload.tasks) ? payload.tasks : [];
        state.agentWorkIssues = Array.isArray(payload.issues)
          ? payload.issues
          : [];
        syncAgentIssues();
        state.agentWorkLoaded = true;
      }
      if (payload.read_state?.error) {
        state.agentWorkError = payload.read_state.error;
      }
      if (payload.read_state?.refreshing) scheduleSurfacePoll("agent_work");
    } catch (error) {
      state.agentWorkError = error.message || "Agent work could not be read.";
    } finally {
      state.agentWorkLoading = false;
      render();
    }
  })().finally(() => {
    state.agentWorkLoadPromise = null;
  });
  return state.agentWorkLoadPromise;
}

function scheduleSurfacePoll(surface) {
  const timerKey = surface === "tasks"
    ? "taskSurfacePollTimer"
    : surface === "proposals"
      ? "proposalSurfacePollTimer"
      : surface === "projects"
        ? "projectSurfacePollTimer"
        : surface === "agent_work"
          ? "agentWorkSurfacePollTimer"
          : "systemTicketSurfacePollTimer";
  if (state[timerKey] !== null) return;
  state[timerKey] = window.setTimeout(() => {
    state[timerKey] = null;
    if (document.hidden) return;
    if (surface === "tasks") void loadTasks({ reason: "poll" });
    else if (surface === "proposals") void loadProposals({ poll: true });
    else if (surface === "projects") void loadProjects({ poll: true });
    else if (surface === "agent_work") void loadAgentWork({ poll: true });
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
  const { refresh = false, poll = false } = arguments[0] || {};
  if (state.projectsLoadPromise) return state.projectsLoadPromise;
  state.projectsLoadPromise = (async () => {
    state.projectsLoading = !state.projects.length;
    state.projectsError = "";
    if (state.snapshot) render();
    try {
      const options = {
        headers: { Accept: "application/json" },
        cache: "no-store",
      };
      const response = refresh
        ? await fetch("/api/projects?refresh=1", options)
        : await fetch("/api/projects", options);
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || "Projects could not be read from GBrain.");
      }
      state.projectsReadState = payload.read_state || null;
      if (response.status === 200) {
        state.projects = Array.isArray(payload.projects) ? payload.projects : [];
        state.projectIssues = Array.isArray(payload.issues) ? payload.issues : [];
        state.projectWarningStateError = payload.warning_state_error || "";
        state.projectsLoaded = true;
      }
      if (payload.read_state?.error) {
        state.projectsError = payload.read_state.error;
      }
      if (response.status === 202) scheduleSurfacePoll("projects");
      if (payload.read_state?.refreshing) scheduleSurfacePoll("projects");
    } catch (error) {
      state.projectsError =
        error.message || "Projects could not be read from GBrain.";
    } finally {
      state.projectsLoading = false;
      if (state.snapshot) render();
    }
  })().finally(() => {
    state.projectsLoadPromise = null;
  });
  return state.projectsLoadPromise;
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

async function readExactGoal(goalSlug) {
  const response = await fetch(`/api/goals/${encodeURIComponent(goalSlug)}`, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(result.error || "Goal could not be read from GBrain.");
    error.code = result.code;
    throw error;
  }
  if (!result.goal || result.goal.slug !== goalSlug) {
    throw new Error("GBrain returned a different Goal during exact readback.");
  }
  return result.goal;
}

function upsertGoalInSnapshot(goal) {
  if (!state.snapshot || !Array.isArray(state.snapshot.goals)) return;
  const index = state.snapshot.goals.findIndex((item) => item.slug === goal.slug);
  if (index === -1) {
    state.snapshot.goals = [goal, ...state.snapshot.goals];
  } else {
    state.snapshot.goals[index] = { ...state.snapshot.goals[index], ...goal };
  }
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
    const verifiedGoal = await readExactGoal(result.goal.slug);
    if (verifiedGoal.title !== result.goal.title) {
      throw new Error(
        "GBrain accepted the goal, but exact Goal readback did not match the write.",
      );
    }
    await loadTasks();
    upsertGoalInSnapshot(verifiedGoal);
    elements.newGoalDialog.close();
    state.activeView = "goals";
    showMutationStatus(
      editing ? "Goal changes verified in GBrain." : "Goal created, linked, and verified in GBrain.",
      "success",
    );
    selectGoal(verifiedGoal.slug);
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
      const open = node("button", "secondary-button attention-task-open", "Open task");
      open.type = "button";
      open.dataset.slug = task.slug;
      open.addEventListener("click", () => selectTask(task.slug, null, open));
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

function renderCanonicalRootIssues() {
  const issues = [
    ...(state.snapshot?.issues || []),
    ...state.projectIssues,
  ].filter((issue) => issue.category === "canonical_root_data");
  const seen = new Set();
  const unique = issues.filter((issue) => {
    const key = `${issue.slug}:${issue.message}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  if (!unique.length) return null;
  const section = node("section", "needs-attention canonical-root-issues");
  section.append(
    node("h2", "", "Canonical data needs attention"),
    node(
      "p",
      "attention-intro",
      "Mission Control is withholding an empty view until the verified GBrain root and typed membership links are restored.",
    ),
  );
  const list = node("div", "attention-list");
  unique.forEach((issue) => {
    const item = node("article", "attention-item");
    item.append(
      node("h3", "", issue.slug || "Canonical root"),
      node("p", "", issue.message || "A required canonical root could not be verified."),
      node("p", "attention-impact", issue.impact || "Refresh the canonical root and retry."),
    );
    if (issue.repair_action) {
      item.append(node("p", "attention-repair", issue.repair_action));
    }
    list.append(item);
  });
  section.append(list);
  return section;
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

function markHandoffFocus(element, key) {
  element.dataset.handoffFocus = key;
  return element;
}

function captureHandoffFocus() {
  const control = document.activeElement?.closest?.("[data-handoff-focus]");
  return control?.dataset?.handoffFocus || null;
}

function isHandoffTaskOrigin(element) {
  const origin = element?.closest?.(".handoff-event-task") || element;
  return origin?.dataset?.handoffTask === "true" ? origin : null;
}

function detailReturnFocusAnchor(element, slug) {
  const anchor = { element, slug };
  if (element?.dataset?.goalExecutionOrigin) {
    anchor.goalExecutionOrigin = element.dataset.goalExecutionOrigin;
  }
  const handoffOrigin = isHandoffTaskOrigin(element);
  if (handoffOrigin) {
    anchor.handoffTask = true;
    anchor.sequence = handoffOrigin.dataset.sequence || "";
    anchor.correlationId = handoffOrigin.dataset.correlationId || "";
  }
  return anchor;
}

function isHandoffDetailFallbackFocus(element) {
  const key = element?.dataset?.handoffFocus;
  return key === "load-status" || key === "filter:correlation_id";
}

function restoreHandoffFocus(key = state.handoffLogFocusKey) {
  if (!key) return;
  if (state.detailFocusReturnAnchor) return;
  window.requestAnimationFrame(() => {
    const selector = `[data-handoff-focus="${CSS.escape(key)}"]`;
    let replacement = document.querySelector(selector);
    const unavailable = (element) => (
      !element || element.disabled || element.hidden || element.classList.contains("is-hidden")
    );
    const fallbackSelector = {
      "filter-clear-correlation": '[data-handoff-focus="filter:correlation_id"]',
      "load-more": '[data-handoff-focus="load-status"]',
    }[key];
    if (unavailable(replacement) && fallbackSelector) {
      replacement = document.querySelector(fallbackSelector);
    }
    if (unavailable(replacement)) return;
    replacement.focus({ preventScroll: true });
    if (state.handoffLogFocusKey === key) state.handoffLogFocusKey = null;
  });
}

function render() {
  const focusedTooltipTarget = document.activeElement?.closest?.(".has-tooltip") || null;
  const focusedSystemTicketSlug = document.activeElement?.closest?.(".system-ticket-card")?.dataset.slug || null;
  if (state.activeView === "agent-work" && state.agentHandoffHistoryOpen) {
    state.handoffLogFocusKey = captureHandoffFocus() || state.handoffLogFocusKey;
  }
  hideHudTooltip();
  window.requestAnimationFrame(() => {
    restorePendingDetailFocus();
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
  const canonicalRootIssueViews = new Set([
    "today",
    "all",
    "completed",
    "goals",
    "projects",
    "board",
    "week",
    "blocked",
  ]);
  const canonicalRootIssues = canonicalRootIssueViews.has(view)
    ? renderCanonicalRootIssues()
    : null;
  const content = canonicalRootIssues
    ? document.createDocumentFragment()
    : view === "artifacts"
    ? renderArtifactsView()
    : !state.snapshot
    ? view === "system-tickets"
      ? renderSystemTicketsView()
      : view === "settings"
        ? renderSettingsView()
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
      : view === "settings"
        ? renderSettingsView()
        : renderListView(view);
  const attention = view === "inbox" ? renderNeedsAttention() : null;
  const goalExecutionAttention = view === "inbox"
    ? renderGoalExecutionInboxActions()
    : null;
  const proposals = view === "inbox" ? renderProposedWork() : null;
  elements.viewSurface.replaceChildren(
    ...[
      ...(canonicalRootIssues ? [canonicalRootIssues] : []),
      ...(goalExecutionAttention ? [goalExecutionAttention] : []),
      ...(attention ? [attention] : []),
      ...(proposals ? [proposals] : []),
      content,
    ],
  );
  if (view === "agent-work" && state.agentHandoffHistoryOpen) restoreHandoffFocus();
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
  if (state.selectedKind === "goal") {
    renderGoalExecutionDetail(elements.goalDetailExecution, { goalSlug: state.selectedSlug });
  } else if (state.selectedKind === "project") {
    renderGoalExecutionDetail(elements.projectDetailExecution, { projectSlug: state.selectedSlug });
  }
  syncMobileDetailModalState();
}

function renderSettingsView() {
  const section = node("section", "settings-view");
  const heading = node("div", "projects-view-heading");
  const copy = node("div");
  copy.append(
    node("h2", "", "Settings"),
    node("p", "", "Local Mission Control display preferences. These controls do not mutate GBrain."),
  );
  heading.append(copy);
  section.append(heading);

  const card = node("article", "settings-card");
  const label = node("label", "settings-field");
  const labelText = node("span", "settings-label", "Completion celebration");
  const select = document.createElement("select");
  select.id = "completion-celebration-preference";
  select.setAttribute("aria-describedby", "completion-celebration-help");
  [
    ["full", "Full Command Confirmation Sweep"],
    ["reduced", "Reduced confirmation"],
    ["off", "Off"],
  ].forEach(([value, text]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = text;
    select.append(option);
  });
  select.value = completionCelebrationStoredPreference();
  select.addEventListener("change", () => {
    setCompletionCelebrationPreference(select.value);
  });
  label.append(labelText, select);
  const help = node(
    "p",
    "settings-help",
    "Local browser preference only. Mission Control celebrates after a completed Task is saved and read back from GBrain.",
  );
  help.id = "completion-celebration-help";
  card.append(label, help);
  const landingCard = node("article", "settings-card");
  const landingLabel = node("label", "settings-field");
  landingLabel.append(node("span", "settings-label", "Default landing page"));
  const landingSelect = document.createElement("select");
  landingSelect.id = "default-landing-view-preference";
  landingSelect.setAttribute("aria-describedby", "default-landing-view-help");
  [
    ["board", "Board"], ["today", "Today"], ["week", "Calendar"],
    ["inbox", "Inbox"], ["agent-work", "Agents"], ["artifacts", "Artifacts"],
    ["blocked", "Blocked"], ["completed", "Completed"], ["all", "All Tasks"],
    ["projects", "Projects"], ["goals", "Goals"],
  ].forEach(([value, text]) => {
    const option = node("option", "", text);
    option.value = value;
    landingSelect.append(option);
  });
  landingSelect.value = storedDefaultLandingView();
  landingSelect.addEventListener("change", () => {
    landingSelect.value = setDefaultLandingView(landingSelect.value);
  });
  landingLabel.append(landingSelect);
  const landingHelp = node(
    "p",
    "settings-help",
    "Used only when Mission Control opens without an explicit view route. Bookmarks and sidebar navigation always win.",
  );
  landingHelp.id = "default-landing-view-help";
  landingCard.append(landingLabel, landingHelp);
  section.append(card, landingCard);
  window.requestAnimationFrame(() => {
    if (state.activeView === "settings" && document.activeElement === document.body) {
      select.focus({ preventScroll: true });
    }
  });
  return section;
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

function renderSystemTicketList(
  container,
  entries,
  emptyCopy,
  sourceSlug = "",
  referenceSection = "",
) {
  container.replaceChildren();
  if (!Array.isArray(entries) || !entries.length) {
    container.append(node("li", "is-empty", emptyCopy));
    return;
  }
  entries.forEach((entry, index) => {
    const item = document.createElement("li");
    renderSafeMarkdown(item, entry, {
      sourceKind: "system-ticket",
      sourceSlug,
      referenceScope: `${referenceSection}:${index}`,
    });
    container.append(item);
  });
}

function findSystemTicket(ticketSlug) {
  return [...state.systemTickets, ...state.completedSystemTickets].find(
    (item) => item.slug === ticketSlug,
  );
}

function selectSystemTicket(ticketSlug, originControl = null) {
  const ticket = findSystemTicket(ticketSlug);
  if (!ticket) return;
  if (!originControl?.dataset?.systemTicketReferenceSourceKind) {
    state.systemTicketMarkdownReturn = null;
  }
  if (originControl instanceof HTMLElement) {
    state.detailReturnFocus = detailReturnFocusAnchor(originControl, ticket.slug);
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
  const hasDisplayMarkdown =
    typeof ticket.display_markdown === "string" && ticket.display_markdown.trim();
  elements.systemTicketDetailMarkdownSection.classList.toggle("is-hidden", !hasDisplayMarkdown);
  elements.systemTicketDetailStructured.classList.toggle("is-hidden", Boolean(hasDisplayMarkdown));
  if (hasDisplayMarkdown) {
    renderSafeMarkdown(
      elements.systemTicketDetailMarkdown,
      ticket.display_markdown,
      { sourceKind: "system-ticket", sourceSlug: ticket.slug, referenceScope: "body" },
    );
  } else {
    renderSafeMarkdown(
      elements.systemTicketDetailRequest,
      ticket.verbatim_request || "No verbatim request recorded.",
      { sourceKind: "system-ticket", sourceSlug: ticket.slug, referenceScope: "request" },
    );
    renderSafeMarkdown(
      elements.systemTicketDetailCriteria,
      ticket.acceptance_criteria || "No acceptance criteria recorded.",
      { sourceKind: "system-ticket", sourceSlug: ticket.slug, referenceScope: "criteria" },
    );
    renderSystemTicketList(
      elements.systemTicketDetailEvidence,
      ticket.linked_evidence,
      "No linked evidence recorded.",
      ticket.slug,
      "evidence",
    );
    renderSystemTicketList(
      elements.systemTicketDetailImplementation,
      ticket.implementation_receipts,
      "No implementation receipt recorded.",
      ticket.slug,
      "implementation",
    );
    renderSystemTicketList(
      elements.systemTicketDetailQa,
      ticket.qa_receipts,
      "No independent QA receipt recorded.",
      ticket.slug,
      "qa",
    );
  }
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
    const previousSystemTicketsRefreshing = Boolean(state.systemTicketsReadState?.refreshing);
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
    if (
      response.status === 200 &&
      previousSystemTicketsRefreshing &&
      !payload.read_state?.refreshing &&
      state.showCompletedSystemTickets &&
      state.completedSystemTickets.length &&
      !state.completedSystemTicketsHasMore
    ) {
      state.completedSystemTicketsHasMore = true;
    }
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
    state.completedSystemTicketsOffset = state.completedSystemTickets.length;
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
    node("h4", "task-todo-title-copy", todo.text),
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
  const editActions = node("div", "task-todo-edit-actions");
  const saveDone = node("button", "secondary-button", "Save & Mark Done");
  saveDone.type = "button";
  saveDone.setAttribute("aria-label", `Save TODO edits and mark Done: ${todo.text}`);
  const saveComplete = node("button", "secondary-button", "Save & Complete Task");
  saveComplete.type = "button";
  saveComplete.setAttribute("aria-label", `Save TODO edits, mark Done, and complete parent Task: ${todo.text}`);
  const combinedActionsEnabled = canUseCombinedTodoActions(todo);
  [saveDone, saveComplete].forEach((button) => {
    button.disabled = !combinedActionsEnabled;
    button.setAttribute(
      "aria-disabled",
      combinedActionsEnabled ? "false" : "true",
    );
    if (!combinedActionsEnabled) {
      setHudTooltip(
        button,
        "This combined TODO action is unavailable for handoff questions, completed tasks, and cancelled tasks.",
      );
    }
  });
  editActions.append(editSubmit, saveDone, saveComplete);
  editForm.append(editText, editDetail, editActions);
  editForm.addEventListener("submit", (event) => {
    event.preventDefault();
    editTaskTodo(todo, editText.value, editDetail.value, editSubmit);
  });
  saveDone.addEventListener("click", () => {
    performTodoEditAction(todo, editText.value, editDetail.value, saveDone, "mark_done");
  });
  saveComplete.addEventListener("click", () => {
    performTodoEditAction(todo, editText.value, editDetail.value, saveComplete, "complete_task");
  });

  const commentsHeading = node("h5", "", "Comments");
  const comments = node("ol", "task-todo-comments");
  (Array.isArray(todo.comments) ? todo.comments : []).forEach((comment) => {
    const item = node("li");
    item.append(
      node("p", "task-todo-comment-copy", comment.body),
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
  const dispatcherHandoff = task?.dispatcher_handoff;
  const blockers = Array.isArray(task?.blockers) ? task.blockers : [];
  const show = Boolean(handoff || dispatcherHandoff || task?.status === "blocked");
  elements.taskHandoffPanel.classList.toggle("is-hidden", !show);
  elements.taskHandoffAnswerForm.classList.add("is-hidden");
  elements.taskHandoffQuestion.classList.add("is-hidden");
  elements.taskHandoffError.classList.add("is-hidden");
  if (!show) return;

  if (!handoff && dispatcherHandoff) {
    const status = dispatcherHandoff.status || "unavailable";
    elements.taskHandoffHeading.textContent = "Verified Agent handoff needs system review";
    elements.taskHandoffCopy.textContent = `Latest dispatcher status: ${status}. Inspect Handoff History for execution recovery evidence before retrying.`;
    elements.taskHandoffQuestion.textContent = "";
    return;
  }

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
  const todoIssues = Array.isArray(task.todo_issues) ? task.todo_issues : [];
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
  if (todoIssues.length) {
    elements.taskTodoError.textContent = (
      todoIssues[0]?.impact ||
      todoIssues[0]?.message ||
      "The canonical TODO list is unavailable."
    );
    elements.taskTodoError.classList.remove("is-hidden");
  } else {
    elements.taskTodoError.classList.add("is-hidden");
  }
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

function canUseCombinedTodoActions(todo) {
  if (!todo || todo.status === "done") return false;
  const task = findTaskBySlug(todo.parent_task);
  if (!task || ["completed", "cancelled"].includes(task.status)) return false;
  if (isActiveHandoffQuestion(todo, task)) return false;
  return true;
}

function setTodoEditActionPending(submit, pending) {
  const form = submit?.closest?.("form");
  const controls = form
    ? Array.from(form.querySelectorAll("button"))
    : submit ? [submit] : [];
  controls.forEach((control) => {
    control.disabled = Boolean(pending);
  });
}

function mergeVerifiedTodoIntoTask(task, todo) {
  if (!task || !todo) return task;
  const todos = Array.isArray(task.todos) ? [...task.todos] : [];
  const index = todos.findIndex((candidate) => candidate.slug === todo.slug);
  if (index === -1) todos.push(todo);
  else todos[index] = todo;
  return { ...task, todos };
}

function verifiedTodoActionPartialMessage(flags, error) {
  const parts = [];
  if (flags.saved) parts.push("TODO edits were verified");
  if (flags.done) parts.push("TODO was marked Done");
  if (flags.completingTask) {
    parts.push(`Task completion was not verified: ${statusErrorMessage(error)}`);
  } else if (flags.markingDone) {
    parts.push(`Mark Done was not verified: ${todoErrorMessage(error)}`);
  } else {
    parts.push(todoErrorMessage(error));
  }
  return parts.join(". ") + (parts.length ? "." : "");
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

async function performTodoEditAction(todo, text, detail, submit, action = "save") {
  const flags = {
    saved: false,
    done: false,
    markingDone: action === "mark_done" || action === "complete_task",
    completingTask: action === "complete_task",
  };
  elements.taskTodoError.classList.add("is-hidden");
  const originalLabel = submit?.textContent || "";
  setTodoEditActionPending(submit, true);
  if (submit) submit.textContent = "Saving…";
  try {
    const previousTask = flags.completingTask
      ? { ...findTaskBySlug(todo.parent_task) }
      : null;
    const updated = await todoMutation(`/api/todos/${encodeURIComponent(todo.slug)}`, "PATCH", {
      text,
      detail,
      expected_updated_at: todo.updated_at,
      actor: "people/tony-guan",
      source: "mission_control",
      idempotency_key: crypto.randomUUID(),
    });
    flags.saved = true;
    applyVerifiedTodoMutation(todo.parent_task, updated, { slug: todo.slug, control: "summary" });
    let verifiedTodo = updated;
    if (flags.markingDone && verifiedTodo.status !== "done") {
      const doneTodo = await todoMutation(
        `/api/todos/${encodeURIComponent(todo.slug)}/status`,
        "PATCH",
        {
          status: "done",
          expected_updated_at: verifiedTodo.updated_at,
          actor: "people/tony-guan",
          source: "mission_control",
          idempotency_key: crypto.randomUUID(),
        },
      );
      verifiedTodo = doneTodo;
      flags.done = true;
      applyVerifiedTodoMutation(todo.parent_task, verifiedTodo, { slug: todo.slug, control: "summary" });
    } else if (verifiedTodo.status === "done") {
      flags.done = true;
    }
    if (flags.completingTask) {
      const receipt = await requestTaskStatus(
        todo.parent_task,
        "completed",
        { completionPreviousTask: previousTask },
      );
      const current = findTaskBySlug(todo.parent_task);
      reconcileVerifiedTask(mergeVerifiedTodoIntoTask(receipt.task, verifiedTodo));
      if (current?.slug === state.selectedSlug || receipt.task.slug === state.selectedSlug) {
        state.selectedKind = "task";
        state.selectedSlug = receipt.task.slug;
        selectTask(receipt.task.slug);
      }
      showMutationStatus(
        "TODO saved, marked Done, and Task completed in GBrain.",
        "success",
      );
      return;
    }
    showMutationStatus(
      action === "mark_done"
        ? "TODO saved and marked Done in GBrain."
        : "TODO edit verified in GBrain.",
      "success",
    );
  } catch (error) {
    elements.taskTodoError.textContent =
      flags.saved || flags.done
        ? verifiedTodoActionPartialMessage(flags, error)
        : todoErrorMessage(error);
    elements.taskTodoError.classList.remove("is-hidden");
    if (error.code === "todo_changed") {
      await refreshTaskTodos(todo.parent_task, { slug: todo.slug, control: "summary" });
    }
  } finally {
    setTodoEditActionPending(submit, false);
    if (submit) submit.textContent = originalLabel;
  }
}

async function editTaskTodo(todo, text, detail, submit) {
  return performTodoEditAction(todo, text, detail, submit, "save");
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
    if (!slug) return "Default Goal";
    return state.snapshot?.goals?.find((goal) => goal.slug === slug)?.title || slug;
  }
  if (kind === "project") {
    if (!slug) return "Default Project";
    return state.projects.find((project) => project.slug === slug)?.title || slug;
  }
  if (!slug) return "No producing Task";
  return findTaskBySlug(slug)?.title || slug;
}

function artifactHierarchyHelp(kind, slug) {
  if (slug) return "";
  if (kind === "goal") return "Items without an explicit Goal relationship";
  if (kind === "project") return "Items without an explicit Project relationship";
  return "";
}

function artifactHierarchyKey(kind, slug, parentKey = "") {
  return `${parentKey}${parentKey ? "/" : ""}${kind}:${slug || "none"}`;
}

function buildArtifactHierarchy() {
  const loadedArtifacts = [...state.artifacts].sort(compareNewestUpdated);
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
  if (entry.kind === "task") toggle.classList.add("is-task-title-only");
  toggle.type = "button";
  toggle.setAttribute("aria-expanded", String(expanded));
  toggle.setAttribute("aria-controls", contentId);
  const toggleItems = [
    node("span", "artifact-hierarchy-chevron", expanded ? "−" : "+"),
    node("strong", "artifact-hierarchy-label", entry.label),
  ];
  if (entry.kind !== "task") {
    toggleItems.push(node("span", "artifact-hierarchy-count", artifactLoadedCountLabel(entry.count)));
  }
  toggle.append(...toggleItems);
  const helper = artifactHierarchyHelp(entry.kind, entry.slug);
  if (helper) {
    toggle.setAttribute("aria-description", helper);
    setHudTooltip(toggle, helper);
  }
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
      .sort(compareNewestUpdated)
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
    const artifactsBySlug = new Map(
      (append ? [...state.artifacts, ...incoming] : incoming)
        .map((artifact) => [artifact.slug, artifact]),
    );
    state.artifacts = [...artifactsBySlug.values()].sort(compareNewestUpdated);
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

function selectArtifact(artifactSlug, originControl = null, { preserveReturnContext = false } = {}) {
  const taskEntries = Array.from(state.taskArtifacts.values()).flatMap((entry) => entry.artifacts || []);
  const artifact = [...state.artifacts, ...taskEntries].find((item) => item.slug === artifactSlug);
  if (!artifact) return;
  const returnFocus = originControl instanceof HTMLElement
    ? originControl
    : document.activeElement instanceof HTMLElement
      ? document.activeElement
    : null;
  if (!preserveReturnContext) {
    state.artifactProducingTaskReturn = null;
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
  elements.artifactDetailTaskLink.classList.toggle("is-hidden", !task);
  elements.artifactDetailTaskLink.onclick = null;
  if (!task) {
    elements.artifactDetailTaskLink.textContent = "";
    elements.artifactDetailTaskLink.removeAttribute("aria-label");
    setHudTooltip(elements.artifactDetailTaskLink, "");
  }
  if (task) {
    const actionLabel = `Open producing Task: ${task.title}`;
    elements.artifactDetailTaskLink.textContent = "Open producing Task";
    elements.artifactDetailTaskLink.setAttribute("aria-label", actionLabel);
    setHudTooltip(elements.artifactDetailTaskLink, actionLabel);
    elements.artifactDetailTaskLink.onclick = () => {
      state.artifactProducingTaskReturn = {
        artifactSlug: artifact.slug,
        taskSlug: task.slug,
        artifactTaskReturn: state.artifactTaskReturn,
        detailReturnFocus: state.detailReturnFocus,
        expanded: new Set(state.artifactExpanded),
      };
      selectTask(task.slug, task, elements.artifactDetailTaskLink);
    };
  }
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
    const relationContext = Array.isArray(artifact.relation_context)
      ? artifact.relation_context
      : [];
    const relationLabels = [];
    if (relationContext.includes("referenced_for_review")) relationLabels.push("Linked for review");
    if (relationContext.includes("produced_for")) relationLabels.push("Produced by this Task");
    const button = node("button", "task-artifact-row");
    button.append(node("span", "task-artifact-title", artifact.title));
    if (relationLabels.length) {
      button.append(node("span", "task-artifact-relation", relationLabels.join(" · ")));
    }
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

function privacySafeEventText(value, maximum = 240) {
  const normalized = String(value || "")
    .replace(/\b(?:bearer|token|secret|password|thread(?:[_ -]?id)?)\b\s*[:=]?\s*\S*/gi, "[redacted]")
    .replace(/https?:\/\/\S+/gi, "[link redacted]")
    .replace(/\b[A-Za-z0-9_-]{24,}\b/g, "[opaque value redacted]")
    .replace(/\s+/g, " ")
    .trim();
  if (!normalized) return "No privacy-safe summary recorded.";
  return normalized.length > maximum
    ? `${normalized.slice(0, maximum - 1)}…`
    : normalized;
}

function redactedCorrelationLabel(value) {
  const safe = String(value || "").replace(/[^A-Za-z0-9._-]/g, "");
  return safe ? `Correlation ••••${safe.slice(-6)}` : "No correlation";
}

function handoffAgentLabel(agentSlug) {
  const profile = state.agents.find((agent) => agent.slug === agentSlug);
  if (profile?.name) return privacySafeEventText(profile.name, 80);
  const leaf = String(agentSlug || "Unassigned").split("/").pop() || "Unassigned";
  return privacySafeEventText(`${leaf[0]?.toUpperCase() || ""}${leaf.slice(1)}`, 80);
}

function eventPassesHandoffTimeFilter(event, filter) {
  if (!filter || filter === "all") return true;
  const occurred = Date.parse(event.occurred_at || "");
  if (!Number.isFinite(occurred)) return false;
  const windows = { hour: 60 * 60 * 1000, day: 24 * 60 * 60 * 1000, week: 7 * 24 * 60 * 60 * 1000 };
  return Date.now() - occurred <= (windows[filter] || Number.POSITIVE_INFINITY);
}

function visibleGraphemes(value) {
  const text = String(value || "");
  if (typeof Intl !== "undefined" && Intl.Segmenter) {
    return Array.from(new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(text), (part) => part.segment);
  }
  return Array.from(text);
}

function truncateVisibleTaskTitle(title, limit = 20) {
  const safe = privacySafeEventText(title, 160);
  const graphemes = visibleGraphemes(safe);
  if (graphemes.length <= limit) return safe;
  return `${graphemes.slice(0, limit).join("")}…`;
}

function handoffStatusLabel(event) {
  const status = String(event?.status || "unknown").replaceAll("_", " ");
  if (event?.status === "dead_letter" || event?.event_type === "delivery_terminal") return "Dead letter";
  if (event?.status === "queued") return "Queued";
  if (event?.status === "leased") return "Leased";
  if (event?.status === "received") return "Received";
  if (event?.status === "acknowledged") return "Acknowledged";
  if (event?.status === "processing" || event?.status === "agent_working") return "Working";
  if (event?.status === "blocked") return "Blocked";
  if (event?.status === "retrying") return "Retrying";
  if (event?.status === "completed" || event?.status === "delivered") return "Completed";
  return privacySafeEventText(status || "Verified", 80);
}

function renderHandoffEvents(events, destination) {
  const list = destination.list;
  list.replaceChildren();
  const showTaskLink = destination.showTaskLink !== false;
  const ordered = [...events]
    .filter((event) => eventPassesHandoffTimeFilter(event, destination.timeFilter))
    .sort((left, right) => Number(left.sequence) - Number(right.sequence));
  ordered.forEach((event) => {
    const deadLetter = event.status === "dead_letter" || event.event_type === "delivery_terminal";
    const item = node("li", `handoff-event${deadLetter ? " is-dead-letter" : ""}`);
    item.dataset.sequence = String(event.sequence);
    const heading = node("div", "handoff-event-heading handoff-event-mainline");
    heading.append(node("strong", "handoff-event-agent", handoffAgentLabel(event.agent_slug)));
    const status = node("span", "handoff-event-status", handoffStatusLabel(event));
    if (showTaskLink) {
      heading.append(node("span", "handoff-event-separator", " - "));
      if (event.task_ref?.available && event.task_ref?.slug && event.task_ref?.title) {
        const fullTitle = privacySafeEventText(event.task_ref.title, 240);
        const taskLink = node(
          "a",
          "handoff-task-link handoff-event-task",
          `Task:${truncateVisibleTaskTitle(fullTitle, 20)}`,
        );
        taskLink.href = `#${event.task_ref.surface === "system_ticket" ? "system-ticket" : "task"}/${encodeURIComponent(event.task_ref.slug)}`;
        taskLink.dataset.slug = event.task_ref.slug;
        taskLink.dataset.handoffTask = "true";
        taskLink.dataset.sequence = String(event.sequence ?? "");
        taskLink.dataset.correlationId = event.correlation_id || "";
        taskLink.title = fullTitle;
        taskLink.setAttribute("aria-label", `Open Task ${fullTitle}`);
        taskLink.addEventListener("click", (clickEvent) => {
          clickEvent.preventDefault();
          openHandoffTaskReference(event, taskLink);
        });
        heading.append(taskLink);
      } else {
        const unavailable = node("span", "handoff-task-unavailable", "Task unavailable");
        unavailable.setAttribute("aria-label", event.task_ref?.reason || "Task unavailable for this handoff event.");
        heading.append(unavailable);
      }
      heading.append(node("span", "handoff-event-separator", " - "));
    }
    heading.append(status);
    const when = node(
      "time",
      "handoff-event-time",
      event.occurred_at ? new Date(event.occurred_at).toLocaleString() : "Time unavailable",
    );
    if (event.occurred_at) when.dateTime = event.occurred_at;
    const meta = node("p", "handoff-event-meta");
    meta.append(when, node("span", "handoff-event-separator", " · "), node("span", "handoff-event-summary", privacySafeEventText(event.detail || event.summary)));
    const controls = node("div", "handoff-event-controls");
    if (deadLetter) controls.append(node("span", "handoff-dead-letter", "Dead letter"));
    item.append(heading, meta, controls);
    list.append(item);
  });
  return ordered.length;
}

function taskHandoffEntry(taskSlug) {
  return state.taskHandoffEvents.get(taskSlug) || {
    events: [],
    total: 0,
    nextSequence: null,
    loading: false,
    error: "",
    stale: false,
    requestToken: 0,
  };
}

function renderTaskHandoffTimeline(taskSlug) {
  const entry = taskHandoffEntry(taskSlug);
  const visible = renderHandoffEvents(entry.events, {
    list: elements.taskHandoffEventList,
    timeFilter: "all",
    showTaskLink: false,
  });
  elements.taskHandoffTotal.textContent = entry.events.length
    ? `${entry.events.length} of ${entry.total}`
    : "";
  elements.taskHandoffEventState.classList.remove("is-error", "is-stale");
  if (entry.loading && !entry.events.length) {
    elements.taskHandoffEventState.textContent = "Loading handoff events…";
  } else if (entry.error && entry.events.length) {
    elements.taskHandoffEventState.textContent = "Last verified handoff events remain visible; refresh failed.";
    elements.taskHandoffEventState.classList.add("is-stale");
  } else if (entry.error) {
    elements.taskHandoffEventState.textContent = "Handoff events are unavailable for this task.";
    elements.taskHandoffEventState.classList.add("is-error");
  } else if (!visible) {
    elements.taskHandoffEventState.textContent = "No handoff events match this task.";
  } else {
    elements.taskHandoffEventState.textContent = "Read-only events in canonical sequence order.";
  }
  elements.taskHandoffLoadMore.classList.toggle("is-hidden", entry.nextSequence === null);
  elements.taskHandoffLoadMore.disabled = entry.loading;
}

function renderTaskTemporaryExecutor(task) {
  const projectedTask = state.agentTasks.find((item) => item.slug === task?.slug) || task;
  const execution = activeTemporaryExecution(projectedTask);
  const ownerSlug = projectedTask?.owner_agent || projectedTask?.owner?.slug || null;
  const lease = execution ? activeDelegationForSource(ownerSlug) : null;
  const executor = execution
    ? state.agents.find((agent) => agent.slug === execution.executor_agent)
    : null;
  elements.taskTemporaryExecutor.classList.toggle("is-hidden", !executor);
  if (!executor) return;
  setCompactAgentAvatar(elements.taskExecutorAvatar, executor);
  elements.taskExecutorName.textContent = `${executor.name} · ${delegationRemainingLabel(lease)}`;
}

async function ensureTaskTemporaryExecutorProjection(task) {
  renderTaskTemporaryExecutor(task);
  const ownerSlug = task?.owner_agent || task?.owner?.slug || null;
  if (
    !ownerSlug ||
    (state.agentsLoaded && state.delegationsLoaded && state.agentWorkLoaded)
  ) return;
  await Promise.all([loadAgents(), loadAgentWork()]);
  if (state.selectedKind === "task" && state.selectedSlug === task.slug) {
    renderTaskTemporaryExecutor(findTaskBySlug(task.slug) || task);
  }
}

function syncTaskHandoffTimelineDisclosure() {
  if (!elements.taskHandoffTimeline || !elements.taskHandoffTimelineHeading) return;
  elements.taskHandoffTimelineHeading.setAttribute(
    "aria-expanded",
    String(Boolean(elements.taskHandoffTimeline.open)),
  );
}

async function readTaskHandoffPage(taskSlug, { reset }) {
  const previous = taskHandoffEntry(taskSlug);
  const returnFocusToTimelineState = (
    !reset && document.activeElement === elements.taskHandoffLoadMore
  );
  const requestToken = previous.requestToken + 1;
  const entry = {
    ...previous,
    loading: true,
    error: "",
    stale: false,
    requestToken,
  };
  state.taskHandoffEvents.set(taskSlug, entry);
  if (state.selectedKind === "task" && state.selectedSlug === taskSlug) {
    renderTaskHandoffTimeline(taskSlug);
    renderTaskTemporaryExecutor(findTaskBySlug(taskSlug));
  }
  const params = new URLSearchParams({
    limit: String(HANDOFF_EVENT_PAGE_SIZE),
    after_sequence: String(reset ? 0 : previous.nextSequence || 0),
  });
  try {
    const response = await fetch(
      `/api/tasks/${encodeURIComponent(taskSlug)}/handoff-events?${params}`,
      { headers: { Accept: "application/json" }, cache: "no-store" },
    );
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Handoff events could not be read.");
    if (taskHandoffEntry(taskSlug).requestToken !== requestToken) return;
    const received = Array.isArray(payload.events) ? payload.events : [];
    const combined = reset ? received : [...previous.events, ...received];
    const snapshotTotal = reset ? Number(payload.total || 0) : previous.total;
    const unique = Array.from(
      new Map(combined.map((event) => [event.sequence, event])).values(),
    ).slice(0, snapshotTotal);
    const nextSequence = unique.length >= snapshotTotal
      ? null
      : Number.isInteger(payload.next_sequence) ? payload.next_sequence : null;
    state.taskHandoffEvents.set(taskSlug, {
      events: unique,
      total: snapshotTotal,
      nextSequence,
      loading: false,
      error: "",
      stale: false,
      requestToken,
    });
  } catch (error) {
    if (taskHandoffEntry(taskSlug).requestToken !== requestToken) return;
    state.taskHandoffEvents.set(taskSlug, {
      ...previous,
      loading: false,
      error: error.message || "Handoff events are unavailable.",
      stale: previous.events.length > 0,
      requestToken,
    });
  }
  if (state.selectedKind === "task" && state.selectedSlug === taskSlug) {
    renderTaskHandoffTimeline(taskSlug);
    if (
      returnFocusToTimelineState &&
      taskHandoffEntry(taskSlug).nextSequence === null
    ) {
      window.requestAnimationFrame(() => {
        const active = document.activeElement;
        if (
          active !== elements.taskHandoffLoadMore &&
          active !== document.body &&
          active?.isConnected
        ) return;
        elements.taskHandoffEventState.focus({ preventScroll: true });
      });
    }
  }
}

async function loadTaskHandoffTimeline(taskSlug) {
  return readTaskHandoffPage(taskSlug, { reset: true });
}

function handoffLogFilters(form = null) {
  if (!form) return { ...state.handoffLogFilters };
  const values = new FormData(form);
  const correlationInput = form.elements.namedItem("correlation_id");
  const typedCorrelation = String(values.get("correlation_id") || "").trim();
  return {
    time: String(values.get("time") || "all"),
    agent_slug: String(values.get("agent_slug") || ""),
    status: String(values.get("status") || ""),
    event_type: String(values.get("event_type") || ""),
    failure: String(values.get("failure") || ""),
    correlation_id: typedCorrelation || (
      correlationInput?.dataset.preserveActive === "true"
        ? state.handoffLogFilters.correlation_id
        : ""
    ),
  };
}

function handoffTimeRange(timeFilter, now = Date.now()) {
  const duration = {
    hour: 60 * 60 * 1000,
    day: 24 * 60 * 60 * 1000,
    week: 7 * 24 * 60 * 60 * 1000,
  }[timeFilter];
  if (!duration) return null;
  return {
    occurredAfter: new Date(now - duration).toISOString(),
    occurredBefore: new Date(now).toISOString(),
  };
}

async function loadHandoffLog({ reset = false, filters = null } = {}) {
  const nextFilters = filters
    ? { ...state.handoffLogFilters, ...filters }
    : state.handoffLogFilters;
  const filtersChanged = JSON.stringify(nextFilters) !== JSON.stringify(state.handoffLogFilters);
  state.handoffLogFilters = nextFilters;
  if (reset && filtersChanged) {
    state.handoffLogEvents = [];
    state.handoffLogTotal = 0;
    state.handoffLogSnapshotTotal = 0;
    state.handoffLogNextSequence = null;
  }
  const requestToken = ++state.handoffLogRequestToken;
  const previousEvents = state.handoffLogEvents;
  const previousTotal = state.handoffLogTotal;
  const previousSnapshotTotal = state.handoffLogSnapshotTotal;
  const previousNextSequence = state.handoffLogNextSequence;
  const previousTimeRange = state.handoffLogTimeRange;
  if (reset) state.handoffLogTimeRange = handoffTimeRange(nextFilters.time);
  state.handoffLogFocusKey = captureHandoffFocus() || state.handoffLogFocusKey;
  state.handoffLogLoading = true;
  state.handoffLogError = "";
  state.handoffLogStale = false;
  if (state.activeView === "agent-work") render();
  const active = handoffLogFilters();
  try {
    const params = new URLSearchParams({
      limit: String(HANDOFF_EVENT_PAGE_SIZE),
      after_sequence: String(reset ? 0 : state.handoffLogNextSequence || 0),
    });
    if (active.agent_slug) params.set("agent_slug", active.agent_slug);
    const effectiveStatus = active.failure || active.status;
    if (effectiveStatus) params.set("status", effectiveStatus);
    if (active.event_type) params.set("event_type", active.event_type);
    if (active.correlation_id) params.set("correlation_id", active.correlation_id);
    if (state.handoffLogTimeRange) {
      params.set("occurred_after", state.handoffLogTimeRange.occurredAfter);
      params.set("occurred_before", state.handoffLogTimeRange.occurredBefore);
    }
    const response = await fetch(`/api/handoff-events?${params}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Handoff events could not be read.");
    if (requestToken !== state.handoffLogRequestToken) return;
    const snapshotTotal = reset
      ? Math.max(0, Number(payload.total || 0))
      : state.handoffLogSnapshotTotal || state.handoffLogTotal;
    const received = Array.isArray(payload.events) ? payload.events : [];
    const events = Array.from(
      new Map([
        ...(reset ? [] : previousEvents),
        ...received,
      ].map((event) => [event.sequence, event])).values(),
    ).slice(0, snapshotTotal);
    const candidate = Number.isInteger(payload.next_sequence)
      ? payload.next_sequence
      : null;
    const nextSequence = events.length >= snapshotTotal ? null : candidate;
    state.handoffLogEvents = events;
    state.handoffLogSnapshotTotal = snapshotTotal || 0;
    state.handoffLogTotal = state.handoffLogSnapshotTotal;
    state.handoffLogNextSequence = nextSequence;
  } catch (error) {
    if (requestToken !== state.handoffLogRequestToken) return;
    state.handoffLogEvents = previousEvents;
    state.handoffLogTotal = previousTotal;
    state.handoffLogSnapshotTotal = previousSnapshotTotal;
    state.handoffLogNextSequence = previousNextSequence;
    state.handoffLogTimeRange = previousTimeRange;
    state.handoffLogError = error.message || "Handoff events are unavailable.";
    state.handoffLogStale = previousEvents.length > 0;
  } finally {
    if (requestToken === state.handoffLogRequestToken) {
      state.handoffLogLoading = false;
      if (state.activeView === "agent-work") render();
    }
  }
}

async function loadCorrelatedHandoffTask(taskSlug) {
  let task = findTaskBySlug(taskSlug);
  if (task) return task;
  await (state.tasksLoadPromise || loadTasks({ reason: "manual" }));
  task = findTaskBySlug(taskSlug);
  if (task) return task;
  await loadAgentWork();
  task = findTaskBySlug(taskSlug);
  if (task) return task;
  try {
    const response = await fetch(`/api/tasks/${encodeURIComponent(taskSlug)}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const payload = await response.json();
    if (!response.ok || payload?.task?.slug !== taskSlug) return null;
    return payload.task;
  } catch (_error) {
    return null;
  }
}

async function loadCorrelatedSystemTicket(ticketSlug) {
  let ticket = findSystemTicket(ticketSlug);
  if (ticket) return ticket;
  await loadSystemTickets({ force: false });
  ticket = findSystemTicket(ticketSlug);
  if (ticket) return ticket;
  try {
    const response = await fetch(`/api/system-tickets/${encodeURIComponent(ticketSlug)}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const payload = await response.json();
    if (!response.ok || payload?.ticket?.slug !== ticketSlug) return null;
    ticket = payload.ticket;
    const destination = ticket.status === "completed" ? "completedSystemTickets" : "systemTickets";
    state[destination] = [
      ...state[destination].filter((item) => item.slug !== ticketSlug),
      ticket,
    ];
    return ticket;
  } catch (_error) {
    return null;
  }
}

async function openHandoffTaskReference(event, originControl = null) {
  const ref = event?.task_ref;
  if (!ref?.available || !ref.slug) return;
  state.handoffLogFilters = {
    ...state.handoffLogFilters,
    correlation_id: event.correlation_id || "",
  };
  state.activeView = "agent-work";
  state.agentHandoffHistoryOpen = true;
  render();
  state.handoffLogFocusKey = null;
  const logRead = loadHandoffLog({ reset: true });
  if (ref.surface === "system_ticket") {
    const ticket = await loadCorrelatedSystemTicket(ref.slug);
    if (ticket) {
      selectSystemTicket(ref.slug, originControl);
      await logRead;
      return;
    }
  } else {
    const task = await loadCorrelatedHandoffTask(ref.slug);
    if (task) {
      selectTask(ref.slug, task, originControl);
      await logRead;
      return;
    }
  }
  await logRead;
  state.handoffLogError = MISSING_LINKED_TASK_ERROR;
  state.handoffLogStale = false;
  state.handoffLogFocusKey = "load-status";
  if (state.activeView === "agent-work") render();
}

async function openHandoffCorrelation(correlationId, taskSlug, originControl = null) {
  state.handoffLogFilters = {
    ...state.handoffLogFilters,
    correlation_id: correlationId || "",
  };
  state.activeView = "agent-work";
  state.agentHandoffHistoryOpen = true;
  render();
  state.handoffLogFocusKey = null;
  const logRead = loadHandoffLog({ reset: true });
  const task = taskSlug ? await loadCorrelatedHandoffTask(taskSlug) : null;
  if (task) {
    selectTask(taskSlug, task, originControl);
    await logRead;
    return;
  }
  await logRead;
  state.handoffLogError = MISSING_LINKED_TASK_ERROR;
  state.handoffLogStale = false;
  state.handoffLogFocusKey = "load-status";
  if (state.activeView === "agent-work") render();
}

function handoffFilterSelect(label, name, options, value) {
  const wrapper = node("label", "handoff-filter-control");
  wrapper.append(node("span", "", label));
  const select = document.createElement("select");
  select.name = name;
  markHandoffFocus(select, `filter:${name}`);
  select.setAttribute("aria-label", `${label} filter`);
  options.forEach(([optionValue, optionLabel]) => {
    const option = document.createElement("option");
    option.value = optionValue;
    option.textContent = optionLabel;
    option.selected = optionValue === value;
    select.append(option);
  });
  wrapper.append(select);
  return wrapper;
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

function openTaskDetailLoading(slug, returnFocus = null, fallback = null) {
  state.detailReturnFocus = returnFocus
    ? detailReturnFocusAnchor(returnFocus, slug)
    : null;
  state.selectedSlug = slug;
  state.selectedKind = "task";
  prepareDetailPanelWidth("task");
  state.showCompletedTodos = false;
  setTodoAddOpen(false, { focus: false });
  elements.detailPanel.setAttribute("aria-hidden", "false");
  elements.detailPanel.setAttribute("aria-busy", "true");
  elements.detailPanel.setAttribute("aria-label", "Task details");
  elements.detailEmpty.classList.add("is-hidden");
  elements.detailContent.classList.remove("is-hidden");
  elements.artifactDetailContent.classList.add("is-hidden");
  elements.goalDetailContent.classList.add("is-hidden");
  elements.projectDetailContent.classList.add("is-hidden");
  elements.systemTicketDetailContent.classList.add("is-hidden");
  elements.calendarEventDetail.classList.add("is-hidden");
  elements.taskDetailStatus.textContent = "Reading";
  elements.taskApproveButton.classList.add("is-hidden");
  elements.taskRejectButton.classList.add("is-hidden");
  elements.taskDuplicateButton.classList.add("is-hidden");
  elements.taskOwner.classList.add("is-hidden");
  elements.detailTitle.textContent = fallback?.title || fallback?.summary || "Reading canonical Task…";
  elements.taskDetailRetry.classList.add("is-hidden");
  elements.taskDetailRetry.setAttribute("aria-hidden", "true");
  renderSafeMarkdown(
    elements.detailCopy,
    fallback?.detail || "Mission Control accepted the selection and is reading the exact canonical Task from GBrain.",
  );
  elements.proposalDetailMeta.classList.add("is-hidden");
  elements.proposalDecisionHistory.classList.add("is-hidden");
  elements.taskProgressDetail.classList.add("is-hidden");
  elements.detailPriority.textContent = "Loading";
  elements.detailDue.textContent = "Loading";
  elements.taskProjectValue.textContent = "Loading";
  elements.taskGoalNav.classList.add("is-hidden");
  elements.taskGoalNav.onclick = null;
  elements.taskGoalValue.textContent = "Loading";
  elements.detailGbrainLink.href = `http://127.0.0.1:8788/?slug=${encodeURIComponent(slug)}`;
  elements.detailSlug.textContent = slug;
  renderTaskTodos({ slug, todos: [], open_todos: [] });
  renderTaskArtifacts(slug);
  renderTaskHandoffTimeline(slug);
  render();
  window.requestAnimationFrame(() => {
    if (window.matchMedia("(max-width: 760px)").matches) {
      elements.detailPanel.scrollIntoView({ block: "start", behavior: "auto" });
    }
    elements.detailTitle.focus({ preventScroll: true });
  });
}

async function readExactTaskForDetail(slug, signal) {
  const response = await fetch(`/api/tasks/${encodeURIComponent(slug)}`, {
    headers: { Accept: "application/json" },
    cache: "no-store",
    signal,
  });
  const payload = await response.json();
  if (!response.ok || payload?.task?.slug !== slug) {
    throw new Error(payload?.error || "Canonical Task could not be read.");
  }
  return payload.task;
}

function cancelTaskDetailRead({ invalidate = false } = {}) {
  if (state.taskDetailReadWatchdogTimer !== null) {
    window.clearTimeout(state.taskDetailReadWatchdogTimer);
    state.taskDetailReadWatchdogTimer = null;
  }
  if (state.taskDetailReadController) {
    state.taskDetailReadController.abort();
    state.taskDetailReadController = null;
  }
  if (invalidate) {
    state.taskDetailReadToken += 1;
    state.taskDetailReadSlug = null;
    state.taskDetailReadPromise = null;
  }
}

function showTaskDetailReadFailure(error, { timedOut = false, fallback = null } = {}) {
  const heading = timedOut ? "Read timed out" : "Read failed";
  const recovery = timedOut
    ? "Canonical Task read timed out. Try again without reloading Mission Control."
    : `${error?.message || "Canonical Task could not be read."} Try again without reloading Mission Control.`;
  if (fallback) {
    selectTask(fallback.slug, fallback, state.detailReturnFocus?.element || null, {
      exactHydrated: true,
    });
    const alert = node("p", "mission-status-hud form-error", recovery);
    alert.setAttribute("role", "alert");
    elements.detailCopy.append(alert);
  } else {
    elements.detailPanel.setAttribute("aria-busy", "false");
    elements.detailTitle.textContent = "Task detail unavailable";
    renderSafeMarkdown(elements.detailCopy, recovery);
  }
  elements.taskDetailStatus.textContent = heading;
  elements.taskDetailRetry.classList.remove("is-hidden");
  elements.taskDetailRetry.setAttribute("aria-hidden", "false");
  render();
}

function retryTaskDetailRead() {
  if (state.selectedKind !== "task" || !state.selectedSlug) {
    return Promise.resolve(null);
  }
  const slug = state.selectedSlug;
  const fallback = findTaskBySlug(slug);
  return selectTaskWithCanonicalRead(
    slug,
    state.detailReturnFocus?.element || null,
    fallback,
  );
}

function focusTaskDetailTarget(task, { focusTarget = null, todoSlug = null } = {}) {
  if (
    focusTarget === "handoff-answer" &&
    isActiveHandoffQuestion(
      (Array.isArray(task?.todos) ? task.todos : []).find((todo) => todo.slug === todoSlug),
      task,
    ) &&
    !elements.taskHandoffAnswerForm.classList.contains("is-hidden")
  ) {
    elements.taskHandoffAnswer.focus({ preventScroll: true });
    return;
  }
  elements.detailTitle.focus({ preventScroll: true });
}

function selectTaskWithCanonicalRead(slug, returnFocus = null, fallback = null, options = {}) {
  if (
    state.taskDetailReadSlug === slug &&
    state.taskDetailReadPromise
  ) {
    const detailHidden = elements.detailPanel.getAttribute("aria-hidden") !== "false";
    const detailNotBusy = elements.detailPanel.getAttribute("aria-busy") !== "true";
    if (
      detailHidden ||
      detailNotBusy ||
      state.selectedSlug !== slug ||
      state.selectedKind !== "task"
    ) {
      openTaskDetailLoading(slug, returnFocus, fallback);
    }
    return state.taskDetailReadPromise.then((task) => {
      if (task && state.selectedSlug === slug && state.selectedKind === "task") {
        window.requestAnimationFrame(() => focusTaskDetailTarget(task, options));
      }
      return task;
    });
  }
  if (state.taskDetailReadPromise) {
    cancelTaskDetailRead({ invalidate: true });
  }
  const token = state.taskDetailReadToken + 1;
  state.taskDetailReadToken = token;
  state.taskDetailReadSlug = slug;
  openTaskDetailLoading(slug, returnFocus, fallback);
  const controller = new AbortController();
  let timedOut = false;
  state.taskDetailReadController = controller;
  state.taskDetailReadWatchdogTimer = window.setTimeout(() => {
    if (
      state.taskDetailReadToken === token &&
      state.taskDetailReadController === controller
    ) {
      timedOut = true;
      controller.abort();
    }
  }, TASK_DETAIL_READ_TIMEOUT_MS);
  state.taskDetailReadPromise = (async () => {
    try {
      const task = await readExactTaskForDetail(slug, controller.signal);
      if (state.taskDetailReadToken !== token || state.selectedSlug !== slug) {
        return null;
      }
      selectTask(slug, task, returnFocus, { exactHydrated: true, ...options });
      return task;
    } catch (error) {
      if (state.taskDetailReadToken !== token || state.selectedSlug !== slug) {
        return null;
      }
      showTaskDetailReadFailure(error, { timedOut, fallback });
      return null;
    } finally {
      if (state.taskDetailReadToken === token) {
        if (state.taskDetailReadWatchdogTimer !== null) {
          window.clearTimeout(state.taskDetailReadWatchdogTimer);
          state.taskDetailReadWatchdogTimer = null;
        }
        if (state.taskDetailReadController === controller) {
          state.taskDetailReadController = null;
        }
        state.taskDetailReadSlug = null;
        state.taskDetailReadPromise = null;
      }
    }
  })();
  return state.taskDetailReadPromise;
}

function selectTask(
  slug,
  taskFallback = null,
  returnFocus = null,
  { exactHydrated = false, focusTarget = null, todoSlug = null } = {},
) {
  const knownTask = findTaskBySlug(slug);
  if (knownTask && taskFallback?.slug === knownTask.slug) {
    Object.assign(knownTask, taskFallback);
  }
  const task = knownTask || taskFallback;
  if (
    !exactHydrated &&
    (!task || !Object.prototype.hasOwnProperty.call(task, "display_markdown"))
  ) return selectTaskWithCanonicalRead(slug, returnFocus, task, { focusTarget, todoSlug });
  if (!task) return selectTaskWithCanonicalRead(slug, returnFocus, null, { focusTarget, todoSlug });
  state.detailReturnFocus = returnFocus
    ? detailReturnFocusAnchor(returnFocus, slug)
    : null;
  state.selectedSlug = slug;
  state.selectedKind = "task";
  prepareDetailPanelWidth("task");
  elements.detailPanel.setAttribute("aria-busy", "false");
  elements.taskDetailRetry.classList.add("is-hidden");
  elements.taskDetailRetry.setAttribute("aria-hidden", "true");
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
  renderSafeMarkdown(
    elements.detailCopy,
    task.display_markdown || task.detail || "",
    { sourceKind: "task", sourceSlug: task.slug, referenceScope: "body" },
  );
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
  void ensureTaskTemporaryExecutorProjection(task);
  elements.taskHandoffTimeline.open = false;
  syncTaskHandoffTimelineDisclosure();
  elements.taskTodoError.classList.add("is-hidden");
  renderTaskTodos(task);
  renderTaskArtifacts(task.slug);
  renderTaskHandoffTimeline(task.slug);
  if (state.artifactTaskReturn?.taskSlug !== task.slug) {
    void loadTaskArtifacts(task.slug);
  }
  void loadTaskHandoffTimeline(task.slug);
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
        ? metric.auto_complete
          ? `Updated by distinct verified job-applied events. At ${metric.target} / ${metric.target}, Mission Control completes this task after canonical readback.`
          : "Updated by distinct verified job-applied events; task completion remains manual."
        : "Manual count metric. Reaching the target does not automatically change task status.";
  }
  elements.detailGbrainLink.href = `http://127.0.0.1:8788/?slug=${encodeURIComponent(task.slug)}`;
  elements.detailSlug.textContent = task.slug;
  elements.taskProjectValue.textContent = taskProjectLabel(task);
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
    focusTaskDetailTarget(task, { focusTarget, todoSlug });
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
    button.dataset.slug = task.slug;
    button.append(
      node("span", "", task.title || task.summary),
      node("span", "", relativeDue(task).label),
    );
    button.addEventListener("click", () => {
      state.goalTaskReturn = state.selectedKind === "goal"
        ? {
          goalSlug: state.selectedSlug,
          taskSlug: task.slug,
          element: button,
          detailReturnFocus: state.detailReturnFocus,
        }
        : null;
      selectTask(task.slug, null, button);
    });
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
    restorePendingGoalTaskFocus({ clear: true });
  } catch (error) {
    if (state.selectedKind !== "goal" || state.selectedSlug !== goal.slug) return;
    elements.goalRelationshipNotice.textContent =
      `Could not verify reciprocal goal links. ${error.message}`;
    elements.goalRelationshipNotice.classList.remove("is-hidden");
    restorePendingGoalTaskFocus({ clear: true });
  }
}

function restorePendingGoalTaskFocus({ clear = false } = {}) {
  const taskSlug = state.goalTaskFocusSlug;
  if (!taskSlug) return;
  window.requestAnimationFrame(() => {
    const target = Array.from(document.querySelectorAll(".goal-task-link"))
      .find((candidate) => candidate.dataset.slug === taskSlug);
    (target || elements.goalDetailTitle).focus({ preventScroll: true });
    if (clear && state.goalTaskFocusSlug === taskSlug) {
      state.goalTaskFocusSlug = null;
    }
  });
}

function selectGoal(slug, returnFocus = undefined) {
  const goal = state.snapshot?.goals.find((item) => item.slug === slug);
  if (!goal) return;
  if (returnFocus !== undefined) {
    state.detailReturnFocus = returnFocus
      ? detailReturnFocusAnchor(returnFocus, slug)
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
  renderGoalExecutionDetail(elements.goalDetailExecution, { goalSlug: goal.slug });
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
  if (state.taskDetailReadPromise) {
    cancelTaskDetailRead({ invalidate: true });
  }
  const systemTicketMarkdownReturn = state.selectedKind === "system-ticket"
    ? state.systemTicketMarkdownReturn
    : null;
  if (systemTicketMarkdownReturn?.destinationTicketSlug === state.selectedSlug) {
    const source = systemTicketMarkdownReturn.sourceKind === "task"
      ? findTaskBySlug(systemTicketMarkdownReturn.sourceSlug)
      : findSystemTicket(systemTicketMarkdownReturn.sourceSlug);
    if (source) {
      if (systemTicketMarkdownReturn.sourceKind === "task") {
        selectTask(source.slug, source);
      } else {
        selectSystemTicket(source.slug);
      }
      state.detailReturnFocus = systemTicketMarkdownReturn.detailReturnFocus;
      state.systemTicketMarkdownReturn = systemTicketMarkdownReturn.parent || null;
      restoreMarkdownSystemTicketReferenceFocus(systemTicketMarkdownReturn);
      return;
    }
    state.systemTicketMarkdownReturn = null;
    state.detailReturnFocus = systemTicketMarkdownReturn.detailReturnFocus;
  }
  const goalTaskReturn = state.selectedKind === "task"
    ? state.goalTaskReturn
    : null;
  if (goalTaskReturn?.taskSlug === state.selectedSlug) {
    state.goalTaskReturn = null;
    state.goalTaskFocusSlug = goalTaskReturn.taskSlug;
    selectGoal(goalTaskReturn.goalSlug);
    state.detailReturnFocus = goalTaskReturn.detailReturnFocus;
    window.requestAnimationFrame(() => {
      const target = Array.from(document.querySelectorAll(".goal-task-link"))
        .find((candidate) => candidate.dataset.slug === goalTaskReturn.taskSlug);
      (target || elements.goalDetailTitle).focus({ preventScroll: true });
    });
    return;
  }
  const producingTaskReturn = state.selectedKind === "task"
    ? state.artifactProducingTaskReturn
    : null;
  if (producingTaskReturn?.taskSlug === state.selectedSlug) {
    state.artifactProducingTaskReturn = null;
    state.artifactExpanded = new Set(producingTaskReturn.expanded);
    state.selectedKind = null;
    state.selectedSlug = null;
    selectArtifact(producingTaskReturn.artifactSlug, null, { preserveReturnContext: true });
    state.artifactTaskReturn = producingTaskReturn.artifactTaskReturn;
    state.detailReturnFocus = producingTaskReturn.detailReturnFocus;
    window.requestAnimationFrame(() => {
      elements.artifactDetailTaskLink.focus({ preventScroll: true });
    });
    return;
  }
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
  state.artifactProducingTaskReturn = null;
  state.goalTaskReturn = null;
  state.goalTaskFocusSlug = null;
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
  state.detailFocusReturnAnchor = returnFocus;
  render();
  restorePendingDetailFocus({ force: true });
}

function restoreMarkdownSystemTicketReferenceFocus(returnContext) {
  window.requestAnimationFrame(() => {
    const selector = [
      `[data-system-ticket-reference-source-kind="${CSS.escape(returnContext.sourceKind)}"]`,
      `[data-system-ticket-reference-source-slug="${CSS.escape(returnContext.sourceSlug)}"]`,
      `[data-system-ticket-reference-key="${CSS.escape(returnContext.referenceKey)}"]`,
    ].join("");
    const target = document.querySelector(selector);
    const fallback = returnContext.sourceKind === "task"
      ? elements.detailTitle
      : elements.systemTicketDetailTitle;
    (target || fallback).focus({ preventScroll: true });
  });
}

function detailFocusReturnTarget(anchor) {
  if (!anchor) return null;
  if (anchor.element?.isConnected) return anchor.element;
  if (anchor.goalExecutionOrigin) {
    const exactGoalExecutionOrigin = document.querySelector(
      `[data-goal-execution-origin="${CSS.escape(anchor.goalExecutionOrigin)}"]`,
    );
    if (exactGoalExecutionOrigin) return exactGoalExecutionOrigin;
  }
  if (anchor.handoffTask) {
    const taskLinks = Array.from(document.querySelectorAll(".handoff-event-task"))
      .filter((candidate) => candidate.dataset.slug === anchor.slug);
    const exactOrigin = taskLinks.find((candidate) => (
      (!anchor.sequence || candidate.dataset.sequence === anchor.sequence) &&
      (!anchor.correlationId || candidate.dataset.correlationId === anchor.correlationId)
    ));
    if (exactOrigin) return exactOrigin;
    if (!anchor.sequence && !anchor.correlationId && taskLinks.length) return taskLinks[0];
  }
  const matchingOrigin = [
    ...document.querySelectorAll(".proposal-card"),
    ...document.querySelectorAll(".task-row-open"),
    ...document.querySelectorAll(".board-card-open"),
    ...document.querySelectorAll(".month-task"),
    ...document.querySelectorAll(".goal-task-link"),
    ...document.querySelectorAll(".inline-task-link"),
    ...document.querySelectorAll(".attention-task-open"),
    ...document.querySelectorAll(".project-card-open"),
    ...document.querySelectorAll(".goal-card"),
    ...document.querySelectorAll(".artifact-card"),
    ...document.querySelectorAll(".handoff-event-task"),
    ...document.querySelectorAll(".system-ticket-card"),
    ...document.querySelectorAll(".ical-event"),
  ].find((candidate) => candidate.dataset.slug === anchor.slug) || null;
  if (matchingOrigin) return matchingOrigin;
  if (state.activeView === "agent-work" && state.agentHandoffHistoryOpen) {
    return (
      document.querySelector('[data-handoff-focus="load-status"]') ||
      document.querySelector('[data-handoff-focus="filter:correlation_id"]')
    );
  }
  return null;
}

function restorePendingDetailFocus({ force = false } = {}) {
  const anchor = state.detailFocusReturnAnchor;
  if (!anchor || elements.detailPanel.getAttribute("aria-hidden") !== "true") return;
  window.requestAnimationFrame(() => {
    if (state.detailFocusReturnAnchor !== anchor) return;
    const active = document.activeElement;
    if (
      !force &&
      active !== document.body &&
      active?.isConnected &&
      !(anchor.handoffTask && isHandoffDetailFallbackFocus(active))
    ) return;
    detailFocusReturnTarget(anchor)?.focus({ preventScroll: true });
  });
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

async function requestTaskStatus(taskSlug, status, { completionPreviousTask = null } = {}) {
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
  maybeCelebrateVerifiedTaskCompletion(completionPreviousTask, result.receipt.task, {
    requestedStatus: status,
  });
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
  if (task.read_only) return;
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
    const previousTask = { ...task };
    const receipt = await requestTaskStatus(taskSlug, status);
    reconcileVerifiedTask(receipt.task);
    maybeCelebrateVerifiedTaskCompletion(previousTask, receipt.task, {
      requestedStatus: status,
    });
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
    maybeCelebrateVerifiedTaskCompletion(currentTask, receipt.task, {
      requestedStatus: status,
    });
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
  if (view === "handoff-log") {
    state.agentHandoffHistoryOpen = true;
    view = "agent-work";
  }
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
  if (view === "agent-work" && !state.projectsLoaded && !state.projectsLoading) {
    void loadProjects();
  }
  if (view === "agent-work" && !state.goalExecutionLoaded && !state.goalExecutionLoading) {
    void loadGoalExecution();
  }
  if (view === "week" && !state.icalConnectionLoaded && !state.icalConnectionLoading) {
    void loadCalendarConnectionState();
  }
  if (view === "agent-work" && !state.handoffLogEvents.length && !state.handoffLogLoading) {
    void loadHandoffLog({ reset: true });
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

function activeViewUsesTaskSnapshot() {
  return !["agent-work", "artifacts", "system-tickets"].includes(state.activeView);
}

function scheduleAutoRefresh({ reset = false } = {}) {
  clearAutoRefreshTimer();
  const now = Date.now();
  if (
    reset ||
    state.autoRefreshDueAt === null ||
    state.autoRefreshDueAt <= now
  ) {
    state.autoRefreshDueAt = now + AUTO_REFRESH_INTERVAL_MS;
  }
  if (document.hidden) {
    updateAutoRefreshLabel(
      `Auto-refresh every ${AUTO_REFRESH_MINUTES} minutes · paused while hidden`,
    );
    return;
  }
  updateAutoRefreshLabel();
  const delay = Math.max(0, state.autoRefreshDueAt - now);
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
    if (activeViewUsesTaskSnapshot()) render();
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
      if (
        snapshotHasProjectReferences(state.snapshot) &&
        !state.projectsLoaded &&
        !state.projectsLoading
      ) {
        void loadProjects();
      }
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
    if (activeViewUsesTaskSnapshot()) render();
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
  elements.taskMetricUseMinimum.classList.add("is-hidden");
  if (binding === "job_applied") {
    const context = state.taskEditorMetricContext || {};
    const verified = Number(context.verifiedCount || 0);
    const saved = Number(context.savedCurrent ?? current);
    const baseline = Number(context.baselineCount ?? Math.max(0, saved - verified));
    const taskDay = context.taskDay || elements.taskEditorDue.value || "the displayed task day";
    const timezone = context.timezone || "America/Los_Angeles";
    const bindingTask = state.snapshot?.event_bindings?.job_applied?.task_slug || "";
    const isConfigured = Boolean(bindingTask) && context.taskSlug === bindingTask;
    const bindingLabel = isConfigured
      ? `Bound task: ${context.taskTitle || bindingTask}.`
      : bindingTask
        ? `Automatic delivery is configured only for ${bindingTask}; this task is not the queue target.`
        : "Automatic delivery binding is unavailable; refresh canonical data before saving.";
    elements.taskMetricBindingCopy.textContent =
      `${bindingLabel} Scope: ${taskDay} (${timezone}). Saved Current ${saved} = ` +
      `${baseline} baseline/manual + ${verified} distinct verified event${verified === 1 ? "" : "s"}. ` +
      `Each distinct verified event increments progress by 1 after canonical readback. ` +
      `The minimum allowed Current is ${verified}; this count belongs only to this bound task and scope, not a global queue total.`;
    if (Number.isInteger(current) && current < verified) {
      elements.taskMetricUseMinimum.textContent = `Use minimum ${verified}`;
      elements.taskMetricUseMinimum.dataset.minimum = String(verified);
      elements.taskMetricUseMinimum.classList.remove("is-hidden");
    }
  } else {
    elements.taskMetricBindingCopy.textContent =
      "Manual metrics never change task status just because current equals target.";
  }
  elements.taskMetricPreview.textContent =
    label && Number.isInteger(target) && target > 0 && Number.isInteger(current)
      ? `${label}: ${current} / ${target}`
      : "Add a name and target to preview progress.";
}

function updateTaskMetricBindingAvailability() {
  const options = [...elements.taskMetricEventBinding.options];
  const automatic = options.find((option) => option.value === "job_applied");
  const manual = options.find((option) => option.value === "");
  const context = state.taskEditorMetricContext || {};
  const boundTask = state.snapshot?.event_bindings?.job_applied?.task_slug || "";
  const editingBoundTask =
    state.taskEditorMode === "edit" && context.taskSlug === boundTask;
  if (automatic) automatic.disabled = !editingBoundTask;
  if (manual) manual.disabled = Number(context.verifiedCount || 0) > 0;
}

function resetTaskEditorMetric(metric = null, task = null) {
  const verifiedCount = Array.isArray(task?.event_progress?.receipt_ids)
    ? task.event_progress.receipt_ids.length
    : 0;
  state.taskEditorMetricContext = task
    ? {
        taskSlug: task.slug,
        taskTitle: task.title || task.summary,
        taskDay: metric?.task_day || task.due_day,
        timezone: metric?.timezone || "America/Los_Angeles",
        savedCurrent: metric?.current || 0,
        verifiedCount,
        baselineCount: task.event_progress?.baseline_count ??
          Math.max(0, (metric?.current || 0) - verifiedCount),
        revision: task.progress_metric_revision || null,
      }
    : null;
  elements.taskTrackMetric.checked = Boolean(metric);
  elements.taskMetricLabel.value = metric?.label ||
    (metric?.unit === "job_application" ? "Job applications" : "");
  elements.taskMetricTarget.value = metric?.target || "";
  elements.taskMetricCurrent.value = metric ? String(metric.current) : "0";
  elements.taskMetricEventBinding.value = metric?.event_binding || "";
  updateTaskMetricBindingAvailability();
  updateTaskMetricPreview();
}

async function loadTaskEditorReferenceData() {
  const loads = [];
  if (!state.projectsLoaded) loads.push(loadProjects());
  if (!state.agentsLoaded) loads.push(loadAgents());
  await Promise.all(loads);
}

function showTaskEditorLoading(mode, heading) {
  state.taskEditorMode = mode;
  elements.taskEditorForm.reset();
  elements.taskEditorMode.textContent = "Reading canonical choices";
  elements.taskEditorHeading.textContent = heading;
  elements.taskEditorSafety.textContent =
    "Loading Projects and Agents before editing. No GBrain write has started.";
  elements.taskEditorSubmit.disabled = true;
  elements.taskEditorError.classList.add("is-hidden");
  if (!elements.taskEditorDialog.open) elements.taskEditorDialog.showModal();
  window.setTimeout(() => elements.taskEditorClose.focus(), 0);
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
  showTaskEditorLoading("duplicate", "Preparing Duplicate…");
  await loadTaskEditorReferenceData();
  if (!elements.taskEditorDialog.open) return;
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
  resetTaskEditorMetric(
    task.progress_metric?.event_binding === "job_applied"
      ? { ...task.progress_metric, event_binding: null, auto_complete: false }
      : task.progress_metric,
  );
  if (task.progress_metric) {
    elements.taskMetricCurrent.value = "0";
    updateTaskMetricPreview();
  }
  elements.taskEditorError.classList.add("is-hidden");
  elements.taskEditorSubmit.disabled = false;
  window.setTimeout(() => elements.taskEditorTitle.focus(), 0);
}

async function openEditTask() {
  if (state.selectedKind !== "task" || !state.selectedSlug) return;
  const task = findTaskBySlug(state.selectedSlug);
  if (!task) return;
  showTaskEditorLoading("edit", "Preparing Edit…");
  await loadTaskEditorReferenceData();
  if (!elements.taskEditorDialog.open) return;
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
  resetTaskEditorMetric(task.progress_metric, task);
  elements.taskEditorError.classList.add("is-hidden");
  elements.taskEditorSubmit.disabled = false;
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
    const previousTask = state.taskEditorMode === "edit"
      ? findTaskBySlug(state.taskEditorSourceSlug)
      : null;
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
        progress_metric_revision: state.taskEditorMetricContext?.revision || null,
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
    if (state.taskEditorMode === "edit") {
      maybeCelebrateVerifiedTaskCompletion(previousTask, savedTask, {
        requestedStatus: payload.status,
      });
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
    if (
      elements.taskMetricEventBinding.value === "job_applied" &&
      /verified job-application|explicitly bound|changed after Edit/i.test(elements.taskEditorError.textContent)
    ) {
      elements.taskMetricCurrent.focus();
    }
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
elements.taskHandoffLoadMore.addEventListener("click", () => {
  if (state.selectedKind !== "task" || !state.selectedSlug) return;
  void readTaskHandoffPage(state.selectedSlug, { reset: false });
});
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
elements.taskMetricUseMinimum.addEventListener("click", () => {
  const minimum = Number(elements.taskMetricUseMinimum.dataset.minimum);
  if (!Number.isInteger(minimum) || minimum < 0) return;
  elements.taskMetricCurrent.value = String(minimum);
  updateTaskMetricPreview();
  elements.taskMetricCurrent.focus();
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
  if (state.activeView === "agent-work" || state.handoffLogEvents.length) {
    void loadHandoffLog({ reset: true });
  }
  void loadGoalExecution({ force: true });
});
elements.showAgentTasks.addEventListener("change", () => {
  setAgentTasksVisible(elements.showAgentTasks.checked);
});
elements.detailClose.addEventListener("click", closeDetails);
elements.taskDetailRetry.addEventListener("click", retryTaskDetailRead);
elements.artifactDetailClose.addEventListener("click", closeDetails);
elements.goalDetailClose.addEventListener("click", closeDetails);
elements.projectDetailClose.addEventListener("click", closeDetails);
elements.systemTicketDetailClose.addEventListener("click", closeDetails);
elements.calendarEventDetailClose.addEventListener("click", closeDetails);
elements.taskHandoffTimeline.addEventListener("toggle", syncTaskHandoffTimelineDisclosure);
document.addEventListener("focusin", (event) => {
  const anchor = state.detailFocusReturnAnchor;
  if (!anchor || elements.detailPanel.getAttribute("aria-hidden") !== "true") return;
  const target = event.target instanceof HTMLElement ? event.target : null;
  if (target && target === detailFocusReturnTarget(anchor)) return;
  state.detailFocusReturnAnchor = null;
});
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
    state.calendarPreferencesNotice = "";
    elements.calendarPickerDialog.close();
    render();
    showToast("Calendar selection saved and verified.");
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
elements.settingsButton.addEventListener("click", () => setView("settings"));
elements.aboutClose.addEventListener("click", closeAboutDialog);
elements.aboutDialog.addEventListener("close", () => {
  if (!elements.logsDialog.open) setAppShellModalIsolation(false);
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
  if (!elements.aboutDialog.open) setAppShellModalIsolation(false);
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
loadGoalExecution();
loadAgentWork();
loadCalendarConnectionState();
loadTasks({ reason: "initial" });
