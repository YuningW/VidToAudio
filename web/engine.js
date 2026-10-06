// Plays the stems live in the browser with the same math as vta/mix.py:
// stem gains -> mid/side width -> RBJ biquad EQ -> loudness match -> limiter.
// Switching takes or moving a slider changes the sound at the same playhead,
// level-matched, so you compare processing, not volume.

const LIMIT_DB = 20 * Math.log10(0.8); // same ceiling as the export (-1.94 dB)

export function stemGains(p) {
  const v = Math.pow(10, p.vocal / 20);
  return {
    crowd: p.screams / 100,
    vocals_dry: v,
    vocals_rev: (v * p.echo) / 100,
    drums: p.drums / 100,
    bass: p.bass / 100,
    other: p.crowd / 100,
  };
}

export function widthMatrix(p) {
  const s = p.width / 100;
  return [(1 + s) / 2, (1 - s) / 2];
}

export function buildGraph(ctx, cfg) {
  const inputs = {};
  const sum = ctx.createGain();
  for (const s of cfg.stems) {
    inputs[s] = ctx.createGain();
    inputs[s].connect(sum);
  }
  const split = ctx.createChannelSplitter(2);
  const merge = ctx.createChannelMerger(2);
  sum.connect(split);
  const ll = ctx.createGain(), rl = ctx.createGain(), lr = ctx.createGain(), rr = ctx.createGain();
  split.connect(ll, 0); ll.connect(merge, 0, 0);   // L -> L
  split.connect(rl, 1); rl.connect(merge, 0, 0);   // R -> L
  split.connect(lr, 0); lr.connect(merge, 0, 1);   // L -> R
  split.connect(rr, 1); rr.connect(merge, 0, 1);   // R -> R
  let last = merge;
  const eq = {};
  for (const f of cfg.eq) {
    const b = ctx.createBiquadFilter();
    b.type = f.type;
    b.frequency.value = f.freq;
    if (f.q) b.Q.value = f.q;
    b.gain.value = 0;
    last.connect(b);
    last = b;
    eq[f.key] = b;
  }
  return { inputs, width: { ll, rl, lr, rr }, eq, out: last };
}

export function applyParams(n, p, ctx, smooth) {
  const set = (param, v) => {
    if (smooth) param.setTargetAtTime(v, ctx.currentTime, 0.015);
    else param.value = v;
  };
  const g = stemGains(p);
  for (const [s, node] of Object.entries(n.inputs)) set(node.gain, g[s]);
  const [a, b] = widthMatrix(p);
  set(n.width.ll.gain, a); set(n.width.rr.gain, a);
  set(n.width.rl.gain, b); set(n.width.lr.gain, b);
  for (const [k, f] of Object.entries(n.eq)) set(f.gain, p[k]);
}

// ITU-R BS.1770 integrated loudness of an AudioBuffer (K-weighted, gated).
export function integratedLufs(buffer) {
  const fs = buffer.sampleRate;
  // libebur128 K-weighting, valid at any sample rate
  let K = Math.tan((Math.PI * 1681.974450955533) / fs);
  const Vh = Math.pow(10, 3.999843853973347 / 20), Vb = Math.pow(Vh, 0.4996667741545416);
  let Q = 0.7071752369554196, a0 = 1 + K / Q + K * K;
  const s1 = {
    b: [(Vh + (Vb * K) / Q + K * K) / a0, (2 * (K * K - Vh)) / a0, (Vh - (Vb * K) / Q + K * K) / a0],
    a: [(2 * (K * K - 1)) / a0, (1 - K / Q + K * K) / a0],
  };
  K = Math.tan((Math.PI * 38.13547087602444) / fs);
  Q = 0.5003270373238773;
  a0 = 1 + K / Q + K * K;
  const s2 = { b: [1, -2, 1], a: [(2 * (K * K - 1)) / a0, (1 - K / Q + K * K) / a0] };

  const block = Math.round(0.4 * fs), hop = Math.round(0.1 * fs);
  const n = buffer.length;
  const nBlocks = n >= block ? Math.floor((n - block) / hop) + 1 : 0;
  if (!nBlocks) return -99;
  const hopSums = new Float64Array(Math.ceil(n / hop));
  for (let c = 0; c < Math.min(2, buffer.numberOfChannels); c++) {
    const x = buffer.getChannelData(c);
    let x1 = 0, x2 = 0, y1 = 0, y2 = 0, u1 = 0, u2 = 0, z1 = 0, z2 = 0;
    for (let i = 0; i < n; i++) {
      const xi = x[i];
      const y = s1.b[0] * xi + s1.b[1] * x1 + s1.b[2] * x2 - s1.a[0] * y1 - s1.a[1] * y2;
      x2 = x1; x1 = xi; y2 = y1; y1 = y;
      const z = y - 2 * u1 + u2 - s2.a[0] * z1 - s2.a[1] * z2;
      u2 = u1; u1 = y; z2 = z1; z1 = z;
      hopSums[(i / hop) | 0] += z * z;
    }
  }
  // a 400 ms block is exactly four 100 ms hops (75% overlap)
  const z = new Float64Array(nBlocks);
  for (let b = 0; b < nBlocks; b++) {
    z[b] = (hopSums[b] + hopSums[b + 1] + hopSums[b + 2] + hopSums[b + 3]) / block;
  }
  const L = (v) => -0.691 + 10 * Math.log10(v);
  let sum = 0, cnt = 0;
  for (const v of z) if (L(v) > -70) { sum += v; cnt++; }
  if (!cnt) return -99;
  const rel = L(sum / cnt) - 10;
  sum = 0; cnt = 0;
  for (const v of z) if (L(v) > -70 && L(v) > rel) { sum += v; cnt++; }
  return cnt ? L(sum / cnt) : -99;
}

export class Engine extends EventTarget {
  constructor(cfg) {
    super();
    this.cfg = cfg;
    this.defaults = Object.fromEntries(cfg.params.map((p) => [p.key, p.default]));
    this.params = { ...this.defaults };
    this.target = -13;
    this.ctx = null;
    this.buffers = null;
    this.sources = [];
    this.playing = false;
    this.offset = 0;
    this.loop = true;
    this.cache = new Map();
    this.levelToken = 0;
    this.lufs = null;
  }

  _ctx() {
    if (this.ctx) return this.ctx;
    this.ctx = new AudioContext({ sampleRate: this.cfg.sr, latencyHint: "playback" });
    this.nodes = buildGraph(this.ctx, this.cfg);
    this.level = this.ctx.createGain();
    // Web Audio's compressor adds automatic make-up gain; take it back off so
    // the browser plays at the same level the export writes.
    const limiter = this.ctx.createDynamicsCompressor();
    limiter.threshold.value = LIMIT_DB;
    limiter.knee.value = 0;
    limiter.ratio.value = 20;
    limiter.attack.value = 0.003;
    limiter.release.value = 0.06;
    const fullRange = LIMIT_DB + -LIMIT_DB / 20;
    const makeup = this.ctx.createGain();
    makeup.gain.value = Math.pow(10, (0.6 * fullRange) / 20);
    this.nodes.out.connect(this.level).connect(limiter).connect(makeup).connect(this.ctx.destination);
    applyParams(this.nodes, this.params, this.ctx, false);
    return this.ctx;
  }

  async load(jobId, start, length) {
    const ctx = this._ctx();
    const bufs = await Promise.all(this.cfg.stems.map(async (s) => {
      const r = await fetch(`/api/jobs/${jobId}/stem/${s}?start=${start}&length=${length}`);
      if (!r.ok) throw new Error(`Couldn't load the ${s} stem`);
      return ctx.decodeAudioData(await r.arrayBuffer());
    }));
    const wasPlaying = this.playing;
    this._stopSources();
    this.buffers = Object.fromEntries(this.cfg.stems.map((s, i) => [s, bufs[i]]));
    this.duration = bufs[0].duration;
    this.offset = 0;
    this.cache.clear();
    this.lufs = null;
    await this.relevel(true);
    if (wasPlaying) this.play();
    this._emit("loaded");
  }

  get ready() { return !!this.buffers; }

  setTarget(lufs) {
    this.target = lufs;
    this._applyLevel(this.lufs, true);
  }

  setParams(p, { measureNow = false } = {}) {
    this.params = { ...this.defaults, ...p };
    if (this.ctx) applyParams(this.nodes, this.params, this.ctx, true);
    const cached = this.cache.get(this._key(this.params));
    if (cached !== undefined) { this._applyLevel(cached, true); return; }
    clearTimeout(this._t);
    this._t = setTimeout(() => this.relevel(), measureNow ? 0 : 180);
  }

  _key(p) { return JSON.stringify(this.cfg.params.map((d) => p[d.key])); }

  async measure(p) {
    const key = this._key(p);
    if (this.cache.has(key)) return this.cache.get(key);
    const first = this.buffers[this.cfg.stems[0]];
    const off = new OfflineAudioContext(2, first.length, first.sampleRate);
    const n = buildGraph(off, this.cfg);
    applyParams(n, p, off, false);
    for (const s of this.cfg.stems) {
      const src = off.createBufferSource();
      src.buffer = this.buffers[s];
      src.connect(n.inputs[s]);
      src.start();
    }
    n.out.connect(off.destination);
    const out = await off.startRendering();
    const l = integratedLufs(out);
    this.cache.set(key, l);
    return l;
  }

  async relevel(immediate = false) {
    if (!this.buffers) return;
    const token = ++this.levelToken;
    this._emit("measuring");
    const l = await this.measure(this.params);
    if (token !== this.levelToken) return;
    this._applyLevel(l, !immediate);
  }

  // Measure a list of settings ahead of time so switching takes is instant.
  async warm(list) {
    for (const p of list) {
      if (!this.buffers) return;
      await this.measure({ ...this.defaults, ...p });
    }
  }

  _applyLevel(lufs, smooth) {
    if (lufs == null) return;
    this.lufs = lufs;
    const db = lufs <= -70 ? 0 : Math.max(-30, Math.min(30, this.target - lufs));
    const g = Math.pow(10, db / 20);
    if (smooth) this.level.gain.setTargetAtTime(g, this.ctx.currentTime, 0.03);
    else this.level.gain.value = g;
    this._emit("level", { lufs, gain: db });
  }

  play(at) {
    if (!this.buffers) return;
    const ctx = this._ctx();
    ctx.resume();
    this._stopSources();
    const pos = at ?? this.offset;
    const when = ctx.currentTime + 0.03;
    this.sources = this.cfg.stems.map((s) => {
      const src = ctx.createBufferSource();
      src.buffer = this.buffers[s];
      src.loop = this.loop;
      src.connect(this.nodes.inputs[s]);
      src.start(when, Math.min(pos, this.duration - 0.01));
      return src;
    });
    this.sources[0].onended = () => {
      if (this.sources.length && !this.loop) { this.playing = false; this.offset = 0; this._emit("state"); }
    };
    this.t0 = when - pos;
    this.playing = true;
    this._emit("state");
  }

  pause() {
    if (!this.playing) return;
    this.offset = this.position();
    this._stopSources();
    this.playing = false;
    this._emit("state");
  }

  toggle() { this.playing ? this.pause() : this.play(); }

  seek(pos) {
    pos = Math.max(0, Math.min(pos, (this.duration || 0) - 0.05));
    if (this.playing) this.play(pos);
    else { this.offset = pos; this._emit("state"); }
  }

  setLoop(on) {
    this.loop = on;
    for (const s of this.sources) s.loop = on;
  }

  position() {
    if (!this.playing || !this.ctx) return this.offset;
    const t = Math.max(0, this.ctx.currentTime - this.t0);
    return this.loop ? t % this.duration : Math.min(t, this.duration);
  }

  _stopSources() {
    const old = this.sources;
    this.sources = [];
    for (const s of old) { try { s.onended = null; s.stop(); } catch { /* already stopped */ } }
  }

  _emit(type, detail) { this.dispatchEvent(new CustomEvent(type, { detail })); }
}
