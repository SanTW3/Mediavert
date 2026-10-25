// Дорисовка краёв фото до нужных пропорций нейросетью LaMa.
// Нейросеть работает с картинкой 512×512: холст сжимается до этого размера, новые поля дорисовываются,
// результат увеличивается (Real-ESRGAN, если видеокарта позволяет) и под ним остаётся исходное фото в полном качестве.
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');
const { Worker } = require('worker_threads');
const tools = require('./tools');
const { ffmpegPath } = require('../lib/convert');

const S = 512;
const RATIOS = { '16:9': 16 / 9, '9:16': 9 / 16, '4:3': 4 / 3, '3:4': 3 / 4, '4:5': 4 / 5, '1:1': 1 };
const MAX_GROW = 3.2; // сильнее нейросеть уже не продолжает фото, а выдумывает

const unpacked = (p) => p.replace('app.asar', 'app.asar.unpacked');
const ortPath = unpacked(path.join(__dirname, '..', 'node_modules', 'onnxruntime-web', 'dist', 'ort.node.min.mjs'));
const workerPath = unpacked(path.join(__dirname, 'lama-worker.js'));

const even = (n) => Math.max(2, Math.round(n / 2) * 2);

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

async function writePNG(file, rgb, w, h, onSpawn) {
  const raw = `${file}.rgb`;
  fs.writeFileSync(raw, rgb);
  await ffmpeg(['-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', `${w}x${h}`, '-i', raw, '-frames:v', '1', file], onSpawn);
  fs.rmSync(raw, { force: true });
}

function inpaint(image, mask, onSpawn) {
  return new Promise((resolve, reject) => {
    const worker = new Worker(workerPath, {
      workerData: { ortPath, modelPath: tools.pathOf('lama'), threads: Math.max(2, Math.min(os.cpus().length, 8)), size: S, image, mask },
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

// src.png (w×h) → out.png с дорисованными краями. Возвращает false, если расширять нечего.
async function outpaint({ src, out, w, h, mode, work, onProgress, onStage, onSpawn }) {
  const c = canvasFor(w, h, mode);
  if (!c) return false;
  const { W, H, L, T } = c;
  const edges = { left: L > 0, right: L + w < W, top: T > 0, bottom: T + h < H };

  // 1. Уменьшенная копия на холсте 512×512 и маска новых полей
  const kx = S / W;
  const ky = S / H;
  const l = Math.round(L * kx);
  const t = Math.round(T * ky);
  const sw = Math.min(Math.max(1, Math.round(w * kx)), S - l);
  const sh = Math.min(Math.max(1, Math.round(h * ky)), S - t);
  const small = await readRGB(src, sw, sh, onSpawn);
  const image = new Float32Array(3 * S * S);
  const mask = new Float32Array(S * S).fill(1);
  const grow = 2; // маску чуть заводим на фото, чтобы шов на границе тоже перерисовался
  for (let y = 0; y < sh; y++) {
    for (let x = 0; x < sw; x++) {
      const i = (t + y) * S + l + x;
      const j = (y * sw + x) * 3;
      image[i] = small[j] / 255;
      image[S * S + i] = small[j + 1] / 255;
      image[2 * S * S + i] = small[j + 2] / 255;
      const nearEdge = (edges.left && x < grow) || (edges.right && x >= sw - grow) || (edges.top && y < grow) || (edges.bottom && y >= sh - grow);
      if (!nearEdge) mask[i] = 0;
    }
  }
  for (let i = 0; i < S * S; i++) {
    if (mask[i]) image[i] = image[S * S + i] = image[2 * S * S + i] = 0;
  }

  // 2. Нейросеть
  onStage('Дорисовываю края…');
  onProgress(0.1);
  const filled = await inpaint(image, mask, onSpawn);
  const rgb = Buffer.alloc(S * S * 3);
  for (let i = 0; i < S * S; i++) {
    rgb[i * 3] = filled[i];
    rgb[i * 3 + 1] = filled[S * S + i];
    rgb[i * 3 + 2] = filled[2 * S * S + i];
  }
  await writePNG(path.join(work, 'fill.png'), rgb, S, S, onSpawn);
  onProgress(0.6);

  // 3. Увеличиваем дорисованное, чтобы новые поля не были мыльными
  let fillFile = 'fill.png';
  if (tools.isInstalled('esrgan') && Math.max(W, H) > S * 1.5) {
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

  // 4. Собираем: дорисованный холст, поверх — исходное фото с мягким переходом на границах
  onStage('Собираю фото…');
  const canvas = await readRGB(path.join(work, fillFile), W, H, onSpawn);
  const orig = await readRGB(src, w, h, onSpawn);
  const feather = Math.max(2, Math.ceil((grow + 1) / Math.min(kx, ky)));
  for (let y = 0; y < h; y++) {
    const dy = Math.min(edges.top ? y : Infinity, edges.bottom ? h - 1 - y : Infinity);
    for (let x = 0; x < w; x++) {
      const d = Math.min(dy, edges.left ? x : Infinity, edges.right ? w - 1 - x : Infinity);
      const a = d >= feather ? 1 : (d + 0.5) / feather;
      const j = (y * w + x) * 3;
      const i = ((T + y) * W + L + x) * 3;
      for (let k = 0; k < 3; k++) canvas[i + k] = Math.round(orig[j + k] * a + canvas[i + k] * (1 - a));
    }
  }
  await writePNG(out, canvas, W, H, onSpawn);
  onProgress(0.9);
  return true;
}

module.exports = { outpaint, canvasFor };
