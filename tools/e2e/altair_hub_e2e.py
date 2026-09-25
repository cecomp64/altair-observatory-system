#!/usr/bin/env python3
"""End-to-end check of Altair against a running Hub (docs/archive/2026-09-integration-plan.md
§9 P4 exit criteria), without PixInsight:

1. index a synthetic archive (frames with the "#<id>" token, and some the
   resolver can't link) and sync: frames appear in the Hub
2. a person assigns the unlinked frames in the Hub (via the Hub's rails
   runner here) -> assign_frames command -> Altair links them manually,
   releases them and resolves PROJECT_UNRESOLVED
3. a night master with a rendered preview reaches the Hub's project page
4. the worker's session_end becomes night_ready; Altair closes the night
5. reconcile is clean

    cd processing && uv run python ../tools/e2e/altair_hub_e2e.py --hub http://localhost:3055 \
        --hub-dir ../hub --node altair-proc-01 --telescope backyard-16in --train backyard-16in
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from astropy.io import fits

from altair.catalog.db import Catalog
from altair.config import AltairConfig
from altair.hub import reporters
from altair.hub.client import HubClient
from altair.hub.previews import render
from altair.hub.reconcile import reconcile
from altair.hub.sync import HubSync
from altair.index.indexer import index


def rails(hub_dir: str, code: str) -> str:
    out = subprocess.run(["bin/rails", "runner", code], cwd=hub_dir, capture_output=True, text=True, check=True)
    return out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""


def fits_frame(path: Path, *, obj: str, ra: float, dec: float, filt: str, i: int, imagetyp: str = "LIGHT") -> None:
    header = fits.Header()
    header.update({"IMAGETYP": imagetyp, "OBJECT": obj, "FILTER": filt, "EXPTIME": 300.0, "DATE-OBS": f"2026-09-26T0{6 + i // 10}:{(i % 10) * 5:02d}:00",
                   "RA": ra, "DEC": dec, "TELESCOP": "e2e-scope", "INSTRUME": "e2e-cam", "GAIN": 100, "XBINNING": 1, "YBINNING": 1})
    path.parent.mkdir(parents=True, exist_ok=True)
    fits.writeto(path, np.random.default_rng(i).normal(1000, 10, (32, 48)).astype(np.float32), header)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub", required=True)
    ap.add_argument("--hub-dir", required=True)
    ap.add_argument("--node", default="altair-proc-01")
    ap.add_argument("--telescope", required=True)
    ap.add_argument("--train", required=True)
    args = ap.parse_args()
    ok = True

    def check(label: str, passed: bool, detail: object = "") -> None:
        nonlocal ok
        ok &= bool(passed)
        print(f"[{'ok' if passed else 'FAIL'}] {label} {detail}")

    info = json.loads(rails(args.hub_dir, f"""
      t = Telescope.find_by!(slug: {args.telescope!r}); node = ProcessingNode.find_or_create_by!(name: {args.node!r})
      node.telescopes << t unless node.telescopes.include?(t)
      k = node.api_keys.new(name: "e2e #{{Time.now.to_i}}"); k.generate_token!; k.save!
      target = Target.not_draft.where(telescope: t).first
      print({{ key: k.plaintext_token, target: target.id, name: target.nina_name, ra: target.ra_deg.to_f, dec: target.dec_deg.to_f,
               timezone: t.timezone, admin: User.admin.first.id }}.to_json)"""))
    work = Path(tempfile.mkdtemp(prefix="altair-e2e-"))
    config = AltairConfig.model_validate({
        "site": {"latitude": 37.3, "longitude": -121.9, "timezone": info["timezone"]}, "paths": {"state": str(work / "state")},
        "rigs": {"e2e": {"hub": {"telescope": args.telescope, "optical_train": args.train}, "telescope": "e2e-scope", "camera": "e2e-cam", "focal_length_mm": 550}},
        "hub": {"enabled": True, "base_url": args.hub, "node": args.node},
    })
    catalog = Catalog(config.catalog_path)
    client = HubClient(args.hub, info["key"])
    sync = HubSync(catalog, config, client)
    hub_config = sync.pull_config()
    check("config pulled", info["target"] in hub_config.targets, f"{len(hub_config.targets)} targets")

    for i in range(6):
        fits_frame(work / "archive" / f"L{i}.fits", obj=info["name"], ra=info["ra"], dec=info["dec"], filt="Ha", i=i)
    for i in range(6, 9):
        fits_frame(work / "archive" / f"X{i}.fits", obj="E2E mystery", ra=(info["ra"] + 120) % 360, dec=0.0, filt="Ha", i=i)
    report = index(catalog, config, work / "archive", rig="e2e", hub_config=hub_config)
    check("indexed", report.indexed == 9 and report.linked == 6 and report.unlinked == 3, report)
    sync.sync_now()
    counts = json.loads(rails(args.hub_dir, f"""print({{ linked: Frame.where(target_id: {info['target']}, origin: 'import').count,
      unlinked: Frame.unassigned.where(object_header: 'E2E mystery').count }}.to_json)"""))
    check("frames in the Hub", counts["linked"] >= 6 and counts["unlinked"] == 3, counts)

    rails(args.hub_dir, f"""Frames::Assigner.assign!(Frame.unassigned.where(object_header: 'E2E mystery'),
      target: Target.find({info['target']}), user: User.find({info['admin']}))""")
    sync.sync_now()
    rows = catalog.query("SELECT status, hub_target_id, assignment_source FROM frames WHERE target = 'E2E mystery'")
    check("assignment reached Altair", {(r["status"], r["hub_target_id"], r["assignment_source"]) for r in rows} == {("valid", info["target"], "manual")})
    check("PROJECT_UNRESOLVED resolved", catalog.one("SELECT status FROM issues WHERE kind = 'PROJECT_UNRESOLVED'")["status"] == "resolved")
    hub_issue = rails(args.hub_dir, "print ProcessingIssue.where(kind: 'PROJECT_UNRESOLVED').order(:updated_at).last&.status")
    check("... and in the Hub", hub_issue == "resolved", hub_issue)

    master = work / "master.fits"
    fits.writeto(master, np.random.default_rng(99).normal(1000, 10, (3, 300, 400)).astype(np.float32))
    preview, thumb = render(master, work / "p.jpg", work / "t.jpg")
    shas = [r["sha256"] for r in catalog.query("SELECT sha256 FROM frames WHERE image_type = 'light'")]
    with catalog.transaction() as tx:
        reporters.enqueue_data_product(tx, "night_master", 9001, {
            "target_id": info["target"], "night": "2026-09-25", "filter": "Ha", "sha256": "e" * 64, "size_bytes": 1, "archive_uri": "s3://e2e/nm.xisf",
            "metrics": {"frames": len(shas), "frame_sha256s": shas}}, preview=str(preview), thumbnail=str(thumb))
    sync.sync_now()
    attached = rails(args.hub_dir, "print DataProduct.find_by(altair_id: 9001)&.preview&.attached?")
    check("night master with preview in the Hub", attached == "true", attached)

    rails(args.hub_dir, f"""t = Telescope.find_by!(slug: {args.telescope!r}); Processing::CommandIssuer.issue!(kind: 'night_ready', telescope: t,
      payload: {{ optical_train: {args.train!r}, night: '2026-09-25', at: '2026-09-26T12:40:00Z', closed_by: 'session_end' }})""")
    sync.sync_now()
    night = catalog.one("SELECT state, closed_by FROM collections WHERE rig = 'e2e' AND night = '2026-09-25'")
    check("night_ready closed the night", night and night["state"] == "closed", dict(night) if night else None)
    sync.sync_now()
    hub_night = rails(args.hub_dir, f"print ObservingNight.find_by(optical_train: OpticalTrain.find_by(key: {args.train!r}), night: '2026-09-25')&.state")
    check("... and the Hub knows", hub_night == "closed", hub_night)
    results = reconcile(catalog, config, client)
    check("reconcile clean", results and all(r["ok"] for r in results), results)
    check("outbox empty", sync.drainer.depth() == 0 and sync.drainer.parked() == 0)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
