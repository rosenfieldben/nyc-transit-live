"""Fetch and decode the MTA Bus Time GTFS-Realtime VehiclePositions feed."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from google.transit import gtfs_realtime_pb2

# The .env file lives in the project root, one level up from backend/.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

VEHICLE_POSITIONS_URL = "https://gtfsrt.prod.obanyc.com/vehiclePositions"


def _api_key() -> str:
    key = os.getenv("BUS_TIME_API_KEY")
    if not key or key == "your-key-here":
        raise RuntimeError(
            "BUS_TIME_API_KEY is not set. Copy .env.example to .env in the "
            "project root and add your MTA Bus Time key."
        )
    return key


async def fetch_vehicle_positions() -> list[dict]:
    """Fetch the feed, decode the protobuf, and return one dict per vehicle.

    Each dict has: id, route_id, latitude, longitude, bearing. Entities without
    a position are skipped; bearing is None when the feed doesn't report it.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(VEHICLE_POSITIONS_URL, params={"key": _api_key()})
        resp.raise_for_status()
        raw = resp.content

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw)

    vehicles: list[dict] = []
    for entity in feed.entity:
        if not entity.HasField("vehicle"):
            continue
        v = entity.vehicle
        if not v.HasField("position"):
            continue
        pos = v.position
        vehicles.append(
            {
                "id": v.vehicle.id or entity.id,
                "route_id": v.trip.route_id or None,
                "latitude": pos.latitude,
                "longitude": pos.longitude,
                "bearing": pos.bearing if pos.HasField("bearing") else None,
            }
        )
    return vehicles
