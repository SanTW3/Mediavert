const fs = require('fs');
const path = require('path');
const { EventEmitter } = require('events');
const { FORMATS, kindOf, kindOfFile, canConvert } = require('../lib/formats');
const { convert } = require('../lib/convert');

const ACCUSATIVE = { audio: 'Аудио', video: 'Видео', image: 'Картинку' };

function safeBase(file) {
  return path.basename(file, path.extname(file)).replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').trim().slice(0, 150) || 'file';
}

// Атомарно занимаем свободное имя: "name.wav", "name (1).wav", ...
function reserve(dir, base, ext) {
  fs.mkdirSync(dir, { recursive: true });
  for (let i = 0; ; i++) {
    const candidate = path.join(dir, i ? `${base} (${i}).${ext}` : `${base}.${ext}`);
    try {
      fs.writeFileSync(candidate, '', { flag: 'wx' });
      return candidate;
    } catch (e) {
      if (e.code !== 'EEXIST') throw e;
    }
  }
}

class Jobs extends EventEmitter {
  constructor(maxParallel = 2) {
    super();
    this.max = maxParallel;
    this.jobs = new Map();
    this.queue = [];
    this.running = 0;
    this.seq = 0;
  }

  add({ input, format, options, outDir, fallbackDir, source }) {
    const job = {
      id: String(++this.seq),
      input,
      name: path.basename(input),
      format,
      source,
      options: options || {},
      status: 'queued',
      progress: 0,
      created: Date.now(),
    };
    this.jobs.set(job.id, job);
    try {
      const kind = kindOf(format);
      const src = kindOfFile(input);
      if (!kind) throw new Error('Неизвестный формат');
      if (!src) throw new Error('Этот тип файла не поддерживается');
      if (!canConvert(src, kind)) throw new Error(`${ACCUSATIVE[src]} нельзя превратить в ${format.toUpperCase()}`);
      if (!fs.existsSync(input)) throw new Error('Файл не найден');
      const ext = FORMATS[kind][format];
      try {
        job.output = reserve(outDir, safeBase(input), ext);
      } catch {
        job.output = reserve(fallbackDir, safeBase(input), ext);
      }
      this.queue.push(job);
    } catch (e) {
      job.status = 'error';
      job.error = e.message;
    }
    this.emitUpdate(job);
    this.pump();
    return job.id;
  }

  pump() {
    while (this.running < this.max && this.queue.length) {
      const job = this.queue.shift();
      this.running++;
      job.status = 'converting';
      this.emitUpdate(job);
      convert({
        input: job.input,
        output: job.output,
        format: job.format,
        options: job.options,
        onProgress: (p) => {
          job.progress = p;
          this.emitUpdate(job);
        },
        onSpawn: (proc) => (job.proc = proc),
      })
        .then(() => {
          job.status = 'done';
          job.progress = 1;
          job.size = fs.statSync(job.output).size;
        })
        .catch((err) => {
          job.status = 'error';
          job.error = err.message;
          fs.rm(job.output, { force: true }, () => {});
        })
        .finally(() => {
          job.proc = null;
          this.running--;
          this.emitUpdate(job);
          this.emit('finished', this.public(job));
          this.pump();
        });
    }
  }

  public(job) {
    const { proc, options, ...rest } = job;
    return { ...rest, outName: job.output ? path.basename(job.output) : null };
  }

  emitUpdate(job) {
    this.emit('update', this.public(job));
  }

  list() {
    return [...this.jobs.values()].map((j) => this.public(j));
  }

  pending(source) {
    return [...this.jobs.values()].filter((j) => j.source === source && (j.status === 'queued' || j.status === 'converting')).length;
  }

  clear(sources) {
    for (const [id, j] of this.jobs) {
      if (sources.includes(j.source) && (j.status === 'done' || j.status === 'error')) this.jobs.delete(id);
    }
  }

  // При выходе: останавливаем ffmpeg и убираем недоделанные файлы
  killAll() {
    for (const j of this.jobs.values()) {
      if (j.status !== 'queued' && j.status !== 'converting') continue;
      if (j.proc) j.proc.kill();
      try { fs.rmSync(j.output, { force: true }); } catch {}
    }
    this.queue = [];
  }
}

module.exports = Jobs;
