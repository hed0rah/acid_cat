"""Colour themes, and the two ways they could quietly not work.

The theme module always intended alternates -- "add alternates as new dict
entries; the module-level constants are sourced from DEFAULT_THEME, so a future
switcher only re-sources them" -- but two things stood in the way of one:

  Every consumer does `from acidcat.tui_theme import ACCENT`, which binds the
  value at import. A switcher that reassigned the module attribute later would
  appear to work and change nothing, because not one of those names points at
  the module any more. So selection happens at import, from the environment.

  The Textual stylesheets spelled the same hex literally -- 50 of them across
  the app and its screens -- so a theme could recolour every Rich label and
  leave every border, background and panel exactly as it was. That is the half
  of the screen a person actually sees.

Both are asserted here rather than described.
"""

import os
import re
import subprocess
import sys

import pytest

from acidcat import tui_theme as th

# Every key a theme has to define, taken from the default rather than written
# out again -- a list copied by hand is a list that goes stale the first time
# the default gains a colour.
REQUIRED = set(th.THEMES[th.DEFAULT_THEME])


class TestEveryThemeIsComplete:
    @pytest.mark.parametrize("name", sorted(th.THEMES))
    def test_it_defines_every_key(self, name):
        missing = REQUIRED - set(th.THEMES[name])
        assert not missing, f"{name} is missing {sorted(missing)}"

    @pytest.mark.parametrize("name", sorted(th.THEMES))
    def test_every_colour_is_a_hex_triplet(self, name):
        bad = []
        for key, val in th.THEMES[name].items():
            values = (val if isinstance(val, list)
                      else list(val.values()) if isinstance(val, dict)
                      else [val])
            bad += [(key, v) for v in values
                    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", str(v))]
        assert not bad, f"{name}: not colours: {bad}"

    @pytest.mark.parametrize("name", sorted(th.THEMES))
    def test_the_ramp_has_the_same_number_of_stops(self, name):
        """`chunk_color` cycles it and `ramp_color` interpolates across it, so
        a shorter ramp in one theme changes how many chunks are told apart."""
        assert len(th.THEMES[name]["PALETTE"]) == \
            len(th.THEMES[th.DEFAULT_THEME]["PALETTE"])

    @pytest.mark.parametrize("name", sorted(th.THEMES))
    def test_text_classes_are_readable_against_the_background(self, name):
        """The default theme documents why: null at #3A3E45 is 1.65:1 on the
        canvas -- fine behind a solid block, unreadable as the digits "00", and
        00 is the most common byte in every header this points at. A new theme
        must not reintroduce that."""
        t = th.THEMES[name]

        def lum(hexstr):
            n = int(hexstr.lstrip("#"), 16)
            parts = []
            for c in ((n >> 16) & 255, (n >> 8) & 255, n & 255):
                v = c / 255
                parts.append(v / 12.92 if v <= 0.03928
                             else ((v + 0.055) / 1.055) ** 2.4)
            return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]

        bg = lum(t["BG"])
        for cls, colour in t["BYTE_CLASS_TEXT"].items():
            fg = lum(colour)
            ratio = (max(fg, bg) + 0.05) / (min(fg, bg) + 0.05)
            assert ratio >= 2.4, (
                f"{name}: {cls} text at {colour} is {ratio:.2f}:1 on "
                f"{t['BG']}; the default theme treats 2.6:1 as the floor for "
                f"digits you have to read")


class TestSelection:
    def test_the_default_is_the_default(self):
        assert th.ACTIVE_THEME in th.THEMES

    @pytest.mark.parametrize("name", sorted(th.THEMES))
    def test_the_environment_picks_a_theme(self, name):
        """Checked in a subprocess, because selection happens at import and
        this module has already imported."""
        env = dict(os.environ, ACIDCAT_THEME=name, PYTHONPATH="src")
        out = subprocess.run(
            [sys.executable, "-c",
             "from acidcat import tui_theme as t;"
             "print(t.ACTIVE_THEME, t.TEAL, t.ORANGE)"],
            capture_output=True, text=True, env=env, timeout=120)
        got = out.stdout.split()
        assert got and got[0] == name, out.stderr
        assert got[1] == th.THEMES[name]["TEAL"]
        assert got[2] == th.THEMES[name]["ORANGE"]

    def test_an_unknown_theme_falls_back_and_says_so(self):
        """Silently starting in a theme nobody asked for is the failure this
        avoids -- the wrong colours look like a bug in the app."""
        env = dict(os.environ, ACIDCAT_THEME="nonsense", PYTHONPATH="src")
        out = subprocess.run(
            [sys.executable, "-c",
             "from acidcat import tui_theme as t; print(t.ACTIVE_THEME)"],
            capture_output=True, text=True, env=env, timeout=120)
        assert out.stdout.strip() == th.DEFAULT_THEME
        assert "unknown theme" in out.stderr
        for name in th.THEMES:
            assert name in out.stderr, "the refusal does not list the choices"


class TestTheStylesheetFollowsTheTheme:
    """The half of the screen that is not Rich markup."""

    def _css(self, name):
        env = dict(os.environ, ACIDCAT_THEME=name, PYTHONPATH="src")
        out = subprocess.run(
            [sys.executable, "-c",
             "from acidcat.tui_app.app import AcidcatTUI; print(AcidcatTUI.CSS)"],
            capture_output=True, text=True, env=env, timeout=300)
        assert out.returncode == 0, out.stderr
        return out.stdout

    @pytest.mark.parametrize("name", sorted(th.THEMES))
    def test_no_placeholder_survives_into_the_stylesheet(self, name):
        assert "$" not in self._css(name), "an unsubstituted $NAME reached the CSS"

    @pytest.mark.parametrize("name", sorted(th.THEMES))
    def test_the_borders_and_background_are_this_theme(self, name):
        css = self._css(name)
        t = th.THEMES[name]
        assert f"background: {t['BG']}" in css
        assert f"#tree {{ border: round {t['TEAL']}" in css

    def test_two_themes_produce_different_stylesheets(self):
        """If they did not, every colour in the CSS was still hardcoded."""
        assert self._css("brand") != self._css("killengn")

    def test_no_hardcoded_hex_is_left_in_the_source(self):
        """A literal left behind is a colour one theme cannot reach, and it
        shows up as one stubbornly wrong border rather than as a failure."""
        import pathlib
        for name in ("app.py", "screens.py"):
            src = pathlib.Path("src/acidcat/tui_app") / name
            text = src.read_text(encoding="utf-8")
            for block in re.findall(r'th\.css\("""(.*?)"""\)', text, re.S):
                found = re.findall(r"#[0-9A-Fa-f]{6}", block)
                assert not found, f"{name}: hardcoded {found} in a CSS block"


class TestTheHouseThemesAreTheHouseColours:
    """These are brand colours, not decoration: they come from
    brand-tokens.css and the LABL-DNGN palette table."""

    def test_kill_engn_is_teal_with_the_cy_borg_pair(self):
        t = th.THEMES["killengn"]
        assert t["TEAL"] == "#00F9DF"      # engn teal
        assert t["ORANGE"] == "#FF3EB5"    # borg pink, PMS 806C
        assert t["AMBER"] == "#FFE900"     # borg yellow, PMS 803C

    def test_fate_rally_is_teal_with_rally_orange(self):
        t = th.THEMES["faterally"]
        assert t["TEAL"] == "#00F9DF"
        assert t["ORANGE"] == "#FF4D00"    # rally orange

    def test_they_are_not_just_the_default_renamed(self):
        base = th.THEMES[th.DEFAULT_THEME]
        for name in ("killengn", "faterally"):
            assert th.THEMES[name] != base, f"{name} is a copy of the default"


class TestTheEnvVarIsTheOnlyLever:
    """There is no --theme flag, and that has to stay a decision rather than
    drift into an oversight.

    A flag cannot work: the palette is imported through core.forensics.viz when
    the `acidcat` package is imported, which happens before argparse exists, and
    every consumer binds its colours by value at that moment. A flag was written
    and measured -- every theme it selected came out as the default -- so it was
    removed rather than shipped doing nothing.
    """

    def test_the_tui_help_says_how_to_set_a_theme(self):
        out = subprocess.run(
            [sys.executable, "-m", "acidcat", "tui", "--help"],
            capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH="src"), timeout=300)
        assert "ACIDCAT_THEME" in out.stdout, out.stdout[-300:]
        for name in th.THEMES:
            assert name in out.stdout, f"{name} is not offered in the help"

    def test_there_is_no_flag_pretending_to_work(self):
        out = subprocess.run(
            [sys.executable, "-m", "acidcat", "tui", "--help"],
            capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH="src"), timeout=300)
        assert "--theme" not in out.stdout.split("Colour theme")[0], (
            "a --theme option is advertised; it cannot work, see the class "
            "docstring")

    def test_importing_the_package_binds_the_palette(self):
        """The fact the flag foundered on. If this ever stops being true, a
        flag becomes possible and the comment in commands/tui.py is stale."""
        out = subprocess.run(
            [sys.executable, "-c",
             "import sys, acidcat; print('acidcat.tui_theme' in sys.modules)"],
            capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH="src"), timeout=300)
        assert out.stdout.strip() == "True", (
            "the palette is no longer bound by importing the package -- a "
            "--theme flag may now be feasible")
