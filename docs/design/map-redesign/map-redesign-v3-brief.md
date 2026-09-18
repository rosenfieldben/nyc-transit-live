# Map redesign, v3 brief: reconcile with the freshness contract and the real route tables

Repo: `rosenfieldben/nyc-transit-live`. This brief amends the v2 handoff (`design_handoff_map_redesign`). Everything in v2 stands except where this document says otherwise. Two things changed underneath the design while it was being made: the app now renders freshness per observation rather than per feed, and the app already carries the real route names and colors for every railroad. The v3 pass reconciles the design with both.

---

## 1. What changed since v2 was drawn

The app now implements a freshness and provenance contract. Every served observation (a train position, an arrival prediction, an alert) carries two fields:

- `observed_at`: when the provider last actually observed it (null when the provider gives no per-observation clock, which is the case for Metro-North).
- `provenance`: one of exactly five values: `reported`, `estimated`, `placed`, `retained`, `unknown`.

Every surface that shows an observation either shows it qualified or does not show it. "Qualified" means a fixed vocabulary of rider-facing words, chosen once for every surface (map popup, station panel, status line). The words are not to be reworded; they can be restyled.

Two consequences for the map:

1. **Opacity is per observation, not per feed.** A train seen ten minutes ago is drawn dimmed inside a perfectly healthy feed, next to a full-opacity train seen ten seconds ago. v2's "stale feed: marker opacity 0.45" is the old per-feed rule and no longer describes the map.
2. **A train has more than two states.** v2 knows "Live GPS" and "Scheduled, no GPS." The contract renders four rider-visible states for a positioned train, plus retained and unknown. Section 3 gives the mapping.

---

## 2. The states, and the words a rider sees

Thresholds are constants in the app: `OBS_FRESH_S` = 90 seconds, `OBS_MAX_S` = 600 seconds. Ages are measured against the provider's own feed clock, not the wall clock.

For every positioned train, the app applies this ladder and takes the first step that holds:

| Step | Provenance | When | What the rider sees (exact words) |
| --- | --- | --- | --- |
| 1 | `reported`, unqualified | GPS observation within 90 s | nothing extra: full opacity, no qualifier |
| 2 | `estimated` | position is older than 90 s, but the train's own arrival prediction is within 90 s; the position is interpolated from the prediction | marker labeled **"estimated from a prediction"** |
| 3 | `reported`, qualified | GPS observation older than 90 s and within 600 s | marker **dimmed**, line reading **"as of {age} ago"** (age in the app's `humanizeAge` form: "2m", "8m", "1h 20m") |
| 4 | `placed` | no usable GPS; a prediction within 600 s names the next stop; drawn at that stop | the existing scheduled treatment, with the age line **"as of {age} ago"** when the prediction is older than 90 s |
| 5 | (absent) | nothing above holds | **no marker**. The count of withheld trains appears on the status line only, never on the map |

Two more values occur:

- `retained`: a carried-forward observation while the provider's feed is failing. Rider words: **"showing last known, as of {age} ago"**, dimmed. This is per observation now, so a retained train sits beside live ones during a partial outage.
- `unknown` (null `observed_at`) has two readings, decided by the provider's policy row:
  - In an age-gated system (LIRR, subway, PATH, ferry, buses, NJ Transit predictions): the marker reads **"age unknown"** and is treated as not fresh, so it can never be step 1; it takes step 2 if a fresh prediction exists, otherwise the qualified (dimmed) treatment.
  - In a non-gated system (Metro-North, whose vehicle timestamps copy the feed header and carry no signal): the marker is silent, drawn as live, and the status line carries the fact once as **"railroad: MNR position age unavailable"** (this clause never raises the status line on its own).

NJ Transit trains are all `placed` or `estimated` by construction (schedule-derived, no GPS), so the outlined form is their normal state; they never reach steps 1 or 3.

---

## 3. Provenance-to-tag mapping

This is the deliverable. Every marker family needs one treatment per state, shown in the marker study at 1x and 3x, light and dark.

### 3.1 Commuter rail tag (LIRR, Metro-North, NJ Transit)

| State | Tag body | Chevron / dot | Opacity | Line beneath the tag or in the popup |
| --- | --- | --- | --- | --- |
| `reported`, unqualified | **solid**: agency block ink, branch block in branch color | filled ink chevron (dot if heading unknown) | 1.0 | none |
| `reported`, qualified | solid, as above | filled | **dimmed** (the app's per-observation opacity; v2's 0.45 is a reasonable value) | "as of {age} ago" |
| `estimated` | **outlined** body (paper fill, ink stroke, branch stripe along the bottom) | **filled** ink chevron: the heading is real even though the position is inferred | 1.0 | "estimated from a prediction" |
| `placed` | outlined body | **outlined** chevron (paper fill, ink stroke), or outlined dot | 1.0 | "scheduled position, no GPS", plus "as of {age} ago" when the prediction is older than 90 s |
| `retained` | whatever the last state was | as last state | dimmed | "showing last known, as of {age} ago" |
| `unknown`, age-gated | outlined body | outlined dot (no heading is trusted) | dimmed | "age unknown" |
| `unknown`, Metro-North | solid (drawn as live by policy) | filled | 1.0 | none at the marker; the status line carries the clause |

The distinction the study must make visible at 1x: solid versus outlined body (position from GPS versus from a prediction), and filled versus outlined chevron (heading trusted versus not). Dimming carries age; the body carries source. A rider should be able to read "outlined body, filled chevron" as "we know where it is going but not exactly where it is."

### 3.2 Subway bullet

Subway positions are placed from trip updates, so most trains are `placed` with the group header as their clock; 98 of them in the reference capture join a vehicle timestamp and can be `reported`. Treatment: full-opacity bullet for unqualified; dimmed bullet with the age line for qualified or retained; no distinct outlined form is needed (there is no GPS-versus-schedule distinction to draw for a rider here). The absent state applies.

### 3.3 PATH diamond, ferry hull, bus arrow and dot

Same rule as the subway bullet: full opacity for unqualified, dimmed with the age line otherwise, absent when withheld. Ferry keeps the docked-boat opacity (0.55) as a separate, compounding rule.

### 3.4 Stations

Unchanged by the contract. A station marker carries no observation.

---

## 4. Popup vocabulary

The v2 popup's "Position" row ("Live GPS" | "Scheduled, no GPS" | "Placed from arrivals") and the "LIVE · UPDATED 12S AGO" footer are replaced by the contract's words. The app renders both surfaces through one helper, so the popup shows exactly what the station panel shows.

- The provenance line, one of: nothing (unqualified), "as of {age} ago", "estimated from a prediction", "scheduled position, no GPS", "showing last known, as of {age} ago", "age unknown". Style it as the `.kv` row named **Position**, value in the words above.
- The footer (`.fresh`) may keep its restyled form, but its text is the per-system freshness the app already computes, in the app's own words: live, "as of {age} ago", or "scheduled headways, no live feed". Do not add a second age to the popup; the row above is the observation's age, the footer is the feed's.
- Arrival rows on the station popup and panel already carry a per-row qualifier ("as of {age} ago" when a prediction is older than 90 s). The countdown still counts to the prediction; the qualifier sits beside it. The v2 `.arr` grid needs a fourth column or a second line for it.

---

## 5. Feed strip and status line

- The per-feed freshness dot (green live, accent stale, gray scheduled-only) is per system and stays. It reflects the feed, not the observations inside it; a feed can be green while a third of its trains are dimmed, and that is correct.
- The trailing note ("METRO-NORTH AS OF 6M AGO") is the app's status line, which now has three populations plus two clauses, all generated by one function. The note must display that text as given, not a re-derived subset. Examples of what it can say:
  - `railroad: MNR as of 6m ago`
  - `subway: content as of 10m ago (1-7+S)` (content old behind a fresh poll)
  - `LIRR: 24 not shown (last seen over 10m ago)` (the withheld count; this never turns the line red on its own)
  - `railroad: MNR position age unavailable` (never raises the line on its own)
  - nothing at all on a healthy day, which is the common case and must not get noisier.
- Hidden feeds: state must not be conveyed by opacity alone. Use `aria-pressed` on the button and a visible mark (a strike or an empty tick) in addition to the fade.

---

## 6. Data-source note: the route tables

v2's branch table is not the feed's. The app already serves the real ids and names, and the feeds publish their own colors. Build from these; do not hand-table.

### 6.1 LIRR, as served by `/api/railroad-routes` today (id, name)

```
1  Babylon Branch          7  Far Rockaway Branch
2  Hempstead Branch        8  West Hempstead Branch
3  Oyster Bay Branch       9  Port Washington Branch
4  Ronkonkoma Branch      10  Port Jefferson Branch
5  Montauk Branch         12  City Terminal Zone
6  Long Beach Branch      13  Greenport Service
```

There is no route 11. Route 12 (City Terminal Zone) and 13 (Greenport) appear in live feeds and need a code and a color. Proposed codes, keyed by name: BAB, HEM, OB, RON, MTK, LB, FR, WH, PW, PJ, CTZ, GRN.

### 6.2 Metro-North, as served today (id, name)

```
1  Hudson      4  New Canaan
2  Harlem      5  Danbury
3  New Haven   6  Waterbury
```

Six routes, not three. Proposed codes: HUD, HAR, NH, NC, DAN, WAT.

### 6.3 NJ Transit, from the feed's own `routes.txt` (id, short name, long name, color)

```
 1  ACRL   Atlantic City Rail Line     #075AAA
 2  MNBTN  Montclair-Boonton Line      #E66859
 5  BERG   Bergen County Line          #FFD411
 6  MAIN   Main Line                   #FFD411
 7  MNE    Morris & Essex Line         #08A652
 8  MNEG   Gladstone Branch            #A4C9AA
 9  NEC    Northeast Corridor          #DD3439
10  NJCL   North Jersey Coast Line     #03A3DF
13  PASC   Pascack Valley Line         #94219A
14  PRIN   Princeton Shuttle           #E87725
15  RARV   Raritan Valley Line         #F2A537
17  MRL    Meadowlands Rail Line       #C1AA72
```

These ids, names and colors are what `/api/njt-routes` serves, straight from NJ Transit. Use the feed's `route_short_name` as the branch code (it is already an abbreviation) and the feed's `route_color` and `route_text_color`. Route 17 runs only on event days and usually has no trips; a station or train naming a route the tables lack must fall back to the id and a neutral color rather than fail.

### 6.4 Colors for LIRR and Metro-North

Both agencies publish `route_color` in their GTFS `routes.txt`. `/api/railroad-routes` does not serve it yet; the implementation adds it (a small backend change, the same shape as NJ Transit's). Until then, treat LIRR and Metro-North colors as **to be read from the feed**, not chosen. The Babylon green in v2 (`#00985F`) is LIRR's published color, so the palette v2 reached for is the right one; only the id mapping was wrong.

### 6.5 Rule

Names, colors and codes come from the feed or from a table keyed by the feed's route **name**, never by a guessed id. The prototype's `RAIL` table is replaced by a lookup into what the app already loads.

---

## 7. Constraints carried over from the app's accessibility work

- Feed toggles: `aria-pressed`, keyboard reachable, hidden state not by opacity alone.
- Marker text at 8px is below readable size on a 1x display; the marker's accessible name must carry agency, branch, heading and state in words, and the study should show the tag at 1x on a non-retina rendering.
- The Key panel's SVG glyphs carry names, as the current legend's do.
- On phones (375 and 320 wide), the header must collapse: the subway key and the feed strip fold behind the Key button, leaving one row. The current legend collapses on phones and the axe specs run at those widths.
- One write to each live region per render; a qualifier appearing is announced once, its age counting up is not.
- The Stations panel and its disclosure button are out of scope and unchanged.

---

## 8. What v3 should deliver

1. The marker study extended to every row of the table in 3.1, for the rail tag, and to the dimmed and absent states for the other families, at 1x and 3x, light and dark.
2. The commuter train popup and the station popup redrawn with the section 4 vocabulary.
3. The feed strip and trailing note redrawn with the section 5 examples, including a healthy day that shows nothing.
4. The branch tables replaced by section 6's, with the codes.
5. A 375-wide and a 320-wide header state.

The v2 tokens, header, feed strip, subway ribbons and bullets, station styles, control stack and popup chrome are accepted as drawn.
