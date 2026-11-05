"""
Answer checkers. Every method's reply goes through the same function, so no method gets a kinder parser.

    check(kind, reply, gold) -> bool

kind:
  number  GSM8K, GSM-Hard   integer gold: exact; non-integer gold (GSM-Hard): within 0.01
  letter  MMLU-Pro          option letter A-J
  exact   BBH               normalised exact match; "(B)" and "B" are the same option
  gaia    GAIA              quasi-exact match, after the official GAIA scorer

The answer is the text after the last "FINAL ANSWER:" in the reply. A reply with no tag falls back
to the whole text: the last number; a bare letter or an "answer is X" phrase; for BBH a short one-line reply.
The same rules apply to every method.
"""

from __future__ import annotations

import re
import string

FINAL = re.compile(r"final\s*answer\s*[:：]", re.I)
THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?|-?\.\d+")
FRACTION = re.compile(r"(-?\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)")


def strip_think(text: str) -> str:
    """Remove reasoning blocks some models put in the reply text."""
    text = THINK.sub("", text or "")
    if "</think>" in text.lower():                      # an opening tag that was cut off upstream
        text = text[text.lower().rfind("</think>") + len("</think>"):]
    return text


def _clean(segment: str) -> str:
    s = segment.replace("**", "").replace("`", "")
    s = s.replace("−", "-").replace("–", "-").replace(" ", " ")   # Unicode minus, en dash, nbsp
    s = re.sub(r"(?<=\d)[   ](?=\d{3}\b)", "", s)                       # 1 234 567 -> 1234567
    s = re.sub(r"\\boxed\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\text\{([^{}]*)\}", r"\1", s)
    s = s.replace("\\$", "$").replace("$", "")
    return s.strip()


def final_segment(reply: str) -> tuple[str, bool]:
    """(text after the last FINAL ANSWER:, whether that tag was present)."""
    text = strip_think(reply)
    matches = list(FINAL.finditer(text))
    if not matches:
        return _clean(text), False
    return _clean(text[matches[-1].end():]), True


def _first_line(s: str) -> str:
    for line in s.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _to_float(token: str) -> float | None:
    try:
        return float(token.replace(",", ""))
    except ValueError:
        return None


def parse_number(segment: str, tagged: bool) -> float | None:
    if tagged:
        segment = _first_line(segment)
        frac = FRACTION.search(segment)
        nums = NUM.findall(segment)
        if frac and (not nums or segment.find(frac.group(0)) <= segment.find(nums[0])):
            a, b = float(frac.group(1)), float(frac.group(2))
            return a / b if b else None
        return _to_float(nums[0]) if nums else None
    nums = NUM.findall(segment)
    return _to_float(nums[-1]) if nums else None


def format_number(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else repr(float(x))


def check_number(reply: str, gold: str) -> bool:
    g = _to_float(str(gold))
    seg, tagged = final_segment(reply)
    p = parse_number(seg, tagged)
    if g is None or p is None:
        return False
    if float(g).is_integer():
        return abs(p - g) <= 1e-6
    return abs(p - g) <= max(0.01, 1e-6 * abs(g))     # a non-integer gold may be rounded to 2 decimals


def parse_letter(segment: str, tagged: bool, letters: str = "A-J") -> str | None:
    if tagged:
        line = _first_line(segment)
        m = re.match(rf"^\W*(?:(?i:option)\s*)?\(?([{letters}])\)?(?![A-Za-z'])", line)
        if m:
            return m.group(1).upper()
        m = re.search(rf"\(([{letters}])\)", line)
        return m.group(1).upper() if m else None
    bare = re.fullmatch(rf"\W*\(?([{letters}])\)?\W*", segment.strip())     # the reply is only a letter
    if bare:
        return bare.group(1)
    found = re.findall(rf"(?i:answer)\s*(?:is|:)?\s*(?:(?i:option)\s*)?\(?([{letters}])\)?(?![A-Za-z'])", segment)
    return found[-1].upper() if found else None


def check_letter(reply: str, gold: str) -> bool:
    seg, tagged = final_segment(reply)
    return parse_letter(seg, tagged) == str(gold).strip().upper()


OPTION_GOLD = re.compile(r"^\(([A-R])\)$")


def _norm(s: str) -> str:
    s = s.strip().strip('"').strip("'").strip()
    s = re.sub(r"\s+", " ", s)
    return s.rstrip(".").strip().lower()


def check_exact(reply: str, gold: str) -> bool:
    seg, tagged = final_segment(reply)
    g = str(gold).strip()
    m = OPTION_GOLD.match(g)
    if m:
        return parse_letter(seg, tagged, "A-R") == m.group(1)
    if not tagged:
        found = re.findall(r"answer\s*(?:is|:)\s*(.+)", seg, re.I)
        if found:
            seg = found[-1]
        elif "\n" in seg.strip() or len(seg.strip()) > 40:     # only a short one-line reply counts as the answer
            return False
    line = _first_line(seg)
    if _to_float(g) is not None:
        nums = NUM.findall(line)
        return bool(nums) and _to_float(nums[0]) == _to_float(g)
    pred = _norm(line)
    if pred.startswith("(") and pred.endswith(")") and not g.startswith("("):
        pred = pred[1:-1].strip()                     # "(True)" for a gold of "True"
    return pred == _norm(g)


# ── GAIA quasi-exact match (after the official scorer) ───────────────────────

def _gaia_norm_str(s: str, remove_punct: bool = True) -> str:
    s = re.sub(r"\s", "", s)
    if remove_punct:
        s = s.translate(str.maketrans("", "", string.punctuation))
    return s.lower()


def _gaia_num(s: str) -> float | None:
    for ch in ("$", "%", ","):
        s = s.replace(ch, "")
    try:
        return float(s)
    except ValueError:
        return None


def check_gaia(reply: str, gold: str) -> bool:
    seg, tagged = final_segment(reply)
    pred = _first_line(seg) if tagged else _first_line(seg[-300:])
    g = str(gold)
    if _gaia_num(g) is not None:
        p = _gaia_num(pred.strip())
        return p is not None and p == _gaia_num(g)
    if any(c in g for c in ",;"):
        gs = re.split(r"[,;]", g)
        ps = re.split(r"[,;]", pred)
        if len(gs) != len(ps):
            return False
        for a, b in zip(gs, ps):
            if _gaia_num(a) is not None:
                if _gaia_num(b.strip()) != _gaia_num(a):
                    return False
            elif _gaia_norm_str(a, False) != _gaia_norm_str(b, False):
                return False
        return True
    return _gaia_norm_str(pred) == _gaia_norm_str(g)


CHECKERS = {"number": check_number, "letter": check_letter, "exact": check_exact, "gaia": check_gaia}


def check(kind: str, reply: str, gold: str) -> bool:
    return CHECKERS[kind](reply or "", gold)


def wrong_answer(task: dict) -> str:
    """A plausible wrong answer, used by datasets.py --check to prove a checker can say no."""
    gold, kind = str(task["gold"]), task["kind"]
    if kind == "number" or (kind in ("exact", "gaia") and _to_float(gold) is not None):
        return format_number((_to_float(gold) or 0) + 7)
    if kind == "letter":
        return "B" if gold.upper() != "B" else "C"
    m = OPTION_GOLD.match(gold)
    if m:
        return "(A)" if m.group(1) != "A" else "(B)"
    swaps = {"true": "False", "false": "True", "yes": "No", "no": "Yes", "valid": "invalid", "invalid": "valid"}
    return swaps.get(gold.lower(), gold + " extra")
