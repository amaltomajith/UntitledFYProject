"""
puppeteer_code/tasks.py
=======================
Loader for PCodeBench-Mini tasks (benchmarks/code_tasks/tasks/<id>/task.json).
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Optional

DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "benchmarks" / "code_tasks" / "tasks"


@dataclasses.dataclass
class BenchTask:
    id: str
    dir: Path
    task: str
    module: str
    split: str
    difficulty: str
    n_bugs: int
    solution: str

    @property
    def repo(self) -> Path:
        return self.dir / "repo"

    @property
    def holdout(self) -> Path:
        return self.dir / "holdout"


def load_tasks(root: Optional[str | Path] = None, split: Optional[str] = None,
               ids: Optional[list[str]] = None, limit: Optional[int] = None) -> list[BenchTask]:
    root = Path(root or DEFAULT_ROOT)
    if not root.exists():
        raise FileNotFoundError(f"{root} not found; run benchmarks/code_tasks/build_tasks.py first")
    tasks = []
    for meta_path in sorted(root.glob("*/task.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        t = BenchTask(id=meta["id"], dir=meta_path.parent, task=meta["task"], module=meta["module"],
                      split=meta["split"], difficulty=meta["difficulty"], n_bugs=meta["n_bugs"],
                      solution=meta["solution"])
        if split and split != "all" and t.split != split:
            continue
        if ids and t.id not in ids:
            continue
        tasks.append(t)
    return tasks[:limit] if limit else tasks
