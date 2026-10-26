// Before/after slider for images.
export function showCompare(container, beforeSrc, afterSrc, width, height) {
  container.innerHTML = `
    <div class="compare-wrap">
      <div class="compare">
        <img class="after" alt="After" draggable="false">
        <div class="before"><img alt="Before" draggable="false"></div>
        <div class="handle"><span>⇆</span></div>
        <span class="tag l">Before</span><span class="tag r">After</span>
      </div>
    </div>`;
  const wrap = container.querySelector('.compare-wrap');
  const box = container.querySelector('.compare');
  const before = box.querySelector('.before');
  const handle = box.querySelector('.handle');
  box.querySelector('.after').src = afterSrc;
  before.querySelector('img').src = beforeSrc;

  let pos = 0.5;
  const set = (p) => {
    pos = Math.min(1, Math.max(0, p));
    before.style.clipPath = `inset(0 ${100 - pos * 100}% 0 0)`;
    handle.style.left = `${pos * 100}%`;
  };
  const layout = () => {
    const r = wrap.getBoundingClientRect();
    const s = Math.min((r.width - 32) / width, (r.height - 32) / height, 2);
    box.style.width = `${Math.max(1, width * s)}px`;
    box.style.height = `${Math.max(1, height * s)}px`;
  };
  const move = (e) => {
    const r = box.getBoundingClientRect();
    set((e.clientX - r.left) / r.width);
  };
  box.addEventListener('pointerdown', (e) => {
    box.setPointerCapture(e.pointerId);
    move(e);
    box.onpointermove = move;
  });
  box.addEventListener('pointerup', () => { box.onpointermove = null; });
  const ro = new ResizeObserver(layout);
  ro.observe(wrap);
  layout();
  set(0.5);
  return { destroy: () => ro.disconnect() };
}
