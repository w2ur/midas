#!/usr/bin/env bash
#
# SessionStart safety net — guarantee the venv exists before a cloud session
# starts working.
#
# The cloud environment's setup script already pre-bakes the venv into the image
# snapshot, so on a warm image this exits in milliseconds with "already current"
# and nothing to do. It exists for the cases the pre-bake cannot cover:
#
#   - the environment cache expires after roughly 7 days and rebuilds;
#   - the clone lands somewhere the setup script's search misses;
#   - the setup script no-ops or fails (it always exits 0 by design, because a
#     non-zero setup script makes every session in the environment unstartable).
#
# This does NOT contradict the trigger's "a failing --check must abort, never
# rebuild" rule. That rule stops the *orchestrator* improvising a repair
# mid-pipeline against a possibly-stale world. This runs deterministically
# before any session work begins, outside the timed pipeline.
#
# Cloud-only: exits immediately on a local checkout, so it never touches a dev
# machine's venv.

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

REPO="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

bash "$REPO/scripts/bootstrap_venv.sh" \
    || echo "[midas] venv build failed — Step 0's --check will abort the session" >&2

# Never non-zero: a failing hook must not be what stops the session. The
# session's own Step 0 --check is the gate, and it fails loudly with a diagnosis.
exit 0
