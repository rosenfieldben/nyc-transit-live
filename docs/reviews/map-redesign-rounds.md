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

| # | Finding | Disposition |
| --- | --- | --- |
| (none) | *Round 1 has not been run yet.* | |

### Mutations

Each on a fresh copy of the tree (`git ls-files --cached --others
--exclude-standard`, so a new untracked file is included), the mutation applied
alone, and the named tier run against it.

| # | Guard reverted | Result | Killed by |
| --- | --- | --- | --- |
| M1 | `aria-pressed` not updated when a feed is toggled | pending | |
| M2 | hidden feed conveyed by opacity alone (the OFF mark and the strike removed) | pending | |
| M3 | the service alerts strip given the fold class | pending | |
| M4 | the trailing note re-derived from the freshness index instead of taking `staleness()`'s output | pending | |
| M5 | the theme not persisted to `localStorage` | pending | |
| M6 | the clock blink not gated by `motionAllowed()` | pending | |

---

## Stage MR2: subway

*Not started.*

## Stage MR3: commuter rail

*Not started.*

## Stage MR4: the other families

*Not started.*

## Stage MR5: popups

*Not started.*
