"""contract-api: the envelope truths, under upstream manipulation, no browser.

Each scenario drives a REAL backend process against the simulator and asserts on
what /api/* actually serves. Every one names the hermetic test that pins the same
behavior one layer down, so a failure here localizes fast: if the hermetic test is
also red the defect is in that unit, and if it is green the defect is in the
composite, which is the whole reason this tier exists.

The waits are poll-until-predicate on observables, never sleeps. See
tests/contract/README.md for the four determinism rules this suite holds itself to.
"""

from __future__ import annotations


def _system(body: dict, name: str) -> dict:
    return body["systems"][name]


# ---------------------------------------------------------------------------
# C2: one railroad system freezes while its sibling keeps advancing
# ---------------------------------------------------------------------------


def test_mnr_outage_while_lirr_advances(contract_app):
    """A partial outage the aggregate envelope alone cannot express.

    Hermetic counterparts: backend/tests/test_api.py's per-system retention tests
    and tests/e2e/smoke.spec.js "C2a", which pin the same divergence against a
    stubbed cache and a stubbed API respectively. What only this tier can show is
    that a real poll loop, fed by a real socket that stopped updating, produces it.
    """
    app = contract_app
    app.sim.await_polls("MNR", 1)
    app.sim.await_polls("LIRR", 1)
    app.await_status(
        lambda s: s["feeds"].get("railroads", {}).get("fetched_at") is not None,
        "the first railroad poll to land",
    )
    trains_before = [t for t in app.get("/api/railroads")["data"] if t["system"] == "MNR"]
    assert trains_before, "MNR should be serving trains before its upstream goes down"

    # MNR's upstream stops answering while LIRR keeps publishing. Note which mode
    # this is: the sim can also serve a FROZEN feed (byte-identical 200s forever),
    # and that is deliberately NOT this scenario. A frozen feed still decodes, so
    # its per-system fetched_at keeps advancing and the block does not diverge at
    # all. What C2 is about is a system whose data is being CARRIED FORWARD, and
    # only a failure produces that.
    app.sim.set_mode("MNR", "error")
    app.sim.await_polls("MNR", 2)

    body = app.await_railroads(lambda r: r["systems"]["MNR"]["ok"] is False, "MNR to report failed")
    mnr, lirr = _system(body, "MNR"), _system(body, "LIRR")

    # THE PER-SYSTEM RULE: the envelope's fetched_at says "this poll ran", the
    # per-system block says "this system's data is this old", and they diverge
    # exactly when something is wrong. A partial failure is still a SUCCESSFUL
    # poll, so the envelope alone would report everything as current.
    assert lirr["ok"] is True
    assert lirr["fetched_at"] > mnr["fetched_at"], (
        f"LIRR should have advanced past failed MNR; "
        f"lirr={lirr['fetched_at']} mnr={mnr['fetched_at']}"
    )
    assert body["fetched_at"] >= lirr["fetched_at"]
    # Retained, not dropped: the data is still served, marked as carried forward,
    # which is the honest middle state between fresh and gone.
    assert mnr["retained_since"] is not None
    assert [t for t in body["data"] if t["system"] == "MNR"], (
        "MNR's last-known trains should still be served while retention holds"
    )


def test_a_failed_feed_keeps_reporting_after_the_retention_cap_empties_it(contract_app):
    """The retention cap drops the DATA and the block keeps reporting the outage.

    The distinction C2 is built on: a long-failed system reports ok=False with
    retained_since=None and no data at all, which is not the same as a system that
    is merely stale. Hermetic counterpart: backend/tests/test_api.py's retention
    cap tests. FEED_RETENTION_MAX_S is compressed to 20s by the harness, so this
    scenario costs seconds rather than the production ten minutes.
    """
    app = contract_app
    app.sim.await_polls("MNR", 1)
    app.await_status(
        lambda s: s["feeds"].get("railroads", {}).get("fetched_at") is not None,
        "the first railroad poll to land",
    )

    # An outright failure, not a freeze: retention is about data whose source
    # STOPPED ANSWERING.
    app.sim.set_mode("MNR", "error")
    app.sim.await_polls("MNR", 2)

    body = app.get("/api/railroads")
    assert _system(body, "MNR")["ok"] is False
    assert _system(body, "LIRR")["ok"] is True, "one system's outage must not touch the other"

    # Past the cap, MNR's carried-forward trains are gone while its block remains.
    def emptied(_status: dict) -> bool:
        rail = app.get("/api/railroads")
        mnr = rail["systems"]["MNR"]
        return mnr["ok"] is False and mnr["retained_since"] is None

    app.await_status(
        emptied,
        "MNR's retention window to expire, dropping its data while its block keeps reporting",
        deadline_s=90,
    )
    rail = app.get("/api/railroads")
    assert not [t for t in rail["data"] if t["system"] == "MNR"], (
        "MNR trains should be gone once the retention cap expired"
    )
    assert [t for t in rail["data"] if t["system"] == "LIRR"], "LIRR must still be serving"


def test_recovery_clears_the_per_system_failure(contract_app):
    app = contract_app
    app.sim.await_polls("MNR", 1)
    app.sim.set_mode("MNR", "error")
    app.sim.await_polls("MNR", 2)
    app.await_status(
        lambda _s: app.get("/api/railroads")["systems"]["MNR"]["ok"] is False,
        "MNR to report failed",
    )

    app.sim.set_mode("MNR", "live")
    app.await_status(
        lambda _s: app.get("/api/railroads")["systems"]["MNR"]["ok"] is True,
        "MNR to recover once its upstream answers again",
    )
    mnr = app.get("/api/railroads")["systems"]["MNR"]
    assert mnr["retained_since"] is None
    assert mnr["fetched_at"] is not None


# ---------------------------------------------------------------------------
# The frozen upstream: what the composite deliberately does NOT notice
# ---------------------------------------------------------------------------


def test_a_frozen_upstream_leaves_every_liveness_signal_green(contract_app):
    """A stuck upstream is invisible to everything except feed_timestamp.

    This scenario pins a DECISION rather than a guarantee, and it is here because
    the decision is easy to reverse by accident. feeds/path.py's docstring states
    it outright: the bridge re-serves identical generations as a matter of course,
    so content sameness across polls is never treated as staleness, "here or
    anywhere downstream". The consequence is what this test measures. A frozen
    upstream keeps answering 200, so the fetch succeeds, so path_feeds reports
    ok=1, so fetched_at keeps advancing, so the page (which keys staleness on poll
    age) keeps every PATH marker bright while the trains sit at months-old
    positions. The map quietly lies and nothing in the stack objects.

    The sim's freeze is STRICTER than anything the real bridge produces: it holds
    the header timestamp still too, which the bridge would not. That is what makes
    the one surviving signal assertable. If a content-staleness heuristic is ever
    added, feed_timestamp falling behind fetched_at is the evidence it would key
    on, and this test is where its arrival should be felt.

    No hermetic counterpart, deliberately: the claim is about a whole poll loop
    against a socket that keeps answering, which is exactly what a stub cannot be.
    """
    app = contract_app
    app.sim.await_polls("PATH", 1)
    app.await_status(lambda s: (s.get("path_feeds") or {}).get("ok") == 1, "the first PATH poll")
    assert app.get("/api/path")["trains"], "PATH should be placing trains before the freeze"

    app.sim.set_mode("PATH", "frozen")
    app.sim.await_polls("PATH", 1)
    frozen_at = app.get("/api/path")

    # Three more polls of byte-identical bodies. Poll count, not elapsed time: the
    # claim is about what repeated FETCHES do, whatever the interval.
    app.sim.await_polls("PATH", 3)
    after = app.get("/api/path")

    assert after["fetched_at"] > frozen_at["fetched_at"], (
        "the poll loop must still be running; a frozen upstream is not a dead one"
    )
    assert after["feed_timestamp"] == frozen_at["feed_timestamp"], (
        "the frozen body's own timestamp is the one thing that does not move"
    )
    assert after["trains"] == frozen_at["trains"], "identical bodies must serve identical trains"

    # Every health signal the operator and the page have to work with: green.
    status = app.status()
    assert (status.get("path_feeds") or {}).get("ok") == 1
    assert (status.get("path_feeds") or {}).get("failed") == []
    assert status["feeds"]["path"]["last_error"] is None
    # /api/status already carries both halves of the evidence side by side:
    # age_s is the poll age (near zero, the loop is healthy) and feed_age_s is the
    # body's own age (growing). Their divergence IS the frozen-upstream signature.
    assert status["feeds"]["path"]["feed_age_s"] > status["feeds"]["path"]["age_s"]


# ---------------------------------------------------------------------------
# C3 + C2: one subway group serves an empty 200
# ---------------------------------------------------------------------------


def test_one_subway_group_served_empty_fails_while_survivors_advance(contract_app):
    """THE C3 PREMISE, end to end: ParseFromString(b"") SUCCEEDS.

    An empty 200 is not a transport error and not a decode exception; a lenient
    decoder reports it as a healthy feed with zero vehicles, which is how a whole
    line group can vanish from the map while every health signal stays green. C3
    made the parser strict about it. Hermetic counterparts: the negative-fixture
    tests in backend/tests/test_feeds*.py (via backend/tests/negatives.py) and
    tests/e2e/smoke.spec.js "C2c".
    """
    app = contract_app
    app.sim.await_polls("subway:BDFM", 1)
    app.await_status(
        lambda s: s["feeds"].get("subways", {}).get("fetched_at") is not None,
        "the first subway poll to land",
    )

    app.sim.set_mode("subway:BDFM", "empty")
    app.sim.await_polls("subway:BDFM", 2)

    body = app.get("/api/subways")
    assert _system(body, "BDFM")["ok"] is False, "an empty 200 must fail its group, not pass it"
    healthy = [name for name, block in body["systems"].items() if block["ok"]]
    assert len(healthy) >= 6, f"survivors should keep advancing, only {healthy} were ok"
    assert _system(body, "ACE")["fetched_at"] > _system(body, "BDFM")["fetched_at"]

    # The error is recorded for the operator and does NOT clear on a later
    # successful poll of a sibling group, which is the false-green C4 fixed.
    status = app.status()
    assert status["subway_feeds"]["failed"] == ["BDFM"]


def test_the_poisoned_subway_group_recovers(contract_app):
    app = contract_app
    app.sim.await_polls("subway:BDFM", 1)
    app.sim.set_mode("subway:BDFM", "empty")
    app.await_status(
        lambda s: (s.get("subway_feeds") or {}).get("failed") == ["BDFM"],
        "the BDFM group to be reported failed",
    )

    app.sim.set_mode("subway:BDFM", "live")
    app.await_status(
        lambda s: (s.get("subway_feeds") or {}).get("failed") == [],
        "the BDFM group to recover once its upstream publishes again",
    )
    assert app.get("/api/subways")["systems"]["BDFM"]["ok"] is True


# ---------------------------------------------------------------------------
# C1: alert outages, partial and total
# ---------------------------------------------------------------------------


def test_one_alert_feed_down_is_visible_per_system(contract_app):
    """A partial alerts outage that the poll-level fields cannot show.

    Four of five feeds decoding is a SUCCESSFUL poll, so the envelope's fetched_at
    advances and a rider-facing marker keyed on it would stay green for hours.
    Hermetic counterparts: backend/tests/test_feeds_alerts.py's per-system health
    tests and tests/e2e/smoke.spec.js "C2d".
    """
    app = contract_app
    app.sim.await_polls("alerts:MNR", 1)
    app.await_status(lambda s: s["alerts"] is not None, "the first alerts poll to land")

    app.sim.set_mode("alerts:MNR", "error")
    app.sim.await_polls("alerts:MNR", 2)

    status = app.await_status(
        lambda s: s["alerts"]["degraded_systems"] == ["MNR"],
        "MNR alone to be reported degraded",
    )
    assert status["alerts"]["fetched_at"] is not None, (
        "a partial outage is still a successful poll, which is exactly why the "
        "per-system block has to exist"
    )
    systems = app.get("/api/alerts")["systems"]
    assert systems["MNR"]["ok"] is False
    assert systems["subway"]["ok"] is True


def test_an_alert_expires_during_a_total_outage(contract_app):
    """C1's core claim: expiry is judged by the WINDOW, not by the last poll.

    With every alert feed down, the backend keeps serving its retained index. An
    alert whose active_period has closed must still drop out of that index, because
    a served_at stamped at response-build time is fresh BY CONSTRUCTION and would
    otherwise present an expired alert as current. Hermetic counterpart:
    backend/tests/test_feeds_alerts.py's merge_alert_generations tests.
    """
    app = contract_app
    # Publish alerts that expire a few seconds out, then let the app ingest them.
    app.sim.alerts_end_in_s = 8.0
    app.sim.await_polls("alerts:subway", 1)
    app.await_status(
        lambda _s: len(app.get("/api/alerts")["alerts"]) > 0,
        "the app to ingest at least one active alert",
    )

    # Now take EVERY alert feed down. Nothing new can arrive; the only thing that
    # may change the index is the passage of the alerts' own window.
    every_system = ["LIRR", "MNR", "bus", "ferry", "subway"]
    for system in every_system:
        app.sim.set_mode(f"alerts:{system}", "error")
    app.await_status(
        lambda s: sorted(s["alerts"]["degraded_systems"]) == every_system,
        "every alert system to report degraded",
    )

    app.await_status(
        lambda _s: app.get("/api/alerts")["alerts"] == [],
        "the retained alerts to drop out as their active window closes, with every feed down",
        deadline_s=90,
    )


# ---------------------------------------------------------------------------
# C5: static publications
# ---------------------------------------------------------------------------


def test_a_rejected_publication_keeps_the_cached_archive_serving(harness):
    """C5's promise against a real download over a real socket.

    Two app lifetimes over one data directory, because that is the only honest way
    to reach the refresh path: an archive is re-downloaded when the cache is stale,
    and MAX_AGE_DAYS is deliberately not a seam. The first boot writes a real
    archive; the file is then backdated; the second boot finds it stale, downloads
    the garbage upstream is now publishing, rejects it, and falls back.

    Hermetic counterpart:
    backend/tests/test_static_shared.py::test_loader_serves_the_cache_when_a_publication_is_rejected,
    which injects a transfer. This one makes the app fetch the bad bytes itself.
    """
    with harness.launch() as app:
        app.await_status(
            lambda s: s["subway_static"] == "ready", "a good publication to warm the subway"
        )
        stops_before = app.get("/api/subway-stops")
    archive = harness.data_dir / "gtfs_static" / "gtfs_subway.zip"
    good_bytes = archive.read_bytes()
    assert good_bytes, "the first boot should have written a real archive"

    harness.age_archives()
    harness.sim.set_publication("subway", "headers-only-stops")

    with harness.launch() as app:
        app.await_status(
            lambda s: s["static_archives"].get("subway", {}).get("failed_downloads", 0) >= 1,
            "the staged download of the bad publication to be rejected",
        )
        status = app.status()
        entry = status["static_archives"]["subway"]
        assert entry["last_promoted_at"] is None, "the garbage must never have been promoted"
        assert "stops.txt" in entry["last_download_error"], entry["last_download_error"]

        # THE PROMISE: the group keeps serving, from the archive it already had.
        assert status["subway_static"] == "ready"
        assert app.get("/api/subway-stops") == stops_before

    # BYTE-IDENTICAL on disk, which is the claim a "still serving" assertion alone
    # would not make: a rewritten-but-valid archive would pass that and fail this.
    assert archive.read_bytes() == good_bytes


def test_finding_4_cold_start_stays_failed_then_heals(harness):
    """Third-audit finding 4, against the real warmup and its real retry schedule.

    A headers-only stops.txt is structurally perfect and parses to nothing. Before
    C5 the subway warmup promoted exactly that to "ready" forever: no station on the
    map, nothing retrying. With DATA_DIR empty there is no cache to fall back on, so
    this is the cold start, and the assertion that matters is the NEGATIVE one.

    Hermetic counterpart:
    backend/tests/test_static_shared.py::test_finding_4_headers_only_stops_never_reaches_ready.
    """
    harness.sim.set_publication("subway", "headers-only-stops")
    with harness.launch() as app:
        app.await_status(
            lambda s: s["subway_static"] == "failed",
            "the subway static group to report failed on a headers-only publication",
        )
        # It is failed-and-RETRYING, not failed-and-stopped: the compressed rungs
        # (1s, 2s, 3s) mean several attempts happen inside this wait, and none of
        # them may promote.
        first = harness.sim.fetches("subway")
        harness.sim.await_polls("subway", 2)
        assert app.status()["subway_static"] == "failed", (
            "a retry must not promote an archive that parses to nothing"
        )
        assert app.status()["static_archives"]["subway"]["last_promoted_at"] is None
        assert harness.sim.fetches("subway") > first, "the warmup stopped retrying"

        # A corrected upstream heals it without a redeploy.
        harness.sim.set_publication("subway", "good")
        app.await_status(
            lambda s: s["subway_static"] == "ready",
            "the group to heal once upstream publishes a real archive",
        )
        assert app.get("/api/subway-stops"), "healing must actually place stations"
