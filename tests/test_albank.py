"""Tests for the N64 ALBankFile (.ctl) walker. Builds a minimal one-instrument
bank (bankfile -> bank -> instrument -> sound -> wavetable -> ADPCM book) and
checks it sniffs as `albank` and the tree walks to the right sample rate + book."""
import struct


def _make_ctl():
    buf = bytearray()
    off = {}

    def add(name, data):
        off[name] = len(buf)
        buf.extend(data)
        while len(buf) % 8:                            # books want 8-byte alignment
            buf.append(0)
        return off[name]

    add("hdr", b"\x42\x31\x00\x01" + b"\x00\x00\x00\x00")   # rev, bankCount=1, bank ptr (patched)
    coefs = struct.pack(">16h", *([100, -50, 25, -12, 6, -3, 1, 0] * 2))
    add("book", struct.pack(">ii", 2, 1) + coefs)          # ALADPCMBook order 2 npred 1
    add("wt", struct.pack(">iiBBH", 0, 512, 0, 0, 0) + struct.pack(">II", 0, off["book"]))
    add("snd", struct.pack(">III", 0, 0, off["wt"]) + struct.pack(">BBBB", 64, 127, 0, 0))
    inst = bytearray(0x10)
    struct.pack_into(">h", inst, 0x0E, 1)                   # soundCount = 1
    inst += struct.pack(">I", off["snd"])
    add("inst", bytes(inst))
    bank = struct.pack(">hBBiI", 1, 0, 0, 22050, 0) + struct.pack(">I", off["inst"])
    add("bank", bank)
    struct.pack_into(">I", buf, 4, off["bank"])            # patch bankfile -> bank
    return bytes(buf)


def test_sniff_albank(tmp_path):
    from acidcat.core.infra import sniff
    f = tmp_path / "bank.ctl"
    f.write_bytes(_make_ctl())
    assert sniff.sniff(str(f)) == "albank"
    # a bare 0x4231 with no valid bank must NOT sniff as albank
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"\x42\x31\x00\x01" + b"\xFF\xFF\xFF\xFF" + bytes(32))
    assert sniff.sniff(str(bad)) != "albank"


def test_walk_albank(tmp_path):
    from acidcat.core.walk import walk_file
    f = tmp_path / "bank.ctl"
    f.write_bytes(_make_ctl())
    label, chunks, warns = walk_file(str(f))
    assert "N64 audio bank" in label and warns == []
    hdr, bank = chunks
    assert hdr["id"] == "ALBankFile"
    fields = {x["name"]: x["value"] for x in hdr["fields"]}
    assert fields["revision"] == "0x4231" and fields["bankCount"] == 1
    bfields = {x["name"]: x["value"] for x in bank["fields"]}
    assert bfields["sampleRate"] == "22050 Hz"
    assert bfields["instCount"] == 1
    assert "1 VADPCM" in bfields["waveforms"]


def test_offset_tables_that_run_past_the_file_do_not_raise(tmp_path):
    """A count is a claim about the file, including about the TABLE it indexes.

    Every offset table here is walked `count` times, and the walker checked
    the bytes each pointer pointed AT while assuming the four bytes holding
    the pointer were readable. They are not, on a bank whose declared counts
    outlive its length: `struct.error` escaped the walk instead of the clean
    refusal the contract promises.

    Truncating a valid bank leaves exactly that shape -- the count still says
    there is a bank, and the four bytes that would hold its offset are half
    present. Six bytes, not sixteen: at sixteen the pointer is readable and
    the walker bails on the pointed-AT check instead, so the guard here is
    never reached and the test passes with the bug still in.
    """
    from acidcat.core.walk import albank as walker

    full = _make_ctl()
    head = full[:6]              # revision + bankCount, then HALF a bank pointer
    p = tmp_path / "truncated.ctl"
    p.write_bytes(head)

    chunks, warns = walker.inspect_albank(str(p))   # must not raise

    assert chunks, "a header-only bank should still report its header"
    assert chunks[0]["id"] == "ALBankFile"


def test_a_bank_pointer_past_eof_reads_as_absent(tmp_path):
    """The pointer table itself is out of range, so every entry is absent.

    Zero rather than an exception, because zero is already what the walker
    reads as "no such entry" -- a table slot that is not in the file is
    exactly that.
    """
    from acidcat.core.walk import albank as walker

    # revision 'B1', 64 banks declared, and nothing after the header
    p = tmp_path / "lying.ctl"
    p.write_bytes(struct.pack(">HH", 0x4231, 64) + b"\x00" * 8)

    chunks, warns = walker.inspect_albank(str(p))

    assert chunks[0]["summary"].endswith("64 bank(s)")
    banks = [f for f in chunks[0]["fields"] if f["name"].startswith("bank[")]
    assert len(banks) == 64, "every declared slot should still be reported"
    assert all("0x0" == f["value"].replace("-> ", "") for f in banks[2:]), \
        "slots past the end of the file should read as absent, not as garbage"
    assert len(chunks) == 1, "no bank body should be walked from an absent pointer"
