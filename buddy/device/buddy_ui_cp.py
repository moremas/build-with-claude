"""Render the buddy's state to the 240x135 Cardputer-Adv LCD.

### Rendering API choice

We use **`M5.Lcd.drawString(text, x, y)`** and **`M5.Lcd.textWidth(text)`**
everywhere instead of `setCursor()+print()` + char-count estimates.
Two reasons:

1. **Cursor origin on this build is baseline, not top-left.**
   `setCursor(x, y)` sets (x, baseline_y). DejaVu9 has ~8 px of ascender
   above the baseline, so text we thought was landing at y=118 was
   actually rendering around y=110..120 — half above the hint strip's
   DARK background. `drawString` uses the driver's text datum, which
   defaults to TL_DATUM (top-left), so (x, y) is the top-left corner
   of the glyph cell. That matches how the rest of the layout math
   thinks about rectangles.

2. **Proportional font widths are not `_CHAR_W * len(text)`.**
   Measured on hardware with DejaVu9 at size 1:

        "Y once"            = 38 px  (not 6*6=36)
        "Q = back to menu"  = 103 px
        "100%"              = 31 px  (not 4*6=24 — this is why the
                                      '%' was wrapping to a second
                                      line when we did x = 240-6-24)
        "Claude Buddy"      = 79 px
        "Settings > Buddy"  = 101 px

   So we call `_LCD.textWidth(...)` for every centering or right-
   alignment calculation. It's a cheap call (pure lookup over the
   font's advance table) and eliminates the off-by-a-few-pixels
   rendering glitches that show up when a layout guesses wrong.

### Font selection

`M5.Lcd.FONTS.DejaVu9` — the smallest DejaVu variant (10 px tall).
The default font is ~16 px and too bulky; DejaVu12 fits body text
but pushes the 3-column hint strip to within 7 px of the right edge
and fits the idle help awkwardly. DejaVu9 gives us ~17 px of strip
right margin and enough vertical room that we can bump the passkey
to size 4 for cross-room readability.

### State-specific layouts

  Idle (advertising / disconnected) — DejaVu9, all size 1:
    y=0..20    header "Claude Buddy" + status badge
    y=28       "Waiting to pair..."
    y=48       "Open Claude, go to"
    y=66       "Settings > Buddy"
    y=84       "and pick this one"
    y=112..134 hint strip "Q = Exit" (centered)

  Connected with heartbeat (no identity band — reused for a 3rd bar):
    y=0..20    header ("Claude Buddy" + status)
    y=24       "5h" quota bar     (100 - five_h_util)
    y=48       "Week" quota bar   (100 - week_util)
    y=72       "Sonnet" quota bar (100 - sonnet_util; hidden while a prompt is up)
    y=74..108  prompt box (when a permission is pending)
    y=112..134 hint strip (Y once / N deny / Q exit columns)

  Passkey overlay (during BLE pairing, layered over main):
    y=28       "Pairing passkey:"
    y=44..84   6-digit code at setTextSize(4)
    y=96       "type it into Claude"
"""

import M5

# Anthropic palette, inlined — byte-for-byte matches ui_theme.py.
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

_W = 240
_H = 135

# Usage bars render the *real* Claude quota, which only the host knows.
# The device is BLE-only (claude_buddy.py takes WiFi down for radio
# coexistence) so it can't query usage itself — the host companion
# (scripts/quota_push.py, backed by `codexbar`) sends, per heartbeat:
#   five_h_util / week_util / sonnet_util   - utilization % (0..100, "used")
#                                             -> bar length = 100 - util
#   five_h_color / week_color / sonnet_color - RGB int -> bar fill colour
# The host derives the colour from the codexbar pace stage (and a
# remaining-% fallback for windows with no pace); the device just paints
# it. Keeping the stage->colour map host-side means colours can be retuned
# without re-flashing.
#
# Claude.app's own heartbeat carries none of these, so on that link the
# bars read "--". See buddy/references/protocol.md.


def _has_cjk(s: str) -> bool:
    """True if s contains any character outside the DejaVu9 Latin range."""
    return any(ord(c) > 0x7E for c in s)


def _set_font_auto(text: str) -> int:
    """Set EFontJA24 for CJK text, DejaVu9 otherwise. Returns px height."""
    if _has_cjk(text):
        try:
            _LCD.setFont(_LCD.FONTS.EFontJA24)
            return 24
        except AttributeError:
            pass
    _LCD.setFont(_LCD.FONTS.DejaVu9)
    return 10


def _right(y: int, pad: int, text: str) -> int:
    """Cursor X so `text` ends `pad` px from the right edge."""
    return _W - pad - _LCD.textWidth(text)


def _center(text: str) -> int:
    """Cursor X to horizontally center `text` in the viewport."""
    return (_W - _LCD.textWidth(text)) // 2


class BuddyUI:
    """240x135 view. Mirrors the Basic's BuddyUI API so the protocol
    and app layers don't care which display is underneath."""

    def __init__(self):
        self._last = {}
        self._passkey = None
        # Unpair confirmation overlay: True while the host has asked
        # us to unpair and we're waiting for an on-device Y/N press.
        # See the threat model in buddy_ble.py — the BLE link is
        # unauthenticated, so destructive commands need an in-person
        # confirmation that an in-range BLE attacker can't fake.
        self._unpair_prompt = False
        self._connection_state = "advertising"
        self._prompt = None
        self._identity_name = "Buddy"
        self._identity_owner = ""
        # Cache last footer values so _draw_connected_main can repaint
        # the footer after _draw_main's fillRect wipes y=96..110.
        self._last_stats = {}
        self._last_battery = {}
        # Cache last rendered footer strings to skip redraws when unchanged.
        # Set to None when the footer area is wiped so the next draw is forced.
        self._last_footer_left = None
        self._last_footer_right = None
        _LCD.fillScreen(BLACK)
        # setFont is sticky across setTextSize calls, so we pick
        # DejaVu9 once at init. Wrapped in try/except so a future
        # UIFlow build that drops the font still loads us (falls back
        # to the default at an uglier size).
        try:
            _LCD.setFont(_LCD.FONTS.DejaVu9)
        except Exception as e:
            print("buddy_ui_cp: setFont fallback:", e)
        # No setRotation — Cardputer-Adv boots in landscape already.
        self._redraw_chrome()

    # ---- public setters (shape matches Basic's BuddyUI)

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
        """Show the destructive-action confirmation overlay.

        Repaints the main panel and the hint strip so the only useful
        keys are Y (confirm) and N (cancel) — Q stays an exit even
        here, mirroring the passkey overlay's escape hatch.
        """
        self._unpair_prompt = True
        self._draw_unpair_overlay()
        self.restore_button_hints()

    def clear_unpair_prompt(self):
        if not self._unpair_prompt:
            return
        self._unpair_prompt = False
        self._draw_main()
        self.restore_button_hints()

    def update_heartbeat(self, hb: dict):
        prev_pending = bool(self._prompt)
        self._last = hb
        self._prompt = hb.get("prompt")
        curr_pending = bool(self._prompt)
        # Steady-state connected heartbeat: skip the full clear+redraw in
        # _draw_main and just update the bar rows in-place.  This eliminates
        # the black flash that _draw_main's fillRect causes on every tick.
        # Fall through to _draw_main only on transitions (prompt appear/
        # disappear) or non-connected states, where a full repaint is needed.
        if (
            self._connection_state not in ("advertising", "disconnected")
            and self._passkey is None
            and not self._unpair_prompt
            and curr_pending == prev_pending
        ):
            self._draw_data_rows()
        else:
            self._draw_main()
        if curr_pending != prev_pending:
            self.restore_button_hints()

    def update_identity(self, name: str, owner: str):
        # The connected layout no longer renders an identity band — that
        # row is reused for the Sonnet bar, and the header already shows
        # "Claude Buddy". Just remember the values.
        self._identity_name = name or "Buddy"
        self._identity_owner = owner or ""

    def update_footer(self, stats: dict, battery: dict):
        self._last_stats = stats
        self._last_battery = battery
        # Stats footer only appears during the connected layout.
        if self._connection_state not in ("advertising", "disconnected"):
            self._draw_footer(stats, battery)

    def flash_decision(self, decision: str):
        color = GREEN if decision == "once" else RED
        self.flash_toast(decision.upper() + " sent", color)

    def flash_toast(self, text: str, color: int = CYAN):
        """Overwrite the hint strip with a one-line colored status."""
        _LCD.fillRect(0, 112, _W, _H - 112, color)
        _LCD.setTextColor(WHITE, color)
        _LCD.setTextSize(1)
        # Clip to whatever fits on the strip; in practice callers
        # keep text short.
        t = text
        while _LCD.textWidth(t) > _W - 12 and len(t) > 1:
            t = t[:-1]
        _LCD.drawString(t, 6, 117)

    def restore_button_hints(self):
        """Paint the hint strip. Shows the keyboard-command menu.

        Two modes:
          - Passkey on screen: Q only — Y/N are no-ops during pairing
            and showing them would be misleading.
          - Otherwise: full Y / N / Q menu, regardless of whether a
            prompt is currently pending. The earlier "only show what
            does something right now" version hid Y/N until a prompt
            arrived, which meant the operator couldn't learn the
            bindings just by looking at the device — the whole
            keyboard menu was invisible except during the ~1s windows
            of active prompts. When Y/N are pressed without a prompt,
            the main loop flashes a "no prompt" toast so the user
            still gets feedback; the menu staying visible is what
            makes the toast's meaning obvious.
        """
        # Thin orange hairline above the strip + DARK fill.
        _LCD.fillRect(0, 111, _W, 1, ORANGE)
        _LCD.fillRect(0, 112, _W, _H - 112, DARK)
        _LCD.setTextColor(CREAM, DARK)
        _LCD.setTextSize(1)
        if self._unpair_prompt:
            # Only Y and N during a destructive-action confirmation;
            # showing Q here invites a thumb-fumble exit that leaves
            # the host hanging on a pending ack.
            _LCD.drawString("Y confirm", 8, 117)
            n = "N cancel"
            _LCD.drawString(n, _right(117, 8, n), 117)
            return
        if self._passkey is not None:
            # During pairing only Q makes sense — Y and N don't
            # actually do anything until the encrypted state fires.
            label = "Q = Exit"
            _LCD.drawString(label, _center(label), 117)
            return
        # 3-column layout. Measured widths on DejaVu9: 38/39/34 px.
        # Left-aligned columns at x=8/96/right-aligned-8 give the
        # eye a clear "approve / deny / back" reading order.
        _LCD.drawString("Y once", 8, 117)
        _LCD.drawString("N deny", 96, 117)
        q = "Q exit"
        _LCD.drawString(q, _right(117, 8, q), 117)

    def is_idle(self) -> bool:
        return (
            self._connection_state in ("advertising", "disconnected")
            and self._passkey is None
            and self._prompt is None
            and not self._unpair_prompt
        )

    def tick_idle_burst(self, frame, last_tick):
        # No burst animation on Cardputer-Adv — kept for API shape
        # so buddy_app's main loop can call unconditionally.
        return frame, last_tick

    # ---- drawing primitives

    def _draw_header(self):
        _LCD.fillRect(0, 0, _W, 20, DARK)
        _LCD.fillRect(0, 20, _W, 1, ORANGE)
        _LCD.setTextSize(1)
        _LCD.setTextColor(ORANGE, DARK)
        _LCD.drawString("Claude Buddy", 6, 5)
        icon, color = self._connection_icon()
        _LCD.setTextColor(color, DARK)
        _LCD.drawString(icon, _right(5, 6, icon), 5)

    def _connection_icon(self):
        s = self._connection_state
        if s == "encrypted":
            return ("LINKED", GREEN)
        if s == "connected":
            return ("PAIR..", YELLOW)
        if s == "disconnected":
            return ("OFF", RED)
        return ("ADV", CYAN)

    def _draw_main(self):
        # In connected state: clear only the content band (y=21..95),
        # leaving the footer band (y=96..110) untouched. This prevents
        # rapid heartbeats from flickering the footer black — the footer
        # is only written by explicit _draw_footer calls via update_footer.
        # In all other states: clear the full band including the footer,
        # since those layouts own the entire area.
        if (
            not self._unpair_prompt
            and self._passkey is None
            and self._connection_state not in ("advertising", "disconnected")
        ):
            _LCD.fillRect(0, 21, _W, 75, BLACK)  # y=21..95, footer spared
        else:
            self._invalidate_footer()
            _LCD.fillRect(0, 21, _W, 90, BLACK)  # y=21..110, full clear
        # Overlays take precedence over the layout under them. The
        # unpair prompt outranks the passkey because they should never
        # both be live at once (passkey only fires during a real
        # pairing flow, which doesn't exist on this build), but
        # ordering it first means future builds with both don't
        # accidentally render the wrong thing.
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
        # Four short lines at size 1. y stride is 18 px which leaves
        # ~8 px of whitespace between 10-px-tall glyphs.
        _LCD.setTextSize(1)
        _LCD.setTextColor(CREAM, BLACK)
        _LCD.drawString("Waiting to pair...", 6, 28)
        _LCD.setTextColor(GRAY_MID, BLACK)
        _LCD.drawString("Open Claude, go to", 6, 48)
        _LCD.drawString("Settings > Buddy", 6, 66)
        _LCD.drawString("and pick this one", 6, 84)

    def _bar_color(self, color):
        """Bar fill colour. The host (scripts/quota_push.py) resolves the
        codexbar pace stage — and the remaining-% fallback for windows with
        no pace — into an RGB int and sends it per bar, so the device just
        paints what it's told. GRAY_MID is only a defensive default for a
        bar with data but no colour (shouldn't happen: the host always pairs
        a colour with a util)."""
        return GRAY_MID if color is None else color

    def _draw_bar(self, label: str, pct, y: int, color: int):
        """Draw a labeled horizontal progress bar showing remaining quota.

        pct is the *remaining* percentage (0..100); pct=100 means the bar is
        full. pct=None means "no data yet" — we draw an empty bar and a "--"
        label rather than inventing a number. `color` is the fill colour,
        chosen by the caller (pace stage, or remaining-% fallback).

        Draws the filled and empty portions of the bar in a single pass (no
        intermediate full-gray state) to avoid visible flicker on in-place
        updates.  The percentage text area is always cleared to a fixed width
        before writing so variable-width strings ("9%" vs "100%") don't leave
        stale pixels from a previous wider value.
        """
        _LCD.setTextSize(1)
        _LCD.setTextColor(GRAY_MID, BLACK)
        _LCD.drawString(label, 6, y)
        # Clear a 36 px slot at the right edge for the pct label so that
        # a shorter string ("9%" / "--") always overwrites a longer one ("100%").
        _LCD.fillRect(_W - 38, y, 32, 10, BLACK)
        if pct is None:
            pct_str = "--"
            fill_pct = 0
        else:
            fill_pct = max(0, min(100, pct))
            pct_str = "{}%".format(fill_pct)
        _LCD.setTextColor(WHITE, BLACK)
        _LCD.drawString(pct_str, _right(y, 6, pct_str), y)
        bar_y = y + 13
        bar_w = _W - 12
        fill_w = int(bar_w * fill_pct // 100)
        # Draw filled portion then empty portion in one pass — never shows
        # an intermediate all-gray state, so the bar updates without flash.
        if fill_w > 0:
            _LCD.fillRect(6, bar_y, fill_w, 8, color)
        if fill_w < bar_w:
            _LCD.fillRect(6 + fill_w, bar_y, bar_w - fill_w, 8, GRAY_DIM)

    def _data_pcts(self):
        """Return (h5, wk, sonnet) remaining % (0..100), each None if unknown.

        `five_h_util` / `week_util` / `sonnet_util` are utilization
        percentages (0..100, "used") the companion sends from `codexbar`
        (primary / secondary / tertiary). We display *remaining*, i.e.
        100 - utilization. An absent field yields None — that bar renders a
        "--" no-data state rather than a fabricated number.
        """
        hb = self._last

        def _rem(key):
            v = hb.get(key)
            return None if v is None else max(0, min(100, 100 - int(v)))

        return _rem("five_h_util"), _rem("week_util"), _rem("sonnet_util")

    def _draw_data_rows(self):
        """Update the quota bars in-place without clearing the full content
        area, so the screen never goes black between heartbeats.

        Three rows (5h / Week / Sonnet) at y=24/48/72 — the identity band
        was dropped to make vertical room. A pending prompt occupies the
        Sonnet row's space (prompt box y=74..108), so we hide Sonnet then.

        Bar length is remaining quota; bar colour is whatever the host sent
        (`*_color`, derived from the codexbar pace stage on the Mac side).
        """
        hb = self._last
        h5, wk, snt = self._data_pcts()
        self._draw_bar("5h", h5, 24, self._bar_color(hb.get("five_h_color")))
        self._draw_bar("Week", wk, 48, self._bar_color(hb.get("week_color")))
        if self._prompt:
            self._draw_prompt_box(self._prompt)
        else:
            self._draw_bar("Sonnet", snt, 72, self._bar_color(hb.get("sonnet_color")))

    def _draw_connected_main(self):
        self._draw_data_rows()
        # After an overlay exits back to connected, _draw_main's full-clear
        # fillRect wiped the footer — restore it, but not while a prompt box
        # (y=74..108) overlaps the footer band (y=96..110).
        if not self._prompt and (self._last_stats or self._last_battery):
            self._draw_footer(self._last_stats, self._last_battery)

    def _draw_prompt_box(self, prompt: dict):
        # Orange-bordered box for the pending permission. y=74..109
        # gives us 35 px of height — two 10-px text rows with a 4-px
        # top gap, 2-px inter-row gap, and 2-px bottom gap. That's
        # enough breathing room to render cleanly without touching
        # either the tokens line at y=58..68 or the hint strip
        # hairline at y=111.
        _LCD.drawRect(3, 74, _W - 6, 35, ORANGE)
        _LCD.setTextSize(1)
        _LCD.setTextColor(ORANGE, BLACK)
        tool_line = "PERM: " + prompt.get("tool", "?")
        h_tool = _set_font_auto(tool_line)
        while _LCD.textWidth(tool_line) > _W - 14 and len(tool_line) > 1:
            tool_line = tool_line[:-1]
        _LCD.drawString(tool_line, 7, 78)
        if h_tool > 10:
            _LCD.setFont(_LCD.FONTS.DejaVu9)
        hint = prompt.get("hint", "")
        h_hint = _set_font_auto(hint)
        _LCD.setTextColor(CREAM, BLACK)
        # CJK hint at 24 px: shift up so it fits in the box (y+24 <= 109).
        hint_y = 82 if h_hint > 10 else 94
        while _LCD.textWidth(hint) > _W - 14 and len(hint) > 1:
            hint = hint[:-1]
        _LCD.drawString(hint, 7, hint_y)
        if h_hint > 10:
            _LCD.setFont(_LCD.FONTS.DejaVu9)

    def _invalidate_footer(self):
        self._last_footer_left = None
        self._last_footer_right = None

    def _draw_unpair_overlay(self):
        if not self._unpair_prompt:
            return
        self._invalidate_footer()
        _LCD.fillRect(0, 21, _W, 90, BLACK)
        _LCD.setTextSize(1)
        _LCD.setTextColor(RED, BLACK)
        # Two-line attention header so the destructive nature is clear
        # at a glance — this is the only path that wipes user state.
        _LCD.drawString("UNPAIR REQUEST", 6, 28)
        _LCD.setTextColor(CREAM, BLACK)
        _LCD.drawString("from connected host.", 6, 46)
        _LCD.drawString("Wipes name, owner, stats", 6, 64)
        _LCD.drawString("and disconnects.", 6, 78)
        _LCD.setTextColor(GRAY_MID, BLACK)
        _LCD.drawString("Y confirm   N cancel", 6, 96)

    def _draw_passkey_overlay(self):
        if self._passkey is None:
            return
        self._invalidate_footer()
        _LCD.fillRect(0, 21, _W, 90, BLACK)
        _LCD.setTextSize(1)
        _LCD.setTextColor(ORANGE, BLACK)
        _LCD.drawString("Pairing passkey:", 6, 28)
        # Size 4 passkey on DejaVu9 = 40 px tall, ~6 digits wide.
        # Centered with textWidth so size-4 doesn't throw off the math.
        pk_str = "{:06d}".format(self._passkey)
        _LCD.setTextColor(CREAM, BLACK)
        _LCD.setTextSize(4)
        pk_w = _LCD.textWidth(pk_str)
        _LCD.drawString(pk_str, (_W - pk_w) // 2, 44)
        _LCD.setTextSize(1)
        _LCD.setTextColor(GRAY_MID, BLACK)
        _LCD.drawString("type it into Claude", 6, 96)

    def _draw_footer(self, stats: dict, battery: dict):
        # Thin stats line between main panel and hint strip, only in
        # the connected layout. y=96..110 (14 tall) holds one 10-px row.
        #
        # Each side is redrawn independently and only when its string
        # changes — avoids the black flash caused by fillRect+drawString
        # on every periodic update when the values are stable.
        # _invalidate_footer() resets the cache whenever a full-clear
        # fillRect wipes this region so the next call forces a redraw.
        _LCD.setTextSize(1)
        left = "Lv.{} a:{} d:{}".format(
            stats.get("lvl", 0),
            stats.get("appr", 0),
            stats.get("deny", 0),
        )
        pct = max(0, min(100, battery.get("pct", 0)))
        rem = battery.get("remaining_min")
        if rem is not None:
            if rem >= 120:
                right = "{}% ~{}h".format(pct, rem // 60)
            elif rem >= 60:
                right = "{}% ~{}h{}m".format(pct, rem // 60, rem % 60)
            else:
                right = "{}% ~{}m".format(pct, rem)
        else:
            right = "{}%".format(pct)

        if left != self._last_footer_left:
            _LCD.fillRect(0, 96, (_W * 2) // 3, 15, BLACK)
            _LCD.setTextColor(GRAY_MID, BLACK)
            _LCD.drawString(left, 6, 98)
            self._last_footer_left = left

        if right != self._last_footer_right:
            _LCD.fillRect((_W * 2) // 3, 96, _W // 3 + 1, 15, BLACK)
            _LCD.setTextColor(CREAM, BLACK)
            # Right-aligned with 6 px of padding — computed from textWidth,
            # not a char-count estimate, so proportional-font surprises
            # (e.g. '%' being 8 px wide) don't push the label off-screen.
            _LCD.drawString(right, _right(98, 6, right), 98)
            self._last_footer_right = right

    def _redraw_chrome(self):
        _LCD.fillScreen(BLACK)
        self._draw_header()
        self._draw_main()
        self.restore_button_hints()
