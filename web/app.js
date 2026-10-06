import { Engine } from "./engine.js";
import { Wave } from "./wave.js";
import { CoverEditor } from "./cover.js";

const $ = (id) => document.getElementById(id);
const LETTERS = "ABCDEFGHIJKLMNOP";
const PREP_STEPS = [
  "Cutting your part",
  "Measuring the original",
  "Loading the separation models",
  "Separating screams and cheering",
  "Separating voice, drums and bass",
  "Taking the echo off the voice",
  "Saving stems",
];

// ── small helpers ────────────────────────────────────────────────
async function api(path, { method = "GET", body, raw = false } = {}) {
  const opts = { method, headers: { "x-vta": "1" } };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["content-type"] = "application/json"; }
  const r = await fetch(path, opts);
  if (raw) return r;
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(data.error || `Request failed (${r.status})`); e.status = r.status; e.data = data; throw e; }
  return data;
}

function fmt(t, digits = 1) {
  t = Math.max(0, t || 0);
  const m = Math.floor(t / 60), s = t - m * 60;
  return `${m}:${s.toFixed(digits).padStart(digits ? 3 + digits : 2, "0")}`;
}

function parseTime(text) {
  const t = String(text).trim().replace("：", ":");
  if (!/^\d+(:\d{1,2}){0,2}(\.\d+)?$/.test(t)) return null;
  return t.split(":").reduce((acc, p) => acc * 60 + parseFloat(p), 0);
}

let toastTimer;
function toast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 5000);
}

// What the last saved export used, so a song reopens as it was saved.
function fromExport(job) {
  const x = job.last_export;
  if (!x) return {};
  return { cover: x.cover, takeId: x.take, params: x.params, takeKey: x.prepared_key, previewKey: null,
           fadeIn: x.fade_in, fadeOut: x.fade_out, level: x.level };
}

const store = {
  get(id) { try { return JSON.parse(localStorage.getItem(`vta:${id}`)) || {}; } catch { return {}; } },
  set(id, v) { try { localStorage.setItem(`vta:${id}`, JSON.stringify({ ...store.get(id), ...v })); } catch { /* private mode */ } },
};

// ── state ────────────────────────────────────────────────────────
const S = {
  cfg: null, settings: null, job: null, peaks: null,
  takeId: "original", params: {}, level: "natural",
  preview: { start: 0, length: 30 }, takesKey: null, sourceShown: false,
  stopAt: null, lastExportShown: null,
};
let engine, waveFull, waveRange, cover;

// ── boot ─────────────────────────────────────────────────────────
async function boot() {
  S.cfg = await api("/api/config");
  S.settings = S.cfg.settings;
  S.defaults = Object.fromEntries(S.cfg.params.map((p) => [p.key, p.default]));
  S.params = { ...S.defaults };
  S.level = S.cfg.levels[S.settings.level] ? S.settings.level : "natural";
  engine = new Engine(S.cfg);
  engine.setTarget(S.cfg.levels[S.level].lufs);
  waveFull = new Wave($("wave-full"), "select");
  waveRange = new Wave($("wave-range"), "window");

  buildModelSelect();
  buildSliders();
  buildLevels();
  buildDatalists();
  $("out-dir").value = S.settings.out_dir;
  for (const r of document.querySelectorAll('input[name="fmt"]')) r.checked = r.value === S.settings.format;

  cover = new CoverEditor({
    drop: $("drop"), canvas: $("crop"), empty: $("drop-empty"), out: $("cover-out"), zoom: $("zoom"),
    upload: async (file) => { const fd = new FormData(); fd.append("file", file, file.name || "pasted.png"); return api("/api/covers", { method: "POST", body: fd }); },
  });
  cover.addEventListener("change", coverChanged);
  cover.addEventListener("error", (e) => toast(e.detail));

  wire();
  await fillRecent();
  const m = location.hash.match(/job=([0-9a-f]{12})/);
  if (m) watch(m[1]);
  else { $("url").focus(); refreshInbox(); }
  try {
    if (sessionStorage.getItem("vta:reused")) { sessionStorage.removeItem("vta:reused"); toast("You've used this video before, so your earlier work is open."); }
  } catch { /* fine */ }
}

function buildModelSelect() {
  $("crowd-model").checked = S.settings.crowd !== false;
  $("crowd-model").addEventListener("change", () => rangeChanged(false));
  const sel = $("model");
  for (const [k, label] of Object.entries(S.cfg.models)) sel.add(new Option(label, k, false, k === S.settings.model));
}

function buildSliders() {
  const groups = { clean: "Clean up", tone: "Tone", stems: "Drums and bass" };
  const box = $("sliders");
  for (const [g, title] of Object.entries(groups)) {
    const wrap = document.createElement(g === "stems" ? "details" : "fieldset");
    wrap.className = `group ${g}`;
    const head = document.createElement(g === "stems" ? "summary" : "legend");
    head.textContent = title;
    wrap.append(head);
    for (const p of S.cfg.params.filter((x) => x.group === g)) {
      const row = document.createElement("div");
      row.className = "slider";
      row.innerHTML = `<label for="p-${p.key}">${p.label}</label>
        <input type="range" id="p-${p.key}" min="${p.min}" max="${p.max}" step="${p.step}" value="${p.default}" aria-describedby="h-${p.key}">
        <output id="o-${p.key}" for="p-${p.key}"></output>
        <p class="help" id="h-${p.key}">${p.help}</p>`;
      const input = row.querySelector("input");
      input.addEventListener("input", () => { S.params[p.key] = +input.value; paramsChanged(); });
      input.addEventListener("dblclick", () => { S.params[p.key] = p.default; paramsChanged(); });
      wrap.append(row);
    }
    box.append(wrap);
  }
}

function buildLevels() {
  const seg = $("levels");
  for (const [k, l] of Object.entries(S.cfg.levels)) {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.level = k;
    b.textContent = `${l.label} ${l.lufs}`;
    b.addEventListener("click", () => setLevel(k));
    seg.append(b);
  }
  setLevel(S.level, false);
}

function buildDatalists() {
  const fill = (id, items) => { const dl = $(id); dl.innerHTML = ""; for (const v of items) dl.append(new Option(v)); };
  fill("artists", S.settings.artists || []);
  fill("albums", S.settings.albums || []);
}

async function fillRecent() {
  const sel = $("recent");
  const list = await api("/api/jobs").catch(() => []);
  sel.length = 1;
  for (const j of list.slice(0, 40)) {
    const tag = j.exports ? " (saved)" : "";
    sel.add(new Option(`${j.title || "Untitled"}${tag}`, j.id));
  }
  sel.hidden = !list.length;
}

// ── wiring ───────────────────────────────────────────────────────
function wire() {
  $("fetch-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("fetch-btn");
    btn.disabled = true;
    try {
      openNew(await api("/api/jobs", { method: "POST", body: { url: $("url").value } }));
    } catch (err) {
      showStatus("fetch-status", err.message, true);
    } finally { btn.disabled = false; }
  });
  $("open-inbox").addEventListener("click", () => api("/api/inbox/open", { method: "POST" }).catch((e) => toast(e.message)));
  $("recent").addEventListener("change", (e) => {
    if (!e.target.value) return;
    location.hash = `job=${e.target.value}`;
    location.reload();
  });

  // step 1: listening and choosing the part
  const audio = $("listen");
  $("listen-play").addEventListener("click", () => (audio.paused ? audio.play() : audio.pause()));
  audio.addEventListener("play", () => { $("listen-play").classList.add("playing"); engine.pause(); tick(); });
  audio.addEventListener("pause", () => $("listen-play").classList.remove("playing"));
  waveFull.addEventListener("seek", (e) => { audio.currentTime = e.detail.t; S.stopAt = null; waveFull.setPlayhead(e.detail.t); $("listen-time").textContent = fmt(e.detail.t); });
  waveFull.addEventListener("select", (e) => { setRangeInputs(e.detail.start, e.detail.end); rangeChanged(); });
  for (const id of ["start", "end"]) {
    $(id).addEventListener("change", () => {
      const s = parseTime($("start").value), en = parseTime($("end").value);
      const dur = S.peaks?.duration || 0;
      if (s == null || en == null || en - s < 5 || en > dur + 0.05) {
        toast("Times look like 1:23 or 83.5, end after start, at least 5 seconds apart.");
        setRangeInputs(...waveFull.sel);
        return;
      }
      waveFull.setSelection(s, Math.min(en, dur));
      rangeChanged();
    });
  }
  for (const b of document.querySelectorAll("[data-set]")) {
    b.addEventListener("click", () => {
      const t = audio.currentTime;
      const [s, e] = waveFull.sel;
      if (b.dataset.set === "start" && t < e - 5) waveFull.setSelection(t, e);
      else if (b.dataset.set === "end" && t > s + 5) waveFull.setSelection(s, t);
      else return toast("Start and end need to be at least 5 seconds apart.");
      setRangeInputs(...waveFull.sel);
      rangeChanged();
    });
  }
  $("hear-start").addEventListener("click", () => { audio.currentTime = waveFull.sel[0]; S.stopAt = null; audio.play(); });
  $("hear-end").addEventListener("click", () => { audio.currentTime = Math.max(0, waveFull.sel[1] - 5); S.stopAt = waveFull.sel[1]; audio.play(); });
  for (const id of ["fade-in", "fade-out"]) $(id).addEventListener("change", () => { saveUi(); updateSummary(); });
  $("prepare-btn").addEventListener("click", prepare);

  // step 2: takes
  $("take-play").addEventListener("click", () => { audio.pause(); engine.toggle(); tick(); });
  engine.addEventListener("state", () => $("take-play").classList.toggle("playing", engine.playing));
  engine.addEventListener("measuring", () => ($("level-read").textContent = "Matching loudness…"));
  engine.addEventListener("level", (e) => {
    const g = e.detail.gain;
    $("level-read").textContent = `Loudness matched (${g >= 0 ? "+" : "−"}${Math.abs(g).toFixed(1)} dB)`;
  });
  waveRange.addEventListener("seek", (e) => {
    const rel = e.detail.t - (S.job.range.start + S.preview.start);
    engine.seek(rel);
  });
  waveRange.addEventListener("window", (e) => moveWindow(e.detail.start - S.job.range.start));
  const hold = $("hold-original");
  const holdOn = () => { hold.classList.add("active"); engine.setParams(S.defaults); };
  const holdOff = () => { if (!hold.classList.contains("active")) return; hold.classList.remove("active"); engine.setParams(S.params); };
  hold.addEventListener("pointerdown", (e) => { e.preventDefault(); hold.setPointerCapture(e.pointerId); holdOn(); });
  for (const ev of ["pointerup", "pointercancel", "lostpointercapture"]) hold.addEventListener(ev, holdOff);
  $("tune-reset").addEventListener("click", () => selectTake(S.takeId));
  $("save-clip").addEventListener("click", saveClip);

  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey || e.target.closest("input, select, textarea, [contenteditable]")) return;
    if (!engine.ready) return;
    if (e.code === "Space") { e.preventDefault(); $("listen").pause(); engine.toggle(); tick(); return; }
    if (e.key.toLowerCase() === "o") { if (!e.repeat) holdOn(); return; }
    const i = LETTERS.indexOf(e.key.toUpperCase());
    if (i >= 0 && i < S.cfg.presets.length && takeAvailable(S.cfg.presets[i])) { selectTake(S.cfg.presets[i].id); }
  });
  document.addEventListener("keyup", (e) => { if (e.key.toLowerCase() === "o") holdOff(); });

  // step 3: details
  for (const id of ["m-title", "m-artist", "m-album", "m-album-artist", "m-year", "m-track"]) {
    $(id).addEventListener("input", () => { detailsChanged(); });
  }
  $("use-video-title").addEventListener("click", () => { $("m-title").value = S.job?.info?.title || ""; detailsChanged(); });
  $("use-thumb").addEventListener("click", () => S.job?.thumb_cover && cover.load(S.job.thumb_cover));
  $("cover-clear").addEventListener("click", () => cover.clear());
  for (const b of document.querySelectorAll("[data-mode]")) {
    b.addEventListener("click", () => {
      for (const o of document.querySelectorAll("[data-mode]")) o.setAttribute("aria-pressed", String(o === b));
      cover.setMode(b.dataset.mode);
      $("zoom-wrap").hidden = b.dataset.mode !== "crop";
    });
  }

  // step 4: save
  $("choose-folder").addEventListener("click", async () => {
    const r = await api("/api/choose-folder", { method: "POST" }).catch((e) => toast(e.message));
    if (r?.path) { $("out-dir").value = r.path; updateSummary(); }
  });
  for (const r of document.querySelectorAll('input[name="fmt"]')) r.addEventListener("change", updateSummary);
  $("out-dir").addEventListener("input", updateSummary);
  $("export-btn").addEventListener("click", () => exportSong(false));
  $("forget").addEventListener("click", async () => {
    const mb = S.job.disk_mb ? ` (${S.job.disk_mb} MB)` : "";
    if (!confirm(`Delete the download and stems for this song${mb}?\n\nYour saved song stays where it is. You'd need the link again to make another version.`)) return;
    try {
      await api(`/api/jobs/${S.job.id}`, { method: "DELETE" });
      try { localStorage.removeItem(`vta:${S.job.id}`); } catch { /* fine */ }
      location.hash = "";
      location.reload();
    } catch (e) { toast(e.message); }
  });
  $("reveal").addEventListener("click", () => api("/api/reveal", { method: "POST", body: { path: S.job.last_export.path } }).catch((e) => toast(e.message)));
}

// ── videos dropped into the folder ───────────────────────────────
let inboxTimer, inboxSeen = "";
async function refreshInbox() {
  clearTimeout(inboxTimer);
  if (S.job) { $("inbox").hidden = true; return; }
  try {
    const r = await api("/api/inbox");
    $("inbox").hidden = false;
    $("open-inbox").textContent = r.folder.replace(/^~\//, "").replaceAll("/", " › ");
    const sig = JSON.stringify(r.files);
    if (sig !== inboxSeen) {
      inboxSeen = sig;
      const ul = $("inbox-list");
      ul.innerHTML = "";
      for (const f of r.files) {
        const b = document.createElement("button");
        b.type = "button";
        b.innerHTML = '<span class="file"></span><span class="size"></span>';
        b.querySelector(".file").textContent = f.name;
        b.querySelector(".size").textContent = f.mb >= 1000 ? `${(f.mb / 1000).toFixed(1)} GB` : `${Math.max(0.1, f.mb)} MB`;
        b.addEventListener("click", async () => {
          b.disabled = true;
          try { openNew(await api("/api/jobs/local", { method: "POST", body: { name: f.name } })); }
          catch (e) { toast(e.message); b.disabled = false; }
        });
        const li = document.createElement("li");
        li.append(b);
        ul.append(li);
      }
      $("inbox-empty").hidden = r.files.length > 0;
    }
  } catch { /* server restarting; try again shortly */ }
  if (!document.hidden) inboxTimer = setTimeout(refreshInbox, 3000);
}
document.addEventListener("visibilitychange", () => { if (!document.hidden && !S.job) refreshInbox(); });

function openNew(job) {
  if (job.reused) sessionStorage.setItem("vta:reused", "1");
  location.hash = `job=${job.id}`;
  if (S.job) { location.reload(); return; }
  if (job.reused) toast("You've used this video before, so your earlier work is open.");
  $("inbox").hidden = true;
  watch(job.id);
}

// ── job polling ──────────────────────────────────────────────────
let pollTimer;
async function watch(id) {
  clearTimeout(pollTimer);
  try {
    const job = await api(`/api/jobs/${id}`);
    await renderJob(job);
    if (job.busy) pollTimer = setTimeout(() => watch(id), 600);
  } catch (e) {
    if (e.status === 400) { showStatus("fetch-status", "That song isn't on this Mac any more. Paste the link again.", true); history.replaceState(null, "", location.pathname); }
    else pollTimer = setTimeout(() => watch(id), 1500);
  }
}

function showStatus(id, text, error = false) {
  const el = $(id);
  el.hidden = !text;
  el.textContent = text || "";
  el.classList.toggle("error", error);
}

async function renderJob(job) {
  S.job = job;
  $("inbox").hidden = true;
  if (!$("url").value && job.url) $("url").value = job.url;
  if (!job.url) $("url").required = false;
  const failed = job.error && !job.busy;

  if (job.state === "fetching") {
    const pct = job.progress != null ? ` ${Math.round(job.progress * 100)}%` : "";
    const what = job.local ? `“${job.local.name}”: ` : "";
    showStatus("fetch-status", failed ? `Couldn't get the audio: ${job.error}` : `${what}${job.label}${pct}`, failed);
    $("fetch-btn").disabled = !failed;
    return;
  }
  showStatus("fetch-status", "");
  $("fetch-btn").disabled = false;
  if (!S.sourceShown) await showSource(job);
  enable("step-details", true);   // details can be typed while the takes are being made

  enable("step-takes", true);
  const prep = job.state === "preparing";
  $("prepare-btn").disabled = prep && !failed;
  if (prep) {
    $("takes-empty").hidden = true;
    $("prep").hidden = false;
    renderPrep(job.label, failed ? job.error : null);
  } else {
    $("prep").hidden = true;
  }

  const takesReady = !!job.prepared_key && !prep;
  if (takesReady) {
    $("takes-empty").hidden = true;
    enable("step-save", true);
    if (S.takesKey !== job.prepared_key) await showTakes(job);
  }

  if (job.state === "exporting") {
    showStatus("export-status", failed ? `Couldn't save the song: ${job.error}` : job.label + "…", failed);
    $("export-btn").disabled = !failed;
  } else {
    showStatus("export-status", "");
    $("export-btn").disabled = false;
  }
  if (job.last_export && S.lastExportShown !== job.last_export.at) showResult(job.last_export);
}

function enable(id, on) { $(id).setAttribute("aria-disabled", String(!on)); }

function renderPrep(label, error) {
  const ol = $("prep-steps");
  let idx = PREP_STEPS.indexOf(label);
  if (label.startsWith("Waiting")) idx = 2;
  ol.innerHTML = "";
  PREP_STEPS.forEach((s, i) => {
    const li = document.createElement("li");
    li.textContent = i === idx && label.startsWith("Waiting") ? label : s;
    if (i < idx) li.className = "done";
    if (i === idx) li.className = "now";
    ol.append(li);
  });
  if (error) {
    const li = document.createElement("li");
    li.className = "now";
    li.style.color = "var(--warn)";
    li.textContent = `Stopped: ${error} Press “Make test takes” to try again.`;
    ol.append(li);
  }
}

// ── step 1 ───────────────────────────────────────────────────────
async function showSource(job) {
  S.sourceShown = true;
  $("source").hidden = false;
  const info = job.info || {};
  const thumb = $("thumb");
  thumb.hidden = !job.thumb_cover;
  if (job.thumb_cover) thumb.src = `/api/covers/${job.thumb_cover.id}`;
  $("src-title").textContent = info.title || job.url;
  const codecs = { mp4a: "AAC", aac: "AAC", opus: "Opus", mp3: "MP3", flac: "FLAC", vorbis: "Vorbis", alac: "ALAC" };
  const codec = (info.acodec || "").split(".")[0];
  const q = info.abr ? `${codecs[codec] || (codec.startsWith("pcm") ? "WAV" : codec.toUpperCase())} ${info.abr} kbps` : "";
  const metaEl = $("src-meta");
  metaEl.innerHTML = "";
  for (const t of [info.uploader, fmt(job.duration, 0), q]) {
    if (t) metaEl.append(Object.assign(document.createElement("span"), { textContent: t }));
  }
  if (info.abr && info.abr < 96) {
    metaEl.append(Object.assign(document.createElement("span"), { className: "warn", textContent: "Low-quality upload: keep processing gentle" }));
  }
  $("use-thumb").hidden = !job.thumb_cover;
  $("listen").src = `/api/jobs/${job.id}/listen`;
  S.peaks = await api(`/api/jobs/${job.id}/peaks`);
  waveFull.setPeaks(S.peaks);
  const ui = { ...fromExport(job), ...store.get(job.id) };
  const r = ui.range || job.range || { start: 0, end: job.duration };
  waveFull.setSelection(r.start, Math.min(r.end, S.peaks.duration));
  setRangeInputs(r.start, r.end);
  if (ui.fadeIn != null) $("fade-in").value = ui.fadeIn;
  if (ui.fadeOut != null) $("fade-out").value = ui.fadeOut;
  if (job.model) $("model").value = job.model;
  // song details: what was saved last time, else what you typed before
  const meta = job.meta || ui.meta || {};
  $("m-title").value = meta.title || "";
  $("m-artist").value = meta.artist || "";
  $("m-album").value = meta.album || "";
  $("m-album-artist").value = meta.album_artist || "";
  $("m-year").value = meta.year || "";
  $("m-track").value = meta.track || "";
  if (ui.cover?.id) {
    cover.load({ id: ui.cover.id }, ui.cover.crop).then(() => {
      if (ui.cover.mode) document.querySelector(`[data-mode="${ui.cover.mode}"]`)?.click();
    }).catch(() => {});
  }
  detailsChanged(false);
  rangeChanged(false);
}

function setRangeInputs(s, e) {
  $("start").value = fmt(s);
  $("end").value = fmt(e);
}

function rangeChanged(save = true) {
  if (!S.job) return;
  const [s, e] = waveFull.sel;
  const prepared = S.job.prepared_key && S.job.range;
  const sameCrowd = (S.job.prepared_key || "").endsWith("-crowd") === $("crowd-model").checked;
  const same = prepared && sameCrowd && Math.abs(prepared.start - s) < 0.05 && Math.abs(prepared.end - e) < 0.05;
  $("prepare-btn").textContent = prepared && !same ? (sameCrowd ? "Make takes for the new part" : "Make takes again with this setting") : prepared ? "Make takes again" : "Make test takes";
  if (save) saveUi();
  updateSummary();
}

function tick() {
  cancelAnimationFrame(tick.raf);
  const audio = $("listen");
  const frame = () => {
    if (!audio.paused) {
      if (S.stopAt && audio.currentTime >= S.stopAt) { audio.pause(); S.stopAt = null; }
      waveFull.setPlayhead(audio.currentTime);
      $("listen-time").textContent = fmt(audio.currentTime);
    }
    if (engine.ready && S.job?.range) {
      const abs = S.job.range.start + S.preview.start + engine.position();
      waveRange.setPlayhead(abs);
      $("take-time").textContent = fmt(abs, 0);
    }
    if (!audio.paused || engine.playing) tick.raf = requestAnimationFrame(frame);
  };
  tick.raf = requestAnimationFrame(frame);
}

async function prepare() {
  const [start, end] = waveFull.sel;
  engine.pause();
  $("prepare-btn").disabled = true;
  try {
    await api(`/api/jobs/${S.job.id}/prepare`, { method: "POST", body: { start, end, model: $("model").value, crowd: $("crowd-model").checked } });
    S.takesKey = null;
    $("step-takes").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
    watch(S.job.id);
  } catch (e) {
    toast(e.message);
    $("prepare-btn").disabled = false;
  }
}

// ── step 2 ───────────────────────────────────────────────────────
async function showTakes(job) {
  S.takesKey = job.prepared_key;
  $("takes").hidden = false;
  const a = job.analysis || { notes: [], suggested: [], avoid: [] };
  const notes = $("notes");
  notes.innerHTML = "";
  for (const n of a.notes) {
    const li = document.createElement("li");
    li.className = n.tone;
    li.innerHTML = "<strong></strong><span></span>";
    li.querySelector("strong").textContent = n.title;
    li.querySelector("span").textContent = n.text;
    notes.append(li);
  }
  renderTakeList(a);

  const ui = { ...fromExport(job), ...store.get(job.id) };
  if (ui.level && S.cfg.levels[ui.level] && ui.level !== S.level) setLevel(ui.level, false);
  const len = job.range.end - job.range.start;
  S.preview = { start: ui.previewStart != null && ui.previewKey === job.prepared_key ? ui.previewStart : job.preview.start, length: Math.min(job.preview.length, len) };
  waveRange.setPeaks(S.peaks);
  waveRange.setView(job.range.start, job.range.end);
  waveRange.setSelection(job.range.start, job.range.end);
  waveRange.setWindow(job.range.start + S.preview.start, S.preview.length);
  updateWindowLabel();

  const first = ui.takeKey === job.prepared_key && ui.takeId ? ui.takeId : (a.suggested[0] || "original");
  $("level-read").textContent = "Loading the takes…";
  await engine.load(job.id, S.preview.start, S.preview.length);
  selectTake(first, ui.takeKey === job.prepared_key ? ui.params : null);
  engine.warm(S.cfg.presets.map((p) => p.set));
}

function hasCrowd() { return !!S.job?.stem_stats?.crowd_model; }

function takeAvailable(p) { return p.needs !== "crowd" || hasCrowd(); }

function renderTakeList(a) {
  const ul = $("take-list");
  ul.innerHTML = "";
  const shown = S.cfg.presets.filter(takeAvailable);
  $("keys-hint").textContent = `Keys A–${LETTERS[S.cfg.presets.indexOf(shown[shown.length - 1])]} switch, Space plays`;
  const scr = $("p-screams").closest(".slider");
  scr.classList.toggle("unavailable", !hasCrowd());
  $("p-screams").disabled = !hasCrowd();
  $("h-screams").textContent = hasCrowd() ? S.cfg.params.find((x) => x.key === "screams").help
    : "Make takes again with “Separate screams & cheering” ticked to use this.";
  S.cfg.presets.forEach((p, i) => {
    if (!takeAvailable(p)) return;
    const li = document.createElement("li");
    li.className = "take";
    li.setAttribute("role", "option");
    li.dataset.id = p.id;
    li.tabIndex = 0;
    const sets = Object.entries(p.set).map(([k, v]) => describe(k, v)).join(", ");
    li.innerHTML = `<span class="letter">${LETTERS[i]}</span><div><div class="name"></div><p class="about"></p>${sets ? '<p class="sets"></p>' : ""}</div>`;
    const name = li.querySelector(".name");
    name.textContent = p.name;
    if (a.suggested.includes(p.id)) name.insertAdjacentHTML("beforeend", '<span class="badge">Suggested</span>');
    if (a.avoid.includes(p.id)) name.insertAdjacentHTML("beforeend", '<span class="badge avoid">Too strong for this source</span>');
    li.querySelector(".about").textContent = p.about;
    if (sets) li.querySelector(".sets").textContent = sets;
    li.addEventListener("click", () => selectTake(p.id));
    li.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectTake(p.id); } });
    ul.append(li);
  });
}

function describe(key, v) {
  const p = S.cfg.params.find((x) => x.key === key);
  if (!p) return "";
  return p.unit === "%" ? `${p.label.toLowerCase()} ${v}%` : `${p.label.toLowerCase()} ${v > 0 ? "+" : ""}${v} dB`;
}

function selectTake(id, params = null) {
  let preset = S.cfg.presets.find((p) => p.id === id) || S.cfg.presets[0];
  if (!takeAvailable(preset)) preset = S.cfg.presets[0];
  S.takeId = preset.id;
  S.params = { ...S.defaults, ...preset.set, ...(params || {}) };
  for (const li of document.querySelectorAll(".take")) li.setAttribute("aria-selected", String(li.dataset.id === preset.id));
  paramsChanged();
}

function paramsChanged() {
  const preset = S.cfg.presets.find((p) => p.id === S.takeId);
  const base = { ...S.defaults, ...preset.set };
  let adjusted = false;
  for (const p of S.cfg.params) {
    const v = S.params[p.key];
    $(`p-${p.key}`).value = v;
    const out = $(`o-${p.key}`);
    out.textContent = p.unit === "%" ? `${v}%` : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v)} dB`;
    out.classList.toggle("moved", v !== p.default);
    if (v !== base[p.key]) adjusted = true;
  }
  const letter = LETTERS[S.cfg.presets.indexOf(preset)];
  $("tune-from").textContent = adjusted ? `${letter}, ${preset.name}, adjusted by you.` : `Starting from ${letter}, ${preset.name}.`;
  $("tune-reset").hidden = !adjusted;
  if (!$("hold-original").classList.contains("active")) engine.setParams(S.params);
  saveUi();
  updateSummary();
}

function setLevel(key, save = true) {
  S.level = key;
  for (const b of $("levels").children) b.setAttribute("aria-pressed", String(b.dataset.level === key));
  $("level-help").textContent = S.cfg.levels[key].help;
  engine?.setTarget(S.cfg.levels[key].lufs);
  if (save) { api("/api/settings", { method: "POST", body: { level: key } }).catch(() => {}); saveUi(); updateSummary(); }
}

async function moveWindow(relStart) {
  const len = S.job.range.end - S.job.range.start;
  S.preview.start = Math.max(0, Math.min(len - S.preview.length, relStart));
  waveRange.setWindow(S.job.range.start + S.preview.start, S.preview.length);
  updateWindowLabel();
  $("level-read").textContent = "Loading this section…";
  try {
    await engine.load(S.job.id, S.preview.start, S.preview.length);
    engine.setParams(S.params);
    engine.warm(S.cfg.presets.map((p) => p.set));
  } catch (e) { toast(e.message); }
  saveUi();
}

function updateWindowLabel() {
  const a = S.job.range.start + S.preview.start;
  $("window-label").textContent = `Takes play ${fmt(a, 0)} to ${fmt(a + S.preview.length, 0)}. Drag the band to hear another section.`;
}

async function saveClip() {
  const i = S.cfg.presets.findIndex((p) => p.id === S.takeId);
  const adjusted = !$("tune-reset").hidden;
  const name = `take ${LETTERS[i]}${adjusted ? " adjusted" : ""}`;
  const btn = $("save-clip");
  btn.disabled = true;
  btn.textContent = "Rendering…";
  try {
    const r = await api(`/api/jobs/${S.job.id}/clip`, { method: "POST", raw: true,
      body: { params: S.params, level: S.level, start: S.preview.start, length: S.preview.length, take: name } });
    if (!r.ok) throw new Error((await r.json()).error);
    const blob = await r.blob();
    const cd = r.headers.get("content-disposition") || "";
    const fname = decodeURIComponent((cd.match(/filename\*=utf-8''([^;]+)/i) || [])[1] || "") || `${name}.m4a`;
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: fname });
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  } catch (e) { toast(e.message); }
  btn.disabled = false;
  btn.textContent = "Save this 30-second clip";
}

// ── step 3 ───────────────────────────────────────────────────────
function meta() {
  return {
    title: $("m-title").value.trim(), artist: $("m-artist").value.trim(), album: $("m-album").value.trim(),
    album_artist: $("m-album-artist").value.trim(), year: $("m-year").value.trim(), track: $("m-track").value.trim(),
  };
}

function detailsChanged(save = true) {
  const m = meta();
  $("sp-title").textContent = m.title || "Song name";
  $("sp-artist").textContent = m.artist || "Artist";
  $("m-album-artist").placeholder = m.artist ? `Same as artist: ${m.artist}` : "Same as artist";
  if (save) saveUi();
  updateSummary();
}

function coverChanged() {
  const has = !!cover.id;
  $("cover-tools").hidden = !has;
  $("spotify-row").hidden = !has && !$("m-title").value;
  const size = S.settings.cover_size;
  let spec = `Saved as a ${size} × ${size} JPEG.`;
  if (has && cover.mode === "crop" && cover.sourceSize < size) spec += ` This crop is only ${cover.sourceSize} px across, so it will look a little soft.`;
  $("cover-spec").textContent = has ? spec : "";
  saveUi();
}

// ── step 4 ───────────────────────────────────────────────────────
function format() { return document.querySelector('input[name="fmt"]:checked').value; }

function updateSummary() {
  if (!S.job || !waveFull?.sel) return;
  const [s, e] = waveFull.sel;
  const m = meta();
  const i = S.cfg.presets.findIndex((p) => p.id === S.takeId);
  const adjusted = !$("tune-reset").hidden;
  const name = m.title ? `“${m.title}.${format()}”` : "the song";
  const take = S.job.prepared_key ? `, take ${LETTERS[i]}${adjusted ? " with your changes" : ""}` : "";
  $("save-summary").textContent = `Saves ${name}: ${fmt(s)} to ${fmt(e)} (${fmt(e - s, 0)})${take}, ${S.cfg.levels[S.level].label.toLowerCase()} loudness.`;
}

async function exportSong(overwrite) {
  const m = meta();
  if (!m.title) { $("m-title").focus(); return toast("Give the song a name first."); }
  if (!m.artist) { $("m-artist").focus(); return toast("Add the artist first."); }
  const [s, e] = waveFull.sel;
  const pr = S.job.range;
  if (Math.abs(pr.start - s) > 0.05 || Math.abs(pr.end - e) > 0.05) {
    return toast("You moved start or end after making the takes. Press “Make takes for the new part” first.");
  }
  const spec = cover.spec;
  const body = {
    params: S.params, level: S.level, meta: m, format: format(), out_dir: $("out-dir").value.trim(),
    cover: spec ? { ...spec, size: S.settings.cover_size } : null, overwrite, take: S.takeId,
    fade_in: +$("fade-in").value || 0, fade_out: +$("fade-out").value || 0,
  };
  if (!spec && !confirm("No cover yet. Save without one?")) return;
  $("export-btn").disabled = true;
  try {
    await api(`/api/jobs/${S.job.id}/export`, { method: "POST", body });
    engine.pause();
    watch(S.job.id);
  } catch (err) {
    $("export-btn").disabled = false;
    if (err.status === 409) {
      if (confirm(`There's already a song saved as\n${err.data.path}\n\nReplace it?`)) exportSong(true);
      return;
    }
    showStatus("export-status", err.message, true);
  }
}

function showResult(x) {
  S.lastExportShown = x.at;
  $("result").hidden = false;
  $("result-name").textContent = x.path.split("/").pop();
  const tp = x.true_peak;
  const pic = x.tags?.pictures ? `cover inside (${Math.round(x.tags.picture_bytes / 1024)} KB)` : "no cover";
  const folder = x.path.slice(0, x.path.lastIndexOf("/")).replace(/^\/Users\/[^/]+/, "~");
  $("result-facts").textContent = `${fmt(x.duration, 0)} long, ${x.lufs.toFixed(1)} LUFS, peaks ${tp.toFixed(1)} dB, ${x.format.toUpperCase()}${x.tags?.version ? ` with ID3 v${x.tags.version} tags` : ""}, ${pic}. Saved in ${folder}`;
  $("result-audio").src = `/api/file?path=${encodeURIComponent(x.path)}`;
  $("forget").textContent = S.job.disk_mb ? `Delete working files (${S.job.disk_mb} MB)` : "Delete working files";
  const img = $("result-cover");
  img.hidden = !cover.id;
  if (cover.id) img.src = $("cover-out").toDataURL("image/jpeg");
  buildDatalists();
  api("/api/config").then((c) => { S.settings = c.settings; buildDatalists(); }).catch(() => {});
  fillRecent();
}

// ── remembering this song's choices ──────────────────────────────
function saveUi() {
  if (!S.job) return;
  store.set(S.job.id, {
    range: waveFull.sel ? { start: waveFull.sel[0], end: waveFull.sel[1] } : undefined,
    fadeIn: $("fade-in").value, fadeOut: $("fade-out").value,
    takeId: S.takeId, params: S.params, takeKey: S.takesKey,
    previewStart: S.preview.start, previewKey: S.takesKey,
    meta: meta(), cover: cover?.spec, level: S.level,
  });
}

boot().catch((e) => toast(`The app couldn't start: ${e.message}`));
