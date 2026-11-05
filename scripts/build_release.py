"""
Build the release files: a wheel with the IDE page bundled, SHA256SUMS, and the two installers.

    cd web && npm ci && npm run build && cd ..
    python scripts/build_release.py            # -> dist/release/

The wheel carries web/out as puppeteer_code/site/, so an installed `compactcode` can show the IDE without Node.
puppeteer_code/site/ is gitignored: it exists only while building.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE_OUT = ROOT / "web" / "out"
BUNDLE = ROOT / "puppeteer_code" / "site"
SKIP = {".vercel"}                      # the Vercel link of the owner's machine, never shipped


def bundle_site() -> int:
    if not (SITE_OUT / "ide.html").exists():
        raise SystemExit("web/out is not built. Run: cd web && npm ci && npm run build")
    if BUNDLE.exists():
        shutil.rmtree(BUNDLE)
    shutil.copytree(SITE_OUT, BUNDLE, ignore=lambda folder, names: [n for n in names if n in SKIP])
    return sum(1 for p in BUNDLE.rglob("*") if p.is_file())


def build_wheel(out: Path) -> Path:
    for old in out.glob("compactcode-*.whl"):
        old.unlink()
    for stale in (ROOT / "build", ROOT / "compactcode.egg-info"):
        shutil.rmtree(stale, ignore_errors=True)
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(out), str(ROOT)],
                   check=True, stdin=subprocess.DEVNULL)
    wheels = list(out.glob("compactcode-*-py3-none-any.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected one wheel in {out}, found {[w.name for w in wheels]}")
    return wheels[0]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "dist" / "release"))
    ap.add_argument("--keep-site", action="store_true", help="leave puppeteer_code/site in place afterwards")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    files = bundle_site()
    try:
        wheel = build_wheel(out)
    finally:
        if not a.keep_site:
            shutil.rmtree(BUNDLE, ignore_errors=True)
    for name in ("install.sh", "install.ps1"):
        shutil.copy2(ROOT / "web" / "public" / name, out / name)
    assets = [wheel, out / "install.sh", out / "install.ps1"]
    (out / "SHA256SUMS").write_text("".join(f"{sha256(p)}  {p.name}\n" for p in assets), encoding="utf-8", newline="\n")
    print(f"bundled {files} site files")
    for p in [*assets, out / "SHA256SUMS"]:
        print(f"{p.stat().st_size:>10,}  {p.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
