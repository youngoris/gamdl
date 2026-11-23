#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
if [[ -n "$(git status --porcelain)" ]]; then
  echo 'Commit or stash local changes before syncing.' >&2
  exit 1
fi
origin_url="$(git config --get remote.origin.url)"
case "$origin_url" in
  https://github.com/*) fork_repo="${origin_url#https://github.com/}" ;;
  git@github.com:*) fork_repo="${origin_url#git@github.com:}" ;;
  *) echo 'The origin must be a GitHub fork.' >&2; exit 1 ;;
esac
fork_repo="${fork_repo%.git}"
fork_repo="$(gh repo view "$fork_repo" --json nameWithOwner --jq .nameWithOwner)"
if [[ "$fork_repo" == 'glomatico/gamdl' ]]; then
  echo 'Run this command from your fork.' >&2
  exit 1
fi
if git config --get remote.upstream.url >/dev/null 2>&1; then
  upstream_url="$(git config --get remote.upstream.url)"
  case "$upstream_url" in
    https://github.com/glomatico/gamdl.git|https://github.com/glomatico/gamdl|git@github.com:glomatico/gamdl.git) ;;
    *) echo 'The upstream remote does not point to glomatico/gamdl.' >&2; exit 1 ;;
  esac
else
  git remote add upstream https://github.com/glomatico/gamdl.git
fi
# No force updates: divergent upstream-tracking branches require review.
gh repo sync "$fork_repo" --source glomatico/gamdl --branch 4.0
git fetch upstream 4.0
git switch profiles
git merge --no-edit upstream/4.0
git push origin profiles
echo 'Synced upstream 4.0 and merged it into profiles.'
