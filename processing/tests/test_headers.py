import struct
from datetime import date

from altair.ingest.headers import night_of, normalize, parse_date_obs, read_header

from conftest import write_fits


def test_normalize_fits(tmp_path, config):
    path = write_fits(tmp_path / "a.fits", filter_="H-alpha", date_obs="2026-09-25T06:10:02")
    fields = normalize(read_header(path), config, config.rigs["esprit"], path.name)
    assert fields["image_type"] == "light"
    assert (fields["telescope"], fields["camera"], fields["filter"]) == ("esprit100", "asi2600mm", "Ha")
    assert fields["night"] == date(2026, 9, 24)  # 23:10 local on the 24th
    assert (fields["rotator_pos"], fields["rotator_units"]) == (31250.0, "steps")
    assert fields["binning"] == "1x1"
    assert (fields["width"], fields["height"]) == (60, 40)


def test_night_rolls_over_at_local_noon():
    assert night_of(parse_date_obs("2026-09-25T18:59:00"), "America/Los_Angeles") == date(2026, 9, 24)  # 11:59 PDT
    assert night_of(parse_date_obs("2026-09-25T19:01:00"), "America/Los_Angeles") == date(2026, 9, 25)  # 12:01 PDT


def test_sexagesimal_objctra(tmp_path, config):
    path = write_fits(tmp_path / "b.fits", ra=None, dec=None, OBJCTRA="00 42 44.3", OBJCTDEC="+41 16 09")
    fields = normalize(read_header(path), config, config.rigs["esprit"], path.name)
    assert abs(fields["ra_deg"] - 10.6846) < 1e-3
    assert abs(fields["dec_deg"] - 41.2692) < 1e-3


def test_xisf_header(tmp_path):
    xml = ('<?xml version="1.0"?><xisf version="1.0" xmlns="http://www.pixinsight.com/xisf"><Image geometry="60:40:1" sampleFormat="Float32">'
           '<FITSKeyword name="IMAGETYP" value="\'LIGHT\'" comment=""/><FITSKeyword name="EXPTIME" value="300." comment=""/>'
           '</Image></xisf>').encode()
    path = tmp_path / "c.xisf"
    path.write_bytes(b"XISF0100" + struct.pack("<I", len(xml)) + b"\0" * 4 + xml)
    header = read_header(path)
    assert header == {"IMAGETYP": "LIGHT", "EXPTIME": 300.0, "NAXIS1": 60, "NAXIS2": 40}
