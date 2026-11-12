"""Install a bundle of MicroPython .py files onto /flash/ on the device.

This is the second step after "flash firmware": it copies the user's
app sources onto the device so it boots into their software instead
of UIFlow's pairing screen. Think of it as provisioning the *payload*
that runs on top of UIFlow.

### Source layout → device layout

Two layouts are supported, chosen by whether the source directory
has an ``apps/`` subdir:

- **Flat (legacy, Basic-style):** every ``*.py`` at the source root
  is uploaded to ``/flash/``. The bundle is expected to ship a
  custom ``boot.py`` and/or ``main.py`` that drives the device, and
  UIFlow's own launcher is bypassed.
- **Nested (Cardputer-style, preferred):** ``*.py`` at the source
  root is uploaded to ``/flash/`` (these are peer modules like
  ``buddy_ble.py`` that apps import but which shouldn't appear in
  the launcher menu). ``*.py`` under ``apps/`` is uploaded to
  ``/flash/apps/``, which is the directory UIFlow's stock App List
  scans for selectable entries. No custom ``boot.py`` is needed;
  UIFlow boots normally and the user picks the app from the menu.
  ``/flash/apps/`` is created on the device if it doesn't exist.

The nested layout is what a well-behaved UIFlow app should use —
it composes cleanly with the stock launcher, doesn't overwrite
UIFlow's boot chain, and makes it obvious which files are apps
(visible) vs. shared modules (hidden).

Transfer uses paste-mode REPL with base64-chunked writes — slow
(~3 KB/s) but depends on nothing beyond stock MicroPython. A faster
alternative is ``mpremote``, but we stay in the dependency-free lane
to match the rest of the skill.

boot.py handling: if a root-level boot.py is being uploaded and the
device has a boot.py but no boot_uiflow.py backup, we rename the
existing one first. This preserves UIFlow's original startup so the
device can be reverted if the custom bundle is later removed. Once
a backup exists, we don't overwrite it — the first snapshot is
assumed to be the genuine UIFlow boot and later states are
derivative. Nested-layout bundles don't ship a boot.py, so this
path is skipped cleanly.
"""

from __future__ import annotations

import argparse
import base64
import glob
import os
import sys
import time
from typing import Iterable, Optional

import mpy_repl


_CHUNK_BYTES = 512


def _paste_or_raise(s, script: str, settle: float = 0.3, what: str = "paste") -> str:
    raw = mpy_repl.paste_exec(s, script, settle=settle)
    text = raw.decode("utf-8", errors="replace")
    # paste_exec already strips its own sentinel, so anything remaining
    # is device-side output. A traceback means the MicroPython side
    # blew up and we should not continue.
    if "Traceback" in text:
        raise RuntimeError("{} failed:\n{}".format(what, text))
    return text


def _set_user_app_boot_mode(s) -> None:
    """Set NVS ``uiflow.boot_option`` to 2 (user-app mode) so UIFlow's
    stock boot.py runs our ``/flash/main.py`` instead of starting its
    framework.

    Why this matters: with ``boot_option=1`` (the default on a freshly
    flashed device), UIFlow starts a background BLE advertise for
    flow.m5stack.com pairing. On ESP32-S3 Cardputer-Adv that
    background advertise wedges the NimBLE controller so that every
    subsequent ``gap_advertise(adv_data=...)`` call from user code
    returns ``OSError(-519)`` — the device advertises with empty AD
    fields and is invisible to strict scanners (iOS Bluetooth,
    desktop Claude Buddy app). Setting ``boot_option=2`` makes
    UIFlow's boot.py hand straight to ``/flash/main.py`` without
    touching BLE itself, leaving the stack pristine for our code.

    We only call this when the bundle ships a ``main.py`` at root —
    those are bundles that want to own the boot flow entirely. A
    pure ``apps/`` bundle (no root main.py) keeps UIFlow's default
    launcher and boot_option.
    """
    script = (
        "import esp32\n"
        "nvs = esp32.NVS('uiflow')\n"
        "try:\n"
        "    cur = nvs.get_u8('boot_option')\n"
        "except Exception:\n"
        "    cur = None\n"
        "if cur != 2:\n"
        "    nvs.set_u8('boot_option', 2)\n"
        "    nvs.commit()\n"
        "    print('BOOT_OPTION_SET 2 (was', cur, ')')\n"
        "else:\n"
        "    print('BOOT_OPTION_ALREADY 2')\n"
    )
    out = _paste_or_raise(s, script, settle=0.3, what="set boot_option=2")
    if "BOOT_OPTION_SET" not in out and "BOOT_OPTION_ALREADY" not in out:
        raise RuntimeError("boot_option set didn't confirm:\n" + out)
    # Surface the outcome to the user — quietly changing NVS state is
    # the kind of thing that's nice to see in the provision log.
    for ln in out.splitlines():
        if "BOOT_OPTION" in ln:
            sys.stderr.write(ln.strip() + "\n")


def _backup_boot_if_needed(s) -> None:
    probe = (
        "import uos\n"
        "try:\n"
        "    uos.stat('/flash/boot_uiflow.py')\n"
        "    have_backup = True\n"
        "except OSError:\n"
        "    have_backup = False\n"
        "try:\n"
        "    uos.stat('/flash/boot.py')\n"
        "    have_boot = True\n"
        "except OSError:\n"
        "    have_boot = False\n"
        "print('BOOT_STATE', have_backup, have_boot)\n"
    )
    text = _paste_or_raise(s, probe, settle=0.3, what="probe boot.py")
    if "BOOT_STATE False True" in text:
        rename = (
            "import uos\n"
            "uos.rename('/flash/boot.py', '/flash/boot_uiflow.py')\n"
            "print('BACKED_UP')\n"
        )
        out = _paste_or_raise(s, rename, settle=0.3, what="rename boot.py")
        if "BACKED_UP" not in out:
            raise RuntimeError("boot.py backup didn't confirm:\n" + out)
        sys.stderr.write("backed up /flash/boot.py -> /flash/boot_uiflow.py\n")


def _sweep_stale_apps(s, bundle_apps) -> None:
    """Remove any ``*.py`` files from ``/flash/apps/`` that aren't in
    ``bundle_apps``.

    This is only called when the bundle's launcher owns the menu
    (i.e. the bundle ships a root ``main.py``) — in that case the
    user expects to see exactly the apps the bundle defines, not
    whatever stock UIFlow demos happen to be in the firmware image
    (notably ``helloworld.py``, which UIFlow bakes into the flash
    image and re-installs on every fresh firmware flash).

    ``bundle_apps`` is the list of basenames we're about to upload
    (e.g. ``["claude_buddy.py", "hello_cardputer.py", "snake.py"]``).
    Anything else ending in ``.py`` under ``/flash/apps/`` is
    considered stale and removed. Non-``.py`` files are left alone
    on the off-chance someone has dropped data assets there.
    """
    # Build the "keep" set as a Python set literal in the script, so
    # the device doesn't have to parse a long argv-style string.
    keep_literal = "{" + ", ".join(repr(a) for a in bundle_apps) + "}"
    script = (
        "import uos\n"
        "keep = " + keep_literal + "\n"
        "removed = []\n"
        "try:\n"
        "    entries = uos.listdir('/flash/apps')\n"
        "except OSError:\n"
        "    entries = []\n"
        "for f in entries:\n"
        "    if f.endswith('.py') and f not in keep:\n"
        "        try:\n"
        "            uos.remove('/flash/apps/' + f)\n"
        "            removed.append(f)\n"
        "        except OSError as e:\n"
        "            print('SWEEP_ERR', f, e)\n"
        "print('SWEEP_REMOVED', removed)\n"
    )
    out = _paste_or_raise(s, script, settle=0.3, what="sweep stale /flash/apps")
    # Surface which files were swept so the provisioning log is
    # honest about what changed on the device.
    for ln in out.splitlines():
        if ln.startswith("SWEEP_REMOVED") or ln.startswith("SWEEP_ERR"):
            sys.stderr.write(ln.strip() + "\n")


def _ensure_dir(s, dev_dir: str) -> None:
    """Create ``dev_dir`` on the device if it's missing. Idempotent.

    MicroPython's ``os.mkdir`` raises ``OSError`` if the directory
    already exists; we swallow exactly that case and re-raise anything
    else so a genuine problem (read-only FS, out of space) surfaces
    instead of being silently skipped.
    """
    script = (
        "import uos\n"
        "try:\n"
        "    uos.stat({dev!r})\n"
        "    print('DIR_OK', {dev!r})\n"
        "except OSError:\n"
        "    uos.mkdir({dev!r})\n"
        "    print('DIR_CREATED', {dev!r})\n"
    ).format(dev=dev_dir)
    out = _paste_or_raise(s, script, settle=0.2, what="mkdir {}".format(dev_dir))
    # Device-side print joins args with a space and calls str() (not
    # repr()) on each, so the output line is "DIR_OK /flash/apps"
    # with no quotes around the path. Match that shape.
    if ("DIR_OK " + dev_dir) not in out and ("DIR_CREATED " + dev_dir) not in out:
        raise RuntimeError("ensure_dir didn't confirm:\n" + out)


def _upload_file(s, src_path: str, dest_path: str) -> None:
    """Upload a local file to an absolute device path.

    ``dest_path`` must be the full path on the device, including any
    subdirectory (e.g. ``/flash/apps/claude_buddy.py``). Callers that
    want the file under a subdir are responsible for having called
    :func:`_ensure_dir` for the parent first; ``open(path, 'wb')``
    does not auto-mkdir parents on MicroPython and will raise
    ``ENOENT`` if the directory is missing.
    """
    with open(src_path, "rb") as f:
        data = f.read()

    label = os.path.basename(dest_path)

    # Open the destination once, then stream base64 chunks. Paste-mode
    # blocks share a globals namespace, so `fp` persists across them.
    head = (
        "import ubinascii\n"
        'fp = open({!r}, "wb")\n'.format(dest_path)
    )
    _paste_or_raise(s, head, settle=0.2, what="open {}".format(dest_path))

    total = len(data)
    sent = 0
    while sent < total:
        chunk = data[sent : sent + _CHUNK_BYTES]
        b64 = base64.b64encode(chunk).decode("ascii")
        # One paste block per chunk. Larger blocks occasionally get
        # truncated on the RX side, so we keep each write small.
        body = 'fp.write(ubinascii.a2b_base64("{}"))\n'.format(b64)
        _paste_or_raise(s, body, settle=0.05, what="chunk @ {}".format(sent))
        sent += len(chunk)
        sys.stderr.write("\r  {}: {}/{} bytes".format(label, sent, total))
        sys.stderr.flush()
    sys.stderr.write("\n")

    tail = "fp.close()\nprint('WROTE', {!r})\n".format(dest_path)
    out = _paste_or_raise(s, tail, settle=0.2, what="close {}".format(dest_path))
    # Same str()-not-repr() quirk as _ensure_dir — the device prints
    # "WROTE /flash/apps/claude_buddy.py" with no surrounding quotes.
    # fp.close() flushes to flash and can occasionally take longer than
    # the 0.2 s settle (seen on Core2 with an empty capture), so give the
    # confirmation up to ~2 s more before calling it a failure.
    for _ in range(4):
        if ("WROTE " + dest_path) in out:
            break
        out += mpy_repl.drain(s, wait=0.5).decode("utf-8", errors="replace")
    if ("WROTE " + dest_path) not in out:
        raise RuntimeError("close/verify failed:\n" + out)


def _plan_uploads(src_dir: str):
    """Walk ``src_dir`` and return a list of ``(src_path, dest_path)``.

    Everything at the source root goes to ``/flash/``. Anything in an
    ``apps/`` subdir goes to ``/flash/apps/`` — that's the directory
    UIFlow's stock App List reads, so apps placed there show up in
    the launcher menu. Other subdirs aren't handled here; if a bundle
    needs a different layout, extend this function rather than bolting
    it on at the caller.

    Returned in a stable order: root files first (peer modules load
    before apps that import them), then apps/ files alphabetically.
    """
    plan = []
    root_files = sorted(glob.glob(os.path.join(src_dir, "*.py")))
    for p in root_files:
        plan.append((p, "/flash/" + os.path.basename(p)))

    apps_dir = os.path.join(src_dir, "apps")
    if os.path.isdir(apps_dir):
        for p in sorted(glob.glob(os.path.join(apps_dir, "*.py"))):
            plan.append((p, "/flash/apps/" + os.path.basename(p)))

    return plan


def install(
    port: str,
    src_dir: str,
    files: Optional[Iterable[str]] = None,
    reset_when_done: bool = True,
) -> None:
    """Push a bundle from ``src_dir`` onto the device.

    Layout handling is described in the module docstring: root ``*.py``
    lands at ``/flash/``, ``apps/*.py`` lands at ``/flash/apps/``.

    If ``files`` is given, only those basenames are uploaded — the
    basenames are matched against the plan's destination basenames,
    so e.g. ``files=["claude_buddy.py"]`` still resolves to
    ``/flash/apps/claude_buddy.py`` if ``claude_buddy.py`` lives under
    ``apps/``. This is the escape hatch for pushing a single edited
    file without re-uploading the whole bundle.
    """
    src_dir = os.path.abspath(src_dir)
    plan = _plan_uploads(src_dir)
    if not plan:
        raise RuntimeError("no .py files found under {}".format(src_dir))

    if files is not None:
        wanted = set(files)
        plan = [(s, d) for (s, d) in plan if os.path.basename(d) in wanted]
        if not plan:
            raise RuntimeError(
                "requested files not found in bundle: {}".format(sorted(wanted))
            )

    # Which root-level basenames are in the plan? boot.py backup
    # logic only fires for root /flash/ targets — an apps/boot.py
    # wouldn't affect UIFlow's boot chain so there's nothing to back
    # up. This keeps the behavior unchanged for flat-layout bundles.
    root_basenames = {os.path.basename(d) for (_, d) in plan if d.startswith("/flash/") and "/flash/apps/" not in d}
    need_apps_dir = any(d.startswith("/flash/apps/") for (_, d) in plan)

    s = mpy_repl.open_port(port)
    try:
        # Get to a known clean state. We used to call hard_reset() here,
        # but DTR/RTS are a no-op on ESP32-S3 native USB (Cardputer-Adv,
        # CoreS3) because the bridge chip isn't there to drive EN/GPIO0
        # — the reset was silent and whatever was running just kept
        # running. Interrupting to REPL gets us writable access on both
        # UART-bridge and native-USB devices without needing to know
        # which topology we're on. The short sleep lets any final bytes
        # from pre-install UIFlow activity drain before we Ctrl-C.
        time.sleep(0.3)
        mpy_repl.interrupt_to_repl(s)
        mpy_repl.drain(s, wait=0.3)

        if "boot.py" in root_basenames:
            _backup_boot_if_needed(s)

        # If the bundle ships its own main.py at /flash/, that's a
        # signal it wants to own the boot flow. Set UIFlow's NVS
        # boot_option to 2 (user-app mode) so UIFlow's boot.py runs
        # our main.py directly instead of its own framework — the
        # latter touches BLE and wedges the controller on ESP32-S3.
        # See _set_user_app_boot_mode docstring for the gory details.
        owns_launcher = "main.py" in root_basenames
        if owns_launcher:
            _set_user_app_boot_mode(s)

        if need_apps_dir:
            _ensure_dir(s, "/flash/apps")

        # If the bundle owns the launcher AND is installing its own
        # apps, sweep any stale *.py files out of /flash/apps/ before
        # uploading. Otherwise stock UIFlow example files that ship
        # with the firmware image (helloworld.py, etc.) remain
        # visible in our custom launcher's menu — every re-flash
        # re-introduces them because they're baked into the UIFlow
        # MicroPython partition. The sweep only runs when we're
        # about to replace the set anyway, so it can't orphan
        # user-added files on a bundle that isn't managing the
        # launcher holistically.
        if owns_launcher and need_apps_dir:
            bundle_apps = sorted(
                os.path.basename(d)
                for (_, d) in plan
                if d.startswith("/flash/apps/")
            )
            _sweep_stale_apps(s, bundle_apps)

        for src_path, dest_path in plan:
            sys.stderr.write("uploading {} -> {}\n".format(os.path.basename(src_path), dest_path))
            _upload_file(s, src_path, dest_path)

        if reset_when_done:
            sys.stderr.write("rebooting device to run the new bundle...\n")
            # repl_reset sends `machine.reset()` through the REPL, which
            # works on every MicroPython-capable USB topology including
            # ESP32-S3 native USB. hard_reset (DTR/RTS) would silently
            # no-op here on Cardputer-Adv, which caused a confusing
            # "files uploaded but nothing changes on screen" regression.
            mpy_repl.repl_reset(s)
    finally:
        s.close()


# A small registry of well-known bundles so callers can pass a name
# instead of a full path. Extend as more bundles graduate from
# "scratch project" to "thing I want on every new M5 I provision".
#
# Resolution order for the ``buddy`` shorthand (first hit wins):
#   1. ``$M5_BUDDY_DIR`` if set — explicit override, always wins.
#      Useful for unusual setups: bundle on a different drive,
#      monorepo at a custom path, OneDrive-redirected Downloads, etc.
#   2. Script-relative ``<repo>/buddy/device``. Walks up four levels
#      from this file
#      (``<repo>/.claude/skills/m5-onboard/scripts/install_apps.py``
#      → ``<repo>/.claude/skills/m5-onboard/`` →
#      ``<repo>/.claude/skills/`` → ``<repo>/.claude/`` →
#      ``<repo>/``, then join ``buddy/device``) so the bundle is
#      found regardless of where the repo was cloned. Uses
#      ``os.path.realpath(__file__)`` so any symlink at
#      ``~/.claude/skills/m5-onboard/`` resolves to the real clone
#      before walking. This is the path that should hit on every
#      normal install — clone anywhere, it works.
#   3. Conventional clone locations under ``~`` for users who
#      cloned to a path the script itself isn't in (e.g. running
#      install_apps.py from a copy somewhere weird while the
#      bundle lives in a familiar spot):
#         ~/Downloads/m5stack/buddy/device
#         ~/Desktop/m5stack/buddy/device
#
# If none of the candidates exist on disk, we still return (1)/(2) so
# the eventual error message points at where we'd have looked first
# — typically the script-relative path, which is the most useful
# breadcrumb for "I cloned the repo and the bundle is missing."

def _candidate_buddy_dirs() -> list[str]:
    cands: list[str] = []
    here = os.path.dirname(os.path.realpath(__file__))
    cands.append(
        os.path.normpath(os.path.join(here, "..", "..", "..", "..", "buddy", "device"))
    )
    home = os.path.expanduser("~")
    cands.append(os.path.join(home, "Downloads", "m5stack", "buddy", "device"))
    cands.append(os.path.join(home, "Desktop", "m5stack", "buddy", "device"))
    return cands


def _default_buddy_dir() -> str:
    for c in _candidate_buddy_dirs():
        if os.path.isdir(c):
            return c
    # Fall back to the first (script-relative) so an "missing bundle"
    # error mentions a path the user is likely to recognize as
    # "oh, that's inside my checkout."
    return _candidate_buddy_dirs()[0]


_DEFAULT_BUDDY_DIR = _default_buddy_dir()
KNOWN_BUNDLES = {
    "buddy": os.environ.get("M5_BUDDY_DIR") or _DEFAULT_BUDDY_DIR,
    # Core2 port: own launcher/UI/app, shared BLE/protocol/state modules
    # symlinked from buddy/device/.
    "buddy-core2": os.path.join(os.path.dirname(_DEFAULT_BUDDY_DIR), "device-core2"),
}


def resolve_src(src: str) -> str:
    if src in KNOWN_BUNDLES:
        return KNOWN_BUNDLES[src]
    return os.path.expanduser(src)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Install a directory of MicroPython apps onto an M5Stack."
    )
    ap.add_argument("--port", required=True)
    ap.add_argument(
        "--src",
        required=True,
        help=(
            "Directory of .py sources, OR a well-known bundle name. "
            "Known names: {}.".format(", ".join(sorted(KNOWN_BUNDLES)))
        ),
    )
    ap.add_argument(
        "--files",
        nargs="*",
        help="Explicit file basenames (default: every *.py under --src).",
    )
    ap.add_argument(
        "--no-reset",
        action="store_true",
        help="Leave the device at the REPL after upload (skip the final reboot).",
    )
    args = ap.parse_args()

    # "buddy" means "the Buddy bundle for this board": on boards with
    # their own port (e.g. Core2) swap in that bundle, since the stock
    # one needs the Cardputer keyboard. Explicit paths/names are kept.
    if args.src == "buddy":
        import identify
        ident = identify.identify(args.port)
        mapped = identify.bundle_for(args.src, ident["variant"])
        if mapped != args.src:
            sys.stderr.write(
                "Identified {} → installing the '{}' bundle.\n".format(
                    ident["machine"], mapped
                )
            )
            args.src = mapped

    install(
        port=args.port,
        src_dir=resolve_src(args.src),
        files=args.files,
        reset_when_done=not args.no_reset,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
