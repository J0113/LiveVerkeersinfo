#!/usr/bin/env bash
# Runs on branch deletion: removes the image published under that branch name.
set -euo pipefail

# shellcheck source=/dev/null
source "$(dirname "$0")/ghcr-lib.sh"

# Same sanitising docker/metadata-action applies when it builds the tag.
tag="$(printf '%s' "$BRANCH" | sed 's|[^a-zA-Z0-9._-]|-|g')"
echo "branch ${BRANCH} deleted -> looking for image tag ${tag}"

# Only delete when this version holds nothing but that branch tag (plus sha-
# tags). An identical build shares its digest — and therefore its version —
# with main, and deleting it would take latest down with it.
id="$(jq -r --arg tag "$tag" '
  [ .[]
    | select((.metadata.container.tags // []) | index($tag))
    | select((.metadata.container.tags // []) | all(. == $tag or startswith("sha-")))
    | .id ] | .[0] // empty' <<<"$(list_versions)")"

if [ -z "$id" ]; then
  echo "nothing to delete (no such tag, or it shares a digest with another tag)"
  exit 0
fi

delete_version "$id" "$tag"
