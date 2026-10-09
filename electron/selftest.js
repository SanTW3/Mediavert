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
  const finishAll = () =>
    new Promise((resolve) => {
      const check = () => (jobs.list().every((j) => j.status === 'done' || j.status === 'error') ? resolve() : setTimeout(check, 200));
      check();
    });

  try {
    settings.set({ outputMode: 'folder', outputDir: path.join(dir, 'out') });

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
      if (process.env.MEDIAVERT_REALCLICK) await realClickTest(main, shelf2);
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
