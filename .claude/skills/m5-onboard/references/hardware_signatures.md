---
name: hardware_signatures
description: I2C addresses and chip IDs that identify each M5Stack Core variant, mapped to the firmware variant the onboarder should fetch.
---

# Identifying which M5Stack you have

esptool `flash_id` gives you the ESP chip family and flash size, but
multiple Core variants share the same ESP32 + same flash. To pick the
right firmware you also need the I2C bus scan (taken once UIFlow is on)
or the PSRAM presence. Use this table.

## Classic Core family (original ESP32)

All of these are `ESP32-D0WDQ6` or `ESP32-D0WDQ6-V3`, CH9102 USB bridge,
40 MHz crystal. The distinction is on the internal I2C bus (SDA=21, SCL=22):

| Model        | Flash | PSRAM | I2C devices           | Firmware variant |
|--------------|-------|-------|------------------------|------------------|
| Basic v2.6   | 16 MB | no    | 0x75 (IP5306)          | `basic-16mb`     |
| Basic v2.x   |  4 MB | no    | 0x75 (IP5306)          | `basic-4mb`      |
| Gray         | 16 MB | no    | 0x75, 0x68 or 0x69 (MPU6886) | `basic-16mb` (Gray uses same UIFlow Core image) |
| FIRE         | 16 MB | 4 MB  | 0x75, 0x68, 0x34 (IP5306 + MPU + AXP) | `basic-16mb` |

**Gotcha — floating-bus reads.** A brand-new or factory-test image will
sometimes respond to WHOAMI reads on bus addresses that have no real
device, because the bus is floating. Trust the I2C *scan* output after
UIFlow is running, not the factory test's IMU detection.

## Core2 and CoreS3

| Model   | ESP chip        | USB        | Distinctive I2C    | Firmware variant |
|---------|-----------------|-------------|--------------------|------------------|
| Core2   | ESP32-D0WDQ6-V3 | CH9102 (v1.1) or Silabs CP2104 (v1.0) | 0x34 (AXP192), 0x51 (BM8563 RTC), 0x38 (touch) | `core2` |
| CoreS3  | ESP32-S3        | native USB  | 0x36 (AXP2101), 0x51, 0x38 | `cores3` |

CoreS3 enumerates as its own USB vendor (`0x303A`) because it uses the
ESP32-S3's built-in USB-JTAG bridge — you won't see a CH9102.

## Cardputer family

| Model           | ESP chip | USB         | Flash | Distinctive traits                  | Firmware variant |
|-----------------|----------|-------------|-------|-------------------------------------|------------------|
| Cardputer       | ESP32-S3 | native USB  |  8 MB | 1.14" LCD + full 56-key QWERTY      | `cardputer`      |
| Cardputer Adv   | ESP32-S3 | native USB  |  8 MB | Same form factor, adds IMU + IR + extra sensors | `cardputer-adv` |

Telling them apart without opening the case: esptool says ESP32-S3 + 8 MB
for both. The silkscreen on the underside has the model name. You can
also probe over REPL after flashing — the Advance has additional I2C
devices that the original lacks (check with `smoke_test.py`).

Both variants live in the `cardputer` category in the M5Burner manifest.
They are distinguished by manifest *name*, not version suffix:
- Original → `UIFlow2.0`
- Advance  → `UIFlow2.0 Cardputer-Adv`

## StickC family (not yet supported)

M5StickC Plus and StickC Plus2 also exist; this skill doesn't currently
cover them. If support is needed, add variants to `fetch_firmware.py`
and a new row here.

## Automatic identification (`scripts/identify.py`)

When a board on a USB-UART bridge is already running MicroPython,
`identify.py` reads its build name (`sys.implementation._machine`) and
maps it to a variant — no I2C scan needed. Verified build names:

| `_machine` contains | Variant | Buddy bundle |
|---------------------|---------|--------------|
| `Core2` (e.g. `M5STACK Core2 with ESP32(SPIRAM)`, UIFlow 2.5.4) | `core2` | `buddy-core2` |

Add a row to `_MACHINE_TO_VARIANT` in `identify.py` only after
checking the build name on real hardware — an unrecognized name makes
`onboard.py` ask for `--variant`, which is safer than a wrong flash.

## How the onboarder uses this

- `detect.py --identify` prints the chip + MAC + flash size.
- After flashing (or against an already-UIFlowed device), run
  `smoke_test.py` and read the `I2C [...]` line.
- Cross-reference with the tables above to pick the firmware variant
  for the next device of the same model, so you don't have to probe
  again.
