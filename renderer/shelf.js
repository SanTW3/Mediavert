const { FORMAT_LIST, EXT, TARGETS } = api.formats;
const $ = (s) => document.querySelector(s);

const island = $('#island');
const SOURCES = ['shelf', 'context'];

// Видимая область окна для каждого состояния: размер островка + место под тень
const dropHeight = () => parseInt(island.style.getPropertyValue('--drop-h'), 10) || 312;
const REGION = {
  idle: () => ({ w: 200, h: 8 }),
  busy: () => ({ w: 392, h: 66 }),
  drop: () => ({ w: 700, h: dropHeight() + 28 }),
  activity: () => ({ w: 700, h: 340 }),
};
const SHRINK_MS = 420; // затухание содержимого 120 мс + сжатие 280 мс + запас

let mode = 'idle';
let dragDepth = 0;
let finishedTimer = null;
let shrinkTimer = null;
let away = false; // открыто полноэкранное приложение — челка спрятана

// пока челка спрятана, её область сжата до точки и не меняется
function setRegion(rect) {
  if (!away) api.setShelfRegion(rect);
}

api.on('shelf:away', (value) => {
  away = value;
  document.body.classList.toggle('away', value);
  if (value) {
    if (mode === 'drop' || mode === 'activity') setMode(restMode());
    api.setShelfRegion({ w: 1, h: 1 });
  } else {
    api.setShelfRegion(REGION[mode]());
  }
});
const jobs = new Map();

const extOf = (name) => (/\.([^.\\/]+)$/.exec(name || '')?.[1] || '').toLowerCase();
const kindOfFile = (name) => Object.keys(EXT).find((k) => EXT[k].includes(extOf(name))) || null;
const kindOfFormat = (f) => Object.keys(FORMAT_LIST).find((k) => FORMAT_LIST[k].some(([v]) => v === f));

/* ---------- состояния островка ---------- */

// При росте сначала расширяем видимую область, потом анимируем островок.
// При сжатии — сначала гаснет содержимое, затем сжимается островок, и только
// после окончания анимации уменьшаем область, чтобы ничего не обрезалось.
function setMode(next) {
  if (next === mode) return;
  const prev = mode;
  mode = next;
  clearTimeout(shrinkTimer);
  const from = REGION[prev]();
  const to = REGION[next]();
  const grows = to.w >= from.w && to.h >= from.h;
  if (grows) {
    setRegion(to);
    requestAnimationFrame(() => (island.className = next));
  } else {
    island.className = `${next} shrinking`;
    shrinkTimer = setTimeout(() => {
      island.classList.remove('shrinking');
      if (mode === next) setRegion(to);
    }, SHRINK_MS);
  }
  if (next === 'activity') renderJobs();
}

const activeJobs = () => [...jobs.values()].filter((j) => j.status === 'queued' || j.status === 'converting');
const restMode = () => (activeJobs().length || finishedTimer ? 'busy' : 'idle');

island.addEventListener('click', (e) => {
  if (mode === 'idle' || mode === 'busy') setMode('activity');
});

api.on('shelf:pointer-left', () => {
  if (mode === 'drop' || mode === 'activity') {
    dragDepth = 0;
    setMode(restMode());
  }
});

/* ---------- перетаскивание ---------- */

function kindsFromDrag(dt) {
  const kinds = new Set();
  for (const item of dt.items || []) {
    if (item.kind !== 'file') continue;
    const t = item.type || '';
    if (t === 'image/gif') kinds.add('video');
    else if (t.startsWith('audio/')) kinds.add('audio');
    else if (t.startsWith('video/')) kinds.add('video');
    else if (t.startsWith('image/')) kinds.add('image');
    else return null; // тип неизвестен — покажем все форматы
  }
  return kinds.size ? kinds : null;
}

function buildTiles(kinds, count) {
  const groups = new Set();
  if (kinds) for (const k of kinds) TARGETS[k].forEach((g) => groups.add(g));
  else ['audio', 'video', 'image'].forEach((g) => groups.add(g));

  const box = $('#tiles');
  box.innerHTML = '';
  for (const g of ['audio', 'video', 'image']) {
    if (!groups.has(g)) continue;
    const row = document.createElement('div');
    row.className = 'group';
    const label = { audio: 'Аудио', video: 'Видео', image: kinds && !kinds.has('image') ? 'Кадр' : 'Фото' }[g];
    row.innerHTML = `<div class="group-label">${label}</div><div class="group-tiles"></div>`;
    for (const [fmt, ext, title] of FORMAT_LIST[g]) {
      const t = document.createElement('div');
      t.className = 'tile';
      t.dataset.format = fmt;
      t.dataset.kind = g;
      const sub = fmt === 'prores' ? '.mov' : /\((.+)\)/.exec(title)?.[1] || '';
      t.innerHTML = `${fmt === 'prores' ? 'ProRes' : fmt.toUpperCase()}${sub ? `<small>${sub}</small>` : ''}`;
      row.lastElementChild.append(t);
    }
    box.append(row);
  }
  const extra = document.createElement('div');
  extra.className = 'group';
  extra.innerHTML = '<div class="group-label"></div><div class="group-tiles"><div class="tile open-app" data-format="__open">Открыть в Mediavert<small>больше настроек</small></div></div>';
  box.append(extra);
  $('#dropCount').textContent = count > 1 ? `· файлов: ${count}` : '';
  // высота островка по содержимому
  island.style.setProperty('--drop-h', `${Math.min(box.offsetTop + box.scrollHeight + 4, 312)}px`);
}

function hot(tile) {
  for (const t of document.querySelectorAll('.tile.hot')) if (t !== tile) t.classList.remove('hot');
  if (tile) tile.classList.add('hot');
}

document.addEventListener('dragenter', (e) => {
  e.preventDefault();
  dragDepth++;
  if (mode !== 'drop') {
    const files = [...(e.dataTransfer.items || [])].filter((i) => i.kind === 'file').length;
    buildTiles(kindsFromDrag(e.dataTransfer), files);
    setMode('drop');
  }
});

document.addEventListener('dragover', (e) => {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'copy';
  hot(e.target.closest?.('.tile'));
});

document.addEventListener('dragleave', () => {
  dragDepth = Math.max(dragDepth - 1, 0);
  setTimeout(() => {
    if (dragDepth === 0 && mode === 'drop') {
      hot(null);
      setMode(restMode());
    }
  }, 120);
});

document.addEventListener('drop', async (e) => {
  e.preventDefault();
  dragDepth = 0;
  const tile = e.target.closest?.('.tile');
  hot(null);
  const files = [...e.dataTransfer.files].map((f) => api.pathForFile(f)).filter(Boolean);
  if (!tile || !files.length) return setMode(restMode());
  if (tile.dataset.format === '__open') return openMainMorph(files);
  await api.convert({ files, format: tile.dataset.format, source: 'shelf' });
  setMode('activity');
});

/* ---------- задачи ---------- */

function fmtSize(b = 0) {
  if (b < 1024 ** 2) return `${Math.max(1, Math.round(b / 1024))} КБ`;
  return `${(b / 1024 ** 2).toFixed(1)} МБ`;
}

function renderBusy() {
  const active = activeJobs();
  const text = $('#busyText');
  const view = $('.busy-view');
  if (active.length) {
    clearTimeout(finishedTimer);
    finishedTimer = null;
    const pct = Math.round((active.reduce((s, j) => s + (j.progress || 0), 0) / active.length) * 100);
    view.classList.remove('finished');
    text.textContent = active.length === 1 ? `${active[0].name} → ${active[0].format.toUpperCase()}` : `Конвертирую файлов: ${active.length}`;
    $('#busyBar').style.width = `${pct}%`;
    if (mode === 'idle') setMode('busy');
  }
}

function onFinished() {
  if (activeJobs().length) return;
  const recent = [...jobs.values()].filter((j) => Date.now() - (j.finishedAt || 0) < 10000);
  const failed = recent.filter((j) => j.status === 'error').length;
  $('.busy-view').classList.add('finished');
  $('#busyText').textContent = failed ? `Готово, ошибок: ${failed} — нажмите, чтобы посмотреть` : 'Готово ✓ — нажмите, чтобы забрать файлы';
  clearTimeout(finishedTimer);
  finishedTimer = setTimeout(() => {
    finishedTimer = null;
    if (mode === 'busy' && !activeJobs().length) setMode('idle');
  }, 4000);
}

function renderJobs() {
  const ul = $('#jobs');
  const list = [...jobs.values()].sort((a, b) => b.created - a.created).slice(0, 30);
  $('#empty').hidden = list.length > 0;
  ul.hidden = !list.length;
  ul.innerHTML = '';
  for (const j of list) {
    const li = document.createElement('li');
    li.className = `job ${j.status}`;
    const kind = kindOfFormat(j.format) || kindOfFile(j.name);
    const done = j.status === 'done';
    const meta =
      j.status === 'error' ? `<div class="job-meta err">${escapeHtml(j.error)}</div>`
      : done ? `<div class="job-meta ok">✓ ${escapeHtml(j.outName)} · ${fmtSize(j.size)}</div>`
      : `<div class="job-meta">${j.status === 'queued' ? 'В очереди…' : `Конвертирую… ${Math.round(j.progress * 100)}%`}</div><div class="bar"><span style="width:${Math.round(j.progress * 100)}%"></span></div>`;
    li.innerHTML = `
      <div class="job-icon" data-kind="${kind}">${escapeHtml((done ? extOf(j.outName) : j.format).slice(0, 4))}</div>
      <div><div class="job-name" title="${escapeHtml(j.input)}">${escapeHtml(j.name)}</div>${meta}</div>
      <div class="job-actions"></div>`;
    if (done) {
      li.draggable = true;
      li.title = 'Перетащите файл в DAW, мессенджер или папку';
      li.addEventListener('dragstart', (e) => {
        e.preventDefault();
        api.startDrag(j.output);
      });
      const actions = li.querySelector('.job-actions');
      actions.append(btn('▶', 'Открыть', () => api.openFile(j.output)), btn('📁', 'Показать в папке', () => api.reveal(j.output)));
    }
    ul.append(li);
  }
}

function btn(label, title, fn) {
  const b = document.createElement('button');
  b.textContent = label;
  b.title = title;
  b.onclick = (e) => {
    e.stopPropagation();
    fn();
  };
  return b;
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

function upsert(job) {
  if (!SOURCES.includes(job.source)) return;
  const prev = jobs.get(job.id);
  const finished = (job.status === 'done' || job.status === 'error') && (!prev || (prev.status !== 'done' && prev.status !== 'error'));
  if (finished) job.finishedAt = Date.now();
  else if (prev) job.finishedAt = prev.finishedAt;
  jobs.set(job.id, job);
  renderBusy();
  if (finished) onFinished();
  if (mode === 'activity') renderJobs();
}

api.on('job:update', upsert);

$('#openApp').onclick = (e) => {
  e.stopPropagation();
  openMainMorph([]);
};

// «Перетекание» в главное окно: сообщаем, где сейчас островок, а когда капля
// уже нарисована поверх — мгновенно убираем сам островок
function openMainMorph(files) {
  const r = island.getBoundingClientRect();
  api.openMain(files, { x: Math.round(r.left), y: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) });
  morphFallback = setTimeout(() => setMode(restMode()), 1200); // если анимация не запустилась
}
let morphFallback = null;
api.on('shelf:morph-started', () => {
  clearTimeout(morphFallback);
  island.classList.add('instant');
  setMode(restMode());
  island.classList.add('instant'); // setMode перезаписывает классы
  requestAnimationFrame(() => requestAnimationFrame(() => island.classList.remove('instant')));
});
$('#clear').onclick = (e) => {
  e.stopPropagation();
  api.clearJobs(SOURCES);
  for (const [id, j] of jobs) if (j.status === 'done' || j.status === 'error') jobs.delete(id);
  renderJobs();
};

api.listJobs().then((list) => list.forEach(upsert));

// приветственная вспышка полоски при запуске
setTimeout(() => {
  if (mode !== 'idle') return;
  island.classList.add('hello');
  setTimeout(() => island.classList.remove('hello'), 3000);
}, 1200);

// акцентный цвет из настроек (челка всегда тёмная)
api.getSettings().then((s) => Accent.apply(s.accent, { forceDark: true }));
api.on('settings:changed', (s) => Accent.apply(s.accent, { forceDark: true }));
