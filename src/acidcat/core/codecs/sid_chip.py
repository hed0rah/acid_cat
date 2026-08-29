"""A MOS 6581/8580 synthesiser, good enough to hear a tune.

WHAT THIS IS. Three oscillators with the SID's four waveforms, three envelope
generators with its ADSR rate tables, and a state-variable filter standing in
for its analog one. Register writes go in, PCM comes out.

WHAT THIS IS NOT. reSID. The SID's character is mostly in the parts that are
hardest to model: an analog filter whose cutoff curve differs between chip
revisions and between individual chips, combined waveforms that are the result
of bits fighting on a bus rather than any arithmetic, and ADSR behaviour with
documented bugs that composers deliberately exploited. What comes out of here
is recognisably the tune, correctly pitched and correctly shaped. It is not
what the chip sounds like, and anything that matters -- comparing chip
revisions, judging a filter sweep -- wants real hardware or reSID.

The approximations are named at each site rather than left for a listener to
discover.
"""

CLOCK_PAL = 985248
CLOCK_NTSC = 1022727

# Attack times in milliseconds for the 16 rate values, from the datasheet:
# how long the envelope takes to climb from 0 to peak.
_ATTACK_MS = (2, 8, 16, 24, 38, 56, 68, 80, 100, 250, 500, 800,
              1000, 3000, 5000, 8000)
# Decay and release share one table, and each value is three times the
# corresponding attack -- the envelope falls more slowly than it rises.
_DECAY_MS = tuple(v * 3 for v in _ATTACK_MS)

# Control register bits
GATE, SYNC, RING, TEST = 0x01, 0x02, 0x04, 0x08
TRIANGLE, SAWTOOTH, PULSE, NOISE = 0x10, 0x20, 0x40, 0x80

_ATTACK, _DECAY, _SUSTAIN, _RELEASE = 0, 1, 2, 3


class Voice(object):
    __slots__ = ("freq", "pw", "ctrl", "ad", "sr", "phase", "env", "stage",
                 "lfsr", "_last_gate")

    def __init__(self):
        self.freq = 0
        self.pw = 0x800
        self.ctrl = 0
        self.ad = 0
        self.sr = 0
        self.phase = 0.0
        self.env = 0.0
        self.stage = _RELEASE
        self.lfsr = 0x7FFFF8
        self._last_gate = False

    def gate_changed(self):
        """Update the envelope stage from the gate bit.

        The gate is edge-triggered: setting it starts attack from wherever the
        envelope currently is (not from zero -- retriggering a still-sounding
        voice is how a hard restart works), clearing it goes to release.
        """
        gate = bool(self.ctrl & GATE)
        if gate and not self._last_gate:
            self.stage = _ATTACK
        elif not gate and self._last_gate:
            self.stage = _RELEASE
        self._last_gate = gate


class SID(object):
    """The register file and the synthesis.

    Rendering is block-based: the CPU runs a frame's worth of player code,
    writing registers, then a frame's worth of audio is generated with those
    registers held constant. That is exactly how a tune with a play routine
    behaves -- the registers only change when the player changes them -- and it
    is why this can be fast without being wrong for the common case.

    It IS wrong for a tune that writes registers faster than the frame rate to
    play digi samples, since those writes land between our snapshots. Those
    tunes are the ones the RSID flag exists to mark.
    """

    def __init__(self, sample_rate=44100, clock=CLOCK_PAL, model=1):
        self.sample_rate = sample_rate
        self.clock = clock
        self.model = model
        self.voices = [Voice(), Voice(), Voice()]
        self.fc = 0
        self.res = 0
        self.filt_mask = 0
        self.mode_vol = 0x0F
        self.regs = bytearray(0x20)
        self._lp = 0.0
        self._bp = 0.0
        self._dc_x = 0.0
        self._dc_y = 0.0

    # ── the register interface ──────────────────────────────────────

    def write(self, addr, value):
        r = addr & 0x1F
        self.regs[r] = value & 0xFF
        if r < 0x15:
            v = self.voices[r // 7]
            o = r % 7
            if o == 0:
                v.freq = (v.freq & 0xFF00) | value
            elif o == 1:
                v.freq = (v.freq & 0x00FF) | (value << 8)
            elif o == 2:
                v.pw = (v.pw & 0x0F00) | value
            elif o == 3:
                v.pw = (v.pw & 0x00FF) | ((value & 0x0F) << 8)
            elif o == 4:
                v.ctrl = value
                v.gate_changed()
            elif o == 5:
                v.ad = value
            elif o == 6:
                v.sr = value
        elif r == 0x15:
            self.fc = (self.fc & 0x7F8) | (value & 7)
        elif r == 0x16:
            self.fc = (self.fc & 0x007) | (value << 3)
        elif r == 0x17:
            self.res = value >> 4
            self.filt_mask = value & 0x0F
        elif r == 0x18:
            self.mode_vol = value

    # ── synthesis ───────────────────────────────────────────────────

    def _cutoff_hz(self):
        """Filter cutoff from the 11-bit FC register.

        The two chip revisions have genuinely different curves and neither is
        linear; the 6581's is famously variable between individual chips. These
        are straight-line stand-ins across each chip's documented range, which
        puts a sweep in the right place without claiming to be either curve.
        """
        f = self.fc / 2047.0
        if self.model == 2:                       # 8580: wider and flatter
            return 30.0 + f * (12500.0 - 30.0)
        return 220.0 + f * (12000.0 - 220.0)      # 6581 sits higher at the bottom

    def render(self, frames, np):
        """`frames` samples of int16 mono, with the registers held constant."""
        out = np.zeros(frames, dtype=np.float64)
        sr = float(self.sample_rate)
        for idx, v in enumerate(self.voices):
            if not (v.ctrl & 0xF0):
                # No waveform selected, so the voice is silent -- but its
                # envelope must still advance, or a voice gated on with no
                # waveform comes back at the level it left rather than where
                # the envelope would actually have been.
                self._envelope(v, frames, np)
                continue
            wave = self._wave(v, frames, sr, np, idx)
            env = self._envelope(v, frames, np)
            out += wave * env
        return self._filter(out, np)

    def _phase(self, v, frames, sr, np):
        """This voice's phase across the block, 0.0 to 1.0, advancing state."""
        hz = v.freq * self.clock / 16777216.0
        step = hz / sr
        ph = (v.phase + step * np.arange(frames)) % 1.0
        v.phase = float((v.phase + step * frames) % 1.0)
        return ph, hz

    def _wave(self, v, frames, sr, np, idx=None):
        """One voice's waveform, as -1.0 to 1.0.

        Combined waveforms are a bitwise AND, which is what the chip does. Bob
        Yannes, who designed it (interview with Andreas Varga, August 1996):

            "The multiplexers were single transistors and did not provide a
            'lock-out', allowing combinations of the waveforms to be selected.
            The combination was actually a logical ANDing of the bits of each
            waveform, which produced unpredictable results, so I didn't
            encourage this ..."

        So each generator is evaluated as its real 12-bit value and the
        selected ones are ANDed. That is why a combined waveform is quieter and
        more lopsided than either component -- an AND can only clear bits --
        and why the result is not any kind of mix.

        Modelling it as an average is audibly wrong for triangle+sawtooth: the
        two agree only 0.77 by correlation, and the average is symmetric where
        the real thing is not. A pulse combined with anything is the one case
        an average could be rescued into, because a pulse is all ones or all
        zeros and ANDing with it is a gate; that special case now falls out of
        the general rule rather than being written separately.

        The oscillator is a 24-bit accumulator clocked at the system clock, so
        its output frequency is freq * clock / 2^24. The accumulator is
        reconstructed from a float phase advanced at the OUTPUT rate: same
        frequency, same waveform, without stepping a million times a second to
        then throw most of it away.
        """
        if v.ctrl & TEST:
            # the test bit holds the accumulator at zero and silences the voice
            v.phase = 0.0
            return np.zeros(frames)
        phase, hz = self._phase(v, frames, sr, np)
        acc = (phase * 16777216.0).astype(np.uint32)

        ctrl = v.ctrl
        out = None

        def merge(value):
            return value if out is None else (out & value)

        if ctrl & SAWTOOTH:
            out = merge((acc >> 12) & 0xFFF)
        if ctrl & TRIANGLE:
            # "Ring Modulation was accomplished by substituting the accumulator
            # MSB of an oscillator in the EXOR function of the triangle
            # waveform generator with the accumulator MSB of the previous
            # oscillator." -- Yannes, same interview. So ring modulation does
            # not scale the triangle, it swaps which oscillator decides the
            # fold. That is also why ring mod is audible only on a triangle:
            # no other generator has that EXOR to substitute into.
            msb = (acc >> 23) & 1
            if ctrl & RING and idx is not None:
                prev = self.voices[(idx - 1) % 3]
                prev_hz = prev.freq * self.clock / 16777216.0
                prev_ph = (prev.phase + (prev_hz / sr) * np.arange(frames)) % 1.0
                msb = ((prev_ph * 16777216.0).astype(np.uint32) >> 23) & 1
            folded = (acc >> 11) & 0xFFF
            out = merge(np.where(msb.astype(bool), (~folded) & 0xFFF, folded))
        if ctrl & PULSE:
            # High while the accumulator is BELOW the pulse width, so the
            # register reads as a duty cycle the way the datasheet describes
            # it: 0 is a 0% pulse, 0x800 a square wave, 0xFFF nearly 100%.
            # Inverting this comparison still gives a square wave at 0x800,
            # so the 50% case cannot catch the mistake -- only pw=0 can.
            out = merge(np.where(((acc >> 12) & 0xFFF) < (v.pw & 0xFFF),
                                 0xFFF, 0x000).astype(np.uint32))
        if ctrl & NOISE:
            out = merge(self._noise12(v, frames, hz, sr, np))

        if out is None:
            return np.zeros(frames)
        # The AND is deliberately NOT fed back into the LFSR. On hardware a
        # noise-plus-anything combination can fill the shift register with
        # zeroes and stop it, which Yannes names as the reason he discouraged
        # combining at all. Reproducing that would silence voices in real
        # tunes, and with no reference player available here there is no way to
        # tell a faithful silence from a bug -- so the lock is documented and
        # not implemented.
        return (out.astype(np.float64) - 0x800) / 2048.0

    def _noise12(self, v, frames, hz, sr, np):
        """The LFSR as a 12-bit value, for ANDing with the other generators."""
        f = self._noise(v, frames, hz, sr, np)
        return (((f + 1.0) * 2047.0).astype(np.uint32)) & 0xFFF

    def _noise(self, v, frames, hz, sr, np):
        """The 23-bit LFSR, sampled at the rate the oscillator would clock it.

        Generated one step at a time because each output depends on the last;
        the rate is low enough (the LFSR advances on bit 19 of the accumulator)
        that this is a few hundred steps per frame, not thousands.
        """
        rate = max(1e-6, hz / 16.0)
        step = rate / sr
        out = np.empty(frames)
        pos = 0.0
        val = ((v.lfsr >> 22) & 1) * 128 + ((v.lfsr >> 20) & 1) * 64
        for i in range(frames):
            pos += step
            while pos >= 1.0:
                pos -= 1.0
                bit = ((v.lfsr >> 22) ^ (v.lfsr >> 17)) & 1
                v.lfsr = ((v.lfsr << 1) | bit) & 0x7FFFFF
                val = ((v.lfsr >> 22) & 1) * 128 + ((v.lfsr >> 20) & 1) * 64 \
                    + ((v.lfsr >> 16) & 1) * 32 + ((v.lfsr >> 13) & 1) * 16
            out[i] = val / 128.0 - 1.0
        return out

    def _rate_per_sample(self, ms):
        """Envelope steps per output sample for a full 0-to-peak sweep."""
        return 1.0 / max(1e-6, (ms / 1000.0) * self.sample_rate)

    def _envelope(self, v, frames, np):
        env = np.empty(frames)
        level = v.env
        stage = v.stage
        atk = self._rate_per_sample(_ATTACK_MS[v.ad >> 4])
        dec = self._rate_per_sample(_DECAY_MS[v.ad & 0x0F])
        rel = self._rate_per_sample(_DECAY_MS[v.sr & 0x0F])
        sustain = (v.sr >> 4) / 15.0
        for i in range(frames):
            if stage == _ATTACK:
                level += atk
                if level >= 1.0:
                    level = 1.0
                    stage = _DECAY
            elif stage == _DECAY:
                if level > sustain:
                    # The real decay is a piecewise-exponential ramp driven by a
                    # counter, not a straight line. Scaling the step by the
                    # distance left gives the same shape family: fast at first,
                    # slow as it settles.
                    level -= dec * max(0.02, level - sustain)
                    if level <= sustain:
                        level = sustain
                        stage = _SUSTAIN
                else:
                    stage = _SUSTAIN
            elif stage == _SUSTAIN:
                level = sustain
            else:
                if level > 0.0:
                    level -= rel * max(0.02, level)
                    if level < 0.0:
                        level = 0.0
            env[i] = level
        v.env = float(level)
        v.stage = stage
        return env

    def _dc_block(self, sig, np):
        """One-pole high pass at roughly 20 Hz.

        Not a correction for the synthesis: the C64's audio output is
        AC-coupled through a capacitor, so no steady offset reaches a speaker
        on real hardware either. Several waveform combinations sit at a
        non-zero mean, and without this they eat headroom that the music
        should have had.
        """
        a = 0.9985
        out = np.empty(len(sig))
        prev_x, prev_y = self._dc_x, self._dc_y
        for i in range(len(sig)):
            x = sig[i]
            prev_y = a * (prev_y + x - prev_x)
            prev_x = x
            out[i] = prev_y
        self._dc_x, self._dc_y = prev_x, prev_y
        return out

    def _filter(self, sig, np):
        """A state-variable filter standing in for the analog one.

        Only voices selected in the filter mask go through it; the rest bypass,
        which is what the FILT register controls. Voice 3 can also be cut
        entirely by bit 7 of the mode register, a trick used to hide a voice
        being run as a modulation source.
        """
        vol = (self.mode_vol & 0x0F) / 15.0
        mode = self.mode_vol >> 4
        if not self.filt_mask or not (mode & 0x07):
            return self._dc_block(sig * vol * 0.25, np)

        import math
        fc = self._cutoff_hz()
        # A topology-preserving-transform state variable filter, not the
        # classic Chamberlin one. The Chamberlin form is only stable while
        # 2*sin(pi*fc/sr) stays below about 1, and the 8580's cutoff range
        # reaches past that at 44.1 kHz -- the filter then diverges to inf and
        # then to NaN, which reached the int16 cast as a silent, corrupt file.
        # This form is stable at every cutoff, which is the whole reason to
        # prefer it here.
        g = math.tan(math.pi * min(fc, self.sample_rate * 0.49) / self.sample_rate)
        k = 1.0 / max(0.5, 0.707 + self.res / 6.0)
        a1 = 1.0 / (1.0 + g * (g + k))
        a2 = g * a1
        a3 = g * a2

        ic1, ic2 = self._lp, self._bp
        out = np.empty(len(sig))
        want_lp, want_bp, want_hp = mode & 1, mode & 2, mode & 4
        for i in range(len(sig)):
            x = sig[i]
            v3 = x - ic2
            v1 = a1 * ic1 + a2 * v3
            v2 = ic2 + a2 * ic1 + a3 * v3
            ic1 = 2.0 * v1 - ic1
            ic2 = 2.0 * v2 - ic2
            y = 0.0
            if want_lp:
                y += v2
            if want_bp:
                y += v1
            if want_hp:
                y += x - k * v1 - v2
            out[i] = y
        lp, bp = ic1, ic2
        self._lp, self._bp = lp, bp
        return self._dc_block(out * vol * 0.25, np)
