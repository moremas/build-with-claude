"""Pomodoro timer for Cardputer-Adv.

Standard Pomodoro cycle: 25-min work → 5-min short break, repeated 4 times,
then a 15-min long break. Advances automatically when each timer expires and
beeps to signal the transition.

### Controls
  SPACE / ENTER  — start or pause the current timer
  R              — reset the current phase (full duration, READY state)
  N              — skip to the next phase immediately
  Q / ESC        — exit back to the launcher

### Layout (240×135)
  Header  y=0..19    "POMODORO" left, 4 session dots right
  Hairline y=20      orange
  Mode    y=24..39   WORK / SHORT BREAK / LONG BREAK, color-coded, centered
  Timer   y=44..87   MM:SS at size 3, centered
  Status  y=92..105  READY / RUNNING / PAUSED, centered
  Hints   y=117..134 key legend

### Timer accuracy
  Uses time.ticks_ms() so the 40 ms display loop doesn't drift the clock.
  We track `secs_left` (remaining at last start/resume) and `tick_start`
  (ticks_ms at that moment); displayed seconds = secs_left - elapsed//1000.
  Pause snapshots secs_left at the nearest whole second.
"""

import time

import M5
import machine
from hardware import MatrixKeyboard


_BLACK    = 0x000000
_ORANGE   = 0xCC785C
_CREAM    = 0xF0EEE6
_DARK     = 0x1F1F1F
_GRAY_MID = 0x777777
_GREEN    = 0x4CAF50
_BLUE     = 0x5B9BD5

_LCD = M5.Lcd
_W = 240
_H = 135

_WORK_SECS        = 25 * 60
_SHORT_BREAK_SECS =  5 * 60
_LONG_BREAK_SECS  = 15 * 60
_CYCLE_LEN        = 4

_WORK        = 0
_SHORT_BREAK = 1
_LONG_BREAK  = 2

_PHASE_LABEL    = {_WORK: "WORK", _SHORT_BREAK: "SHORT BREAK", _LONG_BREAK: "LONG BREAK"}
_PHASE_DURATION = {_WORK: _WORK_SECS, _SHORT_BREAK: _SHORT_BREAK_SECS, _LONG_BREAK: _LONG_BREAK_SECS}
_PHASE_COLOR    = {_WORK: _ORANGE, _SHORT_BREAK: _GREEN, _LONG_BREAK: _BLUE}

_READY   = 0
_RUNNING = 1
_PAUSED  = 2

_STATE_LABEL = {_READY: "READY", _RUNNING: "RUNNING", _PAUSED: "PAUSED"}


def _set_font():
    try:
        _LCD.setFont(_LCD.FONTS.DejaVu9)
    except Exception as e:
        print("pomodoro: setFont:", e)


def _beep():
    try:
        M5.Speaker.tone(880, 200)
        time.sleep_ms(250)
        M5.Speaker.tone(1100, 200)
        time.sleep_ms(250)
        M5.Speaker.tone(1320, 400)
    except Exception as e:
        print("pomodoro: beep:", e)


def _draw_header(completed):
    _LCD.fillRect(0, 0, _W, 20, _DARK)
    _LCD.fillRect(0, 20, _W, 1, _ORANGE)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_ORANGE, _DARK)
    _LCD.drawString("POMODORO", 6, 5)
    # 4 small squares right-aligned; filled = completed this cycle
    x = _W - 8
    for i in range(_CYCLE_LEN - 1, -1, -1):
        color = _ORANGE if i < completed else _GRAY_MID
        _LCD.fillRect(x, 7, 6, 6, color)
        x -= 10


def _draw_mode(phase):
    label = _PHASE_LABEL[phase]
    color = _PHASE_COLOR[phase]
    _LCD.fillRect(0, 22, _W, 18, _BLACK)
    _LCD.setTextSize(1)
    _LCD.setTextColor(color, _BLACK)
    _LCD.drawString(label, (_W - _LCD.textWidth(label)) // 2, 26)


def _draw_countdown(secs):
    mins = secs // 60
    sec  = secs % 60
    text = "{:02d}:{:02d}".format(mins, sec)
    _LCD.fillRect(0, 42, _W, 48, _BLACK)
    _LCD.setTextSize(3)
    _LCD.setTextColor(_CREAM, _BLACK)
    _LCD.drawString(text, (_W - _LCD.textWidth(text)) // 2, 48)


def _draw_status(state):
    label = _STATE_LABEL[state]
    _LCD.fillRect(0, 90, _W, 18, _BLACK)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_GRAY_MID, _BLACK)
    _LCD.drawString(label, (_W - _LCD.textWidth(label)) // 2, 94)


def _draw_hints():
    _LCD.fillRect(0, _H - 18, _W, 18, _DARK)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_GRAY_MID, _DARK)
    hint = "SPC start  R reset  N skip  Q exit"
    _LCD.drawString(hint, (_W - _LCD.textWidth(hint)) // 2, _H - 14)


def _draw_all(phase, state, secs, completed):
    _LCD.fillScreen(_BLACK)
    _draw_header(completed)
    _draw_mode(phase)
    _draw_countdown(secs)
    _draw_status(state)
    _draw_hints()


def _next_phase(phase, completed):
    if phase == _WORK:
        done = completed + 1
        if done >= _CYCLE_LEN:
            return _LONG_BREAK, done
        return _SHORT_BREAK, done
    if phase == _LONG_BREAK:
        return _WORK, 0
    return _WORK, completed


def _key_is(k, chars):
    if k is None:
        return False
    if isinstance(k, int):
        return k in chars or (0x20 <= k <= 0x7E and chr(k).lower() in chars)
    if isinstance(k, str) and k:
        return k.lower() in chars
    return False


def run():
    _set_font()

    phase      = _WORK
    state      = _READY
    completed  = 0
    secs_left  = _PHASE_DURATION[phase]
    last_sec   = -1
    tick_start = None

    _draw_all(phase, state, secs_left, completed)

    kb = MatrixKeyboard()
    time.sleep_ms(400)  # absorb the launcher's Enter keypress

    try:
        while True:
            kb.tick()
            k = kb.get_key()

            if _key_is(k, {'q', '\x1b'}):
                return

            if _key_is(k, {' ', '\r', '\n'}):
                if state in (_READY, _PAUSED):
                    state = _RUNNING
                    tick_start = time.ticks_ms()
                elif state == _RUNNING:
                    elapsed_ms = time.ticks_diff(time.ticks_ms(), tick_start)
                    secs_left  = max(0, secs_left - elapsed_ms // 1000)
                    tick_start = None
                    state = _PAUSED
                _draw_countdown(secs_left)
                _draw_status(state)
                last_sec = secs_left

            elif _key_is(k, {'r'}):
                state      = _READY
                secs_left  = _PHASE_DURATION[phase]
                tick_start = None
                last_sec   = -1
                _draw_countdown(secs_left)
                _draw_status(state)

            elif _key_is(k, {'n'}):
                phase, completed = _next_phase(phase, completed)
                state      = _READY
                secs_left  = _PHASE_DURATION[phase]
                tick_start = None
                last_sec   = -1
                _draw_all(phase, state, secs_left, completed)

            if state == _RUNNING:
                now      = time.ticks_ms()
                elapsed  = time.ticks_diff(now, tick_start)
                cur_secs = secs_left - elapsed // 1000

                if cur_secs <= 0:
                    _draw_countdown(0)
                    _beep()
                    phase, completed = _next_phase(phase, completed)
                    state      = _READY
                    secs_left  = _PHASE_DURATION[phase]
                    tick_start = None
                    last_sec   = -1
                    _draw_all(phase, state, secs_left, completed)
                elif cur_secs != last_sec:
                    _draw_countdown(cur_secs)
                    last_sec = cur_secs

            time.sleep_ms(40)

    finally:
        try:
            _LCD.fillScreen(_BLACK)
        except Exception as e:
            print("pomodoro: clear:", e)
        time.sleep_ms(200)
        machine.reset()


run()
