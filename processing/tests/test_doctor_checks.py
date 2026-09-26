"""`altair doctor`'s checks with a fake Windows probe (SPEC §4.1, §7.5, §7.10, §8.2)."""
from __future__ import annotations

import json
from pathlib import Path

from altair import doctor as doc
from altair.catalog.db import Catalog
from altair.storage.locations import S3Location

from helpers import add_frame, pipeline_config


class FakeProbe(doc.Probe):
    windows = True

    def __init__(self, **answers):
        self.answers = answers

    def powershell(self, script, timeout=30):
        for key, value in self.answers.items():
            if key in script:
                return value
        return None

    def registry(self, key, name):
        return self.answers.get(name)

    def file_version(self, path):
        return self.answers.get("version")

    def session_id(self):
        return self.answers.get("session", 1)

    def long_paths(self):
        return self.answers.get("long_paths", True)


def by_label(results):
    return {c.label: c for c in results}


def test_helpers():
    assert doc.window_inside((5, 14), (4, 15)) and not doc.window_inside((5, 14), (8, 17))
    assert doc.window_inside((23, 2), (22, 3)) and not doc.window_inside((23, 2), (0, 12))
    assert doc.parse_speed("2.5 Gbps") == 2.5 and doc.parse_speed("100 Mbps") == 0.1 and doc.parse_speed(None) is None
    assert doc.unc_host("//nas/astro") == "nas" and doc.unc_host(r"\\nas01\astro") == "nas01" and doc.unc_host("D:/nas") is None
    assert doc.drive_of(Path("/tmp/work")) is None   # no drive letter


def test_this_pc(tmp_path):
    config = pipeline_config(tmp_path, pixinsight={"tested_versions": ["1.9.3"]})
    probe = FakeProbe(version="1.9.2.1170", ActiveHoursStart=8, ActiveHoursEnd=17, ExclusionPath=str(config.paths.work_dir),
                      MediaType="HDD|SATA", session=0, long_paths=False)
    d = doc.Doctor(Catalog(config.catalog_path), config, probe=probe)
    d.pc()
    r = by_label(d.results)
    assert r["state folder writable"].status == "ok"
    assert r["PixInsight version tested"].status == "warn" and "1.9.2.1170 (file version)" in r["PixInsight version tested"].detail
    assert r["Windows Update active hours cover processing"].status == "warn"
    assert r["Defender excludes work/cache/spool"].status == "warn" and "cache (" in r["Defender excludes work/cache/spool"].detail
    assert "work (" not in r["Defender excludes work/cache/spool"].detail
    assert r["interactive session (not a service)"].status == "warn" and r["long paths enabled"].status == "warn"
    # tmp paths have no drive letter: on Windows that means a network or unusual path.
    assert r["work on a local SSD"].status == "warn"

    good = FakeProbe(version="1.9.3.1170", ActiveHoursStart=4, ActiveHoursEnd=15,
                     ExclusionPath=";".join(str(p) for p in (config.paths.work_dir, config.cache_dir, config.paths.spool_dir)))
    d = doc.Doctor(None, config, probe=good)
    d.pc()
    r = by_label(d.results)
    assert {r[k].status for k in ("PixInsight version tested", "Windows Update active hours cover processing",
                                   "Defender excludes work/cache/spool")} == {"ok"}
    assert doc.Doctor(None, config, probe=FakeProbe(ExclusionPath="N/A: Must be an administrator to view exclusions")).pc() is None


def test_nas_link(tmp_path):
    config = pipeline_config(tmp_path, storage={"locations": [{"name": "nas", "kind": "fs", "root": "//nas01/astro"}]})
    d = doc.Doctor(None, config, probe=FakeProbe(Dialect="3.1.1", LinkSpeed="1 Gbps"))
    d.nas()
    r = by_label(d.results)
    assert r["SMB dialect ≥ 3"].status == "ok" and r["link speed ≥ 2.5 Gbit/s"].status == "warn"
    d = doc.Doctor(None, config, probe=FakeProbe(Dialect="2.1", LinkSpeed="10 Gbps"))
    d.nas()
    r = by_label(d.results)
    assert r["SMB dialect ≥ 3"].status == "warn" and r["link speed ≥ 2.5 Gbit/s"].status == "ok"


def test_s3_that_can_delete_raw_data_fails(tmp_path, s3_client):
    config = pipeline_config(tmp_path, s3={})
    catalog = Catalog(config.catalog_path)
    d = doc.Doctor(catalog, config, probe=FakeProbe(), s3=S3Location(config.storage.s3, s3_client))
    d.s3_archive()
    r = by_label(d.results)
    assert r["delete denied under raw/"].status == "fail"    # moto lets anyone delete: exactly what doctor must catch
    assert catalog.one("SELECT status FROM issues WHERE kind = 'S3_CONFIG_UNSAFE'")["status"] == "open"


def test_rig_headers_and_rotator(tmp_path):
    config = pipeline_config(tmp_path, hub=True)
    catalog = Catalog(config.catalog_path)
    for i in range(3):
        add_frame(catalog, OBJECT="M31", GAIN=100, OFFSET=50, FOCALLEN=550)       # no CCD-TEMP, no ROTATOR, no #id
    add_frame(catalog, OBJECT="#34 M31", GAIN=100, OFFSET=50, FOCALLEN=550, ROTATOR=31250, **{"CCD-TEMP": -10})
    d = doc.Doctor(catalog, config, probe=FakeProbe())
    d.rigs()
    r = by_label(d.results)
    headers = r["NINA headers (4 recent lights)"]
    assert headers.status == "warn"
    assert "no CCD-TEMP: 3" in headers.detail and "no ROTATOR: 3" in headers.detail and "OBJECT without #<target id>: 3" in headers.detail
    assert r["rotator wraps"].status == "warn" and r["share reachable"].status == "ok"
    assert json.loads(doc.as_json(d.results))[0]["section"] == "rig esprit"
