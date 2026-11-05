"""
puppeteer_code/privacy.py
=========================
What keeps a Private session's prompts on this computer. These checks run inside
the client, before any bytes go out, because `/model` and the IDE can set a model
at any time. A failed check raises: Private never falls back to anything.

Cloud names are refused first and by name alone, because Ollama answers `/api/show`
for a cloud model by asking ollama.com, so even looking one up would leave the machine.
"""

from __future__ import annotations

import re
from typing import Callable
from urllib.parse import urlsplit

LOOPBACK = "127.0.0.1"
_CLOUD_NAME = re.compile(r"(^|[:-])cloud$", re.I)


class PrivacyError(RuntimeError):
    """Something would have left this computer, so it did not run."""


# Named on every status screen, because the claim does not cover them.
OUTSIDE = ("your own terminals and ! commands", "Git or npm hooks that a command left behind, when you run them",
           "code you run yourself later", "Ollama's own app, which checks for updates")


def private_claim(runner) -> str:
    """What Private may honestly say. Never "no data leaves": the user's own runs are outside this."""
    if runner.contained:
        return "Model on this computer. Commands have no network."
    return "Model on this computer. Commands are not sandboxed."


def is_cloud_name(model: str) -> bool:
    return bool(_CLOUD_NAME.search(model.strip()))


def full_name(model: str) -> str:
    """Ollama lists `name:latest` for a model asked for as `name`."""
    return model if ":" in model else f"{model}:latest"


def check_address(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname != LOOPBACK:
        raise PrivacyError(f"Private mode only talks to {LOOPBACK}, not {parts.hostname or url}.")


class ModelGuard:
    """Decides whether a model is really local. `tags` and `show` read the local server."""

    def __init__(self, tags: Callable[[], list[str]], show: Callable[[str], dict]):
        self.tags = tags
        self.show = show
        self.known: dict[str, dict] = {}

    def check(self, model: str) -> dict:
        """Returns the model's details, or raises. A model that passed is not looked up again."""
        if is_cloud_name(model):
            raise PrivacyError(f"{model} is a cloud model. Private mode only runs models on this computer.")
        name = full_name(model)
        if name not in self.known:
            if name not in self.tags():
                raise PrivacyError(f"{model} is not installed. Run: ollama pull {model}")
            info = self.show(name)
            if info.get("remote_host") or info.get("remote_model"):
                raise PrivacyError(f"{model} runs on a remote server, so Private mode will not use it.")
            self.known[name] = info
        return self.known[name]


__all__ = ["LOOPBACK", "OUTSIDE", "ModelGuard", "PrivacyError", "check_address", "full_name", "is_cloud_name", "private_claim"]
