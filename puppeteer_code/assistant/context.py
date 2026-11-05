"""
puppeteer_code/assistant/context.py
===================================
Bounded prompts. Every agent starts from a fresh, capped view of the state, not a
growing transcript, and a long tool loop drops its oldest output. Step 10 of a
request costs no more context than step 2.
"""

from __future__ import annotations

import json
import posixpath
from typing import Optional

from puppeteer_code.assistant.codemap import parse
from puppeteer_code.index import tokenize

MAX_PROMPT_CHARS = 24000
KEEP_RECENT = 3
MAP_FILES = 60
RELEVANT_FILES = 2
SNIPPET_CHARS = 1500
FAILURE_CHARS = 2500
TURN_CHARS = 600
RECENT_TURNS = 6
SUMMARY_CHARS = 1200
REMOVED = "(older output removed to keep this short)"
FOLDED = "(read earlier: "
FOLD_SYMBOLS = 8
WRITERS = ("write_file", "edit_file")


def file_map(toolbox) -> str:
    files = []
    for rel in toolbox._walk(toolbox.root):
        files.append(rel)
        if len(files) > MAP_FILES:
            files[-1] = "(more files not shown)"
            break
    return "\n".join(files) or "(the folder is empty)"


def _picked(toolbox, files: list[str]) -> str:
    shown = []
    for rel in files:
        try:
            shown.append(f"--- {rel}\n{(toolbox.root / rel).read_text(encoding='utf-8')[:SNIPPET_CHARS]}")
        except (OSError, UnicodeDecodeError):
            continue
    return "\n\n".join(shown)


def relevant_code(toolbox, request: str, files: Optional[list[str]] = None) -> str:
    """Files sharing the most words with the request. Free: no model is involved.

    `files` is the memory graph's pick, which saves reading the whole project.
    """
    picked = _picked(toolbox, files) if files else ""
    if picked:
        return picked
    words = set(tokenize(request))
    scored = []
    for rel in toolbox._walk(toolbox.root):
        path = toolbox.root / rel
        try:
            if path.stat().st_size > 200_000:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        score = 3 * len(words & set(tokenize(rel))) + len(words & set(tokenize(text)))
        if score:
            scored.append((score, rel, text))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return "\n\n".join(f"--- {rel}\n{text[:SNIPPET_CHARS]}" for _, rel, text in scored[:RELEVANT_FILES])


def conversation(history: list[dict]) -> list[dict]:
    """Recent turns, plus one line for each older exchange."""
    recent = [{"role": m["role"], "content": m["content"][:TURN_CHARS]} for m in history[-RECENT_TURNS:]]
    older = history[:-RECENT_TURNS]
    if not older:
        return recent
    lines = [f"{m['role']}: {m['content'][:120]}" for m in older]
    summary = "\n".join(lines)[-SUMMARY_CHARS:]
    return [{"role": "user", "content": f"Earlier in this conversation:\n{summary}"},
            {"role": "assistant", "content": "Understood."}, *recent]


def task_for(st, toolbox, instruction: str) -> str:
    parts = [f"Request: {st.request}"]
    if st.plan:
        parts.append("Plan:\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(st.plan, 1)))
    if st.memory:
        parts.append(st.memory)
    parts.append("Files:\n" + file_map(toolbox))
    code = relevant_code(toolbox, st.request, st.related)
    if code:
        parts.append("Possibly relevant code:\n" + code)
    if st.notes:
        parts.append("So far:\n" + "\n".join(st.notes[-5:]))
    if st.failure:
        parts.append("Latest check output:\n" + st.failure[-FAILURE_CHARS:])
    if st.review:
        parts.append("Reviewer's concerns:\n" + st.review[:800])
    parts.append(instruction)
    return "\n\n".join(parts)


def _size(message: dict) -> int:
    calls = message.get("tool_calls") or []
    return len(message.get("content") or "") + sum(len(c["function"]["arguments"]) for c in calls)


def _shrink_call(call: dict) -> None:
    try:
        args = json.loads(call["function"]["arguments"])
    except ValueError:
        args = {}
    short = {k: (v if len(str(v)) <= 80 else "(removed)") for k, v in args.items()}
    call["function"]["arguments"] = json.dumps(short)


def path_of(arguments: str) -> str:
    try:
        path = json.loads(arguments or "{}").get("path", "")
    except (ValueError, AttributeError):
        return ""
    return posixpath.normpath(str(path).replace("\\", "/")) if path else ""


def _outline(path: str, text: str) -> str:
    """Symbols of the text being folded, so the line numbers are those of what was read."""
    names = [f"{s.qualname} {s.lineno}-{s.end_lineno}" for s in parse(path, text).symbols[:FOLD_SYMBOLS]]
    return f" Symbols: {', '.join(names)}." if names else ""


def fold(messages: list[dict]) -> list[dict]:
    """Replace read_file results that can no longer help, in place. Returns what was folded.

    Kept: the last few tool results, errors, and the latest read of a file with no edit
    after it, because edit_file needs that exact text and folding it would cost a re-read.
    So what goes is a read made stale by an edit, or repeated by a later read.
    """
    calls = {c["id"]: c["function"] for m in messages for c in m.get("tool_calls") or []}
    results = [i for i, m in enumerate(messages) if m["role"] == "tool"]
    latest: dict[str, int] = {}
    edited: dict[str, int] = {}
    for i in results:
        call = calls.get(messages[i].get("tool_call_id"))
        if call is None:
            continue
        path = path_of(call["arguments"])
        if call["name"] == "read_file" and not messages[i]["content"].startswith("ERROR"):
            latest[path] = i
        elif call["name"] in WRITERS:
            edited[path] = i
    folded = []
    for i in results[:-KEEP_RECENT]:
        message = messages[i]
        call = calls.get(message.get("tool_call_id"))
        text = message["content"]
        if (call is None or call["name"] != "read_file" or text.startswith(("ERROR", FOLDED)) or text == REMOVED):
            continue
        path = path_of(call["arguments"])
        if latest.get(path) == i and edited.get(path, -1) < i:
            continue
        short = (f"{FOLDED}{path}, {len(text)} characters.{_outline(path, text)} "
                 "Read it again if you need exact text.)")
        if len(short) < len(text):
            message["content"] = short
            folded.append({"key": (call["name"], call["arguments"]), "path": path, "chars": len(text) - len(short)})
    return folded


def saved_chars(messages: list[dict], removed: int, memory: int, limit: int = MAX_PROMPT_CHARS) -> int:
    """What folding saved on this prompt, net of the memory it carries.

    `trim` would have cut a long prompt to `limit` anyway, so only the part below the
    limit counts. `removed` is already net of re-reads, and can be negative.
    """
    size = sum(_size(m) for m in messages)
    gain = max(0, min(size + removed, limit) - size) if removed > 0 else removed
    return gain - memory


def trim(messages: list[dict], limit: int = MAX_PROMPT_CHARS) -> list[dict]:
    """Shrink the oldest tool traffic in place until the prompt fits."""
    tool_turns = [i for i, m in enumerate(messages) if m["role"] == "tool"]
    for i in tool_turns[:-KEEP_RECENT] if len(tool_turns) > KEEP_RECENT else []:
        if sum(_size(m) for m in messages) <= limit:
            break
        messages[i]["content"] = REMOVED
    if sum(_size(m) for m in messages) > limit:
        for message in messages[:-(2 * KEEP_RECENT)]:
            for call in message.get("tool_calls") or []:
                _shrink_call(call)
    return messages
