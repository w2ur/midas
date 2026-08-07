"""Hash the committed weekday-session trigger prompt.

Why this exists
---------------
The prompt that actually runs the daily session lives in the RemoteTrigger
configuration on claude.ai, outside this repository.
`docs/triggers/weekday-session.md` is its source text, and the two are kept in
step by hand. Nothing verified they were in step: a session could run last
month's prompt against this month's helpers indefinitely, and the only symptom
would be a step quietly not happening. The 2026-08-07 reliability review listed
the live prompt under "what this review could not verify" for exactly this
reason.

The bridge is a hash the live prompt carries:

1. The prompt block in the doc contains a literal ``PROMPT_SHA256: <hex>`` line.
2. That line is **excluded** from the hashed text (otherwise the hash would
   have to contain itself).
3. Step 0 of the session runs ``python scripts/prompt_hash.py --expect <hex>``
   with the hex the *live* prompt carries. The script re-derives the hash from
   the doc in the checkout it just realigned to. A mismatch means the live
   prompt and the repo have diverged — reported, not fatal, because a stale
   prompt still runs and a lost session is worse than a warned one.

`--check` compares the doc against the committed sidecar
``docs/triggers/weekday-session.sha256`` and is what CI runs, so editing the
prompt without regenerating the sidecar turns the suite red.

Usage:
    python scripts/prompt_hash.py                 # print the hash
    python scripts/prompt_hash.py --check         # vs the committed sidecar
    python scripts/prompt_hash.py --write         # regenerate the sidecar
    python scripts/prompt_hash.py --expect <hex>  # vs the live prompt's literal
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

DOC_PATH = _PROJECT_ROOT / "docs" / "triggers" / "weekday-session.md"
SIDECAR_PATH = _PROJECT_ROOT / "docs" / "triggers" / "weekday-session.sha256"

_HEADING = "## Trigger prompt"
_SELF_LINE = re.compile(r"^\s*PROMPT_SHA256:")


class PromptBlockError(RuntimeError):
    """The doc does not contain exactly one extractable trigger-prompt block."""


def extract_prompt(doc_path: Path | None = None) -> str:
    """Return the fenced prompt block under ``## Trigger prompt``.

    Normalisation is deliberately minimal — trailing whitespace per line and a
    single trailing newline. Anything more (collapsing blank lines, stripping
    comments) would let a real edit to the prompt hash identically.
    """
    text = (doc_path or DOC_PATH).read_text(encoding="utf-8")
    try:
        after = text.split(_HEADING, 1)[1]
    except IndexError:
        raise PromptBlockError(
            f"{_HEADING!r} not found in {doc_path or DOC_PATH}"
        ) from None

    lines = after.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith("```"))
    except StopIteration:
        raise PromptBlockError("no fenced block follows the heading") from None
    try:
        end = next(
            i
            for i, ln in enumerate(lines[start + 1 :], start + 1)
            if ln.startswith("```")
        )
    except StopIteration:
        raise PromptBlockError("the fenced block is not closed") from None

    body = [ln.rstrip() for ln in lines[start + 1 : end] if not _SELF_LINE.match(ln)]
    return "\n".join(body) + "\n"


def prompt_sha256(doc_path: Path | None = None) -> str:
    return hashlib.sha256(extract_prompt(doc_path).encode("utf-8")).hexdigest()


def read_sidecar(path: Path | None = None) -> str | None:
    path = path or SIDECAR_PATH
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip()


def write_sidecar(path: Path | None = None, doc_path: Path | None = None) -> str:
    digest = prompt_sha256(doc_path)
    (path or SIDECAR_PATH).write_text(digest + "\n", encoding="utf-8")
    return digest


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", action="store_true", help="compare to the sidecar")
    p.add_argument("--write", action="store_true", help="regenerate the sidecar")
    p.add_argument(
        "--expect",
        metavar="HEX",
        help="compare to the hash the live prompt carries (Step 0 of the session)",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    actual = prompt_sha256()

    if args.write:
        write_sidecar()
        print(f"wrote {SIDECAR_PATH.name}: {actual}")
        return 0

    if args.check:
        expected = read_sidecar()
        if expected is None:
            print(f"FAIL: {SIDECAR_PATH} is missing", file=sys.stderr)
            return 1
        if expected != actual:
            print(
                f"FAIL: trigger prompt hash drifted.\n"
                f"  sidecar: {expected}\n"
                f"  doc:     {actual}\n"
                f"Regenerate with: python scripts/prompt_hash.py --write",
                file=sys.stderr,
            )
            return 1
        print(f"OK: trigger prompt matches sidecar ({actual})")
        return 0

    if args.expect:
        if args.expect.strip().lower() != actual:
            print(
                "PROMPT DRIFT: the live RemoteTrigger prompt does not match "
                "docs/triggers/weekday-session.md in this checkout.\n"
                f"  live prompt says: {args.expect.strip()}\n"
                f"  this checkout:    {actual}\n"
                "Run the session as configured and report this in the final "
                "summary — do NOT edit the prompt mid-run. The owner re-pastes "
                "the doc into the RemoteTrigger configuration out of band.",
                file=sys.stderr,
            )
            return 1
        print(f"OK: live prompt matches this checkout ({actual})")
        return 0

    print(actual)
    return 0


if __name__ == "__main__":
    sys.exit(main())
