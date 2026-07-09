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
    alerts, suppressed = feeds._decode_alerts(raw, "subway", NOW)
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
    (alert,), _ = feeds._decode_alerts(raw, "subway", NOW)
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
    alerts, suppressed = feeds._decode_alerts(raw, "subway", NOW)
    assert [a["id"] for a in alerts] == ["active"]  # only the covering one
    assert suppressed == 1  # future counted; ended not counted


def test_decode_end_zero_is_open_ended():
    # An explicit end of 0 means open-ended (feed fact), so ends_at is null.
    raw = _alert_feed([{"id": "a", "routes": ["Q"], "periods": [(900, 0)]}])
    (alert,), _ = feeds._decode_alerts(raw, "subway", NOW)
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
    (alert,), _ = feeds._decode_alerts(raw, "subway", NOW)
    assert alert["header"] == "Delays"
    assert alert["description"] == "solo espanol"


def test_decode_missing_text_is_none():
    raw = _alert_feed([{"id": "a", "routes": ["Q"], "periods": [(900, None)]}])
    (alert,), _ = feeds._decode_alerts(raw, "subway", NOW)
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
    (alert,), _ = feeds._decode_alerts(raw, "subway", NOW)
    assert alert["effect"] == "DETOUR"
    assert alert["cause"] == "MAINTENANCE"


def test_enum_name_falls_back_to_int_for_unknown_value():
    # A value newer than the bundled binding stringifies rather than raising.
    assert feeds._enum_name(feeds._ALERT_EFFECT, 99999) == "99999"


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
    }
    alerts, suppressed, failed = await feeds.fetch_service_alerts(_FakeClient(by_url))
    assert {a["system"] for a in alerts} == {"subway", "LIRR"}
    assert suppressed == 0
    assert failed == ["MNR", "bus"]


@pytest.mark.anyio
async def test_fetch_raises_when_all_feeds_fail():
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
