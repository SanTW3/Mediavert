// Публикация собранной версии в GitHub Releases.
// Порядок: черновик релиза → загрузка файлов (с повторами) → публикация.
// Пока файлы грузятся, релиз — черновик: пользователи и автообновление его не видят.
const fs = require('fs');
const path = require('path');
const pkg = require('../package.json');

const API = process.env.GITHUB_API || 'https://api.github.com'; // переопределяется только для тестов
const token = process.env.GH_TOKEN;
const { owner, repo } = [].concat(pkg.build.publish)[0];
const version = pkg.version;
const tag = `v${version}`;
const dist = process.env.RELEASE_DIST || path.join(__dirname, '..', 'dist'); // RELEASE_DIST — для тестов
const installer = pkg.build.nsis.artifactName.replace('${version}', version).replace('${ext}', 'exe');
const FILES = [installer, `${installer}.blockmap`, 'latest.yml'];

const headers = {
  Authorization: `Bearer ${token}`,
  Accept: 'application/vnd.github+json',
  'X-GitHub-Api-Version': '2022-11-28',
  'User-Agent': 'mediavert-release',
};

async function gh(method, url, body) {
  const res = await fetch(url.startsWith('http') ? url : `${API}${url}`, {
    method,
    headers: { ...headers, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(`${method} ${url}: ${res.status} ${data.message || ''}`.trim());
    err.status = res.status;
    throw err;
  }
  return data;
}

function explain(err) {
  if (err.status === 401) return 'Токен не подходит (401). Проверьте, что GH_TOKEN задан правильно и не истёк.';
  if (err.status === 403) return 'Нет прав (403). У токена должно быть Contents: Read and write для репозитория ' + `${owner}/${repo}.`;
  if (err.status === 404) return `Репозиторий ${owner}/${repo} не найден или токен не имеет к нему доступа (404).`;
  return err.message;
}

async function findRelease() {
  // черновики по тегу не находятся, поэтому смотрим список
  const list = await gh('GET', `/repos/${owner}/${repo}/releases?per_page=30`);
  return list.find((r) => r.tag_name === tag) || null;
}

// Потоковая загрузка файла с процентами и без таймаутов (большой установщик может грузиться долго)
function sendFile(url, file, size, onProgress) {
  const u = new URL(url);
  const lib = u.protocol === 'https:' ? require('https') : require('http');
  return new Promise((resolve, reject) => {
    const req = lib.request(u, {
      method: 'POST',
      headers: { ...headers, 'Content-Type': 'application/octet-stream', 'Content-Length': size },
    });
    req.on('response', (res) => {
      let text = '';
      res.on('data', (c) => (text += c));
      res.on('end', () => {
        let message = '';
        try { message = JSON.parse(text).message || ''; } catch {}
        resolve({ status: res.statusCode, message });
      });
    });
    req.on('error', reject);
    let sent = 0;
    const stream = fs.createReadStream(file);
    stream.on('data', (chunk) => {
      sent += chunk.length;
      onProgress(sent / size);
    });
    stream.on('error', reject);
    stream.pipe(req);
  });
}

async function upload(release, name) {
  const file = path.join(dist, name);
  const size = fs.statSync(file).size;
  const old = release.assets.find((a) => a.name === name);
  if (old) await gh('DELETE', `/repos/${owner}/${repo}/releases/assets/${old.id}`);

  const url = `${release.upload_url.replace(/\{.*\}$/, '')}?name=${encodeURIComponent(name)}`;
  const mb = (size / 1024 / 1024).toFixed(1);
  for (let attempt = 1; attempt <= 3; attempt++) {
    const label = `  • ${name} (${mb} МБ)${attempt > 1 ? `, попытка ${attempt}` : ''}… `;
    process.stdout.write(label);
    let shown = -1;
    const progress = (p) => {
      const pct = Math.floor(p * 100);
      if (size > 1024 * 1024 && process.stdout.isTTY && pct !== shown && pct % 5 === 0) {
        shown = pct;
        process.stdout.write(`\r${label}${pct}%`);
      }
    };
    try {
      const res = await sendFile(url, file, size, progress);
      if (process.stdout.isTTY && shown >= 0) process.stdout.write(`\r${label}`);
      if (res.status >= 200 && res.status < 300) {
        console.log('готово      ');
        return;
      }
      console.log(`ошибка ${res.status} ${res.message}`);
      if (res.status === 401 || res.status === 403) throw Object.assign(new Error(res.message), { status: res.status });
    } catch (e) {
      if (e.status) throw e;
      console.log(`обрыв: ${e.message}`);
    }
    // после обрыва GitHub мог оставить недогруженный файл — убираем перед повтором
    const fresh = await gh('GET', `/repos/${owner}/${repo}/releases/${release.id}`);
    const broken = fresh.assets.find((a) => a.name === name);
    if (broken) await gh('DELETE', `/repos/${owner}/${repo}/releases/assets/${broken.id}`);
  }
  throw new Error(`Не удалось загрузить ${name} после 3 попыток. Проверьте интернет и запустите npm.cmd run release ещё раз.`);
}

(async () => {
  if (!token) throw new Error('Не задан GH_TOKEN.');
  for (const name of FILES) {
    if (!fs.existsSync(path.join(dist, name))) throw new Error(`Нет файла dist/${name} — сборка не прошла?`);
  }
  const latest = fs.readFileSync(path.join(dist, 'latest.yml'), 'utf8');
  if (!latest.includes(`version: ${version}`) || !latest.includes(installer)) {
    throw new Error(`dist/latest.yml не соответствует версии ${version} — пересоберите проект.`);
  }

  const repoInfo = await gh('GET', `/repos/${owner}/${repo}`);
  let release = await findRelease();
  if (release) {
    console.log(`Релиз ${tag} уже есть — заменяю в нём файлы.`);
  } else {
    release = await gh('POST', `/repos/${owner}/${repo}/releases`, {
      tag_name: tag,
      target_commitish: repoInfo.default_branch,
      name: version,
      body: `Mediavert ${version}\n\nСкачайте **${installer}** ниже и запустите. Уже установленный Mediavert обновится сам.`,
      draft: true,
    });
    console.log(`Создан черновик релиза ${tag}.`);
  }

  console.log('Загружаю файлы:');
  for (const name of FILES) await upload(release, name);

  if (release.draft) {
    release = await gh('PATCH', `/repos/${owner}/${repo}/releases/${release.id}`, { draft: false, make_latest: 'true' });
  }
  console.log(`\nГотово! Релиз опубликован: ${release.html_url}`);
})().catch((err) => {
  console.error(`\nПубликация не удалась: ${explain(err)}\n`);
  process.exit(1);
});
