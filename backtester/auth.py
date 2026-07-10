"""Shared-secret gate for the backtester service.

Active only when BACKTESTER_SECRET is set in the environment. When unset
(local dev, existing test runs), the gate is a no-op. Production deployments
MUST set it (see backtester/README.md) — that is what closes D10's
`--allow-unauthenticated` hole: the Netlify proxy is the sole holder of the
secret, so Cloud Run only answers requests that came through it.
"""

from __future__ import annotations

import os

from fastapi import Header, HTTPException


def require_secret(x_backtester_secret: str | None = Header(default=None)) -> None:
    expected = os.environ.get("BACKTESTER_SECRET")
    if not expected:
        return
    if x_backtester_secret != expected:
        raise HTTPException(status_code=401, detail="unauthorized")
