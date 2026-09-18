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

| Pin | What it holds |
| --- | --- |
| P1 | The status line's text in the **F03 world** (content stale behind a fresh poll), character for character, as `staleness()` produces it. |
| P2 | The status line's text in the **F01 world** (a railroad position past the age gate), character for character, including the withheld-count clause. |
| P3 | The **alert banner rows** for the existing banner fixture: every row's marks and text, in order. |
| P4 | The **legend's accessible names**, every one of them, so the Key panel can be checked against the list rather than against a memory of it. |
| P5 | Per system, that its **markers' HTML is byte-identical** before and after the stage. Screenshot-free: the assertion is on the DOM, not on pixels. |
| P6 | Per system, that its **popup HTML is byte-identical** before and after the stage. Same shape as P5. |

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
