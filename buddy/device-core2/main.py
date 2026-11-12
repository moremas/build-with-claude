"""Launcher for the Core2 buddy bundle.

Core2 counterpart of ``buddy/device/main.py`` (the Cardputer-Adv
launcher). Same reason for existing: ``install_apps.py`` sets NVS
``uiflow.boot_option=2`` whenever a bundle ships a root ``main.py``, so
UIFlow's boot.py runs this file instead of starting its own framework
(which advertises over BLE for flow.m5stack.com pairing and can leave
NimBLE unable to accept our advertising payload). Our launcher never
touches BLE or WiFi, so Claude Buddy gets a pristine radio.

Differences from the Cardputer launcher:
- 320x240 screen, DejaVu18 text.
- Navigation with the three touch buttons under the screen:
  BtnA = up, BtnB = open, BtnC = down.
- No event-WiFi auto-connect and no early NimBLE bring-up (those were
  Cardputer/event-specific workarounds for WiFi+BLE coexistence; with
  WiFi never started there is nothing to coexist with).

Apps exit with ``machine.reset()``, which reboots back into this menu.

To go back to stock UIFlow, from the REPL:
    import esp32; n = esp32.NVS('uiflow'); n.set_u8('boot_option', 1); n.commit()
    import machine; machine.reset()
"""

import os
import sys
import time

import M5

try:
    M5.begin()
except Exception as e:
    print("launcher: M5.begin() warning:", e)

try:
    import burst_frames as _burst
except ImportError as e:
    print("launcher: burst_frames not available:", e)
    _burst = None

for _p in ("/flash", "/flash/apps"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_BLACK = 0x000000
_ORANGE = 0xCC785C
_CREAM = 0xF0EEE6
_DARK = 0x1F1F1F
_GRAY_MID = 0x777777
_RED = 0xFF0000

_LCD = M5.Lcd
_W = 320
_H = 240

_APPS_DIR = "/flash/apps"

_HEADER_H = 30
_STRIP_Y = 203
_BTN_X = (53, 160, 267)

_MENU_X = 14
_MENU_Y = 44
_ROW_H = 30
_MENU_RIGHT = 216
_MAX_VISIBLE = 5

_BURST_W = 72
_BURST_H = 72
_BURST_X = 234
_BURST_Y = 80


def _font(name="DejaVu18"):
    try:
        _LCD.setFont(getattr(_LCD.FONTS, name))
    except Exception as e:
        print("launcher: setFont fallback:", e)
    _LCD.setTextSize(1)


def _center(text, cx=_W // 2):
    return cx - _LCD.textWidth(text) // 2


def _discover_apps():
    """Sorted ``(display_name, module_name)`` for every app in /flash/apps."""
    try:
        files = sorted(f for f in os.listdir(_APPS_DIR) if f.endswith(".py"))
    except OSError as e:
        print("launcher: cannot list", _APPS_DIR, e)
        return []
    out = []
    for fname in files:
        mod = fname[:-3]
        if mod.startswith("_"):
            continue
        out.append((mod.replace("_", " "), mod))
    return out


def _draw_burst_frame(frame_idx):
    if _burst is None:
        return
    data = _burst.FRAMES[frame_idx % len(_burst.FRAMES)]
    color = _burst.COLOR
    _LCD.fillRect(_BURST_X, _BURST_Y, _BURST_W, _BURST_H, _BLACK)
    i = 0
    n = len(data)
    while i < n:
        _LCD.fillRect(_BURST_X + data[i + 1], _BURST_Y + data[i], data[i + 2], 1, color)
        i += 3


def _draw_hints(labels):
    _LCD.fillRect(0, _STRIP_Y - 1, _W, 1, _ORANGE)
    _LCD.fillRect(0, _STRIP_Y, _W, _H - _STRIP_Y, _DARK)
    _font()
    _LCD.setTextColor(_CREAM, _DARK)
    for text, cx in zip(labels, _BTN_X):
        if text:
            _LCD.drawString(text, _center(text, cx), _STRIP_Y + 8)


def _draw_chrome(apps, cursor, scroll_top=0):
    _LCD.fillScreen(_BLACK)
    _font()

    _LCD.fillRect(0, 0, _W, _HEADER_H, _DARK)
    _LCD.fillRect(0, _HEADER_H, _W, 1, _ORANGE)
    _LCD.setTextColor(_ORANGE, _DARK)
    _LCD.drawString("Claude Buddy Launcher", 8, 5)

    y = _MENU_Y
    visible = apps[scroll_top:scroll_top + _MAX_VISIBLE]
    for i, (display, _mod) in enumerate(visible):
        if scroll_top + i == cursor:
            _LCD.fillRect(6, y - 4, _MENU_RIGHT - 6, _ROW_H - 2, _ORANGE)
            _LCD.setTextColor(_BLACK, _ORANGE)
        else:
            _LCD.setTextColor(_CREAM, _BLACK)
        _LCD.drawString(display, _MENU_X, y)
        y += _ROW_H

    _LCD.setTextColor(_ORANGE, _BLACK)
    if scroll_top > 0:
        _LCD.drawString("^", _MENU_RIGHT - 16, _MENU_Y)
    if scroll_top + _MAX_VISIBLE < len(apps):
        _LCD.drawString("v", _MENU_RIGHT - 16, _MENU_Y + (len(visible) - 1) * _ROW_H)

    _draw_hints(("Up", "Open", "Down"))
    _draw_burst_frame(0)


def _wait_any_button():
    while True:
        M5.update()
        if M5.BtnA.wasPressed() or M5.BtnB.wasPressed() or M5.BtnC.wasPressed():
            return
        time.sleep_ms(40)


def _launch(mod_name):
    """Import the app (each app runs itself at import time and exits
    with machine.reset()). Only returns if the app raised."""
    _LCD.fillScreen(_BLACK)
    try:
        __import__(mod_name)
    except Exception as e:
        print("launcher: {} failed: {}".format(mod_name, e))
        sys.print_exception(e)
        _LCD.fillScreen(_BLACK)
        _font()
        _LCD.setTextColor(_RED, _BLACK)
        _LCD.drawString("App crashed:", 8, 20)
        _LCD.setTextColor(_CREAM, _BLACK)
        _LCD.drawString(mod_name, 8, 50)
        msg = str(e)
        _LCD.drawString(msg[:30], 8, 80)
        if len(msg) > 30:
            _LCD.drawString(msg[30:60], 8, 104)
        _draw_hints(("", "Back", ""))
        try:
            del sys.modules[mod_name]
        except KeyError:
            pass
        _wait_any_button()


def main():
    apps = _discover_apps()
    if not apps:
        _LCD.fillScreen(_BLACK)
        _font()
        _LCD.setTextColor(_CREAM, _BLACK)
        _LCD.drawString("No apps in " + _APPS_DIR, 8, 40)
        while True:
            time.sleep_ms(500)

    cursor = 0
    scroll_top = 0
    _draw_chrome(apps, cursor, scroll_top)

    # Let the power-on touch settle so a finger already on the strip
    # doesn't immediately launch something.
    for _ in range(10):
        M5.update()
        time.sleep_ms(30)
    for b in (M5.BtnA, M5.BtnB, M5.BtnC):
        b.wasPressed()

    frame = 0
    frame_ms = _burst.FRAME_MS if _burst is not None else 80
    last_frame_ms = time.ticks_ms()

    while True:
        M5.update()
        if M5.BtnA.wasPressed():
            cursor = (cursor - 1) % len(apps)
            if cursor < scroll_top:
                scroll_top = cursor
            elif cursor >= scroll_top + _MAX_VISIBLE:
                scroll_top = max(0, len(apps) - _MAX_VISIBLE)
            _draw_chrome(apps, cursor, scroll_top)
        elif M5.BtnC.wasPressed():
            cursor = (cursor + 1) % len(apps)
            if cursor >= scroll_top + _MAX_VISIBLE:
                scroll_top = cursor - _MAX_VISIBLE + 1
            elif cursor < scroll_top:
                scroll_top = 0
            _draw_chrome(apps, cursor, scroll_top)
        elif M5.BtnB.wasPressed():
            _launch(apps[cursor][1])
            _draw_chrome(apps, cursor, scroll_top)
            frame = 0
            last_frame_ms = time.ticks_ms()

        now = time.ticks_ms()
        if time.ticks_diff(now, last_frame_ms) >= frame_ms:
            frame += 1
            _draw_burst_frame(frame)
            last_frame_ms = now

        time.sleep_ms(40)


main()
