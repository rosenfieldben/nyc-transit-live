# MR2: subway

Stage 2 of 5 of Phase MR, against the v3.1 handoff at
[`docs/design/map-redesign/`](https://github.com/rosenfieldben/nyc-transit-live/tree/claude/mr2-subway/docs/design/map-redesign)
as amended by the ledger's rulings. Trunk ribbons, the bullet train marker, dot-or-ring
stations, haloed station names with their zoom gate and a Names toggle, and route focus
wired to the bullets MR1 drew. And, on the operator's instruction after round 2, the key
those bullets live in is now **derived from the loaded route list** rather than written
down, every drawn polyline carries the **set** of routes that ride it, focus is membership
in that set, and the key is an **ARIA toolbar with one tab stop**. **Round 3 adds**, on four
more rulings after a multi-agent adversarial review: the ribbons on their own pane below every
other family's lines, the station labels on a pane below every vehicle, transfer counted by
**trunk** rather than by route id, and an off-focus marker out of the accessibility tree and out
of the click path while a route is focused. **No NJ Transit mint was
spent**: nothing in this branch touches the backend, `njt_auth.py` or any credentialed path,
and the contract tier aims every NJ Transit seam at its simulator as it always has.

| Commit | |
| --- | --- |
| `cd8ad77` | pins: what the subway restyle is not allowed to change on its way past |
| `ecb67da` | the subway, drawn as ribbons, bullets, dots and rings |
| `8e8e3b5` | the round, the measurements and the before-and-after pair |
| `98eaa4e` | round 2: an adversarial pass over the written diff |
| `36b1a4e` | round 2 continued: the key derived from the data, and a toolbar to reach it |
| `3d56e94` | round 3: the review's 17 findings, and four operator rulings |
| `418750c` | round 3: two specs the mutation run said were missing |
| `db047fe` | round 3: three more specs the mutation run said were missing |
| `0936e50` | round 3: the ledger and this body |
| (tip) | round 3: the after captures regenerated, and D2x asks the rider's question |

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

**And the key is a function of the data rather than a table beside it.** MR1 wrote down ten
trunks and twenty-three bullets, and measured against the real archive that table and the
network disagreed in both directions. So `subwayKeyModel(routes, trainRoutes)` returns the
whole key and the DOM builder has no decisions left to make: the universe of bullets, their
grouping, each one's focus set, its enabled state and its title all come from one place
where a node test can ask them directly. A ribbon's tag is the same arithmetic from the
other end (`ribbonRouteSet`), which is what lets a route with no geometry of its own still
be reachable: pressing Z lights the polylines J is drawn as, because those polylines know
they carry Z.

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
rather than the diff. Three findings, two fixed here and the third fixed on the operator's
instruction in "Round 2, continued" two sections below.

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
`aria-hidden` after a feed toggle; and a label overlaps a train bullet in a 2x3 pixel corner
and no more.

**Two of those "clean" findings were wrong and round 3 found both.** The last one is the
label overlap: measured as a box against the anchor rather than as painted text, it was 44%
of a bullet's pixels and its route letter (F2 below). And "the pressed ring's backdrop
really is `--surface`, so the 13.70 and 5.53 are measured against the right colour" was true
of the ring's top and bottom edges only; its left and right sat on the neighbouring bullet's
fill at 1.10 (F3 and F10 below). Both are left standing here, struck through by the sections
that follow, because a review record edited to look consistent is worth nothing.

## Round 2 produced an incident, and the incident produced a rule

The multi-agent review was given the repository's own working tree, and its verifiers test a
finding the way this project does: by applying the mutation and seeing whether a spec dies.
Two of those mutations were applied to `frontend/systems/subway.js` and `frontend/map.js`
WHILE the gates for that round were running against the same files, and one reached a
commit. `dda884b` as first written carried M3's own sabotage, `subwayFocusBase` stripped
from two of the four dimming call sites, so its gate numbers described a tree that was never
the one committed. Both files were restored to `ecb67da`'s versions byte for byte, a stray
probe spec the run left behind was removed, every tier was re-run, and the commit was
amended before it went anywhere. **Nothing was pushed at any point.**

**The lesson is the mechanism, not the mutation**, and it is now the standing rule rather
than the next stage's plan. `.claude/workflows/README.md` is new and states it for every
review and probe workflow in this repository, with this incident as the evidence;
`.claude/workflows/adversarial-review.js` carries it as `RULE 0` and passes
`isolation: "worktree"` at all four of its `agent()` spawn sites, so a worktree is not
something a caller has to remember. The caller's half is written there too: **before any
commit, confirm the working tree is what the gates ran on.** Every commit after the incident
did that, and the mutation run below uses a real `git worktree` detached at the commit under
test rather than the tree the gates had just passed.

## Round 2, continued: the operator's two remaining instructions, taken

**G3 / F3. The key is derived from the data, and ribbons are tagged by route.** `helpers.js`
takes the loaded route list and the trains on the map and returns the whole model: the
bullet universe (alias-collapsed, so `S` stands for `GS`, `FS` and `H`), each bullet's focus
set, whether a press would light anything, and the sentence its title carries. `subway.js`
tags every drawn polyline with the SET of routes that ride it, which is the route itself
plus every trunk-mate the app has no geometry for, so the Jamaica Avenue ribbon is `J+Z` and
the three yellow ribbons become `N+W`, `Q+W` and `R+W` the moment a W train appears. Over
the real archive the key comes out as the 22 bullets `1 2 3 4 5 6 7 A C E B D F M G J L N Q
R S SI`: the 24 drawn routes with the three shuttle ids collapsed into one. A bullet whose
press would light nothing is drawn with `aria-disabled` and a title saying why, rather than
`disabled`, because a disabled button leaves the accessibility tree and a rider who cannot
see the key would never learn the route exists. `D2n` (Z), `D2o` (W), `D2p` (S and SI) and
`D2q` (the dark bullet) hold the four cases.

**And membership is asymmetric, which the new specs had to teach me.** The first version
focused the transitive CLOSURE of the ribbons a bullet touches. That is right for Z, where
the J/Z ribbon is the only thing carrying Z, and wrong for N: N's ribbon carries W because
the app has no W shape, W's closure reaches Q and R for the same reason, so pressing N lit
the whole Broadway trunk when a rider asked for one route of it. `D2o` failed on its first
run and was right to. A RIBBON lights when its own route set contains one of the bullet's
ids, and the bullet's ids never grow; the closure survives as `bulletTrackSet`, which is
only what the "shares track with" half of a title is written from. So pressing J and
pressing Z light the same ribbon and different trains, because a train carries its own
`route_id` where a shared ribbon cannot, and `D2n` asserts exactly that. **M8 is its
mutation.**

**F4. The key is an ARIA toolbar with a roving tabindex.** `role="toolbar"`, exactly one
bullet at `tabIndex 0` (the pressed one if there is one, else the first enabled one),
ArrowRight/Left/Up/Down wrapping both ways, Home and End, and disabled bullets skipped. The
keydown is scoped to the key and named in `frontend/keyboard.test.js` with its reason, which
is that test's existing seam for a control's own activation as opposed to a second
page-level router.

**Measured after, on the same harness and the same real 24-route list as the 32: 10 presses
from the top of the document to the Stations button, one of them a bullet.** The arithmetic
is the claim itself, 32 less 23 bullets plus 1 stop is 10, and the nine presses this stage
started from are that 10 less the key. `D2r` holds the property the number follows from,
which is that the key's cost does not grow with the route list.

**One more finding, from writing `D2q`.** Playwright's actionability check treats
`aria-disabled="true"` as not enabled and refuses to click it, which is not what a browser
does: the element is a live `<button>` with no `disabled` attribute and a real press lands
on it. A spec that accepted the refusal would be asserting the test runner's opinion instead
of the page's behaviour, and would pass with the guard removed. `D2q` forces the click, with
the reason written at the call, and **M7** is the mutation that deletes the guard it meets.

## Round 3: a multi-agent adversarial review, and what it found in my own record

The review round 2 started ran to completion in its own worktree and returned **17 confirmed
findings, five of them high**: fourteen agents, 21 candidates, six solo verifiers and three
batched. Two of them contradicted claims this branch had already made, and both corrections are
in the ledger next to the fixes rather than quietly edited out.

**F2. The station name labels painted over the train bullets.** A permanent Leaflet tooltip
defaults to `tooltipPane` at z-index 650; the vehicles are in `markerPane` at 600. Re-measured
from this branch's own committed screenshots, same commit and clock, differing only by
`data-labels`: one bullet lost **44% of its route-coloured pixels** and its letter with them.
**Round 2's pass called this "a 2x3 pixel corner and no more"**, because it measured the label's
box against the anchor rather than its painted text. A defect walked past a review that was
looking straight at it. The labels now have a pane at 460, above the dot a name may cover
(its own station) and below every vehicle it may not.

**F3 and F10. Both bullet rings were measured against the wrong backdrop, and round 2's fix is
why.** Both reach 4px out from a 24px chip while the group gap was the design's 2px, so each
ring's left and right segments sat inside the neighbour's border box, and the `z-index: 1` added
in round 2 for the clipping is exactly what makes them paint there. Against a neighbour's fill
`--focus` reads **1.10** on the A/C/E blue, which is the exact number MR1 round 2 recorded as
the defect an outside ring was chosen to escape. Fixed with one number, a **6px** gap, verified
at the pixel level in both themes and both states: surface, ring, surface, on both sides.

**F4, on the operator's ruling.** `FOCUS_DIM_TRAIN` 0.15 sits below `STALE_MARKER_OPACITY` 0.45,
so a live off-focus train drew dimmer than a ten-minute-stale on-focus one and an off-focus
stale train landed at **0.0675** while staying clickable and announced. Both numbers stay and
`markerOpacity` is untouched; what changes is reach. An off-focus marker is `aria-hidden` and
takes no pointer events, and clearing focus restores both.

**F9, on the operator's ruling.** `isTransferStation` counts **trunks**, not route ids, so the
J/Z skip-stop pairs and the A/C and 4/5 local-express pairs are locals. They were drawn as
interchanges and given the `hub` class the zoom-12 band exists to keep sparse. `P1f`'s station
pin moves with it: six lines, both stations, ring to dot, nothing else in any pin.

**F6, on the operator's ruling.** The 6.5px casings shared one canvas with every other family's
lines, Leaflet draws in insertion order, and the eleven static loaders race, so whether PATH's
33rd St line survived depended on which response arrived first. The ribbons have their own pane
below the shared one now, the whole pane order is documented where the panes are created, and
`D2u` pins it with four families drawn in shuffled insertion order.

**F1, split on the operator's ruling.** `stop_times.txt` is not a required member of the subway
static archive, so the endpoint can serve `routes: []` for all 496 stations while the status
stays `ready`, and then nothing renders at the opening zoom or the City preset while the Names
button reads pressed. The band's arithmetic is not the bug. This branch keeps the frontend's
half: with no hub anywhere, every name from zoom **13**. **The backend half is its own branch
after this merges**, with its three consumers named in the ledger: the transfer ring, the hub
label class, and the station alerts matcher.

**F7, F17, F15, F14** are fixed and measured: the Names toggle announces and explains itself;
the disabled bullet's chip carries the state while its letter carries the route (5.33 light,
8.03 dark, up from as low as 2.17); A4c asserts the halo's clearance as a number instead of
probing a band it had zero slack in; and the tooltip draws at the opacity `A1z3` measures it at
rather than Leaflet's inline 0.9.

**F5, F11, F12 and F13 were four guards and assertions measuring nothing**, each confirmed by a
mutation the whole suite survived. `D2s`, `D2t`, `D2h` and `D2q` now measure them.

**F8 is partly refuted and F16 is not fixed.** In the vendored Leaflet a circleMarker's hit
radius always equals its drawn radius, so no invariant was dropped and the proposed remedy
would have silently grown three other families' station hit radii. And naming the ring in the
Key panel breaks either `A1x`'s pinned row count or `P1e`'s rule that no stage takes a sentence
away from that panel: I tried both and put both back, so it belongs to the stage that owns the
Key panel.

### The sharpest thing this stage learned

**Three of round 3's own fixes survived their own mutation on the first run**, and a fourth was
caught by the node tier alone. The label pane, the 6px gap and the disabled chip's drawn
treatment were each verified by hand, measured, and commented, and none of them had a test that
would have noticed being reverted. `D2x`, `D2y`, `D2z` and `D2z1` exist because the mutation run
said so. **A fix is not finished when it works; it is finished when reverting it fails
something.**

And writing those specs found two more defects in round 3's own work: the Names toggle's tooltip
was keyed on the hub count, so it appeared over any hubless network and claimed no station
listed its routes above a map where every station did; and a `typeof stationRegistry` guard took
the whole page down, because `typeof` on a module-scope `const` in its temporal dead zone throws
rather than answering "undefined".

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
three. **Fixed on the operator's instruction**, one section up: the table is gone entirely
and the key is derived.

**F4. The key costs twenty-three tab stops.** Measured from the top of the document,
reaching the Stations button now takes **32 presses**, where before this stage it took
nine. The skip link is still the first stop, so a rider heading for the station panel is
unaffected; a rider heading for the Key or Stations button walks the whole key. The
standard remedy is a roving tabindex (`role="toolbar"`, one tab stop, arrow keys inside),
about twenty-five lines, and `frontend/keyboard.test.js` already has the seam for it. It is
a new interaction mechanism rather than one of this stage's five items. **Fixed on the
operator's instruction**, one section up: **10 presses** after, one of them a bullet.

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
| the key is derived from the loaded route list, grouped by trunk, every bullet a named button whose text is still the bare route id | `subway.spec.js` D2a, which resolves its own expectation through `subwayRouteUniverse(subwayRouteList(), subwayTrainRoutes())` rather than writing the list down |
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
| Z lights the ribbon J is drawn as, and its own train, and pressing J lights the same ribbon and a different train | D2n |
| a W train lights all three yellow ribbons, and pressing N lights N alone | D2o, which is the spec that found the closure defect |
| one S bullet is three shuttles with a tooltip that says so, and SI has a bullet | D2p |
| a bullet that would light nothing is present, `aria-disabled`, explains itself, is skipped by the arrow keys, and cannot dim the map | D2q |
| the key is one tab stop, and the arrows, Home and End move inside it | D2r, including that the single stop follows the pressed bullet |
| Z lights the ribbon J is drawn as, and its own train | D2n |
| an off-focus train leaves the reading order and the click path, and comes back | D2v, including a real click at its centre |
| and stays out across a poll, and a train arriving mid-focus is out at once | D2w |
| a focused bullet disappearing from the key takes the focus with it | D2s |
| the key does not rebuild under a rider's hand every fifteen seconds, and does rebuild when the bullets really change | D2t |
| the ribbons draw under every other family's lines, in any arrival order | D2u, four families in shuffled insertion order |
| a station name is in the pane below the vehicles, at full opacity | D2x |
| both rings have surface on every side, by geometry and by pixels | D2y, in both themes |
| a hubless network shows every name from 13, not nothing from 12 | D2z |
| and the toggle says why when no station lists its routes at all | D2z1 |
| the draw order, the focus opacities, the station predicate, the zoom band, the derived key, the membership rule, the trunk count and the no-hub band | `frontend/subway.test.js`, 25 tests |

## Mutations, each run and recorded

Each in a real `git worktree` detached at the commit under test, the mutation applied alone,
the named tier run against it, and `git checkout -- .` between runs; the main tree was
verified clean before and after the whole run. That is the incident's lesson applied to my
own probes and not only to the review agents'.

| # | Guard reverted | Result | Killed by |
| --- | --- | --- | --- |
| M1 | the yellow trunk not drawn last | **killed** | node `MR2: the yellow trunk is drawn last`, and `subway.spec.js` D2g |
| M2 | focus rebuilding the ribbon layers instead of changing opacity | **killed** | D2d, on layer identity |
| M3 | the freshness contract's dimming lost under the new icon | **killed** | D2e, and `smoke.spec.js` C2b, C2c, C2h, C2m |
| M4 | the labels not gated by zoom | **killed** | D2j |
| M5 | the transfer ring drawn for single-route stations | **killed** | node, three tests, and D2i and D2j |
| M6 | a circular bullet | **killed** | D2k, and pins P1f |
| M7 | a bullet with an empty set dims the map | **killed** | D2q: every ribbon and every train compared before and after the press, and `#page-announce` required to have said nothing |
| M8 | focus is the transitive closure again, which is the defect `D2o` found | **killed** | node `MR2 G3: the whole key is grouped by trunk`, and D2n and D2o |
| M9 | the labels back to Leaflet's `tooltipPane`, above the vehicles | **killed, second time** | D2x. Survived the first run. |
| M10 | the group gap back to the design's 2px | **killed, second time** | D2y. Survived the first run. |
| M11 | the ribbons back on the shared canvas | **killed** | D2u, and D2m |
| M12 | an off-focus marker stays in the tree and keeps taking clicks | **killed** | D2v and D2w |
| M13 | the station predicate counts route ids again | **killed** | node `MR2 F9`, D2i, D2j, pin P1f |
| M14 | with no hub the band shows nothing instead of everything from 13 | **killed** | node `MR2 F1`, D2z, D2z1. Node tier only on the first run. |
| M15 | the rebuild stops clearing a focus whose bullet has gone | **killed** | D2s |
| M16 | the key rebuilds every poll | **killed** | D2t |
| M17 | the token resolvers can only return their literal fallback | **killed** | D2h |
| M18 | the tabindex and arrows stop skipping disabled bullets | **killed** | D2q |
| M19 | the disabled chip keeps its inline route colour | **killed, second time** | D2q. Survived the first run. |
| M20 | the Names toggle stops announcing | **killed** | D2j |
| M21 | the tooltip back to Leaflet's 0.9 group alpha | **killed** | D2x |

**M1 found a real defect in its own test on the first run**, which is what a mutation run
is for: the draw-order fixture APPENDED the yellow routes, so the payload already arrived
in draw order and D2g passed with `trunkDrawOrder` reduced to `return [...routeIds]`. The
fixture now prepends them, and the comment records why a draw-order test over a payload
that happens to arrive in draw order is not a test.

**Twenty-one mutations, all killed, five of them by the node tier alone**: the draw order, the
station predicate, the zoom band, the no-hub fallback and the key's grouping. Five more than MR1
managed, and the reason is the shape of this stage rather than better testing: MR2's decisions
are arithmetic over data the page already has. The sixteen that only the browser kills are all
wiring, including which pane a tooltip is bound to and what a canvas actually painted.

**And three of round 3's own fixes survived their first mutation**, with a fourth caught only by
the node tier. Each had been verified by hand, measured and commented. `D2x`, `D2y`, `D2z` and
`D2z1` exist because of that.

**One failure in the run was not the mutation's**, recorded because a mutation table that
launders a flake is worse than one with a gap in it. Under M6, a batch running `pins.spec.js`
and `subway.spec.js` together on two workers also failed P1m, the ferry pin, which has
nothing to do with a subway icon. It did not reproduce: `pins.spec.js` alone with M6 applied
fails P1f and nothing else. It is local server contention, and the `pin()` helper cannot be
the cause because in assert mode it only reads the golden.

## Gates, after each commit

Ruff, `ruff format --check`, mypy (30 source files) and the contract-tier lint were clean
at every commit; **the backend is untouched by this branch**, so its 1718 is the same suite
on the same code. Zero em-dashes on every added line and in every commit message.

| Commit | backend | node | e2e (hermetic) | browser contract | API contract |
| --- | --- | --- | --- | --- | --- |
| `cd8ad77` pins | 1718 | 287 | 242 | 4 | 38 |
| `ecb67da` subway | 1718 | 300 | 255 | 4 | 38 |
| `8e8e3b5` the round and the pair | 1718 | 300 | 256 | 4 | 38 |
| `98eaa4e` round 2 | 1718 | 300 | 256 | 4 | 38 |
| `36b1a4e` round 2 continued | 1718 | 309 | 261 | 4 | 38 |
| (tip) round 3 | 1718 | 312 | 270 | 4 | 38 |

- **The node count grows twice**, 287 to 300 with thirteen tests for the pure half, and 300
  to 309 with nine more for the derived key, the membership rule and the corrected asymmetry.
- **The e2e count grows five times**: 239 to 242 with the three new pins, 242 to 255 with
  MR2's twelve claims plus `A1z3`, 255 to 256 with D2m, which the screenshot capture found
  was missing, 256 to 261 with D2n through D2r, and 261 to 270 with D2s through D2z1.
- **The node count grows three times**, the last being three tests for the trunk count, the
  no-hub band and the Names toggle's two sentences.
- **No NJ Transit mint was spent at any point.** `NJT_USERNAME` and `NJT_PASSWORD` are unset
  in this environment and `njt_auth` reads a missing field as "not configured" rather than
  posting a doomed mint, so the contract tier's 503s are the unauthenticated path failing
  upstream, which costs nothing against the ten-a-day cap.
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
🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_017oZP4Mtd6BLoSvAeVaPrJz
