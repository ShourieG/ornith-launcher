from __future__ import annotations

import hashlib
import http.server
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from ornith_launcher import download
from ornith_launcher.download import (
    Downloader,
    DownloadError,
    DownloadPaused,
    Expired,
    HttpRangeSource,
    Status,
    Target,
    format_status,
)

PAYLOAD = os.urandom(1024 * 1024 + 123)
SHA = hashlib.sha256(PAYLOAD).hexdigest()


class RangeHandler(http.server.BaseHTTPRequestHandler):
    """Serves PAYLOAD at /file with Range support; /redirect points to it like the HF CDN."""

    requests: list[str] = []

    def do_GET(self):  # noqa: N802 (http.server naming)
        RangeHandler.requests.append(self.path)
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/file")
            self.end_headers()
            return
        if self.path == "/norange":
            self.send_response(200)
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            self.wfile.write(PAYLOAD)
            return
        start, end = self.headers["Range"].split("=")[1].split("-")
        start, end = int(start), min(int(end), len(PAYLOAD) - 1)
        body = PAYLOAD[start : end + 1]
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {start}-{end}/{len(PAYLOAD)}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class QuietServer(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # clients that stop reading early (pause, ignored ranges) are expected


class DownloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = QuietServer(("127.0.0.1", 0), RangeHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dest = Path(self.tmp.name) / "models" / "model.gguf"
        RangeHandler.requests.clear()
        patcher = mock.patch.object(download, "CHUNK", 64 * 1024)
        patcher.start()
        self.addCleanup(patcher.stop)

    def target(self, path="/redirect", sha=SHA) -> Target:
        return Target(f"{self.base}{path}", self.dest, len(PAYLOAD), sha)

    def downloader(self, **kwargs) -> Downloader:
        kwargs.setdefault("source", HttpRangeSource(timeout=10))
        kwargs.setdefault("segments", 4)
        return Downloader(tick=0.01, sleep=lambda s: None, **kwargs)

    def test_segmented_download_through_a_redirect(self):
        statuses = []
        path = self.downloader(on_progress=statuses.append).fetch(self.target())
        self.assertEqual(path.read_bytes(), PAYLOAD)
        self.assertFalse(Path(str(self.dest) + ".part").exists())
        self.assertFalse(Path(str(self.dest) + ".part.json").exists())
        self.assertEqual(RangeHandler.requests.count("/redirect"), 1)  # resolved once
        self.assertGreaterEqual(RangeHandler.requests.count("/file"), 4)
        self.assertEqual(statuses[-1].phase, "verify")
        self.assertEqual(statuses[-1].done, len(PAYLOAD))

    def test_pause_then_resume(self):
        stop = threading.Event()
        real = HttpRangeSource(timeout=10)

        class StopAfterFirst:
            def resolve(self, url):
                return real.resolve(url)

            def open(self, url, start, end):
                stop.set()  # the user presses Ctrl+C once the first segment starts
                return real.open(url, start, end)

        with self.assertRaises(DownloadPaused):
            self.downloader(source=StopAfterFirst()).fetch(self.target(), stop)
        self.assertTrue(Path(str(self.dest) + ".part.json").exists())
        self.assertFalse(self.dest.exists())
        self.downloader().fetch(self.target())
        self.assertEqual(self.dest.read_bytes(), PAYLOAD)

    def test_existing_complete_file_is_kept(self):
        self.dest.parent.mkdir(parents=True)
        self.dest.write_bytes(PAYLOAD)
        self.downloader().fetch(self.target())
        self.assertEqual(RangeHandler.requests, [])

    def test_checksum_mismatch_removes_the_copy(self):
        with self.assertRaisesRegex(DownloadError, "sha256 does not match"):
            self.downloader().fetch(self.target(sha="0" * 64))
        self.assertFalse(self.dest.exists())
        self.assertFalse(Path(str(self.dest) + ".part").exists())
        self.assertFalse(Path(str(self.dest) + ".part.json").exists())

    def test_server_ignoring_ranges_is_an_error(self):
        with self.assertRaisesRegex(DownloadError, "ignored the byte range"):
            self.downloader().fetch(self.target("/norange"))

    def test_expired_link_is_resolved_again(self):
        real = HttpRangeSource(timeout=10)
        calls = {"resolve": 0, "expired": False}

        class ExpiresOnce:
            def resolve(self, url):
                calls["resolve"] += 1
                return real.resolve(url)

            def open(self, url, start, end):
                if not calls["expired"]:
                    calls["expired"] = True
                    raise Expired()
                return real.open(url, start, end)

        self.downloader(source=ExpiresOnce(), segments=1).fetch(self.target())
        self.assertEqual(calls["resolve"], 2)
        self.assertEqual(self.dest.read_bytes(), PAYLOAD)

    def test_refuses_without_disk_space(self):
        with self.assertRaisesRegex(DownloadError, "not enough disk space"):
            self.downloader(disk_free=lambda path: 1024).fetch(self.target())


class QuickConnectTest(unittest.TestCase):
    def test_an_address_that_does_not_answer_is_left_and_tried_last_after(self):
        import socket
        from unittest import mock

        from ornith_launcher import download

        dead, good = ("192.0.2.1", 443), ("192.0.2.2", 443)
        infos = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", dead), (2, 1, 6, "", good)]
        tried = []

        class FakeSocket:
            def __init__(self, *args):
                self.timeouts = []

            def settimeout(self, value):
                self.timeouts.append(value)

            def connect(self, addr):
                tried.append(addr)
                if addr == dead:
                    raise socket.timeout("timed out")

            def close(self):
                pass

        skip = set()
        conn = download._QuickConnection("example.invalid", timeout=60.0, skip=skip)
        lookup = mock.patch.object(download.socket, "getaddrinfo", return_value=infos)
        with lookup, mock.patch.object(download.socket, "socket", FakeSocket):
            first = conn._create_connection(("example.invalid", 443), 60.0)
            second = conn._create_connection(("example.invalid", 443), 60.0)
        self.assertEqual(tried, [dead, good, good])  # the dead one costs its 10 s only once
        self.assertEqual(skip, {"192.0.2.1"})
        # 10 s to answer, then a stalled download still gets the whole minute.
        self.assertEqual(first.timeouts, [download.CONNECT_TIMEOUT, 60.0])
        self.assertEqual(second.timeouts, [download.CONNECT_TIMEOUT, 60.0])

    def test_downloads_go_through_it(self):
        from ornith_launcher import download

        handlers = download.HttpRangeSource()._opener.handlers
        self.assertTrue(any(isinstance(h, download._QuickHTTPSHandler) for h in handlers))


class FormatTest(unittest.TestCase):
    def test_progress_line(self):
        status = Status("m.gguf", "download", 16 * 1024**3, 32 * 1024**3, rate=64.6e6)
        line = format_status(status, width=10)
        self.assertTrue(line.startswith("[#####.....]  50%  16.0/32.0 GiB  64.6 MB/s  ETA 0:"))
        self.assertTrue(format_status(Status("m", "verify", 1, 2)).endswith("verifying"))
        # The 12 MB macOS runtime counts in MB, not as 0.0/0.0 GiB.
        small = format_status(Status("r.tar.gz", "download", 4_200_000, 12_100_000), width=10)
        self.assertIn("  4.2/12.1 MB  ", small)


if __name__ == "__main__":
    unittest.main()
