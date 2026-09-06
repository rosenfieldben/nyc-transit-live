#!/usr/bin/env python3
"""F13 (P2): "Railway healthchecks do not provide continuous recovery."

WHAT THE AUDIT CLAIMED (docs/reviews/audit-2026-09-05.md, section 1, F13, verbatim):

    "Some comments justify health decisions by saying Railway restarts a container
     on a failed healthcheck. Current Railway documentation says its configured
     endpoint is checked during deployment, not continuously after going live. A
     running process with a dead task or stale data therefore does not receive
     automatic recovery solely because `/healthz` changes to 503. The
     repository-defined production monitor runs every six hours."

RUN IT (from the repository root):

    .venv/bin/python docs/reviews/audit-2026-09-05/f13_healthcheck_recovery_gap.py

Exits 0 while the finding still behaves as recorded, non-zero with a message
naming the changed fact otherwise. It is a regression check on the audit record.

-----------------------------------------------------------------------------
DELIVERABLE 1: THE DOCUMENTATION QUESTION (citation, recorded not re-fetched)
-----------------------------------------------------------------------------
Fetched 2026-09-05 from https://docs.railway.com/deployments/healthchecks (HTTP
200, no redirect; the URL the audit cites is still the canonical page, titled
"Healthchecks"). Two sentences settle the question, quoted verbatim:

  Under the heading "How it works":
    "Note: Railway does not monitor the healthcheck endpoint after the deployment
     has gone live."

  Under the heading "Continuous healthchecks":
    "The healthcheck endpoint is currently not used for continuous monitoring as
     it is only called at the start of the deployment, to ensure it is healthy
     prior to routing traffic to it."

  Same page, under "How it works", on what the probe actually gates:
    "When a new deployment is triggered for a service, if a healthcheck endpoint
     is configured, Railway will query the endpoint until it receives a successful
     response (any 2xx status code). Only then will the new deployment be made
     active and the previous deployment inactive."

  Same page, under "Healthcheck timeout", on what a failing probe costs:
    "If your application fails to serve a 2xx status code during this allotted
     time, the deploy will be marked as failed."

The page the healthchecks page links to next, https://docs.railway.com/deployments/restart-policy
(fetched 2026-09-05, HTTP 200), is where restarts are actually defined, and its
trigger is a process exit rather than a probe response:

    "The restart policy dictates what action Railway should take if a deployed
     service stops, e.g., exits with a non-zero exit code."
    "On Failure : Railway will only restart your service if it stops due to an
     error (e.g., crashes, exits with a non-zero code)."

There is no ambiguity to report: the documentation states the negative explicitly
("does not monitor ... after the deployment has gone live"), under a heading named
for the exact question. Neither page describes any path from a 503 on a live
deployment to a container restart. This script does NOT re-fetch those pages: it
stays hermetic and offline, and the citation above is the record.

-----------------------------------------------------------------------------
WHAT THIS SCRIPT MEASURES (deliverables 2 and 3, as executable checks)
-----------------------------------------------------------------------------
PART 1  The repository-defined cadence, re-derived rather than repeated: the cron
        in .github/workflows/contract-monitor.yml is parsed and its firing times
        enumerated, railway.json is parsed, and FEED_STALE_AFTER_S / POLL_INTERVAL_S
        are read off the real production modules.

PART 2  THE COMMENT INVENTORY. A window scan over `git ls-files` (tracked files
        only) for every line mentioning a restart within three lines of health
        vocabulary, then a classification of each hit against a recorded table:
        class A reasons FROM the restart mechanism TO a status-code decision,
        class B reasons from a restart but decides no status code, class C only
        configures or mentions. Every candidate must be classified or the check
        fails, so a newly added restart-justified comment cannot slip in unnoticed.

PART 3  THE MONITORING GAP. The REAL FastAPI app from backend/main.py is started
        through its REAL lifespan, its REAL poll loop (pollers._poll_feeds) runs a
        real cycle through the real refreshers, and then a fault is injected to
        kill it. The app is then driven over ASGI and its own /healthz and
        /api/status handlers answer.

PRODUCTION CODE EXERCISED IN PART 3 (nothing here is re-implemented):
  main.app, main.lifespan, pollers._poll_feeds, pollers._refresh_buses and its five
  siblings, pollers._bounded_refresh, pollers._total_refresh, routes/status.py's
  healthz and get_status handlers, routes/status.py::_health_codes, cache._serve_cached,
  models.HEALTH_* codes, cache.FEED_STALE_AFTER_S.

INJECTED (explicitly, because no committed fixture can supply these faults):
  1. Network stubs for the static loaders and the upstream fetchers, copied in
     shape from backend/tests/test_api.py::test_lifespan_starts_polls_and_shuts_down_cleanly,
     so startup and polling reach no network at all. NJ Transit stays unconfigured
     (credentials are scrubbed from the environment below), so no NJT mint is spent
     and no NJT host is contacted.
  2. THE FAULT ITSELF: on the second poll cycle the stubbed bus fetcher raises a
     BaseException subclass. That is not caught by _poll_feeds's `except Exception`
     (its own comment says so), so the real poll loop dies exactly as that comment
     warns it could. The app keeps running.
  3. AGE, NOT WALL CLOCK. Feed ages are set by rewriting the cache entries'
     recorded fetched_at, never by sleeping and never against today's date, so the
     90 second boundary is crossed deterministically in milliseconds.

No em-dashes anywhere in this file, by house rule.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))

# Hermetic by construction: run as an UNCONFIGURED deployment whatever the machine
# holds, so the NJT warmup and the NJT refresher both short-circuit before any
# network call and no mint can be spent. Same discipline as backend/tests/conftest.py.
for _var in ("NJT_USERNAME", "NJT_PASSWORD", "BUS_TIME_API_KEY", "MTA_BUS_API_KEY"):
    os.environ.pop(_var, None)

FAILURES: list[str] = []
LINES: list[str] = []


def say(text: str = "") -> None:
    LINES.append(text)
    print(text)


def check(ok: bool, label: str, detail: str = "") -> None:
    """Record one assertion of the audit record. Collected, not raised, so one
    changed fact does not hide the rest of the measurement."""
    mark = "ok  " if ok else "FAIL"
    say(f"  [{mark}] {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILURES.append(label + (f": {detail}" if detail else ""))


def rule(title: str) -> None:
    say()
    say("=" * 78)
    say(title)
    say("=" * 78)


# ---------------------------------------------------------------------------
# PART 1: the repository-defined monitoring cadence
# ---------------------------------------------------------------------------


def cron_fire_minutes(expr: str) -> list[int]:
    """Every minute-of-day a 5 field cron expression fires on an ordinary day.

    Deliberately tiny and date independent: the expression under test has `*` for
    both day fields, so no calendar and no wall clock is involved.
    """
    minute_f, hour_f, dom_f, month_f, dow_f = expr.split()
    if dom_f != "*" or month_f != "*" or dow_f != "*":
        raise ValueError(f"this parser only handles date independent crons, got {expr!r}")

    def expand(field: str, hi: int) -> list[int]:
        out: set[int] = set()
        for part in field.split(","):
            step = 1
            if "/" in part:
                part, step_s = part.split("/")
                step = int(step_s)
            if part == "*":
                lo_v, hi_v = 0, hi
            elif "-" in part:
                lo_s, hi_s = part.split("-")
                lo_v, hi_v = int(lo_s), int(hi_s)
            else:
                lo_v = hi_v = int(part)
            out.update(range(lo_v, hi_v + 1, step))
        return sorted(out)

    minutes = expand(minute_f, 59)
    hours = expand(hour_f, 23)
    return sorted(h * 60 + m for h in hours for m in minutes)


def part1_cadence() -> dict:
    rule("PART 1: the repository-defined monitoring cadence, re-derived")

    wf = (REPO / ".github/workflows/contract-monitor.yml").read_text(encoding="utf-8")
    crons = re.findall(r'^\s*-\s*cron:\s*"([^"]+)"', wf, flags=re.M)
    say(f"  .github/workflows/contract-monitor.yml cron entries : {crons}")

    all_crons = subprocess.run(
        ["git", "grep", "-h", "-E", r"^\s*- cron:", "--", ".github/workflows"],
        cwd=REPO,
        capture_output=True,
        text=True,
    ).stdout.split("\n")
    scheduled = [c.strip() for c in all_crons if c.strip()]
    say(f"  every scheduled workflow in .github/workflows      : {len(scheduled)} cron line(s)")

    fires = cron_fire_minutes(crons[0])
    gaps = [(fires[(i + 1) % len(fires)] - fires[i]) % 1440 for i in range(len(fires))]
    interval_s = gaps[0] * 60
    clock = ", ".join(f"{m // 60:02d}:{m % 60:02d}" for m in fires)
    say(f"  fires per UTC day                                  : {len(fires)}  ({clock} UTC)")
    say(f"  gaps between consecutive fires (minutes)           : {sorted(set(gaps))}")
    say(f"  monitor interval                                   : {interval_s} s "
        f"= {interval_s / 3600:.1f} h")

    railway = json.loads((REPO / "railway.json").read_text(encoding="utf-8"))
    deploy = railway["deploy"]
    say(f"  railway.json healthcheckPath                       : {deploy.get('healthcheckPath')}")
    say(f"  railway.json healthcheckTimeout                    : "
        f"{deploy.get('healthcheckTimeout')} s")
    say(f"  railway.json restartPolicyType                     : {deploy.get('restartPolicyType')}"
        f" (max {deploy.get('restartPolicyMaxRetries')} retries)")
    say("  restartPolicyType triggers on a process exit, not on a probe response;")
    say("  see the Railway restart-policy quotation in this file's header.")

    # main first: pollers imports main and main imports pollers, so the app module
    # has to be the entry point of that cycle (the test suite imports it the same way).
    import main  # noqa: F401  (production module, imported for its side effect on the cycle)

    import cache as cache_mod  # production module
    import pollers  # production module

    stale_s = cache_mod.FEED_STALE_AFTER_S
    poll_s = pollers.POLL_INTERVAL_S
    say(f"  cache.FEED_STALE_AFTER_S                           : {stale_s} s")
    say(f"  pollers.POLL_INTERVAL_S                            : {poll_s} s")
    say()
    say("  THE ARITHMETIC OF THE GAP")
    say(f"    poll cycles missed inside one monitor interval   : "
        f"{interval_s} / {poll_s} = {interval_s // poll_s:.0f}")
    say(f"    /healthz turns 503 after the last poll at        : {stale_s} s")
    say(f"    a 503 can go unread for up to                   : "
        f"{interval_s} - {stale_s} = {interval_s - stale_s} s "
        f"= {(interval_s - stale_s) / 3600:.2f} h")
    say(f"    time from task death to detection, worst case   : "
        f"{interval_s} s = {interval_s / 3600:.1f} h")
    say(f"    monitor interval / staleness threshold           : "
        f"{interval_s} / {stale_s} = {interval_s / stale_s:.0f}x")
    say()
    say("  WHAT THE MONITOR CHECKS when it does run (backend/scripts/contract_monitor.py,")
    say("  check_production): /api/status reachability, each static group's state, per feed")
    say("  poll freshness, alert-system degradation, the /healthz degraded classification,")
    say("  and whether served_at is built rather than replayed. WHAT CHECKS IN BETWEEN:")
    say("  nothing in this repository. Railway polls /healthz only while promoting a")
    say("  deployment, and no other scheduled workflow exists.")

    check(len(crons) == 1, "contract-monitor.yml declares exactly one cron", str(crons))
    check(crons[0] == "17 */6 * * *", "the cron is the audited one", crons[0])
    check(len(scheduled) == 1, "it is the only scheduled workflow in the repository",
          f"{len(scheduled)} cron line(s) tracked")
    check(len(fires) == 4 and set(gaps) == {360},
          "the audit's 'every six hours' is exact", f"{len(fires)} fires/day, gaps={set(gaps)}")
    check(deploy.get("healthcheckPath") == "/healthz",
          "railway.json points the healthcheck at /healthz")
    check(deploy.get("restartPolicyType") == "ON_FAILURE",
          "railway.json restart policy is ON_FAILURE (a process exit trigger)")
    check(stale_s == 90 and poll_s == 20,
          "the freshness and poll constants are as measured", f"{stale_s}s / {poll_s}s")

    return {
        "cron": crons[0],
        "interval_s": interval_s,
        "stale_s": stale_s,
        "poll_s": poll_s,
        "fires": len(fires),
    }


# ---------------------------------------------------------------------------
# PART 2: the comment inventory
# ---------------------------------------------------------------------------

# The mechanical scan: any tracked line mentioning a restart whose three line
# neighbourhood also mentions health vocabulary. A window rather than a single
# line because the load bearing sentences wrap across lines (models.py splits
# "Railway restarts a / container on a failing healthcheck" over two).
RESTART_RE = re.compile(r"restart", re.I)
HEALTH_RE = re.compile(
    r"healthz|healthcheck|health check|status code|503|gating|degraded|readiness probe|container",
    re.I,
)
WINDOW = 3

# docs/ is excluded and the exclusion is deliberate, for two reasons that both
# have to hold. First, docs/ holds historical review narratives (including the
# audit report this script verifies), which DESCRIBE decisions rather than justify
# them beside the code. Second, and this is the trap that actually bit: THIS SCRIPT
# LIVES IN docs/reviews/, and so does f07. An unscoped scan for a symbol quotes
# itself, so a scan written while these files were untracked passes and the same
# scan fails the moment they are committed. Every tree scan in this file goes
# through one of the two constants below so the rule is stated once.
# Application code, tests and the contract tier are all in scope.
SCAN_SKIP_PREFIXES = ("docs/",)
# The same rule as a git pathspec, for the scans that let git do the filtering.
SCAN_PATHSPEC = [f":(exclude){prefix}" for prefix in SCAN_SKIP_PREFIXES]

CLASS_A = "A"  # reasons FROM the restart mechanism TO a status-code decision
CLASS_B = "B"  # reasons from a restart, but decides no status code
CLASS_C = "C"  # configures or mentions only

# The recorded inventory. Keyed by (path, snippet) so it survives line drift; the
# measured line number is printed. `decision` says what a later fix is re-deciding.
INVENTORY = [
    # ---- class A: a restart mechanism justifying a status code -------------
    ("README.md", "restart is still visible to something that watches", CLASS_A,
     "publishing `degraded` as a superset of `reasons` on a 200"),
    ("README.md", "it never gates the 503, because a restart would mint again", CLASS_A,
     "njt-mint-quota stays 200, never 503"),
    ("backend/models.py", "Railway restarts a", CLASS_A,
     "HEALTH_GATING_CODES membership: which codes make /healthz answer 503"),
    ("backend/models.py", "Restarting on it", CLASS_A,
     "HEALTH_NJT_MINT_QUOTA excluded from HEALTH_GATING_CODES (200)"),
    ("backend/models.py", "deliberately not worth a restart", CLASS_A,
     "HealthzResponse carries degraded as a superset of the 503 reasons"),
    ("backend/routes/status.py", "a container restart: the upstream is what is late", CLASS_A,
     "HEALTH_FEED_CONTENT_STALE is non-gating (200)"),
    ("backend/routes/status.py", "a restart would spend another mint", CLASS_A,
     "HEALTH_NJT_MINT_QUOTA is non-gating (200)"),
    ("backend/routes/status.py", "Railway restarts a container on a failing healthcheck", CLASS_A,
     "the whole status-code versus classification split in the healthz handler"),
    ("backend/tests/test_api.py", "a lagging upstream is not fixed by restarting the container",
     CLASS_A, "test_healthz_lenient_one_fresh_other_stale asserts 200"),
    ("backend/tests/test_api.py", "restart does not merely fail to help, it mints", CLASS_A,
     "test_healthz_publishes_a_spent_njt_mint_budget_without_gating_on_it asserts 200"),
    ("backend/tests/test_api.py", "a spent budget must never restart the container", CLASS_A,
     "the assertion message on that same 200"),
    ("backend/tests/test_api.py", "a restart would not fix it", CLASS_A,
     "test_healthz_subway_groups_down_is_a_strict_majority asserts 200"),
    ("backend/tests/test_contract_monitor.py", "must not make Railway restart the container",
     CLASS_A, "the monitor FAILs a run on a degraded code that rode a 200"),
    ("tests/contract/test_contract_api.py", "Railway restarts a container on a failing healthcheck "
     "and a fresh process would", CLASS_A,
     "contract tier: stale upstream content reaches /healthz without moving the status code"),
    ("tests/contract/test_contract_api.py", "A spent budget is not a reason to restart the",
     CLASS_A, "contract tier: the probe still answers 200 on a spent NJT mint budget"),
    ("tests/contract/test_contract_api.py", "status code. Railway restarts a container", CLASS_A,
     "contract tier: feed-content-stale is published without touching the status code"),
    ("tests/contract/test_contract_api.py", "a lagging upstream is not a reason to restart",
     CLASS_A, "the assertion message pinning status == pass"),
    ("tests/contract/test_contract_api.py", "a restart does not bring them back", CLASS_A,
     "contract tier: subway-groups-down asserts status == pass"),
    # ---- class B: restart reasoning, but no status code decided -----------
    ("backend/models.py", 'rather than "restart"', CLASS_B,
     "why njt-mint-quota gets its own code name, not what the code is"),
    ("backend/routes/buses.py", "restart the server to retry", CLASS_B,
     "the /api/bus-route 503 detail text (an operator instruction, not a probe decision)"),
    ("backend/tests/test_api.py", "restart is needed (the warmup retries automatically)", CLASS_B,
     "the subway warming 503 detail no longer tells anyone to restart"),
    ("backend/tests/test_api.py", 'assert "restart" not in err["detail"].lower()', CLASS_B,
     "the assertion pinning that detail text"),
    ("backend/tests/test_api.py", "a process restart with credentials must be picked up", CLASS_B,
     "/api/njt-stops must not be cached while unconfigured"),
    ("frontend/helpers.js", "A backend restart while a feed is still failing", CLASS_B,
     "frontend degraded-set membership when fetched_at is null"),
    ("frontend/helpers.test.js", "a backend restart while a feed is", CLASS_B,
     "the test pinning that frontend behavior"),
    # ---- class C: configuration and bare mentions --------------------------
    ("railway.json", '"restartPolicyType"', CLASS_C, "the platform restart policy itself"),
    ("railway.json", '"restartPolicyMaxRetries"', CLASS_C, "its retry ceiling"),
]

# The subset that names the platform outright rather than reasoning from an
# unattributed "restart". These are the sentences that state a mechanism. Matched
# against the hit's WINDOW rather than its single line, because the sentences wrap
# ("A spent budget is not a reason to restart the / container: Railway would, ...").
NAMES_RAILWAY_RE = re.compile(r"Railway (restarts?|would)", re.I)


def part2_inventory() -> dict:
    rule("PART 2: the comment inventory (tracked files, mechanical scan)")

    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split("\n")

    hits = []
    scanned = 0
    skipped = 0
    for rel in tracked:
        rel = rel.strip()
        if not rel:
            continue
        if rel.startswith(SCAN_SKIP_PREFIXES):
            skipped += 1
            continue
        scanned += 1
        path = REPO / rel
        try:
            lines = path.read_text(encoding="utf-8").split("\n")
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(lines):
            if not RESTART_RE.search(line):
                continue
            window = "\n".join(lines[max(0, i - WINDOW): i + WINDOW + 1])
            if HEALTH_RE.search(window):
                hits.append((rel, i + 1, line.strip(), window))

    # Counted after the skip, not before. The earlier version reported every
    # tracked file as "scanned" including the docs/ ones it never opened, which
    # made the excluded directory invisible in the output.
    say(f"  tracked files scanned                   : {scanned}")
    say(f"  tracked files skipped ({', '.join(SCAN_SKIP_PREFIXES)})           : {skipped}")
    say(f"  candidate lines (restart within {WINDOW} lines of health vocabulary) : {len(hits)}")
    say(f"  scan excluded prefixes                  : {list(SCAN_SKIP_PREFIXES)}")

    # Classify every candidate against the recorded table.
    unclassified = []
    matched: dict[tuple[str, str], list] = {}
    for rel, lineno, text, window in hits:
        for path_key, snippet, cls, decision in INVENTORY:
            if rel == path_key and snippet in text:
                matched.setdefault((path_key, snippet), []).append(
                    (lineno, text, cls, decision, window)
                )
                break
        else:
            unclassified.append((rel, lineno, text))

    by_class = {CLASS_A: [], CLASS_B: [], CLASS_C: []}
    for (path_key, snippet), found in matched.items():
        for lineno, text, cls, decision, window in found:
            by_class[cls].append((path_key, lineno, text, decision, window))
    for key in by_class:
        by_class[key].sort()

    say()
    say("  CLASS A. Comments that reason FROM the restart mechanism TO a status-code")
    say("  decision. These are the ones that state a mechanism the platform docs deny.")
    say()
    named = 0
    for rel, lineno, text, decision, window in by_class[CLASS_A]:
        flag = ""
        if NAMES_RAILWAY_RE.search(window):
            named += 1
            flag = "  <- names Railway outright"
        say(f"    {rel}:{lineno}{flag}")
        say(f'        "{text}"')
        say(f"        justifies: {decision}")
    say()
    say(f"  Class A lines naming Railway explicitly : {named} of {len(by_class[CLASS_A])}")
    say("  (the rest reason from an unattributed 'restart' inherited from those sentences)")

    say()
    say("  CLASS B. Restart reasoning that decides no status code. Not part of the")
    say("  mechanism claim, listed so the inventory is complete.")
    for rel, lineno, text, decision, _w in by_class[CLASS_B]:
        say(f"    {rel}:{lineno}  \"{text[:88]}\"")
        say(f"        about: {decision}")

    say()
    say("  CLASS C. Configuration and bare mentions. These are fine.")
    for rel, lineno, text, decision, _w in by_class[CLASS_C]:
        say(f"    {rel}:{lineno}  \"{text[:88]}\"  ({decision})")

    if unclassified:
        say()
        say("  UNCLASSIFIED (new since the audit, needs a verdict):")
        for rel, lineno, text in unclassified:
            say(f"    {rel}:{lineno}  \"{text[:88]}\"")

    missing = [
        f"{p}::{s}" for p, s, _c, _d in INVENTORY if (p, s) not in matched
    ]

    say()
    check(not missing, "every recorded inventory entry is still present in the tree",
          "; ".join(missing) if missing else "")
    check(not unclassified,
          "no unclassified restart-and-health comment has appeared since the audit",
          "; ".join(f"{r}:{n}" for r, n, _t in unclassified) if unclassified else "")
    check(len(by_class[CLASS_A]) == 18,
          "18 comment lines reason from a restart to a health status code",
          f"measured {len(by_class[CLASS_A])}")
    check(named == 6,
          "6 of them name Railway outright as the actor that restarts",
          f"measured {named}")
    files_a = sorted({rel for rel, _l, _t, _d, _w in by_class[CLASS_A]})
    say(f"  files carrying class A comments         : {len(files_a)}")
    for f in files_a:
        say(f"    {f}")
    check(len(files_a) == 6,
          "class A spans application code, tests and the contract tier",
          f"{len(files_a)} files")

    return {
        "candidates": len(hits),
        "class_a": len(by_class[CLASS_A]),
        "class_b": len(by_class[CLASS_B]),
        "class_c": len(by_class[CLASS_C]),
        "named_railway": named,
        "files_a": files_a,
    }


def part2b_supervision() -> dict:
    """Nothing in the running app watches the poll task. A grep, not an opinion."""
    rule("PART 2b: who supervises the background poll task")
    out = subprocess.run(
        ["git", "grep", "-n", "feed_poll_task", "--", *SCAN_PATHSPEC],
        cwd=REPO, capture_output=True, text=True,
    ).stdout.strip().split("\n")
    refs = [line for line in out if line.strip()]
    # The scan must not be able to see itself. Without the pathspec above this
    # grep matches its own source and f07's, which is exactly how a supervisor
    # count of 0 turned into 7 the moment these files were committed.
    self_seen = [r for r in refs if r.startswith(SCAN_SKIP_PREFIXES)]
    check(not self_seen,
          "the supervision scan cannot see the review directory it lives in",
          "; ".join(r[:80] for r in self_seen) if self_seen else "")
    for line in refs:
        say(f"    {line.strip()[:110]}")
    main_refs = [r for r in refs if r.startswith("backend/main.py:")]
    test_refs = [r for r in refs if r.startswith("backend/tests/")]
    other = [r for r in refs if r not in main_refs and r not in test_refs]
    say()
    say(f"  references in backend/main.py (create + cancel + await) : {len(main_refs)}")
    say(f"  references in the test suite                            : {len(test_refs)}")
    say(f"  references anywhere else (a supervisor would be here)   : {len(other)}")
    check(not other,
          "no request handler, poller or monitor reads the poll task's liveness",
          "; ".join(other) if other else "")
    return {"main": len(main_refs), "tests": len(test_refs), "other": len(other)}


# ---------------------------------------------------------------------------
# PART 3: the monitoring gap, demonstrated against the real app
# ---------------------------------------------------------------------------


class InjectedPollerDeath(BaseException):
    """The injected fault. A BaseException on purpose: pollers._poll_feeds catches
    `except Exception` and its own comment says a BaseException passes straight
    through, which is the death this finding is about."""


# Payload shapes copied from backend/tests/test_api.py so the real response models
# validate; the values are arbitrary and only their identity across probes matters.
BUSES = [
    {"id": "MTA NYCT_1", "route_id": "M15", "latitude": 40.7, "longitude": -74.0, "bearing": 90.0}
]
TRAINS = [
    {
        "trip_id": "70000_1..N01R",
        "route_id": "1",
        "latitude": 40.7,
        "longitude": -74.0,
        "stop_id": "101N",
        "stop_name": "Alpha",
        "direction": "Northbound",
        "prev_lat": 40.69,
        "prev_lon": -74.01,
        "prev_time": 999.0,
        "next_time": 1002.0,
    }
]


async def part3_dead_task(cadence: dict) -> dict:
    rule("PART 3: a running app whose poll task has died (real app, real lifespan)")

    import httpx

    import bus_static
    import ferry_static
    import main as app_module
    import models
    import path_static
    import pollers
    import railroad_static

    kill = {"armed": False, "cycles": 0}

    async def fake_fetch_buses(client):
        kill["cycles"] += 1
        if kill["armed"]:
            raise InjectedPollerDeath("injected: the bus fetch dies with a BaseException")
        return BUSES, time.time() - 5.0

    async def fake_fetch_subways(stops, client):
        return TRAINS, {}, time.time() - 5.0, [], {"ACE": TRAINS}, {"ACE": {}}

    async def fake_fetch_railroads(client, stops):
        return [], {}, time.time() - 5.0, []

    async def fake_fetch_path(client, stops):
        return [], {}, time.time() - 5.0, 0

    async def fake_fetch_ferry(client, ferry_static_tables):
        return [], {}, time.time() - 5.0

    async def fake_stops():
        return {"101N": {"name": "Alpha", "lat": 40.7, "lon": -74.0}}

    # These three return nothing loadable, so their warmups log "failed" and schedule
    # a retry. That is deliberate and costs the measurement nothing: railroad, PATH and
    # ferry static are not /healthz inputs (only the subway static group is, and its
    # stub above reaches "ready"), so the probe's answer is decided entirely by feed
    # freshness, which is what this part measures.
    async def fake_load_railroad_static():
        return {"LIRR": None, "MNR": None}

    async def fake_load_path_static():
        return {}

    async def fake_load_ferry_static():
        return {}

    async def fake_ensure_index():
        return None

    async def fake_refresh_alerts(app, client):
        # No network. The alerts feed is not a /healthz input; this stub only keeps
        # the second background task off the wire.
        entry = app.state.alerts_cache
        entry.update(alerts=[], fetched_at=time.time(), error=None)

    patches = [
        (app_module, "load_subway_stops", fake_stops),
        (app_module, "load_subway_route_shapes", lambda: []),
        (app_module, "load_subway_stations", lambda: {}),
        (app_module, "load_subway_station_routes", lambda: {}),
        (app_module, "fetch_vehicle_positions", fake_fetch_buses),
        (app_module, "fetch_subway_trains", fake_fetch_subways),
        (app_module, "fetch_railroad_trains", fake_fetch_railroads),
        (app_module, "fetch_path_trains", fake_fetch_path),
        (app_module, "fetch_ferry_data", fake_fetch_ferry),
        (railroad_static, "load_railroad_static", fake_load_railroad_static),
        (path_static, "load_path_static", fake_load_path_static),
        (ferry_static, "load_ferry_static", fake_load_ferry_static),
        (bus_static, "ensure_index", fake_ensure_index),
        (pollers, "_refresh_alerts", fake_refresh_alerts),
        (pollers, "POLL_INTERVAL_S", 0.05),
        (pollers, "ALERT_POLL_INTERVAL_S", 0.05),
    ]
    saved = [(obj, name, getattr(obj, name)) for obj, name, _new in patches]
    for obj, name, new in patches:
        setattr(obj, name, new)

    result: dict = {}
    shutdown_noticed = False
    try:
        app = app_module.app
        # THE ONLY PLACE THE APP EVER NOTICES. main.lifespan awaits every background
        # task on shutdown, suppressing only CancelledError, so the dead poll task's
        # exception surfaces there and nowhere else. Caught here because it is this
        # script's own injected fault; anything else is re-raised untouched.
        try:
            async with app_module.lifespan(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                    # Wait for the REAL poll loop to complete one real cycle.
                    for _ in range(400):
                        if app.state.feed_cache["buses"]["data"] is not None:
                            break
                        await asyncio.sleep(0.01)

                    # Hermeticity, asserted rather than promised in prose: with the
                    # credentials scrubbed above the NJT warmup reaches
                    # "not-configured" without a single request, so no NJ Transit host
                    # is contacted and no mint is spent by this script.
                    check(app.state.njt_static_status == "not-configured",
                          "NJ Transit stayed unconfigured, so no mint was spent",
                          str(app.state.njt_static_status))

                    healthy = await c.get("/healthz")
                    buses_before = await c.get("/api/buses")
                    say("  BEFORE THE FAULT (the poll loop is alive)")
                    say(f"    poll task done                : {app.state.feed_poll_task.done()}")
                    say(f"    /healthz                      : {healthy.status_code} "
                        f"{json.dumps(healthy.json())}")
                    say(f"    /api/buses                    : {buses_before.status_code}, "
                        f"{len(buses_before.json()['data'])} vehicle(s)")
                    check(healthy.status_code == 200, "a live app with a fresh feed answers 200")

                    # ---- inject the fault ----
                    kill["armed"] = True
                    for _ in range(400):
                        if app.state.feed_poll_task.done():
                            break
                        await asyncio.sleep(0.01)

                    task = app.state.feed_poll_task
                    exc = None
                    if task.done() and not task.cancelled():
                        exc = task.exception()
                    leaves = []
                    if isinstance(exc, BaseExceptionGroup):
                        def walk(group):
                            for e in group.exceptions:
                                if isinstance(e, BaseExceptionGroup):
                                    walk(e)
                                else:
                                    leaves.append(type(e).__name__)
                        walk(exc)

                    say()
                    say("  THE FAULT: the stubbed bus fetch raises a BaseException on cycle 2.")
                    say(f"    poll cycles run before death  : {kill['cycles']}")
                    say(f"    feed_poll_task.done()         : {task.done()}")
                    say(f"    feed_poll_task.cancelled()    : {task.cancelled()}")
                    say(f"    exception type                : {type(exc).__name__}")
                    say(f"    leaf exceptions               : {sorted(set(leaves))}")
                    say("    the process did NOT exit: this coroutine is still running, so")
                    say("    railway.json's ON_FAILURE restart policy has nothing to fire on.")
                    check(task.done() and not task.cancelled(),
                          "the real pollers._poll_feeds loop is dead")
                    check("InjectedPollerDeath" in leaves,
                          "it died of the injected fault, uncaught by its `except Exception`")

                    # ---- the app keeps serving ----
                    after = {}
                    for path in ("/healthz", "/api/status", "/api/buses", "/api/subways"):
                        r = await c.get(path)
                        after[path] = (r.status_code, r.json())
                    say()
                    say("  IMMEDIATELY AFTER THE DEATH (feed ages still under the threshold)")
                    for path, (code, body) in after.items():
                        extra = ""
                        if path == "/healthz":
                            extra = f"  {json.dumps(body)}"
                        if path == "/api/status":
                            extra = ("  feeds.buses.age_s="
                                     f"{body['feeds']['buses']['age_s']}")
                        say(f"    {path:<14} -> {code}{extra}")
                    check(after["/healthz"][0] == 200,
                          "/healthz still answers 200 right after the poll task dies")
                    check(after["/api/buses"][0] == 200 and
                          after["/api/buses"][1]["data"] == buses_before.json()["data"],
                          "/api/buses serves the identical frozen payload")

                    # ---- age the recorded polls, deterministically ----
                    say()
                    say("  NOW AGE THE RECORDED POLLS. fetched_at is rewritten (no sleeping, no")
                    say("  dependence on today's date); feed_timestamp is kept 5.0 s behind it so")
                    say("  upstream content age stays healthy and only the POLL-STALL term moves.")
                    say()
                    say("    injected poll age   /healthz   status  reasons                     "
                        "/api/status  /api/buses")

                    stale_s = cadence["stale_s"]
                    rows = []
                    for offset in (float(stale_s) - 1.0, float(stale_s), float(stale_s) + 1.0):
                        now = time.time()
                        for entry in app.state.feed_cache.values():
                            if entry["data"] is not None:
                                entry["fetched_at"] = now - offset
                                entry["feed_timestamp"] = now - offset - 5.0
                        hz = await c.get("/healthz")
                        st = await c.get("/api/status")
                        bs = await c.get("/api/buses")
                        body = hz.json()
                        rows.append((offset, hz.status_code, body, st.status_code, bs.status_code,
                                     st.json()["feeds"]["buses"]["age_s"]))
                        say(f"    {offset:>13.1f} s   {hz.status_code:>8}   "
                            f"{body['status']:<6}  {str(body.get('reasons', [])):<27} "
                            f"{st.status_code:>11}  {bs.status_code:>9}")

                    below, at, above = rows
                    say()
                    say(f"    degraded codes at {above[0]:.0f} s of poll age : "
                        f"{above[2]['degraded']}")
                    say(f"    /api/status feeds.buses.age_s              : {above[5]}")
                    frozen = (await c.get("/api/buses")).json()["data"]
                    say(f"    /api/buses payload identical to before     : "
                        f"{frozen == buses_before.json()['data']}")

                    check(below[1] == 200,
                          f"under the threshold ({below[0]:.0f}s) /healthz is still 200")
                    check(at[1] == 503 and above[1] == 503,
                          f"at and past {stale_s}s of poll age /healthz answers 503")
                    check(above[2]["reasons"] == ["no feed has fresh data"],
                          "the 503 reason is the stalled poll loop", str(above[2]["reasons"]))
                    check(models.HEALTH_NO_FEED_FRESH in above[2]["degraded"],
                          "and the machine readable code says the same")
                    check(above[3] == 200 and above[4] == 200,
                          "meanwhile /api/status and /api/buses keep answering 200")

                    # ---- what /api/status can and cannot say ----
                    status_body = (await c.get("/api/status")).json()
                    keys = sorted(status_body.keys())
                    liveness = [k for k in keys
                                if re.search(r"task|heartbeat|alive|supervis|poller", k, re.I)]
                    say()
                    say(f"  /api/status top level keys : {keys}")
                    say(f"  keys reporting task liveness or a heartbeat : {liveness or 'none'}")
                    check(not liveness,
                          "/api/status has no field that says the poll task is dead",
                          str(liveness) if liveness else "")

                    result = {
                        "cycles": kill["cycles"],
                        "healthz_after_death": after["/healthz"][0],
                        "flip_at_s": stale_s,
                        "healthz_stale": above[1],
                        "status_stale": above[3],
                        "buses_stale": above[4],
                        "reasons": above[2].get("reasons"),
                        "degraded": above[2]["degraded"],
                        "status_keys": len(keys),
                    }
        except BaseExceptionGroup as group:
            injected, other = group.split(InjectedPollerDeath)
            if other is not None:
                raise other
            shutdown_noticed = injected is not None

    finally:
        for obj, name, old in saved:
            setattr(obj, name, old)

    if shutdown_noticed:
        say()
        say("  ON SHUTDOWN, main.lifespan awaited the dead poll task and its exception")
        say("  finally surfaced. That is the only place in the application where the")
        say("  death is observable at all, and it is observable only once the process")
        say("  is already going away.")
    return result


# ---------------------------------------------------------------------------


def main() -> int:
    say("F13 verification: Railway healthchecks do not provide continuous recovery")
    say(f"repository: {REPO}")
    say("Railway documentation is quoted in this file's header, fetched 2026-09-05.")
    say("It is not re-fetched at runtime, so this script is hermetic and offline.")

    cadence = part1_cadence()
    inventory = part2_inventory()
    supervision = part2b_supervision()
    with contextlib.suppress(KeyboardInterrupt):
        gap = asyncio.run(part3_dead_task(cadence))

    rule("SUMMARY OF THE MEASURED NUMBERS")
    say(f"  monitor cron                              : {cadence['cron']} "
        f"({cadence['fires']} fires/day, {cadence['interval_s']} s apart)")
    say(f"  /healthz turns 503 after                  : {cadence['stale_s']} s of poll stall")
    say(f"  a 503 can go unread for up to             : "
        f"{cadence['interval_s'] - cadence['stale_s']} s "
        f"({(cadence['interval_s'] - cadence['stale_s']) / 3600:.2f} h)")
    say(f"  death to detection, worst case            : {cadence['interval_s']} s "
        f"({cadence['interval_s'] / 3600:.1f} h)")
    say(f"  poll cycles missed in one monitor gap     : "
        f"{cadence['interval_s'] // cadence['poll_s']:.0f}")
    say(f"  restart-and-health comment candidates     : {inventory['candidates']}")
    say(f"    class A (restart -> status code)        : {inventory['class_a']} "
        f"across {len(inventory['files_a'])} files, {inventory['named_railway']} naming Railway")
    say(f"    class B (restart, no status code)       : {inventory['class_b']}")
    say(f"    class C (configuration or mention)      : {inventory['class_c']}")
    say(f"  supervisors of the feed poll task         : {supervision['other']}")
    say(f"  poll cycles before the injected death     : {gap['cycles']}")
    say(f"  /healthz right after the task died        : {gap['healthz_after_death']}")
    say(f"  /healthz at {gap['flip_at_s']} s of poll age          : "
        f"{gap['healthz_stale']} reasons={gap['reasons']}")
    say(f"  /api/status and /api/buses in that state  : "
        f"{gap['status_stale']} and {gap['buses_stale']} (still serving frozen data)")

    say()
    say("  HONEST CAVEAT, which the audit states too: external monitoring may exist in")
    say("  Railway platform settings or in a third party uptime service. Neither is")
    say("  inspectable from this repository, so this script measures only what the")
    say("  repository defines, and the Railway documentation quoted above is what")
    say("  settles the platform half.")

    rule("VERDICT")
    if FAILURES:
        say("  The audit record no longer matches reality. Changed facts:")
        for f in FAILURES:
            say(f"    - {f}")
        say()
        say("DISPOSITION: REFUTED (this script's recorded expectations no longer hold; "
            "see the failures above)")
        return 1

    say("  Every recorded expectation held.")
    say()
    say("DISPOSITION: VERIFIED  Railway documents that /healthz is polled only while a "
        "deployment is promoted, 18 comment lines across 6 files justify a health status "
        "code by a container restart that mechanism cannot produce, the only repository "
        "monitor runs every 21600 s, and a real app whose poll loop died kept serving "
        "frozen data on 200s while /healthz went 503 with nobody polling it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
