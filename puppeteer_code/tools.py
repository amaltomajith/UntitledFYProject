"""
puppeteer_code/tools.py
=======================
Local tools used by agents.  None of these call an LLM.

  parse_edit_blocks()  extract SEARCH/REPLACE blocks from model output
  apply_edits()        apply them atomically, with the path guard and the
                       test-integrity write guard
  grep()               regex search over workspace Python files

Edit format (asked of every editing agent):

    FILE: path/to/file.py
    <<<<<<< SEARCH
    exact lines currently in the file
    =======
    replacement lines
    >>>>>>> REPLACE

SEARCH must match exactly one place.  When an exact match fails we retry
ignoring trailing whitespace, then ignoring uniform indentation, because
small models often get whitespace slightly wrong; ambiguity is always an
error so we never edit the wrong spot.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Optional

from puppeteer_code.integrity import is_test_path

_FILE_RE = re.compile(r"^\s*(?:\*\*)?(?:FILE|File|file|PATH|Path)\s*:\s*(?:\*\*)?\s*`?([^`\s]+)`?\s*$")
_BARE_PATH_RE = re.compile(r"^\s*`?([\w./\\-]+\.\w+)`?\s*:?\s*$")
_SEARCH_RE = re.compile(r"^\s*<{5,9}\s*SEARCH\s*$")
_DIVIDER_RE = re.compile(r"^\s*={5,9}\s*$")
_REPLACE_RE = re.compile(r"^\s*>{5,9}\s*REPLACE\s*$")
_FENCE_RE = re.compile(r"^\s*```")


@dataclasses.dataclass
class EditBlock:
    file: str
    search: str
    replace: str


@dataclasses.dataclass
class ApplyResult:
    applied: list[str] = dataclasses.field(default_factory=list)   # "file: summary"
    errors: list[str] = dataclasses.field(default_factory=list)
    files: list[str] = dataclasses.field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.applied) and not self.errors


def parse_edit_blocks(text: str) -> list[EditBlock]:
    blocks: list[EditBlock] = []
    current_file: Optional[str] = None
    last_path_line: Optional[str] = None
    state = "idle"
    search: list[str] = []
    replace: list[str] = []

    for line in text.splitlines():
        if state == "idle":
            m = _FILE_RE.match(line)
            if m:
                current_file = m.group(1).strip()
                continue
            if _SEARCH_RE.match(line):
                state = "search"
                search, replace = [], []
                if current_file is None and last_path_line:
                    current_file = last_path_line
                continue
            if not _FENCE_RE.match(line) and line.strip():
                bm = _BARE_PATH_RE.match(line)
                last_path_line = bm.group(1) if bm else None
        elif state == "search":
            if _DIVIDER_RE.match(line):
                state = "replace"
            else:
                search.append(line)
        elif state == "replace":
            if _REPLACE_RE.match(line):
                if current_file:
                    blocks.append(EditBlock(current_file.replace("\\", "/"),
                                            "\n".join(search), "\n".join(replace)))
                state = "idle"
            else:
                replace.append(line)
    return blocks


# ═════════════════════════════════════════════════════════════════════════════
# matching
# ═════════════════════════════════════════════════════════════════════════════

def _find_line_matches(content_lines: list[str], search_lines: list[str], key) -> list[int]:
    n, m = len(content_lines), len(search_lines)
    if m == 0 or m > n:
        return []
    target = [key(s) for s in search_lines]
    keyed = [key(c) for c in content_lines]
    return [i for i in range(n - m + 1) if keyed[i:i + m] == target]


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def apply_block(content: str, block: EditBlock) -> tuple[Optional[str], str]:
    """
    Apply one block to `content` ("\n" line endings).
    Returns (new_content, note) or (None, error).
    """
    search, replace = block.search, block.replace
    if not search.strip():
        if content.strip():
            return None, "empty SEARCH is only allowed for a new or empty file"
        return replace + ("\n" if replace and not replace.endswith("\n") else ""), "created"

    count = content.count(search)
    if count == 1:
        return content.replace(search, replace, 1), "exact"
    if count > 1:
        return None, f"SEARCH text matches {count} places; include more surrounding lines"

    lines = content.split("\n")
    s_lines = search.split("\n")
    r_lines = replace.split("\n")
    # Drop leading/trailing blank lines from the search block for fuzzy matching.
    while s_lines and not s_lines[0].strip():
        s_lines.pop(0)
    while s_lines and not s_lines[-1].strip():
        s_lines.pop()
    if not s_lines:
        return None, "SEARCH block is blank"

    hits = _find_line_matches(lines, s_lines, key=str.rstrip)
    if len(hits) == 1:
        i = hits[0]
        return "\n".join(lines[:i] + r_lines + lines[i + len(s_lines):]), "trailing-whitespace tolerant"
    if len(hits) > 1:
        return None, f"SEARCH text matches {len(hits)} places; include more surrounding lines"

    hits = _find_line_matches(lines, s_lines, key=str.strip)
    if len(hits) == 1:
        i = hits[0]
        delta_file = _indent_of(lines[i])
        delta_search = _indent_of(s_lines[0])
        fixed = []
        for r in r_lines:
            if r.strip() and r.startswith(delta_search):
                fixed.append(delta_file + r[len(delta_search):])
            elif r.strip():
                fixed.append(delta_file + r.lstrip())
            else:
                fixed.append(r)
        return "\n".join(lines[:i] + fixed + lines[i + len(s_lines):]), "indentation tolerant"
    if len(hits) > 1:
        return None, f"SEARCH text matches {len(hits)} places; include more surrounding lines"

    first = s_lines[0].strip()[:60]
    return None, f"SEARCH text not found (first line: {first!r}); copy lines exactly from the snippet"


def apply_edits(ws, blocks: list[EditBlock], may_edit_tests: bool = False) -> ApplyResult:
    """
    Validate every block in memory, then write.  All-or-nothing: if any block
    fails, nothing is written and every error is reported back to the agent.
    """
    result = ApplyResult()
    if not blocks:
        result.errors.append("no SEARCH/REPLACE blocks found in the response")
        return result

    pending: dict[str, tuple[str, bool]] = {}   # file -> (content with \n, was_crlf)
    for b in blocks:
        try:
            ws.resolve(b.file)
        except PermissionError as e:
            result.errors.append(f"{b.file}: {e}")
            continue
        if is_test_path(b.file) and not may_edit_tests:
            result.errors.append(f"{b.file}: editing test files is not allowed for this agent "
                                 f"(fix the source code, not the tests)")
            continue
        if b.file not in pending:
            if ws.exists(b.file):
                raw = ws.read(b.file)
                pending[b.file] = (raw.replace("\r\n", "\n"), "\r\n" in raw)
            else:
                pending[b.file] = ("", False)
        content, crlf = pending[b.file]
        new, note = apply_block(content, b)
        if new is None:
            result.errors.append(f"{b.file}: {note}")
            continue
        pending[b.file] = (new, crlf)
        result.applied.append(f"{b.file} ({note})")

    if result.errors:
        result.applied.clear()
        return result

    for file, (content, crlf) in pending.items():
        ws.write(file, content.replace("\n", "\r\n") if crlf else content)
        result.files.append(file)
    return result


def grep(ws, pattern: str, max_hits: int = 30) -> list[str]:
    try:
        rx = re.compile(pattern)
    except re.error:
        rx = re.compile(re.escape(pattern))
    hits = []
    for rel in ws.python_files():
        for i, line in enumerate(ws.read(rel).splitlines(), 1):
            if rx.search(line):
                hits.append(f"{rel}:{i}: {line.strip()[:120]}")
                if len(hits) >= max_hits:
                    return hits
    return hits
