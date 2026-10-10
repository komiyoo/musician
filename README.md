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

依赖用 [uv](https://docs.astral.sh/uv/) 管理：`pyproject.toml` 是唯一来源，`uv.lock` 锁定版本（已提交）。

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # 没装 uv 时（macOS 也可 brew install uv）
git clone <this repo> && cd code-to-music
uv sync --extra web                # 建 .venv，按 uv.lock 安装依赖 + 本项目（含 `musician` 命令、网页界面依赖）；或: make sync
                                   # 只要命令行、不要网页界面：uv sync

uv run musician all                # 作曲 → 渲染 → 混音；或: uv run make all  /  uv run scripts/run_pipeline.sh
# -> midi/*.mid, build/stems/*.wav, out/final.wav, out/final.mp3, out/final.report.json
```

- `uv run <命令>` 会自动保持 .venv 与 uv.lock 同步，无需手动 activate；也可以 `. .venv/bin/activate` 后直接用 `musician` / `make`。
- 改依赖：编辑 `pyproject.toml`（或 `uv add 包名` / `uv add --optional web 包名`）→ `uv lock` → `make requirements`。
- 不用 uv？`requirements.txt` / `requirements-web.txt` 由 uv.lock 导出（`make requirements`），仍可
  `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt && pip install -e .`。

没有 Surge XT / sfizz / 采样包时，第 2 步会自动退回到 `src/render/fallback_synth.py`（numpy 写的简易合成器），
**音符、渐慢、力度抖动、弦乐渐强渐弱和正式版完全一样**，只是音色是草稿级别——足够在剪辑软件里先对画面、对口播节奏。

分步运行：

```bash
uv run musician score                  # 1. 作曲 → MIDI          （= uv run python -m src.score.write_score / make score）
uv run musician render                 # 2. 渲染每个声部（自动选择 Surge/sfizz/兜底）（= python -m src.render.render_all）
uv run musician render --fallback      #    强制全部用兜底合成器  （= make render-fallback）
uv run musician mix                    # 3+4. 响度对齐 + 效果 + 母带 → out/final.wav（= python -m src.mix.mix）
uv run musician mix --voice 口播.wav    #    可选：按口播音量自动压低配乐（ducking）
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
uv sync                                            # 安装依赖 + `musician` 命令（之后用 uv run musician …；或 activate .venv 后直接 musician …）

musician analyze /path/to/repo                     # 整个仓库 → out/analyze.wav
musician analyze --diff /path/to/repo              # 未提交改动 vs HEAD（干净时用 HEAD~1..HEAD）→ out/analyze_diff.wav
musician analyze --diff --rev v1.0..main /path/to/repo   # 指定范围；--rev <commit> = 该提交相对父提交
python -m musician.analyze src                     # 等价写法：分析本项目自己的 src/
uv run make analyze REPO=../my-project             # Makefile 快捷方式（另有 make analyze-diff，默认带 --heat）
uv run make demo-analyze                           # 演示：分析本项目 src/ → out/analyze.wav (+ .mp3) + out/analysis.json

# 常用选项
--midi-only     只写 MIDI + analysis.json，不渲染
--fallback      强制 numpy 兜底合成器（不装 Surge/sfizz 也能出声）
--max-bars N    长度上限（默认整仓 48 小节 / diff 32 小节；100 BPM 下每小节 2.4 秒）；硬上限，超出时保留「最热」的文件 / hunk
--heat          diff 模式：加一条随改动密度起伏的中提琴 heat 轨
--summary F     额外写一份精简摘要 JSON（形式、协和/不协和段落统计、最热文件）
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
| **圈复杂度** | 动机音符数（1 + (cx−1)/2，最多 8）；琶音**节奏密度**（cx<3 四分 / <6 八分 / ≥6 十六分）；同时参与下面的「张力」 | 复杂代码 = 更密 |
| **张力 t**（复杂度 + 嵌套 + 注释率，见下节） | 中提琴和声音程（3/5/6 度 ↔ 小二度/三全音）、和弦（t≥0.45 偶数小节 Gm，t≥0.70 段尾属和弦 A）、琶音半音替换频率、各声部力度与断奏程度 | 好读的代码协和，难读的代码刺耳 |
| **import** | 段落开头的打击乐：标准库 → 边击（rim），第三方 → 底鼓，本地/相对导入 → 军鼓，八分音符排开 | 依赖多 = 开头一串鼓点 |
| **控制流密度**（每 10 行的 if/for/while/try/with/match 数） | 踩镲细分（无 / 四分 / 八分 / 十六分）+ 贝斯律动（全音符 / 二分 / 推进型） | 分支越多律动越碎 |
| **注释率**（注释 + docstring 行 / 非空行） | pad 的**力度**和**亮度**（CC74；兜底合成器里映射为 pad 低通截止 500–3800 Hz） | 文档写得好，和声更亮更饱满 |
| **重复片段**（同一 3 行窗口在仓库中出现多次） | 含重复的函数，其动机由小提琴高八度**卡农回声**；重复率 >5% 的文件段落以 Asus 结尾 | 复制粘贴 = 回声 |
| 类定义 | 大提琴持续根音 | |
| 语法错误 | 整段属和弦 + 不协和琶音 | |

### 协和度层（codephon 思路）

每个文件 / 函数算一个**张力** `t ∈ [0,1]`（越大越「拧巴」）：

```
t = 0.45 · clamp((圈复杂度 − 2) / 10)      # 复杂度 2 → 0，12 → 1
  + 0.35 · clamp((嵌套深度 − 1) / 4)       # ≤1 层 → 0，5 层 → 1
  + 0.20 · clamp(1 − 注释率 / 0.30)        # 注释 ≥30% → 0
```

函数动机用 `0.5·t(文件) + 0.5·t(函数)`。每个钢琴动机音下方配一个**中提琴**声部：

| t | 色彩 | 音程（D 小调） | 演奏 |
|---|---|---|---|
| < 0.35 | consonant | 自然音阶内的三度 / 六度 / 五度 | 连奏、柔和 |
| 0.35–0.65 | mixed | 以三度为主，每第 3 个音换成四度或七度 | 稍短 |
| ≥ 0.65 | dissonant | 小二度 / 三全音（偶尔大七度、大二度） | 断奏、力度 +10~18，倒数第二个旋律音再升半音 |

同一个 t 还决定：段落和弦（t≥0.45 偶数小节换 Gm，t≥0.70 段尾换属和弦 A），琶音每 4 个（t≥0.45）或每 2 个（t≥0.70）
音换成小二度/三全音，贝斯 / 踩镲 / 琶音力度随 t 变硬、音长随 t 变短。
`build/analyze/analysis.json` 的 `plan[*]` 里记录每段的 `tension`、`color` 和动机色彩统计 `motif_colors`。

### 长度上限与「最热文件」优先

- **热度** `heat = Σ函数圈复杂度 × (1 + churn)`，churn = `git log --numstat` 里该文件累计增删行数（非 git 目录时 churn=0，退化成纯复杂度）。
- 文件数 > 可用小节数时保留最热的文件（仍按路径顺序演奏），被丢掉的列在 `files_dropped`；
  小节数按预算整体缩放，再从最冷的长段落开始削减；段落里函数槽位不够时，优先给 `复杂度 × 行数` 最大的函数。
- 结果总长**严格 ≤ `--max-bars`**（1 小节引子 + 正文 + 2 小节终止），大仓库不会再超长。

### 映射规则（diff 模式）

- 每个变更块（hunk）→ ½ 小节（≤8 行）或 1 小节（>8 行）的短动机，按 diff 顺序排列；
- **新增行上行**：`.py` 用钢琴、`.js/.ts` 用琶音合成器、`.md/.txt` 用小提琴；力度来自行长；
- **音高轮廓跟随复杂度变化**：每个 hunk 计算 `Δ = 新增行复杂度 − 删除行复杂度`（分支关键字 if/for/while/except/case/and/or/&&/||/? + 最深缩进；
  README 等非代码文件记 0），按 hunk 大小归一化到 `[-1, 1]`。Δ>0（变复杂）→ 起音更高、上行更陡、音更短更响，结尾配小二度/三全音；
  Δ≤0（简化 / 重构）→ 平缓上行，结尾配一个协和三度；
- **删除行下行**：大提琴低音区，被删代码越复杂下行越陡；删除多于新增的小节换成 Gm；
- **heat 轨**（`--heat`）：每小节改动行数 / 最大值 → 中提琴在和弦五音上的重复音型（无 / 四分 / 八分 / 十六分），
  力度与 CC11 表情随之起伏，最热的小节跳八度；`analysis.json` 里有 `heat_per_bar`；
- 每进入一个新文件敲一下底鼓，每个 hunk 一下踩镲，改动涉及 `import` 时加边击；
- 超过 `--max-bars` 时按 `行数 × (1 + 复杂度)` 保留最热的 hunk（仍按 diff 顺序），大 hunk 必要时压缩到半小节，丢弃数量在终端提示。

> JS/TS 分析是无依赖的启发式扫描（去掉字符串/注释后数花括号和关键字），Python 分析用标准库 `ast` + `tokenize`，结果精确。

---

## 网页界面：一句话生成配乐（不需要懂音乐）

给剪辑师 / 写稿人用的最小界面：写一句「感觉」或贴口播稿，拖几个旋钮，浏览器里直接试听，满意了导出完整轨。
全程不需要知道和弦、MIDI 是什么。

### 运行

```bash
uv sync --extra web                     # 多装 fastapi + uvicorn（= make sync / make web-deps；pip 用户: pip install -r requirements-web.txt）

uv run musician serve                   # 或: uv run make web   或: uv run python -m src.web.app
# 浏览器打开 http://127.0.0.1:8765/

uv run musician serve --fallback        # 没装 Surge XT / sfizz / 采样包？全部用兜底合成器（最快，草稿音色）
uv run make web PORT=9000 FALLBACK=1    # Makefile 写法
uv run musician serve --host 0.0.0.0    # 局域网里其他电脑也能打开
```

### 怎么用

1. **感觉 / 口播稿**：比如「科技解说，有点悬疑但不要太吓人，给口播让位」。也可以直接贴整段口播稿——
   导出时会按字数（约 4.5 字/秒）估算时长；短描述默认导出 ≈58 秒。
2. **生成试听**：根据文字自动摆好旋钮（页面上会写出识别到了哪些词），渲染约 20 秒的试听并自动播放。
3. **旋钮**（不用懂乐理）：

   | 旋钮 | 往左 → 往右 | 实际改了什么 |
   |---|---|---|
   | 情绪 | 暗 → 亮 | 和弦走向：暗 = 小调带属和弦张力；中 = 原版科技循环；亮 = 关系大调（F 大调） |
   | 速度 | 慢 → 快 | 72 → 132 拍/分（中间 = 原版 100）；结尾自动渐慢 |
   | 密度 | 疏 → 密 | 用几个声部、多密：只有铺底 + 钢琴 → 加大提琴/中提琴/旋律/贝斯 → 加琶音 → 加轻鼓 |
   | 亮度 | 暗 → 亮 | 铺底音色的明暗（滤波）、琶音高低、整体高频 ±3 dB |
   | 是否抢口播 | 不抢 / 平衡 / 偏配乐 | 整体响度 −21 / −18 / −15 LUFS；「不抢」去掉主旋律、在人声频段多让 3 dB |

4. **微调后再渲**：保持你拖好的旋钮重新渲试听。只有内容变了的声部才重新渲染，其余直接用缓存
   （例如只改「是否抢口播」时通常 1 秒内完成；改速度会让所有声部重渲）。
5. **导出完整轨**：同一组旋钮渲染完整长度，可下载 WAV（剪辑用）/ MP3，以及给音乐人的 MIDI 工程文件。
6. 生成后可以展开「编曲规格 JSON」看具体用了什么速度、段落、声部——只是说明，不看也能用。
7. 「草稿音色」勾选后用 numpy 兜底合成器，最快；不勾选则自动使用已安装的 Surge XT / sfizz + 采样包。

### 从图片生成（图片 → 音乐）

不想写字？点「**上传图片**」（或把图片拖进那张卡片）：封面、截图、产品照片都行。

**默认先「读懂」整张图（视觉解读）**：`src/feel/vision.py` 把图片（缩到 1024 px 的 JPEG）发给支持看图的大模型，
要求它像配乐总监一样从**六个角度**读图，每个角度一句具体的中文 + 2~4 个关键词，并给出音乐建议（结构化 JSON）：

| 角度 | 说明 | 主要影响 |
|---|---|---|
| 情绪氛围 | 画面的情绪基调 | 调性（D 小调 / F 大调）、情绪旋钮 |
| 画面内容 | 画面里有什么、在发生什么 | 「感觉」框里的描述；是否需要主旋律讲故事 |
| 给人的感觉 | 观众看到后的内心感受 | 情绪 / 速度微调、是否抢口播 |
| 可能的故事 / 场景 | 这一刻前后可能发生什么、适合什么视频 | 编曲说明、配器取舍 |
| 节奏暗示 | 静止还是流动、舒缓还是紧凑 | 速度（BPM）、密度（这一角度的情绪词权重加倍） |
| 色调与光影情绪 | 冷暖、明暗、对比、光线的情绪 | 亮度（音色明暗）、情绪 |

另外还有数值建议：`energy` / `brightness` / `density`（0–100）、`suggested_key`（Dm/F）、`suggested_bpm`（72–132）、
`suggested_duck`（0 不抢 / 1 平衡 / 2 偏配乐）、`narration_hint`（口播语气建议）、综合 `keywords`。

**融合规则**（`image_spec.fuse_vision`，视觉为主、颜色为辅）：
情绪 ← 情绪明亮度（并保证落在建议调性那一侧）；速度 ← 建议 BPM 70% + 能量 30%；密度 ← 信息量 75% + 能量 25%；
亮度 ← 明亮度 + 调性 + 能量；是否抢口播 ← suggested_duck。各角度文字里的情绪词（与文字模式同一张词表）再轻推 ≤ ±10；
最后用下面的 Pillow 颜色特征做**最多 ±10** 的轻微修正。六个角度的解读整份写进编曲规格 `image_reading`，
试听下方的说明会逐条写出「读图·节奏暗示『…』→ 82 BPM」这样的对应关系。

**网页上看到的**：上传后，旋钮上方出现「**这张图在说什么**」卡片——六个角度各一格（情绪氛围 / 画面内容 / 给人的感觉
高亮显示）、每格带关键词、底部是综合关键词和口播建议，右上角注明所用模型（以及是否命中缓存）。
「使用视觉解读」复选框默认勾选；取消勾选 = 只用颜色规则（离线、即时）。没配 API Key、超时或模型返回不可解析时，
会**自动改用颜色规则**，卡片里用橙色提示写明原因（例如「未配置视觉模型 API Key …」），页面加载时也会提前提示。
之后**照常拖旋钮** → 「微调后再渲」（只重渲变化的声部，读图结果会一起带上），或「生成试听」（图片模式下保留旋钮，
不会被文字覆盖；在感觉框里重新打字或点示例就回到文字模式）→ 满意了「导出完整轨」。

**配置 API Key**（推荐用 `.env`，设置后重启 `musician serve`）：

```bash
cp .env.example .env        # 然后编辑 .env，只填 OPENAI_API_KEY=你的key
uv run musician serve       # 启动时自动读取 .env（python-dotenv；shell 里 export 的同名变量优先）
```

`.env.example` 默认：`OPENAI_BASE_URL=https://code.ticoag.fun/v1`、`CTM_VISION_MODEL=Qwen3.8-Flash-Next`。
**`.env` 已加入 `.gitignore`，永远不要提交 `.env` 或把真实 key 写进任何会提交的文件。**
不设 `CTM_VISION_MODEL` 时默认 `Qwen3.8-Flash-Next`；失败（404 / 403 无权限 / 5xx / 输出不可解析）依次退到
`DeepSeek-V4-Flash-Vision-Exp` → `gpt-4o-mini` …（纯文本模型如 deepseek-chat 看不了图，已移出候选）；会先查 `GET /v1/models`，把大小写 / 标点不同的写法（如 Qwen3.8-Flash-Next ↔ qwen38-flash-next）对上服务端的真实 id。

也可以直接 export（任选一种）：

```bash
export OPENAI_API_KEY=sk-...  OPENAI_BASE_URL=https://code.ticoag.fun/v1   # 默认模型 Qwen3.8-Flash-Next
# 或任何 OpenAI 兼容接口（OpenRouter / 通义千问 / 智谱 / 本地 vLLM …）：
export OPENAI_API_KEY=...  OPENAI_BASE_URL=https://openrouter.ai/api/v1  CTM_VISION_MODEL=qwen/qwen2.5-vl-72b-instruct
# 或 Anthropic Claude（默认依次尝试 claude-sonnet-4-5 → claude-haiku-4-5 → claude-3-5-sonnet-latest …）：
export ANTHROPIC_API_KEY=sk-ant-...
# 可选：CTM_VISION_MODEL=模型1,模型2（按顺序尝试）  CTM_VISION_PROVIDER=openai|anthropic（两个 key 都有时指定）
#       CTM_VISION_TIMEOUT=90（秒）  CTM_VISION_MAX_TOKENS=4000  CTM_VISION_REASONING=none（reasoning_effort，off = 不发送）
#       CTM_VISION_CACHE=build/vision_cache（缓存目录）
uv run musician serve
```

- 模型不存在（404）/ 该模型无权限（403 "no access to model"）/ 限流 / 5xx 时自动换下一个候选模型；Key 无效（401）或超时则直接退回颜色规则并说明原因。
- 推理模型（如 Qwen3.8-Flash-Next）默认发送 `reasoning_effort: none` 关闭思考、`max_tokens` 4000，避免思考吃光 token 只剩空内容；服务端不认这些参数时自动去掉重试。
- **缓存**：按图片内容 SHA-256 + 模型名（+ 提示词版本）缓存在 `build/vision_cache/`，同一张图再次上传不再调用模型、不再计费。
- 调接口只用 Python 标准库 `urllib`；`.env` 由 `python-dotenv` 读取（没装时用内置简易解析，`CTM_NO_DOTENV=1` 可关闭）。

**颜色规则（兜底 / ±10 修正）**：
`src/feel/image_spec.py` 不用机器学习，用 Pillow 把图缩到 256 px，量 4 个直观特征，按固定规则换算成和
`src/feel/spec.py` 完全一样的旋钮（0–100，是否抢口播 0/1/2），所以结果可解释、可复现：

| 画面特征 | 怎么量 | 影响的旋钮 |
|---|---|---|
| 主色相 | HSV 色相 36 档直方图，按 饱和度×明度 加权，只统计有彩色像素（饱和度 ≥ 0.18） | 暖色（红/橙/黄）→ 情绪 +14～+24、亮度 +8～+18；冷色（青/蓝/紫）→ 情绪 −8～−22、亮度 0～−10；绿色/品红 → 略亮。乘以「彩色程度」：黑白灰图色相不起作用 |
| 平均亮度 | 灰度（Rec.601）均值 | 亮度 = 15 + 75×亮度（主因）；情绪 ±20；速度 ±10 |
| 边缘密度 | 轻微模糊后 `FIND_EDGES`，响应 > 40 的像素占比（30% 视为满格） | 密度 = 15 + 75×边缘（细节多 → 更多声部、琶音、鼓） |
| 色彩方差 | RGB 三通道标准差均值（90 视为满格），与饱和度、明暗对比合成「能量」 | 速度 ±27（能量高 → 快）；密度 ±8 |
| 能量 + 细节 | 0.6×能量 + 0.4×边缘 | ≥ 0.62 → 偏配乐（不 duck）；< 0.22 → 不抢（多让位给人声）；其余 → 平衡 |

情绪基准 40、速度基准 50（= 原版 100 BPM），与文字模式的默认值一致。例子（`scripts/smoke_image.py` 及测试图）：
纯暖橙亮图 → 情绪 70 / 速度 47 / 密度 13 / 亮度 82 / 不抢（F 大调、只有铺底 + 钢琴 + 琶音）；
暗蓝 + 大量彩色线条 → 情绪 16 / 速度 68 / 密度 96 / 亮度 33 / 偏配乐（D 小调 113 BPM 全编制带鼓）；
灰图 → 中性、慢、极疏、不抢。

```bash
uv run python -m src.feel.image_spec 封面.jpg       # 命令行：视觉解读 + 颜色修正 → 建议旋钮 + 理由（JSON；无 key 自动退回颜色规则）
uv run python -m src.feel.image_spec --no-vision 封面.jpg   # 只用颜色规则（离线）
uv run python -m src.feel.vision 封面.jpg           # 只看六个角度的读图结果
uv run make test                                    # 单元测试：JSON 解析 / 融合 / 缓存 / 无 key 兜底（不联网）
uv run python scripts/smoke_image.py                # 冒烟：生成 64×48 测试 PNG → 旋钮 → 编曲规格（有 key 时再测视觉解读）
uv run python scripts/smoke_image.py --image 封面.jpg   # 用真实图片测视觉解读
uv run python scripts/smoke_image.py --url http://127.0.0.1:8765 --preview   # 连同接口 + 试听一起测
```

接口：`POST /api/from-image`（multipart：`file`=图片，可选 `vision`=0 关闭视觉解读（默认 1）、`preview`=1 顺便渲试听、
`fallback`=1 草稿音色）→ `{"source"("vision"/"color"), "vision"(六角度解读 + 数值建议 + mood_zh/content_zh/feeling_zh，或 null),
"vision_error"(退回颜色规则的原因或 null), "image_reading"(写进编曲规格的读图), "knobs", "color_knobs", "duck", "voice_label",
"feel", "reasons", "features", "thumbnail"(data URL), "preview"(同 /api/generate 返回或 null)}`。
`POST /api/generate` 可带 `image_reading`（图片模式下前端会自动带上），`GET /api/health` 的 `vision` 字段表示当前可用的视觉接口。
图片上限 20 MB；需要 `python-multipart`（已在 web 依赖里）和 `pillow`（核心依赖）。

### 马尔科夫链：音高转移图 + 变奏

试听生成后，旋钮下方的「马尔科夫链」卡片会画出**钢琴声部的音高转移图**：每个圆是一个音高（圆越大＝出现次数越多，橙色＝最常见），
每条带箭头的线是「从这个音走到那个音」（线越粗、越深＝转移概率越高，橙色小圈＝重复同一个音）；鼠标悬停看具体概率和次数。

- **生成马尔科夫变奏**：用同一份钢琴 MIDI 统计出的转移矩阵 P 重新采样钢琴音高（节奏、时值、力度不变；同小节的和弦内音加权 ×3，
  避免和其他声部打架；和弦保持原样），其余声部直接复用缓存 stem，几秒内混出新的试听。每点一次换一个随机种子。
- **温度**滑块：采样概率 ∝ P^(1/T)。T→0 几乎总走最常见的转移（接近原曲），T=1 按原始概率，T>1 更平均、更出人意料；
  但只会走原曲里出现过的转移，所以不会跑调。
- **致艾丽丝示例**：不用先生成，直接用 `examples/fur_elise_motif.mid`（《致艾丽丝》开头 8 小节右手动机）建链，
  显示它的转移图，并给出「原动机 / 马尔科夫变奏」两段可对比的音频。

代码在 `src/markov/`（只用标准库 + mido，无新依赖）：`chain.py`（MIDI → 音高序列 → 计数 → P → 图 JSON / 温度采样）、
`vary.py`（声部变奏）、`demo.py`（致艾丽丝种子 MIDI + 简易钢琴渲染，`python -m src.markov.demo` 会重写种子 MIDI）。
接口：`/api/generate` 的返回多了 `markov: {"nodes":[{pitch,name,count}], "edges":[{from,to,prob,count}], ...}`；
`POST /api/markov-variation`（同 `/api/generate` 的参数 + `temperature`、`seed`、`harmony`）；`POST /api/markov-demo {"temperature","seed"}`。
测试：`make test`（含 `tests/test_markov.py`）；冒烟：`make smoke-markov`（起 --fallback 服务，断言图有节点/边、变奏改了音），
`make smoke-markov UI=1` 另用无头 Chrome 打开页面点按钮，确认无 JS 报错（playwright 通过 `uv run --with` 临时装，不进项目依赖）。

### 背后的流程

```
文字 / 图片 + 旋钮 ──► src/feel/spec.py     ArrangementSpec（默认 D 小调；速度→BPM；段落 引入/展开/收束；
                                      密度→声部；亮度→pad/琶音；是否抢口播→duck_for_voice + 响度）
           ──► src/feel/compose.py  按规格写音符（复用 write_score 的和弦 / pad 排列 / mido 多轨 MIDI）
           ──► src/feel/render.py   复用 render_all 的渲染器（Surge / sfizz / 兜底，按声部并行），
                                      按「声部 MIDI 内容哈希」缓存 stem 和效果后 stem → 复用 mix 的响度对齐 + 效果 + 限幅
           ──► out/web/preview-*.wav|mp3（≈20 秒） / out/web/full-*.wav|mp3|mid（完整轨）
```

- 网页后端：`src/web/app.py`（FastAPI）；前端：`web/index.html` + `style.css` + `app.js`（无框架）。
- 接口：`POST /api/generate {"feel": "...", "knobs": {...} 或 null, "full": false, "fallback": false}`，
  `POST /api/parse {"feel": "..."}`（只返回建议旋钮），`POST /api/from-image`（图片 → 建议旋钮，见上），`GET /media/<文件名>`。
- 命令行同款：`python -m src.feel.render "轻松温暖的产品介绍" [--full] [--fallback] [--speed 70 ...]`。
- 缓存在 `build/feel/`，输出在 `out/web/`（都在 .gitignore 里，可随时删除）。

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
├── pyproject.toml / uv.lock  依赖唯一来源 + 锁文件（uv）；requirements*.txt 由 uv.lock 导出
├── Makefile
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
│   ├── analyze/              代码 → 音乐（musician analyze）
│   │   ├── metrics.py        Python AST / JS 启发式指标 + 重复片段检测
│   │   ├── diffscan.py       git diff → hunk
│   │   ├── mapping.py        指标 / hunk → 音符事件
│   │   └── pipeline.py       写 midi/analyze → render → mix → out/analyze.wav
│   ├── feel/                 一句话感觉 + 旋钮 → 编曲规格（spec.py）→ MIDI（compose.py）→ 带缓存渲染混音（render.py）；
│   │                         图片 → 旋钮（vision.py 看图模型六角度读图为主 + image_spec.py Pillow 颜色 ±10 修正 / 兜底）
│   ├── markov/               马尔科夫链：音高转移计数 → 矩阵 P → 图 JSON；温度采样钢琴变奏；致艾丽丝示例
│   └── web/app.py            网页界面后端（FastAPI，musician serve）
├── web/                      网页前端（index.html + style.css + app.js，无框架）
├── musician/                 `musician` 命令行入口（cli.py；musician serve；python -m musician.analyze）
├── examples/analyze_self.mid 分析本项目 src/ 生成的示例 MIDI
├── examples/fur_elise_motif.mid 《致艾丽丝》开头动机（马尔科夫示例的种子 MIDI）
├── instruments/*.sfz         SFZ 覆盖层（起音/滤波/力度曲线）
├── presets/                  Surge XT 音色选择（surge_presets.json + 说明）
├── tests/test_vision.py      视觉解读单元测试（JSON 解析 / 融合 / 缓存 / 兜底，HTTP 已 mock；make test）
├── tests/test_markov.py      马尔科夫链单元测试（计数 / 矩阵 / 温度 / 变奏保节奏 / 种子 MIDI）
├── scripts/
│   ├── run_pipeline.sh       一键运行
│   ├── fetch_samples.sh      下载采样包
│   ├── list_surge_presets.py 浏览 Surge 音色
│   ├── capture_surge_state.py 在 Surge 界面里挑音色并保存
│   ├── smoke_image.py        图片 → 音乐冒烟测试（make smoke-image）
│   └── smoke_markov.py       马尔科夫图 / 变奏 / 致艾丽丝冒烟测试（make smoke-markov [UI=1]）
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
Dependencies are managed with **uv** (`pyproject.toml` + committed `uv.lock`): `uv sync --extra web`, then
`uv run musician all` / `uv run musician serve` / `uv run make web`; `requirements*.txt` are exported from the lock
(`make requirements`) for pip users.

**New: `musician analyze <repo>`** maps real code structure to MIDI and feeds the same render → mix
pipeline (D minor, 100 BPM): files → sections, functions → piano motifs, nesting depth → register,
cyclomatic complexity → motif length / arp density / harmonic tension, imports → percussion
(stdlib rim, third-party kick, local snare), control-flow density → hi-hat subdivision, comment ratio →
pad velocity & brightness (CC74), repeated snippets → violin canon echo. A codephon-style consonance layer turns
complexity + nesting + (lack of) comments into a tension score: calm code gets viola 3rds/5ths/6ths, knotty code gets
minor 2nds/tritones, harder velocities and staccato. Long repos are capped at `--max-bars`, keeping the hottest files
(complexity × git churn). `make demo-analyze` renders this repo's own `src/`. `musician analyze --diff <repo>`
turns git diff hunks into short motifs (added lines ascend, removed lines descend on cello).
Output: `midi/analyze/*.mid`, `build/analyze/analysis.json`, `out/analyze.wav`.

**New: web UI (`musician serve`, http://127.0.0.1:8765/)** — a Chinese, jargon-free page: describe the feel or paste
the narration script, adjust five knobs (mood, speed, density, brightness, voice priority), preview ~20 s in the
browser, re-render tweaks (only parts whose MIDI changed are re-rendered), export the full track (WAV/MP3/MIDI).
Backend: FastAPI over `src/feel` (text+knobs → ArrangementSpec → mido MIDI → existing renderers + mix).
**Image → music**: upload an image (`POST /api/from-image`). By default `src/feel/vision.py` asks a vision LLM
(OpenAI-compatible via `OPENAI_API_KEY`/`OPENAI_BASE_URL`, or Anthropic via `ANTHROPIC_API_KEY`; `CTM_VISION_MODEL`
overrides) to read the whole image from six angles (mood, content, feeling, story/scene, rhythm, colour & light) plus
energy/brightness/density, key, BPM and voice priority; these drive the knobs and are written into the spec
(`image_reading`). The Pillow colour rules (`src/feel/image_spec.py`: hue → mood/brightness, luminance, edge density →
density, colour variance → energy) only bias each knob by ≤ ±10, and are the automatic offline fallback (no key,
timeout, bad JSON, or `--no-vision` / unticking 使用视觉解读). Interpretations are cached by image SHA-256.
