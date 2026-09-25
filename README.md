# altair-observatory-system

The software for a shared remote observatory: members plan imaging projects in one web app,
the observatory's rigs capture them with NINA, and the frames are collected, archived and
processed into masters automatically, all tracked on one central database.

One repository, three components that each build, test, release and run on their own and
talk to each other **only through the Hub's HTTP API**:

| Component | What it is | Runs on | Read |
|---|---|---|---|
| [`hub/`](hub/) | **The Hub.** Rails 8 app: users, telescopes, projects, targets, exposure plans, the catalogue, all UI, all APIs. The system of record (PostgreSQL). | A server (Kamal) | [`hub/README.md`](hub/README.md) |
| [`rig-agent/`](rig-agent/) | **Rig agent** (`robs`). Syncs Hub targets into NINA's Target Scheduler on each rig PC and reports acquisition progress and session events. | Each rig PC | [`rig-agent/README.md`](rig-agent/README.md) |
| [`processing/`](processing/) | **Processing core** (Altair). Collects frames from the rigs onto the NAS, backs them up to S3, and calibrates, integrates and merges them with PixInsight, in the context of Hub projects. Hub sync, catalog and indexing are implemented; the PixInsight pipeline is in progress. | The processing PC | [`processing/docs/SPEC.md`](processing/docs/SPEC.md) |
| [`contracts/`](contracts/) | **The API contract**: JSON Schemas, examples, and the generated `observatory-contracts` Python models. The only thing the components share. | — | [`contracts/README.md`](contracts/README.md) |

The design, including what changes in each component and the phased plan, is
[`docs/SYSTEM_ARCHITECTURE.md`](docs/SYSTEM_ARCHITECTURE.md).

## History

This repository merged three repositories with their full histories (`git subtree add`,
not squashed): `remote-observatory-queueing-system` → `hub/`, `remote-observatory-worker`
→ `rig-agent/`, and `altair-pre-processor` → `processing/`. The old repositories are
archived. `astrophotography-database` was not merged in: its features move into the Hub
and `altair index`, and it is retired (docs/SYSTEM_ARCHITECTURE.md §8.4).

To see a file's history from before the merge, follow the merge commit's second parent:

```bash
git log --oneline -- rig-agent/src/robs/sync.py        # shows the merge commit, say b413a6e
git log --oneline b413a6e^2 -- src/robs/sync.py        # history in the old repository
```

## CI and releases

Each component has its own path-filtered workflow in `.github/workflows/` (`hub.yml`,
`rig-agent.yml`, `processing.yml`, `contracts.yml`). A change under `contracts/` runs all of
them. Components are released on their own, with tags `hub-vX.Y.Z`, `rig-agent-vX.Y.Z` and
`processing-vX.Y.Z`: the Hub runs in the cloud while rig PCs and the processing PC upgrade on
their own schedule, so versions interoperate through the contract's `api_revision`
([`contracts/CHANGELOG.md`](contracts/CHANGELOG.md)).
