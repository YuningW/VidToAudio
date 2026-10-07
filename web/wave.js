// Waveform on a canvas.
//   mode "select": two handles choose the part of the video you want.
//   mode "window": a band shows the preview section. Inside it, click to set
//   the marker takes start from, drag to mark a phrase to loop. Click outside
//   it to move the section there.
// Emits: select {start, end, final}, window {start}, seek {t}, phrase {start, end}.

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

export class Wave extends EventTarget {
  constructor(canvas, mode) {
    super();
    this.c = canvas;
    this.mode = mode;
    this.peaks = null;
    this.view = [0, 1];
    this.sel = null;
    this.win = null;      // [start, length]
    this.playhead = null;
    this.phrase = null;   // [start, end] seconds
    this.cue = null;      // seconds
    this.minLen = 5;
    new ResizeObserver(() => this.draw()).observe(canvas);
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => this.draw());
    canvas.addEventListener("pointerdown", (e) => this._down(e));
  }

  setPeaks(p) { this.peaks = p; this.view = [0, p.duration]; this.draw(); }
  setView(t0, t1) { this.view = [t0, t1]; this.draw(); }
  setSelection(s, e) { this.sel = [s, e]; this.draw(); }
  setWindow(s, len) { this.win = [s, len]; this.draw(); }
  setPlayhead(t) { this.playhead = t; this.draw(); }
  setPhrase(r) { this.phrase = r; this.draw(); }
  setCue(t) { this.cue = t; this.draw(); }

  _x(t) { const [a, b] = this.view; return ((t - a) / (b - a)) * this.w; }
  _t(x) { const [a, b] = this.view; return a + (x / this.w) * (b - a); }

  draw() {
    const c = this.c, dpr = devicePixelRatio || 1;
    const w = c.clientWidth, h = c.clientHeight;
    if (!w || !h) return;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
      c.width = Math.round(w * dpr); c.height = Math.round(h * dpr);
    }
    this.w = w; this.h = h;
    const g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    if (!this.peaks) return;
    const ink = css("--ink"), wave = css("--wave"), dim = css("--wave-dim"), signal = css("--signal");
    const soft = css("--signal-soft");
    const { peak, rms, duration } = this.peaks;
    const n = peak.length;
    const top = this.mode === "select" ? 16 : 6;
    const mid = top + (h - top) / 2, amp = (h - top) / 2 - 2;
    const [s, e] = this.sel || [0, duration];

    if (this.win) {
      const x0 = this._x(this.win[0]), x1 = this._x(this.win[0] + this.win[1]);
      g.fillStyle = soft;
      g.fillRect(x0, 0, x1 - x0, h);
    }
    const step = 3;
    for (let x = 0; x < w; x += step) {
      const t0 = this._t(x), t1 = this._t(x + step);
      let i0 = Math.max(0, Math.floor((t0 / duration) * n)), i1 = Math.min(n, Math.ceil((t1 / duration) * n));
      if (i1 <= i0) i1 = Math.min(n, i0 + 1);
      let pk = 0, rm = 0;
      for (let i = i0; i < i1; i++) { if (peak[i] > pk) pk = peak[i]; if (rms[i] > rm) rm = rms[i]; }
      const inside = (t0 + t1) / 2 >= s && (t0 + t1) / 2 <= e;
      const ph = Math.max(1, pk * amp), rh = Math.max(1, rm * amp * 1.6);
      g.fillStyle = inside ? wave : dim;
      g.fillRect(x, mid - ph, step - 1, ph * 2);
      if (inside) {
        g.fillStyle = ink;
        g.fillRect(x, mid - Math.min(rh, ph), step - 1, Math.min(rh, ph) * 2);
      }
    }
    if (this.win) {
      const x0 = this._x(this.win[0]), x1 = this._x(this.win[0] + this.win[1]);
      g.fillStyle = signal;
      g.fillRect(x0, 0, 2, h);
      g.fillRect(x1 - 2, 0, 2, h);
    }
    if (this.phrase) {
      const x0 = this._x(this.phrase[0]), x1 = this._x(this.phrase[1]);
      g.fillStyle = soft;
      g.fillRect(x0, 0, x1 - x0, h);
      g.fillRect(x0, 0, x1 - x0, h);   // twice: the phrase reads darker than the section
      g.fillStyle = signal;
      g.fillRect(x0, h - 4, x1 - x0, 4);
    }
    if (this.cue != null) {
      const x = this._x(this.cue);
      g.fillStyle = signal;
      g.beginPath();
      g.moveTo(x - 6, 0); g.lineTo(x + 6, 0); g.lineTo(x, 8); g.closePath();
      g.fill();
      g.fillRect(x - 0.75, 0, 1.5, h);
    }
    if (this.mode === "select" && this.sel) {
      for (const t of this.sel) {
        const x = this._x(t);
        g.fillStyle = signal;
        g.fillRect(x - 1, top - 2, 2, h - top + 2);
        g.beginPath();
        g.roundRect(x - 7, 0, 14, 16, 4);
        g.fill();
        g.fillStyle = css("--signal-ink");
        g.fillRect(x - 2.5, 4, 1.2, 8);
        g.fillRect(x + 1.3, 4, 1.2, 8);
      }
    }
    if (this.playhead != null && this.playhead >= this.view[0] && this.playhead <= this.view[1]) {
      g.fillStyle = ink;
      g.fillRect(this._x(this.playhead) - 0.75, 0, 1.5, h);
    }
  }

  _down(e) {
    if (!this.peaks) return;
    const rect = this.c.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const t = this._t(x);
    let drag = null;
    if (this.mode === "select" && this.sel) {
      const ds = Math.abs(x - this._x(this.sel[0])), de = Math.abs(x - this._x(this.sel[1]));
      if (Math.min(ds, de) < 12) drag = ds <= de ? "start" : "end";
    } else if (this.mode === "window" && this.win) {
      if (t >= this.win[0] && t <= this.win[0] + this.win[1]) drag = { from: t, x, moved: false };
    }
    if (!drag) {
      if (this.mode === "window" && this.win) {
        const [a, b] = this.view;
        const start = Math.max(a, Math.min(b - this.win[1], t - this.win[1] / 2));
        this.setWindow(start, this.win[1]);
        this._emit("window", { start });
      } else {
        this._emit("seek", { t });
      }
      return;
    }
    this.c.setPointerCapture(e.pointerId);
    const move = (ev) => {
      const tt = this._t(Math.max(0, Math.min(this.w, ev.clientX - rect.left)));
      if (drag === "start") {
        this.sel[0] = Math.max(0, Math.min(tt, this.sel[1] - this.minLen));
        this._emit("select", { start: this.sel[0], end: this.sel[1], final: false });
      } else if (drag === "end") {
        this.sel[1] = Math.min(this.peaks.duration, Math.max(tt, this.sel[0] + this.minLen));
        this._emit("select", { start: this.sel[0], end: this.sel[1], final: false });
      } else if (drag.moved || Math.abs(ev.clientX - rect.left - drag.x) > 4) {
        drag.moved = true;
        const lo = this.win[0], hi = this.win[0] + this.win[1];
        const a = Math.max(lo, Math.min(drag.from, tt)), b = Math.min(hi, Math.max(drag.from, tt));
        this.phrase = [a, b];
      }
      this.draw();
    };
    const up = (ev) => {
      this.c.removeEventListener("pointermove", move);
      this.c.removeEventListener("pointerup", up);
      if (drag === "start" || drag === "end") {
        this._emit("select", { start: this.sel[0], end: this.sel[1], final: true });
      } else if (drag.moved && this.phrase && this.phrase[1] - this.phrase[0] >= 1) {
        this._emit("phrase", { start: this.phrase[0], end: this.phrase[1] });
      } else if (drag.moved) {
        this.phrase = null;   // too short to loop: treat as a click
        this.draw();
        this._emit("seek", { t: drag.from });
      } else {
        this._emit("seek", { t: this._t(ev.clientX - rect.left) });
      }
    };
    this.c.addEventListener("pointermove", move);
    this.c.addEventListener("pointerup", up);
  }

  _emit(type, detail) { this.dispatchEvent(new CustomEvent(type, { detail })); }
}
