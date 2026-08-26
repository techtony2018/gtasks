import unittest
from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_app_runtime_probe(probe: str) -> subprocess.CompletedProcess[str]:
    javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    source = javascript.rsplit(
        '\ndocument.querySelectorAll(".nav-item").forEach',
        maxsplit=1,
    )[0]
    harness = r"""
class FakeClassList {
  add() {}
  remove() {}
  toggle() {}
  contains() { return false; }
}
class FakeElement {
  constructor(tagName = "div") {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.classList = new FakeClassList();
    this.attributes = {};
    this.value = "";
    this.name = "";
    this.focused = false;
    this.listeners = {};
    this.style = { setProperty(name, value) { this[name] = value; } };
  }
  get childNodes() { return this.children; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, callback) {
    this.listeners[name] = this.listeners[name] || [];
    this.listeners[name].push(callback);
  }
  click() {
    for (const callback of this.listeners.click || []) {
      callback({ preventDefault() {} });
    }
  }
  querySelectorAll() { return []; }
  querySelector() { return null; }
  closest() { return this.closestResult || null; }
  focus() { this.focused = true; document.activeElement = this; }
  reset() {}
}
const document = {
  activeElement: null,
  body: new FakeElement("body"),
  documentElement: new FakeElement("html"),
  cookie: "",
  querySelector: () => new FakeElement(),
  querySelectorAll: () => [],
  createElement: (tagName) => new FakeElement(tagName),
  createTextNode: (text) => ({ textContent: String(text) }),
};
document.activeElement = document.body;
const window = {
  innerWidth: 1440,
  localStorage: { getItem: () => null, setItem() {} },
  matchMedia: () => ({ matches: false }),
  requestAnimationFrame: (callback) => callback(),
  setTimeout: (callback, delay = 0) => Number(delay) >= 10000 ? 1 : callback(),
  clearTimeout() {},
};
const CSS = { escape: (value) => String(value).replace(/[^A-Za-z0-9_-]/g, "_") };
const HTMLElement = FakeElement;
const HTMLInputElement = FakeElement;
const HTMLTextAreaElement = FakeElement;
const HTMLSelectElement = FakeElement;
"""
    script = (
        harness
        + "\n"
        + source
        + "\n(async () => {\n"
        + probe
        + "\n})().catch((error) => { console.error(error.stack || error); process.exitCode = 1; });\n"
    )
    return subprocess.run(
        ["node", "-"],
        input=script,
        text=True,
        capture_output=True,
        cwd=PROJECT_ROOT,
        check=False,
    )


class FrontendContractTests(unittest.TestCase):
    def test_goal_execution_stays_inside_agents_and_goal_project_details(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        stylesheet = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="goal-detail-execution"', html)
        self.assertIn('id="project-detail-execution"', html)
        self.assertNotIn('data-view="goal-execution"', html)
        self.assertIn("function renderGoalExecutionSurface", javascript)
        self.assertIn("function renderAgentGoalExecution", javascript)
        self.assertIn('agent.runtime === "openclaw" ? [] : [renderAgentGoalExecution(agent)]', javascript)
        self.assertIn('section.id = "agent-goal-execution"', javascript)
        self.assertIn('heading.id = "agent-goal-execution-heading"', javascript)
        self.assertIn('status.id = "agent-goal-execution-state"', javascript)
        self.assertIn('status.setAttribute("role", "status")', javascript)
        self.assertIn('status.setAttribute("aria-live", "polite")', javascript)
        self.assertIn('list.id = "agent-goal-execution-list"', javascript)
        for state_label in (
            "Ready",
            "Delivering",
            "Executing",
            "Blocked",
            "Needs attention",
        ):
            self.assertIn(state_label, javascript)
        self.assertIn('fetch("/api/goal-execution"', javascript)
        self.assertIn("state.goalExecution", javascript)
        self.assertIn("state.goalExecutionError", javascript)
        self.assertIn("state.goalExecutionLoading", javascript)
        self.assertIn("renderGoalExecutionDetail", javascript)
        self.assertIn("goalExecutionOrigin", javascript)
        self.assertIn("data-goal-execution-origin", javascript)
        self.assertIn(
            'function goalExecutionButton(label, className, slug, activate, originKey = "")',
            javascript,
        )
        self.assertIn("button.dataset.goalExecutionOrigin = originKey", javascript)
        self.assertGreaterEqual(
            javascript.count("detailReturnFocusAnchor(returnFocus, slug)"),
            4,
        )
        self.assertIn("selectGoal", javascript)
        self.assertIn("selectProject", javascript)
        self.assertIn("selectTask", javascript)
        self.assertNotIn("fixed-thread", javascript[javascript.index("function renderGoalExecutionSurface") : javascript.index("async function loadGoalExecution")])
        self.assertIn(".goal-execution-surface", stylesheet)
        self.assertIn("overflow-wrap: anywhere", stylesheet[stylesheet.index(".goal-execution-surface") :])

    def test_goal_execution_fixture_is_get_only_and_source_blind(self) -> None:
        fixture = (PROJECT_ROOT / "tests" / "project_browser_fixture.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("class GoalExecutionFixtureScheduler", fixture)
        self.assertIn('"mode": "shadow"', fixture)
        self.assertIn('"public_reason": "activated" if active else "shadow"', fixture)
        self.assertIn("goal_execution_scheduler=goal_execution_scheduler", fixture)
        self.assertIn('"external_mutation_count": adapter.external_mutation_count', fixture)

    def test_goal_execution_runtime_maps_public_states_and_keeps_last_valid_on_error(self) -> None:
        probe = r'''
function assert(condition, message) { if (!condition) throw new Error(message); }
const goalSlug = "goals/11111111-1111-4111-8111-111111111111";
const taskSlug = "tasks/22222222-2222-4222-8222-222222222222";
state.snapshot = {
  as_of: "2026-08-23",
  goals: [
    { slug: goalSlug, title: "Civic: Verified Goal" },
    { slug: "goals/d837ac94-36f5-4735-93bb-d84c69b45435", title: "Entrepreneurship" },
  ],
  tasks: [{ slug: taskSlug, title: "Review Civic progress", status: "active", owner_agent: "agents/timmy", project: null }],
  today: { in_progress: [], todays_actions: [], waiting_and_blocked: [], overdue: [] },
  views: { today: [], inbox: [], active: [], blocked: [], completed: [], all: [], in_progress: [] },
};
state.agents = [
  { slug: "agents/timmy", name: "Timmy", runtime: "codex", default_goal_slugs: [goalSlug] },
  { slug: "agents/tammy", name: "Tammy", runtime: "codex", default_goal_slugs: [] },
  { slug: "agents/tammy-oc", name: "Tammy-OC", runtime: "openclaw", default_goal_slugs: [] },
];
state.projects = [];
state.goalExecution = {
  mode: "canary",
  last_error: null,
  summary: {
    total_goals: 7,
    needs_attention: 2,
    waiting_for_tony: 1,
    owner_missing: 1,
    in_flight: 1,
    recently_completed: 3,
    blocking_questions: [{
      goal_slug: goalSlug,
      task_slug: taskSlug,
      todo_slug: "todos/question",
      agent_slug: "agents/timmy",
      question: "Which family-care scope should Toddy use next?",
      detail: "Choose the scope and first bounded action.",
    }],
    missing_owners: [{
      goal_slug: "goals/d837ac94-36f5-4735-93bb-d84c69b45435",
      goal_title: "Entrepreneurship",
      required_relationship: "default_agent_for",
      message: "Assign exactly one Codex Agent with a verified default_agent_for link before Mission Control can derive work from this Goal.",
    }],
    action_queue: [
      { owner: "tony", kind: "answer_question", label: "Answer Agent question", goal_slug: goalSlug, task_slug: taskSlug, todo_slug: "todos/question", todo_updated_at: "todo-v1", agent_slug: "agents/timmy", summary: "Which family-care scope should Toddy use next?", detail: "Choose the scope and first bounded action.", answer_template: "Scope categories: accepted\nDesired outcomes: accepted\nConstraints: accepted\nFirst action: approved\nNotes: Keep the work bounded to the stated scope, outcomes, constraints, and first action." },
      { owner: "tony", kind: "answer_question", label: "Answer Agent question", goal_slug: "goals/private-token", task_slug: "tasks/private-token", todo_slug: "todos/private-token", todo_updated_at: "todo-private-v1", agent_slug: "agents/tammy", summary: "Please provide the Tammy artifact publisher token for this fixed Codex worker.", detail: "This requires Tony's private credential input.", private_input_required: true },
      { owner: "tony", kind: "assign_goal_owner", label: "Assign Goal owner", goal_slug: "goals/d837ac94-36f5-4735-93bb-d84c69b45435", agent_slug: null, summary: "Entrepreneurship — add default_agent_for", candidate_owners: [
        { agent_slug: "agents/tammy", agent_name: "Tammy", default_goal_count: 0, recommended: true, recommendation: "recommended: lowest verified Codex Goal load" },
        { agent_slug: "agents/timmy", agent_name: "Timmy", default_goal_count: 1, recommended: false, recommendation: "1 verified default Goal" },
      ] },
      { owner: "agent", kind: "monitor_active_handoff", label: "Agent is executing", goal_slug: goalSlug, task_slug: taskSlug, agent_slug: "agents/timmy", summary: "Review Civic progress" },
    ],
    next_action: "Answer the Timmy question for Which family-care scope should Toddy use next? and assign Entrepreneurship to Tammy (recommended: lowest verified Codex Goal load); executing or delivered Agent work can continue.",
  },
  last_run: {
    ran_at: "2026-08-23T12:00:00Z",
    public_reason: "activated",
    task: { slug: taskSlug, status: "active", agent_slug: "agents/timmy" },
    handoff: { status: "queued" },
    decisions: [{ goal_slug: goalSlug, reason: "auto_eligible", task_slug: taskSlug }],
  },
};
state.goalExecution.last_run.summary = state.goalExecution.summary;
const goalExecutionSurface = renderGoalExecutionSurface();
const goalExecutionSurfaceText = flattenText(goalExecutionSurface);
function walkElements(root, predicate, matches = []) {
  if (!root) return matches;
  if (predicate(root)) matches.push(root);
  (root.children || []).forEach((child) => walkElements(child, predicate, matches));
  return matches;
}
assert(goalExecutionSurfaceText.includes("Next action: Answer the Timmy question for Which family-care scope should Toddy use next? and assign Entrepreneurship to Tammy"), "Goal execution surface did not expose the exact summary next action");
assert(goalExecutionSurfaceText.includes("7 total goals"), "Goal execution surface did not expose total Goal count");
assert(goalExecutionSurfaceText.includes("2 need attention"), "Goal execution surface did not expose attention count");
assert(goalExecutionSurfaceText.includes("1 waiting for Tony"), "Goal execution surface did not expose waiting count");
assert(goalExecutionSurfaceText.includes("1 in flight"), "Goal execution surface did not expose in-flight count");
assert(goalExecutionSurfaceText.includes("Question:"), "Goal execution surface did not expose the blocking question label");
assert(goalExecutionSurfaceText.includes("Which family-care scope should Toddy use next?"), "Goal execution surface did not expose the exact blocking question");
assert(goalExecutionSurfaceText.includes("Missing owner:"), "Goal execution surface did not expose the missing owner label");
assert(goalExecutionSurfaceText.includes("Entrepreneurship"), "Goal execution surface did not expose the exact missing owner title");
assert(goalExecutionSurfaceText.includes("default_agent_for"), "Goal execution surface did not expose the missing owner repair relationship");
assert(goalExecutionSurfaceText.includes("Action queue:"), "Goal execution surface did not expose the action queue");
assert(goalExecutionSurfaceText.includes("Tony action required"), "Goal execution surface did not label Tony-owned actions");
assert(goalExecutionSurfaceText.includes("Agent active"), "Goal execution surface did not distinguish active Agent work");
assert(goalExecutionSurfaceText.includes("Answer Agent question"), "Goal execution surface did not include question action");
assert(goalExecutionSurfaceText.includes("Private input required"), "Goal execution surface did not label private credential questions");
assert(goalExecutionSurfaceText.includes("Assign Goal owner"), "Goal execution surface did not include owner assignment action");
assert(goalExecutionSurfaceText.includes("Choose the scope and first bounded action."), "Goal execution action queue did not expose the question detail near the answer form");
const answerActions = walkElements(goalExecutionSurface, (element) =>
  String(element.className || "").includes("goal-execution-answer-action") &&
  element.dataset?.taskSlug === taskSlug &&
  element.dataset?.todoSlug === "todos/question");
assert(answerActions.length === 1, "action queue did not expose one direct Answer Agent question action");
const inlineAnswerForms = walkElements(goalExecutionSurface, (element) =>
  String(element.className || "").includes("goal-execution-answer-form"));
assert(inlineAnswerForms.length === 1, "action queue did not expose one inline answer form");
const inlineAnswerInputs = walkElements(inlineAnswerForms[0], (element) =>
  String(element.className || "").includes("goal-execution-answer-input"));
const inlineAnswerSubmits = walkElements(inlineAnswerForms[0], (element) =>
  String(element.className || "").includes("goal-execution-answer-submit"));
const inlineAnswerTemplateButtons = walkElements(inlineAnswerForms[0], (element) =>
  String(element.className || "").includes("goal-execution-answer-template"));
assert(inlineAnswerInputs.length === 1, "inline answer form did not expose one textarea");
assert(inlineAnswerSubmits.length === 1, "inline answer form did not expose one submit button");
assert(inlineAnswerTemplateButtons.length === 1, "inline answer form did not expose one answer-template button");
assert(!goalExecutionSurfaceText.includes("todo-private-v1"), "private credential action exposed raw TODO version text");
assert(flattenText(inlineAnswerTemplateButtons[0]).includes("Insert answer template"), "inline answer template button had unclear copy");
inlineAnswerTemplateButtons[0].click();
assert(inlineAnswerInputs[0].value.includes("Scope categories: accepted"), "answer template button did not fill a concrete scope decision");
assert(inlineAnswerInputs[0].value.includes("First action: approved"), "answer template omitted the concrete first-action decision line");
assert(!inlineAnswerInputs[0].value.includes("[accepted/revised]"), "answer template still exposed placeholder decision copy");
assert(
  answerActions[0].dataset?.goalExecutionOrigin === "summary:action:answer_question:tasks_22222222-2222-4222-8222-222222222222:todos_question",
  "direct question action lacks immutable Goal execution origin identity",
);
const questionLinks = walkElements(goalExecutionSurface, (element) =>
  String(element.className || "").includes("goal-execution-blocking-question") &&
  walkElements(element, (child) =>
    String(child.className || "").includes("inline-task-link") &&
    child.dataset?.slug === taskSlug).length === 1);
assert(questionLinks.length === 1, "summary blocking question did not expose one exact Task link");
const missingOwnerLinks = walkElements(goalExecutionSurface, (element) =>
  String(element.className || "").includes("goal-execution-missing-owner") &&
  walkElements(element, (child) =>
    String(child.className || "").includes("goal-execution-link") &&
    String(child.className || "").includes("goal") &&
    child.dataset?.slug === "goals/d837ac94-36f5-4735-93bb-d84c69b45435" &&
    child.dataset?.goalExecutionOrigin === "summary:missing-owner:goals/d837ac94-36f5-4735-93bb-d84c69b45435").length === 1);
assert(missingOwnerLinks.length === 1, "summary missing owner did not expose one exact Goal link");
const ownerAssignButtons = walkElements(goalExecutionSurface, (element) =>
  String(element.className || "").includes("goal-execution-owner-assign"));
assert(ownerAssignButtons.length === 2, "summary missing owner did not expose Codex-only assignment buttons");
assert(ownerAssignButtons.some((button) => button.dataset?.agentSlug === "agents/tammy"), "summary missing owner omitted Tammy assignment");
assert(!ownerAssignButtons.some((button) => button.dataset?.agentSlug === "agents/tammy-oc"), "summary missing owner exposed OpenClaw assignment");
const actionQueueOwnerAssignButtons = walkElements(goalExecutionSurface, (element) =>
  String(element.className || "").includes("goal-execution-action-owner-assign"));
assert(actionQueueOwnerAssignButtons.length === 2, "action queue owner assignment did not expose Codex-only assignment buttons");
assert(actionQueueOwnerAssignButtons.every((button) =>
  button.dataset?.slug === "goals/d837ac94-36f5-4735-93bb-d84c69b45435"),
  "action queue owner assignment buttons did not preserve the exact Goal slug");
assert(actionQueueOwnerAssignButtons.some((button) => button.dataset?.agentSlug === "agents/tammy"), "action queue owner assignment omitted Tammy assignment");
assert(!actionQueueOwnerAssignButtons.some((button) => button.dataset?.agentSlug === "agents/tammy-oc"), "action queue owner assignment exposed OpenClaw assignment");
assert(
  actionQueueOwnerAssignButtons.some((button) => flattenText(button).includes("recommended") && button.dataset?.agentSlug === "agents/tammy"),
  "action queue owner assignment did not label the recommended Codex Agent",
);
const inboxGoalActions = renderGoalExecutionInboxActions();
const inboxGoalActionsText = flattenText(inboxGoalActions);
assert(inboxGoalActionsText.includes("Goal execution actions"), "Inbox did not expose Goal execution actions");
assert(inboxGoalActionsText.includes("Answer Agent question"), "Inbox Goal execution actions omitted the answer action");
assert(inboxGoalActionsText.includes("Insert answer template"), "Inbox Goal execution actions omitted the answer template control");
assert(inboxGoalActionsText.includes("Assign Goal owner"), "Inbox Goal execution actions omitted the owner action");
const inboxTemplateButtons = walkElements(inboxGoalActions, (element) =>
  String(element.className || "").includes("goal-execution-answer-template"));
const inboxAnswerInputs = walkElements(inboxGoalActions, (element) =>
  String(element.className || "").includes("goal-execution-answer-input"));
assert(inboxTemplateButtons.length === 1, "Inbox Goal execution action did not expose one answer-template button");
assert(inboxAnswerInputs.length === 1, "Inbox Goal execution action did not expose one answer textarea");
inboxTemplateButtons[0].click();
assert(inboxAnswerInputs[0].value.includes("Scope categories: accepted"), "Inbox answer template did not fill the concrete draft");
assert(!inboxAnswerInputs[0].value.includes("[accepted/revised]"), "Inbox answer template exposed placeholder copy");
const inboxUnblockButtons = walkElements(inboxGoalActions, (element) =>
  String(element.className || "").includes("goal-execution-unblock-plan"));
assert(inboxUnblockButtons.length === 1, "Inbox Goal execution actions did not expose one recommended unblock plan button");
assert(flattenText(inboxUnblockButtons[0]).includes("Run recommended unblock plan"), "Inbox recommended unblock plan button had unclear copy");
const originalFetch = globalThis.fetch;
const originalLoadAgents = loadAgents;
const originalLoadGoalExecution = loadGoalExecution;
const originalLoadAgentWork = loadAgentWork;
const originalRender = render;
const originalShowToast = showToast;
let planRequests = [];
globalThis.fetch = async (url, options = {}) => {
  planRequests.push({ url: String(url), method: options.method, body: JSON.parse(options.body || "{}") });
  if (String(url).includes("/answer")) {
    return { ok: true, json: async () => ({ verified: true, next_owner: "agents/timmy", todo: {
      slug: "todos/question",
      parent_task: taskSlug,
      status: "done",
      kind: "question",
      text: "Which family-care scope should Toddy use next?",
      updated_at: "todo-v2",
      comments: [{ body: "Scope categories: accepted", author: "people/tony-guan" }],
      events: [],
    }, task: {
      slug: taskSlug,
      title: "Review Civic progress",
      summary: "Review Civic progress",
      status: "blocked",
      priority: "normal",
      due_day: "2026-08-25",
      display_markdown: "Question task",
      owner_agent: "agents/timmy",
      project: null,
      goal: goalSlug,
      todos: [],
      handoff: { state: "ready_for_agent", resume_owner: "agents/timmy", resume_action: "Continue after Tony answers." },
    } }) };
  }
  return { ok: true, json: async () => ({ verified: true, agent: { slug: "agents/tammy" } }) };
};
loadAgents = async () => {};
loadGoalExecution = async () => {};
loadAgentWork = async () => {};
render = () => {};
showToast = () => {};
await applyGoalExecutionRecommendedActions(state.goalExecution.summary, new FakeElement("button"), new FakeElement("p"));
globalThis.fetch = originalFetch;
loadAgents = originalLoadAgents;
loadGoalExecution = originalLoadGoalExecution;
loadAgentWork = originalLoadAgentWork;
render = originalRender;
showToast = originalShowToast;
assert(planRequests.length === 2, "recommended unblock plan did not perform exactly two explicit writes");
assert(planRequests[0].url === "/api/todos/todos%2Fquestion/answer", "recommended unblock plan did not answer first");
assert(planRequests[0].body.answer.includes("Scope categories: accepted"), "recommended unblock plan did not use the concrete answer draft");
assert(planRequests[1].url === "/api/agents/agents%2Ftammy/default-goals", "recommended unblock plan did not assign the recommended Codex owner second");
assert(planRequests[1].body.goal_slug === "goals/d837ac94-36f5-4735-93bb-d84c69b45435", "recommended unblock plan assigned the wrong Goal");
let assignmentRequest = null;
globalThis.fetch = async (url, options = {}) => {
  assignmentRequest = { url: String(url), method: options.method, body: JSON.parse(options.body || "{}") };
  return { ok: true, json: async () => ({ verified: true, agent: { slug: "agents/tammy" } }) };
};
loadAgents = async () => {};
loadGoalExecution = async () => {};
render = () => {};
showToast = () => {};
await assignGoalOwnerFromSummary("goals/d837ac94-36f5-4735-93bb-d84c69b45435", "agents/tammy");
globalThis.fetch = originalFetch;
loadAgents = originalLoadAgents;
loadGoalExecution = originalLoadGoalExecution;
render = originalRender;
showToast = originalShowToast;
assert(assignmentRequest.url === "/api/agents/agents%2Ftammy/default-goals", "summary assignment used wrong endpoint");
assert(assignmentRequest.method === "POST", "summary assignment did not POST");
assert(assignmentRequest.body.goal_slug === "goals/d837ac94-36f5-4735-93bb-d84c69b45435", "summary assignment sent wrong Goal");
assert(assignmentRequest.body.action === "assign", "summary assignment was not an explicit assign action");
let answerRequest = null;
let answerToast = "";
globalThis.fetch = async (url, options = {}) => {
  answerRequest = { url: String(url), method: options.method, body: JSON.parse(options.body || "{}") };
  return { ok: true, json: async () => ({ verified: true, next_owner: "agents/timmy", todo: {
    slug: "todos/question",
    parent_task: taskSlug,
    status: "done",
    kind: "question",
    text: "Which family-care scope should Toddy use next?",
    detail: "Choose the scope and first bounded action.",
    updated_at: "todo-v2",
    comments: [{ body: "Use the suggested scope.", author: "people/tony-guan" }],
    events: [],
  }, task: {
    slug: taskSlug,
    title: "Review Civic progress",
    summary: "Review Civic progress",
    status: "blocked",
    priority: "normal",
    due_day: "2026-08-25",
    display_markdown: "Question task",
    owner_agent: "agents/timmy",
    project: null,
    goal: goalSlug,
    todos: [],
    handoff: { state: "ready_for_agent", resume_owner: "agents/timmy", resume_action: "Continue after Tony answers." },
  } }) };
};
loadGoalExecution = async () => {};
loadAgentWork = async () => {};
render = () => {};
showToast = (message) => { answerToast = message; };
await answerGoalExecutionQuestionFromSummary(
  { task_slug: taskSlug, todo_slug: "todos/question", todo_updated_at: "todo-v1", summary: "Which family-care scope should Toddy use next?", agent_slug: "agents/timmy" },
  "Use the suggested scope.",
  new FakeElement("button"),
  new FakeElement("p"),
);
globalThis.fetch = originalFetch;
loadGoalExecution = originalLoadGoalExecution;
loadAgentWork = originalLoadAgentWork;
render = originalRender;
showToast = originalShowToast;
assert(answerRequest.url === "/api/todos/todos%2Fquestion/answer", "inline question answer used wrong endpoint");
assert(answerRequest.method === "POST", "inline question answer did not POST");
assert(answerRequest.body.answer === "Use the suggested scope.", "inline question answer sent wrong answer");
assert(answerRequest.body.expected_updated_at === "todo-v1", "inline question answer did not send action queue TODO readback version");
assert(answerRequest.body.actor === "people/tony-guan", "inline question answer did not identify Tony as actor");
assert(answerRequest.body.source === "mission_control", "inline question answer used wrong source");
assert(answerToast.includes("Answer verified"), "inline question answer did not report verified success");
let selectedQuestion = null;
const originalSelectTask = selectTask;
selectTask = (slug, fallback, origin, options = {}) => {
  selectedQuestion = { slug, fallback, origin, options };
};
openGoalExecutionQuestionAction(taskSlug, "todos/question", answerActions[0]);
selectTask = originalSelectTask;
assert(selectedQuestion?.slug === taskSlug, "direct question action opened the wrong task");
assert(selectedQuestion?.origin === answerActions[0], "direct question action did not preserve exact origin");
assert(selectedQuestion?.options?.focusTarget === "handoff-answer", "direct question action did not request answer focus");
assert(selectedQuestion?.options?.todoSlug === "todos/question", "direct question action did not preserve TODO identity");
const rerenderedAnswerAction = new FakeElement("button");
rerenderedAnswerAction.dataset.slug = taskSlug;
rerenderedAnswerAction.dataset.goalExecutionOrigin = answerActions[0].dataset.goalExecutionOrigin;
document.querySelector = (selector) => selector === `[data-goal-execution-origin="${CSS.escape(rerenderedAnswerAction.dataset.goalExecutionOrigin)}"]`
  ? rerenderedAnswerAction
  : null;
document.querySelectorAll = (selector) => selector === ".inline-task-link" ? [new FakeElement("button")] : [];
assert(
  detailFocusReturnTarget({ element: new FakeElement("button"), slug: taskSlug, goalExecutionOrigin: answerActions[0].dataset.goalExecutionOrigin }) === rerenderedAnswerAction,
  "close restoration did not prefer exact direct question action origin over same-slug links",
);
const hydratedQuestionTask = {
  slug: taskSlug,
  title: "Review Civic progress",
  summary: "Review Civic progress",
  status: "blocked",
  priority: "normal",
  due_day: "2026-08-25",
  display_markdown: "Question task",
  owner_agent: "agents/timmy",
  project: null,
  goal: goalSlug,
  todos: [{
    slug: "todos/question",
    parent_task: taskSlug,
    status: "not_done",
    kind: "question",
    text: "Which family-care scope should Toddy use next?",
    detail: "Choose the scope and first bounded action.",
    updated_at: "todo-v1",
    comments: [],
    events: [],
  }],
  handoff: {
    state: "waiting_for_input",
    question_todo: "todos/question",
    resume_owner: "agents/timmy",
    resume_action: "Continue after Tony answers.",
  },
};
state.snapshot.tasks = [hydratedQuestionTask];
selectTask(taskSlug, hydratedQuestionTask, answerActions[0], { exactHydrated: true, focusTarget: "handoff-answer", todoSlug: "todos/question" });
assert(elements.taskHandoffAnswer.focused, "hydrated question task did not focus the handoff answer textarea");
const decision = state.goalExecution.last_run.decisions[0];
assert(goalExecutionState(decision) === "Delivering", "queued handoff was not Delivering");
state.goalExecution.last_run.handoff.status = "retrying";
assert(goalExecutionState(decision) === "Delivering", "retrying handoff was not Delivering");
state.goalExecution.last_run.handoff.status = "received";
assert(goalExecutionState(decision) === "Executing", "received handoff was not Executing");
const duplicateDecision = { goal_slug: goalSlug, reason: "duplicate", task_slug: taskSlug };
state.goalExecution.last_run.decisions = [duplicateDecision];
state.goalExecution.last_run.handoff.status = "queued";
assert(goalExecutionState(duplicateDecision) === "Delivering", "queued duplicate handoff was not Delivering");
assert(goalExecutionReasonCopy(duplicateDecision).includes("delivered this Goal work"), "queued duplicate copy did not explain delivery");
state.goalExecution.last_run.handoff.status = "actively_executing";
assert(goalExecutionState(duplicateDecision) === "Executing", "executing duplicate handoff was not Executing");
assert(goalExecutionReasonCopy(duplicateDecision).includes("actively executing"), "executing duplicate copy did not explain active execution");
assert(goalExecutionState({ goal_slug: goalSlug, reason: "wip_full", task_slug: null }) === "Blocked", "WIP was not Blocked");
assert(goalExecutionState({ goal_slug: goalSlug, reason: "route_unavailable", task_slug: null }) === "Needs attention", "route failure was not Needs attention");
assert(goalExecutionState({ goal_slug: goalSlug, reason: "owner_missing", task_slug: null }) === "Needs attention", "missing owner was not Needs attention");
assert(goalExecutionReasonCopy({ goal_slug: goalSlug, reason: "owner_missing", task_slug: null }).includes("Assign exactly one Codex Agent"), "missing owner copy was not actionable");
assert(goalExecutionReasonCopy({ goal_slug: goalSlug, reason: "owner_missing", task_slug: null }).includes("default_agent_for"), "missing owner copy did not name the canonical relationship");
assert(goalExecutionState({ goal_slug: goalSlug, reason: "handoff_missing", task_slug: taskSlug }) === "Needs attention", "missing handoff was not Needs attention");
assert(goalExecutionReasonCopy({ goal_slug: goalSlug, reason: "handoff_missing", task_slug: taskSlug }).includes("no verified Agent handoff"), "missing handoff copy was not explicit");
assert(goalExecutionState({ goal_slug: goalSlug, reason: "handoff_worker_unavailable", task_slug: taskSlug }) === "Needs attention", "stale queued handoff was not Needs attention");
assert(goalExecutionReasonCopy({ goal_slug: goalSlug, reason: "handoff_worker_unavailable", task_slug: taskSlug }).includes("no verified Agent worker has leased"), "stale queued handoff copy was not explicit");
assert(goalExecutionState({ goal_slug: goalSlug, reason: "task_needs_next_action", task_slug: taskSlug }) === "Needs attention", "missing next action was not Needs attention");
assert(goalExecutionReasonCopy({ goal_slug: goalSlug, reason: "task_needs_next_action", task_slug: taskSlug }).includes("no explicit next action"), "missing next action copy was not explicit");
assert(goalExecutionState({ goal_slug: goalSlug, reason: "waiting_for_tony", task_slug: taskSlug }) === "Blocked", "waiting for Tony was not Blocked");
assert(goalExecutionReasonCopy({ goal_slug: goalSlug, reason: "waiting_for_tony", task_slug: taskSlug }).includes("waiting for Tony"), "waiting for Tony copy was not explicit");
assert(goalExecutionReasonCopy({ goal_slug: goalSlug, reason: "completed_after_verified_handoff", task_slug: taskSlug }).includes("Artifact readback"), "completed handoff reconciliation copy was not explicit");
state.agentTasks = [{
  slug: taskSlug,
  title: "Prepare family-care goal map",
  status: "blocked",
  owner_agent: "agents/timmy",
  project: null,
  handoff: { state: "waiting_for_input", question_todo: "todos/question" },
  todos: [{ slug: "todos/question", status: "not_done", kind: "question", text: "Which family-care scope should Toddy use next?" }],
}];
const waitingDecision = { goal_slug: goalSlug, reason: "waiting_for_tony", task_slug: taskSlug };
state.goalExecution.last_run.decisions = [waitingDecision];
function flattenText(value) {
  if (!value) return "";
  if (typeof value.textContent === "string") return value.textContent;
  if (Array.isArray(value.children)) return value.children.map(flattenText).join(" ");
  return "";
}
assert(flattenText(goalExecutionRow(waitingDecision)).includes("Answer: Which family-care scope should Toddy use next?"), "Goal execution row did not expose the exact question TODO");
assert(flattenText(renderAgentGoalExecution(state.agents[0])).includes("Answer: Which family-care scope should Toddy use next?"), "Agent card compact Goal execution did not expose the exact question TODO");
state.agentTasks = [];
state.snapshot.tasks = [];
state.goalExecution.last_run.task = {
  slug: taskSlug,
  title: "Prepare family-care goal map and weekly review brief",
  status: "blocked",
  agent_slug: "agents/timmy",
};
assert(flattenText(goalExecutionRow(waitingDecision)).includes("Prepare family-care goal map and weekly review brief"), "Goal execution row did not use populated last_run task before Agent Work reconciliation");
assert(flattenText(renderAgentGoalExecution(state.agents[0])).includes("Prepare family-care goal map and weekly review brief"), "Agent card did not use populated last_run task before Agent Work reconciliation");
state.goalExecution.last_run.task = null;
state.goalExecution.last_run.handoff = null;
assert(goalExecutionState({ goal_slug: goalSlug, reason: "auto_eligible", task_slug: null }) === "Ready", "eligible work was not Ready");
const previous = state.goalExecution;
globalThis.fetch = async () => ({ ok: false, json: async () => ({ error: "fixture unavailable" }) });
await loadGoalExecution({ force: true });
assert(state.goalExecution === previous, "failed refresh replaced last-valid Goal execution");
assert(state.goalExecutionError === "fixture unavailable", "failed refresh was not disclosed");
'''
        result = run_app_runtime_probe(probe)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_default_landing_view_is_local_board_first_and_preserves_explicit_routes(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('const DEFAULT_LANDING_VIEW_PREFERENCE_KEY = "mission-control.default-landing-view";', javascript)
        self.assertIn('const DEFAULT_LANDING_VIEW = "board";', javascript)
        self.assertIn("function resolveInitialView", javascript)
        self.assertIn("function setDefaultLandingView", javascript)
        self.assertIn('landingSelect.id = "default-landing-view-preference"', javascript)
        self.assertIn("Explicit route and deep-link selection wins", javascript)

    def test_board_default_window_is_three_days_with_week_preset_and_keeps_actionable_undated_tasks(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('const BOARD_DATE_WINDOW_SESSION_KEY = "mission-control.board-date-window";', javascript)
        self.assertIn("function boardTaskIsVisible", javascript)
        self.assertIn("function boardDateWindowSummary", javascript)
        self.assertIn("const BOARD_DATE_WINDOW_DEFAULT_DAYS = 3;", javascript)
        self.assertIn('const BOARD_DATE_WINDOW_VALUES = new Set(["3", "7", "14", "30", "all"]);', javascript)
        self.assertIn('return BOARD_DATE_WINDOW_VALUES.has(value) ? value : "3";', javascript)
        self.assertIn('const windowValue = BOARD_DATE_WINDOW_VALUES.has(value) ? value : "3";', javascript)
        self.assertIn('["3", "3 Days Before and After Today"]', javascript)
        self.assertIn('["7", "One Week Before and After Today"]', javascript)
        self.assertIn('setBoardDateWindowPreference("3")', javascript)
        self.assertIn('task.status === "active" || task.status === "blocked"', javascript)
        self.assertIn("Undated actionable tasks stay visible", javascript)
        self.assertIn('id = "board-date-window"', javascript)

    def test_completion_celebration_hud_is_centered_without_becoming_modal(self) -> None:
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        region = css[
            css.index(".completion-celebration-region {"):
            css.index(".completion-celebration-stack {")
        ]
        self.assertIn("top: 50%;", region)
        self.assertIn("left: 50%;", region)
        self.assertIn("transform: translate(-50%, -50%);", region)
        self.assertIn("pointer-events: none;", region)

    def test_agent_profile_and_work_issues_are_merged_without_dropping_activation_attention(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("agentProfileIssues: []", javascript)
        self.assertIn("agentWorkIssues: []", javascript)
        self.assertIn("function syncAgentIssues()", javascript)
        self.assertIn("state.agentProfileIssues = Array.isArray(payload.issues)", javascript)
        self.assertIn("state.agentWorkIssues = Array.isArray(payload.issues)", javascript)
        self.assertNotIn(
            "state.agentIssues = Array.isArray(payload.issues) ? payload.issues : [];",
            javascript,
        )

    def test_system_ticket_markdown_routes_are_safe_and_render_inside_mission_control(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function safeSystemTicketMarkdownRoute", javascript)
        self.assertIn("function openMarkdownSystemTicketReference", javascript)
        self.assertIn('<div id="system-ticket-detail-request"></div>', html)
        self.assertIn('<div id="system-ticket-detail-criteria"></div>', html)

        renderer = javascript[
            javascript.index("function renderSafeMarkdown") :
            javascript.index("const AGENT_TASKS_PREFERENCE_KEY")
        ]
        self.assertNotIn("innerHTML", renderer)
        self.assertIn("safeSystemTicketMarkdownRoute(url)", renderer)
        self.assertIn("openMarkdownSystemTicketReference(ticketSlug, link)", renderer)

        internal_ticket_branch = renderer[
            renderer.index("safeSystemTicketMarkdownRoute(url)") :
            renderer.index("if (href)", renderer.index("safeSystemTicketMarkdownRoute(url)"))
        ]
        self.assertNotIn('link.target = "_blank"', internal_ticket_branch)
        self.assertNotIn('link.rel = "noreferrer"', internal_ticket_branch)

        navigation = javascript[
            javascript.index("async function openMarkdownSystemTicketReference") :
            javascript.index("async function openHandoffTaskReference")
        ]
        self.assertIn("loadCorrelatedSystemTicket(ticketSlug)", navigation)
        self.assertIn("selectSystemTicket(ticketSlug, originControl)", navigation)
        self.assertIn("MISSING_LINKED_TASK_ERROR", navigation)

        ticket_detail = javascript[
            javascript.index("function renderSystemTicketList") :
            javascript.index("function openEditSystemTicket")
        ]
        self.assertRegex(
            ticket_detail,
            r"renderSafeMarkdown\(\s*elements\.systemTicketDetailRequest",
        )
        self.assertRegex(
            ticket_detail,
            r"renderSafeMarkdown\(\s*elements\.systemTicketDetailCriteria",
        )
        self.assertRegex(ticket_detail, r"renderSafeMarkdown\(item, entry(?:,|\))")

        result = run_app_runtime_probe(
            r'''
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const valid = "#system-ticket/tasks%2Ffad23bf2-571f-4db0-b9f5-07ab52ae8620";
assert(
  safeSystemTicketMarkdownRoute(valid) === "tasks/fad23bf2-571f-4db0-b9f5-07ab52ae8620",
  "canonical encoded System Ticket route was rejected",
);
for (const unsafe of [
  "#system-ticket/tasks%252Ffad23bf2-571f-4db0-b9f5-07ab52ae8620",
  "#system-ticket/tasks%2Ffad23bf2-571f-6db0-b9f5-07ab52ae8620",
  "#system-ticket/tasks%2Ffad23bf2-571f-4db0-b9f5-07ab52ae8620%2Fchild",
  "#system-ticket/tasks%5Cfad23bf2-571f-4db0-b9f5-07ab52ae8620",
  "#task/tasks%2Ffad23bf2-571f-4db0-b9f5-07ab52ae8620",
]) {
  assert(safeSystemTicketMarkdownRoute(unsafe) === null, `unsafe route accepted: ${unsafe}`);
}
const container = new FakeElement();
renderSafeMarkdown(
  container,
  `[Ticket](${valid}) https://example.com/docs). http://127.0.0.1:4179/status. http://localhost:4179/health. http://127.0.0.1 http://localhost:4179 http://localhost.evil/secret javascript:alert(1) <img src=x onerror=alert(1)>`,
);
const anchors = [];
const text = [];
const walk = (entry) => {
  if (entry?.tagName === "A") anchors.push(entry);
  if (entry?.textContent) text.push(entry.textContent);
  for (const child of entry?.children || []) walk(child);
};
walk(container);
assert(anchors.length === 6, `expected only safe links, received ${anchors.length}`);
assert(anchors[0].getAttribute("target") === null, "internal Ticket link opened a new tab");
assert(anchors[1].href === "https://example.com/docs", "HTTPS bare URL included punctuation");
assert(anchors[2].href === "http://127.0.0.1:4179/status", "loopback bare URL included punctuation");
assert(anchors[3].href === "http://localhost:4179/health", "localhost bare URL was not rendered");
assert(anchors[4].href === "http://127.0.0.1", "bare 127.0.0.1 did not terminate safely");
assert(anchors[5].href === "http://localhost:4179", "bare localhost host/port did not terminate safely");
assert(!anchors.some((link) => String(link.href).includes("localhost.evil")), "localhost prefix matched a non-loopback host");
assert(!anchors.some((link) => String(link.href).startsWith("javascript:")), "unsafe scheme became a link");
assert(text.join("").includes("<img src=x onerror=alert(1)>"), "raw HTML rendered instead of text");
'''
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_markdown_renderer_keeps_tilde_and_arbitrary_backtick_fences_inert(self) -> None:
        result = run_app_runtime_probe(
            r'''
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const route = "#system-ticket/tasks%2Ffad23bf2-571f-4db0-b9f5-07ab52ae8620";
const container = new FakeElement();
renderSafeMarkdown(
  container,
  [
    "~~~md",
    `[Tilde Ticket](${route}) http://localhost:4179/inside-tilde`,
    "~~~",
    "",
    "````text",
    `[Four Tick Ticket](${route}) http://127.0.0.1:4179/inside-four`,
    "```",
    "still code http://localhost/inside-four",
    "````",
    "",
    "http://localhost http://127.0.0.1:4179",
  ].join("\n"),
);
const anchors = [];
const pre = [];
const text = [];
const walk = (entry) => {
  if (entry?.tagName === "A") anchors.push(entry);
  if (entry?.tagName === "PRE") pre.push(entry);
  if (entry?.textContent) text.push(entry.textContent);
  for (const child of entry?.children || []) walk(child);
};
walk(container);
assert(pre.length === 2, `expected two fenced blocks, received ${pre.length}`);
assert(anchors.length === 2, `links inside fences became active: ${anchors.length}`);
assert(anchors[0].href === "http://localhost", "outside bare localhost link was lost");
assert(anchors[1].href === "http://127.0.0.1:4179", "outside loopback host/port link was lost");
assert(text.join("\n").includes("still code http://localhost/inside-four"), "shorter backtick run closed a four-backtick fence");
'''
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_markdown_renderer_keeps_arbitrary_inline_backtick_runs_inert(self) -> None:
        result = run_app_runtime_probe(
            r'''
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const route = "#system-ticket/tasks%2Ffad23bf2-571f-4db0-b9f5-07ab52ae8620";
const container = new FakeElement();
renderSafeMarkdown(
  container,
  "``literal ` tick and [Ticket](" + route + ")``\n\nhttp://localhost:4179/outside",
);
const anchors = [];
const code = [];
const walk = (entry) => {
  if (entry?.tagName === "A") anchors.push(entry);
  if (entry?.tagName === "CODE") code.push(entry);
  for (const child of entry?.children || []) walk(child);
};
walk(container);
assert(code.length === 1, `expected one inline code span, received ${code.length}`);
assert(code[0].textContent === "literal ` tick and [Ticket](" + route + ")", "matching double-backtick content drifted");
assert(anchors.length === 1, `internal Markdown inside inline code became active: ${anchors.length}`);
assert(anchors[0].href === "http://localhost:4179/outside", "outside loopback link was lost");
'''
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bare_loopback_urls_keep_query_and_fragment_tails(self) -> None:
        result = run_app_runtime_probe(
            r'''
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const container = new FakeElement();
renderSafeMarkdown(
  container,
  "http://localhost:4179?mode=qa#ticket http://127.0.0.1#health http://localhost.evil?mode=qa",
);
const anchors = [];
const walk = (entry) => {
  if (entry?.tagName === "A") anchors.push(entry);
  for (const child of entry?.children || []) walk(child);
};
walk(container);
assert(anchors.length === 2, `hostile prefix or tail tokenization drifted: ${anchors.length}`);
assert(anchors[0].href === "http://localhost:4179?mode=qa#ticket", `query/fragment tail was lost: ${anchors[0].href}`);
assert(anchors[1].href === "http://127.0.0.1#health", `fragment tail was lost: ${anchors[1].href}`);
'''
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_http_tokenizer_rejects_whole_hostile_candidates_without_prefix_links(self) -> None:
        result = run_app_runtime_probe(
            r'''
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const container = new FakeElement();
const hostile = [
  "http://localhost@evil.com/path",
  "http://localhost_evil/path",
  "http://localhost%2Fevil/path",
  "http://127.0.0.1%2Eevil/path",
];
renderSafeMarkdown(
  container,
  [
    "http://localhost:4179/path?mode=qa#ticket",
    "http://127.0.0.1:8788?slug=tasks%2Fsafe#detail",
    ...hostile,
  ].join(" "),
);
const anchors = [];
const walk = (entry) => {
  if (entry?.tagName === "A") anchors.push(entry);
  for (const child of entry?.children || []) walk(child);
};
walk(container);
assert(anchors.length === 2, `hostile candidate was partially autolinked: ${anchors.map((link) => link.href).join(",")}`);
assert(anchors[0].href === "http://localhost:4179/path?mode=qa#ticket", "valid localhost path/query/fragment drifted");
assert(anchors[1].href === "http://127.0.0.1:8788?slug=tasks%2Fsafe#detail", "valid 127.0.0.1 query/fragment drifted");
assert(!anchors.some((link) => hostile.some((candidate) => candidate.startsWith(link.href))), "a hostile URL retained a loopback prefix anchor");
'''
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_markdown_renderer_decodes_safe_generated_escapes_and_entities(self) -> None:
        result = run_app_runtime_probe(
            r'''
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const route = "#system-ticket/tasks%2Ffad23bf2-571f-4db0-b9f5-07ab52ae8620";
const container = new FakeElement();
renderSafeMarkdown(
  container,
  `# Generated \\# \\[title\\]

[Canonical \\[Ticket&#93;](${route})

[External \\[Doc&#93;](https://example.com)`,
);
const anchors = [];
const text = [];
const walk = (entry) => {
  if (entry?.tagName === "A") anchors.push(entry);
  if (entry?.textContent) text.push(entry.textContent);
  for (const child of entry?.children || []) walk(child);
};
walk(container);
assert(anchors.length === 2, "generated links were not rendered");
assert(anchors[0].textContent === "Canonical [Ticket]", `entity/escape leaked into label: ${anchors[0].textContent}`);
assert(anchors[1].textContent === "External [Doc]", `external label escape leaked: ${anchors[1].textContent}`);
const rendered = text.join(" ");
assert(rendered.includes("Generated # [title]"), `heading escapes remained visible: ${rendered}`);
assert(!rendered.includes("&#93;") && !rendered.includes("\\#"), "generated escape syntax remained visible");
'''
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_task_and_ticket_display_projections_render_with_historical_fallbacks(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="system-ticket-detail-markdown"', html)
        self.assertIn("task.display_markdown || task.detail || \"\"", javascript)
        self.assertIn("ticket.display_markdown", javascript)
        self.assertIn("sourceKind: \"task\"", javascript)
        self.assertIn("sourceKind: \"system-ticket\"", javascript)

        result = run_app_runtime_probe(
            r'''
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const destination = "tasks/fad23bf2-571f-4db0-b9f5-07ab52ae8620";
const route = "#system-ticket/tasks%2Ffad23bf2-571f-4db0-b9f5-07ab52ae8620";
const taskContainer = new FakeElement();
renderSafeMarkdown(taskContainer, `# Task\n\n[Destination](${route})`, {
  sourceKind: "task",
  sourceSlug: "tasks/0bcdef12-3456-4abc-8def-0123456789ab",
  referenceScope: "body",
});
const anchors = [];
const walk = (entry) => {
  if (entry?.tagName === "A") anchors.push(entry);
  for (const child of entry?.children || []) walk(child);
};
walk(taskContainer);
assert(anchors.length === 1, "Task display projection did not render the internal Ticket link");
assert(anchors[0].dataset.systemTicketReferenceSourceKind === "task", "Task source kind was not recorded");
assert(anchors[0].dataset.systemTicketReferenceSourceSlug === "tasks/0bcdef12-3456-4abc-8def-0123456789ab", "Task source slug was not recorded");
assert(safeSystemTicketMarkdownRoute(anchors[0].href) === destination, "Task projection link target drifted");
'''
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_markdown_ticket_navigation_restores_task_and_nested_ticket_sources(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        close_details = javascript[
            javascript.index("function closeDetails") :
            javascript.index("async function saveTaskGoal")
        ]

        self.assertIn("systemTicketMarkdownReturn", javascript)
        self.assertIn("restoreMarkdownSystemTicketReferenceFocus", javascript)
        self.assertIn("sourceKind", close_details)
        self.assertIn("sourceSlug", close_details)
        self.assertIn("referenceKey", close_details)

        result = run_app_runtime_probe(
            r'''
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const taskSlug = "tasks/1bcdef12-3456-4abc-8def-0123456789ab";
const firstTicketSlug = "tasks/fad23bf2-571f-4db0-b9f5-07ab52ae8620";
const secondTicketSlug = "tasks/0bcdef12-3456-4abc-8def-0123456789ab";
const taskCard = new FakeElement("button");
taskCard.dataset.slug = taskSlug;
const taskReplacement = new FakeElement("a");
taskReplacement.isConnected = true;
const ticketReplacement = new FakeElement("a");
ticketReplacement.isConnected = true;
const sourceCard = new FakeElement("button");
sourceCard.isConnected = true;
sourceCard.dataset.slug = firstTicketSlug;
document.querySelector = (selector) => {
  if (selector.includes('source-kind="task"')) return taskReplacement;
  if (selector.includes('source-kind="system-ticket"')) return ticketReplacement;
  return null;
};
document.querySelectorAll = (selector) => (
  selector === ".system-ticket-card" ? [sourceCard] : []
);
render = () => {};
state.snapshot = { tasks: [{ slug: taskSlug, title: "Task source", detail: "" }], goals: [] };
state.systemTickets = [{ slug: firstTicketSlug, title: "First Ticket" }];
state.completedSystemTickets = [];
state.selectedKind = "system-ticket";
state.selectedSlug = firstTicketSlug;
state.detailReturnFocus = { element: taskCard, slug: taskSlug };
state.systemTicketMarkdownReturn = {
  sourceKind: "task",
  sourceSlug: taskSlug,
  destinationTicketSlug: firstTicketSlug,
  referenceKey: "body:0",
  detailReturnFocus: { element: taskCard, slug: taskSlug },
  parent: null,
};
elements.detailPanel.setAttribute("aria-hidden", "false");
selectTask = (slug) => {
  state.selectedKind = "task";
  state.selectedSlug = slug;
};
selectSystemTicket = (slug) => {
  state.selectedKind = "system-ticket";
  state.selectedSlug = slug;
};
closeDetails();
assert(state.selectedKind === "task" && state.selectedSlug === taskSlug, "Task source detail was not restored");
assert(taskReplacement.focused, "Task-origin Markdown link did not regain focus");

taskReplacement.focused = false;
state.selectedKind = "system-ticket";
state.selectedSlug = secondTicketSlug;
state.systemTicketMarkdownReturn = {
  sourceKind: "system-ticket",
  sourceSlug: firstTicketSlug,
  destinationTicketSlug: secondTicketSlug,
  referenceKey: "body:0",
  detailReturnFocus: { element: sourceCard, slug: firstTicketSlug },
  parent: {
    sourceKind: "task",
    sourceSlug: taskSlug,
    destinationTicketSlug: firstTicketSlug,
    referenceKey: "body:0",
    detailReturnFocus: { element: taskCard, slug: taskSlug },
    parent: null,
  },
};
closeDetails();
assert(state.selectedKind === "system-ticket" && state.selectedSlug === firstTicketSlug, "Nested Ticket source was not restored first");
assert(ticketReplacement.focused, "Nested Ticket Markdown link did not regain focus");
closeDetails();
assert(state.selectedKind === "task" && state.selectedSlug === taskSlug, "Nested return did not continue to Task source");
assert(taskReplacement.focused, "Nested return did not restore the exact Task link");
'''
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_job_applied_metric_editor_shows_scoped_breakdown_and_safe_minimum(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="task-metric-use-minimum"', html)
        self.assertIn("state.snapshot?.event_bindings?.job_applied?.task_slug", javascript)
        self.assertIn("distinct verified event", javascript)
        self.assertIn("baseline/manual", javascript)
        self.assertIn("not a global queue total", javascript)
        self.assertIn("taskMetricUseMinimum.dataset.minimum", javascript)
        self.assertIn("elements.taskMetricCurrent.focus()", javascript)
        self.assertIn("function updateTaskMetricBindingAvailability()", javascript)
        self.assertIn("automatic.disabled = !editingBoundTask", javascript)
        self.assertIn("manual.disabled = Number(context.verifiedCount", javascript)
        self.assertNotIn(
            'elements.taskMetricEventBinding.value === "job_applied") {\n    elements.taskMetricEventBinding.value = ""',
            javascript,
        )
        self.assertIn("progress_metric_revision", javascript)
        self.assertIn("At ${metric.target} / ${metric.target}", javascript)
        self.assertNotIn("At 5 / 5", javascript)

    def test_task_editor_opens_immediately_and_coalesces_reference_reads(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function showTaskEditorLoading(mode, heading)", javascript)
        self.assertIn('showTaskEditorLoading("edit", "Preparing Edit…")', javascript)
        self.assertIn('showTaskEditorLoading("duplicate", "Preparing Duplicate…")', javascript)
        self.assertIn("if (state.agentsLoadPromise) return state.agentsLoadPromise", javascript)
        self.assertIn("if (state.projectsLoadPromise) return state.projectsLoadPromise", javascript)


    def test_user_facing_todo_labels_use_compact_todo_spelling(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn(">TODOs<", html)
        self.assertIn("Add a TODO", html)
        self.assertIn("Initial TODO", html)
        self.assertNotIn("Each item, comment, and change is durable in GBrain.", html)
        self.assertNotIn("To Do", html)
        for old_copy in (
            "No open To Dos",
            "To Do:",
            "open To Do",
            "No To Do yet",
            "Edit To Do",
            "Save To Do",
            "Comment on To Do",
            "To Do created",
            "To Do edit",
            "To Do marked",
        ):
            self.assertNotIn(old_copy, javascript)

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
            "function selectArtifact(artifactSlug, originControl = null,",
            javascript,
        )
        self.assertIn("function loadArtifacts(", javascript)
        self.assertIn("No artifacts yet", javascript)
        self.assertIn("renderSafeMarkdown(elements.artifactDetailMarkdown", javascript)
        artifact_selector = javascript[
            javascript.index("function selectArtifact(artifactSlug, originControl = null,") :
            javascript.index("function selectTask(")
        ]
        self.assertNotIn("innerHTML", artifact_selector)
        self.assertIn(".artifact-detail-content", stylesheet)
        mobile = stylesheet[stylesheet.index("@media (max-width: 760px)") :]
        self.assertIn(".detail-panel", mobile)
        self.assertIn("position: fixed", mobile)
        self.assertIn("inset: 0", mobile)
        self.assertIn("overflow-x: hidden", mobile)

    def test_artifacts_support_canonical_hierarchy_and_recent_modes(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        stylesheet = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('artifactViewMode: "hierarchy"', javascript)
        self.assertIn("artifactExpanded: new Set()", javascript)
        self.assertIn("function buildArtifactHierarchy", javascript)
        self.assertIn("function artifactHierarchyNode", javascript)
        self.assertIn('"Hierarchy"', javascript)
        self.assertIn('"Recent"', javascript)
        self.assertIn('"Default Goal"', javascript)
        self.assertIn('"Default Project"', javascript)
        self.assertIn("Items without an explicit Goal relationship", javascript)
        self.assertIn("Items without an explicit Project relationship", javascript)
        self.assertNotIn('return "No Goal"', javascript)
        self.assertNotIn('return "No Project"', javascript)
        self.assertIn('"No producing Task"', javascript)
        self.assertIn('setAttribute("aria-expanded"', javascript)
        self.assertIn('setAttribute("aria-controls"', javascript)
        self.assertIn("loaded Artifact", javascript)
        self.assertIn(".artifact-hierarchy", stylesheet)
        self.assertIn(".artifact-hierarchy-toggle", stylesheet)
        self.assertIn(".artifact-hierarchy-children[hidden]", stylesheet)
        mobile = stylesheet[stylesheet.index("@media (max-width: 760px)") :]
        self.assertIn(".artifact-hierarchy", mobile)
        self.assertIn("overflow-x: hidden", mobile)

    def test_artifact_hierarchy_is_compact_and_task_rows_are_title_only(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        stylesheet = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        hierarchy_node = javascript[
            javascript.index("function artifactHierarchyNode") :
            javascript.index("function artifactViewModeButton")
        ]
        self.assertIn('entry.kind !== "task"', hierarchy_node)
        self.assertIn('classList.add("is-task-title-only")', hierarchy_node)
        self.assertIn("--artifact-reader-default: minmax(0, 78vw)", stylesheet)
        self.assertIn(".artifact-hierarchy-toggle.is-task-title-only", stylesheet)
        self.assertIn("grid-template-columns: 18px minmax(0, 1fr)", stylesheet)
        self.assertIn(".artifact-hierarchy-toggle.is-task-title-only .artifact-hierarchy-count", stylesheet)
        self.assertIn("display: none", stylesheet)

    def test_artifact_reader_uses_two_thirds_of_desktop_and_full_mobile_sheet(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="artifact-detail-gbrain-link"', html)
        self.assertIn('id="artifact-detail-slug"', html)
        self.assertIn(
            ".app-shell:has(.artifact-detail-content:not(.is-hidden))",
            css,
        )
        self.assertIn("minmax(0, 78vw)", css)
        initializer = javascript[
            javascript.index("function initializeDetailPanelResize") :
            javascript.index("function syncMobileDetailModalState")
        ]
        self.assertIn(
            "setDetailPanelWidth(readDetailPanelWidth(), { persist: false })",
            initializer,
        )
        self.assertIn(
            'window.addEventListener("resize", () => setDetailPanelWidth(\n'
            "    readDetailPanelWidth(),\n"
            "    { persist: false },",
            initializer,
        )
        mobile = css[css.index("@media (max-width: 760px)") :]
        self.assertIn(".artifact-detail-content", mobile)
        self.assertIn("max-width: 100%", mobile)

    def test_artifact_detail_opens_verified_producing_task_in_same_detail_panel(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="artifact-detail-task-link"', html)
        selector = javascript[
            javascript.index("function selectArtifact(artifactSlug, originControl = null,") :
            javascript.index("function renderTaskArtifacts")
        ]
        self.assertIn("artifact.produced_for", selector)
        self.assertIn("artifactDetailTaskLink", selector)
        self.assertIn("Open producing Task", selector)
        self.assertIn("selectTask(task.slug", selector)
        self.assertIn("artifactProducingTaskReturn", javascript)
        self.assertIn("state.artifactExpanded", javascript)

    def test_task_artifact_list_labels_explicit_review_references(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        renderer = javascript[
            javascript.index("function renderTaskArtifacts") :
            javascript.index("async function loadTaskArtifacts")
        ]

        self.assertIn("artifact.relation_context", renderer)
        self.assertIn('"Linked for review"', renderer)
        self.assertIn('"Produced by this Task"', renderer)
        self.assertIn("task-artifact-relation", renderer)
        self.assertIn(".task-artifact-relation", css)

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
        self.assertIn("candidate.dataset.slug === anchor.slug", close_details)
        self.assertIn("detailFocusReturnAnchor", javascript)
        self.assertIn("restorePendingDetailFocus", javascript)
        self.assertIn('document.addEventListener("focusin"', javascript)

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
        bootstrap = javascript[-1500:]
        self.assertIn("loadAgentWork();", bootstrap)
        self.assertIn('loadTasks({ reason: "initial" });', bootstrap)
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

    def test_agents_handoff_history_uses_compact_verified_task_links(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        stylesheet = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        renderer = javascript[
            javascript.index("function renderHandoffEvents")
            : javascript.index("function taskHandoffEntry")
        ]

        self.assertIn("event.task_ref?.available", renderer)
        self.assertIn("truncateVisibleTaskTitle", javascript)
        self.assertIn("openHandoffTaskReference", javascript)
        self.assertIn('node("span", "handoff-event-separator", " - ")', renderer)
        self.assertIn('Task:${truncateVisibleTaskTitle', renderer)
        self.assertIn("Task unavailable", renderer)
        self.assertNotIn('`Task: ${privacySafeEventText(event.task_slug', renderer)
        self.assertNotIn('"handoff-event-type"', renderer)
        self.assertIn(".handoff-event-meta", stylesheet)
        self.assertIn(".handoff-event-mainline", stylesheet)
        self.assertIn("grid-template-columns: minmax(0, 1fr)", stylesheet)

    def test_handoff_task_title_truncation_is_unicode_safe_and_deterministic(self) -> None:
        probe = r"""
const short = truncateVisibleTaskTitle("Short title", 20);
const exact = truncateVisibleTaskTitle("12345678901234567890", 20);
const long = truncateVisibleTaskTitle("0123456789🙂ABCDE中文XYZ", 20);
if (short !== "Short title") throw new Error(`short=${short}`);
if (exact !== "12345678901234567890") throw new Error(`exact=${exact}`);
if (long !== "0123456789🙂ABCDE中文XY…") throw new Error(`long=${long}`);
"""
        result = run_app_runtime_probe(probe)
        self.assertEqual(result.returncode, 0, result.stderr)

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

    def test_desktop_detail_panel_is_resizable_persistent_and_mobile_safe(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        stylesheet = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="detail-resize-handle"', html)
        self.assertIn('role="separator"', html)
        self.assertIn('aria-orientation="vertical"', html)
        self.assertIn('aria-label="Resize details panel"', html)
        self.assertIn('const DETAIL_WIDTH_PREFERENCE_KEY', javascript)
        self.assertIn("function setDetailPanelWidth", javascript)
        self.assertIn("function initializeDetailPanelResize", javascript)
        self.assertIn("if (stored === null) return DETAIL_WIDTH_DEFAULT", javascript)
        self.assertIn('addEventListener("pointerdown"', javascript)
        self.assertIn('addEventListener("keydown"', javascript)
        self.assertIn('addEventListener("dblclick"', javascript)
        self.assertIn("window.localStorage.setItem", javascript)
        self.assertIn("--detail-panel-width", stylesheet)
        self.assertIn("cursor: col-resize", stylesheet)
        mobile = stylesheet[stylesheet.index("@media (max-width: 760px)") :]
        self.assertIn(".detail-resize-handle", mobile)
        self.assertIn("display: none", mobile)
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

    def test_board_read_only_task_drop_does_not_write_or_enter_saving_state(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const readonlyTask = {
  slug: "tasks/read-only-agent",
  status: "planned",
  title: "Read-only Agent task",
  summary: "Read-only Agent task",
  priority: "normal",
  due_day: "2026-08-06",
  read_only: true,
  owner_agent: "agents/tammy",
  owner: { name: "Tammy", avatar: { kind: "initials", value: "T" } },
};
state.snapshot = {
  as_of: "2026-08-06",
  tasks: [],
  goals: [],
  views: { blocked: [], completed: [] },
  today: { in_progress: [], todays_actions: [], overdue: [] },
};
state.agentTasks = [readonlyTask];
let writes = 0;
globalThis.fetch = async () => {
  writes += 1;
  throw new Error("read-only task should not write");
};
render = () => {};
showToast = () => {};

await moveBoardTask(readonlyTask.slug, "active");

assert(writes === 0, `read-only board move wrote ${writes} times`);
assert(state.boardMove === null, "read-only board move entered saving/error state");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_task_cards_render_verified_project_titles_not_raw_slugs(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const textOf = (element) => {
  if (!element) return "";
  const own = element.textContent || "";
  const child = (element.children || []).map(textOf).join(" ");
  return `${own} ${child}`.replace(/\s+/g, " ").trim();
};
const task = {
  slug: "tasks/with-project",
  status: "planned",
  title: "Task with canonical project",
  summary: "Task with canonical project",
  detail: "",
  priority: "normal",
  project: "projects/project-opaque-id",
  goal: "goals/career",
  due_day: "2026-08-06",
  inbox: false,
  lifecycle_root: "collections/tonys-tasks",
};
state.snapshot = {
  owner: { name: "Tony", avatar: { kind: "initials", value: "T" } },
  as_of: "2026-08-06",
  tasks: [task],
  goals: [{ slug: "goals/career", title: "Career: Engineering Manager" }],
  views: { blocked: [], completed: [] },
  today: { in_progress: [], todays_actions: [task], overdue: [] },
};
state.projects = [{ slug: "projects/project-opaque-id", title: "Career Path Tuning Up" }];
const boardText = textOf(boardCard(task));
const rowText = textOf(taskRow(task, { calendarWeek: true }));

assert(boardText.includes("Career Path Tuning Up"), `Board card missing project title: ${boardText}`);
assert(rowText.includes("Career Path Tuning Up"), `Task row missing project title: ${rowText}`);
assert(!boardText.includes("projects/project-opaque-id"), `Board card leaked raw project slug: ${boardText}`);
assert(!rowText.includes("projects/project-opaque-id"), `Task row leaked raw project slug: ${rowText}`);
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_task_snapshot_with_project_references_loads_verified_project_titles(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        task_load = javascript[
            javascript.index("async function performTaskLoad(reason)")
            : javascript.index("function loadTasks(")
        ]

        self.assertIn("function snapshotHasProjectReferences", javascript)
        self.assertIn("snapshotHasProjectReferences(state.snapshot)", task_load)
        self.assertIn("void loadProjects()", task_load)

    def test_select_task_opens_busy_detail_before_exact_gbrain_read_finishes(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const taskSlug = "tasks/cold-detail-read";
state.snapshot = {
  as_of: "2026-08-06",
  tasks: [],
  goals: [],
  views: { blocked: [], completed: [] },
  today: { in_progress: [], todays_actions: [], overdue: [] },
};
state.agentTasks = [];
state.projects = [];
render = () => {};
renderTaskTodos = () => {};
renderTaskArtifacts = () => {};
renderTaskHandoffTimeline = () => {};
loadTaskArtifacts = async () => {};
loadTaskHandoffTimeline = async () => {};
let reads = 0;
let releaseRead;
globalThis.fetch = async (url) => {
  reads += 1;
  assert(url === `/api/tasks/${encodeURIComponent(taskSlug)}`, `unexpected URL ${url}`);
  return await new Promise((resolve) => {
    releaseRead = () => resolve({
      ok: true,
      json: async () => ({
        task: {
          slug: taskSlug,
          status: "planned",
          title: "Loaded canonical task",
          summary: "Loaded canonical task",
          detail: "Verified detail",
          priority: "normal",
          due_day: "2026-08-06",
          lifecycle_root: "collections/tonys-tasks",
        },
      }),
    });
  });
};
const origin = new FakeElement("button");
const pending = selectTask(taskSlug, null, origin);
selectTask(taskSlug, null, origin);

assert(elements.detailPanel.getAttribute("aria-hidden") === "false", "detail panel did not open immediately");
assert(elements.detailPanel.getAttribute("aria-busy") === "true", "detail panel was not marked busy");
assert(elements.detailTitle.textContent.includes("Reading canonical Task"), `missing busy heading: ${elements.detailTitle.textContent}`);
assert(reads === 1, `same-task cold selections were not coalesced: ${reads}`);

releaseRead();
await pending;

assert(elements.detailPanel.getAttribute("aria-busy") === "false", "detail panel did not clear busy state");
assert(elements.detailTitle.textContent === "Loaded canonical task", `canonical detail did not render: ${elements.detailTitle.textContent}`);
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_select_task_reopens_busy_detail_when_same_task_read_is_still_in_flight(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const taskSlug = "tasks/long-lived-all-tasks";
state.snapshot = {
  as_of: "2026-08-12",
  tasks: [{
    slug: taskSlug,
    status: "planned",
    title: "Long-lived All Tasks row",
    summary: "Long-lived All Tasks row",
    detail: "Projection without exact detail.",
    priority: "normal",
    due_day: "2026-08-12",
    lifecycle_root: "collections/tonys-tasks",
  }],
  goals: [],
  views: { blocked: [], completed: [] },
  today: { in_progress: [], todays_actions: [], overdue: [] },
};
state.activeView = "all";
state.agentTasks = [];
state.projects = [];
render = () => {};
renderTaskTodos = () => {};
renderTaskArtifacts = () => {};
renderTaskHandoffTimeline = () => {};
loadTaskArtifacts = async () => {};
loadTaskHandoffTimeline = async () => {};
let reads = 0;
let releaseRead;
globalThis.fetch = async (url) => {
  reads += 1;
  assert(url === `/api/tasks/${encodeURIComponent(taskSlug)}`, `unexpected URL ${url}`);
  return await new Promise((resolve) => {
    releaseRead = () => resolve({
      ok: true,
      json: async () => ({
        task: {
          slug: taskSlug,
          status: "planned",
          title: "Loaded long-lived Task",
          summary: "Loaded long-lived Task",
          detail: "Verified detail after long-lived session.",
          display_markdown: "# Loaded long-lived Task\n\nVerified detail.",
          priority: "normal",
          due_day: "2026-08-12",
          lifecycle_root: "collections/tonys-tasks",
        },
      }),
    });
  });
};
const origin = new FakeElement("button");
const pending = selectTask(taskSlug, null, origin);
assert(reads === 1, `first selection did not start exactly one read: ${reads}`);
elements.detailPanel.setAttribute("aria-hidden", "true");
elements.detailPanel.setAttribute("aria-busy", "false");
state.selectedSlug = null;
state.selectedKind = null;

selectTask(taskSlug, null, origin);

assert(reads === 1, `same in-flight Task selection launched duplicate reads: ${reads}`);
assert(elements.detailPanel.getAttribute("aria-hidden") === "false", "detail panel did not reopen for same in-flight Task");
assert(elements.detailPanel.getAttribute("aria-busy") === "true", "reopened detail panel was not busy");
assert(elements.detailTitle.textContent === "Long-lived All Tasks row", `fallback title was not preserved while busy: ${elements.detailTitle.textContent}`);
assert(elements.taskDetailStatus.textContent === "Reading", `missing busy status: ${elements.taskDetailStatus.textContent}`);

releaseRead();
await pending;

assert(elements.detailPanel.getAttribute("aria-busy") === "false", "detail panel did not finish loading");
assert(elements.detailTitle.textContent === "Loaded long-lived Task", `wrong loaded detail: ${elements.detailTitle.textContent}`);
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_task_detail_read_timeout_is_bounded_and_retries_without_page_refresh(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const taskSlug = "tasks/bounded-detail-read";
state.snapshot = {
  as_of: "2026-08-18",
  tasks: [{
    slug: taskSlug,
    status: "planned",
    title: "Bounded canonical detail",
    summary: "Bounded canonical detail",
    detail: "Projected fallback",
    priority: "normal",
    due_day: "2026-08-18",
    lifecycle_root: "collections/tonys-tasks",
  }],
  goals: [],
  views: { blocked: [], completed: [] },
  today: { in_progress: [], todays_actions: [], overdue: [] },
};
state.agentTasks = [];
state.projects = [];
render = () => {};
renderTaskTodos = () => {};
renderTaskArtifacts = () => {};
renderTaskHandoffTimeline = () => {};
loadTaskArtifacts = async () => {};
loadTaskHandoffTimeline = async () => {};

let timerId = 0;
const timers = new Map();
window.setTimeout = (callback, delay) => {
  timerId += 1;
  timers.set(timerId, { callback, delay });
  return timerId;
};
window.clearTimeout = (id) => timers.delete(id);
let reads = 0;
let aborted = 0;
globalThis.fetch = async (url, options = {}) => {
  reads += 1;
  assert(url === `/api/tasks/${encodeURIComponent(taskSlug)}`, `unexpected URL ${url}`);
  return await new Promise((resolve, reject) => {
    options.signal?.addEventListener("abort", () => {
      aborted += 1;
      const error = new Error("aborted");
      error.name = "AbortError";
      reject(error);
    }, { once: true });
  });
};

const origin = new FakeElement("button");
const pending = selectTask(taskSlug, null, origin);
assert(reads === 1, `selection launched ${reads} reads`);
const watchdog = Array.from(timers.values()).find(({ delay }) => delay === TASK_DETAIL_READ_TIMEOUT_MS);
assert(watchdog, "canonical detail watchdog was not armed");
watchdog.callback();
await pending;

assert(aborted === 1, `watchdog did not abort the hung read: ${aborted}`);
assert(elements.detailPanel.getAttribute("aria-hidden") === "false", "timeout closed the detail surface");
assert(elements.detailPanel.getAttribute("aria-busy") === "false", "timeout left detail busy");
assert(elements.taskDetailStatus.textContent === "Read timed out", `wrong timeout status: ${elements.taskDetailStatus.textContent}`);
assert(elements.detailCopy.children.some((child) => String(child.textContent || "").includes("without reloading Mission Control")), "timeout recovery copy was not actionable");
assert(elements.taskDetailRetry.getAttribute("aria-hidden") === "false", "retry control was not exposed");

globalThis.fetch = async () => {
  reads += 1;
  return {
    ok: true,
    json: async () => ({
      task: {
        slug: taskSlug,
        status: "planned",
        title: "Recovered canonical detail",
        summary: "Recovered canonical detail",
        detail: "Verified after retry",
        display_markdown: "# Recovered canonical detail\n\nVerified after retry",
        priority: "normal",
        due_day: "2026-08-18",
        lifecycle_root: "collections/tonys-tasks",
      },
    }),
  };
};
const retry = retryTaskDetailRead();
await retry;
assert(reads === 2, `retry did not launch exactly one new read: ${reads}`);
assert(elements.detailTitle.textContent === "Recovered canonical detail", `retry did not reconcile: ${elements.detailTitle.textContent}`);
assert(elements.detailPanel.getAttribute("aria-busy") === "false", "retry did not finish canonical reconciliation");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_switching_or_closing_task_detail_cancels_only_the_obsolete_read(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const firstSlug = "tasks/first-hung-detail";
const secondSlug = "tasks/second-canonical-detail";
state.snapshot = {
  as_of: "2026-08-18",
  tasks: [
    { slug: firstSlug, status: "planned", title: "First", detail: "", lifecycle_root: "collections/tonys-tasks" },
    { slug: secondSlug, status: "planned", title: "Second", detail: "", lifecycle_root: "collections/tonys-tasks" },
  ],
  goals: [],
  views: { blocked: [], completed: [] },
  today: { in_progress: [], todays_actions: [], overdue: [] },
};
state.agentTasks = [];
state.projects = [];
render = () => {};
renderTaskTodos = () => {};
renderTaskArtifacts = () => {};
renderTaskHandoffTimeline = () => {};
loadTaskArtifacts = async () => {};
loadTaskHandoffTimeline = async () => {};
window.setTimeout = () => 1;
window.clearTimeout = () => {};
let firstAborted = 0;
globalThis.fetch = async (url, options = {}) => {
  if (url.includes(encodeURIComponent(firstSlug))) {
    return await new Promise((resolve, reject) => {
      options.signal?.addEventListener("abort", () => {
        firstAborted += 1;
        const error = new Error("aborted");
        error.name = "AbortError";
        reject(error);
      }, { once: true });
    });
  }
  return {
    ok: true,
    json: async () => ({ task: {
      slug: secondSlug,
      status: "planned",
      title: "Second verified",
      detail: "Verified second task",
      display_markdown: "Verified second task",
      lifecycle_root: "collections/tonys-tasks",
    } }),
  };
};
const firstPending = selectTask(firstSlug);
const secondPending = selectTask(secondSlug);
assert(firstAborted === 1, `switch did not cancel obsolete read: ${firstAborted}`);
await secondPending;
await firstPending;
assert(state.selectedSlug === secondSlug, `obsolete read changed selection: ${state.selectedSlug}`);
assert(elements.detailTitle.textContent === "Second verified", "obsolete read overwrote verified detail");

globalThis.fetch = async (url, options = {}) => await new Promise((resolve, reject) => {
  options.signal?.addEventListener("abort", () => {
    const error = new Error("aborted");
    error.name = "AbortError";
    reject(error);
  }, { once: true });
});
const closePending = selectTask(firstSlug);
closeDetails();
assert(state.taskDetailReadController === null, "closing did not cancel and clear the active detail read");
await closePending;
assert(elements.detailPanel.getAttribute("aria-hidden") === "true", "cancelled read reopened a closed detail surface");
assert(state.selectedSlug === null && state.selectedKind === null, "closing left a selected task behind");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

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

    def test_verified_completion_transition_announces_command_confirmation_sweep(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
window.setTimeout = () => 1;
Date.now = () => 100000;
render = () => {};
selectTask = (slug) => { state.selectedKind = "task"; state.selectedSlug = slug; };
state.snapshot = {
  as_of: "2026-08-13",
  tasks: [{ slug: "tasks/complete-me", title: "Finish the canonical loop", status: "active" }],
  views: { inbox: [], today: [], completed: [], blocked: [] },
  goals: [],
  projects: [],
};
state.selectedKind = "task";
state.selectedSlug = "tasks/complete-me";
const calls = [];
globalThis.fetch = async (endpoint, options) => {
  calls.push({ endpoint, body: JSON.parse(options.body) });
  return { ok: true, json: async () => ({ receipt: { verified: true, task: {
    slug: "tasks/complete-me",
    title: "Finish the canonical loop",
    status: "completed",
  } } }) };
};
await moveBoardTask("tasks/complete-me", "completed");
assert(calls.length === 1, `expected one canonical status write, got ${calls.length}`);
assert(calls[0].body.status === "completed", "completion write not requested");
assert(Array.isArray(state.completionCelebrations), "completion celebration queue missing");
assert(state.completionCelebrations.length === 1, `expected one celebration, got ${state.completionCelebrations?.length}`);
assert(state.completionCelebrations[0].message === "Mission accomplished — Finish the canonical loop", state.completionCelebrations[0].message);
assert(elements.completionCelebrationRegion?.children.length === 1, "aria-live celebration region was not rendered");
assert(document.activeElement !== elements.completionCelebrationRegion, "celebration stole focus");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_completion_celebration_suppresses_stale_failed_same_status_and_off_preference(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
window.setTimeout = () => 1;
render = () => {};
selectTask = (slug) => { state.selectedKind = "task"; state.selectedSlug = slug; };
state.snapshot = {
  as_of: "2026-08-13",
  tasks: [{ slug: "tasks/quiet", title: "Quiet Task", status: "completed" }],
  views: { inbox: [], today: [], completed: [], blocked: [] },
  goals: [],
  projects: [],
};
globalThis.fetch = async () => {
  throw new Error("same-status completion should not write");
};
await moveBoardTask("tasks/quiet", "completed");
assert(!state.completionCelebrations?.length, "same-status completion celebrated");

state.snapshot.tasks = [{ slug: "tasks/background", title: "Background Task", status: "active" }];
reconcileVerifiedTask({ slug: "tasks/background", title: "Background Task", status: "completed" });
assert(!state.completionCelebrations?.length, "passive reconciliation celebrated");

state.snapshot.tasks = [{ slug: "tasks/fail", title: "Failed Task", status: "active" }];
globalThis.fetch = async () => ({
  ok: false,
  json: async () => ({ error: "canonical write failed", code: "partial_write", slug: "tasks/fail" }),
});
await moveBoardTask("tasks/fail", "completed");
assert(!state.completionCelebrations?.length, "failed write celebrated");

window.localStorage = { getItem: () => "off", setItem() {} };
state.boardMove = null;
state.snapshot.tasks = [{ slug: "tasks/off", title: "Off Task", status: "active" }];
globalThis.fetch = async () => ({ ok: true, json: async () => ({ receipt: { verified: true, task: {
  slug: "tasks/off",
  title: "Off Task",
  status: "completed",
} } }) });
await moveBoardTask("tasks/off", "completed");
assert(!state.completionCelebrations?.length, "off preference celebrated");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_task_edit_completion_celebrates_only_after_verified_same_slug_readback(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
window.setTimeout = () => 1;
render = () => {};
selectTask = (slug) => { state.selectedKind = "task"; state.selectedSlug = slug; };
loadTasks = async () => {};
loadAgentWork = async () => {};
state.snapshot = {
  as_of: "2026-08-13",
  tasks: [{ slug: "tasks/edit-complete", title: "Edit Complete", status: "active" }],
  views: { inbox: [], today: [], completed: [], blocked: [] },
  goals: [],
  projects: [],
};
state.taskEditorMode = "edit";
state.taskEditorSourceSlug = "tasks/edit-complete";
elements.taskEditorDialog.close = () => { elements.taskEditorDialog.open = false; };
elements.taskEditorTitle.value = "Edit Complete";
elements.taskEditorDetail.value = "";
elements.taskEditorPriority.value = "normal";
elements.taskEditorDue.value = "";
elements.taskEditorProject.value = "";
elements.taskEditorGoal.value = "";
elements.taskEditorStatus.value = "completed";
elements.taskEditorAssignee.value = "tony";
elements.taskEditorHandoffReason.value = "";
elements.taskTrackMetric.checked = false;
globalThis.fetch = async (endpoint, options) => {
  assert(endpoint === "/api/tasks/tasks%2Fedit-complete", endpoint);
  assert(options.method === "PATCH", options.method);
  const body = JSON.parse(options.body);
  assert(body.status === "completed", "edit did not request completed status");
  return { ok: true, json: async () => ({ receipt: { verified: true, task: {
    slug: "tasks/edit-complete",
    title: "Edit Complete",
    status: "completed",
  } } }) };
};
await submitTaskEditor({ preventDefault() {} });
assert(state.completionCelebrations.length === 1, `expected edit completion celebration, got ${state.completionCelebrations.length}`);
assert(state.completionCelebrations[0].message === "Mission accomplished — Edit Complete", state.completionCelebrations[0].message);
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_completion_celebration_respects_reduced_motion_and_full_cooldown(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
window.setTimeout = () => 1;
let now = 100000;
Date.now = () => now;
window.matchMedia = () => ({ matches: false });
recordCompletionCelebration({ slug: "tasks/one", title: "One", status: "completed" });
now += 1000;
recordCompletionCelebration({ slug: "tasks/two", title: "Two", status: "completed" });
assert(state.completionCelebrations[1].mode === "full", "first celebration did not use full mode");
assert(state.completionCelebrations[0].mode === "reduced", "rapid second celebration did not use reduced stacked mode");

state.completionCelebrations = [];
window.matchMedia = () => ({ matches: true });
now += 10000;
recordCompletionCelebration({ slug: "tasks/reduced", title: "Reduced", status: "completed" });
assert(state.completionCelebrations[0].mode === "reduced", "prefers-reduced-motion did not force reduced mode");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

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

    def test_goal_detail_keyboard_focus_and_exact_origin_restoration(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="goal-detail-title" tabindex="-1"', html)
        self.assertIn("button.dataset.slug = goal.slug", javascript)
        self.assertIn("selectGoal(goal.slug, button)", javascript)
        self.assertIn('...document.querySelectorAll(".goal-card")', javascript)
        self.assertIn("elements.goalDetailTitle.focus({ preventScroll: true })", javascript)

    def test_mobile_detail_sheet_is_modal_and_traps_keyboard_focus(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function syncMobileDetailModalState()", javascript)
        self.assertIn("surface.inert = isModal", javascript)
        self.assertIn('elements.detailPanel.setAttribute("aria-modal", "true")', javascript)
        self.assertIn("function trapMobileDetailFocus(event)", javascript)
        self.assertIn('event.key !== "Tab"', javascript)
        self.assertIn("elements.detailPanel.addEventListener(\"keydown\", trapMobileDetailFocus)", javascript)
        self.assertIn('window.addEventListener("resize", syncMobileDetailModalState)', javascript)

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

    def test_goal_creation_uses_exact_readback_before_stale_snapshot_display(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const goal = {
  slug: "goals/runtime-created",
  title: "Runtime created goal",
  status: "planned",
  outcome: "Outcome.",
  success_criteria: "Criteria.",
  target_day: "2026-09-30",
  strategy: "Strategy.",
  review_cadence: "weekly",
  constraints: "Constraints.",
  advanced_by: [],
};
state.snapshot = {
  tasks: [],
  goals: [],
  views: {},
  read_state: { status: "fresh", refreshing: false, stale: false },
};
state.goalEditorSlug = null;
state.activeView = "goals";
elements.newGoalTitle.value = goal.title;
elements.newGoalOutcome.value = goal.outcome;
elements.newGoalSuccess.value = goal.success_criteria;
elements.newGoalStrategy.value = goal.strategy;
elements.newGoalCadence.value = goal.review_cadence;
elements.newGoalConstraints.value = goal.constraints;
elements.newGoalTarget.value = "";
elements.newGoalDialog.close = () => { elements.newGoalDialog.closed = true; };
let requested = [];
globalThis.fetch = async (url, options = {}) => {
  requested.push({ url, method: options.method || "GET" });
  if (url === "/api/goals") {
    return { ok: true, json: async () => ({ goal, receipt: { verified: true, goal_slug: goal.slug } }) };
  }
  if (url === `/api/goals/${encodeURIComponent(goal.slug)}`) {
    return { ok: true, json: async () => ({ goal }) };
  }
  throw new Error(`unexpected fetch ${url}`);
};
loadTasks = async () => {
  state.snapshot = {
    tasks: [],
    goals: [],
    views: {},
    read_state: { status: "refreshing", refreshing: true, stale: true },
  };
};
render = () => {};
let selected = null;
selectGoal = (slug) => { selected = slug; };
showMutationStatus = (message, phase) => { state.lastMutation = { message, phase }; };

await submitNewGoal({ preventDefault() {} });

assert(requested.some((item) => item.url === `/api/goals/${encodeURIComponent(goal.slug)}` && item.method === "GET"), "exact goal readback was not requested");
assert(state.snapshot.goals.some((item) => item.slug === goal.slug), "verified goal was not inserted into stale task snapshot");
assert(selected === goal.slug, "created goal was not selected after exact readback");
assert(elements.newGoalDialog.closed === true, "goal dialog was not closed after verified readback");
assert(state.lastMutation?.phase === "success", `unexpected mutation phase: ${state.lastMutation?.phase}`);
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

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
        self.assertIn("function setAppShellModalIsolation(isModal)", javascript)
        self.assertIn('elements.appShell.setAttribute("aria-hidden", isModal ? "true" : "false")', javascript)
        self.assertIn("elements.appShell.inert = isModal", javascript)
        self.assertIn("elements.aboutClose.focus();\n    setAppShellModalIsolation(true);", javascript)
        self.assertIn("if (elements.aboutDialog.open) setAppShellModalIsolation(true);", javascript)
        self.assertIn('event.key === "Escape"', javascript)
        self.assertIn("release-history", javascript)
        about_dialog = html[html.index('id="about-dialog"') : html.index('<div class="toast', html.index('id="about-dialog"'))]
        self.assertNotIn('id="completion-celebration-preference"', about_dialog)

    def test_settings_view_follows_info_button_and_owns_completion_preference(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        footer = html[html.index('<div class="mission-art-footer">'):html.index('</main>')]
        logs_button = footer.index('id="logs-button"')
        about_button = footer.index('id="about-button"')
        settings_button = footer.index('id="settings-button"')
        self.assertLess(logs_button, about_button)
        self.assertLess(about_button, settings_button)
        self.assertIn('aria-label="Settings"', footer)
        self.assertIn('data-tooltip="Settings"', footer)
        self.assertEqual(html.count('id="completion-celebration-preference"'), 0)
        self.assertEqual(javascript.count('select.id = "completion-celebration-preference"'), 1)
        self.assertIn('settings: {', javascript)
        self.assertIn('title: "Settings"', javascript)
        self.assertIn('function renderSettingsView()', javascript)
        self.assertIn('elements.settingsButton.addEventListener("click", () => setView("settings"))', javascript)
        self.assertIn('setCompletionCelebrationPreference(select.value)', javascript)
        self.assertIn('elements.settingsButton.classList.toggle("is-active", settingsActive)', javascript)
        self.assertIn('elements.settingsButton.setAttribute("aria-current", settingsActive ? "page" : "false")', javascript)
        self.assertIn('elements.settingsButton.setAttribute("aria-pressed", settingsActive ? "true" : "false")', javascript)

        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
FakeElement.prototype.addEventListener = function(name, callback) {
  this.events = this.events || {};
  this.events[name] = callback;
};
let stored = "reduced";
window.localStorage = {
  getItem: (key) => key === COMPLETION_CELEBRATION_PREFERENCE_KEY ? stored : null,
  setItem: (key, value) => { if (key === COMPLETION_CELEBRATION_PREFERENCE_KEY) stored = value; },
};
const view = renderSettingsView();
const find = (element, predicate) => {
  if (predicate(element)) return element;
  for (const child of element.children || []) {
    const match = find(child, predicate);
    if (match) return match;
  }
  return null;
};
const select = find(view, (element) => element.id === "completion-celebration-preference");
assert(select, "Settings view did not render the completion preference");
assert(select.value === "reduced", `stored preference was reset: ${select.value}`);
select.value = "off";
select.events.change();
assert(stored === "off", `changed preference was not persisted: ${stored}`);
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        isolation_result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const appShell = elements.appShell;
setAppShellModalIsolation(true);
assert(appShell.inert === true, "app shell was not inert while a modal is open");
assert(appShell.getAttribute("aria-hidden") === "true", "app shell stayed exposed to assistive tech");
setAppShellModalIsolation(false);
assert(appShell.inert === false, "app shell stayed inert after modal close");
assert(appShell.getAttribute("aria-hidden") === "false", "app shell stayed hidden after modal close");
"""
        )
        self.assertEqual(isolation_result.returncode, 0, isolation_result.stderr)

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
        self.assertIn("selectTask(task.slug, task, button)", card)
        self.assertIn("function selectTask(\n  slug,\n  taskFallback = null", javascript)
        self.assertIn("const knownTask = findTaskBySlug(slug)", javascript)
        self.assertIn("const task = knownTask || taskFallback", javascript)
        self.assertIn("selectTaskWithCanonicalRead(slug, returnFocus, task, { focusTarget, todoSlug })", javascript)

    def test_all_fallback_task_selections_require_one_exact_detail_hydration(self) -> None:
        result = run_app_runtime_probe(
            r'''
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const slug = "tasks/fad23bf2-571f-4db0-b9f5-07ab52ae8620";
const fallback = {
  slug,
  title: "Inline fallback",
  summary: "Inline fallback",
  detail: "Fallback detail",
  status: "active",
  priority: "normal",
  due_day: "2026-08-10",
  todos: [],
  open_todos: [],
  artifacts: [],
};
const exact = {
  ...fallback,
  title: "Exact canonical Task",
  detail: "Canonical detail",
  display_markdown: "# Exact canonical Task\n\n## 详情\n\nCanonical detail",
};
state.snapshot = {
  as_of: "2026-08-25",
  tasks: [],
  goals: [],
  today: { in_progress: [], todays_actions: [], overdue: [], waiting_and_blocked: [] },
  views: { inbox: [], completed: [], blocked: [], projects: [] },
};
state.agentTasks = [fallback];
state.agents = [];
const requested = [];
fetch = async (url) => {
  requested.push(String(url));
  if (String(url) === `/api/tasks/${encodeURIComponent(slug)}`) {
    return { ok: true, json: async () => ({ task: exact }) };
  }
  return { ok: true, json: async () => ({ artifacts: [], events: [], entries: [] }) };
};
render = () => {};
await selectTask(slug, fallback, new FakeElement("button"));
assert(
  requested.filter((url) => url === `/api/tasks/${encodeURIComponent(slug)}`).length === 1,
  `fallback selection did not perform exactly one exact hydration: ${requested.join(",")}`,
);
assert(state.selectedSlug === slug && state.selectedKind === "task", "exact Task was not selected");
assert(state.agentTasks[0].display_markdown === exact.display_markdown, "exact projection did not replace inline fallback");
'''
        )
        self.assertEqual(result.returncode, 0, result.stderr)

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

    def test_inbox_proposals_only_render_actionable_pending_records(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="proposal-decision-timeline"', html)
        self.assertIn("function isActionableProposal(proposal)", javascript)
        self.assertIn('proposal.status === "proposed"', javascript)
        self.assertIn("proposal.proposal_decision", javascript)
        self.assertIn("state.proposals.filter(isActionableProposal)", javascript)
        self.assertNotIn('"Recent decisions"', javascript)
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
        self.assertIn('focusTaskDetailTarget(task, { focusTarget, todoSlug })', body)
        self.assertIn('elements.detailTitle.focus({ preventScroll: true })', javascript)

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
        self.assertIn("focusTaskDetailTarget(task, { focusTarget, todoSlug })", select_task)
        self.assertIn("elements.detailTitle.focus({ preventScroll: true })", javascript)
        self.assertIn('document.querySelectorAll(".proposal-card")', close_details)
        self.assertIn("candidate.dataset.slug === anchor.slug", close_details)
        self.assertIn("detailFocusReturnTarget(anchor)?.focus({ preventScroll: true })", close_details)

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
        self.assertIn("candidate.dataset.slug === anchor.slug", close_details)
        self.assertIn("detailFocusReturnTarget(anchor)?.focus({ preventScroll: true })", close_details)

    def test_board_and_goal_task_details_restore_exact_origin_after_rerender(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        board_card = javascript[
            javascript.index("function boardCard")
            : javascript.index("function ownerBadge")
        ]
        goal_links = javascript[
            javascript.index("function goalTaskLinks")
            : javascript.index("function renderGoalRelationshipTasks")
        ]
        close_details = javascript[
            javascript.index("function closeDetails")
            : javascript.index("async function saveTaskGoal")
        ]

        self.assertIn("button.dataset.slug = task.slug", board_card)
        self.assertIn(
            'button.addEventListener("click", () => selectTask(task.slug, null, button))',
            board_card,
        )
        self.assertIn("button.dataset.slug = task.slug", goal_links)
        self.assertIn("selectTask(task.slug, null, button)", goal_links)
        self.assertIn("state.goalTaskReturn", goal_links)
        self.assertIn('state.selectedKind === "goal"', goal_links)
        self.assertIn('document.querySelectorAll(".board-card-open")', close_details)
        self.assertIn('document.querySelectorAll(".goal-task-link")', close_details)
        self.assertIn('state.selectedKind === "task"', close_details)
        self.assertIn("selectGoal(goalTaskReturn.goalSlug)", close_details)
        self.assertIn("candidate.dataset.slug === goalTaskReturn.taskSlug", close_details)

        probe = r'''
const slug = "tasks/focus-origin";
for (const selector of [".board-card-open", ".goal-task-link"]) {
  const replacement = new FakeElement("button");
  replacement.dataset.slug = slug;
  document.querySelectorAll = (candidate) => candidate === selector ? [replacement] : [];
  const resolved = detailFocusReturnTarget({ element: new FakeElement("button"), slug });
  if (resolved !== replacement) throw new Error(`missing exact rerender target for ${selector}`);
}
const parentAnchor = { element: new FakeElement("button"), slug: "goals/parent" };
const returnedTask = new FakeElement("button");
returnedTask.dataset.slug = slug;
document.querySelectorAll = (candidate) => candidate === ".goal-task-link" ? [returnedTask] : [];
state.selectedKind = "task";
state.selectedSlug = slug;
state.detailReturnFocus = { element: new FakeElement("button"), slug };
state.goalTaskReturn = {
  goalSlug: "goals/parent",
  taskSlug: slug,
  element: state.detailReturnFocus.element,
  detailReturnFocus: parentAnchor,
};
selectGoal = (goalSlug) => {
  state.selectedKind = "goal";
  state.selectedSlug = goalSlug;
};
closeDetails();
if (state.selectedKind !== "goal" || state.selectedSlug !== "goals/parent") {
  throw new Error("Goal parent detail was not restored");
}
if (!returnedTask.focused) throw new Error("Goal Task origin did not regain focus");
if (state.detailReturnFocus !== parentAnchor) throw new Error("outer Goal return anchor was not preserved");
'''
        result = run_app_runtime_probe(probe)
        self.assertEqual(result.returncode, 0, result.stderr)

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

        self.assertIn('href="/styles.css?v=0.0.89"', html)
        self.assertIn('src="/app.js?v=0.0.89"', html)

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

    def test_todo_user_authored_text_preserves_newline_boundaries(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        todo_card = javascript[
            javascript.index("function todoCard(todo)")
            : javascript.index("function renderTaskHandoff")
        ]

        for expected in (
            '"task-todo-title-copy"',
            '"task-todo-detail-copy"',
            '"task-todo-comment-copy"',
        ):
            self.assertIn(expected, todo_card)
        self.assertIn(".task-todo-title-copy", css)
        self.assertIn(".task-todo-detail-copy", css)
        self.assertIn(".task-todo-comment-copy", css)
        self.assertIn("white-space: pre-wrap", css)
        self.assertIn("overflow-wrap: anywhere", css)

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

    def test_todo_edit_supports_verified_save_done_and_complete_task_actions(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("Save & Mark Done", javascript)
        self.assertIn("Save & Complete Task", javascript)
        self.assertIn("function canUseCombinedTodoActions(todo)", javascript)
        self.assertIn("async function performTodoEditAction", javascript)

        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
window.setTimeout = () => 1;
const form = new FakeElement("form");
const button = new FakeElement("button");
button.textContent = "Save & Complete Task";
button.closest = (selector) => selector === "form" ? form : null;
form.querySelectorAll = () => [button];
state.snapshot = {
  tasks: [{
    slug: "tasks/parent",
    title: "Parent Task",
    status: "active",
    todos: [{
      slug: "todos/one",
      parent_task: "tasks/parent",
      text: "Original TODO",
      detail: "Original detail",
      status: "not_done",
      updated_at: "v1",
    }],
  }],
  views: { inbox: [], today: [], completed: [], blocked: [] },
  goals: [],
  projects: [],
};
state.selectedKind = "task";
state.selectedSlug = "tasks/parent";
elements.taskTodoError = new FakeElement("p");
elements.taskTodoList = new FakeElement("div");
elements.taskTodoEmpty = new FakeElement("p");
elements.taskTodoAddForm = new FakeElement("form");
elements.taskTodoAddToggle = new FakeElement("button");
elements.taskTodoLoading = new FakeElement("p");
elements.taskTodoShowCompleted = new FakeElement("input");
const calls = [];
globalThis.fetch = async (endpoint, options) => {
  calls.push({ endpoint, body: JSON.parse(options.body) });
  if (endpoint === "/api/todos/todos%2Fone" && options.method === "PATCH") {
    return { ok: true, json: async () => ({ receipt: { verified: true, todo: {
      slug: "todos/one",
      parent_task: "tasks/parent",
      text: "Edited TODO",
      detail: "Edited detail",
      status: "not_done",
      updated_at: "v2",
    } } }) };
  }
  if (endpoint === "/api/todos/todos%2Fone/status" && options.method === "PATCH") {
    assert(calls[1].body.expected_updated_at === "v2", "mark-done did not use edited TODO readback version");
    return { ok: true, json: async () => ({ receipt: { verified: true, todo: {
      slug: "todos/one",
      parent_task: "tasks/parent",
      text: "Edited TODO",
      detail: "Edited detail",
      status: "done",
      updated_at: "v3",
    } } }) };
  }
  if (endpoint === "/api/tasks/tasks%2Fparent/status" && options.method === "PATCH") {
    return { ok: true, json: async () => ({ receipt: { verified: true, task: {
      slug: "tasks/parent",
      title: "Parent Task",
      status: "completed",
    } } }) };
  }
  throw new Error(`unexpected ${endpoint}`);
};
await performTodoEditAction(
  state.snapshot.tasks[0].todos[0],
  "Edited TODO",
  "Edited detail",
  button,
  "complete_task",
);
assert(calls.length === 3, `expected 3 verified writes, got ${calls.length}`);
assert(calls[0].body.text === "Edited TODO", "edit body was not sent");
assert(calls[1].body.status === "done", "TODO was not marked done");
assert(calls[2].body.status === "completed", "Task was not completed");
const task = findTaskBySlug("tasks/parent");
assert(task.status === "completed", `task status=${task.status}`);
assert(task.todos[0].status === "done", `todo status=${task.todos[0].status}`);
assert(button.disabled === false, "button stayed disabled");
assert(state.completionCelebrations.length === 1, `expected TODO completion celebration, got ${state.completionCelebrations.length}`);
assert(state.completionCelebrations[0].message === "Mission accomplished — Parent Task", state.completionCelebrations[0].message);
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_todo_combined_action_reports_verified_partial_failure_without_claiming_completion(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const form = new FakeElement("form");
const button = new FakeElement("button");
button.textContent = "Save & Complete Task";
button.closest = (selector) => selector === "form" ? form : null;
form.querySelectorAll = () => [button];
state.snapshot = {
  tasks: [{
    slug: "tasks/parent",
    title: "Parent Task",
    status: "active",
    todos: [{
      slug: "todos/one",
      parent_task: "tasks/parent",
      text: "Original TODO",
      detail: "Original detail",
      status: "not_done",
      updated_at: "v1",
    }],
  }],
  views: { inbox: [], today: [], completed: [], blocked: [] },
  goals: [],
  projects: [],
};
state.selectedKind = "task";
state.selectedSlug = "tasks/parent";
elements.taskTodoError = new FakeElement("p");
elements.taskTodoList = new FakeElement("div");
elements.taskTodoEmpty = new FakeElement("p");
elements.taskTodoAddForm = new FakeElement("form");
elements.taskTodoAddToggle = new FakeElement("button");
elements.taskTodoLoading = new FakeElement("p");
elements.taskTodoShowCompleted = new FakeElement("input");
globalThis.fetch = async (endpoint, options) => {
  if (endpoint === "/api/todos/todos%2Fone" && options.method === "PATCH") {
    return { ok: true, json: async () => ({ receipt: { verified: true, todo: {
      slug: "todos/one", parent_task: "tasks/parent", text: "Edited TODO", detail: "Edited detail", status: "not_done", updated_at: "v2",
    } } }) };
  }
  if (endpoint === "/api/todos/todos%2Fone/status" && options.method === "PATCH") {
    return { ok: true, json: async () => ({ receipt: { verified: true, todo: {
      slug: "todos/one", parent_task: "tasks/parent", text: "Edited TODO", detail: "Edited detail", status: "done", updated_at: "v3",
    } } }) };
  }
  if (endpoint === "/api/tasks/tasks%2Fparent/status" && options.method === "PATCH") {
    return { ok: false, json: async () => ({ error: "Task status readback failed.", code: "ambiguous_readback" }) };
  }
  throw new Error(`unexpected ${endpoint}`);
};
await performTodoEditAction(
  state.snapshot.tasks[0].todos[0],
  "Edited TODO",
  "Edited detail",
  button,
  "complete_task",
);
const message = elements.taskTodoError.textContent || "";
assert(message.includes("TODO edits were verified"), message);
assert(message.includes("TODO was marked Done"), message);
assert(message.includes("Task completion was not verified"), message);
assert(findTaskBySlug("tasks/parent").status === "active", "task was locally completed despite failed readback");
assert(findTaskBySlug("tasks/parent").todos[0].status === "done", "verified TODO done state was not retained");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

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
        handoff_renderer = javascript[
            javascript.index("function renderTaskHandoff")
            : javascript.index("function renderTaskTodos")
        ]
        self.assertNotIn("waiting for Tony", handoff_renderer)

    def test_task_todo_renderer_surfaces_bounded_canonical_todo_issues(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        renderer = javascript[
            javascript.index("function renderTaskTodos")
            : javascript.index("function todoErrorMessage")
        ]

        self.assertIn("task.todo_issues", renderer)
        self.assertIn("canonical TODO list is unavailable", renderer)
        self.assertIn("elements.taskTodoError.classList.remove", renderer)

    def test_task_detail_surfaces_dispatcher_handoff_attention_without_domain_handoff(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        renderer = javascript[
            javascript.index("function renderTaskHandoff")
            : javascript.index("function renderTaskTodos")
        ]

        self.assertIn("task?.dispatcher_handoff", renderer)
        self.assertIn("Verified Agent handoff needs system review", renderer)
        self.assertIn("Latest dispatcher status:", renderer)
        self.assertIn("Inspect Handoff History for execution recovery evidence", renderer)

    def test_task_todo_add_form_is_read_only_until_explicit_plus_action(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn(
            'id="task-todo-add-toggle" type="button" aria-label="Add a TODO"',
            html,
        )
        self.assertIn('data-tooltip="Add a TODO"', html)
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
        self.assertIn('todos.length ? "No open TODOs." : "No TODO yet"', javascript)

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
        self.assertNotIn('view === "agent-work" || view === "handoff-log"', javascript)

    def test_verified_todo_mutation_receipt_updates_ui_without_duplicate_read(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        create = javascript[javascript.index("async function createTaskTodo") : javascript.index("async function editTaskTodo")]
        edit = javascript[javascript.index("async function performTodoEditAction") : javascript.index("async function commentOnTodo")]
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
        agent_work = javascript[javascript.index("function renderAgentWorkView") : javascript.index("function goalCard")]

        self.assertIn("function todoSummary(task)", javascript)
        self.assertIn("todoSummary(task)", task_row)
        self.assertIn("todoSummary(task)", board)
        self.assertIn("todoSummary(task)", calendar)
        self.assertIn("open_todos", agent_work)
        self.assertIn("TODO", agent_work)

    def test_calendar_has_default_on_ical_events_filter(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("showIcalEvents: true", javascript)
        self.assertIn("function calendarEventsFilter", javascript)
        self.assertIn('"Show iCal Events"', javascript)
        self.assertIn("input.checked = state.showIcalEvents", javascript)
        self.assertIn("Connect Calendar", javascript)
        self.assertIn('"Manage"', javascript)
        self.assertIn('fetch("/api/ical-access", { method: "POST"', javascript)
        self.assertIn('fetch("/api/ical-calendars"', javascript)
        self.assertIn('fetch("/api/ical-preferences"', javascript)
        self.assertIn("Calendar permission was not granted", javascript)
        self.assertIn("Local Calendar is unavailable", javascript)
        self.assertIn("icalEventsForDay", javascript)
        self.assertIn("Full Access to Calendar", html)

    def test_calendar_toolbar_keeps_navigation_and_ical_on_one_desktop_row_with_detail_open(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        week_view = javascript[
            javascript.index("function renderWeekView")
            : javascript.index("function calendarEventsFilter")
        ]
        month_view = javascript[
            javascript.index("function renderMonthCalendar")
            : javascript.index("function renderCalendarView")
        ]

        for renderer in (week_view, month_view):
            self.assertIn('node("div", "week-controls calendar-toolbar")', renderer)
            self.assertIn('node("div", "calendar-toolbar-primary")', renderer)
            self.assertIn('node("div", "calendar-nav-controls")', renderer)
            self.assertLess(
                renderer.index("calendar-toolbar-primary"),
                renderer.index("calendar-nav-controls"),
            )
            self.assertLess(
                renderer.index("calendar-nav-controls"),
                renderer.index("calendarEventsFilter()"),
            )

        self.assertIn(".calendar-toolbar", css)
        self.assertIn(".calendar-toolbar-primary", css)
        self.assertIn(".calendar-nav-controls", css)
        desktop_detail = css[
            css.index('.app-shell:has(.detail-panel[aria-hidden="false"]) .calendar-toolbar')
            : css.index(".week-grid")
        ]
        self.assertIn("flex-wrap: nowrap", desktop_detail)
        self.assertIn("min-width: 0", desktop_detail)
        self.assertIn(".calendar-events-filter", desktop_detail)
        self.assertIn("margin-left: auto", desktop_detail)
        mobile = css[css.index("@media (max-width: 760px)") :]
        self.assertIn(".calendar-toolbar", mobile)
        self.assertIn("flex-wrap: wrap", mobile)
        self.assertIn(".calendar-events-filter", mobile)

    def test_calendar_picker_is_compact_and_saves_with_verified_feedback(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="calendar-picker-saving"', html)
        self.assertIn('id="calendar-picker-submit"', html)
        self.assertIn("Saving calendar selection…", html)
        self.assertIn('showToast("Calendar selection saved and verified.")', javascript)
        self.assertIn("await loadCalendarPicker()", javascript)
        self.assertIn("Calendar selection readback did not match", javascript)
        self.assertNotIn("state.calendarPreferencesNotice = `Calendar selection saved and verified.", javascript)
        self.assertIn(".calendar-picker-option input", css)
        self.assertIn("width: 14px", css)
        self.assertIn("grid-template-columns: 14px minmax(0, 1fr)", css)
        self.assertIn("flex: 1 0 100%", css)

    def test_gbrain_communication_statuses_use_dark_hud_treatment(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        for element_id in (
            "task-handoff-error",
            "task-todo-loading",
            "task-todo-error",
            "calendar-access-error",
            "calendar-picker-saving",
            "calendar-picker-error",
        ):
            self.assertIn(f'id="{element_id}"', html)
            fragment = html[
                max(0, html.index(f'id="{element_id}"') - 160)
                : html.index(f'id="{element_id}"') + 160
            ]
            self.assertIn("mission-status-hud", fragment)

        self.assertIn(".mission-status-hud", css)
        hud_start = css.index(".mission-status-hud")
        hud = css[hud_start : css.index(".calendar-events-filter", hud_start)]
        self.assertIn("rgba(2, 8, 22", hud)
        self.assertIn("color: var(--ink)", hud)
        self.assertIn("border: 1px solid", hud)
        self.assertIn('.mission-status-hud[role="alert"]', hud)
        self.assertIn('.mission-status-hud[role="status"]', hud)
        self.assertNotIn("background: white", hud)

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
        self.assertIn('"Reconnect"', calendar_filter)
        self.assertIn("state.icalRange = range", event_loader)
        self.assertIn('if (state.icalStatus !== "authorized") return', event_loader)
        self.assertLess(event_loader.index("state.icalRange = range"), event_loader.index('state.icalStatus = "unavailable"'))

    def test_calendar_connection_and_selection_restore_from_local_readback(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        calendar_filter = javascript[
            javascript.index("function calendarEventsFilter") : javascript.index("function openCalendarAccessDialog")
        ]
        set_view = javascript[
            javascript.index("function setView(view)") : javascript.index("function setConnection")
        ]

        self.assertIn("icalConnectionLoaded: false", javascript)
        self.assertIn("function loadCalendarConnectionState()", javascript)
        self.assertIn('fetch("/api/ical-calendars"', javascript)
        self.assertIn('view === "week"', set_view)
        self.assertIn("loadCalendarConnectionState()", set_view)
        self.assertIn('"Reconnect"', calendar_filter)
        self.assertNotIn('"Reauthorize Calendar"', calendar_filter)
        self.assertIn("Checking Calendar access…", calendar_filter)
        self.assertIn("loadCalendarConnectionState();", javascript[-1200:])

    def test_connected_calendar_uses_manage_without_redundant_reconnect(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        calendar_filter = javascript[
            javascript.index("function calendarEventsFilter") : javascript.index("async function reconnectCalendar")
        ]
        authorized_branch = calendar_filter[
            calendar_filter.index("} else {") :
        ]

        self.assertIn('node("button", "secondary-button", "Manage")', authorized_branch)
        self.assertNotIn('node("button", "secondary-button", "Reconnect")', authorized_branch)
        self.assertNotIn("selected read-only calendar", authorized_branch)
        self.assertNotIn("calendar-preferences-notice", authorized_branch)
        self.assertIn('state.icalStatus !== "authorized"', calendar_filter)
        self.assertIn('"Reconnect"', calendar_filter)

    def test_calendar_events_show_time_and_open_read_only_details(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("function calendarEventItem(event, day)", javascript)
        self.assertIn("function calendarEventTimeLabel(event, day)", javascript)
        self.assertIn('"All day"', javascript)
        self.assertIn('selectCalendarEvent(event, origin)', javascript)
        self.assertIn('state.selectedKind = "ical-event"', javascript)
        self.assertIn('id="calendar-event-detail"', html)
        self.assertIn('id="calendar-event-detail-title"', html)
        self.assertIn(
            "This is a read-only view. To make changes, open the Calendar app.",
            html,
        )
        self.assertIn(".ical-event-time", css)
        self.assertIn(".calendar-event-detail-copy", css)

    def test_background_task_refresh_keeps_verified_ui_and_connection_stable(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        loader = javascript[
            javascript.index("async function performTaskLoad(reason)") : javascript.index("function loadTasks(")
        ]

        self.assertIn("const hasVerifiedSnapshot = Boolean(previousSnapshot)", loader)
        self.assertIn("state.loading = !hasVerifiedSnapshot", loader)
        self.assertIn('if (!hasVerifiedSnapshot)', loader)
        self.assertIn('reason === "manual"', loader)
        self.assertIn("state.tasksLoadPromise", javascript)

    def test_mobile_handoff_log_is_not_rerendered_by_background_task_reads(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
window.innerWidth = 390;
window.matchMedia = (query) => ({ matches: query.includes("760px") });
state.activeView = "agent-work";
state.agentHandoffHistoryOpen = true;
state.snapshot = null;
state.tasksReadState = null;
state.agents = [{ slug: "agents/tammy", name: "Tammy" }];
state.handoffLogEvents = [{
  sequence: 1,
  task_slug: "tasks/runtime-mobile-handoff",
  agent_slug: "agents/tammy",
  event_type: "handoff_queued",
  status: "queued",
  occurred_at: "2026-08-04T18:00:00Z",
  summary: "Stable mobile handoff history event.",
  correlation_id: "correlation-runtime-mobile",
}];
state.handoffLogTotal = 1;
state.handoffLogNextSequence = null;
render();
const originalSurface = elements.viewSurface.children[0];
let release;
globalThis.fetch = () => new Promise((resolve) => {
  release = () => resolve({
    ok: true,
    status: 200,
    json: async () => ({
      as_of: "2026-08-04",
      tasks: [], goals: [], views: { inbox: [], completed: [] },
      read_state: { status: "fresh", refreshing: false, last_valid_at: 1785870000 },
    }),
  });
});
const pending = performTaskLoad("initial");
await Promise.resolve();
assert(elements.viewSurface.children[0] === originalSurface, "task read replaced Agents handoff history while pending");
release();
await pending;
assert(state.activeView === "agent-work", "task read changed the active Agents view");
assert(elements.viewSurface.children[0] === originalSurface, "task read replaced Agents handoff history after completion");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_task_project_and_goal_updates_share_accessible_hud_feedback(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="toast" role="status" aria-live="polite"', html)
        self.assertIn("function showMutationStatus(message, phase", javascript)
        self.assertIn('showMutationStatus(projectStatus, "pending"', javascript)
        self.assertIn('showMutationStatus(goalStatus, "pending"', javascript)
        self.assertIn('showMutationStatus(taskStatus, "pending"', javascript)
        self.assertIn(".toast.mutation-status.is-pending", css)
        self.assertIn(".toast.mutation-status.is-success", css)
        self.assertIn(".toast.mutation-status.is-error", css)
        self.assertNotIn("background: white", css[css.index(".toast {") :])

    def test_all_tasks_and_search_share_one_rolling_date_scope(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('data-view="all"', html)
        self.assertIn('<span class="nav-label">All Tasks</span>', html)
        self.assertIn("allTaskSearch: \"\"", javascript)
        self.assertIn("showAllTaskDates: false", javascript)
        self.assertIn("function filteredAllTasks()", javascript)
        self.assertIn("task.in_default_display_window", javascript)
        self.assertIn("task.scheduled_day || task.due_day", javascript)
        self.assertIn("function renderAllTasksView()", javascript)
        self.assertIn('input.type = "search"', javascript)
        self.assertIn('input.setAttribute("aria-label", "Search tasks")', javascript)
        self.assertIn('toggle.setAttribute("aria-label", "Show tasks outside the default date range")', javascript)
        self.assertIn("matching tasks outside the default range", javascript)
        self.assertIn("renderAllTaskResults", javascript)
        self.assertIn(".all-tasks-toolbar", css)
        self.assertIn(".all-tasks-filter-notice", css)

    def test_all_tasks_filter_is_read_only_and_count_uses_the_filtered_set(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        filter_body = javascript[
            javascript.index("function filteredAllTasks()")
            : javascript.index("function renderAllTasksView()")
        ]
        renderer = javascript[
            javascript.index("function renderAllTasksView()")
            : javascript.index("function renderWeekView()")
        ]

        self.assertNotIn("fetch(", filter_body)
        self.assertNotIn("fetch(", renderer)
        self.assertNotIn("method:", filter_body)
        self.assertIn("all: filteredAllTasks().length", javascript)
        self.assertIn('all: count === 1 ? "task shown" : "tasks shown"', javascript)
        self.assertIn("state.allTaskSearch = input.value", renderer)
        self.assertIn("state.showAllTaskDates = toggle.checked", renderer)

    def test_artifacts_and_all_tasks_use_newest_updated_order_and_all_tasks_show_status(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        task_row = javascript[
            javascript.index("function taskRow") : javascript.index("function section")
        ]
        all_tasks = javascript[
            javascript.index("function allTasksMatchingSearch") :
            javascript.index("function renderAllTasksView")
        ]
        artifact_view = javascript[
            javascript.index("function buildArtifactHierarchy") :
            javascript.index("async function loadArtifacts")
        ]

        self.assertIn("function compareNewestUpdated", javascript)
        self.assertIn("left.updated_at || left.created_at", javascript)
        self.assertIn("right.updated_at || right.created_at", javascript)
        self.assertIn("showStatus = false", task_row)
        self.assertIn('node("span", `task-status-badge ${status}`', task_row)
        self.assertIn("showStatus: true", all_tasks)
        self.assertIn(".sort(compareNewestUpdated)", all_tasks)
        self.assertIn(".sort(compareNewestUpdated)", artifact_view)
        self.assertIn(".task-status-badge", css)

        result = run_app_runtime_probe(
            r'''
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const items = [
  { slug: "tasks/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", title: "B", created_at: "2026-08-03T10:00:00Z", updated_at: null },
  { slug: "tasks/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", title: "A", created_at: "2026-08-01T10:00:00Z", updated_at: "2026-08-04T10:00:00Z" },
  { slug: "tasks/cccccccc-cccc-4ccc-8ccc-cccccccccccc", title: "C", created_at: "2026-08-04T10:00:00Z", updated_at: "2026-08-04T10:00:00Z" },
];
const sorted = [...items].sort(compareNewestUpdated);
assert(sorted.map((item) => item.slug).join(",") === `${items[1].slug},${items[2].slug},${items[0].slug}`, "newest-updated order or stable title fallback is wrong");

state.allTaskSearch = "";
state.snapshot = { tasks: items.map((item, index) => ({
  ...item,
  summary: item.title,
  detail: "",
  status: index === 0 ? "blocked" : "planned",
  priority: "normal",
  inbox: false,
  lifecycle_root: "collections/tonys-tasks",
  todos: [],
})), goals: [], task_display_scope: null };
state.projects = [];
assert(allTasksMatchingSearch().map((item) => item.slug).join(",") === sorted.map((item) => item.slug).join(","), "All Tasks did not use newest-updated order");
const row = taskRow(state.snapshot.tasks[0], { displayRelevantDate: true, showStatus: true });
const statusBadge = row.children[0].children[2].children.find((child) => child.className?.includes?.("task-status-badge"));
assert(statusBadge && statusBadge.textContent === "Blocked", "visible canonical task status is missing");
'''
        )
        self.assertEqual(result.returncode, 0, result.stderr)

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
            javascript.index("function renderAgentWorkView") : javascript.index("function goalCard")
        ]

        self.assertIn('actionIcon("⋯", `Open ${agent.name} profile`', agent_work)
        self.assertIn("openAgentProfile(agent)", agent_work)
        self.assertNotIn("node(\"details\"", agent_work)
        self.assertNotIn("Open Agent Profile", agent_work)
        self.assertIn('node("h3", "", "Current work")', agent_work)
        self.assertIn("No authorized work yet", agent_work)
        self.assertIn("No current task or open TODO recorded.", agent_work)

    def test_agents_cards_show_loading_instead_of_false_empty_during_cold_agent_work_read(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const flatten = (node) => {
  if (!node) return "";
  const own = typeof node.textContent === "string" ? node.textContent : "";
  return own + (node.children || []).map(flatten).join("");
};
state.agents = [{
  slug: "agents/toddy",
  name: "Toddy",
  avatar: { kind: "initials", value: "TO" },
  default_goal_slugs: [],
  runtime: "codex",
}];
state.agentTasks = [];
state.agentWorkLoading = true;
state.agentWorkLoaded = false;
state.agentWorkError = "";
state.goalExecution = null;
state.snapshot = {
  as_of: "2026-08-25",
  tasks: [],
  goals: [],
  today: { in_progress: [], todays_actions: [], overdue: [], waiting_and_blocked: [] },
  views: { inbox: [], completed: [], blocked: [], projects: [] },
};
state.delegations = [];
state.handoffLogEvents = [];
state.handoffLogLoading = false;
state.handoffLogError = "";
const text = flatten(renderAgentWorkView());
assert(text.includes("Reading typed agent task collections"), `missing loading copy: ${text}`);
assert(!text.includes("No authorized work yet"), `false empty authorized copy rendered: ${text}`);
assert(!text.includes("No current task or open TODO recorded"), `false empty TODO copy rendered: ${text}`);
assert(!text.includes("No verified completion yet"), `false empty completion copy rendered: ${text}`);
assert(inContextCountLabel("agent-work") === "Reading Agent Work…", `false count label rendered: ${inContextCountLabel("agent-work")}`);
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_agent_card_surfaces_terminal_dispatcher_status_as_attention(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const flatten = (node) => {
  if (!node) return "";
  const own = typeof node.textContent === "string" ? node.textContent : "";
  return own + (node.children || []).map(flatten).join("");
};
state.snapshot = {
  as_of: "2026-08-25",
  tasks: [],
  goals: [],
  today: { in_progress: [], todays_actions: [], overdue: [], waiting_and_blocked: [] },
  views: { inbox: [], completed: [], blocked: [], projects: [] },
};
state.agents = [{
  slug: "agents/tammy",
  name: "Tammy",
  avatar: { kind: "initials", value: "TA" },
  default_goal_slugs: [],
  runtime: "codex",
}];
state.agentTasks = [{
  slug: "tasks/terminal-dispatcher",
  title: "Provide a weekly Top 10 undervalued stocks research brief",
  status: "active",
  owner: { slug: "agents/tammy", name: "Tammy", avatar: { kind: "initials", value: "TA" } },
  owner_agent: "agents/tammy",
  open_todos: [],
  dispatcher_handoff: { status: "suppressed" },
  updated_at: "2026-08-25T12:00:00Z",
}];
state.agentWorkLoaded = true;
state.agentWorkLoading = false;
state.goalExecution = null;
state.delegations = [];
state.handoffLogEvents = [];
state.handoffLogLoading = false;
state.handoffLogError = "";
const text = flatten(renderAgentWorkView());
assert(text.includes("Needs attention"), `missing attention state: ${text}`);
assert(text.includes("Verified Agent handoff needs system review"), `missing system review copy: ${text}`);
assert(text.includes("Latest dispatcher status: suppressed"), `missing dispatcher status: ${text}`);
assert(!text.includes("1 active · 0 proposed"), `ordinary active count hid dispatcher attention: ${text}`);
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_agent_card_ignores_terminal_history_dispatcher_status(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const flatten = (node) => {
  if (!node) return "";
  const own = typeof node.textContent === "string" ? node.textContent : "";
  return own + (node.children || []).map(flatten).join("");
};
state.snapshot = {
  as_of: "2026-08-25",
  tasks: [],
  goals: [],
  today: { in_progress: [], todays_actions: [], overdue: [], waiting_and_blocked: [] },
  views: { inbox: [], completed: [], blocked: [], projects: [] },
};
state.agents = [{
  slug: "agents/tammy",
  name: "Tammy",
  avatar: { kind: "initials", value: "TA" },
  default_goal_slugs: [],
  runtime: "codex",
}];
state.agentTasks = [{
  slug: "tasks/terminal-history",
  title: "Old completed dispatcher record",
  status: "cancelled",
  owner: { slug: "agents/tammy", name: "Tammy", avatar: { kind: "initials", value: "TA" } },
  owner_agent: "agents/tammy",
  open_todos: [],
  dispatcher_handoff: { status: "suppressed" },
  updated_at: "2026-08-25T12:00:00Z",
}];
state.agentWorkLoaded = true;
state.agentWorkLoading = false;
state.goalExecution = null;
state.delegations = [];
state.handoffLogEvents = [];
state.handoffLogLoading = false;
state.handoffLogError = "";
const text = flatten(renderAgentWorkView());
assert(!text.includes("Needs attention · Verified Agent handoff needs system review"), `terminal history created false attention: ${text}`);
assert(!text.includes("Latest dispatcher status: suppressed"), `terminal history exposed stale dispatcher repair copy: ${text}`);
assert(text.includes("0 active · 0 proposed · 0 blocked · 0 completed"), `ordinary summary was not preserved: ${text}`);
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_agents_route_is_unified_handoff_surface_without_coordinator_copy(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        stylesheet = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        agent_work = javascript[
            javascript.index("function renderAgentWorkView") : javascript.index("function goalCard")
        ]
        set_view = javascript[
            javascript.index("function setView") : javascript.index("function setConnection")
        ]

        self.assertIn('data-view="agent-work"', html)
        self.assertNotIn('data-view="handoff-log"', html)
        self.assertNotIn('<span class="nav-label">Handoff Log</span>', html)
        for forbidden in (
            "Agent Directory",
            "Coordinator",
            "Agent Coordination",
            "Read-only triage across the three canonical agent work roots",
            "No proposal or task is auto-approved here",
        ):
            self.assertNotIn(forbidden, agent_work)
        self.assertNotIn("renderCoordinatorSummary", agent_work)
        self.assertIn("renderAgentHandoffStatus(agent)", agent_work)
        self.assertIn("renderSystemHandoffAttention()", agent_work)
        self.assertIn("renderUnifiedHandoffHistory({", agent_work)
        self.assertIn("function renderUnifiedHandoffHistory", javascript)
        self.assertIn("function renderAgentHandoffStatus(agent)", javascript)
        self.assertIn("function renderSystemHandoffAttention", javascript)
        self.assertIn("Handoff History", javascript)
        self.assertIn("const shouldOpen = historyOpen || state.agentHandoffHistoryOpen", javascript)
        self.assertIn("details.open = Boolean(shouldOpen)", javascript)
        self.assertIn("agentHandoffHistoryOpen: false", javascript)
        self.assertIn("state.agentHandoffHistoryOpen = !details.open", javascript)
        self.assertIn("state.agentHandoffHistoryOpen = details.open", javascript)
        self.assertIn('if (view === "handoff-log")', set_view)
        self.assertIn("state.agentHandoffHistoryOpen = true", set_view)
        self.assertIn('view = "agent-work"', set_view)
        self.assertNotIn('view === "agent-work" || view === "handoff-log"', set_view)
        self.assertIn("agent.slug === event.agent_slug", javascript)
        self.assertIn("!verifiedAgents.has(event.agent_slug)", javascript)
        self.assertIn(".agent-handoff-history", stylesheet)
        self.assertIn(".agent-handoff-history:not([open]) > :not(summary)", stylesheet)
        self.assertIn(".system-handoff-attention", stylesheet)

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

    def test_sidebar_navigation_order_matches_mission_control_priority_order(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        nav = html[html.index('<nav class="nav-list"') : html.index("</nav>", html.index('<nav class="nav-list"'))]
        expected = [
            ("today", "Today"),
            ("week", "Calendar"),
            ("board", "Board"),
            ("inbox", "Inbox"),
            ("agent-work", "Agents"),
            ("artifacts", "Artifacts"),
            ("blocked", "Blocked"),
            ("completed", "Completed"),
            ("all", "All Tasks"),
            ("projects", "Projects"),
            ("goals", "Goals"),
        ]

        positions = []
        for view, label in expected:
            self.assertEqual(nav.count(f'data-view="{view}"'), 1, view)
            self.assertEqual(nav.count(f'aria-label="{label}"'), 1, label)
            self.assertEqual(nav.count(f'<span class="nav-label">{label}</span>'), 1, label)
            positions.append(nav.index(f'data-view="{view}"'))
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(nav.count('class="nav-item'), len(expected))
        self.assertNotIn("Upcoming", nav)

    def test_mission_word_art_is_hud_framed_without_flattening_lights(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        svg = (PROJECT_ROOT / "static" / "assets" / "mission-control-word-art.svg").read_text(encoding="utf-8")
        footer = html[html.index('<div class="mission-art-center"') : html.index('id="sidebar-version"')]

        self.assertIn('class="mission-word-art mission-word-art-frame"', footer)
        self.assertIn('src="/assets/mission-control-word-art.svg"', footer)
        self.assertIn("single Mission Control celestial word art", footer)
        self.assertNotIn('mission-control-word-art.png', footer)
        self.assertNotIn("North Star between the words", footer)
        self.assertIn(">Mission Control<", svg)
        self.assertEqual(svg.count(">Mission Control<"), 1)
        self.assertIn('id="artwordGlow"', svg)
        self.assertIn('class="art-light"', svg)
        self.assertIn(".mission-word-art-frame", css)
        frame = css[css.index(".mission-word-art-frame") : css.index(".mission-version-link")]
        self.assertIn("border: 1px solid", frame)
        self.assertIn("rgba(56, 189, 248", frame)
        self.assertIn("box-shadow", frame)
        self.assertIn("overflow: visible", frame)
        self.assertIn(".mission-word-art-frame::before", frame)
        self.assertIn(".mission-word-art-frame::after", frame)
        image_start = css.index(".mission-word-art img", css.index(".mission-word-art-frame"))
        image_style = css[image_start : css.index(".topbar", image_start)]
        self.assertIn("drop-shadow(0 0 10px", image_style)
        self.assertIn("drop-shadow(0 0 26px", image_style)
        self.assertIn("drop-shadow(0 0 44px", image_style)

    def test_mission_word_art_layers_frame_directly_with_exterior_light_rings(self) -> None:
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        frame = css[css.index(".mission-word-art-frame") : css.index(".mission-version-link")]
        outer_start = css.index(".mission-word-art-frame::before {")
        overlay_start = css.index(".mission-word-art-frame::after {", outer_start)
        outer_glow = css[outer_start:overlay_start]
        frame_overlay = css[overlay_start : css.index(".mission-art-footer")]

        self.assertIn("--mission-frame-glow-inset: -18px", css)
        self.assertIn("inset: var(--mission-frame-glow-inset)", outer_glow)
        self.assertIn("z-index: 0", outer_glow)
        self.assertIn("inset: 0", frame_overlay)
        self.assertIn("border: 1px solid", frame_overlay)
        self.assertIn("z-index: 1", frame_overlay)
        self.assertNotIn("inset 0 0", frame)
        self.assertLess(
            frame.index(".mission-word-art-frame::before"),
            frame.index(".mission-word-art-frame::after"),
        )

    def test_mobile_mission_word_art_remains_large_enough_to_read(self) -> None:
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        mobile = css[css.rindex("@media (max-width: 760px)") :]

        self.assertIn("width: min(330px, calc(100vw - 48px))", mobile)
        self.assertIn(".mission-art-footer", mobile)
        self.assertIn("grid-template-columns: minmax(0, 1fr)", mobile)
        self.assertNotIn("width: min(185px, 50vw)", mobile)
        self.assertNotIn("width: 38vw", mobile)

    def test_desktop_mission_word_art_reserves_clearance_above_exterior_glow(self) -> None:
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        footer = css[css.index(".mission-art-footer") : css.index(".mission-art-center")]

        self.assertIn("--mission-frame-glow-inset: -18px", css)
        self.assertIn("padding-top: 66px", footer)
        self.assertIn("margin: auto 0 0", footer)

    def test_mission_control_uses_the_dark_stargraph_family_brand(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('<meta name="color-scheme" content="dark">', html)
        self.assertIn('<meta name="theme-color" content="#020816">', html)
        self.assertIn('/assets/mission-control-command-mark.svg', html)
        self.assertIn('/assets/mission-control-word-art.svg', html)
        self.assertNotIn('/assets/mission-control-word-art.png', html)
        self.assertIn('class="mission-word-art', html)
        self.assertIn("mission-word-art-frame", html)
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

    def test_canonical_root_issues_are_visible_outside_inbox_empty_states(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function renderCanonicalRootIssues()", javascript)
        self.assertIn('issue.category === "canonical_root_data"', javascript)
        self.assertIn("Mission Control is withholding an empty view", javascript)
        render = javascript[javascript.index("function render()") :]
        self.assertIn("renderCanonicalRootIssues()", render)
        self.assertIn('"system-tickets"', render)
        self.assertIn('"projects"', render)

    def test_first_view_cards_keep_long_detail_in_the_detail_panel(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        tickets = javascript[javascript.index("function renderSystemTicketsView") : javascript.index("function openSystemTicketDialog")]
        self.assertIn('node("button", "system-ticket-card")', tickets)
        self.assertIn("selectSystemTicket(ticket.slug, card)", tickets)
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

    def test_system_ticket_refresh_keeps_last_verified_cards_and_coalesces_reads(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        renderer = javascript[
            javascript.index("function renderSystemTicketsView()")
            : javascript.index("function openSystemTicketDialog()")
        ]
        loader = javascript[
            javascript.index("function loadSystemTickets")
            : javascript.index("async function loadCompletedSystemTickets")
        ]

        self.assertIn("state.systemTicketsError &&", renderer)
        self.assertIn("!state.systemTickets.length &&", renderer)
        self.assertIn("Last verified System Tickets remain visible", renderer)
        self.assertIn("refresh is delayed", renderer)
        self.assertIn("if (state.systemTicketsLoadPromise) return", loader)

    def test_system_ticket_cold_202_is_loading_not_an_authoritative_empty_state(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        renderer = javascript[
            javascript.index("function renderSystemTicketsView()")
            : javascript.index("function openSystemTicketDialog()")
        ]
        count_label = javascript[
            javascript.index("function inContextCountLabel(view)")
            : javascript.index("function setView(view)")
        ]
        loader = javascript[
            javascript.index("async function performSystemTicketLoad")
            : javascript.index("async function loadCompletedSystemTickets")
        ]

        self.assertIn("function systemTicketsColdLoading()", javascript)
        self.assertIn('readState?.status === "loading"', javascript)
        self.assertIn("systemTicketsColdLoading()", renderer)
        self.assertIn('"Reading System Tickets…"', renderer)
        self.assertIn("systemTicketsColdLoading()", count_label)
        self.assertIn('return "Reading System Tickets…"', count_label)
        self.assertIn("if (response.status === 200)", loader)

    def test_system_ticket_selection_uses_detail_panel_and_ticket_safe_editor(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="system-ticket-detail-content"', html)
        self.assertIn('id="system-ticket-edit-button"', html)
        self.assertIn('id="system-ticket-editor-status"', html)
        self.assertIn("function selectSystemTicket(ticketSlug, originControl = null)", javascript)
        self.assertIn('state.selectedKind = "system-ticket"', javascript)
        self.assertIn("selectSystemTicket(ticket.slug, card)", javascript)
        self.assertIn('card.dataset.slug = ticket.slug', javascript)
        self.assertIn("elements.systemTicketDetailTitle.focus({ preventScroll: true })", javascript)
        self.assertIn('...document.querySelectorAll(".system-ticket-card")', javascript)
        self.assertIn(
            'const focusedSystemTicketSlug = document.activeElement?.closest?.(".system-ticket-card")?.dataset.slug || null;',
            javascript,
        )
        self.assertIn('.system-ticket-card[data-slug=', javascript)
        self.assertIn('CSS.escape(focusedSystemTicketSlug)', javascript)
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

    def test_system_ticket_completed_pagination_recovers_after_fresh_refresh(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
state.activeView = "system-tickets";
state.showCompletedSystemTickets = true;
state.completedSystemTickets = Array.from({ length: 45 }, (_item, index) => ({
  slug: `tasks/completed-${index}`,
  title: `Completed ${index}`,
  status: "completed",
  target_subsystem: "mission_control",
  priority: "normal",
  implementation_receipts: [],
  qa_receipts: [],
}));
state.completedSystemTicketsOffset = 45;
state.completedSystemTicketsHasMore = false;
state.systemTicketsReadState = { status: "refreshing", refreshing: true };
render = () => {};
let requested = null;
globalThis.fetch = async (url) => {
  requested = url;
  return {
    ok: true,
    status: 200,
    json: async () => ({
      tickets: [
        { slug: "tasks/open-1", title: "Open 1", status: "planned" },
        { slug: "tasks/open-2", title: "Open 2", status: "active" },
        { slug: "tasks/open-3", title: "Open 3", status: "planned" },
      ],
      issues: [],
      read_state: { status: "fresh", refreshing: false },
    }),
  };
};

await performSystemTicketLoad();

assert(requested === "/api/system-tickets?include_completed=0", `unexpected URL ${requested}`);
assert(state.systemTickets.length === 3, "fresh open System Tickets did not reconcile");
assert(state.completedSystemTicketsHasMore === true, "fresh refresh did not reopen completed pagination");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_completed_system_ticket_offset_tracks_unique_loaded_cards(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
state.activeView = "system-tickets";
state.showCompletedSystemTickets = true;
state.completedSystemTickets = [
  { slug: "tasks/completed-a", title: "Completed A", status: "completed" },
  { slug: "tasks/completed-b", title: "Completed B", status: "completed" },
];
state.completedSystemTicketsOffset = 2;
render = () => {};
let requested = null;
globalThis.fetch = async (url) => {
  requested = url;
  return {
    ok: true,
    status: 200,
    json: async () => ({
      tickets: [
        { slug: "tasks/completed-b", title: "Completed B fresh", status: "completed" },
        { slug: "tasks/completed-c", title: "Completed C", status: "completed" },
      ],
      pagination: { has_more: true },
    }),
  };
};

await loadCompletedSystemTickets();

assert(requested === "/api/system-tickets?completed_only=1&offset=2&limit=5", `unexpected URL ${requested}`);
assert(state.completedSystemTickets.length === 3, "completed tickets were not de-duplicated");
assert(state.completedSystemTicketsOffset === 3, `offset used raw page size: ${state.completedSystemTicketsOffset}`);
assert(state.completedSystemTickets.some((ticket) => ticket.slug === "tasks/completed-c"), "new completed page item missing");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

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
        self.assertLess(footer.index('id="system-tickets-button"'), footer.index('class="mission-word-art'))
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

    def test_stale_refreshing_snapshot_never_rearms_an_overdue_zero_delay_refresh(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const now = 1787024471458;
const delays = [];
let renderCount = 0;
Date.now = () => now;
document.hidden = false;
window.setTimeout = (_callback, delay) => {
  delays.push(delay);
  return delays.length;
};
window.clearTimeout = () => {};
render = () => { renderCount += 1; };
state.activeView = "board";
state.snapshot = {
  as_of: "2026-08-17",
  tasks: [],
  goals: [],
  projects: [],
  views: { blocked: [], completed: [] },
  today: { in_progress: [], todays_actions: [], overdue: [] },
};
state.projectsLoaded = true;
state.autoRefreshDueAt = now - 26482161;
state.autoRefreshTimer = null;
state.taskSurfacePollTimer = null;
globalThis.fetch = async (url) => {
  assert(url === "/api/tasks?refresh=1", `unexpected URL ${url}`);
  return {
    ok: true,
    status: 200,
    json: async () => ({
      ...state.snapshot,
      read_state: {
        surface: "tasks",
        status: "refreshing",
        refreshing: true,
        stale: true,
        last_valid_at: now / 1000,
        error: null,
      },
    }),
  };
};

await performTaskLoad("automatic");

assert(renderCount === 1, `one completed read should render once, got ${renderCount}`);
assert(
  state.autoRefreshDueAt === now + AUTO_REFRESH_INTERVAL_MS,
  `overdue deadline was not advanced: ${state.autoRefreshDueAt - now}`,
);
assert(
  delays.includes(AUTO_REFRESH_INTERVAL_MS),
  `next automatic refresh was not bounded to the configured interval: ${delays}`,
);
assert(!delays.includes(0), `stale refresh rearmed the runaway zero-delay loop: ${delays}`);
assert(delays.includes(1000), `bounded stale-surface poll was not preserved: ${delays}`);
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_repeated_stale_refresh_poll_soak_keeps_timers_and_renders_bounded(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const now = 1787024471458;
const delays = [];
let renderCount = 0;
let fetchCount = 0;
Date.now = () => now;
document.hidden = false;
window.setTimeout = (_callback, delay) => {
  delays.push(delay);
  return delays.length;
};
window.clearTimeout = () => {};
render = () => { renderCount += 1; };
state.activeView = "board";
state.snapshot = {
  as_of: "2026-08-17",
  tasks: [],
  goals: [],
  projects: [],
  views: { blocked: [], completed: [] },
  today: { in_progress: [], todays_actions: [], overdue: [] },
};
state.projectsLoaded = true;
state.autoRefreshDueAt = now - 26482161;
state.autoRefreshTimer = null;
state.taskSurfacePollTimer = null;
globalThis.fetch = async (url) => {
  assert(url === "/api/tasks", `unexpected URL ${url}`);
  fetchCount += 1;
  return {
    ok: true,
    status: 200,
    json: async () => ({
      ...state.snapshot,
      read_state: {
        surface: "tasks",
        status: "refreshing",
        refreshing: true,
        stale: true,
        last_valid_at: now / 1000,
        error: null,
      },
    }),
  };
};

const cycles = 120;
for (let cycle = 0; cycle < cycles; cycle += 1) {
  state.taskSurfacePollTimer = null;
  await performTaskLoad("poll");
}

const automaticDelays = delays.filter((delay) => delay === AUTO_REFRESH_INTERVAL_MS);
const surfacePollDelays = delays.filter((delay) => delay === 1000);
assert(fetchCount === cycles, `expected ${cycles} reads, got ${fetchCount}`);
assert(renderCount === cycles, `expected one render per read, got ${renderCount}`);
assert(
  automaticDelays.length === cycles,
  `automatic timers were not one-per-cycle and bounded: ${automaticDelays.length}`,
);
assert(
  surfacePollDelays.length === cycles,
  `surface poll timers were not one-per-cycle and bounded: ${surfacePollDelays.length}`,
);
assert(!delays.includes(0), `soak reintroduced a zero-delay timer: ${delays}`);
assert(
  delays.length === cycles * 2,
  `unexpected timer growth during soak: ${delays.length}`,
);
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

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
        self.assertIn("state.projectsReadState = payload.read_state || null", javascript)
        self.assertIn('if (response.status === 202) scheduleSurfacePoll("projects")', javascript)
        self.assertIn('surface === "projects"', javascript)
        projects_body = javascript[
            javascript.index("function renderProjectsView()")
            : javascript.index("async function loadProjects()")
        ]
        self.assertIn("projectsColdLoading()", projects_body)
        self.assertIn("const readState = state.projectsReadState", javascript)
        self.assertIn('readState?.status === "loading"', javascript)
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

    def test_handoff_events_share_one_read_only_renderer_and_bounded_source(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertNotIn('data-view="handoff-log"', html)
        self.assertNotIn('<span class="nav-label">Handoff Log</span>', html)
        self.assertIn('id="task-handoff-timeline"', html)
        self.assertIn('id="task-handoff-timeline-heading" aria-expanded="false"', html)
        self.assertIn('Task Timeline', html)
        self.assertIn('<ol class="handoff-event-list" id="task-handoff-event-list"', html)
        self.assertIn('id="task-handoff-load-more"', html)
        self.assertIn("const HANDOFF_EVENT_PAGE_SIZE = 50", javascript)
        self.assertIn("async function loadTaskHandoffTimeline(taskSlug)", javascript)
        self.assertIn("async function loadHandoffLog({ reset = false, filters = null } = {})", javascript)
        self.assertIn("function renderHandoffEvents(events, destination)", javascript)
        self.assertIn("function openHandoffCorrelation(correlationId, taskSlug", javascript)
        self.assertIn('`/api/tasks/${encodeURIComponent(taskSlug)}/handoff-events?${params}`', javascript)
        self.assertIn('fetch(`/api/handoff-events?${params}`', javascript)
        self.assertGreaterEqual(javascript.count("renderHandoffEvents("), 3)
        self.assertIn("showTaskLink: false", javascript)
        self.assertIn("destination.showTaskLink !== false", javascript)
        self.assertIn("Task: ${", javascript)

        task_loader = javascript[
            javascript.index("async function loadTaskHandoffTimeline(taskSlug)") :
            javascript.index("function handoffLogFilters")
        ]
        log_loader = javascript[
            javascript.index("async function loadHandoffLog") :
            javascript.index("function openHandoffCorrelation")
        ]
        for loader in (task_loader, log_loader):
            self.assertNotIn("method:", loader)
            self.assertNotIn('fetch("/api/handoffs', loader)
            self.assertNotIn("/ack", loader)
            self.assertNotIn("/failure", loader)

    def test_handoff_log_filters_counts_order_correlation_and_safe_states(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        stylesheet = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("function renderHandoffEvents(events, destination)", javascript)
        renderer = javascript[
            javascript.index("function renderHandoffEvents(events, destination)") :
            javascript.index("async function loadTaskHandoffTimeline")
        ]
        for label in ("Time", "Agent", "Status", "Event", "Failure", "Correlation"):
            self.assertIn(f'"{label}"', javascript)
        self.assertIn("handoffLogTotal", javascript)
        self.assertIn("nextSequence", javascript)
        self.assertIn("event.sequence", renderer)
        self.assertIn("privacySafeEventText", renderer)
        self.assertIn("destination.showTaskLink !== false", renderer)
        self.assertIn("is-dead-letter", renderer)
        self.assertIn("Open Task", renderer)
        self.assertIn("event.detail || event.summary", renderer)
        self.assertNotIn("Open correlated Task", renderer)
        self.assertNotIn("event.handoff_id", renderer)
        self.assertNotIn("event.registration_ref", renderer)
        self.assertNotIn("event.idempotency_key", renderer)
        for state_copy in (
            "Loading handoff events",
            "No handoff events match",
            "Last verified handoff events",
            "Handoff events are unavailable",
            "Dead letter",
        ):
            self.assertIn(state_copy, javascript)
        self.assertIn("aria-live", html)
        self.assertIn(".handoff-event-list", stylesheet)
        self.assertIn("overflow-wrap: anywhere", stylesheet)
        mobile = stylesheet[stylesheet.index("@media (max-width: 760px)") :]
        self.assertIn(".agent-handoff-history", mobile)
        self.assertIn("overflow-x: hidden", mobile)
        self.assertIn(".handoff-filter-grid", mobile)
        self.assertIn("grid-template-columns: minmax(0, 1fr)", mobile)

    def test_task_handoff_timeline_is_bottom_collapsed_and_no_self_navigation_controls(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        stylesheet = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        timeline_index = html.index('id="task-handoff-timeline"')
        for required_before in (
            'id="task-todo-list"',
            'id="task-artifacts"',
            'class="detail-list"',
            'id="detail-gbrain-link"',
            'id="detail-slug"',
        ):
            self.assertLess(html.index(required_before), timeline_index)
        self.assertIn("<details class=\"task-handoff-timeline\"", html)
        self.assertIn('id="task-handoff-timeline-heading" aria-expanded="false"', html)
        self.assertIn(".task-handoff-timeline:not([open]) > :not(summary)", stylesheet)
        self.assertIn("function syncTaskHandoffTimelineDisclosure", javascript)
        self.assertIn('elements.taskHandoffTimeline.open = false', javascript)
        self.assertIn("showTaskLink: false", javascript)
        task_timeline = javascript[
            javascript.index("function renderTaskHandoffTimeline(taskSlug)") :
            javascript.index("async function loadTaskHandoffTimeline")
        ]
        self.assertIn("showTaskLink: false", task_timeline)

    def test_agents_is_the_only_user_facing_handoff_surface_with_legacy_redirect(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        set_view = javascript[
            javascript.index("function setView") : javascript.index("function setConnection")
        ]
        opener = javascript[
            javascript.index("function openHandoffCorrelation") :
            javascript.index("function handoffFilterSelect")
        ]

        self.assertNotIn('data-view="handoff-log"', html)
        self.assertNotIn("function renderHandoffLogView", javascript)
        self.assertIn('if (view === "handoff-log")', set_view)
        self.assertIn("state.agentHandoffHistoryOpen = true", set_view)
        self.assertIn('view = "agent-work"', set_view)
        self.assertIn('state.activeView = "agent-work"', opener)
        self.assertIn("state.agentHandoffHistoryOpen = true", opener)
        self.assertNotIn('state.activeView = "handoff-log"', opener)

    def test_handoff_correlation_preserves_detail_focus_return_and_fixture_data(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        fixture = (PROJECT_ROOT / "tests" / "project_browser_fixture.py").read_text(encoding="utf-8")

        self.assertIn("function openHandoffCorrelation", javascript)
        opener = javascript[
            javascript.index("function openHandoffCorrelation") :
            javascript.index("function handoffFilterSelect")
        ]
        self.assertIn('state.activeView = "agent-work"', opener)
        self.assertIn("state.agentHandoffHistoryOpen = true", opener)
        self.assertIn("selectTask(taskSlug, task, originControl)", opener)
        self.assertIn('document.querySelectorAll(".handoff-event-task")', javascript)
        self.assertIn("loadTaskHandoffTimeline(task.slug)", javascript)
        self.assertIn("DurableHandoffStore", fixture)
        self.assertIn("ActionableChange", fixture)
        self.assertIn("build_fixture_server", fixture)
        self.assertIn("ReadSnapshotStore(read_cache_path)", fixture)
        self.assertIn("background=False", fixture)
        self.assertIn("correlation-fixture-task", fixture)
        self.assertIn("fixture-terminal", fixture)
        self.assertIn("append_correction", fixture)
        self.assertIn("handoff_store=SyntheticClaimHandoffStore(handoff_store, claim)", fixture)
        self.assertIn("MISSION_CONTROL_QA_DELEGATION_SCENARIO", fixture)
        self.assertIn("MISSION_CONTROL_QA_PORT", fixture)
        self.assertIn('"inactive", "active", "expired", "unknown", "mismatched"', fixture)
        self.assertIn("SyntheticClaimHandoffStore", fixture)
        self.assertIn("external writes: disabled", fixture)
        self.assertIn("/api/qa-fixture-status", fixture)
        production_server = (PROJECT_ROOT / "gtasks" / "server.py").read_text(encoding="utf-8")
        self.assertNotIn("/api/qa-fixture-status", production_server)
        self.assertNotIn("GBrainAdapter(", fixture)
        self.assertNotIn("NatsClient(", fixture)
        self.assertNotIn("OpenClawClient(", fixture)

    def test_handoff_correlation_waits_for_inflight_canonical_task_read(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const taskSlug = "tasks/runtime-canonical";
const task = { slug: taskSlug, status: "active", title: "Runtime canonical task" };
state.snapshot = { tasks: [], goals: [], views: {} };
state.agentTasks = [];
let releaseTaskRead;
state.tasksLoadPromise = new Promise((resolve) => {
  releaseTaskRead = () => { state.snapshot.tasks = [task]; resolve(); };
});
let selected = null;
render = () => {};
loadHandoffLog = async () => {};
selectTask = (slug, fallback, focus) => { selected = { slug, fallback, focus }; };
const origin = new FakeElement("button");
const pending = openHandoffCorrelation("correlation-runtime", taskSlug, origin);
await Promise.resolve();
assert(selected === null, "task detail opened before canonical read completed");
releaseTaskRead();
await pending;
assert(selected?.slug === taskSlug, "task detail did not open after canonical read");
assert(selected?.focus === origin, "correlation origin focus context was not preserved");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_handoff_correlation_loads_agent_task_or_reports_stable_error(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const taskSlug = "tasks/runtime-agent";
const agentTask = { slug: taskSlug, status: "active", title: "Runtime Agent task" };
state.snapshot = { tasks: [], goals: [], views: {} };
state.agentTasks = [];
state.tasksLoadPromise = null;
let releaseAgentRead;
state.agentWorkLoading = true;
state.agentWorkLoadPromise = new Promise((resolve) => {
  releaseAgentRead = () => { state.agentTasks = [agentTask]; resolve(); };
});
let selected = null;
render = () => {};
loadHandoffLog = async () => {};
loadTasks = async () => {};
selectTask = (slug) => { selected = slug; };
const pendingAgent = openHandoffCorrelation("correlation-agent", taskSlug, new FakeElement("button"));
await Promise.resolve();
assert(selected === null, "Agent task opened before its startup read completed");
releaseAgentRead();
await pendingAgent;
assert(selected === taskSlug, "Agent task was not loaded before selection");

state.agentTasks = [];
state.agentWorkLoading = false;
state.agentWorkLoadPromise = null;
selected = null;
loadAgentWork = async () => {};
await openHandoffCorrelation("correlation-missing", "tasks/runtime-missing", new FakeElement("button"));
assert(selected === null, "missing task opened an unrelated detail");
assert(state.handoffLogError.includes("Linked Task"), "missing linked task had no explicit read error");
assert(state.handoffLogFocusKey === "load-status", "missing linked task did not target stable status focus");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_handoff_correlation_reads_exact_canonical_qa_fixture(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const taskSlug = "tasks/70cf1aeb-ac30-4d78-995a-a1fea9d5bea9";
const fixture = {
  slug: taskSlug,
  status: "completed",
  title: "Dispatcher release canary fixture",
  lifecycle_root: "collections/mission-control-qa-fixtures",
  qa_fixture: true,
};
state.snapshot = { tasks: [], goals: [], views: {} };
state.agentTasks = [];
state.tasksLoadPromise = null;
state.agentWorkLoading = false;
state.agentWorkLoadPromise = null;
loadTasks = async () => {};
loadAgentWork = async () => {};
loadHandoffLog = async () => {};
render = () => {};
let requested = null;
globalThis.fetch = async (url) => {
  requested = url;
  return { ok: true, json: async () => ({ task: fixture }) };
};
let selected = null;
selectTask = (slug, fallback, focus) => { selected = { slug, fallback, focus }; };
const origin = new FakeElement("button");

await openHandoffCorrelation("corr-v0076-canary-4", taskSlug, origin);

assert(requested === `/api/tasks/${encodeURIComponent(taskSlug)}`, `unexpected exact task URL: ${requested}`);
assert(selected?.slug === taskSlug, "exact canonical fixture did not open");
assert(selected?.fallback?.qa_fixture === true, "QA fixture readback was not passed to Task detail");
assert(selected?.focus === origin, "correlation origin focus context was not preserved");
assert(state.handoffLogError === "", `unexpected correlation error: ${state.handoffLogError}`);
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_handoff_missing_linked_task_renders_specific_safe_error(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const diagnosis = "Linked Task could not be read from canonical or Agent work.";
state.handoffLogEvents = [];
state.handoffLogLoading = false;
state.handoffLogStale = false;
state.handoffLogError = diagnosis;
const view = renderAgentWorkView({ historyOpen: true });
const findClass = (root, className) => {
  if (root.className === className) return root;
  for (const child of root.children || []) {
    const found = findClass(child, className);
    if (found) return found;
  }
  return null;
};
const renderedState = findClass(view, "handoff-surface-state");
assert(renderedState, "unified Handoff History did not render the state element");
assert(renderedState.textContent === diagnosis, `specific error was replaced by: ${renderedState.textContent}`);
assert(!renderedState.textContent.includes("without changing any task"), "contradictory generic advice remained");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_handoff_close_detail_focus_falls_back_when_origin_row_disappears(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const origin = new FakeElement("button");
origin.isConnected = false;
const stableStatus = new FakeElement("p");
stableStatus.isConnected = true;
document.querySelectorAll = () => [];
document.querySelector = (selector) => selector.includes("load-status") ? stableStatus : null;
render = () => {};
state.activeView = "agent-work";
state.agentHandoffHistoryOpen = true;
state.selectedKind = "task";
state.selectedSlug = "tasks/runtime-disappeared-origin";
state.detailReturnFocus = {
  element: origin,
  slug: "tasks/runtime-disappeared-origin",
};
elements.detailPanel.setAttribute("aria-hidden", "false");
document.activeElement = elements.detailClose;
closeDetails();
assert(stableStatus.focused, "close detail did not focus the stable Handoff History status");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_handoff_task_link_close_restores_exact_origin_after_rerender(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const taskSlug = "tasks/runtime-handoff-origin";
const origin = new FakeElement("a");
origin.isConnected = false;
origin.dataset.slug = taskSlug;
origin.dataset.handoffTask = "true";
origin.dataset.sequence = "42";
origin.dataset.correlationId = "corr-42";
const stableStatus = new FakeElement("p");
stableStatus.isConnected = true;
stableStatus.dataset.handoffFocus = "load-status";
const exactReplacement = new FakeElement("a");
exactReplacement.isConnected = true;
exactReplacement.dataset.slug = taskSlug;
exactReplacement.dataset.handoffTask = "true";
exactReplacement.dataset.sequence = "42";
exactReplacement.dataset.correlationId = "corr-42";
const wrongSameTask = new FakeElement("a");
wrongSameTask.isConnected = true;
wrongSameTask.dataset.slug = taskSlug;
wrongSameTask.dataset.handoffTask = "true";
wrongSameTask.dataset.sequence = "41";
wrongSameTask.dataset.correlationId = "corr-41";
let replacementVisible = false;
document.querySelectorAll = (selector) => {
  if (!replacementVisible) return [];
  return selector === ".handoff-event-task" ? [wrongSameTask, exactReplacement] : [];
};
document.querySelector = (selector) => selector.includes("load-status") ? stableStatus : null;
render = () => {};
state.activeView = "agent-work";
state.agentHandoffHistoryOpen = true;
state.selectedKind = "task";
state.selectedSlug = taskSlug;
state.detailReturnFocus = {
  element: origin,
  slug: taskSlug,
  handoffTask: true,
  sequence: "42",
  correlationId: "corr-42",
};
elements.detailPanel.setAttribute("aria-hidden", "false");
document.activeElement = elements.detailClose;
closeDetails();
assert(stableStatus.focused, "fixture did not first focus the stable Handoff History status");
replacementVisible = true;
state.handoffLogFocusKey = "load-status";
restorePendingDetailFocus();
restoreHandoffFocus();
assert(exactReplacement.focused, "close detail did not restore the exact originating Handoff Task link after rerender");
assert(document.activeElement === exactReplacement, "handoff status focus restore overrode the exact originating Task link");
assert(!wrongSameTask.focused, "close detail focused a different event for the same task slug");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_handoff_task_reference_preserves_visible_history_during_readback(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const taskSlug = "tasks/runtime-handoff-preserve";
const event = {
  sequence: 51,
  task_ref: { available: true, slug: taskSlug, title: "Preserved handoff Task", surface: "task" },
  correlation_id: "corr-preserve",
};
state.handoffLogFilters = { ...state.handoffLogFilters, correlation_id: "corr-preserve" };
state.handoffLogEvents = [event];
state.handoffLogTotal = 1;
state.handoffLogSnapshotTotal = 1;
state.handoffLogNextSequence = null;
state.activeView = "agent-work";
state.agentHandoffHistoryOpen = true;
const origin = new FakeElement("a");
let release;
loadHandoffLog = async () => new Promise((resolve) => { release = resolve; });
loadCorrelatedHandoffTask = async () => ({ slug: taskSlug, title: "Preserved handoff Task" });
selectTask = () => {};
const pending = openHandoffTaskReference(event, origin);
await Promise.resolve();
assert(state.handoffLogEvents.length === 1, "opening a Handoff Task blanked the visible history while readback was pending");
assert(state.handoffLogEvents[0].sequence === 51, "opening a Handoff Task replaced the visible history before readback");
release();
await pending;
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_handoff_completed_system_ticket_uses_exact_slug_readback(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const ticketSlug = "tasks/completed-handoff-ticket";
state.systemTickets = [];
state.completedSystemTickets = [];
loadSystemTickets = async () => {};
let requested = null;
globalThis.fetch = async (url) => {
  requested = url;
  return {
    ok: true,
    json: async () => ({
      ticket: { slug: ticketSlug, title: "Completed handoff ticket", status: "completed" },
    }),
  };
};

const ticket = await loadCorrelatedSystemTicket(ticketSlug);

assert(requested === `/api/system-tickets/${encodeURIComponent(ticketSlug)}`, `unexpected exact System Ticket URL: ${requested}`);
assert(ticket?.slug === ticketSlug, "completed System Ticket did not open by exact slug");
assert(state.completedSystemTickets.some((item) => item.slug === ticketSlug), "exact completed ticket was not cached for detail rendering");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_task_handoff_final_page_moves_load_more_focus_to_timeline_state(self) -> None:
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn(
            'id="task-handoff-event-state" role="status" aria-live="polite" tabindex="-1"',
            html,
        )
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const taskSlug = "tasks/runtime-timeline";
const event = (sequence) => ({
  sequence,
  task_slug: taskSlug,
  event_type: "handoff_queued",
  status: "queued",
  occurred_at: "2026-08-04T12:00:00Z",
  summary: "Safe runtime event.",
});
state.taskHandoffEvents.set(taskSlug, {
  events: [event(1)], total: 2, nextSequence: 1,
  loading: false, error: "", stale: false, requestToken: 0,
});
state.selectedKind = "task";
state.selectedSlug = taskSlug;
elements.taskHandoffLoadMore.focus();
globalThis.fetch = async () => ({
  ok: true,
  json: async () => ({ total: 1, events: [event(2)] }),
});
await readTaskHandoffPage(taskSlug, { reset: false });
assert(taskHandoffEntry(taskSlug).nextSequence === null, "fixture did not reach final page");
assert(elements.taskHandoffEventState.focused, "final page did not focus stable timeline state");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_handoff_recent_time_filter_uses_one_server_bounded_range(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const now = Date.parse("2026-08-04T18:00:00Z");
Date.now = () => now;
const recentEvent = { sequence: 51, occurred_at: "2026-08-04T17:30:00Z" };
let reads = 0;
let requested = "";
globalThis.fetch = async (url) => {
  reads += 1;
  requested = url;
  return { ok: true, json: async () => ({ total: 1, events: [recentEvent] }) };
};
state.activeView = "runtime-probe";
state.handoffLogFilters.time = "hour";
await loadHandoffLog({ reset: true });
const query = new URL(`http://fixture${requested}`).searchParams;
assert(reads === 1, `expected one bounded request, got ${reads}`);
assert(query.get("occurred_after") === "2026-08-04T17:00:00.000Z", "missing lower timestamp bound");
assert(query.get("occurred_before") === "2026-08-04T18:00:00.000Z", "missing upper timestamp bound");
assert(state.handoffLogEvents[0].sequence === 51, "bounded recent event was not rendered");
assert(state.handoffLogTotal === 1, `expected bounded server total 1, got ${state.handoffLogTotal}`);
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_handoff_runtime_filter_enums_cover_every_stored_value(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const statusValues = new Set(HANDOFF_STATUS_FILTER_OPTIONS.map(([value]) => value));
const eventValues = new Set(HANDOFF_EVENT_FILTER_OPTIONS.map(([value]) => value));
for (const value of ["suppressed", "still_blocked", "dead_letter", "retrying"]) {
  assert(statusValues.has(value), `missing status enum ${value}`);
}
for (const value of ["handoff_suppressed", "handoff_leased", "capability_rotated", "lease_expired"]) {
  assert(eventValues.has(value), `missing event enum ${value}`);
}
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_handoff_append_growth_keeps_snapshot_total_and_cursor_coherent(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const event = (sequence) => ({ sequence, occurred_at: "2026-08-04T12:00:00Z" });
const pages = [
  { total: 100, events: Array.from({ length: 50 }, (_, index) => event(index + 1)), next_sequence: 50 },
  { total: 70, events: Array.from({ length: 50 }, (_, index) => event(index + 51)), next_sequence: 100 },
];
let reads = 0;
globalThis.fetch = async () => ({ ok: true, json: async () => pages[reads++] });
state.activeView = "runtime-probe";
await loadHandoffLog({ reset: true });
await loadHandoffLog({ reset: false });
assert(state.handoffLogEvents.length === 100, `expected snapshot size 100, got ${state.handoffLogEvents.length}`);
assert(state.handoffLogTotal === 100, `expected stable total 100, got ${state.handoffLogTotal}`);
assert(state.handoffLogNextSequence === null, "growth beyond the snapshot left an impossible load-more cursor");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_handoff_runtime_focus_restores_equivalent_controls(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
for (const key of ["filter:status", "filter-submit", "load-more"]) {
  const original = markHandoffFocus(new FakeElement("button"), key);
  original.closestResult = original;
  document.activeElement = original;
  assert(captureHandoffFocus() === key, `did not capture ${key}`);
  const replacement = markHandoffFocus(new FakeElement("button"), key);
  document.querySelector = () => replacement;
  state.handoffLogFocusKey = key;
  restoreHandoffFocus(key);
  assert(replacement.focused, `did not restore ${key}`);
}
const statusFilter = handoffFilterSelect("Status", "status", [["", "All"]], "");
assert(statusFilter.children[1].dataset.handoffFocus === "filter:status", "status filter lacks equivalent focus key");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_handoff_clear_correlation_focus_moves_to_surviving_input(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const clear = markHandoffFocus(new FakeElement("button"), "filter-clear-correlation");
clear.closestResult = clear;
document.activeElement = clear;
assert(captureHandoffFocus() === "filter-clear-correlation", "clear focus was not captured");
const correlationInput = markHandoffFocus(new FakeElement("input"), "filter:correlation_id");
document.querySelector = (selector) => selector.includes("filter:correlation_id")
  ? correlationInput
  : null;
state.handoffLogFocusKey = "filter-clear-correlation";
restoreHandoffFocus();
assert(correlationInput.focused, "focus did not move to the surviving correlation input");
assert(state.handoffLogFocusKey === null, "completed focus restoration remained pending");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_handoff_mobile_390_controls_have_44px_targets(self) -> None:
        stylesheet = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        mobile = stylesheet[stylesheet.index("@media (max-width: 760px)") :]
        required_rule = """.handoff-filter-control select,
  .handoff-filter-control input,
  .handoff-filter-grid > .secondary-button,
  .handoff-task-link,
  .handoff-load-more,
  #task-handoff-load-more {
    min-height: 44px;
  }"""
        self.assertIn(required_rule, mobile)

    def test_openclaw_delegation_ui_is_confirmed_compact_and_fail_closed(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        markup = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        stylesheet = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        for copy in (
            "OpenClaw",
            "No goals assigned yet",
            "Owned work",
            "Additional delegated work",
            "Temporarily delegate work",
            "Permanent owner",
            "Temporary executor",
            "End Early",
            "Extend",
            "Revoke",
        ):
            self.assertIn(copy, javascript + markup)
        self.assertIn('fetch("/api/agent-delegations"', javascript)
        self.assertIn("window.confirm", javascript)
        create_flow = javascript[
            javascript.index("async function createTemporaryDelegation"):
            javascript.index("async function changeTemporaryDelegation")
        ]
        self.assertLess(create_flow.index("window.confirm"), create_flow.index('fetch("/api/agent-delegations"'))
        self.assertIn("expected_version", javascript)
        self.assertIn("display_timezone", javascript)
        self.assertIn('type = "datetime-local"', javascript)
        self.assertIn("delegation-card", stylesheet)
        self.assertIn("restoreDelegationFocus", javascript)
        self.assertIn("@media (max-width: 760px)", stylesheet)
        self.assertNotIn("session_key", javascript)
        self.assertNotIn("registration_id", javascript)

    def test_openclaw_delegation_runtime_cancels_without_write_and_expires_locally(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const past = new Date(Date.now() - 60000).toISOString();
state.delegations = [{ source_agent: "agents/tammy", executor_agent: "agents/tammy-oc", state: "active", ends_at: past }];
assert(activeDelegationForSource("agents/tammy") === null, "expired client projection stayed actionable");
let fetches = 0;
globalThis.fetch = async () => { fetches += 1; throw new Error("must not fetch"); };
window.confirm = () => false;
state.agents = [{ slug: "agents/tammy", name: "Tammy" }, { slug: "agents/tammy-oc", name: "Tammy-OC" }];
const end = { value: formatPacificInstant(new Date(Date.now() + 3600000)) };
const submit = { disabled: false };
const section = { querySelector: () => null };
const form = {
  closest: () => section,
  querySelector: (selector) => selector.includes("delegation-end") ? end : submit,
};
await createTemporaryDelegation(
  { preventDefault() {}, currentTarget: form },
  "agents/tammy",
  "agents/tammy-oc",
);
assert(fetches === 0, "cancelled confirmation still submitted a mutation");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_pacific_datetime_local_round_trip_handles_dst_and_request_instant(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
assert(formatPacificInstant(new Date("2026-03-08T09:30:00Z")) === "2026-03-08T01:30", "pre-DST wall time wrong");
assert(formatPacificInstant(new Date("2026-03-08T10:30:00Z")) === "2026-03-08T03:30", "post-DST wall time wrong");
assert(parsePacificLocalDateTime("2026-03-08T03:30").toISOString() === "2026-03-08T10:30:00.000Z", "spring DST instant wrong");
assert(!Number.isFinite(parsePacificLocalDateTime("2026-03-08T02:30").getTime()), "nonexistent spring time accepted");
assert(parsePacificLocalDateTime("2026-11-01T01:30").toISOString() === "2026-11-01T08:30:00.000Z", "fall overlap was not deterministic");

const selected = "2026-08-09T06:45";
const selectedInstant = parsePacificLocalDateTime(selected);
assert(formatPacificInstant(selectedInstant) === selected, "input and summary wall time disagree");
assert(selectedInstant.toISOString() === "2026-08-09T13:45:00.000Z", "request instant disagrees with Pacific input");
assert(formatPacificDisplay(selectedInstant).includes("6:45"), "confirmation display disagrees with input");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_task_detail_resolves_verified_executor_from_agent_work_only(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const future = new Date(Date.now() + 3600000).toISOString();
const leaseSlug = "agent-delegations/22222222-2222-4222-8222-222222222222";
state.agents = [
  { slug: "agents/tammy", name: "Tammy" },
  { slug: "agents/tammy-oc", name: "Tammy-OC" },
];
state.delegations = [{
  slug: leaseSlug,
  source_agent: "agents/tammy",
  executor_agent: "agents/tammy-oc",
  state: "active",
  ends_at: future,
}];
const canonicalTask = { slug: "tasks/fixture", owner_agent: "agents/tammy" };
let executorHidden = true;
elements.taskTemporaryExecutor.classList.toggle = (name, value) => {
  if (name === "is-hidden") executorHidden = value;
};
state.agentTasks = [{
  ...canonicalTask,
  temporary_execution: {
    permanent_owner: "agents/tammy",
    executor_agent: "agents/tammy-oc",
    delegation_slug: leaseSlug,
    expires_at: future,
  },
}];
renderTaskTemporaryExecutor(canonicalTask);
assert(!executorHidden, "verified executor stayed hidden");
assert(elements.taskExecutorName.textContent.includes("Tammy-OC"), "verified executor name missing");

state.agentTasks[0].temporary_execution.delegation_slug = "agent-delegations/33333333-3333-4333-8333-333333333333";
renderTaskTemporaryExecutor(canonicalTask);
assert(executorHidden, "unknown claim did not fail closed");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_task_detail_loads_executor_projection_before_agents_view(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const task = { slug: "tasks/fixture", owner_agent: "agents/tammy" };
state.selectedKind = "task";
state.selectedSlug = task.slug;
state.agentsLoaded = false;
state.delegationsLoaded = false;
state.agentWorkLoaded = false;
let agentLoads = 0;
let workLoads = 0;
let renders = 0;
loadAgents = async () => { agentLoads += 1; };
loadAgentWork = async () => { workLoads += 1; };
renderTaskTemporaryExecutor = (value) => {
  if (value?.slug === task.slug) renders += 1;
};
await ensureTaskTemporaryExecutorProjection(task);
assert(agentLoads === 1, "task detail did not load Agent/delegation projection");
assert(workLoads === 1, "task detail did not load Agent work projection");
assert(renders === 2, "task executor did not render before and after projection load");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_openclaw_cards_use_claim_projection_and_pair_controls(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        agent_view = javascript[
            javascript.index("function renderAgentWorkView"):
            javascript.index("function goalCard")
        ]
        self.assertIn("activeTemporaryExecution(task)", agent_view)
        execution_projection = javascript[
            javascript.index("function activeTemporaryExecution"):
            javascript.index("function delegationRemainingLabel")
        ]
        self.assertIn("temporary_execution", execution_projection)
        self.assertIn("execution.expires_at", execution_projection)
        self.assertNotIn('task.status === "planned"', agent_view)
        self.assertIn("renderDelegationControls(agent)", agent_view)
        self.assertIn("OPENCLAW_PAIR_BY_SOURCE[agent.slug]", javascript)
        self.assertIn("Session health unavailable", javascript)
        runtime = javascript[javascript.index("function agentRuntimeLabel"):javascript.index("function renderSystemHandoffAttention")]
        self.assertIn('["execution_started", "acknowledgement"]', runtime)
        self.assertIn("event.executor_agent === agent.slug", runtime)

    def test_agents_view_renders_all_six_profiles_in_api_order(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const names = ["Tammy", "Timmy", "Toddy", "Tammy-OC", "Timmy-OC", "Toddy-OC"];
state.agents = names.map((name, index) => ({
  slug: `agents/${name.toLowerCase()}`,
  name,
  title: `Agent ${name}`,
  summary: "Synthetic independent profile.",
  avatar: { kind: "initials", value: name.slice(0, 2) },
  runtime: index < 3 ? "codex" : "openclaw",
  default_goal_slugs: [],
}));
state.agentTasks = [];
state.snapshot = { tasks: [], goals: [], views: {} };
state.delegations = [];
state.handoffLogEvents = [];
state.handoffLogLoading = false;
state.handoffLogStale = false;
state.handoffLogError = "";
const view = renderAgentWorkView();
const cards = [];
const walk = (node) => {
  if (node?.className === "agent-profile-card") cards.push(node);
  for (const child of node?.children || []) walk(child);
};
walk(view);
assert(cards.length === 6, `expected six Agent cards, received ${cards.length}`);
assert(state.agents.map((agent) => agent.name).join("|") === names.join("|"), "Agent order changed");
assert(
  state.agents.filter((agent) => agent.runtime === "openclaw").every((agent) => agent.default_goal_slugs.length === 0),
  "OpenClaw fixture profile unexpectedly received a default Goal",
);
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_openclaw_session_health_ignores_arbitrary_handoff_activity(self) -> None:
        result = run_app_runtime_probe(
            r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const agent = { slug: "agents/tammy-oc", runtime: "openclaw" };
state.handoffLogEvents = [{
  agent_slug: agent.slug,
  executor_agent: agent.slug,
  event_type: "handoff_queued",
  sequence: 9,
  occurred_at: "2026-08-09T08:00:00Z",
}];
assert(agentRuntimeLabel(agent).includes("Session health unavailable"), "arbitrary handoff event became session health");
state.handoffLogEvents.push({
  agent_slug: agent.slug,
  executor_agent: agent.slug,
  event_type: "execution_started",
  sequence: 10,
  occurred_at: "2026-08-09T08:01:00Z",
});
assert(agentRuntimeLabel(agent).includes("verified fixed-session activity"), "verified run activity was not shown");
"""
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_delegation_confirmation_names_end_timezone_scope_and_exclusions(self) -> None:
        javascript = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        create_flow = javascript[
            javascript.index("async function createTemporaryDelegation"):
            javascript.index("async function changeTemporaryDelegation")
        ]
        change_flow = javascript[
            javascript.index("async function changeTemporaryDelegation"):
            javascript.index("function renderDelegationControls")
        ]
        for copy in (
            "America/Los_Angeles",
            "task status, TODOs, comments, and Artifacts",
            "No account access, external actions, trading, or scope expansion",
            "Permanent ownership will not change",
        ):
            self.assertIn(copy, create_flow)
        self.assertIn("requested new end", change_flow)
        self.assertIn("formatPacificDisplay", change_flow)
        self.assertIn("parsePacificLocalDateTime", change_flow)


if __name__ == "__main__":
    unittest.main()
