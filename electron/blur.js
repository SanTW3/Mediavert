// Размытие фона под прозрачным окном (челкой) на Windows.
// Стандартный backgroundMaterial Electron к прозрачным окнам не применяется, поэтому
// включаем «акцент» окна напрямую: SetWindowCompositionAttribute(WCA_ACCENT_POLICY).
let api = null;

function load() {
  if (api !== null) return api;
  api = false;
  if (process.platform !== 'win32') return api;
  try {
    const koffi = require('koffi');
    const user32 = koffi.load('user32.dll');
    const dwmapi = koffi.load('dwmapi.dll');
    const ACCENT = koffi.struct('ACCENT_POLICY', { AccentState: 'int32', AccentFlags: 'int32', GradientColor: 'uint32', AnimationId: 'int32' });
    const DATA = koffi.struct('WINCOMPATTRDATA', { Attrib: 'int32', pvData: 'void*', cbData: 'size_t' });
    api = {
      koffi,
      ACCENT,
      DATA,
      SetWindowCompositionAttribute: user32.func('bool __stdcall SetWindowCompositionAttribute(intptr_t hWnd, WINCOMPATTRDATA* data)'),
      SetWindowPos: user32.func('bool __stdcall SetWindowPos(intptr_t hWnd, intptr_t after, int x, int y, int cx, int cy, uint32 flags)'),
      DwmSetWindowAttribute: dwmapi.func('int32 __stdcall DwmSetWindowAttribute(intptr_t hWnd, uint32 attr, _In_ uint32* value, uint32 size)'),
    };
  } catch {
    api = false;
  }
  return api;
}

const WCA_ACCENT_POLICY = 19;
// 0 — выключено, 3 — простое размытие, 4 — акриловое размытие
const STATES = { none: 0, blur: 3, acrylic: 4 };

const handleOf = (win) => {
  const buf = win.getNativeWindowHandle();
  return buf.length === 8 ? buf.readBigUInt64LE() : BigInt(buf.readUInt32LE());
};

// mode: 'acrylic' | 'blur' | 'none'
function setBlur(win, mode) {
  const a = load();
  if (!a || !win || win.isDestroyed()) return false;
  const { koffi } = a;
  const ptr = koffi.alloc(a.ACCENT, 1);
  try {
    // оттенок задаёт сама страница (полупрозрачный островок), поэтому здесь почти прозрачный
    koffi.encode(ptr, a.ACCENT, { AccentState: STATES[mode] ?? 0, AccentFlags: 0, GradientColor: 0x01000000, AnimationId: 0 });
    return a.SetWindowCompositionAttribute(handleOf(win), { Attrib: WCA_ACCENT_POLICY, pvData: ptr, cbData: koffi.sizeof(a.ACCENT) });
  } catch {
    return false;
  } finally {
    koffi.free(ptr);
  }
}

const DWMWA_WINDOW_CORNER_PREFERENCE = 33;
const DWMWCP_ROUND = 2; // скругление 8 px, как у обычных окон Windows 11
const DWMWA_BORDER_COLOR = 34;
const DWMWA_COLOR_NONE = 0xfffffffe;

// Подложка: скруглённые углы и без светлой рамки Windows 11 вокруг окна
function styleUnderlay(win) {
  const a = load();
  if (!a || !win || win.isDestroyed()) return;
  try {
    const h = handleOf(win);
    a.DwmSetWindowAttribute(h, DWMWA_WINDOW_CORNER_PREFERENCE, [DWMWCP_ROUND], 4);
    a.DwmSetWindowAttribute(h, DWMWA_BORDER_COLOR, [DWMWA_COLOR_NONE], 4);
  } catch {}
}

const SWP_NOACTIVATE = 0x10;
const SWP_NOOWNERZORDER = 0x200;
// Поставить окно в заданные координаты (физические пиксели) сразу под другим окном
function placeBelow(win, above, x, y, w, h) {
  const a = load();
  if (!a || !win || win.isDestroyed()) return;
  try {
    a.SetWindowPos(handleOf(win), above && !above.isDestroyed() ? handleOf(above) : 0n, x, y, w, h, SWP_NOACTIVATE | SWP_NOOWNERZORDER);
  } catch {}
}

module.exports = { setBlur, styleUnderlay, placeBelow, available: () => !!load() };
