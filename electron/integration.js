// Пункт «Конвертировать в…» в контекстном меню Проводника (только для текущего пользователя, HKCU)
const { execFile } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { EXT } = require('../lib/formats');

const MENU = {
  audio: [['wav', 'WAV'], ['mp3', 'MP3'], ['flac', 'FLAC'], ['aiff', 'AIFF'], ['aac', 'AAC (.m4a)']],
  video: [['mp4', 'MP4'], ['gif', 'GIF'], ['webm', 'WebM'], ['wav', 'WAV (только звук)'], ['mp3', 'MP3 (только звук)']],
  image: [['png', 'PNG'], ['jpg', 'JPG'], ['webp', 'WebP'], ['ico', 'ICO (иконка)']],
};

const ROOT = 'HKEY_CURRENT_USER\\Software\\Classes\\SystemFileAssociations';
const esc = (s) => s.replace(/\\/g, '\\\\').replace(/"/g, '\\"');

// command — уже заключённый в кавычки путь к программе, например "C:\...\Mediavert.exe"
function buildReg(enable, command, icon) {
  const lines = ['Windows Registry Editor Version 5.00', ''];
  for (const kind of Object.keys(EXT)) {
    for (const ext of EXT[kind]) {
      const key = `${ROOT}\\.${ext}\\shell\\Mediavert`;
      // сначала удаляем старую версию (в том числе со старым названием MediaShift)
      lines.push(`[-${key}]`, '', `[-${ROOT}\\.${ext}\\shell\\MediaShift]`, '');
      if (!enable) continue;
      lines.push(`[${key}]`, '"MUIVerb"="Конвертировать в…"', `"Icon"="${esc(icon)}"`, '"SubCommands"=""', '"MultiSelectModel"="Player"', '');
      MENU[kind].forEach(([fmt, label], i) => {
        const sub = `${key}\\shell\\${String(i).padStart(2, '0')}_${fmt}`;
        lines.push(`[${sub}]`, `"MUIVerb"="${label}"`, '"MultiSelectModel"="Player"', '');
        lines.push(`[${sub}\\command]`, `@="${esc(`${command} --convert ${fmt} "%1"`)}"`, '');
      });
    }
  }
  return lines.join('\r\n');
}

function setContextMenu(enable, command, icon) {
  if (process.platform !== 'win32') return Promise.resolve();
  const file = path.join(os.tmpdir(), `mediavert-${process.pid}-${Date.now()}.reg`);
  fs.writeFileSync(file, Buffer.from('\ufeff' + buildReg(enable, command, icon), 'utf16le'));
  return new Promise((resolve, reject) => {
    execFile('reg.exe', ['import', file], { windowsHide: true }, (err) => {
      fs.rm(file, { force: true }, () => {});
      if (err) reject(new Error('Не удалось изменить контекстное меню'));
      else resolve();
    });
  });
}

module.exports = { setContextMenu, buildReg, MENU };
