"""Load a local `.env` (never committed — see .env.example) into os.environ.

Real environment variables always win (override=False). Uses python-dotenv when installed,
otherwise a tiny KEY=VALUE parser so a bare checkout still works.
Disable with CTM_NO_DOTENV=1; point elsewhere with CTM_DOTENV=/path/to/.env.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_loaded = False


def _parse(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        elif " #" in v:
            v = v.split(" #", 1)[0].rstrip()
        if k:
            out[k] = v
    return out


def load_env(path: str | os.PathLike | None = None) -> Path | None:
    """Load .env once (cwd/.env, then repo-root/.env). Returns the file used, or None."""
    global _loaded
    if _loaded and path is None:
        return None
    _loaded = True
    if os.environ.get("CTM_NO_DOTENV", "") not in ("", "0"):
        return None
    explicit = path or os.environ.get("CTM_DOTENV")
    cands = [Path(explicit)] if explicit else [Path.cwd() / ".env", ROOT / ".env"]
    for p in cands:
        if not p.is_file():
            continue
        try:
            from dotenv import load_dotenv  # type: ignore
            load_dotenv(p, override=False)
        except ImportError:
            for k, v in _parse(p).items():
                os.environ.setdefault(k, v)
        return p
    return None
