---
type: Plan
title: narrative-scoring
description: 实现七段叙事配乐、23 声部、统一时间轴与同步视频，并修复现有渲染一致性问题。
resource: docs/plans/Archive/2026/10-10/00-narrative-scoring.md
tags: [计划]
generated: { by: plan-docs/v2, at: "2026-10-10T00:56:54-07:00" }
status: stable
sources:
  - id: design
    resource: docs/story-scoring.md
    title: 叙事配乐设计
---

# narrative-scoring

设计与边界见[叙事配乐 · 功能范围](../../../../story-scoring.md#功能范围)。[^design]

## Context

现有 MIDI、Surge/sfizz、混音已能完成 8 声部草稿，但文字关键词不能表达剧情顺序。上一轮实测发现时长截断、调性元数据不符、音色缓存缺少音源信息及 surgepy 丢弃 CC。

## Approach

先修共用时间与 MIDI，再加入经过校验的故事规格、配器和渲染，最后从同一份乐谱与音频生成视频。数据契约见[数据与时间](../../../../story-scoring.md#数据与时间)，音源边界见[渲染与混音](../../../../story-scoring.md#渲染与混音)。

## Files and reuse

- `src/config.py`、`src/score/write_score.py`：复用 Score、Note、CC 与分轨 MIDI 写出。
- `src/types/`、`src/story/`：故事契约、确定性编曲、隔离任务输出。
- `src/render/`、`src/feel/render.py`：复用原音源，补 FluidSynth、缓存依赖与控制器。
- `src/mix/`、`src/story/video.py`：复用混音效果与响度测量，新增同步视频。
- `musician/cli.py`、`examples/`、`tests/`、`README.md`：命令、示例与验收。

## Steps

### 1. Shared correctness

- [x] 先复现并修复时长、MIDI 调性、surgepy 控制器和音源缓存问题；新增统一微秒速度时间轴，运行回归测试。

验证：新增 5 项回归在修改前全部失败，修复后通过；原有 26 项测试在集成检查中通过。

### 2. Narrative score

- [x] 实现单一故事 schema、乐器目录、主题变形与和声分工；交付 58 小节 / 23 声部 / 七段 / 19 字幕示例，测试非法输入、主题与时间落点。

验证：故事测试通过；58 小节、23 个有音符的声部、19 字幕及 186 秒时间轴符合要求，Boss 主题移低八度并延长一倍，认出时返回原主题。

### 3. Rendering and CLI

- [x] 接通 story CLI、分轨 MIDI 端口、SFZ/SF2/Surge/草稿渲染、正式音源严格检查、独立输出及配置恢复；验证全部声部有声。

验证：38 项阶段测试通过；23 声部草稿音色均非静音，MIDI 端口独立，失败后配置恢复，已有输出不会覆盖。FluidSynth 使用模拟 CLI 验证参数与音频读取；完整 186 秒渲染通过，结果在交付项记录。

### 4. Video

- [x] 实现双语轨道、音块、叙事文字、真实音频频谱与流式 MP4 编码；检查帧内容、音画时长和失败清理。

验证：真实 FFmpeg 的 2 秒音视频测试通过；频谱能区分静音和 440 Hz 音频；已查看完整乐谱的 Boss 与放手画面，双语轨道、字幕、当前音符与频谱显示正常。完整视频的媒体信息验证结果在交付项记录。

### 5. Delivery

- [x] 更新使用文档与生成 schema，执行全套测试和完整样例音视频渲染，记录结果与正式音源验证限制，完成文档归档。

交付验证：`uv run --frozen make test` 的 42 项测试全部通过，包括短曲 CLI 音视频和编码失败清理；既有网页生成、马尔科夫变奏、分轨缓存和示例下载冒烟通过。锁定依赖的可编辑安装、控制台命令和 schema 一致性检查通过。

完整示例已渲染：58 小节、23 声部、1,105 音符、19 字幕，48 kHz 音频 186 秒、−18 LUFS、峰值 −4.7 dBFS；MP4 为 1280×720、30 fps、5,580 帧、186 秒，音画误差小于一帧。已查看 Boss 和放手代表帧。

可审阅产物位于 `out/story/originally-you-demo/`（不提交二进制）。实际音源为 numpy 草稿；Surge/sfizz/FluidSynth 与第三方音色的真实渲染及最终音乐听感仍需安装对应资产后验收。

## Verification

- `python -m unittest discover -s tests -v`。
- `python -m musician story examples/originally-you.json --midi-only --out <temporary-directory>`。
- `python -m musician story examples/originally-you.json --fallback --video --out <temporary-directory>`。
- 检查 58 小节、23 轨、19 字幕、186 秒；27/44/64/84/108/133 秒转场误差不超过一帧。
- 使用 `ffprobe` 检查音视频流，用 Pillow 检查代表帧；真实音源未安装时不得宣称音色验证通过。

[^design]: 叙事配乐设计。
