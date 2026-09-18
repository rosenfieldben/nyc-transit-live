# MR1: tokens and chrome

Stage 1 of 5 of Phase MR, the front-end map redesign, against the v3.1 handoff now
recorded at [`docs/design/map-redesign/`](https://github.com/rosenfieldben/nyc-transit-live/tree/claude/mr1-chrome/docs/design/map-redesign).
The Modernist token set, self-hosted Archivo, the basemap filters, and a `<header>`
that replaces the right-hand legend panel AND the separately positioned alert strip.
**Chrome only, by scope**: markers, lines, stations, labels, popups, the railroad
tables and route focus are MR2 through MR5, and fourteen pins landed before anything
was restyled to prove this stage reached none of them. **No NJ Transit mint was
spent.** Nothing in this branch touches the backend, `njt_auth.py` or any credentialed
path; the contract tier aims every NJ Transit seam at its simulator as it always has,
and neither `contract_monitor.py` nor a fixture generator was run.

| Commit | |
| --- | --- |
| `fe82a66` | the design of record, and the ledger the five stages answer to |
| `03d82b3` | pins: what the redesign is not allowed to change by accident |
| `88f0862` | tokens and chrome, and the status line taken apart |
| `4b5d583` | round 1: twenty findings, six mutations, and the before-and-after pair |
| `4c37c11` | the pull request body, in the F1 form |
| (tip) | round 2: sixteen findings from the adversarial pass, fixed |

## Before and after

| | 1280 | 375 |
| --- | --- | --- |
| before | ![before, 1280](https://github.com/rosenfieldben/nyc-transit-live/blob/claude/mr1-chrome/docs/reviews/map-redesign/mr1/before-desktop.png?raw=1) | ![before, 375](https://github.com/rosenfieldben/nyc-transit-live/blob/claude/mr1-chrome/docs/reviews/map-redesign/mr1/before-375.png?raw=1) |
| after | ![after, 1280](https://github.com/rosenfieldben/nyc-transit-live/blob/claude/mr1-chrome/docs/reviews/map-redesign/mr1/after-desktop.png?raw=1) | ![after, 375](https://github.com/rosenfieldben/nyc-transit-live/blob/claude/mr1-chrome/docs/reviews/map-redesign/mr1/after-375.png?raw=1) |

Both captured from the hermetic harness at the frozen clock with one agency-wide alert
showing, so the pair differs by the stage and by nothing else. The map content is
identical in all four, which is the point.

## What changed, and the mechanism

**The header keeps `id="panel"`, and that is load-bearing rather than lazy.** The
station panel's inert sweep walks from `#stations-panel` up to `<body>` inerting
siblings at each level, and three suites assert the result by id: the inert set is
exactly `["alert-banner", "map", "panel"]`, and `#stations-toggle`, `#legend-toggle`
and a layer toggle must each report `focusable: false` with `ownInert: false`, i.e.
inertness INHERITED from this element. A rename would have turned all of that
green-by-vacuity. The alert strip moving INSIDE keeps that set unchanged for the same
reason: a descendant of an inert element is inert. It becomes a `<header>` rather than
an `<aside>`, so the landmark is `banner`.

**The feed strip is one button per FEED, which is not one per source.** LIRR and
Metro-North are two feeds inside the one `railroads` source: they poll together and
fail apart, the per-system freshness index has always carried them separately, the
status line has always named them separately, and the C6 dimming specs exist precisely
because one can go stale while the other does not. So the railroad layer groups split
per agency, which is the one piece of system plumbing this stage needed and the reason
`IMPLEMENTATION.md` puts that split beside the feed strip. Nothing about a railroad
marker changes: pins P1h, P1i and P1j hold them byte for byte, and the station pin
passes with the two new groups read in the order the one group held them.

**`aria-pressed` and the visible OFF treatment come out of one decision.**
`feedStripModel`'s `pressed` and `off` are one boolean read two ways, so the state a
screen reader is told and the state a rider sees cannot disagree; the strike and the
word are both drawn, because the v3 brief says the fade alone is not state. The eight
buttons are generated from `helpers.js`'s `FEEDS` rather than written into the markup:
eight static copies of a shape that must agree with that table would be eight chances
to drift from it.

**The trailing note is `staleness()`'s output, verbatim.** `map.js` hands `setStatus`
the problems rather than the composed line. `statusNoteText` is extracted from
`statusLineText` rather than written beside it, so the two cannot word the same trouble
two ways, and the node test asserts the composed line's tail IS the note. What matters
more is what it does not do: `problems` arrives already composed by `staleness()`, and
the two clauses that would go missing first if anything re-derived it from the
freshness index are the withheld count and Metro-North's undated clause, because both
RIDE a raised line rather than raising one. `#status` starts EMPTY where the line said
"Loading...": a note whose job is to be silent on a healthy day must not open by saying
something.

**The alerts strip never folds, and that retires a class of bug rather than restyling
one.** v3.1 point 1. It is a full-width row of the header with the red top rule,
carrying no fold class, at every width. Everything that positioned it is gone with it:
`right: 260px` was arithmetic on the legend panel's width, `left: 54px` was "clear
Leaflet's zoom control or the map cannot be zoomed during an alert", and
`--alert-banner-height` was a measured, republished height so the panel's mobile bound
could subtract it. Two rows of one bounded flex column need none of it.

**The header is a bounded flex column whose Key panel is the only shrinkable row**, and
it stops 72px above the bottom edge so Leaflet's zoom control and the OpenStreetMap
attribution stay clear. The attribution is a condition of using the tiles, and measured
at 320 with no reserve the header covered both.

**The Key is a disclosure at every width now**, and below 700px the same one boolean
folds the subway key and the feed strip with it. A6c moved with it and keeps all three
invariants: focus stays on the button, `aria-expanded` and the drawn state are written
together, and crossing the breakpoint without a click is handled.

**Archivo is self-hosted for the CSP's sake.** `font-src` has no directive of its own
and falls back to `default-src 'self'`, so a Google Fonts link and the gstatic files
behind it are both refused. Self-hosting keeps the CSP byte-identical (the backend test
still pins the same string) and means the page makes no third-party request for type at
all. Two files rather than six, because Archivo is variable-only on Google Fonts and
asking for 400, 600 and 800 returns the same `.woff2` three times; one `@font-face` per
subset at `font-weight: 100 900` instantiates all three from one download.

## Where measurement disagreed with the drawing

Four, each in the ledger with its numbers.

| | Drawn | Measured | Shipped |
| --- | --- | --- | --- |
| the header's surface | `color-mix(... 90%, transparent)` + `blur(14px)` | the brand, the clock, all eight feed names and the note go to axe `incomplete` at all three widths, because a translucent surface over a tile IMAGE has no resolvable background | full `--surface` opacity, no backdrop filter |
| the accent as text | `--accent` | **3.47** light, **4.46** dark, against the 4.5 that 10px and 12px text owes | `--accent-ink`, **5.03** light, **5.44** dark |
| the accent as a fill | `--accent` with `--chipink` | white **4.20**, dark ink **4.14**: NEITHER ink is legal on it | `--accent-ink` as the fill, `--chipink` at **5.45** |
| the focus ring | `#1d4ed8` | **5.53** light and **2.10** dark, against the 3:1 A6f enforces | `--focus`, `#7795e8` dark at **4.86** |

**And one the operator has to rule on.** The handoff gives the official MTA trunk
colours and route-bullet markers. This repository's README Notes say the opposite in as
many words: "The MTA's logos, official map, and route symbols require a license. Use
your own colors and markers rather than official MTA branding", and `LINE_COLORS`
exists because of it. The ferry note beside it draws the line precisely: route colours
published in a feed's `routes.txt` are DATA; logos and route symbols are branding.
MR1's subway key is therefore built from `lineColor()` and `readableTextOn()`, which is
both the ruling's requirement and the only honest option, since a key in one palette
over lines drawn in another says nothing true about the map under it. Every trunk
clears 4.5:1 that way, the lowest being **4.72** on the 4-5-6 green; the L's grey takes
dark ink at 5.00 where white would have been 3.48. **MR2 draws the ribbons and the
bullet markers, so MR2 is where this has to be settled.**

## The pins, and what they hold

Fourteen, landed in `03d82b3` with no production change behind them, against a golden
measured from the running page rather than written by hand.

| Pin | What it holds | Measured value |
| --- | --- | --- |
| P1a | the F03 world's status clause | `trains: ACE group as of 10m ago` |
| P1b | the F01 world's, with Metro-North's poll aged so the line is RAISED | `railroad: MNR as of 6m ago; LIRR 24 trains not shown, last seen over 10m ago; MNR position age unavailable` |
| P1c | a healthy day says nothing, and is not painted as an error | `` (empty) |
| P1d | the alert banner's whole subtree, byte for byte | `.alert-banner-strip` / `.alert-banner-rows` / `.alert-banner-row` / `#alert-banner-dismiss` unchanged |
| P1e | every accessible name the legend says, `aria-hidden` glyphs removed | 17 rows plus the note, asserted as a superset |
| P1f to P1n | per system, every mark's HTML (divIcons) or renderer options (canvas circleMarkers) with its anchors and its per-observation opacity, and its popup HTML as a rider sees it | 9 families, 14 popups |

**The status pins assert the problems TAIL, not the composed line**, because the
composed line is the thing this stage takes apart and a pin on it would have been a pin
on the change. `statusTail()` strips a counts-and-clock head when there is one, using
the clock as the marker of its end, so the same pin reads the same string on both sides
of the stage.

**A popup is read through its marker, never through `.leaflet-popup-content`.** This
app can hold a vehicle popup and a station popup open together, so that selector is not
unique and `map.closePopup()` closes only the map's current one. The settle loop runs on
the driver's side, because the suite's clock is paused and an in-page `setTimeout` never
fires: the first cut deadlocked every station pin for the full timeout.

**All fourteen pass across the restyle**, which is the scope claim made in a form that
cannot be argued with.

## How each new claim is witnessed

A guard nobody has seen fire is indistinguishable from one that cannot.

| Claim | Witness | What it shows |
| --- | --- | --- |
| the eight feeds, in the design's order, counted from the app's own registries | `chrome.spec.js` D1a | the strip's counts compared against `trains`, `buses`, the two halves of `railroads`, and the rest in the same breath |
| the dot is the FEED's freshness, not its observations' | D1b | Metro-North's poll aged six minutes and LIRR's not, in the one source |
| `aria-pressed`, the OFF mark, the strike and the layer move together | D1c | all four read in one evaluate, three times |
| the note is `staleness()`'s text exactly | D1d, and pins P1a and P1b | `toHaveText`, not `toContainText`, on the sentence carrying all three clauses |
| a healthy day stays empty across polls | D1e | two poll intervals, with the clock proving the page was alive |
| the alerts strip never folds | D1f at 375 and 320, and `mobile.spec.js` A6c | asserted as the ABSENCE of the fold class, because an empty strip would satisfy a visibility check and prove nothing |
| the theme persists, and renders with storage empty | D1g | reload, and an unrecognised stored value treated as no value |
| a browser that refuses `localStorage` still gets a page | D1h | both accessors made to throw; the markers still load, which means `shared.js` finished |
| Archivo is ours and the page asks nobody else for type | D1i | `document.fonts.check` at all three weights, the harness's leak list empty, and the served content type |
| the presets fly, and stand down when the rider takes over | D1j | the map asked whether it IS at the preset, in pixels |
| every Key panel row is legible, both themes, three widths | `a11y.spec.js` A1x | the decider for the new undecidable shape |
| the whole header fits the viewport in both Key states | `mobile.spec.js` A6k and A6l | at 320 and 375, with one alert and with three, and the Key panel proven to be the row that gives way |
| every new control meets the 24px floor and rings on focus | `layout.spec.js` A4b, `mobile.spec.js` A6f | the theme toggle, the presets and the feed buttons, at every width they are drawn at |
| the clock blinks, and stops under the preference | `motion.spec.js` A5b and A5d | both halves, and the DIGITS asserted as still running, so a mutation that froze the whole clock cannot pass |

## What round 1 found

Twenty findings, in the ledger in full. The four measurement disagreements are above.
The rest, in one line each:

**Six this stage introduced, each caught by a spec before it landed.**
`renderAlertBanner` kept a call to the publisher this stage retired, so the dismiss
button stopped dismissing (A4d). The feed strip painted at module scope and read
registries that do not exist until later files load, which took `shared.js` down and
brought the page up with no markers at all. The Leaflet overrides were written
unprefixed and lost to Leaflet's own two-class rules, leaving the attribution
translucent white over the tiles (A1z, at 4.06). The header covered the zoom control
and the OSM attribution at 320. The view presets tracked a flag, and `flyTo` fires two
end events, so the preset cleared at the instant it became true. The bus route banner
listened for `change` on a checkbox that is now a button (A7f).

**Three the stage exposed, which were already there.** `a11y.spec.js` A1z read the blue
channel as an alpha (`/[\d.]+\)$/` takes the last number before the `)`), latent for as
long as Leaflet's translucent attribution was in force and surfacing as a ratio of
2118582 the moment it went opaque. `assertNothingIsMidTransition` waits for an endless
animation forever, which was unreachable before this app had one. And the legend never
had a dimmed-vehicle row at all, although the freshness contract has drawn one per
observation since 6.3; the Key panel adds it.

**Four in the specs themselves.** Two pin id shapes are invisible to
`tests/specids.js` (`"P5/P6."` dies on the slash; `` `${system.id}. ...` `` is not a
literal first token, and lost all nine ids while every test passed). The
popup-correction specs wrote `80` down, which was right for a tall right-hand panel and
stopped reaching a one-row top bar. A6f's restructure opened a disclosure with a CLICK,
which sets the pointer modality and stops a programmatic focus matching
`:focus-visible`, so every control came back ringless. And A6k, A6l and A6m were three
specs about two boxes that can no longer collide.

## What round 2 found, after the diff was written

An adversarial pass over the production half of the diff raised 25 findings and verified
each against the tree. **Sixteen were real and are fixed**; the ledger has every number.
The five worth reading here:

- **The design's one filled button never rendered at all.** The old A1 `#stations-toggle`
  rule survived the rewrite and sits LATER in the stylesheet at equal specificity, so it
  won: the Stations button drew as the A1 panel's full-width grey row. It is visibly wrong
  in the "after" screenshot committed at `4b5d583`, which this branch replaces.
- **And deleting it exposed a second defect the first had hidden.** With the accent fill
  finally drawn, the focus ring measures **1.10** against it, and no single colour clears
  3:1 against both the fill and the surface beside it. The one filled control now rings
  inside itself.
- **`.alert-stale` is not the strip's class.** It carries the same hedge inside every
  popup's alert block, and the unscoped rule repainted all of them: **2.59** in the dark
  theme. **The pins could not see this**, and that is worth stating as their limit: byte-
  identical popup HTML does not mean an unchanged rendering, because a stylesheet reaches a
  popup without touching its markup.
- **The OFF treatment was faded along with everything else.** A blanket `opacity: 0.55`
  took the OFF mark to **2.50** and the feed's name to **2.25**: the remedy for "state must
  not be conveyed by opacity alone" was itself made illegible by opacity.
- **The view preset stack was an overlay the popup correction could not see.**
  `POPUP_OBSTACLE_IDS`, whose own comment says the list exists so a third overlay is a
  deliberate edit rather than a silent regression, did not have it.

The rest: focus stranded on `<body>` by the fold, a theme toggle announcing "Light,
pressed" while the page is dark, feed ticks at **1.43** in the dark theme, Leaflet's
disabled zoom state overridden, the Key panel able to report itself expanded while showing
no rows, the alerts row able to overflow the 72px reserve and cover the OSM attribution,
`#legend` left out of the focus-ring list, the route banner's hashed hue as ink on a
surface that can be dark, and a preset still animating when reduced motion is turned on
mid-session.

### One finding is bigger than its fix, and it is the one to read

**In the dark theme every Key panel glyph falls under the 3:1 mark floor**, from 1.11 to
2.63. They were drawn for an opaque white panel and they carry the map's own marker
colours. The key is fixed (each glyph keeps its colour and gains the paper it was drawn
on, 3.98 to 11.31 in both themes), because a key whose glyphs did not match the map would
be worse than a dim one.

**The same arithmetic holds on the map.** The dark basemap filter is MR1's and the markers
are not: until MR2 through MR4 give every mark the paper casing and stroke the design
specifies, a rider who picks the dark theme gets a map whose markers read at those same
ratios. That is the cost of shipping the theme one stage before the marks. It is stated
here rather than discovered, and MR2 is where it starts being paid down.

## Mutations, each run and recorded

Each on a fresh copy of the tree (`git ls-files --cached --others --exclude-standard`,
so a new untracked file is included), the mutation applied alone, and the named tier run
against it.

| # | Guard reverted | Result | Killed by |
| --- | --- | --- | --- |
| M1 | `aria-pressed` not updated when a feed is toggled (the layer still hides, the OFF mark still appears) | **killed** | `chrome.spec.js` D1c: `pressed` reads `"true"` on a hidden feed |
| M2 | hidden feed conveyed by opacity alone (the strike and the visible OFF mark removed) | **killed** | `chrome.spec.js` D1c: `offMarkShown: false`, `strike: "none"` |
| M3 | the service alerts strip given the fold class | **killed** | `mobile.spec.js` A6c: "the service alerts strip must never be given the fold class" |
| M4 | the trailing note re-derived from the freshness index instead of taking `staleness()`'s output | **killed** | `chrome.spec.js` D1d and pins P1a, P1b: the withheld count and the undated clause both vanish, because neither is an age |
| M5 | the theme not persisted to `localStorage` | **killed** | `chrome.spec.js` D1g: the stored value reads `null` |
| M6 | the clock blink not gated by the motion preference | **killed** | `motion.spec.js` A5b: `animationName` reads `hdr-blink` under `prefers-reduced-motion: reduce` |

**The node tier killed none of them, and that is the record rather than a gap.** Every
one of the six reverts a WIRING decision: which attribute the paint function writes,
which rule the stylesheet carries, which function `map.js` calls, whether a click
reaches `localStorage`. The pure half is tested at the node tier and tested well (the
eight feeds and their order, the dot's three states, the tooltip's words including the
two the design's examples could not show, the one boolean behind `aria-pressed` and the
OFF mark, the note as the line's tail, the theme's two decisions), and none of that can
see a caller that stopped calling. The browser is where a wiring mutation dies.

## Deviations, stated rather than buried

- **C6e2 did not stay unchanged**, and the stage brief asked that it should. Its
  liveness guard read `toContainText(/trains/i)` on a HEALTHY page, which held only
  because the status line carried the subway's COUNT, and that is exactly what this
  stage takes apart. **One line changed**: the guard now reads the subway feed's count
  off the strip, which says what the count said where the count now is. C6e2's actual
  claim, that a poisoned BDFM group is named in the status line, is untouched, and
  C6e1, C6e3 and C6e4 are byte-unchanged. A first attempt asserting the note was empty
  was wrong on a real backend, where the static archives load behind the realtime feeds
  and the note legitimately carries four "still loading" sentences for the first polls.
- **The em-dash rule holds on every line I wrote and is broken by 36 lines I did not.**
  `fe82a66` records the handoff verbatim, and its `README.md` and `IMPLEMENTATION.md`
  carry 36 em-dashes between them. Changing a received design document to satisfy a
  house style corrupts the one thing that file is for. Every other commit is at zero on
  its added lines and in its message.
- **The status note folds with the feed strip below 700px**, where today's status line
  does not (it is a sibling of `#legend`, not a child). v3.1 carved the ALERTS strip out
  of the fold and did not carve this out, so MR1 implements it as specified. The
  accessible path is unchanged either way, because `#page-announce` speaks every status
  transition and is never folded; what changes is that a sighted rider on a phone reads
  the note after one tap instead of at a glance. If it should join the alerts row's
  carve-out, MR2 is the place.
- **The dark theme stops at the Stations panel's edge.** The handoff's scope note and
  the stage brief both put that panel out of scope, and its colours ARE its measured
  contrast relationships: A1i, A1j, A1k, A1l and A1m2 depend on them. Worth a stage of
  its own rather than a corner of this one.
- **`statusLineText` is no longer the page's renderer.** It is kept, exported and
  tested, and the MR1 node test asserts the composed line's tail IS `statusNoteText`'s
  output, so the retained composition is the oracle the note is checked against rather
  than dead code.

## False positives and blind spots I know about

- **The Key panel's rows are today's glyphs, not the design's.** The design's Key lists
  the redesign's own marks, and MR1 draws none of them yet: a key showing a route-bullet
  train and a branch tag over a map drawing rounded squares would be worse than no key.
  Each stage that changes a marker brings its row with it, and P1e is what says nothing
  was lost on the way. The design's two explanatory notes (the zoom thresholds and the
  agency-glyph table) arrive with MR2 and MR3 for the same reason.
- **A Key panel row clipped at its own scroll boundary is a new named undecidable.** It
  is answered by A1x rather than excused, and `ACCESSIBILITY.md`'s exception table grew
  the matching row, so the statement and the gate still agree about what the page
  excepts.
- **AirTrain shows no count**, because it has no vehicles: a 0 would be a claim about a
  fleet that does not exist, and a station count would be a different number wearing the
  badge. Its dot is the scheduled-only grey permanently.
- **The view presets stand down while the Key is open on a phone.** At 320 they painted
  over four rows of the key. A rider reading the key is not choosing a view, so the
  chrome that was asked for wins; above the breakpoint there is room for both.
- **Row 1 wraps to two lines at 375 and three at 320.** The brand, the clock and three
  actions do not fit 375px in one line, and the design's own rule is that the header's
  rows wrap and nothing has a fixed height that holds text. The fold takes the subway key
  and the feed strip, which is what "leaving one row" is about.

## Adjacent, noted and not fixed

- **`docs/design/map-redesign/` is the handoff, not the implementation.** The precedence
  is written down in the ledger rather than left to be rediscovered: the brief wins over
  `V3-NOTES.md`, which wins over `README.md`. The prototypes and the Leaflet copy that
  served them are deliberately absent; the reference stylesheet and logic are kept
  because they are the specification of the vocabulary at a precision the prose does not
  reach, and the handoff says in as many words to port from them rather than ship them.
- **The `--alert-banner-height` publisher and its resize listener are gone**, with the
  measurement that justified them kept in a comment at the place they used to be, because
  it is the reason they can go.
- **The subway station toggle is retired**, folded into the Subway feed per the design's
  rule that a feed toggle adds and removes that system's layer groups as one. That also
  retires the duplicate-name hazard it carried: it read "Stations" beside the "Stations"
  button that opens the panel and needed an `aria-label` to tell them apart.

## Gates, after each commit

Numbers are tests passed, with no failures anywhere. Ruff, `ruff format --check`, mypy
(30 source files) and the contract-tier lint were clean at every commit; **the backend
is untouched by this branch**, so its 1718 is the same suite on the same code. The
em-dash count is over each commit's added lines and its message.

| Commit | backend | node | e2e (hermetic) | browser contract | API contract | em-dashes |
| --- | --- | --- | --- | --- | --- | --- |
| `fe82a66` design record | 1718 | 281 | 204 | 4 | 38 | 36 * |
| `03d82b3` pins | 1718 | 281 | 218 | 4 | 38 | 0 |
| `88f0862` chrome | 1718 | 287 | 234 | 4 | 38 | 0 |
| `4b5d583` round 1 | 1718 | 287 | 234 | 4 | 38 | 0 |

- **`fe82a66`'s 36 are the handoff's own**, in the two documents recorded verbatim. See
  the deviations section.
- **The heavy tiers ran at the branch point, at `03d82b3` and at the tip**, not after
  each of the four commits, and this table says so rather than implying sixteen runs.
  `fe82a66` and `4b5d583` are documents and screenshots only and change no file any tier
  loads, so their rows carry the numbers of the commit before and after them
  respectively. `03d82b3` adds tests only; `88f0862` is the one commit with production
  changes, and every tier ran on it.
- **The e2e count grows twice**: 204 to 218 with the fourteen pins, and 218 to 234 with
  MR1's own eleven claims in `chrome.spec.js` plus the five A6k/A6l rewrites and A1x.
- **The node count grows once**: 281 to 287, six tests for the pure half.
- **The browser contract tier is the C6 dimming series**, C6e1 through C6e4, green at
  every point. Three are byte-unchanged; C6e2's one changed line is in the deviations.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_017oZP4Mtd6BLoSvAeVaPrJz
