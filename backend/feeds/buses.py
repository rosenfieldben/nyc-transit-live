"""NYC bus vehicle positions: the OneBusAway vehiclePositions endpoint and its
decode into bounded {id, route_id, latitude, longitude, bearing} markers."""

from __future__ import annotations

import httpx
from google.transit import gtfs_realtime_pb2

from feeds.shared import _api_key, _header_timestamp, _in_nyc

VEHICLE_POSITIONS_URL = "https://gtfsrt.prod.obanyc.com/vehiclePositions"


async def fetch_vehicle_positions(client: httpx.AsyncClient) -> tuple[list[dict], float | None]:
    """Fetch the feed, decode the protobuf, and return (vehicles, feed_timestamp).

    Each vehicle dict has: id, route_id, latitude, longitude, bearing. Entities
    without a position are skipped; bearing is None when the feed doesn't report
    it. feed_timestamp is the feed's content time (MTA's clock). The caller owns
    the client (the polling task holds one for its lifetime).
    """
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
        if not _in_nyc(pos.latitude, pos.longitude):
            continue  # out-of-range coordinate (e.g. 0,0); not a real NYC bus
        vehicles.append(
            {
                "id": v.vehicle.id or entity.id,
                "route_id": v.trip.route_id or None,
                "latitude": pos.latitude,
                "longitude": pos.longitude,
                "bearing": pos.bearing if pos.HasField("bearing") else None,
            }
        )
    return vehicles, _header_timestamp(feed)
