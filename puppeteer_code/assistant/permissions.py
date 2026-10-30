"""
puppeteer_code/assistant/permissions.py
=======================================
Decides whether an action runs, needs the user's approval, or never runs.

File edits run without asking because Undo covers them. Commands do not have an
undo, so anything not known to be harmless asks first. These rules are a seatbelt,
not a sandbox: an approved command runs with the user's own permissions.
"""

from __future__ import annotations

import re
from typing import Iterable

ALLOW, ASK, REFUSE = "allow", "ask", "refuse"

_PROTECTED = [re.compile(p, re.I) for p in (
    r"(^|/)\.env($|\.|/)",
    r"(^|/)\.git($|/)",
    r"(^|/)\.pcode($|/)",
    r"\.(pem|key|p12|pfx|keystore)$",
    r"(^|/)id_(rsa|dsa|ecdsa|ed25519)($|\.)",
    r"(^|/)(keys?|secrets?|credentials?|passwords?)(\.\w+)?$",
)]

_REFUSED = [(re.compile(p, re.I), why) for p, why in (
    (r"\brm\s+(-\w+\s+)*(/|~|\$HOME|[a-z]:[\\/]?)(\s|$|\*)", "deletes outside the project"),
    (r"\brm\s+(-\w+\s+)*\.\.", "deletes outside the project"),
    (r"\b(rd|rmdir|del|erase)\b.*\s([a-z]:\\|\\\\|\.\.)", "deletes outside the project"),
    (r"remove-item\b.*\s([a-z]:\\|\.\.)", "deletes outside the project"),
    (r"\bformat\s+[a-z]:", "formats a drive"),
    (r"\b(mkfs|diskpart|fdisk)\b", "changes disks"),
    (r"\bdd\b.*\bof=/dev/", "writes to a disk"),
    (r"\b(shutdown|reboot|halt)\b", "shuts down the machine"),
    (r":\(\)\s*\{", "fork bomb"),
    (r"\b(curl|wget|iwr|invoke-webrequest)\b.*\|\s*(sudo\s+)?(ba|z|da)?sh\b", "runs code from the internet"),
    (r"\b(iex|invoke-expression)\b", "runs code from a string"),
    (r"\breg(\.exe)?\s+(add|delete)\b", "changes the Windows registry"),
    (r"\bgit\s+push\b.*(--force|-f\b)", "force-pushes"),
    (r"\bsudo\b", "runs as administrator"),
)]

_SAFE = [re.compile(p, re.I) for p in (
    r"^(ls|dir|pwd|cd|echo|whoami|date|tree|wc|which|where)(\s|$)",
    r"^git\s+(status|diff|log|show|branch)(\s|$)",
    r"^(python3?|py|node|npm|npx|pip|go|cargo|java|javac|dotnet|ruby|gcc|g\+\+|rustc)\s+(--?version|-v)$",
    r"^(python3?\s+-m\s+)?pytest(\s|$)",
    r"^(python3?\s+-m\s+)?unittest(\s|$)",
    r"^npm\s+(test|t|run\s+(test|build|lint|typecheck))(\s|$)",
    r"^(yarn|pnpm)\s+(test|build|lint)(\s|$)",
    r"^(go\s+(test|build|vet)|cargo\s+(test|build|check)|mvn\s+(-q\s+)?test|gradle\s+test|dotnet\s+(test|build))(\s|$)",
)]

_CHAINING = re.compile(r"&&|\|\||[;|`\n]|\$\(")
_REDIRECT = re.compile(r"(^|[^2&])>")


def is_protected(rel: str) -> bool:
    """Secrets and internals: no tool may read or write these, whatever the mode."""
    rel = re.sub(r"^(\./)+", "", rel.replace("\\", "/"))
    return any(p.search(rel) for p in _PROTECTED)


def classify_command(command: str, mode: str = "ask", extra_safe: Iterable[str] = ()) -> tuple[str, str]:
    """Returns (allow | ask | refuse, reason). `extra_safe` holds the detected test and build commands."""
    cmd = command.strip()
    if not cmd:
        return REFUSE, "empty command"
    for pattern, why in _REFUSED:
        if pattern.search(cmd):
            return REFUSE, why
    if re.search(r"(^|[\s/\\\"'])\.env\b", cmd):
        return REFUSE, "reads a secrets file"
    if mode == "auto":
        return ALLOW, "auto mode"
    if cmd in set(extra_safe):
        return ALLOW, "this project's test or build command"
    if _CHAINING.search(cmd) or _REDIRECT.search(cmd):
        return ASK, "chains commands or writes to a file"
    if any(p.search(cmd) for p in _SAFE):
        return ALLOW, "known safe command"
    return ASK, "not on the safe list"
