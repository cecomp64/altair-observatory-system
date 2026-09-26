# Disaster recovery (SPEC §7.9, §7.10)

Everything Altair needs to carry on is on the NAS and in S3. The processing PC holds
only the catalog, which is backed up nightly, and caches.

## What is where

| Data | NAS | S3 |
|---|---|---|
| Raw lights | recent nights (until retention moves them S3-only) | all, from the night they were taken |
| Raw calibration subs | yes | no (their masters are kept instead) |
| Calibration masters, references, night masters, multi-night versions | all | all |
| Calibrated light subs (final nights) | recent | all |
| Manifests and sidecars (`*.json` next to the data) | all | all |
| Catalog backups (`catalog/altair-<utc>.db.zst`) | 30 daily, 12 monthly | same |

## The processing PC is lost

1. Install Altair and PixInsight on the new PC. Restore `altair.yaml` from your copy.
   Store the credentials in Windows Credential Manager:
   - the rig shares;
   - the Hub key (`altair-hub`);
   - the S3 profile.
2. Restore the catalog from the newest backup:
   ```
   altair storage restore-catalog --latest          # NAS first, then S3
   ```
3. `altair doctor`, then install the task:
   ```
   deploy\windows\install-task.ps1 -Exe "C:\Program Files\Altair\altair.exe"
   ```
   and log on. Frames that the rigs took in the meantime are still on the rigs. The
   collector picks them up on its first poll.

## Every catalog backup is lost

Rebuild the catalog from the archive. This reads only the manifests and sidecars,
never an image:

```
altair storage rebuild-catalog --from nas            # or --from s3, or --from both
altair rerun --night <date>                           # for nights not yet processed; issues are re-derived
```

The rebuild restores:
- blobs and replicas (recorded as `verify_method = rebuild`; the scrubber verifies
  them again over time);
- frames, with their Hub target links where a night master used them;
- closed nights, calibration masters, projects and references;
- night masters, with their calibrated subs, and multi-night history.

It does not restore:
- **issues**: re-planning derives them again;
- **Hub commands and the outbox**: the Hub reconciles
  (`altair hub reconcile`).

## The NAS is lost or replaced

1. Mount the new share at the same `storage.locations[nas].root`, then run
   `altair storage nas init`. The old identity is gone, so the NAS reads as
   `NAS_UNHEALTHY` until this runs. Collection, healing and all cleanup stay paused
   until then, so nothing is deleted anywhere meanwhile.
2. Repopulate what should live on the NAS:
   ```
   altair storage replicate --to nas --classes calibration_master,project_reference,night_master,multi_night_master
   ```
   Raw lights and calibrated subs come back on demand: the stager fetches them from
   S3 when a job needs them and heals the NAS copy. Raw calibration subs are not in
   S3; their masters are.

## The drill

`tests/test_daemon.py::test_a_night_through_altaird_then_a_catalog_rebuild` runs the
rebuild half of this on every CI run:
1. a night goes through `altaird`;
2. the catalog is dropped;
3. it is rebuilt from the NAS;
4. frames, masters, references, night masters and multi-night masters are compared.

Do the full drill on the real system once after installation (SPEC §15, exit
criterion), and again after major upgrades:
- restore the catalog on a spare PC;
- `altair storage locate` a few raw lights and masters;
- `altair storage fetch --night <old night> --dry-run`.
