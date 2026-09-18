# How MR2's numbers were taken

Every count and every frame time in the MR2 pull request body and in the ledger came from
the real network, not from the hermetic fixture, because the fixture serves two shapes and
two stations and cannot answer a question about drawing a city.

The capture is not committed: it is 514 KB of geometry, eight times the largest fixture
this repository carries, and it would nearly double the repository's git size to make one
measurement reproducible. These two commands rebuild it, and `measure.spec.js` beside this
file is the harness, copied into `tests/e2e/` to run and deleted afterwards.

## 1. The archive, and the backend's own loaders

```sh
curl -sS -o gtfs_subway.zip https://rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip
```

Then, from `backend/`, with `static_data.SUBWAY_GTFS_ZIP` pointed at that file:

```py
import static_data
static_data.SUBWAY_GTFS_ZIP = "<path>/gtfs_subway.zip"
routes = static_data.load_subway_route_shapes()          # -> subway_routes_real.json
index = static_data.load_subway_station_routes()         # routes per parent station
# parent stations are stops.txt rows with location_type == "1"; join `index` onto them
#                                                          -> subway_stops_real.json
```

The loaders are the production ones, so the shapes are deduplicated exactly as the
endpoint serves them and the routes-per-station index is the one the popups use.

## 2. The harness

Copy `measure.spec.js` into `tests/e2e/`, put the two JSON captures in
`tests/e2e/fixtures/`, and run it. It serves the real payloads through the same
`installMocks` override seam every other spec uses, so nothing about the page differs from
a normal run except the size of the world.

It measures A/B **in one page**, so both samples share machine conditions: the tree as it
is, then a casing added under every line, then a permanent tooltip on every station, then
the same with the zoom gate applied, then with every label hidden. Frame times are
requestAnimationFrame deltas across four animated pans at City zoom, which is between 250
and 330 frames per sample.

Absolute frame times are a headless container's and mean nothing on their own. The
comparison between samples is the measurement.
