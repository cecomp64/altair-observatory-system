# Releasing

Each component is released on its own, with its own version and tag prefix.

| Component | Tag | Version lives in | What the release builds |
|---|---|---|---|
| Rig agent | `rig-agent-vX.Y.Z` | `rig-agent/pyproject.toml` | `robs-X.Y.Z-windows-x64.exe`, `example.telescope.yml`, `SHA256SUMS`, attached to the GitHub Release ([`release-rig-agent.yml`](../.github/workflows/release-rig-agent.yml)) |
| Processing (Altair) | `processing-vX.Y.Z` | `processing/pyproject.toml` | `altair-X.Y.Z-windows-x64.exe`, `SHA256SUMS`, attached to the GitHub Release ([`release-processing.yml`](../.github/workflows/release-processing.yml)) |
| Hub | `hub-vX.Y.Z` | the tag | The Docker image `ghcr.io/<owner>/altair-hub:X.Y.Z` ([`release-hub.yml`](../.github/workflows/release-hub.yml)) |

Pushing a tag starts that component's release workflow. Each workflow runs the
component's CI first, checks that the tag matches the version, builds, and publishes.

## Releasing the rig agent or Altair

The steps below use the rig agent as the example. For Altair, use `processing/` and the
`processing-v` prefix instead.

**Shortcut:** `tools/release.sh rig-agent|processing [VERSION]` does steps 1 and 3 for
you. It bumps the version, runs `uv lock`, lint-imports and pytest, commits only
`pyproject.toml` and `uv.lock`, then asks before it pushes `main` and the tag. In VS Code,
run it with **Tasks: Run Task → Release: rig agent and/or Altair**. **Release: test
build (no release)** does step 2.

### 1. Bump the version

On `main`, or on a branch that you merge first:

```bash
cd rig-agent
# edit pyproject.toml: version = "0.4.0"
uv lock                     # the lockfile records the version; CI runs `uv sync --locked`
uv run pytest
cd ..
git commit -am "rig-agent 0.4.0"
git push
```

The tag must match this version exactly. If it doesn't, the build fails and no release is
made.

### 2. Optional: do a test build first

Go to **Actions → release-rig-agent → Run workflow** and pick the branch. This runs the
same checks and build but creates no release. Download `robs.exe` from the run's
**Artifacts** section and try it on a rig PC.

With the GitHub CLI:

```bash
gh workflow run release-rig-agent.yml --ref main
gh run watch
gh run download --name rig-agent-0.4.0
```

### 3. Tag the release and push the tag

Tag the commit that has the new version and push the tag:

```bash
git tag rig-agent-v0.4.0          # on the bumped commit, usually main
git push origin rig-agent-v0.4.0
```

For a release candidate, tag `rig-agent-v0.4.0-rc1`. A version with a suffix is
published as a pre-release.

Push tags one at a time. GitHub doesn't start workflows when more than three tags are
pushed at once, so `git push --tags` can skip the build.

### 4. Wait for the build

The tag starts **release-rig-agent** under **Actions** (`gh run watch`). It:

1. runs the component's CI (`rig-agent.yml`: lint-imports and pytest on Linux and Windows);
2. checks that the tag matches `pyproject.toml`;
3. builds `robs.exe` with PyInstaller on Windows and checks that `robs.exe --help` runs;
4. creates the GitHub Release `rig agent 0.4.0` for the tag if it doesn't exist yet;
5. uploads the executable, `example.telescope.yml` and `SHA256SUMS` to the release, and
   adds short install notes if the release has none.

The files appear under the release's **Assets**.

### 5. Write the release notes

Edit the release on GitHub (**Releases → rig agent 0.4.0 → Edit**) or with the CLI:

```bash
gh release edit rig-agent-v0.4.0 --notes-file notes.md
```

If you create the release yourself before the build finishes, the workflow keeps your
title and notes and only adds the files.

All components share one releases list, and GitHub marks the newest release as
**Latest**. To mark a different release as Latest, edit that release and tick **Set as the
latest release**.

### If the build fails

No release is created, or the existing one is left without the executable. Fix the
problem, then either:

- **Rebuild the same commit** (for example after a flaky test): open the failed run and
  click **Re-run all jobs**.
- **Rebuild from a new commit:** move the tag to the fixed commit and push it again.
  Delete the release first if the workflow already created it.

  ```bash
  gh release delete rig-agent-v0.4.0 --yes        # only if it exists
  git tag -f rig-agent-v0.4.0
  git push -f origin rig-agent-v0.4.0
  ```

A rebuild replaces the files already attached (`--clobber`).

## Releasing the Hub

The same way: push a `hub-vX.Y.Z` tag.

```bash
git tag hub-v1.2.0
git push origin hub-v1.2.0
```

The workflow builds the image, pushes it to the GitHub Container Registry and creates the
GitHub Release. Deploying is a separate step: run `kamal deploy` from `hub/`.

## Required repository settings

- **Settings → Actions → General → Workflow permissions**: the workflows ask for
  `contents: write` themselves. If your organisation restricts the default token, allow
  read and write permissions.
- No secrets are needed. The executables are uploaded with the workflow's own
  `GITHUB_TOKEN`.
