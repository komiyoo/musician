"""`musician` console script (installed by `uv sync`, or `pip install -e .`)."""
from __future__ import annotations

import argparse
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    from src.envfile import load_env
    load_env()                                 # local .env (gitignored) → os.environ; shell exports win
    ap = argparse.ArgumentParser(prog="musician", description="code-to-music: 用代码写音乐")
    sub = ap.add_subparsers(dest="cmd", required=True)
    from src.analyze.__main__ import build_parser
    build_parser(sub.add_parser("analyze", help="代码结构 / git diff → MIDI → 混音"))
    from src.story.pipeline import build_parser as story_parser
    story_parser(sub.add_parser("story", help="故事 JSON → 叙事配乐 / MIDI / 同步视频"))
    sub.add_parser("score", help="1. 作曲 → midi/*.mid")
    r = sub.add_parser("render", help="2. MIDI → build/stems/*.wav")
    r.add_argument("--fallback", action="store_true")
    m = sub.add_parser("mix", help="3. 响度对齐 + 效果 → out/final.wav")
    m.add_argument("--voice")
    al = sub.add_parser("all", help="score + render + mix")
    al.add_argument("--fallback", action="store_true")
    sv = sub.add_parser("serve", help="启动网页界面（一句话 + 旋钮 → 配乐）", add_help=False)
    sv.add_argument("rest", nargs=argparse.REMAINDER)
    if argv and argv[0] == "serve":            # let src.web.app own its flags (--port/--host/--fallback)
        from src.web.app import main as serve
        serve(argv[1:])
        return
    a = ap.parse_args(argv)

    if a.cmd == 'story':
        import json
        import subprocess
        from src.story.pipeline import run
        from src.types import StorySpec
        if a.schema:
            schema = {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                      '$comment': 'Generated from src/types/story.py; regenerate with musician story --schema.',
                      **StorySpec.model_json_schema()}
            print(json.dumps(schema, ensure_ascii=False, indent=2))
            return
        if not a.spec:
            ap.error('story requires a JSON spec or --schema')
        try:
            return run(a.spec, a.out, fallback=a.fallback, strict=a.strict, midi_only=a.midi_only,
                       video=a.video, soundfont=a.soundfont, voice=a.voice, font=a.font,
                       midi=a.midi, sheet_preview=a.sheet_preview)
        except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
            ap.exit(1, f'[story] {error}\n')

    if a.cmd == "analyze":
        from src.analyze.pipeline import run
        run(a.repo, diff=a.diff, rev=a.rev, max_bars=a.max_bars, include_js=not a.no_js,
            render=not a.midi_only, fallback=a.fallback, out=a.out, voice=a.voice,
            heat=a.heat, summary_out=a.summary)
        return
    from src.mix import mix
    from src.render import render_all
    from src.score import write_score
    if a.cmd in ("score", "all"):
        write_score.main()
    if a.cmd in ("render", "all"):
        render_all.main(["--fallback"] if a.fallback else [])
    if a.cmd in ("mix", "all"):
        mix.main(["--voice", a.voice] if getattr(a, "voice", None) else [])


if __name__ == "__main__":
    main()
