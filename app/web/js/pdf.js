// PDF watermark remover: object-level removal with live preview, plus a paint mode for scanned PDFs.
import { escapeHtml, loadImage, post, runJob, saveFile, showError, state, toast, url } from './api.js';
import { renderHome } from './home.js';
import { hydrateIcons, icon } from './icons.js';
import { MaskEditor, bindToolbar, toolbarHtml } from './mask-editor.js';

export function initPdf(section, app) {
  const home = document.createElement('div');
  const ed = document.createElement('div');
  ed.className = 'editor hidden';
  section.append(home, ed);

  renderHome(home, {
    kind: 'pdf',
    title: 'Remove Watermark from <span class="grad-text">PDF</span>',
    sub: 'Remove unwanted text watermarks, stamps and logos from your PDF in a few clicks. Download a clean PDF without spending hours on editing.',
    button: 'Upload PDF',
    line: 'Drop PDF or Paste <a href="#" data-url>URL</a>',
    limits: '(No page limit)',
    features: [
      { icon: 'layers', title: 'Removes the watermark object', text: 'Text, images and stamps added on top of the page are deleted – the real text stays sharp and selectable.' },
      { icon: 'paint', title: 'Works on scanned PDFs too', text: 'Paint mode removes watermarks that are part of a scanned page image, on every page at once.' },
      { icon: 'shield', title: 'Stays on your PC', text: 'Confidential documents are never uploaded anywhere.' },
    ],
  }, { onOpen: (files) => app.route(files), onUrl: (u) => app.openUrl(u) });

  ed.innerHTML = `
    <div class="editor-top">
      <button class="btn ghost" data-back data-icon="back">Back</button>
      <span class="title"></span>
      <span class="sep"></span>
      <div class="paint-tools hidden" style="display:flex;gap:8px;align-items:center">${toolbarHtml()}</div>
      <span class="spacer"></span>
      <div class="page-nav">
        <button class="btn small" data-prev data-icon="chevL"></button>
        <span data-page-label>Page 1 of 1</span>
        <button class="btn small" data-next data-icon="chevR"></button>
      </div>
      <button class="btn ghost" data-original data-icon="eye">Hold to see original</button>
    </div>
    <div class="editor-body">
      <div style="flex:1;position:relative;min-width:0">
        <div class="page-view"><img alt="Page preview"></div>
        <div class="mask-host hidden" style="position:absolute;inset:0"></div>
      </div>
      <aside class="side">
        <div class="side-scroll">
          <div data-objects>
            <h3>Watermarks found</h3>
            <p class="hint">Ticked items are removed. The preview updates as you tick and untick.</p>
            <div style="display:flex;gap:6px;margin-bottom:10px">
              <button class="btn small" data-all>Select all</button><button class="btn small" data-none>Select none</button>
            </div>
            <div class="cand-list"></div>
            <p class="hint" style="margin-top:18px">Watermark is part of a scanned page? <button class="linklike" data-paint-mode>Use paint mode</button></p>
          </div>
          <div data-paint class="hidden">
            <h3>Paint mode</h3>
            <p class="hint">For scanned PDFs. Paint over the watermark on this page (or click <b>Auto-detect</b>). The same area is cleaned on the pages you choose. Pages are turned into pictures, so their text is no longer selectable.</p>
            <div class="field-label">Apply to</div>
            <div class="segmented" data-scope><button data-v="all" class="active">All pages</button><button data-v="page">This page</button></div>
            <div class="field-label" style="margin-top:14px">Quality</div>
            <div class="segmented" data-quality><button data-v="ai">AI (best)</button><button data-v="fast">Fast</button></div>
            <p class="hint" style="margin-top:14px"><button class="linklike" data-object-mode>Back to detected watermarks</button></p>
          </div>
          <div data-result class="hidden">
            <div class="result-banner">${icon('checkCircle')}<span>Watermark removed from your PDF.</span></div>
            <p class="hint" style="margin-top:12px">Flip through the pages to check the result, then save.</p>
          </div>
        </div>
        <div class="side-foot">
          <button class="btn-primary" data-remove>${icon('sparkles')} Remove selected watermarks</button>
          <button class="btn-primary hidden" data-paint-remove>${icon('sparkles')} Remove watermark</button>
          <div data-result-actions class="hidden" style="display:flex;flex-direction:column;gap:8px">
            <button class="btn-primary" data-save>${icon('save')} Save PDF as…</button>
            <button class="btn" style="justify-content:center" data-again data-icon="undo">Back to editing</button>
          </div>
        </div>
      </aside>
    </div>`;
  hydrateIcons(ed);

  const $ = (s) => ed.querySelector(s);
  const pageImg = $('.page-view img');
  const editor = new MaskEditor($('.mask-host'));
  let file = null;
  let result = null;
  let candidates = [];
  let page = 0;
  let pages = 1;
  let mode = 'objects'; // objects | paint | result
  let scope = 'all';
  let quality = state.status?.settings?.image_quality || 'ai';
  let showOriginal = false;
  let previewTimer = null;

  bindToolbar($('.paint-tools'), editor, {
    onDetect: () => paintDetect(),
    isActive: () => section.classList.contains('active') && mode === 'paint',
  });

  const selectedIds = () => candidates.filter((c) => c.selected).map((c) => c.id);

  function pageUrl() {
    if (mode === 'result' && !showOriginal) return url(`/api/pdf/${result.id}/page/${page}`, { w: 1200 });
    const remove = showOriginal ? '' : selectedIds().join(',');
    return url(`/api/pdf/${file.id}/page/${page}`, { w: 1200, remove });
  }

  function refreshPreview(delay = 0) {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(() => {
      pageImg.style.opacity = '.55';
      const src = pageUrl();
      const img = new Image();
      img.onload = () => { pageImg.src = src; pageImg.style.opacity = '1'; };
      img.onerror = () => { pageImg.style.opacity = '1'; };
      img.src = src;
    }, delay);
    $('[data-page-label]').textContent = `Page ${page + 1} of ${pages}`;
    $('[data-prev]').disabled = page <= 0;
    $('[data-next]').disabled = page >= pages - 1;
  }

  function renderCandidates() {
    const list = $('.cand-list');
    if (!candidates.length) {
      list.innerHTML = `<div class="empty"><b>No removable watermark objects found</b>The watermark is probably part of the page image (a scanned PDF). Use paint mode to remove it.</div>`;
      $('[data-remove]').disabled = true;
      return;
    }
    list.innerHTML = '';
    for (const c of candidates) {
      const el = document.createElement('label');
      el.className = `cand${c.selected ? ' on' : ''}`;
      const pagesText = c.page_count === c.total_pages ? (c.total_pages > 1 ? `on all ${c.total_pages} pages` : 'on the page') : `on ${c.page_count} of ${c.total_pages} pages`;
      el.innerHTML = `
        <input type="checkbox" ${c.selected ? 'checked' : ''}>
        <div><div class="lbl">${escapeHtml(c.label)}</div><div class="meta">${pagesText}</div>
          <div class="reasons">${c.reasons.map((r) => `<span class="chip${c.score >= 0.6 ? ' hot' : ''}">${escapeHtml(r)}</span>`).join('')}</div></div>
        ${c.thumb ? `<img src="${c.thumb}" alt="">` : '<span></span>'}`;
      el.querySelector('input').addEventListener('change', (e) => {
        c.selected = e.target.checked;
        el.classList.toggle('on', c.selected);
        $('[data-remove]').disabled = !selectedIds().length;
        const first = c.pages[0] - 1;
        if (c.selected && !c.pages.includes(page + 1)) page = first;
        refreshPreview(120);
      });
      list.appendChild(el);
    }
    $('[data-remove]').disabled = !selectedIds().length;
  }

  function setMode(m) {
    mode = m;
    $('[data-objects]').classList.toggle('hidden', m !== 'objects');
    $('[data-paint]').classList.toggle('hidden', m !== 'paint');
    $('[data-result]').classList.toggle('hidden', m !== 'result');
    $('[data-remove]').classList.toggle('hidden', m !== 'objects');
    $('[data-paint-remove]').classList.toggle('hidden', m !== 'paint');
    $('[data-result-actions]').classList.toggle('hidden', m !== 'result');
    $('.paint-tools').classList.toggle('hidden', m !== 'paint');
    $('.mask-host').classList.toggle('hidden', m !== 'paint');
    $('.page-view').classList.toggle('hidden', m === 'paint');
    $('[data-original]').classList.toggle('hidden', m === 'paint');
  }

  async function loadPaintPage() {
    const img = await loadImage(url(`/api/pdf/${file.id}/page/${page}`, { w: 1400 }));
    const keep = editor.w ? editor.toDataURL() : null;
    editor.setBase(img, img.naturalWidth, img.naturalHeight);
    if (keep) await editor.addMask(keep, { replace: true });
    $('[data-page-label]').textContent = `Page ${page + 1} of ${pages}`;
    $('[data-prev]').disabled = page <= 0;
    $('[data-next]').disabled = page >= pages - 1;
  }

  async function enterPaint() {
    setMode('paint');
    editor.w = 0;
    await loadPaintPage();
  }

  async function paintDetect() {
    try {
      const res = await runJob(() => post('/api/pdf/raster-detect', { file_id: file.id, page }), { title: 'Looking for the watermark…' });
      if (!res.mask) { toast('Could not find it automatically – paint over the watermark instead.'); return; }
      await editor.addMask(res.mask);
      toast(res.method === 'pages' ? 'Found what repeats on every page. Check the red area, then click Remove watermark.' : 'Text marked in red – remove the marks on real content with the eraser.');
    } catch (e) { showError(e); }
  }

  async function removeObjects() {
    try {
      result = await runJob(() => post('/api/pdf/remove', { file_id: file.id, ids: selectedIds() }), { title: 'Removing watermarks…', cancellable: false });
      setMode('result');
      refreshPreview();
    } catch (e) { showError(e); }
  }

  async function removePaint() {
    if (!editor.hasMask()) { toast('Paint over the watermark first (or click Auto-detect).'); return; }
    try {
      result = await runJob(() => post('/api/pdf/raster-remove', {
        file_id: file.id, mask: editor.toDataURL(), pages: scope === 'all' ? null : [page], mode: quality,
      }), { title: 'Cleaning pages…' });
      setMode('result');
      refreshPreview();
    } catch (e) { showError(e); }
  }

  const go = (delta) => {
    page = Math.min(pages - 1, Math.max(0, page + delta));
    if (mode === 'paint') loadPaintPage().catch(showError);
    else refreshPreview();
  };
  $('[data-prev]').addEventListener('click', () => go(-1));
  $('[data-next]').addEventListener('click', () => go(1));
  const orig = $('[data-original]');
  const setOrig = (on) => { if (showOriginal !== on) { showOriginal = on; refreshPreview(); } };
  orig.addEventListener('pointerdown', () => setOrig(true));
  orig.addEventListener('pointerup', () => setOrig(false));
  orig.addEventListener('pointerleave', () => setOrig(false));
  $('[data-all]').addEventListener('click', () => { candidates.forEach((c) => { c.selected = true; }); renderCandidates(); refreshPreview(); });
  $('[data-none]').addEventListener('click', () => { candidates.forEach((c) => { c.selected = false; }); renderCandidates(); refreshPreview(); });
  $('[data-paint-mode]').addEventListener('click', () => enterPaint().catch(showError));
  $('[data-object-mode]').addEventListener('click', () => { setMode('objects'); refreshPreview(); });
  $('[data-remove]').addEventListener('click', removeObjects);
  $('[data-paint-remove]').addEventListener('click', removePaint);
  $('[data-save]').addEventListener('click', () => saveFile(result).catch(showError));
  $('[data-again]').addEventListener('click', () => { result = null; setMode('objects'); refreshPreview(); });
  $('[data-back]').addEventListener('click', () => close());
  ed.querySelectorAll('[data-scope] button').forEach((b) => b.addEventListener('click', () => {
    scope = b.dataset.v;
    ed.querySelectorAll('[data-scope] button').forEach((x) => x.classList.toggle('active', x === b));
  }));
  const qButtons = ed.querySelectorAll('[data-quality] button');
  const setQuality = (q) => { quality = q; qButtons.forEach((b) => b.classList.toggle('active', b.dataset.v === q)); };
  qButtons.forEach((b) => b.addEventListener('click', () => setQuality(b.dataset.v)));
  setQuality(quality);

  function close() {
    ed.classList.add('hidden');
    home.classList.remove('hidden');
    file = null;
  }

  return {
    async open(files) {
      file = files[0];
      result = null;
      page = 0;
      pages = file.meta.pages || 1;
      home.classList.add('hidden');
      ed.classList.remove('hidden');
      $('.title').textContent = file.name;
      setMode('objects');
      pageImg.removeAttribute('src');
      $('.cand-list').innerHTML = '';
      try {
        const analysis = await runJob(() => post('/api/pdf/analyze', { file_id: file.id }), { title: 'Looking for watermarks…', cancellable: false });
        candidates = analysis.candidates;
        pages = analysis.page_count;
        renderCandidates();
        refreshPreview();
        if (!candidates.length) toast('No watermark objects found – this looks like a scanned PDF. Try paint mode.');
      } catch (e) {
        showError(e);
        close();
      }
    },
    get editing() { return !ed.classList.contains('hidden'); },
    close,
  };
}

