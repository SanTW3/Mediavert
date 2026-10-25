// Обработка с нейросетями: распознавание речи (whisper.cpp), улучшение/увеличение фото (Real-ESRGAN)
// и дорисовка краёв (LaMa, electron/outpaint.js).
// Остальные задачи — обычная конвертация через ffmpeg.
const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const tools = require('./tools');
const { kindOf } = require('../lib/formats');
const { convert, probe } = require('../lib/convert');
const { outpaint } = require('./outpaint');

const LANGS = ['auto', 'ru', 'en', 'uk', 'de', 'es', 'fr', 'it', 'pt', 'pl', 'tr', 'ja', 'zh', 'ko'];
const MAX_AI_PIXELS = 2_500_000; // большие фото перед ИИ уменьшаем: нейросеть всё равно увеличит в 4 раза

// Какие компоненты нужны задаче
function neededTools(format, o = {}) {
  const kind = kindOf(format);
  if (kind === 'text') return ['whisper', o.speechModel === 'small' ? 'whisper-small' : 'whisper-base'];
  if (kind !== 'image') return [];
  const names = [];
  if (o.outpaint) names.push('lama');
  if (o.enhance === 'ai' || o.outpaint) names.push('esrgan');
  return names;
}

async function ensure(names, allowDownload, onStage) {
  for (const n of names) {
    if (tools.isInstalled(n)) continue;
    if (!allowDownload) throw new Error('Нужен дополнительный компонент — запустите эту задачу из главного окна, там можно его скачать');
    await tools.install(n, (p) => onStage(`Скачиваю ${tools.TOOLS[n].what}… ${Math.round(p * 100)}%`));
  }
}

// Рабочая папка с латинским именем внутри папки компонента
function workDir(tool) {
  const dir = path.join(tools.dirOf(tool), 'work', crypto.randomUUID().slice(0, 8));
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

async function transcribe({ input, output, format, options: o, onProgress, onStage, onSpawn }) {
  const model = o.speechModel === 'small' ? 'whisper-small' : 'whisper-base';
  await ensure(['whisper', model], o.allowDownload, onStage);
  const work = workDir('whisper');
  try {
    onStage('Готовлю звук…');
    // whisper ждёт 16 кГц моно
    await convert({
      input, output: path.join(work, 'in.wav'), format: 'wav',
      options: { sampleRate: '16000', channels: '1', bitDepth: '16', start: o.start, end: o.end },
      onProgress: (p) => onProgress(p * 0.08), onSpawn,
    });
    onStage('Распознаю речь…');
    const lang = LANGS.includes(o.speechLang) ? o.speechLang : 'auto';
    const threads = Math.max(2, Math.min(os.cpus().length, 8));
    const modelRel = path.relative(work, tools.pathOf(model));
    await tools.run(
      tools.pathOf('whisper'),
      ['-m', modelRel, '-f', 'in.wav', '-l', lang, '-t', String(threads), format === 'srt' ? '-osrt' : '-otxt', '-of', 'out', '-pp', '-np'],
      work,
      (s) => {
        const m = [...s.matchAll(/progress\s*=\s*(\d+)%/g)].pop();
        if (m) onProgress(Math.min(0.08 + (+m[1] / 100) * 0.9, 0.99));
      },
      onSpawn,
    );
    fs.copyFileSync(path.join(work, `out.${format}`), output);
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
  }
}

async function enhanceImage({ input, output, format, options: o, onProgress, onStage, onSpawn }) {
  await ensure(['esrgan'], o.allowDownload, onStage);
  const info = await probe(input);
  if (!info.width) throw new Error('Не удалось прочитать изображение');
  const work = workDir('esrgan');
  try {
    onStage('Готовлю фото…');
    const px = info.width * info.height;
    const k = px > MAX_AI_PIXELS ? Math.sqrt(MAX_AI_PIXELS / px) : 1;
    await convert({
      input, output: path.join(work, 'in.png'), format: 'png',
      options: { start: o.start, width: k < 1 ? String(Math.max(16, Math.round((info.width * k) / 2) * 2)) : '' },
      onSpawn,
    });
    onStage('Улучшаю с помощью ИИ…');
    try {
      await tools.run(
        tools.pathOf('esrgan'),
        ['-i', 'in.png', '-o', 'up.png', '-n', 'realesrgan-x4plus', '-s', '4', '-m', path.relative(work, path.join(tools.dirOf('esrgan'), 'models'))],
        work,
        (s) => {
          const m = [...s.matchAll(/(\d+(?:[.,]\d+)?)%/g)].pop();
          if (m) onProgress(Math.min(0.05 + (parseFloat(m[1].replace(',', '.')) / 100) * 0.85, 0.95));
        },
        onSpawn,
      );
    } catch (e) {
      if (/vulkan|gpu|device/i.test(e.message)) throw new Error('Видеокарта не поддерживает ИИ-улучшение (нужна поддержка Vulkan). Выберите «Чётче и чище».');
      throw e;
    }
    onStage('Сохраняю…');
    // итоговый размер: заданная ширина, иначе исходная × «Увеличить»
    const up = [2, 4].includes(Number(o.upscale)) ? Number(o.upscale) : 1;
    const width = Number(o.width) >= 8 ? Number(o.width) : info.width * up;
    await convert({
      input: path.join(work, 'up.png'), output, format,
      options: { ...o, width: String(Math.round(width / 2) * 2), upscale: '', enhance: '', start: '', end: '' },
      onSpawn,
    });
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
  }
}

// Сначала дорисовываем края, потом обычная обработка фото уже с расширенного холста
async function outpaintImage(args) {
  const { input, options: o, onProgress, onStage, onSpawn } = args;
  await ensure(neededTools(args.format, o), o.allowDownload, onStage);
  const work = workDir('lama');
  try {
    onStage('Готовлю фото…');
    const src = path.join(work, 'src.png');
    await convert({ input, output: src, format: 'png', options: { start: o.start }, onSpawn });
    const info = await probe(src);
    if (!info.width) throw new Error('Не удалось прочитать изображение');
    const wide = path.join(work, 'wide.png');
    const grown = await outpaint({ src, out: wide, w: info.width, h: info.height, mode: o.outpaint, work, onProgress: (p) => onProgress(p * 0.8), onStage, onSpawn });
    const next = { ...args, input: grown ? wide : src, options: { ...o, outpaint: '', start: '', end: '' } };
    if (o.enhance === 'ai') {
      return await enhanceImage({ ...next, onProgress: (p) => onProgress(0.8 + p * 0.2) });
    }
    onStage('Сохраняю…');
    return await convert(next);
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
  }
}

// Единая точка запуска задачи
function runJob(args) {
  const kind = kindOf(args.format);
  if (kind === 'text') return transcribe(args);
  if (kind === 'image' && args.options.outpaint) return outpaintImage(args);
  if (kind === 'image' && args.options.enhance === 'ai') return enhanceImage(args);
  return convert(args);
}

module.exports = { runJob, neededTools };
