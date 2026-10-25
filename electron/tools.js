// Дополнительные компоненты, которые скачиваются один раз по запросу:
// распознавание речи (whisper.cpp + модель), ИИ-увеличение фото (Real-ESRGAN) и дорисовка краёв (LaMa).
// Источники — официальные релизы; каждый файл проверяется по SHA-256.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { spawn } = require('child_process');
const { app } = require('electron');

const TOOLS = {
  whisper: {
    title: 'программа распознавания речи',
    what: 'программу распознавания речи',
    url: 'https://github.com/ggml-org/whisper.cpp/releases/download/b5454/whisper-bin-x64.zip',
    sha256: '6ba69e3482d7826214f90a6a9c84ca07782aec1e1d0c6a7c30c994fd5d816ccb',
    size: 8928640,
    zip: true,
    entry: 'Release/whisper-cli.exe',
  },
  'whisper-base': {
    title: 'быстрая модель распознавания',
    what: 'быструю модель распознавания',
    url: 'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin',
    sha256: '60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe',
    size: 147_951_465,
    entry: 'ggml-base.bin',
  },
  'whisper-small': {
    title: 'точная модель распознавания',
    what: 'точную модель распознавания',
    url: 'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin',
    sha256: '1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b',
    size: 487_601_967,
    entry: 'ggml-small.bin',
  },
  esrgan: {
    title: 'нейросеть улучшения фото',
    what: 'нейросеть улучшения фото',
    url: 'https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip',
    sha256: 'abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d',
    size: 45_474_481,
    zip: true,
    entry: 'realesrgan-ncnn-vulkan.exe',
  },
  lama: {
    title: 'нейросеть дорисовки фото',
    what: 'нейросеть дорисовки фото',
    url: 'https://huggingface.co/Carve/LaMa-ONNX/resolve/c3c0c9e468934d62e79c329e35d82dd09ff8c444/lama_fp32.onnx',
    sha256: '1faef5301d78db7dda502fe59966957ec4b79dd64e16f03ed96913c7a4eb68d6',
    size: 208_044_816,
    entry: 'lama_fp32.onnx',
  },
};

const baseDir = () => path.join(app.getPath('userData'), 'tools');
const dirOf = (name) => path.join(baseDir(), name);
const isInstalled = (name) => fs.existsSync(path.join(dirOf(name), '.ok'));
const pathOf = (name) => path.join(dirOf(name), TOOLS[name].entry);

function status(names) {
  return Object.fromEntries(names.filter((n) => TOOLS[n]).map((n) => [n, { installed: isInstalled(n), size: TOOLS[n].size, title: TOOLS[n].title }]));
}

// Работают в папке компонента с относительными именами: так не мешают русские буквы в путях
function run(exe, args, cwd, onLine, onSpawn) {
  return new Promise((resolve, reject) => {
    const proc = spawn(exe, args, { cwd, windowsHide: true });
    onSpawn?.(proc);
    let tail = '';
    const feed = (d) => {
      const s = String(d);
      tail = (tail + s).slice(-4000);
      onLine?.(s);
    };
    proc.stdout.on('data', feed);
    proc.stderr.on('data', feed);
    proc.on('error', reject);
    proc.on('close', (code) => (code === 0 ? resolve(proc) : reject(new Error(tail.trim().split('\n').slice(-2).join(' ') || `код ${code}`))));
  });
}

const pending = new Map();

// Скачать, проверить и распаковать (один раз; параллельные запросы ждут одну загрузку)
function install(name, onProgress = () => {}) {
  if (isInstalled(name)) return Promise.resolve(pathOf(name));
  if (pending.has(name)) return pending.get(name);
  const t = TOOLS[name];
  const job = (async () => {
    const dir = dirOf(name);
    fs.rmSync(dir, { recursive: true, force: true });
    fs.mkdirSync(dir, { recursive: true });
    const tmpName = t.zip ? 'download.zip' : `${t.entry}.part`;
    const tmp = path.join(dir, tmpName);
    const res = await fetch(t.url, { redirect: 'follow' });
    if (!res.ok || !res.body) throw new Error(`Не удалось скачать ${t.title} (${res.status})`);
    const hash = crypto.createHash('sha256');
    const out = fs.createWriteStream(tmp);
    let got = 0;
    for await (const chunk of res.body) {
      hash.update(chunk);
      got += chunk.length;
      if (!out.write(chunk)) await new Promise((r) => out.once('drain', r));
      onProgress(Math.min(got / t.size, 1));
    }
    await new Promise((r, j) => out.end((e) => (e ? j(e) : r())));
    if (hash.digest('hex') !== t.sha256) {
      fs.rmSync(dir, { recursive: true, force: true });
      throw new Error(`Файл «${t.title}» скачался повреждённым — попробуйте ещё раз`);
    }
    if (t.zip) {
      // встроенный в Windows 10/11 tar умеет zip; берём его по полному пути, а не первый попавшийся в PATH
      const tar = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'tar.exe');
      await run(tar, ['-xf', tmpName], dir);
      fs.rmSync(tmp, { force: true });
    } else {
      fs.renameSync(tmp, path.join(dir, t.entry));
    }
    if (!fs.existsSync(pathOf(name))) throw new Error(`Не удалось распаковать ${t.title}`);
    fs.writeFileSync(path.join(dir, '.ok'), t.sha256);
    return pathOf(name);
  })().finally(() => pending.delete(name));
  pending.set(name, job);
  return job;
}

module.exports = { TOOLS, status, install, isInstalled, pathOf, dirOf, run };
