"""Is a frame file complete? (SPEC §7.3 step 3: it must parse as FITS or
XISF with a data size that matches its header, which catches files NINA is
still writing or that were truncated.)"""
from __future__ import annotations

import re
import struct
from pathlib import Path

BLOCK = 2880


def fits_expected_size(path: Path) -> int | None:
    """Header blocks plus the primary HDU's padded data, or None if the header
    can't be read to its END card."""
    cards: dict[str, str] = {}
    header_bytes = 0
    with path.open("rb") as handle:
        while True:
            block = handle.read(BLOCK)
            if len(block) < BLOCK:
                return None
            header_bytes += BLOCK
            for i in range(0, BLOCK, 80):
                card = block[i:i + 80].decode("ascii", "replace")
                key = card[:8].strip()
                if key == "END":
                    return header_bytes + _padded(_data_bytes(cards))
                if card[8:10] == "= ":
                    cards[key] = card[10:].split("/", 1)[0].strip()
            if header_bytes > 1000 * BLOCK:
                return None


def _data_bytes(cards: dict[str, str]) -> int:
    try:
        naxis = int(cards.get("NAXIS", "0"))
        if naxis == 0:
            return 0
        bitpix = abs(int(cards["BITPIX"]))
        count = 1
        for n in range(1, naxis + 1):
            count *= int(cards[f"NAXIS{n}"])
        return bitpix // 8 * int(cards.get("GCOUNT", "1")) * (int(cards.get("PCOUNT", "0")) + count)
    except (KeyError, ValueError):
        return 0


def _padded(size: int) -> int:
    return (size + BLOCK - 1) // BLOCK * BLOCK


def xisf_expected_size(path: Path) -> int | None:
    with path.open("rb") as handle:
        if handle.read(8) != b"XISF0100":
            return None
        raw = handle.read(8)
        if len(raw) < 8:
            return None
        (length,) = struct.unpack("<I", raw[:4])
        xml = handle.read(length)
        if len(xml) < length:
            return None
    end = 16 + length
    for pos, size in re.findall(rb'attachment:(\d+):(\d+)', xml):
        end = max(end, int(pos) + int(size))
    return end


def complete(path: str | Path) -> bool:
    path = Path(path)
    try:
        size = path.stat().st_size
        suffix = path.suffix.lower()
        if suffix in (".fits", ".fit", ".fts"):
            expected = fits_expected_size(path)
            # NINA pads to whole blocks; a file may also end right after the data.
            return expected is not None and size >= expected - (BLOCK - 1)
        if suffix == ".xisf":
            expected = xisf_expected_size(path)
            return expected is not None and size >= expected
    except OSError:
        return False
    return True
