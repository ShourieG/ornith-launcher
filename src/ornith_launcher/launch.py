"""Day-to-day commands: launch, stop, status, list, monitor, logs, chat-log and remove.

``launch`` only starts the model (D9): it never re-runs setup steps. A missing
model server image is offered for download; anything else a profile needs that
is missing (a model file) is named, with how to repair it.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from ornith_launcher import chat_log
from ornith_launcher.app import App, self_command
from ornith_launcher.errors import OrnithLauncherError
from ornith_launcher.installed import record
from ornith_launcher.integrations.terminals import (
    THIS_TERMINAL,
    by_id,
    detect_terminals,
    open_in_terminal,
    runs_in,
)
from ornith_launcher.profiles import (
    Profile,
    delete_profile,
    list_profiles,
    load_profile,
)
from ornith_launcher.runtime.docker import PROFILE_LABEL, DockerRuntime, ServerError
from ornith_launcher.runtime.monitor import (
    Monitor,
    ask_to_stop,
    read_line_with_timeout,
    stop_banner,
)
from ornith_launcher.settings import load_settings, save_settings
from ornith_launcher.system.memory import parse_meminfo
from ornith_launcher.system.platform import detect_platform
from ornith_launcher.ui.plain import context_label, table
from ornith_launcher.ui.prompts import Choice, PlainPrompter

GIB = 1024**3
LOG_TAIL = 200  # log lines shown before the live ones


class LaunchError(OrnithLauncherError):
    pass


def repair_hint(name: str) -> str:
    return f"Repair it with: ornith-launcher setup --repair {name}"


# Checks before starting -----------------------------------------------------------------


def preflight(
    app: App, profile: Profile, runtime: DockerRuntime, check_image: bool = True
) -> list[str]:
    """What stops this profile from starting, each with the fix (empty when all is well)."""
    problems = []
    for part in profile.model.parts:
        path = Path(part)
        if not path.is_file():
            where = (
                f" It belonged to {profile.model.owner_app}; it may have been removed or "
                "updated there."
                if profile.model.owner_app
                else ""
            )
            problems.append(f"The model file {path} is missing.{where} {repair_hint(profile.name)}")
    if not problems and profile.model.location == "external":
        info = Path(profile.model.parts[0]).stat()
        total = sum(Path(p).stat().st_size for p in profile.model.parts)
        if total != profile.model.size or info.st_mtime_ns != profile.model.mtime_ns:
            problems.append(
                f"The model file {profile.model.parts[0]} changed since setup. "
                + repair_hint(profile.name)
            )
    if profile.model.mmproj and not Path(profile.model.mmproj).is_file():
        problems.append(
            f"The vision projector {profile.model.mmproj} is missing. " + repair_hint(profile.name)
        )
    template = profile.runtime.template
    if template.source == "patched" and template.file and not Path(template.file).is_file():
        problems.append(
            f"The patched chat template {template.file} is missing. " + repair_hint(profile.name)
        )
    if check_image and not runtime.image_present(profile.runtime.image):
        problems.append(
            f"The llama.cpp image {profile.runtime.image.split('@')[0]} is not downloaded. "
            + repair_hint(profile.name)
        )
    return problems


def resolve_name(app: App, name: str | None, prompter: PlainPrompter | None = None) -> str:
    """The profile to act on: the one named, the default, the only one, or a choice."""
    names = list_profiles(app.paths)
    if name:
        return name
    if not names:
        raise LaunchError(
            "there are no profiles yet", hint="Create one with: ornith-launcher setup"
        )
    default = load_settings(app.paths).default_profile
    if default in names:
        return default
    if len(names) == 1:
        return names[0]
    if prompter is None:
        raise LaunchError(
            "several profiles exist and none is the default",
            hint=f"Name one, e.g.: ornith-launcher launch {names[0]}",
        )
    picked = prompter.choose("Which profile?", ["Profile"], [Choice((n,)) for n in names])
    return names[int(picked)]


# Starting --------------------------------------------------------------------------------


@dataclass
class Launcher:
    app: App
    runtime: DockerRuntime
    prompter: PlainPrompter

    def say(self, text: str = "") -> None:
        self.app.say(text)

    def serve(self, profile: Profile, monitor: bool = True, chat: bool | None = None) -> int:
        """Start (or reuse) the server, wait until it answers, then monitor it.

        ``chat`` (default: the profile's choice) puts the chat log proxy in front of the
        server and opens the chat log window as soon as the server starts.
        """
        chat = chat_log.enabled(profile) if chat is None else chat
        if monitor:  # the banner is the first thing in a new server window
            for line in stop_banner(profile.name, chat_log=chat):
                self.say(line)
            self.say()
        if not self._make_room(profile):
            return 1
        if chat:
            self._check_chat_port(profile)
        self._memory_warning(profile)
        started = False
        try:
            state = self.runtime.start(profile, proxied=chat)
            started = True
            if chat:
                self.start_chat_log(profile)
            if state == "reused":
                self.say(f"{profile.name} is already running at {profile.url}.")
            else:
                self.say(
                    f"Starting {profile.name} (context {context_label(profile.runtime.context)})..."
                )
                seconds = self.runtime.wait_ready(
                    profile, on_line=lambda line: self.say(f"  {line}")
                )
                self.say(f"Ready after {seconds:.0f} s. Warming up...")
                self.runtime.warm_up(profile)
            for problem in self.runtime.verify(profile):
                self.say(f"Warning: the running server differs from the profile: {problem}")
            if (
                profile.integrations.get("claude_code")
                and profile.runtime.template.source == "gguf"
            ):
                self._template_self_test(profile)
        except KeyboardInterrupt:
            # A half-started server must not be left behind holding memory.
            if ask_to_stop(self.app.out, read_line_with_timeout):
                self.stop_server(profile)
                self.say("Server stopped; its memory is free again.")
            else:
                self.say(
                    f"The server keeps running. Stop it with: ornith-launcher stop {profile.name}"
                )
            return 130
        except ServerError:
            if started:  # never stop a server this launch did not start
                self.stop_server(profile)
            raise
        self.print_ready(profile)
        if monitor:
            Monitor(
                self.app.host,
                self.runtime,
                profile,
                out=self.app.out,
                reopen_chat=(lambda: self.open_chat_window(profile)) if chat else None,
                on_tick=(lambda: self.keep_chat_log(profile)) if chat else None,
                on_stop=lambda: chat_log.shutdown(self.app, profile.name),
            ).run()
        return 0

    def stop_server(self, profile: Profile) -> None:
        """Stop the server, and the chat log proxy and window with it."""
        self.runtime.stop(profile)
        chat_log.shutdown(self.app, profile.name)

    def _check_chat_port(self, profile: Profile) -> None:
        """The chat log proxy needs the profile's port; say so when another program has it."""
        if self.runtime.status(profile).alive or chat_log.proxy_info(self.app, profile.name):
            return  # this server's own port; start() tells whether it can be reused
        port = profile.runtime.port
        if not chat_log.port_free(port):
            raise LaunchError(
                f"port {port} is in use by another program, so the chat log cannot listen there",
                hint=(
                    "Close that program, or start without the chat log this time: "
                    f"ornith-launcher launch {profile.name} --no-chat-log"
                ),
            )

    def start_chat_log(self, profile: Profile) -> None:
        problem = chat_log.start_proxy(self.app, profile)
        if problem is not None:
            raise ServerError(
                f"the chat log could not start: {problem}",
                hint=f"Start without it this time: ornith-launcher launch {profile.name} "
                "--no-chat-log",
            )
        self.open_chat_window(profile)

    def keep_chat_log(self, profile: Profile) -> None:
        """Start the proxy again if it ended while the server still runs (clients need it)."""
        if chat_log.proxy_info(self.app, profile.name) is not None:
            return
        if not self.runtime.status(profile).alive:
            return
        problem = chat_log.start_proxy(self.app, profile)
        if problem is None:
            self.say("The chat log proxy had stopped; it runs again.")
        else:
            self.say(f"Warning: the chat log proxy stopped and could not start again: {problem}")

    def open_chat_window(self, profile: Profile) -> bool:
        if detect_platform(self.app.host).headless:
            self.say(
                "There is no desktop here for a chat log window. See the chat log in another "
                f"terminal with: ornith-launcher chat-log {profile.name}"
            )
            return False
        return chat_log.open_window(self.app, profile)

    def print_ready(self, profile: Profile) -> None:
        self.say()
        self.say(f"{profile.name} is ready at {profile.url} (model name {profile.runtime.alias}).")
        self.say("  OpenAI-compatible API: " + f"{profile.url}/v1")
        claude = profile.integrations.get("claude_code") or {}
        if claude.get("wrapper"):
            self.say(f"  Claude Code: run {Path(claude['wrapper']).name}")
        opencode = profile.integrations.get("opencode") or {}
        if opencode.get("provider"):
            self.say(
                f"  OpenCode: /models -> {opencode.get('label', 'Ornith (local, ornith-launcher)')}"
            )

    def _template_self_test(self, profile: Profile) -> None:
        """Claude Code sends system messages mid-conversation; check the template takes one."""
        messages = [
            {"role": "system", "content": "a"},
            {"role": "user", "content": "b"},
            {"role": "assistant", "content": "c"},
            {"role": "system", "content": "d"},
            {"role": "user", "content": "e"},
        ]
        reply = self.runtime.http.post_json(
            f"{self.runtime.api_url(profile)}/apply-template", {"messages": messages}, timeout=30
        )
        if reply.status >= 400:
            self.say(
                "Warning: this model's chat template rejects a system message in the middle of "
                "a conversation, so Claude Code requests will fail. Use OpenCode with it, or "
                "pick another build of the model."
            )

    def _make_room(self, profile: Profile) -> bool:
        others = [
            c
            for c in self.runtime.running_profiles()
            if c.labels.get(PROFILE_LABEL) != profile.name
        ]
        if others:
            names = ", ".join(c.labels.get(PROFILE_LABEL, c.name) for c in others)
            self.say(
                f"Another Ornith server is running ({names}); two rarely fit in memory together."
            )
            if not self.prompter.confirm("Stop it first?", default=True):
                self.say("Not starting. Stop it with: ornith-launcher stop")
                return False
            for container in others:
                self.runtime.remove(container.name)
        for server in self.runtime.other_model_servers():
            self.say(
                f"Warning: another model server is running ({server}); it may need the same memory."
            )
        return True

    def _memory_warning(self, profile: Profile) -> None:
        memory = parse_meminfo(self.app.host.read_text("/proc/meminfo") or "")
        available = memory.get("MemAvailable")
        needed = profile.plan.estimate_gib * GIB
        if available is not None and profile.runtime.placement == "single" and available < needed:
            self.say(
                f"Warning: about {profile.plan.estimate_gib:.0f} GiB is needed but only "
                f"{available / GIB:.0f} GiB is free right now. Close other apps if loading fails."
            )


def make_runtime(app: App) -> DockerRuntime:
    return DockerRuntime(app.host)


def prompter_for(app: App, assume_yes: bool = False) -> PlainPrompter:
    """The same prompter setup uses: full screen in a terminal, so Esc and keys act at once."""
    from ornith_launcher.wizard import make_prompter

    return make_prompter(app, assume_yes=assume_yes)


# Commands -------------------------------------------------------------------------------


def add_launch_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("name", nargs="?", help="profile name (default: the default profile)")
    parser.add_argument("--here", action="store_true", help="run in this terminal")
    parser.add_argument("--no-monitor", action="store_true", help="start the server and return")
    chat = parser.add_mutually_exclusive_group()
    chat.add_argument(
        "--chat-log",
        dest="chat_log",
        action="store_const",
        const=True,
        help="also show requests and replies in a chat log window, this time",
    )
    chat.add_argument(
        "--no-chat-log",
        dest="chat_log",
        action="store_const",
        const=False,
        help="no chat log this time",
    )


def run_launch(app: App, args: argparse.Namespace) -> int:
    prompter = prompter_for(app)
    name = resolve_name(app, args.name, prompter)
    profile = load_profile(app.paths, name)
    runtime = make_runtime(app)
    # A missing image is fetched, not refused: removing only the model server must not
    # break a setup's desktop icon.
    problems = preflight(app, profile, runtime, check_image=False)
    if problems:
        for problem in problems:
            app.say(problem)
        raise LaunchError(f"{name} cannot start until the problems above are fixed")
    chat = getattr(args, "chat_log", None)
    if not args.here and _hop_to_terminal(app, profile, chat):
        return 0  # the new window asks about the image, where there is someone to answer
    fetch_image(app, runtime, profile, prompter)
    return Launcher(app, runtime, prompter).serve(profile, monitor=not args.no_monitor, chat=chat)


def fetch_image(
    app: App, runtime: DockerRuntime, profile: Profile, prompter: PlainPrompter
) -> None:
    """Download the llama.cpp image when it is gone, after asking."""
    reference = profile.runtime.image
    if runtime.image_present(reference):
        return
    app.say(f"The model server (llama.cpp image {reference.split('@')[0]}) is not on this machine.")
    if not prompter.confirm("Download it now (a few GB)?", default=True):
        raise LaunchError(
            f"{profile.name} needs the model server to start",
            hint=f"Download it with: ornith-launcher setup --repair {profile.name}",
        )
    runtime.pull(reference)
    record(app.paths, "images", reference)


def _hop_to_terminal(app: App, profile: Profile, chat: bool | None = None) -> bool:
    """Open the chosen terminal and run there, unless this already is that terminal."""
    terminal_id = profile.terminal.get("id") or THIS_TERMINAL
    if terminal_id == THIS_TERMINAL:
        return False
    interactive = getattr(app.out, "isatty", lambda: False)()
    if interactive and runs_in(terminal_id, app.env):
        return False
    terminal = by_id(terminal_id, detect_terminals(app.host))
    if terminal is None:
        app.say(f"The terminal {terminal_id} is no longer installed; running here instead.")
        return False
    command = [*self_command(), "launch", profile.name, "--here"]
    if chat is not None:
        command.append("--chat-log" if chat else "--no-chat-log")
    if open_in_terminal(app.host, terminal, command, f"Ornith: {profile.name}"):
        app.say(f"Opened {profile.name} in {terminal.name}.")
        return True
    app.say(f"Could not open {terminal.name}; running here instead.")
    return False


def add_name_argument(parser: argparse.ArgumentParser, required: bool = False) -> None:
    parser.add_argument("name", nargs=None if required else "?", help="profile name")


def run_stop(app: App, args: argparse.Namespace) -> int:
    runtime = make_runtime(app)
    running = runtime.running_profiles()
    if args.name:
        targets = [c for c in running if c.labels.get(PROFILE_LABEL) == args.name]
        if not targets:
            app.say(f"{args.name} is not running.")
            return 0
    else:
        targets = running
        if not targets:
            app.say("No Ornith server is running.")
            return 0
    for container in targets:
        runtime.remove(container.name)
        name = container.labels.get(PROFILE_LABEL, container.name)
        chat_log.shutdown(app, name)
        app.say(f"Stopped {name}; memory released.")
    return 0


def run_status(app: App, args: argparse.Namespace) -> int:
    runtime = make_runtime(app)
    names = [args.name] if args.name else list_profiles(app.paths)
    settings = load_settings(app.paths)
    app.say(f"Models folder: {settings.models_path(app.paths)}")
    if not names:
        app.say("No profiles yet. Create one with: ornith-launcher setup")
        return 0
    rows = []
    for name in names:
        profile = load_profile(app.paths, name)
        container = runtime.status(profile)
        health = "-"
        if container.alive:
            health = (
                "ready"
                if runtime.http.get(f"{runtime.api_url(profile)}/health").status == 200
                else "loading"
            )
        rows.append(
            [name, container.state, health, profile.url, context_label(profile.runtime.context)]
        )
    app.say(table(["Profile", "Container", "Server", "URL", "Context"], rows))
    memory = parse_meminfo(app.host.read_text("/proc/meminfo") or "")
    if "MemAvailable" in memory:
        app.say(f"Memory free: {memory['MemAvailable'] / GIB:.0f} GiB")
    if getattr(args, "verbose_command", False):
        from ornith_launcher.runtime.docker import run_args

        for name in names:
            app.say(" ".join(run_args(load_profile(app.paths, name))))
    return 0


def run_list(app: App, args: argparse.Namespace) -> int:
    names = list_profiles(app.paths)
    if not names:
        app.say("No profiles yet. Create one with: ornith-launcher setup")
        return 0
    default = load_settings(app.paths).default_profile
    rows = []
    for name in names:
        profile = load_profile(app.paths, name)
        model = profile.model
        rows.append(
            [
                name + (" (default)" if name == default else ""),
                _model_label(profile),
                context_label(profile.runtime.context),
                str(profile.runtime.port),
                model.location,
            ]
        )
    app.say(table(["Profile", "Model", "Context", "Port", "Model file"], rows))
    return 0


def run_monitor(app: App, args: argparse.Namespace) -> int:
    profile = load_profile(app.paths, resolve_name(app, args.name))
    Monitor(app.host, make_runtime(app), profile, out=app.out).run()
    return 0


def run_logs(app: App, args: argparse.Namespace) -> int:
    """Follow a server's own log here; Ctrl+C ends only the log, never the server."""
    profile = load_profile(app.paths, resolve_name(app, args.name))
    runtime = make_runtime(app)
    if not runtime.status(profile).alive:
        app.say(
            f"{profile.name} is not running. Start it with: ornith-launcher launch {profile.name}"
        )
        return 1
    try:
        runtime.follow_logs(profile, LOG_TAIL)
    except KeyboardInterrupt:
        app.say()
        app.say("Closed the log. The server keeps running.")
    return 0


def add_chat_log_arguments(parser: argparse.ArgumentParser) -> None:
    add_name_argument(parser)
    parser.add_argument(
        "--window",
        action="store_true",
        help="open it in a new window (closing one that is still open)",
    )
    parser.add_argument(chat_log.IN_WINDOW, action="store_true", help=argparse.SUPPRESS)


def run_chat_log(app: App, args: argparse.Namespace) -> int:
    """Show requests and replies here, or in their own window; the server is never stopped."""
    profile = load_profile(app.paths, resolve_name(app, args.name))
    runtime = make_runtime(app)
    if args.window:
        return 0 if Launcher(app, runtime, prompter_for(app)).open_chat_window(profile) else 1
    return chat_log.follow(app, runtime, profile, in_window=args.in_window)


def run_chat_proxy(app: App, args: argparse.Namespace) -> int:
    """The chat log proxy's own process, started by launch (not for direct use)."""
    profile = load_profile(app.paths, args.name)
    return chat_log.run_proxy(app, make_runtime(app), profile)


def run_remove(app: App, args: argparse.Namespace) -> int:
    from ornith_launcher.uninstall import remove_profile

    prompter = prompter_for(app, assume_yes=args.yes)
    profile = load_profile(app.paths, args.name)
    remove_profile(app, profile, make_runtime(app), prompter)
    settings = load_settings(app.paths)
    if settings.default_profile == profile.name:
        settings.default_profile = None
        save_settings(app.paths, settings)
    delete_profile(app.paths, profile.name)
    app.say(f"Removed the profile {profile.name}.")
    return 0


def default_action(app: App) -> int:
    """``ornith-launcher`` with no command opens setup; it never starts a server by itself.

    Setup's first menu offers "Launch a model" once a setup exists.
    """
    from ornith_launcher import wizard

    return wizard.run_setup(app, wizard.default_args())


def _model_label(profile: Profile) -> str:
    model = profile.model
    return f"Ornith {model.version or '?'} {model.size_class or '?'} {model.quant or ''}".strip()
