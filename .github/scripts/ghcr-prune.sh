#!/usr/bin/env bash
# Runs after every successful build: drops untagged leftovers and keeps only the
# newest $KEEP_SHA_VERSIONS sha-<commit> images.
set -euo pipefail

# shellcheck source=/dev/null
source "$(dirname "$0")/ghcr-lib.sh"

keep="${KEEP_SHA_VERSIONS:-10}"
versions="$(list_versions)"

# Untagged versions are what an overwritten tag (latest, a branch name) leaves
# behind — nothing references them.
while read -r id; do
  if [ -n "$id" ]; then
    delete_version "$id" "untagged"
  fi
done < <(jq -r '.[] | select((.metadata.container.tags // []) | length == 0) | .id' <<<"$versions")

# Versions carrying only sha- tags: keep the newest $keep, prune the rest. A
# version that also holds latest or a branch tag is left alone.
while read -r id tags; do
  if [ -n "$id" ]; then
    delete_version "$id" "$tags"
  fi
done < <(jq -r --argjson keep "$keep" '
  [ .[] | select((.metadata.container.tags // []) | (length > 0 and all(startswith("sha-")))) ]
  | sort_by(.created_at) | reverse | .[$keep:]
  | .[] | "\(.id) \(.metadata.container.tags | join(","))"' <<<"$versions")

echo "prune done"
