// Landing screen of each tool: big title, upload drop zone, samples.
import { get, post, url, pickFiles, showError, escapeHtml } from './api.js';
import { icon } from './icons.js';

export function renderHome(container, cfg, { onOpen, onUrl }) {
  container.innerHTML = `
    <div class="home">
      <span class="glow" style="width:180px;height:120px;left:-60px;top:40px"></span>
      <span class="glow" style="width:160px;height:110px;right:-70px;top:260px;opacity:.22"></span>
      <h1 class="hero-title">${cfg.title}</h1>
      <p class="hero-sub">${cfg.sub}</p>
      <div class="dropzone" data-kind="${cfg.kind}">
        <button class="btn-upload" data-upload><span class="plus">+</span>${escapeHtml(cfg.button)}</button>
        <p class="line">${cfg.line}</p>
        <div class="url-row hidden" style="max-width:560px;margin:10px auto 6px">
          <div class="link-row" style="padding:5px">
            <input type="url" placeholder="Paste a link to an image, video or PDF" aria-label="Link">
            <button class="btn-primary" style="padding:8px 18px" data-open-url>Open</button>
          </div>
        </div>
        ${cfg.limits ? `<p class="limits">${cfg.limits}</p>` : ''}
        ${cfg.formats ? `<p class="formats">${cfg.formats}</p>` : ''}
        <p class="legal">By opening a file you confirm you own it or have <b>permission</b> to edit it. Everything is processed <b>on this PC</b>.</p>
      </div>
      <div class="samples hidden">
        <h3>${escapeHtml(cfg.samplesTitle || 'No file? Try one of these')}</h3>
        <div class="sample-row"></div>
      </div>
      ${cfg.features ? `<div class="features">${cfg.features.map((f) => `
        <div class="feature"><span class="ico">${icon(f.icon)}</span><h4>${f.title}</h4><p>${f.text}</p></div>`).join('')}</div>` : ''}
    </div>`;

  container.querySelector('[data-upload]').addEventListener('click', async () => {
    try {
      const files = await pickFiles(cfg.kind, { multiple: !!cfg.multiple });
      if (files.length) onOpen(files);
    } catch (e) { showError(e); }
  });

  const urlRow = container.querySelector('.url-row');
  const urlInput = urlRow.querySelector('input');
  container.querySelector('[data-url]')?.addEventListener('click', (e) => {
    e.preventDefault();
    urlRow.classList.toggle('hidden');
    urlInput.focus();
  });
  const submitUrl = () => {
    const value = urlInput.value.trim();
    if (value) onUrl(value);
  };
  urlRow.querySelector('[data-open-url]').addEventListener('click', submitUrl);
  urlInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') submitUrl(); });

  loadSamples(container, cfg, onOpen);
}

async function loadSamples(container, cfg, onOpen) {
  let samples = [];
  try { samples = await get('/api/samples'); } catch { return; }
  const mine = samples.filter((s) => s.kind === cfg.kind);
  if (!mine.length) return;
  const box = container.querySelector('.samples');
  const row = box.querySelector('.sample-row');
  for (const s of mine) {
    const b = document.createElement('button');
    b.className = 'sample';
    b.title = s.name;
    if (s.kind === 'office') {
      const ext = s.name.split('.').pop().toUpperCase();
      b.classList.add('doc');
      b.style.background = ext.startsWith('PP') ? 'linear-gradient(135deg,#f97316,#c2410c)' : 'linear-gradient(135deg,#3b82f6,#1d4ed8)';
      b.textContent = ext;
    } else {
      b.style.backgroundImage = `url("${url(`/api/samples/${encodeURIComponent(s.name)}/thumb`)}")`;
    }
    b.addEventListener('click', async () => {
      try { onOpen([await post(`/api/samples/${encodeURIComponent(s.name)}/open`)]); } catch (e) { showError(e); }
    });
    row.appendChild(b);
  }
  box.classList.remove('hidden');
}
