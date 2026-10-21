"""Обновление прямо из программы.

FishPilot раздаётся zip-архивом с папкой (FishPilot.exe + _internal) — так меньше ложных срабатываний
антивирусов, чем у одного exe. Каждая версия — релиз vX.Y.Z в SanTW3/FishPilot (последний помечен latest)
с файлом FishPilot-win64.zip, в описании:
    version: 2.0.1
    zip_sha256: …

До 2.0.1 программа жила в SanTW3/Mediavert и проверяла там релиз gpo-latest (версии 1.x качали оттуда
одиночный exe). Там лежит «мост» 2.0.1 — после него все проверяют уже этот репозиторий.

Обновление папки: zip скачивается и проверяется (SHA-256), распаковывается в %APPDATA%\\FishPilot\\update,
затем маленький скрипт ждёт, пока программа закроется, копирует новые файлы в папку программы
и запускает её снова. Настройки и улов лежат в %APPDATA%\\FishPilot и не трогаются.
Одиночный exe версии 2.x при обновлении переезжает в папку FishPilot рядом с собой.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass

from . import __version__
from .i18n import tr

DEFAULT_REPO = "SanTW3/FishPilot"   # адрес обновлений зашит в программу, из настроек его не берём
ZIP_ASSET = "FishPilot-win64.zip"
EXE_NAME = "FishPilot.exe"
TIMEOUT = 20
MAX_SIZE = 300 * 1024 * 1024


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
    """Обновлять можно только собранную программу, не запуск из исходников."""
    return bool(getattr(sys, "frozen", False)) and sys.executable.lower().endswith(".exe")


def onefile():
    """Старый формат — один exe (PyInstaller распаковывает себя во временную папку _MEI…)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if not (frozen() and meipass):
        return False
    return os.path.basename(os.path.normpath(meipass)).lower().startswith("_mei")


def _get(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": "FishPilot/" + __version__,
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def parse_release(data):
    """Update из ответа GitHub API о релизе (zip с папкой) или None, если в нём нет zip или версии."""
    body = data.get("body") or ""
    version = re.search(r"^version:\s*([\d.]+)", body, re.MULTILINE)
    sha = re.search(r"^zip_sha256:\s*([0-9a-fA-F]{64})", body, re.MULTILINE)
    asset = next((a for a in data.get("assets") or [] if a.get("name") == ZIP_ASSET), None)
    if not (version and sha and asset):
        return None
    return Update(version.group(1), asset["browser_download_url"], sha.group(1).lower(), int(asset.get("size") or 0))


def check(repo=DEFAULT_REPO):
    """Новая версия или None. Ошибки сети пробрасываются. Одиночный exe переезжает в папку даже на той же
    версии — иначе он так и остался бы в старом формате."""
    with _get(f"https://api.github.com/repos/{repo}/releases/latest") as resp:
        update = parse_release(json.loads(resp.read()))
    if update is None:
        return None
    if is_newer(update.version) or (onefile() and not is_newer(__version__, update.version)):
        return update
    return None


def download(update, dest, progress=None):
    """Скачать файл в dest и проверить SHA-256. progress(доля 0..1)."""
    if update.size > MAX_SIZE:
        raise ValueError(tr("файл обновления слишком большой"))
    h = hashlib.sha256()
    done = 0
    tmp = dest + ".part"
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with _get(update.url, timeout=60) as resp, open(tmp, "wb") as f:
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            done += len(chunk)
            if done > MAX_SIZE:
                raise ValueError(tr("файл обновления слишком большой"))
            f.write(chunk)
            h.update(chunk)
            if progress and update.size:
                progress(min(done / update.size, 1.0))
    if h.hexdigest() != update.sha256:
        os.remove(tmp)
        raise ValueError(tr("файл скачался с ошибкой (не совпала контрольная сумма) — попробуйте ещё раз"))
    os.replace(tmp, dest)
    return dest


def extract(zip_path, dest):
    """Распаковать zip в dest (с защитой от путей вида ../). Возвращает папку, где лежит FishPilot.exe."""
    if os.path.isdir(dest):
        shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest, exist_ok=True)
    root = os.path.realpath(dest)
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            target = os.path.realpath(os.path.join(dest, name))
            if target != root and not target.startswith(root + os.sep):
                raise ValueError("unsafe path in zip: " + name)
        z.extractall(dest)
    for folder, _dirs, files in os.walk(dest):
        if EXE_NAME in files:
            return folder
    raise ValueError(tr("в архиве обновления нет {exe}", exe=EXE_NAME))


def clean_env(env=None):
    """Окружение для запуска новой версии. Без этого новый exe (PyInstaller) решает, что он — дочерний
    процесс старого, и работает из его временной папки _MEI…; старый при выходе пытается её удалить и
    пишет «Failed to remove temporary directory»."""
    env = dict(os.environ if env is None else env)
    for key in list(env):
        if key.startswith("_PYI") or key.startswith("_MEIPASS"):
            del env[key]
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def install_script(new_dir, app_dir, pid):
    """Текст .cmd: дождаться выхода программы (pid), скопировать новые файлы, запустить, удалить себя."""
    exe = os.path.join(app_dir, EXE_NAME)
    # Полные пути: если в PATH есть Git (usr\bin), «find» и «timeout» оказались бы его утилитами
    sys32 = r"%SystemRoot%\System32"
    return "\r\n".join([
        "@echo off",
        f"{sys32}\\chcp.com 65001 >nul",
        ":wait",
        f'{sys32}\\tasklist.exe /FI "PID eq {pid}" 2>nul | {sys32}\\find.exe "{pid}" >nul'
        f' && ({sys32}\\ping.exe -n 2 127.0.0.1 >nul & goto wait)',
        f'{sys32}\\robocopy.exe "{new_dir}" "{app_dir}" /E /R:5 /W:1 /NFL /NDL /NJH /NJS /NP >nul',
        "if errorlevel 8 goto end",
        f'start "" "{exe}" --wait-instance',
        ":end",
        f'rmdir /S /Q "{new_dir}" >nul 2>&1',
        '(goto) 2>nul & del "%~f0"',
        "",
    ])


def install(zip_path, work_dir, app_dir=None, launch=True):
    """Поставить обновление из zip. Папка программы (app_dir) заменяется скриптом после выхода;
    одиночный exe — переезжает в папку FishPilot рядом с собой. Возвращает папку, где будет программа.
    Текущий процесс после этого нужно закрыть."""
    new_dir = extract(zip_path, os.path.join(work_dir, "new"))
    if app_dir is None:
        here = os.path.dirname(os.path.abspath(sys.executable))
        app_dir = os.path.join(here, "FishPilot") if onefile() else here
    os.makedirs(app_dir, exist_ok=True)
    script = os.path.join(work_dir, "install-update.cmd")
    with open(script, "w", encoding="utf-8") as f:
        f.write(install_script(new_dir, app_dir, os.getpid()))
    if launch:
        _run_script(script, app_dir)
    return app_dir


def _run_script(script, app_dir):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if _writable(app_dir):
        subprocess.Popen(["cmd.exe", "/c", script], close_fds=True, creationflags=flags, env=clean_env())
    else:
        # Папка без прав на запись (Program Files) — просим права администратора у Windows
        import ctypes
        ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", f'/c "{script}"', None, 0)


def _writable(path):
    try:
        with tempfile.NamedTemporaryFile(dir=path, delete=True):
            return True
    except OSError:
        return False


def cleanup(exe=None):
    """Хвосты прошлых обновлений рядом с exe (версии 1.x: *.old.exe, *.new)."""
    exe = exe or sys.executable
    for path in (exe[:-4] + ".old.exe", exe + ".new", exe + ".new.part"):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
