#!/usr/bin/env bash
# Shared helpers for the GHCR cleanup scripts. Sourced, not run directly.
#
# GHCR has no API to delete a single tag — only a whole version (one digest,
# with all the tags pointing at it). Both callers therefore check every tag on a
# version before removing it.

OWNER="${GITHUB_REPOSITORY_OWNER}"
PACKAGE="${GITHUB_REPOSITORY,,}"   # the registry lowercases the image path
PACKAGE="${PACKAGE#*/}"

list_versions() {
  gh api --paginate --slurp \
    "/users/${OWNER}/packages/container/${PACKAGE}/versions?per_page=100" \
    --jq 'flatten(1)' 2>/dev/null || echo '[]'
}

delete_version() {
  local id=$1 label=$2
  echo "deleting ${label} (version ${id})"
  gh api --silent -X DELETE \
    "/users/${OWNER}/packages/container/${PACKAGE}/versions/${id}" \
    || echo "  warning: could not delete version ${id}"
}
