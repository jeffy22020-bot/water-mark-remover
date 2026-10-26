// Paint a watermark mask over an image or video: brush, rectangle, eraser, zoom & pan, undo.
import { loadImage } from './api.js';

const MAX_MASK_SIDE = 1600;
const MASK_COLOR = '#ff3b3b';

export class MaskEditor {
  constructor(wrap, { onChange } = {}) {
    this.wrap = wrap;
    this.onChange = onChange || (() => {});
    wrap.classList.add('stage-wrap');
    wrap.innerHTML = `
      <div class="stage"><div class="base"></div><canvas class="mask"></canvas><canvas class="draft"></canvas></div>
      <div class="brush-cursor"></div>`;
    this.stage = wrap.querySelector('.stage');
    this.base = wrap.querySelector('.base');
    this.mask = wrap.querySelector('canvas.mask');
    this.draft = wrap.querySelector('canvas.draft');
    this.cursor = wrap.querySelector('.brush-cursor');
    this.ctx = this.mask.getContext('2d', { willReadFrequently: true });
    this.dctx = this.draft.getContext('2d');
    this.tool = 'brush';
    this.size = 36;
    this.zoom = 1;
    this.panX = 0;
    this.panY = 0;
    this.w = 0;
    this.h = 0;
    this.history = [];
    this.future = [];
    this.enabled = true;
    this.userZoomed = false;
    this.spaceDown = false;
    this._bind();
    new ResizeObserver(() => { if (!this.userZoomed) this.fit(); }).observe(wrap);
    this.setTool('brush');
  }

  /** Use ``el`` (<img>, <video> or <canvas>) of natural size w x h as the picture to paint on. */
  setBase(el, w, h, { keepMask = false } = {}) {
    this.w = w;
    this.h = h;
    this.base.replaceChildren(el);
    this.stage.style.width = `${w}px`;
    this.stage.style.height = `${h}px`;
    if (!keepMask) {
      const s = Math.min(1, MAX_MASK_SIDE / Math.max(w, h));
      const mw = Math.max(1, Math.round(w * s));
      const mh = Math.max(1, Math.round(h * s));
      this.mask.width = this.draft.width = mw;
      this.mask.height = this.draft.height = mh;
      this.history = [];
      this.future = [];
    }
    this.userZoomed = false;
    this.fit();
  }

  // ------------------------------------------------------------ view
  fit() {
    if (!this.w) return;
    const r = this.wrap.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const z = Math.min((r.width - 40) / this.w, (r.height - 40) / this.h, 2);
    this.zoom = Math.max(0.02, z);
    this.panX = (r.width - this.w * this.zoom) / 2;
    this.panY = (r.height - this.h * this.zoom) / 2;
    this.userZoomed = false;
    this._apply();
  }

  zoomBy(factor) {
    const r = this.wrap.getBoundingClientRect();
    this.zoomAt(r.width / 2, r.height / 2, this.zoom * factor);
  }

  zoomAt(sx, sy, z) {
    z = Math.min(16, Math.max(0.02, z));
    const ix = (sx - this.panX) / this.zoom;
    const iy = (sy - this.panY) / this.zoom;
    this.zoom = z;
    this.panX = sx - ix * z;
    this.panY = sy - iy * z;
    this.userZoomed = true;
    this._apply();
  }

  _apply() {
    this.stage.style.transform = `translate(${this.panX}px, ${this.panY}px) scale(${this.zoom})`;
    this.cursor.style.width = this.cursor.style.height = `${this.size}px`;
  }

  setTool(tool) {
    this.tool = tool;
    this.wrap.classList.remove('tool-brush', 'tool-eraser', 'tool-rect', 'tool-hand');
    this.wrap.classList.add(`tool-${tool}`);
    this.cursor.classList.toggle('on', tool === 'brush' || tool === 'eraser');
  }

  setSize(px) {
    this.size = px;
    this._apply();
  }

  setMaskVisible(on) {
    this.mask.classList.toggle('hide', !on);
  }

  // ------------------------------------------------------------ painting
  _pt(e) {
    const r = this.wrap.getBoundingClientRect();
    const sx = e.clientX - r.left;
    const sy = e.clientY - r.top;
    const s = this.mask.width / this.w;
    return { x: ((sx - this.panX) / this.zoom) * s, y: ((sy - this.panY) / this.zoom) * s, sx, sy };
  }

  get _lineWidth() {
    return (this.size / this.zoom) * (this.mask.width / this.w);
  }

  _stroke(a, b) {
    const c = this.ctx;
    c.globalCompositeOperation = this.tool === 'eraser' ? 'destination-out' : 'source-over';
    c.strokeStyle = MASK_COLOR;
    c.lineWidth = this._lineWidth;
    c.lineCap = 'round';
    c.lineJoin = 'round';
    c.beginPath();
    c.moveTo(a.x, a.y);
    c.lineTo(b.x + 0.01, b.y);
    c.stroke();
    c.globalCompositeOperation = 'source-over';
  }

  _bind() {
    const wrap = this.wrap;
    wrap.addEventListener('pointerdown', (e) => {
      if (!this.w) return;
      const pan = e.button === 1 || this.spaceDown || this.tool === 'hand' || !this.enabled;
      if (pan) {
        e.preventDefault();
        wrap.setPointerCapture(e.pointerId);
        this.drag = { mode: 'pan', x: e.clientX, y: e.clientY, px: this.panX, py: this.panY };
        wrap.classList.add('panning');
        return;
      }
      if (e.button !== 0) return;
      wrap.setPointerCapture(e.pointerId);
      const p = this._pt(e);
      this._snapshot();
      if (this.tool === 'rect') {
        this.drag = { mode: 'rect', x0: p.x, y0: p.y };
        return;
      }
      this.drag = { mode: 'paint', last: p };
      this._stroke(p, p);
    });
    wrap.addEventListener('pointermove', (e) => {
      const r = wrap.getBoundingClientRect();
      this.cursor.style.left = `${e.clientX - r.left}px`;
      this.cursor.style.top = `${e.clientY - r.top}px`;
      const d = this.drag;
      if (!d) return;
      if (d.mode === 'pan') {
        this.panX = d.px + (e.clientX - d.x);
        this.panY = d.py + (e.clientY - d.y);
        this.userZoomed = true;
        this._apply();
      } else if (d.mode === 'paint') {
        const p = this._pt(e);
        this._stroke(d.last, p);
        d.last = p;
      } else if (d.mode === 'rect') {
        const p = this._pt(e);
        const c = this.dctx;
        c.clearRect(0, 0, this.draft.width, this.draft.height);
        c.fillStyle = 'rgba(255,59,59,.45)';
        c.strokeStyle = '#fff';
        c.lineWidth = Math.max(1, 1.5 / this.zoom * (this.mask.width / this.w));
        c.setLineDash([6, 4].map((v) => v / this.zoom * (this.mask.width / this.w)));
        const x = Math.min(d.x0, p.x);
        const y = Math.min(d.y0, p.y);
        c.fillRect(x, y, Math.abs(p.x - d.x0), Math.abs(p.y - d.y0));
        c.strokeRect(x, y, Math.abs(p.x - d.x0), Math.abs(p.y - d.y0));
        d.end = p;
      }
    });
    const end = () => {
      const d = this.drag;
      this.drag = null;
      wrap.classList.remove('panning');
      if (!d) return;
      if (d.mode === 'rect') {
        this.dctx.clearRect(0, 0, this.draft.width, this.draft.height);
        if (d.end && Math.abs(d.end.x - d.x0) > 1 && Math.abs(d.end.y - d.y0) > 1) {
          this.ctx.fillStyle = MASK_COLOR;
          this.ctx.fillRect(Math.min(d.x0, d.end.x), Math.min(d.y0, d.end.y), Math.abs(d.end.x - d.x0), Math.abs(d.end.y - d.y0));
        } else {
          this.history.pop();
        }
        this.onChange();
      } else if (d.mode === 'paint') {
        this.onChange();
      }
    };
    wrap.addEventListener('pointerup', end);
    wrap.addEventListener('pointercancel', end);
    wrap.addEventListener('wheel', (e) => {
      if (!this.w) return;
      e.preventDefault();
      const r = wrap.getBoundingClientRect();
      this.zoomAt(e.clientX - r.left, e.clientY - r.top, this.zoom * Math.exp(-e.deltaY * 0.0015));
    }, { passive: false });
    window.addEventListener('keydown', (e) => {
      if (e.code === 'Space' && this.isVisible() && !isTyping(e)) {
        this.spaceDown = true;
        wrap.classList.add('panning');
        e.preventDefault();
      }
    });
    window.addEventListener('keyup', (e) => {
      if (e.code === 'Space') {
        this.spaceDown = false;
        wrap.classList.remove('panning');
      }
    });
  }

  isVisible() {
    return this.wrap.offsetParent !== null;
  }

  // ------------------------------------------------------------ history & data
  _snapshot() {
    this.history.push(this.ctx.getImageData(0, 0, this.mask.width, this.mask.height));
    if (this.history.length > 20) this.history.shift();
    this.future = [];
  }

  undo() {
    if (!this.history.length) return;
    this.future.push(this.ctx.getImageData(0, 0, this.mask.width, this.mask.height));
    this.ctx.putImageData(this.history.pop(), 0, 0);
    this.onChange();
  }

  redo() {
    if (!this.future.length) return;
    this.history.push(this.ctx.getImageData(0, 0, this.mask.width, this.mask.height));
    this.ctx.putImageData(this.future.pop(), 0, 0);
    this.onChange();
  }

  clear() {
    if (!this.hasMask()) return;
    this._snapshot();
    this.ctx.clearRect(0, 0, this.mask.width, this.mask.height);
    this.onChange();
  }

  hasMask() {
    if (!this.mask.width) return false;
    const data = this.ctx.getImageData(0, 0, this.mask.width, this.mask.height).data;
    for (let i = 3; i < data.length; i += 16) if (data[i]) return true;
    return false;
  }

  /** Paint a mask PNG (data URL) on top of the current mask. */
  async addMask(dataUrl, { replace = false } = {}) {
    const img = await loadImage(dataUrl);
    this._snapshot();
    if (replace) this.ctx.clearRect(0, 0, this.mask.width, this.mask.height);
    this.ctx.globalCompositeOperation = 'source-over';
    this.ctx.drawImage(img, 0, 0, this.mask.width, this.mask.height);
    this.onChange();
  }

  toDataURL() {
    return this.mask.toDataURL('image/png');
  }

  exportState() {
    return this.ctx.getImageData(0, 0, this.mask.width, this.mask.height);
  }

  importState(imageData) {
    this.history = [];
    this.future = [];
    if (imageData) this.ctx.putImageData(imageData, 0, 0);
    else this.ctx.clearRect(0, 0, this.mask.width, this.mask.height);
    this.onChange();
  }
}

export function isTyping(e) {
  const t = e.target;
  return t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
}

/** Toolbar markup shared by the image, video and PDF editors. */
export function toolbarHtml({ detect = true } = {}) {
  return `
    <div class="toolgroup">
      <button class="tool active" data-tool="brush" title="Brush (B)" data-icon="brush"></button>
      <button class="tool" data-tool="rect" title="Rectangle (R)" data-icon="rect"></button>
      <button class="tool" data-tool="eraser" title="Eraser (E)" data-icon="eraser"></button>
      <button class="tool" data-tool="hand" title="Move (hold Space)" data-icon="hand"></button>
    </div>
    <label class="size-ctl" title="Brush size ([ and ])">Size <input type="range" min="4" max="160" value="36" data-size></label>
    <div class="toolgroup">
      <button class="tool" data-act="undo" title="Undo (Ctrl+Z)" data-icon="undo"></button>
      <button class="tool" data-act="redo" title="Redo (Ctrl+Y)" data-icon="redo"></button>
      <button class="tool" data-act="clear" title="Clear selection" data-icon="trash"></button>
    </div>
    <div class="toolgroup">
      <button class="tool" data-act="zoomOut" title="Zoom out" data-icon="zoomOut"></button>
      <button class="tool" data-act="fit" title="Fit to screen" data-icon="fit"></button>
      <button class="tool" data-act="zoomIn" title="Zoom in" data-icon="zoomIn"></button>
    </div>
    ${detect ? '<button class="btn" data-act="detect" data-icon="wand">Auto-detect</button>' : ''}`;
}

/** Wire toolbar buttons + keyboard shortcuts to an editor. ``isActive`` gates shortcuts. */
export function bindToolbar(root, editor, { onDetect, isActive }) {
  const tools = root.querySelectorAll('[data-tool]');
  const select = (t) => {
    editor.setTool(t);
    tools.forEach((b) => b.classList.toggle('active', b.dataset.tool === t));
  };
  tools.forEach((b) => b.addEventListener('click', () => select(b.dataset.tool)));
  const size = root.querySelector('[data-size]');
  size?.addEventListener('input', () => editor.setSize(+size.value));
  const acts = {
    undo: () => editor.undo(),
    redo: () => editor.redo(),
    clear: () => editor.clear(),
    zoomIn: () => editor.zoomBy(1.25),
    zoomOut: () => editor.zoomBy(0.8),
    fit: () => editor.fit(),
    detect: () => onDetect?.(),
  };
  root.querySelectorAll('[data-act]').forEach((b) => b.addEventListener('click', () => acts[b.dataset.act]?.()));
  window.addEventListener('keydown', (e) => {
    if (!isActive() || isTyping(e)) return;
    const k = e.key.toLowerCase();
    if ((e.ctrlKey || e.metaKey) && k === 'z') { e.preventDefault(); e.shiftKey ? editor.redo() : editor.undo(); }
    else if ((e.ctrlKey || e.metaKey) && k === 'y') { e.preventDefault(); editor.redo(); }
    else if (e.ctrlKey || e.metaKey || e.altKey) return;
    else if (k === 'b') select('brush');
    else if (k === 'r') select('rect');
    else if (k === 'e') select('eraser');
    else if (k === 'h') select('hand');
    else if (k === '[' || k === ']') {
      const v = Math.min(160, Math.max(4, editor.size + (k === ']' ? 6 : -6)));
      editor.setSize(v);
      if (size) size.value = v;
    }
  });
  return { select };
}
