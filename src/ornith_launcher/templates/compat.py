"""Chat-template fixes so Claude Code works with Ornith models.

Claude Code sends system messages in the middle of a conversation. Ornith's
chat templates either reject them (HTTP 500 on every request) or silently drop
them. Each rule here finds one short line in a template and replaces it so a
later system message is rendered as a system turn; a conversation with only a
leading system message renders exactly as before.

The project ships these rules, never copies of the templates (third-party text).
The patch is applied only when the user chooses Claude Code (D15), to a copy in
the tool's data folder; the model file is never changed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ornith_launcher.catalog import gguf
from ornith_launcher.catalog.gguf import GGUFError, NeedMoreBytes

TEMPLATE_KEY = "tokenizer.chat_template"
# The template comes after the tokenizer arrays, which reach about 10 MiB.
_READ_SIZES = (1024**2, 4 * 1024**2, 16 * 1024**2, 64 * 1024**2)

_SYSTEM_TURN = "{{- '<|im_start|>system\\n' + content + '<|im_end|>' + '\\n' }}"


@dataclass(frozen=True)
class Rule:
    id: str
    description: str
    find: str  # must occur exactly once
    replace: str
    tested: tuple[str, ...]  # sha256 of templates this rule was checked against


RULES = (
    Rule(
        id="late-system-raises",
        description="Qwen-style template that raises on a system message after the first",
        find="{{- raise_exception('System message must be at the beginning.') }}",
        replace=_SYSTEM_TURN,
        tested=("e84f32a23fdda27689f868aa4a1a5621f41133e51a48d7f3efcbea2839574259",),
    ),
    Rule(
        id="late-system-skipped",
        description="template that merges the first two system messages and drops later ones",
        find=(
            '{%- if loop.index0 >= num_sys and message.role != "system" '
            'and message.role != "developer" %}'
        ),
        replace=(
            '{%- if loop.index0 >= num_sys and (message.role == "system" '
            'or message.role == "developer") %}\n'
            "        {{- '<|im_start|>system\\n' + (render_content(message.content, true)|trim)"
            " + '<|im_end|>\\n' }}\n"
            "    {%- elif loop.index0 >= num_sys %}"
        ),
        tested=("f55f52930aa8bf44ab5cb85f99370fcc3c56e9a85640b812086d5330bce5d86b",),
    ),
)


@dataclass(frozen=True)
class Verdict:
    rule: Rule | None
    sha256: str
    tested: bool  # the rule was checked against this exact template

    @property
    def needs_patch(self) -> bool:
        return self.rule is not None


def template_sha256(template: str) -> str:
    return hashlib.sha256(template.encode("utf-8")).hexdigest()


def read_template(model: Path) -> str | None:
    """The chat template stored in a GGUF file, or None if it has none."""
    try:
        metadata = gguf.read_metadata(gguf.file_prefix_reader(model), sizes=_READ_SIZES)
    except (OSError, GGUFError, NeedMoreBytes):
        return None
    value = metadata.values.get(TEMPLATE_KEY)
    return value if isinstance(value, str) else None


def check(template: str) -> Verdict:
    """Which rule fixes this template, if any."""
    digest = template_sha256(template)
    for rule in RULES:
        if template.count(rule.find) == 1:
            return Verdict(rule, digest, digest in rule.tested)
    return Verdict(None, digest, False)


def apply(template: str, rule: Rule) -> str:
    if template.count(rule.find) != 1:
        raise ValueError(f"rule {rule.id} does not apply to this template")
    return template.replace(rule.find, rule.replace)
