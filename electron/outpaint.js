// Дорисовка краёв фото до нужных пропорций.
// Два движка: «быстрый» — LaMa (≈210 МБ, процессор, продолжает фон) и «качественный» — SDXL Inpainting
// (≈4,9 ГБ, видеокарта, по-настоящему генерирует новые детали). Нейросеть работает с уменьшенным холстом,
// результат увеличивается (Real-ESRGAN, если видеокарта позволяет), а сверху ложится исходное фото в полном качестве.
const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { spawn } = require('child_process');
const { Worker } = require('worker_threads');
const tools = require('./tools');
const { ffmpegPath } = require('../lib/convert');

const LAMA = 512;
const SD_AREA = 1024 * 1024; // SDXL обучен на ≈1 Мп
const RATIOS = { '16:9': 16 / 9, '9:16': 9 / 16, '4:3': 4 / 3, '3:4': 3 / 4, '4:5': 4 / 5, '1:1': 1 };
const MAX_GROW = 3.2; // сильнее нейросеть уже не продолжает фото, а выдумывает
const SD_PROMPT = 'photo, natural seamless continuation of the scene, consistent lighting and perspective, high detail';
const SD_NEGATIVE = 'frame, border, text, watermark, logo, signature, collage, split screen, blurry, lowres, deformed, distorted';

const unpacked = (p) => p.replace('app.asar', 'app.asar.unpacked');
const ortPath = unpacked(path.join(__dirname, '..', 'node_modules', 'onnxruntime-web', 'dist', 'ort.node.min.mjs'));
const workerPath = unpacked(path.join(__dirname, 'lama-worker.js'));

const even = (n) => Math.max(2, Math.round(n / 2) * 2);
const round64 = (n) => Math.min(Math.max(Math.round(n / 64) * 64, 512), 1536);

// Какие компоненты нужны движку
const engineTools = (engine) => (engine === 'sd' ? ['sdcpp', 'sdxl-inpaint', 'esrgan'] : ['lama', 'esrgan']);

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

// Качественный движок: SDXL Inpainting на видеокарте, холст ≈1 Мп с сохранением пропорций
async function fillSD({ src, c, w, h, edges, work, prompt, onProgress, onSpawn }) {
  const gw = round64(Math.sqrt((SD_AREA * c.W) / c.H));
  const gh = round64(Math.sqrt((SD_AREA * c.H) / c.W));
  const grow = 8;
  const { rgb, mask, kx, ky } = await prepare(src, c, w, h, gw, gh, grow, edges, onSpawn);
  await writePNG(path.join(work, 'init.png'), rgb, gw, gh, onSpawn);
  await writePNG(path.join(work, 'mask.png'), mask, gw, gh, onSpawn, 'gray');
  const dir = tools.dirOf('sdcpp');
  const rel = (p) => path.relative(dir, p);
  const steps = 30;
  const text = String(prompt || '').trim();
  try {
    await tools.run(
      tools.pathOf('sdcpp'),
      [
        '-m', rel(tools.pathOf('sdxl-inpaint')),
        '-p', text ? `${text}, ${SD_PROMPT}` : SD_PROMPT,
        '-n', SD_NEGATIVE,
        '-i', rel(path.join(work, 'init.png')),
        '--mask', rel(path.join(work, 'mask.png')),
        '-W', String(gw), '-H', String(gh),
        '--strength', '1', '--steps', String(steps), '--cfg-scale', '6',
        '-s', String(crypto.randomInt(1, 2 ** 31)),
        '--vae-tiling',
        '-o', rel(path.join(work, 'fill.png')),
      ],
      dir,
      (s) => {
        const m = [...s.matchAll(/\|\s*(\d+)\/(\d+)\s*-/g)].pop();
        if (m && +m[2] === steps) onProgress(Math.min(+m[1] / steps, 1));
      },
      onSpawn,
    );
  } catch (e) {
    if (/memory|alloc|vulkan|device|gpu/i.test(e.message)) {
      throw new Error('Не хватает видеокарты для качественной дорисовки (нужна видеокарта с Vulkan и 6+ ГБ памяти). Выберите быструю модель.');
    }
    throw e;
  }
  if (!fs.existsSync(path.join(work, 'fill.png'))) throw new Error('Нейросеть не смогла дорисовать фото');
  return { size: Math.max(gw, gh), feather: Math.ceil((grow + 1) / Math.min(kx, ky)) };
}

// src.png (w×h) → out.png с дорисованными краями. Возвращает false, если расширять нечего.
async function outpaint({ src, out, w, h, mode, engine, prompt, work, onProgress, onStage, onSpawn }) {
  const c = canvasFor(w, h, mode);
  if (!c) return false;
  const { W, H, L, T } = c;
  const edges = { left: L > 0, right: L + w < W, top: T > 0, bottom: T + h < H };

  // 1. Нейросеть рисует уменьшенный холст
  onStage(engine === 'sd' ? 'Генерирую края…' : 'Дорисовываю края…');
  onProgress(0.05);
  const args = { src, c, w, h, edges, work, prompt, onSpawn, onProgress: (p) => onProgress(0.05 + p * 0.6) };
  const { size, feather } = engine === 'sd' ? await fillSD(args) : await fillLama(args);
  onProgress(0.65);

  // 2. Увеличиваем дорисованное, чтобы новые поля не были мыльными
  let fillFile = 'fill.png';
  if (tools.isInstalled('esrgan') && Math.max(W, H) > size * 1.5) {
    onStage('Повышаю чёткость краёв…');
    try {
      await tools.run(
        tools.pathOf('esrgan'),
        ['-i', path.relative(tools.dirOf('esrgan'), path.join(work, 'fill.png')), '-o', path.relative(tools.dirOf('esrgan'), path.join(work, 'fill4.png')), '-n', 'realesrgan-x4plus', '-s', '4'],
        tools.dirOf('esrgan'),
        null,
        onSpawn,
      );
      fillFile = 'fill4.png';
    } catch {
      // без Vulkan обходимся обычным увеличением
    }
  }
  onProgress(0.8);

  // 3. Собираем: дорисованный холст, поверх — исходное фото с мягким переходом на границах
  onStage('Собираю фото…');
  const canvas = await readRGB(path.join(work, fillFile), W, H, onSpawn);
  const orig = await readRGB(src, w, h, onSpawn);
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
