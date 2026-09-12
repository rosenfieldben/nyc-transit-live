"""NYC bus vehicle positions: the OneBusAway vehiclePositions endpoint and its
decode into bounded {id, route_id, latitude, longitude, bearing} markers."""

from __future__ import annotations

import httpx

import env_seams
from feeds.shared import _api_key, _header_timestamp, _in_nyc, parse_feed

# Overridable (C6). Used WHOLE, with the API key added as a query parameter
# rather than a path suffix, so this is a full-URL seam and not a base.
VEHICLE_POSITIONS_URL = env_seams.url(
    "BUS_RT_URL", "https://gtfsrt.prod.obanyc.com/vehiclePositions"
)


async def fetch_vehicle_positions(client: httpx.AsyncClient) -> tuple[list[dict], float | None]:
    """Fetch the feed, decode the protobuf, and return (vehicles, feed_timestamp).

    Each vehicle dict has: id, route_id, latitude, longitude, bearing, observed_at
    and provenance. Entities without a position are skipped; bearing is None when the
    feed doesn't report it. feed_timestamp is the feed's content time (MTA's clock). The caller owns
    the client (the polling task holds one for its lifetime).
    """
    resp = await client.get(VEHICLE_POSITIONS_URL, params={"key": _api_key()})
    resp.raise_for_status()
    raw = resp.content

    # parse_feed, not ParseFromString: an empty 200 used to decode as a healthy
    # feed with zero vehicles, clearing the error and blanking the map (C3). The
    # FeedDecodeError it raises is a DecodeError subclass, so _refresh_buses'
    # existing handler records it as a failed poll and keeps last-known.
    feed = parse_feed(raw)

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
                # MEASURED BEFORE IT WAS RELIED ON (section 6.0 of the freshness
                # contract). This is OneBusAway's GTFS-Realtime feed, not SIRI, so
                # there is no RecordedAtTime; the analogous field is this one, and the
                # probe that made the policy row writable found all 2136 vehicles
                # carrying it, 47 distinct values across 104 seconds, median age 15s.
                # Protobuf 0 is unset rather than an instant, hence the falsy guard.
                "observed_at": float(v.timestamp) or None,
                "provenance": "reported",
            }
        )
    return vehicles, _header_timestamp(feed)
