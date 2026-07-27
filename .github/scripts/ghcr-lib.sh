#!/usr/bin/env bash
# Shared helpers for the GHCR cleanup scripts. Sourced, not run directly.
#
# GHCR has no API to delete a single tag — only a whole version (one digest,
# with all the tags pointing at it). Both callers therefore check every tag on a
# version before removing it.

OWNER="${GITHUB_REPOSITORY_OWNER}"
# The registry lowercases the image path, so the package name is too.
PACKAGE="$(printf '%s' "${GITHUB_REPOSITORY#*/}" | tr '[:upper:]' '[:lower:]')"

list_versions() {
  local out
  # --slurp cannot be combined with --jq, so flatten the pages afterwards.
  if ! out="$(gh api --paginate --slurp \
      "/users/${OWNER}/packages/container/${PACKAGE}/versions?per_page=100" 2>&1)"; then
    echo "error: cannot list versions of ${OWNER}/${PACKAGE}" >&2
    echo "  ${out}" >&2
    echo "  If this is a permission error, add a classic PAT with" >&2
    echo "  read:packages + delete:packages as the GHCR_CLEANUP_TOKEN secret." >&2
    return 1
  fi
  printf '%s' "$out" | jq 'flatten(1)'
}

delete_version() {
  local id=$1 label=$2 out
  if out="$(gh api --silent -X DELETE \
      "/users/${OWNER}/packages/container/${PACKAGE}/versions/${id}" 2>&1)"; then
    echo "deleted ${label} (version ${id})"
  else
    echo "warning: could not delete ${label} (version ${id}): ${out}"
  fi
}
