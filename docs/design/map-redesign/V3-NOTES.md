# v3 amendment to this handoff

The v2 spec in `README.md` stands except where `uploads/map-redesign-v3-brief.md` (the v3 brief) says otherwise. The v3 prototype is `Map Redesign v3.dc.html` and `Rail States Study v3.dc.html` at the project root.

What changed:
- Position is per observation. Read `provenance` + `observed_at` through `positionQualifier`; the Position row in the popup takes its words verbatim, the footer takes the feed's freshness. Drop v2's "stale feed: opacity 0.45" and "Live GPS | Scheduled, no GPS | Placed from arrivals".
- Commuter tag states per brief 3.1: solid/outlined body = GPS/prediction; filled/outlined chevron (or dot) = heading trusted or not; dimming = age. `railroadHollow` and `drawnFromPrediction` already decide body and glide; add the chevron decision beside them.
- Route tables: delete the prototype's `RAIL` table. Codes come from a name-keyed map (BAB, HEM, OB, RON, MTK, LB, FR, WH, PW, PJ, CTZ, GRN; HUD, HAR, NH, NC, DAN, WAT); NJ Transit uses the feed's `route_short_name`, `route_color`, `route_text_color`. `/api/railroad-routes` needs `route_color` added (backend, same shape as NJT).
- Feed strip: `aria-pressed`, strike + OFF mark when hidden. Trailing note = `staleness()` output verbatim; empty on a healthy day.
- Header folds behind Key below 700px (`#legend-toggle` behaviour, extended to the subway key and the feed strip).
- Marker accessible names carry agency, branch, heading and position words (`railroadTrainName` + `positionClause`).
- Basemap: the prototype loads Esri Light Gray Canvas tiles only because the preview origin is blocked by tile.openstreetmap.org (and CARTO now requires a key). Ship on the app's existing OSM layer; the cream/desaturated look is the `.leaflet-tile-pane` CSS filter, provider-independent.

## v3.1
- Service alerts never fold on phones. The strip has no `hdr-fold`; below 700px it stacks under the one-row header at full width (`100vw - 24px`).
- Words come from the app. `posState()` and `statusLine()` in the prototype are ILLUSTRATIVE: the implementation calls `positionQualifier()` for the Position row and `staleness()` for the status line and styles their output, never re-derives it. Only the mapping of provenance + age to tag body, chevron and dimming is taken from `posState()`.
- NJ Transit heading comes from the served direction passed to `bearingAlong`; there is no headsign rule.
