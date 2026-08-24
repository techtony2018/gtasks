from __future__ import annotations

import dataclasses
import hashlib
import inspect
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from gtasks.handoff_dispatcher import (
    ActionableChange,
    AgentRegistration,
    DurableHandoffStore,
    ExecutionClaim,
    ExecutionStartGrant,
    HandoffClassifier,
    HandoffDispatcher,
    HandoffGuardian,
    LocalAgentDispatcher,
)
from gtasks.delegation import AgentDelegationLease, DelegationState
from gtasks.gbrain import CanonicalHandoffEventBridge


NOW = datetime(2026, 8, 4, 17, 0, tzinfo=timezone.utc)
TASK = "tasks/11111111-1111-4111-8111-111111111111"
AGENT = "agents/tammy"
REGISTRATION_ID = "private-registration-tammy"
OC_AGENT = "agents/tammy-oc"
OC_REGISTRATION_ID = "private-registration-tammy-oc"
DELEGATION_SLUG = "agent-delegations/22222222-2222-4222-8222-222222222222"


def registration(**overrides: object) -> AgentRegistration:
    values: dict[str, object] = {
        "registration_id": REGISTRATION_ID,
        "agent_slug": AGENT,
        "route": "hosts/tammy",
        "verified": True,
    }
    values.update(overrides)
    return AgentRegistration(**values)


def oc_registration(**overrides: object) -> AgentRegistration:
    values: dict[str, object] = {
        "registration_id": OC_REGISTRATION_ID,
        "agent_slug": OC_AGENT,
        "route": "hosts/tammy",
        "verified": True,
    }
    values.update(overrides)
    return AgentRegistration(**values)


def delegation(**overrides: object) -> AgentDelegationLease:
    values: dict[str, object] = {
        "slug": DELEGATION_SLUG,
        "source_agent": AGENT,
        "executor_agent": OC_AGENT,
        "authorized_by": "people/tony-guan",
        "starts_at": NOW - timedelta(minutes=15),
        "ends_at": NOW + timedelta(minutes=45),
        "display_timezone": "America/Los_Angeles",
        "allowed_operations": ("task_status", "todo", "comment", "artifact"),
        "state": DelegationState.ACTIVE,
        "created_at": NOW - timedelta(minutes=20),
        "updated_at": NOW - timedelta(minutes=20),
    }
    values.update(overrides)
    return AgentDelegationLease(**values)


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
        "task_status": "planned",
        "requested_operation": "todo",
    }
    values.update(overrides)
    return ActionableChange(**values)


class HandoffClassifierTests(unittest.TestCase):
    def test_reference_backed_registration_preserves_direct_registration_semantics(self) -> None:
        direct = registration()
        reference = direct.reference
        factory = getattr(AgentRegistration, "from_reference", None)
        self.assertTrue(callable(factory), "reference-backed registration factory is missing")

        runtime = factory(
            reference,
            agent_slug=AGENT,
            route="hosts/tammy",
        )

        self.assertEqual(direct.lease_identity, REGISTRATION_ID)
        self.assertEqual(direct.reference, reference)
        self.assertEqual(runtime.registration_id, reference)
        self.assertEqual(runtime.lease_identity, reference)
        self.assertEqual(runtime.reference, reference)

    def test_classifies_every_actionable_trigger(self) -> None:
        classifier = HandoffClassifier()
        triggers = (
            "answer_received",
            "tony_answer_received",
            "waiting_for_information_updated",
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
            "tony_owned_no_agent": "tony_owned_no_agent",
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


class HandoffDispatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        handle, self.path = tempfile.mkstemp(
            prefix="handoff-execution-claim-", suffix=".sqlite3"
        )
        os.close(handle)
        self.store = DurableHandoffStore(self.path, retention_days=30)

    def tearDown(self) -> None:
        self.store.close()
        os.unlink(self.path)

    def dispatcher(
        self,
        *,
        leases: tuple[AgentDelegationLease, ...] = (),
        owned_work_ready=lambda _executor: False,
        registrations: tuple[AgentRegistration, ...] | None = None,
    ) -> HandoffDispatcher:
        return HandoffDispatcher(
            self.store,
            registrations=registrations or (registration(), oc_registration()),
            delegations=leases,
            owned_work_ready=owned_work_ready,
        )

    @staticmethod
    def task(index: int) -> str:
        return f"tasks/{index:08d}-1111-4111-8111-{index:012d}"

    def test_owned_openclaw_work_routes_directly_with_a_task_claim(self) -> None:
        actionable = change(
            assigned_to=(OC_AGENT,),
            canonical_event_id="events/owned-oc",
            correlation_id="correlation-owned-oc",
        )

        record = self.dispatcher().record(actionable, now=NOW)
        claim = self.store.get_execution_claim(TASK)

        self.assertEqual(actionable.assigned_to, (OC_AGENT,))
        self.assertEqual(record.agent_slug, OC_AGENT)
        self.assertEqual(record.executor_agent, OC_AGENT)
        self.assertEqual(record.permanent_owner, OC_AGENT)
        self.assertIsNone(record.delegation_slug)
        self.assertIsInstance(claim, ExecutionClaim)
        self.assertEqual(claim.executor_agent, OC_AGENT)
        self.assertEqual(claim.permanent_owner, OC_AGENT)
        self.assertIsNone(claim.delegation_slug)
        self.assertIsNone(
            self.store.claim(REGISTRATION_ID, now=NOW, lease_seconds=30)
        )
        self.assertEqual(
            self.store.claim(OC_REGISTRATION_ID, now=NOW, lease_seconds=30).task_slug,
            TASK,
        )

    def test_owned_claim_release_suppresses_its_pending_handoff(self) -> None:
        record = self.dispatcher().record(
            change(
                assigned_to=(OC_AGENT,),
                canonical_event_id="events/owned-release",
            ),
            now=NOW,
        )
        claim = self.store.get_execution_claim(TASK)

        event = self.store.release_execution_claim(
            TASK,
            executor_agent=OC_AGENT,
            idempotency_key=claim.idempotency_key,
            terminal_state="completed",
            mutation_id="mutation-owned-release",
            now=NOW + timedelta(seconds=1),
        )

        self.assertEqual(event.event_type, "execution_claim_released")
        self.assertEqual(self.store.get(record.handoff_id).status, "suppressed")
        self.assertIsNone(
            self.store.claim(
                OC_REGISTRATION_ID,
                now=NOW + timedelta(seconds=2),
                lease_seconds=30,
            )
        )

    def test_active_verified_lease_routes_without_rewriting_permanent_owner(self) -> None:
        active = delegation()
        actionable = change(canonical_event_id="events/delegated-active")

        record = self.dispatcher(leases=(active,)).record(actionable, now=NOW)
        claim = self.store.get_execution_claim(TASK)

        self.assertEqual(actionable.assigned_to, (AGENT,))
        self.assertEqual(record.agent_slug, OC_AGENT)
        self.assertEqual(record.executor_agent, OC_AGENT)
        self.assertEqual(record.permanent_owner, AGENT)
        self.assertEqual(record.delegation_slug, active.slug)
        self.assertEqual(claim.delegation_slug, active.slug)
        self.assertEqual(claim.expires_at, active.ends_at)
        self.assertEqual(claim.requested_operation, "todo")
        self.assertIsNone(
            self.store.claim(REGISTRATION_ID, now=NOW, lease_seconds=30)
        )
        self.assertEqual(
            self.store.claim(OC_REGISTRATION_ID, now=NOW, lease_seconds=30).task_slug,
            TASK,
        )

    def test_artifact_reservation_orders_terminalization_after_publication(self) -> None:
        active = delegation()
        self.dispatcher(leases=(active,)).record(
            change(
                canonical_event_id="events/artifact-reservation",
                requested_operation="artifact",
            ),
            now=NOW,
        )
        claim = self.store.get_execution_claim(TASK)
        competing_store = DurableHandoffStore(self.path, retention_days=30)
        self.addCleanup(competing_store.close)
        started = threading.Event()
        finished = threading.Event()

        def terminalize():
            started.set()
            event = competing_store.release_execution_claim(
                TASK,
                executor_agent=OC_AGENT,
                idempotency_key=claim.idempotency_key,
                terminal_state="completed",
                mutation_id="mutation-after-artifact-reservation",
                now=NOW + timedelta(seconds=2),
            )
            finished.set()
            return event

        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.store.reserve_artifact_publication(
                TASK,
                executor_agent=OC_AGENT,
                permanent_owner=AGENT,
                delegation_slug=active.slug,
                publication_key="artifact-publication:v1",
                now=NOW + timedelta(seconds=1),
            ) as reserved:
                future = pool.submit(terminalize)
                self.assertTrue(started.wait(timeout=1))
                self.assertFalse(finished.wait(timeout=0.1))
                self.assertEqual(reserved.requested_operation, "artifact")
            event = future.result(timeout=1)
        self.assertEqual(event.event_type, "delegated_execution_handed_back")
        self.assertTrue(finished.is_set())

    def test_failed_reservation_rollback_never_restores_revoked_authority(self) -> None:
        active = delegation()
        self.dispatcher(leases=(active,)).record(
            change(
                canonical_event_id="events/artifact-reservation-crash",
                requested_operation="artifact",
            ),
            now=NOW,
        )
        claim = self.store.get_execution_claim(TASK)

        with self.assertRaisesRegex(RuntimeError, "simulated writer crash"):
            with self.store.reserve_artifact_publication(
                TASK,
                executor_agent=OC_AGENT,
                permanent_owner=AGENT,
                delegation_slug=active.slug,
                publication_key="artifact-publication:crash",
                now=NOW + timedelta(seconds=1),
            ):
                raise RuntimeError("simulated writer crash")

        self.store.release_execution_claim(
            TASK,
            executor_agent=OC_AGENT,
            idempotency_key=claim.idempotency_key,
            terminal_state="revoked",
            mutation_id="mutation-after-artifact-crash",
            now=NOW + timedelta(seconds=2),
        )
        with self.assertRaisesRegex(ValueError, "exact current artifact execution claim"):
            with self.store.reserve_artifact_publication(
                TASK,
                executor_agent=OC_AGENT,
                permanent_owner=AGENT,
                delegation_slug=active.slug,
                publication_key="artifact-publication:crash",
                now=NOW + timedelta(seconds=3),
            ):
                self.fail("revoked authority must not be recovered")

    def test_nonactive_lease_routes_to_the_permanent_owner(self) -> None:
        scheduled = delegation(
            starts_at=NOW + timedelta(minutes=15),
            ends_at=NOW + timedelta(minutes=45),
            state=DelegationState.SCHEDULED,
        )

        record = self.dispatcher(leases=(scheduled,)).record(
            change(canonical_event_id="events/scheduled-delegation"),
            now=NOW,
        )
        claim = self.store.get_execution_claim(TASK)

        self.assertEqual(record.executor_agent, AGENT)
        self.assertEqual(record.permanent_owner, AGENT)
        self.assertIsNone(record.delegation_slug)
        self.assertEqual(claim.executor_agent, AGENT)
        self.assertIsNone(claim.delegation_slug)

    def test_missing_canonical_task_status_fails_closed_to_permanent_owner(self) -> None:
        actionable = ActionableChange(
            task_slug=TASK,
            canonical_event_id="events/missing-task-status",
            canonical_version="42",
            trigger="answer_received",
            assigned_to=(AGENT,),
            route="hosts/tammy",
            summary="A verified answer is ready.",
            occurred_at=NOW,
            correlation_id="correlation-missing-task-status",
        )

        record = self.dispatcher(leases=(delegation(),)).record(
            actionable,
            now=NOW,
        )

        self.assertEqual(record.executor_agent, AGENT)
        self.assertIsNone(record.delegation_slug)

    def test_store_api_cannot_claim_a_suppressed_handoff(self) -> None:
        record = self.dispatcher().record(
            change(
                canonical_event_id="events/suppressed-claim",
                trigger="presentation_only",
            ),
            now=NOW,
        )

        claim = self.store.claim_execution(
            record.handoff_id,
            permanent_owner=AGENT,
            executor_agent=AGENT,
            delegation=None,
            task_status="planned",
            requested_operation="todo",
            owned_work_ready=False,
            now=NOW,
        )

        self.assertEqual(record.status, "suppressed")
        self.assertIsNone(claim)
        self.assertIsNone(self.store.get_execution_claim(TASK))

    def test_owned_openclaw_work_and_active_codex_work_prevent_delegation(self) -> None:
        active = delegation()
        cases = (
            (
                "owned-priority",
                change(canonical_event_id="events/owned-priority"),
                lambda executor: executor == OC_AGENT,
            ),
            (
                "active-codex",
                change(
                    canonical_event_id="events/active-codex",
                    task_status="active",
                ),
                lambda _executor: False,
            ),
        )
        for label, actionable, ready in cases:
            with self.subTest(label=label):
                path = self.path if label == "owned-priority" else self.path + ".active"
                if label == "active-codex":
                    store = DurableHandoffStore(path, retention_days=30)
                else:
                    store = self.store
                try:
                    dispatcher = HandoffDispatcher(
                        store,
                        registrations=(registration(), oc_registration()),
                        delegations=(active,),
                        owned_work_ready=ready,
                    )
                    record = dispatcher.record(actionable, now=NOW)
                    claim = store.get_execution_claim(TASK)
                    self.assertEqual(record.executor_agent, AGENT)
                    self.assertIsNone(record.delegation_slug)
                    self.assertEqual(claim.executor_agent, AGENT)
                    self.assertIsNone(claim.delegation_slug)
                finally:
                    if label == "active-codex":
                        store.close()
                        os.unlink(path)

    def test_two_executors_cannot_claim_the_same_task(self) -> None:
        other_store = DurableHandoffStore(self.path, retention_days=30)
        direct = HandoffDispatcher(
            self.store,
            registrations=(registration(), oc_registration()),
            delegations=(),
        )
        delegated = HandoffDispatcher(
            other_store,
            registrations=(registration(), oc_registration()),
            delegations=(delegation(),),
        )
        barrier = threading.Barrier(2)

        def record_once(item: tuple[HandoffDispatcher, ActionableChange]):
            dispatcher, actionable = item
            barrier.wait()
            return dispatcher.record(actionable, now=NOW)

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                records = list(
                    executor.map(
                        record_once,
                        (
                            (
                                direct,
                                change(
                                    canonical_event_id="events/direct-race",
                                    correlation_id="correlation-direct-race",
                                ),
                            ),
                            (
                                delegated,
                                change(
                                    canonical_event_id="events/delegated-race",
                                    correlation_id="correlation-delegated-race",
                                ),
                            ),
                        ),
                    )
                )
            winner = self.store.get_execution_claim(TASK)
            self.assertIn(winner.executor_agent, {AGENT, OC_AGENT})
            self.assertEqual(
                sorted(record.status for record in records),
                ["queued", "suppressed"],
            )
            claimed = self.store.query_events(
                limit=50,
                after_sequence=0,
                event_type="execution_claimed",
            )
            self.assertEqual(claimed.total, 1)
        finally:
            other_store.close()

    def test_newer_revocation_and_owned_priority_snapshots_win_before_claim_transaction(self) -> None:
        revoked = delegation(
            state=DelegationState.REVOKED,
            updated_at=NOW + timedelta(seconds=1),
        )
        cases = ("revoked", "owned-ready")
        for index, case in enumerate(cases, start=70):
            with self.subTest(case=case):
                path = f"{self.path}.{case}"

                class RaceStore(DurableHandoffStore):
                    def record(inner_self, *args, **kwargs):
                        if case == "revoked":
                            inner_self.observe_delegation_authority(
                                revoked,
                                observed_at=NOW + timedelta(seconds=1),
                            )
                        else:
                            inner_self.observe_executor_priority(
                                OC_AGENT,
                                owned_work_ready=True,
                                version="priority-owned-ready",
                                observed_at=NOW + timedelta(seconds=1),
                            )
                        return super().record(*args, **kwargs)

                store = RaceStore(path)
                try:
                    dispatcher = HandoffDispatcher(
                        store,
                        registrations=(registration(), oc_registration()),
                        delegations=(delegation(),),
                        owned_work_ready=lambda _executor: False,
                    )
                    record = dispatcher.record(
                        change(
                            task_slug=self.task(index),
                            canonical_event_id=f"events/{case}-before-claim",
                            correlation_id=f"correlation-{case}-before-claim",
                        ),
                        now=NOW,
                    )

                    self.assertEqual(record.status, "suppressed")
                    self.assertIsNone(store.get_execution_claim(self.task(index)))
                finally:
                    store.close()
                    os.unlink(path)

    def test_legacy_delegated_path_fails_closed_before_authority_callback(self) -> None:
        for index, case in enumerate(("revoked", "owned-ready"), start=80):
            with self.subTest(case=case):
                path = f"{self.path}.wake-{case}"
                store = DurableHandoffStore(path)
                try:
                    dispatcher = HandoffDispatcher(
                        store,
                        registrations=(registration(), oc_registration()),
                        delegations=(delegation(),),
                        owned_work_ready=lambda _executor: False,
                    )
                    record = dispatcher.record(
                        change(
                            task_slug=self.task(index),
                            canonical_event_id=f"events/{case}-before-wake",
                            correlation_id=f"correlation-{case}-before-wake",
                        ),
                        now=NOW,
                    )
                    wake_tokens: list[str] = []

                    def change_authority(_record) -> bool:
                        if case == "revoked":
                            store.observe_delegation_authority(
                                delegation(
                                    state=DelegationState.REVOKED,
                                    updated_at=NOW + timedelta(seconds=1),
                                ),
                                observed_at=NOW + timedelta(seconds=1),
                            )
                        else:
                            store.observe_executor_priority(
                                OC_AGENT,
                                owned_work_ready=True,
                                version="priority-owned-before-wake",
                                observed_at=NOW + timedelta(seconds=1),
                            )
                        return True

                    local = LocalAgentDispatcher(
                        store,
                        registration_id=OC_REGISTRATION_ID,
                        verify_route=change_authority,
                        wake=lambda _record, token=None: (
                            wake_tokens.append(str(token)) or True
                        ),
                    )

                    result = local.run_once(now=NOW + timedelta(seconds=2))

                    self.assertEqual(result.status, "dead_letter")
                    self.assertEqual(store.get(record.handoff_id).status, "dead_letter")
                    self.assertEqual(wake_tokens, [])
                    self.assertIsNone(store.get_execution_claim(self.task(index)))
                finally:
                    store.close()
                    os.unlink(path)

    def test_restart_replay_preserves_terminal_legacy_delegated_rejection(self) -> None:
        active = delegation()
        dispatcher = self.dispatcher(leases=(active,))
        actionable = change(canonical_event_id="events/restart-replay")
        dispatcher.record(actionable, now=NOW)
        before = self.store.get_execution_claim(TASK)
        wake_count = 0

        def wake(_record, _wake_token) -> bool:
            nonlocal wake_count
            wake_count += 1
            return True

        local = LocalAgentDispatcher(
            self.store,
            registration_id=OC_REGISTRATION_ID,
            verify_route=lambda record: record.executor_agent == OC_AGENT,
            wake=wake,
        )
        self.assertEqual(local.run_once(now=NOW).status, "dead_letter")

        self.store.close()
        self.store = DurableHandoffStore(self.path, retention_days=30)
        replay_dispatcher = self.dispatcher(leases=(active,))
        replay = replay_dispatcher.record(actionable, now=NOW + timedelta(seconds=1))
        after = self.store.get_execution_claim(TASK)
        recovered_local = LocalAgentDispatcher(
            self.store,
            registration_id=OC_REGISTRATION_ID,
            verify_route=lambda record: record.executor_agent == OC_AGENT,
            wake=wake,
        )

        self.assertEqual(replay.status, "dead_letter")
        self.assertIsNotNone(before)
        self.assertIsNone(after)
        self.assertIsNone(recovered_local.run_once(now=NOW + timedelta(seconds=1)))
        self.assertEqual(wake_count, 0)
        self.assertEqual(
            self.store.query_events(
                limit=50,
                after_sequence=0,
                event_type="execution_claimed",
            ).total,
            1,
        )

    def test_legacy_in_process_dispatcher_rejects_delegated_wake_before_callback(self) -> None:
        dispatcher = self.dispatcher(leases=(delegation(),))
        record = dispatcher.record(
            change(canonical_event_id="events/in-process-delegated-rejected"),
            now=NOW,
        )
        callbacks: list[str] = []
        local = LocalAgentDispatcher(
            self.store,
            registration_id=OC_REGISTRATION_ID,
            verify_route=lambda _record: callbacks.append("verify") or True,
            wake=lambda _record, _wake_token: callbacks.append("wake") or True,
        )

        result = local.run_once(now=NOW)

        self.assertEqual(result.status, "dead_letter")
        self.assertEqual(callbacks, [])
        self.assertIsNone(self.store.get_execution_claim(record.task_slug))
        releases = self.store.query_events(
            limit=50,
            after_sequence=0,
            event_type="delegated_execution_handed_back",
        ).events
        self.assertEqual(len(releases), 1)
        self.assertEqual(
            releases[0].execution_state, "terminal_delivery_failure"
        )

    def test_expiry_stops_new_delegated_claims_but_inflight_can_checkpoint(self) -> None:
        active = delegation()
        dispatcher = self.dispatcher(leases=(active,))
        first = dispatcher.record(
            change(canonical_event_id="events/inflight-before-expiry"),
            now=NOW,
        )
        claim = self.store.get_execution_claim(TASK)
        expired_attempt = dispatcher.record(
            change(
                canonical_event_id="events/new-at-expiry",
                canonical_version="43",
                correlation_id="correlation-new-at-expiry",
            ),
            now=active.ends_at,
        )

        self.assertEqual(first.executor_agent, OC_AGENT)
        self.assertEqual(expired_attempt.executor_agent, AGENT)
        self.assertEqual(expired_attempt.status, "suppressed")
        self.assertIsNone(
            self.store.claim(
                OC_REGISTRATION_ID,
                now=active.ends_at,
                lease_seconds=30,
            )
        )
        event = self.store.release_execution_claim(
            TASK,
            executor_agent=OC_AGENT,
            idempotency_key=claim.idempotency_key,
            terminal_state="checkpointed",
            mutation_id="mutation-checkpoint-after-expiry",
            now=active.ends_at,
        )
        replay = self.store.release_execution_claim(
            TASK,
            executor_agent=OC_AGENT,
            idempotency_key=claim.idempotency_key,
            terminal_state="checkpointed",
            mutation_id="mutation-checkpoint-after-expiry",
            now=active.ends_at + timedelta(seconds=1),
        )

        self.assertEqual(event.event_id, replay.event_id)
        self.assertEqual(event.event_type, "delegated_execution_handed_back")
        self.assertEqual(event.execution_state, "checkpointed")
        self.assertIsNone(self.store.get_execution_claim(TASK))
        self.assertEqual(
            self.store.get_execution_claim(TASK, include_terminal=True), claim
        )

    def test_expired_inflight_delivery_can_only_checkpoint_and_hand_back(self) -> None:
        active = delegation()
        self.dispatcher(leases=(active,)).record(
            change(canonical_event_id="events/inflight-expiry-fence"),
            now=NOW,
        )
        claim = self.store.get_execution_claim(TASK)
        delivery = self.store.claim(
            OC_REGISTRATION_ID,
            now=NOW,
            lease_seconds=30,
        )
        self.store.acknowledge(
            delivery.handoff_id,
            "actively_executing",
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            mutation_id="mutation-started-before-expiry",
            now=NOW + timedelta(seconds=1),
        )

        with self.assertRaisesRegex(ValueError, "checkpoint and hand back"):
            self.store.acknowledge(
                delivery.handoff_id,
                "completed",
                registration_id=OC_REGISTRATION_ID,
                lease_token=delivery.lease_token,
                lease_generation=delivery.lease_generation,
                mutation_id="mutation-completed-after-expiry",
                now=active.ends_at,
            )

        checkpoint = self.store.release_execution_claim(
            TASK,
            executor_agent=OC_AGENT,
            idempotency_key=claim.idempotency_key,
            terminal_state="checkpointed",
            mutation_id="mutation-checkpoint-inflight-after-expiry",
            now=active.ends_at,
        )
        self.assertEqual(checkpoint.execution_state, "checkpointed")
        self.assertEqual(self.store.get(delivery.handoff_id).status, "suppressed")

    def test_recovery_never_rotates_an_expired_delegated_execution_claim(self) -> None:
        active = delegation()
        record = self.dispatcher(leases=(active,)).record(
            change(canonical_event_id="events/recovery-after-expiry"),
            now=NOW,
        )
        delivery = self.store.claim(
            OC_REGISTRATION_ID,
            now=NOW,
            lease_seconds=30,
        )

        with self.assertRaisesRegex(ValueError, "checkpoint|expired"):
            self.store.recover_in_progress(
                record.handoff_id,
                registration=oc_registration(),
                expected_generation=delivery.lease_generation,
                now=active.ends_at,
            )

        self.assertEqual(self.store.get(record.handoff_id).status, "suppressed")
        self.assertIsNone(self.store.get_execution_claim(TASK))

    def test_recovery_revalidates_revocation_and_owned_work_priority(self) -> None:
        for index, case in enumerate(("revoked", "owned-ready"), start=90):
            with self.subTest(case=case):
                path = f"{self.path}.recovery-{case}"
                store = DurableHandoffStore(path)
                task_slug = self.task(index)
                try:
                    dispatcher = HandoffDispatcher(
                        store,
                        registrations=(registration(), oc_registration()),
                        delegations=(delegation(),),
                        owned_work_ready=lambda _executor: False,
                    )
                    record = dispatcher.record(
                        change(
                            task_slug=task_slug,
                            canonical_event_id=f"events/recovery-{case}",
                            correlation_id=f"correlation-recovery-{case}",
                        ),
                        now=NOW,
                    )
                    delivery = store.claim(
                        OC_REGISTRATION_ID,
                        now=NOW,
                        lease_seconds=30,
                    )
                    if case == "revoked":
                        store.observe_delegation_authority(
                            delegation(
                                state=DelegationState.REVOKED,
                                updated_at=NOW + timedelta(seconds=1),
                            ),
                            observed_at=NOW + timedelta(seconds=1),
                        )
                    else:
                        store.observe_executor_priority(
                            OC_AGENT,
                            owned_work_ready=True,
                            version="priority-owned-during-recovery",
                            observed_at=NOW + timedelta(seconds=1),
                        )

                    with self.assertRaisesRegex(ValueError, "checkpoint|authority"):
                        store.recover_in_progress(
                            record.handoff_id,
                            registration=oc_registration(),
                            expected_generation=delivery.lease_generation,
                            now=NOW + timedelta(seconds=2),
                        )

                    self.assertEqual(store.get(record.handoff_id).status, "suppressed")
                    self.assertIsNone(store.get_execution_claim(task_slug))
                finally:
                    store.close()
                    os.unlink(path)

    def test_completion_and_revocation_emit_exact_handback_evidence(self) -> None:
        active = delegation()
        for index, terminal_state in enumerate(("completed", "revoked"), start=10):
            with self.subTest(terminal_state=terminal_state):
                task_slug = self.task(index)
                record = self.dispatcher(leases=(active,)).record(
                    change(
                        task_slug=task_slug,
                        canonical_event_id=f"events/{terminal_state}",
                        correlation_id=f"correlation-{terminal_state}",
                    ),
                    now=NOW,
                )
                claim = self.store.get_execution_claim(task_slug)
                event = self.store.release_execution_claim(
                    task_slug,
                    executor_agent=OC_AGENT,
                    idempotency_key=claim.idempotency_key,
                    terminal_state=terminal_state,
                    mutation_id=f"mutation-handback-{terminal_state}",
                    now=NOW + timedelta(seconds=index),
                )

                self.assertEqual(event.handoff_id, record.handoff_id)
                self.assertEqual(event.permanent_owner, AGENT)
                self.assertEqual(event.executor_agent, OC_AGENT)
                self.assertEqual(event.delegation_slug, active.slug)
                self.assertEqual(event.correlation_id, f"correlation-{terminal_state}")
                self.assertEqual(event.idempotency_key, claim.idempotency_key)
                self.assertEqual(event.execution_state, terminal_state)

    def test_completed_acknowledgement_releases_the_execution_fence(self) -> None:
        self.dispatcher(leases=(delegation(),)).record(
            change(canonical_event_id="events/completed-ack-release"),
            now=NOW,
        )
        delivery = self.store.claim(
            OC_REGISTRATION_ID,
            now=NOW,
            lease_seconds=30,
        )

        completed = self.store.acknowledge(
            delivery.handoff_id,
            "completed",
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            mutation_id="mutation-completed-ack-release",
            now=NOW + timedelta(seconds=1),
        )

        self.assertEqual(completed.status, "completed")
        self.assertIsNone(self.store.get_execution_claim(TASK))
        handback = self.store.query_events(
            limit=50,
            after_sequence=0,
            event_type="delegated_execution_handed_back",
        )
        self.assertEqual(handback.total, 1)
        self.assertEqual(handback.events[0].execution_state, "completed")

    def test_revocation_cancels_pending_wake_and_old_release_replays_exactly(self) -> None:
        active = delegation()
        self.dispatcher(leases=(active,)).record(
            change(canonical_event_id="events/revoke-before-wake"),
            now=NOW,
        )
        claim = self.store.get_execution_claim(TASK)
        released = self.store.release_execution_claim(
            TASK,
            executor_agent=OC_AGENT,
            idempotency_key=claim.idempotency_key,
            terminal_state="revoked",
            mutation_id="mutation-revoke-before-wake",
            now=NOW + timedelta(seconds=1),
        )

        self.assertIsNone(
            self.store.claim(
                OC_REGISTRATION_ID,
                now=NOW + timedelta(seconds=2),
                lease_seconds=30,
            )
        )
        self.assertEqual(self.store.get(released.handoff_id).status, "suppressed")

        HandoffDispatcher(
            self.store,
            registrations=(registration(), oc_registration()),
            delegations=(),
        ).record(
            change(
                canonical_event_id="events/owner-after-revoke",
                canonical_version="43",
                correlation_id="correlation-owner-after-revoke",
            ),
            now=NOW + timedelta(seconds=2),
        )
        replay = self.store.release_execution_claim(
            TASK,
            executor_agent=OC_AGENT,
            idempotency_key=claim.idempotency_key,
            terminal_state="revoked",
            mutation_id="mutation-revoke-before-wake",
            now=NOW + timedelta(seconds=3),
        )
        self.assertEqual(replay.event_id, released.event_id)

    def test_revocation_fences_an_already_received_local_delivery(self) -> None:
        self.dispatcher(leases=(delegation(),)).record(
            change(canonical_event_id="events/revoke-after-received"),
            now=NOW,
        )
        claim = self.store.get_execution_claim(TASK)
        delivery = self.store.claim(
            OC_REGISTRATION_ID,
            now=NOW,
            lease_seconds=30,
        )
        self.store.acknowledge(
            delivery.handoff_id,
            "received",
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            mutation_id="mutation-received-before-revoke",
            now=NOW + timedelta(seconds=1),
        )

        self.store.release_execution_claim(
            TASK,
            executor_agent=OC_AGENT,
            idempotency_key=claim.idempotency_key,
            terminal_state="revoked",
            mutation_id="mutation-revoke-received-delivery",
            now=NOW + timedelta(seconds=2),
        )

        self.assertEqual(self.store.get(delivery.handoff_id).status, "suppressed")
        with self.assertRaisesRegex(ValueError, "active lease owner"):
            self.store.acknowledge(
                delivery.handoff_id,
                "actively_executing",
                registration_id=OC_REGISTRATION_ID,
                lease_token=delivery.lease_token,
                lease_generation=delivery.lease_generation,
                mutation_id="mutation-after-revoke-must-fail",
                now=NOW + timedelta(seconds=3),
            )

    def test_durable_revocation_blocks_received_to_actively_executing_transition(self) -> None:
        self.dispatcher(leases=(delegation(),)).record(
            change(canonical_event_id="events/revoked-received-to-active"),
            now=NOW,
        )
        delivery = self.store.claim(
            OC_REGISTRATION_ID, now=NOW, lease_seconds=30
        )
        wake_token = f"wake/{delivery.record.idempotency_key}"
        self.store.authorize_wake(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            wake_token=wake_token,
            now=NOW,
        )
        self.store.acknowledge(
            delivery.handoff_id,
            "received",
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            mutation_id="mutation-received-before-durable-revocation",
            now=NOW + timedelta(seconds=1),
        )
        self.store.observe_delegation_authority(
            delegation(
                state=DelegationState.REVOKED,
                updated_at=NOW + timedelta(seconds=2),
            ),
            observed_at=NOW + timedelta(seconds=2),
        )

        result = self.store.acknowledge(
            delivery.handoff_id,
            "actively_executing",
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            mutation_id="mutation-active-after-durable-revocation",
            now=NOW + timedelta(seconds=3),
        )

        self.assertEqual(result.status, "suppressed")
        self.assertEqual(result.reason, "delegation_authority_changed")
        self.assertIsNone(self.store.get_execution_claim(TASK))

    def test_execution_start_rechecks_exact_wake_and_task_ownership(self) -> None:
        self.dispatcher(leases=(delegation(),)).record(
            change(canonical_event_id="events/task-authority-before-execution"),
            now=NOW,
        )
        delivery = self.store.claim(
            OC_REGISTRATION_ID, now=NOW, lease_seconds=30
        )
        wake_token = f"wake/{delivery.record.idempotency_key}"
        self.store.authorize_wake(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            wake_token=wake_token,
            now=NOW,
        )
        self.store.acknowledge(
            delivery.handoff_id,
            "received",
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            mutation_id="mutation-received-before-task-owner-change",
            now=NOW + timedelta(seconds=1),
        )
        with self.assertRaisesRegex(ValueError, "wake.*intent"):
            self.store.start_execution(
                delivery.handoff_id,
                registration_id=OC_REGISTRATION_ID,
                lease_token=delivery.lease_token,
                lease_generation=delivery.lease_generation,
                wake_token="wake/wrong-token",
                launch_id="launch/wrong-wake",
                now=NOW + timedelta(seconds=2),
            )

        self.store.observe_task_authority(
            TASK,
            owner_agent="agents/timmy",
            status="planned",
            version="43",
            observed_at=NOW + timedelta(seconds=2),
        )
        result = self.store.start_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            wake_token=wake_token,
            launch_id="launch/task-owner-changed",
            now=NOW + timedelta(seconds=3),
        )

        self.assertEqual(result.status, "suppressed")
        self.assertFalse(result.execution_started)
        self.assertIsNone(result.launch_grant)
        self.assertEqual(
            self.store.get(delivery.handoff_id).reason, "task_authority_changed"
        )
        self.assertIsNone(self.store.get_execution_claim(TASK))

    def _received_delegated_delivery(self, *, event_id: str):
        self.dispatcher(leases=(delegation(),)).record(
            change(canonical_event_id=event_id),
            now=NOW,
        )
        delivery = self.store.claim(
            OC_REGISTRATION_ID, now=NOW, lease_seconds=30
        )
        wake_token = f"wake/{delivery.record.idempotency_key}"
        self.store.authorize_wake(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            wake_token=wake_token,
            now=NOW,
        )
        self.store.acknowledge(
            delivery.handoff_id,
            "received",
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            mutation_id=f"mutation-received-{event_id.rsplit('/', 1)[-1]}",
            now=NOW + timedelta(seconds=1),
        )
        return delivery, wake_token

    def test_atomic_execution_start_replays_one_grant_and_fences_second_launch(self) -> None:
        delivery, wake_token = self._received_delegated_delivery(
            event_id="events/atomic-execution-start"
        )

        started = self.store.start_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            wake_token=wake_token,
            launch_id="launch/atomic-execution-start",
            now=NOW + timedelta(seconds=2),
        )
        replay = self.store.start_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            wake_token=wake_token,
            launch_id="launch/atomic-execution-start",
            now=NOW + timedelta(seconds=3),
        )

        self.assertIsInstance(started, ExecutionStartGrant)
        self.assertTrue(started.execution_started)
        self.assertEqual(started.status, "execution_started")
        self.assertEqual(started.launch_grant, replay.launch_grant)
        self.assertEqual(
            self.store.get(delivery.handoff_id).status, "execution_started"
        )
        events = self.store.query_events(
            limit=20,
            after_sequence=0,
            event_type="execution_started",
        )
        self.assertEqual(events.total, 1)
        with self.assertRaisesRegex(ValueError, "launch"):
            self.store.start_execution(
                delivery.handoff_id,
                registration_id=OC_REGISTRATION_ID,
                lease_token=delivery.lease_token,
                lease_generation=delivery.lease_generation,
                wake_token=wake_token,
                launch_id="launch/different",
                now=NOW + timedelta(seconds=4),
            )

    def test_concurrent_execution_start_cas_grants_exactly_one_launch(self) -> None:
        delivery, wake_token = self._received_delegated_delivery(
            event_id="events/concurrent-execution-start"
        )
        other = DurableHandoffStore(self.path, retention_days=30)
        self.addCleanup(other.close)
        barrier = threading.Barrier(2)

        def start(candidate: tuple[DurableHandoffStore, str]):
            store, launch_id = candidate
            barrier.wait()
            try:
                return store.start_execution(
                    delivery.handoff_id,
                    registration_id=OC_REGISTRATION_ID,
                    lease_token=delivery.lease_token,
                    lease_generation=delivery.lease_generation,
                    wake_token=wake_token,
                    launch_id=launch_id,
                    now=NOW + timedelta(seconds=2),
                )
            except ValueError as exc:
                return exc

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    start,
                    (
                        (self.store, "launch/concurrent-one"),
                        (other, "launch/concurrent-two"),
                    ),
                )
            )

        granted = [result for result in results if isinstance(result, ExecutionStartGrant)]
        rejected = [result for result in results if isinstance(result, ValueError)]
        self.assertEqual((len(granted), len(rejected)), (1, 1))
        self.assertTrue(granted[0].execution_started)
        self.assertRegex(str(rejected[0]), "launch|received")
        self.assertEqual(
            self.store.query_events(
                limit=20,
                after_sequence=0,
                event_type="execution_started",
            ).total,
            1,
        )
        with sqlite3.connect(self.path) as inspection:
            self.assertEqual(
                inspection.execute("SELECT COUNT(*) FROM execution_starts").fetchone()[0],
                1,
            )

    def test_revocation_before_start_suppresses_without_launch_grant(self) -> None:
        delivery, wake_token = self._received_delegated_delivery(
            event_id="events/revocation-before-start"
        )
        self.store.observe_delegation_authority(
            delegation(
                state=DelegationState.REVOKED,
                updated_at=NOW + timedelta(seconds=2),
            ),
            observed_at=NOW + timedelta(seconds=2),
        )

        result = self.store.start_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            wake_token=wake_token,
            launch_id="launch/revoked-before-start",
            now=NOW + timedelta(seconds=3),
        )

        self.assertFalse(result.execution_started)
        self.assertIsNone(result.launch_grant)
        self.assertEqual(result.status, "suppressed")
        self.assertEqual(
            self.store.query_events(
                limit=20,
                after_sequence=0,
                event_type="execution_started",
            ).total,
            0,
        )

    def test_received_delegated_delivery_requires_start_before_active_ack(self) -> None:
        delivery, wake_token = self._received_delegated_delivery(
            event_id="events/active-requires-start"
        )
        with self.assertRaisesRegex(ValueError, "execution start"):
            self.store.acknowledge(
                delivery.handoff_id,
                "actively_executing",
                registration_id=OC_REGISTRATION_ID,
                lease_token=delivery.lease_token,
                lease_generation=delivery.lease_generation,
                mutation_id="mutation-active-before-start",
                now=NOW + timedelta(seconds=2),
            )

        self.store.start_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            wake_token=wake_token,
            launch_id="launch/active-after-start",
            now=NOW + timedelta(seconds=2),
        )
        active = self.store.acknowledge(
            delivery.handoff_id,
            "actively_executing",
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            mutation_id="mutation-active-after-start",
            now=NOW + timedelta(seconds=3),
        )
        self.assertEqual(active.status, "actively_executing")

    def test_started_ambiguous_launch_checkpoints_and_replays_handback(self) -> None:
        delivery, wake_token = self._received_delegated_delivery(
            event_id="events/started-checkpoint"
        )
        self.store.start_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            wake_token=wake_token,
            launch_id="launch/started-checkpoint",
            now=NOW + timedelta(seconds=2),
        )

        checkpointed = self.store.checkpoint_started_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            launch_id="launch/started-checkpoint",
            mutation_id="mutation-started-checkpoint",
            reason="Launch outcome requires recovery.",
            now=NOW + timedelta(seconds=3),
        )
        replay = self.store.checkpoint_started_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            launch_id="launch/started-checkpoint",
            mutation_id="mutation-started-checkpoint",
            reason="Launch outcome requires recovery.",
            now=NOW + timedelta(seconds=4),
        )

        self.assertEqual(checkpointed.status, "suppressed")
        self.assertEqual(replay.status, "suppressed")
        self.assertIsNone(self.store.get_execution_claim(TASK))
        handbacks = self.store.query_events(
            limit=20,
            after_sequence=0,
            event_type="delegated_execution_handed_back",
        )
        self.assertEqual(handbacks.total, 1)
        self.assertEqual(handbacks.events[0].execution_state, "checkpointed")

    def test_operator_recovery_requeues_checkpointed_owned_execution(self) -> None:
        dispatcher = HandoffDispatcher(
            self.store,
            registrations=(registration(),),
            delegations=(),
        )
        record = dispatcher.record(
            change(
                canonical_event_id="events/owned-execution-recovery",
                task_status="active",
                requested_operation="task_status",
                trigger="task_activated",
            ),
            now=NOW,
        )
        delivery = self.store.claim(REGISTRATION_ID, now=NOW, lease_seconds=30)
        wake_token = f"wake/{delivery.record.idempotency_key}"
        self.store.authorize_wake(
            delivery.handoff_id,
            registration_id=REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            wake_token=wake_token,
            now=NOW + timedelta(seconds=1),
        )
        self.store.acknowledge(
            delivery.handoff_id,
            "received",
            registration_id=REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            mutation_id="mutation-owned-received",
            now=NOW + timedelta(seconds=2),
        )
        self.store.start_execution(
            delivery.handoff_id,
            registration_id=REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            wake_token=wake_token,
            launch_id="launch/owned-checkpointed",
            now=NOW + timedelta(seconds=3),
        )
        checkpointed = self.store.checkpoint_started_execution(
            delivery.handoff_id,
            registration_id=REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            launch_id="launch/owned-checkpointed",
            mutation_id="mutation-owned-checkpoint",
            reason="Launch outcome requires recovery.",
            now=NOW + timedelta(seconds=4),
        )
        self.assertEqual(checkpointed.status, "suppressed")
        self.assertIsNone(self.store.get_execution_claim(TASK))

        reopened = self.store.retry_suppressed_execution_recovery(
            record.handoff_id,
            mutation_id="mutation-operator-retry",
            summary="Operator verified launch dependency recovered.",
            now=NOW + timedelta(seconds=5),
        )

        self.assertEqual(reopened.status, "retrying")
        self.assertEqual(reopened.reason, "system_dependency_recovered")
        retry_claim = self.store.claim(
            REGISTRATION_ID, now=NOW + timedelta(seconds=6), lease_seconds=30
        )
        self.assertIsNotNone(retry_claim)
        self.assertEqual(retry_claim.record.handoff_id, record.handoff_id)
        self.assertEqual(retry_claim.record.attempt, 2)
        self.store.authorize_wake(
            retry_claim.record.handoff_id,
            registration_id=REGISTRATION_ID,
            lease_token=retry_claim.lease_token,
            lease_generation=retry_claim.lease_generation,
            wake_token=wake_token,
            now=NOW + timedelta(seconds=7),
        )
        self.store.acknowledge(
            retry_claim.record.handoff_id,
            "received",
            registration_id=REGISTRATION_ID,
            lease_token=retry_claim.lease_token,
            lease_generation=retry_claim.lease_generation,
            mutation_id="mutation-owned-retry-received",
            now=NOW + timedelta(seconds=8),
        )
        restarted = self.store.start_execution(
            retry_claim.record.handoff_id,
            registration_id=REGISTRATION_ID,
            lease_token=retry_claim.lease_token,
            lease_generation=retry_claim.lease_generation,
            wake_token=wake_token,
            launch_id="launch/owned-retry-start",
            now=NOW + timedelta(seconds=9),
        )

        self.assertTrue(restarted.execution_started)
        self.assertEqual(restarted.status, "execution_started")
        events = self.store.query_events(
            limit=50,
            after_sequence=0,
            event_type="delivery_retry",
        )
        self.assertEqual(events.total, 1)
        self.assertEqual(events.events[0].execution_state, "active")

    def test_rotated_checkpoint_uses_current_lease_without_mutating_start_fence(self) -> None:
        delivery, wake_token = self._received_delegated_delivery(
            event_id="events/rotated-start-checkpoint"
        )
        launch_id = "launch/rotated-start-checkpoint"
        self.store.start_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            wake_token=wake_token,
            launch_id=launch_id,
            now=NOW + timedelta(seconds=2),
        )
        with sqlite3.connect(self.path) as inspection:
            original_fence = inspection.execute(
                """
                SELECT lease_generation, lease_capability_ref, launch_grant_ref
                FROM execution_starts WHERE handoff_id = ?
                """,
                (delivery.handoff_id,),
            ).fetchone()
        recovered = self.store.recover_in_progress(
            delivery.handoff_id,
            registration=oc_registration(),
            expected_generation=delivery.lease_generation,
            now=NOW + timedelta(seconds=3),
        )

        replay = self.store.start_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=recovered.lease_token,
            lease_generation=recovered.lease_generation,
            wake_token=wake_token,
            launch_id=launch_id,
            now=NOW + timedelta(seconds=4),
        )
        with sqlite3.connect(self.path) as inspection:
            replayed_fence = inspection.execute(
                """
                SELECT lease_generation, lease_capability_ref, launch_grant_ref
                FROM execution_starts WHERE handoff_id = ?
                """,
                (delivery.handoff_id,),
            ).fetchone()
        self.assertEqual(replayed_fence, original_fence)
        self.assertTrue(replay.execution_started)
        with self.assertRaisesRegex(ValueError, "current.*lease|started launch"):
            self.store.checkpoint_started_execution(
                delivery.handoff_id,
                registration_id=OC_REGISTRATION_ID,
                lease_token=delivery.lease_token,
                lease_generation=delivery.lease_generation,
                launch_id=launch_id,
                mutation_id="mutation-stale-rotated-checkpoint",
                reason="Stale credential must not checkpoint current authority.",
                now=NOW + timedelta(seconds=5),
            )

        checkpointed = self.store.checkpoint_started_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=recovered.lease_token,
            lease_generation=recovered.lease_generation,
            launch_id=launch_id,
            mutation_id="mutation-current-rotated-checkpoint",
            reason="Rotated current authority recorded an ambiguous outcome.",
            now=NOW + timedelta(seconds=5),
        )

        self.assertEqual(checkpointed.status, "suppressed")

    def test_checkpoint_after_concurrent_revocation_reconciles_same_start_fence(self) -> None:
        delivery, wake_token = self._received_delegated_delivery(
            event_id="events/revoked-start-checkpoint"
        )
        launch_id = "launch/revoked-start-checkpoint"
        self.store.start_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            wake_token=wake_token,
            launch_id=launch_id,
            now=NOW + timedelta(seconds=2),
        )
        claim = self.store.get_execution_claim(TASK)
        released = self.store.release_execution_claim(
            TASK,
            executor_agent=OC_AGENT,
            idempotency_key=claim.idempotency_key,
            terminal_state="revoked",
            mutation_id="mutation-concurrent-revocation-before-checkpoint",
            now=NOW + timedelta(seconds=3),
        )

        reconciled = self.store.checkpoint_started_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            launch_id=launch_id,
            mutation_id="mutation-ambiguous-after-concurrent-revocation",
            reason="Ambiguous result observed after revocation won the race.",
            now=NOW + timedelta(seconds=4),
        )
        replay = self.store.checkpoint_started_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            launch_id=launch_id,
            mutation_id="mutation-ambiguous-after-concurrent-revocation",
            reason="Ambiguous result observed after revocation won the race.",
            now=NOW + timedelta(seconds=5),
        )

        self.assertEqual(reconciled.status, "suppressed")
        self.assertEqual(replay.status, "suppressed")
        handbacks = self.store.query_events(
            limit=20,
            after_sequence=0,
            event_type="delegated_execution_handed_back",
        )
        self.assertEqual(handbacks.total, 1)
        self.assertEqual(handbacks.events[0].event_id, released.event_id)
        self.assertEqual(handbacks.events[0].execution_state, "revoked")

    def test_command_not_started_abandons_start_and_rotates_next_launch_grant(self) -> None:
        delivery, wake_token = self._received_delegated_delivery(
            event_id="events/abandon-unused-start"
        )
        first_launch = "launch/abandon-unused-start-one"
        first = self.store.start_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            wake_token=wake_token,
            launch_id=first_launch,
            now=NOW + timedelta(seconds=2),
        )

        abandoned = self.store.abandon_unstarted_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            launch_id=first_launch,
            mutation_id="mutation-abandon-unused-start",
            reason="command_not_started",
            now=NOW + timedelta(seconds=3),
        )
        replay = self.store.abandon_unstarted_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            launch_id=first_launch,
            mutation_id="mutation-abandon-unused-start",
            reason="command_not_started",
            now=NOW + timedelta(seconds=4),
        )
        abandoned_start_replay = self.store.start_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            wake_token=wake_token,
            launch_id=first_launch,
            now=NOW + timedelta(seconds=4),
        )

        self.assertEqual(abandoned.status, "received")
        self.assertEqual(replay.status, "received")
        self.assertFalse(abandoned_start_replay.execution_started)
        self.assertEqual(abandoned_start_replay.status, "received")
        self.assertIsNone(abandoned_start_replay.launch_grant)
        with sqlite3.connect(self.path) as inspection:
            self.assertEqual(
                inspection.execute(
                    "SELECT COUNT(*) FROM execution_starts WHERE handoff_id = ?",
                    (delivery.handoff_id,),
                ).fetchone()[0],
                0,
            )
            archived = inspection.execute(
                """
                SELECT launch_id, launch_grant_ref, abandon_reason
                FROM abandoned_execution_starts WHERE handoff_id = ?
                """,
                (delivery.handoff_id,),
            ).fetchall()
        self.assertEqual(
            archived,
            [(first_launch, hashlib.sha256(first.launch_grant.encode()).hexdigest(), "command_not_started")],
        )
        self.assertEqual(
            self.store.query_events(
                limit=20,
                after_sequence=0,
                event_type="execution_start_abandoned",
            ).total,
            1,
        )

        second = self.store.start_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            wake_token=wake_token,
            launch_id="launch/abandon-unused-start-two",
            now=NOW + timedelta(seconds=5),
        )
        self.assertTrue(second.execution_started)
        self.assertNotEqual(second.launch_grant, first.launch_grant)

    def test_abandon_unused_start_requires_current_rotated_lease(self) -> None:
        delivery, wake_token = self._received_delegated_delivery(
            event_id="events/abandon-rotated-current-lease"
        )
        launch_id = "launch/abandon-rotated-current-lease"
        self.store.start_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            wake_token=wake_token,
            launch_id=launch_id,
            now=NOW + timedelta(seconds=2),
        )
        recovered = self.store.recover_in_progress(
            delivery.handoff_id,
            registration=oc_registration(),
            expected_generation=delivery.lease_generation,
            now=NOW + timedelta(seconds=3),
        )

        with self.assertRaisesRegex(ValueError, "current lease"):
            self.store.abandon_unstarted_execution(
                delivery.handoff_id,
                registration_id=OC_REGISTRATION_ID,
                lease_token=delivery.lease_token,
                lease_generation=delivery.lease_generation,
                launch_id=launch_id,
                mutation_id="mutation-stale-abandon-start",
                reason="command_not_started",
                now=NOW + timedelta(seconds=4),
            )
        reset = self.store.abandon_unstarted_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=recovered.lease_token,
            lease_generation=recovered.lease_generation,
            launch_id=launch_id,
            mutation_id="mutation-current-abandon-start",
            reason="command_not_started",
            now=NOW + timedelta(seconds=4),
        )
        self.assertEqual(reset.status, "received")

    def test_abandon_rejects_outcome_that_does_not_prove_command_unstarted(self) -> None:
        delivery, wake_token = self._received_delegated_delivery(
            event_id="events/reject-ambiguous-abandon"
        )
        launch_id = "launch/reject-ambiguous-abandon"
        self.store.start_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            wake_token=wake_token,
            launch_id=launch_id,
            now=NOW + timedelta(seconds=2),
        )

        with self.assertRaisesRegex(ValueError, "command.*started|unused start"):
            self.store.abandon_unstarted_execution(
                delivery.handoff_id,
                registration_id=OC_REGISTRATION_ID,
                lease_token=delivery.lease_token,
                lease_generation=delivery.lease_generation,
                launch_id=launch_id,
                mutation_id="mutation-reject-ambiguous-abandon",
                reason="timeout",
                now=NOW + timedelta(seconds=3),
            )

        self.assertEqual(
            self.store.get(delivery.handoff_id).status, "execution_started"
        )

    def test_concurrent_unused_start_abandon_is_one_audited_cas(self) -> None:
        delivery, wake_token = self._received_delegated_delivery(
            event_id="events/concurrent-abandon-unused-start"
        )
        launch_id = "launch/concurrent-abandon-unused-start"
        self.store.start_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            wake_token=wake_token,
            launch_id=launch_id,
            now=NOW + timedelta(seconds=2),
        )
        other = DurableHandoffStore(self.path, retention_days=30)
        self.addCleanup(other.close)
        barrier = threading.Barrier(2)

        def abandon(store: DurableHandoffStore):
            barrier.wait()
            return store.abandon_unstarted_execution(
                delivery.handoff_id,
                registration_id=OC_REGISTRATION_ID,
                lease_token=delivery.lease_token,
                lease_generation=delivery.lease_generation,
                launch_id=launch_id,
                mutation_id="mutation-concurrent-abandon-unused-start",
                reason="command_not_started",
                now=NOW + timedelta(seconds=3),
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(abandon, (self.store, other)))

        self.assertEqual([result.status for result in results], ["received"] * 2)
        self.assertTrue(all(result.abandoned for result in results))
        self.assertEqual(
            self.store.query_events(
                limit=20,
                after_sequence=0,
                event_type="execution_start_abandoned",
            ).total,
            1,
        )
        with sqlite3.connect(self.path) as inspection:
            self.assertEqual(
                inspection.execute(
                    "SELECT COUNT(*) FROM abandoned_execution_starts"
                ).fetchone()[0],
                1,
            )

    def test_revocation_after_start_preserves_start_evidence_and_hands_back(self) -> None:
        delivery, wake_token = self._received_delegated_delivery(
            event_id="events/revocation-after-start"
        )
        self.store.start_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            wake_token=wake_token,
            launch_id="launch/revocation-after-start",
            now=NOW + timedelta(seconds=2),
        )
        claim = self.store.get_execution_claim(TASK)

        self.store.release_execution_claim(
            TASK,
            executor_agent=OC_AGENT,
            idempotency_key=claim.idempotency_key,
            terminal_state="revoked",
            mutation_id="mutation-revocation-after-start",
            now=NOW + timedelta(seconds=3),
        )
        replay = self.store.start_execution(
            delivery.handoff_id,
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            wake_token=wake_token,
            launch_id="launch/revocation-after-start",
            now=NOW + timedelta(seconds=4),
        )

        self.assertEqual(self.store.get(delivery.handoff_id).status, "suppressed")
        self.assertFalse(replay.execution_started)
        self.assertIsNone(replay.launch_grant)
        self.assertEqual(
            self.store.query_events(
                limit=20,
                after_sequence=0,
                event_type="execution_started",
            ).total,
            1,
        )
        handbacks = self.store.query_events(
            limit=20,
            after_sequence=0,
            event_type="delegated_execution_handed_back",
        )
        self.assertEqual(handbacks.total, 1)
        self.assertEqual(handbacks.events[0].execution_state, "revoked")

    def test_verified_nonactionable_owner_change_updates_durable_task_control(self) -> None:
        dispatcher = self.dispatcher(leases=(delegation(),))
        dispatcher.record(
            change(canonical_event_id="events/owner-before-change"),
            now=NOW,
        )
        delivery = self.store.claim(
            OC_REGISTRATION_ID, now=NOW, lease_seconds=30
        )
        self.store.acknowledge(
            delivery.handoff_id,
            "received",
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            mutation_id="mutation-received-before-owner-change",
            now=NOW + timedelta(seconds=1),
        )

        changed = dispatcher.record(
            change(
                canonical_event_id="events/owner-changed-away",
                canonical_version="43",
                assigned_to=("agents/timmy",),
                route="hosts/timmy",
                correlation_id="correlation-owner-changed-away",
            ),
            now=NOW + timedelta(seconds=2),
        )
        result = self.store.acknowledge(
            delivery.handoff_id,
            "actively_executing",
            registration_id=OC_REGISTRATION_ID,
            lease_token=delivery.lease_token,
            lease_generation=delivery.lease_generation,
            mutation_id="mutation-active-after-owner-change",
            now=NOW + timedelta(seconds=3),
        )

        self.assertEqual(changed.status, "suppressed")
        self.assertEqual(result.status, "suppressed")
        self.assertEqual(result.reason, "task_authority_changed")

    def test_claim_release_is_idempotent_and_fenced(self) -> None:
        self.dispatcher(leases=(delegation(),)).record(
            change(canonical_event_id="events/release-fence"),
            now=NOW,
        )
        claim = self.store.get_execution_claim(TASK)
        for executor_agent, idempotency_key in (
            (AGENT, claim.idempotency_key),
            (OC_AGENT, "0" * 64),
        ):
            with self.subTest(executor_agent=executor_agent):
                with self.assertRaisesRegex(ValueError, "active execution claim"):
                    self.store.release_execution_claim(
                        TASK,
                        executor_agent=executor_agent,
                        idempotency_key=idempotency_key,
                        terminal_state="revoked",
                        mutation_id="mutation-wrong-execution-fence",
                        now=NOW,
                    )

        first = self.store.release_execution_claim(
            TASK,
            executor_agent=OC_AGENT,
            idempotency_key=claim.idempotency_key,
            terminal_state="revoked",
            mutation_id="mutation-correct-execution-fence",
            now=NOW,
        )
        replay = self.store.release_execution_claim(
            TASK,
            executor_agent=OC_AGENT,
            idempotency_key=claim.idempotency_key,
            terminal_state="revoked",
            mutation_id="mutation-correct-execution-fence",
            now=NOW + timedelta(seconds=1),
        )
        self.assertEqual(first.event_id, replay.event_id)
        with self.assertRaisesRegex(ValueError, "already terminal"):
            self.store.release_execution_claim(
                TASK,
                executor_agent=OC_AGENT,
                idempotency_key=claim.idempotency_key,
                terminal_state="completed",
                mutation_id="mutation-conflicting-execution-release",
                now=NOW + timedelta(seconds=2),
            )

    def test_delegation_identity_mismatch_deadletters_without_wake(self) -> None:
        wake_count = 0

        def wake(_record, _wake_token) -> bool:
            nonlocal wake_count
            wake_count += 1
            return True

        dispatcher = self.dispatcher(
            leases=(delegation(),),
            registrations=(
                registration(),
                oc_registration(route="hosts/timmy"),
            ),
        )
        record = dispatcher.record(
            change(canonical_event_id="events/delegation-route-mismatch"),
            now=NOW,
        )
        local = LocalAgentDispatcher(
            self.store,
            registration_id=OC_REGISTRATION_ID,
            verify_route=lambda _record: True,
            wake=wake,
        )

        self.assertEqual(record.status, "dead_letter")
        self.assertEqual(record.reason, "delegation_identity_mismatch")
        self.assertIsNone(local.run_once(now=NOW))
        self.assertEqual(wake_count, 0)
        self.assertIsNone(self.store.get_execution_claim(TASK))
        terminal = self.store.query_events(
            limit=50,
            after_sequence=0,
            event_type="delivery_terminal",
        )
        self.assertEqual(terminal.total, 1)


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

    def test_actionable_record_invokes_optional_buzz_sink_once_after_durable_outbox(self) -> None:
        observed = []
        dispatcher = HandoffDispatcher(
            self.store,
            registrations=(registration(),),
            coordination_sink=lambda task_change, record: observed.append(
                (task_change.canonical_event_id, record.handoff_id, self.store.get(record.handoff_id).status)
            ),
        )

        first = dispatcher.record(change(), now=NOW)
        second = dispatcher.record(change(), now=NOW)

        self.assertEqual(first.handoff_id, second.handoff_id)
        self.assertEqual(observed, [("events/100", first.handoff_id, "queued")])

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

    def test_canonical_bridge_persists_one_verified_record_or_attention_event(self) -> None:
        bridge = CanonicalHandoffEventBridge(self.dispatcher)
        snapshot = {
            "task_slug": TASK,
            "task": {
                "slug": TASK,
                "status": "active",
                "assigned_to": [AGENT],
                "blockers": [],
                "updated_at": NOW.isoformat(),
            },
            "todo": None,
            "route": "hosts/tammy",
        }
        receipt = {
            "verified": True,
            "canonical_event_id": "events/bridge-core",
            "canonical_version": "versions/1",
            "mutation_kind": "todo_created",
        }
        after = {**snapshot, "todo": {"slug": "todos/core", "parent_task": TASK}}

        first = bridge.after_verified_mutation(snapshot, after, receipt, NOW)
        replay = bridge.after_verified_mutation(snapshot, after, receipt, NOW)
        attention = bridge.after_verified_mutation(
            snapshot,
            {**after, "task": {**after["task"], "assigned_to": []}},
            {**receipt, "canonical_event_id": "events/bridge-attention"},
            NOW,
        )

        self.assertEqual(first.handoff_id, replay.handoff_id)
        self.assertEqual(first.status, "queued")
        self.assertEqual(attention.status, "suppressed")
        self.assertEqual(attention.trigger, "system_attention")
        self.assertEqual(self.store.query_events(limit=50, after_sequence=0).total, 2)

    def test_acknowledgement_states_validate_blocked_detail(self) -> None:
        for index, status in enumerate(("received", "actively_executing", "completed")):
            with self.subTest(status=status):
                record = self.record(
                    task_slug=f"tasks/ack-{index}",
                    canonical_event_id=f"events/ack-{index}",
                )
                claim = self.claim()
                acknowledged = self.store.acknowledge(
                    record.handoff_id,
                    status,
                    registration_id=REGISTRATION_ID,
                    lease_token=claim.lease_token,
                    lease_generation=claim.lease_generation,
                    mutation_id=f"mutation-ack-{index}",
                    now=NOW,
                )
                self.assertEqual(acknowledged.status, status)
        record = self.record(
            task_slug="tasks/ack-blocked",
            canonical_event_id="events/ack-blocked",
        )
        claim = self.claim()
        with self.assertRaisesRegex(ValueError, "detail"):
            self.store.acknowledge(
                record.handoff_id,
                "still_blocked",
                registration_id=REGISTRATION_ID,
                lease_token=claim.lease_token,
                lease_generation=claim.lease_generation,
                mutation_id="mutation-blocked-empty",
                now=NOW,
            )
        with self.assertRaisesRegex(ValueError, "privacy-safe"):
            self.store.acknowledge(
                record.handoff_id,
                "still_blocked",
                registration_id=REGISTRATION_ID,
                lease_token=claim.lease_token,
                lease_generation=claim.lease_generation,
                mutation_id="mutation-blocked-private",
                detail="token is missing",
                now=NOW,
            )
        blocked = self.store.acknowledge(
            record.handoff_id,
            "still_blocked",
            registration_id=REGISTRATION_ID,
            lease_token=claim.lease_token,
            lease_generation=claim.lease_generation,
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
                lease_generation=claim.lease_generation,
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
            lease_generation=first_claim.lease_generation,
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
            lease_generation=second_claim.lease_generation,
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
                lease_generation=stale.lease_generation,
                mutation_id="mutation-stale-ack",
                now=NOW + timedelta(seconds=6),
            ),
            lambda: self.store.record_failure(
                record.handoff_id,
                registration_id=REGISTRATION_ID,
                lease_token=stale.lease_token,
                lease_generation=stale.lease_generation,
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
            lease_generation=current.lease_generation,
            mutation_id="mutation-completed",
            now=NOW + timedelta(seconds=6),
        )
        replayed = self.store.acknowledge(
            record.handoff_id,
            "completed",
            registration_id=REGISTRATION_ID,
            lease_token=current.lease_token,
            lease_generation=current.lease_generation,
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
                lease_generation=current.lease_generation,
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
            lease_generation=failure_claim.lease_generation,
            mutation_id="mutation-failure-replay",
            retryable=True,
            summary="Network unavailable.",
            now=NOW,
        )
        replayed_failure = self.store.record_failure(
            failure_record.handoff_id,
            registration_id=REGISTRATION_ID,
            lease_token=failure_claim.lease_token,
            lease_generation=failure_claim.lease_generation,
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

    def test_hashes_lease_capability_and_caller_mutation_id_at_rest(self) -> None:
        record = self.record()
        claim = self.claim()
        mutation_id = "thread-019fc0e2-5a9b-78a0-b989-27e590890fd8"

        self.store.acknowledge(
            record.handoff_id,
            "received",
            registration_id=REGISTRATION_ID,
            lease_token=claim.lease_token,
            lease_generation=claim.lease_generation,
            mutation_id=mutation_id,
            now=NOW,
        )

        with sqlite3.connect(self.path) as inspection:
            capability_ref, generation = inspection.execute(
                "SELECT lease_capability_ref, lease_generation FROM leases WHERE handoff_id = ?",
                (record.handoff_id,),
            ).fetchone()
            mutation_ref, receipt_registration_ref, receipt_generation, receipt_capability_ref = (
                inspection.execute(
                    """
                    SELECT mutation_ref, registration_ref, lease_generation, lease_capability_ref
                    FROM mutation_receipts WHERE handoff_id = ?
                    """,
                    (record.handoff_id,),
                ).fetchone()
            )
            lease_columns = {
                row[1] for row in inspection.execute("PRAGMA table_info(leases)").fetchall()
            }

        self.assertEqual(capability_ref, hashlib.sha256(claim.lease_token.encode()).hexdigest())
        self.assertEqual(generation, claim.lease_generation)
        self.assertEqual(mutation_ref, hashlib.sha256(mutation_id.encode()).hexdigest())
        self.assertEqual(receipt_registration_ref, registration().reference)
        self.assertEqual(receipt_generation, claim.lease_generation)
        self.assertEqual(receipt_capability_ref, capability_ref)
        self.assertNotIn("lease_token", lease_columns)
        with open(self.path, "rb") as database_file:
            database_bytes = database_file.read()
        self.assertNotIn(claim.lease_token.encode(), database_bytes)
        self.assertNotIn(mutation_id.encode(), database_bytes)

    def test_replay_requires_original_owner_generation_and_capability(self) -> None:
        record = self.record()
        claim = self.claim()
        mutation_id = "mutation-authorized-replay"
        self.store.acknowledge(
            record.handoff_id,
            "completed",
            registration_id=REGISTRATION_ID,
            lease_token=claim.lease_token,
            lease_generation=claim.lease_generation,
            mutation_id=mutation_id,
            now=NOW,
        )

        invalid_replays = (
            {
                "registration_id": "private-registration-wrong-owner",
                "lease_token": claim.lease_token,
                "lease_generation": claim.lease_generation,
            },
            {
                "registration_id": REGISTRATION_ID,
                "lease_token": claim.lease_token,
                "lease_generation": claim.lease_generation + 1,
            },
            {
                "registration_id": REGISTRATION_ID,
                "lease_token": "stale-lease-capability",
                "lease_generation": claim.lease_generation,
            },
        )
        for credentials in invalid_replays:
            with self.subTest(credentials=credentials), self.assertRaisesRegex(
                ValueError,
                "receipt.*lease",
            ):
                self.store.acknowledge(
                    record.handoff_id,
                    "completed",
                    mutation_id=mutation_id,
                    now=NOW + timedelta(seconds=1),
                    **credentials,
                )

        replay = self.store.acknowledge(
            record.handoff_id,
            "completed",
            registration_id=REGISTRATION_ID,
            lease_token=claim.lease_token,
            lease_generation=claim.lease_generation,
            mutation_id=mutation_id,
            now=NOW + timedelta(seconds=1),
        )
        self.assertEqual(replay.status, "completed")
        self.assertEqual(
            len(
                self.store.query_events(
                    limit=50,
                    after_sequence=0,
                    event_type="acknowledgement",
                ).events
            ),
            1,
        )

    def test_one_handoff_runs_received_active_blocked_completed_lifecycle(self) -> None:
        record = self.record()
        local_dispatcher = LocalAgentDispatcher(
            self.store,
            registration_id=REGISTRATION_ID,
            verify_route=lambda claimed: claimed.agent_slug == AGENT,
            wake=lambda claimed, wake_token: True,
        )
        claim = local_dispatcher.run_once(now=NOW)
        self.assertIsNotNone(claim)
        self.assertEqual(claim.status, "received")

        active = self.store.acknowledge(
            record.handoff_id,
            "actively_executing",
            registration_id=REGISTRATION_ID,
            lease_token=claim.lease_token,
            lease_generation=claim.lease_generation,
            mutation_id="mutation-active",
            now=NOW + timedelta(seconds=1),
        )
        self.assertEqual(active.status, "actively_executing")
        with self.assertRaisesRegex(ValueError, "transition"):
            self.store.acknowledge(
                record.handoff_id,
                "received",
                registration_id=REGISTRATION_ID,
                lease_token=claim.lease_token,
                lease_generation=claim.lease_generation,
                mutation_id="mutation-regress-received",
                now=NOW + timedelta(seconds=2),
            )
        blocked = self.store.acknowledge(
            record.handoff_id,
            "still_blocked",
            registration_id=REGISTRATION_ID,
            lease_token=claim.lease_token,
            lease_generation=claim.lease_generation,
            mutation_id="mutation-blocked",
            detail="Waiting for a release decision.",
            now=NOW + timedelta(seconds=3),
        )
        self.assertEqual(blocked.status, "still_blocked")
        completed = self.store.acknowledge(
            record.handoff_id,
            "completed",
            registration_id=REGISTRATION_ID,
            lease_token=claim.lease_token,
            lease_generation=claim.lease_generation,
            mutation_id="mutation-completed-lifecycle",
            now=NOW + timedelta(seconds=4),
        )
        self.assertEqual(completed.status, "completed")
        with self.assertRaisesRegex(ValueError, "transition|active lease"):
            self.store.acknowledge(
                record.handoff_id,
                "still_blocked",
                registration_id=REGISTRATION_ID,
                lease_token=claim.lease_token,
                lease_generation=claim.lease_generation,
                mutation_id="mutation-regress-completed",
                detail="Waiting for a release decision.",
                now=NOW + timedelta(seconds=5),
            )
        events = self.store.query_events(
            limit=50,
            after_sequence=0,
            event_type="acknowledgement",
        ).events
        self.assertEqual(
            [event.status for event in events],
            ["received", "actively_executing", "still_blocked", "completed"],
        )

    def test_reopens_and_rotates_capability_for_received_and_active_handoff(self) -> None:
        record = self.record()
        local_dispatcher = LocalAgentDispatcher(
            self.store,
            registration_id=REGISTRATION_ID,
            verify_route=lambda claimed: claimed.agent_slug == AGENT,
            wake=lambda claimed, wake_token: True,
        )
        received_claim = local_dispatcher.run_once(now=NOW)
        self.assertIsNotNone(received_claim)
        self.assertEqual(received_claim.status, "received")

        self.store.close()
        self.store = DurableHandoffStore(self.path, retention_days=30)
        invalid_recoveries = (
            registration(verified=False),
            registration(route="hosts/timmy"),
            registration(
                registration_id="private-registration-wrong-owner",
                agent_slug="agents/timmy",
                route="hosts/timmy",
            ),
        )
        for invalid_registration in invalid_recoveries:
            with self.subTest(registration=invalid_registration), self.assertRaisesRegex(
                ValueError,
                "verified registration|owner",
            ):
                self.store.recover_in_progress(
                    record.handoff_id,
                    registration=invalid_registration,
                    expected_generation=received_claim.lease_generation,
                    now=NOW + timedelta(seconds=1),
                )
        with self.assertRaisesRegex(ValueError, "generation"):
            self.store.recover_in_progress(
                record.handoff_id,
                registration=registration(),
                expected_generation=received_claim.lease_generation + 1,
                now=NOW + timedelta(seconds=1),
            )

        active_claim = self.store.recover_in_progress(
            record.handoff_id,
            registration=registration(),
            expected_generation=received_claim.lease_generation,
            now=NOW + timedelta(seconds=1),
        )
        self.assertGreater(
            active_claim.lease_generation,
            received_claim.lease_generation,
        )
        with self.assertRaisesRegex(ValueError, "active lease"):
            self.store.acknowledge(
                record.handoff_id,
                "actively_executing",
                registration_id=REGISTRATION_ID,
                lease_token=received_claim.lease_token,
                lease_generation=received_claim.lease_generation,
                mutation_id="mutation-old-received-capability",
                now=NOW + timedelta(seconds=2),
            )
        active = self.store.acknowledge(
            record.handoff_id,
            "actively_executing",
            registration_id=REGISTRATION_ID,
            lease_token=active_claim.lease_token,
            lease_generation=active_claim.lease_generation,
            mutation_id="mutation-recovered-active",
            now=NOW + timedelta(seconds=2),
        )
        self.assertEqual(active.status, "actively_executing")

        self.store.close()
        self.store = DurableHandoffStore(self.path, retention_days=30)
        completed_claim = self.store.recover_in_progress(
            record.handoff_id,
            registration=registration(),
            expected_generation=active_claim.lease_generation,
            now=NOW + timedelta(seconds=3),
        )
        with self.assertRaisesRegex(ValueError, "active lease"):
            self.store.acknowledge(
                record.handoff_id,
                "completed",
                registration_id=REGISTRATION_ID,
                lease_token=active_claim.lease_token,
                lease_generation=active_claim.lease_generation,
                mutation_id="mutation-old-active-capability",
                now=NOW + timedelta(seconds=4),
            )
        completed = self.store.acknowledge(
            record.handoff_id,
            "completed",
            registration_id=REGISTRATION_ID,
            lease_token=completed_claim.lease_token,
            lease_generation=completed_claim.lease_generation,
            mutation_id="mutation-recovered-completed",
            now=NOW + timedelta(seconds=4),
        )
        self.assertEqual(completed.status, "completed")

        recoveries = self.store.query_events(
            limit=50,
            after_sequence=0,
            event_type="capability_rotated",
        ).events
        self.assertEqual(
            [(event.status, event.lease_generation) for event in recoveries],
            [
                ("received", active_claim.lease_generation),
                ("actively_executing", completed_claim.lease_generation),
            ],
        )
        self.assertTrue(
            all(event.registration_ref == registration().reference for event in recoveries)
        )
        with open(self.path, "rb") as database_file:
            database_bytes = database_file.read()
        for capability in (
            received_claim.lease_token,
            active_claim.lease_token,
            completed_claim.lease_token,
        ):
            self.assertNotIn(capability.encode(), database_bytes)

    def test_authoritative_recovery_state_validates_and_rotates_leased_claim(self) -> None:
        record = self.record(canonical_event_id="events/leased-recovery")
        claim = self.claim()
        baseline = self.store.query_events(limit=50, after_sequence=0).total

        state = self.store.read_recovery_state(
            record.handoff_id,
            registration=registration(),
        )

        self.assertEqual(state.handoff_id, record.handoff_id)
        self.assertEqual(state.status, "leased")
        self.assertEqual(state.lease_generation, claim.lease_generation)
        rendered = repr(state.to_dict())
        self.assertNotIn(REGISTRATION_ID, rendered)
        self.assertNotIn(claim.lease_token, rendered)
        self.assertEqual(
            self.store.query_events(limit=50, after_sequence=0).total,
            baseline,
        )
        with self.assertRaisesRegex(ValueError, "current owner"):
            self.store.read_recovery_state(
                record.handoff_id,
                registration=registration(
                    registration_id="private-registration-timmy",
                    agent_slug="agents/timmy",
                    route="hosts/timmy",
                ),
            )
        self.assertEqual(
            self.store.query_events(limit=50, after_sequence=0).total,
            baseline,
        )

        recovered = self.store.recover_in_progress(
            record.handoff_id,
            registration=registration(),
            expected_generation=claim.lease_generation,
            now=NOW,
        )
        self.assertEqual(recovered.status, "leased")
        self.assertEqual(
            recovered.lease_generation,
            claim.lease_generation + 1,
        )
        self.assertNotEqual(recovered.lease_token, claim.lease_token)

    def test_guardian_requeues_leased_recovery_after_dispatcher_crashes(self) -> None:
        record = self.record(canonical_event_id="events/leased-recovery-crash")
        original = self.store.claim(
            REGISTRATION_ID,
            now=NOW,
            lease_seconds=5,
        )
        self.assertIsNotNone(original)
        recovered = self.store.recover_in_progress(
            record.handoff_id,
            registration=registration(),
            expected_generation=original.lease_generation,
            now=NOW + timedelta(seconds=4),
        )

        self.assertEqual(
            HandoffGuardian(self.store).reconcile(
                now=NOW + timedelta(seconds=33)
            ),
            0,
        )
        self.assertEqual(
            HandoffGuardian(self.store).reconcile(
                now=NOW + timedelta(seconds=35)
            ),
            1,
        )
        self.assertEqual(self.store.get(record.handoff_id).status, "retrying")
        for label, claim in (("original", original), ("recovered", recovered)):
            with self.subTest(capability=label), self.assertRaisesRegex(
                ValueError,
                "active lease",
            ):
                self.store.acknowledge(
                    record.handoff_id,
                    "completed",
                    registration_id=REGISTRATION_ID,
                    lease_token=claim.lease_token,
                    lease_generation=claim.lease_generation,
                    mutation_id=f"mutation-crashed-{label}",
                    now=NOW + timedelta(seconds=35),
                )

        reclaimed = self.store.claim(
            REGISTRATION_ID,
            now=NOW + timedelta(seconds=35),
            lease_seconds=30,
        )
        self.assertIsNotNone(reclaimed)
        self.assertEqual(reclaimed.handoff_id, record.handoff_id)
        self.assertEqual(
            reclaimed.lease_generation,
            recovered.lease_generation + 1,
        )

    def test_nonleased_recovery_remains_guardian_independent(self) -> None:
        record = self.record(canonical_event_id="events/received-recovery")
        claim = self.claim()
        self.store.acknowledge(
            record.handoff_id,
            "received",
            registration_id=REGISTRATION_ID,
            lease_token=claim.lease_token,
            lease_generation=claim.lease_generation,
            mutation_id="mutation-received-before-recovery",
            now=NOW,
        )
        recovered = self.store.recover_in_progress(
            record.handoff_id,
            registration=registration(),
            expected_generation=claim.lease_generation,
            now=NOW + timedelta(seconds=1),
        )

        self.assertEqual(recovered.status, "received")
        self.assertEqual(
            HandoffGuardian(self.store).reconcile(
                now=NOW + timedelta(days=1)
            ),
            0,
        )
        self.assertEqual(self.store.get(record.handoff_id).status, "received")

    def test_reopens_and_rotates_capability_for_still_blocked_handoff(self) -> None:
        record = self.record()
        blocked_claim = self.claim()
        lifecycle = (
            ("received", "mutation-blocked-restart-received", None),
            ("actively_executing", "mutation-blocked-restart-active", None),
            (
                "still_blocked",
                "mutation-blocked-restart-blocked",
                "Waiting for a release decision.",
            ),
        )
        for index, (status, mutation_id, detail) in enumerate(lifecycle):
            acknowledged = self.store.acknowledge(
                record.handoff_id,
                status,
                registration_id=REGISTRATION_ID,
                lease_token=blocked_claim.lease_token,
                lease_generation=blocked_claim.lease_generation,
                mutation_id=mutation_id,
                detail=detail,
                now=NOW + timedelta(seconds=index),
            )
            self.assertEqual(acknowledged.status, status)

        self.store.close()
        self.store = DurableHandoffStore(self.path, retention_days=30)
        with self.assertRaisesRegex(ValueError, "owner"):
            self.store.recover_in_progress(
                record.handoff_id,
                registration=registration(route="hosts/timmy"),
                expected_generation=blocked_claim.lease_generation,
                now=NOW + timedelta(seconds=3),
            )
        recovered_claim = self.store.recover_in_progress(
            record.handoff_id,
            registration=registration(),
            expected_generation=blocked_claim.lease_generation,
            now=NOW + timedelta(seconds=3),
        )
        self.assertGreater(
            recovered_claim.lease_generation,
            blocked_claim.lease_generation,
        )
        with self.assertRaisesRegex(ValueError, "active lease"):
            self.store.acknowledge(
                record.handoff_id,
                "actively_executing",
                registration_id=REGISTRATION_ID,
                lease_token=blocked_claim.lease_token,
                lease_generation=blocked_claim.lease_generation,
                mutation_id="mutation-blocked-restart-stale",
                now=NOW + timedelta(seconds=4),
            )
        active = self.store.acknowledge(
            record.handoff_id,
            "actively_executing",
            registration_id=REGISTRATION_ID,
            lease_token=recovered_claim.lease_token,
            lease_generation=recovered_claim.lease_generation,
            mutation_id="mutation-blocked-restart-resumed",
            now=NOW + timedelta(seconds=4),
        )
        self.assertEqual(active.status, "actively_executing")
        completed = self.store.acknowledge(
            record.handoff_id,
            "completed",
            registration_id=REGISTRATION_ID,
            lease_token=recovered_claim.lease_token,
            lease_generation=recovered_claim.lease_generation,
            mutation_id="mutation-blocked-restart-completed",
            now=NOW + timedelta(seconds=5),
        )
        self.assertEqual(completed.status, "completed")

        recovery = self.store.query_events(
            limit=50,
            after_sequence=0,
            event_type="capability_rotated",
        ).events
        self.assertEqual(len(recovery), 1)
        self.assertEqual(recovery[0].status, "still_blocked")
        self.assertEqual(recovery[0].lease_generation, recovered_claim.lease_generation)
        self.assertEqual(recovery[0].registration_ref, registration().reference)
        with open(self.path, "rb") as database_file:
            database_bytes = database_file.read()
        self.assertNotIn(blocked_claim.lease_token.encode(), database_bytes)
        self.assertNotIn(recovered_claim.lease_token.encode(), database_bytes)

    def test_recovered_nonterminal_states_can_fail_retry_and_reclaim_same_handoff(self) -> None:
        for index, status in enumerate(
            ("received", "actively_executing", "still_blocked")
        ):
            with self.subTest(status=status):
                record = self.record(canonical_event_id=f"events/recovered-failure-{index}")
                claim = self.claim()
                self.store.acknowledge(
                    record.handoff_id,
                    status,
                    registration_id=REGISTRATION_ID,
                    lease_token=claim.lease_token,
                    lease_generation=claim.lease_generation,
                    mutation_id=f"mutation-recovered-state-{index}",
                    now=NOW,
                    detail=(
                        "Waiting on verified approval."
                        if status == "still_blocked"
                        else None
                    ),
                )
                recovered = self.store.recover_in_progress(
                    record.handoff_id,
                    registration=registration(),
                    expected_generation=claim.lease_generation,
                    now=NOW,
                )
                baseline = self.store.query_events(limit=200, after_sequence=0).total
                with self.assertRaisesRegex(ValueError, "active lease owner"):
                    self.store.record_failure(
                        record.handoff_id,
                        registration_id=REGISTRATION_ID,
                        lease_token=claim.lease_token,
                        lease_generation=claim.lease_generation,
                        mutation_id=f"mutation-recovered-stale-{index}",
                        retryable=True,
                        summary="Dispatcher delivery will retry.",
                        now=NOW,
                    )
                self.assertEqual(
                    self.store.query_events(limit=200, after_sequence=0).total,
                    baseline,
                )
                retried = self.store.record_failure(
                    record.handoff_id,
                    registration_id=REGISTRATION_ID,
                    lease_token=recovered.lease_token,
                    lease_generation=recovered.lease_generation,
                    mutation_id=f"mutation-recovered-retry-{index}",
                    retryable=True,
                    summary="Dispatcher delivery will retry.",
                    now=NOW,
                )
                replay = self.store.record_failure(
                    record.handoff_id,
                    registration_id=REGISTRATION_ID,
                    lease_token=recovered.lease_token,
                    lease_generation=recovered.lease_generation,
                    mutation_id=f"mutation-recovered-retry-{index}",
                    retryable=True,
                    summary="Dispatcher delivery will retry.",
                    now=NOW,
                )
                self.assertEqual(retried.status, "retrying")
                self.assertEqual(replay.status, "retrying")
                reclaimed = self.claim()
                self.assertEqual(reclaimed.handoff_id, record.handoff_id)
                self.assertEqual(
                    reclaimed.lease_generation,
                    recovered.lease_generation + 1,
                )
                self.store.acknowledge(
                    record.handoff_id,
                    "completed",
                    registration_id=REGISTRATION_ID,
                    lease_token=reclaimed.lease_token,
                    lease_generation=reclaimed.lease_generation,
                    mutation_id=f"mutation-recovered-complete-{index}",
                    now=NOW,
                )

    def test_recovered_nonterminal_state_can_fail_terminal_without_regression(self) -> None:
        record = self.record(canonical_event_id="events/recovered-terminal")
        claim = self.claim()
        self.store.acknowledge(
            record.handoff_id,
            "received",
            registration_id=REGISTRATION_ID,
            lease_token=claim.lease_token,
            lease_generation=claim.lease_generation,
            mutation_id="mutation-recovered-terminal-state",
            now=NOW,
        )
        recovered = self.store.recover_in_progress(
            record.handoff_id,
            registration=registration(),
            expected_generation=claim.lease_generation,
            now=NOW,
        )

        terminal = self.store.record_failure(
            record.handoff_id,
            registration_id=REGISTRATION_ID,
            lease_token=recovered.lease_token,
            lease_generation=recovered.lease_generation,
            mutation_id="mutation-recovered-terminal",
            retryable=False,
            summary="Dispatcher delivery stopped after terminal failure.",
            now=NOW,
        )

        self.assertEqual(terminal.status, "dead_letter")
        self.assertIsNone(
            self.store.claim(REGISTRATION_ID, now=NOW, lease_seconds=30)
        )
        self.assertIsNone(self.store.get_execution_claim(record.task_slug))
        self.assertIsNotNone(
            self.store.get_execution_claim(record.task_slug, include_terminal=True)
        )
        release_events = self.store.query_events(
            limit=50,
            after_sequence=0,
            event_type="execution_claim_released",
        ).events
        self.assertEqual(len(release_events), 1)
        self.assertEqual(
            release_events[0].execution_state, "terminal_delivery_failure"
        )

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
            lease_generation=first_claim.lease_generation,
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

        terminal_record = self.record(
            task_slug="tasks/terminal-audit",
            canonical_event_id="events/terminal",
        )
        terminal_claim = self.claim()
        self.store.record_failure(
            terminal_record.handoff_id,
            registration_id=REGISTRATION_ID,
            lease_token=terminal_claim.lease_token,
            lease_generation=terminal_claim.lease_generation,
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

    def test_claim_recovers_an_expired_lease_before_retrying_delivery(self) -> None:
        record = self.record()
        first = self.store.claim(REGISTRATION_ID, now=NOW, lease_seconds=5)

        retried = self.store.claim(
            REGISTRATION_ID,
            now=NOW + timedelta(seconds=6),
            lease_seconds=30,
        )

        self.assertEqual(retried.handoff_id, record.handoff_id)
        self.assertEqual(retried.status, "leased")
        self.assertEqual(retried.lease_generation, first.lease_generation + 1)
        event_types = [
            event.event_type
            for event in self.store.query_events(limit=50, after_sequence=0).events
        ]
        self.assertEqual(
            event_types,
            [
                "handoff_queued",
                "handoff_leased",
                "lease_expired",
                "handoff_leased",
            ],
        )

    def test_store_file_is_created_and_read_back_private(self) -> None:
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

        os.chmod(self.path, 0o644)
        self.store.close()
        self.store = DurableHandoffStore(self.path, retention_days=30)

        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_atomic_claim_identity_predicates_reject_without_mutation(self) -> None:
        record = self.record()
        baseline = self.store.query_events(limit=50, after_sequence=0).total
        current_registration = registration()
        cases = (
            {
                "expected_agent_slug": "agents/timmy",
                "expected_registration_ref": current_registration.reference,
                "expected_route": current_registration.route,
            },
            {
                "expected_agent_slug": current_registration.agent_slug,
                "expected_registration_ref": "0" * 64,
                "expected_route": current_registration.route,
            },
            {
                "expected_agent_slug": current_registration.agent_slug,
                "expected_registration_ref": current_registration.reference,
                "expected_route": "hosts/timmy",
            },
        )

        for expected in cases:
            with self.subTest(expected=expected):
                claimed = self.store.claim(
                    REGISTRATION_ID,
                    now=NOW,
                    lease_seconds=30,
                    **expected,
                )
                self.assertIsNone(claimed)
                self.assertEqual(self.store.get(record.handoff_id).status, "queued")
                self.assertEqual(
                    self.store.query_events(limit=50, after_sequence=0).total,
                    baseline,
                )

    def test_local_dispatcher_claims_only_its_exact_registration_and_acknowledges_received(self) -> None:
        record = self.record()
        dispatcher = LocalAgentDispatcher(
            self.store,
            registration_id=REGISTRATION_ID,
            verify_route=lambda claimed: claimed.agent_slug == AGENT,
            wake=lambda claimed, wake_token: True,
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

    def test_timestamp_range_preserves_filtered_totals_order_and_pagination(self) -> None:
        parameters = inspect.signature(self.store.query_events).parameters
        self.assertIn("occurred_after", parameters)
        self.assertIn("occurred_before", parameters)
        self.record(canonical_event_id="events/old", occurred_at=NOW - timedelta(days=8))
        self.record(canonical_event_id="events/recent-1", occurred_at=NOW - timedelta(minutes=50))
        self.record(canonical_event_id="events/recent-2", occurred_at=NOW - timedelta(minutes=20))
        self.record(canonical_event_id="events/future", occurred_at=NOW + timedelta(minutes=1))

        query = {
            "occurred_after": NOW - timedelta(hours=1),
            "occurred_before": NOW,
        }
        first = self.store.query_events(limit=1, after_sequence=0, **query)
        final = self.store.query_events(
            limit=1,
            after_sequence=first.next_sequence,
            **query,
        )

        self.assertEqual(first.total, 2)
        self.assertEqual([event.sequence for event in first.events], [2])
        self.assertEqual(first.next_sequence, 2)
        self.assertEqual(final.total, 1)
        self.assertEqual([event.sequence for event in final.events], [3])
        self.assertIsNone(final.next_sequence)

        with self.assertRaisesRegex(ValueError, "occurred_after must not exceed"):
            self.store.query_events(
                limit=50,
                after_sequence=0,
                occurred_after=NOW,
                occurred_before=NOW - timedelta(seconds=1),
            )

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


class HandoffSchemaMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        handle, self.path = tempfile.mkstemp(
            prefix="handoff-schema-migration-", suffix=".sqlite3"
        )
        os.close(handle)

    def tearDown(self) -> None:
        os.unlink(self.path)

    def _seed_two_unfenced_legacy_handoffs(self) -> None:
        store = DurableHandoffStore(self.path)
        store.close()
        with sqlite3.connect(self.path) as connection:
            connection.execute("DROP TABLE execution_claims")
            connection.execute("DELETE FROM handoff_events")
            connection.execute("DELETE FROM leases")
            connection.execute("DELETE FROM handoffs")
            rows = (
                (
                    "handoff-legacy-codex",
                    "a" * 64,
                    "events/legacy-codex",
                    AGENT,
                    REGISTRATION_ID,
                ),
                (
                    "handoff-legacy-openclaw",
                    "b" * 64,
                    "events/legacy-openclaw",
                    OC_AGENT,
                    OC_REGISTRATION_ID,
                ),
            )
            for handoff_id, idempotency_key, event_id, agent_slug, registration_id in rows:
                connection.execute(
                    """
                    INSERT INTO handoffs (
                        handoff_id, idempotency_key, task_slug, canonical_event_id,
                        canonical_version, trigger, agent_slug, executor_agent,
                        permanent_owner, delegation_slug, registration_ref, status,
                        reason, summary, correlation_id, created_at, attempt, detail
                    ) VALUES (?, ?, ?, ?, '42', 'answer_received', ?, NULL, NULL,
                        NULL, ?, 'queued', 'answer_received',
                        'A verified legacy handoff is ready.', NULL, ?, 0, NULL)
                    """,
                    (
                        handoff_id,
                        idempotency_key,
                        TASK,
                        event_id,
                        agent_slug,
                        hashlib.sha256(registration_id.encode()).hexdigest(),
                        NOW.isoformat(),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO leases (
                        handoff_id, registration_id, registration_agent_slug,
                        registration_route, lease_until, lease_capability_ref,
                        lease_generation
                    ) VALUES (?, ?, ?, 'hosts/tammy', NULL, NULL, 0)
                    """,
                    (handoff_id, registration_id, agent_slug),
                )
            connection.commit()

    def test_concurrent_startup_backfills_one_legacy_task_fence_and_quarantines_conflict(self) -> None:
        self._seed_two_unfenced_legacy_handoffs()
        barrier = threading.Barrier(2)

        def open_store(_index: int) -> DurableHandoffStore:
            barrier.wait()
            return DurableHandoffStore(self.path)

        with ThreadPoolExecutor(max_workers=2) as executor:
            stores = list(executor.map(open_store, range(2)))
        try:
            with sqlite3.connect(self.path) as inspection:
                active_claims = inspection.execute(
                    "SELECT handoff_id, executor_agent FROM execution_claims WHERE terminal_state IS NULL"
                ).fetchall()
                statuses = inspection.execute(
                    "SELECT status FROM handoffs ORDER BY handoff_id"
                ).fetchall()

            self.assertEqual(len(active_claims), 1)
            self.assertEqual(
                sorted(status for (status,) in statuses),
                ["dead_letter", "queued"],
            )
            winner_registration = (
                OC_REGISTRATION_ID
                if active_claims[0][1] == OC_AGENT
                else REGISTRATION_ID
            )
            claim = stores[0].claim(
                winner_registration,
                now=NOW,
                lease_seconds=30,
            )
            self.assertIsNotNone(claim)
            self.assertEqual(claim.task_slug, TASK)
        finally:
            for store in stores:
                store.close()

    def test_unversioned_legacy_delegation_is_quarantined_instead_of_downgraded_to_owned(self) -> None:
        DurableHandoffStore(self.path).close()
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO handoffs (
                    handoff_id, idempotency_key, task_slug, canonical_event_id,
                    canonical_version, trigger, agent_slug, executor_agent,
                    permanent_owner, delegation_slug, registration_ref, status,
                    reason, summary, correlation_id, created_at, attempt, detail
                ) VALUES (
                    'handoff-legacy-delegated', ?, ?, 'events/legacy-delegated',
                    '42', 'answer_received', ?, ?, ?, ?, ?, 'queued',
                    'answer_received', 'A verified legacy handoff is ready.',
                    'correlation-legacy-delegated', ?, 0, NULL
                )
                """,
                (
                    "c" * 64,
                    TASK,
                    OC_AGENT,
                    OC_AGENT,
                    AGENT,
                    DELEGATION_SLUG,
                    hashlib.sha256(OC_REGISTRATION_ID.encode()).hexdigest(),
                    NOW.isoformat(),
                ),
            )
            connection.execute(
                """
                INSERT INTO leases (
                    handoff_id, registration_id, registration_agent_slug,
                    registration_route, lease_until, lease_capability_ref,
                    lease_generation
                ) VALUES (
                    'handoff-legacy-delegated', ?, ?, 'hosts/tammy', NULL, NULL, 0
                )
                """,
                (OC_REGISTRATION_ID, OC_AGENT),
            )
            connection.commit()

        store = DurableHandoffStore(self.path)
        try:
            self.assertEqual(
                store.get("handoff-legacy-delegated").status,
                "dead_letter",
            )
            self.assertIsNone(store.get_execution_claim(TASK, include_terminal=True))
        finally:
            store.close()

    def test_failed_schema_upgrade_rolls_back_every_ddl_and_backfill_change(self) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TABLE handoffs (handoff_id TEXT PRIMARY KEY)")
            connection.commit()

        with self.assertRaises(sqlite3.OperationalError):
            DurableHandoffStore(self.path)

        with sqlite3.connect(self.path) as inspection:
            tables = {
                row[0]
                for row in inspection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
                if not row[0].startswith("sqlite_")
            }
            columns = [
                row[1] for row in inspection.execute("PRAGMA table_info(handoffs)")
            ]
        self.assertEqual(tables, {"handoffs"})
        self.assertEqual(columns, ["handoff_id"])

    def test_concurrent_empty_store_startup_is_serialized_and_complete(self) -> None:
        barrier = threading.Barrier(2)

        def open_store(_index: int) -> DurableHandoffStore:
            barrier.wait()
            return DurableHandoffStore(self.path)

        with ThreadPoolExecutor(max_workers=2) as executor:
            stores = list(executor.map(open_store, range(2)))
        try:
            with sqlite3.connect(self.path) as inspection:
                self.assertEqual(
                    inspection.execute("PRAGMA integrity_check").fetchone()[0],
                    "ok",
                )
                self.assertIn(
                    "execution_claims",
                    {
                        row[0]
                        for row in inspection.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table'"
                        )
                    },
                )
        finally:
            for store in stores:
                store.close()


if __name__ == "__main__":
    unittest.main()
