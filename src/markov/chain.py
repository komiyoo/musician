"""一阶马尔科夫链（状态 = MIDI 音高），只用标准库 + mido。

  seq   = sequence_from_midi(mid_bytes, channel=1)    # 按起音时间排序的音高序列（和弦内由低到高）
  mc    = MarkovChain.fit(seq)                         # 统计 a→b 次数
  P     = mc.matrix()                                  # (states, P)，P[i][j] = count(i→j) / Σ_j count(i→j)
  graph = mc.graph()                                   # {"nodes":[{pitch,name,count}], "edges":[{from,to,prob,count}]}
  nxt   = mc.sample_next(cur, rng, temperature=0.9)    # p_j ∝ P[cur][j] ** (1/T)

温度 T：T→0 几乎总走最常见的转移（贴近原曲），T=1 按原始概率，T>1 更平均、更「出格」；
只会走原曲里出现过的转移（概率 0 的边永远是 0），所以变奏仍在原来的调式和音域里。
"""
from __future__ import annotations

import io
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

import mido

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(p: int) -> str:
    return f"{NAMES[p % 12]}{p // 12 - 1}"


def notes_from_midi(src: bytes | str | Path, channel: int | None = None, track_name: str | None = None
                    ) -> list[tuple[int, int, int]]:
    """MIDI → [(start_tick, pitch, velocity)]，按 (起音, 音高) 排序。channel / track_name 用来只取一个声部。"""
    mf = mido.MidiFile(file=io.BytesIO(src)) if isinstance(src, (bytes, bytearray)) else mido.MidiFile(src)
    out = []
    for tr in mf.tracks:
        name = next((m.name for m in tr if m.type == "track_name"), None)
        if track_name is not None and name != track_name:
            continue
        t = 0
        for m in tr:
            t += m.time
            if m.type == "note_on" and m.velocity > 0 and (channel is None or m.channel == channel):
                out.append((t, m.note, m.velocity))
    out.sort()
    return out


def sequence_from_midi(src, channel: int | None = None, track_name: str | None = None) -> list[int]:
    return [p for _, p, _ in notes_from_midi(src, channel, track_name)]


class MarkovChain:
    def __init__(self):
        self.counts: dict[int, Counter] = defaultdict(Counter)
        self.state_counts: Counter = Counter()

    @classmethod
    def fit(cls, *sequences: list[int]) -> "MarkovChain":
        mc = cls()
        for seq in sequences:
            mc.state_counts.update(seq)
            for a, b in zip(seq, seq[1:]):
                mc.counts[a][b] += 1
        return mc

    @property
    def states(self) -> list[int]:
        return sorted(self.state_counts)

    def probs(self, a: int) -> dict[int, float]:
        row = self.counts.get(a)
        if not row:
            return {}
        tot = sum(row.values())
        return {b: c / tot for b, c in row.items()}

    def matrix(self) -> tuple[list[int], list[list[float]]]:
        st = self.states
        idx = {p: i for i, p in enumerate(st)}
        P = [[0.0] * len(st) for _ in st]
        for a in st:
            for b, pr in self.probs(a).items():
                P[idx[a]][idx[b]] = pr
        return st, P

    def graph(self, max_edges: int = 200) -> dict:
        nodes = [{"pitch": p, "name": note_name(p), "count": self.state_counts[p]} for p in self.states]
        edges = [{"from": a, "to": b, "prob": round(pr, 4), "count": self.counts[a][b]}
                 for a in self.states for b, pr in self.probs(a).items()]
        edges.sort(key=lambda e: (-e["prob"] * e["count"], e["from"], e["to"]))
        n_tr = sum(sum(r.values()) for r in self.counts.values())
        return {"nodes": nodes, "edges": edges[:max_edges], "n_states": len(nodes), "n_edges": len(edges),
                "n_transitions": n_tr}

    def sample_next(self, cur: int, rng: random.Random, temperature: float = 1.0,
                    weight=None) -> int | None:
        """下一个音高；weight(pitch)→float 可额外加权（例如和弦内音）。没有出边时返回 None。"""
        pr = self.probs(cur)
        if not pr:
            return None
        cands = sorted(pr)
        if temperature <= 1e-3:                      # 贪心
            return max(cands, key=lambda b: (pr[b] * (weight(b) if weight else 1.0), -b))
        w = [math.exp(math.log(pr[b]) / temperature) * (weight(b) if weight else 1.0) for b in cands]
        return rng.choices(cands, weights=w)[0]
