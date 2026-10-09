# Surge XT 音色选择 / Preset selection

`presets/surge_presets.json` 决定 pad / arp / bass 用哪个 Surge XT 音色。
路径相对于 Surge XT 的 **factory patch 目录**（`patches_factory`）：

| 系统 | patches_factory 位置 |
|---|---|
| Linux (.deb) | `/usr/share/surge-xt/patches_factory` |
| macOS | `/Library/Application Support/Surge XT/patches_factory` |
| Windows | `C:\ProgramData\Surge XT\patches_factory` |

其他位置可用环境变量 `CTM_SURGE_FACTORY=/path/to/patches_factory` 指定。

## 默认选择（适合口播铺底）

| 声部 | 默认 patch | 备选 | 理由 |
|---|---|---|---|
| pad | `Pads/MKS-70 Warm Pad.fxp` | `Pads/Still`, `Pads/Distant`, `Pads/Pad 3`, `Pads/Winter Warmer` | 慢起音、暖、高频少，不和人声抢 2–4 kHz |
| arp | `Plucks/Nice Pluck 2.fxp` | `Plucks/Soift`, `Plucks/Clean`, `Plucks/Light 1`, `Plucks/Magic Music Box` | 短促 pluck，十六分音符清楚但不刺耳 |
| bass | `Basses/Sub 1.fxp` | `Basses/Mellow`, `Basses/Smoothie`, `Basses/Deep End` | 纯低频，给推进感又不占中频 |

> 不要用 `Sequences/*`：那些音色自带音序器/琶音器，会和我们 MIDI 里已经写好的十六分音符打架。

## 如何挑音色

```bash
python scripts/list_surge_presets.py            # 分类 + 数量（含第三方音色库约 3000 个）
python scripts/list_surge_presets.py Pads       # 某分类下全部音色
python scripts/list_surge_presets.py --grep warm
```

然后改 `surge_presets.json` 里的 `"patch"`（也可以写 .fxp 绝对路径），再跑
`python -m src.render.surge_render pad`。

## 三种加载方式（surge_render.py 按顺序尝试）

1. **surgepy**（推荐，完全无界面）：Surge XT 源码编译时打开 `-DSURGE_BUILD_PYTHON_BINDINGS=ON`，
   `surgepy.createSurge(sr).loadPatch(path)` 直接读取 json 里的 `.fxp`。
2. **pedalboard + VST3**：`CTM_SURGE_PLUGIN="/usr/lib/vst3/Surge XT.vst3"`。
   pedalboard 无法按名字切换 Surge 音色，所以：
   - `presets/<part>.vstpreset` 存在 → `plugin.load_preset()`；
   - 或 `presets/<part>.state` 存在 → `plugin.raw_state = ...`
     （用 `python scripts/capture_surge_state.py pad` 打开 Surge 界面，选好音色关窗即保存）；
   - 都没有 → Surge 初始化音色（Init Saw），会提示你去 capture。
3. **fallback**：numpy 简易合成器（没有 Surge 也能出声）。

`*.state` / `*.vstpreset` / `*.fxp` 是二进制，已在 `.gitignore` 中忽略；如需共享请自行 `git add -f`。
