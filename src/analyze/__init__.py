"""analyze/ — 把真实代码结构映射成 MIDI 音符事件，再交给已有的 render → mix 流水线。

  metrics.py   Python AST（+ 简易 JS）静态分析：嵌套深度、圈复杂度、import、注释率、控制流密度、重复片段
  diffscan.py  git diff → 变更块（hunk）
  mapping.py   指标 / hunk → 音符事件（D 小调 · 100 BPM，与主流水线的声部和响度目标兼容）
  pipeline.py  写 midi/analyze/*.mid → render_all → mix → out/analyze.wav
"""
