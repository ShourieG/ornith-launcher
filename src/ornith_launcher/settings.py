"""User settings (``settings.json`` in the config folder): choices that carry over between runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ornith_launcher.config import Paths, atomic_write_json, read_json
from ornith_launcher.errors import OrnithLauncherError

SCHEMA_VERSION = 1


@dataclass
class Settings:
    models_dir: str | None = None  # where downloads go; None = the default folder
    extra_model_dirs: list[str] = field(default_factory=list)  # folders the user entered
    default_profile: str | None = None
    disclaimer_version: int = 0  # the version the user accepted; 0 = never
    catalog_refresh_hours: int = 24
    show_uncensored: bool = False

    def models_path(self, paths: Paths) -> Path:
        return Path(self.models_dir) if self.models_dir else paths.default_models

    def remember_folder(self, folder: Path) -> None:
        text = str(folder)
        if text not in self.extra_model_dirs:
            self.extra_model_dirs.append(text)


def load_settings(paths: Paths) -> Settings:
    try:
        raw = read_json(paths.settings_file)
    except ValueError as exc:
        raise OrnithLauncherError(
            f"{paths.settings_file} is not valid JSON ({exc})",
            hint=f"Fix the file, or move it away to start fresh: mv {paths.settings_file} "
            f"{paths.settings_file}.broken",
        ) from exc
    if raw is None:
        return Settings()
    known = {name for name in Settings.__dataclass_fields__}
    values: dict[str, Any] = {k: v for k, v in raw.items() if k in known}
    return Settings(**values)


def save_settings(paths: Paths, settings: Settings) -> None:
    atomic_write_json(paths.settings_file, {"schema_version": SCHEMA_VERSION, **asdict(settings)})
