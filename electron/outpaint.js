// Дорисовка краёв фото до нужных пропорций.
// Два движка: «быстрый» — LaMa (≈210 МБ, процессор, продолжает фон) и «качественный» — SDXL Inpainting
// (≈4,9 ГБ, видеокарта, по-настоящему генерирует новые детали). Сверху всегда ложится исходное фото в полном качестве.
//
// Качественный движок работает в три шага:
// 1. LaMa черново заполняет поля (по серому холсту SDXL часто «оставляет рамку» серой);
// 2. SDXL рисует весь холст в ≈1 Мп — так получается цельная композиция;
// 3. новые поля прорисовываются заново кусками 1024×1024 почти в натуральную величину с малой силой —
//    композиция остаётся, появляются резкость и мелкие детали. Зерно исходника подмешивается в конце.
const fs = require('fs');
const os = require('os');
const net = require('net');
const http = require('http');
const path = require('path');
const crypto = require('crypto');
const { spawn } = require('child_process');
const { Worker } = require('worker_threads');
const { app } = require('electron');
const tools = require('./tools');
const { ffmpegPath } = require('../lib/convert');

const LAMA = 512;
const SD_AREA = 1024 * 1024; // SDXL обучен на ≈1 Мп
const TILE = 1024;
const TILE_OVERLAP = 128;
const REFINE_BUDGET = 4_500_000; // сколько пикселей новых полей прорисовываем (≈6 кусков)
const RATIOS = { '16:9': 16 / 9, '9:16': 9 / 16, '4:3': 4 / 3, '3:4': 3 / 4, '4:5': 4 / 5, '1:1': 1 };
const MAX_GROW = 3.2; // сильнее нейросеть уже не продолжает фото, а выдумывает
// без слова «photo»: рисунки и арт должны продолжаться в своём стиле
const SD_PROMPT = 'seamless continuation of the image, same style, consistent lighting and perspective, high detail';
const SD_NEGATIVE = 'frame, border, letterbox, gray bars, blank area, empty background, solid color, text, watermark, logo, collage, split screen, blurry, lowres';

const unpacked = (p) => p.replace('app.asar', 'app.asar.unpacked');
const ortPath = unpacked(path.join(__dirname, '..', 'node_modules', 'onnxruntime-web', 'dist', 'ort.node.min.mjs'));
const workerPath = unpacked(path.join(__dirname, 'lama-worker.js'));

const even = (n) => Math.max(2, Math.round(n / 2) * 2);
const round64 = (n) => Math.min(Math.max(Math.round(n / 64) * 64, 512), 1536);
const floor64 = (n) => Math.floor(n / 64) * 64;

// Какие компоненты нужны движку
const engineTools = (engine) => (engine === 'sd' ? ['lama', 'sdcpp', 'sdxl-inpaint'] : ['lama', 'esrgan']);

// Размер холста и место исходного фото на нём; null — расширять не нужно
function canvasFor(w, h, mode) {
  let W = w;
  let H = h;
  if (mode === 'more') {
    W = w * 1.3;
    H = h * 1.3;
  } else if (RATIOS[mode]) {
    const r = RATIOS[mode];
    if (w / h < r) W = h * r;
    else H = w / r;
  } else return null;
  W = even(W);
  H = even(H);
  if (W - w < 4 && H - h < 4) return null;
  if (W / w > MAX_GROW || H / h > MAX_GROW) throw new Error('Слишком сильное расширение для этого фото — выберите пропорции ближе к исходным');
  return { W, H, L: Math.floor((W - w) / 2), T: Math.floor((H - h) / 2) };
}

function ffmpeg(args, onSpawn) {
  return new Promise((resolve, reject) => {
    const proc = spawn(ffmpegPath, ['-hide_banner', '-nostdin', '-y', '-loglevel', 'error', ...args], { windowsHide: true });
    onSpawn?.(proc);
    const out = [];
    let err = '';
    proc.stdout.on('data', (d) => out.push(d));
    proc.stderr.on('data', (d) => (err = (err + d).slice(-2000)));
    proc.on('error', reject);
    proc.on('close', (code) => (code === 0 ? resolve(Buffer.concat(out)) : reject(new Error(err.trim() || `ffmpeg: код ${code}`))));
  });
}

// Картинка → сырые RGB-пиксели нужного размера
const readRGB = (file, w, h, onSpawn) =>
  ffmpeg(['-i', file, '-vf', `scale=${w}:${h}:flags=lanczos`, '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'], onSpawn);

async function writePNG(file, pixels, w, h, onSpawn, pixFmt = 'rgb24') {
  const raw = `${file}.raw`;
  fs.writeFileSync(raw, pixels);
  await ffmpeg(['-f', 'rawvideo', '-pix_fmt', pixFmt, '-s', `${w}x${h}`, '-i', raw, '-frames:v', '1', file], onSpawn);
  fs.rmSync(raw, { force: true });
}

// Уменьшенное фото на холсте gw×gh и маска (1 — дорисовать). Маску чуть заводим на фото, чтобы перерисовался и шов.
async function prepare(src, c, w, h, gw, gh, grow, edges, onSpawn) {
  const kx = gw / c.W;
  const ky = gh / c.H;
  const l = Math.round(c.L * kx);
  const t = Math.round(c.T * ky);
  const sw = Math.min(Math.max(1, Math.round(w * kx)), gw - l);
  const sh = Math.min(Math.max(1, Math.round(h * ky)), gh - t);
  const small = await readRGB(src, sw, sh, onSpawn);
  const rgb = Buffer.alloc(gw * gh * 3, 127);
  const mask = Buffer.alloc(gw * gh, 255);
  for (let y = 0; y < sh; y++) {
    for (let x = 0; x < sw; x++) {
      const i = (t + y) * gw + l + x;
      small.copy(rgb, i * 3, (y * sw + x) * 3, (y * sw + x) * 3 + 3);
      const nearEdge = (edges.left && x < grow) || (edges.right && x >= sw - grow) || (edges.top && y < grow) || (edges.bottom && y >= sh - grow);
      if (!nearEdge) mask[i] = 0;
    }
  }
  return { rgb, mask, kx, ky };
}

function lamaRun(image, mask, onSpawn) {
  return new Promise((resolve, reject) => {
    const worker = new Worker(workerPath, {
      workerData: { ortPath, modelPath: tools.pathOf('lama'), threads: Math.max(2, Math.min(os.cpus().length, 8)), size: LAMA, image, mask },
      transferList: [image.buffer, mask.buffer],
    });
    onSpawn?.({ kill: () => worker.terminate() });
    let done = false;
    worker.on('message', (m) => {
      if (m.type === 'done') { done = true; resolve(m.data); worker.terminate(); }
      else if (m.type === 'error') { done = true; reject(new Error(`Нейросеть не смогла обработать фото: ${m.message}`)); worker.terminate(); }
    });
    worker.on('error', (e) => { if (!done) { done = true; reject(e); } });
    worker.on('exit', () => { if (!done) reject(new Error('Отменено')); });
  });
}

// Быстрый движок: холст сжимается в 512×512 — LaMa работает только с таким размером
async function fillLama({ src, c, w, h, edges, work, onSpawn }) {
  const S = LAMA;
  const grow = 2;
  const { rgb, mask, kx, ky } = await prepare(src, c, w, h, S, S, grow, edges, onSpawn);
  const image = new Float32Array(3 * S * S);
  const m = new Float32Array(S * S);
  for (let i = 0; i < S * S; i++) {
    m[i] = mask[i] ? 1 : 0;
    if (m[i]) continue;
    image[i] = rgb[i * 3] / 255;
    image[S * S + i] = rgb[i * 3 + 1] / 255;
    image[2 * S * S + i] = rgb[i * 3 + 2] / 255;
  }
  const filled = await lamaRun(image, m, onSpawn);
  const out = Buffer.alloc(S * S * 3);
  for (let i = 0; i < S * S; i++) {
    out[i * 3] = filled[i];
    out[i * 3 + 1] = filled[S * S + i];
    out[i * 3 + 2] = filled[2 * S * S + i];
  }
  await writePNG(path.join(work, 'fill.png'), out, S, S, onSpawn);
  return { size: S, feather: Math.ceil((grow + 1) / Math.min(kx, ky)) };
}

/* ---------- SDXL через sd-server: модель грузится один раз на задачу ---------- */

const servers = new Set();
app?.on?.('will-quit', () => servers.forEach((p) => p.kill()));

const freePort = () =>
  new Promise((resolve, reject) => {
    const s = net.createServer();
    s.once('error', reject);
    s.listen(0, '127.0.0.1', () => {
      const { port } = s.address();
      s.close(() => resolve(port));
    });
  });

// POST без ограничения времени: у fetch в Node жёсткий лимит 300 с, а первая генерация на слабой видеокарте бывает дольше
function postJSON(port, route, body) {
  return new Promise((resolve, reject) => {
    const data = Buffer.from(JSON.stringify(body));
    const req = http.request({ host: '127.0.0.1', port, path: route, method: 'POST', headers: { 'content-type': 'application/json', 'content-length': data.length } }, (res) => {
      const chunks = [];
      res.on('data', (d) => chunks.push(d));
      res.on('end', () => {
        try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); } catch { resolve({}); }
      });
      res.on('error', reject);
    });
    req.on('error', reject);
    req.end(data);
  });
}

const gpuError = () => new Error('Не хватает видеокарты для качественной дорисовки (нужна видеокарта с Vulkan и 6+ ГБ памяти). Выберите быструю модель.');

async function startServer(onLine) {
  const dir = tools.dirOf('sdcpp');
  const port = await freePort();
  const proc = spawn(
    path.join(dir, 'sd-server.exe'),
    ['-m', path.relative(dir, tools.pathOf('sdxl-inpaint')), '--vae-tiling', '--listen-ip', '127.0.0.1', '--listen-port', String(port)],
    { cwd: dir, windowsHide: true },
  );
  servers.add(proc);
  let log = '';
  let exited = false;
  const feed = (d) => {
    log = (log + d).slice(-4000);
    onLine(String(d));
  };
  proc.stdout.on('data', feed);
  proc.stderr.on('data', feed);
  proc.on('exit', () => { exited = true; servers.delete(proc); });
  const base = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 600; i++) {
    if (exited) throw /memory|alloc|vulkan|device/i.test(log) ? gpuError() : new Error(`Не удалось запустить нейросеть: ${log.trim().split('\n').pop()}`);
    try {
      const r = await fetch(`${base}/sdapi/v1/sd-models`);
      if (r.ok) break;
    } catch {}
    await new Promise((r) => setTimeout(r, 300));
  }
  return {
    proc,
    async generate({ init, mask, width, height, strength, prompt }) {
      let j;
      try {
        j = await postJSON(port, '/sdapi/v1/img2img', {
          prompt, negative_prompt: SD_NEGATIVE,
          init_images: [fs.readFileSync(init).toString('base64')], mask: fs.readFileSync(mask).toString('base64'),
          denoising_strength: strength, width, height, steps: 30, cfg_scale: 6,
          seed: crypto.randomInt(1, 2 ** 31), sampler_name: 'euler_a',
        });
      } catch {
        throw exited ? new Error('Отменено') : gpuError();
      }
      if (!j.images?.[0]) throw /memory|alloc|vulkan|device/i.test(JSON.stringify(j) + log) ? gpuError() : new Error('Нейросеть не смогла дорисовать фото');
      return Buffer.from(j.images[0], 'base64');
    },
    stop() { proc.kill(); },
  };
}

// Куски 1024×1024 вдоль новых полей: по одному ряду/столбцу на каждый край
function tilesFor(Ws, Hs, box, edges) {
  const tw = Math.min(TILE, floor64(Ws));
  const th = Math.min(TILE, floor64(Hs));
  const ctx = 96; // захватываем немного исходника, чтобы нейросеть видела, что продолжать
  const along = (a0, a1, size, total) => {
    const a = Math.min(Math.max(a0, 0), total - size);
    const b = Math.min(Math.max(a1 - size, 0), total - size);
    if (b - a < size / 4) return [a1 >= total ? b : a]; // чуть меньше контекста лучше лишнего ряда кусков
    const n = Math.ceil((b - a) / (size - TILE_OVERLAP)) + 1;
    return Array.from({ length: n }, (_, i) => Math.round(a + ((b - a) * i) / (n - 1)));
  };
  const bands = [];
  if (edges.left) bands.push([0, box.x0 + ctx, 0, Hs]);
  if (edges.right) bands.push([box.x1 - ctx, Ws, 0, Hs]);
  if (edges.top) bands.push([0, Ws, 0, box.y0 + ctx]);
  if (edges.bottom) bands.push([0, Ws, box.y1 - ctx, Hs]);
  const tiles = [];
  for (const [x0, x1, y0, y1] of bands) {
    for (const y of along(y0, y1, th, Hs)) for (const x of along(x0, x1, tw, Ws)) tiles.push({ x, y, w: tw, h: th });
  }
  return tiles;
}

const crop = (buf, W, ch, x, y, w, h) => {
  const out = Buffer.alloc(w * h * ch);
  for (let r = 0; r < h; r++) buf.copy(out, r * w * ch, ((y + r) * W + x) * ch, ((y + r) * W + x + w) * ch);
  return out;
};

// Качественный движок. Возвращает файл холста и масштаб, в котором он нарисован.
async function fillSD(args) {
  const { src, c, w, h, edges, work, prompt, onProgress, onStage, onSpawn } = args;
  const text = String(prompt || '').trim();
  const fullPrompt = text ? `${text}, ${SD_PROMPT}` : SD_PROMPT;

  // 1. черновик LaMa
  await fillLama(args);
  fs.renameSync(path.join(work, 'fill.png'), path.join(work, 'draft.png'));

  // сколько кусков прорисовки получится — от этого зависит шкала прогресса
  const newArea = c.W * c.H - w * h;
  const s = Math.min(1, Math.sqrt(REFINE_BUDGET / newArea));
  const gw = round64(Math.sqrt((SD_AREA * c.W) / c.H));
  const gh = round64(Math.sqrt((SD_AREA * c.H) / c.W));
  const refine = s > (gw / c.W) * 1.3;
  const Ws = refine ? Math.round(c.W * s) : 0;
  const Hs = refine ? Math.round(c.H * s) : 0;
  const box = refine && { x0: Math.round(c.L * s), y0: Math.round(c.T * s), x1: Math.round((c.L + w) * s), y1: Math.round((c.T + h) * s) };
  const tiles = refine ? tilesFor(Ws, Hs, box, edges) : [];
  const steps = 2 + tiles.length; // запуск, общий проход, куски
  let done = 0;
  const tick = (part = 1) => onProgress(Math.min((done + part) / steps, 1));

  onStage('Запускаю нейросеть…');
  let server;
  let current = null;
  onSpawn({ kill: () => { server?.stop(); current?.kill(); } });
  server = await startServer((line) => {
    const m = [...line.matchAll(/\|\s*(\d+)\/(\d+)\s*-/g)].pop();
    if (m && +m[2] > 1) tick(+m[1] / +m[2]);
  });
  const sub = (p) => (current = p);
  try {
    done = 1;
    // 2. общий проход по всему холсту
    onStage('Рисую края…');
    const grow = 8;
    const { rgb, mask } = await prepare(src, c, w, h, gw, gh, grow, edges, sub);
    const draft = await readRGB(path.join(work, 'draft.png'), gw, gh, sub);
    for (let i = 0; i < gw * gh; i++) if (mask[i]) draft.copy(rgb, i * 3, i * 3, i * 3 + 3);
    await writePNG(path.join(work, 'init.png'), rgb, gw, gh, sub);
    await writePNG(path.join(work, 'mask.png'), mask, gw, gh, sub, 'gray');
    const png = await server.generate({ init: path.join(work, 'init.png'), mask: path.join(work, 'mask.png'), width: gw, height: gh, strength: 0.9, prompt: fullPrompt });
    fs.writeFileSync(path.join(work, 'fill.png'), png);
    done = 2;
    if (!refine) return { file: 'fill.png', size: Math.max(gw, gh), feather: Math.ceil((grow + 1) * (c.W / gw)) };

    // 3. прорисовка новых полей в крупном масштабе
    const canvas = await readRGB(path.join(work, 'fill.png'), Ws, Hs, sub);
    const orig = await readRGB(src, box.x1 - box.x0, box.y1 - box.y0, sub);
    const rg = 16; // насколько маска заходит на исходник
    const soft = new Float32Array(Ws * Hs).fill(1); // 1 — можно менять, 0 — исходник
    const ow = box.x1 - box.x0;
    for (let y = box.y0; y < box.y1; y++) {
      for (let x = box.x0; x < box.x1; x++) {
        const d = Math.min(
          edges.left ? x - box.x0 : Infinity, edges.right ? box.x1 - 1 - x : Infinity,
          edges.top ? y - box.y0 : Infinity, edges.bottom ? box.y1 - 1 - y : Infinity,
        );
        const m = d >= rg ? 0 : 1 - d / rg;
        soft[y * Ws + x] = m;
        const i = (y * Ws + x) * 3;
        const j = ((y - box.y0) * ow + x - box.x0) * 3;
        for (let k = 0; k < 3; k++) canvas[i + k] = Math.round(orig[j + k] * (1 - m) + canvas[i + k] * m);
      }
    }
    const R = 48; // плавный переход на стыках кусков
    for (const [n, t] of tiles.entries()) {
      onStage(`Прорисовываю детали ${n + 1}/${tiles.length}…`);
      const m8 = Buffer.alloc(t.w * t.h);
      for (let y = 0; y < t.h; y++) for (let x = 0; x < t.w; x++) m8[y * t.w + x] = soft[(t.y + y) * Ws + t.x + x] > 0 ? 255 : 0;
      await writePNG(path.join(work, 'tile.png'), crop(canvas, Ws, 3, t.x, t.y, t.w, t.h), t.w, t.h, sub);
      await writePNG(path.join(work, 'tmask.png'), m8, t.w, t.h, sub, 'gray');
      const outPng = await server.generate({ init: path.join(work, 'tile.png'), mask: path.join(work, 'tmask.png'), width: t.w, height: t.h, strength: 0.35, prompt: fullPrompt });
      fs.writeFileSync(path.join(work, 'tile-out.png'), outPng);
      const out = await readRGB(path.join(work, 'tile-out.png'), t.w, t.h, sub);
      for (let y = 0; y < t.h; y++) {
        const ry = Math.min(t.y > 0 ? y / R : Infinity, t.y + t.h < Hs ? (t.h - 1 - y) / R : Infinity, 1);
        for (let x = 0; x < t.w; x++) {
          const rx = Math.min(t.x > 0 ? x / R : Infinity, t.x + t.w < Ws ? (t.w - 1 - x) / R : Infinity, 1);
          const a = soft[(t.y + y) * Ws + t.x + x] * Math.min(rx, ry);
          if (a <= 0) continue;
          const i = ((t.y + y) * Ws + t.x + x) * 3;
          const j = (y * t.w + x) * 3;
          for (let k = 0; k < 3; k++) canvas[i + k] = Math.round(out[j + k] * a + canvas[i + k] * (1 - a));
        }
      }
      done = 3 + n;
    }
    await writePNG(path.join(work, 'refined.png'), canvas, Ws, Hs, sub);
    return { file: 'refined.png', size: Math.max(c.W, c.H), feather: Math.ceil(rg / s) };
  } finally {
    server.stop();
  }
}

/* ---------- сборка ---------- */

// Подмешиваем в дорисованные поля зерно исходника: нейросеть рисует гладко, и на стыке это заметно
function matchGrain(canvas, W, H, orig, w, h, L, T, edges) {
  const band = 64;
  const hf = (buf, bw, x, y, k) => {
    const i = (y * bw + x) * 3 + k;
    return buf[i] - (buf[i - 3] + buf[i + 3] + buf[i - bw * 3] + buf[i + bw * 3]) / 4;
  };
  // исходник — полоса у края; дорисованное — подальше от шва, где нет примеси исходника
  const stat = (inOrig) => {
    const sum = [0, 0, 0];
    let n = 0;
    for (let y = 1; y < H - 1; y += 3) {
      for (let x = 1; x < W - 1; x += 3) {
        const ox = x - L;
        const oy = y - T;
        const inside = ox >= 1 && oy >= 1 && ox < w - 1 && oy < h - 1;
        const d = Math.min(edges.left ? Math.abs(ox) : Infinity, edges.right ? Math.abs(w - ox) : Infinity, edges.top ? Math.abs(oy) : Infinity, edges.bottom ? Math.abs(h - oy) : Infinity);
        if (inside !== inOrig) continue;
        if (inOrig ? d > band * 2 || d < 8 : d < band) continue;
        for (let k = 0; k < 3; k++) sum[k] += inOrig ? hf(orig, w, ox, oy, k) ** 2 : hf(canvas, W, x, y, k) ** 2;
        n++;
      }
    }
    return n ? sum.map((v) => Math.sqrt(v / n)) : [0, 0, 0];
  };
  const so = stat(true);
  const sg = stat(false);
  // шум с σ даёт высокочастотную составляющую ≈ σ·√1.25 — переводим обратно
  const add = so.map((v, k) => Math.sqrt(Math.max(0, v * v - sg[k] * sg[k])) / Math.sqrt(1.25));
  if (Math.max(...add) < 1.5) return;
  const gauss = () => Math.sqrt(-2 * Math.log(Math.random() || 1e-9)) * Math.cos(2 * Math.PI * Math.random());
  // шумим весь холст: под исходником он виден только в полосе плавного перехода — там и нужен
  for (let i = 0; i < W * H * 3; i += 3) {
    for (let k = 0; k < 3; k++) canvas[i + k] = Math.max(0, Math.min(255, Math.round(canvas[i + k] + gauss() * add[k])));
  }
}

// src.png (w×h) → out.png с дорисованными краями. Возвращает false, если расширять нечего.
async function outpaint({ src, out, w, h, mode, engine, prompt, work, onProgress, onStage, onSpawn }) {
  const c = canvasFor(w, h, mode);
  if (!c) return false;
  const { W, H, L, T } = c;
  const edges = { left: L > 0, right: L + w < W, top: T > 0, bottom: T + h < H };

  // 1. Нейросеть рисует холст
  onStage(engine === 'sd' ? 'Генерирую края…' : 'Дорисовываю края…');
  onProgress(0.05);
  const args = { src, c, w, h, edges, work, prompt, onStage, onSpawn, onProgress: (p) => onProgress(0.05 + p * 0.8) };
  let fillFile = 'fill.png';
  let size;
  let feather;
  if (engine === 'sd') ({ file: fillFile, size, feather } = await fillSD(args));
  else ({ size, feather } = await fillLama(args));
  onProgress(0.85);

  // 2. Быстрому движку увеличиваем дорисованное, чтобы новые поля не были мыльными
  if (engine !== 'sd' && tools.isInstalled('esrgan') && Math.max(W, H) > size * 1.5) {
    onStage('Повышаю чёткость краёв…');
    try {
      await tools.run(
        tools.pathOf('esrgan'),
        ['-i', path.relative(tools.dirOf('esrgan'), path.join(work, fillFile)), '-o', path.relative(tools.dirOf('esrgan'), path.join(work, 'fill4.png')), '-n', 'realesrgan-x4plus', '-s', '4'],
        tools.dirOf('esrgan'),
        null,
        onSpawn,
      );
      fillFile = 'fill4.png';
    } catch {
      // без Vulkan обходимся обычным увеличением
    }
  }

  // 3. Собираем: дорисованный холст, поверх — исходное фото с мягким переходом на границах
  onStage('Собираю фото…');
  const canvas = await readRGB(path.join(work, fillFile), W, H, onSpawn);
  const orig = await readRGB(src, w, h, onSpawn);
  matchGrain(canvas, W, H, orig, w, h, L, T, edges);
  const f = Math.max(2, feather);
  for (let y = 0; y < h; y++) {
    const dy = Math.min(edges.top ? y : Infinity, edges.bottom ? h - 1 - y : Infinity);
    for (let x = 0; x < w; x++) {
      const d = Math.min(dy, edges.left ? x : Infinity, edges.right ? w - 1 - x : Infinity);
      const a = d >= f ? 1 : (d + 0.5) / f;
      const j = (y * w + x) * 3;
      const i = ((T + y) * W + L + x) * 3;
      for (let k = 0; k < 3; k++) canvas[i + k] = Math.round(orig[j + k] * a + canvas[i + k] * (1 - a));
    }
  }
  await writePNG(out, canvas, W, H, onSpawn);
  onProgress(0.9);
  return true;
}

module.exports = { outpaint, canvasFor, engineTools };
