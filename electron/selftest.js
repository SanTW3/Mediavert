// Автопроверка для разработки: MEDIAVERT_SELFTEST=<папка> electron .
// Не попадает в сборку (исключена в package.json)
const fs = require('fs');
const path = require('path');
const { BrowserWindow } = require('electron');
const settings = require('./settings');
const integration = require('./integration');

module.exports = async function selftest({ app, jobs, startConvert, getShelf, getMain, showMain }) {
  const dir = process.env.MEDIAVERT_SELFTEST;
  const samples = path.join(dir, 'samples');
  const out = path.join(dir, 'shots');
  fs.mkdirSync(out, { recursive: true });
  const log = (...a) => console.log('[selftest]', ...a);
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const shot = async (win, name) => {
    const img = await win.webContents.capturePage();
    fs.writeFileSync(path.join(out, `${name}.png`), img.toPNG());
    log('shot', name, JSON.stringify(win.getBounds()));
  };
  // покадровая запись области экрана во время действия act()
  const record = async ([x, y, w, h, n], name, act) => {
    const prefix = path.join(out, name);
    fs.rmSync(`${prefix}.ready`, { force: true });
    const rec = require('child_process').spawn('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(dir, 'grabseq.ps1'), x, y, w, h, n, prefix].map(String), { stdio: 'ignore' });
    while (!fs.existsSync(`${prefix}.ready`)) await wait(20);
    await wait(80);
    await act();
    await new Promise((r) => rec.on('exit', r));
    log('frames', name);
  };

  async function morphTest(main, shelf) {
    await main.webContents.executeJavaScript(`document.querySelectorAll('dialog[open]').forEach((d) => d.close())`);
    if (process.env.MEDIAVERT_MAXIMIZED) { main.maximize(); await wait(400); }
    main.hide();
    await shelf.webContents.executeJavaScript(`setMode('activity')`);
    await wait(350); // меньше, чем челка ждёт ухода курсора
    const d = require('electron').screen.getPrimaryDisplay().bounds;
    const prefix = path.join(out, 'morph-frames');
    fs.rmSync(`${prefix}.ready`, { force: true });
    log('morph: recording');
    const rec = require('child_process').spawn('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(dir, 'grabseq2.ps1'), d.x, d.y, d.width, d.height, 20, prefix, 0.3, 4].map(String), { stdio: 'ignore' });
    rec.on('error', (e) => log('recorder error', e.message));
    while (!fs.existsSync(`${prefix}.ready`)) await wait(20);
    await wait(60);
    await shelf.webContents.executeJavaScript(`document.querySelector('#openApp').click()`);
    await new Promise((r) => rec.on('exit', r));
    await wait(300);
    const morph = BrowserWindow.getAllWindows().find((w) => w.getTitle() === 'Mediavert Morph');
    log('after morph: main visible', main.isVisible(), 'focused', main.isFocused(), '| morph parked', morph ? morph.getBounds().x < -10000 : 'none',
      '| shelf mode', await shelf.webContents.executeJavaScript('mode'), '| opacity', main.getOpacity());
    // челка после анимации: видна ли, где её область, ловит ли курсор
    await wait(1200);
    const sb = shelf.getBounds();
    const cx = sb.x + Math.round(sb.width / 2);
    const hit = (y) => require('child_process').spawnSync('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(dir, 'hit.ps1'), String(cx), String(y)], { encoding: 'utf8' }).stdout.trim();
    log('shelf after morph: visible', shelf.isVisible(), 'alwaysOnTop', shelf.isAlwaysOnTop(), 'bounds', JSON.stringify(sb), '| hit y=3:', hit(3));
    await shelf.webContents.executeJavaScript(`setMode('activity')`);
    await wait(500);
    log('shelf reopened by click-equivalent: mode', await shelf.webContents.executeJavaScript('mode'), '| hit y=40:', hit(40));
    await wait(600);
    const b = main.getBounds();
    require('child_process').spawnSync('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(dir, 'grab.ps1'), b.x, b.y, b.width, b.height, path.join(out, 'after-morph.png')].map(String));
  }

  // настоящие клики мышью: полоска челки → «Открыть Mediavert» → снова полоска
  async function realClickTest(main, shelf) {
    const click = (x, y) => require('child_process').spawnSync('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(dir, 'realclick.ps1'), String(x), String(y)], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).stdout.trim();
    await main.webContents.executeJavaScript(`document.querySelectorAll('dialog[open]').forEach((d) => d.close())`);
    main.hide();
    await wait(500);
    const sb = shelf.getBounds();
    const cx = sb.x + Math.round(sb.width / 2);
    log('1 strip:', click(cx, 3)); await wait(700);
    log('  mode', await shelf.webContents.executeJavaScript('mode'));
    const r = await shelf.webContents.executeJavaScript(`(() => { const b = document.querySelector('#openApp').getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + b.height / 2 }; })()`);
    log('2 open button:', click(sb.x + Math.round(r.x), sb.y + Math.round(r.y))); await wait(1800);
    log('  main visible', main.isVisible(), '| shelf visible', shelf.isVisible(), '| mode', await shelf.webContents.executeJavaScript('mode'));
    log('3 strip again:', click(cx, 3)); await wait(700);
    log('  mode after second click', await shelf.webContents.executeJavaScript('mode'), '| island class', await shelf.webContents.executeJavaScript('island.className'));
  }

  // слой челки: поверх окон / только на рабочем столе
  async function layerTest(main, shelf) {
    const hit = (y) => {
      const b = shelf.getBounds();
      return require('child_process').spawnSync('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(dir, 'hit.ps1'), String(b.x + Math.round(b.width / 2)), String(y)], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).stdout.trim();
    };
    const setLayer = (v) => main.webContents.executeJavaScript(`setPref({ shelfLayer: '${v}' })`);
    await main.webContents.executeJavaScript(`document.querySelectorAll('dialog[open]').forEach((d) => d.close())`);
    main.maximize(); main.show(); main.focus();
    await wait(800);
    const d = require('electron').screen.getPrimaryDisplay().bounds;
    // как выглядит полоска в обычном состоянии на тёмном и светлом окне
    await shelf.webContents.executeJavaScript("clearTimeout(finishedTimer); finishedTimer = null; setMode('idle')");
    for (const th of ['dark', 'light']) {
      await main.webContents.executeJavaScript(`setPref({ theme: '${th}' })`);
      await wait(900);
      require('child_process').spawnSync('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(dir, 'grab.ps1'), d.x + d.width / 2 - 140, d.y, 280, 24, path.join(out, `pill-${th}.png`)].map(String));
    }
    await main.webContents.executeJavaScript(`setPref({ theme: 'system' })`);

    await setLayer('top'); await wait(400);
    log('layer top, maximized window:', hit(3));

    const intruder = new BrowserWindow({ x: d.x + d.width / 2 - 200, y: d.y, width: 400, height: 120, frame: false, alwaysOnTop: true, focusable: false, show: false, title: 'Intruder' });
    intruder.setAlwaysOnTop(true, 'screen-saver');
    intruder.loadURL('data:text/html,<body style=background:%23c33></body>');
    await new Promise((r) => intruder.once('ready-to-show', r));
    intruder.showInactive(); intruder.moveTop();
    await wait(300);
    log('layer top, right after topmost intruder:', hit(3));
    await wait(2500);
    log('layer top, 2.5s later:', hit(3));
    intruder.destroy();

    await setLayer('desktop'); await wait(400);
    main.focus(); await wait(400);
    log('layer desktop, maximized window focused:', hit(3));
    main.unmaximize(); main.setBounds({ x: d.x + 200, y: d.y + 200, width: 900, height: 600 }); await wait(500);
    log('layer desktop, top of screen free:', hit(3));
    log('tray/settings value:', JSON.stringify(settings.get().shelfLayer));
    await main.webContents.executeJavaScript(`document.querySelector('#openPrefs').click()`);
    await wait(500);
    const b = main.getBounds();
    require('child_process').spawnSync('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(dir, 'grab.ps1'), b.x, b.y, b.width, b.height, path.join(out, 'prefs-layer.png')].map(String));
    await setLayer('top');
  }

  // полноэкранные чужие окна: челка прячется и возвращается
  async function fullscreenTest(main, shelf) {
    const cp = require('child_process');
    const hit = () => {
      const b = shelf.getBounds();
      return cp.spawnSync('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(dir, 'hit.ps1'), String(b.x + Math.round(b.width / 2)), '3'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).stdout.trim();
    };
    const state = async () => `away=${await shelf.webContents.executeJavaScript("document.body.classList.contains('away')")} | onTop=${shelf.isAlwaysOnTop()} | ${hit()}`;
    await main.webContents.executeJavaScript(`document.querySelectorAll('dialog[open]').forEach((d) => d.close())`);
    main.hide();
    await shelf.webContents.executeJavaScript("clearTimeout(finishedTimer); finishedTimer = null; setMode('idle')");
    await wait(600);
    log('before:', await state());
    for (const mode of ['full', 'fullmax', 'max']) {
      const form = cp.spawn('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(dir, 'fsform.ps1'), mode, '6'], { stdio: ['ignore', 'pipe', 'pipe'] });
      form.stdout.on('data', (d) => log(`  [${mode} form] ${String(d).trim()}`));
      await wait(3500);
      log(`${mode} window open:`, await state());
      await new Promise((r) => form.on('exit', r));
      await wait(1800);
      log(`${mode} window closed:`, await state());
    }
  }

  // прозрачность окна: снимки в разных режимах
  async function glassTest(main) {
    const cp = require('child_process');
    await main.webContents.executeJavaScript(`document.querySelectorAll('dialog[open]').forEach((d) => d.close())`);
    await wait(600); // после гайда само открывается окно выбора цвета — закрываем и его
    await main.webContents.executeJavaScript(`document.querySelectorAll('dialog[open]').forEach((d) => d.close())`);
    main.webContents.send('files:add', [path.join(samples, 'beat тест.wav'), path.join(samples, 'clip.mp4'), path.join(samples, 'cover.png')]);
    // яркий фон позади окна, чтобы было видно, сколько просвечивает
    const mb = main.getBounds();
    const backdrop = new BrowserWindow({ x: mb.x - 60, y: mb.y - 40, width: mb.width + 120, height: mb.height + 80, frame: false, show: false, focusable: false, title: 'Backdrop' });
    await backdrop.loadURL('data:text/html,' + encodeURIComponent('<body style="margin:0;height:100vh;background:repeating-linear-gradient(90deg,#e63946 0 120px,#f1c40f 120px 240px,#2ecc71 240px 360px,#3498db 360px 480px);font:900 120px Segoe UI;color:#fff;display:grid;place-items:center">BACKDROP</body>'));
    backdrop.showInactive();
    await wait(300);
    main.show(); main.focus(); main.moveTop();
    await main.webContents.executeJavaScript(`setPref({ theme: 'dark' })`);
    for (const [material, glass] of [['mica', 0], ['mica', 50], ['mica', 90], ['acrylic', 50]]) {
      await main.webContents.executeJavaScript(`setPref({ material: '${material}', glass: ${glass} })`);
      await wait(900);
      main.moveTop(); main.focus(); await wait(400);
      const b = main.getBounds();
      cp.spawnSync('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(dir, 'grab.ps1'), b.x, b.y, b.width, b.height, path.join(out, `glass-${material}-${glass}.png`)].map(String), { stdio: 'ignore' });
      log('glass', material, glass, '| html.glass =', await main.webContents.executeJavaScript("document.documentElement.classList.contains('glass')"));
    }
    backdrop.destroy();
    await main.webContents.executeJavaScript(`setPref({ material: 'acrylic', glass: 55 }); document.querySelector('#openPrefs').click(); document.querySelector('#glassSection').scrollIntoView()`);
    await wait(800);
    const b = main.getBounds();
    cp.spawnSync('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(dir, 'grab.ps1'), b.x, b.y, b.width, b.height, path.join(out, 'glass-prefs.png')].map(String), { stdio: 'ignore' });
    log('saved', JSON.stringify({ material: settings.get().material, glass: settings.get().glass }));
  }

  // «стекло» челки: снимки островка над рабочим столом и положение подложки
  async function shelfGlassTest(main, shelf) {
    const cp = require('child_process');
    const grabTop = (name, h = 360) => {
      const b = shelf.getBounds();
      cp.spawnSync('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(dir, 'grab.ps1'), b.x - 20, b.y, b.width + 40, h, path.join(out, name + '.png')].map(String), { stdio: 'ignore' });
    };
    const under = () => BrowserWindow.getAllWindows().find((w) => w.getTitle() === 'Mediavert Glass');
    await main.webContents.executeJavaScript(`document.querySelectorAll('dialog[open]').forEach((d) => d.close())`);
    main.hide();
    await shelf.webContents.executeJavaScript("clearTimeout(finishedTimer); finishedTimer = null; setMode('idle')");
    for (const [material, glass] of [['acrylic', 55], ['mica', 30]]) {
      await main.webContents.executeJavaScript(`setPref({ material: '${material}', glass: ${glass} })`);
      await wait(900);
      await shelf.webContents.executeJavaScript("setMode('activity')");
      await wait(500); // меньше, чем челка ждёт ухода курсора
      const u = under();
      log(`${material} ${glass}: shelf glass class`, await shelf.webContents.executeJavaScript("document.body.classList.contains('glass')"), '| underlay', u ? JSON.stringify(u.getBounds()) : 'none');
      grabTop(`shelf-glass-${material}`);
      await shelf.webContents.executeJavaScript("buildTiles(new Set(['audio']), 1); setMode('drop')");
      await wait(500);
      grabTop(`shelf-glass-${material}-drop`, 200);
      await shelf.webContents.executeJavaScript("setMode('idle')");
      await wait(900);
      log('  idle → underlay', under() ? JSON.stringify(under().getBounds()) : 'none');
    }
    await main.webContents.executeJavaScript(`setPref({ material: 'none' })`);
    await wait(700);
    log('material none → underlay', under() ? 'still exists' : 'removed', '| glass class', await shelf.webContents.executeJavaScript("document.body.classList.contains('glass')"));
  }

  // снимок вкладки «Аудио» с пресетами и плиток челки
  async function presetsTest(main, shelf) {
    const cp = require('child_process');
    await wait(700);
    await main.webContents.executeJavaScript(`document.querySelectorAll('dialog[open]').forEach((d) => d.close())`);
    await wait(500);
    await main.webContents.executeJavaScript(`document.querySelectorAll('dialog[open]').forEach((d) => d.close()); setPref({ material: 'none', theme: 'dark' }); document.querySelector('.tab[data-tab=audio]').click(); document.querySelectorAll('.preset')[4].click()`);
    await wait(700);
    main.show(); main.moveTop(); main.focus(); await wait(500);
    const b = main.getBounds();
    cp.spawnSync('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(dir, 'grab.ps1'), b.x, b.y, b.width, b.height, path.join(out, 'presets.png')].map(String), { stdio: 'ignore' });
    log('defaults', JSON.stringify(settings.get().defaults), '| preset applied', await main.webContents.executeJavaScript("JSON.stringify(collectOptions())"));
    await shelf.webContents.executeJavaScript("buildTiles(new Set(['audio']), 1); setMode('drop')");
    await wait(500);
    const sb = shelf.getBounds();
    cp.spawnSync('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(dir, 'grab.ps1'), sb.x, sb.y, sb.width, 200, path.join(out, 'shelf-tiles.png')].map(String), { stdio: 'ignore' });
  }

  // окно «Что нового» после обновления
  async function whatsNewTest(main) {
    await wait(1200);
    const info = await main.webContents.executeJavaScript(`JSON.stringify({ open: whatsNewDialog.open, title: $('#whatsNewTitle').textContent, versions: [...document.querySelectorAll('.wn-version')].map((e) => e.textContent), foot: $('#whatsNewFoot').textContent })`);
    log('whatsnew after start:', info);
    main.moveTop(); main.focus(); await wait(300);
    const b = main.getBounds();
    require('child_process').spawnSync('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(dir, 'grab.ps1'), b.x, b.y, b.width, b.height, path.join(out, 'whatsnew.png')].map(String), { stdio: 'ignore' });
    await main.webContents.executeJavaScript("document.querySelector('#whatsNewDialog .modal-foot .primary').click()");
    await wait(800);
    log('after close: lastSeenVersion =', settings.get().lastSeenVersion, '| dialog open', await main.webContents.executeJavaScript('whatsNewDialog.open'));
    // повторный запуск страницы — окно больше не должно появляться
    main.webContents.reload();
    await wait(2500);
    log('after reload: dialog open', await main.webContents.executeJavaScript('whatsNewDialog.open'));
  }

  // разворачивание/сворачивание окна в разных режимах прозрачности — покадрово
  async function maximizeTest(main) {
    const cp = require('child_process');
    await wait(800);
    await main.webContents.executeJavaScript(`document.querySelectorAll('dialog[open]').forEach((d) => d.close())`);
    await wait(500);
    await main.webContents.executeJavaScript(`document.querySelectorAll('dialog[open]').forEach((d) => d.close())`);
    const d = require('electron').screen.getPrimaryDisplay().bounds;
    const backdrop = new BrowserWindow({ x: d.x, y: d.y, width: d.width, height: d.height, frame: false, show: false, focusable: false, title: 'Backdrop' });
    await backdrop.loadURL('data:text/html,' + encodeURIComponent('<body style="margin:0;height:100vh;background:repeating-linear-gradient(90deg,#e63946 0 160px,#f1c40f 160px 320px,#2ecc71 320px 480px,#3498db 480px 640px)"></body>'));
    backdrop.showInactive();
    const modes = (process.env.MEDIAVERT_MAXIMIZE || 'mica,acrylic,none').split(',');
    for (const material of modes) {
      await main.webContents.executeJavaScript(`setPref({ material: '${material}', glass: 50, theme: 'dark' })`);
      main.unmaximize(); main.setBounds({ x: d.x + 300, y: d.y + 150, width: 1000, height: 650 }); main.show(); main.focus();
      await wait(1200);
      const rec = async (name, act) => {
        const prefix = path.join(out, name);
        fs.rmSync(`${prefix}.ready`, { force: true });
        const p = cp.spawn('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(dir, 'grabseq2.ps1'), d.x, d.y, d.width, d.height, 8, prefix, 0.22, 4].map(String), { stdio: 'ignore' });
        while (!fs.existsSync(`${prefix}.ready`)) await wait(20);
        await wait(40);
        act();
        await new Promise((r) => p.on('exit', r));
      };
      await rec(`max-${material}`, () => main.maximize());
      await wait(600);
      await rec(`unmax-${material}`, () => main.unmaximize());
      await wait(600);
      log('recorded', material);
    }
    backdrop.destroy();
  }

  // контраст текста в «стеклянном» режиме: светлая тема на тёмном фоне и наоборот
  async function contrastTest(main) {
    const cp = require('child_process');
    await wait(800);
    for (let i = 0; i < 2; i++) { await main.webContents.executeJavaScript(`document.querySelectorAll('dialog[open]').forEach((d) => d.close())`); await wait(500); }
    await main.webContents.executeJavaScript("document.querySelector('.tab[data-tab=audio]').click()");
    const d = require('electron').screen.getPrimaryDisplay().bounds;
    for (const [theme, bg] of [['light', '#1c1c22'], ['dark', '#f4f4f4']]) {
      const backdrop = new BrowserWindow({ x: d.x, y: d.y, width: d.width, height: d.height, frame: false, show: false, focusable: false, title: 'Backdrop' });
      await backdrop.loadURL('data:text/html,' + encodeURIComponent(`<body style="margin:0;height:100vh;background:${bg}"></body>`));
      backdrop.showInactive();
      for (const glass of [50, 90]) {
        await main.webContents.executeJavaScript(`setPref({ material: 'mica', glass: ${glass}, theme: '${theme}' })`);
        main.setBounds({ x: d.x + 300, y: d.y + 120, width: 1180, height: 760 }); main.show(); main.moveTop(); main.focus();
        await wait(1000);
        // снимок содержимого окна (с прозрачностью) поверх того же сплошного цвета, что позади:
        // размытие сплошного цвета даёт тот же цвет, так что картинка точная
        const img = await main.webContents.capturePage();
        const raw = path.join(out, `raw-${theme}-${glass}.png`);
        fs.writeFileSync(raw, img.toPNG());
        cp.spawnSync('powershell', ['-NoProfile', '-Command', `Add-Type -AssemblyName System.Drawing; $i=[Drawing.Image]::FromFile('${raw}'); $o=New-Object Drawing.Bitmap $i.Width,$i.Height; $g=[Drawing.Graphics]::FromImage($o); $g.Clear([Drawing.ColorTranslator]::FromHtml('${bg}')); $g.DrawImage($i,0,0,$i.Width,$i.Height); $o.Save('${path.join(out, `contrast-${theme}-${glass}.png`)}')`], { stdio: 'ignore' });
        log('contrast', theme, glass);
      }
      backdrop.destroy();
    }
  }

  // «жидкое стекло»: перетаскивание вкладок и переключателей настоящими событиями мыши
  async function liquidTest(main) {
    const cp = require('child_process');
    await wait(800);
    for (let i = 0; i < 2; i++) { await main.webContents.executeJavaScript(`document.querySelectorAll('dialog[open]').forEach((d) => d.close())`); await wait(500); }
    const wc = main.webContents;
    const rect = (sel) => wc.executeJavaScript(`(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width }; })()`);
    const mouse = (type, x, y) => wc.sendInputEvent({ type, x: Math.round(x), y: Math.round(y), button: 'left', clickCount: 1, modifiers: type === 'mouseMove' ? ['leftButtonDown'] : [] });
    const shot = async (name, region) => {
      const img = await wc.capturePage(region);
      fs.writeFileSync(path.join(out, name + '.png'), img.resize({ width: region.width * 3 }).toPNG());
    };
    for (const theme of ['light', 'dark']) {
      await wc.executeJavaScript(`setPref({ material: 'none', theme: '${theme}' }); document.querySelector('.tab[data-tab=audio]').click()`);
      await wait(700);
      const img = await wc.capturePage();
      fs.writeFileSync(path.join(out, `color-${theme}.png`), img.toPNG());
    }
    // 1) тянем «Аудио» на «Фото»
    const a = await rect('.tab[data-tab=audio]');
    const p = await rect('.tab[data-tab=image]');
    const segBox = await wc.executeJavaScript("(() => { const r = document.querySelector('.tabs').getBoundingClientRect(); return { x: Math.round(r.left) - 6, y: Math.round(r.top) - 8, width: Math.round(r.width) + 12, height: Math.round(r.height) + 16 }; })()");
    mouse('mouseDown', a.x, a.y);
    for (let i = 1; i <= 12; i++) { mouse('mouseMove', a.x + ((p.x - a.x) * i) / 12 * 0.55, a.y); await wait(16); }
    await wait(40);
    await shot('liquid-tab-mid', segBox);
    for (let i = 1; i <= 8; i++) { mouse('mouseMove', a.x + (p.x - a.x) * (0.55 + 0.45 * i / 8), a.y); await wait(16); }
    mouse('mouseUp', p.x, p.y);
    await wait(700);
    log('tab drag → active:', await wc.executeJavaScript("document.querySelector('.tab.active').dataset.tab"), '| panel:', await wc.executeJavaScript("document.querySelector('.panel.active').dataset.panel"));
    await shot('liquid-tab-after', segBox);
    // 2) кружок нормализации: тянем вправо
    await wc.executeJavaScript("document.querySelector('.tab[data-tab=audio]').click()");
    await wait(600);
    const sw = await rect('input[name=normalize]');
    const before = await wc.executeJavaScript("document.querySelector('input[name=normalize]').checked");
    const swBox = { x: Math.round(sw.x - 40), y: Math.round(sw.y - 20), width: 80, height: 40 };
    mouse('mouseDown', sw.x - 8, sw.y);
    for (let i = 1; i <= 6; i++) { mouse('mouseMove', sw.x - 8 + i * 1.6, sw.y); await wait(16); }
    await wait(60);
    await shot('liquid-switch-mid', swBox);
    for (let i = 1; i <= 6; i++) { mouse('mouseMove', sw.x + 2 + i * 1.6, sw.y); await wait(16); }
    mouse('mouseUp', sw.x + 12, sw.y);
    await wait(700);
    const after = await wc.executeJavaScript("document.querySelector('input[name=normalize]').checked");
    log('switch drag: before', before, '→ after', after, '| LUFS row visible', await wc.executeJavaScript("!document.querySelector('[data-depends=normalize]').hidden"));
    await wait(400);
    log('saved setting normalize =', settings.get().options.normalize);
    // 3) обычный клик по переключателю по-прежнему работает
    mouse('mouseDown', sw.x, sw.y); await wait(30); mouse('mouseUp', sw.x, sw.y);
    await wait(500);
    log('switch click → checked', await wc.executeJavaScript("document.querySelector('input[name=normalize]').checked"));
    // 4) обычный клик по вкладке
    const v = await rect('.tab[data-tab=video]');
    mouse('mouseDown', v.x, v.y); await wait(30); mouse('mouseUp', v.x, v.y);
    await wait(500);
    log('tab click → active:', await wc.executeJavaScript("document.querySelector('.tab.active').dataset.tab"));
  }

  // «залипание» перетаскивания: потеря фокуса и движение без зажатой кнопки
  async function stuckTest(main) {
    await wait(800);
    for (let i = 0; i < 2; i++) { await main.webContents.executeJavaScript(`document.querySelectorAll('dialog[open]').forEach((d) => d.close())`); await wait(500); }
    const wc = main.webContents;
    await wc.executeJavaScript("setPref({ material: 'none', theme: 'dark' }); document.querySelector('.tab[data-tab=audio]').click()");
    await wait(600);
    const rect = (sel) => wc.executeJavaScript(`(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
    const state = () => wc.executeJavaScript("JSON.stringify({ cls: document.querySelector('.tabs').className, active: document.querySelector('.tab.active').dataset.tab, thumb: document.querySelector('.tabs .seg-thumb').style.transform })");
    const a = await rect('.tab[data-tab=audio]');
    const v = await rect('.tab[data-tab=video]');
    const down = () => wc.sendInputEvent({ type: 'mouseDown', x: a.x, y: a.y, button: 'left', clickCount: 1 });
    const drag = async (to) => { for (let i = 1; i <= 6; i++) { wc.sendInputEvent({ type: 'mouseMove', x: Math.round(a.x + (to - a.x) * i / 6), y: Math.round(a.y), button: 'left', modifiers: ['leftButtonDown'] }); await wait(16); } };
    // 1) тянем и окно теряет фокус (как при скриншоте)
    down(); await drag(v.x); await wait(50);
    log('1 dragging:', await state());
    wc.executeJavaScript("window.dispatchEvent(new Event('blur'))");
    await wait(600);
    log('1 after blur:', await state());
    // движение мыши без кнопки — «пилюля» не должна ехать
    for (let i = 0; i < 5; i++) { wc.sendInputEvent({ type: 'mouseMove', x: Math.round(v.x + 40 + i * 10), y: Math.round(a.y) }); await wait(16); }
    await wait(300);
    log('1 moves w/o button:', await state());
    // 2) тянем, а следующее движение приходит уже без зажатой кнопки (отпустили вне окна)
    wc.sendInputEvent({ type: 'mouseUp', x: a.x, y: a.y, button: 'left' }); await wait(200);
    down(); await drag(v.x); await wait(50);
    for (let i = 0; i < 4; i++) { wc.sendInputEvent({ type: 'mouseMove', x: Math.round(v.x + 60 + i * 10), y: Math.round(a.y) }); await wait(16); }
    await wait(600);
    log('2 after buttonless move:', await state());
  }

  // нейросети и сжатие: распознавание речи, ИИ-улучшение фото, сжатие видео до размера
  async function aiTest() {
    const { probe } = require('../lib/convert');
    const A = path.join(dir, 'ai-samples');
    settings.set({ outputMode: 'beside' });
    // быструю модель подкладываем из уже проверенной копии, остальное скачивается по-настоящему
    const md = path.join(app.getPath('userData'), 'tools', 'whisper-base');
    fs.mkdirSync(md, { recursive: true });
    fs.copyFileSync(path.join(dir, 'dl-model', 'ggml-base.bin'), path.join(md, 'ggml-base.bin'));
    fs.writeFileSync(path.join(md, '.ok'), 'seed');
    const base = settings.get().options;
    const runOne = async (label, file, format, extra) => {
      const [id] = startConvert([file], format, { ...base, allowDownload: true, ...extra }, 'main');
      const t0 = Date.now();
      const stages = new Set();
      for (;;) {
        const j = jobs.list().find((x) => x.id === id);
        if (j.stage) stages.add(j.stage.replace(/\d+%/, '…'));
        if (j.status === 'done' || j.status === 'error') {
          log(label, j.status, j.error || '', '→', j.outName || '', ((Date.now() - t0) / 1000).toFixed(1) + 's', '| этапы:', [...stages].join(' / '));
          return j;
        }
        await wait(200);
      }
    };
    let j = await runOne('1 речь → txt', path.join(A, 'папка с речью', 'речь тест.wav'), 'txt', { speechLang: 'auto' });
    if (j.status === 'done') log('   текст:', fs.readFileSync(j.output, 'utf8').trim().replace(/\s+/g, ' '));
    j = await runOne('2 речь → srt', path.join(A, 'папка с речью', 'речь тест.wav'), 'srt', { speechLang: 'ru' });
    if (j.status === 'done') log('   srt:', fs.readFileSync(j.output, 'utf8').trim().split(/\r?\n/).slice(0, 3).join(' | '));
    j = await runOne('3 видео → srt', path.join(A, 'интервью.mp4'), 'srt', { speechLang: 'ru' });
    if (j.status === 'done') log('   srt:', fs.readFileSync(j.output, 'utf8').trim().split(/\r?\n/).slice(0, 3).join(' | '));
    j = await runOne('4 фото ИИ ×2', path.join(A, 'маленькое фото.png'), 'png', { enhance: 'ai', upscale: '2', width: '' });
    if (j.status === 'done') { const i = await probe(j.output); log('   размер:', i.width + 'x' + i.height); }
    j = await runOne('5 фото чётче', path.join(A, 'маленькое фото.png'), 'jpg', { enhance: 'clean', upscale: '', width: '' });
    j = await runOne('6 фото ×4 без ИИ', path.join(A, 'маленькое фото.png'), 'png', { enhance: '', upscale: '4', width: '' });
    if (j.status === 'done') { const i = await probe(j.output); log('   размер:', i.width + 'x' + i.height); }
    j = await runOne('7 ИИ до ширины 1000', path.join(A, 'маленькое фото.png'), 'webp', { enhance: 'ai', upscale: '', width: '1000' });
    if (j.status === 'done') { const i = await probe(j.output); log('   размер:', i.width + 'x' + i.height); }
    j = await runOne('8 видео 96 МБ → 8 МБ', path.join(A, 'тяжёлое видео.mp4'), 'mp4', { targetSize: '8', height: '', quality: 'medium', mute: false });
    if (j.status === 'done') { const i = await probe(j.output); log('   размер файла:', (fs.statSync(j.output).size / 1048576).toFixed(2) + ' МБ', '| кадр', i.width + 'x' + i.height); }
    j = await runOne('9 без разрешения скачивать', path.join(A, 'папка с речью', 'речь тест.wav'), 'txt', { speechModel: 'small', allowDownload: false });
    log('tools now:', JSON.stringify(require('./tools').status(['whisper', 'whisper-base', 'whisper-small', 'esrgan'])));
  }

  // снимки панелей настроек справа: видео, фото, текст
  async function panelShots(main) {
    await wait(800);
    for (let i = 0; i < 2; i++) { await main.webContents.executeJavaScript(`document.querySelectorAll('dialog[open]').forEach((d) => d.close())`); await wait(500); }
    await main.webContents.executeJavaScript("setPref({ material: 'none', theme: 'dark' })");
    await wait(500);
    for (const tab of ['video', 'image', 'text']) {
      await main.webContents.executeJavaScript(`document.querySelector('.tab[data-tab=${tab}]').click()`);
      await wait(600);
      const r = await main.webContents.executeJavaScript("(() => { const b = document.querySelector('.inspector').getBoundingClientRect(); return { x: Math.round(b.left), y: Math.round(b.top), width: Math.round(b.width), height: Math.min(Math.round(b.height), 640) }; })()");
      const img = await main.webContents.capturePage(r);
      fs.writeFileSync(path.join(out, `panel-${tab}.png`), img.toPNG());
    }
    log('panels captured');
  }

  // дорисовка краёв: модель подкладываем из проверенной копии
  async function outpaintTest() {
    const { probe } = require('../lib/convert');
    const A = path.join(dir, 'op-samples');
    settings.set({ outputMode: 'beside' });
    const md = path.join(app.getPath('userData'), 'tools', 'lama');
    if (!fs.existsSync(path.join(md, '.ok'))) {
      fs.mkdirSync(md, { recursive: true });
      fs.copyFileSync(path.join(dir, 'lama', 'dl', 'lama_fp32.onnx'), path.join(md, 'lama_fp32.onnx'));
      fs.writeFileSync(path.join(md, '.ok'), 'seed');
    }
    const base = settings.get().options;
    const runOne = async (label, file, format, extra) => {
      const [id] = startConvert([file], format, { ...base, enhance: '', upscale: '', width: '', allowDownload: true, ...extra }, 'main');
      const t0 = Date.now();
      const stages = new Set();
      for (;;) {
        const j = jobs.list().find((x) => x.id === id);
        if (j.stage) stages.add(j.stage.replace(/d+%/, '…'));
        if (j.status === 'done' || j.status === 'error') {
          let size = '';
          if (j.status === 'done') { const i = await probe(j.output); size = i.width + 'x' + i.height; fs.copyFileSync(j.output, path.join(out, 'op-' + label.split(' ')[0] + path.extname(j.output))); }
          log(label, j.status, j.error || '', size, ((Date.now() - t0) / 1000).toFixed(1) + 's', '| этапы:', [...stages].join(' / '));
          return j;
        }
        await wait(200);
      }
    };
    const small = path.join(A, 'фото с кириллицей', 'пейзаж.jpg');
    if (process.env.MEDIAVERT_OUTPAINT === 'sd') {
      // тяжёлая модель: файлы подкладываем жёсткими ссылками из scratchpad (sd/bin, sd/model)
      const seed = (name, from) => {
        const d = path.join(app.getPath('userData'), 'tools', name);
        if (fs.existsSync(path.join(d, '.ok'))) return;
        fs.mkdirSync(d, { recursive: true });
        for (const [src, dst] of from) { fs.rmSync(path.join(d, dst), { force: true }); fs.linkSync(src, path.join(d, dst)); }
        fs.writeFileSync(path.join(d, '.ok'), 'seed');
      };
      const bin = path.join(dir, 'sd', 'bin');
      seed('sdcpp', fs.readdirSync(bin).filter((n) => n !== 'sd.zip').map((n) => [path.join(bin, n), n]));
      seed('sdxl-inpaint', [[path.join(dir, 'sd', 'model', 'sdxl-inpaint-q8.gguf'), 'sdxl-inpainting-q8.gguf']]);
      const q = { outpaintModel: 'quality', outpaintPrompt: '' };
      if (process.env.MEDIAVERT_OUTPAINT_FILE) {
        const j = await runOne('sf файл', process.env.MEDIAVERT_OUTPAINT_FILE, 'jpg', { outpaint: '16:9', ...q, outpaintModel: process.env.OP_MODEL || 'quality' });
        if (j.canRedo) {
          const before = fs.statSync(j.output).mtimeMs;
          const id = jobs.redo(j.id);
          const t0 = Date.now();
          for (;;) {
            const r = jobs.list().find((x) => x.id === id);
            if (r.status === 'done' || r.status === 'error') {
              fs.copyFileSync(r.output, path.join(out, 'op-sf2.jpg'));
              const left = fs.readdirSync(path.dirname(r.output)).filter((n) => n.startsWith('~'));
              log('redo', r.status, r.error || '', r.outName, 'тот же файл:', r.output === j.output, 'обновлён:', fs.statSync(r.output).mtimeMs > before, 'врем. файлов:', left.length, ((Date.now() - t0) / 1000).toFixed(1) + 's');
              break;
            }
            await wait(300);
          }
        } else log('redo недоступен');
        return;
      }
      await runOne('s1 16:9', small, 'jpg', { outpaint: '16:9', ...q });
      await runOne('s2 9:16', small, 'jpg', { outpaint: '9:16', ...q });
      await runOne('s3 большое-1:1', path.join(A, 'большое.jpg'), 'jpg', { outpaint: '1:1', ...q });
      await runOne('s4 подсказка', small, 'jpg', { outpaint: '16:9', ...q, outpaintPrompt: 'concrete wall, blue sky' });
      return;
    }
    await runOne('1 16:9', small, 'jpg', { outpaint: '16:9' });
    await runOne('2 9:16', small, 'png', { outpaint: '9:16' });
    await runOne('3 шире', small, 'jpg', { outpaint: 'more' });
    await runOne('4 большое-1:1', path.join(A, 'большое.jpg'), 'jpg', { outpaint: '1:1' });
    await runOne('5 16:9+ИИ×2', small, 'png', { outpaint: '16:9', enhance: 'ai', upscale: '2' });
    await runOne('6 без-разрешения', small, 'jpg', { outpaint: '4:5', allowDownload: false });
  }
  const finishAll = () =>
    new Promise((resolve) => {
      const check = () => (jobs.list().every((j) => j.status === 'done' || j.status === 'error') ? resolve() : setTimeout(check, 200));
      check();
    });

  try {
    settings.set({ outputMode: 'folder', outputDir: path.join(dir, 'out') });
    if (process.env.MEDIAVERT_WHATSNEW) settings.set({ onboardingDone: true, lastSeenVersion: process.env.MEDIAVERT_WHATSNEW === 'none' ? null : process.env.MEDIAVERT_WHATSNEW });

    // челка: состояния и размеры окна
    let shelf;
    while (!(shelf = getShelf()) || !shelf.isVisible()) await wait(100);
    await wait(400);
    await shot(shelf, '1-shelf-idle');
    // какое окно реально под курсором в полоске и чуть ниже неё
    const b = shelf.getBounds();
    const cx = b.x + Math.round(b.width / 2);
    for (const y of [3, 20]) {
      const r = require('child_process').spawnSync('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(dir, 'hit.ps1'), String(cx), String(y)], { encoding: 'utf8' });
      log('hit', r.stdout.trim());
    }
    await shelf.webContents.executeJavaScript(`buildTiles(null, 2); setMode('drop')`);
    await wait(500);
    await shot(shelf, '2-shelf-drop-all');
    await shelf.webContents.executeJavaScript(`setMode('idle')`);
    await wait(500);
    log('after collapse', JSON.stringify(shelf.getBounds()));
    await shelf.webContents.executeJavaScript(`buildTiles(new Set(['audio']), 1); setMode('drop')`);
    await wait(500);
    await shot(shelf, '3-shelf-drop-audio');

    // покадровая съёмка экрана во время сворачивания челки
    if (process.env.MEDIAVERT_FRAMES) {
      const d = require('electron').screen.getPrimaryDisplay().bounds;
      const cx = d.x + Math.round(d.width / 2);
      await record([cx - 360, d.y, 720, 200, 16], 'collapse-frames', () => shelf.webContents.executeJavaScript(`setMode('idle')`));
      await shelf.webContents.executeJavaScript(`buildTiles(new Set(['audio']), 1); setMode('drop')`);
      await wait(600);
    }

    // конвертация через челку и через «Проводник» (--convert)
    startConvert([path.join(samples, 'beat тест.wav')], 'mp3', null, 'shelf');
    startConvert([path.join(samples, 'clip.mp4')], 'gif', null, 'shelf');
    startConvert([path.join(samples, 'cover.png')], 'wav', null, 'shelf'); // ожидаемая ошибка
    await shelf.webContents.executeJavaScript(`setMode('idle')`);
    await wait(700);
    await shot(shelf, '4-shelf-busy');
    await finishAll();
    await wait(300);
    await shot(shelf, '5-shelf-finished');
    await shelf.webContents.executeJavaScript(`setMode('activity')`);
    await wait(500);
    await shot(shelf, '6-shelf-activity');

    // аргументы командной строки, как из контекстного меню
    const argv = [process.execPath, app.getAppPath(), '--convert', 'flac', path.join(samples, 'beat тест.wav')];
    app.emit('second-instance', {}, argv, dir);
    await wait(300);
    await finishAll();
    log('jobs', JSON.stringify(jobs.list().map((j) => [j.source, j.name, j.format, j.status, j.outName, j.error])));

    // главное окно
    showMain();
    let main;
    while (!(main = getMain()) || !main.isVisible()) await wait(100);
    await wait(800);

    if (process.env.MEDIAVERT_MORPH_ONLY) {
      let shelf2;
      while (!(shelf2 = getShelf()) || !shelf2.isVisible()) await wait(100);
      if (process.env.MEDIAVERT_OUTPAINT) await outpaintTest();
      else if (process.env.MEDIAVERT_PANELS) await panelShots(main);
      else if (process.env.MEDIAVERT_AI) await aiTest();
      else if (process.env.MEDIAVERT_STUCK) await stuckTest(main);
      else if (process.env.MEDIAVERT_LIQUID) await liquidTest(main);
      else if (process.env.MEDIAVERT_CONTRAST) await contrastTest(main);
      else if (process.env.MEDIAVERT_MAXIMIZE) await maximizeTest(main);
      else if (process.env.MEDIAVERT_WHATSNEW) await whatsNewTest(main);
      else if (process.env.MEDIAVERT_PRESETS) await presetsTest(main, shelf2);
      else if (process.env.MEDIAVERT_SHELFGLASS) await shelfGlassTest(main, shelf2);
      else if (process.env.MEDIAVERT_GLASS) await glassTest(main);
      else if (process.env.MEDIAVERT_FS) await fullscreenTest(main, shelf2);
      else if (process.env.MEDIAVERT_LAYER) await layerTest(main, shelf2);
      else if (process.env.MEDIAVERT_REALCLICK) await realClickTest(main, shelf2);
      else await morphTest(main, shelf2);
      log('DONE');
      return app.exit(0);
    }

    // настоящий снимок экрана (виден материал Mica и кнопки окна)
    const grab = async (name) => {
      main.moveTop();
      main.focus();
      await wait(500);
      const b = main.getBounds();
      require('child_process').spawnSync('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(dir, 'grab.ps1'), b.x, b.y, b.width, b.height, path.join(out, `${name}.png`)].map(String), { stdio: 'ignore' });
      log('grab', name);
    };
    const theme = async (t) => {
      await main.webContents.executeJavaScript(`setPref({ theme: '${t}' })`);
      await wait(500);
    };
    const slides = await main.webContents.executeJavaScript(`document.querySelector('#guideDialog').open ? document.querySelectorAll('.slide').length : 0`);
    log('guide auto-opened, slides:', slides);
    for (let i = 0; i < slides; i++) {
      await main.webContents.executeJavaScript(`showSlide(${i})`);
      await wait(700);
      await shot(main, `guide-${i + 1}`);
    }
    if (process.env.MEDIAVERT_FRAMES) {
      main.moveTop();
      await wait(300);
      const b = main.getBounds();
      const cx = b.x + Math.round(b.width / 2);
      await main.webContents.executeJavaScript(`showSlide(1)`);
      await wait(700);
      await record([cx - 320, b.y + 110, 640, 330, 12], 'guide-flip-frames', () => main.webContents.executeJavaScript(`document.querySelector('#guideNext').click()`));
      await main.webContents.executeJavaScript(`showSlide(4)`);
      await wait(700);
    }
    await main.webContents.executeJavaScript(`document.querySelector('#guideNext').click()`);
    await wait(700);
    log('onboardingDone', settings.get().onboardingDone);

    // после гайда должно само открыться окно выбора цвета
    await wait(400);
    log('accent dialog auto-opened:', await main.webContents.executeJavaScript(`document.querySelector('#accentDialog').open`));
    await main.webContents.executeJavaScript(`setPref({ theme: 'light' })`);
    await wait(500);
    await grab('accent-default-light');
    // светлый жёлтый: текст на кнопке должен стать тёмным
    await main.webContents.executeJavaScript(`setPickerHex('#ffd84a')`);
    await wait(300);
    log('yellow tokens', await main.webContents.executeJavaScript(`JSON.stringify(Accent.tokens('#ffd84a', false))`));
    await grab('accent-yellow-light');
    // точка на круге слева-снизу от центра (≈ оттенок 148°, насыщенность ~0.86)
    log('wheel pick ->', await main.webContents.executeJavaScript(`(() => {
      const r = document.querySelector('#wheelWrap').getBoundingClientRect();
      pickFromPoint({ clientX: r.left + r.width / 2 - 80, clientY: r.top + r.height / 2 + 50 });
      return pick.hex + ' h=' + Math.round(pick.h) + ' s=' + pick.s.toFixed(2);
    })()`));
    await main.webContents.executeJavaScript(`setPickerHex('#1fa463')`);
    await main.webContents.executeJavaScript(`setPref({ theme: 'dark' })`);
    await wait(500);
    await grab('accent-mint-dark');
    await main.webContents.executeJavaScript(`document.querySelector('#accentSave').click()`);
    await wait(700);
    log('saved accent:', settings.get().accent, 'dialog open:', await main.webContents.executeJavaScript(`document.querySelector('#accentDialog').open`));
    await shelf.webContents.executeJavaScript(`buildTiles(new Set(['audio']), 1); setMode('drop'); document.querySelector('.tile[data-format=mp3]').classList.add('hot')`);
    await wait(600);
    await shot(shelf, 'shelf-mint');
    await shelf.webContents.executeJavaScript(`setMode('idle')`);

    await theme('light');
    await grab('main-empty-light');
    await theme('dark');
    await grab('main-empty-dark');

    main.webContents.send('files:add', [path.join(samples, 'beat тест.wav'), path.join(samples, 'clip.mp4'), path.join(samples, 'cover.png')]);
    await wait(600);
    await main.webContents.executeJavaScript(`document.querySelectorAll('.preset')[5].click(); document.querySelector('#convertAll').click()`);
    await wait(500);
    await finishAll();
    await wait(400);
    await grab('main-dark');
    await theme('light');
    await grab('main-light');
    await main.webContents.executeJavaScript(`document.querySelector('#openPrefs').click()`);
    await wait(500);
    await grab('prefs-light');
    if (process.env.MEDIAVERT_FRAMES) {
      const b = main.getBounds();
      await record([b.x + 300, b.y + 60, 580, 420, 10], 'prefs-close-frames', () => main.webContents.executeJavaScript(`document.querySelector('#prefsDialog .modal-foot .primary').click()`));
      log('prefs open after close:', await main.webContents.executeJavaScript(`document.querySelector('#prefsDialog').open`));
      await main.webContents.executeJavaScript(`document.querySelector('#openPrefs').click()`);
      await wait(500);
    }
    await theme('dark');
    await grab('prefs-dark');
    await main.webContents.executeJavaScript(`document.querySelector('#prefsDialog').close(); openGuide(); showSlide(2)`);
    await wait(500);
    await grab('guide-dark');
    await theme('system');

    if (process.env.MEDIAVERT_MORPH) await morphTest(main, shelf);

    fs.writeFileSync(path.join(dir, 'context-menu.reg.txt'), integration.buildReg(true, '"C:\\Program Files\\Mediavert\\Mediavert.exe"', 'C:\\x.exe,0'));
    log('settings', JSON.stringify(settings.get()));
    log('DONE');
  } catch (e) {
    log('FAILED', e.stack);
  }
  app.exit(0);
};
