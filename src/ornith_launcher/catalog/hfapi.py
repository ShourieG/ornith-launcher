"""A small client for the public Hugging Face Hub API, used without a login.

Anonymous clients get a fixed number of requests per 5-minute window and per
bucket ("api", "resolvers"). Every response carries a ``RateLimit`` header with
the requests left (``r``) and the seconds until the window resets (``t``), so
the client pauses before running out instead of hitting HTTP 429.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from ornith_launcher import __version__
from ornith_launcher.errors import OrnithLauncherError

DEFAULT_ENDPOINT = "https://huggingface.co"
USER_AGENT = f"ornith-launcher/{__version__} (unofficial)"

# Pause when a bucket has this few requests left in the current window.
RATE_RESERVE = 5
MAX_RETRIES = 5
DEFAULT_RETRY_SECONDS = 60

_RATE_LIMIT = re.compile(r'"(?P<bucket>[^"]+)"\s*;\s*r=(?P<r>\d+)\s*;\s*t=(?P<t>\d+)')
_NEXT_LINK = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


class HubError(OrnithLauncherError):
    """A Hugging Face request failed."""


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]  # lower-case names
    body: bytes
    url: str

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class Transport(Protocol):
    def get(
        self,
        url: str,
        headers: Mapping[str, str],
        private_headers: Mapping[str, str],
        limit: int | None = None,
    ) -> Response:
        """GET ``url``, reading at most ``limit`` bytes of the body.

        ``private_headers`` (the token) must not follow redirects to other hosts.
        """


class UrllibTransport:
    def __init__(self, timeout: float = 60.0) -> None:
        self.timeout = timeout

    def get(
        self,
        url: str,
        headers: Mapping[str, str],
        private_headers: Mapping[str, str],
        limit: int | None = None,
    ) -> Response:
        request = urllib.request.Request(url, headers=dict(headers))
        for name, value in private_headers.items():
            # Unredirected headers are not copied when the request is redirected to the CDN.
            request.add_unredirected_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                # A server that ignores Range sends the whole multi-GB file: read only `limit`.
                body = response.read() if limit is None else response.read(limit)
                return Response(response.status, _lower(response.headers), body, response.url)
        except urllib.error.HTTPError as exc:
            return Response(exc.code, _lower(exc.headers), exc.read() or b"", url)
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise HubError(
                f"cannot reach Hugging Face ({reason})",
                hint="Check your internet connection, then run the command again.",
            ) from exc


def _lower(headers: Any) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in headers.items()} if headers else {}


@dataclass
class RateState:
    remaining: int | None = None
    reset_at: float = 0.0


@dataclass
class HubClient:
    transport: Transport = field(default_factory=UrllibTransport)
    endpoint: str = DEFAULT_ENDPOINT
    token: str | None = None
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    on_wait: Callable[[float, str], None] | None = None  # (seconds, reason) for progress output
    _buckets: dict[str, RateState] = field(default_factory=dict)

    @classmethod
    def from_env(cls, env: Mapping[str, str], **kwargs: Any) -> HubClient:
        """Honour ``HF_ENDPOINT`` (mirrors) and use ``HF_TOKEN`` if the user has set one."""
        endpoint = (env.get("HF_ENDPOINT") or DEFAULT_ENDPOINT).rstrip("/")
        return cls(endpoint=endpoint, token=env.get("HF_TOKEN") or None, **kwargs)

    # Requests ---------------------------------------------------------------

    def get(
        self, url: str, extra_headers: Mapping[str, str] | None = None, limit: int | None = None
    ) -> Response:
        """GET with rate-limit pacing and retries on 429 and 5xx."""
        bucket = "resolvers" if "/resolve/" in url else "api"
        headers = {"User-Agent": USER_AGENT, **(extra_headers or {})}
        private = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        for attempt in range(1, MAX_RETRIES + 1):
            self._pace(bucket)
            response = self.transport.get(url, headers, private, limit)
            self._record(response)
            if response.status == 429 or response.status >= 500:
                wait = self._retry_after(response, bucket, attempt)
                self._wait(wait, f"Hugging Face asked to slow down (HTTP {response.status})")
                continue
            return response
        raise HubError(
            f"Hugging Face kept refusing requests to {url} (HTTP {response.status})",
            hint="Wait a few minutes, then run the command again.",
        )

    def get_json(self, path: str, params: Sequence[tuple[str, str]] = ()) -> Any:
        response = self.get(self._url(path, params))
        self._check(response, path)
        return response.json()

    def list_models(self, params: Sequence[tuple[str, str]]) -> Iterator[dict[str, Any]]:
        """Yield every result of ``/api/models`` for these query parameters, following pages."""
        url: str | None = self._url("/api/models", params)
        while url:
            response = self.get(url)
            self._check(response, "/api/models")
            yield from response.json()
            match = _NEXT_LINK.search(response.headers.get("link", ""))
            url = match.group(1) if match else None

    def model_info(self, repo_id: str) -> dict[str, Any]:
        return self.get_json(f"/api/models/{repo_id}")

    def tree(self, repo_id: str, revision: str) -> list[dict[str, Any]]:
        return self.get_json(f"/api/models/{repo_id}/tree/{revision}", [("recursive", "true")])

    def resolve_url(self, repo_id: str, revision: str, path: str) -> str:
        quoted = urllib.parse.quote(path)
        return f"{self.endpoint}/{repo_id}/resolve/{revision}/{quoted}"

    def range_reader(self, repo_id: str, revision: str, path: str) -> Callable[[int], bytes]:
        """A ``fetch(n)`` for ``gguf.read_metadata`` that reads the file's first ``n`` bytes."""
        url = self.resolve_url(repo_id, revision, path)

        def fetch(size: int) -> bytes:
            response = self.get(url, {"Range": f"bytes=0-{size - 1}"}, limit=size)
            self._check(response, path)
            return response.body[:size]

        return fetch

    # Helpers ----------------------------------------------------------------

    def _url(self, path: str, params: Sequence[tuple[str, str]]) -> str:
        query = urllib.parse.urlencode(list(params))
        return f"{self.endpoint}{path}" + (f"?{query}" if query else "")

    def _check(self, response: Response, what: str) -> None:
        if response.status in (200, 206):
            return
        if response.status in (401, 403):
            raise HubError(f"{what} needs a Hugging Face login (HTTP {response.status})")
        if response.status == 404:
            raise HubError(f"{what} was not found on Hugging Face")
        raise HubError(f"Hugging Face answered HTTP {response.status} for {what}")

    def _record(self, response: Response) -> None:
        match = _RATE_LIMIT.search(response.headers.get("ratelimit", ""))
        if match:
            state = self._buckets.setdefault(match["bucket"], RateState())
            state.remaining = int(match["r"])
            state.reset_at = self.clock() + int(match["t"])

    def _pace(self, bucket: str) -> None:
        state = self._buckets.get(bucket)
        if state is None or state.remaining is None or state.remaining > RATE_RESERVE:
            return
        wait = state.reset_at - self.clock()
        if wait > 0:
            self._wait(wait + 1, "rate-limit pause")
        state.remaining = None

    def _retry_after(self, response: Response, bucket: str, attempt: int) -> float:
        state = self._buckets.get(bucket)
        if state is not None and state.reset_at > self.clock():
            return state.reset_at - self.clock() + 1
        retry_after = response.headers.get("retry-after", "")
        if retry_after.isdigit():
            return float(retry_after)
        return min(DEFAULT_RETRY_SECONDS, 2.0 * attempt)

    def _wait(self, seconds: float, reason: str) -> None:
        if self.on_wait is not None:
            self.on_wait(seconds, reason)
        self.sleep(seconds)
