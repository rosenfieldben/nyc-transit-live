# Subway static: the routes-per-station tables become required members

One backend commit, the obligation MR2's ledger recorded when its review split finding
F1. Nothing in `frontend/` changes and nothing touches `njt_auth.py` or any credentialed
path, so **no NJ Transit mint was spent**.

## The finding

`stop_times.txt` was not a required member of the subway static archive.
`load_subway_station_routes` caught every exception and returned `{}`, so
`/api/subway-stations` served `routes: []` for all 496 stations while
`subway_static_status` stayed `ready` and `/healthz` stayed green.

**Three consumers read that index, and all three are rider-visible:**

| | What goes wrong with an empty index |
| --- | --- |
| the transfer ring | MR2 draws a paper ring where two or more trunks call and a filled dot where one does. Every one of the 496 stations becomes a dot, so the map stops saying which stations are interchanges. |
| the hub label class | The same predicate gives a name label its `hub` class, and the zoom band reveals only hub labels at zooms 12 and 13. No label carries it, so **no station name renders at the opening zoom or at the City preset** while the Names control reads pressed. |
| the station alerts join (F11) | A route-scoped alert reaches a station through its routes, so a station with a live alert on every route serving it shows none of them. |

Reproduced by execution during MR2's review: an archive with no `stop_times.txt`, the
loader returning `{}`, the endpoint serving `routes: []`, and the operator surface
saying nothing at all.

## The decision it reverses

The comment above `_REQUIRED_MEMBERS` argued that `trips.txt` and `stop_times.txt` did
not belong there, because "their only subway consumer is `load_subway_station_routes`
(the H5 routes-per-station popup enrichment), which already swallows any problem and
returns an empty index, and the map is fully functional without it".

**That was true when it was written and stopped being true twice**: MR2 gave the index
the transfer ring and the hub label class, and F11 gave it the station alerts join. The
rule that distinguishes these members is the visible-loss rule the same comment states
two paragraphs earlier, and by that rule both files are now required. The comment is
rewritten to say so, with the three consumers named and the finding cited, rather than
left as a sentence that outlived its premise.

The same stale claim appeared in two more places and both are corrected: the
`EXPECTED_REQUIRED` note in `test_static_shared.py`, and a docstring in
`test_static_data.py` that had been asserting `trips.txt` was required for a while when
it was not.

## The change

**1. `_REQUIRED_MEMBERS` gains both files**, so a publication missing either fails the
load through `require_members`, exactly as PATH and the ferry already do. The group
reports `failed`, `HEALTH_SUBWAY_STATIC_FAILED` fires, `/healthz` degrades, and the
monitor's existing `subway-static-failed` check sees it. **No new code and no new
check**, and that is asserted in the monitor's hermetic tests rather than trusted.

**2. `load_subway_station_routes` stops swallowing.** A parse problem in a file that is
now required is a failed load, not a warning and an empty index; the "swallows any
problem" clause goes with the comment that justified it. **What stays tolerant is a
station with no trips**, which is data rather than failure: the index simply has no entry
for it and every consumer reads that as no routes. The difference is between an archive
this loader cannot read and an archive that says nothing calls at a stop, and the second
must never become an outage.

**3. The last-known-good rule, confirmed for this index.** `_warm_subway_static` assigns
`app.state` only after the whole attempt succeeds, so a failure cannot half-write and the
previous index survives. That was already structurally true; it now has a test, because a
refactor that assigned each loader's result as it arrived would pass every status test in
the suite and wipe the index on the first bad publication.

## Tests

Hermetic throughout. Nothing here needs the network or a credential.

| Claim | Test |
| --- | --- |
| a publication missing `stop_times.txt` fails the load, and the error names the file | `test_static_data.py::test_validate_rejects_an_archive_without_the_station_routes_tables[stop_times.txt]` |
| the same for `trips.txt` | the `[trips.txt]` case of the same test |
| F1 end to end at the loader: a reduced publication is rejected as a cache AND as a redownload, so the load raises rather than promoting it | `test_a_reduced_publication_fails_the_load_rather_than_serving_an_empty_index`, both members |
| a present-but-corrupt `stop_times.txt` fails the load, which a required-member set alone cannot catch | `test_load_subway_station_routes_raises_on_corrupt_stop_times` |
| a station with no trips is still tolerated, and so is an archive with no trip rows at all | `test_a_station_with_no_trips_is_still_tolerated` |
| the committed fixture's routes-per-station index is unchanged, byte for byte | `test_load_subway_station_routes_end_to_end`, untouched: it still asserts `{"101": ["1", "2"], "103": ["1"]}` |
| a raise from the loader drives the group to `failed` | `test_api.py::test_subway_static_warmup_fails_when_station_routes_raise` |
| a failed reload keeps the previous index, and its siblings | `test_api.py::test_subway_static_failed_reload_keeps_the_previous_station_routes` |
| the monitor's check fires on the `failed` status without modification | `test_contract_monitor.py::test_a_failed_subway_static_group_fails_the_monitor_without_a_new_check` |

**Two tests reversed rather than deleted.** `..._missing_tables_returns_empty` and
`..._bad_zip_returns_empty` asserted the swallowing. They are the same two inputs with
the answer reversed, renamed to `..._raises_on_...`, so the reversal is legible in the
diff instead of looking like coverage that went missing.

**The declared required-member set is a gate, and it fired.**
`test_static_shared.py::test_required_member_set_is_exactly_what_is_declared` writes the
set out by hand rather than reading it from the module, precisely so that widening it is
a deliberate edit. Adding two members failed that test until the declaration was updated,
which is the gate doing its job.

## Mutations, each run and recorded

Each in a real `git worktree` detached at the commit under test, applied alone, the named
tier run against it, with the main tree verified clean before and after.

| # | Guard reverted | Result | Killed by |
| --- | --- | --- | --- |
| M1 | `stop_times.txt` removed from `_REQUIRED_MEMBERS` | **killed** | the two missing-member cases and both reduced-publication cases, plus the declared-set gate |
| M2 | the `except Exception` clause put back in `load_subway_station_routes` | **killed** | `test_load_subway_station_routes_raises_on_corrupt_stop_times`, and the two raises-on tests |
| M3 | the retention removed (`app.state` assigned before the attempt completes) | **killed** | `test_subway_static_failed_reload_keeps_the_previous_station_routes` |

**M1 caught a vacuous test of mine before it was committed.** The first draft of the
reduced-publication test stubbed `_download_zip` to write the reduced archive straight to
the cache path, which bypassed `staged_fetch` entirely: the archive was promoted
unvalidated, `stops.txt` parsed fine, and the load returned happily. That draft passed
with `_REQUIRED_MEMBERS` reverted, because nothing in it ever ran the validator on a
download. It is injected at the transfer now, so the real chain runs, and the comment at
that seam records why.

## Gates

| Gate | |
| --- | --- |
| `pytest` (backend) | **1729** passed |
| `ruff check` | clean |
| `ruff format --check` | clean, 79 files |
| `mypy` | clean, 30 source files |
| contract-tier lint and format | clean |
| contract API tier | **38** passed |
| `run_all.sh` | **12 of 15**, unchanged from `origin/main`, and twelve is the accepted number for this branch. See below. |

**`run_all.sh` is at twelve on `origin/main`, not fifteen, and this branch does not
change that. Twelve is the accepted number here.** Verified by running it in a worktree
detached at `ac14eec`, the commit this branch forks from: **F11, F12 and F14 were already
red there, before this branch existed**, each with the same cause. They are Node harnesses
that load the production frontend into a vm with their own stubbed Leaflet, and MR2's
changes to `frontend/systems/shared.js` outgrew those stubs:

```
F11  TypeError: Cannot read properties of undefined (reading 'zoom')
F12  TypeError: L.control.zoom is not a function
F14  frontend/systems/shared.js: Cannot read properties of undefined (reading 'zoom')
```

**F08 was this branch's, and this branch fixes it.** Its four archives are built without
`stop_times.txt`, so the validator rejected them for the wrong reason and the run derailed
at phase 2 with `REJECTED missing required member(s): stop_times.txt`. Every archive gains
the member header-only, so the only difference between them stays the invalid-UTF-8
`shapes.txt` the record is about, and F08 verifies again with its disposition unchanged.
So of the four reds seen while building this branch, three predate it and one was its own
and is repaired.

**THE THREE GO TO THEIR OWN BRANCH**, `claude/audit-harness-stubs`, off `origin/main`
after this merges: repair the three harnesses' Leaflet stubs so F11, F12 and F14 verify
again with their dispositions unchanged, add a CI job that runs `run_all.sh` and fails on
any red row, and record in the ledger's standing rules that `run_all.sh` is a gate of
every MR stage. Tests and CI configuration only, no application code. Fixing them here
would mean frontend work on a branch whose whole claim is that it is backend only, and
would mean three records changing in a commit about required members.

**F11 is the record for the station alerts join**, which is one of the three consumers
this branch exists to protect, so that branch is worth doing next rather than eventually.

---
🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_017oZP4Mtd6BLoSvAeVaPrJz
