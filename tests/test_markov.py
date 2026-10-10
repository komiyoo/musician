"""马尔科夫链：计数 / 转移矩阵 / 温度采样 / 变奏 / 致艾丽丝种子 MIDI（离线、无音频渲染）。"""
import random
import unittest

from src.markov import MarkovChain, sequence_from_midi
from src.markov.demo import motif_midi_bytes
from src.markov.vary import vary_score_part


class TestChain(unittest.TestCase):
    def test_counts_and_matrix(self):
        mc = MarkovChain.fit([60, 62, 60, 64, 60, 62])
        self.assertEqual(mc.counts[60][62], 2)
        self.assertEqual(mc.counts[60][64], 1)
        st, P = mc.matrix()
        self.assertEqual(st, [60, 62, 64])
        for row, p in zip(P, st):
            self.assertAlmostEqual(sum(row), 1.0 if mc.counts.get(p) else 0.0)
        self.assertAlmostEqual(P[0][1], 2 / 3)
        g = mc.graph()
        self.assertEqual({n["pitch"] for n in g["nodes"]}, {60, 62, 64})
        self.assertTrue(all(0 < e["prob"] <= 1 for e in g["edges"]))

    def test_temperature(self):
        mc = MarkovChain.fit([60, 62] * 9 + [60, 64])
        rng = random.Random(1)
        self.assertEqual({mc.sample_next(60, rng, 0.0) for _ in range(50)}, {62})        # greedy
        hot = [mc.sample_next(60, rng, 3.0) for _ in range(400)]
        self.assertGreater(hot.count(64), 40)                                           # flatter
        self.assertTrue(set(hot) <= {62, 64})                                           # only seen transitions

    def test_fur_elise_seed(self):
        seq = sequence_from_midi(motif_midi_bytes())
        self.assertEqual(seq[:5], [76, 75, 76, 75, 76])
        g = MarkovChain.fit(seq).graph()
        self.assertGreaterEqual(g["n_states"], 8)
        self.assertGreater(g["n_edges"], 10)

    def test_vary_score_part_keeps_rhythm(self):
        from src.feel import compose as CP
        from src.feel.spec import build_spec, parse_feel
        spec = build_spec("悬疑科技", parse_feel("悬疑科技")[0], preview=True)
        score = CP.compose(spec)
        before = [(n.start, n.dur, n.vel) for n in score.notes if n.part == "piano"]
        pitches = [n.pitch for n in score.notes if n.part == "piano"]
        info = vary_score_part(score, "piano", temperature=1.5, seed=3)
        after = [(n.start, n.dur, n.vel) for n in score.notes if n.part == "piano"]
        self.assertEqual(before, after)
        self.assertGreater(info["changed"], 0)
        self.assertTrue({n.pitch for n in score.notes if n.part == "piano"} <= set(pitches))


if __name__ == "__main__":
    unittest.main()
