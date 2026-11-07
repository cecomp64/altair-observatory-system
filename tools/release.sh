#!/usr/bin/env bash
# Release the rig agent or Altair, following docs/RELEASING.md:
#   1. bump the version in <component>/pyproject.toml, `uv lock`, run the tests,
#      commit and push;
#   2. tag <prefix>vX.Y.Z and push the tag, which starts the release workflow.
#
# Usage: tools/release.sh rig-agent|processing [VERSION]
# Without VERSION it shows the current one and asks. If pyproject.toml already has
# VERSION (for example after an earlier run stopped before tagging), the bump is skipped.
# Asks before pushing anything.
set -euo pipefail

die() { echo "error: $*" >&2; exit 1; }

component="${1:-}"
case "$component" in
  rig-agent)  prefix="rig-agent-v";  workflow="release-rig-agent.yml" ;;
  processing) prefix="processing-v"; workflow="release-processing.yml" ;;
  *) die "usage: $0 rig-agent|processing [VERSION]" ;;
esac

root="$(git rev-parse --show-toplevel)"
cd "$root"
pyproject="$component/pyproject.toml"
lockfile="$component/uv.lock"
current="$(sed -nE 's/^version = "(.*)"/\1/p' "$pyproject" | head -1)"
[ -n "$current" ] || die "no version found in $pyproject"

version="${2:-}"
if [ -z "$version" ]; then
  read -r -p "$component is at $current. New version (e.g. 0.4.0 or 0.4.0-rc1): " version
fi
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]] \
  || die "version must look like X.Y.Z or X.Y.Z-rc1, got '$version'"
tag="$prefix$version"

echo "==> Checking the repository"
branch="$(git rev-parse --abbrev-ref HEAD)"
[ "$branch" = "main" ] || die "on branch '$branch'; releases are tagged on main"
git fetch --quiet origin main --tags
[ "$(git rev-list --count HEAD..origin/main)" = "0" ] \
  || die "main is behind origin/main; pull first"
git rev-parse -q --verify "refs/tags/$tag" > /dev/null && die "tag $tag already exists locally"
git ls-remote --exit-code --tags origin "refs/tags/$tag" > /dev/null 2>&1 \
  && die "tag $tag already exists on origin"
git diff --quiet HEAD -- "$pyproject" "$lockfile" \
  || die "$pyproject or $lockfile has uncommitted changes"

if [ "$current" = "$version" ]; then
  echo "==> $pyproject is already at $version; skipping the bump"
  bumped=no
else
  echo "==> Bumping $component $current -> $version"
  # Only the first `version = ` line (the [project] one); works with BSD and GNU sed.
  sed -i.bak -E "1,/^version = \".*\"/s/^version = \".*\"/version = \"$version\"/" "$pyproject"
  rm -f "$pyproject.bak"
  [ "$(sed -nE 's/^version = "(.*)"/\1/p' "$pyproject" | head -1)" = "$version" ] \
    || die "could not set the version in $pyproject"

  (
    cd "$component"
    echo "==> uv lock"
    uv lock
    echo "==> Tests"
    uv sync --extra dev --quiet
    uv run lint-imports
    uv run pytest
  ) || {
    echo "Checks failed. Reverting $pyproject and $lockfile." >&2
    git checkout -- "$pyproject" "$lockfile"
    exit 1
  }

  # Commit only the version files, not whatever else is in the working tree.
  git commit --quiet -m "$component $version" -- "$pyproject" "$lockfile"
  echo "==> Committed: $(git log -1 --oneline)"
  bumped=yes
fi

echo
if [ "$bumped" = yes ]; then
  echo "About to push main and the tag $tag to origin."
else
  echo "About to tag $(git log -1 --format='%h %s') as $tag and push the tag to origin."
fi
read -r -p "Continue? [y/N] " answer
if [[ ! "$answer" =~ ^[Yy]$ ]]; then
  echo "Stopped. Nothing was pushed."
  [ "$bumped" = yes ] && echo "The version commit is still local; undo it with: git reset --hard HEAD~1  (or run this again to continue)"
  exit 1
fi

if [ "$bumped" = yes ] || [ "$(git rev-list --count origin/main..HEAD)" != "0" ]; then
  echo "==> git push origin main"
  git push origin main
fi

echo "==> Tagging and pushing $tag"
git tag "$tag"
git push origin "$tag"   # one tag per push: GitHub skips workflows for pushes of >3 tags

echo
echo "Done. The tag starts $workflow."
if command -v gh > /dev/null; then
  echo "  Watch it:      gh run watch \$(gh run list --workflow $workflow --limit 1 --json databaseId -q '.[0].databaseId')"
  echo "  Release notes: gh release edit $tag --notes-file notes.md"
fi
