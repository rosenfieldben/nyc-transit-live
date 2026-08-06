"""Background poll loops and the per-feed refreshers.

The two _poll_* loops run for the app's lifetime (started by main's lifespan);
each _refresh_* decodes one system into the shared feed cache. A refresher takes
the app and an httpx client, records last-known-on-failure via the cache
helpers, and never raises out of the loop.

Depends on main for the feed fetchers (fetch_subway_trains, fetch_service_alerts,
...): those are the names the tests monkeypatch on the main module, so the
refreshers resolve them through `main.` at call time to keep
`monkeypatch.setattr(main, "fetch_subway_trains", ...)` effective after the
split. The non-swappable feed helpers (carry_forward_prev, match_path_identities,
merge_alert_generations, the feed-URL sets) are imported straight from feeds.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from collections.abc import Iterable

import httpx
from fastapi import FastAPI
from google.protobuf.message import DecodeError

import env_seams
import main
import njt_auth
from cache import (
    FEED_RETENTION_ENABLED,
    FEED_RETENTION_MAX_S,
    _note_failure,
    _sanitize_upstream,
)
from feeds import (
    ALERT_FEED_URLS,
    ALERT_RETENTION_MAX_S,
    RAILROAD_FEED_URLS,
    SUBWAY_FEED_URLS,
    carry_forward_prev,
    combine_group_arrivals,
    combine_group_trains,
    drop_expired_arrivals,
    match_path_identities,
    merge_alert_generations,
    merge_system_generations,
)
from feeds import njt as njt_feed

# Log through the "main" logger (not __name__) so records and main.py's logging
# config are unchanged by the split.
logger = logging.getLogger("main")

# The backend polls the MTA once and serves every browser client from this
# cache, so N clients never means N upstream fetches.
# Overridable (C6): the contract tier compresses every cadence so a scenario that
# has to outlive a threshold finishes in seconds instead of minutes. Unset, this is
# the prior literal.
POLL_INTERVAL_S = env_seams.seconds("POLL_INTERVAL_S", 20)

# Service alerts poll on their OWN slower loop: alerts change far more slowly than
# vehicle positions, and the subway alerts feed alone is ~400 KB, so re-pulling them
# all every 20s would be wasteful. A separate lifespan task on this cadence keeps
# the position poll lean and independent (an alert-feed outage never stalls it).
# Overridable (C6): the contract tier compresses every cadence so a scenario that
# has to outlive a threshold finishes in seconds instead of minutes. Unset, this is
# the prior literal.
ALERT_POLL_INTERVAL_S = env_seams.seconds("ALERT_POLL_INTERVAL_S", 60)

# THE INTERPRETER FLOOR IS 3.11, and C4 leans on it: asyncio.TaskGroup and
# ExceptionGroup are 3.11 builtins, as is the asyncio.timeout below that R2 added.
# Production pins 3.12 (nixpacks.toml) and both CI workflows install 3.12, so the
# floor is comfortably met; ruff.toml records it too, or the linter reports the
# group builtins as undefined names.
#
# A whole-task deadline for ONE system's refresh, applied per-child INSIDE the poll
# cycle's TaskGroup (see _poll_feeds). The httpx client timeout=30 bounds the gap
# between bytes,
# not the whole exchange, so a trickling upstream that dribbles a byte every few
# seconds can keep a single refresh alive indefinitely; and because the cycle awaits
# all five refreshers together, that one wedged refresh freezes every system's
# fetched_at with it. This is the hard ceiling on a single refresh: when it fires,
# the timeout surfaces as a TimeoutError that _bounded_refresh routes to the same
# _note_failure path every other failure takes (last-known data kept, the error
# recorded for /api/status and the R1 stale surfaces), while the other four
# refreshers finish the cycle normally.
#
# WHY 45s when the poll cadence is 20s: the deadline is deliberately generous
# relative to the cadence. The loop sleeps AFTER the cycle, so a slow-but-finishing
# refresh merely stretches the next tick a little (harmless); only a truly wedged
# refresh should ever be aborted. 45s exceeds any healthy fetch (a full subway
# multi-feed pull is low single-digit seconds) by a wide margin, so it never aborts
# a healthy-but-slow cycle during an upstream slowdown, while still guaranteeing
# every cycle is finite. This does NOT replace the httpx per-op timeout=30: that
# guards a stalled SOCKET (no bytes for 30s) and this guards a whole request that
# keeps trickling under that floor; they catch different failure shapes, so both stay.
REFRESH_DEADLINE_S = 45


async def _bounded_refresh(entry: dict, coro) -> None:
    """Run one refresh coroutine under the whole-task REFRESH_DEADLINE_S. A timeout is
    converted here into the same last-known-on-failure record every other failure
    takes, so the cycle sees a NORMAL return for this system and the other systems
    still finish. Only TimeoutError is caught here; anything else is caught one layer
    out by _total_refresh, which is what keeps a surprise in one source from
    cancelling the other four through their shared TaskGroup (C4).

    The refreshers' only await is the upstream fetch, and everything after it (the
    entry.update, the feed_health and arrivals writes) is synchronous, so a deadline
    can only cancel the fetch, never a half-applied update: last-known state is left
    intact for _note_failure to preserve.

    Only the cache entry's error is recorded here; the per-system feed_health dict
    (a secondary /api/status signal) is deliberately left at its last value, because
    this generic wrapper does not know each system's health shape and the recorded
    504 is the authoritative failure indicator either way."""
    try:
        async with asyncio.timeout(REFRESH_DEADLINE_S):
            await coro
    except TimeoutError:
        _note_failure(
            entry,
            504,
            f"Upstream did not complete within the {REFRESH_DEADLINE_S}s refresh "
            "deadline; keeping last-known data.",
        )


# How many upstream feeds each source fans out over, for the health surface an
# unclassified failure has to mark. Buses are absent on purpose: that source
# publishes no per-feed health dict, so there is nothing to mark beyond its error.
_FEED_HEALTH_TOTALS = {"subways": len(SUBWAY_FEED_URLS), "railroads": len(RAILROAD_FEED_URLS)}
_SINGLE_FEED_HEALTH = {"path": "PATH", "ferry": "ferry", "njt": "njt"}


def _feed_degrader(app: FastAPI, name: str, entry: dict):
    """The per-source "mark everything down" hook handed to _total_refresh.

    Built HERE, at the cycle, because the shape is per source: the subway fans out
    over eight feed groups, the railroad over two, PATH and ferry over one each, and
    the bus feed publishes no health dict at all. It marks the same two surfaces every
    classified failure path in this module marks by hand: the app-state health dict
    and, where the envelope has one, the per-system block C2 publishes.
    """

    def mark() -> None:
        if name in _FEED_HEALTH_TOTALS:
            total = _FEED_HEALTH_TOTALS[name]
            urls = SUBWAY_FEED_URLS if name == "subways" else RAILROAD_FEED_URLS
            setattr(
                app.state,
                f"{name[:-1]}_feed_health",  # subways -> subway_feed_health
                {"total": total, "ok": 0, "failed": sorted(urls)},
            )
        elif name in _SINGLE_FEED_HEALTH:
            label = _SINGLE_FEED_HEALTH[name]
            setattr(app.state, f"{name}_feed_health", {"total": 1, "ok": 0, "failed": [label]})
        # The per-system freshness block, where this envelope has one. Same helper the
        # total-failure paths use, so a rider sees the same dimming either way.
        _mark_all_systems_failed(entry)

    return mark


async def _total_refresh(name: str, entry: dict, coro, mark_degraded=None) -> None:
    """Run one source's refresh so it CANNOT raise into the poll cycle (C4).

    THE CONTRACT: a child's failure is that SYSTEM'S failure, never the cycle's.
    With this in place the cycle's own exceptions are exclusively cancellation and
    bugs in the cycle plumbing itself, which is what lets the cycle join every child
    under a TaskGroup without one source's surprise taking down the generation.

    Every refresher already routes its EXPECTED failures itself (an upstream error,
    a config error, an undecodable body) and _bounded_refresh routes the deadline.
    What reaches here is the unexpected: a shape no handler names. Recording it as
    this system's failure is strictly better than the pre-C4 behavior, where it
    escaped into the cycle handler and was logged with no cache entry marked at all,
    so /api/status showed that source as fine while it silently stopped updating.

    CancelledError IS RE-RAISED, and that ordering is load-bearing. Shutdown cancels
    the poll task, the TaskGroup cancels its children, and a child that swallowed the
    cancellation would hang the group and then the lifespan. It is a BaseException in
    3.8+, so `except Exception` below would not catch it anyway; naming it explicitly
    is a guard against anyone widening that clause later.

    BUT NOT EVERY CancelledError IS OUR SHUTDOWN, and the difference matters because
    a TaskGroup DISCARDS a child that ends cancelled: no error, no log, the cycle
    reports success and sleeps. So a refresher (or a library under it) that raises a
    bare CancelledError while nobody asked it to would silently stop polling that
    system forever, with the cache showing no error at all. Task.cancelling() is the
    discriminator the runtime gives us: nonzero exactly when a cancellation was
    actually requested of this task. When it is zero the cancellation is spurious and
    gets recorded like any other unclassified failure; it is re-raised either way,
    because swallowing a real one is the worse mistake.

    mark_degraded IS NOT OPTIONAL POLISH. Recording entry["error"] alone leaves every
    PER-SYSTEM surface claiming health: /api/status builds degraded_systems from the
    per-system last_error and never looks at entry["error"], C2's blocks keep
    reporting ok: true so the client will not dim, and the contract monitor reads
    neither. An unclassified failure would therefore stop a system polling while every
    operator surface stayed green, which is the exact false-green this audit arc
    exists to remove. The hook comes from the CALLER because only it knows the shape:
    a generic wrapper cannot know that the subway has eight groups and the ferry has
    one (this is the same reasoning _bounded_refresh documents for leaving health
    alone, and the reason that decision is revisited here rather than copied).
    """
    try:
        await coro
    except asyncio.CancelledError:
        task = asyncio.current_task()
        if task is None or task.cancelling() == 0:
            logger.exception("%s refresh raised CancelledError with no cancellation pending", name)
            _note_failure(entry, 500, f"Internal error refreshing {name} (CancelledError)")
            if mark_degraded is not None:
                mark_degraded()
        raise
    except Exception as exc:
        # 500, not 502: 502 means "the upstream misbehaved", and by construction this
        # is a failure nobody classified, which is far more likely to be ours.
        #
        # THE TYPE, NOT THE MESSAGE. This detail is served publicly by /api/status and,
        # while a cache has never filled, by the feed endpoint itself, and str(exc) of
        # an arbitrary exception is arbitrary text: a filesystem path, a config value,
        # a chunk of upstream body. _sanitize_upstream only strips URLs, which is the
        # right tool for an httpx error and not for this. The class name says as much
        # as a reader outside the logs can act on, cannot be empty (which
        # `f"...: {exc}"` can), and the full exception with its traceback is one line
        # above in the log.
        logger.exception("%s refresh failed unexpectedly", name)
        _note_failure(entry, 500, f"Internal error refreshing {name} ({type(exc).__name__})")
        if mark_degraded is not None:
            mark_degraded()


async def _refresh_buses(app: FastAPI, client: httpx.AsyncClient) -> None:
    entry = app.state.feed_cache["buses"]
    try:
        data, feed_timestamp = await main.fetch_vehicle_positions(client)
    except RuntimeError as exc:
        # Missing/placeholder API key — a configuration problem, not a 500.
        _note_failure(entry, 503, str(exc))
        return
    except httpx.HTTPError as exc:
        _note_failure(entry, 502, f"Upstream MTA feed error: {_sanitize_upstream(exc)}")
        return
    except DecodeError:
        # HTTP 200 with a non-protobuf body (CDN error page, maintenance HTML).
        _note_failure(entry, 502, "Upstream bus feed returned undecodable data")
        return
    entry.update(data=data, fetched_at=time.time(), feed_timestamp=feed_timestamp, error=None)


def _merge_feed_systems(
    entry: dict,
    prev_by_system: dict,
    fresh_by_system: dict,
    failed_systems: list[str],
    now: float,
    retain: bool | None = None,
) -> tuple[dict, dict[str, float]]:
    """Merge one poll's per-system data with the previous poll's, retaining a
    failed system's data (C2). Thin glue over the pure merge_system_generations:
    it threads the retention clock out of the cache entry and stores this poll's
    by-system data back under prev_key for the next poll to retain from.

    Called once per kind of data (trains, arrivals), each handed ITS OWN previous
    generation, because the two live in different places: trains in the cache
    entry, arrivals in the app-state index the endpoint reads. Both return the same
    retained_since, because the retention clock is a property of the SYSTEM rather
    than of the data kind: it is threaded from entry["systems"], written once.
    Gating one kind on the other's result was a bug worth naming here, since it
    reads plausible: a system with no retained TRAINS (it had none running, or the
    process just started) would have had its arrivals dropped as if the cap had
    fired, even though its retention had never begun.

    THE RETAINED DATA IS DEEP-COPIED. carry_forward_prev mutates the train dicts
    it is handed (it writes prev_* anchors in place), and retained trains come
    straight out of the entry that a previous response already serialized. Without
    the copy this poll would be editing objects an earlier response handed out,
    and the retained trains would silently acquire fresh anchors on every poll
    they survived, which is the opposite of frozen.
    """
    # Resolved HERE, not as a parameter default: a default binds at definition time,
    # so the flag's value would be frozen at import and no override could reach it.
    if retain is None:
        retain = FEED_RETENTION_ENABLED
    prev_retained = {
        system: block["retained_since"]
        for system, block in (entry.get("systems") or {}).items()
        if block.get("retained_since") is not None
    }
    merged, retained_since = merge_system_generations(
        fresh_by_system,
        prev_by_system if retain else {},
        failed_systems,
        prev_retained,
        now,
        FEED_RETENTION_MAX_S,
    )
    failed = set(failed_systems)
    merged = {
        system: (copy.deepcopy(items) if system in failed else items)
        for system, items in merged.items()
    }
    return merged, retained_since


def _mark_all_systems_failed(entry: dict) -> None:
    """Flip every per-system `ok` to False after a TOTAL poll failure.

    The total-failure paths return early, keeping the whole last-known envelope
    (pre-C2 behavior, deliberately unchanged), so without this the per-system block
    that envelope carries still reports ok: True for every system when not one of
    them decoded. The aggregate error and the frozen aggregate fetched_at already
    say the poll failed; this makes the per-system half say it too, which is the
    half the client reads.

    Each system's fetched_at is left alone: it is already honest ("this system's
    data is from then"), and a total outage does not change when the data was
    fetched. retained_since is left alone because no merge ran on this path, and
    restarting that clock here would be the same defect the write-ordering comments
    in the refreshers guard against.

    A no-op before the first successful poll: with no block there is nothing to
    correct, and the warming cache serves 503 rather than an envelope.
    """
    for block in (entry.get("systems") or {}).values():
        block["ok"] = False


def _routes_by_system(data_by_system: dict[str, list[dict]]) -> dict[str, list[str]]:
    """Which route ids each system's SERVED data covers (C2 PR2, subway only).

    The client needs this to point a stale system's block at the markers it
    describes: a subway train carries a route_id and nothing naming its feed group,
    so without the mapping the client could know the ACE group is stale and still
    have no way to dim its trains. Derived from the by-group partition the poller
    already holds rather than from a hand-maintained table, so it cannot drift from
    SUBWAY_FEED_URLS, and it stays true for retained data (a carried-forward group
    still lists its routes; one the cap has emptied lists none, which is right
    because it has no markers left).

    Sorted for a stable payload. A train with no route_id contributes nothing.
    """
    return {
        system: sorted({train["route_id"] for train in trains if train.get("route_id")})
        for system, trains in data_by_system.items()
    }


def _system_freshness(
    prev: dict | None,
    all_systems: Iterable[str],
    failed_systems: list[str],
    retained_since: dict[str, float],
    now: float,
    routes: dict[str, list[str]] | None = None,
) -> dict[str, dict]:
    """Build the per-system freshness block published in the aggregate envelope.

    fetched_at is THIS SYSTEM'S last poll that decoded, so it freezes while the
    system is failing while the envelope's own fetched_at keeps advancing; that
    divergence is the entire signal (see THE PER-SYSTEM RULE in cache.py). Shape
    matches models.SystemFreshness, and deliberately carries no error text: `ok`
    plus the age are the whole public signal, and sanitized detail stays on
    /api/status.

    `routes` is the optional per-system route coverage (see _routes_by_system);
    only the subway passes it, because only its entities lack a system name of
    their own. A system missing from the mapping publishes an EMPTY list rather
    than null: null means "this envelope does not do route coverage at all" (the
    railroad and alerts blocks), and the client's fail-safe for that is to dim on
    the source's worst system, which would be wrong here.
    """
    failed = set(failed_systems)
    previous = prev or {}
    blocks: dict[str, dict] = {}
    for system in all_systems:
        was = previous.get(system) or {}
        blocks[system] = {
            # A failed system keeps its last decode time; a healthy one stamps now.
            "fetched_at": was.get("fetched_at") if system in failed else now,
            "ok": system not in failed,
            "retained_since": retained_since.get(system),
            "routes": None if routes is None else routes.get(system, []),
        }
    return blocks


async def _refresh_subways(app: FastAPI, client: httpx.AsyncClient) -> None:
    entry = app.state.feed_cache["subways"]
    stops = app.state.subway_stops
    if not stops:
        # Static GTFS not ready yet (still loading, or a failed attempt retrying in
        # the background). No restart needed: the warmup retries automatically.
        # log=False: this recurs every poll during warmup, so the only log is the
        # single transition warning from _set_static_status (no per-poll spam).
        _note_failure(
            entry,
            503,
            "Static subway GTFS is still loading; it will retry automatically. Try again shortly.",
            log=False,
        )
        return
    total_feeds = len(SUBWAY_FEED_URLS)
    try:
        (
            trains,
            arrivals,
            feed_timestamp,
            failed_feeds,
            trains_by_group,
            arrivals_by_group,
        ) = await main.fetch_subway_trains(stops, client)
    except RuntimeError as exc:
        # Every subway feed failed this poll.
        app.state.subway_feed_health = {
            "total": total_feeds,
            "ok": 0,
            "failed": sorted(SUBWAY_FEED_URLS),
        }
        _mark_all_systems_failed(entry)
        _note_failure(entry, 502, _sanitize_upstream(exc))
        return
    except httpx.HTTPError as exc:
        app.state.subway_feed_health = {
            "total": total_feeds,
            "ok": 0,
            "failed": sorted(SUBWAY_FEED_URLS),
        }
        _mark_all_systems_failed(entry)
        _note_failure(entry, 502, f"Upstream MTA feed error: {_sanitize_upstream(exc)}")
        return
    # Partial failures still return data, so without this a vanished line group
    # would leave no trace (the entry error is cleared below, and feed_timestamp
    # is the min over only the surviving feeds). Record which groups dropped so
    # /api/status can surface the partial outage.
    app.state.subway_feed_health = {
        "total": total_feeds,
        "ok": total_feeds - len(failed_feeds),
        "failed": failed_feeds,
    }
    now = time.time()
    # C2: carry a FAILED group's trains and arrivals forward instead of publishing
    # survivors only, so its riders see stale-but-labelled data rather than an empty
    # map. The merge runs BEFORE carry_forward_prev, deliberately: that helper
    # rebuilds its anchor memory from exactly the list it is handed, so a retained
    # train left out of that list would lose its anchor and re-enter as a first
    # sighting the moment the group recovered.
    merged_trains_by_group, retained_since = _merge_feed_systems(
        entry, entry.get("trains_by_system") or {}, trains_by_group, failed_feeds, now
    )
    merged_arrivals_by_group, _ = _merge_feed_systems(
        entry,
        getattr(app.state, "subway_arrivals_by_system", None) or {},
        arrivals_by_group,
        failed_feeds,
        now,
    )
    entry["trains_by_system"] = merged_trains_by_group
    # Prune arrivals whose time has already passed BEFORE combining. A retained
    # group's rows keep their original absolute times, and the trim sorts
    # soonest-first, so an expired row is the smallest number in its bucket and
    # would fill the per-direction cap ahead of every live arrival: one failed
    # group could evict the healthy groups' fresh rows from a shared station.
    merged_arrivals_by_group = drop_expired_arrivals(merged_arrivals_by_group, now, failed_feeds)
    app.state.subway_arrivals_by_system = merged_arrivals_by_group
    trains = combine_group_trains(merged_trains_by_group)
    arrivals = combine_group_arrivals(merged_arrivals_by_group)
    # AFTER both merges, never between them: this write resets retained_since, and
    # a merge running afterwards would read the reset value as "no previous
    # retention", restart the clock at `now`, and never reach the cap.
    entry["systems"] = _system_freshness(
        entry.get("systems"),
        SUBWAY_FEED_URLS,
        failed_feeds,
        retained_since,
        now,
        # Route coverage comes from the MERGED trains, not the fresh ones: those are
        # the trains actually on the map, retained ones included, and dimming has to
        # reach exactly them.
        _routes_by_system(merged_trains_by_group),
    )
    # Carry each trip's previous-poll stop forward as its prev interpolation anchor
    # when the feed pruned the departed stop (mutates trains in place), then remember
    # this poll's positions for the next one.
    app.state.subway_positions = carry_forward_prev(
        trains, getattr(app.state, "subway_positions", {})
    )
    entry.update(data=trains, fetched_at=now, feed_timestamp=feed_timestamp, error=None)
    # Replace the arrivals index only on success, so a failed poll keeps the
    # last-known arrivals on the same fetched_at, consistent with the cache.
    app.state.subway_arrivals = arrivals


async def _refresh_railroads(app: FastAPI, client: httpx.AsyncClient) -> None:
    entry = app.state.feed_cache["railroads"]
    total_feeds = len(RAILROAD_FEED_URLS)
    try:
        trains, arrivals_by_system, feed_timestamp, failed_feeds = await main.fetch_railroad_trains(
            client, getattr(app.state, "railroad_stops", {})
        )
    except RuntimeError as exc:
        # Every railroad feed failed this poll.
        app.state.railroad_feed_health = {
            "total": total_feeds,
            "ok": 0,
            "failed": sorted(RAILROAD_FEED_URLS),
        }
        _mark_all_systems_failed(entry)
        _note_failure(entry, 502, _sanitize_upstream(exc))
        return
    except httpx.HTTPError as exc:
        app.state.railroad_feed_health = {
            "total": total_feeds,
            "ok": 0,
            "failed": sorted(RAILROAD_FEED_URLS),
        }
        _mark_all_systems_failed(entry)
        _note_failure(entry, 502, f"Upstream MTA feed error: {_sanitize_upstream(exc)}")
        return
    # Partial failures still return data; record which systems dropped so
    # /api/status surfaces the partial outage (parallel to _refresh_subways).
    app.state.railroad_feed_health = {
        "total": total_feeds,
        "ok": total_feeds - len(failed_feeds),
        "failed": failed_feeds,
    }
    now = time.time()
    # C2: retain a failed SYSTEM's trains rather than publishing the survivor only,
    # so a down MNR leaves stale-but-labelled markers instead of an empty map. Unlike
    # the subway the decode already tags every train with its system, so the
    # partition is a group-by rather than a change to the decoder. Runs BEFORE
    # carry_forward_prev for the same anchor-memory reason as the subway.
    fresh_by_system: dict[str, list[dict]] = {system: [] for system in RAILROAD_FEED_URLS}
    for train in trains:
        fresh_by_system.setdefault(train["system"], []).append(train)
    for system in failed_feeds:
        # Only DECODING systems may appear: an empty list here would read as
        # "decoded, nothing running" and replace the retained data.
        fresh_by_system.pop(system, None)
    merged_by_system, retained_since = _merge_feed_systems(
        entry, entry.get("trains_by_system") or {}, fresh_by_system, failed_feeds, now
    )
    entry["trains_by_system"] = merged_by_system
    trains = [train for system_trains in merged_by_system.values() for train in system_trains]
    # The arrivals merge runs HERE, before the systems write below, and that
    # ordering is load-bearing. The write resets retained_since; a merge running
    # after it would read the reset value as "no previous retention", restart the
    # clock at `now` every poll, and never reach the cap. Same rule as the subway
    # refresher, which happened to have it right by accident.
    #
    # Keys on the FAILURE LIST, not on a system's absence from arrivals_by_system: a
    # system that decoded but has no static stops loaded contributes no arrivals
    # while never appearing in failed_feeds, and key-absence retention would serve
    # its stale arrivals forever while /api/status counted it healthy.
    merged_arrivals, _ = _merge_feed_systems(
        entry,
        getattr(app.state, "railroad_arrivals", None) or {},
        arrivals_by_system,
        failed_feeds,
        now,
        # NOT gated: railroad arrivals already retained per system BEFORE C2 (the
        # old code updated only the decoded systems' keys), so switching this off
        # with the flag would REGRESS main rather than preserve it. C2 only makes
        # that existing retention bounded, deterministic and honestly dated.
        retain=True,
    )
    app.state.railroad_arrivals = drop_expired_arrivals(merged_arrivals, now, failed_feeds)
    entry["systems"] = _system_freshness(
        entry.get("systems"), RAILROAD_FEED_URLS, failed_feeds, retained_since, now
    )
    # Carry each placed train's prev station forward across polls (the feeds prune
    # the just-departed stop, so the decode leaves prev_* null), giving the gliding
    # increment a previous-station anchor. GPS trains have next_time None, so the
    # forward-bracket guard skips them and they never synthesize a prev. Keyed by
    # (system, trip_id) since LIRR and MNR trip_ids are independent; mutates the
    # placed trains in place, then the memory is remembered for the next poll.
    app.state.railroad_positions = carry_forward_prev(
        trains,
        getattr(app.state, "railroad_positions", {}),
        key=lambda t: (t["system"], t["trip_id"]),
    )
    # feed_timestamp comes from LIRR's header only (MNR's lagging shared clock is
    # excluded; see feeds.RAILROAD_FRESHNESS_SYSTEMS). A TOTAL failure returns above
    # and keeps the last-known timestamp; on a PARTIAL failure the surviving systems
    # decide it, and when the only freshness-bearing system (LIRR) is the one that
    # failed it comes back None. Keep the last-known rather than writing that None
    # over it: /api/status reads an unknown feed age as healthy, so blanking it turns
    # a real outage into a green light.
    entry.update(
        data=trains,
        fetched_at=now,
        feed_timestamp=feed_timestamp if feed_timestamp is not None else entry["feed_timestamp"],
        error=None,
    )


async def _refresh_path(app: FastAPI, client: httpx.AsyncClient) -> None:
    """Refresh the PATH trains + arrivals from the community bridge feed.

    Same cache contract as the other systems: a failed poll keeps the
    last-known trains AND arrivals (the error only surfaces to clients while
    the cache has never filled), and both are replaced only on a poll that
    decoded. Deliberately NO carry_forward_prev here: that anchor memory keys
    on trip ids, and PATH bridge trip ids do not survive an upstream refresh
    (see path_static's module docstring). Identity and anchors come from
    match_path_identities instead (13d), which each successful poll threads
    its state through; a failed poll leaves that state untouched too, since a
    failure is not a generation and must not expire identities.
    """
    entry = app.state.feed_cache["path"]
    stops = getattr(app.state, "path_stops", None)
    if not stops:
        # The 13a static group is not ready yet: neither placement nor
        # arrivals can resolve parent station ids. Same quiet warming path as
        # the subway refresher: log=False because this recurs every poll
        # during warmup and the single transition log belongs to
        # _set_static_status, not the 20s poll loop.
        _note_failure(
            entry,
            503,
            "Static PATH GTFS is still loading; it will retry automatically. Try again shortly.",
            log=False,
        )
        return
    try:
        trains, arrivals, feed_timestamp, unresolved = await main.fetch_path_trains(client, stops)
    except httpx.HTTPError as exc:
        app.state.path_feed_health = {"total": 1, "ok": 0, "failed": ["PATH"]}
        _note_failure(entry, 502, f"Upstream PATH bridge feed error: {_sanitize_upstream(exc)}")
        return
    except DecodeError:
        # HTTP 200 with a non-protobuf body (bridge error page, proxy HTML).
        app.state.path_feed_health = {"total": 1, "ok": 0, "failed": ["PATH"]}
        _note_failure(entry, 502, "Upstream PATH bridge feed returned undecodable data")
        return
    # A nonzero unresolved count means the bridge referenced station ids the
    # static stops table lacks (a renumber, or a lagging 13a snapshot): those
    # trains are silently absent from the map, so the condition must be
    # operator-visible. Logged only when it APPEARS or CLEARS (comparing
    # against the previous poll's health, so a persistent drift never spams
    # the 20s loop, matching _set_static_status's transition-only rule) and
    # carried on path_feed_health so /api/status shows it while it lasts. A
    # failed poll in between resets the memory (its health dict has no count),
    # so the warning refires after an outage: acceptable, it is still news.
    was_drifting = bool((getattr(app.state, "path_feed_health", None) or {}).get("unresolved"))
    if bool(unresolved) != was_drifting:
        if unresolved:
            logger.warning(
                "PATH decode is dropping %d entities whose station ids are missing "
                "from the static stops table (bridge and static GTFS may disagree)",
                unresolved,
            )
        else:
            logger.info("PATH unknown-station drops cleared")
    app.state.path_feed_health = {"total": 1, "ok": 1, "failed": [], "unresolved": unresolved}
    # feed_timestamp is the bridge's write time; it advances even when the
    # content is a re-served identical generation, which is NORMAL for PATH
    # (the bridge regenerates faster than the upstream refreshes), so content
    # sameness across polls is never treated as staleness.
    # Thread the decode through the synthetic identity matcher: the served
    # trains carry a stable `id` (and anchors on an advance) instead of the
    # bridge's unstable trip hash, which never leaves the backend.
    served, app.state.path_identity = match_path_identities(
        app.state.path_identity,
        trains,
        getattr(app.state, "path_station_order", None) or {},
    )
    entry.update(data=served, fetched_at=time.time(), feed_timestamp=feed_timestamp, error=None)
    # Replace the arrivals index only on success, so a failed poll keeps the
    # last-known arrivals on the same fetched_at, consistent with the cache.
    app.state.path_arrivals = arrivals


async def _refresh_ferry(app: FastAPI, client: httpx.AsyncClient) -> None:
    """Refresh the NYC Ferry boats + arrivals from the two realtime endpoints.

    Same cache contract as the other systems with ONE deliberate divergence,
    flagged for reviewers: an EMPTY successful poll REPLACES the boats. NYC Ferry
    stops running roughly 22:30-06:00 ET, and the feeds then return zero entities
    with fresh headers. That empty decode is VALID DATA (the boats went home), so
    it replaces the cache like any other successful poll and the map correctly
    empties; only a FAILED poll (HTTP or decode error, below) keeps the last-known
    boats via _note_failure. This is the standard success-replaces /
    failure-retains split, but it matters more here than for a rail system, where
    an empty feed would be unusual: for ferries an empty feed is the nightly norm
    and must never linger as stale daytime boats.

    ferry_static is a hard dependency: the decode joins each realtime trip_id
    through 14a's static trip -> route map, so the poll waits for that warmup, the
    same quiet warming path the PATH refresher takes while its static loads.
    """
    entry = app.state.feed_cache["ferry"]
    if getattr(app.state, "ferry_static_status", None) != "ready":
        # 14a static not ready: the trip -> route join cannot run. Same log=False
        # warming path as the PATH/subway refreshers (the single transition log
        # belongs to _set_static_status, not the 20s poll loop).
        _note_failure(
            entry,
            503,
            "Static NYC Ferry GTFS is still loading; it will retry automatically. "
            "Try again shortly.",
            log=False,
        )
        return
    try:
        boats, arrivals, feed_timestamp = await main.fetch_ferry_data(
            client, getattr(app.state, "ferry_static", {})
        )
    except httpx.HTTPError as exc:
        app.state.ferry_feed_health = {"total": 1, "ok": 0, "failed": ["ferry"]}
        _note_failure(entry, 502, f"Upstream NYC Ferry feed error: {_sanitize_upstream(exc)}")
        return
    except DecodeError:
        # HTTP 200 with a non-protobuf body (CDN error page, maintenance HTML).
        app.state.ferry_feed_health = {"total": 1, "ok": 0, "failed": ["ferry"]}
        _note_failure(entry, 502, "Upstream NYC Ferry feed returned undecodable data")
        return
    app.state.ferry_feed_health = {"total": 1, "ok": 1, "failed": []}
    # feed_timestamp is the VehiclePositions header time (the boats' feed); a
    # failed poll keeps the last-known timestamp, same as the other caches. An
    # empty boats list REPLACES the cache here on purpose (see the docstring).
    entry.update(data=boats, fetched_at=time.time(), feed_timestamp=feed_timestamp, error=None)
    # Replace the arrivals index only on success, so a failed poll keeps the
    # last-known arrivals on the same fetched_at, consistent with the cache.
    app.state.ferry_arrivals = arrivals


async def _refresh_njt(app: FastAPI, client: httpx.AsyncClient) -> None:
    """Refresh the NJ Transit trains + arrivals from the TripUpdates feed.

    THE CLIENT ARGUMENT IS UNUSED, and that is the point rather than an oversight.
    Every other refresher fetches with the poll loop's shared httpx client; NJT
    goes through njt_auth.njt_post, because the token cache behind that door is
    what makes this poller, the alerts poller and the static loader share ONE
    token and produce exactly one re-mint when it expires. A direct client.post
    here would route around the single-flight lock and turn one expiry into three
    mints, against a rate limit NJ Transit does not publish. The parameter stays
    so this refresher has the same signature as its five siblings and the registry
    below needs no special case.

    NOT-CONFIGURED EXTENDS FROM 15a UNCHANGED. With no credentials the loader
    never reached ready, so the static guard below already short-circuits this
    poll before any network call; njt_post would refuse anyway. The two together
    are why an unconfigured deployment makes zero NJT requests of any kind rather
    than merely failing them quietly.

    Same cache contract as the other systems with the ferry's deliberate
    divergence, and for a sharper reason: an EMPTY successful poll REPLACES the
    trains. The overnight probe recorded a 13-byte valid feed with no entities, so
    zero trains at 03:00 is the correct answer; retaining the evening's trains
    through the night would be a map full of ghosts. Only a FAILED poll keeps the
    last-known trains.
    """
    entry = app.state.feed_cache["njt"]
    if getattr(app.state, "njt_static_status", None) != "ready":
        # Not ready covers BOTH the warming case and the not-configured one, and
        # they must read differently to an operator. "not-configured" is terminal
        # and says so; anything else is the ordinary quiet warming path the PATH
        # and ferry refreshers take (log=False, because the single transition log
        # belongs to _set_static_status rather than to a 30s poll loop).
        if getattr(app.state, "njt_static_status", None) == "not-configured":
            _note_failure(
                entry,
                503,
                "NJ Transit is not configured (NJT_USERNAME/NJT_PASSWORD are unset); "
                "no realtime poll is attempted.",
                log=False,
            )
        else:
            _note_failure(
                entry,
                503,
                "Static NJ Transit GTFS is still loading; it will retry automatically. "
                "Try again shortly.",
                log=False,
            )
        return
    try:
        trains, arrivals, feed_timestamp, warnings = await main.fetch_njt_trains(
            getattr(app.state, "njt_stops", None) or {},
            getattr(app.state, "njt_trips", None) or {},
        )
    except njt_auth.NjtNotConfigured as exc:
        # Unreachable behind the status guard above, and handled anyway so the
        # distinct state cannot degrade into a generic 500 two frames from where
        # the distinction was made.
        app.state.njt_feed_health = {"total": 1, "ok": 0, "failed": ["njt"]}
        _note_failure(entry, 503, str(exc), log=False)
        return
    except njt_auth.NjtAuthError as exc:
        # A rejected token AFTER the one permitted re-mint, or a failed mint. Not
        # a 500: the upstream answered, it just refused us.
        app.state.njt_feed_health = {"total": 1, "ok": 0, "failed": ["njt"]}
        _note_failure(entry, 502, f"NJ Transit rejected our credentials: {_sanitize_upstream(exc)}")
        return
    except (njt_auth.NjtUpstreamError, httpx.HTTPError) as exc:
        app.state.njt_feed_health = {"total": 1, "ok": 0, "failed": ["njt"]}
        _note_failure(entry, 502, f"Upstream NJ Transit feed error: {_sanitize_upstream(exc)}")
        return
    except DecodeError:
        # HTTP 200 with a non-protobuf body (an error page, a proxy's HTML).
        app.state.njt_feed_health = {"total": 1, "ok": 0, "failed": ["njt"]}
        _note_failure(entry, 502, "Upstream NJ Transit feed returned undecodable data")
        return
    njt_feed.log_cross_check(warnings)
    app.state.njt_feed_health = {
        "total": 1,
        "ok": 1,
        "failed": [],
        # The entity.id / trip_short_name cross-check count, which the probe
        # measured at 745/745 agreement. Surfaced on /api/status rather than only
        # logged, so a drift that starts is visible without reading logs.
        "cross_check_failures": len(warnings),
    }
    now = time.time()
    entry.update(
        data=trains,
        fetched_at=now,
        feed_timestamp=feed_timestamp,
        error=None,
        # THE C2 PER-SYSTEM BLOCK, single-entry. Written on every successful poll so
        # the envelope's block and its top-level fetched_at agree while healthy;
        # a failed poll leaves this untouched, which is exactly the divergence the
        # client dims on.
        systems=_system_freshness(entry.get("systems"), [njt_feed.SYSTEM], [], {}, now),
    )
    # Replace the arrivals index only on success, so a failed poll keeps the
    # last-known arrivals on the same fetched_at, consistent with the cache.
    app.state.njt_arrivals = arrivals


async def _poll_feeds(app: FastAPI) -> None:
    """Refresh the feeds every POLL_INTERVAL_S for the app's lifetime.

    One shared client for the task's lifetime; per-feed errors are recorded
    in the cache, and anything unexpected is logged rather than allowed to
    kill the loop.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                # A CYCLE ENDS ONLY WHEN EVERY CHILD HAS ENDED (C4). asyncio.TaskGroup
                # is what guarantees that: the `async with` does not exit until all
                # five tasks have completed, so a generation can never overlap its
                # successor, and cancelling this task propagates into whatever is
                # in flight instead of leaving it detached.
                #
                # THE COMMENT THAT USED TO BE HERE WAS WRONG, and the bug was real.
                # It claimed gather "keeps NO return_exceptions, so an unexpected
                # error still fails the whole cycle", implying the cycle ended there.
                # gather does not cancel or await its siblings when one child raises:
                # the first exception propagated, this loop logged it and slept, and
                # the four orphans kept running into the NEXT generation, where they
                # wrote their stale results into app.state after the new cycle had
                # already started.
                #
                # THE INVARIANT LIVES IN TWO HALVES, and it is worth being exact
                # about which half does what, because the obvious reading is wrong.
                #
                # _total_refresh is what actually removes the orphan today: with
                # children that cannot raise, even gather waits for all five, because
                # gather only abandons siblings when one of them RAISES. The fix for
                # the audited bug is the totality, not the group. (Verified by
                # mutation: with the wrapper in place and gather restored, the overlap
                # tests still pass.)
                #
                # The TaskGroup is the STRUCTURAL BACKSTOP for when that assumption
                # breaks. A BaseException (SystemExit, KeyboardInterrupt) passes
                # straight through the wrapper by design, and a future child added
                # without the wrapper would not be caught by review alone. In either
                # case a group cancels its siblings and does not exit until they have
                # stopped, where gather would detach them to write into the next
                # generation. It also keeps the wrapper a POLICY choice (record this
                # as the system's failure) rather than the only thing standing
                # between the loop and a leak.
                #
                # Together: every source runs to completion, every failure is recorded
                # against its own entry, and no child outlives the generation that
                # created it under any exit path.
                #
                # Each refresh keeps its OWN deadline (see _bounded_refresh) so a
                # wedged upstream still bounds only its system, exactly as R2 left it.
                cache = app.state.feed_cache
                async with asyncio.TaskGroup() as group:
                    # NJ TRANSIT'S VEHICLE POSITIONS FEED IS NOT HERE, AND THAT IS
                    # A DECISION WITH NUMBERS BEHIND IT (15b). getVehiclePositions
                    # is not fetched, not parsed, and not modelled. The 2026-08-05
                    # probes measured, at peak: 89% of coordinates FROZEN, a worst
                    # observed age of 3h18m, and a CANCELED train still broadcasting
                    # a position. Serving any of that would put confidently wrong
                    # trains on the map, which is strictly worse than the
                    # schedule-derived placement the TripUpdates feed supports (and
                    # which /api/njt-trains labels as derived through `status`).
                    #
                    # This comment is here, at the registry, because this is where
                    # the next person adding a feed will be standing. If you are
                    # about to helpfully add it: the numbers above are the reason
                    # not to, and they have not changed unless you re-probed.
                    # occupancy_status is the ONE field that could ever earn a gated
                    # return, since it has no position to be wrong about; that is a
                    # ledger followup, not code.
                    for name, refresh in (
                        ("buses", _refresh_buses),
                        ("subways", _refresh_subways),
                        ("railroads", _refresh_railroads),
                        ("path", _refresh_path),
                        ("ferry", _refresh_ferry),
                        ("njt", _refresh_njt),
                    ):
                        entry = cache[name]
                        group.create_task(
                            _total_refresh(
                                name,
                                entry,
                                _bounded_refresh(entry, refresh(app, client)),
                                _feed_degrader(app, name, entry),
                            )
                        )
            except Exception:
                # DEFENSIVE, AND IT SHOULD BE UNREACHABLE. Every child is total, so
                # what is left is a bug in this cycle's own plumbing (a KeyError on
                # the cache map, a TaskGroup misuse). It stays because the loop dying
                # silently is the worst outcome available: the app would keep serving
                # last-known data forever with nothing to say why.
                #
                # This does NOT catch shutdown. CancelledError is a BaseException, and
                # a TaskGroup that is cancelled raises BaseExceptionGroup rather than
                # ExceptionGroup, so neither is an Exception and both pass straight
                # through to the lifespan that owns them.
                logger.exception("feed poll cycle failed unexpectedly")
            await asyncio.sleep(POLL_INTERVAL_S)


def _apply_alert_generation(
    entry: dict,
    fresh_alerts: list[dict],
    failed_systems: set[str],
    now: float,
    *,
    write_index: bool = True,
) -> None:
    """Merge this poll's fresh alerts into the served index and rewrite per-system
    health. Shared by the partial-failure and the total-outage paths so the expiry
    re-filter, the retention cap and the health surface behave identically in both:
    a total outage is simply the case where nothing is fresh and EVERY system failed.

    Writes only the CONTENT fields (alerts, active) plus health. fetched_at, error
    and suppressed stay with the caller because they differ by path: fetched_at means
    "the last poll that decoded", so a total outage must not advance it.

    write_index=False rewrites health WITHOUT touching the served index, for the one
    caller whose index has never filled. Health must still be written there: a process
    that starts while every feed is down is exactly when an operator needs to see five
    degraded systems, and skipping this left /api/status reporting last_error null for
    all of them, i.e. the same "everything looks healthy while nothing works" that this
    change exists to remove. The index itself must stay None, because writing the
    merge's empty result would turn a warming deployment into one confidently serving
    "no active alerts".

    SAFE TO RUN ON CONSECUTIVE FAILING POLLS, which is what the total-outage path
    needs. The retention cap compares `now` against the retention START threaded out
    of health (prev_retained_since below), not against the previous poll, and the
    expiry re-filter drops on absolute ends_at. Both are therefore idempotent: a
    partial failure following a total one re-runs the same arithmetic over an
    already-shrunk index and reaches the same answer. No retention is double-charged
    and no retention clock restarts.
    """
    health = entry["health"]
    # Thread the prior retention clock through the pure merge so the cap measures
    # total time down, not time-since-this-poll.
    prev_retained_since = {
        system: h["retained_since"]
        for system, h in health.items()
        if h["retained_since"] is not None
    }
    merged, retained_since = merge_alert_generations(
        entry["alerts"],
        fresh_alerts,
        failed_systems,
        prev_retained_since,
        now,
        ALERT_RETENTION_MAX_S,
    )
    for system, h in health.items():
        if system in failed_systems:
            # No per-system upstream string exists to sanitize: fetch_service_alerts'
            # fixed signature returns only the failed feed KEYS, not their errors, so
            # the marker is generic (and URL-free by construction). fresh_at is kept
            # so an operator can see how long ago the system last decoded.
            h["last_error"] = {"status": 502, "detail": "alert feed unavailable this poll"}
            h["retained_since"] = retained_since.get(system)
        else:
            h["fresh_at"] = now
            h["retained_since"] = None
            h["last_error"] = None
    if write_index:
        entry.update(alerts=merged, active=len(merged))


async def _refresh_alerts(app: FastAPI, client: httpx.AsyncClient) -> None:
    """Refresh the active-alerts index. Same cache contract as the feeds: a failed
    poll keeps the last-known index and its fetched_at (the error is recorded but
    only surfaces to clients while the index has never filled).

    THE INDEX CONTENT MAY ALSO SHRINK ON A FAILED POLL. This used to say the index is
    replaced only on a poll that decoded, and that was the finding: a total outage
    left the index BYTE-FROZEN, so alerts that expired mid-outage stayed visible,
    the retention cap never fired, and per-system health kept reporting its last
    happy value while /api/alerts served the whole thing as a 200. Now a failed poll
    still never ADDS anything (there is nothing fresh to add) but it does re-run the
    expiry re-filter and the retention cap over the existing index, so content can
    shrink. That shrinkage is honesty, not data loss: what drops is exactly the
    alerts whose own ends_at has passed, plus systems held past
    ALERT_RETENTION_MAX_S, and the alternative is showing riders alerts that have
    demonstrably finished.

    A partial failure (some feeds down, not all) is still a SUCCESSFUL poll, but it
    no longer silently drops the down systems' alerts. It USED TO: fetch_service_alerts
    returns only the systems that decoded, so replacing the index wholesale deleted a
    down system's alerts while recording success, an asymmetry with the railroad
    arrivals that already retain per system. Now the poll carries the down systems'
    alerts forward through merge_alert_generations (bounded by an activity re-filter
    and a retention cap), and records per-system health so the partial outage is
    visible in /api/status even though the poll succeeds.

    On the all-feeds-failed path the poll-level 502 is still recorded and fetched_at
    still holds the last poll that decoded, but per-system health is no longer left at
    its last partial-poll value: every system is marked failed, so degraded_systems
    tells the truth during a total outage. That closes the blind spot AT THE SOURCE
    which R4's monitor had to work around by consulting the poll age first, because
    the frozen per-system map used to read fully healthy while every feed was down."""
    entry = app.state.alerts_cache
    try:
        # THIS REFRESHER OWNS ITS OWN DEADLINE rather than riding _bounded_refresh the
        # way the feed refreshers do (see _poll_alerts, which calls this directly).
        # REVIEW FIX: the generic wrapper catches the TimeoutError OUTSIDE this
        # function, so it could only call _note_failure; the total-outage machinery
        # below never ran and the index stayed byte-frozen. A trickling alerts feed is
        # exactly the shape REFRESH_DEADLINE_S exists to catch, so that route left the
        # headline defect of this change unfixed for the most likely way it happens.
        # Bounding the fetch here means both total-outage shapes, an all-feeds error
        # and a deadline, land on one path. One deadline, not two: nesting this inside
        # _bounded_refresh's identical 45s window would be a race over which fires.
        # Everything after this await is synchronous, so bounding the fetch bounds the
        # whole refresh.
        async with asyncio.timeout(REFRESH_DEADLINE_S):
            alerts, suppressed, failed = await main.fetch_service_alerts(client)
    except (RuntimeError, TimeoutError) as exc:
        # Every alert feed failed this poll, or the whole fetch outran the deadline.
        # Either way keep the last-known index. Unlike the single-fetch refreshers
        # (buses/subways), there is no httpx.HTTPError to catch here:
        # fetch_service_alerts gathers every feed with return_exceptions=True, so a
        # per-feed HTTP or decode error is captured inside it and only the all-failed
        # RuntimeError ever propagates. CancelledError is a BaseException, so a real
        # cancellation (app shutdown) still passes straight through.
        if isinstance(exc, TimeoutError):
            # Same status and wording _bounded_refresh used, so /api/status and the R1
            # stale surfaces read identically to before for this failure.
            _note_failure(
                entry,
                504,
                f"Upstream did not complete within the {REFRESH_DEADLINE_S}s refresh "
                "deadline; keeping last-known data.",
            )
        else:
            _note_failure(entry, 502, _sanitize_upstream(exc))
        if entry["alerts"] is None:
            # NEVER FILLED: there is no index to re-filter, and writing the merge's
            # empty result would flip /api/alerts from its warming state to a 200
            # serving "no active alerts", which is the same false-green this change
            # exists to remove. But per-system health is still written, or a process
            # that started while every feed was down would report five perfectly
            # healthy systems for as long as the outage lasted.
            _apply_alert_generation(entry, [], set(entry["health"]), time.time(), write_index=False)
            return
        # Run the ordinary machinery with nothing fresh and every system failed.
        # health is seeded from ALERT_FEED_URLS (cache._fresh_alerts_entry), so its
        # key set IS the full system list; taking it from the map we are about to
        # rewrite keeps the failed set and the health write consistent by
        # construction rather than by a second import agreeing with the first.
        _apply_alert_generation(entry, [], set(entry["health"]), time.time())
        # suppressed is deliberately left alone: like fetched_at it describes the last
        # poll that DECODED, and this poll decoded nothing to recount. NOTE the
        # asymmetry this creates in /api/status, which the review flagged: `active` is
        # recomputed here from the shrunk index while `suppressed_planned` still comes
        # from the last decode, so during a long outage the two counts can be up to
        # ALERT_RETENTION_MAX_S apart. That is the lesser of the two evils. `active`
        # MUST match what /api/alerts is actually serving or status would misreport the
        # live index, whereas suppressed_planned counts not-yet-active work that only a
        # decode can observe, so freezing it is the honest option. fetched_at is the
        # field that dates both, which is why it must not advance on this path.
        return

    now = time.time()
    _apply_alert_generation(entry, alerts, set(failed), now)
    entry.update(fetched_at=now, error=None, suppressed=suppressed)


async def _poll_alerts(app: FastAPI) -> None:
    """Refresh the alerts index every ALERT_POLL_INTERVAL_S for the app's lifetime.

    A separate task from _poll_feeds (own client, slower cadence): alerts change
    slowly and the feeds are large, and keeping it independent means an alert-feed
    outage never delays a position poll. Anything unexpected is logged rather than
    allowed to kill the loop, matching _poll_feeds.

    NO TASKGROUP HERE, DELIBERATELY (C4). This cycle has exactly ONE child, so the
    await below already IS the join: there is no sibling to orphan and no generation
    that can overlap its successor. What C4 changes here is the other half, the
    child's totality: an unexpected error is now recorded against the alerts cache
    rather than escaping into the cycle handler, which used to log it while leaving
    /api/status reporting the alerts feed as fine. Adding a group around a single
    child would be ceremony that implies a guarantee the await already provides."""
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                # Same whole-task deadline as the feed refreshers (REFRESH_DEADLINE_S
                # < the 60s alerts cadence): a trickling alerts feed can no longer
                # wedge this loop forever, and a timeout keeps the last-known index.
                # The deadline lives INSIDE _refresh_alerts rather than in
                # _bounded_refresh here, because a timeout has to run the alert-specific
                # outage machinery (expiry re-filter, retention cap, per-system health)
                # and the generic wrapper cannot: it catches the timeout outside the
                # refresher, where the only thing it can do is record the error.
                entry = app.state.alerts_cache
                await _total_refresh(
                    "alerts",
                    entry,
                    _refresh_alerts(app, client),
                    # An unclassified failure is a TOTAL outage as far as the health
                    # map is concerned, and running the shared generation applier is
                    # what keeps the expiry re-filter and the retention cap honest on
                    # a poll that died halfway. write_index is False while the index
                    # has never filled, for the same reason the total-outage path
                    # gives: writing the merge's empty result would turn a warming
                    # deployment into one confidently serving "no active alerts".
                    lambda: _apply_alert_generation(
                        entry,
                        [],
                        set(ALERT_FEED_URLS),
                        time.time(),
                        write_index=entry["alerts"] is not None,
                    ),
                )
            except Exception:
                # Defensive and, as in _poll_feeds, unreachable by construction now
                # that the child is total: what is left is a bug in this cycle itself.
                # Shutdown passes through untouched (CancelledError is a
                # BaseException, and _total_refresh re-raises it).
                logger.exception("alert poll cycle failed unexpectedly")
            await asyncio.sleep(ALERT_POLL_INTERVAL_S)
