from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import tempfile
from pathlib import Path
from contextlib import contextmanager

from gtasks.delegation import AgentDelegationLease, DelegationState
from gtasks.handoff_dispatcher import (
    ActionableChange,
    AgentRegistration,
    DurableHandoffStore,
    ExecutionClaim,
    HandoffDispatcher,
)
from tests.test_server import ArtifactApiTests, FakeAdapter, ServerHarness


class _ClaimStore:
    def __init__(self, claim: ExecutionClaim) -> None:
        self.claim = claim

    def get_execution_claim(self, task_slug: str, *, include_terminal: bool = False):
        if task_slug == self.claim.task_slug and (
            include_terminal or self.claim.terminal_state is None
        ):
            return self.claim
        return None

    def observe_delegation_authority(self, lease, *, observed_at) -> None:
        return None

    @contextmanager
    def reserve_artifact_publication(self, task_slug: str, **_kwargs):
        claim = self.get_execution_claim(task_slug, include_terminal=True)
        if claim is None or claim.requested_operation != "artifact":
            raise ValueError("artifact claim is unavailable")
        yield claim


class _DelegationAwareAdapter(FakeAdapter):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.execution_claims = []

    def create_agent_artifact(
        self,
        artifact,
        *,
        executing_agent: str,
        idempotency_key: str,
        execution_claim=None,
    ):
        self.execution_claims.append(execution_claim)
        return super().create_agent_artifact(
            artifact,
            executing_agent=executing_agent,
            idempotency_key=idempotency_key,
        )


class OpenClawArtifactPublisherTests(unittest.TestCase):
    NOW = datetime(2026, 8, 9, 15, 0, tzinfo=timezone.utc)
    TASK = "tasks/561640dd-8e34-43e1-a03e-e3f3f270033d"
    DELEGATION = "agent-delegations/22222222-2222-4222-8222-222222222222"

    _execution_headers = staticmethod(ArtifactApiTests._execution_headers)

    def _publish_payload(self) -> dict:
        return ArtifactApiTests._publish_payload(self)

    def _delegation(self) -> AgentDelegationLease:
        return AgentDelegationLease(
            slug=self.DELEGATION,
            source_agent="agents/tammy",
            executor_agent="agents/tammy-oc",
            authorized_by="people/tony-guan",
            starts_at=self.NOW - timedelta(hours=1),
            ends_at=self.NOW + timedelta(hours=1),
            display_timezone="America/Los_Angeles",
            allowed_operations=("artifact",),
            state=DelegationState.ACTIVE,
            created_at=self.NOW - timedelta(hours=1),
            updated_at=self.NOW - timedelta(hours=1),
        )

    def _claim(self, **overrides) -> ExecutionClaim:
        values = {
            "task_slug": self.TASK,
            "executor_agent": "agents/tammy-oc",
            "permanent_owner": "agents/tammy",
            "delegation_slug": self.DELEGATION,
            "correlation_id": "correlation-artifact",
            "idempotency_key": "a" * 64,
            "claimed_at": self.NOW - timedelta(minutes=2),
            "expires_at": self.NOW + timedelta(minutes=10),
            "requested_operation": "artifact",
        }
        values.update(overrides)
        return ExecutionClaim(**values)

    def _delegated_payload(self) -> dict:
        return {
            **self._publish_payload(),
            "created_by": "agents/tammy-oc",
            "delegation_ref": self.DELEGATION,
        }

    def _real_store(self, *, requested_operation: str):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store = DurableHandoffStore(str(Path(directory.name) / "handoffs.sqlite3"))
        self.addCleanup(store.close)
        lease = self._delegation()
        registrations = (
            AgentRegistration(
                registration_id="registration-tammy",
                agent_slug="agents/tammy",
                route="hosts/tammy",
                verified=True,
            ),
            AgentRegistration(
                registration_id="registration-tammy-oc",
                agent_slug="agents/tammy-oc",
                route="hosts/tammy",
                verified=True,
            ),
        )
        dispatcher = HandoffDispatcher(
            store,
            registrations=registrations,
            delegations=(lease,),
            owned_work_ready=lambda _executor: False,
        )
        dispatcher.record(
            ActionableChange(
                task_slug=self.TASK,
                canonical_event_id="events/artifact-publication",
                canonical_version="42",
                trigger="answer_received",
                assigned_to=("agents/tammy",),
                route="hosts/tammy",
                summary="Delegated work is ready.",
                occurred_at=self.NOW,
                correlation_id="correlation-artifact-publication",
                blocker=None,
                task_status="planned",
                requested_operation=requested_operation,
            ),
            now=self.NOW,
        )
        return store, lease

    def test_verified_active_claim_publishes_with_private_delegation_provenance(self) -> None:
        adapter = _DelegationAwareAdapter(delegations=(self._delegation(),))
        harness = ServerHarness(
            self,
            adapter,
            handoff_store=_ClaimStore(self._claim()),
            clock=lambda: self.NOW,
        )

        status, payload, _ = harness.request(
            "POST",
            "/api/artifacts",
            self._delegated_payload(),
            self._execution_headers("agents/tammy-oc"),
        )

        self.assertEqual(status, 201)
        self.assertEqual(payload["artifact"]["created_by"], "agents/tammy-oc")
        self.assertEqual(
            payload["artifact"]["agent_collection"],
            "collections/tammy-oc-artifacts",
        )
        self.assertEqual(payload["artifact"]["delegation_ref"], self.DELEGATION)
        self.assertEqual(len(adapter.execution_claims), 1)
        proof = adapter.execution_claims[0]
        self.assertEqual(proof.task_slug, self.TASK)
        self.assertEqual(proof.executor_agent, "agents/tammy-oc")
        self.assertEqual(proof.permanent_owner, "agents/tammy")
        self.assertFalse(hasattr(proof, "lease_capability_ref"))
        self.assertNotIn("token", repr(proof).lower())

    def test_claim_mismatch_rejects_before_artifact_adapter_write(self) -> None:
        cases = (
            self._claim(task_slug="tasks/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            self._claim(executor_agent="agents/timmy-oc"),
            self._claim(permanent_owner="agents/timmy"),
            self._claim(
                delegation_slug="agent-delegations/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            ),
        )
        for claim in cases:
            with self.subTest(claim=claim):
                adapter = _DelegationAwareAdapter(delegations=(self._delegation(),))
                harness = ServerHarness(
                    self,
                    adapter,
                    handoff_store=_ClaimStore(claim),
                    clock=lambda: self.NOW,
                )
                status, payload, _ = harness.request(
                    "POST",
                    "/api/artifacts",
                    self._delegated_payload(),
                    self._execution_headers("agents/tammy-oc"),
                )
                self.assertEqual(status, 422)
                self.assertEqual(payload["code"], "invalid_delegation_claim")
                self.assertEqual(adapter.created_artifacts, [])
                self.assertEqual(adapter.execution_claims, [])

    def test_openclaw_owned_work_omits_delegation_provenance(self) -> None:
        adapter = _DelegationAwareAdapter()
        harness = ServerHarness(self, adapter, clock=lambda: self.NOW)
        body = {
            **self._publish_payload(),
            "created_by": "agents/tammy-oc",
        }

        status, payload, _ = harness.request(
            "POST",
            "/api/artifacts",
            body,
            self._execution_headers("agents/tammy-oc"),
        )

        self.assertEqual(status, 201)
        self.assertIsNone(payload["artifact"]["delegation_ref"])
        self.assertEqual(adapter.execution_claims, [None])

    def test_just_completed_claim_is_accepted_only_within_five_minutes(self) -> None:
        for age, expected_status in (
            (timedelta(minutes=4, seconds=59), 201),
            (timedelta(minutes=5, seconds=1), 422),
        ):
            with self.subTest(age=age):
                claim = self._claim(
                    claimed_at=self.NOW - timedelta(minutes=10),
                    terminal_state="completed",
                    terminal_at=self.NOW - age,
                )
                adapter = _DelegationAwareAdapter(delegations=(self._delegation(),))
                harness = ServerHarness(
                    self,
                    adapter,
                    handoff_store=_ClaimStore(claim),
                    clock=lambda: self.NOW,
                )

                status, payload, _ = harness.request(
                    "POST",
                    "/api/artifacts",
                    self._delegated_payload(),
                    self._execution_headers("agents/tammy-oc"),
                )

                self.assertEqual(status, expected_status)
                if expected_status == 201:
                    self.assertEqual(
                        adapter.execution_claims[0].completed_at,
                        claim.terminal_at,
                    )
                else:
                    self.assertEqual(payload["code"], "invalid_delegation_claim")
                    self.assertEqual(adapter.created_artifacts, [])

    def test_non_completed_terminal_claim_is_never_artifact_authority(self) -> None:
        claim = self._claim(
            terminal_state="revoked",
            terminal_at=self.NOW - timedelta(seconds=1),
        )
        adapter = _DelegationAwareAdapter(delegations=(self._delegation(),))
        harness = ServerHarness(
            self,
            adapter,
            handoff_store=_ClaimStore(claim),
            clock=lambda: self.NOW,
        )

        status, payload, _ = harness.request(
            "POST",
            "/api/artifacts",
            self._delegated_payload(),
            self._execution_headers("agents/tammy-oc"),
        )

        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "invalid_delegation_claim")
        self.assertEqual(adapter.created_artifacts, [])

    def test_real_todo_claim_under_multi_operation_lease_cannot_publish_artifact(self) -> None:
        store, lease = self._real_store(requested_operation="todo")
        adapter = _DelegationAwareAdapter(delegations=(lease,))
        harness = ServerHarness(
            self,
            adapter,
            handoff_store=store,
            clock=lambda: self.NOW + timedelta(seconds=1),
        )

        status, payload, _ = harness.request(
            "POST",
            "/api/artifacts",
            self._delegated_payload(),
            self._execution_headers("agents/tammy-oc"),
        )

        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "invalid_delegation_claim")
        self.assertEqual(adapter.execution_claims, [])
        self.assertEqual(adapter.created_artifacts, [])

    def test_terminalization_after_initial_read_fails_before_artifact_writer(self) -> None:
        store, lease = self._real_store(requested_operation="artifact")
        claim = store.get_execution_claim(self.TASK)

        class TerminalizingAdapter(_DelegationAwareAdapter):
            released = False

            def list_agent_delegations(inner_self):
                if not inner_self.released:
                    inner_self.released = True
                    store.release_execution_claim(
                        self.TASK,
                        executor_agent="agents/tammy-oc",
                        idempotency_key=claim.idempotency_key,
                        terminal_state="revoked",
                        mutation_id="mutation-artifact-race-revoke",
                        now=self.NOW + timedelta(milliseconds=1),
                    )
                return (lease,)

        adapter = TerminalizingAdapter(delegations=(lease,))
        harness = ServerHarness(
            self,
            adapter,
            handoff_store=store,
            clock=lambda: self.NOW + timedelta(seconds=1),
        )

        status, payload, _ = harness.request(
            "POST",
            "/api/artifacts",
            self._delegated_payload(),
            self._execution_headers("agents/tammy-oc"),
        )

        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "invalid_delegation_claim")
        self.assertEqual(adapter.execution_claims, [])
        self.assertEqual(adapter.created_artifacts, [])

    def test_lease_revoke_after_initial_read_is_rechecked_before_artifact_writer(self) -> None:
        store, lease = self._real_store(requested_operation="artifact")
        revoked = replace(
            lease,
            state=DelegationState.REVOKED,
            updated_at=self.NOW + timedelta(milliseconds=1),
        )

        class RevokingAdapter(_DelegationAwareAdapter):
            def __init__(inner_self):
                super().__init__(delegations=(lease,))
                inner_self.reads = 0

            def list_agent_delegations(inner_self):
                inner_self.reads += 1
                if inner_self.reads == 1:
                    inner_self.delegations = (revoked,)
                    return (lease,)
                return inner_self.delegations

        adapter = RevokingAdapter()
        harness = ServerHarness(
            self,
            adapter,
            handoff_store=store,
            clock=lambda: self.NOW + timedelta(seconds=1),
        )

        status, payload, _ = harness.request(
            "POST",
            "/api/artifacts",
            self._delegated_payload(),
            self._execution_headers("agents/tammy-oc"),
        )

        self.assertEqual(status, 422)
        self.assertEqual(payload["code"], "invalid_delegation_claim")
        self.assertGreaterEqual(adapter.reads, 2)
        self.assertEqual(adapter.execution_claims, [])
        self.assertEqual(adapter.created_artifacts, [])


if __name__ == "__main__":
    unittest.main()
