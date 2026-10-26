// A pop-up image editor used to clean pictures embedded in PowerPoint/Word files.
import { busy, loadImage, post, runJob, showError, state, toast, url } from './api.js';
import { hydrateIcons, icon } from './icons.js';
import { MaskEditor, bindToolbar, toolbarHtml } from './mask-editor.js';

export function cleanPicture(imageFile) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'overlay';
    overlay.innerHTML = `
      <div class="modal-editor editor" style="position:fixed">
        <div class="editor-top">
          <span class="title">Clean picture</span><span class="sep"></span>
          ${toolbarHtml()}
          <span class="spacer"></span>
          <button class="btn ghost" data-cancel data-icon="x">Cancel</button>
        </div>
        <div class="editor-body">
          <div style="flex:1;position:relative"><div class="mask-host" style="position:absolute;inset:0"></div></div>
          <aside class="side">
            <div class="side-scroll">
              <h3>Clean this picture</h3>
              <p class="hint">Paint over the watermark in the picture (or click <b>Auto-detect</b>), then click <b>Remove</b>. When it looks right, click <b>Use in document</b>.</p>
              <div class="result-banner hidden" data-done>${icon('checkCircle')}<span>Cleaned. Paint again to touch up, or use it.</span></div>
            </div>
            <div class="side-foot">
              <button class="btn" style="justify-content:center" data-remove data-icon="sparkles">Remove</button>
              <button class="btn-primary" data-use disabled>${icon('check')} Use in document</button>
            </div>
          </aside>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    hydrateIcons(overlay);
    const $ = (s) => overlay.querySelector(s);
    const editor = new MaskEditor($('.mask-host'));
    let current = imageFile;
    let changed = false;
    const binding = { active: true };
    bindToolbar(overlay, editor, { onDetect: detect, isActive: () => binding.active });

    async function load() {
      const img = await loadImage(url(`/api/files/${current.id}/preview`, { v: current.id }));
      editor.setBase(img, current.meta.width, current.meta.height);
    }

    async function detect() {
      try {
        const res = await busy('Looking for watermarks…', () => post('/api/image/detect', { file_id: current.id }));
        if (res.found) await editor.addMask(res.mask);
        else toast('No text watermark found – paint over it with the brush.');
      } catch (e) { showError(e); }
    }

    $('[data-remove]').addEventListener('click', async () => {
      if (!editor.hasMask()) { toast('Paint over the watermark first.'); return; }
      try {
        current = await runJob(() => post('/api/image/remove', {
          file_id: current.id, mask: editor.toDataURL(), mode: state.status?.settings?.image_quality || 'ai',
        }), { title: 'Cleaning picture…' });
        changed = true;
        $('[data-done]').classList.remove('hidden');
        $('[data-use]').disabled = false;
        await load();
      } catch (e) { showError(e); }
    });
    const finish = (value) => {
      binding.active = false;
      overlay.remove();
      resolve(value);
    };
    $('[data-use]').addEventListener('click', () => finish(changed ? current : null));
    $('[data-cancel]').addEventListener('click', () => finish(null));
    load().catch((e) => { showError(e); finish(null); });
  });
}
