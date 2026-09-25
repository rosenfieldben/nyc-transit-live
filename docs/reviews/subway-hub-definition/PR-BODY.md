# Subway hubs: a hub is a station complex, not a stop

Backend and frontend, the ruling that followed the label-band diagnosis of 2026-09-25 and
three more the operator gave after reading the first record:

| commit | |
| --- | --- |
| `92636bc` | backend: `transfers.txt` required, `complex_id` on `/api/subway-stops` |
| `ff030f0` | frontend: the predicate reads the complex; ring, name and kicker follow |
| `3ff5c49` | the adversarial review's fixes (findings H1, H3, H4, H6, H7, H10) |
| `d4cefee` | the first record |
| `fcbce7e` | **ruling 1**: one name per complex |
| `b786b4f` | **ruling 2**: a floor of one cross-stop row; the monitor names every member |
| `0c17079` | **ruling 3**: the Key row reads "Transfer station: change between lines here" |
| this record | the census, the grid and the screenshots re-run at `0c17079` |

Nothing touches `njt_auth.py` or any credentialed path, so **no NJ Transit mint was spent**.

**A concurrent branch shares five of these files.** `claude/bus-zoom-rule` (another session,
open at the same time) touches `frontend/helpers.js`, `frontend/index.html`,
`frontend/systems/shared.js`, `tests/e2e/fixtures/mr_pins.json` and
`docs/reviews/map-redesign-rounds.md`. `git merge-tree` reports a clean merge of the two tips,
and the merged tree's shared suites are recorded under Gates. The one semantic meeting point:
that branch lands the map at the City preset, zoom 13, which is this branch's hubs band, so
the first view a rider sees carries the 48 complex names.

## The finding

On the deployed build at zoom 13 with Names pressed, the map named 96 St, 86 St, 72 St,
Carroll St, 25 St and 36 St, in a band that shows hubs only. The diagnosis measured three
candidate causes against production and the hermetic fixtures and ruled all three out: the
band's thresholds were MR2's (12 and 14), the Names toggle only ever hides, and the hasHubs
fallback was not engaged (all 496 stations list routes). **Every one of those names was a hub
under F9's rule.** The band, the toggle and the data were all correct; the definition was
inverted.

F9 counted trunks **per stop_id**. Measured on production's own `/api/subway-stops`, grouped
by `transfers.txt`:

| | |
| --- | --- |
| rings under F9 | **124** |
| of those, on a stop `transfers.txt` joins to no other | **108**: the B beside the C on Central Park West, the F beside the G at Carroll St, the D beside the R on Fourth Avenue, the 5 beside the 2 up White Plains Road. Shared track, not interchanges. |
| real complexes (two or more stops) | 35 |
| of those, with no ring at all | **22**, Times Square among them, because each of its five stops carries exactly one trunk. So do Grand Central, Union Square, Herald Square, Fulton St, Canal St and Broadway Junction. |

The diagnosis first said 102 and 26, grouping stops within 250 m; these are the same counts
over the MTA's own table.

## The ruling

**A hub is a station complex.** `transfers.txt` becomes a required member of the subway
static archive, by the visible-loss rule PR 116 applied to `stop_times.txt`, because three
rider-visible consumers read it: the transfer ring, the hub label class, and the MR5 kicker.
The backend derives `complex_id` per stop from its cross-stop rows (union-find over the
pairs; a stop in no row is its own complex) and serves it on `/api/subway-stops`, additive,
`None` by default. The frontend's predicate reads the trunks served across every stop in the
complex: **a hub is a complex serving three or more trunks, or two or more trunks across two
or more stops.** The ring and the label class read the same predicate; the kicker lists the
routes of the complex, not the stop. The station alerts join is untouched.

## Three more rulings, after the first record

1. **One name per complex.** The ring on every stop; the label only on the stop whose id is
   the complex id, in its own name. No complex draws two labels; the census stays at 48.
2. **A floor under `transfers.txt`.** A table with zero cross-stop rows fails the load (the
   live archive has 150; the floor is one), the content checked and not only the presence, as
   the bus probe checks its capture. The monitor's subway file list names every required
   member.
3. **The Key row reads "Transfer station: change between lines here."** P1e moves by that
   wording, recorded.

## The change

**1. `transfers.txt` joins `_REQUIRED_MEMBERS`**, so a publication without it fails the load
through `require_members`, the group reports `failed`, `HEALTH_SUBWAY_STATIC_FAILED` fires and
the last-known-good stays: the F1 chain, unchanged, with one more member in it. The comment
above the tuple names the three consumers and, while it was open, the **fourth consumer of the
routes index** that F1's list of three had missed: the MR5 kicker, which draws the routes
calling at a station and is empty without them.

**2. `load_subway_station_complexes`** reads the cross-stop rows (a row from a stop to itself
is a minimum transfer time, not a complex, and is dropped), folds any platform id to its
parent, and closes the pairs by union-find. The complex id is the **smallest station id in
the complex**, so it is stable across loads and is always a real station (Times Square is
`127`). A stop in no row is alone, by its own id. An id that is no station can still link two
that are, and is never a key. It **raises** rather than swallowing, like the routes loader
beside it, and the warmup assigns both only after the whole attempt succeeds.

**3. `complex_id` on `SubwayStop`**, `str | None = None`, additive for the reason
`RailroadRoute.color` gave: the payload is cached for an hour, and a client holding
yesterday's must not fail on a field the server has only just begun to send. `None` means no
index is loaded, which the endpoint keeps apart from a complex of one. The field-set lock is
updated, not relaxed.

**4. `isTransferStation(complex)`** takes `{routes, stops}`, the complex's routes unioned and
its stop count. `subwayComplexIndex` builds one shared object per complex from the payload,
`loadStations` keeps it on each registry entry, and the ring, the hub label class, the theme
repaint, `paintZoomBand`'s hub count and D2j's oracle all ask that object. A stop only counts
toward "two or more stops" if something calls there, so a closed sibling platform cannot ring
the shared-track stop beside it.

**A bare routes list names no complex, and gets F9's per-stop answer.** That is what PATH's
`[]` is (no routes, so a dot either way), what every caller written before complexes hands in,
and what a payload without `complex_id` is. The last is real: `/api/subway-stops` is served
`max-age=3600` while the scripts revalidate, so for an hour after the deploy a returning rider
runs the new code on yesterday's payload. The first version read that payload as "every stop
alone", and the review measured the result (finding H1): 14 rings, and no subway name in Manhattan
at zoom 12 or 13 with Names pressed. Read as F9's answer, it draws yesterday's map until the
next fetch (**D2i5**). The backend keeps the same distinction on the wire: `complex_id` `None`
is "no index loaded", never "a complex of one".

**5. The kicker** lists the complex's routes through `stationKickerRoutes`, **with the stop's
own first**: R3's cap shows three marks before its `+N`, and a rider who clicked the 7's dot
should see the 7 among them. A stop alone lists exactly its own routes, so every other board
is unchanged to the byte.

**6. The alerts join stays on the stop's own routes.** An alert for the 1 belongs on the 1's
platform. The registry keeps `routes` beside the new `complex` for exactly that reader, and
for the station panel.

**7. The two stale comments** the diagnosis found, near `LABEL_NO_HUB_ZOOM` and above
`namesToggleAnnouncement`, said `stop_times.txt` was not a required member and
`load_subway_station_routes` returned `{}`. Both now say it raises, and that the backend half
has landed.

**8. One name per complex (ruling 1).** `stationNamesItself(stationId, complex)` says yes for
the stop whose id is the complex id and for any stop whose complex is not known, and
`loadStations` binds a name only where it says yes. The other stops of a complex keep their
ring, their popup and their kicker, and draw no name. Times Square is named once, "Times
Sq-42 St" on `127`; the Port Authority stop `A27` is ringed and unnamed. At zoom 14, where the
band draws every name there is, the real payload draws 444 names for 444 complexes.

**9. The floor (ruling 2).** `validate_subway_archive` runs the loader's own transfer parser,
split out as the stream parser `_parse_transfer_rows` the way `_parse_stops_rows` was, through
`require_parsed`, the gate `stops.txt` already has. Headers only, self rows only, renamed
columns, or only "no transfer possible" rows: each is refused, and the error names
`transfers.txt` for `/api/status`. The committed 613-row live table passes it with 150 pairs.
`stop_times.txt` keeps PR 116's presence-only rule, which the ruling did not reach. **And the
monitor's `SUBWAY_REQUIRED_MEMBERS`**, which stayed `("stops.txt", "shapes.txt")` through PR 116,
now names every member `static_data._REQUIRED_MEMBERS` does, and a test holds both to one
hand-written list. The ruling said "all four"; the app requires five (`stops`, `shapes`,
`trips`, `stop_times`, `transfers`), and the monitor names all five.

**10. The Key (ruling 3).** "Subway transfer station: two or more route lines meet (click for
arrivals)" was F9's rule in words, and 95 stops where two lines meet at one platform now draw a
dot. The row reads **"Transfer station: change between lines here"**. P1e's golden moves by
that one line of `legend/names`, regenerated for that key alone; `keyglyphs.test.js` finds the
row by its new words. **A1x did not move**: the ruling named it, and it was run, but it
measures each Key row's ink and the row count rather than the words, so it passes unchanged.

## One reading the ruling's words allow, measured

"Two or more trunks across two or more stops" could mean the union of the complex's trunks,
or that the trunks must come from different stops. On the live table the two readings differ
at exactly three complexes: 149 St-Hostos (222 on the 2/5, 415 on the 4), 62 St / New Utrecht
Av (the D/R/W and the N/W) and Delancey St-Essex St (the F and the J/M/Z). All three are real
interchanges under either reading, so the union is implemented and nothing turns on it today.

## The census

On production's payload, **124 rings under F9 become 100 stops in 48 complexes**, all 48
pinned by name in `subway.spec.js` **D2i1**. Since ruling 1 each complex is **named once**, so
the hubs band draws 48 names, one per row below, on the stop whose id is the complex id (the
id in brackets):

| complex | stops | routes |
| --- | --- | --- |
| **168 St-Washington Hts / 168 St** (`112`) | 2 | 1 A C |
| **59 St-Columbus Circle** (`125`) | 2 | 1 2 A B C D |
| **Times Sq-42 St / 42 St-Port Authority Bus Terminal** (`127`) | 5 | 1 2 3 7 7X GS A C E N Q R W |
| **14 St / 6 Av** (`132`) | 3 | 1 2 3 F FX M L |
| **149 St-Hostos** (`222`) | 2 | 2 5 4 |
| **Park Place / Chambers St / World Trade Center / Cortlandt St** (`228`) | 4 | 2 3 A C E N R W |
| **Fulton St** (`229`) | 4 | 2 3 4 5 A C J Z |
| **Borough Hall / Court St** (`232`) | 3 | 2 3 4 5 N R W |
| **Atlantic Av-Barclays Ctr** (`235`) | 3 | 2 3 4 5 B Q D N R W |
| **Franklin Av-Medgar Evers College / Botanic Garden** (`239`) | 2 | 2 3 4 5 FS |
| **Junius St / Livonia Av** (`254`) | 2 | 2 3 4 5 L |
| **161 St-Yankee Stadium** (`414`) | 2 | 4 B D |
| **59 St / Lexington Av/63 St / Lexington Av/59 St** (`629`) | 3 | 4 5 6 6X F M N Q R W |
| **51 St / Lexington Av/53 St** (`630`) | 2 | 4 6 6X E F FX |
| **Grand Central-42 St** (`631`) | 3 | 4 5 6 6X 7 7X GS |
| **14 St-Union Sq** (`635`) | 3 | 4 5 6 6X L N Q R W |
| **Bleecker St / Broadway-Lafayette St** (`637`) | 2 | 4 6 6X B D F FX M |
| **Canal St** (`639`) | 4 | 4 6 6X J Z N Q R W |
| **Brooklyn Bridge-City Hall / Chambers St** (`640`) | 2 | 4 5 6 6X J Z |
| **74 St-Broadway / Jackson Hts-Roosevelt Av** (`710`) | 2 | 7 7X E F FX M R |
| **Queensboro Plaza** (`718`) | 2 | 7 7X N W |
| **Court Sq / Court Sq-23 St** (`719`) | 3 | 7 7X E F FX G |
| **5 Av / 42 St-Bryant Pk** (`724`) | 2 | 7 7X B D F FX M |
| **145 St** (`A12`) | 2 | A C B D |
| **14 St / 8 Av** (`A31`) | 2 | A C E L |
| **W 4 St-Wash Sq** (`A32`) | 2 | A C E B D F FX M |
| **Jay St-MetroTech** (`A41`) | 2 | A C F FX N R W |
| **Franklin Av** (`A45`) | 2 | A C FS |
| **Broadway Junction** (`A51`) | 3 | A C J Z L |
| **62 St / New Utrecht Av** (`B16`) | 2 | D R W N |
| **34 St-Herald Sq** (`D17`) | 2 | B D F FX M N Q R W |
| **Prospect Park** (`D26`) | 1 | B FS Q |
| **Delancey St-Essex St** (`F15`) | 2 | F FX J M Z |
| **4 Av-9 St** (`F23`) | 2 | F G D N R W |
| **Forest Hills-71 Av** (`G08`) | 1 | E F FX M R |
| **67 Av** (`G09`) | 1 | E F M R |
| **63 Dr-Rego Park** (`G10`) | 1 | E F M R |
| **Woodhaven Blvd** (`G11`) | 1 | E F M R |
| **Grand Av-Newtown** (`G12`) | 1 | E F M R |
| **Elmhurst Av** (`G13`) | 1 | E F M R |
| **65 St** (`G15`) | 1 | E F M R |
| **Northern Blvd** (`G16`) | 1 | E F M R |
| **46 St** (`G18`) | 1 | E F M R |
| **Steinway St** (`G19`) | 1 | E F M R |
| **36 St** (`G20`) | 1 | E F M R |
| **Queens Plaza** (`G21`) | 1 | E F FX R |
| **Metropolitan Av / Lorimer St** (`G29`) | 2 | G L |
| **Myrtle-Wyckoff Avs** (`L17`) | 2 | L M |

Ten of the 48 are single Queens Boulevard stops (36 St, 46 St, Steinway St, Northern Blvd, 65 St,
Elmhurst Av, Grand Av-Newtown, Woodhaven Blvd, 63 Dr-Rego Park, 67 Av): the E, the F/M and the
R at one stop, three trunks, because the E and the F run local there at night. That is the
ruling's three-trunk clause as written, and it names 36 St G20 as a hub on purpose. Forest
Hills-71 Av, Queens Plaza and Prospect Park are the other three single-stop hubs.

## Before and after, the diagnosis's own grid

The diagnosis's scripts, rerun unchanged except that the oracle reads the complex. Each cell
is band, names drawn / names drawn with the hub class, and the City preset's in-view count.
"Before" is the deployed build (`d49e9a7`) against production; "after" is this branch at
`0c17079` against the same payload plus `complex_id`, through the hermetic mocks.

| zoom | Names | before: deployed, production | after: `0c17079`, same payload |
| --- | --- | --- | --- |
| 11 | on | none, 0 / 0, 0 in view | none, 0 / 0, 0 in view |
| 12 | on | hubs, **124 / 124**, 88 in view | hubs, **48 / 48**, 44 in view |
| 13 | on | hubs, **124 / 124**, 25 in view | hubs, **48 / 48**, 27 in view |
| 14 | on | all, 496 / 124, 76 in view | all, **444 / 48**, 55 in view |
| 15 | on | all, 496 / 124, 36 in view | all, 444 / 48, 25 in view |
| 11 to 15 | off | the same bands, 0 / 0 | the same bands, 0 / 0 |

`stationLabelShown`, D2j's oracle, disagreed with the page 0 times in every cell of both
columns, and no local name is drawn in either hubs band. 444 is the number of complexes: 496
stops, 52 of them siblings of a named stop, each ringed or dotted as before and unnamed. The
other hermetic worlds are unchanged: the stock fixture (two one-trunk stations, no hub) still
shows every name from 13, D2j's world still has its one hub at Fulton St (three trunks at one
stop), and the `routes: []` world still reads F1's degraded band (every name from 13, one per
complex).

**One transition world, measured because it is real for an hour.** A browser holding the
pre-deploy payload runs the new frontend on stops with no `complex_id`. At `0c17079` that draws
exactly the "before" column: hubs, 124 / 124 at 12 and 13, 88 and 25 in view at the City
preset, 496 names at 14, the oracle agreeing in every cell. At `ff030f0` it drew 14 / 14, which
is review finding H1 and the reason for the change above.

## What a rider sees

Zoom 13, Names on, production's payload through the hermetic mocks, 1280 wide. Left is
`d49e9a7`, right is this branch at `0c17079`.

| before | after |
| --- | --- |
| ![Midtown at zoom 13, before](before-midtown-13.png) | ![Midtown at zoom 13, after](after-midtown-13.png) |
| ![Downtown at zoom 13, before](before-downtown-13.png) | ![Downtown at zoom 13, after](after-downtown-13.png) |

Before: 72 St and 81 St on Central Park West named, 7 Av and 5 Av/53 St named, and Times
Square, Grand Central, Union Square, Herald Square, Canal St and Fulton St drawn as plain dots
with no name. After: the reverse, which is the first ruling, and each of those stations named
once, which is ruling 1.

**What ruling 1 measured away.** At `3ff5c49`, with a name per stop, 32 of the 100 hub names
repeated another stop of the same complex ("Times Sq-42 St" four times, "Fulton St" four,
"Canal St" four) and overprinted at 12 and 13, and the City preset's zoom 13 carried 67 hub
names in view. Now it is 27, none repeated. What remains is ordinary label collision between
two DIFFERENT complexes that sit close together (Park Place against Brooklyn Bridge-City Hall
downtown), which is the density MR2 measured for every zoom and not something a complex rule
can decide.

## Tests

Hermetic throughout except the one contract-tier scenario, which runs the real app against
the simulator. Nothing needs the network or a credential.

**The committed archive is new.** The subway had none: its static tests built synthetic zips.
`backend/tests/fixtures/subway_gtfs/` now carries `stops.txt` and `transfers.txt` **copied byte
for byte from the live publication** (Last-Modified 2026-08-27), which is the one production
had promoted on the day of the diagnosis, with a README recording what the table looks like:
every id a parent station, every cross row reversed, all 35 complexes fully meshed. The e2e
census reads `tests/e2e/fixtures/subway_stops_real.json`, production's payload of that day
plus `complex_id` from the branch's real loaders, and a backend test holds it to the
committed archive so the two fixtures cannot drift apart silently.

| Claim | Test |
| --- | --- |
| **the complex table on the real publication**: Times Square's five stops (`127`, `725`, `902`, `A27`, `R16`) are one complex, `127`; Court Square's three are one; a stop in no row is alone by its own id; the diagnosis's shared-track stops are alone | `test_static_data.py::test_the_committed_archive_complex_table` |
| **the stop count is unchanged**: all 496 parent stations are keyed and nothing else is, 444 complexes, 35 of more than one stop, the largest five | the same test |
| **a zip lacking `transfers.txt` fails the load**, at the validator and end to end (cache rejected, redownload rejected, load raises) | the `[transfers.txt]` cases of F1's `test_validate_rejects_an_archive_without_the_station_routes_tables` and `test_a_reduced_publication_fails_the_load_rather_than_serving_an_empty_index` |
| the loader raises rather than serving every stop alone | `test_load_subway_station_complexes_raises_without_transfers` |
| **the union-find**: A-B and B-C with A-C unlisted is one complex | `test_a_three_stop_chain_is_one_complex` |
| a self row joins nothing; a non-station id links the closure and is never a key; platform ids fold to parents | `test_a_stop_in_no_row_is_its_own_complex_and_self_rows_join_nothing`, `test_a_non_station_id_links_the_closure_but_is_never_a_key` |
| the census fixture agrees with the committed archive: ids, order, names, complex ids | `test_the_e2e_census_fixture_agrees_with_this_archive` |
| the endpoint serves `complex_id`; `None` with no index loaded; five stops under one id | `test_api.py::test_subway_stops_lists_stations`, `..._complex_id_is_none_when_no_index_is_loaded`, `..._serves_every_stop_of_a_complex_under_one_id` |
| the warmup wires the index, a raise fails the group, and a failed reload keeps both indexes | `test_subway_static_warmup_loading_to_ready`, `test_subway_static_failed_complex_load_fails_and_keeps_the_previous_index` |
| the declared required set moved deliberately | `test_static_shared.py::test_required_member_set_is_exactly_what_is_declared[subway]`, and `test_validator_rejects_an_archive_missing_a_required_member[subway-transfers.txt]` |
| the field-set lock, updated not relaxed | `test_models.py::test_subway_stop_field_set_is_locked` |
| through the real warmup at the contract tier: Times Square's three stops in the sim join into `127` | `test_contract_api.py::test_the_station_complexes_ride_the_real_warmup_to_the_stops_endpoint` |
| **node: the predicate over the diagnosis's table**: 96 St, 86 St and 72 St on Central Park West, Carroll St, 25 St and 36 St R36 local; 36 St G20 a hub; Times Square and Court Square hubs, every stop | `subway.test.js` "hub definition: the diagnosis's stations, asked of their complexes" |
| node: the rule itself, both clauses, the stop-count edge cases, and the census (124 before, 100 in 48 after) | "hub definition: a complex is a ring at three trunks...", "...the census over the real payload, before and after" |
| node: `subwayComplexIndex` and `stationKickerRoutes` | "...groups by complex_id and leaves a missing one out", "...the kicker lists the complex's routes, the stop's own first" |
| **e2e: the ring census before and after**, measured in one page on the drawn rings: 124 by F9's rule, 100 by the complex rule, the 48 complexes by name, rings and hub labels the same stations, all five of Times Square ringed | `subway.spec.js` **D2i1** |
| **e2e: the band at zoom 13 over the Upper West Side draws no local**, anywhere; 96 St, 86 St and 72 St in view and not drawn | **D2i2** |
| **e2e: the kicker at Times Square lists every trunk**: from the 7's platform, all 13 routes of the complex, five trunks, the 7 and 7X first, three plates and `+10` | **D2i3** |
| e2e: the alerts join still seeds from the stop's own routes: on the 7's platform the 7's alert and not the 1's, although the kicker names the 1; on the 1's platform the reverse | **D2i4** |
| e2e: **a theme swap repaints the same hundred rings** (review finding H3) | **D2i1**, after the census |
| e2e: **a payload with no `complex_id` draws F9's map**, 124 rings, and the band still has hubs at the City preset (H1) | **D2i5** |
| node: a closed sibling platform does not make a complex two stops (H4) | "hub definition: a stop nothing calls at does not make a complex two stops" |
| backend: transfer types 3, 4 and 5 join nothing, a blank type joins (H7) | `test_a_transfer_type_that_says_no_change_joins_nothing` |
| backend: the complex id is the smallest STATION id, whatever the row order and whatever non-station id links the complex (H10) | `test_the_complex_id_is_the_smallest_station_in_it_whatever_the_order` |
| **ruling 1: no complex draws two names**, at zoom 14 where every name is drawn: 444 names for 444 complexes, each on the stop whose id is the complex id, in its own words; `A27` ringed and unnamed | **D2i6** |
| ruling 1: the census stays at 48, one hub name per hub complex, every one on a ringed stop | **D2i1**; **D2i2** draws 48 at zoom 13 |
| ruling 1, node: over the real payload one stop per complex names itself, 48 of them hubs; with no complex known every stop does | "hub definition: one name per complex, on the stop whose id is the complex id" |
| **ruling 2: zero cross-stop rows fail the load**: headers only, self rows only, renamed columns, only type 3 | `test_validate_rejects_a_transfers_table_with_no_cross_stop_row`, four cases |
| ruling 2: the same end to end, cache and redownload both refused | `test_a_publication_whose_transfers_say_nothing_fails_the_load` |
| ruling 2: one pair passes, and so does the committed live table, with its 150 pairs | `test_validate_accepts_one_cross_stop_row_and_the_live_table` |
| **ruling 2: the monitor names every required member**, each missing one a FAIL naming it, and the monitor's list equal to the app's | `test_contract_monitor.py::test_subway_static_missing_required_member_is_fail`, five cases, and `test_the_monitor_requires_what_the_app_requires` |
| **ruling 3: the Key row's words** | `pins.spec.js` **P1e** (moved by one line) and `keyglyphs.test.js` test 7 |
| **F11's alerts pin holds** | `pins.spec.js` **P3c**, its golden untouched |

**Three node tests rewritten rather than deleted**, because the rule they asserted changed:
"one trunk is a local dot and two or more is a transfer ring" became the complex rule's test,
F9's pairs test keeps its one-trunk half and moves its two-trunk examples across two stops,
and the label test gains the complex forms. **P2a's golden moves by one field**, `complex`,
and against `main` the change to `mr_pins.json` is two lines: `"complex": null` in P2a's
registry entry (the stock world names no complex, and its entry says so), and P1e's one row of
`legend/names`, moved by ruling 3's wording. Nothing else in the golden moved.
D2i's title and D2's fixture comment now say three trunks at one stop, which is what makes
Fulton St its world's one hub.

## Mutations, each run and recorded

Each in a real `git worktree` detached at the commit under test, applied alone, the named tiers
run against it, and the tree verified clean before and after. The four the ruling named ran at
`ff030f0`, again at `3ff5c49` with the review's guards added, and **all sixteen at `0c17079`**,
which is the table.

| # | Guard reverted | Result | Killed by |
| --- | --- | --- | --- |
| M1 | the predicate counting per stop again (F9's `trunks >= 2`, the stop count ignored) | **killed** | node: **the diagnosis's stations** (96 St first) and seven more; e2e **D2i1**, **D2i2** |
| M1b | the same at the draw's call site: the ring and the label handed the stop's own routes | **killed** | e2e **D2i1**, **D2i2**, **D2i6**. Node cannot see a call site, which is why the e2e tier exists |
| M2 | `transfers.txt` removed from the required tuple | **killed** | the declared-set gate and `test_the_monitor_requires_what_the_app_requires`. At `ff030f0` the missing-member cases killed it too; since ruling 2 the floor's own parser also refuses a missing `transfers.txt`, so those cases no longer tell the two apart and the declared-set gate is what does |
| M3 | the kicker reading the stop's routes | **killed** | e2e **D2i3**, and **D2i4**'s premise |
| M4 | union-find dropped for pairwise only (each station's own partners) | **killed** | **`test_a_three_stop_chain_is_one_complex`**, the non-station link test, and the H7 and H10 tests |
| M5 | H1's fallback removed: a bare routes list read as a stop alone | **killed** | e2e **D2i5**; node, three tests |
| M6 | the theme repaint asking the stop's routes | **killed** | e2e **D2i1**'s theme swap. At `ff030f0` this survived the whole suite, which is review finding H3 |
| M7 | every stop counted toward "two stops", routed or not | **killed** | node, the H4 test |
| M8 | the transfer-type filter removed | **killed** | the H7 test, and the floor's `no-transfer-possible-only` case |
| M9 | the complex id as the first station stops.txt lists | **killed** | `test_the_complex_id_is_the_smallest_station_in_it_whatever_the_order` |
| M9b | the complex id as the union-find root | **killed** | the same test, through the lower-sorting non-station id |
| M10 | **ruling 1 at the call site**: a name bound on every stop | **killed** | e2e **D2i6**, **D2i1**, **D2i2** |
| M10b | **ruling 1 in the helper**: `stationNamesItself` always yes | **killed** | node, the one-name test; e2e **D2i6** |
| M11 | **ruling 2's floor removed** from `validate_subway_archive` | **killed** | the four no-cross-row cases and the end-to-end load test |
| M12 | **ruling 2's monitor list** without `transfers.txt` | **killed** | `test_subway_static_missing_required_member_is_fail[transfers.txt]` and the equality test |
| M13 | **ruling 3's words** reverted to F9's | **killed** | `pins.spec.js` **P1e**; `keyglyphs.test.js` test 7 |

**What M1 did not kill at `ff030f0`, measured, and said so rather than hidden: D2j.** D2j's
oracle and the page share `isTransferStation`, so a mutation to the rule moves both sides,
which is the limit D2j's own comment already records. The node tier and D2i1 are what pin
the rule.

**What M4 does not kill: the committed-archive table.** The live table is fully meshed, so a
pairwise match gives the right answer on it. That is recorded in the fixture's README, and it
is why the synthetic chain exists.

## The adversarial review

One round, over `92636bc..ff030f0`, run as the repository's `adversarial-review` workflow in
its own worktree: five finders (silent failure, removed behaviour, boundary and type, test
vacuity, operator reality), a triage that merged 17 candidates into 10, and batched verifiers.
**Nine confirmed, one refuted.** Six are fixed in `3ff5c49`; the other three went to the
operator and are fixed on the rulings in `b786b4f` and `0c17079`. The
findings are numbered H1 to H10 here, because this record already cites MR2's F1 and F9 and
the review's own F-numbers collided with them.

| # | Severity | Finding | Disposition |
| --- | --- | --- | --- |
| H1 | medium | A payload with no `complex_id` (a browser's cached copy for the hour after a deploy) read as "every stop alone": 14 rings, no subway name in Manhattan at 12 or 13, Names pressed | **Fixed.** A bare list gets F9's answer; **D2i5** |
| H2 | medium | A `transfers.txt` that is present with no cross-stop rows loads as every stop alone under `ready`, and the comment claimed the requirement prevented that | **Fixed on ruling 2** (`b786b4f`): a floor of one cross-stop row; four shapes of "says nothing" refused |
| H3 | medium | No test repainted the rings on a theme swap where complex and stop disagree; the repaint mutated to per-stop survived | **Fixed.** D2i1 swaps to dark; M6 now dies |
| H4 | low | A stop with no service counted toward "two or more stops" | **Fixed**, node test, M7 |
| H5 | low | The Key still reads "Subway transfer station: two or more route lines meet", and 95 stops where two lines meet at one stop now draw a dot | **Fixed on ruling 3** (`0c17079`): "Transfer station: change between lines here" |
| H6 | low | The two corrected comments overclaimed: an absent `stop_times.txt` fails the load, a header-only one does not | **Fixed** |
| H7 | low | `transfer_type` 3 (no transfer possible) and 4/5 (in-seat) joined stops | **Fixed**, test, M8 |
| H8 | low | The contract monitor's `SUBWAY_REQUIRED_MEMBERS` is still `("stops.txt", "shapes.txt")`, so its drift check passes a publication production now refuses | **Fixed on ruling 2** (`b786b4f`): the monitor names every member, held equal to the app's |
| H9 | low | "Nothing covers paintZoomBand's hub count reading the complex" | **Refuted**: its premise that no e2e world carries `complex_id` is false (D2i1's does) |
| H10 | low | Nothing held "the smallest station id" | **Fixed**, test, M9 and M9b |

### What went to the operator, and what came back

The first record left three decisions open: the repeated names, how deep "required" goes
(H2 and H8), and the Key's words (H5). The operator ruled on all three, and they are the
three rulings at the top of this body, delivered in `fcbce7e`, `b786b4f` and `0c17079`. Nothing
from the review is open.

## Gates

At `0c17079`, the code tip, from the worktree, with the credentials scrubbed. This record's
commit adds documents and screenshots only.

| Gate | |
| --- | --- |
| `pytest` (backend) | **1763** passed, from 1738 on `main` at `d49e9a7` |
| `ruff check`, `ruff format --check` | clean, 79 files |
| `mypy` | clean, 30 source files |
| node tier, `node --test "frontend/*.test.js" "tests/*.test.js"` | **403** passed |
| hermetic e2e, the full suite | **324** passed, two workers |
| contract-tier lint and format | clean |
| contract API tier | **39** passed (38 before, plus the complex scenario) |
| contract browser tier | **5** passed |
| `run_all.sh` | **15 of 15** |

**And the merge with the concurrent branch, tested rather than assumed.** `git merge-tree`
of `0c17079` and `claude/bus-zoom-rule` at `6b447c2` is clean. The merged tree, committed as
`18dab93` on no branch (a `commit-tree` object checked out in a scratch worktree, so no ref
moved), passes backend **1763**, node **406** and the full hermetic e2e **354**. The two
branches share `frontend/helpers.js`, `frontend/index.html`, `frontend/systems/shared.js`,
`tests/e2e/fixtures/mr_pins.json` and `docs/reviews/map-redesign-rounds.md`, and each side's
edits are additive to the other's. If that branch moves again before either merges, the
second to merge should rerun this.

**How they were run, because another session was working in the same repository at the same
time.**

- **Every run was in `/Users/benjaminrosenfield/nyc-transit-live-hubs`**, a worktree of its
  own. The first minutes of this branch were not: a `git switch` in the main checkout moved
  another session's HEAD, and its commit `8e30014` landed on this branch. It was moved back to
  `claude/bus-zoom-rule` with `git reset --keep`, this branch was reset to `d49e9a7`, and that
  session confirmed its tree intact. Nothing of either branch is in the other.
- **The hermetic e2e ran on port 5184** (5188 for the merged tree), through a config that
  re-exports `tests/e2e/playwright.config.js` unchanged but for the port, because the
  repository's config reuses whatever already holds 5173 and the other session held it. The
  contract browser tier cannot move (its spec reads the ports from its config), so it ran on
  5174 and 5175 in windows agreed with that session, announced at start and finish.
- **The node tier's `nodetier.test.js` needs `TMPDIR` resolved on macOS.** Its loader hook
  compares `/private/var/...` with `/var/...`, and it fails that way on untouched `main` too.
  CI runs on Linux, where the two are one path.
- **The e2e suite needed two workers to mean anything on this machine today**, and the runs
  that did not are recorded rather than dropped. With the other session running Playwright
  passes back to back, the load average reached 20 to 33 on 8 cores, and at the default four
  workers full runs failed 6 and then 17 specs. All but two failed inside `boot`, before the
  page loads, on Playwright's "clock.pauseAt: Cannot fast-forward to the past", a race in the
  suite's frozen-clock boot; the other two were timing assertions (A1v3's announcement, A7h's
  superseded fetch). At `0c17079` one two-worker run under that load failed three (A7h, and
  A6n and P5e in `boot`); those passed 12 of 12 repeated alone, and the next full run passed
  324 of 324 at a load average of 5 to 8.

---
🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01Ap2nYH4fmBJS44TNwaTyoE
