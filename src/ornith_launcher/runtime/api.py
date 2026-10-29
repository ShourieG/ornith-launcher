"""What every backend asks the running llama.cpp server over HTTP, whatever runs it.

The server's own port comes from the backend (``host_port``), so the same checks
work for a Docker container and for a native process, behind the chat log proxy
or not.
"""

from __future__ import annotations

from collections.abc import Callable

from ornith_launcher.errors import OrnithLauncherError
from ornith_launcher.profiles import Profile
from ornith_launcher.runtime.http import Http


class ServerError(OrnithLauncherError):
    pass


class ServerApi:
    http: Http
    clock: Callable[[], float]

    def host_port(self, profile: Profile, cached: bool = True) -> int | None:
        raise NotImplementedError

    def log_command(self, profile: Profile) -> str:
        """The command that shows this server's log, for hints."""
        raise NotImplementedError

    def api_url(self, profile: Profile) -> str:
        """The server's own URL: the profile's, unless the chat log proxy sits in front."""
        port = self.host_port(profile)
        return f"http://127.0.0.1:{port}" if port else profile.url

    def warm_up(self, profile: Profile) -> float:
        started = self.clock()
        reply = self.http.post_json(
            f"{self.api_url(profile)}/v1/chat/completions",
            {
                "model": profile.runtime.alias,
                "max_tokens": 16,
                "messages": [{"role": "user", "content": "Reply with: ready"}],
            },
        )
        if reply.status != 200:
            raise ServerError(
                f"the warm-up request failed (HTTP {reply.status}): {reply.text[:200]}",
                hint=f"See the server log: {self.log_command(profile)}",
            )
        return self.clock() - started

    def verify(self, profile: Profile) -> list[str]:
        """Differences between what runs and what the profile asked for (empty when all match)."""
        problems = []
        url = self.api_url(profile)
        props = self.http.get(f"{url}/props").json() or {}
        n_ctx = (props.get("default_generation_settings") or {}).get("n_ctx")
        if n_ctx is not None and n_ctx != profile.runtime.context:
            problems.append(f"context is {n_ctx}, expected {profile.runtime.context}")
        models = self.http.get(f"{url}/v1/models").json() or {}
        ids = [m.get("id") for m in models.get("data") or []]
        if ids and profile.runtime.alias not in ids:
            problems.append(f"model name is {ids[0]}, expected {profile.runtime.alias}")
        return problems

    def requests_in_progress(self, profile: Profile) -> int | None:
        reply = self.http.get(f"{self.api_url(profile)}/metrics", timeout=2)
        if reply.status != 200:
            return None
        for line in reply.text.splitlines():
            if line.startswith("llamacpp:requests_processing"):
                try:
                    return int(float(line.split()[-1]))
                except ValueError:
                    return None
        return None
