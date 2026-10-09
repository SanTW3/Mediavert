// Генерирует assets/icon.png (рисуется кодом, без внешних картинок)
// и build/installer.nsh (очистка реестра при удалении программы)
const fs = require('fs');
const path = require('path');
const zlib = require('zlib');
const { EXT } = require('../lib/formats');

const ROOT = path.join(__dirname, '..');

/* ---------- PNG ---------- */

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
const crc32 = (buf) => {
  let c = 0xffffffff;
  for (const b of buf) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
};

function encodePng(w, h, rgba) {
  const raw = Buffer.alloc((w * 4 + 1) * h);
  for (let y = 0; y < h; y++) rgba.copy(raw, y * (w * 4 + 1) + 1, y * w * 4, (y + 1) * w * 4);
  const chunk = (type, data) => {
    const len = Buffer.alloc(4);
    len.writeUInt32BE(data.length);
    const td = Buffer.concat([Buffer.from(type), data]);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(td));
    return Buffer.concat([len, td, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0);
  ihdr.writeUInt32BE(h, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // RGBA
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk('IHDR', ihdr),
    chunk('IDAT', zlib.deflateSync(raw, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

/* ---------- рисунок: фиолетовый квадрат со звуковой волной ---------- */

const WAVE = [[5.5, 16], [9, 16], [11, 9.5], [14, 22.5], [17, 12.5], [19, 18.5], [21, 16], [26.5, 16]];
const clamp = (v, a, b) => Math.min(Math.max(v, a), b);

function distToSegment(px, py, [ax, ay], [bx, by]) {
  const dx = bx - ax, dy = by - ay;
  const t = clamp(((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy), 0, 1);
  return Math.hypot(px - ax - t * dx, py - ay - t * dy);
}

function roundRectSd(px, py, half, r) {
  const qx = Math.abs(px - 16) - (half - r);
  const qy = Math.abs(py - 16) - (half - r);
  return Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) + Math.min(Math.max(qx, qy), 0) - r;
}

function drawIcon(size) {
  const px = size / 32;
  const buf = Buffer.alloc(size * size * 4);
  const top = [0x93, 0x76, 0xff];
  const bottom = [0x56, 0x36, 0xe8];
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const u = (x + 0.5) / px;
      const v = (y + 0.5) / px;
      const alpha = clamp(0.5 - roundRectSd(u, v, 15.5, 7) * px, 0, 1);
      const t = clamp((u + v) / 64, 0, 1);
      let col = top.map((c, i) => c + (bottom[i] - c) * t);
      let d = Infinity;
      for (let i = 0; i < WAVE.length - 1; i++) d = Math.min(d, distToSegment(u, v, WAVE[i], WAVE[i + 1]));
      const stroke = clamp(0.5 + (1.25 - d) * px, 0, 1);
      col = col.map((c) => c + (255 - c) * stroke);
      const o = (y * size + x) * 4;
      buf[o] = col[0];
      buf[o + 1] = col[1];
      buf[o + 2] = col[2];
      buf[o + 3] = Math.round(alpha * 255);
    }
  }
  return encodePng(size, size, buf);
}

fs.mkdirSync(path.join(ROOT, 'assets'), { recursive: true });
fs.writeFileSync(path.join(ROOT, 'assets', 'icon.png'), drawIcon(512));

/* ---------- NSIS: очистка при удалении ---------- */

const keys = Object.values(EXT)
  .flat()
  .flatMap((ext) => ['Mediavert', 'MediaShift'].map((name) => `    DeleteRegKey HKCU "Software\\Classes\\SystemFileAssociations\\.${ext}\\shell\\${name}"`));
const nsh = `; сгенерировано scripts/make-assets.js
!macro customUnInstall
  \${ifNot} \${isUpdated}
${keys.join('\n')}
    DeleteRegValue HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Run" "Mediavert"
    DeleteRegValue HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Run" "MediaShift"
  \${endIf}
!macroend
`;
fs.mkdirSync(path.join(ROOT, 'build'), { recursive: true });
fs.writeFileSync(path.join(ROOT, 'build', 'installer.nsh'), nsh);

console.log('assets/icon.png и build/installer.nsh готовы');
