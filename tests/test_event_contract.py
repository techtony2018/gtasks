import json
import unittest

from gtasks.event_queue.contract import (
    ContractError,
    HandlerRegistry,
    JobAppliedV1,
    parse_event,
)


def valid_event() -> dict:
    return {
        "event_id": "evt_01K1E9CXH1R3SX",
        "idempotency_key": "career-path:linkedin:job-42:applied",
        "event_type": "job_applied",
        "schema_version": 1,
        "source": {
            "client_id": "career-path",
            "instance_id": "tony-mac",
        },
        "occurred_at": "2026-07-30T09:42:00-07:00",
        "timezone": "America/Los_Angeles",
        "payload": {
            "application_identity": {
                "job_source": "linkedin",
                "job_id": "job-42",
            },
            "job_snapshot": {
                "title": "Engineering Manager",
                "company": "Example",
                "location": "San Francisco, CA",
                "url": "https://www.linkedin.com/jobs/view/job-42",
            },
            "applied_local_date": "2026-07-30",
            "status_evidence": {
                "status": "applied",
                "committed_at": "2026-07-30T09:41:58-07:00",
                "source": "career-path-local-store",
            },
        },
    }


class EventContractTests(unittest.TestCase):
    def test_parses_exact_job_applied_v1_contract(self) -> None:
        event = parse_event(json.dumps(valid_event()).encode(), "gtasks.events.job_applied.v1")

        self.assertIsInstance(event, JobAppliedV1)
        self.assertEqual(event.payload.application_identity.job_id, "job-42")

    def test_rejects_unknown_top_level_field(self) -> None:
        raw = valid_event()
        raw["command"] = "do anything"

        with self.assertRaisesRegex(ContractError, "unknown envelope field"):
            parse_event(json.dumps(raw).encode(), "gtasks.events.job_applied.v1")

    def test_rejects_unknown_payload_field(self) -> None:
        raw = valid_event()
        raw["payload"]["gbrain_command"] = "put_page"

        with self.assertRaisesRegex(ContractError, "unknown payload field"):
            parse_event(json.dumps(raw).encode(), "gtasks.events.job_applied.v1")

    def test_rejects_naive_timestamp(self) -> None:
        raw = valid_event()
        raw["occurred_at"] = "2026-07-30T09:42:00"

        with self.assertRaisesRegex(ContractError, "timezone offset"):
            parse_event(json.dumps(raw).encode(), "gtasks.events.job_applied.v1")

    def test_rejects_invalid_iana_timezone(self) -> None:
        raw = valid_event()
        raw["timezone"] = "PDT"

        with self.assertRaisesRegex(ContractError, "IANA timezone"):
            parse_event(json.dumps(raw).encode(), "gtasks.events.job_applied.v1")

    def test_rejects_source_not_authorized_for_subject(self) -> None:
        raw = valid_event()
        raw["source"]["client_id"] = "other-producer"

        with self.assertRaisesRegex(ContractError, "source client"):
            parse_event(json.dumps(raw).encode(), "gtasks.events.job_applied.v1")

    def test_rejects_subject_type_version_mismatch(self) -> None:
        with self.assertRaisesRegex(ContractError, "subject"):
            parse_event(json.dumps(valid_event()).encode(), "gtasks.events.job_applied.v2")

    def test_rejects_unknown_handler_without_fallback(self) -> None:
        registry = HandlerRegistry()
        registry.register("job_applied", 1, object())

        with self.assertRaisesRegex(ContractError, "unsupported event type/version"):
            registry.resolve("job_applied", 2)

    def test_fingerprint_is_stable_across_json_key_order(self) -> None:
        first = parse_event(json.dumps(valid_event()).encode(), "gtasks.events.job_applied.v1")
        second = parse_event(
            json.dumps(valid_event(), sort_keys=True).encode(),
            "gtasks.events.job_applied.v1",
        )

        self.assertEqual(first.fingerprint, second.fingerprint)


if __name__ == "__main__":
    unittest.main()
