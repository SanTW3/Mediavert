const { app } = require('electron');
const fs = require('fs');
const path = require('path');

function defaults() {
  return {
    options: {
      sampleRate: '', bitDepth: '24', channels: '', bitrate: '320',
      normalize: false, lufs: '-14', removeSilence: false, fadeIn: '', fadeOut: '',
      height: '', fps: '', quality: 'medium', mute: false,
      width: '', imageQuality: '90',
    },
    defaults: { audio: 'wav', video: 'mp4', image: 'png' },
    outputMode: 'beside', // beside — рядом с исходником, folder — в outputDir
    outputDir: path.join(app.getPath('documents'), 'Mediavert'),
    shelf: true,
    contextMenu: false,
    autostart: false,
    trayHintShown: false,
    onboardingDone: false,
    theme: 'system',
    accent: '#6a4dff',
    autoUpdate: true,
  };
}

let data = null;
const file = () => path.join(app.getPath('userData'), 'settings.json');

function get() {
  if (!data) {
    const base = defaults();
    try {
      const saved = JSON.parse(fs.readFileSync(file(), 'utf8'));
      data = { ...base, ...saved, options: { ...base.options, ...saved.options }, defaults: { ...base.defaults, ...saved.defaults } };
    } catch {
      data = base;
    }
  }
  return data;
}

function set(patch) {
  const cur = get();
  // обрезка относится к конкретной сессии, её не запоминаем
  const { start, end, ...options } = patch.options || {};
  data = {
    ...cur,
    ...patch,
    options: { ...cur.options, ...options },
    defaults: { ...cur.defaults, ...(patch.defaults || {}) },
  };
  fs.mkdirSync(path.dirname(file()), { recursive: true });
  fs.writeFileSync(file(), JSON.stringify(data, null, 2));
  return data;
}

module.exports = { get, set };
