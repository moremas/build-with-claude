"""Brickbreaker for the Cardputer-Adv.

A self-contained classic-style breakout: paddle along the bottom,
ball bouncing around, four rows of bricks at the top. Clear all the
bricks for an "All clear!" win; miss the ball three times and it's
game over. W (or Space, or Enter) restarts from the win/lose screen.
Single round, no level escalation — the 32-brick wall is enough of a
challenge at 2 px/tick and three lives without piling on difficulty.

### Conventions shared with the rest of the bundle

- Matches snake.py / hello_cardputer.py chrome: 240x135 landscape,
  20 px DARK header with an ORANGE hairline at y=20, an 18 px hint
  strip across the bottom, the play area between. Same five-color
  palette so the suite reads as one device.
- Inputs via MatrixKeyboard polled every 40 ms with `kb.tick()` +
  `kb.get_key()`. Two control schemes:
    A/D            (gamer default)
    `,` / `/`      (the Cardputer arrow-cluster glyphs)
  W / `;` / Space launch the ball from the paddle and serve as
  restart on game over. Q / ESC exit back to the launcher.
- Exit is `machine.reset()` inside a `finally` block — same
  no-return-to-launcher constraint claude_buddy and snake hit on
  UIFlow 2.0. Screen is wiped to black before the reboot so the
  launcher doesn't briefly flash the last frame.

### Layout math (240x135)

    Header:       y=0..19    (DARK, ORANGE hairline at y=20)
    Brick field:  y=24..57   (4 rows of 7 px + 3 gaps of 2 px = 34 px)
    Free play:    y=58..107  (50 px of ball-and-paddle space)
    Paddle:       y=108..111 (4 px tall)
    Spacer:       y=112..116 (5 px)
    Hint strip:   y=117..134 (18 px DARK)

    Brick columns: 8 cols x 28 px = 224 px, with 7 gaps of 2 px = 238 px,
                   centered with 1 px margin each side starting at x=1.

### Physics notes

- Ball moves in integer 2-px steps per 40 ms poll — 50 px/s per axis
  is brisk enough to feel like a game and slow enough that an entire
  brick row can be raked without missing the bounce. Stepping by 2
  (not 1) avoids the extra full-screen draws that 80 Hz physics
  would force on the ESP32-S3 LCD path; the bricks are 7 px tall
  so a 2 px step still detects every collision cleanly.
- Paddle moves 4 px per poll while a direction key is held, 100 px/s.
  Faster than the ball so the player can always reposition mid-volley.
- Paddle reflection uses 5 zones across its width: edges deflect at
  the steepest angle (dx = +/- 2), middle is straight (dx = 0).
  Keeps rallies winnable but lets a skilled player aim shots into
  remaining bricks.
- Brick collision uses AABB with a "dominant axis" reversal:
  whichever side the ball penetrated deeper that frame is the side
  it bounces off. Avoids the classic "ball clips through corner"
  bug when dx and dy both step into the same brick on one tick.
"""

import random
import time

import M5
import machine
from hardware import MatrixKeyboard


# Palette inlined from ui_theme so this app is single-file installable.
_BLACK = 0x000000
_ORANGE = 0xCC785C
_CREAM = 0xF0EEE6
_DARK = 0x1F1F1F
_GRAY_MID = 0x777777
_RED = 0xFF0000

# Brick row tints — four shades stepping from cream-on-orange down to
# the deeper accent. Ordered top-to-bottom so the back row (highest
# scoring, hardest to reach last) is the strongest accent.
_BRICK_COLORS = (
    0xCC785C,   # row 0 (top)    — _ORANGE
    0xD68E78,   # row 1
    0xE0A493,   # row 2
    0xEEC4B8,   # row 3 (bottom) — lightest, since this row clears first
)

_LCD = M5.Lcd

_W = 240
_H = 135

# Brick grid dimensions.
_BRICK_COLS = 8
_BRICK_ROWS = 4
_BRICK_W = 28
_BRICK_H = 7
_BRICK_GAP = 2
_BRICK_X0 = 1
_BRICK_Y0 = 24

# Ball.
_BALL_SIZE = 4
_BALL_STEP = 2          # pixels per axis per 40 ms poll

# Paddle.
_PADDLE_W = 36
_PADDLE_H = 4
_PADDLE_Y = 108
_PADDLE_STEP = 4

# Bottom of free play — ball below this is "missed".
_BOTTOM = _PADDLE_Y + _PADDLE_H

# Score per brick: top row scores most, bottom row scores least. The
# rationale being that the ball clears the bottom row first as a
# matter of geometry, so the harder rows should be worth more.
_BRICK_SCORE = (4, 3, 2, 1)


def _set_font():
    """Match snake.py / claude_buddy chrome."""
    try:
        _LCD.setFont(_LCD.FONTS.DejaVu9)
    except Exception as e:
        print("brickbreaker: setFont fallback:", e)


def _draw_chrome(score, lives):
    _LCD.fillScreen(_BLACK)
    _LCD.fillRect(0, 0, _W, 20, _DARK)
    _LCD.fillRect(0, 20, _W, 1, _ORANGE)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_ORANGE, _DARK)
    _LCD.drawString("Brickbreaker", 6, 5)
    _update_status(score, lives)
    # Bottom hint strip — same shape as the other apps.
    _LCD.fillRect(0, _H - 18, _W, 18, _DARK)
    _LCD.setTextColor(_GRAY_MID, _DARK)
    hint = "A/D move   W launch   Q exit"
    _LCD.drawString(hint, (_W - _LCD.textWidth(hint)) // 2, _H - 14)


def _update_status(score, lives):
    """Repaint just the right portion of the header so the title
    text doesn't redraw on every score / life change."""
    _LCD.fillRect(96, 0, _W - 96, 20, _DARK)
    _LCD.setTextColor(_CREAM, _DARK)
    text = "{}  *{}".format(score, lives)
    x = _W - 6 - _LCD.textWidth(text)
    _LCD.drawString(text, x, 5)


def _intent(k):
    """Collapse raw key input to gameplay intents.

    Same shape as snake._intent — printable ints become chars, Enter
    and Escape get special-cased, then a single string-match path.
    """
    if k is None:
        return None
    if isinstance(k, int):
        if k == 0x1B:
            return "exit"
        if k in (0x0A, 0x0D):
            return "launch"
        if 0x20 <= k <= 0x7E:
            k = chr(k)
        else:
            return None
    if not isinstance(k, str) or not k:
        return None
    ch = k.lower()
    if ch == "a" or ch == ",":
        return "left"
    if ch == "d" or ch == "/":
        return "right"
    if ch == "w" or ch == ";" or ch == " ":
        return "launch"
    if ch == "q":
        return "exit"
    if ch == "r":
        return "launch"     # double duty as restart on game over
    return None


# ---- bricks --------------------------------------------------------

def _build_bricks():
    """Return a list of [x, y, w, h, color, score, alive] rows.

    Stored as a flat list rather than a 2D matrix because we iterate
    the whole grid every frame for collision and the flat layout has
    one less indirection per cell. ~32 bricks fits comfortably in
    MicroPython's heap — no need to preallocate or pool.
    """
    bricks = []
    for row in range(_BRICK_ROWS):
        y = _BRICK_Y0 + row * (_BRICK_H + _BRICK_GAP)
        color = _BRICK_COLORS[row]
        score = _BRICK_SCORE[row]
        for col in range(_BRICK_COLS):
            x = _BRICK_X0 + col * (_BRICK_W + _BRICK_GAP)
            bricks.append([x, y, _BRICK_W, _BRICK_H, color, score, True])
    return bricks


def _draw_bricks(bricks):
    for b in bricks:
        if b[6]:
            _LCD.fillRect(b[0], b[1], b[2], b[3], b[4])


def _erase_brick(b):
    _LCD.fillRect(b[0], b[1], b[2], b[3], _BLACK)


# ---- paddle / ball drawing ----------------------------------------

def _draw_paddle(x, prev_x):
    if prev_x is not None and prev_x != x:
        _LCD.fillRect(prev_x, _PADDLE_Y, _PADDLE_W, _PADDLE_H, _BLACK)
    _LCD.fillRect(x, _PADDLE_Y, _PADDLE_W, _PADDLE_H, _CREAM)


def _draw_ball(x, y, prev):
    if prev is not None:
        _LCD.fillRect(prev[0], prev[1], _BALL_SIZE, _BALL_SIZE, _BLACK)
    _LCD.fillRect(x, y, _BALL_SIZE, _BALL_SIZE, _ORANGE)


# ---- collision -----------------------------------------------------

def _hit_brick(bx, by, brick):
    """AABB hit test: ball at (bx, by, _BALL_SIZE) vs brick [x,y,w,h,...]."""
    return (bx < brick[0] + brick[2] and bx + _BALL_SIZE > brick[0]
            and by < brick[1] + brick[3] and by + _BALL_SIZE > brick[1])


def _bounce_axis(bx, by, vx, vy, brick):
    """Pick which axis to reverse on a brick hit.

    Compute the overlap on each axis; flip whichever axis has the
    smaller overlap (i.e. the side the ball just barely entered
    from). If they tie, flip both — corner hits feel right that way.
    """
    bx_l, bx_r = bx, bx + _BALL_SIZE
    by_t, by_b = by, by + _BALL_SIZE
    br_l, br_t = brick[0], brick[1]
    br_r, br_b = brick[0] + brick[2], brick[1] + brick[3]

    # Overlap depths on each axis. Pick the *smaller* overlap as the
    # side the ball came in through.
    if vx > 0:
        ox = bx_r - br_l
    else:
        ox = br_r - bx_l
    if vy > 0:
        oy = by_b - br_t
    else:
        oy = br_b - by_t

    flip_x = ox <= oy
    flip_y = oy <= ox
    return (-vx if flip_x else vx, -vy if flip_y else vy)


def _paddle_reflect(bx, paddle_x):
    """Map ball-x at the moment of paddle contact to a new dx in
    {-2, -1, 0, 1, 2}. Five zones across the paddle width. Center
    bounces straight up; edges bounce at the steepest angle."""
    rel = (bx + _BALL_SIZE // 2) - paddle_x       # 0 .. _PADDLE_W
    if rel < 0:
        rel = 0
    elif rel > _PADDLE_W:
        rel = _PADDLE_W
    zone = int(rel * 5 // (_PADDLE_W + 1))         # 0..4
    return (-2, -1, 0, 1, 2)[zone]


# ---- screens -------------------------------------------------------

def _draw_serve_hint():
    """Drawn between rounds while the ball is held to the paddle."""
    _LCD.fillRect(0, 64, _W, 16, _BLACK)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_CREAM, _BLACK)
    msg = "press W / Space to launch"
    _LCD.drawString(msg, (_W - _LCD.textWidth(msg)) // 2, 68)


def _clear_serve_hint():
    _LCD.fillRect(0, 64, _W, 16, _BLACK)


def _end_screen(kb, won, score):
    """Blocking end-of-round screen. Returns 'restart' or 'exit'."""
    _LCD.fillRect(0, 21, _W, _H - 21 - 18, _BLACK)
    _LCD.setTextSize(1)
    if won:
        _LCD.setTextColor(_ORANGE, _BLACK)
        head = "All clear!"
    else:
        _LCD.setTextColor(_RED, _BLACK)
        head = "Game over"
    _LCD.drawString(head, (_W - _LCD.textWidth(head)) // 2, 36)

    _LCD.setTextColor(_CREAM, _BLACK)
    s = "score: {}".format(score)
    _LCD.drawString(s, (_W - _LCD.textWidth(s)) // 2, 60)

    _LCD.setTextColor(_GRAY_MID, _BLACK)
    h = "W again   Q exit"
    _LCD.drawString(h, (_W - _LCD.textWidth(h)) // 2, 90)

    while True:
        kb.tick()
        i = _intent(kb.get_key())
        if i == "launch":
            return "restart"
        if i == "exit":
            return "exit"
        time.sleep_ms(40)


# ---- main loop -----------------------------------------------------

def _play_round(kb):
    """One ball-life. Returns (score_delta, lives_lost_bool, exit_bool, all_clear_bool)."""
    bricks = _build_bricks()
    paddle_x = (_W - _PADDLE_W) // 2
    score = 0
    lives = 3

    _draw_chrome(score, lives)
    _draw_bricks(bricks)

    while lives > 0:
        # Ball stuck to paddle until launch.
        ball_x = paddle_x + _PADDLE_W // 2 - _BALL_SIZE // 2
        ball_y = _PADDLE_Y - _BALL_SIZE - 1
        # Initial direction: random left/right but always upward.
        vx = random.choice((-1, 1)) * _BALL_STEP
        vy = -_BALL_STEP

        prev_paddle_x = None
        prev_ball = None

        _draw_paddle(paddle_x, prev_paddle_x)
        prev_paddle_x = paddle_x
        _draw_ball(ball_x, ball_y, prev_ball)
        prev_ball = (ball_x, ball_y)
        _draw_serve_hint()

        launched = False
        while not launched:
            kb.tick()
            i = _intent(kb.get_key())
            if i == "exit":
                return score, False, True, False
            if i == "left":
                paddle_x = max(0, paddle_x - _PADDLE_STEP)
                _draw_paddle(paddle_x, prev_paddle_x)
                prev_paddle_x = paddle_x
                ball_x = paddle_x + _PADDLE_W // 2 - _BALL_SIZE // 2
                _draw_ball(ball_x, ball_y, prev_ball)
                prev_ball = (ball_x, ball_y)
            elif i == "right":
                paddle_x = min(_W - _PADDLE_W, paddle_x + _PADDLE_STEP)
                _draw_paddle(paddle_x, prev_paddle_x)
                prev_paddle_x = paddle_x
                ball_x = paddle_x + _PADDLE_W // 2 - _BALL_SIZE // 2
                _draw_ball(ball_x, ball_y, prev_ball)
                prev_ball = (ball_x, ball_y)
            elif i == "launch":
                launched = True
            time.sleep_ms(40)
        _clear_serve_hint()

        # Active play.
        while True:
            kb.tick()
            i = _intent(kb.get_key())
            if i == "exit":
                return score, False, True, False
            if i == "left":
                paddle_x = max(0, paddle_x - _PADDLE_STEP)
                _draw_paddle(paddle_x, prev_paddle_x)
                prev_paddle_x = paddle_x
            elif i == "right":
                paddle_x = min(_W - _PADDLE_W, paddle_x + _PADDLE_STEP)
                _draw_paddle(paddle_x, prev_paddle_x)
                prev_paddle_x = paddle_x

            # Step the ball.
            ball_x += vx
            ball_y += vy

            # Walls.
            if ball_x <= 0:
                ball_x = 0
                vx = -vx
            elif ball_x + _BALL_SIZE >= _W:
                ball_x = _W - _BALL_SIZE
                vx = -vx
            if ball_y <= 21:    # just below the orange hairline
                ball_y = 21
                vy = -vy

            # Paddle.
            if (vy > 0
                    and ball_y + _BALL_SIZE >= _PADDLE_Y
                    and ball_y + _BALL_SIZE <= _PADDLE_Y + _PADDLE_H + 2
                    and ball_x + _BALL_SIZE > paddle_x
                    and ball_x < paddle_x + _PADDLE_W):
                vy = -_BALL_STEP
                vx = _paddle_reflect(ball_x, paddle_x)
                ball_y = _PADDLE_Y - _BALL_SIZE   # snap above paddle

            # Bricks. Iterate alive bricks; first hit reverses the
            # appropriate axis, kills the brick, scores. We allow at
            # most one brick kill per tick — at 2 px steps the ball
            # geometrically can't straddle two bricks across a gap
            # of 2 px in a single step.
            for b in bricks:
                if not b[6]:
                    continue
                if _hit_brick(ball_x, ball_y, b):
                    vx, vy = _bounce_axis(ball_x, ball_y, vx, vy, b)
                    b[6] = False
                    _erase_brick(b)
                    score += b[5]
                    _update_status(score, lives)
                    break

            # Miss?
            if ball_y >= _BOTTOM:
                lives -= 1
                _update_status(score, lives)
                # Erase the dropped ball trail.
                if prev_ball is not None:
                    _LCD.fillRect(prev_ball[0], prev_ball[1],
                                  _BALL_SIZE, _BALL_SIZE, _BLACK)
                _LCD.fillRect(ball_x, ball_y, _BALL_SIZE, _BALL_SIZE, _BLACK)
                prev_ball = None
                break       # back to serve loop

            # All clear?
            if not any(b[6] for b in bricks):
                return score, False, False, True

            _draw_ball(ball_x, ball_y, prev_ball)
            prev_ball = (ball_x, ball_y)
            time.sleep_ms(40)

    # Out of lives.
    return score, True, False, False


def run():
    _set_font()
    kb = MatrixKeyboard()
    # Same 400 ms launch-keypress debounce as the rest of the apps so
    # selecting "brickbreaker" from App List doesn't immediately
    # serve the ball.
    time.sleep_ms(400)
    try:
        while True:
            score, _out_of_lives, exited, all_clear = _play_round(kb)
            if exited:
                return
            if _end_screen(kb, all_clear, score) == "exit":
                return
    finally:
        try:
            _LCD.fillScreen(_BLACK)
        except Exception as e:
            print("brickbreaker: clear warning:", e)
        time.sleep_ms(200)
        machine.reset()


# UIFlow's App List invokes apps both as __main__ and via import; the
# bare run() call mirrors snake.py / hello_cardputer.py.
run()
