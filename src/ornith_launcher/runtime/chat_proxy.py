"""The chat log proxy: the profile's port, passed through to the server, with a live copy.

With the chat log on, the server container publishes on a free port Docker picks,
and this proxy listens on the profile's own port (127.0.0.1 only), so clients and
integrations keep the same URL. Every request and reply passes through byte for
byte, streamed replies chunk by chunk; chat requests are also turned into events
(``runtime.chat_capture``) for the chat log window.

Nothing is written to disk. The last ``KEEP_EXCHANGES`` requests stay in memory so a
reopened window can show them again, and are gone when the proxy ends. The window
reads them from ``VIEW_PATH`` with a token that only the proxy's run file holds.
"""

from __future__ import annotations

import hmac
import http.client
import json
import queue
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from ornith_launcher.runtime.chat_capture import Reply, api_of, request_event

VIEW_PATH = "/ornith-launcher/chat-log"
TOKEN_HEADER = "X-Ornith-Launcher-Token"
KEEP_EXCHANGES = 10
KEEP_CHARS = 200_000  # text kept per request for a reopened window
CHUNK = 64 * 1024
UPSTREAM_TIMEOUT = 3600.0  # a long prompt can take minutes before the first byte
MAX_REQUEST = 256 * 1024**2
VIEWER_QUEUE = 100_000
# Headers that describe one connection, not the message; never passed on.
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


class History:
    """Recent events for windows that open later, and the windows watching now."""

    def __init__(self, keep: int = KEEP_EXCHANGES) -> None:
        self.keep = keep
        self.lock = threading.Lock()
        self.exchanges: OrderedDict[int, list[dict[str, Any]]] = OrderedDict()
        self.sizes: dict[int, int] = {}
        self.viewers: list[queue.Queue] = []
        self.last_id = 0

    def new_id(self) -> int:
        with self.lock:
            self.last_id += 1
            return self.last_id

    def publish(self, event: dict[str, Any]) -> None:
        with self.lock:
            self._remember(event)
            for viewer in list(self.viewers):
                try:
                    viewer.put_nowait(event)
                except queue.Full:  # a window that stopped reading
                    self.viewers.remove(viewer)

    def _remember(self, event: dict[str, Any]) -> None:
        exchange = event["id"]
        if event["type"] == "request":
            self.exchanges[exchange] = [event]
            self.sizes[exchange] = 0
            while len(self.exchanges) > self.keep:
                old, _ = self.exchanges.popitem(last=False)
                self.sizes.pop(old, None)
            return
        events = self.exchanges.get(exchange)
        if events is None:
            return
        if event["type"] == "text":
            text = event.get("text") or ""
            if self.sizes[exchange] + len(text) > KEEP_CHARS:
                return
            self.sizes[exchange] += len(text)
            last = events[-1]
            same = last["type"] == "text" and last["kind"] == event["kind"]
            if same and "label" not in event:
                events[-1] = {**last, "text": last["text"] + text}
                return
        events.append(event)

    def watch(self) -> queue.Queue:
        """A queue that gets the kept events at once, then every new one."""
        viewer: queue.Queue = queue.Queue(maxsize=VIEWER_QUEUE)
        with self.lock:
            kept = [e for events in self.exchanges.values() for e in events]
            for event in kept:
                viewer.put_nowait(event)
            count = len(self.exchanges)
            if count:
                note = f"the last {count} request{'s' if count > 1 else ''} above; new ones follow"
            else:
                note = "waiting for the first request"
            viewer.put_nowait({"type": "notice", "id": 0, "text": note})
            self.viewers.append(viewer)
        return viewer

    def unwatch(self, viewer: queue.Queue) -> None:
        with self.lock:
            if viewer in self.viewers:
                self.viewers.remove(viewer)

    def close(self) -> None:
        with self.lock:
            for viewer in self.viewers:
                try:
                    viewer.put_nowait(None)
                except queue.Full:
                    pass
            self.viewers.clear()


class ChatProxy:
    def __init__(
        self,
        upstream: Callable[[], int | None],
        token: str,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], str] = lambda: time.strftime("%H:%M:%S"),
    ) -> None:
        self.upstream = upstream  # the server's port on 127.0.0.1, or None if not known
        self.token = token
        self.clock = clock
        self.now = now
        self.history = History()
        self.stopping = threading.Event()
        self.server: _Server | None = None
        self._port: int | None = None

    def bind(self, port: int) -> None:
        """Listen on 127.0.0.1:``port``; raises OSError when the port is taken."""
        self.server = _Server(("127.0.0.1", port), self)

    @property
    def port(self) -> int:
        assert self.server is not None
        return self.server.server_address[1]

    def serve(self) -> None:
        assert self.server is not None
        self.server.serve_forever(poll_interval=0.5)

    def shutdown(self) -> None:
        self.stopping.set()
        self.history.close()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()

    # Forwarding ---------------------------------------------------------------------------

    def upstream_port(self) -> int | None:
        if self._port is None:
            self._port = self.upstream()
        return self._port

    def forward(self, handler: _Handler, body: bytes) -> None:
        port = self.upstream_port()
        if port is None:
            self._unavailable(handler)
            return
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=UPSTREAM_TIMEOUT)
        headers = {k: v for k, v in handler.headers.items() if k.lower() not in HOP_BY_HOP}
        headers["Host"] = f"127.0.0.1:{port}"
        if body or handler.command in ("POST", "PUT", "PATCH"):
            headers["Content-Length"] = str(len(body))
        try:
            connection.request(handler.command, handler.path, body=body or None, headers=headers)
            response = connection.getresponse()
        except OSError:
            connection.close()
            self._port = None  # the server may have restarted on another port
            self._unavailable(handler)
            return
        started = self.clock()
        reply = self._capture(handler, body, response)
        finished = False
        try:
            self._relay(handler, response, reply)
            finished = True
        except OSError:  # the client went away (a cancelled request) or the server did
            handler.close_connection = True
        finally:
            response.close()
            connection.close()
            if reply is not None:
                self._finish(reply, response.status, self.clock() - started, finished)

    def _capture(
        self, handler: _Handler, body: bytes, response: http.client.HTTPResponse
    ) -> Reply | None:
        api = api_of(handler.command, handler.path)
        if api is None:
            return None
        try:
            exchange = self.history.new_id()
            path = handler.path.split("?", 1)[0]
            self.history.publish(request_event(exchange, self.now(), path, api, body))
            return Reply(exchange, api, response.getheader("Content-Type") or "")
        except Exception:  # the chat log must never break a request
            return None

    def _relay(
        self, handler: _Handler, response: http.client.HTTPResponse, reply: Reply | None
    ) -> None:
        status = response.status
        handler.send_response_only(status, response.reason)
        length = response.getheader("Content-Length")
        has_body = handler.command != "HEAD" and status not in (204, 304) and status >= 200
        chunked = has_body and length is None
        for key, value in response.getheaders():
            if key.lower() not in HOP_BY_HOP:
                handler.send_header(key, value)
        if length is not None:
            handler.send_header("Content-Length", length)
        elif chunked:
            handler.send_header("Transfer-Encoding", "chunked")
        handler.end_headers()
        if not has_body:
            return
        while True:
            data = response.read1(CHUNK)
            if not data:
                break
            handler.wfile.write(b"%x\r\n%s\r\n" % (len(data), data) if chunked else data)
            handler.wfile.flush()
            if reply is not None:
                reply = self._feed(reply, data)
        if chunked:
            handler.wfile.write(b"0\r\n\r\n")
            handler.wfile.flush()

    def _feed(self, reply: Reply, data: bytes) -> Reply | None:
        try:
            for event in reply.feed(data):
                self.history.publish(event)
            return reply
        except Exception:
            return None

    def _finish(self, reply: Reply, status: int, seconds: float, finished: bool) -> None:
        try:
            events = reply.finish(status, seconds)
            if not finished:
                events[-1]["stop"] = "the connection closed before the reply ended"
            for event in events:
                self.history.publish(event)
        except Exception:
            pass

    def _unavailable(self, handler: _Handler) -> None:
        message = "the model server is not reachable (it may be starting or stopped)"
        _send_json(handler, 502, {"error": {"message": message, "type": "unavailable"}})

    # The chat log window ------------------------------------------------------------------

    def view(self, handler: _Handler) -> None:
        if not hmac.compare_digest(handler.headers.get(TOKEN_HEADER, ""), self.token):
            _send_json(handler, 403, {"error": {"message": "wrong token"}})
            return
        handler.close_connection = True
        handler.send_response_only(200)
        handler.send_header("Content-Type", "application/x-ndjson")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Connection", "close")
        handler.end_headers()
        viewer = self.history.watch()
        try:
            while not self.stopping.is_set():
                try:
                    event = viewer.get(timeout=15.0)
                except queue.Empty:
                    handler.wfile.write(b"\n")  # finds a window that was closed
                    continue
                if event is None:
                    break
                handler.wfile.write(json.dumps(event).encode("utf-8") + b"\n")
        except OSError:
            pass
        finally:
            self.history.unwatch(viewer)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], proxy: ChatProxy) -> None:
        self.proxy = proxy
        super().__init__(address, _Handler)

    def handle_error(self, request: Any, client_address: Any) -> None:
        # A client that hangs up (a cancelled request, a closed window) is normal here.
        if not isinstance(sys.exc_info()[1], OSError):
            super().handle_error(request, client_address)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ornith-launcher-chat-log"
    server: _Server

    def handle_any(self) -> None:
        proxy = self.server.proxy
        if self.path.split("?", 1)[0] == VIEW_PATH:
            proxy.view(self)
            return
        body = self._body()
        if body is not None:
            proxy.forward(self, body)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = handle_any

    def _body(self) -> bytes | None:
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            parts = []
            while True:
                size = int(self.rfile.readline().split(b";", 1)[0].strip() or b"0", 16)
                if size == 0:
                    while self.rfile.readline().strip():  # trailers
                        pass
                    return b"".join(parts)
                parts.append(self.rfile.read(size))
                self.rfile.readline()
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_REQUEST:
            _send_json(self, 413, {"error": {"message": "request body too large or invalid"}})
            self.close_connection = True
            return None
        return self.rfile.read(length) if length else b""

    def log_message(self, format: str, *args: Any) -> None:
        pass  # the server window shows the server's own log


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    data = json.dumps(payload).encode("utf-8")
    handler.send_response_only(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(data)
