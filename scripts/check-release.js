// Проверка перед публикацией релиза на GitHub: понятные подсказки вместо ошибок сборщика
const pkg = require('../package.json');

const pub = [].concat(pkg.build.publish || [])[0] || {};
const problems = [];

if (!pub.owner || pub.owner === 'YOUR_GITHUB_NAME') {
  problems.push('В package.json → build.publish укажите "owner" — ваш логин на GitHub.');
}
if (!pub.repo) problems.push('В package.json → build.publish укажите "repo" — имя репозитория.');
if (!process.env.GH_TOKEN) {
  problems.push(
    'Не задан GH_TOKEN. Создайте токен на github.com → Settings → Developer settings → Personal access tokens\n' +
      '   (Fine-grained, доступ к репозиторию, права Contents: Read and write), затем в PowerShell:\n' +
      '   $env:GH_TOKEN = "ваш_токен"; npm run release',
  );
}

if (problems.length) {
  console.error('\nПубликация невозможна:\n' + problems.map((p) => ' • ' + p).join('\n') + '\n');
  process.exit(1);
}

console.log(`Публикую Mediavert ${pkg.version} в github.com/${pub.owner}/${pub.repo} …`);
