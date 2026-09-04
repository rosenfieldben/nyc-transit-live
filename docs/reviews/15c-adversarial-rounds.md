# 15c adversarial review: the adjudication record

Phase 15c (NJ Transit on the map: route lines from `shapes.txt`, the station and
train layer, and the tiers around both) went through **three rounds**, one per part
of the phase, over a combined diff of about 5,900 lines. This is every finding
raised, with its disposition. Nothing is dropped; a finding that was wrong is
recorded as wrong, with the measurement that refutes it, and so is a finding whose
*expectation* was wrong while the finding itself held.

The same five categories the A4, 15a and 15b records use:

- **Fixed**: real, a change shipped, and a mutation proves the change is load-bearing.
- **Deferred, with reason**: real, not fixed in this phase, and why.
- **Refuted, with evidence**: reported and false; the measurement that refutes it.
- **Refuted then overturned**: refuted at the time and later found real after all.
- **Confirmed but downgraded**: the reported behaviour reproduces, but the severity
  does not survive contact with the rest of the suite.

---

## Round 1 and Round 1b: the backend and the fixture arm

Recorded in full in the commit bodies rather than restated here, because those
commits are the addressable record and a second copy would drift:

- `ba2ee26` **15c backend: NJ Transit route lines from shapes.txt, and the fixture
  arm for them.** The bounded `shapes.txt` parse, `/api/njt-routes`, the seventh
  fixture member, `--shapes-only`, and the six guarded goldens.
- `e62bb78` **15c Part 1b: a branch that reaches its own terminus is not a
  duplicate.** Two defects the real fixture exposed (the dedup deleting Hoboken from
  the Montclair-Boonton line, and a 6.9 MB fixture), the distance dedup with its
  endpoint arm, Douglas-Peucker at `NJT_SIMPLIFY_EPS`, the `_CoverIndex`, and
  fourteen mutations.
- `468fdad` **the fixture itself**, pulled and eyeballed by a human against the live
  credentialed API.

Two things from those rounds are repeated here because they are method rather than
code, and because Round 2 met both again:

**A finding can be right about the defect and wrong about which fix is the fix.**
Round 1b's ordering finding named `keep_distinct_variants` ordering variants by
POINT COUNT, and the reason it gave (a comment inherited from the sibling rule) was
correct; what made it a defect was simplification, which turns point count into a
measure of curvature rather than of length.

**A mutation's EXPECTED victim is a prediction, and predictions get measured.**
Round 1b's M2 was briefed as "COVER_DIST at infinity kills the independently
digitized test". It does not: at infinity everything reads as covered, the dedup
keeps only the first variant, and that test (which asserts exactly one survivor)
still passes. The branch tests are what die. `COVER_DIST = 0` is what kills the
digitized test. Both directions were run and both are in `e62bb78`. Round 2 hit the
same shape twice, at M2 and at M4 below.

---

## What the geometry actually measures

Round 1b argued about the geometry from a synthetic model of the feed. The
committed fixture landed in `468fdad`, so the numbers are now measured off the real
file rather than estimated, by parsing `backend/tests/fixtures/njt_gtfs/` through
`njt_static._parse_open` and `build_njt_route_shapes` and serializing the result
through `models.NjtRoute` exactly as the endpoint does:

| Quantity | Measured |
| --- | --- |
| Routes served by `/api/njt-routes` | **11** of the 12 in `routes.txt` |
| Polylines drawn | 15 |
| Points across all polylines | **1,609** |
| Shapes parsed (bounded to referenced ids) | 29, carrying 2,815 points |
| Routes keeping two variants | **2, 7, 8, 10** (every other route keeps one) |
| JSON served, as FastAPI serializes it | **34,449 bytes = 33.6 KB** |
| Hoboken's distance from every route serving it | **0.000003**, i.e. on the line |
| Worst (station, route) pair in the fixture | Radburn (133) on route 5, **0.00059** |
| Tolerance that pair is measured against | 0.0025, the frontend's own `RAILROAD_ROUTE_ACCEPT_DIST` |

**The twelfth route is 17, the Meadowlands Rail Line**, and its absence is the
feed's rather than a defect: it runs only for events, the committed publication
carries no Meadowlands trips, and a route with no trips references no shape. It has
no entry on `/api/njt-routes` and it never will in an ordinary publication.

**One number in the phase brief did not survive measurement.** The brief recorded
the served payload as 37.8 KB. It is **33.6 KB** (34,449 bytes): FastAPI's
`JSONResponse` serializes with `separators=(",", ":")`, and the nearest larger
figure reproducible from this data is 36.9 KB (37,765 bytes), which is what
`json.dumps` produces with its default `", "` / `": "` separators. Both were
measured; neither is 37.8 KB. The ledger records the served number, because the
served number is the one a page load pays.

**And one premise did not.** Amendment (a) of the Part 2 brief describes route 17 as
arriving "on `/api/njt-stops` with routes `["17"]`". It does not: `derive_njt_stop_routes`
derives a station's routes from the TRIPS calling there, so a route with no trips
appears in no station's list either. Measured on the committed fixture: **no stop
carries route 17**, and the fixture has no Meadowlands station at all (the nearest
are 144 Secaucus Junction and 145 Secaucus Station). The fallback behaviour the
amendment asks for is still exactly right and is implemented and tested; what
changed is how the tests reach the case. They construct it (a route id a train or a
station names, absent from the route tables) rather than reading it out of the
fixture, and the hermetic e2e fixture models the shape a client can actually meet:
a route with trips whose geometry the publication does not draw.

---

## Round 2: the frontend, the tiers, the ledger

One adversarial round over the ~2,300-line Part 2 diff, aimed by the author at six
named surfaces, each finding then handed to a second agent instructed to refute it.

**31 findings raised across six lenses, 31 acted on, 0 refuted on the merits.** Every one was reproduced by the reporter before it was written down,
and the two that mattered most were reproduced again here before anything was
changed.

**A note on the verification pass, because it changes how its verdicts read.** The
fixes landed as the findings arrived rather than after the whole round, so the
verifiers ran against a tree that already carried them: seven of the first eight
verdicts came back REFUTED with the evidence "the code the finding describes is not
in the tree; it reads exactly as the finding's own suggested fix". That is a true
statement about the tree and a false one about the finding, and it is recorded that
way below rather than counted as a refutation. The one verdict that refuted
something on its merits is F16, and it is marked.

### The one that could reach a rider

| # | Finding | Disposition |
| --- | --- | --- |
| F1 | **Every in-transit NJ Transit train was drawn at f SQUARED of its segment.** `trainLatLng` and `computeRouteSlice` were written for the subway and railroad decoders, which place a train AT ITS NEXT STATION and leave the interpolation to the client: `latitude`/`longitude` is the FAR END of the segment. NJ Transit's decoder interpolates ON THE SERVER (`feeds/njt.py` case 3 calls `_interpolate`, and its docstring says why), so its `latitude`/`longitude` is the train's CURRENT position, already f of the way along. Handing that to `trainLatLng` walks prev to current-position by f a second time. Reproduced against the real decoder: a train the backend placed halfway between Newark Penn and New York Penn was drawn a quarter of the way along, **3.6 km short on a 14.5 km leg, at the instant of the poll** rather than merely between polls. Across the segment the drawn fraction ran 0.010 / 0.062 / 0.250 / 0.563 / 0.810 for a true 0.10 / 0.25 / 0.50 / 0.75 / 0.90. **The hermetic fixture hid it**: it hand-wrote NJ_3800's `latitude`/`longitude` as station 109's coordinates, which is a payload `feeds/njt.py` cannot emit for an in-transit train, so the fixture agreed with the bug. | **Fixed**, client-side. `njtGlideTrain` puts the NEXT STOP back where the helpers expect it, using coordinates `/api/njt-stops` already serves, and returns **null** rather than guessing for a train drawn at its own stop or one heading for a stop the table does not carry yet (both then draw at the served position, which is right rather than merely safe). `njtPointFor` is the one place the poll path, the creation path and `animateTrains` read, so they cannot drift about which object they interpolate, which is how this happened. The backend is untouched: its contract is 15b's, it is in production, and its docstring already says what it does. The fixture now carries the position the decoder really emits (0.4 along the leg, 120s of a 300s segment), and mutation **M6** reverting the reconciliation kills two node tests and spec 33. |

### The seven mediums

| # | Finding | Disposition |
| --- | --- | --- |
| F2 | **Every NJT train with a `stop_id` rendered "Also here: <station>", in-transit ones included.** `models.NjtTrain` documents `stop_id` as "where it is, OR the stop it is heading for", so the gate admitted a moving train: measured, NJ_3800 was drawn **0.079 degrees (about 8.7 km)** from New York Penn while its popup read "Also here: New York Penn Station" one line under "Next stop: New York Penn Station". That is exactly what the principle comment at `crossLinkHtml` forbids. | **Fixed**. `njtAtItsStation` (prev_lat null AND a stop_id) is the predicate, read off the anchors rather than off `status` for the reason `isPlacedRailroad` reads `stop_id` rather than the times: the coordinates and the anchors come from the same decoder branch. Spec 33 now asserts the in-transit train's popup carries no "Also here" while the dwelling one does; mutation **M7** killed. |
| F3 | **The `njt` source row omitted `clearOnEmpty`, under a comment arguing the opposite of the backend's own contract.** `pollers._refresh_njt` says it outright: "an EMPTY successful poll REPLACES the trains ... retaining the evening's trains through the night would be a map full of ghosts. Only a FAILED poll keeps the last-known trains." Reproduced: four trains stayed on the map **at full opacity, still gliding**, for ninety seconds after the backend had replaced them with zero, and then the status line read "NJ Transit: feed empty" in the error class for the rest of the lull, while the ferry in an identical state cleared instantly and recorded nothing. | **Fixed**. `clearOnEmpty: true`, the ferry's arm, with the comment rewritten to quote the backend rather than contradict it. Spec 35 drives a successful empty poll and pins the immediate clear; mutation **M10** killed. |
| F4 | **A deployment with no NJT credentials showed a permanently red status line carrying an operator-facing configuration sentence.** `/api/njt-trains` answers 503 with "NJ Transit is not configured (NJT_USERNAME/NJT_PASSWORD are unset)" forever in that state, and `refreshSource` surfaced it verbatim. 15a's F1 had already decided that a deployment which does not run NJT must not be painted red; this put it back, on the surface a RIDER reads. | **Fixed**. A per-source `quiet` predicate: the njt row declares `isNotConfigured`, and a failure it matches counts zero and draws nothing but does not turn the line red. Matched on the served WORDS rather than on the status, because 503 is also what a warming cache answers and a warming NJT is worth surfacing; spec 35 asserts both halves and mutation **M10** killed. |
| F5 | **The NJT board's row ORDER and its printed COUNTDOWNS used different keys.** `feeds/njt._trim_njt_arrivals` sorts on `max(arrival, departure)` so a dwelling train does not sort by a past arrival; the countdown counts to the ARRIVAL while it is still ahead. Two trains at one station with different dwell lengths make the keys disagree: reproduced with rows already in the endpoint's own order, a Dover row printed "3 min" above a Trenton row at "2 min", on both the popup and the panel. | **Fixed**. `njtOrderedArrivals` sorts by the displayed countdown, stable on a tie, unknown times last; both the popup and the panel take it, so one station's board cannot read two ways. Sorted client-side because the key depends on `now`, which the backend does not have at serve time. Mutation **M11** killed. |
| F6 | **`shapeStationArrivals`' njt dwell branch was exercised by nothing.** Deleting `|| kind === "njt"` left every tier green, because the one njt case used arrival 190 / departure 200 against now = 100, where the dwell rule and the plain `arrival - now` fallback agree. Rider-visible: for a train standing at the platform the panel would have said "now" while the popup said "departs 4 min". | **Fixed as coverage**. A node test with a dwelling row (arrival past, departure ahead) pinning mode, seconds, `at`, the spoken sentence and the popup's own rendering of the same train. |
| F7 | **`njtDelayText`'s "rounds to the nearest minute" was measured by nothing**: every asserted value read the same under `Math.floor`, so a truncating rewrite would silently drop every 30-to-59 second delay from both the popup and the accessible name. | **Fixed as coverage**. The two values that separate the modes (40 and -40 seconds) plus 29, which keeps the "under half a minute" half honest. |
| F8 | **`vanish.spec.js` was left on the marker-count opener this change diagnosed and replaced elsewhere.** `state.js`'s new witness comment names the defect exactly, `escape.spec.js` and `markers.spec.js` were converted, and `vanish.spec.js` was not, with four `[...railroads.keys()][0]` reads on the lines after it. Observed for real: a full run failed at A8c with `Cannot read properties of undefined (reading 'marker')`. | **Fixed**. Converted to `expectState(page, "every vehicle system loaded")`. |

### The unexercised guards, and the comments that were not true

Grouped because they are one finding wearing eleven costumes: this project's rule is
that every guard is shown to fire and every comment states something true, and a
review round is the only thing that measures either.

| # | Finding | Disposition |
| --- | --- | --- |
| F9 | The badge's `route_id \|\| "?"` was the only thing between a null route and the literal word "null" in a rider-visible chip, and nothing exercised it. `models.NjtArrival` declares the field nullable and `feeds/njt.py` really produces one (an ADDED trip whose TripDescriptor omits `route_id` and joins no static trip). Mutated away, the board printed `>null<`. | **Fixed as coverage**. A node test, plus a third row in the hermetic fixture, plus the panel sentence it produces in A1s. |
| F10 | `applyNjt`'s leave-the-feed sweep was exercised by no test: no spec at any tier ever shrank the NJT payload. | **Fixed as coverage**. Spec 34 drops one train on a later poll and asserts by IDENTITY, not only by count. Mutation **M8** killed. |
| F11 | The create path's `opacity: markerOpacity(age)` carried the C2b claim ("retained data must never render live, not even for one frame") with nothing behind it, and no NJT spec held a popup open across a poll, so the `isPopupOpen` refresh never fired either. | **Fixed as coverage**. Spec 36 gives NJT the C2b treatment (drives `applyNjt` directly in an already-stale system and reads the marker before any sweep can run) and holds a popup open across a delay change. |
| F12 | `loadNjtRoutes`' empty-payload guard was unpinned: `if (false) return false` left every tier green, because the RENDERING is identical either way. What is not identical is whether the loader keeps asking. | **Fixed as coverage**. Spec 34 counts the fetches across the retry backoff. Mutation **M9** killed. |
| F13 | `record.routeId` was write-only dead state, and the comment keeping it claimed it drives the re-projection (which `_segId` does). | **Fixed**. Removed, and the record-shape comment corrected to the fields that exist. |
| F14 | The "NO ALERTS PREPEND" comment gave a reason that is not true of this codebase: it claimed a bare-id join would attach Metro-North's alerts to New Jersey platforms, and there is no bare-id join available to make (`indexAlerts` keys on `${system}|${id}`, `stationAlertsBlock` takes the system). | **Fixed**. Rewritten to the reason that holds: `stationAlertsBlock` unions route ids out of `body.directions`, which a FLAT NJT arrivals body does not have, so wiring the join means changing a helper four other systems depend on. Deferred with its reason rather than skipped without one. |
| F15 | `animateTrains`' NJT comment claimed every NJT train glides. Only in-transit ones do: three of the four trains in the repo's own fixture never move, and the PATH comment three lines above words the same situation correctly. | **Fixed**. Reworded to match. |
| F16 | `njtRouteColor` / `njtRouteName` carried two guard clauses nothing exercises, one of which cannot change the result for a Map; `njtRowLabel` was the one helper in the family with no null guard; `njtDelayText`'s `Number.isNaN` clause was dead code. | **Fixed, and the verification pass corrected the reporter here.** The two dead clauses are gone (the code now reads exactly what the reporter proposed), `njtRowLabel` has its siblings' guard, and the `NaN` clause is replaced by a comment saying the zero-delay line already returns "". The remaining family guards are kept and now asserted rather than deleted. |
| F17 | `style.css` claimed the e2e suite reads both NJT marker classes; only `.njt-marker` was read. | **Fixed by making it true**: spec 33 counts the station squares through `.njt-station-marker`. |
| F18 | `njtSystemStaleAt`'s comment claimed the `("njt", "njt")` key pair is "written once", while `shared.js` wrote it again inline. | **Fixed**. `animateTrains` now goes through `njtPointFor`, and the comment says where the pair is read from. |
| F19 | `njtArrivalsHtml`'s comment described a two-slot row the function has never rendered (`njtRowLabel` returns one string, and the badge carries the route id rather than the name). | **Fixed**. Reworded to what the function does. |

### The tests that did not measure what they said

| # | Finding | Disposition |
| --- | --- | --- |
| F20 | **C2g and C6e4 closed on a status assertion the healthy page already satisfied.** `map.js` builds the counts from every source's label unconditionally, so "N NJ Transit" is in the status line at all times: measured healthy, "... 3 ferries - 4 NJ Transit - updated 12:00:00 PM" with no error class. Excluding njt from the problems clause entirely left C2g green. | **Fixed**. Both now assert the problems clause (`"NJ Transit: as of"` / `/NJ Transit: /`) and the error class, which only a degraded NJT produces. C2f already had it right one spec earlier. |
| F21 | **A2f's "and its delay every poll" half survived the exact trap it names**: the route table landed on the SAME poll the delay flipped, and that poll rebuilds the icon, so a `setMarkerName` gated on the re-skin still picked both up. Reproduced by moving `setMarkerName` inside the re-skin block: green. | **Fixed**. The delay now flips on a later poll where nothing else about the train moves. Also renamed to **A2j**: `A2f` was already taken by `announce.spec.js`, which the duplicate would have made uncitable. |
| F22 | **The contract route-lines docstring claimed it catches a crossed route/shape join.** It cannot: the simulator publishes s1 and s13 along one corridor and both routes call at the same three stops, so a build that swaps them passes. Measured: that mutation passes the contract scenario and fails four hermetic tests, worst pair 1.11908 against the 0.0025 tolerance. | **Fixed as documentation, plus one real assertion.** The docstring now credits the hermetic golden with the crossed-join and wrong-variant claims and states what this tier does own (the whole chain running, and its output still lining up with the stations). The scenario also now asserts every published shape row survived the parse, which a dropped row would trip before a station left its line. |
| F23 | **The contract scenario said the called-at stations come from the served payload; they were restated inline.** | **Fixed by making it true**: the per-route call lists are now derived from the `routes` index `/api/njt-stops` merges onto every marker. |
| F24 | **A1s's "ONE ROW, not two" proved nothing about the system-qualified registry key**: no id in the hermetic fixture collided, and the query excluded the PATH row on tokens anyway. Removing the qualifier left six specs green. | **Fixed by making the collision real.** The fixture's Hoboken now carries id **"12"**, which LIRR Jamaica also uses, because that is what these two bare-integer id spaces really do (the contract tier has measured 21 of 24 ferry dock ids colliding with Metro-North's). The railroad stations register first, so a bare-id key sends a rider standing in Hoboken to Jamaica, Queens; spec 33 asserts the cross-link says Hoboken and does not say Jamaica. |
| F25 | **`njtArrivalsHtml`'s R1 stale-age line was exercised at no tier, and no browser spec ever opened an NJT station popup**, so the whole departures board was never rendered in a browser. | **Fixed**. A node assertion on a stale board, and spec 33 opens a station popup and reads the rows a rider clicks for. |

### The accessibility gates that were not extended to the new system

| # | Finding | Disposition |
| --- | --- | --- |
| F26 | **NJT's two colour-accessibility call sites were wired to nothing.** `layout.spec.js` A4g is the repo's named WIRING check ("the node tests prove `readableTextOn` and `readableInk` are correct over every palette; they cannot prove the call sites actually call them"), and it opened a subway popup and searched "times", so no NJT badge and no NJT popup heading was ever in its sample. Worse, `a11y.spec.js` names A4g as the DECIDER for the `span.arr-badge` undecidable-contrast shape, so a whole class of arrival badge was being reported as decided while nothing decided it. Reproduced: both NJT call sites replaced with raw colours, 187 node and 174 e2e tests green. The NJT palette is where it bites, since FFD411 measures 1.43:1 on white. | **Fixed**. A4g now serves the Bergen County Line (FFD411) as its discriminating NJT route, exactly as it already serves the N for the subway, and opens an NJT station popup so the badges join the sample. Mutation **M12** killed. |
| F27 | **Blanking the NJT station marker's accessible name left the whole suite green.** `applyMarkerName` early-returns on a falsy name, so the square would get no role, no `aria-label` and no `aria-hidden` on its svg, and axe does not flag a bare div. `markers.test.js`'s builder check is per FILE and `njt.js` already satisfied it through `njtTrainName`, so adding `njtStationName` to that list was inert. | **Fixed**. Spec 33 reads every `.njt-station-marker`'s `aria-label` and role, the shape A2d already uses for AirTrain. Mutation **M13** killed. |
| F28 | **A4b's 24px target-size gate was not extended to the two new marker classes.** It samples from a literal list, and the 12px station square is the smallest interactive icon on the map, so it is the one most dependent on the shared halo. Reproduced: both NJT halos shrunk to 10px, the whole suite green at all three widths. | **Fixed**. Both classes added to the list. Mutation **M14** killed at desktop, 375 and 320. |
| F29 | **`style.css`'s re-measured legend figures went stale again.** The block states as present-tense fact "unbounded 1280x720 content 771px ... 375x667 content 884px", and three new legend rows moved them to 869 and 982. The block's own opening says it was rewritten because A3's round found these numbers stale, so this is the same defect in the same paragraph a second time. | **Fixed**. Both unbounded rows re-measured, and the block now says outright that a row added to the legend invalidates them, while the bounded pair (which `max-height` fixes) is called out as immune. |
| F30 | **The script-tag comment claimed an adjacency the markup does not have and a dependency `njt.js` does not have.** It said NJT is loaded "after railroad.js ... reading it beside the file it borrows from is the point of the order"; the tag sits three files below railroad.js, the constants it borrows live in `helpers.js`, and no such phase decision is written down anywhere. | **Fixed**. Rewritten to what is true: the position carries no requirement. |
| F31 | **The two new legend swatches inverted the size relationship the map draws.** `.legend-row svg` is a fixed 16px box, so the viewBox alone sets a swatch's apparent size: at viewBox 12 the station's 10-unit square scaled up to 13.3px and read LARGER than the 12px train square, the reverse of the map and of what `njt.js` and `style.css` both say. | **Fixed**. The station row takes viewBox 16 with a 10-unit rect, so the legend shows what the map shows. |

### What the lenses found nothing in

Recorded because a clean lens is a result, and an unrecorded one gets re-run.

**Escaping, and whether the escapes are load-bearing.** Seven `esc()` calls in the
NJT block were deleted one at a time and every one turned a node test red. The only
unescaped interpolations are colours, and they are unreachable with feed text:
`njtRouteTables` is the colour table's only writer and it writes `njtColor`'s
regex-validated value, the defaults are constants, and the panel chip path assigns
through the DOM `style` setter.

**The popup and the accessible name cannot disagree about a delay.** Both read the
same `record.latest` through the same `njtDelayText`, checked across late, early,
zero, null and NaN.

**No regression from the generic `headsign` field**, measured rather than read:
every arrivals fixture in the repo run through `shapeStationArrivals` plus
`arrivalSentence` plus `arrivalsSignature` with and without the clause, and the
subway, railroad, PATH and ferry sentences are byte-identical and every signature is
identical for every kind. `NjtArrival` is the only arrivals model in
`backend/models.py` that declares a headsign.

**The keyed diff itself.** The record shape is symmetric across the create and
update branches, the `_segId`/`_route` cache cannot serve a stale slice (a null
slice is falsy, so it recomputes every poll until the routes land, and a relabel
changes the key), `setMarkerName` is ungated, the popup closure reads
`record.latest`, load order is safe (`map.js` is the last script), the animate gate
matches the toggle exactly, reduced motion skips the glide while the dimming and the
announcement still run, and hiding the layer strands nothing.

**The freshness path end to end.** The backend keys the block "njt", `sourceKeys`
derives the same key so a `systems: null` envelope synthesizes `njt|njt`, and no
other source key is a prefix of `njt|`, so `worstSystemFreshness` cannot
cross-contaminate.

**The contract tier's arithmetic**: `_COS_LAT` is cos(40.7 degrees), `_ON_THE_LINE`
is `route_geometry.COVER_DIST`, the simulator's shapes trace the stops vertex for
vertex, a dropped middle row measures 0.0124 and would trip the tolerance, and
adding "no-shapes" to the shared `PUBLICATIONS` tuple weakens nothing (PATH and the
ferry both list `shapes.txt` among the members they read, so for them the same drop
is a missing required member and their loads fail, which is the divergence worth
being able to express).

**The page and the visual grammar.** The three accessibility suites
(`a11y.spec.js`, `layout.spec.js`, `mobile.spec.js`, 54 specs) pass untouched
against the three new legend rows and the new toggle. `#toggle-njt` computes to "NJ
Transit" and a page-wide sweep found no duplicate accessible name; the new legend
svgs are `aria-hidden` with plain adjacent text; both NJT marker classes are
`role="img"` with a null tabindex and an `aria-hidden` inner svg. Contrast was
computed through the repo's own helpers for every value the layer renders: the
station fill `#334155` is 10.35:1 on white, the neutral fallback `#4a4e69` carries
white ink at 8.12:1 and is 8.12:1 as ink on white, and all twelve published route
colours clear AA both ways after the helpers (the two sharp cases, FFD411 at 1.43:1
raw and A4C9AA at 1.82:1, resolve to 4.50:1 and 4.72:1 as ink and to 12.16:1 and
9.54:1 as chips with dark ink; the worst of the twelve is DD3439 at 4.54:1). At
320x640 with the legend expanded the panel bottom is 610 against a 640 viewport and
the status line is reachable by scrolling.

**One thing checked and deliberately not reported, recorded because it is a
judgement call rather than a clean result.** `njtIcon` emits geometry
byte-identical to `railroad.js`'s placed-train rect, so an NJT train and a scheduled
LIRR or Metro-North train differ only by stroke colour, and the closest cross-palette
pairs are #94219A against #7b1fa2 (RGB distance 26) and #075AAA against #1565c0
(28). `njt.js` argues the borrowing explicitly (the hollow square is the "this is a
schedule estimate" signal a rider has already learned), and the two railroads are
geographically separate from New Jersey, so it stays. The asymmetry is worth naming:
the NJT fallback colour is guarded against PATH's and the ferry's by a test, and
nothing guards these two palettes against each other.

---

## Mutations

Each on a fresh copy of the tree (`git ls-files --cached --others
--exclude-standard`, so a new untracked file is included), the mutation applied
alone, and the named tier run against it.

| # | Guard reverted | Result | Killed by |
| --- | --- | --- | --- |
| M1a | the dedup's fraction arm can never fire (`min_new = inf`) | killed | `test_a_variant_far_enough_along_its_length_survives_without_the_endpoint_arm`, plus a `_CoverIndex` equivalence seed |
| M1b | the dedup's endpoint arm removed (`reaches_somewhere_new = False`) | killed | `test_a_branch_whose_own_terminus_is_its_START_survives_too` **and the real-fixture golden**, which reports Hoboken 0.02664 off route 8: Defect A of Part 1b reappearing |
| M2 | the station projection tolerance set to infinity | killed | `test_the_station_projection_claim_fails_when_a_station_leaves_its_line` (the negative control stops being able to fail) **and** the new tolerance seam test |
| M3 | the marker key switched to `trip_id` | killed | the `njtKey` node test, A2j, spec 33 (**4 markers becomes 3**) and C2f |
| M4 | the NJT stale-sweep registration removed | **SURVIVED** C6e4 and C2f; see below | (then killed by C2g) |
| M5 | the re-skin gated on `route_id` rather than the resolved colour | killed | A2j (`#4a4e69` where `#DD3439` was expected) |
| M6 | the glide reconciliation removed (the payload position used as the far end) | killed | two node tests and spec 33 |
| M7 | the cross-link gated on `stop_id` alone | killed | spec 33 |
| M8 | the leave-the-feed sweep deleted | killed | spec 34 |
| M9 | `loadNjtRoutes` accepts an empty payload as final | killed | spec 34's retry count |
| M10 | the njt row loses `clearOnEmpty` and the quiet arm | killed | spec 35 |
| M11 | the board served in the backend's order rather than the display's | killed | the ordering node test |
| M12 | the NJT ink and badge helpers unwired (raw colours) | killed | A4g |
| M13 | the NJT station marker's accessible name blanked | killed | spec 33 |
| M14 | the NJT hit halos shrunk to 10px | killed | A4b at desktop, 375 and 320 |

**M4 is the one that matters, and it is recorded as it ran rather than as it was
predicted.** The phase brief expected removing the sweep registration to kill C6e4.
It did not, and the reason is structural rather than lucky: in both C6e4 and its
hermetic counterpart C2f the backend keeps SERVING (retained data behind a dead
upstream), so a poll lands every fifteen seconds, `applyNjt` runs, and its own
per-marker `dimMarker` call re-dims everything the sweep would have. The sweep was
doing no work either spec could see. The state that needs it is the one where NO
poll lands: `/api/njt-trains` itself unreachable, so `applyNjt` is never entered
again while the clock walks past the threshold. **C2g was written for exactly that**,
and M4 dies on it. This is the third time in this phase that a mutation's predicted
victim was wrong and the measurement was worth more than the prediction; the other
two are Part 1b's M2 and this round's F16.

---

## Close-out: 15c in production

Recorded when the phase left the branch, for the reason the 15b entry gives: a
review record that stops at its last fix cannot say whether the thing it reviewed
ever worked. Every line below was measured after the merge. One of them still cannot
be measured from where this entry is written, and it says so rather than assert a
number.

**Merged as `fa494a5`, and it is a true merge.** Two parents, `dd64707` (main, at PR
96's merge) and `a52532c` (the branch tip), at 2026-09-03T14:43:14Z, from PR 97 "15c:
NJ Transit route lines from shapes.txt and map layer". Neither a squash nor a
fast-forward, which is what keeps the ledger at the top of this file addressable:

| Pinned hash | Resolves to | Ancestor of main |
| --- | --- | --- |
| `ba2ee26` | `ba2ee267fb74d7632efa859991765dcbd47bace1` | yes |
| `e62bb78` | `e62bb78a4dd8ca1b0ceae6e40c0a3945258e3a77` | yes |
| `468fdad` | `468fdadfafc7f7fb5de8d2c86c67a61b7d6566e3` | yes |
| `7ef0353` | `7ef0353928880fed63253aad626ed0a0b615c56e` | yes |

Checked with `git merge-base --is-ancestor` against main at `fa494a5`, not by reading
the branch. The first three are the Round 1 and Round 1b ledger, which is the whole
record for those rounds; the fourth is the 15b close-out this entry is written in the
form of. A squash would have left all four unreachable from main and turned that
ledger into four dangling citations, which is why the merge shape is recorded as a
fact rather than assumed from the PR being green.

**PR 97 CI green.** Ten check runs on `a52532c`, every one `success`, across the five
jobs the pipeline runs: `backend-tests`, `frontend-tests`, `frontend-e2e`,
`contract-api`, `contract-e2e`. Ten rather than five because the head commit was built
twice, once on `push` and once for the pull request.

| Workflow run | Event | Started | Finished | Jobs |
| --- | --- | --- | --- | --- |
| #449 | `push` | 2026-09-03T14:09:07Z | 2026-09-03T14:13:44Z | 5 of 5 success |
| #450 | `pull_request` | 2026-09-03T14:37:52Z | 2026-09-03T14:42:19Z | 5 of 5 success |

Per job, #449 then #450: `backend-tests` 34s and 48s, `frontend-tests` 10s and 7s,
`contract-e2e` 2m54s and 3m28s, `frontend-e2e` 3m54s and 3m50s, `contract-api` 4m31s
and 4m23s. Both runs are on `a52532c` itself, which is the merge of `dd64707` into the
branch, so the five jobs were green on the exact tree the merge published rather than
on a pre-merge branch tip.

**Deployed, and the gap between merge and serve is small enough to pin.** The
production deployment for `fa494a5` (GitHub deployment `6246319039`) went
`in_progress` at 2026-09-03T14:43:19Z, five seconds after the merge commit, and
`success` at 14:44:00Z. The deployment it replaced was marked `inactive` at 14:44:03Z.

**The first two green monitor runs on the deployed code.** The 6-hourly contract
monitor has run four times on `fa494a5` and every one is green; the first two are the
record here.

| Monitor run | Head | Job start | `njt-static` | `njt-realtime` | `production:feeds` | Totals |
| --- | --- | --- | --- | --- | --- | --- |
| #215 | `fa494a5` | 2026-09-03T16:30:13Z | **WARN** (standing) | PASS, 63 trip updates | PASS, 6 feeds fresh | 0 FAIL, 3 WARN, 18 checks total |
| #216 | `fa494a5` | 2026-09-03T21:06:29Z | **WARN** (standing) | PASS, 103 trip updates | PASS, 6 feeds fresh | 0 FAIL, 3 WARN, 18 checks total |

Runs #217 (2026-09-04T04:40Z, 44 trip updates) and #218 (2026-09-04T11:25Z, 143 trip
updates) have since reported the same three lines and the same totals.

**`njt-static` is a WARN in both runs, not a PASS, and the distinction is the
entry's.** The check reaches upstream and parses the archive successfully; it warns,
and it is the same standing warning recorded at the end of this section. A WARN never
fails a run, so "green" and "PASS njt-static" are not the same claim, and only the
first one is true here.

**`production:feeds` reads 6 in both runs, and it does not step across this merge.**
That is the correct result rather than a disappointing one, and the reason is worth
stating because the same line was the load-bearing witness one phase ago. Under 15b
the count stepped 5 to 6 because that phase added a feed, and the step was the proof
the NJT realtime poller was alive in production. 15c adds no feed. It draws geometry
that was already in the static archive. So the number that settled 15b is structurally
incapable of settling 15c, and reading a steady 6 as evidence for route lines would be
reading it as evidence for 15b a second time.

**Nothing in the monitor witnesses route geometry being served, and this entry did not
witness it either.** Three separate lines look like they might and none of them does:

- `check_njt_static` fetches the RailData archive *from the runner*. `shapes.txt` sits
  in `NJT_WATCHED_MEMBERS`, so its absence would be a WARN, which means the check can
  report on NJ Transit's publication and says nothing at all about the deployment's
  response.
- `production:statics` accepts `njt_static` in `ready` or `not-configured` by design
  (15a, F1). Worse for this purpose, `ready` is explicitly compatible with no geometry:
  `/api/njt-routes` documents `[]` as a valid answer for a publication that carried no
  `shapes.txt`, because route lines are additive and such a publication still serves
  stations and trains and still reports ready.
- `production:feeds` counts feeds, per the paragraph above.

So the witness for route lines actually reaching a rider has to come from outside the
monitor: the rider's own screenshot of 2026-09-03, and a direct read of
`/api/njt-routes` against the deployment. That read is below, kept separate from the
green runs above rather than folded into them, because a monitor that cannot see a
dark route layer cannot be cited as having seen a live one.

**The direct read, and it is the route-geometry witness this section was missing.**
`GET /api/njt-routes` against production at 2026-09-04T14:37:54Z: **HTTP 200**,
`application/json`, **34,449 bytes**.

| Measure | Served by production | Committed fixture |
| --- | --- | --- |
| Routes | 11 | 11 |
| Polylines | 15 | 15 |
| Points | 1,609 | 1,609 |
| Routes drawing two variants | 2, 7, 8, 10 | 2, 7, 8, 10 |

The two columns agree, and they agree per route rather than only in total: 1/74,
2/306, 5/189, 6/187, 7/336, 8/224, 9/47, 10/129, 13/58, 14/8, 15/51 as
(route, points) on both sides. The fixture column is
`njt_static.build_njt_route_shapes` run over the committed `njt_gtfs` members, which is
the same call the goldens make.

**Why these are the numbers that settle it, and not merely numbers.** An empty list
would have been a 200 as well, and `njt_static_status` would still have read `ready`,
which is the exact ambiguity the three monitor lines above cannot resolve; 34,449 bytes
of geometry is the thing an `[]` cannot fake. The 11 is the golden's own assertion
(`len(built) == 11`, with the twelfth route, the event-only Meadowlands Rail Line,
absent by name rather than by arithmetic), so production is serving neither more nor
fewer lines than the record says it should. The 15 against 11 is the dedup having run
in production exactly as it ran in the fixture: four routes keep a second variant and
the rest collapse to one, which is what `_check_variant_counts_are_bounded` asserts as
a bound and this read pins as a number. And 1,609 points is the simplification having
survived the trip through the deployment: unsimplified, this geometry is the 195,545
rows and 6.9 MB that Part 1b's second defect was about.

**What the read does not establish.** It proves the payload a browser receives, not
what the browser drew with it. The rider's screenshot of 2026-09-03 remains the only
witness that these polylines reached a map as visible lines, and that half of the
question is still evidenced by a human's eyes rather than by a measurement.

**The mint budget in force at this deploy.** PR 96 shipped `MAX_TOKEN_AGE_S` at twelve
hours, and the arithmetic that ceiling produces is now the one running in production:

| Consumer | Mints per Eastern day | From |
| --- | --- | --- |
| Contract monitor | 4 | `cron: "17 */6 * * *"`, one mint per run shared across `njt-static` and `njt-realtime`'s two feeds (`_NjtToken`) |
| Production | 2 | `MAX_TOKEN_AGE_S = 12 * 3600`, so a process that stays up through the day re-mints twice |
| | **6 of 10 committed, 4 spare** | `njt_auth.DAILY_MINT_LIMIT`, ten per account per Eastern day, observed 2026-09-02 |

A deploy spends one of the four spare: the restart gives the new process a cold
`TokenCache`, which mints on its first NJT request. This deploy did that at
2026-09-03T14:44:00Z.

**A correction to the premise this section was asked to record.** The 15c deploy is not
the first production run of PR 96. PR 96 merged as `dd64707` at 2026-09-03T06:11:15Z
and reached production on its own (GitHub deployment `6238082417`, `success` at
2026-09-03T06:12:00Z), where it ran until this merge replaced it at 14:44:03Z. Monitor
run #214 at 2026-09-03T11:22Z was already probing a production carrying the twelve-hour
ceiling. So the Eastern day 2026-09-03 carried **two** restart mints rather than one,
at 02:12 and 10:44 ET, and the 15c deploy is the second process under the new ceiling,
not the first.

**What the account actually spent that day is not measurable from here, and is not
claimed.** Tokens are product-scoped, so the Usage API rejects a GTFSRT token and no
process can read its own counter; `DAILY_MINT_LIMIT` exists to name the number, not to
gate anything. The table above is therefore the schedule's *commitment*, derived from
the cron cadence and the ceiling constant, and the two restart mints are derived from
two observed deployments. Neither is an observed count against NJ Transit's limit.

**One standing WARN, and it is still not this phase's.** `njt-static` warns "stops.txt
now carries location_type, parent_station; the loader treats this feed as FLAT and a
parent/child model is a human decision" in both runs above. It is unchanged and
unresolved since run #111 (2026-08-07T00:17:49Z), which reported it when the monitor
still ran 15 checks rather than 18, and the 15b close-out already recorded it as
predating that phase. 15c did not resolve it: this phase drew the stations the loader
already produces, flat, so the parent/child question is exactly where 15b left it and
is now a question about what is on a map rather than about what is in a dict. The other
two WARNs in each run are `path-static` (acknowledged 2026-07-12) and `bus-realtime`
(skipped, `MTA_BUS_API_KEY` not set).
