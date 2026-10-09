// Акцентный цвет: из одного выбранного цвета выводим все оттенки для светлой и тёмной темы
(function () {
  const DEFAULT = '#6a4dff';

  const PRESETS = [
    ['Фиалка', '#6a4dff'],
    ['Сирень', '#a54ce0'],
    ['Малина', '#e0336e'],
    ['Коралл', '#ef5b3a'],
    ['Янтарь', '#e88a00'],
    ['Мята', '#1fa463'],
    ['Бирюза', '#0f9aa8'],
    ['Океан', '#2f6ff0'],
    ['Графит', '#5d5d6b'],
  ];

  const clamp = (v, a, b) => Math.min(Math.max(v, a), b);

  function hexToRgb(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(String(hex).trim());
    if (!m) return null;
    const n = parseInt(m[1], 16);
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
  }

  function rgbToHex({ r, g, b }) {
    return '#' + [r, g, b].map((v) => Math.round(clamp(v, 0, 255)).toString(16).padStart(2, '0')).join('');
  }

  function rgbToHsl({ r, g, b }) {
    r /= 255; g /= 255; b /= 255;
    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    const l = (max + min) / 2;
    let h = 0;
    let s = 0;
    if (max !== min) {
      const d = max - min;
      s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
      if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
      else if (max === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h *= 60;
    }
    return { h, s, l };
  }

  function hslToRgb(h, s, l) {
    h = ((h % 360) + 360) % 360;
    s = clamp(s, 0, 1);
    l = clamp(l, 0, 1);
    const k = (n) => (n + h / 30) % 12;
    const a = s * Math.min(l, 1 - l);
    const f = (n) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
    return { r: f(0) * 255, g: f(8) * 255, b: f(4) * 255 };
  }

  const hslToHex = (h, s, l) => rgbToHex(hslToRgb(h, s, l));
  const hexToHsl = (hex) => rgbToHsl(hexToRgb(hex) || hexToRgb(DEFAULT));

  // относительная яркость по WCAG
  function luminance(hex) {
    const { r, g, b } = hexToRgb(hex);
    const ch = (v) => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b);
  }
  const contrast = (a, b) => {
    const [x, y] = [luminance(a), luminance(b)].sort((m, n) => n - m);
    return (x + 0.05) / (y + 0.05);
  };

  function rgba(hex, alpha) {
    const { r, g, b } = hexToRgb(hex);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  // Набор переменных для одной темы
  function tokens(hex, dark) {
    const { h, s, l: l0 } = hexToHsl(hex);
    const l = clamp(l0, 0.3, 0.72);
    const base = dark ? hslToHex(h, s, clamp(l + 0.07, 0.45, 0.78)) : hslToHex(h, s, l);
    const { l: lb } = hexToHsl(base);
    const hover = hslToHex(h, s, dark ? lb + 0.06 : lb - 0.07);
    const accent2 = hslToHex(h + 26, Math.min(s + 0.05, 1), clamp(lb + 0.06, 0, 0.8));
    // текст на акцентной заливке: белый или почти чёрный — что контрастнее
    const onAccent = contrast(base, '#ffffff') >= contrast(base, '#16161a') ? '#ffffff' : '#16161a';
    // акцент как цвет текста на фоне окна: затемняем/осветляем до читаемости
    const bg = dark ? '#17171b' : '#f2f2f5';
    let tl = lb;
    let text = base;
    for (let i = 0; i < 20 && contrast(text, bg) < 4.2; i++) {
      tl += dark ? 0.03 : -0.03;
      text = hslToHex(h, s, tl);
    }
    return {
      '--accent': base,
      '--accent-hover': hover,
      '--accent-soft': rgba(base, dark ? 0.17 : 0.12),
      '--accent-2': accent2,
      '--accent-text': text,
      '--on-accent': onAccent,
    };
  }

  const block = (t) => Object.entries(t).map(([k, v]) => `${k}: ${v};`).join(' ');

  // Применяет цвет ко всей странице. forceDark — для челки, она всегда тёмная.
  function apply(hex, { forceDark = false } = {}) {
    if (!hexToRgb(hex)) hex = DEFAULT;
    let el = document.getElementById('accent-vars');
    if (!el) {
      el = document.createElement('style');
      el.id = 'accent-vars';
      document.head.append(el);
    }
    el.textContent = forceDark
      ? `:root { ${block(tokens(hex, true))} }`
      : `:root { ${block(tokens(hex, false))} }
         @media (prefers-color-scheme: dark) { :root { ${block(tokens(hex, true))} } }`;
  }

  window.Accent = { DEFAULT, PRESETS, apply, tokens, hexToRgb, rgbToHex, hexToHsl, hslToHex, normalize: (hex) => (hexToRgb(hex) ? rgbToHex(hexToRgb(hex)) : null) };
})();
