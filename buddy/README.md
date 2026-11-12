# buddy

MicroPython app bundle for the M5Stack Cardputer-Adv. Installed onto `/flash/` by the [`m5-onboard`](../onboard/) skill — see the [monorepo README](../README.md) for the end-to-end flow.

For the **M5Stack Core2**, see [`device-core2/`](device-core2/README.md): its own launcher, UI and Claude Buddy app, sharing this bundle's `buddy_ble` / `buddy_protocol` / `buddy_state` / `buddy_chars` modules via symlinks.

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

### Status reply differs from the Hardware Buddy reference (to do)

Claude Desktop polls `{"cmd":"status"}` to fill the Hardware Buddy
window's stats panel. This bundle's shared `buddy_protocol.py` answers
in a shape that predates the
[Hardware Buddy reference](https://github.com/anthropics/claude-desktop-buddy/blob/main/REFERENCE.md),
so the panel shows less than it could:

| | Reference | This bundle (Cardputer) |
|---|---|---|
| Envelope | `{"ack":"status","ok":true,"data":{...}}` | fields at the top level, no `ok` / `data` |
| `stats.vel` | median **seconds to approve**, last 8 approvals, integer (0 = no data) | approvals **per minute**, float — Desktop shows `--s` |
| `bat.mA` | negative = charging | always 0 (no battery reader yet) |
| `stats.nap` | cumulative seconds napping | count of idle periods |
| `stats.lvl` | from output tokens (50K per level) | from approve + deny count |

The Core2 bundle already sends the reference envelope, `vel`, and
`bat.mA` sign — see the `_Protocol` subclass in
[`device-core2/apps/claude_buddy.py`](device-core2/apps/claude_buddy.py)
and [`device-core2/README.md`](device-core2/README.md#status-reply).
**Enhancement:** move that envelope and `vel` logic into the shared
`buddy_protocol.py` / `buddy_state.py` so the Cardputer returns the
same data (then drop the Core2 subclass), and test on a Cardputer.
`nap` and `lvl` are pet-related and intentionally left as-is on both devices for now.

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
