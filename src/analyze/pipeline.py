"""analyze 模式的流水线：代码 → 指标 → 音符 → midi/analyze/*.mid → render_all → mix → out/analyze.wav

The existing render/mix modules read paths and the form (N_BARS, rit) from
src.config at call time, so this module points them at an analyze-specific
profile (midi/analyze, build/analyze/stems, …) instead of duplicating them.
The main 22-bar score in midi/*.mid and out/final.wav is never touched.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import mido

from src import config as C
from src.analyze import diffscan, mapping, metrics
from src.score import write_score as W


def use_profile(name: str, n_bars: int) -> None:
    """Redirect config to midi/<name>, build/<name> with an n-bar form at 100 BPM."""
    C.MIDI_DIR = C.ROOT / "midi" / name
    C.BUILD_DIR = C.ROOT / "build" / name
    C.STEMS_DIR = C.BUILD_DIR / "stems"
    C.N_BARS = n_bars
    C.RIT_BPM = mapping.rit_for(n_bars)
    C.SECTIONS = {"intro": (1, 1), "body": (2, max(2, n_bars - 2)), "resolve": (n_bars - 1, n_bars)}


def write_midi(score: W.Score) -> dict[str, int]:
    """Write only the parts that have notes (tempo map + part per file) and a full.mid."""
    C.MIDI_DIR.mkdir(parents=True, exist_ok=True)
    for old in C.MIDI_DIR.glob("*.mid"):
        old.unlink()
    if C.STEMS_DIR.exists():                      # stale stems would leak into the mix
        for old in C.STEMS_DIR.glob("*"):
            if old.is_file():
                old.unlink()
    end = C.N_BARS * C.BEATS_PER_BAR
    score.notes = [n for n in score.notes if n.start < end]
    for n in score.notes:
        n.dur = min(n.dur, end + 2 - n.start)
    full = mido.MidiFile(type=1, ticks_per_beat=W.TPB)
    full.tracks.append(W.tempo_track())
    counts = {}
    for part in C.PARTS:
        k = sum(1 for n in score.notes if n.part == part)
        if not k:
            continue
        tr = W.part_track(score, part)
        full.tracks.append(tr)
        single = mido.MidiFile(type=1, ticks_per_beat=W.TPB)
        single.tracks.append(W.tempo_track())
        single.tracks.append(tr)
        single.save(C.MIDI_DIR / f"{part}.mid")
        counts[part] = k
    full.save(C.MIDI_DIR / "full.mid")
    return counts


def render_and_mix(parts: list[str], out: Path, fallback: bool, voice: str | None) -> None:
    from src.mix import mix
    from src.render import render_all
    render_all.main((["--fallback"] if fallback else []) + parts)
    mix.main(["--out", str(out)] + (["--voice", voice] if voice else []))


def short_summary(meta: dict) -> dict:
    """Compact digest of analysis.json: form, consonance balance, hottest sections."""
    keys = ("mode", "repo", "range", "key", "bpm", "bars", "duration_s", "progression", "parts",
            "summary", "hunks", "hunks_dropped", "files_dropped", "heat_per_bar")
    out = {k: meta[k] for k in keys if k in meta}
    plan = meta.get("plan", [])
    out["colors"] = dict(Counter(p.get("color") for p in plan if p.get("color")))
    if meta.get("mode") == "repo":
        out["sections"] = [{k: p[k] for k in ("file", "bars", "chords", "tension", "color", "heat", "churn")}
                           for p in plan]
        out["hottest"] = [p["file"] for p in sorted(plan, key=lambda p: -p["heat"])[:5]]
    else:
        out["hunks_sonified"] = [{k: p[k] for k in ("file", "beat", "added", "removed", "cx_delta", "color")}
                                 for p in plan]
    return out


def run(repo: str, diff: bool = False, rev: str | None = None, max_bars: int | None = None,
        include_js: bool = True, render: bool = True, fallback: bool = False,
        out: str | None = None, voice: str | None = None, heat: bool = False,
        summary_out: str | None = None) -> dict:
    repo_p = Path(repo).expanduser().resolve()
    if not repo_p.exists():
        raise SystemExit(f"[analyze] no such path: {repo_p}")
    if diff:
        hunks, label = diffscan.diff_hunks(repo_p, rev)
        if not hunks:
            raise SystemExit(f"[analyze] git diff ({label}) has no text hunks to sonify")
        score, tl, plan, dropped = mapping.compose_diff(hunks, max_bars or 32, heat=heat)
        profile, default_out = "analyze/diff", C.OUT_DIR / "analyze_diff.wav"
        info = {"mode": "diff", "range": label, "hunks": len(hunks), "hunks_dropped": dropped}
        print(f"[analyze] diff {label}: {len(hunks)} hunks in {len({h.path for h in hunks})} files"
              + (f" ({dropped} dropped, raise --max-bars)" if dropped else ""))
    else:
        files = metrics.analyze_repo(repo_p, include_js)
        if not files:
            raise SystemExit(f"[analyze] no .py/.js files found under {repo_p}")
        summary = metrics.summarize(files)
        score, tl, plan = mapping.compose_repo(files, max_bars or 48)
        profile, default_out = "analyze", C.OUT_DIR / "analyze.wav"
        info = {"mode": "repo", "summary": summary, "files_dropped": tl.dropped,
                "metrics": [f.to_dict() for f in files]}
        print(f"[analyze] {summary['files']} files, {summary['loc']} LOC, {summary['functions']} functions, "
              f"max depth {summary['max_depth']}, mean complexity {summary['mean_complexity']}, "
              f"{summary['imports']} imports, comments {summary['comment_ratio']:.0%}, dup {summary['dup_ratio']:.1%}"
              + (f" ({len(tl.dropped)} coldest files dropped, raise --max-bars)" if tl.dropped else ""))

    use_profile(profile, len(tl.chords))
    counts = write_midi(score)
    C.BUILD_DIR.mkdir(parents=True, exist_ok=True)
    meta = {"repo": str(repo_p), "key": C.KEY, "bpm": C.BPM, "bars": C.N_BARS, "rit_bpm": C.RIT_BPM,
            "duration_s": round(C.total_seconds(), 2), "progression": tl.chords, "parts": counts,
            "plan": plan, **({"heat_per_bar": tl.heat} if tl.heat else {}), **info}
    (C.BUILD_DIR / "analysis.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    if summary_out:
        sp = Path(summary_out)
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps(short_summary(meta), ensure_ascii=False, indent=1))
        print(f"[analyze] summary -> {sp}")
    rel = C.MIDI_DIR.relative_to(C.ROOT)
    print(f"[analyze] {C.KEY}, {C.BPM} BPM, {C.N_BARS} bars, ~{C.total_seconds():.1f}s")
    print("[analyze] progression:", " ".join(tl.chords))
    for p, k in counts.items():
        print(f"[analyze]   {p:7s} {k:4d} notes -> {rel}/{p}.mid")
    print(f"[analyze] report -> {(C.BUILD_DIR / 'analysis.json').relative_to(C.ROOT)}")
    if render:
        render_and_mix(list(counts), Path(out) if out else default_out, fallback, voice)
    return meta
