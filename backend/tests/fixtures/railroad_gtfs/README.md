# `railroad_gtfs/` fixture: the two railroad feeds' `routes.txt`

Added on `claude/railroad-route-colors`, when `/api/railroad-routes` began serving
`color` and `text_color`. Two files, one per system, because the two feeds do not
publish the same columns and that difference is part of what the tests pin.

**Copied from the live archives, probed 2026-09-18**
(`https://rrgtfsfeeds.s3.amazonaws.com/gtfslirr.zip` and `/gtfsmnr.zip`), which is
why the row counts, the route ids, the names and every hex value are what they are
rather than invented:

| | Rows | Header |
| --- | --- | --- |
| `lirr_routes.txt` | 13 | `route_id,route_long_name,route_type,route_color,route_text_color` |
| `mnr_routes.txt` | 6 | `route_id,agency_id,route_short_name,route_long_name,route_desc,route_type,route_url,route_color,route_text_color` |

Three properties of the real feeds that the tests read these files to assert:

1. **LIRR publishes no `route_short_name` COLUMN at all**, so `short_name` is `None`
   for all thirteen and the parser's `.get()` on an absent column is exercised for
   real rather than against a synthetic blank.
2. **The New Haven family shares one red.** Metro-North gives New Haven (3), New
   Canaan (4), Danbury (5) and Waterbury (6) the same `EE0034`, so a colour is not
   a route identifier. LIRR does the same more quietly: Ronkonkoma (4) and Greenport
   (13) share `A626AA`.
3. **Both feeds fill both colour columns on every route**, which is the fact the
   old parser comment denied. That is also why one departure from the live data is
   deliberate, below.

## The one deliberate departure: LIRR route 11

**`route_color` is blanked on Belmont Park (11); the live feed fills it**, with
`60269E`. Its `route_text_color` is left as published.

Blanking it is the only way to test the `None` branch against feeds that have no
blank anywhere, and leaving `text_color` filled is what makes the row worth having:
it is the one row where the two columns disagree, so a parser that read
`text_color` out of `route_color`'s column returns `None` here and the test catches
it. Nothing else in either file is altered.

`route_type` is `2` (rail) throughout, as published. Do not "fix" route 11 to match
the live feed without replacing what it tests.
