# A4 adversarial review: the four-round adjudication record

Phase A4 (the page-wide accessibility gate and the statement) went through four
adversarial rounds. This is every finding raised, with its disposition. Nothing is
dropped; a finding that was wrong is recorded as wrong, with the evidence.

Five categories:

- **Fixed** — real, a change shipped, and a mutation proves the change is load-bearing.
- **Deferred, with reason** — real, not fixed in this phase, and why.
- **Refuted, with evidence** — reported and false; the measurement that refutes it.
- **Refuted then overturned** — refuted at the time and later found real after all.
- **Confirmed but downgraded** — the reported behaviour reproduces, but the severity does
  not survive contact with the rest of the suite.

Rounds 2, 3 and 4 attacked the *previous round's fixes* rather than re-searching the
original surface. Over half of round 3's findings were in the test and documentation code
written to pin round 2.

**The standing method, from round 3 onward:** re-run the motivating mutation and confirm
it dies, then invent a DIFFERENT mutation of the same defect class and see whether that
one survives. A surviving mutation is the finding.

---

## Round 1 — three reviewers over the six A4 deliverables

**13 findings raised, 13 fixed, 0 refuted.** One item (1.11) is fixed as documentation
and deferred as implementation, with reason.

| # | Finding | Disposition |
| --- | --- | --- |
| 1.1 | Closing a popup the rider was standing in dropped them on `document.body` in silence. Measured at 1280 with focus on the close button: Escape → `{"active":"BODY","announced":""}`, the close button → the same, next Tab → `#stations-skip`. Found independently by two reviewers. | **Fixed** — `ad0ca76`. One helper, two call sites, the decision taken before the popup is destroyed. Quiet, not announced: the rider asked for it. Mutations killed: the focus move removed fails A9i and A9j; the capture handler removed fails A9j. |
| 1.2 | The popup pan read the popup at `popupopen`, before its async content existed. Measured at 375: h=29 at read time; settled at top 169.65 bottom 321 against `#panel` bottom 284.5, still overlapping. | **Fixed** — `6e221c1`. A `ResizeObserver` re-runs the correction when the content changes size. |
| 1.3 | The pan only ever moved DOWN. At 1280 the legend spans y 10..710 of a 720px map, so nothing fits below it and the guard bailed every time: six of nine sample points across a real station popup returned `legend-row`. | **Fixed** — `6e221c1`. All four directions costed; the cheapest that actually clears wins. |
| 1.4 | The pan knew about one obstacle, so a downward move could push a tall popup under the alert banner (z-index 1001, paints over the popup pane). Measured with a 300px popup at 375: pan down 368.5, popup bottom 620, banner top 595.4. | **Fixed** — `6e221c1`. The banner is an obstacle like any other, and every candidate is re-checked against ALL obstacles. |
| 1.5 | A1y had no completeness half: every invariant is a property of whatever the walk happens to find. Measured: `tabindex="-1"` on all seven layer toggles removed every layer control from keyboard reach and all 153 e2e tests stayed green. | **Fixed** — `d8505c8`, refined in rounds 2–4. |
| 1.6 | A1z's pairing check resolved nothing: it asserted the decider sentence MATCHED a citation-shaped pattern. A decider rewritten to `nosuchfile.spec.js A0z composites...` passed. | **Fixed** — `d8505c8`. Resolution moved to `tests/specids.js`, shared by the gate and the statement checker, because both had their own regex and both were wrong the same way. |
| 1.7 | The keydown allowlist was keyed by FILE, so a file already excused for a control's activation handler could add unlimited routers. The round bound a second bubble-phase Escape router on `window` inside `systems/shared.js` and 154 node tests plus 8 escape specs stayed green. Separately, `document?.addEventListener` slipped the regex entirely. | **Fixed** — `d8505c8`. Keyed by file AND receiver; optional chaining normalised away before matching. |
| 1.8 | The citation parse dropped ranges silently: it understood only the literal word "through". Rewriting one range as `A9a to A9h` dropped six citations, 81 became 75, and no floor noticed. | **Fixed** — `d8505c8`. The joiner set is closed and broader; a range written with an unknown joiner is a red build. |
| 1.9 | The statement's own re-verify command omitted `tests/*.test.js`, so following it verified the statement without checking a single citation. | **Fixed** — `d8505c8`. |
| 1.10 | The statement claimed more than its tests establish in seven places: focus rings, the 24px floor, route colour, six systems, live regions, "painted", "no trap". | **Fixed** — `19011fd`. Each trimmed to the narrower true sentence, with the gap stated rather than implied. |
| 1.11 | Two WCAG 2.2 criteria were undocumented: 2.2.2 Pause, Stop, Hide and 2.5.7 Dragging Movements. | **Documentation fixed; the criteria themselves deferred, with reason** — `19011fd`. 2.2.2 is documented as unmet with the precise reason reduced motion does not satisfy it (the preference changes how a position updates, never whether it does), and filed as [#88](https://github.com/rosenfieldben/nyc-transit-live/issues/88) rather than solved inside an accessibility-statement phase. 2.5.7 is documented as unmet, with the arrow-key path cited (measured: one ArrowRight moves the centre from −74.00597 to −73.97850) and its insufficiency stated: 2.5.7 is a POINTER criterion, and a keyboard path is not an alternative for someone who can point but cannot drag. |
| 1.12 | A gate state named "popup open with cross-link" opened a popup with no cross-link at all. The reviewer proved the cost by emptying the cross-link's accessible name — the exact defect A1l injects as its canary — and all fifteen tests passed. | **Fixed** — `db23e9b` era, corrected in round 1. Selection by the app's own `placed` flag, with a loud throw if the fixture stops carrying one. |
| 1.13 | Leaflet's autoPan is animated, so a popup's resting position is unknowable while it runs; under the fixed clock the gate needs, `PosAnimation` drives off `+new Date()` and never completes. Measured at 1280: real clock settles at x 1001..1276 over the legend; fixed clock x 1288..1563, off the map's right edge, forever. | **Fixed, as a ruled contract change** — `6e221c1` + `c831d72`. autoPan is instant for every rider, and the principle is enshrined where the change lives: **animate the journey, never the adjustment.** |

**Process finding, ruled on and now standing policy:** reviewers that mutate get isolated
git worktrees. Round 1's three reviewers shared one working tree and one dev server and
contaminated each other's measurements.

---

## Round 2 — four worktree-isolated lenses, each finding put to an independent refuter whose default was to refute

**17 findings raised, 11 survived, 6 refuted.**

| # | Finding | Disposition |
| --- | --- | --- |
| 2.1 | The round-1 `ResizeObserver` correction outlived its welcome: a background refresh re-ran it on a map the RIDER had moved. Measured at 375: the rider dragged to centre lat 40.65134, a refresh grew the popup, the map jumped to 40.72996. | **Fixed** — `09762c5`. Stand-down guard. The guard's first implementation was itself found defective in round 3 (3.1) and its lifetime in round 4 (4.17, 4.18, 4.19). |
| 2.2 | The pan geometry fought itself: costing each direction as "the most any blocker demands" turns two obstacles into one enormous move that leaves the viewport, and discarding a candidate that lands on a non-blocking obstacle throws away the answer. Together they could cancel the desktop leftward move and leave the popup fully under the legend. | **Fixed** — `09762c5`. Each blocker costed separately; landing collisions resolved on the other axis. Three node tests, three killing mutations — though round 4 (G8-3) found none of the three could tell the fix from "cost only the first blocker". |
| 2.3 | The close button read `map._popup` rather than the popup it was bound to. | **Fixed** — `09762c5`, and swept codebase-wide in round 3 (3.2). |
| 2.4 | The SVG-glyph undecidable named `layout.spec.js A4g` as its decider; A4g samples `.arr-badge`, `.station-chip`, `.leaflet-popup-content b` and `.arr-dir`, and touches no SVG text node. The legend's subway glyph recoloured to 1.14:1 left the whole suite green. | **Fixed** — `09762c5`. A1z now measures SVG glyphs. Which shape it measured them against was found wrong in round 3 (3.3), and which PROPERTY it read in round 4 (4.10). |
| 2.5 | A fourth undecidable kind nobody had named: single-character ARRIVAL BADGES, visible only when the panel-detail state's popup had rendered by scan time, so the gate reported them intermittently. | **Fixed** — `09762c5`. Its own shape, decider A4g, which really does sample `.arr-badge`. |
| 2.6 | The keydown scan could be respelt past: a bare `addEventListener("keydown", …)` (window, in a classic script) and a single-quoted `'keydown'`. | **Fixed** — `c1c8f84`. |
| 2.7 | The joiner guard exempted anything CONTAINING a joiner, so `A9a through-ish A9l` was exempt, the parse dropped the middle ids, and the build stayed green — the exact silent drop the guard exists to stop. Its character class also excluded newlines, so a range broken across a line wrap was invisible. | **Fixed** — `c1c8f84`. |
| 2.8 | A9k was not the test its title claimed: "closing a popup the rider was NOT in" describes a close that never happens, because the rider is in the panel and the ladder takes the panel rung. Its comment claimed a mutant that only A9l catches. | **Fixed** — `c1c8f84` retitled it to what it really pins; round 3 gave it a witness. |
| 2.9 | A1y's comment said the owned-control list was "derived from the page"; it is explicit, and a derived list is not available, because "every focusable thing" includes Leaflet's controls and the tile attribution. | **Fixed** — `c1c8f84`. |
| 2.10 | The statement's exception table and the gate disagreed the moment the shapes were split, and the pairing check caught it: "the statement lists 3 exceptions and the gate enforces 5". | **Fixed** — `c1c8f84`. |
| 2.11 | Two README claims outlived the honesty pass ("no focus trap anywhere on this page", "search every station the map knows"), and the undecidable count was stale there. | **Fixed** — `c1c8f84`. |
| 2.12–2.17 | Six findings put to the independent refuter and refuted. | **Refuted, with evidence** — each was refuted by re-running the reporter's own reproduction and getting the opposite result; the transcripts are in the round-2 review packet on PR 89. |

Also caught by the suite in the same run it was written: the click listener's parameter
shadowed the `popupopen` event, so `event.popup` was `undefined` and the close button
stopped closing anything. A9j failed. **My own defect, fixed in the same commit.**

---

## Round 3 — 16 surviving defects, over half in the test and documentation code written to pin round 2

**16 findings raised, 16 fixed, 0 refuted.** One (3.12) was found while fixing another,
by measuring the fix rather than reading it.

| # | Finding | Disposition |
| --- | --- | --- |
| 3.1 | The stand-down guard keyed on a CENTRE COMPARISON. Leaflet's `invalidateSize` re-centres by `round(oldSize/2) − round(newSize/2)`, so any viewport parity flip shifts the reported centre with no rider input. Measured: 375x667 → 375x600 drifts 0.500003px and buries the popup 146px under the legend with `corrective pans: 0`. | **Fixed** — `c322785`. The guard keys on rider-INTENT events. An epsilon would only have relocated the bug. |
| 3.2 | `map._popup` was still read in the Escape rung. It answers RECENCY, not identity, and Leaflet never clears it on close. | **Fixed and swept sideways** — `c322785` (app), `9b628f7` (suites). Every remaining read asks `openPopupsOnMap()`. One deliberate exception: A9m sets `map._popup` to the wrong popup on purpose, because that IS its trap. |
| 3.3 | A1z measured each SVG glyph against `querySelector("rect, circle, path, polygon")` — first in DOCUMENT order, which is PAINT order, so it read the bottom of the stack while a rider sees the top. | **Fixed** — `9b628f7`. Topmost shape under the glyph's centre. Mutation: a #000 plate under a #eee plate under white text reads 21.00:1 by document order and 1.16:1 by what is actually behind it. |
| 3.4 | The same measurement passed silently on anything it could not read: `fill="none"` parsed to `[]`, the ratio came out `NaN`, and `NaN < 4.5` is false. | **Fixed** — `9b628f7`. Every unmeasurable case returns a null ratio with a sentence, and null is a failure. |
| 3.5 | The three text scanners could still drop what they could not classify. The id collector required a leading LETTER, so 35 of `smoke.spec.js`'s 41 tests were never collected and a citation to one was INVISIBLE rather than dangling — the silent-drop disease in its third costume. | **Fixed** — `cbdcfd3`. Two rules, now standing: anything a scanner cannot CLASSIFY is a loud failure; and each scanner carries a spelling corpus, so a new spelling arrives with a failing example first. |
| 3.6 | Four specs claimed states they never reached, and every one was green: A9k (never closed a popup), A9m (never had two popups), A1y (required station rows in a state with none), the gate's cross-link state (no cross-link). | **Fixed** — `18333ad`. `tests/e2e/state.js`: witnesses with `expectState` asserting the state before the spec's own assertions, and the convention documented in the README where spec authors look (`2c93618`). |
| 3.7 | A1y's `.station-row` requirement was inert: the walk never opened the panel. | **Fixed** — `18333ad`. `inert` is subtracted from the requirement, because unreachable is what `inert` MEANS; and because two filters now sit between the owned list and the requirement, each call site states which controls the state offers. That guard immediately caught a wrong claim of my own (`#stations-close` is `display:none` above the breakpoint). |
| 3.8 | The close button's capture flag was pinned by nothing: flipping it to `false` left all thirteen escape specs green. | **Fixed** — `9b628f7`. Understanding *why* turned it into a reduced-motion contract: without capture, Leaflet's own handler closes the popup first, and the app's focus decision only still worked because `DivOverlay` defers the container's removal by 200ms when the map is fade-animated. `shared.js` builds the map with `fadeAnimation: motionAtLoad`, so a rider who asked for reduced motion has no fade and would be dropped at the top of the document. |
| 3.9 | No reduced-motion popup spec existed. | **Fixed** — `9b628f7`. A9n, the reduced-motion half of the pair A9j covers for everyone else. |
| 3.10 | A4j passed 5–7 runs in ten with its subject deleted: flaky and vacuous at once. | **Fixed, in two attempts** — `cefa294` + `56273c6`. Deterministic wait (a probe `ResizeObserver` created after the app's, delivered in creation order); the clock paused rather than fixed (a refresh was rebuilding the markers and resetting the flag); A4k as the control. The first rebuild STILL passed 20/20 against a build with the guard deleted, because the drag went right and pushed the popup outside the map container, so no clearing move existed — declining an impossibility reads exactly like declining a choice. |
| 3.11 | Statement corrections: five undecidable shapes described as three; `A9a through A9l` stale; the SVG-glyph decider's wording; the marker and legend glyph call sites described as covered by nothing; the owned-controls claim silent about what it excludes. | **Fixed** — `9b628f7`. |
| 3.12 | Found while fixing 3.10, by measuring the guard rather than reading it: the `leafletAutoPanning` flag was cleared one line too early — before the `panBy` that fires the `movestart` the guard reads — so EVERY autopan was filed as the rider taking over. Rider-visible: Leaflet nudges an overflowing popup back into view on its own, and from then on the app declines to move that popup out from under the legend. Measured at 375 with a clearing move available throughout: after the autopan, popup at y 5..131; after a refresh, y −35..131, still under the legend; with the fix, y 292..458, clear. | **Fixed** — `56273c6`. Cleared in a `finally`. A4l pins it. |

---

## Round 4 — narrow, one question: does each spec reach the state it names

### Provenance

> **Adjudication operator-performed after harness wedge, findings verified serially on a
> clean tree.**

Round 4 ran as eight worktree-isolated lenses. Seven returned. The eighth wedged: it tried
to apply a mutation through a shell heredoc, the permission layer refused, and the agent
stopped to wait for an approval that cannot arrive in a background run. Because the script
used a `parallel()` barrier, the adjudication stage could never fire. The wedged lens was
abandoned in place; its group (motion `A5e`/`A5f` plus the node tier) was performed by hand
to the same standard — motivating mutation, then a same-class sibling invented and tried —
and the adjudication that the workflow's final stage would have done was performed by the
operator instead, serially, on a clean tree, with every HIGH re-measured rather than read.

The role collapse is stated rather than hidden: the same party wrote the fixes, ran the
mutations and judged the findings. What replaces the missing independence is that every
confirmation and every refutation below carries its own reproduction, and every fix carries
a mutation that now fails.

### Counts

| | |
| --- | --- |
| Raised (21 lens findings + 3 from the hand-performed group 8) | **24** |
| Confirmed by reproduction | **22** |
| Refuted with evidence | **2** |
| Confirmed but downgraded on reproduction | **3** |

### Confirmed and fixed

| # | Finding | Reproduction | Fix |
| --- | --- | --- | --- |
| 4.G | The gate scanned a popup that had not finished opening. Leaflet fades a popup in from opacity 0 over 200ms; a translucent element is measured against whatever is behind it, so axe read the popup's text against a MAP TILE. Only the muted ink fails that way. Found by CI, not by a lens. | CI: `color-contrast (serious) [.popup-sub, .popup-crosslink]` at 1280 and four nodes at 375, on a commit this machine passed 16/16. Sampled at 375: opacity 0, 0, 0.083 over the first three frames. | `7fe7898`. The fix is not in that state: a 3s fade-in on the alert banner reddens the banner state identically, so the GATE refuses to scan a document that is still animating. Witness: "popup finished opening". |
| 4.17 **HIGH** | The stand-down was pinned as one-shot, not permanent: A4j only ever grew the popup once. | Guard rewritten to consume `riderOwnsTheView` on first use: **A4j 15/15 pass**. After the fix: **A4j 5/5 fail**. | `459ea40`. A4j grows the popup twice. |
| 4.18 **HIGH** | The keyboard rider's takeover was never measured. Leaflet's Keyboard handler pans through `panBy` and fires neither `dragstart` nor `zoomstart`, so `movestart` is the only producer it reaches. | `movestart` handler deleted: **all 162 e2e specs pass**. After the fix: **A4m + A4l 10/10 fail**. Rider cost measured: centre thrown from 40.71955 to 40.74036 with a clearing move available. | `459ea40`. A4m is that rider; the press is the assertion, because under a paused clock the keyboard pan starts and never completes. |
| 4.19 | `A4l` measured only one direction of the `leafletAutoPanning` flag's job. | Flag never cleared at all: **162/162 pass**. After the fix: **A4m + A4l 10/10 fail**. Rider cost measured: 40.76221 → 40.83680. | `459ea40`. A4l asserts both halves. |
| 4.15 **HIGH** | A6o checked one container, not the whole background. | Partial release, ORDER UNTOUCHED: **21/21 mobile specs pass**. After the fix: **A6o red**. | `418d450`. A6o asserts no element anywhere is left inert, on both closing paths. |
| 4.14 **HIGH** | A6p never isolated the ENTERING direction: every crossing it performed also crossed the 1100 dock boundary, where `applyStationsDocking` re-applies inertness anyway. | Listener handling only the way OUT of mobile: **21/21 pass**. The mirror image (entering only) **died**. After the fix: **A6p red**. | `418d450`. A 701 → 700 step, which crosses no other breakpoint. |
| 4.16a | A6q asked about `inert`, and `inert` is not the only door out of the accessibility tree. | `aria-hidden="true"` on every background sibling with the `inert` exemption untouched: **48/48 pass** across mobile, vanish, announce and a11y. After the fix: **A6q red**. | `418d450`. The claim is made in the tree's terms. |
| 4.9 **HIGH** | A1z never opened a popup, while the exception it answers excuses the popup close glyph by name. | Close glyph recoloured to `#f2f2f2` (1.09:1): **162/162 pass**. Degrading the OTHER glyph the same sentence names WAS caught. After the fix: **A1z red**. | `6930967`. A1z opens a popup and measures the close glyph against what is painted under its own centre. |
| 4.10 **HIGH** | A1z read the presentation attribute, and CSS beats it in the cascade. | `.legend-row svg text { fill: #2a6ac8 }` drives the legend glyph to 1.16:1: **162/162 pass**. The identical regression in the attribute was caught. After the fix: **A1z red**. | `6930967`. `getComputedStyle` alone. |
| 4.11 | The citation grammar dropped what it could not read — the silent-drop disease in its fourth costume and fourth file. | A decider naming `smoke.spec.js 42` and `mobile.spec.js A6zzz` alongside one real pair: **A1z passes, node tier passes**. After the fix: **A1z red**. | `6930967`. Every `.spec.js` is a citation ATTEMPT; the token after it is an id or a loud failure. |
| 4.12 | `#route-clear` was `.station-row` all over again: on the owned list, 0x0 in every walked state, required of nothing. | `tabindex="-1"`: **162/162 pass**. After the fix: **A1y red**. | `6930967`. A1y draws a bus route through the marker, so the control exists and is required by name. |
| 4.8 | The "map alone" state never checked the panel was closed. | `closeStationsPanel` neutered: **15/15 a11y specs pass**. After the fix: **A1w red**. | `6930967`. Witness: "panel closed". The two pages differ to axe — 516 nodes with the skip link reported incomplete, 548 without. |
| 4.20 **HIGH** | The banner path had no silence half, so the rescue could be keyed on the CAUSE instead of on the RIDER. | Both branches keyed on the strip's own state: **162 e2e + 167 node pass**. After the fix: **A8i red**. | `11e49d0`. A8i is a rider mid-word in the search box; asserts focus, silence, and the text still in the box. WCAG 3.2.2. |
| 4.21 | A8a's pattern `/^The .+ you were following/` matches "vehicle", so the message need not name anything. | Label dropped from the rescue call: **vanish and announce suites pass**. After the fix: **A8a red**. | `11e49d0`. A8a reads the marker's name before it goes and asserts the whole sentence. |
| G8-1 | A5e could not tell "instant" from "absent": both its assertions are satisfied by a pan that never ran, though its own comment says suppressing the pan "would change what the rider can see, which this gate must never do". | Autopan suppressed outright: **8/8 motion specs pass**. After the fix: **A5e red**. | `a7cd672`. A5e asserts the popup ended up inside the map. |
| G8-2 | The keydown scanner could not SEE a computed member call: `window["addEventListener"]("keydown", f)` has no `.addEventListener(` to match and hides the name in a string. Fifth costume. | A page-level Escape router written that way: **node tier passes**. After the fix: **`keyboard.test.js` red**. | `a7cd672`. The bracket is looked for, at a position outside any string. Three corpus entries. |
| G8-3 | The round-2 "each blocker costed separately" fix had no test that could tell it from "cost only the first blocker". | First-blocker-only variant: **161/161 node tests pass**. Non-equivalent, found by search: both blockers costed gives `dx 30`, first only gives `dx 30 dy 45`. After the fix: **`helpers.test.js` red**. | `a7cd672`. That geometry is now a test. |
| 4.5 | A9b's comment names three properties — no `preventDefault`, no `stopPropagation`, Leaflet's handler still reachable — and asserted none of them. | `if (!closed) return;` deleted: **every escape spec passes**. After the fix: **A9b red**. | `d7fb322`. A bubble-phase listener registered before the press; a capture-phase `stopPropagation` is observable as the listener not firing. |
| 4.7 | The banner's absence from the ladder was only half pinned: A9a catches a banner added as a FALLBACK rung, not as a rider's-own-surface rung. | Banner given a rider's-own rung: **every escape and vanish spec passes**. After the fix: **A9o red**. | `d7fb322`. A9o presses Escape from inside the banner. Written wrong first and corrected by measurement: Escape there DOES close the panel, because focus on the dismiss button is focus outside both transients. |
| 4.4 | The Escape key had no identity pin: A9m pins "the popup that owns the button", nothing pinned "the popup the rider is standing in". | `popupContaining(document.activeElement) ||` deleted: **all 14 escape specs pass**. After the fix: **A9p red**. | `d7fb322`. A9p, on A9m's two-popup staging. |

### Confirmed but downgraded on reproduction

| # | Finding | Reproduction | Disposition |
| --- | --- | --- | --- |
| 4.1 | A9d's third leg asserts an ordering in a geometry where the app has nothing to order. | Order reversal: **A9d passes**, but A9a, A9c, A9f and A9l all fail. The both-close sibling: three fail. | **Downgraded to test hygiene.** The contract is well pinned by four other specs. Fixed as a comment correction in `d7fb322`, because a leg that reads as protection and is not is what this round exists to find. |
| 4.2, 4.3 | A9a and A9c cannot tell the ladder from Leaflet's own container handler. | The declining-rung sibling: **A9a and A9c pass**, A9f, A9h and A9l all fail. | **Downgraded to test hygiene.** From the map container the two handlers are indistinguishable by construction; the specs that stand the rider elsewhere are what catch it. Both comments now say which spec does the work. |

### Refuted, with evidence

| # | Claim | Evidence that refutes it |
| --- | --- | --- |
| 4.13 (headline) | "Re-scoping the gate away from the two containers A1l injects into SURVIVES, so A1l measures axe-core rather than the gate." | Reproduced: the re-scope **reddens 7 of the 15 a11y specs**, because `assertScanned` requires named targets to appear in the pass list. The gate does defend its own scope. **The residual held and was fixed**: A1l did build its own `AxeBuilder`, so the two could drift; one `scanPage()` now serves both (`6930967`). |
| 4.16b | "Muting `announcePage`'s only writer survives, so the live region's speech is unmeasured." | Reproduced: **7 specs fail** — `A8e`, `A8h`, `A2h` among them. The writer is well covered. Only the `aria-hidden` sibling (4.16a) survived, and that is fixed separately. |

### Out of scope, recorded rather than fixed

- **`smoke.spec.js` C2c2** flakes roughly one run in ten under two-worker contention and
  passes 6/6 in isolation. It is a C2-phase spec, untouched by this branch's diff, and a
  freeze is the wrong moment to start editing it. Filed as a follow-up on PR 89.
- **One mutation reported as a finding and correctly withheld by its lens:** dropping
  `alert-banner` from `POPUP_OBSTACLE_IDS` survives all three popup-correction specs, but
  it is an equivalent mutant in this fixture — the banner's rect at 375 measures
  `{l:8, t:645, r:367, b:645}`, zero height, so `popupObstacles()` already filters it out
  and the correction sees one obstacle either way. Recorded because a lens declining to
  report an equivalent mutant is the behaviour the method wants.

---

## What the four rounds cost, and what they bought

Every round attacked the previous round's fixes, and every round found that **the fixes had
their own defects**. The app converged quickly — three rider-visible defects in round 1, two
in round 2, one in round 3, none in round 4. The SPECS did not: round 4's 22 confirmed
findings are almost entirely specs that could not tell a correct build from a broken one.

Three conventions came out of it, and they are the durable part:

1. **`tests/e2e/state.js`** — a spec that claims a state asserts its witness first. Four
   specs across three rounds claimed a state they never reached and passed anyway.
2. **The two-part mutation standard** — kill the motivating mutation, then invent a
   different one of the same class. Nine of round 4's findings are same-class siblings that
   survived a fix whose motivating mutation died.
3. **Loud, never absent** — anything a scanner cannot classify is a failure. Five costumes
   across four files before that rule was written down, and one more (G8-2) after.
