"""contract-api: the envelope truths, under upstream manipulation, no browser.

Each scenario drives a REAL backend process against the simulator and asserts on
what /api/* actually serves. Where a hermetic test pins the same behavior one layer
down, the scenario names it, so a failure localizes fast: if the hermetic test is
also red the defect is in that unit, and if it is green the defect is in the
composite, which is the whole reason this tier exists. A few scenarios have no
hermetic counterpart and say so -- they are about a poll loop over time, which is
the thing a stub cannot be.

The waits are poll-until-predicate on observables, never sleeps. See
tests/contract/README.md for the four determinism rules this suite holds itself to.
"""

from __future__ import annotations

import time

from conftest import CONTRACT_TIMING
from upstream_sim import SUBWAY_GROUPS


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

    # THE MIDDLE STATE MUST BE OBSERVED, not assumed. Without this the closing
    # assertions are satisfied by a system that never entered retention at all:
    # retained_since is None both before retention starts and after the cap expires,
    # so the `emptied` predicate below would be true on its first evaluation and a
    # regression that dropped a failed system's data immediately would read as a
    # working retention cap.
    body = app.await_railroads(
        lambda r: r["systems"]["MNR"]["retained_since"] is not None,
        "MNR to enter retention: failed, but still carrying its last-known trains",
    )
    assert _system(body, "MNR")["ok"] is False
    assert _system(body, "LIRR")["ok"] is True, "one system's outage must not touch the other"
    retained_at = _system(body, "MNR")["retained_since"]
    assert [t for t in body["data"] if t["system"] == "MNR"], (
        "retention means the trains are STILL SERVED while the window holds"
    )

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
    # The window really elapsed rather than collapsing to nothing. Compared against
    # the compressed cap the harness configured, so this stays honest if that changes.
    held_for = time.time() - retained_at
    assert held_for >= float(CONTRACT_TIMING["FEED_RETENTION_MAX_S"]) * 0.5, (
        f"the retention window collapsed: data was carried for only {held_for:.1f}s "
        f"against a {CONTRACT_TIMING['FEED_RETENTION_MAX_S']}s cap"
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
    down = app.await_railroads(
        lambda r: r["systems"]["MNR"]["ok"] is False,
        "MNR to report failed",
    )
    stalled_at = down["systems"]["MNR"]["fetched_at"]

    app.sim.set_mode("MNR", "live")
    app.await_railroads(
        lambda r: r["systems"]["MNR"]["ok"] is True,
        "MNR to recover once its upstream answers again",
    )
    mnr = app.get("/api/railroads")["systems"]["MNR"]
    assert mnr["retained_since"] is None
    # ADVANCED past where the outage stalled it, not merely non-None. Per-system
    # fetched_at is never written back to None once set, so "is not None" was true
    # before the outage, during it, and after -- it could not have caught a recovery
    # that flipped ok back to True while leaving the timestamp frozen.
    assert mnr["fetched_at"] > stalled_at, (
        f"recovery must move the system's own clock forward; "
        f"stalled at {stalled_at}, now {mnr['fetched_at']}"
    )
    assert [t for t in app.get("/api/railroads")["data"] if t["system"] == "MNR"], (
        "and it must be serving real trains again, not just reporting healthy"
    )


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
    # The healthy baseline for the divergence assertion at the end. Sampling it here
    # rather than reasoning about it is the point: on a live feed feed_age_s is
    # already a few hundred milliseconds ABOVE age_s (the body is stamped before it
    # is fetched), so "feed_age_s > age_s" on its own is true of a healthy feed and
    # pins nothing at all.
    healthy_lag = app.status()["feeds"]["path"]["feed_age_s"]

    app.sim.set_mode("PATH", "frozen")
    # TWO polls, not one, before snapshotting. await_polls returns when the
    # SIMULATOR begins answering a request, which says nothing about the app having
    # ingested it; a single poll followed by a bare read can capture the last LIVE
    # generation, and every equality below would then compare two different bodies
    # and fail intermittently. The poller is sequential per source, so the sim
    # seeing a SECOND frozen fetch proves the first one was fully committed.
    app.sim.await_polls("PATH", 2)
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
    # /api/status carries both halves of the evidence side by side: age_s is the
    # poll age (near zero, the loop is healthy) and feed_age_s is the body's own age.
    # The assertion is on the MAGNITUDE of the gap, not its sign. The sign is true of
    # a healthy feed too; what only a frozen upstream produces is a gap that has
    # grown by roughly the elapsed polls, so this compares against the healthy
    # baseline plus the three polls waited above.
    lag = status["feeds"]["path"]["feed_age_s"]
    grown_by = lag - healthy_lag
    assert grown_by >= 3 * float(CONTRACT_TIMING["POLL_INTERVAL_S"]) * 0.5, (
        f"the frozen body's age should have outrun the poll age by roughly the three "
        f"polls waited; it grew by {grown_by:.1f}s (healthy lag {healthy_lag}s, now {lag}s)"
    )
    assert status["feeds"]["path"]["age_s"] < float(CONTRACT_TIMING["POLL_INTERVAL_S"]) * 2, (
        "the poll age must stay small: that is what makes this invisible to the page"
    )


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
    healthy = sorted(name for name, block in body["systems"].items() if block["ok"])
    # Exactly seven: one group was poisoned and there are eight. A >= bound would
    # leave room for a second group to fail as collateral, which is precisely the
    # one-group-failure-bleeding-into-another shape this scenario exists to catch.
    assert len(healthy) == 7, f"exactly the seven survivors should be ok, got {healthy}"
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
    # fetched_at, not `s["alerts"] is not None`: the alerts BLOCK exists from the
    # instant the app answers, because lifespan creates the cache entry, so the
    # obvious-looking predicate returns on its first evaluation and synchronises
    # nothing. fetched_at is what a completed poll actually sets.
    healthy = app.await_status(
        lambda s: (s.get("alerts") or {}).get("fetched_at") is not None,
        "the first alerts poll to land",
    )
    fetched_before = healthy["alerts"]["fetched_at"]

    app.sim.set_mode("alerts:MNR", "error")
    app.sim.await_polls("alerts:MNR", 2)

    status = app.await_status(
        lambda s: s["alerts"]["degraded_systems"] == ["MNR"],
        "MNR alone to be reported degraded",
    )
    # ADVANCED, not merely non-None. fetched_at is never written back to None once
    # set, so "is not None" was a precondition that held before the outage began and
    # could not have caught the regression its message names. What the claim needs is
    # that the envelope keeps calling this a successful poll WHILE one feed is down,
    # which only a moving fetched_at shows.
    assert status["alerts"]["fetched_at"] > fetched_before, (
        "a partial outage is still a successful poll: the envelope's fetched_at must "
        "keep advancing, which is exactly why the per-system block has to exist"
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
        # them may promote. await_polls IS the retry assertion -- it raises naming
        # the upstream if the warmup ever stops asking -- so there is no follow-up
        # `fetches > first` check here; one would be true by construction on every
        # path that reaches it and would read as an independent check that is not.
        harness.sim.await_polls("subway", 2)
        assert app.status()["subway_static"] == "failed", (
            "a retry must not promote an archive that parses to nothing"
        )
        assert app.status()["static_archives"]["subway"]["last_promoted_at"] is None

        # A corrected upstream heals it without a redeploy.
        harness.sim.set_publication("subway", "good")
        app.await_status(
            lambda s: s["subway_static"] == "ready",
            "the group to heal once upstream publishes a real archive",
        )
        assert app.get("/api/subway-stops"), "healing must actually place stations"


# ---------------------------------------------------------------------------
# 15a: NJ Transit, the first credentialed upstream
# ---------------------------------------------------------------------------
#
# Four scenarios, and the last two are a MATCHED PAIR that has to be read
# together. NJ Transit answers a dead token with HTTP 500 and
# {"errorMessage":"Invalid token."} rather than 401 or 403, so "re-mint on an
# invalid token" and "re-mint on any 500" look identical from one direction. The
# expiry scenario proves the app heals from the first; the control proves it does
# NOT treat the second the same way. Either one alone is satisfied by the wrong
# implementation.


def test_njt_cold_start_serves_stops_and_reports_ready(contract_app):
    """The ordinary path, end to end over a real socket: mint, POST, validate, serve.

    Worth a scenario of its own even though it asserts nothing exotic, because
    every part of it is new in 15a and none of it is exercised anywhere else at
    this tier: a POST upstream, a credential exchange, a token riding as a form
    field, and a validator built for a feed with no calendar.txt and no
    feed_info.txt. A hermetic test can pin each piece; only this can pin that the
    real app, with real env wiring, reaches ready.

    Hermetic counterparts: backend/tests/test_njt_auth.py (the token dance) and
    backend/tests/test_njt_static.py (the validators and indexes).
    """
    app = contract_app
    app.await_status(
        lambda s: s["njt_static"] == "ready",
        "the NJ Transit static group to reach ready from a simulator archive",
    )
    stops = app.get("/api/njt-stops")
    assert stops, "a ready NJT group must place stations"
    by_id = {stop["id"]: stop for stop in stops}
    # The two identity stops the probe named, carried end to end.
    assert "New York" in by_id["109"]["name"]
    assert "Newark" in by_id["112"]["name"]
    # NO wheelchair KEY, unlike /api/ferry-stops. NJ Transit publishes no
    # accessibility data, and the marker must not invent a False that a client
    # would read as an affirmative "not accessible".
    assert "wheelchair" not in by_id["109"]

    # ONE MINT for a healthy cold start. Not two: the single-flight cache means the
    # loader's one attempt takes one token, and nothing re-mints when nothing is
    # rejected. Asserted on getToken POSTS RECEIVED, not on tokens issued: a refused
    # mint costs the same against a rate limit as a successful one, and the issued
    # count cannot see one.
    assert app.sim.mint_requests() == 1, (
        f"a healthy cold start should POST getToken once, got {app.sim.mint_requests()}"
    )
    assert app.sim.gtfs_requests() == 1, "and should fetch the archive exactly once"

    status = app.status()
    assert status["njt_static"] == "ready"
    # The C5 archive block comes free with staged_fetch, and its presence here is
    # the proof the NJT download went through the SAME staged pipeline as every
    # other archive rather than a private path beside it.
    assert status["static_archives"]["njt"]["last_promoted_at"] is not None
    assert status["static_archives"]["njt"]["last_download_error"] is None


def test_njt_not_configured_makes_no_request_and_leaves_the_app_healthy(harness):
    """No credentials: a distinct state, zero network, nothing else disturbed.

    THE NEGATIVE IS THE ASSERTION. An unconfigured deployment must not mint, must
    not fetch, and must not enter a retry loop that would do either on a schedule.
    The mint counter staying at zero is what says so, and it is checked AFTER
    another system has demonstrably finished warming, so it is a statement about a
    running app rather than about an app that had not got round to it yet.

    Hermetic counterpart:
    backend/tests/test_njt_auth.py::test_absent_credentials_never_reach_the_transport.
    """
    # Emptied rather than removed: the launch env is a merge over os.environ, so a
    # developer running this suite with real NJT credentials in their own shell
    # would otherwise leak them into the scenario and make it configured.
    with harness.launch(NJT_USERNAME="", NJT_PASSWORD="") as app:
        app.await_status(
            lambda s: s["njt_static"] == "not-configured",
            "the NJ Transit group to report not-configured with no credentials",
        )
        # A DIFFERENT WAIT, on a different system, is what makes the zero below
        # mean something: the app has been up long enough to warm an unrelated
        # static group, so "NJT never asked for anything" is not just "NJT has not
        # asked yet".
        app.await_status(
            lambda s: s["subway_static"] == "ready",
            "the subway group to warm, proving the app ran while NJT stayed silent",
        )
        assert app.sim.mints() == 0, "an unconfigured deployment must never mint a token"
        assert app.sim.fetches("njt") == 0, (
            "an unconfigured deployment must never fetch the archive"
        )

        # The endpoint answers, empty, uncacheable. NOT a 503: nothing is coming, so
        # promising data would lie.
        assert app.get("/api/njt-stops") == []
        # And the rest of the app is entirely unaffected, which is the other half
        # of the claim.
        assert app.get("/api/subway-stops"), "the subway layer must be untouched by NJT's absence"


def test_njt_bad_publication_stays_failed_then_heals(harness):
    """Finding 4, NJT edition: a headers-only stops.txt must never reach ready.

    Structurally perfect, parses to nothing. The app must reject it, keep retrying
    on the rung schedule, report failed honestly, stay healthy overall, and heal
    without a redeploy once upstream publishes something real.

    Hermetic counterpart:
    backend/tests/test_njt_static.py::test_headers_only_stops_fails_validation.
    """
    harness.sim.set_publication("njt", "headers-only-stops")
    with harness.launch() as app:
        app.await_status(
            lambda s: s["njt_static"] == "failed",
            "the NJT group to report failed on a headers-only publication",
        )
        # Failed-and-RETRYING, not failed-and-stopped. await_fetched IS the retry
        # assertion: it names the upstream and fails if the warmup ever stops asking.
        harness.sim.await_fetched("njt", 2)
        assert app.status()["njt_static"] == "failed", (
            "a retry must not promote an archive that parses to nothing"
        )
        assert app.status()["static_archives"]["njt"]["last_promoted_at"] is None
        assert "stops.txt" in app.status()["static_archives"]["njt"]["last_download_error"]

        # RETRIES DO NOT RE-MINT. The token is still good, so the cache hands the
        # same one to every attempt; a loader that minted per attempt would burn
        # through an unpublished rate cap during any upstream outage.
        assert app.sim.mint_requests() == 1, (
            f"repeated failed attempts must reuse one token, got "
            f"{app.sim.mint_requests()} getToken POSTs"
        )

        # The app is not sick. NJT failing is one layer degraded, not an outage.
        assert app.get("/api/subway-stops")

        harness.sim.set_publication("njt", "good")
        app.await_status(
            lambda s: s["njt_static"] == "ready",
            "the group to heal once upstream publishes a real archive",
        )
        assert app.get("/api/njt-stops"), "healing must actually place stations"


def test_njt_token_expiry_costs_exactly_one_extra_mint(harness):
    """THE MOST DANGEROUS PROBE FACT, pinned: a dead token is an HTTP 500.

    The simulator rejects the first token it ever issued with the real upstream's
    exact body, {"errorMessage":"Invalid token."} under a 500, and accepts the one
    that replaces it. A loader that reads that 500 as a server error backs off
    forever while the fix is a single re-mint; one that reads it correctly recovers
    INSIDE one attempt.

    THE ASSERTION IS ARITHMETIC, not a log line: exactly two mints across the whole
    scenario. One for the cold cache, one to replace the token that died, and no
    more, because "re-mint once" must be structural rather than a convention that
    drifts into a loop against an unpublished rate cap.

    Hermetic counterpart:
    backend/tests/test_njt_auth.py::test_one_remint_then_the_attempt_fails.
    """
    harness.sim.set_token_mode("reject-first")
    with harness.launch() as app:
        app.await_status(
            lambda s: s["njt_static"] == "ready",
            "the NJT group to recover from an expired token inside one attempt",
        )
        assert harness.sim.mint_requests() == 2, (
            "an expired token costs exactly one extra mint: one cold, one to replace "
            f"the dead one, got {harness.sim.mint_requests()}"
        )
        # THE OTHER HALF, AND THE FALSIFIABLE ONE. Two getGTFS POSTs is what
        # "recovered INSIDE one attempt" looks like on the wire: the rejected fetch
        # and the retry that succeeded. A loader that read the 500 as an outage
        # instead would fail the attempt, retry on the rung schedule, and re-post the
        # SAME token, so this count would climb while mint_requests stayed at 1.
        #
        # The static_archives block is deliberately NOT the assertion here, though it
        # is checked below for corroboration: static_shared._record CLEARS
        # failed_downloads and last_download_error on every promotion, so those two
        # read zero after any successful attempt, re-minted or not. They cannot tell
        # this scenario from the failure it is about.
        assert harness.sim.gtfs_requests() == 2, (
            "recovery must happen inside one attempt: one rejected fetch, one retry, "
            f"got {harness.sim.gtfs_requests()} getGTFS POSTs"
        )
        archive = app.status()["static_archives"]["njt"]
        assert archive["last_promoted_at"] is not None, archive
        assert app.get("/api/njt-stops"), "recovery must actually place stations"


def test_a_real_njt_500_neither_mints_nor_heals(harness):
    """THE CONTROL for the scenario above, and it is not optional.

    Same status code, different body: a genuine NJ Transit fault. The app must NOT
    re-mint (mints are rate-limited below the data cap, the limit is unpublished,
    and spending them on someone else's outage is how an integration gets itself
    throttled) and it MUST classify the attempt as a failure.

    Loosening njt_auth.is_auth_error to "any HTTP 500" passes the expiry scenario
    above and fails HERE, which is what makes that mutation detectable; the 15a
    handoff records the mutation run that proves it.
    """
    harness.sim.set_token_mode("server-error")
    with harness.launch() as app:
        app.await_status(
            lambda s: s["njt_static"] == "failed",
            "the NJT group to report failed on a genuine upstream 500",
        )
        # Let several attempts happen, so "no extra mint" is a claim about a
        # RETRYING app rather than about one that has only tried once. The rungs are
        # compressed to 1s/2s/3s in this tier, so this is a couple of seconds.
        harness.sim.await_fetched("njt", 3)
        assert harness.sim.mint_requests() == 1, (
            "a real 500 must never provoke a re-mint, however many attempts fail; "
            f"got {harness.sim.mint_requests()} getToken POSTs"
        )
        assert app.status()["njt_static"] == "failed"
        assert app.status()["static_archives"]["njt"]["failed_downloads"] >= 1
        assert app.get("/api/subway-stops"), "one failing system must not take the app down"


# ---------------------------------------------------------------------------
# F1: the degraded states a monitor probing only /api/status could not see
# ---------------------------------------------------------------------------
#
# The audit's HIGH finding: the 6-hourly contract monitor probed /api/status and
# exited 0 through every one of these, so it could tell that production was dead
# but never that it was sick. The classification now lives in /healthz beside the
# state it judges, and these scenarios drive a REAL app into each state and read
# what the probe says about it. The monitor's side of the same wire is pinned
# hermetically in backend/tests/test_contract_monitor.py; what cannot be faked is
# whether the app actually enters the state, which is what this tier is for.


def test_healthz_publishes_nothing_degraded_when_the_app_is_healthy(contract_app):
    """THE GREEN PATH, and it is load-bearing rather than a formality: every
    scenario below asserts a code is PRESENT, and a probe that reported every code
    always would satisfy all of them. A monitor that fails on a healthy deployment
    gets muted within two weeks, which costs more than the blindness it replaced.
    """
    app = contract_app
    app.await_status(lambda s: (s.get("subway_feeds") or {}).get("ok", 0) > 0, "the first poll")
    body = app.await_healthz(lambda h: h.get("status") == "pass", "a healthy readiness probe")
    assert body["degraded"] == [], f"a healthy app must report nothing degraded, got {body}"
    assert "reasons" not in body


def test_a_failed_bus_route_index_reaches_healthz(harness):
    """Every borough zip corrupt, so the index build fails outright.

    Visible at /api/status all along as bus_route_index.status, and the monitor
    never read that key. It is asserted here through /healthz instead of adding a
    second opinion to the monitor, so there is exactly one place that decides what
    "the index is broken" means.
    """
    for borough in ("manhattan", "brooklyn", "bronx", "queens", "staten_island", "mta_bus_co"):
        harness.sim.set_publication(f"bus:{borough}", "corrupt-zip")
    with harness.launch() as app:
        body = app.await_healthz(
            lambda h: "bus-route-index-failed" in h.get("degraded", []),
            "the failed bus route index to reach the readiness probe",
        )
        # A GATING code, so the probe answers 503 and says why in prose too. The
        # index is a build this deployment owns, unlike a lagging upstream.
        assert body["status"] == "fail"
        assert any("index" in reason for reason in body["reasons"])
        assert app.status()["bus_route_index"]["status"] == "failed"


def test_stale_upstream_content_reaches_healthz_without_taking_the_app_down(harness):
    """The frozen-upstream blind spot, now visible.

    test_a_frozen_upstream_leaves_every_liveness_signal_green pins the decision
    that content sameness is never read as staleness. This is the other half: the
    content CLOCK falling behind is read, and it is reported without touching the
    status code. Railway restarts a container on a failing healthcheck and a
    fresh process would be exactly as late, so a lagging upstream must reach a
    human without reaching the platform.

    PATH alone goes stale, so the app keeps a fresh feed and stays ready; that is
    what makes this a test of the new code rather than of "no feed is fresh".
    """
    harness.sim.set_mode("PATH", "stale")
    with harness.launch() as app:
        body = app.await_healthz(
            lambda h: "feed-content-stale" in h.get("degraded", []),
            "stale upstream content to reach the readiness probe",
        )
        assert body["status"] == "pass", "a lagging upstream is not a reason to restart"
        assert "reasons" not in body
        status = app.status()
        # THE POINT, stated as the two numbers that disagree: the poll is young and
        # the content is old. Everything the pre-F1 monitor read is the first one.
        assert status["feeds"]["path"]["age_s"] < 90
        assert status["feeds"]["path"]["feed_age_s"] > 90
        assert (status.get("path_feeds") or {}).get("ok") == 1, "the fetch itself still succeeds"


def test_most_subway_groups_down_reaches_healthz(harness):
    """Five of the eight line groups erroring: a mostly dark map behind a 200.

    subway_feeds has published {total, ok, failed} on /api/status all along and
    the monitor never read it. The majority threshold lives in the probe, with its
    reasoning, rather than here or in the monitor.
    """
    down = ("ACE", "BDFM", "G", "JZ", "NQRW")
    for group in down:
        harness.sim.set_mode(f"subway:{group}", "error")
    with harness.launch() as app:
        body = app.await_healthz(
            lambda h: "subway-groups-down" in h.get("degraded", []),
            "a mostly dark subway to reach the readiness probe",
        )
        # Not gating, for the same reason as stale content: the groups are upstream
        # and a restart does not bring them back. The rider still gets three lines.
        assert body["status"] == "pass"
        health = app.status()["subway_feeds"]
        assert health["ok"] == len(SUBWAY_GROUPS) - len(down)
        assert sorted(health["failed"]) == sorted(down)


def test_exactly_half_the_subway_groups_down_is_not_a_majority(harness):
    """The other side of the line, placed ON the boundary rather than near it.

    FOUR of eight, not three. Three is comfortably a minority and would pass under
    a `>=` rule just as happily as under `>`, so a scenario built on it cannot see
    the difference between the two and pins nothing about where the line is; the
    mutation run caught exactly that and this is the corrected version. Four is
    where `(total - ok) * 2 > total` and `>= total` disagree, so this is the case
    that holds the rule in place. The rider still has half the map.
    """
    down = ("ACE", "BDFM", "G", "JZ")
    for group in down:
        harness.sim.set_mode(f"subway:{group}", "error")
    with harness.launch() as app:
        app.await_status(
            lambda s: len((s.get("subway_feeds") or {}).get("failed", [])) == len(down),
            "the four down groups to be recorded",
        )
        body = app.healthz()
        assert body["status"] == "pass"
        assert "subway-groups-down" not in body["degraded"]
