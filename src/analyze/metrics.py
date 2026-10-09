"""Static code metrics for the analyze layer.

Python files are parsed with `ast` + `tokenize` (exact).  JS/TS files use a small
brace/keyword scanner (heuristic, no dependencies).  Everything is plain data so
mapping.py can turn it into music and pipeline.py can dump it to JSON.
"""
from __future__ import annotations

import ast
import io
import re
import subprocess
import sys
import tokenize
import zlib
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

PY_EXT = {".py"}
JS_EXT = {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx"}
SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", "build", "dist",
             "site-packages", ".tox", ".mypy_cache", ".pytest_cache", "samples", ".idea", ".vscode"}
MAX_FILE_BYTES = 400_000
DUP_WINDOW = 3                     # lines per "snippet" for the repeated-code heuristic

CONTROL_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith)
if sys.version_info >= (3, 10):
    CONTROL_NODES = CONTROL_NODES + (ast.Match,)
if sys.version_info >= (3, 11):
    CONTROL_NODES = CONTROL_NODES + (ast.TryStar,)


@dataclass
class FuncMetrics:
    name: str
    lineno: int
    end_lineno: int
    loc: int
    max_depth: int          # deepest nesting of control-flow blocks inside the function
    complexity: int         # McCabe-ish: 1 + decision points
    calls: int = 0
    is_async: bool = False
    has_dup: bool = False   # contains a snippet that also appears elsewhere


@dataclass
class FileMetrics:
    path: str
    lang: str
    loc: int                         # non-blank lines
    comment_lines: int
    comment_ratio: float
    imports: list[str] = field(default_factory=list)
    import_kinds: list[str] = field(default_factory=list)   # stdlib | third | local
    control_nodes: int = 0
    control_density: float = 0.0     # control-flow statements per 10 LOC
    max_depth: int = 0
    complexity: int = 0              # sum over functions (+ module-level decisions)
    classes: int = 0
    functions: list[FuncMetrics] = field(default_factory=list)
    dup_windows: int = 0
    dup_ratio: float = 0.0
    parse_error: str | None = None
    churn: int = 0                   # lines added+removed over git history (0 outside a git repo)
    commits: int = 0                 # commits touching the file
    _windows: list[tuple[int, int]] = field(default_factory=list, repr=False)  # (line, hash)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("_windows", None)
        return d


# --------------------------------------------------------------------- helpers
def _norm_lines(lines: list[str], comment_prefix: str) -> list[tuple[int, str]]:
    out = []
    for i, ln in enumerate(lines, start=1):
        s = re.sub(r"\s+", " ", ln.strip())
        if len(s) < 8 or s.startswith(comment_prefix) or set(s) <= set("{}()[];,:"):
            continue
        out.append((i, s))
    return out


def _windows(norm: list[tuple[int, str]]) -> list[tuple[int, int]]:
    return [(norm[i][0], zlib.crc32("\n".join(s for _, s in norm[i:i + DUP_WINDOW]).encode()))
            for i in range(len(norm) - DUP_WINDOW + 1)]


def _stdlib_names() -> set[str]:
    return set(getattr(sys, "stdlib_module_names", ())) | set(sys.builtin_module_names)


STDLIB = _stdlib_names()


# --------------------------------------------------------------------- Python
class _FuncVisitor(ast.NodeVisitor):
    """Complexity + nesting for one function body (nested defs are measured separately)."""

    def __init__(self):
        self.complexity = 1
        self.max_depth = 0
        self.depth = 0
        self.calls = 0
        self.control = 0

    def generic_visit(self, node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)) \
                and getattr(self, "_root", None) is not node:
            return  # nested scope -> its own FuncMetrics
        nested = isinstance(node, CONTROL_NODES)
        if nested:
            self.control += 1
            self.depth += 1
            self.max_depth = max(self.max_depth, self.depth)
        if isinstance(node, (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While, ast.Assert)):
            self.complexity += 1
        elif isinstance(node, ast.ExceptHandler):
            self.complexity += 1
        elif isinstance(node, ast.BoolOp):
            self.complexity += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            self.complexity += 1 + len(node.ifs)
        elif sys.version_info >= (3, 10) and isinstance(node, ast.match_case):
            self.complexity += 1
        elif isinstance(node, ast.Call):
            self.calls += 1
        if isinstance(node, ast.If) and len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            # `elif` chain: the inner If sits in orelse but is NOT one level deeper
            self.visit(node.test)
            for child in node.body:
                self.visit(child)
            self.depth -= 1
            self.visit(node.orelse[0])   # counts itself as one more control node
            return
        super().generic_visit(node)
        if nested:
            self.depth -= 1

    def run(self, root):
        self._root = root
        self.generic_visit(root)
        return self


def analyze_python(path: str, text: str) -> FileMetrics:
    lines = text.splitlines()
    loc = sum(1 for ln in lines if ln.strip())
    fm = FileMetrics(path=path, lang="python", loc=loc, comment_lines=0, comment_ratio=0.0)
    comment_rows: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                comment_rows.add(tok.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        fm.parse_error = f"{type(e).__name__}: {e.msg} (line {e.lineno})"
        fm.comment_lines = len(comment_rows)
        fm.comment_ratio = round(len(comment_rows) / max(1, loc), 3)
        return fm

    # docstrings count as "comments" (documentation density)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                comment_rows.update(range(body[0].lineno, (body[0].end_lineno or body[0].lineno) + 1))
    fm.comment_lines = len(comment_rows)
    fm.comment_ratio = round(min(1.0, len(comment_rows) / max(1, loc)), 3)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                fm.imports.append(a.name)
                fm.import_kinds.append("stdlib" if a.name.split(".")[0] in STDLIB else "third")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            kind = "local" if node.level else ("stdlib" if mod.split(".")[0] in STDLIB else "third")
            for a in node.names:
                fm.imports.append(("." * node.level) + mod + ":" + a.name)
                fm.import_kinds.append(kind)
        elif isinstance(node, ast.ClassDef):
            fm.classes += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            v = _FuncVisitor().run(node)
            end = node.end_lineno or node.lineno
            floc = sum(1 for ln in lines[node.lineno - 1:end] if ln.strip())
            fm.functions.append(FuncMetrics(node.name, node.lineno, end, floc, v.max_depth, v.complexity,
                                            v.calls, isinstance(node, ast.AsyncFunctionDef)))
    mod = _FuncVisitor().run(tree)          # module-level (outside defs)
    fm.functions.sort(key=lambda f: f.lineno)
    fm.control_nodes = mod.control  # module-level control
    func_control = sum(_FuncVisitor().run(n).control for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
    fm.control_nodes += func_control
    fm.control_density = round(10 * fm.control_nodes / max(1, loc), 3)
    fm.max_depth = max([mod.max_depth] + [f.max_depth for f in fm.functions])
    fm.complexity = (mod.complexity - 1) + sum(f.complexity for f in fm.functions)
    fm._windows = _windows(_norm_lines(lines, "#"))
    return fm


# --------------------------------------------------------------------- JS / TS (heuristic)
JS_CTRL_KW = re.compile(r"\b(if|for|while|switch|try|catch|else|do|finally)\b")
JS_DECISION = re.compile(r"\b(if|for|while|case|catch)\b|&&|\|\||\?(?![.?])")
JS_FUNC = re.compile(r"(?:function\s*\*?\s*(\w+)\s*\(|(\w+)\s*[:=]\s*(?:async\s*)?(?:function\b|\([^()]*\)\s*=>|\w+\s*=>)"
                     r"|^\s*(?:async\s+)?(\w+)\s*\([^()]*\)\s*\{)", re.M)
JS_IMPORT = re.compile(r"^\s*import\b.*?from\s*['\"]([^'\"]+)['\"]|^\s*import\s*['\"]([^'\"]+)['\"]"
                       r"|require\(\s*['\"]([^'\"]+)['\"]\s*\)", re.M)
NON_FUNC = {"if", "for", "while", "switch", "catch", "return", "function"}


def _strip_js(text: str) -> tuple[str, set[int]]:
    """Blank out strings/comments (keeping newlines); return code + comment line numbers."""
    out, rows, i, n, line = [], set(), 0, len(text), 1
    while i < n:
        c, nx = text[i], text[i + 1] if i + 1 < n else ""
        if c == "/" and nx == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            rows.add(line)
            out.append(" " * (j - i)); i = j
        elif c == "/" and nx == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            seg = text[i:j]
            rows.update(range(line, line + seg.count("\n") + 1))
            out.append(re.sub(r"[^\n]", " ", seg)); line += seg.count("\n"); i = j
        elif c in "'\"`":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            seg = text[i:j + 1]
            out.append(c + re.sub(r"[^\n]", " ", seg[1:-1]) + c if len(seg) > 1 else seg)
            line += seg.count("\n"); i = j + 1
        else:
            if c == "\n":
                line += 1
            out.append(c); i += 1
    return "".join(out), rows


def analyze_js(path: str, text: str) -> FileMetrics:
    lines = text.splitlines()
    loc = sum(1 for ln in lines if ln.strip())
    code, crow = _strip_js(text)
    fm = FileMetrics(path=path, lang="js", loc=loc, comment_lines=len(crow),
                     comment_ratio=round(min(1.0, len(crow) / max(1, loc)), 3))
    for m in JS_IMPORT.finditer(text):
        mod = next(g for g in m.groups() if g)
        fm.imports.append(mod)
        fm.import_kinds.append("local" if mod.startswith(".") else ("stdlib" if mod.startswith("node:") else "third"))
    # brace scan: which braces are control blocks / function bodies
    starts = {}
    for m in JS_FUNC.finditer(code):
        name = next((g for g in m.groups() if g), "anonymous")
        if name in NON_FUNC:
            continue
        b = code.find("{", m.end() - 1)
        if b >= 0 and b - m.end() < 80:
            starts[b] = name
    stack, depth, max_depth, ctrl = [], 0, 0, 0
    last_break = 0
    funcs: list[FuncMetrics] = []
    for i, c in enumerate(code):
        if c == "{":
            head = code[last_break:i]
            is_ctrl = bool(JS_CTRL_KW.search(head))
            if is_ctrl:
                depth += 1; ctrl += 1
                max_depth = max(max_depth, depth)
            stack.append((is_ctrl, starts.get(i), i, depth))
            last_break = i + 1
        elif c == "}":
            if stack:
                is_ctrl, fname, b, d0 = stack.pop()
                if is_ctrl:
                    depth -= 1
                if fname:
                    body = code[b:i]
                    l0 = code.count("\n", 0, b) + 1
                    l1 = l0 + body.count("\n")
                    sub_depth = _js_depth(body)
                    funcs.append(FuncMetrics(fname, l0, l1, sum(1 for ln in lines[l0 - 1:l1] if ln.strip()),
                                             sub_depth, 1 + len(JS_DECISION.findall(body)),
                                             len(re.findall(r"\w\s*\(", body))))
            last_break = i + 1
        elif c == ";":
            last_break = i + 1
    fm.functions = sorted(funcs, key=lambda f: f.lineno)
    fm.control_nodes = ctrl
    fm.control_density = round(10 * ctrl / max(1, loc), 3)
    fm.max_depth = max_depth
    fm.complexity = len(JS_DECISION.findall(code)) + max(1, len(funcs))
    fm.classes = len(re.findall(r"\bclass\s+\w+", code))
    fm._windows = _windows(_norm_lines(code.splitlines(), "//"))
    return fm


def _js_depth(body: str) -> int:
    depth = mx = 0
    last = 0
    st = []
    for i, c in enumerate(body):
        if c == "{":
            ctrl = bool(JS_CTRL_KW.search(body[last:i]))
            st.append(ctrl)
            if ctrl:
                depth += 1; mx = max(mx, depth)
            last = i + 1
        elif c == "}":
            if st and st.pop():
                depth -= 1
            last = i + 1
        elif c == ";":
            last = i + 1
    return mx


# --------------------------------------------------------------------- repo scan
def list_files(root: Path, include_js: bool = True) -> list[Path]:
    exts = PY_EXT | (JS_EXT if include_js else set())
    files: list[Path] = []
    try:  # respect .gitignore when the target is a git work tree
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard"],
                             capture_output=True, text=True, check=True).stdout
        files = [root / p for p in out.splitlines() if Path(p).suffix in exts]
    except (subprocess.CalledProcessError, FileNotFoundError):
        files = [p for p in root.rglob("*") if p.suffix in exts]
    keep = []
    for p in files:
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]) or not p.is_file():
            continue
        if p.stat().st_size > MAX_FILE_BYTES or p.name.endswith(".min.js"):
            continue
        keep.append(p)
    return sorted(keep)


def git_churn(root: Path, max_commits: int = 2000) -> dict[str, tuple[int, int]]:
    """{path relative to root: (lines added+removed, commits)} from `git log --numstat`.

    Renames are followed only as far as numstat reports them (`old => new` paths are
    credited to the new name). Empty dict when root is not inside a git work tree."""
    cwd = root if root.is_dir() else root.parent
    try:
        out = subprocess.run(["git", "-C", str(cwd), "log", f"-n{max_commits}", "--numstat",
                              "--format=tformat:@@", "--relative", "--no-renames", "--", "."],
                             capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {}
    churn: dict[str, list[int]] = {}
    for ln in out.splitlines():
        parts = ln.split("\t")
        if len(parts) != 3 or parts[0] == "-":
            continue
        a, d, path = parts
        c = churn.setdefault(path, [0, 0])
        c[0] += int(a) + int(d)
        c[1] += 1
    return {k: (v[0], v[1]) for k, v in churn.items()}


def analyze_repo(root: str | Path, include_js: bool = True) -> list[FileMetrics]:
    root = Path(root).resolve()
    if root.is_file():
        paths, base = [root], root.parent
    else:
        paths, base = list_files(root, include_js), root
    res: list[FileMetrics] = []
    for p in paths:
        text = p.read_text(encoding="utf-8", errors="replace")
        rel = str(p.relative_to(base))
        fm = analyze_python(rel, text) if p.suffix in PY_EXT else analyze_js(rel, text)
        if fm.loc == 0:
            continue
        res.append(fm)
    # repeated-snippet heuristic: identical normalized 3-line windows anywhere in the tree
    counts = Counter(h for fm in res for _, h in fm._windows)
    for fm in res:
        dup_lines = [ln for ln, h in fm._windows if counts[h] > 1]
        fm.dup_windows = len(dup_lines)
        fm.dup_ratio = round(len(dup_lines) / max(1, len(fm._windows)), 3)
        for f in fm.functions:
            f.has_dup = any(f.lineno <= ln <= f.end_lineno for ln in dup_lines)
    churn = git_churn(root)
    for fm in res:
        fm.churn, fm.commits = churn.get(fm.path, (0, 0))
    return res


def summarize(files: list[FileMetrics]) -> dict:
    funcs = [f for fm in files for f in fm.functions]
    return {
        "files": len(files),
        "loc": sum(f.loc for f in files),
        "functions": len(funcs),
        "max_depth": max([fm.max_depth for fm in files] or [0]),
        "mean_complexity": round(sum(f.complexity for f in funcs) / max(1, len(funcs)), 2),
        "imports": sum(len(fm.imports) for fm in files),
        "comment_ratio": round(sum(fm.comment_lines for fm in files) / max(1, sum(fm.loc for fm in files)), 3),
        "dup_ratio": round(sum(fm.dup_windows for fm in files) / max(1, sum(len(fm._windows) for fm in files)), 3),
        "parse_errors": [fm.path for fm in files if fm.parse_error],
        "churn": sum(fm.churn for fm in files),
    }
