---
type: Plan
title: 可编辑乐谱与MIDI回渲染
description: 生成总谱与分谱，并把人工编辑的 MIDI 接回故事渲染。
resource: docs/plans/Archive/2026/10-10/02-可编辑乐谱与midi回渲染.md
tags: [计划]
generated: { by: plan-docs/v2, at: "2026-10-10T06:31:23-07:00" }
status: stable
sources:
  - id: design
    resource: docs/story-scoring.md
    title: 乐谱交付与人工修改
---

# 可编辑乐谱与MIDI回渲染

设计与取舍见[乐谱交付与人工修改](../../../../story-scoring.md#乐谱交付与人工修改)。

## Context

用户认可乐理优化版的音频，希望获得能交给专业人员修改、再次渲染的乐谱。

## Approach

复用故事的音符和时间轴，用 music21 输出 MusicXML，用可选 verovio 输出可打印预览；通过有校验的 MIDI 导入复用原有音源与混音。

## Files

- `src/types/music.py`、`src/story/compose.py`：记谱时值与演奏时值分开。
- `src/story/notation.py`：总谱、分谱、乐器清单和交接说明。
- `src/story/midi_import.py`、`src/score/write_score.py`：校验编辑稿并保留 MIDI 表情事件。
- `src/story/pipeline.py`、`musician/cli.py`：接通命令行交付流程。
- `tests/test_story_notation.py`、README、依赖锁：回归验证与使用说明。

## Reuse

- `Score` / `Note` / `TimedNote` / `StoryManifest`：同一份音乐数据。
- `profile` / `midi_files` / `render_part` / `mix.main`：保持音源路由及混音行为。

## 实施

- [x] 实现并验证 MusicXML 总谱、分谱、可打印预览与乐器清单。
- [x] 实现并验证编辑 MIDI 导入、时序检查和再次渲染。
- [x] 为当前作品生成交接包，核对原版一致性、预览和完整测试；更新文档。

## Verification

- `make test`：保留已有测试，增加谱面解析、跨小节连结、打击乐、MIDI 修改往返与非法输入测试。
- 《原来是你》保留 58 小节、23 声部、1,016 个音符及原始演奏数据。
- 实际生成打印预览并查看代表性页面；实际改一段 MIDI 再渲染短音频。
- 原先 `out/story/originally-you-theory/` 保持不变，交付写入新目录。
