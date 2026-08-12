"""A chart may rescale itself. It may not do so quietly.

Two complaints from actually using the TUI, both the same shape:

  The entropy plot is pinned to the ceiling on nearly every real file, because
  audio sits around 7.9 of a theoretical 8 and the axis is the full range.
  Everything worth seeing happens in the top two percent, where it cannot be.

  The byte histogram of a padded sampler bank is one spike at 0x00 and a flat
  line, because a bin 60x taller than the rest sets the axis for all of them.

The fix is a choice of vertical axis. The risk the fix carries is that a
rescaled chart is indistinguishable from an absolute one by eye -- an entropy
plot spanning 7.90 to 7.95 looks exactly like one spanning 0 to 8 -- so a chart
that rescales without saying so states a measurement it did not make. Every
test here is really about the caption.

Same for scope: these views used to be whole-file only, and a picture captioned
as the file that is really one chunk is the same lie in the horizontal.
"""

import asyncio
import struct

import pytest

from acidcat.core.forensics import viz
from acidcat.tui_theme import PALETTE, ramp_color

pytest.importorskip("textual")

from acidcat.tui_app.app import AcidcatTUI     # noqa: E402


def _run(scenario):
    asyncio.run(scenario())


# ── the axis primitive ────────────────────────────────────────────────

class TestScaleValues:
    def test_absolute_uses_the_axis_it_was_given(self):
        norm, lo, hi, _ = viz.scale_values([0, 4, 8], "absolute",
                                           floor=0.0, ceiling=8.0)
        assert norm == [0.0, 0.5, 1.0] and (lo, hi) == (0.0, 8.0)

    def test_auto_stretches_a_narrow_band_to_the_full_height(self):
        """The entropy complaint, in one assertion."""
        vals = [7.90, 7.92, 7.95]
        flat, *_ = viz.scale_values(vals, "absolute", floor=0.0, ceiling=8.0)
        assert max(flat) - min(flat) < 0.01, "precondition: absolute is flat here"
        norm, *_ = viz.scale_values(vals, "auto")
        assert norm == [0.0, pytest.approx(0.4), 1.0]

    def test_auto_names_the_window_it_chose(self):
        _n, _lo, _hi, label = viz.scale_values([7.90, 7.95], "auto")
        assert "7.9" in label, f"the caption must give the range away: {label!r}"

    def test_flat_data_is_reported_flat_not_full(self):
        """"everything is identical" and "everything is at maximum" are
        different findings and must not draw the same."""
        norm, _lo, _hi, label = viz.scale_values([5.0] * 4, "auto")
        assert norm == [0.0] * 4
        assert "flat" in label

    def test_log_lifts_the_rest_off_the_floor(self):
        """The histogram complaint: one bin 65x the others."""
        counts = [781] * 256
        counts[0] = 50786
        lin, *_ = viz.scale_values(counts, "absolute", floor=0.0)
        assert lin[1] < 0.02, "precondition: linear buries the other bins"
        log, *_ = viz.scale_values(counts, "log", floor=0.0)
        assert 0.5 < log[1] < 0.9 and log[0] == 1.0

    def test_clip_says_how_many_it_clipped(self):
        vals = [10] * 100 + [10000, 20000]
        _n, _lo, hi, label = viz.scale_values(vals, "clip", floor=0.0)
        assert hi < 10000
        assert "above" in label, f"silent clipping: {label!r}"

    def test_empty_input_is_not_a_crash(self):
        assert viz.scale_values([], "auto") == ([], 0.0, 0.0, "auto")

    def test_every_advertised_mode_is_implemented(self):
        """SCALES is what a caller iterates; a name in it that falls through to
        the absolute branch would be a mode that silently does nothing."""
        vals = [1, 5, 2, 900]
        seen = {}
        for m in viz.SCALES:
            norm, _lo, _hi, label = viz.scale_values(vals, m, floor=0.0)
            assert len(norm) == len(vals)
            assert all(0.0 <= v <= 1.0 for v in norm), m
            seen[m] = label
        assert len(set(seen.values())) == len(viz.SCALES), (
            f"two modes produced the same caption: {seen}")


class TestColumnPeaks:
    def test_it_samples_the_way_the_chart_does(self):
        """Colour is applied per cell from this; if it disagreed with
        braille_line the picture and its colours would describe two different
        datasets. A spike must land in the cell that is drawn tall."""
        vals = [0.0] * 64
        vals[32] = 1.0
        peaks = viz.column_peaks(vals, 16)
        assert len(peaks) == 16
        hot = [i for i, v in enumerate(peaks) if v > 0.5]
        assert len(hot) == 1, f"the spike should occupy exactly one cell: {hot}"
        rows = viz.braille_line(vals, width=16, height=4, vmin=0.0, vmax=1.0)
        assert rows[0][hot[0]] != "⠀", (
            "the cell coloured hot is not the cell drawn tall")

    def test_degenerate_widths_do_not_raise(self):
        assert viz.column_peaks([], 8) == []
        assert viz.column_peaks([1, 2], 0) == []


class TestNoBarCanVanish:
    """A chart with more data than columns must not drop the tall bar.

    Found by the test above. braille_line point-sampled its input, so drawing
    256 histogram bins across 69 cells looked at 138 of them and never read the
    other 118. A file whose distribution spikes at one of those byte values --
    a padded bank, a fill byte, a single-byte XOR key -- rendered as a flat
    chart with nothing indicating a bar had been skipped. Silent omission in
    the view whose entire job is showing you the outlier.
    """

    def test_a_spike_at_any_byte_value_is_drawn(self):
        invisible = []
        for spike in range(256):
            counts = [10] * 256
            counts[spike] = 100000
            rows = viz.braille_line(counts, width=69, height=6, vmin=0, fill=True)
            if all(ch == "⠀" for ch in rows[0]):
                invisible.append(spike)
        assert not invisible, (
            f"{len(invisible)} byte values render invisible: {invisible[:8]}")

    def test_downsampling_reports_the_peak_not_a_sample(self):
        vals = [0] * 1000
        vals[500] = 42
        assert max(viz._sample_columns(vals, 20)) == 42

    def test_upsampling_still_interpolates_smoothly(self):
        """A four-point line must not become four steps."""
        cols = viz._sample_columns([0, 1, 2, 3], 16)
        assert len(cols) == 16 and cols[0] == 0 and cols[-1] == 3
        assert cols == sorted(cols), "an upsampled ramp should stay monotonic"

    def test_the_chart_and_its_colours_agree(self):
        """column_peaks feeds the colour; braille_line draws the bar. They read
        the same sampling now, and must keep doing so."""
        import random
        random.seed(2)
        vals = [random.random() for _ in range(500)]
        peaks = viz.column_peaks(vals, 40)
        rows = viz.braille_line(vals, width=40, height=8, vmin=0.0, vmax=1.0)
        tallest = peaks.index(max(peaks))
        assert rows[0][tallest] != "⠀", (
            "the cell coloured hottest is not the cell drawn tallest")


class TestRampColor:
    def test_the_ends_are_the_palette_ends(self):
        assert ramp_color(0.0).lower() == PALETTE[0].lower()
        assert ramp_color(1.0).lower() == PALETTE[-1].lower()

    def test_it_interpolates_rather_than_quantising(self):
        """Eight stops cannot show a magnitude; the point is the in-between."""
        shades = {ramp_color(i / 40) for i in range(41)}
        assert len(shades) > len(PALETTE) * 2

    def test_out_of_range_and_nan_are_clamped(self):
        assert ramp_color(-5) == ramp_color(0.0)
        assert ramp_color(99) == ramp_color(1.0)
        assert ramp_color(float("nan")) == ramp_color(0.0)


# ── byte ranges in the file-backed views ──────────────────────────────

@pytest.fixture
def two_halves(tmp_path):
    """Zeros then noise, so a range genuinely changes the answer."""
    import random
    random.seed(11)
    p = tmp_path / "halves.bin"
    p.write_bytes(b"\x00" * 8192
                  + bytes(random.randrange(256) for _ in range(8192)))
    return str(p)


class TestByteRanges:
    def test_entropy_over_a_range_reads_that_range(self, two_halves):
        lo, _s, _sm = viz.file_entropy(two_halves, windows=4, start=0, end=8192)
        hi, _s, _sm = viz.file_entropy(two_halves, windows=4, start=8192)
        assert max(lo) < 0.1, "the zero half should be flat at zero"
        assert min(hi) > 7.0, "the noise half should be near 8"

    def test_the_reported_size_is_the_range_not_the_file(self, two_halves):
        _v, size, _s = viz.file_entropy(two_halves, windows=4, start=8192)
        assert size == 8192

    def test_the_default_is_still_the_whole_file(self, two_halves):
        a = viz.file_entropy(two_halves, windows=8)
        b = viz.file_entropy(two_halves, windows=8, start=0, end=None)
        assert a == b

    def test_hilbert_over_a_range_maps_that_range(self, two_halves):
        grid, _side, _s = viz.hilbert_from_file(two_halves, order=3,
                                                start=0, end=8192)
        cells = [c for row in grid for c in row if c is not None]
        assert cells and max(cells) == 0, "the zero half should map to zeros"

    def test_a_range_past_the_end_is_clamped_not_crashed(self, two_halves):
        vals, size, _s = viz.file_entropy(two_halves, windows=4,
                                          start=999999, end=1000000)
        assert vals == [] and size == 0

    def test_a_reversed_range_yields_nothing(self, two_halves):
        _v, size, _s = viz.file_entropy(two_halves, windows=4,
                                        start=8000, end=10)
        assert size == 0


# ── the keys, and what the caption admits to ──────────────────────────

@pytest.fixture
def wav(tmp_path):
    import random
    random.seed(5)
    audio = bytes(random.randrange(256) for _ in range(40000))
    body = b"WAVE"
    body += b"fmt " + struct.pack("<I", 16) + struct.pack(
        "<HHIIHH", 1, 2, 44100, 176400, 4, 16)
    body += b"data" + struct.pack("<I", len(audio)) + audio
    p = tmp_path / "t.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(p)


def _caption(app, mode):
    return app._viz_render(mode).plain.splitlines()[0]


class TestScaleKey:
    def test_S_cycles_and_the_caption_names_the_axis(self, wav):
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                app._view = "entropy"
                seen = []
                for _ in range(len(app._VIZ_SCALES["entropy"])):
                    seen.append(_caption(app, "entropy"))
                    await pilot.press("S")
                    await pilot.pause()
                assert all("scale " in c for c in seen), seen
                assert len(set(seen)) == len(seen), (
                    f"a scale change left the caption identical: {seen}")
        _run(scenario)

    def test_S_declines_on_a_view_with_no_axis(self, wav):
        """hex and hilbert have no magnitude axis. A key that appears to work
        and changes nothing is the defect this whole file is about."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                app._view = "hilbert"
                before = dict(app._viz_scale)
                await pilot.press("S")
                await pilot.pause()
                assert app._viz_scale == before
                assert app._scale_for("hilbert") is None
        _run(scenario)

    def test_the_default_axis_is_the_untransformed_one(self, wav):
        """A first-run picture must not be secretly stretched."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                assert app._scale_for("entropy") == "absolute"
                assert app._scale_for("histogram") == "absolute"
                assert "0-8" in _caption(app, "entropy")
        _run(scenario)


class TestScopeKey:
    def test_r_scopes_the_graph_to_the_selected_node(self, wav):
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                app._view = "entropy"
                assert "whole file" in _caption(app, "entropy")
                for _ in range(2):
                    await pilot.press("down")
                await pilot.press("r")
                await pilot.pause()
                lo, hi, _label = app._viz_range()
                assert (lo, hi) != (0, app.fsize), "scope did not narrow"
                cap = _caption(app, "entropy")
                assert "whole file" not in cap and f"0x{lo:08x}" in cap
        _run(scenario)

    def test_r_toggles_back(self, wav):
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                app._view = "entropy"
                await pilot.press("down")
                await pilot.press("r")
                await pilot.press("r")
                await pilot.pause()
                assert app._viz_range() == (0, app.fsize, "whole file")
        _run(scenario)

    def test_a_selection_with_no_bytes_says_it_fell_back(self, wav):
        """Silently showing the whole file under a caption that claims a region
        is worse than not scoping at all."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                app._view = "entropy"
                app._viz_scope = "region"
                app._cur_node = None
                lo, hi, label = app._viz_range()
                assert (lo, hi) == (0, app.fsize)
                assert "whole file" in label and "no bytes" in label
        _run(scenario)

    def test_r_declines_in_the_hex_view(self, wav):
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                assert app._view == "hex"
                await pilot.press("r")
                await pilot.pause()
                assert app._viz_scope == "file"
        _run(scenario)

    def test_the_container_marker_is_dropped_when_scoped(self, wav):
        """It answers "where does the FILE end", which is meaningless once the
        x axis is one chunk -- and it would be drawn at a wrong column."""
        async def scenario():
            app = AcidcatTUI(wav)
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                app._view = "entropy"
                for _ in range(2):
                    await pilot.press("down")
                await pilot.press("r")
                await pilot.pause()
                assert "container ends at" not in app._viz_render("entropy").plain
        _run(scenario)
