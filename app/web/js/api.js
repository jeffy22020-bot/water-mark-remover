// Talking to the local backend, plus shared UI helpers (toasts, progress, open/save).
export const TOKEN = new URLSearchParams(location.search).get('t') || '';

export const ACCEPT = {
  image: '.jpg,.jpeg,.jfif,.png,.webp,.bmp,.tif,.tiff,.gif,.heic,.heif,.avif',
  video: '.mp4,.mov,.m4v,.webm,.mkv,.avi,.mpg,.mpeg,.flv,.wmv,.3gp,.ts,.mts,.gif',
  pdf: '.pdf',
  office: '.pptx,.pptm,.ppsx,.potx,.docx,.docm,.dotx,.ppt,.pps,.odp,.doc,.odt,.rtf',
};

export function url(path, query = {}) {
  const u = new URL(path, location.origin);
  u.searchParams.set('t', TOKEN);
  for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null) u.searchParams.set(k, v);
  return u.toString();
}

export async function api(path, { method = 'GET', body, form } = {}) {
  const opts = { method, headers: { 'X-Token': TOKEN } };
  if (form) opts.body = form;
  else if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new Error('The app engine stopped responding. Please restart the app.');
  }
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch { /* not json */ }
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  const ct = res.headers.get('content-type') || '';
  return ct.includes('application/json') ? res.json() : res;
}
export const get = (path) => api(path);
export const post = (path, body = {}) => api(path, { method: 'POST', body });

export class Cancelled extends Error {
  constructor() { super('Cancelled'); this.cancelled = true; }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Show the progress dialog and poll a background job until it finishes. */
export async function runJob(start, { title = 'Working…', cancellable = true } = {}) {
  const dlg = progressDialog(title, cancellable);
  try {
    let job = typeof start === 'function' ? await start() : await start;
    dlg.onCancel = () => post(`/api/jobs/${job.id}/cancel`).catch(() => {});
    while (job.status === 'queued' || job.status === 'running') {
      dlg.update(job.progress, job.message);
      await sleep(job.progress > 0 ? 400 : 250);
      job = await get(`/api/jobs/${job.id}`);
    }
    if (job.status === 'cancelled') throw new Cancelled();
    if (job.status === 'error') throw new Error(job.error || 'Something went wrong');
    dlg.update(1, 'Done');
    return job.result;
  } finally {
    dlg.close();
  }
}

function progressDialog(title, cancellable) {
  const el = document.createElement('div');
  el.className = 'overlay';
  el.innerHTML = `
    <div class="progress-card" role="dialog" aria-live="polite">
      <h3>${escapeHtml(title)}</h3>
      <div class="msg"></div>
      <div class="bar indeterminate"><i></i></div>
      <div class="pct"></div>
      ${cancellable ? '<button class="btn">Cancel</button>' : ''}
    </div>`;
  document.body.appendChild(el);
  const bar = el.querySelector('.bar');
  const fill = bar.querySelector('i');
  const state = { onCancel: null };
  el.querySelector('.btn')?.addEventListener('click', (e) => {
    e.target.disabled = true;
    e.target.textContent = 'Cancelling…';
    state.onCancel?.();
  });
  return Object.assign(state, {
    update(progress, message) {
      if (message !== undefined) el.querySelector('.msg').textContent = message || '';
      if (progress > 0.001) {
        bar.classList.remove('indeterminate');
        fill.style.width = `${Math.round(progress * 100)}%`;
        el.querySelector('.pct').textContent = `${Math.round(progress * 100)}%`;
      }
    },
    close() { el.remove(); },
  });
}

export function toast(message, { type = 'info', action, timeout = 5000 } = {}) {
  const box = document.getElementById('toasts');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.innerHTML = `<span style="flex:1"></span>`;
  el.firstChild.textContent = message;
  if (action) {
    const b = document.createElement('button');
    b.className = 'btn';
    b.textContent = action.label;
    b.onclick = () => { action.run(); el.remove(); };
    el.appendChild(b);
  }
  box.appendChild(el);
  setTimeout(() => el.remove(), type === 'error' ? Math.max(timeout, 8000) : timeout);
}

export function showError(err) {
  if (err?.cancelled) { toast('Cancelled'); return; }
  console.error(err);
  toast(err?.message || String(err), { type: 'error' });
}

export function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export function fmtTime(sec) {
  if (!isFinite(sec)) return '0:00';
  sec = Math.max(0, sec);
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  const h = Math.floor(m / 60);
  return h ? `${h}:${String(m % 60).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
}

export function fmtSize(bytes) {
  if (!bytes) return '';
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  while (bytes >= 1024 && i < u.length - 1) { bytes /= 1024; i++; }
  return `${bytes.toFixed(i ? 1 : 0)} ${u[i]}`;
}

export function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('Could not load the picture'));
    img.src = src;
  });
}

// ------------------------------------------------------------ desktop bridge
let bridgeReady = null;
export function desktopBridge() {
  if (window.pywebview?.api) return Promise.resolve(window.pywebview.api);
  if (!bridgeReady) {
    bridgeReady = new Promise((resolve) => {
      window.addEventListener('pywebviewready', () => resolve(window.pywebview.api), { once: true });
      setTimeout(() => resolve(window.pywebview?.api || null), 1500);
    });
  }
  return bridgeReady;
}

/** Let the user choose files; returns opened file records from the backend. */
export async function pickFiles(kind, { multiple = false } = {}) {
  const bridge = await desktopBridge();
  if (bridge?.pick_files) {
    const paths = await bridge.pick_files(kind, multiple);
    if (!paths || !paths.length) return [];
    const out = [];
    for (const p of paths) out.push(await post('/api/open-path', { path: p }));
    return out;
  }
  const files = await new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = kind === 'any' ? Object.values(ACCEPT).join(',') : ACCEPT[kind];
    input.multiple = multiple;
    input.onchange = () => resolve([...input.files]);
    input.click();
  });
  return uploadFiles(files);
}

/** Upload File objects (drag & drop, paste, browser mode). */
export async function uploadFiles(files) {
  if (!files.length) return [];
  const big = files.reduce((n, f) => n + f.size, 0) > 25 * 1024 * 1024;
  const dlg = big ? progressDialog('Opening…', false) : null;
  try {
    const out = [];
    for (const [i, f] of files.entries()) {
      out.push(await uploadOne(f, (p) => dlg?.update((i + p) / files.length, f.name)));
    }
    return out;
  } finally {
    dlg?.close();
  }
}

function uploadOne(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload');
    xhr.setRequestHeader('X-Token', TOKEN);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* ignore */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error(data.detail || `Could not open ${file.name}`));
    };
    xhr.onerror = () => reject(new Error(`Could not open ${file.name}`));
    const form = new FormData();
    form.append('file', file, file.name);
    xhr.send(form);
  });
}

/** Save a produced file: native "Save as" in the desktop app, a download in a browser. */
export async function saveFile(file) {
  const bridge = await desktopBridge();
  if (bridge?.save_file) {
    const path = await bridge.save_file(file.id, file.name);
    if (path) {
      toast(`Saved: ${path}`, { type: 'ok', action: { label: 'Show in folder', run: () => post('/api/reveal', { path }) } });
    }
    return path;
  }
  const a = document.createElement('a');
  a.href = url(`/api/files/${file.id}`, { download: 1 });
  a.download = file.name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  return null;
}

export const state = { status: null };

/** Show an indeterminate progress dialog while ``fn`` runs (for quick, non-job requests). */
export async function busy(title, fn) {
  const dlg = progressDialog(title, false);
  try {
    return await fn();
  } finally {
    dlg.close();
  }
}

export async function saveMany(files) {
  const bridge = await desktopBridge();
  if (bridge?.save_many) {
    const folder = await bridge.save_many(files.map((f) => f.id));
    if (folder) toast(`Saved ${files.length} files to ${folder}`, { type: 'ok', action: { label: 'Open folder', run: () => post('/api/reveal', { path: folder }) } });
    return;
  }
  for (const f of files) {
    await saveFile(f);
    await sleep(350);
  }
}
