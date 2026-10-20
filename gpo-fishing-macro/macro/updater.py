"""Обновление exe прямо из программы.

GitHub после каждой сборки выкладывает exe в релиз с тегом gpo-latest (предварительный релиз
репозитория, чтобы не мешать релизам Mediavert). В описании релиза — версия и SHA-256 файла:
    version: 1.5.6
    sha256: 3f2a…
Программа сравнивает версию со своей, качает exe рядом с собой (*.new), проверяет SHA-256,
переименовывает себя в *.old.exe (запущенный exe Windows переименовать разрешает), ставит новый
на своё место и запускает его. config.json, fish_learned и остальное рядом с exe не трогаются.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass

from . import __version__

DEFAULT_REPO = "SanTW3/Mediavert"
TAG = "gpo-latest"
ASSET = "GPO-Fishing-Macro.exe"
TIMEOUT = 20


@dataclass
class Update:
    version: str
    url: str
    sha256: str
    size: int


def parse_version(text):
    return tuple(int(x) for x in re.findall(r"\d+", str(text or ""))[:4]) or (0,)


def is_newer(remote, local=__version__):
    return parse_version(remote) > parse_version(local)


def frozen():
    """Обновлять можно только собранный exe, не запуск из исходников."""
    return bool(getattr(sys, "frozen", False)) and sys.executable.lower().endswith(".exe")


def _get(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": "GPO-Fishing-Macro/" + __version__,
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def parse_release(data):
    """Update из ответа GitHub API о релизе или None, если в нём нет exe или версии."""
    body = data.get("body") or ""
    version = re.search(r"version:\s*([\d.]+)", body)
    sha = re.search(r"sha256:\s*([0-9a-fA-F]{64})", body)
    asset = next((a for a in data.get("assets") or [] if a.get("name") == ASSET), None)
    if not (version and sha and asset):
        return None
    return Update(version.group(1), asset["browser_download_url"], sha.group(1).lower(), int(asset.get("size") or 0))


def check(repo=DEFAULT_REPO):
    """Новая версия или None. Ошибки сети пробрасываются."""
    with _get(f"https://api.github.com/repos/{repo}/releases/tags/{TAG}") as resp:
        update = parse_release(json.loads(resp.read()))
    return update if update and is_newer(update.version) else None


def download(update, dest, progress=None):
    """Скачать exe в dest и проверить SHA-256. progress(доля 0..1)."""
    h = hashlib.sha256()
    done = 0
    tmp = dest + ".part"
    with _get(update.url, timeout=60) as resp, open(tmp, "wb") as f:
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress and update.size:
                progress(min(done / update.size, 1.0))
    if h.hexdigest() != update.sha256:
        os.remove(tmp)
        raise ValueError("файл скачался с ошибкой (не совпала контрольная сумма) — попробуйте ещё раз")
    os.replace(tmp, dest)
    return dest


def old_path(exe):
    return exe[:-4] + ".old.exe"


def install(new_exe, exe=None, launch=True):
    """Поставить скачанный exe вместо текущего и запустить его. Текущий процесс потом надо закрыть."""
    exe = exe or sys.executable
    old = old_path(exe)
    if os.path.exists(old):
        os.remove(old)
    os.replace(exe, old)          # запущенный exe переименовать можно, удалить — нет
    try:
        os.replace(new_exe, exe)
    except OSError:
        os.replace(old, exe)      # не вышло — возвращаем как было
        raise
    if launch:
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([exe], cwd=os.path.dirname(exe), close_fds=True, creationflags=flags)


def cleanup(exe=None):
    """Удалить *.old.exe от прошлого обновления (если старый процесс уже закрылся)."""
    exe = exe or sys.executable
    for path in (old_path(exe), exe + ".new", exe + ".new.part"):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
