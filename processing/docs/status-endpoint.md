# Status endpoint, metrics and logs (SPEC §12.2, §13)

## The local status endpoint

`altaird` can serve a read-only HTTP endpoint for Home Assistant, dashboards and
Prometheus. With a Hub, the Hub's pages cover the same ground; this is for local tools.

```yaml
http:
  enabled: true
  bind: 127.0.0.1          # another address needs a token
  port: 8765
  token_env: ALTAIR_HTTP_TOKEN   # the variable holding the token; send "Authorization: Bearer <token>" or ?token=
  metrics_file: null       # e.g. C:/ProgramData/node_exporter/textfile/altair.prom
```

| Path | Returns |
|---|---|
| `/` | links, and the most recent reports |
| `/status` | the `ALTAIR_STATUS.json` document |
| `/issues?open=1&kind=K` | issues |
| `/projects/<id>` | a project with its night masters, multi-night versions and report links |
| `/jobs?status=S&limit=N` | jobs, newest first |
| `/healthz` | 200 while every worker has run within 3× its interval, else 503 |
| `/metrics` | Prometheus text format |
| `/reports/<kind>/<id>` | a night or merge report as HTML (`.md` for the Markdown) |

### Home Assistant

```yaml
rest:
  - resource: http://altair-pc:8765/status
    headers: { Authorization: !secret altair_token }
    scan_interval: 120
    sensor:
      - name: Altair open issues
        value_template: "{{ value_json.issues.open }}"
      - name: Altair blocking issues
        value_template: "{{ value_json.issues.blocking }}"
      - name: Altair raw lights waiting for S3
        value_template: "{{ value_json.storage.raw_lights_without_s3 }}"
binary_sensor:
  - platform: rest
    name: Altair healthy
    resource: http://altair-pc:8765/healthz
    headers: { Authorization: !secret altair_token }
    value_template: "{{ value_json.ok }}"
```

### Metrics

Among them:
- `altair_jobs{status}`
- `altair_issues_open{kind,severity}`
- `altair_backup_pending_blobs{data_class}` and `altair_backup_pending_bytes{data_class}`
- `altair_raw_lights_without_s3`
- `altair_nas_usable`, `altair_nas_free_percent`
- `altair_cache_bytes`
- `altair_hub_outbox_pending`, `altair_hub_outbox_parked`
- `altair_job_last_duration_seconds{kind}`
- `altair_location_reachable{location}`
- `altair_worker_runs_total`, `altair_worker_errors_total` and
  `altair_worker_last_run_timestamp_seconds`, each labelled `{worker}`

Two alerts worth having:
- `altair_raw_lights_without_s3 > 0` for longer than `raw_backup_sla_hours`;
- `increase(altair_worker_errors_total[1h]) > 0`.

## Reports

Each night stack and merge writes a Markdown report:
- a night report at `<paths.published>/<target>/<night>/report_<filter>.md`;
- a merge report at `<paths.published>/<target>/multinight/<filter>_vNNN_report.md`,
  with a coverage map image.

With a Hub, the same report (without the local image) goes to the Hub with the data
product, and the Hub shows it on the master's report page. `altair publish --refresh`
rewrites every report.

## Logs

`altaird` writes JSON lines to `state/logs/altaird.jsonl`:
- it rotates the file at midnight UTC and keeps `logging.retention_days` days;
- every entry carries the worker, rig, night, project and job it concerns.

`altair logs` filters it:

```
altair logs --job 42
altair logs --night 2026-09-24 --level warning
altair logs --follow --worker processing
```

PixInsight's console output for each job stays in `state/logs/jobs/<job-id>.log`,
90 days.

To get plain text logs instead, set `logging.format: text` (`state/logs/altaird.log`).
