# Phase MR, stage 4 of 5: the other four families, and the dark theme's release

Fourteen commits off `origin/main` (`db73f05`): the pins, the four families' marks as pure
arithmetic, the wiring, the canvas theme registry, ruling **R2 met** (`#theme-toggle` loses its
`hidden` attribute and the dark theme is a thing a rider can choose), the round entries, the
captures, round 1 of the adversarial pass, and **round 2, which is the two rulings that came
back: Q1a in the accessibility statement and Q9 as the Key panel round**. **Frontend only**: no
backend file changes, so nothing here deploys a service. No NJ
Transit credential is set in this environment and the contract tier drives a simulator, so **no
mint was spent**. No em-dashes on added lines.

## What the four families drew before, and what they draw now

| | before | after |
| --- | --- | --- |
| PATH train | a 16x16 diamond, `M8 1.5 L14.5 8 L8 14.5 L1.5 8 Z`, `fill=#d93a30 stroke="#fff" stroke-width=1.5` | the design's diamond, `M8 1 L15 8 L8 15 L1 8 Z` at `stroke-width 1.2`, both paints in an inline STYLE so the stroke can be `var(--paper)`; same 16x16 box, same `[8, 20]` lift |
| PATH line | one polyline per shape, `weight 2.5 opacity 0.5` | `weight 3.5 opacity 1`, round caps and joins stated rather than inherited, no casing (the one family the design gives none) |
| PATH station | a canvas `circleMarker` `radius 4 color #fff weight 1.5 fillColor #3d5a80`: an INVERTED fill, chosen so PATH would not be mistaken for a subway station where the two coincide | the subway's LOCAL dot through `stationMarkStyle([], ink, paper)`: `radius 3.5 fillColor var(--ink) stroke false`, and in the canvas theme registry because that fill is a token |
| ferry boat | a rounded rectangle, `<rect x=1 y=3 width=20 height=8 rx=4 stroke="#fff" stroke-width=1.5>` | a hull, `M1 3 H21 L17.5 11 H4.5 Z` at `stroke-width 1`: a flat deck wider than the keel, which is what the file's own comment had argued for since it was written |
| ferry route | `weight 2.5 opacity 0.5`, solid | `weight 2 opacity 0.9 dashArray "6 5"`: the dash says the service crosses water on no fixed way, which is the one thing a solid line of any colour cannot |
| ferry dock | `radius 4.5 color #fff weight 1.5 fillColor #0e7490` | `radius 4 color <paper> weight 1.5 fillColor #00839c`, and a permanent name label in `stn-label ferry`. The cyan settles a disagreement the app already had: the feed strip's ferry tick has been `#00839c` since MR1 while the Key's dock glyph was `#0e7490` |
| AirTrain station | a 14x14 magenta square, `rx=2 fill=#fff stroke=#b5179e stroke-width=2.5`, class `airtrain-marker` | the commuter square, byte for byte the one the three rail families draw, class `rail-stn-marker rail-airtrain-stn` |
| AirTrain guideway | `#b5179e`, `weight 3 opacity 0.85`, solid | `var(--scheduled)` resolved at draw (`#6d6e71` light, `#9a9a9a` dark), `weight 3`, `dashArray "8 5"`, and in the theme registry because the colour is the app's rather than a feed's |
| bus | a 20x20 box: an arrow at `stroke-width 1.2` or a `circle r 5.5`, filled from `routeColor` at `hsl(h, 75%, 40%)` | a 14x14 box for BOTH states: `M7 1 L12 13 L7 10 L2 13 Z` or a `circle r 3.5`, filled from `busMarkColor`, stroked in `var(--paper)` |

And the census, which is the same table read as counts. `markerIcons` stays 23 because no
family gained or lost a mark.

| | before | after | why |
| --- | --- | --- | --- |
| `vehicleSentinel` | 18 | 15 | AirTrain's three stations stopped being counted as vehicles |
| `railStationMarkers` | 5 | 8 | the same three, counted as the stations they always were |
| `stnLabels` | 7 | 9 | the ferry's two dock names, which the design asks for and no dock has ever had |
| `stnLabelsSubway` | 2 | 2 | **the one that caught the carry-forward**: read as `:not(.rail)` it was 4 |

## The theme swap, and the debt MR3 named

Six families register a painter; the draw path and the repaint call the same function, so "what
colour is this mark" and "what colour does it become" are one expression each.

| Family | Paints | Must not touch |
| --- | --- | --- |
| subway ribbons | the CASING half's colour, found by `ribbon.part` | the line half's trunk colour, and any opacity |
| subway stations | `stationMarkStyle(routes, ink, paper)` per registry entry | the routes that decide dot or ring |
| **rail casings** | the casing's colour, found by `railroadCasingRenderer` | **every branch line in the same layer groups**, which carry the agencies' published colours |
| PATH stations | `stationMarkStyle([], ink, paper)` | the lines, which take the feed's colour |
| ferry docks | `ferryDockStyle(paper)` | the dock's own cyan, and the routes' feed colours |
| AirTrain lines | `airtrainLineStyle(scheduled)` | nothing else: the squares are divIcons |

**Colour only, never opacity.** Route focus owns every ribbon's opacity and guards on it, so a
swap that passed opacity would silently undo a rider's focus the moment they changed theme. The
two `setStyle` calls in `subway.js` touch disjoint options on purpose.

**Filtered by renderer, not swept over the group.** Each rail family's layer group holds the 5px
casing and the 2.5px branch line together, so a blind sweep would paint every branch line in
paper and erase the agencies' published colours, which are the entire subject of MR3. MR3 put
the casings on their own renderer for the drawing order, and that split is what identifies them.

**setStyle and never a rebuild.** `theme.spec.js` D5b tags every marker element before the swap
and finds the same elements after it, keeps a popup open across it, and compares the canvas
layers' Leaflet ids: a rebuild would close every popup a rider is holding and drop every
marker's accessible name.

Everything else is a divIcon whose theme-dependent paint is `var(--paper)` or `var(--ink)` in an
inline STYLE, so it follows a swap through the cascade at no cost and is deliberately NOT in the
registry. `families.test.js` asserts the registry **against the source** rather than against a
list: it strips comments, compiles what is left to prove the stripper did not eat code, and
requires every file that resolves a token for a canvas mark to register a family.

## G15's arithmetic, re-run on the marks

`tests/e2e/contrast.js` states the definition and both callers share it. The surface is the
theme's own `--paper`, read live off the page: a basemap tile is an IMAGE and can be any colour,
so nothing clears 3:1 against every possible pixel, and what the design promises instead is that
every mark carries a casing or stroke in the theme's paper, which is what MR1's dark tile filter
takes an OSM tile close to. A mark is found by its STRONGEST paint, because a station square's
fill IS the surface and its outline is what a rider sees.

| family | the paint that carries it in dark | on `--paper` | on `--surface` | light, for control |
| --- | --- | --- | --- | --- |
| AirTrain station square | rect stroke `rgb(243, 242, 242)` | 14.86 | 12.60 | 14.86 |
| PATH station dot | fill `rgb(243, 242, 242)` | 14.86 | 12.60 | 14.86 |
| PATH train | path fill `rgb(217, 58, 48)` | 3.64 | 3.08 | 4.09 |
| bus | path fill `rgb(199, 107, 155)` | 4.75 | 4.02 | 6.65 |
| ferry boat | path fill `rgb(0, 131, 156)` | 3.74 | 3.17 | 1.31 |
| ferry dock | fill `rgb(0, 131, 156)` | 3.74 | 3.17 | 3.98 |
| rail station square | rect stroke `rgb(243, 242, 242)` | 14.86 | 12.60 | 14.86 |
| rail tag | rect stroke `rgb(243, 242, 242)` | 14.86 | 12.60 | 14.86 |
| subway station dot | fill `rgb(243, 242, 242)` | 14.86 | 12.60 | 14.86 |
| subway train | text fill `rgb(255, 255, 255)` | 16.60 | 14.07 | 4.87 |

Every family clears 3:1 in dark, and the four this stage drew clear it on their FILL rather than
on a letter printed on them, which is the stronger claim and the one MR4 is answerable for.
`theme.spec.js` D5d asserts both. `pins.spec.js` P4c records every family's paints in BOTH
themes as a golden, because which paint carries a family is exactly what a later stage would
change without noticing. **The two numbers worth reading twice are in that table**: the subway
train is carried by its white letter, not by its route square (`#1f5fbf` reads 2.73 against the
dark paper), and the ferry boat's light-theme 1.31 is the South Brooklyn yellow the feed
publishes. Both are finding Q1, and round 2 has the ruling: the subway train's white letter is
a paint this app chose and clears, the ferry hull's yellow is the feed's and is reported rather
than promised.

**And this table is the round-1 version of itself**, which is worth saying because the first one
was wrong in two rows. The measurement resolved every paint through a probe, CSSOM drops a value
it cannot parse, and `none` (the computed stroke of every shape that sets no stroke) therefore
measured as the probe's inherited black: the subway train and the rail tag were recorded at
18.79 in the light theme on a colour no mark paints. Regenerating the golden and READING IT
found the second half, a `<line>`'s phantom fill. R1 in the round below has the repair.

## Nine findings, all measured

**Q1. The 3:1 floor cannot be met by a fill that is an agency's published colour, at either end
of the theme.** Measured against the theme's own paper: subway A `#1f5fbf` 2.73, J `#7d5a3c`
2.69, 7 `#8e44ad` 2.83, S `#566573` 2.77; of the 19 pairs the MTA railroad feeds publish, Port
Washington `#6E3219` 1.69, MTA blue `#0039A6` 1.69, `#4D5357` 2.13. And the mirror case predates
this phase: the ferry's South Brooklyn `#ffd100` reads 1.31 against the LIGHT paper. A paper
casing does not change this arithmetic, because in either theme the paper IS approximately the
surface. **Not fixed here**, because every remedy is either out of this stage's scope or a design
change, and **a ruling is wanted**: (a) keep the floor defined as it is, on the strongest paint,
and leave published fills alone, which is MR3's N1 principle; (b) apply N1's hue-preserving
scaling to any mark FILL under 3:1 against the current paper, which makes those fills
theme-dependent and reaches MR2's subway square and MR3's rail tag, both out of this stage; (c)
draw the mark PLATE in `--ink` in the dark theme so every published fill keeps its light-theme
arithmetic, at the cost of a bright plate around every mark on a dark map.
**RULED: (a), and answered in round 2**, which is where the table and the one exemption are.
One number above is corrected there: PATH's diamond is 4.09 light and 3.64 dark on the drawn
page, so the ferry hull's 1.31 is the only paint under the floor.

**Q2. One lightness cannot serve both themes for the bus mark, and the README names one.** A bus
route's colour is a hash of its id, so its legibility is a claim about all 360 hues. The README's
`hsl(h, 45%, 38%)` clears 3:1 for every one of them against the light paper (worst 3.16) and
leaves **188 of 360 under** against the dark paper (worst 1.62). No fixed lightness clears both:
60% is perfect in dark (worst 3.60 on `--paper`, 3.05 on `--surface`) and leaves 219 under in
light. **So the lightness is `var(--bus-mark-lightness)` and the hue is still the route's.** A
custom property is substituted before the value is parsed, so one string is a real colour in
either theme and follows a swap with no rebuild. The README's 38% is unchanged: it is what the
token resolves to in light, and it is the var's fallback so a context with no stylesheet still
gets a colour.

**Q3. PATH stations and subway stations are now the same mark**, which is the cost of the ruling
and the design's word. They coincide at 33rd St, WTC and 14th St, and the paragraph this replaced
argued for the inverted slate-blue fill on exactly those grounds. A PATH station's identity is
still reachable everywhere except the glyph, and the dot's shrink (radius 4 + weight 1.5 to
radius 3.5, no stroke) stays inside an exception ACCESSIBILITY.md already carries for
canvas-drawn station dots.

**Q4. "A square always means regional rail" becomes "regional rail or AirTrain."** Jamaica has an
LIRR station and an AirTrain station and they are now the same square. `rail.spec.js` D3c is
rewritten around the wider sentence, asserts the four square families and the three circle
families, asserts that the set of station kinds on the page is exactly the seven it knows, and
measures the Jamaica collision itself.

**Q5. Section 3.3's "absent when withheld" cannot be built for PATH, the ferry or the buses.**
Only `backend/feeds/railroad.py` implements the withholding ladder, so those three have no
withheld state to draw. Not attempted here.

**Q6. Scoping the subway's label band to its own class raised it above the Names toggle.**
`:root[data-label-band="all"] .stn-label.subway` is (0,4,0) and the Names-off rule was (0,3,0),
so Names off stopped hiding subway names. D2j caught it. **Repaired structurally**: every band
rule states its family inside `:where()`, which selects without adding specificity, so all of
them are (0,3,0) and ONE Names-off rule at the end of the section reaches every family by source
order. The three per-family off rules MR2 and MR3 accumulated are gone.

**Q7. Two dead CSS selectors, found by measuring.** `.njt-marker` and `.njt-station-marker` have
matched nothing since MR3 gave NJ Transit the rail tag and the commuter square. Deleted.

**Q8. A claim this codebase repeats in four places is false, and its own ledger said so.** Three
source comments and two specs asserted that `fill="var(--paper)"` as an SVG presentation
attribute "is not a paint value and does not resolve". **Measured**: it computes to
`rgb(243, 242, 242)`, byte for byte what the style form computes to, because a presentation
attribute is mapped into the cascade as a declaration. An unknown token is where black comes
from, in either form. MR2's finding H2 had it right and the sentence got stronger every time it
was copied. Every site is corrected and the house rule is kept on its real grounds: a
presentation attribute is the lowest-priority author declaration there is, so any stylesheet rule
beats the mark's own paint silently. **This is also why mutation M30 is recorded as surviving
every browser gate.**

**Q9. Seven of the Key panel's eighteen rows describe marks that no longer exist, and they have
since MR3.** That stage gave the three rail families one grammar (the tag, the commuter square, a
casing under the agency's colour) and updated none of the legend's rail rows: rows 6 and 7 drew
an LIRR/Metro-North train as a 16x16 purple rounded square, row 8 its route line as a flat
`#7b1fa2` line, row 9 its station as a white ring stroked `#334155`, and rows 15 to 17 did the
same three things for NJ Transit. It wanted a ruling because the repo's own principle cuts the
other way: G15's disposition says "a key whose glyphs did not match the map would be worse than a
dim one", which is why MR2 updated the subway's rows and why this stage updated the four rows for
the families it redrew. **The ruling granted the round, and round 2 is it.** See "Round 2" below.

## Round 2: the Key panel, and Q1 in the statement

Two rulings on the round 1 findings that were referred up.

**Q1a: fills stay the agency's published colours, and each family's identifying paint is named
and measured.** `theme.spec.js D5d` now carries a table naming the carrying paint per family per
theme and whether the APP chose that colour or an AGENCY published it. Where the app chose it, the
spec requires 3:1. Where an agency published it, the value is read and reported. `ACCESSIBILITY.md`
states both halves. **One paint is under the floor and it is named rather than smoothed:** the
ferry boat's hull at **1.31** in the light theme, which is the colour NYC Ferry publishes for its
South Brooklyn route; its only other paint is the paper casing every mark carries, which cannot
raise a fill's ratio against paper. The exemption is asserted BY MEASUREMENT, so the day a stage
gives the hull an ink edge, D5d fails and the paragraph can promise the floor for all ten families.
Round 1's report had PATH's diamond at 2.76; that was a route colour this app serves but does not
draw, and on the drawn page it is **4.09** light and **3.64** dark, so PATH clears in both.

**Q9: the Key panel is now a picture of the map.** Three regional rail station rows become the one
commuter square `railStationSvg` draws, named for all four families. Three commuter train rows
become the two tag bodies `railTagSvg` draws. The subway station row splits so the transfer ring
gets the name F16 asked for. **Sixteen rows and one note**, from eighteen and one.

Two rows are beyond the ruling's three named items and are flagged as such: the LIRR / Metro-North
and NJ Transit **route line** glyphs, which drew a flat line at an opacity the map has never used
for a rail line. Same defect, same panel, same round; neither costs a sentence.

**"At legend scale" was the one real design decision.** The rail tag is the only glyph in this
panel that carries type, and type does not survive scaling: in the shared 16px cell the tag's 8px
Archivo renders at **3.66px**. Widening the cell buys almost nothing while the viewBox is 30 tall,
because it is then height-constrained by the air the stem and head hang in. So the viewBox is
cropped to the tag and the CELL adapts to the mark, at a scale of exactly **1.00** and type at the
map's own 8px. No stem and no head: those captions name the body axis, and a glyph drawing a mark
its caption never explains is F16 again. That wants a row of its own and is recorded as **F17**.

**All three row counts moved rather than being relaxed, including one the ruling did not name.**
`a11y.spec.js A1x` 19 to 17, `pins.spec.js P1e` from a superset to an ordered equality, and
`subway.spec.js D2l` 18 to 16, which nobody had noticed was a third independent count on this
panel. P1e's superset is the reason Q9 could sit for two stages: "additions only" made replacing a
stale row the one thing a stage could not do.

**Two new guards, for claims nothing on this repo could see.** `frontend/keyglyphs.test.js`
compares every Key glyph to the mark's own source, so the panel drifting from the map fails on the
node tier rather than waiting for a reviewer. `a11y.spec.js A1x2` measures the size the tag's type
is drawn at: deleting one CSS declaration scales it to 3.05px and every other gate stays green
(**F18**). A ninth undecidable shape was added for axe declining the tag's type as "overlapped by
another element", with `A1z` as its decider, measured at 14.86, 4.69, 14.86 and 14.86.

**Forty-three mutations, forty-two dead.** Seventeen new, one per changed row and one per guard
this round moved, and the stage's earlier twenty-six re-run at the same tip. M47 survives exactly
as it was recorded to. **One of the twenty-six had stopped running** and only the whole-table
re-run found it (**F19**): M35's anchor went stale when round 1 turned the line it targets into a
block, so it had been reporting `ANCHOR MISS` rather than testing the guard it names. Re-anchored,
it dies on `P4c`.

**The captures did not need regenerating and that was measured, not assumed:** the six frames are
taken with the Key closed, and the tree before this round regenerates them identically. Since a
reviewer of a Key round should be able to see the Key, **four new frames open it** (desktop and
375, both themes), with a before pair beside them.

## Round 1: the adversarial pass, and what it found in this stage's own tests

Five finder dimensions over the production diff, each in a worktree detached at the commit,
pointed at the four shapes this phase's defects have taken. **Ten findings: nine repaired, one
for a ruling (Q9 above).** Three of the nine were the third shape, a test that cannot fail, in
the tests this stage wrote to catch the others.

| | What was wrong | The repair |
| --- | --- | --- |
| **the measurement measured paints that do not exist** | `contrast.js` resolves a paint through a probe's `color`, and CSSOM DROPS a value it cannot parse. `none` is the computed stroke of every shape that sets no stroke, so it left the probe's INHERITED colour to be measured as the mark's: P4c recorded the subway train and the rail tag in the light theme at **18.79 on `rgb(0, 0, 0)`**, and no mark on this map paints black. D5d's floor is a MAXIMUM over a mark's paints, so a phantom at 18.79 would have carried any mark past it. | `CSS.supports("color", value)`, which asks the browser what it will take as a colour. And regenerating the golden and READING IT found the second half: a `<line>` has no area, so it paints its stroke and nothing else, but its computed FILL is the initial black, which the first guard admits. Paints are enumerated per element kind now. |
| **three tests that could not fail** | D5b's no-rebuild probe had `railCasing: null` on both sides of its identity comparison, so the one family it names as its mutation target was the one it asserted nothing about. D4e computed the vehicle sentinel (the number six specs share, which this stage moved from 18 to 15) and never asked about it. The registry-coverage scrape was file-granular, so `subway.js`'s two draw sites passed with either registration deleted. | The casing ids are collected and every id in the probe is asserted non-null; the vehicle bucket is asserted by count AND by the set of classes in it; and the scrape counts DRAW SITES per file, where a site is a maximal run of consecutive resolver lines, which merges one draw's several tokens and separates two draws. |
| **the ferry's dock names rode the subway's degraded band** | `data-label-band` reads "all" from zoom 13 when no subway station lists a route, so every dock name came on a zoom early against the design's "names from 14", for a reason that has nothing to do with the ferry. | `data-ferry-label-band`, which is MR3's own sentence about the rail names applied again: the two bands overlap and one attribute cannot hold two answers. |
| **the band's sentinel lost the "actually painted" half** | Moving off the DOM meant a subway a rider pressed OFF read as "stations exist, zero hubs", the DEGRADED band. | `map.hasLayer`, plus the half the finding did not name: the band was only recomputed on `zoomend`, so `applyFeedVisibility` repaints it. **And measuring it corrected the review's reason**: the finding said a tooltip on a removed layer is not in the document, and it is, so the DOM count had the same defect and this one is older than the diff. D2z2 asserts the measurement as well as the behaviour. |
| **a swallowed paint error, and a comment that promised a recovery** | `repaintCanvasFamilies` caught every error with no signal, and said "the next draw reads the live token anyway". After load there is no next draw: every loader draws once per page. | It records into `canvasThemeFailures`, which D5b asserts is empty; the comment says what is true; the catch stays, because one family may not take the swap down with it. A rider is still told nothing, because there is no action for them in it. |
| **"colour only, never opacity" ran over two of six entries** | And they are exactly the two that keep it. | The rule is restated per family, because an entry may not write an opacity that something ELSE owns, and the six are asserted to be all of them, so a seventh has to declare its side. |
| **the Key pictured a bus in a colour no bus can be** | The three bus glyphs took the design's box, path and stroke in this stage and kept `#1d4ed8`, which is the unmuted family Q2 replaced, in a row captioned "Color indicates route". | `#354d8d`, the same hue at the muted saturation and the light theme's lightness, which is the right end for a plate that is light in both themes by ruling H3. 7.24 on that plate, against the 6.00 it replaces. |

**Two findings came back from two different lenses each** with the same file and line (the null
identity and the opacity guard's coverage), which is the cheap part of a fan-out to verify.

## Pins

P1f, P1g, P1h and P1j already held these marks byte for byte and this stage moves four of those
halves. Their POPUP halves are NOT regenerated: the popups are MR5, so a popup that moves here is
a defect and that claim stays an assertion. It held: regenerating moved `census/stock` and
`markers/{airtrain,buses,ferry,path}`, and not one `popups/*` key.

- **P4a**, the census of every marker class and every canvas group, by class rather than by
  total, with the four label counts as POSITIVE classes. It earned itself on its first outing:
  two subway labels read as four, because the ferry's dock names are `.stn-label` and not
  `.rail`.
- **P4b** and **P4b2**, the ferry's docked opacity healthy (0.55) and its compound stale
  (0.2475), asserted as arithmetic as well as recorded. **P4b's first draft was a pin that could
  not fail** and reading the golden after writing it is what caught it: it aged a source on a
  loaded page and the next poll overwrote the edit, so both halves came back identical.
- **P4c**, every family's paints in both themes, which is where Q1's numbers live.

## Tests

- `frontend/families.test.js`, 11 tests: each family's builder as a function of provenance, age
  and heading; the diamond, hull, arrow and dot geometry; the dock's and guideway's options; the
  muted hue swept over all 360 hues at both ends against both papers; and **the theme registry
  asserted against the SOURCE**, which strips comments and compiles the result so the scrape
  cannot be fooled by prose (it was: `njt.js` names `paperColor()` in a sentence) nor by a
  stripper that ate code.
- `tests/e2e/families.spec.js`, D4a to D4g: each family's mark and its dimmed state on the drawn
  page, the ferry's docked rule alone in a healthy world, the dock labels' band and the Names
  toggle, and the two counts this stage widened.
- `tests/e2e/theme.spec.js`, D5a to D5d: the control's release and G7's name rule at both states;
  the swap reaching all six canvas families while rebuilding nothing; the divIcon population
  following the cascade at both ends; and G15's floor on the drawn page.
- `tests/e2e/rail.spec.js` D3c rewritten as the wider census.
- `a11y.spec.js`: the popup state now runs at 320 as well, so the one surface scanned in both
  themes is scanned at all three widths (six scans).
- `tests/contract/staleness.contract.spec.js` **C6e5**: the ferry against a real backend, with
  `ferry:vehicle` killed and `ferry:tripupdate` left alive, because the dimming is about
  positions. A docked boat draws at 0.55 * 0.45 and an under-way one at 0.45, the model agrees
  with the drawn page, and both clear on recovery. Its status-line pattern was measured rather
  than guessed: the line reads `ferries: as of 105s ago`, not the railroads' `MNR as of 30s`.

## Mutations, each in a worktree detached at the commit

**Twenty-six at round 1**, twelve for the stage and fourteen more for the guards that round
repaired, **and seventeen more in round 2**, one per changed Key row and one per guard it moved.
Every one re-run at this tip, with the worktree's sha echoed and compared before every run, the
server killed by PORT rather than by command text, and `CI=1` so Playwright cannot serve the
unmutated tree. **Forty-three in total: forty-two die and one survives for a reason that is
written down.** The full tables are in the ledger. Five are worth naming here:

- **M30** (the diamond's stroke as a presentation attribute) is **killed at the node tier only,
  with every browser gate green**, and that is finding Q8 rather than a sleeping guard.
- **M32** was **restated once**: its first draft also deleted the registry query the Names
  toggle's title is built from, so the page died on a ReferenceError and 24 specs went red, which
  proves nothing about the guard under test. A mutation reverts one decision.
- **M39** makes a family's painter throw, and it is the one that proves round 1's repair: before
  it, D5b and D5d both passed with a family left wearing the previous theme.
- **M45** (the clicked route line back to the raw wheel) **first survived the page tier**, and the
  reason is the repair itself: the painter runs on every theme press, so a line drawn from the
  wrong wheel is corrected by the first press and every comparison made after it passes. D5b takes
  its reading before the theme is touched at all now. The same shape as MR3's M9.
- **M35** (the `<line>` phantom-fill guard) **had stopped running**, and only re-running the whole
  table found it: round 1 turned the line its anchor targets into a block and the table was never
  re-anchored, so it reported `ANCHOR MISS` instead of testing anything. Finding **F19**.
  Re-anchored, it dies on `P4c`.
- **M53** (the Key's rail route line casing dropped) dies on the node tier with **every browser
  gate green**, which is the argument for `frontend/keyglyphs.test.js` in one line: nothing in
  the browser had ever looked at what a Key glyph draws.
- **M47** (the element's alpha not composited) **survives, and is recorded as surviving**: the
  only element on this map with an opacity is the subway's plate, whose paint IS the surface
  colour, so no number moves. There is nothing for a guard to see, and saying so is the honest
  version of a green run.

## Gates

| tier | result |
| --- | --- |
| backend `pytest` | 1738 passed (no backend file changed) |
| `ruff check` / `ruff format --check` | clean |
| `mypy` | clean, 30 source files |
| contract-tier lint | clean |
| node | 361 passed (343 + 6 at round 1, plus 12 in the new `frontend/keyglyphs.test.js`) |
| hermetic e2e | 301 passed (the new `a11y.spec.js A1x2`) |
| contract browser tier | 5 passed (C6e5 new) |
| contract API tier | 38 passed |
| `run_all.sh` | 15 passed |

`run_all.sh` needed one repair of its own on the way, and it is the same shape as MR3's finding
about the animation wait. It went 14/15 with nothing in the repository having moved: F14's tree
walk found the `ACCESSIBILITY.md` inside two of the review's own per-agent worktrees and reported
"screen readers are now named outside the statement of absence". The walk prunes any directory
holding a `.git` entry of its own now, which is what a nested worktree or clone is. A record whose
verdict depends on whether someone happened to be reviewing at the time is not a record.

Two harnesses and two audit drivers gained the stand-ins MR4's load-time registry needs
(`boards.test.js`, `f03`, `f04`), which is what `f03`'s `staleTreatments` stand-in already was.

## Before and after

`docs/reviews/map-redesign/mr4/`, the City preset at 1280 and 375 and the Region preset at 1280
(so AirTrain is in frame), each in both themes. `MEASURING.md` says how to take them again and
`capture.spec.js` is the harness; the two runs differ only by the tree they ran in.

| | 1280 | 375 | region 1280 |
| --- | --- | --- | --- |
| before | `before-desktop.png` | `before-375.png` | `before-region.png` |
| before, dark | `before-desktop-dark.png` | `before-375-dark.png` | `before-region-dark.png` |
| after | `after-desktop.png` | `after-375.png` | `after-region.png` |
| after, dark | `after-desktop-dark.png` | `after-375-dark.png` | `after-region-dark.png` |

**And round 2's own pair, which is the Key panel OPEN.** The six frames above are taken with it
closed, so the round that rewrote seven of its rows is invisible in them. These four are the
round's evidence, and the before pair is taken from the tree as it stood before it.

| | 1280 | 375 |
| --- | --- | --- |
| before | `before-key-desktop.png` | `before-key-375.png` |
| before, dark | `before-key-desktop-dark.png` | `before-key-375-dark.png` |
| after | `after-key-desktop.png` | `after-key-375.png` |
| after, dark | `after-key-desktop-dark.png` | `after-key-375-dark.png` |

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_017oZP4Mtd6BLoSvAeVaPrJz
