"""
puppeteer_code/assistant/snapshots.py
=====================================
Undo for a folder the assistant edits directly.

A shadow git repository lives in ~/.pcode/, outside the project, so a project
needs no git of its own and the user's real .git is never touched. Restoring only
changes files that differ from the snapshot, and only those listed when a list is
given, so the user's unrelated work survives.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Iterable, Optional

from puppeteer_code.workspace import GIT_IDENTITY, IGNORE_DIRS

# dist, build and env stay in: the assistant can write there, and Undo has to cover it.
SKIP = sorted(IGNORE_DIRS - {"dist", "build", "env"}) + [".env", ".env.*", "*.pem", "*.key", "*.pyc"]


def pcode_home() -> Path:
    return Path.home() / ".pcode"


class Snapshots:
    def __init__(self, project: str | Path, home: Optional[Path] = None):
        self.project = Path(project).resolve()
        key = hashlib.sha256(str(self.project).lower().encode("utf-8")).hexdigest()[:16]
        self.git_dir = Path(home or pcode_home()) / "projects" / key / "git"
        if not (self.git_dir / "HEAD").exists():
            self.git_dir.parent.mkdir(parents=True, exist_ok=True)
            self._git("init", "--quiet", "--initial-branch=main")
        exclude = self.git_dir / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text("\n".join(SKIP) + "\n", encoding="utf-8")
        # Beats any .gitattributes in the project, so Undo restores exact bytes.
        (exclude.parent / "attributes").write_text("* -text -filter -ident\n", encoding="utf-8")

    def _git(self, *args: str, check: bool = True, text: bool = True) -> str:
        cmd = ["git", f"--git-dir={self.git_dir}", f"--work-tree={self.project}",
               "-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "-c", "core.quotepath=off",
               *GIT_IDENTITY, *args]
        proc = subprocess.run(cmd, cwd=self.project, stdin=subprocess.DEVNULL,
                              capture_output=True, text=text, encoding="utf-8" if text else None,
                              errors="replace" if text else None)
        if check and proc.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed: {proc.stderr.strip()[:300]}")
        return proc.stdout

    def snapshot(self, label: str) -> str:
        self._git("add", "-A")
        self._git("commit", "--quiet", "--allow-empty", "--no-verify", "-m", label)
        return self._git("rev-parse", "HEAD").strip()

    def changed_since(self, sha: str) -> list[str]:
        self._git("add", "-A")
        out = self._git("diff", "--cached", "--name-only", "--no-renames", "-z", sha)
        return sorted(f for f in out.split("\0") if f)

    def diff(self, sha: str, files: Optional[Iterable[str]] = None) -> str:
        self._git("add", "-A")
        paths = list(files) if files is not None else []
        if files is not None and not paths:
            return ""
        return self._git("diff", "--cached", "--no-renames", sha, *(["--", *paths] if paths else []))

    def read(self, sha: str, rel: str) -> Optional[str]:
        """A file's text at `sha`, or None if it did not exist or is not text."""
        cmd = ["git", f"--git-dir={self.git_dir}", "show", f"{sha}:{rel}"]
        proc = subprocess.run(cmd, cwd=self.project, stdin=subprocess.DEVNULL, capture_output=True)
        try:
            return proc.stdout.decode("utf-8") if proc.returncode == 0 else None
        except UnicodeDecodeError:
            return None

    def restore(self, sha: str, files: Optional[Iterable[str]] = None) -> list[str]:
        """Put files back as they were at `sha`. Files that did not exist then are removed."""
        changed = self.changed_since(sha)
        targets = [f for f in changed if files is None or f in set(files)]
        if not targets:
            return []
        existed = set(self._git("ls-tree", "-r", "--name-only", "-z", sha).split("\0"))
        for rel in targets:
            path = self.project / rel
            if rel in existed:
                data = self._git_bytes("show", f"{sha}:{rel}")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            else:
                path.unlink(missing_ok=True)
                self._prune(path.parent)
        self._git("add", "-A")
        return targets

    def _git_bytes(self, *args: str) -> bytes:
        cmd = ["git", f"--git-dir={self.git_dir}", f"--work-tree={self.project}", *args]
        return subprocess.run(cmd, cwd=self.project, stdin=subprocess.DEVNULL,
                              capture_output=True, check=True).stdout

    def _prune(self, folder: Path) -> None:
        while folder != self.project and self.project in folder.parents:
            try:
                folder.rmdir()
            except OSError:
                return
            folder = folder.parent
