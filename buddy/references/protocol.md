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
  "tokens": N,         # this turn
  "tokens_today": N,   # today total
  "prompt": {          # optional; present when waiting > 0
    "id": "...",
    "tool": "Bash",
    "hint": "rm -rf ./build/"
  }
}
```

Heartbeats arrive ~every 10 s while connected. No response is expected;
the device updates its UI silently.

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

> The shared `buddy_protocol.py` sends these at the top level of the
> ack. The [Hardware Buddy reference](https://github.com/anthropics/claude-desktop-buddy/blob/main/REFERENCE.md)
> wraps them — `{"ack":"status","ok":true,"data":{...}}` — and defines
> `vel` as median seconds-to-approve and `bat.mA` as negative while
> charging. The Core2 app follows the reference; the Cardputer doesn't
> yet. See `buddy/README.md` → "Status reply differs from the Hardware
> Buddy reference".

## Timing

- 10 s heartbeat interval
- 30 s silence → host treats device as dead
- 30 s unpair confirmation window (per request above)
- BLE IRQ handlers return within ~5 ms to avoid backpressure on the
  stack's RX path
