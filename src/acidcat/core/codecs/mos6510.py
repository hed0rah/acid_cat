"""A MOS 6510 interpreter, enough of one to run a SID tune's player.

The 6510 is a 6502 with an extra I/O port at $0000/$0001 that controls which
of RAM, BASIC ROM, KERNAL ROM and the I/O page is visible. Music code cares
about that port, so it is modelled; almost nothing else about the C64 is.

SCOPE. This exists to execute two subroutines -- a tune's init and its play --
and to let the writes they make to the SID registers be observed. It is not a
C64 emulator: there is no video, no disk, no accurate interrupt timing, and
cycle counts are used only to bound execution rather than to schedule anything.
That is enough for a player called at a fixed frame rate, and honestly not
enough for a tune that installs its own interrupt handler and expects to be
driven by it.

The undocumented opcodes are implemented because music code uses them. A player
squeezing itself into a few hundred bytes will happily use LAX to save a byte,
and a CPU that treats those as errors stops on real files.
"""

# flag bits in the status register, in hardware order
C, Z, I, D, B, U, V, N = 1, 2, 4, 8, 16, 32, 64, 128


class Bus(object):
    """64 KB of RAM with the C64's banking and an I/O window.

    Reads and writes to $D000-$DFFF go to `io_read`/`io_write` when the I/O
    page is banked in, which is what makes SID register writes visible. Every
    other address is plain RAM -- there are no ROM images here, so a tune that
    calls into KERNAL gets whatever the RAM under it holds.
    """

    __slots__ = ("ram", "sid_write", "raster", "_frame")

    def __init__(self, sid_write=None):
        self.ram = bytearray(65536)
        self.sid_write = sid_write
        self.raster = 0
        self._frame = 0

    def read(self, addr):
        if 0xD000 <= addr <= 0xDFFF and self.ram[1] & 3:
            return self._io_read(addr)
        return self.ram[addr]

    def write(self, addr, value):
        if 0xD000 <= addr <= 0xDFFF and self.ram[1] & 3:
            self._io_write(addr, value)
            return
        self.ram[addr] = value

    def _io_read(self, addr):
        if addr == 0xD012:
            # The raster counter. Players poll it to sync or to burn time, and
            # a value that never changes turns such a loop into a hang -- so it
            # advances on every read rather than standing still.
            self.raster = (self.raster + 1) & 0xFF
            return self.raster
        if addr == 0xD011:
            return 0x1B
        if 0xD400 <= addr <= 0xD7FF:
            # SID registers are write-only apart from the two ADC ports and the
            # oscillator/envelope readbacks. Returning 0 is what an unconnected
            # read gives, and is what a tune reading $D41B for randomness sees
            # as "no entropy" rather than a crash.
            return 0
        if 0xDC00 <= addr <= 0xDCFF:
            # CIA 1. Timers are not modelled; the interrupt-status read at
            # $DC0D returns 0 so a polling loop sees "no interrupt pending".
            return 0
        return 0

    def _io_write(self, addr, value):
        if 0xD400 <= addr <= 0xD7FF and self.sid_write is not None:
            # the FULL address, not a masked register number -- which chip a
            # write belongs to is decided by the caller, who knows where the
            # header put the second and third SID
            self.sid_write(addr, value)
        self.ram[addr] = value


class CPU(object):
    """A 6510 core. `run` executes until RTS unwinds past the entry frame."""

    __slots__ = ("bus", "a", "x", "y", "sp", "pc", "p", "cycles", "jammed")

    def __init__(self, bus):
        self.bus = bus
        self.a = self.x = self.y = 0
        self.sp = 0xFD
        self.pc = 0
        self.p = U | I
        self.cycles = 0
        self.jammed = False

    # ── helpers ─────────────────────────────────────────────────────

    def _rd(self, a):
        return self.bus.read(a & 0xFFFF)

    def _wr(self, a, v):
        self.bus.write(a & 0xFFFF, v & 0xFF)

    def _rd16(self, a):
        return self._rd(a) | (self._rd(a + 1) << 8)

    def _push(self, v):
        self.bus.ram[0x100 + self.sp] = v & 0xFF
        self.sp = (self.sp - 1) & 0xFF

    def _pop(self):
        self.sp = (self.sp + 1) & 0xFF
        return self.bus.ram[0x100 + self.sp]

    def _setzn(self, v):
        v &= 0xFF
        self.p = (self.p & ~(Z | N)) | (Z if v == 0 else 0) | (v & N)
        return v

    def run(self, addr, acc=0, max_cycles=2000000):
        """Call a subroutine at `addr` with `acc` in A, and return when it
        RTSes back past the frame we pushed.

        A sentinel return address is pushed rather than watching for a
        particular opcode: a player may RTS out of a nested call, and stopping
        at the first RTS would leave the tune half-initialised. Bounded by
        `max_cycles` because a tune that expects an interrupt it will never get
        can spin forever, and a hung render is worse than a short one.
        """
        self.a = acc & 0xFF
        self.x = self.y = 0
        self.sp = 0xFD
        self.p = U | I
        self.jammed = False
        sentinel = 0xFFFE
        self._push((sentinel - 1) >> 8)
        self._push((sentinel - 1) & 0xFF)
        self.pc = addr & 0xFFFF
        start = self.cycles
        while self.cycles - start < max_cycles:
            if self.pc == sentinel or self.jammed:
                return True
            self.step()
        return False                       # ran out of budget, not finished

    # ── addressing ──────────────────────────────────────────────────

    def _imm(self):
        a = self.pc
        self.pc = (self.pc + 1) & 0xFFFF
        return a

    def _zp(self):
        a = self._rd(self.pc)
        self.pc = (self.pc + 1) & 0xFFFF
        return a

    def _zpx(self):
        return (self._zp() + self.x) & 0xFF

    def _zpy(self):
        return (self._zp() + self.y) & 0xFF

    def _abs(self):
        a = self._rd16(self.pc)
        self.pc = (self.pc + 2) & 0xFFFF
        return a

    def _abx(self):
        return (self._abs() + self.x) & 0xFFFF

    def _aby(self):
        return (self._abs() + self.y) & 0xFFFF

    def _indx(self):
        z = (self._zp() + self.x) & 0xFF
        return self.bus.ram[z] | (self.bus.ram[(z + 1) & 0xFF] << 8)

    def _indy(self):
        z = self._zp()
        base = self.bus.ram[z] | (self.bus.ram[(z + 1) & 0xFF] << 8)
        return (base + self.y) & 0xFFFF

    def _ind(self):
        a = self._abs()
        # the JMP ($xxFF) page-wrap bug, which real code has relied on
        hi = (a & 0xFF00) | ((a + 1) & 0xFF)
        return self._rd(a) | (self._rd(hi) << 8)

    # ── arithmetic ──────────────────────────────────────────────────

    def _adc(self, v):
        a = self.a
        if self.p & D:
            lo = (a & 0x0F) + (v & 0x0F) + (self.p & C)
            hi = (a >> 4) + (v >> 4)
            if lo > 9:
                lo += 6
                hi += 1
            self.p &= ~C
            if hi > 9:
                hi += 6
                self.p |= C
            r = ((hi << 4) | (lo & 0x0F)) & 0xFF
            self._setzn(r)
            self.a = r
            return
        s = a + v + (self.p & C)
        self.p = (self.p & ~(C | V)) | (C if s > 0xFF else 0)
        if (~(a ^ v) & (a ^ s)) & 0x80:
            self.p |= V
        self.a = self._setzn(s)

    def _sbc(self, v):
        if self.p & D:
            a = self.a
            borrow = (self.p & C) ^ 1
            lo = (a & 0x0F) - (v & 0x0F) - borrow
            hi = (a >> 4) - (v >> 4)
            if lo & 0x10:
                lo -= 6
                hi -= 1
            if hi & 0x10:
                hi -= 6
            d = a - v - borrow
            self.p = (self.p & ~(C | V)) | (0 if d & 0x100 else C)
            if ((a ^ v) & (a ^ d)) & 0x80:
                self.p |= V
            self.a = ((hi << 4) | (lo & 0x0F)) & 0xFF
            self._setzn(self.a)
            return
        self._adc((~v) & 0xFF)

    def _cmp(self, reg, v):
        d = (reg - v) & 0x1FF
        self.p = (self.p & ~C) | (C if reg >= v else 0)
        self._setzn(d)

    def _branch(self, take):
        off = self._rd(self.pc)
        self.pc = (self.pc + 1) & 0xFFFF
        if take:
            if off & 0x80:
                off -= 0x100
            self.pc = (self.pc + off) & 0xFFFF
            self.cycles += 1

    def _asl(self, v):
        self.p = (self.p & ~C) | (1 if v & 0x80 else 0)
        return self._setzn(v << 1)

    def _lsr(self, v):
        self.p = (self.p & ~C) | (v & 1)
        return self._setzn(v >> 1)

    def _rol(self, v):
        c = self.p & C
        self.p = (self.p & ~C) | (1 if v & 0x80 else 0)
        return self._setzn((v << 1) | c)

    def _ror(self, v):
        c = (self.p & C) << 7
        self.p = (self.p & ~C) | (v & 1)
        return self._setzn((v >> 1) | c)

    # ── the interpreter ─────────────────────────────────────────────

    def step(self):
        op = self._rd(self.pc)
        self.pc = (self.pc + 1) & 0xFFFF
        self.cycles += 2

        # loads
        if op == 0xA9:   self.a = self._setzn(self._rd(self._imm()))
        elif op == 0xA5: self.a = self._setzn(self._rd(self._zp()))
        elif op == 0xB5: self.a = self._setzn(self._rd(self._zpx()))
        elif op == 0xAD: self.a = self._setzn(self._rd(self._abs()))
        elif op == 0xBD: self.a = self._setzn(self._rd(self._abx()))
        elif op == 0xB9: self.a = self._setzn(self._rd(self._aby()))
        elif op == 0xA1: self.a = self._setzn(self._rd(self._indx()))
        elif op == 0xB1: self.a = self._setzn(self._rd(self._indy()))
        elif op == 0xA2: self.x = self._setzn(self._rd(self._imm()))
        elif op == 0xA6: self.x = self._setzn(self._rd(self._zp()))
        elif op == 0xB6: self.x = self._setzn(self._rd(self._zpy()))
        elif op == 0xAE: self.x = self._setzn(self._rd(self._abs()))
        elif op == 0xBE: self.x = self._setzn(self._rd(self._aby()))
        elif op == 0xA0: self.y = self._setzn(self._rd(self._imm()))
        elif op == 0xA4: self.y = self._setzn(self._rd(self._zp()))
        elif op == 0xB4: self.y = self._setzn(self._rd(self._zpx()))
        elif op == 0xAC: self.y = self._setzn(self._rd(self._abs()))
        elif op == 0xBC: self.y = self._setzn(self._rd(self._abx()))

        # stores
        elif op == 0x85: self._wr(self._zp(), self.a)
        elif op == 0x95: self._wr(self._zpx(), self.a)
        elif op == 0x8D: self._wr(self._abs(), self.a)
        elif op == 0x9D: self._wr(self._abx(), self.a)
        elif op == 0x99: self._wr(self._aby(), self.a)
        elif op == 0x81: self._wr(self._indx(), self.a)
        elif op == 0x91: self._wr(self._indy(), self.a)
        elif op == 0x86: self._wr(self._zp(), self.x)
        elif op == 0x96: self._wr(self._zpy(), self.x)
        elif op == 0x8E: self._wr(self._abs(), self.x)
        elif op == 0x84: self._wr(self._zp(), self.y)
        elif op == 0x94: self._wr(self._zpx(), self.y)
        elif op == 0x8C: self._wr(self._abs(), self.y)

        # transfers
        elif op == 0xAA: self.x = self._setzn(self.a)
        elif op == 0x8A: self.a = self._setzn(self.x)
        elif op == 0xA8: self.y = self._setzn(self.a)
        elif op == 0x98: self.a = self._setzn(self.y)
        elif op == 0xBA: self.x = self._setzn(self.sp)
        elif op == 0x9A: self.sp = self.x

        # stack
        elif op == 0x48: self._push(self.a)
        elif op == 0x68: self.a = self._setzn(self._pop())
        elif op == 0x08: self._push(self.p | B | U)
        elif op == 0x28: self.p = (self._pop() | U) & ~B

        # logic
        elif op == 0x29: self.a = self._setzn(self.a & self._rd(self._imm()))
        elif op == 0x25: self.a = self._setzn(self.a & self._rd(self._zp()))
        elif op == 0x35: self.a = self._setzn(self.a & self._rd(self._zpx()))
        elif op == 0x2D: self.a = self._setzn(self.a & self._rd(self._abs()))
        elif op == 0x3D: self.a = self._setzn(self.a & self._rd(self._abx()))
        elif op == 0x39: self.a = self._setzn(self.a & self._rd(self._aby()))
        elif op == 0x21: self.a = self._setzn(self.a & self._rd(self._indx()))
        elif op == 0x31: self.a = self._setzn(self.a & self._rd(self._indy()))
        elif op == 0x09: self.a = self._setzn(self.a | self._rd(self._imm()))
        elif op == 0x05: self.a = self._setzn(self.a | self._rd(self._zp()))
        elif op == 0x15: self.a = self._setzn(self.a | self._rd(self._zpx()))
        elif op == 0x0D: self.a = self._setzn(self.a | self._rd(self._abs()))
        elif op == 0x1D: self.a = self._setzn(self.a | self._rd(self._abx()))
        elif op == 0x19: self.a = self._setzn(self.a | self._rd(self._aby()))
        elif op == 0x01: self.a = self._setzn(self.a | self._rd(self._indx()))
        elif op == 0x11: self.a = self._setzn(self.a | self._rd(self._indy()))
        elif op == 0x49: self.a = self._setzn(self.a ^ self._rd(self._imm()))
        elif op == 0x45: self.a = self._setzn(self.a ^ self._rd(self._zp()))
        elif op == 0x55: self.a = self._setzn(self.a ^ self._rd(self._zpx()))
        elif op == 0x4D: self.a = self._setzn(self.a ^ self._rd(self._abs()))
        elif op == 0x5D: self.a = self._setzn(self.a ^ self._rd(self._abx()))
        elif op == 0x59: self.a = self._setzn(self.a ^ self._rd(self._aby()))
        elif op == 0x41: self.a = self._setzn(self.a ^ self._rd(self._indx()))
        elif op == 0x51: self.a = self._setzn(self.a ^ self._rd(self._indy()))

        # arithmetic
        elif op == 0x69: self._adc(self._rd(self._imm()))
        elif op == 0x65: self._adc(self._rd(self._zp()))
        elif op == 0x75: self._adc(self._rd(self._zpx()))
        elif op == 0x6D: self._adc(self._rd(self._abs()))
        elif op == 0x7D: self._adc(self._rd(self._abx()))
        elif op == 0x79: self._adc(self._rd(self._aby()))
        elif op == 0x61: self._adc(self._rd(self._indx()))
        elif op == 0x71: self._adc(self._rd(self._indy()))
        elif op == 0xE9 or op == 0xEB: self._sbc(self._rd(self._imm()))
        elif op == 0xE5: self._sbc(self._rd(self._zp()))
        elif op == 0xF5: self._sbc(self._rd(self._zpx()))
        elif op == 0xED: self._sbc(self._rd(self._abs()))
        elif op == 0xFD: self._sbc(self._rd(self._abx()))
        elif op == 0xF9: self._sbc(self._rd(self._aby()))
        elif op == 0xE1: self._sbc(self._rd(self._indx()))
        elif op == 0xF1: self._sbc(self._rd(self._indy()))

        # compares
        elif op == 0xC9: self._cmp(self.a, self._rd(self._imm()))
        elif op == 0xC5: self._cmp(self.a, self._rd(self._zp()))
        elif op == 0xD5: self._cmp(self.a, self._rd(self._zpx()))
        elif op == 0xCD: self._cmp(self.a, self._rd(self._abs()))
        elif op == 0xDD: self._cmp(self.a, self._rd(self._abx()))
        elif op == 0xD9: self._cmp(self.a, self._rd(self._aby()))
        elif op == 0xC1: self._cmp(self.a, self._rd(self._indx()))
        elif op == 0xD1: self._cmp(self.a, self._rd(self._indy()))
        elif op == 0xE0: self._cmp(self.x, self._rd(self._imm()))
        elif op == 0xE4: self._cmp(self.x, self._rd(self._zp()))
        elif op == 0xEC: self._cmp(self.x, self._rd(self._abs()))
        elif op == 0xC0: self._cmp(self.y, self._rd(self._imm()))
        elif op == 0xC4: self._cmp(self.y, self._rd(self._zp()))
        elif op == 0xCC: self._cmp(self.y, self._rd(self._abs()))

        # increments
        elif op == 0xE6: a = self._zp();  self._wr(a, self._setzn(self._rd(a) + 1))
        elif op == 0xF6: a = self._zpx(); self._wr(a, self._setzn(self._rd(a) + 1))
        elif op == 0xEE: a = self._abs(); self._wr(a, self._setzn(self._rd(a) + 1))
        elif op == 0xFE: a = self._abx(); self._wr(a, self._setzn(self._rd(a) + 1))
        elif op == 0xC6: a = self._zp();  self._wr(a, self._setzn(self._rd(a) - 1))
        elif op == 0xD6: a = self._zpx(); self._wr(a, self._setzn(self._rd(a) - 1))
        elif op == 0xCE: a = self._abs(); self._wr(a, self._setzn(self._rd(a) - 1))
        elif op == 0xDE: a = self._abx(); self._wr(a, self._setzn(self._rd(a) - 1))
        elif op == 0xE8: self.x = self._setzn(self.x + 1)
        elif op == 0xCA: self.x = self._setzn(self.x - 1)
        elif op == 0xC8: self.y = self._setzn(self.y + 1)
        elif op == 0x88: self.y = self._setzn(self.y - 1)

        # shifts
        elif op == 0x0A: self.a = self._asl(self.a)
        elif op == 0x06: a = self._zp();  self._wr(a, self._asl(self._rd(a)))
        elif op == 0x16: a = self._zpx(); self._wr(a, self._asl(self._rd(a)))
        elif op == 0x0E: a = self._abs(); self._wr(a, self._asl(self._rd(a)))
        elif op == 0x1E: a = self._abx(); self._wr(a, self._asl(self._rd(a)))
        elif op == 0x4A: self.a = self._lsr(self.a)
        elif op == 0x46: a = self._zp();  self._wr(a, self._lsr(self._rd(a)))
        elif op == 0x56: a = self._zpx(); self._wr(a, self._lsr(self._rd(a)))
        elif op == 0x4E: a = self._abs(); self._wr(a, self._lsr(self._rd(a)))
        elif op == 0x5E: a = self._abx(); self._wr(a, self._lsr(self._rd(a)))
        elif op == 0x2A: self.a = self._rol(self.a)
        elif op == 0x26: a = self._zp();  self._wr(a, self._rol(self._rd(a)))
        elif op == 0x36: a = self._zpx(); self._wr(a, self._rol(self._rd(a)))
        elif op == 0x2E: a = self._abs(); self._wr(a, self._rol(self._rd(a)))
        elif op == 0x3E: a = self._abx(); self._wr(a, self._rol(self._rd(a)))
        elif op == 0x6A: self.a = self._ror(self.a)
        elif op == 0x66: a = self._zp();  self._wr(a, self._ror(self._rd(a)))
        elif op == 0x76: a = self._zpx(); self._wr(a, self._ror(self._rd(a)))
        elif op == 0x6E: a = self._abs(); self._wr(a, self._ror(self._rd(a)))
        elif op == 0x7E: a = self._abx(); self._wr(a, self._ror(self._rd(a)))

        # jumps and calls
        elif op == 0x4C: self.pc = self._abs()
        elif op == 0x6C: self.pc = self._ind()
        elif op == 0x20:
            t = self._abs()
            r = (self.pc - 1) & 0xFFFF
            self._push(r >> 8)
            self._push(r & 0xFF)
            self.pc = t
        elif op == 0x60:
            lo = self._pop()
            self.pc = ((self._pop() << 8) | lo) + 1 & 0xFFFF
        elif op == 0x40:
            self.p = (self._pop() | U) & ~B
            lo = self._pop()
            self.pc = ((self._pop() << 8) | lo) & 0xFFFF

        # branches
        elif op == 0x10: self._branch(not self.p & N)
        elif op == 0x30: self._branch(self.p & N)
        elif op == 0x50: self._branch(not self.p & V)
        elif op == 0x70: self._branch(self.p & V)
        elif op == 0x90: self._branch(not self.p & C)
        elif op == 0xB0: self._branch(self.p & C)
        elif op == 0xD0: self._branch(not self.p & Z)
        elif op == 0xF0: self._branch(self.p & Z)

        # flags
        elif op == 0x18: self.p &= ~C
        elif op == 0x38: self.p |= C
        elif op == 0x58: self.p &= ~I
        elif op == 0x78: self.p |= I
        elif op == 0xB8: self.p &= ~V
        elif op == 0xD8: self.p &= ~D
        elif op == 0xF8: self.p |= D

        # bit test
        elif op == 0x24 or op == 0x2C:
            v = self._rd(self._zp() if op == 0x24 else self._abs())
            self.p = (self.p & ~(Z | N | V)) | (Z if not (self.a & v) else 0) \
                | (v & (N | V))

        elif op == 0xEA: pass
        elif op == 0x00:
            # BRK. A tune that reaches this has lost its way; stopping is the
            # honest outcome, and jamming lets the caller see it happened.
            self.jammed = True

        # ── undocumented, because music code uses them ──────────────
        elif op == 0xA7: self.a = self.x = self._setzn(self._rd(self._zp()))
        elif op == 0xB7: self.a = self.x = self._setzn(self._rd(self._zpy()))
        elif op == 0xAF: self.a = self.x = self._setzn(self._rd(self._abs()))
        elif op == 0xBF: self.a = self.x = self._setzn(self._rd(self._aby()))
        elif op == 0xA3: self.a = self.x = self._setzn(self._rd(self._indx()))
        elif op == 0xB3: self.a = self.x = self._setzn(self._rd(self._indy()))
        elif op == 0x87: self._wr(self._zp(), self.a & self.x)
        elif op == 0x97: self._wr(self._zpy(), self.a & self.x)
        elif op == 0x8F: self._wr(self._abs(), self.a & self.x)
        elif op == 0x83: self._wr(self._indx(), self.a & self.x)
        elif op == 0x4B:
            self.a = self._setzn(self.a & self._rd(self._imm()))
            self.a = self._lsr(self.a)
        elif op == 0x0B or op == 0x2B:
            self.a = self._setzn(self.a & self._rd(self._imm()))
            self.p = (self.p & ~C) | (1 if self.a & 0x80 else 0)
        elif op in (0x1A, 0x3A, 0x5A, 0x7A, 0xDA, 0xFA):
            pass                                    # undocumented NOPs
        elif op in (0x80, 0x82, 0x89, 0xC2, 0xE2):
            self._imm()                             # two-byte NOPs
        elif op in (0x04, 0x44, 0x64):
            self._zp()
        elif op in (0x14, 0x34, 0x54, 0x74, 0xD4, 0xF4):
            self._zpx()
        elif op in (0x0C,):
            self._abs()
        elif op in (0x1C, 0x3C, 0x5C, 0x7C, 0xDC, 0xFC):
            self._abx()
        elif op in (0x02, 0x12, 0x22, 0x32, 0x42, 0x52,
                    0x62, 0x72, 0x92, 0xB2, 0xD2, 0xF2):
            # the genuine JAM opcodes: the real chip halts, and so do we
            self.jammed = True
        else:
            # A read-modify-write undocumented opcode we have not special-cased.
            # Treating it as a NOP keeps the tune running; treating it as an
            # error would stop on a file a real C64 plays.
            pass
