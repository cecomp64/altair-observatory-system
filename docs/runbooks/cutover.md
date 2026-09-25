# Cutover runbook: from separate tools to the unified system

The concrete steps behind [SYSTEM_ARCHITECTURE.md §10](../SYSTEM_ARCHITECTURE.md#10-migration--cutover-runbook).
Work top to bottom; every step can be repeated safely. Steps 1–7 can be done in an
afternoon; step 8 is a week of watching; step 9 is the clean-up afterwards.

**Before you start**

- [ ] `hub/` deploys from the `altair-observatory-system` repository (`cd hub && kamal deploy`).
- [ ] A database backup of the current Hub (the old queueing system's Postgres).
- [ ] The NINA Target Scheduler database on each rig PC backed up (`schedulerdb.sqlite`).

## 1. Deploy the Hub with P1–P3

```bash
cd hub
kamal deploy                         # runs db:migrate (migrations 1–10) on boot
kamal app exec 'bin/rails "catalogue:import[all]"'   # OpenNGC + LDN + LBN (a few minutes)
```

What changes for people: every existing target now sits in its own project (same name,
same owner). Existing workers keep working unchanged: the API is additive, and their
keys keep every scope they used (`files:write` included). Check:

- [ ] `/projects` lists one project per old target.
- [ ] The worker's next `roof-open` still succeeds (its request log, or the key's "last used").
- [ ] Admin → Catalogue shows the imported counts.

## 2. Equipment in the Hub

For each observatory telescope, in **Admin → Telescopes**:

- [ ] Set the **timezone** (nights are local noon to noon in it) and the minimum altitude.
- [ ] Upload the horizon file if there isn't one.
- [ ] Create one **optical train per Altair rig**. The train's **key must equal the rig
      name** in `altair.yaml` (e.g. `esprit100_2600mm`). Fill in focal length, pixel size and
      sensor size: trains without optics aren't sent to Altair.
- [ ] List the train's **filters**, with the raw `FILTER` header values as aliases
      (`Ha: H-alpha, HA`). The project wizard offers exactly these filters.
- [ ] Set the header aliases (`TELESCOP`, `INSTRUME` values).
- [ ] Pick the telescope's default optical train (the one existing targets use).

## 3. Processing node

- [ ] **Admin → Processing nodes → New node**: name it as `hub.node` will be in
      `altair.yaml` (e.g. `altair-proc-01`), and tick the telescopes it serves.
- [ ] **Create API key** on the node page; copy the token once.
- [ ] On the processing PC, store it in Windows Credential Manager:

  ```powershell
  cmdkey /generic:altair-hub /user:altair-proc-01 /pass:<token>
  ```

  (`ALTAIR_HUB_API_KEY` works too, for testing.)

## 4. Turn on Altair's Hub sync

In `altair.yaml` (SPEC §5.1):

```yaml
hub:
  enabled: true
  base_url: "https://hub.example.org"
  node: "altair-proc-01"
  credential_target: "altair-hub"
rigs:
  esprit100_2600mm:
    hub: { telescope: "backyard-16in", optical_train: "esprit100_2600mm" }
```

```powershell
altair hub pull-config
altair doctor            # repeat until every line is [ok]
altair hub sync-now
```

- [ ] `altair doctor` is clean: Hub reachable, key scopes, node name, each rig matches its
      train (timezone, focal length, camera type), filters known.
- [ ] If Altair already has data: `altair hub reconcile` (every closed night), then check
      **Files → Unassigned inbox** in the Hub and assign what's there; Altair re-links it.
- [ ] Old archives from the observatory's rigs: `altair index <dir> --rig <rig>` (add
      `--adopt` to copy them into the NAS layout so they can be processed).
- [ ] The node page shows a heartbeat, outbox depth 0 and no parked items.

## 5. Upgrade the workers

On each rig PC:

- [ ] Install the new `robs` (0.2.0+).
- [ ] In the telescope YAML: `data_pipeline: altair`, `timezone:` (the Hub telescope's), keep
      `ts_project_mode: per_hub_project`.
- [ ] `robs check-schema --config …` and `robs check-config --config …` are clean. If
      check-schema says the per-project columns are missing, the worker uses one managed
      project instead; that works, it just loses per-project priority.
- [ ] NINA: keep **one** end-of-sequence External Script, `robs end-of-night --config …`,
      and **remove `altair-session-end.cmd`** from the rig PC. The session end now travels
      through the Hub (`session_end` → `night_ready`).
- [ ] NINA's image file pattern is Altair's recommended one (SPEC §4.2), and `subs_dir` is
      the folder Altair collects from as this rig's `raw_root`.

On the first `roof-open`, Target Scheduler shows `#P<id> …` projects with `#<id> …`
targets. Targets from the old "Remote Observatory Queue" project move over with their
accepted counts, and that project is disabled once empty.

## 6. Legacy uploads

Nothing to do: old worker uploads (`sub`, `stacked`, `preview`) stay visible on target
pages as legacy files. The old worker bucket stays as it is. To bring those subs into the
archive, index a local copy with `altair index <dir> --rig <rig> --adopt`.

## 7. Import astrophotography-database

For each member who used the desktop app, with their `astrophotography.db` (and its
`showcases/` folder next to it):

```bash
bin/rails "import:astrodb[/path/astrophotography.db,member@example.org,telescope=backyard-16in]"
```

Projects arrive with **draft** targets on that telescope, one exposure plan per goal filter;
the member reviews and submits them. Without `telescope=…` the projects keep only their
objects and goals (in the description). Warnings list goal filters the optical train
doesn't have. Images aren't imported: index the observatory's archives with `altair index`.

## 8. Run both for a week

Every morning:

- [ ] Dashboard: "imaging now" appeared during the night; node health green.
- [ ] Each night's frames are under the right projects, and the unassigned inbox is empty
      (or explained).
- [ ] `altair hub reconcile` reports every night ok.
- [ ] Spot-check counters on one project: acquired (Target Scheduler), collected (Altair),
      integrated (masters).
- [ ] No parked outbox items (`altair hub outbox list --parked`), no `HUB_*` issues.

## 9. Remove the legacy paths (after a clean week)

Only when every worker has run `data_pipeline: altair` for a week without trouble:

- [ ] Remove from `rig-agent/`: `stacking/`, `s3_publisher.py`, the `legacy` branch of
      `end_of_night.py` and its config fields, and the boto3 dependency (release `rig-agent-v1.0.0`).
- [ ] Hub: drop `files:write` from worker keys that no longer need it (Admin → telescope →
      API keys; new keys already get it only if asked).
- [ ] astrophotography-database: cut its final release (the README banner is in place),
      disable its release and PWA workflows, and archive the repository on GitHub.
- [ ] Archive `remote-observatory-queueing-system`, `remote-observatory-worker` and
      `altair-pre-processor` (their READMEs already point here).

## Rolling back

- Workers: set `data_pipeline: legacy` again and put `altair-session-end.cmd` back in NINA.
  Nothing in the Hub needs undoing.
- Altair: `hub.enabled: false`; it goes back to standalone (SPEC v0.7 behaviour).
- Hub: the migrations are reversible (`bin/rails db:rollback STEP=10`), but restoring the
  pre-cutover database backup is simpler if it's the same day.
