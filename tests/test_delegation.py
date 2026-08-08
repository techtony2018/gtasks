import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from gtasks.delegation import (
    AgentDelegationLease,
    DelegationState,
    delegated_work_is_eligible,
    lease_state_at,
    paired_openclaw_agent,
)


NOW = datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc)


def make_lease(
    *,
    slug: str = "agent-delegations/22222222-2222-4222-8222-222222222222",
    source_agent: str = "agents/tammy",
    executor_agent: str = "agents/tammy-oc",
    authorized_by: str = "people/tony-guan",
    starts_at: datetime = NOW - timedelta(minutes=5),
    ends_at: datetime = NOW + timedelta(hours=1),
    display_timezone: str = "America/Los_Angeles",
    allowed_operations: tuple[str, ...] = ("status_update", "comment"),
    state: DelegationState = DelegationState.ACTIVE,
    created_at: datetime = NOW - timedelta(minutes=10),
    updated_at: datetime = NOW - timedelta(minutes=5),
) -> AgentDelegationLease:
    return AgentDelegationLease(
        slug=slug,
        source_agent=source_agent,
        executor_agent=executor_agent,
        authorized_by=authorized_by,
        starts_at=starts_at,
        ends_at=ends_at,
        display_timezone=display_timezone,
        allowed_operations=allowed_operations,
        state=state,
        created_at=created_at,
        updated_at=updated_at,
    )


def active_tammy_lease() -> AgentDelegationLease:
    return make_lease()


class AgentDelegationLeaseValidationTests(unittest.TestCase):
    def test_accepts_each_fixed_codex_openclaw_pair(self) -> None:
        self.assertEqual(paired_openclaw_agent("agents/tammy"), "agents/tammy-oc")
        self.assertEqual(paired_openclaw_agent("agents/timmy"), "agents/timmy-oc")
        self.assertEqual(paired_openclaw_agent("agents/toddy"), "agents/toddy-oc")
        self.assertEqual(make_lease().executor_agent, "agents/tammy-oc")

    def test_rejects_unknown_or_openclaw_source_pair(self) -> None:
        for source_agent in ("agents/tammy-oc", "agents/unknown", "agents/tammy "):
            with self.subTest(source_agent=source_agent):
                with self.assertRaisesRegex(ValueError, "paired Codex Agent"):
                    paired_openclaw_agent(source_agent)

    def test_rejects_wrong_executor_for_source_agent(self) -> None:
        with self.assertRaisesRegex(ValueError, "fixed paired OpenClaw Agent"):
            make_lease(executor_agent="agents/timmy-oc")

    def test_rejects_non_tony_authorizer(self) -> None:
        with self.assertRaisesRegex(ValueError, "Tony"):
            make_lease(authorized_by="agents/tammy")

    def test_rejects_noncanonical_lease_slug(self) -> None:
        with self.assertRaisesRegex(ValueError, "canonical UUID"):
            make_lease(slug="agent-delegations/tammy-delegation")

    def test_rejects_naive_instants_and_normalizes_aware_instants_to_utc(self) -> None:
        naive = NOW.replace(tzinfo=None)
        pacific = NOW.astimezone(timezone(timedelta(hours=-7)))
        for field, value in (
            ("starts_at", naive),
            ("ends_at", naive),
            ("created_at", naive),
            ("updated_at", naive),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaisesRegex(ValueError, "aware UTC"):
                    make_lease(**{field: value})
        self.assertEqual(make_lease(starts_at=pacific).starts_at, NOW)

    def test_custom_duration_is_bounded_and_dst_safe(self) -> None:
        lease = make_lease(starts_at=NOW, ends_at=NOW + timedelta(hours=8))
        self.assertEqual(lease.display_timezone, "America/Los_Angeles")
        self.assertEqual(lease.starts_at.tzinfo, timezone.utc)
        self.assertEqual(lease.ends_at.tzinfo, timezone.utc)
        with self.assertRaisesRegex(ValueError, "15 minutes through 7 days"):
            make_lease(starts_at=NOW, ends_at=NOW + timedelta(minutes=14, seconds=59))
        with self.assertRaisesRegex(ValueError, "15 minutes through 7 days"):
            make_lease(starts_at=NOW, ends_at=NOW + timedelta(days=8))

    def test_rejects_an_end_not_strictly_after_start(self) -> None:
        with self.assertRaisesRegex(ValueError, "strictly after"):
            make_lease(starts_at=NOW, ends_at=NOW)

    def test_rejects_a_nonstandard_display_timezone(self) -> None:
        with self.assertRaisesRegex(ValueError, "America/Los_Angeles"):
            make_lease(display_timezone="UTC")

    def test_preserves_explicit_allowed_operations_as_an_immutable_tuple(self) -> None:
        operations = ("status_update", "comment", "publish_artifact")
        lease = make_lease(allowed_operations=operations)

        self.assertEqual(lease.allowed_operations, operations)
        with self.assertRaisesRegex(ValueError, "non-empty tuple"):
            make_lease(allowed_operations=("status_update", "",))
        with self.assertRaisesRegex(ValueError, "non-empty tuple"):
            make_lease(allowed_operations=["status_update"])  # type: ignore[arg-type]


class DelegationLifecycleTests(unittest.TestCase):
    def test_scheduled_active_and_expired_states_follow_utc_instants(self) -> None:
        lease = make_lease(
            starts_at=NOW + timedelta(minutes=15),
            ends_at=NOW + timedelta(hours=1),
            state=DelegationState.SCHEDULED,
        )

        self.assertEqual(lease_state_at(lease, NOW), DelegationState.SCHEDULED)
        self.assertEqual(
            lease_state_at(lease, NOW + timedelta(minutes=15)),
            DelegationState.ACTIVE,
        )
        self.assertEqual(
            lease_state_at(lease, NOW + timedelta(hours=1)),
            DelegationState.EXPIRED,
        )

    def test_completed_and_revoked_states_are_terminal(self) -> None:
        completed = make_lease(state=DelegationState.COMPLETED)
        revoked = make_lease(state=DelegationState.REVOKED)

        self.assertEqual(lease_state_at(completed, NOW), DelegationState.COMPLETED)
        self.assertEqual(lease_state_at(revoked, NOW), DelegationState.REVOKED)

    def test_explicit_expiry_remains_terminal(self) -> None:
        expired = make_lease(
            ends_at=NOW + timedelta(hours=1),
            state=DelegationState.EXPIRED,
        )

        self.assertEqual(lease_state_at(expired, NOW), DelegationState.EXPIRED)

    def test_extension_uses_a_new_approved_lease_value_and_restores_active_time(self) -> None:
        expired = make_lease(
            starts_at=NOW - timedelta(hours=1),
            ends_at=NOW,
            updated_at=NOW,
            state=DelegationState.ACTIVE,
        )
        extended = replace(
            expired,
            ends_at=NOW + timedelta(hours=2),
            updated_at=NOW + timedelta(minutes=1),
        )

        self.assertEqual(lease_state_at(expired, NOW), DelegationState.EXPIRED)
        self.assertEqual(lease_state_at(extended, NOW), DelegationState.ACTIVE)
        self.assertEqual(extended.created_at, expired.created_at)

    def test_rejects_naive_now(self) -> None:
        with self.assertRaisesRegex(ValueError, "aware UTC"):
            lease_state_at(make_lease(), NOW.replace(tzinfo=None))


class DelegatedWorkEligibilityTests(unittest.TestCase):
    def test_owned_work_always_prevents_delegated_claim(self) -> None:
        self.assertFalse(delegated_work_is_eligible(
            owned_work_ready=True,
            task_status="planned",
            task_owner="agents/tammy",
            lease=active_tammy_lease(),
            now=NOW,
        ))

    def test_only_unstarted_work_for_the_permanent_source_owner_is_eligible(self) -> None:
        lease = active_tammy_lease()
        self.assertTrue(delegated_work_is_eligible(
            owned_work_ready=False,
            task_status="planned",
            task_owner="agents/tammy",
            lease=lease,
            now=NOW,
        ))
        for task_status in ("active", "blocked", "completed", "cancelled", "proposed"):
            with self.subTest(task_status=task_status):
                self.assertFalse(delegated_work_is_eligible(
                    owned_work_ready=False,
                    task_status=task_status,
                    task_owner="agents/tammy",
                    lease=lease,
                    now=NOW,
                ))
        self.assertFalse(delegated_work_is_eligible(
            owned_work_ready=False,
            task_status="planned",
            task_owner="agents/tammy-oc",
            lease=lease,
            now=NOW,
        ))

    def test_scheduled_expired_completed_and_revoked_leases_cannot_claim(self) -> None:
        for lease in (
            make_lease(
                starts_at=NOW + timedelta(minutes=15),
                ends_at=NOW + timedelta(hours=1),
                state=DelegationState.SCHEDULED,
            ),
            make_lease(
                starts_at=NOW - timedelta(hours=1),
                ends_at=NOW,
                state=DelegationState.ACTIVE,
            ),
            make_lease(state=DelegationState.COMPLETED),
            make_lease(state=DelegationState.REVOKED),
        ):
            with self.subTest(state=lease.state):
                self.assertFalse(delegated_work_is_eligible(
                    owned_work_ready=False,
                    task_status="planned",
                    task_owner="agents/tammy",
                    lease=lease,
                    now=NOW,
                ))

    def test_busy_lease_can_have_zero_delegated_claims_without_becoming_invalid(self) -> None:
        lease = active_tammy_lease()

        self.assertEqual(lease_state_at(lease, NOW), DelegationState.ACTIVE)
        self.assertFalse(delegated_work_is_eligible(
            owned_work_ready=True,
            task_status="planned",
            task_owner="agents/tammy",
            lease=lease,
            now=NOW,
        ))


if __name__ == "__main__":
    unittest.main()
