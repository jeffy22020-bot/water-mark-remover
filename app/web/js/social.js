// Social media downloader (TikTok, Instagram, YouTube, …) with a hand-off to the video remover.
import { desktopBridge, escapeHtml, fmtTime, pickFiles, post, runJob, saveFile, showError, toast, url } from './api.js';
import { hydrateIcons, icon } from './icons.js';

export function initSocial(section, app) {
  section.innerHTML = `
    <div class="social-hero">
      <span class="glow" style="width:120px;height:60px;left:36%;top:-30px;opacity:.45"></span>
      <span class="glow" style="width:46px;height:46px;left:12%;top:170px;opacity:.5"></span>
      <span class="glow" style="width:46px;height:46px;right:13%;top:52px;opacity:.5"></span>
      <span class="glow" style="width:160px;height:240px;right:-90px;top:300px;opacity:.25"></span>
      <h1 class="hero-title">Download TikTok Videos<br><span class="grad-text">without Watermark</span></h1>
      <p class="hero-sub" style="margin-bottom:28px">Save and enjoy your favorite TikTok, Instagram, YouTube and other videos offline, without any watermarks</p>
      <div class="link-row">
        <input type="url" placeholder="Paste the Link here" aria-label="Video link" data-link>
        <button class="btn-paste" data-paste>${icon('clipboard')} Paste</button>
        <button class="btn-download" data-download disabled>Download</button>
      </div>
      <p class="platforms">TikTok · Instagram Reels · YouTube & Shorts · Facebook · X (Twitter) · Snapchat · Pinterest · Reddit · Vimeo · Douyin · and 1,000+ more</p>
      <div data-result></div>
      <p class="social-sub">Have video with watermark?? lets remove then...</p>
      <button class="btn-upload compact" data-upload><span class="plus">+</span>Upload Video</button>
    </div>
    <div class="howto">
      <h2 class="grad-text" style="display:inline-block">How It Works</h2>
      <div class="steps">
        <div class="step"><span class="n">STEP 1</span><h4>Copy the link</h4><p>In TikTok, Instagram or YouTube tap <b>Share → Copy link</b> on the video you want.</p></div>
        <div class="step"><span class="n">STEP 2</span><h4>Paste it here</h4><p>Click <b>Paste</b> (or press Ctrl+V) and then <b>Download</b>.</p></div>
        <div class="step"><span class="n">STEP 3</span><h4>Save – watermark free</h4><p>TikTok videos are saved without the TikTok logo. If a video still has a mark burned in, click <b>Remove watermark</b>.</p></div>
      </div>
      <p class="hint" style="color:var(--muted);font-size:12.5px;margin-top:26px">Only download videos you have the right to use – for example your own posts, or with the creator's permission.</p>
    </div>`;
  hydrateIcons(section);

  const $ = (s) => section.querySelector(s);
  const input = $('[data-link]');
  const dl = $('[data-download]');
  const resultBox = $('[data-result]');

  const sync = () => { dl.disabled = !input.value.trim(); };
  input.addEventListener('input', sync);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && input.value.trim()) download(); });
  $('[data-paste]').addEventListener('click', async () => {
    try {
      const bridge = await desktopBridge();
      const text = bridge?.read_clipboard ? await bridge.read_clipboard() : await navigator.clipboard.readText();
      if (text) { input.value = text.trim(); sync(); }
      else toast('The clipboard is empty – copy a video link first.');
    } catch {
      input.focus();
      toast('Press Ctrl+V to paste the link.');
    }
  });
  dl.addEventListener('click', download);
  $('[data-upload]').addEventListener('click', async () => {
    try {
      const files = await pickFiles('video');
      if (files.length) app.route(files);
    } catch (e) { showError(e); }
  });

  async function download() {
    const link = input.value.trim();
    if (!link) return;
    try {
      const file = await runJob(() => post('/api/social/download', { url: link }), { title: 'Downloading video…' });
      showResult(file);
    } catch (e) { showError(e); }
  }

  function showResult(file) {
    const s = file.social || {};
    const meta = [s.platform, s.uploader, s.duration ? fmtTime(s.duration) : null, file.meta?.width ? `${file.meta.width}×${file.meta.height}` : null].filter(Boolean).join(' · ');
    const badge = s.platform_watermarks
      ? (s.watermark_free ? `<span class="badge-ok">${icon('checkCircle')} Downloaded without the ${escapeHtml(s.platform)} watermark</span>`
        : `<span class="badge-warn">Only a watermarked copy was available – use Remove watermark</span>`)
      : `<span class="badge-ok">${icon('checkCircle')} Original video downloaded</span>`;
    resultBox.innerHTML = `
      <div class="result-card">
        <video src="${url(`/api/files/${file.id}`)}" controls playsinline preload="metadata"></video>
        <div>
          <h3>${escapeHtml(s.title || file.name)}</h3>
          <div class="meta">${escapeHtml(meta)}</div>
          ${badge}
          <div class="actions">
            <button class="btn-primary" data-save>${icon('save')} Save video</button>
            <button class="btn" data-remove>${icon('wand')} Remove watermark</button>
          </div>
          <p class="hint" style="color:var(--muted);font-size:12.5px;margin-top:10px">Still see a logo or username burned into the video? <b>Remove watermark</b> finds and erases it.</p>
        </div>
      </div>`;
    resultBox.querySelector('[data-save]').onclick = () => saveFile(file).catch(showError);
    resultBox.querySelector('[data-remove]').onclick = () => app.route([file], { detect: true });
  }

  return {
    setUrl(value, { start = false } = {}) {
      input.value = value;
      sync();
      if (start) download();
    },
  };
}
