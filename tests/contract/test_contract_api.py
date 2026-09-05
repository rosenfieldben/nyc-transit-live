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

import json
import time

from conftest import CONTRACT_TIMING
from upstream_sim import NJT_QUOTA_CANARY, SUBWAY_GROUPS


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
    the one surviving signal assertable.

    THE CONTENT-STALENESS HEURISTIC THIS ONCE PREDICTED HAS SHIPPED, and it keys on
    exactly the evidence named above. F1 added `feed-content-stale` to the
    `degraded` list /healthz publishes: routes/status.py names any cached ENDPOINT
    whose feed_age has reached FEED_STALE_AFTER_S, feed_age being fetched_at minus
    feed_timestamp, the gap this scenario grows. It reports per endpoint and never
    per subway line group, because feeds/subway.py folds the eight group headers
    with min() before the cache sees them.

    IT DOES NOT MOVE THE STATUS CODE. feed-content-stale is outside
    HEALTH_GATING_CODES, so `status` stays "pass" and the probe still answers 200:
    Railway restarts a container on a failing healthcheck and a fresh process would
    be exactly as late, so a lagging upstream has to reach a human without reaching
    the platform. The contract monitor is the stricter reader and fails its run on
    any degraded code at all.

    THIS SCENARIO IS STILL GREEN THROUGHOUT, and that is not an oversight.
    FEED_STALE_AFTER_S is deliberately not overridable (cache.py), so a freeze
    started here would need 90 seconds of waiting to cross it, which this tier's
    budget cannot afford. What the assertions below measure is therefore unchanged:
    for the first 90 seconds a stuck upstream still moves no signal but
    feed_timestamp. The classification's own witness is
    test_stale_upstream_content_reaches_healthz_without_taking_the_app_down, which
    uses the simulator's `stale` mode to reach the same end state immediately.

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
    #
    # SIX SYSTEMS SINCE 15b, and NJ Transit's is not keyed like the other five: it
    # is POSTed behind a token, so it lives at "njt:alerts" beside its sibling
    # realtime route rather than under the alerts: prefix. The contract app runs
    # WITH NJT credentials, so njt is in the active feed set and a total outage
    # that left it up would not be total. This list is hand-written against
    # feeds.active_alert_feeds on purpose, the same coupling the poll registry's
    # fixture guard uses: a seventh alert feed must fail here rather than quietly
    # make this scenario about a partial outage.
    every_system = ["LIRR", "MNR", "bus", "ferry", "njt", "subway"]
    for system in every_system:
        app.sim.set_mode("njt:alerts" if system == "njt" else f"alerts:{system}", "error")
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
            lambda s: s["subway_static"] == "ready",
            "a good publication to warm the subway",
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
        # through the account's ten mints a day during any upstream outage.
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


# ---------------------------------------------------------------------------
# 15c: the route lines, and the publication that carries none
# ---------------------------------------------------------------------------

# The isotropic degree basis the geometry is judged in, WRITTEN OUT HERE rather than
# imported from backend/route_geometry.py. Importing it would make this scenario
# agree with the code it is testing by construction: a sign error in the module's
# own distance would cancel out and the assertion would still pass. Twelve lines of
# independent arithmetic is the cheaper half of that trade.
#
# cos(40.7 degrees), New York's latitude: a degree of longitude is that much shorter
# than a degree of latitude here, so comparing raw degree deltas without it makes
# east-west error read as smaller than it is.
_COS_LAT = 0.7581

# The station-to-line tolerance, stated as the same 0.0025 the hermetic golden uses
# (route_geometry.COVER_DIST, about 275 m in the latitude direction). The simulator's
# shapes trace the stops vertex for vertex, so the real distance here is zero and
# this is a ceiling rather than a fitted value; it is written as the golden's number
# so the two tiers are asking one question.
_ON_THE_LINE = 0.0025


def _iso_distance_to_segment(point, start, end) -> float:
    """Distance from `point` to the segment start..end, in isotropic degrees."""
    px, py = (point[1] - start[1]) * _COS_LAT, point[0] - start[0]
    qx, qy = (end[1] - start[1]) * _COS_LAT, end[0] - start[0]
    span = qx * qx + qy * qy
    if span == 0:
        return (px * px + py * py) ** 0.5
    t = max(0.0, min(1.0, (px * qx + py * qy) / span))
    dx, dy = px - t * qx, py - t * qy
    return (dx * dx + dy * dy) ** 0.5


def _distance_to_polylines(point, polylines) -> float:
    """The closest approach of `point` to any segment of any of `polylines`."""
    best = float("inf")
    for line in polylines:
        for start, end in zip(line, line[1:]):
            best = min(best, _iso_distance_to_segment(point, start, end))
    return best


def test_njt_route_lines_pass_through_the_stations_their_trips_call_at(contract_app):
    """THE GEOMETRY AND THE STATIONS DESCRIBE ONE RAILROAD, end to end.

    /api/njt-routes is built from shapes.txt by a chain nothing short of this tier
    runs whole: a credentialed POST for the archive, the bounded shapes parse, the
    simplifier, the distance dedup, and the endpoint's warming gate. The claim that
    survives all of it is the one a rider would notice failing: every station a
    route's trips call at lies ON the line drawn for that route.

    WHAT THIS SCENARIO CANNOT CATCH, and the first draft of this docstring claimed it
    could. A route/shape join that CROSSED two routes is undetectable here, because
    the simulator publishes s1 and s13 along the same corridor and both routes call at
    the same three stops: measured, a build that hands route 1 route 13's polylines
    and vice versa passes this scenario and fails four hermetic tests, worst pair
    1.11908 against the 0.0025 tolerance. A dedup that discarded the wrong variant is
    likewise unmeasurable, since each route here keeps exactly one. Both belong to
    backend/tests/test_njt_static.py, over a fixture with eleven real routes and four
    branching ones, and the credit is theirs.

    What IS this tier's own is that the whole chain runs and its output still lines
    up with the stations: a shapes parse that silently dropped rows would shorten the
    polyline and move a station off it, and only a real archive over a real socket
    exercises the parse at all.

    Hermetic counterparts: backend/tests/test_njt_static.py's
    _check_stations_sit_on_their_routes over the committed fixture, and
    backend/tests/test_route_geometry.py for the dedup itself.
    """
    app = contract_app
    app.await_status(
        lambda s: s["njt_static"] == "ready",
        "the NJ Transit static group to reach ready from a simulator archive",
    )
    routes = app.get("/api/njt-routes")
    assert routes, "a ready NJT group whose publication carries shapes must serve lines"
    by_route = {route["route"]: route for route in routes}
    # Both routes the simulator publishes trips for, and only those: shapes.txt also
    # carries s99, referenced by no trip, and decision (b) of this phase is that the
    # parse never reads it. A route that appeared here from an unreferenced shape
    # would be a route the map draws and no train ever runs on.
    assert sorted(by_route) == ["1", "13"], routes

    # The feed's own colours, carried verbatim, and text_color null on both because
    # NJ Transit publishes it empty on every route. A client that printed text on
    # `color` has to compute its own ink; the endpoint documents that and this is
    # what makes the documentation checkable.
    assert by_route["1"]["color"] == "EF3E42"
    assert by_route["1"]["text_color"] is None
    assert by_route["1"]["name"] == "Northeast Corridor"

    served_stops = app.get("/api/njt-stops")
    stops = {stop["id"]: stop for stop in served_stops}
    # WHICH STATIONS EACH ROUTE SERVES IS READ OFF THE ENDPOINT, not restated here.
    # /api/njt-stops merges the routes-per-station index (H5) onto every marker, and
    # that index is derived from the same trips table the geometry is joined through,
    # so a simulator whose trips drift takes this assertion with it instead of leaving
    # it agreeing with a list nobody updated. The first draft did restate the ids
    # inline under a comment claiming otherwise.
    calls: dict[str, list[str]] = {}
    for stop in served_stops:
        for route_id in stop["routes"]:
            calls.setdefault(route_id, []).append(stop["id"])
    assert sorted(calls) == ["1", "13"], calls
    assert all(len(ids) == 3 for ids in calls.values()), calls
    for route_id, stop_ids in calls.items():
        polylines = by_route[route_id]["polylines"]
        assert polylines, f"route {route_id} reached the endpoint with nothing to draw"
        assert all(len(line) >= 2 for line in polylines), "a one-point line draws nothing"
        # EVERY PUBLISHED ROW SURVIVED. The simulator publishes three points per
        # shape and the simplifier keeps all three (the middle one is 0.0124 off the
        # chord, fifty times the epsilon), so a parse that dropped a row would show
        # up as a shorter polyline here before it showed up as a station off its line.
        assert [len(line) for line in polylines] == [3], polylines
        for stop_id in stop_ids:
            stop = stops[stop_id]
            distance = _distance_to_polylines((stop["lat"], stop["lon"]), polylines)
            assert distance <= _ON_THE_LINE, (
                f"{stop['name']} is {distance:.5f} from every polyline of route "
                f"{route_id}, past the {_ON_THE_LINE} tolerance: the line served and "
                f"the stations served do not describe the same railroad"
            )


def test_njt_publication_without_shapes_reaches_ready_with_no_routes(harness):
    """DECISION (a) OF THIS PHASE, at the tier where it can be a whole publication.

    shapes.txt is deliberately OUT of njt_static._REQUIRED_MEMBERS: route lines are
    additive, so a publication that drops it must still place stations, still serve
    trains, and still report READY, with an empty routes list rather than a failure.
    That is a real divergence from the PATH and ferry loaders, which both list their
    own shapes.txt among the members they read and cannot degrade around, and the
    simulator's "no-shapes" publication exists to make the divergence expressible.

    THE EMPTY LIST IS NOT THE INTERESTING HALF. /api/njt-routes serves [] for a
    failed load and for an unconfigured deployment too, so an empty list on its own
    says nothing. What this asserts is empty ROUTES beside a ready STATUS and served
    STATIONS, which is the combination no other state produces.

    Hermetic counterpart: backend/tests/test_njt_static.py's shapes-absent arm.
    """
    harness.sim.set_publication("njt", "no-shapes")
    with harness.launch() as app:
        app.await_status(
            lambda s: s["njt_static"] == "ready",
            "a publication with no shapes.txt to still reach ready",
        )
        assert app.get("/api/njt-stops"), "a publication with no geometry still places stations"
        assert app.get("/api/njt-routes") == [], (
            "a publication with no shapes.txt has no lines to draw, and that is a "
            "served answer rather than a failure"
        )
        # AND NOT AS A QUIET DEGRADATION EITHER: the archive was promoted, so nothing
        # downstream is waiting for a retry that will fix it.
        assert app.status()["static_archives"]["njt"]["last_promoted_at"] is not None
        assert app.status()["static_archives"]["njt"]["last_download_error"] is None

        # AND THE REST OF NJ TRANSIT IS UNTOUCHED, which is what "additive" has to
        # mean if it means anything: the realtime side keeps polling and serving
        # while the static side has no geometry to offer. Waited for rather than
        # read on the spot, because the static group reaches ready before the first
        # realtime poll lands and /api/njt-trains answers 503 until it does.
        app.await_status(
            lambda s: s["feeds"].get("njt", {}).get("fetched_at") is not None,
            "the first NJ Transit realtime poll to land alongside a geometry-less static load",
        )
        assert "trains" in app.get("/api/njt-trains")
        assert app.status()["njt_static"] == "ready"

        # NO HEAL HALF HERE, deliberately, and the reason is worth writing down so
        # nobody adds one: warmups._warm_njt_static RETURNS once the group is ready
        # and never re-downloads, so a later set_publication("njt", "good") would
        # change nothing in this process and the wait would time out. The healing
        # path is the FAILED one, and test_njt_bad_publication_stays_failed_then_heals
        # is where it belongs, because only a failed group is still asking.


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
    drifts into a loop against a cap of ten mints a day.

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
    re-mint (mints are rate-limited below the data cap, at ten per account per
    Eastern day, and spending them on someone else's outage is how an integration
    takes its own layer dark) and it MUST classify the attempt as a failure.

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


def test_njt_a_spent_mint_budget_is_reported_as_a_budget_not_an_outage(harness):
    """THE DAILY CAP, END TO END, and it is the third thing an HTTP 500 can mean.

    NJ Transit issues ten tokens per account per Eastern day (observed 2026-09-02)
    and refuses the eleventh with an HTTP 500 whose errorMessage begins "Daily usage
    limit". That is the same status a dead token carries and the same status a
    genuine fault carries, so this scenario runs beside those two rather than
    replacing either: three answers, one status code, told apart by the body alone.

    WHAT THE APP MUST DO, and each half is asserted here:

      1. NOTHING NEW. The NJT group fails and retries on its rung schedule, the rest
         of the app is untouched, and no attempt re-mints. A refusal that says "you
         have spent your budget" is the single worst response to retry harder at,
         because every retry is charged to the budget it is waiting on.
      2. SAY WHICH 500 IT WAS. /healthz publishes njt-mint-quota, and the log carries
         njt_auth's fixed string. Without this the operator's only signal is
         njt_static "failed", which is what a real NJ Transit outage looks like.

    THE PROBE MUST STILL ANSWER 200. A spent budget is not a reason to restart the
    container: Railway would, the fresh process would mint on its first NJ Transit
    request, and that spends another of the ten that already ran out.

    Hermetic counterparts:
    backend/tests/test_njt_auth.py::test_a_refused_mint_raises_the_fixed_string_and_nothing_from_the_body,
    backend/tests/test_api.py::test_healthz_publishes_a_spent_njt_mint_budget_without_gating_on_it.
    """
    harness.sim.set_token_mode("quota")
    with harness.launch() as app:
        app.await_status(
            lambda s: s["njt_static"] == "failed",
            "the NJT group to fail once its mints for the day are gone",
        )
        # THE CODE, off the live probe. This is the only surface that distinguishes
        # this run from test_a_real_njt_500_neither_mints_nor_heals, whose /api/status
        # looks identical.
        health = app.await_healthz(
            lambda body: "njt-mint-quota" in body.get("degraded", []),
            "/healthz to publish the spent NJ Transit budget",
        )
        assert health["status"] == "pass", health
        assert "reasons" not in health, "a spent budget must never gate the probe"

        # LET SEVERAL ATTEMPTS HAPPEN, so what follows is a claim about a RETRYING
        # app rather than one that has tried once. await_mints rather than a bare
        # read of the counter: the rungs are 1s/2s/3s in this tier and the probe can
        # publish the code before the second attempt lands, so reading the counter
        # here would be a race that passes on a slow machine and fails on a fast one.
        harness.sim.await_mints(3)
        # And each of those attempts cost exactly ONE getToken, with no archive
        # fetch behind a token that was never issued. That is what "no retry loop"
        # means on the wire: the app is not doubling up inside an attempt, only
        # trying again on the schedule the warmup owns.
        assert harness.sim.gtfs_requests() == 0, (
            "a mint that was refused must never be followed by an archive fetch"
        )

        # THE F3 CANARY, at the socket. The app now READS this body, so "reads it"
        # and "quotes it" have to be visibly different: the fixed string is present,
        # the refusal's tail is not.
        log = app.log(limit=200_000)
        assert "NJ Transit daily mint limit reached" in log, log[-2000:]
        assert NJT_QUOTA_CANARY not in log, "the getToken body reached the log"

        # ONE LAYER, and the rest of the app is entirely well.
        assert app.get("/api/subway-stops"), "a spent NJT budget must not dim anything else"

        # AND IT CLEARS ITSELF. Eastern midnight is not reachable from a test, but
        # the mechanism is: the flag tracks the most recent mint attempt, so the
        # first one that succeeds retires the code with no clock involved.
        harness.sim.set_token_mode("ok")
        app.await_status(
            lambda s: s["njt_static"] == "ready",
            "the group to heal on the first mint that is not refused",
        )
        assert "njt-mint-quota" not in app.healthz()["degraded"]


def test_njt_a_redirected_mint_never_delivers_the_credentials(harness):
    """Audit 4, F2, at the socket: a credentialed POST never follows a redirect.

    The simulator's getToken answers every mint with a 307 whose Location is a
    route on the simulator that counts credentialed arrivals and hands out a
    WORKING token. httpx re-sends a POST body on a 307, so the app this finding
    describes would deliver the username and password to that Location, mint
    through it, and reach ready looking perfectly healthy. The fixed app never
    follows: the arrival counter stays at zero, the mint fails on the status alone,
    and the NJT group reports failed honestly while the rest of the app is fine.

    WHAT THE COUNTER PROVES AND WHAT IT DOES NOT. Zero arrivals at a same-origin
    Location proves the client does not follow a redirect with its body, which is
    the mechanism; it says nothing about a different origin, because a second
    listener would be needed to observe an arrival there and one was deliberately
    not built. The cross-origin half is carried by the hermetic transport tests,
    which record the URL of every request a real httpx client makes.

    Hermetic counterparts:
    backend/tests/test_njt_auth.py::test_a_credentialed_post_never_follows_a_redirect,
    backend/tests/test_contract_monitor.py::test_the_post_arm_never_follows_a_redirect.
    """
    harness.sim.set_token_mode("redirect")
    with harness.launch() as app:
        app.await_status(
            lambda s: s["njt_static"] == "failed",
            "the NJT group to fail an attempt whose mint was answered with a 307",
        )
        # The app did reach getToken, so "zero arrivals" is a claim about a mint
        # that was really made and really redirected, not about an app that never
        # tried. Several attempts have happened by now (the rungs are 1s/2s/3s in
        # this tier), and every one was answered with the redirect.
        assert harness.sim.mint_requests() >= 1
        assert harness.sim.redirect_arrivals() == 0, (
            "the credentials arrived at the redirect's Location: a POST carrying the "
            f"username and password followed a 307 {harness.sim.redirect_arrivals()} times"
        )
        # The attempt failed ON THE STATUS, which is the whole of what the message
        # may say about a getToken response. The backend log is the same line
        # Railway would show, so this is also the F3 claim at the socket: nothing
        # from the response body, and no credential, rides in it.
        log = app.log(limit=200_000)
        assert "getToken returned HTTP 307" in log, log[-2000:]
        assert "not-a-real-password" not in log and "TooManyRedirects" not in log, log[-2000:]
        assert app.status()["static_archives"]["njt"]["last_promoted_at"] is None
        assert app.get("/api/subway-stops"), "one refused mint must not take the app down"


# ---------------------------------------------------------------------------
# 15b: NJ Transit realtime
# ---------------------------------------------------------------------------
#
# THE TRAP MATRIX IS THE CENTREPIECE. upstream_sim._njt_trip_updates serves six
# trips at once, five of which are shapes the 2026-08-05 rush probe watched NJ
# Transit publish and one (ADDED) that it never did. The claims below are made BY
# NAME against served endpoints rather than against a decoder return value,
# because "no phantom arrival at stop 109" is a statement about what a rider's
# departure board shows, and only a served surface can carry it.
#
# WHY /api/njt-arrivals AND NOT ONLY /api/njt-trains: a canceled trip fails BOTH
# products, and the two fail differently. Placement drops it because it has no
# live segment; arrivals drop it because the decoder filters CANCELED above the
# split. Asserting only on trains would leave the board untested, and the board is
# where the phantom would actually be read.

# The trap matrix's train numbers, by role. Named so a failure says which trap
# broke rather than which integer is missing.
_HEALTHY = "3800"  # T1, the control
_PHANTOM = "3802"  # T3, trip-level CANCELED with full times on every skipped stop
_PARTIAL = "3804"  # T4, running, with Penn dropped and Newark surviving
_BARE_SKIP = "1602"  # T5, the times-less SKIPPED variant
_HEADSIGN_VICTIM = "3806"  # T7, skipping Penn while headsigned for New York
_ADDED = "9001"  # A9, a trip no static knows; its trip_id is EMPTY on the wire
_ADDED_2 = "9002"  # A9b, the second extra, sharing that same empty trip_id


def _trains_by_num(body: dict) -> dict:
    return {train["train_num"]: train for train in body["trains"]}


def _await_njt_trains(app):
    """Wait for the first NJT realtime poll to land, and return the envelope.

    Waits on the SERVED envelope rather than on the simulator's fetch counter,
    because a fetch that the app then failed to decode would satisfy the counter
    and leave every assertion below reading an empty list.
    """
    app.await_status(
        lambda s: s["njt_static"] == "ready",
        "the NJ Transit static group to reach ready (realtime cannot poll before it)",
    )
    return app._await(
        lambda: app.get("/api/njt-trains"),
        lambda body: bool(body.get("trains")),
        "the first NJ Transit realtime poll to place trains",
        60.0,
        lambda last: f"last /api/njt-trains: {json.dumps(last, indent=2)[:3000]}",
    )


def test_njt_realtime_healthy_places_trains_and_serves_a_board(contract_app):
    """The ordinary path, end to end: mint, POST getTripUpdates, decode, place, serve.

    Worth its own scenario for the same reason the 15a cold start was: everything
    here is new. A POST realtime feed behind a token, a schedule-derived placement
    with no GPS anywhere in it, and a per-system freshness block on an envelope
    this tier has never seen before.

    THE PLACEMENT CLAIM IS THE SHARP ONE. NJ Transit's vehicle positions feed is
    never fetched (89% frozen coordinates at peak, worst age 3h18m), so every
    coordinate here was computed from the TripUpdates feed's own times against
    15a's stop table. Asserting a train sits BETWEEN its two stops is asserting
    that arithmetic ran, which no amount of feed liveness would give for free.

    Hermetic counterpart: backend/tests/test_njt_rt.py (the decode and _place).
    """
    app = contract_app
    body = _await_njt_trains(app)
    trains = _trains_by_num(body)

    assert _HEALTHY in trains, f"the healthy control train must be placed: {sorted(trains)}"
    control = trains[_HEALTHY]
    assert control["headsign"] == "New York", "the static join must supply the real headsign"
    assert control["route_id"] == "1"
    # BETWEEN Newark Penn (-74.1646) and New York Penn (-73.9935), which is only
    # true if the interpolation ran. A decoder that snapped every train to its next
    # stop, or dropped the placement entirely, fails here.
    assert -74.1646 < control["longitude"] < -73.9935, control
    assert 40.734 < control["latitude"] < 40.751, control
    assert control["status"] in ("in-transit", "at-station"), control

    # THE C2 BLOCK, keyed "njt": a single-entry map on purpose, so a client reads
    # the same shape here as on the subway and railroad envelopes.
    assert set(body["systems"]) == {"njt"}, body["systems"]
    assert body["systems"]["njt"]["ok"] is True
    assert body["feed_timestamp"] is not None, "the feed header must survive to the envelope"

    # And the board at Penn, which the trap assertions below are all about.
    board = app.get("/api/njt-arrivals/109")
    assert board["stop_name"] == "New York Penn Station"
    assert _HEALTHY in {row["train_num"] for row in board["arrivals"]}


def test_njt_the_trap_matrix_never_reaches_a_riders_board(contract_app):
    """THE SCENARIO THIS PHASE EXISTS FOR. Every probed trap, in one feed, at once.

    Each assertion below is a claim about a specific train at a specific station,
    and each names the shape that would produce it if the decoder were wrong.

    THE POSITIVE CONTROLS ARE NOT DECORATION. Every "not on the board" claim here
    would pass on an app that served an empty board, so each is paired with a
    train that MUST be there. A decoder that dropped everything would fail this
    test on the positives before it could pass on the negatives.

    Hermetic counterpart: backend/tests/test_njt_rt.py, which pins each rule
    against a synthetic protobuf; what only this tier shows is the whole matrix
    surviving one real decode, one real poll, and two real endpoints.
    """
    app = contract_app
    body = _await_njt_trains(app)
    trains = _trains_by_num(body)
    penn = {row["train_num"]: row for row in app.get("/api/njt-arrivals/109")["arrivals"]}
    newark = {row["train_num"]: row for row in app.get("/api/njt-arrivals/112")["arrivals"]}
    hoboken = {row["train_num"]: row for row in app.get("/api/njt-arrivals/38")["arrivals"]}

    # -- 1. THE PHANTOM (decoder law 1) ------------------------------------
    # T3 is CANCELED at the trip level and still carries a full, plausible,
    # nearly-arriving time at Penn on a stop it marks SKIPPED. 8% of peak Penn
    # stop_time_updates were this shape. It must appear NOWHERE.
    assert _PHANTOM not in penn, (
        "a trip-level CANCELED train is on the Penn departure board. It keeps full "
        "arrival and departure times on every SKIPPED stop, so only the trip-level "
        "relationship distinguishes it from a running train."
    )
    assert _PHANTOM not in trains, "and it must not be on the map either"
    assert _HEALTHY in penn, "positive control: the board is not simply empty"

    # -- 2. THE PARTIAL CANCELLATION ---------------------------------------
    # T4 is running normally and loses ONLY the stops it drops. The probe watched
    # exactly this: normal through Newark, then Penn dropped with a plausible
    # delay still attached to the dropped row.
    assert _PARTIAL in newark, (
        "a partially canceled train's SURVIVING stops must still serve. Filtering "
        "the whole trip on any SKIPPED stop would erase a train that is running."
    )
    assert _PARTIAL not in penn, "but its DROPPED stop must not appear"
    assert _PARTIAL in trains, "and the train itself is still on the map, because it is running"

    # -- 3. BOTH SKIPPED VARIANTS (decoder law 2) --------------------------
    # T5 marks Newark SKIPPED with NO times at all, the second observed variant
    # (35 seen against 238 of the with-times shape). Both drop the stop.
    assert _BARE_SKIP not in newark, (
        "a stop marked SKIPPED with no arrival or departure time still has to drop. "
        "A decoder that reads times before relationships cannot see this variant."
    )
    assert _BARE_SKIP in hoboken, "positive control: its surviving future stop still serves"
    assert _BARE_SKIP in trains, "and a bare SKIPPED stop must not unplace the train"

    # -- 4. THE NAMED VICTIM (decoder law 2's rider-facing case) -----------
    # T7 is headsigned "New York" in the static and its only remaining call is
    # New York, SKIPPED. The row a rider would actually act on.
    assert _HEADSIGN_VICTIM not in penn, (
        "a train headsigned FOR New York, skipping New York, is on the New York board"
    )
    # It is also absent from the map, and that is placement being honest rather
    # than a second filter: with its only future call dropped there is no segment
    # left to interpolate along, so there is no position that would not be invented.
    assert _HEADSIGN_VICTIM not in trains, (
        "a train whose only remaining stop is skipped has no segment to be placed on; "
        "drawing it anyway would be a guess"
    )

    # -- 5. ADDED (decoder law 3) ------------------------------------------
    # Never observed in either probe, accepted anyway, joins no static.
    assert _ADDED in trains, "an ADDED trip must be accepted, not dropped and not crashed on"
    added = trains[_ADDED]
    assert added["headsign"] == "1 9001", (
        "an ADDED trip joins no static, so its display name is synthesized from the "
        f"realtime route plus the train number; got {added['headsign']!r}"
    )
    assert _ADDED in penn, "and it reaches a rider's board like any other train"

    # BOTH EXTRAS, AND THAT IS THE POINT OF THERE BEING TWO. NJ Transit publishes
    # ADDED trips with an EMPTY trip_id (36 of 164 on a live capture), so a decoder
    # keying on trip_id merges every extra into one train. One ADDED trip in the
    # matrix could never show that; two sharing the empty id can.
    assert _ADDED_2 in trains, "the second extra must survive as its own train"
    assert trains[_ADDED]["trip_id"] != trains[_ADDED_2]["trip_id"], (
        "two extras sharing an empty trip_id must not share a key: at the scale the "
        "real feed runs extras that is 35 trains vanishing from the map"
    )
    assert {_ADDED, _ADDED_2} <= set(penn), "and both reach the board"


def test_njt_realtime_outage_degrades_only_njt(harness):
    """A partial outage across systems, with NJ Transit as the failing one.

    THE POINT IS THE NEGATIVE SPACE. NJ Transit is the first system in this app
    behind a credentialed POST, so it has failure modes none of its siblings do,
    and a failure that leaked out of them would be invisible in an aggregate. The
    subway must keep advancing, /api/status must stay serveable, and the NJT
    envelope must report its own outage on its own block.

    THE LAST-KNOWN TRAINS ARE KEPT, unlike an EMPTY SUCCESS (the scenario below),
    and the pair is the whole distinction: a poll that failed knows nothing new, so
    the previous answer is still the best available; a poll that SUCCEEDED with no
    entities knows there are no trains.
    """
    with harness.launch() as app:
        _await_njt_trains(app)
        before = app.get("/api/njt-trains")
        assert before["trains"], "the outage has to start from a healthy state"

        harness.sim.set_mode("njt:tripupdate", "error")
        app.await_status(
            lambda s: s["feeds"].get("njt", {}).get("last_error") is not None,
            "the NJ Transit realtime poll to record its upstream failure",
        )
        # The subway is untouched and must prove it by advancing, not merely by
        # being non-null: a frozen-but-present subway cache would satisfy a
        # weaker assertion while the poll loop was actually wedged.
        harness.sim.await_polls("subway:1-7+S", 2)

        during = app.get("/api/njt-trains")
        assert during["trains"], (
            "a FAILED poll must keep the last-known trains: it learned nothing new, "
            "and the C2 block below is what tells the client to draw them as stale"
        )
        assert during["systems"]["njt"]["ok"] is False, during["systems"]
        assert app.status()["feeds"]["njt"]["last_error"], "the failure is named on /api/status"
        # `data`, not `trains`: the subway envelope predates the per-system naming
        # the NJT one uses.
        assert app.get("/api/subways")["data"], "one credentialed system down is not an outage"

        # And it heals on its own, which is what makes the degradation a state
        # rather than a terminal condition.
        harness.sim.set_mode("njt:tripupdate", "live")
        app._await(
            lambda: app.get("/api/njt-trains"),
            lambda body: body["systems"]["njt"]["ok"] is True,
            "the NJ Transit realtime poll to recover once its upstream returns",
            60.0,
            lambda last: f"last systems block: {json.dumps(last.get('systems'))}",
        )


def test_njt_overnight_empty_feed_is_a_served_state_not_a_failure(harness):
    """The 13-byte valid feed the overnight probe recorded (decoder law 6).

    ZERO TRAINS AT 03:00 IS THE CORRECT ANSWER. Retaining the evening's trains
    through the night would leave a map full of ghosts, so an EMPTY SUCCESS
    replaces the cache where a FAILURE keeps it. That divergence from the other
    systems is deliberate and is the only thing this scenario is about.

    It also pins that empty is not an error: /api/njt-trains serves 200 with an
    empty list, the system block stays ok, and nothing lands in degraded_systems.
    A C3 parse failure on a truly empty body would fail all three, which is why
    the simulator's NJT "empty" mode serves a valid entity-less feed rather than
    zero bytes.
    """
    with harness.launch() as app:
        _await_njt_trains(app)

        harness.sim.set_mode("njt:tripupdate", "empty")
        body = app._await(
            lambda: app.get("/api/njt-trains"),
            lambda last: last["trains"] == [],
            "the empty overnight feed to REPLACE the trains rather than be retained",
            60.0,
            lambda last: f"last /api/njt-trains: {json.dumps(last)[:2000]}",
        )
        assert body["systems"]["njt"]["ok"] is True, (
            "an empty feed decoded successfully, so the system is healthy; marking it "
            "degraded would put a permanent warning on every overnight deployment"
        )
        assert app.status()["feeds"]["njt"]["last_error"] is None
        # The board empties with it, from the same generation.
        assert app.get("/api/njt-arrivals/109")["arrivals"] == []


def test_njt_token_expiry_mid_poll_costs_exactly_one_mint_across_three_consumers(
    harness,
):
    """THE CONSERVATION CLAIM, made where it is hardest: three consumers at once.

    By 15b the static loader, the trains poller and the alerts poller all POST
    behind the SAME token, on three different cadences, from three different tasks.
    When that token dies they can meet the rejection concurrently. njt_auth's
    single-flight cache is what turns that into ONE re-mint instead of three, and
    the failure it prevents is spending mints against a rate limit NJ Transit does
    not publish, at the exact moment the integration is already in trouble.

    THE ASSERTION IS ARITHMETIC ON THE WIRE. The simulator rejects the first token
    it ever issued, on EVERY route, and accepts its replacement. Two getToken POSTs
    total is the whole claim: one cold, one to replace the dead token. A third
    would mean two consumers each minted their own.

    The realtime counter is what makes it non-vacuous: it proves the realtime
    routes were actually being polled across the expiry rather than idle.

    Hermetic counterpart: backend/tests/test_njt_auth.py's single-flight tests,
    which pin the same lock against concurrent callers with no sockets involved.
    """
    harness.sim.set_token_mode("reject-first")
    with harness.launch() as app:
        _await_njt_trains(app)
        # Let realtime poll several times past the recovery, so "no further mints"
        # is a claim about a RUNNING app rather than one that has polled once.
        harness.sim.await_polls("njt:tripupdate", 3)

        assert harness.sim.mint_requests() == 2, (
            "one dead token shared by three consumers must cost exactly one re-mint: "
            f"one cold, one replacement. Got {harness.sim.mint_requests()} getToken POSTs, "
            "which means a consumer minted its own instead of waiting on the shared lock."
        )
        assert harness.sim.rt_requests() >= 3, (
            "the claim is only meaningful if realtime was actually being polled across "
            f"the expiry; got {harness.sim.rt_requests()} realtime POSTs"
        )
        assert app.get("/api/njt-trains")["trains"], "and the app recovered inside the poll"
        assert app.status()["njt_static"] == "ready", (
            "the static loader recovered on the same token"
        )


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
