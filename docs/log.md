# 变更记录

## 2026-10-10

- 创建[叙事配乐设计](story-scoring.md#功能范围)，明确故事规格、统一时间轴、配器、音源与同步视频。
- 实现 `musician story` 与《原来是你》示例，完成 186 秒、23 声部的草稿音视频验证；入口见[README](../README.md#叙事配乐故事时间表--乐谱--音频与视频)。
- 修复逐小节变速时长、调性标记、音色缓存与 Surge 控制器转发；共用 MIDI 写出与故事配置恢复保持原有流程可用。
- 修复草稿合成器的颤音：对频率变化积分后计算相位，避免弦乐、木管和人声的长音随时长大幅跑调。补充通过波形过零点测量音高的回归测试。
- 参考 music-composition skill 与 Open Music Theory 优化叙事编曲：分解和弦在固定音区内接近上一音，尼龙吉他改为稀疏色彩声部，木管轮流对答；新增整曲跳进、对答轮换及退场回归测试。依据见[编曲依据与当前取舍](story-scoring.md#编曲依据与当前取舍)。
- 乐理优化版通过 45 项测试及 186 秒完整音频渲染；钢琴、竖琴连续伴奏的八度及以上跳进均降为零，23 声部保留，输出共 1,016 个音符。已导出与上版的等响度开头对比；采样音色和主观听感待验收。
- 将 music-composition skill 固定在上游版本 `07cecf9` 并随仓库保存到 `.agents/skills/music-composition/`，保留完整资料与许可证，补充[来源记录](../.agents/skills/music-composition/UPSTREAM.md)。
- 增加可编辑乐谱交付：故事输出 MusicXML 总谱、23 份分谱、SVG/HTML 预览、`instruments.csv` 和 `HANDOFF.md`；`--midi` 对保留声部 ID 的 type-1 MIDI 做时间轴与事件校验后重新渲染，并覆盖速度、力度、踏板和弯音的往返测试。交付示例位于 `out/story/originally-you-handoff/`。
