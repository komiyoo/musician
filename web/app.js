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
  $("btn-tweak").disabled = on || !$("player").src;
  $("btn-export").disabled = on || !$("player").src;
}

async function generate({ full = false, useKnobs = true } = {}) {
  if (busy) return;
  lock(true);
  const t0 = performance.now();
  status(full ? "正在渲染完整轨（约 1 分钟的音乐，可能要几十秒）…" : "正在生成试听…", "busy");
  const body = { feel: $("feel").value, full, fallback: $("fallback").checked, knobs: useKnobs ? getKnobs() : null };
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
      $("player-card").hidden = false;
      $("player-meta").textContent = `${d.duration_s} 秒 · ${d.spec.bpm} 拍/分 · ${d.engines.includes("fallback") && d.engines.length === 1 ? "草稿音色" : "正式音色"}`;
      $("notes").innerHTML = (d.spec.notes || []).map((x) => `<li>${esc(x)}</li>`).join("");
      const p = $("player");
      p.src = d.audio + "?t=" + Date.now();
      p.play().catch(() => {});
    }
  } catch (e) {
    status("出错了：" + e.message, "err");
  } finally {
    lock(false);
  }
}
function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

KNOBS.forEach((n) => $(n).addEventListener("input", () => showKnob(n)));
document.querySelectorAll("#voice button").forEach((b) => b.addEventListener("click", () => setVoice(+b.dataset.v)));
document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => { $("feel").value = c.dataset.text; }));
// 生成试听: read the text again and let it set the knobs; 微调后再渲: keep the knobs the user moved
$("btn-gen").addEventListener("click", () => generate({ useKnobs: false }));
$("btn-tweak").addEventListener("click", () => generate({ useKnobs: true }));
$("btn-export").addEventListener("click", () => generate({ full: true, useKnobs: true }));

fetch("/api/health").then((r) => r.json()).then((h) => {
  setKnobs(h.defaults);
  if (h.force_fallback) { $("fallback").checked = true; $("fallback").disabled = true; }
}).catch(() => setKnobs({ mood: 40, speed: 50, density: 50, brightness: 50, voice: 1 }));
