"""
puppeteer_code/assistant/setup.py
=================================
API keys for people who are not running from this repo. They live in
~/.pcode/.env, and nothing here ever returns a key's value.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from puppeteer_code.assistant.snapshots import pcode_home, project_folder

# In preference order: the first one with a key is used.
PROVIDER_KEYS = {"nebius": "NEBIUS_API_KEY", "groq": "GROQ_API_KEY"}
KEY_HELP = {"nebius": "https://tokenfactory.nebius.com", "groq": "https://console.groq.com/keys"}


def _key_file(home: Optional[Path]) -> Path:
    return Path(home or pcode_home()) / ".env"


def load_keys(home: Optional[Path] = None) -> None:
    """Read saved keys into the environment. A key already set in the environment wins."""
    path = _key_file(home)
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() in PROVIDER_KEYS.values() and value.strip():
            os.environ.setdefault(name.strip(), value.strip())


def save_key(provider: str, value: str, home: Optional[Path] = None) -> None:
    name = PROVIDER_KEYS[provider]
    value = value.strip()
    if not value or any(c.isspace() for c in value):
        raise ValueError("that does not look like an API key")
    path = _key_file(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    keep = [] if not path.exists() else [
        line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith(name + "=")]
    path.write_text("\n".join([*keep, f"{name}={value}"]) + "\n", encoding="utf-8")
    path.chmod(0o600)
    os.environ[name] = value


def key_status() -> dict[str, bool]:
    return {provider: bool(os.environ.get(name)) for provider, name in PROVIDER_KEYS.items()}


def pick_provider() -> Optional[str]:
    return next((p for p, ready in key_status().items() if ready), None)


def _settings_file(project: str | Path, home: Optional[Path]) -> Path:
    return project_folder(project, home) / "settings.json"


def is_private(project: str | Path, home: Optional[Path] = None) -> bool:
    """Whether Private was left on for this project."""
    try:
        return json.loads(_settings_file(project, home).read_text(encoding="utf-8")).get("private") is True
    except (OSError, ValueError, AttributeError):
        return False


def remember_private(project: str | Path, on: bool, home: Optional[Path] = None) -> None:
    path = _settings_file(project, home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"private": on}), encoding="utf-8")
