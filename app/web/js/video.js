// Video watermark remover: mark watermark areas (with time ranges), auto-detect, process, compare, save.
import { escapeHtml, fmtTime, post, runJob, saveFile, showError, state, toast, url } from './api.js';
import { renderHome } from './home.js';
import { hydrateIcons, icon } from './icons.js';
import { MaskEditor, bindToolbar, toolbarHtml } from './mask-editor.js';

export function initVideo(section, app) {
  const home = document.createElement('div');
  const ed = document.createElement('div');
  ed.className = 'editor hidden';
  section.append(home, ed);

  renderHome(home, {
    kind: 'video',
    title: 'AI Watermark Eraser &amp; Logo<br>Remover from <span class="grad-text">Video</span>',
    sub: 'Remove watermarks, logos, usernames and captions from your videos – including TikTok-style watermarks that jump around.',
    button: 'Upload Video',
    line: 'Drag and drop your video here, or paste a <a href="#" data-url>link</a>',
    limits: 'No length or size limits – long videos simply take longer.',
    formats: 'Supported formats: <code>mp4 | mov | webm | mkv | avi | wmv | flv | mpg | m4v | 3gp | gif</code>',
    features: [
      { icon: 'wand', title: 'Finds the watermark for you', text: 'Detects logos and text that stay in place while the video moves – and follows marks that change corner.' },
      { icon: 'cpu', title: 'Uses your graphics card', text: 'AI mode runs on any DirectX 12 graphics card. Fast mode works on every PC.' },
      { icon: 'video', title: 'Keeps sound & quality', text: 'The original audio is kept and the video is saved in high-quality MP4.' },
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
      <div style="flex:1;position:relative;min-width:0;display:flex;flex-direction:column">
        <div style="flex:1;position:relative">
          <div class="mask-host" style="position:absolute;inset:0"></div>
          <div class="result-host hidden" style="position:absolute;inset:0;display:grid;place-items:center;background:#0c0c0e;padding:16px">
            <video controls playsinline style="max-width:100%;max-height:100%;box-shadow:var(--shadow);background:#000"></video>
          </div>
          <div class="stage-hint" data-hint>Pause where the watermark is visible and paint over it – or click <b>Auto-detect</b></div>
        </div>
        <div class="timeline" data-timeline>
          <button class="btn small" data-play data-icon="play"></button>
          <div class="track"><input type="range" min="0" max="1" step="0.01" value="0" aria-label="Position"><div class="spans"></div></div>
          <span class="time">0:00 / 0:00</span>
        </div>
      </div>
      <aside class="side">
        <div class="side-scroll">
          <div data-edit-panel>
            <h3>Watermark areas</h3>
            <p class="hint">Each area is removed during its time range. A watermark that moves to another spot (like TikTok's) needs one area per position – <b>Auto-detect</b> does this for you.</p>
            <div class="regions"></div>
            <button class="btn small" style="margin-top:10px" data-add-region data-icon="plus">Add area from here</button>
            <div style="margin-top:22px">
              <div class="field-label">Quality</div>
              <div class="segmented" data-quality>
                <button data-v="auto">Auto</button><button data-v="ai">AI (best)</button><button data-v="fast">Fast</button>
              </div>
              <p class="hint" data-quality-hint style="margin-top:8px"></p>
            </div>
          </div>
          <div data-result-panel class="hidden">
            <div class="result-banner">${icon('checkCircle')}<span>Watermark removed.</span></div>
            <div class="segmented" style="margin-top:14px" data-ab><button data-v="after" class="active">Result</button><button data-v="before">Original</button></div>
            <p class="hint" style="margin-top:12px">Still see a trace? Use <b>Touch up</b> to mark it on the result and run again.</p>
          </div>
        </div>
        <div class="side-foot">
          <button class="btn-primary" data-remove>${icon('sparkles')} Remove watermark</button>
          <div data-result-actions class="hidden" style="display:flex;flex-direction:column;gap:8px">
            <button class="btn-primary" data-save>${icon('save')} Save video as…</button>
            <div style="display:flex;gap:8px">
              <button class="btn" style="flex:1;justify-content:center" data-touchup data-icon="brush">Touch up</button>
              <button class="btn" style="flex:1;justify-content:center" data-reedit data-icon="undo">Edit again</button>
            </div>
          </div>
        </div>
      </aside>
    </div>`;
  hydrateIcons(ed);

  const $ = (s) => ed.querySelector(s);
  const editor = new MaskEditor($('.mask-host'), { onChange: () => onMaskChange() });
  const videoEl = document.createElement('video');
  videoEl.muted = true;
  videoEl.playsInline = true;
  videoEl.preload = 'auto';
  const resultVideo = $('.result-host video');
  const range = $('.track input');
  let file = null;
  let playableId = null;
  let result = null;
  let regions = [];
  let activeRegion = 0;
  let mode = 'edit';
  let quality = state.status?.settings?.video_quality || 'auto';
  let nextId = 1;

  bindToolbar(ed, editor, {
    onDetect: () => autoDetect(),
    isActive: () => section.classList.contains('active') && !ed.classList.contains('hidden') && mode === 'edit',
  });

  // ------------------------------------------------------------ quality
  const qButtons = ed.querySelectorAll('[data-quality] button');
  const setQuality = (q) => {
    quality = q;
    qButtons.forEach((b) => b.classList.toggle('active', b.dataset.v === q));
    const gpu = state.status?.gpu;
    const hints = {
      auto: gpu ? 'Uses AI on your graphics card.' : 'No supported graphics card found, so Fast mode is used. Choose AI for the best result (slower).',
      ai: gpu ? 'Best result. Runs on your graphics card.' : 'Best result, but slow on the processor – roughly 1–3 seconds per frame.',
      fast: 'Quick. Good for small logos; may look smudgy on detailed backgrounds.',
    };
    $('[data-quality-hint]').textContent = hints[q];
  };
  qButtons.forEach((b) => b.addEventListener('click', () => setQuality(b.dataset.v)));
  setQuality(quality);

  // ------------------------------------------------------------ playback
  const duration = () => file?.meta?.duration || videoEl.duration || 0;
  const updateTime = () => {
    const d = duration();
    range.max = d || 1;
    range.value = videoEl.currentTime;
    $('.time').textContent = `${fmtTime(videoEl.currentTime)} / ${fmtTime(d)}`;
    renderRegionHighlights();
  };
  videoEl.addEventListener('timeupdate', updateTime);
  videoEl.addEventListener('loadedmetadata', updateTime);
  videoEl.addEventListener('play', () => { $('[data-play]').innerHTML = icon('pause'); });
  videoEl.addEventListener('pause', () => { $('[data-play]').innerHTML = icon('play'); });
  range.addEventListener('input', () => { videoEl.currentTime = +range.value; updateTime(); });
  $('[data-play]').addEventListener('click', () => (videoEl.paused ? videoEl.play() : videoEl.pause()));

  // ------------------------------------------------------------ regions
  function newRegion(start = 0, end = duration()) {
    return { id: nextId++, start, end, mask: null };
  }

  function saveActiveMask() {
    const r = regions[activeRegion];
    if (r && editor.w) r.mask = editor.hasMask() ? editor.exportState() : null;
  }

  function selectRegion(i) {
    saveActiveMask();
    activeRegion = i;
    editor.importState(regions[i]?.mask || null);
    renderRegions();
  }

  function onMaskChange() {
    $('[data-hint]').classList.toggle('hidden', mode !== 'edit' || editor.hasMask() || regions.some((r, i) => i !== activeRegion && r.mask));
  }

  function renderRegions() {
    const list = $('.regions');
    list.innerHTML = '';
    regions.forEach((r, i) => {
      const el = document.createElement('div');
      el.className = `region${i === activeRegion ? ' active' : ''}`;
      el.innerHTML = `
        <div class="region-head"><span><span class="dot"></span>Area ${i + 1}</span>
          ${regions.length > 1 ? `<button class="btn ghost small" data-del title="Delete area">${icon('trash')}</button>` : ''}</div>
        <div class="region-times">
          <span>${fmtTime(r.start)} – ${fmtTime(Math.min(r.end, duration()))}</span>
          <button class="btn" data-start>Start here</button><button class="btn" data-end>End here</button>
        </div>`;
      el.addEventListener('click', (e) => {
        if (e.target.closest('button')) return;
        if (i !== activeRegion) selectRegion(i);
      });
      el.querySelector('[data-start]').onclick = () => { r.start = Math.min(videoEl.currentTime, r.end - 0.05); if (i !== activeRegion) selectRegion(i); renderRegions(); };
      el.querySelector('[data-end]').onclick = () => { r.end = Math.max(videoEl.currentTime, r.start + 0.05); if (i !== activeRegion) selectRegion(i); renderRegions(); };
      el.querySelector('[data-del]')?.addEventListener('click', () => {
        saveActiveMask();
        regions.splice(i, 1);
        activeRegion = Math.min(activeRegion, regions.length - 1);
        editor.importState(regions[activeRegion]?.mask || null);
        renderRegions();
      });
      list.appendChild(el);
    });
    renderRegionHighlights();
  }

  function renderRegionHighlights() {
    const spans = $('.track .spans');
    const d = duration() || 1;
    spans.innerHTML = regions.map((r, i) => `<i class="${i === activeRegion ? 'active' : ''}" style="left:${(r.start / d) * 100}%;width:${Math.max(0.5, ((Math.min(r.end, d) - r.start) / d) * 100)}%"></i>`).join('');
  }

  async function maskToImageData(dataUrl) {
    const img = await new Promise((resolve, reject) => {
      const im = new Image();
      im.onload = () => resolve(im);
      im.onerror = reject;
      im.src = dataUrl;
    });
    const c = document.createElement('canvas');
    c.width = editor.mask.width;
    c.height = editor.mask.height;
    const ctx = c.getContext('2d');
    ctx.drawImage(img, 0, 0, c.width, c.height);
    return ctx.getImageData(0, 0, c.width, c.height);
  }

  function imageDataToUrl(data) {
    const c = document.createElement('canvas');
    c.width = data.width;
    c.height = data.height;
    c.getContext('2d').putImageData(data, 0, 0);
    return c.toDataURL('image/png');
  }

  // ------------------------------------------------------------ actions
  async function autoDetect() {
    try {
      const res = await runJob(() => post('/api/video/detect', { file_id: file.id }), { title: 'Looking for watermarks…' });
      if (!res.regions.length) {
        toast('No fixed watermark found. Pause on a frame where it is visible and paint over it.');
        return;
      }
      saveActiveMask();
      const keep = regions.filter((r) => r.mask);
      const found = [];
      for (const r of res.regions) {
        const region = newRegion(r.start, r.end);
        region.mask = await maskToImageData(r.mask);
        found.push(region);
      }
      regions = [...keep, ...found];
      activeRegion = keep.length;
      editor.importState(regions[activeRegion].mask);
      renderRegions();
      videoEl.currentTime = Math.min(regions[activeRegion].start + 0.2, duration());
      toast(found.length > 1
        ? `Found the watermark in ${found.length} positions. Check each area, then click Remove watermark.`
        : 'Watermark marked in red. Check it, then click Remove watermark.');
    } catch (e) { showError(e); }
  }

  async function remove() {
    saveActiveMask();
    const payload = regions.filter((r) => r.mask).map((r) => ({ start: r.start, end: r.end, mask: imageDataToUrl(r.mask) }));
    if (!payload.length) {
      toast('Paint over the watermark first (or click Auto-detect).');
      return;
    }
    videoEl.pause();
    try {
      result = await runJob(
        () => post('/api/video/remove', { file_id: file.id, regions: payload, mode: quality }),
        { title: 'Removing watermark from video…' },
      );
      showResult();
    } catch (e) { showError(e); }
  }

  function setMode(m) {
    mode = m;
    const res = m === 'result';
    $('.mask-host').classList.toggle('hidden', res);
    $('.result-host').classList.toggle('hidden', !res);
    $('[data-timeline]').classList.toggle('hidden', res);
    $('.edit-tools').style.visibility = res ? 'hidden' : '';
    $('[data-toggle-mask]').classList.toggle('hidden', res);
    $('[data-edit-panel]').classList.toggle('hidden', res);
    $('[data-result-panel]').classList.toggle('hidden', !res);
    $('[data-remove]').classList.toggle('hidden', res);
    $('[data-result-actions]').classList.toggle('hidden', !res);
    onMaskChange();
  }

  function showResult() {
    setMode('result');
    resultVideo.src = url(`/api/files/${result.id}`);
    ed.querySelectorAll('[data-ab] button').forEach((b) => b.classList.toggle('active', b.dataset.v === 'after'));
  }

  ed.querySelectorAll('[data-ab] button').forEach((b) => b.addEventListener('click', () => {
    const t = resultVideo.currentTime;
    const playing = !resultVideo.paused;
    resultVideo.src = url(`/api/files/${b.dataset.v === 'after' ? result.id : playableId}`);
    resultVideo.currentTime = t;
    if (playing) resultVideo.play();
    ed.querySelectorAll('[data-ab] button').forEach((x) => x.classList.toggle('active', x === b));
  }));

  $('[data-add-region]').addEventListener('click', () => {
    saveActiveMask();
    regions.push(newRegion(videoEl.currentTime, duration()));
    activeRegion = regions.length - 1;
    editor.importState(null);
    renderRegions();
  });
  $('[data-remove]').addEventListener('click', remove);
  $('[data-save]').addEventListener('click', () => saveFile(result).catch(showError));
  $('[data-reedit]').addEventListener('click', () => { resultVideo.pause(); setMode('edit'); editor.fit(); });
  $('[data-touchup]').addEventListener('click', () => { resultVideo.pause(); open([result]).catch(showError); });
  $('[data-back]').addEventListener('click', () => close());
  const toggleMask = $('[data-toggle-mask]');
  let maskShown = true;
  toggleMask.addEventListener('click', () => {
    maskShown = !maskShown;
    editor.setMaskVisible(maskShown);
    toggleMask.innerHTML = `${icon(maskShown ? 'eye' : 'eyeOff')} ${maskShown ? 'Hide selection' : 'Show selection'}`;
  });

  function close() {
    videoEl.pause();
    resultVideo.pause();
    videoEl.removeAttribute('src');
    resultVideo.removeAttribute('src');
    ed.classList.add('hidden');
    home.classList.remove('hidden');
    file = null;
  }

  async function open(files, { detect = false } = {}) {
    file = files[0];
    result = null;
    home.classList.add('hidden');
    ed.classList.remove('hidden');
    $('.title').textContent = file.name;
    setMode('edit');
    const playable = await post('/api/video/playable', { file_id: file.id });
    playableId = playable.file_id || (await runJob(() => playable.job, { title: 'Preparing preview…' })).file_id;
    videoEl.src = url(`/api/files/${playableId}`);
    await new Promise((resolve) => {
      videoEl.onloadeddata = resolve;
      videoEl.onerror = resolve;
    });
    const w = file.meta.width || videoEl.videoWidth;
    const h = file.meta.height || videoEl.videoHeight;
    editor.setBase(videoEl, w, h);
    regions = [newRegion(0, duration())];
    activeRegion = 0;
    renderRegions();
    updateTime();
    if (detect) await autoDetect();
  }

  return {
    open,
    get editing() { return !ed.classList.contains('hidden'); },
    close,
  };
}

