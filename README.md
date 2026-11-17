# HKC MB24S1A firmware fix (Realtek RTD2513AR)

Two fixes for the HKC MB24S1A monitor, three bytes in total, plus a Linux tool that reads and
patches the firmware of a Realtek-scaler monitor **over its HDMI/VGA cable**, without opening it.

| Problem | Cause in the firmware | Fix |
|---|---|---|
| Contrast set from the computer (DDC/CI, VCP 0x12) ends up 78 too high: `ddcutil setvcp 12 0` shows 78 in the monitor's menu, 22 shows 100, and anything higher runs the menu bar off its end. Menu values below 78 cannot be reached remotely. | The menu stores contrast as a plain 0–100 number, but the DDC handler converts the value with limits centre 128 / min 78 / max 178 first. Brightness uses limits 0–100 and is fine. | Make the contrast set and get handlers use the brightness limits. 2 bytes. |
| The monitor switches itself off after 4 hours without a button press. | A once-a-minute timer counts idle minutes; at 238 it starts a 120-second on-screen countdown, then powers off. | The counter no longer advances. 1 byte. |

No firmware image is included here. You read your own monitor's firmware, patch that file and
write it back.

**This can leave a monitor unusable if something goes wrong.** It was done once, on one unit.
Keep the verified dump. The recovery path if the cable route stops working is a clip-on SPI
programmer on the flash chip (see the end).

## The hardware

- Board `HKCMNT RTD2513AR-A` (A1.0-20181119), scaler Realtek RTD2513AR.
- Firmware in `U602`, a Macronix MX25L4006E (512 KB SPI flash, SOP-8, 3.3 V).
- The controller reports itself over DDC as `Mfg: RealTek`, firmware level 0.1, EDID
  manufacturer `HKC`, product 0, serial 0x01010101.
- Image this was developed on: sha256
  `2e1d0a384d34c3defc7805b1940067448aad60594d8f3cb031d0a90d9e21095d`.
  The patch script checks the bytes around each patch, so a different build is refused,
  not corrupted.

## Doing it

You need Linux, `i2c-dev` access to the monitor's DDC bus (the same thing `ddcutil` needs), and
the monitor connected by HDMI and powered. Find the bus number with `ddcutil detect`
(`/dev/i2c-N`). Stop anything else that talks DDC to the monitor while you work. The screen goes
dark while the controller is halted and comes back by itself.

```sh
./rtd_isp.py N probe                      # anything at I2C 0x4A? changes nothing
./rtd_isp.py N id                         # expect: flash ID c22013, 512 KB
./rtd_isp.py N dump original.bin          # a few minutes; must say the checksum matches the controller
cp original.bin original-keep-safe.bin

./patch_mb24s1a.py original.bin patched.bin            # add --keep-auto-off to fix contrast only
./rtd_isp.py N flash patched.bin --only 0x0-0x6ffff        # lists the sectors that would change
./rtd_isp.py N flash patched.bin --only 0x0-0x6ffff --yes  # writes and verifies them
```

Expect three sectors (`0x1d000`, `0x2c000`, `0x2d000`) to be rewritten. To undo, flash
`original.bin` with the same `--only` range.

Always pass `--only 0x0-0x6ffff`. The monitor saves its own settings in the last 64 KB of the
same chip (sectors around `0x7b000`), and they change after your dump; writing that area back
rolls the monitor's settings back to dump time.

Afterwards `ddcutil setvcp 12 50` should show 50 in the monitor's menu.

## What `rtd_isp.py` does

Realtek scalers answer at I2C address 0x4A on the DDC line. Bit 7 of register 0x6F halts the
controller and hands the host its SPI flash: registers 0x60–0x69 run one flash command, 0x70
streams data, 0x6F/0x71 program a page, and 0x6F with 0x72–0x75 computes a checksum over a range.
The protocol is from [ghent360/RTD-2660-Programmer](https://github.com/ghent360/RTD-2660-Programmer)
(written for the RTD2660/2662); it works unchanged on the RTD2513AR. Things learned on the way:

- I2C reads glitch now and then (a 1 KB block comes back as garbage), so every block is read
  until two reads agree, and a dump is checked against the controller's own checksum.
- The page-program opcode register (0x6D) reads 0xFF on this chip and has to be set to 0x02.
- The flash status register is 0x9C (block-protected). "Write enable, then write status 0x00"
  clears it; the tool puts the old value back when it is done.
- 4 KB sector erase is command 0x20 sent as "write after write-enable" with a 3-byte address.
- Clearing register 0x6F restarts the controller. For a few seconds after that the flash reads
  as FF through service mode; the tool waits and retries.

Other Realtek-scaler monitors may work with `probe`, `id`, `dump` and `verify`. `flash` on
anything else is at your own risk.

## How the bugs were found

The image is banked 8051 code (Keil), six 64 KB code banks plus a data area. Calls between banks
go through stubs at `0x09xx`. Addresses below are bank:address; file offset = bank × 0x10000 + address.

**Contrast.** `3:BDEB` converts a percentage to a "real" value and `3:BC16` converts back, both
taking centre/min/max as arguments. Two small helpers in bank 2 load those arguments:

- `2:EAED`: centre 50, min 0, max 100 (no change). Used by DDC brightness set (`2:C41B`) and get (`2:D47E`).
- `2:EAC4`: centre 128, min 78, max 178. Used by DDC contrast set (`2:C43F`) and get (`2:D48D`), and nowhere else.

Contrast is stored at XDATA `0xE158` and the menu shows that number as it is, 0–100. So a DDC
value `v` is stored as `v + 78`. The patch changes the two `LCALL 0xEAC4` into `LCALL 0xEAED`
(file offsets `0x2C441` and `0x2D48F`, `C4` → `ED`).

**Auto power-off.** Timer event 0x1C fires every 60 000 ms. Its handler at `1:DE08` increments a
counter at XDATA `0xE45F`; at 238 it starts event 0x1D, whose handler (`1:DE2F`) shows a countdown
from 120 and powers off. Button presses clear the counter (`4:FC10`). The patch turns the
`INC A` at `1:DE1B` into a `NOP` (file offset `0x1DE1B`, `04` → `00`). It only clears a bit, so
that page can even be programmed without an erase.

## If the cable route does not work: wires on the flash chip

`pico-diag/` is a small Raspberry Pi Pico program used while trying to read the chip directly
(Pico as a 3.3 V programmer with [pico-serprog](https://github.com/stacksmashing/pico-serprog)
and `flashrom`): it reports the level of each line, tries a chip-ID read with every pin
arrangement, and samples the lines to show whether the monitor's controller is using the bus.

Flash pinout with the printing upright and the dot bottom-left: bottom row left to right is
pins 1–4 (CS, data out, WP, GND), top row left to right is pins 8–5 (VCC, HOLD, clock, data in).

What that attempt showed: powering the chip in-circuit also powers the board, the controller
boots and reads the flash continuously (about 25 000 chip-select pulses in 1.7 s), so a
programmer cannot get a word in. Either lift the chip's VCC leg and power only the chip, or use
the cable route above, which is what worked.

## License

GPL-3.0. The service-mode protocol was learned from ghent360's BSD-licensed programmer.
