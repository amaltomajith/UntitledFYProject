"""
puppeteer_code/assistant/codemap.py
===================================
The structure of a project, read without running any of it. Python goes through
the standard `ast` module, JavaScript and TypeScript through Tree-sitter. Any other
file is a file with no symbols, and so is a script when Tree-sitter is not installed.

`sync` compares the folder with the last indexed snapshot, so edits made outside
the assistant are found without a file watcher. A CALLS edge is matched by name
alone, and only when the name is unique in the project: a missing edge is better
than a wrong one.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import posixpath
from collections import defaultdict
from typing import Callable, Optional

from puppeteer_code.assistant.permissions import is_protected
from puppeteer_code.index import Symbol, _Visitor
from puppeteer_code.workspace import IGNORE_DIRS

MAX_FILE_BYTES = 200_000
MAX_FILES = 5000
SIGNATURE_CHARS = 120
BATCH_FILES = 200
MINIFIED_LINE = 500             # average characters per line: a bundle, not source anyone edits
GENERATED_DIRS = frozenset({".next", ".nuxt", ".svelte-kit", ".turbo", ".parcel-cache", ".cache", "coverage", "target"})

PYTHON = (".py",)
SCRIPTS = {".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
           ".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx"}
SCRIPT_ENDINGS = ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
                  "/index.ts", "/index.tsx", "/index.js", "/index.jsx")

# Names every runtime has. A project method called `get` says nothing about a call to dict.get.
COMMON_NAMES = frozenset("""
    add all any append apply bind call catch clear close concat copy count delete dump dumps emit encode decode
    entries error every exists extend fetch fill filter find flush forEach format from get has includes index
    indexOf info insert isArray items join json keys length list load loads log map match max min next on once
    open parse pop print push read reduce remove replace reverse run save search send set shift slice some sort
    splice split start stop strip then toString trim update values warn warning write
""".split())

FUNCTION_VALUES = ("arrow_function", "function_expression", "function", "generator_function")
DECLARATIONS = {"function_declaration": "function", "generator_function_declaration": "function",
                "class_declaration": "class", "abstract_class_declaration": "class", "class": "class",
                "interface_declaration": "type", "type_alias_declaration": "type", "enum_declaration": "type",
                "method_definition": "method", "abstract_method_signature": "method"}

_parsers: dict[str, object] = {}


@dataclasses.dataclass
class FileMap:
    path: str
    language: Optional[str] = None
    digest: str = ""
    symbols: list[Symbol] = dataclasses.field(default_factory=list)
    imports: list[str] = dataclasses.field(default_factory=list)
    partial: bool = False       # the parser met a syntax error, so some symbols may be missing


def tree_sitter_ready() -> bool:
    return _parser("javascript") is not None


def _parser(language: str):
    if language not in _parsers:
        try:
            import tree_sitter
            if language == "javascript":
                import tree_sitter_javascript as grammar
                pointer = grammar.language()
            else:
                import tree_sitter_typescript as grammar
                pointer = grammar.language_tsx() if language == "tsx" else grammar.language_typescript()
            _parsers[language] = tree_sitter.Parser(tree_sitter.Language(pointer))
        except (ImportError, ValueError, OSError):
            _parsers[language] = None
    return _parsers[language]


def parse(path: str, text: str) -> FileMap:
    """Never raises on bad source: a file that cannot be read is still a file."""
    dot = posixpath.splitext(path)[1].lower()
    if dot in PYTHON:
        return _parse_python(path, text)
    language = SCRIPTS.get(dot)
    parser = _parser(language) if language else None
    if parser is None or path.endswith(".min.js") or len(text) / (text.count("\n") + 1) > MINIFIED_LINE:
        return FileMap(path)
    try:
        return _parse_script(path, text, language, parser)
    except (ValueError, UnicodeError):
        return FileMap(path, language=language, partial=True)


def _parse_python(path: str, text: str) -> FileMap:
    out = FileMap(path, language="python")
    try:
        tree = ast.parse(text)
        visitor = _Visitor(path)
        visitor.visit(tree)
    except (SyntaxError, ValueError, RecursionError):
        out.partial = True
        return out
    out.symbols = [s for s in visitor.symbols if s.kind != "variable"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.imports += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            out.imports.append(base)
            joiner = "" if base.endswith(".") or not base else "."
            out.imports += [base + joiner + alias.name for alias in node.names if alias.name != "*"]
    out.imports = sorted(set(out.imports))
    return out


def _text(node) -> str:
    return node.text.decode("utf-8", "replace") if node is not None else ""


def _module(node) -> str:
    """The text of a string literal node, without its quotes."""
    return _text(node).strip("'\"`")


def _call_name(node) -> str:
    target = node.child_by_field_name("function") or node.child_by_field_name("constructor")
    if target is None:
        return ""
    if target.type == "identifier":
        return _text(target)
    if target.type == "member_expression":
        return _text(target.child_by_field_name("property"))
    return ""


def _signature(node) -> str:
    params = node.child_by_field_name("parameters") or node.child_by_field_name("parameter")
    answer = node.child_by_field_name("return_type")
    return " ".join((_text(params) + _text(answer)).split())[:SIGNATURE_CHARS]


def _parse_script(path: str, text: str, language: str, parser) -> FileMap:
    tree = parser.parse(text.encode("utf-8"))
    out = FileMap(path, language=language, partial=tree.root_node.has_error)
    imports: set[str] = set()

    def add(name: str, kind: str, node, source, scope: tuple[str, ...]) -> Symbol:
        qual = ".".join([*scope, name])
        symbol = Symbol(qualname=qual, name=name, kind=kind, file=path, lineno=node.start_point[0] + 1,
                        end_lineno=node.end_point[0] + 1, signature=_signature(source), doc="")
        out.symbols.append(symbol)
        return symbol

    stack = [(tree.root_node, None, ())]    # iterative: minified bundles nest deeper than Python's stack
    while stack:
        node, owner, scope = stack.pop()
        kind = node.type
        made = None
        if kind in DECLARATIONS:
            name = _text(node.child_by_field_name("name"))
            if name:
                wanted = "method" if kind == "method_definition" else DECLARATIONS[kind]
                made = add(name, wanted, node, node, scope)
        elif kind in ("variable_declarator", "public_field_definition", "field_definition"):
            value = node.child_by_field_name("value")
            field = node.child_by_field_name("name") or node.child_by_field_name("property")
            if value is not None and value.type in FUNCTION_VALUES and field is not None:
                made = add(_text(field), "function" if kind == "variable_declarator" else "method", node, value, scope)
        elif kind in ("import_statement", "export_statement"):
            source = node.child_by_field_name("source")
            if source is not None:
                imports.add(_module(source))
        elif kind in ("call_expression", "new_expression"):
            target = node.child_by_field_name("function")
            arguments = node.child_by_field_name("arguments")
            is_load = target is not None and (target.type == "import" or _text(target) == "require")
            if is_load and arguments is not None and arguments.named_child_count:
                first = arguments.named_children[0]
                if first.type == "string":
                    imports.add(_module(first))
            elif owner is not None and _call_name(node):
                owner.calls.add(_call_name(node))
        elif kind in ("jsx_opening_element", "jsx_self_closing_element"):
            name = _text(node.child_by_field_name("name"))
            if owner is not None and name[:1].isupper() and "." not in name:
                owner.calls.add(name)

        inner = (owner if made is None else made, scope if made is None else (*scope, made.name))
        for child in reversed(node.children):
            stack.append((child, *inner))

    out.imports = sorted(imports)
    return out


# ── linking ──────────────────────────────────────────────────────────────────

def resolve_import(path: str, name: str, files: set[str]) -> Optional[str]:
    """The project file an import refers to, or None for a package or a built-in."""
    folder = posixpath.dirname(path)
    if path.endswith(".py"):
        level = len(name) - len(name.lstrip("."))
        tail = name[level:].replace(".", "/")
        if level:
            base = folder
            for _ in range(level - 1):
                base = posixpath.dirname(base)
            roots = [base]
        else:
            roots = ["", folder, "src"]
        for root in roots:
            stem = posixpath.join(root, tail) if tail else root
            for candidate in (f"{stem}.py", posixpath.join(stem, "__init__.py")):
                if candidate in files and candidate != path:
                    return candidate
        return None
    if name.startswith("."):
        stems = [posixpath.normpath(posixpath.join(folder, name))]
    elif name.startswith(("@/", "~/")):
        stems = [name[2:], f"src/{name[2:]}"]
    else:
        return None
    for stem in stems:
        trimmed = stem[:-3] if stem.endswith(".js") else stem    # TypeScript writes ./a.js for a.ts
        for candidate in (stem, *(trimmed + ending for ending in SCRIPT_ENDINGS)):
            if candidate in files and candidate != path:
                return candidate
    return None


def link_calls(symbols: list[dict]) -> list[tuple[str, str]]:
    """(caller id, callee id) for every call whose name means exactly one symbol in the project."""
    by_name: dict[str, list[str]] = defaultdict(list)
    for symbol in symbols:
        if symbol["kind"] != "type":
            by_name[symbol["name"]].append(symbol["id"])
    edges = []
    for symbol in symbols:
        for name in symbol["calls"]:
            found = by_name.get(name, [])
            if len(found) == 1 and name not in COMMON_NAMES and found[0] != symbol["id"]:
                edges.append((symbol["id"], found[0]))
    return edges


# ── keeping the graph current ────────────────────────────────────────────────

def indexable(rel: str) -> bool:
    parts = rel.split("/")
    return (not is_protected(rel) and not rel.endswith((".pyc", ".pyo"))
            and not any(part in IGNORE_DIRS or part in GENERATED_DIRS or part.endswith(".egg-info")
                        for part in parts[:-1]))


def _read(toolbox, rel: str) -> Optional[FileMap]:
    """None when the file is gone. A file too large or not text keeps its node and has no symbols."""
    path = toolbox.root / rel
    try:
        if not path.is_file():
            return None
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            return FileMap(rel, digest=f"size:{size}")
        data = path.read_bytes()
    except OSError:
        return None
    digest = hashlib.sha256(data).hexdigest()[:16]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return FileMap(rel, digest=digest)
    found = parse(rel, text)
    found.digest = digest
    return found


def sync(graph, toolbox, snapshots, progress: Optional[Callable[[int], None]] = None) -> dict:
    """Bring the graph up to the folder. Returns the ids that appeared and went."""
    sha = graph.get_meta("indexed_sha")
    changed: Optional[list[str]] = None
    if sha:
        try:
            changed = snapshots.changed_since(sha)
        except RuntimeError:        # a snapshot store that was removed by hand: start again
            changed = None
    if changed == []:
        return {"added": [], "removed": []}

    known = graph.file_digests()
    if changed is None:
        wanted = [rel for rel in snapshots.tracked() if indexable(rel)][:MAX_FILES]
        changed = wanted + sorted(set(known) - set(wanted))
    before = graph.structure_ids()
    edited = 0
    batch: list[FileMap] = []
    for rel in changed:
        found = _read(toolbox, rel) if indexable(rel) else None
        if found is None:
            if rel in known:
                graph.remove_file(rel)
                edited += 1
        elif known.get(rel) != found.digest:
            batch.append(found)
            edited += 1
            if len(batch) >= BATCH_FILES:      # one commit per file took most of a first index
                graph.put_files(batch)
                batch = []
                if progress:
                    progress(edited)
    graph.put_files(batch)
    if edited:
        relink(graph)
    graph.set_meta("indexed_sha", snapshots.snapshot("index"))
    after = graph.structure_ids()
    return {"added": sorted(after - before), "removed": sorted(before - after)}


def relink(graph) -> None:
    """IMPORTS and CALLS depend on the whole project, so both are rebuilt after any change."""
    files = graph.file_imports()
    names = set(files)
    imports = sorted({(path, target) for path, wanted in files.items() for name in wanted
                      if (target := resolve_import(path, name, names))})
    graph.set_links(imports, link_calls(graph.symbol_calls()))
