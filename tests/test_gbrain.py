import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import gtasks.domain as domain
from gtasks.domain import (
    ACTIVE_ROOT,
    ARTIFACTS_ROOT,
    ARTIFACT_BY_AGENT,
    AgentProfile,
    COMPLETED_ROOT,
    EventProgress,
    GOALS_ROOT,
    PROJECTS_ROOT,
    PROPOSALS_ROOT,
    QA_FIXTURES_ROOT,
    TaskProposal,
    SYSTEM_TICKETS_ROOT,
    SystemTicket,
    Task,
    TodoItem,
    ProgressMetric,
    new_agent_artifact,
    new_goal,
    new_inbox_task,
    new_project,
    new_task,
)
import gtasks.gbrain as gbrain_module
from gtasks.markdown_policy import (
    SystemTicketReference,
    render_system_ticket_body,
    render_task_body,
)
from gtasks.handoff_dispatcher import (
    AgentRegistration,
    DurableHandoffStore,
    HandoffDispatcher,
)
from gtasks.gbrain import (
    GBrainAdapter,
    CanonicalHandoffEventBridge,
    AgentRead,
    AgentWorkRead,
    CanonicalRootError,
    CollectionIssue,
    GBrainCommandError,
    GBrainProtocolError,
    GoalLinkReceipt,
    GoalRead,
    NextActionMutationReceipt,
    PartialMutationError,
    ProposalRead,
    ProjectRead,
    RemoteHttpCommandRunner,
    LifecycleIntegrityError,
    SubprocessCommandRunner,
    _render_preserved_page,
)
from gtasks.goal_execution import (
    GoalExecutionCandidate,
    GoalExecutionPlanner,
    GoalExecutionSnapshot,
    derived_task_slug,
)
from gtasks.handoff import TaskHandoff


MARKDOWN_CONTRACT = "unified-task-ticket-v1"


def stored_page(task) -> dict:
    return {
        "slug": task.slug,
        "type": "task",
        "title": task.title,
        "compiled_truth": f"# {task.title}",
        "compiled_markdown": f"# {task.title}\n\n## 详情\n\n{task.detail}",
        "frontmatter": {
            "status": task.status,
            "summary": task.summary,
            "detail": task.detail,
            "due_day": task.due_day.isoformat(),
            "priority": task.priority,
            "next_action": task.next_action,
            "scheduled_day": "none",
            "inbox": task.inbox,
            "completed_at": None,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
            "links": [{"to": ACTIVE_ROOT, "type": "member_of"}],
        },
    }


def marked_stored_page(task) -> dict:
    page = stored_page(task)
    page["frontmatter"]["markdown_contract"] = MARKDOWN_CONTRACT
    return page


class EntityTypePreservationTests(unittest.TestCase):
    def test_generic_preserved_update_refuses_task_to_concept_downgrade(self) -> None:
        task = new_inbox_task(
            "Keep canonical task type",
            datetime(2026, 7, 31, tzinfo=timezone.utc),
            "type01",
        )
        page = stored_page(task)
        changed = deepcopy(page["frontmatter"])
        changed["type"] = "concept"

        with self.assertRaisesRegex(
            GBrainProtocolError,
            "refusing to change canonical page type",
        ):
            _render_preserved_page(page, changed)

    def test_generic_preserved_update_serializes_the_read_task_type(self) -> None:
        task = new_inbox_task(
            "Preserve canonical task type",
            datetime(2026, 7, 31, tzinfo=timezone.utc),
            "type02",
        )
        page = stored_page(task)

        content = _render_preserved_page(page, page["frontmatter"])

        self.assertIn('type: "task"', content)


class HandoffDispatcherRegistrationReadbackTests(unittest.TestCase):

    def test_bridge_preserves_owner_and_exposes_verified_claim_inputs(self) -> None:
        class RecordingDispatcher:
            registrations = ()

            def __init__(self) -> None:
                self.change = None

            def record(self, change, *, now):
                self.change = change
                return change

        dispatcher = RecordingDispatcher()
        bridge = CanonicalHandoffEventBridge(dispatcher)
        before = {
            "task_slug": "tasks/11111111-1111-4111-8111-111111111111",
            "task": {
                "slug": "tasks/11111111-1111-4111-8111-111111111111",
                "status": "planned",
                "assigned_to": ["agents/tammy"],
                "blockers": [],
            },
            "route": "hosts/tammy",
        }
        after = {
            **before,
            "todo": {
                "slug": "todos/22222222-2222-4222-8222-222222222222",
                "parent_task": "tasks/11111111-1111-4111-8111-111111111111",
            },
        }
        bridge.after_verified_mutation(
            before,
            after,
            {
                "verified": True,
                "canonical_event_id": "events/delegated-todo",
                "canonical_version": "versions/1",
                "mutation_kind": "todo_created",
                "correlation_id": "correlation-delegated-todo",
            },
            datetime(2026, 8, 4, 17, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(dispatcher.change.assigned_to, ("agents/tammy",))
        self.assertEqual(dispatcher.change.task_status, "planned")
        self.assertEqual(dispatcher.change.requested_operation, "todo")

    def test_reads_runtime_route_from_stored_registration_reference_only(self) -> None:
        registration_reference = hashlib.sha256(
            b"private-registration-tammy"
        ).hexdigest()
        runner = FakeRunner(
            {
                "get_page": [
                    {
                        "slug": "agents/tammy",
                        "type": "agent",
                        "title": "Agent Tammy",
                        "frontmatter": {
                            "handoff_dispatcher": {
                                "registration_sha256": registration_reference,
                                "route": "hosts/tammy",
                                "verified": True,
                            }
                        },
                    }
                ]
            }
        )
        adapter = GBrainAdapter(runner)
        reader = getattr(
            adapter,
            "read_handoff_dispatcher_registration_by_reference",
            None,
        )
        self.assertTrue(callable(reader), "safe registration reference reader is missing")

        registration = reader("agents/tammy", registration_reference)

        self.assertEqual(registration.agent_slug, "agents/tammy")
        self.assertEqual(registration.registration_id, registration_reference)
        self.assertEqual(registration.lease_identity, registration_reference)
        self.assertEqual(registration.reference, registration_reference)
        self.assertEqual(registration.route, "hosts/tammy")
        self.assertTrue(registration.verified)

    def test_reference_reader_rejects_mismatch_invalid_or_ambiguous_route(self) -> None:
        expected = hashlib.sha256(b"private-registration-tammy").hexdigest()
        cases = (
            {
                "registration_sha256": "0" * 64,
                "route": "hosts/tammy",
                "verified": True,
            },
            {
                "registration_sha256": expected,
                "route": "invalid route",
                "verified": True,
            },
            [
                {
                    "registration_sha256": expected,
                    "route": "hosts/tammy",
                    "verified": True,
                },
                {
                    "registration_sha256": expected,
                    "route": "hosts/timmy",
                    "verified": True,
                },
            ],
        )
        for dispatcher in cases:
            with self.subTest(dispatcher=dispatcher):
                runner = FakeRunner(
                    {
                        "get_page": [
                            {
                                "slug": "agents/tammy",
                                "type": "agent",
                                "title": "Agent Tammy",
                                "frontmatter": {"handoff_dispatcher": dispatcher},
                            }
                        ]
                    }
                )
                adapter = GBrainAdapter(runner)
                reader = getattr(
                    adapter,
                    "read_handoff_dispatcher_registration_by_reference",
                    None,
                )
                self.assertTrue(callable(reader))
                self.assertIsNone(reader("agents/tammy", expected))

    def test_reads_exact_verified_canonical_agent_registration_and_route(self) -> None:
        registration_id = "private-registration-tammy"
        runner = FakeRunner(
            {
                "get_page": [
                    {
                        "slug": "agents/tammy",
                        "type": "agent",
                        "title": "Agent Tammy",
                        "frontmatter": {
                            "handoff_dispatcher": {
                                "registration_sha256": hashlib.sha256(
                                    registration_id.encode("utf-8")
                                ).hexdigest(),
                                "route": "hosts/tammy",
                                "verified": True,
                            }
                        },
                    }
                ]
            }
        )

        registration = GBrainAdapter(runner).read_handoff_dispatcher_registration(
            "agents/tammy", registration_id
        )

        self.assertEqual(registration.agent_slug, "agents/tammy")
        self.assertEqual(registration.registration_id, registration_id)
        self.assertEqual(registration.route, "hosts/tammy")
        self.assertTrue(registration.verified)

    def test_rejects_revoked_or_mismatched_canonical_registration(self) -> None:
        registration_id = "private-registration-tammy"
        digest = hashlib.sha256(registration_id.encode("utf-8")).hexdigest()
        for dispatcher in (
            {"registration_sha256": digest, "route": "hosts/tammy", "verified": False},
            {"registration_sha256": "0" * 64, "route": "hosts/tammy", "verified": True},
            {"registration_sha256": digest, "route": "invalid route", "verified": True},
        ):
            with self.subTest(dispatcher=dispatcher):
                runner = FakeRunner(
                    {
                        "get_page": [
                            {
                                "slug": "agents/tammy",
                                "type": "agent",
                                "title": "Agent Tammy",
                                "frontmatter": {"handoff_dispatcher": dispatcher},
                            }
                        ]
                    }
                )
                self.assertIsNone(
                    GBrainAdapter(runner).read_handoff_dispatcher_registration(
                        "agents/tammy", registration_id
                    )
                )

    def test_rejects_soft_deleted_canonical_agent_registration(self) -> None:
        registration_id = "private-registration-tammy"
        runner = FakeRunner(
            {
                "get_page": [
                    {
                        "slug": "agents/tammy",
                        "type": "agent",
                        "title": "Agent Tammy",
                        "deleted_at": "2026-08-04T18:00:00Z",
                        "frontmatter": {
                            "handoff_dispatcher": {
                                "registration_sha256": hashlib.sha256(
                                    registration_id.encode("utf-8")
                                ).hexdigest(),
                                "route": "hosts/tammy",
                                "verified": True,
                            }
                        },
                    }
                ]
            }
        )

        self.assertIsNone(
            GBrainAdapter(runner).read_handoff_dispatcher_registration(
                "agents/tammy", registration_id
            )
        )


class HandoffMutationReadbackTests(unittest.TestCase):
    NOW = datetime(2026, 8, 4, 19, 0, tzinfo=timezone.utc)
    TASK = "tasks/11111111-1111-4111-8111-111111111111"
    TODO = "todos/22222222-2222-4222-8222-222222222222"

    class RecordingDispatcher:
        def __init__(self) -> None:
            self.changes = []

        def record(self, change, *, now):
            self.changes.append((change, now))
            return change

    def snapshot(self, **overrides):
        value = {
            "task_slug": self.TASK,
            "task": {
                "slug": self.TASK,
                "status": "active",
                "title": "Ship the bridge",
                "detail": "Verified canonical work.",
                "priority": "normal",
                "blockers": [],
                "assigned_to": ["agents/tammy"],
                "authorization": "granted",
                "system_dependencies": {"gbrain": "available"},
                "updated_at": self.NOW.isoformat(),
            },
            "todo": None,
            "route": "hosts/tammy",
        }
        value.update(overrides)
        return value

    def receipt(self, **overrides):
        value = {
            "verified": True,
            "canonical_event_id": "events/bridge-1",
            "canonical_version": "versions/2",
            "idempotent": False,
        }
        value.update(overrides)
        return value

    def test_normalizes_every_actionable_verified_canonical_change(self) -> None:
        todo = {
            "slug": self.TODO,
            "parent_task": self.TASK,
            "text": "Use the verified answer",
            "detail": "",
            "status": "not_done",
            "updated_at": self.NOW.isoformat(),
        }
        cases = (
            (
                self.snapshot(task={**self.snapshot()["task"], "handoff": {"state": "waiting_for_input"}}),
                self.snapshot(task={**self.snapshot()["task"], "handoff": {"state": "ready_for_agent"}}, todo={**todo, "status": "done"}),
                self.receipt(mutation_kind="answer_agent_question"),
                "answer_received",
            ),
            (self.snapshot(), self.snapshot(todo=todo), self.receipt(mutation_kind="todo_created"), "todo_added"),
            (self.snapshot(todo=todo), self.snapshot(todo={**todo, "text": "Use the final verified answer"}), self.receipt(mutation_kind="todo_edited"), "todo_materially_changed"),
            (self.snapshot(task={**self.snapshot()["task"], "status": "planned"}), self.snapshot(), self.receipt(), "task_activated"),
            (self.snapshot(task={**self.snapshot()["task"], "status": "blocked", "blockers": ["people/tony-guan"]}), self.snapshot(), self.receipt(), "blocker_resolved"),
            (self.snapshot(task={**self.snapshot()["task"], "status": "blocked", "blockers": ["systems/gbrain"], "system_dependencies": {"gbrain": "unavailable"}}), self.snapshot(), self.receipt(), "system_dependency_recovered"),
            (self.snapshot(task={**self.snapshot()["task"], "status": "proposed", "proposal_decision": None}), self.snapshot(task={**self.snapshot()["task"], "status": "planned", "proposal_decision": "approve"}), self.receipt(mutation_kind="proposal_decision"), "authorization_granted"),
            (self.snapshot(task={**self.snapshot()["task"], "assigned_to": ["agents/timmy"]}), self.snapshot(), self.receipt(), "ownership_changed"),
        )

        for before, after, receipt, expected in cases:
            with self.subTest(trigger=expected):
                dispatcher = self.RecordingDispatcher()
                change = CanonicalHandoffEventBridge(dispatcher).after_verified_mutation(
                    before, after, receipt, self.NOW
                )
                self.assertEqual(change.trigger, expected)
                self.assertEqual(change.task_slug, self.TASK)
                self.assertEqual(change.assigned_to, ("agents/tammy",))
                self.assertEqual(dispatcher.changes, [(change, self.NOW)])

    def test_unchanged_system_blocker_is_suppressed_not_recovered(self) -> None:
        task = self.snapshot()["task"]
        blocked = {
            **task,
            "status": "blocked",
            "blockers": ["systems/gbrain"],
            "system_dependencies": {"gbrain": "unavailable"},
        }
        still_blocked = {**blocked, "status": "active"}

        change = CanonicalHandoffEventBridge(
            self.RecordingDispatcher()
        ).after_verified_mutation(
            self.snapshot(task=blocked),
            self.snapshot(task=still_blocked),
            self.receipt(),
            self.NOW,
        )

        self.assertEqual(change.trigger, "unchanged_blocker")

    def test_blocked_task_reassignment_preserves_actionable_ownership_handoff(self) -> None:
        task = self.snapshot()["task"]
        before_task = {
            **task,
            "status": "blocked",
            "blockers": ["systems/gbrain"],
            "assigned_to": ["agents/timmy"],
        }
        after_task = {
            **before_task,
            "assigned_to": ["agents/tammy"],
        }
        dispatcher = self.RecordingDispatcher()

        change = CanonicalHandoffEventBridge(dispatcher).after_verified_mutation(
            self.snapshot(task=before_task),
            self.snapshot(task=after_task),
            self.receipt(),
            self.NOW,
        )

        self.assertEqual(change.trigger, "ownership_changed")
        self.assertEqual(change.assigned_to, ("agents/tammy",))
        self.assertEqual(change.route, "hosts/tammy")
        self.assertEqual(dispatcher.changes, [(change, self.NOW)])

    def test_normalizes_explicit_non_actionable_canonical_changes(self) -> None:
        task = self.snapshot()["task"]
        cases = (
            (self.snapshot(), self.snapshot(task={**task, "title": "Ship the verified bridge"}), self.receipt(), "presentation_only"),
            (self.snapshot(), self.snapshot(), self.receipt(idempotent=True), "duplicate_save"),
            (self.snapshot(), self.snapshot(task={**task, "progress_metric": {"current": 2}}), self.receipt(mutation_kind="derived_count"), "derived_count"),
            (self.snapshot(), self.snapshot(), self.receipt(mutation_kind="stale_cache_refresh"), "stale_cache_refresh"),
            (self.snapshot(task={**task, "status": "blocked", "blockers": ["people/tony-guan"]}), self.snapshot(task={**task, "status": "blocked", "blockers": ["people/tony-guan"]}), self.receipt(), "unchanged_blocker"),
        )

        for before, after, receipt, expected in cases:
            with self.subTest(trigger=expected):
                change = CanonicalHandoffEventBridge(self.RecordingDispatcher()).after_verified_mutation(
                    before, after, receipt, self.NOW
                )
                self.assertEqual(change.trigger, expected)

    def test_preserves_task_and_todo_identity_from_verified_readback(self) -> None:
        todo = {
            "slug": self.TODO,
            "parent_task": self.TASK,
            "text": "Use the verified answer",
            "detail": "",
            "status": "not_done",
            "updated_at": self.NOW.isoformat(),
        }
        change = CanonicalHandoffEventBridge(self.RecordingDispatcher()).after_verified_mutation(
            self.snapshot(),
            self.snapshot(todo=todo),
            self.receipt(mutation_kind="todo_created", canonical_event_id=self.TODO),
            self.NOW,
        )

        self.assertEqual(change.task_slug, self.TASK)
        self.assertEqual(change.canonical_event_id, self.TODO)

    def test_unverified_partial_or_ambiguous_routing_is_system_attention(self) -> None:
        cases = (
            (self.snapshot(), self.receipt(verified=False)),
            (self.snapshot(task={**self.snapshot()["task"], "assigned_to": []}), self.receipt()),
            (self.snapshot(task={**self.snapshot()["task"], "assigned_to": ["agents/tammy", "agents/timmy"]}), self.receipt()),
            (self.snapshot(route=None), self.receipt()),
        )
        for after, receipt in cases:
            with self.subTest(after=after, receipt=receipt):
                change = CanonicalHandoffEventBridge(self.RecordingDispatcher()).after_verified_mutation(
                    self.snapshot(), after, receipt, self.NOW
                )
                self.assertEqual(change.trigger, "system_attention")
                self.assertNotEqual(change.blocker, "Tony")

    def test_tony_answer_is_classified_by_semantic_effect_across_storage_shapes(self) -> None:
        task = self.snapshot()["task"]
        todo = {
            "slug": self.TODO,
            "parent_task": self.TASK,
            "text": "Tony needs to answer.",
            "detail": "Waiting for Tony.",
            "status": "not_done",
            "comments": [],
            "updated_at": self.NOW.isoformat(),
        }
        answered_todo = {
            **todo,
            "status": "done",
            "comments": [
                {
                    "author": "people/tony-guan",
                    "body": "Use CUV and keep each session to 30 minutes.",
                }
            ],
        }
        handoff_waiting = {
            "state": "waiting_for_input",
            "question_todo": self.TODO,
            "waiting_on": "people/tony-guan",
            "resume_owner": "agents/tammy",
            "resume_action": "Draft the seven-day plan.",
            "requested_at": self.NOW.isoformat(),
            "answered_at": None,
            "acknowledged_at": None,
            "round": 1,
        }
        handoff_ready = {
            **handoff_waiting,
            "state": "ready_for_agent",
            "waiting_on": None,
            "answered_at": self.NOW.isoformat(),
        }
        cases = (
            (
                self.snapshot(task={**task, "handoff": handoff_waiting}, todo=todo),
                self.snapshot(task={**task, "handoff": handoff_ready}, todo=answered_todo),
                "answer_agent_question",
            ),
            (
                self.snapshot(task={**task, "answer": ""}),
                self.snapshot(task={**task, "answer": "Use CUV and keep each session to 30 minutes."}),
                "task_answer_field_saved",
            ),
            (
                self.snapshot(task={**task, "detail": "## Answer\n\nOld answer."}),
                self.snapshot(task={**task, "detail": "## Answer\n\nUse CUV and keep each session to 30 minutes."}),
                "task_body_answer_saved",
            ),
            (
                self.snapshot(todo=todo),
                self.snapshot(todo={**todo, "detail": "Tony answer: Use CUV and keep each session to 30 minutes."}),
                "todo_answer_detail_saved",
            ),
        )

        for before, after, mutation_kind in cases:
            with self.subTest(mutation_kind=mutation_kind):
                change = CanonicalHandoffEventBridge(
                    self.RecordingDispatcher()
                ).after_verified_mutation(
                    before,
                    after,
                    self.receipt(mutation_kind=mutation_kind),
                    self.NOW,
                )
                self.assertIn(
                    change.trigger,
                    {"tony_answer_received", "waiting_for_information_updated"},
                )
                self.assertEqual(change.assigned_to, ("agents/tammy",))
                self.assertIn("answer", change.summary.lower())

    def test_tony_answer_idempotency_uses_semantic_digest_not_storage_field(self) -> None:
        handle, path = tempfile.mkstemp(prefix="handoff-answer-digest-", suffix=".sqlite3")
        os.close(handle)
        try:
            store = DurableHandoffStore(path)
            dispatcher = HandoffDispatcher(
                store,
                registrations=(
                    AgentRegistration(
                        registration_id="private-registration-tammy",
                        agent_slug="agents/tammy",
                        route="hosts/tammy",
                        verified=True,
                    ),
                ),
            )
            bridge = CanonicalHandoffEventBridge(dispatcher)
            base_task = self.snapshot()["task"]
            before = self.snapshot(task={**base_task, "answer": ""})
            first_after = self.snapshot(
                task={**base_task, "answer": "Use CUV and keep each session to 30 minutes."}
            )
            revised_after = self.snapshot(
                task={**base_task, "answer": "Use ESV and keep each session to 45 minutes."}
            )
            receipt = self.receipt(
                mutation_kind="task_answer_field_saved",
                canonical_event_id="events/same-save",
                canonical_version="versions/same",
            )

            first = bridge.after_verified_mutation(before, first_after, receipt, self.NOW)
            duplicate = bridge.after_verified_mutation(before, first_after, receipt, self.NOW)
            revised = bridge.after_verified_mutation(before, revised_after, receipt, self.NOW)

            self.assertEqual(first.handoff_id, duplicate.handoff_id)
            self.assertNotEqual(first.handoff_id, revised.handoff_id)
            self.assertEqual(store.query_events(limit=50, after_sequence=0).total, 2)
        finally:
            store.close()
            os.unlink(path)

    def test_tony_owned_task_without_agent_is_informational_not_system_attention(self) -> None:
        task = {**self.snapshot()["task"], "assigned_to": [], "owner_agent": None}

        change = CanonicalHandoffEventBridge(
            self.RecordingDispatcher()
        ).after_verified_mutation(
            self.snapshot(task={**task, "answer": ""}, route=None),
            self.snapshot(
                task={**task, "answer": "Tony recorded the decision himself."},
                route=None,
            ),
            self.receipt(mutation_kind="task_answer_field_saved"),
            self.NOW,
        )

        self.assertEqual(change.trigger, "tony_owned_no_agent")
        self.assertEqual(change.assigned_to, ())
        self.assertIn("No Agent handoff required", change.summary)


def stored_goal(slug: str, title: str) -> dict:
    return {
        "slug": slug,
        "type": "goal",
        "title": title,
        "compiled_truth": f"# {title}",
        "frontmatter": {
            "status": "planned",
            "outcome": f"{title}.",
            "success_criteria": "Define during weekly review.",
            "target_day": "2026-09-30T00:00:00.000Z",
            "strategy": "Define during weekly review.",
            "review_cadence": "weekly",
            "constraints": "Define during weekly review.",
            "collection": GOALS_ROOT,
        },
    }


def stored_project(project) -> dict:
    return {
        "slug": project.slug,
        "type": "project",
        "title": project.title,
        "compiled_truth": f"# {project.title}",
        "frontmatter": {
            "status": project.status,
            "summary": project.summary,
            "created_at": (
                project.created_at.isoformat() if project.created_at else None
            ),
            "updated_at": (
                project.updated_at.isoformat() if project.updated_at else None
            ),
            "links": [{"to": PROJECTS_ROOT, "type": "member_of"}],
        },
    }


def stored_projects_root() -> dict:
    return {
        "slug": PROJECTS_ROOT,
        "type": "collection",
        "title": "Tony's Projects",
        "compiled_truth": "# Tony's Projects",
        "frontmatter": {
            "owner": "people/tony-guan",
            "status": "active",
            "visibility": "private",
        },
    }


class FakeRunner:
    def __init__(self, responses: dict[str, list[object]]) -> None:
        self.responses = {name: list(values) for name, values in responses.items()}
        self.calls: list[tuple[str, dict]] = []

    def run(self, tool: str, params: dict) -> object:
        self.calls.append((tool, params))
        result = self.responses[tool].pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class StatefulTaskRunner:
    def __init__(self, page: dict, links: list[dict]) -> None:
        self.page = deepcopy(page)
        self.links = deepcopy(links)
        self.calls: list[tuple[str, dict]] = []

    def run(self, tool: str, params: dict) -> object:
        self.calls.append((tool, deepcopy(params)))
        if tool == "get_page":
            return deepcopy(self.page)
        if tool == "get_links":
            return deepcopy(self.links)
        if tool == "get_backlinks":
            slug = params["slug"]
            return deepcopy([
                edge for edge in self.links if edge.get("to_slug") == slug
            ])
        if tool == "put_page":
            content = params["content"]
            lines = content.splitlines()
            end = lines.index("---", 1)
            frontmatter = {}
            for line in lines[1:end]:
                key, raw = line.split(": ", 1)
                parsed_key = json.loads(key) if key.startswith('"') else key.strip()
                frontmatter[parsed_key] = json.loads(raw)
            self.page = {
                **self.page,
                "type": frontmatter.get("type"),
                "title": frontmatter.get("title", self.page.get("title")),
                "frontmatter": frontmatter,
                "compiled_truth": "\n".join(lines[end + 1 :]).lstrip(),
            }
            return {"slug": params["slug"]}
        raise AssertionError(f"unexpected tool: {tool}")


class StatefulIdentityMigrationRunner:
    """Small in-memory GBrain contract for copy/relink migration tests."""

    def __init__(self, pages: dict[str, dict], links: list[dict]) -> None:
        self.pages = deepcopy(pages)
        self.links = deepcopy(links)
        self.calls: list[tuple[str, dict]] = []

    def run(self, tool: str, params: dict) -> object:
        self.calls.append((tool, deepcopy(params)))
        if tool == "get_page":
            slug = params["slug"]
            page = self.pages.get(slug)
            if page is None or (page.get("deleted_at") and not params.get("include_deleted")):
                raise GBrainCommandError("page_not_found")
            return deepcopy(page)
        if tool == "get_links":
            return deepcopy(
                [edge for edge in self.links if edge.get("from_slug") == params["slug"]]
            )
        if tool == "get_backlinks":
            return deepcopy(
                [edge for edge in self.links if edge.get("to_slug") == params["slug"]]
            )
        if tool == "put_page":
            slug = params["slug"]
            content = params["content"]
            first = self.pages.get(slug, {})
            lines = content.splitlines()
            frontmatter: dict = {}
            if lines and lines[0] == "---" and "---" in lines[1:]:
                end = lines.index("---", 1)
                index = 1
                while index < end:
                    line = lines[index]
                    if not line or line.startswith((" ", "\t")) or ":" not in line:
                        index += 1
                        continue
                    raw_key, raw_value = line.split(":", 1)
                    key = raw_key.strip().strip('"')
                    value = raw_value.strip()
                    if key == "links" and not value:
                        parsed_links: list[dict] = []
                        index += 1
                        while index < end and lines[index].startswith("  "):
                            nested = lines[index].strip()
                            if nested.startswith("- to:"):
                                parsed_links.append(
                                    {"to": nested.split(":", 1)[1].strip().strip("'\"")}
                                )
                            elif nested.startswith("type:") and parsed_links:
                                parsed_links[-1]["type"] = nested.split(":", 1)[1].strip().strip("'\"")
                            index += 1
                        frontmatter[key] = parsed_links
                        continue
                    try:
                        frontmatter[key] = json.loads(value)
                    except json.JSONDecodeError:
                        if value in {"none", "null", "~"}:
                            frontmatter[key] = None
                        elif value in {"true", "false"}:
                            frontmatter[key] = value == "true"
                        else:
                            frontmatter[key] = value.strip("'\"")
                    index += 1
            raw_type = "concept" if content.startswith("---\ntype: goal\n") else None
            if raw_type is None:
                raw_type = frontmatter.get("type", first.get("type"))
            title = frontmatter.get("title", first.get("title"))
            self.pages[slug] = {
                **first,
                "slug": slug,
                "type": raw_type,
                "title": title,
                "compiled_truth": content,
                "compiled_markdown": content.split("\n---\n", 1)[1].strip(),
                "frontmatter": frontmatter,
                "deleted_at": None,
            }
            return {"slug": slug}
        if tool == "add_link":
            edge = {
                "from_slug": params["from"],
                "to_slug": params["to"],
                "link_type": params.get("link_type", ""),
                "context": params.get("context", ""),
                "link_source": params.get("link_source", "manual"),
            }
            if not any(
                existing.get("from_slug") == edge["from_slug"]
                and existing.get("to_slug") == edge["to_slug"]
                and existing.get("link_type") == edge["link_type"]
                for existing in self.links
            ):
                self.links.append(edge)
            return deepcopy(edge)
        if tool == "remove_link":
            self.links = [
                edge
                for edge in self.links
                if not (
                    edge.get("from_slug") == params["from"]
                    and edge.get("to_slug") == params["to"]
                    and (
                        params.get("link_type") is None
                        or edge.get("link_type") == params.get("link_type")
                    )
                    and (
                        params.get("link_source") is None
                        or edge.get("link_source") == params.get("link_source")
                    )
                )
            ]
            return {"removed": True}
        if tool == "delete_page":
            slug = params["slug"]
            if slug not in self.pages:
                raise GBrainCommandError("page_not_found")
            self.pages[slug]["deleted_at"] = "2026-08-01T12:00:00Z"
            self.links = [
                edge
                for edge in self.links
                if edge.get("from_slug") != slug and edge.get("to_slug") != slug
            ]
            return {"slug": slug, "deleted": True}
        raise AssertionError(f"unexpected tool: {tool}")


class GoalExecutionStateRunner(StatefulIdentityMigrationRunner):
    """Remote-MCP-shaped in-memory runner for Goal execution mutations."""

    def run(self, tool: str, params: dict) -> object:
        if tool == "list_pages":
            self.calls.append((tool, deepcopy(params)))
            requested_type = params.get("type")
            return {
                "pages": [
                    deepcopy(page)
                    for page in self.pages.values()
                    if requested_type is None or page.get("type") == requested_type
                ]
            }
        return super().run(tool, params)


class GoalExecutionAdapterTests(unittest.TestCase):
    GOAL = "goals/41fb50e0-e1d7-592b-b2c3-ff1f7aacff10"
    PROJECT = "projects/97b3214e-53d3-5506-beb1-0705816484f9"
    AGENT = "agents/timmy"
    WORK_ROOT = "collections/timmys-tasks"
    NOW = datetime(2026, 8, 23, 16, 0, tzinfo=timezone.utc)

    def _goal(self) -> domain.Goal:
        return domain.Goal(
            slug=self.GOAL,
            title="Civic: Help California be better through political action",
            status="planned",
            outcome="Help California be better through political action.",
            success_criteria="Maintain evidence-backed civic progress.",
            target_day=date(2026, 12, 31),
            strategy="Choose one bounded next step from current evidence.",
            review_cadence="weekly",
            constraints="No external action without Tony approval.",
        )

    def _project(self) -> domain.Project:
        return domain.Project(
            slug=self.PROJECT,
            title="ERFA PAC",
            status="active",
            summary="Maintain an internal evidence and next-action ledger.",
            supporting_goal_slugs=(self.GOAL,),
        )

    def _agent(self, *, slug: str | None = None) -> AgentProfile:
        agent_slug = slug or self.AGENT
        work_roots = {
            "agents/tammy": "collections/tammys-tasks",
            "agents/timmy": self.WORK_ROOT,
            "agents/toddy": "collections/toddys-tasks",
        }
        return AgentProfile(
            slug=agent_slug,
            name=agent_slug.removeprefix("agents/").title(),
            title="Agent Timmy",
            summary="Civic and systems research.",
            work_root=work_roots[agent_slug],
            default_goal_slugs=(self.GOAL,),
            runtime="codex",
        )

    def _candidate(self) -> GoalExecutionCandidate:
        decision = GoalExecutionPlanner().plan(
            GoalExecutionSnapshot(
                goals=(self._goal(),),
                projects=(self._project(),),
                agents=(self._agent(),),
                tasks=(),
                route_health={self.AGENT: True},
            )
        ).decisions[0]
        self.assertEqual(decision.reason, "auto_eligible")
        assert decision.candidate is not None
        return decision.candidate

    def _runner(self) -> GoalExecutionStateRunner:
        pages = {
            GOALS_ROOT: {
                "slug": GOALS_ROOT,
                "type": "collection",
                "title": "Tony's Goals",
                "frontmatter": {},
                "compiled_truth": "# Tony's Goals",
            },
            PROJECTS_ROOT: stored_projects_root(),
            self.WORK_ROOT: {
                "slug": self.WORK_ROOT,
                "type": "collection",
                "title": "Timmy Tasks",
                "frontmatter": {},
                "compiled_truth": "# Timmy Tasks",
            },
            self.AGENT: {
                "slug": self.AGENT,
                "type": "agent",
                "title": "Agent Timmy",
                "compiled_truth": "Civic and systems research.",
                "frontmatter": {
                    "runtime": "codex",
                    "work_root": self.WORK_ROOT,
                },
            },
            self.GOAL: stored_goal(self.GOAL, self._goal().title),
            self.PROJECT: stored_project(self._project()),
        }
        links = [
            {
                "from_slug": self.GOAL,
                "to_slug": GOALS_ROOT,
                "link_type": "member_of",
                "link_source": "gtasks",
            },
            {
                "from_slug": self.PROJECT,
                "to_slug": PROJECTS_ROOT,
                "link_type": "member_of",
                "link_source": "gtasks",
            },
            {
                "from_slug": self.PROJECT,
                "to_slug": self.GOAL,
                "link_type": "supports_goal",
                "link_source": "gtasks",
            },
            {
                "from_slug": self.AGENT,
                "to_slug": self.GOAL,
                "link_type": "default_agent_for",
                "link_source": "gtasks",
            },
        ]
        return GoalExecutionStateRunner(pages, links)


    def test_snapshot_hydrates_waiting_question_todo_for_goal_execution_summary(self) -> None:
        adapter = GBrainAdapter(self._runner())
        waiting = replace(
            new_task(
                title="Prepare family-care goal map",
                detail="Bounded read-only work.",
                now=self.NOW,
                identity="waiting-question-goal-work",
            ),
            status="blocked",
            lifecycle_root=self.WORK_ROOT,
            owner_agent=self.AGENT,
            handoff=TaskHandoff(
                state="waiting_for_input",
                waiting_on="people/tony-guan",
                question_todo="todos/question",
                resume_owner=self.AGENT,
                resume_action="Use Tony's answer.",
                requested_at=self.NOW,
                answered_at=None,
                acknowledged_at=None,
                round=1,
            ),
        )
        question = TodoItem(
            slug="todos/question",
            parent_task=waiting.slug,
            text="Which family-care scope should Toddy use next?",
            detail="Choose the scope and first bounded action.",
            status="not_done",
            kind="question",
            created_at=self.NOW,
            updated_at=self.NOW,
            creator=self.AGENT,
            source="agent",
        )
        adapter.list_goals = lambda: GoalRead((self._goal(),), ())
        adapter.list_projects = lambda: ProjectRead((self._project(),), ())
        adapter.list_agent_profiles = lambda: AgentRead(
            (
                self._agent(slug="agents/tammy"),
                self._agent(),
                self._agent(slug="agents/toddy"),
            ),
            (),
        )
        adapter.list_agent_work = lambda **_kwargs: AgentWorkRead(
            (waiting.to_dict(),), (), (self.WORK_ROOT,)
        )
        adapter.get_task = lambda slug: waiting if slug == waiting.slug else None
        adapter._list_task_todos_for_task = lambda task, **_kwargs: gbrain_module.TodoRead(
            (question,), ()
        )

        snapshot = adapter.read_goal_execution_snapshot({self.AGENT: True})

        self.assertEqual(snapshot.tasks[0].todos, (question,))

    def test_snapshot_fails_closed_when_goals_root_is_missing(self) -> None:
        runner = self._runner()
        adapter = GBrainAdapter(runner)
        adapter.list_goals = lambda: GoalRead(
            (),
            (
                CollectionIssue(
                    slug=GOALS_ROOT,
                    message="page_not_found",
                    category="canonical_root_data",
                ),
            ),
        )

        with self.assertRaises(CanonicalRootError) as raised:
            adapter.read_goal_execution_snapshot({self.AGENT: True})

        self.assertEqual(raised.exception.roots, (GOALS_ROOT,))
        self.assertFalse(any(tool == "put_page" for tool, _ in runner.calls))

    def test_snapshot_does_not_abort_for_non_visible_agent_work_issue(self) -> None:
        adapter = GBrainAdapter(self._runner())
        adapter.list_goals = lambda: GoalRead((self._goal(),), ())
        adapter.list_projects = lambda: ProjectRead((self._project(),), ())
        adapter.list_agent_profiles = lambda: AgentRead(
            (
                self._agent(slug="agents/tammy"),
                self._agent(),
                self._agent(slug="agents/toddy"),
            ),
            (),
        )
        adapter.list_agent_work = lambda **_kwargs: AgentWorkRead(
            (),
            (
                CollectionIssue(
                    slug="tasks/malformed-handoff",
                    message="waiting_for_input requires blocked task status",
                    category="core_data",
                    task_visible=False,
                    owner_agent="agents/tammy",
                ),
            ),
            (self.WORK_ROOT,),
        )

        snapshot = adapter.read_goal_execution_snapshot({self.AGENT: True})

        self.assertEqual(snapshot.goals, (self._goal(),))
        self.assertEqual(snapshot.tasks, ())

    def test_snapshot_still_fails_closed_for_agent_work_root_issue(self) -> None:
        adapter = GBrainAdapter(self._runner())
        adapter.list_goals = lambda: GoalRead((self._goal(),), ())
        adapter.list_projects = lambda: ProjectRead((self._project(),), ())
        adapter.list_agent_profiles = lambda: AgentRead(
            (
                self._agent(slug="agents/tammy"),
                self._agent(),
                self._agent(slug="agents/toddy"),
            ),
            (),
        )
        adapter.list_agent_work = lambda **_kwargs: AgentWorkRead(
            (),
            (
                CollectionIssue(
                    slug=self.WORK_ROOT,
                    message="root read failed",
                    category="core_data",
                    task_visible=False,
                    owner_agent=self.AGENT,
                ),
            ),
            (self.WORK_ROOT,),
        )

        with self.assertRaises(CanonicalRootError) as raised:
            adapter.read_goal_execution_snapshot({self.AGENT: True})

        self.assertEqual(raised.exception.roots, (self.WORK_ROOT,))

    def test_create_derived_task_writes_planned_then_verifies_all_edges(self) -> None:
        runner = self._runner()
        candidate = self._candidate()

        receipt = GBrainAdapter(runner).create_or_adopt_derived_agent_task(
            candidate,
            self.NOW,
        )

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.task.status, "planned")
        self.assertEqual(receipt.task.slug, derived_task_slug(candidate.fingerprint))
        self.assertEqual(
            receipt.task.goal_derivation.fingerprint,
            candidate.fingerprint,
        )
        typed_edges = {
            (edge["from_slug"], edge["to_slug"], edge["link_type"])
            for edge in runner.links
        }
        self.assertTrue(
            {
                (receipt.task.slug, self.WORK_ROOT, "member_of"),
                (receipt.task.slug, self.AGENT, "assigned_to"),
                (receipt.task.slug, self.GOAL, "advances_goal"),
                (receipt.task.slug, self.PROJECT, "member_of"),
                (self.GOAL, receipt.task.slug, "advanced_by"),
            }.issubset(typed_edges)
        )

    def test_create_derived_task_adopts_exact_same_slug_after_partial_write(self) -> None:
        runner = self._runner()
        adapter = GBrainAdapter(runner)
        candidate = self._candidate()
        first = adapter.create_or_adopt_derived_agent_task(candidate, self.NOW)
        runner.links = [
            edge
            for edge in runner.links
            if not (
                edge.get("from_slug") == first.task_slug
                and edge.get("link_type") == "assigned_to"
            )
        ]
        runner.calls.clear()

        adopted = adapter.create_or_adopt_derived_agent_task(candidate, self.NOW)

        self.assertEqual(adopted.task_slug, first.task_slug)
        self.assertFalse(any(tool == "put_page" for tool, _ in runner.calls))
        add_calls = [params for tool, params in runner.calls if tool == "add_link"]
        self.assertEqual(
            add_calls,
            [
                {
                    "from": first.task_slug,
                    "to": self.AGENT,
                    "link_type": "assigned_to",
                    "context": "Tony explicitly assigned this work to the agent.",
                    "link_source": "gtasks",
                }
            ],
        )

    def test_create_derived_task_rejects_existing_same_slug_with_other_receipt(self) -> None:
        runner = self._runner()
        adapter = GBrainAdapter(runner)
        candidate = self._candidate()
        first = adapter.create_or_adopt_derived_agent_task(candidate, self.NOW)
        runner.pages[first.task_slug]["frontmatter"]["goal_derivation"][
            "fingerprint"
        ] = "b" * 64
        runner.calls.clear()

        with self.assertRaises(PartialMutationError):
            adapter.create_or_adopt_derived_agent_task(candidate, self.NOW)

        self.assertFalse(
            any(
                tool in {"put_page", "add_link", "remove_link"}
                for tool, _ in runner.calls
            )
        )

    def test_create_derived_task_never_links_tony_or_proposed_roots(self) -> None:
        runner = self._runner()
        receipt = GBrainAdapter(runner).create_or_adopt_derived_agent_task(
            self._candidate(),
            self.NOW,
        )

        forbidden = {ACTIVE_ROOT, PROPOSALS_ROOT, "collections/tonys-tasks"}
        task_edges = [
            edge for edge in runner.links if edge.get("from_slug") == receipt.task_slug
        ]
        self.assertFalse(
            forbidden.intersection(
                str(edge.get("to_slug")) for edge in task_edges
            )
        )
        self.assertFalse(
            forbidden.intersection(
                str(link.get("to"))
                for link in runner.pages[receipt.task_slug]["frontmatter"]["links"]
            )
        )

    def test_create_derived_task_concurrent_retries_write_one_page(self) -> None:
        runner = self._runner()
        adapter = GBrainAdapter(runner)
        candidate = self._candidate()

        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = tuple(
                pool.map(
                    lambda _index: adapter.create_or_adopt_derived_agent_task(
                        candidate,
                        self.NOW,
                    ),
                    range(2),
                )
            )

        self.assertEqual(
            {receipt.task_slug for receipt in receipts},
            {derived_task_slug(candidate.fingerprint)},
        )
        self.assertEqual(
            sum(
                1
                for tool, params in runner.calls
                if tool == "put_page"
                and params.get("slug") == derived_task_slug(candidate.fingerprint)
            ),
            1,
        )

    def test_create_derived_task_restart_adopts_original_creation_time(self) -> None:
        runner = self._runner()
        candidate = self._candidate()
        first = GBrainAdapter(runner).create_or_adopt_derived_agent_task(
            candidate,
            self.NOW,
        )
        runner.calls.clear()

        adopted = GBrainAdapter(runner).create_or_adopt_derived_agent_task(
            candidate,
            self.NOW + timedelta(days=1),
        )

        self.assertEqual(adopted.task_slug, first.task_slug)
        self.assertEqual(adopted.task.created_at, first.task.created_at)
        self.assertEqual(adopted.task.due_day, first.task.due_day)
        self.assertFalse(any(tool == "put_page" for tool, _ in runner.calls))




def stored_artifact(artifact) -> dict:
    links = [
        {"to": artifact.agent_collection, "type": "member_of"},
        {"to": artifact.created_by, "type": "created_by"},
        {"to": artifact.produced_for, "type": "produced_for"},
    ]
    if artifact.project:
        links.append({"to": artifact.project, "type": "supports_project"})
    if artifact.goal:
        links.append({"to": artifact.goal, "type": "supports_goal"})
    if artifact.supersedes:
        links.append({"to": artifact.supersedes, "type": "supersedes"})
    return {
        "slug": artifact.slug,
        "type": "concept",
        "title": artifact.title,
        "compiled_markdown": artifact.markdown,
        "frontmatter": {
            "type": "artifact",
            "title": artifact.title,
            "artifact_kind": artifact.artifact_kind,
            "created_by": artifact.created_by,
            "produced_for": artifact.produced_for,
            "attachments": list(artifact.attachments),
            "git_url": artifact.git_url,
            "created_at": artifact.created_at.isoformat(),
            "links": links,
        },
    }


def artifact_edges(artifact) -> list[dict]:
    edges = [
        {"from_slug": artifact.slug, "to_slug": artifact.agent_collection, "link_type": "member_of"},
        {"from_slug": artifact.slug, "to_slug": artifact.created_by, "link_type": "created_by"},
        {"from_slug": artifact.slug, "to_slug": artifact.produced_for, "link_type": "produced_for"},
    ]
    if artifact.project:
        edges.append({"from_slug": artifact.slug, "to_slug": artifact.project, "link_type": "supports_project"})
    if artifact.goal:
        edges.append({"from_slug": artifact.slug, "to_slug": artifact.goal, "link_type": "supports_goal"})
    return edges


def authorized_artifact_task(artifact) -> tuple[dict, list[dict]]:
    work_root = dict(domain.AGENT_SCOPES)[artifact.created_by]
    page = {
        "slug": artifact.produced_for,
        "type": "task",
        "title": "Authorized Artifact task",
        "compiled_truth": "# Authorized Artifact task",
        "frontmatter": {
            "type": "task",
            "title": "Authorized Artifact task",
            "summary": "Authorized Artifact task",
            "detail": "Produce the durable deliverable.",
            "status": "active",
            "priority": "normal",
            "next_action": "Publish the verified Artifact.",
            "due_day": "2026-08-02",
            "scheduled_day": "none",
            "inbox": False,
            "links": [{"to": work_root, "type": "member_of"}],
        },
    }
    edges = [
        {
            "from_slug": artifact.produced_for,
            "to_slug": work_root,
            "link_type": "member_of",
        },
        {
            "from_slug": artifact.produced_for,
            "to_slug": artifact.created_by,
            "link_type": "assigned_to",
        },
    ]
    return page, edges


class StatefulArtifactRunner(StatefulIdentityMigrationRunner):
    def run(self, tool: str, params: dict) -> object:
        result = super().run(tool, params)
        if tool == "put_page" and params["slug"].startswith("artifacts/"):
            content = params["content"]
            lines = content.splitlines()
            end = lines.index("---", 1)
            self.pages[params["slug"]]["type"] = "concept"
            self.pages[params["slug"]]["compiled_markdown"] = "\n".join(
                lines[end + 1 :]
            ).strip()
        return result






class CanonicalIdentityMigrationTests(unittest.TestCase):
    def _fixture(self) -> tuple[StatefulIdentityMigrationRunner, dict[str, str]]:
        goal_slug = "goals/health-label"
        project_slug = "projects/wellbeing-plan"
        task_slug = "collections/toddys-tasks/weekly-walk"
        excluded_slug = "tasks/deleted-erfa"
        pages = {
            goal_slug: {
                "slug": goal_slug,
                "type": "concept",
                "title": "Health: Be healthier",
                "compiled_truth": "\n".join(
                    [
                        "---",
                        "type: goal",
                        "title: 'Health: Be healthier'",
                        "status: planned",
                        "outcome: Be healthier.",
                        "success_criteria: Walk weekly.",
                        "target_day: '2026-09-30'",
                        "strategy: Start small.",
                        "review_cadence: weekly",
                        "constraints: Preserve history.",
                        f"collection: {GOALS_ROOT}",
                        "---",
                        "",
                        "# Health: Be healthier",
                        "",
                        f"Legacy reference `{task_slug}` must remain resolvable.",
                    ]
                ),
                "frontmatter": {"source_kind": "fixture"},
                "deleted_at": None,
            },
            project_slug: {
                "slug": project_slug,
                "type": "project",
                "title": "Wellbeing plan",
                "compiled_truth": "\n".join(
                    [
                        "---",
                        "type: project",
                        "title: Wellbeing plan",
                        "status: active",
                        "summary: Preserve this project body.",
                        "links:",
                        f"  - to: {PROJECTS_ROOT}",
                        "    type: member_of",
                        "---",
                        "",
                        "# Wellbeing plan",
                    ]
                ),
                "frontmatter": {
                    "status": "active",
                    "summary": "Preserve this project body.",
                    "links": [
                        {"to": PROJECTS_ROOT, "type": "member_of"}
                    ],
                    "source_kind": "fixture",
                },
                "deleted_at": None,
            },
            task_slug: {
                "slug": task_slug,
                "type": "task",
                "title": "Weekly walk",
                "compiled_truth": "\n".join(
                    [
                        "---",
                        "type: task",
                        "title: Weekly walk",
                        "status: planned",
                        "summary: Weekly walk",
                        "detail: Keep original task detail.",
                        "priority: normal",
                        "next_action: Put shoes by the door",
                        "due_day: '2026-08-02'",
                        "scheduled_day: none",
                        "inbox: false",
                        "next_action_history: [{\"action\": \"Old step\", \"completed_at\": \"2026-08-01T08:00:00-07:00\"}]",
                        "progress_metric: null",
                        "event_progress: null",
                        "links:",
                        "  - to: collections/toddys-tasks",
                        "    type: member_of",
                        "  - to: agents/toddy",
                        "    type: assigned_to",
                        "---",
                        "",
                        "# Weekly walk",
                        "",
                        "Keep original task detail.",
                    ]
                ),
                "frontmatter": {
                    "status": "planned",
                    "summary": "Weekly walk",
                    "detail": "Keep original task detail.",
                    "priority": "normal",
                    "next_action": "Put shoes by the door",
                    "due_day": "2026-08-02",
                    "scheduled_day": None,
                    "inbox": False,
                    "next_action_history": [
                        {
                            "action": "Old step",
                            "completed_at": "2026-08-01T08:00:00-07:00",
                        }
                    ],
                    "progress_metric": None,
                    "event_progress": None,
                    "links": [
                        {"to": "collections/toddys-tasks", "type": "member_of"},
                        {"to": "agents/toddy", "type": "assigned_to"},
                    ],
                    "source_kind": "fixture",
                },
                "deleted_at": None,
            },
            excluded_slug: {
                "slug": excluded_slug,
                "type": "task",
                "title": "Deleted ERFA",
                "compiled_truth": "---\ntype: task\n---\n\n# Deleted ERFA",
                "frontmatter": {"source_kind": "fixture"},
                "deleted_at": "2026-07-30T12:00:00Z",
            },
        }
        links = [
            {"from_slug": goal_slug, "to_slug": GOALS_ROOT, "link_type": "", "context": "", "link_source": "manual"},
            {"from_slug": project_slug, "to_slug": PROJECTS_ROOT, "link_type": "member_of", "context": "Scoped project", "link_source": "gtasks"},
            {"from_slug": project_slug, "to_slug": PROJECTS_ROOT, "link_type": "", "context": "Legacy project scope", "link_source": "manual"},
            {"from_slug": task_slug, "to_slug": "collections/toddys-tasks", "link_type": "member_of", "context": "Agent task", "link_source": "gtasks"},
            {"from_slug": task_slug, "to_slug": "collections/toddys-tasks", "link_type": "", "context": "Legacy agent scope", "link_source": "manual"},
            {"from_slug": task_slug, "to_slug": "agents/toddy", "link_type": "assigned_to", "context": "Agent owner", "link_source": "gtasks"},
            {"from_slug": task_slug, "to_slug": goal_slug, "link_type": "advances_goal", "context": "Task goal", "link_source": "markdown"},
            {"from_slug": goal_slug, "to_slug": task_slug, "link_type": "advanced_by", "context": "Goal task", "link_source": "gtasks"},
            {"from_slug": project_slug, "to_slug": goal_slug, "link_type": "supports_goal", "context": "Project goal", "link_source": "gtasks"},
            {"from_slug": "agents/toddy", "to_slug": goal_slug, "link_type": "default_agent_for", "context": "Default owner", "link_source": "gtasks"},
        ]
        mapping = {
            goal_slug: "goals/d175890b-6e89-5543-b587-b5df345c1c81",
            project_slug: "projects/65c2f720-fb49-5403-9a9e-76228e285277",
            task_slug: "tasks/6a52932a-e645-5aaa-b14a-44fc83d9337c",
        }
        return StatefulIdentityMigrationRunner(pages, links), mapping

    def test_audit_is_read_only_and_reports_goal_membership_repair(self) -> None:
        runner, mapping = self._fixture()

        audit = GBrainAdapter(runner).audit_canonical_identity_migration(
            mapping,
            excluded=("tasks/deleted-erfa",),
        )

        self.assertEqual(audit["entity_count"], 3)
        self.assertEqual(audit["goal_membership_repairs"], ["goals/health-label"])
        self.assertEqual(audit["excluded"], ["tasks/deleted-erfa"])
        self.assertTrue(all(item["content_sha256"] for item in audit["entities"]))
        self.assertFalse(
            {"put_page", "add_link", "remove_link"}
            & {tool for tool, _params in runner.calls}
        )

    def test_matches_real_gbrain_normalized_task_and_project_readback(self) -> None:
        runner, mapping = self._fixture()
        adapter = GBrainAdapter(runner)

        for old_slug in (
            "projects/wellbeing-plan",
            "collections/toddys-tasks/weekly-walk",
        ):
            source = runner.pages[old_slug]
            # Model the live source contract where GBrain exposes structured
            # frontmatter separately and only the Markdown body as compiled_truth.
            compiled = source["compiled_truth"]
            closing = compiled.find("\n---\n", 4)
            source["compiled_truth"] = compiled[closing + len("\n---\n\n") :]
            source["frontmatter"] = {
                "status": "active" if old_slug.startswith("projects/") else "planned",
                "summary": source["title"],
                "links": [
                    {
                        "to": PROJECTS_ROOT
                        if old_slug.startswith("projects/")
                        else "collections/toddys-tasks",
                        "type": "member_of",
                    }
                ],
            }
            expected = adapter._migration_page_content(source, mapping)
            parsed = adapter._parse_migration_rendered_content(expected)
            self.assertIsNotNone(parsed)
            expected_frontmatter, expected_body = parsed  # type: ignore[misc]
            normalized = {
                "slug": mapping[old_slug],
                "type": expected_frontmatter.pop("type"),
                "title": expected_frontmatter.pop("title"),
                "frontmatter": expected_frontmatter,
                "compiled_truth": expected_body,
                "deleted_at": None,
            }

            self.assertTrue(adapter._migration_destination_matches(normalized, expected))

    def test_normalized_destination_mismatch_fails_closed(self) -> None:
        runner, mapping = self._fixture()
        adapter = GBrainAdapter(runner)
        source = runner.pages["collections/toddys-tasks/weekly-walk"]
        compiled = source["compiled_truth"]
        closing = compiled.find("\n---\n", 4)
        source["compiled_truth"] = compiled[closing + len("\n---\n\n") :]
        source["frontmatter"] = {
            "status": "planned",
            "summary": "Weekly walk",
            "links": [
                {"to": "collections/toddys-tasks", "type": "member_of"}
            ],
        }
        expected = adapter._migration_page_content(source, mapping)
        parsed = adapter._parse_migration_rendered_content(expected)
        self.assertIsNotNone(parsed)
        expected_frontmatter, expected_body = parsed  # type: ignore[misc]
        destination = {
            "slug": mapping["collections/toddys-tasks/weekly-walk"],
            "type": expected_frontmatter.pop("type"),
            "title": expected_frontmatter.pop("title"),
            "frontmatter": expected_frontmatter,
            "compiled_truth": expected_body,
            "deleted_at": None,
        }

        wrong_nested = deepcopy(destination)
        wrong_nested["frontmatter"]["links"][0]["to"] = "collections/timmys-tasks"
        wrong_body = deepcopy(destination)
        wrong_body["compiled_truth"] += "\nchanged"
        wrong_title = deepcopy(destination)
        wrong_title["title"] = "Changed label"

        self.assertFalse(adapter._migration_destination_matches(wrong_nested, expected))
        self.assertFalse(adapter._migration_destination_matches(wrong_body, expected))
        self.assertFalse(adapter._migration_destination_matches(wrong_title, expected))

    def test_copies_relinks_aliases_and_repairs_goal_membership(self) -> None:
        runner, mapping = self._fixture()

        adapter = GBrainAdapter(runner)
        receipt = adapter.migrate_canonical_identities(
            mapping,
            excluded=("tasks/deleted-erfa",),
        )

        self.assertTrue(receipt.verified)
        self.assertEqual(set(receipt.migrated), set(mapping.values()))
        self.assertEqual(receipt.excluded, ("tasks/deleted-erfa",))
        self.assertEqual(runner.pages["tasks/deleted-erfa"]["deleted_at"], "2026-07-30T12:00:00Z")
        self.assertNotIn("tasks/deleted-erfa", mapping)
        for old_slug, new_slug in mapping.items():
            self.assertIn(new_slug, runner.pages)
            self.assertIn((old_slug, new_slug, "canonical_alias_of"), {
                (edge["from_slug"], edge["to_slug"], edge["link_type"])
                for edge in runner.links
            })
        new_goal = mapping["goals/health-label"]
        new_project = mapping["projects/wellbeing-plan"]
        new_task = mapping["collections/toddys-tasks/weekly-walk"]
        edge_keys = {
            (edge["from_slug"], edge["to_slug"], edge["link_type"])
            for edge in runner.links
        }
        self.assertIn((new_goal, GOALS_ROOT, "member_of"), edge_keys)
        self.assertIn((new_task, new_goal, "advances_goal"), edge_keys)
        self.assertIn((new_goal, new_task, "advanced_by"), edge_keys)
        self.assertIn((new_project, new_goal, "supports_goal"), edge_keys)
        self.assertIn(("agents/toddy", new_goal, "default_agent_for"), edge_keys)
        self.assertNotIn(("goals/health-label", GOALS_ROOT, ""), edge_keys)
        self.assertNotIn((new_project, PROJECTS_ROOT, ""), edge_keys)
        self.assertNotIn((new_task, "collections/toddys-tasks", ""), edge_keys)
        for slug, root in (
            (new_goal, GOALS_ROOT),
            (new_project, PROJECTS_ROOT),
            (new_task, "collections/toddys-tasks"),
        ):
            self.assertEqual(
                len(
                    [
                        edge
                        for edge in runner.links
                        if edge["from_slug"] == slug
                        and edge["to_slug"] == root
                        and edge["link_type"] == "member_of"
                    ]
                ),
                1,
            )
        self.assertIn("Keep original task detail.", runner.pages[new_task]["compiled_truth"])
        self.assertEqual(
            runner.pages[new_task]["frontmatter"]["next_action_history"],
            [
                {
                    "action": "Old step",
                    "completed_at": "2026-08-01T08:00:00-07:00",
                }
            ],
        )
        self.assertIn(
            "Legacy reference `collections/toddys-tasks/weekly-walk` must remain resolvable.",
            runner.pages[new_goal]["compiled_truth"],
        )
        self.assertEqual([goal.slug for goal in adapter.list_goals().goals], [new_goal])
        self.assertEqual(adapter.list_goals().issues, ())
        self.assertEqual([project.slug for project in adapter.list_projects().projects], [new_project])
        relationship = adapter.read_goal_relationships("goals/health-label")
        self.assertEqual(relationship.goal_slug, new_goal)
        self.assertEqual(relationship.task_slugs, (new_task,))
        self.assertNotIn(
            "markdown",
            [
                params.get("link_source")
                for tool, params in runner.calls
                if tool == "add_link"
            ],
        )

    def test_legacy_task_slug_resolves_to_new_canonical_task(self) -> None:
        runner, mapping = self._fixture()
        adapter = GBrainAdapter(runner)
        adapter.migrate_canonical_identities(mapping)

        task = adapter.get_task("collections/toddys-tasks/weekly-walk")

        self.assertEqual(task.slug, mapping["collections/toddys-tasks/weekly-walk"])

    def test_rejects_namespace_changes_and_non_uuid_targets_before_writing(self) -> None:
        runner, _mapping = self._fixture()

        with self.assertRaisesRegex(ValueError, "same namespace"):
            GBrainAdapter(runner).migrate_canonical_identities(
                {"collections/toddys-tasks/weekly-walk": "projects/65c2f720-fb49-5403-9a9e-76228e285277"}
            )
        with self.assertRaisesRegex(ValueError, "opaque UUID"):
            GBrainAdapter(runner).migrate_canonical_identities(
                {"collections/toddys-tasks/weekly-walk": "tasks/still-title-derived"}
            )
        self.assertNotIn("put_page", [tool for tool, _ in runner.calls])

    def test_stops_before_retiring_edges_when_a_source_changes_during_copy(self) -> None:
        base, mapping = self._fixture()
        source_slug = "goals/health-label"

        class ConcurrentSourceRunner(StatefulIdentityMigrationRunner):
            def __init__(self) -> None:
                super().__init__(base.pages, base.links)
                self.source_reads = 0

            def run(self, tool: str, params: dict) -> object:
                result = super().run(tool, params)
                if tool == "get_page" and params.get("slug") == source_slug:
                    self.source_reads += 1
                    if self.source_reads > 1:
                        result["content_hash"] = "concurrent-change"
                return result

        runner = ConcurrentSourceRunner()

        with self.assertRaisesRegex(PartialMutationError, "changed during migration"):
            GBrainAdapter(runner).migrate_canonical_identities(mapping)

        self.assertIn(
            (source_slug, GOALS_ROOT, ""),
            {
                (edge["from_slug"], edge["to_slug"], edge["link_type"])
                for edge in runner.links
            },
        )

    def test_resumes_only_when_partial_destination_content_matches_plan(self) -> None:
        runner, mapping = self._fixture()
        adapter = GBrainAdapter(runner)
        for old_slug, new_slug in mapping.items():
            runner.run(
                "put_page",
                {
                    "slug": new_slug,
                    "content": adapter._migration_page_content(runner.pages[old_slug], mapping),
                },
            )
        runner.calls.clear()

        receipt = adapter.migrate_canonical_identities(mapping)

        self.assertTrue(receipt.verified)
        self.assertNotIn("put_page", [tool for tool, _params in runner.calls])

    def test_rejects_body_only_partial_destination(self) -> None:
        runner, mapping = self._fixture()
        adapter = GBrainAdapter(runner)
        old_slug = "projects/wellbeing-plan"
        new_slug = mapping[old_slug]
        runner.pages[new_slug] = {
            "slug": new_slug,
            "type": "concept",
            "title": "Incomplete migration residue",
            "compiled_truth": runner.pages[old_slug]["compiled_truth"].split(
                "\n---\n", 1
            )[1],
            "frontmatter": {},
            "deleted_at": None,
        }

        with self.assertRaisesRegex(PartialMutationError, "does not semantically match"):
            adapter.migrate_canonical_identities(mapping)

        self.assertNotIn("put_page", [tool for tool, _params in runner.calls])


class CollectionReadTests(unittest.TestCase):
    def test_empty_canonical_roots_surface_actionable_integrity_issues(self) -> None:
        roots = (ACTIVE_ROOT, COMPLETED_ROOT, GOALS_ROOT, PROJECTS_ROOT)
        for root in roots:
            with self.subTest(root=root):
                runner = FakeRunner(
                    {
                        "get_backlinks": [[]],
                        "get_page": [
                            {
                                "slug": root,
                                "type": "collection",
                                "title": root,
                            }
                        ],
                    }
                )
                adapter = GBrainAdapter(runner)
                result = (
                    adapter.list_goals()
                    if root == GOALS_ROOT
                    else adapter.list_projects()
                    if root == PROJECTS_ROOT
                    else adapter.list_collection_tasks(root)
                )
                self.assertEqual(result.issues[0].category, "canonical_root_data")
                self.assertIn("zero verified member_of backlinks", result.issues[0].message)

    def test_missing_canonical_root_does_not_become_a_successful_empty_surface(self) -> None:
        runner = FakeRunner(
            {
                "get_backlinks": [[]],
                "get_page": [GBrainCommandError("page_not_found")],
            }
        )

        result = GBrainAdapter(runner).list_collection_tasks(ACTIVE_ROOT)

        self.assertEqual(result.tasks, ())
        self.assertEqual(result.issues[0].category, "canonical_root_data")
        self.assertIn("could not be read", result.issues[0].message)

    def test_loads_only_direct_member_backlinks_from_the_approved_root(self) -> None:
        task = new_inbox_task(
            "Real task",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        },
                        {
                            "from_slug": "notes/unrelated",
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "mentions",
                        },
                    ]
                ],
                "get_page": [stored_page(task)],
                "get_links": [[]],
            }
        )

        result = GBrainAdapter(runner).list_collection_tasks(ACTIVE_ROOT)

        self.assertEqual([item.slug for item in result.tasks], [task.slug])
        self.assertEqual(result.issues, ())
        self.assertNotIn("list_pages", [tool for tool, _ in runner.calls])
        self.assertEqual(
            runner.calls,
            [
                ("get_backlinks", {"slug": ACTIVE_ROOT}),
                ("get_page", {"slug": task.slug}),
            ],
        )

    def test_typed_member_collection_read_preserves_frontmatter_relationships_without_get_links(self) -> None:
        task = replace(
            new_inbox_task(
                "Linked task",
                datetime(2026, 7, 30, tzinfo=timezone.utc),
                "linked01",
            ),
            project="projects/mission",
            goal="goals/focus",
            parent="tasks/parent",
        )
        page = stored_page(task)
        page["frontmatter"]["project"] = task.project
        page["frontmatter"]["links"] = [
            {"to": ACTIVE_ROOT, "type": "member_of"},
            {"to": task.project, "type": "member_of"},
            {"to": task.goal, "type": "advances_goal"},
            {"to": task.parent, "type": "child_of"},
        ]
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        }
                    ]
                ],
                "get_page": [page],
                "get_links": [GBrainCommandError("unexpected fan-out")],
            }
        )

        result = GBrainAdapter(runner).list_collection_tasks(ACTIVE_ROOT)

        self.assertEqual([item.slug for item in result.tasks], [task.slug])
        self.assertEqual(result.tasks[0].project, task.project)
        self.assertEqual(result.tasks[0].goal, task.goal)
        self.assertEqual(result.tasks[0].parent, task.parent)
        self.assertEqual(result.issues, ())
        self.assertNotIn("get_links", [tool for tool, _ in runner.calls])


class AgentProfileReadTests(unittest.TestCase):

    def test_agent_profile_runtime_requires_an_approved_value(self) -> None:
        page = {
            "slug": "agents/tammy",
            "type": "agent",
            "title": "Agent Tammy",
            "compiled_truth": "Canonical Codex Agent on Tammy.",
            "frontmatter": {"runtime": "unknown"},
        }

        with self.assertRaisesRegex(domain.DomainValidationError, "runtime"):
            AgentProfile.from_page(
                page,
                work_root="collections/tammys-tasks",
                edges=(),
            )

    def test_existing_codex_agent_profile_defaults_runtime_without_name_inference(self) -> None:
        page = {
            "slug": "agents/tammy",
            "type": "agent",
            "title": "Agent An Arbitrary Display Name",
            "compiled_truth": "Existing Codex Agent.",
            "frontmatter": {},
        }

        profile = AgentProfile.from_page(
            page,
            work_root="collections/tammys-tasks",
            edges=(),
        )

        self.assertEqual(profile.runtime, "codex")

    def test_reads_tony_board_avatar_from_canonical_person_attachment(self) -> None:
        runner = FakeRunner(
            {
                "get_page": [
                    {
                        "slug": "people/tony-guan",
                        "type": "person",
                        "title": "Tony Guan",
                        "compiled_truth": (
                            "# Tony Guan\n\n## Attachments\n\n"
                            "![Tony Profile](people/tony-guan/Tony Profile.jpeg)"
                        ),
                        "frontmatter": {},
                    }
                ]
            }
        )

        profile = GBrainAdapter(runner).get_tony_profile()

        self.assertEqual(profile["slug"], "people/tony-guan")
        self.assertEqual(profile["avatar"], {
            "kind": "attachment",
            "value": "/media/people/tony-guan/Tony%20Profile.jpeg",
        })

    def test_timmy_uses_the_exact_lowercase_slug_and_rejects_a_non_agent_page(self) -> None:
        timmy_page = {
            "slug": "agents/timmy",
            "type": "concept",
            "title": "Agent Timmy",
            "compiled_truth": "# Agent Timmy",
            "frontmatter": {},
        }
        runner = FakeRunner(
            {
                "list_pages": [[]],
                "get_page": [timmy_page],
                "get_links": [[]],
            }
        )

        with self.assertRaisesRegex(Exception, "agents/timmy is not an agent page"):
            GBrainAdapter(runner).get_agent_profile("agents/timmy")

        self.assertIn(("get_page", {"slug": "agents/timmy"}), runner.calls)
        self.assertNotIn(("get_page", {"slug": "agents/Timmy"}), runner.calls)

    def test_avatar_write_reasserts_agent_type_after_attachment_snapshot(self) -> None:
        page = {
            "slug": "agents/timmy",
            "type": "agent",
            "title": "Agent Timmy",
            "compiled_truth": "# Agent Timmy",
            "frontmatter": {"work_root": "collections/timmys-tasks"},
        }
        stored = deepcopy(page)
        stored["frontmatter"] = {
            "work_root": "collections/timmys-tasks",
            "avatar": {"kind": "attachment", "value": "/media/agents/timmy/avatar.jpg"},
        }
        runner = FakeRunner(
            {
                "list_pages": [[page]],
                "get_page": [page, page, stored],
                "get_links": [[], [], []],
                "put_page": [{"slug": "agents/timmy"}],
            }
        )

        profile = GBrainAdapter(runner).set_agent_avatar(
            "agents/timmy", "/media/agents/timmy/avatar.jpg"
        )

        self.assertEqual(profile.avatar_value, "/media/agents/timmy/avatar.jpg")
        content = next(
            params["content"]
            for tool, params in runner.calls
            if tool == "put_page"
        )
        self.assertIn('type: "agent"', content)


class ProjectPersistenceTests(unittest.TestCase):
    def test_lists_only_typed_g_tasks_scope_members_without_tasks(self) -> None:
        project = new_project(
            "Interview preparation",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        edge = {
            "from_slug": project.slug,
            "to_slug": PROJECTS_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "get_backlinks": [[edge]],
                "get_page": [stored_project(project)],
                "get_links": [[edge]],
            }
        )

        result = GBrainAdapter(runner).list_projects()

        self.assertEqual([item.slug for item in result.projects], [project.slug])

    def test_reports_malformed_typed_scope_members_for_projects_attention(self) -> None:
        project = new_project(
            "Malformed scoped project",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        edge = {
            "from_slug": project.slug,
            "to_slug": PROJECTS_ROOT,
            "link_type": "member_of",
        }
        malformed_page = stored_project(project)
        malformed_page["type"] = "note"
        runner = FakeRunner(
            {
                "get_backlinks": [[edge]],
                "get_page": [malformed_page],
                "get_links": [[edge]],
            }
        )

        result = GBrainAdapter(runner).list_projects()

        self.assertEqual(result.projects, ())
        self.assertEqual([issue.slug for issue in result.issues], [project.slug])
        self.assertIn("not a project page", result.issues[0].message)

    def test_excludes_old_projects_without_typed_scope_membership(self) -> None:
        scoped = new_project(
            "Scoped project",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        old = new_project(
            "Old unrelated project",
            datetime(2025, 1, 1, tzinfo=timezone.utc),
            "d4e5f6",
        )
        scoped_edge = {
            "from_slug": scoped.slug,
            "to_slug": PROJECTS_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        scoped_edge,
                        {
                            "from_slug": old.slug,
                            "to_slug": PROJECTS_ROOT,
                            "link_type": "involved_in",
                        },
                        {
                            "from_slug": old.slug,
                            "to_slug": "collections/other-projects",
                            "link_type": "member_of",
                        },
                    ]
                ],
                "get_page": [stored_project(scoped)],
                "get_links": [[scoped_edge]],
            }
        )

        result = GBrainAdapter(runner).list_projects()

        self.assertEqual([item.slug for item in result.projects], [scoped.slug])
        self.assertNotIn(old.slug, [item.slug for item in result.projects])

    def test_create_project_requires_page_and_collection_link_readback(self) -> None:
        project = new_project(
            "Interview preparation",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        edge = {
            "from_slug": project.slug,
            "to_slug": PROJECTS_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "put_page": [{"slug": project.slug}],
                "get_page": [stored_projects_root(), stored_project(project)],
                "add_link": [{}],
                "get_links": [[edge]],
            }
        )

        receipt = GBrainAdapter(runner).create_project(project)

        self.assertTrue(receipt.verified)
        self.assertIn("type: project", runner.calls[1][1]["content"])
        self.assertIn(("add_link", {
            "from": project.slug,
            "to": PROJECTS_ROOT,
            "link_type": "member_of",
            "context": "This project is explicitly owned by GTasks.",
            "link_source": "gtasks",
        }), runner.calls)

    def test_new_project_initializes_missing_scope_only_on_explicit_create(self) -> None:
        project = new_project(
            "ERFA PAC",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        edge = {
            "from_slug": project.slug,
            "to_slug": PROJECTS_ROOT,
            "link_type": "member_of",
        }
        missing = GBrainCommandError(
            "GBrain tool get_page failed: page_not_found"
        )
        runner = FakeRunner(
            {
                "get_page": [
                    missing,
                    stored_projects_root(),
                    stored_project(project),
                ],
                "put_page": [
                    {"slug": PROJECTS_ROOT},
                    {"slug": project.slug},
                ],
                "add_link": [{}],
                "get_links": [[edge]],
            }
        )

        receipt = GBrainAdapter(runner).create_project(project)

        self.assertTrue(receipt.verified)
        self.assertEqual(
            runner.calls[:3],
            [
                ("get_page", {"slug": PROJECTS_ROOT}),
                (
                    "put_page",
                    {
                        "slug": PROJECTS_ROOT,
                        "content": runner.calls[1][1]["content"],
                    },
                ),
                ("get_page", {"slug": PROJECTS_ROOT}),
            ],
        )
        self.assertIn("type: collection", runner.calls[1][1]["content"])
        self.assertIn("title: Tony's Projects", runner.calls[1][1]["content"])
        self.assertIn("type: member_of", runner.calls[3][1]["content"])


class AgentArtifactAdapterTests(unittest.TestCase):
    def artifact(self, *, created_at: datetime | None = None):
        return new_agent_artifact(
            title="Family care weekly review brief",
            artifact_kind="markdown",
            created_by="agents/toddy",
            produced_for="tasks/561640dd-8e34-43e1-a03e-e3f3f270033d",
            markdown="# Weekly review\n\nCanonical content.",
            project="projects/11111111-1111-4111-8111-111111111111",
            goal="goals/22222222-2222-4222-8222-222222222222",
            now=created_at or datetime(2026, 8, 2, 14, tzinfo=timezone.utc),
        )

    def qa_fixture_task(self, artifact):
        now = datetime(2026, 8, 3, 9, tzinfo=timezone(timedelta(hours=-7)))
        return replace(
            new_task(
                title="Agent Artifact release canary fixture",
                detail="Non-sensitive isolated fixture for V0.0.70 canary verification.",
                due_day=now.date(),
                now=now,
                identity="artifactqa70",
            ),
            slug=artifact.produced_for,
            status="completed",
            lifecycle_root=QA_FIXTURES_ROOT,
            qa_fixture=True,
            qa_owner="mission_control_release_canary",
            qa_release="V0.0.70",
            owner_agent=artifact.created_by,
            inbox=False,
            completed_at=now,
        )

    def test_create_completed_agent_qa_fixture_and_artifact_end_to_end(self) -> None:
        artifact = self.artifact()
        fixture = self.qa_fixture_task(artifact)
        runner = StatefulArtifactRunner({}, [])
        adapter = GBrainAdapter(runner)

        fixture_receipt = adapter.create_agent_qa_fixture_task(
            fixture, artifact.created_by
        )
        artifact_receipt = adapter.create_agent_artifact(
            artifact,
            executing_agent=artifact.created_by,
            idempotency_key="v0.0.70:agent-artifact-live-canary:v1",
        )

        self.assertTrue(fixture_receipt.verified)
        self.assertTrue(artifact_receipt.verified)
        stored_fixture = Task.from_page(
            runner.pages[fixture.slug],
            edges=[edge for edge in runner.links if edge["from_slug"] == fixture.slug],
        )
        self.assertEqual(stored_fixture.status, "completed")
        self.assertEqual(stored_fixture.lifecycle_root, QA_FIXTURES_ROOT)
        self.assertEqual(stored_fixture.owner_agent, artifact.created_by)
        actual = {
            (edge["from_slug"], edge["to_slug"], edge["link_type"])
            for edge in runner.links
        }
        self.assertIn((fixture.slug, QA_FIXTURES_ROOT, "member_of"), actual)
        self.assertIn((fixture.slug, artifact.created_by, "assigned_to"), actual)
        self.assertIn((artifact.slug, fixture.slug, "produced_for"), actual)
        self.assertTrue(
            all(edge.get("link_source") == "gtasks" for edge in runner.links),
            runner.links,
        )

    def test_qa_fixture_artifact_preflight_rejects_incomplete_fixture_contract(self) -> None:
        artifact = self.artifact()
        fixture = self.qa_fixture_task(artifact)
        page = stored_page(fixture)
        page["frontmatter"].update(
            {
                "status": "completed",
                "completed_at": fixture.completed_at.isoformat(),
                "qa_fixture": True,
                "qa_owner": fixture.qa_owner,
                "qa_release": fixture.qa_release,
                "links": [{"to": QA_FIXTURES_ROOT, "type": "member_of"}],
            }
        )
        edges = [
            {
                "from_slug": fixture.slug,
                "to_slug": QA_FIXTURES_ROOT,
                "link_type": "member_of",
            }
        ]
        runner = StatefulArtifactRunner({fixture.slug: page}, edges)

        with self.assertRaisesRegex(
            domain.DomainValidationError,
            "approved canonical Agent task or completed Agent QA fixture",
        ):
            GBrainAdapter(runner).create_agent_artifact(
                artifact,
                executing_agent=artifact.created_by,
                idempotency_key="v0.0.70:invalid-fixture:v1",
            )
        self.assertFalse(
            any(
                call[0] == "put_page"
                and call[1]["slug"].startswith("artifacts/")
                for call in runner.calls
            )
        )

    def test_qa_fixture_creator_requires_canonical_task_uuid_before_write(self) -> None:
        artifact = self.artifact()
        fixture = replace(
            self.qa_fixture_task(artifact),
            slug="projects/66666666-6666-4666-8666-666666666666",
        )
        runner = StatefulArtifactRunner({}, [])

        with self.assertRaisesRegex(ValueError, "canonical tasks UUID slug"):
            GBrainAdapter(runner).create_agent_qa_fixture_task(
                fixture, artifact.created_by
            )
        self.assertEqual(runner.calls, [])

    def test_qa_fixture_creator_reports_ambiguous_page_write_as_partial(self) -> None:
        artifact = self.artifact()
        fixture = self.qa_fixture_task(artifact)

        class AmbiguousPutRunner(StatefulArtifactRunner):
            def run(self, tool, params):
                result = super().run(tool, params)
                if tool == "put_page" and params.get("slug") == fixture.slug:
                    raise GBrainCommandError("connection closed after page write")
                return result

        runner = AmbiguousPutRunner({}, [])

        with self.assertRaisesRegex(PartialMutationError, fixture.slug):
            GBrainAdapter(runner).create_agent_qa_fixture_task(
                fixture, artifact.created_by
            )

    def test_qa_fixture_creator_resumes_exact_page_with_missing_edges(self) -> None:
        artifact = self.artifact()
        fixture = self.qa_fixture_task(artifact)
        runner = StatefulArtifactRunner({}, [])
        runner.run(
            "put_page",
            {
                "slug": fixture.slug,
                "content": gbrain_module.render_task_page(fixture),
            },
        )
        runner.calls.clear()

        receipt = GBrainAdapter(runner).create_agent_qa_fixture_task(
            fixture, artifact.created_by
        )

        self.assertTrue(receipt.verified)
        self.assertFalse(
            any(
                tool == "put_page" and params.get("slug") == fixture.slug
                for tool, params in runner.calls
            )
        )
        self.assertEqual(
            {
                (edge["to_slug"], edge["link_type"], edge["link_source"])
                for edge in runner.links
                if edge["from_slug"] == fixture.slug
            },
            {
                (QA_FIXTURES_ROOT, "member_of", "gtasks"),
                (artifact.created_by, "assigned_to", "gtasks"),
            },
        )

    def test_qa_fixture_creator_rejects_mismatched_existing_page_without_write(self) -> None:
        artifact = self.artifact()
        fixture = self.qa_fixture_task(artifact)
        runner = StatefulArtifactRunner({}, [])
        runner.run(
            "put_page",
            {
                "slug": fixture.slug,
                "content": gbrain_module.render_task_page(fixture),
            },
        )
        mismatched = runner.pages[fixture.slug]
        mismatched["title"] = "Different fixture"
        mismatched["frontmatter"]["title"] = "Different fixture"
        runner.calls.clear()

        with self.assertRaisesRegex(ValueError, "does not match"):
            GBrainAdapter(runner).create_agent_qa_fixture_task(
                fixture, artifact.created_by
            )

        self.assertFalse(
            any(
                tool in {"put_page", "add_link"}
                and params.get("slug", params.get("from")) == fixture.slug
                for tool, params in runner.calls
            )
        )

    def test_qa_fixture_creator_rejects_duplicate_frontmatter_relationship(self) -> None:
        artifact = self.artifact()
        fixture = self.qa_fixture_task(artifact)
        runner = StatefulArtifactRunner({}, [])
        runner.run(
            "put_page",
            {
                "slug": fixture.slug,
                "content": gbrain_module.render_task_page(fixture),
            },
        )
        runner.pages[fixture.slug]["frontmatter"]["links"].append(
            {
                "to": artifact.created_by,
                "type": "assigned_to",
                "context": "Duplicate relationship.",
            }
        )
        runner.calls.clear()

        with self.assertRaisesRegex(ValueError, "does not match"):
            GBrainAdapter(runner).create_agent_qa_fixture_task(
                fixture, artifact.created_by
            )

        self.assertFalse(
            any(
                tool in {"put_page", "add_link"}
                and params.get("slug", params.get("from")) == fixture.slug
                for tool, params in runner.calls
            )
        )

    def test_artifact_creation_rejects_non_gtasks_relationship_sources(self) -> None:
        class ManualSourceRunner(StatefulArtifactRunner):
            def run(self, tool, params):
                if tool == "add_link" and str(params.get("from", "")).startswith(
                    "artifacts/"
                ):
                    params = {**params, "link_source": "manual"}
                return super().run(tool, params)

        artifact = self.artifact()
        fixture = self.qa_fixture_task(artifact)
        seed = StatefulArtifactRunner({}, [])
        adapter = GBrainAdapter(seed)
        adapter.create_agent_qa_fixture_task(fixture, artifact.created_by)
        adapter.ensure_artifact_collections()
        runner = ManualSourceRunner(deepcopy(seed.pages), deepcopy(seed.links))

        with self.assertRaisesRegex(PartialMutationError, "not fully verified"):
            GBrainAdapter(runner).create_agent_artifact(
                artifact,
                executing_agent=artifact.created_by,
                idempotency_key="manual-source-must-fail",
            )
        self.assertIn(fixture.slug, runner.pages)

    def test_qa_fixture_creator_rejects_optional_business_relationships(self) -> None:
        artifact = self.artifact()
        fixture = replace(
            self.qa_fixture_task(artifact),
            project="projects/11111111-1111-4111-8111-111111111111",
        )
        runner = StatefulArtifactRunner({}, [])

        with self.assertRaisesRegex(
            ValueError,
            "QA fixture cannot contain project, goal, parent, dependency, or blocker relationships",
        ):
            GBrainAdapter(runner).create_agent_qa_fixture_task(
                fixture, artifact.created_by
            )
        self.assertEqual(runner.calls, [])

    def test_timmy_execution_cannot_publish_as_toddy(self) -> None:
        artifact = self.artifact()
        runner = StatefulArtifactRunner({}, [])

        with self.assertRaisesRegex(
            domain.DomainValidationError,
            "Artifact publisher identity does not match its installed execution contract",
        ):
            GBrainAdapter(runner).create_agent_artifact(
                artifact,
                executing_agent="agents/timmy",
                idempotency_key="timmy-cannot-impersonate-toddy:v1",
            )
        self.assertEqual(runner.calls, [])

    def test_create_artifact_verifies_page_and_all_typed_links(self) -> None:
        artifact = self.artifact()
        task_page, task_edges = authorized_artifact_task(artifact)
        pages = {
            ARTIFACTS_ROOT: {
                "slug": ARTIFACTS_ROOT,
                "type": "collection",
                "title": "Mission Control Artifacts",
                "frontmatter": {"collection_kind": "mission_control_artifacts"},
                "compiled_truth": "Mission Control Agent artifacts.",
            },
            artifact.produced_for: task_page,
        }
        for agent, collection in domain.ARTIFACT_AGENT_SCOPES:
            pages[collection] = {
                "slug": collection,
                "type": "collection",
                "title": collection,
                "frontmatter": {
                    "collection_kind": "mission_control_artifacts",
                    "agent": agent,
                },
                "compiled_truth": "Mission Control Agent artifacts.",
            }
        collection_edges = [
            {"from_slug": collection, "to_slug": ARTIFACTS_ROOT, "link_type": "part_of"}
            for _agent, collection in domain.ARTIFACT_AGENT_SCOPES
        ] + [
            {"from_slug": collection, "to_slug": agent, "link_type": "for_agent"}
            for agent, collection in domain.ARTIFACT_AGENT_SCOPES
        ]
        runner = StatefulArtifactRunner(pages, [*collection_edges, *task_edges])

        receipt = GBrainAdapter(runner).create_agent_artifact(
            artifact, executing_agent=artifact.created_by
        )

        self.assertTrue(receipt.verified)
        actual = {
            (edge["from_slug"], edge["to_slug"], edge["link_type"])
            for edge in runner.links
        }
        self.assertIn((artifact.slug, artifact.agent_collection, "member_of"), actual)
        self.assertIn((artifact.slug, "agents/toddy", "created_by"), actual)
        self.assertIn((artifact.slug, artifact.produced_for, "produced_for"), actual)
        self.assertIn((artifact.slug, artifact.project, "supports_project"), actual)
        self.assertIn((artifact.slug, artifact.goal, "supports_goal"), actual)

    def test_get_artifact_accepts_exact_gbrain_normalized_page_shape(self) -> None:
        artifact = self.artifact()
        page = stored_artifact(artifact)
        page["type"] = "artifact"
        page["frontmatter"].pop("type")
        page["frontmatter"].pop("title")
        page["compiled_truth"] = page.pop("compiled_markdown")
        runner = StatefulArtifactRunner(
            {artifact.slug: page}, artifact_edges(artifact)
        )
        adapter = GBrainAdapter(runner)

        readback = adapter.get_agent_artifact(artifact.slug)
        listed = adapter.list_agent_artifacts(agent=artifact.created_by)

        self.assertEqual(readback.to_dict(), artifact.to_dict())
        self.assertEqual([item.to_dict() for item in listed.artifacts], [artifact.to_dict()])
        self.assertEqual(listed.issues, ())

    def test_get_artifact_rejects_top_level_and_frontmatter_type_conflicts(self) -> None:
        artifact = self.artifact()
        pages = []
        for top_level_type, frontmatter_type in (
            ("task", "artifact"),
            ("artifact", "task"),
        ):
            page = stored_artifact(artifact)
            page["type"] = top_level_type
            page["frontmatter"]["type"] = frontmatter_type
            pages.append(page)

        for page in pages:
            with self.subTest(
                top_level=page["type"], frontmatter=page["frontmatter"]["type"]
            ):
                runner = StatefulArtifactRunner(
                    {artifact.slug: page}, artifact_edges(artifact)
                )
                with self.assertRaisesRegex(
                    domain.DomainValidationError, "canonical artifact"
                ):
                    GBrainAdapter(runner).get_agent_artifact(artifact.slug)

    def test_collection_bootstrap_is_idempotent_and_verifies_child_edges(self) -> None:
        runner = StatefulArtifactRunner({}, [])
        adapter = GBrainAdapter(runner)

        adapter.ensure_artifact_collections()
        first_puts = [call for call in runner.calls if call[0] == "put_page"]
        adapter.ensure_artifact_collections()

        self.assertEqual(
            len(first_puts),
            len(domain.EXISTING_CODEX_ARTIFACT_AGENT_SCOPES) + 1,
        )
        self.assertEqual(
            len([call for call in runner.calls if call[0] == "put_page"]),
            len(domain.EXISTING_CODEX_ARTIFACT_AGENT_SCOPES) + 1,
        )
        actual = {
            (edge["from_slug"], edge["to_slug"], edge["link_type"])
            for edge in runner.links
        }
        for agent, collection in domain.EXISTING_CODEX_ARTIFACT_AGENT_SCOPES:
            self.assertIn((collection, ARTIFACTS_ROOT, "part_of"), actual)
            self.assertIn((collection, agent, "for_agent"), actual)


    def test_collection_bootstrap_rejects_extra_child_scope_edges(self) -> None:
        runner = StatefulArtifactRunner({}, [])
        adapter = GBrainAdapter(runner)
        adapter.ensure_artifact_collections()
        child = "collections/toddys-artifacts"
        runner.links.append(
            {
                "from_slug": child,
                "to_slug": "collections/not-the-artifact-root",
                "link_type": "part_of",
            }
        )

        with self.assertRaisesRegex(GBrainProtocolError, "exactly one part_of"):
            adapter.ensure_artifact_collections()

    def test_collection_bootstrap_refuses_reserved_non_collection_page(self) -> None:
        runner = StatefulArtifactRunner(
            {
                ARTIFACTS_ROOT: {
                    "slug": ARTIFACTS_ROOT,
                    "type": "concept",
                    "frontmatter": {"type": "note"},
                }
            },
            [],
        )

        with self.assertRaisesRegex(GBrainProtocolError, "not a collection"):
            GBrainAdapter(runner).ensure_artifact_collections()
        self.assertFalse(any(call[0] == "put_page" for call in runner.calls))

    def test_create_artifact_partial_link_write_fails_closed(self) -> None:
        artifact = self.artifact()
        task_page, task_edges = authorized_artifact_task(artifact)

        class MissingTaskLinkRunner(StatefulArtifactRunner):
            def run(self, tool: str, params: dict) -> object:
                if tool == "add_link" and params.get("link_type") == "produced_for":
                    self.calls.append((tool, deepcopy(params)))
                    return {}
                return super().run(tool, params)

        runner = MissingTaskLinkRunner({artifact.produced_for: task_page}, task_edges)
        with self.assertRaisesRegex(PartialMutationError, artifact.slug):
            GBrainAdapter(runner).create_agent_artifact(
                artifact, executing_agent=artifact.created_by
            )

    def test_create_artifact_idempotency_returns_existing_and_rejects_mismatch(self) -> None:
        original = self.artifact()
        task_page, task_edges = authorized_artifact_task(original)
        runner = StatefulArtifactRunner({original.produced_for: task_page}, task_edges)
        adapter = GBrainAdapter(runner)
        key = "toddy:weekly-review:v1"

        first = adapter.create_agent_artifact(
            original,
            executing_agent=original.created_by,
            idempotency_key=key,
        )
        retry_candidate = self.artifact()
        retry = adapter.create_agent_artifact(
            retry_candidate,
            executing_agent=retry_candidate.created_by,
            idempotency_key=key,
        )

        self.assertFalse(first.idempotent)
        self.assertTrue(retry.idempotent)
        self.assertEqual(retry.artifact.slug, original.slug)
        put_count = len(
            [
                call
                for call in runner.calls
                if call[0] == "put_page" and call[1]["slug"].startswith("artifacts/")
            ]
        )
        with self.assertRaises(gbrain_module.ArtifactIdempotencyConflict):
            adapter.create_agent_artifact(
                replace(self.artifact(), title="Different content"),
                executing_agent=original.created_by,
                idempotency_key=key,
            )
        self.assertEqual(
            len(
                [
                    call
                    for call in runner.calls
                    if call[0] == "put_page"
                    and call[1]["slug"].startswith("artifacts/")
                ]
            ),
            put_count,
        )

    def test_create_artifact_accepts_canonical_updated_at_and_retries_idempotently(self) -> None:
        original = self.artifact()
        task_page, task_edges = authorized_artifact_task(original)

        class CanonicalTimestampRunner(StatefulArtifactRunner):
            def run(self, tool: str, params: dict) -> object:
                result = super().run(tool, params)
                if tool == "put_page" and params["slug"].startswith("artifacts/"):
                    self.pages[params["slug"]]["updated_at"] = (
                        "2026-08-11T10:30:00-07:00"
                    )
                return result

        runner = CanonicalTimestampRunner(
            {original.produced_for: task_page}, task_edges
        )
        adapter = GBrainAdapter(runner)
        key = "tammy:value-discovery:canonical-timestamp:v1"

        first = adapter.create_agent_artifact(
            original,
            executing_agent=original.created_by,
            idempotency_key=key,
        )
        retry = adapter.create_agent_artifact(
            self.artifact(),
            executing_agent=original.created_by,
            idempotency_key=key,
        )

        self.assertTrue(first.verified)
        self.assertFalse(first.idempotent)
        self.assertTrue(retry.verified)
        self.assertTrue(retry.idempotent)
        self.assertEqual(retry.artifact.slug, original.slug)

    def test_concurrent_same_key_publication_serializes_scan_and_write(self) -> None:
        original = self.artifact()
        task_page, task_edges = authorized_artifact_task(original)

        class SlowFirstScanRunner(StatefulArtifactRunner):
            def __init__(self):
                super().__init__({original.produced_for: task_page}, task_edges)
                self.block_scans = False
                self.first_scan_started = threading.Event()
                self.release_first_scan = threading.Event()
                self._blocked_once = False

            def run(self, tool, params):
                if (
                    self.block_scans
                    and tool == "get_backlinks"
                    and params.get("slug") == original.agent_collection
                    and not self._blocked_once
                ):
                    self._blocked_once = True
                    self.first_scan_started.set()
                    self.release_first_scan.wait(timeout=2)
                return super().run(tool, params)

        runner = SlowFirstScanRunner()
        adapter = GBrainAdapter(runner)
        adapter.ensure_artifact_collections()
        runner.block_scans = True
        key = "toddy:weekly-review:concurrent:v1"
        retry_candidate = self.artifact()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                adapter.create_agent_artifact,
                original,
                executing_agent=original.created_by,
                idempotency_key=key,
            )
            self.assertTrue(runner.first_scan_started.wait(timeout=2))
            second = pool.submit(
                adapter.create_agent_artifact,
                retry_candidate,
                executing_agent=retry_candidate.created_by,
                idempotency_key=key,
            )
            time.sleep(0.05)
            runner.release_first_scan.set()
            receipts = (first.result(timeout=2), second.result(timeout=2))

        artifact_puts = [
            call
            for call in runner.calls
            if call[0] == "put_page" and call[1]["slug"].startswith("artifacts/")
        ]
        self.assertEqual(len(artifact_puts), 1)
        self.assertEqual({receipt.artifact.slug for receipt in receipts}, {original.slug})
        self.assertEqual(sorted(receipt.idempotent for receipt in receipts), [False, True])

    def test_create_artifact_preflights_missing_malformed_and_unauthorized_task(self) -> None:
        artifact = self.artifact()
        authorized_page, authorized_edges = authorized_artifact_task(artifact)
        malformed_page = {**authorized_page, "type": "concept"}
        unauthorized_page = deepcopy(authorized_page)
        unauthorized_page["frontmatter"] = deepcopy(authorized_page["frontmatter"])
        unauthorized_page["frontmatter"]["links"] = [
            {"to": "collections/tammys-tasks", "type": "member_of"}
        ]
        unauthorized_edges = [
            {
                "from_slug": artifact.produced_for,
                "to_slug": "collections/tammys-tasks",
                "link_type": "member_of",
            },
            {
                "from_slug": artifact.produced_for,
                "to_slug": "agents/tammy",
                "link_type": "assigned_to",
            },
        ]

        cases = (
            ("missing", {}, []),
            ("malformed", {artifact.produced_for: malformed_page}, authorized_edges),
            ("unauthorized", {artifact.produced_for: unauthorized_page}, unauthorized_edges),
        )
        for label, pages, links in cases:
            with self.subTest(label=label):
                runner = StatefulArtifactRunner(pages, links)
                with self.assertRaises((domain.DomainValidationError, GBrainCommandError)):
                    GBrainAdapter(runner).create_agent_artifact(
                        artifact,
                        executing_agent=artifact.created_by,
                        idempotency_key=f"{label}:v1",
                    )
                self.assertFalse(
                    any(
                        call[0] == "put_page"
                        and call[1]["slug"].startswith("artifacts/")
                        for call in runner.calls
                    )
                )

    def test_prewrite_gbrain_outage_stays_gbrain_error(self) -> None:
        artifact = self.artifact()

        class OfflineRunner(StatefulArtifactRunner):
            def run(self, tool, params):
                if tool == "get_page" and params.get("slug") == artifact.produced_for:
                    raise GBrainCommandError("GBrain offline")
                return super().run(tool, params)

        runner = OfflineRunner({}, [])
        with self.assertRaises(GBrainCommandError):
            GBrainAdapter(runner).create_agent_artifact(
                artifact,
                executing_agent=artifact.created_by,
                idempotency_key="offline:v1",
            )
        self.assertFalse(any(call[0] == "put_page" for call in runner.calls))

    def test_artifact_filters_require_canonical_uuid_before_backlink_reads(self) -> None:
        invalid_filters = (
            {"task": "tasks/title-derived"},
            {"project": "projects/title-derived"},
            {"goal": "goals/title-derived"},
            {"task": "tasks/6ba7b810-9dad-11d1-80b4-00c04fd430c8"},
            {"project": "projects/3d813cbb-47fb-32ba-91df-831e1593ac29"},
            {"goal": "goals/6ba7b810-9dad-11d1-80b4-00c04fd430c8"},
        )
        for filters in invalid_filters:
            with self.subTest(filters=filters):
                runner = StatefulArtifactRunner({}, [])
                with self.assertRaisesRegex(ValueError, "canonical UUID"):
                    GBrainAdapter(runner).list_agent_artifacts(**filters)
                self.assertEqual(runner.calls, [])

    def test_artifact_filters_accept_canonical_uuid5_identities(self) -> None:
        valid_filters = (
            {"task": "tasks/f07660c8-f6cf-5226-a602-4f12e4587104"},
            {"project": "projects/65c2f720-fb49-5403-9a9e-76228e285277"},
            {"goal": "goals/41fb50e0-e1d7-592b-b2c3-ff1f7aacff10"},
        )
        for filters in valid_filters:
            with self.subTest(filters=filters):
                runner = StatefulArtifactRunner({}, [])
                GBrainAdapter(runner).list_agent_artifacts(**filters)
                self.assertTrue(
                    any(
                        tool == "get_backlinks" and params.get("slug") in filters.values()
                        for tool, params in runner.calls
                    )
                )

    def test_list_artifacts_reports_malformed_member_without_hiding_valid_item(self) -> None:
        valid = self.artifact()
        malformed_slug = "artifacts/33333333-3333-4333-8333-333333333333"
        runner = StatefulArtifactRunner(
            {
                valid.slug: stored_artifact(valid),
                malformed_slug: {
                    **stored_artifact(valid),
                    "slug": malformed_slug,
                    "frontmatter": {**stored_artifact(valid)["frontmatter"], "type": "note"},
                },
            },
            [
                *artifact_edges(valid),
                {"from_slug": malformed_slug, "to_slug": valid.agent_collection, "link_type": "member_of"},
            ],
        )

        read = GBrainAdapter(runner).list_agent_artifacts(agent="agents/toddy")

        self.assertEqual([item.slug for item in read.artifacts], [valid.slug])
        self.assertEqual([issue.slug for issue in read.issues], [malformed_slug])

    def test_list_artifacts_uses_typed_filters_and_stable_newest_first_pagination(self) -> None:
        older = self.artifact(created_at=datetime(2026, 8, 1, 14, tzinfo=timezone.utc))
        newer = self.artifact(created_at=datetime(2026, 8, 2, 14, tzinfo=timezone.utc))
        unrelated = new_agent_artifact(
            title="Other task output",
            artifact_kind="markdown",
            created_by="agents/tammy",
            produced_for="tasks/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            markdown="# Other",
            now=datetime(2026, 8, 3, 14, tzinfo=timezone.utc),
        )
        runner = StatefulArtifactRunner(
            {item.slug: stored_artifact(item) for item in (older, newer, unrelated)},
            [edge for item in (older, newer, unrelated) for edge in artifact_edges(item)],
        )

        first = GBrainAdapter(runner).list_agent_artifacts(
            agent="agents/toddy", task=older.produced_for, limit=1, cursor=0
        )
        second = GBrainAdapter(runner).list_agent_artifacts(
            agent="agents/toddy", task=older.produced_for, limit=1, cursor=1
        )

        self.assertEqual([item.slug for item in first.artifacts], [newer.slug])
        self.assertEqual(first.next_cursor, 1)
        self.assertEqual([item.slug for item in second.artifacts], [older.slug])
        self.assertIsNone(second.next_cursor)
        task_backlink_calls = [
            params for tool, params in runner.calls
            if tool == "get_backlinks" and params.get("slug") == older.produced_for
        ]
        self.assertTrue(task_backlink_calls)

    def test_task_filter_unions_typed_review_references_and_orders_by_updated_at(self) -> None:
        task_slug = "tasks/561640dd-8e34-43e1-a03e-e3f3f270033d"
        direct = self.artifact(created_at=datetime(2026, 8, 2, 14, tzinfo=timezone.utc))
        reviewed = new_agent_artifact(
            title="Referenced review artifact",
            artifact_kind="markdown",
            created_by="agents/toddy",
            produced_for="tasks/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            markdown="# Referenced review",
            now=datetime(2026, 8, 1, 14, tzinfo=timezone.utc),
        )
        markdown_only = new_agent_artifact(
            title="Markdown-only candidate",
            artifact_kind="markdown",
            created_by="agents/toddy",
            produced_for="tasks/cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            markdown="# Markdown-only candidate",
            now=datetime(2026, 8, 5, 14, tzinfo=timezone.utc),
        )
        malformed = "artifacts/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        direct_page = stored_artifact(direct)
        reviewed_page = stored_artifact(reviewed)
        reviewed_page["frontmatter"]["updated_at"] = "2026-08-04T14:00:00+00:00"
        runner = StatefulArtifactRunner(
            {
                direct.slug: direct_page,
                reviewed.slug: reviewed_page,
                markdown_only.slug: stored_artifact(markdown_only),
                task_slug: {
                    "slug": task_slug,
                    "compiled_truth": f"Review {markdown_only.slug} in Markdown only.",
                },
                malformed: {
                    "slug": malformed,
                    "type": "concept",
                    "frontmatter": {"type": "note"},
                    "compiled_truth": "# Not an Artifact",
                },
            },
            [
                *artifact_edges(direct),
                *artifact_edges(reviewed),
                *artifact_edges(markdown_only),
                {
                    "from_slug": task_slug,
                    "to_slug": reviewed.slug,
                    "link_type": "reviews_artifact",
                },
                {
                    "from_slug": task_slug,
                    "to_slug": malformed,
                    "link_type": "reviews_artifact",
                },
            ],
        )

        read = GBrainAdapter(runner).list_agent_artifacts(task=task_slug)

        self.assertEqual([artifact.slug for artifact in read.artifacts], [reviewed.slug, direct.slug])
        self.assertEqual([issue.slug for issue in read.issues], [malformed])
        self.assertNotIn(markdown_only.slug, [artifact.slug for artifact in read.artifacts])
        relation_context = {
            artifact["slug"]: artifact["relation_context"]
            for artifact in read.to_dict()["artifacts"]
        }
        self.assertEqual(relation_context[reviewed.slug], ["referenced_for_review"])
        self.assertEqual(relation_context[direct.slug], ["produced_for"])

    def test_add_artifact_review_reference_is_idempotent_and_preserves_provenance(self) -> None:
        artifact = self.artifact()
        review_task = "tasks/540d2d36-4ce4-47f2-a06f-bd6ba8ae2700"
        source_task_page, source_task_edges = authorized_artifact_task(artifact)
        review_page = deepcopy(source_task_page)
        review_page["slug"] = review_task
        review_page["frontmatter"] = deepcopy(source_task_page["frontmatter"])
        runner = StatefulArtifactRunner(
            {
                artifact.slug: stored_artifact(artifact),
                artifact.produced_for: source_task_page,
                review_task: review_page,
            },
            [
                *[{**edge, "link_source": "gtasks"} for edge in artifact_edges(artifact)],
                *source_task_edges,
                {
                    "from_slug": review_task,
                    "to_slug": "collections/toddys-tasks",
                    "link_type": "member_of",
                },
                {
                    "from_slug": review_task,
                    "to_slug": "agents/toddy",
                    "link_type": "assigned_to",
                },
            ],
        )
        adapter = GBrainAdapter(runner)

        first = adapter.add_artifact_review_reference(review_task, artifact.slug)
        second = adapter.add_artifact_review_reference(review_task, artifact.slug)

        self.assertTrue(first.verified)
        self.assertFalse(first.idempotent)
        self.assertTrue(second.idempotent)
        self.assertIn(
            (review_task, artifact.slug, "reviews_artifact", "gtasks"),
            {
                (edge["from_slug"], edge["to_slug"], edge["link_type"], edge.get("link_source"))
                for edge in runner.links
            },
        )
        self.assertIn(
            (artifact.slug, artifact.produced_for, "produced_for"),
            {(edge["from_slug"], edge["to_slug"], edge["link_type"]) for edge in runner.links},
        )

    def test_add_artifact_review_reference_accepts_verified_legacy_artifact_link_sources(self) -> None:
        artifact = self.artifact()
        review_task = "tasks/540d2d36-4ce4-47f2-a06f-bd6ba8ae2700"
        source_task_page, source_task_edges = authorized_artifact_task(artifact)
        review_page = deepcopy(source_task_page)
        review_page["slug"] = review_task
        review_page["frontmatter"] = deepcopy(source_task_page["frontmatter"])
        runner = StatefulArtifactRunner(
            {
                artifact.slug: stored_artifact(artifact),
                artifact.produced_for: source_task_page,
                review_task: review_page,
            },
            [
                *artifact_edges(artifact),
                *source_task_edges,
                {
                    "from_slug": review_task,
                    "to_slug": "collections/toddys-tasks",
                    "link_type": "member_of",
                },
                {
                    "from_slug": review_task,
                    "to_slug": "agents/toddy",
                    "link_type": "assigned_to",
                },
            ],
        )

        receipt = GBrainAdapter(runner).add_artifact_review_reference(
            review_task, artifact.slug
        )

        self.assertTrue(receipt.verified)
        self.assertIn(
            (review_task, artifact.slug, "reviews_artifact", "gtasks"),
            {
                (
                    edge["from_slug"], edge["to_slug"], edge["link_type"],
                    edge.get("link_source"),
                )
                for edge in runner.links
            },
        )

    def test_add_artifact_review_reference_serializes_concurrent_retries(self) -> None:
        artifact = self.artifact()
        review_task = "tasks/540d2d36-4ce4-47f2-a06f-bd6ba8ae2700"
        source_task_page, source_task_edges = authorized_artifact_task(artifact)
        review_page = deepcopy(source_task_page)
        review_page["slug"] = review_task
        review_page["frontmatter"] = deepcopy(source_task_page["frontmatter"])

        class ConcurrentReviewRunner(StatefulArtifactRunner):
            def __init__(self, pages, links) -> None:
                super().__init__(pages, links)
                self.add_barrier = threading.Barrier(2)

            def run(self, tool: str, params: dict) -> object:
                if tool == "add_link" and params.get("link_type") == "reviews_artifact":
                    try:
                        self.add_barrier.wait(timeout=0.2)
                    except threading.BrokenBarrierError:
                        pass
                return super().run(tool, params)

        runner = ConcurrentReviewRunner(
            {
                artifact.slug: stored_artifact(artifact),
                artifact.produced_for: source_task_page,
                review_task: review_page,
            },
            [
                *[{**edge, "link_source": "gtasks"} for edge in artifact_edges(artifact)],
                *source_task_edges,
                {
                    "from_slug": review_task,
                    "to_slug": "collections/toddys-tasks",
                    "link_type": "member_of",
                },
                {
                    "from_slug": review_task,
                    "to_slug": "agents/toddy",
                    "link_type": "assigned_to",
                },
            ],
        )
        adapter = GBrainAdapter(runner)
        start = threading.Barrier(2)

        def write_reference() -> object:
            start.wait(timeout=1)
            return adapter.add_artifact_review_reference(review_task, artifact.slug)

        with ThreadPoolExecutor(max_workers=2) as executor:
            receipts = list(executor.map(lambda _index: write_reference(), range(2)))

        self.assertEqual(sorted(receipt.idempotent for receipt in receipts), [False, True])
        matching = [
            edge for edge in runner.links
            if edge.get("from_slug") == review_task
            and edge.get("to_slug") == artifact.slug
            and edge.get("link_type") == "reviews_artifact"
        ]
        self.assertEqual(len(matching), 1)

    def test_task_filter_dedupes_direct_and_reviewed_artifact_with_stable_pagination_and_issues(self) -> None:
        task_slug = "tasks/561640dd-8e34-43e1-a03e-e3f3f270033d"
        newest = self.artifact(created_at=datetime(2026, 8, 3, 14, tzinfo=timezone.utc))
        older = new_agent_artifact(
            title="Older reviewed Artifact",
            artifact_kind="markdown",
            created_by="agents/toddy",
            produced_for="tasks/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            markdown="# Older",
            now=datetime(2026, 8, 1, 14, tzinfo=timezone.utc),
        )
        missing_a = "artifacts/11111111-1111-4111-8111-111111111111"
        missing_b = "artifacts/eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        runner = StatefulArtifactRunner(
            {
                newest.slug: stored_artifact(newest),
                older.slug: stored_artifact(older),
            },
            [
                *artifact_edges(newest),
                *artifact_edges(older),
                {
                    "from_slug": task_slug,
                    "to_slug": newest.slug,
                    "link_type": "reviews_artifact",
                },
                {
                    "from_slug": task_slug,
                    "to_slug": older.slug,
                    "link_type": "reviews_artifact",
                },
                {
                    "from_slug": task_slug,
                    "to_slug": missing_b,
                    "link_type": "reviews_artifact",
                },
                {
                    "from_slug": task_slug,
                    "to_slug": missing_a,
                    "link_type": "reviews_artifact",
                },
            ],
        )
        adapter = GBrainAdapter(runner)

        first = adapter.list_agent_artifacts(task=task_slug, limit=1, cursor=0)
        second = adapter.list_agent_artifacts(task=task_slug, limit=1, cursor=1)

        self.assertEqual([item.slug for item in first.artifacts], [newest.slug])
        self.assertEqual(first.next_cursor, 1)
        self.assertEqual(
            first.to_dict()["artifacts"][0]["relation_context"],
            ["produced_for", "referenced_for_review"],
        )
        self.assertEqual([item.slug for item in second.artifacts], [older.slug])
        self.assertIsNone(second.next_cursor)
        self.assertEqual(
            [issue.slug for issue in first.issues],
            [missing_a, missing_b],
        )
        self.assertEqual(
            [issue.slug for issue in second.issues],
            [missing_a, missing_b],
        )

    def test_renderers_emit_canonical_collection_and_artifact_contracts(self) -> None:
        artifact = self.artifact()
        child = gbrain_module.render_artifact_collection_page(
            slug=artifact.agent_collection,
            title="Toddy Artifacts",
            agent="agents/toddy",
        )
        page = gbrain_module.render_agent_artifact_page(artifact)

        self.assertIn("type: collection", child)
        self.assertIn("type: part_of", child)
        self.assertIn("type: artifact", page)
        self.assertIn("type: produced_for", page)
        self.assertIn("# Weekly review", page)

    def test_adapter_does_not_expose_publisher_readback_claims(self) -> None:
        artifact = self.artifact()
        page = stored_artifact(artifact)
        page["frontmatter"].update(
            {
                "sha": "publisher-asserted-sha",
                "hash": "publisher-asserted-hash",
                "verified": True,
            }
        )
        runner = StatefulArtifactRunner(
            {artifact.slug: page}, artifact_edges(artifact)
        )

        readback = GBrainAdapter(runner).get_agent_artifact(artifact.slug)
        rendered = gbrain_module.render_agent_artifact_page(readback)

        self.assertNotIn("sha", readback.to_dict())
        self.assertNotIn("hash", readback.to_dict())
        self.assertNotIn("verified", readback.to_dict())
        self.assertNotIn("publisher-asserted", rendered)




class ProjectPersistenceContinuationTests(unittest.TestCase):
    def test_reports_invalid_linked_pages_without_hiding_valid_tasks(self) -> None:
        valid = new_inbox_task(
            "Valid task",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        invalid_page = stored_page(valid)
        invalid_page["slug"] = "tasks/missing-due"
        invalid_page["frontmatter"]["due_day"] = "none"
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": valid.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        },
                        {
                            "from_slug": "tasks/missing-due",
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        },
                    ]
                ],
                "get_page": [stored_page(valid), invalid_page],
                "get_links": [[], []],
            }
        )

        result = GBrainAdapter(runner).list_collection_tasks(ACTIVE_ROOT)

        self.assertEqual([item.slug for item in result.tasks], [valid.slug])
        self.assertEqual(result.issues[0].slug, "tasks/missing-due")
        self.assertIn("due_day", result.issues[0].message)

    def test_loads_legacy_untyped_membership_when_collection_matches_root(self) -> None:
        task = new_inbox_task(
            "Apply for five more companies",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        page = stored_page(task)
        page["frontmatter"].pop("links")
        page["frontmatter"]["collection"] = ACTIVE_ROOT
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "",
                        }
                    ]
                ],
                "get_page": [page],
                "get_links": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "",
                        }
                    ]
                ],
            }
        )

        result = GBrainAdapter(runner).list_collection_tasks(ACTIVE_ROOT)

        self.assertEqual([item.slug for item in result.tasks], [task.slug])
        self.assertEqual(result.tasks[0].lifecycle_root, ACTIVE_ROOT)
        self.assertEqual(result.issues[0].severity, "warning")
        self.assertIn("legacy untyped", result.issues[0].message.lower())

    def test_exact_task_read_accepts_legacy_missing_detail_as_empty_text(self) -> None:
        task = new_inbox_task(
            "Legacy detail-free task",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        page = stored_page(task)
        page["frontmatter"].pop("detail")
        runner = FakeRunner(
            {
                "get_page": [page],
                "get_links": [
                    [],
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        }
                    ],
                ],
            }
        )

        payload = GBrainAdapter(runner).get_task_api_payload(task.slug)

        self.assertEqual(payload["slug"], task.slug)
        self.assertEqual(payload["detail"], "")
        self.assertEqual(payload["status"], "planned")

    def test_exact_task_read_accepts_legacy_missing_summary_from_title(self) -> None:
        task = new_inbox_task(
            "Legacy summary-free task",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        page = stored_page(task)
        page["frontmatter"].pop("summary")
        runner = FakeRunner(
            {
                "get_page": [page],
                "get_links": [
                    [],
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        }
                    ],
                ],
            }
        )

        payload = GBrainAdapter(runner).get_task_api_payload(task.slug)

        self.assertEqual(payload["slug"], task.slug)
        self.assertEqual(payload["title"], "Legacy summary-free task")
        self.assertEqual(payload["summary"], "Legacy summary-free task")

    def test_typed_membership_wins_over_duplicate_legacy_backlinks(self) -> None:
        task = new_inbox_task(
            "One canonical task",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "",
                        },
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        },
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        },
                    ]
                ],
                "get_page": [stored_page(task)],
                "get_links": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        }
                    ]
                ],
            }
        )

        result = GBrainAdapter(runner).list_collection_tasks(ACTIVE_ROOT)

        self.assertEqual([item.slug for item in result.tasks], [task.slug])
        self.assertEqual(result.issues, ())
        self.assertEqual(
            [tool for tool, _params in runner.calls].count("get_page"),
            1,
        )

    def test_does_not_accept_untyped_backlink_without_matching_collection(self) -> None:
        task = new_inbox_task(
            "Unrelated mention",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "",
                        }
                    ]
                ],
                "get_page": [stored_page(task)],
                "get_links": [[]],
            }
        )

        result = GBrainAdapter(runner).list_collection_tasks(ACTIVE_ROOT)

        self.assertEqual(result.tasks, ())
        self.assertEqual(result.issues, ())

    def test_shows_task_shaped_legacy_page_with_wrong_type_as_warning(self) -> None:
        task = new_inbox_task(
            "Complete the Career Upbeat Project",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        page = stored_page(task)
        page["type"] = "concept"
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        }
                    ]
                ],
                "get_page": [page],
                "get_links": [[]],
            }
        )

        result = GBrainAdapter(runner).list_collection_tasks(ACTIVE_ROOT)

        self.assertEqual([item.slug for item in result.tasks], [task.slug])
        self.assertEqual(result.issues[0].severity, "warning")
        self.assertIn("concept", result.issues[0].message)

    def test_legacy_optional_goal_read_failure_does_not_hide_core_valid_task(self) -> None:
        task = new_inbox_task(
            "Core-valid task",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        page = stored_page(task)
        page["frontmatter"].pop("links")
        page["frontmatter"]["collection"] = ACTIVE_ROOT
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "",
                        }
                    ]
                ],
                "get_page": [page],
                "get_links": [GBrainCommandError("relationship service unavailable")],
            }
        )

        result = GBrainAdapter(runner).list_collection_tasks(ACTIVE_ROOT)

        self.assertEqual([item.slug for item in result.tasks], [task.slug])
        self.assertEqual(result.tasks[0].goal, None)
        self.assertTrue(
            any(
                issue.severity == "warning" and "relationships" in issue.message
                for issue in result.issues
            )
        )

    def test_multiple_optional_goal_edges_warn_and_do_not_hide_task(self) -> None:
        task = new_inbox_task(
            "Task with malformed optional goals",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        page = stored_page(task)
        page["frontmatter"]["links"] = [
            {"to": ACTIVE_ROOT, "type": "member_of"},
            {"to": "goals/one", "type": "advances_goal"},
            {"to": "goals/two", "type": "advances_goal"},
        ]
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        }
                    ]
                ],
                "get_page": [page],
            }
        )

        result = GBrainAdapter(runner).list_collection_tasks(ACTIVE_ROOT)

        self.assertEqual([item.slug for item in result.tasks], [task.slug])
        self.assertIsNone(result.tasks[0].goal)
        self.assertEqual(result.issues[0].severity, "warning")
        self.assertIn("multiple", result.issues[0].message.lower())

    def test_rejects_an_unapproved_collection_root(self) -> None:
        with self.assertRaisesRegex(ValueError, "approved"):
            GBrainAdapter(FakeRunner({})).list_collection_tasks("index")


class LifecycleRepairTests(unittest.TestCase):
    def test_archives_completed_tony_task_after_next_monday_boundary(self) -> None:
        completed_at = datetime.fromisoformat("2026-08-12T15:30:00-07:00")
        now = datetime.fromisoformat("2026-08-17T00:05:00-07:00")
        task = replace(
            new_inbox_task("Archive after Monday", completed_at, "archive01"),
            status="completed",
            completed_at=completed_at,
        )
        initial_page = stored_page(task)
        initial_page["frontmatter"]["status"] = "completed"
        initial_page["frontmatter"]["completed_at"] = completed_at.isoformat()
        initial_page["frontmatter"]["captured_via"] = "manual"
        other_edge = {
            "from_slug": task.slug,
            "to_slug": "goals/keep-history",
            "link_type": "advances_goal",
        }
        active_edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        completed_edge = {
            "from_slug": task.slug,
            "to_slug": COMPLETED_ROOT,
            "link_type": "member_of",
        }
        archived_page = deepcopy(initial_page)
        archived_page["frontmatter"]["links"] = [
            {"to": COMPLETED_ROOT, "type": "member_of"}
        ]
        runner = FakeRunner(
            {
                "get_page": [initial_page, archived_page],
                "get_links": [[active_edge, other_edge], [completed_edge, other_edge]],
                "put_page": [{"slug": task.slug}],
                "add_link": [completed_edge],
                "remove_link": [{"removed": True}],
            }
        )

        receipt = GBrainAdapter(runner).archive_due_completed_tony_tasks(
            now,
            task_slugs=[task.slug],
        )

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.archived_slugs, (task.slug,))
        self.assertEqual(receipt.skipped_slugs, ())
        self.assertEqual(receipt.issue_count, 0)
        self.assertIn(
            (
                "add_link",
                {
                    "from": task.slug,
                    "to": COMPLETED_ROOT,
                    "link_type": "member_of",
                    "context": "Mission Control completed-task archive boundary.",
                    "link_source": "gtasks",
                },
            ),
            runner.calls,
        )
        self.assertIn(
            (
                "remove_link",
                {
                    "from": task.slug,
                    "to": ACTIVE_ROOT,
                    "link_type": "member_of",
                },
            ),
            runner.calls,
        )
        written = next(
            params["content"]
            for tool, params in runner.calls
            if tool == "put_page"
        )
        self.assertIn(COMPLETED_ROOT, written)
        self.assertIn('captured_via: "manual"', written)
        self.assertIn(completed_at.isoformat(), written)

    def test_archive_boundary_skips_completed_task_before_next_monday(self) -> None:
        completed_at = datetime.fromisoformat("2026-08-12T15:30:00-07:00")
        now = datetime.fromisoformat("2026-08-16T23:59:00-07:00")
        task = replace(
            new_inbox_task("Not due yet", completed_at, "archive02"),
            status="completed",
            completed_at=completed_at,
        )
        page = stored_page(task)
        page["frontmatter"]["status"] = "completed"
        page["frontmatter"]["completed_at"] = completed_at.isoformat()
        active_edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "get_page": [page],
                "get_links": [[active_edge]],
            }
        )

        receipt = GBrainAdapter(runner).archive_due_completed_tony_tasks(
            now,
            task_slugs=[task.slug],
        )

        self.assertEqual(receipt.archived_slugs, ())
        self.assertEqual(receipt.skipped_slugs, (task.slug,))
        self.assertNotIn("put_page", [tool for tool, _params in runner.calls])
        self.assertNotIn("remove_link", [tool for tool, _params in runner.calls])

    def test_archive_boundary_is_idempotent_for_already_archived_task(self) -> None:
        completed_at = datetime.fromisoformat("2026-08-12T15:30:00-07:00")
        now = datetime.fromisoformat("2026-08-17T00:05:00-07:00")
        task = replace(
            new_inbox_task("Already archived", completed_at, "archive03"),
            status="completed",
            lifecycle_root=COMPLETED_ROOT,
            completed_at=completed_at,
        )
        page = stored_page(task)
        page["frontmatter"]["status"] = "completed"
        page["frontmatter"]["completed_at"] = completed_at.isoformat()
        page["frontmatter"]["links"] = [{"to": COMPLETED_ROOT, "type": "member_of"}]
        completed_edge = {
            "from_slug": task.slug,
            "to_slug": COMPLETED_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "get_page": [page],
                "get_links": [[completed_edge]],
            }
        )

        receipt = GBrainAdapter(runner).archive_due_completed_tony_tasks(
            now,
            task_slugs=[task.slug],
        )

        self.assertEqual(receipt.archived_slugs, ())
        self.assertEqual(receipt.skipped_slugs, (task.slug,))
        self.assertTrue(receipt.verified)
        self.assertNotIn("put_page", [tool for tool, _params in runner.calls])

    def test_archive_boundary_reports_partial_readback_without_success(self) -> None:
        completed_at = datetime.fromisoformat("2026-08-12T15:30:00-07:00")
        now = datetime.fromisoformat("2026-08-17T00:05:00-07:00")
        task = replace(
            new_inbox_task("Partial archive", completed_at, "archive04"),
            status="completed",
            completed_at=completed_at,
        )
        initial_page = stored_page(task)
        initial_page["frontmatter"]["status"] = "completed"
        initial_page["frontmatter"]["completed_at"] = completed_at.isoformat()
        active_edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        completed_edge = {
            "from_slug": task.slug,
            "to_slug": COMPLETED_ROOT,
            "link_type": "member_of",
        }
        unverified_page = deepcopy(initial_page)
        runner = FakeRunner(
            {
                "get_page": [initial_page, unverified_page],
                "get_links": [[active_edge], [active_edge, completed_edge]],
                "put_page": [{"slug": task.slug}],
                "add_link": [completed_edge],
                "remove_link": [{"removed": True}],
            }
        )

        receipt = GBrainAdapter(runner).archive_due_completed_tony_tasks(
            now,
            task_slugs=[task.slug],
        )

        self.assertFalse(receipt.verified)
        self.assertEqual(receipt.archived_slugs, ())
        self.assertEqual(receipt.skipped_slugs, ())
        self.assertEqual(receipt.issue_count, 1)
        self.assertEqual(receipt.issues[0].slug, task.slug)
        self.assertIn("not verified", receipt.issues[0].impact)

    def test_repairs_unambiguous_legacy_active_membership_with_readback(self) -> None:
        task = new_inbox_task(
            "Repair active membership",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        legacy_page = stored_page(task)
        legacy_page["frontmatter"].pop("links")
        legacy_page["frontmatter"]["collection"] = ACTIVE_ROOT
        legacy_edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "",
        }
        typed_edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "get_page": [legacy_page, stored_page(task)],
                "get_links": [[legacy_edge], [typed_edge]],
                "put_page": [{"slug": task.slug}],
                "add_link": [typed_edge],
                "remove_link": [{"removed": True}],
            }
        )

        receipt = GBrainAdapter(runner).repair_active_membership(task.slug)

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.task_slug, task.slug)
        self.assertIn(
            ("add_link", {
                "from": task.slug,
                "to": ACTIVE_ROOT,
                "link_type": "member_of",
                "context": "GTasks active task membership repair.",
                "link_source": "gtasks",
            }),
            runner.calls,
        )
        written = next(
            params["content"]
            for tool, params in runner.calls
            if tool == "put_page"
        )
        self.assertIn('"type": "member_of"', written)
        self.assertIn(ACTIVE_ROOT, written)

    def test_refuses_repair_without_exact_legacy_collection_contract(self) -> None:
        task = new_inbox_task(
            "Not eligible",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        runner = FakeRunner(
            {
                "get_page": [stored_page(task)],
                "get_links": [[]],
            }
        )

        with self.assertRaisesRegex(ValueError, "not eligible"):
            GBrainAdapter(runner).repair_active_membership(task.slug)

        self.assertNotIn(
            "put_page",
            [tool for tool, _params in runner.calls],
        )


class GoalMutationTests(unittest.TestCase):
    def test_create_goal_writes_and_reads_typed_collection_membership(self) -> None:
        goal = new_goal(
            title="Launch the pilot",
            outcome="The pilot is live.",
            success_criteria="Ten users complete the workflow.",
            strategy="Ship one validated slice each week.",
            review_cadence="weekly",
            constraints="Keep customer data local.",
            now=datetime(2026, 7, 30, tzinfo=timezone.utc),
            identity="a1b2c3",
        )
        page = stored_goal(goal.slug, goal.title)
        page["frontmatter"].update(
            {
                "outcome": goal.outcome,
                "success_criteria": goal.success_criteria,
                "target_day": goal.target_day.isoformat(),
                "strategy": goal.strategy,
                "review_cadence": goal.review_cadence,
                "constraints": goal.constraints,
                "links": [{"to": GOALS_ROOT, "type": "member_of"}],
            }
        )
        edge = {
            "from_slug": goal.slug,
            "to_slug": GOALS_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "put_page": [{"slug": goal.slug}],
                "get_page": [page],
                "add_link": [{}],
                "get_links": [[edge]],
            }
        )

        receipt = GBrainAdapter(runner).create_goal(goal)

        self.assertTrue(receipt.verified)
        self.assertIn("type: goal", runner.calls[0][1]["content"])
        self.assertIn("type: member_of", runner.calls[0][1]["content"])

    def test_pause_preserves_goal_type_content_and_relationships(self) -> None:
        page = stored_goal("goals/pause-me", "Pause me")
        # GBrain's raw storage type for Markdown-backed Goals is intentionally
        # concept; the validated frontmatter contract remains type: goal.
        page["type"] = "concept"
        page["frontmatter"]["type"] = "goal"
        edge = {
            "from_slug": page["slug"],
            "to_slug": "tasks/linked",
            "link_type": "advanced_by",
        }
        paused_page = deepcopy(page)
        paused_page["frontmatter"]["status"] = "paused"
        runner = FakeRunner(
            {
                "get_page": [page, paused_page],
                "get_links": [[], [edge], [edge]],
                "put_page": [{"slug": page["slug"]}],
            }
        )

        receipt = GBrainAdapter(runner).set_goal_paused(page["slug"])

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.goal.status, "paused")
        written = next(
            params["content"] for tool, params in runner.calls if tool == "put_page"
        )
        self.assertIn('type: "goal"', written)
        self.assertIn('status: "paused"', written)

    def test_goal_update_accepts_gbrain_raw_concept_with_canonical_goal_frontmatter(self) -> None:
        page = stored_goal("goals/compiled-goal", "Compiled goal")
        page["type"] = "concept"
        page["frontmatter"]["type"] = "goal"
        updated = deepcopy(page)
        updated["frontmatter"].update({
            "title": "Renamed label", "outcome": "A revised outcome.",
        })
        runner = FakeRunner({
            "get_page": [page, updated],
            "get_links": [[], []],
            "put_page": [{"slug": page["slug"]}],
        })

        receipt = GBrainAdapter(runner).update_goal(
            page["slug"], title="Renamed label", outcome="A revised outcome.",
            success_criteria="Define during weekly review.",
            strategy="Define during weekly review.", review_cadence="weekly",
            constraints="Define during weekly review.", target_day=date(2026, 9, 30),
        )

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.goal.title, "Renamed label")

    def test_delete_without_linked_tasks_is_verified_recoverable_soft_delete(self) -> None:
        page = stored_goal("goals/delete-me", "Delete me")
        deleted_page = {**page, "deleted_at": "2026-07-30T20:00:00Z"}
        runner = FakeRunner(
            {
                "get_page": [page, deleted_page],
                "get_links": [[], []],
                "get_backlinks": [[], []],
                "delete_page": [{"slug": page["slug"]}],
            }
        )

        receipt = GBrainAdapter(runner).delete_goal(page["slug"])

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.recoverable_until_hours, 72)
        self.assertIn(
            (
                "get_page",
                {"slug": page["slug"], "include_deleted": True},
            ),
            runner.calls,
        )

    def test_delete_unlinks_both_directions_without_deleting_linked_task(self) -> None:
        page = stored_goal("goals/delete-linked", "Delete linked")
        task_slug = "tasks/keep-me"
        outgoing = {
            "from_slug": page["slug"],
            "to_slug": task_slug,
            "link_type": "advanced_by",
        }
        incoming = {
            "from_slug": task_slug,
            "to_slug": page["slug"],
            "link_type": "advances_goal",
        }
        deleted_page = {**page, "deleted_at": "2026-07-30T20:00:00Z"}
        runner = FakeRunner(
            {
                "get_page": [page, deleted_page],
                "get_links": [[outgoing], []],
                "get_backlinks": [[incoming], []],
                "delete_page": [{"slug": page["slug"]}],
            }
        )

        class DeleteAdapter(GBrainAdapter):
            def __init__(self) -> None:
                super().__init__(runner)
                self.goal_updates: list[tuple[str, str | None]] = []

            def set_task_goal(
                self,
                task_slug: str,
                goal_slug: str | None,
            ) -> GoalLinkReceipt:
                self.goal_updates.append((task_slug, goal_slug))
                return GoalLinkReceipt(
                    task_slug=task_slug,
                    goal_slug=goal_slug,
                    verified=True,
                )

        adapter = DeleteAdapter()
        receipt = adapter.delete_goal(page["slug"])

        self.assertEqual(adapter.goal_updates, [(task_slug, None)])
        self.assertEqual(receipt.removed_task_links, (task_slug,))
        self.assertNotIn(
            "delete_page",
            [
                tool
                for tool, params in runner.calls
                if params.get("slug") == task_slug
            ],
        )


class GoalReadTests(unittest.TestCase):
    def test_discovers_every_direct_goal_backlink_dynamically(self) -> None:
        first = stored_goal("goals/one", "First goal")
        sixth = stored_goal("goals/political-action", "Help California through action")
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": first["slug"],
                            "to_slug": GOALS_ROOT,
                            "link_type": "",
                        },
                        {
                            "from_slug": sixth["slug"],
                            "to_slug": GOALS_ROOT,
                            "link_type": "",
                        },
                        {
                            "from_slug": "notes/not-a-goal",
                            "to_slug": GOALS_ROOT,
                            "link_type": "mentions",
                        },
                    ]
                ],
                "get_page": [first, sixth],
            }
        )

        result = GBrainAdapter(runner).list_goals()

        self.assertEqual(
            [goal.slug for goal in result.goals],
            ["goals/one", "goals/political-action"],
        )
        self.assertNotIn("list_pages", [tool for tool, _ in runner.calls])

    def test_goal_read_deduplicates_repeated_root_backlinks_by_slug(self) -> None:
        goal = stored_goal("goals/one", "Same visible title")
        distinct = stored_goal("goals/two", "Same visible title")
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": goal["slug"],
                            "to_slug": GOALS_ROOT,
                            "link_type": "member_of",
                        },
                        {
                            "from_slug": goal["slug"],
                            "to_slug": GOALS_ROOT,
                            "link_type": "member_of",
                        },
                        {
                            "from_slug": distinct["slug"],
                            "to_slug": GOALS_ROOT,
                            "link_type": "member_of",
                        },
                    ]
                ],
                "get_page": [goal, distinct],
            }
        )

        result = GBrainAdapter(runner).list_goals()

        self.assertEqual(
            [item.slug for item in result.goals],
            [goal["slug"], distinct["slug"]],
        )
        self.assertEqual(
            [params["slug"] for tool, params in runner.calls if tool == "get_page"],
            [goal["slug"], distinct["slug"]],
        )
        self.assertEqual(result.issues, ())

    def test_ignores_goal_like_concepts_that_only_mention_goal_root(self) -> None:
        goal = stored_goal("goals/one", "First goal")
        legacy_concept = {
            "slug": "goals/friendship",
            "type": "concept",
            "title": "Friendship",
            "compiled_truth": "# Friendship\n\nMentions [[collections/tonys-goals]].",
            "frontmatter": {},
        }
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": legacy_concept["slug"],
                            "to_slug": GOALS_ROOT,
                            "link_type": "mentions",
                        },
                        {
                            "from_slug": goal["slug"],
                            "to_slug": GOALS_ROOT,
                            "link_type": "member_of",
                        },
                    ]
                ],
                "get_page": [goal],
            }
        )

        result = GBrainAdapter(runner).list_goals()

        self.assertEqual([item.slug for item in result.goals], [goal["slug"]])
        self.assertEqual(result.issues, ())
        self.assertNotIn(
            ("get_page", {"slug": legacy_concept["slug"]}),
            runner.calls,
        )

    def test_reads_reciprocal_task_slugs_only_for_selected_goal_detail(self) -> None:
        goal = stored_goal("goals/one", "First goal")
        runner = FakeRunner(
            {
                "get_page": [goal],
                "get_links": [
                    [],
                    [
                        {
                            "from_slug": goal["slug"],
                            "to_slug": "tasks/first",
                            "link_type": "advanced_by",
                        }
                    ]
                ],
            }
        )

        result = GBrainAdapter(runner).read_goal_relationships(goal["slug"])

        self.assertEqual(result.task_slugs, ("tasks/first",))
        self.assertEqual(
            runner.calls,
            [
                ("get_links", {"slug": goal["slug"]}),
                ("get_page", {"slug": goal["slug"]}),
                ("get_links", {"slug": goal["slug"]}),
            ],
        )


class InboxMutationTests(unittest.TestCase):
    def test_verified_full_task_creation_path_is_available(self) -> None:
        self.assertTrue(
            callable(getattr(GBrainAdapter(FakeRunner({})), "create_task", None))
        )

    def test_verified_duplicate_creation_path_is_available(self) -> None:
        self.assertTrue(
            callable(getattr(GBrainAdapter(FakeRunner({})), "duplicate_task", None))
        )

    def test_full_creation_serializes_and_reads_back_optional_metric(self) -> None:
        metric = ProgressMetric.from_value(
            {
                "kind": "count",
                "label": "Job applications",
                "unit": "job_application",
                "target": 5,
                "current": 2,
                "event_binding": None,
                "auto_complete": False,
                "task_day": None,
                "timezone": None,
            }
        )
        task = new_task(
            title="Apply for five companies",
            progress_metric=metric,
            now=datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
            identity="metric1",
        )
        page = marked_stored_page(task)
        page["frontmatter"]["progress_metric"] = metric.to_dict()
        edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "put_page": [{"slug": task.slug}],
                "get_page": [page, page, page],
                "add_link": [edge],
                "get_links": [[], [edge], [edge]],
            }
        )

        receipt = GBrainAdapter(runner).create_task(task)

        self.assertTrue(receipt.verified)
        content = runner.calls[0][1]["content"]
        self.assertIn("progress_metric:", content)
        self.assertIn('"label": "Job applications"', content)
        self.assertIn('"unit": "job_application"', content)
        self.assertIn('"current": 2', content)
        self.assertIn("event_progress: null", content)

    def test_full_creation_verifies_project_and_bidirectional_goal_links(self) -> None:
        project = new_project(
            "Job Search",
            datetime(2026, 7, 30, 9, tzinfo=timezone.utc),
            "project1",
        )
        goal_page = stored_goal("goals/get-a-job", "Get a job")
        task = new_task(
            title="Apply for five companies",
            project=project.slug,
            goal=goal_page["slug"],
            now=datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
            identity="linked1",
        )
        page = marked_stored_page(task)
        page["frontmatter"]["project"] = project.slug
        page["frontmatter"]["links"].append(
            {"to": project.slug, "type": "member_of"}
        )

        class CreationRunner:
            def __init__(self) -> None:
                self.calls = []
                self.links = {
                    (project.slug, PROJECTS_ROOT, "member_of"),
                }

            def run(self, tool: str, params: dict) -> object:
                self.calls.append((tool, params))
                if tool == "put_page":
                    return {"slug": task.slug}
                if tool == "get_page":
                    if params["slug"] == project.slug:
                        return stored_project(project)
                    if params["slug"] == goal_page["slug"]:
                        return goal_page
                    if params["slug"] == task.slug:
                        return page
                if tool == "add_link":
                    self.links.add(
                        (
                            params["from"],
                            params["to"],
                            params["link_type"],
                        )
                    )
                    return {}
                if tool == "get_links":
                    slug = params["slug"]
                    return [
                        {
                            "from_slug": source,
                            "to_slug": target,
                            "link_type": link_type,
                        }
                        for source, target, link_type in sorted(self.links)
                        if source == slug or target == slug
                    ]
                raise AssertionError(f"unexpected {tool}: {params}")

        runner = CreationRunner()

        receipt = GBrainAdapter(runner).create_task(task)

        self.assertTrue(receipt.verified)
        self.assertIn((task.slug, project.slug, "member_of"), runner.links)
        self.assertIn((task.slug, goal_page["slug"], "advances_goal"), runner.links)
        self.assertIn((goal_page["slug"], task.slug, "advanced_by"), runner.links)

    def test_agent_creation_verifies_one_scope_and_one_assigned_to_edge(
        self,
    ) -> None:
        now = datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc)
        ticket_slug = "tasks/fad23bf2-571f-4db0-b9f5-07ab52ae8620"
        base = new_task(
            title="Prepare a wellbeing update",
            detail=f"Use {ticket_slug} for the canonical handoff.",
            next_action="Draft three bullets",
            now=now,
            identity="agent12",
        )
        agent_slug = "agents/toddy"
        work_root = "collections/toddys-tasks"
        task = replace(
            base,
            lifecycle_root=work_root,
            owner_agent=agent_slug,
        )
        page = marked_stored_page(task)
        page["frontmatter"]["links"] = [
            {"to": work_root, "type": "member_of"},
            {"to": agent_slug, "type": "assigned_to"},
        ]
        page["frontmatter"]["created_at"] = now.isoformat()
        page["frontmatter"]["updated_at"] = now.isoformat()
        page["compiled_markdown"] = render_task_body(
            task.title,
            task.detail,
            {ticket_slug: SystemTicketReference(ticket_slug, "Dispatcher")},
        )
        agent_page = {
            "slug": agent_slug,
            "type": "agent",
            "title": "Agent Toddy",
            "compiled_truth": "# Agent Toddy",
            "frontmatter": {},
        }

        class AgentCreationRunner:
            def __init__(self) -> None:
                self.links: set[tuple[str, str, str]] = set()
                self.calls = []

            def run(self, tool: str, params: dict) -> object:
                self.calls.append((tool, deepcopy(params)))
                if tool == "list_pages":
                    return [agent_page]
                if tool == "put_page":
                    return {"slug": task.slug}
                if tool == "get_page":
                    if params["slug"] == ticket_slug:
                        return {
                            "slug": ticket_slug,
                            "type": "task",
                            "title": "Dispatcher",
                            "frontmatter": {
                                "type": "task",
                                "title": "Dispatcher",
                                "status": "planned",
                                "priority": "normal",
                                "verbatim_request": "Canonical dispatcher request.",
                                "target_subsystem": "mission_control",
                                "links": [{"to": SYSTEM_TICKETS_ROOT, "type": "member_of"}],
                            },
                        }
                    return agent_page if params["slug"] == agent_slug else page
                if tool == "get_links":
                    slug = params["slug"]
                    if slug == ticket_slug:
                        return [{
                            "from_slug": ticket_slug,
                            "to_slug": SYSTEM_TICKETS_ROOT,
                            "link_type": "member_of",
                        }]
                    return [
                        {
                            "from_slug": source,
                            "to_slug": target,
                            "link_type": link_type,
                        }
                        for source, target, link_type in sorted(self.links)
                        if source == slug or target == slug
                    ]
                if tool == "add_link":
                    self.links.add(
                        (
                            params["from"],
                            params["to"],
                            params["link_type"],
                        )
                    )
                    return {}
                raise AssertionError(f"unexpected {tool}: {params}")

        runner = AgentCreationRunner()

        receipt = GBrainAdapter(runner).create_agent_task(task, agent_slug)

        self.assertTrue(receipt.verified)
        self.assertEqual(
            {
                edge
                for edge in runner.links
                if edge[2] == "member_of"
                and edge[1] in {
                    ACTIVE_ROOT,
                    COMPLETED_ROOT,
                    "collections/toddys-tasks",
                    "collections/timmys-tasks",
                    "collections/tammys-tasks",
                }
            },
            {(task.slug, work_root, "member_of")},
        )
        self.assertIn((task.slug, agent_slug, "assigned_to"), runner.links)
        body = next(
            params["content"].split("\n---\n", 1)[1].strip()
            for tool, params in runner.calls
            if tool == "put_page"
        )
        self.assertEqual(body, page["compiled_markdown"])

    def test_writes_page_and_edge_then_verifies_both(self) -> None:
        task = new_inbox_task(
            "Create the launch brief",
            datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
            "a1b2c3",
        )
        edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
            "link_source": "gtasks",
        }
        runner = FakeRunner(
            {
                "put_page": [{"slug": task.slug}],
                "get_page": [marked_stored_page(task), marked_stored_page(task)],
                "add_link": [edge],
                "get_links": [[], [edge]],
            }
        )

        result = GBrainAdapter(runner).create_inbox(task)

        self.assertEqual(result.slug, task.slug)
        self.assertTrue(result.verified)
        tools = [tool for tool, _ in runner.calls]
        self.assertEqual(
            tools,
            [
                "put_page",
                "get_page",
                "get_links",
                "add_link",
                "get_links",
                "get_page",
            ],
        )
        content = runner.calls[0][1]["content"]
        self.assertIn('due_day: "2026-07-30"', content)
        self.assertNotIn("due_day: none", content)
        self.assertEqual(
            runner.calls[3],
            (
                "add_link",
                {
                    "from": task.slug,
                    "to": ACTIVE_ROOT,
                    "link_type": "member_of",
                    "context": "GTasks active task membership.",
                    "link_source": "gtasks",
                },
            ),
        )

    def test_rejects_uuid_derived_title_readback(self) -> None:
        task = new_inbox_task(
            "Apply for EDD unemployment insurance",
            datetime(2026, 8, 9, 14, 22, tzinfo=timezone.utc),
            "title1",
        )
        malformed = stored_page(task)
        malformed["title"] = task.slug.rsplit("/", 1)[-1].replace("-", " ").title()
        runner = FakeRunner(
            {
                "put_page": [{"slug": task.slug}],
                "get_page": [malformed],
            }
        )

        with self.assertRaises(PartialMutationError) as raised:
            GBrainAdapter(runner).create_inbox(task)

        self.assertEqual(raised.exception.slug, task.slug)
        self.assertIn("page readback", str(raised.exception))
        self.assertNotIn("add_link", [tool for tool, _ in runner.calls])

    def test_surfaces_a_partial_write_if_edge_readback_fails(self) -> None:
        task = new_inbox_task(
            "Create the launch brief",
            datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
            "a1b2c3",
        )
        runner = FakeRunner(
            {
                "put_page": [{"slug": task.slug}],
                "get_page": [marked_stored_page(task)],
                "add_link": [{}],
                "get_links": [[], []],
            }
        )

        with self.assertRaises(PartialMutationError) as raised:
            GBrainAdapter(runner).create_inbox(task)

        self.assertEqual(raised.exception.slug, task.slug)
        self.assertIn("membership", str(raised.exception))

    def test_refuses_duplicate_lifecycle_membership_without_adding_another_edge(self) -> None:
        task = new_inbox_task(
            "Keep one lifecycle edge",
            datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
            "oneedge",
        )
        duplicate_edges = [
            {"from_slug": task.slug, "to_slug": ACTIVE_ROOT, "link_type": "member_of"},
            {"from_slug": task.slug, "to_slug": ACTIVE_ROOT, "link_type": "member_of"},
        ]
        runner = FakeRunner(
            {
                "put_page": [{"slug": task.slug}],
                "get_page": [marked_stored_page(task)],
                "get_links": [duplicate_edges],
            }
        )

        with self.assertRaises(PartialMutationError) as raised:
            GBrainAdapter(runner).create_inbox(task)

        self.assertIn("2 verified lifecycle memberships", str(raised.exception))
        self.assertNotIn("add_link", [tool for tool, _ in runner.calls])


class GoalLinkMutationTests(unittest.TestCase):
    def test_adds_and_verifies_both_goal_edges_for_approved_nodes(self) -> None:
        task = new_inbox_task(
            "Create the launch brief",
            datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
            "a1b2c3",
        )
        goal = stored_goal("goals/ship-product", "Ship the product")
        goal_edge = {
            "from_slug": task.slug,
            "to_slug": goal["slug"],
            "link_type": "advances_goal",
            "link_source": "gtasks",
        }
        reciprocal_edge = {
            "from_slug": goal["slug"],
            "to_slug": task.slug,
            "link_type": "advanced_by",
            "link_source": "gtasks",
        }
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        }
                    ],
                    [
                        {
                            "from_slug": goal["slug"],
                            "to_slug": GOALS_ROOT,
                            "link_type": "",
                        }
                    ],
                ],
                "get_page": [stored_page(task), goal],
                "get_links": [[], [], [goal_edge], [reciprocal_edge]],
                "add_link": [goal_edge, reciprocal_edge],
            }
        )

        receipt = GBrainAdapter(runner).set_task_goal(task.slug, goal["slug"])

        self.assertTrue(receipt.verified)
        self.assertTrue(receipt.reciprocal_verified)
        self.assertEqual(receipt.goal_slug, goal["slug"])
        self.assertIn(
            (
                "add_link",
                {
                    "from": task.slug,
                    "to": goal["slug"],
                    "link_type": "advances_goal",
                    "context": "This task advances the linked Tony goal.",
                    "link_source": "gtasks",
                },
            ),
            runner.calls,
        )
        self.assertIn(
            (
                "add_link",
                {
                    "from": goal["slug"],
                    "to": task.slug,
                    "link_type": "advanced_by",
                    "context": "This goal is advanced by the linked GTasks task.",
                    "link_source": "gtasks",
                },
            ),
            runner.calls,
        )

    def test_unchanged_selection_repairs_a_missing_reciprocal_edge(self) -> None:
        task = new_inbox_task(
            "Create the launch brief",
            datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
            "a1b2c3",
        )
        goal = stored_goal("goals/ship-product", "Ship the product")
        forward = {
            "from_slug": task.slug,
            "to_slug": goal["slug"],
            "link_type": "advances_goal",
        }
        reverse = {
            "from_slug": goal["slug"],
            "to_slug": task.slug,
            "link_type": "advanced_by",
        }
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        }
                    ],
                    [
                        {
                            "from_slug": goal["slug"],
                            "to_slug": GOALS_ROOT,
                            "link_type": "",
                        }
                    ],
                ],
                "get_page": [stored_page(task), goal],
                "get_links": [[forward], [], [forward], [reverse]],
                "add_link": [reverse],
            }
        )

        receipt = GBrainAdapter(runner).set_task_goal(task.slug, goal["slug"])

        self.assertTrue(receipt.reconciled)
        self.assertEqual(
            [call for call in runner.calls if call[0] == "add_link"],
            [
                (
                    "add_link",
                    {
                        "from": goal["slug"],
                        "to": task.slug,
                        "link_type": "advanced_by",
                        "context": "This goal is advanced by the linked GTasks task.",
                        "link_source": "gtasks",
                    },
                )
            ],
        )

    def test_clears_both_relationship_directions_and_verifies_removal(self) -> None:
        task = new_inbox_task(
            "Create the launch brief",
            datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
            "a1b2c3",
        )
        goal_edge = {
            "from_slug": task.slug,
            "to_slug": "goals/ship-product",
            "link_type": "advances_goal",
        }
        goal = stored_goal("goals/ship-product", "Ship the product")
        reciprocal_edge = {
            "from_slug": goal["slug"],
            "to_slug": task.slug,
            "link_type": "advanced_by",
        }
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        }
                    ],
                    [
                        {
                            "from_slug": goal["slug"],
                            "to_slug": GOALS_ROOT,
                            "link_type": "",
                        }
                    ],
                ],
                "get_page": [stored_page(task), goal],
                "get_links": [[goal_edge], [reciprocal_edge], [], []],
                "remove_link": [{}, {}],
            }
        )

        receipt = GBrainAdapter(runner).set_task_goal(task.slug, None)

        self.assertTrue(receipt.verified)
        self.assertIsNone(receipt.goal_slug)
        self.assertIn(
            (
                "remove_link",
                {
                    "from": task.slug,
                    "to": "goals/ship-product",
                    "link_type": "advances_goal",
                },
            ),
            runner.calls,
        )
        self.assertIn(
            (
                "remove_link",
                {
                    "from": goal["slug"],
                    "to": task.slug,
                    "link_type": "advanced_by",
                },
            ),
            runner.calls,
        )

    def test_replaces_both_directions_after_new_pair_is_added(self) -> None:
        task = new_inbox_task(
            "Create the launch brief",
            datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
            "a1b2c3",
        )
        old_goal = stored_goal("goals/old", "Old goal")
        new_goal = stored_goal("goals/new", "New goal")
        old_forward = {
            "from_slug": task.slug,
            "to_slug": old_goal["slug"],
            "link_type": "advances_goal",
        }
        old_reverse = {
            "from_slug": old_goal["slug"],
            "to_slug": task.slug,
            "link_type": "advanced_by",
        }
        new_forward = {
            "from_slug": task.slug,
            "to_slug": new_goal["slug"],
            "link_type": "advances_goal",
        }
        new_reverse = {
            "from_slug": new_goal["slug"],
            "to_slug": task.slug,
            "link_type": "advanced_by",
        }
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        }
                    ],
                    [
                        {
                            "from_slug": old_goal["slug"],
                            "to_slug": GOALS_ROOT,
                            "link_type": "",
                        },
                        {
                            "from_slug": new_goal["slug"],
                            "to_slug": GOALS_ROOT,
                            "link_type": "",
                        },
                    ],
                ],
                "get_page": [stored_page(task), old_goal, new_goal],
                "get_links": [
                    [old_forward],
                    [old_reverse],
                    [],
                    [new_forward],
                    [],
                    [new_reverse],
                ],
                "add_link": [new_forward, new_reverse],
                "remove_link": [{}, {}],
            }
        )

        receipt = GBrainAdapter(runner).set_task_goal(task.slug, new_goal["slug"])

        self.assertEqual(receipt.goal_slug, new_goal["slug"])
        mutation_tools = [
            tool for tool, _ in runner.calls if tool in {"add_link", "remove_link"}
        ]
        self.assertEqual(
            mutation_tools,
            ["add_link", "add_link", "remove_link", "remove_link"],
        )

    def test_rolls_back_a_partial_pair_add_and_reports_verification(self) -> None:
        task = new_inbox_task(
            "Create the launch brief",
            datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
            "a1b2c3",
        )
        goal = stored_goal("goals/ship-product", "Ship the product")
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        }
                    ],
                    [
                        {
                            "from_slug": goal["slug"],
                            "to_slug": GOALS_ROOT,
                            "link_type": "",
                        }
                    ],
                ],
                "get_page": [stored_page(task), goal],
                "get_links": [[], [], [], []],
                "add_link": [
                    {},
                    GBrainCommandError("reciprocal write failed"),
                ],
                "remove_link": [{}],
            }
        )

        with self.assertRaises(PartialMutationError) as raised:
            GBrainAdapter(runner).set_task_goal(task.slug, goal["slug"])

        self.assertIn("Rollback verified", str(raised.exception))
        self.assertIn(
            (
                "remove_link",
                {
                    "from": task.slug,
                    "to": goal["slug"],
                    "link_type": "advances_goal",
                },
            ),
            runner.calls,
        )




def canonical_projection_page_hash(page: dict) -> str:
    return hashlib.sha256(
        json.dumps(page, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()








class NoCallRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def run(self, tool: str, params: dict) -> object:
        self.calls.append((tool, deepcopy(params)))
        raise AssertionError("legacy provisioning must not call GBrain")


class AgentReadTests(unittest.TestCase):
    def test_specific_profile_read_failure_is_attributed_to_that_agent(self) -> None:
        class ProfileFailureRunner:
            def run(self, tool: str, params: dict) -> object:
                slug = params.get("slug")
                if tool == "list_pages":
                    return [
                        {
                            "slug": f"agents/{name}",
                            "type": "agent",
                            "title": name.title(),
                            "compiled_truth": "",
                            "frontmatter": {},
                        }
                        for name in ("toddy", "timmy", "tammy")
                    ]
                if tool == "get_page" and slug == "agents/toddy":
                    raise GBrainCommandError("Toddy profile unavailable")
                if tool == "get_page" and slug in {"agents/timmy", "agents/tammy"}:
                    return {
                        "slug": slug,
                        "type": "agent",
                        "title": slug.split("/")[-1].title(),
                        "compiled_truth": "",
                        "frontmatter": {},
                    }
                if tool == "get_links" and slug in {
                    "agents/toddy",
                    "agents/timmy",
                    "agents/tammy",
                }:
                    return []
                raise AssertionError((tool, params))

        read = GBrainAdapter(ProfileFailureRunner()).list_agent_profiles()

        issue = next(issue for issue in read.issues if issue.slug == "agents/toddy")
        self.assertEqual(issue.owner_agent, "agents/toddy")














class ProposalReadTests(unittest.TestCase):
    def test_proposal_projection_does_not_hydrate_unrelated_agent_task_todos(self) -> None:
        runner = FakeRunner({"get_backlinks": [[]]})
        calls: list[bool] = []

        class Adapter(GBrainAdapter):
            def list_agent_work(self, *, include_todos: bool = True):
                calls.append(include_todos)
                return AgentWorkRead(tasks=())

        Adapter(runner).list_proposals()

        self.assertEqual(calls, [False])

    def test_excludes_decided_task_even_when_stale_projection_still_marks_it_proposed(self) -> None:
        decided_at = datetime(
            2026, 8, 1, 10, tzinfo=timezone(timedelta(hours=-7))
        )
        slug = "tasks/2dcb6465-46de-45fc-b4eb-170707df3c28"
        event = {
            "event_id": "proposal-decision:2dcb6465:approve",
            "event_type": "proposal_decision",
            "occurred_at": decided_at.isoformat(),
            "actor": "people/tony-guan",
            "source": "mission_control",
            "decision": "approve",
            "decision_note": "Proceed.",
            "previous_status": "proposed",
            "resulting_status": "planned",
            "proposal_slug": slug,
        }
        task = {
            "slug": slug,
            "title": "Prepare the launch",
            "status": "proposed",
            "owner_agent": "agents/toddy",
            "proposal_recipient": "agent",
            "proposal_submitted_at": "2026-08-01T09:00:00-07:00",
            "proposal_decision": "approve",
            "proposal_decided_at": decided_at.isoformat(),
            "proposal_decision_note": "Proceed.",
            "proposal_decision_events": [event],
            "created_at": "2026-08-01T09:00:00-07:00",
            "updated_at": decided_at.isoformat(),
            "detail": "Prepare a bounded launch.",
            "next_action": "Draft the launch checklist.",
            "due_day": "2026-08-02",
            "goal": "goals/41fb50e0-e1d7-592b-b2c3-ff1f7aacff10",
        }
        runner = FakeRunner({"get_backlinks": [[]]})

        class Adapter(GBrainAdapter):
            def list_agent_work(self, *, include_todos: bool = True):
                return AgentWorkRead(tasks=(task,))

        result = Adapter(runner).list_proposals()

        self.assertEqual(result.proposals, ())

    def test_only_pending_proposed_agent_tasks_are_returned_to_inbox(self) -> None:
        base = {
            "title": "Agent proposal",
            "owner_agent": "agents/toddy",
            "proposal_recipient": "agent",
            "proposal_submitted_at": "2026-08-01T09:00:00-07:00",
            "created_at": "2026-08-01T09:00:00-07:00",
            "updated_at": "2026-08-01T09:00:00-07:00",
            "detail": "Prepare bounded work.",
            "next_action": "Draft the checklist.",
            "due_day": "2026-08-02",
            "goal": "goals/41fb50e0-e1d7-592b-b2c3-ff1f7aacff10",
        }
        tasks = tuple(
            {
                **base,
                "slug": f"tasks/{status}",
                "status": status,
                "proposal_decision": None,
            }
            for status in (
                "proposed",
                "planned",
                "active",
                "blocked",
                "completed",
                "cancelled",
            )
        )
        rejected_stale = {
            **base,
            "slug": "tasks/rejected-stale-proposed",
            "status": "proposed",
            "proposal_decision": "reject",
        }
        runner = FakeRunner({"get_backlinks": [[]]})

        class Adapter(GBrainAdapter):
            def list_agent_work(self, *, include_todos: bool = True):
                return AgentWorkRead(tasks=(*tasks, rejected_stale))

        result = Adapter(runner).list_proposals()

        self.assertEqual(
            [proposal.slug for proposal in result.proposals],
            ["tasks/proposed"],
        )

    def test_excludes_decided_legacy_proposals_from_active_review(self) -> None:
        slug = "proposals/tammy-decided-legacy"
        page = {
            "slug": slug,
            "type": "task_proposal",
            "title": "Historical Tammy proposal",
            "compiled_truth": "# Historical Tammy proposal",
            "frontmatter": {
                "status": "approved",
                "recipient": "agent",
                "proposing_agent": "agents/tammy",
                "rationale": "Historical compatibility record.",
                "proposed_next_step": "Use the linked canonical task.",
                "due_day": "2026-07-31",
                "submitted_at": "2026-07-30T14:00:00-07:00",
                "updated_at": "2026-07-30T15:00:00-07:00",
            },
        }
        edges = [
            {"from_slug": slug, "to_slug": PROPOSALS_ROOT, "link_type": "member_of"},
            {"from_slug": slug, "to_slug": "agents/tammy", "link_type": "proposed_by"},
        ]
        runner = FakeRunner(
            {
                "get_backlinks": [[edges[0]]],
                "get_page": [page],
                "get_links": [edges],
            }
        )

        result = GBrainAdapter(runner).list_proposals()

        self.assertEqual(result.proposals, ())

    def test_excludes_legacy_review_projection_until_it_is_canonical_proposed(self) -> None:
        slug = "proposals/tammy-review-legacy"
        page = {
            "slug": slug,
            "type": "task_proposal",
            "title": "Historical review projection",
            "compiled_truth": "# Historical review projection",
            "frontmatter": {
                "status": "review",
                "recipient": "agent",
                "proposing_agent": "agents/tammy",
                "rationale": "This projection is not canonical proposed work.",
                "proposed_next_step": "Keep it out of the actionable Inbox.",
                "due_day": "2026-08-03",
                "submitted_at": "2026-08-03T09:00:00-07:00",
                "updated_at": "2026-08-03T09:00:00-07:00",
            },
        }
        edges = [
            {"from_slug": slug, "to_slug": PROPOSALS_ROOT, "link_type": "member_of"},
            {"from_slug": slug, "to_slug": "agents/tammy", "link_type": "proposed_by"},
        ]
        runner = FakeRunner(
            {
                "get_backlinks": [[edges[0]]],
                "get_page": [page],
                "get_links": [edges],
            }
        )

        result = GBrainAdapter(runner).list_proposals()

        self.assertEqual(result.proposals, ())

    def test_reads_only_typed_proposals_and_keeps_malformed_items_visible(self) -> None:
        slug = "proposals/toddy-wellbeing-check-in"
        page = {
            "slug": slug,
            "type": "task_proposal",
            "title": "Schedule a wellbeing check-in",
            "compiled_truth": "# Schedule a wellbeing check-in",
            "frontmatter": {
                "status": "proposed",
                "recipient": "tony",
                "proposing_agent": "agents/toddy",
                "rationale": "This supports the wellbeing goal.",
                "proposed_next_step": "Choose a time tomorrow.",
                "due_day": "2026-07-31",
                "submitted_at": "2026-07-30T14:00:00-07:00",
                "updated_at": "2026-07-30T14:00:00-07:00",
                "links": [
                    {"to": PROPOSALS_ROOT, "type": "member_of"},
                    {"to": "agents/toddy", "type": "proposed_by"},
                    {
                        "to": "goals/happier-and-healthier",
                        "type": "serves_goal",
                    },
                ],
            },
        }
        edges = [
            {
                "from_slug": slug,
                "to_slug": PROPOSALS_ROOT,
                "link_type": "member_of",
            },
            {
                "from_slug": slug,
                "to_slug": "agents/toddy",
                "link_type": "proposed_by",
            },
            {
                "from_slug": slug,
                "to_slug": "goals/happier-and-healthier",
                "link_type": "serves_goal",
            },
        ]
        runner = FakeRunner(
            {
                "get_backlinks": [
                    [
                        edges[0],
                        {
                            "from_slug": "proposals/legacy-untyped",
                            "to_slug": PROPOSALS_ROOT,
                            "link_type": "",
                        },
                    ]
                ],
                "get_page": [page],
                "get_links": [edges],
            }
        )

        result = GBrainAdapter(runner).list_proposals()

        self.assertEqual([item.slug for item in result.proposals], [slug])
        self.assertEqual(result.issues, ())


class TaskStatusMutationTests(unittest.TestCase):
    def test_rejects_legacy_waiting_as_a_new_status_update(self) -> None:
        runner = FakeRunner({})

        with self.assertRaisesRegex(ValueError, "status must be one of"):
            GBrainAdapter(runner).set_task_status(
                "tasks/legacy-waiting",
                "waiting",
                datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc),
            )

        self.assertEqual(runner.calls, [])


class ProposalDecisionTimelineTests(unittest.TestCase):
    def test_task_proposal_decision_is_one_atomic_idempotent_page_write(self) -> None:
        now = datetime(2026, 8, 1, 10, tzinfo=timezone(timedelta(hours=-7)))
        task = replace(
            new_task(title="Review the launch", now=now, identity="launch01"),
            status="proposed",
            lifecycle_root="collections/toddys-tasks",
            owner_agent="agents/toddy",
            proposal_recipient="agent",
            proposal_submitted_at=now - timedelta(hours=1),
        )
        page = stored_page(task)
        page["frontmatter"].update(
            {
                "status": "proposed",
                "proposal_recipient": "agent",
                "proposal_submitted_at": (now - timedelta(hours=1)).isoformat(),
                "proposal_decision_note": "",
                "created_at": task.created_at.isoformat(),
                "updated_at": task.updated_at.isoformat(),
            }
        )
        page["frontmatter"]["links"] = [
            {"to": "collections/toddys-tasks", "type": "member_of"},
            {"to": "agents/toddy", "type": "assigned_to"},
        ]
        links = [
            {
                "from_slug": task.slug,
                "to_slug": "collections/toddys-tasks",
                "link_type": "member_of",
            },
            {
                "from_slug": task.slug,
                "to_slug": "agents/toddy",
                "link_type": "assigned_to",
            },
        ]
        proposal = TaskProposal(
            slug=task.slug,
            title=task.title,
            status="proposed",
            recipient="agent",
            proposing_agent="agents/toddy",
            rationale=task.detail or "Review the bounded launch.",
            proposed_next_step=task.next_action or "Review it.",
            due_day=task.due_day,
            submitted_at=now - timedelta(hours=1),
            updated_at=now - timedelta(hours=1),
            source_kind="task",
        )
        runner = StatefulTaskRunner(page, links)

        class Adapter(GBrainAdapter):
            def list_proposals(self):
                current = Task.from_page(runner.page, edges=runner.links)
                return ProposalRead(
                    (
                        replace(
                            proposal,
                            status=(
                                "approved"
                                if current.proposal_decision == "approve"
                                else "rejected"
                                if current.proposal_decision == "reject"
                                else "proposed"
                            ),
                            decision=current.proposal_decision,
                            decision_at=current.proposal_decided_at,
                            resulting_status=(
                                current.status if current.proposal_decision else None
                            ),
                            decision_events=current.proposal_decision_events,
                        ),
                    )
                )

            def get_task(self, _slug):
                return Task.from_page(runner.page, edges=runner.links)

        adapter = Adapter(runner)

        receipt = adapter.decide_proposal(
            task.slug,
            action="approve",
            decision_note="Proceed.",
            now=now,
        )

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.status, "approved")
        self.assertEqual(receipt.proposal.resulting_status, "planned")
        self.assertEqual(
            [tool for tool, _params in runner.calls].count("put_page"),
            1,
        )
        events = runner.page["frontmatter"]["proposal_decision_events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["decision"], "approve")

        second = adapter.decide_proposal(
            task.slug,
            action="approve",
            decision_note="Proceed.",
            now=now + timedelta(minutes=1),
        )
        self.assertTrue(second.verified)
        self.assertEqual(second.status, "approved")
        self.assertEqual(
            [tool for tool, _params in runner.calls].count("put_page"),
            1,
        )


    def test_agent_status_update_preserves_owner_scope_and_task_identity(
        self,
    ) -> None:
        now = datetime(
            2026,
            7,
            30,
            9,
            15,
            tzinfo=timezone(timedelta(hours=-7)),
        )
        base = new_task(
            title="Prepare a wellbeing update",
            now=now,
            identity="agent34",
        )
        task = replace(
            base,
            lifecycle_root="collections/toddys-tasks",
            owner_agent="agents/toddy",
        )
        page = stored_page(task)
        page["frontmatter"].update(
            {
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "links": [
                    {
                        "to": "collections/toddys-tasks",
                        "type": "member_of",
                    },
                    {"to": "agents/toddy", "type": "assigned_to"},
                ],
            }
        )
        links = [
            {
                "from_slug": task.slug,
                "to_slug": "collections/toddys-tasks",
                "link_type": "member_of",
            },
            {
                "from_slug": task.slug,
                "to_slug": "agents/toddy",
                "link_type": "assigned_to",
            },
        ]
        runner = StatefulTaskRunner(page, links)

        receipt = GBrainAdapter(runner).set_task_status(
            task.slug,
            "active",
            now + timedelta(minutes=5),
        )

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.status, "active")
        self.assertEqual(receipt.task.owner_agent, "agents/toddy")
        self.assertEqual(
            receipt.task.lifecycle_root,
            "collections/toddys-tasks",
        )
        self.assertEqual(runner.page["type"], "task")
        self.assertEqual(runner.links, links)

    def test_agent_status_update_keeps_notes_in_reopened_task_readback(
        self,
    ) -> None:
        now = datetime(
            2026,
            7,
            31,
            9,
            15,
            tzinfo=timezone(timedelta(hours=-7)),
        )
        notes = "## Tony's notes\n\nKeep the confirmed handoff context."
        task = replace(
            new_task(title="Prepare agent handoff", detail=notes, now=now, identity="agent-notes"),
            status="blocked",
            lifecycle_root="collections/tammys-tasks",
            owner_agent="agents/tammy",
        )
        page = stored_page(task)
        page["frontmatter"].update(
            {
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "links": [
                    {"to": "collections/tammys-tasks", "type": "member_of"},
                    {"to": "agents/tammy", "type": "assigned_to"},
                ],
            }
        )
        links = [
            {
                "from_slug": task.slug,
                "to_slug": "collections/tammys-tasks",
                "link_type": "member_of",
            },
            {
                "from_slug": task.slug,
                "to_slug": "agents/tammy",
                "link_type": "assigned_to",
            },
        ]
        runner = StatefulTaskRunner(page, links)
        adapter = GBrainAdapter(runner)

        receipt = adapter.set_task_status(task.slug, "active", now + timedelta(minutes=3))
        reopened = adapter.get_task(task.slug)

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.task.detail, notes)
        self.assertEqual(reopened.status, "active")
        self.assertEqual(reopened.detail, notes)
        self.assertEqual(reopened.owner_agent, "agents/tammy")
        self.assertEqual(reopened.lifecycle_root, "collections/tammys-tasks")
        self.assertEqual(runner.page["frontmatter"]["detail"], notes)

    def test_completion_sets_local_timestamp_and_keeps_active_membership(self) -> None:
        now = datetime(2026, 7, 30, 9, 15, tzinfo=timezone(timedelta(hours=-7)))
        task = new_inbox_task("Finish GTasks", now, "a1b2c3")
        initial_page = stored_page(task)
        initial_page["frontmatter"]["captured_via"] = "capture-cli"
        active_edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        final_page = deepcopy(initial_page)
        final_page["frontmatter"]["status"] = "completed"
        final_page["frontmatter"]["completed_at"] = now.isoformat()
        final_page["frontmatter"]["updated_at"] = now.isoformat()
        runner = FakeRunner(
            {
                "get_page": [initial_page, final_page, final_page, final_page, final_page],
                "get_links": [
                    [active_edge],
                    [active_edge],
                    [active_edge],
                    [active_edge],
                    [active_edge],
                    [active_edge],
                    [active_edge],
                ],
                "get_backlinks": [[], []],
                "put_page": [{"slug": task.slug}],
            }
        )

        receipt = GBrainAdapter(runner).set_task_status(task.slug, "completed", now)

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.status, "completed")
        self.assertEqual(receipt.lifecycle_root, ACTIVE_ROOT)
        self.assertEqual(receipt.completed_at, now)
        self.assertEqual(receipt.task.status, "completed")
        written = runner.calls[2][1]["content"]
        self.assertIn('type: "task"', written)
        self.assertIn('"type": "member_of"', written)
        self.assertIn('captured_via: "capture-cli"', written)
        self.assertIn("# Finish GTasks", written)
        self.assertNotIn("add_link", [tool for tool, _ in runner.calls])
        self.assertNotIn("remove_link", [tool for tool, _ in runner.calls])

    def test_status_edit_refuses_unexpected_non_task_type_before_write(self) -> None:
        task = new_inbox_task(
            "Misclassified task",
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            "a1b2c3",
        )
        page = stored_page(task)
        page["type"] = "concept"
        runner = FakeRunner(
            {
                "get_page": [page],
                "get_links": [
                    [
                        {
                            "from_slug": task.slug,
                            "to_slug": ACTIVE_ROOT,
                            "link_type": "member_of",
                        }
                    ]
                ],
            }
        )

        with self.assertRaisesRegex(ValueError, "unexpected page type concept"):
            GBrainAdapter(runner).set_task_status(
                task.slug,
                "active",
                datetime(2026, 7, 30, 12, tzinfo=timezone.utc),
            )

        self.assertNotIn("put_page", [tool for tool, _params in runner.calls])

    def test_status_edit_reconstructs_missing_frontmatter_membership(self) -> None:
        now = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
        task = new_inbox_task(
            "Legacy graph-only membership",
            now,
            "a1b2c3",
        )
        before = stored_page(task)
        before["frontmatter"].pop("links")
        after_task = replace(task, status="active")
        after = stored_page(after_task)
        after["frontmatter"]["updated_at"] = now.isoformat()
        edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "get_page": [before, after],
                "get_links": [[edge], [edge]],
                "put_page": [{"slug": task.slug}],
            }
        )

        receipt = GBrainAdapter(runner).set_task_status(
            task.slug,
            "active",
            now,
        )

        self.assertTrue(receipt.verified)
        content = next(
            params["content"]
            for tool, params in runner.calls
            if tool == "put_page"
        )
        self.assertIn('type: "task"', content)
        self.assertIn('"type": "member_of"', content)

    def test_reopening_an_archived_task_restores_active_membership(self) -> None:
        now = datetime(2026, 7, 30, 9, 15, tzinfo=timezone(timedelta(hours=-7)))
        completed_at = datetime(2026, 7, 28, 17, 0, tzinfo=timezone(timedelta(hours=-7)))
        task = replace(
            new_inbox_task("Reopen GTasks", now, "a1b2c3"),
            status="completed",
            lifecycle_root=COMPLETED_ROOT,
            completed_at=completed_at,
        )
        initial_page = stored_page(task)
        initial_page["frontmatter"]["links"] = [
            {"to": COMPLETED_ROOT, "type": "member_of"}
        ]
        initial_page["frontmatter"]["completed_at"] = completed_at.isoformat()
        archived_edge = {
            "from_slug": task.slug,
            "to_slug": COMPLETED_ROOT,
            "link_type": "member_of",
        }
        active_edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        final_page = deepcopy(initial_page)
        final_page["frontmatter"]["status"] = "active"
        final_page["frontmatter"]["completed_at"] = None
        final_page["frontmatter"]["updated_at"] = now.isoformat()
        final_page["frontmatter"]["links"] = [
            {"to": ACTIVE_ROOT, "type": "member_of"}
        ]
        runner = FakeRunner(
            {
                "get_page": [initial_page, final_page],
                "get_links": [[archived_edge], [active_edge]],
                "put_page": [{"slug": task.slug}],
                "add_link": [active_edge],
                "remove_link": [{}],
            }
        )

        receipt = GBrainAdapter(runner).set_task_status(task.slug, "active", now)

        self.assertEqual(receipt.lifecycle_root, ACTIVE_ROOT)
        self.assertIsNone(receipt.completed_at)
        self.assertIn(("add_link", {
            "from": task.slug,
            "to": ACTIVE_ROOT,
            "link_type": "member_of",
            "context": "GTasks active task membership.",
            "link_source": "gtasks",
        }), runner.calls)
        self.assertIn(("remove_link", {
            "from": task.slug,
            "to": COMPLETED_ROOT,
            "link_type": "member_of",
        }), runner.calls)

    def test_status_write_requires_matching_page_and_link_readback(self) -> None:
        now = datetime(2026, 7, 30, 9, 15, tzinfo=timezone.utc)
        task = new_inbox_task("Finish GTasks", now, "a1b2c3")
        page = stored_page(task)
        active_edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "get_page": [page, page],
                "get_links": [[active_edge], [active_edge]],
                "put_page": [{"slug": task.slug}],
            }
        )

        with self.assertRaises(PartialMutationError) as raised:
            GBrainAdapter(runner).set_task_status(task.slug, "blocked", now)

        self.assertEqual(raised.exception.slug, task.slug)
        self.assertIn("readback", str(raised.exception).lower())


class TodoAdapterTests(unittest.TestCase):
    def _fixture(
        self,
        *,
        next_action: str = "",
        history: list[dict] | None = None,
        agent_slug: str | None = None,
    ) -> tuple[StatefulIdentityMigrationRunner, Task]:
        now = datetime.fromisoformat("2026-08-01T09:00:00-07:00")
        task = new_task(
            title="Ship the release",
            detail="Preserve every unrelated relationship.",
            priority="high",
            next_action=next_action,
            due_day=date(2026, 8, 2),
            project=None,
            goal=None,
            now=now,
            identity="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        )
        page = stored_page(task)
        page["frontmatter"]["next_action_history"] = history or []
        page["frontmatter"]["updated_at"] = now.isoformat()
        links = [
            {
                "from_slug": task.slug,
                "to_slug": ACTIVE_ROOT,
                "link_type": "member_of",
                "context": "Tony task",
                "link_source": "gtasks",
            },
            {
                "from_slug": task.slug,
                "to_slug": "goals/11111111-1111-4111-8111-111111111111",
                "link_type": "advances_goal",
                "context": "Goal",
                "link_source": "gtasks",
            },
        ]
        if agent_slug:
            root = {
                "agents/toddy": "collections/toddys-tasks",
                "agents/timmy": "collections/timmys-tasks",
                "agents/tammy": "collections/tammys-tasks",
            }[agent_slug]
            page["frontmatter"]["links"] = [
                {"to": root, "type": "member_of"},
                {"to": agent_slug, "type": "assigned_to"},
            ]
            links[0]["to_slug"] = root
            links.append(
                {
                    "from_slug": task.slug,
                    "to_slug": agent_slug,
                    "link_type": "assigned_to",
                    "context": "Agent owner",
                    "link_source": "gtasks",
                }
            )
            task = Task.from_page(page, edges=links)
        return StatefulIdentityMigrationRunner({task.slug: page}, links), task

    def test_task_api_payload_includes_active_handoff_question_todo(self) -> None:
        runner, task = self._fixture(agent_slug="agents/toddy")
        adapter = GBrainAdapter(runner)
        receipt = adapter.request_agent_input(
            task.slug,
            question="Which family-care scope should Toddy use next?",
            question_detail="Choose the scope before the Agent continues.",
            resume_action="Resume with Tony's selected scope.",
            agent_slug="agents/toddy",
            idempotency_key="family-scope-question",
            now=datetime.fromisoformat("2026-08-01T10:00:00-07:00"),
        )

        payload = adapter.get_task_api_payload(task.slug)

        self.assertEqual(payload["handoff"]["state"], "waiting_for_input")
        self.assertEqual(payload["handoff"]["question_todo"], receipt.todo.slug)
        self.assertEqual([todo["slug"] for todo in payload["todos"]], [receipt.todo.slug])
        self.assertEqual(payload["todos"][0]["text"], receipt.todo.text)
        self.assertEqual(payload["todos"][0]["kind"], "question")

    def test_creates_multiple_stable_todos_and_projects_first_open_text(self) -> None:
        self.assertTrue(hasattr(GBrainAdapter, "create_todo"))
        runner, task = self._fixture()
        adapter = GBrainAdapter(runner)
        first = adapter.create_todo(
            task.slug,
            text="Draft the release notes",
            detail="Cover migration and rollback.",
            kind="action",
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="create-first",
            now=datetime.fromisoformat("2026-08-01T10:00:00-07:00"),
        )
        second = adapter.create_todo(
            task.slug,
            text="Verify the dashboard restart",
            detail="Read back the live version.",
            kind="action",
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="create-second",
            now=datetime.fromisoformat("2026-08-01T10:01:00-07:00"),
        )

        self.assertNotEqual(first.todo.slug, second.todo.slug)
        self.assertTrue(first.todo.slug.startswith("todos/"))
        self.assertEqual(first.todo.status, "not_done")
        self.assertEqual(runner.pages[first.todo.slug]["type"], "todo")
        self.assertTrue(
            any(
                edge["from_slug"] == first.todo.slug
                and edge["to_slug"] == task.slug
                and edge["link_type"] == "todo_for"
                for edge in runner.links
            )
        )
        self.assertEqual(
            runner.pages[task.slug]["frontmatter"]["next_action"],
            "Draft the release notes",
        )
        stored = adapter.list_task_todos(task.slug, limit=100)
        self.assertEqual([todo.text for todo in stored.todos], [
            "Draft the release notes",
            "Verify the dashboard restart",
        ])

    def test_create_retry_is_idempotent_and_does_not_duplicate_event(self) -> None:
        self.assertTrue(hasattr(GBrainAdapter, "create_todo"))
        runner, task = self._fixture()
        adapter = GBrainAdapter(runner)
        request = dict(
            text="Draft the release notes",
            detail="",
            kind="action",
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="same-request",
            now=datetime.fromisoformat("2026-08-01T10:00:00-07:00"),
        )

        first = adapter.create_todo(task.slug, **request)
        second = adapter.create_todo(task.slug, **request)

        self.assertEqual(first.todo.slug, second.todo.slug)
        self.assertTrue(second.idempotent)
        event_pages = [
            slug for slug, page in runner.pages.items()
            if page.get("type") == "todo_event" and not page.get("deleted_at")
        ]
        self.assertEqual(len(event_pages), 1)

    def test_list_is_deterministic_filterable_bounded_and_restart_persistent(self) -> None:
        self.assertTrue(hasattr(GBrainAdapter, "list_task_todos"))
        runner, task = self._fixture()
        adapter = GBrainAdapter(runner)
        created = []
        for index, text in enumerate(("First", "Second", "Third")):
            created.append(adapter.create_todo(
                task.slug, text=text, detail="", kind="action",
                actor="people/tony-guan", source="mission_control",
                idempotency_key=f"todo-{index}",
                now=datetime.fromisoformat(f"2026-08-01T10:0{index}:00-07:00"),
            ).todo)
        adapter.set_todo_status(
            created[1].slug, status="done", expected_updated_at=created[1].updated_at,
            actor="people/tony-guan", source="mission_control", idempotency_key="done-second",
            now=datetime.fromisoformat("2026-08-01T10:05:00-07:00"),
        )

        restarted = GBrainAdapter(runner)
        first_page = restarted.list_task_todos(task.slug, cursor=0, limit=2)
        second_page = restarted.list_task_todos(task.slug, cursor=2, limit=2)
        done = restarted.list_task_todos(task.slug, status="done", limit=100)

        self.assertEqual([todo.text for todo in first_page.todos], ["First", "Third"])
        self.assertEqual(first_page.next_cursor, 2)
        self.assertEqual([todo.text for todo in second_page.todos], ["Second"])
        self.assertIsNone(second_page.next_cursor)
        self.assertEqual([todo.text for todo in done.todos], ["Second"])

    def test_comments_status_and_edit_history_are_per_item_append_only(self) -> None:
        self.assertTrue(hasattr(GBrainAdapter, "add_todo_comment"))
        runner, task = self._fixture()
        adapter = GBrainAdapter(runner)
        first = adapter.create_todo(
            task.slug, text="Confirm window", detail="Ask Tony.", kind="question",
            actor="people/tony-guan", source="mission_control",
            idempotency_key="todo-one",
            now=datetime.fromisoformat("2026-08-01T10:00:00-07:00"),
        ).todo
        second = adapter.create_todo(
            task.slug, text="Publish notes", detail="", kind="action",
            actor="people/tony-guan", source="mission_control",
            idempotency_key="todo-two",
            now=datetime.fromisoformat("2026-08-01T10:01:00-07:00"),
        ).todo
        edited = adapter.edit_todo(
            first.slug,
            text="Confirm the 17:00 deployment window",
            detail="Tony must answer before deploy.",
            expected_updated_at=first.updated_at,
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="edit-one",
            now=datetime.fromisoformat("2026-08-01T10:02:00-07:00"),
        ).todo
        reply = adapter.add_todo_comment(
            first.slug,
            body="17:00 works. Proceed.",
            expected_updated_at=edited.updated_at,
            author="people/tony-guan",
            source="mission_control",
            idempotency_key="reply-one",
            now=datetime.fromisoformat("2026-08-01T10:03:00-07:00"),
        ).todo
        done = adapter.set_todo_status(
            first.slug,
            status="done",
            expected_updated_at=reply.updated_at,
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="done-one",
            now=datetime.fromisoformat("2026-08-01T10:04:00-07:00"),
        ).todo

        self.assertEqual(done.status, "done")
        self.assertEqual([comment.body for comment in done.comments], ["17:00 works. Proceed."])
        self.assertEqual(
            [event.event_type for event in done.events],
            ["created", "edited", "comment_added", "status_changed"],
        )
        untouched = next(
            item
            for item in adapter.list_task_todos(task.slug, limit=100).todos
            if item.slug == second.slug
        )
        self.assertEqual(untouched.slug, second.slug)
        self.assertEqual(untouched.comments, ())
        self.assertEqual(untouched.status, "not_done")
        self.assertEqual(runner.pages[task.slug]["frontmatter"]["next_action"], "Publish notes")
        self.assertEqual(
            runner.pages[task.slug]["frontmatter"]["next_action_history"],
            [{"action": "Confirm the 17:00 deployment window", "completed_at": "2026-08-01T10:04:00-07:00"}],
        )

    def test_completing_parent_task_reconciles_open_child_todo(self) -> None:
        runner, task = self._fixture()
        adapter = GBrainAdapter(runner)
        todo = adapter.create_todo(
            task.slug,
            text="Publish the release notes",
            detail="This open TODO must not contradict a completed parent.",
            kind="action",
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="todo-open",
            now=datetime.fromisoformat("2026-08-01T10:00:00-07:00"),
        ).todo

        receipt = adapter.set_task_status(
            task.slug,
            "completed",
            datetime.fromisoformat("2026-08-01T10:05:00-07:00"),
        )
        stored = next(
            item
            for item in adapter.list_task_todos(task.slug, limit=100).todos
            if item.slug == todo.slug
        )

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.task.status, "completed")
        self.assertEqual(stored.slug, todo.slug)
        self.assertEqual(stored.status, "done")
        self.assertEqual(
            [event.event_type for event in stored.events],
            ["created", "status_changed"],
        )
        self.assertEqual(stored.events[-1].actor, "people/tony-guan")
        self.assertEqual(stored.events[-1].source, "mission_control")
        self.assertEqual(stored.events[-1].before, {"status": "not_done"})
        self.assertEqual(stored.events[-1].after, {"status": "done"})

    def test_completing_parent_task_reconciles_multiple_todos_without_reopening_done_ones(self) -> None:
        runner, task = self._fixture()
        adapter = GBrainAdapter(runner)
        first = adapter.create_todo(
            task.slug,
            text="First open item",
            detail="",
            kind="action",
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="todo-first",
            now=datetime.fromisoformat("2026-08-01T10:00:00-07:00"),
        ).todo
        second = adapter.create_todo(
            task.slug,
            text="Second open item",
            detail="",
            kind="action",
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="todo-second",
            now=datetime.fromisoformat("2026-08-01T10:01:00-07:00"),
        ).todo
        already_done = adapter.set_todo_status(
            second.slug,
            status="done",
            expected_updated_at=second.updated_at,
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="done-second-before-parent",
            now=datetime.fromisoformat("2026-08-01T10:02:00-07:00"),
        ).todo

        adapter.set_task_status(
            task.slug,
            "completed",
            datetime.fromisoformat("2026-08-01T10:05:00-07:00"),
        )
        todos = {
            todo.slug: todo
            for todo in adapter.list_task_todos(task.slug, limit=100).todos
        }

        self.assertEqual(todos[first.slug].status, "done")
        self.assertEqual(todos[second.slug].status, "done")
        self.assertEqual(
            [event.event_type for event in todos[first.slug].events],
            ["created", "status_changed"],
        )
        self.assertEqual(
            [event.event_type for event in todos[second.slug].events],
            [event.event_type for event in already_done.events],
        )

    def test_completed_parent_status_retry_does_not_duplicate_todo_events(self) -> None:
        runner, task = self._fixture()
        adapter = GBrainAdapter(runner)
        todo = adapter.create_todo(
            task.slug,
            text="Reconcile exactly once",
            detail="",
            kind="action",
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="todo-open",
            now=datetime.fromisoformat("2026-08-01T10:00:00-07:00"),
        ).todo

        adapter.set_task_status(
            task.slug,
            "completed",
            datetime.fromisoformat("2026-08-01T10:05:00-07:00"),
        )
        adapter.set_task_status(
            task.slug,
            "completed",
            datetime.fromisoformat("2026-08-01T10:06:00-07:00"),
        )
        stored = next(
            item
            for item in adapter.list_task_todos(task.slug, limit=100).todos
            if item.slug == todo.slug
        )

        self.assertEqual(stored.slug, todo.slug)
        self.assertEqual(stored.status, "done")
        self.assertEqual(
            [event.event_type for event in stored.events],
            ["created", "status_changed"],
        )

    def test_same_status_completed_parent_repairs_historical_open_todo(self) -> None:
        runner, task = self._fixture()
        adapter = GBrainAdapter(runner)
        todo = adapter.create_todo(
            task.slug,
            text="Historical leftover item",
            detail="",
            kind="action",
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="todo-open",
            now=datetime.fromisoformat("2026-08-01T10:00:00-07:00"),
        ).todo
        completed_at = datetime.fromisoformat("2026-08-01T10:05:00-07:00")
        page = deepcopy(runner.pages[task.slug])
        page["frontmatter"]["status"] = "completed"
        page["frontmatter"]["completed_at"] = completed_at.isoformat()
        page["frontmatter"]["updated_at"] = completed_at.isoformat()
        runner.pages[task.slug] = page

        receipt = adapter.set_task_status(
            task.slug,
            "completed",
            datetime.fromisoformat("2026-08-01T10:06:00-07:00"),
        )
        stored = adapter.list_task_todos(task.slug, limit=100).todos[0]

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.task.status, "completed")
        self.assertEqual(stored.slug, todo.slug)
        self.assertEqual(stored.status, "done")

    def test_legacy_next_action_todo_is_reconciled_when_parent_completes(self) -> None:
        runner, task = self._fixture(
            next_action="Publish the legacy next action",
            history=[
                {
                    "action": "Collect earlier evidence",
                    "completed_at": "2026-08-01T09:30:00-07:00",
                }
            ],
        )
        adapter = GBrainAdapter(runner)
        adapter.migrate_legacy_next_actions(
            task.slug,
            now=datetime.fromisoformat("2026-08-01T10:00:00-07:00"),
        )

        adapter.set_task_status(
            task.slug,
            "completed",
            datetime.fromisoformat("2026-08-01T10:05:00-07:00"),
        )
        todos = adapter.list_task_todos(task.slug, limit=100).todos

        self.assertEqual({todo.status for todo in todos}, {"done"})
        current = next(
            todo for todo in todos if todo.text == "Publish the legacy next action"
        )
        self.assertEqual(
            [event.event_type for event in current.events],
            ["legacy_migrated", "status_changed"],
        )

    def test_full_edit_completed_parent_repairs_historical_open_todo(self) -> None:
        runner, task = self._fixture()
        adapter = GBrainAdapter(runner)
        todo = adapter.create_todo(
            task.slug,
            text="Historical edit-path leftover",
            detail="",
            kind="action",
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="todo-open",
            now=datetime.fromisoformat("2026-08-01T10:00:00-07:00"),
        ).todo
        completed_at = datetime.fromisoformat("2026-08-01T10:05:00-07:00")
        page = deepcopy(runner.pages[task.slug])
        page["frontmatter"]["status"] = "completed"
        page["frontmatter"]["completed_at"] = completed_at.isoformat()
        page["frontmatter"]["updated_at"] = completed_at.isoformat()
        runner.pages[task.slug] = page

        receipt = adapter.edit_task(
            task.slug,
            title="Ship the edited release",
            detail=task.detail,
            priority=task.priority,
            due_day=task.due_day,
            next_action=task.next_action,
            project_slug=task.project,
            goal_slug="goals/11111111-1111-4111-8111-111111111111",
            status="completed",
            assignee_slug="tony",
            progress_metric=task.progress_metric,
            event_progress=task.event_progress,
            handoff_reason="",
            now=datetime.fromisoformat("2026-08-01T10:06:00-07:00"),
        )
        stored = adapter.list_task_todos(task.slug, limit=100).todos[0]

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.task.status, "completed")
        self.assertEqual(stored.slug, todo.slug)
        self.assertEqual(stored.status, "done")
        self.assertEqual(
            [event.event_type for event in stored.events],
            ["created", "status_changed"],
        )

    def test_automation_created_daily_task_completion_reconciles_child_todo(self) -> None:
        now = datetime.fromisoformat("2026-08-12T08:00:00-07:00")
        task = new_task(
            title="Daily automation-created review",
            detail="Created by the recurring Mission Control builder.",
            priority="normal",
            next_action="Review the daily queue",
            due_day=now.date(),
            project=None,
            goal=None,
            now=now,
            identity="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        )
        page = stored_page(task)
        page["frontmatter"]["source"] = "mission_control_nightly_builder"
        runner = StatefulIdentityMigrationRunner(
            {task.slug: page},
            [
                {
                    "from_slug": task.slug,
                    "to_slug": ACTIVE_ROOT,
                    "link_type": "member_of",
                    "context": "Automation-created Tony task",
                    "link_source": "gtasks",
                }
            ],
        )
        adapter = GBrainAdapter(runner)
        todo = adapter.create_todo(
            task.slug,
            text="Finish the generated daily action",
            detail="",
            kind="action",
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="automation-todo",
            now=datetime.fromisoformat("2026-08-12T08:01:00-07:00"),
        ).todo

        adapter.set_task_status(
            task.slug,
            "completed",
            datetime.fromisoformat("2026-08-12T08:10:00-07:00"),
        )
        stored = next(
            item
            for item in adapter.list_task_todos(task.slug, limit=100).todos
            if item.slug == todo.slug
        )

        self.assertEqual(stored.slug, todo.slug)
        self.assertEqual(stored.status, "done")
        self.assertEqual(stored.events[-1].source, "mission_control")

    def test_comment_mutation_reuses_verified_immutable_history(self) -> None:
        runner, task = self._fixture()
        adapter = GBrainAdapter(runner)
        todo = adapter.create_todo(
            task.slug,
            text="Confirm window",
            detail="Ask Tony.",
            kind="question",
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="todo-one",
            now=datetime.fromisoformat("2026-08-01T10:00:00-07:00"),
        ).todo
        edited = adapter.edit_todo(
            todo.slug,
            text="Confirm the 17:00 deployment window",
            detail="Tony must answer before deploy.",
            expected_updated_at=todo.updated_at,
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="edit-one",
            now=datetime.fromisoformat("2026-08-01T10:01:00-07:00"),
        ).todo
        first_reply = adapter.add_todo_comment(
            todo.slug,
            body="17:00 works.",
            expected_updated_at=edited.updated_at,
            author="people/tony-guan",
            source="mission_control",
            idempotency_key="reply-one",
            now=datetime.fromisoformat("2026-08-01T10:02:00-07:00"),
        ).todo

        warmed = adapter.list_task_todos(task.slug, limit=100).todos[0]
        immutable_history = set((*warmed.comment_slugs, *warmed.event_slugs))
        runner.calls.clear()
        adapter.add_todo_comment(
            todo.slug,
            body="Proceed with the verified window.",
            expected_updated_at=first_reply.updated_at,
            author="people/tony-guan",
            source="mission_control",
            idempotency_key="reply-two",
            now=datetime.fromisoformat("2026-08-01T10:03:00-07:00"),
        )

        reread_immutable_history = [
            (tool, params["slug"])
            for tool, params in runner.calls
            if tool in {"get_page", "get_links"}
            and params.get("slug") in immutable_history
        ]
        self.assertEqual(reread_immutable_history, [])
        self.assertEqual(
            sum(
                tool == "get_page" and params.get("slug") == todo.slug
                for tool, params in runner.calls
            ),
            2,
        )
        self.assertEqual(
            sum(
                tool == "get_page" and params.get("slug") == task.slug
                for tool, params in runner.calls
            ),
            1,
        )
        self.assertEqual(
            sum(
                tool == "get_links" and params.get("slug") == task.slug
                for tool, params in runner.calls
            ),
            1,
        )

    def test_cold_todo_hydration_respects_canonical_cli_lane(self) -> None:
        runner, task = self._fixture()
        adapter = GBrainAdapter(runner)
        todo = adapter.create_todo(
            task.slug,
            text="Confirm window",
            detail="Ask Tony.",
            kind="question",
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="todo-one",
            now=datetime.fromisoformat("2026-08-01T10:00:00-07:00"),
        ).todo
        edited = adapter.edit_todo(
            todo.slug,
            text="Confirm 17:00",
            detail="Ask Tony.",
            expected_updated_at=todo.updated_at,
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="edit-one",
            now=datetime.fromisoformat("2026-08-01T10:01:00-07:00"),
        ).todo
        adapter.add_todo_comment(
            todo.slug,
            body="17:00 works.",
            expected_updated_at=edited.updated_at,
            author="people/tony-guan",
            source="mission_control",
            idempotency_key="reply-one",
            now=datetime.fromisoformat("2026-08-01T10:02:00-07:00"),
        )

        class DelayedSubprocessFixtureRunner(
            StatefulIdentityMigrationRunner,
            SubprocessCommandRunner,
        ):
            def __init__(self, pages: dict[str, dict], links: list[dict]) -> None:
                StatefulIdentityMigrationRunner.__init__(self, pages, links)
                self._activity_lock = threading.Lock()
                self.active = 0
                self.max_active = 0

            def run(self, tool: str, params: dict) -> object:
                with self._activity_lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                try:
                    time.sleep(0.005)
                    return StatefulIdentityMigrationRunner.run(self, tool, params)
                finally:
                    with self._activity_lock:
                        self.active -= 1

        cold_runner = DelayedSubprocessFixtureRunner(runner.pages, runner.links)
        cold = GBrainAdapter(cold_runner)
        readback = cold.list_task_todos(task.slug, limit=100)

        self.assertEqual(len(readback.todos[0].comments), 1)
        self.assertEqual(cold_runner.max_active, 1)
        immutable_history = set(
            (*readback.todos[0].comment_slugs, *readback.todos[0].event_slugs)
        )
        self.assertEqual(
            [
                params["slug"]
                for tool, params in cold_runner.calls
                if tool == "get_links" and params.get("slug") in immutable_history
            ],
            [],
        )
        self.assertEqual(
            sum(
                tool == "get_backlinks" and params.get("slug") == todo.slug
                for tool, params in cold_runner.calls
            ),
            1,
        )

    def test_task_enrichment_respects_canonical_cli_lane(self) -> None:
        runner, task = self._fixture()
        second = new_task(
            title="Verify the deployment",
            detail="",
            priority="normal",
            next_action="",
            due_day=date(2026, 8, 2),
            project=None,
            goal=None,
            now=datetime.fromisoformat("2026-08-01T09:05:00-07:00"),
            identity="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        )
        runner.pages[second.slug] = stored_page(second)
        runner.links.append({
            "from_slug": second.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
            "context": "Tony task",
            "link_source": "gtasks",
        })

        class DelayedSubprocessFixtureRunner(
            StatefulIdentityMigrationRunner,
            SubprocessCommandRunner,
        ):
            def __init__(self, pages: dict[str, dict], links: list[dict]) -> None:
                StatefulIdentityMigrationRunner.__init__(self, pages, links)
                self._activity_lock = threading.Lock()
                self.active = 0
                self.max_active = 0

            def run(self, tool: str, params: dict) -> object:
                with self._activity_lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                try:
                    time.sleep(0.005)
                    return StatefulIdentityMigrationRunner.run(self, tool, params)
                finally:
                    with self._activity_lock:
                        self.active -= 1

        cold_runner = DelayedSubprocessFixtureRunner(runner.pages, runner.links)
        enriched, issues = GBrainAdapter(cold_runner).enrich_tasks_with_todos(
            (task, second)
        )

        self.assertEqual(len(enriched), 2)
        self.assertEqual(issues, ())
        self.assertEqual(cold_runner.max_active, 1)

    def test_task_todo_list_reads_multiple_items_through_canonical_cli_lane(self) -> None:
        runner, task = self._fixture()
        adapter = GBrainAdapter(runner)
        for index in range(3):
            adapter.create_todo(
                task.slug,
                text=f"Item {index}",
                detail="",
                kind="action",
                actor="people/tony-guan",
                source="mission_control",
                idempotency_key=f"todo-{index}",
                now=datetime.fromisoformat(f"2026-08-01T10:0{index}:00-07:00"),
            )

        class DelayedSubprocessFixtureRunner(
            StatefulIdentityMigrationRunner,
            SubprocessCommandRunner,
        ):
            def __init__(self, pages: dict[str, dict], links: list[dict]) -> None:
                StatefulIdentityMigrationRunner.__init__(self, pages, links)
                self._activity_lock = threading.Lock()
                self.active = 0
                self.max_active = 0

            def run(self, tool: str, params: dict) -> object:
                with self._activity_lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                try:
                    time.sleep(0.005)
                    return StatefulIdentityMigrationRunner.run(self, tool, params)
                finally:
                    with self._activity_lock:
                        self.active -= 1

        cold_runner = DelayedSubprocessFixtureRunner(runner.pages, runner.links)
        readback = GBrainAdapter(cold_runner).list_task_todos(task.slug, limit=100)

        self.assertEqual(len(readback.todos), 3)
        self.assertEqual(cold_runner.max_active, 1)

    def test_comment_and_audit_event_writes_respect_canonical_cli_lane(self) -> None:
        runner, task = self._fixture()
        seeded = GBrainAdapter(runner).create_todo(
            task.slug,
            text="Confirm window",
            detail="",
            kind="question",
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="todo-one",
            now=datetime.fromisoformat("2026-08-01T10:00:00-07:00"),
        ).todo

        class DelayedSubprocessFixtureRunner(
            StatefulIdentityMigrationRunner,
            SubprocessCommandRunner,
        ):
            def __init__(self, pages: dict[str, dict], links: list[dict]) -> None:
                StatefulIdentityMigrationRunner.__init__(self, pages, links)
                self._activity_lock = threading.Lock()
                self.active = 0
                self.max_active = 0

            def run(self, tool: str, params: dict) -> object:
                with self._activity_lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                try:
                    time.sleep(0.005)
                    return StatefulIdentityMigrationRunner.run(self, tool, params)
                finally:
                    with self._activity_lock:
                        self.active -= 1

        delayed_runner = DelayedSubprocessFixtureRunner(runner.pages, runner.links)
        adapter = GBrainAdapter(delayed_runner)
        warmed = adapter.list_task_todos(task.slug, limit=100).todos[0]
        delayed_runner.max_active = 0
        adapter.add_todo_comment(
            seeded.slug,
            body="Proceed.",
            expected_updated_at=warmed.updated_at,
            author="people/tony-guan",
            source="mission_control",
            idempotency_key="reply-one",
            now=datetime.fromisoformat("2026-08-01T10:01:00-07:00"),
        )

        self.assertEqual(delayed_runner.max_active, 1)

    def test_rejects_stale_concurrent_edit_without_lost_update(self) -> None:
        self.assertTrue(hasattr(GBrainAdapter, "edit_todo"))
        runner, task = self._fixture()
        adapter = GBrainAdapter(runner)
        todo = adapter.create_todo(
            task.slug, text="Confirm window", detail="", kind="action",
            actor="people/tony-guan", source="mission_control",
            idempotency_key="todo-one",
            now=datetime.fromisoformat("2026-08-01T10:00:00-07:00"),
        ).todo
        adapter.edit_todo(
            todo.slug, text="Confirm 17:00", detail="", expected_updated_at=todo.updated_at,
            actor="people/tony-guan", source="mission_control", idempotency_key="edit-a",
            now=datetime.fromisoformat("2026-08-01T10:01:00-07:00"),
        )

        with self.assertRaisesRegex(Exception, "changed since it was read"):
            adapter.edit_todo(
                todo.slug, text="Confirm 18:00", detail="", expected_updated_at=todo.updated_at,
                actor="people/tony-guan", source="mission_control", idempotency_key="edit-b",
                now=datetime.fromisoformat("2026-08-01T10:02:00-07:00"),
            )

    def test_failed_event_write_rolls_back_todo_and_projection(self) -> None:
        self.assertTrue(hasattr(GBrainAdapter, "edit_todo"))

        class FailingEventRunner(StatefulIdentityMigrationRunner):
            fail_event_write = False

            def run(self, tool: str, params: dict) -> object:
                if (
                    self.fail_event_write
                    and tool == "put_page"
                    and str(params.get("slug", "")).startswith("todo-events/")
                ):
                    raise GBrainCommandError("forced event write failure")
                return super().run(tool, params)

        base, task = self._fixture()
        runner = FailingEventRunner(base.pages, base.links)
        adapter = GBrainAdapter(runner)
        todo = adapter.create_todo(
            task.slug, text="Confirm window", detail="", kind="action",
            actor="people/tony-guan", source="mission_control",
            idempotency_key="todo-one",
            now=datetime.fromisoformat("2026-08-01T10:00:00-07:00"),
        ).todo
        runner.fail_event_write = True

        with self.assertRaisesRegex(PartialMutationError, "Rollback verified"):
            adapter.edit_todo(
                todo.slug,
                text="Confirm 17:00",
                detail="",
                expected_updated_at=todo.updated_at,
                actor="people/tony-guan",
                source="mission_control",
                idempotency_key="edit-fails",
                now=datetime.fromisoformat("2026-08-01T10:01:00-07:00"),
            )

        stored = adapter.list_task_todos(task.slug, limit=100).todos[0]
        self.assertEqual(stored.text, "Confirm window")
        self.assertEqual(runner.pages[task.slug]["frontmatter"]["next_action"], "Confirm window")
        self.assertEqual([event.event_type for event in stored.events], ["created"])

    def test_migrates_legacy_next_action_and_history_once(self) -> None:
        self.assertTrue(hasattr(GBrainAdapter, "migrate_legacy_next_actions"))
        runner, task = self._fixture(
            next_action="Draft the release notes",
            history=[
                {"action": "Collect evidence", "completed_at": "2026-07-31T09:00:00-07:00"},
                {"action": "Review scope", "completed_at": "2026-07-31T10:00:00-07:00"},
            ],
        )
        adapter = GBrainAdapter(runner)
        now = datetime.fromisoformat("2026-08-01T10:00:00-07:00")

        first = adapter.migrate_legacy_next_actions(task.slug, now=now)
        second = adapter.migrate_legacy_next_actions(task.slug, now=now)

        self.assertEqual(len(first.todos), 3)
        self.assertEqual(len(second.todos), 3)
        self.assertEqual([todo.status for todo in first.todos], ["not_done", "done", "done"])
        first_history = next(
            todo
            for todo in first.todos
            if todo.legacy_provenance.get("index") == 0
        )
        self.assertEqual(first_history.updated_at.isoformat(), "2026-07-31T09:00:00-07:00")
        self.assertEqual(first_history.legacy_provenance["field"], "next_action_history")
        current = next(todo for todo in first.todos if todo.status == "not_done")
        self.assertEqual(current.legacy_provenance["field"], "next_action")
        self.assertEqual(
            len([page for page in runner.pages.values() if page.get("type") == "todo" and not page.get("deleted_at")]),
            3,
        )

    def test_migrates_maximum_length_legacy_action_with_bounded_identity_key(self) -> None:
        long_action = "A" * 240
        runner, task = self._fixture(next_action=long_action)

        result = GBrainAdapter(runner).migrate_legacy_next_actions(
            task.slug,
            now=datetime.fromisoformat("2026-08-01T10:00:00-07:00"),
        )

        self.assertEqual(len(result.todos), 1)
        self.assertEqual(result.todos[0].text, long_action)
        self.assertLessEqual(len(result.todos[0].events[0].idempotency_key), 200)

    def test_agent_question_preserves_parent_lifecycle_and_relationships(self) -> None:
        self.assertTrue(hasattr(GBrainAdapter, "create_todo"))
        runner, task = self._fixture(agent_slug="agents/toddy")
        original_links = deepcopy(runner.links)
        receipt = GBrainAdapter(runner).create_todo(
            task.slug,
            text="Tony: choose the deployment window",
            detail="Reply with 17:00 or 18:00.",
            kind="question",
            actor="agents/toddy",
            source="agent",
            idempotency_key="toddy-question-window",
            now=datetime.fromisoformat("2026-08-01T10:00:00-07:00"),
        )

        self.assertEqual(receipt.todo.status, "not_done")
        self.assertEqual(receipt.todo.creator, "agents/toddy")
        self.assertEqual(runner.pages[task.slug]["frontmatter"]["status"], "planned")
        for edge in original_links:
            self.assertIn(edge, runner.links)

    def test_request_agent_input_blocks_task_and_records_resume_contract(self) -> None:
        runner, task = self._fixture(agent_slug="agents/tammy")
        now = datetime.fromisoformat("2026-08-02T10:00:00-07:00")

        receipt = GBrainAdapter(runner).request_agent_input(
            task.slug,
            question="Which Bible translation should I use?",
            question_detail="Name the exact translation or authorize Tammy to choose.",
            resume_action="Draft the complete seven-day plan.",
            agent_slug="agents/tammy",
            idempotency_key="question-round-1",
            now=now,
        )

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.task.status, "blocked")
        self.assertEqual(receipt.task.blockers, ("people/tony-guan",))
        self.assertEqual(receipt.task.handoff.state, "waiting_for_input")
        self.assertEqual(receipt.task.handoff.question_todo, receipt.todo.slug)
        self.assertEqual(receipt.task.handoff.resume_owner, "agents/tammy")
        self.assertEqual(receipt.task.handoff.resume_action, "Draft the complete seven-day plan.")
        self.assertEqual(receipt.todo.kind, "question")
        self.assertEqual(receipt.next_owner, "people/tony-guan")
        self.assertTrue(any(
            edge.get("from_slug") == task.slug
            and edge.get("to_slug") == "people/tony-guan"
            and edge.get("link_type") == "blocked_by"
            for edge in runner.links
        ))

    def test_answer_question_atomically_returns_work_to_agent(self) -> None:
        runner, task = self._fixture(agent_slug="agents/tammy")
        adapter = GBrainAdapter(runner)
        question = adapter.request_agent_input(
            task.slug,
            question="Which Bible translation, time budget, and reading alignment?",
            question_detail="Answer all three parts.",
            resume_action="Draft and return the complete seven-day Bible-study plan.",
            agent_slug="agents/tammy",
            idempotency_key="question-round-1",
            now=datetime.fromisoformat("2026-08-02T10:00:00-07:00"),
        ).todo
        answered_at = datetime.fromisoformat("2026-08-02T10:29:22-07:00")

        receipt = adapter.answer_agent_question(
            question.slug,
            answer="Chinese Union Version (Shen Edition); 30 minutes; independent readings.",
            expected_updated_at=question.updated_at,
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="answer-round-1",
            now=answered_at,
        )

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.todo.status, "done")
        self.assertEqual(receipt.todo.comments[-1].body, "Chinese Union Version (Shen Edition); 30 minutes; independent readings.")
        self.assertEqual(receipt.task.status, "active")
        self.assertEqual(receipt.task.blockers, ())
        self.assertEqual(receipt.task.next_action, "Draft and return the complete seven-day Bible-study plan.")
        self.assertEqual(receipt.task.handoff.state, "ready_for_agent")
        self.assertEqual(receipt.task.handoff.answered_at, answered_at)
        self.assertEqual(receipt.task.updated_at, answered_at)
        self.assertEqual(receipt.next_owner, "agents/tammy")
        self.assertFalse(any(
            edge.get("from_slug") == task.slug
            and edge.get("to_slug") == "people/tony-guan"
            and edge.get("link_type") == "blocked_by"
            for edge in runner.links
        ))

        duplicate = adapter.answer_agent_question(
            question.slug,
            answer="Chinese Union Version (Shen Edition); 30 minutes; independent readings.",
            expected_updated_at=question.updated_at,
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="answer-round-1",
            now=answered_at,
        )
        self.assertTrue(duplicate.idempotent)
        self.assertEqual(len(duplicate.todo.comments), 1)

    def test_acknowledge_and_follow_up_reuse_the_same_task(self) -> None:
        runner, task = self._fixture(agent_slug="agents/tammy")
        adapter = GBrainAdapter(runner)
        question = adapter.request_agent_input(
            task.slug,
            question="Which translation?",
            question_detail="Name it.",
            resume_action="Draft the plan.",
            agent_slug="agents/tammy",
            idempotency_key="question-round-1",
            now=datetime.fromisoformat("2026-08-02T10:00:00-07:00"),
        ).todo
        answered = adapter.answer_agent_question(
            question.slug,
            answer="Chinese Union Version.",
            expected_updated_at=question.updated_at,
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="answer-round-1",
            now=datetime.fromisoformat("2026-08-02T10:20:00-07:00"),
        )

        acknowledged = adapter.acknowledge_agent_handoff(
            task.slug,
            actor="agents/tammy",
            now=datetime.fromisoformat("2026-08-02T11:00:00-07:00"),
        )
        self.assertEqual(acknowledged.task.slug, task.slug)
        self.assertEqual(acknowledged.task.handoff.state, "agent_working")
        self.assertEqual(acknowledged.task.status, "active")
        self.assertEqual(acknowledged.task.owner_agent, "agents/tammy")

        follow_up = adapter.request_agent_input(
            task.slug,
            question="Should each day use an independent reading?",
            question_detail="Answer yes or no.",
            resume_action=answered.task.handoff.resume_action,
            agent_slug="agents/tammy",
            idempotency_key="question-round-2",
            now=datetime.fromisoformat("2026-08-02T11:10:00-07:00"),
        )
        self.assertEqual(follow_up.task.slug, task.slug)
        self.assertEqual(follow_up.task.status, "blocked")
        self.assertEqual(follow_up.task.handoff.state, "waiting_for_input")
        self.assertEqual(follow_up.task.handoff.round, 2)
        self.assertNotEqual(follow_up.todo.slug, question.slug)

    def test_completing_acknowledged_handoff_clears_handoff(self) -> None:
        runner, task = self._fixture(agent_slug="agents/tammy")
        adapter = GBrainAdapter(runner)
        question = adapter.request_agent_input(
            task.slug,
            question="Which translation?",
            question_detail="Name it.",
            resume_action="Draft the plan.",
            agent_slug="agents/tammy",
            idempotency_key="question-round-1",
            now=datetime.fromisoformat("2026-08-02T10:00:00-07:00"),
        ).todo
        adapter.answer_agent_question(
            question.slug,
            answer="Chinese Union Version.",
            expected_updated_at=question.updated_at,
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="answer-round-1",
            now=datetime.fromisoformat("2026-08-02T10:20:00-07:00"),
        )
        adapter.acknowledge_agent_handoff(
            task.slug,
            actor="agents/tammy",
            now=datetime.fromisoformat("2026-08-02T11:00:00-07:00"),
        )

        receipt = adapter.set_task_status(
            task.slug,
            "completed",
            datetime.fromisoformat("2026-08-02T12:00:00-07:00"),
        )

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.task.status, "completed")
        self.assertIsNone(receipt.task.handoff)

    def test_repeating_completion_repairs_partial_terminal_handoff(self) -> None:
        runner, task = self._fixture(agent_slug="agents/tammy")
        adapter = GBrainAdapter(runner)
        question = adapter.request_agent_input(
            task.slug,
            question="Which translation?",
            question_detail="Name it.",
            resume_action="Draft the plan.",
            agent_slug="agents/tammy",
            idempotency_key="question-round-1",
            now=datetime.fromisoformat("2026-08-02T10:00:00-07:00"),
        ).todo
        adapter.answer_agent_question(
            question.slug,
            answer="Chinese Union Version.",
            expected_updated_at=question.updated_at,
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="answer-round-1",
            now=datetime.fromisoformat("2026-08-02T10:20:00-07:00"),
        )
        adapter.acknowledge_agent_handoff(
            task.slug,
            actor="agents/tammy",
            now=datetime.fromisoformat("2026-08-02T11:00:00-07:00"),
        )
        partial_at = datetime.fromisoformat("2026-08-02T12:00:00-07:00")
        frontmatter = runner.pages[task.slug]["frontmatter"]
        frontmatter["status"] = "completed"
        frontmatter["completed_at"] = partial_at.isoformat()
        frontmatter["updated_at"] = partial_at.isoformat()

        receipt = adapter.set_task_status(
            task.slug,
            "completed",
            datetime.fromisoformat("2026-08-02T12:05:00-07:00"),
        )

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.task.status, "completed")
        self.assertIsNone(receipt.task.handoff)

    def test_repeating_completion_repairs_partial_ready_handoff(self) -> None:
        runner, task = self._fixture(agent_slug="agents/tammy")
        adapter = GBrainAdapter(runner)
        question = adapter.request_agent_input(
            task.slug,
            question="Which translation?",
            question_detail="Name it.",
            resume_action="Draft the plan.",
            agent_slug="agents/tammy",
            idempotency_key="question-round-1",
            now=datetime.fromisoformat("2026-08-02T10:00:00-07:00"),
        ).todo
        adapter.answer_agent_question(
            question.slug,
            answer="Chinese Union Version.",
            expected_updated_at=question.updated_at,
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="answer-round-1",
            now=datetime.fromisoformat("2026-08-02T10:20:00-07:00"),
        )
        partial_at = datetime.fromisoformat("2026-08-02T12:00:00-07:00")
        frontmatter = runner.pages[task.slug]["frontmatter"]
        frontmatter["status"] = "completed"
        frontmatter["completed_at"] = partial_at.isoformat()
        frontmatter["updated_at"] = partial_at.isoformat()

        receipt = adapter.set_task_status(
            task.slug,
            "completed",
            datetime.fromisoformat("2026-08-02T12:05:00-07:00"),
        )

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.task.status, "completed")
        self.assertIsNone(receipt.task.handoff)

    def test_repairs_answered_legacy_question_into_verified_ready_handoff(self) -> None:
        runner, task = self._fixture(
            agent_slug="agents/tammy",
            next_action="Tony confirms translation, time budget, and reading alignment.",
        )
        adapter = GBrainAdapter(runner)
        migrated = adapter.migrate_legacy_next_actions(
            task.slug,
            now=datetime.fromisoformat("2026-08-02T10:00:00-07:00"),
        ).todos[0]
        answered = adapter.edit_todo(
            migrated.slug,
            text=migrated.text,
            detail=(
                "Bible translation: Chinese Union Version (Shen Edition)\n"
                "daily time budget: 30 minutes\n"
                "Readings should be independent."
            ),
            expected_updated_at=migrated.updated_at,
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="legacy-answer-edit",
            now=datetime.fromisoformat("2026-08-02T10:20:00-07:00"),
        ).todo
        completed = adapter.set_todo_status(
            answered.slug,
            status="done",
            expected_updated_at=answered.updated_at,
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="legacy-answer-done",
            now=datetime.fromisoformat("2026-08-02T10:29:22-07:00"),
        ).todo
        prior_events = completed.event_slugs

        receipt = adapter.repair_answered_agent_handoff(
            task.slug,
            question_todo_slug=completed.slug,
            expected_answer=completed.detail,
            resume_action="Draft and return the complete seven-day Bible-study plan.",
            agent_slug="agents/tammy",
            idempotency_key="repair-tammy-seven-day-plan",
            now=datetime.fromisoformat("2026-08-02T15:00:00-07:00"),
        )

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.task.status, "active")
        self.assertEqual(receipt.task.next_action, "Draft and return the complete seven-day Bible-study plan.")
        self.assertEqual(receipt.task.handoff.state, "ready_for_agent")
        self.assertEqual(receipt.task.handoff.answered_at.isoformat(), "2026-08-02T10:29:22-07:00")
        self.assertEqual(receipt.task.owner_agent, "agents/tammy")
        self.assertEqual(receipt.todo.kind, "question")
        self.assertEqual(receipt.todo.status, "done")
        self.assertEqual(receipt.todo.comments[-1].body, completed.detail)
        self.assertEqual(receipt.todo.event_slugs[: len(prior_events)], prior_events)
        self.assertEqual(receipt.next_owner, "agents/tammy")

        duplicate = adapter.repair_answered_agent_handoff(
            task.slug,
            question_todo_slug=completed.slug,
            expected_answer=completed.detail,
            resume_action="Draft and return the complete seven-day Bible-study plan.",
            agent_slug="agents/tammy",
            idempotency_key="repair-tammy-seven-day-plan",
            now=datetime.fromisoformat("2026-08-02T15:00:00-07:00"),
        )
        self.assertTrue(duplicate.idempotent)
        self.assertEqual(len(duplicate.todo.comments), 1)

    def test_repairs_incomplete_answer_back_to_waiting_for_input(self) -> None:
        runner, task = self._fixture(agent_slug="agents/toddy")
        adapter = GBrainAdapter(runner)
        question = adapter.request_agent_input(
            task.slug,
            question="Which single family-care category should Toddy consider first?",
            question_detail="Choose exactly one category.",
            resume_action="Resume after Tony chooses one category.",
            agent_slug="agents/toddy",
            idempotency_key="family-category-question",
            now=datetime.fromisoformat("2026-08-02T10:00:00-07:00"),
        ).todo
        answered = adapter.answer_agent_question(
            question.slug,
            answer="Scope, outcomes, constraints, and procedure accepted.",
            expected_updated_at=question.updated_at,
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="partial-family-answer",
            now=datetime.fromisoformat("2026-08-02T10:30:00-07:00"),
        )
        prior_comment_slugs = answered.todo.comment_slugs
        prior_event_slugs = answered.todo.event_slugs

        frontmatter = runner.pages[task.slug]["frontmatter"]
        frontmatter["status"] = "active"
        frontmatter["blocked_by"] = "Tony category selection"
        frontmatter["next_action"] = (
            "Which one category should be considered first: core care, periodic "
            "support, emergency-only, or out of current scope?"
        )
        frontmatter["handoff"]["waiting_on"] = "people/tony-guan"
        frontmatter["handoff"]["acknowledged_at"] = "2026-08-02T10:35:00-07:00"

        receipt = adapter.repair_incomplete_agent_answer_handoff(
            task.slug,
            question_todo_slug=question.slug,
            remaining_question=frontmatter["next_action"],
            resume_action="Resume after Tony chooses one category.",
            agent_slug="agents/toddy",
            idempotency_key="repair-incomplete-family-answer",
            now=datetime.fromisoformat("2026-08-02T11:00:00-07:00"),
        )

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.next_owner, "people/tony-guan")
        self.assertEqual(receipt.task.status, "blocked")
        self.assertEqual(receipt.task.owner_agent, "agents/toddy")
        self.assertEqual(receipt.task.blockers, ("people/tony-guan",))
        self.assertEqual(receipt.task.handoff.state, "waiting_for_input")
        self.assertEqual(receipt.task.handoff.question_todo, question.slug)
        self.assertEqual(receipt.task.handoff.waiting_on, "people/tony-guan")
        self.assertIsNone(receipt.task.handoff.answered_at)
        self.assertIsNone(receipt.task.handoff.acknowledged_at)
        self.assertEqual(receipt.task.next_action, frontmatter["next_action"])
        self.assertEqual(receipt.todo.status, "not_done")
        self.assertEqual(receipt.todo.comment_slugs, prior_comment_slugs)
        self.assertEqual(receipt.todo.event_slugs[: len(prior_event_slugs)], prior_event_slugs)
        self.assertEqual(receipt.todo.events[-1].event_type, "status_changed")
        self.assertEqual(receipt.todo.events[-1].before["status"], "done")
        self.assertEqual(receipt.todo.events[-1].after["status"], "not_done")
        self.assertTrue(any(
            edge.get("from_slug") == task.slug
            and edge.get("to_slug") == "people/tony-guan"
            and edge.get("link_type") == "blocked_by"
            for edge in runner.links
        ))

        duplicate = adapter.repair_incomplete_agent_answer_handoff(
            task.slug,
            question_todo_slug=question.slug,
            remaining_question=frontmatter["next_action"],
            resume_action="Resume after Tony chooses one category.",
            agent_slug="agents/toddy",
            idempotency_key="repair-incomplete-family-answer",
            now=datetime.fromisoformat("2026-08-02T11:00:00-07:00"),
        )
        self.assertTrue(duplicate.idempotent)
        self.assertEqual(duplicate.todo.event_slugs, receipt.todo.event_slugs)

    def test_handoff_rejects_empty_answer_stale_question_and_wrong_agent(self) -> None:
        runner, task = self._fixture(agent_slug="agents/tammy")
        adapter = GBrainAdapter(runner)
        question = adapter.request_agent_input(
            task.slug,
            question="Which translation?",
            question_detail="Name it.",
            resume_action="Draft the plan.",
            agent_slug="agents/tammy",
            idempotency_key="question-round-1",
            now=datetime.fromisoformat("2026-08-02T10:00:00-07:00"),
        ).todo

        with self.assertRaisesRegex(ValueError, "answer"):
            adapter.answer_agent_question(
                question.slug,
                answer=" ",
                expected_updated_at=question.updated_at,
                actor="people/tony-guan",
                source="mission_control",
                idempotency_key="empty-answer",
                now=datetime.fromisoformat("2026-08-02T10:10:00-07:00"),
            )
        with self.assertRaisesRegex(Exception, "changed since it was read"):
            adapter.answer_agent_question(
                question.slug,
                answer="Chinese Union Version.",
                expected_updated_at=datetime.fromisoformat("2026-08-02T09:59:00-07:00"),
                actor="people/tony-guan",
                source="mission_control",
                idempotency_key="stale-answer",
                now=datetime.fromisoformat("2026-08-02T10:10:00-07:00"),
            )
        with self.assertRaisesRegex(ValueError, "assigned Agent"):
            adapter.acknowledge_agent_handoff(
                task.slug,
                actor="agents/timmy",
                now=datetime.fromisoformat("2026-08-02T10:15:00-07:00"),
            )

    def test_answer_keeps_task_blocked_when_an_unrelated_blocker_remains(self) -> None:
        runner, task = self._fixture(agent_slug="agents/tammy")
        runner.pages[task.slug]["frontmatter"]["links"].append(
            {"to": "systems/calendar-permission", "type": "blocked_by"}
        )
        runner.links.append(
            {
                "from_slug": task.slug,
                "to_slug": "systems/calendar-permission",
                "link_type": "blocked_by",
                "context": "Independent blocker",
                "link_source": "gtasks",
            }
        )
        adapter = GBrainAdapter(runner)
        question = adapter.request_agent_input(
            task.slug,
            question="Which translation?",
            question_detail="Name it.",
            resume_action="Draft the plan.",
            agent_slug="agents/tammy",
            idempotency_key="question-round-1",
            now=datetime.fromisoformat("2026-08-02T10:00:00-07:00"),
        ).todo

        receipt = adapter.answer_agent_question(
            question.slug,
            answer="Chinese Union Version.",
            expected_updated_at=question.updated_at,
            actor="people/tony-guan",
            source="mission_control",
            idempotency_key="answer-round-1",
            now=datetime.fromisoformat("2026-08-02T10:20:00-07:00"),
        )

        self.assertEqual(receipt.todo.status, "done")
        self.assertEqual(receipt.task.status, "blocked")
        self.assertEqual(receipt.task.blockers, ("systems/calendar-permission",))
        self.assertIsNone(receipt.task.handoff)
        self.assertIsNone(receipt.next_owner)

    def test_failed_answer_link_removal_rolls_back_question_and_parent(self) -> None:
        class FailingBlockerRemovalRunner(StatefulIdentityMigrationRunner):
            fail_once = True

            def run(self, tool: str, params: dict) -> object:
                if (
                    self.fail_once
                    and tool == "remove_link"
                    and params.get("link_type") == "blocked_by"
                    and params.get("to") == "people/tony-guan"
                ):
                    self.fail_once = False
                    raise GBrainCommandError("forced blocker removal failure")
                return super().run(tool, params)

        base, task = self._fixture(agent_slug="agents/tammy")
        runner = FailingBlockerRemovalRunner(base.pages, base.links)
        adapter = GBrainAdapter(runner)
        question = adapter.request_agent_input(
            task.slug,
            question="Which translation?",
            question_detail="Name it.",
            resume_action="Draft the plan.",
            agent_slug="agents/tammy",
            idempotency_key="question-round-1",
            now=datetime.fromisoformat("2026-08-02T10:00:00-07:00"),
        ).todo

        with self.assertRaisesRegex(PartialMutationError, "Rollback verified"):
            adapter.answer_agent_question(
                question.slug,
                answer="Chinese Union Version.",
                expected_updated_at=question.updated_at,
                actor="people/tony-guan",
                source="mission_control",
                idempotency_key="answer-round-1",
                now=datetime.fromisoformat("2026-08-02T10:20:00-07:00"),
            )

        restored_task = adapter.get_task(task.slug)
        restored_question = adapter.list_task_todos(task.slug, limit=100).todos[0]
        self.assertEqual(restored_task.status, "blocked")
        self.assertEqual(restored_task.handoff.state, "waiting_for_input")
        self.assertEqual(restored_question.status, "not_done")
        self.assertEqual(restored_question.comments, ())
        self.assertIn("people/tony-guan", restored_task.blockers)


class TaskNextActionMutationTests(unittest.TestCase):
    def test_full_task_edit_uses_same_history_preserving_write(self) -> None:
        now = datetime(2026, 7, 30, 15, tzinfo=timezone.utc)
        task = replace(
            new_inbox_task("Prepare interview", now, "a1b2c3"),
            next_action="Collect examples",
        )
        initial_page = stored_page(task)
        final_page = deepcopy(initial_page)
        final_page["frontmatter"].update(
            {
                "type": "task",
                "title": task.title,
                "summary": task.title,
                "detail": task.detail,
                "priority": task.priority,
                "due_day": task.due_day.isoformat(),
                "next_action": "Draft three STAR examples",
                "next_action_history": [
                    {
                        "action": "Collect examples",
                        "completed_at": now.isoformat(),
                    }
                ],
                "progress_metric": None,
                "event_progress": None,
                "updated_at": now.isoformat(),
            }
        )
        edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "get_page": [initial_page, final_page],
                "get_links": [[edge], [edge]],
                "put_page": [{"slug": task.slug}],
            }
        )

        receipt = GBrainAdapter(runner).edit_task(
            task.slug,
            title=task.title,
            detail=task.detail,
            priority=task.priority,
            due_day=task.due_day,
            next_action="Draft three STAR examples",
            project_slug=None,
            goal_slug=None,
            status=task.status,
            assignee_slug="tony",
            progress_metric=None,
            event_progress=None,
            handoff_reason="",
            now=now,
        )

        self.assertTrue(receipt.verified)
        written = next(
            params["content"]
            for tool, params in runner.calls
            if tool == "put_page"
        )
        self.assertIn('"action": "Collect examples"', written)
        self.assertIn(f'"completed_at": "{now.isoformat()}"', written)
        self.assertNotIn("markdown_contract", written)
        self.assertIn(f"# {task.title}", written)

    def test_marked_full_task_edit_rerenders_and_verifies_unified_body(self) -> None:
        now = datetime(2026, 8, 10, 15, tzinfo=timezone.utc)
        task = new_inbox_task("Original title", now, "marked-edit")
        initial_page = stored_page(task)
        initial_page["frontmatter"]["markdown_contract"] = MARKDOWN_CONTRACT
        initial_page["compiled_truth"] = render_task_body(
            task.title, task.detail, {}
        )
        updated_title = "Updated # title"
        updated_detail = "Updated authoritative detail."
        expected_body = render_task_body(updated_title, updated_detail, {})
        final_page = deepcopy(initial_page)
        final_page.update({"title": updated_title, "compiled_markdown": expected_body})
        final_page["frontmatter"].update(
            {
                "type": "task",
                "title": updated_title,
                "summary": updated_title,
                "detail": updated_detail,
                "priority": task.priority,
                "due_day": task.due_day.isoformat(),
                "next_action": task.next_action,
                "next_action_history": [],
                "progress_metric": None,
                "event_progress": None,
                "updated_at": now.isoformat(),
                "markdown_contract": MARKDOWN_CONTRACT,
            }
        )
        edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "get_page": [initial_page, final_page],
                "get_links": [[edge], [edge]],
                "put_page": [{"slug": task.slug}],
            }
        )

        receipt = GBrainAdapter(runner).edit_task(
            task.slug,
            title=updated_title,
            detail=updated_detail,
            priority=task.priority,
            due_day=task.due_day,
            next_action=task.next_action,
            project_slug=None,
            goal_slug=None,
            status=task.status,
            assignee_slug="tony",
            progress_metric=None,
            event_progress=None,
            handoff_reason="",
            now=now,
        )

        written = next(
            params["content"] for tool, params in runner.calls if tool == "put_page"
        )
        self.assertTrue(receipt.verified)
        self.assertIn(f'markdown_contract: "{MARKDOWN_CONTRACT}"', written)
        self.assertTrue(written.rstrip().endswith(expected_body))

    def test_marked_full_task_edit_rejects_stale_compiled_body_readback(self) -> None:
        now = datetime(2026, 8, 10, 15, tzinfo=timezone.utc)
        task = new_inbox_task("Original title", now, "marked-stale-edit")
        initial_page = stored_page(task)
        initial_page["frontmatter"]["markdown_contract"] = MARKDOWN_CONTRACT
        initial_page["compiled_truth"] = render_task_body(
            task.title, task.detail, {}
        )
        stale_page = deepcopy(initial_page)
        stale_page["frontmatter"].update(
            {"detail": "Changed detail", "updated_at": now.isoformat()}
        )
        edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "get_page": [initial_page, stale_page],
                "get_links": [[edge], [edge]],
                "put_page": [{"slug": task.slug}],
            }
        )

        with self.assertRaisesRegex(PartialMutationError, "compiled Markdown"):
            GBrainAdapter(runner).edit_task(
                task.slug,
                title=task.title,
                detail="Changed detail",
                priority=task.priority,
                due_day=task.due_day,
                next_action=task.next_action,
                project_slug=None,
                goal_slug=None,
                status=task.status,
                assignee_slug="tony",
                progress_metric=None,
                event_progress=None,
                handoff_reason="",
                now=now,
            )

    def test_sets_next_action_and_preserves_task_identity_and_relationships(self) -> None:
        now = datetime(2026, 7, 30, 14, 15, tzinfo=timezone(timedelta(hours=-7)))
        task = new_inbox_task("Prepare interview", now, "a1b2c3")
        initial_page = stored_page(task)
        initial_page["frontmatter"]["captured_via"] = "capture-cli"
        active_edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        goal_edge = {
            "from_slug": task.slug,
            "to_slug": "goals/find-next-role",
            "link_type": "advances_goal",
        }
        final_page = deepcopy(initial_page)
        final_page["frontmatter"]["next_action"] = "Draft three STAR examples"
        final_page["frontmatter"]["next_action_history"] = []
        final_page["frontmatter"]["updated_at"] = now.isoformat()
        runner = FakeRunner(
            {
                "get_page": [initial_page, final_page],
                "get_links": [
                    [active_edge, goal_edge],
                    [active_edge, goal_edge],
                ],
                "put_page": [{"slug": task.slug}],
            }
        )

        receipt = GBrainAdapter(runner).set_task_next_action(
            task.slug,
            "  Draft three STAR examples  ",
            now,
        )

        self.assertIsInstance(receipt, NextActionMutationReceipt)
        self.assertEqual(receipt.next_action, "Draft three STAR examples")
        self.assertTrue(receipt.verified)
        written = next(
            params["content"]
            for tool, params in runner.calls
            if tool == "put_page"
        )
        self.assertIn('type: "task"', written)
        self.assertIn('"type": "member_of"', written)
        self.assertIn('captured_via: "capture-cli"', written)
        self.assertIn('next_action_history: []', written)
        self.assertIn("# Prepare interview", written)
        self.assertNotIn("add_link", [tool for tool, _params in runner.calls])
        self.assertNotIn("remove_link", [tool for tool, _params in runner.calls])

    def test_can_clear_next_action(self) -> None:
        now = datetime(2026, 7, 30, 14, 15, tzinfo=timezone.utc)
        task = replace(
            new_inbox_task("Prepare interview", now, "a1b2c3"),
            next_action="Draft three STAR examples",
        )
        initial_page = stored_page(task)
        final_page = deepcopy(initial_page)
        final_page["frontmatter"]["next_action"] = ""
        final_page["frontmatter"]["next_action_history"] = [
            {
                "action": "Draft three STAR examples",
                "completed_at": now.isoformat(),
            }
        ]
        final_page["frontmatter"]["updated_at"] = now.isoformat()
        active_edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "get_page": [initial_page, final_page],
                "get_links": [[active_edge], [active_edge]],
                "put_page": [{"slug": task.slug}],
            }
        )

        receipt = GBrainAdapter(runner).set_task_next_action(task.slug, "", now)

        self.assertEqual(receipt.next_action, "")
        self.assertTrue(receipt.verified)
        written = next(
            params["content"]
            for tool, params in runner.calls
            if tool == "put_page"
        )
        self.assertIn('"action": "Draft three STAR examples"', written)
        self.assertIn(f'"completed_at": "{now.isoformat()}"', written)

    def test_rolls_back_when_next_action_readback_does_not_match(self) -> None:
        now = datetime(2026, 7, 30, 14, 15, tzinfo=timezone.utc)
        task = replace(
            new_inbox_task("Prepare interview", now, "a1b2c3"),
            next_action="Review role notes",
        )
        initial_page = stored_page(task)
        mismatched_page = deepcopy(initial_page)
        mismatched_page["frontmatter"]["next_action"] = "Unexpected value"
        active_edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "get_page": [initial_page, mismatched_page, initial_page],
                "get_links": [[active_edge], [active_edge], [active_edge]],
                "put_page": [
                    {"slug": task.slug},
                    {"slug": task.slug},
                ],
            }
        )

        with self.assertRaises(PartialMutationError) as raised:
            GBrainAdapter(runner).set_task_next_action(
                task.slug,
                "Draft three STAR examples",
                now,
            )

        self.assertEqual(raised.exception.slug, task.slug)
        self.assertIn("Rollback verified", str(raised.exception))
        self.assertEqual(
            [tool for tool, _params in runner.calls].count("put_page"),
            2,
        )


class MarkdownCreationPathTests(unittest.TestCase):
    TICKET_SLUG = "tasks/fad23bf2-571f-4db0-b9f5-07ab52ae8620"

    def test_task_page_renders_canonical_goal_derivation_receipt(self) -> None:
        goal_slug = "goals/41fb50e0-e1d7-592b-b2c3-ff1f7aacff10"
        derivation = domain.GoalDerivationReceipt(
            planner_version="goal-execution-v1",
            fingerprint="c" * 64,
            action_kind="goal_progress_review",
            authority_class="auto_eligible",
            goal_slug=goal_slug,
            project_slug=None,
            expected_evidence="One internal progress brief.",
        )
        task = replace(
            new_inbox_task(
                "Review Civic progress",
                datetime(2026, 8, 23, 9, tzinfo=timezone.utc),
                "goal-review",
            ),
            lifecycle_root="collections/timmys-tasks",
            owner_agent="agents/timmy",
            goal=goal_slug,
            goal_derivation=derivation,
        )

        rendered = gbrain_module.render_task_page(task)

        self.assertIn(
            'goal_derivation: {"planner_version": "goal-execution-v1", '
            '"fingerprint": "' + "c" * 64,
            rendered,
        )
        self.assertIn('"goal_slug": "' + goal_slug + '"', rendered)

    def test_create_task_projects_verified_ticket_reference_without_a_relationship(self) -> None:
        task = new_task(
            title="Continue dispatcher work",
            detail=f"Use {self.TICKET_SLUG}.",
            now=datetime(2026, 8, 10, 9, tzinfo=timezone.utc),
            identity="markdown-task",
        )
        ticket = SystemTicket(
            self.TICKET_SLUG,
            "Dispatcher",
            "planned",
            "Original dispatcher request.",
            "mission_control",
            "normal",
        )

        class Runner:
            def __init__(self) -> None:
                self.calls = []
                self.links = []
                self.body = ""

            def run(self, tool, params):
                self.calls.append((tool, deepcopy(params)))
                slug = params.get("slug")
                if tool == "put_page":
                    self.body = params["content"].split("\n---\n", 1)[1].strip()
                    return {"slug": params["slug"]}
                if tool == "get_page" and slug == task.slug:
                    page = marked_stored_page(task)
                    page["compiled_markdown"] = self.body
                    return page
                if tool == "get_page" and slug == self_ticket_slug:
                    return {
                        "slug": ticket.slug,
                        "type": "task",
                        "title": ticket.title,
                        "frontmatter": {
                            "type": "task",
                            "title": ticket.title,
                            "status": ticket.status,
                            "priority": ticket.priority,
                            "verbatim_request": ticket.verbatim_request,
                            "target_subsystem": ticket.target_subsystem,
                            "links": [{"to": SYSTEM_TICKETS_ROOT, "type": "member_of"}],
                        },
                    }
                if tool == "get_links" and slug == task.slug:
                    return deepcopy(self.links)
                if tool == "get_links" and slug == self_ticket_slug:
                    return [{"from_slug": ticket.slug, "to_slug": SYSTEM_TICKETS_ROOT, "link_type": "member_of"}]
                if tool == "add_link":
                    self.links.append({
                        "from_slug": params["from"], "to_slug": params["to"],
                        "link_type": params["link_type"], "link_source": params.get("link_source"),
                    })
                    return {}
                raise AssertionError(f"unexpected {tool}: {params}")

        self_ticket_slug = self.TICKET_SLUG
        runner = Runner()
        receipt = GBrainAdapter(runner).create_task(task)

        self.assertTrue(receipt.verified)
        self.assertEqual(
            runner.body,
            render_task_body(
                task.title,
                task.detail,
                {ticket.slug: SystemTicketReference(ticket.slug, ticket.title)},
            ),
        )
        self.assertEqual(
            [(edge["to_slug"], edge["link_type"]) for edge in runner.links],
            [(ACTIVE_ROOT, "member_of")],
        )

    def test_reference_resolution_leaves_ordinary_tasks_unlinked_and_labels_only_explicit_unavailable_tickets(self) -> None:
        ordinary = "tasks/0bcdef12-3456-4abc-8def-0123456789ab"
        missing = "tasks/1bcdef12-3456-4abc-8def-0123456789ab"
        runner = FakeRunner(
            {
                "get_page": [
                    {
                        "slug": ordinary,
                        "type": "task",
                        "title": "Ordinary task",
                        "frontmatter": {"type": "task", "title": "Ordinary task"},
                    },
                    GBrainCommandError("page_not_found"),
                ],
                "get_links": [[]],
            }
        )

        references = GBrainAdapter(runner)._verified_system_ticket_references(
            (f"Follow {ordinary}.", f"System Ticket: {missing}")
        )

        self.assertEqual(references, {missing: None})
        self.assertNotIn("search", [tool for tool, _params in runner.calls])
        self.assertNotIn("add_link", [tool for tool, _params in runner.calls])

    def test_frontmatter_only_ticket_membership_is_unavailable_not_clickable(self) -> None:
        slug = "tasks/2bcdef12-3456-4abc-8def-0123456789ab"
        page = {
            "slug": slug,
            "type": "task",
            "title": "Forged ticket projection",
            "frontmatter": {
                "type": "task",
                "title": "Forged ticket projection",
                "status": "planned",
                "priority": "normal",
                "verbatim_request": "Do not trust frontmatter alone.",
                "target_subsystem": "mission_control",
                "links": [{"to": SYSTEM_TICKETS_ROOT, "type": "member_of"}],
            },
        }
        for live_links in (
            [],
            [{"from_slug": slug, "to_slug": SYSTEM_TICKETS_ROOT, "link_type": "wrong_type"}],
        ):
            with self.subTest(live_links=live_links):
                runner = FakeRunner({"get_page": [page], "get_links": [live_links]})
                detail = f"System Ticket: {slug}"
                references = GBrainAdapter(runner)._verified_system_ticket_references(
                    (detail,)
                )
                body = render_task_body("Continue", detail, references)

                self.assertEqual(references, {slug: None})
                self.assertIn(f"System Ticket unavailable: {slug}", body)
                self.assertNotIn("#system-ticket/", body)

        ordinary_runner = FakeRunner({"get_page": [page], "get_links": [[]]})
        ordinary_body = render_task_body(
            "Continue",
            f"Follow {slug} when ready.",
            GBrainAdapter(ordinary_runner)._verified_system_ticket_references(
                (f"Follow {slug} when ready.",)
            ),
        )
        self.assertNotIn("关联的 System Tickets", ordinary_body)
        self.assertNotIn("#system-ticket/", ordinary_body)

    def test_create_inbox_treats_a_missing_compiled_markdown_body_as_partial_mutation(self) -> None:
        task = new_inbox_task(
            "Verify body readback",
            datetime(2026, 8, 10, 9, tzinfo=timezone.utc),
            "markdown-readback",
        )
        page = marked_stored_page(task)
        page.pop("compiled_markdown")
        runner = FakeRunner(
            {"put_page": [{"slug": task.slug}], "get_page": [page]}
        )

        with self.assertRaisesRegex(PartialMutationError, "compiled Markdown"):
            GBrainAdapter(runner).create_inbox(task)

        self.assertNotIn("add_link", [tool for tool, _params in runner.calls])

    def test_create_inbox_rereads_full_task_and_rejects_unexpected_assignment(self) -> None:
        task = new_inbox_task(
            "Keep Tony ownership",
            datetime(2026, 8, 10, 9, tzinfo=timezone.utc),
            "tony-owner-readback",
        )
        page = marked_stored_page(task)
        lifecycle = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        unexpected_assignment = {
            "from_slug": task.slug,
            "to_slug": "agents/tammy",
            "link_type": "assigned_to",
        }
        runner = FakeRunner(
            {
                "put_page": [{"slug": task.slug}],
                "get_page": [page, page],
                "get_links": [[], [lifecycle, unexpected_assignment]],
                "add_link": [{}],
            }
        )

        with self.assertRaisesRegex(PartialMutationError, "ownership") as raised:
            GBrainAdapter(runner).create_inbox(task)

        self.assertEqual(raised.exception.slug, task.slug)
        self.assertEqual(
            [tool for tool, _params in runner.calls].count("get_page"),
            2,
        )

    def test_create_inbox_rejects_final_page_drift_after_membership_write(self) -> None:
        task = new_inbox_task(
            "Verify the final page",
            datetime(2026, 8, 10, 9, tzinfo=timezone.utc),
            "final-page-readback",
        )
        initial = marked_stored_page(task)
        drifted = deepcopy(initial)
        drifted["frontmatter"]["detail"] = "Changed after the first readback."
        lifecycle = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "put_page": [{"slug": task.slug}],
                "get_page": [initial, drifted],
                "get_links": [[], [lifecycle]],
                "add_link": [{}],
            }
        )

        with self.assertRaisesRegex(PartialMutationError, "final.*Task") as raised:
            GBrainAdapter(runner).create_inbox(task)

        self.assertEqual(raised.exception.slug, task.slug)

    def test_create_agent_task_wraps_post_write_edge_read_as_partial_mutation(self) -> None:
        now = datetime(2026, 8, 10, 9, tzinfo=timezone.utc)
        agent_slug = "agents/toddy"
        work_root = "collections/toddys-tasks"
        task = replace(
            new_task(
                title="Verify post-write boundary",
                now=now,
                identity="agent-edge-readback",
            ),
            lifecycle_root=work_root,
            owner_agent=agent_slug,
        )
        profile = AgentProfile(
            slug=agent_slug,
            name="Toddy",
            title="Codex Agent",
            summary="Canonical execution agent.",
            work_root=work_root,
            default_goal_slugs=(),
        )
        agent_page = {
            "slug": agent_slug,
            "type": "agent",
            "title": "Agent Toddy",
            "compiled_truth": "# Agent Toddy",
            "frontmatter": {},
        }

        class Runner:
            def run(self, tool, params):
                if tool == "get_page" and params["slug"] == agent_slug:
                    return agent_page
                if tool == "get_links" and params["slug"] == agent_slug:
                    return []
                if tool == "put_page":
                    return {"slug": task.slug}
                if tool == "get_links" and params["slug"] == task.slug:
                    raise GBrainCommandError("post-write edge read failed")
                raise AssertionError(f"unexpected {tool}: {params}")

        adapter = GBrainAdapter(Runner())
        with patch.object(
            adapter,
            "list_agent_profiles",
            return_value=gbrain_module.AgentRead((profile,), ()),
        ):
            with self.assertRaises(PartialMutationError) as raised:
                adapter.create_agent_task(task, agent_slug)

        self.assertEqual(raised.exception.slug, task.slug)
        self.assertIn("post-write edge read failed", str(raised.exception))

    def test_create_agent_task_wraps_final_lifecycle_readback_as_partial_mutation(self) -> None:
        now = datetime(2026, 8, 10, 9, tzinfo=timezone.utc)
        agent_slug = "agents/toddy"
        work_root = "collections/toddys-tasks"
        task = replace(
            new_task(
                title="Verify final lifecycle boundary",
                now=now,
                identity="agent-final-lifecycle",
            ),
            lifecycle_root=work_root,
            owner_agent=agent_slug,
        )
        profile = AgentProfile(
            slug=agent_slug,
            name="Toddy",
            title="Codex Agent",
            summary="Canonical execution agent.",
            work_root=work_root,
            default_goal_slugs=(),
        )
        agent_page = {
            "slug": agent_slug,
            "type": "agent",
            "title": "Agent Toddy",
            "compiled_truth": "# Agent Toddy",
            "frontmatter": {},
        }
        task_page = marked_stored_page(task)
        task_page["frontmatter"]["links"] = [
            {"to": work_root, "type": "member_of"},
            {"to": agent_slug, "type": "assigned_to"},
        ]
        task_page["compiled_markdown"] = render_task_body(
            task.title, task.detail, {}
        )
        final_links = [
            {"from_slug": task.slug, "to_slug": work_root, "link_type": "member_of"},
            {"from_slug": task.slug, "to_slug": ACTIVE_ROOT, "link_type": "member_of"},
            {"from_slug": task.slug, "to_slug": agent_slug, "link_type": "assigned_to"},
        ]

        class Runner:
            def __init__(self):
                self.task_link_reads = 0

            def run(self, tool, params):
                if tool == "get_page" and params["slug"] == agent_slug:
                    return agent_page
                if tool == "get_links" and params["slug"] == agent_slug:
                    return []
                if tool == "put_page":
                    return {"slug": task.slug}
                if tool == "add_link":
                    return {}
                if tool == "get_page" and params["slug"] == task.slug:
                    return task_page
                if tool == "get_links" and params["slug"] == task.slug:
                    self.task_link_reads += 1
                    return [] if self.task_link_reads == 1 else final_links
                raise AssertionError(f"unexpected {tool}: {params}")

        adapter = GBrainAdapter(Runner())
        with patch.object(
            adapter,
            "list_agent_profiles",
            return_value=gbrain_module.AgentRead((profile,), ()),
        ):
            with self.assertRaises(PartialMutationError) as raised:
                adapter.create_agent_task(task, agent_slug)

        self.assertEqual(raised.exception.slug, task.slug)
        self.assertIn("lifecycle", str(raised.exception).lower())

    def test_new_page_body_replaces_only_the_legacy_body_projection(self) -> None:
        parent = new_inbox_task(
            "Bible Study parent",
            datetime(2026, 8, 10, 9, tzinfo=timezone.utc),
            "bible-parent",
        )
        task = replace(
            new_inbox_task(
                "Bible Study child",
                datetime(2026, 8, 10, 9, tzinfo=timezone.utc),
                "bible-child",
            ),
            parent=parent.slug,
        )
        legacy = gbrain_module.render_task_page(task)
        expected_body = render_task_body(task.title, task.detail, {})
        rendered = gbrain_module.render_task_page(task, body=expected_body)

        rendered_frontmatter = rendered.split("\n---\n", 1)[0].replace(
            f"markdown_contract: {MARKDOWN_CONTRACT}\n", ""
        )
        self.assertEqual(legacy.split("\n---\n", 1)[0], rendered_frontmatter)
        self.assertEqual(
            rendered.split("\n---\n", 1)[1].strip(), expected_body.strip()
        )
        self.assertIn(f"markdown_contract: {MARKDOWN_CONTRACT}", rendered)

    def test_exact_task_api_payload_exposes_display_only_compiled_markdown(self) -> None:
        task = new_inbox_task(
            "Display canonical body",
            datetime(2026, 8, 10, 9, tzinfo=timezone.utc),
            "task-display-payload",
        )
        page = stored_page(task)
        display = render_task_body(task.title, task.detail, {})
        page["compiled_markdown"] = display
        page["frontmatter"]["markdown_contract"] = MARKDOWN_CONTRACT
        edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner({"get_page": [page], "get_links": [[], [edge]]})

        payload = GBrainAdapter(runner).get_task_api_payload(task.slug)

        self.assertEqual(payload["detail"], task.detail)
        self.assertEqual(payload["display_markdown"], display)

    def test_task_api_omits_unmarked_or_stale_display_projection(self) -> None:
        task = new_inbox_task(
            "Do not expose stale body",
            datetime(2026, 8, 10, 9, tzinfo=timezone.utc),
            "task-display-stale",
        )
        edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        for marker, compiled in (
            (None, render_task_body(task.title, task.detail, {})),
            (MARKDOWN_CONTRACT, "# Stale body"),
        ):
            with self.subTest(marker=marker):
                page = stored_page(task)
                page["compiled_markdown"] = compiled
                if marker is not None:
                    page["frontmatter"]["markdown_contract"] = marker
                runner = FakeRunner(
                    {"get_page": [page], "get_links": [[], [edge]]}
                )

                payload = GBrainAdapter(runner).get_task_api_payload(task.slug)

                self.assertNotIn("display_markdown", payload)

    def test_historical_task_api_payload_safely_omits_missing_projection(self) -> None:
        task = new_inbox_task(
            "Historical task",
            datetime(2026, 8, 10, 9, tzinfo=timezone.utc),
            "historical-display",
        )
        page = stored_page(task)
        page.pop("compiled_markdown")
        edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner({"get_page": [page], "get_links": [[], [edge]]})

        payload = GBrainAdapter(runner).get_task_api_payload(task.slug)

        self.assertNotIn("display_markdown", payload)


class SystemTicketAdapterTests(unittest.TestCase):
    def _system_ticket_page(self, ticket: SystemTicket) -> dict:
        return {
            "slug": ticket.slug,
            "type": "task",
            "title": ticket.title,
            "compiled_markdown": render_system_ticket_body(
                ticket.title,
                ticket.verbatim_request,
                acceptance_criteria=ticket.acceptance_criteria,
                implementation_receipts=ticket.implementation_receipts,
                qa_receipts=ticket.qa_receipts,
            ),
            "frontmatter": {
                "type": "task",
                "markdown_contract": MARKDOWN_CONTRACT,
                "title": ticket.title,
                "status": ticket.status,
                "priority": ticket.priority,
                "verbatim_request": ticket.verbatim_request,
                "target_subsystem": ticket.target_subsystem,
                "acceptance_criteria": ticket.acceptance_criteria,
                "implementation_receipts": list(ticket.implementation_receipts),
                "qa_receipts": list(ticket.qa_receipts),
                "links": [{"to": SYSTEM_TICKETS_ROOT, "type": "member_of"}],
            },
        }

    def _write_system_ticket_snapshot(self, path: Path, tickets: tuple[SystemTicket, ...]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "surfaces": {
                        "system_tickets_all": {
                            "last_valid_at": 1786615358.282858,
                            "payload": {
                                "root_slug": SYSTEM_TICKETS_ROOT,
                                "tickets": [ticket.to_dict() for ticket in tickets],
                                "issues": [],
                            },
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_reads_pre_ui_system_ticket_from_its_existing_task_detail(self) -> None:
        page = {
            "slug": "tasks/calendar-view-selected-task-highlight",
            "type": "task",
            "title": "Highlight selected task in Calendar View",
            "frontmatter": {
                "status": "planned", "priority": "normal",
                "detail": "Tony requested a visible selected state.",
                "target_subsystem": "mission-control-calendar",
                "acceptance_criteria": "The state is accessible.",
            },
            "created_at": "2026-07-31T17:00:00+00:00",
        }
        edge = {"from_slug": page["slug"], "to_slug": SYSTEM_TICKETS_ROOT, "link_type": "member_of"}

        ticket = SystemTicket.from_page(page, [edge])

        self.assertEqual(ticket.status, "planned")
        self.assertEqual(ticket.target_subsystem, "mission_control")
        self.assertEqual(ticket.verbatim_request, "Tony requested a visible selected state.")

    def test_ticket_read_exposes_compiled_markdown_without_changing_fields(self) -> None:
        ticket = SystemTicket(
            "tasks/system-tickets/display-a1b2c3",
            "Display ticket body",
            "planned",
            "Preserve this exact request.",
            "mission_control",
            "normal",
        )
        display = render_system_ticket_body(
            ticket.title, ticket.verbatim_request
        )
        page = {
            "slug": ticket.slug,
            "type": "task",
            "title": ticket.title,
            "compiled_markdown": display,
            "frontmatter": {
                "type": "task",
                "markdown_contract": MARKDOWN_CONTRACT,
                "title": ticket.title,
                "status": ticket.status,
                "priority": ticket.priority,
                "verbatim_request": ticket.verbatim_request,
                "target_subsystem": ticket.target_subsystem,
                "links": [{"to": SYSTEM_TICKETS_ROOT, "type": "member_of"}],
            },
        }
        edge = {
            "from_slug": ticket.slug,
            "to_slug": SYSTEM_TICKETS_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner(
            {
                "get_backlinks": [[edge]],
                "get_page": [page],
                "get_links": [[edge]],
            }
        )

        payload = GBrainAdapter(runner).list_system_tickets().to_dict()

        self.assertEqual(payload["tickets"][0]["verbatim_request"], ticket.verbatim_request)
        self.assertEqual(payload["tickets"][0]["display_markdown"], display)

    def test_ticket_list_display_uses_stored_markdown_without_reference_fanout(self) -> None:
        ticket = SystemTicket(
            "tasks/system-tickets/display-reference-a1b2c3",
            "Show referenced repair task",
            "planned",
            "Repair exact affected task tasks/business-reference-a1b2c3.",
            "mission_control",
            "normal",
        )
        display = render_system_ticket_body(ticket.title, ticket.verbatim_request)
        page = {
            "slug": ticket.slug,
            "type": "task",
            "title": ticket.title,
            "compiled_markdown": display,
            "frontmatter": {
                "type": "task",
                "markdown_contract": MARKDOWN_CONTRACT,
                "title": ticket.title,
                "status": ticket.status,
                "priority": ticket.priority,
                "verbatim_request": ticket.verbatim_request,
                "target_subsystem": ticket.target_subsystem,
                "links": [{"to": SYSTEM_TICKETS_ROOT, "type": "member_of"}],
            },
        }
        edge = {
            "from_slug": ticket.slug,
            "to_slug": SYSTEM_TICKETS_ROOT,
            "link_type": "member_of",
        }

        class NoReferenceFanoutRunner:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict]] = []

            def run(self, tool: str, params: dict) -> object:
                self.calls.append((tool, dict(params)))
                if tool == "get_backlinks":
                    return [edge]
                if tool == "get_page":
                    return page
                if tool == "get_links":
                    raise AssertionError(
                        "System Ticket list display must not hydrate referenced tasks"
                    )
                raise AssertionError(f"unexpected tool {tool}")

        runner = NoReferenceFanoutRunner()

        payload = GBrainAdapter(runner).list_system_tickets().to_dict()

        self.assertEqual(payload["tickets"][0]["display_markdown"], display)
        self.assertEqual(
            [tool for tool, _params in runner.calls if tool == "get_links"],
            [],
        )

    def test_default_ticket_read_skips_completed_before_link_and_display_hydration(self) -> None:
        planned = SystemTicket(
            "tasks/system-tickets/open-read-a1b2c3",
            "Keep open ticket fast",
            "planned",
            "Open ticket should remain visible.",
            "mission_control",
            "normal",
        )
        completed = SystemTicket(
            "tasks/system-tickets/completed-read-a1b2c3",
            "Completed ticket should not fan out",
            "completed",
            "Completed ticket is not needed for the default view.",
            "mission_control",
            "normal",
        )
        planned_edge = {
            "from_slug": planned.slug,
            "to_slug": SYSTEM_TICKETS_ROOT,
            "link_type": "member_of",
        }
        completed_edge = {
            "from_slug": completed.slug,
            "to_slug": SYSTEM_TICKETS_ROOT,
            "link_type": "member_of",
        }
        planned_page = {
            "slug": planned.slug,
            "type": "task",
            "title": planned.title,
            "compiled_markdown": render_system_ticket_body(
                planned.title, planned.verbatim_request
            ),
            "frontmatter": {
                "type": "task",
                "markdown_contract": MARKDOWN_CONTRACT,
                "title": planned.title,
                "status": planned.status,
                "priority": planned.priority,
                "verbatim_request": planned.verbatim_request,
                "target_subsystem": planned.target_subsystem,
                "links": [{"to": SYSTEM_TICKETS_ROOT, "type": "member_of"}],
            },
        }
        completed_page = {
            "slug": completed.slug,
            "type": "task",
            "title": completed.title,
            "compiled_markdown": render_system_ticket_body(
                completed.title, completed.verbatim_request
            ),
            "frontmatter": {
                "type": "task",
                "markdown_contract": MARKDOWN_CONTRACT,
                "title": completed.title,
                "status": completed.status,
                "priority": completed.priority,
                "verbatim_request": completed.verbatim_request,
                "target_subsystem": completed.target_subsystem,
                "links": [{"to": SYSTEM_TICKETS_ROOT, "type": "member_of"}],
            },
        }
        runner = FakeRunner(
            {
                "get_backlinks": [[planned_edge, completed_edge]],
                "get_page": [planned_page, completed_page],
                "get_links": [[planned_edge]],
            }
        )

        payload = GBrainAdapter(runner).list_system_tickets(
            include_completed=False
        ).to_dict()

        self.assertEqual([ticket["slug"] for ticket in payload["tickets"]], [planned.slug])
        self.assertEqual(
            [params["slug"] for tool, params in runner.calls if tool == "get_links"],
            [],
        )

    def test_default_ticket_read_skips_unchanged_cached_completed_pages(self) -> None:
        cached_at = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)
        planned = SystemTicket(
            "tasks/system-tickets/open-fast-a1b2c3",
            "Open ticket",
            "planned",
            "Return this ticket.",
            "mission_control",
            "normal",
            updated_at=cached_at,
        )
        completed = tuple(
            SystemTicket(
                f"tasks/system-tickets/completed-{index}",
                f"Completed ticket {index}",
                "completed",
                "Do not hydrate completed tickets for the default view.",
                "mission_control",
                "normal",
                updated_at=cached_at,
            )
            for index in range(3)
        )
        edges = [
            {
                "from_slug": ticket.slug,
                "to_slug": SYSTEM_TICKETS_ROOT,
                "link_type": "member_of",
            }
            for ticket in (planned, *completed)
        ]
        with TemporaryDirectory() as directory:
            cache_path = Path(directory) / "read-snapshots.json"
            self._write_system_ticket_snapshot(cache_path, (planned, *completed))
            runner = FakeRunner(
                {
                    "get_backlinks": [edges],
                    "list_pages": [
                        [
                            {
                                "slug": ticket.slug,
                                "type": "task",
                                "updated_at": cached_at.isoformat(),
                            }
                            for ticket in (planned, *completed)
                        ]
                    ],
                }
            )
            with patch.dict(os.environ, {"GTASKS_READ_CACHE_FILE": str(cache_path)}):
                payload = GBrainAdapter(runner).list_system_tickets(
                    include_completed=False
                ).to_dict()

        self.assertEqual([ticket["slug"] for ticket in payload["tickets"]], [planned.slug])
        self.assertEqual(
            [params["slug"] for tool, params in runner.calls if tool == "get_page"],
            [],
        )
        self.assertEqual(
            [params["slug"] for tool, params in runner.calls if tool == "get_links"],
            [],
        )

    def test_default_ticket_read_rehydrates_changed_completed_snapshot_to_detect_reopen(self) -> None:
        cached_at = datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc)
        reopened_at = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)
        cached_completed = SystemTicket(
            "tasks/system-tickets/reopened-a1b2c3",
            "Reopened ticket",
            "completed",
            "The cached snapshot is stale.",
            "mission_control",
            "high",
            updated_at=cached_at,
        )
        reopened = replace(cached_completed, status="planned", updated_at=reopened_at)
        edge = {
            "from_slug": reopened.slug,
            "to_slug": SYSTEM_TICKETS_ROOT,
            "link_type": "member_of",
        }
        with TemporaryDirectory() as directory:
            cache_path = Path(directory) / "read-snapshots.json"
            self._write_system_ticket_snapshot(cache_path, (cached_completed,))
            runner = FakeRunner(
                {
                    "get_backlinks": [[edge]],
                    "list_pages": [
                        [
                            {
                                "slug": reopened.slug,
                                "type": "task",
                                "updated_at": reopened_at.isoformat(),
                            }
                        ]
                    ],
                    "get_page": [self._system_ticket_page(reopened)],
                }
            )
            with patch.dict(os.environ, {"GTASKS_READ_CACHE_FILE": str(cache_path)}):
                payload = GBrainAdapter(runner).list_system_tickets(
                    include_completed=False
                ).to_dict()

        self.assertEqual(
            [(ticket["slug"], ticket["status"]) for ticket in payload["tickets"]],
            [(reopened.slug, "planned")],
        )
        self.assertEqual(
            [params["slug"] for tool, params in runner.calls if tool == "get_page"],
            [reopened.slug],
        )
        self.assertEqual(
            [tool for tool, _params in runner.calls if tool == "get_links"],
            [],
        )

    def test_include_completed_ticket_read_serves_unchanged_verified_snapshot(self) -> None:
        cached_at = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)
        planned = SystemTicket(
            "tasks/system-tickets/open-all-a1b2c3",
            "Open ticket",
            "active",
            "Hydrate non-completed tickets.",
            "mission_control",
            "high",
            updated_at=cached_at,
        )
        completed = SystemTicket(
            "tasks/system-tickets/completed-all-a1b2c3",
            "Completed ticket",
            "completed",
            "Serve completed ticket from last verified snapshot.",
            "mission_control",
            "normal",
            updated_at=cached_at,
        )
        edges = [
            {
                "from_slug": ticket.slug,
                "to_slug": SYSTEM_TICKETS_ROOT,
                "link_type": "member_of",
            }
            for ticket in (planned, completed)
        ]
        with TemporaryDirectory() as directory:
            cache_path = Path(directory) / "read-snapshots.json"
            self._write_system_ticket_snapshot(cache_path, (planned, completed))
            runner = FakeRunner(
                {
                    "get_backlinks": [edges],
                    "list_pages": [
                        [
                            {
                                "slug": ticket.slug,
                                "type": "task",
                                "updated_at": cached_at.isoformat(),
                            }
                            for ticket in (planned, completed)
                        ]
                    ],
                }
            )
            with patch.dict(os.environ, {"GTASKS_READ_CACHE_FILE": str(cache_path)}):
                payload = GBrainAdapter(runner).list_system_tickets(
                    include_completed=True
                ).to_dict()

        self.assertEqual(
            {ticket["slug"] for ticket in payload["tickets"]},
            {planned.slug, completed.slug},
        )
        self.assertEqual(
            [params["slug"] for tool, params in runner.calls if tool == "get_page"],
            [],
        )
        self.assertEqual(
            [params["slug"] for tool, params in runner.calls if tool == "get_links"],
            [],
        )

    def test_partial_verified_system_ticket_snapshot_hydrates_only_missing_member_slug(self) -> None:
        cached_at = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)
        planned = SystemTicket(
            "tasks/system-tickets/open-partial-a1b2c3",
            "Open ticket",
            "planned",
            "Hydrate open tickets.",
            "mission_control",
            "high",
            updated_at=cached_at,
        )
        cached_completed = SystemTicket(
            "tasks/system-tickets/cached-completed-a1b2c3",
            "Cached completed ticket",
            "completed",
            "Serve this from the verified snapshot.",
            "mission_control",
            "normal",
            updated_at=cached_at,
        )
        missing_completed = SystemTicket(
            "tasks/system-tickets/new-completed-a1b2c3",
            "New completed ticket",
            "completed",
            "Hydrate only this missing member.",
            "mission_control",
            "normal",
            updated_at=cached_at,
        )
        tickets = (planned, cached_completed, missing_completed)
        edges = [
            {
                "from_slug": ticket.slug,
                "to_slug": SYSTEM_TICKETS_ROOT,
                "link_type": "member_of",
            }
            for ticket in tickets
        ]

        class SlugAwareRunner:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict]] = []

            def run(self, tool: str, params: dict) -> object:
                self.calls.append((tool, dict(params)))
                if tool == "get_backlinks":
                    return edges
                if tool == "list_pages":
                    return [
                        {
                            "slug": ticket.slug,
                            "type": "task",
                            "updated_at": cached_at.isoformat(),
                        }
                        for ticket in tickets
                    ]
                slug = params["slug"]
                if tool == "get_page":
                    return self_page[slug]
                if tool == "get_links":
                    return [
                        edge for edge in edges if edge["from_slug"] == slug
                    ]
                raise AssertionError(f"unexpected tool {tool}")

        self_page = {
            planned.slug: self._system_ticket_page(planned),
            cached_completed.slug: self._system_ticket_page(cached_completed),
            missing_completed.slug: self._system_ticket_page(missing_completed),
        }
        with TemporaryDirectory() as directory:
            cache_path = Path(directory) / "read-snapshots.json"
            self._write_system_ticket_snapshot(
                cache_path,
                (planned, cached_completed),
            )
            runner = SlugAwareRunner()
            with patch.dict(os.environ, {"GTASKS_READ_CACHE_FILE": str(cache_path)}):
                payload = GBrainAdapter(runner).list_system_tickets(
                    include_completed=True
                ).to_dict()

        self.assertEqual(
            {ticket["slug"] for ticket in payload["tickets"]},
            {ticket.slug for ticket in tickets},
        )
        self.assertEqual(
            [params["slug"] for tool, params in runner.calls if tool == "get_page"],
            [missing_completed.slug],
        )
        self.assertEqual(
            [params["slug"] for tool, params in runner.calls if tool == "get_links"],
            [],
        )

    def test_include_completed_uses_root_backlink_when_page_read_shows_cached_open_ticket_completed(self) -> None:
        cached_at = datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc)
        completed_at = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)
        stale_open = SystemTicket(
            "tasks/system-tickets/stale-open-a1b2c3",
            "Stale open ticket",
            "active",
            "The snapshot has not seen this completion yet.",
            "mission_control",
            "normal",
            updated_at=cached_at,
        )
        completed = replace(stale_open, status="completed", updated_at=completed_at)
        edge = {
            "from_slug": stale_open.slug,
            "to_slug": SYSTEM_TICKETS_ROOT,
            "link_type": "member_of",
        }

        class NoCompletedLinksRunner:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict]] = []

            def run(self, tool: str, params: dict) -> object:
                self.calls.append((tool, dict(params)))
                if tool == "get_backlinks":
                    return [edge]
                if tool == "list_pages":
                    return [
                        {
                            "slug": completed.slug,
                            "type": "task",
                            "updated_at": completed_at.isoformat(),
                        }
                    ]
                if tool == "get_page":
                    return self._page
                if tool == "get_links":
                    raise AssertionError(
                        "completed System Ticket membership is already proven by the root backlink"
                    )
                raise AssertionError(f"unexpected tool {tool}")

        runner = NoCompletedLinksRunner()
        runner._page = self._system_ticket_page(completed)
        with TemporaryDirectory() as directory:
            cache_path = Path(directory) / "read-snapshots.json"
            self._write_system_ticket_snapshot(cache_path, (stale_open,))
            with patch.dict(os.environ, {"GTASKS_READ_CACHE_FILE": str(cache_path)}):
                payload = GBrainAdapter(runner).list_system_tickets(
                    include_completed=True
                ).to_dict()

        self.assertEqual(payload["tickets"][0]["slug"], completed.slug)
        self.assertEqual(payload["tickets"][0]["status"], "completed")
        self.assertEqual(
            [params["slug"] for tool, params in runner.calls if tool == "get_page"],
            [completed.slug],
        )
        self.assertEqual(
            [tool for tool, _params in runner.calls if tool == "get_links"],
            [],
        )

    def test_system_ticket_list_uses_root_backlink_membership_without_per_ticket_link_read(self) -> None:
        planned = SystemTicket(
            "tasks/system-tickets/root-backlink-a1b2c3",
            "Root backlink ticket",
            "planned",
            "The root backlink is the typed membership readback.",
            "mission_control",
            "normal",
        )
        edge = {
            "from_slug": planned.slug,
            "to_slug": SYSTEM_TICKETS_ROOT,
            "link_type": "member_of",
        }

        class RootBacklinkOnlyRunner:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict]] = []

            def run(self, tool: str, params: dict) -> object:
                self.calls.append((tool, dict(params)))
                if tool == "get_backlinks":
                    return [edge]
                if tool == "get_page":
                    return self._page
                if tool == "get_links":
                    raise AssertionError(
                        "System Ticket list already verified membership through root backlinks"
                    )
                raise AssertionError(f"unexpected tool {tool}")

        runner = RootBacklinkOnlyRunner()
        runner._page = self._system_ticket_page(planned)

        payload = GBrainAdapter(runner).list_system_tickets(
            include_completed=False
        ).to_dict()

        self.assertEqual(payload["tickets"][0]["slug"], planned.slug)
        self.assertEqual(
            [params["slug"] for tool, params in runner.calls if tool == "get_page"],
            [planned.slug],
        )
        self.assertEqual(
            [tool for tool, _params in runner.calls if tool == "get_links"],
            [],
        )

    def test_verified_system_ticket_update_invalidates_ticket_snapshot_cache(self) -> None:
        existing = SystemTicket(
            "tasks/system-tickets/update-cache-a1b2c3",
            "Update ticket",
            "planned",
            "Update this ticket.",
            "mission_control",
            "normal",
        )
        updated = replace(
            existing,
            status="active",
            implementation_receipts=("implementation started",),
        )
        edge = {
            "from_slug": existing.slug,
            "to_slug": SYSTEM_TICKETS_ROOT,
            "link_type": "member_of",
        }
        with TemporaryDirectory() as directory:
            cache_path = Path(directory) / "read-snapshots.json"
            self._write_system_ticket_snapshot(cache_path, (existing,))
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            cache["surfaces"]["system_tickets"] = cache["surfaces"][
                "system_tickets_all"
            ]
            cache["surfaces"]["tasks"] = {"payload": {"unrelated": True}}
            cache_path.write_text(json.dumps(cache), encoding="utf-8")
            runner = FakeRunner(
                {
                    "get_page": [
                        self._system_ticket_page(existing),
                        self._system_ticket_page(updated),
                    ],
                    "get_links": [[edge], [edge]],
                    "put_page": [{}],
                }
            )
            with patch.dict(os.environ, {"GTASKS_READ_CACHE_FILE": str(cache_path)}):
                receipt = GBrainAdapter(runner).update_system_ticket(updated)

            stored = json.loads(cache_path.read_text(encoding="utf-8"))

        self.assertTrue(receipt.verified)
        self.assertIn("system_tickets_all", stored["surfaces"])
        refreshed = stored["surfaces"]["system_tickets_all"]["payload"]["tickets"]
        self.assertEqual(
            [item["slug"] for item in refreshed],
            [updated.slug],
        )
        self.assertEqual(refreshed[0]["status"], updated.status)
        self.assertIn("tasks", stored["surfaces"])

    def test_ticket_read_omits_unmarked_or_stale_display_projection(self) -> None:
        ticket = SystemTicket(
            "tasks/system-tickets/stale-display-a1b2c3",
            "Do not expose stale Ticket body",
            "planned",
            "Canonical request.",
            "mission_control",
            "normal",
        )
        edge = {
            "from_slug": ticket.slug,
            "to_slug": SYSTEM_TICKETS_ROOT,
            "link_type": "member_of",
        }
        for marker, compiled in (
            (None, render_system_ticket_body(ticket.title, ticket.verbatim_request)),
            (MARKDOWN_CONTRACT, "# Stale Ticket body"),
        ):
            with self.subTest(marker=marker):
                page = {
                    "slug": ticket.slug,
                    "type": "task",
                    "title": ticket.title,
                    "compiled_markdown": compiled,
                    "frontmatter": {
                        "type": "task",
                        "title": ticket.title,
                        "status": ticket.status,
                        "priority": ticket.priority,
                        "verbatim_request": ticket.verbatim_request,
                        "target_subsystem": ticket.target_subsystem,
                        "links": [
                            {"to": SYSTEM_TICKETS_ROOT, "type": "member_of"}
                        ],
                    },
                }
                if marker is not None:
                    page["frontmatter"]["markdown_contract"] = marker
                runner = FakeRunner(
                    {
                        "get_backlinks": [[edge]],
                        "get_page": [page],
                        "get_links": [[edge]],
                    }
                )

                payload = GBrainAdapter(runner).list_system_tickets().to_dict()

                self.assertNotIn("display_markdown", payload["tickets"][0])
    def test_create_ticket_writes_task_type_typed_membership_and_exact_readback(self) -> None:
        now = datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc)
        ticket = SystemTicket(
            slug="tasks/system-tickets/calendar-selection-a1b2c3",
            title="Improve Calendar selection",
            status="planned",
            verbatim_request="Highlight the selected Calendar task.",
            target_subsystem="mission_control",
            priority="high",
            acceptance_criteria="The selected task is clear.",
            created_at=now,
            updated_at=now,
        )
        root = {"slug": SYSTEM_TICKETS_ROOT, "type": "collection"}
        stored = {
            "slug": ticket.slug,
            "type": "task",
            "title": ticket.title,
            "compiled_markdown": render_system_ticket_body(
                ticket.title,
                ticket.verbatim_request,
                acceptance_criteria=ticket.acceptance_criteria,
            ),
            "frontmatter": {
                "type": "task", "title": ticket.title, "status": "planned",
                "markdown_contract": MARKDOWN_CONTRACT,
                "priority": "high", "verbatim_request": ticket.verbatim_request,
                "target_subsystem": "mission_control",
                "acceptance_criteria": ticket.acceptance_criteria,
                "linked_evidence": [], "implementation_receipts": [], "qa_receipts": [],
                "created_at": now.isoformat(), "updated_at": now.isoformat(),
                "links": [{"to": SYSTEM_TICKETS_ROOT, "type": "member_of"}],
            },
        }
        edge = {"from_slug": ticket.slug, "to_slug": SYSTEM_TICKETS_ROOT, "link_type": "member_of"}
        runner = FakeRunner({
            "get_page": [root, stored], "put_page": [{"slug": ticket.slug}],
            "add_link": [{}], "get_links": [[edge]],
        })

        receipt = GBrainAdapter(runner).create_system_ticket(ticket)

        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.slug, ticket.slug)
        content = next(params["content"] for tool, params in runner.calls if tool == "put_page")
        self.assertIn("type: task", content)
        self.assertIn(f"markdown_contract: {MARKDOWN_CONTRACT}", content)
        self.assertIn(SYSTEM_TICKETS_ROOT, content)
        self.assertEqual(
            next(params for tool, params in runner.calls if tool == "add_link")["link_type"],
            "member_of",
        )

    def test_create_ticket_accepts_unified_compiled_truth_readback(self) -> None:
        now = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
        ticket = SystemTicket(
            slug="tasks/system-tickets/truth-readback-a1b2c3",
            title="Accept compiled truth",
            status="planned",
            verbatim_request="Treat the normalized canonical body as verified.",
            target_subsystem="mission_control",
            priority="normal",
            created_at=now,
            updated_at=now,
        )
        expected = render_system_ticket_body(ticket.title, ticket.verbatim_request)
        stored = {
            "slug": ticket.slug,
            "type": "task",
            "title": ticket.title,
            "compiled_truth": expected,
            "frontmatter": {
                "type": "task", "title": ticket.title, "status": ticket.status,
                "markdown_contract": MARKDOWN_CONTRACT, "priority": ticket.priority,
                "verbatim_request": ticket.verbatim_request,
                "target_subsystem": ticket.target_subsystem,
                "linked_evidence": [], "implementation_receipts": [], "qa_receipts": [],
                "created_at": now.isoformat(), "updated_at": now.isoformat(),
                "links": [{"to": SYSTEM_TICKETS_ROOT, "type": "member_of"}],
            },
        }
        edge = {
            "from_slug": ticket.slug,
            "to_slug": SYSTEM_TICKETS_ROOT,
            "link_type": "member_of",
        }
        runner = FakeRunner({
            "get_page": [{"slug": SYSTEM_TICKETS_ROOT, "type": "collection"}, stored],
            "put_page": [{"slug": ticket.slug}], "add_link": [{}], "get_links": [[edge]],
        })

        receipt = GBrainAdapter(runner).create_system_ticket(ticket)

        self.assertTrue(receipt.verified)

    def test_create_ticket_requires_exact_live_membership_edge(self) -> None:
        now = datetime(2026, 8, 10, 9, tzinfo=timezone.utc)
        ticket = SystemTicket(
            "tasks/system-tickets/live-edge-a1b2c3",
            "Verify live membership",
            "planned",
            "Do not trust frontmatter membership alone.",
            "mission_control",
            "normal",
            created_at=now,
            updated_at=now,
        )
        stored = {
            "slug": ticket.slug,
            "type": "task",
            "title": ticket.title,
            "compiled_markdown": render_system_ticket_body(
                ticket.title, ticket.verbatim_request
            ),
            "frontmatter": {
                "type": "task",
                "title": ticket.title,
                "status": ticket.status,
                "priority": ticket.priority,
                "verbatim_request": ticket.verbatim_request,
                "target_subsystem": ticket.target_subsystem,
                "linked_evidence": [],
                "implementation_receipts": [],
                "qa_receipts": [],
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "links": [{"to": SYSTEM_TICKETS_ROOT, "type": "member_of"}],
            },
        }
        root = {"slug": SYSTEM_TICKETS_ROOT, "type": "collection"}
        for live_links in (
            [],
            [
                {
                    "from_slug": ticket.slug,
                    "to_slug": SYSTEM_TICKETS_ROOT,
                    "link_type": "related_to",
                }
            ],
            [
                {
                    "from_slug": "tasks/system-tickets/somewhere-else",
                    "to_slug": SYSTEM_TICKETS_ROOT,
                    "link_type": "member_of",
                }
            ],
        ):
            with self.subTest(live_links=live_links):
                runner = FakeRunner(
                    {
                        "get_page": [root, stored],
                        "put_page": [{"slug": ticket.slug}],
                        "add_link": [{}],
                        "get_links": [live_links],
                    }
                )
                with self.assertRaisesRegex(
                    PartialMutationError, "live System Tickets membership"
                ) as raised:
                    GBrainAdapter(runner).create_system_ticket(ticket)
                self.assertEqual(raised.exception.slug, ticket.slug)

    def test_create_ticket_wraps_post_write_edge_failure_as_partial_mutation(self) -> None:
        now = datetime(2026, 8, 10, 9, tzinfo=timezone.utc)
        ticket = SystemTicket(
            "tasks/system-tickets/edge-failure-a1b2c3",
            "Surface edge failure",
            "planned",
            "Keep the mutated slug visible.",
            "mission_control",
            "normal",
            created_at=now,
            updated_at=now,
        )
        runner = FakeRunner(
            {
                "get_page": [
                    {"slug": SYSTEM_TICKETS_ROOT, "type": "collection"}
                ],
                "put_page": [{"slug": ticket.slug}],
                "add_link": [GBrainCommandError("edge write failed")],
            }
        )

        with self.assertRaises(PartialMutationError) as raised:
            GBrainAdapter(runner).create_system_ticket(ticket)

        self.assertEqual(raised.exception.slug, ticket.slug)
        self.assertIn("edge write failed", str(raised.exception))

    def test_nightly_selector_returns_all_planned_tickets_in_stable_order(self) -> None:
        first = SystemTicket("tasks/system-tickets/first-aaaaaa", "First", "planned", "A request", "mission_control", "normal", created_at=datetime(2026, 7, 30, tzinfo=timezone.utc))
        active = SystemTicket("tasks/system-tickets/active-bbbbbb", "Active", "active", "Another request", "mission_control", "normal", created_at=datetime(2026, 7, 29, tzinfo=timezone.utc))
        second = SystemTicket("tasks/system-tickets/second-cccccc", "Second", "planned", "Later request", "career_path", "normal", created_at=datetime(2026, 7, 31, tzinfo=timezone.utc))
        def page(ticket: SystemTicket) -> dict:
            return {"slug": ticket.slug, "type": "task", "title": ticket.title, "frontmatter": {"type":"task", "title":ticket.title, "status":ticket.status, "priority":ticket.priority, "verbatim_request":ticket.verbatim_request, "target_subsystem":ticket.target_subsystem, "acceptance_criteria":"", "linked_evidence":[], "implementation_receipts":[], "qa_receipts":[], "created_at":ticket.created_at.isoformat(), "links":[{"to":SYSTEM_TICKETS_ROOT,"type":"member_of"}]}}
        edges = lambda ticket: [{"from_slug":ticket.slug,"to_slug":SYSTEM_TICKETS_ROOT,"link_type":"member_of"}]
        runner = FakeRunner({
            "get_backlinks": [[*edges(first), *edges(active), *edges(second)]],
            "get_page": [page(first), page(active), page(second)],
            "get_links": [edges(first), edges(active), edges(second)],
        })

        selected = GBrainAdapter(runner).planned_system_tickets()

        self.assertEqual([ticket.slug for ticket in selected], [first.slug, second.slug])

    def test_update_ticket_preserves_unknown_fields_receipts_and_typed_membership(self) -> None:
        now = datetime(2026, 8, 1, 16, 0, tzinfo=timezone.utc)
        original = SystemTicket(
            "tasks/system-tickets/edit-me-a1b2c3", "Original", "planned",
            "Original request", "mission_control", "normal", "Original criteria",
            ("evidence",), ("implementation",), ("qa",), now, now,
        )
        updated = replace(
            original,
            title="Edited",
            status="active",
            priority="high",
            verbatim_request="Edited request",
            acceptance_criteria="Edited criteria",
            updated_at=datetime(2026, 8, 1, 17, 0, tzinfo=timezone.utc),
        )
        edge = {"from_slug": original.slug, "to_slug": SYSTEM_TICKETS_ROOT, "link_type": "member_of"}
        def page(ticket: SystemTicket) -> dict:
            return {
                "slug": ticket.slug, "type": "task", "title": ticket.title,
                "compiled_truth": "# User-authored body\n\nKeep this text.",
                "frontmatter": {
                    "type": "task", "title": ticket.title, "status": ticket.status,
                    "priority": ticket.priority, "verbatim_request": ticket.verbatim_request,
                    "target_subsystem": ticket.target_subsystem,
                    "acceptance_criteria": ticket.acceptance_criteria,
                    "linked_evidence": list(ticket.linked_evidence),
                    "implementation_receipts": list(ticket.implementation_receipts),
                    "qa_receipts": list(ticket.qa_receipts),
                    "created_at": ticket.created_at.isoformat(),
                    "updated_at": ticket.updated_at.isoformat(),
                    "custom_user_field": "preserve me",
                    "links": [{"to": SYSTEM_TICKETS_ROOT, "type": "member_of"}],
                },
            }
        runner = FakeRunner({
            "get_page": [page(original), page(updated)],
            "get_links": [[edge], [edge]],
            "put_page": [{"slug": original.slug}],
        })

        receipt = GBrainAdapter(runner).update_system_ticket(updated)

        self.assertTrue(receipt.verified)
        content = next(params["content"] for tool, params in runner.calls if tool == "put_page")
        self.assertIn("custom_user_field", content)
        self.assertIn("Keep this text.", content)
        self.assertIn("implementation", content)
        self.assertIn(SYSTEM_TICKETS_ROOT, content)
        self.assertNotIn("markdown_contract", content)

    def test_update_marked_ticket_rerenders_and_verifies_unified_body(self) -> None:
        now = datetime(2026, 8, 10, 16, tzinfo=timezone.utc)
        original = SystemTicket(
            "tasks/system-tickets/marked-edit-a1b2c3",
            "Original Ticket",
            "planned",
            "Original request",
            "mission_control",
            "normal",
            created_at=now,
            updated_at=now,
        )
        updated = replace(
            original,
            title="Updated Ticket",
            verbatim_request="Updated request",
            acceptance_criteria="Updated criteria",
            updated_at=now + timedelta(hours=1),
        )
        edge = {
            "from_slug": original.slug,
            "to_slug": SYSTEM_TICKETS_ROOT,
            "link_type": "member_of",
        }

        def page(ticket: SystemTicket, body: str) -> dict:
            return {
                "slug": ticket.slug,
                "type": "task",
                "title": ticket.title,
                "compiled_truth": body,
                "compiled_markdown": body,
                "frontmatter": {
                    "type": "task",
                    "markdown_contract": MARKDOWN_CONTRACT,
                    "title": ticket.title,
                    "status": ticket.status,
                    "priority": ticket.priority,
                    "verbatim_request": ticket.verbatim_request,
                    "target_subsystem": ticket.target_subsystem,
                    "acceptance_criteria": ticket.acceptance_criteria,
                    "linked_evidence": list(ticket.linked_evidence),
                    "implementation_receipts": list(ticket.implementation_receipts),
                    "qa_receipts": list(ticket.qa_receipts),
                    "created_at": ticket.created_at.isoformat(),
                    "updated_at": ticket.updated_at.isoformat(),
                    "links": [
                        {"to": SYSTEM_TICKETS_ROOT, "type": "member_of"}
                    ],
                },
            }

        original_body = render_system_ticket_body(
            original.title, original.verbatim_request
        )
        expected_body = render_system_ticket_body(
            updated.title,
            updated.verbatim_request,
            acceptance_criteria=updated.acceptance_criteria,
        )
        runner = FakeRunner(
            {
                "get_page": [page(original, original_body), page(updated, expected_body)],
                "get_links": [[edge], [edge]],
                "put_page": [{"slug": original.slug}],
            }
        )

        receipt = GBrainAdapter(runner).update_system_ticket(updated)

        written = next(
            params["content"] for tool, params in runner.calls if tool == "put_page"
        )
        self.assertTrue(receipt.verified)
        self.assertIn(f'markdown_contract: "{MARKDOWN_CONTRACT}"', written)
        self.assertTrue(written.rstrip().endswith(expected_body))

    def test_update_marked_ticket_rejects_stale_body_readback(self) -> None:
        now = datetime(2026, 8, 10, 16, tzinfo=timezone.utc)
        original = SystemTicket(
            "tasks/system-tickets/marked-stale-a1b2c3",
            "Original Ticket",
            "planned",
            "Original request",
            "mission_control",
            "normal",
            created_at=now,
            updated_at=now,
        )
        updated = replace(original, verbatim_request="Changed request")
        edge = {
            "from_slug": original.slug,
            "to_slug": SYSTEM_TICKETS_ROOT,
            "link_type": "member_of",
        }
        stale_body = render_system_ticket_body(
            original.title, original.verbatim_request
        )
        page = {
            "slug": original.slug,
            "type": "task",
            "title": original.title,
            "compiled_truth": stale_body,
            "compiled_markdown": stale_body,
            "frontmatter": {
                "type": "task",
                "markdown_contract": MARKDOWN_CONTRACT,
                "title": original.title,
                "status": original.status,
                "priority": original.priority,
                "verbatim_request": original.verbatim_request,
                "target_subsystem": original.target_subsystem,
                "acceptance_criteria": "",
                "linked_evidence": [],
                "implementation_receipts": [],
                "qa_receipts": [],
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "links": [{"to": SYSTEM_TICKETS_ROOT, "type": "member_of"}],
            },
        }
        stale_readback = deepcopy(page)
        stale_readback["frontmatter"]["verbatim_request"] = updated.verbatim_request
        runner = FakeRunner(
            {
                "get_page": [page, stale_readback],
                "get_links": [[edge], [edge]],
                "put_page": [{"slug": original.slug}],
            }
        )

        with self.assertRaisesRegex(PartialMutationError, "compiled Markdown"):
            GBrainAdapter(runner).update_system_ticket(updated)


class TaskProgressMetricMutationTests(unittest.TestCase):
    def test_verified_metric_mutation_path_is_available(self) -> None:
        adapter = GBrainAdapter(FakeRunner({}))

        self.assertTrue(
            callable(getattr(adapter, "set_task_progress_metric", None))
        )

    def test_event_progress_mutation_path_is_available(self) -> None:
        adapter = GBrainAdapter(FakeRunner({}))

        self.assertTrue(
            callable(getattr(adapter, "apply_task_progress_event", None))
        )

    def test_sets_metric_with_exact_readback_and_preserves_task_identity(self) -> None:
        now = datetime(
            2026,
            7,
            30,
            14,
            15,
            tzinfo=timezone(timedelta(hours=-7)),
        )
        task = new_inbox_task("Apply for five companies", now, "a1b2c3")
        page = stored_page(task)
        edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = StatefulTaskRunner(page, [edge])
        metric = ProgressMetric.from_value(
            {
                "kind": "count",
                "label": "Job applications",
                "unit": "job_application",
                "target": 5,
                "current": 0,
                "event_binding": "job_applied",
                "auto_complete": True,
                "task_day": "2026-07-30",
                "timezone": "America/Los_Angeles",
            }
        )
        event_progress = EventProgress()

        receipt = GBrainAdapter(runner).set_task_progress_metric(
            task.slug,
            metric,
            event_progress,
            now,
        )

        self.assertIsNotNone(receipt)
        self.assertTrue(receipt.verified)
        self.assertEqual(receipt.task.progress_metric, metric)
        self.assertEqual(receipt.task.event_progress, event_progress)
        self.assertEqual(runner.page["type"], "task")
        self.assertEqual(runner.page["frontmatter"]["links"], page["frontmatter"]["links"])
        self.assertEqual(
            runner.links,
            [edge],
        )

    def test_rejects_event_metric_for_a_different_task_day_before_write(
        self,
    ) -> None:
        now = datetime(
            2026,
            7,
            30,
            14,
            15,
            tzinfo=timezone(timedelta(hours=-7)),
        )
        task = new_task(
            title="Apply for five companies",
            due_day=date(2026, 7, 30),
            now=now,
            identity="a1b2c3",
        )
        page = stored_page(task)
        edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = StatefulTaskRunner(page, [edge])
        metric = ProgressMetric.from_value(
            {
                "kind": "count",
                "label": "Job applications",
                "unit": "job_application",
                "target": 5,
                "current": 0,
                "event_binding": "job_applied",
                "auto_complete": True,
                "task_day": "2026-07-31",
                "timezone": "America/Los_Angeles",
            }
        )

        with self.assertRaisesRegex(ValueError, "must match the task due day"):
            GBrainAdapter(runner).set_task_progress_metric(
                task.slug,
                metric,
                EventProgress(),
                now,
            )

        self.assertEqual(
            [tool for tool, _params in runner.calls].count("put_page"),
            0,
        )

    def test_five_distinct_events_increment_once_and_then_complete(self) -> None:
        now = datetime(
            2026,
            7,
            30,
            14,
            15,
            tzinfo=timezone(timedelta(hours=-7)),
        )
        metric = ProgressMetric.from_value(
            {
                "kind": "count",
                "label": "Job applications",
                "unit": "job_application",
                "target": 5,
                "current": 0,
                "event_binding": "job_applied",
                "auto_complete": True,
                "task_day": "2026-07-30",
                "timezone": "America/Los_Angeles",
            }
        )
        task = new_task(
            title="Apply for five companies",
            progress_metric=metric,
            event_progress=EventProgress(),
            now=now,
            identity="a1b2c3",
        )
        page = stored_page(task)
        page["frontmatter"]["progress_metric"] = metric.to_dict()
        page["frontmatter"]["event_progress"] = EventProgress().to_dict()
        edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = StatefulTaskRunner(page, [edge])
        adapter = GBrainAdapter(runner)

        for index in range(1, 5):
            receipt = adapter.apply_task_progress_event(
                task.slug,
                event_binding="job_applied",
                evidence_slug=f"applications/{index}",
                receipt_id=f"evt-{index}",
                now=now + timedelta(minutes=index),
            )
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt.task.progress_metric.current, index)
            self.assertEqual(receipt.task.status, "planned")

        writes_before_duplicate = len(
            [tool for tool, _params in runner.calls if tool == "put_page"]
        )
        duplicate = adapter.apply_task_progress_event(
            task.slug,
            event_binding="job_applied",
            evidence_slug="applications/4",
            receipt_id="evt-4",
            now=now + timedelta(minutes=5),
        )
        writes_after_duplicate = len(
            [tool for tool, _params in runner.calls if tool == "put_page"]
        )
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(writes_before_duplicate, writes_after_duplicate)

        completed = adapter.apply_task_progress_event(
            task.slug,
            event_binding="job_applied",
            evidence_slug="applications/5",
            receipt_id="evt-5",
            now=now + timedelta(minutes=6),
        )

        self.assertTrue(completed.verified)
        self.assertFalse(completed.duplicate)
        self.assertEqual(completed.task.progress_metric.current, 5)
        self.assertEqual(completed.task.status, "completed")
        self.assertIsNotNone(completed.task.completed_at)
        self.assertEqual(
            completed.task.event_progress.receipt_ids,
            ("evt-1", "evt-2", "evt-3", "evt-4", "evt-5"),
        )

    def test_seeded_progress_increments_once_and_completes_at_custom_target(self) -> None:
        now = datetime(
            2026,
            7,
            30,
            14,
            15,
            tzinfo=timezone(timedelta(hours=-7)),
        )
        metric = ProgressMetric.from_value(
            {
                "kind": "count",
                "label": "Job applications",
                "unit": "job_application",
                "target": 3,
                "current": 2,
                "event_binding": "job_applied",
                "auto_complete": True,
                "task_day": "2026-07-30",
                "timezone": "America/Los_Angeles",
            }
        )
        progress = EventProgress(baseline_count=2)
        task = new_task(
            title="Apply for more companies",
            progress_metric=metric,
            event_progress=progress,
            now=now,
            identity="a1b2c4",
        )
        page = stored_page(task)
        page["frontmatter"]["progress_metric"] = metric.to_dict()
        page["frontmatter"]["event_progress"] = progress.to_dict()
        edge = {
            "from_slug": task.slug,
            "to_slug": ACTIVE_ROOT,
            "link_type": "member_of",
        }
        runner = StatefulTaskRunner(page, [edge])

        completed = GBrainAdapter(runner).apply_task_progress_event(
            task.slug,
            event_binding="job_applied",
            evidence_slug="applications/new",
            receipt_id="evt-new",
            now=now + timedelta(minutes=1),
        )

        self.assertEqual(completed.task.progress_metric.current, 3)
        self.assertEqual(completed.task.status, "completed")
        self.assertEqual(completed.task.event_progress.baseline_count, 2)
        self.assertEqual(completed.task.event_progress.receipt_ids, ("evt-new",))


class SubprocessRunnerTests(unittest.TestCase):
    @patch("gtasks.gbrain.urlopen")
    def test_remote_http_runner_reuses_one_oauth_token_across_calls(self, open_url) -> None:
        class Response:
            def __init__(self, payload: object, content_type: str = "application/json") -> None:
                self.payload = payload
                self.headers = {"Content-Type": content_type}
                self.status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
                if isinstance(self.payload, str):
                    return self.payload.encode("utf-8")
                return json.dumps(self.payload).encode("utf-8")

        calls: list[tuple[str, str | None]] = []

        def respond(request, **_kwargs):
            calls.append((request.full_url, request.headers.get("Authorization")))
            if request.full_url.endswith("/.well-known/oauth-authorization-server"):
                return Response({"token_endpoint": "https://brain.test/token"})
            if request.full_url == "https://brain.test/token":
                return Response({"access_token": "token-one", "expires_in": 3600})
            return Response(
                "data: "
                + json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "one",
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps({"slug": "tasks/one"}),
                                }
                            ]
                        },
                    }
                )
                + "\n\n",
                "text/event-stream",
            )

        open_url.side_effect = respond
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "remote_mcp": {
                            "issuer_url": "https://brain.test",
                            "mcp_url": "https://brain.test/mcp",
                            "oauth_client_id": "client-one",
                            "oauth_client_secret": "secret-one",
                        }
                    }
                ),
                encoding="utf-8",
            )
            runner = RemoteHttpCommandRunner(config_path=config_path)
            first = runner.run("get_page", {"slug": "tasks/one"})
            second = runner.run("get_page", {"slug": "tasks/one"})

        self.assertEqual(first, {"slug": "tasks/one"})
        self.assertEqual(second, first)
        self.assertEqual(
            sum(url == "https://brain.test/token" for url, _auth in calls),
            1,
        )
        self.assertEqual(
            [auth for url, auth in calls if url == "https://brain.test/mcp"],
            ["Bearer token-one", "Bearer token-one"],
        )

    @patch("gtasks.gbrain.urlopen")
    def test_remote_http_runner_honors_explicit_config_file_env(self, open_url) -> None:
        class Response:
            headers = {"Content-Type": "application/json"}
            status = 200

            def __init__(self, payload: object) -> None:
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
                return json.dumps(self.payload).encode("utf-8")

        def respond(request, **_kwargs):
            if request.full_url.endswith("/.well-known/oauth-authorization-server"):
                return Response({"token_endpoint": "https://brain.test/token"})
            if request.full_url == "https://brain.test/token":
                return Response({"access_token": "token-env", "expires_in": 3600})
            return Response(
                {
                    "jsonrpc": "2.0",
                    "id": "one",
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps({"slug": "tasks/from-env"}),
                            }
                        ]
                    },
                }
            )

        open_url.side_effect = respond
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "remote_mcp": {
                            "issuer_url": "https://brain.test",
                            "mcp_url": "https://brain.test/mcp",
                            "oauth_client_id": "client-env",
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "GBRAIN_CONFIG_FILE": str(config_path),
                    "GBRAIN_REMOTE_CLIENT_SECRET": "secret-env",
                },
                clear=False,
            ):
                result = RemoteHttpCommandRunner().run(
                    "get_page", {"slug": "tasks/from-env"}
                )

        self.assertEqual(result, {"slug": "tasks/from-env"})

    @patch("gtasks.gbrain.urlopen")
    def test_remote_http_runner_reads_credentials_file_env_for_remote_home_config(
        self, open_url
    ) -> None:
        class Response:
            status = 200

            def __init__(self, payload: object) -> None:
                self.payload = payload
                self.headers = {"Content-Type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
                return json.dumps(self.payload).encode("utf-8")

        def respond(request, **_kwargs):
            if request.full_url.endswith("/.well-known/oauth-authorization-server"):
                return Response({"token_endpoint": "https://brain.test/token"})
            if request.full_url == "https://brain.test/token":
                return Response({"access_token": "token-file", "expires_in": 3600})
            return Response(
                {
                    "jsonrpc": "2.0",
                    "id": "one",
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps({"slug": "tasks/from-file"}),
                            }
                        ]
                    },
                }
            )

        open_url.side_effect = respond
        with TemporaryDirectory() as directory:
            home = Path(directory) / "remote-home"
            config_path = home / ".gbrain" / "config.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                json.dumps(
                    {
                        "remote_mcp": {
                            "issuer_url": "https://brain.test",
                            "mcp_url": "https://brain.test/mcp",
                            "oauth_client_id": "client-file",
                        }
                    }
                ),
                encoding="utf-8",
            )
            credentials = home / "credentials.env"
            credentials.write_text(
                "GBRAIN_REMOTE_CLIENT_SECRET=secret-file\n",
                encoding="utf-8",
            )
            os.chmod(credentials, 0o600)
            with patch.dict(
                os.environ,
                {
                    "GBRAIN_HOME": str(home),
                    "GBRAIN_CREDENTIALS_FILE": str(credentials),
                    "GBRAIN_REMOTE_CLIENT_SECRET": "",
                },
                clear=False,
            ):
                os.environ.pop("GBRAIN_CONFIG_FILE", None)
                result = RemoteHttpCommandRunner().run(
                    "get_page", {"slug": "tasks/from-file"}
                )

        self.assertEqual(result, {"slug": "tasks/from-file"})

    @patch("gtasks.gbrain.urlopen")
    def test_remote_http_runner_falls_back_to_dashboard_runtime_config(self, open_url) -> None:
        class Response:
            status = 200

            def __init__(self, payload: object, content_type: str = "application/json") -> None:
                self.payload = payload
                self.headers = {"Content-Type": content_type}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
                if isinstance(self.payload, str):
                    return self.payload.encode("utf-8")
                return json.dumps(self.payload).encode("utf-8")

        seen_auth: list[str | None] = []

        def respond(request, **_kwargs):
            seen_auth.append(request.headers.get("Authorization"))
            if request.full_url.endswith("/.well-known/oauth-authorization-server"):
                return Response({"token_endpoint": "https://brain.test/token"})
            if request.full_url == "https://brain.test/token":
                return Response({"access_token": "token-dashboard", "expires_in": 3600})
            return Response(
                "data: "
                + json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "one",
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps({"slug": "tasks/dashboard"}),
                                }
                            ]
                        },
                    }
                )
                + "\n\n",
                "text/event-stream",
            )

        open_url.side_effect = respond
        with TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            local_config = home / ".gbrain" / "config.json"
            local_config.parent.mkdir(parents=True)
            local_config.write_text(
                json.dumps({"engine": "pglite", "database_path": "local"}),
                encoding="utf-8",
            )
            remote_config = root / "state" / "gtasks-remote" / ".gbrain" / "config.json"
            remote_config.parent.mkdir(parents=True)
            remote_config.write_text(
                json.dumps(
                    {
                        "engine": "remote",
                        "remote_mcp": {
                            "issuer_url": "https://brain.test",
                            "mcp_url": "https://brain.test/mcp",
                            "oauth_client_id": "client-dashboard",
                        },
                    }
                ),
                encoding="utf-8",
            )
            credentials = root / "state" / "gtasks-remote" / "credentials.env"
            credentials.write_text(
                "GBRAIN_REMOTE_CLIENT_SECRET=secret-dashboard\n",
                encoding="utf-8",
            )
            os.chmod(credentials, 0o600)
            (root / "dashboard-integration.json").write_text(
                json.dumps(
                    {
                        "remote_mcp": {
                            "config": str(remote_config),
                            "credentials": str(credentials),
                        }
                    }
                ),
                encoding="utf-8",
            )
            previous_cwd = Path.cwd()
            try:
                os.chdir(root)
                with patch.dict(
                    os.environ,
                    {
                        "HOME": str(home),
                        "GBRAIN_REMOTE_CLIENT_SECRET": "",
                    },
                    clear=False,
                ):
                    os.environ.pop("GBRAIN_HOME", None)
                    os.environ.pop("GBRAIN_CONFIG_FILE", None)
                    result = RemoteHttpCommandRunner().run(
                        "get_page", {"slug": "tasks/dashboard"}
                    )
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(result, {"slug": "tasks/dashboard"})
        self.assertIn("Bearer token-dashboard", seen_auth)

    @patch("gtasks.gbrain.urlopen")
    def test_remote_http_runner_falls_back_to_module_dashboard_runtime_config_outside_repo(
        self, open_url
    ) -> None:
        class Response:
            status = 200

            def __init__(self, payload: object, content_type: str = "application/json") -> None:
                self.payload = payload
                self.headers = {"Content-Type": content_type}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
                if isinstance(self.payload, str):
                    return self.payload.encode("utf-8")
                return json.dumps(self.payload).encode("utf-8")

        seen_auth: list[str | None] = []

        def respond(request, **_kwargs):
            seen_auth.append(request.headers.get("Authorization"))
            if request.full_url.endswith("/.well-known/oauth-authorization-server"):
                return Response({"token_endpoint": "https://brain.test/token"})
            if request.full_url == "https://brain.test/token":
                return Response({"access_token": "token-module", "expires_in": 3600})
            return Response(
                "data: "
                + json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "one",
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps({"slug": "tasks/module"}),
                                }
                            ]
                        },
                    }
                )
                + "\n\n",
                "text/event-stream",
            )

        open_url.side_effect = respond
        with TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            outside = root / "outside"
            module_repo = root / "module-repo"
            local_config = home / ".gbrain" / "config.json"
            local_config.parent.mkdir(parents=True)
            local_config.write_text(
                json.dumps({"engine": "pglite", "database_path": "local"}),
                encoding="utf-8",
            )
            outside.mkdir()
            remote_config = root / "state" / "gtasks-remote" / ".gbrain" / "config.json"
            remote_config.parent.mkdir(parents=True)
            remote_config.write_text(
                json.dumps(
                    {
                        "engine": "remote",
                        "remote_mcp": {
                            "issuer_url": "https://brain.test",
                            "mcp_url": "https://brain.test/mcp",
                            "oauth_client_id": "client-module",
                        },
                    }
                ),
                encoding="utf-8",
            )
            credentials = root / "state" / "gtasks-remote" / "credentials.env"
            credentials.write_text(
                "GBRAIN_REMOTE_CLIENT_SECRET=secret-module\n",
                encoding="utf-8",
            )
            os.chmod(credentials, 0o600)
            module_repo.mkdir()
            (module_repo / "dashboard-integration.json").write_text(
                json.dumps(
                    {
                        "remote_mcp": {
                            "config": str(remote_config),
                            "credentials": str(credentials),
                        }
                    }
                ),
                encoding="utf-8",
            )
            previous_cwd = Path.cwd()
            previous_file = gbrain_module.__file__
            try:
                os.chdir(outside)
                gbrain_module.__file__ = str(module_repo / "gtasks" / "gbrain.py")
                with patch.dict(
                    os.environ,
                    {
                        "HOME": str(home),
                        "GBRAIN_REMOTE_CLIENT_SECRET": "",
                    },
                    clear=False,
                ):
                    os.environ.pop("GBRAIN_HOME", None)
                    os.environ.pop("GBRAIN_CONFIG_FILE", None)
                    result = RemoteHttpCommandRunner().run(
                        "get_page", {"slug": "tasks/module"}
                    )
            finally:
                gbrain_module.__file__ = previous_file
                os.chdir(previous_cwd)

        self.assertEqual(result, {"slug": "tasks/module"})
        self.assertIn("Bearer token-module", seen_auth)

    def test_remote_http_runner_rejects_dashboard_secret_with_wrong_mode(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            local_config = home / ".gbrain" / "config.json"
            local_config.parent.mkdir(parents=True)
            local_config.write_text(
                json.dumps({"engine": "pglite", "database_path": "local"}),
                encoding="utf-8",
            )
            remote_config = root / "state" / "gtasks-remote" / ".gbrain" / "config.json"
            remote_config.parent.mkdir(parents=True)
            remote_config.write_text(
                json.dumps(
                    {
                        "remote_mcp": {
                            "issuer_url": "https://brain.test",
                            "mcp_url": "https://brain.test/mcp",
                            "oauth_client_id": "client-dashboard",
                        }
                    }
                ),
                encoding="utf-8",
            )
            credentials = root / "state" / "gtasks-remote" / "credentials.env"
            credentials.write_text(
                "GBRAIN_REMOTE_CLIENT_SECRET=secret-dashboard\n",
                encoding="utf-8",
            )
            os.chmod(credentials, 0o644)
            (root / "dashboard-integration.json").write_text(
                json.dumps(
                    {
                        "remote_mcp": {
                            "config": str(remote_config),
                            "credentials": str(credentials),
                        }
                    }
                ),
                encoding="utf-8",
            )
            previous_cwd = Path.cwd()
            try:
                os.chdir(root)
                with patch.dict(os.environ, {"HOME": str(home)}, clear=False):
                    os.environ.pop("GBRAIN_HOME", None)
                    os.environ.pop("GBRAIN_CONFIG_FILE", None)
                    os.environ.pop("GBRAIN_REMOTE_CLIENT_SECRET", None)
                    with self.assertRaisesRegex(
                        GBrainCommandError,
                        "permissions are invalid",
                    ):
                        RemoteHttpCommandRunner().run(
                            "get_page", {"slug": "tasks/dashboard"}
                        )
            finally:
                os.chdir(previous_cwd)

    @patch("gtasks.gbrain.subprocess.run")
    def test_retries_one_read_after_serialized_oauth_refresh_failure(self, run) -> None:
        run.side_effect = [
            subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr="Auth failed after token refresh. Verify oauth_client_id and secret.",
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout='{"slug":"tasks/recovered"}',
                stderr="",
            ),
        ]

        result = SubprocessCommandRunner().run(
            "get_page", {"slug": "tasks/recovered"}
        )

        self.assertEqual(result, {"slug": "tasks/recovered"})
        self.assertEqual(run.call_count, 2)

    @patch("gtasks.gbrain.sleep")
    @patch("gtasks.gbrain.subprocess.run")
    def test_retries_second_read_after_persistent_refresh_race(self, run, sleep) -> None:
        auth_failure = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="Auth failed after token refresh. Verify oauth_client_id and secret.",
        )
        run.side_effect = [
            auth_failure,
            auth_failure,
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout='{"slug":"tasks/recovered-after-cooldown"}',
                stderr="",
            ),
        ]

        result = SubprocessCommandRunner().run(
            "get_page", {"slug": "tasks/recovered-after-cooldown"}
        )

        self.assertEqual(result, {"slug": "tasks/recovered-after-cooldown"})
        self.assertEqual(run.call_count, 3)
        sleep.assert_called_once_with(0.5)

    @patch("gtasks.gbrain.subprocess.run")
    def test_does_not_retry_write_after_oauth_refresh_failure(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="Auth failed after token refresh. Verify oauth_client_id and secret.",
        )

        with self.assertRaisesRegex(GBrainCommandError, "Auth failed"):
            SubprocessCommandRunner().run(
                "put_page", {"slug": "tasks/write", "content": "---\ntype: task\n---"}
            )

        self.assertEqual(run.call_count, 1)

    @patch("gtasks.gbrain.subprocess.run")
    def test_bounds_cli_calls_to_avoid_oauth_token_bursts(self, run) -> None:
        active = 0
        maximum = 0
        lock = threading.Lock()

        def invoke(*_args, **_kwargs):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout='{"ok":true}', stderr=""
            )

        run.side_effect = invoke
        runner = SubprocessCommandRunner()
        with ThreadPoolExecutor(max_workers=3) as executor:
            results = list(
                executor.map(
                    lambda index: runner.run("get_page", {"slug": f"tasks/{index}"}),
                    range(3),
                )
            )

        self.assertEqual(results, [{"ok": True}, {"ok": True}, {"ok": True}])
        self.assertEqual(maximum, 1)

    @patch("gtasks.gbrain.subprocess.run")
    def test_foreground_operation_preempts_background_refresh_between_calls(self, run) -> None:
        first_background_started = threading.Event()
        release_first_background = threading.Event()
        order: list[str] = []
        order_lock = threading.Lock()

        def invoke(command, **_kwargs):
            slug = json.loads(command[-1])["slug"]
            with order_lock:
                order.append(slug)
            if slug == "tasks/background-one":
                first_background_started.set()
                release_first_background.wait(timeout=1)
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout='{"ok":true}', stderr=""
            )

        run.side_effect = invoke
        runner = SubprocessCommandRunner()

        def refresh() -> None:
            runner.run("get_page", {"slug": "tasks/background-one"})
            runner.run("get_page", {"slug": "tasks/background-two"})

        background = threading.Thread(
            target=refresh,
            name="gtasks-tasks-refresh",
        )
        background.start()
        self.assertTrue(first_background_started.wait(timeout=1))
        with runner.foreground_operation():
            release_first_background.set()
            runner.run("get_page", {"slug": "tasks/foreground"})
        background.join(timeout=1)

        self.assertFalse(background.is_alive())
        self.assertEqual(
            order,
            ["tasks/background-one", "tasks/foreground", "tasks/background-two"],
        )

    @patch("gtasks.gbrain.subprocess.run")
    def test_invokes_gbrain_without_a_shell(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"slug": ACTIVE_ROOT}),
            stderr="",
        )

        result = SubprocessCommandRunner().run("get_page", {"slug": ACTIVE_ROOT})

        self.assertEqual(result, {"slug": ACTIVE_ROOT})
        positional, keyword = run.call_args
        self.assertEqual(
            positional[0],
            [
                "gbrain",
                "call",
                "get_page",
                json.dumps({"slug": ACTIVE_ROOT}, separators=(",", ":")),
            ],
        )
        self.assertNotIn("shell", keyword)


if __name__ == "__main__":
    unittest.main()
