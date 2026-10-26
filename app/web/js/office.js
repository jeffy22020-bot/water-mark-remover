// PowerPoint & Word: remove watermark shapes, and clean pictures that have a watermark burned in.
import { busy, escapeHtml, post, runJob, saveFile, showError, toast } from './api.js';
import { renderHome } from './home.js';
import { hydrateIcons, icon } from './icons.js';
import { cleanPicture } from './picture-modal.js';

export function initOffice(section, app) {
  const home = document.createElement('div');
  const ed = document.createElement('div');
  ed.className = 'editor hidden';
  section.append(home, ed);

  renderHome(home, {
    kind: 'office',
    title: 'Remove Watermarks from <span class="grad-text">PowerPoint</span> &amp; Word',
    sub: 'Delete “Confidential”, “Draft”, “Evaluation Warning” and logo watermarks from presentations and documents – and clean watermarked pictures inside them.',
    button: 'Upload File',
    line: 'Drop a presentation or document here',
    limits: 'PowerPoint: .pptx .ppsx .potx · Word: .docx .dotx',
    formats: 'Older .ppt / .doc / .odp files are converted automatically when <b>LibreOffice</b> is installed.',
    features: [
      { icon: 'layers', title: 'Finds watermark shapes', text: 'Checks slide masters, layouts, every slide and page headers for watermark text, logos and stamps.' },
      { icon: 'image', title: 'Cleans pictures too', text: 'A watermark inside a photo on a slide? Open it, remove the mark with AI, and it is replaced everywhere.' },
      { icon: 'file', title: 'Keeps your formatting', text: 'Only the watermark is removed; everything else in the file stays exactly as it was.' },
    ],
  }, { onOpen: (files) => app.route(files), onUrl: (u) => app.openUrl(u) });

  ed.innerHTML = `
    <div class="editor-top">
      <button class="btn ghost" data-back data-icon="back">Back</button>
      <span class="title"></span>
      <span class="spacer"></span>
    </div>
    <div class="editor-body">
      <div class="doc-summary">
        <div class="doc-card">
          <h2 data-heading>Watermarks found</h2>
          <p data-sub>Ticked items will be removed.</p>
          <div style="display:flex;gap:6px;margin-bottom:12px">
            <button class="btn small" data-all>Select all</button><button class="btn small" data-none>Select none</button>
          </div>
          <div class="cand-list"></div>
        </div>
      </div>
      <aside class="side">
        <div class="side-scroll">
          <h3>Pictures in this file</h3>
          <p class="hint">Is the watermark inside a picture? Click <b>Clean</b> to remove it from the picture with AI.</p>
          <div class="media-grid"></div>
          <div data-result class="hidden" style="margin-top:18px">
            <div class="result-banner">${icon('checkCircle')}<span data-result-text>Watermark removed.</span></div>
          </div>
        </div>
        <div class="side-foot">
          <button class="btn-primary" data-remove>${icon('sparkles')} Remove selected</button>
          <button class="btn-primary hidden" data-save>${icon('save')} Save as…</button>
        </div>
      </aside>
    </div>`;
  hydrateIcons(ed);

  const $ = (s) => ed.querySelector(s);
  let file = null;
  let analysis = null;
  let changed = false;

  function render() {
    const list = $('.cand-list');
    const cands = analysis.candidates;
    const kind = analysis.type === 'pptx' ? 'presentation' : 'document';
    $('[data-heading]').textContent = cands.length ? 'Watermarks found' : 'No watermark shapes found';
    $('[data-sub]').textContent = cands.length
      ? 'Ticked items will be removed. Items found on the slide master or in headers show on every slide/page.'
      : `This ${kind} has no watermark text or logo shapes${changed ? ' left' : ''}. If the watermark is inside a picture, clean it on the right.`;
    list.innerHTML = '';
    for (const c of cands) {
      const el = document.createElement('label');
      el.className = `cand${c.selected ? ' on' : ''}`;
      const where = c.where.length > 3 ? `${c.where.slice(0, 3).join(', ')} +${c.where.length - 3} more` : c.where.join(', ');
      el.innerHTML = `
        <input type="checkbox" ${c.selected ? 'checked' : ''}>
        <div><div class="lbl">${escapeHtml(c.label)}</div><div class="meta">${escapeHtml(where)}${c.count > 1 ? ` · ${c.count}×` : ''}</div>
          <div class="reasons">${c.reasons.map((r) => `<span class="chip${c.score >= 0.6 ? ' hot' : ''}">${escapeHtml(r)}</span>`).join('')}</div></div>
        ${c.thumb ? `<img src="${c.thumb}" alt="">` : '<span></span>'}`;
      el.querySelector('input').addEventListener('change', (e) => {
        c.selected = e.target.checked;
        el.classList.toggle('on', c.selected);
        updateButtons();
      });
      list.appendChild(el);
    }
    const grid = $('.media-grid');
    grid.innerHTML = analysis.media.length ? '' : '<div class="empty" style="grid-column:1/-1">No pictures</div>';
    for (const m of analysis.media) {
      const el = document.createElement('div');
      el.className = 'media';
      el.innerHTML = `<img alt="" src="${m.thumb || ''}"><button class="btn small" style="justify-content:center">${icon('wand')} Clean</button>`;
      el.querySelector('button').addEventListener('click', () => clean(m));
      grid.appendChild(el);
    }
    $('[data-result]').classList.toggle('hidden', !changed);
    updateButtons();
  }

  function updateButtons() {
    const any = analysis.candidates.some((c) => c.selected);
    $('[data-remove]').classList.toggle('hidden', !any && changed);
    $('[data-remove]').disabled = !any;
    $('[data-save]').classList.toggle('hidden', !changed);
  }

  async function removeSelected() {
    const ids = analysis.candidates.filter((c) => c.selected).map((c) => c.id);
    try {
      const resultFile = await busy('Removing watermarks…', () => post('/api/office/remove', { file_id: file.id, ids }));
      file = resultFile;
      analysis = await runJob(() => post('/api/office/analyze', { file_id: file.id }), { title: 'Checking the result…', cancellable: false });
      analysis.candidates.forEach((c) => { c.selected = false; });
      changed = true;
      $('[data-result-text]').textContent = `Removed ${ids.length} watermark item${ids.length > 1 ? 's' : ''}. Save the file to keep it.`;
      render();
    } catch (e) { showError(e); }
  }

  async function clean(media) {
    try {
      const picture = await post('/api/office/media-to-image', { file_id: file.id, name: media.name });
      const cleaned = await cleanPicture(picture);
      if (!cleaned) return;
      const res = await busy('Updating the file…', () => post('/api/office/replace-media', { file_id: file.id, name: media.name, image_file_id: cleaned.id }));
      file = res.file;
      const selected = new Set(analysis.candidates.filter((c) => c.selected).map((c) => c.label));
      analysis = res;
      analysis.candidates.forEach((c) => { c.selected = selected.has(c.label) && c.selected; });
      changed = true;
      $('[data-result-text]').textContent = 'Picture cleaned and replaced everywhere it is used. Save the file to keep it.';
      render();
    } catch (e) { showError(e); }
  }

  $('[data-remove]').addEventListener('click', removeSelected);
  $('[data-save]').addEventListener('click', () => saveFile(file).catch(showError));
  $('[data-back]').addEventListener('click', () => close());
  $('[data-all]').addEventListener('click', () => { analysis.candidates.forEach((c) => { c.selected = true; }); render(); });
  $('[data-none]').addEventListener('click', () => { analysis.candidates.forEach((c) => { c.selected = false; }); render(); });

  function close() {
    ed.classList.add('hidden');
    home.classList.remove('hidden');
    file = null;
  }

  return {
    async open(files) {
      file = files[0];
      changed = false;
      try {
        analysis = await runJob(() => post('/api/office/analyze', { file_id: file.id }), { title: 'Looking for watermarks…', cancellable: false });
        file = analysis.file;
        home.classList.add('hidden');
        ed.classList.remove('hidden');
        $('.title').textContent = file.name;
        render();
        if (!analysis.candidates.length && analysis.media.length) toast('No watermark shapes found. If the watermark is inside a picture, click Clean on it.');
      } catch (e) { showError(e); }
    },
    get editing() { return !ed.classList.contains('hidden'); },
    close,
  };
}

