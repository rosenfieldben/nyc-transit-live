# Reproductions for the 2026-09-05 audit

Every number in section 2 of [../audit-2026-09-05.md](../audit-2026-09-05.md) comes
from one of the scripts here. They exist so the dispositions are re-derivable rather
than asserted: a claim in a review document that nobody can re-run is a claim with a
shelf life.

```
bash docs/reviews/audit-2026-09-05/run_all.sh      # from the repository root
```

Fourteen scripts, about sixteen seconds. Each one exits 0 while the finding still
behaves the way the table records it, and non-zero the moment the code stops
matching. So these are regression checks on the audit record, not one-off prints: a
red run means the table is stale, which is the only failure mode that matters once
the fixes start landing. Run one on its own with `.venv/bin/python <script>` or
`node <script>`.

None of them is wired into CI. They pin the state of a set of open defects, so they
are expected to start failing as those defects are fixed, and a check that is
supposed to go red is a bad gate.

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
  drives production code over committed fixtures or an explicitly injected fault.
- **No NJ Transit mint can be spent here.** The account is capped at ten mints a day
  and six are already committed (see the NJ Transit section of the README), so the
  NJT reproductions use fake transports and fake mint callbacks, point every NJT
  env seam at a dead local port before importing the backend, and refuse to run if a
  real credential is present. F05 measures a mint storm without making one.

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

The Railway healthcheck citation behind F13 is recorded in that script's header
comment rather than fetched at runtime, so the script stays offline. Re-read the
cited pages by hand when re-checking that finding.
