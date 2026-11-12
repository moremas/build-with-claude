# m5stack

Flash a Cardputer-Adv and install the Claude Buddy apps in one command.

## Quick start

1. Clone this repo locally — anywhere is fine:
   ```bash
   git clone <repo-url>
   ```
   The skill auto-detects the buddy bundle relative to its own install location, so the clone path doesn't matter. `~/Downloads/m5stack/` and `~/Desktop/m5stack/` are also checked as conventional fallbacks.
2. Plug the Cardputer into your laptop via USB-C
3. Open Claude Code and start a new chat
4. Point Claude Code to the repo folder
5. Type `m5-onboard go`

That's it — Claude will automatically flash the firmware and push the apps onto the device.

### When Claude prompts you to put the device into download mode

Halfway through, Claude will pause and ask you to do this on the **back** of the device:

1. Hold down the **G0** button on the Cardputer
2. While still holding G0, press the **Reset** button
3. Release Reset first, then release G0
4. The screen goes dark — device is in download mode

Claude takes over from there.

### What happens next

- **Firmware writes to the device** (~180 seconds)
- **Apps push to the device** (~100 seconds)
- **Device reboots** straight into the launcher — pick an app and go

Done. Power the device on/off with the side switch.

---

## Using Claude Buddy (BLE)

1. Power on the Cardputer
2. Pick **Claude Buddy** from the launcher menu
3. In Claude Desktop: **Help → Troubleshooting → Enable Developer Tools** (one-time, persists)
4. Then **Developer menu → Hardware Buddy → Connect**

## M5Stack Core2

The Core2 is supported with its own Claude Buddy bundle in [`buddy/device-core2/`](buddy/device-core2/) — a touch-button launcher and a 320x240 Claude Buddy that reuse the Cardputer bundle's BLE, protocol and state modules. Plug it in and type `m5-onboard go` as usual:

- If the Core2 is already running UIFlow 2.0, the skill **identifies it automatically**, flashes the Core2 firmware, and — when apps are requested — installs `buddy-core2` instead of the Cardputer bundle.
- A blank Core2 can't be identified, so pass the variant: `python3 .claude/skills/m5-onboard/scripts/onboard.py --variant core2 --apps buddy-core2`.
- Firmware only (no apps): leave off `--apps`.
- No button dance: the Core2's USB-UART bridge resets itself into download mode.

On the device: **A** up / **B** open / **C** down in the launcher; in Claude Buddy **A** approve, **B** deny, **hold C** to exit. See [`buddy/device-core2/README.md`](buddy/device-core2/README.md).

## Event WiFi

The bundled launcher auto-connects to the event WiFi (`cardputer` / `cardconnect`) on every boot and displays the result on screen — `Connected · IP: 192.168.x.x` on success, `WiFi: offline` on failure (the launcher always continues either way). The credentials are intentionally checked into [`buddy/device/wifi_event.py`](buddy/device/wifi_event.py); the AP is publicly broadcast at the venue and the password is the published handout, so this is not a leaked secret. To use this bundle outside the event, edit `wifi_event.py` (replace `SSID`/`PASSWORD`) or remove the `_connect_wifi_with_splash()` call near the top of `main()` to disable the auto-connect entirely.

## Adding your own app

1. Drop a `.py` file into `buddy/device/apps/`
2. Push just the apps without re-flashing:
   ```bash
   python3 .claude/skills/m5-onboard/scripts/install_apps.py --port <PORT> --src buddy
   ```
3. The launcher auto-discovers the new app on next boot

Crib from `buddy/device/apps/hello_cardputer.py` — it's the smallest example of the conventions (keyboard polling, font, exit behaviour).

## Getting back to stock UIFlow

The buddy bundle takes over the boot flow via `/flash/main.py`. Remove
that file and UIFlow's stock launcher boots normally on the next reset.
From the device REPL:

```python
import os
os.remove('/flash/main.py')
import machine; machine.reset()
```

To also drop the apps under `/flash/apps/`, walk that directory the
same way and remove what you don't want.

If you want a fresh UIFlow firmware on top, re-run `m5-onboard go`
_without_ `--apps`: the skill flashes UIFlow and stops, leaving the
filesystem alone. The previous `boot_uiflow.py`-rename procedure here
referred to a backup that `install_apps.py` only creates when the
bundle ships its own root `boot.py`; the buddy bundle doesn't, so
that backup never exists for these users.

---

## Prerequisites

You need **Python 3.10+**, **git**, and **Claude Code** on your laptop. `pyserial` ships vendored inside `.claude/skills/m5-onboard/scripts/vendor/`. `esptool` is GPL-licensed and is **not** vendored — the skill auto-installs it via pip on first run if it isn't already in your environment, so the user-facing experience is still a single command. To pre-install explicitly: `python3 -m pip install --user -r requirements.txt`.

Bootstrap if needed:

- **macOS** — `python3` usually pre-installed; if not, `brew install python`
- **Linux (Debian/Ubuntu)** — `sudo apt-get install -y python3 python3-pip git`
- **Windows** — `winget install -e --id Python.Python.3.13` and `winget install -e --id Git.Git`

**Windows + older boards only:** the CH9102 USB-UART driver is needed for Basic / Fire / Core2 / StickC. Download from [WCH](https://www.wch.cn/downloads/CH343SER_EXE.html). Cardputer-Adv and CoreS3 use the in-box composite-USB driver and need nothing extra.

**Want `--apps buddy` to point at a different bundle?** The default resolves to the `buddy/device/` directory next to the skill in this repo, with `~/Downloads/m5stack/` and `~/Desktop/m5stack/` checked as fallbacks. To override (e.g. you maintain a fork or have a customized bundle elsewhere), set `M5_BUDDY_DIR`:

```bash
export M5_BUDDY_DIR=/path/to/buddy/device
```

## Troubleshooting

- **Download-mode prompt keeps retrying** — you're releasing G0 too early. Release Reset first, keep holding G0 for about a second, then release.
- **"No USB-UART bridge found" (older boards)** — install the CH9102 driver on Windows; on macOS/Linux, unplug and replug.
- **Claude Buddy never connects over BLE** — make sure the buddy launcher (not UIFlow's) owns `/flash/main.py`. The skill handles this automatically on install.
- **Something else feels broken** — run `python3 .claude/skills/m5-onboard/scripts/smoke_test.py --port <PORT>` for an I2C + LCD + speaker + button check.

## What's in this repo

- **`.claude/skills/m5-onboard/`** — the Claude Code skill. Detect port, flash UIFlow, install apps. See [`.claude/skills/m5-onboard/SKILL.md`](.claude/skills/m5-onboard/SKILL.md) for the full playbook and every gotcha baked into the scripts.
- **`buddy/`** — the MicroPython app bundle that gets installed. See [`buddy/README.md`](buddy/README.md) for device-side layout and iteration tooling. `buddy/device/` is the Cardputer-Adv bundle; `buddy/device-core2/` is the Core2 port.

The two are decoupled by design: the `m5-onboard` skill can install any bundle via `--apps <path>`; `buddy` is just what ships here.

## License

This project's own code is licensed under **Apache 2.0** — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

`pyserial` (BSD-3-Clause, Apache-compatible) is the only third-party package bundled in `.claude/skills/m5-onboard/scripts/vendor/`. `esptool` (GPLv2+) is intentionally not vendored; it's declared as a pip dependency in [`requirements.txt`](requirements.txt) so the repository itself stays cleanly Apache-2.0. See [`LICENSE-THIRD-PARTY.md`](LICENSE-THIRD-PARTY.md) for details.
