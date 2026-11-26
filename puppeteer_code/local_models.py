"""
puppeteer_code/local_models.py
==============================
Local models to offer in setup: a short list, what each needs, and how well it fits
this computer.

Download sizes were copied from the tag pages on ollama.com/library on 2026-11-05.
Memory needed is an estimate, labelled "about" in the UI: the download plus room for
a 16K-token context. Only qwen3.5:9b has been run through whole CompactCode requests
(CLAUDE.md §8); every other entry says it is not tested yet.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from puppeteer_code.hardware import GB, models_folder
from puppeteer_code.privacy import is_cloud_name

CONTEXT_ROOM = 1.5 * GB         # the KV cache for num_ctx 16384, roughly, on top of the weights
SYSTEM_ROOM = 4 * GB            # what the operating system and the IDE keep for themselves
SHARED_GPU = 2 / 3              # the part of Apple's shared memory the GPU may use by default
SMALL_FOR_CPU = 5 * GB          # on the processor alone, a bigger model is too slow to be useful


@dataclass(frozen=True)
class LocalModel:
    name: str
    size: float                 # download, GB as ollama.com shows it
    tools: str                  # "native": real tool calls; "text": writes calls as text, which pcode reads
    note: str
    tested: bool = False

    @property
    def needs(self) -> int:
        return int(self.size * GB + CONTEXT_ROOM)


# Larger first. The order is by size, not by a measured ranking.
CATALOG: list[LocalModel] = [
    LocalModel("qwen3-coder:30b", 19, "native", "Large coding model. Needs a big graphics card."),
    LocalModel("qwen3.5:27b", 17, "native", "Large general model."),
    LocalModel("gpt-oss:20b", 14, "native", "OpenAI's open model."),
    LocalModel("qwen3.5:9b", 6.6, "native", "The default.", tested=True),
    LocalModel("qwen3:8b", 5.2, "native", "Thinks at length before it answers."),
    LocalModel("qwen2.5-coder:7b", 4.7, "text", "Coding model. Fast."),
    LocalModel("qwen3.5:4b", 3.3, "native", "Smaller and quicker. Weaker on big tasks."),
    LocalModel("qwen3:4b", 2.5, "native", "Small."),
    LocalModel("granite4:latest", 2.1, "native", "IBM's small model."),
    LocalModel("qwen3:1.7b", 1.4, "native", "Very small. For trying it out on a slow computer."),
]
BY_NAME = {m.name: m for m in CATALOG}

FITS = {
    "gpu": "Runs on your graphics card",
    "split": "Runs partly on your graphics card, slower",
    "cpu": "Runs on your processor, slower",
    "too_big": "Too big for this computer",
    "unknown": "Memory unknown",
}


def fit(model: LocalModel, hw: dict) -> str:
    memory, gpu = hw.get("memory"), hw.get("gpu")
    if gpu and gpu.get("memory"):
        room = gpu["memory"] * (SHARED_GPU if gpu.get("shared") else 1)
        if model.needs <= room:
            return "gpu"
        if not gpu.get("shared") and memory and model.needs <= room + memory - SYSTEM_ROOM:
            return "split"
        return "too_big"
    if memory is None:
        return "unknown"
    return "cpu" if model.needs <= memory - SYSTEM_ROOM else "too_big"


def recommend(hw: dict) -> Optional[str]:
    """The largest model that fits the graphics card. A tested model that runs on the card, even partly, beats a
    smaller untested one. Else a small model the processor can run."""
    fits = {m.name: fit(m, hw) for m in CATALOG}
    on_gpu = [m for m in CATALOG if fits[m.name] == "gpu"]
    tested = next((m for m in CATALOG if m.tested and fits[m.name] in ("gpu", "split")), None)
    if tested and (not on_gpu or on_gpu[0].size <= tested.size):
        return tested.name
    if on_gpu:
        return on_gpu[0].name
    slower = [m for m in CATALOG if fits[m.name] in ("split", "cpu") and m.size * GB <= SMALL_FOR_CPU]
    return slower[0].name if slower else None


def installed(folder: Optional[Path] = None) -> list[str]:
    """Models in Ollama's folder, read from its manifests, so no server has to run. Cloud names are left out."""
    root = (folder or models_folder()) / "manifests" / "registry.ollama.ai"
    names = []
    if not root.is_dir():
        return names
    for tag in root.glob("*/*/*"):
        if not tag.is_file():
            continue
        namespace, model = tag.parent.parent.name, tag.parent.name
        name = f"{model}:{tag.name}" if namespace == "library" else f"{namespace}/{model}:{tag.name}"
        if not is_cloud_name(name):
            names.append(name)
    return sorted(names)


def offers(hw: dict, have: list[str]) -> list[dict]:
    best = recommend(hw)
    rows = []
    for m in CATALOG:
        kind = fit(m, hw)
        rows.append({**asdict(m), "needs": m.needs, "fit": kind, "fit_text": FITS[kind],
                     "installed": m.name in have, "recommended": m.name == best})
    return rows


def _choice_file(home: Path) -> Path:
    return Path(home) / "local.json"


def chosen(home: Path) -> Optional[str]:
    try:
        value = json.loads(_choice_file(home).read_text(encoding="utf-8")).get("model")
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) and value and not is_cloud_name(value) else None


def choose(home: Path, model: str) -> None:
    if is_cloud_name(model):
        raise ValueError(f"{model} is a cloud model.")
    path = _choice_file(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": model}), encoding="utf-8")


__all__ = ["CATALOG", "BY_NAME", "FITS", "fit", "recommend", "installed", "offers", "chosen", "choose"]
