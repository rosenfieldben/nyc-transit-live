# Handoff: NYC Transit Live — front-end map redesign

Repo: `rosenfieldben/nyc-transit-live` (branch `main`, scope `frontend/`).

## Overview

A restyle of the live Leaflet map so the network reads like the MTA's geographic subway map: bold trunk-colour ribbons, route-bullet train markers, dot/ring stations with haloed names, and a new grammar for commuter rail (LIRR, Metro-North, NJ Transit) that makes each train's agency, branch, heading and data source legible at a glance. The page chrome moves from a boxed right-hand legend to a slim full-width top bar with a feed strip, styled on the Modernist design system (Archivo, zero radius, 2px rules, one red accent).

Scope agreed with the product owner: marker shapes and colours, station and vehicle popups, status/freshness. Layer toggles and alerts were restyled along the way because they live in the same header. The Stations side panel and the accessibility panel behaviour are **out of scope** and unchanged.

## About the design files

Everything in `prototype/` is a **design reference built in HTML** on top of the real Leaflet 1.9.4 library with synthetic data (`sample-data.js`). It shows intended look and behaviour; it is not production code to paste in. The task is to **recreate it inside the existing vanilla-JS frontend** (`frontend/index.html`, `frontend/style.css`, `frontend/helpers.js`, `frontend/systems/*.js`) using its established patterns: `L.divIcon` HTML strings, canvas renderers, `escapeHtml`, the existing feed-age/staleness helpers and CSP. No new tile origin, framework or build step is needed.

Open `prototype/Map Redesign v2.dc.html` in a browser (serve the folder over HTTP; it fetches sibling files). Use **Rail** (bottom-right) to see the commuter-rail grammar, **Dark** for the dark theme, **Key** for the legend. `Train Markers Study.dc.html` shows every rail marker at 1× and 3×. `Current Map.dc.html` is a faithful recreation of today's UI for side-by-side comparison. `Map Redesign.dc.html` (earlier A/B exploration) is not part of the handoff.

## Fidelity

**High-fidelity.** Colours, type, sizes and interactions below are final and should be matched. Data values (counts, station coordinates, train positions, alert text) are sample data only. Basemap tiles are the same OSM tiles the app already serves, with a CSS filter.

---

## Screens / views

There is one screen: the map. Its regions are described from top to bottom.

### 1. Top bar (`<header>`)

Full-width, pinned to the top of the map container, `z-index: 1000`.

- Background `color-mix(in srgb, var(--surface) 90%, transparent)` with `backdrop-filter: blur(14px)`; `border-bottom: 2px solid var(--divider)`. No shadow, no radius.
- **Row 1** — `display:flex; flex-wrap:wrap; align-items:center; gap: 8px 20px; padding: 0 16px; min-height: 48px`.
  - Brand: "NYC Transit Live", Archivo 800 15px, letter-spacing −0.02em. Immediately right (baseline-aligned, gap 10px): live clock `HH:MM:SS`, Archivo 600 10px, uppercase, letter-spacing 0.1em, colour `--muted`, tabular numerals, preceded by a 5×5px accent square that blinks (`@keyframes blink` 2s: opacity 1 → .25 → 1).
  - Subway key: route bullets grouped by trunk, `margin-right:auto`. Each trunk is a `flex` group with `gap:2px`; groups have `gap: 4px 8px`. A bullet is a 22×22px circle button, no border, trunk colour fill, Archivo 800 11.5px, text colour black on the yellow trunk and white on the rest. Hover: `filter: brightness(0.9)`. Focused route: `outline: 2px solid var(--accent); outline-offset: 2px`; all other bullets fade to opacity 0.3 (transition 0.2s). "ALL LINES" text button (accent colour, 600 10px uppercase, letter-spacing 0.08em) appears only while a route is focused.
  - Actions, right-aligned, `gap:2px`: **Dark/Light** and **Key** are text-only buttons (height 30px, padding 0 10px, Archivo 600 10px uppercase, letter-spacing 0.08em, colour `--muted`, hover → `--ink`). **Stations** is the single filled button: `background: var(--accent); color: var(--chipink)`, Archivo 700 10px uppercase, padding 0 14px, `margin-left: 8px`, hover `filter: brightness(0.92)`. Zero radius.
- **Row 2, feed strip** — `border-top: 1px solid var(--rule); min-height: 30px; padding: 0 10px; display:flex; flex-wrap:wrap; gap: 0 4px`. One button per feed, in this order: Subway, Buses, LIRR, Metro-North, NJ Transit, PATH, Ferry, AirTrain.
  - Button: `inline-flex; gap:7px; height:30px; padding:0 8px; background:transparent`, hover tint `color-mix(in srgb, var(--ink) 6%, transparent)`. Hidden feed → opacity 0.3.
  - Leading mark: for subway/bus/PATH/ferry a 12×3px colour tick (`#0039A6`, `#605d5d`, `#d93a30`, `#00839c`); for LIRR/MNR/NJT/AirTrain the agency glyph block (13px tall, min-width 11px, padding 0 3px, `background: var(--ink); color: var(--surface)`, Archivo 800 8px): `L`, `M`, `NJ`; AirTrain uses a gray tick `#6d6e71`.
  - Name: Archivo 600 10px uppercase, letter-spacing 0.08em, `--muted`. Count: Archivo 800 12px, tabular. Freshness dot 5×5px circle: green `#00933c` = live, accent = stale, `var(--divider)` = scheduled-only. Tooltip carries the wording ("Live · 12s · hide Subway", "As of 6m ago · hide Metro-North", "Scheduled · hide NJ Transit").
  - Trailing note, `margin-left:auto`: worst stale feed in words, e.g. "METRO-NORTH AS OF 6M AGO" (600 10px uppercase, `--muted`). Omit when everything is live.
- **Key panel** (toggle) — `border-top: 2px solid var(--divider); padding: 10px 14px 12px; display:grid; grid-template-columns: 1fr 1fr; gap: 7px 18px; font-size: 12px`. Rows are icon + label as in the prototype: train bullet, subway ribbon, local station, transfer station, commuter branch line, commuter station square, live commuter tag, scheduled commuter tag, PATH diamond, dashed ferry route, bus arrow, dimmed = stale, and the two explanatory notes (zoom thresholds; agency glyph and branch code legend).

### 2. Service alerts strip

Sits under the header, right-aligned inside the same top-anchored column (`margin: 12px 12px 0`), width `min(380px, 100vw − 24px)`.

- `background: color-mix(in srgb, var(--surface) 92%, transparent); backdrop-filter: blur(14px); border-left: 2px solid var(--accent); box-shadow: var(--shadow)`; padding `10px 10px 10px 14px`; font 12px / 1.45.
- Each alert is a row: leading route marks (subway bullets `.bul.sm`, or a `BUS` tag `.sq.sm` in ink) then the text. Rows separated by 8px, no heading.
- Dismiss: 24×24 "×", `--muted` → `--ink` on hover. Hides the strip for the session (existing banner-plan logic stays).

### 3. Bottom-right control stack

`position:absolute; right:12px; bottom:100px`, above the Leaflet zoom control. Same translucent surface, `border: 1px solid var(--divider)`.

- Buttons, top to bottom: **City** (zoom 13, centre 40.7295, −73.99), **Rail** (zoom 11, 40.76, −73.96), **Region** (zoom 10, 40.79, −73.90), **Names** (toggle station labels). Each `height:30px; padding:0 12px; min-width:72px`, Archivo 600 10px uppercase, letter-spacing 0.08em, left-aligned, `border-bottom: 1px solid var(--rule)`. Active: `background: var(--ink); color: var(--surface)`.
- View change uses `map.flyTo(center, zoom, { duration: 0.8 })`.
- Leaflet zoom control: `border-radius:0; border:1px solid var(--divider); background: var(--surface); color: var(--ink)`, glyphs Archivo 800 16px, no Leaflet shadow. Attribution: `background: var(--surface); color: var(--muted)`, 10px.

### 4. Map content

**Basemap** — `https://tile.openstreetmap.org/{z}/{x}/{y}.png` (unchanged). Apply to `.leaflet-tile-pane`:
light `filter: saturate(0.2) sepia(0.12) brightness(1.07) contrast(0.86)`;
dark `filter: grayscale(0.7) invert(1) hue-rotate(180deg) brightness(0.7) contrast(1.0)`.

**Panes** — keep `stationPane` at `z-index: 450` (between overlay and marker panes) for station markers.

**Subway route lines** — two polylines per shape on the canvas renderer, both `lineCap:"round"; lineJoin:"round"; interactive:false`: a casing in paper colour (`weight: 6.5; opacity: 0.9`) and the trunk-colour line (`weight: 4; opacity: 1`). Draw the yellow trunk (N/Q/R/W) last so it never sits under darker trunks. Trunk colours (official MTA):
`1 2 3` #EE352E · `4 5 6` #00933C · `7` #B933AD · `A C E` #0039A6 · `B D F M` #FF6319 · `G` #6CBE45 · `J Z` #996633 · `L` #A7A9AC · `N Q R W` #FCCC0A (black text) · `S` shuttles #808183.
Focus state (bullet click): the focused route keeps casing 0.9 / line 1; all others drop to casing 0 / line 0.18 and their trains to opacity 0.15.

**Subway train marker** — the route bullet, lifted so it sits just above the ribbon. `L.divIcon`, class `m-subway v-lift`, 18×18 icon with a 2px halo bleed: `<circle r="9.5" fill=paper opacity=.95>` behind `<circle r="8" fill=trunk>` and the letter (Archivo 800; 10.5px for one character, 8.5px for two). `iconAnchor: [9, 21]; popupAnchor: [0, −21]`. Keep the existing 24×24 invisible hit target (`::before`), anchored to the bottom for lifted markers.

**Subway stations** — `L.circleMarker` on the station-pane canvas. Local (one route): `radius 3.5`, fill ink, no stroke. Transfer (two or more routes): `radius 4.5`, fill paper, `stroke ink weight 2`. Names via permanent tooltip, class `stn-label`, `direction:"right"; offset:[7,0]`: Archivo 600 10.5px, ink, letter-spacing 0.01em, halo `text-shadow: 0 0 2px halo ×2, 0 0 3px halo, 0 0 4px halo` (halo = `rgba(243,242,242,.92)` light / `rgba(32,30,29,.9)` dark), no background/border/padding, `pointer-events:none`. Transfer stations get class `hub` (700 11.5px). Visibility by zoom via a `data-zoom` attribute on the root: hubs from zoom 12, all from 14; the Names toggle (`data-labels="off"`) hides all.

**Buses** — smallest marks on the map. Heading known: 14×14 arrow `M7 1 L12 13 L7 10 L2 13 Z` rotated to bearing, fill = route colour, paper stroke 0.8. Heading unknown: 12×12 dot `r 3.5`, paper stroke 1. Route colour is the existing hashed hue but muted: `hsl(h, 45%, 38%)`.

**Commuter rail lines (LIRR / MNR / NJT)** — branch colour, casing paper `weight 5 opacity .9` plus line `weight 2.5 opacity 1`, round caps. Branch code + colour table (`system|route_id` → `[code, colour]`):
LIRR — 1 BAB #00985F · 2 MAIN #A626AA · 3 RON #A626AA · 4 PJ #006EC7 · 5 OB #00AF3F · 6 HEM #CE8E00 · 7 LB #FF6319 · 8 FR #6E3219 · 9 ATL #4D5357 · 10 PW #C60C30 · 11 GCM #4D5357
MNR — 1 HUD #009B3A · 2 HAR #0039A6 · 3 NH #EE0034
NJT — 7 NEC #EF3E42 · 8 NJCL #00A0DF · 9 RVL #FAA634 · 10 M&E #00A94F · 11 MOBO #A2559D · 12 MBL #FFD006 · 13 PVL #8E258D
Unknown route → code = route id, colour #6d6e71. **Map these against the real GTFS `route_id`s in the feeds** (the prototype's ids are sample data).

**Commuter rail stations** — squares, so a square always means regional rail and a circle always means subway. `L.divIcon` 10×10, `<rect x=1 y=1 w=8 h=8 fill=paper stroke=ink stroke-width=1.6>`, on `stationPane`, hit target 20×20. Names as above (never `hub`). Shown from zoom 11.

**Commuter train marker (`railTag`)** — a two-part tag lifted above the track plus a heading chevron on the track. Total glyph 30px tall; `iconAnchor: [w/2, 21]` so the chevron centre sits on the line.
- Tag height 13px. Agency block width 11px (`L`, `M`) or 16px (`NJ`); branch block width `code.length × 5.6 + 7`. Text Archivo 800 8px, centred in each block.
- **Live GPS (solid):** paper backing rect 1px larger at opacity .9; agency block `fill ink`, glyph in paper; branch block `fill branchColour`, code in black-or-white per luminance. Chevron `M cx 15.5 L cx+5 24.5 L cx 22 L cx−5 24.5 Z`, fill ink, paper stroke 1, rotated to bearing about (cx, 21). Unknown heading → dot `r 3` fill ink.
- **Scheduled (outlined):** rect `fill paper stroke ink 1.2`, vertical divider at the agency block edge, both texts in ink, branch colour as a 2.5px stripe along the bottom of the branch block. Chevron/dot outlined: fill paper, stroke ink 1.4.
- Stem: 1px ink line from tag bottom (y 13) to y 16.5.
- Bearing = direction of travel along the branch polyline at the nearest segment (`bearingAlong`): project points to layer space, find the segment with the smallest `L.LineUtil.pointToSegmentDistance`, take its direction, reverse when the train is inbound. NJT uses headsign === "New York" as inbound.
- Stale feed: marker opacity 0.45 (compounds with any resting opacity, as today).

**PATH** — lines in the feed's route colour, `weight 3.5`, round caps, casing none. Stations: subway "local" dot style. Trains: 16×16 diamond `M8 1 L15 8 L8 15 L1 8 Z`, route fill, paper stroke 1.2, lifted (`v-lift`).

**Ferry** — routes dashed `weight 2; opacity .9; dashArray "6 5"`. Docks: `circleMarker radius 4`, fill `#00839c`, paper stroke 1.5, names shown. Boats: 22×14 hull `M1 3 H21 L17.5 11 H4.5 Z`, route fill, paper stroke 1; docked boats opacity 0.55 (existing rule).

**AirTrain JFK** — gray `#6d6e71` (dark: `#9a9a9a`), `weight 3`, `dashArray "8 5"`. Stations use the commuter square.

### 5. Popups

Leaflet popup restyle (`.leaflet-popup-content-wrapper`, `.leaflet-popup-tip`): `background: color-mix(in srgb, var(--surface) 94%, transparent); backdrop-filter: blur(14px); color: var(--ink); border-radius: 0; box-shadow: var(--shadow); border-left: 2px solid var(--ink)`. Content margin `14px 16px`, Archivo 13px / 1.35, min-width 220px, `maxWidth 320`. Close button `--muted`, 16px.

> **Erratum, MR5 (2026-09-20): the popup ships at full `--surface` opacity, and the 94% is
> overruled the way MR1 overruled the header's 90%.** MR1's finding F1 is the same finding about
> the same drawing one surface out: "axe cannot resolve the contrast of text over a translucent
> surface whose backdrop is a tile IMAGE", and making the header opaque took the undecidable set
> from nine entries to one. Measured on this branch at 94%, every popup's text came back
> `incomplete` ("background color could not be determined because element contains an image node")
> at 1280, 375 and 320 in both themes. **And it did worse than obscure a safe surface: the dark
> theme's real `color-contrast` violation on the popup head's ink and `.popup-sub` was reported
> ONLY as undecidable, so the translucency hid a serious failure.** Opaque, axe names that
> violation, which is how MR5 came to fix it. The undecidable inventory does not grow.
> **`backdrop-filter: blur(14px)` goes with the alpha**, for the same reason and by the same
> precedent: a backdrop filter filters what is behind the element and the element's own background
> then paints over it, so at full opacity none of the filtered backdrop is ever visible. MR1 took
> the filter off the header along with the header's 90%; this is that pair one surface out. The ink
> edge, the radius, the shadow and the content metrics are as drawn. Full measurements in
> `docs/reviews/map-redesign-rounds.md` under Stage MR5.

Auto-pan must clear the page chrome: on `popupopen`, measure the rendered header + alert strip bottom edge and set `autoPanPaddingTopLeft = [24, bottom + 12]`, `autoPanPaddingBottomRight = [110, 40]`, then call `_adjustPan()`.

> **Erratum, MR5 (2026-09-19): the recipe above is measured broken on this app, and stage MR5
> implements a clamped form of it.** Two measurements, both on the shipped frontend. (1) The
> paddings carry no viewport-fit guard, and Leaflet's own arithmetic lets the TOP padding win
> unconditionally when top and bottom cannot both be honoured. With the Key panel open at
> 375x640 the header's bottom edge is 579, so the recipe asks for a top padding of 592 and
> `_adjustPan()` puts the popup at `top 592, bottom 718` on a 667px map: 51px off the bottom.
> The app's own `panPopupClearOfChrome` cannot rescue it, because that is a collision solver
> and the popup is not colliding, it is off-screen. Horizontally `24 + 110` is unsatisfiable
> below about 400px wide. (2) Leaflet's autopan has no equivalent of this app's
> `riderOwnsTheView` guard, and `popup.update()` runs it every fifteen seconds for every open
> vehicle popup, so the recipe's padding grows the band in which the map is yanked out from
> under a rider from a 5px strip to the whole header. **What MR5 ships**: each padding clamped
> to what the measured map and popup can satisfy, `panPopupClearOfChrome` kept as the authority
> for the real boxes and for growth after the first paint, and the padding stood down while the
> rider owns the view. The full measurements are in `docs/reviews/map-redesign-rounds.md` under
> Stage MR5, ruling S3.

Shared vocabulary (classes in `reference/map-redesign-v2.css`):
- `.pk` kicker row: 600 10px uppercase, letter-spacing .1em, `--muted`, `flex; justify-content: space-between` (left: "Subway station", right: route bullets / direction / accessibility).
- `.pt` title: 800 17px / 1.15, letter-spacing −.015em, with the route mark before the text (`.bul.lg` 24px bullet for subway, `.rtag` for commuter rail, `.sq` for bus/PATH/ferry).
- `.bul` route bullet 20px circle (`.sm` 17px, `.lg` 24px), Archivo 800. `.sq` square tag 20px tall, padding 0 5px. `.rtag` commuter tag 18px tall: `b` agency block ink/paper padding 0 5px; `i` branch block padding 0 6px in branch colour.
- Vehicle popup body `.kv`: 2-column grid label/value, `border-top 1px --rule`, rows `padding 5px 0; border-bottom 1px --rule`; labels 11px uppercase `--muted`, values right-aligned 600 tabular. Rows: Train / Next stop / Delay / Position ("Live GPS" | "Scheduled, no GPS" | "Placed from arrivals") / Trip.
- Station popup: `.dir` direction header (800 11px uppercase, `border-bottom 2px --divider`, margin-top 10px) then `.arr` rows in a 3-column grid (mark · destination/branch · countdown). Countdown 700 tabular; under 30s reads "now" in the accent.
- `.alert` block above everything: `border-left 3px var(--accent); padding 6px 0 6px 10px; 11px/1.4`.
- `.xlink` cross-link button ("Also here: Jamaica →"): 600 11px, `border 1px --divider`, transparent.
- `.fresh` footer: `border-top 1px --rule; margin-top 10px; 600 10px uppercase --muted` with a 6×6 square: green `#00933c` "LIVE · UPDATED 12S AGO"; stale → accent text and square, "AS OF 6M AGO · FEED STALE"; schedule-only → gray square, "SCHEDULED HEADWAYS · NO LIVE FEED".

> **Erratum, MR5 (2026-09-20): the vocabulary ships in these class names, with three deviations,
> each measured.**
>
> 1. **A popup's route mark is the MAP's mark, not `.bul.lg` / `.sq` / `.rtag`.** The three DOM
>    forms above would be a second drawing of a mark this app already builds (the subway's plate,
>    the rail tag, the bus arrow or dot, the PATH diamond, the ferry hull), and two drawings of one
>    thing drift the first time either is edited, which is this phase's finding N6 one surface out.
>    So `popupMarkHtml` re-wraps the string the marker's own icon is wearing, at the size this list
>    gives (`.bul.lg` 24 in a title, `.bul.sm` 17 in a kicker or a row), with the mark's body copied
>    byte for byte. The rail tag is drawn at its own 30-unit box instead of 24, because scaling that
>    box down would draw its blocks at 10.4 units with 7px type, smaller than the map draws them.
>    A subway station, a PATH station and a ferry dock are canvas circles with no string to borrow,
>    so their titles carry words alone.
> 2. **`.arr .now` is `--accent-ink`, not `--accent`.** Measured on the popup's surface, `--accent`
>    reads 3.47 in the light theme, below the 4.5 a string owes; `--accent-ink` is the token that
>    exists for that case and reads 5.03 light and 5.44 dark. Same hue, readable lightness. This is
>    the same correction the `.fresh` footer's words took under ruling Q2.
> 3. **The footer's three sentences are not typed anywhere** (ruling Q2): "LIVE · UPDATED 12S AGO"
>    is the sentence memo D9 forbids, so the live state shows its square and says its words only to
>    a screen reader, and the stale and schedule-only states take the feed strip's own strings from
>    `feedStateWords`. The square, the rule above it and the metrics are as drawn.
>
> 4. **`.alert` ships as the app's `.alert-block` with `.alert-row` inside it**, carrying this
>    list's rules (accent left edge, `6px 0 6px 10px`, 11px, `--ink`, no fill) on the REGION rather
>    than on each alert. The `.alert + .alert` rule above implies one box per alert, which draws an
>    accent edge per alert: a station popup with three of them would read as three warnings rather
>    than one block of them. `.xlink` IS renamed, because there the class was the only thing left to
>    adopt, and its arrow is `aria-hidden` so a screen reader does not read "right arrow" after the
>    station's name. Measured, that span made axe report a new undecidable finding ("Element content
>    contains only non-text characters") at every width in both themes, and the ruling on this
>    surface is that the undecidable inventory does not grow, so the arrow is not drawn at all.
>
> The `.kv` row list is as given, plus four labels the app's own fields needed (Direction, Status,
> Speed, To) and two nouns it already printed (Bus, Boat). Full measurements in
> `docs/reviews/map-redesign-rounds.md` under Stage MR5.

---

## Interactions & behaviour

- **Route focus**: clicking a header bullet toggles focus on that route (click again or "All lines" clears). Applies opacity changes described above; no re-render of layers.
- **Feed toggles**: click a feed-strip button to add/remove that system's layer groups (lines, stations, vehicles). Hidden feeds show at opacity 0.3. Station groups also respect zoom gating.
- **Zoom gating**: subway/PATH/ferry station groups added at zoom ≥ 12 (names from 14, transfer names from 12); commuter/AirTrain station groups at zoom ≥ 11. Vehicles and lines always shown. Recompute on `zoomend` and after toggles.
- **Theme**: `data-theme="light|dark"` on the root swaps CSS variables; markers whose SVG embeds ink/paper colours must be rebuilt (the prototype tears down and rebuilds the map at the same view). Persist the choice in `localStorage`.
- **Names toggle**: sets `data-labels="off"`; CSS hides `.stn-label`.
- **View presets**: `flyTo` 0.8s. Active preset highlighted.
- **Alerts dismiss**: hides the strip; existing banner-plan/session logic unchanged.
- **Marker motion**: keep the existing 0.5s glide (`transition: transform .5s`) on buses and ferries; frozen when stale, as today.
- **Reduced motion**: respect the existing `motionAllowed()`; the clock dot blink should also stop.
- **Responsive**: header rows wrap (`flex-wrap`), alert strip is `min(380px, 100vw − 24px)`, control stack stays bottom-right. Nothing has fixed heights that hold text.

## State

`theme`, `focusRoute | null`, `hidden: Set<feedKey>`, `zoom`, `labels: boolean`, `view: "City"|"Rail"|"Region"`, `bannerDismissed`, plus the existing feed-age map used for freshness dots and popup footers. Counts per feed come from the existing vehicle arrays.

## Design tokens

From the Modernist stylesheet (`prototype/_ds/…/styles.css`), mapped to the map's local variables:

Light: `--bg` #f3f2f2 · `--surface` #eae9e9 · `--ink` #201e1d · `--muted` #605d5d · `--accent` #ec3013 · `--divider` rgba(32,30,29,.4) · `--rule` rgba(32,30,29,.15) · `--chipink` #f3f2f2 · `--halo` rgba(243,242,242,.92) · `--shadow` 0 3px 10px rgba(45,43,43,.16)
Dark: `--bg` #201e1d · `--surface` #2d2b2b · `--ink` #f3f2f2 · `--muted` #bab6b6 · `--accent` #ff563c · `--divider` rgba(243,242,242,.35) · `--rule` rgba(243,242,242,.14) · `--chipink` #201e1d · `--halo` rgba(32,30,29,.9) · `--shadow` 0 3px 10px rgba(0,0,0,.5)
Status: live green #00933c · scheduled gray #6d6e71 (dark #9a9a9a).

Type: Archivo only (Google Fonts, weights 400/600/800; fallback `system-ui, sans-serif`). Sizes used: 8, 10, 10.5, 11, 11.5, 12, 13, 15, 17px. Uppercase labels carry letter-spacing .08–.1em.
Radius: 0 everywhere except route bullets and freshness dots, which are circles by definition.
Rules: 2px `--divider` for major edges, 1px `--rule` for hairlines.
Spacing: 8/10/12/14/16px paddings as listed per element.
Shadows: only `--shadow` on the alert strip and popups.

## Screenshots

In `screenshots/` (1280px-wide captures of the prototype):

- `01-city-light.jpg` — City view, light theme: header, feed strip, alert strip, subway ribbons and bullets.
- `02-rail-light.jpg` — Rail view: LIRR / Metro-North / NJ Transit branch lines, square stations, train tags with chevrons.
- `03-rail-train-popup.jpg` — a commuter train popup (agency · branch tag, key/value rows, freshness footer).
- `04-city-subway-popup.jpg` — a subway train popup with the route bullet title.
- `05-key-open.jpg` — the Key panel expanded.
- `06-city-dark.jpg`, `07-rail-dark.jpg` — dark theme.
- `08-marker-study.jpg` — every marker at 1× and 3×, both themes.
- `09-current-map.jpg` — the current production UI, for comparison.

## Assets

None. All marks are inline SVG generated from data. Font: Archivo via Google Fonts (or self-host). Tiles: OpenStreetMap, as today.

## Files

- `prototype/Map Redesign v2.dc.html` — the design. Open over HTTP.
- `prototype/Train Markers Study.dc.html` — every rail/subway marker at 1× and 3×, light and dark.
- `prototype/Current Map.dc.html` — recreation of the current UI for comparison.
- `prototype/sample-data.js` — synthetic feed data in the backend's shapes.
- `reference/map-redesign-v2.css` — the prototype's stylesheet block (Leaflet overrides, labels, popup classes).
- `reference/map-redesign-v2.logic.js` — the prototype's logic: trunk/branch tables, `railTag()`, `railTagHtml()`, `bearingAlong()`, popup builders, focus/gating/toggle behaviour. Port from this, don't ship it.
- `IMPLEMENTATION.md` — file-by-file mapping onto the repo.
