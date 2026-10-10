"""马尔科夫链：从 MIDI 音符序列统计音高转移 → 转移矩阵 P → 图 JSON / 温度采样变奏。"""
from src.markov.chain import MarkovChain, note_name, sequence_from_midi  # noqa: F401
