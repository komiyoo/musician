"""feel 模式：一句「感觉」/口播稿 + 几个旋钮 → 编曲规格 → MIDI → 渲染 + 混音（给不懂乐理的人用）。

  spec.py     文字 + 旋钮 → ArrangementSpec（调性、速度、段落、声部、亮度、避让口播）
  compose.py  ArrangementSpec → 音符（复用 write_score 的 Score / 和弦 / MIDI 写出）
  render.py   按声部内容哈希缓存渲染结果，只重渲变化的声部，再混音 → WAV/MP3
"""
