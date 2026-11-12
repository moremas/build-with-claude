# buddy / device-core2

Claude Buddy for the **M5Stack Core2** (ESP32, 320x240 touchscreen, three
touch buttons under the screen). Port of the Cardputer-Adv bundle in
[`../device/`](../device/).

## What's shared and what isn't

| File | Source |
|------|--------|
| `buddy_ble.py`, `buddy_protocol.py`, `buddy_state.py`, `buddy_chars.py`, `burst_frames.py` | symlinks to `../device/` — same BLE stack, wire protocol, NVS state and sprite frames |
| `buddy_ui_core2.py` | Core2 view (320x240, DejaVu18/24/56, vibration on new prompts) |
| `apps/claude_buddy.py` | Core2 app loop: touch buttons, real AXP192 battery readings |
| `main.py` | Core2 launcher (no WiFi, no keyboard) |

The Cardputer's `hello_cardputer.py` and `snake.py` aren't included —
they need the Cardputer keyboard.

## Install

The normal `m5-onboard` commands recognize a Core2 that is running
UIFlow 2.0 (via `scripts/identify.py`) and pick the Core2 firmware and
this bundle automatically:

```bash
python3 .claude/skills/m5-onboard/scripts/onboard.py --apps buddy        # flash + install
python3 .claude/skills/m5-onboard/scripts/onboard.py                     # UIFlow 2.0 firmware only
python3 .claude/skills/m5-onboard/scripts/install_apps.py --port /dev/cu.usbserial-XXXX --src buddy   # apps only
```

A blank Core2 (no MicroPython yet) can't be identified — name it:

```bash
python3 .claude/skills/m5-onboard/scripts/onboard.py --variant core2 --apps buddy-core2
```

Installing sets NVS `uiflow.boot_option=2`, so the Core2 boots into
`main.py` (this launcher) instead of UIFlow's own startup.

## Screen

Connected, top to bottom:

- **Header** — "Claude Buddy" (or the name set from Claude Desktop) and
  the link state (`WAITING` / `PAIRING` / `LINKED` / `OFF`).
- **Queue** — `Running N  Waiting N  Total N` from Desktop's heartbeat.
- **Request area** (four rows) — an orange **Allow:** box you can answer
  with A/B, a yellow **Waiting for approval** box to answer in the app,
  or Desktop's latest status message.
- **Bottom row** — `Today: 53.3k tokens` (the heartbeat's
  `tokens_today`: output tokens since local midnight) and the battery
  (`100% USB` on USB power, `87%` on battery).
- **Button labels** — Approve · Deny · Hold: exit.

Level and approve/deny counts are still kept on the device and reported
to Claude Desktop, but aren't shown on screen.

## Buttons

| | BtnA (left) | BtnB (middle) | BtnC (right) |
|-|-------------|---------------|--------------|
| Launcher | up | open | down |
| Claude Buddy | approve once | deny | hold ~1 s to exit |
| Unpair request | confirm | cancel | — |

## Connecting

1. Open **Claude Buddy** from the launcher. The screen shows the device's
   BLE name (e.g. `Claude_44747E`).
2. In Claude Desktop: **Help → Troubleshooting → Enable Developer Tools**
   (one-time), then **Developer → Hardware Buddy → Connect** and pick that
   name.

## Approving requests

When Claude Desktop sends an answerable permission request, the Core2
buzzes and shows an orange **Allow:** box — press **A** to approve or
**B** to deny.

Claude Desktop doesn't always include the request. Testing with Claude
Code sessions in Claude Desktop: Bash requests with short commands
(up to ~35 characters, e.g. `say "my name is joe"`) arrived with a
`"prompt"` and were approved/denied from the device; longer ones
(~70+ characters) and question dialogs arrived as heartbeats with
`"waiting": 1` but no `"prompt"`. The device link uses the default
20-byte BLE payload (the app doesn't request a larger MTU), which may
be why Desktop leaves larger prompts out — untested. Without a request
id there is nothing to answer, so the device shows a yellow **Waiting
for approval** box and "Approve in the Claude app". With one session
running it also shows the tool call (taken from Desktop's `evt`
stream); with several, it shows "N waiting for approval" and how many
sessions are running instead, since the latest tool call may belong to
a different session than the one waiting.

Heartbeats are combined across all sessions, so the device buzzes once
when the waiting count goes up or a prompt with a new id arrives — not
on tool calls, which other (auto-approved) sessions also send.

## Status reply

Claude Desktop polls `{"cmd":"status"}` every couple of seconds for the
Hardware Buddy window's stats panel. The Core2 app answers in the shape
the [Hardware Buddy reference](https://github.com/anthropics/claude-desktop-buddy/blob/main/REFERENCE.md)
specifies — `{"ack":"status","ok":true,"data":{name, sec, bat, sys, stats}}`
— with `bat.mA` negative while charging, and `stats.vel` as the median
seconds you took to approve the last 8 requests answered on the device
(kept in NVS; 0 until the first one). It does this with a small
`_Protocol` subclass in `apps/claude_buddy.py`; the shared
`buddy_protocol.py` (and so the Cardputer bundle) still sends the
older shape — see `../README.md` for the gap and the planned fix.

`stats.deny` counts only denials pressed on the device (B); requests
denied in the Claude app never reach it. `stats.nap` (idle periods,
not seconds) and `stats.lvl` (from approvals, not tokens) differ from
the reference on purpose for now — they feed pet features, which
aren't built yet.

## Debugging what Desktop sends

Create an empty `/flash/buddy_rx.log` on the device (from the REPL:
`open('/flash/buddy_rx.log','w').close()`) and Claude Buddy appends
every line it receives (capped at 16 KB). Delete the file to stop —
the log contains tool-call text from your Claude session.

## Back to stock UIFlow

From the device REPL:

```python
import esp32; n = esp32.NVS('uiflow'); n.set_u8('boot_option', 1); n.commit()
import machine; machine.reset()
```
