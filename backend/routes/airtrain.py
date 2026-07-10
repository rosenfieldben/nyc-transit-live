"""AirTrain JFK endpoint: static geometry, stations, and scheduled headways."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from models import AirTrainData

router = APIRouter()


@router.get("/api/airtrain", response_model=AirTrainData)
async def get_airtrain(request: Request, response: Response) -> dict:
    """AirTrain JFK static geometry, stations, and SCHEDULED headways.

    Static-only: AirTrain JFK has no realtime feed, so this endpoint never carries
    live positions or countdowns; the headways are scheduled reference bands (see
    the _provenance block in data/airtrain_jfk.json). Loaded once at startup from a
    committed fixture, so it is always ready while the server is up (no warming
    503) and is cacheable for the session like the other static endpoints.
    """
    response.headers["Cache-Control"] = "public, max-age=3600"
    return request.app.state.airtrain
