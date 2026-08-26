#!/usr/bin/env bash
# run_ci.sh — trigger the on-demand CI workflow on GitHub's runners and
# stream the result back, instead of running pytest locally.
#
# Usage:
#   ./scripts/run_ci.sh                        # full suite, current branch, Python 3.12
#   ./scripts/run_ci.sh tests/test_graph.py     # just one file
#   ./scripts/run_ci.sh -k "test_actor"         # marker/keyword expression
#   ./scripts/run_ci.sh --python 3.11 tests/    # pin a Python version
#   ./scripts/run_ci.sh --branch codex/btn-90-x tests/
#
# Requires: gh CLI authenticated (`gh auth login`), run from repo root or
# anywhere (uses --repo explicitly).

set -euo pipefail

REPO="lrburkholder/battalion"
WORKFLOW="ci-ondemand.yml"
PYTHON_VERSION="3.12"
BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
TEST_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch)
      BRANCH="$2"; shift 2 ;;
    --python)
      PYTHON_VERSION="$2"; shift 2 ;;
    *)
      TEST_ARGS+=("$1"); shift ;;
  esac
done

TEST_PATH="${TEST_ARGS[*]:-tests/}"

echo "== Triggering $WORKFLOW on $REPO@$BRANCH (Python $PYTHON_VERSION) =="
echo "   test_path: $TEST_PATH"

gh workflow run "$WORKFLOW" \
  --repo "$REPO" \
  --ref "$BRANCH" \
  -f test_path="$TEST_PATH" \
  -f python_version="$PYTHON_VERSION"

# Give GitHub a moment to register the dispatched run before we query for it.
sleep 3

RUN_ID=$(gh run list \
  --repo "$REPO" \
  --workflow "$WORKFLOW" \
  --branch "$BRANCH" \
  --limit 1 \
  --json databaseId \
  -q '.[0].databaseId')

if [[ -z "$RUN_ID" ]]; then
  echo "Could not locate the dispatched run. Check 'gh run list --repo $REPO' manually."
  exit 1
fi

echo "== Watching run $RUN_ID =="
if gh run watch "$RUN_ID" --repo "$REPO" --exit-status; then
  echo "== PASSED =="
  exit 0
else
  echo "== FAILED — showing failed step logs =="
  gh run view "$RUN_ID" --repo "$REPO" --log-failed
  exit 1
fi