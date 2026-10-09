"""git diff → hunks (CodeSonify-style: every changed hunk becomes a short motif)."""
from __future__ import annotations

import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


@dataclass
class Hunk:
    path: str
    old_start: int
    new_start: int
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    context: str = ""          # function/class name git puts after the @@ header

    @property
    def size(self) -> int:
        return len(self.added) + len(self.removed)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["added"], d["removed"] = len(self.added), len(self.removed)
        return d


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if r.returncode:
        raise subprocess.CalledProcessError(r.returncode, ["git", *args], r.stdout, r.stderr)
    return r.stdout


def resolve_range(repo: Path, rev: str | None) -> tuple[list[str], str]:
    """Pick what to sonify. Returns (git diff args, human label)."""
    if rev:  # "A..B" range, or a single commit (diffed against its parent / the empty tree)
        if ".." in rev:
            return [rev], rev
        try:
            _git(repo, "rev-parse", "--verify", "-q", f"{rev}~1")
            return [f"{rev}~1", rev], rev
        except subprocess.CalledProcessError:
            return [EMPTY_TREE, rev], f"{rev} (root commit)"
    if _git(repo, "status", "--porcelain", "--untracked-files=no").strip():
        return ["HEAD"], "working tree vs HEAD"
    try:
        _git(repo, "rev-parse", "--verify", "-q", "HEAD~1")
        return ["HEAD~1", "HEAD"], "HEAD~1..HEAD"
    except subprocess.CalledProcessError:
        return [EMPTY_TREE, "HEAD"], "root commit (empty tree..HEAD)"


def parse_diff(text: str) -> list[Hunk]:
    hunks: list[Hunk] = []
    path, cur = None, None
    for ln in text.splitlines():
        if ln.startswith("diff --git"):
            path, cur = None, None
        elif ln.startswith("+++ "):
            p = ln[4:].strip()
            path = p[2:] if p.startswith("b/") else p
        elif ln.startswith("--- ") and path is None:
            continue
        elif ln.startswith("@@"):
            m = HUNK_RE.match(ln)
            if m and path:
                cur = Hunk(path, int(m.group(1)), int(m.group(3)), context=m.group(5).strip())
                hunks.append(cur)
        elif cur is not None and ln and ln[0] in "+-" and not ln.startswith(("+++", "---")):
            (cur.added if ln[0] == "+" else cur.removed).append(ln[1:])
    for h in hunks:
        if h.path == "/dev/null":
            h.path = "(deleted)"
    return [h for h in hunks if h.size]


def diff_hunks(repo: str | Path, rev: str | None = None, paths: list[str] | None = None) -> tuple[list[Hunk], str]:
    repo = Path(repo).resolve()
    try:
        args, label = resolve_range(repo, rev)
        text = _git(repo, "diff", "--no-color", "--unified=0", "--no-ext-diff", *args, "--", *(paths or []))
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"[analyze] git failed ({' '.join(e.cmd)}): {(e.stderr or '').strip()}") from None
    hunks = parse_diff(text)
    # skip binary/huge generated stuff
    hunks = [h for h in hunks if not h.path.endswith((".lock", ".min.js", ".mid", ".wav", ".svg"))]
    return hunks, label
