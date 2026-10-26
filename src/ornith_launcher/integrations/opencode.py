"""OpenCode: add each profile's model to the user's OpenCode config, and remove it again.

Only our own keys are touched: ``provider.<ours>.models.<alias>`` and the
provider's connection details. The file is backed up before every change and
written atomically with its old permissions. A file with comments (JSONC)
cannot be rewritten by the standard library without losing them, so the user
gets the snippet to paste instead, unless they agree to drop the comments.
"""

from __future__ import annotations

import copy
import datetime
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ornith_launcher.config import atomic_write_text
from ornith_launcher.profiles import Profile

PROVIDER = "ornith-launcher"
PROVIDER_NAME = "Ornith (local, ornith-launcher)"
SCHEMA = "https://opencode.ai/config.json"
OUTPUT_TOKENS = 32000


def config_path(env: Mapping[str, str], home: Path) -> Path:
    base = env.get("XDG_CONFIG_HOME")
    folder = (Path(base) if base and Path(base).is_absolute() else home / ".config") / "opencode"
    jsonc = folder / "opencode.jsonc"
    return (
        jsonc
        if jsonc.exists() and not (folder / "opencode.json").exists()
        else folder / "opencode.json"
    )


def has_comments(text: str) -> bool:
    """True if the text has // or /* */ comments outside strings."""
    in_string = escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "/" and text[index + 1 : index + 2] in ("/", "*"):
            return True
    return False


def provider_id_for(config: Mapping[str, Any], profile: Profile) -> str:
    """One shared provider while ports agree; a provider per profile when they differ."""
    providers = config.get("provider") or {}
    shared = providers.get(PROVIDER)
    if shared is None or shared.get("options", {}).get("baseURL") == base_url(profile):
        return PROVIDER
    return f"{PROVIDER}-{profile.name}"


def base_url(profile: Profile) -> str:
    return f"{profile.url}/v1"


def model_entry(profile: Profile, display: str) -> dict[str, Any]:
    return {"name": display, "limit": {"context": profile.runtime.context, "output": OUTPUT_TOKENS}}


def merged(config: Mapping[str, Any], profile: Profile, provider_id: str, display: str) -> dict:
    """The config with this profile's model added; every other key is kept as it was."""
    result = copy.deepcopy(dict(config))
    result.setdefault("$schema", SCHEMA)
    providers = result.setdefault("provider", {})
    provider = providers.setdefault(provider_id, {})
    provider["npm"] = "@ai-sdk/openai-compatible"
    provider["name"] = (
        PROVIDER_NAME if provider_id == PROVIDER else f"{PROVIDER_NAME}: {profile.name}"
    )
    provider.setdefault("options", {})["baseURL"] = base_url(profile)
    provider.setdefault("models", {})[profile.runtime.alias] = model_entry(profile, display)
    return result


def without(config: Mapping[str, Any], provider_id: str, alias: str) -> dict:
    """The config with this profile's model removed, and our provider too once it is empty."""
    result = copy.deepcopy(dict(config))
    providers = result.get("provider") or {}
    provider = providers.get(provider_id)
    if provider is None:
        return result
    (provider.get("models") or {}).pop(alias, None)
    if not provider.get("models"):
        providers.pop(provider_id)
    return result


def snippet(profile: Profile, provider_id: str, display: str) -> str:
    return json.dumps({"provider": merged({}, profile, provider_id, display)["provider"]}, indent=2)


def backup(path: Path, now: datetime.datetime) -> Path:
    stamp = now.strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.ornith-launcher-backup-{stamp}")
    target.write_bytes(path.read_bytes())
    return target


def restore(record: Mapping[str, str]) -> None:
    """Put the config back exactly as setup found it; only safe right after setup wrote it."""
    path = Path(record["config"])
    if record.get("backup"):
        Path(record["backup"]).replace(path)
    else:
        path.unlink(missing_ok=True)  # setup created it


def write(path: Path, config: Mapping[str, Any]) -> None:
    atomic_write_text(path, json.dumps(config, indent=2) + "\n")


def remove_entry(app, profile: Profile) -> None:
    """Undo what setup added for this profile; a file the user changed by hand stays valid."""
    record = profile.integrations.get("opencode") or {}
    path = Path(record.get("config", ""))
    if not record.get("provider") or not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    if has_comments(text):
        app.say(
            f"Remove the provider {record['provider']} from {path} by hand (the file has comments)."
        )
        return
    try:
        config = json.loads(text)
    except ValueError:
        app.say(f"{path} is not valid JSON; remove the provider {record['provider']} by hand.")
        return
    backup(path, datetime.datetime.now())
    write(path, without(config, record["provider"], profile.runtime.alias))
    app.say(f"Removed {profile.runtime.alias} from {path} (a backup was kept).")
