# Reproductions for the 2026-09-05 audit

Every number in section 2 of [../audit-2026-09-05.md](../audit-2026-09-05.md) comes
from one of the scripts here. They exist so the dispositions are re-derivable rather
than asserted: a claim in a review document that nobody can re-run is a claim with a
shelf life.

```
bash docs/reviews/audit-2026-09-05/run_all.sh      # from the repository root
```

Fifteen scripts, about sixteen seconds. Each one exits 0 while the finding still
behaves the way the table records it, and non-zero the moment the code stops
matching. So these are regression checks on the audit record, not one-off prints: a
red run means the table is stale, which is the only failure mode that matters once
the fixes start landing. A script therefore measures what the code DOES and never
where it lives, so a behavior-preserving refactor cannot turn a row red. Run one on
its own with `.venv/bin/python <script>` or `node <script>`.

None of them is wired into CI. They pin the state of a set of open defects, so they
are expected to start failing as those defects are fixed, and a check that is
supposed to go red is a bad gate.

**Fourteen of the fifteen are reproductions; the fifteenth is a PROBE and the
difference is worth keeping straight.** `probe_bus_observation_clock.py` re-derives
the buses row of the age policy in section 3.3 of
[../../design/freshness-contract.md](../../design/freshness-contract.md), which that
design could not write because no bus feed had ever been captured here. It has no
before and no after, it pins no defect, and nothing is expected to make it go red
when a fix lands. It goes red only if its committed capture stops supporting the row
that cites it, which is the same contract every other script here has and the reason
it lives beside them rather than in a directory of its own.

**If you add a script here, scope every tree scan away from this directory.** These
files are tracked, they quote the symbols and the comment text they are scanning
for, and an unscoped `git grep` or `git ls-files` walk therefore matches itself. The
failure is silent in the worst way: it passes while the script is untracked and
starts failing the moment it is committed, which is exactly how F13's supervisor
count went from 0 to 7. `f13_healthcheck_recovery_gap.py` states the rule once in
`SCAN_SKIP_PREFIXES` and `SCAN_PATHSPEC`, uses it for both of its scans, and asserts
that the scan cannot see itself. Every other script scans only `backend/` or
`frontend/` subtrees, or excludes `docs/reviews` by name.

## What they need, and what they cannot do

- A Python environment with `backend/requirements-dev.txt` installed. `run_all.sh`
  defaults to `.venv/bin/python`; override with `PY=/path/to/python`.
- Node 20 or newer for the two `.mjs` scripts (F11 and F12).
- No network, no credential, no live upstream, no running server. Every reproduction
  drives production code over committed fixtures or an explicitly injected fault, and
  the one probe measures a committed capture with `gtfs_realtime_pb2` alone. The
  capture behind the probe was fetched once, by hand, and its request and response
  instants are recorded in that script's header so nothing has to fetch it again.
- **No NJ Transit mint can be spent here.** The account is capped at ten mints a day
  and six are already committed (see the NJ Transit section of the README), so the
  NJT reproductions use fake transports and fake mint callbacks, point every NJT
  env seam at a dead local port before importing the backend, and refuse to run if a
  real credential survives. F05 measures a mint storm without making one. The rule
  and its enforcement are below; this paragraph described them for a while before
  they were true.

## THE CONTAINMENT RULE, and why a scrub is not one

**Every script that imports a backend module must call `_hermetic.contain()` before
that import and `_hermetic.verify()` after it.** Two lines, and they are not
ceremony: the four scripts that hand-rolled the same intention were all reaching the
real NJ Transit API on a developer checkout, and none of them could tell.

Here is the thing to understand, because it defeats the obvious fix and the obvious
workaround in turn:

```python
for var in ("NJT_USERNAME", "NJT_PASSWORD"):
    os.environ.pop(var, None)     # looks like a scrub
import njt_auth                   # the credentials come back HERE
```

`load_dotenv` fills environment keys that are **absent** and leaves keys that are
**set**. Popping makes a key absent, so a pop before a backend import is not a scrub,
it is an invitation, and this backend loads the `.env` in **two** places
(`backend/env_seams.py:46` and `backend/feeds/shared.py:35`). A script that dropped
the credentials after the first import had them back after the second, which happens
hundreds of lines later if the script imports the app inside a function.

Blanking in the parent shell does not save you either: `NJT_USERNAME= script.py` sets
an empty value that `load_dotenv` will not overwrite, and then the script's own `pop`
deletes it and the next import refills it from the `.env`.

So the rule is two walls, and the first one is the load-bearing one:

1. **The address.** Every NJ Transit URL seam is pointed at `127.0.0.1:9` before any
   backend import. `env_seams` reads these once at import and `load_dotenv` never
   overrides a key that is already set, so this cannot be undone. A process contained
   this way cannot reach NJ Transit while holding perfect credentials, which is what
   makes it the wall that also covers F05 and F07, both of which must look
   *configured* because that is the finding.
2. **The credentials.** Set **empty**, never deleted, so no `load_dotenv` anywhere,
   now or later, can refill them. Every consumer reads them through a helper that
   treats empty as missing.

`verify()` then asserts, on the far side of the imports, that `is_configured()` is
false (or that the credentials are the script's own fabricated pair) and that no
seam and no resolved `njt_auth` URL names `raildata.njtransit.com`. It raises
`SystemExit`, so a script that cannot prove it is contained does not run at all.
Remove `contain()` from any script and it exits 1 with a breach message rather than
quietly reaching the vendor.

**Prove it, do not assume it.** `_hermetic_selftest.py` measures the containment
against a fake in four subprocesses: it reproduces the leak, refutes the shell
workaround, contains the unconfigured path, and drives a real `njt_auth.mint()` on
the configured path to show it lands on the discard port. Run it after touching
`_hermetic.py`:

```
./backend/.venv/bin/python docs/reviews/audit-2026-09-05/_hermetic_selftest.py
```

To watch the whole suite at the socket level, put `_tracer/` on `PYTHONPATH`. It
installs an audit hook in every interpreter and logs one line per process plus every
resolution and connection:

```
NJT_SOCKET_TRACE=/tmp/sockets.log \
  PYTHONPATH=$PWD/docs/reviews/audit-2026-09-05/_tracer \
  PY=backend/.venv/bin/python bash docs/reviews/audit-2026-09-05/run_all.sh
```

Measured on 2026-09-12, on a checkout whose `.env` holds real RailData credentials
and a real Bus Time key, with nothing blanked in the shell: fifteen passed, thirteen
interpreters instrumented, **zero** hostname resolutions and **zero** socket
connections of any kind. The thirteen are the Python scripts; the two `.mjs` ones run
under node, which this hook does not instrument. The count moved from twelve because
the bus probe is the thirteenth Python script, and it is the one script here whose
FIXTURE came off the network, which is exactly why it was worth re-running this.

## The scripts

| Script | Finding | What it drives |
| --- | --- | --- |
| `f01_lirr_gps_observation_age.py` | F01 | The real `_decode_railroad_vehicles`, `_refresh_railroads` and `GET /api/railroads` over the committed LIRR and MNR captures. |
| `f02_canceled_railroad_gps.py` | F02 | The real railroad decoders and routes over the committed capture, plus a synthetic combined-entity feed in the Metro-North layout. |
| `f03_arrivals_content_freshness.py` | F03 | The real `_refresh_subways` and the ASGI app over the committed subway capture, re-stamped behind the poll clock. |
| `f04_airtrain_historical_reference.py` | F04 | The real `load_airtrain` and `/api/airtrain`, then the shipped `stations.js` and `helpers.js` renderers in a `node:vm` against six injected New York instants. |
| `f05_njt_failed_mint_storm.py` | F05 | The real `TokenCache`, `njt_auth.mint`, `_refresh_njt`, `_refresh_alerts` and `_warm_njt_static` against an in-process fake RailData. |
| `f06_partial_startup_no_recovery.py` | F06 | The real `_warm_railroad_static`, `bus_static.ensure_index` and `main.lifespan` with one injected download failure. |
| `f07_static_never_refreshes.py` | F07 | The real `main.lifespan` across forty poll cycles, and a backdated, calendar-expired NJT archive. |
| `f08_bad_geometry_replaces_good.py` | F08 | The real staged download, validate and promote path over archives built on disk, with only the socket transfer faked. |
| `f09_nonfinite_coordinates.py` | F09 | The real static parsers, the ASGI app, the real geometry builder, the real NJT warmup, and the vendored Leaflet 1.9.4 in a `node:vm`. |
| `f10_deadline_health_disagreement.py` | F10 | The real nested refresh composition and the real status route, served through the app. |
| `f11_station_panel_hides_alerts.mjs` | F11 | `helpers.js`, `systems/shared.js`, `systems/subway.js` and `stations.js` in a `node:vm` over a DOM parsed from `index.html`. |
| `f12_stale_error_body_overwrites.mjs` | F12 | The production `fetchPanelArrivals` in a `node:vm`, with response headers and body delivered separately. |
| `f13_healthcheck_recovery_gap.py` | F13 | A mechanical comment inventory over tracked files, and the real app lifespan with the poll task killed underneath it. |
| `f14_accessibility_gaps.py` | F14 | The real frontend in a `node:vm`, driven through twenty-eight rider actions via the page's own handlers. |
| `probe_bus_observation_clock.py` | 6.0 | The committed OneBusAway capture, measured with `gtfs_realtime_pb2` alone, against the Metro-North capture as the copied-header control. |

The Railway healthcheck citation behind F13 is recorded in that script's header
comment rather than fetched at runtime, so the script stays offline. Re-read the
cited pages by hand when re-checking that finding.
