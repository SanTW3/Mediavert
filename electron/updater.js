// Автообновление из GitHub Releases (electron-updater).
// Проверяет при запуске и раз в 4 часа, скачивает в фоне, ставит при перезапуске или выходе.
const { app } = require('electron');
const path = require('path');
const fs = require('fs');

const CHECK_EVERY_MS = 4 * 60 * 60 * 1000;
const PLACEHOLDER_OWNER = 'YOUR_GITHUB_NAME';

let autoUpdater = null;
let status = { state: 'idle' };
let onStatus = () => {};
let timer = null;

// Адрес репозитория сборщик кладёт в resources/app-update.yml (в упакованном package.json его нет)
function publishConfig() {
  try {
    const text = fs.readFileSync(path.join(process.resourcesPath, 'app-update.yml'), 'utf8');
    const get = (key) => (new RegExp(`^${key}:\\s*(.+)$`, 'm').exec(text) || [])[1]?.trim().replace(/^['"]|['"]$/g, '');
    return get('provider') === 'github' ? { owner: get('owner'), repo: get('repo') } : null;
  } catch {
    return null;
  }
}

// Обновления работают только в установленной версии и при настроенном репозитории
// MEDIAVERT_UPDATE_URL — для проверки обновлений с локального сервера вместо GitHub
const testFeed = process.env.MEDIAVERT_UPDATE_URL;

function isConfigured() {
  if (!app.isPackaged || process.env.PORTABLE_EXECUTABLE_FILE) return false;
  if (testFeed) return true;
  const pub = publishConfig();
  return !!(pub && pub.owner && pub.owner !== PLACEHOLDER_OWNER);
}

function setStatus(next) {
  status = { ...status, ...next };
  onStatus(status);
}

function init(listener) {
  onStatus = listener;
  if (!isConfigured()) {
    status = { state: 'unavailable', reason: app.isPackaged ? 'not-configured' : 'dev' };
    return;
  }
  ({ autoUpdater } = require('electron-updater'));
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.allowPrerelease = false;
  autoUpdater.logger = null;
  if (testFeed) autoUpdater.setFeedURL({ provider: 'generic', url: testFeed });

  autoUpdater.on('checking-for-update', () => setStatus({ state: 'checking', error: null }));
  autoUpdater.on('update-not-available', () => setStatus({ state: 'latest', checkedAt: Date.now() }));
  autoUpdater.on('update-available', (info) => setStatus({ state: 'downloading', version: info.version, percent: 0 }));
  autoUpdater.on('download-progress', (p) => setStatus({ state: 'downloading', percent: Math.round(p.percent) }));
  autoUpdater.on('update-downloaded', (info) => setStatus({ state: 'ready', version: info.version, notes: plainNotes(info.releaseNotes) }));
  autoUpdater.on('error', (err) => {
    const message = String(err?.message || err);
    if (status.state === 'ready') return;
    // релизов ещё нет — значит, обновляться не на что
    if (/No published versions|Unable to find latest version|HttpError: 404|latest\.yml.*404/i.test(message)) {
      setStatus({ state: 'latest', checkedAt: Date.now(), error: null });
      return;
    }
    // нет интернета / GitHub недоступен — тихо пробуем позже
    setStatus({ state: 'error', error: message.split('\n')[0] });
  });
}

function plainNotes(notes) {
  const text = Array.isArray(notes) ? notes.map((n) => n.note).join('\n') : String(notes || '');
  return text.replace(/<[^>]+>/g, '').trim().slice(0, 600);
}

async function check() {
  if (!autoUpdater) return status;
  if (status.state === 'downloading' || status.state === 'ready') return status;
  try {
    await autoUpdater.checkForUpdates();
  } catch {
    // ошибка уже пришла в событии 'error'
  }
  return status;
}

function startSchedule(enabled) {
  clearInterval(timer);
  timer = null;
  if (!autoUpdater || !enabled) return;
  setTimeout(check, 15 * 1000); // не мешаем запуску
  timer = setInterval(check, CHECK_EVERY_MS);
}

// Тихая установка и перезапуск приложения
function install() {
  if (!autoUpdater || status.state !== 'ready') return false;
  setImmediate(() => autoUpdater.quitAndInstall(true, true));
  return true;
}

module.exports = { init, check, install, startSchedule, getStatus: () => status, isConfigured };
