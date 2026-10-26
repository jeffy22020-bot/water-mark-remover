// Image watermark remover: paint/auto-detect a mask, remove, compare, save. Supports batches.
import { busy, loadImage, post, runJob, saveFile, saveMany, showError, state, toast, url } from './api.js';
import { showCompare } from './compare.js';
import { renderHome } from './home.js';
import { hydrateIcons, icon } from './icons.js';
import { MaskEditor, bindToolbar, toolbarHtml } from './mask-editor.js';

export function initImage(section, app) {
  const home = document.createElement('div');
  const ed = document.createElement('div');
  ed.className = 'editor hidden';
  section.append(home, ed);

  renderHome(home, {
    kind: 'image',
    multiple: true,
    title: '<span class="grad-text">AI</span> Image Watermark Remover',
    sub: 'Remove watermarks, logos, text, captions and date stamps from your pictures in seconds while preserving the image quality.',
    button: 'Upload',
    line: 'Drop images or Paste <a href="#" data-url>URL</a> (Image, PDF, or Video)',
    limits: 'Select several pictures at once to clean a whole batch with the same settings.',
    formats: 'JPG · PNG · WEBP · HEIC · TIFF · BMP · GIF · AVIF',
    features: [
      { icon: 'sparkles', title: 'AI rebuilds the background', text: 'The LaMa inpainting model fills the area behind the watermark with matching texture and detail.' },
      { icon: 'wand', title: 'Automatic detection', text: 'Finds text watermarks – even faint, tiled ones – or paint over any logo with the brush.' },
      { icon: 'shield', title: 'Private & offline', text: 'Your pictures never leave this computer. No accounts, no limits, no uploads.' },
    ],
  }, { onOpen: (files) => app.route(files), onUrl: (u) => app.openUrl(u) });

  ed.innerHTML = `
    <div class="editor-top">
      <button class="btn ghost" data-back data-icon="back">Back</button>
      <span class="title"></span>
      <span class="sep"></span>
      <div class="edit-tools" style="display:flex;gap:8px;align-items:center">${toolbarHtml()}</div>
      <span class="spacer"></span>
      <button class="btn ghost" data-toggle-mask data-icon="eye">Hide selection</button>
    </div>
    <div class="editor-body">
      <div style="flex:1;position:relative;min-width:0">
        <div class="mask-host" style="position:absolute;inset:0"></div>
        <div class="compare-host hidden" style="position:absolute;inset:0;background:#0c0c0e"></div>
        <div class="stage-hint" data-hint>Paint over the watermark, or click <b>Auto-detect</b></div>
      </div>
      <aside class="side">
        <div class="side-scroll">
          <div data-edit-panel>
            <h3>Remove watermark</h3>
            <p class="hint">1. Paint over the watermark with the brush, draw a box, or press <b>Auto-detect</b>.<br>2. Click <b>Remove watermark</b>.</p>
            <div class="field-label">Quality</div>
            <div class="segmented" data-quality><button data-v="ai">AI (best)</button><button data-v="fast">Fast</button></div>
            <p class="hint" data-quality-hint style="margin-top:8px"></p>
          </div>
          <div data-result-panel class="hidden">
            <div class="result-banner">${icon('checkCircle')}<span>Watermark removed. Drag the slider to compare.</span></div>
            <p class="hint" style="margin-top:14px">Something left behind? Click <b>Touch up</b> and paint over it again.</p>
          </div>
          <div data-batch class="hidden" style="margin-top:22px">
            <h3>All images (<span data-count></span>)</h3>
            <p class="hint">Same watermark on every picture? Mark it on this one and apply it to all, or let the app find it on each picture.</p>
            <div style="display:flex;flex-direction:column;gap:8px">
              <button class="btn" data-apply-all data-icon="layers">Use this selection on all images</button>
              <button class="btn" data-auto-all data-icon="wand">Auto-remove on all images</button>
              <button class="btn" data-save-all data-icon="save">Save all results…</button>
            </div>
          </div>
        </div>
        <div class="side-foot">
          <button class="btn-primary" data-remove>${icon('sparkles')} Remove watermark</button>
          <div data-result-actions class="hidden" style="display:flex;flex-direction:column;gap:8px">
            <button class="btn-primary" data-save>${icon('save')} Save as…</button>
            <div style="display:flex;gap:8px">
              <button class="btn" style="flex:1;justify-content:center" data-touchup data-icon="brush">Touch up</button>
              <button class="btn" style="flex:1;justify-content:center" data-revert data-icon="undo">Undo</button>
            </div>
          </div>
        </div>
      </aside>
    </div>
    <div class="strip hidden"></div>`;
  hydrateIcons(ed);

  const $ = (s) => ed.querySelector(s);
  const editor = new MaskEditor($('.mask-host'), { onChange: () => updateHint() });
  const compareHost = $('.compare-host');
  let items = [];
  let active = 0;
  let mode = 'edit';
  let quality = state.status?.settings?.image_quality || 'ai';

  bindToolbar(ed, editor, {
    onDetect: () => autoDetect(),
    isActive: () => section.classList.contains('active') && !ed.classList.contains('hidden') && mode === 'edit',
  });

  // ------------------------------------------------------------ quality
  const qButtons = ed.querySelectorAll('[data-quality] button');
  const setQuality = (q) => {
    quality = q;
    qButtons.forEach((b) => b.classList.toggle('active', b.dataset.v === q));
    $('[data-quality-hint]').textContent = q === 'ai'
      ? `Rebuilds what was under the watermark with AI (runs on your ${state.status?.gpu ? 'graphics card' : 'processor'}).`
      : 'Instant. Good for small marks on plain backgrounds.';
  };
  qButtons.forEach((b) => b.addEventListener('click', () => setQuality(b.dataset.v)));
  setQuality(quality);

  // ------------------------------------------------------------ helpers
  const cur = () => items[active];
  const previewUrl = (f) => url(`/api/files/${f.id}/preview`, { v: f.id });

  function updateHint() {
    const hint = $('[data-hint]');
    hint.classList.toggle('hidden', mode !== 'edit' || editor.hasMask());
  }

  function setMode(m) {
    mode = m;
    const result = m === 'result';
    $('.mask-host').classList.toggle('hidden', result);
    compareHost.classList.toggle('hidden', !result);
    $('.edit-tools').style.visibility = result ? 'hidden' : '';
    $('[data-toggle-mask]').classList.toggle('hidden', result);
    $('[data-edit-panel]').classList.toggle('hidden', result);
    $('[data-result-panel]').classList.toggle('hidden', !result);
    $('[data-remove]').classList.toggle('hidden', result);
    $('[data-result-actions]').classList.toggle('hidden', !result);
    $('[data-revert]').disabled = !(cur()?.history.length);
    updateHint();
  }

  async function showItem(i, { keepMask = true } = {}) {
    if (keepMask && items[active] && mode === 'edit' && editor.w) items[active].mask = editor.hasMask() ? editor.toDataURL() : null;
    active = i;
    const item = cur();
    $('.title').textContent = item.current.name;
    renderStrip();
    if (item.history.length && item.showResult) {
      setMode('result');
      const before = item.history[item.history.length - 1];
      showCompare(compareHost, previewUrl(before), previewUrl(item.current), item.current.meta.width, item.current.meta.height);
      return;
    }
    setMode('edit');
    await loadBase(item);
  }

  async function loadBase(item) {
    const img = await loadImage(previewUrl(item.current));
    img.draggable = false;
    editor.setBase(img, item.current.meta.width, item.current.meta.height);
    if (item.mask) await editor.addMask(item.mask, { replace: true });
    editor.history = [];
    updateHint();
  }

  function renderStrip() {
    const strip = ed.querySelector('.strip');
    const batch = items.length > 1;
    strip.classList.toggle('hidden', !batch);
    $('[data-batch]').classList.toggle('hidden', !batch);
    $('[data-count]').textContent = items.length;
    if (!batch) return;
    strip.innerHTML = '';
    items.forEach((it, i) => {
      const b = document.createElement('button');
      b.className = `thumb${i === active ? ' active' : ''}`;
      b.title = it.source.name;
      b.style.backgroundImage = `url("${url(`/api/files/${it.current.id}/preview`, { max_side: 240 })}")`;
      if (it.history.length) b.innerHTML = '<span class="badge">✓</span>';
      b.onclick = () => showItem(i).catch(showError);
      strip.appendChild(b);
    });
  }

  // ------------------------------------------------------------ actions
  async function autoDetect() {
    try {
      const res = await busy('Looking for watermarks…', () => post('/api/image/detect', { file_id: cur().current.id }));
      if (!res.found) {
        toast('No text watermark found. Paint over the watermark with the brush instead.');
        return;
      }
      await editor.addMask(res.mask);
      toast('Watermark marked in red. Adjust with the brush or eraser if needed, then click Remove watermark.');
    } catch (e) { showError(e); }
  }

  async function remove() {
    if (!editor.hasMask()) {
      toast('Paint over the watermark first (or click Auto-detect).');
      return;
    }
    const item = cur();
    try {
      const result = await runJob(
        () => post('/api/image/remove', { file_id: item.current.id, mask: editor.toDataURL(), mode: quality }),
        { title: 'Removing watermark…' },
      );
      item.history.push(item.current);
      item.current = result;
      item.mask = null;
      item.showResult = true;
      await showItem(active);
    } catch (e) { showError(e); }
  }

  async function applyToAll(auto) {
    let mask = null;
    if (!auto) {
      if (!editor.hasMask()) { toast('Mark the watermark on this picture first.'); return; }
      mask = editor.toDataURL();
    }
    try {
      const ids = items.map((it) => it.current.id);
      if (auto) {
        const common = await busy('Comparing pictures…', () => post('/api/image/detect-batch', { file_ids: ids }));
        mask = common.mask;
      }
      const results = await runJob(
        () => post('/api/image/remove-batch', { file_ids: ids, mask, mode: quality }),
        { title: `Cleaning ${items.length} images…` },
      );
      let changed = 0;
      results.forEach((r, i) => {
        if (!r.changed) return;
        changed++;
        items[i].history.push(items[i].current);
        items[i].current = r;
        items[i].mask = null;
        items[i].showResult = true;
      });
      toast(changed ? `Done – ${changed} of ${items.length} images cleaned.` : 'No watermark found automatically. Mark it on one picture and use “Use this selection on all images”.');
      await showItem(active);
    } catch (e) { showError(e); }
  }

  ed.querySelector('[data-back]').addEventListener('click', () => close());
  $('[data-remove]').addEventListener('click', remove);
  $('[data-save]').addEventListener('click', () => saveFile(cur().current).catch(showError));
  $('[data-touchup]').addEventListener('click', () => {
    cur().showResult = false;
    showItem(active).catch(showError);
  });
  $('[data-revert]').addEventListener('click', () => {
    const item = cur();
    if (!item.history.length) return;
    item.current = item.history.pop();
    item.showResult = item.history.length > 0;
    showItem(active).catch(showError);
  });
  $('[data-apply-all]').addEventListener('click', () => applyToAll(false));
  $('[data-auto-all]').addEventListener('click', () => applyToAll(true));
  $('[data-save-all]').addEventListener('click', () => {
    const done = items.filter((it) => it.history.length).map((it) => it.current);
    if (!done.length) { toast('Nothing cleaned yet.'); return; }
    saveMany(done).catch(showError);
  });
  const toggleMask = $('[data-toggle-mask]');
  let maskShown = true;
  toggleMask.addEventListener('click', () => {
    maskShown = !maskShown;
    editor.setMaskVisible(maskShown);
    toggleMask.innerHTML = `${icon(maskShown ? 'eye' : 'eyeOff')} ${maskShown ? 'Hide selection' : 'Show selection'}`;
  });

  function close() {
    ed.classList.add('hidden');
    home.classList.remove('hidden');
    items = [];
  }

  return {
    async open(files) {
      items = files.map((f) => ({ source: f, current: f, history: [], mask: null, showResult: false }));
      active = 0;
      home.classList.add('hidden');
      ed.classList.remove('hidden');
      await showItem(0, { keepMask: false });
    },
    async openWithMask(file, maskUrl) {
      await this.open([file]);
      if (maskUrl) await editor.addMask(maskUrl);
    },
    get editing() { return !ed.classList.contains('hidden'); },
    get current() { return items[active]?.current; },
    close,
  };
}

