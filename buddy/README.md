# buddy

MicroPython app bundle for the M5Stack Cardputer-Adv. Installed onto `/flash/` by the [`m5-onboard`](../onboard/) skill — see the [monorepo README](../README.md) for the end-to-end flow.

## What's on the device

```
/flash/
├── main.py              launcher menu (replaces UIFlow's boot flow)
├── buddy_*.py           shared libs (BLE, UI, state, protocol, chars)
├── burst_frames.py      sprite frames
└── apps/
    ├── claude_buddy.py  BLE client that pairs with Claude.app's Hardware Buddy
    ├── hello_cardputer.py
    └── snake.py
```

`main.py` scans `/flash/apps/` at boot and shows every `.py` as a menu entry. Drop a new file in there, re-run `m5-onboard go` (or `install_apps.py --src buddy`), and it shows up.

## Claude Buddy (BLE)

Open Claude → Developer menu → **Hardware Buddy** → Connect. BLE-only. Stats (approvals / denials / level) persist across reboots via NVS under the `buddy` namespace.

## Quota bars (BLE companion)

The **5h / Week / Sonnet** bars show the real account quota. Claude.app's heartbeat doesn't carry quota, and the device is BLE-only so it can't reach usage itself. A host companion, [`scripts/quota_push.py`](scripts/quota_push.py), bridges the gap: it reads `codexbar --provider anthropic --format json` and writes `five_h_util` / `week_util` / `sonnet_util` heartbeats to the device, which renders `100 − utilization` for each.

The **bar length** is remaining quota; the **bar colour** reflects the codexbar *pace stage* — green when you're under the even-burn pace (reserve) through red when you're well ahead of it (deficit, on track to run out early). Sonnet has no pace, so it colours by remaining-%. The stage→colour map lives **in `quota_push.py` (`_STAGE_COLORS`)**, not on the device, so you can retune colours by editing the script and restarting it — no re-flash. See [references/protocol.md](references/protocol.md#quota-fields-from-the-ble-companion-not-claudeapp).

### Show quota on the device — runbook

**One-time setup**

```bash
pip install bleak          # BLE central library
# codexbar must be on PATH and authenticated:
codexbar --provider anthropic --format json   # should print usage JSON
```

Preview the numbers any time without a device or Bluetooth:

```bash
python3 buddy/scripts/quota_push.py --dry-run
# 5h    : 86% remaining  stage=farBehind     color=0x00FF00
# Week  : 87% remaining  stage=slightlyAhead color=0xFFAA00
# Sonnet: 94% remaining  stage=n/a           color=0x00FF00
```

**Each time you want the bars live**

1. **On the device** — open the **Claude Buddy** app from the launcher so it advertises. If Claude.app is connected to it, disconnect first (Claude → Developer → Hardware Buddy → Disconnect): the device accepts **one BLE central at a time**.
2. **On this Mac — from Terminal.app** (see the Bluetooth note below), run:
   ```bash
   cd <repo>/buddy
   python3 scripts/quota_push.py            # connect, push real quota every 60s
   ```
   The bars fill with the real percentages and refresh each cycle. Leave it running; `Ctrl-C` to stop. (`--once` pushes a single sample and exits — handy for a quick check.)
3. **To go back to prompt-approval**, `Ctrl-C` the companion and reconnect from Claude.app. The bars revert to `--` (Claude.app sends no quota) — expected. Claude.app and the companion can't be connected at the same time.

> **Must run from Terminal.app.** macOS grants Bluetooth permission **per binary**. Launching the companion from inside another app's shell (e.g. an editor/agent terminal) inherits that app's Bluetooth context, and CoreBluetooth aborts the process with **SIGABRT (exit 134)**. `--dry-run` does no BLE and is exempt. First run from Terminal.app will prompt for Bluetooth permission — allow it.

See [references/protocol.md](references/protocol.md#quota-fields-from-the-ble-companion-not-claudeapp) for the wire details.

## Iterating on device code

`scripts/` has dev tooling for editing device sources without re-running the full onboard flow:

```bash
# Push a subset of files over USB-serial
python3 scripts/push.py --port /dev/cu.usbmodem1101 --files apps/snake.py

# Watch device logs
python3 scripts/tail_serial.py --port /dev/cu.usbmodem1101

# One-shot REPL exec
python3 scripts/repl_run.py --port /dev/cu.usbmodem1101 --script "import os; print(os.listdir('/flash'))"
```

`gen_burst_frames.py` regenerates `burst_frames.py` from source sprites.

## References

- `references/` — BLE protocol notes for the Claude Buddy app

## License

Apache 2.0 — see the [root LICENSE](../LICENSE) and [LICENSE-THIRD-PARTY.md](../LICENSE-THIRD-PARTY.md).
