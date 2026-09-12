"""Golden test: a real captured MNR service-alerts feed decodes to stable output.

Synthetic protobufs (test_feeds_alerts.py) exercise the branches, but only a real
feed exercises the true shape of the data (mixed route-only / stop-only / both
selectors, open-ended vs bounded windows, planned work held back). The fixture was
captured once from the live keyless camsys MNR alerts feed (no PII), with `now`
frozen to the feed's header timestamp so the active-now filter is deterministic:
the same alerts are active every run, and the suppressed (not-yet-active) count is
fixed. MNR was chosen over LIRR only because it is smaller.

To regenerate after an INTENTIONAL decode change, from backend/:

    python - <<'PY'
    import json, httpx
    from pathlib import Path
    from google.transit import gtfs_realtime_pb2 as pb
    import feeds
    FIX = Path("tests/fixtures")
    raw = httpx.get(feeds.ALERT_FEED_URLS["MNR"], timeout=30, follow_redirects=True).content
    feed = pb.FeedMessage(); feed.ParseFromString(raw)
    now = float(feed.header.timestamp)
    alerts, suppressed = feeds._decode_alerts(raw, "MNR", now)
    FIX.joinpath("alerts_mnr.pb").write_bytes(raw)
    out = {"now": now, "feed_key": "MNR", "alerts": alerts, "suppressed": suppressed}
    FIX.joinpath("alerts_mnr_expected.json").write_text(json.dumps(out, indent=0) + "\n")
    PY
"""

import json
from pathlib import Path

import feeds

FIXTURES = Path(__file__).parent / "fixtures"


def _load():
    raw = (FIXTURES / "alerts_mnr.pb").read_bytes()
    expected = json.loads((FIXTURES / "alerts_mnr_expected.json").read_text())
    return raw, expected


def test_real_alert_feed_decodes_to_golden_output():
    raw, expected = _load()
    alerts, suppressed = feeds._decode_alerts(raw, expected["feed_key"], expected["now"])
    assert alerts == expected["alerts"]
    assert suppressed == expected["suppressed"]


def test_golden_alerts_are_nontrivial():
    # Guard the guard: an empty fixture (or one with nothing held back) would make
    # the equality test vacuous and skip the active/suppressed split entirely.
    _, expected = _load()
    assert len(expected["alerts"]) >= 3  # active alerts present
    assert expected["suppressed"] >= 1  # and some not-yet-active planned work held back


def test_every_golden_alert_is_active_and_well_formed():
    # Real-data invariants: every emitted alert is tagged MNR, carries an id and
    # header, and is genuinely active at the frozen now (started, and open-ended or
    # not yet ended on the half-open window). Also confirm the fixture exercises
    # BOTH selector shapes so the golden is a real mix, not all one kind.
    _, expected = _load()
    now = expected["now"]
    saw_route_only = saw_stop = False
    for a in expected["alerts"]:
        assert a["system"] == "MNR"
        assert a["id"]
        assert a["header"]  # MNR alerts always carry a header
        assert isinstance(a["routes"], list) and isinstance(a["stops"], list)
        assert a["starts_at"] is None or a["starts_at"] <= now
        assert a["ends_at"] is None or a["ends_at"] > now
        if a["routes"] and not a["stops"]:
            saw_route_only = True
        if a["stops"]:
            saw_stop = True
    assert saw_route_only  # at least one route-only (systemwide) alert
    assert saw_stop  # and at least one carrying stop selectors
