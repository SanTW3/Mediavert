// «Перетекание» челки в главное окно: капля стекает с верхнего края,
// отрывается и разрастается в форму окна. Координаты — относительно экрана.
const blob = document.getElementById('blob');
const ISLAND = '#0c0c11';
const DURATION = 560;

const frame = (r, radius, color, shadow) => ({
  left: `${r.x}px`,
  top: `${r.y}px`,
  width: `${r.w}px`,
  height: `${r.h}px`,
  borderRadius: radius,
  backgroundColor: color,
  boxShadow: shadow,
});

api.on('morph:play', ({ from, to, toColor }) => {
  blob.getAnimations().forEach((a) => a.cancel());
  blob.style.opacity = '1';

  // капля: уже островка, вытянута вниз, но ещё держится за верхний край
  const dripW = Math.min(340, from.w);
  const drip = { x: from.x + (from.w - dripW) / 2, y: from.y, w: dripW, h: from.h + 150 };
  // момент отрыва: капля округляется и начинает двигаться к окну
  const midW = (dripW + to.w) / 2.6;
  const midH = (drip.h + to.h) / 2.4;
  const mid = {
    x: (drip.x + drip.w / 2 + to.x + to.w / 2) / 2 - midW / 2,
    y: Math.max(to.y * 0.55, 24),
    w: midW,
    h: midH,
  };

  const anim = blob.animate(
    [
      { ...frame(from, '0 0 28px 28px', ISLAND, '0 10px 16px rgba(0,0,0,.40)'), offset: 0, easing: 'cubic-bezier(.55, 0, .8, .45)' },
      { ...frame(drip, `0 0 ${dripW / 2}px ${dripW / 2}px`, ISLAND, '0 18px 34px rgba(0,0,0,.42)'), offset: 0.3, easing: 'cubic-bezier(.35, 0, .3, 1)' },
      { ...frame(mid, `${Math.min(midW, midH) / 2.2}px`, ISLAND, '0 24px 60px rgba(0,0,0,.40)'), offset: 0.58, easing: 'cubic-bezier(.25, .8, .35, 1)' },
      { ...frame(to, '10px', toColor, '0 30px 80px rgba(0,0,0,.30)'), offset: 1 },
    ],
    { duration: DURATION, fill: 'forwards' },
  );
  // на ~76% капля почти совпадает с окном — пора показывать настоящее окно под ней
  setTimeout(() => api.morphAlmost(), DURATION * 0.76);
  // капля встала на место — растворяется сама, открывая окно (хвост анимации идёт уже под растворением)
  setTimeout(() => {
    blob.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 140, easing: 'ease-out', fill: 'forwards', composite: 'replace' }).onfinish = () => {
      anim.cancel();
      api.morphLanded();
    };
  }, DURATION * 0.88);
});
