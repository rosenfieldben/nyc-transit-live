"""Feed-cache primitives shared by the pollers and the route handlers.

The in-memory cache entry shapes, the warming/last-known-on-failure serving
contract, the freshness threshold, and the upstream-error sanitizer. A leaf
module: it imports nothing from main/pollers/routes, so everything else can
import it without a cycle. _serve_cached takes the app explicitly (rather than
closing over a module global) precisely so it can live here.
"""

from __future__ import annotations

import logging
import re

from fastapi import HTTPException, Response

from feeds import ALERT_FEED_URLS

# Log through the "main" logger (not __name__) so records and main.py's logging
# config are unchanged by the split, the same discipline the feeds package uses.
logger = logging.getLogger("main")

# Upstream-staleness threshold: how far the feed's CONTENT time (MTA's clock)
# may lag the poll time (this server's clock) before the data is considered
# stale — used by /healthz and reported via /api/status. Computed from two
# server-captured timestamps (fetched_at - feed_timestamp), so the browser
# clock is never involved; the frontend mirrors this in helpers.js.
FEED_STALE_AFTER_S = 90


def _feed_age(entry: dict) -> float | None:
    """Seconds the feed content lagged the poll, or None if not computable.
    Both inputs are server-captured at poll time, so this is clock-skew free."""
    if entry["fetched_at"] is None or entry["feed_timestamp"] is None:
        return None
    return entry["fetched_at"] - entry["feed_timestamp"]


def _fresh_entry() -> dict:
    # fetched_at = this server's poll time; feed_timestamp = the feed's content
    # time (MTA's clock). Both are stored so freshness can be judged without the
    # browser clock — see _feed_age and FEED_STALE_AFTER_S.
    return {"data": None, "fetched_at": None, "feed_timestamp": None, "error": None}


def _fresh_alerts_entry() -> dict:
    # alerts = the active-alert index (None until the first successful poll, [] once
    # a poll decoded zero active alerts); active/suppressed are the counts /api/status
    # reports. Same last-known-on-failure rule as the feed cache: a failed poll keeps
    # the last index and its fetched_at, replacing them only on a poll that decoded.
    # health = per-system freshness, so a PARTIAL outage (one feed down, not all) is
    # visible instead of silently thinning the index: fresh_at is the last decode,
    # retained_since marks a system whose alerts are being carried forward from a
    # down feed (null when fresh or once the retention cap drops them), last_error
    # flags a system failing this poll. Keyed by the same four alert systems.
    return {
        "alerts": None,
        "fetched_at": None,
        "error": None,
        "active": 0,
        "suppressed": 0,
        "health": {
            system: {"fresh_at": None, "retained_since": None, "last_error": None}
            for system in ALERT_FEED_URLS
        },
    }


def _note_failure(entry: dict, status: int, detail: str, log: bool = True) -> None:
    """Record why the latest poll failed. Last-known data keeps being served;
    the error only surfaces to clients while the cache has never been filled.
    log=False suppresses the warning for an EXPECTED, recurring condition (the
    subway warming path notes a 503 every poll while static loads, but the single
    transition warning belongs to _set_static_status, not every 20s poll)."""
    entry["error"] = {"status": status, "detail": detail}
    if log:
        logger.warning("feed poll failed (%d): %s", status, detail)


_URL_RE = re.compile(r"https?://\S+")


def _sanitize_upstream(exc: BaseException) -> str:
    """Strip URLs from upstream error text before recording it: httpx error
    strings embed the full request URL, which for the bus feed includes the
    API key query parameter, and recorded details are served by /api/status
    and the never-filled error paths."""
    return _URL_RE.sub("<feed url>", str(exc))


def _serve_cached(app, name: str, data_key: str = "data") -> dict:
    """Serve {fetched_at, feed_timestamp, <data_key>} from the cache. Stale-but-present
    data is still served; the frontend judges staleness from the fetched_at /
    feed_timestamp pair (upstream lag) plus its own skew-corrected poll age
    (now - fetched_at), so a stuck poller serving frozen data still surfaces.
    Errors only reach clients while the cache has never successfully filled.

    data_key names the payload field in the envelope: the MTA feeds use "data"
    (the default), the PATH feed uses "trains" (its PathFeed model). Keeping the
    envelope/warming/never-filled contract in one place means a change here
    (a header, a reworded 503) reaches every feed endpoint, PATH included.

    The app is passed in (not a module global) so this can live in the leaf cache
    module; the route handlers hand it request.app."""
    entry = app.state.feed_cache[name]
    if entry["data"] is not None:
        return {
            "fetched_at": entry["fetched_at"],
            "feed_timestamp": entry["feed_timestamp"],
            data_key: entry["data"],
        }
    if entry["error"]:
        raise HTTPException(entry["error"]["status"], entry["error"]["detail"])
    raise HTTPException(
        status_code=503, detail="Feed cache is warming up; try again in a few seconds."
    )


def _require_filled_cache(entry: dict) -> None:
    """Warming gate shared by the arrivals endpoints: until the feed's cache
    has filled once there is no per-station index worth serving, so surface
    the recorded upstream error when there is one, else the generic warming
    503. Same contract _serve_cached keeps for the feed endpoints; the three
    arrivals endpoints each carried an identical inline copy until the
    13d-era cleanup."""
    if entry["data"] is None:
        if entry["error"]:
            raise HTTPException(entry["error"]["status"], entry["error"]["detail"])
        raise HTTPException(
            status_code=503, detail="Feed cache is warming up; try again in a few seconds."
        )


def _static_endpoint_ready(status: str, response: Response, warming_detail: str) -> bool:
    """Shared warming behavior for the static-derived (decorative) endpoints.

    - loading: raise a 503 (the data is coming; do not cache anything).
    - ready: set the long cache header and return True so the caller serves data.
    - failed (retrying): set no-cache and return False so the caller serves [] that
      a browser will NOT cache, so a later retry success is not masked for an hour.
    Returning [] under a max-age here (the old behavior) was the cold-start bug:
    a browser could cache an empty payload for the whole warmup.
    """
    if status == "loading":
        raise HTTPException(status_code=503, detail=warming_detail)
    if status == "ready":
        response.headers["Cache-Control"] = "public, max-age=3600"
        return True
    response.headers["Cache-Control"] = "no-cache"  # failed: never cache the empty
    return False
