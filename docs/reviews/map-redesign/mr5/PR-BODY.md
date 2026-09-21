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
| `df96e06` | the README erratum says four where it lists four, and the arrow reads straight |
| `c3766b2` | two records the rename touched, put back the way a record should read |
| `f7693cb` | six comments that described a surface this app does not draw |
| `445e927` | the contract's amendment note names the helper that exists |
| `29ad0e6` | the PR body, less the review round it is waiting on |
| `da27c3a` | round 1: the three defects the drawn page had and the string did not |
| `9edca7e` | round 2: the guards the reviewers proved could not fail, and the prose that had stopped being true |
| `8fb99b5` | round 2: this round's own seven mutation rows, so the repaired guards can be re-run |
| `21343a4` | round 2: the round in the ledger and in the PR body |
| `aab3e57` | round 2: the sha the table was last run at |
| `99f56cd` | round 2: P5d's flake diagnosed and fixed |
| `8bcb1fe` | round 2: the table's shas |
| `5b51f76` | R1 and R2: N6 paid on every surface a rider can see, and the footer speaks the state rather than the repeat |
| `1f065e4` | R3: all five station boards carry the routes calling there, through one helper with one measured cap |
| `6e6d8c0` | R1 to R3: the eight mutation rows the three rulings owe |
| `65b7af3` | R1 to R3: two anchors re-anchored and one mutation that could not fail |
| `47e80e2` to `c7c873e` | the three rulings written into the ledger and this body, and the sha the table was last run at |
| `665186b` | round 3: the mark normaliser moves to a file the node tier can reach, and the tier's package rule becomes a test |
| after `665186b` | round 3 written into the ledger and this body. Documentation only: `665186b` is the last commit on this branch to touch a file any mutation row anchors in, which is why the numbers in this body are the branch's rather than a snapshot |

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
| the kicker at twelve routes | first measured as a 158px span wrapping to two rows inside a 220px popup, which was WRONG | 204px on ONE row, pushing the popup to 267px, because Leaflet sizes a popup to its own nowrap content up to maxWidth 320. Ruling R3 re-measured it, since a cap cannot be sized from a wrap that does not happen |

## The mutation table, re-run whole at the tip

**Thirty-three rows at `65b7af3`: thirty-two died, one survived, none failed to run, and every anchor
matched exactly once**, and again at `47e80e2`, the last commit to touch a file any row anchors in. M85 to M92 are the three rulings' own. The run also found two stale anchors and
one mutation that could not fail, none of which is a result about the code, which is what standing rule
6 exists to make visible; the ledger records all three. Two rows had gone stale since they were written (M64 named the
`backdrop-filter` the footer commit dropped, M66 named an argument list the vocabulary commit added
to) and standing rule 6 is what turned that into a re-anchoring rather than into two survivors: an
ANCHOR MISS is a failure of the run. The table is `docs/reviews/map-redesign/mr5/mutations.sh`, so it
can be re-run rather than re-read.

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
| M78 | the mark token loses the mark's identity (a black plate, an oversized plate) | died, node and smoke C2i |
| M79 | the translucency and the blur come back in a LATER popup rule, where the cascade hides them | died, node |
| M80 | one system's popup prints words pinned only in another system's surfaces | died, P5b |
| M81 | the literal scanner reads a regex after a keyword as a division again | died, P5b |
| M82 | the rail popup's title loses the mark A1z4's scope closure now counts | died, A1z4 |
| M83 | a second surface adopts a mark class the census records in both places | died, P4a |
| M84 | the countdown that reads "now" takes `--accent`, at 3.47 on the light surface | died, node |
| M85 | the footer's `live` beats `said` again, so a screen reader hears two ages | died, node (both tiers) |
| M86 | the NJ Transit board resolver back to the second neutral | died, node |
| M87 | the railroad's paint resolver back to the published fill, unmoved | died, node |
| M88 | the panel's railroad chip resolves a colour of its own again | died, P3a |
| M89 | the route tag's viewBox origin shifted, which empties every kicker silently | died, node and P5a |
| M90 | the kicker's cap removed, so a dozen routes draw a dozen marks | died, node and P5e |
| M91 | the routes stop being spoken, so an aria-hidden mark is all a reader gets | died, node and the pins |
| M92 | the mark token takes the first `<text>` again, losing every branch code | died, node |

## The adversarial round, and what it cost

Five reviewers in worktrees detached at `790b9a2`, each echoing its sha, pointed at the four defect
shapes this phase keeps producing plus this stage's own and the fifth it named. **Round 1 fixed the
three defects the drawn page had**: the rail tag's type rendering at 17px instead of 8px in three
train popups (a presentation attribute travels with an SVG string and a marker-scoped CSS rule does
not), a row's interior space deleted by its own flex cell, and `.arr`'s row rule drawn as three
staggered stubs.

**Round 2 is the half about the guards**, and nothing a rider sees changed in it. Nine assertions
that could not fail on the thing they were written for, now able to (the seven rows M78 to M84 above,
plus A4g's premise satisfiable by an aria-hidden mark and P4d's premise satisfiable by a map row),
one reader that was two copies, and twelve comments or documents that described a tree that no longer
exists: `.alert-stale`'s unreachable grey with the two false premises defending it, `.fresh-dot`'s
base background (which WAS the silent default its own comment disclaimed), the tip's blur, the theme
hook's reach, `.station-alerts`'s broken pairing, the 94% present tense, a closure claimed over
callers' words, the freshness contract's section 3.2 anchors and two of its rows, the README's
deviation count and its Position row, this ledger's alpha table and its three popup widths, the stage
table's `planned`, and `IMPLEMENTATION.md`'s five names that do not exist. The ledger's round 2
section has the full table, including what the reviewers checked and found sound.

## The three rulings the adversarial round produced

Round 2 brought three findings to the operator rather than fixing them. All three came back as rulings,
and unlike round 2 all three change what a rider gets.

**R1, N6 paid on every surface a rider can see.** MR5 round 1 paid finding N6 ("two answers for one
judgment: what colour is this route") on the NJ Transit popup head and called it structurally closed;
three more readers went on answering elsewhere. The NJ Transit station board's badge and the panel's
chip resolved `njtRouteColor` (fallback `#4a4e69`) beside a map tag resolving `railBranchColor`
(`#6d6e71`). The railroad's board badge, popup title ink and panel chip resolved `railroadColor`, a HASH
of the route id that never saw the system, so the LIRR's Babylon Branch and Metro-North's Hudson Line
both drew `#5d4037` for two different published greens. All six readers now resolve the published paint
through the one lookup the tag uses, `railroadColor` is deleted, and the badge takes railBranchPaint's
PAIR because `EE0034` (four of Metro-North's six routes) clears with neither ink and its fill has to
move. Six values move, and the ledger records each before and after.

**R2, the footer speaks the state rather than the repeat.** The suppressed words went into the same
visually-hidden span the live state uses, so a screen reader heard the Position row's age and then the
footer's: two ages about one train, which the rule's own comment forbids. `said` beats `live` now, in one
line, pinned in two tiers with both channels denied rather than the hidden span alone.

**R3, all five station boards carry the routes calling there.** One helper, each family's own mark at the
popup's row size, a body-only rail tag whose class is deliberately not the map tag's, and one overflow
rule: three marks, a `+n` count, every route's name spoken. Three is measured, not chosen: an NJ Transit
tag is 66.69 units where a subway plate is 17, and at four of them the kicker wraps at phone widths.
Twelve plates do not wrap at all, they make the popup 267px wide, which corrected a measurement this
phase's own ledger had recorded the other way round.

**Four defects the three rulings turned up on their own**, each recorded in the ledger with its
measurement: P5b's call-graph crawler deduplicated by name and source LENGTH, so two of the app's seven
vehicle popups were never walked and nine literals were invisible to the coverage test; a `const` in a
vm context is not a property of that context, so a fixture-side fallback in `boards.test.js` was
`undefined` and drew `fill="undefined"`; `withoutMarks` could see neither a rail tag's branch code nor a
fill declared in a `style` attribute; and D6j compared the first `.pmark` in a popup against the
marker's own icon, which a kicker's marks precede.

## Round 3: the gate only CI could run

CI found one thing this checkout could not, and it is worth stating plainly because every gate below
was green while it was true. Round 2 de-duplicated the mark normaliser `withoutMarks` into
`tests/e2e/popup.js` and had `frontend/boards.test.js` require it, on the written claim that
**"`tests/e2e/popup.js` requires nothing"**. It requires `@playwright/test`, for `expect`. A local
`node --test` resolves that from `node_modules` and passes. The `frontend-tests` job installs nothing,
by design: the app is buildless and the unit tier needs no packages, so the job checks out the repo and
runs `node --test` against the runner's own node. The require threw at load, and node's reporting is
what made it quiet: **a file that dies at load counts as one failing test**, so the job said
`# tests 382 / # fail 1` where the truth was that thirteen tests had stopped existing. 394 here, 382
there, and a twelve-test gap was the only visible trace.

The reader moved to `tests/e2e/marktoken.js`, which requires nothing, is re-exported by `popup.js` so no
spec's import moves, and is required directly by the node tier. One copy still, which was round 2's
ruling; the premise under it is the part that got fixed.

And the hole got a gate, because a claim about what a tier may require is a claim a test should hold:
`tests/nodetier.test.js` asks node rather than a regex. It reads the job's own globs out of
`.github/workflows/ci.yml`, spawns a child node with `Module._load` hooked and `node:test` stubbed to a
no-op so nothing RUNS, requires each seed file for real, and fails on any specifier that is not a node
builtin. A source scan was the obvious version and would have gone blind: this closure deliberately
names `@playwright/test` in prose and carries regex literals full of double quotes, and a mis-lexed
quote does not fail loudly, it swallows the rest of the file and reports a clean tier. The hook is
tested on the exact shape that got past round 2, a seed whose only require is a local file with the
package one hop away, and a recorded package is answered with a stub rather than resolved so one run
names every offender and the check needs nothing installed itself.

Verified by putting the defect back: with `boards.test.js` pointed at `popup.js` again the new test
fails and names `tests/e2e/popup.js requires @playwright/test`. And with `node_modules` moved aside,
which is what the job has, the tier reports **397 pass, 0 fail** where before it could not load at all.
Two mutation rows moved with the file, M78 and M92, both anchors taken from the file programmatically;
the whole table re-run at `665186b` puts both of them in `tests/e2e/marktoken.js`, where both died.

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
| node, `frontend/popupvocab.test.js` | the vocabulary asked one builder at a time: the mark's body byte-for-byte across seven builders, the size clamp as arithmetic, the escaping happening once, the silence rule, the three-cell row, and the six kicker words asserted against the surfaces they came from |
| node, `frontend/tokens.test.js` | the popup's surface is the token at full strength, the translucency cannot come back by either spelling, the blur went with it, the ink edge is on the wrapper alone |
| node, `frontend/boards.test.js` | the six board pins rewritten in section 5's grammar, with one spelled out in full so the grammar itself is pinned with no shared template in the way |
| browser, `tests/e2e/popups.spec.js` | D6a to D6j: the surface and the edge per theme, the width cap and the 220 floor at every bind site, fourteen surfaces at two widths in two themes, the theme swap re-inking the head, every inline colour a popup prints measured against its own fill or the surface, the auto-pan's cap, edge, stand-down and desktop recipe, and (round 2) that a popup's title mark is its own marker's markup rather than any mark at all |
| browser, `tests/e2e/pins.spec.js` | P5d (direction A, the residue assertion) and P4d (every non-opaque paint, composited and not), plus P5b's extractor rewritten as a mode-aware scanner with its own self-tests and a per-system coverage haystack, and P4a's census widened to the six mark classes in both places they are drawn |
| browser, `tests/e2e/a11y.spec.js` | A1z4 opens a rail train popup, both tag bodies, so the scope closure its axe exception depends on covers the surface this stage added |
| node, `frontend/popupvocab.test.js` (R3) | the seventh mark builder in the roster, and the kicker's overflow rule asked one case at a time: the cap, the count, the spoken list, a route that cannot be drawn skipped BEFORE the cap, an empty list, and a mark whose viewBox the re-wrap cannot read |
| browser, `tests/e2e/pins.spec.js` (R3) | P5e, the only world that reaches the overflow rule: Times Sq's real dozen routes, at 1280 and 375, three drawn, nine counted, twelve spoken, one row, inside the cap. And P5a asserts a mark count per station board outside `pin()`, so an empty kicker cannot be written into a golden |
| node, `tests/nodetier.test.js` (round 3) | the node unit tier runs with no `node_modules`, so nothing it loads may require a package: the seeds are read from the workflow's own globs, the closure is walked by hooking a child node's module loader rather than by scraping source, and the hook is self-tested on a package one hop behind a local file |

## Gates

`ruff check`, `ruff format --check`, `mypy` and `pytest` in `backend/` (1738 passed); BOTH contract-tier
jobs, run locally as CI runs them (the lint and format check, 38 `pytest tests/contract`, and 5 contract
specs driving the real backend and the simulator); `node --test "frontend/*.test.js" "tests/*.test.js"`
(397 passed, and 397 again with `node_modules` moved aside, which is the condition the CI job actually
runs in); the hermetic Playwright suite (318 passed); and
`docs/reviews/audit-2026-09-05/run_all.sh` (fifteen records, all still matching, two of which learned
this stage's markup and one of which learned its new argument).

**The table, re-run whole at `665186b`: thirty-three rows, thirty-two died, M75 survived as recorded,
none failed to run, every anchor matched exactly once, and every row printed that sha.**

One flake appeared in this stage's own spec and was fixed rather than recorded: `pins.spec.js` P5d
failed twice in four full parallel runs with the injected-string proof reporting an empty residue,
because the app rebuilds an open popup on its fifteen-second poll while the mocked fetch that triggers
the rebuild resolves in real time, so it can land between the injection and the read. Reproduced
deterministically, fixed by doing both in one `page.evaluate`, and proved still sharp by forcing the
rebuild inside it. The ledger's flake list has the mechanism and the process lesson the first
occurrence cost.
