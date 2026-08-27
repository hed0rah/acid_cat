"""Tests for the SID player: the 6510 core, the chip, and the render loop.

A tune is a program, so "does it play" cannot be asserted the way a decoder can
be. What can be asserted is that the CPU computes what a 6502 computes, that
the chip turns register writes into the frequencies and shapes they name, and
that the render loop refuses what it genuinely cannot drive instead of
producing silence and calling it success.

The synthetic tune below is hand-assembled 6510: it sets the volume, programs
voice 1 to a sawtooth at a known frequency, gates it on, and returns. That is
enough for an end-to-end assertion with a note whose pitch is known in advance.
"""
import struct

import pytest

from acidcat.core.codecs import sid_chip
from acidcat.core.codecs.mos6510 import CPU, Bus

np = pytest.importorskip("numpy", reason="rendering a SID needs numpy")

from acidcat.core.codecs import sid_render          # noqa: E402  (after numpy)


# ── a tune, hand-assembled ──────────────────────────────────────────

INIT = 0x1000
PLAY = 0x1020
VOICE1_FREQ = 0x151F           # 5407 -> 5407 * 985248 / 2**24 = 317.5 Hz


def _tune_code():
    """init: volume, frequency, envelope, gate on. play: nothing."""
    def lda_sta(value, addr):
        return bytes([0xA9, value, 0x8D, addr & 0xFF, addr >> 8])

    init = (lda_sta(0x0F, 0xD418)               # volume 15, filter off
            + lda_sta(VOICE1_FREQ & 0xFF, 0xD400)
            + lda_sta(VOICE1_FREQ >> 8, 0xD401)
            + lda_sta(0x00, 0xD405)             # attack 2 ms, decay 6 ms
            + lda_sta(0xF0, 0xD406)             # sustain 15, release fast
            + lda_sta(0x21, 0xD404)             # sawtooth + gate
            + bytes([0x60]))                    # RTS
    body = bytearray(init.ljust(PLAY - INIT, b"\xEA"))
    body += bytes([0x60])                       # play: RTS
    return bytes(body)


def _tune(**over):
    from test_sid import _sid
    kw = dict(load=0, init=INIT, play=PLAY, songs=1,
              data=struct.pack("<H", INIT) + _tune_code())
    kw.update(over)
    return _sid(**kw)


# ── the 6510 ────────────────────────────────────────────────────────

def _cpu(code, at=0x1000):
    bus = Bus()
    bus.ram[at:at + len(code)] = code
    cpu = CPU(bus)
    cpu.pc = at
    return cpu, bus


def test_load_store_and_flags():
    cpu, bus = _cpu(bytes([0xA9, 0x00,        # LDA #$00
                           0x85, 0x10,        # STA $10
                           0xA9, 0x80]))      # LDA #$80
    cpu.step()
    assert cpu.a == 0 and cpu.p & 0x02, "Z must be set for a zero load"
    cpu.step()
    assert bus.ram[0x10] == 0
    cpu.step()
    assert cpu.a == 0x80 and cpu.p & 0x80, "N must follow bit 7"


def test_adc_sets_carry_and_overflow():
    cpu, _ = _cpu(bytes([0xA9, 0x7F, 0x69, 0x01]))     # LDA #$7F : ADC #$01
    cpu.step(); cpu.step()
    assert cpu.a == 0x80
    assert cpu.p & 0x40, "signed overflow: 127 + 1 must set V"
    assert not cpu.p & 0x01, "no unsigned carry out of 0x80"

    cpu, _ = _cpu(bytes([0xA9, 0xFF, 0x69, 0x02]))
    cpu.step(); cpu.step()
    assert cpu.a == 0x01 and cpu.p & 0x01, "0xFF + 2 must carry"


def test_sbc_borrows_through_carry():
    # SEC then SBC #$01 is a plain subtract; without SEC it borrows one more
    cpu, _ = _cpu(bytes([0x38, 0xA9, 0x05, 0xE9, 0x01]))
    for _ in range(3):
        cpu.step()
    assert cpu.a == 0x04

    cpu, _ = _cpu(bytes([0x18, 0xA9, 0x05, 0xE9, 0x01]))
    for _ in range(3):
        cpu.step()
    assert cpu.a == 0x03, "carry clear means an extra borrow"


def test_decimal_mode_adc():
    """Music code uses BCD for score and time displays, and a player that
    shares a routine with one will run through it."""
    cpu, _ = _cpu(bytes([0xF8, 0x38, 0xA9, 0x09, 0x69, 0x01]))   # SED SEC LDA#9 ADC#1
    for _ in range(4):
        cpu.step()
    assert cpu.a == 0x11, "9 + 1 + carry in decimal is 0x11, not 0x0B"


def test_jmp_indirect_reproduces_the_page_wrap_bug():
    """JMP ($xxFF) reads the high byte from the START of the same page, not
    the next one. Real code has depended on it, so emulating it correctly
    means emulating the bug."""
    bus = Bus()
    bus.ram[0x10FF] = 0x34
    bus.ram[0x1000] = 0x12                 # the wrapped high byte
    bus.ram[0x1100] = 0xAB                 # what a naive read would take
    bus.ram[0x2000:0x2003] = bytes([0x6C, 0xFF, 0x10])
    cpu = CPU(bus)
    cpu.pc = 0x2000
    cpu.step()
    assert cpu.pc == 0x1234, "expected the page-wrapped target"


def test_jsr_and_rts_round_trip():
    bus = Bus()
    bus.ram[0x1000:0x1003] = bytes([0x20, 0x10, 0x20])    # JSR $2010
    bus.ram[0x2010] = 0x60                                # RTS
    cpu = CPU(bus)
    cpu.pc = 0x1000
    cpu.step()
    assert cpu.pc == 0x2010
    cpu.step()
    assert cpu.pc == 0x1003, "RTS must return past the JSR operand"


def test_run_returns_when_the_subroutine_unwinds():
    bus = Bus()
    bus.ram[0x1000] = 0x60                       # RTS immediately
    cpu = CPU(bus)
    assert cpu.run(0x1000) is True


def test_run_is_bounded_so_a_spin_cannot_hang():
    """A tune waiting for an interrupt that will never arrive loops forever.
    Returning False is the honest outcome; hanging is not."""
    bus = Bus()
    bus.ram[0x1000:0x1003] = bytes([0x4C, 0x00, 0x10])   # JMP $1000
    cpu = CPU(bus)
    assert cpu.run(0x1000, max_cycles=5000) is False


def test_a_jam_opcode_stops_rather_than_spinning():
    bus = Bus()
    bus.ram[0x1000] = 0x02                       # JAM
    cpu = CPU(bus)
    assert cpu.run(0x1000, max_cycles=5000) is True
    assert cpu.jammed is True


def test_undocumented_lax_loads_both_registers():
    cpu, bus = _cpu(bytes([0xA7, 0x20]))         # LAX $20
    bus.ram[0x20] = 0x42
    cpu.step()
    assert cpu.a == 0x42 and cpu.x == 0x42


# ── the bus ─────────────────────────────────────────────────────────

def test_sid_writes_are_reported_with_the_full_address():
    """Not a masked register number. Which chip a write belongs to is decided
    by the caller, who knows where the header put the second and third SID --
    masking here would make that impossible."""
    seen = []
    bus = Bus(sid_write=lambda a, v: seen.append((a, v)))
    bus.ram[1] = 0x37
    bus.write(0xD420, 0x99)
    assert seen == [(0xD420, 0x99)]


def test_the_raster_register_advances():
    """A player polling $D012 for a value that never changes spins forever."""
    bus = Bus()
    bus.ram[1] = 0x37
    first = bus.read(0xD012)
    assert bus.read(0xD012) != first


# ── the chip ────────────────────────────────────────────────────────

def test_register_writes_reach_the_right_voice():
    chip = sid_chip.SID()
    chip.write(0x07, 0x1F)          # voice 2 freq lo
    chip.write(0x08, 0x15)          # voice 2 freq hi
    assert chip.voices[1].freq == 0x151F
    assert chip.voices[0].freq == 0, "voice 1 must be untouched"


def test_the_gate_bit_drives_the_envelope_stage():
    chip = sid_chip.SID()
    v = chip.voices[0]
    assert v.stage == sid_chip._RELEASE
    chip.write(0x04, 0x21)                       # sawtooth + gate
    assert v.stage == sid_chip._ATTACK
    chip.write(0x04, 0x20)                       # gate off
    assert v.stage == sid_chip._RELEASE


def test_a_pulse_with_zero_width_is_silence_not_a_dc_offset():
    """The bug this test exists for.

    Combined waveforms on real hardware are bits pulling each other DOWN on a
    shared bus, closer to an AND than a sum. A voice running pulse+sawtooth
    with a pulse width of 0 has a permanently low pulse: on the chip that is
    silence. Summed as a -1 it is a large DC offset on the whole mix, which is
    what it was, and it swamped the music.
    """
    chip = sid_chip.SID()
    chip.write(0x00, 0x1F)
    chip.write(0x01, 0x15)
    chip.write(0x02, 0x00)                       # pulse width 0
    chip.write(0x03, 0x00)
    chip.write(0x06, 0xF0)                       # sustain full
    chip.write(0x04, 0x61)                       # pulse + sawtooth + gate
    chip.write(0x18, 0x0F)
    out = chip.render(4410, np)
    assert abs(float(np.mean(out))) < 0.05, (
        "a permanently low pulse must not become a DC offset: mean was %r"
        % float(np.mean(out)))


def test_a_pulse_alone_is_still_a_square_wave():
    """The control for the test above: gating must not silence a lone pulse."""
    chip = sid_chip.SID()
    chip.write(0x00, 0x1F)
    chip.write(0x01, 0x15)
    chip.write(0x02, 0x00)
    chip.write(0x03, 0x08)                       # 50% duty
    chip.write(0x06, 0xF0)
    chip.write(0x04, 0x41)                       # pulse + gate
    chip.write(0x18, 0x0F)
    out = chip.render(4410, np)
    assert float(np.max(np.abs(out))) > 0.01, "a lone pulse must make sound"


@pytest.mark.parametrize("fc_hi", [0x00, 0x40, 0x80, 0xC0, 0xFF])
@pytest.mark.parametrize("res", [0x00, 0x70, 0xF0])
def test_the_filter_is_stable_at_every_cutoff(fc_hi, res):
    """The other bug this test exists for.

    The classic Chamberlin state-variable filter is only stable while its
    coefficient stays below about 1, and the cutoff range at 44.1 kHz reaches
    past that. It then diverges to inf and to NaN, and NaN reached the int16
    cast -- so the failure arrived as a silent, corrupt file rather than an
    error.
    """
    chip = sid_chip.SID()
    chip.write(0x16, fc_hi)
    chip.write(0x17, res | 0x0F)                 # resonance + all voices filtered
    chip.write(0x18, 0x1F)                       # lowpass, volume 15
    chip.write(0x00, 0xFF)
    chip.write(0x01, 0x40)
    chip.write(0x06, 0xF0)
    chip.write(0x04, 0x21)
    for _ in range(6):
        out = chip.render(4410, np)
        assert np.all(np.isfinite(out)), "filter diverged at fc_hi=%#x res=%#x" % (fc_hi, res)
        assert float(np.max(np.abs(out))) < 100.0, "filter blew up"


# ── the render loop ─────────────────────────────────────────────────

def test_a_tune_renders_at_the_frequency_it_programmed():
    """End to end: the CPU runs real 6510, the writes reach the chip, and the
    note that comes out is the one the code asked for."""
    pcm, info = sid_render.render(_tune(), seconds=1.0)
    a = np.frombuffer(pcm, dtype="<i2").astype(float)
    assert len(a) > 40000
    assert float(np.sqrt((a * a).mean())) > 100, "the tune should be audible"

    expected = VOICE1_FREQ * sid_chip.CLOCK_PAL / 16777216.0
    seg = a[8192:8192 + 16384] * np.hanning(16384)
    spec = np.abs(np.fft.rfft(seg))
    freqs = np.fft.rfftfreq(16384, 1.0 / info["sample_rate"])
    peak = freqs[int(np.argmax(spec))]
    assert abs(peak - expected) < 8.0, (
        "expected the fundamental near %.1f Hz, found %.1f" % (expected, peak))


def test_a_tune_that_drives_itself_is_refused_not_faked():
    """playAddress 0 means the tune installs its own interrupt handler.
    Nothing here schedules interrupts, so calling anything would be guessing
    at an entry point the file never named."""
    raw = _tune(play=0)
    can, why = sid_render.can_render(raw)
    assert can is False and "interrupt" in why
    with pytest.raises(sid_render.CannotRender):
        sid_render.render(raw, seconds=0.2)


def test_mus_data_is_refused_because_there_is_no_player_in_it():
    raw = _tune(flags=0x0015)                    # musPlayer bit set
    can, why = sid_render.can_render(raw)
    assert can is False and "MUS" in why


def test_ntsc_uses_the_ntsc_clock_and_frame_rate():
    pal, ipal = sid_render.render(_tune(flags=0x0014), seconds=0.5)
    ntsc, intsc = sid_render.render(_tune(flags=0x0018), seconds=0.5)
    assert ipal["clock"] == "PAL" and ipal["frame_hz"] == 50.0
    assert intsc["clock"] == "NTSC" and intsc["frame_hz"] == 60.0


def test_extra_sid_chips_get_their_own_register_file():
    """The third bug this file pins.

    A write to $D420 masked to five bits is register 0x00 -- voice 1's
    frequency low byte on the FIRST chip. A stereo tune therefore corrupted
    voice 1 on every write meant for its second chip, and rendered as nothing.
    """
    raw = _tune(version=3, sid2=0x42)            # second SID at $D420
    _pcm, info = sid_render.render(raw, seconds=0.3)
    assert info["sid_chips"] == 2
    assert info["chip_addresses"] == ["$D400", "$D420"]

    single = sid_render.render(_tune(), seconds=0.3)[1]
    assert single["sid_chips"] == 1


def test_an_illegal_extra_sid_position_adds_no_chip():
    raw = _tune(version=3, sid2=0x43)            # odd, therefore not a position
    _pcm, info = sid_render.render(raw, seconds=0.3)
    assert info["sid_chips"] == 1


def test_the_subtune_number_reaches_the_init_routine():
    raw = _tune(songs=4, start=3)
    _pcm, info = sid_render.render(raw, seconds=0.2)
    assert info["subtune"] == 3
    _pcm, info = sid_render.render(raw, seconds=0.2, subtune=2)
    assert info["subtune"] == 2


def test_a_subtune_outside_the_range_is_clamped_not_trusted():
    raw = _tune(songs=2)
    assert sid_render.render(raw, seconds=0.2, subtune=99)[1]["subtune"] == 2
    assert sid_render.render(raw, seconds=0.2, subtune=0)[1]["subtune"] == 1


def test_the_render_declares_itself_approximate():
    """The filter is a stand-in and the combined waveforms are not what the
    bus does. Saying so in the result is the difference between a preview and
    a claim about how the chip sounds."""
    _pcm, info = sid_render.render(_tune(), seconds=0.2)
    assert info["approximate"] is True


def test_output_is_finite_and_in_range():
    pcm, _info = sid_render.render(_tune(), seconds=1.0)
    a = np.frombuffer(pcm, dtype="<i2")
    assert len(a) and int(np.max(np.abs(a.astype(np.int32)))) <= 32767
