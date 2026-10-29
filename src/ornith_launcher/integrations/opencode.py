"""OpenCode: add each profile's model to the user's OpenCode config, and remove it again.

Only our own keys are touched: ``provider.<ours>.models.<alias>``, the
provider's connection details and, if the user agrees, the default ``model``,
which uninstall puts back as it was. The file is backed up before every change and
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

from ornith_launcher.catalog import gguf
from ornith_launcher.config import Paths, atomic_write_text
from ornith_launcher.profiles import Profile

PROVIDER = "ornith-launcher"
PROVIDER_NAME = "Ornith (local, ornith-launcher)"
SCHEMA = "https://opencode.ai/config.json"
OUTPUT_TOKENS = 32000

# Every Ornith model thinks before it answers unless the request turns thinking off, and
# it has no effort levels: llama.cpp sends low, medium and high to the model alike. So
# the entry replaces the three levels OpenCode would offer with one real choice.
EFFORT_LEVELS = ("low", "medium", "high")
THINKING_OFF = "no-thinking"

# OpenCode's chat draws images that are part of the model's reply as data: URIs, but not
# images a tool read or files on disk (checked in OpenCode 1.19). Models do not know
# that, so setup adds this file to OpenCode's "instructions", which go into the system
# prompt of every session. Models copy readable SVG from a tool's output exactly but slip
# on long encoded strings: base64 tags and, in a later session, two percent-encoded tags
# came out with changed characters, while a 2,092-character readable tag was copied
# exactly and showed. A raw # ends the URI, so the script encodes only % and #. The
# model also built the tag around the printed text by hand and lost its closing quote,
# so the script prints the whole tag.
PICTURES_NAME = "show-pictures.md"
PICTURES = """\
# Showing pictures in the chat

The chat shows an image only when it is part of your reply. It does not show images that a
tool reads, and it does not show files on disk.

To show a chart, diagram or drawing, put an SVG image in your reply as an img tag with a
data: address, on a line of its own. This is the only form the chat shows; do not use
base64 or markdown image syntax. One wrong character in the tag breaks the image, so keep
the picture small (short labels, no more shapes than it needs, about 3000 characters at
most) and build the tag in one of these two ways.

If you can run commands, which is best for anything more than a few shapes:

1. Write the SVG to a file in a temporary folder, for example /tmp/picture.svg. Use single
   quotes inside the SVG, never double quotes, and never the & character (write "and").
   Hex colours, ids, markers and gradients are fine this way.
2. With a short script, read the file and check that it parses as XML. Stop if it has a
   double quote or an &. Replace every % with %25, then every # with %23, join the lines
   into one, and print the whole tag, from <img to the closing ">, in this form:
   <img src="data:image/svg+xml;utf8,SVG">
3. Put the printed line in your reply exactly as printed, on a line of its own. Copy all
   of it; do not build any part of the tag yourself, retype it, shorten it or change any
   character.

If you cannot run commands, write the tag yourself. This complete example works; copy its
form exactly:

<img src="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='400' height='240'><rect width='400' height='240' fill='white'/><line x1='40' y1='200' x2='370' y2='200' stroke='dimgray' stroke-width='2'/><polygon points='370,195 380,200 370,205' fill='dimgray'/><circle cx='200' cy='100' r='6' fill='firebrick'/><text x='212' y='96' font-size='15' font-family='sans-serif' fill='firebrick'>a point</text></svg>">

Rules for a tag you write yourself:

- Start with exactly data:image/svg+xml;utf8, (a semicolon before utf8, a comma after it).
- Never write the # character anywhere in the image: the browser cuts the image off at the
  first #. Use colour names such as steelblue, firebrick or dimgray, or rgb(70,130,180),
  never hex colours such as #333. Before sending, look through the tag for any #.
- Do not use ids, markers, gradients or url(...). Draw arrowheads as small polygons.
- Use single quotes inside the SVG, never double quotes, and never the & character.
- Close every element: every <text> needs its </text>, and the tag ends with </svg>">.
- Keep the whole tag on one line. Write < and > as they are; do not percent-encode them.

If the user says the image is broken or shows as text:

- Shown as text means the tag was not closed: it must end with </svg>">.
- A broken image means a character changed on the way into your reply, or the SVG has a
  mistake.
- Build the tag again the same way (run the script again, or check each rule above if you
  wrote it by hand) and send the new tag. Do not search the web or try other forms.

If the user also wants a file, save one as well.
"""  # noqa: E501 (the example tag must stay on one line)


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


def projector_inputs(mmproj: str) -> list[str]:
    """What the projector file lets the model take in besides text: images, audio or both.

    llama.cpp marks each encoder in the file's keys; a projector without the keys,
    or one that cannot be read, is a vision projector, as all older ones are.
    """
    try:
        values = gguf.read_metadata(gguf.file_prefix_reader(Path(mmproj))).values
    except (OSError, ValueError, gguf.NeedMoreBytes):
        return ["image"]
    inputs = []
    if values.get("clip.has_vision_encoder", "clip.has_audio_encoder" not in values):
        inputs.append("image")
    if values.get("clip.has_audio_encoder"):
        inputs.append("audio")
    return inputs


def model_entry(profile: Profile, display: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": display,
        "limit": {"context": profile.runtime.context, "output": OUTPUT_TOKENS},
        # Without this OpenCode labels the model non-reasoning; the thinking shows either way.
        "reasoning": True,
        "variants": {
            **{level: {"disabled": True} for level in EFFORT_LEVELS},
            THINKING_OFF: {"chat_template_kwargs": {"enable_thinking": False}},
        },
    }
    inputs = projector_inputs(profile.model.mmproj) if profile.model.mmproj else []
    if inputs:
        # OpenCode treats a custom model as text only unless told otherwise, and then
        # refuses images ("this model does not support image input") before sending them.
        # The output stays text: the model writes no images, whatever the config says.
        entry["attachment"] = True
        entry["modalities"] = {"input": ["text", *inputs], "output": ["text"]}
    return entry


def model_ref(provider_id: str, profile: Profile) -> str:
    """How OpenCode names the model: ``provider/model``, as in its ``model`` key and ``-m``."""
    return f"{provider_id}/{profile.runtime.alias}"


def default_after_removal(config: Mapping[str, Any], record: Mapping[str, Any]) -> dict:
    """The config with OpenCode's default model put back to what it was before setup.

    Only while the default is still the one setup made: a default the user picked
    since is theirs. A previous default that was another of our models, removed in
    the meantime, is dropped rather than left pointing at nothing.
    """
    result = copy.deepcopy(dict(config))
    if not record.get("default_model") or result.get("model") != record["default_model"]:
        return result
    previous = record.get("previous_model")
    if previous and _is_ours(previous) and not _exists(result, previous):
        previous = None
    if previous:
        result["model"] = previous
    else:
        result.pop("model", None)
    return result


def _is_pictures(entry: str) -> bool:
    path = Path(entry)
    return path.name == PICTURES_NAME and path.parent.name == "opencode"


def _is_ours(ref: str) -> bool:
    return ref.split("/", 1)[0].startswith(PROVIDER)


def _exists(config: Mapping[str, Any], ref: str) -> bool:
    provider_id, _, alias = ref.partition("/")
    provider = (config.get("provider") or {}).get(provider_id) or {}
    return alias in (provider.get("models") or {})


def pictures_file(paths: Paths) -> Path:
    return paths.data / "opencode" / PICTURES_NAME


def write_pictures(paths: Paths) -> Path:
    """Write (or refresh) the picture instructions in the launcher's own data folder."""
    target = pictures_file(paths)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, PICTURES)
    return target


def merged(
    config: Mapping[str, Any],
    profile: Profile,
    provider_id: str,
    display: str,
    pictures: Path | None = None,
) -> dict:
    """The config with this profile's model added; every other key is kept as it was.

    ``pictures``, when given, is added to OpenCode's ``instructions`` list once.
    """
    result = copy.deepcopy(dict(config))
    result.setdefault("$schema", SCHEMA)
    if pictures is not None:
        instructions = result.setdefault("instructions", [])
        if isinstance(instructions, list) and str(pictures) not in instructions:
            instructions.append(str(pictures))
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
    """The config with this profile's model removed, and our provider too once it is empty.

    Once none of our models is left, our picture instructions go too (any file
    named like ours, wherever the launcher's data folder was), and an
    ``instructions`` list that held only them is removed.
    """
    result = copy.deepcopy(dict(config))
    providers = result.get("provider") or {}
    provider = providers.get(provider_id)
    if provider is not None:
        (provider.get("models") or {}).pop(alias, None)
        if not provider.get("models"):
            providers.pop(provider_id)
    instructions = result.get("instructions")
    if isinstance(instructions, list) and not any(_is_ours(p) for p in providers):
        kept = [p for p in instructions if not _is_pictures(str(p))]
        if kept:
            result["instructions"] = kept
        else:
            result.pop("instructions")
    return result


def pictures_removed(config: Mapping[str, Any], provider_id: str, alias: str) -> list[str]:
    """The picture instructions that removing this model takes out of ``instructions``."""
    before = config.get("instructions")
    if not isinstance(before, list):
        return []
    after = without(config, provider_id, alias).get("instructions") or []
    return [str(entry) for entry in before if entry not in after]


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
            f"  Remove the provider {record['provider']} from {path} by hand (the file has "
            "comments)."
        )
        return
    try:
        config = json.loads(text)
    except ValueError:
        app.say(f"  {path} is not valid JSON; remove the provider {record['provider']} by hand.")
        return
    backup(path, datetime.datetime.now())
    result = default_after_removal(
        without(config, record["provider"], profile.runtime.alias), record
    )
    write(path, result)
    app.say(f"  Removed {profile.runtime.alias} from {path} (a backup was kept).")
    if config.get("model") != result.get("model"):
        app.say(
            f"  OpenCode's default model is {result['model']} again."
            if result.get("model")
            else "  OpenCode has no default model set again, as before setup."
        )
