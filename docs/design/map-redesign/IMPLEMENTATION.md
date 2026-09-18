# Implementation map — `rosenfieldben/nyc-transit-live`

Where each part of the redesign lands in the existing frontend. Keep the current architecture (vanilla JS, Leaflet 1.9.4 vendored, canvas renderers, `escapeHtml` on all feed strings, existing CSP).

## frontend/index.html
- Remove the right-hand `<aside>` legend/toggles/status panel and the top alert `<section>` positioning.
- Add: `<header>` (row 1 brand + subway bullets + actions; row 2 feed strip; collapsible key), the alert strip under it, and the bottom-right view stack. Markup and inline sizes per README §1–3.
- Root `#map` wrapper gets `data-theme`, `data-zoom`, `data-labels` attributes.
- Load Archivo (Google Fonts link, or self-host and add to CSP `font-src`).

## frontend/style.css
- Replace panel/legend/banner/popup rules with `reference/map-redesign-v2.css` (rename `.om-map` to the root selector you use). Keep `.leaflet-marker-icon::before` hit-target rules; add the `.v-lift` bottom-anchored variant and `.m-rstn::before { 20px }`.
- Tile filters on `.leaflet-tile-pane` (light/dark).
- `.stn-label` visibility by `[data-zoom]` / `[data-labels="off"]`.
- Motion: gate `@keyframes blink` and marker transitions behind `prefers-reduced-motion` / `motionAllowed()`.

## frontend/helpers.js
- `lineColor(routeId)` → the MTA trunk table (README §4). Keep the readable-ink fallback for popups.
- Add `RAIL` table (`system|route_id → [code, colour]`), `railMeta / railCode / railroadColor(system, route)`. Replace the hashed palette `railroadColor(id)`; update callers to pass the system.
- Add `sysGlyph(system)` (L / M / NJ) and `sysName(system)`.
- Add `railTagIcon(system, route, placed, bearing)` returning the `L.divIcon` per README §4 "Commuter train marker", and `railTagHtml(system, route)` for popups.
- Add `bearingAlong(map, pts, lat, lon, inbound)`.
- Popup builders: `vehiclePopupHtml({kicker, sub, mark, title, rows, alerts, age, xlink})` and `stationPopupHtml({kicker, title, buckets, markFor, alerts, age, extra})` using the `.pk/.pt/.kv/.dir/.arr/.fresh/.alert/.xlink` classes. Route the existing `stalePopupLine` / `feedAgeLine` wording into `.fresh`.
- Bus route colour: `hsl(h, 45%, 38%)`.

## frontend/systems/shared.js
- Map init: `zoomControl:false`, add `L.control.zoom({position:"bottomright"})`.
- `popupopen` handler that measures header + alert strip and sets autopan padding (README §5).
- `setStatus()` → feed-strip update: per-feed count + freshness dot + tooltip, and the trailing "worst stale" note.
- `renderAlertBanner()` → new strip markup (route bullets / BUS tag per row, dismiss ×). Keep the plan/session logic.
- Zoom gating helper: `stationMinZoom(group)` (11 regional, 12 city) applied on `zoomend` and after toggles; `data-zoom` attribute update.
- Theme toggle: swap `data-theme`, persist to `localStorage`, rebuild marker icons that embed ink/paper (subway bullets' halo, rail tags, squares) — or re-render all systems once.
- Route focus: registry `route → { polylines[], markers[] }` populated by subway.js; `applyFocus(route|null)` sets opacities.

## frontend/systems/subway.js
- Lines: casing (paper, 6.5) + trunk line (4), round caps; yellow trunk drawn last; register in the focus registry.
- Trains: bullet `divIcon` (18px, halo, `v-lift`), `iconAnchor [9,21]`.
- Stations: local dot vs transfer ring by `routes.length`; permanent tooltip labels (`stn-label`, `hub`).
- Popups via the new builders (route bullets in kicker, `.bul.lg` in title).

## frontend/systems/railroad.js (LIRR + Metro-North)
- Split layer groups per agency (`lirr*`, `mnr*`) so the feed strip can toggle them separately.
- Lines: casing (paper, 5) + branch colour (2.5).
- Stations: 10px square `divIcon` on `stationPane`; labels.
- Trains: `railTagIcon(system, route, placed = stop_id != null, bearing)`; bearing from `bearingAlong` with the branch polyline and `direction`; stale opacity compounding as today. Popup title = branch name, mark = `railTagHtml`.

## frontend/systems/njt.js
- Same as railroad.js with system `"NJT"`; all trains `placed = true` (outlined tags); inbound when headsign is "New York". Popup rows include Delay ("On time" / "N min late").

## frontend/systems/path.js
- Lines weight 3.5 round caps, no casing. Stations: local-dot style + labels. Trains: 16px diamond, `v-lift`.

## frontend/systems/ferry.js
- Routes dashed `6 5`, weight 2. Docks radius 4 `#00839c`, labels. Boats hull path, docked opacity unchanged.

## frontend/systems/airtrain.js
- Gray dashed `8 5`, weight 3. Stations use the commuter square; popup uses `.kv` headways and a scheduled `.fresh` footer.

## frontend/systems/buses.js
- Arrow 14px / dot 12px, paper stroke; popup mark `.sq` in route colour; rows Vehicle / Position.

## Tests (tests/e2e)
- Update selectors: legend `aside` → `header`, feed-strip buttons by `title`, alert strip by `aria-label="Service alerts"`.
- Add: bullet focus dims other routes; commuter train tag contains agency glyph and branch code; scheduled trains render outlined (`stroke` on the tag rect); station labels hidden below zoom 14; popup never overlaps the header (bounding-rect assertion).

## Out of scope / unchanged
- Stations side panel and its button behaviour; accessibility panel; backend; tile origin and CSP; feed polling and staleness thresholds.
