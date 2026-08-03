# Accessibility

## What this document is

This is a statement of what the page has been **measured** to do, what it
deliberately does not do, and what has never been checked at all. Every claim
below names the test that proves it, by file and test id, so any sentence here
can be re-run rather than believed.

**Posture.** This app **targets WCAG 2.2 Level AA** and documents its
exceptions. It does **not** claim conformance. A conformance claim asserts that
every success criterion is met on every page in every state, and nobody here has
grounds to assert that: the checks below are automated, they run in one browser
engine at two widths, and no screen reader and no disabled rider has ever been
put in front of this page. The list of exceptions in
[What is not covered](#what-is-not-covered) is part of the statement, not a
footnote to it.

## How the claims are checked

A page-wide axe-core scan runs in CI on every change
(`tests/e2e/a11y.spec.js`, test `A1w`). Its scope is the whole **document**, at
**1280x720** and **375x667**, across six states: the map alone, the station panel
listing results, the panel showing one station's arrivals, a vehicle popup open,
the alert banner active, and the full-screen mobile overlay. Zero violations is
required in all eleven instantiations.

Three things stop that gate from being decoration:

- **It is proven to have teeth.** `A1l` injects an unnamed button into the
  station panel and into the legend and requires axe to report `button-name` in
  both. The legend half exists because the legend used to be outside the scan's
  scope, and a gate that has stopped reaching a surface reports zero violations
  exactly like a clean page does.
- **It counts what it examined.** `assertScanned` requires a floor of rules and
  nodes and requires named targets to appear in the pass list, so a scan that
  silently stopped reaching the popup or the banner fails rather than passes.
- **Every fix is mutation-tested.** Reverting a fix must redden the gate. This
  is how the page-wide widening was validated: reverting the `<main>`/`<h1>`
  landmark fix fails every state, removing the popup pan fails the 375 popup
  state, neutering the inert sweep fails three mobile states.

## What the page does, and what proves it

### Keyboard

- A skip link is the **first** thing focus reaches, at both widths, and pressing
  it opens the station panel it skips to.
  `a11y.spec.js A1y`, `mobile.spec.js A6g` (375), `mobile.spec.js A6h` (desktop).
- **There is no keyboard trap anywhere.** A real Tab walk from the top of the
  document runs to the end and hands focus back to the browser, including with
  the full-screen mobile overlay open. `a11y.spec.js A1y`.
  The overlay uses the platform's `inert` rather than a focus trap precisely
  because `inert` does not wrap: `mobile.spec.js A6n` measures that Tab leaves
  the panel rather than cycling inside it.
- Every keyboard stop **announces itself**: it has an accessible name from an
  explicit source, or from its own content where the role takes a name from
  content. `a11y.spec.js A1y`.
- Every keyboard stop is **painted while focused**, so a sighted keyboard rider
  can see where they are. `a11y.spec.js A1y`.
- **Tab order follows the document.** No element on the page carries a positive
  `tabindex`. `a11y.spec.js A1y`.
- Every control this app owns draws a focus ring of at least 2px at 3:1 or
  better against its own surface, and a **mouse click does not** draw one.
  `mobile.spec.js A6f`.
- **Escape closes the topmost transient only**, and which one that is depends on
  where the rider is standing: the popup first from inside a popup, the panel
  first from inside the panel. One press never closes two surfaces. The whole
  ladder is one document-level listener, which `frontend/keyboard.test.js` pins
  at the source. `escape.spec.js A9a` through `A9h`.
- The banner is deliberately **not** Escape-dismissable, because it is not
  transient: it carries service alerts and has its own dismiss button.

### Focus, on a page that rewrites itself every fifteen seconds

- A popup refreshing under the rider **returns focus** to the control they were
  on. `crosslink.spec.js A3e`, `A3g`.
- A banner rebuilt because the MTA reworded an incident returns focus to the
  dismiss button, and does **not** grab focus from a rider who was elsewhere.
  `layout.spec.js A4d`, `A4e`.
- When the thing being held **genuinely disappears**, focus moves to the map
  container and the page says so once, politely: "The 1 train you were following
  left the feed. Focus moved to the map.", or "Alerts cleared. Focus moved to the
  map." `vanish.spec.js A8a` through `A8h`.
- Nothing is announced and nothing is moved when the rider was **not** inside the
  thing that vanished. `vanish.spec.js A8b`, `A8g`.
- Closing the mobile overlay un-inerts the background **before** restoring focus,
  because restoring focus into an inert subtree silently does nothing.
  `mobile.spec.js A6o`.
- Closing the panel never strands focus on the body, by any closing path.
  `stations.spec.js A1b`.

### The station panel, which is the text equivalent of the map

- Every station the map knows, across all six rail and ferry systems, is
  searchable, and each one's next arrivals read as sentences.
  `stations.spec.js A1a`, `A1e`.
- Arrivals that are stale, still warming, failed, or scheduled rather than live
  say so **in words**. `stations.spec.js A1f`, `A1h`, `A1n`, `A1o`, `A1q`.
- A ferry dock's wheelchair accessibility is stated in words, not left as a
  glyph. `stations.spec.js A1g`.
- Selecting a station pans the map and opens that station's popup, so a
  screen-reader rider and a sighted rider are looking at the same place, and it
  never steals focus. `stations.spec.js A1c`.

### Announcements

- One live region, one door: nothing else in the app writes to it.
  `announce.spec.js A2i`.
- A feed going stale announces **once** and then stays quiet.
  `announce.spec.js A2f`.
- Two refreshes of unchanged data announce **nothing**, and a countdown tick
  never speaks. `announce.spec.js A2g`, `stations.spec.js A1r`, `A1p`.
- A new agency-wide alert announces once, as a summary. `announce.spec.js A2h`.

### Colour, size and layout

- Every rendered route colour meets AA wherever it carries or is text, computed
  in-page with the same sRGB and relative-luminance formulas the app uses.
  `layout.spec.js A4g`.
- Every interactive thing on the map surface meets the WCAG 2.2 **24px target
  floor**, sampled at 1280, 375 and 320. `layout.spec.js A4b`.
- The document **never scrolls sideways**, at 1280 docked and at 375.
  `layout.spec.js A4h`.
- The legend panel stays inside the viewport at both widths, so the status line
  inside it cannot fall off the bottom where nothing scrolls.
  `layout.spec.js A4f`.
- The alert banner never covers the zoom controls, at 1280 or 320.
  `layout.spec.js A4a`.
- A popup never exceeds the phone's viewport (`mobile.spec.js A6e`) and never
  opens underneath the legend: if it would, the map pans down so it does not
  (`a11y.spec.js A1w`, the 375 popup state).

### Motion

The map follows one rule: **animate the journey, never the adjustment.**

- A **journey** is navigation the rider chose, and it keeps its preference gate.
  Selecting a station in the panel pans the map to it, animated unless
  `prefers-reduced-motion: reduce` is set, because the motion carries continuity:
  it shows that this new place is that old place, moved. `motion.spec.js A5g`
  (with the preference), `A5h` (without it).
- An **adjustment** is the app correcting its own fit, and it is instant for
  everyone, preference or none. Leaflet nudging an opening popup back inside the
  viewport is one; so is moving a popup out from under the legend. Nobody asked
  for it and it carries no continuity. `motion.spec.js A5e`, `A5f`.
- With the preference set, vehicles also step to each new position instead of
  sliding, and the marker and panel transitions are off.
  `motion.spec.js A5a` through `A5d`.
- Nothing is hidden and no data is withheld: the preference changes only how a
  position updates, never what is shown.

## What axe cannot decide, and how each is decided instead

axe reports a third category besides pass and fail: **incomplete**, meaning it
needs a human. A green "zero violations" says nothing about those, so the gate
asserts them too. The list is closed: an incomplete finding outside these three
shapes fails the build. Each entry names the test that answers the question axe
declined, and `a11y.spec.js A1z` **asserts that pairing**, so an exception cannot
quietly become a suppression with a sentence attached.

| What axe could not decide | Why | How it is decided |
| --- | --- | --- |
| Contrast of a single-character glyph inside an SVG icon | axe cannot tell a route letter from a decorative mark when the visible text is one character | `layout.spec.js A4g` computes every rendered route colour's contrast in-page; `a11y.spec.js A1z` measures the zoom glyph and the popup close glyph against their own control backgrounds |
| Contrast of Leaflet's attribution, which sits directly on map tiles | the background is live imagery, so there is no single colour to compute against | `a11y.spec.js A1z` composites the attribution's translucent background over **both** extremes a tile can be, black and white, and requires AA against the worse of the two, which bounds every possible tile |
| Whether the skip link's target becomes visible on activation | the panel is hidden at scan time and no static rule can activate a link | `mobile.spec.js A6g` presses it at 375 and asserts focus lands inside the panel that was hidden a moment earlier; `A6h` asserts the desktop behaviour it must not break |

This inventory may only shrink by **conversion**: an entry leaves it by becoming
decidable and passing, never by being deleted and never by loosening a pattern
until it stops matching.

## What is not covered

These are the honest gaps. They are not ranked by how easy they would be to fix.

**No assistive technology has been used on this page.** Every claim above comes
from automated checks driving headless Chromium. NVDA, JAWS, VoiceOver, Narrator,
Dragon, switch access and screen magnifiers have not been run against it, and no
disabled rider has tested it. Automated checks catch a minority of accessibility
defects; the ones they catch are the ones stated above, and nothing more should
be read into a green build.

**One browser engine.** The gate runs in Chromium only. Firefox and WebKit are
untested, including their differing `inert` and focus behaviours.

**Two widths for the scan, four for layout.** The axe gate runs at 1280 and 375.
Layout and target size are additionally sampled at 320, and the 700px and 1100px
breakpoints are exercised in both directions (`mobile.spec.js A6b`, `A6p`). Widths
between and beyond those are unverified.

**Six states.** The gate scans the six states listed above. Error states, stale
and partial-outage states, and the many combinations of open panel plus open
popup plus active banner are not axe-scanned, though several are covered by
behavioural specs.

**Vehicle markers are deliberately outside the tab order.** There can be several
hundred of them, and tabbing through every bus in Brooklyn to reach a control is
not a keyboard path anyone wants. This is a considered exception with a stated
equivalent: the station panel is the keyboard path to the same arrival data, one
Tab away via the skip link. Markers do carry accessible names, so a screen reader
on a touch device announces "1 train, next stop Times Sq-42 St, Northbound"
rather than an unlabeled button (`markers.spec.js A2a` through `A2d`,
`frontend/markers.test.js`), and the map container itself stays focusable so
Leaflet's arrow-key panning still works (`markers.spec.js A2e`). Where a vehicle
sits on top of a station, its popup carries an "Also here" link to that station's
arrivals so the station stays reachable (`crosslink.spec.js A3a`, `A3b`).

**The map is still a picture.** The tiles are third-party imagery with no text
alternative. Geographic relationships, route shapes and vehicle positions are not
available in text; what is available in text is arrivals, by station.

**Buses are not in the station panel**, because their stops are not stations in
the data this app has. A keyboard-only rider cannot reach bus arrivals.

**Text resize and zoom are unverified.** No test asserts behaviour at 200% text
size or 400% page zoom. Horizontal scrolling is pinned at 1280 and 375 at default
zoom only (`layout.spec.js A4h`).

**Leaflet reads its zoom and pan animation settings once at load.** If the
reduced-motion preference changes while the page is open, everything else responds
immediately but those two take effect on the next load.

**Criteria no automated check can reach** have had no formal audit: meaningful
sequence, headings and labels quality, error identification and suggestion,
language of parts, and consistent help among them.

## Reporting a problem

Open an issue at
<https://github.com/rosenfieldben/nyc-transit-live/issues>. A description of what
you were trying to do, what happened instead, and which assistive technology and
browser you were using is enough; a reproduction is welcome but not required.

Barriers found by riders take priority over anything on the automated list, since
by definition they are the ones the automation missed.

## Provenance

Written from the accessibility gate's own final exception list at the close of
the A4 phase. Re-verify the whole statement with:

```bash
npx playwright test --config tests/e2e/playwright.config.js
node --test "frontend/*.test.js" "tests/*.test.js"
```
