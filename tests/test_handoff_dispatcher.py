from __future__ import annotations

import dataclasses
import hashlib
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from gtasks.handoff_dispatcher import (
    ActionableChange,
    AgentRegistration,
    DurableHandoffStore,
    HandoffClassifier,
    HandoffDispatcher,
    HandoffGuardian,
    LocalAgentDispatcher,
)


NOW = datetime(2026, 8, 4, 17, 0, tzinfo=timezone.utc)
TASK = "tasks/11111111-1111-4111-8111-111111111111"
AGENT = "agents/tammy"
REGISTRATION_ID = "private-registration-tammy"


def registration(**overrides: object) -> AgentRegistration:
    values: dict[str, object] = {
        "registration_id": REGISTRATION_ID,
        "agent_slug": AGENT,
        "route": "hosts/tammy",
        "verified": True,
    }
    values.update(overrides)
    return AgentRegistration(**values)


def change(**overrides: object) -> ActionableChange:
    values: dict[str, object] = {
        "task_slug": TASK,
        "canonical_event_id": "events/100",
        "canonical_version": "42",
        "trigger": "answer_received",
        "assigned_to": (AGENT,),
        "route": "hosts/tammy",
        "summary": "A verified answer is ready.",
        "occurred_at": NOW,
        "correlation_id": "correlation-100",
        "blocker": None,
    }
    values.update(overrides)
    return ActionableChange(**values)


class HandoffClassifierTests(unittest.TestCase):
    def test_classifies_every_actionable_trigger(self) -> None:
        classifier = HandoffClassifier()
        triggers = (
            "answer_received",
            "todo_added",
            "todo_materially_changed",
            "task_activated",
            "blocker_resolved",
            "system_dependency_recovered",
            "authorization_granted",
            "ownership_changed",
        )

        for trigger in triggers:
            with self.subTest(trigger=trigger):
                result = classifier.classify(change(trigger=trigger), (registration(),))
                self.assertTrue(result.actionable)
                self.assertEqual(result.reason, trigger)
                self.assertEqual(result.registration_ref, registration().reference)
                rendered = dataclasses.asdict(result)
                self.assertNotIn("registration_id", rendered)
                self.assertNotIn(REGISTRATION_ID, repr(rendered))

    def test_suppresses_every_non_actionable_change(self) -> None:
        classifier = HandoffClassifier()
        suppressions = {
            "presentation_only": "presentation_only",
            "duplicate_save": "duplicate_save",
            "derived_count": "derived_count",
            "stale_cache_refresh": "stale_cache_refresh",
            "unchanged_blocker": "stable_blocker",
            "stable_blocker": "stable_blocker",
        }

        for trigger, reason in suppressions.items():
            with self.subTest(trigger=trigger):
                result = classifier.classify(change(trigger=trigger), (registration(),))
                self.assertFalse(result.actionable)
                self.assertEqual(result.reason, reason)

    def test_suppresses_missing_multiple_and_exact_route_mismatch(self) -> None:
        classifier = HandoffClassifier()
        cases = (
            ((), "missing_registration"),
            ((registration(), registration(registration_id="private-registration-2")), "multiple_registrations"),
            ((registration(route="hosts/timmy"),), "route_mismatch"),
            ((registration(verified=False),), "missing_registration"),
            ((registration(),), "missing_assignment"),
            ((registration(),), "multiple_assignments"),
        )
        changes = (
            change(),
            change(),
            change(),
            change(),
            change(assigned_to=()),
            change(assigned_to=(AGENT, "agents/timmy")),
        )

        for action, (registrations, reason) in zip(changes, cases, strict=True):
            result = classifier.classify(action, registrations)
            self.assertFalse(result.actionable)
            self.assertEqual(result.reason, reason)


class DurableHandoffStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        handle, self.path = tempfile.mkstemp(prefix="handoff-dispatcher-", suffix=".sqlite3")
        os.close(handle)
        self.store = DurableHandoffStore(self.path, retention_days=30)
        self.dispatcher = HandoffDispatcher(self.store, registrations=(registration(),))

    def tearDown(self) -> None:
        self.store.close()
        os.unlink(self.path)

    def record(self, **overrides: object):
        return self.dispatcher.record(change(**overrides), now=NOW)

    def test_persists_idempotent_outbox_and_redacted_audit_projection(self) -> None:
        first = self.record()
        duplicate = self.record()
        page = self.store.query_events(limit=50, after_sequence=0)

        self.assertEqual(first.handoff_id, duplicate.handoff_id)
        self.assertEqual(first.status, "queued")
        self.assertEqual(page.total, 1)
        expected_ref = hashlib.sha256(REGISTRATION_ID.encode()).hexdigest()
        for value in (first.to_dict(), page.to_dict(), dataclasses.asdict(page.events[0])):
            rendered = repr(value)
            self.assertNotIn(REGISTRATION_ID, rendered)
            self.assertNotIn("thread_ref", rendered)
        self.assertEqual(first.to_dict()["registration_ref"], expected_ref)
        self.assertEqual(dataclasses.asdict(page.events[0])["registration_ref"], expected_ref)

    def test_idempotency_includes_canonical_version_and_event_id(self) -> None:
        first = self.record(canonical_event_id="events/shared", canonical_version="42")
        second = self.record(canonical_event_id="events/shared", canonical_version="43")

        self.assertNotEqual(first.handoff_id, second.handoff_id)
        self.assertEqual(self.store.query_events(limit=50, after_sequence=0).total, 2)

    def test_records_suppression_without_creating_a_lease(self) -> None:
        record = self.record(trigger="stable_blocker", blocker="waiting on approval")
        self.assertEqual(record.status, "suppressed")
        self.assertEqual(record.reason, "stable_blocker")
        self.assertIsNone(self.store.claim(REGISTRATION_ID, now=NOW, lease_seconds=30))

    def test_acknowledgement_states_validate_blocked_detail(self) -> None:
        record = self.record()
        for status in ("received", "actively_executing", "completed"):
            with self.subTest(status=status):
                acknowledged = self.store.acknowledge(record.handoff_id, status, now=NOW)
                self.assertEqual(acknowledged.status, status)
        with self.assertRaisesRegex(ValueError, "detail"):
            self.store.acknowledge(record.handoff_id, "still_blocked", now=NOW)
        with self.assertRaisesRegex(ValueError, "privacy-safe"):
            self.store.acknowledge(
                record.handoff_id,
                "still_blocked",
                detail="token is missing",
                now=NOW,
            )
        blocked = self.store.acknowledge(
            record.handoff_id,
            "still_blocked",
            detail="Waiting for a release decision.",
            now=NOW,
        )
        self.assertEqual(blocked.status, "still_blocked")
        self.assertEqual(blocked.detail, "Waiting for a release decision.")
        with self.assertRaisesRegex(ValueError, "acknowledgement"):
            self.store.acknowledge(record.handoff_id, "invented", now=NOW)

    def test_retryable_and_terminal_failures_have_distinct_audit_states(self) -> None:
        record = self.record()
        retry = self.store.record_failure(record.handoff_id, retryable=True, summary="network unavailable", now=NOW)
        terminal = self.store.record_failure(record.handoff_id, retryable=False, summary="route revoked", now=NOW)
        page = self.store.query_events(limit=50, after_sequence=0)

        self.assertEqual(retry.status, "retrying")
        self.assertEqual(terminal.status, "dead_letter")
        self.assertEqual([event.event_type for event in page.events[-2:]], ["delivery_retry", "delivery_terminal"])

    def test_claim_is_atomic_and_guardian_recovers_an_expired_lease(self) -> None:
        record = self.record()
        first = self.store.claim(REGISTRATION_ID, now=NOW, lease_seconds=5)
        second = self.store.claim(REGISTRATION_ID, now=NOW, lease_seconds=5)
        recovered = HandoffGuardian(self.store).reconcile(now=NOW + timedelta(seconds=6))

        self.assertEqual(first.handoff_id, record.handoff_id)
        self.assertIsNone(second)
        self.assertEqual(recovered, 1)
        self.assertEqual(self.store.get(record.handoff_id).status, "retrying")

    def test_local_dispatcher_claims_only_its_exact_registration_and_acknowledges_received(self) -> None:
        record = self.record()
        dispatcher = LocalAgentDispatcher(
            self.store,
            registration_id=REGISTRATION_ID,
            verify_route=lambda claimed: claimed.agent_slug == AGENT,
            wake=lambda claimed: True,
        )
        claimed = dispatcher.run_once(now=NOW)

        self.assertEqual(claimed.handoff_id, record.handoff_id)
        self.assertEqual(self.store.get(record.handoff_id).status, "received")

    def test_rejects_naive_clock_and_preserves_sequence_for_skewed_and_duplicate_timestamps(self) -> None:
        with self.assertRaisesRegex(ValueError, "UTC-aware"):
            self.dispatcher.record(change(occurred_at=datetime(2026, 8, 4, 10, 0)), now=NOW)
        with self.assertRaisesRegex(ValueError, "UTC-aware"):
            self.dispatcher.record(change(), now=datetime(2026, 8, 4, 17, 0))
        self.record(canonical_event_id="events/skew", occurred_at=NOW + timedelta(days=2))
        self.record(canonical_event_id="events/same", occurred_at=NOW + timedelta(days=2))
        page = self.store.query_events(limit=50, after_sequence=0)

        self.assertEqual([event.sequence for event in page.events], [1, 2])
        self.assertEqual(page.events[0].occurred_at, page.events[1].occurred_at)

    def test_filters_pagination_and_end_cursor_semantics(self) -> None:
        self.record(canonical_event_id="events/1", correlation_id="corr-a")
        self.record(canonical_event_id="events/2", correlation_id="corr-b", trigger="todo_added")
        self.record(canonical_event_id="events/3", correlation_id="corr-a", trigger="presentation_only")
        self.record(canonical_event_id="events/4", correlation_id="corr-a", trigger="todo_added")

        first = self.store.query_events(limit=2, after_sequence=0, correlation_id="corr-a")
        final = self.store.query_events(limit=2, after_sequence=first.next_sequence, correlation_id="corr-a")
        filtered = self.store.query_events(
            limit=50,
            after_sequence=0,
            task_slug=TASK,
            agent_slug=AGENT,
            status="queued",
            event_type="handoff_queued",
            correlation_id="corr-b",
        )

        self.assertEqual(first.total, 3)
        self.assertEqual(len(first.events), 2)
        self.assertEqual(first.next_sequence, first.events[-1].sequence)
        self.assertIsNone(final.next_sequence)
        self.assertNotIn("next_sequence", final.to_dict())
        self.assertEqual(len(final.events), 1)
        self.assertIsNone(final.next_sequence)
        self.assertEqual([event.correlation_id for event in filtered.events], ["corr-b"])

    def test_correction_is_append_only_and_export_metadata_declares_retention(self) -> None:
        record = self.record()
        initial = self.store.query_events(limit=50, after_sequence=0).events[0]
        correction = self.store.append_correction(
            record.handoff_id,
            supersedes_event_id=initial.event_id,
            summary="Corrected privacy-safe summary.",
            now=NOW,
        )
        page = self.store.query_events(limit=50, after_sequence=0)
        export = self.store.export_events(limit=50, after_sequence=0)

        self.assertEqual(len(page.events), 2)
        self.assertEqual(correction.event_type, "correction")
        self.assertEqual(correction.supersedes_event_id, initial.event_id)
        self.assertEqual(export["metadata"]["retention_days"], 30)
        self.assertEqual(export["metadata"]["format"], "handoff-audit-v1")

    def test_reopens_durably_after_restart(self) -> None:
        record = self.record()
        self.store.close()
        self.store = DurableHandoffStore(self.path, retention_days=30)

        restored = self.store.get(record.handoff_id)
        page = self.store.query_events(limit=50, after_sequence=0)
        self.assertEqual(restored.handoff_id, record.handoff_id)
        self.assertEqual(page.total, 1)


if __name__ == "__main__":
    unittest.main()
