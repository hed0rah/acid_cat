"""A JSON record must be enough to locate its own bytes.

`dump --json` reported `offset` for the 8-byte [id][size] header while `size`
and `hex` described the payload, so feeding one record into `carve --offset`
read eight bytes early and returned the ASCII chunk id. `inspect --json` had the
same skew: `chunk.offset + field.off` landed on the header, not the field.

It was format-dependent, which is what made it dangerous -- a headerless chunk
model like MOD has no skew, so a script tuned on trackers broke silently on
RIFF and AIFF. `inspect --full` already emitted absolute offsets; the cheap
paths now do too.

These tests carve at the offsets the JSON gives and compare against the bytes
the JSON claims are there, so they cannot pass with a skew of any size.
"""

import json
import struct
import subprocess
import sys

import pytest


def _wav(path):
    pcm = b"\x11\x22" * 64
    body = (b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def _run(*args):
    r = subprocess.run([sys.executable, "-m", "acidcat"] + list(args),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytest.fixture
def wav(tmp_path):
    p = tmp_path / "a.wav"
    _wav(p)
    return str(p)


def test_dump_record_locates_its_own_payload(wav):
    rec = json.loads(_run("dump", "--json", wav, "fmt"))
    rec = rec[0] if isinstance(rec, list) else rec

    carved = _run("carve", wav, "--offset", str(rec["payload_offset"]),
                  "--length", str(rec["size"]), "--encoding", "hex")
    assert carved.split() == [rec["hex"][i:i + 2]
                              for i in range(0, len(rec["hex"]), 2)]


def test_dump_offset_still_points_at_the_header(wav):
    """`offset` keeps its meaning -- the fix is additive, not a renumbering."""
    rec = json.loads(_run("dump", "--json", wav, "fmt"))
    rec = rec[0] if isinstance(rec, list) else rec
    assert rec["payload_offset"] == rec["offset"] + 8

    header = _run("carve", wav, "--offset", str(rec["offset"]),
                  "--length", "4", "--encoding", "hex")
    assert bytes.fromhex(header.replace(" ", "")) == b"fmt "


def test_inspect_field_abs_is_the_real_byte(wav):
    doc = json.loads(_run("inspect", "--json", wav).splitlines()[0])
    fmt = [c for c in doc["chunks"] if c["id"].strip() == "fmt"][0]
    assert fmt["payload_base"] == fmt["offset"] + 8

    by_name = {f["name"]: f for f in fmt["fields"]}
    rate = by_name["sample_rate"]

    raw = _run("carve", wav, "--offset", str(rate["abs"]),
               "--length", str(rate["len"]), "--encoding", "hex")
    assert struct.unpack("<I", bytes.fromhex(raw.replace(" ", "")))[0] == 44100

    chans = by_name["channels"]
    raw = _run("carve", wav, "--offset", str(chans["abs"]),
               "--length", str(chans["len"]), "--encoding", "hex")
    assert struct.unpack("<H", bytes.fromhex(raw.replace(" ", "")))[0] == 2


def test_full_and_plain_json_agree_on_absolute_offsets(wav):
    """--full was already correct; the two must not drift apart again."""
    plain = json.loads(_run("inspect", "--json", wav).splitlines()[0])
    full = json.loads(_run("inspect", "--full", wav).splitlines()[0])

    def abs_map(doc):
        return {(c["id"], f["name"]): f.get("abs")
                for c in doc["chunks"] for f in c["fields"]
                if f.get("off") is not None}

    a, b = abs_map(plain), abs_map(full)
    assert a and a == b
