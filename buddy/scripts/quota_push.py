"""Push real Claude usage quota to the Cardputer buddy over BLE.

The buddy's "5h / Week / Sonnet" bars need real quota figures, but the
device is BLE-only and Claude.app's Hardware Buddy heartbeat carries no
quota (see buddy/references/protocol.md). This companion fills that gap:
it reads the quota from **codexbar** and writes, per heartbeat, a bar
length and a bar colour for each window to the device's Nordic-UART RX
characteristic, so the bars track the real account quota.

Backend: `codexbar --provider anthropic --format json`. Its first array
entry has `usage.{primary, secondary, tertiary}.usedPercent` (the bar
length) and `pace.{primary, secondary}.stage` (which we turn into a
colour here, host-side):

    primary   -> five_h_util + five_h_color  (5-hour window)
    secondary -> week_util   + week_color    (7-day, all)
    tertiary  -> sonnet_util + sonnet_color  (7-day, Sonnet; NO pace)

(Mapping verified against the labeled usage API: primary==five_hour,
secondary==seven_day, tertiary==seven_day_sonnet.) `<name>_util` gives
the device its bar length (*remaining* = 100 - util). `<name>_color` is
an RGB int the device paints directly — resolved here from the codexbar
pace stage via `_STAGE_COLORS` (green=reserve … red=deficit), with a
remaining-% fallback where there's no stage (Sonnet, or a window too
early for codexbar to emit pace). Keeping the colour map in this script
means you can retune colours without re-flashing the device.

### Connection model (important)

This script is the BLE **central**, exactly like Claude.app. A buddy
accepts one central at a time, so **while this is connected, Claude.app
cannot be** — you get the live quota readout but not prompt-approval.
Run it when you want quota on the desk display; quit it (Ctrl-C) and
reconnect from Claude.app for the approval workflow.

### Requirements

    pip install bleak                # BLE central for macOS/Linux/Windows
    codexbar on PATH, authenticated  # `codexbar --provider anthropic --format json`

### Usage

    python quota_push.py                  # scan for a Claude_* buddy, push forever
    python quota_push.py --interval 30    # poll/push every 30 s (default 60)
    python quota_push.py --address <UUID> # skip the scan, connect directly
    python quota_push.py --once           # connect, push one sample, exit
    python quota_push.py --dry-run        # fetch+print quota only, no BLE

Run it from **Terminal.app**, not from inside another app's shell —
macOS Bluetooth permission is per-binary, so an indirectly-launched
Python aborts with SIGABRT (exit 134). `--dry-run` does no BLE and is
exempt.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys

NUS_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
CODEXBAR_CMD = ["codexbar", "--provider", "anthropic", "--format", "json"]
DEFAULT_NAME_PREFIX = "Claude_"
DEFAULT_INTERVAL = 60

# Bar colour (RGB int 0xRRGGBB) per codexbar pace stage. The stage encodes
# consumption pace vs an even burn: *Behind = under pace (reserve, safe),
# onTrack = on pace, *Ahead = over pace (deficit, runs out early). We ramp
# green->red. This map lives host-side on purpose: tweak it and restart the
# script — no device re-flash needed (the device just paints what we send).
_STAGE_COLORS = {
    "farBehind":      0x00FF00,  # green  — deep reserve
    "behind":         0x55FF00,
    "slightlyBehind": 0xAAFF00,
    "onTrack":        0xFFFF00,  # yellow — exactly on pace
    "slightlyAhead":  0xFFAA00,
    "ahead":          0xFF5500,
    "farAhead":       0xFF0000,  # red    — heavy deficit
}


def _color_for(util, stage):
    """Resolve a bar colour (RGB int) or None. Prefer the pace stage; else
    a remaining-% scale (Sonnet has no pace, and codexbar omits pace early
    in a window). None only when there's no utilization at all."""
    if stage in _STAGE_COLORS:
        return _STAGE_COLORS[stage]
    if util is None:
        return None
    remaining = 100 - util
    return 0x00FF00 if remaining > 50 else (0xFFFF00 if remaining > 20 else 0xFF0000)


def _read_codexbar():
    """Run codexbar and return {name: (used%|None, stage|None)} for the
    five_h / week / sonnet windows (primary / secondary / tertiary)."""
    try:
        out = subprocess.run(
            CODEXBAR_CMD, capture_output=True, text=True, check=True, timeout=20,
        ).stdout
    except FileNotFoundError:
        raise SystemExit(
            "codexbar not found on PATH. Install it (e.g. `brew install codexbar`) "
            "and make sure `codexbar --provider anthropic --format json` works."
        )
    except subprocess.TimeoutExpired:
        raise SystemExit("codexbar timed out.")
    except subprocess.CalledProcessError as e:
        raise SystemExit("codexbar failed (exit %s): %s" % (e.returncode, (e.stderr or "").strip()))

    data = json.loads(out)
    if not data:
        raise RuntimeError("codexbar returned no entries")
    entry = data[0]
    usage = entry.get("usage") or {}
    pace = entry.get("pace") or {}

    def used(section):
        u = (usage.get(section) or {}).get("usedPercent")
        return None if u is None else int(round(u))

    def stage(section):
        return (pace.get(section) or {}).get("stage")

    return {
        "five_h": (used("primary"), stage("primary")),
        "week": (used("secondary"), stage("secondary")),
        "sonnet": (used("tertiary"), None),   # tertiary (Sonnet) has no pace
    }


def fetch_quota():
    """Build the heartbeat dict: per window, `<name>_util` (bar length =
    100-util) and `<name>_color` (RGB int the device paints directly).
    Colour resolution happens here, host-side, so it's tunable without a
    device re-flash. Windows with no utilization are omitted.

    e.g. {"five_h_util": 14, "five_h_color": 65280, "week_util": 13,
          "week_color": 16755200, "sonnet_util": 6, "sonnet_color": 65280}
    """
    hb = {}
    for name, (util, stage) in _read_codexbar().items():
        if util is None:
            continue
        hb[name + "_util"] = util
        color = _color_for(util, stage)
        if color is not None:
            hb[name + "_color"] = color
    return hb


def _heartbeat_line(hb: dict) -> bytes:
    return (json.dumps(hb) + "\n").encode("utf-8")


async def _find_device(name_prefix: str, timeout: float = 8.0):
    from bleak import BleakScanner
    print("scanning %.0fs for a '%s*' buddy..." % (timeout, name_prefix))
    devices = await BleakScanner.discover(timeout=timeout)
    for d in devices:
        if d.name and d.name.startswith(name_prefix):
            return d
    return None


async def _run(address, name_prefix, interval, once):
    from bleak import BleakClient

    target = address
    if target is None:
        dev = await _find_device(name_prefix)
        if dev is None:
            raise SystemExit(
                "No '%s*' buddy found. Is the Claude Buddy app running on "
                "the device, and not already connected to Claude.app?"
                % name_prefix
            )
        print("found %s (%s)" % (dev.name, dev.address))
        target = dev

    async with BleakClient(target) as client:
        print("connected; pushing quota every %ds (Ctrl-C to stop)" % interval)
        while True:
            try:
                hb = fetch_quota()
                await client.write_gatt_char(NUS_RX_UUID, _heartbeat_line(hb), response=False)
                print("pushed %s" % hb)
            except Exception as e:  # keep the link up across transient errors
                print("push error: %s" % e)
            if once:
                return
            if not client.is_connected:
                print("device disconnected")
                return
            await asyncio.sleep(interval)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                    help="seconds between quota pushes (default %d)" % DEFAULT_INTERVAL)
    ap.add_argument("--name-prefix", default=DEFAULT_NAME_PREFIX,
                    help="advertising-name prefix to match (default %s)" % DEFAULT_NAME_PREFIX)
    ap.add_argument("--address", default=None,
                    help="connect to this BLE address/UUID, skipping the scan")
    ap.add_argument("--once", action="store_true",
                    help="push a single sample then exit (smoke test)")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and print quota only; no BLE")
    args = ap.parse_args(argv)

    if args.dry_run:
        raw = _read_codexbar()
        for name, label in (("five_h", "5h"), ("week", "Week"), ("sonnet", "Sonnet")):
            util, stage = raw[name]
            rem = "--" if util is None else "%d%%" % (100 - util)
            color = _color_for(util, stage)
            color_s = "n/a" if color is None else "0x%06X" % color
            print("%-6s: %s remaining  stage=%-13s color=%s" % (
                label, rem, stage or "n/a", color_s))
        return 0

    try:
        asyncio.run(_run(args.address, args.name_prefix, args.interval, args.once))
    except KeyboardInterrupt:
        print("\nstopped")
    except ImportError:
        raise SystemExit("bleak is required for BLE: pip install bleak")
    return 0


if __name__ == "__main__":
    sys.exit(main())
