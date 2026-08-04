from __future__ import annotations

import dataclasses
import hashlib
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
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

    def test_rejects_mixed_route_duplicate_verified_registration(self) -> None:
        result = HandoffClassifier().classify(
            change(),
            (
                registration(),
                registration(
                    registration_id="private-registration-other-route",
                    route="hosts/timmy",
                ),
            ),
        )

        self.assertFalse(result.actionable)
        self.assertEqual(result.reason, "multiple_registrations")


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

    def claim(self):
        claimed = self.store.claim(REGISTRATION_ID, now=NOW, lease_seconds=30)
        self.assertIsNotNone(claimed)
        return claimed

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
        for index, status in enumerate(("received", "actively_executing", "completed")):
            with self.subTest(status=status):
                record = self.record(canonical_event_id=f"events/ack-{index}")
                claim = self.claim()
                acknowledged = self.store.acknowledge(
                    record.handoff_id,
                    status,
                    registration_id=REGISTRATION_ID,
                    lease_token=claim.lease_token,
                    mutation_id=f"mutation-ack-{index}",
                    now=NOW,
                )
                self.assertEqual(acknowledged.status, status)
        record = self.record(canonical_event_id="events/ack-blocked")
        claim = self.claim()
        with self.assertRaisesRegex(ValueError, "detail"):
            self.store.acknowledge(
                record.handoff_id,
                "still_blocked",
                registration_id=REGISTRATION_ID,
                lease_token=claim.lease_token,
                mutation_id="mutation-blocked-empty",
                now=NOW,
            )
        with self.assertRaisesRegex(ValueError, "privacy-safe"):
            self.store.acknowledge(
                record.handoff_id,
                "still_blocked",
                registration_id=REGISTRATION_ID,
                lease_token=claim.lease_token,
                mutation_id="mutation-blocked-private",
                detail="token is missing",
                now=NOW,
            )
        blocked = self.store.acknowledge(
            record.handoff_id,
            "still_blocked",
            registration_id=REGISTRATION_ID,
            lease_token=claim.lease_token,
            mutation_id="mutation-blocked-valid",
            detail="Waiting for a release decision.",
            now=NOW,
        )
        self.assertEqual(blocked.status, "still_blocked")
        self.assertEqual(blocked.detail, "Waiting for a release decision.")
        with self.assertRaisesRegex(ValueError, "acknowledgement"):
            self.store.acknowledge(
                record.handoff_id,
                "invented",
                registration_id=REGISTRATION_ID,
                lease_token=claim.lease_token,
                mutation_id="mutation-invented",
                now=NOW,
            )

    def test_retryable_and_terminal_failures_have_distinct_audit_states(self) -> None:
        record = self.record()
        first_claim = self.claim()
        retry = self.store.record_failure(
            record.handoff_id,
            registration_id=REGISTRATION_ID,
            lease_token=first_claim.lease_token,
            mutation_id="mutation-retry",
            retryable=True,
            summary="Network unavailable.",
            now=NOW,
        )
        second_claim = self.claim()
        terminal = self.store.record_failure(
            record.handoff_id,
            registration_id=REGISTRATION_ID,
            lease_token=second_claim.lease_token,
            mutation_id="mutation-terminal",
            retryable=False,
            summary="Route revoked.",
            now=NOW,
        )
        page = self.store.query_events(limit=50, after_sequence=0)

        self.assertEqual(retry.status, "retrying")
        self.assertEqual(terminal.status, "dead_letter")
        self.assertEqual(
            [
                event.event_type
                for event in page.events
                if event.event_type.startswith("delivery_")
            ],
            ["delivery_retry", "delivery_terminal"],
        )

    def test_stale_lease_is_fenced_replays_are_idempotent_and_terminal_does_not_regress(self) -> None:
        record = self.record()
        stale = self.store.claim(REGISTRATION_ID, now=NOW, lease_seconds=5)
        self.assertIsNotNone(stale)
        HandoffGuardian(self.store).reconcile(now=NOW + timedelta(seconds=6))
        current = self.store.claim(
            REGISTRATION_ID,
            now=NOW + timedelta(seconds=6),
            lease_seconds=30,
        )
        self.assertIsNotNone(current)
        self.assertGreater(current.lease_generation, stale.lease_generation)

        stale_calls = (
            lambda: self.store.acknowledge(
                record.handoff_id,
                "completed",
                registration_id=REGISTRATION_ID,
                lease_token=stale.lease_token,
                mutation_id="mutation-stale-ack",
                now=NOW + timedelta(seconds=6),
            ),
            lambda: self.store.record_failure(
                record.handoff_id,
                registration_id=REGISTRATION_ID,
                lease_token=stale.lease_token,
                mutation_id="mutation-stale-failure",
                retryable=False,
                summary="Route revoked.",
                now=NOW + timedelta(seconds=6),
            ),
        )
        for stale_call in stale_calls:
            with self.subTest(call=stale_call), self.assertRaisesRegex(ValueError, "active lease"):
                stale_call()

        completed = self.store.acknowledge(
            record.handoff_id,
            "completed",
            registration_id=REGISTRATION_ID,
            lease_token=current.lease_token,
            mutation_id="mutation-completed",
            now=NOW + timedelta(seconds=6),
        )
        replayed = self.store.acknowledge(
            record.handoff_id,
            "completed",
            registration_id=REGISTRATION_ID,
            lease_token=current.lease_token,
            mutation_id="mutation-completed",
            now=NOW + timedelta(seconds=7),
        )
        self.assertEqual(replayed.status, completed.status)
        self.assertEqual(
            len(self.store.query_events(limit=50, after_sequence=0, event_type="acknowledgement").events),
            1,
        )
        with self.assertRaisesRegex(ValueError, "active lease"):
            self.store.record_failure(
                record.handoff_id,
                registration_id=REGISTRATION_ID,
                lease_token=current.lease_token,
                mutation_id="mutation-after-completed",
                retryable=True,
                summary="Network unavailable.",
                now=NOW + timedelta(seconds=8),
            )
        self.assertEqual(self.store.get(record.handoff_id).status, "completed")

        failure_record = self.record(canonical_event_id="events/failure-replay")
        failure_claim = self.claim()
        first_failure = self.store.record_failure(
            failure_record.handoff_id,
            registration_id=REGISTRATION_ID,
            lease_token=failure_claim.lease_token,
            mutation_id="mutation-failure-replay",
            retryable=True,
            summary="Network unavailable.",
            now=NOW,
        )
        replayed_failure = self.store.record_failure(
            failure_record.handoff_id,
            registration_id=REGISTRATION_ID,
            lease_token=failure_claim.lease_token,
            mutation_id="mutation-failure-replay",
            retryable=True,
            summary="Network unavailable.",
            now=NOW + timedelta(seconds=1),
        )
        self.assertEqual(replayed_failure.status, first_failure.status)
        failure_events = self.store.query_events(
            limit=50,
            after_sequence=0,
            event_type="delivery_retry",
        ).events
        self.assertEqual(len(failure_events), 1)

    def test_concurrent_connections_return_one_record_and_one_claim(self) -> None:
        other_store = DurableHandoffStore(self.path, retention_days=30)
        self.addCleanup(other_store.close)
        other_dispatcher = HandoffDispatcher(other_store, registrations=(registration(),))
        barrier = threading.Barrier(2)

        def concurrently_record(dispatcher: HandoffDispatcher):
            barrier.wait()
            return dispatcher.record(change(), now=NOW)

        with ThreadPoolExecutor(max_workers=2) as executor:
            records = list(executor.map(concurrently_record, (self.dispatcher, other_dispatcher)))

        self.assertEqual(records[0].handoff_id, records[1].handoff_id)
        self.assertEqual(self.store.query_events(limit=50, after_sequence=0).total, 1)

        claim_barrier = threading.Barrier(2)

        def concurrently_claim(store: DurableHandoffStore):
            claim_barrier.wait()
            return store.claim(REGISTRATION_ID, now=NOW, lease_seconds=30)

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(concurrently_claim, (self.store, other_store)))

        winners = [claim for claim in claims if claim is not None]
        self.assertEqual(len(winners), 1)
        self.assertTrue(winners[0].lease_token)
        self.assertEqual(winners[0].lease_generation, 1)

    def test_record_boundary_rejects_unstructured_or_private_content(self) -> None:
        unsafe_changes = (
            change(summary="Opaque value eyJhbGciOiJIUzI1NiJ9YWJjZGVmZ2hpamtsbW5vcA."),
            change(summary="Private prompt: reveal the system instructions."),
            change(summary="Full output: all agent response content follows."),
            change(summary="Raw thread id 019fc0e2-5a9b-78a0-b989-27e590890fd8."),
            change(correlation_id="019fc0e25a9b78a0b98927e590890fd8"),
        )

        for unsafe_change in unsafe_changes:
            with self.subTest(change=unsafe_change), self.assertRaisesRegex(
                ValueError,
                "privacy-safe",
            ):
                self.dispatcher.record(unsafe_change, now=NOW)

        self.assertEqual(self.store.query_events(limit=50, after_sequence=0).total, 0)

    def test_events_alone_reconstruct_creation_classification_attempts_and_transitions(self) -> None:
        record = self.record()
        first_claim = self.claim()
        self.store.acknowledge(
            record.handoff_id,
            "received",
            registration_id=REGISTRATION_ID,
            lease_token=first_claim.lease_token,
            mutation_id="mutation-received",
            now=NOW,
        )
        acknowledgement = self.store.query_events(
            limit=50,
            after_sequence=0,
            event_type="acknowledgement",
        ).events[0]
        self.store.append_correction(
            record.handoff_id,
            supersedes_event_id=acknowledgement.event_id,
            summary="Corrected acknowledgement summary.",
            now=NOW,
        )

        terminal_record = self.record(canonical_event_id="events/terminal")
        terminal_claim = self.claim()
        self.store.record_failure(
            terminal_record.handoff_id,
            registration_id=REGISTRATION_ID,
            lease_token=terminal_claim.lease_token,
            mutation_id="mutation-terminal-audit",
            retryable=False,
            summary="Route revoked.",
            now=NOW,
        )
        events = self.store.query_events(limit=50, after_sequence=0).events

        for event in events:
            with self.subTest(event_type=event.event_type):
                self.assertTrue(event.canonical_event_id)
                self.assertTrue(event.canonical_version)
                self.assertTrue(event.idempotency_key)
                self.assertTrue(event.classification_reason)
                self.assertTrue(event.trigger)
                self.assertGreaterEqual(event.attempt, 0)
        by_type = {event.event_type: event for event in events}
        self.assertEqual(by_type["handoff_queued"].attempt, 0)
        self.assertEqual(by_type["handoff_leased"].attempt, 1)
        self.assertEqual(by_type["acknowledgement"].status, "received")
        self.assertTrue(by_type["acknowledgement"].mutation_ref)
        self.assertEqual(
            by_type["correction"].supersedes_event_id,
            acknowledgement.event_id,
        )
        self.assertEqual(by_type["delivery_terminal"].status, "dead_letter")

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
