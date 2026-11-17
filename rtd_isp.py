#!/usr/bin/python3
"""Read and patch the firmware of a Realtek-scaler monitor over its video cable (Linux).

Realtek scalers (tested: RTD2513AR in an HKC MB24S1A) answer on the DDC line at I2C address
0x4A. Setting bit 7 of register 0x6F halts the controller and hands the host its SPI flash:
registers 0x60..0x69 run one flash command, 0x70 streams data, 0x6F/0x71 program a page and
0x6F/0x72..0x75 compute a checksum. The protocol comes from ghent360/RTD-2660-Programmer.

The monitor only needs power and a video cable to this computer. BUS is the number of its
/dev/i2c-N device (`ddcutil detect` shows it). The screen goes dark while the controller is
halted; it resumes when the command finishes.

  rtd_isp.py BUS probe               does anything answer at 0x4A? (changes nothing)
  rtd_isp.py BUS id                  flash chip ID and status register
  rtd_isp.py BUS dump FILE           read the whole chip, every block read until two reads agree,
                                     then check the result against the controller's checksum
  rtd_isp.py BUS verify FILE         compare the chip with FILE by checksum (and say where they differ)
  rtd_isp.py BUS flash FILE --only A-B --yes
                                     rewrite the 4 KB sectors inside A-B (hex) that differ from FILE,
                                     verify each. --only is required: the monitor keeps its own saved
                                     settings in the same chip, and they change after your dump.

`flash` erases and rewrites flash sectors. Keep a verified dump first. A wrong image can leave
the monitor unusable until the chip is rewritten with a clip-on programmer.
"""
import fcntl
import os
import sys
import time

I2C_SLAVE, ADDR = 0x0703, 0x4A


class Bus:
    def __init__(self, n):
        self.fd = os.open(f'/dev/i2c-{n}', os.O_RDWR)
        fcntl.ioctl(self.fd, I2C_SLAVE, ADDR)

    def read(self, reg, n=1):
        os.write(self.fd, bytes([reg]))
        return os.read(self.fd, n)

    def write(self, reg, value):
        os.write(self.fd, bytes([reg, value]))

    def wait_clear(self, reg, mask, timeout=5):
        end = time.time() + timeout
        while self.read(reg)[0] & mask:
            if time.time() > end:
                raise RuntimeError(f'register {reg:#x} stayed busy')


def crc8(data):
    """The controller's checksum (polynomial x^8 + x^2 + x + 1, as in the reference tool)."""
    c = 0
    for x in data:
        c ^= x << 8
        for _ in range(8):
            if c & 0x8000:
                c ^= 0x1070 << 3
            c = (c << 1) & 0xFFFFFF
    return (c >> 8) & 255


def enter(bus, attempts=5):
    """Halt the controller and make sure its flash answers. For a few seconds after a
    previous session the controller is still restarting and the flash reads as FF; then
    let it go again and give it time undisturbed before the next try."""
    problem = 'no answer'
    for attempt in range(attempts):
        if attempt:
            time.sleep(6)
        try:
            bus.write(0x6F, 0x80)
            if bus.read(0x6F)[0] & 0x80:
                ident = flash_id(bus)
                if ident not in (b'\xff\xff\xff', b'\x00\x00\x00'):
                    return ident
                problem = 'flash did not identify itself'
            else:
                problem = 'controller did not enter service mode'
            bus.write(0x6F, 0x00)
        except (OSError, RuntimeError) as e:
            problem = str(e)
    raise RuntimeError(problem)


def leave(bus):
    bus.write(0x6F, 0x00)


def cmd(bus, kind, code, reads=0, writes=0, value=0):
    """One flash command. kind: 2 read, 3 write after WREN (also used for sector erase)."""
    reg = (kind << 5) | (writes << 3) | (reads << 1)
    bus.write(0x60, reg)
    bus.write(0x61, code)
    for i in range(writes):
        bus.write(0x64 + i, value >> (8 * (writes - 1 - i)) & 255)
    bus.write(0x60, reg | 1)
    bus.wait_clear(0x60, 1, 10)
    return bytes(bus.read(0x67 + i)[0] for i in range(reads))


def flash_id(bus):
    return cmd(bus, 2, 0x9F, reads=3)


def status(bus):
    return cmd(bus, 2, 0x05, reads=1)[0]


def wait_idle(bus, timeout=20):
    end = time.time() + timeout
    while status(bus) & 1:
        if time.time() > end:
            raise RuntimeError('flash stayed busy')
        time.sleep(.01)


def size_of(ident):
    return 1 << ident[2] if 0x10 <= ident[2] <= 0x18 else None


def read(bus, addr, n):
    """n bytes from addr. I2C reads glitch now and then, so every 1 KB block is read until
    two reads agree."""
    out = bytearray()
    for a in range(addr, addr + n, 1024):
        seen = {}
        while True:
            bus.write(0x60, 0x46)
            bus.write(0x61, 0x03)
            bus.write(0x64, a >> 16 & 255)
            bus.write(0x65, a >> 8 & 255)
            bus.write(0x66, a & 255)
            bus.write(0x60, 0x47)
            bus.wait_clear(0x60, 1)
            block = b''.join(bus.read(0x70, 64) for _ in range(16))
            seen[block] = seen.get(block, 0) + 1
            if seen[block] >= 2:
                break
            if sum(seen.values()) > 10:
                raise RuntimeError(f'unstable read at {a:#x}')
        out += block
    return bytes(out[:n])


def controller_crc(bus, start, end):
    for reg, v in ((0x64, start >> 16), (0x65, start >> 8), (0x66, start),
                   (0x72, end >> 16), (0x73, end >> 8), (0x74, end)):
        bus.write(reg, v & 255)
    bus.write(0x6F, 0x84)
    t = time.time()
    while not bus.read(0x6F)[0] & 2:
        if time.time() - t > 30:
            raise RuntimeError('checksum timeout')
    return bus.read(0x75)[0]


def program_page(bus, addr, page):
    assert len(page) == 256 and addr % 256 == 0
    bus.wait_clear(0x6F, 0x40)
    bus.write(0x71, 255)
    bus.write(0x64, addr >> 16 & 255)
    bus.write(0x65, addr >> 8 & 255)
    bus.write(0x66, addr & 255)
    for i in range(0, 256, 32):
        os.write(bus.fd, bytes([0x70]) + page[i:i + 32])
    bus.write(0x6F, 0xA0)
    bus.wait_clear(0x6F, 0x40)
    wait_idle(bus)


def sector_erase(bus, addr):
    assert addr % 4096 == 0
    cmd(bus, 3, 0x20, writes=3, value=addr)        # WREN, then 4 KB sector erase
    wait_idle(bus)


def differing_ranges(bus, image, step=0x10000):
    """64 KB ranges whose controller checksum differs from the image."""
    return [(a, a + step) for a in range(0, len(image), step)
            if controller_crc(bus, a, a + step - 1) != crc8(image[a:a + step])]


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    bus, what = Bus(int(sys.argv[1])), sys.argv[2]
    if what == 'probe':
        print('register 0x6F reads', bus.read(0x6F).hex())
        return
    ident = enter(bus)
    try:
        size = size_of(ident)
        print(f'flash ID {ident.hex()}, {size // 1024 if size else "?"} KB, status register {status(bus):02x}')
        if what == 'id':
            return
        if not size:
            sys.exit('unknown flash size; stopping')
        if what == 'dump':
            data = read(bus, 0, size)
            ok = controller_crc(bus, 0, size - 1) == crc8(data)
            open(sys.argv[3], 'wb').write(data)
            print(f'{sys.argv[3]}: {len(data)} bytes, checksum', 'matches the controller' if ok else 'DOES NOT MATCH')
            if not ok:
                sys.exit(1)
            return
        image = open(sys.argv[3], 'rb').read()
        if len(image) != size:
            sys.exit(f'image is {len(image)} bytes, chip is {size}')
        if what == 'verify':
            bad = differing_ranges(bus, image)
            print('chip equals image' if not bad else
                  'differs in ' + ', '.join(f'{a:#x}-{b - 1:#x}' for a, b in bad))
            sys.exit(1 if bad else 0)
        if what == 'flash':
            if '--only' not in sys.argv:
                sys.exit('give the address range to write, e.g. --only 0x0-0x6ffff')
            lo, hi = (int(x, 16) for x in sys.argv[sys.argv.index('--only') + 1].split('-'))
            sectors = []
            for a, b in differing_ranges(bus, image):
                chip = read(bus, a, b - a)
                sectors += [s for s in range(a, b, 4096)
                            if chip[s - a:s - a + 4096] != image[s:s + 4096] and lo <= s and s + 4095 <= hi]
            if '--yes' not in sys.argv:
                print(f'{len(sectors)} sector(s) differ inside the range:', ' '.join(hex(s) for s in sectors))
                sys.exit('add --yes to really write them')
            print(f'{len(sectors)} sector(s) to rewrite:', ' '.join(hex(s) for s in sectors))
            if not sectors:
                return
            before = status(bus)
            bus.write(0x6D, 0x02)                          # page-program opcode for the controller
            cmd(bus, 3, 0x01, writes=1, value=0x00)        # clear the block-protect bits
            wait_idle(bus)
            if status(bus) & 0x1C:
                sys.exit('flash is still write-protected; nothing written')
            try:
                for s in sectors:
                    for attempt in range(3):
                        sector_erase(bus, s)
                        for p in range(s, s + 4096, 256):
                            if image[p:p + 256] != b'\xff' * 256:
                                program_page(bus, p, image[p:p + 256])
                        if read(bus, s, 4096) == image[s:s + 4096]:
                            print(f'  {s:#x} written and verified')
                            break
                    else:
                        sys.exit(f'sector {s:#x} could not be written correctly; chip is in a mixed state')
            finally:
                cmd(bus, 3, 0x01, writes=1, value=before)  # put the protection back
                wait_idle(bus)
            ok = controller_crc(bus, lo, hi) == crc8(image[lo:hi + 1])
            print(f'checksum of {lo:#x}-{hi:#x}', 'matches the image' if ok else 'DOES NOT MATCH')
            sys.exit(0 if ok else 1)
        sys.exit(__doc__)
    finally:
        leave(bus)


if __name__ == '__main__':
    main()
