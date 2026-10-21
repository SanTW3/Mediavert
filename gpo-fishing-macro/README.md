# FishPilot — fishing autopilot for GPO

*English · [Русский](README.ru.md)*

A Windows app for Grand Piece Online (Roblox). It casts the rod, plays the fishing mini-game, keeps a catch log
and **rings your phone** when something goes wrong.

> FishPilot is free (MIT license) and **not affiliated with Roblox or Grand Piece Online**. Automating the game
> may break its rules and can get you banned — use it at your own risk. The software is provided "as is".
> Fish icons and names belong to their respective owners.

**Support the author:** [donationalerts.com/r/etnealy](https://www.donationalerts.com/r/etnealy) — or the
**♥ Support the author** button in the app and in the Telegram mini app.

## Install

1. Download **FishPilot-win64.zip** from the [gpo-latest](https://github.com/SanTW3/Mediavert/releases/tag/gpo-latest) release.
2. Unpack it to any folder (don't run it from inside the archive) and start `FishPilot\FishPilot.exe`.
   No Python needed.
3. Windows SmartScreen may say "Windows protected your PC" — the app has no paid code signature.
   Click **More info → Run anyway**.

The app updates itself: when a new version is out, an **⬆ Update to …** button appears in the bottom left.
It downloads the zip, checks its SHA-256, replaces the files after the app closes and starts again.
Your settings are kept.

**Languages:** English, Русский, Español, Português (BR). Pick one on the first page of the guide or in
**App → Language**.

## First setup

A step-by-step guide opens on the first launch (reopen it with **? Setup guide** in the bottom left).
**Home** always shows what is still missing.

1. Stand at the water with the rod. Put the rod in a hotbar slot: **Setup → Item slots**.
2. Catch one fish by hand and press **F2** during the mini-game: draw a box around both bars.
3. **Setup → Casting → Set water spot**: click the water.
4. **F1** — start/stop, **F3** — exit, **F5** — HUD over the game. Keys can be changed in **App**.

## Phone alarms

Section **Phone**. The app alerts you when there are no bites, no fish, the Roblox window is gone,
the anti-cheat black screen doesn't go away or the macro crashed.

| Channel | What you need |
|---|---|
| **Telegram** | Create **your own** bot with @BotFather, open it, press Start, then **Get chat id** and **Test Telegram** |
| **ntfy** (free) | The ntfy app; subscribe to a long unique topic and enter the same name in the app |
| **Pushover** | User key and API token; emergency priority rings every 30 s until acknowledged |

With Telegram set up you also get control buttons (`/menu`), the catch list with icons (`/catches`) and a live
**mini app** panel with stats, start/stop and screenshots.

## Data and privacy

Everything is stored in `%APPDATA%\FishPilot`: `config.json` (settings), `secrets.bin` (tokens, encrypted with
Windows DPAPI for your user account), `fish_learned` (fish catalog) and `logs`.
**App → Help** has settings export/import (without tokens) and **Copy diagnostics** for bug reports.

FishPilot collects nothing and sends nothing to the author. Only what you configure goes online: messages and
screenshots (Roblox window only) to your Telegram bot, alarms to ntfy/Pushover, the mini app status through
public MQTT brokers (hashed topic, HMAC-signed), and the update check to GitHub.

## Build from source

Python 3.10+: `run.bat` starts the app, `build.bat` builds `dist\FishPilot` and `dist\FishPilot-win64.zip`.
Tests: `python -m pytest tests`. Releases are published by pushing a `fishpilot-vX.Y.Z` tag.

Detailed documentation (mini-game tuning, automation, HUD, catch log) is in [README.ru.md](README.ru.md).

## License

[MIT](LICENSE). Third-party libraries and their licenses are listed in `THIRD_PARTY_LICENSES.txt` inside the
archive. pynput (LGPL-3.0) is used unmodified and can be replaced in the `_internal` folder.
