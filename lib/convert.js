const { spawn } = require('child_process');
const { kindOf } = require('./formats');

// В собранном приложении ffmpeg.exe лежит вне архива asar
const ffmpegPath = require('ffmpeg-static').replace('app.asar', 'app.asar.unpacked');

const LOSSY_AUDIO = new Set(['mp3', 'aac', 'ogg', 'opus']);

// "1:23.5" / "83.5" -> секунды
function parseTime(value) {
  if (value === undefined || value === null || String(value).trim() === '') return null;
  const parts = String(value).trim().split(':').map(Number);
  if (parts.some((n) => !Number.isFinite(n) || n < 0) || parts.length > 3) {
    throw new Error(`Неверное время: "${value}"`);
  }
  return parts.reduce((acc, n) => acc * 60 + n, 0);
}

function pick(value, allowed, fallback) {
  const v = Number(value);
  return allowed.includes(v) ? v : fallback;
}

// Узнаём длительность, частоту дискретизации и наличие потоков
function probe(input) {
  return new Promise((resolve) => {
    const proc = spawn(ffmpegPath, ['-hide_banner', '-i', input], { windowsHide: true });
    let err = '';
    proc.stderr.on('data', (d) => (err += d));
    proc.on('close', () => {
      const dur = err.match(/Duration: (\d+):(\d+):(\d+(?:\.\d+)?)/);
      const sr = err.match(/Audio:.*?(\d+) Hz/);
      resolve({
        duration: dur ? +dur[1] * 3600 + +dur[2] * 60 + +dur[3] : 0,
        sampleRate: sr ? +sr[1] : 0,
        hasAudio: /Stream #.*Audio:/.test(err),
        hasVideo: /Stream #.*Video:(?!.*attached pic)/.test(err),
      });
    });
    proc.on('error', () => resolve({ duration: 0, sampleRate: 0, hasAudio: false, hasVideo: false }));
  });
}

function audioArgs(format, o, info) {
  const args = ['-vn', '-map', '0:a:0', '-map_metadata', '0'];
  const filters = [];

  const removeSilence = !!o.removeSilence;
  const fadeIn = Math.min(Math.max(Number(o.fadeIn) || 0, 0), 60);
  const fadeOut = Math.min(Math.max(Number(o.fadeOut) || 0, 0), 60);

  if (removeSilence) filters.push('silenceremove=start_periods=1:start_threshold=-60dB:start_silence=0.05');
  if (fadeIn) filters.push(`afade=t=in:d=${fadeIn}`);
  // Конец обрабатываем через разворот: так не нужно знать итоговую длительность
  if (removeSilence || fadeOut) {
    filters.push('areverse');
    if (removeSilence) filters.push('silenceremove=start_periods=1:start_threshold=-60dB:start_silence=0.05');
    if (fadeOut) filters.push(`afade=t=in:d=${fadeOut}`);
    filters.push('areverse');
  }
  if (o.normalize) {
    const lufs = pick(o.lufs, [-23, -16, -14, -11, -9, -8], -14);
    filters.push(`loudnorm=I=${lufs}:TP=-1:LRA=11`);
  }
  if (filters.length) args.push('-af', filters.join(','));

  const channels = pick(o.channels, [1, 2], 0);
  if (channels) args.push('-ac', String(channels));

  let sr = pick(o.sampleRate, [22050, 32000, 44100, 48000, 88200, 96000, 192000], 0);
  // loudnorm внутри работает на 192 кГц — возвращаем исходную частоту
  if (!sr && o.normalize) sr = info.sampleRate || 48000;
  if (format === 'opus') sr = 48000;
  else if (LOSSY_AUDIO.has(format) && sr > 48000) sr = 48000;
  if (format === 'mp3' && !sr && info.sampleRate > 48000) sr = 48000;
  if (sr) args.push('-ar', String(sr));

  const depth = ['16', '24', '32f'].includes(String(o.bitDepth)) ? String(o.bitDepth) : '24';
  const kbps = pick(o.bitrate, [96, 128, 160, 192, 256, 320], 320);

  switch (format) {
    case 'wav':
      args.push('-c:a', { 16: 'pcm_s16le', 24: 'pcm_s24le', '32f': 'pcm_f32le' }[depth]);
      break;
    case 'aiff':
      args.push('-c:a', { 16: 'pcm_s16be', 24: 'pcm_s24be', '32f': 'pcm_s32be' }[depth]);
      break;
    case 'flac':
      args.push('-c:a', 'flac', '-compression_level', '8');
      if (depth === '16') args.push('-sample_fmt', 's16');
      else args.push('-sample_fmt', 's32', '-bits_per_raw_sample', '24');
      break;
    case 'alac':
      args.push('-c:a', 'alac', '-sample_fmt', depth === '16' ? 's16p' : 's32p');
      break;
    case 'mp3':
      args.push('-c:a', 'libmp3lame', '-b:a', `${kbps}k`, '-id3v2_version', '3');
      break;
    case 'aac':
      args.push('-c:a', 'aac', '-b:a', `${Math.min(kbps, 320)}k`, '-movflags', '+faststart');
      break;
    case 'ogg':
      args.push('-c:a', 'libvorbis', '-b:a', `${kbps}k`);
      break;
    case 'opus':
      args.push('-c:a', 'libopus', '-b:a', `${Math.min(kbps, 256)}k`);
      break;
  }
  return args;
}

function videoArgs(format, o) {
  const height = pick(o.height, [2160, 1440, 1080, 720, 480, 360, 240], 0);
  const fps = pick(o.fps, [60, 50, 30, 25, 24, 15, 12, 10], 0);
  const quality = ['high', 'medium', 'low'].includes(o.quality) ? o.quality : 'medium';
  const mute = !!o.mute;

  if (format === 'gif') {
    const gifFps = fps && fps <= 30 ? fps : 15;
    const scale = height ? `scale=-2:${height}:flags=lanczos` : 'scale=480:-2:flags=lanczos';
    return [
      '-filter_complex',
      `[0:v:0]fps=${gifFps},${scale},split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4`,
      '-an', '-loop', '0',
    ];
  }

  const filters = [height ? `scale=-2:${height}:flags=lanczos` : 'scale=trunc(iw/2)*2:trunc(ih/2)*2'];
  if (fps) filters.push(`fps=${fps}`);
  const args = ['-map', '0:v:0', '-vf', filters.join(',')];
  if (!mute) args.push('-map', '0:a:0?');

  const crf = { high: 18, medium: 23, low: 28 }[quality];
  switch (format) {
    case 'mp4':
    case 'mov':
    case 'mkv':
      args.push('-c:v', 'libx264', '-preset', 'medium', '-crf', String(crf), '-pix_fmt', 'yuv420p');
      if (!mute) args.push('-c:a', 'aac', '-b:a', '192k');
      if (format !== 'mkv') args.push('-movflags', '+faststart');
      break;
    case 'prores':
      args.push('-c:v', 'prores_ks', '-profile:v', quality === 'low' ? '1' : quality === 'high' ? '3' : '2',
        '-pix_fmt', 'yuv422p10le', '-vendor', 'apl0');
      if (!mute) args.push('-c:a', 'pcm_s24le');
      break;
    case 'webm':
      args.push('-c:v', 'libvpx-vp9', '-crf', String(crf + 10), '-b:v', '0', '-row-mt', '1',
        '-deadline', 'good', '-cpu-used', '4');
      if (!mute) args.push('-c:a', 'libopus', '-b:a', '160k');
      break;
  }
  if (mute) args.push('-an');
  return args;
}

function imageArgs(format, o) {
  const width = Math.round(Number(o.width) || 0);
  const quality = Math.min(Math.max(Math.round(Number(o.imageQuality) || 90), 1), 100);
  const filters = [];

  if (format === 'ico') {
    filters.push('scale=256:256:force_original_aspect_ratio=decrease');
  } else if (width >= 8 && width <= 16384) {
    filters.push(`scale=${width}:-2:flags=lanczos`);
  }

  const args = ['-map', '0:v:0', '-frames:v', '1', '-update', '1'];
  if (filters.length) args.push('-vf', filters.join(','));

  switch (format) {
    case 'png': args.push('-c:v', 'png', '-pix_fmt', 'rgba'); break;
    case 'jpg': args.push('-c:v', 'mjpeg', '-q:v', String(Math.round(31 - (quality / 100) * 29))); break;
    case 'webp': args.push('-c:v', 'libwebp', '-quality', String(quality)); break;
    case 'bmp': args.push('-c:v', 'bmp'); break;
    case 'tiff': args.push('-c:v', 'tiff', '-compression_algo', 'lzw'); break;
    case 'ico': args.push('-c:v', 'png', '-pix_fmt', 'rgba'); break;
  }
  return args;
}

function buildArgs(input, output, format, o, info) {
  const kind = kindOf(format);
  if (!kind) throw new Error(`Неизвестный формат: ${format}`);

  const start = parseTime(o.start);
  const end = parseTime(o.end);
  if (start !== null && end !== null && end <= start) throw new Error('Конец обрезки должен быть позже начала');

  const args = ['-hide_banner', '-nostdin', '-y'];
  if (start) args.push('-ss', String(start));
  args.push('-i', input);
  if (end !== null) args.push('-t', String(end - (start || 0)));

  if (kind === 'audio') args.push(...audioArgs(format, o, info));
  else if (kind === 'video') args.push(...videoArgs(format, o));
  else args.push(...imageArgs(format, o));

  args.push('-progress', 'pipe:1', '-nostats', output);
  return args;
}

function expectedDuration(o, info) {
  const start = parseTime(o.start) || 0;
  const end = parseTime(o.end);
  return Math.max((end !== null ? Math.min(end, info.duration || end) : info.duration) - start, 0);
}

// Запуск конвертации; onProgress получает число 0..1
async function convert({ input, output, format, options, onProgress = () => {}, onSpawn = () => {} }) {
  const info = await probe(input);
  const kind = kindOf(format);
  if (kind === 'audio' && !info.hasAudio) throw new Error('В файле нет звуковой дорожки');
  if ((kind === 'video' || kind === 'image') && !info.hasVideo) throw new Error('В файле нет изображения/видео');

  const args = buildArgs(input, output, format, options, info);
  const total = expectedDuration(options, info);

  return new Promise((resolve, reject) => {
    const proc = spawn(ffmpegPath, args, { windowsHide: true });
    onSpawn(proc);
    let log = '';
    let buf = '';
    proc.stdout.on('data', (d) => {
      buf += d;
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        const m = line.match(/^out_time_us=(\d+)/);
        if (m && total > 0) onProgress(Math.min(+m[1] / 1e6 / total, 0.99));
      }
    });
    proc.stderr.on('data', (d) => {
      log += d;
      if (log.length > 20000) log = log.slice(-10000);
    });
    proc.on('error', reject);
    proc.on('close', (code) => {
      if (code === 0) return resolve();
      const lines = log.trim().split('\n').filter((l) => !/^\s/.test(l));
      reject(new Error(lines.slice(-2).join(' ').trim() || `ffmpeg завершился с кодом ${code}`));
    });
  });
}

module.exports = { convert };
