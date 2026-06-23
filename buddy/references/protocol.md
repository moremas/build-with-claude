# Claude Buddy BLE protocol — reference

This file documents the wire format the device implements. The
upstream specification lives with the Claude Desktop Buddy
implementation in Claude.app's Developer menu; if the host side
changes, mirror the change here and in `buddy_protocol.py`.

## Transport

Nordic UART Service, line-delimited UTF-8 JSON with `\n` terminators.

| Role | Characteristic UUID | Flags |
| ---- | ------------------- | ----- |
| Service | `6e400001-b5a3-f393-e0a9-e50e24dcca9e` | — |
| RX (host → device) | `6e400002-b5a3-f393-e0a9-e50e24dcca9e` | `WRITE`, `WRITE_NR` |
| TX (device → host) | `6e400003-b5a3-f393-e0a9-e50e24dcca9e` | `READ`, `NOTIFY` |

Advertising name: `Claude_<last 6 hex digits of BT MAC>`.

## Authentication

**The link is unauthenticated on UIFlow 2.0.** The MicroPython BLE
build that ships with UIFlow 2.0 strips the pairing API entirely —
characteristic-level encryption flags are accepted but ignored, and
there is no IO_CAPABILITY config to drive a DisplayOnly passkey
exchange. This means any BLE central in range can connect to the
service and write to RX. The device-side code lights up the encrypted
flow automatically on any future build that restores the pairing API
(see `buddy_ble.py`); until then we mitigate at the application
layer:

- **File-push commands are refused.** `char_begin`, `file`, `chunk`,
  `file_end`, and `char_end` always return
  `{"ack":"<cmd>","ok":false,"err":"file push disabled on unauthenticated link"}`.
  Re-enabled when the link gains real authentication.
- **`unpair` requires on-device confirmation.** The device shows a
  confirmation overlay and waits up to 30 s for a Y/N press from the
  physical keyboard before performing the wipe. See the table below
  for the ack shapes during this flow.
- **`status`, `name`, `owner`, and heartbeats remain open.** They
  are non-destructive and the desktop needs them to render the
  connected UI; gating them without host-side coordination would
  break the user-facing experience for no security gain. The status
  ack reports `sec` honestly (`false` on this build) so the host can
  surface the trust state to the operator.

The `sec` field in the status ack reflects whether the underlying
GATT link is encrypted. Today this is always `false` on UIFlow 2.0.
A future build with link encryption will report `true`, and the
defenses above can be relaxed accordingly.

## Inbound (host → device)

| cmd | shape | behavior |
| --- | ----- | -------- |
| `status` | `{"cmd":"status"}` | Reply with a status ack line. |
| `name`   | `{"cmd":"name","name":"..."}` | Persist name, redraw identity band. |
| `owner`  | `{"cmd":"owner","owner":"..."}` | Persist owner, redraw. |
| `unpair` | `{"cmd":"unpair"}` | Show on-device confirmation overlay. See ack shapes below. |
| `char_begin` / `file` / `chunk` / `file_end` / `char_end` | (per upstream spec) | Refused on this build with `{"ack":"<cmd>","ok":false,"err":"file push disabled on unauthenticated link"}`. |

A message **without** a `cmd` field is a heartbeat. Recognized fields:

```
{
  "total": N,          # total sessions/prompts
  "running": N,        # currently active
  "waiting": N,        # awaiting permission
  "msg": "string",     # flavor text
  "entries": N,        # history entries
  "tokens": N,         # cumulative tokens (session-scoped; see note)
  "tokens_today": N,   # cumulative tokens today
  "prompt": {          # optional; present when waiting > 0
    "id": "...",
    "tool": "Bash",
    "hint": "rm -rf ./build/"
  }
}
```

Heartbeats arrive ~every 10 s while connected. No response is expected;
the device updates its UI silently.

This is the **complete** field set — confirmed by a live capture from
Claude.app's Hardware Buddy (Cardputer-Adv):

```
keys: ['entries', 'msg', 'running', 'tokens', 'tokens_today', 'total', 'waiting']
```

> A note on `tokens`: protocol.md historically called it "this turn",
> but the live capture showed `tokens` (134704) > `tokens_today` (45665),
> so it is **not** per-turn — it's a larger cumulative counter (session
> or rolling). Treat its exact window as unspecified.

### Quota fields (from the BLE companion, not Claude.app)

Claude.app's heartbeat contains **no** quota/utilization/limit/reset
field, and the device is BLE-only so it can't query usage itself. The
on-device "5h / Week / Sonnet" bars are instead fed by a host companion,
`scripts/quota_push.py` (backed by `codexbar --provider anthropic
--format json`), which writes extra heartbeat fields:

```
{
  "five_h_util": N,    # codexbar usage.primary.usedPercent   (5-hour)
  "week_util": N,      # codexbar usage.secondary.usedPercent (7-day, all)
  "sonnet_util": N,    # codexbar usage.tertiary.usedPercent  (7-day, Sonnet)
  "five_h_color": C,   # RGB int (0xRRGGBB) for the 5h bar fill
  "week_color": C,     # RGB int for the Week bar fill
  "sonnet_color": C    # RGB int for the Sonnet bar fill
}
```

The `*_util` values are utilization percentages (0..100, "used"); the
codexbar mapping was verified against the labeled usage API
(`primary`==`five_hour`, `secondary`==`seven_day`,
`tertiary`==`seven_day_sonnet`). The device renders *remaining* =
`100 - util` for the **bar length**, and shows `--` for any field it
hasn't received (e.g. on the Claude.app link, which sends none).

The `*_color` values are **plain RGB ints the device paints directly** —
no colour logic on the device. The companion resolves them host-side from
the codexbar **pace stage** (`farBehind`…`farAhead`) on a green→red ramp
(`*Behind`/reserve = green … `*Ahead`/deficit = red), with a remaining-%
fallback where there's no stage (Sonnet always; 5h/Week when codexbar
omits pace early in a window). Keeping the stage→colour map on the host
means colours can be retuned without re-flashing the device. All these
names are in the device's heartbeat-detection set (`_HEARTBEAT_FIELDS` in
`buddy_protocol.py`) so a quota-only message is recognized as a heartbeat.

**Connection model:** the companion is the BLE central, like Claude.app,
and a buddy accepts one central at a time — so the companion and
Claude.app are mutually exclusive. Connect the companion for a live quota
readout; quit it and reconnect from Claude.app for prompt-approval.
Simultaneous use would require multi-connection support in the firmware.

## Outbound (device → host)

| Shape | When |
| ----- | ---- |
| `{"cmd":"hello","name":..,"owner":..,"version":...}` | Once, right after the connection state advances to "encrypted" (or its dummy equivalent on builds without encryption). |
| `{"cmd":"permission","id":"<prompt id>","decision":"once" \| "deny"}` | On Y/N keypress while a permission prompt is pending. |
| `{"ack":"status","name":..,"sec":<bool>,"bat":{...},"sys":{...},"stats":{...}}` | In response to `status`. `sec` reflects actual GATT encryption state; `false` on UIFlow 2.0. |
| `{"ack":"<cmd>","ok":bool,...}` | Generic ack for name/owner/char_*. |

### Unpair ack shapes

`unpair` triggers a multi-step flow because the device requires
on-device confirmation. The host should expect *two* acks per
request:

1. Immediate response on receipt:
   `{"ack":"unpair","ok":false,"pending":true,"err":"awaiting on-device confirmation"}`
2. Resolution, sent up to 30 s later, exactly one of:
   - `{"ack":"unpair","ok":true,"confirmed":true}` — user pressed Y. The device wipes state and disconnects ~200 ms after this ack.
   - `{"ack":"unpair","ok":false,"cancelled":true}` — user pressed N. The connection stays up.
   - `{"ack":"unpair","ok":false,"timed_out":true,"err":"no on-device confirmation"}` — 30 s elapsed with no press. The connection stays up; the host can re-issue if it still wants to unpair.

If the device-side app exits via Q while a confirmation is pending,
the resolution ack may not arrive — the link drops without one. The
host should treat a disconnect during the pending window as
equivalent to a cancellation.

### Status ack body

```
bat:   {pct, mV, mA, usb}     # pct quantized to 0/25/50/75/100 on Basic (IP5306)
sys:   {up, heap}             # seconds since boot, free heap bytes
stats: {appr, deny, vel, nap, lvl}
```

## Timing

- 10 s heartbeat interval
- 30 s silence → host treats device as dead
- 30 s unpair confirmation window (per request above)
- BLE IRQ handlers return within ~5 ms to avoid backpressure on the
  stack's RX path
