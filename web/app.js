// 一句话生成配乐 — tiny vanilla-JS frontend for src/web/app.py
const $ = (id) => document.getElementById(id);
const KNOBS = ["mood", "speed", "density", "brightness"];
const WORDS = {
  mood: ["很暗", "偏暗", "中性", "偏亮", "很亮"],
  speed: ["很慢", "偏慢", "适中", "偏快", "很快"],
  density: ["很疏", "偏疏", "适中", "偏密", "很密"],
  brightness: ["很暗", "偏暗", "适中", "偏亮", "很亮"],
};
let voice = 1;
let busy = false;
let fromImage = false;   // knobs were set from an uploaded image → 生成试听 keeps them instead of re-reading the text
let lastGen = null;      // body of the last preview request → 生成马尔科夫变奏 re-renders the same arrangement
let imageReading = null; // 多角度读图（/api/from-image），图片模式下随每次生成带回后端写进编曲规格

function word(k, v) { return WORDS[k][Math.min(4, Math.floor(v / 20))]; }
function showKnob(k) { $(k + "-v").textContent = word(k, +$(k).value); }
function setVoice(v) {
  voice = v;
  document.querySelectorAll("#voice button").forEach((b) => {
    const on = +b.dataset.v === v;
    b.classList.toggle("on", on);
    b.setAttribute("aria-checked", on);
  });
}
function setKnobs(k) {
  KNOBS.forEach((n) => { $(n).value = k[n]; showKnob(n); });
  setVoice(k.voice);
}
function getKnobs() {
  const k = { voice };
  KNOBS.forEach((n) => (k[n] = +$(n).value));
  return k;
}
function status(msg, cls = "") { const s = $("status"); s.textContent = msg; s.className = "status " + cls; }
function lock(on) {
  busy = on;
  $("btn-gen").disabled = on;
  $("btn-image").disabled = on;
  $("btn-tweak").disabled = on || !$("player").src;
  $("btn-export").disabled = on || !$("player").src;
  $("btn-markov").disabled = on || !lastGen;
  $("btn-elise").disabled = on;
}

async function generate({ full = false, useKnobs = true } = {}) {
  if (busy) return;
  lock(true);
  const t0 = performance.now();
  status(full ? "正在渲染完整轨（约 1 分钟的音乐，可能要几十秒）…" : "正在生成试听…", "busy");
  const body = { feel: $("feel").value, full, fallback: $("fallback").checked, knobs: useKnobs ? getKnobs() : null,
    image_reading: fromImage ? imageReading : null };
  try {
    const r = await fetch("/api/generate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    setKnobs(d.knobs);
    const secs = ((performance.now() - t0) / 1000).toFixed(1);
    const reuse = d.reused.length ? `，复用了 ${d.reused.length} 个已渲染声部` : "";
    status(`完成：${d.duration_s} 秒音乐，用时 ${secs} 秒${reuse}。`);
    if (d.reasons && d.reasons.length) $("reasons").innerHTML = d.reasons.map((x) => `<li>${esc(x)}</li>`).join("");
    else if (useKnobs) $("reasons").innerHTML = "";
    $("spec").textContent = JSON.stringify(d.spec, null, 2);
    $("spec-card").hidden = false;
    if (full) {
      $("export-card").hidden = false;
      $("export-meta").textContent = `${d.duration_s} 秒 · 响度 ${d.lufs} LUFS`;
      $("export-player").src = d.audio + "?t=" + Date.now();
      $("dl-wav").href = d.wav; $("dl-wav").setAttribute("download", "配乐-完整.wav");
      if (d.mp3) { $("dl-mp3").href = d.mp3; $("dl-mp3").hidden = false; $("dl-mp3").setAttribute("download", "配乐-完整.mp3"); }
      else $("dl-mp3").hidden = true;
      $("dl-mid").href = d.midi; $("dl-mid").setAttribute("download", "配乐-工程.mid");
      $("export-card").scrollIntoView({ behavior: "smooth", block: "nearest" });
    } else {
      lastGen = { ...body, full: false, knobs: d.knobs };
      showPreview(d);
    }
  } catch (e) {
    status("出错了：" + e.message, "err");
  } finally {
    lock(false);
  }
}
function showPreview(d, title = "试听") {
  $("player-title").textContent = title;
  if (d.markov) drawMarkov(d.markov, "钢琴音高转移", `${d.markov.n_notes} 个音 · ${d.markov.n_states} 个状态 · ${d.markov.n_edges} 条转移`);
  $("spec").textContent = JSON.stringify(d.spec, null, 2);
  $("spec-card").hidden = false;
  $("player-card").hidden = false;
  $("player-meta").textContent = `${d.duration_s} 秒 · ${d.spec.bpm} 拍/分 · ${d.engines.includes("fallback") && d.engines.length === 1 ? "草稿音色" : "正式音色"}`;
  $("notes").innerHTML = (d.spec.notes || []).map((x) => `<li>${esc(x)}</li>`).join("");
  const p = $("player");
  p.src = d.audio + "?t=" + Date.now();
  p.play().catch(() => {});
}

// 图片 → 旋钮（+ 可选自动试听）。之后旋钮照常可调，「微调后再渲」只重渲变化的声部。
async function uploadImage(file) {
  if (busy || !file) return;
  if (!file.type.startsWith("image/")) { status("请选择图片文件（JPG / PNG / WebP …）", "err"); return; }
  lock(true);
  const auto = $("image-auto").checked;
  const t0 = performance.now();
  const useVision = $("image-vision").checked;
  status((useVision ? "看图模型正在读图（约 5–30 秒）" : "正在分析图片颜色") + (auto ? "，随后生成试听…" : "…"), "busy");
  const fd = new FormData();
  fd.append("file", file);
  fd.append("preview", auto ? "1" : "0");
  fd.append("fallback", $("fallback").checked ? "1" : "0");
  fd.append("vision", useVision ? "1" : "0");
  try {
    const r = await fetch("/api/from-image", { method: "POST", body: fd });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    fromImage = true;
    imageReading = d.image_reading || null;
    showReading(d, useVision);
    setKnobs(d.knobs);
    $("feel").value = d.feel;
    $("image-preview").src = d.thumbnail;
    $("image-name").textContent = `${d.filename || "图片"} · ${d.features.width}×${d.features.height}`;
    $("image-palette").innerHTML = (d.features.palette || []).map((c) => `<i style="background:${esc(c)}" title="${esc(c)}"></i>`).join("");
    $("image-thumb").hidden = false;
    $("btn-image-clear").hidden = false;
    $("reasons").innerHTML = d.reasons.map((x) => `<li>${esc(x)}</li>`).join("");
    const secs = ((performance.now() - t0) / 1000).toFixed(1);
    const how = d.source === "vision" ? "按读图结果" : "按图片颜色";
    if (d.preview) {
      lastGen = { feel: d.feel, full: false, fallback: $("fallback").checked, knobs: d.knobs, image_reading: imageReading };
      showPreview(d.preview);
      status(`已${how}设好旋钮并生成 ${d.preview.duration_s} 秒试听（用时 ${secs} 秒）。可以继续拖旋钮，再点「微调后再渲」。`);
    } else {
      status(`已${how}设好旋钮。点「生成试听」听听看，或先拖动旋钮微调。`);
    }
  } catch (e) {
    status("图片分析失败：" + e.message, "err");
  } finally {
    lock(false);
    $("image-file").value = "";
  }
}
// 多角度读图卡片：情绪氛围 / 画面内容 / 给人的感觉 / 故事·场景 / 节奏暗示 / 色调与光影
function showReading(d, useVision) {
  const v = d.vision;
  $("reading-card").hidden = false;
  const fb = $("reading-fallback");
  if (v) {
    $("reading-src").textContent = `视觉解读 · ${v.model}${v.cached ? " · 缓存" : ` · ${v.seconds} 秒`}`;
    fb.hidden = true;
    $("reading-angles").innerHTML = v.angles.map((a) =>
      `<div class="angle${["mood", "content", "feeling"].includes(a.key) ? " main" : ""}"><h3>${esc(a.title)}</h3><p>${esc(a.text)}</p>` +
      (a.keywords.length ? `<div class="tags">${a.keywords.map((k) => `<span class="tag">${esc(k)}</span>`).join("")}</div>` : "") + "</div>").join("");
    const kw = (d.image_reading && d.image_reading.keywords) || v.keywords || [];
    $("reading-kw").innerHTML = kw.map((k) => `<span class="tag">${esc(k)}</span>`).join("");
    $("reading-narr").hidden = !v.narration_hint;
    $("reading-narr").textContent = v.narration_hint ? "口播建议：" + v.narration_hint : "";
  } else {
    $("reading-src").textContent = "颜色规则（未使用视觉解读）";
    fb.hidden = !useVision;
    fb.textContent = d.vision_error ? "视觉解读不可用：" + d.vision_error : "";
    $("reading-angles").innerHTML = `<div class="angle"><h3>画面颜色</h3><p>${esc(d.feel.replace(/^图片配乐：/, ""))}</p></div>`;
    $("reading-kw").innerHTML = "";
    $("reading-narr").hidden = true;
  }
}
function clearImage() {
  fromImage = false;
  imageReading = null;
  $("reading-card").hidden = true;
  $("image-thumb").hidden = true;
  $("btn-image-clear").hidden = true;
  $("image-preview").removeAttribute("src");
}

// ---------------------------------------------------------------- 马尔科夫链：图 + 变奏 + 致艾丽丝示例
const SVGNS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs, parent) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}
// nodes on a circle ordered by pitch; radius ∝ √count; edge width / opacity ∝ P(from→to); self-loops in orange
function drawMarkov(g, title, meta) {
  const svg = $("markov-svg");
  svg.innerHTML = "";
  $("markov-title").textContent = title;
  $("markov-meta").textContent = meta || "";
  $("markov-hint").hidden = true;
  const W = 520, H = 380, cx = W / 2, cy = H / 2, R = Math.min(W, H) / 2 - 42;
  if (!g || !g.nodes.length) { svgEl("text", { x: cx, y: cy, class: "empty" }, svg).textContent = "没有音符"; return; }
  const defs = svgEl("defs", {}, svg);
  const mk = svgEl("marker", { id: "arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 9, markerHeight: 9, markerUnits: "userSpaceOnUse", orient: "auto-start-reverse" }, defs);
  svgEl("path", { d: "M0,0 L10,5 L0,10 z", fill: "#3b5bdb", "fill-opacity": 0.7 }, mk);
  const maxC = Math.max(...g.nodes.map((n) => n.count));
  const pos = {};
  g.nodes.forEach((n, i) => {
    const a = -Math.PI / 2 + (2 * Math.PI * i) / g.nodes.length;
    pos[n.pitch] = { x: cx + R * Math.cos(a), y: cy + R * Math.sin(a), a, r: 7 + 15 * Math.sqrt(n.count / maxC), n };
  });
  const eg = svgEl("g", {}, svg);
  for (const e of [...g.edges].sort((a, b) => a.prob - b.prob)) {
    const A = pos[e.from], B = pos[e.to];
    if (!A || !B) continue;
    const w = (0.6 + 7 * e.prob).toFixed(2), op = (0.18 + 0.7 * e.prob).toFixed(2);
    let d;
    if (e.from === e.to) {           // self-loop: small circle just outside the node
      const ox = A.x + Math.cos(A.a) * (A.r + 9), oy = A.y + Math.sin(A.a) * (A.r + 9);
      d = `M${A.x + Math.cos(A.a) * A.r},${A.y + Math.sin(A.a) * A.r} A9,9 0 1,1 ${ox + 0.1},${oy + 0.1}`;
    } else {                         // curved so a→b and b→a don't overlap; ends trimmed to the circles
      const dx = B.x - A.x, dy = B.y - A.y, L = Math.hypot(dx, dy) || 1;
      const mx = (A.x + B.x) / 2 - (dy / L) * L * 0.18, my = (A.y + B.y) / 2 + (dx / L) * L * 0.18;
      const sa = Math.atan2(my - A.y, mx - A.x), ea = Math.atan2(my - B.y, mx - B.x);
      d = `M${A.x + Math.cos(sa) * A.r},${A.y + Math.sin(sa) * A.r} Q${mx},${my} ${B.x + Math.cos(ea) * (B.r + 2)},${B.y + Math.sin(ea) * (B.r + 2)}`;
    }
    const p = svgEl("path", { d, class: "edge" + (e.from === e.to ? " self" : ""), "stroke-width": w, "stroke-opacity": op,
      "marker-end": e.from === e.to ? "" : "url(#arrow)" }, eg);
    svgEl("title", {}, p).textContent = `${pos[e.from].n.name} → ${pos[e.to].n.name}  P=${e.prob}（${e.count} 次）`;
  }
  const top = g.nodes.reduce((a, b) => (b.count > a.count ? b : a));
  for (const n of g.nodes) {
    const P = pos[n.pitch];
    const c = svgEl("circle", { cx: P.x, cy: P.y, r: P.r, class: "node" + (n === top ? " hot" : "") }, svg);
    svgEl("title", {}, c).textContent = `${n.name}（MIDI ${n.pitch}）出现 ${n.count} 次`;
    svgEl("text", { x: P.x, y: P.y }, svg).textContent = n.name;
  }
}
async function markovVariation() {
  if (busy || !lastGen) return;
  lock(true);
  const T = +$("markov-temp").value, t0 = performance.now();
  status(`正在按马尔科夫链（温度 ${T}）重新采样钢琴声部并重新混音…`, "busy");
  try {
    const body = { ...lastGen, fallback: $("fallback").checked, temperature: T };
    const r = await fetch("/api/markov-variation", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    showPreview(d, "试听 · 马尔科夫变奏");
    const v = d.variation, g = d.markov_source;
    drawMarkov(g, "钢琴音高转移（变奏采样用）", `温度 ${v.temperature} · 种子 ${v.seed} · 改了 ${v.changed}/${v.n_notes} 个音 · ${g.n_states} 状态 · ${g.n_edges} 转移`);
    status(`马尔科夫变奏完成：钢琴改了 ${v.changed}/${v.n_notes} 个音（节奏不变），其他声部复用 ${d.reused.length} 个，用时 ${((performance.now() - t0) / 1000).toFixed(1)} 秒。再点一次换一种变奏。`);
  } catch (e) {
    status("马尔科夫变奏失败：" + e.message, "err");
  } finally {
    lock(false);
  }
}
async function eliseDemo() {
  if (busy) return;
  lock(true);
  const T = +$("markov-temp").value;
  status("正在用《致艾丽丝》动机建马尔科夫链并采样变奏…", "busy");
  try {
    const r = await fetch("/api/markov-demo", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ temperature: T }) });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    drawMarkov(d.graph, "致艾丽丝 动机", `${d.original.length} 个音 · ${d.graph.n_states} 状态 · ${d.graph.n_edges} 转移 · 温度 ${d.temperature} · 改了 ${d.changed} 个音`);
    $("elise-box").hidden = false;
    $("elise-orig").textContent = d.original.join(" ");
    $("elise-var").textContent = d.variation.join(" ");
    $("elise-a-orig").src = d.audio_original;
    const a = $("elise-a-var");
    a.src = d.audio_variation + "?t=" + Date.now();
    a.play().catch(() => {});
    status("致艾丽丝示例：上面是由原动机统计出的转移图，下面可以对比原动机和马尔科夫变奏。");
  } catch (e) {
    status("示例失败：" + e.message, "err");
  } finally {
    lock(false);
  }
}

function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

KNOBS.forEach((n) => $(n).addEventListener("input", () => showKnob(n)));
document.querySelectorAll("#voice button").forEach((b) => b.addEventListener("click", () => setVoice(+b.dataset.v)));
document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => { $("feel").value = c.dataset.text; clearImage(); }));
$("feel").addEventListener("input", () => { if (fromImage) clearImage(); });   // typing a new feel → back to text mode
$("btn-image").addEventListener("click", () => $("image-file").click());
$("image-file").addEventListener("change", (e) => uploadImage(e.target.files[0]));
$("btn-image-clear").addEventListener("click", clearImage);
const imgCard = $("image-card");
["dragenter", "dragover"].forEach((t) => imgCard.addEventListener(t, (e) => { e.preventDefault(); imgCard.classList.add("drag"); }));
["dragleave", "drop"].forEach((t) => imgCard.addEventListener(t, (e) => { e.preventDefault(); imgCard.classList.remove("drag"); }));
imgCard.addEventListener("drop", (e) => uploadImage(e.dataTransfer.files[0]));
// 生成试听: read the text again and let it set the knobs (unless they came from an image);
// 微调后再渲: keep the knobs the user moved
$("btn-gen").addEventListener("click", () => generate({ useKnobs: fromImage }));
$("btn-tweak").addEventListener("click", () => generate({ useKnobs: true }));
$("btn-export").addEventListener("click", () => generate({ full: true, useKnobs: true }));
$("btn-markov").addEventListener("click", markovVariation);
$("btn-elise").addEventListener("click", eliseDemo);
$("markov-temp").addEventListener("input", () => { $("markov-temp-v").textContent = (+$("markov-temp").value).toFixed(1); });

fetch("/api/health").then((r) => r.json()).then((h) => {
  setKnobs(h.defaults);
  if (h.force_fallback) { $("fallback").checked = true; $("fallback").disabled = true; }
  if (!h.vision) {
    $("vision-note").hidden = false;
    $("vision-note").textContent = "未检测到视觉模型 API Key（OPENAI_API_KEY / ANTHROPIC_API_KEY），上传图片时会自动改用颜色规则。";
  }
}).catch(() => setKnobs({ mood: 40, speed: 50, density: 50, brightness: 50, voice: 1 }));
