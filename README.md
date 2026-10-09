# code-to-music · 用代码写一段科技解说铺底音乐

> **D 小调 · 100 BPM · 22 小节 · ≈58 秒** — 给中文科技解说视频用的「口播铺底」配乐，从乐谱到成品全部由代码生成。
> *Code-generated underscore for a Chinese tech-commentary video: score → MIDI → Surge XT / sfizz → pedalboard mix.*

核心思路：**作曲与音色分离**。先把音乐写成 MIDI（只有音高、时值、力度、表情控制），再给每个声部挑音源（合成器 or 采样），最后统一响度并加效果。改旋律不用动音色，换音色也不用动旋律。

```
                ┌──────────── 1. 作曲 Score ────────────┐
src/score/write_score.py  ──►  midi/full.mid + midi/<part>.mid  (+ build/score.json)
                                     │
            ┌────────────────────────┴─────────────────────────┐
   2a. 电子音色 Surge XT                               2b. 原声音色 sfizz + SFZ
   src/render/surge_render.py                          src/render/sfizz_render.py
   pad / arp / bass                                    piano / violin / viola / cello / drums
   presets/surge_presets.json                          instruments/*.sfz  (+ 采样包)
            └────────────────────────┬─────────────────────────┘
                                     ▼  build/stems/<part>.wav   （缺软件/采样时自动用 numpy 简易合成器兜底）
              3. 响度对齐 src/mix/loudness.py  （pyloudnorm, 每轨目标 LUFS）
                                     ▼
              4. 效果与总线 src/mix/fx.py + mix.py （pedalboard）
                                     ▼
                              out/final.wav / final.mp3
```

---

## 快速开始（不装任何采样也能出声）

```bash
git clone <this repo> && cd code-to-music
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

scripts/run_pipeline.sh            # 或: make all
# -> midi/*.mid, build/stems/*.wav, out/final.wav, out/final.mp3, out/final.report.json
```

没有 Surge XT / sfizz / 采样包时，第 2 步会自动退回到 `src/render/fallback_synth.py`（numpy 写的简易合成器），
**音符、渐慢、力度抖动、弦乐渐强渐弱和正式版完全一样**，只是音色是草稿级别——足够在剪辑软件里先对画面、对口播节奏。

分步运行：

```bash
python -m src.score.write_score        # 1. 作曲 → MIDI
python -m src.render.render_all        # 2. 渲染每个声部（自动选择 Surge/sfizz/兜底）
python -m src.render.render_all --fallback   #    强制全部用兜底合成器
python -m src.mix.mix                  # 3+4. 响度对齐 + 效果 + 母带 → out/final.wav
python -m src.mix.mix --voice 口播.wav  #    可选：按口播音量自动压低配乐（ducking）
```

## 四步流程 与 口播铺底 的对应关系

| 步骤 | 做什么 | 为什么这样适合「口播铺底」 |
|---|---|---|
| **1. 作曲 (mido → MIDI)** | 22 小节，三段：**引入**（1–6 小节，只有 pad + 钢琴）→ **展开**（7–18，加入弦乐、贝斯、琶音、鼓）→ **收束**（19–22，钢琴放慢、整体渐慢，终止在 Dm） | 引入段只给和声垫子，留出开场白；展开段靠**律动密度**（钢琴 8 分、琶音 16 分、军鼓后加）推进而不是靠音量；收束段渐慢配合结论/转场 |
| **2. 选音色 (Surge XT / sfizz)** | pad、arp、bass 用 Surge XT 合成器；钢琴用 Salamander 采样；弦乐用 VSCO-2；鼓用 Sam's Sonor | 慢起音的 pad、软化起音（0.28 s）的弦乐不会「咬」到人声的辅音 |
| **3. 响度对齐 (pyloudnorm)** | 每轨先对齐到目标响度：钢琴 −21、小提琴 −22、贝斯/鼓 −25、pad/琶音 −29 LUFS | 不管换什么音色/采样，层次关系都稳定：钢琴是主线，pad/arp 永远在后面 |
| **4. 效果 (pedalboard)** | 每轨高通、在 1.5–4 kHz 挖「人声口袋」、混响推远；总线轻压缩、母带 −18 LUFS、−1 dBFS 峰值 | 中文语音可懂度集中在 1–4 kHz，配乐在这里让位；−18 LUFS 的配乐放在 −16~−14 LUFS 的口播下面刚好 |

### 编曲细节（`src/score/write_score.py`）

- **调性 / 速度**：D 小调，100 BPM，4/4；第 19–22 小节渐慢 96→90→84→76 BPM，加 3.6 s 余响，总长 ≈ 58 s。
- **和声进行**（每小节一个和弦）：
  `Dm Bb F C Dm Bb | Dm Bb F C Gm Bb F A Dm Bb Gm A | Bb Gm Asus Dm`
  （i–VI–III–VII 的「科技感」循环，展开段加入 iv 和属和弦 A，收束 VI–iv–Vsus–i）。
- **密度层次**：pad 每小节 1 个和弦；钢琴每拍 2 个音（8 分音符分解和弦）；琶音每拍 4 个音（16 分）。
- **弦乐**：小提琴旋律（第 7 小节起）、中提琴内声部（三音+五音，二分音符）、大提琴根音（全音符）；
  CC11 表情控制 + 力度共同实现**渐强进入（7–10 小节）/ 渐弱退出（19–22 小节）**。
- **鼓**：第 1、3 拍底鼓；每半拍闭镲；11–14 小节加边击（rim），15–18 小节换成轻军鼓，18 小节末一个小加花进收束。
- **人性化**：每个音都有 ±4~8 的随机力度抖动（固定随机种子 `CTM_SEED`，可复现）。

---

## 新模式：`musician analyze` —— 让代码自己作曲

除了手写的 22 小节铺底，现在还可以把**任意代码仓库的真实结构**变成音乐（思路参考 repo2music），
或者把一次 **git diff 的变更块**变成一串短动机（思路参考 CodeSonify）。
analyze 层只负责「代码 → 音符事件」，后面完全复用已有的 **render → mix** 流水线：
同样的 D 小调、100 BPM、同样的声部名 / 响度目标 / 效果链，结尾同样是 A → Dm 终止 + 渐慢。

```
<repo> ──► src/analyze/metrics.py   Python AST（+ 简易 JS/TS 扫描）：嵌套深度、圈复杂度、import、
                │                    注释率、控制流密度、重复片段（跨文件相同的 3 行窗口）
                ▼
        src/analyze/mapping.py      指标 → 音符事件（复用 write_score 的 Score / 和弦 / pad 排列）
                ▼
        midi/analyze/<part>.mid + full.mid   build/analyze/analysis.json（指标 + 每段映射）
                ▼   复用 render_all（Surge XT / sfizz / numpy 兜底）+ mix（响度对齐 + 效果 + 母带）
        out/analyze.wav (+ .mp3, .report.json)
```

### 运行

```bash
pip install -e .                                   # 安装 `musician` 命令（或直接用 python -m）

musician analyze /path/to/repo                     # 整个仓库 → out/analyze.wav
musician analyze --diff /path/to/repo              # 未提交改动 vs HEAD（干净时用 HEAD~1..HEAD）→ out/analyze_diff.wav
musician analyze --diff --rev v1.0..main /path/to/repo   # 指定范围；--rev <commit> = 该提交相对父提交
python -m musician.analyze src                     # 等价写法：分析本项目自己的 src/
make analyze REPO=../my-project                    # Makefile 快捷方式（另有 make analyze-diff）

# 常用选项
--midi-only     只写 MIDI + analysis.json，不渲染
--fallback      强制 numpy 兜底合成器（不装 Surge/sfizz 也能出声）
--max-bars N    长度上限（默认整仓 48 小节 / diff 32 小节；100 BPM 下每小节 2.4 秒）
--no-js         只分析 .py
--voice 口播.wav 和主流程一样按口播自动避让
```

输出位置与主流程互不干扰：`midi/analyze/`、`midi/analyze/diff/`、`build/analyze/`、`out/analyze*.wav`。
`midi/*.mid`、`out/final.wav` 不会被覆盖。`midi/analyze/` 已加入 `.gitignore`；
`examples/analyze_self.mid` 是分析本项目 `src/` 得到的示例。

### 映射规则（整仓模式）

| 代码指标 | 音乐参数 | 听感 |
|---|---|---|
| 文件 | 一个段落，1–4 小节（∝ 非空行数），按路径顺序演奏；第 1 小节是只有 pad 的引子 | 大文件 = 长段落 |
| 函数 | 该文件段落里的一个**钢琴动机**，时间槽 ∝ 函数行数，起音从当前和弦音中按函数名哈希选取 | 每个函数有自己的「签名旋律」 |
| **嵌套深度** | 动机的**音区**：0 层 A3 → 1 层 D4 → 2 层 G4 → 3 层 C5 → ≥4 层 E5；力度随深度增加 | 嵌套越深越高越紧 |
| **圈复杂度** | 动机音符数（1 + (cx−1)/2，最多 8）；琶音**节奏密度**（cx<3 四分 / <6 八分 / ≥6 十六分）；cx≥6 段落加入 Gm、cx≥10 加入属和弦 A，且动机里插入半音摩擦、琶音每 4 个音换成小二度/三全音 | 复杂代码 = 更密、更不协和 |
| **import** | 段落开头的打击乐：标准库 → 边击（rim），第三方 → 底鼓，本地/相对导入 → 军鼓，八分音符排开 | 依赖多 = 开头一串鼓点 |
| **控制流密度**（每 10 行的 if/for/while/try/with/match 数） | 踩镲细分（无 / 四分 / 八分 / 十六分）+ 贝斯律动（全音符 / 二分 / 推进型） | 分支越多律动越碎 |
| **注释率**（注释 + docstring 行 / 非空行） | pad 的**力度**和**亮度**（CC74；兜底合成器里映射为 pad 低通截止 500–3800 Hz） | 文档写得好，和声更亮更饱满 |
| **重复片段**（同一 3 行窗口在仓库中出现多次） | 含重复的函数，其动机由小提琴高八度**卡农回声**；重复率 >5% 的文件段落以 Asus 结尾 | 复制粘贴 = 回声 |
| 类定义 | 大提琴持续根音 | |
| 语法错误 | 整段属和弦 + 不协和琶音 | |

### 映射规则（diff 模式）

- 每个变更块（hunk）→ ½ 小节（≤8 行）或 1 小节（>8 行）的短动机，按 diff 顺序排列；
- **新增行上行**：`.py` 用钢琴、`.js/.ts` 用琶音合成器、`.md/.txt` 用小提琴；音高步进来自该行文本的哈希，力度来自行长；
- **删除行下行**：大提琴低音区；删除多于新增的小节换成 Gm；
- 每进入一个新文件敲一下底鼓，每个 hunk 一下踩镲，改动涉及 `import` 时加边击；
- 超过 `--max-bars` 的 hunk 会被截掉并在终端提示。

> JS/TS 分析是无依赖的启发式扫描（去掉字符串/注释后数花括号和关键字），Python 分析用标准库 `ast` + `tokenize`，结果精确。

---

## 完整渲染（正式音色）

### 1) Surge XT（pad / arp / bass）

- 官网下载：<https://surge-synthesizer.github.io/>（Linux `.deb`、macOS、Windows 安装包；免费开源 GPL-3）
  - Debian/Ubuntu：`sudo apt install ./surge-xt-linux-x64-1.3.4.deb`
- 安装后 VST3 一般在 `/usr/lib/vst3/Surge XT.vst3`（macOS `/Library/Audio/Plug-Ins/VST3/`），脚本会自动寻找；
  其他位置请设置 `CTM_SURGE_PLUGIN="/path/to/Surge XT.vst3"`。
- **音色选择**：`presets/surge_presets.json` 里按名字写 factory patch，例如 `Pads/MKS-70 Warm Pad.fxp`。
  Surge XT 自带 3000+ 音色（含第三方库），用 `python scripts/list_surge_presets.py Pads` 浏览。
  详细说明见 [presets/README.md](presets/README.md)。
- 实现细节：pedalboard 不能按名字切换 Surge 音色，所以 `src/render/juce_state.py` 把 `.fxp` 的 patch chunk
  转成 VST3 `raw_state`，**无界面**也能加载任意 factory patch；如果编译了 Surge 的 Python 绑定 `surgepy`，会优先用它。

### 2) sfizz（piano / strings / drums 的采样播放器）

- 项目主页：<https://sfz.tools/sfizz/>，源码与发布：<https://github.com/sfztools/sfizz/releases>
- 需要命令行工具 **`sfizz_render`**（部分发行版的包里没有，可以从源码编译）：
  ```bash
  git clone --recursive -b 1.2.3 https://github.com/sfztools/sfizz.git
  cmake -S sfizz -B sfizz/build -DCMAKE_BUILD_TYPE=Release -DSFIZZ_RENDER=ON -DSFIZZ_JACK=OFF \
        -DSFIZZ_LV2=OFF -DSFIZZ_VST=OFF -DSFIZZ_SHARED=OFF
  cmake --build sfizz/build -j && sudo install sfizz/build/library/bin/sfizz_render /usr/local/bin/
  ```
  （需要 `cmake build-essential libsndfile1-dev`。不在 PATH 时可设 `CTM_SFIZZ_RENDER=/path/to/sfizz_render`）

### 3) 采样包（**不提交到仓库**，体积大，请自行下载）

一键下载到 `./samples`（或 `CTM_SAMPLES_DIR` 指定的目录）：

```bash
scripts/fetch_samples.sh all        # 或 drums / strings / piano
```

| 声部 | 采样包 | 许可 | 大小 | 下载 |
|---|---|---|---|---|
| 钢琴 | **Salamander Grand Piano V3**（SFZ+FLAC）by Alexander Holm | CC-BY 3.0 | ~700 MB 压缩包 | <https://freepats.zenvoid.org/Piano/acoustic-grand-piano.html> |
| 弦乐 | **VSCO-2 Community Edition** by Versilian Studios / Sam Gossner（只用 Strings 里的 susVib 文件夹） | CC0 | ~340 MB（稀疏检出） | <https://github.com/sgossner/VSCO-2-CE> · <https://versilian-studios.com/vsco-community/> |
| 鼓 | **Sam's Sonor**（Sonor Force 3001）by Sam Greene，SFZ 映射 by kinwie | CC-BY-SA 4.0 | ~70 MB | <https://github.com/sfzinstruments/SamsSonor> |

目录结构应为：

```
samples/
├── SalamanderGrandPiano-SFZ+FLAC-V3+20200602/SalamanderGrandPiano-V3+20200602.sfz
├── VSCO-2-CE/Strings/{Violin Section/susVib, Viola Section/susvib, Cello Section/susvib}/*.wav
└── SamsSonor/SamsSonor.sfz
```

> 使用这些采样渲染出的音频请按各自许可署名（见下方 Credits）。

### 4) SFZ 音色定义（`instruments/*.sfz`）

`instruments/` 里是**我们自己的 SFZ 覆盖层**——纯文本、可直接编辑：

| 文件 | 起音 ampeg_attack | 其他 |
|---|---|---|
| `piano.sfz` | 0.012 s（把 ~1 ms 的琴槌瞬态软化） | 低通 5.2 kHz + 力度跟随、温和的力度曲线 |
| `strings_common.sfz` → `violin/viola/cello.sfz` | **0.28 s**（弓子慢慢「拉进来」） | `amplitude_oncc11` 接 CC11 渐强渐弱、力度→亮度 |
| `drums.sfz` | 0.001 s（保留鼓的瞬态） | 柔和力度曲线、9 kHz 低通避开齿音 |

`src/render/sfz_prepare.py` 会把采样包自带的映射（或按 VSCO 文件名自动生成的 region）与这些覆盖层合并，
输出 `build/sfz/<part>.sfz` 给 sfizz 渲染。**覆盖层里出现的 opcode 会替换采样包里所有同名 opcode**
（否则采样包在 region 上写的 `ampeg_attack` 会让 `<global>` 的设置失效）。

---

## 目录结构

```
├── README.md                 本文件
├── requirements.txt / pyproject.toml / Makefile
├── src/
│   ├── config.py             路径、曲式、速度表、声部、响度目标、采样包位置
│   ├── score/write_score.py  作曲：22 小节全部音符 → midi/*.mid
│   ├── render/
│   │   ├── surge_render.py   Surge XT（surgepy 或 pedalboard+VST3）
│   │   ├── juce_state.py     .fxp → VST3 raw_state（无界面切换 Surge 音色）
│   │   ├── sfz_prepare.py    合并采样包映射 + instruments/*.sfz
│   │   ├── sfizz_render.py   调用 sfizz_render CLI
│   │   ├── fallback_synth.py numpy 兜底合成器
│   │   ├── midi_io.py        MIDI 读取 / stem 写出
│   │   └── render_all.py     渲染全部声部
│   ├── mix/
│   │   ├── loudness.py       pyloudnorm 每轨响度对齐（LUFS 或 RMS dBFS）
│   │   ├── fx.py             每轨 pedalboard 效果链 + 母带限幅
│   │   └── mix.py            总混 → out/final.wav
│   └── analyze/              代码 → 音乐（musician analyze）
│       ├── metrics.py        Python AST / JS 启发式指标 + 重复片段检测
│       ├── diffscan.py       git diff → hunk
│       ├── mapping.py        指标 / hunk → 音符事件
│       └── pipeline.py       写 midi/analyze → render → mix → out/analyze.wav
├── musician/                 `musician` 命令行入口（cli.py；python -m musician.analyze）
├── examples/analyze_self.mid 分析本项目 src/ 生成的示例 MIDI
├── instruments/*.sfz         SFZ 覆盖层（起音/滤波/力度曲线）
├── presets/                  Surge XT 音色选择（surge_presets.json + 说明）
├── scripts/
│   ├── run_pipeline.sh       一键运行
│   ├── fetch_samples.sh      下载采样包
│   ├── list_surge_presets.py 浏览 Surge 音色
│   └── capture_surge_state.py 在 Surge 界面里挑音色并保存
└── midi/                     生成的 MIDI（已提交，方便直接拖进 DAW）
```

## 常用调整

- **改旋律/和声**：`src/score/write_score.py` 里的 `PROGRESSION`、`MELODY`、`PAD_VOICINGS`。
- **改长度/速度/渐慢**：`src/config.py` 的 `BPM`、`RIT_BPM`、`TAIL_SECONDS`。
- **换音色**：`presets/surge_presets.json`（电子）或 `instruments/*.sfz`（采样）。
- **调层次**：`src/config.py` 的 `LOUDNESS_TARGETS`、`MASTER_TARGET_LUFS`；
  效果链在 `src/mix/fx.py`。`python -m src.mix.mix --mode dbfs` 改用 RMS dBFS 对齐。
- **配合口播自动避让**：`python -m src.mix.mix --voice narration.wav`（包络跟随，人声出现时配乐压低约 7 dB）。
- 每次混音会写 `out/final.report.json`：每轨实际用的引擎、测得响度、增益，以及母带 LUFS / 峰值。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `CTM_SAMPLES_DIR` | `./samples` | 采样包目录 |
| `CTM_SURGE_PLUGIN` | 自动查找 | `Surge XT.vst3` 路径 |
| `CTM_SURGE_FACTORY` | 自动查找 | Surge `patches_factory` 目录 |
| `CTM_SFIZZ_RENDER` | `sfizz_render` | sfizz 渲染器可执行文件 |
| `CTM_SAMPLE_RATE` | `48000` | 采样率（视频常用 48 kHz） |
| `CTM_SEED` | `20261009` | 力度抖动随机种子 |

## Credits / 致谢

- **Surge XT** — Surge Synth Team, GPL-3.0 — <https://surge-synthesizer.github.io/>
- **sfizz** — SFZ Tools, BSD-2-Clause — <https://sfz.tools/sfizz/>
- **Salamander Grand Piano V3** — Alexander Holm, CC-BY 3.0（SFZ/FLAC 版本 by roberto@zenvoid.org）
- **VSCO-2 Community Edition** — Versilian Studios / Sam Gossner, CC0
- **Sam's Sonor** — Sam Greene, CC-BY-SA 4.0；SFZ 映射 by kinwie（sfzinstruments）
- **pedalboard** — Spotify, GPL-3.0；**pyloudnorm** — Christian Steinmetz, MIT；**mido** — MIT

本仓库代码以 **MIT** 许可发布。采样包不包含在仓库中，各自遵循原许可。

---

### English summary

`code-to-music` generates a 58-second D-minor, 100 BPM narration bed in four steps: (1) compose 22 bars
(intro: pad + piano; develop: + strings, bass, arp, drums; resolve: ritardando, cadence on Dm) into
per-part MIDI with mido; (2) render pad/arp/bass with **Surge XT** (headless factory-patch loading via
pedalboard or surgepy) and piano/strings/drums with **sfizz** using editable SFZ override layers
(0.28 s string attack); (3) loudness-align each stem with pyloudnorm (piano −21, violin −22,
bass/drums −25, pad/arp −29 LUFS); (4) per-track pedalboard FX with a 1.5–4 kHz "voice pocket",
master to −18 LUFS. Without Surge/sfizz/samples, a numpy fallback synth renders the same MIDI, so
`scripts/run_pipeline.sh` always produces `out/final.wav`. Sample packs are not committed — run
`scripts/fetch_samples.sh`.

**New: `musician analyze <repo>`** maps real code structure to MIDI and feeds the same render → mix
pipeline (D minor, 100 BPM): files → sections, functions → piano motifs, nesting depth → register,
cyclomatic complexity → motif length / arp density / harmonic tension, imports → percussion
(stdlib rim, third-party kick, local snare), control-flow density → hi-hat subdivision, comment ratio →
pad velocity & brightness (CC74), repeated snippets → violin canon echo. `musician analyze --diff <repo>`
turns git diff hunks into short motifs (added lines ascend, removed lines descend on cello).
Output: `midi/analyze/*.mid`, `build/analyze/analysis.json`, `out/analyze.wav`.
