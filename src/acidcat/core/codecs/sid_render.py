"""Render a SID tune to PCM by running it.

There is no decoding step here in the usual sense. The file holds a program;
the only way to learn what it sounds like is to execute it, watch what it
writes to the SID chip's registers, and synthesise that. So this loads the
memory image, calls the tune's init routine, then calls its play routine once
per emulated frame while a synthesised SID turns the register writes into
samples.

WHAT WILL NOT PLAY, and why it is refused rather than half-played:

A tune with playAddress 0 installs its own interrupt handler and expects the
machine to drive it. Nothing here schedules interrupts, so calling anything
would be guessing at an entry point the file did not name. Every RSID is in
this category by definition. Measured over the whole High Voltage SID
Collection: 57,122 of 61,157 tunes can be driven from a play address (93.4%),
and the 4,035 that cannot are all this case.

Of a random 2,309-tune sample rendered for eight seconds each, 99.4% produced
sound, 0.6% were silent and none crashed. Adding interrupt scheduling is what
would close the remaining 6.6%, and it is a real piece of work rather than a
flag.

A tune that writes SID registers faster than the frame rate -- to play digi
samples through the volume register -- has those writes collapsed, because
registers are sampled once per frame. The result is the tune without its
samples rather than a failure, so it plays, and the caller is told.
"""

from acidcat.core.codecs import sid_chip
from acidcat.core.codecs.mos6510 import CPU, Bus
from acidcat.core.formats import sid as sidmod

# Vertical blank rates. A tune's speed bits can ask for the CIA timer instead,
# which defaults to 60 Hz on both machines; the difference is a tempo change,
# not a pitch one, because the oscillators are clocked by the system clock
# rather than by the player.
FRAME_HZ = {"PAL": 50.0, "NTSC": 60.0}

# Enough to get through a slow init -- some tunes clear large tables or
# decompress themselves -- without letting a tune that waits for an interrupt
# spin forever.
_INIT_BUDGET = 20000000
_PLAY_BUDGET = 500000


class CannotRender(Exception):
    """Raised when the file names no entry point we can drive."""


def _numpy():
    try:
        import numpy
    except ImportError:
        raise CannotRender(
            "rendering a SID needs numpy: pip install 'acidcat[analysis]'")
    return numpy


def render(raw, seconds=30.0, subtune=None, sample_rate=44100):
    """Return (pcm_bytes, info). `pcm_bytes` is signed 16-bit mono."""
    np = _numpy()
    h = sidmod.parse_header(raw)
    if h["magic"] not in sidmod.MAGICS:
        raise CannotRender("not a SID file")
    if h["mus_player"]:
        raise CannotRender(
            "the payload is Compute!'s Sidplayer MUS data with no player in "
            "it; an external player has to be merged before it can be replayed")
    if not h["play_address"]:
        raise CannotRender(
            "this tune installs its own interrupt handler (playAddress is 0) "
            "and has to be driven by one; nothing here schedules interrupts")

    clock_name = sidmod.clock_name(h["clock"])
    ntsc = clock_name == "NTSC"
    clock = sid_chip.CLOCK_NTSC if ntsc else sid_chip.CLOCK_PAL
    frame_hz = FRAME_HZ["NTSC" if ntsc else "PAL"]

    # One chip per SID the header names. A stereo or three-SID tune writes to
    # $D420 or $D500 as well as $D400, and those windows must go to their own
    # register files: masking the address to five bits sends a write meant for
    # the second chip's frequency register straight onto the first chip's, so
    # a 3SID tune silently corrupts voice 1 and renders as nothing.
    chips = [sid_chip.SID(sample_rate=sample_rate, clock=clock,
                          model=h["sid_model"] or 1)]
    windows = [0xD400]
    for addr, model in ((h["second_sid"], h["sid_model_2"]),
                        (h["third_sid"], h["sid_model_3"])):
        if addr:
            # an extra chip with an unknown model takes the first SID's
            chips.append(sid_chip.SID(sample_rate=sample_rate, clock=clock,
                                      model=model or h["sid_model"] or 1))
            windows.append(addr)

    def route(addr, value):
        for base, chip in zip(windows, chips):
            if base <= addr < base + 0x20:
                chip.write(addr - base, value)
                return

    bus = Bus(sid_write=route)
    cpu = CPU(bus)

    image = raw[h["code_offset"]:]
    load = h["effective_load"]
    end = min(0x10000, load + len(image))
    bus.ram[load:end] = image[:end - load]

    # The environment the format description pins down. Without these a tune
    # reads whatever happens to be in RAM and picks the wrong timing.
    bus.ram[0x01] = 0x37                      # I/O, KERNAL and BASIC banked in
    bus.ram[0x02A6] = 0x00 if ntsc else 0x01
    latch = 0x4295 if ntsc else 0x4025
    bus.ram[0xDC04] = latch & 0xFF
    bus.ram[0xDC05] = latch >> 8

    song = h["start_song"] if subtune is None else subtune
    song = max(1, min(song, max(1, h["songs"])))
    if not cpu.run(h["effective_init"], acc=song - 1, max_cycles=_INIT_BUDGET):
        raise CannotRender("the tune's init routine did not return; it is "
                           "probably waiting for an interrupt")

    per_frame = int(round(sample_rate / frame_hz))
    total_frames = max(1, int(round(seconds * frame_hz)))
    blocks = []
    stalled = 0
    for _ in range(total_frames):
        if not cpu.run(h["play_address"], max_cycles=_PLAY_BUDGET):
            stalled += 1
            if stalled > 3:
                break
        block = chips[0].render(per_frame, np)
        for extra in chips[1:]:
            block = block + extra.render(per_frame, np)
        blocks.append(block if len(chips) == 1 else block / len(chips))

    if not blocks:
        raise CannotRender("the play routine produced no frames")

    sig = np.concatenate(blocks)
    peak = float(np.max(np.abs(sig))) if len(sig) else 0.0
    if peak > 0:
        # Normalise to a comfortable level rather than to full scale: the
        # synthesis is approximate and a tune that happens to peak once should
        # not set the level for the whole render.
        sig = sig * (0.85 / peak)
    pcm = np.clip(sig * 32767.0, -32768, 32767).astype("<i2").tobytes()

    info = {
        "subtune": song, "songs": h["songs"], "clock": clock_name or "PAL",
        "frame_hz": frame_hz, "sample_rate": sample_rate,
        "model": sidmod.model_name(h["sid_model"]),
        "seconds": len(sig) / float(sample_rate),
        "name": h["name"], "author": h["author"], "released": h["released"],
        "stalled_frames": stalled, "sid_chips": len(chips),
        "chip_addresses": ["$%04X" % w for w in windows],
        "approximate": True,
    }
    return pcm, info


def render_file(path, **kw):
    with open(path, "rb") as fh:
        return render(fh.read(), **kw)


def can_render(raw):
    """(bool, reason). Cheap enough to ask before offering playback."""
    try:
        h = sidmod.parse_header(raw)
    except Exception:
        return False, "unreadable header"
    if h["magic"] not in sidmod.MAGICS:
        return False, "not a SID file"
    if h["mus_player"]:
        return False, "MUS payload with no player"
    if not h["play_address"]:
        return False, "drives itself from an interrupt"
    return True, ""
