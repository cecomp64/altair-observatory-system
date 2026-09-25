from altair.hub.reconcile import local_digest, reconcile

from conftest import TRAIN
from helpers import index_night, make_sync


def test_config_is_cached_and_etag_skips_unchanged_payloads(catalog, config, fake_hub):
    sync = make_sync(catalog, config, fake_hub)
    config1 = sync.pull_config()
    config2 = sync.pull_config()
    assert config1.targets.keys() == config2.targets.keys() == {34, 35, 36}
    assert fake_hub.requests.count(("GET", "/api/v1/processing/config")) == 2

    # The Hub goes away: the cached copy still answers.
    from altair.hub.config_sync import load_cached

    assert load_cached(catalog).canonical_filter("backyard-16in", TRAIN, "h-alpha") == "Ha"


def test_config_mismatch_blocks_the_rig_until_fixed(catalog, config, fake_hub):
    fake_hub.config["telescopes"][0]["timezone"] = "Europe/Lisbon"
    fake_hub.etag = '"v2"'
    sync = make_sync(catalog, config, fake_hub)
    sync.pull_config()
    issue = catalog.one("SELECT * FROM issues WHERE kind = 'HUB_CONFIG_MISMATCH'")
    assert issue["status"] == "open" and "timezone" in issue["message"]

    fake_hub.config["telescopes"][0]["timezone"] = "America/Los_Angeles"
    fake_hub.etag = '"v3"'
    sync.pull_config()
    assert catalog.one("SELECT status FROM issues WHERE kind = 'HUB_CONFIG_MISMATCH'")["status"] == "resolved"


def test_reconcile_requeues_a_night_the_hub_is_missing_frames_for(catalog, config, tmp_path, fake_hub):
    index_night(catalog, config, tmp_path, fake_hub, lights=3)
    sync = make_sync(catalog, config, fake_hub)
    sync.drainer.drain()
    from altair import nights

    nights.close(catalog, config, rig="esprit", night="2026-09-24", closed_by="manual")
    sync.drainer.drain()

    assert reconcile(catalog, config, fake_hub.client()) == [{"rig": "esprit", "night": "2026-09-24", "ok": True, "requeued": 0}]

    fake_hub.frames.pop(next(iter(fake_hub.frames)))  # the Hub lost one
    report = reconcile(catalog, config, fake_hub.client())
    assert report[0]["ok"] is False and report[0]["requeued"] == 4
    sync.drainer.drain()
    assert reconcile(catalog, config, fake_hub.client())[0]["ok"] is True
    assert local_digest(catalog, "esprit", "2026-09-24")["frame_count"] == 4
