"""Message dispatcher: JSON line in, JSON line out.

The BLE layer hands us raw byte buffers that happen to be lines. This
module parses them, routes to the right handler, and returns bytes
that the BLE layer notifies back. All protocol shapes live here so
the BLE transport stays dumb.

Host → device messages (what we receive):
    {"cmd":"status"}                             - ask us to ack state
    {"cmd":"name", "name":"..."}                 - rename the buddy
    {"cmd":"owner", "owner":"..."}               - set owner string
    {"cmd":"unpair"}                             - wipe pairing + state
    {"cmd":"char_begin"|"file"|"chunk"|...}      - folder push
    heartbeat, no "cmd": {"total":N, "running":N, "waiting":N,
        "msg":"...", "entries":N, "tokens":N, "tokens_today":N,
        "prompt":{"id":"...","tool":"...","hint":"..."}}
    quota heartbeat from the BLE companion (scripts/quota_push.py):
        {"five_h_util":N, "week_util":N, "sonnet_util":N}  - utilization %
        (5-hour / 7-day-all / 7-day-Sonnet) from codexbar, plus matching
        "*_color" RGB ints (host-resolved from the pace stage) for the bars

Device → host messages (what we emit):
    {"ack":"status","name":..,"sec":true,"bat":{...},"sys":{...},"stats":{...}}
    {"ack":"<other>","ok":true}
    {"cmd":"permission","id":"<prompt id>","decision":"once"|"deny"}
    {"cmd":"hello","name":..,"version":...}

Heartbeat detection: we key on absence of "cmd"/"ack" and presence of
one of the heartbeat-shape fields. That way we don't need the desktop
to tag heartbeats explicitly (it doesn't in the reference). Any other
unknown message is logged and ignored rather than crashing.
"""

import json
import time


FIRMWARE_VERSION = "m5buddy-0.1"

_HEARTBEAT_FIELDS = (
    "total", "running", "waiting", "tokens", "tokens_today", "entries",
    # Quota fields from the BLE companion (scripts/quota_push.py). Listed
    # here so a quota-only heartbeat — e.g. {"five_h_util":N,"week_util":N,
    # "sonnet_util":N} with none of the Claude.app fields — is still
    # recognized as a heartbeat and reaches the UI, instead of falling
    # through to "unclassified". *_color are RGB ints (resolved host-side
    # from the codexbar pace stage) that the device paints as the bar fill.
    "five_h_util", "week_util", "sonnet_util",
    "five_h_color", "week_color", "sonnet_color",
)

# Unpair is destructive (wipes name/owner/stats and disconnects). The
# BLE link on UIFlow 2.0 is unauthenticated — see buddy_ble.py — so any
# central in range could otherwise issue this. We hold the request
# pending until the user presses Y on the device, or auto-cancel after
# this many ms.
_UNPAIR_CONFIRM_TIMEOUT_MS = 30_000


class BuddyProtocol:
    """Wire-format bridge between the BLE transport and app state."""

    def __init__(self, state, ui, chars, ble, battery_reader, permission_pending=None):
        self.state = state
        self.ui = ui
        self.chars = chars
        self.ble = ble
        self._battery = battery_reader
        # Tracks the currently-displayed prompt so button handlers know
        # which id to answer. None means "nothing pending".
        self._pending = permission_pending or {"id": None, "tool": None, "hint": None}
        self._boot_ms = time.ticks_ms()
        # Unpair confirmation state. _unpair_pending_ms == 0 means
        # nothing pending; nonzero is the time.ticks_ms() when the
        # request arrived. The run loop polls unpair_pending() at its
        # tick rate, which auto-resolves the timeout.
        self._unpair_pending_ms = 0

    # ----- inbound

    def on_line(self, raw: bytes) -> None:
        try:
            msg = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError) as e:
            print("buddy_protocol: bad line:", e, raw[:60])
            return
        if not isinstance(msg, dict):
            print("buddy_protocol: non-object msg:", type(msg))
            return

        cmd = msg.get("cmd")
        if cmd == "status":
            self._send(self._build_status_ack())
            return
        if cmd == "name":
            new = msg.get("name", "").strip()
            if new:
                self.state.set_name(new)
                self.ui.update_identity(self.state.name, self.state.owner)
            self._send({"ack": "name", "ok": bool(new), "name": self.state.name})
            return
        if cmd == "owner":
            self.state.set_owner(msg.get("owner", "").strip())
            self.ui.update_identity(self.state.name, self.state.owner)
            self._send({"ack": "owner", "ok": True, "owner": self.state.owner})
            return
        if cmd == "unpair":
            # Defer the actual wipe until the user confirms on the
            # device. See _UNPAIR_CONFIRM_TIMEOUT_MS for the threat
            # model. Re-arming the timer on duplicate requests is fine
            # — the user just sees the prompt persist.
            self._unpair_pending_ms = time.ticks_ms() or 1  # avoid 0
            self.ui.show_unpair_prompt()
            self._send({
                "ack": "unpair",
                "ok": False,
                "pending": True,
                "err": "awaiting on-device confirmation",
            })
            return
        if cmd in ("char_begin", "file", "chunk", "file_end", "char_end"):
            ack = self.chars.handle(msg)
            if ack:
                self._send(ack)
            return
        if cmd is not None:
            print("buddy_protocol: unknown cmd:", cmd)
            return

        # No "cmd" field → treat as heartbeat if it looks like one.
        if any(k in msg for k in _HEARTBEAT_FIELDS) or "prompt" in msg:
            self._on_heartbeat(msg)
            return

        # The desktop sends periodic {"time": <epoch>} ticks so the device
        # can correlate wall-clock time with its own uptime. We don't
        # render time anywhere yet, but we recognize the shape so it
        # doesn't spam the "unclassified msg" log. Don't route this to
        # _on_heartbeat — that would set self._last to a payload with no
        # queue/token fields and blank out the cached UI state.
        if "time" in msg and len(msg) == 1:
            return

        # The desktop also streams raw chat events — {"evt":..,"role":..,
        # "content":..} — forwarded from the active Claude session. These
        # are interesting raw material for a future "show the latest
        # assistant line on the buddy screen" feature, but we don't have
        # UI for them yet. Recognize and drop so the log stays quiet.
        if "evt" in msg and "role" in msg:
            return

        print("buddy_protocol: unclassified msg, keys:", list(msg.keys()))

    def _on_heartbeat(self, hb: dict) -> None:
        self.ui.update_heartbeat(hb)
        prompt = hb.get("prompt")
        if prompt and prompt.get("id"):
            self._pending = {
                "id": prompt.get("id"),
                "tool": prompt.get("tool"),
                "hint": prompt.get("hint"),
            }
        elif self._pending.get("id") is not None:
            # Desktop cleared the prompt — forget it so buttons don't
            # answer a stale id next time someone taps A.
            self._pending = {"id": None, "tool": None, "hint": None}

    # ----- outbound

    def send_hello(self) -> None:
        """Called once on BLE encryption-established to announce ourselves.

        The desktop identifies us by the pairing address, but this
        gives it our friendly name + firmware version up front so the
        UI can render before the first status round-trip.
        """
        ok = self._send({
            "cmd": "hello",
            "name": self.state.name,
            "owner": self.state.owner,
            "version": FIRMWARE_VERSION,
        })
        print("buddy_protocol: send_hello ok=", ok)

    def _send(self, obj: dict) -> bool:
        line = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        return self.ble.send_line(line)

    def send_permission(self, decision: str) -> bool:
        """Answer the currently-displayed prompt with 'once' or 'deny'.

        Returns True if a prompt was pending and the answer was queued.
        False if there's nothing to answer — callers can use this to
        suppress the audible click for no-op button presses.
        """
        pid = self._pending.get("id")
        if not pid:
            return False
        self._send({"cmd": "permission", "id": pid, "decision": decision})
        self.state.record_decision(decision)
        self.ui.flash_decision(decision)
        # Clear pending immediately; if the host still wants an answer
        # it'll re-advertise in the next heartbeat.
        self._pending = {"id": None, "tool": None, "hint": None}
        return True

    def has_pending(self) -> bool:
        return self._pending.get("id") is not None

    def unpair_pending(self) -> bool:
        """True iff a host-issued unpair is awaiting on-device Y/N.

        Auto-resolves the timeout: if more than
        _UNPAIR_CONFIRM_TIMEOUT_MS has elapsed since the request
        arrived, we clear the pending state and (best-effort) emit a
        timeout ack so the host UI doesn't spin forever. Safe to call
        at the run-loop tick rate; idempotent until the next unpair
        request arrives.
        """
        if not self._unpair_pending_ms:
            return False
        elapsed = time.ticks_diff(time.ticks_ms(), self._unpair_pending_ms)
        if elapsed > _UNPAIR_CONFIRM_TIMEOUT_MS:
            self._unpair_pending_ms = 0
            self.ui.clear_unpair_prompt()
            # Best-effort ack; if the host is already gone the send
            # just no-ops. Timeout is a normal outcome, not an error.
            self._send({
                "ack": "unpair",
                "ok": False,
                "timed_out": True,
                "err": "no on-device confirmation",
            })
            return False
        return True

    def confirm_unpair(self) -> None:
        """User pressed Y at the device — actually wipe state."""
        if not self.unpair_pending():
            return
        self._unpair_pending_ms = 0
        # Ack first while the link is still up; the desktop closes on
        # this ack, so we send before tearing down. 200ms flush window
        # mirrors the original (pre-confirmation) flow.
        self._send({"ack": "unpair", "ok": True, "confirmed": True})
        time.sleep_ms(200)
        self.state.reset_all()
        self.ble.disconnect()
        self.ble.forget_bonds()
        self.ui.clear_unpair_prompt()
        self.ui.update_identity(self.state.name, self.state.owner)

    def cancel_unpair(self) -> None:
        """User pressed N at the device — clear pending, notify host."""
        if not self.unpair_pending():
            return
        self._unpair_pending_ms = 0
        self.ui.clear_unpair_prompt()
        self._send({"ack": "unpair", "ok": False, "cancelled": True})

    def _build_status_ack(self) -> dict:
        import gc

        uptime_s = time.ticks_diff(time.ticks_ms(), self._boot_ms) // 1000
        bat = self._battery()
        ack = {
            "ack": "status",
            "name": self.state.name,
            "owner": self.state.owner,
            # sec reflects whether the underlying link is encrypted+paired.
            # True on builds with pairing API; False on stock UIFlow 2.0.
            "sec": bool(getattr(self.ble, "encrypted", False)),
            "bat": bat,
            "sys": {
                "up": uptime_s,
                "heap": gc.mem_free(),
            },
            "stats": self.state.stats(),
            "version": FIRMWARE_VERSION,
        }
        return ack
