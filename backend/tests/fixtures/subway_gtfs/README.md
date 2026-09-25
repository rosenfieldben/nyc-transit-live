# `subway_gtfs/` fixture: the two members the station complex index reads

Added on `claude/subway-hub-definition`, when `transfers.txt` became a required member of
the subway static archive and `/api/subway-stops` began serving `complex_id`.

**Copied byte for byte from the live archive** (`https://rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip`,
`Last-Modified: Thu, 27 Aug 2026 13:39:18 GMT`, members dated 2026-07-31). That is the
publication production had promoted when the label-band diagnosis measured it on 2026-09-25:
the same 496 parent stations, in the same order, with the same names and the same routes per
station as production's `/api/subway-stops` served that day. Nothing here is invented.

| | Rows | What the tests read it for |
| --- | --- | --- |
| `stops.txt` | 1,488 | the 496 parent stations (`location_type` 1) the complex index is keyed by, and the platform-to-parent fold |
| `transfers.txt` | 613 | the 150 cross-stop rows that join stops into complexes; the other 463 are a stop to itself |

## What the live table looks like, measured

- **Every id in `transfers.txt` is a parent station.** 464 distinct ids, none a platform, so
  the loader's fold through `parent_station` is a guard rather than a translation.
- **Every cross-stop row has its reverse.** 150 rows, 75 unordered pairs, none one-way.
- **Every complex is fully meshed.** 35 complexes of two or more stops, the largest five
  (Times Square: `127`, `725`, `902`, `A27`, `R16`), and in every one of them every pair is
  listed directly. So on this publication a pairwise match and a closure give the same
  answer, and the union-find in `derive_subway_station_complexes` is exercised by a synthetic
  three-stop chain rather than by this file. It is there because the table's shape is the
  MTA's to change, and a chain is still one complex.
- **All 613 rows are `transfer_type` 2** (a minimum time is required), which the loader does
  not read: the complex is who is joined to whom, not how long the walk is.

## What is not here

`stop_times.txt` (36 MB) and `trips.txt`, so the routes per station are not derivable from
this directory. The e2e census fixture (`tests/e2e/fixtures/subway_stops_real.json`) carries
the routes as the real loader derived them from the full archive, and
`test_static_data.py::test_the_e2e_census_fixture_agrees_with_this_archive` holds its ids,
names and complex ids to this directory, so the two fixtures cannot drift apart silently.
