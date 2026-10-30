"""
puppeteer_code/index.py
=======================
Zero-token, AST-based code intelligence for RepoScoutAgent.

Builds a symbol table for every Python file in the workspace using only the
standard library `ast` module:

  Symbol   qualified name, kind, file, line range, signature, first docstring
           line, and the names it calls

and answers the questions a coding agent needs before it edits:

  * which symbol contains file:line (maps traceback frames to code)
  * who calls this symbol (the "blast radius" of an edit)
  * which symbols are most relevant to a task + failing tests (ranking)
  * the exact source of a symbol, padded with surrounding lines, so that
    SEARCH/REPLACE blocks copied from it match the file verbatim
"""

from __future__ import annotations

import ast
import dataclasses
import math
import re
from collections import defaultdict
from typing import Iterable, Optional

from puppeteer_code.integrity import is_test_path

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "it", "be", "with",
    "when", "that", "this", "should", "fix", "bug", "fails", "failing", "test", "tests",
    "return", "returns", "not", "does", "do", "def", "self", "none", "true", "false",
    # general English function words (a subset of the NLTK list) and file noise
    "are", "was", "were", "been", "being", "has", "have", "had", "but", "if", "as", "at", "by",
    "from", "into", "out", "up", "so", "than", "then", "there", "these", "those", "its", "can",
    "could", "would", "will", "all", "any", "some", "no", "only", "also", "one", "other",
    "another", "each", "every", "same", "such", "very", "too", "more", "most", "py", "code",
    "pass", "passes", "wrong", "make", "makes",
}


def _split_identifier(name: str) -> list[str]:
    """parse_date -> [parse, date]; parseDate -> [parse, date]."""
    parts = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower().split("_")
    return [p for p in parts if p]


def tokenize(text: str) -> list[str]:
    out = []
    for w in _WORD_RE.findall(text):
        for p in _split_identifier(w):
            if len(p) > 1 and p not in _STOPWORDS:
                out.append(p)
    return out


@dataclasses.dataclass
class Symbol:
    qualname: str           # e.g. "Interval.overlaps" or "parse_date"
    name: str               # short name, e.g. "overlaps"
    kind: str               # function | method | class | variable (module-level)
    file: str               # posix path relative to workspace root
    lineno: int
    end_lineno: int
    signature: str
    doc: str
    calls: set[str] = dataclasses.field(default_factory=set)
    refs: set[str] = dataclasses.field(default_factory=set)    # names read (not only called)

    @property
    def key(self) -> str:
        return f"{self.file}::{self.qualname}"

    @property
    def is_test(self) -> bool:
        return is_test_path(self.file) or self.name.startswith("test_")

    def header(self) -> str:
        return f"{self.file}:{self.lineno}-{self.end_lineno}  {self.kind} {self.qualname}{self.signature}"


class _Visitor(ast.NodeVisitor):
    def __init__(self, file: str):
        self.file = file
        self.stack: list[str] = []
        self.symbols: list[Symbol] = []

    def _calls(self, node: ast.AST) -> set[str]:
        names = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Name):
                    names.add(f.id)
                elif isinstance(f, ast.Attribute):
                    names.add(f.attr)
        return names

    @staticmethod
    def _refs(node: ast.AST) -> set[str]:
        return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}

    def visit_Module(self, node: ast.Module):
        # Module-level assignments (constants, dispatch tables such as
        # OPS = {"+": lambda ...}) hold logic too; index them as variables.
        for stmt in node.body:
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                for t in targets:
                    if isinstance(t, ast.Name):
                        self.symbols.append(Symbol(
                            qualname=t.id, name=t.id, kind="variable", file=self.file,
                            lineno=stmt.lineno, end_lineno=getattr(stmt, "end_lineno", stmt.lineno),
                            signature="", doc="", calls=self._calls(stmt), refs=self._refs(stmt),
                        ))
        self.generic_visit(node)

    def _add(self, node, kind: str, signature: str):
        qual = ".".join([*self.stack, node.name])
        doc = (ast.get_docstring(node) or "").strip().splitlines()
        self.symbols.append(Symbol(
            qualname=qual, name=node.name, kind=kind, file=self.file,
            lineno=node.lineno, end_lineno=getattr(node, "end_lineno", node.lineno),
            signature=signature, doc=doc[0][:160] if doc else "",
            calls=self._calls(node) if kind != "class" else set(),
            refs=self._refs(node) if kind != "class" else set(),
        ))

    def visit_ClassDef(self, node: ast.ClassDef):
        bases = ", ".join(ast.unparse(b) for b in node.bases)
        self._add(node, "class", f"({bases})" if bases else "")
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _visit_func(self, node):
        kind = "method" if self.stack else "function"
        try:
            sig = f"({ast.unparse(node.args)})"
        except Exception:
            sig = "(...)"
        if node.returns is not None:
            sig += f" -> {ast.unparse(node.returns)}"
        self._add(node, kind, sig)
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _visit_func
    visit_AsyncFunctionDef = _visit_func


class SymbolIndex:
    """Symbol table over a workspace.  Rebuild after edits with `build()`."""

    def __init__(self, workspace):
        self.ws = workspace
        self.symbols: dict[str, Symbol] = {}
        self.by_name: dict[str, list[Symbol]] = defaultdict(list)
        self.by_file: dict[str, list[Symbol]] = defaultdict(list)
        self.parse_errors: list[str] = []

    def build(self) -> "SymbolIndex":
        self.symbols.clear()
        self.by_name.clear()
        self.by_file.clear()
        self.parse_errors.clear()
        for rel in self.ws.python_files():
            try:
                tree = ast.parse(self.ws.read(rel), filename=rel)
            except SyntaxError as e:
                self.parse_errors.append(f"{rel}:{e.lineno}: {e.msg}")
                continue
            v = _Visitor(rel)
            v.visit(tree)
            for s in v.symbols:
                self.symbols[s.key] = s
                self.by_name[s.name].append(s)
                self.by_file[rel].append(s)
        return self

    # ── lookups ──────────────────────────────────────────────────────────────

    def at(self, file: str, line: int) -> Optional[Symbol]:
        """Innermost symbol containing file:line (file may be absolute or relative)."""
        file = file.replace("\\", "/")
        candidates = self.by_file.get(file)
        if candidates is None:
            # traceback paths can be absolute or prefixed; match by suffix
            for f, syms in self.by_file.items():
                if file.endswith("/" + f) or file == f or f.endswith("/" + file):
                    candidates = syms
                    break
        if not candidates:
            return None
        inside = [s for s in candidates if s.lineno <= line <= s.end_lineno]
        return min(inside, key=lambda s: s.end_lineno - s.lineno) if inside else None

    def callers(self, sym: Symbol, limit: int = 8) -> list[Symbol]:
        out = [s for s in self.symbols.values()
               if (sym.name in s.calls or sym.name in s.refs) and s.key != sym.key]
        out.sort(key=lambda s: (s.is_test, s.file, s.lineno))
        return out[:limit]

    def source(self, sym: Symbol, pad: int = 2) -> str:
        """Exact source lines of a symbol (plus `pad` lines of context)."""
        lines = self.ws.read(sym.file).splitlines()
        start = max(0, sym.lineno - 1 - pad)
        # include decorators directly above the def
        while start > 0 and lines[start - 1].lstrip().startswith("@"):
            start -= 1
        end = min(len(lines), sym.end_lineno + pad)
        return "\n".join(lines[start:end])

    # ── ranking ──────────────────────────────────────────────────────────────

    def rank(
        self,
        task: str,
        frames: Iterable[tuple[str, int]] = (),
        failing_tests: Iterable[str] = (),
        k: int = 6,
    ) -> list[tuple[Symbol, float, list[str]]]:
        """
        Score non-test symbols by relevance.  Returns (symbol, score, reasons).

        Signals (all zero-token):
          +4.0  appears in a failing-test traceback frame (non-test code)
          +2.5  is called by a failing test function
          +2.5·0.7^d  reached d (≤3) calls below such a function, or a table it reads
          +1.0  per task term matching the symbol's name/doc (idf-weighted)
          +1.0  calls, or is called by, a traceback symbol (blast radius)
        """
        scores: dict[str, float] = defaultdict(float)
        reasons: dict[str, list[str]] = defaultdict(list)

        frame_syms: list[Symbol] = []
        for file, line in frames:
            s = self.at(file, line)
            if s is not None and not s.is_test:
                if s.key not in {f.key for f in frame_syms}:
                    frame_syms.append(s)
                    scores[s.key] += 4.0
                    reasons[s.key].append(f"in traceback ({file}:{line})")

        test_names = {t.split("::")[-1].split("[")[0] for t in failing_tests}
        entry: dict[str, Symbol] = {}
        for tname in test_names:
            for tsym in self.by_name.get(tname, []):
                for callee in tsym.calls:
                    for s in self.by_name.get(callee, []):
                        if not s.is_test:
                            scores[s.key] += 2.5
                            reasons[s.key].append(f"called by failing {tname}")
                            entry[s.key] = s

        # Call chain: a plain assert failure leaves no library frames, and the bug is
        # often a few calls below what the test calls (report() -> total() -> merge()).
        # Walk callees and the module tables they read; each symbol keeps its best path.
        chain: dict[str, tuple[float, str]] = {}
        frontier = [(s, 2.5) for s in entry.values()]
        for _ in range(3):
            nxt = []
            for parent, score in frontier:
                score *= 0.7
                for name in parent.calls | parent.refs:
                    for s in self.by_name.get(name, []):
                        if s.is_test or s.key in entry:
                            continue
                        if s.kind == "variable" and not (s.file == parent.file
                                                         and (s.end_lineno > s.lineno or s.name.isupper())):
                            continue
                        if score > chain.get(s.key, (0.0, ""))[0]:
                            chain[s.key] = (score, parent.qualname)
                            nxt.append((s, score))
            frontier = nxt
        for key, (score, via) in chain.items():
            scores[key] += score
            reasons[key].append(f"reached from failing test via {via}")

        terms = tokenize(task)
        if terms:
            n = max(1, len(self.symbols))
            df: dict[str, int] = defaultdict(int)
            sym_terms = {}
            for key, s in self.symbols.items():
                st = set(tokenize(s.qualname + " " + s.doc + " " + s.file))
                sym_terms[key] = st
                for t in st:
                    df[t] += 1
            for key, s in self.symbols.items():
                if s.is_test:
                    continue
                hit = [t for t in set(terms) if t in sym_terms[key]]
                if hit:
                    scores[key] += sum(math.log(1 + n / df[t]) for t in hit) / 2.0
                    reasons[key].append("matches task: " + ", ".join(sorted(hit)))

        for fs in frame_syms:
            for s in self.symbols.values():
                if s.is_test or s.key == fs.key:
                    continue
                if s.name in fs.calls or fs.name in s.calls:
                    scores[s.key] += 1.0
                    reasons[s.key].append(f"linked to {fs.qualname}")

        # Data dependencies: module-level tables/constants read by a strong
        # candidate (e.g. OPS = {"/": lambda a, b: ...} used by evaluate()).
        strong = [self.symbols[k_] for k_, v in scores.items() if v >= 2.5 and self.symbols[k_].kind != "variable"]
        for s in strong:
            for ref in s.refs:
                for v in self.by_name.get(ref, []):
                    if (v.kind == "variable" and not v.is_test and v.file == s.file
                            and (v.end_lineno > v.lineno or v.name.isupper())
                            and f"used by {s.qualname}" not in reasons[v.key]):
                        scores[v.key] += 1.5
                        reasons[v.key].append(f"used by {s.qualname}")

        ranked = sorted(
            ((self.symbols[k_], v, reasons[k_]) for k_, v in scores.items() if v > 0),
            key=lambda item: (-item[1], item[0].file, item[0].lineno),
        )
        # Prefer the innermost symbol: drop a class if one of its methods ranked.
        picked: list[tuple[Symbol, float, list[str]]] = []
        for sym, score, why in ranked:
            if sym.kind == "class" and any(p[0].qualname.startswith(sym.qualname + ".") for p in ranked[:k * 2]):
                continue
            picked.append((sym, score, why))
            if len(picked) >= k:
                break
        return picked
