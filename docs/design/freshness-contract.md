# The freshness and provenance contract

**Status:** BUILT, section 6 in order, one branch per step. It was a design for review on
`claude/design-freshness-contract` (PR #108), which changed no code, no test, no fixture and
no golden, and that sentence is what this line replaces. N2's shared accepted set landed
first on `claude/n2-shared-gps-set` (PR #109), then 6.0's bus probe on
`claude/bus-observation-probe` (PR #110), 6.1 on `claude/freshness-6-1-models` (PR #111),
6.2 on `claude/freshness-6-2-boards` (PR #112), and 6.3 on `claude/freshness-6-3-positions`,
which closes F01 and is the branch this line is written on. **A "today" or a "currently"
below is the world this design was written against unless a sentence beside it says
otherwise.** Corrections made after a step landed are marked at the sentence; the open
questions in section 5 are deliberately not, because a question rewritten after it is
answered stops being evidence of what was actually asked. **Amendment 1** (A03 and A10 of
the 2026-10-12 audit, the last section of this document) is PROPOSED on
`claude/design-freshness-amendment-1`, for review before anything is built from it, and
changes nothing yet: sections 1 to 6 and the Decisions of 2026-09-12 read as they did
before it, and the body is amended in place only on the date its own decisions land. Its
citations are at `1d17aa0`, and the application code is `1d17aa0`'s: the branch's fork
commit is `74bd2ee` (PR #131's merge), and the two merges between change no application
code.

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

The frontend then compounds it rather than catching it. The panel's staleness line was
computed at `frontend/helpers.js:2193` in the tree this was written against (`ageSeconds` is gone
now; section 4.4's table records what replaced it):

```js
ageSeconds: payload.fetched_at == null ? null : now - payload.fetched_at,
```

That is the age of OUR ACQUISITION, not the age of the content. `frontend/stations.js:665`, in
that same tree, turned it into the rider-facing sentence, and the popup renders the same line from the same
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

`_decode_railroad_vehicles` read `v.HasField("position")`, the geographic box, the
cancellation set and the route join. It never read `v.timestamp`, and its own docstring
recorded that `now` is unused in that phase. All 69 observations therefore left the decoder
wearing the feed's freshness and nothing of their own.

**Since F02 landed, 68 of the 69 were served** (the canceled 5-train is dropped before
emission, for cancellation and not for age), so **41 stale observations reached riders
unqualified**. That was the live number.

**BUILT AT 6.3, AND THE TWO PARAGRAPHS ABOVE ARE THE BEFORE.** The decoder reads
`v.timestamp` on every row (`_railroad_observed_at`, `backend/feeds/railroad.py:98`, emitted
at `:663`) and emits only what the position ladder draws at a vehicle's own position, and
the docstring that recorded `now` as unused now says the opposite in capitals: "THE AGE GATE
IS APPLIED HERE, AND ITS CLOCK IS NOT `now`" (`railroad.py:572`). The same bytes serve **38**
GPS rows, the oldest served fix is **593s**, 6 aged trains are drawn as estimates, 11 are
drawn qualified and dimmed, and 24 are withheld and counted, so the 41 that reached a rider
unqualified are now **none**. The counts are asked of the decoder rather than of the
goldens: `test_the_lirr_capture_orders_27_6_11_0_24` in
`backend/tests/test_position_ladder.py`, and the served body in
`backend/tests/test_f01_positions.py`.

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

**N2 rode here, and is closed.** `positioned_ids` was built from every entity with a
position, with no geographic box and no age check, and the placement pass skipped any trip
in it. It was therefore STRICTLY BROADER than the set the GPS pass actually emitted. A
positioned vehicle rejected by the box appeared on no surface at all: not as GPS, because
the box dropped it, and not as an estimate, because `positioned_ids` still claimed it. An
age gate on the GPS pass would widen that hole by exactly the number of observations the
gate rejects, unless the two passes were made to share one accepted set. That was the same
edit, and it landed first, on `claude/n2-shared-gps-set` (PR #109): `positioned_ids`
(`backend/feeds/railroad.py:1012`) is now narrowed by `_accepted_as_gps` at `:1033`, and
6.3 widened that one function with the position ladder rather than going around it.

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
disagree about them completely: one supplies both, THREE supply a vehicle clock and no
prediction clock, one supplies a prediction clock and has no vehicles at all, one supplies
neither while having the best header in the set, and one supplies a value that carries no
information at all.

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
| Buses | GPS position | `VehiclePosition.timestamp` | **2136 of 2136 set**, 47 distinct values over 104s | **No** |
| Alerts | Alert | none in the GTFS-RT alert message | 9 and 37 alerts, no per-alert time | n/a |

### 2.1 Long Island Rail Road

**The header is the true feed-generation time and every vehicle dates itself
independently.** That is stated in the code (`backend/feeds/railroad.py:43-50`) and it
holds on the capture: the header is `1782006915` and not one of the 69 positioned vehicles
carries that value. The ages run from 4s to 53676s with a median of 203s, so the
distribution is not a couple of outliers behind a healthy fleet: half the fleet is over
three minutes old and more than a THIRD is over ten minutes old (25 of 69).

The layout is SEPARATE ENTITIES: 69 vehicle-only, 132 trip-update-only, none combined. The
join is by `trip_id`, and it is total on this capture: all 69 positioned vehicles join a
trip update, including all 42 of the stale ones.

**The predictions date themselves too, and that matters more than it looks.** 127 of the
132 trip updates carry `trip_update.timestamp`; the median age is 887s and the oldest is
52538s. So "fall back to a prediction" is not automatically an improvement over a stale
GPS reading, and LIRR is the one system where we can tell the difference before deciding.

LIRR is therefore the only provider that supplies an independent observation time for
BOTH kinds of observation, and when this was written the decoder read neither. It reads both
from 6.1: `_railroad_observed_at` (`backend/feeds/railroad.py:98`) for the vehicle and
`_prediction_observed_at` (`:775`) for the trip update.

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

**THE TAIL IS A FACT ABOUT THE FEED AND NOT ABOUT THE MAP**, which is the correction the
erratum under section 4.6 records in full. Only 2 of the 95 SERVED trains carry one of
those 16 observations: the other 14 belong to trips the placement pass never draws,
having not started or having no resolvable upcoming stop. Every figure in this paragraph
is about the feed, and 6.3's gate is sized from the served number instead.

**Two facts make the subway case more work than the table suggests.** `_decode_feed`
(`backend/feeds/subway.py:85-87`) walks `trip_update` entities and nothing else, so the 98
VehiclePosition entities are not merely unread, they are never visited: joining a vehicle
clock to a train is a new join, not a new field read. And the join does not cover the
fleet. All 98 VehiclePositions join a trip update by `trip_id`, but **62 of the 160 trip
updates have no VehiclePosition at all**, so 62 subway trains fall back to their group's
header while 98 carry a real per-observation time. Both are age-gated and neither is null:
the subway is the one system where two different clocks answer the same question for
different trains.

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

**It populates it on every vehicle, and the clock is a good one.** The probe section 6.0
called for was taken on 2026-09-12 and committed as
`backend/tests/fixtures/bus_vehicle_positions.pb` (280965 bytes, 2136 entities, header
`1789230515`). Measured from those bytes: **2136 of 2136 entities carry a position and all
2136 carry `VehiclePosition.timestamp`**, across 47 distinct values spanning 104 seconds.
The ages against the header run 0s to 104s with a median of 15s and a p95 of 29s; **one**
observation of 2136 exceeds 90 seconds and **none** exceeds 600.

**It is not the Metro-North pattern, and the probe says so against the Metro-North capture
rather than by assertion.** None of the 2136 is ahead of the header and exactly 20 tie it,
which is not a copy but an artifact of how the header is built: **the header equals the
newest observation exactly**, so it is a generation time computed from the content rather
than a write time advancing on its own, which is the PATH failure mode this contract has to
tell apart. By the origin's own clock the feed it served was 5 seconds old (its `Date`
header minus the feed header); our own read instant sits 15.7s behind it, but 0.247s of
that was round trip and the rest is skew between two machines, so the same-clock number is
the one the policy uses. `docs/reviews/audit-2026-09-05/probe_bus_observation_clock.py`
re-derives all of it from the committed bytes and exits non-zero if any of it stops
holding.

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
`observed_at` and a provenance of `reported` rather than `retained`, which is one of the
things the enumeration in 3.1 has to be able to say.

### 2.9 Reading the table sideways

Five things fall out, and they set the shape of the contract:

1. **No single rule can cover these providers, or even one provider's whole fleet.** LIRR
   dates everything, Metro-North dates nothing, subway dates 98 of its 160 trips and not
   the other 62, PATH dates the prediction and has no vehicle, NJ Transit dates neither and
   has a very good header, ferry dates the boat and not the dock, and buses date every one
   of 2136. A per-provider policy TABLE is not a convenience here, it is the only correct
   form.
2. **Absence of a PER-OBSERVATION clock has to be a first-class state, and it is not the
   same as having no clock at all.** `observed_at` is null for Metro-North and for nothing
   else. Subway predictions, the subway trains with no VehiclePosition, NJ Transit and ferry
   docks have no per-observation time either, but they do have a HEADER their provider
   actually sent, and 3.3 ages them against that rather than against nothing. The distinction decides which rows are age-gated, and therefore, through
   clause (c) of the rule in 3.2, what a rider is told.
3. **A per-observation time is not automatically better than a header.** Metro-North's
   per-vehicle value is a copy, and using it would be strictly worse than using nothing.
   The policy has to name which clock each provider's rule reads.
4. **Five clocks already exist and four of them are read by nothing.** LIRR's, the
   subway's, PATH's and the buses' are decoded past and discarded; ferry's is carried all
   the way to the wire as `updated_at` and read by no surface. Part of this contract is already
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
non-nullable field would force a value on all of it, computed by us rather than sent by the
provider, and a rider-facing qualifier rendered from a number we made up is the precise
failure this contract exists to prevent. `None` means "this provider does not date this
observation".

**Null is narrower than "has no per-observation clock", and 3.3 is where the difference is
decided.** `observed_at` is null for Metro-North positions and predictions, and for nothing
else. Subway predictions, the 62 subway trains with no VehiclePosition, NJ
Transit and ferry dock arrivals have no per-observation clock either, but their providers DO
send a header, so 3.3 ages them against that and their `observed_at` is the header rather
than null. Only the first group is null, and only the first group is non-gated.

**`observed_at` was not a new name.** The audit's own reproduction already used it,
asserting that `models.RailroadFeed` STRIPPED an `observed_at` added to a cached train, so
the field name was already written down and the model already had a documented behavior
toward it. 6.1 reversed the behavior rather than working around it:
`RailroadTrain.observed_at` is a served field (`backend/models.py:127`), and
`docs/reviews/audit-2026-09-05/f01_lirr_gps_observation_age.py` records the flip. The line
number that citation once carried is not repeated here, because that script is rewritten
whenever its finding moves.

**`provenance` is a closed enumeration of five values.** The names are lowercase single
words, needing none of the hyphens the health codes and `NjtTrain.status` carry, and a value
outside the set is a decoder bug rather than a client's problem. It would be the first closed set in the file: `backend/models.py`
contains no `Enum` and no `Literal` anywhere, and `NjtTrain.status`, `FerryBoat.status`,
`Alert.effect` and `Alert.cause` all ship as bare `str`. Two of those are GTFS-RT
pass-throughs that are deliberately open, so whether provenance is typed or merely
documented as closed is a real choice, and the honest default is to type it, because unlike
those two nothing upstream can widen it.

| Value | Means | Today's implicit equivalent |
| --- | --- | --- |
| `reported` | The provider sent this as observed: a GPS coordinate, a prediction, an alert. | `isPlacedRailroad(t) === false`, and nothing at all for a prediction or an alert |
| `estimated` | Computed by interpolating a prediction between two known points. | the `prev_*`/`next_time` anchors being populated |
| `placed` | A stop's own coordinates, from a prediction or the timetable. | `isPlacedRailroad(t) === true`, `NjtTrain` always, `PathTrain` always |
| `retained` | Carried forward from an earlier poll; not in the latest decode. | `SystemFreshness.retained_since`, per system only |
| `unknown` | Provenance could not be determined. | nothing |

**The client already computes this enumeration, from the shape of the fields, and that is
the thing to stop.** This was the whole of the client's provenance system, at
`frontend/helpers.js:211` in the tree this was written against (the function is gone; every line
number in this diagnosis is that tree's, not today's):

```js
function isPlacedRailroad(t) {
  return t.stop_id != null;
}
```

A rule that reads presence-of-a-field as meaning-about-the-world is exactly what breaks the
moment a decoder starts filling `stop_id` for a GPS train, and it is duplicated per system
on the client. Moving it to the server is most of the work of this contract and none of the
risk. **Done at 6.3:** `isPlacedRailroad` is deleted rather than fixed, the served
`provenance` is read instead, and `frontend/positions.test.js` asserts the function is gone
from every frontend file rather than merely unused. The old anchor is now popup geometry.

**Two notes on the enumeration itself.**

`retained` is the odd member, and deliberately so: the other four say how an OBSERVATION
was derived, and `retained` says when it was SERVED. A retained GPS position is both. The
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

**The first value is `reported` and not `live-gps`, and the reason is that the enumeration
is read by code over three kinds of observation.** An alert is never derived, so `estimated`
and `placed` are both wrong for one and the only values it can take are as-sent and
`retained`. The same strain shows on a prediction: an LIRR arrival time is reported, not
GPS. `reported` is true of a coordinate, a prediction and an alert alike, which no position
word is. **The rider string is unaffected**: a fresh reported POSITION still reads "live
GPS" on the surfaces that already say it, because the code value and the rider word answer
different questions and this codebase already lets them differ freely (`at-station` renders
as nothing a rider ever sees). Settled as Q8; see Decisions.

**`reported` and the shipped string "not reporting" are unrelated, and the collision is
close enough to be worth one sentence.** "{system} not reporting" (`helpers.js:2058`) means
a system that has NEVER DECODED anything, which is a statement about a feed. `reported` is
a statement about one observation: the provider sent it as observed rather than us deriving
it. A system can be reporting fine while every observation in it is `placed`, and a system
that is "not reporting" has no observations to carry any provenance at all. Nothing in the
rendering puts the two on the same surface, and nothing should start.

**NJ Transit's served-empty alerts state (N4) needs saying out loud**: zero alerts observed
at a real time is an observation of nothing, not an absence of observation, and the two
must not render the same way. It takes a real `observed_at` and `reported`.

### 3.2 The words a rider sees, defined once for every surface

**Most of this vocabulary already ships**, which is the strongest argument for making it
the contract's rather than inventing a new one. These are the literal strings in the code
today:

| Shipped string | Where | Says |
| --- | --- | --- |
| `live GPS` | `helpers.js:2358-2359` (`positionQualifier`) | a real reported position |
| `scheduled position (no GPS)` | `helpers.js:2365` (`positionQualifier`'s `placed` answer) | a position the app placed from a prediction |
| `scheduled position, no GPS` | `helpers.js:2367` (the same answer's `.spoken`) | the same fact in an accessible name |
| `scheduled (no GPS)` | *no reader since MR5, see the note at the end* | the compact form, once the railroad popup's |
| `as of {age} ago` | `helpers.js:2211` (a row's qualifier), `:2237` (a board's line), `:2056-2057` (the status line); `As of {age} ago` at `:677` (the feed strip and a vehicle popup's footer) | how old the thing just named is |
| `{system} not reporting` | `helpers.js:2058` (`staleness`'s blind clause) | a system that has never decoded |
| `feed empty, showing last known` | `helpers.js:3486`, `:3492` | a bounded empty run |
| `scheduled service (no live tracking)` | `helpers.js:3796` (the AirTrain station popup's sub-line) | AirTrain, which has no feed |

**RE-ANCHORED BY MR5, AND TWO OF THESE ROWS SAID SOMETHING FALSE AS WELL AS POINTING SOMEWHERE
wrong.** Map redesign stage MR5 grew `helpers.js` by about 940 lines and rebuilt every popup, so
every anchor in this table pointed at unrelated text until this pass; the phase's own rule
(`docs/reviews/map-redesign-rounds.md`, "a citation is re-anchored whenever the code it targets is
touched") is what this is. Beyond the numbers: the `live GPS` row named `systems/railroad.js` as a
renderer, and ruling Q1 took the words out of that file (what is left there is comment); and
`scheduled position (no GPS)` was attributed to "NJT and PATH popups" when it is produced centrally
by `positionQualifier` and now reaches EVERY vehicle popup through `positionWords`. A citation that
resolves to the wrong place is a claim that reads as evidence, which is why this table is worth a
pass of its own rather than a note.

**The contract adds three words and fixes one omission.** Building 6.2 added a fourth, the
board's form of the per-system clause (the last row), recorded under 6.2 as an amendment
rather than a decision.

| Provenance / state | The rider word | Note |
| --- | --- | --- |
| `reported`, fresh | *(nothing)* | Silence means current. That is the grammar every surface already uses and the reason the F03 defect is invisible. |
| `reported`, aged, a position | `live GPS, as of {age} ago` | Reuses both shipped halves, verbatim. |
| `reported`, aged, a prediction | `as of {age} ago` | The same age line without the position clause, which would be false of a prediction. An alert is never aged: its row in 3.3 is retention-only. |
| `estimated` | **`estimated from a prediction`** | NEW. The one case the codebase has no word for. |
| `placed` | `scheduled position (no GPS)` | Unchanged, and already correct on NJT and PATH. |
| `retained` | `showing last known, as of {age} ago` | Joins a shipped clause to a shipped clause. |
| `unknown` | **`age unknown`** | NEW. The positive statement that silence cannot make. |
| `observed_at` null, on an age-gated row | `age unknown` | The provider normally dates this and did not. An anomaly, so it is said at the observation. |
| `observed_at` null, on a non-gated row | *(nothing at the observation)* | The provider does not date this at all. Said ONCE on that system's line instead; see the rule below. Metro-North is the only system in this state today. |
| A whole system with no observation clock | **`{system} position age unavailable`** | NEW, and per-system rather than per-observation. A clause on the source's line, so the railroad source reads "railroad: MNR position age unavailable". |
| A whole system with no observation clock, on its arrival board | **`{system} prediction age unavailable`** | NEW in 6.2: the row above in its prediction form, since a board carries predictions and "position" would be false of them, naming the system in the rider's word ("Metro-North prediction age unavailable") because the panel speaks it. It rides the board's own stale-poll line and never raises it, so a board whose poll is current says nothing. |

**The rule, stated once for all surfaces:** a marker, a popup, a panel row or a board row
MUST carry its word when any of the following holds. (a) Its provenance is not `reported`.
(b) Its `observed_at` is older than `OBS_FRESH_S`, read against the clock its row in the
section 3.3 policy table names. (c) Its `observed_at` is null AND that row is age-gated. There is no surface exempted
for being small, and no surface that gets its own phrasing. `humanizeAge` stays the single
age formatter so the map, the popup, the panel and the live region cannot word the same age
differently, which is the discipline `stalePopupLine` was written to enforce and which this
extends rather than replaces.

**ERRATUM, 2026-09-19 (map redesign stage MR3, the operator's ruling on that stage's finding
N3): a row that clause (c) applies to is DRAWN as not-fresh, not as fresh.** Clause (c) made an
undated observation on an age-gated row SAYABLE and did not say what it is drawn as, and the
answer the code had fallen into was "at full opacity", because `markerOpacity` read an age and
there was none. So a row this section calls an anomaly rendered exactly like a fix five seconds
old, and the map redesign's own marker table (brief 3.1, row 6) could not be drawn at all.

**The rule is that dimming carries NOT-FRESH, not an age.** A dimmed marker has never meant
"this is N seconds old"; it has meant "do not read this as current", which is what an
observation with no clock earns on a row whose provider normally sends one. `helpers.js`
`AGE_UNKNOWN` is that value and `staleAge` names it as its own clause rather than leaving it to
arithmetic; `observationDimAge` decides it, from the ROW's own stamp and its row's `gated` flag.

**What the erratum does not change.** The words: `positionQualifier` said "age unknown" before
and says it now, and no surface gains or loses a sentence. The position ladder: a step is
decided in the backend from the served row and nothing here reaches it, so the F01 world's
counts are the same on both sides of this change. The glide freeze: `observationStaleAt` still
returns null for a clockless row, so such a marker freezes on its system's deadline exactly as
before. It is opacity, and only opacity. And it does not reach a non-gated row, so Metro-North's
33 markers stay bright, for the same reason clause (c) is narrow.

**`/healthz` KEEPS THE OPERATOR'S RULE WHILE THE RIDER SEES "age unknown" AND A DIMMED MARKER,
and the two disagreeing is the decision rather than an oversight** (the operator's ruling R-b,
2026-09-19). The subway is the one age-gated family whose 3.3 row has a fallback: its clock is
`vehicle.timestamp`, *else the contributing group header*. So a subway row the backend could date
only from a header reaches the page with a null `observed_at` while the feed is, for the
operator's purposes, a dated feed that is being read correctly: `/healthz` must not go yellow for
it, because nothing is wrong upstream and an alert nobody can act on is worse than no alert.

The rider's question is a different question. It is not "does this feed date its rows" but "can
this train's position be dated", and for that row the answer is no. So the rider gets clause
(c)'s sentence, "live GPS, age unknown", and a marker at `STALE_MARKER_OPACITY`. A single answer
would have to be wrong for one of them: made an operator answer it hides a real gap from the
rider, and made a rider answer it raises a false alarm for the operator. `frontend/positions.test.js`
asserts the header-less subway case as intended behaviour, so it cannot be mistaken later for the
bug it looks like.

**Clause (c) is narrow on purpose, and this is the amendment Q5 settled.** A provider that
never dates its observations is not an anomaly, it is a property of the provider, and
stamping "age unknown" on all 33 Metro-North markers and on its 926 arrival rows would put
a permanent qualifier on an entire commuter railroad. A qualifier a rider sees everywhere, always, is one a rider stops
reading, and it would devalue the same words on the LIRR train where they mean something
real. **So a non-gated row is silent at the observation and states the fact once, on that
system's line**, in the same place and the same grammar `staleness()` already uses for "as
of {age} ago" and "not reporting". It is the one qualifier in this vocabulary that
describes the provider rather than the observation, which is exactly why it belongs there
and not on the markers.

**The rule's two halves cover every row of 3.3 once each, and that is worth checking rather
than assuming.** Twelve rows are age-gated, so clause (b) speaks for them and clause (c)
catches a null that should not have been there. Metro-North's two rows are the only ones a
whole system's line has to speak for. Buses were undecided until 6.0, whose probe made their
row gated like the rest, and alerts are carried by retention. Nothing falls between, which
was not true of the first draft of this rule: it left the 62 subway trains with no
VehiclePosition qualified nowhere, until measuring
`feeds/subway.py:155-175` showed they are placed from their trip update like every other
subway train and are therefore dated by the group header rather than undated.

**One implementation hazard, measured, that does not change the decision but decides how it
lands.** `staleness()` returns `null` when nothing is stale and nothing is blind
(`frontend/helpers.js:2012`), and its own header states the rule it was written under: "THE
COMMON CASE MUST NOT GET NOISIER". A population that is PERMANENT rather than transient
breaks that: Metro-North never has an observation age, so a fourth clause added the same way
as the first three would make the railroad line non-null forever, and a status line that
always says something is a status line nobody reads. **The clause should therefore ride the
line without being able to raise it**: it appears when that source's line is already
rendering, and a source whose only condition is "this provider does not date its
observations" stays silent. The cost is that a rider on a wholly healthy day never learns
it, which is the right trade for a fact that is true of every Metro-North marker on every
day and is therefore not news. Whoever builds 6.3 should treat this as part of the rule
rather than as a detail, because implementing it the obvious way produces permanent
chatter.

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
| LIRR | Prediction | `trip_update.timestamp`, else the feed header | **Yes** | 127 of 132 independent, 88 of them already over 90s. The other 5 are the capture's canceled trips, which this pass drops anyway; the header is named here because the decoder reads it and this table is the place a reader looks. It is a real number the provider sent, not one computed by us, and it is the same fallback every header-dated row in this table already uses. |
| Metro-North | GPS position | none | **No** | The stamp is a copy of a header that lags 2 to 4 minutes. Gating on it would mark a live fleet stale. `observed_at` is null. |
| Metro-North | Prediction | none | **No** | 0 of 119 carry a timestamp. `observed_at` is null. |
| Subway | Position, joined to a VehiclePosition | `vehicle.timestamp` | **Yes** | 98 of 160 trips join one; 16 of those are already over 90s. |
| Subway | Position, no VehiclePosition | contributing group header | **Yes** | 62 of 160 trips. Every subway position is placed from its trip update whether or not a VehiclePosition exists (`feeds/subway.py:155-175`), so a missing vehicle entity leaves the train dated by the same header as a prediction, not undated. |
| Subway | Prediction | contributing group header | **Yes** | 0 of 160 self-dated; the group header is the only honest clock and is already selected per contributor. |
| NJ Transit | Placed position | feed header | **Yes** | The header is a good clock (lag 9s to 23s at peak, measured). |
| NJ Transit | Prediction | feed header | **Yes** | Same clock, same derivation. |
| PATH | Placed position | `trip_update.timestamp` | **Yes** | 53 of 53 and 55 of 55 independent. NOT the envelope's `feed_timestamp`, which is a write time that advances regardless. |
| PATH | Prediction | `trip_update.timestamp` | **Yes** | Same field, same reason. 12 of 55 on the rush capture already exceed 90s. |
| NYC Ferry | GPS position | `vehicle.timestamp` (served today as `updated_at`) | **Yes** | 28 of 28 independent. The field already exists; only the rule and the rendering are new. |
| NYC Ferry | Dock arrival | TripUpdates header | **Yes** | 0 of 50 self-dated, and the audit's remedy names this explicitly: age against the TripUpdates clock, not the boat clock. |
| Buses | GPS position | `vehicle.timestamp` | **Yes** | Measured 2026-09-12: 2136 of 2136 self-dated, 47 distinct values over 104s, median 15s, p95 29s, 1 over 90s and 0 over 600s. The capture is `backend/tests/fixtures/bus_vehicle_positions.pb`; `probe_bus_observation_clock.py` re-derives it. |
| Alerts | Alert | alert feed header, carried but not gated | **No**, retention only | GTFS-RT alerts carry no time. `active_period` is about the world, not the observation. `observed_at` is served so a consumer has it; no age rule fires, and what a rider is shown about an old alert is the retained SOURCE label F11 already ships. |

**Twelve of the fifteen rows are age-gated and three are not**, and the split is the input
to clause (c) of the rule in 3.2, so it is worth reading off precisely. TWO rows have no
clock of any kind behind them, and both are Metro-North's. A third, alerts, is governed by
retention rather than by age because a GTFS-RT alert carries no time to gate on.
**Metro-North is therefore the only SYSTEM that is wholly undated**, which is what makes the
per-system clause in 3.2 apply to exactly one source today.

**The buses row was the last one written and it is the reason that sentence is short.** It
said "pending a probe" until 2026-09-12; the probe ran, OneBusAway dates every observation
it sends, and the row is now the same shape as LIRR's. Had it come back the other way,
Metro-North would not have been alone and the per-system clause would have had to carry the
largest fleet in the application.

**Neither of the remaining two is a gap to be filled later.** They are measurements. Writing a rule
for Metro-North positions would require a clock Metro-North does not send.

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

1. **`reported`, unqualified.** A GPS position whose `observed_at` is within `OBS_FRESH_S`.
2. **`estimated`.** Interpolated from a prediction whose own `observed_at` is within
   `OBS_FRESH_S`, when the position is not.
3. **`reported`, qualified.** The GPS position itself, when its `observed_at` is within
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
| 1. `reported` unqualified | **27** | unchanged from today |
| 2. `estimated` | **6** | a marker labeled "estimated from a prediction" |
| 3. `reported` qualified | **11** | a dimmed marker reading "as of {age} ago" |
| 4. `placed` | **0** | (see below) |
| 5. nothing | **24** | **the marker is gone** |

**Twenty-four of sixty-eight LIRR trains disappear from the map, and that is the decision
this section exists to put in front of a reader.** Before 6.3 all 68 were drawn as live.
Under this policy 27 stay as they are, 17 become honest, and 24 stop being drawn, because
there is nothing honest left to draw: their GPS is over ten minutes old and so is every
prediction behind them.

**Step 4 never fires on this capture, and the exact reason is sharper than "the predictions
are old too".** Of the 24 vehicles whose GPS exceeds ten minutes, only 4 have a future stop
time at all, and all 4 of those trip updates are themselves over ten minutes old (1114s,
1753s, 4326s and 52538s). A DIFFERENT four carry a trip update well inside the window (27s,
62s, 82s and 529s) and are still no use, because every stop time in them is already in the
past. So the two ways a fallback could work fail separately: **a fresh prediction about a
finished trip is as useless as a stale prediction about a live one**, and no vehicle on this
capture has both halves at once. The step stays in the order because that is one capture on one evening, and
a provider whose two feeds fail independently is entirely plausible; but nobody should
expect it to rescue anything, and an acceptance test that requires it to fire would be
testing a synthetic feed rather than this one.

**Sensitivity to the prediction threshold, since this is the parameter most likely to be
argued about:** relaxing step 2 from 90s to 300s moves 2 vehicles from step 3 to step 2,
and to 600s moves 5. It never changes the 24. The estimate threshold is a choice about how
honest a label is on 6 to 11 markers; the `OBS_MAX_S` value is the choice that decides
whether 24 riders' trains exist.

**Amended while building it, and recorded here so it is not mistaken for a decision.** The
cost table above was met exactly, which is why nothing in it is edited: 6.3 serves
**27 / 6 / 11 / 0 / 24** on the committed LIRR capture and 33 / 0 / 0 / 0 / 0 on
Metro-North's. What follows is the one vehicle those five rows do not cover, and where the
counts are carried.

**A positioned vehicle the geographic box rejects is judged by N2's fallback rather than by
the ladder, and not every step is true of it.** When its own fix is past `OBS_MAX_S` and
nothing can place its trip it is counted at step 5, because "last seen over 10m ago" is as
true of it out of the box as in it: moving `GO201_26_8945` out keeps the count at 24
(`test_an_out_of_range_vehicle_withheld_for_age_stays_counted_withheld`,
`backend/tests/test_feeds_railroad.py`). When its fix is WITHIN `OBS_MAX_S` it is counted
nowhere, because it is no marker and it was not withheld for age, so step 5's claim about
when it was last seen would be false of it. Moving `GO201_26_7987` out takes step 3 from 11
to 10 and the five counts then sum to 67 of the 68
(`test_an_out_of_range_vehicle_with_a_recent_fix_and_no_placeable_trip_is_counted_nowhere`).
That is a decision rather than a gap, and it is the only case in which these five rows do
not partition the fleet.

**The carrier is `positions.suppressed`**, the fifth count of `models.PositionSteps`
(`backend/models.py:142`), on the per-system block of the envelope a rider's client already
reads (`SystemFreshness.positions`, `:245`), and projected for the operator as
`/api/status`'s `railroad_positions` (`backend/routes/status.py:214`, built by
`_railroad_positions` at `:221`) so the two surfaces cannot disagree about a count. Step 5's
row says "the marker is gone"; the count is what a rider is given instead, which is Q7's
answer, rendered as "LIRR 24 trains not shown, last seen over 10m ago" (`withheldClause`,
`frontend/helpers.js:2076`).

**Amended while building it, and recorded here so it is not mistaken for a decision this
section made: the clause RIDES the status line and never raises it.** As built it raised
the line, on the argument that a train which has left the map is the one thing no marker
can say. The whole-branch review measured what raising meant: the committed capture
withholds 24 of 68 on a wholly healthy railroad, so the line was non-null on every poll,
and `frontend/map.js` paints any rendered line with the error class, so an ordinary day
showed a rider a red status bar. Worse, it raised the line that section 3.2's Metro-North
clause was deliberately built only to ride, so that clause spoke every day too and the
trade it was designed around was gone. Raising therefore reopened the devaluation 3.2
names ("a status line that always says something is a status line nobody reads") through
the clause added to answer Q7. The count is not lost: it is served on every railroad block
and on `/api/status`, and the contract monitor prints it every run, which is the operator's
copy this section always intended. What a rider loses is being told on a day when nothing
else is wrong, and that is the same trade 3.2 already makes for Metro-North.

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
| `Vehicle`, `Train`, `RailroadTrain`, `PathTrain`, `NjtTrain`, `FerryBoat` | `+ observed_at`, `+ provenance`. `FerryBoat` already has the value under the name `updated_at`, which Q1 renames to `observed_at` with the old key served alongside for one release. |
| `Arrival`, `RailroadArrival`, `PathArrival`, `NjtArrival`, `FerryArrival` | `+ observed_at`, `+ provenance`. A prediction is an observation. |
| `Alert` | `+ observed_at`, `+ provenance`, the latter only ever `reported` or `retained`. |
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
relaxed. **Built at 6.1, and they were updated rather than relaxed.** `SystemFreshness` now
carries SIX fields, not four: `feed_timestamp` from 3.5 below (`backend/models.py:209`) and
`positions` from 6.3's suppression count (`:245`), beside the `fetched_at`, `ok`,
`retained_since` and `routes` it had (`:191`, `:215`, `:220`, `:235`).

**The gap is the other direction, and it is the one to fix in the same commit.** The row
models are locked; the arrivals ENVELOPES are not.
`test_station_arrivals_validates_handler_shape` only validates a four-key payload, and
pydantic ignores fields it is not given, so adding `served_at` and `systems` to
`StationArrivals` passes it silently. The five envelopes this contract widens are exactly
the five with no field-set lock, which is presumably how they came to be missing
`served_at` in the first place. **Closed at 6.1:** all five are locked by
`test_arrivals_envelope_field_set_is_locked` over an `ARRIVALS_ENVELOPES` table
(`backend/tests/test_models.py:267`), and `test_every_arrivals_envelope_is_locked` asserts
the roster itself, so a sixth envelope added later cannot be the one nothing covers. The
handler-shape test still validates its payload and is now at `:285`.

### 4.2 The decoders

| Decoder | Change |
| --- | --- |
| `feeds/railroad.py` `_decode_railroad_vehicles` | Read `v.timestamp`, emit `observed_at` for LIRR and `None` for MNR, apply the LIRR age gate, and stop returning a bare list of trains that the placement pass has to second-guess. |
| `feeds/railroad.py` `_decode_railroad_feed` | Build `positioned_ids` from the SAME accepted set the GPS pass emitted, which closes N2, and read `tu.timestamp` for the prediction gate and for a placed train's `observed_at`. |
| `feeds/__init__.py` `RAILROAD_FRESHNESS_SYSTEMS` | Unchanged in value, but it now has a THIRD reader. It had two when this was written, and both anchors have since moved: may this header drive `feed_timestamp` is `railroad.py:1252`, and may this header raise a staleness WARN is `contract_monitor.py:732`. **Built: the value is still `frozenset({"LIRR"})` (`railroad.py:51`) and it has five reads, not three.** The three new ones are `_railroad_observed_at` (`railroad.py:121`, may this system's vehicle stamp become an `observed_at`), `_position_age_gated` (`:136`, is this system's position row age-gated) and `routes/status.py:573` (the served-observation health code's own gate). The per-provider policy table did NOT become another place where Metro-North's exclusion is restated: `_position_age_gated` reads the set and says so in its docstring. |
| `feeds/subway.py` | A NEW join: `_decode_feed` visits only `trip_update` entities today, so the 98 VehiclePositions have to be indexed by `trip_id` first. Then `v.timestamp` onto each joined train, the group header onto the other 62 and onto each arrival. |
| `feeds/njt.py` | Emit `provenance: "placed"` (or `"estimated"` on the interpolated segment) and the header as `observed_at`. No new clock: the header is the only one. |
| `feeds/path.py` | Read `tu.timestamp` per entity. This is the one decoder where the new clock is strictly better than the envelope clock it has. |
| `feeds/ferry.py` | Rename `updated_at` to `observed_at` (Q1), serving the old key alongside it for one release, and carry the TripUpdates header onto dock arrivals rather than letting them inherit the boat clock. |
| `feeds/buses.py` | Unblocked by the 6.0 probe. Read `v.timestamp` in `fetch_vehicle_positions` beside the `position`, the NYC box and the bearing it already reads, and emit it as `observed_at` with provenance `reported`. The smallest decoder change in this table: one field, on the largest fleet. Note that buses now have a committed capture but still no GOLDEN, so unlike every other system there is no expected-output file to move and nothing that would catch a silent shape change. |
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

`/api/status` gains nothing structural for 3.5's clock. It already reports per-feed content
lag, and the new per-system `feed_timestamp` is the same number it has, moved to where the
client that never fetches `/api/status` can read it. **6.3 did add one key**, and it is a
projection rather than a second record: `railroad_positions`
(`backend/routes/status.py:214`, built by `_railroad_positions` at `:221`) carries the
ladder's counts off the same per-system blocks `/api/railroads` serves, so the operator's
snapshot and the rider's envelope cannot disagree about a count. The contract monitor reads
it as `production:railroad-positions`.

**Asserted today that this makes false:** the comment at `backend/routes/subway.py:89-102`
says "THE OLDEST CONTRIBUTING GROUP'S poll time, not the aggregate's" and explains that the
honest answer for a union is the worst of its parts. The reasoning survives intact; the
sentence stops being the whole answer, because poll time was never the clock that F03 was
about. **Built at 6.1:** the comment carries that second half itself now ("THE REASONING
SURVIVED 6.1 AND THE SENTENCE STOPPED BEING THE WHOLE ANSWER", `:97`), and the content-time
sibling sits beside it as `_oldest_contributing_content_at` (`:106`).

### 4.4 The frontend

| Surface | Change |
| --- | --- |
| Popup | Render the provenance word and the age line from the SERVED values instead of deriving them. `isPlacedRailroad` is deleted rather than fixed. **Built at 6.3:** it is gone, `positionQualifier` (`helpers.js:2342`) is the one helper every vehicle surface renders a position's words through, and `railroadHollow` (`:2492`) records why the glyph stopped reading `stop_id`. The old anchor, `helpers.js:211`, points at nothing about provenance in any tree since. |
| Station panel | Stop computing `ageSeconds` as `now - payload.fetched_at` and read the served content clock. Per-row qualification, so a stale contributor's rows are marked and a healthy contributor's are not. **Built at 6.2:** `ageSeconds` is gone and `boardSystemLine` (`helpers.js:2230`) replaced it, which the comment above that function states. The old anchor, `helpers.js:2193`, points at nothing about freshness. |
| Marker style | `STALE_MARKER_OPACITY` becomes per-observation. Before 6.3 a marker dimmed only when its whole SYSTEM was stale, which is why 41 stale LIRR observations sat at full opacity inside a healthy feed. **Built at 6.3:** every opacity site asks `vehicleMarkerAge` (`frontend/systems/shared.js:2062`), which takes the worse of the system's age and the row's own (`observationAge`, `helpers.js:2275`) before `markerOpacity` (`:1857`) sees it. |
| Glide / animation | The freeze deadline (`systemStaleAtOf`, `glideClock`) becomes per-observation too. Dead-reckoning a position from a ten-minute-old fix is the animated form of the same falsehood. |
| Live region | One write per render, not two. N6 is open on `#page-announce` and a per-observation qualifier is exactly the kind of second writer that trips it. |
| `ingestSystems` | The single door every freshness value enters through (`helpers.js:1595`), and it read exactly four names. Any field the contract adds to an envelope reaches no surface at all until this function changes, and nothing currently tests that it drops the rest. This is the first frontend edit, not the last. **Built: it reads six**, the four plus `feed_timestamp` (6.1) and `positions` (6.3). |
| The shared lag term | `systemAges` computed `ages[name] = Math.max(lag, poll, 0)` with `lag` taken from the ENVELOPE, so every system of a source shared one content-lag number. A per-system content clock replaces that term, and this is the line that makes it possible. **Built at 6.1:** `systemAges` is `helpers.js:1740` and its lag term is `systemLag` (`:1723`), which reads the system's own `feedTimestamp` and falls back to the envelope's only for a block that carries none. |
| Suppression count | NEW, and Q7's answer: when the ladder's step 5 drops observations for age, the count reaches the status line and nothing else. A rider can otherwise not tell "no trains on this branch" from "we dropped 24 of them". No per-marker ghost, which is the thing being fixed. |
| Status line | Gaining two clauses, one of them in new wording. `staleness()` already produces "railroad: MNR as of 6m ago" and the two-clause stale/blind split; it gains a THIRD population (systems whose CONTENT is old while their poll is current), which reuses "as of {age} ago" verbatim, and a FOURTH (systems with no observation clock, per Q5's amendment to 3.2), which is the one new string. Neither may be merged into an existing clause, for the same reason the first two were split: a system in one state announced with another state's age is the exact defect that split them. |

**Asserted today that this makes false:** the panel computed its age from `fetched_at`, and
the comment above the popup's age line said that line exists so a popup "must say how old
they are rather than imply liveness". The mechanism was right and the input was wrong, so
the line was silent in exactly the case it was written for. **Built at 6.2 and 6.3, and
neither anchor survives:** `ageSeconds` gave way to `boardSystemLine` (`helpers.js:2230`),
and `feedAgeLine` is no longer in the tree, its work split between the vehicle popup's footer,
which speaks for a system's age, and `positionQualifier` (`:2342`), which speaks for the
observation's. (MR5 then replaced that footer's own helper: `stalePopupLine`, named here until this
pass, gave way to `popupFreshHtml` (`helpers.js:740`) and the `.fresh` footer it builds, under
ruling Q2.)

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

**Amended at 6.2:** the code, `observations-qualified`, fires when every row a system serves
is past the operator band (the header check's `REALTIME_STALE_S`, 600 s) or carries no clock
where its provider dates rows, or when the retention cap has dropped a system's rows; not
when rows are merely qualified for a rider at `OBS_FRESH_S`. 6.2 records why.

**Asserted today that this makes false:** nothing. This is the one section that is purely
additive, which is itself informative: the operator surfaces were built with the right
shape and were never given the value.

### 4.6 The goldens that move

Eleven committed goldens, **2,864 records between them** (418 vehicle-shaped rows, 2,442
arrival rows, 4 alerts), compared by whole-object equality against live decoder output. Two
new fields per record rewrites all eleven:

| Golden | Records | Beyond the two new fields |
| --- | --- | --- |
| `railroad_lirr_expected.json` | 68 | **68 to 38, built at 6.3 and 38 on disk.** 27 fresh plus 11 aged; 6 move to the placed golden and 24 leave every golden. |
| `railroad_lirr_placed_expected.json` | 56 | **56 to 62, built at 6.3 and 62 on disk**, the 6 estimated trains arriving from the GPS pass. |
| `railroad_mnr_expected.json` | 49 | Count unchanged. Every row gets `observed_at: null`, which is the whole Metro-North policy made visible in one file. |
| `railroad_mnr_placed_expected.json` | 1 | Count unchanged. |
| `railroad_lirr_arrivals_expected.json` | 765 | Rows gain the prediction's own clock; the LIRR prediction gate may drop rows. **It dropped none:** 765 rows at 6.1 and byte-identical through 6.3, which moved only the two position goldens above. |
| `railroad_mnr_arrivals_expected.json` | 926 | Rows gain `observed_at: null`. |
| `njt_tu_expected.json` | 68 trains + 648 arrivals | The largest golden in the repository. `observed_at` the header on all 716. The 68 TRAINS are `placed` or `estimated` (60 estimated, 8 placed, following the motion state exactly); the 648 ARRIVALS are `reported`, because a prediction is sent rather than derived. An earlier draft of this row said every row was placed or estimated, which was wrong about 648 of the 716. |
| `subway_1_7_s_expected.json` | 95 | Rows gain the vehicle clock where a VehiclePosition joins and the group header where none does; 16 of the 98 joined observations are over 90s. |
| `path_rt_gen_a_expected.json` | 53 trains + 53 arrivals | Every row gains its own per-trip clock, which is new information the golden has never held. |
| `ferry_rt_expected.json` | 28 boats + 50 arrivals | Both keys on every boat for one release (Q1); the 50 dock rows gain the TripUpdates clock. |
| `alerts_mnr_expected.json` | 4 | Feed clock and provenance. |

**ERRATUM, 2026-09-12, on the subway row above.** Its "16 of the 98 joined observations
are over 90s" is true of the FEED and is the wrong number to build from. Measured while
regenerating this golden: **only 2 of the 95 SERVED trains carry an observation older than
90 seconds.** The other 14 belong to trips the placement pass never draws, having not
started yet or having no resolvable upcoming stop, so they are stale observations of trains
that are on no map. The feed number is still correct about the feed, and the row keeps it
because it is what the vehicle join actually sees.

**The subway half of F01 is sized from the served number, not the feed's.** A rule acts on
trains that reach a rider, so it is two markers on this capture rather than sixteen: reading
the feed figure as the workload overestimates it by a FACTOR of eight, not by eight markers.
The measurement is pinned
at `test_subway_positions_take_the_vehicle_clock_where_one_joins`
(`backend/tests/test_models.py:615`) rather than left in prose here, so the 2 and the 84 move
together or fail.

**That paragraph opened "6.3's gate is sized from the served number" until 6.3 corrected it,
and the word "gate" overstated what 6.3 built.** The gate is the
railroad shared rule alone, LIRR by policy and Metro-North exempt by policy, and no other
system's positions are dropped past `OBS_MAX_S`. Those two subway markers are QUALIFIED by
the rendering, dimmed and dated and frozen exactly as any other system's aged position is,
which is 3.2's half of this contract rather than 3.4's.

The two count changes in the LIRR goldens are arithmetic from section 3.4's policy, stated
here so a regeneration that produces different numbers is a signal rather than a surprise.
They transfer cleanly because `railroad_lirr_expected.json` records `now: 1782006915.0`,
which is the capture's own header: the clock the golden test runs the decoder against is
the same clock every age in this document was measured against.

**A CHANGE IN A GOLDEN'S RECORD COUNT IS EXPLAINED IN THE COMMIT THAT REGENERATES IT, NEVER
ABSORBED.** Adding two fields to every row is a diff nobody reads line by line, and a count
that moves inside it is the one thing in that diff that carries meaning: it says an
observation stopped being served, or started. The regenerating commit states the before, the
after, and which rule accounts for the difference, in the same voice F02's did ("69
positioned vehicles yield 68 served trains"). A count this document predicted and the
regeneration contradicts is a finding about the policy or about the capture, and either way
it is worth more than the commit that would have swallowed it. The counterpart rule already
exists on the other side: the invariant belongs in a test asked of the decoder, so a
recapture cannot move a count silently either.

**One model-level fact belonged here rather than in 4.1, because a reproduction already
pinned it:** `models.RailroadFeed` did not merely lack an observation time, it STRIPPED one.
Adding `observed_at` to a cached train and serving it through `/api/railroads` dropped the
key, and `f01_lirr_gps_observation_age.py` asserted exactly that. So the field could not be
smuggled in through the cache; it had to land in the model. **It did, at 6.1:**
`RailroadTrain.observed_at` (`backend/models.py:127`), and the reproduction now records the
flip rather than the strip.

**Asserted today that this makes false:** `railroad_lirr_expected.json` asserted that 68
positioned LIRR vehicles are served. Twenty-four of those rows were the finding. F02's fix
had already established the pattern for this: the invariant belongs in
`test_feeds_railroad.py` as a law asked of the decoder rather than of the golden, so a
recapture cannot bless the old behavior back in. **Built at 6.3, exactly as forecast:** the
golden asserts 38, and the laws are asked of the decoder, at
`test_the_lirr_capture_orders_27_6_11_0_24` (`backend/tests/test_position_ladder.py`) and
across `backend/tests/test_feeds_railroad.py`, with the served body committed as
`tests/e2e/fixtures/f01_railroads.json` and read by `backend/tests/test_f01_positions.py`.

### 4.7 The audit reproductions

`f01_lirr_gps_observation_age.py` and `f03_arrivals_content_freshness.py` both exited 0
because the defects held. Under the directory README's own rule, a fixed finding's script
becomes the regression check on the FIX and must carry the before and the after. Both flip
when this work lands, and both should be rewritten in the F02/F05/F10/F12 shape rather than
deleted: the measured before-values in this document are the ones they should pin.

**Asserted today that this makes false:** both scripts exited 0 precisely because the defect
held, so each one's success was a report that a rider is being misled. That is
the intended design of the directory (a red run means the audit table is stale), and it is
worth saying out loud that these two were the only files in the repository whose passing
this work was supposed to break.

**Both have flipped, and both were rewritten rather than deleted.**
`f03_arrivals_content_freshness.py` was rewritten at 6.2 (`claude/freshness-6-2-boards`) and
`f01_lirr_gps_observation_age.py` in 6.3's acceptance commit on this branch, each over the
world its own fix built and each carrying the audit's figure in the label of every check
that moved. The before-values measured in this document are the ones they pin.

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

Each is phrased so a yes or a no settles it. **All eight were answered on 2026-09-12 and
the answers are recorded below in Decisions.** The questions are kept here exactly as they
were posed, including the recommendations, because a question rewritten after it is answered
stops being evidence of what was actually asked. Where a decision changed the design,
sections 3 and 4 were amended in place on the same date, so the body is the thing to build
from and it cannot disagree with this section.

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

**DONE, 2026-09-12.** `backend/tests/fixtures/bus_vehicle_positions.pb`, 280965 bytes,
fetched once. All 2136 entities carry a position and a `VehiclePosition.timestamp`, 47
distinct values across 104 seconds, median age 15s, p95 29s, 1 of 2136 over 90s and 0 over
600s; the header equals the newest observation, so it is a generation time. The row is in
3.3 and 2.7 carries the reading.
`docs/reviews/audit-2026-09-05/probe_bus_observation_clock.py` re-derives every number from
the committed bytes and exits non-zero if one stops holding. **The gate is cleared: 6.1 can
start.**

**What the answer bought, since a gate that changes nothing is not worth being a gate.**
Buses are the largest fleet in the application, 2136 vehicles against 68 LIRR trains on
their capture, and they are the fleet a rider is most likely to be looking at. Had the
field been absent, every one of those markers would have been undated and the per-system
clause from Q5 would have had to speak for the whole bus layer. Instead the row costs
riders almost nothing today: at `OBS_FRESH_S` 90 exactly one observation of 2136 would be
qualified, and at `OBS_MAX_S` 600 none would be dropped. **The gate was worth running
precisely because that outcome was not knowable in advance**, and the cheap answer, assuming
buses were undated and rendering them so, would have been wrong about 2135 markers.

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

**Amended while building it, and recorded here so it is not mistaken for a decision.** 3.2's
per-system clause was written for the status line, and its wording, "{system} position age
unavailable", would be false on a board, which carries predictions. The board's system line
says `{system} prediction age unavailable` instead: the same clause in its prediction form,
under the rule the status line's clause follows, riding the board's line that reports a
stale poll and never raising it, so a healthy Metro-North board says nothing (Q5). It names
the system in the rider's word, "Metro-North" rather than the feed code, because the panel
speaks the line and an initialism is read letter by letter. 3.2's vocabulary table carries
it as its own row.

**And one change made at review, recorded for the same reason.** The served-observation
code (4.5) first fired at the rider's `OBS_FRESH_S`, the threshold 3.2 gives a board row,
so it named every system whose rows were all 90 seconds old and every group failing for a
single poll. It now fires at the operator's band, the header check's 600 seconds
(`REALTIME_STALE_S`), which is the band 4.5 already gives the monitor's served-observation
check, and it ages a carried-forward row by its clock like any other. A rider's "as of 2m
ago" is information beside a countdown; an operator is told at ten minutes. This is 3.3's
line between the operator surface and the rider surface, read facing the other way: an
operator rule copied into the rendering reproduces F01, and a rider rule copied into an
operator code is one an operator learns to ignore.

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

## Decisions, 2026-09-12

All eight questions in section 5 are settled, and **every answer matched the recommendation
this document gave**. That is worth stating rather than leaving implicit, because it is the
kind of agreement that looks like a rubber stamp and was not: Q5 and Q8 were both answered
against what the BODY of the document said at the time, since sections 3.1 and 3.2 were
written before those questions were formed and proposed the opposite of what section 5 went
on to recommend. The amendment below is the body catching up with its own recommendation,
not the design changing direction.

| Q | Answer | Consequence |
| --- | --- | --- |
| Q1 | **Yes** | `FerryBoat.updated_at` becomes `observed_at`. The old key is served alongside it for one release, then dropped. One name for one concept across every system. |
| Q2 | **Yes, 600** | `OBS_MAX_S` adopts the retention cap's horizon and its argument. On the committed LIRR capture this is what removes 24 of 68 trains from the map. |
| Q3 | **Yes** | `retained` wins the provenance field. A retained GPS position reports `retained`, not `reported`; `SystemFreshness.retained_since` carries the timing for anyone who needs it. |
| Q4 | **Yes, 90** | The estimate step uses `OBS_FRESH_S`, so there is one freshness number rather than two. 6 estimated markers on the committed capture, not 8 or 11. |
| Q5 | **No** | A non-gated provider is SILENT at the marker and the fact is carried once on its per-system line. 3.2 had proposed the opposite and is amended accordingly. |
| Q6 | **Yes**, and the probe has since run | The bus probe in 6.0 blocked the build. It was taken on 2026-09-12 and OneBusAway dates every observation it sends: 2136 of 2136 positioned vehicles carry `VehiclePosition.timestamp`, median age 15s, 1 over 90s, 0 over 600s, and its header is a generation time rather than a write time. The buses row of 3.3 is written and age-gated, so the gate is cleared and 6.1 can start. |
| Q7 | **Yes** for the status line, **no** for the map | A suppression count reaches the status line so a rider can tell an empty branch from a filtered one. Nothing reaches the map, which would reintroduce the ghost. |
| Q8 | **No** | The first provenance value is **`reported`**, not `live-gps`. 3.1 had proposed `live-gps` and is amended accordingly. The rider string "live GPS" is unchanged on the surfaces that already say it. |

**What changed in the body, and where.** Q2, Q3, Q4 and Q6 confirmed what sections 3.3,
3.1, 3.4 and 6.0 already proposed, so nothing moved for them. The other four did move
something:

- **Q8** renamed the first enumeration value everywhere it is used as a VALUE: the
  enumeration table in 3.1, the vocabulary table in 3.2, the fallback ladder and cost table
  in 3.4. It did not touch the shipped rider strings in 3.2's first table, which are
  quotations of code, and it did not touch the auditor's wording in 6.3, which says
  "unqualified live-GPS marker" and is quoted verbatim.
- **Q5** rewrote the rule in 3.2 into three lettered clauses and added a fourth row to the
  vocabulary table, splitting a null `observed_at` by whether its policy row is age-gated.
  It also added a per-system clause, `{system} position age unavailable`, which is the one
  qualifier in this vocabulary that describes a provider rather than an observation, plus
  the measured note that it must not be able to raise the status line on its own.
- **Q1** made the ferry rows in 4.1, 4.2 and 4.6 definite instead of conditional.
- **Q7** added a row to 4.4 for the suppression count.

**ERRATUM, 2026-09-16, on Q7's answer above.** "Yes for the status line" is right and is
unchanged; what it did not say is whether that count may RAISE the line or only ride one,
and as built it raised. Measured at 6.3, on the capture this document sizes everything
else from: a healthy LIRR withholds 24 of its 68 positioned vehicles, about a third of its
markers, on an ordinary evening with nothing wrong. So a clause that can raise the line
raises it on every poll, and `frontend/map.js` paints any raised line with the error
class, which gave a rider a permanently red status bar and spent the colour that tells
them a feed is actually down. That is section 3.2's own hazard, "a status line that always
says something is a status line nobody reads", reopened through the clause added to answer
this question, and it also raised the line that Q5's Metro-North clause was deliberately
built only to ride, so that clause spoke every day too. **The count therefore rides the
railroad line and never raises one**, exactly as Q5's clause does, and the answer above
should be read as "yes for the status line, as a clause that rides it". Nothing is lost to
an operator or to a later reader: the count is served on every railroad block, published
on `/api/status` as `railroad_positions`, and printed per system by the contract monitor
on every one of its six-hourly runs. What a rider gives up is being told on a day when
nothing else is wrong, which is the same trade Q5 already made for Metro-North.

**One correction that is not a decision, made while amending and recorded so it is not
mistaken for one.** Applying clause (c) to 3.3's table exposed a row that was wrong before
any question was answered: "Subway, position, no VehiclePosition" was marked as having no
clock and therefore non-gated. It is not. `feeds/subway.py:155-175` places EVERY subway
train from its trip update, so a train with no vehicle entity is dated by its group's header
exactly like a prediction, not left undated. That row is now age-gated, which moved the
tally to eleven gated and four not on the day this was written, removes the 62 trains from
every list of undated observations, and leaves **Metro-North as the only wholly undated
system**, so the per-system clause Q5 introduced applies to exactly one source today. The
first draft of the amended rule left those 62 trains qualified nowhere; this closes that
hole rather than documenting it.

**The tally moved once more, at 6.0, and two of the three places that state it were not
moved with it.** The bus probe made the buses row age-gated, so 3.3 reads TWELVE gated and
three not, which is what the sentence under its table says and what counting the table
gives. The two that ran a row behind from 6.0 until 6.3 corrected them are the sentence
above and the one in 3.2 that reads the split off for clause (c). No policy changed with
either; the number was arithmetic about a table, and three copies of it is how one of them
goes stale.

**One consequence of Q5 worth stating on its own, because it is the largest rider-visible
effect of any answer here.** Had Q5 been answered yes, which is what section 3.2 originally
proposed, Metro-North's 33 markers and its 926 arrival rows would each have carried a
permanent "age unknown". Under the answer none of them does, and Metro-North states it once
on its system line instead. That trades per-observation precision for a qualifier
vocabulary a rider still reads when it appears, and the bet is that a word seen rarely is
believed and a word seen everywhere is not. If that bet is wrong it will show up as riders
trusting an undated Metro-North marker exactly as much as a dated LIRR one, which is
measurable and worth revisiting rather than arguing about in advance.

---

## For the next amendment, posed by Amendment 1 as its Q8: the compact form has no reader

**Recorded rather than acted on**, because removing a form this contract defines is an amendment to
the contract and not a stage's tidying.

`positionQualifier` returns three renderings of one judgment: `.words` for a popup, `.spoken` for a
marker's accessible name, and `.compact`. The three differ in exactly one family, `placed`, where
`.words` and `.spoken` say "scheduled position (no GPS)" and "scheduled position, no GPS" and
`.compact` says "scheduled (no GPS)".

**`.compact` existed for one caller and that caller is gone.** The railroad popup rendered it
directly, from a line in `systems/railroad.js`, and it was the only surface in the app that chose
between the forms rather than going through the shared helper (`positionLineHtml` then, and
`positionWords` since the vocabulary commit renamed it: section 5's grid renders the words and the
helper returns them). Map redesign stage MR5, ruling Q1,
unified every popup on `.words`, which changed two strings a rider reads (a `placed` railroad train
gained "position", and a FRESH reported fix went silent, because silence means current and this popup
was the one place that broke that). **Since then nothing in the frontend reads `.compact` at all.**

It was found by measurement rather than by reading: `pins.spec.js` P5b, the popup coverage test,
reported `"scheduled (no GPS)"` as a rider-visible literal in the popup call graph that no pin
covered, on the very commit that unified the call site. It is waived there in `NOT_RIDER_TEXT`, with
the reason, rather than in `UNREACHED_STATES`, because that second map is for text a rider WOULD read
in a state no world reaches and there is no such state left for this one.

**What the next amendment has to decide** is whether a contract that defines three forms should keep
one no surface renders. Two things are worth weighing and neither is obvious. Against keeping it: an
unread form is a third spelling of one judgment that a future surface could pick up by accident,
which is how two neutrals ended up on screen for one NJ Transit route (MR3's finding N6). For
keeping it: the compact form is shorter, a surface tighter than a popup may yet want it, and
`positions.test.js` still pins the difference so it cannot drift while it waits.

Nothing else in the contract changes either way: `.words` and `.spoken` are unaffected, section 3.2's
rider-word table is unaffected, and no provenance, age policy or silence rule moves.

---

**Nothing here was built when this was written.** This is the design the Release 1 order put
at item 7, and the two items it blocks are items 8 and 9. With the eight questions settled
the build started at 6.0, the bus probe, which Q6 made a gate rather than a note, and ran
through 6.1, 6.2 and 6.3 in that order. **All four steps are built**, on the branches the
status line at the top of this document names: F03 closed at 6.2 and F01, with N2 already
under it, at 6.3.

---

## Amendment 1, 2026-11-01: empty boards and headerless generations (A03, A10)

**Proposed, not built, and not decided.** This section is written on
`claude/design-freshness-amendment-1`, forked from `main` at `1d17aa0` (PR #127's merge) and
rebased onto `74bd2ee` (PR #131's merge), as
item 3 of the release order in [the 2026-10-12 record](../reviews/audit-2026-10-12.md)
(Section 4), first half: A03 and A10 as an amendment to this contract, for review before
anything is built from it. It changes no code, no test, no fixture, no golden and no witness
script: the 2026-10-12 record still reads A03 and A10 as VERIFIED, both audit harnesses
report what they report on `main`, and the build that follows is what flips them. Sections 1 to 6 and the Decisions of 2026-09-12 are not rewritten here.
What this amendment adds to 3.2 and 3.5 and changes in 4.x is stated in its own subsections
below, and the body is amended in place on the date a decision lands, as it was on
2026-09-12. Two edits fall outside this section: the Status paragraph at the top, which says
this amendment is proposed and changes nothing yet, and the heading of the compact-form note
above, which this amendment poses as Q8.

**A "today" or a "currently" in this section means the world at `1d17aa0`.** Every citation
below is a path and a line range at that commit, re-anchored for this amendment rather than
carried over from the audit's links, which were taken at `1e3e73d` with four pull requests
merged since. Every number is measured on this branch by the command named beside it, from
the repository root, on CPython 3.12.13 with `backend/requirements-dev.txt` installed, and
at `1d17aa0` under pytest 9.1.1, the version CI last installed on `main` before 9.2.0 was
published (run 39295886396, 2026-10-19): at that commit pytest 9.2.0 (published 2026-10-27)
stopped the A03 witness at `KeyError: 'subway_station_routes'` before it measured anything,
because it restores a patched attribute from the target's instance `__dict__`, which
Starlette's `State` does not use. PR #130 (merged as `ef98328`) fixed that double patch at
its source, a harness repair with every check unchanged, and pins `pytest==9.2.0`.
**The rebase onto `74bd2ee` changes no application code.** PR #130 and PR #131 between them
change `backend/requirements-dev.txt`, one patch in `backend/tests/test_api.py` and one in
the A03 witness, the 2026-10-12 harness README, the npm test tooling and `ACCESSIBILITY.md`.
Of this section's citations, only those into `backend/tests/test_api.py` past line 1000
move, each four lines later at `74bd2ee`: `:1291-1350` is `:1295-1354` there, `:1353-1445`
is `:1357-1449`, `:5572` is `:5576` and `:5682-5683` is `:5686-5687`.

### A1.1 The problem, with the numbers

**A03, the backend half.** `backend/.venv/bin/python
docs/reviews/audit-2026-10-12/a03_empty_board_hides_failed_feed.py` drives the acceptance
world of `backend/tests/test_f03_boards.py` (the committed 1-7+S capture and its stops, the
frozen clock T0 = 1782993600, station 219 Prospect Av, a poll every 20 s) through the real
`_refresh_subways` and the ASGI app, with 1-7+S answering HTTP 503 from T0+20 s beside a
healthy G and six other healthy groups. Its own table, as printed on this branch:

| Poll | 1-7+S | Rows at 219 | `systems` | Board `fetched_at` age | Board `feed_timestamp` |
| --- | --- | --- | --- | --- | --- |
| T0 | ok | 2 | `1-7+S` | 0 s | T0-5 |
| T0+20 to T0+160 | failing | 2, `retained` | `1-7+S` | 20 s to 160 s | T0-5 |
| T0+180 | failing | 1, `retained` | `1-7+S` | 180 s | T0-5 |
| T0+200 | failing | **0** | **`null`** | **0 s** | **`null`** |

219's last retained prediction was due at **T0+194 s** (the ledger's figure, reproduced), so
the T0+200 s poll is the first past it. Its board has empty directions, `systems: null`, no
content clock and a `fetched_at` **0 s** old, where one poll earlier the board named 1-7+S
and was **180 s** old. At that same poll `/api/status` still lists `subway_feeds` as
`{'total': 8, 'ok': 7, 'failed': ['1-7+S']}`, `/api/subways`'s 1-7+S block says `ok: false`,
retained since T0+20 s, and 420 s remain before the cap. **The whole served board, every
clock read relative to the instant it was served, is identical to genuinely fresh empty
service's.** The retention cap's road ends the same way on the untrimmed capture: at T0+600 s
the board carries 12 retained rows, `systems` names 1-7+S and `fetched_at` is 600 s old; at
**T0+620 s** (the ledger's figure, reproduced) it carries 0 rows, `systems: null` and a 0 s
old `fetched_at`, with 1-7+S still failed. Two more worlds in the same run serve boards
identical to fresh empty service's as well: stale content (1-7+S `ok`, its content 620 s
old) and never decoded (1-7+S failing from the first poll).

**The mechanism.** Three selectors over one membership rule, and two exits that remove what
the rule reads:

- `backend/cache.py:441-462`, `_contributing_systems`, is the membership. A group takes part
  at a station exactly when the station is a key of that group's arrivals index and the
  group has a block (`:457-462`), so a group with no rows indexed here is not a contributor,
  whatever its block says. Its docstring argues that membership is the INDEX rather than the
  served payload, deliberately (`:448-455`); it does not consider a group that has left the
  index.
- `:505-516`, `_contributing_freshness`, publishes the contributors' blocks `or None`
  (`:516`), so a board with no contributor serves `systems: null`. `:465-502`,
  `_oldest_contributing_content_at`, returns `None` for no contributor (`:500-501`). And
  `:519-548`, `_oldest_contributing_fetched_at`, falls back to the aggregate's own
  `fetched_at` when no contributor has a usable clock (`:539-543`), which its docstring
  states as the pre-C2 answer ("falling back to the aggregate's (C2)", `:522-523`).
- `backend/feeds/shared.py` removes the key those three read, by two exits. The retention cap
  in `merge_system_generations` (`:389`) drops a failed system's items outright,
  `continue  # capped` (`:455-456`), and `drop_expired_arrivals` (`:490-529`) keeps a station
  only while one of its buckets survives the prune (`:521-527`), so the station's key leaves
  with its last expired row.
- `backend/routes/subway.py:108-124` assembles the three into the served board: `fetched_at`
  at `:108`, `feed_timestamp` at `:111`, `systems` at `:124`.

**The blind spot this amendment inherits.** `backend/models.py:1173-1186`, the comment over
`HEALTH_OBSERVATIONS_QUALIFIED` (`:1187`), records 6.2's two KNOWN BLIND SPOTS, and the
second (`:1180-1186`) is this finding at the operator's surface: NJ Transit, the ferry and
PATH keep their last rows on a failed poll, "but one of them that fails while serving
nothing (an empty feed is an ordinary overnight state for NJ Transit and the ferry) has no
row to age and none to drop, so this code says nothing for as long as the outage lasts,
while /api/status still shows the feed's error. Closing that needs a decision about the
overnight empty state first, so it is not closed here." This amendment is where that
decision is posed (Q5), because the overnight empty state is the first of A03's four states,
**fresh and empty**, and the outage it hides is the third or the fourth.

**A03, the frontend half.** The same run boots the whole page (every script `index.html`
loads) in a `node:vm` over four empty boards that DO carry the contributor, the shape a
backend repair would serve, and reads the station popup, `#stations-detail` and the spoken
`#stations-announce`:

| Board | 1-7+S's block, as served | Popup | Panel | Spoken |
| --- | --- | --- | --- | --- |
| Fresh empty, the baseline | every group `ok` and current | `Northbound / No trains / Southbound / No trains` | `No trains.` | "Prospect Av, Subway. No trains." |
| A, stale content | `ok: true`, `fetched_at` 0 s old, `feed_timestamp` 620 s old | **identical to the baseline** | **identical** | **identical** |
| B, never decoded | `ok: false`, `fetched_at: null`, `feed_timestamp: null`, `retained_since` T0, `routes: []` | **identical to the baseline** | **identical** | **identical** |
| C, failed after decoding | last decoded 200 s before the board | adds `as of 3m ago` | adds `as of 3m ago` (`p.station-detail-stale`) | "Prospect Av, Subway. as of 3m ago. No trains." |

A and B are byte-identical to the baseline on all three surfaces. C is the script's control,
the evidence that this page does print a warning when the code raises one, and it raises
one here only because 200 s is past the 90 s threshold. Mechanism:

- `frontend/helpers.js:2395-2404`, `boardFreshness`, takes `pollAge` as the worst
  `servedAge(block.fetchedAt, ...)` over the envelope's systems (`:2398-2402`) and reads
  neither a block's `feedTimestamp` nor its `ok`. A's block is 0 s old by poll, so `pollAge`
  is 0; B's has no `fetchedAt`, contributes nothing, and leaves `pollAge` null.
- `helpers.js:1830-1841`: when a body's `systems` is null, `ingestSystems` (which
  `ingestEnvelope` calls at `:1908`) substitutes ONE block under the source key, built from
  the envelope's own `fetched_at` and marked `ok: true`. The backend half's `systems: null`
  therefore reaches the board as one healthy contributor polled at the aggregate's clock.
- `helpers.js:2462-2471`, `boardSystemLine`, speaks for an empty board only once
  `staleAge(board.pollAge)` holds (`:2468`), which is 90 s through `staleAge`
  (`:2013-2016`, `FEED_STALE_AFTER_S`). Neither A's 0 nor B's null gets there.
- The words themselves: the subway popup's `No trains` at `frontend/systems/subway.js:469-470`,
  the panel's `No trains.` at `frontend/stations.js:701-705`, the spoken board's at `:852-854`.

**A10.** `backend/.venv/bin/python docs/reviews/audit-2026-10-12/a10_headerless_lirr_clock.py`
drives the real railroad and subway decoders and the real block-building path through the
ASGI app. As printed on this branch:

| Step | LIRR, `/api/railroads` (`ok`, `fetched_at`, `feed_timestamp`) | ACE, `/api/subways`, the same bytes |
| --- | --- | --- |
| 900, timestamped (header 900) | (true, 900, 900) | (true, 900, 900) |
| 1000, headerless, decoded, own trip update dated 990 | **(true, 1000, 900)** | (true, 1000, `null`) |
| 1100, failed (HTTP 503) | (false, 1000, 900) | (false, 1000, `null`) |
| Control: 900, then failed at 1000 | (false, 900, 900) | (false, 900, 900) |

At 1000 the LIRR block implies an age of 100 s for a generation whose own trip update is
dated 990, nothing marks the clock as carried over (`retained_since` null), `/api/status`
reads `railroad_feeds {'total': 2, 'ok': 2, 'failed': []}`, the LIRR station board serves the
same 900, and Metro-North's block is null throughout. Beyond the audit's citation, the
railroad ENVELOPE's `feed_timestamp` reads 900 at 900, 1000 and 1100, and `/api/status`'s
`feeds.railroads.feed_age_s` reads **100.0** at 1000; the subway's envelope reads 900,
`null`, `null` and its `feed_age_s` `null` at 1000. Arm D runs the committed LIRR capture
(header H = 1782006915) and then five successful headerless next generations, each 20 s on
with every entity clock advanced by the same: all five are `ok`, all serve the capture's
LIRR rows, and every one publishes `feed_timestamp` H, so the implied age reads 20, 40, 60,
80 and **100 s**, past `FEED_STALE_AFTER_S` (90 s) on a feed that decoded every poll, and
`/api/status`'s `feed_age_s` follows it exactly.

**The mechanism.** One reader, two producers that disagree about it, and an envelope rule
that sits beside both:

- The reader, `backend/pollers.py:449-461`, in `_system_freshness` (`:395-474`): a system
  ABSENT from the content-clock map did not decode and keeps the clock it last published; a
  system PRESENT with `None` decoded without a header and publishes `None`, because carrying
  the old number forward "would age it by one interval every interval, without bound, while
  the feed looked healthy". The comment names `feeds/subway.py` as the producer that encodes
  the difference (`:456-457`).
- The producer that does not encode it, `backend/feeds/railroad.py:1320-1327`: `if feed_ts is
  not None and system in RAILROAD_FRESHNESS_SYSTEMS:` (`:1325`) gates the record at `:1327`,
  so a decoded headerless LIRR generation is absent from the map, which the reader takes for
  a system that did not decode.
- The producer that does, `backend/feeds/subway.py:344`, `feed_ts_by_group[feed_key] =
  feed_ts`, unconditionally for every group that decoded, under the convention its comment
  states (`:326-332`).
- The envelope's keep-last-known branch, `backend/pollers.py:696-708`. Its comment argues
  from a PARTIAL FAILURE ("when the only freshness-bearing system (LIRR) is the one that
  failed it comes back None", `:698-700`); its condition, `feed_timestamp if feed_timestamp
  is not None else entry["feed_timestamp"]` (`:706`), fires on a headerless success as well.
  So the audit's correction, a `None` at the producer, would repair the LIRR block and the
  LIRR board and leave the envelope and `/api/status`'s `feed_age_s` on 900, which is why
  A1.3 states both halves.

### A1.2 What each surface does today with an empty board, measured

This is the amendment's section 2: the four states are a contract only if every arrivals
family is measured against them, not only the subway. Each family below was driven through
its real refresh function (`_refresh_subways`, `_refresh_railroads`, `_refresh_path`,
`_refresh_njt`, `_refresh_ferry` in `backend/pollers.py`) and its real board route through
the ASGI app, with stub upstreams that answer real `httpx.Response` objects, failing feeds
answering HTTP 503, a poll every 20 s, and production defaults (`FEED_RETENTION_MAX_S` 600,
`FEED_STALE_AFTER_S` 90). The worlds are the existing test modules', imported rather than
copied: `test_f03_boards.py` for the subway (the A03 run above), `test_f01_positions.py`'s
`f01_world` and the committed LIRR and Metro-North captures for the railroads,
`test_feeds_path.py`'s capture (`path_rt_gen_a.pb`) for PATH, `test_njt_rt.py`'s feed
builders for NJ Transit (driven through the real `njt_auth.njt_post` over an
`httpx.MockTransport`, so no request left the process and no mint was spent), and the
committed `ferry_vp_a.pb` and `ferry_tu_a.pb` for the ferry, each restamped with
`test_f03_boards._restamp` and emptied with `_quiet_feed`. The contributor that fails is
1-7+S for the subway (G and six others healthy), LIRR for the railroads (Metro-North
healthy), and the only system for the other three. **The drivers are not committed**: each
harness directory's README governs its own membership and this branch adds no file, so the
recipe for each row is stated under the tables, precise enough to rebuild, with the existing
tests that already pin a cell named beside it.

**What the served board carries**, by state:

| Family | (i) failing, rows still on the board | (ii) failing, rows past their time | (iii) failing, past the retention cap | (iv) never decoded in this process | (v) healthy and empty |
| --- | --- | --- | --- | --- | --- |
| Subway union (`/api/subway-arrivals/219`) | `systems` names 1-7+S: `ok: false`, `fetched_at` frozen at the last decode, `retained_since` set; rows `retained` | **`systems: null`**, `fetched_at` the aggregate's (0 s old), `feed_timestamp: null`: identical to (v) | **the same as (ii)**, from T0+620 s | **the same as (v)**: the group has no row here, so it is not a contributor | `systems: null`, `fetched_at` the aggregate's, `feed_timestamp: null` |
| Railroad (`/api/railroad-arrivals/LIRR/{stop}`) | `systems` names LIRR: `ok: false`, `fetched_at` frozen, `retained_since` set; rows `retained`, pruned at each poll | `systems` keeps LIRR, `ok: false`, `fetched_at` frozen; directions `{}` | the same; the cap also drops rows not yet due, and `retained_since` reads null for one poll in 31 and then restarts, a 620 s cycle at the 20 s poll (H4 on the 2026-10-12 record's live list) | with Metro-North healthy: HTTP 200, `systems` names LIRR with `ok: false`, `fetched_at: null`, `feed_timestamp: null`, `retained_since` set; with both failing: HTTP 502 | `systems` names LIRR, `ok: true`, both clocks current |
| PATH (`/api/path-arrivals/{stop}`) | `systems: null` in every state; rows kept as `reported`, `fetched_at` frozen | **no pruning**: departed rows are served as they were, measured to T0+3620 s | **no cap**: the same as (ii) | HTTP 503 before any poll; **HTTP 502** after a failed first poll, carrying the sanitized upstream error | `systems: null`, `feed_timestamp: null`, `fetched_at` current |
| NJ Transit (`/api/njt-arrivals/{stop}`) | `systems` names `njt`: `ok: false`, `fetched_at` frozen; rows kept as `reported` | **no pruning**, measured to T0+14400 s | **no cap**: the same as (ii) | HTTP 503 before any poll (also while static loads, and when unconfigured); **HTTP 502** after a failed first poll | `systems` names `njt`, `ok: true`; `arrivals: []` |
| NYC Ferry (`/api/ferry-arrivals/{stop}`) | `systems: null` in every state; rows kept as `reported`, `fetched_at` frozen | **no pruning**, measured to T0+14400 s | **no cap**: the same as (ii); `FEED_RETENTION_MAX_S` has no effect on it | HTTP 503 before any poll; **HTTP 502** after a failed first poll, whichever leg failed | `systems: null`, `feed_timestamp: null`, `fetched_at` current; `routes: {}` |
| `observations-qualified` (`/healthz`) | silent until the youngest held row is past 600 s, then on (Metro-North's undated rows never age it on) | retaining families: silent until the last decode is 600 s old, then on; the others: rows never leave, so (i)'s rule | retaining families: on from the cap poll, every poll after; the others: no cap, (i)'s rule | **silent on every family**, measured to T0+1800 s (subway, railroads) and T0+10800 s (PATH, the ferry, NJ Transit), while `/api/status` shows it (a failed list; for NJ Transit only `feeds.njt.last_error`) | silent |

**What the rider reads on an empty board**, from the same served bodies through the
production renderers (`frontend/helpers.js` loaded in `node` the way
`frontend/boards.test.js` loads it, `boardFreshness`, `boardSystemLine` and each family's
popup renderer). State (iv)'s HTTP 502 never reaches those renderers, so it was rendered by
the page itself, in the browser, where the popup prints the body's `detail`
(`frontend/systems/shared.js:2891-2896` into `stationError`, `:2856-2860`) and the panel
prints it and speaks it (`frontend/stations.js:513-514`, `:660-665`); the text and how it
was measured are under the table:

| Family | (ii) or (iii), failing and empty | (iv) never decoded | (v) healthy and empty |
| --- | --- | --- | --- |
| Subway | `No trains`, no line: **identical to (v)** | `No trains`: **identical to (v)** | `No trains` |
| Railroad | `No trains`; from 90 s after the last decode, `as of {age} ago` above it | `No trains`: **identical to (v)** (with Metro-North healthy); with both failing, the 502's `detail`, `All railroad feeds failed: LIRR: Server error '503 Service Unavailable' for url '<feed url> For more information check: <feed url> MNR: ...` and the same for Metro-North | `No trains` |
| PATH | rows never leave a failing board, so it is empty only when it failed empty: `No trains`, then `as of {age} ago` from 90 s | the 502's `detail`: `Upstream PATH bridge feed error: Server error '503 Service Unavailable' for url '<feed url> For more information check: <feed url>` | `No trains` |
| NJ Transit | as PATH | the 502's `detail`: `Upstream NJ Transit feed error: NJ Transit returned HTTP 503: (empty body)`, the upstream's body in place of `(empty body)` when it sends one | `No trains` |
| NYC Ferry | as PATH, with `No boats` | the 502's `detail`, PATH's with `Upstream NYC Ferry feed error:` in front, from the first failing leg | `No boats` |

**What the 502 carries, rendered.** Its body is `{"detail": ...}`: `_require_filled_cache`
raises the error the failed poll recorded (`backend/cache.py:321-333`), and the same detail is
`/api/status`'s `feeds.<entry>.last_error` (`backend/routes/status.py:119`). The detail is a
family prefix and the upstream exception's own text, with only its URL runs replaced by
`<feed url>` (`_sanitize_upstream`, `backend/cache.py:248-268`, whose `https?://\S+` also
takes the closing quote after the URL and, on the railroad, the `;` of its join). For PATH,
the ferry and the railroads that text is httpx's `raise_for_status` message, which carries
the upstream's status code and HTTP reason phrase and not its body; for NJ Transit it is
`_body_or_raise`'s, which carries up to 200 characters of the upstream's body, whitespace
collapsed (`backend/njt_auth.py:647-665`, `:1479`), and not the reason phrase (`:705` keeps
only the status and the bytes). With a stub upstream that put one marker in its reason phrase
and another in its body, the reason phrase's marker reached the rider on PATH, the ferry (its
first failing leg's only, `backend/feeds/ferry.py:459`) and both railroads, and the body's on
NJ Transit, each on the popup, on the panel, and in `#stations-announce`, which speaks
"{station}, {label}. {detail}". The popup escapes it, so it shows as text, not markup. So text
the upstream chose reaches a rider-facing response: H6 on the 2026-10-12 record's live list,
which stands apart from Q10.

**How each row was measured.** Every world polls every 20 s and fails its contributor from
the first poll after a healthy one (T0), except state (iv), which fails it from the first poll
of the process; "failing" is HTTP 503 unless a row says otherwise. The review round re-derived
the subway's cells, in both tables, from this text alone, by re-running the committed A03
witness; every other cell was checked against the uncommitted drivers' reports, and its
bullet says so.

- **Subway:** the A03 run above, unchanged; its sections (a), (b) and (c) are the five cells.
  Re-derived from the text: the review round re-ran the witness.
- **Railroad:** `test_f01_positions.f01_world`, unwrapped and handed a
  `pytest.MonkeyPatch.context()` as A03 handles `f03_world`, both captures restamped to the
  frozen clock; boards LIRR 102 Jamaica (12 rows, straddling the cap), 129 Medford (one row at
  T0+90 s, so it expires inside the window) and 24 Belmont Park (none in a healthy decode);
  read at every poll to T0+1500 s; the never-decoded world fails LIRR from the first poll with
  Metro-North healthy, then both. **Not re-derived from the text:** the review round checked these cells against the driver's report.
- **PATH:** `path_rt_gen_a.pb` at its own header, boards 26723, 26733 and 26734, read to
  T0+3620 s; the empty world is a header-only `_quiet_feed` poll before the failures. **Not re-derived from the text:** the review round checked these cells against the driver's report.
- **NJ Transit:** `test_njt_rt._feed` and `_trip` (three trips at stop 109), through the real
  `_refresh_njt` and `njt_auth.njt_post` over an `httpx.MockTransport`, read to T0+14400 s;
  `test_health_observations.py:483-494` pins the one NJ Transit state any test pins (a single
  failed poll is quiet). **Not re-derived from the text:** the review round checked these cells against the driver's report.
- **Ferry:** `ferry_vp_a.pb` and `ferry_tu_a.pb` restamped to T0-5 and T0-12 so the two clocks
  can be told apart, stop 17 East 34th Street (10 rows) and stop 4 Hunters Point South
  (none); each leg failed alone and both together, which served identical bodies; read to
  T0+14400 s. **Not re-derived from the text:** the review round checked these cells against the driver's report.
- **`observations-qualified`:** the same refreshers with a fresh `buses` entry beside them so
  `no-feed-fresh` cannot speak for the family, and `test_health_observations.py`'s own
  builders for the bare rule; the tests that pin a state are
  `test_a_system_that_never_decoded_has_dropped_nothing` (`:417-428`, state (iv)), `:272-311`
  (retained rows before and past the band), `:360-409` (the cap poll and after) and `:431-436`
  (healthy and empty), with `backend/tests/test_api.py:1291-1350` and `:1353-1445` driving the
  subway's retained-then-dropped road through the real refresher. The only pin on the second
  blind spot is one failed NJ Transit poll, quiet at 20 s (`:483-494`); no test pins the PATH or
  ferry case, or either blind spot past a single poll. **Not re-derived from the text:** the review round checked these cells against the driver's report.
- **Seams a rebuilder will meet:** `test_f03_boards._Upstream.get(url)` takes no keyword
  arguments, so PATH (`headers=`) and the ferry (`follow_redirects=True`) need a subclass that
  accepts them; driving the real `njt_post` needs a fake rider and secret in the environment
  and the token URL pointed at the `httpx.MockTransport`; the `observations-qualified` worlds
  stood NJ Transit in through `fetch_njt_trains`' own `post` argument instead, with `njt_post`
  never called; and state (ii) on the subway and the railroads needs a capture trimmed to the
  calls due inside the window (the subway's to calls by T0+100 s), or the cap arrives first.
- **The rider's table:** the served bodies above, rendered at `now = served_at` through
  `frontend/helpers.js` loaded with `require()`. **Not re-derived from the text** outside the
  subway's row: the review round checked the other cells against the driver's report.
- **State (iv)'s 502, rendered:** measured after the review round, so **not re-derived from
  the text** either. Backend: one fresh process per family, each family's real `_refresh_*`
  run once, the process's first poll, against a stub answering `httpx.Response(503,
  content=<body marker>, extensions={"reason_phrase": <reason marker>})`, and again against a
  bare `httpx.Response(503)`; NJ Transit through the real `njt_post` over an
  `httpx.MockTransport` with a fake rider and secret, so no request left the process and no
  mint was spent; boards PATH 26734, ferry 18 (both legs failing), NJ Transit 109 and LIRR 12
  (both railroads failing), each read with `/api/status` through the ASGI app. Browser: a
  Playwright spec on the stock fixture answering each family's board route with status 502
  and the body the backend served, opening the station's popup as
  `tests/e2e/stations.spec.js`'s A1s does and its panel as A1q does, and reading each surface
  through `innerText`, which shows the detail's newline as a space (the live region keeps it).
  Both ran on a scratch copy of `22c4824` (PR #131's second commit), whose application code is
  `1d17aa0`'s. That a real HTTP/1.1 socket fills httpx's `reason_phrase` extension was not
  measured.

Seven things fall out, and they are what makes the four states a contract rather than a
subway fix.

1. **Only the subway drops a failing contributor from the board**, and it drops it in three
   of the four failing states. Every other family names its one system on every served board
   (the railroads and NJ Transit) or names none on any (PATH and the ferry), so membership is
   a subway problem; what the others share with it is the second point.
2. **No family's renderer reads `ok`.** `boardFreshness` reads each block's `fetchedAt`
   alone (`frontend/helpers.js:2399-2402`), so an EMPTY board whose block says `ok: false`
   reads exactly like a healthy one until the last decode is 90 s old, on every family that
   has a block, and so does a populated NJ Transit board, whose kept rows stay `reported`;
   only retained rows (the subway's and the railroads' partial failures) say "showing last
   known" from the first failed poll. A never-decoded block, whose `fetched_at` is null,
   never reaches the line at all.
   For a board with no block, `ingestSystems` substitutes one marked `ok: true`
   (`:1830-1841`). The field that distinguishes failed from healthy is served and unread.
3. **"Never decoded" has three shapes today.** A subway group that never decoded is absent;
   an LIRR beside a healthy Metro-North is a block with `fetched_at: null`; a single-system
   family, and a railroad board with both railroads failing, is an HTTP 502 whose `detail` is
   the upstream error with its URLs scrubbed, which the popup and the panel print and the
   panel speaks (rendered in the browser; the text is under the rider's table, and H6 on the
   2026-10-12 record's live list). Only the second is a field a board renderer could read.
4. **Expiry is a retaining family's rule only.** `drop_expired_arrivals` runs on a partial
   failure of the subway or the railroads; PATH, NJ Transit and the ferry keep their last
   decode whole, and so does the railroad on a TOTAL failure (`pollers.py:600-618` returns
   before any merge), so a departed train is served as `reported` and counts down to "now"
   for as long as the outage lasts (an NJ Transit row read "as of 4h ago departs now" at
   T0+14400 s). State (ii) therefore exists for two families and is replaced by state (i)
   without end for the other three. Q9 poses it.
5. **An empty board has no content clock on three families.** PATH's and the ferry's board
   `feed_timestamp` is the oldest row's `observed_at` (`backend/cache.py:361-399`), so it is
   null on every empty board, healthy or failing, and the subway's is null on any board with
   no contributor. Only the railroads and NJ Transit carry a block clock an empty board could
   age by, which bounds what Q4 can buy outside the subway (see Q4).
6. **The failing families' operator surfaces are not symmetric either.** `/api/status`
   carries `subway_feeds`, `railroad_feeds`, `path_feeds` and `ferry_feeds`, but no NJ
   Transit list: `app.state.njt_feed_health` is written (`backend/pollers.py:986-994`, whose
   comment says it is "Surfaced on /api/status") and not served (H5 on the 2026-10-12
   record's live list).
7. **The operator's code is silent in exactly the states a rider cannot see.** It never fires
   for a system that has never decoded, on any family (the first KNOWN BLIND SPOT,
   `backend/models.py:1173-1179`, measured over 91 polls), nor for PATH, the ferry or NJ
   Transit failing while serving nothing (the second, `:1180-1186`, measured over three
   hours), and in a total railroad outage it never names Metro-North, whose undated rows are
   neither carried nor dropped: `backend/routes/status.py:451-454` says so and the models list
   does not, and it was measured silent for 30 minutes on an outage that began with LIRR
   empty. Its dropped rung is wider than its comment: it fires 600 s after a retaining
   system's last decode whether the cap took the rows, they expired, or there were none
   (`backend/routes/status.py:520-528`; `backend/models.py:1112-1114` names only the cap). And
   no surface names the system: `/healthz` publishes the code alone (`routes/status.py:689-691`
   reduces the names to a boolean), and `/api/status` carries neither the code nor the names.

### A1.3 The contract, amended

#### To 3.5: the membership clause (A10)

**Every producer that hands the per-system reader a content-clock map records a key for
EVERY system that decoded this poll. A system absent from the map did not decode.** The
reader already says this in a comment (`backend/pollers.py:449-457`); this makes the
membership contract text, so a producer that departs from it is a contract defect rather
than a style. **The VALUE under a decoded system's key is its header's time, and, for a
generation that carried no usable header, whatever Q6 decides**: the reader's convention,
and this amendment's recommendation, is `None`. The membership stands under any answer to
Q6, including its third alternative, a refusal, which makes the generation a FAILURE and so
belongs in the poll's failed list; today's railroad producer does neither, recording no key
and no failure.

"No usable header" covers two cases and the clause means both: a header that carries no
timestamp, and a header this contract excludes by policy. Metro-North's header decodes and
is not a freshness signal (2.2, `RAILROAD_FRESHNESS_SYSTEMS`), so under the clause Metro-North
is recorded rather than left out, with the value Q6 gives a generation with no usable
header. Under the recommended `None` its block is null today and stays null; what changes is
that the exclusion stops resting on absence, which is the one thing the reader reads as "did
not decode".

Every producer, measured against the clause by running a timestamped generation, then a
decoded one whose header carries no timestamp (unset and `0` behave alike, because
`_header_timestamp` returns `float(ts) or None`, `backend/feeds/shared.py:155-159`), then a
failure, through the real fetchers, refreshers and routes:

| Producer | Hands the reader | A decoded, headerless generation publishes | Obeys the clause |
| --- | --- | --- | --- |
| Subway (`pollers.py:561`, map at `backend/feeds/subway.py:344`) | a map of every decoded group | the group's block `null`; the envelope `null` when every group is headerless, otherwise the oldest DATED group's time, so a headerless group drops out of the envelope's fold (`subway.py:342-348`) | **yes** |
| Railroad (`pollers.py:667`, map at `backend/feeds/railroad.py:1320-1327`) | a map of the decoded systems that sent a header and are admitted | LIRR's block **900**, the last clock, and the envelope 900 (the A10 run) | **no**: a headerless LIRR is absent, which reads as "did not decode" |
| NJ Transit (`pollers.py:1005-1014`) | `{njt_feed.SYSTEM: feed_timestamp}`, every poll that decoded | the block and the envelope `null` | **yes** |
| Alerts (`pollers.py:1263-1272`, map at `backend/feeds/alerts.py:582`) | its own map with the same rule, every decoded feed keyed | the system's `content_at` `null`, so the envelope's content clock is `null` (`backend/routes/status.py:64-71`) | **yes**, by a rule of its own rather than through `_system_freshness` |
| PATH, the ferry, buses (`pollers.py:782`, `:836`, `:294`) | no map: each envelope carries no block (`backend/cache.py:306-313`) | the envelope `null` and `feed_age_s` `null` | not applicable: a single-system envelope writes the fetch's clock unconditionally, so a headerless success is `null` and a failure keeps the last clock by returning before the write |

**The railroad is the only producer that departs from the clause, and `pollers.py:706` is
the only conditional `feed_timestamp` write in the poller**: the others are `:294`, `:582`,
`:782`, `:836` and `:999`, each unconditional. Neither departure is pinned by a test: the
backend suite passes 2035 of 2035 with the producer recording every decoded system and with
`:706` writing what it computed, each mutation applied to a scratch copy of the tree, while
the A10 witness turns red on each (nine checks move under the first, three under the
second). The first mutation as first run keyed only the admitted systems; the review round
re-ran it as the clause reads, Metro-North recorded as `None` too, and measured the same
2035 and the same nine.

**What the envelope and `/api/status` publish for a headerless success.** Today, for the
railroad, the last clock: 900 on the envelope and `feed_age_s` 100.0 at 1000, growing 20 s per
20 s poll, measured over five headerless polls from 100 s to 180 s. That age is read by `/healthz`, not by
`/api/status`, which computes `feed_age_s` (`backend/routes/status.py:112-118`) and decides
nothing from it; the reading is `upstream_ok = feed_age is None or feed_age <
FEED_STALE_AFTER_S` (`:302`, its docstring "unknown is tolerated, having data beats
penalizing a missing timestamp" at `:288-290`), so the comment at `pollers.py:700-702` names
the wrong surface when it says "/api/status reads an unknown feed age as healthy". Measured
through it: a `null` age with a current poll is `pass` with nothing degraded; content 600 s
old is `fail`, `no-feed-fresh` and `feed-content-stale`; a `null` age behind a poll stalled
600 s is `fail`, `no-feed-fresh`; and content exactly 90 s old is already stale, the boundary being `<`. So a decoding LIRR whose own entity
clocks are current reads `feed-content-stale` on `/healthz` once the kept clock is 90 s old,
which is the opposite error to the one the branch guards against. **The contract monitor
never reads `feed_age_s`** (no occurrence in `backend/scripts/contract_monitor.py`;
`production:feeds` reads `age_s` alone, `:2532-2533`, and passes with `feed_age_s` set to
`None`, `0.0`, `99999.0` or a string). It sees a headerless upstream directly:
`railroad-realtime` WARNs "LIRR omitted its header timestamp" (`:825-832`, pinned by
`test_railroad_realtime_lirr_missing_header_is_warn`), and it sees `/healthz` through
`production:healthz`, which FAILs on any recognized code (`:2295-2309`). Q6 asks what a
headerless generation publishes and Q7 what the envelope does with it.

#### To 3.2 and 3.5: the empty-board states (A03)

**The four states, in the report's own words, are this amendment's acceptance:** "Regression
cases must distinguish genuinely fresh empty service from stale, failed and unknown empty
service in both surfaces." The 2026-10-12 record names them fresh and empty, stale, failed
and never decoded (Section 4 item 3). Each must be distinguishable in a FIELD of the served
board (on PATH and the ferry, whose boards carry no block today, only if Q10 is answered yes),
3.5's standard ("a healthy contributor and a lagging one now differ in a field rather
than in nothing"), and each takes a word from 3.2:

| State | What the served board carries that makes it this state | What the rider reads: popup, panel, spoken | 3.2 word |
| --- | --- | --- | --- |
| **Fresh and empty** | every expected contributor's block reads `ok: true`, with `fetched_at`, and `feed_timestamp` where its provider dates content, within `OBS_FRESH_S` of `served_at` | `No trains` (`No boats` on a ferry board) and no line; spoken "Prospect Av, Subway. No trains." | `No trains`, `No boats`: shipped, and added to 3.2 below |
| **Stale** | an expected contributor reads `ok: true` and its `feed_timestamp` (the content) or its `fetched_at` (the poll) is older than `OBS_FRESH_S` | the board's line `as of {age} ago` above `No trains`, the age the worse of the two (Q4); spoken "Prospect Av, Subway. as of 10m ago. No trains." | `as of {age} ago`, 3.2's first table; the board's form is `helpers.js:2469` |
| **Failed** | an expected contributor reads `ok: false` and its `fetched_at` is a number: it decoded in this process and is failing now | the board's line names it once its last decode is `OBS_FRESH_S` old, the grace the status line gives a failed system, in the word Q11 decides (`{system} not reporting` recommended, `as of {age} ago` the alternative); whether it also takes the `No trains` slot is Q3 | `{system} not reporting` if Q11 is answered yes (the row added below), otherwise `as of {age} ago` |
| **Never decoded** | an expected contributor reads `ok: false` with `fetched_at: null` (Q2), both: a substitute block can have a null clock with `ok: true` | as failed, from the first poll, as the status line says `not reporting` at once | `{system} not reporting`, 3.2's first table: "a system that has never decoded" |

**Two rules make the table a contract rather than four cases.** Both are this amendment's
proposals rather than questions, and A1.5 lists them with the others, so a decision can
reject one by name.

- **A failing expected contributor with no row on the board is said whether or not other
  contributors' rows are there**, at once if it has never decoded and once its last decode is
  `OBS_FRESH_S` old if it has, so a single failed poll is not news on a board any more than on
  the status line (`staleness()` raises a failed system at that age,
  `frontend/helpers.js:2262`). `boardSystemLine` already "speaks only for what the rows
  cannot" (`frontend/helpers.js:2446-2449`), and a contributor whose rows are gone is exactly
  what no row can speak for. So the line names it, in Q11's word for a failed one, on a board
  with rows too. On a subway board "no row of it" is a join, since a row names its route and
  not its group: the rows' route ids against the contributor's `SystemFreshness.routes`, or
  Q1's table for a group that has never decoded and so lists none. It matters at the 73 parent stations whose routes in the live index
  belong to two or three groups (reading each group's routes from its name, with GS placed
  by the committed capture; Q1), where one group's expiry leaves the others' rows looking
  like the whole board.
- **One clause per population, never merged, in the status line's order.** A board whose
  contributors are in different states says each, as `staleness()` builds its clauses
  separately (`helpers.js:2287-2290`): the stale population's `as of {age} ago`, then the
  failing population's clause. A system in one state announced with
  another's age is the defect the status line's own split was made to prevent.

**The membership rule for a station board's `systems` map changes from "contributors with
rows indexed here" to "expected contributors".** For the subway that is the groups Q1's
source names for the station, UNITED with any group that has rows indexed there, so a
diverted group's rows still vote: the committed capture carries route 5 at seven
Lexington Avenue stations (622 to 630, 116 St to 51 St) that the live static index does not
list for it. The union is also what keeps F03's own acceptance world standing: that world
primes the static index empty (`backend/tests/test_f03_boards.py:215`), and on a scratch copy
of the tree with an expected-ONLY prototype of this rule the 2026-09-05 F03 witness stops at
`TypeError: 'NoneType' object is not iterable`, its board naming no contributor, where with the
union it runs exactly as today. For the other four families the expected contributor is the board's one system,
which the railroad and NJ Transit boards already name and PATH and the ferry boards do not
(both serve `"systems": None`, `backend/routes/path.py:129` and `backend/routes/ferry.py:122-123`),
so for those two the rule reaches a field only if Q10 is answered yes.
3.5's second rule survives in substance: a group that is not expected here, and has no rows
here, does not take part, so a down SIR group still cannot age a Manhattan board.

**`SystemFreshness.fetched_at` on a board.** The field is already `float | None`
(`backend/models.py:191`), and the comment above it says what it is: "This system's last poll
that DECODED". On a board, `None` therefore means exactly one thing, that this expected
contributor has not decoded in this process, and Q2 asks that it be published rather than
omitted. It is the field that tells never decoded from failed, and it is already served that
way by one family: an LIRR that never decoded beside a healthy Metro-North (A1.2).

**The status line and `observations-qualified`, in the same four words.** The status line
already speaks all four for the map's own sources: silence for fresh; `as of {age} ago` for a
stale poll or stale content (`staleness()`'s first two populations, `frontend/helpers.js:2261-2268`);
`{system} not reporting` for a system that has never decoded (its third, `:2256-2260`); and,
for a system that decoded and is failing, `as of {age} ago` once its last decode is 90 s old.
That last reading dates what the map draws while the map is still holding the system's
vehicles, and it does not change once the cap has taken them: measured through `staleness()`
with 1-7+S failing and nothing of it drawn, the line reads "trains: 1-7+S group as of 10m ago"
at the cap poll and "as of 25m ago" at 1500 s, where 200 s into the failure, its vehicles
held, it read "as of 3m ago". So a failed contributor with nothing on a board and a failed
system with nothing on the map past the cap are one state on two surfaces, and Q11 asks which
word it takes on both. A third shipped surface has a word for the never-decoded half already:
the feed strip and the vehicle popup's footer say `Not reporting` for a feed with no age
(`feedStateWords`, `frontend/helpers.js:822-827`, waived as a state no fixture world reaches at
`tests/e2e/pins.spec.js:1186-1188`). The operator's code is where the four states are not yet
said: it is silent for a never-decoded system on every family and for PATH, the ferry and NJ
Transit failing with nothing to show (A1.2, point 7). Q5 asks whether the four states govern
it, which closes both of 6.2's blind spots; answered no, the amendment records precisely why
they stay open, which is the cost Q5 states beside its recommendation.

**The vocabulary, weighed.** The candidates the amendment considered are all shipped
strings, and no state needs a NEW word; one NEW form is proposed, for naming a subway group
on a board (Q3), in the rows below. `No trains` (and the ferry's `No boats`) is the
fresh-and-empty word as shipped; it is in neither of 3.2's tables, which list qualifiers, and
is added to the second below because the four states turn on when it may be said. `as of
{age} ago` is stale's, unchanged in form. `{system} not reporting` is never decoded's as
shipped and, if Q11 is answered yes, failed's as well. `showing last known, as of {age} ago` is not
a candidate for any of the four: it qualifies a ROW that is held, an empty board holds none,
and it stays the word for a failing contributor's retained rows, state (i), exactly as
shipped. `age unknown` is not either: it is clause (c)'s word for an observation whose
provider normally dates it and did not, and an empty board has no observation, so a
contributor whose content cannot be dated (Metro-North by policy, or a headerless
generation under Q6) leaves an empty board aged by its poll, with 3.2's undated clause riding
the line as it does today.

**Added to 3.2's second table**, three rows of shipped strings and one NEW form:

| Provenance / state | The rider word | Note |
| --- | --- | --- |
| An empty board, every expected contributor fresh | `No trains`; `No boats` on a ferry board | Shipped (`frontend/helpers.js:4047`, `:4210`, `:4386`, `:4743`, `frontend/systems/subway.js:470`; the panel's `No trains.` and `No boats.` at `frontend/stations.js:703` and `:853`), and in neither of 3.2's tables before this amendment, although every board has said it since before this contract. A statement about service, so whether it gives way to `{system} not reporting` when no expected contributor is up is Q3. |
| An empty board, an expected contributor stale | `as of {age} ago` | The board's line as shipped, its age the worse of poll and content (Q4). |
| A board, an expected contributor failing with no row on it | `{system} not reporting` | The shipped status-line clause on a board. On the status line it means never decoded; on a board it means "failing, and nothing of it is here", which covers never decoded and failed alike, because a board with no row of the contributor has nothing to date. Q11 for a failed contributor; Q3 for the slot. |
| A board, a failing subway group, named | **`{routes} trains not reporting`** | NEW, and only if Q3 and Q1 are answered yes: the subway's `{system}` on a board. A group key is a feed's name ("1-7+S") and the panel speaks the line, so 6.2's reason for "Metro-North" over "MNR" applies. It joins the panel's shipped route noun ("5 train", `arrivalSentence`, `frontend/helpers.js:5423-5441`) to 3.2's `not reporting`: "2 and 5 trains not reporting" at Prospect Av. Where the routes cannot be named, the board's own system word, `Subway` (`POPUP_SYSTEM_WORDS`, `frontend/helpers.js:986-993`), never the key. |

### A1.4 What changes where

In 4.x's form. Each subsection ends with the design's own line, because the places where
something correct today stops being correct are the useful part of a change list.

#### The models

| Model | Change |
| --- | --- |
| `StationArrivals.systems` | Its MEANING changes, not its type: the expected contributors (A1.3, Q1) with a never-decoded one as a block (Q2), where today it is the groups with rows indexed at the station. Nothing new in `SystemFreshness`: `fetched_at` is already `float \| None` (`backend/models.py:191`). |
| `PathStationArrivals.systems`, `FerryStationArrivals.systems` | From always null to the board's one system, if Q10 is answered yes. |
| `RailroadStationArrivals`, `NjtStationArrivals` | None: each board already names its one system. |

**Asserted today that this makes false:** `StationArrivals`'s comment states the
contributor rule as "a group contributing nothing at this station does not participate"
(`backend/models.py:369-373`), which becomes "a group not expected at this station, with no
rows here, does not participate". And one sentence that is already false and that Q2 would
put on a board: `SystemFreshness.retained_since` "Set while this system's data is being
carried forward from its last good poll ... a long-failed system reports ok=False with
retained_since=None and no data at all" (`backend/models.py:216-220`). A1.2 measured the
field set on a system that never decoded, and, past the cap, null for one poll in 31 and then
set again, because the capped branch records no clock and the next poll starts one
(`backend/feeds/shared.py:452-456`; H4 on the 2026-10-12 record's live list). Retention
is unchanged by this amendment (section 5); the build has to word that comment truthfully
and must not read `retained_since` as "something is held".

#### The producers

| Producer | Change |
| --- | --- |
| `feeds/railroad.py` | Record `feed_ts_by_system[system]` for every system that decoded (`:1320-1327`): the header's time for a dated LIRR generation, and for an undated one and for Metro-North (by policy) the value Q6 decides, `None` recommended. The envelope's `timestamps` list keeps its gate, so the envelope still folds LIRR alone. |
| `pollers._refresh_railroads` | The keep-last-known branch (`backend/pollers.py:696-708`) as Q7 rules it. |
| The subway, NJ Transit and alerts producers; PATH, the ferry and buses | None: each already obeys the clause or has no map (A1.3's table). |

**Asserted today that this makes false:** two comments that rest the Metro-North exclusion on
absence, "a system that is not admitted here is simply absent from the map, so its block
reports None without anything downstream knowing why" (`backend/feeds/railroad.py:1322-1324`)
and "METRO-NORTH IS ABSENT FROM THIS MAP AND THAT IS THE WHOLE EXCLUSION"
(`backend/pollers.py:675-678`); the exclusion is unchanged and stops being absence. And the
envelope comment's account of when the branch fires (`backend/pollers.py:696-702`), which
A1.1 measured firing on a headerless success it does not mention.

#### The endpoints

`/api/subway-arrivals/{id}` (`backend/routes/subway.py:108-124`): the three selectors read the
expected set. `_contributing_systems` (`backend/cache.py:441-462`) gains the station's
expected groups beside the groups indexed there; `_contributing_freshness` (`:505-516`)
publishes a block for each, never-decoded included (Q2), so it returns `None` only where
nothing is expected; `_oldest_contributing_content_at` (`:465-502`) keeps its three rules over
the wider set; and `_oldest_contributing_fetched_at`'s aggregate fallback (`:539-543`) is
reached only by a station with no expected contributor: on the live index, three of the
496 (S01, S03 and S04, served by FS alone) until Q1's probe places FS, and none after it. The other four arrivals routes change only as Q9 and Q10 are answered:
`/api/path-arrivals` and `/api/ferry-arrivals` serve their one system's block instead of
`"systems": None` (`backend/routes/path.py:129`, `backend/routes/ferry.py:122-123`), and the
single-system boards answer a failed first poll with a board rather than the recorded 502
(`_require_filled_cache`, `backend/cache.py:321-333`, the recorded error raised at `:328-330`,
called at `backend/routes/path.py:111`, `backend/routes/ferry.py:106` and
`backend/routes/njt.py:166`). `/api/status` gains nothing for the boards; Q5 says what its
health code reads.

**Asserted today that this makes false:** "MEMBERSHIP IS THE PER-GROUP INDEX, NOT THE SERVED
PAYLOAD" (`backend/cache.py:448-455`), whose argument (a group's rows can be trimmed out of
the response and it keeps its vote) survives and whose premise, that the index is the whole
of membership, does not; `_contributing_freshness`'s "publishing only the contributors
answers theirs" (`:510-512`); and `_oldest_contributing_fetched_at`'s fallback comment, "keeps
the endpoint from regressing to null" (`:540-542`), which is the line that served A03's 0 s
old `fetched_at`.

#### The frontend

| Surface | Change |
| --- | --- |
| `boardFreshness` (`frontend/helpers.js:2395-2404`) | Reads each block's `ok`, `fetchedAt` and `feedTimestamp`, all three already ingested (`:1846-1856`), and returns each expected contributor's state rather than one poll age (Q4). |
| `boardSystemLine` (`:2462-2471`) | Speaks for an empty board's stale contributors by content age as well as poll age (Q4), and for a failing contributor with no row on the board whether or not other rows are there, in Q11's word (A1.3's proposal). |
| The `No trains` slot | `frontend/helpers.js:4047` (railroad), `:4210` (PATH), `:4386` (`No boats`, ferry), `:4743` (NJ Transit), `frontend/systems/subway.js:469-470` (subway), and the panel's `frontend/stations.js:701-705`: as Q3 rules. |
| The panel's spoken line | `arrivalsSpeech` (`frontend/stations.js:841-856`) already carries the board's line before the rows; it carries the new clauses the same way, in the same one write (`speakPanel`'s rule, `:858-870`). |
| `ingestSystems`' substitute block (`:1830-1841`) | Unchanged. After the build `systems: null` on a board means one of three things: an older backend, for which `ok: true` is the right default; a subway station with no expected contributor (S01, S03 and S04 until Q1's probe places FS); or, if Q10 is answered no, any PATH or ferry board, as today. |
| N6 and the announcement rule | The board's line reaches no live region of its own: its only writer is the panel's one composed write to `#stations-announce` (`frontend/stations.js:894-898`, through `speakPanel` at `:873-878`), and `#page-announce`'s writers (`frontend/systems/shared.js:1872`, `:1903`) never see it, so N6's open half is not walked into. What the change does meet is `announcementWorthy`'s clause 4 (`frontend/helpers.js:5523`), which announces a line only when it appears or goes: a board going from `as of 10m ago` to `{system} not reporting` would change on screen and stay silent, so the build makes a change of the line's KIND news, as `arrivalQualifier`'s `kind` already is for a row (`:2426-2430`). |

**Asserted today that this makes false:** `boardFreshness`'s comment, "the age of its worst
system's last poll" (`frontend/helpers.js:2391-2394`), and `boardSystemLine`'s, "an EMPTY
board, whose 'No trains' is only as current as the poll behind it" (`:2450`), which become
"as current as the worst of its expected contributors' polls and contents, and only as true
as their being up".

#### The monitor

The monitor reads one rider board, and it is two checks on one fetch, not one:
`_fetch_production_board` (`backend/scripts/contract_monitor.py:3028-3052`) reads
`/api/subway-arrivals/G08` (the station chosen at `:368-377`), and `production:board-clock`
(`:3147-3199`) and `production:board-contributors` (`:3211-3277`) judge it; the README
"Monitoring" section describes both (README at `1d17aa0`, lines 1121-1138). Both FAIL a populated
board missing any contributor's `feed_timestamp` (`_board_contributor_clocks`, `:3088-3117`), and
both WARN on ANY empty board (`:3174-3179`, `:3268-3274`): "so it has no clock to judge" on
`production:board-clock` (`:3178`), "so it has no contributors to tell apart" on
`production:board-contributors` (`:3272-3273`).
Measured with the monitor's own board builder over amendment-shaped boards: a never-decoded
NQRW block added to a populated G08 board turns both lines FAIL ("lacks a feed_timestamp on
NQRW"); a failed-after-decoding NQRW block passes `production:board-contributors`, named "not
judged" there, and passes `production:board-clock` while its kept clock is inside the 600 s
band, which WARNs on it once it is past; an empty board stays
WARN whether its `systems` is null, every block never decoded, or its content 900 s old. So the
build gives the two lines the four states: a never-decoded expected contributor with no rows
is its own named state rather than a missing clock, and an empty board is judged by its blocks
(failed or never decoded is not the same WARN as fresh and empty, which is the WARN's own
reason, "no clock to judge", stopping being true). The monitor's realtime arms need nothing
from Q6 or Q7 (A1.3), but measuring them found two gaps a build may close or leave: a
headerless bus feed passes silently (`bus-realtime`, `:1370`), and the PATH and NJ Transit
missing-header WARNs (`:878-883`, `:1252-1254`) are pinned by no test.

**Asserted today that this makes false:** the README's "An empty board, or one that could not
be fetched, is a `WARN`", and the two `if not rows` arms that implement it, whose reason (an
empty board has nothing to judge) is what the four states remove; and the board-contributors
builder's assumption that every block on a populated board carries a clock, which a
never-decoded block (Q2) breaks on purpose.

#### The goldens and acceptance worlds that move

Measured by applying prototypes of the amendment to scratch copies of the tree (never to
this branch) and running the suites:

- **Membership (A1.3, Q1, Q2).** No unit test calls the four membership helpers directly
  (`backend/cache.py:441-548`); they are exercised only through the subway board. With
  expected contributors UNITED with the indexed groups, `test_f03_boards.py` and `test_api.py`
  pass 258 of 258. With expected contributors alone, two acceptance tests fail, which is why
  A1.3 states the union: 6.2's "one healthy subway group beside the aged one",
  `test_acceptance_old_valid_feed_rows_are_dated_stale_and_healthy_rows_stay_distinguishable`
  (`backend/tests/test_f03_boards.py:302`, whose `sorted(systems) == ['1-7+S', 'ACE']` at
  `:367-378` and golden `tests/e2e/fixtures/f03_board_219.json` at `:455-461` both move), and
  6.1's `test_acceptance_other_healthy_contributors_remain_distinguishable`
  (`backend/tests/test_api.py:5572`, `:5682-5683`). With 219's real static routes the golden
  stays equal under the union and not otherwise; it is also rendered by
  `tests/e2e/pins.spec.js:553` (P2c) and `tests/e2e/smoke.spec.js:2462` (C2i). Under the union,
  one never-decoded expected group on a POPULATED board makes its `fetched_at` and
  `feed_timestamp` null by rule 3 (`cache.py:544-547`, `:500-501`), which the monitor reads as
  above (Q2 states the cost).
- **The operator's code (Q5).** Exactly one test pins each silence:
  `test_a_system_that_never_decoded_has_dropped_nothing` (`backend/tests/test_health_observations.py:417`),
  `test_a_failing_group_inside_its_window_with_nothing_to_carry_is_quiet` (`:439`) and
  `test_a_total_outage_leaves_an_empty_entry_undropped` (`:464`). Named at Q5's band, 600 s
  after the last decode or, never decoded, after the only start-of-failure clock a block
  carries today, its `retained_since`, ONE fails, `:464`, and 36 of the file's 37 pass
  (measured by the review round on a scratch copy): `:439` pins quiet inside the window and
  firing at its end, which is Q5's own rule, and `:417` moves only once a never-decoded system
  has a clock that does not restart at the cap (Q5). The 2026-09-05 F10 witness does not move
  under the band, its subway poll being 120 s old. An earlier prototype that named both states
  AT ONCE, which is not what Q5 proposes, failed all three (642 of 645 across the files run)
  and turned two F10 checks, each arm gaining `observations-qualified` beside
  `subway-groups-down`.
- **The railroad (Q6, Q7).** Nothing pins today's producer or today's envelope branch: 590 of
  590 pass with either changed or both, and 441 of 441 across every file that drives
  `_refresh_railroads` with the branch deleted outright. Eight `test_api.py` tests build fakes that mirror
  today's producer (Metro-North decoded and absent from the clock map) without asserting it;
  the build adds the pins Q7's recommendation asks for.
- **The frontend (Q3, Q4, Q11).** No unit test pins today's empty-board silence: a prototype
  that speaks only for an EMPTY board whose every block is down, at once, passes 397 of 397
  `frontend/*.test.js` tests. The rule as A1.3 writes it (boards with rows too, the 90 s grace
  for a failed contributor) was not prototyped, so what it moves is not measured here. One pin constrains the rule's shape:
  `frontend/helpers.test.js:2394` (its `:2432-2438`) expects no line for a body with no systems
  map, whose substitute block has `fetchedAt` null and `ok: true`, so "never decoded" must be
  read as `ok: false` AND `fetched_at` null, never as a null clock alone. The status line's
  vocabulary pin, "trains: BDFM group as of 5m ago; ACE group as of 10m ago; SIR group not
  reporting" (`helpers.test.js:1962-1973`), is what keeps `not reporting` meaning never decoded
  on that surface; it moves only if Q11 is answered yes, and then only past the cap. A new
  popup literal such as `not reporting` in `boardSystemLine` meets `tests/e2e/pins.spec.js`
  P5b's uncovered-literal check (`:1645-1668`) and needs a pin, read from the spec (the e2e
  suite was not run).
- **The compact form (Q8).** Its numbers are under Q8.

**Asserted today that this makes false:** the golden's `systems` map is not among them, under
the union; what becomes false is every pin of a silence (the three operator tests above, and
the A03 checks below), each of which is a record that a rider or an operator is told nothing.

#### The audit reproductions

Both scripts flip in the build and are rewritten rather than deleted, under the directory's
own rule: "As fixes land, rows move to FIXED and their scripts are rewritten to pin the
repair, the way the 2026-09-05 scripts were" (`docs/reviews/audit-2026-10-12/README.md:35-36`),
each carrying the before and the after and the audit's figure in the label of every check
that moved. Nothing here edits either script. Measured on scratch prototypes, the checks the
build turns:

- **`a03_empty_board_hides_failed_feed.py`, 13 of its 27 checks.** The backend half alone
  turns nine: the seven `[backend]` checks that record the board after expiry, at the cap, and
  in the stale-content and never-decoded worlds as identical to fresh empty service, and the
  two `[rider]` checks for after expiry and at the cap. The frontend half alone turns two, the
  `[frontend]` checks for A (content 620 s old) and B (never decoded). Both together turn those
  eleven and two more `[rider]` checks (content 620 s old, never decoded). The other 14 stay
  PASS: the four `[world]` checks; the five `[backend]` checks that describe the world rather
  than the board's silence (the healthy poll, the first failed poll, the board before expiry,
  `/api/status` still listing 1-7+S after it, the board one poll before the cap); and the five
  `[frontend]` checks of the served blocks, the baseline and the CONTROL, whose difference
  from fresh empty service becomes the new line. That matches the script's own kill record (its README row: the
  backend spec turns the backend checks red with the frontend checks green, and the reverse).
- **`a10_headerless_lirr_clock.py`, 12 of its 32 checks.** The producer alone turns nine:
  section B's five at 1000 and 1100 (the content clock 900, the implied age, the 90 s gap to
  the generation's own trip update, the board's inherited clock, the failure after it) and
  section D's four (the captured header on every poll and the age it implies). Narrowing the
  envelope turns three, the "beyond the audit's citation" checks of the envelope and of
  `feed_age_s`. Section C, the subway control, and the two failure-only controls stay PASS, as
  they must: a real failure retains its clock on both sides of the repair.

The rewritten scripts carry the before beside the after, in the form the FIXED scripts
already use ("BEFORE (this script's record at ...)" and "each prints the BEFORE value beside
the one it measures", `docs/reviews/audit-2026-10-12/a02_unreadable_trips_promoted.py:50` and
`:213-216`), and their README rows record the kills of the repair.

**Asserted today that this makes false:** both scripts exit 0 because the defect holds, so
each one's success is a report that a rider is being misled, exactly as 4.7 said of F01's and
F03's; these two are the files whose passing this amendment's build is supposed to break.

### A1.5 What this does not decide, and the open questions

**It does not decide open question 1**, the LIRR prediction threshold. It was opened on
2026-09-13 on `claude/freshness-6-2-boards` and is tracked at
`docs/reviews/audit-2026-09-05.md:757-806`, whose input is "a week of deployed observation"
and whose state there is "Not decided here"; the 2026-10-12 record's live list carries it as
"Open question 1", with `backend/tests/test_health_observations.py` and
`backend/tests/test_f01_positions.py` as its witnesses. Nothing below moves the LIRR
prediction row of 3.3, and an empty LIRR board has no prediction to age.

**It does not decide F06/A05 or F07/A06**, which stay Release 2 design work (the 2026-10-12
record, Section 4 item 6). One consequence touches this amendment and is stated so it is not
mistaken for a decision: Q1's static index is loaded once per process (F07/A06), so a station
whose routes change in a publication keeps its old expected contributors until a restart.

**It does not decide H1 or H3.** Each rides the branch that next edits its file (Section 4,
the closing notes): H1 the railroad decoder, H3 the popup controller. A build of this
amendment edits the popup's renderers but not its controller, so H3 does not ride it unless
that build also opens the controller.

**It does not change the retention horizon or the 90 second budget.** `FEED_RETENTION_MAX_S`
and `FEED_STALE_AFTER_S` are borrowed with their derivations, as section 5 already says of
both; the four states are read against them, never in place of them.

**Proposed in the body, not asked.** Four things A1.3 and A1.4 propose without a question, each
named here so a decision can reject it by name rather than find it in passing: the
membership clause's key rule (a decoded system always has a key, whatever value Q6 gives it);
the union of expected and seen contributors (measured necessary for F03's own world, A1.3);
the line rule for a failing contributor on a board that has other rows, with its 90 s grace
(A1.3), whose effect on the frontend suite is not measured (A1.4); and one clause per
population on a board's line.

#### The open questions

Each is phrased so a yes or a no settles it, with the recommendation stated, in section 5's
form. **None is answered here.** The decisions are the operator's and are appended below
this amendment before it merges, as they were on 2026-09-12; until then nothing in A1.3 or
A1.4 that depends on a question is decided by being written down, and each place that
depends on one names it.

**Q1. Should a subway station's EXPECTED contributors come from the static routes-per-station
index through a route-to-group table, rather than from a memory of the groups seen at that
station in this process?** The index is a required, validated member since PRs #116 and #126
(`static_data.load_subway_station_routes`, `backend/static_data.py:643-673`, set on
`app.state.subway_station_routes` at `backend/warmups.py:196`), and its coverage is complete:
over the live archive (Last-Modified 2026-10-28, `feed_version
20261028-fall-2026-pick-with-long-term-supplements`) it gives all **496** parent stations at
least one route, across **29** route ids, the same 29 as `routes.txt`. **But the table this
question presumes does not exist.** `SUBWAY_FEED_URLS` (`backend/feeds/subway.py:35-44`) maps
the eight group KEYS to URLs, and a group's routes are derived per poll from the trains it
served (`pollers._routes_by_system`, `backend/pollers.py:375-392`, stored as
`SystemFreshness.routes`), a choice recorded twice as deliberate: "rather than from a
hand-maintained table, so it cannot drift from SUBWAY_FEED_URLS" (`pollers.py:381-383`), and
the model's own "The alternative was duplicating the backend's group table in JavaScript,
which would then drift" (`backend/models.py:226-227`). Read from the keys, 7 of the 29 ids
name no group (6X, 7X, FS, FX, GS, H, SI); the only subway capture committed is 1-7+S's, which
places GS in 1-7+S (by measurement, where the first character would say G) and leaves FS and H
unplaceable from anything in the tree; and the index is a union over weekday, Saturday and
Sunday service, so it expects BDFM at 16 parents on a weekend when only the weekday B serves
them, which is harmless (a healthy group with no rows there is fresh and empty) but means
"expected" is "may serve", not "is serving". Yes means a new static table of 29 rows in
`feeds/subway.py`, beside the URLs it must agree with, its FS, H and SI rows taken from a
keyless probe of the eight live group feeds before the build (6.0's shape: a
measurement nobody has taken, not a decision nobody has made), and a check that every route
a live group serves is in the table; no means a memory of groups seen, which cannot name a
group that has never decoded here, so the fourth state stays unsayable on the subway.
**What the probe measures:** the route ids each group feed carries over a full service day,
the overnight shuttles included, since one fetch sees only what runs at that minute (Step
0). **An unverified lead, not a fact this rests on:** MTA's developer documentation
describes the ACE feed as also carrying H, the BDFM feed FS, and the 1-7 feed GS. Nothing
in the tree confirms the first two, and the probe is what would. Which group carries a
route is feed data, under the README's branding rule (`README.md:1351-1359`): the table
would hold what the feeds are measured to carry, not a document's grouping or a line's
branding.
*Recommendation: yes, static, with the probe first and the union of expected and seen that
A1.3 states, because it is the only source that makes "never decoded" sayable, and a
29-row table checked against the feeds it describes is a smaller drift risk than the one
`models.py:226-227` rejected, a copy in a second language with nothing checking it.*

**Q2. Should a never-decoded expected contributor publish a block with `fetched_at: null` and
`ok: false`, rather than be omitted from `systems`?** It is the only field that tells never
decoded from failed (A1.3), and one family already serves it that way: an LIRR that never
decoded beside a healthy Metro-North (A1.2). Yes also means the block carries whatever
`retained_since` the retention clock gives it, which A1.2 measured as set for a system that
has retained nothing (the clock is recorded "WHETHER OR NOT ANYTHING WAS CARRIED",
`backend/feeds/shared.py:457-467`), so a renderer must read `fetched_at`, never
`retained_since`, to know whether anything is held. And it has a measured cost on a POPULATED
board: by the selectors' rule 3, one never-decoded contributor makes the board's own
`fetched_at` and `feed_timestamp` null (`backend/cache.py:544-547`, `:500-501`), which the rows
do not need (each carries its own clock) and which turns both of the monitor's board lines
FAIL until the monitor learns the state (A1.4). No means never decoded stays an absence,
which is the state A03 measured. *Recommendation: yes.*

**Q3. On an empty board whose every expected contributor is failed or never decoded, should
the board say `{system} not reporting` in the slot `No trains` occupies, rather than `No
trains` with the clause on the system line?** `No trains` asserts a fact about service that
the app cannot know while the feed is dark. Which word the line gives a contributor that
decoded and is now failing is Q11; this question is only the slot. The rider word for a subway group: today
`staleness()` names one by its feed key and the noun "group" ("1-7+S group",
`frontend/map.js:45` and `frontend/helpers.js:2285`), and the panel speaks the board's line,
which is why 6.2 named Metro-North in the rider's word rather than "MNR". A group key is a
feed's name and no word a rider uses for a train, so on a board the group would be named by
the routes it serves at this station, in the panel's shipped spoken form ("5 train",
`arrivalSentence`, `helpers.js:5423-5441`): "2 and 5 trains not reporting" at Prospect Av, the
NEW form A1.3's added rows record. That needs Q1's table; without it, the board's own system
word, "Subway not reporting", never the key, which 6.2 kept off the spoken line. No means `No trains` stays in the slot and the
clause rides the line. *Recommendation: yes, replace it, naming the group by its routes at
the station when Q1 is answered yes.*

**Q4. Should an empty board age by CONTENT (the worst expected contributor's
`feed_timestamp`, at `OBS_FRESH_S`) as well as by poll, through one helper, so stale empty
service reads `as of {age} ago`?** Today `boardFreshness` reads `fetchedAt` alone
(`frontend/helpers.js:2399-2402`), so A03's world A, content 620 s old behind a 0 s old poll,
reads as fresh. What content age can buy differs by family (A1.2, point 5): the subway's
contributors and the railroad and NJ Transit blocks carry a content clock an empty board can
read; PATH's and the ferry's boards have none once empty, since their `feed_timestamp` is the
oldest row's; and Metro-North's is null by policy, so an empty Metro-North board stays aged by
poll alone, as 3.2's undated clause already accepts. Yes means one helper that takes the
worse of a contributor's poll age and its content age, the two read through the door
(`ingestSystems` already carries both, `:1846-1856`). *Recommendation: yes.*

**Q5. Should the same four states govern the status line and `observations-qualified`,
closing 6.2's second blind spot: a healthy poll that decodes an empty feed is fresh and empty
and says nothing; a failing poll with no rows is failed, sayable from the block's `ok` without
any row?** For the status line the four states already hold (A1.3), save the case Q11 poses,
so the weight of this question is on the operator's code. Measured (A1.2, point 7): the code is silent for a never-decoded system on every
family and for PATH, the ferry and NJ Transit failing while serving nothing, and the line
that makes the first so is `fetched_at is not None` in the dropped rung
(`backend/routes/status.py:526`). In the four words: fresh and empty says nothing, which is
also the overnight state the vehicle map already treats as silent for the two families
where emptiness is ordinary (`clearOnEmpty`, `frontend/map.js:118-131`); stale is the code's
age rule as it stands, at the operator's 600 s band; failed and never decoded are named
from the block's `ok`, and the time since the system last decoded or, having never decoded,
since this process first tried it, at the same band. That last clock is not served today: a
never-decoded system's `fetched_at` is null, and its `retained_since`, which starts at its
first failure, restarts after the cap (null one poll in 31, A1.4's models), so yes needs a
first-attempt clock kept beside the block for the rule to read. Yes closes the FIRST blind spot as
well as the second, and reverses the reason given for the first, "one that never has took
nothing from riders here" (`backend/models.py:1174-1175`): A03 is the measurement that it
takes their board. It needs a block on the rule's input for PATH, the ferry and NJ Transit,
which pass none (`routes/status.py:585-590`, `:611-616`), and an operator copy of NJ
Transit's failure, which `/api/status` does not serve although two comments say it does
(`backend/pollers.py:990-992`, `backend/main.py:278-280`; H5 on the 2026-10-12 record's
live list). It must exclude a family that is
not configured, which is a deployment fact the page already keeps quiet (`isNotConfigured`,
`frontend/helpers.js:4708`).
And it has a cost to state beside it: the contract monitor fails a run on any recognized
code (`backend/models.py:1153-1155`), so a group down since a deploy fails the monitor from
its tenth minute, where today it passes. *Recommendation: yes, named here, built with A03.*

**Q6. Should a decoded generation with no usable header publish `None` for its system, the
reader's convention, rather than a clock taken from its entities, the HTTP headers, or a
refusal?** It is the reader's convention and three of the four producers already follow
it (A1.3's table). The alternatives each turn the A10 witness red, as its README records of
its second fixes: dating the generation by its newest entity clock, by its fresh rows, by
`Last-Modified` or by the poll time, or refusing it as a failure. What `None` costs is
measured: where a family's ROWS take their clock from the header (the subway, `subway.py:148`
and `:228`; the ferry, `ferry.py:282`; NJ Transit, `njt.py:701` and `:725`), a headerless
generation leaves every row undated on an age-gated row, so the rider reads clause (c)'s
`age unknown` and `/healthz` raises `observations-qualified` (non-gating) at once (3106 of 3106
rows on the subway capture, 50 of 50 on the ferry's, 6 of 6 in NJ Transit's test world); that
is today's behaviour for all three, which already publish `None`. LIRR's rows carry their own
trip-update clocks (`railroad.py:863-864`), so the one producer this changes moves no row: 0
of 763 undated on the capture with its header cleared. PATH's rows likewise (`path.py:122`).
*Recommendation: `None`; every train already carries its own `observed_at` since 6.1, and
the per-row surfaces do not move.*

**Q7. Should the envelope's keep-last-known branch (`backend/pollers.py:696-708`) be narrowed
to failures, so a headerless success publishes `None` on the envelope and `feed_age_s` reads
unknown?** The measured behaviour on a `null` age is A1.3's: `/healthz` reads it as healthy
(`backend/routes/status.py:302`), `/api/status` publishes it and decides nothing, and the
monitor never reads it, but WARNs on the headerless upstream itself through
`railroad-realtime`. So narrowing moves a headerless LIRR's operator signal from a FAIL built
on an age nobody measured (`production:healthz` on `feed-content-stale`, once the kept clock
passes 90 s) to the WARN that names the cause, and leaves the outage the comment guards
against where it is, provided the branch keeps the last clock exactly when LIRR is in the
poll's failed list: dropping the branch outright, which the producer fix alone might suggest,
also turns "LIRR down, Metro-North up" from `feed-content-stale` into a clean `/healthz`
(measured on a scratch copy). It also matches the subway, whose envelope is `null` when every
group is headerless (A1.3's table); a railroad envelope has one freshness-bearing system, so
its headerless LIRR is that case. The monitor needs no change for this question; the gaps it
does have on a headerless feed are named in A1.4. *Recommendation: yes, narrowed to "LIRR
failed this poll", with a test that pins both arms, since none does today.*

**Q8. Should `positionQualifier`'s `.compact` form be removed from the contract and the code,
moving `positions.test.js`'s pin from three forms to two, or kept for a surface that may yet
want it?** The note above, "For the next amendment, posed by Amendment 1 as its Q8", is the
evidence: its text is left as it was written, and only its heading now names this question. Measured: `frontend/positions.test.js` makes 48
three-form `deepEqual` calls at 39 call sites through its `said()` and `placed()` helpers
(`:82-95`), five of which pin a `.compact` that differs from `.words` (the `placed()` calls
at `:157-162` and `:227`), plus two direct pins (`:976`, `:1033`); removing the form fails 12 tests, all in
that file. `tests/e2e/pins.spec.js` waives "scheduled (no GPS)" in `NOT_RIDER_TEXT`
(`:1134-1146`) with the reason that "deleting a contract form is an amendment to that contract
rather than a stage's tidying" (`:1139-1142`), and P5b fails on a waiver nobody extracts any more
(`:1660-1661`), so yes deletes the waiver in the same change. No means the form stays defined,
pinned and waived, and no surface renders it. *Recommendation: remove; an unread third
spelling is the MR3 N6 hazard waiting.*

**Q9. Should PATH, NJ Transit, the ferry, and the railroads on a TOTAL failure drop a kept row
once its time has passed, as `drop_expired_arrivals` does for a retaining family's partial
failure?** Raised by A1.2. None of the four prunes, so a departed train is served as
`reported` and counts down to "now" for as long as an outage lasts: "1600 as of 4h ago departs
now" on an NJ Transit board at T0+14400 s, PATH's to 3620 s, the ferry's to 4 h, LIRR's to
1500 s on a total failure. The "as of" is honest about age; the countdown beside it is not
about anything that exists, which is the ghost 3.4 removed from the map. Yes means state (ii)
exists on every family and an outage empties its boards into the four states this amendment
defines, through the same prune with its same zero grace; no means those boards stay in state
(i) without end. It does not touch retention: nothing that has not departed is dropped.
*Recommendation: yes.*

**Q10. Should a single-system board (PATH, NJ Transit and the ferry, and a railroad board
whose whole family has never decoded) carry its system's block on every answer, including
the answer to a failed first poll, which today is HTTP 502 with the sanitized upstream
error?** Raised by A1.2, and a reconciliation rather than a new direction: 4.1's table gave
all five arrivals envelopes `+ served_at` and `+ systems`, on 3.5's ground that "they need the
`systems` map so the board can name which contributor is behind", and the build added the
field and serves it null for these two ("No systems block exists for PATH: it is one feed,
and no poller writes one", `backend/routes/path.py:128-129`). Today PATH's and the ferry's boards serve `"systems": None` always,
so `ok` reaches no field and the renderer's substitute block says `ok: true`; and a family
that has never decoded answers 502, whose detail both surfaces print, rendered as "Upstream
PATH bridge feed error: Server error '503 Service Unavailable' for url '<feed url> For more
information check: <feed url>" (A1.2), so a rider reads upstream error text where `SystemFreshness`'s docstring keeps it out of every
envelope ("NO ERROR TEXT LIVES HERE"). The rule against one-entry blocks
(`backend/cache.py:306-310`) is argued for the vehicle envelopes, where "their top-level
fetched_at already means what a per-system block would say"; on a board it does not, because
nothing at the top level says `ok`. Yes means one shape of "never decoded" on every family (a
200 board with a block reading `fetched_at: null`, `ok: false`) and the rider reads 3.2's
`{system} not reporting` instead of an error string; the 503 before any poll stays, since
nothing has been tried, and the vehicle envelopes do not change. *Recommendation: yes.*

**Q11. On a board, should an expected contributor that decoded in this process and is now
failing, with no row on the board, read `{system} not reporting`, rather than `as of {age}
ago`, the age of its last decode?** Raised by the review round, which found the body
deciding it while Q3 deferred to the body. Today the one board that names such a
contributor, A03's world C, reads `as of 3m ago` above `No trains`; but the decode it dates
had trains in it (219's last two were due after it), so the line dates a statement that decode
did not make. Yes widens the shipped word from never decoded (3.1; 3.2's first table; the
status line's pin at `frontend/helpers.test.js:1962-1973`; the feed strip's `Not reporting`,
`frontend/helpers.js:822-827`) to "failing, and nothing of it shown here", and, so that one
state takes one word on every surface (3.2: "defined once for every surface"), the status line
takes it too once the cap has taken a system's vehicles, where it now reads "trains: 1-7+S
group as of 10m ago" over a map that draws nothing of the group (A1.3, measured); inside the
retention window, where the map still draws the vehicles, its `as of {age} ago` stays, because
it dates what is drawn. No keeps `not reporting` for never decoded everywhere: a failed
contributor reads `as of {age} ago` on the board, from 90 s after its last decode, and Q3's slot
question is then about never-decoded contributors alone. Either way the 90 s grace stands, so
one failed poll is not news. *Recommendation: yes, on the board and on the status line past
the cap.*

### A1.6 The order of the build

**One build branch, A03 with A10 riding it**, which is the 2026-10-12 record's Section 4
item 3 in its own words ("A03 as an amendment to the freshness contract first, then the
build, with A10 riding it"), in three steps after a probe. A10's producer change is independent of
everything else here and lands first because it is the smallest; A03's two halves can be
built together and accepted together, because the frontend half depends only on the shape
of `systems` that the backend half serves, and the A03 witness already serves the page boards
in exactly that shape (its section (d)) and goes red on either half alone (its README row:
the backend spec turns the backend checks red with the frontend checks green, and the
frontend spec the reverse). Each step states its acceptance in the auditor's terms, because
those are the sentences the work will be judged against, as 6.x does.

**Step 0, the probe (a prerequisite, not a build step, and only if Q1 is answered yes).**
Keyless fetches of each of the eight subway group feeds over a full service day, the
overnight shuttles included, counting the route ids each carries, so the route-to-group
table's FS, H and SI rows are written from a measurement and the table cites a committed
capture, like 3.3's buses row. **Acceptance:** every one of the live archive's 29 route ids
is placed in exactly one group by a capture, or is stated as unplaced with the capture that
shows it absent.

**Step 1, A10.** The railroad producer records a key for every decoded system under the
membership clause, and the envelope's keep-last-known branch takes the shape Q7's answer
gives it. **Acceptance, in the auditor's terms:** *"Emit explicit null for a successfully
decoded but undated generation; preserve prior timestamps only for actual failures. Test
timestamped → headerless → failed transitions."*
Concretely, on `a10_headerless_lirr_clock.py`'s sequence and under Q6's recommended `None`:
at 1000 the LIRR block reads (true, 1000, `null`) where it reads (true, 1000, 900) today; at 1100, (false, 1000, `null`);
the failure-only control unchanged at (false, 900, 900); arm D's five headerless polls each
publish `null` with no implied age; and the envelope and `feed_age_s` as Q7 rules.

**Step 2, A03's backend half.** Expected contributors (Q1), the never-decoded block (Q2), the
three selectors over the expected set, and, as Q9 and Q10 are answered, the prune and the
single-system block on the other families. **Acceptance, in the auditor's terms:** *"Maintain
station-to-feed coverage independently of displayed predictions; preserve expected failed
contributors after expiry or retention limits."* Concretely, in A03's world: after the T0+200 s
expiry and at the T0+620 s cap, 219's `systems` names 1-7+S with `ok: false`; in world B it
names 1-7+S with `fetched_at: null`; and no served board is identical to fresh empty
service's while a contributor fails.

**Step 3, A03's frontend half, and the operator's code.** `boardFreshness` and `boardSystemLine`
over the expected contributors' `ok`, `fetchedAt` and `feedTimestamp` (Q3, Q4, Q11), one write per
render, and `observations-qualified` in the same four states (Q5). **Acceptance, in the
auditor's terms:** *"Carry availability, unknown clocks and source-content age through the
board renderer. Regression cases must distinguish genuinely fresh empty service from stale,
failed and unknown empty service in both surfaces."* Concretely, each of A03's section (d)
boards A, B and C reads differently from the fresh one on the popup, the panel and the spoken
board, and the fresh one reads exactly as it does today; B (never decoded) and C (failed)
differ in the served field, and share the rider's word if Q11 is answered yes.

**Steps 2 and 3 land together or in that order, never 3 alone.** The frontend half reads
fields only the backend half serves, and the reverse order is safe: a backend that names a
failed contributor in front of today's renderer makes an empty board read `as of {age} ago`
from 90 s after the last decode (A03's world C, the control), never less honest than now.
The one surface Step 2 cannot leave for later is the monitor: a never-decoded block fails both
of its board lines (A1.4), so their four-state reading lands with Step 2.
