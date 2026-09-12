# The freshness and provenance contract

**Status:** a design for review. Nothing in it is implemented on this branch, and this
branch changes no code, no test, no fixture and no golden.

**Written against:** `672206a`, the `main` this branch forked from, with items 1 to 6 of
the Release 1 order in [the audit record](../reviews/audit-2026-09-05.md) already landed
(F12, N1, F10, F02, F11, F05, and N3 and N5 riding with them). This is item 7, the one
those six were sequenced ahead of because it is the only one that blocks anything: F03
and F01 both need it, and building it twice is how the map and the panel drift apart
again.

**What it is for.** Three surfaces in this application can currently present old
information in the grammar of current information, and each of them does it a different
way, so three separate fixes would produce three separate vocabularies. This document
proposes one contract that all of them speak: what a served observation carries, what a
rider is told about it, and what each provider's data actually supports. Section 5 lists
what it deliberately does not settle.

**Every number below is measured**, from the captures committed to this repository or
from the shipped source, and each says where it came from. Where a provider supplies no
per-observation time, this document says so and stops. The single largest risk in a
freshness contract is inventing a timestamp that no provider gave us, and then rendering
a rider-facing qualifier from it.

---

## 1. The problem, with the numbers

### 1.1 F03: a countdown that cannot say how old it is

A station board counts down. The countdown is arithmetic on an absolute epoch time that
came out of a prediction, so it is only as current as the prediction behind it, and
nothing in the payload that carries it says when that prediction was made.

`f03_arrivals_content_freshness.py` drives the real `_refresh_subways` and the real ASGI
app over the committed subway capture re-stamped 600 seconds behind the poll clock.
`/api/subways` reports 599.7 seconds of content lag, because the vehicle envelope carries
`feed_timestamp`. `/api/subway-arrivals/219` reports a freshly stamped `fetched_at`, eight
of eight groups `ok`, and no content clock of any kind, because
[`StationArrivals`](../../backend/models.py) has no field that could hold one. A rider
reading that board sees a two minute countdown built from a ten minute old prediction,
with no qualifier, while `/api/status` says every contributing feed is healthy. Both
statements are true of what they measure. Neither is true of the board.

The frontend then compounds it rather than catching it. The panel's staleness line is
computed at `frontend/helpers.js:2193`:

```js
ageSeconds: payload.fetched_at == null ? null : now - payload.fetched_at,
```

That is the age of OUR ACQUISITION, not the age of the content. `frontend/stations.js:665`
turns it into the rider-facing sentence, and the popup renders the same line from the same
threshold through `stalePopupLine`, deliberately, so the two surfaces cannot word it
differently. They agree, and they are both answering the wrong question: the line appears
when our poller stops and stays silent when the provider stops.

**The shape of the defect is not subway-specific.** Five arrivals models
(`StationArrivals`, `RailroadStationArrivals`, `PathStationArrivals`,
`NjtStationArrivals`, `FerryStationArrivals`) carry `fetched_at` and no content clock, so
this is one design error committed five times rather than one system's bug. `AlertFeed`
makes six clockless envelopes against six that carry a clock, and the split is exactly
vehicles-yes, everything-else-no.

### 1.2 F01: forty-one old observations drawn as live

The committed LIRR capture (`backend/tests/fixtures/railroad_lirr.pb`, header
`1782006915`, 2026-06-20 21:55:15 EDT) holds 201 entities: 69 vehicle-only entities with a
position, and 132 trip-update-only entities. Every one of the 69 carries its own
`vehicle.timestamp`, and **none of the 69 equals the header**, so this feed genuinely
dates each observation separately from the message that carries it.

Measured against that capture's own header, with no reference to today's date:

| Ages of the 69 positioned LIRR observations, relative to their own feed header | |
| --- | --- |
| Freshest | 4s |
| Median | 203s |
| Older than 90s | **42** |
| Older than 300s | 32 |
| Older than 600s | 25 |
| Oldest | **53676s (14h 54m 36s)** |

The oldest was recorded at 07:00:39 and published in a feed generated at 21:55:15 the same
day. It is a morning rush-hour position on an evening map.

**Twenty-three of the 42 report `IN_TRANSIT_TO`**, with a median age of 593 seconds. The
feed is not merely serving old fixes, it is serving old fixes of trains it describes as
moving, which is the reading a marker on a map gives them anyway.

`_decode_railroad_vehicles` reads `v.HasField("position")`, the geographic box, the
cancellation set and the route join. It never reads `v.timestamp`, and its own docstring
records that `now` is unused in that phase. All 69 observations therefore leave the decoder
wearing the feed's freshness and nothing of their own.

**Since F02 landed, 68 of the 69 are served** (the canceled 5-train is now dropped before
emission, for cancellation and not for age), so **41 stale observations still reach riders
unqualified**. That is the live number.

**Filtering will not restore them, which is what makes this a design job.** Of the 42 stale
observations, all 42 join a trip update in the same feed by `trip_id`, and only **15** have
a trip update with any stop time still in the future relative to the header. All 15 survive
F02. And the trip updates are not automatically trustworthy either: 127 of the 132 carry
their own `timestamp`, 88 of those are themselves more than 90 seconds old, and the oldest
is 52538 seconds (14h 35m). Gating the fallback on the prediction's own age, which this
feed makes possible, changes how many riders get an estimate rather than nothing:

| Prediction age allowed | Stale GPS observations with a usable estimate behind them |
| --- | --- |
| 90s | **6** of 41 |
| 300s | 8 of 41 |
| 600s | 11 of 41 |
| No limit | 15 of 41 |

That table is the real content of the F01 decision, and it is why the threshold is a
question for section 5 rather than a constant this document picks.

**One measurement points the other way and is worth stating**: of the 27 observations that
are fresh (90s or newer), **zero** have a trip update older than 90 seconds. The two clocks
agree when the feed is healthy and diverge only on the trains that are already a problem,
which is the evidence that this is a real signal and not noise.

**N2 rides here.** `positioned_ids` (`backend/feeds/railroad.py:342`) is built from every
entity with a position, with no geographic box and no age check, and the placement pass
skips any trip in it. It is therefore STRICTLY BROADER than the set the GPS pass actually
emits. A positioned vehicle rejected by the box appears on no surface at all: not as GPS,
because the box dropped it, and not as an estimate, because `positioned_ids` still claims
it. Whoever adds an age gate to the GPS pass widens that hole by exactly the number of
observations the gate rejects, unless the two passes are made to share one accepted set.
That is the same edit.

### 1.3 The acceptance case that makes this one contract and not three

> **F03 acceptance:** repeatedly returning an old, valid HTTP-200 feed makes its countdowns
> visibly qualified as stale; other healthy contributors remain distinguishable.

The second clause is the one that reaches into the models. A subway station is served by
several feed groups at once, so "this board is stale" is not the answer when one of four
contributors is lagging and three are current. The machinery for the union already exists
and is already correct for the clock it has: `_oldest_contributing_fetched_at`
(`backend/cache.py:317`) reports the worst POLL time among the systems actually
contributing arrivals at one station, excludes a group that contributes nothing there, and
returns `None` rather than let a healthy group speak for one that has never decoded.

There is no equivalent for CONTENT time, because `SystemFreshness` does not carry one. Its
four fields are `fetched_at`, `ok`, `retained_since` and `routes`, and all four are about
our relationship with the provider (when we last decoded it, whether it is failing, whether
we are carrying it forward, which routes it covers). None of them is about the age of what
the provider sent. A system can be `ok: true`, `fetched_at` one second ago, `retained_since`
null, and be serving ten minute old content, which is precisely the F03 reproduction.

**So the acceptance case cannot be written against today's models**, and that is the
dependency: F03 needs a per-contributor content clock, F01 needs a per-observation clock,
and both need the same vocabulary at the surface or the map and the panel will say
different words about the same feed.

---

## 2. What each provider actually gives us

This is the section the contract has to be built on rather than around. Every row below
was measured from a capture committed to this repository, parsed directly from the
protobuf, or read out of the decoder that consumes it. GTFS-Realtime makes both
`VehiclePosition.timestamp` and `TripUpdate.timestamp` optional, and our providers
disagree about them completely: one supplies both, two supply a vehicle clock and no
prediction clock, one supplies a prediction clock and has no vehicles at all, one supplies
neither while having the best header in the set, one supplies a value that carries no
information, and one we have not measured.

| System | Observation | Per-observation time in the feed | Measured on the committed capture | Read by the decoder today |
| --- | --- | --- | --- | --- |
| LIRR | GPS position | `vehicle.timestamp` | 69 of 69 set, **none equal to the header** | **No** |
| LIRR | Prediction | `trip_update.timestamp` | 127 of 132 set, 88 of them over 90s old | **No** |
| Metro-North | GPS position | `vehicle.timestamp` | 49 of 49 set, **all 49 exactly equal to the header** | No |
| Metro-North | Prediction | `trip_update.timestamp` | **0 of 119 set** | No |
| Subway | Stop observation | `vehicle.timestamp` | 98 of 98 set (98 of 160 trips), 16 over 90s, oldest 20912s | **No**, the entity is never visited |
| Subway | Prediction | `trip_update.timestamp` | **0 of 160 set** | No |
| NJ Transit | Placed position | none: no vehicle feed is fetched | 0 vehicle entities in 112 | n/a |
| NJ Transit | Prediction | `trip_update.timestamp` | **0 of 112 set** | No |
| PATH | Placed position | `trip_update.timestamp` | **53 of 53 and 55 of 55 set** | **No** |
| NYC Ferry | GPS position | `vehicle.timestamp` | 28 of 28 set, 1s to 15s old | **Yes**, as `updated_at` |
| NYC Ferry | Prediction | `trip_update.timestamp` | **0 of 50 set** | No |
| Buses | GPS position | `VehiclePosition.timestamp` | **no committed capture: unmeasured** | No |
| Alerts | Alert | none in the GTFS-RT alert message | 9 and 37 alerts, no per-alert time | n/a |

### 2.1 Long Island Rail Road

**The header is the true feed-generation time and every vehicle dates itself
independently.** That is stated in the code (`backend/feeds/railroad.py:43-50`) and it
holds on the capture: the header is `1782006915` and not one of the 69 positioned vehicles
carries that value. The ages run from 4s to 53676s with a median of 203s, so the
distribution is not a couple of outliers behind a healthy fleet: half the fleet is over
three minutes old and a quarter is over ten minutes old.

The layout is SEPARATE ENTITIES: 69 vehicle-only, 132 trip-update-only, none combined. The
join is by `trip_id`, and it is total on this capture: all 69 positioned vehicles join a
trip update, including all 42 of the stale ones.

**The predictions date themselves too, and that matters more than it looks.** 127 of the
132 trip updates carry `trip_update.timestamp`; the median age is 887s and the oldest is
52538s. So "fall back to a prediction" is not automatically an improvement over a stale
GPS reading, and LIRR is the one system where we can tell the difference before deciding.

LIRR is therefore the only provider that supplies an independent observation time for
BOTH kinds of observation, and today the decoder reads neither.

### 2.2 Metro-North

**The header is copied onto every vehicle timestamp, so MNR vehicle timestamps carry no
independent signal.** On the committed capture the header is `1782006692` and all 49
positioned vehicles report exactly that value: 49 of 49 equal, 0 differing, age spread
exactly zero. There is nothing to measure because there is nothing there.

The layout is COMBINED: all 119 entities carry a trip update and a vehicle on one entity.
None of the 119 trip updates carries `trip_update.timestamp`.

**The 49 positioned entities collapse to 33 served markers**, because four trip ids repeat
across several entities and the live path keys one marker per trip
(`backend/tests/test_feeds_railroad.py:528-536`). Today that collapse is free: every
duplicate carries the same copied header, so there is nothing to choose between. Under a
contract with real per-observation times it stops being free, and whichever decoder
collapses duplicates has to say which observation's `observed_at` survives.

**And the header itself is not publish time.** `backend/feeds/railroad.py:43-50` records
the probe that established this:

> A per-vehicle-vs-header probe found MNR stamps a bursty clock that lags ~2-4 min onto
> its header AND copies it onto every vehicle.timestamp (no independent signal), while its
> GPS positions are live; LIRR's header is the true feed-generation time.

That is why `RAILROAD_FRESHNESS_SYSTEMS` is `frozenset({"LIRR"})` and MNR's header never
reaches the railroad envelope's `feed_timestamp`. The contract must inherit that exclusion
rather than re-derive it, and it must inherit the reason: **a Metro-North position is
live while its only available timestamp is two to four minutes behind.** Aging Metro-North
observations against their own stamps would mark a live fleet stale every few minutes,
which is the exact inverse of the F01 defect and no better.

**So Metro-North supplies no per-observation time at all.** The contract does not get to
invent one for it. What Metro-North gets instead is section 3.3's explicit
"no observation clock" row, and the age signal it already relies on: the poll-age term,
which detects our own failure but not the provider's.

### 2.3 Subway

**The subway vehicle feed dates its observations and carries no coordinates.** All 98
VehiclePosition entities on the committed capture carry `timestamp`; **none** carries a
`position`. What each one reports is a stop and a status (`current_stop_sequence`,
`current_status: STOPPED_AT`, `stop_id: "103N"`), so every subway position this
application draws is DERIVED: placed at the stop the feed names and interpolated between
stops from the prediction times.

The per-observation times are mostly healthy and have a real tail: median 12s, 16 of 98
over 90 seconds, 12 over 300 seconds, 11 over ten minutes, oldest 20912s (5h 48m). That
tail is the subway's own small F01, present in the same capture the goldens are built
from, and currently invisible for the same reason.

**Two facts make the subway case more work than the table suggests.** `_decode_feed`
(`backend/feeds/subway.py:85-87`) walks `trip_update` entities and nothing else, so the 98
VehiclePosition entities are not merely unread, they are never visited: joining a vehicle
clock to a train is a new join, not a new field read. And the join does not cover the
fleet. All 98 VehiclePositions join a trip update by `trip_id`, but **62 of the 160 trip
updates have no VehiclePosition at all**, so 62 subway trains would carry `observed_at`
null while 98 carry a real one. The subway is the one system where BOTH answers in section
3.3 are correct, per train.

**The predictions do not date themselves:** 0 of 160 trip updates carry
`trip_update.timestamp`. So a subway arrival's only honest content clock is the header of
the feed group that produced it, which is exactly the clock F03 says is missing from the
arrivals envelope, and exactly the clock `_oldest_contributing_fetched_at` already knows
how to select the worst of.

**And the group headers are already folded to one anonymous number.**
`backend/feeds/subway.py:287` takes `min(timestamps)` across the groups that decoded, so
the envelope's `feed_timestamp` is ALREADY the worst group's content time. Nothing records
which group it came from, and the other seven are discarded. Publishing a per-group content
clock is therefore a refinement of a fold the codebase already performs correctly, not a
new idea: the worst-case answer exists, it just cannot be attributed, and attribution is
the whole of the acceptance case's second clause. The subway half of this contract is therefore one new value
carried through machinery that already exists, not new machinery.

### 2.4 NJ Transit

**There is no GPS to be fresh or stale.** The vehicle positions feed is deliberately not
fetched, and the committed capture is 112 trip updates and zero vehicle entities. Every
NJ Transit position in this application is computed from the TripUpdates feed's own times
against the static stop coordinates, which `backend/models.py:355-360` states as the
model's first sentence:

> SCHEDULE-DERIVED, never GPS: every position below is computed from the TripUpdates
> feed's own times against 15a's stop coordinates.

**The predictions do not date themselves either:** 0 of 112 trip updates carry a
timestamp. The header is the only clock, and unlike Metro-North's it is a good one. THE
FRESHNESS BUDGET, DERIVED (`backend/feeds/njt.py:91-146`) is the repository's most worked
freshness derivation and it is load-bearing here: generation every ~11.8s, header lag 9s
to 23s at peak measured as receive-time minus header, a typical worst age of 45s against
the shared 90s budget and a pathological worst of 88s. NJ Transit is the tightest-margin
system under that budget.

**One caveat on that derivation, since this document claims every number is
re-derivable.** It is the only set of figures here that is not. The 2026-08-05 probe's
output is committed nowhere; the numbers survive as prose in seven tracked files
(`feeds/njt.py`, `cache.py`, `pollers.py`, `scripts/contract_monitor.py`,
`scripts/gen_njt_rt_fixture.py`, `tests/test_contract_monitor.py` and `README.md`), and a
re-probe is the only way to check them. They are used here as the repository's own recorded
measurement rather than as something this document re-measured.

**NJ Transit is also the system where retention is already unbounded and unmarked.**
`FEED_RETENTION_MAX_S` scopes itself to one failed subsystem inside an AGGREGATE envelope,
and NJ Transit is a single-system feed whose total failure keeps the whole envelope, so no
cap applies and no per-train marker says the data is being carried forward. A `retained`
provenance value has more to fix here than anywhere else.

**What NJ Transit already has that looks like provenance and is not.** `NjtTrain.status`
is a closed set of three values, `"at-station"`, `"approaching"` and `"in-transit"`, and
it describes MOTION, not derivation. Every one of those three is a placed position. A new
provenance field must not be called `status` on this model, and must not be folded into
it.

### 2.5 PATH

**PATH's trip updates date themselves, on every capture, and nothing reads it.** 53 of 53
on the general capture (ages 18s to 63s, median 38s) and 55 of 55 on the rush capture
(ages 25s to 110s, median 55s, **12 over 90 seconds**). This is a better clock than the
one the envelope currently reports.

What the envelope reports is the bridge's WRITE time, and `backend/models.py:237-239` is
explicit that this is not a content clock:

> The bridge's WRITE time: it advances every regeneration (~15s) even when the entity
> content is unchanged, so it signals "bridge alive", not "upstream refreshed". Unchanged
> content across polls is normal for PATH.

**That is the sharpest case in the whole table, and the contract tier already says so in
its own words.** `tests/contract/test_contract_api.py:191`, on a stuck PATH upstream: "The
map quietly lies and nothing in the stack objects." An age rule built on the PATH envelope
alone can never fire, because the clock it reads advances whether or not anything upstream
happened. The per-trip timestamps can fire, and on the rush capture 12 of 55 already would
at the app's 90 second threshold. **They would fire at that threshold only**: the monitor's
own PATH band is `PATH_STALE_S = 300` (`backend/scripts/contract_monitor.py:244`), and at
300 seconds not one observation in any committed PATH capture is old enough to flag.
Whichever threshold the policy names has to be named on purpose. Positions remain placed rather than observed (the bridge carries no coordinates),
so PATH's per-observation time dates a PREDICTION, which is what it should be used to
qualify.

### 2.6 NYC Ferry

**Ferry is the one system where this contract is already half built.** The decoder reads
`vehicle.timestamp` and serves it (`backend/feeds/ferry.py:193-195`):

```python
# Per-vehicle content time (advances each poll); the boat's own
# freshness, distinct from the feed header timestamp.
"updated_at": float(vehicle.timestamp) or None,
```

It reaches the model as `FerryBoat.updated_at` and is served by `/api/ferry`. Measured on
the committed capture: 28 of 28 boats carry it, ages 1s to 15s against the header, median
4.5s. A real, independent, healthy observation clock.

**No frontend surface reads it.** A search of `frontend/` for `updated_at` returns
nothing. So the field exists, is correct, is served, and changes nothing a rider sees.
That is simultaneously the precedent for this contract and its warning: **a per-observation
timestamp that no surface consumes is not a freshness contract, it is a field.** Half of
the work in sections 3 and 4 is the consuming half.

The two ferry feeds are separate with separate clocks. VehiclePositions dates each boat;
TripUpdates dates nothing (0 of 50), so a dock arrival's honest clock is the TripUpdates
header. The envelope currently reports the VehiclePositions header as `feed_timestamp`,
which is the audit's point in the F03 remedy: **ferry arrivals should be aged against the
TripUpdates clock rather than the boat-position clock**, and today there is no field in
which to carry the difference.

### 2.7 Buses

**We do not consume SIRI, so there is no `RecordedAtTime` to read.** The bus feed is
OneBusAway's GTFS-Realtime endpoint (`backend/feeds/buses.py:11-15`,
`https://gtfsrt.prod.obanyc.com/vehiclePositions`), and the analogous field is
`VehiclePosition.timestamp`. `fetch_vehicle_positions` reads `position`, the NYC box, the
route and the bearing, and does not read `timestamp`.

**There is no committed bus capture, so whether OneBusAway populates that field is
unmeasured here.** Every other row in the table above was measured; this one cannot be,
and the honest consequence is that **the buses row of the age policy in section 3.3 cannot
be written yet.** It is a capture, not a decision: one probe of the live endpoint, counting
how many vehicles carry a timestamp and how far each sits from the header, settles it the
way the 2026-08-05 NJT probe settled the freshness budget. Until then buses take the
"no observation clock" row, which is the safe direction, and the probe is listed in section
6 as a prerequisite rather than assumed away.

### 2.8 Service alerts

A GTFS-RT `Alert` has no timestamp. It has `active_period` (when the alert applies, which
is a fact about the world and not about the observation), and the feed header. The
committed alert captures hold 9 (Metro-North) and 37 (NJ Transit) alerts and no
per-alert time.

So an alert's `observed_at` is the alert feed's own clock, and the interesting freshness
question for alerts is retention rather than age: the alerts index keeps its last good
content on a failed poll, re-filters for expiry and applies a retention cap, so a shown
alert may be current, retained, or retained-and-still-valid. The panel already labels a
stale or retained alert SOURCE per system, which F11 landed, and that label is the closest
thing in the codebase to a rider-facing provenance word.

**The NJ Transit empty-alerts rule (N4) needs a value in the enumeration.** A zero-byte 200
from that endpoint is now a served state meaning zero alerts, and `/api/status` says so at
`feeds.njt_alerts_served_empty`. The ambiguity is recorded rather than hidden: a dead
endpoint sends the same bytes. That is a served observation of "nothing", with a real
`observed_at` and a provenance that is neither live data nor retained data, and section
3.1 has to be able to express it.

### 2.9 Reading the table sideways

Four things fall out, and they set the shape of the contract:

1. **No single rule can cover these providers, or even one provider's whole fleet.** LIRR
   dates everything, Metro-North dates nothing, subway dates 98 of its 160 trips and not
   the other 62, PATH dates the prediction and has no vehicle, NJ Transit dates neither and
   has a very good header, ferry dates the boat and not the dock. A per-provider policy TABLE is not a convenience here, it is the
   only correct form.
2. **Absence of a clock has to be a first-class state.** It is the answer for Metro-North,
   for every subway prediction, for every NJ Transit observation, for ferry docks and
   (pending the probe) for buses. If `observed_at` cannot be null, the contract will invent
   values for more than half the fleet.
3. **A per-observation time is not automatically better than a header.** Metro-North's
   per-vehicle value is a copy, and using it would be strictly worse than using nothing.
   The policy has to name which clock each provider's rule reads.
4. **Four clocks already exist and three of them are read by nothing.** LIRR's, the
   subway's and PATH's are decoded past and discarded; ferry's is carried all the way to
   the wire as `updated_at` and read by no surface. Part of this contract is already
   sitting in the payload.
5. **There is no uncertainty channel to lean on.** GTFS-Realtime offers
   `StopTimeEvent.uncertainty`, and no provider here populates it: 0 of 1501 LIRR
   stop-time updates, 0 of 100 ferry events, 0 of 53 and 0 of 55 PATH events. A confidence
   qualifier finer than "how old is it" has nothing behind it.

---

## 3. The contract

### 3.1 Every served observation carries an observation time and a provenance

**An observation is one thing this application asserts about the world**: a GPS position, a
placed position, an arrival prediction, an alert. Every one of them, in every envelope,
carries two new fields.

```python
observed_at: float | None   # when the PROVIDER observed this, epoch seconds
provenance: str             # one of the five values below, never empty
```

**`observed_at` is nullable, and that is the load-bearing part of the design.** Section 2
found that more than half of what we serve has no per-observation clock behind it, and a
non-nullable field would force a value for all of it: the header, the poll time, or
something computed. Each of those would be a qualifier rendered from a number the provider
never supplied. `None` means "this provider does not date this observation", it is a state
the rider vocabulary in 3.2 covers, and it is the only correct answer for Metro-North
positions, subway predictions, NJ Transit anything, ferry dock arrivals and (pending a
probe) buses.

**`observed_at` is not a new name.** The audit's own reproduction already uses it:
`f01_lirr_gps_observation_age.py:412` asserts that `models.RailroadFeed` STRIPS an
`observed_at` added to a cached train, so the field name is already written down and the
model already has a documented behavior toward it.

**`provenance` is a closed enumeration of five values.** The names are kebab-case to match
the health codes and `NjtTrain.status`, and a value outside the set is a decoder bug rather
than a client's problem. It would be the first closed set in the file: `backend/models.py`
contains no `Enum` and no `Literal` anywhere, and `NjtTrain.status`, `FerryBoat.status`,
`Alert.effect` and `Alert.cause` all ship as bare `str`. Two of those are GTFS-RT
pass-throughs that are deliberately open, so whether provenance is typed or merely
documented as closed is a real choice, and the honest default is to type it, because unlike
those two nothing upstream can widen it.

| Value | Means | Today's implicit equivalent |
| --- | --- | --- |
| `live-gps` | The provider reported a coordinate for this vehicle. | `isPlacedRailroad(t) === false` |
| `estimated` | Computed by interpolating a prediction between two known points. | the `prev_*`/`next_time` anchors being populated |
| `placed` | A stop's own coordinates, from a prediction or the timetable. | `isPlacedRailroad(t) === true`, `NjtTrain` always, `PathTrain` always |
| `retained` | Carried forward from an earlier poll; not in the latest decode. | `SystemFreshness.retained_since`, per system only |
| `unknown` | Provenance could not be determined. | nothing |

**The client already computes this enumeration, from the shape of the fields, and that is
the thing to stop.** `frontend/helpers.js:211` is the whole of today's provenance system:

```js
function isPlacedRailroad(t) {
  return t.stop_id != null;
}
```

A rule that reads presence-of-a-field as meaning-about-the-world is exactly what breaks the
moment a decoder starts filling `stop_id` for a GPS train, and it is duplicated per system
on the client. Moving it to the server is most of the work of this contract and none of the
risk.

**Two notes on the enumeration itself.**

`retained` is the odd member, and deliberately so: the other four say how a position was
DERIVED, and `retained` says when it was SERVED. A retained GPS position is both. The
recommendation here is that `retained` wins the field, because "this is not in the current
decode" is the fact that changes what a rider should believe, and `SystemFreshness`
already carries `retained_since` for anyone who needs the timing. **This is question Q3 in
section 5**, because collapsing it loses the original derivation and a reader could
reasonably want both.

`unknown` must not become the default a lazy decoder reaches for. It exists for one real
case: an observation reaching a surface through a path that predates this contract, which
happens during the rollout and in a browser holding a new frontend against an old backend.
The client's fail-safe for `unknown` is defined in 3.2 and it is the pessimistic one, the
same direction `systemFreshnessOf` already takes when it falls back to a source's worst.

**The enumeration is position-shaped, and predictions and alerts do not fit it cleanly.**
An alert is never derived, so `estimated` and `placed` are both wrong for one, and the only
values it can take are "as the provider sent it" and `retained`. The first of those is
spelled `live-gps` above, which is a position word doing a job it was not named for. The
same strain shows on a prediction: an LIRR arrival time is reported, not GPS. This is
question Q8 in section 5, and it is a naming decision rather than a modelling one, since
the set of states is right either way.

**NJ Transit's served-empty alerts state (N4) needs saying out loud**: zero alerts observed
at a real time is an observation of nothing, not an absence of observation, and the two
must not render the same way. It takes a real `observed_at` and the as-sent provenance
value, whatever Q8 names it.

### 3.2 The words a rider sees, defined once for every surface

**Most of this vocabulary already ships**, which is the strongest argument for making it
the contract's rather than inventing a new one. These are the literal strings in the code
today:

| Shipped string | Where | Says |
| --- | --- | --- |
| `live GPS` | `helpers.js:2378`, `systems/railroad.js:158` | a real reported position |
| `scheduled position (no GPS)` | `helpers.js:1092`, `:1464` | NJT and PATH popups |
| `scheduled position, no GPS` | `helpers.js:1512`, `:2378`, `:2391` | accessible names |
| `scheduled (no GPS)` | `systems/railroad.js:158` | the railroad popup's compact form |
| `as of {age} ago` | `helpers.js:701`, `:730`, `stations.js:666` | the staleness line, one renderer |
| `{system} not reporting` | `helpers.js:703` | a system that has never decoded |
| `feed empty, showing last known` | `helpers.js:751`, `:757` | a bounded empty run |
| `scheduled service (no live tracking)` | `helpers.js:1030` | AirTrain, which has no feed |

**The contract adds exactly two words and fixes one omission.**

| Provenance / state | The rider word | Note |
| --- | --- | --- |
| `live-gps`, fresh | *(nothing)* | Silence means current. That is the grammar every surface already uses and the reason the F03 defect is invisible. |
| `live-gps`, aged | `live GPS, as of {age} ago` | Reuses both halves, verbatim. |
| `estimated` | **`estimated from a prediction`** | NEW. The one case the codebase has no word for. |
| `placed` | `scheduled position (no GPS)` | Unchanged, and already correct on NJT and PATH. |
| `retained` | `showing last known, as of {age} ago` | Joins a shipped clause to a shipped clause. |
| `unknown` | **`age unknown`** | NEW. The positive statement that silence cannot make. |
| observed_at is null | `age unknown` | Same words: from a rider's side these are one state. |

**The rule, stated once for all surfaces:** a marker, a popup, a panel row or a board row
whose provenance is not `live-gps`, OR whose `observed_at` is older than its system's fresh
threshold, OR whose `observed_at` is null, MUST carry its word. There is no surface
exempted for being small, and no surface that gets its own phrasing. `humanizeAge` stays
the single age formatter (seconds under two minutes, whole minutes above) so the map, the
popup, the panel and the live region cannot word the same age differently, which is the
discipline `stalePopupLine` was written to enforce and which this extends rather than
replaces.

**One piece of the shipped vocabulary does not survive contact with these ages.**
`humanizeAge` has two tiers, seconds under two minutes and whole minutes above, which is
right for a feed that is 90 seconds late and wrong for an observation that is 14 hours old:
53676 seconds renders as "as of 895m ago". `countdownParts` in the same file already has an
hours form for exactly this reason, and a third tier borrowed from it is the smallest
change that keeps one formatter.

**And the negative half of the rule matters as much:** a surface that cannot show the word
must not show the observation. A dot on a map with no popup open has nowhere to put "as of
6m ago", so an aged observation must be visually distinguishable at the marker itself. The
`STALE_MARKER_OPACITY` treatment already exists and already compounds with a marker's
resting opacity; what is new is that it becomes PER-OBSERVATION rather than per-system.
Today every marker of a stale system dims together and no marker of a healthy system ever
dims, which is why 41 stale LIRR observations sit at full opacity inside a healthy feed.

### 3.3 The age policy, as data

**One pair of numbers, and a per-provider table that says which clock each rule reads.**
The numbers are not new. They are the two the repository has already derived and already
justified, applied one level down:

| Name | Value | Where the value comes from |
| --- | --- | --- |
| `OBS_FRESH_S` | **90** | `cache.FEED_STALE_AFTER_S`. NJ Transit is the tightest-margin system under it and THE FRESHNESS BUDGET, DERIVED (`feeds/njt.py:91`) is the working. Reusing it keeps one number to re-derive, not two. |
| `OBS_MAX_S` | **600** | `cache.FEED_RETENTION_MAX_S`, whose own comment already makes the observation-level argument: "a ten-minute-old train position, rendered AS stale, is honest context a rider can use, while an hour-old one is a ghost". |

The per-provider table is DATA. One row per system and observation kind, read by the
decoder, not branched on in it:

| System | Observation | Clock the rule reads | Age-gated | Rationale |
| --- | --- | --- | --- | --- |
| LIRR | GPS position | `vehicle.timestamp` | **Yes** | 69 of 69 independent. This is F01. |
| LIRR | Prediction | `trip_update.timestamp` | **Yes** | 127 of 132 independent, 88 of them already over 90s. |
| Metro-North | GPS position | none | **No** | The stamp is a copy of a header that lags 2 to 4 minutes. Gating on it would mark a live fleet stale. `observed_at` is null. |
| Metro-North | Prediction | none | **No** | 0 of 119 carry a timestamp. `observed_at` is null. |
| Subway | Position, joined to a VehiclePosition | `vehicle.timestamp` | **Yes** | 98 of 160 trips join one; 16 of those are already over 90s. |
| Subway | Position, no VehiclePosition | none | **No** | 62 of 160 trips. `observed_at` is null. |
| Subway | Prediction | contributing group header | **Yes** | 0 of 160 self-dated; the group header is the only honest clock and is already selected per contributor. |
| NJ Transit | Placed position | feed header | **Yes** | The header is a good clock (lag 9s to 23s at peak, measured). |
| NJ Transit | Prediction | feed header | **Yes** | Same clock, same derivation. |
| PATH | Placed position | `trip_update.timestamp` | **Yes** | 53 of 53 and 55 of 55 independent. NOT the envelope's `feed_timestamp`, which is a write time that advances regardless. |
| PATH | Prediction | `trip_update.timestamp` | **Yes** | Same field, same reason. 12 of 55 on the rush capture already exceed 90s. |
| NYC Ferry | GPS position | `vehicle.timestamp` (served today as `updated_at`) | **Yes** | 28 of 28 independent. The field already exists; only the rule and the rendering are new. |
| NYC Ferry | Dock arrival | TripUpdates header | **Yes** | 0 of 50 self-dated, and the audit's remedy names this explicitly: age against the TripUpdates clock, not the boat clock. |
| Buses | GPS position | **undetermined** | **Pending a probe** | No committed capture. The row is written after one probe of the live endpoint, not before. |
| Alerts | Alert | alert feed header | Retention only | GTFS-RT alerts carry no time. `active_period` is about the world, not the observation. |

**Six of the fourteen rows are "no per-observation clock", and none of them is a gap to be
filled later.** They are measurements. Writing a rule for Metro-North positions would
require a clock Metro-North does not send.

**This collides with a decision the repository has already made, deliberately, twice.**
`/api/status` treats an absent upstream timestamp as HEALTHY on purpose ("unknown is
tolerated, having data beats penalizing a missing timestamp",
`backend/routes/status.py:241-243`), and `_refresh_railroads` goes further: it writes the
LAST-KNOWN `feed_timestamp` rather than the `None` it just computed, because "`/api/status`
reads an unknown feed age as healthy, so blanking it turns a real outage into a green
light" (`backend/pollers.py:631-632`). Both are right about the operator surface, where a
missing number must not fail a deploy. **They are the wrong default for a rider surface**,
where silence reads as "current". The contract needs the two to diverge on purpose: unknown
stays tolerated by `/healthz` and becomes SAYABLE to a rider. Anyone implementing this who
copies the operator rule into the rendering will reproduce F01 with a new field.

### 3.4 The fallback order, and what it costs

**The order**, applied per vehicle, taking the first step that succeeds:

1. **`live-gps`, unqualified.** A GPS position whose `observed_at` is within `OBS_FRESH_S`.
2. **`estimated`.** Interpolated from a prediction whose own `observed_at` is within
   `OBS_FRESH_S`, when the position is not.
3. **`live-gps`, qualified.** The GPS position itself, when its `observed_at` is within
   `OBS_MAX_S`, rendered with the age line and the dimmed marker.
4. **`placed`.** At the stop a prediction within `OBS_MAX_S` names, when nothing above
   holds.
5. **Nothing.** No marker, and the vehicle is absent from the map.

**Step 2 sits above step 3 deliberately.** A fresh prediction says more about where a train
is NOW than a ten-minute-old coordinate does, even though the coordinate was once exact.
Step 3 sits above step 4 for the mirror reason: a six-minute-old real position beats a
placement derived from a nine-minute-old prediction.

**What this costs, measured on the committed LIRR capture at `OBS_FRESH_S` 90 and
`OBS_MAX_S` 600:**

| Step | Vehicles | What the rider sees |
| --- | --- | --- |
| 1. `live-gps` unqualified | **27** | unchanged from today |
| 2. `estimated` | **6** | a marker labeled "estimated from a prediction" |
| 3. `live-gps` qualified | **11** | a dimmed marker reading "as of {age} ago" |
| 4. `placed` | **0** | (see below) |
| 5. nothing | **24** | **the marker is gone** |

**Twenty-four of sixty-eight LIRR trains disappear from the map, and that is the decision
this section exists to put in front of a reader.** Today all 68 are drawn as live. Under
this policy 27 stay as they are, 17 become honest, and 24 stop being drawn, because there
is nothing honest left to draw: their GPS is over ten minutes old and so is every
prediction behind them.

**Step 4 never fires on this capture, and the reason is worth recording.** Not one of the
24 vehicles whose GPS exceeds ten minutes has a prediction younger than ten minutes: only
4 of the 24 have a future stop time at all, and all 4 of those predictions are themselves
over ten minutes old. **A train whose GPS has gone quiet has a trip update that has gone
quiet too.** The step stays in the order because that is one capture on one evening, and
a provider whose two feeds fail independently is entirely plausible; but nobody should
expect it to rescue anything, and an acceptance test that requires it to fire would be
testing a synthetic feed rather than this one.

**Sensitivity to the prediction threshold, since this is the parameter most likely to be
argued about:** relaxing step 2 from 90s to 300s moves 2 vehicles from step 3 to step 2,
and to 600s moves 5. It never changes the 24. The estimate threshold is a choice about how
honest a label is on 6 to 11 markers; the `OBS_MAX_S` value is the choice that decides
whether 24 riders' trains exist.

### 3.5 What `SystemFreshness` has to carry

The audit's acceptance case has two clauses and the models can express neither:

> repeatedly returning an old, valid HTTP-200 feed makes its countdowns visibly qualified
> as stale; **other healthy contributors remain distinguishable**.

`SystemFreshness` carries `fetched_at`, `ok`, `retained_since` and `routes`. Every one of
those is about OUR RELATIONSHIP WITH THE PROVIDER: when we last decoded it, whether it is
failing, whether we are carrying it forward, which routes it covers. None is about the age
of what the provider sent, so a system can report `ok: true`, `fetched_at` one second ago,
`retained_since` null, and be serving ten minute old content. That is the F03 reproduction,
exactly.

**One field closes it:**

```python
feed_timestamp: float | None   # THIS system's content time, mirroring the envelope's
```

The name mirrors the envelope field it is the per-system version of, the same way
`SystemFreshness.fetched_at` mirrors the envelope's. With it:

- the first clause is a comparison this block can already express, `fetched_at` minus
  `feed_timestamp`, which is `_feed_age` applied per system instead of per envelope;
- the second clause becomes a fact about the map rather than an inference, because a
  healthy contributor and a lagging one now differ in a field rather than in nothing;
- the subway arrivals union gets its content-time analogue of
  `_oldest_contributing_fetched_at`, with the same rules already argued there: the worst
  contributor answers, a group contributing nothing at this station does not participate,
  and one contributor that has never decoded makes the answer `None` rather than letting
  the others speak for it.

**Metro-North's row stays null and must be allowed to.** `RAILROAD_FRESHNESS_SYSTEMS`
excludes MNR from the envelope's `feed_timestamp` for a measured reason, and the per-system
block inherits that exclusion rather than quietly reintroducing the lagging clock one level
down.

**The five arrivals envelopes need two of the three timestamps they are missing.**
`StationArrivals`, `RailroadStationArrivals`, `PathStationArrivals`, `NjtStationArrivals`
and `FerryStationArrivals` each carry `fetched_at` alone. They need `served_at`, which
every vehicle envelope already has and which exists precisely so a stuck poller is visible,
and they need the `systems` map so the board can name which contributor is behind. The
per-row `observed_at` from 3.1 does the rest.

---

## 4. What changes where

Every subsection ends with the same line: **what is asserted today that this contract makes
false.** That is the useful form, because a design that only lists additions hides the
places where something currently correct stops being correct, and one subsection below
answers "nothing", which is itself worth knowing.

### 4.1 The models

| Model | Change |
| --- | --- |
| `Vehicle`, `Train`, `RailroadTrain`, `PathTrain`, `NjtTrain`, `FerryBoat` | `+ observed_at`, `+ provenance`. `FerryBoat` already has the value under the name `updated_at` (Q1). |
| `Arrival`, `RailroadArrival`, `PathArrival`, `NjtArrival`, `FerryArrival` | `+ observed_at`, `+ provenance`. A prediction is an observation. |
| `Alert` | `+ observed_at`, `+ provenance`, the latter only ever as-sent or `retained`. |
| `AlertFeed` | `+ feed_timestamp`. It is the SIXTH clockless envelope, not a seventh arrivals one: the six vehicle envelopes all carry a content clock and the five arrivals envelopes plus this one carry none. |
| `SystemFreshness` | `+ feed_timestamp`, per 3.5. The one field the acceptance case cannot be written without. |
| `StationArrivals`, `RailroadStationArrivals`, `PathStationArrivals`, `NjtStationArrivals`, `FerryStationArrivals` | `+ served_at`, `+ systems`. Five envelopes, one omission committed five times. |

Every new field is optional-with-a-default on the wire, following the convention
`SubwayFeed.systems` already states: "Optional so the field can be added without breaking a
client that predates it." A browser holding the old frontend against the new backend
ignores them; a browser holding the new frontend against the old backend sees them absent,
which is the `unknown` case 3.1 reserves and 3.2 renders as "age unknown".

**Asserted today that this makes false:** `backend/tests/test_models.py` holds **10**
exact-field-set assertions of the form `set(Vehicle.model_fields) == set(VEHICLE)`, and
another **25** exact dict-literal equalities across `test_api.py`, `test_feeds.py`,
`test_feeds_ferry.py` and `test_feeds_path.py`, **10** of which pin `SystemFreshness` to
exactly its four fields. Those are deliberate guards against silent shape drift and they
fail on the first commit of this work, which is correct: they should be updated, not
relaxed.

**The gap is the other direction, and it is the one to fix in the same commit.** The row
models are locked; the arrivals ENVELOPES are not.
`test_station_arrivals_validates_handler_shape` (`backend/tests/test_models.py:215-223`)
only validates a four-key payload, and pydantic ignores fields it is not given, so adding
`served_at` and `systems` to `StationArrivals` passes it silently. The five envelopes this
contract widens are exactly the five with no field-set lock, which is presumably how they
came to be missing `served_at` in the first place.

### 4.2 The decoders

| Decoder | Change |
| --- | --- |
| `feeds/railroad.py` `_decode_railroad_vehicles` | Read `v.timestamp`, emit `observed_at` for LIRR and `None` for MNR, apply the LIRR age gate, and stop returning a bare list of trains that the placement pass has to second-guess. |
| `feeds/railroad.py` `_decode_railroad_feed` | Build `positioned_ids` from the SAME accepted set the GPS pass emitted, which closes N2, and read `tu.timestamp` for the prediction gate and for a placed train's `observed_at`. |
| `feeds/__init__.py` `RAILROAD_FRESHNESS_SYSTEMS` | Unchanged in value, but it now has a THIRD reader. It has two today: `railroad.py:580` (may this header drive `feed_timestamp`) and `contract_monitor.py:678` (may this header raise a staleness WARN). The per-provider policy table must not become a fourth place where Metro-North's exclusion is restated. |
| `feeds/subway.py` | A NEW join: `_decode_feed` visits only `trip_update` entities today, so the 98 VehiclePositions have to be indexed by `trip_id` first. Then `v.timestamp` onto each joined train, null onto the other 62, and the group header onto each arrival. |
| `feeds/njt.py` | Emit `provenance: "placed"` (or `"estimated"` on the interpolated segment) and the header as `observed_at`. No new clock: the header is the only one. |
| `feeds/path.py` | Read `tu.timestamp` per entity. This is the one decoder where the new clock is strictly better than the envelope clock it has. |
| `feeds/ferry.py` | Rename or alias `updated_at`, and carry the TripUpdates header onto dock arrivals rather than letting them inherit the boat clock. |
| `feeds/buses.py` | Blocked on the probe in 6.0. |
| `feeds/alerts.py` | Emit the feed clock as `observed_at`; mark retained alerts `retained`. |

**The two railroad passes must share one set, and that is the whole of N2's fix.** Today
the GPS pass rejects on the geographic box and the placement pass builds `positioned_ids`
from every positioned entity regardless, so a box-rejected vehicle is claimed by a pass
that will not draw it and excluded from the pass that would. Adding an age gate to the GPS
pass widens that hole by exactly the number of observations the gate rejects. **The age
gate must not land before the shared set.** On the committed capture N2 costs nothing today
(all 69 positioned vehicles are inside the box, which is why the audit had to move one to
lat 0, lon 0 to exhibit it), and an age gate landing first would take that hole from zero
vehicles to 41 in one commit.

**Asserted today that this makes false:** the docstring of `_decode_railroad_vehicles`
states that "`now` is unused in phase 1 (no schedule join yet); it is kept for parity with
the subway decoders and frozen by the golden test." Both halves stop being true: `now` is
used, and the golden that froze it moves.

### 4.3 The endpoints

`/api/subway-arrivals/{id}` is the reference implementation, because
`_oldest_contributing_fetched_at` already solved the hard half. It gains a content-time
sibling with the same three rules (the worst contributor answers, a group contributing
nothing here does not participate, one never-decoded contributor makes the answer `None`),
and the handler gains `served_at` and the `systems` map. The other four arrivals endpoints
follow the same shape; the vehicle endpoints change only in that their payload rows are
wider.

`/api/status` gains nothing structural. It already reports per-feed content lag, and the
new per-system `feed_timestamp` is the same number it has, moved to where the client that
never fetches `/api/status` can read it.

**Asserted today that this makes false:** the comment at `backend/routes/subway.py:85-92`
says "THE OLDEST CONTRIBUTING GROUP'S poll time, not the aggregate's" and explains that the
honest answer for a union is the worst of its parts. The reasoning survives intact; the
sentence stops being the whole answer, because poll time was never the clock that F03 was
about.

### 4.4 The frontend

| Surface | Change |
| --- | --- |
| Popup | Render the provenance word and the age line from the SERVED values instead of deriving them. `isPlacedRailroad` (`helpers.js:211`) is deleted rather than fixed. |
| Station panel | Stop computing `ageSeconds` as `now - payload.fetched_at` (`helpers.js:2193`) and read the served content clock. Per-row qualification, so a stale contributor's rows are marked and a healthy contributor's are not. |
| Marker style | `STALE_MARKER_OPACITY` becomes per-observation. Today a marker dims only when its whole SYSTEM is stale, which is why 41 stale LIRR observations sit at full opacity inside a healthy feed. |
| Glide / animation | The freeze deadline (`systemStaleAtOf`, `glideClock`) becomes per-observation too. Dead-reckoning a position from a ten-minute-old fix is the animated form of the same falsehood. |
| Live region | One write per render, not two. N6 is open on `#page-announce` and a per-observation qualifier is exactly the kind of second writer that trips it. |
| `ingestSystems` | The single door every freshness value enters through (`helpers.js:489-516`), and it reads exactly four names. Any field the contract adds to an envelope reaches no surface at all until this function changes, and nothing currently tests that it drops the rest. This is the first frontend edit, not the last. |
| The shared lag term | `systemAges` computes `ages[name] = Math.max(lag, poll, 0)` with `lag` taken from the ENVELOPE (`helpers.js:552-563`), so every system of a source shares one content-lag number. A per-system content clock replaces that term, and this is the line that makes it possible. |
| Status line | Unchanged in wording. `staleness()` already produces "railroad: MNR as of 6m ago" and the two-clause stale/blind split; it gains a third population (systems whose CONTENT is old while their poll is current) and must not merge it into either existing clause, for the same reason the two clauses were split in the first place. |

**Asserted today that this makes false:** `helpers.js:2193` computes the panel's age from
`fetched_at` and the comment above `feedAgeLine` says the line exists so a popup "must say
how old they are rather than imply liveness". The mechanism is right and the input is
wrong, so the line is currently silent in exactly the case it was written for.

### 4.5 The monitor

The contract monitor already has the closest thing to F03's acceptance check and cannot
currently see the case. `_evaluate_subway` fails a run when every live feed header is older
than `REALTIME_STALE_S` (600) and warns when some are, reading the headers directly from
upstream. What it cannot do is compare what a rider is SHOWN against what the feed said,
because the arrivals endpoint has never carried a content clock.

**And it is smaller than "add a check" sounds, because of what the monitor fetches.**
Against the deployment it reads three URLs: `/api/status` twice, `/healthz` and
`/api/njt-routes`. It fetches no rider-facing data endpoint at all, so no arrivals board and
no vehicle payload is ever inspected. It also never reads the one content-lag field
`/api/status` already publishes: `feeds[*].feed_age_s` (`backend/routes/status.py:99`) has
zero readers in the monitor. Both additions below therefore need a new FETCH, not just a
new assertion.

Two additions, both small:

1. **A served-observation age check.** Read `/api/subway-arrivals/{id}` and assert that its
   content clock is present and within the same bands the header check uses. Today this
   check cannot be written; that is the finding, not an omission.
2. **A contributor-distinguishability check.** With one group's content aged and the rest
   current, assert that the envelope reports different `feed_timestamp` values per system.
   This is the acceptance case's second clause, expressed as a probe.

A new degradation code is needed and `feed-content-stale` is not it: that code is about a
FEED header lagging, and the new state is about SERVED OBSERVATIONS being qualified or
dropped. The two can occur independently, which is the test of whether a code deserves to
exist. It belongs in `HEALTH_DEGRADED_CODES` and not in `HEALTH_GATING_CODES`, for the
reason N1 established: the status code answers "may this build go live", and old upstream
data is a property of the world rather than of the build.

**Asserted today that this makes false:** nothing. This is the one section that is purely
additive, which is itself informative: the operator surfaces were built with the right
shape and were never given the value.

### 4.6 The goldens that move

Eleven committed goldens, **2,864 records between them** (418 vehicle-shaped rows, 2,442
arrival rows, 4 alerts), compared by whole-object equality against live decoder output. Two
new fields per record rewrites all eleven:

| Golden | Records | Beyond the two new fields |
| --- | --- | --- |
| `railroad_lirr_expected.json` | 68 | **68 to 38.** 27 fresh plus 11 aged; 6 move to the placed golden and 24 leave every golden. |
| `railroad_lirr_placed_expected.json` | 56 | **56 to 62**, the 6 estimated trains arriving from the GPS pass. |
| `railroad_mnr_expected.json` | 49 | Count unchanged. Every row gets `observed_at: null`, which is the whole Metro-North policy made visible in one file. |
| `railroad_mnr_placed_expected.json` | 1 | Count unchanged. |
| `railroad_lirr_arrivals_expected.json` | 765 | Rows gain the prediction's own clock; the LIRR prediction gate may drop rows. |
| `railroad_mnr_arrivals_expected.json` | 926 | Rows gain `observed_at: null`. |
| `njt_tu_expected.json` | 68 trains + 648 arrivals | The largest golden in the repository. Every row `placed` or `estimated`, `observed_at` the header. |
| `subway_1_7_s_expected.json` | 95 | Rows gain the vehicle clock where a VehiclePosition joins and null where none does; 16 of the 98 joined observations are over 90s. |
| `path_rt_gen_a_expected.json` | 53 trains + 53 arrivals | Every row gains its own per-trip clock, which is new information the golden has never held. |
| `ferry_rt_expected.json` | 28 boats + 50 arrivals | `updated_at` resolves to whatever Q1 decides; the 50 dock rows gain the TripUpdates clock. |
| `alerts_mnr_expected.json` | 4 | Feed clock and provenance. |

The two count changes in the LIRR goldens are arithmetic from section 3.4's policy, stated
here so a regeneration that produces different numbers is a signal rather than a surprise.
They transfer cleanly because `railroad_lirr_expected.json` records `now: 1782006915.0`,
which is the capture's own header: the clock the golden test runs the decoder against is
the same clock every age in this document was measured against.

**One model-level fact belongs here rather than in 4.1, because a reproduction already pins
it:** `models.RailroadFeed` does not merely lack an observation time, it STRIPS one. Adding
`observed_at` to a cached train and serving it through `/api/railroads` drops the key, and
`f01_lirr_gps_observation_age.py:412` asserts exactly that. So the field cannot be smuggled
in through the cache; it lands in the model or it lands nowhere.

**Asserted today that this makes false:** `railroad_lirr_expected.json` currently asserts
that 68 positioned LIRR vehicles are served. Twenty-four of those rows are the finding.
F02's fix has already established the pattern for this: the invariant belongs in
`test_feeds_railroad.py` as a law asked of the decoder rather than of the golden, so a
recapture cannot bless the old behavior back in.

### 4.7 The audit reproductions

`f01_lirr_gps_observation_age.py` and `f03_arrivals_content_freshness.py` both exit 0 today
because the defects hold. Under the directory README's own rule, a fixed finding's script
becomes the regression check on the FIX and must carry the before and the after. Both flip
when this work lands, and both should be rewritten in the F02/F05/F10/F12 shape rather than
deleted: the measured before-values in this document are the ones they should pin.

**Asserted today that this makes false:** both scripts exit 0 precisely because the defect
holds, so each one's success is currently a report that a rider is being misled. That is
the intended design of the directory (a red run means the audit table is stale), and it is
worth saying out loud that these two are the only files in the repository whose passing
this work is supposed to break.

---

## 5. What this does not decide

**It does not re-derive the 90 second budget.** `FEED_STALE_AFTER_S` has a worked
derivation at THE FRESHNESS BUDGET, DERIVED and NJ Transit is the tightest-margin system
under it. This contract borrows the number and inherits the instruction that goes with it:
if 90 ever moves, that derivation is what gets re-checked first.

**It does not change retention.** `FEED_RETENTION_MAX_S`, `FEED_RETENTION_ENABLED` and the
rule that retained data must be drawn AS stale are unchanged. This contract borrows the ten
minute horizon and the argument behind it, and adds nothing to the retention decision
itself.

**It does not cover static data staleness.** A calendar-expired NJ Transit archive served
at HTTP 200 (F07) is a different clock on a different cadence, and it belongs with F06 to
F08 and F13 in Release 2. "Fresh" here always means an observation of a moving thing.

**It does not settle AirTrain (F04).** AirTrain has no feed, so it has no observation to
date. It shares one word with this vocabulary ("scheduled") and nothing else, and the F04
work is gated on collecting current official data rather than on this contract.

**It does not close N6.** The `#page-announce` two-writers-in-one-task hazard stays open,
and a per-observation qualifier is precisely the kind of second writer that trips it, so
whoever builds section 6.2 should read N6 before touching a live region.

**It does not propose changing a provider.** The bus feed stays OneBusAway's GTFS-Realtime
endpoint. Whether a SIRI source would give us a per-observation time is a real question and
a different project.

**It adds no error text to `SystemFreshness`.** That model's docstring excludes it
deliberately ("NO ERROR TEXT LIVES HERE") to keep the leak surface where it already is, and
one new numeric field does not reopen that.

### The open questions

Each is phrased so a yes or a no settles it.

**Q1. Should `FerryBoat.updated_at` be renamed to `observed_at`, so one name covers every
system?** It is a served field, so renaming it is a wire change on an endpoint that already
ships. No means ferry keeps `updated_at` and every other system gets `observed_at`, which
is two names for one concept in one payload set. *Recommendation: yes, with the old key
served alongside for one release.*

**Q2. Should `OBS_MAX_S` be 600, adopting the retention cap's number and its argument?** On
the committed LIRR capture that removes 24 of 68 trains from the map. No means picking a
looser horizon: at 1800s the same capture keeps 15 of those 24, drawn dimmed and labeled,
and 9 disappear; at 3600s, 20 of them, and 4 disappear. *Recommendation: yes. The ten minute line is already this repository's
answer to "when does old data become a ghost", and having two different answers to that
question at two levels of the same system is how they drift.*

**Q3. Should `retained` win the provenance field, losing what the position originally
was?** Yes gives one flat enumeration and relies on `SystemFreshness.retained_since` for
the timing. No means provenance keeps the derivation (`live-gps`, `estimated`, `placed`)
and retention is carried by a separate boolean or by the existing per-system block.
*Recommendation: yes, because "not in the current decode" is the fact that changes what a
rider should believe, and a five-value enumeration that a client can switch on beats a
matrix.*

**Q4. Should the estimate step use `OBS_FRESH_S` (90) rather than a looser prediction
threshold?** Yes yields 6 estimated markers on the committed capture; 300s yields 8 and
600s yields 11, and in every case the same 24 trains are dropped. *Recommendation: yes. One
threshold is easier to defend than two, and an estimate built from a nine minute old
prediction is labeled honestly and still nearly worthless.*

**Q5. Should a system with no observation clock show "age unknown" on every popup?** Metro-
North is 33 markers on the committed capture (from 49 positioned entities) and none of them
can be dated. Yes means every
Metro-North popup carries a qualifier that never goes away, which risks teaching riders to
ignore the qualifier everywhere. No means Metro-North popups stay silent about age and rely
on the system-level status line, accepting that silence means "current" on every other
system and "unknown" on this one. *Recommendation: no, with the per-system line carrying it
instead, and this is the question in this list I am least confident about.*

**Q6. Should the bus probe block the build?** Yes means section 6.0 runs first and buses
ship with a real policy row. No means buses ship on the "no observation clock" row and are
revisited, which is safe but leaves the largest fleet in the application permanently
unqualified. *Recommendation: yes, and it is one probe.*

**Q7. Should a rider be told when observations were suppressed?** Today a rider cannot
distinguish "no trains on this branch right now" from "we dropped 24 of them for age". Yes
means a count reaches a surface, probably the status line, in the same grammar as
"not reporting". No means the map is simply quieter and the drop is visible only to an
operator. *Recommendation: yes for the status line, no for the map, since a count beside
the system name costs nothing and a per-marker ghost costs the thing we are fixing.*

**Q8. Should the first provenance value be named `live-gps`, accepting that a prediction
and an alert have to borrow a position word?** Yes keeps the code value and the shipped
rider string "live GPS" identical, which is worth something. No renames it to `reported`
and accepts that the code value and the rider word differ, which this codebase already does
freely (`at-station` renders as nothing a rider ever sees). *Recommendation: no. A closed
enumeration is read by code, and `reported` is the only one of the two that is true of all
three observation kinds; the rider string does not change either way.*

---

## 6. The order of the build

Four steps. Each states its acceptance in the auditor's terms, because those are the
sentences this work will be judged against and paraphrasing them loses the second clause
that is usually the hard one.

### 6.0 The bus probe (prerequisite, not a build step)

One probe of `https://gtfsrt.prod.obanyc.com/vehiclePositions`, counting how many vehicles
carry `VehiclePosition.timestamp` and how far each sits from the header, and a committed
capture so the number is re-derivable. Ten minutes of work; it is listed separately because
it is the only row of the section 3.3 table that is a measurement nobody has taken rather
than a decision nobody has made.

**Acceptance:** the buses row of the age policy table is written from a measurement and
cites the capture, like every other row.

### 6.1 `SystemFreshness` and the models

`observed_at`, `provenance` and `SystemFreshness.feed_timestamp` on the models; the
decoders filling them; the endpoints serving them. **No rider-visible change, and no age
gate.** This step is deliberately inert: it produces the values and nothing consumes them
yet, which is the same discipline C2 PR1 used when it shipped the per-system blocks with
retention still off.

**Acceptance, in the auditor's terms:** *other healthy contributors remain
distinguishable.* Concretely: an envelope whose one contributor is serving ten minute old
content, with every other contributor current, reports a different `feed_timestamp` for
that system than for the others, `/api/status` and the envelope agree about which one it
is, and the 10 model field-set assertions are updated rather than removed, with a new one added
for each arrivals envelope, which has never had one.

### 6.2 F03, the arrival boards

The content clock reaches the five arrivals envelopes, the contributor-aware selection gets
its content-time sibling, and the panel and the popup render from the served value instead
of from `now - fetched_at`.

**Acceptance, in the auditor's terms:** *repeatedly returning an old, valid HTTP-200 feed
makes its countdowns visibly qualified as stale; other healthy contributors remain
distinguishable.* The existing reproduction already builds the first half's world: the
committed subway capture re-stamped 600 seconds behind the poll clock, served through the
real refresh path and the real ASGI app. It currently measures that
`/api/subway-arrivals/219` reports eight of eight groups `ok` and no content clock; after
this step it measures the qualifier, and the second clause needs one healthy group beside
the aged one so "visibly qualified" is a statement about some rows rather than all of them.

### 6.3 F01, the old GPS observations, with N2

The shared accepted-GPS set first, then the age gate, then the fallback ladder, then the
per-observation rendering.

**The order inside this step is not a preference.** The shared set must land before the age
gate, or the gate widens N2's hole by exactly the number of observations it rejects, which
is 41 on the committed capture. And the rendering must land in the SAME COMMIT as the gate,
for the reason `FEED_RETENTION_ENABLED` states about its own pairing: a gate without its
rendering trades a ghost for an unexplained absence, and 24 trains vanishing from the map
with nothing said about it is not obviously the better of the two.

**Acceptance, in the auditor's terms:** *a fresh header containing an old vehicle
observation never produces an unqualified live-GPS marker; a usable prediction can produce
a clearly labeled estimated marker instead.* Plus N2's own, which the audit stated as a
latent defect rather than an acceptance and which this step has to satisfy anyway: *a
positioned vehicle the GPS pass rejects is considered by the placement pass, and appears on
some surface or on none for a stated reason.*

Measured expectations for that step, from section 3.4, so the reproduction has numbers to
pin: 68 served becomes 27 unqualified, 6 estimated, 11 qualified and 24 absent, and the
placed golden gains the 6.

### What this order leaves for later

F04 (AirTrain) stays item 10 of the Release 1 order, unblocked by this and gated on
collecting current official data. F06 to F09 and F13 stay in Release 2. N6's
`#page-announce` half stays open, with the note in 4.4 that this work walks past it.

---

**Nothing above is built.** This document is the design for review that the Release 1 order
put at item 7, and the two items it blocks are items 8 and 9. Read it before anything is
built from it, and settle the eight questions in section 5 first: three of them change the
data model, four change what a rider sees, and one decides whether a probe blocks the
build.
