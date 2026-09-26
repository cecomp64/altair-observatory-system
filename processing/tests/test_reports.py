"""Per-night and per-merge reports (SPEC §13): written next to the viewing
copies and in the cache, and sent to the Hub with the data product."""
from __future__ import annotations

from altair.catalog.db import Catalog
from altair.executor.executor import Executor
from altair.planner.plan import Planner
from altair import reports

from helpers import add_frame, fake_pixinsight, make_sync, pipeline_config

NIGHT = "2026-09-24"


def processed_night(tmp_path, *, hub=False, control=None):
    config = pipeline_config(tmp_path, hub=hub, pixinsight=fake_pixinsight(tmp_path, control),
                             paths={"published": str(tmp_path / "published")})
    catalog = Catalog(config.catalog_path)
    nas = config.storage.nas.root
    for i in range(6):
        add_frame(catalog, night=NIGHT, date_obs=f"2026-09-25T06:{i:02d}:00Z", nas_root=nas)
    for i in range(3):
        add_frame(catalog, "dark", night=NIGHT, date_obs=f"2026-09-25T12:0{i}:00Z", nas_root=nas)
        add_frame(catalog, "darkflat", night=NIGHT, exposure=2.5, date_obs=f"2026-09-25T12:1{i}:00Z", nas_root=nas)
        add_frame(catalog, "flat", night=NIGHT, exposure=2.5, date_obs=f"2026-09-25T12:2{i}:00Z", nas_root=nas)
    Planner(catalog, config).plan_night("esprit", NIGHT)
    Executor(catalog, config).run_all()
    return config, catalog


def test_the_night_report(tmp_path):
    rejected = []
    config, catalog = processed_night(tmp_path, control={"reject": rejected})
    nm = catalog.one("SELECT * FROM night_masters")
    text = (tmp_path / "published" / "T34" / NIGHT / "report_Ha.md").read_text(encoding="utf-8")
    assert text.startswith(f"# T34 · Ha · night {NIGHT}")
    assert "Final night master on **esprit** (esprit100 / asi2600mm)" in text and "Merge status: **merged**" in text
    assert "| Frames used | 6 of 6 |" in text and "| Integration | 0.50 h |" in text and "| Overlap with the reference | 96% |" in text
    # Calibration with its match evidence.
    assert "dark · 2026-09-24 · 300 s · -10 °C · 3 subs" in text and "Δt +0 °C" in text
    assert "flat · 2026-09-24 · 2.5 s · rotator 31250 · 3 subs" in text
    assert "same night" in text and "rotator Δ 0" in text
    # One row per frame, and timings.
    assert text.count("| used |") == 6 and "| light_" in text
    assert "## Timings" in text and "| PixInsight |" in text
    assert "## Open issues\n\nNone." in text
    assert reports.report_path(config, "night_master", nm["id"]).read_text(encoding="utf-8") == text


def test_the_merge_report_and_the_hub(tmp_path, fake_hub):
    config, catalog = processed_night(tmp_path, hub=True)
    m = catalog.one("SELECT * FROM multi_night_masters")
    local = (tmp_path / "published" / "T34" / "multinight" / "Ha_v001_report.md").read_text(encoding="utf-8")
    assert local.startswith("# T34 · Ha · multi-night master v1")
    assert "| Mode | master_merge |" in local and "| Night weighting | measured_psf_signal |" in local
    assert f"| {NIGHT} | 6 | 0.50 h |" in local and "100.0 %" in local and "## Excluded nights\n\nNone." in local
    assert "![Coverage: nights per pixel](Ha_v001_coverage.jpg)" in local
    assert (tmp_path / "published" / "T34" / "multinight" / "Ha_v001_coverage.jpg").exists()
    cached = reports.report_path(config, "multi_night_master", m["id"]).read_text(encoding="utf-8")
    assert "## Coverage" not in cached     # the Hub copy has no local image links

    make_sync(catalog, config, fake_hub).drainer.drain()
    assert fake_hub.contract_violations == []
    night = next(v for (k, _), v in fake_hub.products.items() if k == "night_master")
    merge = next(v for (k, _), v in fake_hub.products.items() if k == "multi_night_master")
    assert "report" in night["files"] and night["report"].startswith(f"# T34 · Ha · night {NIGHT}")
    assert merge["report"] == cached.rstrip("\n")   # the multipart parser drops the trailing newline


def test_refresh_rewrites_reports(tmp_path):
    config, catalog = processed_night(tmp_path)
    path = tmp_path / "published" / "T34" / NIGHT / "report_Ha.md"
    path.unlink()
    assert reports.refresh_all(catalog, config) == 2 and path.exists()
