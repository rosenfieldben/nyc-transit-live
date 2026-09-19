# Phase MR, stage 3 of 5: the commuter-rail grammar

Ten commits off `origin/main` (`49c5956`): the pins, the grammar as pure arithmetic with
the brief's 3.1 table as its oracle, the wiring, the round entries and the captures, your
rulings on N1 through N5, and round 4 (the adversarial panel's two criticals plus your
rulings R-a through R-d). LIRR, Metro-North and NJ Transit draw **one grammar** for the
first time. **This branch deploys backend as well as frontend** (N5). No NJ Transit
credential is set in this environment, so **no mint was spent**. No em-dashes on added lines.

## Round 4: two criticals that had shipped into the diff

| | What was wrong | The fix |
| --- | --- | --- |
| **the casing was never painted** | It passed the literal string `"var(--paper)"` to a **canvas** renderer. Canvas2D resolves no custom properties: the assignment is a silent no-op that leaves the context holding whatever colour it stroked last, so every rail casing drew in the previous branch's ink. Measured in-page on a context primed with `#123456`: the value stayed `#123456`. `shared.js` has carried `paperColor()` since MR2 with the reason written above it, and MR3 walked past it in two files. | `paperColor()` in both rail loaders. **D3e asserted the defect** (`toEqual(["var(--paper)", "var(--paper)"])`) and now asserts the resolved hex three ways: equal to `paperColor()`'s live answer, matching `/^#[0-9a-f]{6}$/`, and containing no `var(`. |
| **N2's pane closed F6 against PATH and reopened it inside the pane** | All three rail families shared one canvas renderer, and a canvas draws in insertion order, so where two branches share track the later casing erases the earlier line. | Two passes per loader was the first fix and **it was not enough**, which the draw chain said out loud: with one renderer the hermetic world produced `[5, 5, 2.5, 2.5, 5, 5, 5, 2.5, 2.5, 2.5]`, so all three NJ Transit casings stroked after both railroad lines. The two route endpoints land in a race and neither loader can order the other's marks. **So the tier is the pane**: `railroadCasingPane` at **394** for every casing, `railroadLinePane` at **395** for every line, one `railDrawRibbons` for all three families. D3e reads both renderers' draw chains and asserts each holds one weight; D2u now holds **four tiers** across a shuffled insertion order. |

### And the review tool refuted both of them

Two verifier worktrees were at `origin/main`, where this branch does not exist, so their
evidence read *"the diff is empty"* and *"`bindRailStationLabel` does not exist"* and both
findings came back confident **REFUTED**. Both were re-verified by hand and both were real.
A refutation that reads *the code you describe is not there* is indistinguishable from a
correct refutation of a hallucinated finding, which makes it the worst output a review tool
can produce: it deletes a real defect and looks like diligence doing it.

Fixed structurally as **RULE 0b** of `.claude/workflows/adversarial-review.js`. The caller
passes `{commit, branch}`; every agent's prompt opens with a preflight that runs
`git rev-parse HEAD`, `git checkout --detach <sha>` if it does not match (detached, because
N worktrees share one object store and a branch checkout either fails or moves the caller's
ref), and `git diff --stat <range>` to prove the diff is non-empty; every schema **requires**
the sha it read and the file count it saw; and the script discards any agent whose sha does
not match. A discarded verdict set makes its findings **UNVERIFIED**, never refuted. A
discarded finder dimension is logged as lost coverage. A triage from the wrong tree is thrown
away whole, because triage re-reads the code to correct each finding's location and a triage
on the wrong branch would "correct" every real finding into a drop.

### And the mutation harness was broken the same way

M9 puts the literal back on the casing, which D3e asserts against three ways. **It came back
green.** `playwright.config.js` sets `reuseExistingServer: !process.env.CI` and `serve.js`
resolves its root from its own `__dirname`, so a static server left up from the main checkout
is silently reused by a worktree's run and every browser assertion reads the **unmutated**
frontend. A mutation that cannot die is the one failure mode mutation testing exists to catch.
The driver now kills by **port** rather than by command text (a `pkill` on the server's path
also matches the shell running the driver), refuses to run a browser gate while the port is
held, and sets `CI=1`. Every mutation below was re-run under the fixed driver.

## Your four rulings

| | Ruling | What it changed |
| --- | --- | --- |
| **R-a** | drop `railDirectionReverses` from both geometry paths | Gone, with its export. `railTrainBearing` reads `pointAtArcLength(points, cum, s0)` to the same at `s1`, which is the **interval** rather than the whole branch: verified on a bending polyline that the two legs read 0 and 90 while the end-to-end chord reads neither. On the real F01 capture the three inbound anchored rows moved from 104/78/32 to 284/258/212. **`railtag.test.js:497` asserted the defect** (180 over a pair whose true azimuth is 0) and is corrected in place with the reason; the ledger records that a test asserted it, because a test that asserts a defect makes the bug load-bearing. |
| **R-b** | an explicit table matching contract 3.3 | The gate it replaces was **right by accident**: bus, subway, PATH and ferry rows carry no `system` field, so `has(undefined)` was false and four families were gated as a side effect. `OBSERVATION_GATED` is the 3.3 table transcribed, keyed by the family each layer knows; `observationGated` reads the row's own system first, which is why `railroads` has no row and its two systems disagree. `positions.test.js` scrapes every `vehicleMarkerAge("<key>"` call site from `systems/` and asserts the set is exactly the six sources and that every family one can name has a row, in both directions against `UNDATED_SYSTEMS`. The header-less subway case is asserted **as intended**, and the erratum says why `/healthz` keeps the operator rule while the rider sees "age unknown" and dimmed. |
| **R-c** | Belmont Park is BEL | The live feed serves LIRR route 11, Belmont Park, colour `60269E`, against the brief's *"There is no route 11"*. Thirteen branches now, and the **count assertion is what caught it**. The brief keeps its sentence and carries a dated erratum under it. |
| **R-d** | refetch once with `cache: "reload"` | `fetchRoutesPayload(url, field)` in `shared.js`, with the pure predicate `staticPayloadHasField` in `helpers.js` so node can ask it. Both static route endpoints are served under an hour-long cache, so the deploy that adds a field ships a frontend reading it against a response from before the backend rolled: well formed, field absent, nothing errors, every NJ Transit tag prints "9" for up to an hour. Keyed on **some** entry, not every: route 17 (Meadowlands, event-only) never reaches the endpoint with a short name, so "every" would re-read past the cache forever. A null value counts as absent, because a half-rolled nullable column looks exactly like an unknown field. **Follow-up you asked for**: a version stamp on the static-derived endpoints, so the frontend can ASK rather than infer. Recorded in the ledger. |

## Six more repairs in the same round

- **The Names toggle's sentence read only the subway's band.** The toggle hides rail names too
  and the two bands disagree by design: rail from 11, subway from 12. At zoom 11 the button
  said *"none at this zoom, zoom in to see them"* while the press had just switched off every
  rail name on screen, which is the one thing that sentence exists to prevent.
- **`paintZoomBand`'s sentinel counted rail labels as subway ones.** With rail labels present
  and subway labels not yet, the counts read "labels: many, hubs: zero", which is the
  **degraded** band: every subway name from 13 instead of hubs from 12, on a map whose subway
  index is merely still in flight.
- **`railTagState.dim` was a second expression of the dimming rule that nothing read**, and it
  had rotted into contradicting your own N3 ruling: the paragraph above it argued at length for
  `dim = false` on row 6. Gone, with its `age` parameter; the opacity column is asserted against
  `markerOpacity` itself.
- **`railTagHeadingTrusted` was dead and wrong**: exported, never called, and its rule
  disagrees with what the table does for a retained row.
- **`markerAge`'s docstring said the opposite of what N3 settled.**
- **`njtTagState` dropped the `now` it was passed**, so the stale sweep's pinned clock produced
  the live clock's words.

## And three specs in this branch were weaker than their titles

**D3b** is titled *"a retained train is drawn as the state it was in, **dimmed**"* and asserted
only the two shapes. Worse, its world **could not have dimmed**: it carried the FRESH system
blocks beside rows stamped `retained`, a payload saying at once "this generation could not be
refreshed" and "the last successful poll was a moment ago". The world now ages the blocks with
the retention, which is what the backend serves, and the spec asserts 0.45 off the element's
inline style for every retained row and 1 for the before. **D2u**'s shuffle drew the rail
families on `lineRenderer`, which is not the renderer production uses, and used the literal
`"var(--paper)"` as a canvas colour. **D2j** asserted *"none at this zoom"* at zoom 11, which
MR3 made false; the claim moves to zoom 10, where both bands are "none".

The two audit-record DOM stubs learned `:not(.class)`, which their own error message asked for.

## What the three families drew before

| | before |
| --- | --- |
| a train | LIRR and Metro-North a 16x16 rounded square; NJ Transit a **byte-identical copy** of its hollow rect, in a different file, with no shared helper between them, so changing one did not change the other |
| the colour | a **hash of the route id**, not the agency's palette. Two branches sharing one published colour got two different ones; a branch whose id moved changed colour |
| a station | LIRR and Metro-North a 3.5px white circle; NJ Transit a 12px filled slate square. "A square always means regional rail" was true of none of them |
| a branch line | one 2.5px hairline at opacity 0.5 |
| a station name | none: a canvas `circleMarker` has no element to hang a tooltip on |

## The 3.1 table is the deliverable, so it is a function

`railTagState` answers it and `railTagSvg` renders the answer as a **string**, both in
`helpers.js`, so node asks them one row at a time. That is not tidiness: **two of the seven
rows are unreachable in any fixture world** (a `reported` row with no clock on a gated
system, and a retained row the page never saw), so a browser test could not have covered
them at all. `frontend/railtag.test.js` asks all seven, one case per row.

**It is keyed on `positionQualifier`'s `kind`, not on provenance alone.** Three rows are the
same provenance and the app already has a vocabulary that separates them: a `reported` fix is
row 1 when fresh, row 2 when aged and row 6 when the provider sent no clock at all. That is
one function's answer rather than three re-derivations, which is what v3.1 requires.

**The body is `railroadHollow`'s answer, called rather than restated**, so there is one
expression of "did this train report this position". A node case asserts the tie across every
provenance and every `before` the app can produce, and counts the exceptions.

| row | body | head | dimmed |
| --- | --- | --- | --- |
| reported, unqualified | solid | filled | no |
| reported, qualified | solid | filled | **yes** |
| **estimated** | **outlined** | **filled** | no |
| placed | outlined | outlined | no |
| retained | as it was | as it was | yes |
| unknown, age-gated | outlined | outlined **dot** | see N3 |
| unknown, Metro-North | solid | filled | no |

Row 3 is the one the table exists for: *"we know where it is going but not exactly where it
is."* **Before this stage rows 3 and 4 were the same mark.**

## Six findings, all measured

### N1. The agencies publish an ink that does not always work

Of the **19** `(route_color, route_text_color)` pairs the two MTA railroad feeds publish,
only **eleven** carry 4.5:1 as published. Measured 2026-09-19 over the live archives:

| | published ratio | |
| --- | --- | --- |
| Babylon Branch `00985F` / `FFFFFF` | **3.71** | readable fill, unreadable ink |
| Oyster Bay Branch `00AF3F` / `FFFFFF` | **2.92** | the worst of the four |
| Long Beach Branch `FF6319` / `FFFFFF` | **2.98** | |
| Hudson `009B3A` / `FFFFFF` | **3.65** | |
| New Haven, New Canaan, Danbury, Waterbury `EE0034` / `FFFFFF` | **white 4.48, dark 3.88** | a fill NO ink can rescue |

`railBranchPaint` returns the **pair**: the feed's ink where it clears, the computed one
where a readable fill carries an unreadable ink, and where neither clears, the fill scales
**1% toward black** (`EE0034` to `#ec0033`, white 4.48 to 4.55). That is the same
hue-preserving scaling `readableInk` already uses, and it is the remedy the note at
`railroadColor` already names for this class: *"a fill that has to move rather than an ink
that has to be chosen."*

**The route line is never moved**, only the 24-by-13 block with 8px type on it. A rider
comparing tag to line on the New Haven family sees the same red; a meter does not. The three
counts (11 as published, 4 inks recomputed, 4 fills moved) are asserted.

**This qualifies a sentence `claude/railroad-route-colors` put in two docstrings**, that "a
renderer can trust a railroad `text_color`". It can be **preferred**. The four ratios are why
it cannot be trusted.

### N2. The 5px casing is on the shared canvas, which is MR2's F6 in a new costume

Leaflet's canvas draws in **insertion order**, so a rail casing arriving after PATH's 3.5px
line, the AirTrain's 3px or the ferry's 2px covers it where they overlap. The subway got its
own pane for exactly this. Nothing makes the rail casing safe against those three, and the
eleven static loaders land in whatever order their responses do.

**Built as specified** ("casing and line per branch on the existing canvas") **and the
exposure written down rather than left to be met on a map.** D2u gained NJ Transit, which was
**missing from its family list entirely**, and its shuffle now draws each family's casing as
well as its line; it asserts all four non-subway families share one pane and that the thinnest
is thinner than the casing over it.

**A pane at 395, between `subwayLinePane` and `overlayPane`, closes it in one line.** That is
your call, not this stage's.

### N3. Row 6 of the table cannot be drawn as written

The table says an age-gated row with no clock is **dimmed**. Dimming is `markerOpacity`'s,
`markerOpacity` reads an age, and the entire content of that row is that **there is no age**.
`staleAge(null)` is false.

**The body and head halves are obeyed and the opacity half is not.** Dimming it would tell a
rider "this is old" about a train whose age the same tag has just said is unknown. The
pessimism the row exists for is carried by an outlined body and an outlined dot, which is the
strongest "do not trust this" the tag can draw. It is the one place the body goes past
`railroadHollow`, and the freshness contract is why: clause (c) is an **anomaly** in the
contract's own words. The deviation is asserted **as a deviation**, so a later stage that
decides to dim it has to come here first.

### N4. axe cannot judge the tag's type

With the tags on the map, axe's color-contrast rule reported **37 findings at 1280 and 17 at
375**, all *"background color could not be determined because it is overlapped by another
element"*: a tag is 35 to 45px wide where the square was 16, so at regional zoom the tags
overlap each other. `aria-hidden` does not silence it and **should not**, because a sighted
rider still sees the type.

So it is a **named shape with a decider**, which is A1w's own protocol. **A1z4** reads each
tag's printed ink and the fill of the block under it off the **drawn page**, in both themes,
requires AA, and asserts every `svg.rail-tag` belongs to a rail tag marker so the exception
cannot widen. ACCESSIBILITY.md carries both statements, and `statement.test.js` A4 caught the
omission when I first forgot it.

### N5. One enabling backend change

**`/api/njt-routes` did not serve `route_short_name`**, so the brief's stated source for NJ
Transit's branch code was not reachable and every NJ Transit tag would have read its route id
("9" for the Northeast Corridor). `njt_static` has parsed the column since 15c and the
builder dropped it. `NjtRoute.short_name`, None default, additive exactly as the colour
fields are. The two exact-dict guards in `test_api.py` are **updated rather than relaxed**.

### N6. Two neutrals for an unknown route, left deliberately

The tag and the line take the README's stated `#6d6e71`; `njtColor`'s older `#4a4e69` still
reaches the NJ Transit popup head. The popups are **stage MR5** and P1k pins this one byte
for byte, so changing it here would break a pin that must hold. MR5 is where they converge.

## Three guards fired during the wiring, and all three were right

Each was a place the stage was about to diverge quietly.

- **`markers.test.js`** caught a raw `L.marker` for the station square, written on the
  subway's reasoning (a station carries no accessible name), which holds only because a
  canvas `circleMarker` has no element to name. NJ Transit's squares have gone through
  `labeledMarker` since 15c; these do now, with `railroadStationName` beside
  `njtStationName`. Still not a tab stop: `labeledMarker` owns `keyboard: false`.
- **`positions.test.js`** caught the glyph rule leaving `railroad.js`. The **call** moved into
  `railTagState` and the **rule** did not, since that function calls `railroadHollow`. The
  guard follows the chain now and also asserts `njt.js` reaches the same table, which is what
  makes "one grammar for three families" checkable rather than asserted.
- **`statement.test.js`** caught N4's exception missing from the accessibility statement.

## Pins

| Pin | |
| --- | --- |
| **P3a** | A rail station's registry entry, all three agencies. Pins the pane as **drawn**, not as configured, which is the opposite choice from P2a's and for the same reason: MR2 kept the subway a circleMarker, MR3 turns these into `L.marker`s, so the invariant is the answer and not the route to it. |
| **P3b** | The panel for LIRR Jamaica. Its arrivals read "Babylon Branch", so it fails if the restyle loses the route-name resolution. |
| **P3c** | The alerts join on **both** surfaces, with its own alert list: the stop-scoped alert reaches Jamaica by id, the route-scoped one **only** through the arrivals board, and a third on a route that does not serve Jamaica is the negative. |
| **P3d** | The ladder's five states by count and by words: 60 / 11 / 6 / 59 drawn, 24 withheld on the status line. |

**P3b and P3c caught a pin measuring the wrong thing before a line of the stage was
written.** "jamaica" matches two stations and the AirTrain's row sorts first, so `.first()`
pinned "Jamaica (AirTrain)" under the key `panel/lirr`: a filled-in golden that would have
held through any change to the surface it exists to watch.

## Tests

`tests/e2e/rail.spec.js`, **D3a** through **D3f**: the six reachable rows on one page found
by **accessible name**, with four distinct marks across them; the retained row over two
polls; squares and circles counted both ways with one shape asserted byte for byte across
three agencies; the rail label band at 10, 11 and under the Names toggle; the casing pair and
the feed's colours; and Metro-North solid, live, saying its undated policy once on the status
line and never on a marker.

**`smoke.spec.js` C2j, the F01 acceptance, now reads the body AND head of all 136 markers**,
which it could not do before, because two of its populations drew the same mark.

Four specs that read the old mark are **updated rather than relaxed**: the two hollow probes
read the state off the svg's class instead of sniffing `rect[fill="#fff"]`, which is the
decision rather than an incidental paint value; A2j reads the branch colour where it now
lives and also asserts the **code** arrives on the same late fetch; A4b measures the tag's
three parts rather than its box, because a 30px box is the glyph's real extent and the old
assertion would read it as an inflation; and the two subway label specs are scoped to
`:not(.rail)`, since the two bands overlap on purpose and neither may be asserted through the
other.

## Mutations, each in an isolated worktree

| # | Reverted | Result | Killed by |
| --- | --- | --- | --- |
| M1 | the chevron filled for a placed train | **killed**, both tiers | row 4, and D3a on the page |
| M2 | the body solid for an estimated train | **killed**, 3 node | row 3, the body-tie, the markup test |
| M3 | the 6.3 erratum reverted, so an undated gated row draws bright | **killed**, 2 node + 2 e2e | the header-less subway case, row 6, D3a and D3b. Restated in round 4: the table no longer carries an opacity column, so the mutation reverts the rule itself |
| M4 | Metro-North gated | **killed**, node + e2e | row 7's policy case, D3f, and now the every-family-listed test in both directions |
| M5 | a circle for a rail station | **killed**, both tiers | the square markup test, and D3c's count |
| M6 | *retired.* It reverted the inbound reversal and R-a removed the reversal; **M12** and **M12b** replace it | | |
| M7 | the code table keyed by id again | **killed**, both tiers | the name-keyed test, and D3a's glyphs on the page |
| M8 | the ink computed even where the feed supplies one | **killed**, 2 node, **e2e green** | the two railroad ink tests |
| **M9** | the casing's colour back to the literal `"var(--paper)"` | **killed** | D3e's three-way assertion. **This is the one that exposed the harness**: it survived until the driver stopped reusing the main checkout's server |
| **M10** | the casing back on `railroadLineRenderer` | **killed**, both specs | D3e ("no rail casing reached the casing canvas") and D2u ("lirr is on the rail panes") |
| **M11** | the bearing read end to end over the whole branch | **killed** | the bending-polyline case, where the two legs read 0 and 90 and the chord reads neither |
| **M12** | the direction word turns the slice again | **killed** | the all-directions loop over a north and a south slice |
| **M12b** | the direction word turns the served anchor pair again | **killed** | the corrected `railtag.test.js:497`, the line that used to assert this |
| **M13** | one family dropped from `OBSERVATION_GATED` | **killed** | the every-family-listed test, which scrapes the call sites from `systems/` |
| **M14** | `staticPayloadHasField` keyed on EVERY entry | **killed** | the route-17 case |
| **M15** | the Names sentence reads only the first band | **killed** | the four variadic cases |
| **M16** | the band's sentinel counts rail labels again | **killed** | D3g, on a world with rail stations and no subway ones: "all" instead of "hubs" at zoom 13 |
| **M17** | the table grows a second dimming rule (`dim`) again | **killed after the guard was widened** | it SURVIVED first: the keys assertion asked one row, so a `dim` on the estimated branch alone passed. Every return site is asked now, and each is asked whether an age changes its answer |
| **M18** | `njtTagState` drops the clock it was passed | **killed** | the threading assertion |
| **M19** | the re-skin gate stops comparing `headingTrusted` | **killed** | the skin-key coverage test, which reads the icon's inputs off `railTagIcon` itself |
| **M20** | Belmont Park removed from the code table | **killed** | the count assertion, which is what found it in the live feed |
| **M21** | NJ Transit draws its own pair on the line renderer again | **killed**, both specs | D3e ("the line canvas holds only lines") and D2u ("njt is on the rail panes") |

**M8's green e2e is the signature, not a gap**: NJ Transit publishes no `route_text_color` at
all, so its half of the grammar cannot tell the difference and the railroads' half can, which
is exactly what you predicted.

**Two mutations survived their first run and both were real gaps.** M9 survived the broken
harness above; M17 survived a guard that asked one row of a five-row table. Neither is a
mutation "not worth keeping": a surviving mutation is the finding.

## Gates

| Gate | |
| --- | --- |
| `pytest` (backend) | **1738** passed |
| `ruff check` / `ruff format` | clean, 79 files |
| `mypy` | clean, 30 source files |
| contract-tier lint and format | clean |
| node | **334** passed (from 312) |
| hermetic e2e | **281** passed (from 270) |
| browser contract tier (C6) | **4** passed |
| contract API tier | **38** passed |
| `run_all.sh` | **15 of 15** |

## Before and after

`docs/reviews/map-redesign/mr3/`, the Rail preset on the stock fixture world at the frozen
clock, the two runs differing only by the tree they ran in (`MEASURING.md` says how).

| | 1280 | 375 |
| --- | --- | --- |
| before | `before-desktop.png` | `before-375.png` |
| after | `after-desktop.png` | `after-375.png` |

---
🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_017oZP4Mtd6BLoSvAeVaPrJz
