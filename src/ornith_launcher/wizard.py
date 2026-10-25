"""The setup wizard: from a fresh machine to a model ready to launch.

Screens, in order: disclaimer, system check, models already on the machine,
model list, options, models folder, summary, download. Each screen is a method
that asks through a Prompter, so the same flow runs full-screen, in plain text
and in tests. Nothing is changed on disk before the summary is confirmed,
except the settings the user explicitly chose.
"""

from __future__ import annotations

import datetime
import socket
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ornith_launcher.app import App
from ornith_launcher.catalog.catalog import Entry
from ornith_launcher.catalog.classify import Badge, Source, load_rules
from ornith_launcher.catalog.hfapi import HubClient
from ornith_launcher.catalog.local import (
    LocalModel,
    as_entry,
    cached_sha256,
    check_entered_path,
    clean_path_input,
    confirm_checksum,
    identify,
    quick_search,
    search_entered_path,
    search_locations,
    sort_found,
)
from ornith_launcher.download import (
    Downloader,
    DownloadPaused,
    HttpRangeSource,
    Status,
    Target,
    format_status,
)
from ornith_launcher.errors import OrnithLauncherError
from ornith_launcher.folders import check_models_folder, ensure_folder, read_mounts
from ornith_launcher.planner.memory_model import load_calibration
from ornith_launcher.planner.rank import (
    Candidate,
    Filters,
    Level,
    build_candidates,
    load_tested,
    rank,
)
from ornith_launcher.resources import load_text
from ornith_launcher.scan import Plan, make_plan, print_system
from ornith_launcher.settings import Settings, load_settings, save_settings
from ornith_launcher.ui.plain import context_label, gib
from ornith_launcher.ui.prompts import Cancelled, Choice, PlainPrompter

DISCLAIMER_FILE = "disclaimer.txt"
UNCENSORED_PHRASE = "I understand"
FIRST_PORT = 18400
PAGE = 40
HASH_CACHE = "file-hashes.json"
ASSUMED_SPEED = 50e6  # bytes per second, for the download time shown before starting


class SetupError(OrnithLauncherError):
    pass


@dataclass
class Selection:
    """What the user chose; the runtime steps turn it into a profile."""

    entry: Entry
    candidate: Candidate
    local: LocalModel | None = None  # set when the model is already on the machine
    port: int = FIRST_PORT
    models_dir: Path | None = None  # where a download goes; None for a local model
    model_paths: list[Path] = field(default_factory=list)  # real paths of the model parts
    mmproj_path: Path | None = None
    consents: dict[str, str] = field(default_factory=dict)  # name -> ISO timestamp

    @property
    def needs_download(self) -> bool:
        return self.local is None


def disclaimer_text() -> tuple[int, str]:
    raw = load_text(DISCLAIMER_FILE)
    first, _, body = raw.partition("\n")
    return int(first.split(":", 1)[1]), body.strip()


def first_free_port(start: int = FIRST_PORT, attempts: int = 100) -> int:
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise SetupError(
        f"no free port between {start} and {start + attempts - 1}",
        hint="Stop other servers, or pass a port: ornith-launcher setup --port 18500",
    )


@dataclass
class Wizard:
    app: App
    prompter: PlainPrompter
    settings: Settings
    model_path: Path | None = None  # setup --model PATH: skip the quick search
    accept_uncensored: bool = False  # --accept-uncensored, needed with --yes
    port: int | None = None
    downloader_factory: Callable[[Callable[[Status], None]], Downloader] | None = None
    plan: Plan | None = None  # set by run(); the later setup steps reuse it
    now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(datetime.timezone.utc)

    @property
    def say(self) -> Callable[[str], None]:
        return self.prompter.say

    # Flow -----------------------------------------------------------------------------

    def run(self) -> Selection:
        self.disclaimer()
        plan = self.system_check()
        self.plan = plan
        selection = self.existing_models(plan)
        if selection is None:
            selection = self.browse(plan)
        selection = self.options(selection, plan)
        if selection.needs_download:
            self.models_folder(selection)
        self.summary(selection)
        if selection.needs_download:
            self.download(selection)
        return selection

    # 1. Disclaimer ----------------------------------------------------------------------

    def disclaimer(self) -> None:
        version, text = disclaimer_text()
        if self.settings.disclaimer_version >= version:
            return
        self.say(text)
        self.say()
        if not self.prompter.confirm("Continue?", default=True):
            raise Cancelled()
        self.settings.disclaimer_version = version
        save_settings(self.app.paths, self.settings)

    # 2. System check --------------------------------------------------------------------

    def system_check(self) -> Plan:
        self.say()
        self.say("Checking this machine...")
        plan = make_plan(self.app, show_uncensored=self.settings.show_uncensored)
        print_system(self.app, plan.report)
        if plan.report.blocking:
            raise SetupError(
                "setup cannot continue until the problems above are fixed",
                hint="Fix them, then run: ornith-launcher setup",
            )
        if plan.report.warnings and not self.prompter.confirm("Continue anyway?", default=True):
            raise Cancelled()
        if plan.catalog.source == "empty":
            self.say("The list of models is empty.")
            if not self.prompter.confirm("Download it from Hugging Face now (a few minutes)?"):
                raise Cancelled()
            plan = make_plan(self.app, refresh=True, show_uncensored=self.settings.show_uncensored)
        return plan

    # 2b. Models already on the machine ---------------------------------------------------

    def existing_models(self, plan: Plan) -> Selection | None:
        if self.model_path is not None:
            return self._use_entered(self.model_path, plan)
        self.say()
        self.say("Looking for Ornith models already on this machine...")
        locations = search_locations(
            self.app.env,
            Path.home() if "HOME" not in self.app.env else Path(self.app.env["HOME"]),
            self.app.os_name,
            self.settings.models_path(self.app.paths),
            [Path(p) for p in self.settings.extra_model_dirs],
        )
        result = quick_search(locations)
        for folder, why in result.skipped:
            self.say(f"  Skipped {folder}: {why}")
        models = sort_found(self._identify_all(result.files, plan))
        while True:
            if models:
                choices = [self._local_choice(m, plan) for m in models]
                picked = self.prompter.choose(
                    "Ornith models found on this machine (used where they are, never moved):",
                    ["Model", "Quant", "Size", "Fits", "App", "Path"],
                    choices,
                    keys={"e": "enter a location", "b": "browse and download"},
                )
            else:
                self.say("No Ornith models found in the usual places.")
                picked = self.prompter.choose(
                    "What next?",
                    [],
                    [],
                    keys={
                        "e": "use an existing model (enter its location)",
                        "b": "browse and download",
                    },
                )
            if picked == "b":
                return None
            if picked == "e":
                path = self._ask_location()
                if path is None:
                    continue
                selection = self._use_entered(path, plan)
                if selection is not None:
                    return selection
                continue
            selection = self._use_local(models[int(picked)], plan)
            if selection is not None:
                return selection

    def _identify_all(self, files: Sequence, plan: Plan) -> list[LocalModel]:
        rules = load_rules()
        models = []
        for item in files:
            model = identify(item, plan.families, rules, plan.entries)
            if model is not None and model.classification.shape is not None:
                models.append(model)
        return models

    def _local_choice(self, model: LocalModel, plan: Plan) -> Choice:
        cls = model.classification
        best = self._best_candidate(as_entry(model), plan) if model.supported else None
        name = cls.family.display_name if cls.family else f"Ornith ? {cls.size_label or '?'}"
        if model.likely is not None:
            source = f"probably {model.likely.repo.id}"
        else:
            source = "unknown source"
        fits = "yes" if best and best.fits else ("no" if best else "-")
        detail = f"{source}; {', '.join(b.value for b in cls.badges) or 'no badges'}"
        if model.file.incomplete:
            detail = model.file.incomplete
        return Choice(
            cells=(
                name,
                cls.quant or "?",
                gib(model.file.size),
                fits,
                _app_name(model.file.owner_app),
                str(model.file.path),
            ),
            detail=detail,
            enabled=model.supported,
        )

    def _ask_location(self) -> Path | None:
        raw = self.prompter.text("Path to a model file or folder (Enter to go back)")
        if not raw:
            return None
        home = Path(self.app.env.get("HOME", str(Path.home())))
        path = clean_path_input(raw, self.app.env, home)
        problem = check_entered_path(path)
        if problem:
            self.say(problem)
            return None
        return path

    def _use_entered(self, path: Path, plan: Plan) -> Selection | None:
        problem = check_entered_path(path)
        if problem:
            raise SetupError(problem)
        found = search_entered_path(path)
        models = self._identify_all(found.files, plan)
        if not found.files:
            self.say(f"No GGUF model (1 GiB or larger) was found at {path}.")
            return None
        if not models:
            self.say(f"{path} is not a GGUF file this tool can read.")
            return None
        if path.is_dir():
            self.settings.remember_folder(path)
            save_settings(self.app.paths, self.settings)
        if len(models) > 1:
            picked = self.prompter.choose(
                f"Models in {path}:",
                ["Model", "Quant", "Size", "Fits", "App", "Path"],
                [self._local_choice(m, plan) for m in models],
            )
            model = models[int(picked)]
        else:
            model = models[0]
        return self._use_local(model, plan)

    def _use_local(self, model: LocalModel, plan: Plan) -> Selection | None:
        cls = model.classification
        if model.file.incomplete:
            self.say(f"{model.file.path} is {model.file.incomplete}; it cannot be used.")
            return None
        if not cls.supported:
            arch = model.header.get("general.architecture", "unknown")
            self.say(f"{model.file.path} is not a supported Ornith size ({arch}: {cls.excluded}).")
            return None
        entry = as_entry(model)
        best = self._best_candidate(entry, plan)
        if best is None:
            self.say("This model cannot be planned on this machine.")
            return None
        selection = Selection(entry=entry, candidate=best, local=model)
        # Ask for consents before the minute-long checksum, not after it.
        if not self._consents(selection):
            return None
        if model.likely is not None and not model.confirmed:
            self.say(f"Checking {model.file.path.name} against {model.likely.repo.id}...")
            progress = ProgressLine(self.prompter.out)
            matched = confirm_checksum(
                model,
                self.app.paths.cache / HASH_CACHE,
                lambda done: progress.update(
                    Status(model.file.path.name, "verify", done, model.file.size)
                ),
            )
            progress.finish()
            if not matched:
                self.say("The checksum does not match the catalog: treating it as a local file.")
                model = LocalModel(**{**model.__dict__, "likely": None, "confirmed": False})
                entry = as_entry(model)
                selection = Selection(entry=entry, candidate=best, local=model)
                if not self._consents(selection):  # the licence is no longer known
                    return None
        selection.model_paths = list(model.file.parts)
        selection.mmproj_path = model.mmproj
        return selection

    # 3. Model list ------------------------------------------------------------------------

    def browse(self, plan: Plan) -> Selection:
        show_all = False
        search = ""
        page = 0
        while True:
            if not show_all:
                rows = [s.candidate for s in plan.suggestions]
                choices = [
                    Choice(self._cells(c), detail=s.reason) for c, s in zip(rows, plan.suggestions)
                ]
                title = "Suggested for this machine:"
                keys = {"a": "all models", "u": self._uncensored_label()}
            else:
                rows = self._full_rows(plan, search)
                start = page * PAGE
                rows_page = rows[start : start + PAGE]
                choices = [
                    Choice(self._cells(c), detail=self._detail(c), enabled=c.fits)
                    for c in rows_page
                ]
                title = f"All models ({len(rows)} files, best first; page {page + 1}):"
                keys = {"s": "search", "b": "suggested", "u": self._uncensored_label()}
                if start + PAGE < len(rows):
                    keys["n"] = "next page"
                if page:
                    keys["p"] = "previous page"
                rows = rows_page
            if not choices and not show_all:
                self.say("Nothing fits this machine with 15% memory to spare; showing all models.")
                show_all = True
                continue
            picked = self.prompter.choose(
                title,
                ["Model", "Source", "Quant", "Context", "Memory", "Fits", "Licence"],
                choices,
                keys=keys,
            )
            if picked == "a":
                show_all, page = True, 0
            elif picked == "b":
                show_all, page = False, 0
            elif picked == "n":
                page += 1
            elif picked == "p":
                page = max(0, page - 1)
            elif picked == "s":
                search = self.prompter.text("Search (words in the repo or file name)")
                page = 0
            elif picked == "u":
                plan = self._toggle_uncensored(plan)
            else:
                candidate = rows[int(picked)]
                selection = Selection(entry=candidate.entry, candidate=candidate)
                if self._consents(selection):
                    return selection

    def _full_rows(self, plan: Plan, search: str) -> list[Candidate]:
        """One row per model file: its best setting, fitting ones first."""
        filters = Filters(show_uncensored=self.settings.show_uncensored, search=search)
        seen: set[tuple[str, str]] = set()
        rows = []
        for candidate in plan.ranked:
            key = (candidate.entry.repo.id, candidate.entry.file.path)
            if key in seen or not filters.accepts(candidate):
                continue
            seen.add(key)
            rows.append(candidate)
        return rows

    def _cells(self, c: Candidate) -> tuple[str, ...]:
        cls = c.entry.classification
        name = cls.family.display_name if cls.family else f"Ornith ? {cls.size_label or '?'}"
        return (
            name,
            cls.source.value,
            cls.quant or "?",
            context_label(c.context),
            gib(c.estimate.total),
            "yes" if c.fits else "no",
            c.entry.repo.licence or "none",
        )

    def _detail(self, c: Candidate) -> str:
        parts = [f"{c.entry.repo.id}/{c.entry.file.path}"]
        if not c.fits and c.shortfall:
            parts.append(c.shortfall)
        badges = [b for b in c.badges if b not in (Level.ESTIMATED.value,)]
        if badges:
            parts.append(" ".join(badges))
        parts.extend(c.notes)
        return "; ".join(parts)

    def _uncensored_label(self) -> str:
        return "hide uncensored" if self.settings.show_uncensored else "show uncensored"

    def _toggle_uncensored(self, plan: Plan) -> Plan:
        if not self.settings.show_uncensored:
            self.say(
                "Uncensored (abliterated) models have their safety behaviour removed and can "
                "produce harmful or illegal content. You alone are responsible for how you use "
                "them. They stay listed until you hide them again."
            )
            if not self.prompter.confirm("Show them?", default=False):
                return plan
        self.settings.show_uncensored = not self.settings.show_uncensored
        save_settings(self.app.paths, self.settings)
        return make_plan(self.app, show_uncensored=self.settings.show_uncensored)

    def _consents(self, selection: Selection) -> bool:
        """Typed or explicit confirmations for uncensored, licence-less and third-party models."""
        cls = selection.entry.classification
        stamp = self.now().isoformat(timespec="seconds")
        if cls.uncensored:
            if self.prompter.assume_yes and not self.accept_uncensored:
                raise SetupError(
                    "this model is uncensored; --yes alone does not accept that",
                    hint="Run setup without --yes, or add --accept-uncensored.",
                )
            if not self.accept_uncensored and not self.prompter.typed_confirmation(
                "This model is uncensored: its safety behaviour was removed and it can produce "
                "harmful or illegal content. You alone are responsible for how you use it.",
                UNCENSORED_PHRASE,
            ):
                return False
            selection.consents["uncensored_ack"] = stamp
        licence = selection.entry.repo.licence
        if licence in (None, "other"):
            what = "declares no licence" if licence is None else "uses a non-standard licence"
            self.say(f"This model {what}. Check its terms on its Hugging Face page before use.")
            if not self.prompter.confirm("I have checked the licence. Continue?", default=False):
                return False
            selection.consents["licence_ack"] = stamp
        if cls.source is Source.COMMUNITY:
            self.say("Third-party build, not from Ornith AI.")
        if Badge.UNTESTED_LOCALLY in cls.badges:
            self.say("This size has not been run by the project yet (untested locally).")
        return True

    def _best_candidate(self, entry: Entry, plan: Plan) -> Candidate | None:
        rows = self._candidates_for(entry, plan)
        return rows[0] if rows else None

    def _candidates_for(self, entry: Entry, plan: Plan) -> list[Candidate]:
        candidates = build_candidates([entry], plan.report.pools, load_calibration(), load_tested())
        return rank(candidates, plan.families)

    # 4. Options -------------------------------------------------------------------------

    def options(self, selection: Selection, plan: Plan) -> Selection:
        rows = [
            c
            for c in self._candidates_for(selection.entry, plan)
            if c.kv_type == selection.candidate.kv_type
            and c.placement == selection.candidate.placement
        ]
        contexts: list[Candidate] = []
        for c in rows:
            if all(c.context != seen.context for seen in contexts):
                contexts.append(c)
        contexts.sort(key=lambda c: c.context)
        default = next(
            (i for i, c in enumerate(contexts) if c.context == selection.candidate.context), 0
        )
        picked = self.prompter.choose(
            "Context size (the longest prompt the model can read at once):",
            ["Context", "Memory", "Fits", "Tested"],
            [
                Choice(
                    (
                        context_label(c.context) + (" (native)" if c.context == c.native else ""),
                        gib(c.estimate.total),
                        "yes" if c.fits else "no",
                        c.level.value,
                    ),
                    detail=_context_note(c),
                    enabled=c.fits,
                )
                for c in contexts
            ],
            default=default,
        )
        chosen = contexts[int(picked)]
        port = self.port or first_free_port()
        has_vision = selection.entry.repo.best_mmproj() is not None
        summary = (
            f"KV cache f16, {chosen.slots} parallel requests, "
            f"vision {'on' if chosen.vision else ('off' if has_vision else 'not available')}, "
            f"port {port}"
        )
        if not self.prompter.confirm(f"Use these settings: {summary}?", default=True):
            chosen = self._custom_options(selection, plan, chosen)
            answer = self.prompter.text("Port", str(port))
            port = int(answer) if answer.isdigit() else port
        selection.candidate = chosen
        selection.port = port
        if selection.local is not None and not chosen.vision:
            selection.mmproj_path = None
        return selection

    def _custom_options(self, selection: Selection, plan: Plan, chosen: Candidate) -> Candidate:
        kv = "f16"
        if self.prompter.confirm(
            "Use the q8_0 KV cache (about half the memory, UNTESTED with Ornith)?", default=False
        ):
            kv = "q8_0"
        slots = (
            4
            if self.prompter.confirm(
                "Allow 4 parallel requests (good for coding agents)? No = 1", default=True
            )
            else 1
        )
        vision = chosen.vision
        if selection.entry.repo.best_mmproj() is not None:
            vision = self.prompter.confirm("Turn vision (image input) on?", default=chosen.vision)
        candidates = build_candidates(
            [selection.entry],
            plan.report.pools,
            load_calibration(),
            load_tested(),
            slots=slots,
            contexts=[chosen.context],
            kv_types=[kv],
        )
        matching = [
            c
            for c in rank(candidates, plan.families)
            if c.vision == vision and c.placement == chosen.placement
        ]
        if not matching:
            return chosen
        result = matching[0]
        if not result.fits:
            self.say(f"Warning: these settings do not fit ({result.shortfall}).")
            if not self.prompter.confirm("Keep them anyway?", default=False):
                return chosen
        return result

    # 4b. Models folder ----------------------------------------------------------------------

    def models_folder(self, selection: Selection) -> None:
        size = self._download_size(selection)
        folder = self.settings.models_path(self.app.paths)
        home = Path(self.app.env.get("HOME", str(Path.home())))
        mounts = read_mounts(self.app.host.read_text("/proc/mounts") or "")
        while True:
            check = check_models_folder(
                folder, size, home=home, os_name=self.app.os_name, mounts=mounts
            )
            self.say()
            self.say(f"Models folder: {folder}")
            if check.free is not None:
                self.say(f"  Download {gib(size)}; free space {gib(check.free)}.")
            if check.hidden_notice:
                self.say(f"  {check.hidden_notice}")
            for error in check.errors:
                self.say(f"  Problem: {error}")
            for warning in check.warnings:
                self.say(f"  Warning: {warning}")
            keep = check.ok and self.prompter.confirm("Keep this folder?", default=True)
            if (
                keep
                and check.warnings
                and not self.prompter.confirm("Use it anyway?", default=False)
            ):
                keep = False
            if keep:
                break
            answer = self.prompter.text("Folder for downloaded models")
            if answer:
                folder = clean_path_input(answer, self.app.env, home)
            elif not check.ok:
                self.say("That folder cannot be used; please enter another one.")
        ensure_folder(folder)
        if folder != self.settings.models_path(self.app.paths):
            self.settings.models_dir = str(folder)
            save_settings(self.app.paths, self.settings)
        selection.models_dir = folder

    def _download_size(self, selection: Selection) -> int:
        size = selection.entry.file.size
        mmproj = selection.entry.repo.best_mmproj()
        if selection.candidate.vision and mmproj is not None:
            size += mmproj.size
        return size

    # 7. Summary -----------------------------------------------------------------------------

    def summary(self, selection: Selection) -> None:
        c = selection.candidate
        cls = selection.entry.classification
        self.say()
        self.say("Summary")
        name = cls.family.display_name if cls.family else "Ornith model"
        self.say(f"  Model:    {name} {cls.quant or ''} ({selection.entry.repo.id})")
        self.say(
            f"  Settings: context {context_label(c.context)}, KV {c.kv_type}, {c.slots} slots, "
            f"vision {'on' if c.vision else 'off'}, port {selection.port}"
        )
        self.say(f"  Memory:   about {gib(c.estimate.total)} ({c.level.value})")
        if selection.local is not None:
            self.say(f"  File:     {selection.local.file.real_path}")
            self.say("            Used where it is; ornith-launcher will not move or delete it.")
            if selection.local.file.owner_app:
                self.say(f"            It belongs to {_app_name(selection.local.file.owner_app)}.")
        else:
            size = self._download_size(selection)
            minutes = size / ASSUMED_SPEED / 60
            self.say(
                f"  Download: {gib(size)} into {selection.models_dir} "
                f"(about {minutes:.0f} min at 50 MB/s; ESTIMATED)"
            )
        if cls.source is Source.COMMUNITY:
            self.say("  Note:     third-party build, not from Ornith AI.")
        flavour = self.plan.report.flavour if self.plan else None
        if flavour is not None:
            self.say(
                f"  Runtime:  llama.cpp {flavour.image.tag} in Docker "
                "(downloaded once if missing, a few GB)"
            )
        if not self.prompter.confirm("Go ahead?", default=True):
            raise Cancelled()

    # 8. Download ----------------------------------------------------------------------------

    def download(self, selection: Selection) -> None:
        repo = selection.entry.repo
        folder = selection.models_dir / repo.owner / f"{repo.id.split('/', 1)[1]}@{repo.sha[:7]}"
        targets = []
        hub = HubClient.from_env(self.app.env)
        for part in selection.entry.file.parts:
            targets.append(
                Target(
                    hub.resolve_url(repo.id, repo.sha, part.path),
                    folder / PurePosixPath(part.path).name,
                    part.size,
                    part.sha256,
                )
            )
        mmproj = repo.best_mmproj()
        if selection.candidate.vision and mmproj is not None:
            targets.append(
                Target(
                    hub.resolve_url(repo.id, repo.sha, mmproj.path),
                    folder / PurePosixPath(mmproj.path).name,
                    mmproj.size,
                    mmproj.sha256,
                )
            )
        progress = ProgressLine(self.prompter.out)
        downloader = (
            self.downloader_factory(progress.update)
            if self.downloader_factory
            else Downloader(
                source=HttpRangeSource(token=self.app.env.get("HF_TOKEN") or None),
                on_progress=progress.update,
            )
        )
        stop = threading.Event()
        for index, target in enumerate(targets, 1):
            self.say(f"Downloading {target.dest.name} ({index}/{len(targets)})")
            if target.dest.exists() and target.dest.stat().st_size == target.size:
                if target.sha256 is None or self._cached_sha(target.dest) == target.sha256:
                    self.say("  Already downloaded and verified.")
                    continue
            try:
                downloader.fetch(target, stop)
            except (DownloadPaused, KeyboardInterrupt):
                progress.finish()
                raise DownloadPaused(
                    "download paused; nothing is lost",
                    hint="Run ornith-launcher setup again to resume.",
                ) from None
            progress.finish()
        selection.model_paths = [t.dest for t in targets[: len(selection.entry.file.parts)]]
        if selection.candidate.vision and mmproj is not None:
            selection.mmproj_path = targets[-1].dest

    def _cached_sha(self, path: Path) -> str:
        return cached_sha256(path, self.app.paths.cache / HASH_CACHE)


class ProgressLine:
    """A progress line redrawn in place on a terminal, or printed every 10% elsewhere."""

    def __init__(self, out=None, min_interval: float = 0.5) -> None:
        self.out = out or sys.stdout
        self.tty = getattr(self.out, "isatty", lambda: False)()
        self.min_interval = min_interval
        self._last = 0.0
        self._last_step = -1
        self._shown = False

    def update(self, status: Status) -> None:
        if self.tty:
            now = time.monotonic()
            if now - self._last < self.min_interval and status.done < status.total:
                return
            self._last = now
            self.out.write("\r" + format_status(status) + "\x1b[K")
            self.out.flush()
        else:
            step = int(status.done * 10 / status.total) if status.total else 10
            if step == self._last_step:
                return
            self._last_step = step
            print(format_status(status), file=self.out, flush=True)
        self._shown = True

    def finish(self) -> None:
        if self.tty and self._shown:
            self.out.write("\n")
            self.out.flush()
        self._shown = False
        self._last_step = -1


def _context_note(c: Candidate) -> str:
    notes = list(c.notes)
    if c.yarn_factor:
        notes.insert(0, f"YaRN x{c.yarn_factor}: slower prefill on long prompts")
    if not c.fits and c.shortfall:
        notes.insert(0, c.shortfall)
    return "; ".join(notes)


def _app_name(owner_app: str | None) -> str:
    return {
        "hf-cache": "Hugging Face cache",
        "lmstudio": "LM Studio",
        "ollama": "Ollama",
        "llamacpp-cache": "llama.cpp cache",
        None: "-",
    }.get(owner_app, owner_app or "-")


# The setup command ------------------------------------------------------------------------


def add_arguments(parser) -> None:
    parser.add_argument("--model", type=Path, help="use this model file or folder; skip the search")
    parser.add_argument("--models-dir", type=Path, help="folder for downloaded models")
    parser.add_argument(
        "--port", type=int, help="port for the model server (default: 18400 or next free)"
    )
    parser.add_argument(
        "--plain", action="store_true", help="numbered menus instead of full-screen lists"
    )
    parser.add_argument("--yes", action="store_true", help="accept every default without asking")
    parser.add_argument("--name", help="profile name (default: made from the model and context)")
    parser.add_argument("--repair", metavar="NAME", help="put back what a profile needs")
    parser.add_argument(
        "--integrations", metavar="NAME", help="connect a profile to Claude Code or OpenCode"
    )
    parser.add_argument(
        "--accept-uncensored",
        action="store_true",
        help="accept an uncensored model with --yes (it is never accepted otherwise)",
    )


def make_prompter(app: App, *, plain: bool = False, assume_yes: bool = False) -> PlainPrompter:
    from ornith_launcher.ui.tui import TuiPrompter, supports_full_screen

    full = app.full_screen if app.full_screen is not None else supports_full_screen(stdout=app.out)
    cls = TuiPrompter if full and not plain else PlainPrompter
    return cls(read_line=app.read_line, out=app.out, assume_yes=assume_yes)


def default_args():
    import argparse

    return argparse.Namespace(
        model=None,
        models_dir=None,
        port=None,
        plain=False,
        yes=False,
        name=None,
        repair=None,
        integrations=None,
        accept_uncensored=False,
    )


def run_setup(app: App, args) -> int:
    from ornith_launcher import setup_profile

    settings = load_settings(app.paths)
    prompter = make_prompter(app, plain=args.plain, assume_yes=args.yes)
    if args.repair:
        return setup_profile.repair(app, prompter, args.repair)
    if getattr(args, "integrations", None):
        return setup_profile.change_integrations(app, prompter, args.integrations)
    if args.models_dir is not None:
        settings.models_dir = str(args.models_dir.expanduser())
    wizard = Wizard(
        app=app,
        prompter=prompter,
        settings=settings,
        model_path=args.model.expanduser() if args.model else None,
        accept_uncensored=args.accept_uncensored,
        port=args.port,
    )
    try:
        selection = wizard.run()
        finish(app, selection)
        profile = setup_profile.finish_setup(
            app, prompter, selection, wizard.plan, wizard.settings, args.name
        )
        return setup_profile.launch_now(app, prompter, profile)
    except Cancelled:
        app.say()
        app.say(
            "Setup stopped. Nothing else was changed; run ornith-launcher setup to start again."
        )
        return 1


def finish(app: App, selection: Selection) -> None:
    app.say()
    app.say("The model is ready:")
    for path in selection.model_paths:
        app.say(f"  {path}")
    if selection.mmproj_path:
        app.say(f"  {selection.mmproj_path} (vision)")
