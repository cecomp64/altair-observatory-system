"""S3 bucket setup and the safety checks (SPEC §7.5), NAS init and the
health guard (SPEC §7.10), and catalog backup/restore (SPEC §7.9)."""
from __future__ import annotations

import json
import sqlite3

import pytest
from botocore.exceptions import ClientError

from altair.catalog.db import Catalog
from altair.storage import catalog_backup, s3_setup
from altair.storage import nas as nas_mod
from altair.storage.locations import nas_location, s3_location

from helpers import pipeline_config


def test_s3_init_configures_the_bucket_and_check_flags_what_the_daemon_can_delete(tmp_path, s3_client):
    config = pipeline_config(tmp_path, s3={"object_lock": {"mode": "GOVERNANCE"}})
    cfg = config.storage.s3
    policy = s3_setup.init(s3_client, cfg)
    deletes = [s for s in policy["Statement"] if "s3:DeleteObject" in s["Action"]][0]["Resource"]
    assert all("multinight" in r or "catalog" in r for r in deletes)

    lifecycle = s3_client.get_bucket_lifecycle_configuration(Bucket="astro-archive")["Rules"]
    raw = next(r for r in lifecycle if "raw_light" in r["ID"])
    assert raw["Transitions"] == [{"Days": 120, "StorageClass": "DEEP_ARCHIVE"}]
    assert {"Key": "altair-class", "Value": "raw_light"} in raw["Filter"]["And"]["Tags"]

    results = {label: ok for label, ok, _ in s3_setup.check(s3_client, cfg)}
    assert results["bucket versioning enabled"] and results["public access blocked"] and results["lifecycle rules"]
    assert results["delete denied under raw/"] is False   # moto has no IAM: exactly what doctor must catch

    catalog = Catalog(config.catalog_path)
    assert s3_setup.flag(catalog, config, s3_setup.check(s3_client, cfg)) is False
    assert catalog.one("SELECT status FROM issues WHERE kind = 'S3_CONFIG_UNSAFE'")["status"] == "open"

    class DenyingClient:
        def __init__(self, inner):
            self.inner = inner

        def delete_object(self, **kwargs):
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "DeleteObject")

        def __getattr__(self, name):
            return getattr(self.inner, name)

    assert s3_setup.flag(catalog, config, s3_setup.check(DenyingClient(s3_client), cfg)) is True
    assert catalog.one("SELECT status FROM issues WHERE kind = 'S3_CONFIG_UNSAFE'")["status"] == "resolved"


def test_nas_init_writes_an_identity_and_refuses_someone_elses_nas(tmp_path):
    config = pipeline_config(tmp_path)
    catalog = Catalog(config.catalog_path)
    identity = nas_mod.init(catalog, config)
    assert json.loads((tmp_path / "nas" / ".altair-location.json").read_text())["id"] == identity["id"]
    assert all((tmp_path / "nas" / d).is_dir() for d in nas_mod.TREE)
    assert nas_mod.check(catalog, config).usable

    other = Catalog(tmp_path / "other" / "catalog.sqlite")
    with pytest.raises(nas_mod.NasError, match="already initialised"):
        nas_mod.init(other, config)
    assert nas_mod.init(other, config, adopt=True)["id"] == identity["id"]


def test_many_known_files_missing_at_once_marks_the_nas_unhealthy_not_lost(tmp_path):
    config = pipeline_config(tmp_path)
    catalog = Catalog(config.catalog_path)
    nas_mod.init(catalog, config)
    with catalog.transaction() as tx:
        for i in range(10):
            path = tmp_path / "nas" / f"f{i}"
            path.write_text("x")
            tx.execute("INSERT INTO blobs(sha256, size_bytes, data_class, logical_path, created_at) VALUES (?, 1, 'raw_light', ?, 'now')", (f"{i:064d}", f"f{i}"))
            tx.execute("INSERT INTO replicas(sha256, location, uri, state, verified_at) VALUES (?, 'nas', ?, 'present', 'now')", (f"{i:064d}", str(path)))
    (tmp_path / "nas" / "f0").unlink()
    assert nas_mod.check(catalog, config).healthy               # one stray file: healed later, not a mount problem
    for i in range(1, 5):
        (tmp_path / "nas" / f"f{i}").unlink()
    health = nas_mod.check(catalog, config)
    assert not health.healthy and "missing" in health.reason
    assert catalog.one("SELECT count(*) AS n FROM replicas WHERE state = 'missing'")["n"] == 0   # nothing marked missing


def test_catalog_backup_goes_to_the_nas_and_restores(tmp_path):
    config = pipeline_config(tmp_path)
    catalog = Catalog(config.catalog_path)
    nas_mod.init(catalog, config)
    catalog.set_state("marker", "before")
    sha, logical = catalog_backup.backup(catalog, config, nas_location(config))
    assert logical.startswith("catalog/altair-") and (tmp_path / "nas" / logical).exists()
    assert catalog.one("SELECT data_class FROM blobs WHERE sha256 = ?", (sha,))["data_class"] == "metadata"
    catalog.set_state("marker", "after")
    catalog.close()

    restored = catalog_backup.restore(config, nas=nas_location(config))
    assert sqlite3.connect(restored).execute("SELECT value FROM hub_state WHERE key = 'marker'").fetchone()[0] == '"before"'
    assert list(restored.parent.glob("catalog.sqlite.replaced-*"))


def test_catalog_restore_from_s3(tmp_path, s3_client):
    config = pipeline_config(tmp_path, s3={})
    catalog = Catalog(config.catalog_path)
    nas_mod.init(catalog, config)
    sha, logical = catalog_backup.backup(catalog, config, nas_location(config))
    s3 = s3_location(config, s3_client)
    s3.upload(tmp_path / "nas" / logical, logical, sha, "metadata")
    catalog.close()
    (tmp_path / "nas" / logical).chmod(0o644)
    (tmp_path / "nas" / logical).unlink()
    assert catalog_backup.restore(config, s3=s3).exists()
