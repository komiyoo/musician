#!/usr/bin/env python3
"""马尔科夫链冒烟测试：起一个 --fallback 网页服务 → 生成试听 → 断言图有节点/边 → 马尔科夫变奏 → 致艾丽丝示例
→（--ui，需要 playwright + Chrome）无头浏览器打开页面、点按钮、确认没有 JS 报错且 SVG 画出了图。

  python scripts/smoke_markov.py                 # API 部分（只用标准库）
  uv run --with playwright python scripts/smoke_markov.py --ui     # 加上无头浏览器（playwright 不进项目依赖）
  python scripts/smoke_markov.py --url http://127.0.0.1:8765      # 打已经在跑的服务
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def post(url, body, timeout=300):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def check_graph(g, what):
    assert g and g["nodes"] and g["edges"], f"{what}: graph empty: {g}"
    pitches = {n["pitch"] for n in g["nodes"]}
    assert all(e["from"] in pitches and e["to"] in pitches and 0 < e["prob"] <= 1 for e in g["edges"]), what
    print(f"[ok] {what}: {g['n_states']} nodes, {g['n_edges']} edges")


def ui(base):
    from playwright.sync_api import sync_playwright
    errors = []
    exe = next((p for p in (os.environ.get("CHROME"), shutil.which("google-chrome"), shutil.which("chromium"),
                            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome") if p and Path(p).exists()), None)
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, **({"executable_path": exe} if exe else {}))
        page = b.new_page()
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" and "Failed to load resource" not in m.text else None)   # HTTP errors: see below
        page.on("response", lambda r: errors.append(f"HTTP {r.status} {r.url}")
                if r.status >= 400 and not r.url.endswith("/favicon.ico") else None)
        page.goto(base + "/")
        page.wait_for_selector("#markov-card")
        page.check("#fallback") if not page.is_disabled("#fallback") else None
        page.click("#btn-elise")
        page.wait_for_selector("#markov-svg circle", timeout=30000)
        n_elise = page.locator("#markov-svg circle").count()
        page.click("#btn-gen")
        page.wait_for_function("document.getElementById('player-card').hidden === false", timeout=300000)
        page.wait_for_function("!document.getElementById('btn-markov').disabled", timeout=30000)
        n_gen = page.locator("#markov-svg circle").count()
        page.click("#btn-markov")
        page.wait_for_function("document.getElementById('player-title').textContent.includes('马尔科夫')", timeout=300000)
        n_edges = page.locator("#markov-svg path.edge").count()
        status = page.text_content("#status")
        page.screenshot(path=str(ROOT / "out" / "web" / "smoke-markov.png"), full_page=True)
        b.close()
    assert not errors, errors
    assert n_elise > 0 and n_gen > 0 and n_edges > 0, (n_elise, n_gen, n_edges)
    print(f"[ok] UI: no JS errors; Für Elise graph {n_elise} nodes; preview graph {n_gen} nodes; {n_edges} edges drawn")
    print(f"[ok] UI status: {status}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="已在运行的服务（默认自己起一个 --fallback 服务）")
    ap.add_argument("--ui", action="store_true", help="无头浏览器检查（需要 playwright）")
    a = ap.parse_args()
    proc = None
    base = a.url
    if not base:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        proc = subprocess.Popen([sys.executable, "-m", "src.web.app", "--port", str(port), "--fallback"], cwd=ROOT,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
    try:
        d = post(base + "/api/generate", {"feel": "悬疑一点的科技解说", "fallback": True})
        check_graph(d["markov"], "preview piano graph")
        v = post(base + "/api/markov-variation", {"feel": d["spec"].get("text", ""), "knobs": d["knobs"],
                                                  "fallback": True, "temperature": 1.2, "seed": 7})
        check_graph(v["markov_source"], "variation source graph")
        assert v["variation"]["changed"] > 0 and v["audio"] and v["audio"] != d["audio"], v["variation"]
        print(f"[ok] variation: changed {v['variation']['changed']}/{v['variation']['n_notes']} notes, "
              f"re-rendered {v['rendered']}, reused {v['reused']} → {v['audio']}")
        e = post(base + "/api/markov-demo", {"temperature": 0.9, "seed": 1})
        check_graph(e["graph"], "Für Elise graph")
        for k in ("audio_original", "audio_variation"):
            urllib.request.urlopen(base + e[k], timeout=10).read(16)
        html = urllib.request.urlopen(base + "/", timeout=10).read().decode()
        assert "生成马尔科夫变奏" in html and "致艾丽丝示例" in html
        print("[ok] page has 生成马尔科夫变奏 / 致艾丽丝示例")
        if a.ui:
            ui(base)
    finally:
        if proc:
            proc.terminate()
    print("[ok] smoke-markov passed")


if __name__ == "__main__":
    main()
