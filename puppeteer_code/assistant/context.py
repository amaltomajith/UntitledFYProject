"""
puppeteer_code/assistant/context.py
===================================
Bounded prompts. Every agent starts from a fresh, capped view of the state, not a
growing transcript, and a long tool loop drops its oldest output. Step 10 of a
request costs no more context than step 2.
"""

from __future__ import annotations

import json

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


def file_map(toolbox) -> str:
    files = []
    for rel in toolbox._walk(toolbox.root):
        files.append(rel)
        if len(files) > MAP_FILES:
            files[-1] = "(more files not shown)"
            break
    return "\n".join(files) or "(the folder is empty)"


def relevant_code(toolbox, request: str) -> str:
    """Files sharing the most words with the request. Free: no model is involved."""
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
    parts.append("Files:\n" + file_map(toolbox))
    code = relevant_code(toolbox, st.request)
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
