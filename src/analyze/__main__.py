"""CLI: python -m src.analyze <repo> [--diff [--rev A..B]]  (same as `musician analyze`)."""
from __future__ import annotations

import argparse


def build_parser(ap: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    ap = ap or argparse.ArgumentParser(prog="musician analyze",
                                       description="把代码结构 / git diff 变成 D 小调 100 BPM 的 MIDI + 混音")
    ap.add_argument("repo", nargs="?", default=".", help="repo / directory / single file to analyze")
    ap.add_argument("--diff", action="store_true",
                    help="sonify git diff hunks instead of the whole tree "
                         "(default: dirty work tree vs HEAD, else HEAD~1..HEAD)")
    ap.add_argument("--rev", help="with --diff: a commit (vs its parent) or a range A..B")
    ap.add_argument("--max-bars", type=int, help="length cap (default 48 repo / 32 diff; 100 BPM → 2.4 s per bar)")
    ap.add_argument("--no-js", action="store_true", help="only analyze .py files")
    ap.add_argument("--midi-only", action="store_true", help="write MIDI + analysis.json, skip render/mix")
    ap.add_argument("--fallback", action="store_true", help="force the numpy sketch synth (no Surge/sfizz)")
    ap.add_argument("--out", help="output WAV (default out/analyze.wav or out/analyze_diff.wav)")
    ap.add_argument("--heat", action="store_true",
                    help="with --diff: add a viola heat track that follows churn density per bar")
    ap.add_argument("--summary", metavar="JSON", help="also write a short analysis summary JSON here")
    ap.add_argument("--voice", help="narration WAV to duck under (same as src.mix.mix --voice)")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    from src.analyze.pipeline import run
    run(a.repo, diff=a.diff, rev=a.rev, max_bars=a.max_bars, include_js=not a.no_js,
        render=not a.midi_only, fallback=a.fallback, out=a.out, voice=a.voice,
        heat=a.heat, summary_out=a.summary)


if __name__ == "__main__":
    main()
