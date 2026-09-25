# Follow-up 1: bus markers are drawn from City zoom (13) and not below it

The phase close-out's first follow-up, on its own branch off `d49e9a7`. **Frontend only**: no
backend file changes. **No NJ Transit mint was spent**: the one script here that imports the
backend (to decode the committed bus capture for the screenshots) sets fake credentials first and
calls no NJ Transit code.

## The finding

At the Rail preset (zoom 11) and at Region (zoom 10), the bus layer is hundreds of 14px arrows and
dots across Queens and Brooklyn. Each one is correct and none can be read, and together they are
the noisiest thing on the map. The design gave the arrow a size and a hue and said nothing about
zoom.

Measured with a real population rather than the fixture world's two buses: the committed
OneBusAway capture (`backend/tests/fixtures/bus_vehicle_positions.pb`) decodes to 2,136 buses, and
1,890 of them fall inside the Rail frame.

| Rail, before | Rail, after |
| --- | --- |
| ![before](https://github.com/rosenfieldben/nyc-transit-live/blob/claude/bus-zoom-rule/docs/reviews/map-redesign/followup-1/before-rail.png?raw=true) | ![after](https://github.com/rosenfieldben/nyc-transit-live/blob/claude/bus-zoom-rule/docs/reviews/map-redesign/followup-1/after-rail.png?raw=true) |

The strip reads **Buses 2,136** in both frames. City is unchanged (`before-city.png` and
`after-city.png` draw all 2,136). The view a rider lands on is below. `MEASURING.md` says how the
frames were taken.

## Concurrent branch

**`claude/subway-hub-definition` is in flight at the same time and also edits
`frontend/helpers.js`.** It changes `isTransferStation`'s signature, so that a hub becomes a
station complex. This branch adds the bus band beside the other zoom bands, a few hundred lines
away, and also touches `FEEDS` and `feedTooltip`. Whichever merges second should expect to
rebase through that file, and should re-run its gates after the rebase.

## The change

**1. One decision, one attribute, two readers.** `busMarkerBand` in `helpers.js` answers `drawn`
from zoom 13 up and `hidden` below it, and `hidden` for any zoom that is not a finite number.
`paintZoomBand` writes that answer on the root as `data-bus-band`, in the same call that writes
`data-zoom`. The stylesheet hides `.bus-marker` when the band says `hidden`. `buses.js` reads the
same attribute and writes `aria-hidden` and `pointer-events: none` on each bus element, the
treatment MR2 gave an off-focus train, and takes both off when the band says `drawn`.

**2. No re-render.** No marker is added, removed or rebuilt. The registry, the strip's count and
every pin stay where they were, and the zoomend that crosses 13 draws the buses without waiting for
a poll. The band is also repainted when a move ends at a new integer zoom, which is how a preset's
fly ends when a drag cuts it short (Leaflet fires no zoomend then). A bus the poll adds while the
map is below City zoom gets its reach from an `add` hook.

**3. Fail-open.** Both readers act only on the word `hidden`. A page whose script never wrote the
band draws every bus, as the map did before this change.

**4. The words.** The Buses button's tooltip reads `Live · 12s · shown from City zoom · hide Buses`,
and the Key's first bus row reads `Bus (arrow points where it's heading); shown from City zoom`.
Both come from, or are held to, one constant. The count in the strip and the empty map at Rail no
longer disagree in silence. `ACCESSIBILITY.md` and the README say so too.

## Where this departs from the brief

| The brief | What shipped | Why |
| --- | --- | --- |
| CSS "keyed by data-zoom" | CSS keyed by `data-bus-band`, computed from the same integer zoom in the same call | `style.css` already records why at the label gate: a stylesheet listing `data-zoom` values fails silently at any zoom it does not list. The brief's own "non-finite hidden" can only be written as a band. |
| "the Key's bus row" | the first of the two bus rows | It is the row that names the family. The heading-unknown dot below it is the same bus under the same rule. |

**The landing, as ruled.** As first built, the map opened at zoom 12, one below the band, so a
rider landed on a strip counting every bus and a map drawing none. The ruling moved the landing,
not the rule. The map now opens at the City preset (zoom 13, the design's own default) with its
button pressed, and the band stays at 13. The preset table moved to the top of `shared.js`, so the
landing is read from the same City row the button uses and cannot drift from it. D7i is the
ruling's one test.

| Landing view before (zoom 12) | Landing view after (the City preset, pressed) |
| --- | --- |
| ![before](https://github.com/rosenfieldben/nyc-transit-live/blob/claude/bus-zoom-rule/docs/reviews/map-redesign/followup-1/before-open.png?raw=true) | ![after](https://github.com/rosenfieldben/nyc-transit-live/blob/claude/bus-zoom-rule/docs/reviews/map-redesign/followup-1/after-open.png?raw=true) |

## Pins first

`8e30014`, before anything else. P1f to P1n already held every mark and every popup byte for byte,
but only at the opening zoom, and this rule reaches every zoom. So the pins read the opening view
and all three presets:

| Pin | Holds | Unchanged at the tip |
| --- | --- | --- |
| P6a | the bus count in the strip, the registry and the document, at each view | yes |
| P6b | every non-bus marker at each view: markup, name, drawn, pointer, exposed | yes |
| P6c | the bus marker's icon and both bus popups, byte for byte, at each view | yes |
| P1g | the bus marker and bus popup (the existing pin) | yes |

**P1e moves by exactly one sentence**, the Key row above. P6b was regenerated at the base once the
review sharpened its reader (computed opacity, 80 values from `""` to `"1"`). The landing ruling
moved what recorded the old landing, and the ledger keeps each before:

- P6a's `open` row: zoom 12 becomes 13.
- chrome D1j: no preset pressed at load becomes City pressed.
- D7a and D7b: their landing rows.
- A1w: its landing state.

## Tests

| Claim | Test |
| --- | --- |
| 12 is hidden, 13 is drawn, a non-finite zoom is hidden, and a string is not coerced | `frontend/buszoom.test.js` |
| the tooltip over its whole matrix, and no other feed carrying the note | `frontend/buszoom.test.js` |
| no bus drawn at Region or Rail, every bus drawn at City; strip, registry and document counts never move; every other vehicle still drawn | `buszoom.spec.js` D7a |
| `aria-hidden`, `pointer-events` and a real hit test follow the band in both directions; no bus image at Rail for a screen reader to list | D7b |
| the band survives a feed hidden and shown, a re-icon by poll (asserting Leaflet reuses the element) and a bus arriving at Rail | D7c |
| zooming in draws the buses on the zoomend, with no poll and the same elements | D7d |
| the tooltip at every preset and while the feed is hidden, and the Key row, both hold the one constant | D7e |
| the clicked route line, its banner, its popup, the popup's own mark and route focus are all left alone | D7f |
| a root with no band draws every bus and leaves every one reachable | D7g |
| a fly cut short leaves the band on the zoom the map actually rests at, in both directions | D7h |
| the map lands at the City preset: every bus drawn and reachable, and City alone pressed | D7i |
| axe green with the buses drawn at the landing view, undrawn at Rail, and drawn at City again after Rail, at 1280, 375 and 320, in both themes; each bus checked per axe rule (`aria-hidden-focus` where hidden, `role-img-alt` where drawn) | `a11y.spec.js` A1w, 18 new scans |

## The sentinels, re-read

MR3's carry-forward: a hidden marker is still a marker. Every count over `.leaflet-marker-icon` or a
bus class is unchanged, since the markers stay in the document. **Four readings moved to City**,
because each read a bus that is no longer drawn at the opening zoom:

- **smoke 7** clicks a bus, and below 13 a bus takes no pointer.
- **layout A4b** was passing vacuously: an undrawn bus's box is 0 by 0, so "must not have been
  visually inflated" held over nothing. It now also asserts that every family's box is drawn.
- **families D4f** read a resolved transform, which Chrome reports as `none` inside a
  `display: none` subtree.
- **families D4g** was reading a dimmed opacity on a mark nobody sees. The shared `marks()` reader
  now throws on an undrawn mark for every family.

**The review then found readers this re-read had missed:** the bus-paint readers in `contrast.js`
and `theme.spec.js`, and busroute A7c and A7f. Those moved too (see Review). The full table is in the
ledger, and it marks which rows were missed and by whom.

## Mutations, each run and recorded

Each row runs in a worktree detached at the commit, with the sha echoed and compared, the anchor
required to match exactly once, and `CI=1` so no server is reused. The runner and the table are
`docs/reviews/map-redesign/followup-1/mutations.sh`, re-runnable at any sha. **M0 and M0z are
controls**: each runs every gate on the unmutated tree, one before the rows and one after, and both
must survive. The table exits non-zero if a control dies or any other row survives.

| # | Guard reverted | Killed by |
| --- | --- | --- |
| M0 | none (the opening control) | **survived all eleven gates** |
| M1 | threshold moved to 12 (from the brief) | node, the 12-and-13 test |
| M2 | the stylesheet rule removed (from the brief) | D7a |
| M3 / M3b | no `aria-hidden` on an undrawn bus (from the brief) | D7b / A1w at Rail |
| M4 / M4b | the tooltip wording removed (from the brief) | node / D7e |
| M5 | `pointer-events` left on an undrawn bus | D7b |
| M6 | the `add` hook removed | D7c, at a bus the poll adds at Rail |
| M7 / M7b | `paintZoomBand` no longer sweeps the buses | D7b / A1w at City |
| M8 / M8b | fail-closed, in the stylesheet / in `buses.js` | D7g / D7g |
| M9 | the Key row loses the words | D7e |
| M10 | the band never written on the root | D7a |
| M11 | the rule widened to every marker | P6b |
| M12 | the rule reaches the popup's own mark | D7f |
| M13 | A4b measured where the bus is not drawn | A4b |
| M14 / M15 | D4f / D4g read where the bus is not drawn | the `marks()` guard |
| M16 | the rule as a re-render (markers taken off the layer) | P6a |
| M17 | smoke 7 clicks where the bus is not drawn | smoke 7 |
| M18 | a drawn bus left with no role and no name | A1w at City, `role-img-alt` |
| M19 | a fly cut short no longer repaints the band | D7h |
| M20 / M21 | the theme spec / P4c read where the bus is not drawn | D5d / P4c |
| M22 / M22b | the rule widened to every other marker as a fade | P6b / D7a |
| M23 | a route focus that hides every bus at City | D7f |
| M24 | the Key row's clause hidden inside `<span hidden>` | D7e |
| M25 | A7c clicks where the bus is not drawn | A7c |
| M26 | the map opens at the old zoom 12 | D7i |
| M27 | the map lands at City without City pressed | D7i |
| M0z | none (the closing control) | **survived all eleven gates** |

**All 34 rows at `6b447c2`. Both controls survived all eleven of their gates, the other 32 died, none failed to run, and every anchor matched exactly once.** One earlier run, at `2355232`, could not run M13, because its anchor stopped partway through a line. Under standing rule 6, a row that doesn't run fails the run, so after the fix the whole table ran again, not just that row.

**M6 corrected a claim of mine.** The hook's comment said the feed toggle depended on it. With the
hook removed, the toggle half of D7c still passed, because `applyFeedVisibility` repaints the band
after re-adding the layer. The half that died was a bus the poll adds at Rail, which no zoomend
follows. The comments now say that.

## Review

The `adversarial-review` workflow ran five finders at `68d31da`, each in a worktree detached at
that commit and checked against it. They were pointed at the phase's five defect shapes, told to
use `CI=1` on a reserved port, and told to spend no mint. **Fourteen findings survived triage.
Their verdicts: nine CONFIRMED, four PLAUSIBLE and one REFUTED.** Every one is repaired or
recorded, and rows M19 to M25 prove the repairs.

- **One a rider meets: a fly cut short.** A drag stops a preset's fly without a zoomend, and the
  band kept the old zoom, so every bus stayed drawn at 11.8. Fixed with a moveend repaint and D7h.
- **Readers that had not learned the rule.** `contrast.js` (so D5d, P4c and P4d) and
  `theme.spec.js`'s own bus reads were still reading the bus at zoom 12, where it is not drawn.
  A7c and A7f were still clicking a bus no mouse could reach.
- **Tests that could not fail:**
  - P6b and D7a were blind to a fade; they now read the computed opacity.
  - D7f asked about route focus only where every bus is hidden anyway; it now asks at City too.
  - D7e read markup; it now reads the open panel's rendered text.
- **The runner:**
  - It exited 0 with a dead control or a surviving row.
  - It killed whatever held the port.
  - It scored environment failures as kills.
- **The prose:** two comments that overstated or misnamed things.

The full table, with each verdict and the row that proves it, is in the ledger. **One thing it
turned up is not this branch's:** after a cut-short fly, the next preset press ends with its button
unpressed. It reproduces at `d49e9a7` too, and it is recorded below.

## Gates

The code is `2355232`, the landing ruling. `6b447c2` changes only one anchor in the mutation table.

| Gate | Result |
| --- | --- |
| `pytest` (backend, unchanged, re-run) | **1738** passed |
| `ruff check` / `ruff format --check` / `mypy` | clean / 79 files / 30 source files |
| contract-tier lint and format | clean |
| contract API tier | **38** passed, re-run at the tip |
| contract browser tier (C6e1 to C6e5) | **5 of 5** |
| node tier | **400 of 400**, and 400 again with no `node_modules` (the `frontend-tests` condition) |
| hermetic e2e | **347 of 348**. The one failure is smoke 25, a ferry test that timed out waiting on a retry. It passed 3 of 3 alone, and it passed in the previous full run on identical code, which scored 346 of 348 because two tests hit the known clock race; both passed alone. |
| `run_all.sh` | **15 of 15** |
| mutation table | **34 rows at `6b447c2`**: both controls survived, 32 died, none failed to run |

## For the operator

1. **The landing:** ruled and done (above).
2. **The `aria-hidden` and `pointer-events` half is redundant while the stylesheet applies**,
   because `display: none` already does both jobs. It is there because the brief asked for it. If
   the stylesheet ever failed to load, every bus would be drawn, and below 13 these two attributes
   would leave them drawn but silent and unclickable.
3. **A preset pressed after a fly is cut short ends unpressed.** This predates the branch
   (reproduced at `d49e9a7`) and could be a small branch of its own.
4. **The concurrent branch** `claude/subway-hub-definition` also edits `frontend/helpers.js`
   (above).
5. **An incident, repaired.** Another session switched this checkout's branch mid-task, and the
   pins commit landed on its branch. That session put it back itself, and nothing was lost. The
   ledger's standing rule 4 gains a clause from it: check the branch as well as the tree before a
   commit.
6. **Flakes, on the phase's list:**
   - The suite's frozen-clock boots throw "Cannot fast-forward to the past" under load, before any
     page loads. The fix is known, and D7's boot has it.
   - A1v3 fails under load at `d49e9a7` too.
   - A7g and D2w failed once each; both passed alone and on repeat.
7. **One new axe undecidable, named and decided.** At 320 with the Key open, the City landing puts
   a subway train behind the header, under the status note's box. axe reports the note's
   background as undecidable instead of stopping at the header's opaque surface. The note now has
   its own named shape, and A1x measures its ink against that surface at every width and in both
   themes.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_013CzGVmL9ktytLxrRUYjFr5
