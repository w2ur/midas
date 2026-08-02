#!/usr/bin/env bash
#
# Build (or verify) the Python 3.12 virtualenv the session runs on.
#
# Why this exists
# ---------------
# The 2026-07-31 weekday session fired on time at 20:00 UTC, stalled ~5 minutes
# in *while rebuilding the venv*, and did not resume until ~63 hours later. The
# session_guard added afterwards catches the resulting staleness, but it treats
# the symptom: a multi-minute network-bound install of pandas/bt/pandas-ta sat
# inside the timed critical path of every single run, and that is where the run
# died.
#
# The split this script enables:
#
#   IMAGE BUILD TIME (unattended, untimed, outside the session)
#       scripts/bootstrap_venv.sh
#   SESSION TIME (Step 0, inside the 3h session_guard budget)
#       scripts/bootstrap_venv.sh --check
#
# `--check` is O(milliseconds), touches no network, and mutates nothing. It
# either confirms the venv is exactly what requirements.txt asks for, or it
# fails loudly and the session aborts before authoring anything.
#
# Aborting is the correct outcome, not a regression. A session that cannot start
# is cheap: no commit lands, session-watchdog files an issue the next morning,
# and the following scheduled session starts clean. A session that silently
# rebuilds is how we lost 63 hours.
#
# Idempotent: re-running the build mode on an already-correct venv exits fast
# without reinstalling. The stamp records the interpreter version and a hash of
# requirements.txt, so a lockfile bump correctly invalidates it.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${MIDAS_VENV:-$REPO_ROOT/.venv}"
REQ_FILE="${MIDAS_REQS:-$REPO_ROOT/requirements.txt}"
STAMP="$VENV_DIR/.midas-bootstrap"
MIN_MAJOR=3
MIN_MINOR=12

usage() {
    cat >&2 <<'USAGE'
usage: scripts/bootstrap_venv.sh [--check]

  (no args)  Build or repair the venv. Safe to re-run; installs only when the
             recorded stamp does not match the current requirements lockfile.
             Run this at image-build time, never inside a timed session.
  --check    Verify only. No network, no mutation. Exit 1 with a diagnosis if
             the venv is missing, on the wrong interpreter, or stale against
             requirements.txt.
USAGE
}

MODE=build
case "${1:-}" in
    --check) MODE=check ;;
    -h | --help) usage; exit 0 ;;
    "") ;;
    *) echo "unknown argument: $1" >&2; usage; exit 2 ;;
esac

die() { echo "FATAL: $*" >&2; exit 1; }

# Hash of the lockfile the venv was built from. Any lockfile edit invalidates
# the stamp, so a dependency bump cannot silently keep an old environment.
reqs_hash() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$REQ_FILE" | cut -d' ' -f1
    else
        shasum -a 256 "$REQ_FILE" | cut -d' ' -f1
    fi
}

# "3.12.2" -> fails if < 3.12. Guards the 2026-07-17 crash, where the venv was
# built on 3.11 and pandas-ta blew up at import time mid-session.
assert_version_ok() {
    local py="$1" ver major minor
    ver="$("$py" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" \
        || die "cannot run interpreter: $py"
    major="${ver%%.*}"
    minor="${ver##*.}"
    if ((major < MIN_MAJOR || (major == MIN_MAJOR && minor < MIN_MINOR))); then
        die "venv runs Python $ver, need >= $MIN_MAJOR.$MIN_MINOR (pandas-ta breaks below it)"
    fi
    echo "$ver"
}

[[ -f "$REQ_FILE" ]] || die "no requirements lockfile at $REQ_FILE"

# ---------------------------------------------------------------- check mode
if [[ "$MODE" == check ]]; then
    [[ -x "$VENV_DIR/bin/python" ]] \
        || die "no venv at $VENV_DIR — build it at image-build time with scripts/bootstrap_venv.sh"
    ver="$(assert_version_ok "$VENV_DIR/bin/python")"
    [[ -f "$STAMP" ]] \
        || die "venv at $VENV_DIR has no bootstrap stamp — rebuild it with scripts/bootstrap_venv.sh"

    want="$(reqs_hash)"
    got="$(cut -d' ' -f1 <"$STAMP")"
    [[ "$want" == "$got" ]] \
        || die "venv is stale: built for requirements ${got:0:12}, lockfile is now ${want:0:12} — rebuild it"

    echo "venv OK: Python $ver at $VENV_DIR, matches $(basename "$REQ_FILE") ${want:0:12}"
    exit 0
fi

# ---------------------------------------------------------------- build mode
# Prefer an explicit python3.12; fall back to python3 only if it is new enough.
PY=""
for candidate in python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        v="$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
        maj="${v%%.*}"; min="${v##*.}"
        if [[ -n "$v" ]] && ((maj > MIN_MAJOR || (maj == MIN_MAJOR && min >= MIN_MINOR))); then
            PY="$candidate"
            break
        fi
    fi
done
[[ -n "$PY" ]] || die "no Python >= $MIN_MAJOR.$MIN_MINOR on PATH (looked for python3.12, python3)"

want="$(reqs_hash)"

# Already correct? Say so and stop — this is the common case on a warm image.
if [[ -x "$VENV_DIR/bin/python" && -f "$STAMP" ]]; then
    if [[ "$want" == "$(cut -d' ' -f1 <"$STAMP")" ]] \
        && assert_version_ok "$VENV_DIR/bin/python" >/dev/null 2>&1; then
        echo "venv already current — nothing to do."
        exit 0
    fi
    echo "venv stale or on the wrong interpreter — rebuilding from scratch."
    rm -rf "$VENV_DIR"
fi

echo "Creating venv at $VENV_DIR using $PY ($("$PY" --version 2>&1))..."
"$PY" -m venv "$VENV_DIR"
ver="$(assert_version_ok "$VENV_DIR/bin/python")"

echo "Installing $(basename "$REQ_FILE") (this is the slow step — it belongs here, not in a session)..."
"$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null
"$VENV_DIR/bin/python" -m pip install -r "$REQ_FILE"

# Stamp last: an interrupted install leaves no stamp, so --check fails closed
# rather than blessing a half-installed venv.
echo "$want $ver" >"$STAMP"
echo "venv ready: Python $ver at $VENV_DIR, stamped ${want:0:12}"
