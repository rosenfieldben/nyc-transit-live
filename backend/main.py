"""FastAPI app exposing decoded MTA Bus Time data as clean JSON."""

from __future__ import annotations

import httpx
from fastapi import FastAPI, HTTPException

from feeds import fetch_vehicle_positions

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
