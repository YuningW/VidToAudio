// Cover editor. The canvas shows exactly the square that will be saved:
// drag to move the picture under it, zoom to crop tighter, or switch to
// "fit" to keep the whole picture on a blurred fill (as vta/cover.py does).

export class CoverEditor extends EventTarget {
  constructor({ drop, canvas, empty, out, zoom, upload }) {
    super();
    Object.assign(this, { drop, canvas, empty, out, zoomInput: zoom, upload });
    this.img = null;
    this.id = null;
    this.mode = "crop";
    this.zoom = 1;
    this.cx = 0; this.cy = 0;
    this._wire();
  }

  get spec() {
    if (!this.id) return null;
    const s = this._crop();
    return { id: this.id, mode: this.mode, crop: { x: Math.round(s.x), y: Math.round(s.y), size: Math.round(s.size) } };
  }

  get sourceSize() { return this.img ? Math.round(this._crop().size) : 0; }

  async load(record, crop = null) {
    const img = new Image();
    img.src = `/api/covers/${record.id}`;
    await img.decode();
    this.img = img;
    this.id = record.id;
    this.zoom = 1;
    this.zoomInput.value = 1;
    this.cx = img.naturalWidth / 2;
    this.cy = img.naturalHeight / 2;
    // portraits usually have faces in the upper part: start the crop there
    if (img.naturalHeight > img.naturalWidth * 1.15) this.cy = img.naturalWidth / 2 + (img.naturalHeight - img.naturalWidth) * 0.2;
    if (crop?.size) {   // reopening a saved song: put the crop back where it was
      this.zoom = Math.max(1, Math.min(4, Math.min(img.naturalWidth, img.naturalHeight) / crop.size));
      this.zoomInput.value = this.zoom;
      this.cx = crop.x + crop.size / 2;
      this.cy = crop.y + crop.size / 2;
    }
    this.canvas.hidden = false;
    this.empty.hidden = true;
    this.drop.classList.add("has");
    this._changed();
  }

  clear() {
    this.img = null; this.id = null;
    this.canvas.hidden = true; this.empty.hidden = false;
    this.drop.classList.remove("has");
    this._changed();
  }

  setMode(m) { this.mode = m; this._changed(); }

  _crop() {
    const w = this.img.naturalWidth, h = this.img.naturalHeight;
    const size = Math.min(w, h) / this.zoom;
    const x = Math.max(0, Math.min(w - size, this.cx - size / 2));
    const y = Math.max(0, Math.min(h - size, this.cy - size / 2));
    return { x, y, size };
  }

  render(canvas) {
    const g = canvas.getContext("2d");
    const S = canvas.width;
    g.clearRect(0, 0, S, S);
    if (!this.img) return;
    const img = this.img, w = img.naturalWidth, h = img.naturalHeight;
    g.imageSmoothingQuality = "high";
    if (this.mode === "fit") {
      // cheap, portable blur: shrink a lot, then stretch back up
      const tiny = document.createElement("canvas");
      tiny.width = tiny.height = 20;
      const m = Math.min(w, h);
      tiny.getContext("2d").drawImage(img, (w - m) / 2, (h - m) / 2, m, m, 0, 0, 20, 20);
      g.drawImage(tiny, 0, 0, S, S);
      g.fillStyle = "rgba(0,0,0,.25)";
      g.fillRect(0, 0, S, S);
      const k = Math.min(S / w, S / h);
      g.drawImage(img, (S - w * k) / 2, (S - h * k) / 2, w * k, h * k);
    } else {
      const c = this._crop();
      g.drawImage(img, c.x, c.y, c.size, c.size, 0, 0, S, S);
    }
  }

  _changed() {
    const dpr = devicePixelRatio || 1;
    const size = Math.round((this.canvas.clientWidth || 360) * dpr);
    if (this.canvas.width !== size) { this.canvas.width = size; this.canvas.height = size; }
    this.render(this.canvas);
    this.render(this.out);
    this.dispatchEvent(new Event("change"));
  }

  async _file(file) {
    if (!file || !file.type.startsWith("image/")) {
      this.dispatchEvent(new CustomEvent("error", { detail: "That isn't a picture." }));
      return;
    }
    try {
      this.load(await this.upload(file));
    } catch (e) {
      this.dispatchEvent(new CustomEvent("error", { detail: e.message }));
    }
  }

  _wire() {
    const d = this.drop;
    const fileInput = document.getElementById("cover-file");
    d.addEventListener("click", () => { if (!this.img) fileInput.click(); });
    d.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && !this.img) { e.preventDefault(); fileInput.click(); } });
    fileInput.addEventListener("change", () => { this._file(fileInput.files[0]); fileInput.value = ""; });
    for (const ev of ["dragenter", "dragover"]) d.addEventListener(ev, (e) => { e.preventDefault(); d.classList.add("over"); });
    for (const ev of ["dragleave", "drop"]) d.addEventListener(ev, () => d.classList.remove("over"));
    d.addEventListener("drop", (e) => { e.preventDefault(); this._file(e.dataTransfer.files[0]); });
    document.addEventListener("paste", (e) => {
      if (e.target.closest && e.target.closest("input, textarea")) return;
      const item = [...(e.clipboardData?.items || [])].find((i) => i.type.startsWith("image/"));
      if (item) { e.preventDefault(); this._file(item.getAsFile()); }
    });
    this.zoomInput.addEventListener("input", () => { this.zoom = +this.zoomInput.value; this._changed(); });
    this.canvas.addEventListener("wheel", (e) => {
      if (!this.img || this.mode !== "crop") return;
      e.preventDefault();
      this.zoom = Math.max(1, Math.min(4, this.zoom * (e.deltaY < 0 ? 1.06 : 1 / 1.06)));
      this.zoomInput.value = this.zoom;
      this._changed();
    }, { passive: false });
    this.canvas.addEventListener("pointerdown", (e) => {
      if (!this.img || this.mode !== "crop") return;
      this.canvas.setPointerCapture(e.pointerId);
      const start = { x: e.clientX, y: e.clientY, cx: this.cx, cy: this.cy };
      const c = this._crop();
      const scale = c.size / this.canvas.clientWidth;
      // keep the centre inside the reachable range so dragging feels direct
      this.cx = c.x + c.size / 2; this.cy = c.y + c.size / 2;
      start.cx = this.cx; start.cy = this.cy;
      const move = (ev) => {
        this.cx = start.cx - (ev.clientX - start.x) * scale;
        this.cy = start.cy - (ev.clientY - start.y) * scale;
        const k = this._crop();
        this.cx = k.x + k.size / 2; this.cy = k.y + k.size / 2;
        this._changed();
      };
      const up = () => { this.canvas.removeEventListener("pointermove", move); this.canvas.removeEventListener("pointerup", up); };
      this.canvas.addEventListener("pointermove", move);
      this.canvas.addEventListener("pointerup", up);
    });
    this.canvas.addEventListener("keydown", (e) => {
      if (!this.img) return;
      const step = this._crop().size * 0.03;
      const k = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
      if (k) { e.preventDefault(); this.cx += k[0]; this.cy += k[1]; this._changed(); }
    });
  }
}
