import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from gtasks.buzz_coordination import (
    AGENT_BUZZ_IDENTITIES,
    BuzzCoordinationMessage,
    BuzzCoordinationOutbox,
    BuzzDeliveryError,
    classify_inbound_coordination,
)


class BuzzCoordinationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.outbox = BuzzCoordinationOutbox(Path(self.directory.name))
        self.message = BuzzCoordinationMessage(
            task_slug="tasks/11111111-1111-4111-8111-111111111111",
            canonical_event_id="events/22222222-2222-4222-8222-222222222222",
            canonical_version="v-17",
            owner="agents/tammy",
            agent="agents/tammy",
            state="blocked",
            next_action="Review the verified implementation receipt.",
            evidence=("receipts/verified-17",),
            needs="Tony review",
        )

    def test_exact_public_identity_map_is_versioned_without_secrets(self) -> None:
        self.assertEqual(
            AGENT_BUZZ_IDENTITIES,
            {
                "agents/tammy": "3ad96d9f8a1ddb233905ac86f582d47006dabbf248f27264d5b041f50d5eb827",
                "agents/timmy": "64f1c766c8fbb16391f7cc27efc0ea0b807a4a842e64c99259ccc16bc30c3dda",
                "agents/toddy": "066a89e9f7bccff197c5ca2156284e3fe069fc41689021bbc0e2cc8aac042f8e",
            },
        )

    def test_outbox_is_durable_before_delivery_and_accepted_receipt_is_idempotent(self) -> None:
        calls = []

        def sender(command, *, input_text):
            record = json.loads(next(Path(self.directory.name).glob("*.json")).read_text())
            self.assertEqual(record["delivery_status"], "pending")
            calls.append((command, input_text))
            return {"accepted": True, "event_id": "buzz-event-17"}

        first = self.outbox.deliver(self.message, sender=sender)
        second = self.outbox.deliver(self.message, sender=sender)

        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)
        command, body = calls[0]
        self.assertIn("--mention", command)
        self.assertIn(AGENT_BUZZ_IDENTITIES["agents/tammy"], command)
        self.assertEqual(command[-2:], ["--content", "-"])
        self.assertIn('"mc_task":"tasks/11111111-1111-4111-8111-111111111111"', body)
        self.assertEqual(first["delivery_status"], "accepted")
        self.assertEqual(first["buzz_event_id"], "buzz-event-17")

    def test_unaccepted_or_cross_identity_delivery_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "verified Buzz identity"):
            self.outbox.deliver(
                replace(self.message, agent="agents/unknown"),
                sender=lambda *_args, **_kwargs: {"accepted": True, "event_id": "wrong"},
            )
        with self.assertRaises(BuzzDeliveryError):
            self.outbox.deliver(
                self.message,
                sender=lambda *_args, **_kwargs: {"accepted": False},
            )

    def test_inbound_is_allowlisted_proposal_only_and_never_a_canonical_mutation(self) -> None:
        proposal = classify_inbound_coordination(
            sender_pubkey=AGENT_BUZZ_IDENTITIES["agents/tammy"],
            payload={
                "intent": "blocked",
                "mc_task": self.message.task_slug,
                "state": "blocked",
                "next_action": "Need Tony to choose a launch date.",
                "evidence": ["receipts/verified-17"],
                "needs": "Tony decision",
            },
        )
        self.assertEqual(proposal["record_kind"], "coordination_proposal")
        self.assertEqual(proposal["agent"], "agents/tammy")
        self.assertNotIn("mutation", proposal)
        with self.assertRaisesRegex(ValueError, "allowlisted"):
            classify_inbound_coordination(
                sender_pubkey=AGENT_BUZZ_IDENTITIES["agents/tammy"],
                payload={"intent": "reassign", "mc_task": self.message.task_slug},
            )

    def test_thread_reply_keeps_explicit_mention_and_dm_opens_before_send(self) -> None:
        thread_calls = []
        self.outbox.deliver(
            self.message,
            reply_to="buzz-parent-17",
            sender=lambda command, *, input_text: (
                thread_calls.append((command, input_text))
                or {"accepted": True, "event_id": "buzz-thread-18"}
            ),
        )
        self.assertIn("--reply-to", thread_calls[0][0])
        self.assertIn("--mention", thread_calls[0][0])

        dm_outbox = BuzzCoordinationOutbox(Path(self.directory.name) / "dm")
        dm_calls = []

        def dm_sender(command, *, input_text):
            dm_calls.append((command, input_text))
            if command[1:3] == ["dms", "open"]:
                return {"channel_id": "dm-channel-17"}
            return {"accepted": True, "event_id": "buzz-dm-19"}

        dm_outbox.deliver(self.message, direct=True, sender=dm_sender)
        self.assertEqual(dm_calls[0][0][1:3], ["dms", "open"])
        self.assertEqual(dm_calls[1][0][1:3], ["messages", "send"])
        self.assertIn("dm-channel-17", dm_calls[1][0])
        self.assertIn("--mention", dm_calls[1][0])


if __name__ == "__main__":
    unittest.main()
