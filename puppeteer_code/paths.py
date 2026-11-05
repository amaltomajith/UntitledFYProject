"""
Where things live, for a source checkout and for an installed copy.

A source checkout (this repository) keeps the built site in web/out and runs in runs/. An installed copy
(`compactcode` from the installer) has the site bundled inside the package as puppeteer_code/site/ and keeps
runs under ~/.pcode/runs, because site-packages is not the place for user data.
"""

from __future__ import annotations

from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parent


def is_source_checkout(root: Path = ROOT) -> bool:
    """True when running from a clone of the repository rather than an installed wheel."""
    return (root / "pyproject.toml").exists() and (root / "puppeteer_code").is_dir() and (root / "core").is_dir()


def site_dir(root: Path = ROOT, package: Path = PACKAGE) -> Path:
    """The built site: web/out in a checkout when it has been built, otherwise the copy bundled in the package."""
    checkout = root / "web" / "out"
    if (checkout / "ide.html").exists():
        return checkout
    return package / "site"


def site_ready(root: Path = ROOT, package: Path = PACKAGE) -> bool:
    return (site_dir(root, package) / "ide.html").exists()


def runs_dir(root: Path = ROOT, home: Path | None = None) -> Path:
    if is_source_checkout(root):
        return root / "runs"
    return (home or Path.home()) / ".pcode" / "runs"
