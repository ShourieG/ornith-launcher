"""The setup wizard: from a fresh machine to a model ready to launch.

Screens, in order: disclaimer, system check, models already on the machine,
model list, options, models folder, summary, download. The first menu also
leads to "Launch a model" and the uninstall menu, and Back and Main menu work
on every screen up to the summary. Each screen is a method
that asks through a Prompter, so the same flow runs full-screen, in plain text
and in tests. Nothing is changed on disk before the summary is confirmed,
except the settings the user explicitly chose.
"""

from __future__ import annotations

import argparse
import datetime
import socket
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

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
from ornith_launcher.installed import record
from ornith_launcher.planner.memory_model import load_calibration
from ornith_launcher.planner.rank import (
    THIRD_PARTY_NOTE,
    Candidate,
    Filters,
    Level,
    build_candidates,
    load_tested,
    rank,
)
from ornith_launcher.profiles import list_profiles, load_profile
from ornith_launcher.resources import load_text
from ornith_launcher.scan import Plan, make_plan, print_system
from ornith_launcher.settings import Settings, load_settings, save_settings
from ornith_launcher.ui.plain import compact_count, context_label, gib, key_values
from ornith_launcher.ui.prompts import (
    BACK,
    MAIN_MENU,
    NEXT_PAGE,
    PREVIOUS_PAGE,
    Cancelled,
    Choice,
    PlainPrompter,
)
from ornith_launcher.unfinished import (
    PART,
    STATE,
    Unfinished,
    files_in_use,
    forget_choices,
    load_choices,
    save_choices,
    state_progress,
    unfinished_downloads,
)
from ornith_launcher.uninstall import EVERYTHING, delete_downloads, has_installs, uninstall_menu

DISCLAIMER_FILE = "disclaimer.txt"
UNCENSORED_PHRASE = "I understand"
UNCENSORED_WARNING = (
    "Uncensored (abliterated) models have their safety behaviour removed and can produce "
    "harmful or illegal content. You alone are responsible for how you use them."
)
UNCENSORED_LIST_NOTE = (
    "This list is a best-effort guess from model names, tags and lineage, so it can miss some "
    "or include others."
)
# The browse screen's three lists.
SUGGESTED, ALL_MODELS, UNCENSORED_ONLY = "suggested", "all", "uncensored"
BROWSE_HEADERS = [
    "Model", "Source", "Licence", "Quant", "Context", "Memory", "Download", "Fits", "Likes",
    "DLs/30d",
]  # fmt: skip
# Orders for the long lists: (label, what to sort by, largest first). Likes and downloads
# only sort most first; nobody looks for the least used model.
SORTS = (
    ("best first", "", False),
    ("memory, smallest first", "memory", False),
    ("memory, largest first", "memory", True),
    ("download size, smallest first", "download", False),
    ("download size, largest first", "download", True),
    ("likes, most first", "likes", True),
    ("downloads, most first", "downloads", True),
)
FIRST_PORT = 18400
PAGE = 40
HASH_CACHE = "file-hashes.json"
ASSUMED_SPEED = 50e6  # bytes per second, for the download time shown before starting
PROGRESS_INDENT = "     "  # under "1/2  Model: ..."
VISION_ROLE = "Vision file (lets the model read images)"


class SetupError(OrnithLauncherError):
    pass


class Back(Exception):
    """The user asked for the previous menu."""


class MainMenu(Exception):
    """The user asked for the first menu of setup."""


class Uninstalled(Exception):
    """Everything was removed from the setup menu, so setup ends."""


class LaunchRequested(Exception):
    """The user picked a setup to launch from the first menu; setup ends and it starts."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


@dataclass
class BrowseState:
    """Where browsing was, so Back from a later step returns to the same list and page."""

    views: list[str] = field(default_factory=lambda: [SUGGESTED])
    page: int = 0
    search: str = ""
    sort: int = 0  # index into SORTS

    def open(self, view: str) -> None:
        if view in self.views:
            del self.views[self.views.index(view) + 1 :]
        else:
            self.views.append(view)
        self.page = 0


def download_size(entry: Entry, vision: bool) -> int:
    """Bytes to download: the model file, plus the vision file when vision is on."""
    mmproj = entry.repo.best_mmproj()
    return entry.file.size + (mmproj.size if vision and mmproj is not None else 0)


def sorted_rows(rows: Sequence[Candidate], sort: int) -> list[Candidate]:
    """``rows`` in the order SORTS[sort] names; models that fit stay first."""
    _, by, largest_first = SORTS[sort]
    if not by:
        return list(rows)
    values = {
        "memory": lambda c: c.estimate.total,
        "download": lambda c: download_size(c.entry, c.vision),
        "likes": lambda c: c.entry.repo.likes,
        "downloads": lambda c: c.entry.repo.downloads,
    }
    value = values[by]
    sign = -1 if largest_first else 1
    # Stable, so ties keep the best-first order.
    return sorted(rows, key=lambda c: (not c.fits, sign * value(c)))


def _unfinished_notice(unfinished: Sequence[Unfinished]) -> str:
    if not unfinished:
        return ""
    if len(unfinished) == 1:
        u = unfinished[0]
        share = f" ({u.done * 100 // u.total}% done)" if u.total else ""
        return (
            f"A download was stopped: {u.model_name}{share}. Press r to resume it, or u to "
            "remove it."
        )
    return f"{len(unfinished)} downloads were stopped. Press r to resume one, or u to remove them."


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
    resumed: Unfinished | None = None  # a stopped download picked up again, in its own folder
    preset: bool = False  # the settings come from the stopped download: no options to ask

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
    browse_state: BrowseState = field(default_factory=BrowseState)
    found_models: list[LocalModel] | None = None  # the quick search, run once
    now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(datetime.timezone.utc)

    @property
    def say(self) -> Callable[[str], None]:
        return self.prompter.say

    # Flow -----------------------------------------------------------------------------

    def run(self) -> Selection:
        self.disclaimer()
        plan = self.system_check()
        self.plan = plan
        selection = self.choose_model()
        while not selection.preset:
            try:
                selection = self.options(selection, self.plan)
                break
            except Back:
                browsed = selection.local is None and selection.resumed is None
                selection = self.choose_model(resume_browse=browsed)
            except MainMenu:
                selection = self.choose_model()
        if selection.needs_download and selection.models_dir is None:
            self.models_folder(selection)  # a resumed download stays where its files are
        self.summary(selection)
        if selection.needs_download:
            self.download(selection)
        return selection

    def choose_model(self, resume_browse: bool = False) -> Selection:
        """The first menu, then browsing; Back and Main menu lead back here."""
        while True:
            if not resume_browse:
                self.browse_state = BrowseState()
                selection = self.existing_models(self.plan)
                if selection is not None:
                    return selection
            resume_browse = False
            try:
                return self.browse(self.plan)
            except (Back, MainMenu):
                continue

    # 1. Disclaimer ----------------------------------------------------------------------

    def disclaimer(self) -> None:
        version, text = disclaimer_text()
        if self.settings.disclaimer_version >= version:
            return
        self.say(text)
        if not self.prompter.confirm("Continue?", default=True):
            raise Cancelled()
        self.settings.disclaimer_version = version
        save_settings(self.app.paths, self.settings)

    # 2. System check --------------------------------------------------------------------

    def system_check(self) -> Plan:
        self.prompter.new_screen("Checking this machine...")
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
            # Used once: Back from a later step shows this menu instead.
            path, self.model_path = self.model_path, None
            return self._use_entered(path, plan)
        while True:
            models = self._found(plan)
            unfinished = unfinished_downloads(self.app.paths)
            keys = {"p": "custom model path", "d": "browse and download"}
            if unfinished:
                keys = {"r": "resume a download", **keys}
            if list_profiles(self.app.paths):
                keys = {"l": "launch a model", **keys}
            if has_installs(self.app):
                keys["u"] = "uninstall or remove setups"
            notice = _unfinished_notice(unfinished)
            if models:
                picked = self.prompter.choose(
                    "Ornith models found on this machine (used where they are, never moved):",
                    ["Model", "Quant", "Size", "Fits", "App", "Path"],
                    [self._local_choice(m, plan) for m in models],
                    keys=keys,
                    notice=notice,
                )
            else:
                picked = self.prompter.choose("What next?", [], [], keys=keys, notice=notice)
            if picked == "r":
                selection = self._choose_resume(unfinished, plan)
                if selection is not None:
                    return selection
                continue
            if picked == "l":
                name = self._choose_launch()
                if name is not None:
                    raise LaunchRequested(name)
                continue
            if picked == "u":
                if uninstall_menu(self.app, self.prompter) == EVERYTHING:
                    raise Uninstalled()
                # Downloads may be gone and the default setup may have changed.
                self.found_models = None
                self.settings = load_settings(self.app.paths)
                continue
            if picked == "d":
                return None
            if picked == "p":
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

    def _choose_resume(self, unfinished: list[Unfinished], plan: Plan) -> Selection | None:
        """The stopped downloads; picking one goes on with the same model and folder."""
        matches = [self._resumable(u, plan) for u in unfinished]
        choices = []
        for u, entry in zip(unfinished, matches):
            cls = entry.classification if entry else None
            name = cls.family.display_name if cls and cls.family else u.repo_id
            progress = f"{gib(u.done)} of {gib(u.total)}"
            detail = f"{u.repo_id}, in {u.folder}"
            if entry is None:
                detail = (
                    f"{u.repo_id} is no longer in the model list (it may have changed on "
                    "Hugging Face). Remove it with u, Uninstall."
                )
            choices.append(
                Choice(
                    (name, (cls.quant or "") if cls else "", progress, u.model_name),
                    detail=detail,
                    enabled=entry is not None,
                )
            )
        while True:
            picked = self.prompter.choose(
                "Unfinished downloads (pick one to resume where it stopped):",
                ["Model", "Quant", "Downloaded", "File"],
                choices,
                back=True,
            )
            if picked == BACK:
                return None
            u, entry = unfinished[int(picked)], matches[int(picked)]
            earlier = load_choices(self.app.paths, u.folder)  # what was chosen when it started
            saved = self._saved_candidate(entry, plan, earlier) if earlier else None
            best = saved or self._best_candidate(entry, plan)
            if best is None:
                self.say("This model cannot be planned on this machine.")
                continue
            selection = Selection(entry=entry, candidate=best, models_dir=u.models_dir, resumed=u)
            if saved is not None and earlier is not None:
                # The settings and confirmations given when the download started.
                selection.preset = True
                selection.port = self._port_like(earlier.get("port"))
                selection.consents = dict(earlier.get("consents") or {})
                if self._consents_given(selection):
                    return selection
            if self._consents(selection):
                return selection

    def _saved_candidate(
        self, entry: Entry, plan: Plan, choices: dict[str, Any]
    ) -> Candidate | None:
        """The settings saved for a stopped download, if they still fit this machine."""
        names = [PurePosixPath(part.path).name for part in entry.file.parts]
        if choices.get("file") not in names:
            return None
        try:
            candidates = build_candidates(
                [entry],
                plan.report.pools,
                load_calibration(),
                load_tested(),
                slots=int(choices["slots"]),
                contexts=[int(choices["context"])],
                kv_types=[str(choices["kv_type"])],
            )
        except (KeyError, TypeError, ValueError):
            return None
        for c in rank(candidates, plan.families):
            same = c.vision == bool(choices.get("vision"))
            if same and c.placement.value == choices.get("placement") and c.fits:
                return c
        return None

    def _consents_given(self, selection: Selection) -> bool:
        """Whether the saved confirmations still cover this model (its label may have changed)."""
        needed = []
        if selection.entry.classification.uncensored:
            needed.append("uncensored_ack")
        if selection.entry.repo.licence in (None, "other"):
            needed.append("licence_ack")
        return all(name in selection.consents for name in needed)

    def _port_like(self, saved: Any) -> int:
        """The port chosen before, or the next free one if something else took it."""
        if self.port:
            return self.port
        try:
            return first_free_port(int(saved))
        except (TypeError, ValueError):
            return first_free_port()

    @staticmethod
    def _resumable(unfinished: Unfinished, plan: Plan) -> Entry | None:
        """The catalog file this download belongs to: same repo, revision and file name."""
        for candidate in plan.ranked:
            entry = candidate.entry
            names = {PurePosixPath(part.path).name for part in entry.file.parts}
            if (
                entry.repo.id == unfinished.repo_id
                and entry.repo.sha.startswith(unfinished.sha7)
                and unfinished.model_name in names
            ):
                return entry
        return None

    def _choose_launch(self) -> str | None:
        """The setups ready to launch; one that cannot start is greyed out with its fix."""
        from ornith_launcher.launch import _model_label, make_runtime, preflight

        runtime = make_runtime(self.app)
        names = list_profiles(self.app.paths)
        default = load_settings(self.app.paths).default_profile
        choices = []
        for name in names:
            profile = load_profile(self.app.paths, name)
            # A missing image is downloaded at launch, so it does not grey a setup out.
            problems = preflight(self.app, profile, runtime, check_image=False)
            tools = [
                label
                for label, key in (("Claude Code", "claude_code"), ("OpenCode", "opencode"))
                if profile.integrations.get(key)
            ]
            detail = f"{profile.url}{' (default)' if name == default else ''}"
            choices.append(
                Choice(
                    (
                        name,
                        _model_label(profile),
                        context_label(profile.runtime.context),
                        str(profile.runtime.port),
                        ", ".join(tools) or "-",
                    ),
                    detail=problems[0] if problems else detail,
                    enabled=not problems,
                )
            )
        picked = self.prompter.choose(
            "Launch a model:",
            ["Setup", "Model", "Context", "Port", "Coding tools"],
            choices,
            default=names.index(default) if default in names else 0,
            back=True,
        )
        return None if picked == BACK else names[int(picked)]

    def _found(self, plan: Plan) -> list[LocalModel]:
        if self.found_models is None:
            # Once per setup: coming back to this menu shows the same list without noise.
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
            self.found_models = sort_found(self._identify_all(result.files, plan))
            if not self.found_models:
                self.say("No Ornith models found in the usual places.")
        return self.found_models

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
            link=self._card(model.likely.repo.id) if model.likely is not None else "",
        )

    def _ask_location(self) -> Path | None:
        raw = ""
        while not raw:
            raw = self.prompter.text(f"Path to a model file or folder ({BACK} to go back)")
        if raw.lower() == BACK:
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
        """Suggested, all or only uncensored models; raises Back or MainMenu to leave."""
        state = self.browse_state
        while True:
            view = state.views[-1]
            notice = ""
            previous_page = next_page = False
            if view == SUGGESTED:
                rows = [s.candidate for s in plan.suggestions]
                choices = [
                    Choice(self._cells(c), detail=s.reason, link=self._card(c.entry.repo.id))
                    for c, s in zip(rows, plan.suggestions)
                ]
                shown = " (uncensored shown)" if self.settings.show_uncensored else ""
                title = f"Suggested for this machine{shown}:"
                keys = {"a": "all models", "o": "only uncensored", "u": self._uncensored_label()}
            else:
                rows = self._full_rows(plan, state.search, only_uncensored=view == UNCENSORED_ONLY)
                rows = sorted_rows(rows, state.sort)
                start = state.page * PAGE
                rows_page = rows[start : start + PAGE]
                choices = [
                    Choice(
                        self._cells(c),
                        detail=self._detail(c),
                        enabled=c.fits,
                        link=self._card(c.entry.repo.id),
                    )
                    for c in rows_page
                ]
                files = "file" if len(rows) == 1 else "files"
                shown = ", uncensored shown" if self.settings.show_uncensored else ""
                counts = f"({len(rows)} {files}, {SORTS[state.sort][0]}; page {state.page + 1})"
                if view == UNCENSORED_ONLY:
                    title = f"Uncensored models only {counts}:"
                    notice = f"{UNCENSORED_WARNING} {UNCENSORED_LIST_NOTE}"
                    keys = {"/": "search", "s": "sort", "a": "all models"}
                else:
                    counts = counts.replace(";", f"{shown};", 1)
                    title = f"All models {counts}:"
                    keys = {
                        "/": "search",
                        "s": "sort",
                        "o": "only uncensored",
                        "u": self._uncensored_label(),
                    }
                previous_page = state.page > 0
                next_page = start + PAGE < len(rows)
                rows = rows_page
            if not choices and view == SUGGESTED:
                self.say("Nothing fits this machine with 15% memory to spare; showing all models.")
                state.views = [ALL_MODELS]
                continue
            picked = self.prompter.choose(
                title,
                BROWSE_HEADERS,
                choices,
                keys=keys,
                notice=notice,
                previous_page=previous_page,
                next_page=next_page,
                back=True,
                main_menu=len(state.views) > 1,
            )
            if picked == BACK:
                if len(state.views) == 1:
                    raise Back()
                state.views.pop()
                state.page = 0
            elif picked == MAIN_MENU:
                raise MainMenu()
            elif picked == "a":
                state.open(ALL_MODELS)
            elif picked == "o":
                if not self.settings.show_uncensored:
                    plan = self.plan = self._toggle_uncensored(plan)
                if self.settings.show_uncensored:
                    state.open(UNCENSORED_ONLY)
            elif picked == NEXT_PAGE:
                state.page += 1
            elif picked == PREVIOUS_PAGE:
                state.page = max(0, state.page - 1)
            elif picked == "/":
                state.search = self.prompter.text("Search (words in the repo or file name)")
                state.page = 0
            elif picked == "s":
                state.sort = self._choose_sort(state.sort)
                state.page = 0
            elif picked == "u":
                plan = self.plan = self._toggle_uncensored(plan)
            else:
                candidate = rows[int(picked)]
                selection = Selection(entry=candidate.entry, candidate=candidate)
                if self._consents(selection):
                    return selection

    def _choose_sort(self, current: int) -> int:
        picked = self.prompter.choose(
            "Sort the list by:",
            ["Order"],
            [Choice((label[:1].upper() + label[1:],)) for label, _, _ in SORTS],
            default=current,
            back=True,
        )
        return current if picked == BACK else int(picked)

    def _full_rows(self, plan: Plan, search: str, only_uncensored: bool = False) -> list[Candidate]:
        """One row per model file: its best setting, fitting ones first."""
        filters = Filters(
            show_uncensored=self.settings.show_uncensored,
            only_uncensored=only_uncensored,
            search=search,
        )
        seen: set[tuple[str, str]] = set()
        rows = []
        for candidate in plan.ranked:
            key = (candidate.entry.repo.id, candidate.entry.file.path)
            if key in seen or not filters.accepts(candidate):
                continue
            seen.add(key)
            rows.append(candidate)
        return rows

    def _card(self, repo_id: str) -> str:
        """The repo's model card, on the mirror if ``HF_ENDPOINT`` names one."""
        return HubClient.from_env(self.app.env).model_card_url(repo_id)

    def _cells(self, c: Candidate) -> tuple[str, ...]:
        cls = c.entry.classification
        name = cls.family.display_name if cls.family else f"Ornith ? {cls.size_label or '?'}"
        repo = c.entry.repo
        # The licence comes early so a narrow terminal cuts the counts, never the licence.
        return (
            name,
            cls.source.value,
            repo.licence or "none",
            cls.quant or "?",
            context_label(c.context),
            gib(c.estimate.total),
            gib(download_size(c.entry, c.vision)),
            "yes" if c.fits else "no",
            compact_count(repo.likes),
            compact_count(repo.downloads),
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
            self.prompter.new_screen("Uncensored models")
            self.prompter.paragraph(
                f"{UNCENSORED_WARNING} Once shown, they stay in every list and search, in later "
                "runs too, until you hide them again with u."
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
        licence = selection.entry.repo.licence
        notes = []
        if cls.source is Source.COMMUNITY:
            notes.append("Third-party build, not from Ornith AI.")
        if Badge.UNTESTED_LOCALLY in cls.badges:
            notes.append("This size has not been run by the project yet (untested locally).")
        if cls.uncensored or licence in (None, "other") or notes:
            name = cls.family.display_name if cls.family else "this model"
            self.prompter.new_screen(f"About {name} {cls.quant or ''}".rstrip())
        for note in notes:
            self.say(f"Note: {note}")
        if cls.uncensored:
            if notes:
                self.say()
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
        if licence in (None, "other"):
            what = "declares no licence" if licence is None else "uses a non-standard licence"
            if notes or cls.uncensored:
                self.say()
            self.prompter.paragraph(
                f"This model {what}. Check its terms on its Hugging Face page before use."
            )
            if not self.prompter.confirm("I have checked the licence. Continue?", default=False):
                return False
            selection.consents["licence_ack"] = stamp
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
            back=True,
            main_menu=selection.local is None,  # from a local model, Back is the first menu
        )
        if picked == BACK:
            raise Back()
        if picked == MAIN_MENU:
            raise MainMenu()
        chosen = contexts[int(picked)]
        port = self.port or first_free_port()
        self.prompter.new_screen("Settings")
        native = " (native)" if chosen.context == chosen.native else ""
        rows = [
            ("Context", context_label(chosen.context) + native),
            ("KV cache", chosen.kv_type),
            ("Parallel requests", str(chosen.slots)),
            ("Vision", _vision_label(selection, chosen.vision)),
            ("Port", str(port)),
        ]
        for line in key_values(rows):
            self.say(line)
        if not self.prompter.confirm("Use these settings?", default=True):
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
        mmproj = selection.entry.repo.best_mmproj()
        if mmproj is not None:
            extra = (
                "" if selection.local is not None else f" It adds a {gib(mmproj.size)} download."
            )
            vision = self.prompter.confirm(
                f"Turn vision (image input) on?{extra}", default=chosen.vision
            )
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
        note = ""  # shown on the next screen, which clears this one
        while True:
            check = check_models_folder(
                folder,
                size,
                home=home,
                os_name=self.app.os_name,
                mounts=mounts,
                disk_free=lambda path: self.app.host.disk_free(str(path)),
            )
            self.prompter.new_screen("Models folder")
            if note:
                self.say(note)
                self.say()
                note = ""
            rows = [("Folder", str(folder)), ("Download", gib(size))]
            if check.free is not None:
                rows.append(("Free space", gib(check.free)))
            for line in key_values(rows):
                self.say(line)
            if check.hidden_notice:
                self.say()
                self.prompter.paragraph(check.hidden_notice)
            if check.errors or check.warnings:
                self.say()
            for error in check.errors:
                self.prompter.paragraph(f"Problem: {error}")
            for warning in check.warnings:
                self.prompter.paragraph(f"Warning: {warning}")
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
                note = "That folder cannot be used; please enter another one."
        ensure_folder(folder)
        if folder != self.settings.models_path(self.app.paths):
            self.settings.models_dir = str(folder)
            save_settings(self.app.paths, self.settings)
        selection.models_dir = folder

    def _download_size(self, selection: Selection) -> int:
        return download_size(selection.entry, selection.candidate.vision)

    # 7. Summary -----------------------------------------------------------------------------

    def summary(self, selection: Selection) -> None:
        c = selection.candidate
        cls = selection.entry.classification
        self.prompter.new_screen("Resuming the download" if selection.preset else "Summary")
        if selection.preset:
            self.say("With the settings chosen when it started:")
        name = cls.family.display_name if cls.family else "Ornith model"
        rows = [
            ("Model", f"{name} {cls.quant or ''} ({selection.entry.repo.id})"),
            (
                "Settings",
                f"context {context_label(c.context)}, KV {c.kv_type}, {c.slots} slot"
                f"{'' if c.slots == 1 else 's'}, "
                f"vision {'on' if c.vision else 'off'}, port {selection.port}",
            ),
            ("Memory", f"about {gib(c.estimate.total)} ({c.level.value})"),
        ]
        if selection.local is not None:
            where = "used where it is; ornith-launcher will not move or delete it"
            if selection.local.file.owner_app:
                where += f" (it belongs to {_app_name(selection.local.file.owner_app)})"
            rows.append(("File", f"{selection.local.file.real_path}, {where}"))
        elif selection.resumed is not None:
            u, size = selection.resumed, self._download_size(selection)
            rows.append(
                ("Download", f"resumes in {u.folder} ({gib(u.done)} of {gib(size)} already there)")
            )
        else:
            size = self._download_size(selection)
            minutes = size / ASSUMED_SPEED / 60
            mmproj = selection.entry.repo.best_mmproj()
            split = ""
            if c.vision and mmproj is not None:
                split = f"model {gib(size - mmproj.size)} + vision file {gib(mmproj.size)}, "
            rows.append(
                (
                    "Download",
                    f"{gib(size)} into {selection.models_dir} "
                    f"({split}about {minutes:.0f} min at 50 MB/s; ESTIMATED)",
                )
            )
        if cls.source is Source.COMMUNITY:
            rows.append(("Note", THIRD_PARTY_NOTE))
        flavour = self.plan.report.flavour if self.plan else None
        if flavour is not None:
            rows.append(
                (
                    "Runtime",
                    f"llama.cpp {flavour.image.tag} in Docker (downloaded once if missing, "
                    "a few GB)",
                )
            )
        for line in key_values(rows):
            self.say(line)
        if selection.preset:
            return  # choosing Resume was the go-ahead
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
        progress = ProgressLine(self.prompter.out, indent=PROGRESS_INDENT)
        downloader = (
            self.downloader_factory(progress.update)
            if self.downloader_factory
            else Downloader(
                source=HttpRangeSource(token=self.app.env.get("HF_TOKEN") or None),
                on_progress=progress.update,
            )
        )
        stop = threading.Event()
        c = selection.candidate
        save_choices(
            self.app.paths,
            folder,
            {
                "file": targets[0].dest.name,
                "context": c.context,
                "kv_type": c.kv_type,
                "slots": c.slots,
                "vision": c.vision,
                "placement": c.placement.value,
                "port": selection.port,
                "consents": selection.consents,
            },
        )
        roles = _file_roles(len(selection.entry.file.parts), len(targets))
        files = "1 file" if len(targets) == 1 else f"{len(targets)} files"
        self.say()
        self.say(f"Downloading {files}, {gib(sum(t.size for t in targets))} in all, into {folder}")
        self.say("Ctrl+C pauses the download; then keep it to resume later, or delete it.")
        for index, (target, role) in enumerate(zip(targets, roles), 1):
            self.say()
            self.say(f"{index}/{len(targets)}  {role}: {target.dest.name}, {gib(target.size)}")
            if target.dest.exists() and target.dest.stat().st_size == target.size:
                if target.sha256 is None or self._cached_sha(target.dest) == target.sha256:
                    self.say(f"{PROGRESS_INDENT}Already here and checked.")
                    continue
            already, _ = _saved_progress(target.dest)
            if already:
                self.say(f"{PROGRESS_INDENT}Resuming: {gib(already)} is already here.")
            record(self.app.paths, "downloads", str(target.dest))
            started = time.monotonic()
            try:
                downloader.fetch(target, stop)
            except (DownloadPaused, KeyboardInterrupt):
                progress.finish()
                self._after_pause(target, targets, folder)
            progress.finish()
            self.say(f"{PROGRESS_INDENT}{_done_line(target, already, time.monotonic() - started)}")
        selection.model_paths = [t.dest for t in targets[: len(selection.entry.file.parts)]]
        if selection.candidate.vision and mmproj is not None:
            selection.mmproj_path = targets[-1].dest

    def _after_pause(self, target: Target, targets: list[Target], folder: Path) -> NoReturn:
        """Keep the stopped download for later (the default), or delete it and stop setup."""
        done, size = _saved_progress(target.dest)
        where = f" at {done * 100 // size}% ({gib(done)} of {gib(size)})" if size else ""
        # The .part file is full size from the start, so count what was really downloaded.
        kept = done + sum(t.size for t in targets if t.dest.exists())
        self.say()
        self.say(f"Download paused{where}.")
        try:
            picked = self.prompter.choose(
                "What now?",
                ["Option", ""],
                [
                    Choice(("Resume later", "keep it; run setup again and press r to go on")),
                    Choice(("Delete it and exit", f"remove the {gib(kept)} downloaded so far")),
                ],
            )
        except (KeyboardInterrupt, EOFError, Cancelled):
            picked = 0  # a second Ctrl+C keeps it: nothing is lost by accident
        if picked == 1:
            in_use = files_in_use(self.app.paths)
            files = [
                path
                for t in targets
                if str(t.dest) not in in_use
                for path in (t.dest, *_part_files(t.dest))
                if path.exists()
            ]
            delete_downloads(self.app, files)
            forget_choices(self.app.paths, folder)
            self.say(f"Deleted the download ({gib(kept)}).")
            raise Cancelled()
        raise DownloadPaused(
            f"the download of {target.dest.name} is paused{where}. Nothing is lost",
            hint=(
                "Resume it: run ornith-launcher setup and press r. Or remove it: run "
                "ornith-launcher setup, press u and choose Remove unfinished downloads."
            ),
        )

    def _cached_sha(self, path: Path) -> str:
        return cached_sha256(path, self.app.paths.cache / HASH_CACHE)


def _vision_label(selection: Selection, on: bool) -> str:
    mmproj = selection.entry.repo.best_mmproj()
    if mmproj is None:
        return "not available"
    if selection.local is not None:
        return "on (image input)" if on else "off"
    if on:
        return f"on: adds a {gib(mmproj.size)} vision file so the model can read images"
    return f"off (on would add a {gib(mmproj.size)} vision file for image input)"


def _file_roles(model_parts: int, files: int) -> list[str]:
    """What each downloaded file is, in download order: the model's parts, then vision."""
    roles = [
        "Model" if model_parts == 1 else f"Model, part {i} of {model_parts}"
        for i in range(1, model_parts + 1)
    ]
    return roles + [VISION_ROLE] * (files - model_parts)


def _part_files(dest: Path) -> tuple[Path, Path]:
    return dest.with_name(dest.name + PART), dest.with_name(dest.name + STATE)


def _saved_progress(dest: Path) -> tuple[int, int]:
    """(bytes on disk, file size) of a stopped download; (0, 0) when none was started."""
    return state_progress(dest.with_name(dest.name + STATE))


def _done_line(target: Target, already: int, seconds: float) -> str:
    checked = ", sha256 checked" if target.sha256 else ""
    fetched = target.size - already
    if seconds < 1 or fetched <= 0:
        return f"Done{checked}."
    minutes, rest = divmod(int(seconds), 60)
    return f"Done in {minutes}:{rest:02d} ({fetched / seconds / 1e6:.1f} MB/s){checked}."


class ProgressLine:
    """A progress line redrawn in place on a terminal, or printed every 10% elsewhere."""

    def __init__(self, out=None, min_interval: float = 0.5, indent: str = "") -> None:
        self.out = out or sys.stdout
        self.tty = getattr(self.out, "isatty", lambda: False)()
        self.min_interval = min_interval
        self.indent = indent
        self._last = 0.0
        self._last_step = -1
        self._shown = False

    def update(self, status: Status) -> None:
        if self.tty:
            now = time.monotonic()
            if now - self._last < self.min_interval and status.done < status.total:
                return
            self._last = now
            self.out.write("\r" + self.indent + format_status(status) + "\x1b[K")
            self.out.flush()
        else:
            step = int(status.done * 10 / status.total) if status.total else 10
            if step == self._last_step:
                return
            self._last_step = step
            print(self.indent + format_status(status), file=self.out, flush=True)
        self._shown = True

    def finish(self) -> None:
        if self.tty and self._shown:
            self.out.write("\n")
            self.out.flush()
        self._shown = False
        self._last_step = -1


def _context_note(c: Candidate) -> str:
    # Every row is the same model here, so its third-party note is shown once, elsewhere.
    notes = [note for note in c.notes if note != THIRD_PARTY_NOTE]
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
    selection = None
    try:
        selection = wizard.run()
        prompter.new_screen("Finishing setup")
        finish(app, selection)
        profile = setup_profile.finish_setup(
            app, prompter, selection, wizard.plan, wizard.settings, args.name
        )
        if selection.needs_download and selection.model_paths:
            forget_choices(app.paths, selection.model_paths[0].parent)  # nothing left to resume
    except Uninstalled:
        return 0
    except LaunchRequested as request:
        from ornith_launcher.launch import run_launch

        return run_launch(
            app, argparse.Namespace(name=request.name, here=False, no_monitor=False, logs=None)
        )
    except Cancelled:
        app.say()
        if selection is not None and selection.needs_download:
            app.say(
                f"Setup stopped. The downloaded model stays in {selection.model_paths[0].parent} "
                "and is found when you run ornith-launcher setup again."
            )
        else:
            app.say(
                "Setup stopped. Nothing else was changed; run ornith-launcher setup to start again."
            )
        if unfinished_downloads(app.paths):
            app.say(
                "A stopped download is kept: resume it with r on setup's first menu, or remove it "
                "with u, Uninstall."
            )
        return 1
    try:
        return setup_profile.launch_now(app, prompter, profile)
    except Cancelled:
        # The profile is already saved, so setup itself finished.
        app.say()
        app.say(f"Not started. Start it any time with: ornith-launcher launch {profile.name}")
        return 0


def finish(app: App, selection: Selection) -> None:
    app.say("The model is ready:")
    for path in selection.model_paths:
        app.say(f"  {path}")
    if selection.mmproj_path:
        app.say(f"  {selection.mmproj_path} (vision)")
