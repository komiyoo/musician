"""用马尔科夫链给一个声部做变奏：保留原来的节奏、时值、力度，只按链重新采样音高。

- 链从该声部自己的 MIDI 音符序列统计（所以变奏还在原调、原音域里）；
- 单音逐个采样：下一个音 ~ P[上一个音] ** (1/T)，再乘「和弦内音」权重（同小节原来出现过的音级 ×harmony），
  避免分解和弦和其他声部打架；
- 同时起音的和弦（结尾琶音和弦等）保持原样，只把链的当前状态挪到和弦最高音。
"""
from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import replace

from src.markov.chain import MarkovChain


def vary_notes(notes: list, chain: MarkovChain, temperature: float = 0.9, seed: int = 0,
               harmony: float = 3.0, beats_per_bar: int = 4) -> tuple[list[int], int]:
    """notes: 有 .start(拍) .pitch 的对象列表（按 start, pitch 排）。返回 (新音高列表, 改动的音数)。"""
    rng = random.Random(seed)
    groups: dict[float, list[int]] = defaultdict(list)
    for i, n in enumerate(notes):
        groups[round(n.start, 4)].append(i)
    bar_pcs: dict[int, set[int]] = defaultdict(set)
    for n in notes:
        bar_pcs[int(n.start // beats_per_bar)].add(n.pitch % 12)

    new = [n.pitch for n in notes]
    cur = None
    changed = 0
    for start in sorted(groups):
        idx = groups[start]
        if len(idx) > 1 or cur is None:              # 和弦 / 第一个音：保留原样
            cur = max(new[i] for i in idx)
            continue
        i = idx[0]
        pcs = bar_pcs[int(start // beats_per_bar)]
        nxt = chain.sample_next(cur, rng, temperature, weight=lambda p: harmony if p % 12 in pcs else 1.0)
        if nxt is None:                              # 死胡同：回到原音
            nxt = notes[i].pitch
        changed += nxt != notes[i].pitch
        new[i] = cur = nxt
    return new, changed


def vary_score_part(score, part: str, temperature: float = 0.9, seed: int = 0, harmony: float = 3.0) -> dict:
    """就地改 score 里某个声部的音高（src.score.write_score.Score），返回 {chain, changed, n_notes}。"""
    idx = sorted((i for i, n in enumerate(score.notes) if n.part == part),
                 key=lambda i: (score.notes[i].start, score.notes[i].pitch))
    notes = [score.notes[i] for i in idx]
    chain = MarkovChain.fit([n.pitch for n in notes])
    pitches, changed = vary_notes(notes, chain, temperature, seed, harmony)
    for i, p in zip(idx, pitches):
        score.notes[i] = replace(score.notes[i], pitch=int(p))
    return {"chain": chain, "changed": changed, "n_notes": len(notes)}
