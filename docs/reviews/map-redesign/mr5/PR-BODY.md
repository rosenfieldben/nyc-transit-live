# Phase MR, stage 5 of 5: the popups

Stage 5 of 5 of Phase MR, against the v3.1 handoff at
[`docs/design/map-redesign/`](https://github.com/rosenfieldben/nyc-transit-live/tree/claude/mr5-popups/docs/design/map-redesign)
as amended by the ledger's rulings. **The chrome** (an opaque surface, an ink edge, the design's
content metrics, and an auto-pan that clears the page chrome without throwing the rider's view
away), **the vocabulary** (`.pk`, `.pt`, `.kv`, `.dir`, `.arr`, `.fresh`, `.alert`, `.xlink`, with
every popup in the app rebuilt in it), and **the words**, which are the app's own everywhere: the
Position row is the freshness contract's, the footer is the feed strip's, and nothing in section 5's
prose is typed into a popup.

**Frontend only**: no backend file changes, so nothing here deploys a service. **No NJ Transit mint
was spent**: nothing on this branch touches the backend, `njt_auth.py` or any credentialed path, and
the contract tier aims every NJ Transit seam at its simulator as it always has. No em-dashes on
added lines.

| Commit | |
| --- | --- |
| `3c97d2f` | a mutation whose anchor misses did not run, as standing rule 6 |
| `9664ec6` | the four rulings the stage opened with, and the README's erratum |
| `11910de` | the pins invert, before anything is restyled |
| `8d7f1b7` | N6 paid, and one resolver reaches the NJ Transit popup head |
| `5ab314c` | the ferry hull's ink edge, and an exemption that ended by measurement |
| `5ec5798` | F17 paid, and the Key answers the head's two questions |
| `a2d6f32` | the popup's chrome, and the surface the words are printed on |
| `fd0919b` | the mutation table, and the survivor that found three things |
| `c7cbecb` | rulings Q1 and Q2, and the form Q1 leaves with no reader |
| `40967b5` | the footer wired, the blur dropped, and two more helpers with no caller |
| `3691000` | the flakes in one list, and the fifth shape this phase's defects take |
| `409f714` | the popup's vocabulary, and all twelve popups rebuilt in it |
| `dcc07f9` | `.xlink` named, `.alert`'s granularity kept, and M47 determined |
| `c1cfbc3` | the mutation table re-anchored at the tip, and one more row where a fixture renders it |
| `dd99432` | the captures, one popup per system in both themes |
| `790b9a2` | the mutation table's results at the tip, recorded |
| `4115767` | the kicker's marks measured at twelve routes, not at three |

## What a popup said before, and what it says now

| | before | after |
| --- | --- | --- |
| subway train | `<b>1 train</b>`, then `<br>`-joined lines: next stop, direction, the position's words, a muted `Trip sub-1` | kicker `SUBWAY`, title the map's own plate before `1 train`, then the design's grid: Next stop, Direction, Position, Trip, and the freshness footer |
| bus | `<b>M15</b>`, `Bus MTA NYCT_101`, `Heading: 90°` | kicker `BUSES`, title the arrow (or the dot) at the bearing it is drawn at before `M15`, rows Bus, Heading, Position |
| LIRR / Metro-North train | `<b>LIRR · Babylon Branch</b>`, train number, next stop, direction, and `position.compact` printed UNCONDITIONALLY (its own line, the app's one surface that said "live GPS" of a current fix) | kicker `LIRR`, title the tag this train is wearing before `Babylon Branch`, rows Train, Next stop, Direction, Position through the contract's `.words`, the cross-link, the footer |
| NJ Transit train | `<b>Northeast Corridor</b> <span>NJ Transit</span>`, train, to, next stop, delay, position | kicker `NJ TRANSIT`, title the tag before the route's name, rows Train, To, Next stop, Delay, Position |
| PATH train | `<b>Newark - World Trade Center</b> <span>PATH</span>`, next stop, direction, position | kicker `PATH`, title the diamond before the route's name, rows Next stop, Direction, Position |
| ferry boat | `<b>East River</b> <span>NYC Ferry</span>`, `Boat H201`, status, speed, position | kicker `NYC FERRY`, title the hull before the route's name, rows Boat, Status, Speed, Position |
| subway station | `<b>Times Sq-42 St</b>`, a heading per direction, rows joined by `<br>` | kicker `SUBWAY` with the plates of every route calling there, title the station's name, a `.dir` heading per direction and an `.arr` grid of mark, destination, countdown |
| LIRR / MNR / NJT station | `<b>Jamaica</b> <span>LIRR</span>` and the same `<br>` board | kicker the served system, title the paper square before the station's name, the same grid |
| PATH station, ferry dock | a bold name, a muted system tag, the dock's wheelchair glyph beside it | kicker the system word with the glyph in its right-hand slot, title the name, the grid. No title mark: these are canvas circles with no icon to borrow |
| AirTrain station | `<b>Terminal Alpha</b>`, one muted sentence, a line per branch | kicker `AIRTRAIN JFK`, title the square before the name, the sentence under it, and each branch as a label with its headway as a value |

## The three things a reader should check first

**The mark in a popup is the mark on the map, not a second drawing of it.** `markerMarkHtml` reads
the icon the marker is wearing and `popupMarkHtml` re-wraps that string at the popup's size, copying
every byte after the opening tag. A node test asserts that body byte-for-byte across all six mark
builders. The one clamp is arithmetic: a title mark is the larger of section 5's 24 and the mark's
own box, so only the rail tag's 30-unit box is not enlarged, because scaling it down would draw its
blocks smaller than the map draws them.

**The words are the app's.** Six kicker words, each asserted against the surface it came from (four
are the feed strip's own names, `NYC Ferry` is what `ferryBoatName` says, `AirTrain JFK` is what
`airtrainStationName` says). The Position row is `positionQualifier().words` through one helper, with
the silence rule ruling Q1 kept. The footer's words are `feedStateWords`, the same helper the feed
strip's dot uses, and the README's "LIVE · UPDATED 12S AGO" is typed nowhere.

**Every word a rider reads belongs to a named slot.** `pins.spec.js` P5d reads the popup twice, once
as a tree walk (the universe) and once through the vocabulary's selectors (a partition), and asserts
there is no residue. It is proved three ways, including one unclaimed string injected into a rendered
popup.

## The rulings this stage was given, and what each one cost

| | ruling | what shipped |
| --- | --- | --- |
| **Q1** | unify the Position row on `.words`, keep the silence rule, record the two railroad strings | one helper for every popup. A `placed` railroad train gains the contract's word ("scheduled position (no GPS)"), a FRESH GPS fix stops saying "live GPS" because silence means current, and the retired markup pin keeps the before |
| **Q2** | the footer's square is the feed strip's dot at the popup: present in all three states, no text when live, the app's own strings when stale or schedule-only | `feedStateWords` came out of `feedTooltip` so one answer serves both surfaces. The footer is `vehicleStaleLine` restyled with its suppression rule, not a second voice, and that helper is gone |
| **S3** | clamp each README padding to what the measured map and popup can satisfy, keep `panPopupClearOfChrome` for real boxes, stand down while the rider owns the view | the recipe is measured broken on this app (at 375 with the Key open it asks for a 592px top padding on a 667px map) and ships clamped, with the erratum beside the README's recipe |
| **F17** | one Key row for filled against outlined, one for chevron against dot | two rows framed by axis rather than by shape; A1x, D2l and P1e move by two |
| **the surface** | `var(--surface)` at full strength, fix the head's ink and `.popup-sub`'s `#666`, record the deviation | measured, the design's 94% made every popup's text undecidable to axe at three widths in both themes AND hid a real dark-theme violation. Opaque, axe names it, which is how this stage came to fix it |
| **the blur** | a rule measured to paint nothing is not kept with a test saying so | `backdrop-filter` dropped with the alpha, and `tokens.test.js` asserts its absence rather than explaining its presence |

## The measurements

| what | before | after |
| --- | --- | --- |
| the popup surface, axe | `incomplete` on every popup's text at 1280, 375 and 320 in both themes, with a real dark-theme `color-contrast` violation reported only as undecidable | decidable everywhere; the violation named and fixed |
| `readableInk` | one-directional: 18 of 27 subway route colours returned `#000000` at 1.49 on the dark surface | two-directional, with the darkening path character-for-character the old loop |
| a popup's `textContent` | `MNRMHUDHudsonTrain1797` | `MNR M HUD Hudson Train 1797`, because CSS ignores a whitespace-only node in a grid or flex container and `textContent` does not |
| the auto-pan recipe at 375 with the Key open | `top 592` on a 667px map: the popup lands 51px off the bottom | clamped per axis to what the measured map and popup can satisfy |
| A4j's second growth | a 40px growth needs an 88px clearing move that lands 2px inside the control stack, so no move exists | re-staged at 4px, where the move exists at 52, with the geometry written down |
| the alpha branch (M47) | reverting it moved no number, because `bestPerFamily` reports a family's strongest paint | P4d pins every non-opaque paint, composited and not, on both surfaces, from map, popup and chrome, at three decimals |
| the kicker at twelve routes | not measured | a 158px span that wraps to two rows inside a 220px popup, `scrollWidth === clientWidth` |

## The mutation table, re-run whole at the tip

**Seventeen died, one survived, none failed to run.** Two rows had gone stale since they were
written (M64 named the `backdrop-filter` the footer commit dropped, M66 named an argument list the
vocabulary commit added to) and standing rule 6 is what turned that into a re-anchoring rather than
into two survivors: an ANCHOR MISS is a failure of the run.

| # | mutation | result |
| --- | --- | --- |
| M60 to M69 | the chrome's ten | all died |
| M70 | the popup's mark rebuilt instead of copied | died, node |
| M71 | the title mark's clamp removed | died, node |
| M72 | the grid prints a row with nothing to say | died, node and the pins |
| M73 | the cell separator dropped | died, node |
| M74 | a kicker word coined rather than taken from the app | died, node |
| M75 | the bus's route note loses its class | **survived**, and the reason is recorded: no pinned world renders that note, so it is outside both directions of the coverage test |
| M76 | M47 again, the element's alpha not composited | died, P4d |
| M77 | M75's defect where the stock world does render it | died, P5d |

## The captures

One frame per surface, clipped to the popup at 1:1, in both themes, plus two full frames at 375
where the popup has to clear the chrome. `MEASURING.md` beside them says how to regenerate either
side of the pair.

| surface | light | dark |
| --- | --- | --- |
| subway train | `after-popup-subway-train.png` | `after-popup-subway-train-dark.png` |
| subway station | `after-popup-subway-station.png` | `after-popup-subway-station-dark.png` |
| bus | `after-popup-bus.png` | `after-popup-bus-dark.png` |
| LIRR train | `after-popup-lirr-train.png` | `after-popup-lirr-train-dark.png` |
| LIRR station | `after-popup-lirr-station.png` | `after-popup-lirr-station-dark.png` |
| Metro-North train | `after-popup-mnr-train.png` | `after-popup-mnr-train-dark.png` |
| Metro-North station | `after-popup-mnr-station.png` | `after-popup-mnr-station-dark.png` |
| NJ Transit train | `after-popup-njt-train.png` | `after-popup-njt-train-dark.png` |
| NJ Transit station | `after-popup-njt-station.png` | `after-popup-njt-station-dark.png` |
| PATH train | `after-popup-path-train.png` | `after-popup-path-train-dark.png` |
| PATH station | `after-popup-path-station.png` | `after-popup-path-station-dark.png` |
| ferry boat | `after-popup-ferry-boat.png` | `after-popup-ferry-boat-dark.png` |
| ferry dock | `after-popup-ferry-dock.png` | `after-popup-ferry-dock-dark.png` |
| AirTrain station | `after-popup-airtrain-station.png` | `after-popup-airtrain-station-dark.png` |
| the popup against the chrome at 375 | `after-chrome-375.png` | `after-chrome-375-dark.png` |

## What this stage changed about the tests

| tier | what is new |
| --- | --- |
| node, `frontend/popupvocab.test.js` | the vocabulary asked one builder at a time: the mark's body byte-for-byte across six builders, the size clamp as arithmetic, the escaping happening once, the silence rule, the three-cell row, and the six kicker words asserted against the surfaces they came from |
| node, `frontend/tokens.test.js` | the popup's surface is the token at full strength, the translucency cannot come back by either spelling, the blur went with it, the ink edge is on the wrapper alone |
| node, `frontend/boards.test.js` | the six board pins rewritten in section 5's grammar, with one spelled out in full so the grammar itself is pinned with no shared template in the way |
| browser, `tests/e2e/popups.spec.js` | D6a to D6i: the surface and the edge per theme, the width cap and the 220 floor at every bind site, fourteen surfaces at two widths in two themes, the theme swap re-inking the head, every inline colour a popup prints measured against its own fill or the surface, and the auto-pan's cap, edge, stand-down and desktop recipe |
| browser, `tests/e2e/pins.spec.js` | P5d (direction A, the residue assertion) and P4d (every non-opaque paint, composited and not), plus P5b's extractor rewritten as a mode-aware scanner |

## Gates

`ruff check`, `ruff format --check`, `mypy` and `pytest` in `backend/` (1738 passed); the
contract-tier lint; `node --test "frontend/*.test.js" "tests/*.test.js"` (392 passed); the hermetic
Playwright suite (316 passed); and `docs/reviews/audit-2026-09-05/run_all.sh` (fifteen records, all
still matching, two of which learned this stage's markup).
