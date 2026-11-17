#!/usr/bin/python3
"""Apply the HKC MB24S1A fixes to your own firmware dump.  patch_mb24s1a.py IN.bin OUT.bin [--keep-auto-off]

Refuses to touch an image whose bytes around a patch are not exactly the expected ones."""
import hashlib
import sys

KNOWN = '2e1d0a384d34c3defc7805b1940067448aad60594d8f3cb031d0a90d9e21095d'
PATCHES = [
    # offset, context before the byte, old, new, what
    (0x2C441, bytes([0x12, 0xEA]), 0xC4, 0xED, 'DDC contrast set: use the 0..100 limits brightness uses'),
    (0x2D48F, bytes([0x12, 0xEA]), 0xC4, 0xED, 'DDC contrast get: same'),
    (0x1DE1B, bytes([0x90, 0xE4, 0x5F, 0xE0]), 0x04, 0x00, 'idle-minute counter never advances: no 4-hour auto power-off'),
]


def main():
    src, dst = sys.argv[1], sys.argv[2]
    data = bytearray(open(src, 'rb').read())
    digest = hashlib.sha256(data).hexdigest()
    print('input sha256', digest, '(the image this was developed on)' if digest == KNOWN else
          '(NOT the image this was developed on; context bytes are still checked)')
    for offset, context, old, new, what in PATCHES:
        if offset == 0x1DE1B and '--keep-auto-off' in sys.argv:
            continue
        if data[offset - len(context):offset] != context or data[offset] not in (old, new):
            sys.exit(f'{offset:#x}: unexpected bytes, this is a different firmware build. Nothing written.')
        data[offset] = new
        print(f'{offset:#07x}: {old:02X} -> {new:02X}  {what}')
    open(dst, 'wb').write(data)


if __name__ == '__main__':
    main()
