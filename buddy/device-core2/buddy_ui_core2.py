"""Render the buddy's state to the 320x240 M5Stack Core2 LCD.

Same public API as ``buddy_ui_cp.BuddyUI`` (the Cardputer-Adv view), so
the shared protocol layer (``buddy_protocol``) and the app loop don't
care which display is underneath. Differences from the Cardputer view:

- 320x240 panel, so body text uses DejaVu18 (21 px line height measured
  on hardware) instead of DejaVu9.
- Input is the Core2's three touch buttons (BtnA / BtnB / BtnC), which
  sit in the touch strip *below* the LCD. The hint strip at the bottom
  of the screen labels each one directly above its touch zone.
- The Core2 has a vibration motor; a short buzz announces a newly
  pending permission prompt so you notice it without staring at the
  screen. ``tick()`` must be called from the main loop to stop it.

Layout (all y values are top-left of the glyph cell; drawString uses
TL_DATUM by default on UIFlow 2.0):

    y=0..29    header: "Claude Buddy" + connection badge
    y=30       orange hairline
    y=31..201  main panel (idle / connected / passkey / unpair views)
               connected: queue line y=40, request box y=66..171
    y=176      footer: today's tokens + battery (connected view only)
    y=202      orange hairline
    y=203..239 hint strip: three labels centred over BtnA / BtnB / BtnC
"""

import time

import M5

# Anthropic palette — same values as buddy_ui_cp.py.
ORANGE = 0xCC785C
CREAM = 0xF0EEE6
DARK = 0x1F1F1F
BLACK = 0x000000
WHITE = 0xFFFFFF
GRAY_DIM = 0x333333
GRAY_MID = 0x777777
GREEN = 0x00FF00
CYAN = 0x00FFFF
YELLOW = 0xFFFF00
RED = 0xFF0000

_LCD = M5.Lcd

_W = 320
_H = 240

_HEADER_H = 30
_MAIN_Y = 31
_STRIP_Y = 203
_MAIN_H = _STRIP_Y - 1 - _MAIN_Y  # stop above the strip hairline
_FOOTER_Y = 176
_LINE_H = 22  # DejaVu18 cell is 21 px tall on hardware; +1 for descenders
_BOX_Y = 66   # request box / message area, just under the queue line
_BOX_H = 106  # four 24 px rows, ending above the footer at y=176
_BOX_ROWS = 4

# Horizontal centres of the three Core2 touch buttons (BtnA/B/C zones
# are each a third of the 320 px width).
_BTN_X = (53, 160, 267)

_VIBRATE_LEVEL = 200
_VIBRATE_MS = 300


def _font(name):
    """Select a DejaVu font by attribute name, tolerating builds that
    lack it (falls back to whatever font is current)."""
    try:
        _LCD.setFont(getattr(_LCD.FONTS, name))
    except Exception as e:
        print("buddy_ui_core2: setFont", name, "fallback:", e)
    _LCD.setTextSize(1)


def _clip(text, max_w):
    while text and _LCD.textWidth(text) > max_w:
        text = text[:-1]
    return text


def _wrap(text, max_w, n):
    """Split ``text`` into exactly ``n`` lines that each fit ``max_w`` px
    in the current font; overflow past the last line is dropped."""
    lines = []
    rest = " ".join((text or "").split())
    for _ in range(n):
        line = _clip(rest, max_w)
        # Prefer breaking at a space if one is reasonably close.
        if len(line) < len(rest):
            cut = line.rfind(" ")
            if cut > len(line) // 2:
                line = line[:cut]
        lines.append(line)
        rest = rest[len(line):].lstrip()
    return lines


def _right(pad, text):
    return _W - pad - _LCD.textWidth(text)


def _center(text, cx=_W // 2):
    return cx - _LCD.textWidth(text) // 2


class BuddyUI:
    """320x240 view for the Core2."""

    def __init__(self):
        self._last = {}
        self._passkey = None
        self._unpair_prompt = False
        self._connection_state = "advertising"
        self._prompt = None
        self._identity_name = "Buddy"
        self._identity_owner = ""
        self._device_name = ""
        self._footer = None  # last (stats, battery), repainted with main
        # What each incrementally-drawn row last showed (see _line).
        # Reset by _draw_main whenever the panel is fully repainted.
        self._drawn = {}
        self._buzz_until = 0
        # Latest tool call seen in Desktop's raw "evt" stream, as
        # (tool_name, summary). Shown when Desktop reports something
        # waiting for approval but doesn't send an answerable "prompt"
        # (observed for longer commands, ~70+ chars, and question dialogs).
        self._last_tool = None
        # Tool-call id of the latest tool_use, and the id we last buzzed
        # for, so each new waiting request buzzes exactly once — even
        # when "waiting" never drops to 0 between two requests.
        self._last_tool_id = None
        self._buzzed_tool_id = None
        _LCD.fillScreen(BLACK)
        self._redraw_chrome()

    # ---- public setters (shape matches buddy_ui_cp.BuddyUI)

    def set_device_name(self, name: str):
        """Advertised BLE name, shown on the idle screen so you can
        pick the right device in Claude's Hardware Buddy list."""
        self._device_name = name or ""
        if self.is_idle():
            self._draw_main()

    def set_connection(self, state: str):
        if state == self._connection_state:
            return
        self._connection_state = state
        self._draw_header()
        if state in ("advertising", "disconnected"):
            self._prompt = None
            self._last = {}
        self._draw_main()
        self.restore_button_hints()

    def show_passkey(self, pk: int):
        self._passkey = pk
        self._draw_passkey_overlay()
        self.restore_button_hints()

    def clear_passkey(self):
        if self._passkey is None:
            return
        self._passkey = None
        self._draw_main()

    def show_unpair_prompt(self):
        self._unpair_prompt = True
        self._draw_unpair_overlay()
        self.restore_button_hints()
        self._buzz()

    def clear_unpair_prompt(self):
        if not self._unpair_prompt:
            return
        self._unpair_prompt = False
        self._draw_main()
        self.restore_button_hints()

    def update_heartbeat(self, hb: dict):
        prev_pending = bool(self._prompt)
        prev_in_app = self.waiting_in_app()
        self._last = hb
        self._prompt = hb.get("prompt")
        if self._showing_connected():
            self._draw_connected_main()  # incremental: no flicker
        else:
            self._draw_main()
        if bool(self._prompt) != prev_pending:
            self.restore_button_hints()
            if self._prompt:
                self._buzz()
        elif self.waiting_in_app():
            self._buzz_for_waiting(newly_waiting=not prev_in_app)

    def waiting_in_app(self) -> bool:
        """Desktop says something is waiting for approval, but sent no
        prompt we could answer — it has to be approved in the app."""
        try:
            waiting = int(self._last.get("waiting", 0) or 0)
        except (TypeError, ValueError):
            waiting = 0
        return waiting > 0 and not self._prompt

    def note_tool(self, name: str, summary: str, tool_id=None):
        """Record the latest tool call. State only, no drawing — safe to
        call from the BLE callback, so the "waiting" heartbeat that
        follows a few ms later already sees the right tool and id."""
        self._last_tool = (name or "?", summary or "")
        if tool_id is not None:
            self._last_tool_id = tool_id

    def refresh_waiting(self):
        """Main-loop follow-up to note_tool(): repaint the waiting box
        with the new tool and buzz if this request hasn't buzzed yet."""
        if self.waiting_in_app() and self._showing_connected():
            self._draw_connected_main()
            self._buzz_for_waiting(newly_waiting=False)

    def _showing_connected(self) -> bool:
        """True when the connected view (not idle, not an overlay) is
        what's on screen."""
        return (
            self._connection_state not in ("advertising", "disconnected")
            and self._passkey is None
            and not self._unpair_prompt
        )

    def _buzz_for_waiting(self, newly_waiting: bool):
        """Buzz once per waiting request: on the 0 -> waiting edge, or
        when a tool call we haven't buzzed for yet is the one waiting."""
        tid = self._last_tool_id
        if newly_waiting or (tid is not None and tid != self._buzzed_tool_id):
            self._buzzed_tool_id = tid
            self._buzz()

    def update_identity(self, name: str, owner: str):
        # No separate identity row on the Core2: a custom name replaces
        # the "Claude Buddy" title in the header; the default name
        # ("Buddy") and the owner aren't shown, leaving the panel for
        # the queue, tokens and permission prompt.
        self._identity_name = name or "Buddy"
        self._identity_owner = owner or ""
        self._draw_header()

    def update_footer(self, stats: dict, battery: dict):
        self._footer = (stats, battery)
        if self._showing_connected():
            self._draw_footer(stats, battery)

    def flash_decision(self, decision: str):
        color = GREEN if decision == "once" else RED
        label = "APPROVED" if decision == "once" else "DENIED"
        self.flash_toast(label, color)

    def flash_toast(self, text: str, color: int = CYAN):
        """Overwrite the hint strip with a one-line coloured status."""
        _font("DejaVu18")
        _LCD.fillRect(0, _STRIP_Y, _W, _H - _STRIP_Y, color)
        _LCD.setTextColor(WHITE, color)
        t = _clip(text, _W - 16)
        _LCD.drawString(t, _center(t), _STRIP_Y + 8)

    def restore_button_hints(self):
        """Label each touch button. Modes:
          - unpair confirmation: A confirm / B cancel
          - passkey on screen:   only C (hold to exit)
          - otherwise:           A approve / B deny / C hold-to-exit
        """
        _LCD.fillRect(0, _STRIP_Y - 1, _W, 1, ORANGE)
        _LCD.fillRect(0, _STRIP_Y, _W, _H - _STRIP_Y, DARK)
        _font("DejaVu18")
        if self._unpair_prompt:
            labels = (("Confirm", RED), ("Cancel", CREAM), ("", CREAM))
        elif self._passkey is not None:
            labels = (("", CREAM), ("", CREAM), ("Hold: exit", GRAY_MID))
        else:
            # Approve/Deny are brighter while a prompt is pending so it
            # is obvious they will do something right now.
            live = bool(self._prompt)
            labels = (
                ("Approve", GREEN if live else CREAM),
                ("Deny", RED if live else CREAM),
                ("Hold: exit", GRAY_MID),
            )
        for (text, color), cx in zip(labels, _BTN_X):
            if text:
                _LCD.setTextColor(color, DARK)
                _LCD.drawString(text, _center(text, cx), _STRIP_Y + 8)

    def is_idle(self) -> bool:
        return (
            self._connection_state in ("advertising", "disconnected")
            and self._passkey is None
            and self._prompt is None
            and not self._unpair_prompt
        )

    def tick(self):
        """Main-loop housekeeping: stop the vibration motor once its
        pulse has elapsed. Kept out of the BLE callback path so we never
        sleep inside a scheduled callback."""
        if self._buzz_until and time.ticks_diff(time.ticks_ms(), self._buzz_until) >= 0:
            self._buzz_until = 0
            try:
                M5.Power.setVibration(0)
            except Exception:
                pass

    def tick_idle_burst(self, frame, last_tick):
        return frame, last_tick

    # ---- helpers

    def _buzz(self):
        try:
            M5.Power.setVibration(_VIBRATE_LEVEL)
            self._buzz_until = time.ticks_add(time.ticks_ms(), _VIBRATE_MS)
        except Exception as e:
            print("buddy_ui_core2: vibration unavailable:", e)

    # ---- drawing

    def _draw_header(self):
        _LCD.fillRect(0, 0, _W, _HEADER_H, DARK)
        _LCD.fillRect(0, _HEADER_H, _W, 1, ORANGE)
        _font("DejaVu18")
        icon, color = self._connection_icon()
        name = self._identity_name
        title = name if name and name != "Buddy" else "Claude Buddy"
        title = _clip(title, _W - 24 - _LCD.textWidth(icon))
        _LCD.setTextColor(ORANGE, DARK)
        _LCD.drawString(title, 8, 5)
        _LCD.setTextColor(color, DARK)
        _LCD.drawString(icon, _right(8, icon), 5)

    def _connection_icon(self):
        s = self._connection_state
        if s == "encrypted":
            return ("LINKED", GREEN)
        if s == "connected":
            return ("PAIRING", YELLOW)
        if s == "disconnected":
            return ("OFF", RED)
        return ("WAITING", CYAN)

    def _draw_main(self):
        # Full repaint: used for view changes (connect/disconnect,
        # overlays), not for routine heartbeats.
        self._drawn = {}
        _LCD.fillRect(0, _MAIN_Y, _W, _MAIN_H, BLACK)
        if self._unpair_prompt:
            self._draw_unpair_overlay()
            return
        if self._passkey is not None:
            self._draw_passkey_overlay()
            return
        if self._connection_state in ("advertising", "disconnected"):
            self._draw_idle_main()
            return
        self._draw_connected_main()

    def _draw_idle_main(self):
        _font("DejaVu18")
        _LCD.setTextColor(CREAM, BLACK)
        _LCD.drawString("Waiting to connect...", 8, 44)
        _LCD.setTextColor(GRAY_MID, BLACK)
        _LCD.drawString("In Claude Desktop, open", 8, 78)
        _LCD.drawString("Developer > Hardware Buddy", 8, 102)
        _LCD.drawString("> Connect, and pick:", 8, 126)
        if self._device_name:
            _font("DejaVu24")
            _LCD.setTextColor(ORANGE, BLACK)
            dn = _clip(self._device_name, _W - 16)
            _LCD.drawString(dn, _center(dn), 160)

    def _line(self, key, text, x, y, fg, right=_W - 6):
        """Draw one DejaVu18 row without flicker: skip it entirely if
        it's unchanged since the last draw, otherwise paint the glyphs
        over the old ones (text background = BLACK) and blank only the
        leftover tail to the right. Never blanks the row first."""
        text = text or ""
        sig = (text, fg, x, right)
        if self._drawn.get(key) == sig:
            return
        self._drawn[key] = sig
        _font("DejaVu18")
        text = _clip(text, right - x)
        end = x
        if text:
            _LCD.setTextColor(fg, BLACK)
            _LCD.drawString(text, x, y)
            end = x + _LCD.textWidth(text)
        if end < right:
            _LCD.fillRect(end, y, right - end, _LINE_H, BLACK)

    def _draw_connected_main(self):
        """Connected view, drawn incrementally — called on every
        heartbeat, so it must not blank anything that hasn't changed."""
        hb = self._last
        _font("DejaVu18")  # _wrap/_clip below measure in this font
        queue = "Running {}  Waiting {}  Total {}".format(
            hb.get("running", 0), hb.get("waiting", 0), hb.get("total", 0)
        )
        self._line("queue", queue, 8, 40, WHITE)

        # Lower area (y=66..171, four rows): an orange prompt box
        # (answerable here), a yellow waiting box (approve in the app),
        # or just the status message. Only a change of *kind* clears it.
        if self._prompt:
            p = self._prompt
            hint = _wrap(str(p.get("hint", "")), _W - 24, 3)
            kind, border = "prompt", ORANGE
            rows = (("Allow: " + str(p.get("tool", "?")), ORANGE),) + tuple(
                (line, CREAM) for line in hint
            )
        elif self.waiting_in_app():
            tool = ""
            if self._last_tool:
                name, summary = self._last_tool
                tool = name + (": " + summary if summary else "")
            kind, border = "waiting", YELLOW
            rows = (("Waiting for approval", YELLOW),) + tuple(
                (line, CREAM) for line in _wrap(tool, _W - 24, 2)
            ) + (("Approve it in the Claude app", GRAY_MID),)
        else:
            kind, border = "msg", None
            rows = ((hb.get("msg", ""), GRAY_MID),) + (("", GRAY_MID),) * 3

        if self._drawn.get("box") != kind:
            _LCD.fillRect(0, _BOX_Y, _W, _BOX_H, BLACK)
            if border is not None:
                _LCD.drawRect(4, _BOX_Y, _W - 8, _BOX_H, border)
            self._drawn["box"] = kind
            for i in range(_BOX_ROWS):
                self._drawn.pop("row%d" % i, None)
        x, right = (12, _W - 12) if border is not None else (8, _W - 6)
        for i, (text, color) in enumerate(rows):
            self._line("row%d" % i, text, x, _BOX_Y + 5 + 24 * i, color, right)

        if self._footer is not None:
            self._draw_footer(*self._footer)

    def _draw_unpair_overlay(self):
        if not self._unpair_prompt:
            return
        _LCD.fillRect(0, _MAIN_Y, _W, _MAIN_H, BLACK)
        _font("DejaVu24")
        _LCD.setTextColor(RED, BLACK)
        _LCD.drawString("UNPAIR REQUEST", 8, 44)
        _font("DejaVu18")
        _LCD.setTextColor(CREAM, BLACK)
        _LCD.drawString("From the connected computer.", 8, 86)
        _LCD.drawString("Wipes name, owner and stats,", 8, 112)
        _LCD.drawString("then disconnects.", 8, 136)

    def _draw_passkey_overlay(self):
        if self._passkey is None:
            return
        _LCD.fillRect(0, _MAIN_Y, _W, _MAIN_H, BLACK)
        _font("DejaVu18")
        _LCD.setTextColor(ORANGE, BLACK)
        _LCD.drawString("Pairing passkey:", 8, 44)
        pk_str = "{:06d}".format(self._passkey)
        _font("DejaVu56")
        _LCD.setTextColor(CREAM, BLACK)
        _LCD.drawString(pk_str, _center(pk_str), 80)
        _font("DejaVu18")
        _LCD.setTextColor(GRAY_MID, BLACK)
        _LCD.drawString("Type it into Claude", 8, 150)

    def _draw_footer(self, stats: dict, battery: dict):
        """Bottom row: today's tokens on the left, battery on the right.
        Called on every heartbeat and every 3 s; only repaints what
        changed. (Level/approve/deny stats are still kept in NVS and
        reported to Desktop — they're just not on screen.)"""
        y = _FOOTER_Y + 2
        _font("DejaVu18")
        pct = max(0, min(100, battery.get("pct", 0)))
        label = "{}%{}".format(pct, " USB" if battery.get("usb") else "")
        label_x = _right(8, label)
        if self._drawn.get("foot_r") != label:
            # Blank from just left of the label to the right margin,
            # which also covers a longer previous label ("100% USB").
            self._drawn["foot_r"] = label
            _LCD.fillRect(label_x - 10, y, _W - 6 - (label_x - 10), _LINE_H, BLACK)
            _LCD.setTextColor(CREAM, BLACK)
            _LCD.drawString(label, label_x, y)
        # Longest form that fits beside the battery label. Measured on
        # hardware (DejaVu18): "Today: 10,000,000 tokens" is 229 px but
        # only ~204 px is free next to "100% USB", so fall back to "tok"
        # and then drop "Today:" rather than cutting letters off.
        tok = "{:,}".format(self._last.get("tokens_today", 0))
        room = label_x - 10 - 8
        text = tok + " tok"
        for cand in ("Today: " + tok + " tokens", "Today: " + tok + " tok"):
            if _LCD.textWidth(cand) <= room:
                text = cand
                break
        self._line("foot_l", text, 8, y, CYAN, right=label_x - 10)

    def _redraw_chrome(self):
        _LCD.fillScreen(BLACK)
        self._draw_header()
        self._draw_main()
        self.restore_button_hints()
