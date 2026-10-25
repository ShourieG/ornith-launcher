"""Synthetic catalog repos for tests, with file sizes from the official Ornith repos."""

from __future__ import annotations

from ornith_launcher.catalog import gguf
from ornith_launcher.catalog.catalog import Catalog, FilePart, ModelFile, Repo, header_subset
from tests.gguf_builder import gguf_bytes, ornith_pairs

GIB = 1024**3


def header(size: str, name: str = "", nextn: bool = True) -> dict:
    values = gguf.parse_metadata(gguf_bytes(ornith_pairs(size, name=name, nextn=nextn))).values
    return header_subset(values)


def repo(
    repo_id: str,
    size: str,
    files: dict[str, float],
    *,
    licence: str | None = "mit",
    mmproj_gib: float | None = None,
    base_models: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
    name: str = "",
    downloads: int = 0,
    gated: bool = False,
) -> Repo:
    """A repo whose files all share one header; ``files`` maps file name to size in GiB."""
    model_files = tuple(
        ModelFile((FilePart(path, int(gib * GIB), f"{index:064x}"),))
        for index, (path, gib) in enumerate(sorted(files.items()), 1)
    )
    mmproj = ()
    if mmproj_gib:
        mmproj = (FilePart("mmproj-BF16.gguf", int(mmproj_gib * GIB), "f" * 64),)
    return Repo(
        id=repo_id,
        sha="a" * 40,
        last_modified="2026-10-18T00:00:00.000Z",
        gated=gated,
        licence=licence,
        base_models=base_models,
        tags=tags,
        downloads=downloads,
        files=model_files,
        mmproj=mmproj,
        header=None if gated else header(size, name),
        header_file=None if gated else model_files[0].path,
    )


def official_catalog() -> Catalog:
    """The official Ornith GGUF repos (real file sizes), plus a few community builds."""
    repos = [
        repo(
            "ornith-ai/Ornith-1.5-9B-GGUF",
            "9B",
            {
                "Ornith-1.5-9B-BF16.gguf": 17.14,
                "Ornith-1.5-9B-Q8_0.gguf": 9.11,
                "Ornith-1.5-9B-Q6_K.gguf": 7.04,
                "Ornith-1.5-9B-Q5_K_M.gguf": 6.19,
                "Ornith-1.5-9B-Q4_K_M.gguf": 5.38,
            },
            mmproj_gib=0.86,
        ),
        repo(
            "ornith-ai/Ornith-1.0-9B-GGUF",
            "9B",
            {"Ornith-1.0-9B-Q8_0.gguf": 8.87, "Ornith-1.0-9B-Q4_K_M.gguf": 5.24},
            name="Ornith 1.0 9B",
        ),
        repo(
            "ornith-ai/Ornith-1.5-35B-A3B-GGUF",
            "35B",
            {
                "Ornith-1.5-35B-BF16.gguf": 66.19,
                "Ornith-1.5-35B-Q8_0.gguf": 35.21,
                "Ornith-1.5-35B-Q6_K.gguf": 27.20,
                "Ornith-1.5-35B-Q5_K_M.gguf": 23.61,
                "Ornith-1.5-35B-Q4_K_M.gguf": 20.22,
            },
            mmproj_gib=0.84,
        ),
        repo(
            "ornith-ai/Ornith-1.0-35B-GGUF",
            "35B",
            {"Ornith-1.0-35B-Q8_0.gguf": 34.37, "Ornith-1.0-35B-Q4_K_M.gguf": 19.71},
        ),
        repo(
            "ornith-ai/Ornith-1.5-122B-GGUF",
            "122B",
            {"Ornith-1.5-122B-Q8_0.gguf": 123.45, "Ornith-1.5-122B-Q4_K_M.gguf": 70.63},
            mmproj_gib=0.85,
        ),
        repo(
            "ornith-ai/Ornith-1.5-397B-GGUF",
            "397B",
            {"Ornith-1.5-397B-Q8_0.gguf": 399.09, "Ornith-1.5-397B-Q4_K_M.gguf": 227.53},
            mmproj_gib=0.86,
        ),
        repo(
            "bartowski/Ornith-1.5-397B-GGUF",
            "397B",
            {"Ornith-1.5-397B-IQ1_S.gguf": 76.2, "Ornith-1.5-397B-IQ2_XXS.gguf": 99.0},
            base_models=("ornith-ai/Ornith-1.5-397B",),
            licence="mit",
        ),
        repo(
            "dealignai/Ornith-1.5-35B-A3B-UNCENSORED-GGUF",
            "35B",
            {"Ornith-1.5-35B-A3B-CRACK-Q8_0.gguf": 35.21},
            base_models=("ornith-ai/Ornith-1.5-35B-A3B",),
            mmproj_gib=0.84,
            tags=("uncensored",),
        ),
        repo(
            "someone/Ornith-9B-mystery-GGUF",
            "9B",
            {"Ornith-9B-Q8_0.gguf": 9.0},
            licence=None,
        ),
        repo(
            "gated/Ornith-1.5-9B-GGUF",
            "9B",
            {"Ornith-1.5-9B-Q8_0.gguf": 9.11},
            gated=True,
        ),
    ]
    return Catalog(fetched_at=1_760_000_000, repos={r.id: r for r in repos})
