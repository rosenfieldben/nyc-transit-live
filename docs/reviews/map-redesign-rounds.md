# Phase MR adversarial review: the adjudication record

Phase MR (the front-end map redesign) ships as **five stages, one pull request
each**, against the v3.1 handoff recorded at `docs/design/map-redesign/`. This file
is the running adjudication record for all five: every finding raised in every
round, with its disposition, and every mutation run with the tier that killed it.
Nothing is dropped; a finding that was wrong is recorded as wrong, with the
measurement that refutes it, and so is a finding whose *expectation* was wrong
while the finding itself held.

The same five categories the A4, 15a, 15b and 15c records use:

- **Fixed**: real, a change shipped, and a mutation proves the change is load-bearing.
- **Deferred, with reason**: real, not fixed in this stage, and why.
- **Refuted, with evidence**: reported and false; the measurement that refutes it.
- **Refuted then overturned**: refuted at the time and later found real after all.
- **Confirmed but downgraded**: the reported behaviour reproduces, but the severity
  does not survive contact with the rest of the suite.

## The design of record

`docs/design/map-redesign/` carries the handoff as it was received, not as it was
implemented:

- `README.md`: the v2 spec. Screens, tokens, marker grammar, popup vocabulary.
- `V3-NOTES.md`: what v3 and v3.1 changed under the v2 spec. **It wins over
  `README.md` wherever the two disagree**, and `map-redesign-v3-brief.md` wins over
  both.
- `map-redesign-v3-brief.md`: the v3 brief, the freshness contract's provenance
  ladder, the per-observation states, the real route tables, and the accessibility
  constraints carried over.
- `IMPLEMENTATION.md`: the file-by-file mapping onto this repo.
- `reference/map-redesign-v2.css`, `reference/map-redesign-v2.logic.js`: the
  prototype's stylesheet and logic. **Ported from, not shipped**: the handoff says
  so in as many words, and V3.1 adds that the prototype's `posState()` and
  `statusLine()` are illustrative only.
- `screenshots/`: the 1280px captures the fidelity claims are measured against.

The prototypes themselves and the vendored Leaflet copy that served them are
**deliberately not in this repository**. The prototype is a React/DCLogic component
against synthetic data; keeping a second, diverging Leaflet tree and a second
marker implementation in the tree would give every future reader two answers to
every question, and the handoff's own instruction is to recreate the design inside
the existing frontend rather than to paste the prototype in. The stylesheet and the
logic file are kept because they are the *specification* of the vocabulary at a
precision prose cannot reach; the screenshots are kept because they are what
fidelity is judged against.

## The five stages

Each stage is one pull request.

### The standing rules every stage is held to

Collected here because they were scattered across three stages' findings, and a rule a
future stage has to go looking for is a rule it will miss.

1. **The pins land before anything is restyled**, as their own commit at the head of the
   stage, and they say what the stage may NOT change.
2. **These stay green at every stage**: the C6-series dimming specs, the F01 and F03 e2e
   specs, and the axe specs at desktop, 375 and 320.
3. **`docs/reviews/audit-2026-09-05/run_all.sh` is a gate of every stage**, at fifteen of
   fifteen. Added after MR2, and added because it was learned the hard way: the suite was run
   by hand, so when MR1 restyled Leaflet's zoom control and MR2 rewrote
   `frontend/systems/shared.js`, three of its Node harnesses' own stubbed Leaflet and DOM
   outgrew the frontend they load, and **F11, F12 and F14 sat red on `main` through a whole
   stage** with nothing failing, because nothing ran them. F11 is the record for the station
   alerts join, which is one of the three consumers the F1 backend branch exists to protect,
   so the gap was directly over something two stages had just worked on. It is a CI job now
   (`audit-records` in `.github/workflows/ci.yml`), so a stage cannot merge past a red row,
   and a record that goes stale says so on the pull request rather than waiting to be noticed.
4. **Every review and probe workflow runs in its own worktree**, and before any commit the
   working tree is confirmed to be what the gates ran on. `.claude/workflows/README.md` states
   it and `adversarial-review.js` enforces it; MR2 round 2's incident is the evidence.
5. **A fix is finished when reverting it fails something**, not when it works. MR2 round 3 had
   three fixes survive their own mutation on the first run, each already verified by hand and
   commented.
6. **A mutation whose anchor misses is a mutation that did not run**, so the WHOLE table is
   re-run before every push and an `ANCHOR MISS` is a failure of the run rather than a
   survivor. Added after MR4's finding **F19**: M35's anchor targeted a line that round 1 had
   turned into a block, and for a whole round the runner printed `ANCHOR MISS`, exited
   non-zero and tested nothing, so a guard this repo relies on had no evidence at all while
   its table still read "killed". Two things follow from it and both are the rule rather than
   advice. A mutation table is CODE and rots exactly the way an unread field does (round 4's
   `dim` column is the same lesson in a different file), so it is re-anchored whenever the
   code it targets is touched. And re-running only the NEW rows is what hides this: the old
   rows are the ones whose anchors have had time to go stale.

| Stage | Scope | State |
| --- | --- | --- |
| **MR1** | **Tokens and chrome.** The Modernist token set on the root with `data-theme`, self-hosted Archivo 400/600/800, the `.leaflet-tile-pane` filters for both themes, and a `localStorage`-persisted theme toggle, **built and tested and then hidden until MR4** (round 3, R2). The `<header>` replaces the right-hand `<aside>`: brand and blinking clock, the subway bullet key (display only, and in the app's own shape), the Key and Stations buttons, the feed strip, the Key panel, and the service alerts strip as a full-width row inside the header. The bottom-right control stack with the City/Rail/Region presets and the restyled zoom control. No marker, line, station, label, popup or route-table change: the pins prove it. | merged |
| **MR2** | **Subway.** Trunk ribbons (casing plus line, yellow drawn last), the bullet train marker with its halo and lift, local dot versus transfer ring stations, the haloed permanent-tooltip labels with their zoom gating, the Names toggle, and route focus wired to the stage 1 bullets. Every ribbon takes its colour from `lineColor()` and every bullet keeps the app's own rounded rectangle, never the authority's palette or its roundel (round 3, R1). **And, on the operator's instruction after round 2**, the key is derived from the loaded route list rather than written down, every drawn polyline carries the set of routes that ride it, focus is membership in that set, and the key is an ARIA toolbar with one tab stop. **Round 3 adds**, on four more rulings: the subway's ribbons on their own pane below every other family's lines, the station labels on a pane below every vehicle, transfer counted by TRUNK rather than by route id, and an off-focus marker out of the accessibility tree and out of the click path while a route is focused. | merged |
| **MR3** | **Commuter rail.** The real route tables (§6 of the brief: name-keyed codes for LIRR and Metro-North, the feed's `route_short_name` and `route_color` for NJ Transit, `route_color` added to `/api/railroad-routes` by `claude/railroad-route-colors` and `route_short_name` by this stage), the branch lines with their casings, ONE square station for all three agencies, names from zoom 11, and `railTagIcon` with the §3.1 provenance states: solid versus outlined body, filled versus outlined chevron, dimming for age. Bearing reuses the slice the glide already built and takes the SERVED direction; there is no headsign rule (v3.1). | in review |
| **MR4** | **The other families.** PATH diamonds and lines, ferry dashed routes, dock dots and hulls, AirTrain's gray dashed service, and the bus arrow and dot at the muted hashed hue. The §3.3 dimmed and absent states for each. **Also the dark theme's release**: MR1 built it and hid the toggle, and MR4 is the stage at which every mark on the map has the casing that makes it legal (round 3, R2). | planned |
| **MR5** | **Popups.** The `.pk/.pt/.kv/.dir/.arr/.fresh/.alert/.xlink` vocabulary, the §4 words routed from `positionQualifier()` and the per-system freshness rather than re-derived, the arrivals qualifier column, and the autopan padding that clears the stage 1 chrome. | planned |

### The backend branch this phase owed, and paid

**`stop_times.txt` is a required member of the subway static archive now**, which is the rule
`shapes.txt` already got and the rule PATH and the ferry already applied. Delivered on
`claude/subway-static-station-routes`, one backend commit, after MR2 merged.

**What it was.** `_REQUIRED_MEMBERS` was `("stops.txt", "shapes.txt")`,
`load_subway_station_routes` caught every exception and returned `{}`, and the subway warmup
then reported `ready` with an all-empty index while `/healthz` stayed green. **Three consumers
depend on that index and all three are rider-visible**: the transfer ring, the hub label class
the zoom-12 band is built on, and the station alerts matcher. Reproduced by execution in MR2
round 3.

**What it is.** `_REQUIRED_MEMBERS` is `("stops.txt", "shapes.txt", "trips.txt",
"stop_times.txt")`, so a reduced publication fails the load through `require_members`, the
group reports `failed`, `HEALTH_SUBWAY_STATIC_FAILED` fires, `/healthz` degrades and the
monitor's existing `subway-static-failed` check sees it: no new code and no new check, which is
asserted in the monitor's hermetic tests rather than trusted. `load_subway_station_routes`
raises instead of swallowing, because a parse problem in a required member is a failed load.
What stays tolerant is a station with no trips, which is data rather than failure. The
last-known-good index survives a failed reload, which the warmup already guaranteed
structurally and a test now pins.

**MR2's frontend fallback stays** and is still worth having: it covers a PARTIAL index, where
some stations list routes and others do not, which no required member can rule out. The
backend change covers the absent one.

---

## Stage MR1: tokens and chrome

### The pins, and why they come first

A restyle that touches one stylesheet and one HTML file can move a marker or a
popup without anyone noticing, because nothing in the suite asserts that they did
not move. Stage 1's first commit is therefore six pins with no production change
behind them, each written to fail if stage 1 drifts outside its scope:

All of them live in `tests/e2e/pins.spec.js` against the golden
`tests/e2e/fixtures/mr_pins.json`, which was **measured from the running page, not
written by hand**.

| Pin | What it holds | Measured value |
| --- | --- | --- |
| P1a | The status line's words in the **F03 world**: the ACE group's content ten minutes behind a poll that keeps succeeding. | `trains: ACE group as of 10m ago` |
| P1b | The status line's words in the **F01 world**: the committed capture with Metro-North's own poll aged six minutes, which is the one world where all three clauses arrive together. | `railroad: MNR as of 6m ago; LIRR 24 trains not shown, last seen over 10m ago; MNR position age unavailable` |
| P1c | A **healthy day says nothing** and is not painted as an error. | `` (empty) |
| P1d | The **alert banner's subtree**, byte for byte, plus its rows' text in order. | `.alert-banner-strip` / `.alert-banner-rows` / `.alert-banner-row` / `#alert-banner-dismiss` unchanged |
| P1e | Every **accessible name the legend says**, with the `aria-hidden` glyphs removed, so the Key panel is checked against the list rather than against a memory of it. | 17 rows plus the note as MR1 measured it; a SUPERSET check, so a stage could add but never remove. **MR4 round 2 made it an ordered equality over the 16 rows plus the note the panel has now**, by ruling, and recorded the 18 names it replaced. The superset is what let six stale rail rows sit for two stages: it made replacing a row the one thing a stage could not do |
| P1f to P1n | Per system, every **mark's HTML** (divIcons) or **renderer options** (canvas circleMarkers) with its anchors and its per-observation opacity, and its **popup HTML** as a rider sees it, read through the marker that owns it. Screenshot-free: the assertion is on the DOM and on Leaflet's options, never on pixels. | 9 families, 14 popups |

Two things the pins had to be written around, both measured rather than guessed:

- **The status pins assert the problems tail, not the whole line.** The whole line is the
  thing MR1 takes apart: the counts become the feed strip's, the clock becomes the
  header's, and only the problems stay in `#status`. A pin on the composed line would
  have been a pin on the change. `statusTail()` strips a counts-and-clock head when there
  is one, so the same pin reads the same string before and after.
- **A popup is read through its marker, not through `.leaflet-popup-content`.** This app
  can hold a vehicle popup and a station popup open at once, so that selector is not
  unique and `map.closePopup()` closes only the map's current one. And the settle loop
  runs on the driver's side, because the suite's clock is paused and an in-page
  `setTimeout` never fires: the first cut of this helper deadlocked every station pin for
  the full timeout.
- **The pin ids are literal strings, and two shapes had to be thrown away to learn it.**
  `tests/specids.js` collects test ids by pattern, and an id it cannot collect is worse
  than a missing one: a claim citing it resolves against nothing and still reads as
  evidence. `P5/P6.` dies on the slash, and a title built as `` `${system.id}. ...` ``
  loses all nine ids while every test still passes. Hence `P1a` through `P1n`, one literal
  title per pin over a shared body.

### Round 1 findings

Twenty, from building the stage against the handoff and running every tier after each
step. Six were introduced by this commit and caught by the suite before it landed;
three are defects the stage merely *exposed* and one is a design ruling this stage
cannot make. Nothing is dropped.

#### The design's own values, where measurement disagreed with the drawing

| # | Finding | Disposition |
| --- | --- | --- |
| F1 | **The header as drawn is undecidable to axe.** The handoff gives it `color-mix(in srgb, var(--surface) 90%, transparent)` over `backdrop-filter: blur(14px)`. The panel it replaces was translucent once and was made opaque for a measured reason this stylesheet records: axe cannot resolve the contrast of text over a translucent surface whose backdrop is a tile IMAGE, and opacity took the undecidable set from nine entries to one. Reproduced on this branch with the drawn surface: the brand, the clock, all eight feed names and the note went to `incomplete` at 1280, 375 and 320, and `a11y.spec.js`'s undecidable list is closed and asserted, so it is a gate failure rather than a risk. | **Fixed by overruling the drawing.** The header ships at full `--surface` opacity with no backdrop filter, and `helpers.test.js`'s A3 contrast test asserts the absence of both ways it could come back. The visual difference is small; the accessibility difference is the entire reason that rule exists. |
| F2 | **The accent fails AA as text, and no ink clears it as a fill.** `--accent` measures **3.47** on `--surface` in the light theme and **4.46** in the dark, against the 4.5 that 10px and 12px text owes, and the design uses it as text in four places (the stale note, the OFF mark, a stale footer, an under-30-seconds countdown). Worse for the filled Stations button: on `#ec3013` white measures **4.20** and dark ink **4.14**, so *neither* ink is legal. | **Fixed, measured.** `--accent-ink` (`#bd260f` light, `#ff7863` dark) carries the words and is the filled button's background; the undarkened accent carries the MARKS, where 3:1 applies and 3.47 clears it. That split is the one `readableInk` already makes in this app, and the no-ink case is the one this stylesheet met before at the PATH fallback and recorded as "a fill that has to move rather than an ink that has to be chosen". Measured after: 5.03 as text, 5.45 as a fill. |
| F3 | **The focus ring halves in the dark theme.** `#1d4ed8` measures 5.53 on the light surface and **2.10** on the dark one, against the 3:1 `mobile.spec.js` A6f enforces. Nothing in the suite had ever scanned a dark page, so this would have shipped silently. | **Fixed.** `--focus`, `#1d4ed8` light and `#7795e8` (4.86) dark. The A3 node test resolves both token blocks out of the stylesheet and checks every chrome pairing in both themes, so a token edited without measuring fails at the node tier. |
| F4 | **The design's trunk palette contradicts a standing repository ruling.** The handoff gives the official MTA trunk colours and route-bullet markers. This repository's README Notes say the opposite in as many words: "The MTA's logos, official map, and route symbols require a license. Use your own colors and markers rather than official MTA branding", and `LINE_COLORS` exists because of it. The ferry note next to it draws the line precisely: route colours published in a feed's `routes.txt` are DATA; logos and route symbols are branding. | **Deferred, with reason, and it must be settled before MR2.** MR1's subway key is built from `lineColor()` and `readableTextOn()`, which is both the ruling's requirement and the only honest option: a key in one palette over lines drawn in another says nothing true about the map under it. Every trunk clears 4.5:1 that way, the lowest being 4.72 on the 4-5-6 green, and the L's grey takes dark ink at 5.00 where white would have been 3.48. MR2 draws the ribbons and the bullet markers, so MR2 is where the operator has to rule. |

#### Defects this stage introduced, and the spec that caught each

| # | Finding | Disposition |
| --- | --- | --- |
| F5 | **`renderAlertBanner` kept a call to the publisher this stage retired**, so the rebuild threw `ReferenceError: publishBannerHeight is not defined` and the dismiss button stopped dismissing. The removal took two call sites and left the third, which differed only by indentation. | **Fixed.** Caught by `layout.spec.js` A4d, which presses Enter on the focused dismiss and requires the control to be gone: exactly the assertion that "focus on a right-looking element is not the same as the rider being able to act" was written for. |
| F6 | **The feed strip painted at module scope and read registries that did not exist yet.** `buildFeedStrip()` ended in `refreshFeedStrip()`, which reads `trains`, `buses` and the rest; those are declared in the per-system files, which load *after* `shared.js`. `ReferenceError: trains is not defined` took `shared.js`, `stations.js` and `map.js` with it and the page came up with no markers at all. | **Fixed.** `buildFeedStrip` paints the count-free model instead, and the first poll paints the rest. Caught by every spec in the suite at once, which is what a dead page looks like. |
| F7 | **The Leaflet control overrides lost to Leaflet's own two-class rules.** `.leaflet-control-attribution` at one class cannot beat `.leaflet-container .leaflet-control-attribution`, so the attribution stayed translucent white over the tiles. The prototype's stylesheet prefixes every one of these and the reason was not carried over. | **Fixed.** Every Leaflet override is prefixed with `.leaflet-container`. Caught by `a11y.spec.js` A1z at **4.06** against the worst possible tile. |
| F8 | **The header covered the map's own controls at 320.** With the Key open and no bottom reserve, the header ran to y=620 of 640 and put Leaflet's zoom control and the OpenStreetMap attribution underneath it. Three undecidables, and more than an axe problem: the attribution is a condition of using the tiles. | **Fixed.** The header is capped at `calc(100% - 72px)`, which is Leaflet's bottom-right corner. |
| F9 | **The view presets tracked a flag, and `flyTo` fires two end events.** `zoomend` and `moveend` both land, so a handler that ignored one cleared the active preset at the instant it became true. The second cut compared centres in DEGREES and failed for a different reason: a `flyTo` arrives through pixel arithmetic and landed **0.0007 degrees** off its target at zoom 10, which a 1e-4 allowance read as the rider having moved. | **Fixed.** The presets ask the MAP whether they are active, with a two-PIXEL tolerance, which is the same sentence the button claims. Caught by `chrome.spec.js` D1j. |
| F10 | **The bus route banner listened for `change` on a checkbox.** The feed toggles are buttons with `aria-pressed` now: they fire no `change` and carry no `.checked`, so the listener went silent and the banner went on claiming a route line that was no longer drawn. | **Fixed.** It listens for `click` and asks `feedShowing("buses")`, which is the strip's own state rather than a control's rendering of it. Caught by `busroute.spec.js` A7f. |

#### Defects this stage exposed, which were already there

| # | Finding | Disposition |
| --- | --- | --- |
| F11 | **`a11y.spec.js` A1z read the blue channel as an alpha.** `backgroundColor.match(/[\d.]+\)$/)` takes the last number before the `)`, which is the alpha of an `rgba()` and the BLUE of an `rgb()`. Latent for as long as Leaflet's translucent default was in force; making the attribution opaque surfaced it as a contrast ratio of **2118582** over black and **-0.11** over white. | **Fixed.** A four-component colour has an alpha and a three-component one is opaque. |
| F12 | **`assertNothingIsMidTransition` waits for an endless animation forever.** It requires `getAnimations()` to be empty before every scan, which is the right question about a TRANSITION (a move between two settled states) and unanswerable about a 2s infinite pulse. Measured: all seventeen axe scans died on the five-second wait rather than on anything they found. Not reachable before this stage, because the app had no endless animation. | **Fixed.** The filter is on the thing that has two states. The pulse is still gated and still asserted, at `motion.spec.js` A5b and A5d, which is where a claim about the preference belongs. |
| F13 | **The legend never had a "dimmed" row**, although the freshness contract has drawn a vehicle dimmed per observation since 6.3. Measured: the seventeen rows mention dimming exactly once, in "dimmed when at a dock", which is a docked boat and a different rule entirely. The stage brief assumed the row existed. | **Fixed as coverage.** The Key panel adds it, drawn as the bus arrow at the contract's own marker opacity so the row shows the treatment rather than describing it. Pin P1e asserts the other seventeen are all still there. |

#### Findings in the specs themselves

| # | Finding | Disposition |
| --- | --- | --- |
| F14 | **Two pin id shapes were invisible to `tests/specids.js`.** `"P5/P6."` dies on the slash, and a title built as `` `${system.id}. ...` `` is not a literal first token, so all nine per-system ids vanished while every test still passed. An id the collector cannot see is worse than a missing one: a claim citing it resolves against nothing and still reads as evidence. | **Fixed.** `P1a` through `P1n`, one literal title per pin over a shared body, and A4's corpus test in `tests/statement.test.js` is what says so. |
| F15 | **The popup-correction specs wrote `80` down.** A4j, A4k and A4m grew a popup by a literal 80px to push it under the chrome. That was right for a tall right-hand panel and stopped reaching a one-row top bar. The premise assertion caught it honestly, which is what a premise assertion is for. | **Fixed.** The growth is measured from the actual gap, so a stage that changes the header's height changes what these specs push rather than whether they still test anything. |
| F16 | **A6f's two-pass restructure broke `:focus-visible`.** Opening the feed strip with a CLICK sets the browser's interaction modality to pointer, after which a programmatic `focus()` no longer matches `:focus-visible`, and every control in the pass came back with no ring: the spec would have reported correct code as broken. | **Fixed.** The strip is opened from the keyboard, which is the state a rider who is tabbing is actually in, and is also the distinction the spec's own negative half is about. |
| F17 | **A6k, A6l and A6m were three specs about two boxes that could collide.** They existed because the alert banner and the legend panel were independently positioned and met at the bottom of a phone screen, with a measured, republished `--alert-banner-height` as the machinery. | **Replaced with the stronger claim.** The strip is a row of a bounded flex column, so the collision is structurally impossible; the specs now assert that the whole header fits the viewport in both Key states, at both phone widths, with one alert and with three, and that the Key panel is the row that gives way. |

#### Deviations and consequences, for the operator to rule on

| # | Finding | Disposition |
| --- | --- | --- |
| F18 | **C6e2 did not stay unchanged**, and the stage brief asked that it should. Its liveness guard read `toContainText(/trains/i)` on a HEALTHY page, which held only because the status line carried the subway's COUNT, and that is precisely what this stage takes apart. | **Changed, one line, flagged.** The guard now reads the subway feed's count off the strip, which says what the count said where the count now is. C6e2's actual claim, that a poisoned BDFM group is named in the status line, is untouched; C6e1, C6e3 and C6e4 are byte-unchanged and green. A first attempt asserting the note was empty was wrong on a real backend, where the static archives load behind the realtime feeds and the note legitimately carries four "still loading" sentences for the first polls. |
| F19 | **The status note folds with the feed strip below 700px, and today's status line does not.** `#status` is a sibling of `#legend` in the old panel, so it stays visible on a phone; in the design it is row 2's trailing note and row 2 folds behind Key. v3.1 carved the ALERTS strip out of the fold and did not carve this out, so MR1 implements it as specified. The accessible path is unchanged either way, because `#page-announce` speaks every status transition and is never folded; what changes is that a sighted rider on a phone reads the note after one tap instead of at a glance. | **Delivered as specified, raised here.** If the note should join the alerts row's carve-out, MR2 is the place. |
| F20 | **The dark theme stops at the Stations panel's edge.** The panel keeps its literal colours, so a dark map sits beside a light panel. | **Out of scope, twice over, and left alone.** The handoff's own scope note ("The Stations side panel and the accessibility panel behaviour are out of scope and unchanged") and the stage brief ("the panel untouched") both say so, and the panel's colours ARE its measured contrast relationships: A1i, A1j, A1k, A1l and A1m2 depend on them. Worth a stage of its own rather than a corner of this one. |

### Mutations

Each on a fresh copy of the tree (`git ls-files --cached --others --exclude-standard`,
so a new untracked file is included), the mutation applied alone, and the named tier
run against it.

| # | Guard reverted | Result | Killed by |
| --- | --- | --- | --- |
| M1 | `aria-pressed` not updated when a feed is toggled (the layer still hides, the OFF mark still appears) | killed | `chrome.spec.js` **D1c**: `pressed` reads `"true"` on a hidden feed |
| M2 | hidden feed conveyed by opacity alone (the strike and the visible OFF mark removed) | killed | `chrome.spec.js` **D1c**: `offMarkShown: false` and `strike: "none"` on a hidden feed |
| M3 | the service alerts strip given the fold class | killed | `mobile.spec.js` **A6c**: "the service alerts strip must never be given the fold class" |
| M4 | the trailing note re-derived from the freshness index instead of taking `staleness()`'s output | killed | `chrome.spec.js` **D1d** and pins **P1a** and **P1b**: the withheld count and Metro-North's undated clause both vanish, because neither is an age |
| M5 | the theme not persisted to `localStorage` | killed | `chrome.spec.js` **D1g**: the stored value reads `null` where `"dark"` was expected |
| M6 | the clock blink not gated by the motion preference | killed | `motion.spec.js` **A5b**: the dot's `animationName` reads `hdr-blink` under `prefers-reduced-motion: reduce` |

**THE NODE TIER KILLED NONE OF THEM, and that is the honest record rather than a
gap.** Every one of these six reverts a WIRING decision: which attribute the paint
function writes, which rule the stylesheet carries, which function `map.js` calls,
whether a click reaches `localStorage`. The pure half is tested at the node tier and
tested well (the eight feeds and their order, the dot's three states, the tooltip's
words, the one boolean behind `aria-pressed` and the OFF mark, the note as the line's
tail, the theme's two decisions), and none of that can see a caller that stopped
calling. The browser is where a wiring mutation dies, which is what these six confirm
and what the tier split is for.

### Round 2: the adversarial pass over the production diff

Four dimensions read the production half of the diff (`frontend/index.html`,
`style.css`, `helpers.js`, `map.js`, `systems/*.js`) and raised **25 findings**, each
handed to a hostile verifier whose default was REFUTED. Sixteen are fixed below. Every
number in this table was re-measured here, against the repository's own helpers, before
anything was changed.

**The two that mattered most were a bug hiding a bug.**

| # | Finding | Disposition |
| --- | --- | --- |
| G1 | **The design's one filled button never rendered at all.** The old A1 `#stations-toggle` rule survived the rewrite, and it sits LATER in the stylesheet at equal specificity, so it won: the Stations button drew as the A1 panel's full-width light-grey bordered row (`display: block; width: 100%`), not as the accent chip. It is visibly wrong in the "after" screenshot committed at `4b5d583`. | **Fixed.** The old rule is deleted rather than moved; the header rule is the whole of it now. |
| G2 | **And deleting it exposed the ring the fill had been hiding.** With the accent fill finally drawn, the focus ring is measured against it: `--focus` reads **1.10** on `--accent-ink`. No single colour clears 3:1 against both that fill and the surface beside it (`--focus` 1.10 on the fill, `--ink` 2.73, `--chipink` 5.45 on the fill but 1.06 on the surface). | **Fixed.** The one filled control rings INSIDE itself, `outline-color: var(--chipink); outline-offset: -3px`, where one colour is enough and where the measurement and the drawing agree about which surface the ring is on. Caught by `mobile.spec.js` A6f within the hour. |

**One leaked out of scope, and the pins could not see it.**

| # | Finding | Disposition |
| --- | --- | --- |
| G3 | **`.alert-stale` is not the strip's class.** The same class carries the same freshness hedge inside every popup's alert block (`routeAlertsBlock`, `stationAlertsBlock`), where the surface is still the popup's white. Unscoped, the new rule repainted all of them: **6.09** in the light theme, legal but out of scope, and **2.59** in the dark, which is neither. The pins did not catch it because they hold popup HTML and this is a computed style. | **Fixed**, scoped to `#alert-banner`. And noted as a real limit of P1f to P1n: byte-identical HTML does not mean an unchanged rendering, because a stylesheet reaches a popup without touching its markup. |

**One defeated the fix it was part of.**

| # | Finding | Disposition |
| --- | --- | --- |
| G4 | **The OFF treatment was faded along with everything else.** A blanket `opacity: 0.55` on a hidden feed's button dimmed the two things that carry the state: the OFF mark fell to **2.50** and the feed's own name to **2.25** against the header surface, both under the 4.5 they owe. The remedy for "state must not be conveyed by opacity alone" was itself made illegible by opacity. | **Fixed.** The fade is on the tick, the glyph, the count and the dot, which the rider no longer needs to read; the name and the OFF mark stay at full strength at **5.03** and **5.38**, because they are what says why. |

**Six more, each confirmed and fixed.**

| # | Finding | Disposition |
| --- | --- | --- |
| G5 | **The view preset stack was an overlay the popup correction could not see.** It is fixed to the bottom right at z-index 1000 and paints over the popup pane exactly like the other two, and `POPUP_OBSTACLE_IDS` listed only `panel` and `alert-banner`, whose own comment says the list exists so that "adding a third overlay is a deliberate edit here and not a silent regression". Verified independently as high severity. | **Fixed**: `view-stack` joins the list. |
| G6 | **The fold could strand keyboard focus on `<body>`.** Two ways in: Tab to a feed toggle on a phone and press Key, or hold focus in the strip while a resize crosses the breakpoint, which needs no press at all. The view stack's stand-down is a third, by a CSS `display: none` the rider did not ask for. This app treats a dropped focus as a defect everywhere else it can happen. | **Fixed.** `applyHeaderDisclosure` asks what is about to stop existing before it does, and sends focus to the Key button, which is the control that did it and the one that undoes it. Silent: a rider who pressed a disclosure is not owed news that it closed. |
| G7 | **The theme toggle claimed a state its own label contradicted.** In the dark theme it read "Light" and reported `aria-pressed="true"`, so a screen reader said "Light, pressed", which states that the light theme is on while the page is dark. `aria-pressed` belongs to a toggle whose label does NOT move with the state; the design's label is the action. | **Fixed**: the label is the whole answer and `aria-pressed` is gone. D1g asserts its absence, because the defect was an extra claim rather than a missing one. |
| G8 | **The feed strip's colour ticks are theme-blind.** Against the header surface: Subway's `#0039A6` reads **8.11** light and **1.43** dark; Buses **2.16** and AirTrain **2.76** dark. All under the 3:1 a mark owes. | **Fixed without inventing a second palette.** The tick keeps its identity colour and gains a one-pixel `--ink` boundary, which is how every vehicle on this map is already drawn: a route-coloured shape with a paper stroke, so the SHAPE reads whatever is behind it. |
| G9 | **Leaflet's disabled zoom state was overridden.** The new `.leaflet-bar a` rule set the colour for every state at a higher specificity than `.leaflet-disabled`, so at zoom 19 the "+" looked exactly as live as the "-" and did nothing. | **Fixed**: the disabled glyph goes to `--muted` and the cursor stops inviting the press. |
| G10 | **The bus route banner's label was a hashed hue on a surface that can now be dark.** `routeColor()` returns `hsl(h, 75%, 40%)`, measured against the old panel's opaque white and set straight onto the text. | **Fixed by the rule this app already states** at `readableInk`: "the brand colour stays on the SHAPES that carry identity". The route's colour is a mark before the words; the words are `--ink`. |

**Three about the header's own bounds.**

| # | Finding | Disposition |
| --- | --- | --- |
| G11 | **The Key panel could be open, report itself expanded, and show no rows.** `min-height: 0` is what lets it shrink at all, and with a tall row 1, a wrapped feed strip and three alerts it shrank to a slit. | **Fixed**: a `min(96px, 100%)` floor, which is three rows and the scrollbar that says there are more. |
| G12 | **The 72px bottom reserve bounds the header's BOX, not its content.** A box whose children are all `flex: none` overflows its own `max-height`: with the Key open and three wrapped alerts at 320, the alerts row ran past the reserve and back over Leaflet's zoom control and the OSM attribution, which is the thing the reserve exists to keep clear. | **Fixed**: the alerts row shrinks second (after the Key) and scrolls what it cannot show, with a `min(48px, 100%)` floor. It still never folds, which is a different promise and still kept. |
| G13 | **`#legend` became a tab stop and was the one new control left out of the focus-ring list**, falling back to the browser's own ring. | **Fixed.** |

**And one where the preference was read live but could not be honoured.**

| # | Finding | Disposition |
| --- | --- | --- |
| G14 | **A view preset still animated when reduced motion was turned on mid-session.** `motionAllowed()` is read live on every press, so the non-animated branch was taken correctly; what it could not do was honour the rider, because Leaflet reads `zoomAnimation` ONCE at construction (`helpers.js` says so at `watchMotionPreference`) and a bare `setView` on a map built while motion was allowed still animates the zoom. A rider who set the preference before load was never affected, which is why only the mid-session case could show it. | **Fixed**: `setView(..., { animate: false })`, the supported way to say no to that one call. |

**And one that is bigger than any fix in this stage.**

| # | Finding | Disposition |
| --- | --- | --- |
| G15 | **In the dark theme every Key panel glyph falls under the 3:1 mark floor**, from **1.11** (the subway station ring) to **2.63** (the ferry dock). They were drawn for an opaque white panel and they carry the map's own marker colours. | **The key is fixed; the map is not, and the map is the real finding.** The glyphs keep their colours exactly and gain the paper they were drawn on, measured at 3.98 to 11.31 in both themes, because a key whose glyphs did not match the map would be worse than a dim one. **THE SAME ARITHMETIC HOLDS ON THE MAP.** The dark basemap filter is MR1's and the markers are not: until MR2 through MR4 give every mark the paper casing and stroke the design specifies, a rider who picks the dark theme gets a map whose markers read at those same ratios. That is the cost of shipping the theme one stage before the marks, it is stated here rather than discovered, and **round 3's R2 is the operator's answer to it: the cost is not paid at all, because the toggle is hidden until MR4.** |

**One recorded and not changed.**

| # | Finding | Disposition |
| --- | --- | --- |
| G16 | **The freshness dot distinguishes live from stale by hue alone**, and the two measure **1.05** against each other. | **Confirmed but not changed, because the information is in text in all three states.** An amber dot and a raised status line are the same condition by construction: `feedDotState` returns "stale" exactly when `staleAge(age)` is true, which is exactly when `staleness()` puts that system in its `stale` population and the note names it. The tooltip carries all three states in words, and AirTrain's scheduled-only state also shows as the absence of a count. The dot is the design's, at 5px, and no shape is distinguishable from another at 5px; the words are what carry it, and they are already there. |

**Seven raised and refuted**, or already fixed by another finding in the same round, and
recorded rather than dropped: they are in the run's journal with the measurement that
refuted each.

### Mutations, re-run after round 2

All six still die, and three now die on more assertions than before, because round 2
added them.

| # | Guard reverted | Result | Killed by |
| --- | --- | --- | --- |
| M1 | `aria-pressed` not updated on toggle | killed | `chrome.spec.js` D1c |
| M2 | hidden feed by opacity alone | killed | `chrome.spec.js` D1c |
| M3 | the alerts strip given the fold class | killed | `mobile.spec.js` A6c |
| M4 | the note re-derived instead of taking `staleness()` | killed | `chrome.spec.js` D1d, pins P1a and P1b |
| M5 | the theme not persisted | killed | `chrome.spec.js` D1g |
| M6 | the clock blink not gated by the motion preference | killed | `motion.spec.js` A5b |

### Round 3: the operator's rulings

Four rulings came back on the round 2 diff. Each is recorded here with the reason it
was given, what MR1 changed to obey it, and the spec that now holds it.

| # | Ruling | What it changed here |
| --- | --- | --- |
| R1 | **The handoff's MTA palette row and its circular lettered bullets are overruled by the README's own rule.** `docs/design/map-redesign/README.md` says in its Notes that "The MTA's logos, official map, and route symbols require a license", and the official roundel is a route symbol. The handoff drew one anyway and pasted the authority's hex values beside it. The app's own rule wins: MR2 draws trunk ribbons in `lineColor()`'s answer and bullets in the app's own shape. | **Obeyed in MR1.** The subway key's swatches already took their colour from `lineColor()` and their ink from `readableTextOn()`, never from the handoff's table, so only the shape was wrong: `.bul` is `border-radius: 4px`, which is what `systems/subway.js` already draws (`<rect rx="3">` in an 18 unit box) for every train on the map. The key and the marker are now the same mark at two sizes, which is what a key is for. `chrome.spec.js` **D1l** holds it: no bullet is a circle, and all 23 bullets across the 10 trunks carry exactly `lineColor()`'s answer. |
| R2 | **The dark theme waits for MR4.** Keep the tokens, the plumbing, the tile filter and the tests; hide the toggle until every mark has its paper casing. | **Obeyed, and G15 is why.** Round 2 measured every Key glyph in the dark theme at **1.11 to 2.63** and fixed it with `--glyph-plate`, a light casing under the mark. The same arithmetic holds for the markers, the labels and the popups on the map, and MR1 is not allowed to touch any of them: shipping the toggle would hand a rider a theme that is legal in the chrome and illegal everywhere else. So `#theme-toggle` carries `hidden` and `#theme-toggle[hidden] { display: none }`, and nothing else is removed. `applyTheme`, `storedTheme`, `nextTheme`, `themeChoice`, the `data-theme` token blocks, the `.leaflet-tile-pane` filters and their tests all stay, D1g drives them through `applyTheme` instead of through a click, and the axe scan still measures both themes. MR4 unhides one attribute. |
| R3 | **The trailing status note does not fold below 700px.** It is empty on a healthy day. The alerts strip and the note are the two carve-outs; the feed buttons and the Key still fold. | **This is F19, ruled on.** The fold class moved off `#toggles` and onto a new inner `#feed-buttons`, so row 2 folds its buttons and keeps its note. `#status:empty { display: none }` means the healthy day costs no row at all, and `#feed-buttons[hidden] ~ #status` reclaims the left margin when it is the only thing left. `chrome.spec.js` **D1k** holds it at 375 and 320 with the Key folded: the note is visible, reads `staleness()`'s text in full ("railroad: MNR as of 6m ago; MNR position age unavailable"), carries `.error`, fits the viewport and is not truncated. **A6c** now asserts both halves: `#feed-buttons` is hidden and `#status` has no `.hdr-fold` ancestor. |
| R4 | **An opened popup, both themes, joins the axe scan**, so popup contrast is measured from here on. If it is not small, it becomes MR2's first item. | **It was small, so it is done here, not deferred.** The a11y suite already ran a list of page states; it gained a theme axis (`state.themes ?? ["light"]`, and a `setTheme` helper that calls `applyTheme` and waits on `data-theme`), and the "popup open with cross-link" state opts in with `themes: ["light", "dark"]`. About fifteen lines. It came back green at both themes at the two widths that state runs at, 1280 and 375, which is the measurement the ruling asked for rather than a promise of one. MR2 inherits a scan that will fail the moment a popup token drifts. |

**What R2 costs, said plainly.** The dark theme is now reachable only from the console
in this stage, so the pair of screenshots below is a light-theme pair, and the dark
half of the design is evidenced by the token blocks, the tile filter, D1g, D1h and the
axe scan rather than by a picture. That is the ruling's intent: the theme is built and
tested, and it is not offered until it is true everywhere.

### Mutations, re-run after round 3

All six still die, each on a fresh copy of the tree with the mutation applied alone.
M3 and M6 were the two the rulings could plausibly have broken, and neither moved: the
clock's gate is untouched by hiding a sibling button, and moving the fold class inward
narrowed what folds without changing what may never fold. **M4 now dies on five
assertions rather than three**, because R3's D1k reads the note at both phone widths and
a re-derived note is wrong there too.

| # | Guard reverted | Result | Killed by |
| --- | --- | --- | --- |
| M1 | `aria-pressed` not updated on toggle | killed | `chrome.spec.js` D1c |
| M2 | hidden feed by opacity alone | killed | `chrome.spec.js` D1c |
| M3 | the alerts strip given the fold class | killed | `mobile.spec.js` A6c |
| M4 | the note re-derived instead of taking `staleness()` | killed | `chrome.spec.js` D1d and D1k at both widths, pins P1a and P1b |
| M5 | the theme not persisted | killed | `chrome.spec.js` D1g |
| M6 | the clock blink not gated by the motion preference | killed | `motion.spec.js` A5b |

### Before and after

`docs/reviews/map-redesign/mr1/`, from the hermetic harness at the frozen clock with
one agency-wide alert showing, so the pair differs by the stage and by nothing else.
Regenerated after round 3, so the "after" pair shows the rounded-rectangle bullets, no
theme toggle, and the note carved out of the fold. **It is a light-theme pair**, because
R2 hides the toggle; the dark theme's evidence is the token blocks, the tile filter, D1g,
D1h and the axe scan.

| | 1280 | 375 |
| --- | --- | --- |
| before | `before-desktop.png` | `before-375.png` |
| after | `after-desktop.png` | `after-375.png` |

---

## Stage MR2: subway

Ribbons, bullets, dots, rings, names and route focus. Carries round 3's **R1**: ribbons in
`lineColor()`'s answer, bullets in the app's own shape, and the handoff's palette row and
its lettered roundel overruled and not copied. The popup axe scan **R4** asked for landed
in MR1, so MR2 inherits it green in both themes and adds one undecidable shape of its own.

### The pins, and why three of them are new

P1f already held every subway mark byte for byte, and MR2 is the stage that deliberately
moves it: its MARK half is regenerated below and the before and after are recorded here,
which is what "measured" means for a mark a stage exists to change. Its POPUP half is not
regenerated. The popups are stage MR5, so a subway popup that changes in MR2 is a defect,
and that claim stays an assertion; after the regeneration the golden's diff is one key
wide (`markers.subway`), which is how that was checked rather than asserted.

What MR2 reaches past on its way is the rest, and three pins landed first to say so.

| Pin | What it holds |
| --- | --- |
| **P2a** | A subway station's registry entry, field by field, minus the two object fields, which are pinned as the questions actually asked of them: is this still the marker the panel syncs to, is it still in the layer the feed strip toggles, and is it still drawn by the canvas on `stationPane`. It also records the distinction P1f's station pin cannot show: the marker's own `pane` option is `overlayPane`, Leaflet's default for a vector layer, and the drawing is on `stationPane` through the RENDERER. Writing `pane` onto the circleMarker would look identical on screen and fail this pin, which is exactly the point. |
| **P2b** | The station panel for a subway station, read from the DOM after a search and a selection: the results list, the detail heading, the arrivals rows and the words spoken into the panel's live region, in one pin because they are one act. |
| **P2c** | F03's qualifiers on both surfaces, as a MEASURED golden. C2i already holds this world and holds it harder. This is a second witness of a different kind: C2i says what the rows should read and would have to be edited to accept a change; this says what they did read before MR2 and fails without anyone editing anything. |

### What the two marks were, and what they are

The before is the golden as `origin/main` served it; the after is the golden this branch
regenerated.

| | before | after |
| --- | --- | --- |
| train icon | `<rect x=1.5 y=1.5 w=15 h=15 rx=3 fill=#c0392b stroke=#fff stroke-width=1.5>` and a `system-ui` 700 9px letter | a `var(--paper)` halo `<rect x=0 y=0 w=18 h=18 rx=4 opacity=.95>` behind the same body rect with no stroke, and an Archivo 800 10.5px letter |
| anchors | `iconAnchor [9,22]`, `popupAnchor [0,-22]` | `[9,21]`, `[0,-21]` |
| station | `radius 4, color #333, weight 1.5, fillColor #fff` | transfer `radius 4.5, color --ink, weight 2, fillColor --paper`; local `radius 3.5, fillColor --ink, no stroke` |

The body's geometry did not move. What moved is that its hard-coded white stroke became a
paper ring BEHIND it, which is a token and therefore follows a theme swap through the
cascade at no cost, and that the type is the design's.

### The measurements the stage brief asked for

Taken against the REAL network rather than the hermetic fixture, because the fixture
serves two shapes and two stations and cannot answer a question about drawing a city.
`docs/reviews/map-redesign/mr2/MEASURING.md` carries the two commands that rebuild the
capture and the harness that runs it; the capture itself is 514 KB and is deliberately not
committed.

**The network, measured**: 24 routes, **35 shapes**, **22,520 points**, **496 parent
stations**, of which **171 have one route** and **325 have two or more**, and none has
zero. MR2 draws 70 polylines and 45,040 points, exactly double, and 171 dots and 325
rings, which the shipped code reproduces exactly.

**Doubling the polylines costs nothing measurable.** A/B in one page, four animated pans
at City zoom per sample, 250 to 330 frames each:

| | polylines | median | p90 | p95 |
| --- | --- | --- | --- | --- |
| before | 35 | 17.0 to 19.0 ms | 20.4 to 22.6 | 21.6 to 25.4 |
| with the casings | 70 | 17.2 to 18.2 ms | 20.4 to 22.4 | 21.5 to 24.2 |

So the answer to the brief's conditional is no: it does not cost more than a few
milliseconds per frame, it does not cost anything, and there is no cut to propose.

**The labels are where the cost is, and it is the PAINTED count that decides it.** Same
harness, same page:

| painted labels | median | p90 | p95 |
| --- | --- | --- | --- |
| 0 | 17.0 to 17.4 ms | 20.4 to 21.1 | 21.5 to 23.3 |
| 10 | 17.8 | 21.0 | 22.1 |
| 156 (a viewport gate at City zoom) | 20.0 | 26.7 | 33.7 |
| 325 (the zoom gate at City zoom, which is what ships) | 19.8 to 21.2 | 26.7 to 29.5 | 30.5 to 36.8 |
| 496 (every name, which is the band from zoom 14) | 20.8 to 23.2 | 29.5 to 32.9 | 34.7 to 44.9 |

The shipped code measures the same: 20.7 median / 35.3 p95 at City zoom with 325 painted,
and 18.4 / 22.7 with the Names toggle off, which is the no-labels baseline exactly.

**So the tooltips are NOT gated to the viewport, and that is a decision with a measurement
behind it rather than a default.** The brief's own test was the count: at zoom 14 over
Midtown, **62 of the 496 labels are inside the viewport**, which is not thousands, so they
may all be permanent. The cost that does exist is not recovered by a viewport gate either:
156 painted costs the same as 325 (20.0 vs 19.8 median, 33.7 vs 30.5 p95), the whole
difference appears between 10 painted and 156, and a viewport gate would add a `moveend`
handler and a class sweep for no measured gain. The two switches that DO work are the zoom
band and the Names toggle, and with names off the frame time returns to baseline exactly.

### Round 1: what building the stage found

| # | Finding | Disposition |
| --- | --- | --- |
| H1 | **A module-scope `const` is in the temporal dead zone until its own line runs**, and the bullet registry was declared after the loop that fills it. `systems/shared.js` threw at load and every one of the 54 smoke specs timed out at boot, which reads as a suite-wide failure rather than as one line in the wrong place. This is the same shape as MR1's feed strip painting at module scope. | **Fixed**: the registry is declared above the loop, with the reason in a comment. |
| H2 | **`fill="var(--paper)"` works in a Chromium presentation attribute and `style="fill: var(--paper)"` works everywhere**, and the difference matters because the failure mode is a black square rather than an error. Measured both in the browser; the CSP is `style-src 'self' 'unsafe-inline'`, so the style form is allowed. | **Fixed**: the style form, in the marker and in the Key. |
| H3 | **The Key panel's glyphs may NOT use the theme tokens**, because `.legend-row svg` sits on `--glyph-plate`, which is `#f3f2f2` in BOTH themes by MR1 round 2's ruling. A glyph drawn in `var(--paper)` would be a dark halo on a light plate the moment MR4 unhides the dark theme. | **Fixed**: the three subway glyphs carry the light theme's literals, and the comment says why. Measured on the plate: the bullet body and the ribbon line read 5.45 and the dot and ring read 14.86. |
| H4 | **Two passes, not one per shape**, which the design does not say. Casing and line interleaved per route means a route drawn later cuts a paper gap through every route already drawn; since the yellow trunk is drawn LAST on purpose, interleaving would have it erase a stripe out of every trunk it shares track with, which on Broadway is three of them. | **Decided and recorded**: all casings, then all lines, each pass in trunk order. |
| H5 | **The reference implementation's yellow-last sort tests only N and R.** `sort((a, b) => (a.route === "N" \|\| a.route === "R") - ...)` leaves Q and W under the darker trunks, and a test written against N would pass. | **Not ported.** `trunkDrawOrder` keys on the trunk and is node tested over all four, including a lettered variant. |
| H6 | **The 24px target floor beats the design's 22px bullet.** The bullets are controls now. At 22 with the design's 2px group gap the centres are 24 apart, which is exactly the boundary of 2.5.8's spacing exception rather than clear of it. | **Deviation, measured.** 24x24, and the bullets joined `layout.spec.js` A4b's list and `a11y.spec.js`'s owned list rather than being exempted. |
| H7 | **The design's "every other bullet fades to 0.3" is not implemented.** A 24px chip at 0.3 over `--surface` blends both the fill and the letter towards the surface, and the letter's contrast against its own chip collapses to near 1:1: the state would be conveyed by making twenty-five route names unreadable. MR1 round 2 found the same defect in the feed strip's OFF treatment and fixed it the same way, by fading only what is decorative; here nothing is decorative, because the letter IS the route. | **Deviation, measured.** The pressed bullet carries a ring in `--ink` (13.70 light, 12.60 dark on `--surface`), the focus ring sits OUTSIDE the chip where it is measured against the surface rather than against ten different fills, and the map says which route is focused by dimming every other one. |
| H8 | **A permanent tooltip is the first DOM a subway station has ever had.** The A2 footnote records that a canvas circleMarker has no element and therefore no accessible name, with the station panel as the stated equivalent. Leaving 496 place names in the reading order would undo that silently. | **Fixed**: every label is `aria-hidden`, with the equivalent restated, and `A1z3` asserts it. |
| H9 | **And `aria-hidden` does not exempt a label from axe's contrast rule**, which is correct: hidden from a screen reader is not hidden from an eye. Two new undecidable findings appeared in every scanned state, and a third message appears when the Key panel is open and overlaps the labels. | **Fixed**: a seventh named shape in `UNDECIDABLE_SHAPES` covering both messages for one element pattern, `A1z3` as its decider, and the matching row in `ACCESSIBILITY.md`. `A1z3` was mutation-tested by removing the halo, which fails it. |
| H10 | **The design's zoom enumeration fails silently outside its range.** `[data-zoom="12"]` through `[data-zoom="19"]` matches nothing at zoom 20 and nothing at a fractional zoom, and the consequence is that every name disappears with no error. | **Fixed**: the root carries `data-zoom` (the design's attribute) AND `data-label-band`, which is `labelZoomBand()`'s answer and is what the stylesheet reads. Three values, one node-tested function, no range to fall out of. |
| H11 | **`IMPLEMENTATION.md` puts these attributes on a `#map` wrapper and MR1 put `data-theme` on `<html>`.** Following the file would have meant selectors that never matched. | **Followed MR1**, which is the shipped decision, and recorded here rather than left to be rediscovered. |
| H12 | **Escape had to join the existing ladder rather than bind a listener.** `frontend/keyboard.test.js` permits exactly one page-level keydown handler, and a second one bound in the bubble phase would have been inert anyway, because the ladder captures and stops the event. | **Fixed**: a rung at the BOTTOM of `map.js`'s ladder, below the popup and the panel, because route focus is a map-wide state rather than a surface anyone stands in. `D2f` walks all three rungs, which is what says the rung is last rather than merely present. |
| H13 | **A paused clock does not run `requestAnimationFrame`, and a canvas renderer redraws on one.** Found while capturing this stage's screenshots: the focus screenshot showed the undimmed map while every layer's `options.opacity` had already changed. It is a property of the harness rather than of the app, and it exposed a real gap in the specs, which asserted what focus WRITES and never that anything repainted. | **Fixed in the tests**: `D2m` reads the overlay canvas's alpha channel and requires focus to take ink off it and clearing to put it back. |
| H14 | **The first draw-order fixture appended the yellow routes** instead of prepending them, so the payload already arrived in draw order and `D2g` passed with `trunkDrawOrder` reduced to `return [...routeIds]`. Found by the mutation run, not by review. | **Fixed**: N and Q come first in the payload, and the comment records why a draw-order test over a payload that happens to arrive in draw order is not a test. |

### Round 2: an adversarial pass over the written diff

MR1's round 2 was the highest-yield step of that stage, and MR2 had not had one: the
workflow before this stage was written was RECON (six readers over the terrain, two
critics), and the mutations after it test the guards rather than the diff. This is the
pass over the production half as written. A multi-agent run is still going; what is below
is what the inline pass found and verified, and a later commit may extend it.

| # | Finding | Disposition |
| --- | --- | --- |
| G1 | **The pressed bullet's ring was clipped by its neighbour.** The bullets are siblings in a flex group with the design's 2px gap, all `position: relative` with `z-index: auto`, and siblings paint in DOM order, so a ring reaching 4px out was drawn into the gap and then painted over by the next bullet's background for its outer half. Measured on the "2" bullet, which has a neighbour on both sides: whole on the left, where the earlier sibling paints below it, and cut off on the right. This is MR1's G1 again in a different costume, a rule that looks right and draws wrong, and the only way to see either one is to look at the drawn page. | **Fixed**: one stacking level, on the two states rather than on `.bul`, so an ordinary bullet keeps painting in source order. `D2a` holds it. |
| G2 | **`stationLabelShown` was exported, node tested, and called by nothing.** The label gate is three CSS rules; a node test over that function looked like the gate's test and decided nothing, which is worse than an absent test because it reads as coverage. | **Converted rather than deleted**, which is the shape this repository already uses for `statusLineText`: the function is the ORACLE and `D2j` now drives the stylesheet against it across zooms 11 to 15 in both toggle states. **And then the comment saying so was corrected**, because a mutation run showed the grid's limit: the oracle and the attribute the CSS reads both come from `labelZoomBand`, so a mutation to the RULE moves both sides of the comparison together and leaves all thirteen specs in that file green. The node tier kills that one. The division is now written down: node pins what the rule is, the grid pins that the stylesheet implements whatever the rule says. |
| G3 | **Three of the twenty-three bullets focus nothing, and four drawn routes have no bullet.** Against the real static archive the map draws `1 2 3 4 5 6 7 A B C D E F FS G GS H J L M N Q R SI`, and the key is MR1's ten trunks. So **Z, W and S draw no ribbon at all** (pressing one dims the whole map and highlights nothing), and **FS, GS, H and SI have no bullet**, which means the Staten Island Railway and every shuttle are unfocusable and the **S** bullet that looks like it should focus a shuttle matches none of their ids. MR1's bullets were display only, so the mismatch was invisible; MR2 is the stage that makes it bite. | **Raised as F3, then fixed on the operator's instruction**: the key is derived from the loaded route list rather than written down, and focus is membership in a ribbon's route set rather than equality with one id. "Round 2, continued" below is the whole of it, `D2n` through `D2q` hold it, and M7 and M8 are its mutations. |

**And the pass itself produced an incident worth recording**, because it is a hazard for
every future stage that runs one. The multi-agent run was given the repository's own
working tree, and its verifiers test a finding the way this project does: by applying the
mutation and seeing whether a spec dies. Two of those mutations were applied to
`frontend/systems/subway.js` and `frontend/map.js` WHILE the gates for this round were
being run against the same files, and one of them reached a commit: `dda884b` as first
written carried M3's own sabotage, `subwayFocusBase` stripped from two of the four dimming
call sites, so its gate numbers described a tree that was never the one committed. The
agent had already un-applied its `subway.js` mutation by the time this was noticed; the
`map.js` one, an Escape ladder reordered to put route focus above the station panel, was
still applied because the run was stopped mid-flight.

Both files were restored to `ecb67da`'s versions byte for byte, a stray probe spec the run
left in `tests/e2e/` was removed, every tier was re-run against the restored tree, and the
commit was amended before it went anywhere. **The lesson is the mechanism, not the
mutation**: a review that verifies by mutation must run in its own checkout, and the next
stage's pass gets `isolation: "worktree"` rather than the shared tree. Nothing was pushed
at any point.

**And on the operator's instruction that is now the standing rule rather than the next
stage's plan.** `.claude/workflows/README.md` is new and states it for every review and
probe workflow in this repository, with this incident as the evidence;
`.claude/workflows/adversarial-review.js` carries it as `RULE 0` and passes
`isolation: "worktree"` at all four of its `agent()` spawn sites, so a worktree is not
something a caller has to remember. The caller's own half is written there too: **before
any commit, confirm the working tree is what the gates ran on**, by `git status --short`
and a diff against the last commit you controlled. Every commit from here in this stage
did that, and the mutation run below used a real `git worktree` off the commit under test
rather than the tree the gates had just passed.

**What the pass checked and found clean**, recorded because a review that lists only its
hits reads as if it looked only where it found something:

- `.bul` and `.stn-label` reach no surface outside the subway key and the station labels,
  which is the MR1 G3 hazard (an unscoped `.alert-stale` repainted every popup's alert
  block, and byte-identical HTML pins could not see it).
- Route focus survives a feed hide and show, and a `setIcon` route relabel, on both
  `options.opacity` and the rendered element's inline style.
- The labels come back `aria-hidden` after a feed toggle, because the listener is on the
  marker rather than on the tooltip element Leaflet recreates.
- The pressed ring's backdrop really is `--surface` (`#panel`, measured as
  `rgb(234, 233, 233)`), so the 13.70 and 5.53 this stage claims are measured against the
  right colour rather than an assumed one. **WRONG, and round 3's F3 and F10 say how**: true
  of the ring's top and bottom edges, and its left and right sat on the neighbouring bullet's
  fill at 1.10.
- A station label overlaps a train bullet in a 2x3 pixel corner and no more: the bullet is
  lifted 21px above its anchor and the label sits 7px to the right of it, so the
  tooltipPane being above the markerPane costs the bullet's bottom three pixels. **WRONG, and
  round 3's F2 says how**: measured as a box against the anchor rather than as painted text.
  It was 44% of a bullet's route-coloured pixels and its letter.

**Both are left standing above rather than rewritten**, with the corrections attached, because
a review record edited to look consistent afterwards is worth nothing. The pattern they share
is worth more than either: both were measured with the right instrument pointed at the wrong
thing, a box instead of the ink and one edge instead of four.

### Four findings for the operator

| # | Finding | Proposal |
| --- | --- | --- |
| **F1** | **The station names collide.** Measured in the viewport over Midtown, the share of painted labels whose box intersects another one is **89% at zoom 12, 77% at 13, 40% at 14 and 29% at 15**. The design's gating (hubs from 12, all from 14) does not declutter, and neither does the reference implementation. It is visible in `after-desktop.png`, where "34 St-Penn Station", "34 St-Penn Sta" and "34 St-Herald Sq" sit on top of one another. A rider can turn the names off, which is what the Names toggle is for, and the zoom that reads best is 15. | Two remedies, neither of them in this stage's scope: **declutter**, by hiding a label whose box intersects one already placed, recomputed on `moveend`, cheapest-first; or **narrow what a hub is**, since "two or more routes" is 325 of 496 stations and the design's word suggests the big interchanges. The first is a new mechanism with its own questions (which label wins, and does it flicker on a pan); the second is a threshold the record does not fix. Shipping as specified and raising it, which is how the MTA palette question reached round 3. |
| **F3** | **Three of the twenty-three bullets focus nothing** (Z, W and S draw no ribbon against the real archive), **and four routes the map draws have no bullet** (FS, GS, H, SI). The S bullet is the sharp one: the shuttles ARE drawn, under the ids GS, FS and H, and focusing S matches none of them. | Two parts, both touching MR1's trunk set rather than this stage's five items. An **alias table**, so one bullet can name several feed route ids (S to S, GS, FS and H), which is the only way the key and the feed can disagree about an id without a rider noticing. And a **decision about SI**, which the map draws and the key does not explain. Focusing by colour instead would be worse, not better: 1, 2 and 3 share one hex, so it would light all three. **Fixed**, see "Round 2, continued" below. |
| **F4** | **The key costs twenty-three tab stops.** Measured from the top of the document, reaching the Stations button now takes **32 presses**, twenty-three of them route bullets, where before this stage it took nine. The skip link is still the first stop, so a rider heading for the station panel is unaffected; a rider heading for the Key or Stations button walks the whole key. | The standard remedy is a roving tabindex: `role="toolbar"`, one tab stop, arrow keys inside. It is about twenty-five lines and `frontend/keyboard.test.js` already has the seam for it (a keydown outside the ladder is allowed when it is named as a control's own activation, with its reason). It is a new interaction mechanism rather than one of this stage's five items, so it is raised rather than taken. **Fixed on the operator's instruction**: measured after, **10 presses**, one of them a bullet. See "Round 2, continued" below. |
| **F2** | **A yellow line on a paper casing reads 1.67 in the light theme.** Measured through the repository's own helpers, every trunk against `--paper`: N/Q/R/W **1.67**, B/D/F/M 2.52, G 2.66, L 3.11, and everything else between 4.22 and 8.31. Three trunks are under the 3:1 a non-text indicator owes against the colour beside it. **It is better than what it replaces**: before this stage the same lines were 2.5px at opacity 0.5 straight onto the basemap, so every trunk gained rather than lost. And route identity is never carried by a line alone: every train names its route in words, every popup does, and the Key panel does. | Recorded rather than fixed, because the fix is a palette change and the palette is the app's own by ruling R1. If the operator wants the three to clear 3:1 against paper, that is a change to `LINE_COLORS`, which every surface reads, and it belongs in a stage of its own rather than in a corner of this one. The dark theme's column is worse (SI 1.79, A 2.73, S 2.77, J 2.69) and is MR4's to answer, for the same reason MR1's Key glyphs were. |

### Round 2, continued: the operator's three instructions

The operator read round 2 and answered it with three instructions before the push. The
first is the worktree rule, recorded with the incident above. The other two are the
findings this round raised and did not take, taken.

| # | Instruction | What it is now |
| --- | --- | --- |
| **G3 / F3** | **The key is data-driven and ribbons are tagged by route.** A bullet for every route the app can draw a ribbon or a train for, from the loaded route list, grouped by trunk. Each drawn polyline carries the set of routes that share it, and focus is membership: Z lights the J/Z ribbon and its trains, W the N/Q/R/W ribbon and its trains. The S bullet focuses GS, FS and H together with a tooltip that says so. SI gets its bullet. A bullet whose set draws nothing is present and `aria-disabled` with a reason, and never dims the map. | `helpers.js` derives the whole model from `(routes, trainRoutes)`: the bullet universe (alias-collapsed, `S` standing for `GS`, `FS`, `H`), each bullet's focus set, whether a press would light anything, and the title. `subway.js` tags every polyline with `ribbonRouteSet`, which is the route itself plus every trunk-mate the app has no geometry for, so the J ribbon is `J+Z` and the three yellow ribbons become `N+W`, `Q+W`, `R+W` the moment a W train appears. Over the real archive the key comes out as the 22 bullets `1 2 3 4 5 6 7 A C E B D F M G J L N Q R S SI`, which is the 24 drawn routes with the three shuttle ids collapsed into one. `D2n` (Z), `D2o` (W), `D2p` (S and SI) and `D2q` (the dark bullet) hold the four cases; M7 and M8 are its mutations. |
| **F4** | **The key is an ARIA toolbar with a roving tabindex, one tab stop.** Measure the tab count to Stations after, beside the 32. | `role="toolbar"`, exactly one bullet at `tabIndex 0` (the pressed one if there is one, else the first enabled one), ArrowRight/Left/Up/Down wrapping both ways, Home and End, and disabled bullets skipped. The keydown is scoped to the key and named in `frontend/keyboard.test.js` with its reason, which is that test's existing seam for a control's own activation rather than a second page-level router. **Measured after, same harness and the same real 24-route list as the 32: 10 presses from the top of the document to the Stations button, one of them a bullet.** The arithmetic is the claim itself: 32 less 23 bullets plus 1 stop is 10, and the nine presses this stage started from are that 10 less the key. `D2r` holds the property the number follows from, which is that the key's cost does not grow with the route list. |

Two more findings came out of building those two, both from the new specs rather than from
reading the diff:

| # | Finding | Disposition |
| --- | --- | --- |
| G4 | **Focus closed transitively, and lit three routes when a rider asked for one.** The first version of membership focused the transitive closure of the ribbons a bullet touches. That is correct for Z, where the J/Z ribbon is the only thing carrying Z, and wrong for N: N's ribbon carries W because the app has no W shape, W's closure reaches Q and R for the same reason, so pressing N lit the whole Broadway trunk. Found by `D2o` on its first run, which is the direction that matters: the spec that failed was the one asserting what a rider would want, not one asserting the implementation back at itself. | **Fixed.** Membership is asymmetric on purpose now: a RIBBON lights when its own route set contains one of the bullet's ids, and the bullet's ids never grow. `focusRoutesForBullet` is the focus set and is `bulletRouteIds` under a name that reads as the decision; the closure survives as `bulletTrackSet`, which is only what the "shares track with" half of a title is written from. So pressing J and pressing Z light the same ribbon and different trains, and `D2n` asserts exactly that, because a train carries its own `route_id` where a shared ribbon cannot. M8 is its mutation. |
| G5 | **`aria-disabled` is not `disabled`, and Playwright will not click it.** Playwright's actionability check treats `aria-disabled="true"` as not enabled and refuses the click, which is not what a browser does: the element is a live `<button>` with no `disabled` attribute and a real press lands on it. A spec that accepted the refusal would be asserting the test runner's opinion instead of the page's behaviour, and would pass with the guard removed. | **`click({ force: true })` in `D2q`, with the reason written at the call.** The inertness under test is `toggleRouteFocus`'s own early return, which is what a rider's press actually meets, and M7 is the mutation that removes it. |

### Round 3: the multi-agent adversarial pass, and four rulings

The review that round 2 started ran to completion in its own worktree (the rule that incident
produced, now enforced at all four spawn sites) and returned **17 confirmed findings, five of
them high**. Fourteen agents, 21 candidates, six solo verifiers and three batched ones. **Two
of its findings contradicted claims this branch had already made**, which is recorded here as
plainly as the fixes, because a review whose own record is edited to look consistent is worth
nothing.

| # | Finding | Disposition |
| --- | --- | --- |
| **F2** | **The station name labels painted over the train bullets.** A permanent Leaflet tooltip defaults to `tooltipPane` at z-index 650 and the vehicles are in `markerPane` at 600. Verified from this stage's own committed screenshots, `after-desktop.png` against `after-desktop-names-off.png`: same commit, same frozen clock, same view, differing only by `data-labels`. One bullet lost **44% of its route-coloured pixels** and its route letter with them. **Round 2's own pass called this "a 2x3 pixel corner and no more"**, and that note was wrong: it measured the label's BOX against the anchor rather than its painted text, which is how a defect got through a review that was looking directly at it. | **Fixed.** A `stationLabelPane` at 460, above the dots a name may cover (its own station) and below every vehicle it may not. `D2x` asks which pane the tooltips are actually IN, which is the question the pane-order assertions do not. |
| **F3**, **F10** | **Both bullet rings were measured against the wrong backdrop.** Both reach 4px out from a 24px chip while `.bul-group`'s gap was the design's 2px, so each ring's left and right segments sat inside the neighbouring bullet's border box, and **round 2's own `z-index: 1` fix is exactly what makes them paint there** rather than be clipped. Against a neighbour's fill `--focus` reads **1.10** on the A/C/E blue, 1.08 on J/Z, 1.23 on 1/2/3, 1.42 on 4/5/6; `--ink` reads 2.73 and 2.69, and in the dark theme 1.67 on the yellow trunk. 1.10 is the exact number MR1 round 2 recorded as the defect an outside ring was chosen to escape, so the claim "measured against `--surface`, where `--focus` reads 5.53" held for the top and bottom edges only. | **Fixed with one number**: the gap is **6px**, the minimum that leaves a 2px surface band inside the ring and 2px outside it before the neighbour begins. Verified at the pixel level in both themes and both states: surface, ring, surface, on both sides. A third deviation from the drawn design, with the arithmetic beside it. |
| **F4** | **Route focus wrote into the freshness contract's channel with a stronger value.** `FOCUS_DIM_TRAIN` 0.15 is below `STALE_MARKER_OPACITY` 0.45, so a live off-focus train draws dimmer than a ten-minute-stale on-focus one, and an off-focus stale train lands at **0.0675** while staying clickable and screen-reader-announced. Leaflet's `setOpacity` writes nothing but `style.opacity`. Corroborated by the app's own convention: `FERRY_DOCKED_OPACITY` is 0.55, deliberately ABOVE the floor. | **The operator's ruling: keep both numbers and take the reach instead.** Focus still multiplies on the contract's own opacity and `markerOpacity` is untouched. While a route is focused every off-focus marker is `aria-hidden` and takes no pointer events, and clearing restores both. `D2v` walks it in both directions including a real click at the marker's centre; `D2w` holds it across a poll and for a train arriving mid-focus, because `setIcon` replaces the element. |
| **F9** | **`isTransferStation` counted route IDS, so skip-stop and local-express pairs were interchanges.** Marcy Av is J and Z, one line taking turns; every `["A","C"]` and `["4","5"]` stop is the same shape. All of them drew the paper transfer ring and took the `hub` class the zoom-12 band exists to keep sparse. | **The operator's ruling: count TRUNKS**, the groups `lineColor()` already knows, because sharing a colour is what being one line means. `P1f`'s subway station pin moves with it: six lines, both stations, ring to dot, nothing else in any pin. |
| **F6** | **The 6.5px paper casings shared one canvas with every other family's route lines.** Leaflet's canvas draws in insertion order regardless of LayerGroup, the eleven static loaders start together, and `/api/subway-routes` is the largest payload and re-fetches on a warming 503, so the ribbons routinely landed last and erased PATH's 33rd St line (weight 2.5), the AirTrain at Howard Beach (3) and the LIRR Atlantic Branch (2.5). Whether another family's line survived was a race. | **The operator's ruling: a `subwayLinePane` below `overlayPane`**, so the subway is the base network and no arrival order can change it. The whole pane order is documented in one block where the panes are created, and `D2u` pins it with four families drawn in shuffled insertion order. |
| **F1** | **Every station name can vanish with a green status.** `stop_times.txt` is not a required member of the subway static archive, `load_subway_station_routes` returns `{}` on any failure, and the endpoint then serves `routes: []` for all 496 stations while `subway_static_status` stays `ready`. Every station is a local, no label carries `hub`, and at the opening zoom 12 and the City preset's 13 nothing renders while the Names button reads pressed. Reproduced by execution against an archive with no `stop_times.txt`. | **Split on the operator's ruling.** The band's arithmetic was right and is not the bug: "hubs from 12" showing no hubs is the correct answer to that data. **This branch keeps the frontend's half**: with no hub anywhere the band shows every name from **13**, one zoom later than the hub band because 12 is the worst zoom the collision measurements found, and one earlier than the all band because a rider should not need 14 to see any name. Plus F7 below. **The backend half is its own branch after MR2 merges**: `stop_times.txt` becomes a required member, the rule PATH and the ferry already apply to `shapes.txt`. Its three consumers are the transfer ring, the hub label class and the station alerts matcher. **FIXED on branch `claude/subway-static-station-routes`**, one backend commit: `trips.txt` and `stop_times.txt` joined `_REQUIRED_MEMBERS`, `load_subway_station_routes` stopped swallowing, and the last-known-good index is held by a test. The section on this phase's backend obligation records what it did. |
| **F7** | **The Names toggle reported a state it did not have.** Below zoom 12 the band is `none` and two of the three view presets put the map there, so the button flipped `data-labels` with no visible change, no announcement and `aria-pressed="true"` intact. Route focus has announced since this stage was written; this control never did. | **Fixed.** It announces, and carries a tooltip in the one state a rider cannot deduce from the screen. **And writing its spec found a false sentence in the fix**: keyed on the hub count, the tooltip appeared over any hubless network and claimed no station listed its routes above a map where every station did. The band is keyed on hubs (that is what the band asks) and the sentence on the registry (that is what the sentence is about). |
| **F17** | **The disabled bullet's fade made its route letter unreadable.** `filter: grayscale(0.7); opacity: 0.55` on the whole button took the letter to between **2.17 and 3.80** against its own chip, from between 5.00 and 9.30, and the rule's comment claimed it was "as legible as any other". State conveyed by making a route name unreadable, which MR1 round 2 ruled out twice. | **Fixed the way MR1 round 2 fixed the OFF treatment**: the chip carries the state and the letter carries the route. `--chip-off` per theme, measured at 5.33 light and 8.03 dark, with the chip at 4.40 and 6.49 against its own surface. One grey cannot clear 3:1 against both surfaces, because they sit either side of mid grey, so it is a token per theme. |
| **F15** | **`layout.spec.js` A4c probed a fixed three-pixel band and this stage had spent the slack.** Lifting `iconAnchor` from `[9,22]` to `[9,21]` moved the halo's bottom edge from 4px above the anchor to 3px, so the probe passed with exactly zero margin while `style.css`'s comment went on stating the measurement as 4px. | **Fixed.** A4c finds the FIRST covered pixel and asserts the number, per system: 3 for the subway, 4 for PATH, both `iconAnchor.y - iconSize.y`. A claim that can go stale loudly instead of quietly. |
| **F5**, **F11**, **F12**, **F13** | **Four guards and assertions that measured nothing**, each confirmed by a mutation the whole suite survived. F5: nothing shrank the bullet universe while a route was focused, so the rebuild's focus-clearing branch was untested and deleting it left the map permanently dimmed with no pressed control. F11: nothing advanced a poll with focus inside the toolbar, so the signature guard's own claim was unmeasured. F12: `paperColor`'s fallback is the light theme's `--paper` byte for byte and every spec runs in the light theme, so `D2h`'s token assertion held whether or not a token was ever read. F13: `D2q` put the dark bullet at index 3 of 9, where the roving stop and the arrow wrap give identical answers with and without the guard. | **All four fixed as tests.** `D2s` shrinks the universe under a focus; `D2t` advances two polls with focus in the toolbar and also asserts the legitimate rebuild still happens; `D2h` moves the tokens and asks the resolvers again; `D2q` presses the two keys either side of the dark bullet. |
| **F14** | **Leaflet's Tooltip defaults to `opacity: 0.9` and writes it as an INLINE style**, which no stylesheet rule can reach, so every station name drew at 90% group alpha while `A1z3` measured its ink at 100% and reported a ratio about 23% better than the drawn one. The `.stn-label` rule enumerates five tooltip defaults it takes back off; this was the sixth. | **Fixed** with `opacity: 1` in the `bindTooltip` options, because it is not a rule. `D2x` holds it beside the pane, since both are properties of that one call. |
| **F8** | **Partly refuted, and this is why findings get verified.** The claim was that `stationMarkStyle`'s local branch dropped a hit-radius invariant: 4.75px before, 3.5px after. The arithmetic is right and the framing is not. In the vendored Leaflet, `_containsPoint` is `radius + (stroke ? weight/2 : 0) + renderer.tolerance`, which is **also the drawn outer radius**, so the hit radius has always equalled the drawn one and there was no separate invariant to re-establish. The local dot is smaller because the design specifies a smaller dot. The proposed remedy, a `tolerance` on the renderer, would have silently grown the hit radius of PATH, railroad and ferry stations, three systems this stage's pins exist to hold still. | **Not changed.** The residue worth recording: tapping a local station is harder than before, by the design's own call, and the A2 footnote's marker-versus-panel equivalence already covers the target-size question for every mark on this map. |
| **F16** | **The Key panel's station row draws two marks and its caption describes one thing**, so a rider sees the difference and does not learn what it means. Real, and not fixable here: a second row moves `A1x`'s pinned row count, and rewording the caption drops an exact sentence, which is precisely what `P1e` forbids ("no stage takes a sentence away from this panel, additions only"). Tried both, put both back. | **Recorded for the stage that owns the Key panel**, with the reason written at the markup. The same discipline as the "ALL LINES" button. **CLOSED in MR4 round 2**, by the ruling that owns the panel: the row is split, the dot keeps its caption byte for byte, and the ring gets the name this finding asked for. Both obstacles were moved rather than worked around, `A1x` to 17 and `P1e` to an ordered equality. |

**Round 3's own fixes went in half guarded, and the mutation run is what said so.** Three of
them survived their first mutation: the label pane (**M9**), the 6px gap (**M10**) and the
disabled chip's drawn treatment (**M19**), with a fourth (**M14**) dying only at the node tier.
`D2x`, `D2y`, `D2z` and `D2z1` exist because of that, and the lesson is the one this stage
keeps relearning: **a fix is not finished when it works, it is finished when reverting it
fails something.**

**And writing those specs found two more defects in round 3's own work**: the Names toggle's
false sentence above, and a `typeof stationRegistry` guard that took the whole page down,
because `typeof` on a module-scope `const` in its temporal dead zone THROWS rather than
answering "undefined" (it answers that only for a name never declared at all). This file had
already been taken down that way once this stage.

### Mutations

Each in a real `git worktree` detached at the commit under test, the mutation applied
alone, the named tier run against it, and `git checkout -- .` between runs; the main tree
was verified clean before and after the whole run. That is the incident's lesson applied to
my own probes and not only to the review agents': the earlier runs of M1 to M6 used a copy
of the tree (`git ls-files --cached --others --exclude-standard`, so a new untracked file
was included), which is enough isolation only as long as nothing else is running.

| # | Guard reverted | Result | Killed by |
| --- | --- | --- | --- |
| M1 | the yellow trunk not drawn last (`trunkDrawOrder` returns its input) | **killed** | node `MR2: the yellow trunk is drawn last`, and `subway.spec.js` **D2g** once its fixture stopped handing the payload to it in draw order (H14) |
| M2 | route focus rebuilding the ribbon layers instead of changing their opacity | **killed** | `subway.spec.js` **D2d**, on layer identity: every `L.Util.stamp` is unchanged across a focus and a clear |
| M3 | the freshness contract's dimming lost under the new icon (the age dropped at all four call sites) | **killed** | `subway.spec.js` **D2e**, and `smoke.spec.js` **C2b**, **C2c**, **C2h** and **C2m** |
| M4 | the labels not gated by zoom (`.stn-label { display: block }` unconditionally) | **killed** | `subway.spec.js` **D2j**, which reads COMPUTED display rather than the attribute, so a band written correctly over a rule that never matched still fails |
| M5 | the transfer ring drawn for single-TRUNK stations (`isTransferStation` at `>= 1`) | **killed** | node, four tests, and `subway.spec.js` **D2i**, **D2j**, **D2z** and pin **P1f**. Re-cut in round 3: the original counted route ids and F9 replaced that line, so the same guard is probed in its new form and M13 is the other half. |
| M6 | a circular bullet (the body rect replaced by a circle) | **killed** | `subway.spec.js` **D2k**, which is `D1l`'s sibling on the map, and pins **P1f**, because the subway train's HTML is pinned and this is the stage that regenerated it |
| M7 | a bullet with an empty set dims the map (the `aria-disabled` early return in `toggleRouteFocus` deleted) | **killed** | `subway.spec.js` **D2q**: every ribbon and every train is compared before and after the press, and `#page-announce` is required to have said nothing |
| M8 | focus is the transitive closure again (`subwayKeyModel` hands `bulletTrackSet` to the focus field), which is G4 put back | **killed** | node `MR2 G3: the whole key is grouped by trunk`, and `subway.spec.js` **D2n** and **D2o** |
| M9 | the labels go back to Leaflet's default `tooltipPane`, above the vehicles (F2) | **killed, second time** | `D2x`. **It survived the first run**: `D2u`'s pane-order assertions stay correct with the `bindTooltip` option deleted, because the panes are still created. |
| M10 | `.bul-group`'s gap back to the design's 2px, so both rings land on the neighbour's fill (F3, F10) | **killed, second time** | `D2y`. **It survived the first run** too: nothing in the suite sampled the pixels beside a ring. |
| M11 | the ribbons back on the canvas every other family shares (F6) | **killed** | `D2u`, and `D2m`, which would otherwise be reading the wrong canvas entirely |
| M12 | an off-focus marker stays in the accessibility tree and keeps taking clicks (F4) | **killed** | `D2v` and `D2w` |
| M13 | the station predicate counts route ids again (F9) | **killed** | node `MR2 F9`, and `D2i`, `D2j` and pin `P1f` |
| M14 | with no hub anywhere the band shows nothing instead of everything from 13 (F1) | **killed** | node `MR2 F1`, and `D2z` and `D2z1`. **Only the node tier caught it on the first run**, which is why the two browser specs exist. |
| M15 | the rebuild stops clearing a focus whose bullet has gone (F5) | **killed** | `D2s` |
| M16 | the key rebuilds every poll (F11) | **killed** | `D2t` |
| M17 | the token resolvers can only ever return their literal fallback (F12) | **killed** | `D2h` |
| M18 | the roving tabindex and the arrow keys stop skipping disabled bullets (F13) | **killed** | `D2q` |
| M19 | the disabled chip keeps its inline route colour, so the token never lands (F17) | **killed, second time** | `D2q`'s drawn-treatment block. **It survived the first run**: nothing asserted the disabled bullet's painted colours. |
| M20 | the Names toggle stops announcing (F7) | **killed** | `D2j` |
| M21 | the tooltip goes back to Leaflet's default 0.9 group alpha (F14) | **killed** | `D2x` |

**Twenty-one mutations, all killed, and the node tier takes five of them on its own**: the
draw order, the station predicate, the zoom band, the no-hub fallback and the key's grouping.
That is five more than MR1 managed and the reason is the shape of this stage rather than
better testing, because MR2's decisions are arithmetic over data the page already has. The
sixteen that only the browser kills are all wiring: which function a call site passes an age
to, which pane a tooltip is bound to, whether a renderer repaints, whether a stylesheet rule
matches, whether a click handler returns early, what a canvas actually painted.

**Three of round 3's fixes survived their own mutation on the first run** (M9, M10, M19) and a
fourth was caught by the node tier alone (M14). Every one of those four was a fix whose
correctness I had verified by hand, measured, and written a comment about, and none of them
had a test that would have noticed it being reverted. That is the sharpest thing this stage
learned and it is worth more than any single finding in the table above.

**One failure in the run was not the mutation's**, and it is recorded because a mutation
table that launders a flake is worse than one with a gap in it. Under M6, a batch running
`pins.spec.js` and `subway.spec.js` together on two workers also failed **P1m**, the ferry
pin, which has nothing to do with a subway icon. It did not reproduce: `pins.spec.js` alone
with M6 applied fails **P1f** and nothing else, and P1m passes. It is the same local server
contention this stage met once before, and the `pin()` helper cannot be the cause, because
in assert mode it only reads the golden.

### Before and after

`docs/reviews/map-redesign/mr2/`, from the hermetic harness at the frozen clock over the
REAL subway geometry, centred on Midtown at zoom 13, so the pair shows a network rather
than the fixture's two shapes.

| | 1280 | 375 |
| --- | --- | --- |
| before | `before-desktop.png` | `before-375.png` |
| after | `after-desktop.png` | `after-375.png` |

Two more, because two of this stage's five items cannot be seen in a still of the default
state: `after-desktop-names-off.png` is the same view with the Names toggle off, which is
the ribbons and the marks without the label noise F1 describes, and
`after-desktop-focus-4.png` is route focus on the 4, where the Lexington Avenue trunk
stands at full strength and every other trunk is at 0.18 with its casing gone.


## Stage MR3: commuter rail

Branch lines in the agencies' own colours, one paper square for every rail station, and
`railTag` with the brief's 3.1 provenance states, for LIRR, Metro-North and NJ Transit
together. Three families, one grammar: before this stage they drew three different station
marks and two byte-identical copies of one train glyph with no shared helper between them.

### The pins, and why four of them are new

P1h, P1i, P1j and P1k already held every rail mark byte for byte, and MR3 is the stage that
deliberately moves them: their MARK halves are regenerated and the before and after are
below. Their POPUP halves are NOT regenerated, for the reason MR2 gives about its own: the
popups are stage MR5, so a rail popup that changes here is a defect and that claim stays an
assertion.

What MR3 reaches past on its way is the rest, and four pins landed first to say so.

| Pin | What it holds |
| --- | --- |
| **P3a** | A rail station's registry entry, field by field, for all three agencies, because MR3 rewrites the two files they are registered from. It pins the pane as DRAWN rather than as configured, which is the opposite choice from P2a's and for the same reason: MR2 kept the subway a circleMarker, so its own `pane` option was the invariant; MR3 turns a canvas circleMarker whose renderer put it on `stationPane` into an `L.marker` whose own `pane` is `stationPane`, so what holds across the stage is the answer, not the route to it. The marker's CLASS is deliberately not pinned: that IS the restyle. |
| **P3b** | The station panel for LIRR Jamaica, as a rider reaches it. Its arrivals read "Babylon Branch", which is `nameFor`'s answer reaching the panel, so it fails if the restyle rebuilds the station descriptor and loses the route-name resolution. |
| **P3c** | The alerts join for that station, on BOTH surfaces, with its own alert list. Two alerts, one per path into F11's union: the stop-scoped one reaches Jamaica by its own id and the route-scoped one ONLY through the arrivals board, because `railroadStops` serves no routes field at all. A third alert on a route that does not serve Jamaica is the negative. |
| **P3d** | The position ladder's five states in the F01 world, by count and by words: 60 reported unqualified, 11 reported qualified, 6 estimated, 59 placed, 136 drawn, and the fifth state as the 24 the status line reports, which is the only place a train with no marker exists on this page. |

**P3b and P3c caught a pin measuring the wrong thing, before a line of the stage was
written.** "jamaica" matches two stations on the fixture, the AirTrain's and the LIRR's, and
the AirTrain row sorts first: `.first()` pinned "Jamaica (AirTrain)" under the key
`panel/lirr`, with no alerts and a headway sentence, a filled-in golden that would have held
through any change to the surface it exists to watch. The picker names the row it wants now
and both specs assert the heading says LIRR before pinning anything.

### What the marks were, and what they are

The before is the golden as `origin/main` served it; the after is what this branch
regenerated.

| | before | after |
| --- | --- | --- |
| rail train | a 16x16 rounded square, `<rect x=2 y=2 w=12 h=12 rx=1.5 fill=#fff stroke=#5d4037 stroke-width=2.5>` when hollow, the fill and stroke swapped when filled, colour from a HASH of the route id | a two-part tag, 35 to 45px wide by 30 tall, anchored `[w/2, 21]` so its head sits on the rail: an agency block ("L", "M", "NJ") and a branch block ("BAB", "HUD", "NEC") in Archivo 800 8px, the body solid or outlined by provenance, the head a filled or outlined chevron rotated to the bearing, or a dot |
| rail station | LIRR and Metro-North a canvas `circleMarker` `radius 3.5 color #334155 weight 2.5 fillColor #fff`; NJ Transit a 12x12 `<rect ... fill=#334155 stroke=#fff>` | all three an `L.marker` on `stationPane`, `<rect x=6 y=6 w=8 h=8 fill=var(--paper) stroke=var(--ink) stroke-width=1.6>` in a 20x20 box, identical byte for byte across the three agencies (D3c asserts that, not just that each is a square) |
| branch line | one polyline, `weight 2.5 opacity 0.5`, colour from the same hash | a casing and a line per branch, `var(--paper)` at `weight 5 opacity 0.9` under the agency's own `route_color` at `weight 2.5 opacity 1`, round caps, added back to back |
| rail station name | none: a canvas `circleMarker` has no element to hang a tooltip on | a permanent tooltip on `stationLabelPane`, class `stn-label rail`, from zoom 11, under MR2's Names toggle, never `hub` |

### The findings

| # | Finding | Disposition |
| --- | --- | --- |
| **N1** | **The agencies publish an ink that does not always work.** Of the 19 `(route_color, route_text_color)` pairs the two MTA railroad feeds publish, only ELEVEN carry 4.5:1 as published. Measured 2026-09-19 over the live archives: Babylon 3.71, Oyster Bay 2.92, Long Beach 2.98 and Hudson 3.65 are readable fills under an unreadable ink; the New Haven family's shared `EE0034` is a fill NO ink can rescue (white 4.48, dark 3.88), and it is four of Metro-North's six routes. Metro-North's positions are not age-gated, so those four draw with a SOLID body and their code on that block: the common case on that railroad, not a corner. | **The ink is preferred, not trusted, and the fill moves only where nothing else can work.** `railBranchPaint` returns the pair: the feed's ink where it clears, the computed one where a readable fill carries an unreadable ink, and where neither clears, the fill scales 1% toward black (`EE0034` to `#ec0033`, white 4.48 to 4.55), which is the same hue-preserving scaling `readableInk` already uses for text and the remedy the note at `railroadColor` already names for this class ("a fill that has to move rather than an ink that has to be chosen"). **THE ROUTE LINE IS NEVER MOVED**: only the 24-by-13 block with 8px type on it. The three counts are asserted, so a change that started moving every fill is visible. **IT ALSO QUALIFIES A SENTENCE** `claude/railroad-route-colors` put in two docstrings, that "a renderer can trust a railroad text_color": it can be preferred, and the four ratios are why it cannot be trusted. |
| **N2** | **The 5px rail casing is on the SHARED canvas, which is MR2's F6 in a new costume.** Leaflet's canvas draws in insertion order, so a rail casing arriving after PATH's 3.5px line, the AirTrain's 3px or the ferry's 2px covers it where they overlap. The subway got its own pane for exactly this and nothing makes the rail casing safe against those three; the eleven static loaders land in whatever order their responses do. | **Built as specified ("casing and line per branch on the existing canvas") and the exposure written down rather than met on a map.** D2u gained NJ Transit, which was missing from its family list entirely, and its shuffle now draws each family's casing as well as its line; it asserts that all four non-subway families share one pane and that the thinnest of them is thinner than the casing over it. **A pane at 395, between `subwayLinePane` and `overlayPane`, closes it in one line** and is the operator's call, not this stage's. |
| **N3** | **Row 6 of the 3.1 table cannot be drawn as written.** The table says an age-gated row with no clock is dimmed; dimming is `markerOpacity`'s, `markerOpacity` reads an age, and the whole content of that row is that there is no age. `staleAge(null)` is false. | **The body and head halves are obeyed and the opacity half is not, argued rather than dropped.** Dimming it would tell a rider "this is old" about a train whose age the same tag has just said is unknown. The pessimism the row exists for is carried where it belongs: an outlined body and an outlined dot, which is the strongest "do not trust this" the tag can draw. It is the one place the body goes past `railroadHollow`, and the freshness contract is why: clause (c) is an anomaly in the contract's own words. `railtag.test.js` asserts the deviation as a deviation, so a later stage that decides to dim it has to come here. |
| **N4** | **axe cannot judge the tag's type.** With the tags on the map its color-contrast rule reported 37 findings at 1280 and 17 at 375, all "background color could not be determined because it is overlapped by another element": a tag is 35 to 45px wide where the square was 16, so at regional zoom the tags overlap each other. `aria-hidden` does not silence it and should not, because a sighted rider still sees the type. | **A named shape with a decider, which is A1w's own protocol.** `a11y.spec.js` **A1z4** reads each tag's printed ink and the fill of the block under it off the DRAWN page, in both themes, requires AA, and asserts that every `svg.rail-tag` belongs to a rail tag marker so the exception cannot widen. `railtag.test.js` measures the same pair in node over all 31 published colour pairs, which is what found N1. ACCESSIBILITY.md carries both statements and `statement.test.js` A4 caught the omission. |
| **N5** | **`/api/njt-routes` did not serve `route_short_name`**, so the brief's stated source for NJ Transit's branch code ("the feed's `route_short_name`") was not reachable and every NJ Transit tag would have read its route id: "9" for the Northeast Corridor. `njt_static` has parsed the column since 15c and the builder dropped it. | **One additive backend change**, the same shape `claude/railroad-route-colors` used for the colours: `NjtRoute.short_name`, None default, carried by the builder and served by the endpoint. The two exact-dict guards in `test_api.py` are updated rather than relaxed, which is what they exist for. |
| **N6** | **Two neutrals are on screen for an unknown route.** The tag and the line take the README's stated `#6d6e71`; `njtColor`'s older `#4a4e69` still reaches the NJ Transit popup head, which route 17 (the event-only Meadowlands line, never on `/api/njt-routes`) is the live example of. | **Left, deliberately, and named.** The popups are stage MR5 and P1k pins this one byte for byte, so changing `njtColor` here would break a pin that must hold. MR5 is where the two converge; the pin is what proves the popup did not move in the meantime.  **PAID IN MR5, structurally.** The head resolves through `railBranchColor(njtBranch(t).color)` now, which is the same resolver and the same published paint the tag reads, so the two cannot drift again; two constants that happened to agree would have been a coincidence waiting to be broken. Asserted as a SOURCE fact in `railtag.test.js`, and that is the point rather than laziness: no fixture world has an unknown NJ Transit route, so the drawn page cannot tell the two neutrals apart and every browser gate stays green either way. |

**Three guards fired during the wiring and all three were right**, which is worth recording
because each was a place the stage was about to diverge quietly. `markers.test.js` caught a
raw `L.marker` for the station square, written on the subway's reasoning (a station carries
no accessible name) which holds only because a canvas circleMarker has no element to name;
NJ Transit's squares have gone through `labeledMarker` since 15c, and these do now, with
`railroadStationName` added beside `njtStationName`. `positions.test.js` caught the glyph
rule leaving `railroad.js`: the CALL moved into `railTagState` and the RULE did not, since
that function calls `railroadHollow`, and the guard follows the chain now and asserts that
`njt.js` reaches the same table, which is what makes "one grammar for three families"
checkable rather than asserted. `statement.test.js` caught N4's exception missing from
ACCESSIBILITY.md.

### Round 4: the multi-agent adversarial pass, and what it cost to read it

Five finder dimensions over the written production diff (frontend and backend), triage, then
verifiers, every agent in its own worktree under the standing rules. **It found two criticals
that had shipped into the written diff**, and both are the shapes this phase was told to look
for.

| # | Confirmed | What was wrong | The fix |
| --- | --- | --- | --- |
| **#11** | **the branch casing passed the literal string `"var(--paper)"` to a CANVAS renderer** | Canvas2D resolves no custom properties. `ctx.strokeStyle = "var(--paper)"` is not an error and not a fallback: the assignment is a SILENT no-op that leaves the context holding whatever colour it stroked last, so every rail casing drew in the previous branch's ink. Proved by executing the assignment in-page on a context primed with `#123456`: the value stayed `#123456`, while the subway's casing correctly passed `#f3f2f2`. `shared.js` has carried `paperColor()` since MR2 for exactly this, with the reason written above it, and MR3 walked past it in two files. | `paperColor()` in both rail loaders. `rail.spec.js` D3e asserted the DEFECT (`toEqual(["var(--paper)", "var(--paper)"])`) and now asserts the resolved hex three ways: equal to `paperColor()`'s live answer, matching `/^#[0-9a-f]{6}$/`, and containing no `var(`. |
| **#3** | **`railroadLinePane` at 395 closed N2 against PATH and reopened MR2's F6 INSIDE the new pane** | All three rail families shared one canvas renderer, and a canvas draws in insertion order. Where two branches share track, the later branch's 5px casing lands after the earlier branch's 2.5px line and erases it. | Two passes per loader was the first fix and **it was not enough**, which the draw chain said out loud: with one renderer the hermetic world produced `[5, 5, 2.5, 2.5, 5, 5, 5, 2.5, 2.5, 2.5]`, so all three NJ Transit casings stroked after both railroad lines. `/api/railroad-routes` and `/api/njt-routes` are two endpoints landing in a race and neither loader can order the other's marks. **So the tier is the pane:** `railroadCasingPane` at 394 for every casing, `railroadLinePane` at 395 for every line, one `railDrawRibbons` for all three families. D3e reads both renderers' draw chains and asserts each holds one weight; D2u's four tiers hold the pane relation across a shuffled insertion order. |

**Two of the panel's refutations were invalid, and the reason is a defect in the review tool
rather than in the reviewers.** Both verifier worktrees were at `origin/main`, where this
branch does not exist, so their evidence read "the diff is empty" and "`bindRailStationLabel`
does not exist" and both findings came back confident **REFUTED**. Both were re-verified by
hand and both were real: they are #11 and #3 above, the two criticals of this round. The only
reason they were not lost is that a refutation of that shape looked wrong; a refutation that
reads *the code you describe is not there* is indistinguishable from a correct refutation of a
hallucinated finding, which makes it the worst output a review tool can produce. It deletes a
real defect and looks like diligence doing it.

**Fixed structurally, as RULE 0b of `.claude/workflows/adversarial-review.js`.** A worktree is
created at the default branch unless something puts it elsewhere, and nothing did. Now the
caller passes `{commit, branch}`; every agent's prompt opens with a preflight that runs
`git rev-parse HEAD`, `git checkout --detach <sha>` if it does not match, and
`git diff --stat <range>` to prove the diff is non-empty; every schema REQUIRES the agent to
echo back the sha it read and the file count it saw; and the script discards the output of any
agent whose sha does not match. A discarded verdict set makes its findings **UNVERIFIED**,
never refuted. A discarded finder dimension is logged as lost coverage, and a triage read from
the wrong tree is thrown away with every candidate going forward unmerged, because triage
re-reads the code to correct each finding's location and a triage on the wrong branch would
"correct" every real finding into a drop. `.claude/workflows/README.md` carries the rule for
every review and probe workflow in the repository.

**And a test in this stage's own diff asserted a defect.** `railtag.test.js` held
`assert.equal(Math.round(railTrainBearing({ ...anchored, direction: "Inbound" })), 180)` over an
anchor pair whose true azimuth is 0: `prev_lat`/`prev_lon` is where the train WAS and
`latitude`/`longitude` is where it IS, so the pair is already travel-directed and reversing it
pointed every inbound NJ Transit train backwards. A test that asserts a defect is worse than no
test, because it makes the bug load-bearing: the fix now fails the suite and the suite reads
like the authority. It is recorded here rather than quietly corrected. The same file's slices
were two-point chords rather than the `{points, cum, s0, s1}` shape `computeRouteSlice` returns,
which is how the geometry half of the same defect got past them.

### Round 4: the operator's rulings

| | Ruling | What it changed |
| --- | --- | --- |
| **R-a** | **Drop `railDirectionReverses` from both geometry paths.** `s0` to `s1` and the served anchor pair are travel-directed by construction. The served bearing stays. Correct `railtag.test.js:497` to the true value and record that a test asserted the defect. | The function and its export are gone. `railTrainBearing` reads `pointAtArcLength(slice.points, slice.cum, slice.s0)` to the same at `s1`, which is the INTERVAL the train occupies rather than the whole branch: verified on a bending polyline that the two legs give 0 and 90 and the end-to-end chord gives neither. On the real F01 capture the three inbound anchored rows moved from 104/78/32 to 284/258/212. The corrected test asserts 0 and says in place why the 180 was wrong. |
| **R-b** | **Keep N3 general; replace the absent-field gate with an explicit table matching contract 3.3.** LIRR, subway, PATH, ferry, buses and NJT gated, Metro-North not, keyed by the family each layer knows, with a test that every family is listed. The header-less subway case is intended: assert it, and say in the erratum that `/healthz` keeps the operator rule while the rider sees "age unknown" and dimmed. | The first cut read `!UNDATED_SYSTEMS.has(row.system)` and was **right by accident**: bus, subway, PATH and ferry rows carry no `system` field at all, so `has(undefined)` was false and four families were gated as a side effect rather than by a decision. `OBSERVATION_GATED` is the 3.3 table transcribed; `observationGated` reads the row's own system first, which is why "railroads" has no row of its own and its two systems disagree. `positions.test.js` scrapes every `vehicleMarkerAge("<key>"` call site from `systems/` and asserts the set is exactly the six sources and that every family one of them can name has a row, in both directions against `UNDATED_SYSTEMS`. The header-less subway row is asserted as intended, and the erratum says why the two surfaces disagree on purpose. |
| **R-c** | **Belmont Park is BEL.** Add it, and correct the brief with a dated note rather than editing the claim away. | The live feed serves LIRR route 11, Belmont Park, colour `60269E`, against the brief's "There is no route 11". Thirteen LIRR branches now, and the count assertion in `railtag.test.js` is what caught it. The brief keeps its sentence and carries a dated erratum under it. |
| **R-d** | **The frontend refetches a routes payload once with cache "reload" when the field it needs is missing, then falls back to the id.** Record a version stamp on static-derived endpoints as a follow-up. | `fetchRoutesPayload(url, field)` in `shared.js`, with the pure predicate `staticPayloadHasField` in `helpers.js` so node can ask it. Both static route endpoints are served under an hour-long cache, so the deploy that adds a field ships a frontend reading it against a response from before the backend rolled: well formed, field absent, nothing errors, and every NJ Transit tag prints "9" for up to an hour. The predicate is keyed on **some** entry carrying the field rather than every entry, which is the whole subtlety: route 17 (Meadowlands, event-only) never reaches the endpoint with a short name, so "every" would re-read past the cache forever. A null value counts as absent, because a half-rolled nullable column looks exactly like an unknown field. |
| | **The follow-up R-d asks for** | A version stamp on the static-derived endpoints, so the frontend can ASK whether a payload predates a field instead of inferring it from absence. Not this stage's: it is a backend change plus a frontend read, and the refetch is correct without it. |

**Six more repairs in the same round**, each with a mutation below.

- **The Names toggle's sentence read only the subway's band.** The toggle hides commuter-rail
  names too (`:root[data-labels="off"] .stn-label.rail`), and the two bands disagree: rail names
  show from zoom 11 and the subway's first band opens at 12. At zoom 11 the button said "none at
  this zoom, zoom in to see them" while the press had just switched off every rail name on
  screen, which is the one thing that sentence exists to prevent. `namesToggleAnnouncement` is
  variadic now, and no bands at all reads as "on" rather than "none", because `[].every()` is
  vacuously true and the obvious spelling would have a caller that passes nothing claim the zoom
  shows no names.
- **`paintZoomBand`'s sentinel counted rail labels as subway ones.** It asks "has the subway
  loaded, and does it publish any interchange", and MR3 put about 300 commuter-rail labels in the
  same `.stn-label` class. Counted together, a page with rail labels and no subway labels reads
  as "subway loaded, zero hubs", which is `LABEL_NO_HUB_ZOOM`'s degraded band: every subway name
  from 13 instead of hubs from 12, on a map whose subway index is merely still in flight.
  `:not(.rail)` on both counts.
- **`railTagState.dim` was a second expression of the dimming rule that nothing read.** Every
  rail marker's opacity comes from `markerOpacity(vehicleMarkerAge(...))` applied to the marker
  itself, on the apply path, the stale sweep and at creation. `dim` rotted the way an unread
  field does: the paragraph above it argued at length for `dim = false` on row 6, and R-b's
  predecessor ruling made row 6 dim. The field and the `age` parameter are gone; the table's
  opacity column is asserted in `railtag.test.js` against `markerOpacity` itself, which is
  stronger, and the returned keys are asserted so the field cannot come back silently.
- **`railTagHeadingTrusted` was dead AND wrong.** Exported, never called, and its rule
  (`kind !== "unknown"`) disagrees with what `railTagState` actually does for a retained row
  (`before != null`). Deleted.
- **`markerAge`'s docstring contradicted the code.** It said "an unknown observation age dims
  nothing on its own, because positionQualifier says it in words instead", which is exactly what
  the N3 ruling reversed.
- **`njtTagState` dropped the `now` it was passed.** It reached only the age term, so with the
  age term gone the parameter would have been unread while the stale sweep passed a pinned clock
  and got the live one back. Threaded into `njtPosition`, as `railroadPosition(train, now)`
  always was.

**And two specs in this stage's diff were weaker than their titles.** `rail.spec.js` D3b is
titled "a retained train is drawn as the state it was in, **dimmed**" and asserted only the two
shapes; worse, its world could not have dimmed, because it carried `systems: railWorld().systems`
(the FRESH blocks) beside rows stamped `retained`, a payload saying at once "this generation
could not be refreshed" and "the last successful poll was a moment ago". The world now ages the
blocks with the retention, which is what the backend serves, and the spec asserts 0.45 off the
element's inline style for every retained row and 1 for the before. `subway.spec.js` D2u's
shuffle probe drew the rail families on `lineRenderer`, which is not the renderer production
uses, and its comment called the resulting overlap an accepted cost; it also used the literal
`"var(--paper)"` as a canvas colour, the very defect of #11.

### The mutations

Each in its own worktree detached at the commit under test, applied alone, with the main
tree verified clean before and after.

**AND THE MUTATION HARNESS ITSELF WAS BROKEN, which round 4 found by having a mutation
survive that could not have.** M9 puts the literal `"var(--paper)"` back on the casing, which
`rail.spec.js` D3e asserts against three ways; it came back GREEN. The cause is two lines that
are individually reasonable: `tests/e2e/playwright.config.js` sets
`reuseExistingServer: !process.env.CI`, and `tests/e2e/serve.js` resolves its document root from
its own `__dirname`. So a static server left running from the MAIN checkout is silently reused
by a worktree's run, and every browser assertion then reads the **unmutated** frontend. A
mutation that cannot die is the one failure mode mutation testing exists to catch, and this
harness had it for every browser-tier mutation run from a worktree while a server happened to be
up. The node tier was never affected, because it loads files from its own cwd.

The driver now kills whatever holds the port **by port rather than by command text** (a `pkill`
on the server's path also matches the shell running the driver, which is how the first fix
killed itself), refuses to run a browser gate while the port is still held, and sets `CI=1` so
the worktree starts its own server from its own tree. Every row below was re-run under the
fixed driver. **M6 is retired rather than re-run**: it reverted the inbound reversal, and the
operator's ruling R-a removed the reversal, so its replacements are M12 and M12b, which put it
back on each of the two geometry paths.

**M17 survived its first run too, and that was a real gap in the guard rather than the
harness.** The keys assertion asked ONE row of the table, so a `dim` field restored on the
estimated branch alone passed it. The table has five return sites; every one is asked now, and
each is also asked whether an age handed to it changes its answer, because `railTagState.length`
is 1 (parameters after the first default do not count) and arity alone cannot catch a fourth
parameter being read.

### Round 4, after the push: the sentinel six specs share

**CI found one this branch's own full local run did not, and it is MR3's.**
`crosslink.spec.js` A3a went red with *"the fixture must contain a placed railroad train"*
over an empty `railroads` map: a premise assertion, not a claim about the cross-link.

Six specs open the page the same way, by polling until
`document.querySelectorAll(".leaflet-marker-icon").length > 5`, and every one of them then
reads a VEHICLE registry on the next line. The count was standing in for "the vehicles have
landed". **MR3 broke the stand-in** by turning the LIRR, Metro-North and NJ Transit stations
into markers: measured on the fixture world, 23 marker icons, **5 of them rail stations**, so
`> 5` is now satisfied by the stations plus a single vehicle of any kind. A page with six
buses and no railroad passes the gate and then fails on an empty `railroads`. The margin went
from six vehicles to one, and the local suite happened to win the race every time.

Two fixes, both minimal and both at the root. The sentinel counts
`.leaflet-marker-icon:not(.rail-stn-marker)` in all six specs, which is the class every rail
station icon carries and no vehicle does; it now means what it was written to mean and
slightly more, since three NJ Transit station squares had been counted as vehicles since 15c.
And `crosslink.spec.js`'s own `open()` additionally waits for `railroads.size > 0 && trains.size
> 0`, the two registries its specs actually read: a premise assertion that can fail on a race
was never testing anything, it was reporting one, so it becomes a wait.

**This is the same shape as `paintZoomBand`'s sentinel earlier in this round**, and that is the
lesson worth carrying into stage MR4: MR3 put about 300 new markers and 300 new labels into
classes that existing code was counting, and every count over a class MR3 widened has to be
re-read. Two were found by different means, one by a node-adjacent browser spec and one by CI,
and neither by the adversarial panel.

### The mutations, round 4

| # | Guard reverted | Result | Killed by |
| --- | --- | --- | --- |
| **M9** | the casing's colour back to the literal `"var(--paper)"` | **killed** | D3e's three-way assertion: equal to `paperColor()`'s live answer, matching a hex, containing no `var(`. **This is the one that exposed the harness**: it survived until the driver stopped reusing the main checkout's server |
| **M10** | the casing back on `railroadLineRenderer` | **killed**, both specs | D3e ("no rail casing reached the casing canvas") and D2u ("lirr is on the rail panes") |
| **M11** | the bearing read end to end over the whole branch instead of over `s0` to `s1` | **killed** | the bending-polyline case, where the two legs read 0 and 90 and the chord reads neither |
| **M12** | the direction word turns the slice again | **killed** | the all-directions loop over a north and a south slice |
| **M12b** | the direction word turns the served anchor pair again | **killed** | the corrected `railtag.test.js:497`, which is the line that used to assert this |
| **M13** | one family dropped from `OBSERVATION_GATED` | **killed** | the every-family-listed test, which scrapes the call sites from `systems/` |
| **M14** | `staticPayloadHasField` keyed on EVERY entry rather than some | **killed** | the route-17 case: a payload where one entry has no short name is a payload from a backend that knows the field |
| **M15** | the Names sentence reads only the first band | **killed** | the four variadic cases in `subway.test.js` |
| **M16** | `paintZoomBand`'s sentinel counts rail labels again | **killed** | D3g, on a world with rail stations and no subway ones: the band reads "all" instead of "hubs" at zoom 13 |
| **M17** | the table grows a second dimming rule (`dim`) again | **killed after the guard was widened** | the per-row keys assertion and the "an age changes nothing" pair |
| **M18** | `njtTagState` drops the clock it was passed | **killed** | the threading assertion in `positions.test.js` |
| **M19** | the re-skin gate stops comparing `headingTrusted` | **killed** | the skin-key coverage test, which reads the icon's inputs off `railTagIcon` itself |
| **M20** | Belmont Park removed from the code table | **killed** | the count assertion, which is what found it in the live feed |
| **M21** | NJ Transit draws its own casing-and-line pair on the line renderer again | **killed**, both specs | D3e ("the line canvas holds only lines") and D2u ("njt is on the rail panes") |


| # | Guard reverted | Result | Killed by |
| --- | --- | --- | --- |
| **M1** | the chevron filled for a placed train | **killed**, both tiers | `railtag.test.js` row 4, and `rail.spec.js` D3a on the page |
| **M2** | the body solid for an estimated train | **killed**, 3 node failures | row 3, the body-is-railroadHollow's-answer tie, and the markup test |
| **M3** | the 6.3 erratum reverted, so an undated age-gated row draws bright again (`staleAge` drops its `AGE_UNKNOWN` clause) | **killed**, 2 node and 2 e2e failures | the header-less subway case, row 6, D3a and D3b. **Restated in round 4**: it used to revert `dim = false` inside the table, and the table no longer carries an opacity column, so the mutation now reverts the rule itself |
| **M4** | Metro-North gated like every other system | **killed**, node and e2e | row 7's policy case and D3f. **Restated in round 4**: it is now one row of `OBSERVATION_GATED`, so the every-family-listed test catches it as well, in both directions against `UNDATED_SYSTEMS` |
| **M5** | a circle drawn for a rail station | **killed**, both tiers | the station-square markup test, and D3c's squares-and-circles count |
| **M6** | *retired.* It reverted the inbound reversal, and the operator's ruling R-a removed the reversal: the slice's interval and the served anchor pair are travel-directed by construction. Its replacements are **M12** and **M12b**, which put the reversal back on each of the two geometry paths | | |
| **M7** | the code table keyed by route id again | **killed** | the name-keyed test, on the two cases an id table cannot answer: a branch whose id moved, and a route with no name |
| **M8** | the ink computed even where the feed supplies one | **killed**, 2 node failures, e2e GREEN | the two railroad ink tests. The e2e staying green is the signature, not an omission: NJ Transit publishes no `route_text_color` at all, so its half of the grammar cannot tell the difference, and the railroads' half can. |

### The screenshots

`docs/reviews/map-redesign/mr3/`, the Rail preset on the stock fixture world at the frozen
clock. `MEASURING.md` says how to take them again and `capture.spec.js` is the harness;
the two runs differ only by the tree they ran in.

| | 1280 | 375 |
| --- | --- | --- |
| before | `before-desktop.png` | `before-375.png` |
| after | `after-desktop.png` | `after-375.png` |

## Stage MR4: the other families, and the dark theme's release

PATH, the ferry, AirTrain and the buses, and then ruling R2 met: when the last family on
the map has its paper casing or stroke, `#theme-toggle` loses its `hidden` attribute and
the dark theme is offered. Four families and one release, and the release is what the other
four are for.

It carried two debts into the stage, both named by MR3 rather than discovered here.

**A third population for the theme swap.** The `setStyle` this stage does over the subway
ribbons and the station circles has to reach the three commuter rail families' 5px paper
casings as well: `railDrawRibbons` resolves `paperColor()` once per draw, exactly as
`drawRibbons` does, so a casing drawn under the light theme keeps its light paper until
something restyles it. They live on their own pane (`railroadCasingPane`) and in each
family's own layer group, so they are reachable; what MR4 owed was to reach them.

**And every count over a class this phase widens.** MR3 put ~300 commuter rail names in
`.stn-label` and six specs' vehicle sentinel counted five rail STATIONS as vehicles; the
repair then was `:not(.rail)`, an exclusion. MR4 widens three more classes (the ferry's
dock names join `.stn-label`, AirTrain's stations join `.rail-stn-marker`, and the ferry's
docks join the station label pane), so every one of those exclusions was wrong again the
moment this stage drew a dock. That is recorded under the findings below as the stage's own
carry-forward, and it is paid structurally rather than with a fourth exclusion.

### The pins, and why two of them are new

P1f, P1g, P1h and P1j already held the bus, PATH and ferry marks byte for byte, and MR4 is
the stage that deliberately moves four of those halves. Their POPUP halves are NOT
regenerated, for the reason MR2 and MR3 both give about their own: the popups are stage
MR5, so a popup that moves here is a defect and that claim stays an assertion. It held:
regenerating the golden moved `census/stock` and `markers/airtrain`, `markers/buses`,
`markers/ferry` and `markers/path`, and not one `popups/*` key.

| Pin | What it holds |
| --- | --- |
| **P4a** | The census of every marker class and every canvas group on the stock world, by class rather than by total: `markerIcons`, the vehicle sentinel, `railStationMarkers`, a per-class tally, and the four label counts as POSITIVE classes. This is the pin that exists because of MR3's carry-forward, and it earned itself on its first outing (below). |
| **P4b** | The ferry's docked-boat opacity in a healthy world, and **P4b2** the compound on a stale one: 0.55 for docked, 0.45 for a stale under-way boat, and 0.2475 for a boat that is both. The compound is asserted as arithmetic as well as recorded as a golden, so a reader does not have to multiply to see which rule is which. |

**P4b's first draft was a pin that could not fail, and reading the golden after writing it
is what caught it.** It aged `sources.ferry.fetchedAt` on a loaded page and called
`refreshAll()`; the next poll answered with the stock envelope and overwrote the edit, so
the "stale" half came back byte for byte identical to the healthy half. It is two pins now
and the stale one boots into a world whose envelope is old from its first byte.

### What the marks were, and what they are

The before is the golden as `origin/main` served it; the after is what this branch
regenerated.

| | before | after |
| --- | --- | --- |
| PATH train | a 16x16 diamond, `<path d="M8 1.5 L14.5 8 L8 14.5 L1.5 8 Z" fill=#d93a30 stroke="#fff" stroke-width=1.5>` | the design's diamond, `d="M8 1 L15 8 L8 15 L1 8 Z"` at `stroke-width 1.2`, the fill and the stroke both in an inline STYLE so the stroke can be `var(--paper)`, same 16x16 box and same `[8, 20]` lift |
| PATH line | one polyline per shape, `weight 2.5 opacity 0.5` | `weight 3.5 opacity 1`, round caps and joins stated rather than inherited, no casing (the one family the design gives none) |
| PATH station | a canvas `circleMarker` `radius 4 color #fff weight 1.5 fillColor #3d5a80`: an INVERTED fill, chosen so PATH would not be mistaken for a subway station where the two coincide | the subway's LOCAL dot through `stationMarkStyle([], ink, paper)`: `radius 3.5 fillColor var(--ink) stroke false`, and in the canvas theme registry because that fill is a token |
| ferry boat | a rounded rectangle, `<rect x=1 y=3 width=20 height=8 rx=4 stroke="#fff" stroke-width=1.5>` | a hull, `<path d="M1 3 H21 L17.5 11 H4.5 Z" stroke-width=1>`: a flat deck wider than the keel, which is what the file's own comment had argued for since it was written |
| ferry route | `weight 2.5 opacity 0.5`, solid | `weight 2 opacity 0.9 dashArray "6 5"`: the dash is the family's signature, and says the service crosses water on no fixed way |
| ferry dock | `radius 4.5 color #fff weight 1.5 fillColor #0e7490` | `radius 4 color <paper> weight 1.5 fillColor #00839c`, and a permanent name label in `stn-label ferry`: the cyan settles a disagreement the app already had (the feed strip's ferry tick has been `#00839c` since MR1 while the Key's dock glyph was `#0e7490`) |
| AirTrain station | a 14x14 magenta square, `<rect x=1.5 y=1.5 width=11 height=11 rx=2 fill=#fff stroke=#b5179e stroke-width=2.5>`, class `airtrain-marker` | the commuter square, byte for byte the one the three rail families draw, class `rail-stn-marker rail-airtrain-stn` |
| AirTrain guideway | `#b5179e`, `weight 3 opacity 0.85`, solid | `var(--scheduled)` resolved at draw (`#6d6e71` light, `#9a9a9a` dark), `weight 3`, `dashArray "8 5"`, and in the theme registry because the colour is the app's rather than a feed's |
| bus | a 20x20 box: an arrow `d="M10 2 L16 17 L10 13 L4 17 Z"` at `stroke-width 1.2` or a `circle r 5.5`, filled from `routeColor` at `hsl(h, 75%, 40%)` | a 14x14 box for both states: the arrow `d="M7 1 L12 13 L7 10 L2 13 Z"` or a `circle r 3.5`, filled from `busMarkColor` at `hsl(h, 45%, 38%)`, stroked in `var(--paper)` |

And the census, which is the same table read as counts:

| | before | after | why |
| --- | --- | --- | --- |
| `markerIcons` | 23 | 23 | no family gained or lost a mark |
| `vehicleSentinel` | 18 | 15 | AirTrain's three stations stopped being counted as vehicles |
| `railStationMarkers` | 5 | 8 | the same three, counted as the stations they always were |
| `stnLabels` | 7 | 9 | the ferry's two dock names, which the design asks for and no dock has ever had |
| `stnLabelsSubway` | 2 | 2 | **this is the one that caught the carry-forward**: read as `:not(.rail)` it was 4 |

### What the theme swap reaches, and what it must not

Six families register a painter; the draw path and the repaint call the same function, so
"what colour is this mark" and "what colour does it become" are one expression each.

| Family | Registered in | Paints | Must not touch |
| --- | --- | --- | --- |
| subway ribbons | `systems/subway.js` | the CASING half's colour, found by `ribbon.part` | the line half's trunk colour; any opacity, which route focus owns and guards on |
| subway stations | `systems/subway.js` | `stationMarkStyle(routes, ink, paper)` per registry entry | the routes that decide dot or ring, which are read back off the registry |
| rail casings | `systems/railroad.js` | the casing's colour, found by `railroadCasingRenderer` | every branch line in the same layer groups, which carry the agencies' published colours |
| PATH stations | `systems/path.js` | `stationMarkStyle([], ink, paper)` | the lines, which take the feed's colour and have no casing |
| ferry docks | `systems/ferry.js` | `ferryDockStyle(paper)` | the dock's own cyan, and the dashed routes' feed colours |
| AirTrain lines | `systems/airtrain.js` | `airtrainLineStyle(scheduled)` | nothing else: the station squares are divIcons and follow the cascade |

Everything else on the map is a divIcon whose theme-dependent paint is `var(--paper)` or
`var(--ink)` in an inline STYLE, so it follows a swap through the cascade at no cost and is
deliberately NOT in the registry. `theme.spec.js` D5c asserts that population from the computed
style at both ends and in both directions, because what a rider sees is the resolved colour and
not the expression that produced it.

### The findings

| # | Finding | Disposition |
| --- | --- | --- |
| **Q1** | **The 3:1 floor a mark owes cannot be met by a fill that is an agency's published colour, at either end of the theme.** Measured on this branch against the theme's own paper: the subway's A trunk `#1f5fbf` reads **2.73** against the dark paper, J `#7d5a3c` 2.69, 7 `#8e44ad` 2.83, S `#566573` 2.77; of the 19 pairs the two MTA railroad feeds publish, Port Washington `#6E3219` reads **1.69**, MTA blue `#0039A6` 1.69 and `#4D5357` 2.13. And it is not a dark-theme problem: the ferry's own South Brooklyn yellow `#ffd100` reads **1.31** against the LIGHT paper, and has since before this phase. The design's answer (a paper casing) does not change this arithmetic, because in either theme the paper IS approximately the surface. | **Measured, recorded as a golden, and NOT fixed here, because every remedy is either out of this stage's scope or a design change.** `theme.spec.js` D5d asserts the floor on the best paint each family has, which every family clears: the subway's square is carried by the white letter on it at 16.6, the rail tags by their type and outline at 14.86. `pins.spec.js` P4c records every family's paints in BOTH themes, so which paint carries which family is a golden rather than a sentence. The remedies, for the operator: (a) keep the floor defined as it is here, on the strongest paint, and leave published fills alone, which is MR3's N1 principle ("the feed's colour is preferred and moved only where nothing else can work"); (b) apply N1's hue-preserving scaling to any mark FILL under 3:1 against the current paper, which makes those fills theme-dependent and reaches MR2's subway square and MR3's rail tag, both out of this stage; (c) draw the mark PLATE in `--ink` in the dark theme so every published fill keeps its light-theme arithmetic, at the cost of a bright plate around every mark on a dark map. **A ruling is wanted before any of them.** |
| **Q2** | **One lightness cannot serve both themes for the bus mark, and the README names one.** A bus route's colour is a HASH of its id, so its legibility is a claim about all 360 hues. The README's `hsl(h, 45%, 38%)` clears 3:1 for every one of them against the light paper (worst 3.16) and leaves **188 of 360 under** against the dark paper (worst 1.62). No fixed lightness clears both: 60% is perfect in dark (worst 3.60 on `--paper`, 3.05 on `--surface`) and leaves 219 under in light. | **The lightness is a token and the hue is still the route's.** `busMarkColor` emits `hsl(h, 45%, var(--bus-mark-lightness, 38%))`; a custom property is substituted before the value is parsed, so one string is a real colour in either theme and follows a swap through the cascade with NO rebuild, exactly as the `var(--paper)` stroke beside it does. The README's 38% is unchanged: it is what the token resolves to in the light theme, and it is the var's fallback so a context with no stylesheet still gets a colour. `families.test.js` sweeps all 360 hues at both ends against both papers, and asserts that NEITHER end alone would do. |
| **Q3** | **PATH stations and subway stations are now the same mark**, which is the cost of the operator's ruling and the design's word ("Stations: subway 'local' dot style"). They coincide at 33rd St, WTC and 14th St, and the paragraph this replaced argued for the inverted slate-blue fill on exactly those grounds. The dot also shrinks from `radius 4 + weight 1.5` to `radius 3.5, no stroke`. | **Built as ruled, and the cost recorded rather than quietly dropped.** A PATH station's identity is still reachable everywhere except the glyph (its popup, its panel entry and its accessible name all say PATH), and the design's answer to "which mode is this" on this map is the LINE under the dot. The shrink stays inside an exception ACCESSIBILITY.md already carries: a canvas-drawn station dot has no DOM node for any test to measure and relies on the panel's 24px rows as the equivalent control, which is the same statement the subway's identical dot has lived under since MR2. |
| **Q4** | **"A square always means regional rail" becomes "regional rail or AirTrain."** Jamaica has an LIRR station and an AirTrain station, and after this stage they are the same 20x20 square on the same map. | **The design's instruction, carried out and re-measured rather than softened.** `rail.spec.js` D3c is rewritten around the wider sentence, asserts the four square families and the three circle families, and asserts that the set of station kinds on the page is exactly the seven it knows, so a fifth station family fails there until someone says which grammar it draws. It also measures the Jamaica collision itself: two registry entries, two system labels, one glyph. |
| **Q5** | **Section 3.3's "absent when withheld" cannot be built for PATH, the ferry or the buses**, which is worth naming in the stage that drew all three. Only `backend/feeds/railroad.py` implements the withholding ladder, so those three families have no withheld state to draw and their marks cannot say anything about one. | **Not in this stage and not attempted.** The marks this stage drew are complete against what their feeds serve. A backend that grows the ladder for those sources is the change that would make a withheld PATH train a thing this map could draw, and MR3's N3 is the precedent for how such a row gets drawn once it exists. |
| **Q6** | **Scoping the subway's label band to its own class raised it above the Names toggle.** MR4 gave each family's label band its own rule, so `:root[data-label-band="all"] .stn-label.subway` became (0,4,0) while the Names-off rule was (0,3,0): Names off stopped hiding subway names at zoom 14, and the hub band at zoom 12 would have been unhideable too. `subway.spec.js` D2j caught it on the first run after the change. | **Repaired structurally rather than by another exclusion.** Every band rule now states its family inside `:where()`, which selects the family and adds nothing to specificity, so all of them are (0,3,0) and ONE Names-off rule at the end of the section reaches every family by source order. The three per-family off rules MR2 and MR3 accumulated are gone. A family added in a later stage is hideable by construction rather than by someone recomputing four numbers, and D2j, D3d and D4d each press the toggle and ask for nothing. |
| **Q7** | **Two dead CSS selectors, found by measuring.** `.njt-marker` and `.njt-station-marker` have matched nothing since MR3 gave NJ Transit the rail tag and the commuter square; the dark-theme measurement selected zero elements through them. | **Deleted, for the reason the comment six lines above them already gives** about the railroad's and AirTrain's own dead selectors: a dead selector is a thing that looks live. The P4a census is the standing proof, since it lists every class that is actually drawn. |
| **Q8** | **A claim this codebase repeats in four places is false, and its own ledger already said so.** Three source comments and two specs asserted that `fill="var(--paper)"` as an SVG presentation attribute "is not a paint value and does not resolve", and one of them used that as the reason a test exists. **Measured on this branch**: `stroke="var(--paper)"` on a presentation attribute computes to `rgb(243, 242, 242)`, byte for byte what the inline-style form computes to; a presentation attribute is mapped into the cascade as a declaration, so the token resolves. An UNKNOWN token (`var(--nope)`) is where black comes from, and it does that in EITHER form. MR2's finding **H2** measured this correctly and wrote "works in a Chromium presentation attribute"; the sentence got stronger every time it was copied, and MR3 and MR4 both copied the strong version. | **Every site corrected to the measured truth, and the house rule kept on its real grounds.** A presentation attribute is the lowest-priority author declaration there is, so any stylesheet rule beats the mark's own paint silently, and the style form also works where attributes are not mapped at all. That is a weaker reason and still decisive, so `families.test.js` keeps asserting the style form and now says what it is asserting. **It is also why mutation M30 is recorded as surviving every browser gate**: the two forms draw the same pixels in this browser, so a mutation that swaps them can only die at the node tier, and a reader who found the e2e green would otherwise have concluded the guard was asleep. |

| **Q9** | **Seven of the Key panel's eighteen rows describe marks that no longer exist, and they have since MR3.** That stage gave LIRR, Metro-North and NJ Transit one grammar (the rail tag, the commuter square, a casing under the agency's own colour) and updated none of the legend's rail rows. Measured from `index.html` on this branch: rows 6 and 7 draw an LIRR/Metro-North train as a 16x16 purple rounded square, row 8 its route line as a flat `#7b1fa2` 2.5px line, row 9 its station as a white ring stroked `#334155`, and rows 15, 16 and 17 do the same three things for NJ Transit in `#075AAA`. Every one of those is a mark this map stopped drawing in MR3. | **Recorded and NOT fixed, and it wants a ruling because the repo's own principle cuts the other way.** MR1's G15 disposition says it out loud: "a key whose glyphs did not match the map would be worse than a dim one", which is why MR2 updated the subway's three rows with the subway's marks and why MR4 updated the four rows for the families it redrew (the bus arrow and dot, the AirTrain guideway and square, the PATH dot, the ferry hull and dock). The rail rows are not this stage's marks: the operator scoped the rail marks out of MR4 and asked for the Key's glyphs to be left unchanged on their plate, and drawing a 35-to-45px two-block tag at legend scale is a design decision rather than a mechanical swap. So it is named here with its measurement. **MR5 or a stage of its own is the natural home**; the row LABELS are already right, so nothing a rider reads is wrong, only every glyph beside them. **RULED ON AND CLOSED in MR4 round 2** (see that section): the round was granted, seven rows changed, five sentences left and are recorded there. One measurement above is wrong and is corrected there too: row 17 draws its NJ Transit station as a filled slate `#334155` square, not the `#075AAA` this row says. |

### Every sentinel over a class this stage widened, re-read

The operator bound this stage to re-read every count over a class it widens, and MR4 widens
three: `.stn-label` gains the ferry's two dock names, `.rail-stn-marker` gains AirTrain's three
station squares, and `stationLabelPane` gains a fourth family's labels. This is the audit, and
the counts are the stock fixture world's (P4a's census is the standing version of it).

| Sentinel | Where | Before | After | Verdict |
| --- | --- | --- | --- | --- |
| `.leaflet-marker-icon:not(.rail-stn-marker)` > 5, "the vehicles have landed" | `announce`, `busroute`, `crosslink`, `layout`, `mobile`, `motion` | 18 | **15** | **More correct, not less.** AirTrain's three stations were counted as vehicles by every one of these; they are stations and now they are excluded. The margin over the threshold falls from 13 to 10, and in a world where ONLY AirTrain had loaded the count is now 0 rather than 3, which is the direction that cannot produce a false pass. |
| `.leaflet-marker-icon`, total | P4a's census, `theme.spec.js` D5b's no-rebuild probe | 23 | 23 | Unchanged, and that is the claim: no family gained or lost a mark. D5b tags all 23 elements before a theme swap and requires the same 23 after it. |
| `.rail-stn-marker` | P4a, `families.spec.js` D4e | 5 | **8** | Deliberate: the three AirTrain squares joined the class whose members are rail-grammar STATIONS, which is what they are. |
| `.airtrain-marker` | P4a, D4e | 3 | **0** | The old class is retired, and BOTH counts are kept so the retirement is asserted rather than assumed. |
| `.stn-label` (unqualified) | `paintZoomBand`'s band sentinel | 7 | **not counted at all** | The band asks `stationRegistry` by `kind` now. This is the repair: `:not(.rail)` was MR3's patch and MR4 would have needed `:not(.rail):not(.ferry)`, which is a list that is wrong once per stage. |
| `.stn-label:not(.rail)` | `subway.spec.js` D2j, `rail.spec.js` D3g | 2 | **would have been 4** | The defect, caught by P4a's census on its first outing. Both now ask `.stn-label.subway`, a positive class no other family can join. |
| `.stn-label.subway` / `.rail` / `.ferry` | P4a, D2j, D3d, D3g, D4d | (new) | 2 / 5 / 2 | Positive per family, so the next family to join this pane changes one number rather than inflating someone else's. |
| `.leaflet-tooltip`, total | `a11y.spec.js` A1z3 | 7 | **9** | The two dock names. A1z3 also asserts the three per-family counts, and measures every one of the nine by the same loop rather than excusing the new family from it. |
| `markerIconsByClass` | P4a | 12 keys | 12 keys | One key changed name (`airtrain-marker` to `rail-airtrain-stn rail-stn-marker`), which is the per-class tally doing its job: a family that changes class moves a key rather than a total. |

### The tests, and what each tier is for

**Node, `frontend/families.test.js`, 11 tests.** Every mark as a function of its inputs, one
state at a time, for the reason MR3's `railtag.test.js` gives: a mark built as a STRING can be
asked, and a mark built inside an `L.divIcon` can only be photographed. The diamond, the hull,
the arrow and the dot as geometry; the dock's and the guideway's options with the token as a
parameter; the bus hue swept over all 360 hues at both theme ends against both papers; and the
predicate table for "is this bus pointed anywhere" (a served null, an absent field, a NaN, a
string, and zero, which is a heading and which a truthiness test would have lost).

**And the theme registry asserted against the SOURCE rather than against a list**, which is what
the operator asked for: a node test naming the six families would be a second copy of the
registry and would agree with itself forever. The scrape reads every file in `systems/` that
resolves `paperColor()`, `inkColor()` or `scheduledColor()` and requires each one to register a
family. It cost two corrections on the way, both of them the same lesson:

- It went red on `njt.js`, whose only mention of `paperColor()` is a SENTENCE in the MR3 comment
  explaining that `railDrawRibbons` "gets `paperColor()` for free". The match was prose. So the
  scrape strips comments first, which is asking the question of the program rather than of the
  file.
- And a stripper that ate a string or a regex would hide a real call site and leave the test
  passing over nothing, which is one of the four defect shapes this phase keeps producing. So
  the stripped file is COMPILED (`new vm.Script`, which parses without running) before it is
  scraped, and the stripper itself is unit-tested on prose, a string and a division.

**Hermetic e2e, `tests/e2e/families.spec.js`, D4a to D4g.** Each family's mark and its dimmed
state on the drawn page; the ferry's docked rule alone in a healthy world, which is the half P4b2
cannot show because there every boat is dimmed by the feed as well; the dock labels' band and the
Names toggle; and the two counts this stage widened. Paints are read as COMPUTED STYLE and
opacity as `el.style.opacity`, never as the option that asked for it.

**Hermetic e2e, `tests/e2e/theme.spec.js`, D5a to D5d.** The control's release and G7's name rule
at both states; the swap reaching all six canvas families while rebuilding nothing (every marker
element tagged before the swap and found after it, a popup held open across it, the canvas
layers' Leaflet ids compared); the divIcon population following the cascade at both ends; and
G15's floor on the drawn page.

**Contract browser tier, C6e5.** The ferry is the family worth bringing to a real backend because
it is the only one with TWO opacity rules, and they multiply rather than replace. With
`ferry:vehicle` killed and `ferry:tripupdate` left alive (the dimming is about POSITIONS, which
is also the sharper test), a docked boat draws at 0.55 * 0.45 and an under-way one at 0.45, the
option agrees with the drawn page, and both clear on recovery.

**Two flakes, recorded rather than smoothed over.** `smoke.spec.js` 21 (a boat moves between
polls without churn) failed once in a three-file parallel run and passed alone at the same sha;
P1k failed once inside a mutation run that cannot reach anything NJ Transit draws, and passed on
an isolated re-run. Both are timing-sensitive under load. Neither failed in any full-suite run.

### Round 1: the adversarial pass over the written diff

Five finder dimensions over the production diff at `27d857f`, each in its own worktree
detached at that sha (RULE 0 and RULE 0b). The reviewers were pointed at the four shapes this
phase's defects have actually taken: a markup read where the drawn page is what matters, the
model believed over the page, a test that cannot fail, and a count over a class a later stage
widened. **Fourteen findings, every one CONFIRMED by the verify phase and none refuted. One wants a
ruling and is not fixed; thirteen are repaired here, and six of them are that third shape
inside the tests this stage wrote to catch the others.**

| # | Finding | Disposition |
| --- | --- | --- |
| **R1** | **The contrast measurement counted paints that do not exist, and the golden recorded two of them.** `resolve()` hands a paint through a probe's `color` to get one notation, and CSSOM DROPS an assignment it cannot parse: `none`, which is the computed `stroke` of every shape that sets no stroke, left the probe's INHERITED colour to be measured as the mark's. P4c recorded "subway train" and "rail tag" in the light theme as carrying `rgb(0, 0, 0)` at 18.79, and no mark on this map paints black. D5d's floor is a MAXIMUM over a mark's paints, so a phantom at 18.79 would have carried any mark past it. | **Fixed with `CSS.supports("color", value)`, which asks the browser what it will take as a colour rather than re-implementing a parser.** And READING THE GOLDEN AFTER REGENERATING IT found the second half: a `<line>` has no area, so it paints its stroke and nothing else, but its computed FILL is the initial value, a real black that the first guard admits. The rail tag's divider line was carrying that family at 18.79. Paints are enumerated per element kind now, and every row of P4c is a colour a mark actually carries. |
| **R2** | **D5b's no-rebuild probe had no subject for the rail casings**: `railCasing: null` is the same literal on both sides of `expect(after.ids).toEqual(before.ids)`, so the one family the test names as its mutation target was the one family whose identity was never compared. A painter rewritten to remove and redraw would have passed every assertion in it. | **Fixed**: the casing layers' `_leaflet_id`s are collected in the loop that already walks them, and a premise loop asserts every id in the probe is non-null, so a null on both sides cannot happen again in any family. |
| **R3** | **The band's sentinel lost the "only judge labels that are actually painted" half** when it moved from a DOM count to a registry query, so a subway a rider pressed OFF reads as "stations exist, zero hubs", which is the DEGRADED band. | **Fixed with `map.hasLayer`, and the review's REASON was corrected by measuring it.** The finding said the DOM count answered this by construction because "a tooltip on a removed layer is not in the document". It is: Leaflet leaves a permanent tooltip's element in the pane when its layer is removed, measured on the page, so the DOM count read the same number with the feed on and off and the defect is OLDER than this diff. The fix is also incomplete without a second half the finding did not name: the band was only recomputed on `zoomend`, so `applyFeedVisibility` now repaints it. `subway.spec.js` **D2z2** is the new spec, and it asserts the measurement (the elements survive) as well as the behaviour. |
| **R4** | **`repaintCanvasFamilies` swallowed every paint error with no signal, and the comment promised a recovery that does not exist.** It said "the next draw reads the live token anyway, so a miss here is repainted by the load that follows it"; after load there is no next draw, because every loader draws once per page. A family whose paint threw would keep the previous theme's colours forever while the page reported the new one. | **Fixed by recording rather than recovering**, because there is nothing here that could recover it. The catch stays (one family may not take the swap down with it), the comment now says what is true, and each failure lands in `canvasThemeFailures`, which D5b asserts is empty after a swap. A rider is still told nothing: there is no action for them in it. |
| **R5** | **The ferry's dock names rode the SUBWAY's band, including its degraded value.** `data-label-band` reads "all" from zoom 13 when no subway station lists a route, so every dock name came on one zoom early, against the design's "names from 14", for a reason that has nothing to do with the ferry. | **Fixed with `data-ferry-label-band`**, which is MR3's own sentence about the rail names applied again: the two bands overlap and one attribute cannot hold two answers. A dock's answer is the zoom and nothing else, because a dock has no interchange to reveal and no degraded state to fall back to. D4d asserts the attribute at 13 and at 14. |
| **R6** | **The Key's bus swatch was a colour no bus mark can be.** The three bus glyphs took the design's 14px box, arrow path and paper stroke in this stage and kept `#1d4ed8`, which is `hsl(224, 76%, 48%)`: the unmuted family finding Q2 measured as leaving 147 of 360 hashed hues under the floor. The row's caption says "Color indicates route". | **Fixed to `#354d8d`**, the same hue at the muted saturation and at the LIGHT theme's lightness, which is the right end for a plate that is `#f3f2f2` in both themes by ruling H3. Measured on that plate: 7.24, against the 6.00 it replaces. A literal rather than the token, for H3's reason. |
| **R7** | **"Colour only, never opacity" was enforced on two of the six registry entries**, and they are exactly the two that keep it. The other four hand a whole style object to `setStyle`, opacity and all. | **The rule is restated as what it actually is, per family, and the split is not arbitrary: an entry may not write an opacity that something ELSE owns.** Ribbons and casings may not (route focus reads theirs back and guards on it); a station dot, a dock and a guideway may, because nothing else writes theirs and what they pass is a CONSTANT of the design, which the test asserts directly by asking the builders for two token sets. The six are asserted to be all of them, so a seventh has to declare its side. |
| **R8** | **The registry-coverage scrape was file-granular**: it asked whether a file contains a `registerCanvasFamily` call at all. `subway.js` draws from tokens at two independent sites and registers two families, and the check passed with either registration deleted, or with a third uncovered draw added. | **Fixed by counting DRAW SITES rather than files or calls.** One draw resolves as many tokens as its style needs (`loadSubwayStations` takes ink and paper on consecutive lines), so a maximal run of consecutive resolver lines is one site: it merges one draw's several tokens and separates two draws. Registrations must be at least sites, per file, and the site map itself is asserted so a scrape that silently found nothing fails. |
| **R9** | **D4e computed the vehicle sentinel and never asserted it.** The value six specs share, the one the ledger records moving 18 to 15, was read into a variable and dropped, in the spec written to close exactly that carry-forward. | **Fixed**: the count is asserted at 15 AND the set of classes in that bucket is asserted by name, so a family that joins the marker pane without joining `rail-stn-marker` while not being a vehicle fails by name rather than by inflating a number nobody reads. |
| **R10** | **Seven of the Key panel's rows contradict the map, and MR4 sharpened one of them into a reversal.** The Key now says a square is "AirTrain JFK station" and a circle is "LIRR / Metro-North station"; on the map both families draw the identical square, and at Jamaica they are 100m apart. | **This is finding Q9, and round 2 fixed it.** It wanted a ruling and got one. The design decision it names is answered in the round's section: the tag is drawn at MAP scale in a cell that adapts to it, because it is the one glyph in this panel that carries type. The reversal this row reports is gone with the three station rows that caused it: one square, one row, four families named. What MR4 changed before that is still worth keeping: before this stage the square in the Key was AirTrain's magenta one and no rail family drew a square at all. |

| **R11** | **The station panel's AirTrain route chip kept the magenta the map deleted.** `stations.js` returned `#b5179e` for an AirTrain chip, which is the guideway colour this stage replaced with `--scheduled`, so the chip keyed to a colour a rider can no longer see anywhere on the map. | **Fixed to the same gray the guideway is drawn in, read LIVE** (`scheduledColor()` at render), so the chip and the line agree in whichever theme is current; the panel re-renders on every poll, so a swap with it open heals on the next one. It is the one chip that can be a token at all: the others are feed colours and fixed palettes. |
| **R12** | **D4e's "the magenta is gone" could not fail.** It asked `/magenta\|#e\|#f0f/i`, and `#b5179e`, the only magenta this stage removed, matches none of those. | **Fixed by naming the colour**, and asked of both surfaces that carried it: the guideway's options, a sweep of the whole document, and the panel's chip through the function that builds it (a rendered row exists only while an AirTrain station is selected, so a document sweep alone would pass over a page that has none). |
| **R13** | **The clicked bus ROUTE LINE was still the raw hashed hue**, `routeColor` at `hsl(h, 75%, 40%)`, theme-blind and outside the canvas registry, in the stage that made the bus MARK's lightness a token for exactly that reason. Measured against the dark paper: **169 of 360 hues under 3:1, worst 1.45**, and **217 under once the line's own 0.65 opacity is composited** (worst 1.19). | **FIXED, and the verifier's evidence is what changed the answer.** This was first recorded here as wanting a ruling, on the grounds that the line is a surface the stage's scope names only for its ownership rule. Two things in the verifier's report settle it the other way. The invariant is the REPO'S OWN, written at `routeColor` itself: "it is a polyline colour and a heading colour ... so this wheel owes 3:1 as a non-text indicator". The heading half was tokenised this stage and the polyline half was not. And the consequence is not subtle: a rider who clicked a bus got a line in a DIFFERENT COLOUR from the arrow they clicked, in both themes. So the line takes `busMarkColorAt` with the lightness resolved at draw, and it is the **seventh canvas family**, and the only one whose colour depends on something besides the theme: the route id rides on each layer so the painter can recompute per route. `busMarkLightness()` is the fourth token resolver and the scrape knows it. D5b draws a route and asserts the line and its own arrow are ONE COLOUR, compared to each other rather than to a literal. |
| **R14** | **D4f's token assertion could not detect a missing token.** It compared the drawn fill against `busMarkColorAt(route, 38)`, and the var's FALLBACK is the same 38%, deliberately, so a stylesheet that never declared `--bus-mark-lightness` would resolve to the identical rgb and pass. | **Fixed by asking the root directly** for the declared value. theme.spec.js D5c is the other half and always was: with no token declared, both themes would draw the fallback and its "the two themes draw them at different lightnesses" would fail. |
| **R15** | **The contrast measurement's alpha branch was unreachable**, so the file's stated guarantee ("an alpha is composited rather than ignored") was half false: it composited an rgba() COLOUR's alpha, which no mark here has, and ignored the one alpha that is actually on this map, the subway plate's `opacity="0.95"` ELEMENT attribute. | **Fixed: a paint's effective alpha is its colour's alpha times the element's `opacity` and its `fill-opacity` or `stroke-opacity`, composited over the surface before the ratio. And it is recorded as UNGUARDED**, which is the honest half: it changes no number today, because the plate's paint IS the surface colour and compositing it over the surface returns the surface. Mutation M47 reverts it and SURVIVES, for that reason rather than because a guard is asleep. |

**24 candidates from five dimensions, 11 merged away in triage, 14 confirmed and none refuted
or unverified.** Three findings came back from three different lenses each with the same file
and line (the phantom `none`, the null identity, and the vacuous magenta regex), which is the
part of a fan-out that is cheap to verify. The panel's verdicts are worth keeping for one
reason beyond the findings themselves: **the bus route line was recorded here as wanting a
ruling and the verifier's evidence overturned that**, by quoting the invariant the repository
writes at `routeColor` and by measuring the line at the opacity it is actually stroked with.

### The mutations

Each one in a `git worktree` **detached at the commit under test**, with the worktree's sha
echoed before the run and compared against the commit's, so a run against the wrong tree is
visible rather than silent. **Every one of them was re-run at the round-1 tip (`3fdc074`)** and
the results below are that run; M22 through M33 were first run at `65193fd` and killed there
too. That is review-workflow RULE 0b applied to the
mutation harness, which is where MR3 learned it: its M9 came back green twice because the
mutated worktree was being served the main checkout's frontend. The driver kills the static
server **by port** and sets `CI=1` so Playwright refuses to reuse one, and it refuses to run a
browser gate at all while the port is still held.

| # | Guard reverted | Result | Killed by |
| --- | --- | --- | --- |
| **M22** | the rail casings left out of the theme restyle | **killed**, node and e2e | `families.test.js`'s registry-against-the-source scrape and its casings-by-renderer test, and `theme.spec.js` D5b, which asserts the dark paper reached them |
| **M23** | the toggle released with G7's name defect reintroduced (`aria-pressed` back on the button) | **killed**, 2 e2e | `theme.spec.js` D5a, which asks both halves at both states, and `chrome.spec.js` D1g |
| **M24** | the ferry compound lost: the docked base dropped from the stale sweep, so the two rules assign instead of multiplying | **killed**, 3 e2e | P4b, P4b2 and P1m |
| **M25** | a family's dimmed state drawn at full opacity (PATH's sweep stops asking the observation's age) | **killed**, node and e2e | `positions.test.js`'s every-sweep scrape, which reads the call sites out of `systems/`, and `families.spec.js` D4b |
| **M26** | the bus arrow drawn when no heading is served | **killed**, node and 4 e2e | `families.test.js`'s predicate table (a served null, an absent field, a NaN, a string), D4f, D4g, P1g and P4c |
| **M27** | a circle drawn for an AirTrain station | **killed**, 4 e2e | `rail.spec.js` D3c (the census, both ways), `families.spec.js` D4e, P1n and P4c |
| **M28** | the muted hue replaced by the raw hashed hue | **killed**, node and 5 e2e | `families.test.js`, D4f, P1g, P4c, D5c and **D5d, which is the one that matters**: the raw hue reads 1.62 against the dark paper |
| **M29** | one lightness for both themes: the token replaced by the README's literal 38% | **killed**, node and 4 e2e | the token assertion in `families.test.js`, P1g, P4c, D5c ("the two themes draw them at different lightnesses") and D5d |
| **M30** | the PATH diamond's paper stroke as an SVG presentation attribute instead of an inline style | **killed at the NODE tier only. Every browser gate stayed GREEN, and that is finding Q8**: measured, a presentation attribute DOES resolve a custom property in Chromium and computes to the same rgb, so the two forms draw the same pixels and no page test can tell them apart. The comments that claimed otherwise are corrected; the node guard stays, on the cascade's grounds. |
| **M31** | the label band's `:where()` removed, so the family scope out-specifies the Names toggle again | **killed** | `subway.spec.js` D2j, which is the spec that caught the defect when it was real |
| **M32** | `paintZoomBand`'s sentinel counts the DOM again, in exactly the `:not(.rail)` shape MR3 left it | **killed** | `rail.spec.js` D3g. **Restated once**: the first draft also deleted the registry query the Names toggle's title is built from, so the page died on a ReferenceError and 24 specs went red, which proves nothing about this guard. A mutation reverts one decision. |
| **M33** | the ferry dock's ring back to a literal white, which is a light halo on a dark map (G15 measured that mark at 2.63) | **killed**, node and 3 e2e | `families.test.js`'s the-ring-is-the-caller's test, `families.spec.js` D4c, `theme.spec.js` D5b and D5d, and P1m and P4c |

**One flake, recorded rather than smoothed over.** In the three-file run of M33, P1k (the NJ
Transit marks) also failed; on an isolated re-run of the same mutation against the same sha it
passed, and the mutation reaches nothing NJ Transit draws. The kill above is the isolated run.

**And one per guard the round repaired**, which is the operator's rule applied to the review's
own findings rather than only to the stage's. **Twenty-six in total: twenty-five die and one
survives for a reason that is written down.**

| # | Guard reverted | Result | Killed by |
| --- | --- | --- | --- |
| **M34** | the colour guard dropped from the contrast measurement, so `none` is resolved to the probe's inherited colour again | **killed** | P4c, on the numbers: two families come back carrying `rgb(0, 0, 0)` |
| **M35** | a `<line>`'s phantom fill counted again | **killed** | P4c, on the rail tag's row |
| **M36** | the rail casing's identity back to a literal `null` on both sides | **killed** | D5b's premise loop, which asserts every id in the probe is non-null |
| **M37** | `paintZoomBand` counts registry entries that are not on the map again | **killed** | `subway.spec.js` D2z2 |
| **M38** | the band no longer repainted when a feed's visibility changes | **killed** | D2z2's other half, the press that puts the layer back |
| **M39** | a family's painter throws (the ferry docks call a function that does not exist) | **killed**, 2 e2e | D5b's `failures` assertion AND D5d, which finds the dock still wearing the light theme's ring. **This is the mutation that proves the catch records instead of swallowing**: before the round, both of those passed. |
| **M40** | one of `subway.js`'s two registrations deleted, which the file-granular scrape allowed | **killed**, node and e2e | the site-count assertion, the six-families list, and D5b and D5d on the page |
| **M41** | a focus-owning entry passes opacity, which is the rule the per-entry guard states | **killed**, 2 node | the per-entry opacity test and the casings-by-renderer test |
| **M42** | the ferry's dock names back on the subway's band, degraded value and all | **killed** | D4d, on `data-ferry-label-band` at 13 and at 14 |
| **M43** | the station panel's AirTrain chip back to the magenta the map no longer paints | **killed** | D4e, through the function the panel builds the chip with |
| **M44** | the AirTrain guideway back to the magenta literal | **killed**, node and e2e | the registry scrape and D4e, which names the colour now instead of asking a regex that could not match it |
| **M45** | the clicked bus route line back to `routeColor`'s raw wheel | **killed after the guard was strengthened.** It first survived the page tier, and the reason is the repair itself: the painter runs on every theme press, so a line DRAWN from the wrong wheel is corrected by the first press and every comparison made after it passes. D5b takes its reading before the theme is touched at all now, and the node scrape catches the lost resolver either way. The same shape as MR3's M9 and this stage's own P4b |
| **M46** | `--bus-mark-lightness` deleted from the light theme, so the var falls back | **killed** | D4f, which asks the root for the declared value rather than comparing the token against its own fallback |
| **M47** | the element's alpha not composited in the contrast measurement | **SURVIVES, and it is recorded as surviving.** The only element on this map with an opacity is the subway's plate, whose paint IS the surface colour, so compositing it over the surface returns the surface and no number moves. The repair is correct for the case it will meet and there is nothing today for a guard to see; saying so is the honest version of a green run |


### Round 2: the operator's two rulings, and the Key panel paid

Two rulings arrived on the round 1 findings that were referred up. **Q1 is answered in the
accessibility statement and Q9 is the Key panel round.** Nothing on the map changed: every marker
this stage drew is byte for byte what round 1 pushed, which the pins confirm (`mr_pins.json` moved
in exactly one block, `legend/names`, and no `markers`, `popups`, `census` or `contrast` entry
moved with it).

#### Ruling Q1a, and the one paint it cannot promise

> *Fills stay the agency's published colors; each family's identifying paint (letter, stroke, or
> casing) meets 3:1 on the drawn page in both themes, named per family, with P4c as the witness.*

`theme.spec.js D5d` was rewritten around a table that names the carrying paint per family and per
theme, and says for each whether the APP chose that colour or an AGENCY published it. Where the app
chose it, the spec asserts 3:1 or better. Where an agency published it, the value is read and
reported and no floor is claimed. Two statements went into `ACCESSIBILITY.md`, the second of which
is the ruling's one gap, stated rather than smoothed:

| family | light theme | dark theme | chosen by |
| --- | --- | --- | --- |
| subway train | route square, **4.87** at worst of two | white letter, **16.60** | published (light), app (dark) |
| subway station dot | ink fill, **14.86** | ink fill, **14.86** | app |
| PATH station dot | ink fill, **14.86** | ink fill, **14.86** | app |
| rail station square | ink stroke, **14.86** | ink stroke, **14.86** | app |
| AirTrain station square | ink stroke, **14.86** | ink stroke, **14.86** | app |
| rail tag | ink box, **14.86** at worst of six | **14.86** | app |
| bus | muted wheel, **6.65** at worst of two | **4.75** | app |
| ferry dock | `#00839c`, **3.98** | **3.74** | app |
| PATH train | published red, **4.09** | **3.64** | published |
| **ferry boat** | **1.31** | **3.74** | published |

**The ferry boat's hull in the light theme is the one paint under the floor, and it is the only
one.** A boat is filled with the colour NYC Ferry publishes for its route and South Brooklyn's
`#ffd100` is that colour; the hull's only other paint is the paper casing, which cannot raise a
fill's ratio against paper. So the statement reports that number rather than promising the floor
for it, and `D5d` asserts the exemption BY MEASUREMENT (`bestOf("light", "ferry boat") < 3`), which
means the day a stage gives the hull an ink edge the spec fails and the paragraph can be
strengthened. **The minimal change that would let the statement promise the floor for all ten
families, without moving a single published fill, is an ink edge inside the ferry hull's and the
PATH diamond's paper casing.** That alters two marks and therefore the captures, which is why it is
recorded here for a ruling rather than taken.

*One correction to round 1's own report, since it was read off a throwaway probe and not off the
drawn page:* PATH's diamond was reported at 2.76 in the light theme. That was route 859's blue,
which this app serves but does not draw. On the drawn page the diamond is the published red at
**4.09** light and **3.64** dark, so PATH clears in both themes and the ferry boat stands alone.

#### Ruling Q9, and the five sentences that left

> *Fix the Key now, as a round. The square row reads regional rail station and names LIRR,
> Metro-North, NJ Transit and AirTrain; the commuter train row shows the two-part tag at legend
> scale in both body states as the states study drew it; the transfer ring gets its name (F16).*

**The Key's eighteen accessible names before this round**, which is the before the ruling asked to
be recorded. Five of them leave; a row's departure is marked, and every other string is byte for
byte what it was:

| # | name before | after this round |
| --- | --- | --- |
| 1 | Bus (arrow points where it's heading) | unchanged |
| 2 | Bus, heading unknown | unchanged |
| 3 | Subway train, at/approaching the stop shown | unchanged |
| 4 | Subway route line | unchanged |
| 5 | Subway station (click for arrivals) | **unchanged, and its glyph now draws one mark** |
| 6 | LIRR / Metro-North train (live GPS) | **GONE**, into the solid tag row |
| 7 | LIRR / Metro-North train (scheduled or estimated, no GPS) | **GONE**, into the outlined tag row |
| 8 | LIRR / Metro-North route line | unchanged, glyph redrawn |
| 9 | LIRR / Metro-North station (click for arrivals) | **GONE**, into the commuter square row |
| 10 | AirTrain JFK route line (scheduled service, no live tracking) | unchanged, and its glyph was already right |
| 11 | AirTrain JFK station (click for scheduled headways) | **GONE**, into the commuter square row |
| 12 | PATH station (click for arrivals); trains are diamonds | unchanged |
| 13 | NYC Ferry boat (live GPS); dimmed when at a dock | unchanged |
| 14 | Ferry dock (click for arrivals) | unchanged |
| 15 | NJ Transit route line | unchanged, glyph redrawn |
| 16 | NJ Transit train (scheduled or estimated, no GPS) | **GONE**, into the outlined tag row |
| 17 | NJ Transit station (click for departures) | **GONE**, into the commuter square row |
| 18 | Color indicates route / click a bus to draw its route | unchanged (the note) |

And the three names that arrive: **Subway transfer station: two or more route lines meet (click for
arrivals)**, **LIRR / Metro-North / NJ Transit train (live GPS)** and **LIRR / Metro-North / NJ
Transit train (scheduled or estimated, no GPS); NJ Transit is always this**, plus the merged
**Regional rail station: LIRR, Metro-North, NJ Transit, AirTrain JFK (click for arrivals or
departures; AirTrain is scheduled only)**.

**The arithmetic: 18 rows, minus 2 for the station merge, minus 1 for the train merge, plus 1 for
the F16 split, is 16, and 16 plus the one note is 17.**

#### The three row counts, all moved rather than relaxed

The ruling named two. **There is a third**, and finding it is round 2's own first result: a panel
with three independent counts is exactly how a row leaves quietly.

| pin | was | is | what it counts |
| --- | --- | --- | --- |
| `a11y.spec.js` **A1x** | 19 | **17** | `.legend-row` plus `.legend-note`, at three widths in both themes |
| `subway.spec.js` **D2l** | 18 | **16** | `.legend-row` alone, and **the count the ruling did not name** |
| `pins.spec.js` **P1e** | superset of 18 | **ordered equality on 17** | the accessible names themselves |

**P1e was strengthened, not regenerated quietly.** It had been a SUPERSET for four stages, and that
asymmetry is what let Q9 sit for two of them: "additions only" made REPLACING a stale row the one
thing a stage could not do, so nothing did. An equality in order now fails for three things a
superset could never see: a row removed and replaced in the same commit, a sentence quietly
reworded, and two rows swapping places.

#### What each changed row draws now, against what the map draws

| row | before | after | the map's own source |
| --- | --- | --- | --- |
| subway station | one glyph drawing BOTH a dot and a ring, caption describing one thing (F16) | the dot alone, caption byte for byte unchanged | `stationMarkStyle`, local branch: r 3.5, ink fill, no stroke |
| subway transfer (**new**) | nothing in the panel named the ring | r 4.5 paper fill in a 2-unit ink stroke | `stationMarkStyle`, transfer branch, at 1:1 |
| commuter train, solid | a 13x13 `#7b1fa2` rounded square | the LIRR tag's body at scale **1.00**: ink agency block, paper `L`, Babylon `#00985F` block, `BAB` in `#1a1a1a` | `railTagSvg`, `body === "solid"` |
| commuter train, outlined | two rows, a `#7b1fa2` and a `#075AAA` hollow square | the NJ Transit tag's body at scale **1.00**: paper box in a 1.2 ink stroke, divider at x 16, NEC's `#DD3439` stripe | `railTagSvg`, else branch |
| LIRR / Metro-North route line | a flat `#7b1fa2` 2.5px line at opacity **0.6**, no cap | paper casing weight 5 at 0.9, then `#00985F` at weight 2.5 and opacity 1, round caps | `railDrawRibbons` |
| NJ Transit route line | the same three defects in `#075AAA` | the same casing; **the colour is kept**, because `#075AAA` IS a published NJ Transit route colour (the Atlantic City Rail Line's) and this row's defect was the flat line | `railDrawRibbons` |
| regional rail station | three rows: a white ring stroked `#334155`, a filled slate `#334155` square, and AirTrain's correct one | one row, and its glyph is `railStationSvg`'s output with the tokens resolved: an 8x8 paper rect at 6,6 in a 1.6 ink stroke | `railStationSvg`, one builder for all four families |

**Two of those rows are beyond the ruling's three named items and are flagged as such**: the LIRR /
Metro-North and NJ Transit route line glyphs. They are the same defect in the same panel, recorded
in Q9's own measurement ("row 8 its route line as a flat `#7b1fa2` 2.5px line"), and leaving two
knowingly false glyphs in a panel whose round exists to stop it lying would have been the worse
call. Neither row's accessible name moves, so neither costs a sentence.

#### "At legend scale", which was the one real design decision

`.legend-row svg` is a fixed 16px box, so a glyph's viewBox alone decides its apparent size. Every
other glyph in this panel is a SHAPE and a shape survives being scaled. The rail tag carries TYPE,
and type does not. Measured, three ways:

| what | scale | the tag's 8px Archivo renders at |
| --- | --- | --- |
| the tag's native `viewBox 0 0 35 30` in the 16x16 cell | 0.457 | **3.66px** |
| the widest tag, `0 0 45 30`, same cell | 0.356 | **2.84px** |
| widening the cell to 40x16 while the viewBox stays 30 tall | 0.533 | **4.27px** |

**The third row is the trap worth recording: widening the cell buys almost nothing while the
viewBox is 30 tall**, because the box is then height-constrained by the air the stem and the head
hang in. So the viewBox is cropped to the TAG (y 0 to 13, padded to 16) and the CELL adapts to the
mark: `viewBox 42x16` in a 42x16 box is a scale of exactly **1.00** and type at the map's own 8px.
**"At legend scale" therefore resolves to "at map scale"**, which is the states study's own left
column: it magnifies a mark beside the map, it never shrinks one to fit a cell. 42 is
`railTagGeometry("NJT", "NEC").width + 2`, and the height stays 16, so the plate is 44x18 against
18x18 elsewhere and **no row's height changes**.

**No stem and no head, and that is F16's lesson applied rather than an economy.** These two
captions name the BODY state, which is where the position came from. The head is the other axis
entirely. A glyph drawing a mark its caption never explains is precisely the gap F16 recorded, and
repeating it in a row added by the round that fixes F16 would be perverse. **The chevron against
the dot is therefore a new finding (F17) and not a corner of this one.**

#### F17, and the one claim no existing gate could see

| # | Finding | Disposition |
| --- | --- | --- |
| **F17** | **Nothing in the Key explains the chevron against the dot**, before this round or after it. The tag's head carries whether a heading is trusted, which is one of the two axes the states study exists to make visible, and the panel is silent on it. | **Recorded, not fixed.** It is a new row with a new sentence, which is a ruling this round does not have. The two rows added here are the body axis, and adding the head axis to their glyphs would have been F16 again. |
| **F19** | **A mutation whose anchor has gone stale is a mutation that never ran, and one had.** Re-running the stage's whole table at this round's tip, which the ruling asked for, is what found it: **M35** (the `<line>` phantom-fill guard reverted) reported `anchor in tests/e2e/contrast.js matched 0 times` and exited without testing anything. Round 1 turned the line it anchors on into a block when it added alpha compositing, and the table was never re-anchored, so between round 1 and here this repo carried a guard whose only evidence was a mutation that had stopped executing. | **Fixed and re-run.** M35 is re-anchored against the source as it now stands and dies on `P4c`, which is what it recorded before. The lesson is the one round 4 already learned about its `dim` column: a mutation table is code and rots exactly the way an unread field does. The runner had been printing `ANCHOR MISS` and exiting non-zero all along; what was missing was anyone running it again. |
| **F18** | **"At legend scale" is a claim about SIZE and nothing in the suite measured size.** Measured: deleting `width: 42px` scales the type to 3.05px, an illegible smudge, and every gate stays green. The colours do not move (A1z reads computed fill, which is scale-invariant), the row counts do not move, the accessible names do not move (P1e strips the glyph, precisely because it is decorative), axe sees an `aria-hidden` subtree, and no capture is diffed byte for byte. | **Fixed in the round.** `a11y.spec.js` **A1x2** reads `getScreenCTM()` on each tag's two text nodes and asserts the scale is 1, the declared size is the map's 8 user units, the rendered size is at least 8px, the weight is 800, the cell equals the viewBox, and the class is `key-rail-tag` and not `rail-tag`. A CTM rather than a bounding box because a text element's box is its INK: "BAB" and "NEC" would answer one question two ways. |


#### The mutations, round 2, and the whole table re-run

**One per changed row, as the ruling asked, plus one per guard the round moved or added.** Every
one run in a worktree detached at the round's tip, sha echoed and compared, the server killed by
PORT and `CI=1` so Playwright refuses to reuse one. **Seventeen, and all seventeen die.**

**And the stage's earlier twenty-six re-run at the same sha, which the ruling also asked for.**
Twenty-five die exactly as recorded and **M47 survives, exactly as recorded**. One did not run at
all, and that is finding **F19**: M35's anchor had gone stale in round 1 and the table was never
re-anchored, so it had been reporting `ANCHOR MISS` instead of testing anything. Re-anchored here,
it dies on `P4c`. **Forty-three mutations in total: forty-two die and one survives for a reason
that is written down.**

*On the sha: every mutation ran at `3198450`. The commits after it change the ledger, the PR body
and one comment block in `pins.spec.js` and nothing else, so `git diff 3198450 HEAD` over the
executable files is comment text only and no outcome here can differ at the tip.*

| # | Row or guard reverted | Result | Killed by |
| --- | --- | --- | --- |
| **M48** | the subway station row draws BOTH marks again, with the transfer row still there | **killed**, node | `keyglyphs` 7 (one mark per row) and 7b, which notices PATH's dot is no longer the same glyph |
| **M49** | the transfer row deleted, which is F16 un-paid | **killed**, node and 3 e2e | `keyglyphs` 7, then `A1x` (16 not 17), `P1e` and `D2l`. The row that all four counts agree about |
| **M50** | the transfer row kept and its name reworded | **killed**, node | `keyglyphs` 7, which looks the row up BY NAME. Under the superset `P1e` this was an addition and passed |
| **M51** | the solid tag row back to MR3's purple rounded square | **killed**, node and e2e | `keyglyphs` 8 (solid) and `A1x2`, which now finds one tag where the pair is the claim |
| **M52** | the outlined tag's divider left at LIRR's 11 when the tag is NJ Transit's | **killed**, node | `keyglyphs` 8 (outlined), against `railTagGeometry`'s own agency width |
| **M53** | the LIRR / Metro-North route line's casing dropped, so a ribbon is a hairline | **killed, node ONLY. Every browser gate stayed green**, and that is why `frontend/keyglyphs.test.js` exists: `A1x` measures a row's ink, `A1z` measures a glyph's type, `P1e` reads the names and strips the glyph on purpose. Nothing in the browser looks at what a Key glyph DRAWS | `keyglyphs` 4 |
| **M54** | the casing kept and the line back to `opacity="0.6"` | **killed**, node | `keyglyphs` 4, on the half a casing check alone would miss |
| **M55** | the merged square row back to one agency's white ring, name and all | **killed**, node and e2e | `keyglyphs` 2 and 3, then `P1e`. **Not `A1x` and not `D2l`**: a one-for-one replacement moves no count, which is the reversal the row counts cannot see |
| **M56** | the row and its name kept, and only the square's stroke moved off `railStationSvg`'s 1.6 | **killed**, node | `keyglyphs` 2, against the builder's own output |
| **M57** | the NJ Transit casing given the SUBWAY's 6.5 | **killed**, node | `keyglyphs` 4, and 5 is the control that says a rail ribbon reads thinner than a trunk |
| **M58** | the `width: 42px` cell deleted, so "at legend scale" silently becomes 3.05px type | **killed**, node and e2e | `A1x2`, which is the assertion finding F18 asked for, and `keyglyphs` 8 on both rows through the CSS it reads |
| **M59** | the Key's tag takes the map's own `rail-tag` class | **killed**, node and 2 e2e | `keyglyphs` 9, `A1x2` and **`A1z4`**, which is the closure `ACCESSIBILITY.md` states: every `svg.rail-tag` belongs to a rail tag marker |
| **M60** | the inline font dropped, on the theory that a stylesheet supplies it | **killed**, node and e2e | `keyglyphs` 8 and `A1x2`. It does not: `.rail-tag-marker svg text` is scoped to the MARKER |
| **M61** | `P1e`'s explicit equality reverted to the superset, AND the transfer row moved to the bottom | **killed**, e2e. **And it corrected the round's own account of what it had changed**: `pin()` has always been an ordered deep equality, so the superset lived in the `.filter(...)` this round removed, not in the assertion it added. The explicit `toEqual` is for the failure MESSAGE. That is now written at `P1e` rather than implied | `P1e`, through `pin` |
| **M64** | the same reorder with every guard INTACT, which is M61's other half | **killed**, e2e | `P1e`, on the ORDER. No name left, no count moved, and the node oracle passes because it looks a row up BY NAME: the ordered equality is the only thing on this repo that sees a reordered row |
| **M62** | `A1x` updated to 17 as the ruling named, and the third count left at 18: the caller who read the ruling literally | **killed**, e2e | `D2l`. Nothing about the panel is wrong in this mutation; the gate is, and that is the finding |
| **M63** | the Q1 exemption asserted as a comment rather than as a measurement: `D5d` claims the ferry boat clears in the light theme | **killed**, e2e | `D5d` itself, at 1.31 against a floor of 3. Which is the point: the exemption in `ACCESSIBILITY.md` is held by a measurement that fails the day the hull gains an ink edge, not by a sentence |



## Stage MR5: popups

The last stage. The popup chrome, ONE vocabulary every system renders through, and the rule
the whole stage turns on: **the words are the app's**. The Position row renders
`positionQualifier`'s output and the `.fresh` footer the per-system freshness the app already
computes; the README's `Live GPS | Scheduled, no GPS | Placed from arrivals` and
`LIVE / UPDATED 12S AGO` are not typed anywhere.

### The rulings this stage opened with

Four, taken before any code was written, each because the design of record and the app
disagreed and the disagreement was measured rather than argued.

| | The question | The ruling |
| --- | --- | --- |
| **S1** | **Which form of `positionQualifier` does the Position row render?** The railroads compose their line inline in `systems/railroad.js` and print `position.compact` UNCONDITIONALLY; every other family calls the shared `positionLineHtml`, which prints `position.words` and prints NOTHING when `kind` is falsy. Measured across all fourteen position states, the two forms differ in exactly one family of rows (`placed`) and the presence rule differs for one (`reported`, fresh). | **`.words`, through one helper, and the silence rule is kept: an empty `.words` omits the row.** Two rider-visible strings change, both on the railroads, and both are the freshness contract reaching a surface that predated it: a `placed` row gains a word (`scheduled (no GPS)` to `scheduled position (no GPS)`) and a fresh GPS row stops printing `live GPS`, which is what every other family has always done. Recorded here as the before; the new string pin is taken AFTER the change and the retired markup pin keeps the before. |
| **S2** | **What does the `.fresh` footer say when a feed is live?** The README wants `LIVE / UPDATED 12S AGO`. The app has no word for live: silence means current on every surface it owns (memo D9, and `D1e` pins that a healthy day's status note is empty). `feedDotState` computes exactly the three states the footer needs; `feedTooltip` carries their words but appends the strip button's own action. | **The footer's square is the feed strip's dot at the popup.** Present in all three states, no text when live, the app's own strings when stale or schedule-only, and its accessible name from the same helper the strip's dot uses, so the state is said one way on both surfaces. Nothing is coined. The state clause is lifted out of `feedTooltip` into a shared helper both call, so the two cannot drift. |
| **S3** | **How does auto-pan clear the chrome?** The README's recipe is `autoPanPaddingTopLeft = [24, headerBottom + 12]`, `autoPanPaddingBottomRight = [110, 40]`, then `_adjustPan()`. **Measured on this app, the literal recipe is broken.** See the section below. | **Clamp each padding to what the measured map and popup can satisfy**, keep `panPopupClearOfChrome` as the authority for the real boxes and for post-paint growth, and stand down while `riderOwnsTheView`. The cap and the stand-down are pinned. The README carries an erratum beside its recipe. |
| **S4** | **How does the Key explain the rail tag's head (finding F17)?** The map draws FOUR heads, not the three a first reading suggests: filled chevron, outlined chevron, outlined dot and a filled dot (a GPS fix that serves no bearing). | **Two rows, framed by AXIS rather than by shape.** One row for filled against outlined (the heading is trusted, or it is not), one for chevron against dot (a heading is served, or it is not). `A1x`, `D2l` and `P1e` move by two, recorded as before. |

### The ferry hull's ink edge, and an exemption that ended by measurement

MR4's ruling Q1 left one paint on this map under the 3:1 a mark owes: the ferry boat's hull at
**1.31** against the light paper. A boat is filled with the colour NYC Ferry publishes for its
route, this app does not move a published fill, and the hull's only other paint was the paper
casing, which cannot raise a fill's ratio against paper because it IS approximately the paper.
MR4 reported it rather than promising the floor, and said in the assertion itself what would
end it.

**Two strokes on one path, wider first.** A stroke is centred on its path, so one stroke cannot
be both the casing and the edge. The paper goes to 2 (one unit out, one in) and the ink follows
at 0.8 on the same geometry, drawn second, so it lands on the boundary with a full unit of
paper still outside it. Reading outward a rider gets the route's published fill, the ink edge
that finds it, the paper casing that separates it from the tile. The drawn mark grows half a
unit on each side and stays inside its 22x14 box. **Measured after: 14.86 in both themes.**

**The exemption ended because the measurement moved, not because a sentence was edited.**
`theme.spec.js D5d` asserted `bestOf("light", "ferry boat") < 3`, deliberately the wrong way
round, with the note that the day a stage gave the hull an ink edge the assertion would fail
and `ACCESSIBILITY.md` could be strengthened. It did fail, and it is inverted here rather than
deleted. Two assertions replace it: every family now clears the floor in both themes, and the
hull's FILL is still the feed's yellow, which is the half of Q1a that says what may not change.

**What moved with it**, named because this is a marker change inside a popup stage:

| | |
| --- | --- |
| `markers/ferry` (P1m) | regenerated: the hull is two paths now |
| `contrast/marks` (P4c) | regenerated: `ferry boat` goes from `path fill` at 1.31/3.74 to `path stroke` at 14.86/14.86 |
| `theme.spec.js` D5d | the exemption inverted, plus the published-fill assertion |
| `theme.spec.js` D5c | the hull becomes TWO rows, casing and edge, which is stronger than the one it replaces: it says which path carries which token, so an edit that swapped them fails where "the strokes are paper" could not have seen it |
| `families.test.js` | asserts the order and the relation (casing wider, drawn first) rather than a literal width |
| `ACCESSIBILITY.md` | the exemption paragraph strengthened, keeping the history |
| the MR4 captures | all ten `after-*` frames regenerated in one run, so they stay mutually consistent; the City and Region presets are the ones that actually show a boat |

### The pins invert, and the retired goldens are the before

MR1 through MR4 pinned every popup's HTML byte for byte, and each of those stages said in as
many words that "the popups are stage MR5, so a popup that moves here is a defect". That claim
held for four stages and it is what let them restyle the whole map without touching a word a
rider reads. **MR5 changes this markup on purpose**, so the same pin is now a pin on the thing
being changed, which is the error the header of `pins.spec.js` exists to warn about.

So the pins invert. What is pinned from here is what a rider READS, extracted as TEXT rather
than as markup, taken before any restyle and held after: **`popupText/stock`** (all fourteen
surfaces, one per system and kind) and **`popupText/f01`** (the ladder world, one railroad
popup per position state, which is where `positionQualifier`'s vocabulary is on screen at
once). The fourteen `popups/*` entries they replace were these, and this table is their record:

| system | surface | markup pinned |
| --- | --- | --- |
| `airtrain` | `airtrain station` | 256 |
| `buses` | `bus` | 79 |
| `ferry` | `ferry boat` | 117 |
| `ferry` | `ferry dock` | 269 |
| `lirr` | `lirr station` | 381 |
| `lirr` | `lirr train` | 250 |
| `mnr` | `mnr station` | 368 |
| `mnr` | `mnr train` | 98 |
| `njt` | `njt station` | 410 |
| `njt` | `njt train` | 230 |
| `path` | `path station` | 344 |
| `path` | `path train` | 202 |
| `subway` | `subway station` | 336 |
| `subway` | `subway train` | 186 |

**Three views, not one, and none of them is `textContent` or `innerText`.** `textContent`
inserts nothing at element boundaries, so a dropped `<br>` is invisible and two words fuse
into a run-on a pin cannot tell from the real thing. `innerText` is computed from rendered
boxes, so it moves when `display` moves, and changing `display` is this stage's entire job.
The reader is a tree walk: `seen` (every text node), `spoken` (the same with `aria-hidden`
subtrees removed, which is what a screen reader gets and what will diverge once the map's own
marks sit inside `.pt`), and `labels` (text that exists only in an attribute, which today is
exactly the dock's `title="Wheelchair accessible"` and which a reader of text nodes alone
would lose silently).

**The seconds are redacted and the redaction is asserted.** An age under 120s renders in
seconds, and the settle loop advances a paused clock, so a live feed's age is `1s` or `2s`
depending on how many round trips an arrivals fetch took. `smoke.spec.js` already takes this
exact escape for the same reason. Digits become `{n}` and no surface may carry a raw seconds
age into the golden.

**And the inversion is more dangerous than the pins it replaces**, for one structural reason
worth stating plainly: in MR1 through MR4 these goldens were ASSERT-ONLY, so a broken reader
FAILED. MR5 regenerates them by design, so every emptiness that used to be loud becomes silent
the moment it is written into the golden and matches itself forever. Two guards landed with
the inversion, both outside `pin()`:

- **`MR_PINS_REGENERATE` is now asserted unset in CI.** Nothing anywhere checked it, and the
  regenerate branch returns before `expect` ever runs, so a shell with it exported saw every
  pin in the file pass and a CI job that inherited it would have been green forever against an
  empty golden.
- **A pin key is asserted to be at most two levels.** `pin` destructures exactly two segments,
  so a three-level key writes to its second and drops the third silently; two such keys
  overwrite each other and the failure reads as an unrelated diff.

Per surface: a floor on the length read, a check that the fourteen surfaces do not all read as
one string, and the dock's attribute-only label asserted present so `labels` is known to be
doing work. P4b2 is the round this repo paid for that lesson in.

### The coverage test, and what it can and cannot prove

The claim is that every rider-visible string in a popup is pinned, and the hard part is making
it something other than a list checking itself. **`P5b` builds its inventory from the running
app, never from the golden.** The roots are read off live objects (each popup's bound content
function, and `openStation.render` for a station board), the call graph is walked by taking
each function's own source through `Function.prototype.toString` and resolving its callees on
`globalThis`, and the literals are extracted from that source. This works here and only here
because the app is BUILDLESS: the page is plain ordered `<script>` tags, so every popup builder
is a top-level function and `String(fn)` is the real source of the real function.

Three things the first runs taught, each now written into the test:

1. **Comments have to come out first, with a scanner rather than a regex.** `railroadPopup`'s
   own comment contains the word "station's", and that apostrophe opened a single-quoted string
   to a naive scanner which then ran across three template literals and reported four lines of
   prose as rider text.
2. **A candidate is prose or a label, never an identifier.** Two letters together, then either
   a space or a capital. Without that filter the report was 72 lines, 50 of them enum values
   and field names, and a waiver list that long IS the escape hatch.
3. **Entities are decoded**, because the builder writes `&middot;` and the rider reads the
   character.

**What it cannot prove, stated here rather than discovered later**: a lost DATA string, a train
number, a station name, a headsign, is an interpolation and invisible to it. Only the pins see
those. It is a supplement to `P5a` and `P5c`, never a substitute.

**The waivers are two maps, not one.** `NOT_RIDER_TEXT` is "a rider never reads this" (three
entries: two Intl arguments and `positionQualifier`'s spoken form, which reaches a marker's
accessible name and never a popup). `UNREACHED_STATES` is "a rider does read this and no world
pinned here renders it" (fourteen entries, each naming the state), which makes it a backlog
with reasons rather than an excuse, visible in every diff. A waiver the extractor no longer
finds is itself a failure, so dead ones cannot accumulate behind the live ones.

### The README's auto-pan recipe is measured broken on this app

Recorded here and as an erratum in `docs/design/map-redesign/README.md`, because a recipe that
is followed literally and then produces a defect is worse than one that says why it cannot be.

**The first break: the padding has no viewport-fit guard and the correction cannot rescue it.**
`#panel` is capped at `calc(100% - 72px)` (`style.css`), so with the Key open at 375x667 the
header's bottom edge is 579 and the README's top padding is 592. Leaflet's own arithmetic is
`i.y+e+o.y>s.y && (a = ...); i.y-a-n.y<0 && (a = ...)`, and **the second assignment overwrites
the first**, so when the top and bottom paddings cannot both be honoured the top wins
unconditionally. Measured: `_adjustPan()` then puts the popup at `top 592, bottom 718` on a
667px map, 51px off the bottom edge. `popupClearingShift` returns **null**, correctly and
uselessly: it is a collision solver and the popup is not colliding with anything, it is simply
gone. The same arithmetic at 320x640 gives the same result, and `mobile.spec.js A6l` is the
spec that proves the header really does reach that cap. Horizontally the paddings are
unsatisfiable at phone widths too: `24 + 110` leaves 241px at 375 for a popup that is 256px at
its own CSS floor, and Leaflet silently drops the right padding by the same last-write-wins.

**The second break: Leaflet's padded autopan overrides the rider, and no spec would catch it.**
The app's stated rule is that it does not tidy a position the rider chose (`riderOwnsTheView`,
and "animate the journey, never the adjustment"). Leaflet's autopan has no such guard, and
`popup.update()` runs it on every fifteen-second poll for every open vehicle popup. The
README's padding grows the band in which that fires from a 5px strip to the whole header.
Measured: after three rider drags the centre moved to 40.61903 with `riderOwnsTheView` true,
and the next poll's `update()` put it back to 40.67322. **`layout.spec.js A4j` explicitly
declines to assert against this** ("if Leaflet autopanned, the centre moved for a reason that
is not this correction"), so widening the hole would have been invisible to the whole suite.


### F17 paid: the Key gains the head's two axes, by axis and not by list

MR4 round 2 recorded F17 rather than fixing it, and said why: "It is a new row with a new
sentence, which is a ruling this round does not have." The ruling arrived with this stage
(**option 3, framed by axis**), and the framing is the whole content of it. The tag's head
answers two independent questions, and a panel that answered them with four pictures would be
another list of shapes, which is what F16 was about. So it is two rows, one per axis, and each
row holds the other axis constant:

| row | the axis | left mark | right mark | held constant |
| --- | --- | --- | --- | --- |
| "filled when it is trusted, outlined when it is not" | is the heading TRUSTED | chevron, ink fill, 1-unit paper edge | chevron, paper fill, 1.4-unit ink edge | the shape: a chevron twice |
| "a chevron when one is served, a dot when none is" | is a heading SERVED | chevron, ink fill, 1-unit paper edge | dot, ink fill, 1-unit paper edge | the fill: the filled form twice |

**The oracle is `railTagSvg`'s own output, not a number typed into the test.** `keyglyphs.test.js`
test 10 asks the map's builder for a chevron-headed tag and a dot-headed one and compares: each
row's paths must equal `railTagChevronPath(cx)` for the cx the row drew at, the outlined head's
edge weight must equal the weight the map's outlined head carries, and the dot's radius must
equal the map's. `RAIL_TAG_DOT_R` and `RAIL_TAG_TRACK_Y` are not exported and exporting them to
assert against would only have moved the copy; the claim is that this panel's head is the head
the map draws, so the map is asked for one. That is the same discipline as test 8, whose oracle
for the tag body is `railTagSvg` rather than a transcription of it, and it fails in the direction
the defect actually travels: when the MAP changes and the panel does not follow.

**The literals are H3's, again.** Both rows paint in the light theme's resolved `#201e1d` and
`#f3f2f2` rather than in `var(--ink)` and `var(--paper)`, because `--glyph-plate` is `#f3f2f2` in
both themes: a glyph in the tokens would be a dark plate on a light one the moment a rider chose
dark. `PAPER` and `INK` in the test are read out of `style.css`, so a token edited without the
glyphs following fails here too.

**The three counts move by two, as the ruling said, and are recorded as before.**

| pin | was | is | what it counts |
| --- | --- | --- | --- |
| `a11y.spec.js` **A1x** | 17 | **19** | `.legend-row` plus `.legend-note`, at three widths in both themes |
| `subway.spec.js` **D2l** | 16 | **18** | `.legend-row` alone |
| `pins.spec.js` **P1e** | ordered equality on 17 | **ordered equality on 19** | the accessible names themselves |

The `legend/names` golden gains exactly two lines, inserted where the rows sit in the document:
after "LIRR / Metro-North / NJ Transit train (scheduled or estimated, no GPS); NJ Transit is
always this" and before "LIRR / Metro-North route line". Nothing else in the file moves, which is
the point of an ordered equality: had a third row drifted in the same commit, the diff would say
so.

### The chrome, and three things that only appeared once the popup stopped being white

Section 5's wrapper is the first translucent surface this app has ever painted, and the first
that is not `#ffffff`. Both facts broke something, and neither was visible by reading.

**The design's own spelling of the surface defeats four contrast readers and axe.** This is
recorded even though the surface ships opaque, because it is the reason the 94% could not have
been shipped even if the ruling had gone the other way, and because it says what a future stage
must not reach for. `color-mix(in srgb, var(--surface) 94%, transparent)` computes, measured in
the shipped Chromium, to `color(srgb 0.917647 0.913725 0.913725 / 0.94)`. Every contrast reader in
this repository parses `rgba()` and nothing else: `layout.spec.js` A4g's nearest-opaque-ancestor
walk, `tests/e2e/contrast.js`'s `parse`, and this stage's own first draft of D6a. Three of them
read it as absent and one as opaque. Measured: A4g's walk skipped the popup entirely and reported
the N train's head against `.leaflet-container`'s `--bg` two elements further up, and D6a's
`alpha()` returned 1 and certified a translucent popup as solid. **The reader that cannot be
taught is axe-core's**, and an axe that cannot determine a popup's background reports its content
UNDECIDABLE, which is the exact hole MR1's A4 work climbed out of.

A translucency that reaches the page therefore has to be spelled `rgb(... / 0.94)` to be visible
to the tests that would report it, and is undecidable to axe either way. `frontend/tokens.test.js`
asserts both spellings absent from the popup rule for that reason: the `color-mix` form because it
is unparseable as well as translucent, and any alpha form because of the ruling below.

**Every popup head's ink was computed against white, and a popup is not white.**
`readableInk(color, background = "#ffffff")` walks a route's published colour toward
legibility until it clears 4.5:1 against the background it is given, and all six heads took the
default. That was true while a Leaflet popup was white; in the dark theme it never was, and
after this commit it is not true in either. A4g caught it on the commit that changed the
surface, which is the gate doing its job: `rgb(138, 110, 0)`, the N train's `#FCCC0A` walked
against white, reads **4.36** against what A4g could see and **4.02** against the popup's own
`--surface`.

| route | published | walked against white | against the popup's surface |
| --- | --- | --- | --- |
| N (subway) | `#FCCC0A` | `#8b7005`, 4.75 on white | `#7e6605`, 4.57 |
| PATH | `#d93a30` | `#d93a30` untouched, 4.57 | `#c3342b`, 4.51 |
| East River (ferry) | `#00839c` | `#007c94`, 4.87 | `#006f85`, 4.80 |
| ferry fallback | `#78909c` | `#60737d`, 4.95 | `#5a6c75`, 4.52 |
| NEC (NJ Transit) | `#DD3439` | `#DD3439` untouched, 4.54 | `#bc2c30`, 4.89 |

`popupSurfaceColor()` resolves the token beside `paperColor()`, `inkColor()` and
`scheduledColor()`, and `--surface` rather than the composite is deliberate: the popup is 94%
of `--surface` over `--bg`, and `--surface` is the DARKER of the two in the light theme and the
LIGHTER in the dark one, so it is the end that gives dark ink and light ink respectively the
least to work with. Ink that clears here clears on the real composite.

**And a resolved token is a string, so a theme swap has to rebuild what resolved it.** This is
MR4's canvas lesson one surface further out: `applyTheme` repaints the canvas families because
a 2D context takes a colour string, and it now also calls `popup.update()` on every open popup
because a popup head takes one too. Without it a popup built in the light theme keeps
light-theme ink on a dark surface until the next fifteen-second poll, and a STATION popup keeps
it until the rider closes it. `popup.update()` rather than a close and reopen, so the rider's
focus stays where it is.

**The clamped horizontal padding leaves a phone-width popup almost no freedom, and two specs
were staged on the freedom it used to have.** Section 5's 220px content floor makes the popup
263px wide at 375, and ruling S3's clamp then pins it to x 2..265: 112px of slack in the whole
axis, spent entirely on the design's 110px right padding. Measured consequences, both in
`layout.spec.js`:

- **A4j** dragged 20px LEFT, which now puts the popup at x -18..245, off the map.
  `popupClearingShift` refuses any move that leaves the viewport, so no clearing move existed
  and the spec's own anti-vacuity premise read false. The spec has now been wrong in both
  directions: its first draft dragged RIGHT and pushed the popup past 375. It drags STRAIGHT
  DOWN, which cannot reach either edge and is a takeover all the same, because `dragstart` does
  not care about the direction.
- **A4l** asserted that a clearing move existed immediately after Leaflet's own autopan. With
  the padding in place that autopan now lands the popup CLEAR of the header, so there was
  nothing to clear and the premise read false on a build whose guard was working. The premise
  moved onto the box the guard is actually tested on, the GROWN one, which is where A4j's
  equivalent premise already lived: the popup is clear before the growth, the growth puts it
  back under, and a clearing move exists for that box.

Both are re-staged rather than relaxed, and each new number is a measurement.

**And the app took the pan outright, because the clamp could not be applied to Leaflet's.**
Leaflet's autopan runs inside the open, BEFORE any `popupopen` handler, and the clamped padding
cannot be in place for it: the padding is a function of the popup's rendered size and the popup has
no rendered size until it is in the document. Left on, that is TWO pans per open, Leaflet's with
its default 5px strip and then the app's with the clamped padding. **`motion.spec.js` A5e caught
it**: its `distinct` centre count went from 1 to 2 against a claim that the map "must not travel
through intermediate positions". Both pans are synchronous and unanimated, so no frame is painted
between them and the assertion is a proxy rather than the thing itself, but a proxy that has to be
argued with is a proxy worth satisfying.

So `POPUP_OPTIONS` carries `autoPan: false` and `applyPopupAutoPan` flips it on for the length of
one `_adjustPan()` call. One pan per open, with the right padding, owned by the app. **That also
closes the second break the README's erratum records, structurally rather than by guard**: the
erratum's complaint was that `popup.update()` re-ran Leaflet's autopan on every fifteen-second poll
for every open vehicle popup, with no equivalent of `riderOwnsTheView`. With `autoPan` off at rest
a poll cannot pan at all, and the only autopan in the app is one it asked for and brackets.

**A4l followed, and it is renamed rather than re-staged.** It was "Leaflet's own autopan is not the
rider taking over" and it drove `popup.update()` to make Leaflet pan. That path now stages nothing,
so its premise read false on a working build, which is how this was found rather than reasoned. The
defect it guards is untouched: the app's autopan fires the same `autopanstart` and the same
`movestart`, so `leafletAutoPanning`'s lifetime is exactly as decidable and exactly as easy to get
wrong. Only the caller moved, so the staging calls `applyPopupAutoPan` and the title says "an
autopan".

**Two smaller things, recorded because each cost a wrong assertion.** Leaflet's own default
`minWidth` is **50**, so "no bind site names a minWidth" cannot be spelled as null: D6b asserts
instead that every popup reports the SAME number and that it is the library's, which is what
catches the 170 the station popups used to carry. And the ink edge is on the wrapper alone: the
tip is one square rotated 45 degrees, so a `border-left` on it paints a diagonal stripe across
the arrow rather than a rule down the popup's side. D6a asserts that in both directions, so an
edit that tidies the rule onto the shared selector fails.

**M47, so far.** The stage was asked whether the popup's translucent surface gives the contrast
measurement's alpha branch an element to reach. Not yet, and not for the reason the question
supposed: the translucency is a COLOUR alpha on a background, which R15's repair already
composited, while M47 reverts the ELEMENT alpha (`opacity`, `fill-opacity`, `stroke-opacity`)
and `tests/e2e/contrast.js` measures marks on the MAP rather than popup content. The live
question is the next commit's: the rail tag's solid body carries a `--paper` backing at
`opacity="0.9"`, and on the map that composites over `--paper` and returns it, which is exactly
why M47 survived. Inside a popup it would composite over `--surface`, where it does not. The
determination is recorded there.

### Two measurements, one ruling, and one that ruled itself

**`readableInk` cannot make a brand colour readable on a dark surface, and this is what forces
the design's colourless title.** The function walks a colour toward legibility by SCALING IT
DOWN (`for (let scale = 0.95; scale >= 0; scale -= 0.05)`) and its last resort is `#000000`,
under a comment that says in as many words "black fails nothing on a light surface". On the dark
theme's `--surface` it fails everything: measured, **eighteen of the app's twenty-seven subway
route colours come back as `#000000` at 1.49:1**, and so does every colour that cannot clear 4.5
as published. Nine clear it as published; the rest cannot be rescued in the direction this
function moves.

That was harmless while a Leaflet popup was white in BOTH themes, which it was until this stage:
the dark theme shipped in MR4 over a white popup, so no caller had ever handed this function a
dark background. Section 5's surface is the first, the defect is created here, and axe named it on
`[b, .popup-sub]` at all three widths the moment it was.

**FIXED IN THE HELPER RATHER THAN AT THE SIX CALL SITES**, because the bug is the helper's: a
function whose contract is "the ink to print ON this background" was returning an unreadable
answer for two thirds of its inputs and reporting nothing. It now asks the BACKGROUND which way
there is room to move, by comparing black and white against it, and tints toward white where
darkening cannot help. `c + (255 - c) * scale` mirrors the scaling A3 chose for the other
direction and for A3's reason, that it preserves the hue: measured on the dark surface, eight of
the eleven distinct subway colours move and three already clear, with `#c0392b` becoming `#d67e75`
at 4.76, `#1e8449` becoming `#56a377` at 4.63, and `#FCCC0A` left alone at 9.24.

**THE DARKENING PATH IS THE OLD LOOP, CHARACTER FOR CHARACTER, AND THAT COST A MEASUREMENT.** The
obvious rewrite folds both directions into one loop, `1 - step` against `c + (255 - c) * step`.
That is not the same function: 0.05 has no exact binary form, so counting down by subtraction and
up by addition accumulate different error, and at a rounding boundary they disagree by one unit per
channel. Thirteen of this app's own colours came back different on the light surfaces (`#FCCC0A`
on white went `#8b7005` to `#8b7006`), which would have moved thirteen pins for a reason unrelated
to the repair. So the two directions are two loops, and `helpers.test.js` compares the new function
against a transcription of the old body over every colour the app ships and six surfaces: 168
comparisons, zero differences, and at least eight deliberate disagreements on the dark surface so
the guard cannot pass by the repair having done nothing.

**Section 5's `.pt` says the same thing in its own vocabulary**, by giving the title a weight, a
size, a letter-spacing and a route MARK and no colour at all, which is MR2's rule that "the brand
colour stays on the SHAPES that carry identity". That is the vocabulary commit's to implement; it
is no longer this measurement's to force, because the helper is correct on either surface now.

**And the translucency does not merely make axe unsure, it hides a real violation.** With the
popup opaque, axe reports a genuine `color-contrast (serious)` failure on `[b, .popup-sub]` in
the dark theme at all three widths: the head's ink (above) and `.popup-sub`'s hard-coded `#666`,
which is a light-theme grey on a dark surface. With the popup at 94%, the SAME two nodes are
reported as `incomplete` instead, "background color could not be determined because element
contains an image node", because 6% of a tiled basemap shows through and axe will not composite
an image. Measured: opaque, three A1w states fail and all three are real violations naming the
defect; translucent, eight fail and not one of them names it.

| A1w state | opaque | at 94% |
| --- | --- | --- |
| popup open with cross-link, light, 1280 / 375 / 320 | pass | 4 undecidables each |
| popup open with cross-link, dark, 1280 / 375 / 320 | **violation**, `[b, .popup-sub]` | 4 undecidables each |
| panel detail, 1280 (popup still open) | pass | 4 undecidables |

The `.popup-sub` half and the head's ink are this stage's to fix either way, and fixing them
does not change the table's right-hand column: a decidable popup is what makes axe's answer mean
anything, and 6% of tile is what takes it away. `UNDECIDABLE_SHAPES` can hold a popup shape and
the machinery is built for exactly this ("a new shape to add WITH a decider spec"), but that
list's own standing rule is that **the inventory shrinks by conversion and never grows by
exclusion**, so four new entries would have been a policy act rather than an implementation
detail.

**RULED: the popup ships at full `--surface` opacity, the way MR1 overruled the header's 90%, and
the undecidable inventory does not grow.** The ink edge is kept; the blur is not (below).
`tokens.test.js`
holds the rule against both spellings of a return; `helpers.test.js`'s A3 sweep, whose popup half
had measured three greys against a literal `#ffffff` since MR1 with the note "until MR5 restyles
them", now resolves the popup's inks per theme beside the chrome's and asserts the popup surface
is a token exactly as it already asserted the header's. An erratum sits beside section 5 in
`docs/design/map-redesign/README.md`.

**And the blur went with the alpha, on a second ruling and by the same precedent.** At full opacity
`backdrop-filter: blur(14px)` paints nothing: a backdrop filter filters what is behind the element
and the element's own background then paints over it, so with alpha 1 and no radius none of the
filtered backdrop is ever visible. MR1's F1 took the filter off the header along with the header's
90%, and this is that pair one surface out.

**The first draft kept it, with a comment and a test explaining that it was inert, and that was the
wrong shape.** `tokens.test.js` asserted the declaration PRESENT and asserted that the sentence above
it still read "PAINTS NOTHING", which is a guard on an explanation rather than on the page. The
operator's ruling names the principle: **a rule measured to paint nothing is not kept with a test
saying so.** The declaration is gone and the test is inverted, in the same words the A3 sweep already
used for `#panel`, so the popup and the header are now asserted alike in all three ways: the surface
is a token, there is no backdrop filter, and there is no alpha. The day a decider spec makes a
translucent popup measurable again, the blur comes back with the alpha it belongs to.

### The mutations, and the one that survived long enough to find three things

Ten mutations against the chrome commit, each in a worktree detached at it with the sha echoed and
compared before anything ran. The table is a script,
`docs/reviews/map-redesign/mr5/mutations.sh`, rather than prose: MR4's F19 was a mutation that had
been printing `ANCHOR MISS` and testing nothing for a whole round, and standing rule 6 came out of
it. A table in a markdown file cannot be re-run, so this one is not in one.

Nine ran against the commit as first written; **M66** survived it, and its re-run is against the
same commit with D6i added, which is what the survivor bought. Rule 6's whole-table re-run at the
branch tip happens before the push and is recorded there, so this section is the round's finding
rather than the push's evidence.

| # | the defect introduced | verdict |
| --- | --- | --- |
| **M60** | the autopan's top padding taken from a literal instead of the rendered header's edge | **DIED**, D6d: "and the derived one is cut to what is left" |
| **M61** | the clamp removed, so each padding is the README's recipe verbatim | **DIED**, two S3 node tests |
| **M62** | the stand-down skips rather than disarms the padding | **DIED**, D6f: "the padding is DISARMED, not merely skipped" |
| **M63** | `readableInk` only darkens again, as it did before this stage | **DIED**, both new node tests |
| **M64** | the popup surface goes back to the design's 94% | **DIED**, A3's sweep and two `tokens.test.js` tests |
| **M65** | `.popup-sub` back to A3's `#666` | **DIED**, A3's sweep |
| **M66** | the ferry popup head loses the resolved surface, so it inks against the light fallback | **SURVIVED first, then DIED**; see below |
| **M67** | the theme swap stops rebuilding open popups | **DIED**, D6h: "the head was re-inked against the dark surface" |
| **M68** | `autoPan` left on, so Leaflet pans and then the app pans again | **DIED**, A5e: "the map must not travel through intermediate positions" |
| **M69** | one bind site loses `POPUP_OPTIONS` | **DIED**, D6b, naming the bind site |

**M66 SURVIVED, AND THE REASON WAS COVERAGE RATHER THAN A SLEEPING ASSERTION.** Removing
`popupSurfaceColor()` from the ferry boat popup's arguments makes its head ink against helpers.js's
light-theme fallback and then print it on the dark surface, measured at **2.42:1**. Nothing failed.
`a11y.spec.js` A1w's popup states open a SUBWAY train popup and `layout.spec.js` A4g opens the
subway and NJ Transit ones, so the bus, railroad, PATH and ferry heads and every dock board's
route-coloured bucket heading were **never measured anywhere**. Five of the six route-coloured heads
could have been inked against the wrong surface with every gate green, which is the same shape as
the defect the head's ink was in the first place.

**D6i closes it**: all fourteen surfaces, both themes, every INLINE colour a popup prints, measured
against what it is actually printed on. Inline specifically, because a colour that came through the
cascade is a token and follows the theme for free; the ones that cannot are exactly the ones a
builder resolved to a string. M66 now dies on it, naming `East River, rgb(0, 111, 133) on
rgb(45, 43, 43), 2.42, need 4.5, ferry boat @ dark`.

**And writing D6i cost two findings of its own, both worth the record.**

1. **Its first draft passed while measuring nothing, in the spec written to close exactly that
   trap.** The in-page block is a template literal handed to `new Function`, so every escape is read
   twice: `/[\s,/]+/` in the file arrives as `/[s,/]+/` in the page. That split colour strings on
   the letter "s", so `Number("(45")` was `NaN`, every ratio was `NaN`, and **`NaN < 4.5` is false**,
   so there were no failures. It was spotted because the debug dump showed every label with its
   letter "s" missing ("Ea t River", "Hud on"). The block now parses with `indexOf` and a literal
   comma and contains no backslash escapes at all, and the spec asserts **every ratio is a finite
   number** before comparing any of them, because a NaN is indistinguishable from a pass at the
   point of comparison and the only place to catch it is before.
2. **Ink on a fill is not ink on the surface.** The first working reader measured `.arr-badge`
   against the popup surface and reported twelve badges as failures at 1.21. A badge's white or
   black text is `readableTextOn`'s answer against the badge's own route-coloured fill and is
   theme-independent; A4g already draws that line between its "ink" and "fill" samples. D6i now
   measures against an element's own background where it has one, and asserts both kinds are in the
   sample so a reader that silently classified everything one way cannot certify the other half.

### Ruling Q1: the railroad popup joins the contract, and what its two strings were

The railroad popup was the app's one surface that rendered a position's words itself. It printed
`position.compact`, from a line written in `systems/railroad.js`, UNCONDITIONALLY, while every other
popup went through `positionLineHtml`. The ruling unifies on `.words` and keeps the silence rule, and
because that popup predated the contract, two strings a rider reads change. **The before, as the
retired pins held it:**

| surface | before | after |
| --- | --- | --- |
| `popupText/stock` · lirr train | `... Next stop: Jamaica Outbound scheduled (no GPS) Also here: Jamaica` | `... scheduled position (no GPS) ...` |
| `popupText/stock` · mnr train | `MNR · Hudson Train 1797 live GPS` | `MNR · Hudson Train 1797` |
| `popupText/f01` · placed | `... Outbound scheduled (no GPS), as of 29m ago` | `... scheduled position (no GPS), as of 29m ago` |
| `popupText/f01` · unqualified | `LIRR · Babylon Branch Train 7566 live GPS` | `LIRR · Babylon Branch Train 7566` |

**Two changes, not four.** The first is the contract's own word: `.words` says "scheduled position (no
GPS)" where `.compact` says "scheduled (no GPS)", which is the single family those two forms differ
in. The second is the silence rule reaching this popup: a FRESH GPS fix now says nothing, because
"silence means current" (memo D9) and this was the only place in the app that broke it. An AGED fix
still speaks, which is the contract working rather than a compromise: `popupText/f01` · aged still
reads "live GPS, as of 5m ago" and did not move.

**`.compact` is not dead and has not been unified away.** `positionQualifier`'s contract owns both
forms and `positions.test.js` still holds the difference between them. What is gone is a SURFACE
choosing between them.

**The pins moved by exactly those four strings and nothing else**, which is what an ordered equality
is for: no station popup, no other system and no other field in the golden changed. The new pins were
taken after the change, as the ruling asked, and the diff is the record that nothing else came with
them.

**And a node test now holds the call site**, in `positions.test.js`, because nothing did: the change
is rider-visible and the whole node tier stayed green through it. It scrapes `railroadPopup` for
`positionLineHtml(position)` and for the absence of `position.compact`, asserts the same absence
across the other five system files, and asserts the two strings from `positionQualifier` directly so
it says what the change IS rather than only which call moved. **It had to strip comments first**: the
comment this stage wrote at the changed line necessarily names `position.compact`, and scraping the
raw source failed a correct build. `pins.spec.js` P5b paid for the same thing in its literal
extractor.

**Q1 leaves `.compact` with no reader, and P5b is what found it.** The coverage test reported
`"scheduled (no GPS)"` as a rider-visible literal in the popup call graph that no pin covered, on the
very commit that unified the call site. It was right: the railroad popup was that form's only reader,
so after the ruling nothing in the app renders it. **The field stays.** `.compact` is one of the three
forms section 3.2 of the freshness contract defines, `positions.test.js` still pins its difference
from `.words`, and deleting a form the contract defines is an amendment to that contract rather than a
stage's tidying. It is waived in `NOT_RIDER_TEXT` rather than `UNREACHED_STATES`, because that second
map is for text a rider WOULD read in a state no world reaches and there is no such state left for
this one. The waiver names the reason so the next stage finds it stated rather than guesses, and the
comment at `positionLineHtml` was corrected: its first draft said `.compact` "is not dead", which a
reader could take as "has callers".

**Three specs outside the pins asserted the old strings, and each needed a different repair.**

- **`smoke.spec.js` C2j**, F01's own acceptance test, read "the 27 fresh fixes are exactly what they
  always were: filled, bright, 'live GPS'". Its `bare()` predicate looked for that string in the
  popup; the tell for "this reads as live" is now the ABSENCE of a position line, so it is `silent()`.
  A check was added that separates "says nothing about its position" from "says nothing at all", so
  it cannot pass on a popup that lost its whole vocabulary. The marker's accessible NAME still says
  "live GPS", because a name renders `.spoken` and silence on a name would say nothing rather than
  mean something.
- **`crosslink.spec.js` A3c** used `toContainText("live GPS")` as its witness that the right popup
  was open. The ruling takes that fact away, so the witness is the train's own number, read out of the
  record rather than typed, which identifies THIS train rather than a class of them. The premise
  nothing had asserted was added with it: that the popup names no next stop, which is WHY there is no
  link to make.
- **`a11y.spec.js`** quotes a past capture containing "live GPS" inside a comment. That is history
  and stays, with a clause naming the ruling so a reader does not take it for a claim about the
  current build.

### Ruling Q2: the state's words, said one way on two surfaces

`feedTooltip` has carried a feed's state since MR1 with the button's action on the end ("Live · 12s ·
hide Subway"). The footer's square is "the feed strip's dot at the popup" with "its accessible name
from the same helper the strip's dot uses", and `feedTooltip` cannot be that helper: a popup has no
button to press, so its words must not end in what pressing one would do. Re-deriving the clause in
the footer would have been a second answer to one question, which is finding N6 one stage earlier.

So `feedStateWords({state, age})` came out and `feedTooltip` is now that plus the action. Nothing is
coined: the four strings are the ones the tooltip already said. The refactor is proven over the whole
matrix rather than on the design's three examples, because "they agree on the examples" is what a
copy looks like from outside.

**The footer is `vehicleStaleLine` restyled, not a second voice.** This is the one thing a footer
added naively gets wrong. Every vehicle popup already ends in `vehicleStaleLine`, which prints "as of
5m ago" when the vehicle's SYSTEM has gone stale; a footer that also said "As of 6m ago" would put
one fact on screen twice in two capitalisations. So the footer takes that line's job and its rule:
where the vehicle's own position has already stated an age at least as old as the feed's, the footer
shows the SQUARE and withholds the WORDS. `vehicleStaleLine`'s own comment is where the reason is
written and it has not changed, that an observation's age and a feed's differ by the provider's lag.
What the footer ADDS is the two states that line never had, live and schedule-only.

**The square is never withheld**, because a rider cannot tell "this feed is live" from "this popup
forgot to say" unless the mark is always there. **And there is no word for live**: the words are said
through A1's `visually-hidden`, so a screen reader gets the state exactly where an eye gets the
square. The README's "LIVE · UPDATED 12S AGO" is the sentence memo D9 forbids and it is typed
nowhere. AirTrain gets a footer reading "Scheduled", which is the same answer the strip gives it.

**The helpers land inert one commit before their wiring**, which is this repo's own idiom: 6.3's
position helpers "landed inert one commit before the gate, and are wired in the gate's own commit",
for the same reason, that the wiring touches every popup and the arithmetic should be settled and
tested before it does.
