"""Pydantic response models: make API shape drift fail loudly.

These document the JSON each endpoint returns and validate it at the response
boundary, so a decode/cache change that drops or mistypes a field surfaces as
a loud 500 (and a test failure) instead of silently reshaping the API. They
are intentionally permissive about EXTRA keys at runtime (an added field is
dropped, not a 500), so production stays resilient; the tests assert the field
sets match the decode output exactly, catching additions in CI instead.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# THE PROVENANCE ENUMERATION (the freshness and provenance contract, section 3.1 of
# docs/design/freshness-contract.md). How a served observation came to exist, for
# every kind of observation this application serves: a GPS position, a placed
# position, an arrival prediction, an alert.
#
#   reported   the provider sent this as observed. A coordinate it reported, a
#              prediction it published, an alert it raised. NOT "live-gps": the
#              value is read by code over all three kinds and a position word
#              would be false of two of them (Q8). The rider-facing string on a
#              fresh reported POSITION is still "live GPS", unchanged; the code
#              value and the rider word answer different questions.
#   estimated  computed by interpolating a prediction between two known points.
#   placed     a stop's own coordinates, from a prediction or the timetable.
#   retained   carried forward from an earlier poll, not in the latest decode.
#              THE ODD MEMBER, deliberately: the other four say how an observation
#              was DERIVED and this says when it was SERVED. It wins the field when
#              both apply, because "not in the current decode" is the fact that
#              changes what a rider should believe (Q3), and SystemFreshness
#              .retained_since carries the timing for anyone who needs it.
#   unknown    provenance could not be determined. The DEFAULT, and the reason the
#              default is not `reported`: an observation reaching a surface through
#              a path that predates this contract must not claim to be reported. It
#              is not a value a decoder should ever reach for on purpose.
#
# THE FIRST CLOSED SET IN THIS FILE EXPRESSED AS A TYPE, AND THE ONLY ONE THAT CAN
# BE. HEALTH_DEGRADED_CODES below is closed too, but as a tuple of str constants a
# reader has to obey rather than a type a checker enforces.
# NjtTrain.status, FerryBoat.status, Alert.effect and Alert.cause all ship as bare
# `str` and that is not an oversight: they are GTFS-RT pass-throughs, so a new enum
# member added upstream would turn a typed field into a 500 on a feed we do not
# control. Provenance is OURS. Every value is written by a decoder in this
# repository, nothing upstream can widen it, and a value outside the set is a bug
# here rather than a surprise from a provider. So it is typed rather than merely
# documented, and mypy catches the typo the comment could not.
#
# NOT TO BE CONFUSED WITH NjtTrain.status, which is a MOTION phase (at-station /
# approaching / in-transit) and says nothing about derivation: all three of those
# are placed positions. A new field named `status` on these models would collide
# with it, which is why this one is named for what it answers.
Provenance = Literal["reported", "estimated", "placed", "retained", "unknown"]


class Vehicle(BaseModel):
    id: str
    route_id: str | None
    latitude: float
    longitude: float
    bearing: float | None
    # THE CONTRACT PAIR, added to every observation-carrying model by 6.1 and
    # explained once here because the shape is identical on all twelve.
    #
    # OPTIONAL-WITH-A-DEFAULT ON THE WIRE, the same convention SubwayFeed.systems
    # states and for the same reason: a client that predates these fields is not
    # broken by them, and a payload built before they existed still validates. The
    # optionality is a COMPAT affordance and not a licence for a decoder to skip
    # them; every decoder fills both, and the field-set locks in test_models.py
    # fail if one stops.
    #
    # observed_at is NULLABLE and that is load-bearing. Metro-North sends no
    # per-observation clock at all (its vehicle.timestamp is a copy of a header
    # that lags two to four minutes), so null is the only honest answer there.
    # Filling it with something computed here would be a rider-facing qualifier
    # rendered from a number no provider sent, which is the failure the whole
    # contract exists to prevent. Which systems get null is DATA, not code: the
    # per-provider policy table is section 3.3 of the design.
    observed_at: float | None = None  # contract 6.1: when the PROVIDER observed this
    provenance: Provenance = "unknown"


class Train(BaseModel):
    trip_id: str
    route_id: str | None
    latitude: float  # next/current station, the static-fallback position
    longitude: float
    stop_id: str
    stop_name: str | None
    direction: str | None
    # Interpolation anchors (v2: route-polyline slice, straight-line fallback).
    prev_lat: float | None
    prev_lon: float | None
    prev_time: float | None  # _stop_time at the previous station (epoch)
    next_time: float | None  # expected time at the next station (epoch)
    observed_at: float | None = None  # contract 6.1: when the PROVIDER observed this
    provenance: Provenance = "unknown"


class RailroadTrain(BaseModel):
    system: str  # "LIRR" or "MNR"
    trip_id: str
    route_id: str | None
    latitude: float  # a GPS fix or a station placement; `provenance` says which
    longitude: float
    bearing: float | None
    train_num: str | None  # vehicle label/id, the rider-facing train number
    # Placement fields. stop_id/stop_name are the next/current station (null for a
    # GPS train); the rest are filled for placed trains, with the anchors carried
    # forward across polls. The model mirrors models.Train.
    stop_id: str | None
    stop_name: str | None
    direction: str | None
    prev_lat: float | None
    prev_lon: float | None
    prev_time: float | None
    next_time: float | None
    # BOTH RAILROAD ANSWERS LIVE ON THIS ONE MODEL, which is why the policy is a
    # table rather than a branch. An LIRR row carries a real observed_at: its feed
    # dates every vehicle independently and dates its predictions too. A
    # Metro-North row carries null, because MNR copies its header onto every
    # vehicle.timestamp and sends no trip-update timestamp at all, so there is
    # nothing to report and inventing one would mark a live fleet stale.
    observed_at: float | None = None  # contract 6.1: when the PROVIDER observed this
    provenance: Provenance = "unknown"


class BusFeed(BaseModel):
    # The three freshness timestamps; see THE THREE TIMESTAMPS in cache.py for the
    # canonical contract. feed_timestamp = upstream generation, fetched_at = our
    # last successful poll, served_at = this response's build time (moves while
    # fetched_at holds, so a stuck poller is visible).
    fetched_at: float | None  # this server's poll time
    feed_timestamp: float | None  # the feed's content time (MTA's clock)
    served_at: float  # this response's build time (see cache.py)
    data: list[Vehicle]


class SystemFreshness(BaseModel):
    """One subsystem's own freshness inside an aggregate envelope (C2).

    THE ONE PER-SYSTEM CONTRACT, defined here and consumed everywhere. An
    aggregate endpoint fans out over several upstream systems (subway: 8 feed
    groups; railroad: LIRR + MNR; alerts: 5 systems) and a partial failure is
    still a SUCCESSFUL poll, so the envelope's top-level fetched_at only means
    "this poll ran". It says nothing about whether any particular system's data
    was refreshed. This block is what does: each system reports the age of ITS
    OWN data, and the two timestamps diverge exactly when something is wrong.

    NO ERROR TEXT LIVES HERE, deliberately. `ok` plus the age carry the whole
    rider-facing signal, and sanitized failure detail stays in /api/status, which
    keeps the leak surface exactly where it already was rather than widening it
    to every live envelope.
    """

    # This system's last poll that DECODED. Frozen while the system is failing,
    # which is the entire point: compare it with the envelope's fetched_at to see
    # how far behind this system has fallen.
    fetched_at: float | None
    # THIS SYSTEM'S OWN CONTENT TIME, mirroring the envelope field it is the
    # per-system version of, exactly as fetched_at above mirrors the envelope's.
    #
    # WITHOUT IT THE OTHER FOUR FIELDS CANNOT EXPRESS AN OLD-BUT-SUCCESSFUL FEED,
    # which is the defect F03 named. Every one of them describes OUR RELATIONSHIP
    # WITH THE PROVIDER: when we last decoded it, whether it is failing, whether we
    # are carrying it forward, which routes it covers. None is about the age of
    # what the provider SENT, so a system can report ok=True, fetched_at one second
    # ago and retained_since None while serving ten-minute-old content, and the
    # reproduction measures exactly that. fetched_at minus this is _feed_age
    # applied per system instead of per envelope.
    #
    # NULL IS A REAL ANSWER AND NOT A HOLE. A system whose header is not a usable
    # freshness signal reports None here rather than a number that would mislead:
    # Metro-North is the standing case (feeds.RAILROAD_FRESHNESS_SYSTEMS states the
    # exclusion once and this block inherits it rather than restating it), and so is
    # any system that has not yet decoded anything.
    feed_timestamp: float | None = None
    # False while the system failed its most recent poll. NOTE the deliberate
    # difference from *FeedHealth.ok in this module, which is an integer COUNT of
    # healthy feeds; this is a per-system boolean. They are different models with
    # different audiences: those are operator counts on /api/status, this is the
    # rider-facing per-system flag on the live envelopes.
    ok: bool
    # Set while this system's data is being carried forward from its last good
    # poll. Null when the system is fresh AND null once the retention cap has
    # dropped the data, so it is not the complement of `ok`: a long-failed system
    # reports ok=False with retained_since=None and no data at all.
    retained_since: float | None
    # WHICH ROUTES THIS SYSTEM'S CURRENT DATA COVERS. Populated by the SUBWAY only,
    # and it is not a freshness field: it is the join key the client needs to point
    # this block at the markers it describes. A subway train carries a route_id and
    # nothing that names its feed group, so without this the client could know that
    # the ACE group is stale and still have no way to tell which trains were its.
    # The alternative was duplicating the backend's group table in JavaScript, which
    # would then drift from SUBWAY_FEED_URLS silently.
    #
    # Derived per poll from the by-group partition (the routes actually present in
    # this system's served data), so it needs no hand-maintained table and stays
    # true for retained data: a carried-forward group still lists its routes, and a
    # group the retention cap has emptied lists none, which is correct because it has
    # no markers left to describe. Null on the railroad and alerts blocks, whose
    # entities already carry their own system name.
    routes: list[str] | None = None


class SubwayFeed(BaseModel):
    fetched_at: float | None
    feed_timestamp: float | None  # oldest content time across subway feeds
    served_at: float  # this response's build time (see cache.py)
    data: list[Train]
    # Keyed by feed group ("ACE", "BDFM", ...). Optional so the field can be
    # added without breaking a client that predates it; absent only on an
    # envelope built before the first poll recorded any group.
    systems: dict[str, SystemFreshness] | None = None


class RailroadFeed(BaseModel):
    fetched_at: float | None
    # LIRR's feed-generation time; MNR's header is a lagging shared clock that
    # does not track publish time, so it is not used as a freshness signal (see
    # feeds.RAILROAD_FRESHNESS_SYSTEMS).
    feed_timestamp: float | None
    served_at: float  # this response's build time (see cache.py)
    data: list[RailroadTrain]
    systems: dict[str, SystemFreshness] | None = None  # keyed "LIRR" / "MNR"


class RouteGeometry(BaseModel):
    route: str
    directions: list[list[list[float]]]


class SubwayRoute(BaseModel):
    route: str
    polylines: list[list[list[float]]]


class RailroadRoute(BaseModel):
    system: str  # "LIRR" or "MNR" (route ids collide across systems)
    route: str
    name: str | None  # rider-facing route name from routes.txt, null when absent
    polylines: list[list[list[float]]]


class SubwayStop(BaseModel):
    id: str
    name: str | None
    lat: float
    lon: float
    # Route ids serving this station (H5), derived from stop_times -> trips; the
    # station popup joins route-scoped alerts for these. Defaults to [] so an
    # older client and a pre-index warmup both stay valid.
    routes: list[str] = []


class RailroadStop(BaseModel):
    system: str  # "LIRR" or "MNR" (stop_id namespaces are independent)
    id: str
    name: str | None
    lat: float
    lon: float
    routes: list[str] = []  # route ids serving this stop (H5)


class Arrival(BaseModel):
    route_id: str | None
    trip_id: str
    arrival: float  # absolute epoch seconds
    # A PREDICTION IS AN OBSERVATION, which is the whole reason these rows carry
    # the pair: the countdown a rider reads is arithmetic on `arrival`, so it is
    # only as current as the prediction behind it, and nothing in this payload used
    # to say when that was. No subway trip update carries a timestamp of its own
    # (0 of 160 on the committed capture), so observed_at here is the header of the
    # FEED GROUP that produced the row.
    observed_at: float | None = None  # contract 6.1: when the PROVIDER observed this
    provenance: Provenance = "unknown"


class RailroadArrival(BaseModel):
    route_id: str | None
    trip_id: str
    arrival: float  # absolute epoch seconds
    train_num: str | None  # rider-facing train number, null when no vehicle joins
    # LIRR rows carry the trip update's own timestamp (127 of 132 on the committed
    # capture carry one); Metro-North rows carry null, for the same reason its
    # trains do.
    observed_at: float | None = None  # contract 6.1: when the PROVIDER observed this
    provenance: Provenance = "unknown"


class StationArrivals(BaseModel):
    fetched_at: float | None
    station_id: str
    station_name: str | None
    # Keyed by "Northbound" / "Southbound"; both keys always present.
    directions: dict[str, list[Arrival]]
    # THE CLOCKS THE FIVE ARRIVALS ENVELOPES NEVER HAD (contract 4.1). Every vehicle
    # envelope in this module carries the full feed_timestamp / fetched_at /
    # served_at triple and a systems map; all five of these carried fetched_at
    # alone, which is one design error committed five times rather than one system's
    # bug.
    #
    # feed_timestamp is THE CONTENT CLOCK F03 NAMED, and this is the ONE envelope the
    # contributor rule is written for: a subway station is served by several feed
    # groups at once. The worst contributor answers, a group contributing nothing at
    # this station does not participate, and one contributor that cannot be dated
    # makes the answer None rather than letting the others speak for it.
    #
    # served_at exists so a stuck poller is visible (it keeps moving while fetched_at
    # holds; THE THREE TIMESTAMPS in cache.py), and the systems map is what lets a
    # board say WHICH contributor is behind rather than only that one is. All three
    # are optional-with-a-default for the wire reason, and the handlers always set
    # served_at: test_every_arrivals_endpoint_stamps_served_at pins that a served
    # response never carries None for it, on every one of the five.
    feed_timestamp: float | None = None
    served_at: float | None = None
    systems: dict[str, SystemFreshness] | None = None


class RailroadStationArrivals(BaseModel):
    fetched_at: float | None
    system: str
    stop_id: str
    stop_name: str | None
    # Bucket keys are asymmetric and only present when they have trains: LIRR uses
    # "Outbound"/"Inbound" (from direction_id), MNR and direction-less LIRR trips
    # use "Trains". An empty dict means nothing upcoming.
    directions: dict[str, list[RailroadArrival]]
    # THE CLOCKS THE FIVE ARRIVALS ENVELOPES NEVER HAD (contract 4.1). Every vehicle
    # envelope in this module carries the full feed_timestamp / fetched_at /
    # served_at triple and a systems map; all five of these carried fetched_at
    # alone, which is one design error committed five times rather than one system's
    # bug.
    #
    # feed_timestamp is THE CONTENT CLOCK F03 NAMED. A railroad station belongs to
    # exactly ONE system, so there is no union to take: this is that system's own
    # content time, and it is None for Metro-North, whose header is not a usable
    # freshness signal at all (feeds.RAILROAD_FRESHNESS_SYSTEMS).
    #
    # served_at exists so a stuck poller is visible (it keeps moving while fetched_at
    # holds; THE THREE TIMESTAMPS in cache.py), and the systems map is what lets a
    # board say WHICH contributor is behind rather than only that one is. All three
    # are optional-with-a-default for the wire reason, and the handlers always set
    # served_at: test_every_arrivals_endpoint_stamps_served_at pins that a served
    # response never carries None for it, on every one of the five.
    feed_timestamp: float | None = None
    served_at: float | None = None
    systems: dict[str, SystemFreshness] | None = None


# PATH realtime (13b placement + 13d identity): trains placed at their next
# station from the community bridge feed, plus a per-station arrivals index.
# PATH ids stay in their own namespace (numeric PATH stop ids collide with MTA
# numeric ids across systems). The bridge's own trip ids are UNSTABLE across
# upstream refreshes and display-poor (see path_static's module docstring), so
# 13d dropped them from this payload entirely: `id` is the backend-minted
# synthetic identity (feeds.match_path_identities), stable across polls, which
# the frontend keys its markers on. prev_* is populated only after an observed
# advance (the matcher's branch 2) and drives the same glide contract the
# subway v2 payload feeds trainLatLng; a freshly-minted identity carries null
# anchors and renders placed at its station.
class PathTrain(BaseModel):
    id: str
    route_id: str | None
    latitude: float  # next/current station, the static placement (no GPS in this feed)
    longitude: float
    stop_id: str
    stop_name: str | None
    direction: str | None  # "To New York" / "To New Jersey", null when the feed omits it
    prev_lat: float | None
    prev_lon: float | None
    prev_time: float | None
    next_time: float | None
    # observed_at is the bridge entity's OWN TripUpdate.timestamp, not the
    # envelope's feed_timestamp. The envelope carries the bridge's write time,
    # which advances every regeneration whether or not anything upstream moved
    # (see PathFeed below), so an age computed from it can never fire. The
    # per-entity stamp can, and does: 12 of 55 entities on the committed rush
    # capture are already more than 90 seconds old.
    observed_at: float | None = None  # contract 6.1: when the PROVIDER observed this
    provenance: Provenance = "unknown"


class PathFeed(BaseModel):
    fetched_at: float | None
    # The bridge's WRITE time: it advances every regeneration (~15s) even when
    # the entity content is unchanged, so it signals "bridge alive", not
    # "upstream refreshed". Unchanged content across polls is normal for PATH.
    feed_timestamp: float | None
    served_at: float  # this response's build time (see cache.py)
    trains: list[PathTrain]


class PathArrival(BaseModel):
    # Deliberately NO trip id, unlike RailroadArrival: the bridge's hashes are
    # unstable across upstream refreshes and display-poor, and since the 13d
    # cleanup they appear in no served payload anywhere.
    route_id: str | None
    arrival: float  # absolute epoch seconds
    # The producing entity's own TripUpdate.timestamp, as for PathTrain.
    observed_at: float | None = None  # contract 6.1: when the PROVIDER observed this
    provenance: Provenance = "unknown"


class PathStationArrivals(BaseModel):
    fetched_at: float | None
    stop_id: str
    stop_name: str | None
    # Keys are "To New York" / "To New Jersey" (from direction_id) with
    # "Trains" as the direction-less residual, present only when populated
    # (the railroad bucket discipline); {} means nothing upcoming.
    directions: dict[str, list[PathArrival]]
    # THE CLOCKS THE FIVE ARRIVALS ENVELOPES NEVER HAD (contract 4.1). Every vehicle
    # envelope in this module carries the full feed_timestamp / fetched_at /
    # served_at triple and a systems map; all five of these carried fetched_at
    # alone, which is one design error committed five times rather than one system's
    # bug.
    #
    # feed_timestamp is THE CONTENT CLOCK F03 NAMED, taken from the ROWS rather than
    # from this envelope's siblings: PATH is one feed with no per-system block, and
    # the cache entry's own clock is the bridge's WRITE time, which advances every
    # regeneration whether or not anything upstream moved (see
    # _oldest_row_observed_at).
    #
    # served_at exists so a stuck poller is visible (it keeps moving while fetched_at
    # holds; THE THREE TIMESTAMPS in cache.py), and the systems map is what lets a
    # board say WHICH contributor is behind rather than only that one is. All three
    # are optional-with-a-default for the wire reason, and the handlers always set
    # served_at: test_every_arrivals_endpoint_stamps_served_at pins that a served
    # response never carries None for it, on every one of the five.
    feed_timestamp: float | None = None
    served_at: float | None = None
    systems: dict[str, SystemFreshness] | None = None


class PathFeedHealth(BaseModel):
    total: int  # 1: PATH is a single bridge feed
    ok: int
    failed: list[str]  # ["PATH"] when the last poll failed, else []
    # Entities the last successful poll dropped because NO stop id resolved to
    # a known parent station: nonzero means the bridge and the static stops
    # table disagree (a station renumber or a lagging 13a snapshot) and those
    # trains are silently missing from the map. Defaulted because the
    # failure-branch health dicts carry no count (no decode ran).
    unresolved: int = 0


# PATH static (13a): station markers and route geometry.
class PathStop(BaseModel):
    id: str
    name: str | None
    lat: float
    lon: float
    routes: list[str] = []  # route ids serving this station (H5)


class PathRoute(BaseModel):
    id: str
    name: str | None  # rider-facing route name from routes.txt, null when absent
    color: str | None  # route_color hex (no '#') verbatim from routes.txt
    text_color: str | None  # route_text_color hex, same treatment
    # The modal polyline(s) for the route: one per direction that survives the
    # reverse-direction dedup (usually one), as [[lat, lon], ...] lists.
    shape: list[list[list[float]]]


# NYC Ferry static (14a): station markers and route geometry. Flatter than
# PATH (no parent/child split), and the marker carries a wheelchair flag that
# is display-relevant to a later phase. Ferry stop ids are short numerics that
# collide with MTA and PATH ids, so ferry data stays in its own namespace.
class FerryStop(BaseModel):
    id: str
    name: str | None
    lat: float
    lon: float
    wheelchair: bool  # GTFS wheelchair_boarding == 1 (accessible), else False
    routes: list[str] = []  # route ids serving this dock (H5)


class FerryRoute(BaseModel):
    id: str
    name: str | None  # route_long_name from routes.txt, null when absent
    color: str | None  # route_color hex (no '#') verbatim from routes.txt
    text_color: str | None  # route_text_color hex, same treatment
    # The modal polyline(s) for the route: one per direction that survives the
    # reverse-direction dedup, as [[lat, lon], ...] lists.
    shape: list[list[list[float]]]


# NJ Transit Rail static (15a): station markers from the credentialed RailData
# GTFS. NJT stop ids are small integers (1..176) that collide heavily with MTA,
# PATH and ferry ids (stop_id 112 names four different places across our feeds), so
# NJT data stays in its own namespace like every other system's.
class NjtStop(BaseModel):
    id: str
    name: str | None
    lat: float
    lon: float
    routes: list[str] = []  # route ids serving this station (H5)
    # NO wheelchair FIELD, unlike FerryStop, and the absence is deliberate: NJ
    # Transit's GTFS carries no accessibility data anywhere, and a hardcoded False
    # would read as an affirmative "not accessible" the feed never published.


class NjtRoute(BaseModel):
    """One NJ Transit rail line's drawable geometry (15c).

    Mirrors RailroadRoute and adds the two colour fields, because unlike the LIRR
    and Metro-North feeds this one publishes route_color. There is no `system`
    field: NJ Transit is one system whose route ids are its own namespace, so
    nothing here needs the (system, route) key the railroad model carries.
    """

    route: str
    name: str | None  # long_name, else short_name, else null (from routes.txt)
    # THE FEED'S OWN COLOURS, carried exactly as published. route_color is set on
    # all twelve routes; route_text_color is EMPTY on all twelve (probed
    # 2026-08-05), so text_color is null in practice and a renderer must compute a
    # readable ink itself rather than trusting the feed to supply one. Both stay
    # None-able so a publication that starts filling text_color needs no change
    # here, and neither is defaulted: inventing a colour the feed never published
    # would hide that it said nothing.
    color: str | None
    text_color: str | None
    polylines: list[list[list[float]]]


# NJ Transit Rail realtime (15b). SCHEDULE-DERIVED, never GPS: every position
# below is computed from the TripUpdates feed's own times against 15a's stop
# coordinates, because NJ Transit's vehicle positions feed is deliberately not
# fetched (the reasoning and its numbers are at the poller registry in
# pollers.py). A consumer must treat these as derived, which is what `status`
# makes checkable rather than implicit.
class NjtTrain(BaseModel):
    # The trip_id where there is one (measured stable at 100% across polls), and
    # "njt:<entity.id>" where there is not, which is every ADDED trip. Never empty:
    # the decoder's fallback chain is what a consumer keying a map by this relies on.
    id: str
    trip_id: str
    route_id: str | None
    # Static headsign when the trip joins 15a's index; for an ADDED trip (36 of them
    # in the first capture that caught a disrupted evening, all carrying an empty
    # trip_id) this is synthesized from route plus train number, which is what a
    # departure board would show.
    headsign: str | None
    train_num: str | None  # trip_short_name == entity.id == the train number
    latitude: float
    longitude: float
    # "at-station" while inside the dwell window (arrival <= now < departure),
    # "approaching" before the first listed stop, "in-transit" on the straight
    # segment between two stops. The straight segment is this phase's accepted
    # limit; shape-following is 15c's line-drawing decision.
    status: str
    stop_id: str | None  # where it is, or the stop it is heading for
    stop_name: str | None
    delay: int | None  # seconds, from the feed; absolute times remain authoritative
    # Interpolation anchors, so 15c can glide between polls exactly as it does for
    # every other system. Null while dwelling (there is nothing to glide along).
    prev_lat: float | None
    prev_lon: float | None
    prev_time: float | None
    next_time: float | None
    # observed_at is the TripUpdates HEADER, because NJ Transit dates nothing else:
    # it sends no vehicle feed at all and none of its trip updates carries a
    # timestamp. The header is a good clock (generation every ~11.8s, lag 9s to 23s
    # at peak; THE FRESHNESS BUDGET, DERIVED in feeds/njt.py is the working), which
    # is why this row is age-gated on it while Metro-North's is not gated at all.
    # provenance is `placed` while dwelling and `estimated` on the interpolated
    # segment, which is a DIFFERENT question from `status` above: that is motion,
    # this is derivation, and all three motion states are one or the other of
    # these two.
    observed_at: float | None = None  # contract 6.1: when the PROVIDER observed this
    provenance: Provenance = "unknown"


class NjtFeed(BaseModel):
    fetched_at: float | None
    feed_timestamp: float | None  # the TripUpdates header time
    served_at: float  # this response's build time (see cache.py)
    trains: list[NjtTrain]
    # Keyed "njt", a single-entry block. A degenerate map for one system is
    # deliberate rather than a scalar: C2's contract is that a client reads the
    # same per-system shape from every envelope, and NJ Transit being one system
    # today is not a reason to make its client code special.
    systems: dict[str, SystemFreshness] | None = None


class NjtArrival(BaseModel):
    train_num: str | None
    route_id: str | None
    headsign: str | None
    # Both times, because a departure board shows both and the dwell window that
    # places the train is derived from the pair. Either may be null at an origin
    # or a terminal; a row where both are null is never emitted.
    arrival: float | None
    departure: float | None
    delay: int | None
    trip_id: str
    # The TripUpdates header, as for NjtTrain: nothing else in this feed is dated.
    observed_at: float | None = None  # contract 6.1: when the PROVIDER observed this
    provenance: Provenance = "unknown"


class NjtStationArrivals(BaseModel):
    fetched_at: float | None
    stop_id: str
    stop_name: str | None
    # FLAT and chronological, not bucketed by direction or route: every row
    # carries its own route and headsign, and a board reads by time. CANCELED
    # trips and SKIPPED stops are already excluded upstream in the decoder, so no
    # consumer can reconstruct a phantom from this list.
    arrivals: list[NjtArrival]
    # THE CLOCKS THE FIVE ARRIVALS ENVELOPES NEVER HAD (contract 4.1). Every vehicle
    # envelope in this module carries the full feed_timestamp / fetched_at /
    # served_at triple and a systems map; all five of these carried fetched_at
    # alone, which is one design error committed five times rather than one system's
    # bug.
    #
    # feed_timestamp is THE CONTENT CLOCK F03 NAMED. NJ Transit is one system, so this
    # is its own header, which is the only clock behind every row here and a good one
    # (9s to 23s of lag at peak, measured).
    #
    # served_at exists so a stuck poller is visible (it keeps moving while fetched_at
    # holds; THE THREE TIMESTAMPS in cache.py), and the systems map is what lets a
    # board say WHICH contributor is behind rather than only that one is. All three
    # are optional-with-a-default for the wire reason, and the handlers always set
    # served_at: test_every_arrivals_endpoint_stamps_served_at pins that a served
    # response never carries None for it, on every one of the five.
    feed_timestamp: float | None = None
    served_at: float | None = None
    systems: dict[str, SystemFreshness] | None = None


# NYC Ferry realtime (14b): live GPS boats from the VehiclePositions feed and a
# per-dock arrivals index from the TripUpdates feed. Both feeds carry an empty
# route_id, so route_id is recovered by joining trip_id through 14a's static
# trip -> route map; a boat whose trip_id does not join keeps its position with
# route_id null (never dropped over a metadata miss). Ferry ids stay in their own
# namespace (short numerics collide with MTA and PATH ids).
class FerryBoat(BaseModel):
    id: str  # vehicle descriptor id, stable across polls
    label: str | None  # hull name (e.g. "H201"), null when absent
    trip_id: str  # a real, stable schedule id (unlike PATH's unstable hashes)
    route_id: str | None  # from the static trip -> route join, null on a miss
    latitude: float  # real GPS position (not a station projection)
    longitude: float
    # Raw feed speed, unit undocumented (0-13 observed, plausibly m/s): passed
    # through without conversion rather than served in a guessed unit. Null when
    # the feed omits it.
    speed: float | None
    # VehicleStopStatus enum name (STOPPED_AT when docked, IN_TRANSIT_TO /
    # INCOMING_AT under way), null when the feed omits it. bearing is deliberately
    # absent: the feed always reports 0.0, so serving it would be a lie.
    status: str | None
    # Q1'S RENAME, AND BOTH KEYS SHIP FOR ONE RELEASE. `updated_at` is the field
    # this contract turned out to have already built, correctly, on exactly one
    # system: the ferry decoder has read vehicle.timestamp and served it under this
    # name since 14b, and no frontend surface has ever read it. 6.1 gives it the
    # contract's name and keeps the old one beside it so a client holding the
    # previous payload shape is not broken by the rename. They carry the SAME
    # VALUE, pinned by a test, and `updated_at` is dropped a release from now.
    updated_at: float | None  # per-vehicle content time; == observed_at (Q1)
    observed_at: float | None = None  # contract 6.1: when the PROVIDER observed this
    provenance: Provenance = "unknown"


class FerryFeed(BaseModel):
    fetched_at: float | None  # this server's poll time
    feed_timestamp: float | None  # the VehiclePositions feed header time
    served_at: float  # this response's build time (see cache.py)
    boats: list[FerryBoat]


class FerryArrival(BaseModel):
    route_id: str | None  # from the static trip -> route join, null on a miss
    trip_id: str  # real schedule id, exposed (unlike PathArrival)
    # Docks report BOTH times (a dwell): arrival is when the boat reaches the
    # dock, departure when it leaves. Either may be null (an origin dock has no
    # arrival, a terminal no departure), but never both on a kept row.
    arrival: float | None
    departure: float | None
    # THE TRIPUPDATES HEADER, NOT THE BOAT CLOCK, and the distinction is the one the
    # audit's F03 remedy named. The two ferry feeds are separate with separate
    # clocks: VehiclePositions dates every boat, TripUpdates dates nothing (0 of 50
    # on the committed capture), and the envelope's feed_timestamp is the
    # VehiclePositions header. A dock row aged against the boat clock would be aged
    # against a feed it did not come from.
    observed_at: float | None = None  # contract 6.1: when the PROVIDER observed this
    provenance: Provenance = "unknown"


class FerryStationArrivals(BaseModel):
    fetched_at: float | None
    stop_id: str
    stop_name: str | None
    # Bucketed BY ROUTE NAME (the feed carries no direction_id, and route reads
    # better at a multi-route dock), present only when populated; an empty dict
    # means nothing upcoming. A join-missed trip lands in a "Ferry" residual bucket.
    routes: dict[str, list[FerryArrival]]
    # THE CLOCKS THE FIVE ARRIVALS ENVELOPES NEVER HAD (contract 4.1). Every vehicle
    # envelope in this module carries the full feed_timestamp / fetched_at /
    # served_at triple and a systems map; all five of these carried fetched_at
    # alone, which is one design error committed five times rather than one system's
    # bug.
    #
    # feed_timestamp is THE CONTENT CLOCK F03 NAMED, taken from the ROWS: the ferry is
    # one all-or-nothing feed pair with no per-system block, and this envelope's own
    # clock is the VehiclePositions header, which dates the BOATS rather than the dock
    # predictions these rows came from (see _oldest_row_observed_at).
    #
    # served_at exists so a stuck poller is visible (it keeps moving while fetched_at
    # holds; THE THREE TIMESTAMPS in cache.py), and the systems map is what lets a
    # board say WHICH contributor is behind rather than only that one is. All three
    # are optional-with-a-default for the wire reason, and the handlers always set
    # served_at: test_every_arrivals_endpoint_stamps_served_at pins that a served
    # response never carries None for it, on every one of the five.
    feed_timestamp: float | None = None
    served_at: float | None = None
    systems: dict[str, SystemFreshness] | None = None


class FerryFeedHealth(BaseModel):
    total: int  # 1: the two ferry endpoints are polled as one all-or-nothing feed
    ok: int
    failed: list[str]  # ["ferry"] when the last poll failed, else []


# AirTrain JFK: a static-only mode (no realtime feed exists). The whole dataset
# ships as one committed fixture, so a single /api/airtrain endpoint returns
# AirTrainData. Headways are SCHEDULED reference bands, never live countdowns.
class AirTrainHeadwayBand(BaseModel):
    start: str  # "HH:MM" service-day local (America/New_York), band start inclusive
    end: str  # "HH:MM", band end exclusive ("24:00" == end of service day)
    headway_min: int  # scheduled minutes between trains in this band (reference, not live)


class AirTrainStation(BaseModel):
    id: str
    name: str
    lat: float
    lon: float


class AirTrainRoute(BaseModel):
    id: str
    name: str
    polyline: list[list[float]]  # ordered [[lat, lon], ...] guideway geometry
    stations: list[str]  # ordered station ids this branch serves
    headways: list[AirTrainHeadwayBand]  # non-overlapping bands covering the service day


class AirTrainData(BaseModel):
    stations: list[AirTrainStation]
    routes: list[AirTrainRoute]


# Service alerts. One polled feed per system (subway/bus/LIRR/MNR/ferry); the
# decode keeps only alerts active now and tags each with its system. Text is verbatim
# from the feed (route tokens like [Q] included); 12b owns rendering.
class Alert(BaseModel):
    id: str
    system: str  # feed this came from: subway | bus | LIRR | MNR | ferry
    header: str | None
    description: str | None
    effect: str  # GTFS-RT Effect enum name (e.g. NO_SERVICE, DETOUR)
    cause: str  # GTFS-RT Cause enum name (e.g. MAINTENANCE)
    routes: list[str]  # deduped route selectors from the informed_entity list
    stops: list[str]  # deduped stop selectors (subway: parent-station ids)
    starts_at: float | None  # covering period start, null when open on the left
    ends_at: float | None  # covering period end, null when open-ended
    # A GTFS-RT alert carries no time of its own, so observed_at is the alert feed's
    # clock. starts_at / ends_at are NOT it: those are facts about the world (when
    # the disruption applies), not about when we were told. provenance is only ever
    # `reported` or `retained` here, because an alert is never derived.
    observed_at: float | None = None  # contract 6.1: when the PROVIDER observed this
    provenance: Provenance = "unknown"


class AlertFeed(BaseModel):
    fetched_at: float | None
    # The alert feeds' content time, which this envelope had never carried: before
    # 6.1 it was the ONLY vehicle-shaped envelope without one, and the FIVE arrivals
    # envelopes defined above had none either.
    feed_timestamp: float | None = None
    served_at: float  # this response's build time (see cache.py)
    alerts: list[Alert]
    # Keyed by alert system ("subway", "bus", "LIRR", "MNR", "ferry"), projected
    # from the health map C1 made truthful. Carried HERE rather than left on
    # /api/status because the client never fetches /api/status: without this, a
    # partial alerts outage was invisible to the rider-facing freshness marker.
    systems: dict[str, SystemFreshness] | None = None


class FeedError(BaseModel):
    status: int
    detail: str


class FeedStatus(BaseModel):
    fetched_at: float | None
    age_s: float | None  # seconds since this server last polled
    feed_age_s: float | None  # how stale the feed CONTENT was at poll time
    last_error: FeedError | None


class BusIndexStatus(BaseModel):
    status: str
    partial: bool


class StaticGtfsStatus(BaseModel):
    mtime: float
    age_s: float


class StaticArchiveStatus(BaseModel):
    """One GTFS static archive's download honesty (C5).

    Answers "is what I am serving current, and if not why". last_promoted_at is
    when a download last passed validation and replaced the cache; a null with a
    nonzero failed_downloads means every publication seen this process has been
    rejected and the cache predates them all. last_download_error is sanitized at
    the source (static_shared.describe_failure): a validation failure names the
    file shape we rejected, anything else is a type name only, never raw upstream
    text that could carry a URL or a key.
    """

    last_promoted_at: float | None
    last_download_error: str | None
    failed_downloads: int


class SubwayFeedHealth(BaseModel):
    total: int  # number of subway feed groups polled
    ok: int  # how many returned usable data on the last poll
    failed: list[str]  # feed-group keys that failed the last poll (e.g. ["BDFM"])


class RailroadFeedHealth(BaseModel):
    total: int  # number of railroad feeds polled (LIRR + MNR)
    ok: int  # how many returned usable data on the last poll
    failed: list[str]  # systems that failed the last poll (e.g. ["MNR"])


class AlertSystemHealth(BaseModel):
    # Per-alert-feed freshness, so a partial outage (one of the alert feeds down)
    # is visible even though the poll as a whole still succeeds.
    fresh_at: float | None  # last poll this system decoded (null before its first)
    # Set while a down system's alerts are being carried forward from its last good
    # poll; null when the system is fresh or once the retention cap has dropped them.
    retained_since: float | None
    # This system's failure this poll, null when fresh. The detail is the FETCH'S OWN
    # reason (a connect error, an HTTP status, a per-feed deadline, an undecodable
    # body), sanitized at the recording boundary, rather than one fixed marker for
    # every way an alert feed can fail.
    last_error: FeedError | None
    # THE ONE SUCCESS WORTH A SENTENCE, and the reason it is a string rather than a
    # bool: an operator looking at zero NJ Transit alerts needs to tell "upstream
    # says there are none" from "upstream said nothing", and only words do that.
    # Null on an ordinary decode and null on a failure, so it is never ambiguous
    # with last_error. Today only NJ Transit's alerts feed can set it: it answers
    # HTTP 200 with a zero-byte body when it has no active rail alerts (observed
    # 2026-09-07; feeds.njt_alerts_served_empty carries the rule and its ambiguity).
    # Defaulted so pre-existing /api/status fixtures validate unchanged.
    served_empty: str | None = None


class AlertStatus(BaseModel):
    fetched_at: float | None
    age_s: float | None  # seconds since the alert poll last succeeded
    last_error: FeedError | None
    active: int  # active alerts currently in the index
    suppressed_planned: int  # not-yet-active planned alerts held back this poll
    # Per-system alert-feed health and the systems failing right now. Defaulted so
    # pre-retention /api/status fixtures validate unchanged; the live handler always
    # populates them once the alerts cache exists.
    systems: dict[str, AlertSystemHealth] | None = None
    degraded_systems: list[str] = []


class NjtMintCooldown(BaseModel):
    """Why the app is not asking NJ Transit for a token right now, and for how long.

    TWO FIELDS BECAUSE TWO READERS. `seconds_remaining` is for anything that does
    arithmetic (a monitor deciding whether a window outlasts its own cadence);
    `detail` is the sentence an operator reads, and it names the failure that
    started the window as well as the time left, so "NJ Transit is down" and "we
    are deliberately not asking" stop looking alike on this surface.

    NO getToken BODY CAN REACH EITHER (Audit 4, F3): the detail is built from the
    failing mint's message, which njt_auth composes from a status code, an
    exception type name or one of its own constants, never from the response.
    """

    seconds_remaining: float
    detail: str


class StatusResponse(BaseModel):
    served_at: float  # this snapshot's build time (see cache.py)
    feeds: dict[str, FeedStatus]
    bus_route_index: BusIndexStatus
    static_subway_gtfs: StaticGtfsStatus | None
    # Background static-GTFS warmup state per group: "loading" | "ready" |
    # "failed" (None only before the lifespan sets it, e.g. a bare test app).
    subway_static: str | None
    railroad_static: str | None
    path_static: str | None
    # Defaulted so pre-14a /api/status fixtures validate unchanged; the live
    # handler always populates it.
    ferry_static: str | None = None
    # NJ Transit (15a). FOUR states here, not three: "loading" | "ready" |
    # "failed" | "not-configured". The fourth is what makes an unconfigured
    # deployment legible: no NJT credentials means no network attempt of any kind,
    # which is a deliberate configuration choice and must never look like a broken
    # upstream. Defaulted so pre-15a fixtures validate unchanged.
    njt_static: str | None = None
    # THE MINT COOLDOWN (Audit 5, F05), and it is a sibling of njt_static rather
    # than a fifth state of it for the reason static_archives is a sibling of the
    # *_static strings: the group state says whether NJ Transit can be SERVED, this
    # says whether a token can currently be ASKED FOR, and the two are independent.
    # A running cooldown with an over-age token in hand leaves njt_static "ready"
    # and this populated, which is the state the fix exists to make possible.
    # Null whenever a mint may be attempted, and defaulted so every pre-F05
    # /api/status fixture validates unchanged.
    njt_mint_cooldown: NjtMintCooldown | None = None
    subway_feeds: SubwayFeedHealth | None
    railroad_feeds: RailroadFeedHealth | None
    path_feeds: PathFeedHealth | None
    # Defaulted so pre-14b /api/status fixtures validate unchanged; the live
    # handler always populates it once the first ferry poll runs.
    ferry_feeds: FerryFeedHealth | None = None
    # Per-ARCHIVE download honesty (C5), keyed "subway" / "railroad_LIRR" /
    # "railroad_MNR" / "path" / "ferry". Deliberately a SIBLING of the
    # *_static warmup strings above rather than an expansion of them: those are
    # plain strings the contract monitor reads by name, and a group is not an
    # archive anyway (the railroad group covers two). Read together they answer
    # "ready, serving an archive from Tuesday, three failed publications since".
    # A key appears only once its archive has been downloaded at least once in
    # this process, so the map is empty on a cold boot with a warm cache.
    static_archives: dict[str, StaticArchiveStatus] = {}
    # Alert feed health (None only before the lifespan sets it, e.g. a bare test app).
    # Defaulted so pre-alerts /api/status callers and fixtures validate unchanged;
    # the live handler always populates it.
    alerts: AlertStatus | None = None


# ---------------------------------------------------------------------------
# Readiness probe (/healthz)
# ---------------------------------------------------------------------------

# THE DEGRADED CODES, defined here beside the model that carries them because
# they ARE the contract. The contract monitor imports this tuple and matches the
# probe's response against it, so a code renamed on one side without the other
# fails at import rather than going quietly unwatched in production, which is the
# exact class of blindness the F1 audit finding was about.
#
# Stable strings rather than an enum: they cross a JSON boundary to a monitor
# that may be running a different revision than the deployment it probes, so the
# wire value has to be the identity.
HEALTH_NO_FEED_FRESH = "no-feed-fresh"
HEALTH_BUS_INDEX_FAILED = "bus-route-index-failed"
HEALTH_SUBWAY_STATIC_FAILED = "subway-static-failed"
HEALTH_FEED_CONTENT_STALE = "feed-content-stale"
HEALTH_SUBWAY_GROUPS_DOWN = "subway-groups-down"
# THE ONE CODE THAT IS NOT ABOUT AN UPSTREAM BEING UNWELL. NJ Transit allows ten
# getToken calls per account per Eastern day (observed 2026-09-02; the budget and
# what spends it are set out at njt_auth.DAILY_MINT_LIMIT) and refuses the
# eleventh. When that happens the NJ Transit layer is dark until Eastern midnight
# and there is nothing wrong with NJ Transit at all, so reporting it as an ordinary
# failure would send whoever is on call hunting an outage that does not exist. It
# gets its own code so the answer reads "the budget is spent", not "something
# broke", and so the fix reads "wait, or stop spending mints" rather than "restart".
HEALTH_NJT_MINT_QUOTA = "njt-mint-quota"

HEALTH_DEGRADED_CODES = (
    HEALTH_NO_FEED_FRESH,
    HEALTH_BUS_INDEX_FAILED,
    HEALTH_SUBWAY_STATIC_FAILED,
    HEALTH_FEED_CONTENT_STALE,
    HEALTH_SUBWAY_GROUPS_DOWN,
    HEALTH_NJT_MINT_QUOTA,
)

# The subset that makes the probe answer 503. READINESS AND SICKNESS ARE TWO
# DIFFERENT QUESTIONS and this tuple is the seam between them.
#
# WHAT THE STATUS CODE ACTUALLY DOES, stated once here because every non-gating
# decision below and in routes/status.py rests on it. Railway calls /healthz while
# a deployment is being PROMOTED and, under a heading for exactly this question,
# its documentation says it "does not monitor the healthcheck endpoint after the
# deployment has gone live"; a container restart is a separate mechanism on the
# restart-policy page and triggers on a process exit, not on a probe. So the status
# code has one consumer and one moment: it decides whether this build is allowed to
# become the live deployment. Once it is live, nothing on the platform reads it
# again, and `degraded` riding a 200 is the only thing that reaches a watcher.
#
# (This comment used to justify the split by saying the platform reboots a container
# whose healthcheck fails. It does not do that after promotion, and the audit
# ledger's N1 records the correction with the citation. The DECISIONS were right and
# are unchanged; what follows is the reason they are right.)
#
# So the question a non-gating code has to answer is not "would a restart help" but
# "is this a reason to refuse this build". A lagging upstream, a dark subway group
# and a spent mint budget are all properties of the WORLD rather than of the code
# being promoted: gating on them would block a good deploy for something no deploy
# can fix, and would keep blocking for as long as the world stayed that way. They
# must still reach a human, which is what the non-gating classification is for. The
# gating set is therefore exactly the three reasons the probe already had before F1,
# and the non-gating codes are new information rather than new behavior.
#
# HEALTH_NJT_MINT_QUOTA IS THE SHARPEST CASE FOR THE SPLIT YET, because gating on it
# has a price and not merely no benefit. A refused promotion is retried, and each
# fresh process mints on its first NJ Transit request, spending another of the ten
# the account has already run out of: the probe would consume the budget it exists
# to report.
HEALTH_GATING_CODES = (
    HEALTH_NO_FEED_FRESH,
    HEALTH_BUS_INDEX_FAILED,
    HEALTH_SUBWAY_STATIC_FAILED,
)


class HealthzResponse(BaseModel):
    """The readiness probe's body.

    `status` and `reasons` are unchanged from before F1: prose for a human
    reading a deploy log, and the thing that decides the status code. `degraded`
    is the machine-readable classification the contract monitor reads, and it is
    a SUPERSET of what drove the status code, because the status code is read only
    while a deployment is being promoted (see HEALTH_GATING_CODES above; a running
    instance's 503 restarts nothing and is polled by nothing). A state that is
    deliberately not a reason to refuse the build therefore has nowhere else to be
    seen, and `degraded` on a 200 is where something watching a LIVE deployment
    finds it.

    ALWAYS PRESENT, EVEN EMPTY, unlike `reasons`. An absent list and an empty one
    have to be distinguishable: empty means this deployment classified itself and
    found nothing wrong, absent means it is running code that predates the
    classification and is therefore unwatched. The monitor treats those
    differently and cannot do so if a healthy deployment omits the key.
    """

    status: str  # "pass" | "fail"
    # Omitted when empty, matching the pre-F1 body exactly.
    reasons: list[str] = []
    degraded: list[str] = []
