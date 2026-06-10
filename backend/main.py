"""FastAPI app exposing decoded MTA Bus Time data as clean JSON."""

from __future__ import annotations

from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from feeds import fetch_vehicle_positions

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="NYC Transit Live", version="0.1.0")


@app.get("/api/buses")
async def get_buses() -> list[dict]:
    """Return a JSON list of buses, each with id, route_id, latitude,
    longitude, and bearing."""
    try:
        return await fetch_vehicle_positions()
    except RuntimeError as exc:
        # Missing/placeholder API key — a configuration problem, not a 500.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Upstream MTA feed error: {exc}"
        ) from exc


# Mounted last so /api/* routes take priority; html=True serves index.html at /.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
