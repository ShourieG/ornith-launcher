from __future__ import annotations

import json
import unittest

from ornith_launcher.catalog.hfapi import HubClient, HubError, Response


class FakeTransport:
    """Answers GETs from a list of queued responses and records each request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def get(self, url, headers, private_headers, limit=None):
        self.requests.append((url, dict(headers), dict(private_headers), limit))
        return self.responses.pop(0)


def reply(status=200, body=b"[]", **headers) -> Response:
    if not isinstance(body, bytes):
        body = json.dumps(body).encode()
    return Response(status, {k.replace("_", "-"): v for k, v in headers.items()}, body, "")


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(seconds))
        self.now += seconds


def client(transport, clock=None, **kwargs) -> HubClient:
    clock = clock or Clock()
    return HubClient(transport=transport, clock=clock, sleep=clock.sleep, **kwargs)


class HubClientTest(unittest.TestCase):
    def test_follows_link_pagination(self):
        transport = FakeTransport(
            [
                reply(
                    body=[{"id": "a/1"}], link='<https://huggingface.co/api/models?c=2>; rel="next"'
                ),
                reply(body=[{"id": "b/2"}]),
            ]
        )
        found = list(client(transport).list_models([("search", "Ornith")]))
        self.assertEqual([m["id"] for m in found], ["a/1", "b/2"])
        self.assertEqual(
            transport.requests[0][0], "https://huggingface.co/api/models?search=Ornith"
        )
        self.assertEqual(transport.requests[1][0], "https://huggingface.co/api/models?c=2")

    def test_pauses_before_the_rate_limit_runs_out(self):
        clock = Clock()
        transport = FakeTransport(
            [reply(ratelimit='"api";r=3;t=40'), reply(ratelimit='"api";r=499;t=300')]
        )
        hub = client(transport, clock)
        hub.get_json("/api/models/a/b")
        hub.get_json("/api/models/a/b")
        self.assertEqual(clock.slept, [41])

    def test_retries_after_429_using_the_reset_time(self):
        clock = Clock()
        waits = []
        transport = FakeTransport([reply(429, ratelimit='"api";r=0;t=12'), reply(body={"ok": 1})])
        hub = client(transport, clock, on_wait=lambda s, why: waits.append(why))
        self.assertEqual(hub.get_json("/api/models/x/y"), {"ok": 1})
        self.assertEqual(clock.slept, [13])
        self.assertIn("HTTP 429", waits[0])

    def test_gives_up_after_repeated_server_errors(self):
        hub = client(FakeTransport([reply(503)] * 5))
        with self.assertRaisesRegex(HubError, "kept refusing"):
            hub.get_json("/api/models/x/y")

    def test_token_is_sent_only_as_a_private_header(self):
        transport = FakeTransport([reply()])
        client(transport, token="hf_secret").get_json("/api/models")
        url, headers, private, _ = transport.requests[0]
        self.assertNotIn("Authorization", headers)
        self.assertEqual(private, {"Authorization": "Bearer hf_secret"})
        self.assertIn("unofficial", headers["User-Agent"])

    def test_range_reader_caps_the_body(self):
        transport = FakeTransport([reply(206, body=b"GGUF" + b"\0" * 100)])
        fetch = client(transport).range_reader("o/r", "abc123", "dir/model Q8.gguf")
        self.assertEqual(len(fetch(16)), 16)
        url, headers, _, limit = transport.requests[0]
        self.assertEqual(url, "https://huggingface.co/o/r/resolve/abc123/dir/model%20Q8.gguf")
        self.assertEqual(headers["Range"], "bytes=0-15")
        self.assertEqual(limit, 16)

    def test_errors_name_the_cause(self):
        for status, message in ((401, "login"), (404, "not found"), (418, "HTTP 418")):
            with self.subTest(status=status):
                with self.assertRaisesRegex(HubError, message):
                    client(FakeTransport([reply(status)])).get_json("/api/models/x/y")

    def test_endpoint_and_token_from_environment(self):
        hub = HubClient.from_env({"HF_ENDPOINT": "https://mirror.example/", "HF_TOKEN": "t"})
        self.assertEqual(hub.endpoint, "https://mirror.example")
        self.assertEqual(hub.token, "t")
        self.assertIsNone(HubClient.from_env({}).token)


if __name__ == "__main__":
    unittest.main()
