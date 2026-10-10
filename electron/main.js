const { app, BrowserWindow, Tray, Menu, ipcMain, screen, shell, dialog, nativeImage, Notification, nativeTheme, systemPreferences } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const settings = require('./settings');
const Jobs = require('./jobs');
const integration = require('./integration');
const updater = require('./updater');
const fullscreen = require('./fullscreen');
const { extOf } = require('../lib/formats');

const ROOT = path.join(__dirname, '..');
const ICON = path.join(ROOT, 'assets', 'icon.png');
const PRELOAD = path.join(__dirname, 'preload.js');

// Окно «челки» фиксированного размера; видимая область задаётся из shelf.js
const SHELF_WIN = { w: 720, h: 360 };
const SHELF_IDLE = { w: 200, h: 8 };

let mainWin = null;
let shelfWin = null;
let tray = null;
let quitting = false;
let shelfRegion = { ...SHELF_IDLE };
let pointerTimer = null;
let leaveTicks = 0;

const jobs = new Jobs(2);
const dragIcons = new Map();

// Материал Mica есть в Windows 11 22H2 (сборка 22621) и новее
const MICA = process.platform === 'win32' && Number(os.release().split('.')[2]) >= 22621;

function titleBarColors() {
  const dark = nativeTheme.shouldUseDarkColors;
  return {
    color: MICA ? '#00000000' : dark ? '#17171b' : '#f2f2f5',
    symbolColor: dark ? '#e8e8ee' : '#2a2a33',
    height: 48,
  };
}

// ID остался от старого названия (MediaShift): так установщик обновляет прежнюю установку,
// а не ставит вторую программу рядом
// Chromium перестаёт рисовать окна, которые считает невидимыми. Из-за этого главное окно,
// заранее показанное прозрачным под «каплей», появлялось пустым на ~150 мс.
app.commandLine.appendSwitch('disable-features', 'CalculateNativeWinOcclusion');
app.setAppUserModelId('com.mediashift.app');
migrateFromMediaShift();

// Переезд со старого названия: переносим настройки из %APPDATA%\MediaShift
function migrateFromMediaShift() {
  if (process.env.MEDIAVERT_SELFTEST) return;
  try {
    const target = path.join(app.getPath('userData'), 'settings.json');
    const legacy = path.join(app.getPath('appData'), 'MediaShift', 'settings.json');
    if (!fs.existsSync(target) && fs.existsSync(legacy)) {
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.copyFileSync(legacy, target);
    }
  } catch {}
}
if (process.env.MEDIAVERT_SELFTEST) app.setPath('userData', path.join(process.env.MEDIAVERT_SELFTEST, 'userdata'));

/* ---------- запуск ---------- */

// Путь к программе для контекстного меню и автозапуска
function launchCommand() {
  const exe = process.env.PORTABLE_EXECUTABLE_FILE || process.execPath;
  return app.isPackaged ? { exe, args: [] } : { exe, args: [ROOT] };
}

function parseArgv(argv) {
  const args = argv.slice(app.isPackaged ? 1 : 2);
  const isFile = (p) => {
    try { return fs.statSync(p).isFile(); } catch { return false; }
  };
  const i = args.indexOf('--convert');
  if (i >= 0) return { convert: { format: args[i + 1], files: args.slice(i + 2).filter(isFile) } };
  return { files: args.filter((a) => !a.startsWith('--') && isFile(a)), hidden: args.includes('--hidden') };
}

function handleArgv(argv, fromSecondInstance) {
  const p = parseArgv(argv);
  if (p.convert) {
    startConvert(p.convert.files, p.convert.format, null, 'context');
    return;
  }
  if (p.files.length) {
    showMain();
    sendWhenReady(mainWin, 'files:add', p.files);
    return;
  }
  if (fromSecondInstance || !p.hidden) showMain();
}

function outputDirFor(file) {
  const s = settings.get();
  return s.outputMode === 'beside' ? path.dirname(file) : s.outputDir;
}

function startConvert(files, format, options, source) {
  const s = settings.get();
  return files.map((input) =>
    jobs.add({
      input,
      format,
      options: options || s.options,
      outDir: outputDirFor(input),
      fallbackDir: s.outputDir,
      source,
    }),
  );
}

/* ---------- окна ---------- */

function sendWhenReady(win, channel, data) {
  if (!win || win.isDestroyed()) return;
  if (win.webContents.isLoading()) win.webContents.once('did-finish-load', () => win.webContents.send(channel, data));
  else win.webContents.send(channel, data);
}

function broadcast(channel, data) {
  for (const w of BrowserWindow.getAllWindows()) if (!w.isDestroyed()) w.webContents.send(channel, data);
}

function createMain({ autoShow = true } = {}) {
  mainWin = new BrowserWindow({
    width: 1180,
    height: 820,
    minWidth: 760,
    minHeight: 560,
    title: 'Mediavert',
    icon: ICON,
    backgroundColor: MICA ? '#00000000' : nativeTheme.shouldUseDarkColors ? '#17171b' : '#f2f2f5',
    backgroundMaterial: MICA ? 'mica' : undefined,
    titleBarStyle: 'hidden',
    titleBarOverlay: titleBarColors(),
    show: false,
    autoHideMenuBar: true,
    webPreferences: { preload: PRELOAD, sandbox: false, backgroundThrottling: false, additionalArguments: MICA ? ['--mediavert-mica'] : [] },
  });
  mainWin.removeMenu();
  mainWin.loadFile(path.join(ROOT, 'renderer', 'index.html'));
  if (autoShow) mainWin.once('ready-to-show', () => mainWin.show());
  mainWin.webContents.on('will-navigate', (e) => e.preventDefault());
  mainWin.on('close', (e) => {
    if (quitting) return;
    e.preventDefault();
    mainWin.hide();
    const s = settings.get();
    if (!s.trayHintShown) {
      settings.set({ trayHintShown: true });
      notify('Mediavert работает в трее', 'Челка и меню Проводника доступны. Выйти — правой кнопкой по иконке в трее.');
    }
  });
}

function showMain() {
  if (!mainWin || mainWin.isDestroyed()) createMain();
  else {
    if (mainWin.isMinimized()) mainWin.restore();
    mainWin.show();
    mainWin.focus();
  }
}

function createShelf() {
  if (shelfWin && !shelfWin.isDestroyed()) return;
  shelfWin = new BrowserWindow({
    width: SHELF_WIN.w,
    height: SHELF_WIN.h,
    frame: false,
    transparent: true,
    backgroundColor: '#00000000',
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    focusable: false,
    hasShadow: false,
    thickFrame: false,
    show: false,
    webPreferences: { preload: PRELOAD, sandbox: false, backgroundThrottling: false },
  });
  applyShelfLayer();
  shelfWin.loadFile(path.join(ROOT, 'renderer', 'shelf.html'));
  shelfWin.webContents.on('will-navigate', (e) => e.preventDefault());
  shelfRegion = { ...SHELF_IDLE };
  shelfWin.once('ready-to-show', () => {
    placeShelf();
    shelfWin.showInactive();
    startFullscreenWatch();
  });
  shelfWin.on('closed', () => {
    shelfWin = null;
    clearInterval(pointerTimer);
    pointerTimer = null;
  });
}

function destroyShelf() {
  if (shelfWin && !shelfWin.isDestroyed()) shelfWin.destroy();
  shelfWin = null;
  applyShelfLayer();
  startFullscreenWatch();
}

// Полноэкранные приложения (видео, игры, презентации): челка прячется — гаснет,
// не ловит мышь и уходит под все окна. Окно при этом не скрывается (hide() ломает клики).
let shelfAway = false;
let stopFullscreenWatch = null;
function startFullscreenWatch() {
  stopFullscreenWatch?.();
  stopFullscreenWatch = null;
  setShelfAway(false);
  if (!shelfWin || shelfWin.isDestroyed() || !settings.get().hideInFullscreen) return;
  stopFullscreenWatch = fullscreen.watch(() => shelfWin, setShelfAway);
}
function setShelfAway(away) {
  if (away === shelfAway) return;
  shelfAway = away;
  if (!shelfWin || shelfWin.isDestroyed()) return;
  shelfWin.webContents.send('shelf:away', away);
  if (away) {
    clearInterval(layerTimer);
    layerTimer = null;
    shelfWin.setAlwaysOnTop(false);
    fullscreen.sendToBottom(shelfWin);
  } else {
    applyShelfLayer();
  }
}

// Слой челки: «top» — поверх всех окон, «desktop» — как обычное окно, его закрывают приложения.
// В режиме «top» раз в пару секунд возвращаем челку наверх: другие окна «поверх всех»
// (плееры, оверлеи, диспетчер задач) могут её перекрыть.
let layerTimer = null;
function applyShelfLayer() {
  clearInterval(layerTimer);
  layerTimer = null;
  if (!shelfWin || shelfWin.isDestroyed()) return;
  if (shelfAway) {
    shelfWin.setAlwaysOnTop(false);
    return;
  }
  if (settings.get().shelfLayer === 'desktop') {
    shelfWin.setAlwaysOnTop(false);
  } else {
    shelfWin.setAlwaysOnTop(true, 'screen-saver');
    layerTimer = setInterval(() => {
      if (shelfWin && !shelfWin.isDestroyed() && shelfWin.isVisible()) shelfWin.moveTop();
    }, 2000);
  }
}

// Окно челки всегда одного размера и не двигается: изменение размера прозрачного окна
// на Windows даёт кадры со сдвинутым старым содержимым. Вместо этого меняем видимую
// и кликабельную область (setShape) — всё снаружи неё прозрачно для мыши и drop.
function placeShelf() {
  if (!shelfWin || shelfWin.isDestroyed()) return;
  const d = screen.getPrimaryDisplay().bounds;
  shelfWin.setBounds({ x: Math.round(d.x + (d.width - SHELF_WIN.w) / 2), y: d.y, width: SHELF_WIN.w, height: SHELF_WIN.h });
  applyShelfRegion();
}

function regionRect() {
  const w = Math.min(Math.max(Math.round(shelfRegion.w), 1), SHELF_WIN.w);
  const h = Math.min(Math.max(Math.round(shelfRegion.h), 1), SHELF_WIN.h);
  return { x: Math.round((SHELF_WIN.w - w) / 2), y: 0, width: w, height: h };
}

function applyShelfRegion() {
  if (!shelfWin || shelfWin.isDestroyed()) return;
  shelfWin.setShape([regionRect()]);
  const open = shelfRegion.h > 100;
  if (open && !pointerTimer) {
    leaveTicks = 0;
    pointerTimer = setInterval(checkPointer, 150);
  } else if (!open && pointerTimer) {
    clearInterval(pointerTimer);
    pointerTimer = null;
  }
}

// Сворачиваем челку, когда курсор ушёл (работает и во время перетаскивания)
function checkPointer() {
  if (!shelfWin || shelfWin.isDestroyed()) return;
  const p = screen.getCursorScreenPoint();
  const wb = shelfWin.getBounds();
  const r = regionRect();
  const b = { x: wb.x + r.x, y: wb.y, width: r.width, height: r.height };
  const inside = p.x >= b.x - 12 && p.x <= b.x + b.width + 12 && p.y >= b.y && p.y <= b.y + b.height + 24;
  leaveTicks = inside ? 0 : leaveTicks + 1;
  if (leaveTicks === 5) shelfWin.webContents.send('shelf:pointer-left');
}

/* ---------- «перетекание» челки в главное окно ---------- */

let morphWin = null;
let morphLanded = null;
let morphAlmost = null;

function prefersReducedMotion() {
  try {
    return !!systemPreferences.getAnimationSettings().prefersReducedMotion;
  } catch {
    return false;
  }
}

// Прозрачное окно на весь экран, сквозь которое проходят клики; рисует только «каплю»
function ensureMorphWin() {
  if (morphWin && !morphWin.isDestroyed()) return Promise.resolve(morphWin);
  morphWin = new BrowserWindow({
    frame: false,
    transparent: true,
    backgroundColor: '#00000000',
    resizable: false,
    movable: false,
    focusable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    hasShadow: false,
    thickFrame: false,
    show: false,
    webPreferences: { preload: PRELOAD, sandbox: false, backgroundThrottling: false },
  });
  morphWin.setIgnoreMouseEvents(true);
  morphWin.setAlwaysOnTop(true, 'screen-saver');
  morphWin.on('closed', () => (morphWin = null));
  // Окно показывается один раз и живёт за краем экрана: показ окна Windows анимирует
  // (оно проявляется ~200 мс), а перенос — нет. Для анимации окно просто переносится на экран.
  return morphWin.loadFile(path.join(ROOT, 'renderer', 'morph.html')).then(() => {
    parkMorphWin();
    morphWin.showInactive();
    return morphWin;
  });
}

function parkMorphWin() {
  if (!morphWin || morphWin.isDestroyed()) return;
  const d = screen.getPrimaryDisplay().bounds;
  morphWin.setBounds({ x: -20000, y: -20000, width: d.width, height: d.height });
}

const withTimeout = (promise, ms) => Promise.race([promise, new Promise((r) => setTimeout(r, ms))]);

// rect — островок челки в координатах окна челки
async function openMainFromShelf(rect) {
  const canMorph =
    rect && shelfWin && !shelfWin.isDestroyed() && !prefersReducedMotion() &&
    (!mainWin || mainWin.isDestroyed() || (!mainWin.isVisible() && !mainWin.isMinimized()));
  if (!canMorph) return showMain();

  if (!mainWin || mainWin.isDestroyed()) {
    createMain({ autoShow: false });
    await withTimeout(new Promise((r) => mainWin.once('ready-to-show', r)), 3000);
  }
  const display = screen.getPrimaryDisplay().bounds;
  const target = mainWin.getBounds();
  const onDisplay =
    target.x + target.width / 2 > display.x && target.x + target.width / 2 < display.x + display.width &&
    target.y + target.height / 2 > display.y && target.y + target.height / 2 < display.y + display.height;
  if (!onDisplay) return showMain();

  const morph = await ensureMorphWin();
  const sb = shelfWin.getBounds();
  morph.setBounds(display);
  morph.moveTop();
  morph.webContents.send('morph:play', {
    from: { x: sb.x + rect.x - display.x, y: sb.y + rect.y - display.y, w: rect.width, h: rect.height },
    to: { x: target.x - display.x, y: target.y - display.y, w: target.width, h: target.height },
    toColor: nativeTheme.shouldUseDarkColors ? '#17171b' : '#f2f2f5',
  });
  // пока капля летит, главное окно уже показано, но прозрачно — успевает отрисоваться
  mainWin.setOpacity(0);
  mainWin.showInactive();
  mainWin.webContents.send('window:prepare');
  // капля уже нарисована поверх островка — убираем сам островок
  // островок убираем, когда капля уже нарисована поверх.
  // Окно челки НЕ скрываем: после hide()/showInactive() Windows перестаёт
  // нормально передавать ему клики, и челка больше не открывается.
  setTimeout(() => shelfWin && !shelfWin.isDestroyed() && shelfWin.webContents.send('shelf:morph-started'), 50);

  const landed = new Promise((r) => (morphLanded = r));
  await withTimeout(new Promise((r) => (morphAlmost = r)), 1500);
  morphAlmost = null;

  // капля почти приняла форму окна — делаем настоящее окно видимым под ней
  mainWin.setOpacity(1);
  mainWin.show();
  mainWin.focus();
  mainWin.webContents.send('window:enter');

  await withTimeout(landed, 1000);
  morphLanded = null;
  parkMorphWin();
}

/* ---------- трей и уведомления ---------- */

function notify(title, body, onClick) {
  if (!Notification.isSupported()) return;
  const n = new Notification({ title, body, icon: ICON, silent: true });
  if (onClick) n.on('click', onClick);
  n.show();
}

function buildTrayMenu() {
  if (!tray) return;
  const s = settings.get();
  const up = updater.getStatus();
  const updateItems =
    up.state === 'ready'
      ? [{ label: `Перезапустить и обновить до ${up.version}`, click: () => updater.install() }, { type: 'separator' }]
      : [];
  tray.setContextMenu(
    Menu.buildFromTemplate([
      ...updateItems,
      { label: 'Открыть Mediavert', click: showMain },
      { label: 'Папка с результатами', click: openOutput },
      {
        label: 'Как пользоваться',
        click: () => {
          showMain();
          sendWhenReady(mainWin, 'guide:open');
        },
      },
      { type: 'separator' },
      { label: '«Челка» сверху экрана', type: 'checkbox', checked: s.shelf, click: (i) => applySettings({ shelf: i.checked }).catch(() => {}) },
      {
        label: 'Где показывать челку',
        enabled: s.shelf,
        submenu: [
          { label: 'Поверх всех окон', type: 'radio', checked: s.shelfLayer !== 'desktop', click: () => applySettings({ shelfLayer: 'top' }).catch(() => {}) },
          { label: 'Только на рабочем столе', type: 'radio', checked: s.shelfLayer === 'desktop', click: () => applySettings({ shelfLayer: 'desktop' }).catch(() => {}) },
        ],
      },
      { label: 'Меню Проводника «Конвертировать в…»', type: 'checkbox', checked: s.contextMenu, click: (i) => applySettings({ contextMenu: i.checked }).catch((e) => notify('Mediavert', e.message)) },
      { label: 'Запускать вместе с Windows', type: 'checkbox', checked: s.autostart, click: (i) => applySettings({ autostart: i.checked }).catch(() => {}) },
      { type: 'separator' },
      ...(updater.isConfigured() ? [{ label: 'Проверить обновления', click: () => updater.check() }] : []),
      { label: `Версия ${app.getVersion()}`, enabled: false },
      { label: 'Выход', click: () => app.quit() },
    ]),
  );
}

function createTray() {
  const img = nativeImage.createFromPath(ICON).resize({ width: 32, height: 32, quality: 'best' });
  tray = new Tray(img);
  tray.setToolTip('Mediavert — конвертер');
  tray.on('click', showMain);
  buildTrayMenu();
}

function openOutput() {
  const dir = settings.get().outputDir;
  fs.mkdirSync(dir, { recursive: true });
  shell.openPath(dir);
}

/* ---------- настройки ---------- */

async function applySettings(patch) {
  const before = settings.get();
  if ('contextMenu' in patch && patch.contextMenu !== before.contextMenu) {
    const { exe, args } = launchCommand();
    const command = [exe, ...args].map((a) => `"${a}"`).join(' ');
    await integration.setContextMenu(patch.contextMenu, command, `${exe},0`);
  }
  if ('accent' in patch && !/^#[0-9a-f]{6}$/i.test(String(patch.accent))) delete patch.accent;
  if ('autoUpdate' in patch) updater.startSchedule(!!patch.autoUpdate);
  if ('shelfLayer' in patch && !['top', 'desktop'].includes(patch.shelfLayer)) delete patch.shelfLayer;
  if ('theme' in patch) nativeTheme.themeSource = ['light', 'dark'].includes(patch.theme) ? patch.theme : 'system';
  if ('autostart' in patch) {
    const { exe, args } = launchCommand();
    app.setLoginItemSettings({ openAtLogin: patch.autostart, path: exe, args: [...args, '--hidden'], name: 'Mediavert' });
  }
  const s = settings.set(patch);
  if ('shelf' in patch) (s.shelf ? createShelf() : destroyShelf());
  if ('shelfLayer' in patch) applyShelfLayer();
  if ('hideInFullscreen' in patch) startFullscreenWatch();
  buildTrayMenu();
  broadcast('settings:changed', s);
  return s;
}

/* ---------- IPC ---------- */

ipcMain.handle('jobs:add', (_e, { files, format, options, source }) =>
  startConvert(files, format, options, source === 'main' ? 'main' : 'shelf'),
);
ipcMain.handle('jobs:list', () => jobs.list());
ipcMain.handle('jobs:clear', (_e, sources) => jobs.clear(sources));
ipcMain.handle('settings:get', () => settings.get());
ipcMain.handle('settings:set', (_e, patch) => applySettings(patch));
ipcMain.handle('dialog:pick-folder', async (e) => {
  const r = await dialog.showOpenDialog(BrowserWindow.fromWebContents(e.sender), {
    properties: ['openDirectory', 'createDirectory'],
    defaultPath: settings.get().outputDir,
  });
  return r.canceled ? null : r.filePaths[0];
});

ipcMain.handle('app:version', () => app.getVersion());
ipcMain.handle('update:status', () => updater.getStatus());
ipcMain.handle('update:check', () => updater.check());
ipcMain.handle('update:install', () => updater.install());

ipcMain.on('file:reveal', (_e, p) => p && shell.showItemInFolder(p));
ipcMain.on('file:open', (_e, p) => p && shell.openPath(p));
ipcMain.on('output:open', openOutput);
ipcMain.on('main:open', (_e, paths, rect) => {
  openMainFromShelf(rect)
    .catch(() => showMain())
    .finally(() => {
      if (paths?.length) sendWhenReady(mainWin, 'files:add', paths);
    });
});
ipcMain.on('morph:landed', () => morphLanded?.());
ipcMain.on('morph:almost', () => morphAlmost?.());
ipcMain.on('shelf:region', (_e, r) => {
  if (!r || !Number.isFinite(r.w) || !Number.isFinite(r.h)) return;
  shelfRegion = { w: r.w, h: r.h };
  applyShelfRegion();
});

// Перетаскивание готового файла из приложения в DAW / Проводник
ipcMain.on('file:drag-out', (e, file) => {
  if (!file || !fs.existsSync(file)) return;
  const icon = dragIcons.get(extOf(file)) || nativeImage.createFromPath(ICON).resize({ width: 48, height: 48 });
  e.sender.startDrag({ file, icon });
});

/* ---------- задачи ---------- */

let contextDone = [];
jobs.on('update', (job) => broadcast('job:update', job));
jobs.on('finished', (job) => {
  if (job.status === 'done') {
    const ext = extOf(job.output);
    if (!dragIcons.has(ext)) {
      app.getFileIcon(job.output, { size: 'large' }).then((img) => dragIcons.set(ext, img)).catch(() => {});
    }
  }
  // Без челки сообщаем о результатах конвертации из Проводника уведомлением
  if (job.source === 'context') {
    contextDone.push(job);
    if (!jobs.pending('context')) {
      const ok = contextDone.filter((j) => j.status === 'done');
      const failed = contextDone.length - ok.length;
      const last = ok[ok.length - 1];
      contextDone = [];
      if (!shelfWin || failed) {
        notify(
          failed ? 'Mediavert: есть ошибки' : 'Mediavert: готово',
          `Сконвертировано: ${ok.length}${failed ? `, ошибок: ${failed}` : ''}`,
          last ? () => shell.showItemInFolder(last.output) : showMain,
        );
      }
    }
  }
});

/* ---------- жизненный цикл ---------- */

// Исходный argv передаём через additionalData: в параметре argv Chromium может переставить аргументы
if (!app.requestSingleInstanceLock({ argv: process.argv })) {
  app.quit();
} else {
  app.on('second-instance', (_e, argv, _cwd, data) =>
    app.whenReady().then(() => handleArgv(Array.isArray(data?.argv) ? data.argv : argv, true)),
  );
  app.on('window-all-closed', () => {}); // остаёмся в трее
  app.on('before-quit', () => {
    quitting = true;
    jobs.killAll();
  });

  app.whenReady().then(async () => {
    const s = settings.get();
    nativeTheme.themeSource = ['light', 'dark'].includes(s.theme) ? s.theme : 'system';
    nativeTheme.on('updated', () => {
      if (mainWin && !mainWin.isDestroyed()) {
        mainWin.setTitleBarOverlay(titleBarColors());
        if (!MICA) mainWin.setBackgroundColor(titleBarColors().color);
      }
    });
    createTray();
    if (s.shelf) createShelf();
    setTimeout(() => ensureMorphWin().catch(() => {}), 3000);

    let notifiedVersion = null;
    updater.init((st) => {
      broadcast('update:status', st);
      buildTrayMenu();
      if (st.state === 'ready' && st.version !== notifiedVersion) {
        notifiedVersion = st.version;
        notify(`Обновление ${st.version} готово`, 'Нажмите, чтобы перезапустить Mediavert. Или оно установится само при следующем выходе.', () => updater.install());
      }
    });
    updater.startSchedule(s.autoUpdate);
    screen.on('display-metrics-changed', placeShelf);
    screen.on('display-removed', placeShelf);
    // обновляем путь в меню Проводника (например, после переустановки)
    if (s.contextMenu) {
      const { exe, args } = launchCommand();
      integration.setContextMenu(true, [exe, ...args].map((a) => `"${a}"`).join(' '), `${exe},0`).catch(() => {});
    }
    // автозапуск: убираем запись со старым названием и при необходимости ставим новую
    if (app.isPackaged && !process.env.MEDIAVERT_SELFTEST) {
      const { exe, args } = launchCommand();
      app.setLoginItemSettings({ openAtLogin: false, path: exe, name: 'MediaShift' });
      if (s.autostart) app.setLoginItemSettings({ openAtLogin: true, path: exe, args: [...args, '--hidden'], name: 'Mediavert' });
    }
    if (process.env.MEDIAVERT_SELFTEST) require('./selftest')({ app, jobs, startConvert, getShelf: () => shelfWin, getMain: () => mainWin, showMain });
    else handleArgv(process.argv, false);
  });
}
