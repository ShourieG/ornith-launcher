"""Architecture facts derived from GGUF metadata.

Used to recognise a model's size (classify.py) and, later, to estimate its memory
use. Keys are read as ``<architecture>.<name>``, e.g. ``qwen35moe.block_count``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ornith_launcher.catalog.gguf import GGUFError


@dataclass(frozen=True)
class ModelArch:
    architecture: str
    block_count: int
    nextn_layers: int  # MTP ("nextn") layers; llama.cpp loads but does not use them
    embedding_length: int
    context_length: int
    head_count_kv: int
    key_length: int
    value_length: int
    full_attention_interval: int | None  # every Nth layer has full attention; None = all layers
    expert_count: int  # 0 for dense models
    ssm_state_size: int
    ssm_inner_size: int
    ssm_group_count: int
    ssm_time_step_rank: int
    ssm_conv_kernel: int
    has_rope_scaling: bool  # the file sets its own long-context scaling (no YaRN flags on top)

    @property
    def main_layers(self) -> int:
        return self.block_count - self.nextn_layers

    @property
    def attention_layers(self) -> int:
        """Layers with a KV cache that grows with context."""
        if not self.full_attention_interval:
            return self.main_layers
        return self.main_layers // self.full_attention_interval

    @classmethod
    def from_metadata(cls, values: Mapping[str, Any]) -> ModelArch:
        """Build from parsed GGUF metadata; raises GGUFError if a required key is missing."""
        arch = values.get("general.architecture")
        if not isinstance(arch, str):
            raise GGUFError("general.architecture is missing")

        def required(name: str) -> int:
            value = values.get(f"{arch}.{name}")
            if value is None:
                raise GGUFError(f"{arch}.{name} is missing")
            return _as_int(value)

        def optional(name: str) -> int:
            value = values.get(f"{arch}.{name}")
            return 0 if value is None else _as_int(value)

        return cls(
            architecture=arch,
            block_count=required("block_count"),
            nextn_layers=optional("nextn_predict_layers"),
            embedding_length=required("embedding_length"),
            context_length=required("context_length"),
            head_count_kv=required("attention.head_count_kv"),
            key_length=required("attention.key_length"),
            value_length=required("attention.value_length"),
            full_attention_interval=optional("full_attention_interval") or None,
            expert_count=optional("expert_count"),
            ssm_state_size=optional("ssm.state_size"),
            ssm_inner_size=optional("ssm.inner_size"),
            ssm_group_count=optional("ssm.group_count"),
            ssm_time_step_rank=optional("ssm.time_step_rank"),
            ssm_conv_kernel=optional("ssm.conv_kernel"),
            has_rope_scaling=any(key.startswith(f"{arch}.rope.scaling.") for key in values),
        )


def _as_int(value: Any) -> int:
    # Some architectures store per-layer values as an array; the largest one bounds memory use.
    # Parsed headers hold tuples; headers read back from the JSON cache hold lists.
    try:
        if isinstance(value, (tuple, list)):
            return max(int(item) for item in value) if value else 0
        return int(value)
    except (TypeError, ValueError):
        raise GGUFError(f"unexpected value {value!r} in the header") from None
