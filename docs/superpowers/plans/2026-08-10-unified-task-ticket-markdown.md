# Unified Task and Ticket Markdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce one verified Markdown contract for every newly created Mission Control Task and System Ticket, with safe external links and canonical System Ticket references that open inside Mission Control rather than Memory Stargraph.

**Architecture:** Add a pure Markdown policy module that renders canonical Task/Ticket bodies without changing authoritative structured fields. GBrain creation paths resolve referenced System Tickets by exact canonical readback, pass verified references into the renderer, and verify the stored body plus existing typed relationships. The existing safe browser renderer gains narrowly scoped bare-URL autolinking and internal `#system-ticket/<encoded slug>` navigation through the existing System Ticket loader.

**Tech Stack:** Python 3.12 standard library, GBrain adapter contracts, vanilla JavaScript/CSS, Python `unittest`, independent browser QA at `1440x1000` and genuine `390x844`.

## Global Constraints

- Apply prospectively to every new Tony, Codex Agent, OpenClaw Agent, Bible Study Task, and Mission Control System Ticket.
- Do not bulk-rewrite historical pages.
- Preserve canonical `detail`, `verbatim_request`, criteria, receipts, and typed relationships exactly; Markdown is their deterministic page-body projection.
- A referenced System Ticket must use `[Canonical title](#system-ticket/tasks%2F<uuid>)`, never a Memory Stargraph URL or hard-coded Mission Control origin.
- Resolve System Ticket references only by exact canonical page/link readback; never guess by title.
- A `tasks/<uuid>` that canonically resolves to an ordinary Task is not a
  System Ticket reference and remains ordinary text. Show `System Ticket
  unavailable` only when the surrounding author text explicitly labels a
  missing or wrong-type target as a System Ticket.
- Unsafe `javascript:`, `data:`, and `file:` URLs remain non-clickable.
- A Markdown reference never creates or infers a GBrain relationship.
- Page write, rendered-body readback, membership readback, and ownership readback must all verify before success.
- UI-affecting work remains uncommitted until independent QA explicitly returns PASS at desktop `1440x1000` and genuine mobile `390x844`.
- Preserve unrelated `.gitignore` and untracked user artifacts.

---

### Task 1: Add the pure Markdown policy

**Files:**
- Create: `gtasks/markdown_policy.py`
- Create: `tests/test_markdown_policy.py`

**Interfaces:**
- Produces: `SystemTicketReference`, `extract_system_ticket_slugs`,
  `reference_is_explicitly_labeled_system_ticket`, `system_ticket_route`,
  `render_task_body`, `render_system_ticket_body`, and
  `validate_generated_markdown`.
- Consumes: primitive strings and verified reference mappings only; it performs no GBrain I/O.

- [ ] **Step 1: Write failing policy tests**

```python
def test_task_body_preserves_detail_and_adds_verified_ticket_links(self):
    detail = "Follow tasks/fad23bf2-571f-4db0-b9f5-07ab52ae8620 exactly."
    refs = {
        "tasks/fad23bf2-571f-4db0-b9f5-07ab52ae8620":
            SystemTicketReference(
                slug="tasks/fad23bf2-571f-4db0-b9f5-07ab52ae8620",
                title="Implement an event-driven Agent Handoff Dispatcher",
            )
    }
    body = render_task_body("Continue dispatcher", detail, refs)
    self.assertIn("# Continue dispatcher\n\n## 详情\n\n" + detail, body)
    self.assertIn(
        "[Implement an event-driven Agent Handoff Dispatcher]"
        "(#system-ticket/tasks%2Ffad23bf2-571f-4db0-b9f5-07ab52ae8620)",
        body,
    )
```

Add separate tests for System Ticket section order, exact user-field
preservation, duplicate reference deduplication, explicitly labeled missing
references rendered as `System Ticket unavailable`, ordinary Task slugs left
unchanged, heading escaping, percent encoding, and unsafe generated URLs.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python3 -m unittest tests.test_markdown_policy -v`
Expected: import failure because `gtasks.markdown_policy` does not exist.

- [ ] **Step 3: Implement the minimal pure policy**

```python
@dataclass(frozen=True, slots=True)
class SystemTicketReference:
    slug: str
    title: str


TASK_SLUG_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(tasks/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12})(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
MARKDOWN_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")


def reference_is_explicitly_labeled_system_ticket(
    slug: str, values: Iterable[str]
) -> bool:
    marker = re.compile(
        rf"(?:System Ticket|系统工单)\s*[:：#-]?\s*{re.escape(slug)}",
        re.IGNORECASE,
    )
    return any(marker.search(value) for value in values)


def system_ticket_route(slug: str) -> str:
    if not TASK_SLUG_RE.fullmatch(slug):
        raise MarkdownContractError("System Ticket slug is not canonical")
    return "#system-ticket/" + quote(slug, safe="")


def validate_generated_markdown(value: str) -> None:
    for _label, target in MARKDOWN_LINK_RE.findall(value):
        if target.startswith("#system-ticket/"):
            encoded = target.removeprefix("#system-ticket/")
            if system_ticket_route(unquote(encoded)) != target:
                raise MarkdownContractError("unsafe internal Ticket route")
        else:
            parsed = urlsplit(target)
            if not (
                parsed.scheme == "https"
                or (
                    parsed.scheme == "http"
                    and parsed.hostname in {"127.0.0.1", "localhost"}
                )
            ):
                raise MarkdownContractError("unsafe generated Markdown link")
```

`render_task_body(...)` emits `# title`, `## 详情`, the exact detail, and an optional `## 关联的 System Tickets` list. `render_system_ticket_body(...)` emits the approved 用户请求, 验收标准, 关联票据, and 实施与验证记录 sections, omitting only empty optional sections.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python3 -m unittest tests.test_markdown_policy -v`
Expected: PASS.

- [ ] **Step 5: Commit the pure policy**

```bash
git add gtasks/markdown_policy.py tests/test_markdown_policy.py
git commit -m "feat: define task ticket markdown contract"
```

---

### Task 2: Enforce the policy in canonical GBrain creation paths

**Files:**
- Modify: `gtasks/gbrain.py`
- Modify: `tests/test_gbrain.py`

**Interfaces:**
- Consumes: Task/SystemTicket structured fields and Task 1 policy functions.
- Produces: `_verified_system_ticket_references(values)` and verified page-body creation for `create_task`, `create_agent_task`, QA fixture creation, and `create_system_ticket`.

- [ ] **Step 1: Write failing adapter tests**

```python
def test_create_agent_task_reads_referenced_ticket_and_stores_internal_link(self):
    task = replace(self.task(), detail="Use tasks/fad23bf2-571f-4db0-b9f5-07ab52ae8620")
    runner.add_system_ticket("tasks/fad23bf2-571f-4db0-b9f5-07ab52ae8620", "Dispatcher")
    receipt = GBrainAdapter(runner).create_agent_task(task, task.owner_agent)
    self.assertTrue(receipt.verified)
    self.assertIn(
        "[Dispatcher](#system-ticket/tasks%2Ffad23bf2-571f-4db0-b9f5-07ab52ae8620)",
        runner.page_markdown(task.slug),
    )
```

Add tests for Tony Tasks, Codex Agents, OpenClaw Agents, Bible Study parent/child Tasks, System Tickets, unavailable/wrong-type references, exact frontmatter preservation, no inferred relationship, and post-write body mismatch becoming `PartialMutationError`.

- [ ] **Step 2: Run focused adapter tests and verify RED**

Run: `python3 -m unittest tests.test_gbrain -v`
Expected: new assertions fail because page bodies still contain only the legacy title/detail projection.

- [ ] **Step 3: Add exact reference resolution**

```python
def _verified_system_ticket_references(
    self, values: Iterable[str]
) -> dict[str, SystemTicketReference | None]:
    result = {}
    for slug in extract_system_ticket_slugs("\n".join(values)):
        page = self.runner.run("get_page", {"slug": slug})
        links = self.runner.run("get_links", {"slug": slug})
        try:
            ticket = SystemTicket.from_page(page, links)
        except (DomainValidationError, GBrainError):
            if reference_is_explicitly_labeled_system_ticket(slug, values):
                result[slug] = None
            continue
        else:
            result[slug] = SystemTicketReference(ticket.slug, ticket.title)
    return result
```

Call this before every new Task/Ticket page write. Do not use search/title lookup and do not add any relationship for the reference.

- [ ] **Step 4: Replace legacy body rendering and add exact body readback**

Keep existing YAML/frontmatter fields and relationship descriptors unchanged. Replace only the body assembly with `render_task_body(...)` or `render_system_ticket_body(...)`. After `get_page`, compare `page["compiled_markdown"].strip()` to the exact expected rendered body in addition to existing domain/edge equality; a missing/non-string body or mismatch raises `PartialMutationError` after the write.

- [ ] **Step 5: Run adapter tests and verify GREEN**

Run: `python3 -m unittest tests.test_gbrain -v`
Expected: PASS.

- [ ] **Step 6: Commit adapter enforcement**

```bash
git add gtasks/gbrain.py tests/test_gbrain.py
git commit -m "feat: enforce markdown on canonical task writes"
```

---

### Task 3: Render bare URLs and internal Ticket links safely

**Files:**
- Modify: `static/app.js`
- Modify: `static/index.html`
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: Markdown emitted by Tasks 1–2.
- Produces: `safeSystemTicketMarkdownRoute`, `openMarkdownSystemTicketReference`, and safe bare-URL rendering inside `renderSafeMarkdown`.

- [ ] **Step 1: Write failing frontend contract and runtime tests**

Require the renderer to accept only `#system-ticket/tasks%2F<canonical UUID>`, reject encoded slash/backslash tricks and other fragments, keep unsafe schemes as text, autolink bare HTTPS/loopback HTTP URLs, and call the existing `loadCorrelatedSystemTicket` plus `selectSystemTicket` path. Require System Ticket request, criteria, evidence, implementation receipts, and QA receipts to use the safe Markdown renderer rather than `textContent`.

```python
self.assertIn("function safeSystemTicketMarkdownRoute", renderer)
self.assertIn("loadCorrelatedSystemTicket(ticketSlug)", renderer)
self.assertIn("selectSystemTicket(ticketSlug, originControl)", renderer)
self.assertNotIn('link.target = "_blank"', internal_ticket_branch)
```

- [ ] **Step 2: Run frontend tests and verify RED**

Run: `python3 -m unittest tests.test_frontend_contract -v`
Expected: FAIL because the safe renderer currently permits only external/Stargraph media links.

- [ ] **Step 3: Implement narrow internal navigation**

```javascript
function safeSystemTicketMarkdownRoute(value) {
  const match = String(value || "").match(/^#system-ticket\/(tasks%2F[0-9a-f-]{36})$/i);
  if (!match) return null;
  const slug = decodeURIComponent(match[1]);
  return /^tasks\/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(slug)
    ? slug : null;
}
```

The internal branch creates an anchor without `target=_blank`, prevents default navigation, reads the exact System Ticket through `loadCorrelatedSystemTicket`, opens it with `selectSystemTicket`, reports the existing unavailable error without mutation, and preserves return focus. External links retain current safe target/rel behavior.

- [ ] **Step 4: Add safe bare-URL tokenization**

Extend inline parsing without `innerHTML`. Only `https://...` and loopback `http://127.0.0.1[:port]/...` become anchors; trim trailing sentence punctuation without changing displayed source text.

Change the System Ticket request/criteria containers from `<p>` to `<div>` so
block Markdown remains valid. Update `selectSystemTicket` and
`renderSystemTicketList` to call `renderSafeMarkdown` for request, criteria,
evidence, implementation receipts, and QA receipts. Do not render raw HTML.

- [ ] **Step 5: Run frontend tests and verify GREEN**

Run: `python3 -m unittest tests.test_frontend_contract -v`
Expected: PASS.

- [ ] **Step 6: Commit frontend behavior**

```bash
git add static/app.js static/index.html tests/test_frontend_contract.py
git commit -m "feat: open markdown ticket references in mission control"
```

---

### Task 4: Keep the task-creation skill and helper contract aligned

**Files:**
- Modify: `skills/mc-add-task/SKILL.md`
- Modify: `skills/mc-add-task/scripts/mc_add_task.py`
- Create: `tests/test_mc_add_task_skill.py`

**Interfaces:**
- Consumes: the shared canonical policy from Task 1 and the installed/source skill contract.
- Produces: dry-run/readback evidence that helper-created Tony and Agent Tasks use the shared Markdown renderer.

- [ ] **Step 1: Add failing source-contract tests**

Tests assert that the source skill requires `### 用户请求`, `### 日期说明`, `### 相关链接`, canonical Ticket readback, the internal Mission Control route, and never a Stargraph link for referenced Tickets. Add helper dry-run tests for Tony and OpenClaw ownership without live writes.

- [ ] **Step 2: Run tests and verify RED for missing helper evidence**

Run: `python3 -m unittest tests.test_mc_add_task_skill -v`
Expected: source wording assertions pass, but helper output/body-policy evidence assertions fail.

- [ ] **Step 3: Add helper policy evidence**

In dry-run output include `markdown_contract: "unified-task-ticket-v1"` and `rendered_body` produced by `render_task_body`; in live output verify the read-back compiled body matches the same renderer in addition to title and edges.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python3 -m unittest tests.test_mc_add_task_skill -v`
Expected: PASS.

- [ ] **Step 5: Synchronize and verify the active skill copy**

```bash
ditto skills/mc-add-task/SKILL.md /Users/tony/.codex/skills/mc-add-task/SKILL.md
ditto skills/mc-add-task/scripts/mc_add_task.py /Users/tony/.codex/skills/mc-add-task/scripts/mc_add_task.py
shasum -a 256 skills/mc-add-task/SKILL.md /Users/tony/.codex/skills/mc-add-task/SKILL.md
shasum -a 256 skills/mc-add-task/scripts/mc_add_task.py /Users/tony/.codex/skills/mc-add-task/scripts/mc_add_task.py
```

Expected: each source/installed pair has identical hashes.

- [ ] **Step 6: Commit skill/helper alignment**

```bash
git add skills/mc-add-task tests/test_mc_add_task_skill.py
git commit -m "docs: align mc add task markdown contract"
```

---

### Task 5: Document, independently QA, and release

**Files:**
- Modify: `README.md`
- Create: `docs/runbooks/task-ticket-markdown.md`
- Modify: `gtasks/releases.json`
- Create: `docs/release-evidence/v0.0.88.md`

**Interfaces:**
- Consumes: the complete uncommitted candidate from Tasks 1–4.
- Produces: independently verified V0.0.88 release and dashboard-managed runtime evidence.

- [ ] **Step 1: Update documentation and release metadata**

Document the shared formatter, exact internal Ticket-link route, historical no-migration boundary, active skill synchronization, and verification commands. Add V0.0.88 as the next sequential release; do not rewrite older release entries.

- [ ] **Step 2: Run focused and full automated verification**

```bash
python3 -m unittest tests.test_markdown_policy tests.test_gbrain tests.test_frontend_contract tests.test_mc_add_task_skill -v
python3 -m unittest discover -s tests
node --check static/app.js
python3 -m compileall -q gtasks tests skills/mc-add-task/scripts
git diff --check
```

Expected: all commands PASS with no warnings/errors attributable to the candidate.

- [ ] **Step 3: Freeze the candidate and run independent UI/UX QA**

QA uses synthetic/read-only Task and Ticket fixtures at desktop `1440x1000` and genuine mobile `390x844`. It verifies section hierarchy, bare external links, exact internal Ticket navigation, unavailable fallback, focus restoration, safe schemes, no overflow, and zero GBrain mutations. A FAIL or INCONCLUSIVE result returns to RED/GREEN repair before commit.

- [ ] **Step 4: Commit and push only after independent PASS**

```bash
git add README.md docs/runbooks gtasks/releases.json docs/release-evidence/v0.0.88.md
git commit -m "release: ship unified task ticket markdown"
git push origin main
```

Verify `git rev-parse HEAD` equals `git rev-parse origin/main` and that unrelated user-owned files remain unstaged.

- [ ] **Step 5: Restart the dashboard-managed service and verify runtime**

Use the established All Things Codex Dashboard restart path for service `gtasks`. Verify:

```bash
curl -sS http://127.0.0.1:4179/api/health
curl -sS http://127.0.0.1:4179/api/releases
```

Expected: healthy canonical GBrain store and current version `V0.0.88`.

- [ ] **Step 6: Perform bounded production readback**

Use GET-only inspection of a newly created synthetic QA Task/System Ticket or an isolated fixture explicitly authorized for this release. Verify the stored Markdown body, typed membership/ownership, clickable external link, and internal Ticket link. Do not alter historical Tony/Agent tasks during release verification.
