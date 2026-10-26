// App shell: tabs, routing opened files to the right tool, drag & drop, paste, settings.
import { desktopBridge, get, pickFiles, post, runJob, showError, state, toast, uploadFiles, api } from './api.js';
import { hydrateIcons } from './icons.js';
import { initImage } from './image.js';
import { initOffice } from './office.js';
import { initPdf } from './pdf.js';
import { initSocial } from './social.js';
import { initVideo } from './video.js';

const views = {};
let tools = {};
let currentTab = 'image';

function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll('.tab-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  Object.entries(views).forEach(([k, v]) => v.classList.toggle('active', k === tab));
}

const app = {
  switchTab,
  /** Open already-registered files in the matching tool. */
  async route(files, opts = {}) {
    if (!files.length) return;
    const kind = files[0].kind;
    const same = files.filter((f) => f.kind === kind);
    if (same.length < files.length) toast('Opened files of one type only – open the others separately.');
    switchTab(kind);
    try {
      if (kind === 'image') await tools.image.open(same);
      else if (kind === 'video') await tools.video.open(same.slice(0, 1), opts);
      else if (kind === 'pdf') await tools.pdf.open(same.slice(0, 1));
      else if (kind === 'office') await tools.office.open(same.slice(0, 1));
      if (kind !== 'image' && same.length > 1) toast('Only the first file was opened.');
    } catch (e) { showError(e); }
  },
  async openUrl(link) {
    try {
      const res = await post('/api/fetch-url', { url: link });
      if (res.social) {
        switchTab('social');
        tools.social.setUrl(res.url, { start: true });
        return;
      }
      await app.route([res]);
    } catch (e) { showError(e); }
  },
};

async function openDropped(fileList) {
  try {
    const files = await uploadFiles([...fileList]);
    await app.route(files);
  } catch (e) { showError(e); }
}

function setupDragDrop() {
  const veil = document.getElementById('dropVeil');
  let depth = 0;
  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
  window.addEventListener('dragenter', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth++;
    veil.classList.remove('hidden');
  });
  window.addEventListener('dragleave', () => {
    depth = Math.max(0, depth - 1);
    if (!depth) veil.classList.add('hidden');
  });
  window.addEventListener('dragover', (e) => e.preventDefault());
  window.addEventListener('drop', (e) => {
    e.preventDefault();
    depth = 0;
    veil.classList.add('hidden');
    if (e.dataTransfer.files.length) openDropped(e.dataTransfer.files);
    else {
      const text = e.dataTransfer.getData('text/uri-list') || e.dataTransfer.getData('text/plain');
      if (text) app.openUrl(text.trim());
    }
  });
}

function setupPaste() {
  document.addEventListener('paste', (e) => {
    const t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')) return;
    const files = [...(e.clipboardData?.files || [])];
    if (files.length) {
      e.preventDefault();
      // Pasted screenshots arrive as "image.png" – give them a friendlier name.
      const named = files.map((f) => (f.name === 'image.png' ? new File([f], `pasted-${Date.now()}.png`, { type: f.type }) : f));
      openDropped(named);
      return;
    }
    const text = e.clipboardData?.getData('text') || '';
    if (/https?:\/\//i.test(text)) {
      e.preventDefault();
      if (currentTab === 'social') tools.social.setUrl(text.trim());
      else app.openUrl(text.trim());
    }
  });
  window.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'o') {
      e.preventDefault();
      const kind = currentTab === 'social' ? 'video' : currentTab;
      pickFiles(kind, { multiple: kind === 'image' }).then((f) => app.route(f)).catch(showError);
    }
  });
}

// ------------------------------------------------------------ settings
async function refreshStatus() {
  state.status = await get('/api/status');
  const chip = document.getElementById('deviceChip');
  chip.innerHTML = `AI: <b>${state.status.ai_available ? state.status.device : 'Fast mode only'}</b>`;
  return state.status;
}

function setupSettings() {
  const dlg = document.getElementById('settingsDlg');
  const fields = {
    device: document.getElementById('setDevice'),
    image_quality: document.getElementById('setImageQuality'),
    video_quality: document.getElementById('setVideoQuality'),
    video_crf: document.getElementById('setCrf'),
    output_dir: document.getElementById('setOutput'),
    cookies_browser: document.getElementById('setCookies'),
  };
  const fill = () => {
    const s = state.status;
    for (const [k, el] of Object.entries(fields)) el.value = s.settings[k] ?? '';
    document.getElementById('deviceDesc').textContent = s.gpu
      ? `A supported graphics card was found. AI currently runs on: ${s.device}.`
      : 'No supported graphics card was found, so the AI runs on the processor (slower for videos).';
    document.getElementById('ytdlpVersion').textContent = `Version ${s.ytdlp}`;
    document.getElementById('aboutKv').innerHTML = `
      <span>App version</span><b>${s.version}</b>
      <span>AI model</span><b>${s.ai_available ? 'LaMa inpainting (installed)' : 'missing – Fast mode only'}</b>
      <span>AI runs on</span><b>${s.device}</b>
      <span>LibreOffice</span><b>${s.libreoffice ? 'found – old .ppt/.doc files supported' : 'not installed (only needed for .ppt/.doc)'}</b>`;
  };
  document.getElementById('settingsBtn').addEventListener('click', async () => {
    await refreshStatus().catch(() => {});
    fill();
    dlg.showModal();
  });
  dlg.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => dlg.close()));
  for (const [k, el] of Object.entries(fields)) {
    el.addEventListener('change', async () => {
      const value = k === 'video_crf' ? Number(el.value) : el.value;
      try {
        await api('/api/settings', { method: 'PUT', body: { [k]: value } });
        await refreshStatus();
        fill();
      } catch (e) { showError(e); }
    });
  }
  document.getElementById('pickOutput').addEventListener('click', async () => {
    const bridge = await desktopBridge();
    if (!bridge?.pick_folder) { fields.output_dir.focus(); return; }
    const folder = await bridge.pick_folder();
    if (folder) {
      fields.output_dir.value = folder;
      fields.output_dir.dispatchEvent(new Event('change'));
    }
  });
  document.getElementById('updateYtdlp').addEventListener('click', async () => {
    try {
      const res = await runJob(() => post('/api/social/update'), { title: 'Updating the video downloader…' });
      toast(`Downloader updated to ${res.version}. Restart the app to use it.`, { type: 'ok', timeout: 9000 });
    } catch (e) { showError(e); }
  });
}

async function main() {
  hydrateIcons(document);
  for (const v of document.querySelectorAll('.view')) views[v.dataset.kind] = v;
  try {
    await refreshStatus();
  } catch (e) {
    showError(e);
  }
  tools = {
    image: initImage(views.image, app),
    video: initVideo(views.video, app),
    pdf: initPdf(views.pdf, app),
    office: initOffice(views.office, app),
    social: initSocial(views.social, app),
  };
  document.querySelectorAll('.tab-btn').forEach((b) => b.addEventListener('click', () => switchTab(b.dataset.tab)));
  setupDragDrop();
  setupPaste();
  setupSettings();
  if (state.status && !state.status.ai_available) {
    toast('The AI model was not found, so only Fast mode is available. Reinstalling the app fixes this.', { type: 'error' });
  }
  window.__app = { app, tools, switchTab };
}

main();
