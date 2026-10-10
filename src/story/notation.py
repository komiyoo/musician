"""Editable concert-pitch scores and a self-contained handoff for each story job."""
from __future__ import annotations

import csv
import html
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree as ET

from src import config as C
from src.types import INSTRUMENTS, StoryManifest


def build_score(manifest: StoryManifest):
    from music21 import articulations, chord, clef, expressions, instrument, key
    from music21 import metadata, meter, note, pitch, stream, tempo, tie

    result = stream.Score()
    result.metadata = metadata.Metadata(title=manifest.story.title, composer='Code to Music')
    result.atSoundingPitch = True
    bars = []
    for section in manifest.story.sections:
        for local in range(section.bars):
            bars.append((section, local, section.chords[local % len(section.chords)]))
    length = len(bars) * 4
    for part_index, part in enumerate(manifest.story.parts):
        ins = INSTRUMENTS[part.instrument]
        staff = stream.Part(id=part.id)
        staff.partName = f'{ins.en} [{part.id}]'
        staff.partAbbreviation = part.id
        staff.atSoundingPitch = True
        sound = (instrument.SnareDrum() if ins.drum_note == 38 else instrument.CrashCymbals()
                 if ins.drum_note is not None else instrument.instrumentFromMidiProgram(ins.program))
        sound.partId = part.id
        sound.instrumentName = staff.partName
        sound.transposition = None  # All pitches are sounding, including octave-transposing instruments.
        sound.midiChannel = 9 if ins.drum_note is not None else part_index % 15 + (part_index % 15 >= 9)
        staff.insert(0, sound)
        for index, (section, local, symbol) in enumerate(bars):
            measure = stream.Measure(number=index + 1)
            if index == 0:
                measure.timeSignature = meter.TimeSignature('4/4')
                measure.clef = (clef.PercussionClef() if ins.drum_note is not None else
                                clef.AltoClef() if part.instrument == 'viola' else
                                clef.BassClef() if ins.center < 60 else clef.TrebleClef())
                measure.insert(0, expressions.TextExpression(f'Concert pitch · {part.articulation}'))
            if local == 0:
                if ins.drum_note is None:
                    measure.keySignature = key.Key(section.key)
                measure.insert(0, expressions.RehearsalMark(
                    f'{section.id} · {section.start_s:.0f}s'))
            bpm = 60_000_000 / manifest.timeline.tempos[index]
            if index == 0 or manifest.timeline.tempos[index] != manifest.timeline.tempos[index - 1]:
                measure.insert(0, tempo.MetronomeMark(number=round(bpm, 2), numberSounding=round(bpm, 6)))
            if ins.drum_note is None:
                # A text direction keeps chord labels visible without asking MIDI exporters
                # to synthesize hidden harmony notes during a score-to-MIDI round trip.
                measure.insert(0, expressions.TextExpression(symbol))
            staff.insert(index * 4, measure)

        grouped = defaultdict(list)
        for n in manifest.notes:
            if n.part == part.id:
                start = min(length - 0.25, round(n.start * 4) / 4)
                duration = n.dur if n.notation_dur is None else n.notation_dur
                end = min(length, max(start + 0.25, round((n.start + duration) * 4) / 4))
                grouped[start, end].append(n)
        measures = list(staff.getElementsByClass(stream.Measure))
        for (start, end), events in sorted(grouped.items()):
            cursor = start
            while cursor < end:
                bar = int(cursor // 4)
                stop = min(end, (bar + 1) * 4)
                if ins.drum_note is not None:
                    element = note.Unpitched(displayName='C5')
                    element.storedInstrument = sound
                    element.notehead = 'x' if ins.drum_note == 49 else 'normal'
                else:
                    pitches = []
                    for event in events:
                        p = pitch.Pitch(event.pitch)
                        if p.name == 'A#':
                            p = p.getEnharmonic()
                        pitches.append(p)
                    element = note.Note(pitches[0]) if len(pitches) == 1 else chord.Chord(pitches)
                element.quarterLength = stop - cursor
                element.volume.velocity = round(sum(n.vel for n in events) / len(events))
                if len(events) > 1 and ins.drum_note is None:
                    for member, event in zip(element.notes, events):
                        member.volume.velocity = event.vel
                if cursor > start or stop < end:
                    element.tie = tie.Tie('continue' if cursor > start and stop < end else
                                          'stop' if cursor > start else 'start')
                if cursor == start and part.articulation == 'staccato':
                    element.articulations.append(articulations.Staccato())
                measures[bar].insert(cursor - bar * 4, element)
                cursor = stop
        for measure in measures:
            measure.makeVoices(inPlace=True)
            measure.makeRests(refStreamOrTimeRange=[0, 4], fillGaps=True,
                              timeRangeFromBarDuration=True, inPlace=True)
        staff.makeNotation(inPlace=True)
        result.insert(0, staff)
    return result


def preview_scores(directory: Path, titles: dict[str, str]) -> None:
    import verovio

    links = []
    for stem, title in titles.items():
        toolkit = verovio.toolkit()
        full = stem == 'full'
        toolkit.setOptions({'pageWidth': 2970 if full else 2100, 'pageHeight': 4200 if full else 2970,
                            'scale': 35 if full else 55, 'breaks': 'auto', 'condense': 'none',
                            'header': 'auto', 'footer': 'auto', 'svgHtml5': True})
        if not toolkit.loadFile(str(directory / f'{stem}.musicxml')):
            raise RuntimeError(f'cannot engrave {stem}: {toolkit.getLog()}')
        pages = []
        for index in range(1, toolkit.getPageCount() + 1):
            name = f'{stem}-{index:03d}.svg'
            (directory / name).write_text(toolkit.renderToSVG(index), encoding='utf-8')
            pages.append(f'<img class="page" src="{name}" alt="{html.escape(title)} 第 {index} 页">')
        (directory / f'{stem}.html').write_text(
            '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            f'<title>{html.escape(title)}</title><style>'
            'body{margin:24px;background:#eee;font:16px system-ui}nav{margin:24px auto;max-width:900px}'
            '.page{display:block;background:white;width:100%;max-width:1200px;margin:20px auto}'
            f'@page{{size:{"A3" if full else "A4"} portrait;margin:0}}'
            '@media print{body{margin:0;background:white}nav{display:none}'
            '.page{width:100%;max-width:none;margin:0;break-after:page}}'
            '</style><nav><a href="index.html">返回乐谱目录</a> · '
            f'{html.escape(title)} · 实际音高 / Concert pitch · 使用浏览器打印可保存 PDF</nav>'
            + ''.join(pages) + '</html>', encoding='utf-8')
        links.append(f'<li><a href="{stem}.html">{html.escape(title)}</a> · '
                     f'<a href="{stem}.musicxml">MusicXML</a> · {len(pages)} 页</li>')
    (directory / 'index.html').write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>乐谱交付</title>'
        '<style>body{max-width:900px;margin:48px auto;padding:0 24px;font:18px/1.8 system-ui}'
        'a{color:#146452}</style><h1>总谱与分谱</h1>'
        '<p>实际音高（Concert pitch）。点击查看乐谱，用浏览器打印或保存 PDF；'
        'MusicXML 可在 MuseScore、Dorico 等软件继续编辑。</p><ul>'
        + ''.join(links) + '</ul></html>', encoding='utf-8')


def export(manifest: StoryManifest, output: Path, *, preview: bool = False) -> None:
    from music21 import stream

    directory = output / 'notation'
    directory.mkdir()
    score = build_score(manifest)
    titles = {'full': f'{manifest.story.title} — 总谱'}
    full = None
    for part, staff in zip(manifest.story.parts, score.parts):
        single = stream.Score()
        single.metadata = deepcopy(score.metadata)
        single.metadata.movementName = staff.partName
        single.insert(0, deepcopy(staff))
        path = directory / f'{part.id}.musicxml'
        single.write('musicxml', fp=path)
        root = ET.parse(path).getroot()
        if full is None:
            full = deepcopy(root)
            full.find('movement-title').text = manifest.story.title
            full.find('part-list').clear()
            full.remove(full.find('part'))
        # music21 assigns channels within one port; assemble separately exported parts for large scores.
        declaration = deepcopy(root.find('part-list/score-part'))
        midi_index = list(declaration).index(declaration.find('midi-instrument'))
        declaration.insert(midi_index, ET.Element('midi-device', port=str(C.PARTS[part.id]['port'] + 1)))
        declaration.find('midi-instrument/midi-channel').text = str(C.PARTS[part.id]['channel'] + 1)
        full.find('part-list').append(declaration)
        body = deepcopy(root.find('part'))
        if len(full.findall('part')):
            for measure in body.findall('measure'):
                for element in list(measure):
                    if element.tag == 'harmony' or (element.tag == 'direction' and (
                            element.find('direction-type/metronome') is not None or
                            element.find('direction-type/rehearsal') is not None)):
                        measure.remove(element)
        full.append(body)
        titles[part.id] = f'{INSTRUMENTS[part.instrument].zh} — {part.id}'
    ET.ElementTree(full).write(directory / 'full.musicxml', encoding='utf-8', xml_declaration=True)
    with (output / 'instruments.csv').open('w', newline='', encoding='utf-8-sig') as file:
        writer = csv.writer(file)
        writer.writerow(['track_id', '乐器', 'instrument', 'role', 'articulation', 'GM_program_1_based',
                         'MIDI_port_0_based', 'MIDI_channel_1_based', 'notes', 'first_s', 'last_s'])
        for part in manifest.story.parts:
            ins, route = INSTRUMENTS[part.instrument], C.PARTS[part.id]
            notes = [n for n in manifest.notes if n.part == part.id]
            writer.writerow([part.id, ins.zh, ins.en, part.role, part.articulation,
                             '' if ins.drum_note is not None else ins.program + 1,
                             route['port'], route['channel'] + 1, len(notes),
                             round(min((n.start_s for n in notes), default=0), 3),
                             round(max((n.end_s for n in notes), default=0), 3)])
    (output / 'HANDOFF.md').write_text(
        f'# {manifest.story.title} · 乐谱与音频交接\n\n'
        f'{len(manifest.timeline.tempos)} 小节，4/4 拍，{len(manifest.story.parts)} 声部，'
        f'{manifest.timeline.duration_s:.3f} 秒（含余响）。\n\n'
        '## 文件\n\n'
        '- `notation/full.musicxml`：可编辑总谱；其余 MusicXML 为分谱。全部按实际音高记谱。\n'
        '- `instruments.csv`：中英乐器名、职责、演奏法、GM 音色、通道与进出时间。\n'
        '- `midi/full.mid`：精确演奏数据，含力度、踏板与表情；各轨 MIDI 可分别导入 DAW。\n'
        '- `story.json`：故事时间、声部配置与音源位置；`score.json`：本次实际音符。\n'
        '- `final.wav` / `final.mp3`：渲染试听；`stems/` 与 `build/stems_fx/` 为干声及处理后分轨。\n\n'
        '## 怎样修改再生成音乐\n\n'
        '1. 在 MuseScore、Dorico、Sibelius 等软件打开总谱 MusicXML；也可以在 DAW 直接改 `midi/full.mid`。\n'
        '2. 改音高、音符时值、节奏与力度；保留原有声部、4/4 拍、小节数及速度。'
        '轨名保留原 ID（如 `clarinet`），或名称末尾的 `[clarinet]`，不要将多件乐器合成一轨。\n'
        '3. 从乐谱软件导出多轨 type-1 MIDI，命名为 `edited.mid`。若要保留细腻的踏板、表情和连奏，优先直接编辑原始 MIDI。\n'
        '4. 在本项目目录执行（替换路径，输出目录必须为空）：\n\n'
        '```bash\n'
        'uv run musician story /path/to/handoff/story.json --midi /path/to/edited.mid --fallback --out out/story/revised\n'
        '# 正式音源：去掉 --fallback，配置 story.json 中的音源文件，并使用 --strict\n'
        '# 打印预览：uv sync --extra notation，再给上面的命令加 --sheet-preview\n'
        '```\n\n'
        '导入将替换自动作曲，输出新音频、MIDI、乐谱和音符数据；加 `--video` 可重绘同步视频。'
        '每个声部应保留一条命名轨（允许全休止），不接受新增或无法识别的轨。\n\n'
        '## 记谱与演奏的区别\n\n'
        '谱面量化到十六分音符，作曲时的名义时值不含演奏门限；原始 MIDI 保留呼吸空隙、连奏重叠与全部控制器。'
        '由谱面重新导出的 MIDI 因此可能与原版演奏略有差异。导入 MIDI 的新谱面按实际音符时值量化。'
        '自动排谱尚未进行人工校订；移调乐器的演奏分谱、键盘分手、弓法与翻页请由编辑人员确认。'
        '和弦标记与演奏法文字来自故事规格，不会根据改过的音符自动重新分析。\n\n'
        '## 时间与音源\n\n'
        '导出 MIDI 时启用完整速度表。若文件完全没有速度事件，项目沿用故事速度；'
        '若带速度事件，则检查整条速度曲线（容许每分钟 0.02 拍的舍入误差）。'
        '改变速度、拍号、结构或让事件超出作品范围会报错，避免画面失同步；此类改编需要同步调整故事规格。'
        'MusicXML 记录音符和乐器，不包含采样库。story.json 中的音源为本机绝对路径，换机器须重新配置。'
        '控制器、弯音和触后保存在 MIDI 中；实际效果由音源支持程度决定，草稿音源不模拟全部演奏法。\n',
        encoding='utf-8')
    if preview:
        preview_scores(directory, titles)
