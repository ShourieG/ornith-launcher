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
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

from ornith_launcher.app import App
from ornith_launcher.browse import (
    ALL_MODELS,
    BEST_FIT,
    DOWNLOADS_OPTIONS,
    FILTER_FIELDS,
    FIT_ORDER,
    HIDDEN,
    LICENCE_OPTIONS,
    LIFETIME_OPTIONS,
    LIKES_OPTIONS,
    ONLY,
    QUANT_LABELS,
    SHOWN,
    SORTS,
    SOURCE_OPTIONS,
    UNCENSORED_OPTIONS,
    UPDATED_OPTIONS,
    VISION_OPTIONS,
    ListFilters,
    Lists,
    Sort,
    build_lists,
    count_matching,
    download_size,
    file_key,
    lightest,
    sort_rows,
    updated_on,
)
from ornith_launcher.catalog.catalog import Entry
from ornith_launcher.catalog.classify import Badge, Source, load_rules
from ornith_launcher.catalog.families import version_key
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
from ornith_launcher.catalog.quant import QUANT_GROUPS
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
    FIT_LABELS,
    KV_TYPES,
    SHORT_CONTEXT,
    THIRD_PARTY_NOTE,
    Candidate,
    Fit,
    Level,
    build_candidates,
    load_tested,
    rank,
)
from ornith_launcher.planner.score import Scorer
from ornith_launcher.profiles import list_profiles, load_profile
from ornith_launcher.resources import load_text
from ornith_launcher.scan import Plan, make_plan, print_system
from ornith_launcher.settings import Settings, load_settings, save_settings
from ornith_launcher.ui.plain import GIB, compact_count, context_label, gib, key_values
from ornith_launcher.ui.prompts import (
    BACK,
    MAIN_MENU,
    NEXT_PAGE,
    PREVIOUS_PAGE,
    Cancelled,
    Choice,
    PlainPrompter,
    is_back,
    shown_key,
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
# The browse screen: Suggested, then the best fit list and All models, which Tab switches.
SUGGESTED = "suggested"
FIT_HEADERS = [
    "Model", "Source", "Licence", "Quant", "Context", "Memory", "Download", "Fits", "Likes",
    "DLs/30d", "DLs total", "Updated",
]  # fmt: skip
ALL_HEADERS = [
    "Model", "Source", "Licence", "Quant", "Needs at least", "Download", "Fits", "Likes",
    "DLs/30d", "DLs total", "Updated",
]  # fmt: skip
LIGHTEST_NOTE = (
    "Needs at least: the shortest context (32k), vision off. You choose the context on the "
    "next screen."
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
    """Where browsing was, so Back from a later step returns to the same list and page.

    Each list keeps its own sort; the sorts and filters last until setup ends.
    """

    views: list[str] = field(default_factory=lambda: [SUGGESTED])
    page: int = 0
    search: str = ""
    sorts: dict[str, int] = field(default_factory=lambda: {BEST_FIT: 0, ALL_MODELS: 0})
    filters: ListFilters = field(default_factory=ListFilters)

    def open(self, view: str) -> None:
        if view in self.views:
            del self.views[self.views.index(view) + 1 :]
        else:
            self.views.append(view)
        self.page = 0

    def switch(self) -> None:
        """Tab: the other list in place of this one, so Back still leads to Suggested."""
        self.views[-1] = ALL_MODELS if self.views[-1] == BEST_FIT else BEST_FIT
        self.page = 0

    def fresh(self) -> BrowseState:
        """A new start from the first menu that keeps the sorts and filters."""
        return BrowseState(sorts=dict(self.sorts), filters=self.filters)


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
    accepted_fit: Fit = Fit.FITS  # the worst memory verdict the user already said yes to

    @property
    def needs_download(self) -> bool:
        return self.local is None


def disclaimer_text() -> tuple[int, str]:
    raw = load_text(DISCLAIMER_FILE)
    first, _, body = raw.partition("\n")
    return int(first.split(":", 1)[1]), body.strip()


def port_is_free(port: int) -> bool:
    """True when nothing listens on ``port`` at 127.0.0.1, the only address servers use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def first_free_port(start: int = FIRST_PORT, attempts: int = 100) -> int:
    for port in range(start, start + attempts):
        if port_is_free(port):
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
        selection = self._settled(self.choose_model())
        # A resumed download stays where its files are, so only a new one asks for a folder.
        asks_folder = selection.needs_download and selection.models_dir is None
        on_folder = asks_folder
        while True:
            try:
                if on_folder:
                    self.models_folder(selection)
                    on_folder = False
                self.summary(selection)
                break
            except Back:
                if not on_folder and asks_folder:
                    on_folder = True  # Esc on the summary: the folder screen again
                    continue
                # Esc on the folder screen, or on a summary with no folder: the settings.
                if asks_folder:
                    selection.models_dir = None
                if selection.preset:
                    selection = self._settled(self.choose_model())
                else:
                    selection = self._settled(selection, settings_first=True)
                asks_folder = on_folder = selection.needs_download and selection.models_dir is None
        if selection.needs_download:
            self.download(selection)
        return selection

    def _settled(self, selection: Selection, settings_first: bool = False) -> Selection:
        """The context screen and settings for ``selection``, with Back and Main menu."""
        while not selection.preset:
            try:
                return self.options(selection, self.plan, settings_first=settings_first)
            except Back:
                browsed = selection.local is None and selection.resumed is None
                selection = self.choose_model(resume_browse=browsed)
            except MainMenu:
                selection = self.choose_model()
            settings_first = False
        return selection

    def choose_model(self, resume_browse: bool = False) -> Selection:
        """The first menu, then browsing; Back and Main menu lead back here."""
        while True:
            if not resume_browse:
                self.browse_state = self.browse_state.fresh()
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
            if same and c.placement.value == choices.get("placement") and c.fit is not Fit.TOO_BIG:
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
        fits = FIT_LABELS[best.fit] if best else "-"
        info = [("File", str(model.file.path)), ("Source", source[:1].upper() + source[1:])]
        if model.file.incomplete:
            info.append(("Status", model.file.incomplete[:1].upper() + model.file.incomplete[1:]))
        else:
            info.append(("Badges", " ".join(b.value for b in cls.badges) or "none"))
        return Choice(
            cells=(
                name,
                cls.quant or "?",
                gib(model.file.size),
                fits,
                _app_name(model.file.owner_app),
                str(model.file.path),
            ),
            info=tuple(info),
            enabled=model.supported,
            link=self._card(model.likely.repo.id) if model.likely is not None else "",
        )

    def _ask_location(self) -> Path | None:
        raw = ""
        while not raw:
            raw = self.prompter.text(
                f"Path to a model file or folder ({shown_key(BACK)} to go back)", back=True
            )
        if is_back(raw):
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
        """Suggested, the best fit list or All models; raises Back or MainMenu to leave."""
        state = self.browse_state
        while True:
            self._sync_uncensored(state)
            view = state.views[-1]
            lists = None
            previous_page = next_page = False
            if view == SUGGESTED:
                if not plan.suggestions:
                    self.say(
                        "Nothing fits this machine with 15% memory to spare; showing all models."
                    )
                    state.views = [ALL_MODELS]
                    continue
                rows = [s.candidate for s in plan.suggestions]
                choices = [
                    Choice(
                        self._cells(c),
                        info=(("Why", s.reason), *self._info(c)),
                        link=self._card(c.entry.repo.id),
                    )
                    for c, s in zip(rows, plan.suggestions)
                ]
                shown = " (uncensored shown)" if self.settings.show_uncensored else ""
                title, headers = f"Suggested for this machine{shown}:", FIT_HEADERS
                notice = ""
                keys = {"b": "best fit list", "a": "all models", "/": "search"}
            else:
                sort = SORTS[view][state.sorts[view]]
                scorer = Scorer.for_candidates(plan.ranked) if sort.by == "score" else None
                today = self.now().date()
                lists = build_lists(plan.ranked, state.filters, state.search, today, scorer)
                every = lists.best_fit if view == BEST_FIT else lists.all_models
                every = sort_rows(every, sort, scorer)
                start = state.page * PAGE
                rows = every[start : start + PAGE]
                lightest_rows = view == ALL_MODELS
                choices = [
                    Choice(
                        self._cells(c, lightest=lightest_rows),
                        info=self._info(c, scorer),
                        link=self._card(c.entry.repo.id),
                    )
                    for c in rows
                ]
                title = self._list_title(view, state, lists, len(every), sort)
                headers = ALL_HEADERS if lightest_rows else FIT_HEADERS
                notice = self._list_notice(view, state, lists, bool(every))
                other = {"a": "all models"} if view == BEST_FIT else {"b": "best fit list"}
                keys = {
                    "s": "sort",
                    "f": "filters",
                    "/": "search",
                    "c": "clear search and filters",
                    **other,
                }
                previous_page = state.page > 0
                next_page = start + PAGE < len(every)
            picked = self.prompter.choose(
                title,
                headers,
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
            elif picked in ("a", "b"):
                wanted = ALL_MODELS if picked == "a" else BEST_FIT
                if view == SUGGESTED:
                    state.open(wanted)
                else:
                    state.switch()  # the other list in place of this one
            elif picked == NEXT_PAGE:
                state.page += 1
            elif picked == PREVIOUS_PAGE:
                state.page = max(0, state.page - 1)
            elif picked == "/":
                typed = self.prompter.text(
                    "Search (words in the repo or file name; Enter alone clears it, "
                    f"{shown_key(BACK)} keeps it)",
                    back=True,
                )
                if is_back(typed):
                    continue  # the search stays as it was
                state.search = typed
                state.page = 0
                if view == SUGGESTED:
                    state.open(ALL_MODELS)  # a search looks things up, so it uses the catalog
            elif picked == "s":
                state.sorts[view] = self._choose_sort(view, state.sorts[view])
                state.page = 0
            elif picked == "f":
                plan = self.plan = self._filters(plan, state)
                state.page = 0
            elif picked == "c":
                state.search = ""
                state.filters = state.filters.cleared()
                state.page = 0
            else:
                candidate = rows[int(picked)]
                if lists is not None and view == ALL_MODELS:
                    # The row shows the least memory; the next screens start from the
                    # setting the file is recommended at.
                    candidate = lists.recommended[file_key(candidate)]
                selection = Selection(entry=candidate.entry, candidate=candidate)
                if self._accept_fit(selection, candidate) and self._consents(selection):
                    return selection

    def _sync_uncensored(self, state: BrowseState) -> None:
        """The uncensored filter follows the saved setting; "only" lasts until setup ends."""
        filters = state.filters
        if self.settings.show_uncensored:
            wanted = ONLY if filters.uncensored == ONLY else SHOWN
        else:
            wanted = HIDDEN
        if filters.uncensored != wanted:
            state.filters = replace(filters, uncensored=wanted)

    def _list_title(
        self, view: str, state: BrowseState, lists: Lists, count: int, sort: Sort
    ) -> str:
        page = f"{sort.label}; page {state.page + 1}"
        if view == BEST_FIT:
            return f"Best fit for this machine ({_files(count)}; {page}):"
        narrowed = state.search or not state.filters.is_default
        counts = f"{count} of {_files(lists.total)}" if narrowed else _files(count)
        return f"All models ({counts}; {page}):"

    def _list_notice(self, view: str, state: BrowseState, lists: Lists, any_rows: bool) -> str:
        """The lines under a list's title: filters and search, then what the list leaves out."""
        lines = [f"Filters: {state.filters.summary()}. Search: {state.search or 'none'}."]
        if state.filters.uncensored == ONLY:
            lines.append(f"{UNCENSORED_WARNING} {UNCENSORED_LIST_NOTE}")
        too_big = lists.too_big
        if view == BEST_FIT and not any_rows and too_big:
            lines.append(
                f"Nothing that fits this machine matches. {_files(too_big)} "
                f"{'matches' if too_big == 1 else 'match'} in All models (too big): press a."
            )
        elif not any_rows:
            lines.append("Nothing matches the search and filters. Press c to clear them.")
        elif view == BEST_FIT and too_big:
            these = "file is" if too_big == 1 else "files are"
            them = "it" if too_big == 1 else "them"
            lines.append(
                f"{too_big} more {these} too big for this machine: a shows {them} in All models."
            )
        if view == ALL_MODELS and any_rows:
            lines.append(LIGHTEST_NOTE)
        return "\n".join(lines)

    def _choose_sort(self, view: str, current: int) -> int:
        sorts = SORTS[view]
        if view == BEST_FIT:
            title = "Sort the best fit list by:"
            notice = (
                "The first two give advice: they rank each file by how well it suits this "
                "machine. The others order the list by one value."
            )
        else:
            title = "Sort all models by:"
            notice = "Fit never moves a row here; the Fits column says how each file fits."
        picked = self.prompter.choose(
            title,
            ["Order"],
            [Choice((s.label[:1].upper() + s.label[1:],)) for s in sorts],
            default=current,
            notice=notice,
            back=True,
        )
        return current if picked == BACK else int(picked)

    # 3a. Filters ----------------------------------------------------------------------------

    def _filters(self, plan: Plan, state: BrowseState) -> Plan:
        """The filter screen; returns the plan, rebuilt when the uncensored setting changed."""
        at = 0
        while True:
            today = self.now().date()
            rows = list(lightest(plan.ranked, state.search).values())
            matching, fitting = count_matching(rows, state.filters, today)
            picked = self.prompter.choose(
                f"Filters ({_matching(matching)}; {fitting} of them fit this machine):",
                ["Filter", "Showing"],
                [Choice(cells) for cells in state.filters.showing()],
                keys={"c": "clear all filters"},
                notice="Enter changes the highlighted filter. Filters last until setup ends.",
                default=at,
                back=True,
            )
            if picked == BACK:
                return plan
            if picked == "c":
                state.filters = state.filters.cleared()
                continue
            at = int(picked)
            plan = self._edit_filter(plan, state, at, rows, today)

    def _edit_filter(
        self,
        plan: Plan,
        state: BrowseState,
        index: int,
        rows: Sequence[Candidate],
        today: datetime.date,
    ) -> Plan:
        """One filter's picker. Every change applies at once; Esc goes back with it kept."""
        field_name = FILTER_FIELDS[index]

        def count(**change: Any) -> int:
            return count_matching(rows, replace(state.filters, **change), today)[0]

        def status() -> str:
            matching, fitting = count_matching(rows, state.filters, today)
            showing = dict(state.filters.showing())[_FILTER_NAMES[field_name]]
            return f"Showing: {showing}; {_matching(matching)}, {fitting} fit this machine."

        def apply(value: Any) -> None:
            state.filters = replace(state.filters, **{field_name: value})

        if field_name in _MULTI_FILTERS:
            name, any_label = _MULTI_FILTERS[field_name]
            self._pick_many(
                name,
                _multi_options(field_name, rows),
                lambda: getattr(state.filters, field_name),
                lambda v: count(**{field_name: frozenset({v})}),
                any_label,
                apply,
                status,
            )
        elif field_name == "uncensored":
            plan = self._edit_uncensored(plan, state, count)
        else:
            title, options = _SINGLE_FILTERS[field_name]
            current = getattr(state.filters, field_name)
            apply(self._pick_one(title, options, current, lambda v: count(**{field_name: v})))
        return plan

    def _edit_uncensored(self, plan: Plan, state: BrowseState, count: Callable[..., int]) -> Plan:
        current = state.filters.uncensored
        wanted = self._pick_one(
            "Uncensored models", UNCENSORED_OPTIONS, current, lambda v: count(uncensored=v)
        )
        if wanted == current:
            return plan
        shown = self._show_uncensored(plan, wanted != HIDDEN)
        if shown is None:  # declined the warning
            return plan
        state.filters = replace(state.filters, uncensored=wanted)
        return shown

    def _pick_many(
        self,
        name: str,
        options: Sequence[tuple[str, str]],
        ticked: Callable[[], frozenset[str]],
        count: Callable[[str], int],
        any_label: str,
        apply: Callable[[frozenset[str]], None],
        status: Callable[[], str],
    ) -> None:
        """Tick any number of ``options``: each tick applies at once, ``c`` clears, Esc leaves."""
        at = 0
        while True:
            chosen = ticked()
            picked = self.prompter.choose(
                f"{name} (Enter ticks or unticks; nothing ticked means {any_label}):",
                [name, "Files"],
                [
                    Choice((f"[{'x' if value in chosen else ' '}] {label}", str(count(value))))
                    for value, label in options
                ],
                keys={"c": f"clear ({any_label})"},
                notice=status(),
                default=at,
                back=True,
            )
            if picked == BACK:
                return
            if picked == "c":
                apply(frozenset())
                continue
            at = int(picked)
            apply(chosen ^ {options[at][0]})

    def _pick_one(
        self,
        title: str,
        options: Sequence[tuple[Any, str]],
        current: Any,
        count: Callable[[Any], int],
    ) -> Any:
        """One of ``options``, applied on Enter; ``c`` takes the first ("any").

        Esc keeps ``current``.
        """
        values = [value for value, _ in options]
        picked = self.prompter.choose(
            f"{title}:",
            ["Showing", "Files"],
            [Choice((label, str(count(value)))) for value, label in options],
            keys={"c": f"clear ({options[0][1].lower()})"},
            default=values.index(current) if current in values else 0,
            back=True,
        )
        if picked == BACK:
            return current
        return values[0] if picked == "c" else values[int(picked)]

    def _show_uncensored(self, plan: Plan, show: bool) -> Plan | None:
        """Save whether uncensored models are shown, warning first; None when declined."""
        if show and not self.settings.show_uncensored:
            self.prompter.new_screen("Uncensored models")
            self.prompter.paragraph(
                f"{UNCENSORED_WARNING} Once shown, they stay in every list and search, in later "
                "runs too, until you hide them again in Filters."
            )
            if not self.prompter.confirm("Show them?", default=False):
                return None
        if show == self.settings.show_uncensored:
            return plan
        self.settings.show_uncensored = show
        save_settings(self.app.paths, self.settings)
        return make_plan(self.app, show_uncensored=show)

    def _card(self, repo_id: str) -> str:
        """The repo's model card, on the mirror if ``HF_ENDPOINT`` names one."""
        return HubClient.from_env(self.app.env).model_card_url(repo_id)

    def _cells(self, c: Candidate, lightest: bool = False) -> tuple[str, ...]:
        """A list row; ``lightest`` rows (All models) show no context, it is always 32k."""
        cls = c.entry.classification
        name = cls.family.display_name if cls.family else f"Ornith ? {cls.size_label or '?'}"
        repo = c.entry.repo
        updated = updated_on(c.entry)
        context = () if lightest else (context_label(c.context),)
        # The licence comes early so a narrow terminal cuts the counts, never the licence.
        return (
            name,
            cls.source.value,
            repo.licence or "none",
            cls.quant or "?",
            *context,
            gib(c.estimate.total),
            gib(download_size(c.entry, c.vision)),
            FIT_LABELS[c.fit],
            compact_count(repo.likes),
            compact_count(repo.downloads),
            compact_count(repo.downloads_all_time),
            updated.isoformat() if updated else "-",
        )

    def _info(self, c: Candidate, scorer: Scorer | None = None) -> tuple[tuple[str, str], ...]:
        """The labelled lines under a list for its highlighted row."""
        lines = [("File", f"{c.entry.repo.id}/{c.entry.file.path}")]
        if c.fit_reason:
            lines.append(
                (FIT_LABELS[c.fit].capitalize(), c.fit_reason[:1].upper() + c.fit_reason[1:])
            )
        if scorer is not None:
            lines.append(("Score", scorer.score(c).summary().removeprefix("score ")))
        badges = [b for b in c.badges if b not in (Level.ESTIMATED.value,)]
        if badges:
            lines.append(("Badges", " ".join(badges)))
        if c.notes:
            notes = "; ".join(c.notes)
            lines.append(("Notes", notes[:1].upper() + notes[1:]))
        return tuple(lines)

    def _accept_fit(self, selection: Selection, candidate: Candidate) -> bool:
        """Warn before a setting that is a stretch or too big; True to go on with it.

        Each risk is asked about once: a choice no worse than one already accepted goes on.
        """
        fit = candidate.fit
        if FIT_ORDER[fit] <= FIT_ORDER[selection.accepted_fit]:
            return True
        cls = candidate.entry.classification
        name = f"{cls.family.display_name if cls.family else 'This model'} {cls.quant or ''}"
        at = f"{name.rstrip()} at {context_label(candidate.context)} context"
        if fit is Fit.TOO_BIG:
            self.prompter.new_screen("Too big for this machine")
            self.prompter.paragraph(
                f"Warning: {at} {candidate.fit_reason}. It will most likely fail to load, or "
                "swap so heavily that it is unusable, and loading it can make the whole machine "
                "unresponsive. You can still download and set it up, at your own risk: for "
                "example to use it on a machine with more memory later."
            )
            question, default = "Download it anyway, at your own risk?", False
        else:
            self.prompter.new_screen("A stretch for this machine")
            self.prompter.paragraph(f"Warning: {at} {candidate.fit_reason}.")
            question = "Use it anyway?"
            default = candidate.fits  # only short of free memory right now: yes by default
        if not self.prompter.confirm(question, default=default):
            return False
        selection.accepted_fit = fit
        return True

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

    def options(self, selection: Selection, plan: Plan, settings_first: bool = False) -> Selection:
        """The context screen, then the settings menu; Esc on Settings returns to the contexts.

        With ``settings_first`` (coming back from a later step) the settings menu opens first.
        """
        chosen = selection.candidate
        # Coming back keeps the port already chosen; the first time, the first free one.
        port = selection.port if settings_first else self.port or first_free_port()
        while True:
            if not settings_first:
                chosen = self._choose_context(selection, plan, chosen)
            settings_first = False
            try:
                chosen, port = self._settings_menu(selection, plan, chosen, port)
                break
            except Back:
                continue
        selection.candidate = chosen
        selection.port = port
        if selection.local is not None and not chosen.vision:
            selection.mmproj_path = None
        return selection

    def _choose_context(self, selection: Selection, plan: Plan, current: Candidate) -> Candidate:
        """The context screen for ``current``'s KV cache, slots and vision; Esc raises Back."""
        candidates = build_candidates(
            [selection.entry],
            plan.report.pools,
            load_calibration(),
            load_tested(),
            slots=current.slots,
            kv_types=[current.kv_type],
        )
        rows = [
            c
            for c in rank(candidates, plan.families)
            if c.placement == current.placement and c.vision == current.vision
        ]
        contexts: list[Candidate] = []
        for c in rows:
            if all(c.context != seen.context for seen in contexts):
                contexts.append(c)
        contexts.sort(key=lambda c: c.context)
        recommended = recommended_context(contexts)
        default = next(
            (i for i, c in enumerate(contexts) if c.context == current.context),
            recommended if recommended is not None else 0,
        )
        # The Tested column only earns its width when the rows differ; else the notice says it.
        levels = {c.level for c in contexts}
        show_level = len(levels) > 1
        notice = memory_notice(contexts[0], None if show_level else min(levels)) if contexts else ""
        headers = ["Context", "Context memory", "Total", "Plan left", "Reserve used", "Free after"]
        while True:
            picked = self.prompter.choose(
                "Context size (the longest prompt the model can read at once):",
                [*headers, "Verdict", *(["Tested"] if show_level else [])],
                [
                    Choice(
                        (
                            context_label(c.context)
                            + (" (native)" if c.context == c.native else ""),
                            gib(c.estimate.context_part),
                            gib(c.estimate.total),
                            *memory_cells(c),
                            "recommended" if i == recommended else c.fit.value,
                            *([c.level.value] if show_level else []),
                        ),
                        detail=_context_note(c),
                    )
                    for i, c in enumerate(contexts)
                ],
                notice=notice,
                default=default,
                back=True,
                main_menu=selection.local is None,  # from a local model, Back is the first menu
            )
            if picked == BACK:
                raise Back()
            if picked == MAIN_MENU:
                raise MainMenu()
            chosen = contexts[int(picked)]
            if self._accept_fit(selection, chosen):
                break
            default = recommended if recommended is not None else int(picked)  # back to advice
        return chosen

    def _settings_menu(
        self, selection: Selection, plan: Plan, chosen: Candidate, port: int
    ) -> tuple[Candidate, int]:
        """Settings as a menu: Enter changes one, Continue (last, the default) goes on.

        Esc raises Back.

        A change applies at once and the memory line is worked out again; one that makes
        the setting a stretch or too big asks first.
        """
        at = _CONTINUE
        cls = selection.entry.classification
        name = f"{cls.family.display_name if cls.family else 'this model'} {cls.quant or ''}"
        has_vision = selection.entry.repo.best_mmproj() is not None
        while True:
            native = " (native)" if chosen.context == chosen.native else ""
            rows = [
                Choice(("Context", context_label(chosen.context) + native)),
                Choice(("KV cache", _kv_label(chosen.kv_type))),
                Choice(("Parallel requests", _slots_label(chosen.slots))),
                Choice(("Vision", _vision_label(selection, chosen.vision)), enabled=has_vision),
                Choice(("Port", str(port))),
                Choice(("Continue with these settings", ""), action=True),
            ]
            picked = self.prompter.choose(
                f"Settings for {name.rstrip()}:",
                ["Setting", "Value"],
                rows,
                notice=_settings_notice(chosen),
                default=at,
                back=True,
                main_menu=selection.local is None,
            )
            if picked == BACK:
                raise Back()
            if picked == MAIN_MENU:
                raise MainMenu()
            at = int(picked)
            if at == _CONTINUE:
                return chosen, port
            if at == 0:
                raise Back()  # the context screen, which comes back here
            if at == 4:
                port = self._ask_port(port)
                continue
            if at == 1:
                options = [(kv, self._variant(selection, plan, chosen, kv=kv)) for kv in KV_TYPES]
            elif at == 2:
                options = [(n, self._variant(selection, plan, chosen, slots=n)) for n in (4, 1)]
            else:
                options = [
                    (v, self._variant(selection, plan, chosen, vision=v)) for v in (True, False)
                ]
            chosen = self._pick_setting(selection, chosen, at, options)

    def _variant(
        self,
        selection: Selection,
        plan: Plan,
        chosen: Candidate,
        kv: str | None = None,
        slots: int | None = None,
        vision: bool | None = None,
    ) -> Candidate | None:
        """``chosen`` with one setting changed, or None when no such setting is planned."""
        candidates = build_candidates(
            [selection.entry],
            plan.report.pools,
            load_calibration(),
            load_tested(),
            slots=chosen.slots if slots is None else slots,
            contexts=[chosen.context],
            kv_types=[chosen.kv_type if kv is None else kv],
        )
        wanted = chosen.vision if vision is None else vision
        matching = [
            c
            for c in rank(candidates, plan.families)
            if c.vision == wanted and c.placement == chosen.placement
        ]
        return matching[0] if matching else None

    def _pick_setting(
        self,
        selection: Selection,
        chosen: Candidate,
        row: int,
        options: Sequence[tuple[Any, Candidate | None]],
    ) -> Candidate:
        """One setting's options and what each does to memory; ``chosen`` on Esc."""
        title, column, label, note = _SETTING_ROWS[row]
        offered = [(value, c) for value, c in options if c is not None]

        def same(c: Candidate) -> bool:
            return (c.kv_type, c.slots, c.vision) == (chosen.kv_type, chosen.slots, chosen.vision)

        current = next((i for i, (_, c) in enumerate(offered) if same(c)), 0)
        choices = [
            Choice(
                (
                    label(value),
                    gib(c.estimate.total),
                    FIT_LABELS[c.fit],
                    memory_cells(c)[2],
                    note(value, selection),
                )
            )
            for value, c in offered
        ]
        picked = self.prompter.choose(
            f"{title}:",
            [column, "Memory", "Fits", "Free after", "Notes"],
            choices,
            default=current,
            back=True,
        )
        if picked == BACK:
            return chosen
        result = offered[int(picked)][1]
        return result if self._accept_fit(selection, result) else chosen

    def _ask_port(self, port: int) -> int:
        """A port for the server (127.0.0.1 only); the old one when the answer will not do."""
        answer = self.prompter.text("Port (1024 to 65535)", str(port))
        if not answer.isdigit() or not 1024 <= int(answer) <= 65535:
            self.say(f"{answer!r} is not a port from 1024 to 65535; keeping {port}.")
            return port
        if int(answer) != port and not port_is_free(int(answer)):
            self.say(
                f"Port {answer} is in use; keeping {port}. Stop what uses it, or pick another."
            )
            return port
        return int(answer)

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
            keep = check.ok and self.prompter.confirm("Keep this folder?", default=True, back=True)
            if keep is None:
                raise Back()  # to the settings
            if (
                keep
                and check.warnings
                and not self.prompter.confirm("Use it anyway?", default=False)
            ):
                keep = False
            if keep:
                break
            answer = self.prompter.text(
                f"Folder for downloaded models ({shown_key(BACK)} to keep this one)", back=True
            )
            if is_back(answer):
                if not check.ok:
                    raise Back()  # nothing usable to keep: back to the settings
                continue
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
            ("Memory", f"about {gib(c.estimate.total)} ({c.level.value}){_fit_warning(c)}"),
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
        native = self.plan.report.native if self.plan else None
        if flavour is not None:
            rows.append(
                (
                    "Runtime",
                    f"llama.cpp {flavour.image.tag} in Docker (downloaded once if missing, "
                    "a few GB)",
                )
            )
        elif native is not None:
            rows.append(
                (
                    "Runtime",
                    f"llama.cpp {native.build} for macOS, no Docker (downloaded once if "
                    f"missing, about {native.size / 1000**2:.0f} MB)",
                )
            )
        for line in key_values(rows):
            self.say(line)
        if selection.preset:
            return  # choosing Resume was the go-ahead
        go = self.prompter.confirm("Go ahead?", default=True, back=True)
        if go is None:
            raise Back()  # to the folder screen, or the settings
        if not go:
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


def _kv_label(kv: str) -> str:
    return kv if kv == "f16" else f"{kv} (UNTESTED with Ornith)"


def _kv_note(kv: str, selection: Selection) -> str:
    return "the default" if kv == "f16" else "about half the KV cache memory; UNTESTED with Ornith"


def _slots_label(slots: int) -> str:
    return f"{slots} (good for coding agents)" if slots > 1 else "1 (one at a time)"


def _slots_note(slots: int, selection: Selection) -> str:
    return "coding agents send several requests at once" if slots > 1 else "one request at a time"


def _vision_note(on: bool, selection: Selection) -> str:
    mmproj = selection.entry.repo.best_mmproj()
    if not on:
        return "text only"
    if selection.local is not None or mmproj is None:
        return "reads images"
    return f"reads images; adds a {gib(mmproj.size)} vision file"


# The settings menu's pickers by row: (title, column, how a value reads, its note).
_SETTING_ROWS: dict[int, tuple[str, str, Callable[[Any], str], Callable[[Any, Selection], str]]] = {
    1: ("KV cache (memory for the conversation so far)", "Type", str, _kv_note),
    2: ("Parallel requests (how many chats or agent calls run at once)", "Slots", str, _slots_note),
    3: ("Vision (image input)", "Vision", lambda on: "on" if on else "off", _vision_note),
}
_CONTINUE = 5  # the settings menu's last row, below a blank line


def _settings_notice(c: Candidate) -> str:
    free = memory_cells(c)[2]
    return (
        f"Memory with these settings: {gib(c.estimate.total)} ({c.level.value}); "
        f"{c.fit.value}, {free} free after.\nEnter changes the highlighted setting."
    )


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


# The filter screen's multi-tick rows: field -> (title, "any" label).
_MULTI_FILTERS = {
    "sizes": ("Model size", "any size"),
    "versions": ("Version", "any version"),
    "quants": ("Quant", "any quant"),
}
# The single-choice rows: field -> (title, options).
_SINGLE_FILTERS = {
    "source": ("Source (who made the file)", SOURCE_OPTIONS),
    "licence": ("Licence", LICENCE_OPTIONS),
    "vision": ("Vision", VISION_OPTIONS),
    "updated_days": ("Updated (when the Hugging Face repo last changed)", UPDATED_OPTIONS),
    "min_likes": ("Likes (on the Hugging Face repo)", LIKES_OPTIONS),
    "min_downloads": ("Downloads in the last 30 days (per Hugging Face repo)", DOWNLOADS_OPTIONS),
    "min_lifetime_downloads": ("Downloads in all (per Hugging Face repo)", LIFETIME_OPTIONS),
}
# Each field's row name on the filter screen, as ListFilters.showing() gives it.
_FILTER_NAMES = dict(zip(FILTER_FIELDS, (name for name, _ in ListFilters().showing())))


def _multi_options(field_name: str, rows: Sequence[Candidate]) -> list[tuple[str, str]]:
    """(value, label) to tick for a multi-tick filter: what the catalog has, in order."""
    if field_name == "quants":
        return [(g, QUANT_LABELS[g]) for g in QUANT_GROUPS]
    classes = [c.entry.classification for c in rows]
    if field_name == "sizes":
        shapes = sorted({c.shape for c in classes} - {None}, key=lambda s: s.params_b)
        return [(s.label, s.label) for s in shapes]
    versions = sorted({c.version for c in classes} - {None}, key=version_key, reverse=True)
    return [(v, v) for v in versions]


def _files(count: int) -> str:
    return f"{count} file" if count == 1 else f"{count} files"


def _matching(count: int) -> str:
    return f"{_files(count)} {'matches' if count == 1 else 'match'}"


def _fit_warning(c: Candidate) -> str:
    if c.fit is Fit.FITS:
        return ""
    risk = ", at your own risk" if c.fit is Fit.TOO_BIG else ""
    return f"; warning: {c.fit.value}{risk}: {c.fit_reason}"


def recommended_context(contexts: Sequence[Candidate]) -> int | None:
    """The row to recommend: the longest context up to native that fits; else the longest fit.

    Beyond native, YaRN slows down long prompts, so it is never the first advice.
    """
    fitting = [i for i, c in enumerate(contexts) if c.fit is Fit.FITS]
    native = [i for i in fitting if contexts[i].context <= contexts[i].native]
    if native:
        return max(native, key=lambda i: contexts[i].context)
    return max(fitting, key=lambda i: contexts[i].context) if fitting else None


def memory_notice(c: Candidate, level: Level | None = None) -> str:
    """This machine's memory, the model's fixed share and a key to the memory columns.

    ``level`` is the Tested label every row shares, when the list leaves out that column.
    """
    lines = []
    for n in c.needs:
        pool = n.pool
        text = (
            f"{pool.name}: {gib(pool.total)} in total. The system uses about "
            f"{gib(pool.baseline)}, so {gib(pool.usable)} is usable"
        )
        if pool.available_now is not None:
            text += f"; {gib(pool.available_now)} is free right now"
        lines.append(
            f"{text}. Setup plans up to {gib(n.budget)} and keeps {gib(n.pool.usable - n.budget)} "
            "(15%) in reserve so the model does not slow down or fail under load."
        )
    estimate = c.estimate
    weights = f"The model weights take {gib(estimate.weights)}"
    if estimate.mmproj:
        weights += f", plus {gib(estimate.mmproj)} for the vision file"
    lines.append(f"{weights}; each context adds its KV cache and compute buffers (Context memory).")
    key = (
        "Plan left: room before the reserve. Reserve used: how much of the reserve a setting "
        "takes. Free after: what the machine really has left once it runs."
    )
    if len(c.needs) > 1:
        key += " Each row shows its fullest memory pool."
    if level is not None:
        key += f" All memory figures here are {level.value}."
    lines.append(key)
    return "\n".join(lines)


def memory_cells(c: Candidate) -> tuple[str, str, str]:
    """Plan left, reserve used and free after, for the pool the candidate fills most."""
    n = max(c.needs, key=lambda n: n.need / max(n.pool.usable, 1))
    reserve = n.pool.usable - n.budget
    used = min(max(n.need - n.budget, 0), reserve)
    free = n.pool.usable - n.need
    if used == 0:
        reserve_text = "none"
    elif free < 0:
        reserve_text = f"all {gib(reserve)}"
    else:
        reserve_text = f"{used / GIB:.1f} of {gib(reserve)}"
    free_text = gib(free) if free >= 0 else f"{gib(-free)} short"
    return gib(max(n.spare, 0)), reserve_text, free_text


def _context_note(c: Candidate) -> str:
    # Every row is the same model here, so its third-party note is shown once, elsewhere.
    notes = [note for note in c.notes if note != THIRD_PARTY_NOTE]
    if c.yarn_factor:
        notes.insert(0, f"YaRN x{c.yarn_factor}: slower prefill on long prompts")
    if c.context < SHORT_CONTEXT:
        notes.insert(0, "short for coding agents: their own instructions and tools fill much of it")
    if c.fit_reason:
        notes.insert(0, c.fit_reason)
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
        "--shortcuts", metavar="NAME", help="add a desktop icon or menu entry for a profile"
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
        shortcuts=None,
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
    if getattr(args, "shortcuts", None):
        return setup_profile.add_shortcuts(app, prompter, args.shortcuts)
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
        if selection is not None and selection.needs_download:
            lines = [
                f"Setup stopped. The downloaded model stays in {selection.model_paths[0].parent} "
                "and is found when you run ornith-launcher setup again."
            ]
        else:
            lines = [
                "Setup stopped. Nothing else was changed; run ornith-launcher setup to start again."
            ]
        if unfinished_downloads(app.paths):
            lines.append(
                "A stopped download is kept: resume it with r on setup's first menu, or remove it "
                "with u, Uninstall."
            )
        prompter.quit_screen(lines)
        return 1
    try:
        return setup_profile.launch_now(app, prompter, profile)
    except Cancelled:
        # The profile is already saved, so setup itself finished.
        prompter.quit_screen(
            [f"Not started. Start it any time with: ornith-launcher launch {profile.name}"]
        )
        return 0


def finish(app: App, selection: Selection) -> None:
    app.say("The model is ready:")
    for path in selection.model_paths:
        app.say(f"  {path}")
    if selection.mmproj_path:
        app.say(f"  {selection.mmproj_path} (vision)")
