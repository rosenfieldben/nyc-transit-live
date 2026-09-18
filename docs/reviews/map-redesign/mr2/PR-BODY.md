# MR2: subway

Stage 2 of 5 of Phase MR, against the v3.1 handoff at
[`docs/design/map-redesign/`](https://github.com/rosenfieldben/nyc-transit-live/tree/claude/mr2-subway/docs/design/map-redesign)
as amended by the ledger's rulings. Trunk ribbons, the bullet train marker, dot-or-ring
stations, haloed station names with their zoom gate and a Names toggle, and route focus
wired to the bullets MR1 drew. **No NJ Transit mint was spent**: nothing in this branch
touches the backend, `njt_auth.py` or any credentialed path, and the contract tier aims
every NJ Transit seam at its simulator as it always has.

| Commit | |
| --- | --- |
| `cd8ad77` | pins: what the subway restyle is not allowed to change on its way past |
| `ecb67da` | the subway, drawn as ribbons, bullets, dots and rings |
| `8e8e3b5` | the round, the measurements and the before-and-after pair |
| (tip) | round 2: an adversarial pass over the written diff |

## Before and after

The four captures are committed in this branch at
[`docs/reviews/map-redesign/mr2/`](https://github.com/rosenfieldben/nyc-transit-live/tree/claude/mr2-subway/docs/reviews/map-redesign/mr2).
They are links rather than inline images because the tooling that posts this body strips
embedded images from it.

| | 1280 | 375 |
| --- | --- | --- |
| before | [before-desktop.png](https://github.com/rosenfieldben/nyc-transit-live/blob/claude/mr2-subway/docs/reviews/map-redesign/mr2/before-desktop.png?raw=1) | [before-375.png](https://github.com/rosenfieldben/nyc-transit-live/blob/claude/mr2-subway/docs/reviews/map-redesign/mr2/before-375.png?raw=1) |
| after | [after-desktop.png](https://github.com/rosenfieldben/nyc-transit-live/blob/claude/mr2-subway/docs/reviews/map-redesign/mr2/after-desktop.png?raw=1) | [after-375.png](https://github.com/rosenfieldben/nyc-transit-live/blob/claude/mr2-subway/docs/reviews/map-redesign/mr2/after-375.png?raw=1) |

Both from the hermetic harness at the frozen clock over the **real subway geometry**,
centred on Midtown at zoom 13, so the pair shows a network rather than the fixture's two
shapes. Two more, because two of the five items cannot be seen in a still of the default
state: [after-desktop-names-off.png](https://github.com/rosenfieldben/nyc-transit-live/blob/claude/mr2-subway/docs/reviews/map-redesign/mr2/after-desktop-names-off.png?raw=1)
is the ribbons and the marks with the Names toggle off, and
[after-desktop-focus-4.png](https://github.com/rosenfieldben/nyc-transit-live/blob/claude/mr2-subway/docs/reviews/map-redesign/mr2/after-desktop-focus-4.png?raw=1)
is route focus on the 4, where Lexington Avenue stands at full strength and every other
trunk is at 0.18 with its casing gone.

## The measurements the brief asked for

Taken against the **real network**, because the hermetic fixture serves two shapes and two
stations and cannot answer a question about drawing a city.
[`MEASURING.md`](https://github.com/rosenfieldben/nyc-transit-live/blob/claude/mr2-subway/docs/reviews/map-redesign/mr2/MEASURING.md)
carries the two commands that rebuild the capture from the MTA static archive through the
backend's own loaders, and `measure.spec.js` beside it is the harness. The capture itself
is 514 KB, eight times the largest fixture this repository carries, and is deliberately
not committed.

**The network**: 24 routes, **35 shapes**, **22,520 points**, **496 parent stations**, of
which **171 have one route** and **325 have two or more**, and none has zero. MR2 draws
**70 polylines and 45,040 points**, exactly double, and **171 dots and 325 rings**, which
the shipped code reproduces exactly.

### Doubling the polylines costs nothing measurable

A/B in one page so both samples share machine conditions; four animated pans at City zoom
per sample, 250 to 330 frames each.

| | polylines | median | p90 | p95 |
| --- | --- | --- | --- | --- |
| before | 35 | 17.0 to 19.0 ms | 20.4 to 22.6 | 21.6 to 25.4 |
| with the casings | 70 | 17.2 to 18.2 ms | 20.4 to 22.4 | 21.5 to 24.2 |

**So the answer to the conditional is no**: it does not cost more than a few milliseconds
per frame, it does not cost anything, and there is no cut to propose. Absolute times are a
headless container's and mean nothing on their own; the comparison is the measurement.

### The tooltip count, and why they are not gated to the viewport

At zoom 14 over Midtown, **62 of the 496 labels are inside the viewport**. Not thousands,
so by the brief's own test they may all be permanent.

The cost that does exist is in the **painted** count, and a viewport gate does not recover
it:

| painted labels | median | p90 | p95 |
| --- | --- | --- | --- |
| 0 | 17.0 to 17.4 ms | 20.4 to 21.1 | 21.5 to 23.3 |
| 10 | 17.8 | 21.0 | 22.1 |
| 156 (a viewport gate at City zoom) | 20.0 | 26.7 | 33.7 |
| 325 (the zoom gate at City zoom, which ships) | 19.8 to 21.2 | 26.7 to 29.5 | 30.5 to 36.8 |
| 496 (every name, the band from zoom 14) | 20.8 to 23.2 | 29.5 to 32.9 | 34.7 to 44.9 |

156 painted costs the same as 325. The whole difference appears between 10 and 156, so a
viewport gate would add a `moveend` handler and a class sweep for no measured gain. The
two switches that work are the zoom band and the Names toggle: the shipped code measures
20.7 median / 35.3 p95 at City zoom with 325 painted, and **18.4 / 22.7 with names off,
which is the no-labels baseline exactly**.

## What changed, and the mechanism

**Ruling R1 is obeyed twice over.** The handoff gives the authority's official trunk hexes
and a circular lettered bullet; this repository's README says route symbols require a
license and to use our own colours and markers, and round 3 ruled the README wins. So
every ribbon takes `lineColor()`'s answer and every bullet keeps the rounded rectangle
`systems/subway.js` has always drawn. `D1l` held the Key's half; **`D2k` holds the map's**:
no train marker contains a circle, every fill is `lineColor()`'s answer, and so is every
ribbon's colour.

**Two passes, not one per shape**, which the design does not say and which matters. Casing
and line interleaved per route means a route drawn later cuts a paper gap through every
route already drawn; since the yellow trunk is drawn LAST on purpose, interleaving would
have it erase a stripe out of every trunk it shares track with, which on Broadway is three
of them. So: all casings, then all lines, each pass in trunk order.

**`trunkDrawOrder` is in `helpers.js` with a node test**, because the reference
implementation's version of that sort tests only N and R and leaves Q and W under the
darker trunks. A test written against N would have passed.

**The bullet's halo is a token rather than `#fff`**, which is the only thing about the body
that moved: it used to be a 1.5px white stroke ON the body and is now a 1.5-unit paper ring
BEHIND it, at the design's 0.95. The body's geometry is unchanged (`rect 1.5,1.5,15,15
rx 3`); the type is the design's (Archivo 800, 10.5px for one character and 8.5px for two);
the anchors are the design's (`[9,21]`, `[0,-21]`); the 24x24 hit target and its bottom
anchoring are untouched.

**A station the backend served no routes for is a dot, not a ring.** Measured against the
real archive, no station has zero routes, which is exactly why the fallback had to be
chosen deliberately rather than discovered: a backend that stopped serving the index would
otherwise turn all 496 into interchanges, which is a claim about the network made out of
an absent value.

**The zoom gate is a band, not the zoom.** The root carries `data-zoom`, which is the
design's attribute, and `data-label-band`, which is `labelZoomBand()`'s answer and is what
the stylesheet reads. The design enumerates `[data-zoom="12"]` through `[data-zoom="19"]`
because CSS cannot compare integers, and an enumeration fails silently outside its range:
at zoom 20, or at any fractional zoom, no selector matches and every name disappears with
no error. Three values, one node-tested function, no range to fall out of.

**The labels are out of the accessibility tree, and that is the status quo rather than a
subtraction.** A canvas circleMarker has no element, which is why station dots have never
had an accessible name; the A2 footnote says so and names the station panel as the
equivalent. A permanent tooltip is the first DOM these stations have ever had, and leaving
496 bare place names in the reading order would undo that silently. `aria-hidden` does
**not** exempt them from axe's contrast rule, which is correct, so MR2 adds a seventh named
undecidable shape with `a11y.spec.js A1z3` as its decider and the matching row in
`ACCESSIBILITY.md`. A1z3 was mutation-tested by removing the halo, which fails it.

**Route focus is opacity and nothing else, and it composes with the freshness contract
rather than competing with it.** `dimMarker` and `markerOpacity` already take a `base`
multiplier and focus supplies it, so nothing in section 3.3 is touched: a stale train on
the focused route stays dim, a stale train off it is dimmed twice, and the poll fifteen
seconds later re-derives the product instead of erasing the focus. Escape clears it from
the **bottom** rung of `map.js`'s ladder, which is the page's only keydown handler; the
rung is last because route focus is a map-wide state rather than a surface anyone stands in,
and `D2f` walks all three rungs to say so.

## Two deviations, both measured

| | Drawn | Measured | Shipped |
| --- | --- | --- | --- |
| the route bullet's size | 22x22 | the bullets are controls now, and 22 is under the WCAG 2.2 target floor `layout.spec.js A4b` enforces: at 22 with the design's 2px group gap the centres are 24 apart, which is the boundary of 2.5.8's spacing exception rather than clear of it | 24x24, and the bullets joined A4b's list and the a11y owned list rather than being exempted |
| the unfocused bullets | fade to opacity 0.3 | a 24px chip at 0.3 over `--surface` blends both fill and letter towards the surface and the letter's contrast against its own chip collapses to near 1:1: the state would be conveyed by making twenty-five route names unreadable. MR1 round 2 found the same defect in the feed strip's OFF treatment | not implemented. The pressed bullet carries a ring in `--ink` (13.70 light, 12.60 dark on `--surface`), the focus ring sits OUTSIDE the chip where it is measured against the surface rather than against ten different fills, and the map says which route is focused by dimming every other one |

## What round 2 found, after the diff was written

MR1's round 2 was the highest-yield step of that stage and MR2 had not had one: the
workflow that ran BEFORE this stage was recon, and the mutations after it test the guards
rather than the diff. Three findings, two fixed.

- **The pressed bullet's ring was clipped by its neighbour.** The bullets are siblings in a
  flex group with the design's 2px gap, all `position: relative` with `z-index: auto`, and
  siblings paint in DOM order, so a ring reaching 4px out was drawn into the gap and then
  painted over by the next bullet's background for its outer half. Measured on the "2"
  bullet: whole on the left, cut off on the right. **This is MR1's G1 in a different
  costume**, a rule that looks right and draws wrong, and the only way to see either one is
  to look at the drawn page. Fixed with one stacking level; `D2a` holds it.
- **`stationLabelShown` was exported, node tested and called by nothing.** The gate is three
  CSS rules, so a node test over that function read as coverage and decided nothing.
  Converted rather than deleted: `D2j` now drives the stylesheet against it across zooms 11
  to 15 in both toggle states. **And then the comment saying so was corrected**, because a
  mutation showed the grid's limit: the oracle and the attribute the CSS reads both come
  from `labelZoomBand`, so a mutation to the RULE moves both sides together and leaves all
  thirteen specs green. The node tier kills that one, and the division is now written down.
- **Three bullets focus nothing and four drawn routes have no bullet.** Raised as F3 below.

**What the pass checked and found clean**, recorded because a review that lists only its
hits reads as if it looked only where it found something: `.bul` and `.stn-label` reach no
other surface (the MR1 `.alert-stale` hazard); route focus survives a feed hide and show
and a `setIcon` relabel, on both the option and the rendered element; the labels come back
`aria-hidden` after a feed toggle; the pressed ring's backdrop really is `--surface`, so
the 13.70 and 5.53 are measured against the right colour; and a label overlaps a train
bullet in a 2x3 pixel corner and no more.

## Four findings for the operator

**F1. The station names collide.** In the viewport over Midtown, the share of painted
labels whose box intersects another one is **89% at zoom 12, 77% at 13, 40% at 14 and 29%
at 15**. The design's gating does not declutter and neither does the reference
implementation. It is visible in `after-desktop.png`, where "34 St-Penn Station", "34
St-Penn Sta" and "34 St-Herald Sq" sit on top of one another. A rider can turn the names
off, which is what the Names toggle is for, and the zoom that reads best is 15.

Two remedies, neither in this stage's scope: **declutter**, by hiding a label whose box
intersects one already placed, recomputed on `moveend`; or **narrow what a hub is**, since
"two or more routes" is 325 of 496 stations and the design's word suggests the big
interchanges. The first is a new mechanism with its own questions (which label wins, and
does it flicker on a pan); the second is a threshold the record does not fix. Shipped as
specified and raised here, which is how the MTA palette question reached round 3.

**F3. Three of the twenty-three bullets focus nothing, and four drawn routes have no
bullet.** Against the real static archive the map draws `1 2 3 4 5 6 7 A B C D E F FS G GS
H J L M N Q R SI`, and the key is MR1's ten trunks. **Z, W and S draw no ribbon at all**,
so pressing one dims the whole map and highlights nothing; **FS, GS, H and SI have no
bullet**, so the Staten Island Railway and every shuttle are unfocusable. The **S** bullet
is the sharp one: the shuttles ARE drawn, under the ids GS, FS and H, and focusing S
matches none of them. MR1's bullets were display only, so the mismatch was invisible; MR2
is the stage that makes it bite.

The fix is an alias table, so one bullet can name several feed route ids, plus a decision
about SI in the key. Both touch MR1's trunk set rather than this stage's five items.
Focusing by colour instead would be worse: 1, 2 and 3 share one hex, so it would light all
three.

**F4. The key costs twenty-three tab stops.** Measured from the top of the document,
reaching the Stations button now takes **32 presses**, where before this stage it took
nine. The skip link is still the first stop, so a rider heading for the station panel is
unaffected; a rider heading for the Key or Stations button walks the whole key. The
standard remedy is a roving tabindex (`role="toolbar"`, one tab stop, arrow keys inside),
about twenty-five lines, and `frontend/keyboard.test.js` already has the seam for it. It is
a new interaction mechanism rather than one of this stage's five items.

**F2. A yellow line on a paper casing reads 1.67 in the light theme.** Every trunk against
`--paper`, through the repository's own helpers: N/Q/R/W **1.67**, B/D/F/M 2.52, G 2.66,
L 3.11, and everything else between 4.22 and 8.31. Three trunks are under the 3:1 a
non-text indicator owes against the colour beside it. **It is better than what it
replaces**: before this stage the same lines were 2.5px at opacity 0.5 straight onto the
basemap, so every trunk gained. And route identity is never carried by a line alone: every
train names its route in words, every popup does, and the Key panel does. The fix is a
change to `LINE_COLORS`, which every surface reads, and that belongs in a stage of its own.
The dark theme's column is worse (SI 1.79, A 2.73, S 2.77, J 2.69) and is MR4's to answer,
for the same reason MR1's Key glyphs were.

## How each new claim is witnessed

| Claim | Witness |
| --- | --- |
| the key is 23 named buttons whose text is still the bare route id | `subway.spec.js` D2a |
| focus dims every other ribbon and every other train, and pressing again clears | D2b |
| pressing a different bullet moves the focus rather than clearing it | D2c |
| focus is opacity only: no layer is added, removed or rebuilt | D2d, on `L.Util.stamp` identity across a focus and a clear |
| a focused route survives the next poll, and the contract still composes | D2e, including that focusing a stale train's own route does not restore it |
| Escape clears the focus, and only once there is nothing else to close | D2f, walking all three rungs |
| the yellow trunk is drawn last, on the map, in both passes | D2g, over a payload that lists the yellow routes first |
| a ribbon is a casing in paper under a line in the app's own colour | D2h, asserting the casing IS the token's value |
| a transfer ring, a local dot, and a routeless station drawn as a dot | D2i |
| names appear at the right zooms, hubs first, and Names hides them | D2j, read as COMPUTED display rather than as the attribute |
| no subway mark is a circle | D2k |
| MR1's chrome and the status line are where MR1 left them | D2l |
| focusing actually repaints the canvas, not just the options | D2m, on the overlay canvas's alpha channel |
| the labels are legible over any tile, in both themes, and out of the reading order | `a11y.spec.js` A1z3 |
| the draw order, the focus opacities, the station predicate and the zoom band | `frontend/subway.test.js`, 13 tests |

## Mutations, each run and recorded

Each on a fresh copy of the tree, the mutation applied alone, the named tier run against it.

| # | Guard reverted | Result | Killed by |
| --- | --- | --- | --- |
| M1 | the yellow trunk not drawn last | **killed** | node `MR2: the yellow trunk is drawn last`, and `subway.spec.js` D2g |
| M2 | focus rebuilding the ribbon layers instead of changing opacity | **killed** | D2d, on layer identity |
| M3 | the freshness contract's dimming lost under the new icon | **killed** | D2e, and `smoke.spec.js` C2b, C2c, C2h, C2m |
| M4 | the labels not gated by zoom | **killed** | D2j |
| M5 | the transfer ring drawn for single-route stations | **killed** | node, three tests, and D2i and D2j |
| M6 | a circular bullet | **killed** | D2k |

**M1 found a real defect in its own test on the first run**, which is what a mutation run
is for: the draw-order fixture APPENDED the yellow routes, so the payload already arrived
in draw order and D2g passed with `trunkDrawOrder` reduced to `return [...routeIds]`. The
fixture now prepends them, and the comment records why a draw-order test over a payload
that happens to arrive in draw order is not a test.

**The node tier killed two of the six on its own**, two more than MR1 managed, and the
reason is the shape of this stage rather than better testing: MR2's decisions are
arithmetic over data the page already has, and MR1's were wiring.

## Gates, after each commit

Ruff, `ruff format --check`, mypy (30 source files) and the contract-tier lint were clean
at every commit; **the backend is untouched by this branch**, so its 1718 is the same suite
on the same code. Zero em-dashes on every added line and in every commit message.

| Commit | backend | node | e2e (hermetic) | browser contract | API contract |
| --- | --- | --- | --- | --- | --- |
| `cd8ad77` pins | 1718 | 287 | 242 | 4 | 38 |
| `ecb67da` subway | 1718 | 300 | 255 | 4 | 38 |
| (tip) the round | 1718 | 300 | 256 | 4 | 38 |

- **The node count grows once**, 287 to 300: thirteen tests for the pure half.
- **The e2e count grows three times**: 239 to 242 with the three new pins, 242 to 255 with
  MR2's twelve claims plus `A1z3`, and 255 to 256 with D2m, which the screenshot capture
  found was missing.
- **The browser contract tier is the C6 dimming series**, C6e1 through C6e4, green at every
  point and byte-unchanged: MR2 changed what a subway marker looks like and nothing about
  when it dims.

## Adjacent, noted and not fixed

- **The Key panel's three subway glyphs follow the marks they describe, in place.** No row
  was added, no accessible name changed, and the station row now draws both marks in one
  glyph rather than claiming the map has only one kind of subway station. That keeps
  `A1x`'s row count and `P1e`'s list of names both true without either being edited. The
  glyphs are literals rather than tokens, because `.legend-row svg` sits on `--glyph-plate`,
  which is `#f3f2f2` in both themes by MR1 round 2's ruling and does not move with a theme.
- **The design's "ALL LINES" text button is not shipped.** The stage's scope names two ways
  to clear a focus, pressing the bullet again and Escape, and both are implemented and
  tested. A third control is a widening rather than a completion.
- **Route focus is unreachable below 700px until the Key is opened**, because the subway key
  folds behind it there and round 3 closed the carve-out list at two. That is MR1's fold,
  not MR2's, and it is stated rather than worked around.
- **`IMPLEMENTATION.md` puts `data-zoom` and `data-labels` on a `#map` wrapper.** MR1 put
  `data-theme` on `<html>` with `:root[...]` token blocks, so following the file would have
  meant selectors that never matched. MR2 follows MR1.

---
_Generated by [Claude Code](https://claude.ai/code)_
