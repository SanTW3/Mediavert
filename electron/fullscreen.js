// Определение полноэкранных приложений (видео во весь экран, игры, презентации) на Windows.
// Electron не видит чужие окна, поэтому спрашиваем у Windows напрямую через koffi.
let koffi = null;
let api = null;

function load() {
  if (api !== null) return api;
  api = false;
  if (process.platform !== 'win32') return api;
  try {
    koffi = require('koffi');
    const user32 = koffi.load('user32.dll');
    const shell32 = koffi.load('shell32.dll');
    const RECT = koffi.struct('RECT', { left: 'int32', top: 'int32', right: 'int32', bottom: 'int32' });
    const MONITORINFO = koffi.struct('MONITORINFO', { cbSize: 'uint32', rcMonitor: RECT, rcWork: RECT, dwFlags: 'uint32' });
    api = {
      RECT,
      MONITORINFO,
      GetForegroundWindow: user32.func('intptr_t __stdcall GetForegroundWindow()'),
      GetWindowRect: user32.func('bool __stdcall GetWindowRect(intptr_t hWnd, _Out_ RECT* lpRect)'),
      IsZoomed: user32.func('bool __stdcall IsZoomed(intptr_t hWnd)'),
      IsWindowVisible: user32.func('bool __stdcall IsWindowVisible(intptr_t hWnd)'),
      GetWindowThreadProcessId: user32.func('uint32 __stdcall GetWindowThreadProcessId(intptr_t hWnd, _Out_ uint32* pid)'),
      GetClassNameW: user32.func('int __stdcall GetClassNameW(intptr_t hWnd, _Out_ uint16_t* name, int max)'),
      MonitorFromWindow: user32.func('intptr_t __stdcall MonitorFromWindow(intptr_t hWnd, uint32 flags)'),
      GetMonitorInfoW: user32.func('bool __stdcall GetMonitorInfoW(intptr_t hMonitor, _Inout_ MONITORINFO* info)'),
      SetWindowPos: user32.func('bool __stdcall SetWindowPos(intptr_t hWnd, intptr_t after, int x, int y, int cx, int cy, uint32 flags)'),
      SHQueryUserNotificationState: shell32.func('int32 __stdcall SHQueryUserNotificationState(_Out_ int32* state)'),
    };
  } catch {
    api = false;
  }
  return api;
}

const MONITOR_DEFAULTTONEAREST = 2;
// окна рабочего стола и панели задач занимают весь экран, но полноэкранными не считаются
const SHELL_CLASSES = new Set(['Progman', 'WorkerW', 'Shell_TrayWnd', 'Shell_SecondaryTrayWnd']);
// QUNS_RUNNING_D3D_FULL_SCREEN (игры в эксклюзивном режиме), QUNS_PRESENTATION_MODE
const FULLSCREEN_STATES = new Set([3, 4]);

const handleOf = (win) => {
  const buf = win.getNativeWindowHandle();
  return buf.length === 8 ? buf.readBigUInt64LE() : BigInt(buf.readUInt32LE());
};

function className(hwnd) {
  const buf = new Uint16Array(128);
  const n = api.GetClassNameW(hwnd, buf, 128);
  return String.fromCharCode(...buf.slice(0, Math.max(n, 0)));
}

function monitorRect(hwnd) {
  const mon = api.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST);
  const info = { cbSize: koffi.sizeof(api.MONITORINFO), rcMonitor: {}, rcWork: {}, dwFlags: 0 };
  if (!api.GetMonitorInfoW(mon, info)) return null;
  return { mon: BigInt(mon), rect: info.rcMonitor };
}

// Есть ли полноэкранное приложение на том же мониторе, что и окно win
function isFullscreenOn(win) {
  if (!load() || !win || win.isDestroyed()) return false;
  try {
    const state = [0];
    const systemSays = api.SHQueryUserNotificationState(state) === 0 && FULLSCREEN_STATES.has(state[0]);

    const fg = api.GetForegroundWindow();
    if (!fg) return systemSays;
    const pid = [0];
    api.GetWindowThreadProcessId(fg, pid);
    if (pid[0] === process.pid) return false; // наши окна (главное, челка)
    if (!api.IsWindowVisible(fg) || SHELL_CLASSES.has(className(fg))) return systemSays;

    const ours = monitorRect(handleOf(win));
    const theirs = monitorRect(fg);
    if (!ours || !theirs || ours.mon !== theirs.mon) return false; // другой монитор — не мешает

    const r = {};
    api.GetWindowRect(fg, r);
    const m = theirs.rect;
    const coversMonitor = r.left <= m.left && r.top <= m.top && r.right >= m.right && r.bottom >= m.bottom;
    // развёрнутое обычное окно (IsZoomed) — не полноэкранный режим, даже если панель задач скрыта
    return systemSays || (coversMonitor && !api.IsZoomed(fg));
  } catch {
    return false;
  }
}

const HWND_BOTTOM = 1;
const SWP_NOSIZE = 0x1, SWP_NOMOVE = 0x2, SWP_NOACTIVATE = 0x10, SWP_NOOWNERZORDER = 0x200;

// Отправить окно под все остальные (чтобы прозрачная челка не лежала поверх игры)
function sendToBottom(win) {
  if (!load() || !win || win.isDestroyed()) return;
  try {
    api.SetWindowPos(handleOf(win), HWND_BOTTOM, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_NOOWNERZORDER);
  } catch {}
}

// Следит за полноэкранными приложениями; onChange(true/false) при смене состояния
function watch(getWin, onChange, interval = 1000) {
  if (!load()) return () => {};
  let last = false;
  const timer = setInterval(() => {
    const now = isFullscreenOn(getWin());
    if (now !== last) {
      last = now;
      onChange(now);
    }
  }, interval);
  return () => clearInterval(timer);
}

module.exports = { watch, isFullscreenOn, sendToBottom, available: () => !!load() };
