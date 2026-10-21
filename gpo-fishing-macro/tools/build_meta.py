"""Файлы для сборки: ресурс версии exe (свойства файла в проводнике) и список сторонних лицензий.

    python tools/build_meta.py version  build/version.txt
    python tools/build_meta.py licenses build/THIRD_PARTY_LICENSES.txt   (нужен pip-licenses)
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from macro import __version__  # noqa: E402

VERSION_TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
                    date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'SanTW3'),
      StringStruct('FileDescription', 'FishPilot - fishing assistant for GPO (Roblox)'),
      StringStruct('FileVersion', '{v}'),
      StringStruct('InternalName', 'FishPilot'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 SanTW3. MIT License'),
      StringStruct('OriginalFilename', 'FishPilot.exe'),
      StringStruct('ProductName', 'FishPilot'),
      StringStruct('ProductVersion', '{v}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""

# Что попадает в сборку, но не стоит в pip: интерпретатор, Tcl/Tk, mqtt.js мини-приложения
EXTRA = """
================================================================================
Python {py}
Python Software Foundation License — https://docs.python.org/3/license.html

================================================================================
Tcl/Tk (tkinter)
BSD-style license — https://www.tcl.tk/software/tcltk/license.html

================================================================================
pynput — LGPL-3.0
pynput is used unmodified as a separate library. Its source code is available at
https://github.com/moses-palmer/pynput and you may replace it: the library files
are in the _internal folder of FishPilot.
"""


def version():
    nums = [int(x) for x in __version__.split(".")][:4]
    nums += [0] * (4 - len(nums))
    return VERSION_TEMPLATE.format(t=tuple(nums), v=__version__)


def _runtime_packages():
    """Пакеты из requirements.txt и всё, от чего они зависят (без pytest, PyInstaller и прочих инструментов)."""
    from importlib import metadata
    from packaging.requirements import Requirement
    with open(os.path.join(os.path.dirname(__file__), "..", "requirements.txt"), encoding="utf-8") as f:
        todo = [Requirement(line) for line in (x.split("#")[0].strip() for x in f) if line]
    seen = {}
    while todo:
        req = todo.pop()
        if req.marker and not req.marker.evaluate({"extra": ""}):
            continue
        name = req.name.lower().replace("_", "-")
        if name in seen:
            continue
        try:
            dist = metadata.distribution(req.name)
        except metadata.PackageNotFoundError:
            continue
        seen[name] = dist.metadata["Name"]
        todo += [Requirement(r) for r in dist.requires or ()]
    return sorted(seen.values(), key=str.lower)


def licenses():
    out = subprocess.run([sys.executable, "-m", "piplicenses", "--from=mixed", "--with-license-file",
                          "--no-license-path", "--format=json", "--packages", *_runtime_packages()],
                         capture_output=True, text=True, check=True).stdout
    parts = [f"FishPilot {__version__} includes the following third-party software.\n"]
    for pkg in sorted(json.loads(out), key=lambda p: p["Name"].lower()):
        parts.append("=" * 80)
        name = pkg["License"]
        if name in ("", "UNKNOWN"):
            from importlib import metadata
            name = metadata.metadata(pkg["Name"]).get("License-Expression") or "see license text"
        parts.append(f"{pkg['Name']} {pkg['Version']} — {name}")
        text = (pkg.get("LicenseText") or "").strip()
        if text and text != "UNKNOWN":
            parts.append(text)
        parts.append("")
    parts.append(EXTRA.format(py=sys.version.split()[0]))
    parts.append("=" * 80)
    parts.append("mqtt.js (Telegram mini app) — MIT License — https://github.com/mqttjs/MQTT.js")
    return "\n".join(parts)


if __name__ == "__main__":
    text = {"version": version, "licenses": licenses}[sys.argv[1]]()
    os.makedirs(os.path.dirname(os.path.abspath(sys.argv[2])), exist_ok=True)
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        f.write(text)
