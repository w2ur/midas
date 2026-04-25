"""Midas backtester service — FastAPI app.

Deployed to Google Cloud Run. Wraps the existing `engine.backtest.run_backtest`
behind an HTTP API consumed by the `/simulate` page on midas.revah.paris.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Midas Backtester", version="0.1.0")

# Site is served from midas.revah.paris; allow it plus localhost for dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://midas.revah.paris",
        "http://localhost:4321",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
