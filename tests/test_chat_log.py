from __future__ import annotations

import http.client
import io
import json
import socket
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ornith_launcher import chat_log
from ornith_launcher.app import App
from ornith_launcher.config import resolve_paths
from ornith_launcher.runtime.chat_capture import ChatView, Reply, api_of, clip, request_event
from ornith_launcher.runtime.chat_proxy import TOKEN_HEADER, VIEW_PATH, ChatProxy, History
from ornith_launcher.runtime.docker import DockerRuntime
from tests.runtime_fixtures import FakeDocker, FakeHttp, profile


def sse(*chunks) -> bytes:
    return b"".join(
        b"data: " + (c if isinstance(c, bytes) else json.dumps(c).encode()) + b"\n\n"
        for c in chunks
    )


def render(events) -> str:
    view = ChatView()
    return "".join(view.feed(e) for e in events)


class CaptureTest(unittest.TestCase):
    def test_only_chat_requests_are_shown(self):
        self.assertEqual(api_of("POST", "/v1/chat/completions"), "openai")
        self.assertEqual(api_of("POST", "/v1/messages?beta=true"), "anthropic")
        self.assertEqual(api_of("POST", "/completion"), "completion")
        self.assertIsNone(api_of("GET", "/v1/chat/completions"))
        self.assertIsNone(api_of("POST", "/v1/messages/count_tokens"))
        self.assertIsNone(api_of("GET", "/health"))

    def test_an_openai_request_shows_the_newest_user_message(self):
        body = {
            "model": "ornith",
            "stream": True,
            "tools": [{"type": "function"}],
            "messages": [
                {"role": "system", "content": "a long system prompt"},
                {"role": "user", "content": "first question"},
                {"role": "assistant", "content": "first answer"},
                {"role": "tool", "content": "line 1\nline 2"},
                {"role": "user", "content": [{"type": "text", "text": "and now?"}]},
            ],
        }
        event = request_event(3, "12:00:00", "/v1/chat/completions", "openai", _json(body))
        self.assertEqual(event["summary"], "5 messages, 1 tool, streamed, model ornith")
        self.assertEqual(event["parts"], [["Tool result", "line 1\nline 2"], ["You", "and now?"]])
        self.assertNotIn("system prompt", json.dumps(event))

    def test_an_anthropic_request_with_tool_results_images_and_text(self):
        body = {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "content": [{"type": "text", "text": "ok"}]},
                        {"type": "image", "source": {}},
                        {"type": "text", "text": "what is in the picture?"},
                    ],
                },
            ]
        }
        event = request_event(1, "t", "/v1/messages", "anthropic", _json(body))
        self.assertEqual(
            event["parts"],
            [["Tool result", "ok"], ["You", "[an image]"], ["You", "what is in the picture?"]],
        )

    def test_long_text_is_clipped_and_bad_bodies_are_harmless(self):
        self.assertEqual(clip("a\nb\nc\nd", 2), "a\nb\n... (2 more lines)")
        event = request_event(1, "t", "/completion", "completion", b"not json")
        self.assertEqual((event["summary"], event["parts"]), ("", []))
        prompt = request_event(2, "t", "/completion", "completion", _json({"prompt": "p"}))
        self.assertEqual(prompt["parts"], [["Prompt", "p"]])

    def test_an_openai_stream_with_thinking_tool_calls_and_timings(self):
        reply = Reply(1, "openai", "text/event-stream")
        data = sse(
            {"choices": [{"delta": {"reasoning_content": "Let me look."}}]},
            {"choices": [{"delta": {"content": "Reading it."}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "read"}}]}}]},
            {
                "choices": [
                    {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"p":'}}]}}
                ]
            },
            {
                "choices": [
                    {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"x"}'}}]}}
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            {"timings": {"predicted_n": 12, "predicted_per_second": 51.25}, "choices": []},
            b"[DONE]",
        )
        events = []
        for i in range(0, len(data), 7):  # bytes arrive in small pieces
            events += reply.feed(data[i : i + 7])
        events += reply.finish(200, 2.04)
        self.assertEqual(
            [(e["type"], e.get("kind"), e.get("text"), e.get("label")) for e in events],
            [
                ("text", "thinking", "Let me look.", None),
                ("text", "reply", "Reading it.", None),
                ("text", "tool", "", "read"),
                ("text", "tool", '{"p":', None),
                ("text", "tool", '"x"}', None),
                ("end", None, None, None),
            ],
        )
        self.assertEqual(
            events[-1],
            {
                "type": "end",
                "id": 1,
                "status": 200,
                "seconds": 2.0,
                "tokens": 12,
                "rate": 51.2,
                "stop": "tool_calls",
            },
        )

    def test_an_anthropic_stream(self):
        reply = Reply(2, "anthropic", "text/event-stream; charset=utf-8")
        data = (
            b"event: content_block_start\n"
            + sse({"type": "content_block_start", "content_block": {"type": "thinking"}})
            + sse(
                {
                    "type": "content_block_delta",
                    "delta": {"type": "thinking_delta", "thinking": "hmm"},
                }
            )
            + sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}})
            + sse(
                {
                    "type": "content_block_start",
                    "content_block": {"type": "tool_use", "name": "Bash"},
                }
            )
            + sse(
                {
                    "type": "content_block_delta",
                    "delta": {"type": "input_json_delta", "partial_json": '{"cmd": "ls"}'},
                }
            )
            + sse(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use"},
                    "usage": {"output_tokens": 9},
                }
            )
        )
        events = reply.feed(data) + reply.finish(200, 1.0)
        self.assertEqual(
            [(e.get("kind"), e.get("text"), e.get("label")) for e in events[:-1]],
            [
                ("thinking", "hmm", None),
                ("reply", "Hi", None),
                ("tool", "", "Bash"),
                ("tool", '{"cmd": "ls"}', None),
            ],
        )
        self.assertEqual((events[-1]["stop"], events[-1]["tokens"]), ("tool_use", 9))

    def test_whole_replies_and_errors(self):
        openai = Reply(1, "openai", "application/json")
        body = {
            "choices": [
                {
                    "message": {
                        "content": "Hello",
                        "tool_calls": [{"function": {"name": "f", "arguments": "{}"}}],
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"completion_tokens": 4},
        }
        events = openai.feed(_json(body)[:10]) + openai.feed(_json(body)[10:])
        events += openai.finish(200, 0.5)
        self.assertEqual(
            [(e.get("kind"), e.get("text"), e.get("label")) for e in events[:-1]],
            [("reply", "Hello", None), ("tool", "{}", "f")],
        )
        self.assertEqual(events[-1]["tokens"], 4)
        anthropic = Reply(2, "anthropic", "application/json")
        anthropic.feed(_json({"content": [{"type": "tool_use", "name": "Read", "input": {}}]}))
        self.assertEqual(anthropic.finish(200, 0.1)[0]["label"], "Read")
        failed = Reply(3, "openai", "application/json")
        failed.feed(_json({"error": {"message": "the context is full"}}))
        end = failed.finish(400, 0.1)[-1]
        self.assertEqual(end["error"], "the context is full")


class ViewTest(unittest.TestCase):
    def test_a_streamed_reply_reads_as_text(self):
        text = render(
            [
                {"type": "notice", "id": 0, "text": "waiting for the first request"},
                {
                    "type": "request",
                    "id": 1,
                    "time": "12:00:00",
                    "path": "/v1/chat/completions",
                    "summary": "2 messages",
                    "parts": [["You", "two\nlines"]],
                },
                {"type": "text", "id": 1, "kind": "thinking", "text": "Hmm."},
                {"type": "text", "id": 1, "kind": "reply", "text": "Line one"},
                {"type": "text", "id": 1, "kind": "reply", "text": "\nline two\n"},
                {"type": "text", "id": 1, "kind": "tool", "text": "{}", "label": "read"},
                {"type": "end", "id": 1, "status": 200, "seconds": 1.2, "stop": "tool_calls"},
            ]
        )
        self.assertEqual(
            text,
            "(waiting for the first request)\n"
            "\n=== #1  12:00:00  /v1/chat/completions  (2 messages)\n"
            "You:\n  two\n  lines\n"
            "Thinking:\n  Hmm.\n"
            "Reply:\n  Line one\n  line two\n"
            "Tool call: read\n  {}\n"
            "--- #1 done in 1.2 s, stop: tool_calls\n",
        )

    def test_parallel_requests_say_which_one_continues(self):
        start = {"type": "request", "time": "t", "path": "/v1/messages", "parts": []}
        text = render(
            [
                {**start, "id": 1},
                {"type": "text", "id": 1, "kind": "reply", "text": "one"},
                {**start, "id": 2},
                {"type": "text", "id": 1, "kind": "reply", "text": " more"},
                {"type": "end", "id": 2, "status": 500, "seconds": 0.1, "error": "boom"},
            ]
        )
        self.assertIn("--- #1 continues\nReply:\n   more\n", text)
        self.assertIn("--- #2 done in 0.1 s, error 500: boom\n", text)


class HistoryTest(unittest.TestCase):
    def test_a_new_window_gets_recent_requests_merged_then_new_ones(self):
        history = History(keep=2)
        for exchange in (1, 2, 3):
            history.publish({"type": "request", "id": exchange, "parts": []})
            history.publish({"type": "text", "id": exchange, "kind": "reply", "text": "a"})
            history.publish({"type": "text", "id": exchange, "kind": "reply", "text": "b"})
        viewer = history.watch()
        kept = [viewer.get_nowait() for _ in range(viewer.qsize())]
        self.assertEqual([e["id"] for e in kept if e["type"] == "request"], [2, 3])
        self.assertEqual([e["text"] for e in kept if e["type"] == "text"], ["ab", "ab"])
        self.assertEqual(kept[-1]["text"], "the last 2 requests above; new ones follow")
        history.publish({"type": "end", "id": 3, "status": 200, "seconds": 1})
        self.assertEqual(viewer.get_nowait()["type"], "end")
        history.close()
        self.assertIsNone(viewer.get_nowait())

    def test_an_empty_history_says_it_waits(self):
        viewer = History().watch()
        self.assertEqual(viewer.get_nowait()["text"], "waiting for the first request")


class _Upstream(BaseHTTPRequestHandler):
    """A stand-in for llama-server: health, a streamed chat reply and a whole one."""

    protocol_version = "HTTP/1.1"
    seen: list = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.seen.append(("GET", self.path, dict(self.headers)))
        self._send(200, b'{"status": "ok"}')

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.seen.append(("POST", self.path, body))
        if json.loads(body).get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for chunk in (
                {"choices": [{"delta": {"content": "Hel"}}]},
                {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]},
            ):
                data = sse(chunk)
                self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
            self.wfile.write(b"0\r\n\r\n")
        else:
            self._send(200, _json({"choices": [{"message": {"content": "Whole"}}]}))

    def _send(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # the proxy hanging up on a finished request is expected


class ProxyTest(unittest.TestCase):
    def setUp(self):
        _Upstream.seen = []
        self.upstream = _QuietServer(("127.0.0.1", 0), _Upstream)
        threading.Thread(target=self.upstream.serve_forever, daemon=True).start()
        self.addCleanup(self.upstream.server_close)
        self.addCleanup(self.upstream.shutdown)
        self.upstream_port = self.upstream.server_address[1]
        self.proxy = ChatProxy(lambda: self.upstream_port, "secret", now=lambda: "12:00:00")
        self.proxy.bind(0)
        threading.Thread(target=self.proxy.serve, daemon=True).start()
        self.addCleanup(self.proxy.shutdown)

    def connection(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.proxy.port, timeout=10)
        self.addCleanup(connection.close)
        return connection

    def test_requests_and_replies_pass_through_unchanged(self):
        connection = self.connection()
        connection.request("GET", "/health")
        response = connection.getresponse()
        self.assertEqual((response.status, response.read()), (200, b'{"status": "ok"}'))
        body = _json({"stream": True, "messages": [{"role": "user", "content": "hi"}]})
        connection.request("POST", "/v1/chat/completions", body=body)  # the same connection
        response = connection.getresponse()
        self.assertEqual(response.getheader("Content-Type"), "text/event-stream")
        self.assertEqual(
            response.read(),
            sse(
                {"choices": [{"delta": {"content": "Hel"}}]},
                {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]},
            ),
        )
        self.assertEqual(_Upstream.seen[1][2], body)
        whole = _json({"messages": [{"role": "user", "content": "again"}]})
        connection.request("POST", "/v1/chat/completions", body=whole)
        response = connection.getresponse()
        self.assertEqual(response.read(), _json({"choices": [{"message": {"content": "Whole"}}]}))
        events = self.kept()
        self.assertEqual([e["type"] for e in events].count("request"), 2)
        self.assertIn("Reply:\n  Hello\n", render(events))
        self.assertIn("Reply:\n  Whole\n", render(events))

    def test_the_window_needs_the_token_and_gets_events_live(self):
        connection = self.connection()
        connection.request("GET", VIEW_PATH)
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 403)
        viewer = http.client.HTTPConnection("127.0.0.1", self.proxy.port, timeout=10)
        self.addCleanup(viewer.close)
        viewer.request("GET", VIEW_PATH, headers={TOKEN_HEADER: "secret"})
        stream = viewer.getresponse()
        self.assertEqual(json.loads(stream.readline())["type"], "notice")
        body = _json({"stream": True, "messages": [{"role": "user", "content": "live?"}]})
        self.connection().request("POST", "/v1/chat/completions", body=body)
        first = json.loads(stream.readline())
        self.assertEqual((first["type"], first["parts"]), ("request", [["You", "live?"]]))
        self.assertNotIn(VIEW_PATH, [seen[1] for seen in _Upstream.seen])  # never forwarded

    def test_an_unreachable_server_is_a_clear_error(self):
        self.proxy.upstream = lambda: None
        self.proxy._port = None
        connection = self.connection()
        connection.request("GET", "/health")
        response = connection.getresponse()
        self.assertEqual(response.status, 502)
        self.assertIn(b"not reachable", response.read())

    def kept(self, timeout=5.0):
        """Every event so far, once the last request has ended."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            viewer = self.proxy.history.watch()
            events = [viewer.get_nowait() for _ in range(viewer.qsize())]
            self.proxy.history.unwatch(viewer)
            requests = [e for e in events if e["type"] == "request"]
            ends = [e for e in events if e["type"] == "end"]
            if requests and len(ends) == len(requests):
                return events
            time.sleep(0.02)
        self.fail("the replies did not end")


class ProxyProcessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.host = FakeDocker(env={"HOME": str(root)})
        self.app = App(
            host=self.host,
            paths=resolve_paths({}, root, "linux"),
            out=io.StringIO(),
            err=io.StringIO(),
            os_name="linux",
            machine="aarch64",
        )
        self.profile = profile(root)

    def test_a_taken_port_is_reported_to_the_launch(self):
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen()
            port = taken.getsockname()[1]
            self.profile = replace(self.profile, runtime=replace(self.profile.runtime, port=port))
            self.assertFalse(chat_log.port_free(port))
            runtime = DockerRuntime(self.host, FakeHttp())
            self.assertEqual(chat_log.run_proxy(self.app, runtime, self.profile), 1)
        error = chat_log.proxy_error_file(self.app, self.profile.name).read_text()
        self.assertIn(f"port {port} is in use by another program", error)

    def test_starting_waits_until_the_proxy_listens(self):
        clock = [0.0]

        def sleep(seconds):  # the proxy process writes its run file meanwhile
            clock[0] += seconds
            if clock[0] >= 0.3:
                path = chat_log.proxy_file(self.app, self.profile.name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"pid": 77, "port": 18400, "token": "t"}))
                self.host.processes[77] = f"ornith-launcher chat-proxy {self.profile.name}"

        started = chat_log.start_proxy(self.app, self.profile, sleep=sleep, clock=lambda: clock[0])
        self.assertIsNone(started)
        self.assertEqual(self.host.spawned[0][-2:], ("chat-proxy", self.profile.name))
        # Already running: nothing more is started.
        self.assertIsNone(chat_log.start_proxy(self.app, self.profile))
        self.assertEqual(len(self.host.spawned), 1)

    def test_starting_reports_why_the_proxy_did_not_listen(self):
        def sleep(seconds):
            chat_log.proxy_error_file(self.app, self.profile.name).write_text(
                "port 18400 is in use"
            )

        chat_log.proxy_error_file(self.app, self.profile.name).parent.mkdir(parents=True)
        self.assertEqual(
            chat_log.start_proxy(self.app, self.profile, sleep=sleep), "port 18400 is in use"
        )
        clock = [0.0]

        def wait(seconds):
            clock[0] += seconds

        self.assertIn(
            "did not start within",
            chat_log.start_proxy(self.app, self.profile, sleep=wait, clock=lambda: clock[0]),
        )

    def test_the_proxy_ends_once_the_server_has_stopped(self):
        runtime = DockerRuntime(self.host, FakeHttp())
        runtime.start(self.profile, proxied=True)
        checks = []

        def sleep(seconds):
            checks.append(seconds)
            if len(checks) == 2:
                runtime.stop(self.profile)

        chat_log._watch(runtime, self.profile, sleep=sleep)
        self.assertEqual(len(checks), 3)  # two checks in a row found it gone


def _json(data) -> bytes:
    return json.dumps(data).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
