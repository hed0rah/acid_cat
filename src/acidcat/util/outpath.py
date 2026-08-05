"""One guard against a verb destroying the file it is reading.

`carve -o` pointed at its own input truncated a 2,044-byte WAV to the 4 bytes it
had been asked to extract, and exited 0. `convert` has the same shape and can
reach it without `-o` at all, since the default output name is the input's stem
plus the target extension -- so converting a `.wav` to WAV replaces the original
with the converted bytes.

Neither is a torn write: both read the input fully into memory before opening
the output, so the bytes that land are correct. The file that was there before
is simply gone, and both commands' help says the input is never modified.

`repair` and `write` are already safe by a different route -- they go through
core/write/writer.py's atomic temp + os.replace, so even out == input is a clean
in-place replacement of fully-computed data.
"""

import os


def same_file(a, b):
    """True if two paths name the same file. Tolerates `b` not existing yet.

    os.path.samefile needs both to exist, which is exactly not the case when
    checking an output path before creating it, so fall back to comparing
    resolved paths -- that also collapses symlinks and, on Windows, differing
    case and separators.
    """
    if not a or not b:
        return False
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.realpath(a) == os.path.realpath(b)


def refuse_self_overwrite(verb, source, out):
    """An error string when `out` would clobber `source`, else None.

    Returning the message rather than printing keeps this usable from commands
    that report differently, and keeps the wording identical across them.
    """
    if out and same_file(source, out):
        return (f"acidcat {verb}: {out}: output is the input; refusing to "
                f"overwrite the file being read")
    return None
