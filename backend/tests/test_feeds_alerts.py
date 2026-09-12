"""Unit tests for the service-alerts decode: the pure active-now window logic, the
synthetic-protobuf decode (selectors, translations, active/suppressed split), and
the fetch aggregation (per-feed degrade, all-failed raise)."""

import httpx
import pytest
from google.transit import gtfs_realtime_pb2 as pb

import feeds

# ---- pure active-now window logic (_alert_window_status), tested directly ----

NOW = 1000


def test_window_no_periods_is_always_active():
    # An alert with no active_period at all is always active, with open bounds.
    assert feeds._alert_window_status([], NOW) == ("active", None, None)


def test_window_covering_period_is_active_with_bounds():
    assert feeds._alert_window_status([(900, 1100)], NOW) == ("active", 900, 1100)


def test_window_future_only_is_suppressed_not_ended():
    # Starts after now: excluded from output but counted as planned work.
    assert feeds._alert_window_status([(1100, 1200)], NOW) == ("future", None, None)


def test_window_ended_period_is_ended():
    assert feeds._alert_window_status([(800, 900)], NOW) == ("ended", None, None)


def test_window_open_ended_is_active():
    # The decode maps an end of 0 or unset to None; a None end never ends.
    assert feeds._alert_window_status([(900, None)], NOW) == ("active", 900, None)


def test_window_open_start_is_active():
    # A None start is open on the left (active from minus infinity).
    assert feeds._alert_window_status([(None, 1100)], NOW) == ("active", None, 1100)


def test_window_half_open_boundaries():
    # [start, end): start is inclusive, end is exclusive (matches the GTFS-RT spec).
    assert feeds._alert_window_status([(1000, 2000)], NOW)[0] == "active"  # now == start
    assert feeds._alert_window_status([(500, 1000)], NOW)[0] == "ended"  # now == end


def test_window_multiple_periods_only_one_covers():
    status, start, end = feeds._alert_window_status([(100, 200), (900, 1100)], NOW)
    assert (status, start, end) == ("active", 900, 1100)


def test_window_earliest_covering_period_reported():
    # When several periods cover now, the earliest-starting one is reported.
    status, start, end = feeds._alert_window_status([(900, 1100), (950, 1050)], NOW)
    assert (status, start, end) == ("active", 900, 1100)


def test_window_future_wins_over_ended_for_counting():
    # No period covers now, one elapsed and one upcoming: classified future (counted).
    assert feeds._alert_window_status([(100, 200), (1100, 1200)], NOW)[0] == "future"


# ---- overlapping periods: the effective end is the LATEST, not the earliest ----
# C1 BUG FIX. The end used to come from the earliest-STARTING covering period, so
# whenever periods overlap and the earlier one also ends earlier, ends_at came back
# too small and every downstream expiry check (the retention re-filter during an
# outage, the client's open-ended-first sort) treated a live alert as finished.


def test_window_overlapping_periods_report_the_latest_end():
    # Both cover NOW. The earliest-starting period ends at 1100, the other at 5000.
    # The alert is plainly active until 5000, and reporting 1100 expired it early.
    status, start, end = feeds._alert_window_status([(0, 1100), (950, 5000)], NOW)
    assert status == "active"
    assert start == 0  # starts_at is unchanged: still the earliest covering start
    assert end == 5000  # was 1100 before the fix


def test_window_one_open_ended_period_makes_the_whole_alert_open_ended():
    # A bounded period that covers now alongside an open-ended one: the alert must
    # never self-expire, so a bounded end must not be reported over the open one.
    # Order is reversed in the second call to prove the result is order-independent.
    assert feeds._alert_window_status([(900, 1100), (950, None)], NOW) == ("active", 900, None)
    assert feeds._alert_window_status([(950, None), (900, 1100)], NOW) == ("active", 900, None)


def test_window_not_yet_started_period_does_not_extend_the_effective_end():
    # REVIEW FIX, and this test asserted the OPPOSITE before. The max is over the
    # COVERING periods only. Including not-yet-STARTED ones reached past the overlap
    # bug being fixed and reported an end far beyond the window actually in effect:
    # on real captured MNR data (lmm:planned_work:32622 in fixtures/alerts_mnr.pb,
    # five periods) it overshot by 24 DAYS, and ends_at is a public field the client
    # sorts and displays.
    status, start, end = feeds._alert_window_status([(900, 1100), (2000, 3000)], NOW)
    assert (status, start, end) == ("active", 900, 1100)


def test_window_unstarted_open_ended_period_does_not_make_the_alert_open_ended():
    # The same overreach in its worst form: an open-ended period that has NOT STARTED
    # made ends_at null outright, so the retention re-filter could never expire the
    # alert (only the 1800s cap could) and compareAlerts' open-ended-first rule
    # promoted an alert with 50 seconds left above genuinely indefinite ones in every
    # popup and the banner.
    status, start, end = feeds._alert_window_status([(900, 1050), (2000, None)], NOW)
    assert (status, start, end) == ("active", 900, 1050)


def test_window_covering_open_ended_period_still_makes_the_alert_open_ended():
    # The narrowing must not lose the real case: an open-ended period that DOES cover
    # now still means the alert never self-expires.
    status, start, end = feeds._alert_window_status([(900, 1050), (950, None)], NOW)
    assert (status, start, end) == ("active", 900, None)


def test_window_all_periods_ended_is_still_ended():
    # The latest-end rule must not resurrect a fully elapsed alert: with nothing
    # covering and nothing upcoming, this is "ended" and carries no bounds at all.
    assert feeds._alert_window_status([(100, 200), (300, 400)], NOW) == ("ended", None, None)


# ---- synthetic-protobuf decode (_decode_alerts) ----


def _alert_feed(specs: list[dict]) -> bytes:
    """Serialize a FeedMessage of alert entities from lightweight specs."""
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    for spec in specs:
        entity = feed.entity.add()
        entity.id = spec["id"]
        alert = entity.alert
        for start, end in spec.get("periods", []):
            tr = alert.active_period.add()
            if start is not None:
                tr.start = start
            if end is not None:
                tr.end = end
        for route in spec.get("routes", []):
            alert.informed_entity.add().route_id = route
        for stop in spec.get("stops", []):
            alert.informed_entity.add().stop_id = stop
        for text, lang in spec.get("header", []):
            t = alert.header_text.translation.add()
            t.text, t.language = text, lang
        for text, lang in spec.get("description", []):
            t = alert.description_text.translation.add()
            t.text, t.language = text, lang
        if "effect" in spec:
            alert.effect = spec["effect"]
        if "cause" in spec:
            alert.cause = spec["cause"]
    return feed.SerializeToString()


def test_decode_route_only_and_stop_only_and_both_selectors():
    raw = _alert_feed(
        [
            {"id": "a", "routes": ["Q"], "periods": [(900, None)]},  # route-only
            {"id": "b", "stops": ["R20"], "periods": [(900, None)]},  # stop-only
            {"id": "c", "routes": ["4"], "stops": ["245"], "periods": [(900, None)]},  # both
        ]
    )
    alerts, suppressed, _ = feeds._decode_alerts(raw, "subway", NOW)
    assert suppressed == 0
    by_id = {a["id"]: a for a in alerts}
    assert by_id["a"]["routes"] == ["Q"] and by_id["a"]["stops"] == []
    assert by_id["b"]["routes"] == [] and by_id["b"]["stops"] == ["R20"]
    assert by_id["c"]["routes"] == ["4"] and by_id["c"]["stops"] == ["245"]
    assert all(a["system"] == "subway" for a in alerts)


def test_decode_dedups_selectors_in_first_seen_order():
    raw = _alert_feed(
        [
            {
                "id": "a",
                "routes": ["Q", "N", "Q"],  # Q repeated
                "stops": ["R20", "R20", "R21"],  # R20 repeated
                "periods": [(900, None)],
            }
        ]
    )
    (alert,), _, _ = feeds._decode_alerts(raw, "subway", NOW)
    assert alert["routes"] == ["Q", "N"]
    assert alert["stops"] == ["R20", "R21"]


def test_decode_future_excluded_and_counted_ended_dropped():
    raw = _alert_feed(
        [
            {"id": "active", "routes": ["Q"], "periods": [(900, 1100)]},
            {"id": "future", "routes": ["N"], "periods": [(1100, 1200)]},
            {"id": "ended", "routes": ["R"], "periods": [(800, 900)]},
        ]
    )
    alerts, suppressed, _ = feeds._decode_alerts(raw, "subway", NOW)
    assert [a["id"] for a in alerts] == ["active"]  # only the covering one
    assert suppressed == 1  # future counted; ended not counted


def test_decode_end_zero_is_open_ended():
    # An explicit end of 0 means open-ended (feed fact), so ends_at is null.
    raw = _alert_feed([{"id": "a", "routes": ["Q"], "periods": [(900, 0)]}])
    (alert,), _, _ = feeds._decode_alerts(raw, "subway", NOW)
    assert alert["starts_at"] == 900
    assert alert["ends_at"] is None


def test_decode_prefers_english_translation():
    raw = _alert_feed(
        [
            {
                "id": "a",
                "routes": ["Q"],
                "periods": [(900, None)],
                "header": [("retraso", "es"), ("Delays", "en")],
                "description": [("solo espanol", "es")],  # no english: first available
            }
        ]
    )
    (alert,), _, _ = feeds._decode_alerts(raw, "subway", NOW)
    assert alert["header"] == "Delays"
    assert alert["description"] == "solo espanol"


def test_decode_missing_text_is_none():
    raw = _alert_feed([{"id": "a", "routes": ["Q"], "periods": [(900, None)]}])
    (alert,), _, _ = feeds._decode_alerts(raw, "subway", NOW)
    assert alert["header"] is None
    assert alert["description"] is None


def test_decode_effect_and_cause_enum_names():
    raw = _alert_feed(
        [
            {
                "id": "a",
                "routes": ["Q"],
                "periods": [(900, None)],
                "effect": pb.Alert.Effect.Value("DETOUR"),
                "cause": pb.Alert.Cause.Value("MAINTENANCE"),
            }
        ]
    )
    (alert,), _, _ = feeds._decode_alerts(raw, "subway", NOW)
    assert alert["effect"] == "DETOUR"
    assert alert["cause"] == "MAINTENANCE"


def test_enum_name_falls_back_to_int_for_unknown_value():
    # A value newer than the bundled binding stringifies rather than raising.
    assert feeds._enum_name(feeds._ALERT_EFFECT, 99999) == "99999"


# ---- ferry alert decode tolerance (wiring ferries into the alert pipeline) ----
#
# _decode_alerts is pure GTFS-RT with no agency-specific handling, so the ferry feed
# needs no decoder change; these pin that tolerance with synthetic feeds rather than a
# captured golden (a populated ferry alert is rare and its content changes). They
# assert the ferry feed key flows through unchanged: a plain alert decodes, ferry
# route/stop selectors land under system "ferry" with those keys, and a selector-less
# alert is systemwide.


def test_decode_ferry_plain_alert_is_systemwide():
    # A minimal ferry alert: header + description only, NO informed_entity and no
    # agency-specific extensions. It decodes, is tagged system "ferry", and carries
    # no route/stop, the systemwide convention (the frontend banner, not a popup).
    raw = _alert_feed(
        [
            {
                "id": "f1",
                "periods": [(900, None)],
                "header": [("Ferry Point Park landing closed", "en")],
                "description": [("Power outage; the Rockaway/Soundview route skips it.", "en")],
            }
        ]
    )
    (alert,), suppressed, _ = feeds._decode_alerts(raw, "ferry", NOW)
    assert suppressed == 0
    assert alert["system"] == "ferry"
    assert alert["header"] == "Ferry Point Park landing closed"
    assert alert["description"].startswith("Power outage")
    assert alert["routes"] == [] and alert["stops"] == []  # selector-less: systemwide


def test_decode_ferry_route_and_stop_selectors_land_under_ferry():
    # Ferry route ids (ER) and ferry stop ids (18) land under system "ferry" with
    # those exact keys, the same id space /api/ferry and /api/ferry-stops use, so the
    # frontend join on (ferry, route_id) / (ferry, stop_id) matches.
    raw = _alert_feed(
        [
            {"id": "route", "routes": ["ER"], "periods": [(0, None)]},
            {"id": "stop", "stops": ["18"], "periods": [(0, None)]},
        ]
    )
    alerts, _, _ = feeds._decode_alerts(raw, "ferry", NOW)
    by_id = {a["id"]: a for a in alerts}
    assert by_id["route"]["system"] == "ferry"
    assert by_id["route"]["routes"] == ["ER"] and by_id["route"]["stops"] == []
    assert by_id["stop"]["system"] == "ferry"
    assert by_id["stop"]["stops"] == ["18"] and by_id["stop"]["routes"] == []


def test_decode_ferry_real_world_shape_route_scoped():
    # Pinned to the shape actually observed live on 2026-07-12, the first populated
    # ferry alert: route-scoped (route "RS", no stop), an unset start (open on the
    # left), a far-future end, and UNKNOWN effect/cause. Synthetic bytes, real shape.
    raw = _alert_feed(
        [
            {
                "id": "1",
                "routes": ["RS"],
                "periods": [(None, 4134002399)],  # no start; far-future end
                "header": [("Service Alert - Ferry Point Park - Temporary Closure", "en")],
                "effect": pb.Alert.Effect.Value("UNKNOWN_EFFECT"),
                "cause": pb.Alert.Cause.Value("UNKNOWN_CAUSE"),
            }
        ]
    )
    (alert,), suppressed, _ = feeds._decode_alerts(raw, "ferry", NOW)
    assert suppressed == 0
    assert alert["system"] == "ferry"
    assert alert["routes"] == ["RS"] and alert["stops"] == []
    assert alert["starts_at"] is None and alert["ends_at"] == 4134002399
    assert alert["effect"] == "UNKNOWN_EFFECT" and alert["cause"] == "UNKNOWN_CAUSE"


# ---- fetch aggregation (per-feed degrade, all-failed raise) ----


class _FakeResp:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self) -> None:
        pass


class _FakeClient:
    """Minimal stand-in for httpx.AsyncClient.get, keyed by URL to bytes or a
    raised exception, so fetch_service_alerts can be tested without the network."""

    def __init__(self, by_url: dict):
        self._by_url = by_url

    async def get(self, url: str) -> _FakeResp:
        value = self._by_url[url]
        if isinstance(value, Exception):
            raise value
        return _FakeResp(value)


def _one_active(route: str) -> bytes:
    return _alert_feed([{"id": route, "routes": [route], "periods": [(0, None)]}])


@pytest.mark.anyio
async def test_fetch_degrades_on_partial_failure():
    # Two feeds decode, two fail: the poll still succeeds with the decoded alerts,
    # and the failed feed keys are reported (sorted) instead of raising.
    by_url = {
        feeds.ALERT_FEED_URLS["subway"]: _one_active("Q"),
        feeds.ALERT_FEED_URLS["bus"]: httpx.ConnectError("boom"),
        feeds.ALERT_FEED_URLS["LIRR"]: _one_active("1"),
        feeds.ALERT_FEED_URLS["MNR"]: b"not-a-protobuf-\xff\xfe",  # DecodeError
        feeds.ALERT_FEED_URLS["ferry"]: _one_active("RS"),  # the fifth feed decodes
    }
    fetched = await feeds.fetch_service_alerts(_FakeClient(by_url))
    assert {a["system"] for a in fetched.alerts} == {"subway", "LIRR", "ferry"}
    assert fetched.suppressed == 0
    assert list(fetched.failed) == ["MNR", "bus"]
    assert fetched.served_empty == []


@pytest.mark.anyio
async def test_each_failed_feed_reports_its_own_reason_not_a_shared_marker():
    """The reasons are what /api/status now records per system, so they have to
    distinguish the ways a feed fails rather than all read alike. Before this they
    existed only inside the fetch: the caller got keys, and every failed system's
    health block said "alert feed unavailable this poll" whatever had happened."""
    by_url = {url: _one_active("Q") for url in feeds.ALERT_FEED_URLS.values()}
    by_url[feeds.ALERT_FEED_URLS["bus"]] = httpx.ConnectError("connection refused")
    by_url[feeds.ALERT_FEED_URLS["MNR"]] = b"not-a-protobuf-\xff\xfe"
    by_url[feeds.ALERT_FEED_URLS["LIRR"]] = TimeoutError()

    fetched = await feeds.fetch_service_alerts(_FakeClient(by_url))
    assert list(fetched.failed) == ["LIRR", "MNR", "bus"], "sorted, so the order is stable"
    assert "connection refused" in fetched.failed["bus"]
    assert "undecodable protobuf" in fetched.failed["MNR"]
    # str(TimeoutError()) is the empty string; _describe_feed_error is what stops
    # that reaching an operator as a blank reason.
    deadline = feeds.alerts.ALERT_FEED_DEADLINE_S
    assert fetched.failed["LIRR"] == f"no response within {deadline:.0f}s"


@pytest.mark.anyio
async def test_an_unexpected_exception_publishes_its_TYPE_and_not_its_message(caplog):
    """THE REASON THIS SURFACE IS CLASSIFIED RATHER THAN COPIED.

    These reasons stopped being log-only when the poller began recording them per
    system on /api/status, which puts them under the rule _total_refresh states for
    that surface: an arbitrary exception's str() is arbitrary text, and arbitrary
    text can be a filesystem path, a config value, or a chunk of a body nobody meant
    to republish. The five sibling refreshers never face this because their except
    clauses classify by type; this gather catches everything, so the classification
    lives in _describe_feed_error instead. The traceback still reaches the log, which
    is where a fault of OURS is diagnosed.
    """
    by_url = {url: _one_active("Q") for url in feeds.ALERT_FEED_URLS.values()}
    by_url[feeds.ALERT_FEED_URLS["bus"]] = ValueError("/etc/app/secret.env said no")

    with caplog.at_level("WARNING"):
        fetched = await feeds.fetch_service_alerts(_FakeClient(by_url))

    assert fetched.failed["bus"] == "internal error fetching this feed (ValueError)"
    assert "/etc/app/secret.env" not in fetched.failed["bus"]
    assert "/etc/app/secret.env" in caplog.text, "the log still gets the whole thing"
    assert "Traceback" in caplog.text, "and now gets the traceback it never had"


@pytest.mark.anyio
async def test_a_classified_upstream_failure_still_publishes_its_message():
    """The other side of the same boundary, and the reason it is a list of types
    rather than a blanket rule: an operator needs the upstream's own words for a
    failure the upstream caused, and every family below is already published in
    exactly this shape by a sibling refresher's classified arm."""
    by_url = {url: _one_active("Q") for url in feeds.ALERT_FEED_URLS.values()}
    by_url[feeds.ALERT_FEED_URLS["bus"]] = httpx.ConnectError("connection refused")

    fetched = await feeds.fetch_service_alerts(_FakeClient(by_url))
    assert fetched.failed["bus"] == "connection refused"
    assert set(feeds.alerts._PUBLISHABLE_FEED_ERRORS) == {
        httpx.HTTPError,
        feeds.alerts.njt_auth.NjtNotConfigured,
        feeds.alerts.njt_auth.NjtAuthError,
        feeds.alerts.njt_auth.NjtUpstreamError,
    }, (
        "the publishable set is the boundary written down: widening it is a decision "
        "about what /api/status may say, not a refactor"
    )


@pytest.mark.anyio
async def test_fetch_raises_when_all_feeds_fail():
    by_url = {url: httpx.ConnectError("down") for url in feeds.ALERT_FEED_URLS.values()}
    with pytest.raises(RuntimeError, match="All alert feeds failed"):
        await feeds.fetch_service_alerts(_FakeClient(by_url))


@pytest.mark.anyio
async def test_the_total_outage_exception_carries_every_feeds_reason():
    """AllAlertFeedsFailed is a RuntimeError (so every existing caller and every
    test standing in for a total outage is unaffected) that also carries the
    per-feed reasons, which is what lets the poller write a real cause into each
    system's health block instead of one shared marker."""
    by_url = {url: httpx.ConnectError("down") for url in feeds.ALERT_FEED_URLS.values()}
    by_url[feeds.ALERT_FEED_URLS["ferry"]] = b"\xff\xfe not a protobuf"
    with pytest.raises(feeds.AllAlertFeedsFailed) as excinfo:
        await feeds.fetch_service_alerts(_FakeClient(by_url))
    assert isinstance(excinfo.value, RuntimeError)
    errors = excinfo.value.feed_errors
    assert set(errors) == set(feeds.active_alert_feeds())
    assert "undecodable protobuf" in errors["ferry"]
    assert "down" in errors["subway"]


# ---- NJ Transit membership (15b): the sixth feed, POSTed and credential-gated ----
#
# THE HONESTY THIS PROTECTS. An unconfigured NJ Transit must be ABSENT from the
# feed set, not present-and-failing. Present-and-failing would put "njt" in
# degraded_systems on every deployment that does not run NJ Transit, permanently,
# and a degraded banner that is always on is one nobody reads. 15a drew this line
# for the static loader ("not-configured" is its own state, distinct from failed);
# these are the same line for alerts.


def test_unconfigured_njt_is_dropped_from_the_active_feed_set():
    # Explicit empty credentials, the shape a missing environment variable actually
    # reaches this code as (see njt_auth.credentials).
    active = feeds.active_alert_feeds({"NJT_USERNAME": "", "NJT_PASSWORD": ""})
    assert "njt" not in active
    assert set(active) == set(feeds.ALERT_FEED_URLS) - {"njt"}


def test_configured_njt_is_in_the_active_feed_set():
    active = feeds.active_alert_feeds({"NJT_USERNAME": "u", "NJT_PASSWORD": "p"})
    assert active["njt"] == feeds.ALERT_FEED_URLS["njt"]
    assert set(active) == set(feeds.ALERT_FEED_URLS)


def test_half_configured_njt_is_dropped():
    # A username with no password is not "configured enough to try": it would spend
    # a doomed mint out of ten a day (njt_auth.DAILY_MINT_LIMIT).
    assert "njt" not in feeds.active_alert_feeds({"NJT_USERNAME": "u", "NJT_PASSWORD": ""})
    assert "njt" not in feeds.active_alert_feeds({"NJT_USERNAME": "", "NJT_PASSWORD": "p"})


def test_placeholder_credentials_are_not_configured():
    # The .env.example values copied verbatim. Same rule as 15a's static loader.
    assert "njt" not in feeds.active_alert_feeds(
        {"NJT_USERNAME": "your-njt-username", "NJT_PASSWORD": "your-njt-password"}
    )


@pytest.mark.anyio
async def test_configured_njt_alerts_go_through_the_token_door_not_the_shared_client(
    monkeypatch,
):
    """The dispatch, asserted by making the WRONG path impossible rather than by
    watching for the right one: the fake client raises on any GET of the NJT URL,
    so a client.get here fails the test loudly instead of quietly working against
    a live endpoint.

    Why it matters beyond tidiness: njt_auth owns the process-wide single-flight
    token cache. Three callers (this feed, the trains poller, the static loader)
    meeting an expired token together must produce ONE re-mint. A client.post here
    would route around that lock and spend three.
    """
    monkeypatch.setattr(
        feeds.alerts.njt_auth,
        "is_configured",
        lambda env=None: True,
    )
    posted: list[tuple[str, dict]] = []

    async def fake_post(url, form):
        posted.append((url, form))
        return _one_active("NEC")

    monkeypatch.setattr(feeds.alerts.njt_auth, "njt_post", fake_post)

    by_url = {url: _one_active("Q") for url in feeds.ALERT_FEED_URLS.values()}
    # Any GET at the NJT url is a routing bug, so make it explode.
    by_url[feeds.ALERT_FEED_URLS["njt"]] = AssertionError(
        "the NJT alerts feed was GETed through the shared client instead of POSTed through njt_auth"
    )

    fetched = await feeds.fetch_service_alerts(_FakeClient(by_url))
    assert fetched.failed == {}
    assert [url for url, _form in posted] == [feeds.ALERT_FEED_URLS["njt"]]
    njt_alerts = [a for a in fetched.alerts if a["system"] == "njt"]
    assert len(njt_alerts) == 1, "the POSTed feed's alerts are decoded like any other"
    assert njt_alerts[0]["routes"] == ["NEC"]


@pytest.mark.anyio
async def test_all_feeds_failed_is_measured_against_the_active_set(monkeypatch):
    """The total-outage raise counts against the feeds actually polled.

    Measured against the full table instead, a five-of-five outage on a deployment
    without NJ Transit would be 5 != 6 and would NOT raise: the poll would report a
    healthy generation with zero alerts, which is the false-green C4 exists to
    remove.
    """
    monkeypatch.setattr(feeds.alerts.njt_auth, "is_configured", lambda env=None: False)
    by_url = {url: httpx.ConnectError("down") for url in feeds.ALERT_FEED_URLS.values()}
    with pytest.raises(RuntimeError, match="All alert feeds failed"):
        await feeds.fetch_service_alerts(_FakeClient(by_url))


# ---- merge_alert_generations (per-system retention across partial outages) ----
#
# Pure and clock-injected, so every case fixes `now` and the prior retention clock
# explicitly rather than sleeping. CAP is the retention ceiling under test.

CAP = 1800


def _al(alert_id, system, ends_at=None):
    """A minimal alert dict carrying only the fields the merge reads."""
    return {"id": alert_id, "system": system, "ends_at": ends_at}


def test_merge_no_failures_replaces_wholesale_and_retains_nothing():
    prev = [_al("old", "subway")]
    fresh = [_al("new", "subway"), _al("b", "bus")]
    merged, retained = feeds.merge_alert_generations(prev, fresh, [], {}, 5000, CAP)
    assert merged == fresh  # fresh is authoritative; the stale "old" is gone
    assert retained == {}


def test_merge_retains_a_failed_systems_alerts():
    prev = [_al("m1", "MNR"), _al("s1", "subway")]
    fresh = [_al("s2", "subway")]  # subway decoded, MNR down this poll
    merged, retained = feeds.merge_alert_generations(prev, fresh, ["MNR"], {}, 5000, CAP)
    assert {a["id"] for a in merged} == {"s2", "m1"}  # fresh subway + carried-forward MNR
    assert set(retained) == {"MNR"}
    assert retained["MNR"] == 5000  # newly down: the retention clock starts at now


def test_merge_expiry_during_outage_drops_expired_keeps_live():
    prev = [_al("expired", "MNR", ends_at=1200), _al("live", "MNR", ends_at=None)]
    # now is past the expired alert's ends_at: it drops (same rule the decode uses),
    # while the open-ended one is still carried.
    merged, retained = feeds.merge_alert_generations(prev, [], ["MNR"], {"MNR": 1000}, 1500, CAP)
    assert {a["id"] for a in merged} == {"live"}
    assert set(retained) == {"MNR"}


def test_merge_retention_cap_drops_system_after_max_age():
    prev = [_al("open", "MNR", ends_at=None)]  # open-ended: only the cap can clear it
    started = 1000
    # One second before the cap: still retained.
    merged, retained = feeds.merge_alert_generations(
        prev, [], ["MNR"], {"MNR": started}, started + CAP - 1, CAP
    )
    assert {a["id"] for a in merged} == {"open"} and set(retained) == {"MNR"}
    # At the cap: dropped, and no longer reported retained (the caller still flags
    # MNR degraded via last_error).
    merged, retained = feeds.merge_alert_generations(
        prev, [], ["MNR"], {"MNR": started}, started + CAP, CAP
    )
    assert merged == [] and retained == {}


def test_merge_recovery_replaces_retained_with_fresh_and_clears_since():
    prev = [_al("carried", "MNR", ends_at=None)]
    fresh = [_al("fresh_mnr", "MNR", ends_at=None)]  # MNR decoded again this poll
    merged, retained = feeds.merge_alert_generations(prev, fresh, [], {"MNR": 1000}, 2000, CAP)
    assert {a["id"] for a in merged} == {"fresh_mnr"}  # fresh wins, the carried one is gone
    assert retained == {}  # recovered: retained_since cleared


def test_merge_failed_system_with_no_prior_alerts_retains_nothing():
    # MNR is down but had nothing to carry: merged is just the fresh alerts and MNR
    # is not reported retained (the caller records health only). prev None is treated
    # as empty, exercising the pre-first-poll path.
    fresh = [_al("s1", "subway")]
    merged, retained = feeds.merge_alert_generations(None, fresh, ["MNR"], {}, 5000, CAP)
    assert merged == fresh
    assert retained == {}


def test_merge_retained_since_survives_polls_and_epoch_zero_is_kept():
    # The cap measures from the ORIGINAL down time threaded back in, not from this
    # poll, and a 0.0 (epoch) start must not be reset by a truthiness bug.
    prev = [_al("open", "MNR", ends_at=None)]
    merged, retained = feeds.merge_alert_generations(prev, [], ["MNR"], {"MNR": 0.0}, 1700, CAP)
    assert {a["id"] for a in merged} == {"open"}
    assert retained["MNR"] == 0.0  # preserved, not bumped to 1700
    merged, _ = feeds.merge_alert_generations(prev, [], ["MNR"], {"MNR": 0.0}, CAP, CAP)
    assert merged == []  # one cap-span past epoch zero, it drops


@pytest.mark.anyio
async def test_c3_an_empty_200_on_one_alert_feed_fails_that_feed_only():
    # An empty body used to decode as "this system has no alerts right now",
    # which is indistinguishable from a healthy quiet system and is exactly the
    # wrong answer during an outage: the system's real alerts silently vanished
    # while /api/status reported it healthy. It now joins the failed set, which
    # feeds the per-system health map and the retention window.
    by_url = {url: _one_active("X") for url in feeds.ALERT_FEED_URLS.values()}
    by_url[feeds.ALERT_FEED_URLS["MNR"]] = b""
    fetched = await feeds.fetch_service_alerts(_FakeClient(by_url))
    assert list(fetched.failed) == ["MNR"]
    assert {a["system"] for a in fetched.alerts} == {"subway", "bus", "LIRR", "ferry"}
    assert fetched.served_empty == [], "the served-empty rule is NJ Transit's alone"


@pytest.mark.anyio
async def test_c3_a_VALID_EMPTY_alert_feed_is_a_healthy_system_with_no_alerts():
    # The distinction the parser draws, at the alerts boundary: a header-only feed
    # is a system with genuinely nothing to report (the common case most of the
    # day), so it must NOT be marked failed, or every quiet system would be
    # reported degraded and its alerts put into retention.
    header_only = pb.FeedMessage()
    header_only.header.gtfs_realtime_version = "2.0"
    by_url = {url: _one_active("X") for url in feeds.ALERT_FEED_URLS.values()}
    by_url[feeds.ALERT_FEED_URLS["MNR"]] = header_only.SerializeToString()
    fetched = await feeds.fetch_service_alerts(_FakeClient(by_url))
    assert fetched.failed == {}
    assert "MNR" not in {a["system"] for a in fetched.alerts}  # healthy, and quiet
    assert fetched.served_empty == [], (
        "a header-only body is an ORDINARY quiet decode, not the served-empty state: "
        "only a zero-byte body from NJ Transit gets the sentence on /api/status"
    )


# ---- NJ Transit's served-empty alerts body (2026-09-07) ----
#
# THE ONE BODY THIS MODULE READS AS A SERVED STATE RATHER THAN A FAILURE, and the
# tests below are deliberately a matched set of four: the state itself, the one-byte
# control that stops the rule widening to "not enough bytes", the wrong-system
# control that stops it widening to another feed, and the poll-level effect. Any one
# of them alone is satisfied by a rule that is too broad. feeds.njt_alerts_served_empty
# carries the evidence and the ambiguity being accepted.


def _configure_njt(monkeypatch) -> None:
    """Make njt_auth report NJ Transit configured, so it is in the active feed set.
    The test environment has no credentials, and without this "njt" is absent from
    every feed set and a scenario about it would pass vacuously."""
    monkeypatch.setattr(feeds.alerts.njt_auth, "is_configured", lambda env=None: True)


def _njt_body(monkeypatch, body: bytes) -> None:
    """Answer the NJT alerts POST with `body`, through the same seam the production
    code uses (njt_auth.njt_post), never through the shared GET client."""

    async def fake_post(url, form):
        return body

    monkeypatch.setattr(feeds.alerts.njt_auth, "njt_post", fake_post)


def test_a_zero_byte_njt_alerts_body_decodes_as_zero_alerts():
    # THE RULE ITSELF, at the decoder. Before 2026-09-07 this raised FeedDecodeError
    # ("empty body served as 200 (no protobuf header)") and the alerts poller called
    # NJ Transit degraded on every quiet night.
    # The third element is the feed's content clock (6.1). It is None here and that is
    # the honest answer: a zero-byte 200 carries no header to read, so what the entry
    # can date is when we were SERVED it, not when it was generated.
    assert feeds._decode_alerts(b"", "njt", NOW) == ([], 0, None)
    assert feeds.njt_alerts_served_empty("njt", b"") is True


def test_one_byte_from_njt_is_still_a_failure():
    # THE CONTROL THAT KEEPS THE RULE NARROW. The rule is "no bytes at all", not
    # "not enough bytes": a truncated or corrupted body is exactly the silent
    # upstream failure C3 exists to catch, and widening the arm to any short or
    # unparseable body would swallow it.
    assert feeds.njt_alerts_served_empty("njt", b"\x00") is False
    with pytest.raises(feeds.FeedDecodeError):
        feeds._decode_alerts(b"\x00", "njt", NOW)


def test_a_zero_byte_body_from_any_other_alert_system_is_still_a_failure():
    # THE OTHER CONTROL. The exemption is NJ Transit's alone, because NJ Transit is
    # the only publisher observed doing this; the MTA and ferry feeds carry a header
    # even when they have nothing to report, so zero bytes from them is the C3
    # signature and nothing else.
    for system in ("subway", "bus", "LIRR", "MNR", "ferry"):
        assert feeds.njt_alerts_served_empty(system, b"") is False
        with pytest.raises(feeds.FeedDecodeError):
            feeds._decode_alerts(b"", system, NOW)


@pytest.mark.anyio
async def test_a_served_empty_njt_feed_is_a_successful_poll_that_names_itself(monkeypatch):
    """The poll-level effect, which is what /api/status is built from: njt is NOT in
    the failed set, contributes no alerts, and IS named in served_empty so the caller
    can tell "upstream says there are none" from "upstream said nothing"."""
    _configure_njt(monkeypatch)
    _njt_body(monkeypatch, b"")
    by_url = {url: _one_active("Q") for url in feeds.ALERT_FEED_URLS.values()}

    fetched = await feeds.fetch_service_alerts(_FakeClient(by_url))
    assert fetched.failed == {}, "a served-empty body is a decode, not an outage"
    assert fetched.served_empty == ["njt"]
    assert "njt" not in {a["system"] for a in fetched.alerts}
    assert {a["system"] for a in fetched.alerts} == {"subway", "bus", "LIRR", "MNR", "ferry"}


@pytest.mark.anyio
async def test_a_one_byte_njt_feed_fails_that_feed_alone(monkeypatch):
    """The control at the poll level, and the reason it is worth having twice: the
    decoder test proves the rule is narrow, this proves the narrowness survives the
    gather. njt joins the failed set with a reason, and the five keyless feeds are
    untouched."""
    _configure_njt(monkeypatch)
    _njt_body(monkeypatch, b"\x00")
    by_url = {url: _one_active("Q") for url in feeds.ALERT_FEED_URLS.values()}

    fetched = await feeds.fetch_service_alerts(_FakeClient(by_url))
    assert list(fetched.failed) == ["njt"]
    assert "undecodable protobuf" in fetched.failed["njt"]
    assert fetched.served_empty == []
    assert {a["system"] for a in fetched.alerts} == {"subway", "bus", "LIRR", "MNR", "ferry"}
