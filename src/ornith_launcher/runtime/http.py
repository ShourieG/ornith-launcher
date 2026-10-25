"""Small HTTP calls to the local model server: health, properties, metrics, warm-up."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Reply:
    status: int  # 0 when the server could not be reached
    body: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class Http(Protocol):
    def get(self, url: str, timeout: float = 3.0) -> Reply: ...

    def post_json(self, url: str, payload: Any, timeout: float = 300.0) -> Reply: ...


class UrllibHttp:
    def get(self, url: str, timeout: float = 3.0) -> Reply:
        return self._send(urllib.request.Request(url), timeout)

    def post_json(self, url: str, payload: Any, timeout: float = 300.0) -> Reply:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        return self._send(request, timeout)

    def _send(self, request: urllib.request.Request, timeout: float) -> Reply:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return Reply(response.status, response.read())
        except urllib.error.HTTPError as exc:
            return Reply(exc.code, exc.read() or b"")
        except (urllib.error.URLError, OSError, ValueError):
            return Reply(0, b"")
