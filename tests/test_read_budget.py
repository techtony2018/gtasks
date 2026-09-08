import json
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request

from gtasks.gbrain import RemoteHttpCommandRunner
from gtasks.read_budget import ReadBudget, ReadDeadlineExceeded, check_budget, current_budget, use_budget


class Response:
    headers = {"Content-Type": "application/json"}
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        return False
    def read(self):
        return json.dumps(self.payload).encode()


REMOTE = {"issuer_url": "https://synthetic.test", "mcp_url": "https://synthetic.test/mcp",
          "oauth_client_id": "synthetic", "oauth_client_secret": "synthetic"}


class RemoteBudgetTests(unittest.TestCase):
    def test_precreated_child_inherits_entry_parent_deadline_cancellation_and_restores_context(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                now = [0.0]
                child = ReadBudget(10, clock=lambda: now[0])
                parent = ReadBudget(1, clock=lambda: now[0])
                with use_budget(parent):
                    before = current_budget()
                    with use_budget(child):
                        if cancel:
                            parent.cancelled.set()
                        else:
                            now[0] = 2
                        with self.assertRaises(ReadDeadlineExceeded):
                            check_budget()
                    self.assertIs(current_budget(), before)
                self.assertIsNone(current_budget())
                # Reusing the child outside that parent must not retain an
                # injected parent or create an ancestry cycle.
                with use_budget(child):
                    check_budget()

    def test_socket_timeout_at_overall_deadline_keeps_deadline_classification(self):
        now = [0.0]
        def expired_socket(*_args, **_kwargs):
            now[0] = 1
            raise TimeoutError("synthetic socket timeout")
        with patch("gtasks.gbrain.urlopen", side_effect=expired_socket):
            with use_budget(ReadBudget(0.5, clock=lambda: now[0])):
                with self.assertRaises(Exception) as raised:
                    RemoteHttpCommandRunner()._read_json_response(Request("https://synthetic.test/mcp"))
                self.assertIsInstance(raised.exception, ReadDeadlineExceeded)

    def test_nested_read_budget_cannot_extend_or_detach_parent_deadline(self):
        now = [0.0]
        parent = ReadBudget(1, clock=lambda: now[0])
        with use_budget(parent):
            nested = ReadBudget(20, clock=lambda: now[0])
            now[0] = 2
            with self.assertRaises(TimeoutError):
                with use_budget(nested):
                    pass

    def test_held_canonical_lane_obeys_budget_without_token_or_call(self):
        runner = RemoteHttpCommandRunner()
        runner._lane_active = True
        with patch.object(runner, "_remote_config", return_value=REMOTE), \
             patch("gtasks.gbrain.urlopen") as request:
            try:
                started = time.monotonic()
                with use_budget(ReadBudget(0.04)):
                    with self.assertRaises(TimeoutError):
                        runner.run("get_page", {"slug": "tasks/synthetic"})
                self.assertLess(time.monotonic() - started, 0.15)
                request.assert_not_called()
            finally:
                with runner._lane_condition:
                    runner._lane_active = False
                    runner._lane_condition.notify_all()

    def test_held_oauth_lock_consumes_budget_without_canonical_access(self):
        runner = RemoteHttpCommandRunner()
        runner._token = "synthetic"
        runner._token_expires_at = time.time() + 3600
        runner._token_lock.acquire()
        timer = threading.Timer(0.3, runner._token_lock.release)
        timer.start()
        try:
            with patch("gtasks.gbrain.urlopen") as request:
                started = time.monotonic()
                with use_budget(ReadBudget(0.05)):
                    with self.assertRaises(TimeoutError):
                        runner._access_token(REMOTE)
                self.assertLess(time.monotonic() - started, 0.2)
                request.assert_not_called()
        finally:
            timer.join(1)

    def test_discovery_token_and_canonical_calls_share_one_deadline(self):
        runner = RemoteHttpCommandRunner()
        calls = []
        now = [0.0]
        def respond(request, *, timeout):
            calls.append((request.full_url, timeout))
            now[0] += 0.04
            if request.full_url.endswith("oauth-authorization-server"):
                return Response({"token_endpoint": "https://synthetic.test/token"})
            if request.full_url.endswith("/token"):
                return Response({"access_token": "synthetic", "expires_in": 3600})
            return Response({"result": {"structuredContent": {"ok": True}}})
        with patch.object(runner, "_remote_config", return_value=REMOTE), \
             patch("gtasks.gbrain.urlopen", side_effect=respond):
            started = time.monotonic()
            with use_budget(ReadBudget(0.1, clock=lambda: now[0])):
                with self.assertRaises(TimeoutError):
                    runner.run("get_page", {"slug": "tasks/synthetic"})
            self.assertLess(time.monotonic() - started, 0.16)
        self.assertEqual(len(calls), 3)
        self.assertLess(calls[1][1], calls[0][1])
        self.assertLess(calls[2][1], 0.025)

    def test_expired_budget_does_not_begin_discovery_or_canonical_call(self):
        runner = RemoteHttpCommandRunner()
        budget = ReadBudget(0.01)
        runner._token = "synthetic"
        runner._token_expires_at = time.time() + 3600
        with patch.object(runner, "_remote_config", return_value=REMOTE), \
             patch("gtasks.gbrain.urlopen", return_value=Response(
                 {"result": {"structuredContent": {"ok": True}}})) as request:
            with use_budget(budget):
                time.sleep(0.02)
                with self.assertRaises(TimeoutError):
                    runner.run("get_page", {"slug": "tasks/synthetic"})
            request.assert_not_called()

    def test_streaming_body_cannot_extend_refresh_with_successive_chunks(self):
        class StreamingResponse(Response):
            def read1(self, _size):
                time.sleep(0.02)
                return b" "
        runner = RemoteHttpCommandRunner()
        with patch("gtasks.gbrain.urlopen", return_value=StreamingResponse({"ok": True})):
            started = time.monotonic()
            with use_budget(ReadBudget(0.05)):
                with self.assertRaises(TimeoutError):
                    runner._read_json_response(Request("https://synthetic.test/mcp"))
            self.assertLess(time.monotonic() - started, 0.15)


if __name__ == "__main__":
    unittest.main()
