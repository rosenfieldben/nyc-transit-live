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

Each stage is one pull request. **The pins land before anything is restyled**, as
their own commit at the head of stage 1, and the C6-series dimming specs, the F01
and F03 e2e specs, and the axe specs at desktop, 375 and 320 stay green at every
stage.

| Stage | Scope | State |
| --- | --- | --- |
| **MR1** | **Tokens and chrome.** The Modernist token set on the root with `data-theme`, self-hosted Archivo 400/600/800, the `.leaflet-tile-pane` filters for both themes, and a `localStorage`-persisted theme toggle. The `<header>` replaces the right-hand `<aside>`: brand and blinking clock, the subway bullet key (display only), the Dark/Light, Key and Stations buttons, the feed strip, the Key panel, and the service alerts strip as a full-width row inside the header. The bottom-right control stack with the City/Rail/Region presets and the restyled zoom control. No marker, line, station, label, popup or route-table change: the pins prove it. | in review |
| **MR2** | **Subway.** Trunk ribbons (casing plus line, yellow drawn last), the bullet train marker with its halo and lift, local dot versus transfer ring stations, the haloed permanent-tooltip labels with their zoom gating, the Names toggle, and route focus wired to the stage 1 bullets. | planned |
| **MR3** | **Commuter rail.** The real route tables (§6 of the brief: name-keyed codes for LIRR and Metro-North, the feed's `route_short_name` and `route_color` for NJ Transit, `route_color` added to `/api/railroad-routes`), the branch lines, the square stations, and `railTagIcon` with the §3.1 provenance states: solid versus outlined body, filled versus outlined chevron, dimming for age. | planned |
| **MR4** | **The other families.** PATH diamonds and lines, ferry dashed routes, dock dots and hulls, AirTrain's gray dashed service, and the bus arrow and dot at the muted hashed hue. The §3.3 dimmed and absent states for each. | planned |
| **MR5** | **Popups.** The `.pk/.pt/.kv/.dir/.arr/.fresh/.alert/.xlink` vocabulary, the §4 words routed from `positionQualifier()` and the per-system freshness rather than re-derived, the arrivals qualifier column, and the autopan padding that clears the stage 1 chrome. | planned |

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
| P1e | Every **accessible name the legend says**, with the `aria-hidden` glyphs removed, so the Key panel is checked against the list rather than against a memory of it. | 17 rows plus the note |
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
| G15 | **In the dark theme every Key panel glyph falls under the 3:1 mark floor**, from **1.11** (the subway station ring) to **2.63** (the ferry dock). They were drawn for an opaque white panel and they carry the map's own marker colours. | **The key is fixed; the map is not, and the map is the real finding.** The glyphs keep their colours exactly and gain the paper they were drawn on, measured at 3.98 to 11.31 in both themes, because a key whose glyphs did not match the map would be worse than a dim one. **THE SAME ARITHMETIC HOLDS ON THE MAP.** The dark basemap filter is MR1's and the markers are not: until MR2 through MR4 give every mark the paper casing and stroke the design specifies, a rider who picks the dark theme gets a map whose markers read at those same ratios. That is the cost of shipping the theme one stage before the marks, it is stated here rather than discovered, and MR2 is where it starts being paid down. |

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

### Before and after

`docs/reviews/map-redesign/mr1/`, from the hermetic harness at the frozen clock with
one agency-wide alert showing, so the pair differs by the stage and by nothing else.

| | 1280 | 375 |
| --- | --- | --- |
| before | `before-desktop.png` | `before-375.png` |
| after | `after-desktop.png` | `after-375.png` |

---

## Stage MR2: subway

*Not started.*

## Stage MR3: commuter rail

*Not started.*

## Stage MR4: the other families

*Not started.*

## Stage MR5: popups

*Not started.*
