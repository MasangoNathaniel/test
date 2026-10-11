"use strict";
/* FreeMask UI — talks to the local Python server. */

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const app = $("#app");

const SLIDERS = [
  { key: "grow", label: "Expand / Contract", min: -40, max: 40, step: 1, fmt: (v) => (v > 0 ? "+" : "") + v + " px" },
  { key: "feather", label: "Feather", min: 0, max: 40, step: 0.5, fmt: (v) => v + " px" },
  { key: "smart_refine", label: "Smart refine", min: 0, max: 30, step: 1, fmt: (v) => (v === 0 ? "Off" : v + " px") },
  { key: "consolidate", label: "Consolidate", min: 0, max: 6, step: 1, fmt: (v) => (v === 0 ? "Off" : "±" + v + " fr") },
  { key: "remove_specks", label: "Denoise", min: 0, max: 3000, step: 10, fmt: (v) => (v === 0 ? "Off" : v + " px²") },
  { key: "fill_holes", label: "Fill holes", min: 0, max: 5000, step: 10, fmt: (v) => (v === 0 ? "Off" : v + " px²") },
  { key: "clean_black", label: "Clean black", min: 0, max: 0.5, step: 0.01, fmt: (v) => Math.round(v * 100) + "%" },
  { key: "clean_white", label: "Clean white", min: 0, max: 0.5, step: 0.01, fmt: (v) => Math.round(v * 100) + "%" },
];
const DEFAULTS = { grow: 0, feather: 0, smart_refine: -1, consolidate: 0, remove_specks: 64, fill_holes: 0, clean_black: 0, clean_white: 0, invert: false };

const ui = {
  state: null,
  frame: 0,
  obj: 1,
  tool: "include",
  view: "overlay",
  playing: false,
  extraObjects: new Set([1]),
  drag: null,
  hover: null,
  formats: new Set(["png", "video", "cutout"]),
  lastJobKey: null,
  settingsTimer: null,
  sentDoneModal: null,
  frameShown: -1,
};

/* ─────────── API ─────────── */
async function api(path, body) {
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new Error("Lost connection to FreeMask. Is it still running?");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}
async function act(path, body) {
  try {
    const s = await api(path, body);
    if (s && s.status) applyState(s);
    return s;
  } catch (e) {
    toast(e.message, "err");
    return null;
  }
}

/* ─────────── Images ─────────── */
class Cache {
  constructor(max) { this.max = max; this.map = new Map(); }
  get(key, url) {
    let img = this.map.get(key);
    if (img) { this.map.delete(key); this.map.set(key, img); return img; }
    img = new Image();
    img.decoding = "async";
    img.src = url;
    img.onload = () => { if (img._wanted) draw(); };
    this.map.set(key, img);
    if (this.map.size > this.max) this.map.delete(this.map.keys().next().value);
    return img;
  }
}
const frames = new Cache(400);
const overlays = new Cache(240);
const mattes = new Cache(60);
const clipKey = () => (ui.state ? `${ui.state.source}|${ui.state.clip.first_frame}|${ui.state.clip.frames}` : "");
const frameImg = (i) => frames.get(`${clipKey()}#${i}`, `/api/frame/${i}?k=${encodeURIComponent(clipKey())}`);
const overlayImg = (i) => overlays.get(`${clipKey()}#${i}#${ui.state.mask_rev}#${ui.obj}`, `/api/overlay/${i}?obj=${ui.obj}&rev=${ui.state.mask_rev}&k=${encodeURIComponent(clipKey())}`);
const settingsKey = () => JSON.stringify(ui.state.settings);
const matteImg = (i) => mattes.get(`${clipKey()}#${i}#${ui.state.mask_rev}#${settingsKey()}`, `/api/matte/${i}?rev=${ui.state.mask_rev}&s=${encodeURIComponent(settingsKey())}&k=${encodeURIComponent(clipKey())}`);
const ready = (img) => img && img.complete && img.naturalWidth > 0;

function preload(center) {
  if (!ui.state?.clip) return;
  const n = ui.state.clip.frames;
  for (let d = 1; d <= 12; d++) {
    if (center + d < n) frameImg(center + d);
    if (center - d >= 0 && d <= 4) frameImg(center - d);
  }
}

/* ─────────── Canvas ─────────── */
const imgCanvas = $("#imgCanvas");
const uiCanvas = $("#uiCanvas");
const ictx = imgCanvas.getContext("2d");
const uctx = uiCanvas.getContext("2d");
let fit = { w: 0, h: 0, scale: 1 };
let checker = null;

function layout() {
  if (!ui.state?.clip) return;
  const wrap = $("#canvasWrap").getBoundingClientRect();
  const { proc_width: pw, proc_height: ph } = ui.state.clip;
  const scale = Math.min(wrap.width / pw, wrap.height / ph);
  const w = Math.max(1, Math.floor(pw * scale)), h = Math.max(1, Math.floor(ph * scale));
  const dpr = window.devicePixelRatio || 1;
  for (const c of [imgCanvas, uiCanvas]) {
    c.style.width = w + "px";
    c.style.height = h + "px";
  }
  imgCanvas.width = pw; imgCanvas.height = ph;
  uiCanvas.width = Math.round(w * dpr); uiCanvas.height = Math.round(h * dpr);
  fit = { w, h, scale, dpr };
  draw();
}

function checkerPattern() {
  if (checker) return checker;
  const c = document.createElement("canvas");
  c.width = c.height = 32;
  const g = c.getContext("2d");
  g.fillStyle = "#1b1b22"; g.fillRect(0, 0, 32, 32);
  g.fillStyle = "#24242d"; g.fillRect(0, 0, 16, 16); g.fillRect(16, 16, 16, 16);
  checker = ictx.createPattern(c, "repeat");
  return checker;
}

const scratch = document.createElement("canvas");
function draw() {
  if (!ui.state?.clip) return;
  const i = ui.frame;
  const W = imgCanvas.width, H = imgCanvas.height;
  const fimg = frameImg(i); fimg._wanted = true;
  if (ready(fimg)) {
    if (ui.view === "matte" || ui.view === "cutout") {
      const m = matteImg(i); m._wanted = true;
      if (ready(m)) {
        if (ui.view === "matte") {
          ictx.fillStyle = "#000"; ictx.fillRect(0, 0, W, H);
          ictx.drawImage(m, 0, 0, W, H);
        } else {
          scratch.width = W; scratch.height = H;
          const s = scratch.getContext("2d");
          s.clearRect(0, 0, W, H);
          s.drawImage(fimg, 0, 0, W, H);
          s.globalCompositeOperation = "destination-in";
          s.drawImage(m, 0, 0, W, H);
          s.globalCompositeOperation = "source-over";
          ictx.fillStyle = checkerPattern(); ictx.fillRect(0, 0, W, H);
          ictx.drawImage(scratch, 0, 0);
        }
      } else if (ui.frameShown !== i) {
        ictx.drawImage(fimg, 0, 0, W, H);
      }
    } else {
      ictx.drawImage(fimg, 0, 0, W, H);
      if (ui.view === "overlay") {
        const o = overlayImg(i); o._wanted = true;
        if (ready(o)) ictx.drawImage(o, 0, 0, W, H);
      }
    }
    ui.frameShown = i;
  }
  drawUI();
  updateTimecode();
  drawTimeline();
}

function color(obj, a = 1) {
  const c = (ui.state?.colors || [])[obj % 10] || [255, 255, 255];
  return `rgba(${c[0]},${c[1]},${c[2]},${a})`;
}

function drawUI() {
  const { w, h, dpr, scale } = fit;
  uctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  uctx.clearRect(0, 0, w, h);
  if (!ui.state?.prompts) return;
  if (ui.view !== "original") {
    for (const p of ui.state.prompts) {
      if (p.frame !== ui.frame) continue;
      const active = p.object === ui.obj;
      if (p.box) {
        const [x1, y1, x2, y2] = p.box.map((v) => v * scale);
        uctx.save();
        uctx.strokeStyle = color(p.object, active ? 1 : 0.55);
        uctx.lineWidth = 1.5;
        uctx.setLineDash([6, 4]);
        uctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
        uctx.restore();
        corner(x1, y1, x2, y2, color(p.object, active ? 1 : 0.55));
      }
      p.points.forEach(([x, y], k) => dot(x * scale, y * scale, p.labels[k], active, p.object));
    }
  }
  if (ui.drag && ui.drag.box) {
    const { x0, y0, x, y } = ui.drag;
    uctx.save();
    uctx.fillStyle = color(ui.obj, 0.1);
    uctx.strokeStyle = color(ui.obj, 1);
    uctx.lineWidth = 1.5;
    uctx.fillRect(Math.min(x0, x), Math.min(y0, y), Math.abs(x - x0), Math.abs(y - y0));
    uctx.strokeRect(Math.min(x0, x), Math.min(y0, y), Math.abs(x - x0), Math.abs(y - y0));
    uctx.restore();
  }
}

function corner(x1, y1, x2, y2, c) {
  const L = 10;
  uctx.save();
  uctx.strokeStyle = c; uctx.lineWidth = 3; uctx.lineCap = "round";
  uctx.beginPath();
  for (const [x, y, dx, dy] of [[x1, y1, 1, 1], [x2, y1, -1, 1], [x1, y2, 1, -1], [x2, y2, -1, -1]]) {
    uctx.moveTo(x + dx * L, y); uctx.lineTo(x, y); uctx.lineTo(x, y + dy * L);
  }
  uctx.stroke();
  uctx.restore();
}

function dot(x, y, label, active, obj) {
  const fill = label ? "#3ddc97" : "#ff6b6b";
  uctx.save();
  uctx.globalAlpha = active ? 1 : 0.55;
  uctx.shadowColor = "rgba(0,0,0,.6)"; uctx.shadowBlur = 6;
  uctx.beginPath(); uctx.arc(x, y, 8, 0, Math.PI * 2); uctx.fillStyle = "rgba(10,10,14,.85)"; uctx.fill();
  uctx.shadowBlur = 0;
  uctx.lineWidth = 2; uctx.strokeStyle = color(obj, 1); uctx.stroke();
  uctx.beginPath(); uctx.arc(x, y, 4.5, 0, Math.PI * 2); uctx.fillStyle = fill; uctx.fill();
  uctx.strokeStyle = "#0a0a0e"; uctx.lineWidth = 1.6;
  uctx.beginPath(); uctx.moveTo(x - 2.4, y); uctx.lineTo(x + 2.4, y);
  if (label) { uctx.moveTo(x, y - 2.4); uctx.lineTo(x, y + 2.4); }
  uctx.stroke();
  uctx.restore();
}

/* ─────────── Pointer input ─────────── */
function toProc(e) {
  const r = uiCanvas.getBoundingClientRect();
  const x = e.clientX - r.left, y = e.clientY - r.top;
  return { x, y, px: (x / r.width) * ui.state.clip.proc_width, py: (y / r.height) * ui.state.clip.proc_height };
}

uiCanvas.addEventListener("contextmenu", (e) => e.preventDefault());
uiCanvas.addEventListener("pointerdown", (e) => {
  if (!editable()) return;
  uiCanvas.setPointerCapture(e.pointerId);
  const p = toProc(e);
  ui.drag = { x0: p.x, y0: p.y, x: p.x, y: p.y, px0: p.px, py0: p.py, button: e.button, alt: e.altKey, box: ui.tool === "box" };
});
uiCanvas.addEventListener("pointermove", (e) => {
  if (!ui.drag) return;
  const p = toProc(e);
  Object.assign(ui.drag, { x: p.x, y: p.y, px: p.px, py: p.py });
  if (ui.drag.button === 0 && Math.hypot(p.x - ui.drag.x0, p.y - ui.drag.y0) > 7) ui.drag.box = true;
  drawUI();
});
uiCanvas.addEventListener("pointerup", async (e) => {
  const d = ui.drag;
  ui.drag = null;
  if (!d) return;
  const p = toProc(e);
  if (d.box && Math.abs(p.x - d.x0) > 6 && Math.abs(p.y - d.y0) > 6) {
    drawUI();
    await act("/api/box", { frame: ui.frame, obj: ui.obj, x1: d.px0, y1: d.py0, x2: p.px, y2: p.py });
  } else if (!d.box || ui.tool !== "box") {
    const label = d.button === 2 || d.alt || ui.tool === "exclude" ? 0 : 1;
    // Show the dot immediately, then let the server answer with the mask.
    ui.state.prompts.push({ frame: ui.frame, object: ui.obj, points: [[p.px, p.py]], labels: [label], box: null });
    drawUI();
    await act("/api/point", { frame: ui.frame, obj: ui.obj, x: p.px, y: p.py, label });
  } else {
    drawUI();
  }
});
uiCanvas.addEventListener("pointercancel", () => { ui.drag = null; drawUI(); });

function editable() {
  return ui.state?.status === "ready" && !jobRunning();
}
const jobRunning = () => !!(ui.state?.job && !ui.state.job.done);

/* ─────────── Timeline ─────────── */
const tl = $("#tlCanvas");
const tctx = tl.getContext("2d");
function drawTimeline() {
  if (!ui.state?.clip) return;
  const r = tl.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  if (tl.width !== Math.round(r.width * dpr) || tl.height !== Math.round(r.height * dpr)) {
    tl.width = Math.round(r.width * dpr); tl.height = Math.round(r.height * dpr);
  }
  const W = r.width, H = r.height, n = ui.state.clip.frames;
  const X = (f) => (n <= 1 ? 0 : (f / (n - 1)) * (W - 16) + 8);
  tctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  tctx.clearRect(0, 0, W, H);

  // ticks
  tctx.fillStyle = "rgba(255,255,255,.08)";
  const step = niceStep(n, W);
  tctx.font = "10px " + getComputedStyle(document.body).getPropertyValue("--mono");
  for (let f = 0; f < n; f += step) {
    const x = X(f);
    tctx.fillRect(x, 0, 1, 8);
    tctx.fillStyle = "rgba(255,255,255,.28)";
    tctx.fillText(String(f + ui.state.clip.first_frame), x + 3, 11);
    tctx.fillStyle = "rgba(255,255,255,.08)";
  }

  // tracked ranges
  const y = 22, bh = 12;
  tctx.fillStyle = "rgba(255,255,255,.05)";
  roundRect(8, y, W - 16, bh, 4); tctx.fill();
  const g = tctx.createLinearGradient(0, 0, W, 0);
  g.addColorStop(0, ui.state.stale ? "rgba(124,92,255,.35)" : "#7c5cff");
  g.addColorStop(1, ui.state.stale ? "rgba(0,214,201,.35)" : "#00d6c9");
  tctx.fillStyle = g;
  for (const [a, b] of ui.state.tracked || []) {
    const x1 = X(a) - (n > 1 ? 0.5 : 0), x2 = X(b) + (n > 1 ? 1.5 : W - 16);
    roundRect(x1, y, Math.max(2, x2 - x1), bh, 3); tctx.fill();
  }

  // marked frames (diamonds)
  const marked = new Map();
  for (const p of ui.state.prompts || []) if (!marked.has(p.frame) || p.object === ui.obj) marked.set(p.frame, p.object);
  for (const [f, o] of marked) {
    const x = X(f), cy = y + bh + 10;
    tctx.save();
    tctx.translate(x, cy); tctx.rotate(Math.PI / 4);
    tctx.fillStyle = color(o, 1);
    tctx.shadowColor = color(o, 0.8); tctx.shadowBlur = 8;
    tctx.fillRect(-4, -4, 8, 8);
    tctx.restore();
  }

  // hover
  if (ui.hover != null) {
    tctx.fillStyle = "rgba(255,255,255,.25)";
    tctx.fillRect(X(ui.hover), 0, 1, H);
  }
  // playhead
  const px = X(ui.frame);
  tctx.fillStyle = "#fff";
  tctx.shadowColor = "rgba(255,255,255,.6)"; tctx.shadowBlur = 8;
  tctx.fillRect(px - 1, 0, 2, H);
  tctx.shadowBlur = 0;
  tctx.beginPath(); tctx.moveTo(px - 6, 0); tctx.lineTo(px + 6, 0); tctx.lineTo(px, 7); tctx.closePath(); tctx.fill();
}
function niceStep(n, W) {
  const target = Math.max(1, Math.round(n / Math.max(1, W / 70)));
  for (const s of [1, 2, 5, 10, 12, 24, 25, 30, 50, 60, 100, 120, 250, 500, 1000, 2500, 5000]) if (s >= target) return s;
  return target;
}
function roundRect(x, y, w, h, r) {
  tctx.beginPath();
  tctx.moveTo(x + r, y); tctx.arcTo(x + w, y, x + w, y + h, r); tctx.arcTo(x + w, y + h, x, y + h, r);
  tctx.arcTo(x, y + h, x, y, r); tctx.arcTo(x, y, x + w, y, r); tctx.closePath();
}
function tlFrame(e) {
  const r = tl.getBoundingClientRect(), n = ui.state.clip.frames;
  return Math.round(Math.min(1, Math.max(0, (e.clientX - r.left - 8) / (r.width - 16))) * (n - 1));
}
let scrubbing = false;
$("#timeline").addEventListener("pointerdown", (e) => {
  if (!ui.state?.clip || jobRunning()) return;
  scrubbing = true; $("#timeline").setPointerCapture(e.pointerId);
  setFrame(tlFrame(e));
});
$("#timeline").addEventListener("pointermove", (e) => {
  if (!ui.state?.clip) return;
  ui.hover = tlFrame(e);
  if (scrubbing) setFrame(ui.hover); else drawTimeline();
});
$("#timeline").addEventListener("pointerup", () => (scrubbing = false));
$("#timeline").addEventListener("pointerleave", () => { ui.hover = null; drawTimeline(); });

/* ─────────── Frames / playback ─────────── */
function setFrame(f) {
  if (!ui.state?.clip) return;
  ui.frame = Math.max(0, Math.min(ui.state.clip.frames - 1, f));
  preload(ui.frame);
  draw();
}
function updateTimecode() {
  const c = ui.state.clip;
  $("#tcFrame").textContent = ui.frame + c.first_frame;
  $("#tcTotal").textContent = `/ ${c.first_frame + c.frames - 1}`;
}
let playTimer = null;
function togglePlay(force) {
  ui.playing = force ?? !ui.playing;
  $("#playIcon").setAttribute("href", ui.playing ? "#i-pause" : "#i-play");
  clearInterval(playTimer);
  if (ui.playing) {
    const fps = Math.min(30, ui.state.clip.fps || 24);
    playTimer = setInterval(() => {
      const next = ui.frame + 1 >= ui.state.clip.frames ? 0 : ui.frame + 1;
      if (!ready(frameImg(next))) { preload(ui.frame); return; } // wait for the frame rather than stutter
      setFrame(next);
    }, 1000 / fps);
  }
}

/* ─────────── Actions ─────────── */
async function track(direction) {
  if (!ui.state?.prompts?.length) return toast("Click on what you want to mask first.", "err");
  togglePlay(false);
  await act("/api/track", { direction });
  poll();
}
async function doExport() {
  if (!ui.formats.size) return toast("Choose at least one export format.", "err");
  if (!ui.state?.tracked_count) return toast("Track the subject before exporting.", "err");
  if (ui.state.stale) {
    toast("Re-tracking with your latest marks first…");
    const s = await act("/api/track", { direction: "both" });
    if (!s) return;
    await waitJob();
  }
  ui.sentDoneModal = "pending";
  await act("/api/export", { formats: [...ui.formats] });
  poll();
}
function waitJob() {
  return new Promise((resolve) => {
    const t = setInterval(async () => {
      const s = await api("/api/state").catch(() => null);
      if (s) applyState(s);
      if (!s || !s.job || s.job.done) { clearInterval(t); resolve(s); }
    }, 150);
  });
}

function sendSettings(patch) {
  Object.assign(ui.state.settings, patch);
  renderSliders();
  if (ui.view === "overlay" || ui.view === "original") setView("matte");
  else draw();
  clearTimeout(ui.settingsTimer);
  ui.settingsTimer = setTimeout(() => act("/api/settings", patch), 120);
}

/* ─────────── Sidebar ─────────── */
function renderObjects() {
  const s = ui.state;
  const objs = new Set([...ui.extraObjects, ...(s.objects || []), ui.obj]);
  const counts = {};
  for (const p of s.prompts || []) counts[p.object] = (counts[p.object] || 0) + 1;
  const hidden = new Set(s.hidden || []);
  const list = $("#objects");
  list.innerHTML = "";
  for (const o of [...objs].sort((a, b) => a - b)) {
    const li = document.createElement("li");
    li.className = "obj" + (o === ui.obj ? " active" : "") + (hidden.has(o) ? " hidden-obj" : "");
    li.innerHTML = `<span class="swatch" style="color:${color(o)}"></span>
      <span class="obj-name">Object ${o}</span>
      <span class="obj-sub">${counts[o] ? counts[o] + (counts[o] > 1 ? " frames" : " frame") : "no marks"}</span>
      <span class="key">${o}</span>
      <button class="icon-btn" title="${hidden.has(o) ? "Show in matte" : "Hide from matte"}"><svg class="i"><use href="#${hidden.has(o) ? "i-eye-off" : "i-eye"}"/></svg></button>`;
    li.addEventListener("click", () => selectObject(o));
    li.querySelector("button").addEventListener("click", (e) => {
      e.stopPropagation();
      if (!counts[o]) return toast("Mark this object first.");
      act("/api/hidden", { obj: o, hidden: !hidden.has(o) });
    });
    list.appendChild(li);
  }
  $("#addObject").disabled = objs.size >= 9;
}
function selectObject(o) {
  ui.obj = o;
  ui.extraObjects.add(o);
  renderObjects();
  draw();
}

function renderSliders() {
  const box = $("#sliders");
  const st = ui.state.settings;
  if (!box.children.length) {
    for (const sd of SLIDERS) {
      const el = document.createElement("label");
      el.className = "slider";
      el.innerHTML = `<div class="slider-top"><span>${sd.label}</span><span class="slider-val"></span></div>
        <input type="range" min="${sd.min}" max="${sd.max}" step="${sd.step}" data-key="${sd.key}">`;
      const input = el.querySelector("input");
      input.addEventListener("input", () => sendSettings({ [sd.key]: Number(input.value) }));
      input.addEventListener("dblclick", () => sendSettings({ [sd.key]: DEFAULTS[sd.key] }));
      box.appendChild(el);
    }
  }
  for (const sd of SLIDERS) {
    const input = box.querySelector(`[data-key="${sd.key}"]`);
    let v = st[sd.key];
    let label;
    if (sd.key === "smart_refine" && v < 0) { label = `Auto · ${ui.state.auto_refine} px`; v = ui.state.auto_refine; }
    else label = sd.fmt(v);
    if (document.activeElement !== input) input.value = v;
    input.style.setProperty("--p", ((Number(input.value) - sd.min) / (sd.max - sd.min)) * 100 + "%");
    input.closest(".slider").querySelector(".slider-val").textContent = label;
  }
  $("#invert").checked = !!st.invert;
}
$("#invert").addEventListener("change", (e) => sendSettings({ invert: e.target.checked }));
$("#resetRefine").addEventListener("click", () => sendSettings({ ...DEFAULTS }));
$("#addObject").addEventListener("click", () => {
  for (let o = 1; o <= 9; o++) {
    if (![...ui.extraObjects, ...(ui.state.objects || [])].includes(o)) { selectObject(o); toast(`Object ${o} — click to mark it`); return; }
  }
});

$$(".fmt").forEach((b) => b.addEventListener("click", () => {
  const f = b.dataset.fmt;
  ui.formats.has(f) ? ui.formats.delete(f) : ui.formats.add(f);
  b.classList.toggle("active", ui.formats.has(f));
}));

/* ─────────── Toolbar ─────────── */
function setTool(t) {
  ui.tool = t;
  $$("[data-tool]").forEach((b) => b.classList.toggle("active", b.dataset.tool === t));
  uiCanvas.classList.toggle("box-mode", t === "box");
}
function setView(v) {
  ui.view = v;
  $$("[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
  draw();
}
$$("[data-tool]").forEach((b) => b.addEventListener("click", () => setTool(b.dataset.tool)));
$$("[data-view]").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
$("#undoBtn").addEventListener("click", () => act("/api/undo", { frame: ui.frame, obj: ui.obj }));
$("#clearBtn").addEventListener("click", () => act("/api/clear", { frame: ui.frame, obj: ui.obj }));
$("#trackBoth").addEventListener("click", () => track("both"));
$("#staleTrack").addEventListener("click", () => track("both"));
$("#trackFwd").addEventListener("click", () => track("forward"));
$("#trackBack").addEventListener("click", () => track("backward"));
$("#stepBack").addEventListener("click", () => setFrame(ui.frame - 1));
$("#stepFwd").addEventListener("click", () => setFrame(ui.frame + 1));
$("#playBtn").addEventListener("click", () => togglePlay());
$("#jobStop").addEventListener("click", () => act("/api/cancel", {}));
$("#loadingCancel").addEventListener("click", () => act("/api/cancel", {}));
$("#exportBtn").addEventListener("click", doExport);
$("#exportBtnTop").addEventListener("click", doExport);
$("#saveBtn").addEventListener("click", async () => {
  const r = await act("/api/save", {});
  if (r?.path) toast("Session saved", "ok");
});
$("#errorBack").addEventListener("click", () => { ui.forceOpen = true; render(); });
$("#doneKeep").addEventListener("click", () => ($("#doneModal").hidden = true));
$("#doneFinish").addEventListener("click", async () => {
  if (ui.handoffResult) return closeApp(ui.handoffResult.ok ? "Done — back to Resolve" : "FreeMask closed", "");
  if (!ui.state?.from_resolve) return closeApp("Your mattes are saved", ui.state?.out_dir || "");
  const btn = $("#doneFinish");
  btn.disabled = true;
  $("#doneKeep").disabled = true;
  showHandoff({ status: "waiting" });
  const r = await act("/api/finish", {});
  if (!r) { btn.disabled = false; $("#doneKeep").disabled = false; $("#handoff").hidden = true; return; }
  for (let i = 0; i < 1200; i++) {  // the server gives up after 2 minutes
    const s = await api("/api/state").catch(() => null);
    const h = s && s.handoff;
    if (h && h.status !== "waiting") return showHandoff(h);
    await new Promise((res) => setTimeout(res, 250));
  }
});
function showHandoff(h) {
  const box = $("#handoff");
  box.hidden = false;
  box.className = "handoff " + (h.status === "waiting" ? "wait" : h.ok ? "ok" : "warn");
  $("#handoffMsg").textContent = h.status === "waiting" ? "Adding to your DaVinci Resolve timeline…" : h.message;
  $("#handoffLog").textContent = h.log ? "Log: " + h.log : "";
  if (h.status === "waiting") return;
  const btn = $("#doneFinish");
  btn.disabled = false;
  btn.textContent = "Close FreeMask";
  ui.handoffResult = h;
  $("#doneKeep").hidden = true;
}
async function closeApp(title, sub) {
  await api("/api/quit", {}).catch(() => {});
  document.body.innerHTML = `<div style="height:100vh;display:grid;place-items:center;text-align:center;font:15px var(--font);color:#8c8a9e">
    <div><img src="/assets/icon.svg" width="48" height="48" alt=""><p style="color:#ecebf3;font-size:18px;font-weight:600;margin:16px 0 6px"></p>
    <p class="sub"></p><p>You can close this window.</p></div></div>`;
  document.querySelector("p").textContent = title;
  document.querySelector("p.sub").textContent = sub;
  setTimeout(() => window.close(), 800);
}
$("#openForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  ui.forceOpen = false;
  await act("/api/open", { path: $("#openPath").value, start: $("#openStart").value || 0, end: $("#openEnd").value || null });
  poll();
});

/* ─────────── Keyboard ─────────── */
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" && e.target.type !== "range" && e.target.type !== "checkbox") return;
  if (!ui.state?.clip || app.dataset.screen !== "editor") return;
  const mod = e.metaKey || e.ctrlKey;
  const k = e.key;
  if (mod && k.toLowerCase() === "z") { e.preventDefault(); return act("/api/undo", { frame: ui.frame, obj: ui.obj }); }
  if (mod && k.toLowerCase() === "s") { e.preventDefault(); return $("#saveBtn").click(); }
  if (mod && k.toLowerCase() === "e") { e.preventDefault(); return doExport(); }
  if (mod) return;
  const n = ui.state.clip.frames;
  const map = {
    ArrowLeft: () => setFrame(ui.frame - (e.shiftKey ? 10 : 1)),
    ArrowRight: () => setFrame(ui.frame + (e.shiftKey ? 10 : 1)),
    a: () => setFrame(ui.frame - 1), d: () => setFrame(ui.frame + 1),
    Home: () => setFrame(0), End: () => setFrame(n - 1),
    " ": () => togglePlay(), k: () => togglePlay(),
    t: () => track("both"), "]": () => track("forward"), "[": () => track("backward"),
    i: () => setTool("include"), x: () => setTool("exclude"), b: () => setTool("box"),
    c: () => act("/api/clear", { frame: ui.frame, obj: ui.obj }),
    v: () => setView(["overlay", "matte", "cutout", "original"][(["overlay", "matte", "cutout", "original"].indexOf(ui.view) + 1) % 4]),
    n: () => {
      const fs = [...new Set((ui.state.prompts || []).map((p) => p.frame))].sort((a, b) => a - b);
      if (fs.length) setFrame(fs.find((f) => f > ui.frame) ?? fs[0]);
    },
    Escape: () => { if (jobRunning()) act("/api/cancel", {}); },
  };
  if (/^[1-9]$/.test(k)) { e.preventDefault(); return selectObject(Number(k)); }
  const fn = map[k] || map[k.toLowerCase?.()];
  if (fn) { e.preventDefault(); fn(); }
});

/* ─────────── Toasts ─────────── */
function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.innerHTML = (kind === "ok" ? '<svg class="i"><use href="#i-check"/></svg>' : kind === "err" ? '<svg class="i"><use href="#i-alert"/></svg>' : "") + `<span></span>`;
  el.querySelector("span").textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.classList.add("out"), kind === "err" ? 5000 : 2600);
  setTimeout(() => el.remove(), kind === "err" ? 5400 : 3000);
}

/* ─────────── State ─────────── */
function applyState(s) {
  const prev = ui.state;
  const firstClip = s.status === "ready" && (!prev || prev.status !== "ready" || prev.source !== s.source || prev.clip?.first_frame !== s.clip?.first_frame);
  ui.state = s;
  if (!prev && s.formats) {
    ui.formats = new Set(s.formats);
    $$(".fmt").forEach((b) => b.classList.toggle("active", ui.formats.has(b.dataset.fmt)));
  }
  if (firstClip) {
    ui.frame = s.start_frame || 0;
    ui.extraObjects = new Set([1, ...(s.objects || [])]);
    ui.obj = (s.objects || [])[0] || 1;
    $("#sliders").innerHTML = "";
    requestAnimationFrame(layout);
  }
  const job = s.job;
  if (job && !job.done && job.kind === "track" && job.frame != null) {
    ui.frame = job.frame;
  }
  if (job && job.done) {
    const key = `${job.kind}:${job.id}`;
    if (ui.lastJobKey !== key && ui.lastJobKey !== null) {
      if (job.error) toast(job.error, "err");
      else if (job.kind === "track" && job.result?.finished) toast(`Tracked ${s.tracked_count} frames in ${job.elapsed}s`, "ok");
      else if (job.kind === "track") toast("Tracking stopped");
      else if (job.kind === "export" && job.result) showDone(job.result);
    }
    ui.lastJobKey = key;
  } else if (job) {
    ui.lastJobKey = "running";
  }
  render();
}

function showDone(paths) {
  const labels = { png: "Matte", video: "Matte movie", cutout: "Cutout", cutout_video: "Cutout movie" };
  $("#doneFinish").textContent = ui.state?.from_resolve ? "Finish & send to Resolve" : "Done";
  $("#donePaths").innerHTML = "";
  for (const [k, v] of Object.entries(paths)) {
    const li = document.createElement("li");
    li.innerHTML = `<b>${labels[k] || k}</b><span></span>`;
    li.querySelector("span").textContent = v;
    $("#donePaths").appendChild(li);
  }
  $("#doneModal").hidden = false;
}

function render() {
  const s = ui.state;
  if (!s) return;
  let screen = "open";
  if (s.status === "loading") screen = "loading";
  else if (s.status === "error") screen = "error";
  else if (s.status === "ready") screen = "editor";
  if (ui.forceOpen) screen = "open";
  const changed = app.dataset.screen !== screen;
  app.dataset.screen = screen;

  const job = s.job, running = jobRunning();
  // top bar
  $("#clipMeta").hidden = screen !== "editor";
  $("#saveBtn").disabled = screen !== "editor" || running;
  $("#exportBtnTop").disabled = $("#exportBtn").disabled = screen !== "editor" || running || !s.tracked_count;
  const pill = $("#statusPill");
  pill.hidden = !running;
  if (running) $("#statusText").textContent = job.kind === "track" ? "Tracking" : job.kind === "export" ? "Exporting" : "Loading";

  if (screen === "loading" && job) {
    const pct = Math.round((job.progress || 0) * 100);
    $("#ringBar").style.strokeDashoffset = String(326.7 * (1 - (job.progress || 0)));
    $("#ringPct").textContent = pct + "%";
    $("#loadingTitle").textContent = job.message?.startsWith("Loading") ? "Warming up the tracker" : "Preparing clip";
    $("#loadingMsg").textContent = (job.message || "Working") + "…" + (job.message?.startsWith("Loading") ? " (first run downloads the model)" : "");
  }
  if (screen === "error") $("#errorMsg").textContent = s.error || job?.error || "Unknown error";
  if (screen === "open" && s.source && !$("#openPath").value) $("#openPath").value = s.source;

  if (screen !== "editor") return;
  const c = s.clip;
  $("#clipName").textContent = s.name;
  $("#clipName").title = s.source;
  $("#clipRes").textContent = `${c.width}×${c.height}`;
  $("#clipFps").textContent = `${Number(c.fps.toFixed(3))} fps`;
  $("#engineChip").textContent = `SAM 2.1 ${s.model} · ${String(s.device || "").toUpperCase()}`;
  $("#outPath").textContent = s.out_dir;
  $("#outPath").title = s.out_dir;

  // job bar
  $("#job").hidden = !running || job.kind === "load";
  if (running) {
    $("#jobFill").style.width = Math.round((job.progress || 0) * 100) + "%";
    $("#jobText").textContent = `${job.kind === "track" ? "Tracking" : "Exporting"} · ${Math.round((job.progress || 0) * 100)}%`;
  }
  $("#trackedInfo").innerHTML = s.tracked_count
    ? `<b>${s.tracked_count}</b> / ${c.frames} frames tracked`
    : `${c.frames} frames`;
  for (const id of ["trackBoth", "trackFwd", "trackBack", "undoBtn", "clearBtn"]) $("#" + id).disabled = running;

  $("#coach").hidden = (s.prompts || []).length > 0;
  $("#staleBanner").hidden = !(s.stale && s.has_tracked) || running;

  renderObjects();
  renderSliders();
  if (changed) requestAnimationFrame(layout);
  else draw();
}

/* ─────────── Polling & heartbeat ─────────── */
let pollTimer = null;
async function poll() {
  clearTimeout(pollTimer);
  try {
    const s = await api("/api/state");
    applyState(s);
  } catch (e) { /* server gone; keep trying quietly */ }
  const fast = jobRunning() || ui.state?.status === "loading";
  pollTimer = setTimeout(poll, fast ? 140 : 1500);
}
setInterval(() => fetch("/api/ping", { method: "POST" }).catch(() => {}), 4000);
fetch("/api/ping", { method: "POST" }).catch(() => {});
window.addEventListener("pagehide", () => navigator.sendBeacon("/api/bye"));
window.addEventListener("resize", () => requestAnimationFrame(layout));

poll();
