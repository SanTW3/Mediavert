const { FORMAT_LIST, EXT, TARGETS, KIND_LABEL } = api.formats;

// цвет из прошлого запуска — сразу, чтобы не мелькал фиолетовый до загрузки настроек
try { Accent.apply(localStorage.getItem('accent')); } catch {}

const PRESETS = [
  { name: 'DAW · WAV 24/48', format: 'wav', set: { sampleRate: '48000', bitDepth: '24' } },
  { name: 'Мастер · WAV 24/44.1', format: 'wav', set: { sampleRate: '44100', bitDepth: '24' } },
  { name: 'CD · WAV 16/44.1', format: 'wav', set: { sampleRate: '44100', bitDepth: '16' } },
  { name: 'Сэмпл · моно 16/44.1', format: 'wav', set: { sampleRate: '44100', bitDepth: '16', channels: '1', removeSilence: true } },
  { name: 'Демо · MP3 320', format: 'mp3', set: { sampleRate: '', bitrate: '320' } },
  { name: 'Стриминг · −14 LUFS', format: 'wav', set: { sampleRate: '44100', bitDepth: '24', normalize: true, lufs: '-14' } },
  { name: 'Архив · FLAC', format: 'flac', set: { sampleRate: '', bitDepth: '24' } },
  { name: 'Apple · ALAC', format: 'alac', set: { sampleRate: '', bitDepth: '24' } },
  { name: 'Голосовое · MP3 128 моно', format: 'mp3', set: { bitrate: '128', channels: '1' } },
];

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const state = { items: [], defaults: { audio: 'wav', video: 'mp4', image: 'png' }, settings: null };
const byJob = new Map();
let uid = 0;

const extOf = (name) => (/\.([^.\\/]+)$/.exec(name)?.[1] || '').toLowerCase();
const kindOfFile = (name) => Object.keys(EXT).find((k) => EXT[k].includes(extOf(name))) || null;
const kindOfFormat = (f) => Object.keys(FORMAT_LIST).find((k) => FORMAT_LIST[k].some(([v]) => v === f));

function fmtSize(b) {
  if (b < 1024) return `${b} Б`;
  if (b < 1024 ** 2) return `${(b / 1024).toFixed(0)} КБ`;
  if (b < 1024 ** 3) return `${(b / 1024 ** 2).toFixed(1)} МБ`;
  return `${(b / 1024 ** 3).toFixed(2)} ГБ`;
}

function defaultTarget(kind, name) {
  let f = state.defaults[kind];
  const ext = extOf(name);
  // не предлагаем конвертировать в тот же формат
  if (ext === f || (f === 'jpg' && (ext === 'jpeg' || ext === 'jfif'))) {
    f = { audio: f === 'mp3' ? 'wav' : 'mp3', video: f === 'mp4' ? 'mp3' : 'mp4', image: f === 'png' ? 'jpg' : 'png' }[kind];
  }
  return f;
}

/* ---------- настройки форматов ---------- */

function fillFormatSelect(select, groups, value) {
  select.innerHTML = '';
  for (const g of groups) {
    const og = document.createElement('optgroup');
    og.label = { audio: 'Аудио', video: 'Видео', image: 'Изображение / кадр' }[g];
    for (const [v, , label] of FORMAT_LIST[g]) og.append(new Option(label, v));
    select.append(og);
  }
  select.value = value;
}

const field = (name) => $(`.settings [name="${name}"]`);
const optionFields = () => $$('.settings [name]:not([data-pref])');

function setField(name, value) {
  const el = field(name);
  if (!el) return;
  if (el.type === 'checkbox') el.checked = !!value;
  else el.value = value;
}

function collectOptions() {
  const o = {};
  for (const el of optionFields()) o[el.name] = el.type === 'checkbox' ? el.checked : el.value.trim();
  return o;
}

let saveTimer;
function saveOptions() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => api.setSettings({ options: collectOptions(), defaults: state.defaults }), 300);
}

for (const sel of $$('[data-default]')) {
  const kind = sel.dataset.default;
  fillFormatSelect(sel, [kind], state.defaults[kind]);
  sel.addEventListener('change', () => {
    state.defaults[kind] = sel.value;
    for (const it of state.items) {
      if (it.kind === kind && it.status !== 'done' && it.status !== 'busy') {
        it.target = sel.value;
        render(it);
      }
    }
    saveOptions();
  });
}

const presetBox = $('#presets');
for (const p of PRESETS) {
  const b = document.createElement('button');
  b.className = 'preset';
  b.textContent = p.name;
  b.onclick = () => {
    // сначала сбрасываем обработку, затем применяем пресет
    ['normalize', 'removeSilence'].forEach((n) => setField(n, false));
    setField('channels', '');
    for (const [k, v] of Object.entries(p.set)) setField(k, v);
    const def = $('[data-default="audio"]');
    def.value = p.format;
    def.dispatchEvent(new Event('change'));
    $$('.preset').forEach((x) => x.classList.toggle('active', x === b));
    syncDepends();
  };
  presetBox.append(b);
}

for (const el of optionFields()) {
  el.addEventListener('input', () => {
    $$('.preset').forEach((x) => x.classList.remove('active'));
    saveOptions();
  });
}

// «Ползунок» сегментированного переключателя едет под активную кнопку
function moveThumb(seg) {
  const active = seg.querySelector('button.active');
  const thumb = seg.querySelector('.seg-thumb');
  if (!active || !thumb || !active.offsetWidth) return;
  thumb.style.width = `${active.offsetWidth}px`;
  thumb.style.transform = `translateX(${active.offsetLeft - 3}px)`;
}
new ResizeObserver(() => $$('.segmented').forEach(moveThumb)).observe(document.body);

$$('.tab').forEach((t) =>
  t.addEventListener('click', () => {
    $$('.tab').forEach((x) => x.classList.toggle('active', x === t));
    $$('.panel').forEach((p) => p.classList.toggle('active', p.dataset.panel === t.dataset.tab));
    moveThumb(t.closest('.segmented'));
  }),
);

// Зависимые строки (например, цель LUFS видна только при включённой нормализации)
function syncDepends() {
  for (const row of $$('[data-depends]')) row.hidden = !field(row.dataset.depends)?.checked;
}
$$('.settings .switch').forEach((s) => s.addEventListener('change', syncDepends));

if (api.platform?.mica) document.documentElement.classList.add('mica');

const q = field('imageQuality');
q.addEventListener('input', () => ($('#qVal').textContent = q.value));

/* ---------- настройки приложения ---------- */

function applySettingsToUI(s) {
  state.settings = s;
  for (const [k, v] of Object.entries(s.options)) setField(k, v);
  $('#qVal').textContent = field('imageQuality').value;
  Object.assign(state.defaults, s.defaults);
  for (const sel of $$('[data-default]')) sel.value = state.defaults[sel.dataset.default];
  for (const el of $$('[data-pref]')) {
    const v = s[el.dataset.pref];
    if (el.type === 'radio') el.checked = el.value === v;
    else el.checked = !!v;
  }
  $('#outDir').textContent = s.outputDir;
  $('#outDir').title = s.outputDir;
  syncDepends();
  syncTheme(s.theme);
  applyAccentSetting(s.accent);
}

function applyAccentSetting(accent) {
  syncPrefSwatches(accent);
  if (accentDialog.open) return; // во время выбора цвета показываем предпросмотр
  Accent.apply(accent);
  try { localStorage.setItem('accent', accent || ''); } catch {}
}

const LAYER_HINT = {
  top: 'Всегда видна, даже над развёрнутыми окнами',
  desktop: 'Окна её закрывают — видна, когда верх экрана свободен',
};
function syncLayer(s) {
  const layer = s.shelfLayer === 'desktop' ? 'desktop' : 'top';
  $('#layerRow').hidden = !s.shelf;
  $('#layerHint').textContent = LAYER_HINT[layer];
  $$('#layerSeg button').forEach((b) => b.classList.toggle('active', b.dataset.layer === layer));
  moveThumb($('#layerSeg'));
}
$$('#layerSeg button').forEach((b) =>
  b.addEventListener('click', () => {
    syncLayer({ ...state.settings, shelfLayer: b.dataset.layer });
    setPref({ shelfLayer: b.dataset.layer });
  }),
);

function syncTheme(theme) {
  const seg = $('#themeSeg');
  $$('button', seg).forEach((b) => b.classList.toggle('active', b.dataset.theme === (theme || 'system')));
  moveThumb(seg);
}
$$('#themeSeg button').forEach((b) =>
  b.addEventListener('click', () => {
    syncTheme(b.dataset.theme);
    setPref({ theme: b.dataset.theme });
  }),
);

async function setPref(patch) {
  const errors = $$('.pref-error');
  errors.forEach((e) => (e.textContent = ''));
  try {
    applyPrefsOnly(await api.setSettings(patch));
  } catch (e) {
    const msg = String(e.message || e).replace(/^Error invoking remote method '[^']+': (Error: )?/, '');
    errors.forEach((el) => (el.textContent = msg));
    applyPrefsOnly(await api.getSettings());
  }
}

function applyPrefsOnly(s) {
  state.settings = s;
  for (const el of $$('[data-pref]')) {
    const v = s[el.dataset.pref];
    if (el.type === 'radio') el.checked = el.value === v;
    else el.checked = !!v;
  }
  $('#outDir').textContent = s.outputDir;
  syncTheme(s.theme);
  syncLayer(s);
  applyAccentSetting(s.accent);
}

for (const el of $$('[data-pref]')) {
  el.addEventListener('change', () => setPref({ [el.dataset.pref]: el.type === 'radio' ? el.value : el.checked }));
}
$('#pickDir').onclick = async () => {
  const dir = await api.pickFolder();
  if (dir) setPref({ outputDir: dir, outputMode: 'folder' });
};
api.on('settings:changed', applyPrefsOnly);

/* ---------- окно настроек и гайд ---------- */

const prefsDialog = $('#prefsDialog');
const guideDialog = $('#guideDialog');
const slides = $$('.slide', guideDialog);
let slide = 0;

// Закрытие с анимацией: сначала проигрываем «уход», потом закрываем по-настоящему
function closeDialog(dlg) {
  if (!dlg.open || dlg.classList.contains('closing')) return;
  dlg.classList.add('closing');
  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    dlg.classList.remove('closing');
    dlg.close();
  };
  dlg.addEventListener('animationend', function onEnd(e) {
    if (e.target !== dlg || e.animationName !== 'sink') return;
    dlg.removeEventListener('animationend', onEnd);
    finish();
  });
  setTimeout(finish, 400); // на случай, если анимации отключены
}

const accentDialog = $('#accentDialog');

for (const dlg of [prefsDialog, guideDialog, accentDialog]) {
  $$('[data-close]', dlg).forEach((b) => b.addEventListener('click', () => closeDialog(dlg)));
  // клик по затемнению вокруг окна закрывает его
  dlg.addEventListener('click', (e) => {
    if (e.target === dlg) closeDialog(dlg);
  });
  // Esc тоже закрывает плавно
  dlg.addEventListener('cancel', (e) => {
    e.preventDefault();
    closeDialog(dlg);
  });
}

$('#openPrefs').onclick = () => {
  prefsDialog.showModal();
  moveThumb($('#themeSeg'));
  moveThumb($('#layerSeg'));
};

function showSlide(i) {
  slide = Math.max(0, Math.min(i, slides.length - 1));
  slides.forEach((s, n) => {
    s.classList.toggle('active', n === slide);
    s.classList.toggle('before', n < slide);
  });
  $$('#guideDots span').forEach((d, n) => d.classList.toggle('on', n === slide));
  $('#guidePrev').style.visibility = slide ? 'visible' : 'hidden';
  $('#guideNext').textContent = slide === slides.length - 1 ? 'Начать работу' : 'Далее';
}

function openGuide() {
  if (prefsDialog.open) prefsDialog.close();
  showSlide(0);
  guideDialog.showModal();
}

$('#guideDots').append(
  ...slides.map((_, n) => {
    const d = document.createElement('span');
    d.onclick = () => showSlide(n);
    return d;
  }),
);
$('#guidePrev').onclick = () => showSlide(slide - 1);
$('#guideNext').onclick = () => (slide === slides.length - 1 ? closeDialog(guideDialog) : showSlide(slide + 1));
guideDialog.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowRight') showSlide(slide + 1);
  if (e.key === 'ArrowLeft') showSlide(slide - 1);
});
// гайд считается просмотренным, как только его закрыли любым способом
guideDialog.addEventListener('close', () => {
  // первый запуск: после гайда предлагаем выбрать цвет
  if (!state.settings?.onboardingDone) {
    setPref({ onboardingDone: true });
    setTimeout(() => openAccent(true), 180);
  }
});

/* ---------- обновления ---------- */

function renderUpdate(st) {
  const text = $('#updateText');
  const action = $('#updateAction');
  const pill = $('#updatePill');
  action.hidden = false;
  action.disabled = false;
  action.textContent = 'Проверить';
  pill.hidden = st.state !== 'ready';
  switch (st.state) {
    case 'unavailable':
      text.textContent = st.reason === 'dev' ? 'Обновления работают только в установленной версии' : 'Источник обновлений не настроен';
      action.hidden = true;
      break;
    case 'checking':
      text.textContent = 'Проверяю…';
      action.disabled = true;
      break;
    case 'latest':
      text.textContent = 'Установлена последняя версия';
      break;
    case 'downloading':
      text.textContent = `Скачиваю версию ${st.version}… ${st.percent || 0}%`;
      action.disabled = true;
      break;
    case 'ready':
      text.textContent = `Версия ${st.version} скачана и готова к установке`;
      action.textContent = 'Перезапустить';
      $('#updatePillText').textContent = `Обновить до ${st.version}`;
      break;
    case 'error':
      text.textContent = 'Не удалось проверить — нет интернета или GitHub недоступен';
      break;
    default:
      text.textContent = 'Обновления проверяются автоматически';
  }
  state.update = st;
}

function installUpdate() {
  const busy = state.items.some((i) => i.status === 'busy');
  if (busy && !confirm('Сейчас идёт конвертация. Перезапустить и обновить всё равно? Незавершённые файлы будут прерваны.')) return;
  api.installUpdate();
}

$('#updateAction').onclick = () => (state.update?.state === 'ready' ? installUpdate() : api.checkUpdates());
$('#updatePill').onclick = installUpdate;
api.on('update:status', renderUpdate);

api.on('window:prepare', () => document.body.classList.add('preparing'));
// окно появилось из челки — мягко проявляем содержимое
api.on('window:enter', () => {
  document.body.classList.remove('entering');
  void document.body.offsetWidth;
  document.body.classList.remove('preparing');
  document.body.classList.add('entering');
  setTimeout(() => document.body.classList.remove('entering'), 500);
});
api.updateStatus().then(renderUpdate);
api.getVersion().then((v) => ($('#appVersion').textContent = v));

/* ---------- выбор акцентного цвета ---------- */

const wheel = $('#wheel');
const wheelWrap = $('#wheelWrap');
const knob = $('#knob');
const lightInput = $('#lightness');
const hexInput = $('#hexInput');
const pick = { h: 250, s: 1, l: 0.65, hex: Accent.DEFAULT, original: Accent.DEFAULT, saved: false, drawnL: null };

function drawWheel(l) {
  if (pick.drawnL === l) return;
  pick.drawnL = l;
  const size = wheel.width;
  const c = size / 2;
  const R = c - 1;
  const ctx = wheel.getContext('2d');
  const img = ctx.createImageData(size, size);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const dx = x - c + 0.5;
      const dy = y - c + 0.5;
      const r = Math.hypot(dx, dy);
      if (r > R + 1) continue;
      const h = (Math.atan2(dy, dx) * 180) / Math.PI;
      const { r: cr, g, b } = Accent.hexToRgb(Accent.hslToHex(h, Math.min(r / R, 1), l));
      const o = (y * size + x) * 4;
      img.data[o] = cr;
      img.data[o + 1] = g;
      img.data[o + 2] = b;
      img.data[o + 3] = Math.round(Math.min(Math.max(R + 1 - r, 0), 1) * 255); // сглаженный край
    }
  }
  ctx.putImageData(img, 0, 0);
}

function placeKnob() {
  const R = 110;
  const a = (pick.h * Math.PI) / 180;
  knob.style.left = `${R + Math.cos(a) * pick.s * R}px`;
  knob.style.top = `${R + Math.sin(a) * pick.s * R}px`;
  knob.style.background = pick.hex;
}

// Обновляет всё по текущему цвету; from — откуда пришло изменение (чтобы не перетирать ввод)
function setPickerColor(from) {
  if (from !== 'hex-input') hexInput.value = pick.hex;
  if (from !== 'slider') lightInput.value = Math.round(pick.l * 100);
  lightInput.style.setProperty('--lo', Accent.hslToHex(pick.h, pick.s, 0.3));
  lightInput.style.setProperty('--mid', Accent.hslToHex(pick.h, pick.s, 0.51));
  lightInput.style.setProperty('--hi', Accent.hslToHex(pick.h, pick.s, 0.72));
  drawWheel(pick.l);
  placeKnob();
  $$('.swatch', accentDialog).forEach((s) => s.classList.toggle('on', s.dataset.hex === pick.hex));
  Accent.apply(pick.hex); // живой предпросмотр во всём окне
}

function setPickerHex(hex, from) {
  const norm = Accent.normalize(hex);
  if (!norm) return;
  const { h, s, l } = Accent.hexToHsl(norm);
  Object.assign(pick, { h: s < 0.01 ? pick.h : h, s, l: Math.min(Math.max(l, 0.3), 0.72), hex: norm });
  setPickerColor(from);
}

function pickFromPoint(e) {
  const rect = wheelWrap.getBoundingClientRect();
  const dx = e.clientX - rect.left - rect.width / 2;
  const dy = e.clientY - rect.top - rect.height / 2;
  pick.h = ((Math.atan2(dy, dx) * 180) / Math.PI + 360) % 360;
  pick.s = Math.min(Math.hypot(dx, dy) / (rect.width / 2), 1);
  pick.hex = Accent.hslToHex(pick.h, pick.s, pick.l);
  setPickerColor('wheel');
}

wheelWrap.addEventListener('pointerdown', (e) => {
  wheelWrap.setPointerCapture(e.pointerId);
  wheelWrap.classList.add('dragging');
  pickFromPoint(e);
});
wheelWrap.addEventListener('pointermove', (e) => {
  if (wheelWrap.hasPointerCapture(e.pointerId)) pickFromPoint(e);
});
wheelWrap.addEventListener('pointerup', () => wheelWrap.classList.remove('dragging'));

lightInput.addEventListener('input', () => {
  pick.l = lightInput.value / 100;
  pick.hex = Accent.hslToHex(pick.h, pick.s, pick.l);
  setPickerColor('slider');
});
hexInput.addEventListener('input', () => {
  const v = hexInput.value.trim();
  setPickerHex(v.startsWith('#') ? v : `#${v}`, 'hex-input');
});

function makeSwatches(box, onPick) {
  for (const [name, hex] of Accent.PRESETS) {
    const b = document.createElement('button');
    b.className = 'swatch';
    b.title = name;
    b.dataset.hex = hex;
    b.style.setProperty('--c', hex);
    b.onclick = () => onPick(hex);
    box.append(b);
  }
}
makeSwatches($('#swatches'), (hex) => setPickerHex(hex));
makeSwatches($('#prefSwatches'), (hex) => {
  Accent.apply(hex);
  setPref({ accent: hex });
});

function syncPrefSwatches(accent) {
  const cur = Accent.normalize(accent) || Accent.DEFAULT;
  $$('#prefSwatches .swatch').forEach((s) => s.classList.toggle('on', s.dataset.hex === cur));
}

function openAccent(firstRun = false) {
  pick.original = Accent.normalize(state.settings?.accent) || Accent.DEFAULT;
  pick.saved = false;
  $('#accentTitle').textContent = firstRun ? 'Выберите свой цвет' : 'Цвет оформления';
  $('#accentLead').textContent = firstRun
    ? 'Последний шаг: выберите акцентный цвет — им подсвечиваются кнопки, переключатели и челка. Поменять его можно в любой момент в настройках.'
    : 'Выберите акцентный цвет — им подсвечиваются кнопки, переключатели и челка.';
  $('#accentCancel').textContent = firstRun ? 'Оставить как есть' : 'Отмена';
  setPickerHex(pick.original);
  accentDialog.showModal();
}

$('#openAccent').onclick = () => openAccent(false);
$('#accentReset').onclick = () => setPickerHex(Accent.DEFAULT);
$('#accentCancel').onclick = () => closeDialog(accentDialog);
$('#accentSave').onclick = () => {
  pick.saved = true;
  setPref({ accent: pick.hex });
  closeDialog(accentDialog);
};
// закрыли без «Готово» — возвращаем прежний цвет
accentDialog.addEventListener('close', () => {
  if (!pick.saved) Accent.apply(pick.original);
});
$('#openGuide').onclick = openGuide;
api.on('guide:open', openGuide);
$('#replayGuide').onclick = openGuide;

/* ---------- список файлов ---------- */

const list = $('#files');

function addPaths(paths) {
  addEntries(api.stat(paths));
}

function addEntries(entries) {
  const skipped = [];
  let last = null;
  for (const e of entries) {
    const kind = kindOfFile(e.name);
    if (!kind) {
      skipped.push(e.name);
      continue;
    }
    if (state.items.some((i) => i.path === e.path && i.status !== 'done')) continue;
    const it = { id: ++uid, ...e, kind, target: defaultTarget(kind, e.name), status: 'idle', progress: 0 };
    state.items.push(it);
    createRow(it);
    last = it;
  }
  if (skipped.length) alert(`Эти файлы не поддерживаются:\n${skipped.join('\n')}`);
  if (last) $(`.tab[data-tab="${kindOfFormat(last.target)}"]`).click();
  refresh();
}

function createRow(it) {
  const row = $('#rowTpl').content.firstElementChild.cloneNode(true);
  it.row = row;
  row.dataset.kind = it.kind;
  $('.ficon', row).textContent = extOf(it.name).slice(0, 4);
  $('.fname', row).textContent = it.name;
  $('.fname', row).title = it.path;
  const sel = $('.ftarget', row);
  fillFormatSelect(sel, TARGETS[it.kind].filter((g) => !(extOf(it.name) === 'gif' && g === 'audio')), it.target);
  sel.addEventListener('change', () => {
    it.target = sel.value;
    if (it.status === 'done' || it.status === 'error') it.status = 'idle';
    $(`.tab[data-tab="${kindOfFormat(it.target)}"]`).click();
    render(it);
    refresh();
  });
  row.addEventListener('dragstart', (e) => {
    if (it.status !== 'done') return;
    e.preventDefault();
    api.startDrag(it.output);
  });
  list.append(row);
  render(it);
}

function button(icon, cls, onClick, title) {
  const b = document.createElement('button');
  b.className = `icon-btn ${cls || ''}`;
  b.innerHTML = `<svg><use href="#i-${icon}"/></svg>`;
  if (title) b.title = title;
  b.onclick = onClick;
  return b;
}

function render(it) {
  const row = it.row;
  if (!row) return;
  const busy = it.status === 'busy';
  row.className = `file ${busy ? 'busy' : ''} ${it.status === 'done' ? 'done' : ''}`;
  row.draggable = it.status === 'done';
  $('.ftarget', row).value = it.target;
  $('.ftarget', row).disabled = busy;
  $('.bar span', row).style.width = `${Math.round(it.progress * 100)}%`;

  const meta = $('.fmeta', row);
  meta.className = 'fmeta';
  if (it.status === 'idle') meta.textContent = `${KIND_LABEL[it.kind]} · ${fmtSize(it.size)}`;
  if (busy) meta.textContent = it.queued ? 'В очереди…' : `Конвертирую… ${Math.round(it.progress * 100)}%`;
  if (it.status === 'done') {
    meta.classList.add('ok');
    meta.textContent = `Готово · ${it.outName} · ${fmtSize(it.outSize || 0)}`;
  }
  if (it.status === 'error') {
    meta.classList.add('err');
    meta.textContent = `Ошибка: ${it.error}`;
  }

  const actions = $('.factions', row);
  actions.innerHTML = '';
  if (it.status === 'done') {
    actions.append(
      button('play', 'go', () => api.openFile(it.output), 'Открыть'),
      button('folder', '', () => api.reveal(it.output), 'Показать в папке'),
    );
  }
  if (it.status === 'error') actions.append(button('retry', '', () => runItems([it]), 'Повторить'));
  if (!busy) actions.append(button('close', '', () => removeItem(it), 'Убрать из списка'));
}

function removeItem(it) {
  it.row.remove();
  state.items = state.items.filter((x) => x !== it);
  refresh();
}

function refresh() {
  const n = state.items.length;
  const pending = state.items.filter((i) => i.status === 'idle' || i.status === 'error').length;
  const done = state.items.filter((i) => i.status === 'done').length;
  $('#counter').textContent = n ? `Файлов: ${n} · готово: ${done}` : 'Файлов нет';
  const btn = $('#convertAll');
  btn.disabled = !pending;
  btn.textContent = pending ? `Конвертировать (${pending})` : 'Конвертировать';
  $('#dragHint').hidden = !done;
  document.body.classList.toggle('has-files', n > 0);
}

/* ---------- конвертация ---------- */

async function runItems(items) {
  const options = collectOptions();
  for (const it of items) {
    it.status = 'busy';
    it.queued = true;
    it.progress = 0;
    it.error = null;
    render(it);
    const [jobId] = await api.convert({ files: [it.path], format: it.target, options, source: 'main' });
    it.jobId = jobId;
    byJob.set(jobId, it);
  }
  refresh();
}

api.on('job:update', (job) => {
  const it = byJob.get(job.id);
  if (!it) return;
  it.progress = job.progress || 0;
  it.queued = job.status === 'queued';
  if (job.status === 'done') {
    it.status = 'done';
    it.output = job.output;
    it.outName = job.outName;
    it.outSize = job.size;
  } else if (job.status === 'error') {
    it.status = 'error';
    it.error = job.error;
  }
  render(it);
  if (job.status === 'done' || job.status === 'error') refresh();
});

$('#convertAll').addEventListener('click', () =>
  runItems(state.items.filter((i) => i.status === 'idle' || i.status === 'error')),
);
$('#clearDone').onclick = () => state.items.filter((i) => i.status === 'done').forEach(removeItem);
$('#clearAll').onclick = () => state.items.filter((i) => i.status !== 'busy').forEach(removeItem);
$('#openFolder').onclick = () => api.openOutput();

/* ---------- добавление файлов ---------- */

const drop = $('#drop');
const picker = $('#picker');
picker.addEventListener('change', () => {
  addPaths([...picker.files].map((f) => api.pathForFile(f)).filter(Boolean));
  picker.value = '';
});
let dragDepth = 0;
document.addEventListener('dragenter', (e) => {
  e.preventDefault();
  if (++dragDepth === 1) drop.classList.add('over');
});
document.addEventListener('dragover', (e) => e.preventDefault());
document.addEventListener('dragleave', () => {
  if (--dragDepth <= 0) {
    dragDepth = 0;
    drop.classList.remove('over');
  }
});
document.addEventListener('drop', (e) => {
  e.preventDefault();
  dragDepth = 0;
  drop.classList.remove('over');
  addPaths([...e.dataTransfer.files].map((f) => api.pathForFile(f)).filter(Boolean));
});
api.on('files:add', addPaths);

api.getSettings().then((s) => {
  applySettingsToUI(s);
  if (!s.onboardingDone) openGuide();
});
refresh();
