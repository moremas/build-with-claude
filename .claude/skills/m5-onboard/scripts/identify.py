"""Identify which M5Stack board is on a port by asking its running firmware.

esptool can tell an ESP32 from an ESP32-S3, but every classic-Core board
(Basic, Fire, Core2, Tough) is the same ESP32-D0WDQ6-V3 with the same
16 MB flash, so the chip alone can't pick a firmware variant. If the
board is already running MicroPython (UIFlow 2.0, as shipped or from a
previous onboard run), the firmware's build name answers the question
directly: ``sys.implementation._machine`` is e.g.
``"M5STACK Core2 with ESP32(SPIRAM)"``.

This module is deliberately conservative: it only maps build names it
has been verified against, and returns ``None`` for anything else so
the caller falls back to asking the user for ``--variant``. Flashing
the wrong variant boot-loops the board, so a wrong guess is worse than
no guess.

It also owns the variant -> app-bundle mapping, so ``--apps buddy`` on a
board with its own port of the Buddy bundle installs that port instead
of the Cardputer one (whose launcher needs the Cardputer keyboard).

CLI:
    python3 identify.py --port /dev/cu.usbserial-XXXX
"""

from __future__ import annotations

import argparse
import sys
import time

import vendor_path
vendor_path.ensure_on_syspath()

import mpy_repl


# Lower-cased substring of the MicroPython build name -> firmware
# variant (a key of fetch_firmware.VARIANTS). Checked in order.
_MACHINE_TO_VARIANT = (
    ("core2", "core2"),
)

# Firmware variant -> app bundle to use when the caller asks for the
# generic "buddy" bundle. Variants not listed keep "buddy" (the
# Cardputer-Adv bundle).
BUDDY_BUNDLE_FOR_VARIANT = {
    "core2": "buddy-core2",
}

_PROBE = (
    "import sys, os\n"
    "print('M5ID_MACHINE=' + str(getattr(sys.implementation, '_machine', os.uname().machine)))\n"
)


def variant_for_machine(machine: str) -> str | None:
    m = (machine or "").lower()
    for needle, variant in _MACHINE_TO_VARIANT:
        if needle in m:
            return variant
    return None


def bundle_for(apps: str | None, variant: str | None) -> str | None:
    """Swap the generic ``buddy`` bundle for the board-specific port,
    if one exists. Any other ``apps`` value (a path, or an explicit
    bundle name) is returned unchanged."""
    if apps == "buddy" and variant in BUDDY_BUNDLE_FOR_VARIANT:
        return BUDDY_BUNDLE_FOR_VARIANT[variant]
    return apps


def identify(port: str, boot_wait: float = 3.0, attempts: int = 2) -> dict:
    """Ask the board's running MicroPython what it is.

    Returns ``{"machine": str | None, "variant": str | None}``. Never
    raises for an unresponsive board — a board without MicroPython (blank,
    factory-test or Arduino firmware) just yields ``None`` for both.

    Opening a UART-bridge port can pulse the auto-reset circuit and
    reboot the board, so we give it ``boot_wait`` seconds to get past
    boot.py before interrupting to the REPL. The board is left at the
    REPL prompt; callers that need it running again should reset it.
    """
    result = {"machine": None, "variant": None}
    try:
        s = mpy_repl.open_port(port)
    except Exception as e:
        sys.stderr.write(f"identify: cannot open {port}: {e}\n")
        return result
    try:
        time.sleep(boot_wait)
        for _ in range(attempts):
            mpy_repl.interrupt_to_repl(s)
            out = mpy_repl.paste_exec(s, _PROBE, settle=0.8).decode("utf-8", "replace")
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("M5ID_MACHINE="):
                    machine = line.split("=", 1)[1]
                    result["machine"] = machine
                    result["variant"] = variant_for_machine(machine)
                    return result
            time.sleep(2.0)
    except Exception as e:
        sys.stderr.write(f"identify: REPL probe failed: {e}\n")
    finally:
        try:
            s.close()
        except Exception:
            pass
    return result


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Identify an M5Stack board from its running MicroPython build."
    )
    ap.add_argument("--port", required=True)
    args = ap.parse_args()
    info = identify(args.port)
    print(f"Machine:  {info['machine'] or 'unknown (no MicroPython REPL answered)'}")
    print(f"Variant:  {info['variant'] or 'unknown — pass --variant explicitly'}")
    print(f"Buddy bundle: {bundle_for('buddy', info['variant'])}")
    return 0 if info["variant"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
