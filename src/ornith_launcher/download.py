"""Segmented, resumable downloads with sha256 verification.

A file is fetched as several HTTP Range requests in parallel, written into a
preallocated ``<file>.part``. Progress per segment is saved in
``<file>.part.json``, so Ctrl+C, a crash or a reboot loses nothing: running the
same download again continues where it stopped. The finished file is checked
against its sha256 before it replaces the ``.part`` file.

Each worker writes through its own file handle with ``seek`` and ``write``
(``os.pwrite`` does not exist on Windows).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Protocol

from ornith_launcher import __version__
from ornith_launcher.config import atomic_write_json
from ornith_launcher.errors import OrnithLauncherError

USER_AGENT = f"ornith-launcher/{__version__} (unofficial)"
DEFAULT_SEGMENTS = 8
CHUNK = 4 * 1024**2
STALL_TIMEOUT = 60.0
MAX_ATTEMPTS = 30
SAVE_EVERY = 5.0  # seconds between state saves
RATE_WINDOW = 10.0  # seconds of history for the speed estimate
DISK_RESERVE = 2 * 1024**3  # keep this much free after the download
JOIN_TIMEOUT = 5.0


class DownloadError(OrnithLauncherError):
    pass


class DownloadPaused(DownloadError):
    """The user stopped the download; its progress is saved."""


class Expired(Exception):
    """The CDN link expired (HTTP 403/410); the original URL must be resolved again."""


class RangeSource(Protocol):
    def resolve(self, url: str) -> str:
        """Follow redirects once and return the URL that serves the bytes."""

    def open(self, url: str, start: int, end: int) -> BinaryIO:
        """A stream of bytes ``start`` to ``end`` (inclusive). Raises Expired for a stale link."""


class HttpRangeSource:
    def __init__(self, timeout: float = STALL_TIMEOUT, token: str | None = None) -> None:
        self.timeout = timeout
        self.token = token

    def _request(self, url: str, start: int, end: int) -> urllib.request.Request:
        request = urllib.request.Request(
            url, headers={"Range": f"bytes={start}-{end}", "User-Agent": USER_AGENT}
        )
        if self.token and url.startswith("https://huggingface.co/"):
            request.add_unredirected_header("Authorization", f"Bearer {self.token}")
        return request

    def resolve(self, url: str) -> str:
        try:
            with urllib.request.urlopen(self._request(url, 0, 0), timeout=self.timeout) as r:
                return r.url
        except urllib.error.HTTPError as exc:
            raise DownloadError(
                f"the server refused the download (HTTP {exc.code}): {url}"
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise DownloadError(
                f"cannot reach the download server ({getattr(exc, 'reason', exc)})",
                hint="Check your internet connection, then run setup again to resume.",
            ) from exc

    def open(self, url: str, start: int, end: int) -> BinaryIO:
        try:
            response = urllib.request.urlopen(self._request(url, start, end), timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 410):
                raise Expired() from exc
            raise
        if response.status != 206:
            response.close()
            raise DownloadError(f"the server ignored the byte range (HTTP {response.status})")
        return response


@dataclass(frozen=True)
class Target:
    url: str
    dest: Path
    size: int
    sha256: str | None = None


@dataclass
class Status:
    """What the progress display shows."""

    name: str
    phase: str  # "download" or "verify"
    done: int
    total: int
    rate: float = 0.0  # bytes per second over the last RATE_WINDOW seconds

    @property
    def eta(self) -> float | None:
        return (self.total - self.done) / self.rate if self.rate > 0 else None


@dataclass
class _Segment:
    start: int
    end: int  # exclusive
    done: int = 0

    @property
    def remaining(self) -> int:
        return self.end - self.start - self.done


@dataclass
class Downloader:
    source: RangeSource = field(default_factory=HttpRangeSource)
    segments: int = DEFAULT_SEGMENTS
    on_progress: Callable[[Status], None] | None = None
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    disk_free: Callable[[Path], int] = lambda path: shutil.disk_usage(path).free
    tick: float = 0.5

    def fetch(self, target: Target, stop: threading.Event | None = None) -> Path:
        """Download ``target`` (or finish a paused download) and return its path.

        Raises DownloadPaused when ``stop`` is set; run it again to continue.
        """
        stop = stop or threading.Event()
        dest = target.dest
        if dest.exists() and dest.stat().st_size == target.size:
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        part, state_file = _part_paths(dest)
        segments = self._load_state(state_file, part, target)
        remaining = sum(s.remaining for s in segments)
        free = self.disk_free(dest.parent)
        if free < remaining + DISK_RESERVE:
            raise DownloadError(
                f"not enough disk space in {dest.parent}: {remaining / 1024**3:.1f} GiB to "
                f"download plus 2 GiB spare, but only {free / 1024**3:.1f} GiB free",
                hint="Free some space or choose another models folder, then run setup again.",
            )
        if remaining:
            self._download(target, part, state_file, segments, stop)
        self._verify(target, part, state_file)
        part.replace(dest)
        state_file.unlink(missing_ok=True)
        return dest

    # Download -------------------------------------------------------------------

    def _load_state(self, state_file: Path, part: Path, target: Target) -> list[_Segment]:
        if state_file.exists() and part.exists():
            try:
                saved = json.loads(state_file.read_text(encoding="utf-8"))
                if saved["size"] == target.size and saved.get("sha256") == target.sha256:
                    return [_Segment(*s) for s in saved["segments"]]
            except (ValueError, KeyError, TypeError):
                pass
        count = max(1, min(self.segments, target.size // CHUNK or 1))
        length = -(-target.size // count)
        segments = [_Segment(i * length, min(target.size, (i + 1) * length)) for i in range(count)]
        with open(part, "wb") as handle:
            handle.truncate(target.size)  # sparse on file systems that allow it
        self._save_state(state_file, target, segments)
        return segments

    def _save_state(self, state_file: Path, target: Target, segments: list[_Segment]) -> None:
        atomic_write_json(
            state_file,
            {
                "url": target.url,
                "size": target.size,
                "sha256": target.sha256,
                "segments": [[s.start, s.end, s.done] for s in segments],
            },
        )

    def _download(
        self,
        target: Target,
        part: Path,
        state_file: Path,
        segments: list[_Segment],
        stop: threading.Event,
    ) -> None:
        lock = threading.Lock()
        url_box = [self.source.resolve(target.url)]
        errors: list[str] = []

        def refresh_url(stale: str) -> str:
            with lock:
                if url_box[0] == stale:
                    url_box[0] = self.source.resolve(target.url)
                return url_box[0]

        def worker(segment: _Segment) -> None:
            attempts = 0
            # Unbuffered, so the saved progress never counts bytes still in a Python buffer.
            with open(part, "r+b", buffering=0) as handle:
                while segment.remaining > 0 and not stop.is_set():
                    url = url_box[0]
                    try:
                        start = segment.start + segment.done
                        stream = self.source.open(url, start, segment.end - 1)
                        with stream:
                            handle.seek(start)
                            while not stop.is_set():
                                chunk = stream.read(min(CHUNK, segment.remaining))
                                if not chunk:
                                    break
                                _write_all(handle, chunk)
                                with lock:
                                    segment.done += len(chunk)
                        attempts = 0
                    except Expired:
                        refresh_url(url)
                    except DownloadError as exc:
                        errors.append(str(exc))
                        return
                    except Exception as exc:  # network hiccup: retry this segment
                        attempts += 1
                        if attempts > MAX_ATTEMPTS:
                            errors.append(f"{type(exc).__name__}: {exc}")
                            return
                        self.sleep(min(30.0, 2.0 * attempts))

        threads = [
            threading.Thread(target=worker, args=(s,), daemon=True)
            for s in segments
            if s.remaining > 0
        ]
        for thread in threads:
            thread.start()
        history: list[tuple[float, int]] = []
        last_save = self.clock()
        try:
            while any(t.is_alive() for t in threads):
                stop.wait(self.tick)
                now = self.clock()
                with lock:
                    done = sum(s.end - s.start - s.remaining for s in segments)
                history = [h for h in history if now - h[0] <= RATE_WINDOW] + [(now, done)]
                rate = 0.0
                if len(history) > 1 and history[-1][0] > history[0][0]:
                    rate = (history[-1][1] - history[0][1]) / (history[-1][0] - history[0][0])
                self._report(Status(target.dest.name, "download", done, target.size, rate))
                if now - last_save >= SAVE_EVERY:
                    with lock:
                        self._save_state(state_file, target, segments)
                    last_save = now
        except KeyboardInterrupt:
            stop.set()
        # A worker blocked on a slow read must not hold up a pause; the saved state only
        # counts bytes already written, so a late write is at worst downloaded twice.
        for thread in threads:
            thread.join(JOIN_TIMEOUT if stop.is_set() else None)
        with lock:
            self._save_state(state_file, target, segments)
        if stop.is_set():
            raise DownloadPaused(
                f"download of {target.dest.name} paused",
                hint="Run setup again to resume where it stopped.",
            )
        if errors or any(s.remaining for s in segments):
            raise DownloadError(
                f"download of {target.dest.name} failed: {errors[0] if errors else 'incomplete'}",
                hint="Run setup again to resume; finished parts are kept.",
            )

    # Verify ---------------------------------------------------------------------

    def _verify(self, target: Target, part: Path, state_file: Path) -> None:
        if part.stat().st_size != target.size:
            raise DownloadError(f"{part} has the wrong size")
        if target.sha256 is None:
            return
        digest = sha256_file(
            part, lambda done: self._report(Status(target.dest.name, "verify", done, target.size))
        )
        if digest != target.sha256.lower():
            part.unlink(missing_ok=True)
            state_file.unlink(missing_ok=True)
            raise DownloadError(
                f"{target.dest.name} is corrupt: its sha256 does not match",
                hint="The damaged copy was removed. Run setup again to download it again.",
            )

    def _report(self, status: Status) -> None:
        if self.on_progress is not None:
            self.on_progress(status)


def sha256_file(path: Path, on_progress: Callable[[int], None] | None = None) -> str:
    digest = hashlib.sha256()
    done = 0
    with open(path, "rb") as handle:
        while True:
            block = handle.read(16 * 1024**2)
            if not block:
                break
            digest.update(block)
            done += len(block)
            if on_progress is not None:
                on_progress(done)
    return digest.hexdigest()


def _write_all(handle: BinaryIO, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = handle.write(view)
        view = view[written:]


def _part_paths(dest: Path) -> tuple[Path, Path]:
    return dest.with_name(dest.name + ".part"), dest.with_name(dest.name + ".part.json")


def format_status(status: Status, width: int = 30) -> str:
    """``[#####.....] 47%  16.6/35.2 GiB  64.6 MB/s  ETA 0:04:52`` (verify: no speed)."""
    fraction = status.done / status.total if status.total else 1.0
    filled = int(fraction * width)
    bar = "#" * filled + "." * (width - filled)
    text = (
        f"[{bar}] {fraction * 100:3.0f}%  "
        f"{status.done / 1024**3:.1f}/{status.total / 1024**3:.1f} GiB"
    )
    if status.phase == "verify":
        return text + "  verifying"
    text += f"  {status.rate / 1e6:.1f} MB/s"
    eta = status.eta
    if eta is not None:
        hours, rest = divmod(int(eta), 3600)
        text += f"  ETA {hours}:{rest // 60:02d}:{rest % 60:02d}"
    return text
