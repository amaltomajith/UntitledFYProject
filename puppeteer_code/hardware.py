"""
puppeteer_code/hardware.py
==========================
What this computer can run, for choosing a local model: memory, graphics memory and
free disk. Every number is one the system reported. When a value cannot be read it is
None and the UI says "unknown"; nothing here guesses.

Graphics memory is read from `nvidia-smi` for NVIDIA cards. Apple Silicon shares one
pool of memory between the processor and the GPU, so it reports that. Other GPUs
(AMD, Intel) are listed as unknown, and models are judged for the processor instead.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

GB = 1024 ** 3
SMI_TIMEOUT = 5


def total_memory() -> Optional[int]:
    try:
        import psutil
        return int(psutil.virtual_memory().total)
    except Exception:
        return None


def models_folder() -> Path:
    """Where Ollama keeps models: OLLAMA_MODELS if set, else ~/.ollama/models."""
    custom = os.environ.get("OLLAMA_MODELS")
    return Path(custom).expanduser() if custom else Path.home() / ".ollama" / "models"


def free_disk(folder: Optional[Path] = None) -> Optional[int]:
    """Free bytes on the drive that holds the models folder, or the nearest folder above it that exists."""
    place = folder or models_folder()
    while not place.exists() and place.parent != place:
        place = place.parent
    try:
        return shutil.disk_usage(place).free
    except OSError:
        return None


def nvidia_gpus(run: Callable = subprocess.run) -> list[dict]:
    try:
        done = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                   stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=SMI_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return []
    if done.returncode != 0:
        return []
    gpus = []
    for line in done.stdout.splitlines():
        name, _, mib = line.rpartition(",")
        try:
            gpus.append({"name": name.strip(), "memory": int(float(mib.strip())) * 1024 * 1024})
        except ValueError:
            continue
    return gpus


def apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


def detect(run: Callable = subprocess.run, memory: Optional[int] = None, disk: Optional[int] = None,
           apple: Optional[bool] = None) -> dict:
    """{memory, disk_free, gpu: {name, memory, shared} | None}. Bytes throughout; None means unknown."""
    memory = total_memory() if memory is None else memory
    gpu = None
    if apple_silicon() if apple is None else apple:
        gpu = {"name": "Apple Silicon", "memory": memory, "shared": True}
    else:
        cards = nvidia_gpus(run)
        if cards:
            best = max(cards, key=lambda c: c["memory"])
            gpu = {"name": best["name"], "memory": best["memory"], "shared": False}
    return {"memory": memory, "disk_free": free_disk() if disk is None else disk, "gpu": gpu,
            "models_folder": str(models_folder())}


__all__ = ["detect", "free_disk", "models_folder", "nvidia_gpus", "GB"]
