# Installing `robs` on a rig control PC

`robs` is the rig agent. It runs on the Windows PC that runs NINA. Install one copy per
PC; each telescope on that PC gets its own config file. For what the commands do, see
[`../README.md`](../README.md).

## 1. Before you start

On the rig PC:

- NINA with the **Target Scheduler** plugin installed. Open it once so that
  `schedulerdb.sqlite` exists (by default
  `%LOCALAPPDATA%\NINA\SchedulerPlugin\schedulerdb.sqlite`).
- Target Scheduler at its default **DEBUG** log level. `robs target-start` reads the
  plugin's `Logs\TS-*.log`, and those log lines are only written at DEBUG.
- The PC clock set to the site's time zone. Target Scheduler's log times are local time.
- NINA's image file pattern set as described in Altair's SPEC §4.2, saving into the folder
  Altair has as this rig's `raw_root`. That folder is `subs_dir` below.

In the Hub:

- The telescope exists, with its site and time zone.
- A telescope API key from the telescope's page under **Admin → Telescopes**. Copy it when
  it is shown; the Hub shows it only once.

## 2. Get the executable

Download the release assets from the repository's GitHub Releases page (tag
`rig-agent-vX.Y.Z`; [`../../docs/RELEASING.md`](../../docs/RELEASING.md) explains how a
release is made):

- `robs-X.Y.Z-windows-x64.exe`
- `example.telescope.yml`
- `SHA256SUMS`

Check the download in PowerShell, and compare the result with the line in `SHA256SUMS`:

```powershell
Get-FileHash .\robs-0.3.0-windows-x64.exe -Algorithm SHA256
```

Make a folder for it and rename the executable to `robs.exe`, so that NINA and Task
Scheduler keep the same path when you upgrade:

```
C:\Observatory\robs\
    robs.exe
    config\
        backyard-16in.yml
        .env
    logs\
```

`robs.exe` is a single file. It needs no Python or other software installed.

> **Building it yourself.** If there is no release yet, build the executable on any
> Windows machine with the repository checked out and [uv](https://docs.astral.sh/uv/)
> installed:
>
> ```bash
> cd rig-agent
> uv sync --locked
> uv run --no-sync --with pyinstaller pyinstaller --noconfirm packaging/robs.spec
> ```
>
> The executable is `rig-agent\dist\robs.exe`.

## 3. Configure each telescope

Copy `example.telescope.yml` to `config\<telescope-slug>.yml` and fill it in:

| Field | Value |
|---|---|
| `slug` | The telescope's slug in the Hub. |
| `api_base_url` | The Hub's URL, for example `https://observatory.example.org`. |
| `api_key` | Leave this out; put the key in `.env` instead (below). |
| `scheduler_db_path` | Full path to `schedulerdb.sqlite`. Use forward slashes or quote the path. |
| `nina_profile_id` | GUID of the NINA profile for this telescope (NINA → Options → General → Profile). |
| `subs_dir` | The folder NINA saves frames to, which is also Altair's `raw_root` for this rig. |
| `timezone` | IANA time zone, for example `America/Los_Angeles`. Must match the Hub telescope. |
| `ts_project_mode` | `per_hub_project` (default). Use `single` if `check-schema` reports that the per-project columns are missing. |

Put the API key in `config\.env`, next to the YAML file. The variable name is the slug in
upper case, with `-` replaced by `_`:

```
ROBS_BACKYARD_16IN_API_KEY=paste-the-key-here
```

Any other field can be set the same way (`ROBS_<SLUG>_<FIELD>`). A real environment
variable takes precedence over `.env`.

## 4. Check the installation

```powershell
cd C:\Observatory\robs
.\robs.exe check-config --config config\backyard-16in.yml
.\robs.exe check-schema --config config\backyard-16in.yml
```

`check-config` checks that the Hub can be reached and accepts the key, that the time zone
matches the Hub, that the folders exist, that the NINA profile exists, and that its filter
wheel has every filter the exposure plans use. `check-schema` checks that the installed
Target Scheduler version has the database layout `robs` expects. Fix anything they report
before the first night. Run `check-schema` again after every Target Scheduler upgrade.

Add `--verbose` before the command name (`robs.exe --verbose check-config …`) for
debug output.

## 5. Wrapper scripts

Use one small `.cmd` file per command. NINA and Task Scheduler then only need a path, and
`robs` output is saved to a log file (`robs` logs to the console, so output is lost
otherwise). Save these in `C:\Observatory\robs\` and change the slug:

`roof-open.cmd`
```bat
@echo off
cd /d C:\Observatory\robs
robs.exe roof-open --config config\backyard-16in.yml >> logs\robs.log 2>&1
```

Create `target-start.cmd`, `sync-progress.cmd` and `end-of-night.cmd` the same way,
changing `roof-open` to the command name.

The scripts exit with `robs`'s exit code, so NINA can see when a command failed. If
`roof-open` fails, Target Scheduler images the targets it already has.

## 6. Connect NINA

In NINA's Advanced Sequencer, add **External Script** instructions:

| Where | Script |
|---|---|
| After the roof opens, before the Target Scheduler Container | `C:\Observatory\robs\roof-open.cmd` |
| Target Scheduler Container → **Before New Target** | `C:\Observatory\robs\target-start.cmd` |
| End of the sequence, after the roof closes | `C:\Observatory\robs\end-of-night.cmd` |

`end-of-night` is the only end-of-sequence step needed. It runs a final progress sync,
reports the end of the session to the Hub (which tells Altair the night is over), and
then runs cleanup.

## 7. Schedule progress syncs

Run `sync-progress` every 15–30 minutes so the Hub shows progress during the night. In an
elevated PowerShell:

```powershell
schtasks /Create /TN "robs sync-progress backyard-16in" `
  /TR "C:\Observatory\robs\sync-progress.cmd" `
  /SC MINUTE /MO 20 /RU "$env:USERNAME" /RL LIMITED
```

Run it as the same Windows user that runs NINA, so that it can read that user's
`%LOCALAPPDATA%`. It does no harm to run it during the day.

## 8. First night

- In the Hub, check that the telescope's worker heartbeat is shown on the admin page after
  `check-config` or the first scheduled sync.
- After `roof-open`, Target Scheduler should have projects named `#P<id> <name>` and
  targets named `#<id> <name>`.
- Check that NINA writes `#<id> <name>` into the `OBJECT` header of saved frames. Altair
  uses this to link frames to Hub targets.
- `roof-open` saves a backup of the database to `robs_backups\` next to
  `schedulerdb.sqlite` and keeps the newest 14. To restore one, close NINA and copy the
  backup over `schedulerdb.sqlite`.

## Upgrading

Download the new release, check its hash, and replace `robs.exe` while NINA is not running
a sequence. The config, `.env` and wrapper scripts stay as they are. Then run
`check-config` and `check-schema` again.

## Several telescopes on one PC

Add a YAML file per telescope to `config\`, add its key to the same `.env`, and create a
set of wrapper scripts and a scheduled task for each telescope.
