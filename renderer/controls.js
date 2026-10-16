// «Жидкое стекло»: переключатели и вкладки можно тянуть мышкой.
// Пока держишь — ползунок становится стеклянной линзой; при отпускании пружинит на место.
(function () {
  const DRAG_START = 4; // px — меньше считается обычным кликом

  /* ---------- переключатели (input.switch) ---------- */

  const TRAVEL = 16; // ход кружка по дорожке, px

  function enhanceSwitch(input) {
    let s = null;
    let suppressClick = false;

    const cleanup = () => {
      input.classList.remove('pressing', 'dragging');
      input.style.removeProperty('--pos');
      s = null;
    };

    input.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 || input.disabled) return;
      s = { x: e.clientX, from: input.checked ? 1 : 0, moved: false };
      input.setPointerCapture(e.pointerId);
      input.classList.add('pressing');
    });

    input.addEventListener('pointermove', (e) => {
      if (!s) return;
      const dx = e.clientX - s.x;
      if (!s.moved && Math.abs(dx) < DRAG_START) return;
      s.moved = true;
      input.classList.add('dragging');
      input.style.setProperty('--pos', Math.min(1, Math.max(0, s.from + dx / TRAVEL)).toFixed(3));
    });

    input.addEventListener('pointerup', () => {
      if (!s) return;
      if (s.moved) {
        const next = Number(input.style.getPropertyValue('--pos')) > 0.5;
        suppressClick = true; // клик после перетаскивания не должен ещё раз переключать
        if (next !== input.checked) {
          input.checked = next;
          input.dispatchEvent(new Event('input', { bubbles: true }));
          input.dispatchEvent(new Event('change', { bubbles: true }));
        }
      }
      cleanup();
    });
    input.addEventListener('pointercancel', cleanup);

    input.addEventListener('click', (e) => {
      if (!suppressClick) return;
      suppressClick = false;
      e.preventDefault();
    });
  }

  /* ---------- вкладки (.segmented) ---------- */

  function enhanceSegmented(seg) {
    const thumb = seg.querySelector('.seg-thumb');
    if (!thumb) return;
    let s = null;

    const buttons = () => [...seg.querySelectorAll('button')];
    const nearest = (center) => {
      let best = null;
      let dist = Infinity;
      for (const b of buttons()) {
        const d = Math.abs(b.offsetLeft + b.offsetWidth / 2 - center);
        if (d < dist) [best, dist] = [b, d];
      }
      return best;
    };

    const finish = () => {
      seg.classList.remove('lifted', 'dragging');
      buttons().forEach((b) => b.classList.remove('under'));
      s = null;
    };

    seg.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      const active = seg.querySelector('button.active');
      if (!active) return;
      // тянуть можно только выбранную вкладку — как в iOS
      const r = active.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right) return;
      s = { x: e.clientX, base: active.offsetLeft - 3, w: active.offsetWidth, dragging: false, lastX: e.clientX, lastT: performance.now(), stretch: 1 };
      seg.classList.add('lifted');
    });

    seg.addEventListener('pointermove', (e) => {
      if (!s) return;
      const dx = e.clientX - s.x;
      if (!s.dragging) {
        if (Math.abs(dx) < DRAG_START) return;
        s.dragging = true;
        seg.setPointerCapture(e.pointerId);
        seg.classList.add('dragging');
      }
      const max = seg.clientWidth - 6 - s.w;
      const x = Math.min(max, Math.max(0, s.base + dx));
      // «капля»: растягивается от скорости и чуть сплющивается по высоте
      const now = performance.now();
      const v = (e.clientX - s.lastX) / Math.max(now - s.lastT, 1);
      s.lastX = e.clientX;
      s.lastT = now;
      s.stretch += (1 + Math.min(Math.abs(v) * 0.18, 0.28) - s.stretch) * 0.35;
      thumb.style.transform = `translateX(${x}px) scale(${(1.06 * s.stretch).toFixed(3)}, ${(1.1 / Math.sqrt(s.stretch)).toFixed(3)})`;
      s.x2 = x;
      const under = nearest(x + 3 + s.w / 2);
      buttons().forEach((b) => b.classList.toggle('under', b === under));
    });

    const release = () => {
      if (!s) return;
      if (s.dragging) {
        const target = nearest((s.x2 ?? s.base) + 3 + s.w / 2);
        finish();
        if (target && !target.classList.contains('active')) target.click();
        else if (typeof moveThumb === 'function') moveThumb(seg);
      } else {
        finish();
      }
    };
    seg.addEventListener('pointerup', release);
    seg.addEventListener('pointercancel', release);
  }

  document.querySelectorAll('input.switch').forEach(enhanceSwitch);
  document.querySelectorAll('.segmented').forEach(enhanceSegmented);
})();
