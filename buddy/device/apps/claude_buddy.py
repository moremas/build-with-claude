"""Claude Buddy for the M5 Cardputer-Adv.

This is a port of the Basic's `buddy_app.py` to a device with a QWERTY
matrix keyboard instead of three face buttons, a 240x135 LCD instead of
320x240, and no accessible battery IC (Cardputer-Adv ships with a
different power rail that we don't bother reading here). The wire
protocol, BLE stack, persistent state, and character-receive logic are
unchanged — we reuse `buddy_ble`, `buddy_protocol`, `buddy_state`, and
`buddy_chars` byte-for-byte from the Basic build. Only the I/O layer
(input → UI) is Cardputer-specific.

### Install layout

UIFlow 2.0's launcher shows any `*.py` inside `/flash/apps/` in its
"App List" menu. The peer modules go alongside this file in the same
directory, and we prepend `/flash/apps/` to sys.path on entry so
`import buddy_ble` etc. resolves. This keeps the whole bundle
self-contained in one folder — no touching /flash/ root, no clobbering
UIFlow's own main.py/boot.py.

### Input mapping

The Cardputer has a full keyboard, so we pick intuitive letters rather
than mimicking BtnA/B/C. The mapping is shown in the hint strip:

  Y / y / Enter   → approve once
  N / n           → deny
  Q / q / ESC     → quit back to the UIFlow App List

MatrixKeyboard.get_key() returns single-character strings for printable
keys and small integer codes for specials. We accept both forms for the
keys that have both — Enter (0x0D) and Escape (0x1B).

### Return-to-menu

UIFlow 2.0 has no return-to-launcher API; when a user app's `run()`
ends, the launcher does not repaint and the screen stays frozen on
whatever the app drew last. The established workaround (see
`hello_cardputer.py`) is to soft-reboot via `machine.reset()` on exit,
which lands the user back at the launcher automatically. We do that
here, in the `finally` block, *after* tearing BLE down cleanly.
"""

import sys

# Make our peer modules importable *before* the first `import buddy_ble`
# below, otherwise we ImportError at load time and the launcher has no
# graceful way to show it.
#
# UIFlow 2.0's default sys.path on this build is roughly:
#   ['', '.frozen', '/lib', '/system', '/flash/libs']
# Notably /flash itself is NOT on the path, even though that's where
# boot.py and main.py live. We put the buddy_* peer modules at /flash/
# root (to keep them out of the App List, which scans /flash/apps/),
# and claude_buddy.py lives in /flash/apps/. Prepend both so imports
# resolve regardless of which layout a future install lands on.
for _p in ("/flash", "/flash/apps"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import time

import M5
import machine
from hardware import MatrixKeyboard

import buddy_ble
import buddy_chars
import buddy_protocol
import buddy_state
import buddy_ui_cp as buddy_ui


# ---- battery reader
#
# M5.Power on UIFlow 2.0 wraps the AXP2101 PMIC on the Cardputer-Adv.
# Internal 120mAh + backpack 1400mAh are wired in parallel on the same
# BAT rail, so AXP2101 sees them as a single ~1520mAh source.
#
# Remaining-time estimate: use getBatteryCurrent() if the AXP2101
# returns a real value (> 0); otherwise fall back to a fixed 90mA
# typical draw for BLE-active Cardputer-Adv. `remaining_min` is None
# while charging so the UI can suppress the estimate.
_TOTAL_MAH = 1520
_FALLBACK_MA = 90

def _read_battery():
    p = M5.Power
    pct = max(0, min(100, p.getBatteryLevel()))
    charging = bool(p.isCharging())
    mA = p.getBatteryCurrent()
    if mA <= 0:
        mA = _FALLBACK_MA
    remaining_min = None if charging else int(_TOTAL_MAH * pct / 100 * 60 / mA)
    return {
        "pct": pct,
        "mV": p.getBatteryVoltage(),
        "mA": mA,
        "usb": charging,
        "remaining_min": remaining_min,
    }


# ---- key adapter
#
# We translate the raw key from MatrixKeyboard into one of three
# intents: APPROVE / DENY / QUIT / None. That keeps the main loop dumb
# — it doesn't care which key was pressed, just what it means. Picking
# the mapping here (rather than sprinkling magic constants through the
# loop) also makes it trivial to add synonyms later (e.g. space = once).
_INTENT_APPROVE = "approve"
_INTENT_DENY = "deny"
_INTENT_QUIT = "quit"


def _intent_for_key(k):
    """Return an intent string or None for an unrecognized key.

    MatrixKeyboard.get_key() on this UIFlow 2.0 build hands back the
    raw ASCII byte value as an **int** — e.g. 0x59 for 'Y', 0x6E for
    'n', 0x1B for Escape. Enter on this firmware reports as 0x0A
    (LF), not 0x0D (CR) — main.py:_intent_for_key in the launcher
    has the same accommodation. We accept both 0x0A and 0x0D so a
    future build that flips back doesn't silently break Enter here.
    Older builds returned a length-1 string instead; accepted too.
    Ints in the printable range 0x20..0x7E are converted to their
    single-char string and fall through to the string matcher below.

    The previous version of this function treated every int except
    0x0D / 0x1B as unknown, which silently dropped every Y/N/Q press
    — that's the "keyboard buttons don't work" symptom we saw on
    hardware. The Enter-key bug behind it (0x0A reports as not-0x0D)
    is what motivates the explicit (0x0A, 0x0D) check here too.
    """
    if k is None:
        return None
    if isinstance(k, int):
        if k in (0x0A, 0x0D):
            return _INTENT_APPROVE
        if k == 0x1B:
            return _INTENT_QUIT
        if 0x20 <= k <= 0x7E:
            k = chr(k)
        else:
            return None
    if isinstance(k, (bytes, bytearray)) and len(k) == 1:
        k = chr(k[0])
    if not isinstance(k, str) or not k:
        return None
    ch = k.lower()
    if ch in ("y", "\r", "\n"):
        return _INTENT_APPROVE
    if ch == "n":
        return _INTENT_DENY
    if ch in ("q", "\x1b"):
        return _INTENT_QUIT
    return None


def run():
    # Per-step prints so a hard fault during init (NimBLE Guru
    # Meditation, LCD driver crash, etc.) leaves a breadcrumb on the
    # serial console pointing at which step faulted. C-level crashes
    # bypass the launcher's try/except and reboot the chip, so the
    # last print before reboot is the only diagnostic we get.
    print("claude_buddy: run() start")

    # Power WiFi down before bringing up BLE. ESP32 shares a single
    # 2.4 GHz radio between WiFi and BLE, with software coexistence
    # arbitrating between them. The launcher (main.py) connects to
    # the event WiFi at boot, which leaves the radio actively
    # servicing beacons/keepalives by the time we get here.
    # `bluetooth.BLE().active(True)` in `_ensure_stack` cold-starts
    # the NimBLE controller, and in busy RF environments — many
    # nearby BLE peers, lots of WiFi traffic — that init
    # intermittently faults at the C layer and reboots the chip with
    # no Python-catchable error. We saw this consistently with the
    # crash log ending at `pre_active= False`. Buddy is BLE-only, so
    # taking WiFi down for the duration of the app is harmless; the
    # launcher reconnects on the next reboot via main.py.
    try:
        import network
        sta = network.WLAN(network.STA_IF)
        if sta.active():
            try:
                sta.disconnect()
            except OSError:
                pass
            sta.active(False)
        print("claude_buddy: wifi off")
    except Exception as e:
        # Defensive — if `network` isn't importable on this build, or
        # the WLAN object behaves unexpectedly, we'd rather continue
        # and risk the original coexistence crash than fail the app
        # outright. The print is enough to investigate later.
        print("claude_buddy: wifi disable warning:", e)
    # Drain the radio scheduler so WiFi tx queues finish before BLE
    # init takes over the controller. 1000 ms (was 200 ms) is the
    # value that finally got us past intermittent NimBLE
    # active(True) C-faults on a busy show floor — ESP32's WiFi
    # tear-down is more leisurely than its connect path, and the
    # 200 ms we tried first wasn't enough to fully release the
    # radio before BLE asks for it.
    time.sleep_ms(1000)

    ui = buddy_ui.BuddyUI()
    print("claude_buddy: ui ready")
    state = buddy_state.BuddyState()
    print("claude_buddy: state ready")
    ui.update_identity(state.name, state.owner)

    buddy_chars.sweep_partials()
    chars = buddy_chars.CharReceiver()
    print("claude_buddy: chars ready")

    # Protocol needs a handle on the BLE object (for disconnect /
    # forget_bonds), and BLE needs the on_line callback which needs the
    # protocol. Same indirection trick as the Basic: stash the protocol
    # in a 1-slot dict that the callback reads at event time.
    proto_holder = {"p": None}

    def on_line(raw):
        p = proto_holder["p"]
        if p is not None:
            p.on_line(raw)

    # BLE callbacks dispatch from micropython.schedule context, which
    # runs between bytecodes on the main thread. That means a
    # callback can land *inside* a Python-level UI routine that's
    # mid-way through a sequence of SPI ops to the LCD, interleaving
    # writes and leaving the panel in an inconsistent state. We avoid
    # that by having callbacks only mutate plain Python state and
    # letting the main loop drain it into UI calls. send_hello stays
    # in the callback because it's BLE-only — no LCD bus contention.
    pending_state = [None]
    pending_passkey = [None]

    def on_passkey(pk):
        pending_passkey[0] = pk

    # Pre-bind so on_state_change's closure can resolve `ble` even if
    # the IRQ fires during BuddyBLE.__init__ (a central that connects
    # mid-init can deliver _IRQ_CENTRAL_CONNECT before the
    # `ble = BuddyBLE(...)` assignment below completes). Without this
    # pre-bind, on_state_change raises NameError in IRQ context and
    # the link is silently lost. The `is None` guard means the very
    # first event during init won't get the pairing-aware remap, but
    # any subsequent event will — and the run loop stays alive.
    ble = None

    def on_state_change(s):
        # The stripped UIFlow 2.0 BLE build doesn't fire
        # _IRQ_ENCRYPTION_UPDATE, so "connected" is terminal. Remap
        # it to "encrypted" so the UI advances past the PAIR... badge
        # and the protocol starts emitting its hello.
        effective = s
        if s == "connected" and ble is not None and not ble.pairing_supported:
            effective = "encrypted"
        print("claude_buddy: state", s, "->", effective)
        pending_state[0] = effective
        if effective == "encrypted":
            p = proto_holder["p"]
            if p is not None:
                p.send_hello()

    # Run a full GC pass before NimBLE init. The controller
    # allocates several large chunks during active(True) — bonding
    # store, advertising buffers, host/controller queues — and a
    # fragmented MicroPython heap at this point has been observed
    # to push allocation onto a path that C-faults instead of
    # raising MemoryError. Cheap insurance to call gc.collect() here
    # since we have no other allocation pressure between launcher
    # exit and BLE init.
    import gc
    gc.collect()
    print("claude_buddy: gc done, free=", gc.mem_free())
    print("claude_buddy: constructing BuddyBLE")
    ble = buddy_ble.BuddyBLE(
        on_line=on_line,
        on_passkey=on_passkey,
        on_state=on_state_change,
    )
    print("claude_buddy: BuddyBLE returned")

    proto = buddy_protocol.BuddyProtocol(
        state=state,
        ui=ui,
        chars=chars,
        ble=ble,
        battery_reader=_read_battery,
    )
    proto_holder["p"] = proto

    ui.update_footer(state.stats(), _read_battery())
    print("Claude Buddy up as", ble.advertised_name)

    # Keyboard: debounce 400 ms before polling so the key used to pick
    # this app from App List doesn't count as an intent. Same pattern
    # hello_cardputer.py uses — confirmed by testing there that
    # MatrixKeyboard.get_key() is reliable inside an app context as
    # long as we tick() before reading.
    kb = MatrixKeyboard()
    time.sleep_ms(400)

    last_footer_ms = time.ticks_ms()
    last_toast_ms = 0
    footer_interval = 3000
    toast_dwell_ms = 1500

    try:
        while True:
            # Drain BLE-callback-deferred UI work in main-loop context
            # so LCD writes don't interleave with the periodic footer
            # paint or the prompt rendering kicked off by protocol
            # events. set_connection in particular repaints the whole
            # header strip, which is several SPI transactions long.
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

            kb.tick()
            k = kb.get_key()
            intent = _intent_for_key(k)

            # An active unpair confirmation outranks any permission
            # prompt: pressing Y here means "yes, wipe me", not "yes,
            # approve the pending tool call". The unpair_pending()
            # check is also where the protocol layer rolls over the
            # 30s timeout, so we want it called every loop iteration
            # regardless of what key (if any) was pressed.
            unpair_active = proto.unpair_pending()

            if intent == _INTENT_APPROVE:
                if unpair_active:
                    proto.confirm_unpair()
                elif not proto.send_permission("once"):
                    ui.flash_toast("Y: no prompt", buddy_ui.GRAY_DIM)
                    ui.update_footer(state.stats(), _read_battery())
                last_toast_ms = time.ticks_ms()
            elif intent == _INTENT_DENY:
                if unpair_active:
                    proto.cancel_unpair()
                elif not proto.send_permission("deny"):
                    ui.flash_toast("N: no prompt", buddy_ui.GRAY_DIM)
                last_toast_ms = time.ticks_ms()
            elif intent == _INTENT_QUIT:
                # Break out so the `finally` block tears BLE down
                # cleanly before we reboot back to the launcher. If
                # an unpair is pending, leaving without confirming
                # cancels it on the device side; the host already has
                # an "ok:false,pending:true" ack and a subsequent
                # disconnect will tell it the request didn't go
                # through.
                return

            now = time.ticks_ms()
            if time.ticks_diff(now, last_footer_ms) >= footer_interval:
                state.tick_nap()
                ui.update_footer(state.stats(), _read_battery())
                last_footer_ms = now
            if last_toast_ms and time.ticks_diff(now, last_toast_ms) >= toast_dwell_ms:
                ui.restore_button_hints()
                last_toast_ms = 0

            # 40 ms matches buddy_app.py — fast enough for responsive
            # key handling, slow enough that the BLE IRQ gets plenty
            # of room. MatrixKeyboard handles debounce internally on
            # tick(), so no additional delay is needed for the input
            # path specifically.
            time.sleep_ms(40)
    finally:
        # Mirror buddy_app.py's teardown ordering: BLE first so a late
        # async disconnect event can't repaint Buddy chrome on top of
        # the launcher (cf. the comment in BuddyBLE.deinit), then wipe
        # the screen to black, then hand control back to UIFlow.
        try:
            ble.deinit()
        except Exception as e:
            print("claude_buddy: deinit warning:", e)
        try:
            M5.Lcd.fillScreen(buddy_ui.BLACK)
        except Exception as e:
            print("claude_buddy: screen-clear warning:", e)
        # UIFlow has no launcher-return API; machine.reset() is the
        # only way back to App List. Same pattern hello_cardputer.py
        # uses. Brief pause so any trailing BLE log doesn't get
        # truncated mid-line on the USB console.
        time.sleep_ms(200)
        machine.reset()


# UIFlow 2.4.x's App List has been observed to invoke apps both as
# __main__ (file run directly) and via import (when picked through
# the menu vs. the file system, depending on version). The previous
# if/else with both arms calling run() was just dispatching to
# itself; collapse it so the empirical behavior is the documented
# behavior. The trade-off is that anyone who imports this module
# from CPython for inspection will trigger a BLE init — but the
# imports above (M5, hardware, bluetooth) already only resolve
# on-device, so that path isn't a real use case.
run()
