#!/usr/bin/env python3
"""End-to-end check of the rig agent against a running Hub
(docs/SYSTEM_ARCHITECTURE.md §9 P5), with a throwaway Target Scheduler
database instead of NINA:

1. robs roof-open: per-project Target Scheduler projects ("#P<id> ..."),
   targets named "#<id> ...", schedule_count; the Hub sees roof_open
2. robs sync-progress: accepted counts reach the Hub's plans
3. robs end-of-night (data_pipeline: altair): no uploads; session_end makes
   the Hub queue night_ready for the node serving the telescope

    cd rig-agent && uv run python ../tools/e2e/worker_hub_e2e.py --hub http://localhost:3055 --hub-dir ../hub --telescope backyard-16in
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

TS_SCHEMA = """
CREATE TABLE project (Id INTEGER PRIMARY KEY AUTOINCREMENT, profileId TEXT NOT NULL, name TEXT NOT NULL, description TEXT,
                      state INTEGER, priority INTEGER, minimumAltitude REAL, createDate INTEGER);
CREATE TABLE target (Id INTEGER PRIMARY KEY AUTOINCREMENT, projectId INTEGER NOT NULL, name TEXT NOT NULL, ra REAL, dec REAL, enabled INTEGER);
CREATE TABLE exposuretemplate (Id INTEGER PRIMARY KEY AUTOINCREMENT, profileId TEXT NOT NULL, name TEXT, filterName TEXT NOT NULL, defaultExposure INTEGER);
CREATE TABLE exposureplan (Id INTEGER PRIMARY KEY AUTOINCREMENT, targetId INTEGER NOT NULL, exposureTemplateId INTEGER NOT NULL, desired INTEGER, accepted INTEGER);
"""


def rails(hub_dir: str, code: str) -> str:
    out = subprocess.run(["bin/rails", "runner", code], cwd=hub_dir, capture_output=True, text=True, check=True)
    return out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""


def robs(*args: str) -> str:
    out = subprocess.run(["robs", *args], capture_output=True, text=True)
    if out.returncode:
        raise SystemExit(f"robs {' '.join(args)} failed:\n{out.stdout}\n{out.stderr}")
    return out.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub", required=True)
    ap.add_argument("--hub-dir", required=True)
    ap.add_argument("--telescope", required=True)
    args = ap.parse_args()
    ok = True

    def check(label: str, passed: bool, detail: object = "") -> None:
        nonlocal ok
        ok &= bool(passed)
        print(f"[{'ok' if passed else 'FAIL'}] {label} {detail}")

    info = json.loads(rails(args.hub_dir, f"""
      t = Telescope.find_by!(slug: {args.telescope!r})
      k = t.api_keys.new(name: "e2e worker #{{Time.now.to_i}}"); k.generate_token!; k.save!
      node = ProcessingNode.find_or_create_by!(name: "altair-proc-01"); node.telescopes << t unless node.telescopes.include?(t)
      target = t.targets.schedulable.first or abort "no schedulable target on the telescope"
      print({{ key: k.plaintext_token, target: target.id, nina: target.nina_name, project: target.project.ts_project_name,
               plan: target.exposure_plans.first.id, tz: t.timezone, node: node.id,
               before: node.processing_commands.where(kind: "night_ready").count }}.to_json)"""))

    work = Path(tempfile.mkdtemp(prefix="robs-e2e-"))
    db = work / "schedulerdb.sqlite"
    sqlite3.connect(db).executescript(TS_SCHEMA)
    (work / "subs").mkdir()
    config = work / "telescope.yml"
    config.write_text(yaml.safe_dump({
        "slug": args.telescope, "api_base_url": args.hub, "api_key": info["key"], "scheduler_db_path": str(db),
        "subs_dir": str(work / "subs"), "nina_profile_id": "11111111-1111-1111-1111-111111111111",
        "data_pipeline": "altair", "timezone": info["tz"],
    }))

    print(robs("roof-open", "--config", str(config)))
    conn = sqlite3.connect(db)
    projects = [r[0] for r in conn.execute("SELECT name FROM project")]
    targets = [r[0] for r in conn.execute("SELECT name FROM target")]
    check("Target Scheduler project per Hub project", info["project"] in projects, projects)
    check("target carries the Hub's NINA name", info["nina"] in targets, targets)
    roof = rails(args.hub_dir, f"print ObservingNight.where(telescope: Telescope.find_by!(slug: {args.telescope!r})).where.not(roof_open_at: nil).exists?")
    check("Hub saw roof_open", roof == "true")

    conn.execute("UPDATE exposureplan SET accepted = 7")
    conn.commit()
    print(robs("sync-progress", "--config", str(config)))
    completed = rails(args.hub_dir, f"print ExposurePlan.find({info['plan']}).completed_count")
    check("accepted counts reached the Hub", completed == "7", completed)

    print(robs("end-of-night", "--config", str(config)))
    after = int(rails(args.hub_dir, f"print ProcessingNode.find({info['node']}).processing_commands.where(kind: 'night_ready').count"))
    check("session_end became night_ready for the node", after > info["before"], f"{info['before']} -> {after}")
    check("no files uploaded", rails(args.hub_dir, "print DataProduct.legacy.where('created_at > ?', 5.minutes.ago).count") == "0")
    heartbeat = rails(args.hub_dir, f"print Telescope.find_by!(slug: {args.telescope!r}).worker_last_heartbeat_at.present?")
    check("worker heartbeat recorded", heartbeat == "true")
    print(robs("check-config", "--config", str(config)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
