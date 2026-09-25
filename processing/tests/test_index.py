from altair.hub.config_sync import HubConfig
from altair.index.indexer import index

from conftest import write_fits


def run(catalog, config, fake_hub, root, **kw):
    return index(catalog, config, root, rig="esprit", hub_config=HubConfig.from_payload(fake_hub.config), **kw)


def test_index_catalogues_in_place_and_is_idempotent(catalog, config, tmp_path, fake_hub):
    for i in range(3):
        write_fits(tmp_path / "old" / "2025-10-01" / f"L{i}.fits", object_="#34 M31", seed=i)
    write_fits(tmp_path / "old" / "2025-10-01" / "F0.fits", imagetyp="FLAT", seed=9)
    (tmp_path / "old" / "notes.txt").write_text("not an image")

    first = run(catalog, config, fake_hub, tmp_path / "old")
    second = run(catalog, config, fake_hub, tmp_path / "old")

    assert (first.seen, first.indexed, first.linked, first.errors) == (4, 4, 3, [])
    assert (second.indexed, second.already_known) == (0, 4)
    replica = catalog.one("SELECT location, uri FROM replicas LIMIT 1")
    assert replica["location"] == "external:2025-10-01"
    assert (tmp_path / "old").as_posix() in replica["uri"].replace("\\", "/")
    assert catalog.one("SELECT read_only FROM locations WHERE name = 'external:2025-10-01'")["read_only"] == 1
    assert {r["origin"] for r in catalog.query("SELECT origin FROM frames")} == {"import"}


def test_files_under_the_nas_root_become_nas_replicas_and_adopt_copies(catalog, config, tmp_path, fake_hub):
    nas = tmp_path / "nas"
    write_fits(nas / "raw" / "esprit" / "L0.fits", seed=1)
    run(catalog, config, fake_hub, nas / "raw", nas_root=nas)
    assert catalog.one("SELECT location FROM replicas")["location"] == "nas"

    write_fits(tmp_path / "elsewhere" / "L1.fits", seed=2)
    run(catalog, config, fake_hub, tmp_path / "elsewhere", nas_root=nas, adopt=True)
    adopted = catalog.query("SELECT uri FROM replicas WHERE location = 'nas'")
    assert len(adopted) == 2
    assert (nas / "raw" / "esprit" / "2026-09-24" / "L1.fits").is_file()


def test_other_rigs_are_skipped_and_dry_run_writes_nothing(catalog, config, tmp_path, fake_hub):
    write_fits(tmp_path / "rasa" / "L0.fits", telescop="RASA 8", instrume="ZWO ASI533MC Pro")
    write_fits(tmp_path / "rasa" / "L1.fits", seed=3)
    dry = run(catalog, config, fake_hub, tmp_path / "rasa", dry_run=True)
    assert (dry.skipped_unknown_rig, dry.indexed) == (1, 1)
    assert catalog.one("SELECT count(*) AS n FROM frames")["n"] == 0

    real = run(catalog, config, fake_hub, tmp_path / "rasa")
    assert real.skipped_unknown_rig == 1
    assert catalog.one("SELECT kind FROM issues")["kind"] == "UNKNOWN_RIG"


def test_broken_files_are_reported_not_fatal(catalog, config, tmp_path, fake_hub):
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "junk.fits").write_bytes(b"not fits")
    write_fits(tmp_path / "bad" / "ok.fits")
    report = run(catalog, config, fake_hub, tmp_path / "bad")
    assert report.indexed == 1 and len(report.errors) == 1
