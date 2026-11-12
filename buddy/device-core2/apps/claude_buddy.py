"""Claude Buddy for the M5Stack Core2.

Port of the Cardputer-Adv ``claude_buddy.py`` to the Core2. The BLE
stack, wire protocol, persistent state and character-receive modules
(``buddy_ble``, ``buddy_protocol``, ``buddy_state``, ``buddy_chars``)
are the shared ones from ``buddy/device/`` — only input, display and
battery are Core2-specific:

- Input: the three touch buttons under the screen.
      BtnA (left)    approve once   / confirm unpair
      BtnB (middle)  deny           / cancel unpair
      BtnC (right)   hold ~1 s to exit back to the launcher
  Exit is hold-to-exit rather than a tap so a stray touch on the right
  edge doesn't drop the BLE link mid-session.
- Display: ``buddy_ui_core2`` (320x240).
- Battery: real readings from the Core2's AXP192 via ``M5.Power``.

Exit path is the same as on the Cardputer: tear BLE down, then
``machine.reset()``, which reboots into the launcher (``/flash/main.py``).
"""

import sys

for _p in ("/flash", "/flash/apps"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import gc
import time

import M5
import machine

import buddy_ble
import buddy_chars
import buddy_protocol
import buddy_state
import buddy_ui_core2 as buddy_ui


_EXIT_HOLD_MS = 1000
# "On USB" = the battery isn't discharging. VBUS can't be used: with
# UIFlow's 5 V bus output on, the AXP192 reads ~5 V on VBUS even on
# battery. Measured on hardware: charging ~+100 mA, plugged in and
# full 0 mA, on battery about -75 mA (the board's own draw).
_ON_BATTERY_MA = -20

# Optional receive log for debugging what Claude Desktop sends. Off
# unless this file already exists on the device; create it (empty) from
# the REPL to turn logging on, delete it to turn it off. Lines are
# buffered in the BLE callback and written from the main loop, so no
# flash I/O happens in callback context.
_RX_LOG = "/flash/buddy_rx.log"
_RX_LOG_MAX = 16 * 1024
_RX_LINE_MAX = 400


def _rx_log_enabled():
    import os
    try:
        os.stat(_RX_LOG)
        return True
    except OSError:
        return False


def _rx_log_flush(lines):
    import os
    try:
        try:
            if os.stat(_RX_LOG)[6] > _RX_LOG_MAX:
                open(_RX_LOG, "w").close()
        except OSError:
            pass
        with open(_RX_LOG, "a") as f:
            for ms, raw in lines:
                f.write("{} {}\n".format(ms, raw))
    except Exception as e:
        print("claude_buddy: rx log write failed:", e)


def _battery():
    """Battery snapshot in the shape buddy_protocol expects:
    ``{pct, mV, mA, usb}``. Falls back to a benign stub if the power
    chip can't be read, so a flaky I2C read never kills the app."""
    try:
        ma = int(M5.Power.getBatteryCurrent())
        return {
            "pct": int(M5.Power.getBatteryLevel()),
            "mV": int(M5.Power.getBatteryVoltage()),
            "mA": -ma,  # reference convention: negative = charging
            "usb": ma > _ON_BATTERY_MA,
        }
    except Exception as e:
        print("claude_buddy: battery read failed:", e)
        return {"pct": 0, "mV": 0, "mA": 0, "usb": True}


_TOOL_SUMMARY_KEYS = ("command", "file_path", "path", "pattern", "url", "query", "description")


def _tool_from_evt(raw):
    """Return ``(tool_name, summary, tool_id)`` for the last tool_use block in a
    Desktop ``evt`` line, or None. Called from the BLE callback, so it
    only parses and returns plain data — no LCD work here."""
    import json
    try:
        msg = json.loads(bytes(raw).decode("utf-8"))
    except Exception:
        return None
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, list):
        return None
    found = None
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            found = block
    if found is None:
        return None
    inp = found.get("input") or {}
    summary = ""
    if isinstance(inp, dict):
        for key in _TOOL_SUMMARY_KEYS:
            val = inp.get(key)
            if isinstance(val, str) and val:
                summary = val
                break
    summary = " ".join(summary.split())[:120]
    return (str(found.get("name") or "?"), summary, found.get("id"))


_VEL_KEY = "velr"  # NVS key (buddy namespace): last 8 response times, "12,5,30"
_VEL_KEEP = 8


def _median(values):
    if not values:
        return 0
    v = sorted(values)
    return v[len(v) // 2]


class _Protocol(buddy_protocol.BuddyProtocol):
    """Shared protocol, adjusted to the Hardware Buddy reference
    (anthropics/claude-desktop-buddy REFERENCE.md) for the Core2:

    - Status ack shaped ``{"ack":"status","ok":true,"data":{...}}``;
      the shared module sends those fields at the top level.
    - ``stats.vel`` is the median seconds-to-approve over the last 8
      approvals answered on the device (0 = no data), as the reference
      firmware reports it. The shared module sends approvals per
      minute, which Claude Desktop shows as "--s".

    Overriding here keeps the Cardputer bundle unchanged until it can be
    tested; see buddy/device-core2/README.md "Status reply"."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._prompt_seen = (None, 0)  # (prompt id, ticks_ms first seen)
        self._vel = []
        try:
            raw = buddy_state._get_str(_VEL_KEY, "")
            self._vel = [int(x) for x in raw.split(",") if x][-_VEL_KEEP:]
        except Exception as e:
            print("claude_buddy: vel load failed:", e)

    def _on_heartbeat(self, hb):
        p = hb.get("prompt")
        pid = p.get("id") if isinstance(p, dict) else None
        if pid and pid != self._prompt_seen[0]:
            self._prompt_seen = (pid, time.ticks_ms())
        super()._on_heartbeat(hb)

    def send_permission(self, decision):
        pid = self._pending.get("id")
        seen_id, seen_ms = self._prompt_seen
        ok = super().send_permission(decision)
        if ok and decision == "once" and pid and pid == seen_id:
            secs = max(1, time.ticks_diff(time.ticks_ms(), seen_ms) // 1000)
            self._vel = (self._vel + [min(secs, 65535)])[-_VEL_KEEP:]
            try:
                buddy_state._set_str(_VEL_KEY, ",".join(str(v) for v in self._vel))
            except Exception as e:
                print("claude_buddy: vel save failed:", e)
        return ok

    def confirm_unpair(self):
        # Unpair wipes the shared stats; wipe ours with them.
        super().confirm_unpair()
        self._vel = []
        buddy_state._erase(_VEL_KEY)

    def _build_status_ack(self):
        flat = super()._build_status_ack()
        flat.pop("ack", None)
        stats = dict(flat.get("stats") or {})
        stats["vel"] = _median(self._vel)
        flat["stats"] = stats
        return {"ack": "status", "ok": True, "data": flat}


def _drain_buttons(ms=400):
    """Swallow the press that launched us (and any bounce) so it
    doesn't count as an approve/deny."""
    end = time.ticks_add(time.ticks_ms(), ms)
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        M5.update()
        time.sleep_ms(20)
    for b in (M5.BtnA, M5.BtnB, M5.BtnC):
        b.wasPressed()
        b.wasHold()


def run():
    print("claude_buddy: run() start (core2)")

    # Buddy is BLE-only. ESP32 shares one 2.4 GHz radio between WiFi and
    # BLE; the launcher doesn't start WiFi on Core2, but if anything
    # else did, take it down before NimBLE comes up (same reasoning as
    # the Cardputer app).
    try:
        import network
        sta = network.WLAN(network.STA_IF)
        if sta.active():
            try:
                sta.disconnect()
            except OSError:
                pass
            sta.active(False)
            time.sleep_ms(1000)
            print("claude_buddy: wifi off")
    except Exception as e:
        print("claude_buddy: wifi disable warning:", e)

    ui = buddy_ui.BuddyUI()
    state = buddy_state.BuddyState()
    ui.update_identity(state.name, state.owner)

    buddy_chars.sweep_partials()
    chars = buddy_chars.CharReceiver()

    proto_holder = {"p": None}
    rx_log_on = _rx_log_enabled()
    rx_pending = []
    if rx_log_on:
        print("claude_buddy: rx log ->", _RX_LOG)

    pending_tool = [None]

    def on_line(raw):
        # Desktop forwards raw chat events; buddy_protocol drops them,
        # but the latest tool call is worth showing while something is
        # waiting for approval in the app. Cheap prefix check first so
        # heartbeats and status polls don't pay for a second parse.
        if raw[:8] == b'{"evt":"':
            tool = _tool_from_evt(raw)
            if tool is not None:
                ui.note_tool(*tool)  # state only; drawing happens in the loop
                pending_tool[0] = True
        if rx_log_on and len(rx_pending) < 30:
            try:
                text = bytes(raw[:_RX_LINE_MAX]).decode("utf-8", "replace")
            except Exception:
                text = repr(raw[:_RX_LINE_MAX])
            rx_pending.append((time.ticks_ms(), text))
        p = proto_holder["p"]
        if p is not None:
            p.on_line(raw)

    # BLE callbacks only stash state; the main loop does the LCD work
    # so SPI writes never interleave (see the Cardputer app for the
    # long version of this story).
    pending_state = [None]
    pending_passkey = [None]

    def on_passkey(pk):
        pending_passkey[0] = pk

    ble = None

    def on_state_change(s):
        # UIFlow 2.0's BLE build has no pairing API, so "connected" is
        # terminal — treat it as "encrypted" so the UI and protocol
        # advance.
        effective = s
        if s == "connected" and ble is not None and not ble.pairing_supported:
            effective = "encrypted"
        print("claude_buddy: state", s, "->", effective)
        pending_state[0] = effective
        if effective == "encrypted":
            p = proto_holder["p"]
            if p is not None:
                p.send_hello()

    gc.collect()
    print("claude_buddy: gc done, free=", gc.mem_free())
    ble = buddy_ble.BuddyBLE(
        on_line=on_line,
        on_passkey=on_passkey,
        on_state=on_state_change,
    )

    proto = _Protocol(
        state=state,
        ui=ui,
        chars=chars,
        ble=ble,
        battery_reader=_battery,
    )
    proto_holder["p"] = proto

    ui.set_device_name(ble.advertised_name)
    ui.update_footer(state.stats(), _battery())
    print("Claude Buddy up as", ble.advertised_name)

    M5.BtnC.setHoldThresh(_EXIT_HOLD_MS)
    _drain_buttons()

    last_footer_ms = time.ticks_ms()
    last_toast_ms = 0
    footer_interval = 3000
    toast_dwell_ms = 1500

    try:
        while True:
            new_state = pending_state[0]
            if new_state is not None:
                pending_state[0] = None
                ui.set_connection(new_state)
                if new_state == "encrypted":
                    ui.clear_passkey()
            new_pk = pending_passkey[0]
            if new_pk is not None:
                pending_passkey[0] = None
                ui.show_passkey(new_pk)
            if pending_tool[0]:
                pending_tool[0] = None
                ui.refresh_waiting()

            M5.update()
            approve = M5.BtnA.wasPressed()
            deny = M5.BtnB.wasPressed()
            quit_ = M5.BtnC.wasHold()
            c_tap = M5.BtnC.wasClicked()

            # Called every iteration: this is also where the protocol
            # rolls over the 30 s unpair timeout.
            unpair_active = proto.unpair_pending()

            if approve:
                if unpair_active:
                    proto.confirm_unpair()
                elif not proto.send_permission("once"):
                    ui.flash_toast(
                        "Approve it in the Claude app" if ui.waiting_in_app()
                        else "Nothing to approve",
                        buddy_ui.GRAY_DIM,
                    )
                last_toast_ms = time.ticks_ms()
            elif deny:
                if unpair_active:
                    proto.cancel_unpair()
                elif not proto.send_permission("deny"):
                    ui.flash_toast(
                        "Deny it in the Claude app" if ui.waiting_in_app()
                        else "Nothing to deny",
                        buddy_ui.GRAY_DIM,
                    )
                last_toast_ms = time.ticks_ms()
            elif quit_:
                return
            elif c_tap:
                ui.flash_toast("Hold C to exit", buddy_ui.GRAY_DIM)
                last_toast_ms = time.ticks_ms()

            ui.tick()

            if rx_pending:
                batch = rx_pending[:]
                del rx_pending[:len(batch)]
                _rx_log_flush(batch)

            now = time.ticks_ms()
            if time.ticks_diff(now, last_footer_ms) >= footer_interval:
                state.tick_nap()
                ui.update_footer(state.stats(), _battery())
                last_footer_ms = now
            if last_toast_ms and time.ticks_diff(now, last_toast_ms) >= toast_dwell_ms:
                ui.restore_button_hints()
                last_toast_ms = 0

            time.sleep_ms(40)
    finally:
        try:
            M5.Power.setVibration(0)
        except Exception:
            pass
        try:
            ble.deinit()
        except Exception as e:
            print("claude_buddy: deinit warning:", e)
        try:
            M5.Lcd.fillScreen(buddy_ui.BLACK)
        except Exception as e:
            print("claude_buddy: screen-clear warning:", e)
        time.sleep_ms(200)
        machine.reset()


run()
