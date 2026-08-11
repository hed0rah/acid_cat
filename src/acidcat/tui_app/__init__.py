"""acidcat TUI -- interactive terminal explorer for audio and preset files.

Walk a file's structure in a tree, view any node's bytes in a hex pane, scan for
hidden/embedded audio, carve regions, extract samples, edit metadata, and repair
containers -- all over the never-raise walk engine, so the core stays
zero-dependency (Textual loads only when the TUI runs).

Split into render (byte/field helpers + edit profiles), screens (the modal
widgets + hex pane), and app (the AcidcatTUI application). This module re-exports
the stable surface.
"""

from acidcat.tui_app.app import AcidcatTUI  # noqa: F401
from acidcat.tui_app.render import edit_profile, hex_text, text_field_for  # noqa: F401,E501
from acidcat.tui_app.screens import (  # noqa: F401
    BrowseScreen, ConfirmScreen, DiffScreen, DiscScreen, EditScreen, HelpScreen,
    HexPane, MapScreen, PromptScreen, RegionsScreen, ValidateScreen,
)

__all__ = [
    "AcidcatTUI", "edit_profile", "hex_text", "text_field_for",
    "BrowseScreen", "ConfirmScreen", "DiffScreen", "DiscScreen", "EditScreen",
    "HelpScreen", "HexPane", "MapScreen", "PromptScreen", "RegionsScreen",
    "ValidateScreen",
]
