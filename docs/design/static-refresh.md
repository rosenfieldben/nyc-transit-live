# Schedules that follow their publishers: static refresh in a running process (F07 and A06 first)

**Status:** DESIGN, for review before anything is built. It is written on
`claude/design-static-refresh`, forked from `main` at `16d1c19` (PR #150's merge, committed
2026-11-28 08:12:41 UTC, 03:12:41 Eastern), and it is documents only: this file, then pointers
to it in `docs/reviews/audit-2026-10-12.md` as the branch's last commit, made after the
republication branch (`claude/njt-republication-2026-11-27`) merges, since that branch edits
the same file. It changes no code, no test, no fixture, no golden and no witness script, and it
deploys nothing. Writing it spent no NJ Transit mint, dispatched no monitor run and re-ran none.
**It decides nothing.** Every choice Ben owns is a numbered question in section 5, each with a
recommendation and the evidence that leads to it, and every proposal the body makes without
asking is listed there by name, so that a decision can reject it by name, as Amendment 1's A1.5
did for the freshness contract. The answers are to be recorded in a Decisions section appended
at the end, in the form of the freshness contract's "Decisions, 2026-09-12"
(`docs/design/freshness-contract.md:1507`), with the body amended in place where an answer
changes it and each question kept as it was posed.

**Written against `16d1c19`.** Every citation is a repo-relative `path:line` at that commit, and
the application code cited is its code; a line at another commit is written
`commit:path:line`. **A "today" or a "currently" below is the world at `16d1c19`** unless a
sentence beside it says otherwise. "The ledger" is `docs/reviews/audit-2026-10-12.md` at
`16d1c19`, cited as `ledger:line`; its Section 1 is the 2026-10-12 report as delivered, so that
report's findings are cited there too. "The 2026-09-05 report" is
`docs/reviews/audit-2026-09-05.md` at `16d1c19`, cited as `audit-2026-09-05.md:line`. The third
incident's record lives only on `claude/njt-republication-2026-11-27`, another session's branch
with no pull request yet. It is cited as **the republication record at `a0ed9f4`**
(`git show a0ed9f4:docs/reviews/audit-2026-10-12.md`, committed 2026-11-28 10:36:40 UTC,
05:36:40 Eastern), its lines as `record:line`. Until that commit it was `a236c11`; `a0ed9f4` adds day one at 20
checked trips and two timeline rows (record:663 and :664), so its line numbers after 662 are
`a236c11`'s plus two. That incident is quoted from the record and was not measured again.

**Times** are UTC, with Eastern in parentheses for human events. Eastern is UTC minus 5 h at
every instant after 2026-11-01 06:00 UTC and UTC minus 4 h before it (2026-07-10 below). A monitor run is named by its number, with its databaseId and `created_at`
where it first matters. A publisher's change known only to lie between two runs is given as
that pair of runs, because the pair is what was measured.

**Every number below is measured, and each says where it came from:** a run's log line or
`created_at` (`gh run list` and `gh run view --log`, read for this design on 2026-11-28), a
deployment record's statuses (`gh api .../deployments/<id>/statuses`, the same day), a line of
a record at a commit, or a command over the code at `16d1c19`. A span between two instants is
arithmetic over the two literals beside it. A memory figure names its instrument
(tracemalloc, footprint or getsizeof) and its conditions (one archive alone or the warmups
together, a cached archive or a cold one), because the same structure measures differently
under each (2.1).

**What it answers.** Item 6 of the ledger's release order, "F06 and A05, F07 and A06, as
Release 2 design work" (ledger:923-929), in the order the ledger's amendment of 2026-11-22 set
(ledger:682-694): F07 and A06, the backend's half, first, with A09's off-the-loop rule in from
the start, and F06, A05, F09 and H2 riding where they touch the same warmups. In the 2026-09-05
report's terms it is Release 2, "continuous operation" (audit-2026-09-05.md:357), whose work is
F06 to F09 and F13, "snapshot refresh and per-provider recovery", and whose completion measure
reads "Cold-start partial failure heals; expired static data refreshes; stopped tasks are
detected."

**The auditors' words, verbatim**, copied from the files at `16d1c19`. The 2026-09-05 report's
headings carry a dash, so each is named here in prose and its body is quoted whole.

F07, "Static schedules do not refresh after successful warmup" (audit-2026-09-05.md:208), its
five paragraphs (:210-218):

> **Evidence:** [startup-only tasks](https://github.com/rosenfieldben/nyc-transit-live/blob/625861759bc6975dfe79323caaccf97f9ead3879/backend/main.py#L323), [successful warmup returns](https://github.com/rosenfieldben/nyc-transit-live/blob/625861759bc6975dfe79323caaccf97f9ead3879/backend/warmups.py#L184), and [NJT load-time age/validity checks](https://github.com/rosenfieldben/nyc-transit-live/blob/625861759bc6975dfe79323caaccf97f9ead3879/backend/njt_static.py#L968).
>
> Cache-age and service-calendar checks run when loaders execute. Successful warmup tasks return permanently. A long-lived process can cross an archive-age threshold or a schedule's validity boundary without reloading, revalidating or attempting a new publication.
>
> **Impact:** route geometry, trip joins, station service and NJT schedules can remain outdated despite healthy realtime HTTP traffic. Frequent deployments may incidentally conceal the problem; they are not a refresh policy.
>
> **Remedy:** periodically construct and atomically publish complete immutable snapshots, retaining the last usable one during failures. Use publication changes and service validity to select refresh times. Report snapshot version, loaded time, valid-through date and next refresh. Let clients detect changed static versions and update their registries safely.
>
> **Acceptance:** simulate a validity boundary and a changed publication in a running service; the new snapshot is adopted without restarting, while a bad replacement leaves the prior usable snapshot intact.

F06, "Partial static startup failures persist until restart" (audit-2026-09-05.md:196), its five
paragraphs (:198-206):

> **Evidence:** [railroad warmup](https://github.com/rosenfieldben/nyc-transit-live/blob/625861759bc6975dfe79323caaccf97f9ead3879/backend/warmups.py#L192), [bus index task](https://github.com/rosenfieldben/nyc-transit-live/blob/625861759bc6975dfe79323caaccf97f9ead3879/backend/bus_static.py#L301), and [startup scheduling](https://github.com/rosenfieldben/nyc-transit-live/blob/625861759bc6975dfe79323caaccf97f9ead3879/backend/main.py#L323).
>
> Railroad warmup retries when both systems fail. If one succeeds, it marks the aggregate `ready` and terminates. Injecting a first-call-only Metro-North failure left `MNR_stops=None`, aggregate status `ready`, and exactly one attempt per system, even after the shortened retry interval elapsed. Bus indexing likewise terminated after one injected failure although the second call was programmed to succeed. Partial bus builds also rely on a later startup for another build.
>
> **Impact:** a temporary startup failure can leave missing railroad stations, arrivals and geometry, or missing bus route geometry, for the entire process lifetime. Working GPS markers can make the partial railroad loss less obvious.
>
> **Remedy:** keep successful components available while independently retrying failed operators/boroughs. Publish per-component readiness and degradation. Coordinate frontend static-data reloading: a loader that stops permanently after the first partial nonempty response will otherwise miss repaired data.
>
> **Acceptance:** one provider fails at boot, recovers without a deployment, and its stations/routes appear in an already-open browser without a manual reload.

A06, graded Medium, "Static schedules never refresh after successful startup" (ledger:261), its
three paragraphs and its code-evidence line (:263-269):

> **Failure.** Static warmups return when ready and are started once. File-age policies are checked during loading, not periodically in a long-running process. New schedules, trip IDs, routes and service-calendar expiry can therefore outlive the data loaded into memory. Frontend static loaders also stop after their first usable result.
>
> **Impact.** New ferry trip IDs can lose route joins, schedule-dependent NJT behavior can retain an old service calendar, and route/station changes require a restart or reload. MTA states that railroad GTFS changes frequently and offers supplemented subway schedules updated hourly, so a startup-only strategy is materially different from current service coverage. This is another documented unresolved design limitation.
>
> **Repair.** Add jittered, per-source refresh with staged validation, generation/version identifiers and atomic snapshot swaps. Keep last-known-good data on failure and ensure browsers discover changed static generations. Coordinate NJT refresh with the authentication budget. Test a process crossing a service-day boundary and a changing upstream publication without a restart.
>
> Code evidence: [backend/main.py:330](https://github.com/rosenfieldben/nyc-transit-live/blob/1e3e73d11cedd1a12cbdb70c69b05901d66ba4b2/backend/main.py#L330) · [backend/warmups.py:193](https://github.com/rosenfieldben/nyc-transit-live/blob/1e3e73d11cedd1a12cbdb70c69b05901d66ba4b2/backend/warmups.py#L193) · [backend/warmups.py:269](https://github.com/rosenfieldben/nyc-transit-live/blob/1e3e73d11cedd1a12cbdb70c69b05901d66ba4b2/backend/warmups.py#L269) · [backend/warmups.py:462](https://github.com/rosenfieldben/nyc-transit-live/blob/1e3e73d11cedd1a12cbdb70c69b05901d66ba4b2/backend/warmups.py#L462)

A05, graded Medium, "Partial startup success prevents failed static systems from recovering"
(ledger:251), its three paragraphs and its code-evidence line (:253-259):

> **Failure.** Railroad warmup returns permanently once any system has usable stops. If LIRR loads and Metro-North fails, partial data is correctly served, but the missing system is never retried. Bus indexing has the same one-build limitation for failed boroughs.
>
> **Evidence.** A fake loader that would recover on its second call was invoked only once: warmup finished, status was ready, and Metro-North stops remained absent. This is an unresolved prior audit finding, not a newly introduced regression.
>
> **Repair.** Keep serving healthy components while scheduling independent retries for failed components. Expose ready/degraded status per system rather than letting a single ready flag imply completeness. Verify boot with one failed system, subsequent upstream recovery and no process restart.
>
> Code evidence: [backend/warmups.py:195](https://github.com/rosenfieldben/nyc-transit-live/blob/1e3e73d11cedd1a12cbdb70c69b05901d66ba4b2/backend/warmups.py#L195) · [backend/warmups.py:220](https://github.com/rosenfieldben/nyc-transit-live/blob/1e3e73d11cedd1a12cbdb70c69b05901d66ba4b2/backend/warmups.py#L220) · [backend/warmups.py:246](https://github.com/rosenfieldben/nyc-transit-live/blob/1e3e73d11cedd1a12cbdb70c69b05901d66ba4b2/backend/warmups.py#L246) · [backend/bus_static.py:302](https://github.com/rosenfieldben/nyc-transit-live/blob/1e3e73d11cedd1a12cbdb70c69b05901d66ba4b2/backend/bus_static.py#L302)

The ledger's amendment of 2026-11-22, whole (ledger:682-694):

> **Amended 2026-11-22: item 6's refresh goes first.** F07 and A06, the backend's half, are
> re-graded High by the second production incident of it (the live list's first row) and move
> to the top of this order, ahead of items 4 and 5; items 1 to 3 are built. Nothing else in
> item 6 moves with them unless the design needs it: A09's off-the-loop rule is in that design
> from the start, as item 6 says, and F06, A05, F09 and H2 ride it where they touch the same
> warmups. Two things the incident adds to its acceptance. **The fix is not a timer alone.**
> NJ Transit republished at least three times in ten days and its realtime feed can move
> before its schedule endpoint does (run 445), so a refresh that finds the schedule unchanged
> must try again soon, and `njt-schedule-behind`'s own count is the signal that says when it
> is wanted. **And the swap has to be whole:** stations, routes, trips and lines are read
> together by every poll, so a refresh that replaced one of them under a decode would be the
> incident over again for a poll. A refresh reuses the live token (item 6), so it costs no
> mint.

One clause of it is measured otherwise in 2.7. "A refresh reuses the live token (item 6), so it
costs no mint" holds while the live token stays good: a refresh attempt through the shipped
chain sent 0 getToken POSTs under the twelve-hour ceiling (`MAX_TOKEN_AGE_S`,
`backend/njt_auth.py:302`), 1 at it, and 2 in one attempt, the most the code allows, since the
download calls `get()` at `backend/njt_auth.py:1432` and once more at `:1455` (measured for this
design on 2026-11-28 against a local fake token server; 2.7 gives every case).

And item 6 itself (ledger:923-929):

> 6. **F06 and A05, F07 and A06, as Release 2 design work** (F07 and A06 first of all since
>    2026-11-22, as amended above), with A09's off-the-loop
>    rule designed in from the start, because a periodic refresh turns a startup stall
>    into a recurring one: whatever runs every few hours must not be able to stop the
>    serving loop for as long as a cold start can. NJ Transit's refresh reuses the
>    live token rather than minting, since a refresh that mints spends the budget item
>    1 exists to protect.

**The three witnesses.** Each is a process holding an NJ Transit schedule NJ Transit had
replaced, the republication record's count ("Three times in production within four weeks",
record:592, where the ledger at `16d1c19` still reads "Twice in production within three weeks", ledger:592), and
each is set out in 1.1:

1. **2026-11-03 to 2026-11-07, stop ids** (and trip ids, from seven digits to one to four):
   positions and boards joined to the wrong stations (1.1.1).
2. **2026-11-12 to 2026-11-22, trip ids**, numbered 1 to N afresh in a new publication: other
   trains' destinations and numbers (1.1.2).
3. **2026-11-27 to 2026-11-28, trip ids again**, with PR #145's rule carrying the rows: each
   refuted train shown its own number (1.1.3).

A possible fourth window, 2026-11-10 to 2026-11-12, is set out in 1.1.4 and posed for the ledger, not counted (Q15).

**Out of scope, and said so.**

* **The age limit on a stalled feed's rows.** The third of the live-list notes the republication
  record adds on 2026-11-28, "a contributor whose content has stopped has no age limit on a
  board" (record:620), is a question the record itself sends to "a later amendment of the
  freshness contract" (the same line). It belongs to that contract: a refresh changes what a
  row is joined against, not how old a served row may be.
* **Multiworker operation.** The 2026-10-12 report says "Keep one worker until ingestion
  leadership, shared snapshots and credential coordination are implemented." (ledger:332), and
  the 2026-09-05 report heads the same point "Treat multiworker operation as a separate design
  change" (audit-2026-09-05.md:334), and the README requires a single uvicorn worker (`README.md:724`). This design is for one process with one worker, as today.
* **A09's ceilings, beyond what a refresh needs.** A09's arms 2 and 3, "no byte, member or
  expanded-size ceiling" (ledger:595), ride item 6 by the ledger's own note (ledger:931-935).
  This design takes up only what a refresh needs: the gates a cold start runs, so that a bad publication cannot replace a good one (3.3), one refresh at a time (3.10) and a memory ceiling (Q6). The rest stays on A09's row.

---

## 1. The problem, with the numbers

A running process never fetches a static archive again. The five static warmups and the bus
route index are started once, as tasks, at startup (`backend/main.py:343-357`), and each warmup
returns once its group is ready: NJ Transit's at `backend/warmups.py:474-475`, after the parse
has filled `app.state.njt_static`, the routes per station, the stations, the route lines, the
trip index, the line names and the stop schedule (`:442-473`). So a process serves the publication it promoted at
its cold start until the next cold start. NJ Transit replaced its publication under a running
process three times in four weeks, and each time the process kept joining the trains of the new
publication against the tables of the old one.

### 1.1 The three incidents

#### 1.1.1 2026-11-03 to 2026-11-07: stop ids

**What riders read.** Production placed NJ Transit trains and built boards against stop ids NJ
Transit had reassigned: Penn Station New York became 105 and Newark Penn Station 107, while 109
became North Elizabeth and 112 Orange (ledger:653). At 2026-11-07 00:18:55 UTC (2026-11-06
19:18:55 Eastern) production's board for stop 112, titled "Newark Penn Station", listed six
trains, all "Morris & Essex Line", which never serves Newark Penn, and its board for 109, "Penn
Station New York", listed Northeast Corridor and North Jersey Coast trains (ledger:653). All 56
trains production served at 00:19 UTC matched the new `trips.txt` on trip_id, route_id and
train number, and its boards showed synthesized headsigns ("7 6359"), "because the new trip ids
are absent from its archive" (ledger:653): the trip ids had moved too, from seven digits to one
to four, with no overlap (ledger:611). None of the 56 was an ADDED trip, since the code of that
day served an ADDED train under the key `njt:<entity.id>` (`ef98328:backend/feeds/njt.py:595-600`,
`:624`), which no `trips.txt` row carries, so every one was a trip the held schedule lacked.

**The window.** The process held the archive it promoted at 2026-11-01 15:14:21 UTC
(10:14:21 Eastern), "before the change" (ledger:653), and no deploy replaced it until
2026-11-07: the three production deployment records between, 7526192539, 7536271414 and
7613800372, each went `in_progress` and then `inactive` 14 s later with no `success`, the shape
of a skipped deploy (their statuses). The publication changed between run 425 (41531218367,
created 2026-11-02 21:45:18 UTC), whose line was
`PASS  njt-static: 12 routes, 172 stops, 109/112, service through 20270501`,
and run 426 (41592974665, created 2026-11-03 06:55:47 UTC).
The realtime feed's move "is dated only by the read at 2026-11-07 00:18:55 UTC, most likely with
the static" (ledger:653). So riders were served wrongly for at least the 3 h 22 min 23 s from
that read to the repair's promotion, and for up to 4 d 5 h 56 min 0 s if the realtime feed
moved after run 425 was created; no record bounds the start earlier.

**What said so.** The monitor's `njt-static` identity check, in every scheduled run from 426
to 433 (8 runs, the last 42279999223, created 2026-11-06 19:29:00 UTC), on one line, first
stamped 2026-11-03 06:56:26 UTC (01:56:26 Eastern) in run 426's log:

```
FAIL  njt-static: stop 109 name is 'NORTH ELIZABETH', expected New York; stop 112 name is 'ORANGE', expected Newark
```

That line reads the upstream publication, not production. Production said nothing about itself:
`ef98328`'s `/api/status` served no NJ Transit count at all
(`git show ef98328:backend/routes/status.py | grep -c njt_feeds` prints 0), it had no
`njt-schedule-behind` code (the same count in `ef98328:backend/models.py`: 0), and its monitor
had no check of production's schedule (runs 425 to 435 print `22 checks total`).

**What ended it.** PR #137's merge (`b27dc6d`, merged 2026-11-07 03:39:32 UTC, 2026-11-06
22:39:32 Eastern), which keyed the identity check on `stop_code` and place and carried
application code, "so its merge deploys and that deploy's cold start is the repair"
(ledger:653). The cold start promoted NJ Transit's archive at 03:41:18 UTC (22:41:18 Eastern)
(ledger:653), 3 d 20 h 44 min 52 s after run 426's red line, through 8 red runs.

#### 1.1.2 2026-11-12 to 2026-11-22: trip ids

**What riders read.** "NJ Transit riders were served other trains' destinations and train
numbers" (ledger:589). The decoder joined a trip update to the schedule by `trip_id` and took
the destination and the number from the join, and NJ Transit numbers its trips 1..N afresh in
each publication (ledger:589). In read-only GETs of production on 2026-11-22 from 23:12 to
23:26 UTC (18:12 to 18:26 Eastern), of 50 distinct trips, 42 joined the held schedule and not
one carried its own number, two carried their own destination, each under a wrong number, and
the other eight missed the schedule and read `5 1724`, the route id and the number. All six
rows on New York Penn's board were trains ending there, served as Port Jervis, Hoboken, Dover
and Hackettstown, and all six on Newark Penn's were wrong (ledger:589). The line, every
station's id, name and place, and the times stayed right (ledger:589).

**The window.** Production promoted its NJ Transit archive at 2026-11-12 06:36:34 UTC (01:36:34
Eastern), the cold start of PR #143's deploy, and held it until 2026-11-22 23:48:52 UTC
(18:48:52 Eastern) (ledger:589). The API's service end moved at least three times after the
promotion, to 20270509 from run 449, 20270515 from run 462 and 20270516 from run 465, and
"Which of them moved the trip ids is not known": after run 448 (43174588356, created
2026-11-12 07:20:56 UTC), and very probably before run 466 (44811710331, created 2026-11-20
20:28:43 UTC), "the first in-service decode clean on the publication of the 22nd, which
production did not hold" (ledger:589). So riders were served wrongly for at most
10 d 17 h 12 min 18 s, from the promotion at 06:36:34, since the ledger's "after run 448"
rests on the API's download and the realtime feed can lead the API (2.3); or for at most
10 d 16 h 27 min 56 s if run 448 counts as a witness, whose decode passed on 8 trip updates
with the feed closed and did not print how many it checked, as run 444's did (their logs;
2.3). They were served wrongly very probably for more than two days, and certainly from the first
reading on record (23:04 UTC, its seconds not recorded) to the Redeploy's promotion, 43 min
53 s to 44 min 52 s.

**What said so.** "**What said so: nothing.**" (ledger:589). `/healthz` read
`{"status":"pass","degraded":[]}`; `/api/status` carried `cross_check_failures`, 41 at 23:04
UTC (18:04 Eastern), "read by Claude, whose status check this came from", and 37 to 40 over 30
polls, and nothing reads it; and all 23 scheduled monitor runs between, 448 to 470, ended with 0
`FAIL`, "their `njt-static` and `njt-realtime` reading the schedule each run downloads, which is
always new" (ledger:589). "Nobody read production's count between 2026-11-09 and 2026-11-22"
(ledger:589).

**What ended it.** "The repair of the day was a redeploy by Ben's hand" (ledger:589):
deployment record 7730268879, PR #143's, "which a redeploy of the same commit reuses", went
`in_progress` at 2026-11-22 23:47:21 UTC (18:47:21 Eastern) and `success` at 23:48:45, and
NJ Transit's archive was promoted at 23:48:52 (ledger:589); the record's statuses carry a second
`success` at 23:48:49, which the ledger does not quote. PR #145 followed (`758c0f6`, merged
2026-11-23 02:28:38 UTC, 2026-11-22 21:28:38 Eastern; NJ Transit promoted 02:29:20 UTC,
ledger:589), which "stops a train being called by another's words and makes the state visible;
it refreshes nothing" (ledger:592).

#### 1.1.3 2026-11-27 to 2026-11-28: trip ids again

**What riders read.** The first time under PR #145's rule: "a refuted train was shown its own
number, and the name the held stop table gives its own last stop, rather than another train's
words" (record:589). At Claude's read at 2026-11-27 20:24:02 UTC (15:24:02 Eastern)
`/api/njt-trains` served 34 trains, 25 with `identity` refuted, 3 unjoined and 6 with no
`identity` key; New York Penn's board (105) carried 6 rows, 5 refuted and 1 with no key, and
Newark Penn's (107) 6 rows, all refuted; and each of nine served numbers looked up was that
trip's `block_id` in the new file's `trips.txt` (record:589, :649).

**The window.** Riders' rows were carried by PR #145's rule for "at least 11 h 47 min 50 s and
at most 34 h 19 min 41 s", from some moment
after run 478's check, whose printed line is stamped 2026-11-26 21:41:09 UTC (16:41:09
Eastern), and no later than
2026-11-27 20:13:00 UTC (15:13:00 Eastern), to the Redeploy's NJ Transit promotion at
2026-11-28 08:00:50 UTC (03:00:50 Eastern) (record:666). The record counts from that printed
line, which follows the read. Counted as 2.3 counts the realtime window, from run 478's step
opening at 2026-11-26 21:40:50.30 UTC (its log), the safe side of its read, to the
promotion's `last_promoted_at` of 08:00:50.670 UTC (`/api/status`, read for this design), the
upper bound is 34 h 20 min 0.37 s. One run narrows it, if it counts as a
witness: run 479 (45967989070, created 2026-11-27 08:44:18 UTC) read production at 0 of 2
checked, and its own decode passed on 6 trip updates with the feed closed (its log). Counted,
the move came after run 479's step opening at 08:44:33.63 UTC, and the upper bound falls to
23 h 16 min 17.04 s, to the promotion's `last_promoted_at` of 08:00:50.670 UTC (`/api/status`,
read for this design). Whether it counts is not measured: the log does not say how many of the
six joined or were checked. The API's archive moved between run 479 and run 480 (46070185338,
created 21:25:13 UTC) (record:589). The public `rail_data.zip` read Last-Modified 2026-11-27
20:33:53 UTC when fetched at 2026-11-28 08:35:10 UTC (record:651); a Last-Modified is the latest
change before the fetch, so whether the public file first changed before or after the realtime
feed moved is not measured.

**What said so.** First `/healthz`, its `njt-schedule-behind` "raised only once the rule has
held for `SCHEDULE_BEHIND_HOLD_S` (600 s, `backend/feeds/njt.py`)", seen at Claude's read at
20:23:00 UTC (15:23:00 Eastern) with `njt_feeds` at joined 59, checked 59 and
`number_mismatches` 45 (record:589, :648); the code is degraded and never gating
(`backend/models.py:1404-1408` lists the three gating codes, and it is not one). Then the
monitor: run 480's `FAIL  production:njt-schedule` at 21:25:44 UTC (16:25:44 Eastern), "at
least 1 h 12 min 44 s after the mismatches began (from 20:13:00 UTC) and 23 h 44 min 35 s after
the last check that saw production in step" (record:666), and run 481's at 2026-11-28 07:58:50
UTC (02:58:50 Eastern). "**What told Ben: nothing automatic.**" (record:589); 1.3 is that step.

**What ended it.** "The repair was a redeploy by Ben's hand again, Railway's Redeploy, not
Restart" (record:589): deployment record 7988955096, `758c0f6`'s, went `in_progress` at
2026-11-28 08:00:16 UTC (03:00:16 Eastern) and `success` at 08:00:51 and again at 08:00:54, and
all six archives were promoted from 08:00:44 to 08:00:57 UTC, NJ Transit's at 08:00:50
(record:589, :657, :658), 10 h 35 min 6 s after run 480's red line. Day one at 20 checked
trips, read at 10:34:26 UTC (05:34:26 Eastern): joined 20, checked 20, and
`number_mismatches`, `no_identity` and `repeated_identity` 0; no body carrying an `identity`
key; 8 trains and 12 board rows right against the new file; and between 08:40:08 and 10:34:25
UTC (03:40:08 to 05:34:25 Eastern) 114 reads 60 to 62 s apart, each with `/healthz` passing,
nothing degraded and 0 mismatches (record:589, :663, :664).

#### 1.1.4 A possible fourth window, 2026-11-10 to 2026-11-12

The same cause may have run a fourth time, between the first two the ledger counts, and nothing
measured it. Production ran `590054a` from that deploy's cold start (record 7639505039,
`success` 2026-11-08 17:48:48 UTC, 12:48:48 Eastern; NJ Transit's archive promoted by 17:48:54,
ledger:655) until `55065d0`'s process took over (7639505039 `inactive` at 2026-11-12 06:36:34
UTC, the instant of `55065d0`'s NJ Transit promotion, ledger:589). `4489202` merged between
them "with no deploy" (ledger:655): its record 7674420159 went `in_progress` at 2026-11-10
05:29:20 UTC and `inactive` at 05:29:27, with no `success`, and no production deployment record
was created after it until `55065d0`'s (the production environment's deployments, `gh api`).
Production's archives still read promoted 2026-11-08 17:48:40 to 17:48:54 UTC at 05:30:40 and at
07:29 UTC on 2026-11-10 (ledger:655). After that read, a restart that writes no deployment
record and reloads the publication production held is excluded by no source. One that loaded
the publication runs 446 to 448 downloaded is very probably excluded through run 447's read,
which lies between its step opening (2026-11-11 19:41:03.1060423 UTC) and its first printed
line (19:41:20.7611465): `production:njt-routes` read 13 routes and 1595 points on runs 445 to
447, and 14 and 1623 on runs 448 to 470, once `55065d0`'s cold start had loaded that publication
or one their `njt-static` line cannot tell from it (2.2; the runs' logs). The backend code
is the same at both (a `git diff` of `590054a` against `55065d0` over `backend/` lists only
`backend/scripts/contract_monitor.py` and two tests), and so is the check (a `git diff` of
`4489202` against `55065d0` over `backend/scripts/contract_monitor.py` is empty; runs 445 to 447 ran
`4489202`, run 448 `55065d0`). Between run 447's read and 2026-11-12 06:36:34 UTC, a restart
that writes no deployment record is excluded by no source.

Run 445 (42889072568, created 2026-11-10 19:49:15 UTC, 14:49:15 Eastern, on `4489202`) printed,
against the schedule it had downloaded in the same 19.1131031 s (2.3; its step opened at 19:49:29.134 and
its first line is stamped 19:49:48.247; the download runs before the realtime read,
`4489202:backend/scripts/contract_monitor.py:4006`, `:4032`):

```
WARN  njt-realtime: 41 entity.id/trip_short_name mismatches (probe: 0 of 745): 0421/6431 on trip 2152, 0423/6437 on trip 2153, 0426/0400 on trip 2069, 0428/0430 on trip 2070, 0877/6621 on trip 1974, 36 more not named
```

Its download printed 16 routes, 229 stops, service through 20270503, as runs 440, 441, 442 and 444 had (run 443's two attempts skipped
NJ Transit, 2.2),
and run 446 (42973821279, created 2026-11-11 06:38:14 UTC) downloaded 17 routes, 228 stops,
service through 20270507, and its realtime passed (the runs' logs). At `4489202` the 41 counts
every identity warning the decoder raised, of its three kinds
(`4489202:backend/scripts/contract_monitor.py:1481`; the kinds at
`4489202:backend/feeds/njt.py:582`, `:588`, `:611`), so the run shows at least 5 number
mismatches and at most 41.

If production's archive and run 445's download were one publication, which their printed fields
allow and do not show (the `njt-static` line prints no trip count and no hash,
`backend/scripts/contract_monitor.py:2484-2493`), production's own decodes met the same
contradiction from the realtime feed's move until 2026-11-12 06:36:34 UTC, or until a restart after
run 447's read, which no source excludes (above), under a decoder that
still took a train's destination and number from the join. The realtime feed moved after run
442's step opening (42712092087, created 2026-11-09 22:27:48 UTC; step opened 22:28:02.835),
whose in-service decode of 108 trip updates raised no identity warning, and before run 445's
first line; the API moved after run 445's step opening and before run 446's first line
(2026-11-11 06:38:51.309 UTC) (the runs' logs). Over those literals:

| Counted from | To 2026-11-12 06:36:34 UTC |
| --- | --- |
| The realtime feed's move, at its latest (run 445's first line, 2026-11-10 19:49:48.247) | at least 34 h 46 min 45.75 s |
| The realtime feed's move, at its earliest (run 442's step opening) | at most 56 h 8 min 31.17 s |
| The same, if run 444 (42775373334, created 2026-11-10 07:27:05 UTC; 7 trip updates, the feed closed, no warning) counts as a witness, from its step opening at 07:27:18.569 | at most 47 h 9 min 15.44 s |
| The API's move (run 445's step opening to run 446's first line) | at least 23 h 57 min 42.69 s, at most 34 h 47 min 4.87 s |

The first three rows start at the realtime feed's move and the last at the API's, and none is a
measured window of harm: what riders read on 2026-11-10 and 2026-11-11 is not measured. What
said so: run 445's `WARN`, and no `FAIL`, since `production:njt-schedule` did not exist (runs
445 to 447 print `22 checks total`); production's one NJ Transit count,
`njt_feeds.cross_check_failures` (`590054a:backend/models.py:863`), served and read by nothing
("Nobody read production's count between 2026-11-09 and 2026-11-22", ledger:589); and neither
run 445's production lines nor run 446's carry an NJ Transit count (their logs). What ended it
was no repair aimed at it: PR #143's merge (`55065d0`, merged 2026-11-12 06:35:01 UTC, 01:35:01
Eastern) deployed, and its cold start loaded the publication that 1.1.2 then held. The republication
record counts three (record:592), where the ledger at `16d1c19` still counts two (ledger:592). **This design poses the fourth for the ledger (Q15) and does not count it.**

### 1.2 The detection chain

Times in the table are UTC; each cell's source is beside it or in 1.1.

| | 2026-11-03: stop ids | 2026-11-12: trip ids | 2026-11-27: trip ids again | 2026-11-10: the possible fourth (1.1.4) |
| --- | --- | --- | --- | --- |
| **Process holding the replaced schedule** | `ef98328`, NJ Transit promoted 2026-11-01 15:14:21 (ledger:653) | `55065d0`, promoted 2026-11-12 06:36:34 (ledger:589) | `758c0f6`, promoted 2026-11-23 02:29:20 (ledger:589) | `590054a`, promoted by 2026-11-08 17:48:54 (ledger:655) |
| **Publisher moved** | Static between run 425 (created 2026-11-02 21:45:18) and run 426 (2026-11-03 06:55:47); realtime dated only by a read at 2026-11-07 00:18:55, "most likely with the static" (ledger:653) | Trip ids after run 448 (created 2026-11-12 07:20:56), very probably before run 466 (2026-11-20 20:28:43); which republication moved them is not known (ledger:589). That dates the API's download: the realtime feed's move is dated after run 448 only if its closed decode of 8 trip updates counts as a witness, so riders' wrong rows began no earlier than the promotion at 06:36:34 (1.1.2) | API between run 479 (created 2026-11-27 08:44:18) and run 480 (21:25:13); trains contradicting the held schedule from 20:13:00 at the latest (record:589, :647) | Realtime between run 442's step opening (2026-11-09 22:28:02.835) and run 445's first line (2026-11-10 19:49:48.247); API between run 445's step opening (19:49:29.134) and run 446's first line (2026-11-11 06:38:51.309) |
| **First signal, and what it read** | Run 426's `njt-static` identity `FAIL`, 2026-11-03 06:56:26 (01:56:26 Eastern): the upstream publication, not production; production served no NJ Transit count | A count that nothing read, `cross_check_failures` on `/api/status`; first reading on record 41 at 2026-11-22 23:04 (18:04 Eastern); `/healthz` pass (ledger:589) | `/healthz` `njt-schedule-behind` after its 600 s hold, seen at Claude's read at 20:23:00 (15:23:00 Eastern): joined 59, checked 59, mismatches 45 (record:589, :648) | Run 445's `njt-realtime` `WARN`, 41, against the run's own download; production's `cross_check_failures`, read by nothing (ledger:589) |
| **First red run** | Run 426 (41592974665), `1 FAIL, 2 WARN, 22 checks total`; runs 426 to 433 all red on that line (433: 42279999223, created 2026-11-06 19:29:00) | None: runs 448 to 470, 23 runs, all 0 `FAIL` (ledger:589) | Run 480 (46070185338, created 2026-11-27 21:25:13), its `FAIL` at 21:25:44 (16:25:44 Eastern), 44 of 58; run 481 (46123612408, created 2026-11-28 07:58:21), 7 of 8 (record:680, :691) | None: runs 445 to 447 end 0 `FAIL` on 22 checks |
| **When Ben knew, and from what** | Not on the record. The first repository record of the event is commit `e053bd9`, 2026-11-04 02:31:56 (2026-11-03 21:31:56 Eastern), 19 h 35 min 30 s after run 426's line; whether GitHub's email for run 426 reached Ben is not recorded | Not on the record: the count of 23:04 was "read by Claude, whose status check this came from" (ledger:589), and the minute Ben learned is not given | From Claude's messages, then the failing lines in the Actions tab, which he read himself; no GitHub email by about 08:30 (03:30 Eastern) on 2026-11-28 (record:589, :619), nor by 12:43:47 (07:43:47 Eastern) (1.3) | Not on the record: nobody read production's count from 2026-11-09 to 2026-11-22 (ledger:589) |
| **Repaired by** | PR #137's merge deploy (`b27dc6d`, merged 2026-11-07 03:39:32, 2026-11-06 22:39:32 Eastern), NJ Transit promoted 03:41:18 (ledger:653): 3 d 20 h 44 min 52 s and 8 red runs after the first red line | Ben's hand Redeploy, record 7730268879 `in_progress` 2026-11-22 23:47:21 (18:47:21 Eastern), NJ Transit promoted 23:48:52 (ledger:589) | Ben's hand Railway Redeploy, record 7988955096 `in_progress` 2026-11-28 08:00:16 (03:00:16 Eastern), NJ Transit promoted 08:00:50 (record:589, :657, :658): 10 h 35 min 6 s after run 480's line | Nothing aimed at it: PR #143's merge deploy (`55065d0`), NJ Transit promoted 2026-11-12 06:36:34 (ledger:589) |

**What could see production, at each.** At `ef98328` nothing could: the NJ Transit count was
written to `app.state.njt_feed_health` (`ef98328:backend/pollers.py:986-994`) and not served,
and there was no `njt-schedule-behind` and no `production:njt-schedule` (`grep -c` for
`njt_feeds`, `njt-schedule-behind` and `production:njt-schedule` over that commit's
`backend/routes/status.py`, `backend/models.py` and `backend/scripts/contract_monitor.py`
prints 0 each). From `590054a` to
`55065d0`, `/api/status` served one count, `njt_feeds.cross_check_failures`
(`590054a:backend/models.py:863`), all three warning kinds together, and nothing read it on a
schedule; `njt-schedule-behind` and `production:njt-schedule` still counted 0 at `55065d0`.
From `758c0f6` (and at `16d1c19`), `njt_feeds` serves five counts (`backend/models.py:884-902`),
a row that disagrees or does not join is marked `identity: refuted` or `unjoined`
(`backend/models.py:650-658`), `/healthz` raises `njt-schedule-behind`
(`backend/routes/status.py:826`, `:440-441`; the code at `backend/models.py:1356`), and the monitor's 23rd check, `production:njt-schedule`, sets
production's counts beside its own decode (runs 471 onward print `23 checks total`). **What
none of them sees is the MISS**, 2026-11-03's shape, in the code's own words
(`backend/models.py:1348-1351`):

```python
# WHAT IT CANNOT SEE is a schedule behind by misses alone. A trip id the schedule does
# not have is no mismatch, so a publication whose ids overlap the held one nowhere
# (2026-11-03's: seven digits before, one to four after) joins nothing and raises
# nothing here. The 2026-10-12 record's live list carries that as its own row.
```

**When a monitor run can FAIL.** `_check_production_njt_schedule`
(`backend/scripts/contract_monitor.py:3520-3652`) FAILs only when all six of these hold, tested
in this order; each that does not hold ends the check at the PASS or WARN named beside it:

1. Production serves NJ Transit, with two readable verdict counts: `njt_static` is not
   `not-configured` (else PASS, `:3570-3571`), and `njt_feeds` is served with `checked` and
   `number_mismatches`, not both null, both counts, and the mismatches no more than the checked
   (else WARN, `:3572-3585`).
2. Production's decode did not join 20 or more trips (`NJT_MIN_TRIPS_FOR_RATIO`, `:653`) while
   able to check fewer than half of them (else WARN, `:3595-3600`).
3. Production's counts pass the app's own rule, `schedule_behind`: at least 5 mismatches
   (`SCHEDULE_BEHIND_MIN_MISMATCHES`, `backend/feeds/njt.py:599`) and at least one in four of the
   checked (`SCHEDULE_BEHIND_ONE_IN`, `:600`; the rule at `:628-636`, tested at
   `contract_monitor.py:3601`). Since the mismatches cannot exceed the checked (`:3584`), this
   needs production's last decode to have checked at least five trips. Else PASS, in step or
   "too few to judge", or WARN (`:3601-3613`, below).
4. The run made its own decode against the schedule it downloaded, and that decode checked at
   least 5 trips and at least half of what it joined (`can_judge_schedule` and
   `mostly_checked`, `backend/feeds/njt.py:609-620`); else WARN, "this run has nothing to set
   beside it" (`contract_monitor.py:3614-3628`).
5. The run's own decode agrees with its own schedule, fewer than one in four of its checked
   trips contradicting it; else WARN, run 445's shape, because "a redeploy would load one that
   disagrees too" (`:3633-3639`).
6. The app's gate did not refuse the publication the run downloaded; else WARN, "wait for one
   it accepts" (`:3640-3647`).

The check has "NO HOLD OF ITS OWN" (`:3559-3561`): one reading decides, and what it reads is
production's last decode at the run's minute, from the `/api/status` the run fetched. Runs 480
and 481 met all six, and printed, verbatim (record:680, :691):

```
FAIL  production:njt-schedule: production's schedule is behind NJ Transit's publication: redeploy (44 of 58 checked trips contradict the schedule's train number (58 joined, 0 with no identity, 0 repeated); 0 of 61 against the schedule this run downloaded)
FAIL  production:njt-schedule: production's schedule is behind NJ Transit's publication: redeploy (7 of 8 checked trips contradict the schedule's train number (8 joined, 0 with no identity, 0 repeated); 0 of 8 against the schedule this run downloaded)
```

**`can_judge_schedule`, and the overnight run.** The floor in condition 3 is one function
(`backend/feeds/njt.py:609-613`):

```python
def can_judge_schedule(checked: int) -> bool:
    """Whether a decode checked enough trips to say anything about the schedule, for or
    against. Fewer than the mismatch floor could never reach it, so a feed of four is not
    in step: it is four trains, whatever they say."""
    return checked >= SCHEDULE_BEHIND_MIN_MISMATCHES
```

Under it, production's counts PASS "too few to judge" (`contract_monitor.py:3613`), or WARN when
the run's own decode checked 20 or more in the same minutes (`:3606-3612`), the arm whose
docstring names "a schedule without the trips, the 2026-11-03 shape" (`:3554-3555`); that WARN
has not been printed by any of runs 471 to 481 (their logs). Run 479, the overnight run before
the alarm, is the case, with its own download still the publication production held
(record:646), verbatim (record:672):

```
PASS  production:njt-schedule: 0 of 2 checked trips contradict the schedule's train number (2 joined, 0 with no identity, 0 repeated): too few to judge
```

The schedule is `cron: "17 */6 * * *"` (`.github/workflows/contract-monitor.yml:22`), but GitHub
created 58 scheduled runs from run 422 (41334042000, created 2026-11-01 17:56:53 UTC) to run 481
(2026-11-28 07:58:21 UTC): 2.182 a day counted by runs over that span, or 2.144 counted by its
57 gaps, which ran from 4 h 11 min 1 s to 16 h 9 min 49 s, median 11 h 5 min 56 s; 27 of the 58
were created between 06:00 and 10:59 UTC (01:00 to 05:59 Eastern), 29 between 17:00 and 23:59
UTC and 2 at 13 UTC (`gh run list`). In effect there is one overnight run and one evening run a
day, and the overnight one is often too thin: production's checked trips were 2, 40, 6, 0, 2 and
8 on the overnight runs 471, 473, 475, 477, 479 and 481, and 100, 103, 95, 43 and 58 on the
evening runs 472, 474, 476, 478 and 480 (their logs; record:671, :672, :680, :691). Run 481
judged on 8, three above the floor, and its own decode checked 8 too. The `/healthz` code has
no such limit through the night: a decode that cannot judge "returns the run as it found it, so
neither ends one nor lengthens one" (`backend/feeds/njt.py:651-658`), and run 481's
`production:healthz` still carried `njt-schedule-behind` (record:692). But "nothing reads
`/healthz` on a schedule except the monitor" (record:666).

### 1.3 The alarm's last step

"A scheduled monitor run that goes red reaches Ben only by GitHub's notification email, and its
delay has been hours" (record:619). Run 480 ended at 2026-11-27 21:25:47 UTC (16:25:47 Eastern)
and run 481 at 2026-11-28 07:58:53 UTC (02:58:53 Eastern) (record:619). As of about 08:30 UTC on
2026-11-28 (03:30 Eastern) no email had arrived for either, and Ben learned of both from
Claude's messages (record:589, :619, :661). The session writing this design asked him again at
2026-11-28 09:38:20 UTC (04:38:20 Eastern). His answer, recorded at 12:43:47 UTC (07:43:47
Eastern): **neither email has arrived.** So as of that answer the lag is at least 15 h 18 min
0 s for run 480 and at least 4 h 44 min 54 s for run 481. It is unknown and unbounded above. If
an email arrives before this design's pull request merges, its lag goes in this paragraph.

**What is on record about where the notice goes.** The runs API names `rosenfieldben` as actor
and triggering actor of runs 479, 480 and 481 (record:589, :619; run 481's read again for this
design on 2026-11-28, `gh api .../actions/runs/46123612408`). GitHub's documentation, read for
this design the same day, says: "Notifications for scheduled workflows are sent to the user who
last modified the cron syntax in the workflow file." The cron line
(`.github/workflows/contract-monitor.yml:22`) was last changed by `ea24561`
(`git blame -L 22,22 16d1c19 .github/workflows/contract-monitor.yml`), authored and committed as
`Claude <noreply@anthropic.com>` at 2026-07-10 18:23:30 UTC (14:23:30 Eastern), which GitHub's
commit API attributes to the login `claude` as author and committer (read for this design); it
reached `main` through PR #56, opened by `rosenfieldben` and merged 2026-07-10 20:36:41 UTC
(16:36:41 Eastern). The record paraphrases the documentation as "the user who created the
workflow or last changed its cron line" and concludes "the notice is addressed to Ben"
(record:589). **Which account GitHub treats as the user who last modified the cron syntax is not
measured**, and Ben adds that GitHub's failure emails sometimes reach his inbox long after the
run (record:589, :619). What would measure the lag needs no mint and no dispatch: each email's
arrival, with its Date and Received headers, if one comes, and the Actions entry of Ben's
GitHub notification settings.

So on 2026-11-27 the alarm worked up to its last step and no further: production's own code was
up by 20:23:00 UTC at the latest, the monitor was red at 21:25:44, and what told Ben was Claude.
The 2026-10-12 report had said as much of the monitor's cadence: "The six-hour upstream contract
monitor is valuable for drift detection but is too coarse to be the only availability signal."
(ledger:328). How Ben is told is Q5.

### 1.4 Why a timer alone is not the fix

The ledger's amendment says "**The fix is not a timer alone.**" (ledger:687), and the runs say
why. Over runs 400 (40189913375, created 2026-10-25 11:41:24 UTC) to 481, 33 d 20 h 16 min 57 s,
NJ Transit's API archive, as `njt-static` prints it, changed visibly six times on dates a pair of runs brackets, with a seventh difference dated only to runs 425 to 434, and no two visible changes from 2026-11-10 on came less than 24.09 h apart (24 h 5 min 29.17 s from run 462's first printed line, 2026-11-18 20:39:23.01 UTC, to run 464's step opening, 2026-11-19 20:44:52.18 UTC; 24 h 5 min 55 s from creation to creation; the runs' logs, 2.2). Those are changes the line can see, not publications: the line prints routes, stops, the NY and NP rows and the last service day
(`backend/scripts/contract_monitor.py:2484-2493`) and never a trip id, and the trip ids are what
moved in the second and third incidents. And the instant a timer would be chasing is not the
publication's. Run 445's realtime feed contradicted a schedule the same run had downloaded
seconds before, at least 5 and at most 41 mismatches, and run 446's download agreed with the trains: the longest lead of the realtime feed over the API that the logs allow is under 32 h 10 min 48.48 s, and a lead of zero is not excluded (2.3). On 2026-11-27 no run landed between the two moves, and the bounds allow either to have gone first (2.3). So a refresh that runs because
the trains contradict the schedule can find the publication unchanged, and has to try again
soon; a timer alone does not know that it should, and a timer slow enough to be cheap leaves
the incident running until it fires. 2.2 gives each publisher's cadence and the cost of a check
that finds nothing, and 2.3 gives run 445's shape in full.

---

## 2. What the publishers and the process give us, measured

This section is the ground the design stands on, as section 2 of the freshness contract was
for that one: what a refresh costs the process, how often each publisher changes what it
serves and what we can see of it, and what run 445's shape looks like in the logs, the shape
the ledger cites when it says a refresh that finds the schedule unchanged must try again soon
(ledger:688-691). Section 3 builds on it, and section 5's
questions cite it.

**Where the numbers come from.** Three kinds of source, and each figure says which. *This
machine:* an Apple M2 with 8 CPUs and 8,589,934,592 bytes of memory (`sysctl hw.memsize`),
macOS 27.2.1, CPython 3.12.13, measured for this design on 2026-11-28 (each reading's UTC
time is given with it) by harnesses that import the application's own loaders, gates and
warmups at `16d1c19`, a fresh process per reading, with the NJ Transit and bus credentials
empty and every URL seam pointed at a local port (closed, or a fake server where a
measurement needed one), so that no measured process reached an upstream or minted; a second
harness, written independently, re-derived the load-bearing figures, and where the two differ
both are given. The machine is shared with other sessions (one-minute load average 1.88 to
4.59 at the start of the timing runs, `sysctl vm.loadavg`), and production runs Python 3.12
under Nixpacks
(`nixpacks.toml:6`) on Linux and on Railway's network, so no time or memory figure from this
machine is production's. *Records:* the contract monitor's run logs (`gh run view
<databaseId> --log`), GitHub's deployment statuses for the production environment (`gh api
repos/rosenfieldben/nyc-transit-live/deployments/<id>/statuses`), the ledger
(`docs/reviews/audit-2026-10-12.md` at `16d1c19`) and the republication record at
`a0ed9f4`. *The wire:* read-only HEAD, GET and conditional GET requests to the public static
URLs, and read-only GETs of production, each stamped in UTC. No NJ Transit token was minted
or used, no monitor run was dispatched or re-run, and nothing was deployed. **The harnesses
are not committed**, as Amendment 1's drivers were not: each table states its method closely
enough to rebuild it, and the scripts are kept beside the reports they produced, on the
operator's machine, in `~/claude-scratch-archive/2026-11-28-static-refresh-design/` (a folder
per measurement, so a script named below by its file name alone, such as `miss_share.py`, is
there). MB is 1,000,000 bytes.

**Four instruments measure memory here, and they disagree, so every memory figure names its
instrument.** `tracemalloc` counts the interpreter's own allocations, in a process of its own
because it inflates both time and resident size. *Footprint* is macOS's physical footprint
(`proc_pid_rusage`, `ri_phys_footprint`). *Resident* is the resident set (`proc_pid_rusage`,
checked against `ps -o rss=`). *getsizeof* is a recursive `sys.getsizeof` walk of the
objects a field holds, each object counted once. All four are macOS's; none is Linux's
resident memory, and whether `tracemalloc`'s counts match the same load on Linux is not
measured.

### 2.1 The cost of a refresh, per archive

A refresh built on today's loaders pays, per archive, what a cold start pays per archive: a
transfer, the gates, the parse and the builders. Nothing below includes NJ Transit's token
POST, which was not attempted; NJ Transit's costs are measured on two stand-ins for the
archive production holds, described after the table.

**One archive, alone in a fresh process.** Medians of three fresh processes per archive (and
one more under `tracemalloc`), each over its own fresh data directory with the archive copied
to its cache path, through the calls each warmup makes (`backend/warmups.py`) preceded by the
gates `staged_fetch` runs on a new download (`backend/static_shared.py:645`, `:649`). "Gate"
is the always-gate plus the decorative gate where the loader has one; "load" is everything
after the gates. The downloads were three tries per archive with curl from this machine,
2026-11-28 09:42:41 to 09:43:51 UTC, and every try of an archive returned the same sha256.

| Archive | Bytes on the wire | Download, median of three tries, s | Gate, ms | Load, ms | Inline on the serving loop today, ms | In a worker thread today, ms | `tracemalloc` peak, gate and load, MB | `tracemalloc` retained after gc, bytes | Footprint added, holding the result after gc, MB |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Subway | 5,606,305 | 3.041 | 821.3 | 883.1 | 7.6 | 1,696.5 | 39.7 | 3,766,401 | 23.4 |
| LIRR | 1,692,161 | 0.809 | 236.1 | 241.3 | 241.3 | 236.1 | 27.3 | 17,459,689 | 31.5 |
| Metro-North | 3,979,272 | 1.720 | 740.6 | 677.3 | 677.3 | 740.6 | 46.9 | 45,558,075 | 54.9 |
| PATH | 1,214,919 | 0.534 | 371.2 | 391.8 | 391.8 | 371.2 | 31.9 | 22,879,434 | 32.5 |
| NYC Ferry | 31,699 | 0.600 | 6.1 | 5.8 | 5.8 | 6.1 | 0.7 | 599,558 | 0.9 |
| NJ Transit, public stand-in | 3,295,223 | 1.038 | 1,020.7 | 2,735.5 | 2,735.5 | 1,020.7 | 99.5 | 88,470,849 | 116.0 |
| NJ Transit, rail-only stand-in | 2,499,303 (constructed here, never on a wire) | none | 797.1 | 2,313.6 | 2,313.6 | 797.1 | 83.0 | 62,196,276 | 95.9 |
| Bus, six boroughs | 54,633,368 | 12.667 (the six medians summed) | no gate exists | 1,386.5 | 0.0 | 1,386.5 | 19.6 | 86,563 | 14.0 |
| AirTrain | 17,696 (a committed file, `data/airtrain_jfk.json`) | none | none | 0.1 | 0.1, once, in the lifespan | 0.0 | 0.1 | 45,704 | 0.1 |

The second harness's medians: gates 819.5 (subway), 237.6 (LIRR), 733.0 (Metro-North), 360.3
(PATH) and 999.6 ms (NJ Transit public), within 3.0% of the table; loads within 4.0%;
`tracemalloc` retained within 0.2% for every archive but the ferry (595,548 bytes against 599,558, 0.7%) and the bus (90,648 bytes against 86,563, 4.7%); and its `tracemalloc` peaks, in bytes, NJ Transit public 99,531,820, Metro-North 47,030,357, the subway 39,954,958, PATH 31,903,859, LIRR 27,340,207 and the bus 19,627,347. The footprint column is macOS's physical footprint after the load, less before it, in the first harness's plain runs. The eleven public archives the app downloads total 67,157,724 bytes on the wire, of
which the six bus archives are 54,633,368 (arithmetic over the table). The download column is
this machine's network, not Railway's, and production's own timings per archive are on no
record read here (the cold-start baseline below is the nearest).

**Where each step runs today** (code at `16d1c19`). The subway's two heavy loaders run in
worker threads (`backend/warmups.py:180`, `:186`). The railroads, PATH and the ferry parse on
the serving loop (`backend/railroad_static.py:546`, `backend/path_static.py:535`,
`backend/ferry_static.py:450`), and so do their builders. NJ Transit's parse
(`backend/njt_static.py:1301`) and all six of its builders (`backend/warmups.py:448` to
`:471`) run on the loop; the largest, `build_njt_route_shapes`, measured medians of 1,715.7,
1,721.1, 1,735.0 and 1,763.0 ms on the public stand-in in four separate measurements on this
machine, because it simplifies every shape in the table, wanted by a trip or not
(`backend/njt_static.py:1207-1210`). Every gate runs in a thread
(`backend/static_shared.py:645`, `:649` on a download; `:403`, `:381` on the cached check).
A download's chunk writes and its rename run on the loop (`backend/static_shared.py:542`,
`:683`), and NJ Transit's whole body is written on the loop
after the awaited POST (`backend/njt_static.py:773-774`). The bus index is built entirely in
a thread (`backend/bus_static.py:300`). A load that downloads over a usable cache pays the
gates twice in one attempt, once on the cache and once on the stage: for the subway that is
821.3 ms twice (`backend/static_data.py:395-403`, then `backend/static_shared.py:645`,
`:649`).

**The bus, and AirTrain.** The bus index reads two members per borough, `trips.txt` and
`shapes.txt` (`backend/bus_static.py:124`, `:145`), 41,750,793 expanded bytes of the six
archives' 530,729,611, and never opens `stop_times.txt` (487,825,465 of them). It has no
publication gate, and its freshness is its manifest's mtime alone
(`backend/bus_static.py:273`). One build wrote 345 route files of 5,462,783 bytes and a
2,260-byte `_manifest.json`, and holds 86,563 bytes in memory (`tracemalloc`): its geometry
lives on disk. AirTrain downloads nothing.

**NJ Transit's two stand-ins, and what they are not.** The app downloads NJ Transit's archive
from the token API (`backend/njt_static.py:144-146`), and that archive was not fetched. The
*public stand-in* is NJ Transit's public `rail_data.zip` (Last-Modified 2026-11-27 20:33:53
UTC, sha256 `11ba8a68e333eafa033663924cac1df1fa6a33b4750e01dfe684f0ff1a5a9869`), which the
app never fetches (2.2). The *rail-only stand-in* is the public file with the trips of its
three `route_type` 0 routes removed (1,914 of its 3,301 trips) and the `stop_times.txt` and
`shapes.txt` rows only those trips reference removed with them. Its route lines equal
production's `/api/njt-routes` value for value and its 164 served stops equal production's
`/api/njt-stops` (production read 2026-11-28 10:00:17 UTC; reproduced at 10:37:58 UTC and at
10:17:26 UTC by two other constructions of the same projection). That production's archive
lacks those trips is an inference from those served counts. **The real archive may be larger
than either stand-in.** The latest recorded size of a token pull is 4,369,879 bytes,
for the archive dated 2026-11-07 00:11 UTC (ledger:653; the code's
note of 2026-08-05 gives "~3.3 MB zipped / 11.1 MB unzipped", `backend/njt_static.py:139-140`),
which by arithmetic over the literals is 1.326 times the public stand-in's 3,295,223 bytes and
1.748 times the rail-only stand-in's 2,499,303; and the API's archive carried 8,697
`calendar_dates` rows on 2026-08-05 (`backend/njt_static.py:18-19`) where both stand-ins carry
1,617. So the NJ Transit rows above may understate what a refresh of the real archive costs.
By how much is not measured.

**One generation's memory, by instrument.** The same loads, in bytes, measured three ways:

| Archive | `tracemalloc`, whole load, retained after gc | getsizeof, every field the warmup sets, one walk | getsizeof, another walk |
| --- | --- | --- | --- |
| Subway | 3,766,401 | 3,720,366 | 3,721,381 (the five structures walked one by one and summed) |
| LIRR | 17,459,689 | 17,415,012 | 17,398,801 (`railroad_static["LIRR"]` alone) |
| Metro-North | 45,558,075 | 45,513,487 | 45,500,484 (`railroad_static["MNR"]` alone) |
| PATH | 22,879,434 | 22,834,355 | 22,828,178 (`path_static` alone) |
| NYC Ferry | 599,558 | 555,707 | 548,826 (`ferry_static` alone) |
| NJ Transit, public stand-in | 88,470,849 | 88,001,553 | not walked |
| NJ Transit, rail-only stand-in | 62,196,276 (the constructed zip); 62,339,219 (a projection, re-parsed); 62,082,558 (a projection, read as the bytes freed when its fields are deleted) | 61,940,922 | not walked |

The three rail-only `tracemalloc` figures come from three constructions of the same projection
and differ by up to 256,661 bytes (arithmetic over the literals).

**Two generations alive in one process.** One fresh process loaded every archive once, in
the order subway, LIRR, Metro-North, PATH, ferry, NJ Transit (the public stand-in), bus,
AirTrain, gates included, one after another rather than concurrently as a cold start runs
them; then, holding that first generation, it built a second complete one from the same
cached files (the bus into a second directory), with `gc.collect()` before every reading.
Two plain runs and one `tracemalloc` run, then the second harness's own plain and
`tracemalloc` runs:

| Reading, bytes | Footprint, run 1 | Footprint, run 2 | Footprint, second harness | `tracemalloc` | `tracemalloc`, second harness |
| --- | --- | --- | --- | --- | --- |
| Before any load | 28,312,008 | 28,312,008 | not reported | 0 | not reported |
| After generation 1 | 254,182,000 | 254,394,992 | not reported (generation 1 added 228,294,824) | 178,624,994 | 178,595,700 |
| After generation 2 | 456,704,768 | 456,590,080 | 456,917,808 | 357,120,853 | 357,066,038 |
| **Added by generation 2** | **202,522,768** | **202,195,088** | **201,212,072** | **178,495,859** | **178,470,338** |

Resident memory added by generation 2 read 201,392,128 and 182,681,600 bytes in the two plain
runs (`proc_pid_rusage`) and 187,219,968 in the second harness's (`ps -o rss=`); the peak
resident size at the end (`ru_maxrss`) was 466,862,080 and 425,394,176. Footprint
deltas repeat between the two plain runs, within 0.4 MB per archive (`m1_measure.py generations
plain`, twice); resident deltas do not, since resident size fell on several loads in run 2
while footprint did not, which on macOS is consistent with pages being compressed or purged
(not investigated). Neither repeats between generations, because memory freed after one load's
transient peak is reused by the next; `tracemalloc`'s per-archive deltas do, within 1.2%
between the generations for every archive but the bus. Substituting the rail-only
stand-in for the public one would lower the NJ Transit share by the difference of the two
retained sizes, 26,274,573 bytes (`tracemalloc`, arithmetic over the table above); that
substitution was not run in a two-generation process.

**Structures held but never read.** Four of the fields the warmups set are read by no
application code. A `git grep` at `16d1c19` for each name, outside `backend/tests/` and
`backend/scripts/`, finds only the initial value in the lifespan, the warmup's assignment,
the module re-export list (`backend/main.py:487-490`), docstrings, and
`backend/routes/status.py:176`, `:183`, `:195`, which read the `*_static_status` fields, not
these:

| Field | Set at | Read by | Size, with its instrument |
| --- | --- | --- | --- |
| `app.state.njt_static`, the whole parse, `shapes` included | `backend/warmups.py:442` (initially `backend/main.py:263`) | tests only (`backend/tests/test_api.py:5609`, `:5852`) | public stand-in, getsizeof: `shapes` 59,482,559, `stop_times` 13,435,859. Rail-only: `shapes` 50,427,455 (getsizeof), 50,176,714 (`tracemalloc`, its parse), 50,236,023 (`tracemalloc`, freed when deleted) |
| `app.state.njt_stop_schedule` | `backend/warmups.py:471` (initially `backend/main.py:273`) | tests only (`backend/tests/test_api.py:5615`; set, not read, at `:5841`) | public stand-in: 12,393,046 (getsizeof), 12,393,072 (`tracemalloc`). Rail-only, the shape production serves: 4,911,854 (getsizeof), 4,911,728 (`tracemalloc`, freed when deleted) |
| `app.state.railroad_static` | `backend/warmups.py:249` (initially `backend/main.py:223`) | nothing | 62,898,354 (getsizeof, walked as one dict), of which 56,535,728 is referenced by nothing else: 6,362,626 is shared with `railroad_stops` and with the route lines' point lists |
| `app.state.path_static` | `backend/warmups.py:308` (initially `backend/main.py:232`) | nothing | 22,828,178 (getsizeof), of which 22,651,175 is referenced by nothing else |

`app.state.ferry_static` is the one whole parse that is read after its warmup
(`backend/pollers.py:993`).

**The memory limit: unknown, and a reading is pending.** Nothing in the repository states a
plan or a limit. `railway.json:1-23` sets the builder, watch patterns, start command,
`healthcheckPath` `/healthz`, `healthcheckTimeout` 300, `restartPolicyType` `ON_FAILURE` and
`restartPolicyMaxRetries` 10, and no memory, replica or plan field; `nixpacks.toml` names the
Python provider and pins its version (`nixpacks.toml:3`, `:6`), and nothing else. Asked for
the limit on 2026-11-28, Ben chose to log the Railway CLI in so that it can be read here; as
of 12:50 UTC (07:50 Eastern) the CLI still answered Unauthorized. The same reading would give
production's own resident memory while it holds one generation: the service's memory series
for deployment 7988955096 since 2026-11-28 08:00:16 UTC (03:00:16 Eastern). Until both are read, the two-generation figures
above have no limit to be set against, and this paragraph is where the reading goes.

**What the serving loop waits through.** A 1 ms heartbeat task beside each warmup, the gap
between ticks recorded, over a cached archive so the gates ran as the cached check in threads
and nothing was downloaded. NJ Transit cannot enter its real warmup unconfigured
(`backend/warmups.py:405`), so it ran as a mirror of the warmup's calls, except in one
re-derivation that ran the real `load_njt_static` over a cached public stand-in. **"Alone" is
one warmup in its process; "together" is three warmups at once; neither is a cold start**,
which downloads, gates and parses with all five warmups and the bus build started at once
(`backend/main.py:343-357`).

| Warmup | Longest stall alone, ms (three runs; median) | Second harness, alone, ms | Together, ms (railroad, PATH and ferry warmups at once, three runs) |
| --- | --- | --- | --- |
| Subway, real warmup | 16.3, 18.3, 16.3; 16.3 | 17.8 | not run together |
| Railroad, both systems, real warmup | 716.1, 728.1, 668.1; 716.1 (each run also had a second stall over 100 ms: 455.3, 458.3, 448.8) | 727.3, 732.8, 720.7 (second 457.8 to 473.4) | the two parses as two stalls, 454.1, 470.7, 483.9 and 654.7, 670.3, 669.2; then the derives and assignments, 40.1, 39.5, 39.8 |
| PATH, real warmup | 377.4, 377.0, 384.7; 377.4 | 390.0 | 525.8, 594.5, 689.3 |
| Ferry, real warmup | 6.6, 6.6, 7.3; 6.6 | not re-run | 27.4, 26.6, 25.3 |
| NJ Transit, public stand-in | 2,683.3, 2,685.4, 2,683.9; 2,683.9 (the mirror) | 2,698.4, 2,727.4, 2,703.4 (a mirror); 2,813.1 (the real loader, whose whole warm took 3,881.1 ms) | not run together |
| NJ Transit, rail-only stand-in | 2,287.8, 2,322.8, 2,300.7; 2,300.7 (the mirror) | not re-run | not run together |
| Bus processing, in its thread | 13.5, 14.9, 14.9; 14.9 (a mirror) | not re-run | not run together |

Work in a thread still shares the interpreter with the loop: during the subway's warmup the
loop ticked 880 to 899 times in 1.781 to 1.789 s against a 1 ms sleep. NJ Transit's
threaded gates, timed alone the same way, left gaps of 36.0, 45.3 and 42.1 ms
(`validate_njt_geometry`, 871.1 to 879.6 ms in its thread) and 13.1, 6.7 and 9.9 ms
(`validate_njt_publication`), at the interpreter's switch interval of 0.005 s. PATH's stretch
run together is longer than its parse alone (355.8 ms in the second harness) while the
railroad gates run in threads beside it, so the alone figures are not cold-start stalls.
**Not measured:** a whole cold start's stalls with everything running at once; and NJ
Transit's download step on the loop, where the POST's body is buffered by `njt_auth.njt_post`
and then written inline (`backend/njt_static.py:773-774`). Both can be measured on this
machine with no mint, by pointing NJ Transit's token and archive URLs at a fake server on
127.0.0.1, as the token measurements of 2.7 were made. The rule the design inherits is the
ledger's: "a periodic refresh turns a startup stall into a recurring one: whatever runs every
few hours must not be able to stop the serving loop for as long as a cold start can"
(ledger:925-927),
and the 300 s attempt deadline (`backend/main.py:197`) cannot cut an inline parse short:
"Timeouts do not preempt synchronous CPU work" (ledger:291).

**The cold-start baseline: what a deploy or a hand Redeploy costs today.** Every cold start
between 2026-11-01 and 2026-11-28 that has a production deployment record, eight of them. The
four other records in that span (7526192539, 7536271414, 7613800372 and 7674420159, the last
`4489202`'s) each read `in_progress` and then `inactive` with no `success`, a skip; restarts
without a deploy are on no record read here. Statuses from the deployments API, read
2026-11-28 and re-read independently, matching to the second. Promotion times from the ledger (ledger:645 and :653 for `ef98328`; :653 for `b27dc6d`; :654 for `11e0dc5`; :655 for `590054a`; :664 and :589 for `55065d0`; :589 for the 2026-11-22 Redeploy and for `758c0f6`) and, for 2026-11-28, from production's `/api/status` `static_archives`, read at
10:03:08 UTC and again at 10:33:51 UTC with every `last_promoted_at` equal to the digit (and
agreeing with record:658). Spans are arithmetic over the
literals beside them, all UTC.

| Deploy | Record | `in_progress` | First `success` | First archive promoted | NJ Transit promoted | Last promoted | To first, s | To NJ Transit, s | To last, s | `success` before the last promotion, s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PR #130's merge, `ef98328` | 7474868063 | 2026-11-01 15:12:53 | 15:14:22 | 15:14:15 | 15:14:21 | 15:14:31 | 82 | 88 | 98 | 9 |
| PR #137's merge, `b27dc6d` | 7616080251 | 2026-11-07 03:39:38 | 03:41:18 | 03:41:11 | 03:41:18 | 03:41:27 | 93 | 100 | 109 | 9 |
| PR #138's merge, `11e0dc5` | 7627269401 | 2026-11-07 21:25:16 | 21:26:32 | 21:26:25 | 21:26:49 | 21:27:06 | 69 | 93 | 110 | 34 |
| PR #139's merge, `590054a` | 7639505039 | 2026-11-08 17:47:14 | 17:48:48 | 17:48:40 | 17:48:54 | 17:48:54 | 86 | 100 | 100 | 6 |
| PR #143's merge, `55065d0` | 7730268879 | 2026-11-12 06:35:07 | 06:36:30 | 06:36:26 | 06:36:34 | 06:36:43 | 79 | 87 | 96 | 13 |
| **Ben's hand Redeploy of `55065d0`** | 7730268879, reused | 2026-11-22 23:47:21 (18:47:21 Eastern) | 23:48:45, and again 23:48:49 | 23:48:39 | 23:48:52 | 23:49:09 | 78 | 91 | 108 | 24 |
| PR #145's merge, `758c0f6` | 7988955096 | 2026-11-23 02:28:43 | 02:29:18 | 02:29:11 | 02:29:20 | 02:29:30 | 28 | 37 | 47 | 12 |
| **Ben's hand Redeploy of `758c0f6`** | 7988955096, reused | 2026-11-28 08:00:16 (03:00:16 Eastern) | 08:00:51, and again 08:00:54 | 08:00:44.200 (ferry) | 08:00:50.670 | 08:00:57.278 (subway) | 28 | 34.7 | 41 | 6 |

On 2026-11-28 the six promotions ran ferry 08:00:44.200, LIRR 08:00:46.056, PATH 08:00:48.557,
NJ Transit 08:00:50.670, Metro-North 08:00:55.232 and subway 08:00:57.278, 13.077 s first to
last. What the records show, and what they do not:

- **Railway reports `success` before the last archive is promoted, on all eight**, by 6 to
  34 s, and the code allows it: the healthcheck is `/healthz` with a 300 s timeout
  (`railway.json:18-19`), `/healthz` answers 503 only for a gating code
  (`backend/routes/status.py:828`, `:853`), the gating codes are `no-feed-fresh`, `bus-route-index-failed` and `subway-static-failed` (`backend/models.py:1404-1408`), and "A
  still-LOADING static group or bus index is NOT degraded, so a cold-start deploy stays
  healthy through the warmup" (`backend/routes/status.py:776-777`).
- **From `in_progress` to the first promotion took 69 to 93 s on the six cold starts from
  2026-11-01 to 2026-11-22, and 28 s on both cold starts of `758c0f6`.** Why is on no record
  read here: what Railway did between `in_progress` and the new process's start is not in the
  statuses.
- **A promotion is not the moment the new schedule serves.** `last_promoted_at` is stamped
  when a download has passed its gate and been renamed over the cache
  (`backend/static_shared.py:483`, inside `_record` at `:693`, after the gate at `:645` and
  the rename at `:683`); the parse and the builders come after it (NJ Transit's at
  `backend/warmups.py:442-473`), then `ready` (`:474`). The instant of `ready` is not served on
  `/api/status`, so when a new process starts joining against its new schedule is on no
  record (2.4 measures the gap on this machine). One production datum bears on it: on
  2026-11-28 the subway's cached file has mtime 08:00:44.220 UTC
  (`static_subway_gtfs.mtime` 1795852844.2199597), 13.058 s before its `last_promoted_at`
  (08:00:57.278 UTC), read at 09:43:38 and again at 10:15:36 UTC; the same gap on this
  machine was 0.833 s, around a subway gate measured above at 821.3 ms. The cause of the
  difference is not measured.
- **When the old process stopped serving at either hand Redeploy is on no record.** A merge
  deploy's predecessor record goes `inactive` 3 to 4 s after the new `success` (for example
  7639505039 at 2026-11-12 06:36:34, 4 s after 06:36:30); a Redeploy reuses its record and
  writes no `inactive`. What the second `success` on each Redeploy record means is not known.
- **Every cold start downloads every archive**, because "the data directory does not
  persist" (`backend/static_shared.py:598-600`), and the bus index, which is not a
  `static_archives` key, is dated by no record read here.
- **What would date the rest, by a code read and not a reading.** Each warmup logs `"%s
  ready"` through the `main` logger (`backend/warmups.py:40`, `:72`), and the bus build logs
  `"bus route index %s: %d routes in %.0fs"` (`backend/bus_static.py:253-258`); both loggers
  are raised to INFO (`backend/main.py:108-109`), so Railway's deploy logs for 7730268879 and
  7988955096, if it still holds them, would date each group's `ready` and the bus build. The
  `"Downloading %s from %s"` and `"Promoted %s to %s"` lines (`backend/static_shared.py:631`,
  `:694`) go through the `static_shared` logger (`:47`), which that list does not raise from
  the root's WARNING (`backend/main.py:106-107`), so they would not appear. No log was read.

So today's only repair, a hand Redeploy, took 91 s on 2026-11-22 and 34.7 s on 2026-11-28
from its record's `in_progress` to NJ Transit's promotion, and 108 s and 41 s to the last
promotion; what happened before `in_progress`, and when the old process stopped serving, are
on no record. Its token cost is 2.7's.

### 2.2 How each publisher behaves

Two instruments see the publishers, and neither is a log of publications. The monitor's
lines say what changed in the fields each line prints, at the minutes the monitor happened to
run. The publishers' HTTP headers say when each file was last written, as of the minute they
were read.

**The monitor's minutes.** Runs 400 to 481: 82 runs, created 2026-10-25 11:41:24 UTC to
2026-11-28 07:58:21 UTC (33 d 20 h 16 min 57 s), 80 scheduled and 2 dispatched (439 and 443),
86 attempts with the re-runs, every attempt's log read (`gh run view --attempt <n> --log`).
The workflow says `cron: "17 */6 * * *"` (`.github/workflows/contract-monitor.yml:22`), but
the 81 gaps between runs ran from 1 h 56 min 12 s (443 to 444) to 16 h 9 min 49 s (471 to
472), median 10.65 h, and 24 of them exceeded 12 h. 84 of the 86 attempts read NJ Transit's
archive; the two that did not are run 443's (databaseId 42762598432, a dispatch without
`njt_mint`, then its re-run), which printed the skip reason of `_njt_mint_refused`
(`backend/scripts/contract_monitor.py:2247-2259`). A publication that came and went inside
one gap would not be seen.

**What each line printed, runs 400 to 481, set beside each file's Last-Modified on
2026-11-28.** Line texts from the 86 attempts' logs, compared whole; the bracket of a change is
the first new run's `created_at` less the last old run's. Last-Modified from a HEAD and GET of
each URL at 2026-11-28 09:41:50 to 09:42:41 UTC, unchanged at 10:18 UTC. "Then" is the
ledger's own reading of the publications served on 2026-10-15 at 14:37 UTC
(ledger:825, :832-837); "now" is this design's download of
2026-11-28 09:42:41 to 09:43:51 UTC.

| Archive | Line | Distinct texts | Changes the line showed | Last-Modified (UTC) | Then and now: bytes on the wire, `stop_times.txt` rows |
| --- | --- | --- | --- | --- | --- |
| Subway | `subway-static` | 2 | 1: run 410 (databaseId 40693489876, 2026-10-28 13:06:47) printed `WARN  subway-static: feed_end_date 20261031 is within 30 days`, run 411 (40781701629, 22:39:58) `PASS  subway-static: 1488 stops parsed`; bracket 9 h 33 min 11 s | 2026-10-28 16:39:52, inside that bracket; `feed_info` now 20261101 to 20270515 | not in that table |
| LIRR and Metro-North | `railroad-static` | 1, `PASS  railroad-static: stops within bands (LIRR 127, MNR 114)`, on all 86 | 0 | LIRR 2026-11-25 22:10:39; Metro-North 2026-11-26 17:30:32; both inside the window | LIRR 1,714,389 and 23,289, now 1,692,161 and 19,080; Metro-North 4,505,078 and 470,274, now 3,979,272 and 419,545 |
| PATH | `path-static` | 1, the acknowledged WARN on `feed_end_date 20260601`, on all 86 | 0 | 2025-06-06 17:54:51, before the window | 1,214,919 and 153,927, both times |
| NYC Ferry | `ferry-static` | 2 | 1: run 424 (41399145027, 2026-11-02 06:19:05) `7 routes, 51 stops, ER`, run 425 (41531218367, 21:45:18) `7 routes, 50 stops, ER`; bracket 15 h 26 min 13 s | 2026-11-27 06:25:01, after that change; `feed_version` now `20261127` | 35,842 and 3,386, now 31,699 and 2,919 |
| Bus, six archives | none: `bus-static` occurs 0 times in `backend/scripts/contract_monitor.py` at `1d17aa0`, `018e82d` and `16d1c19` | | | 2026-10-16 15:14:41 to 15:14:45, before the window | not in that table |
| AirTrain | none; a committed file | | | | |
| NJ Transit, the token API's archive | `njt-static` | 10: 7 PASS states, 1 FAIL, 2 skips | 6 dated changes, and 1 content difference dated only to a 104.38 h bracket, below | never read: not probed, and the app drops the response's headers (below) | 4,369,879 bytes on 2026-11-07 (2.1); rows unknown |

So **the line texts count changes the lines can see, not publications.** LIRR, Metro-North
and the ferry each served different bytes and different `stop_times.txt` rows on 2026-11-28
from those of 2026-10-15, with a Last-Modified inside the window, while `railroad-static`
printed one text for 812.28 h and `ferry-static` showed one change that predates the ferry's
current upload. How many uploads each made in the window, and which changed content, is not
measured.

**NJ Transit's archive, change by change.** The `njt-static` line names the route count, the
stop count, the id and name at `stop_code` NY and NP, and the last added service day of the
archive the run just downloaded from the API (`backend/scripts/contract_monitor.py:2484-2493`,
`:2071`).

| Change | Last run with the old text (databaseId, `created_at` UTC) | First with the new | Bracket | What changed |
| --- | --- | --- | --- | --- |
| C1 | 425 (41531218367, 2026-11-02 21:45:18) | 426 (41592974665, 2026-11-03 06:55:47) | 9 h 10 min 29 s | stops 109 and 112 now name `NORTH ELIZABETH` and `ORANGE`: the identity FAIL of the first incident |
| C2 | 425 | 434 (42345632878, 2026-11-07 06:07:57) | 104.38 h | 12 routes and 172 stops to 16 and 229, service end 20270501 to 20270503. The FAIL lines of runs 426 to 433 print neither counts nor service day, so this may be C1's own publication; the check's code also changed at `b27dc6d` |
| C3 | 445 (42889072568, 2026-11-10 19:49:15) | 446 (42973821279, 2026-11-11 06:38:14) | 10 h 48 min 59 s | 16 routes to 17, 229 stops to 228, 20270503 to 20270507 |
| C4 | 448 (43174588356, 2026-11-12 07:20:56) | 449 (43296282882, 2026-11-12 20:02:31) | 12 h 41 min 35 s | 20270507 to 20270509, counts unchanged |
| C5 | 461 (44260514018, 2026-11-18 07:38:52) | 462 (44393189160, 2026-11-18 20:38:43) | 12 h 59 min 51 s | 20270509 to 20270515, counts unchanged |
| C6 | 464 (44611764548, 2026-11-19 20:44:38) | 465 (44694946250, 2026-11-20 07:26:14) | 10 h 41 min 36 s | 20270515 to 20270516, counts unchanged |
| C7 | 479 (45967989070, 2026-11-27 08:44:18) | 480 (46070185338, 2026-11-27 21:25:13) | 12 h 40 min 55 s | 17 routes to 16, 228 stops to 227, 20270516 to 20270524 |

A run's reads happen after its monitor step opens and before its first printed line (2.3),
so `created_at` to `created_at` is not the strictest window: from the last old run's step
opening to the first new run's first printed line, C3 lies in under 10 h 49 min 22.18 s and C7
in under 12 h 41 min 10.89 s (the runs' logs). Over the four intervals between consecutive
changes from C3 on, where one line format and one check hold throughout, the lower bounds run
from 24.10 h to 169.30 h (median 78.16 h) and the upper bounds from 47.79 h to 192.68 h
(median 102.76 h), from creation to creation (`cadence.py`, which prints the shortest as
1 d 0 h 5 min 55 s, 24.10 h); from each change's first new run's first printed line to the next
change's last old run's step opening, the lower bounds run from 24.09 h to 169.30 h (median
78.15 h) (the runs' logs); the longest closed stretch with no visible change is runs 465 to 479,
169.30 h from creation to creation. These are intervals between changes the line can see.

**What each line cannot see.** Every line is `f"{r.status:4}  {r.name}: {r.detail}\n"`
(`backend/scripts/contract_monitor.py:4321-4323`), and on every static check a WARN or FAIL
detail is the list of problems only, which replaces the PASS text and its counts. An end date
is printed only within 30 days or past (`FEED_END_WARN_DAYS = 30`, `:265`;
`_feed_end_date_status`, `:1749-1795`). The check functions for the subway, railroads, PATH and
ferry, and their parsers, are unchanged across every head the runs executed, so every change in
those four lines is upstream.

| Line | Prints on PASS | Cannot see |
| --- | --- | --- |
| `subway-static` | the parsed stop count (`:1861`) | any republication that keeps 1,488 stops and an end date more than 30 days out or absent: trips, stop times, shapes, calendars, routes |
| `railroad-static` | each system's stop count (`:1927`) | any republication of either archive that keeps its stop count and a far or absent end date; the LIRR and Metro-North uploads above |
| `path-static` | parent count and `26733=Newark` (`:1983`); on all 86 attempts it printed the standing acknowledged WARN instead (`:1740`) | a republication that keeps end date 20260601, every required member, the app gate's acceptance, at least `PATH_STATIC_MIN_PARENTS` parents and stop 26733 named Newark (`:1966-1984`) |
| `ferry-static` | routes, stops, `ER` (`:2033`) | any republication keeping 7 routes, the stop count and a far or absent end date; the 2026-11-27 upload |
| `njt-static` | routes, stops, the two identity rows, the last service day | trips: no trip count, trip id, hash or archive date, so a republication that renumbers trip ids (the key of the second and third incidents) and keeps the rest is invisible; a skipped attempt; and the public `rail_data.zip`, which it does not read |
| `njt-realtime` | trip updates and feed state | production's held schedule: it decodes against the archive the run just downloaded, "which is always new" (`:3529-3530`) |
| `production:njt-schedule`, from run 471 | `M of N checked trips contradict the schedule's train number` | fewer than five checked, "too few to judge" (runs 471, 477, 479) |
| `production:njt-routes` | routes and points production serves | a republication that keeps the shapes; its value changed only after promotions (11 routes and 1609 points on runs 400 to 433, 13 and 1595 on 434 to 447, 14 and 1623 on 448 to 470, 14 and 1598 on 471 to 481) |
| `production:statics` | `all static groups ready`, on all 86 | which publication any group holds |

**Conditional GETs: every public publisher answers 304.** Each of the eleven public URLs the
app downloads (the subway, LIRR, Metro-North, PATH, the ferry and the six bus archives), and
NJ Transit's public `rail_data.zip`, was asked with HEAD, GET, `If-None-Match` carrying its
ETag and `If-Modified-Since` carrying its Last-Modified, through its redirects and at its
final URL, by curl 8.7.1 from this machine, 2026-11-28 09:41:50 to 09:42:41 UTC, and again
10:18:07 to 10:18:22 UTC with the same twelve answers and validators. HEAD and GET agreed on
ETag, Last-Modified, Content-Length, Content-Type and Server for all twelve.

| Archive | URL defined | Server | ETag, and what it is | Last-Modified (UTC) | `If-None-Match` | `If-Modified-Since` | The 304 carried |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Subway | `backend/static_data.py:38-40` | AmazonS3 | `"de495fc9e33cb8115581ae63807a25d8"`, the body's MD5 | 2026-10-28 16:39:52 | 304, no body | 304, no body | ETag, Last-Modified |
| LIRR | `backend/railroad_static.py:50-52` | AmazonS3 | `"3ccea7730b057624f72fc5a1fdd0c9eb"`, the body's MD5 | 2026-11-25 22:10:39 | 304 | 304 | ETag, Last-Modified |
| Metro-North | `backend/railroad_static.py:50`, `:53` | AmazonS3 | `"a1e0461a9e854026821605d6be858ee0"`, the body's MD5 | 2026-11-26 17:30:32 | 304 | 304 | ETag, Last-Modified |
| Bus, six archives | `backend/bus_static.py:56-63` | AmazonS3 | the body's MD5, six of six | 2026-10-16 15:14:41 to 15:14:45 | 304, six of six | 304, six of six | ETag, Last-Modified |
| PATH | `backend/path_static.py:65-67` | Apache/2.4.6 (CentOS) | `"1289c7-636eaed4871b8"`: hex of the size (1,214,919) and of the mtime in microseconds | 2025-06-06 17:54:51 | 304 | 304 | ETag; no Last-Modified |
| NYC Ferry | `backend/ferry_static.py:65-67`, a 302 to `/rtt/public/resource/gtfs.zip` | Microsoft-IIS/10.0 | `"eb81708387edd1:0"`, not decoded | 2026-11-27 06:25:01 | 304, through the 302 and at the final URL | 304, both ways | date and ETag only |
| NJ Transit public `rail_data.zip` (the app does not fetch it) | none in app code | cloudflare | `"6b09e931-3247f7"`: hex of the Last-Modified in seconds and of the Content-Length, on 11 of 11 observations | 2026-11-27 20:33:53 | 304, through two 301s, the first to another host | 304 | ETag, Last-Modified, `cache-control: max-age=300`, `expires`, `age` |
| NJ Transit token API, `getGTFS` | `backend/njt_static.py:144-146` | not probed | unknown | unknown | unknown | unknown | unknown |

The S3 ETag is the body's MD5 on all nine S3 objects, so it does not change for identical
bytes; the PATH and NJ Transit ETags encode a file time, so by inference (no re-upload was
observed) they change on any re-upload. The application's own client, httpx 0.28.1 as the
lock pins it (`backend/requirements-lock.txt:25`), built as the app builds it
(`follow_redirects=True`, `client.stream("GET", ...)`), carried `If-None-Match` across the
ferry's 302 and NJ Transit's two 301s, and both answered 304 (2026-11-28 10:22:57 and 10:22:58
UTC). How long Cloudflare's edge serves the previous `rail_data.zip` after NJ Transit replaces
it is not measured; the only number on the wire is `max-age=300`.

**What the app sends and keeps today: no validator.** Every public download is a plain GET
with no headers (`backend/static_shared.py:537-538`; the bus at `backend/bus_static.py:199`, on
the client of `:231`), and NJ Transit's POST returns `resp.status_code, resp.content`, dropping
the response's headers (`backend/njt_auth.py:715-716`). A `git grep` at `16d1c19` for ETag,
Last-Modified, the two conditional headers or a 304 in `backend/` outside the tests finds only
`backend/main.py:462-463`, the app's own static file serving, and no archive hash or header
capture. Each archive's record is four fields, `{last_promoted_at, last_download_error,
failed_downloads, degraded_members}` (`backend/static_shared.py:433-440`), and none names a
publication. Whether to download is the cache file's mtime against 30 days
(`backend/static_shared.py:425-426`, `MAX_AGE_DAYS = 30` in every loader). Inside the archives,
the identities on offer are: subway `feed_version`
`20261028-fall-2026-pick-with-long-term-supplements`; LIRR `GO203_26`; Metro-North no
`feed_info.txt`; PATH `UTC: 22-May-2025 18:06` (feed 20250522 to 20260601); ferry `20261127`
(20261127 to 20271231); the six bus archives `20261009` (five) and `20261012` (MTA Bus Co),
feed 20261012 to 20270102; NJ Transit's public file neither `feed_info.txt` nor
`calendar.txt`.

**NJ Transit's token-gated endpoint: what is not known.** Nothing was probed, by rule. What
the repository records, and what that leaves open:

- **Method.** "every RailData endpoint is POST multipart/form-data with the token as a form
  field, so a GET gets nothing" (`backend/njt_static.py:761-762`). Whether a HEAD exists is
  not recorded.
- **Validators.** The app drops the headers (`backend/njt_auth.py:715-716`), and no script,
  fixture, ledger line or pull request body read for this design records a header of a
  `getGTFS` response. Whether it sends an ETag or a Last-Modified, and whether it honours a
  conditional request on a POST, is unknown.
- **What a download costs NJ Transit's budget.** Recorded: ten getToken calls per account per
  Eastern day, every attempt counted (`backend/njt_auth.py:140-143`, `DAILY_MINT_LIMIT = 10`
  at `:183`), of which one process may spend 4 (`DAILY_MINT_ALLOWANCE`, `:219`), and a token
  held up to 12 h (`MAX_TOKEN_AGE_S`, `:302`). Whether a `getGTFS` call is itself counted or
  metered is not recorded, and the usage counter cannot be read with a GTFSRT token
  (`:145-146`). 2.7 measures what a refresh spends.
- **Size and dates.** The latest recorded size of a token pull, 4,369,879 bytes (2.1), and
  two recorded dates of it, "the archive is dated 19:11 EST, 2026-11-07 00:11 UTC" and "its
  members built 2026-11-06 14:25" (ledger:653), the first from a
  source the line does not name. No other archive date of the API's is on record.
- **Whether it and the public file are ever one publication**: below. No same-day pair of
  hashes exists, so byte identity on any day is unknown. The monitor's existing run downloads
  the API's archive on its one token every scheduled run
  (`backend/scripts/contract_monitor.py:2074-2097`), and prints none of its bytes, hash, trip
  count or member sizes.

**NJ Transit's public file: every known publication.** Nine from ten Wayback Machine captures
of the zip's final URL, listed by its CDX index (read 2026-11-28 09:43:42 to 09:43:50 UTC and
again 10:18:52 to 10:18:57 UTC), each capture's original headers read with one HEAD of its
`id_` URL and the four 2026 bodies downloaded and parsed (their SHA-1 equal to the CDX
digest); one known only from our records; and today's, measured. Service spans are the added
`calendar_dates` days.

| # | Last-Modified (UTC) | Bytes | ETag | Service span | Routes, stops, trips | Source |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 2023-04-21 16:10:14 | 5,580,897 | `"6442b566-552861"` | not read | not read | capture 20230509111746, headers |
| 2 | 2023-08-17 17:13:21 | 5,279,515 | `"64de5531-508f1b"` | not read | not read | capture 20230820014310 |
| 3 | 2024-09-22 17:09:02 | 3,932,259 | `"66f04f2e-3c0063"` | not read | not read | capture 20240922171920 |
| 4 | 2025-03-26 17:59:26 | 2,808,730 | `"67e4407e-2adb9a"` | not read | not read | capture 20250406105506 |
| 5 | 2025-05-20 16:11:36 | 2,926,053 | `"682ca9b8-2ca5e5"` | not read | not read | capture 20250524001005 |
| 6 | 2026-03-16 18:33:54 | 3,092,416 | `"69b84d12-2f2fc0"` | 20260315 to 20260910, 580 rows, 180 dates | 16, 228, 3,233 | capture 20260325002641, body read |
| 7 | 2026-05-14 19:59:33 | 5,524,371 | `"6a0629a5-544b93"` | 20260513 to 20261108, 1,300 rows, 180 dates | 16, 230, 5,145 | capture 20260525025342, body read |
| 8 | 2026-07-31 00:10:10 | 3,080,193 | `"6a6be7e2-2f0001"` | 20260729 to 20270124, 619 rows, 180 dates | 17, 231, 3,469 | capture 20260801012305, body read |
| 9 | 2026-09-17 17:55:35 | 6,380,384 | `"6aac2997-615b60"` | 20260915 to 20270313, 1,816 rows, 180 dates | 17, 230, 6,197 | captures 20261018072718 and 20261022002243, the second's body read |
| 10 | 2026-11-20 21:31:55 | 3,316,038 | not recorded | 20261118 to 20270516 (the endpoints only; dates not counted) | 17, 228, 3,355 | `backend/tests/fixtures/njt_drift_2026-11-22.json:3-14`, read 2026-11-22; the same bytes fetched 2026-11-24 00:06 UTC (ledger:589); no copy held |
| 11 | 2026-11-27 20:33:53 | 3,295,223 | `"6b09e931-3247f7"` | 20261126 to 20270524, 1,617 rows, 180 dates | 16, 227, 3,301 | GET 2026-11-28 09:42:01 UTC; first read 08:35:10 UTC (record:589, :651) |

Each of the five 2026 publications whose body was read (rows 6 to 9 and 11) spans exactly 180
service dates, starting one day (rows 6, 7, 11) or two days (rows 8, 9) before its
Last-Modified date; row 10's endpoints also span 180 days, starting two days before. Trip ids
are exactly 1..N in every body read. The days between consecutive 2026 Last-Modified dates
are 59, 78, 48, 64 and 7. These are known publications only: Wayback captured no day between
2026-10-22 and 2026-11-28, and the captures are too sparse to rule out a publication between
any two rows. What they do bound: row 9 was the file at both 2026-10-18 07:27:18 and 2026-10-22
00:22:43 UTC (`x-archive-orig-date`), so it was not replaced in that interval, if
Last-Modified tracks each upload.

**The public file is not the API's archive, and was not a mirror of it before November.**

| When, or what | The public file | The API's archive | The same? |
| --- | --- | --- | --- |
| 2026-08 | row 8: 619 `calendar_dates` rows, 20260729 to 20270124; 17 routes, `route_type` 2 (14) and 0 (3); 231 stops, ids up to 43599 | the 2026-08-05 probe (`backend/njt_static.py:14-36`): 8,697 rows, 2026-07-28 to 2027-05-01; 12 routes, all `route_type` 113; 172 stops, ids 1..176 | no |
| 2026-10-22 | row 9: through 20270313 | runs 382 to 425 (to 2026-11-02 21:45:18 UTC): 12 routes, 172 stops, through 20270501 | no |
| Stop ids and codes | 105 New York Penn and 107 Newark Penn in rows 6 to 9 and 11, since 2026-03-16, with `stop_code` 95105 and 95107 | took 105 and 107 only at C1 (runs 425 to 426), with `stop_code` NY and NP (`njt-static`: `NY=105 'NEW YORK PENN STATION', NP=107 'NEWARK PENN STATION'`) | the ids from November; the codes never |
| 2026-11-20 | row 10: 17, 228, through 20270516 | runs 465 to 479: 17, 228, through 20270516 | by counts and service end only |
| 2026-11-27 | row 11: 16, 227, through 20270524 | runs 480 and 481: 16, 227, through 20270524 | by counts and service end only |
| `trip_short_name` | no such column in `trips.txt` | carried: the parser reads it (`backend/njt_static.py:385`), and a trip counts as checked only when the schedule gives it one (`backend/feeds/njt.py:812-814`), as 58 of 58 joined did in production at run 480 | no |
| Light-rail trips | 1,914 of 3,301, `route_type` 0 | inferred absent: removing them reproduces production's 164 stops and `/api/njt-routes` value for value (2.1) | inferred no |

**Last-Modified is a bound, not a publish time, so neither lag between the two is measured.**
A Last-Modified header gives the latest write before the fetch, not the first moment the file
carried that publication. Row 11's was first read at 2026-11-28 08:35:10 UTC; row 10's on
2026-11-22. Set beside the API: run 465's line, stamped 2026-11-20 07:26:48 UTC, already read
20270516, 14 h 5 min 7 s before row 10's Last-Modified (21:31:55 UTC), so either the two
channels publish at different times, or a later upload or a later publication with the same
counts and service end refreshed the header; nothing read here tells which. On 2026-11-27 the
trains contradicted production's schedule by 20:13:00 UTC at the latest (record:589, :647), 20 min 53 s before row 11's Last-Modified; whether the
public file first changed before or after the realtime feed moved is not measured. **The first
incident has no public-file record at all**: no capture or read exists between row 9 and row
10, and the public file's ids could not have shown it, since they were already 105 and 107.

### 2.3 Run 445's shape, and 2026-11-27's bounds

Run 445 is the one run in the record that named pairs: trips to which NJ Transit's realtime
feed gave a different train number from the one in the schedule that same run had just
downloaded. (Runs 434 and 441 printed counts of 7 and 1, of a kind their lines do not name,
and the counts of runs 452 and 455 held no mismatch, below.) Run 445 (databaseId
42889072568, scheduled, attempt 1, created 2026-11-10 19:49:15 UTC, on `4489202`), its two
NJ Transit lines verbatim from its log:

```
2026-11-10T19:49:48.2472094Z PASS  njt-static: 16 routes, 229 stops, NY=105 'NEW YORK PENN STATION', NP=107 'NEWARK PENN STATION', service through 20270503
2026-11-10T19:49:48.2475033Z WARN  njt-realtime: 41 entity.id/trip_short_name mismatches (probe: 0 of 745): 0421/6431 on trip 2152, 0423/6437 on trip 2153, 0426/0400 on trip 2069, 0428/0430 on trip 2070, 0877/6621 on trip 1974, 36 more not named
```

Its summary read `0 FAIL, 3 WARN, 22 checks total`, and its production lines carried no NJ
Transit count.

**Five pairs, and a count of 41 of three kinds.** Each pair is `entity.id/trip_short_name on
trip trip_id`: `0421/6431 on trip 2152` says the realtime feed calls trip 2152 train 0421 and
the schedule run 445 downloaded calls it train 6431. The five named trip ids include two
adjacent pairs (2152 and 2153, 2069 and 2070). The 41 is `len(warnings)`
(`4489202:backend/scripts/contract_monitor.py:1481`), every identity warning the decoder
raised, of all three kinds: a number mismatch, raised only where the trip joined
(`4489202:backend/feeds/njt.py:582`), a trip with no identity (`:588`) and a repeated identity
(`:611`). The five are the first warnings that parse as a mismatch, and "36 more not named" is
41 less 5, mismatches past the cap and warnings of the other kinds alike
(`4489202:backend/scripts/contract_monitor.py:1300-1319`). So run 445 shows at least 5 number
mismatches and at most 41. That a count of that code is not all mismatches is on record: the
monitor's own docstring says runs 452 and 455 printed `"3"` and `"1 entity.id/trip_short_name
mismatches"` "for warnings that were none of them a mismatch"
(`backend/scripts/contract_monitor.py:1319-1322`). No denominator was printed: how many trip
updates joined or could be checked is not in the log.
`(probe: 0 of 745)` is a literal of the format string, the 2026-08-05 baseline, not a count
from that run.

**When the two reads happened.** The log stamps neither. Every check line of run 445 was
written within 1.174 ms, from 19:49:48.2468682 to the summary, because the monitor takes `now`
once, runs every check inside `run_all`, and only then writes the lines
(`4489202:backend/scripts/contract_monitor.py:4074-4078`; the same at `16d1c19`,
`backend/scripts/contract_monitor.py:4337`, `:4339`, `:4341`). Inside `run_all` the schedule
download comes first and the realtime decode after it
(`4489202:backend/scripts/contract_monitor.py:4006`, `:4032`; at `16d1c19`, `:4261`, `:4293`).
The monitor step opened at 19:49:29.1337651, so both reads lie in 19.1131031 s, in that order, how
far apart not logged. A step's opening stamp precedes every read in it and its first printed
line follows every read, so each bound below lies on the safe side of the read it stands for.

| Bound on 2026-11-10 | UTC (Eastern) | From |
| --- | --- | --- |
| Realtime's ids moved, at the latest | before 19:49:48.2468682 (14:49:48) | run 445's realtime read, before its first printed line |
| Realtime's ids moved, at the earliest | after 2026-11-09 22:28:02.8353449 (17:28:02) | run 442 (databaseId 42712092087) at its step opening: 108 trip updates in service and no identity warning of any kind, which on `590054a` would have made the line a WARN |
| The same, if run 444 is a witness | after 2026-11-10 07:27:18.5692700 (02:27:18) | run 444 (42775373334) at its step opening: 7 trip updates, closed, no warning; how many could be checked was not printed |
| The API's schedule moved, at the earliest | after 19:49:29.1337651 (14:49:29) | run 445's step opening: its download still read 16, 229, 20270503 |
| The API's schedule moved, at the latest | before 2026-11-11 06:38:51.3086373 (01:38:51) | run 446's (42973821279) first printed line; its `njt-static` line, stamped 06:38:51.3092359, printed 17, 228, 20270507, and its realtime agreed |

From these, by arithmetic over the stamps: the API's change lies in a window under 10 h 49 min
22.18 s; the realtime move in one under 21 h 21 min 45.42 s, or under 12 h 22 min 29.68 s if run
444 counts; the longest lead of realtime over the API consistent with the logs is under 32 h 10
min 48.48 s, or under 23 h 11 min 32.74 s with run 444. **The shortest lead is not positive.**
Run 445 downloaded first and read realtime after, within 19.1131031 s, so had both moved together,
or the API less than 19.1131031 s before the feed, inside those seconds, run 445 would have printed
what it printed and run 446 what it printed. A lead of zero is not excluded. The ledger's
"most likely the realtime feed moved before the schedule endpoint did, so a fresh schedule can
be behind too, for some hours" (ledger:589) is an inference from one
run landing in the window, not a measurement. That run is also why `SCHEDULE_BEHIND_HOLD_S`
exists (`backend/feeds/njt.py:602-605`), and its 600 s is "the band the operator's other codes
use" (`:605-606`). From run 446 on the pairs are gone: every `njt-realtime`
from 446 to 450 is a PASS, run 447's on 76 trip updates in service. The download changed once
more in that stretch, C4 (run 448's step opening 2026-11-12 07:21:12.26 UTC to run 449's first
line 20:03:04.69 UTC, 20270507 to 20270509), and run 449, in service with 79 trip updates,
printed no pairs. What production held in those days, and what it implies, is 1.1.4's.

**2026-11-27: the two moves, with no run between them.**

| Run (databaseId, created UTC) | `njt-static` | `njt-realtime` | `production:njt-schedule` |
| --- | --- | --- | --- |
| 477 (45783913934, 2026-11-26 08:31:39) | 17 routes, 228 stops, through 20270516 | PASS, 21 trip updates, closed | PASS, 0 of 0 checked, too few to judge |
| 478 (45899743040, 2026-11-26 21:40:26) | 17, 228, 20270516 | PASS, 64, in service | PASS, 0 of 43 checked (43 joined) |
| 479 (45967989070, 2026-11-27 08:44:18) | 17, 228, 20270516 | PASS, 6, closed | PASS, 0 of 2 checked (2 joined), too few to judge |
| 480 (46070185338, 2026-11-27 21:25:13) | **16, 227, 20270524** | PASS, 66, in service | **FAIL**, 44 of 58 checked (58 joined); "0 of 61 against the schedule this run downloaded" |
| 481 (46123612408, 2026-11-28 07:58:21) | 16, 227, 20270524 | PASS, 12, closed | **FAIL**, 7 of 8 checked (8 joined); "0 of 8 against the schedule this run downloaded" |

All five are scheduled first attempts on `018e82d`, read from their logs; runs 480 and 481 match the republication record verbatim (record:680, :691). What they bound:

- **The realtime feed's trip ids** had moved by 2026-11-27 20:13:00 UTC (15:13:00 Eastern) at
  the latest, since `/healthz` raised `njt-schedule-behind` at Claude's read at 20:23:00 UTC
  (15:23:00 Eastern) and raises it only after the rule has held for 600 s (record:589, :647, :648). They had not moved at run 478's read
  (production 0 of 43 checked, its own decode clean), whose step opened 2026-11-26
  21:40:50.3029436 UTC: a window of 22 h 32 min 9.70 s.
- **The API's schedule** had moved by run 480's first printed line (21:25:44.52 UTC) and had not
  at run 479's step opening (08:44:33.63 UTC), whose download was still the publication
  production held (record:646): a window under 12 h 41 min
  10.89 s.
- **By run 480 the two agreed**: 0 of 61 trips contradicted the schedule that run downloaded,
  and 0 of 8 at run 481. No run landed between the two moves, so no run printed run 445's
  shape that day.
- **Which led is not known.** The bounds allow the API first by up to 11 h 28 min 26.37 s (run
  479's step opening to 20:13:00) or the realtime feed first by up to 23 h 44 min 54.22 s (run
  478's step opening to run 480's first line). A lead of zero is not excluded on this day
  either.
- **Run 479, a conditional witness.** Its production read checked 2 trips of 2 joined and
  found 0 contradicting the held schedule, and its own decode passed on 6 trip updates, closed.
  If those reads would have shown a move, the realtime feed moved after 08:44:33.63 UTC, the
  longest realtime lead shrinks to 12 h 41 min 10.89 s, and the API's possible lead is
  unchanged. Nothing measured here says whether 2 checked trips would have shown it, so the
  witness is stated, not counted.
- **The riders' window** is 1.1.3's: at most 34 h 20 min 0.37 s from run 478's step opening (21:40:50.30 UTC) to the promotion's `last_promoted_at` of 08:00:50.670 UTC (34 h 19 min 41 s by the record, counted from run 478's printed line, record:666), and 23 h 16 min 17.04 s if run 479 counts.
- **The public file** read Last-Modified 2026-11-27 20:33:53 UTC, 20 min 53 s after 20:13:00:
  a bound on its latest write before 2026-11-28 08:35:10 UTC, not its first (2.2).

**What the two days bound together**, from the logs above:

| | 2026-11-10 | 2026-11-27 |
| --- | --- | --- |
| The API's change lies in | a window under 10 h 49 min 22.18 s | a window under 12 h 41 min 10.89 s |
| The realtime move lies in | a window under 21 h 21 min 45.42 s (12 h 22 min 29.68 s with run 444) | a window of 22 h 32 min 9.70 s (11 h 28 min 26.37 s with run 479) |
| Longest realtime lead consistent with the logs | under 32 h 10 min 48.48 s (23 h 11 min 32.74 s with run 444) | 23 h 44 min 54.22 s (12 h 41 min 10.89 s with run 479) |
| Longest API lead consistent with the logs | under 19.12 s (run 445's step, 19.1131031 s) | 11 h 28 min 26.37 s |
| A lead of zero | not excluded | not excluded |
| A run that read realtime contradicting its own fresh download | run 445: at least 5 and at most 41 mismatches, no denominator | none |

### 2.4 What a swap must cover

A refresh replaces, in a running process, what a cold start builds once. This subsection lists
what that is, per archive: every structure built from it, the code that builds it, where it is
assigned, who reads it, and what each reader takes together. It was measured for this design on
2026-11-28 by three inventories (NJ Transit; the subway and the bus index; the railroads, PATH,
the ferry and AirTrain), each followed by a second pass that re-derived its numbers with scripts
of its own. The instruments: a whole-word grep of every `app.state` attribute across `backend/`
and `frontend/`, tests included; an AST pass listing every `await` and every `app.state` read per
function; and the real warmups run offline on this machine (CPython 3.12.13, macOS), with the
download replaced by a local copy of the public archive, a 1 ms heartbeat or an event-loop
iteration counter on the loop, and every `app.state` write stamped. Scripts are named below by
file name; they and their outputs are kept in the design's measurement archive
(`2026-11-28-static-refresh-design/`, one folder per item and one per verification), outside the
repository. Nothing here ran on production's hardware. Production was read only through
read-only GETs, and at every read on 2026-11-28 its static payloads equalled the payloads the
app's own code builds from that day's public archives: `/api/subway-stops` (496) and
`/api/subway-routes` (24) between 09:43:38 and 09:46:55 UTC and again at 10:15:36 to 10:15:37 UTC; `/api/railroad-stops` (241),
`/api/railroad-routes` (18), `/api/path-stops` (13), `/api/ferry-stops` (50) and
`/api/ferry-routes` (7) at 09:58:32 UTC, through the real route handlers and response models; and
`/api/njt-stops` (164) and `/api/njt-routes` (13 routes, 18 polylines, 1,595 points) at 09:47:02
and 10:17:26 UTC, against a rail-only projection of NJ Transit's public `rail_data.zip`
(`Last-Modified: Fri, 27 Nov 2026 20:33:53 GMT`, 3,295,223 bytes, sha256 `11ba8a68...9869`), its
1,914 light-rail trips on routes 4, 12 and 16 removed. That production's token archive lacks
those trips is an inference from served counts: the monitor prints no trip count, and runs 480
(databaseId 46070185338, created 2026-11-27 21:25:13 UTC) and 481 (46123612408, created
2026-11-28 07:58:21 UTC) printed `16 routes, 227 stops`, the public file's figures.

What each structure costs in memory, by which instrument, is 2.1's; this subsection names which
structures have no reader at all.

#### 2.4.1 NJ Transit (`data/gtfs_static/gtfs_njt.zip`, downloaded through the token)

Counts are the public archive's, then the rail-only projection's, which is production's served
form (`measure_njt_structures.py`, re-derived by `verify_m4a.py`).

| Structure | Built by | Assigned at | Read by | Read together with |
| --- | --- | --- | --- | --- |
| `njt_static`: the whole parse, 6 tables (227 stops, 16 routes, 3,301 / 1,387 trips, 1,476 / 1,387 shapes, stop_times, calendar_dates) | `_parse_zip` inline (`backend/njt_static.py:1301`) | `backend/warmups.py:442` (initial `{}` at `backend/main.py:263`) | **no production reader**: tests only (`backend/tests/test_api.py:5609`, `:5852`) | nothing |
| `njt_stops`: served stations, 227 / 164; its values are the same dict objects as `njt_static["stops"]` | `served_stops` (`backend/njt_static.py:869`) | `backend/warmups.py:455` | the poll (`backend/pollers.py:1122`); the decoder's call filter (`backend/feeds/njt.py:249`), placement, and the own last stop's name of a refuted or unjoined train (`:702`, `:727`); `/api/njt-stops` (`backend/routes/njt.py:79`); `/api/njt-arrivals` gate and `stop_name` (`:179` to `:181`, `:188`) | in the poll, `njt_trips` and `njt_lines`; in `/api/njt-stops`, `njt_station_routes` and the status |
| `njt_trips`: trip_id to route, headsign, number; 3,301 / 1,387 | `build_njt_trip_index` (`backend/njt_static.py:918`) | `backend/warmups.py:469` | the poll (`backend/pollers.py:1123`); the decoder's join (`backend/feeds/njt.py:812`), which yields `joined`, `checked` and `number_mismatches` | the poll's set |
| `njt_lines`: route_id to rider-facing name; 16 | `build_njt_line_names` (`backend/njt_static.py:1152`) | `backend/warmups.py:470` | the poll (`backend/pollers.py:1124`); the decoder's fallback headsign (`backend/feeds/njt.py:729`) | the poll's set |
| `njt_station_routes`: stop_id to route ids; 227 | `derive_njt_stop_routes` (`backend/njt_static.py:844`) | `backend/warmups.py:448` to `:449` | `/api/njt-stops` (`backend/routes/njt.py:80`) | `njt_stops` |
| `njt_routes`: 16 routes, 22 polylines / 13, 18, 1,595 points | `build_njt_route_shapes` (`backend/njt_static.py:1159`) | `backend/warmups.py:463` to `:465` | `/api/njt-routes` (`backend/routes/njt.py:124`) | the status |
| `njt_stop_schedule`: 227 stops, 44,102 / 17,461 calls | `build_njt_stop_schedule` (`backend/njt_static.py:945`) | `backend/warmups.py:471` to `:473` | **no production reader**: tests only | nothing |
| `njt_static_status` | `_set_static_status` | `ready` at `backend/warmups.py:474` | the poll gate, before its await (`backend/pollers.py:1095`); both static routes (`backend/routes/njt.py:76`, `:121`); `/api/status` (`backend/routes/status.py:195`); `/healthz` (`:739`) | each reader's own set |
| `static_shared._ARCHIVE_STATUS["njt"]`, a module global (`backend/static_shared.py:440`) | `_record` inside `staged_fetch` | at the promotion (`backend/static_shared.py:693`), several awaits before the block | `/api/status` `static_archives.njt` (`backend/routes/status.py:224`) | nothing |
| The archive file | `staged_fetch`'s rename (`backend/static_shared.py:683`) | on disk | the two cached checks in worker threads (`backend/njt_static.py:1269`, `:1274`), then the inline parse (`:1301`) | not app state |

The poll reads `njt_stops`, `njt_trips` and `njt_lines`, and no other static structure
(`backend/pollers.py:1122` to `:1124`). The ledger's "stations, routes, trips and lines are read
together by every poll" (ledger:691-692) names
one more than the poll reads: `njt_routes` and `njt_station_routes` are read only by the two
static endpoints. The decoder keeps no module-level cache: each decode builds its keys, trains
and arrivals afresh (`backend/feeds/njt.py:928` to `:935`). `njt_static_task` is read at shutdown
(`backend/main.py:374`, `:385`).

**The block assigns in one step on success.** `backend/warmups.py:442` to `:475` holds no
`await`. With a stand-in loader, all 8 assignments carried the same heartbeat count, 38; with the
real `load_njt_static` over a cached public archive, 0 of 395 ticks of a 1 ms watcher saw a mixed
set of the 8 fields, and the task ended done with result `None` and status `ready`
(`verify_m4a.py` part D). 2.4.9 gives the case where it is not atomic.

#### 2.4.2 Subway (`data/gtfs_static/gtfs_subway.zip`)

Counts from the cached arm of the real `warmups._warm_subway_static` over the publication of
2026-10-28 (`Last-Modified: Wed, 28 Oct 2026 16:39:52 GMT`, 5,606,305 bytes, `feed_version`
`20261028-fall-2026-pick-with-long-term-supplements`), by `runtime_measure.py`, re-derived by
`raw_counts.py` (no app code) and `verify_runtime.py`.

| Structure | Built by | Assigned at | Read by | Read together with |
| --- | --- | --- | --- | --- |
| `subway_stops`: 1,488 (496 parents, 992 platforms) | `load_subway_stops` (`backend/static_data.py:382-436`), on the loop; the promotion happens inside it | `backend/warmups.py:193` | the poll (`backend/pollers.py:586`, gate `:587`, passed at `:610`); the decoder's membership and lookups (`backend/feeds/subway.py:219`, `:254`, `:288`, `:297`), copied into each train's coordinates, `stop_name` and previous stop | alone: the poll reads no other static structure |
| `subway_routes`: 24 routes, 35 polylines, 22,458 points | `load_subway_route_shapes`, in a thread (`backend/warmups.py:180`) | `:194` | `/api/subway-routes` (`backend/routes/subway.py:45`); `/healthz`'s `subway-lines-degraded` (`backend/routes/status.py:823`) | the status |
| `subway_stations`: 496 | `load_subway_stations`, inline (`backend/warmups.py:181`) | `:195` | `/api/subway-stops` (`backend/routes/subway.py:57`); the arrivals gate (`:88`) | the two below and the status |
| `subway_station_routes`: 496, every one with a route | `load_subway_station_routes`, in a thread (`backend/warmups.py:186`) | `:196` | `/api/subway-stops` (`backend/routes/subway.py:58`); a board's expected groups (`:97`); `/api/status` and `/healthz` through `_unmapped_subway_routes` on every call (`backend/routes/status.py:469-472`) | as above |
| `subway_station_complexes`: 496 to 444 complexes, 35 of two or more | `load_subway_station_complexes`, inline (`backend/warmups.py:189`) | `:197` | `/api/subway-stops` (`backend/routes/subway.py:59`) | as above |
| `subway_static_status` | `_set_static_status` | `ready` at `backend/warmups.py:198` | routes, `/api/status`, `/healthz` (`subway-static-failed`, `backend/routes/status.py:813`) | |
| The file, by module path `static_data.SUBWAY_GTFS_ZIP` (`backend/static_data.py:33-34`) | `staged_fetch`'s rename | on disk | every loader, with no path parameter (`backend/static_data.py:378`, `:449`, `:513`, `:667`, `:771`); `/api/status` stats its mtime on every request (`backend/routes/status.py:131-135`) | not app state |
| `_ARCHIVE_STATUS["subway"]` | `_record` | `backend/static_shared.py:693` | `/api/status` | |

The builds are separated by awaits (`backend/warmups.py:173`, `:180`, `:186`), each reopening the
file at the module path, so the five structures agree only while that path is unchanged for the
attempt; the assignment is one step: all six writes ran in one event-loop iteration (iteration 12
cold, 23 cached, `verify_runtime.py` counting `BaseEventLoop._run_once`). The app reads no
calendar, no `feed_info`, no `routes.txt` and no `agency.txt` from this archive (a grep of
`backend/static_data.py`). Two loaders are lenient and are assigned under `ready` when they fail:
`load_subway_stations` returns `{}` (`backend/static_data.py:453-455`) and
`load_subway_route_shapes` returns `[]` (`:543-545`). An empty `subway_routes` raises
`subway-lines-degraded`; an empty `subway_stations` raises no code.

#### 2.4.3 The bus route index (six borough archives)

| Structure | Built by | Written | Read by |
| --- | --- | --- | --- |
| `bus_static._status`, a module global | `ensure_index` | inside it | `/api/bus-route` before its await (`backend/routes/buses.py:28`); `/api/status`; `/healthz` (`bus-route-index-failed`) |
| `bus_static._partial`, a module global | `_build_index_sync` | **from the worker thread** (`backend/bus_static.py:250-251`) | `/api/bus-route` after its await (`backend/routes/buses.py:41`); `/api/status` |
| One file per route, `DATA_DIR/cache/bus_routes/<route>.json`: 345 files of 5,462,783 bytes in one build, counted for this design on 2026-11-28 over the six public borough archives | `_process_zip`, in a thread (`backend/bus_static.py:300`) | **one file at a time**, each atomically (`backend/bus_static.py:169-176`) | one file per request, in a thread (`backend/routes/buses.py:39`) |
| `_manifest.json`, 2,260 bytes in that build | `_build_index_sync` | last | `ensure_index` at start only; no request reads it |

The module keeps nothing in memory but its two flags (`backend/bus_static.py:7-9`). A build
sweeps only `*.part` files (`backend/bus_static.py:225-226`) and no request consults the
manifest, so a route a newer publication drops keeps its old file until the disk is wiped, which
on Railway is each deploy (`backend/bus_static.py:41-42`). The 503 for a failed index reads
"restart the server to retry" (`backend/routes/buses.py:37`).

#### 2.4.4 LIRR and Metro-North, PATH, the ferry and AirTrain

Counts from `measure_structures.py` over the public archives downloaded for this design at 09:42:43
to 09:42:45 UTC
(LIRR `Last-Modified: Wed, 25 Nov 2026 22:10:39 GMT`; Metro-North `Thu, 26 Nov 2026 17:30:32 GMT`;
PATH `Fri, 06 Jun 2025 17:54:51 GMT`; ferry `Fri, 27 Nov 2026 06:25:01 GMT`), re-derived by
`verify_m4c.py` over fresh downloads with the same sha256.

| Archive | Structure | Built by | Assigned at | Read by |
| --- | --- | --- | --- | --- |
| LIRR and Metro-North (two archives, one warmup, one status) | `railroad_static`: system to the whole parse | `load_railroad_static`, both systems under one `gather` (`backend/railroad_static.py:593`) | `backend/warmups.py:249` | **no reader in app code** |
| | `railroad_stops`: LIRR 127, Metro-North 114; the same objects as `railroad_static[s]["stops"]` | the comprehension at `backend/warmups.py:252-254` | `:252` | the poll (`backend/pollers.py:727`), both passes of the decode (`backend/feeds/railroad.py:1320`, `:1352`); `/api/railroad-stops` (`backend/routes/railroad.py:107`); the arrivals gate and `stop_name` (`:151`) |
| | `railroad_routes`: LIRR 12, Metro-North 6 | `build_railroad_route_shapes`, between the assignments | `backend/warmups.py:255-262` | `/api/railroad-routes` (`backend/routes/railroad.py:81`) |
| | `railroad_station_routes` | `derive_railroad_stop_routes` | `backend/warmups.py:266-273` | `/api/railroad-stops` (`backend/routes/railroad.py:108`) |
| | `railroad_static_status`, one flag for both systems | `_set_static_status` | `backend/warmups.py:274` | the two static routes; `/api/status`; the monitor's `production:statics` |
| PATH | `path_static`: the whole parse | `load_path_static` | `backend/warmups.py:308` | **no reader in app code** |
| | `path_stops`: 13 parent stations; the same object as `path_static["stops"]` | the parse | `:309` | the poll's gate and argument (`backend/pollers.py:873-874`, `:889`), the decode (`backend/feeds/path.py:231`); `/api/path-stops` (`backend/routes/path.py:42`); the arrivals gate and name (`:114`) |
| | `path_routes`: 7 routes, 14 polylines | `build_path_route_shapes` | `:310-312` | `/api/path-routes` (`backend/routes/path.py:70`) |
| | `path_station_order`: 14 keys, lists of 2 to 9 stations | `build_path_station_order` | `:317-319` | the poll, **after** its await (`backend/pollers.py:933`), into the identity matcher |
| | `path_station_routes`, `path_static_status` | `derive_path_station_routes`; `_set_static_status` | `:323-325`; `:326` | `/api/path-stops` (`backend/routes/path.py:43`); routes, status, monitor |
| NYC Ferry | `ferry_static`: 50 stops, 485 trips, 7 routes | `load_ferry_static` | `backend/warmups.py:360` | the poll (`backend/pollers.py:993`): `trips` and `routes` taken at `backend/feeds/ferry.py:406-407` and joined per boat (`:179`) and per trip update (`:263`) on every poll |
| | `ferry_stops` (50; the same object as `ferry_static["stops"]`), `ferry_routes` (7), `ferry_station_routes` | the parse; `build_ferry_route_shapes`; `derive_ferry_stop_routes` | `:361`; `:362-364`; `:369-371` | `/api/ferry-stops` (`backend/routes/ferry.py:42`, `:43`); `/api/ferry-routes` (`:70`); the arrivals gate (`:109`) |
| | `ferry_static_status` | `_set_static_status` | `:372` | the poll gate (`backend/pollers.py:978`) |
| AirTrain | `airtrain`: 10 stations, 3 routes, from the committed `data/airtrain_jfk.json` | `airtrain_static.load_airtrain` | `backend/main.py:295`, synchronously in `lifespan`; no status, no task | `/api/airtrain` (`backend/routes/airtrain.py:23`); it changes only with a deploy |
| Every downloaded archive | `_ARCHIVE_STATUS["railroad_LIRR"]`, `["railroad_MNR"]`, `["path"]`, `["ferry"]` | `_record` | at each promotion, before the parse (`backend/static_shared.py:693`) | `/api/status` |

Each warmup's success path assigns in one step: every assignment of a group landed on one
event-loop tick (ferry tick 46, PATH tick 800, railroad tick 1617, `verify_m4c.py` with a ticker
coroutine). Only on the success path does no `await` fall between a load and its
assignments: `backend/warmups.py:242`, `:301` and `:353` each hold `attempt = await _fail_and_wait(...)` on the
empty-result branch, which continues before any assignment.

No railroad, PATH or ferry loader or decoder parses a calendar; AirTrain's fixture records its own
caveat, "GTFS calendar end_date 20211231; used as GEOMETRY and STATION + HEADWAY REFERENCE only"
(`data/airtrain_jfk.json`, its `_provenance` block, dropped at load).

**Tables in the source that were derived from an archive by hand.** A swap does not change them.

| Table | Where | What it states about an archive |
| --- | --- | --- |
| `SUBWAY_ROUTE_GROUPS`, 30 rows over 8 groups | `backend/feeds/subway.py:72-103` | which feed carries a route, written from a capture |
| `SUBWAY_ROUTES_ABSENT_FROM_ARCHIVE = frozenset({"SS"})` | `backend/feeds/subway.py:107` | a fact about `routes.txt`, which the app never reads |
| `_RAILROAD_DIRECTION = {0: "Outbound", 1: "Inbound"}` | `backend/feeds/railroad.py:747` | the agency's `direction_id` semantics; it keys the arrivals buckets |
| `_NYC_ANCHOR_LAT, _NYC_ANCHOR_LON = 40.752998, -73.977056` | `backend/feeds/railroad.py:755` | Metro-North stop 1's coordinates, equal today to the `stops.txt` row "Grand Central"; it drives direction inference |
| `_PATH_DIRECTION` | `backend/feeds/path.py:45-50` | "verified against static trips.txt across all 7 routes" |
| `RAIL_BRANCH_CODES`, 13 LIRR and 6 Metro-North names | `frontend/helpers.js:3043-3070` | keyed by the route NAMES `/api/railroad-routes` serves |
| The station id patterns | `backend/routes/railroad.py:29`, `backend/routes/path.py:27`, `backend/routes/ferry.py:28` | the id shape; 0 of 241, 0 of 13 and 0 of 50 of today's ids fail them |

#### 2.4.5 Readers that hold a reference across an await

Every static request handler reads its set in one synchronous stretch: no handler in
`backend/routes/subway.py`, `railroad.py`, `path.py`, `ferry.py`, `njt.py`, `airtrain.py` or
`status.py` contains an `await` (a grep counts 0 in each of those seven files at `16d1c19`; the AST
pass and both verifications agree). The pollers are where a swap would land mid-read. Each poll runs under `REFRESH_DEADLINE_S` = 45 s
(`backend/pollers.py:108`), one cycle every `POLL_INTERVAL_S` = 20 s (`:70`).

| Reader | Static taken before the await | The await | Used after it |
| --- | --- | --- | --- |
| NJ Transit poll, `_refresh_njt` | `njt_stops`, `njt_trips`, `njt_lines` as arguments (`backend/pollers.py:1122-1124`) | `await sender(NJT_TU_URL, {})` (`backend/feeds/njt.py:1067`), inside which `njt_post` awaits the token, the POST, and possibly a re-mint and a second POST (`backend/njt_auth.py:1432`, `:1455`) | the decode (`backend/feeds/njt.py:1068`); its products are written with no await between them: `njt_feed_health` (`backend/pollers.py:1191`), `njt_behind_run` (`:1203`), `feed_cache["njt"]` (`:1206`), `njt_arrivals` (`:1229`) |
| Subway poll, `_refresh_subways` | `subway_stops` (`backend/pollers.py:586`) | the only await of the poll, `:610`, reaching the `gather` (`backend/feeds/subway.py:481`) | the decode with the same reference (`backend/feeds/subway.py:494`, then `:219`, `:254`, `:288`, `:297`) |
| Railroad poll | `railroad_stops`, read once (`backend/pollers.py:727`) | the `gather` (`backend/feeds/railroad.py:1304`) | both passes (`:1320`, `:1352`): one generation per poll |
| PATH poll | `path_stops` (`backend/pollers.py:873`) | `client.get` (`backend/feeds/path.py:229`) | the decode with the held stops (`:231`); `path_station_order` is read **fresh** after the await (`backend/pollers.py:933`), so a swap during the fetch would decode against one generation and match against the next |
| Ferry poll | `ferry_static` (`backend/pollers.py:993`), its `trips` and `routes` (`backend/feeds/ferry.py:406-407`) | the `TaskGroup` (`:428-432`) | the decode (`:464-469`): one generation |
| `/api/bus-route/{id}` | `bus_static.status()` (`backend/routes/buses.py:28`) | the threaded file read (`:39`) | `bus_static.is_partial()` (`:41`): the two flags straddle the await |
| The subway warmup's own attempt | `stops` after `backend/warmups.py:173` | `:180`, `:186` | `stops` across both, `routes` and `stations` across `:186` |

#### 2.4.6 The mix, measured on the real poll

The real `pollers._refresh_njt` ran on an app warmed by the real warmup with generation A, the
committed `backend/tests/fixtures/njt_gtfs` (the 2026-09-01 trim: 164 served stations, 131
trips), and its capture `njt_tu.pb` decoded at its own clock 1788302206 (2026-09-01 22:36:46 UTC).
The poll was held where `fetch_njt_trains` awaits the network; a second real warmup assigned
generation B, the public `rail_data.zip` above (227 served stations); the poll was released.
Measured by `measure_njt_structures.py` part 3, re-derived count for count by `verify_m4a.py`
part E, which gated `njt_auth.njt_post` itself so the poll waited at
`backend/feeds/njt.py:1067`'s own await.

| Measure | Value |
| --- | --- |
| The poll's `stops`, `trips` and `lines` were generation A's objects | true, true, true |
| `app.state.njt_stops` after the poll | generation B's (227 stations) |
| `njt_arrivals` keys the poll wrote | 164, all generation A's ids |
| Of those, ids generation B also has | 144 |
| Of the 144, ids that name another station in generation B | **100** (44 name the same station in both); id 1, rows built for Aberdeen-Matawan, served beside B's "30th St. PHL."; id 8, Atco beside Asbury Park |
| `get_njt_arrivals` over the 164 keys | 144 answered with generation B's `stop_name` over generation A's rows; 20 refused 404, though their rows are in the index |
| Trains written to `feed_cache["njt"]` | 68; 60 carry a `stop_id` generation B has, and 50 of those a `stop_name` that is not B's name at that id |
| `njt_feed_health` published after the swap | joined 86, checked 86, `number_mismatches` 0: the decode's counts against A, published beside B |
| `njt_behind_run` | `None` |

The two generations are real publications 87 days apart, set together to make the mix visible;
they are not a production pair. The trains are consistent with A, whose realtime this is: the
mismatch is with the generation then served. The 2026-11-03 incident is the production shape of
the same mix (stop ids reassigned, Penn 109 to 105, `backend/routes/njt.py:161-164`).

#### 2.4.7 Live state keyed by static ids

Nothing resets any of this on an assignment of the static fields today; each lives until the next
successful poll writes it, or longer where the table says so. Writers re-read at `16d1c19` by the
verification: `njt_feed_health` only at `backend/pollers.py:1085`, `:1191` and the degrader's
`setattr` (`:217`); `njt_behind_run` only at `:1203`; `njt_arrivals` only at `:1229` and the prune
(`:452-473`).

| State | Keyed by, or carrying | What clears it today |
| --- | --- | --- |
| `njt_arrivals` | stop ids of the poll's held `njt_stops`; headsigns from the held trips or stop names | the next successful poll, whole; departed rows pruned in place (`backend/pollers.py:452-473`) |
| `feed_cache["njt"]` trains | `stop_id`, `stop_name`, coordinates from the held stops | the next successful poll; a failed poll keeps them |
| `njt_feed_health` | counts of the decode against the held `njt_trips` | the next decode |
| `njt_behind_run` | a `BehindRun`, "The first and the latest decode that schedule_behind held for with no decode in between that said otherwise" (`backend/feeds/njt.py:639-641`), the decodes run against the held `njt_trips` | ends only when a decode can judge (`checked` at least 5, `:609-613`) and fewer than one in four contradict; `/healthz` names it once a decode at least 600 s after the run's first still sees it, since a decode that cannot judge leaves `latest` where it was (`SCHEDULE_BEHIND_HOLD_S`, `:606`; `:659-664`, `:667-669`) |
| Subway trains, and a failed group's trains retained | coordinates and `stop_name` from `subway_stops` | the next decoding poll; a retained group up to `FEED_RETENTION_MAX_S` 600 s (`backend/cache.py:104`) |
| `subway_arrivals`, `subway_arrivals_by_system` | parent station ids filtered by `subway_stops` | replaced on success; retained per failed group up to 600 s |
| `subway_positions`, `railroad_positions` anchors | static stop ids and coordinates | **not the next poll**: `carry_forward_prev` keeps a train's anchor while its next stop is unchanged (`backend/feeds/shared.py:335`) and serves it as `prev_lat` and `prev_lon` (`:345-347`), so an older generation's coordinates survive successful polls until each train's next stop changes; how long that is was not measured |
| `subway_unmapped_routes` | route ids a decoding group served that `SUBWAY_ROUTE_GROUPS` lacks, the feed side (`backend/pollers.py:646-657`); no static table is read to build it | latched for the process; the static side is not stored: `_unmapped_subway_routes` re-derives it from `subway_station_routes` per call (`backend/routes/status.py:469-472`), under a docstring that reads "The index is fixed for the process" (`:462`) |
| `railroad_arrivals` | static stop ids; buckets by `direction_id` or by direction inferred from static coordinates | retained per failed system; departed rows pruned |
| `path_identity` | slots `(route_id, direction, stop_id)` (`backend/feeds/path.py:335-336`); the advance branch needs `path_station_order` | created once per process (`backend/main.py:247`) |
| `path_arrivals`, `path_feed_health["unresolved"]` | static station ids; entities whose stop ids `path_stops` lacks | the next poll |
| `ferry_arrivals` | buckets keyed by the static route's **long name** (`backend/feeds/ferry.py:263-266`), which the page sorts | the next poll |
| `_ARCHIVE_STATUS` | which archive was promoted, when, what it lacks | outlives the app (`backend/static_shared.py:451-453`) |

Not keyed by static: NJ Transit's alert selectors (raw `informed_entity` ids, joined only in the
browser), `TOKEN_CACHE`, and the bus vehicles (route from the realtime feed alone).

#### 2.4.8 The promotion is stamped before the generation is assigned

`last_promoted_at` is `time.time()` at the rename (`backend/static_shared.py:483`, from `:693`),
inside `staged_fetch`, before the load parses and before the warmup's block. Measured:

- **Subway: at least 894.6 ms.** Stamped at the promotion itself, one cold run on this machine
  gave 894.63 ms from the promotion to the first `app.state` write, with 4 event-loop iterations
  between (`verify_runtime.py`). `runtime_measure.py`'s 923.45 ms was measured from the end of
  `load_subway_stops`, and its cached arm promoted nothing. In production the subway file's mtime
  (2026-11-28 08:00:44.220 UTC) preceded its `last_promoted_at` (08:00:57.278 UTC) by 13.058 s,
  read at 09:43:38 and 10:15:36 UTC; the same gap was 0.833 s on this machine. What the 13 s
  were spent on is not measured.
- **LIRR: at least 9.176 s.** Production's stamps read LIRR 08:00:46.056 and Metro-North
  08:00:55.232 UTC (read at 09:58:32 and 10:25:32 UTC). Metro-North's stamp is written inside its
  own `_load_one`, before its parse, and the railroad warmup assigns both systems only after the
  `gather` returns (`backend/railroad_static.py:593`), so LIRR's structures went live no earlier
  than 9.176 s after LIRR's stamp.
- **Railway reads success before then.** Deployment record 7988955096 reads `success` at
  08:00:51 and 08:00:54 UTC (`gh api .../deployments/7988955096/statuses`; record:658), before Metro-North's stamp.
- **When a later step raises.** The subway's other loaders run after the promotion, so a raise
  there leaves the new file on disk and the old structures in memory. On the parse-class arm of
  `load_subway_station_routes` and `load_subway_station_complexes` (`backend/static_data.py:667`,
  `:771`, under `_discarding_an_unreadable_cache`, `:291-300`), the file is unlinked:
  `static_subway_gtfs` reads null, `last_promoted_at` stays, and the next attempt downloads again.
  That arm was read from the code, not reproduced. Whether a timed-out attempt's worker thread,
  still reading the module path, can overlap the next attempt's promotion was not reproduced
  either.

#### 2.4.9 The block is atomic only when every builder returns

NJ Transit's builders sit after the warmup's `try` (`backend/warmups.py:415-433` against
`:448-473`), between the assignments. Measured on a second warm over generation B after A, with
one builder made to raise (`measure_njt_structures.py` part 4; `verify_m4a.py` part F with the
real loader):

| Builder raising | Left new | Left old | Status, task |
| --- | --- | --- | --- |
| `build_njt_route_shapes` | `njt_static`, `njt_station_routes`, `njt_stops` | `njt_routes`, `njt_trips`, `njt_lines`, `njt_stop_schedule` | `ready`; task done with `ValueError` |
| `build_njt_trip_index` | `njt_static`, `njt_station_routes`, `njt_stops`, `njt_routes` | `njt_trips`, `njt_lines`, `njt_stop_schedule` | `ready` |

The other NJ Transit builders were not measured. On a cold start the same raise leaves the status
`loading` (F09, audit-2026-09-05.md:431). The railroad, PATH and ferry blocks
have the same shape by code, builders between assignments (`backend/warmups.py:249-273`,
`:308-325`, `:360-371`); no raise there was measured. Of the five warmups in `backend/warmups.py`,
only the subway's assigns values its `try` already built (`:193-198`).

#### 2.4.10 The browser's tables

Every page table is filled once, by the first usable answer of its loader (2.5.1), and never
rebuilt.

| System | Page-held tables | Filled from | Declared and filled at |
| --- | --- | --- | --- |
| NJ Transit | `njtStopCoords`, a `const` Map, never cleared | `/api/njt-stops` | `frontend/systems/njt.js:54`, set per station `:162` |
| NJ Transit | `njtRouteColors`, `njtRoutePaints`, `njtRouteShortNames`, `njtRouteNames`, `njtRouteIndex`, reassigned together from one payload | `/api/njt-routes` | `frontend/systems/njt.js:37-47`, `:82-86` |
| Subway | `routeIndex`; the complex index | `/api/subway-routes`; `/api/subway-stops` | `frontend/systems/subway.js:220`, `:246`; `:506` |
| Railroads | `railroadRouteIndex`, `railroadRouteNames`, `railroadRouteColors` | `/api/railroad-routes` | `frontend/systems/railroad.js:7`, `:10`, `:24`, set `:52` |
| PATH | `pathRouteColors`, `pathRouteNames`, `pathRouteIndex` | `/api/path-routes` | `frontend/systems/path.js:9`, `:10`, `:17` |
| Ferry | `ferryRouteColors`, `ferryRouteNames` | `/api/ferry-routes` | `frontend/systems/ferry.js:13`, `:14` |
| Every station system | `stationRegistry`, **append-only**; one arrivals URL per station, built once from its id | each `*-stops` payload | `frontend/systems/shared.js:2808`, push at `:2816`; `frontend/systems/njt.js:166` |

The live records (`njtTrainRecords`, `frontend/systems/njt.js:445`; `railroads`,
`frontend/systems/railroad.js:514`; `pathTrainRecords`, `frontend/systems/path.js:265`;
`ferryBoatRecords`, `frontend/systems/ferry.js:269`) are keyed by realtime ids and glide along the
page-held geometry and coordinates. One path reads past the HTTP cache: `fetchRoutesPayload`
(`frontend/systems/shared.js:2156`) reads a routes payload once more with `cache: "reload"` when
no entry carries the field the caller needs (`:2169`, `:2174`), and only inside the first load.
Its comment records the lack this design meets: "A FOLLOW-UP, NOT A FIX: a version stamp on the
static-derived endpoints would let the frontend ASK whether the payload predates the field"
(`:2147-2149`). No served static payload carries a generation or version key: the key sets of the
five railroad, PATH and ferry static payloads read at 09:58:32 UTC are exactly their models'
fields, and `backend/models.py` defines no such field for any static endpoint or live envelope.

### 2.5 The page's half

#### 2.5.1 What the page requests, and when

The page's scripts (`frontend/index.html:565-588`) hold 15 `fetch(` call sites and 24 distinct
`/api/` path literals (`inventory.sh`, a grep over the loaded scripts; 15 recounted per script by
the verification). They fall in four classes.

| Class | Endpoints | When | Stop rule |
| --- | --- | --- | --- |
| Static, at boot | 11: `/api/subway-routes`, `/api/subway-stops`, `/api/railroad-routes`, `/api/railroad-stops`, `/api/airtrain`, `/api/path-routes`, `/api/path-stops`, `/api/ferry-routes`, `/api/ferry-stops`, `/api/njt-routes`, `/api/njt-stops` | once each, started by `retryUntil` (`frontend/map.js:281-291`) | `retryUntil` (`frontend/helpers.js:5471-5484`) returns for good on the first truthy result (`:5480`); otherwise it doubles its wait from 1,000 ms to 30,000 ms (`frontend/map.js:16-17`, `frontend/helpers.js:5482`) with no attempt cap. Each fetch is bounded by `FETCH_DEADLINE_MS` 15,000 (`frontend/helpers.js:1702`) |
| Static, on a rider's action | `/api/bus-route/{route_id}` | each bus popup open (`frontend/systems/buses.js:279`) | not retried; a failure leaves a note for 60 s |
| Live, keyed by a page-held static id | the five `*-arrivals/{id}` | on a popup or panel open, then every 15 s while open | URL built once at load from the payload's id |
| Live, polled | `/api/buses`, `/api/subways`, `/api/railroads`, `/api/path`, `/api/ferry`, `/api/njt-trains`; `/api/alerts` | every 15 s (`POLL_INTERVAL_MS`, `frontend/map.js:7`); alerts every 60 s (`:11`) | none |

After the first usable answer nothing calls a static loader again: the page has no
`visibilitychange`, `pageshow`, `online`, service worker, `location.reload` or `history.go` path,
and `localStorage` holds only the theme (`inventory.sh`; re-read by the verification). The page
never fetches `/api/status`.

#### 2.5.2 Cache headers on the twelve static-derived endpoints

All twelve were read in production on 2026-11-28 by read-only GETs.

| Endpoint | `Cache-Control` | Read at (UTC) | Set by |
| --- | --- | --- | --- |
| `/api/njt-stops` | `public, max-age=3600` | 09:47:02 to 09:47:03; 09:59:41 to 09:59:42; 10:26:17 | `backend/cache.py:373`, from `backend/routes/njt.py:77` |
| `/api/njt-routes` | `public, max-age=3600` | 09:47:02 to 09:47:03; 09:59:41 to 09:59:42 | the same, from `backend/routes/njt.py:122` |
| `/api/subway-stops` | `public, max-age=3600` | 09:43:38 to 09:46:55; 09:59:41 to 09:59:42; 10:15:36 to 10:15:37 | from `backend/routes/subway.py:55` |
| `/api/subway-routes` | `public, max-age=3600` | 09:43:38 to 09:46:55; 10:15:36 to 10:15:37 | from `backend/routes/subway.py:43` |
| `/api/railroad-stops` | `public, max-age=3600` | 09:58:32 to 09:58:33; 10:25:32 | from `backend/routes/railroad.py:105` |
| `/api/railroad-routes` | `public, max-age=3600` | 09:58:32 to 09:58:33; 10:26:17 | from `backend/routes/railroad.py:79` |
| `/api/path-stops` | `public, max-age=3600` | 09:58:32 to 09:58:33 | from `backend/routes/path.py:40` |
| `/api/path-routes` | `public, max-age=3600` | 12:50:27 | from `backend/routes/path.py:68`; `no-cache` when a ready list is empty (`:71-72`) |
| `/api/ferry-stops` | `public, max-age=3600` | 09:58:32 to 09:58:33; 10:25:32 | from `backend/routes/ferry.py:40` |
| `/api/ferry-routes` | `public, max-age=3600` | 09:58:32 to 09:58:33 | from `backend/routes/ferry.py:68`; the same empty-list override (`:71-72`) |
| `/api/airtrain` | `public, max-age=3600` | 09:59:41 to 09:59:42 | `backend/routes/airtrain.py:22` |
| `/api/bus-route/M15` | **none** | 09:43:38 to 09:46:55; 09:59:41 to 09:59:42; 10:26:18 | none: `backend/routes/buses.py:24-48` sets no header |

Each time is the span of the batch of read-only GETs that made the read, by its own record. No
backend API code sets `ETag` or `Last-Modified` (a grep finds them only in the docstring at `backend/main.py:462-463`), and of the
seven endpoints whose reads recorded those headers (`/api/njt-stops`, `/api/njt-routes`,
`/api/subway-stops`, `/api/airtrain`, `/api/bus-route/M15`, `/api/railroad-routes`,
`/api/path-routes`), none carried either. For contrast, the live envelopes are `no-store`
(`backend/cache.py:300`), and the page's own scripts are `no-cache` with an ETag (`backend/main.py:469`), so a reload re-checks the code
while it may reuse the payloads. The code records the consequence for the subway:
"/api/subway-stops is served max-age=3600 while the scripts are revalidated, so a returning rider
runs this code on yesterday's payload" (`frontend/helpers.js:360-362`). Whether a given browser's
reload re-requests a fresh `max-age` payload that has no validator, and whether any browser stores
`/api/bus-route` heuristically, are not measured.

#### 2.5.3 Where the page joins live rows to its tables

No live row is joined on the page by trip id: trip ids key only the page's own marker records.
Destination, train number and next stop name are joined on the server and read straight off the
live row. The page joins three ways:

- **By route id**, in every system with a feed: geometry for the glide (`computeRouteSlice`),
  names, colours and branch codes. A miss draws a straight line, a fallback colour and the route
  id as its code (for example NJ Transit, `frontend/systems/njt.js:483`).
- **By NJ Transit stop id**, three ways: the glide toward `njtStopCoords`
  (`frontend/systems/njt.js:478`, lookup `frontend/helpers.js:4787-4788`; a miss means no glide);
  the "Also here" crosslink keyed `NJT|<id>` (`frontend/systems/njt.js:377`, lookup
  `frontend/systems/shared.js:2566`); and the station-alert join below. An NJ Transit board's
  title and route marks are the page-held `station.name` and `station.routes`
  (`frontend/helpers.js:5011-5012`); the body's own `stop_name` (`backend/routes/njt.py:188`) is
  not read.
- **The station-alert join**, keyed `system|stationId` (`matchStationAlerts`,
  `frontend/helpers.js:5229-5235`, the key at `:5231`; entry `stationAlerts`, `:5333`), for every
  board with an alerts feed: subway (`frontend/systems/subway.js:582`), LIRR and Metro-North
  (`frontend/systems/railroad.js:242`), ferry (`frontend/systems/ferry.js:116`) and NJ Transit
  (`frontend/systems/njt.js:199`). PATH and AirTrain have no alerts feed. So NJ Transit's live
  alerts land on whichever station the tab holds under the id they name.

The page reads no `stop_code` anywhere (a grep of its scripts).

#### 2.5.4 What an open tab shows across a republication

The tab keeps the tables of 2.4.10 for its life. The witness
`docs/reviews/audit-2026-10-12/a06_frontend_static_once.mjs`, run at `16d1c19` under Node v26.0.0 four times on 2026-11-28, exit 0 each (two of the outputs, compared by `diff` with the timing line excluded, were identical),
boots the production page in a `node:vm` and prints: all **11 of 11** static loaders retried through 3 refusals and took 1
usable answer each, then made **0** further static requests across 25 simulated hours, while the
backend restarted into a changed, announced publication at t = 360 s, the tab was hidden and came
back 13 times and the page polled `/api/subways` 6,001 times; **0** of the changed coordinates
reached the map. It never opens a bus popup.

What such a tab then shows, reasoned from the code of 2.5.3:

- An id the backend no longer serves keeps its marker and panel entry; opening its board gets the
  404 detail, for NJ Transit `Unknown NJ Transit stop {id}.` (`backend/routes/njt.py:181`).
- An id that now names another station opens that station's departures under the page-held name
  and route marks; an NJ Transit train heading for it glides toward the old coordinates; its
  crosslink names the old station; an alert naming it lands on the old station.
- An id the page lacks has no marker, no panel entry and no board, and an NJ Transit train heading
  for it does not glide.

The ledger counts this for 2026-11-03's renumbering: "Of the 172 ids production serves from the
2026-11-01 archive, 101 name another station in the 2026-11-06 publication and 22 are gone, and of
the 49 that still name their station one, Lyndhurst (75), sits 285 m from where the old table
puts it" (ledger:610; its two code citations are at an
older commit: at `16d1c19` the header is `backend/cache.py:373` and the NJ Transit loaders start at
`frontend/map.js:290-291`). For 2026-11-27's publication it is not measured whether any stop id a
tab held moved, beyond New York Penn at 105 and Newark Penn at 107 in both (record:589); the trip ids that moved are joined only on the server.

#### 2.5.5 Where a version stamp was owed

| Record | Line at `16d1c19` | Its words | What it owes the page |
| --- | --- | --- | --- |
| 2026-09-05, F06 remedy | audit-2026-09-05.md:204 | "Coordinate frontend static-data reloading: a loader that stops permanently after the first partial nonempty response will otherwise miss repaired data." | the reload, not a stamp |
| 2026-09-05, F06 acceptance | `:206` | "its stations/routes appear in an already-open browser without a manual reload" | discovery in an open tab |
| 2026-09-05, F07 remedy | `:216` | "Report snapshot version, loaded time, valid-through date and next refresh. Let clients detect changed static versions and update their registries safely." | a version clients can detect |
| 2026-10-12, A06 repair | ledger:267 | "generation/version identifiers and atomic snapshot swaps ... ensure browsers discover changed static generations" | identifiers, and discovery |
| 2026-10-12, release table, row 2 | `:375` | "generation-aware frontend reload" | a reload keyed by generation |
| MR3, the operator's ruling R-d | `docs/reviews/map-redesign-rounds.md:839-840` | "Record a version stamp on static-derived endpoints as a follow-up"; "so the frontend can ASK whether a payload predates a field instead of inferring it from absence" | a stamp, recorded as a follow-up; carried in the code at `frontend/systems/shared.js:2147-2149` |

F06's lines name no version, generation or stamp; F06 owes the reload. Today no static endpoint
and no live envelope carries a generation or version field (`backend/models.py` has none; the
static endpoints return bare lists, `backend/routes/njt.py:87`, `:124`). The nearest is each
archive's `last_promoted_at` under `static_archives` on `/api/status` (`backend/models.py:1005`,
inside `StaticArchiveStatus` at `:993`), which the page never fetches, and which 2.4.8 shows leads
the in-memory generation.

### 2.6 Two witnesses the feed gives

#### 2.6a The MISS share

PR #145's open note asks for this one: "A schedule behind by misses alone raises nothing on
`/healthz`; the monitor WARNs. On the live list." (PR #145's body, read with
`gh pr view 145 --json body` for this design on 2026-11-28.) A **miss** is a running trip update,
or a served train, whose trip id the held schedule lacks. A joined one has its trip id in the held
schedule; after a renumbering it is a **collision** when the held trip names another train, which
the decoder serves as `identity: "refuted"`. Counted for this design on
2026-11-28 by `miss_share.py`, and again with no app code by `recount.py`.

| Shape | What was compared | Misses | Joined (refuted: a collision) | ADDED among the misses | Source |
| --- | --- | --- | --- | --- | --- |
| 2026-11-03 (stop ids, and trip ids from 7 digits to 1 to 4), stand-in | the 50 sampled realtime trip ids of 2026-11-22 against the 131 seven-digit ids of `backend/tests/fixtures/njt_gtfs` | **50 of 50** | 0 of 50 | 0: every id non-empty | `miss_share.py`, `recount.py` |
| the same, the app's decode of those 50 | `decode_njt_trip_updates` | joined 0, checked 0 | mismatches 0; `behind_run(None, report)` returns `None` | | `miss_share.py` |
| the same, production | the trains on `/api/njt-trains` at 2026-11-07 00:19 UTC (19:19 Eastern on 11-06) on the new ids, the held archive on 7 digits | **56 of 56**, train level | 0 | **0**: an ADDED train is served under `njt:<entity.id>` (`backend/feeds/njt.py:853-858`; the same at `ef98328`, the code of that day), and all 56 matched the new `trips.txt` on trip_id | PR #137's body; the read's time at ledger:653 |
| 2026-11-22 (trip ids renumbered 1 to N) | 50 distinct trips sampled 23:12 to 23:26 UTC | **8 of 50 (16.0%)**, all route 5, every id non-empty | 42 of 50, every one refuted | 0 | `backend/tests/fixtures/njt_drift_2026-11-22.json`; ledger:589; asserted at `backend/tests/test_njt_drift_incident.py:29` |
| the same, per poll | `synthesized` of `trains` over 30 polls, 23:12:12 to 23:26:47 UTC | 5 of 37 (13.5%) to 6 of 36 (16.7%); pooled 174 of 1,116 (15.6%) | 30 to 34 placed joined trains a poll | not separable | that fixture's `before.series` |
| 2026-11-27 (trip ids again) | Claude's wire read at 20:24:02 UTC (15:24:02 Eastern) | **3 of 34 trains (8.8%)** | 31 of 34: 25 refuted, 6 not refuted (no `identity` key) | **cannot be told**: the record lists none of the 3 ids | record:589 |
| Steady state, the committed pair | `njt_tu.pb` (2026-09-01 22:36:46 UTC) against `njt_gtfs` | 4 of 90 running (4.4%) | 86 of 90 | **4 of 4**, each with an empty trip id | `miss_share.py`, `recount.py` |
| Steady state, a recorded live capture | 164 trip updates against the full live static; CANCELED is in this denominator | 36 of 164 (22.0%) | 128 | **36 of 36** | `backend/scripts/gen_njt_rt_fixture.py:168-172` |
| Steady state, production | trains carrying `identity: "unjoined"` | 0 of 29 (2026-11-22 23:51:29 UTC), 0 of 73 (2026-11-24 00:00:30 UTC), 0 of 2 (2026-11-28 08:13:57 UTC), 0 of 6 (10:02:33 UTC), 0 of 8 (10:29:01 UTC) | all | | ledger:589; record:589; GETs for this design |

The 0 of 29 ran `55065d0`'s code, which had no identity mark; it rests on "no synthesized name"
(ledger:589), since that code made a headsign only when the join gave
none.

**Non-ADDED misses against the process's own current schedule: 0 in every steady-state
measurement found.** The committed pair's 0 is a measurement, not an artifact of the trim: the
trim adds a capture's trip ids only when the full publication of the same run has them
(`backend/scripts/njt_fixture_trim.py:290`), so all 108 non-empty trip ids of the 2026-09-01
capture named a trip in the full live publication. One selection effect is real: the generator
refuses a capture whose scheduled and cancelled trips join under 0.95
(`MIN_LIVE_JOIN_RATE`, `backend/scripts/gen_njt_rt_fixture.py:177`), so no committed capture can
show a stale miss share of 5% or more.

**ADDED is told apart by the id.** Every ADDED trip seen carries an empty trip id (40 of 40: 4 in
the committed capture, 36 in the recorded one), and the decoder keys it `njt:<entity.id>`
(`backend/feeds/njt.py:853-858`), so a served ADDED train carries an `njt:` id and a stale miss a
numeric one. The code names one other legitimate miss: "a publication boundary crossed between the
two downloads would show as a handful of misses" (`backend/scripts/gen_njt_rt_fixture.py:174-175`).

**On the 2026-11-03 shape the mismatch rule has nothing to count.** A trip id of 1 to 4 digits
cannot equal one of 7, so no collision is possible while every held id has 7 digits; every one of
the 3,301 ids of the 2026-11-27 publication is absent from `njt_gtfs` (`miss_share.py`). The
decode joins 0 and checks 0, so `njt-schedule-behind` cannot rise.

**No miss count is served today.**

- The decode forms no running total: `joined = checked = 0` (`backend/feeds/njt.py:930`), each
  trip update adds its `joined` and `checked` (`:951-952`), and its `IdentityReport` holds only
  `joined`, `checked` and `warnings` (`:1004`).
- `njt_feeds` on `/api/status` carries `total`, which is "1: NJ Transit's trip updates are one
  feed" (`backend/models.py:885`), and `joined`, "running trip updates whose trip_id is in the
  schedule" (`:898`); there is no running count beside it. Production at 10:02:32 UTC served
  joined 11, checked 11, and nothing saying how many trip updates were running.
- The only trace is per row, `identity: "unjoined"` (`backend/feeds/njt.py:883`), on placed trains
  and board rows only.
- The log is silent: "The join miss is NOT a warning" (`backend/feeds/njt.py:769-770`), and
  `log_cross_check` returns when there are no warnings (`:1079`).
- `/healthz` is blind to it by its own comment: "WHAT IT CANNOT SEE is a schedule behind by
  misses alone" (`backend/models.py:1348-1351`).
- The monitor never FAILs on misses alone. With production checking fewer than 5,
  `production:njt-schedule` WARNs "too few to judge, beside N this run checked against the schedule
  it downloaded" only when the run's own decode checked at least 20 (`NJT_MIN_TRIPS_FOR_RATIO`,
  `backend/scripts/contract_monitor.py:653`, the WARN at `:3606-3612`), and otherwise reads "too
  few to judge" (`:3613`). A partial miss is not seen at all: with 5 or more checked and fewer than
  one in four contradicting, the line reads in step whatever share missed (`:3602`). Across the
  eleven scheduled runs 471 to 481 the WARN arm never printed, and "too few to judge" printed on
  run 471 (databaseId 45133919291, created 2026-11-23 07:49:36 UTC), run 477 (45783913934,
  2026-11-26 08:31:39 UTC) and run 479 (45967989070, 2026-11-27 08:44:18 UTC). Run 477's
  production joined 0 beside 21 trip updates in the run's own fetch; why is recorded nowhere.

Unmeasured: any realtime capture from 2026-11-03 to 2026-11-07 (the first row is a stand-in);
the trip-id sets of the schedules production held on 2026-11-12 and 2026-11-23, so the
trip-update-level miss share of the 2026-11-22 and 2026-11-27 shapes; the steady-state ADDED share
beyond the counts above, over days and disruptions.

#### 2.6b Implied speed under the held stop table

PR #145's open note names the other gap: "A republication that moves the STOP ids too
(2026-11-03's). The last stop is named through the held table, so a refuted or unjoined train
reads another station's name" (PR #145's body, line 134). The code says the same: "Where NJ
Transit renumbers its STOPS too, as it did on 2026-11-03, the name is another station's"
(`backend/feeds/njt.py:690-692`).

**The witness.** For each pair of consecutive calls in a trip update, the great-circle distance
between the two stops under the held table's coordinates, divided by the time between them
(predicted departure at the first, predicted arrival at the second), in km/h. It reads stop ids
and times only, and needs no route id and no trip join. A great circle is no longer than the
track, so every implied speed is a lower bound on the average speed between the calls. Measured
for this design on 2026-11-28 by `speed_witness.py`, re-derived to the printed precision with no
app code by `verify_speed.py`, `verify_express.py`, `verify_extra.py`, `verify_own_sched.py` and
`verify_kept_pairs.py`. The decoder's rules are mirrored (CANCELED and DELETED trips dropped,
SKIPPED and NO_DATA calls dropped, calls in `stop_sequence` order), with one difference on
purpose: the decoder drops a stop id the table lacks before ordering
(`backend/feeds/njt.py:249-250`), and the witness keeps it so that a pair touching it counts as
absent rather than bridged.

**The inputs.** Old numbering: `backend/tests/fixtures/njt_gtfs` (164 stops) and `njt_tu.pb`
(2026-09-01 22:36:46 UTC, 18:36:46 Eastern, a weekday rush: 90 trips kept, 1,231 calls). New
numbering: the public `rail_data.zip` of 2026-11-27, its 164 rail rows, which equal production's
`/api/njt-stops` read at 10:08:16 and 10:37:18 UTC by id, by place (0.0 m apart at most) and by
case-folded name. **No realtime on the new numbering with per-call times exists in the
repository**, so the two new-realtime arms are stand-ins: C1 and D1 replay the 2026-09-01
predictions with every stop id carried into the new numbering by a station join (nearest of the
164 new rail rows, ties at Secaucus's two levels broken by shared route names: 164 to 164, 159
within 1 m, 49 kept their id and 115 moved, the counts the 2026-11-06 pull gave: 164, 49 and 115
at ledger:653, and 159 within 1 m at `backend/scripts/contract_monitor.py:468-469`); C2 and D2 use the new publication's own scheduled
stop_times (1,387 rail trips). Capturing real new-numbering realtime takes a mint.

**Per snapshot, per pair.** Right means the realtime read under its own table; wrong means under
the other.

| Arm | Pairs with a speed | Median km/h | Over 160 km/h | Over 400 km/h | Calls absent from the table (distinct ids; trips touched) |
| --- | ---: | ---: | --- | --- | --- |
| A: old realtime, old table (right) | 1,130 | 54.9 | 14 (1.2%) | 4 (0.4%) | 0 |
| B: old realtime, new table (wrong) | 917 | 406.9 | 682 (74.4%) | 463 (50.5%) | 114 (20; 69 of 90) |
| C1: new-id stand-in, new table (right) | 1,130 | 54.4 | 14 (1.2%) | 4 (0.4%) | 0 |
| D1: new-id stand-in, old table (wrong; **2026-11-03's shape**) | 768 | 430.5 | 603 (78.5%) | 407 (53.0%) | 215 (20; 85 of 90) |
| C2: new schedule, new table (right) | 16,074 | 43.9 | 0 | 0 | 0 |
| D2: new schedule, old table (wrong) | 11,029 | 385.7 | 8,449 (76.6%) | 5,383 (48.8%) | 3,021 (20; 1,151 of 1,387) |

Each realtime arm is one snapshot: the gap is measured on one feed per arm, not on a distribution
of feeds.

**Per trip, no threshold separates the realtime arms without overlap.** Arm A has 9 of 90 trips
with a pair above 160 km/h and 3 above 400; its largest trip maximum, 4,343.7 km/h, is above the
wrong arms' smallest. Arm B has 3 of 90 trips at or under 160: the Princeton shuttle, trains 4168,
4169 and 4170, all at 110.3 km/h, because old ids 131 and 132 are Ridgewood and River Edge in the
new table, 9.2 km apart where the real stations are 4.13 km apart. In D1, the incident's shape,
all 86 measured trips are above 250 km/h (smallest maximum 256.1) and the other 4 have no
measurable pair, since their calls alternate between present and absent ids; with absent calls
dropped before pairing, as the decoder drops them, 2 of 90 D1 trips are at or under 160 (87.4 and
91.7 km/h). The overlap depends on the timing taken: arrival to arrival, arm A's maximum is 316.0
km/h, with 3 trips above 160 and none above 400. Under the static schedule's times the arms
separate: 0 of 1,387 right trips above 160 (C2's largest maximum 116.0 km/h) against 1,360 of 1,360
measured wrong trips above 200 (D2's smallest maximum 211.9). Under the trip updates' own
`scheduled_time`, 2 of 90 right trips (3267 and 3272) are above 400.

**The right arm's tail, named.** The 14 pairs of arm A above 160 km/h: 9 on five late Gladstone
Branch trains, where the predicted delay falls between consecutive stations faster than a train
can recover it (train 0435, Murray Hill departure 22:51:32 UTC with 302 s of delay to Berkeley
Heights arrival 22:51:35 UTC with 110 s, 3 s for 3,620 m, 4,343.7 km/h, against a scheduled 3 min
15 s); 2 on NJCL trains 3267 and 3272 at calls their own schedule does not list; and 3 on two
more late trains where the predicted delay falls between consecutive stations the same way: NJCL
train 3363 twice (Spring Lake departure with 2,701 s of delay to Manasquan arrival with 2,551 s,
45 s against a scheduled 3 min 15 s, 279.3 km/h; Hazlet with 3,192 s to Middletown with 3,031 s,
139 s against a scheduled 5 min, 178.8 km/h) and Bergen County Line train 1169 (Waldwick with
561 s to Allendale with 462 s, 36 s against a scheduled 2 min 15 s, 211.7 km/h), by
`dump_trips.py` on entities 3363 and 1169 (routes 10 and 5 of the fixture's `routes.txt`).

**Absent stop ids are the sharper signal.** Zero in every right arm (0 of 1,231 realtime calls,
0 of 17,461 and 0 of 1,722 schedule calls); 20 distinct absent ids in every wrong arm, on 69 of 90
(B), 85 of 90 (D1), 1,151 of 1,387 (D2) and 104 of 131 (the old schedule under the new table)
trips; and 71 to 150 absent calls under every small shift of the ids (below). Combined at trip
level: in D1, 90 of 90 trips show a pair above 160 km/h or an absent id; in B, 87 of 90, the
Princeton shuttle showing neither; in the right arms, 9 of 90 show a pair above 160 and none an
absent id. Today the decoder drops such a call without a count (`backend/feeds/njt.py:249-250`).

**Express trains.** Nothing marks an express by name: no headsign of the fixture's 131 trips or
the public file's 3,301, and no route row, contains "EXP" or "EXPRESS". By the network (a pair is
express when some rail trip of the current publication calls at both with a call between), 60 of
the 90 realtime trips are express, with 102 of their 1,130 pairs. Every right-arm pair above 160
km/h is on an express trip, but 12 of the 14 are local pairs on late trains and the other 2 are
express pairs at calls the trip's own schedule does not list. Trips with no express pair never
exceed 137.1 km/h (A) or 111.2 km/h (C2); the schedule arms' express pairs top out at 110.1 km/h
(C2) and 114.4 km/h (the old schedule). The 25 kept realtime calls on 7 joined trips that their
own stop_times do not list fall on NJCL 3267 (6), 3272 (5) and 3275 (1), NEC 3875 (1), Raritan
Valley 5435 (5) and Morris and Essex 6651 (6) and 6655 (1).

**Why ids are not neighbours.** NJ Transit numbers stations in alphabetical order: 157 of the 163
numerically adjacent old ids ascend by station name, and 146 of the 150 adjacent new rail ids
under 1000 do. Between the station at id n and the one at n plus k, for every k from minus 16 to
plus 16, the median distance is 36.5 to 45.4 km, and at most 8 of the 141 to 153 pairs per k are
under 5 km; the one 0 m pair is Secaucus's two levels, 144 and 145. So a shift of every id by k
reads like a renumbering: 80.8% to 91.9% of pairs above 160 km/h, and 0 to 3 trips at or under
160, over all 32 shifts. A moved id looked up in the old table names another station in 95 of 115
cases, 3.37 to 141.46 km from the right one (median 36.18 km, none under 2 km), and nothing in 20.

**What it does not read.** It reads the stop table, not the trip table: 2026-11-12's and
2026-11-27's incidents moved trip ids. The join reproduces the 2026-11-06 move counts class by
class between the 2026-09-01 capture and the 2026-11-27 file, which is consistent with no stop
renumbering since 2026-11-06; that is an inference from counts, since the held tables are not
public. Pairs between two kept ids read right under either table (67 of B's pairs, none above 160
km/h), and under the wrong table the last call names the right station on 23 of 90 trips and
another station on 63 (B) and 62 (D1). A short trip between two ids that become two nearby
stations reads below every threshold.

### 2.7 The token budget during a refresh

#### 2.7.1 One token for three callers

One `TokenCache` per process, `TOKEN_CACHE` (`backend/njt_auth.py:1348`), which `njt_post` uses
whenever no cache is passed (`:1417`). The three production callers pass none:

| Caller | Call | Cadence and deadline |
| --- | --- | --- |
| The trains poller | `njt_auth.njt_post(NJT_TU_URL, {})` (`backend/feeds/njt.py:1066-1067`) | every 20 s (`backend/pollers.py:70`), under 45 s (`:108`), only while `njt_static_status` is `ready` (`:1095`) |
| The alerts poller | `njt_auth.njt_post(url, {})` (`backend/feeds/alerts.py:537`) | every 60 s, under `ALERT_FEED_DEADLINE_S` 20 s (`:384`, applied at `:529`) |
| The static download | `njt_auth.njt_post(url, {})` (`backend/njt_static.py:773`) | once per warmup attempt that finds its cache not fresh (`backend/njt_static.py:1275-1276`), under `asyncio.timeout(deadline_s)` (`:772`), `DOWNLOAD_DEADLINE_S` 120 s (`backend/static_shared.py:523`); so once per process today when the first attempt succeeds, while a failed download with no usable cache returns `{}` (`backend/njt_static.py:1288-1291`) and the warmup posts again on its rungs (`backend/warmups.py:434-441`) |

A token is reused until `MAX_TOKEN_AGE_S` = 12 h (`backend/njt_auth.py:302`, checked lazily at
`:1031`). A process sends at most `DAILY_MINT_ALLOWANCE` = 4 getToken POSTs per Eastern day
(`:219`), against the account's 10 (`DAILY_MINT_LIMIT`, `:183`). `njt_post` calls `get()` at most
twice, with no loop (`:1432`, `:1455`), so one attempt sends at most 2. **While a hold runs,
`get()` serves the held token whatever its age** (`:1087-1098`, `:1252-1256`), so an over-age
token does not always mean a mint: during a ladder, allowance or quota hold the static download
posts the over-age token and sends nothing to getToken. On a cold start the NJ Transit alerts
fetch, started beside the warmup (`backend/main.py:354`), races the warmup's
download to `TokenCache.get`; its lock means one mint either way, and which caller sends it is not
measured.

**No refresh exists today, and the loader re-run is not one.** `load_njt_static` downloads only
when its cache is not fresh (`backend/njt_static.py:1275-1276`), so called again inside
`MAX_AGE_DAYS` it sent 0 getToken and 0 getGTFS POSTs (`m9_token_during_refresh.py`, case R0).

#### 2.7.2 What a refresh would send, by case

Measured for this design on 2026-11-28 by two witnesses against a local fake token server on
127.0.0.1, each running the real `njt_post`, `TokenCache`, `_httpx_post` over a real socket,
`_download_via_token`, `_download_zip` and `staged_fetch`: `m9_token_during_refresh.py`, over the
committed fixture directory zipped by the script (79,091 bytes), and `v9_witness.py`, through the
unwrapped shipped chain with the real 3,295,223-byte public archive as the payload. Cases named R,
T and E are `m9_token_during_refresh.py`'s; cases named V are `v9_witness.py`'s. Each world first warms the cache as a
process hours in has it: one cold mint, accepted by one poll. The fake is a model of a server,
not NJ Transit.

| Case | getToken POSTs the refresh sent | Outcome | Witness |
| --- | ---: | --- | --- |
| Token 6 h old | 0 | promoted on the live token | R1, V1 |
| Token at the ceiling minus 1 s | 0 | promoted | V2 |
| Token exactly at `MAX_TOKEN_AGE_S` | 1, the one the ceiling costs whoever meets it first | promoted | R2, V3 |
| The same, five polls launched together, getToken 0.3 s slow | 1 for all six callers | all six ok | R2b |
| An accepted token refused at request start (`500`, "Invalid token.") | 1 re-mint, then 200 | promoted | R3, V5 |
| The refresh mints, a poll confirms the new token, getGTFS then refuses it | **2**, the most the code allows | promoted on the re-mint | V6 |
| Every token refused | 1 re-mint, its token refused fresh | `NjtAuthError`, nothing promoted; a 600 s hold | R4 |
| A cooldown running, token over age | 0: the held token was posted | promoted | R5 |
| The day's allowance spent, token over age | 0 | promoted; the hold, which runs to the next Eastern midnight plus 60 s (`backend/njt_auth.py:1164-1165`, `:630-655`), read 23,972.5 s, a length set by the hour the run's clock read (`m9_token_during_refresh.py` starts its fake wall clock at the real `time.time()`, its line 247) | R5b |

**The refresh-specific +1.** The one path measured to add a POST was a deadline cancelling a
mint in flight: with the refresh's deadline cut to 1.0 s against a 2.0 s getToken, 3 POSTs where
the ceiling alone takes 2, the cancelled one counted against the allowance, and a 600 s hold through which the over-age
token kept serving (T6). At the shipped deadlines the refresh's 120 s is the longest of the three
callers' (45 s, 20 s), so a poller meets that case first. With the pollers' deadlines scaled by
1/100 and getToken taking 0.6 s: a poll whose 0.45 s deadline cancelled its mint had it counted
against the allowance and opened a 600 s hold, a refresh inside the hold sent 0 and promoted on the over-age token,
and the poll after 601 s minted again, 3 getToken in all with the warm mint (V7a); a refresh
meeting the ceiling first on its own 120 s completed its mint, 2 in all and no hold (V7b). By
code, not measured, the one refresh-specific path is a re-mint begun late in the 120 s window after a slow data POST. The ledger's "A refresh reuses the live token
(item 6), so it costs no mint" (ledger:693-694) holds
under the ceiling; at the ceiling, or on an expired token, the refresh may be the caller that
spends the mint the pollers would otherwise spend. How many attempts a refresh that keeps failing
would make in a day, on the warmup's rungs, is not measured; each attempt is bounded at 2 and the
process at 4 a day.

**The day.** A refresh's POSTs come out of the same per-process allowance as the pollers'. The
README commits 4 monitor mints and 2 production mints a day, 6 of the account's 10
(`README.md:497-499` at `16d1c19`); the scheduled monitor ran 2 times on every Eastern day from
2026-11-15 to 2026-11-27 (runs 455 to 480, every one `schedule`, attempt 1, by
`gh run list --workflow contract-monitor.yml`), and on 2026-11-23 the monitor's 2 and
production's 2 of 4 (ledger:589) are the 4 attributable mints.
Production's allowance read `{"day": "2026-11-28", "spent": 1, "limit": 4, "spent_out": false}`
at 10:14:54 and 10:38:58 UTC. A deploy is a fresh process with a fresh allowance
(`README.md:521-523`), so two processes on one Eastern day can spend more than 4 between them.

#### 2.7.3 A token that expires mid-download

The token rides once, as the form field `token` at request start (`backend/njt_auth.py:1471`). The
whole body is read before the status is judged (`resp.content`, `:716`), and once a 200 status
line has arrived the server can only end the stream or add bytes. Any exception inside the
transfer but a cancellation becomes `NjtUpstreamError`: `_post` catches `Exception`
(`backend/njt_auth.py:1470-1473`), which a cancellation is not, so the download deadline's
cancellation surfaces as `TimeoutError` (T7). Neither invalidates nor confirms the token.
`staged_fetch` unlinks the stage on any exception and records the failure
(`backend/static_shared.py:665-670`), and the loader keeps serving a usable cache. A 200 calls
`confirm(token)` before the body is validated (`backend/njt_auth.py:1434-1436`).

| Case | Server model | Outcome | Token, cache |
| --- | --- | --- | --- |
| T1 | 200, `Content-Length` full, connection closed at half (at a third with the public archive, V4) | `NjtUpstreamError` (`RemoteProtocolError`) | token kept, no hold, 0 getToken; no stage file, nothing promoted |
| T2 | 200, chunked, closed with no last chunk | `NjtUpstreamError` | the same |
| T3 | 200, close-delimited, cut (at a third with the public archive, V4) | httpx returned the part as a 200; staged, then refused: "not a readable zip archive" | the same; `confirm` already called |
| T4 | 200, the whole archive, then `{"errorMessage":"Invalid token."}` | **promoted**, 33 trailing bytes included | token kept |
| T5 | T1, T2 and T3 over a promoted cache | three failures, `failed_downloads` 3 | the cache byte-identical |
| T7 | 6,000 bytes trickled in 300-byte pieces 0.3 s apart, deadline 1.5 s | `TimeoutError` at 1.503 s | token kept; the next poll used it |
| E1 | half sent, the token killed, the connection cut, a poll every 0.15 s beside it | `NjtUpstreamError` | the poll after the kill met `500 Invalid token.` and re-minted once: 2 getToken in all |
| E2 | half sent, the token killed, the body finished, polls beside it | ok, promoted | the poller re-minted once: 2 in all |
| E3 | as E1 with no poller, then the next attempt | first fails with the token still cached; the next meets `500 Invalid token.`, re-mints once, gets 200 | 2 in all, promoted on the second attempt |

**A cut body is never promoted; a complete one with bytes appended is.** Every strict prefix of
the zipped fixture, 79,091 of 79,091, and 73,001 of 73,001 sampled prefixes of the public
archive (n = 0, 3,000 random lengths and every one of the last 70,000) were refused by the real
`validate_archive` with `validate_njt_publication` as "not a readable zip archive". A complete
archive followed by trailing bytes was accepted with up to **65,535** appended zero bytes and
refused with 65,536, on both archives (a binary search in `v9_witness.py`); the 33-byte
invalid-token JSON and 1 KiB of HTML were accepted.

#### 2.7.4 What bounds a transfer

`REQUEST_TIMEOUT_S` = 30 s (`backend/njt_auth.py:254`), passed as `httpx.AsyncClient(timeout=...)`
(`:712-713`), bounds each read, not the request: with `timeout_s=1.0`, a 3,000-byte body delivered
in 10 pieces 0.3 s apart completed in 2.725 s (`m9_token_during_refresh.py`) and 2.728 s
(`v9_witness.py`), on httpx 0.28.1; with `timeout_s=0.5`, 1.5 s of silence after the headers raised `ReadTimeout` at 0.507 s. The
module's comment calls it the "Whole-request ceiling for one NJT POST" (`:238`), and
`FRESH_TOKEN_S = 4 * REQUEST_TIMEOUT_S` (`:265`) rests on that reading. On the static path the
whole-transfer bound is the 120 s `asyncio.timeout` (`backend/njt_static.py:772`), inside the 300 s
attempt (`backend/main.py:197`). The body is buffered whole and written inline
(`backend/njt_static.py:774`), with no byte ceiling.

#### 2.7.5 What is not known

The token-gated endpoints were not contacted, so these are unknown: the token's lifetime ("the
vendor's own docs template carries a literal blank", `backend/njt_auth.py:9-12`); whether NJ
Transit checks the token only at request start (E2's model) or can end a transfer in flight (E1's);
whether a token expires at a fixed age; how `getGTFS` frames its body (`Content-Length`, chunked or
close-delimited), which decides whether a cut raises (T1, T2) or is staged and refused (T3);
whether a getToken POST the client abandoned is charged (the code assumes so); what NJ Transit
answers to bad credentials; and whether `getGTFS` serves the bytes of the public `rail_data.zip`.
On this side, one thing decides whether the trains poller keeps calling `get()` every 20 s through a refresh, and so keeps the token's age under the ceiling plus one poll gap outside a hold: whether `njt_static_status` stays `ready`. Today nothing refreshes, so it is untested; 3.1 derives the status from whether a generation is held, so under the design it stays `ready`.
The token-gated pull of the 2026-11-06 publication was 4,369,879 bytes
(ledger:653), against the public file's 3,295,223 bytes of 2026-11-27.

### 2.8 The machinery a refresh inherits

#### 2.8.1 `staged_fetch` and the two-mode rule

`staged_fetch` (`backend/static_shared.py:545-694`), in order:

| Step | What happens | Where |
| --- | --- | --- |
| Sweep | deletes this archive's `<stem>.*.part` files left by a hard kill | `backend/static_shared.py:625-627` |
| Stage | `mkstemp` in the cache's own directory, so the rename stays on one filesystem | `:628-630` |
| Transfer | the injected download, or a streamed GET under `asyncio.timeout(deadline_s)`, 120 s by default | `:523`, `:526-542`, `:634` |
| Always-gate | `validate_archive(stage, validate)` in a worker thread | `:645` |
| Decorative gate | when given, the same in a thread; the two-mode rule decides what its failure does | `:646-664` |
| Failure | any exception: the stage unlinked; unless it is a `CancelledError`, recorded; re-raised | `:665-681` |
| Promotion | `stage.replace(dest)`, an atomic rename over the cache | `:682-692` |
| Record | `_record(key, promoted=True, degraded=degraded)` | `:693` |

**The two-mode rule.** The always-gate refuses in every mode. The decorative gate refuses only when
`cache_serves`, the loader's own verdict that its cache is usable and draws; otherwise the
publication is promoted and the failure written as a degraded member. A cold start has no cache,
so there a decorative failure always degrades (`backend/static_shared.py:598-605`).

| Loader (record key) | Always-gate, also the cached check | Decorative gate |
| --- | --- | --- |
| Subway (`subway`) | `validate_subway_archive` | `validate_subway_geometry` (`shapes.txt`) |
| LIRR, Metro-North (`railroad_LIRR`, `railroad_MNR`) | `validate_railroad_publication` | `validate_railroad_stop_times` |
| PATH (`path`) | `validate_path_publication` | none |
| NYC Ferry (`ferry`) | `validate_ferry_publication` | none |
| NJ Transit (`njt`) | `validate_njt_publication`, with the service-date guard (2.8.4) | `validate_njt_geometry` (`shapes.txt`) |
| Bus index | not built on `staged_fetch` | none |

#### 2.8.2 What is recorded per archive

Four fields per archive in the module-level `_ARCHIVE_STATUS` (`backend/static_shared.py:440`,
`_entry` at `:456-465`): `last_promoted_at` (`time.time()` at the rename), `last_download_error`,
`failed_downloads` (reset by a promotion) and `degraded_members`. Served under `static_archives`
on `/api/status` (`backend/routes/status.py:224`; `StaticArchiveStatus`, `backend/models.py:993`).
A key appears once its archive is downloaded in this process, or once a loader's cached check
finds a usable cache failing its decorative gate: `cached_archive_draws` then writes that key's
`degraded_members` with no download (`backend/static_shared.py:375-377`, `:383`, `_entry` at
`:456-465`), for the subway, both railroads and NJ Transit (`backend/static_data.py:400-402`,
`backend/railroad_static.py:523-525`, `backend/njt_static.py:1274`), as
`backend/models.py:1168-1170` says. So a boot over a warm cache whose decorative gates all pass
publishes no entry (the F07 witness prints `archive record on /api/status  = {}`, its `njt`
entry, for its warm-cache NJ Transit boot,
`docs/reviews/audit-2026-09-05/f07_static_never_refreshes.py:763`), and a boot over one whose
cached `shapes.txt` (subway, NJ Transit) or `stop_times.txt` (railroads) fails its gate publishes
an entry with `last_promoted_at` null and that member under `degraded_members`. **Not recorded anywhere in app code:** `Last-Modified`, `ETag`, any conditional
request, a sha256 or other hash, the byte size, the source URL, the `feed_info` version or the
service span (a grep over `backend/*.py`, `backend/routes/*.py`, `backend/feeds/*.py`); "No
production module parses feed_info today" (`backend/scripts/contract_monitor.py:1760`). **A
validation or transfer cut off by the 300 s attempt deadline never reaches the record**: the
attempt's `asyncio.timeout` arrives inside `staged_fetch` as a `CancelledError`, which skips
`_record` (`backend/static_shared.py:667-670`); measured with an outer `asyncio.timeout(0.05)`
around a 0.3 s validator, which raised `TimeoutError` and left no entry, no cache and no stage file,
while the transfer's own 0.05 s deadline was recorded as `last_download_error 'TimeoutError'`
(`v8_probe.py` part C).

Production at 10:13:37 and 10:34:31 UTC carried six entries, each `failed_downloads` 0,
`last_download_error` null and `degraded_members` `{}`, promoted after Ben's Railway Redeploy of
2026-11-28 08:00:16 UTC (03:00:16 Eastern; record:657): ferry
08:00:44.200785, LIRR 08:00:46.056070, PATH 08:00:48.557989, NJ Transit 08:00:50.670418,
Metro-North 08:00:55.232631, subway 08:00:57.278155 UTC, 13.077 s from first to last. The comment
listing the keys omits `njt` (`backend/models.py:1162-1163`), which the code writes.

#### 2.8.3 The 30-day rule

`MAX_AGE_DAYS = 30` in all six modules (`backend/static_data.py:44`,
`backend/railroad_static.py:62`, `backend/path_static.py:73`, `backend/ferry_static.py:73`,
`backend/njt_static.py:153`, `backend/bus_static.py:43`). `cache_is_fresh` calls a file fresh
when its age, `time.time()` at the call minus its mtime, is at least 0 and under `max_age_days`
times 86,400 s (`backend/static_shared.py:426`): measured with `time.time` pinned, 30 days minus 1 ms is fresh, exactly 30 days is stale, and an mtime 1 ms in the
future is stale (`v8_probe.py` part B). A stale cache is re-downloaded at the next load, and if
that fails the old cache is served "INCLUDING PAST MAX_AGE_DAYS". **In production the branch is
never reached.** Every deploy is a cold start: "the data directory does not persist, confirmed
against production's promotion stamps of 2026-10-13" (`backend/static_shared.py:598-600`); "On
Railway the disk is wiped every deploy" (`backend/bus_static.py:41-42`). A running process never
re-checks, since each loader's one production call site is its warmup (`backend/warmups.py:173`,
`:222`, `:296`, `:348`, `:423`; `backend/main.py:357`). **A restart is not a deploy:**
`railway.json` sets `restartPolicyType "ON_FAILURE"` with `restartPolicyMaxRetries 10`, and whether
a restart keeps `DATA_DIR` is not measured. If it does, a restart boots over a warm cache, which is
the only path to the 30-day branch and to the F07 witness's `{}` record. The docstring that says
lost NJ Transit lines return at "the age policy's re-fetch at MAX_AGE_DAYS"
(`backend/njt_static.py:1087-1088`) rests on that unreached branch.

#### 2.8.4 The calendar guard

Only NJ Transit's loader checks a calendar (a grep of the subway, railroad, PATH, ferry and bus
loaders finds none). `validate_njt_archive` (`backend/njt_static.py:669-679`) takes `service_span`,
the later of `calendar.txt`'s latest `end_date` and the latest added `calendar_dates.txt` day
(`:682`), and refuses when it is before today in New York (`:673`, `_today` at `:595`). It is part
of the always-gate, so it runs on every download and on each load's cached check. Measured through
the real `validate_njt_publication`: the fixture's two rows at an injected noon in New York and
the shifted fixture at the real clock by `m8_calendar_and_age.py`, and the one-day and removal
rows by `v8_probe.py` part A, at the instants their rows name and otherwise at noon in New York on
20261128:

| Publication and clock | Verdict |
| --- | --- |
| The fixture's span 20260818 to 20270501, on 20270501 | accepted |
| The same, on 20270502 | refused: "service ended 20270501, before today (20270502); the schedule has expired" |
| One added day, 20261231, at 2026-12-31 23:59 New York | accepted |
| The same, at 2027-01-01 00:01 New York | refused |
| **The fixture shifted 132 days (span 20261228 to 20270910), at the real date 20261128, 0 services today** | **accepted** |
| One added day, 20270127, on 20261128 | accepted |
| Removal rows only | refused: "calendar_dates.txt schedules no service days" |

So a publication not yet in service, one that schedules nothing today, passes; there is no
earliest-date or runs-today check (`backend/njt_static.py:665-679`). Past a feed's end the other
loaders do not look: run 481 (databaseId 46123612408, created 2026-11-28 07:58:21 UTC) logged
`WARN  path-static: feed_end_date 20260601 is in the past (acknowledged: ...)`, and production served `path_static`
`ready` at 10:13:37 UTC.

#### 2.8.5 The warmups' loops and returns

| Warmup | Loop | Returns | A partial result |
| --- | --- | --- | --- |
| Subway | `while True` | on success, after 5 fields and `ready` (`backend/warmups.py:199`) | all or nothing; `subway_routes` may be `[]` under `ready` |
| Railroad | `while True` around both systems' `gather` | when ANY system has stops (`backend/warmups.py:226`) | a `None` system stays `None` and is never retried in the process |
| PATH, ferry | `while True` | on nonempty stops (`:327`, `:373`) | empty is a failure, retried |
| NJ Transit | at once as `not-configured` without credentials, else `while True` | on nonempty stops (`:475`) | empty is a failure, retried |
| Bus index | none: one threaded build (`backend/bus_static.py:300`) | after one build, success or not | `failed` or `partial`, neither rebuilt in the process |

A failed attempt sleeps on the rungs `(15, 30, 60, STATIC_RETRY_S)` with `STATIC_RETRY_S` 300
(`backend/main.py:125`, `:163-165`). Each rung is capped at `STATIC_RETRY_S`
(`backend/warmups.py:121`) and then multiplied by `0.9 + 0.2 * rand()` (`:111`, `rand` defaulting
to `random.random`, `:96`), so the last rung sleeps 270 to 330 s; each attempt runs under `STATIC_ATTEMPT_DEADLINE_S` = 300 s
(`backend/main.py:197`), which cannot preempt an inline parse. All six tasks are created once in
`lifespan` (`backend/main.py:343-357`); the F06 witness counted 8 `create_task` calls in one
startup, 0 after the warmups finished, and no `add_done_callback` in the backend package. A failed
attempt leaves `app.state` as it was, since each block assigns only after a successful load. The
comment justifying the 300 s by a second download inside one attempt (`backend/main.py:179-187`)
no longer holds: each loader calls `_download_zip` once per attempt (`backend/path_static.py:524`,
`backend/railroad_static.py:533`).

#### 2.8.6 Where each step runs

Off the loop, in `asyncio.to_thread`: every validation, staged and cached
(`backend/static_shared.py:381`, `:403`, `:645`, `:649`); the subway's route shapes and routes
index (`backend/warmups.py:180`, `:186`); the bus build (`backend/bus_static.py:300`) and each bus
route read (`backend/routes/buses.py:39`). On the loop, as plain calls: the subway's `_parse_stops`
(`backend/static_data.py:427`), stations and complexes (`backend/warmups.py:181`, `:189`); the
railroad, PATH, ferry and NJ Transit parses (`backend/railroad_static.py:546`,
`backend/path_static.py:535`, `backend/ferry_static.py:450`, `backend/njt_static.py:1301`); every
builder in the warmups' blocks (`backend/warmups.py:252-273`, `:309-325`, `:362-371`, `:448-473`);
NJ Transit's whole-body buffer and its inline write (`backend/njt_auth.py:716`,
`backend/njt_static.py:774`); and the default transfer's per-chunk writes
(`backend/static_shared.py:540-542`). A threaded gate still stalls the loop a little: with a 1 ms
heartbeat, `validate_njt_geometry` in its thread left longest gaps of 36.0, 45.3 and 42.1 ms over 3
runs, and `validate_njt_publication` 13.1, 6.7 and 9.9 ms (`gate_thread_gaps.py`). How long each
inline stretch holds the loop is 2.1's.

#### 2.8.7 The witnesses that still reproduce at `16d1c19`

Each ran green at `16d1c19` on 2026-11-28, hermetically (credentials blanked, `-I -B`, a fresh
`TMPDIR`), once for the inventory and once for its verification.

| Witness | Exit, time | What it printed |
| --- | --- | --- |
| `docs/reviews/audit-2026-09-05/f06_partial_startup_no_recovery.py` (F06, and A05, which has no script of its own: "VERIFIED, restating F06", ledger:421) | 0, 5 s | a partial railroad warmup reaches `ready` with Metro-North `None` after exactly 1 attempt per system and makes no further attempt across 100 shortened retry intervals; the both-fail control retried 81 times (inventory) and 84 (verification), against the ledger's 92 at `1e3e73d`, a count that moves with the machine; a partial bus build leaves 5 of 8 routes until a new startup |
| `docs/reviews/audit-2026-09-05/f07_static_never_refreshes.py` (F07, A06's backend half) | 0, 1 s | all 6 static tasks return; `load_njt_static` ran 1 time across 40 poll cycles; a 31-day-stale (2,678,400 s) archive whose calendar has ended still serves 164 stations at HTTP 200 with `public, max-age=3600`, `/healthz` `pass` with `degraded=[]` |
| `docs/reviews/audit-2026-10-12/a06_frontend_static_once.mjs` (A06's frontend half) | 0, 4 s | 2.5.4's numbers |
| `docs/reviews/audit-2026-10-12/a09_unbounded_upstream.py` (A09) | 0, 18 s | arms 2 and 3 still stand: the real transfer and subway validator promoted a 536,871,856-byte publication, an archive of 70,005 members, and one whose `stop_times.txt` declares 2,147,483,720 expanded bytes (985:1); no byte, member-count or expanded-size ceiling exists at or below those sizes |

#### 2.8.8 `/healthz`: eleven codes

`HEALTH_DEGRADED_CODES` holds 11 codes (`backend/models.py:1358-1370`) and `HEALTH_GATING_CODES`
3 of them (`:1404-1408`): `no-feed-fresh`, `bus-route-index-failed` and `subway-static-failed`;
the handler answers 503 only for those. The codes that concern static data:

| Code | Gating | Raised when |
| --- | --- | --- |
| `bus-route-index-failed` | yes | the bus index `failed` (`backend/routes/status.py:328-329`) |
| `subway-static-failed` | yes | `subway_static_status == "failed"` (`:334-335`) |
| `subway-lines-degraded` | no | the subway is `ready` and no served route line has two points (`:821-824`); "Cleared by the next start whose lines draw, which in production is the next deploy" (`backend/models.py:1313-1314`) |
| `subway-route-unmapped` | no | the static routes-per-station index names a route outside `SUBWAY_ROUTE_GROUPS`, re-read on every call (`backend/routes/status.py:469-472`), or a decoding group has served one in this process, latched (`backend/pollers.py:646-657`) |
| `njt-schedule-behind` | no | the held schedule contradicts the trains past the 600 s hold (`backend/routes/status.py:826`, `:440-441`); "WHAT CLEARS IT ... means a redeploy" (`backend/models.py:1343-1345`) |

**No code exists for** a railroad, PATH, ferry or NJ Transit group `failed` or `loading` (railroad
"intentionally omitted", `backend/routes/status.py:332-333`); any `static_archives` field; any
archive's age; or NJ Transit's calendar end in a running process (the F07 witness's
`degraded=[]`). Production read `{"status":"pass","degraded":[]}` at 10:13:38 and 10:34:32 UTC.

#### 2.8.9 The monitor: 23 checks and one "redeploy" verdict

Run 481 (databaseId 46123612408, scheduled, attempt 1, created 2026-11-28 07:58:21 UTC, on
`018e82d`, whose `contract_monitor.py` is `16d1c19`'s) ended "2 FAIL, 3 WARN, 23 checks total":
5 upstream static checks (`subway-static`, `railroad-static`, `path-static`, `ferry-static`,
`njt-static`), 7 upstream realtime and 11 production lines (`run_all`,
`backend/scripts/contract_monitor.py:4223`). The upstream static checks run each publication
through the app's own gates on the runner; **nothing in the monitor reads `static_archives`** (the
one occurrence of the record's names is prose at `:358`), and nothing compares production's held
generation of the subway, railroad, PATH or ferry archive with upstream's. Only NJ Transit's is
compared, and only through its trains.

`production:njt-schedule` is the one check whose FAIL sentence is itself the instruction:
"production's schedule is behind NJ Transit's publication: redeploy" (`:3651`). Other FAIL
texts carry the word too. `production:healthz` FAILs on every recognized code outside
`_WARN_HEALTH_CODES` (`:332`, `:2856-2857`) and appends each reported code's note (`:2850`). All
six notes say "redeploy" (`:355-411`); the two WARN codes' notes reach a FAIL line only beside a
code that FAILs. Of the four notes on codes that FAIL, only `subway-lines-degraded`'s rests on a
running process never re-fetching: "a redeploy restores the lines only once upstream's shapes.txt
draws again" (`:356-364`). Those of `njt-mint-allowance`, `njt-mint-quota` and
`observations-qualified` say a redeploy does not fix their state (`:370`, `:378`, `:386`). An
absent `degraded` key FAILs with "Redeploy from a revision that includes it." (`:2831-2836`).

---

## 3. The design (proposed; every choice Ben owns is a question in section 5)

This section proposes one mechanism for the six static archives and the bus route index, built NJ
Transit first (section 6). A generation holds everything one archive's readers need and is replaced
by one assignment (3.1). Triggers decide when to look upstream (3.2). A candidate passes the gates a
cold start passes before it can be assigned (3.3), and NJ Transit's is fetched on a borrowed token
that is never minted (3.4). The operator (3.5, 3.6) and an open page (3.7) learn when a generation
changes. 3.8 puts the two witnesses of 2.6 to work, 3.9 says what a cold start does, and 3.10 bounds
memory. Each proposal names the measurement behind it. Where a choice is Ben's, the subsection ends
with its question in section 5; what the body proposes without a question is listed at the head of
section 5, so a decision can reject any of it by name.

Code is cited at `16d1c19`. Every timing and size measured for this design was taken on 2026-11-28
on one machine (Apple M2, 8 CPUs, 8,589,934,592 bytes of RAM, CPython 3.12.13), not on Railway.
"Measured for this design" below means measured there, on that date, by the scripts and commands
section 2 names at the subsection given; the instrument is repeated beside each figure, because the
same structure measures differently under tracemalloc, macOS footprint and a recursive `getsizeof`.

### 3.1 State: one immutable generation per archive

**What a refresh would meet in today's state (2.4).** Each archive's derived structures are separate
`app.state` attributes, assigned by a warmup that runs once. On success the assignment is one loop
turn: no tick of a 1 ms watcher saw a mixed set of NJ Transit's eight fields (0 of 395 ticks,
measured for this design with a 1 ms watcher over the real loader and a cached copy of NJ Transit's
public file, 2.4), and the block from `backend/warmups.py:442` to `:475` holds no await. Nothing
mixes today only because nothing is assigned twice. A refresh that reused those fields would meet
four measured facts. Polls hold a static reference across an await (five sites, below). A builder
that raises leaves the set mixed: with `build_njt_route_shapes` raising on a second warm,
`njt_static`, `njt_station_routes` and `njt_stops` were new, the other four old, the status `ready`
and the task dead with `ValueError`; with `build_njt_trip_index` raising, four new and three old
(measured for this design by raising from one builder at a time on a second warm, 2.4). The
promotion record leads the tables (below). And live state keyed by static ids outlives the tables it
was keyed by. Measured for this design on the real `_refresh_njt`, held at its own await
(`backend/feeds/njt.py:1067`) while a second warmup assigned the 2026-11-27 public file over the
committed 2026-09-01 fixture, two real publications 87 days apart set together to make the mix
visible (2.4): of the 144 boards whose ids exist in both, 100 named another station; 20 of the 164
keys the poll wrote answered 404 though their rows were in the index; 50 of the 60 trains carrying a
stop id the new table has carried a `stop_name` that is not the new table's name at that id; and
`njt_feed_health` published joined 86, checked 86 against the old trips beside the new ones.

**The generation.** The design proposes one frozen object per archive, holding every derived
structure a reader reads and nothing else, plus its identity. The fields are today's attributes,
regrouped. Counts are production's current publications, measured for this design: NJ Transit's
served form by GETs at 2026-11-28 09:47:02 UTC, the others built by the app's own loaders from the
archives production holds and found equal to production's payloads (GETs at 09:43:38 UTC for the
subway, 09:58:32 to 09:58:33 UTC for the railroads, PATH and ferry); 2.4.

| Archive key | Generation fields (today's `app.state` name) | Measured | Readers that take it |
| --- | --- | --- | --- |
| `njt` | `stops` (`njt_stops`), `station_routes` (`njt_station_routes`), `routes` (`njt_routes`), `trips` (`njt_trips`, the decode's join index), `lines` (`njt_lines`), and `stop_schedule` (`njt_stop_schedule`), which no reader reads and Q14 may drop | 164 stations; 13 routes, 18 polylines, 1,595 points; 17,461 scheduled calls | the trains poll (`backend/pollers.py:1121-1125`); `/api/njt-stops` (`backend/routes/njt.py:79-80`), `/api/njt-routes` (`:124`), `/api/njt-arrivals/{stop_id}` (`:179-188`) |
| `subway` | `stops`, `routes`, `stations`, `station_routes`, `station_complexes` | 1,488 stops (496 parents, 992 platforms); 24 routes, 35 polylines, 22,458 points; 496 stations, all 496 indexed, 444 complexes | the subway poll (`backend/pollers.py:586`, `:610`); the three subway endpoints; `/api/status` and `/healthz` (`backend/routes/status.py:469-472`, `:823`) |
| `railroad_LIRR`, `railroad_MNR` | per system: `stops`, `routes`, `station_routes` | LIRR 127 stops, 12 routes, 126 indexed; Metro-North 114, 6, 113 | the railroad poll (`backend/pollers.py:727`); the three railroad endpoints |
| `path` | `stops`, `routes`, `station_order`, `station_routes` | 13 stations; 7 routes, 14 polylines, 1,302 points; 14 orders; 13 indexed | the PATH poll (`backend/pollers.py:873`, `:933`); the three PATH endpoints |
| `ferry` | `stops`, `routes` (`ferry_routes`), `station_routes`, and the `trips` and `routes` tables of `ferry_static` that the decode joins every boat against (`backend/feeds/ferry.py:406-407`) | 50 docks; 7 routes, 14 polylines, 712 points; 485 trips | the ferry poll (`backend/pollers.py:993`); the three ferry endpoints |
| `bus` | the route-file directory and its manifest | 345 route files of 5,462,783 bytes plus a 2,260-byte manifest, from six borough archives (one build for this design, counted on disk, 2.1) | `/api/bus-route/{route_id}` (`backend/routes/buses.py:28-41`) |

AirTrain is not an archive. `data/airtrain_jfk.json` (17,696 bytes) is committed, read once in the
lifespan (`backend/main.py:295`), and changes only with a deploy, so it stays outside the refresh;
its endpoint carries the file's sha256 as its stamp (3.7, 4.4).

**The identity** each generation carries, proposed:

```python
@dataclass(frozen=True)
class ArchiveIdentity:
    archive: str                 # "njt", "subway", "railroad_LIRR", "railroad_MNR", "path", "ferry", "bus"
    sha256: str                  # of the bytes as received; for "bus", of its boroughs' sha256 values in BUS_GTFS_URLS order, a borough it lacks as None
    content: str                 # digest of (member name, CRC-32, size) read from the zip's central directory; for "bus", of its boroughs' content in BUS_GTFS_URLS order, a borough it lacks as None
    size: int                    # bytes as received
    etag: str | None             # as the publisher sent it, else None; None for "bus", whose six are its boroughs'
    last_modified: str | None    # likewise
    boroughs: dict[str, "ArchiveIdentity | None"] | None  # "bus" only: each borough archive's own identity, keyed as BUS_GTFS_URLS is, None for a borough this generation lacks; else None
    feed_version: str | None     # feed_info.txt, where the archive has one
    service_from: str | None     # YYYYMMDD, the first scheduled day
    service_through: str | None  # YYYYMMDD, the last scheduled day
    degraded_members: dict[str, str]  # decorative members this generation lacks (3.3)
    built_at: float              # the builder's injected clock when it returned
    promoted_at: float           # time.time() read on the loop at the assignment, a wall-clock instant as last_promoted_at is today (set by dataclasses.replace)
```

Two of these fields need saying. `content` stands beside `sha256` because whether two downloads of
one NJ Transit publication are byte-identical is unknown: the token endpoint's headers are discarded
(`backend/njt_auth.py:715-716`) and no record holds a same-publication pair of hashes (2.2). The
ledger's 2026-11-06 pull was "dated 19:11 EST, 2026-11-07 00:11 UTC; 4,369,879 bytes ... its members
built 2026-11-06 14:25" (ledger:653), which fits a stored file and does
not prove one. A digest of each member's CRC-32 and size is equal for two zips of the same members
whatever their zip-level bytes, so the design proposes that "unchanged" means equal `content`; Step 0 (6.0, Q17) measures whether `sha256` alone would do. The service span and `feed_version` are read
for identity, not as gates (3.3): the app parses no `feed_info.txt` today ("No production module
parses feed_info today", `backend/scripts/contract_monitor.py:1760`), NJ Transit's `service_span`
returns only the last day (`backend/njt_static.py:682`), and the archives differ in what they carry:
the subway's feed runs 20261101 to 20270515, PATH's 20250522 to 20260601, the ferry's 20261127 to
20271231, Metro-North has no `feed_info.txt`, and NJ Transit's public file has neither
`feed_info.txt` nor `calendar.txt` (each archive read for this design on 2026-11-28, 2.2).

**The builder.** A pure function, `build(archive, path, *, now)`, returning a `Generation`. It reads
only the file at `path` (the staged download), takes the clock as an argument (NJ Transit's guard
reads today in New York from it, `backend/njt_static.py:595-607`), writes no `app.state`, opens no
socket and reads no module-path constant. That last clause changes the subway: its five loaders open
`static_data.SUBWAY_GTFS_ZIP` by module path with no parameter (`backend/static_data.py:378`,
`:449`, `:513`, `:667`, `:771`), so today a subway generation can be built only after its archive is
renamed over the live cache, and the monitor builds one by reassigning the constant
(`backend/scripts/contract_monitor.py:1681-1682`).

The builder runs in a worker thread (`asyncio.to_thread`), as the gates already do
(`backend/static_shared.py:645`, `:649`) and as the subway's two heavy loaders do
(`backend/warmups.py:180`, `:186`). That is A09's rule, which item 6 carries: "whatever runs every
few hours must not be able to stop the serving loop for as long as a cold start can"
(ledger:925-927). Inline today (2.1), NJ Transit's warmup held the loop
for 2,813.1 ms in one stall (measured for this design with a 1 ms heartbeat, the real loader over a
cached public stand-in), the rail-only shape for 2,300.7 ms (median of three runs of a mirror of the
same calls, the same heartbeat), the railroads for 716.1 ms and PATH for 377.4 ms measured alone (medians of three, 2.1's table), and PATH for 525.8 to 689.3 ms when three warmups ran together on a warm cache. A thread does not
free the loop entirely: NJ Transit's geometry gate in a thread still left gaps of 36.0 to 45.3 ms
(the same heartbeat, switch interval 0.005 s), and the subway warmup, with its heavy steps threaded,
16.3 to 18.3 ms. The design also moves NJ Transit's `dest.write_bytes(body)`, inline today after the
POST (`backend/njt_static.py:774`), into the worker with the hashing; its cost is unmeasured.

A thread cannot be stopped ("The thread cannot be stopped and finishes on its own",
`backend/static_shared.py:640-644`). So the design proposes that the refresh worker awaits each
build to its end and starts no second build beside it (3.10), with a deadline on the transfer
(`DOWNLOAD_DEADLINE_S`, 120 s, `backend/static_shared.py:523`) and none on the build. A subprocess is the alternative if a hard CPU deadline is wanted (Q16). The case for one is the archive a thread cannot
bound: the A09 witness at `16d1c19` still promotes a 536,871,856-byte publication, an archive of
70,005 members and one whose `stop_times.txt` declares 2,147,483,720 expanded bytes
(`docs/reviews/audit-2026-10-12/a09_unbounded_upstream.py`, run for this design, exit 0, 2.8). The
cost of returning a generation across a process boundary is unmeasured.

**The single assignment.** When the build returns, the worker assigns on the loop, in one statement:
`app.state.static[archive] = gen`, with `promoted_at` read from `time.time()` at that statement, a
wall-clock instant as `last_promoted_at` is today (`backend/static_shared.py:483`), not the loop's
monotonic `loop.time()`. Nothing is
assigned beside it. The `*_static_status` strings the monitor reads by name (`production:statics`,
`backend/scripts/contract_monitor.py:3061-3105`) become derived: `ready` once a generation is
assigned, `loading` before the first, `failed` while a cold start has none and its last attempt
failed. NJ Transit's fourth, `not-configured`, is not derived from a generation: the cold start sets
it when there are no credentials, as today (`backend/warmups.py:405-412`, `backend/main.py:274-277`),
and hands nothing to the refresh worker, so no refresh runs behind it (4.2). Status and tables then
cannot disagree, and a builder that raises assigns nothing, so the
mixed sets above cannot form. Today promotion and memory are two steps: `last_promoted_at` is
stamped at the rename (`backend/static_shared.py:483`, called at `:693`), before the parse. Stamped
at the promotion itself, the subway's tables were assigned 894.63 ms later, with 4 loop iterations
between (one cold run of the real warmup, measured for this design by counting event-loop
iterations, 2.4); LIRR's no earlier than 9.176 s after its stamp (production's `/api/status` stamps
of 2026-11-28, 08:00:46.056 and 08:00:55.232 UTC, 2.4); and production's subway file mtime preceded
its promotion stamp by 13.058 s (`/api/status` read at 10:15:36 UTC, 2.4). With the identity inside
the generation, what `/api/status` reports and what readers read are one object.

**The reader rule.** Every reader takes the reference once, per poll or per request, and reads only
that generation: `gen = app.state.static.get("njt")`. A poll that awaited across a swap finishes on
the generation it took, which today's NJ Transit poll already does with its three arguments (in the
measurement above they were the old publication's objects after the new one was assigned). The rule
changes the five sites that hold a reference across an await today (2.4):

| Poll | Read | Await | Under the rule |
| --- | --- | --- | --- |
| NJ Transit | `njt_stops`, `njt_trips`, `njt_lines` as arguments (`backend/pollers.py:1121-1125`) | the POST (`backend/feeds/njt.py:1067`) | one `gen`, its three tables |
| subway | `subway_stops` (`backend/pollers.py:586`) | `fetch_subway_trains` (`:610`) | one `gen` |
| railroads | `railroad_stops`, both systems (`backend/pollers.py:727`) | the `asyncio.gather` inside the fetch (`backend/feeds/railroad.py:1304`) | the two railroad generations, taken together before the fetch |
| PATH | `path_stops` before the fetch (`backend/pollers.py:873`), `path_station_order` after it (`:933`) | `fetch_path_trains` (`:889`) | both from the `gen` taken before the fetch |
| ferry | `ferry_static`'s `trips` and `routes` (`backend/feeds/ferry.py:406-407`) | the TaskGroup (`backend/feeds/ferry.py:428-432`) | one `gen` |

PATH's is the one that straddles: a swap during its fetch would decode against one generation and
match identities against the next (`backend/feeds/path.py:229-231` decodes against the stops the
poll took; `backend/pollers.py:933` reads the order after the await). The request handlers have no
await (an AST pass and a grep of the route modules for this design, 2.4), so for them the rule is
one `get` at the top. The exception is `/api/bus-route/{route_id}`, which reads the index's status
before an await and its partial flag after it (`backend/routes/buses.py:28`, `:39`, `:41`); it takes
both from one bus generation.

**The live-state rule.** Live state carries the generation it was decoded under, so a route never
serves rows decoded under one generation with names from another:

| Live state | What ties it to a generation today | Under the design |
| --- | --- | --- |
| `njt_arrivals`, `feed_cache["njt"]` trains (`backend/pollers.py:1229`, `:1206`) | keyed by held stop ids; headsigns, stop names and coordinates from the held tables | carry their generation; a route joins their rows only to that generation's names; replaced whole by the next poll |
| `njt_feed_health` (`backend/pollers.py:1191-1199`) | counts of the decode against the held `trips` | carries its generation, which `/api/status` names beside the counts |
| `njt_behind_run` (`backend/pollers.py:1203`) | a run of decodes in which the held schedule contradicts the trains (`BehindRun`, `backend/feeds/njt.py:639-642`), ended only by a decode that can judge (`:648-664`) | carries its generation; a decode under another starts from no run, so `/healthz` never names a schedule the process no longer holds |
| subway and railroad position anchors (`backend/feeds/shared.py:335`, `:345-347`) | an anchor keeps its coordinates until the train's next stop changes | anchors of another generation are dropped at the first poll under the new one |
| retained trains, up to `FEED_RETENTION_MAX_S` 600 s (`backend/cache.py:104`) | deep copies carrying old coordinates and names | carry their generation; never merged into another generation's envelope |
| `path_identity` (`backend/main.py:247`, `backend/pollers.py:930`) | slots keyed by route, direction and stop id; an advance needs `station_order` | a new epoch at the first poll under the new generation |
| `ferry_arrivals` (`backend/feeds/ferry.py:263-266`) | buckets keyed by the route's long name from the held table | carries its generation |
| `subway_arrivals`, `subway_arrivals_by_system`, `railroad_arrivals`, `path_arrivals` (`backend/pollers.py:444-446`) | keyed by static station or stop ids; the boards name them from the table the request reads (`backend/routes/subway.py:120`, `backend/routes/railroad.py:171`, `backend/routes/path.py:127`) | carry their generation; a board names their rows only from that generation |
| `_ARCHIVE_STATUS` (`backend/static_shared.py:440`) | a module global written at the rename, several awaits before memory changes | replaced on `/api/status` by the identity of the generation readers hold (3.5) |

On a swap the design proposes that the poll loop's sleep ends early, so the next cycle starts at
once rather than after `POLL_INTERVAL_S` (20 s, `backend/pollers.py:70`). No poll of one family
exists to wake: `_poll_feeds` runs the six refreshers of `FEED_REFRESHERS`, the buses' included
(`backend/pollers.py:1268-1275`), as one `TaskGroup` cycle and sleeps after it (`:1330`, `:1362`),
and "A CYCLE ENDS ONLY WHEN EVERY CHILD HAS ENDED" (`:1288`). So the early cycle runs all six
refreshers, and a swap during a cycle starts no second NJ Transit
poll beside the one in flight, which holds the old generation and, ending last, would overwrite the
new poll's rows (`:1206`, `:1229`). Until the first poll under the new generation lands, the rows
decoded under the old one are served only under the old generation's names, which their envelope's
stamp says (3.7). While polls succeed, old rows are served for the rest of the cycle in flight and
one more poll, each bounded by `REFRESH_DEADLINE_S` (45 s, `backend/pollers.py:108`): at most 90 s
(arithmetic over that literal). A failed poll keeps the rows it does not replace ("Last-known data
keeps being served", `backend/cache.py:239`; `backend/pollers.py:1085-1093`, `:1226-1229`; 2.4.7):
NJ Transit's whole envelope for as long as its polls fail (`backend/pollers.py:379-380`), and one
failed system of an aggregate for up to `FEED_RETENTION_MAX_S`, 600 s (`backend/cache.py:104`). The
old generation stays alive while any live row carries it (3.10). An envelope carries one stamp per
archive (4.4), so a failed subway group's retained trains of the old generation are either dropped
at the swap, leaving the map until that group decodes, or served in an envelope that names both
subway generations; Q22 asks which. A railroad system is its own archive and is retained whole
(`backend/pollers.py:757-771`), so its retained rows keep its old generation's stamp and no
envelope names two for it. Every count of the measured mix above is zero
under the two rules by construction; Step 1's witness has to show it.

**What the generation leaves out.** The design proposes dropping three structures no reader reads: `njt_static` whole (assigned at `backend/warmups.py:442`; read only by tests, `backend/tests/test_api.py:5609`, `:5852`), `railroad_static` (`backend/warmups.py:249`) and `path_static` (`:308`), which no app code reads (a grep at `16d1c19` for this design, 2.1). A fourth, `njt_stop_schedule` (`backend/warmups.py:471`; read only by tests, `backend/tests/test_api.py:5615`, and set by one at `:5841`), is kept for now, because the lifespan reserves it "for the panel era" (`backend/main.py:273`); Q14 asks whether it goes too. `ferry_static`'s `trips` and `routes` stay, since the decode joins against them on every poll. Measured per generation (2.1): in production's NJ Transit shape (the rail-only projection), deleting `njt_static["shapes"]` freed 50,236,023 bytes and deleting `njt_stop_schedule` 4,911,728 bytes (tracemalloc deletion deltas); what `railroad_static` holds that nothing else references is 56,535,728 bytes and what `path_static` holds 22,651,175 bytes (recursive `getsizeof` over objects no served structure shares). The two instruments differ, so the figures are not summed here.

**The bus index's generation** is a directory named by its identity, written whole by the build and
swapped by reference. Today route files are rewritten one at a time into the directory that requests
read, and a route a newer publication drops keeps its old file, because the build sweeps only
`*.part` (`backend/bus_static.py:169-176`, `:225-226`). After a swap the old directory is removed
once no request holds it.

Its identity is one over six archives, so a single `etag` and `last_modified` cannot hold the
validators that a conditional GET of each borough (3.2's table) and the monitor's per-borough
comparison (3.5, 6.5) need: the six carry six different ETags, and five different Last-Modified
values from 15:14:41 to 15:14:45 UTC on 2026-10-16, Brooklyn's and Queens' both 15:14:43
(`probe_headers.py`, 2.2). The design proposes `boroughs`, one identity per borough archive keyed
as `BUS_GTFS_URLS` is (`backend/bus_static.py:57-64`), None for a borough the generation lacks,
each with its own `sha256`, `content`, `size`, `etag` and `last_modified`, published with the rest of
the identity (4.5). How one borough's queue entry (4.2) yields a whole directory is open. A borough
that failed at the cold start wrote no route files, so the live directory's files plus its own
make the whole set, since "each route is operated by one division" (`backend/bus_static.py:116-118`).
A borough whose publication changed is harder: the build keeps no borough archive
(`backend/bus_static.py:211`) and the manifest records no route's borough (`:246-249`), so a route
it drops can leave only by a rebuild from all six, 54,633,368 bytes at today's sizes (2.1), or by a
manifest that records which borough wrote each route. The six Last-Modified values fall within
four seconds of each other (the stamps above), so the one change read moved all six. And today a failed borough is
logged and skipped while the other five are kept (`backend/bus_static.py:239-242`), where a refresh
could instead keep the live directory. Q23 asks both.

**The archive file on disk becomes a cache of the bytes, not the state.** The worker renames the
validated stage over `data/gtfs_static/<archive>.zip` after the assignment, not before it, and
nothing in memory reads the file again. With no volume (3.9) no later start reads it either; it is
kept for a restart that keeps the data directory, if Railway's does (unmeasured, 2.8).

**Questions this leaves:** Q6 (the memory ceiling, 3.10), Q14 (dropping the unread structures), Q16 (the off-loop mechanism, a thread or a subprocess), Q22 (whether a failed subway group's retained trains are dropped at a swap or served in an envelope that names both generations) and Q23 (whether one bus borough's change rebuilds all six or only that borough's routes, and whether a bus refresh with a failed borough is `failed`, keeping the live directory whole).

### 3.2 Triggers

A generation is replaced only when a check finds a publication that differs from it. When to check
is answered twice in the records: F07's remedy, "Use publication changes and service validity to
select refresh times" (audit-2026-09-05.md:216), and the ledger's, "a refresh that
finds the schedule unchanged must try again soon, and `njt-schedule-behind`'s own count is the
signal that says when it is wanted" (ledger:689-691).

**One worker, one state machine per archive.** The design proposes a single refresh worker with a
queue (3.10). Each archive waits until its `next_check_at` or an evidence trigger, is checked (a
conditional GET, or for NJ Transit a download on the borrowed token), and if the check finds a
change, is validated and built off the loop. Each check ends in one of five outcomes:

| Outcome | What the worker does | Next check |
| --- | --- | --- |
| `unchanged`: a 304, or a 200 whose `content` equals the live generation's | records `last_checked_at`; builds nothing | the timer, or the next rung while evidence holds |
| `promoted` | assigns (3.1), ends the poll loop's sleep so the next cycle runs at once (3.1), renames the stage | the timer |
| `refused`: a gate or the builder refused the candidate | keeps the live generation; records why (3.3) | the next rung while the evidence holds (Q3); otherwise, for NJ Transit, as Q3 answers, and for a public archive or a component with no generation, as Q20 answers |
| `failed`: transport, deadline or upstream status | keeps; records the error | the next rung while the evidence holds (Q3); otherwise, for NJ Transit, as Q3 answers, and for a public archive or a component with no generation, as Q20 answers |
| `deferred`: for NJ Transit, no live token to borrow or the borrowed token refused (3.4); for any archive, memory over Q6's ceiling if Q6 sets one; how it is counted is Q18 | sends nothing, or for a refused token the one `getGTFS` POST | for the token cases, after the trains poll's next decode; for Q6's, as Q6 answers |

**The timer, and a conditional GET.** For every archive with a public URL the check is a GET
carrying the ETag (`If-None-Match`) and Last-Modified (`If-Modified-Since`) the live generation
holds for that URL, for a bus borough its entry in `boroughs` (3.1). Every
one of the 11 URLs the app fetches answered both with 304 and no body, as did NJ Transit's public
file (curl, measured for this design 2026-11-28 09:41:50 to 09:42:41 UTC and again 10:18:07 to
10:18:22 UTC, 2.2), and httpx 0.28.1, the version the lock pins, on a client built like the app's,
carried `If-None-Match` through the ferry's 302 and through the public file's two 301s to a 304
(10:22:57 and 10:22:58 UTC, 2.2). A check that finds nothing therefore costs headers, where an
unconditional one costs the bodies: 67,157,724 bytes for one download of all eleven, 54,633,368 of
them the six bus archives (curl's received bytes, measured for this design, 2.1). Today the app
sends no validator (`backend/static_shared.py:537-538`) and stores none (a grep at `16d1c19` finds
only `backend/main.py:462-463`, about the app's own static files). A 200 is not by itself a change:
S3's ETag is the body's MD5 (9 of 9 objects, each body hashed for this design, 2.2), but PATH's
encodes size and mtime and the ferry's was not decoded, so a re-upload of the same bytes can
answer with a full body. The worker compares `content` before it builds.

**NJ Transit's check.** NJ Transit's archive comes from a POST with the token as a form field
(`backend/njt_static.py:758-774`). Whether that endpoint answers HEAD or a conditional request, or
sends any validator, is unknown (2.2). So its check is the full download on the borrowed token
(3.4), compared by `content`: one getGTFS POST of 4,369,879 bytes at the last recorded size
(ledger:653), answered in 428 ms overnight and 8.9 s at peak when probed
on 2026-08-05 (`backend/njt_static.py:139-141`), and no getToken POST. Whether getGTFS calls are
themselves metered is not recorded; the repository records only that the usage counter cannot be
read with this token (`backend/njt_auth.py:145-146`). NJ Transit's public `rail_data.zip` answers
conditional GETs, but the design does not propose it as a trigger. It was a different publication
before November (its capture of 2026-10-22 ran through 20270313 while the monitor read the API's
service through 20270501, 2.2), it carries 1,914 light-rail trips production does not serve (counted
for this design by `route_type`, 2.2), and its Last-Modified gives only the latest change before a
fetch, so neither lag read between it and the API measures which moved first (2.2). It is evidence
for Q1, not a mechanism.

**The evidence triggers, NJ Transit's.** Beside its timer, NJ Transit's check is triggered at once
by three signals the decode holds. Each arrives on a poll that used the live token seconds before,
so a triggered check finds a token to borrow (3.4) unless a hold is running.

1. **The start of a behind run**: the first decode for which `schedule_behind` holds
   (`backend/feeds/njt.py:628-636`, `:660-661`), not the 600 s hold after which `/healthz` names it
   (`:606`, `:667-669`). On 2026-11-27 production's trains contradicted its schedule from 20:13:00
   UTC at the latest (record:647).
2. **The start of a MISS run** (3.8): the first decode whose misses that are not ADDED reach Q9's
   trigger. The design proposes that a MISS run, like a behind run (`backend/feeds/njt.py:648-664`),
   then holds until a decode says otherwise, so a count moving about the trigger does not trigger a
   check at each crossing (2026-11-27's 3 of 34 trains unjoined, whose ADDED share is not recorded,
   3.8, would sit at Q9's recommended 3 only if none was ADDED); what ends one is Q9's. The 2026-11-03 shape, held trip ids that overlap the new ones nowhere,
   gives the mismatch rule nothing: on a stand-in of 50 new ids against 131 held, the app's decode
   read joined 0, checked 0, and `behind_run` returned `None` (measured for this design, 2.6); the
   code says so of itself: "WHAT IT CANNOT SEE is a schedule behind by misses alone"
   (`backend/models.py:1348-1351`).
3. **The start of a stop run** (3.8): the first decode past Q8's trigger threshold. The design
   proposes that a stop run, like a MISS run, then holds until a decode says otherwise, so a count
   moving about the threshold does not trigger a check at each crossing; what ends one is Q8's.

A fourth trigger serves every archive with a span: **validity**. A check is triggered at once when
today in New York first comes within a band of a generation's `service_through`, and again when
today first passes it; otherwise the archive stays on its timer, and the trigger is not evidence
that keeps rungs going. A generation promoted already past its span is checked on its timer: PATH's
runs 20250522 to 20260601 (3.1) and its publisher has not republished since 2025-06-06 (the table
below), so a trigger that fired at every check while a span was past would never stop for it. The
band, and whether the trigger fires once per crossing, are Q19's. The F07
witness held a calendar-expired NJ Transit archive at HTTP 200 across 40 poll cycles with `/healthz`
passing (`docs/reviews/audit-2026-09-05/f07_static_never_refreshes.py`, run for this design at
`16d1c19`, exit 0, 2.8); the monitor already bands NJ Transit's span at 30 days
(`NJT_SERVICE_WARN_DAYS`, `backend/scripts/contract_monitor.py:487`, `:2066-2070`).

**Backoff.** When a triggered check finds the publication unchanged, or promotes a new one and the
trains still contradict it, the worker retries on rungs. A behind, MISS or stop run that starts under a
generation the worker has just promoted takes the next rung, not a check at once, since the check
that promoted it read the publication moments before (4.3). The realtime feed can lead the schedule
endpoint: run 445 (42889072568, created 2026-11-10 19:49:15 UTC) counted 41 identity warnings
against the schedule it had just downloaded, at least 5 and at most 41 of them number mismatches
(the monitor of `4489202` counted three kinds of warning, 2.3), and the next run's download agreed
(`backend/feeds/njt.py:602-605`). From the monitor's logs (2.3), the realtime lead on 2026-11-10 was
under 32 h 10 min 48.48 s (under 23 h 11 min 32.74 s if run 444, 42775373334, created 2026-11-10
07:27:05 UTC, counts as a witness) and greater than minus 19.12 s, so a lead of zero is not
excluded; on 2026-11-27 it lay between minus 11 h 28 min 26.37 s and plus 23 h 44 min 54.22 s (plus
12 h 41 min 10.89 s if run 479, 45967989070, created 2026-11-27 08:44:18 UTC, counts). The design
proposes rungs that rise from a first rung of minutes to a ceiling no longer than the archive's own
timer period, jittered as the warmups' are (0.9 to 1.1 of the rung, `backend/warmups.py:111`), and
kept going while any of the evidence holds: a behind run is open, a MISS run is open, or a stop
run is open. The archive returns to its timer only once none holds: the
behind run ended by a decode that can judge saying in step (`backend/feeds/njt.py:662-663`), or
never begun; the MISS run ended (Q9); and the stop run ended (Q8). The numbers, and
whether a refused or failed NJ Transit check climbs the same rungs, are Q3's; a public archive's
refused or failed check, and a component with no generation, are Q20's; for scale, the warmups' rungs are 15, 30, 60 and 300 s
(`backend/main.py:163-165`), sized for a process that has no generation at all.

**The other direction.** The same bounds allow the API to have moved first: on 2026-11-27 by up to
11 h 28 min 26.37 s, on 2026-11-10 by under 19.12 s (2.3's table). A check in that window, by
timer or by validity, finds a publication the trains are not on yet. It passes every gate of 3.3,
since NJ Transit's checks members, rows and the calendar's end and nothing of the trains
(`backend/njt_static.py:665-679`), and is promoted. The trains then contradict the new generation,
a behind run starts, and its rungs find the same publication until the realtime moves, so the
window is bounded by the API's lead. Nothing in the process returns to the generation the trains
agree with; a redeploy in that window loads the same publication, and the monitor's own decode
disagrees too, so `production:njt-schedule` WARNs rather than FAILs
(`backend/scripts/contract_monitor.py:3633-3639`). Today's process in the same window serves the
old publication rightly and is then behind from the realtime's move until a redeploy; the design
moves the wrong window earlier and bounds it by the lead. On 2026-11-03's shape the wrong table
would be the new one: arm B of 2.6b, old realtime under the new table, reads 682 of 917 pairs above
160 km/h and names another station as the last stop on 63 of 90 trips. Q8's withholding covers
those names, not the positions or the boards. Whether a refresh keeps the live generation while the
trains agree with it and contradict the candidate is Q21.

**A component that failed at the cold start** has no generation, and its archive is checked on the
failure rungs, whose numbers are Q20's, so the process recovers it without a
deploy. That is F06's acceptance, "recovers
without a deployment" (audit-2026-09-05.md:206), and A05's. Today a railroad system
that failed is never retried once the other reached `ready` (`backend/warmups.py:226`; the F06
witness, `docs/reviews/audit-2026-09-05/f06_partial_startup_no_recovery.py`, run for this design at
`16d1c19`: 1 attempt per system across 100 shortened intervals, 2.8). Section 6 builds it in Step 4.
A whole archive that fails at the cold start keeps its warmup's own loop, 15, 30, 60 and then 300 s
(`backend/main.py:163-165`), since 4.2 hands an archive to the worker only where its warmup returns
after a success; the failure rungs are for what a warmup leaves behind, a railroad system or a bus
borough.

**The cadence each timer has to meet (2.2), for Q2.** The monitor sees a change only when a field it
prints changes (2.2), so "visible" below undercounts.

| Archive | Changes visible to the monitor, runs 400 to 481 (812.28 h) | Last-Modified, read 2026-11-28 | A check costs |
| --- | --- | --- | --- |
| NJ Transit (the API) | 6 dated changes and 1 dated only to runs 425 to 434; from 2026-11-10 on, no two visible changes less than 24.09 h apart (2.2) | none sent | one getGTFS POST, no getToken |
| subway | 1, between run 410 (40693489876, created 2026-10-28 13:06:47 UTC) and run 411 (40781701629, 22:39:58 UTC) | 2026-10-28 16:39:52 UTC | a 304 |
| LIRR, Metro-North | 0 | 2026-11-25 22:10:39 and 2026-11-26 17:30:32 UTC, both inside the window | a 304 each |
| PATH | 0 | 2025-06-06 17:54:51 UTC | a 304 |
| ferry | 1, between run 424 (41399145027, created 2026-11-02 06:19:05 UTC) and run 425 (41531218367, 21:45:18 UTC) | 2026-11-27 06:25:01 UTC | a 304 |
| bus, six archives | no monitor line | 2026-10-16 15:14:41 to 15:14:45 UTC | six 304s |

The process the 2026-11-28 Redeploy replaced had held its archives since 2026-11-23 02:29 UTC
(deployment record 7988955096) through the LIRR, Metro-North and ferry uploads above; whether their bytes changed from what it held is unknown (GitHub's deployment statuses, read for this design, 2.1; the uploads, 2.2).

**Questions this leaves:** Q1 (the NJ Transit trigger: the evidence, a timer, or both), Q2 (each
archive's timer), Q3 (the rungs and their ceiling), Q20 (the rungs of a component
with no generation, and a public archive's refused or failed check), Q19 (the
validity band, and when the trigger fires again), Q21 (whether a candidate the
trains are not on yet is held back); the evidence thresholds, and what ends a stop or a MISS run,
are Q8 and Q9 (3.8).

### 3.3 Validation

A refresh runs, on the staged bytes and before the builder, every gate a cold start runs: the
always-gate (`backend/static_shared.py:645`), the decorative gate where the archive has one (the
subway's and NJ Transit's `shapes.txt`, the railroads' `stop_times.txt`, handed to `staged_fetch` at
`backend/static_data.py:313`, `backend/njt_static.py:839` and `backend/railroad_static.py:195`; PATH
and the ferry pass none, `backend/path_static.py:137-145`, `backend/ferry_static.py:130-145`), and,
inside NJ Transit's always-gate, its service-date guard (`backend/njt_static.py:669-679`). A refresh
never promotes what a cold start would refuse.

**The decorative gate in replacing mode.** The two-mode rule decides by whether a cache that draws
is there to keep (`backend/static_shared.py:647-664`). If one is, a publication whose decorative
member fails is refused; if none is, the publication is promoted with the member recorded as
degraded, "since nothing is lost" (`:598-605`). For a refresh the thing to keep is the live
generation, so the design proposes that `cache_serves` means "the live generation's decorative
member draws" (its `degraded_members` does not name it). In the ordinary case that is replacing
mode, and a decorative failure refuses. When the live generation was itself promoted without its
lines, a publication no worse is promoted, as the rule promotes one over no cache today.

**A refused or failed refresh keeps the live generation untouched**, because nothing was assigned,
and records why on its archive (3.5). The worker writes that record itself, outside `staged_fetch`,
so an attempt cut off by a deadline is recorded too. Today a deadline that expires inside
`staged_fetch` arrives as a `CancelledError`, which skips `_record`
(`backend/static_shared.py:665-670`): with a 0.05 s outer deadline around a 0.3 s validator the
archive's record held no entry at all (measured for this design, 2.8). A builder that raises is a
refusal (3.1). Two subway loaders do not raise: `load_subway_stations` returns `{}` and
`load_subway_route_shapes` returns `[]` on any exception (`backend/static_data.py:453-455`,
`:543-545`; 2.4.2). Both read members the gate has just read with the same parsers
(`backend/static_data.py:164-168`, `:254`), so, where the gate passed, only a transient fault reaches them; reused in a
builder, such a fault would promote, over a live generation of 496 stations and 35 polylines
(3.1's table), one with no station markers or no lines, the second the replacement F08 names
("Bad subway geometry can replace known-good geometry", audit-2026-09-05.md:220), with
`degraded_members` empty because the gate passed. Its `content` would equal the publication's, so
every later check would read `unchanged` and build nothing until the MTA republished (once in runs
400 to 481, 3.2's table). The design proposes that in a refresh a lenient loader's empty result
is a refusal, `load_subway_route_shapes`' only for a `shapes.txt` the gate passed, since by design
an archive whose `shapes.txt` fails the geometry gate can be promoted with that loader returning
`[]` (`backend/static_data.py:503-509`). The subway's unreadable-cache arm, which unlinks the live cache on a parse error at
load (`backend/static_data.py:291-300`), has nothing to unlink under the design: the builder reads
the stage.

**Two gaps the measurement found are posed, not fixed.**

1. **A publication not yet in service is accepted.** NJ Transit's guard refuses a calendar that has
   ended and has no earliest-date or runs-today check (`backend/njt_static.py:669-679`). With the
   committed fixture shifted 132 days (span 20261228 to 20270910, 0 services scheduled today), the
   real `validate_njt_publication` accepted it at the real clock (measured for this design, 2.8); a
   calendar whose only added day was 20270127, with today 20261128, was accepted too (the same). A
   refresh that promoted such a publication would hold a schedule with no service on the day it was
   promoted.
2. **A complete archive with trailing bytes is accepted.** Through the real `validate_archive` with
   `validate_njt_publication`, 65,535 trailing zero bytes were accepted and 65,536 refused, on the
   public stand-in and on the zipped fixture alike, and the 33-byte `{"errorMessage":"Invalid
   token."}` was accepted (a binary search for this design, 2.7). A cut body never is: every strict
   prefix checked was refused, 73,001 of 73,001 of the public stand-in and 79,091 of 79,091 of the
   zipped fixture (2.7). Under `content` (3.1) trailing bytes do not change an archive's identity.

**A held generation past its own span.** What the process serves when its generation's service has
ended and no accepted replacement exists is not changed by this design. Today it serves it: the F07
witness's expired archive answered `/api/njt-stops` with 164 stations at HTTP 200 under `public,
max-age=3600` while `/healthz` passed (2.8), where a cold start would refuse it. The design proposes
that the refresh code of 3.5 names that state.

**Questions this leaves:** Q12 (whether a refresh refuses a publication not yet in service, and keeps an expired one), Q13 (the trailing bytes) and Q4 (the code that names a refresh refused, failing, or holding an expired generation).

### 3.4 Budget: borrow, never mint

**The rule as recorded, and what the code does.** The ledger states it twice: "A refresh reuses the
live token (item 6), so it costs no mint" (ledger:693-694), and "NJ
Transit's refresh reuses the live token rather than minting, since a refresh that mints spends the
budget item 1 exists to protect" (`:927-929`). Today's path does not keep it (2.7). The static
download goes through `njt_post` (`backend/njt_static.py:773`) on the process-wide `TOKEN_CACHE`
(`backend/njt_auth.py:1348`, chosen at `:1417`), whose `get` mints when the token is absent or past
`MAX_TOKEN_AGE_S` (12 h, `:302`), and whose one re-mint branch follows a refusal of a token that is
not fresh (`:1451-1456`). Through the unwrapped shipped chain on a local fake token server (measured
for this design with a witness script whose every request went to 127.0.0.1, 2.7), a refresh sent 0
getToken POSTs on a token 6 h old and 1 s under the ceiling, 1 at the ceiling, 1 on an accepted
token refused, and 2 in one attempt, the most the code allows (`get` at `:1432` and `:1455` only). A
mint cancelled by its caller's deadline stays counted against the allowance, which is spent before the POST (`:1274`), and opens a 600 s hold (`:1281-1296`); the code's comment says the POST "had most likely gone out and been charged" (`:1284-1285`), and whether NJ Transit charges it is unknown (below). On the
fake, 3 getToken POSTs where the ceiling alone takes 2 (2.7). At today's deadlines (120 s for the
transfer, 45 s for the trains poll, 20 s for an alerts fetch) a poller meets that case first, and
the path left to a refresh alone is a re-mint begun late in its 120 s window (2.7).

**The proposal.** The refresh never calls `get(_mint)`. It borrows: it reads the token that
`_live_token` would return (`backend/njt_auth.py:1022-1033`), present and under `MAX_TOKEN_AGE_S`,
and posts with it once. If there is none, or the post is refused, the check ends `deferred` (3.2)
and nothing else happens: no mint, no `invalidate`, no `confirm`. The trains poll calls `get` itself
every `POLL_INTERVAL_S` (20 s) while NJ Transit is ready (`backend/feeds/njt.py:1066-1067`;
`backend/pollers.py:1095`); it spends the mint it would spend anyway, or meets the same refusal and
re-mints once as it does today, and the refresh borrows the new token after that poll. Not
confirming matters as much as not minting: `confirm` runs on any 200 before the body is validated
(`backend/njt_auth.py:1434-1436`), so today a truncated close-delimited 200 counts as the token's
acceptance (2.7); a borrowed post leaves the token's state as the pollers left it. The refresh's
getToken count is then zero by construction, not by timing, and the refresh-specific +1 of 2.7
cannot happen, because a refresh never holds a mint in flight for its deadline to cancel.

One variant is proposed against. While a hold runs the cache serves its token whatever its age
(`backend/njt_auth.py:1087-1098`, `:1252-1256`), and a refresh on that over-age token sent 0
getToken POSTs (2.7). The rule above does not borrow through a hold, so a refresh defers until the
hold ends, which for an allowance or quota hold is the next Eastern midnight plus 60 s
(`backend/njt_auth.py:1107-1112`, `:1164-1165`); in the allowance case measured, 23,972.5 s of the
hold remained, a length set by the hour that run's clock read (`m9_token_during_refresh.py` starts
its fake wall clock at the real `time.time()`, its line 247). Borrowing it would mint
nothing either. 5.1 lists it so a decision can reverse it.

**What a download can meet mid-transfer**, measured for this design on the local fake (2.7; the
`Content-Length` and close-delimited cuts were repeated, cut at a third, with the real 3,295,223-byte public stand-in as the body, `v9_witness.py` case V4; the chunked cut ran on the 79,091-byte zipped fixture only, and the trailing invalid-token JSON was checked against the public archive by the validator alone):

| Case | Outcome | Token after | Promoted |
| --- | --- | --- | --- |
| 200 with `Content-Length`, connection cut part way | `NjtUpstreamError` (`RemoteProtocolError`) | kept, no hold, 0 getToken | no; stage removed |
| 200 chunked, cut with no last chunk | the same | kept | no |
| 200 close-delimited, cut | staged as a 200, then refused: "not a readable zip archive" | kept, and today `confirm` has already run | no |
| 200, the whole archive, then the invalid-token JSON | accepted | kept | yes, with 33 trailing bytes (3.3) |
| the token killed after half the body, the connection cut, a poll beside it (E1) | `NjtUpstreamError` at 0.407 s | the poll re-minted once | no |
| the same with the body finished (E2) | ok at 1.048 s | the poll re-minted once | yes |
| 6,000 bytes in 300-byte pieces 0.3 s apart under a 1.5 s deadline (T7) | `TimeoutError` at 1.503 s | kept | no |

Over a promoted cache the three cuts left it byte-identical (2.7). `REQUEST_TIMEOUT_S` (30 s,
`backend/njt_auth.py:254`) bounds each read, not the request: 3,000 bytes in ten pieces 0.3 s apart
passed a 1.0 s timeout in 2.728 s (2.7). The bound on the whole transfer is the
`asyncio.timeout(deadline_s)` around the POST (`backend/njt_static.py:772`), 120 s by default.

**The proof.** Step 1 is accepted only with a witness that counts getToken POSTs on a local fake
token server through the shipped chain, in the pattern of the witness 2.7 used: every request to
127.0.0.1 behind a host guard, the credentials asserted empty before fakes are set in-process. It
prints the count per case, and must show:

- 0 getToken POSTs from the refresh on a token under the ceiling;
- at the ceiling, the refresh deferring and the next poll sending the 1 POST it sends today;
- a token refused on getGTFS: the refresh sends nothing more, and the poll re-mints once;
- a token killed mid-download under `Content-Length`, chunked and close-delimited framing: each
  check refused or failed, the live generation byte-identical, and 0 POSTs from the refresh;
- the trailing-bytes case, as 3.3's question rules it;
- a refresh whose transfer deadline expires, with no mint of its own in flight to cancel.

Its named mutations, each a one-line change the witness must catch: a refresh that calls
`get(_mint)`, one that calls `invalidate` on a refusal, and one that calls `confirm` on its 200. `confirm` changes nothing on a token a poll has already seen accepted (`backend/njt_auth.py:1227-1231`), so the witness catches that mutation only if the close-delimited cut also runs on a borrowed token no poll has yet seen accepted. 6.1 carries the six cases, that run and the three mutations.

**What remains unknown about NJ Transit's server** (2.2, 2.7; none of it probed, by rule): the
token's lifetime; whether an expiry is checked only at request start (E2) or can end a transfer in
flight (E1); how getGTFS frames its body, which decides whether a cut raises or is staged; whether
an abandoned getToken POST is charged; whether getGTFS answers HEAD, sends a validator or is
metered; and whether two downloads of one publication are byte-identical.

**Questions this leaves:** none of its own. Q2's NJ Transit period weighs the metering unknown, the borrow-through-a-hold variant is listed in 5.1, and Q18 asks how a deferral is counted.

### 3.5 What the operator sees

F07's remedy asks the process to "Report snapshot version, loaded time, valid-through date and next
refresh" (audit-2026-09-05.md:216). Today `/api/status` serves `static_archives`
(`backend/routes/status.py:224`) with four fields per archive (`backend/static_shared.py:456-465`;
`StaticArchiveStatus`, `backend/models.py:993-1017`), and none names a publication: there is no
ETag, Last-Modified, hash, `feed_version` or span anywhere in the app's record (a grep at `16d1c19`
for this design, 2.8). A key appears once its archive has been downloaded in this process, or once a cached check found the cache without its decorative member
(`backend/models.py:1168-1170`; `backend/static_shared.py:468-471`), so a boot over a warm cache whose members all draw publishes `{}` (the F07 witness, 2.8),
and the bus index has no key at all.

**`/api/status`.** The design proposes, per archive and for the bus index, from the cold start's
first assignment on:

| Field of `static_archives.<archive>` | Today | Proposed |
| --- | --- | --- |
| `last_promoted_at` | the rename (`backend/static_shared.py:483`, at `:693`), before the parse | the assignment: equal to `generation.promoted_at` |
| `last_download_error`, `failed_downloads` | the last download, and failures since a promotion | kept as they are |
| `degraded_members` | the module record's, written by a promotion or a cached check | the held generation's own |
| `generation` | none | the identity of 3.1 for the generation readers hold: F07's version (`content`, `sha256`), loaded time (`promoted_at`) and valid-through date (`service_through`) |
| `last_checked_at` | none | when the last check ended |
| `last_check` | none | `unchanged`, `promoted`, `refused`, `failed` or `deferred` (3.2; Q18) |
| `last_check_trigger` | none | `cold-start`, `timer`, `behind`, `miss`, `stop` or `validity` |
| `last_refresh_error` | none | the reason, sanitized as `describe_failure` sanitizes today (`backend/static_shared.py:498-510`) |
| `consecutive_failures` | none | refused and failed checks since the last `unchanged` or `promoted`; a deferral does not count (Q18) |
| `next_check_at` | none | the timer or the rung: F07's next refresh |

`njt_feeds` gains the generation its counts were decoded under (3.1) and the counts of 3.8.

**`/healthz`.** One new code, degraded and never gating, raised when an archive's checks have been
refused or have failed past a band, and when NJ Transit's held generation has passed its own
`service_through` with no accepted replacement (3.3). Today no code reads any `static_archives`
field or any archive's age (2.8). There are 11 codes, 3 of them gating
(`backend/models.py:1358-1370`, `:1404-1408`), and only a gating code turns the probe's 200 into a
503 (`backend/routes/status.py:828`, `:853`). The case for never gating is the one the code already
makes for every NJ Transit code: Railway reads the probe while a build is promoted, and "gating
would refuse the very build that fixes it" (`backend/models.py:1353-1355`). The code's name and its
band are Q4.

**The words that say "redeploy".** A redeploy stops being the fix, so the texts that prescribe it
change. `njt-schedule-behind`'s comment says "WHAT CLEARS IT ... means a redeploy"
(`backend/models.py:1343-1345`). The monitor's `production:njt-schedule` FAILs with "production's
schedule is behind NJ Transit's publication: redeploy" (`backend/scripts/contract_monitor.py:3651`),
its two WARNs reason from what "a redeploy would" do (`:3637-3638`, `:3644-3646`), and its note on
the code says "production:njt-schedule says whether a redeploy fixes it" (`:404-410`). The design
proposes that the FAIL reads "the refresh did not take" and names production's
`static_archives.njt.last_check`, `last_checked_at` and `last_refresh_error`, so a red run says what
the process tried; against a production that publishes no refresh fields (a revision before the
build) it keeps today's words.

**A new monitor check: production's held generation against upstream, for every archive.** Today the
only comparison of production with upstream is NJ Transit's trains against its schedule. For the
other archives the monitor parses upstream with the app's own parsers and compares nothing with what
production holds: its production section reads only `/api/status`, `/healthz`, `/api/njt-routes` and
one subway board (`backend/scripts/contract_monitor.py:2714`, `:2719`, `:2725`, `:2731`), and
nothing in the monitor reads `static_archives`: its one mention is prose
(`backend/scripts/contract_monitor.py:358`). Each run already downloads six of the archives on its
five static lines, NJ Transit's on its one token (`_NjtToken`, `:2074-2097`), so computing each
one's `content` costs no request and no mint. The bus archives, which the monitor does not download,
are S3 objects whose ETag is the body's MD5, so a conditional GET of each with the ETag production holds for that borough (`generation.boroughs`, 3.1) compares them, answering 304 with no body while they match. The design proposes the verdict: PASS when production's `generation.content` equals the run's; FAIL when they differ and production checked that archive after the run's download yet holds a generation promoted before it ("the refresh did not take", with `last_check`), or its last check was `refused` or `failed`, or it has not checked for more than one of its periods (Q2); otherwise WARN, either when production's generation was promoted after the run's download ("upstream moved during the run"; the runner's clock is set against Railway's here, and their skew is not measured) or when production's last check, within its period, came before the run's download ("production has not checked since"). Step 0's line (6.0), the run printing the identity of what it downloaded, is the first half of this check.

**Questions this leaves:** Q4 (the code's name and band, degraded or gating) and Q17 (whether Step 0 comes first).

### 3.6 How the operator is told

A red run alone is not enough, by 1.3: when Ben answered at 2026-11-28 12:43:47 UTC (07:43:47 Eastern), neither GitHub email had arrived, at least 15 h 18 min 0 s after run 480 ended and at least 4 h 44 min 54 s after run 481 ended, and which account GitHub notifies for a scheduled workflow is not measured. Nothing on the platform reads `/healthz` once a deploy is
live (`backend/models.py:1376-1383`), and "nothing reads `/healthz` on a schedule except the
monitor" (record:666). On 2026-11-27 the probe carried
`njt-schedule-behind` by 20:23:00 UTC (15:23:00 Eastern; record:648), 1 h 2 min 44 s before run 480's FAIL line at 21:25:44 UTC (record:652), and still carried it on run 481's read (record:692), because a
thin night's decode neither ends nor lengthens a behind run (`backend/feeds/njt.py:651-658`). And the monitor runs 2.182 times a day, not the four its cron asks for, its scheduled runs a median 11 h 5 min 56 s apart and at most 16 h 9 min 49 s (1.2).

| Option | What it would have caught on 2026-11-27 | Its delay is bounded by | What it needs | What it cannot see |
| --- | --- | --- | --- | --- |
| (a) a push from the monitor on any FAIL | run 480's FAIL at 21:25:44 UTC | the run cadence: median gap 11 h 5 min 56 s, longest 16 h 9 min 49 s | a workflow step and a secret for a push service | anything between runs |
| (b) an external poller of `/healthz` that alerts on a nonempty `degraded` | the code by 20:23:00 UTC | its own period | an outside service that reads the body: a degraded code rides a 200 (`backend/routes/status.py:853`) | what only the monitor compares, production against upstream (3.5) |
| (c) a GitHub issue opened on FAIL | run 480's FAIL | the run cadence | `issues: write`, where the workflow grants only `contents: read` and says "There is deliberately no issue-creation or other automation here" (`.github/workflows/contract-monitor.yml:14-15`, `:38-39`) | anything between runs; its notice rides GitHub's notifications, the path 1.3 could not measure |
| (d) Railway's own alerts | nothing: Railway reads the probe only while promoting and restarts only on a process exit (`backend/models.py:1376-1383`) | none applies | the plan's alert features, unread (the Railway CLI is logged out) | a degraded code in a live process |

The recommendation Q5 carries is (b) first, set up by Ben outside the repository, and then (a). On the one incident where both signals existed, the probe said so an hour before the monitor's run and held the code through the night; (a) carries what the probe cannot, the monitor's comparisons with upstream, including 3.5's new check, and a push to a channel Ben chooses avoids GitHub's routing question entirely, where (c) rides it. (a) is recommended as its own small branch, before Step 1: the alarm's gap exists whether
or not anything refreshes, and the record's note already says "For its own branch; no fix here" (record:619).

**Questions this leaves:** Q5 (the channel, and whether it is built here or on its own branch).

### 3.7 The page

**Every static-derived endpoint and every live envelope carries the stamp of the generation or
generations it was built from**: a prefix of each generation's `content`, per archive. This is the
stamp the records owe the page: F07's remedy, "Let clients detect changed static versions and update
their registries safely" (audit-2026-09-05.md:216); A06's repair, "generation/version
identifiers ... ensure browsers discover changed static generations"
(ledger:267); the release table's "generation-aware frontend reload"
(`:375`); and Stage MR3's follow-up, which the code carries as "A FOLLOW-UP, NOT A FIX: a version
stamp on the static-derived endpoints" (`frontend/systems/shared.js:2147-2149`), though the question that follow-up asks, whether a payload predates a field, is the build's, and a stamp of the publication does not answer it (4.7). F06 owes the reload
rather than the stamp: stations and routes that "appear in an already-open browser without a manual
reload" (audit-2026-09-05.md:206). A live envelope's stamp is the generation its rows
were decoded under (3.1), not the newest one, so a page first sees a new stamp on the poll whose
rows are new, and its tables and the rows change together. The stamp rides a response header on the
static endpoints, so their bodies keep the bare-list shape clients read (`backend/routes/njt.py:87`,
`:124`), and a body field on the envelopes and boards.

**Stamped URLs keep `max-age=3600` safe.** Today 11 of the 12 static-derived endpoints are served
`public, max-age=3600` with no validator, and `/api/bus-route/{route_id}` with no Cache-Control at
all (read from production for this design on 2026-11-28, the last, `/api/path-routes`, at 12:50:27
UTC, 2.5; `backend/cache.py:373`, `backend/routes/airtrain.py:22`). So a reload within the hour can
take an old table from the HTTP cache, which the code records in its own words: a returning rider
"runs this code on yesterday's payload" (`frontend/helpers.js:360-362`). The design proposes two
URLs per static endpoint. The bare one is served `no-cache` with an ETag that covers the body as well as the publication, the stamp joined to a hash of the serialized payload, so a reload
revalidates for a bodiless 304, as the page's own scripts already do (`backend/main.py:456-470`), and a deploy that changes what an endpoint builds from the same archive changes the ETag, as a deployed change to a script changes the ETag StaticFiles sets for it (`backend/main.py:462-464`). An ETag of the stamp alone would answer 304 to a body built by older code until the publisher next changed the archive, where today `max-age=3600` bounds that at an hour, and deploys have changed static payloads over an unchanged archive: `e22f8bf` left NJ Transit's routeless stops out of `/api/njt-stops`, `fc65a74` gave NJ Transit's routes payload a `short_name`, and `92636bc` gave `/api/subway-stops` its `complex_id` (`git show` of each). The
stamped one (`?g=<stamp>`) is served `public, max-age=3600` as today, which stays safe across publications because a new generation is a new URL. It names the publication and not the build, so after a deploy that changes a payload it can serve the older body for up to an hour, as every static URL can today. A stamped request naming a generation the process no longer holds is answered with the live generation under `no-cache`, its stamp beside it. The page boots on the bare URLs, since it holds no stamp yet, and fetches
every later table at the stamped URL a live envelope names.

**The reload.** When a poll's stamp for an archive differs from the stamp of the page's tables for
that archive, the page refetches the archive's static endpoints and replaces its registries in one
assignment, as the backend does. A stamp for an archive the page's tables did not carry counts as a
change, which is F06's case: a page that booted while Metro-North was failing holds railroad
tables without it. Today the page refetches no static endpoint once it has loaded it (2.5). The eleven loaders run once each through
`retryUntil`, which returns for good on its first usable answer (`frontend/map.js:281-291`,
`frontend/helpers.js:5480`). `njtStopCoords` is a `const` Map filled per station and never cleared
(`frontend/systems/njt.js:54`, `:162`). The station registry only grows
(`frontend/systems/shared.js:2808`, `:2816`). Each arrivals URL is built once from a static id
(`frontend/systems/njt.js:166`, `frontend/systems/subway.js:575`,
`frontend/systems/railroad.js:231-233`, `frontend/systems/path.js:106`,
`frontend/systems/ferry.js:94`). And the one fetch that bypasses the HTTP cache,
`fetchRoutesPayload`'s `cache: "reload"` (`frontend/systems/shared.js:2174`), fires only when a
field is missing from every entry, so a changed publication with the same fields never triggers it.
The A06 frontend witness measured the whole: 11 of 11 loaders made 0 requests in 25 simulated hours
while the backend restarted into a changed publication, and 0 of the changed coordinates reached the
map (`docs/reviews/audit-2026-10-12/a06_frontend_static_once.mjs`, run for this design at `16d1c19`,
exit 0, 2.5). The design's acceptance is that witness reversed.

**Joins only within one generation.** The page joins a live row to a table only when their stamps
agree: the NJ Transit glide (`frontend/systems/njt.js:478`), the "Also here" crosslink keyed `NJT|<id>` (`frontend/systems/njt.js:377`), and each system's route index, names and
colours (2.5). The station-alert join keyed `system|stationId`
(`frontend/helpers.js:5229-5235`), through which NJ Transit's live alerts land on the page's held
stop ids (2.5), has no stamp to compare: `AlertFeed` gains none unless Q24 says so (4.4), and an alert's stops are the publisher's raw `informed_entity` ids (`backend/feeds/alerts.py:349-354`), which no generation resolves (2.4.7). It reads the replaced registry (4.7), so until the reload lands an NJ Transit alert naming a renumbered stop lands on the station the old table held under that id, as today (2.5.3). Whether `AlertFeed` carries a stamp and the page joins alerts only while the stamps agree is Q24. An envelope whose stamp the page does not hold is drawn where the server placed it,
as a glide miss is today (`frontend/helpers.js:4787-4788`), until the reload lands. And each live
request that names a static id carries the stamp of the table the id came from
(`/api/njt-arrivals/{stop_id}?g=<stamp>`); the design proposes that the server answers a stamp other
than its rows' generation as stale rather than joining the id. A tab then never opens a board for an
id that now names another station: of the 172 ids production served from the 2026-11-01 archive, 101
named another station in the 2026-11-06 publication and 22 were gone
(ledger:610).

**Questions this leaves:** Q7 (whether the page reloads its tables silently or says so, and in which
words and where) and Q24 (whether alerts carry a stamp).

### 3.8 The two witnesses as guards and triggers

Both are NJ Transit's, read off the trains poll's own decode; neither needs a request.

**The MISS: a trip update whose trip id is present and absent from the held trips.** Today it is
counted nowhere that is served. The decode forms `joined` and `checked` and never the number running
(`backend/feeds/njt.py:930`, `:951-952`, `:1004`); `njt_feeds` has no miss or running count
(`backend/models.py:884-902`); and the miss is deliberately not a warning: "warning on ADDED would
train the operator to ignore the signal that matters" (`backend/feeds/njt.py:769-770`). An ADDED
trip carries an empty trip id and is keyed `njt:<entity.id>` (`backend/feeds/njt.py:853-858`; 40 of
40 ADDED trip updates in the committed capture and a recorded one, 2.6), so in every update measured the id alone told a stale miss from an
ADDED train. The id is not the relationship, though: an ADDED trip that carried a trip id would read as a miss. Measured for this design (2.6):

| Shape | Non-ADDED misses |
| --- | --- |
| 2026-11-03, a stand-in: 50 realtime ids of 1 to 4 digits against 131 held ids of 7 | 50 of 50; the decode joined 0 and checked 0, and no behind run began |
| 2026-11-03, production's trains at 2026-11-07 00:19 UTC (PR #137's body) | 56 of 56, none of them ADDED: every one matched the new `trips.txt` on its trip id, which an ADDED train's `njt:` key cannot |
| 2026-11-22, 50 sampled trips (`backend/tests/fixtures/njt_drift_2026-11-22.json`) | 8 of 50 (16.0%); per poll, at train level, 5 of 37 (13.5%) to 6 of 36 (16.7%) |
| 2026-11-27, production's wire at 20:24:02 UTC (record:649) | 3 of 34 trains unjoined (8.8%), whether ADDED not recorded |
| steady state: the committed pair, a recorded 164-update live capture, five production reads | 0 in every one; the 4 of 90 running trip updates and the 36 of 164 trip updates (CANCELED included) that missed were all ADDED, and 0 of 29, 73, 2, 6 and 8 placed trains were unjoined |

The design proposes that the decode counts `running` (trip updates kept after CANCELED and DELETED
are dropped, `backend/feeds/njt.py:941`), `missed` (non-ADDED) and `added`, served on `njt_feeds`, telling an ADDED trip by the `schedule_relationship` that same line reads, with the empty id checked beside it.
Past a threshold on `missed`, a MISS run starts, which triggers a check at once (3.2). Past a threshold on `missed` and on `missed` over `running`, it also
raises a code (Q9 poses the first as a count in one decode, the second as a count and a share), and the design proposes that the code be `njt-schedule-behind` itself, its run given
a second arm, so one code says "the held schedule is not the feed's" for both shapes and the monitor
applies the same rule to production's counts. One legitimate non-ADDED miss is known: a publication
boundary crossed between the realtime and static downloads, which the fixture generator allows as "a
handful of misses" (`backend/scripts/gen_njt_rt_fixture.py:174-176`). It is the very moment a check
is wanted. The thresholds, for the trigger and for the code, are Q9: the steady state measured 0,
and the smallest incident share measured 8.8% of trains.

**The stop witness: stop ids the held table lacks, and implied speeds under its coordinates.** Today
`_ordered_calls` drops a call whose stop id the table lacks before anything counts it
(`backend/feeds/njt.py:249-250`). Measured on the one realtime snapshot with per-call times
(2026-09-01 22:36:46 UTC) under the old and new tables, measured for this design (2.6):

- **Absent ids.** 0 calls in every right arm; in the 2026-11-03 shape (old table held, realtime on
  new ids, a stand-in), 215 of 1,231 calls, 20 distinct ids, on 85 of 90 trips.
- **Pairs above 160 km/h.** 14 of 1,130 (1.2%) in the right arm; 603 of 768 (78.5%) in that wrong
  one.
- **Per trip.** No threshold separates without overlap on realtime times: 9 of 90 right trips have a
  pair above 160 km/h, and with absent calls dropped first, as the decoder drops them, 2 of 90 wrong
  trips stay at or under it (87.4 and 91.7 km/h). The two signals together mark 90 of 90 wrong trips
  and 9 of 90 right ones.
- **The rider's cost.** Under the wrong table the last-stop rule names another station for 62 of 90
  trips.

One snapshot is n = 1: how the right table's tail varies across days is unmeasured. The 2026-11-12
and 2026-11-27 incidents moved trip ids, not stop ids, and by the counts the stop witness would have
read both as right (an inference from the stop ids' move counts, 2.6).

The design proposes that the decode counts calls naming an absent stop id before dropping them, and
pairs above a speed, and serves both on `njt_feeds`. Past Q8's trigger threshold for a decode, a stop run starts, which triggers a check at once (3.2); and a refuted or unjoined train that either mark names, a call at an absent stop id or a pair above Q8's speed for the mark, takes the fallback `_destination` already has, the line name and train number
(`backend/feeds/njt.py:723-730`), rather than the held table's last-stop name. That answers PR #145's open note, "The last stop is named through the held table,
so a refuted or unjoined train reads another station's name", which the code states of itself: "THE
NAME IS THE HELD SCHEDULE'S" (`backend/feeds/njt.py:690-692`). Whether the witness also raises a
code, its thresholds for the trigger, the code and the mark, and what ends a stop run, are Q8.

**Questions this leaves:** Q8 (the stop witness: its trigger and what ends a stop run, whether it also raises a code, their thresholds, and the per-trip mark and its speed), Q9
(the MISS thresholds, for the trigger and for a code).

### 3.9 Cold starts and a volume

**The design needs no volume.** Every deploy is a cold start: "the data directory does not persist,
confirmed against production's promotion stamps of 2026-10-13" (`backend/static_shared.py:598-600`),
and "On Railway the disk is wiped every deploy" (`backend/bus_static.py:41-42`). A cold start
downloads every archive as today, runs the same gates in their cold mode (no live generation, so a
decorative failure degrades, 3.3), builds each generation off the loop and assigns it. That
assignment is the archive's first `promoted` check, and its timer starts from it.

**The cold-start baseline (2.1).** The eight cold starts from 2026-11-01 to 2026-11-28 took 69 to 93 s from `in_progress` to the first promotion on the six before 2026-11-23 and 28 s on both of `758c0f6`'s, 34.7 to 100 s to NJ Transit's promotion, and 13 to 41 s from the first promotion to the last, and Railway recorded `success` 6 to 34 s before the last archive was promoted, since a loading group is not degraded (`backend/routes/status.py:776-777`). With the builds off the loop the probe
answers through the warmup without the stalls of 3.1.

**The hand Redeploy stays documented as the fallback**, with its measured cost. On 2026-11-28,
`in_progress` at 08:00:16 UTC (03:00:16 Eastern) to NJ Transit's promotion at 08:00:50.670 UTC was
34.7 s (record 7988955096; record:657, :658); on 2026-11-22,
`in_progress` at 23:47:21 UTC (18:47:21 Eastern) to 23:48:52 UTC was 91 s (record 7730268879's
statuses; ledger:589). The new process mints at its start (the allowance
read 1 of 4 at 08:33:51 UTC, record:662), every open tab keeps its tables (3.7), and every
live state starts over.

**What a volume would change** is Ben's open call. A cold start could serve the last good generation
from disk while its first check runs, which needs the identity of 3.1 written beside the bytes. A
deploy during an upstream outage would keep a schedule: today a cold start whose download fails
returns `{}` for NJ Transit, PATH and the ferry, `None` for a railroad system, and raises for the
subway, then retries (`backend/njt_static.py:1289-1291`, `backend/path_static.py:526-528`,
`backend/ferry_static.py:439-441`, `backend/railroad_static.py:535-537`,
`backend/static_data.py:412-415`). And the 30-day rule would be reachable again. That rule is
`MAX_AGE_DAYS = 30` in six modules, tested only inside the loaders (2.8), and in production it
changes nothing: a cold start always downloads and a running process never re-checks (2.8). Under
the design the timer does its work in a running process, so the rule would matter only to a start
over a warm disk. Whether a Railway restart after a process exit keeps the data directory
(`restartPolicyType` `ON_FAILURE`, `restartPolicyMaxRetries` 10, `railway.json`) is unmeasured, and
it is the only path today to a warm-disk start (2.8).

**Questions this leaves:** Q10 (whether the 30-day rule survives), Q11 (the volume: none, as written
here, or one, and for what).

### 3.10 Memory

The design proposes one refresh at a time, serialized across the archives and the bus index by the
one worker of 3.2, with each build awaited to its end (3.1). Once the cold start's loads have ended, the peak is then the live set plus one
archive's new generation, which overlaps its old one until no live row carries the old one: the
first successful poll under it, later while that family's polls fail (3.1). The one worker may start
the next archive's build in that time, so while a family's polls fail its old generation adds to
that peak. During the cold start that bound is not shown. The lifespan starts the five warmups and the bus build together (`backend/main.py:343-357`), each warmup retries until it succeeds (`backend/warmups.py:164`, `:215`, `:288`, `:341`, `:414`), and each hands its archive to the worker where it returns (4.2), so a check that NJ Transit's first decodes trigger (3.2) can build beside the others' loads. That adds a build whose stand-in peaked at 99.5 MB under tracemalloc (below) to a cold start whose peak with everything loading at once was not measured, since 2.1 loaded the sets one archive after another. Whether the worker waits for the cold start is Q25.

**What was measured (2.1).** Two whole sets in one process, loaded one archive after another with NJ Transit's public stand-in over cached files, on this machine: the second set added 178,495,859 bytes under tracemalloc (178,470,338 in the second harness) and 201,212,072 to 202,522,768 bytes of macOS footprint over three runs. One archive alone in a fresh process, retained after gc (tracemalloc): NJ Transit's public stand-in 88,470,849 bytes, its rail-only shape 62,196,276, Metro-North 45,558,075, PATH 22,879,434, LIRR 17,459,689, the subway 3,766,401, the ferry 599,558 and the bus index 86,563 (its geometry is on disk). The tracemalloc peak during gate and load was largest for NJ Transit's stand-in, 99.5 MB, then Metro-North's 46.9 MB and the subway's 39.7 MB (99,531,820, 47,030,357 and 39,954,958 bytes in the second harness). So with one refresh at a time the addition one build makes is bounded by NJ Transit's, and the structures 3.1 drops shrink it; each old generation that a failing family's polls keep alive (above) adds its retained size beside that build, NJ Transit's stand-in's 88,470,849 bytes the largest, a sum no harness measured in one process. None of these is Linux resident memory, and the API archive may cost more than its stand-in: its last recorded download was 4,369,879 bytes (ledger:653) against the stand-in's 3,295,223, and its own parse and build were never measured, since reading it takes a mint (2.1).

**The deployment's memory limit is unknown**, and its reading is pending (2.1): nothing in the repository states a plan or a limit, and the Railway CLI still answered Unauthorized at 12:50 UTC (07:50 Eastern) on 2026-11-28. The number goes in 2.1 and here when it arrives.

**Questions this leaves:** Q6 (the ceiling above which a refresh is not attempted, and what is done
instead) and Q25 (whether the worker waits for the cold start's loads).

---

## 4. What changes where

Every subsection ends with the line PR #108's section 4 ended every subsection with: **what is
asserted today that this design makes false.** A design that lists only its additions hides the
places where something correct today stops being correct. Code is cited at `16d1c19`. The names
this section writes that the code lacks are this design's proposals, each listed in 5.1 or posed in 5.2:
`app.state.static[archive]` (3.1; 5.1 item 7), the identity of 3.1 and its fields, such as
`service_through` and `promoted_at` (5.1 item 2), the `/api/status` fields of 4.5's table (3.5;
5.1 item 27), the envelopes' `static_generations` (3.7; 5.1 item 30), the stamped URL's `?g=`
(3.7; 5.1 item 31) and Q4's code, `static-refresh-failing` (4.6; Q4). Every other name is the code's, or HTTP's for a header.

### 4.1 The loaders and builders

| File | Change |
| --- | --- |
| `backend/njt_static.py`, the build | A pure builder over today's parse (`_parse_zip`, `:1001`, called inline at `:1301`) and the six builders the warmup runs inline after it (`backend/warmups.py:448-473`). It takes the archive's bytes or a path and an injected clock, writes no `app.state`, opens no socket, and returns one frozen generation: the served stops, the station routes, the route lines, the trip index, the line names and the stop schedule, with the identity of 3.1, whose `service_through` is read by the gate's own `service_span` (`backend/njt_static.py:682-715`), so the valid-through date the operator reads and the one the gate judged cannot differ. The whole parse, `app.state.njt_static` today, is not kept (3.1; Q14), and the stop schedule is kept unless Q14 drops it. |
| `backend/njt_static.py`, the transfer | `_download_via_token` posts through `njt_auth.njt_post(url, {})` (`:773`), which mints on its own (below), and writes the buffered body on the serving loop (`:774`). The refresh's transfer takes the borrow-only path below and hands the body to the builder's worker thread; the file it writes there is a cache of the bytes, not the state (3.1). |
| `backend/njt_auth.py` | A borrow-only read of `TOKEN_CACHE` (`:1348`): the live token when `_live_token` (`:1022`) finds one under `MAX_TOKEN_AGE_S` (`:302`) and no hold is running (`:1252-1256`), and nothing otherwise; and an `njt_post` mode that never calls `get(_mint)`, never calls `confirm` on a 200 as `:1434-1436` do today, and on `is_auth_error` (`:529-584`) neither invalidates nor re-mints, as `:1451-1456` do today, but raises a deferral (3.4). |
| `backend/static_data.py` | The five subway loaders read `SUBWAY_GTFS_ZIP` (`:34`) by module path (`:378`, `:449`, `:513`, `:667`, `:771`), so a subway generation can be built today only from the promoted file. The builder takes the path, so it reads the stage before anything is promoted, and the monitor stops reassigning the module constant to parse a download (`backend/scripts/contract_monitor.py:1681-1682`). In a refresh the subway builder refuses the candidate, rather than building a generation, when `load_subway_stations` returns `{}` (`:453-455`) or, for a `shapes.txt` the gate passed, `load_subway_route_shapes` returns `[]` (`:543-545`) (3.3; 5.1 item 39). |
| `backend/railroad_static.py`, `backend/path_static.py`, `backend/ferry_static.py` | Builders over today's inline parses (`railroad_static.py:546`, `path_static.py:535`, `ferry_static.py:450`) and the warmups' inline derives (`backend/warmups.py:252-273`, `:309-325`, `:360-371`). One generation per railroad system, so LIRR and Metro-North swap and retry apart (Step 4). `railroad_static` and `path_static` are not kept (3.1; Q14). The ferry generation keeps its trips and routes tables, because the ferry decoder joins them on every poll (`backend/feeds/ferry.py:111`, `:406-407`). |
| `backend/static_shared.py` | `_stream_to_file` (`:526`) sends no conditional header (`:537-538`). The refresh's transfer sends `If-None-Match` and `If-Modified-Since` from the live generation's identity, reports `unchanged` on a 304, and on a 200 compares the body's `content` with the live generation's before anything is built (3.2). A changed body runs `staged_fetch`'s gates as a cold start does (`:645`, `:649`), with `cache_serves` meaning what 3.3 proposes, that the live generation's decorative member draws: replacing mode (`:647-664`) in the ordinary case, and the cold-start rule (`:598-605`) when the live generation was itself promoted without its lines or there is none, as for a component that failed at the cold start (3.2; 5.1 item 22). |
| `backend/bus_static.py` | Route files are replaced one at a time inside the directory requests read (`:169-176`, `:312-325`), and a route a newer publication drops keeps its old file, since the build sweeps only `*.part` (`:225-226`). One directory per generation instead, written whole and then named by the reference (3.1). How one borough's change builds a whole directory, and whether a refresh with a failed borough is `failed`, are Q23's (3.1). |

**Why, measured.** The builders move into a thread because today's inline stretches hold the serving loop, NJ Transit's for 2,813.1 ms at its longest (2.1); each is one unit because a builder that raises inside today's block leaves a mixed set under `ready` (2.4.9); and what is not kept, and what that saves by instrument, is 3.1's.

**Unchanged, and said so.** The tables in code that state facts about an archive stay code, so
no refresh updates them: `SUBWAY_ROUTE_GROUPS` (`backend/feeds/subway.py:72-103`),
`_PATH_DIRECTION` (`backend/feeds/path.py:45-50`), `_RAILROAD_DIRECTION` and the Grand Central
anchor (`backend/feeds/railroad.py:747`, `:755`), and `RAIL_BRANCH_CODES` (`frontend/helpers.js:3043-3070`). `subway-route-unmapped` remains the one that reads its own
drift.

**Asserted today that this makes false:** `backend/njt_static.py:1086-1088`, "the lines stay
gone until a later promotion whose geometry draws, which in production is the next deploy or
the age policy's re-fetch at MAX_AGE_DAYS", and `backend/railroad_static.py:504-505`, "a
partial result reaches ready and the missing system is never retried (F06, A05), so one EMFILE
would drop a system until the next deploy".

### 4.2 `warmups.py` and `main.py`'s lifespan

- **The initial values become one map.** The per-field initial values of every group
  (`backend/main.py:209-278`) give way to `app.state.static = {}`; until an archive's first
  generation is assigned a reader finds no entry and answers as `loading` does today.
- **The warmups become each archive's first check.** The five warmups
  (`backend/warmups.py:157-475`) build and assign the first generation as they load today,
  set `ready`, and then, where each `return`s now (`:199`, `:275`, `:327`, `:373`, `:475`),
  hand the archive to the refresh worker of 3.2, its next check at its timer (Q2) or, for NJ Transit, at the evidence (Step 2). The four status strings keep their meanings (`:43-74`, `backend/main.py:274-278`). Three are now derived from whether a generation is held (3.1), and `not-configured` stays the cold start's terminal state, set at `:405-412` where the warmup returns without handing the archive on; a refresh never moves a `ready` group back to `loading` or
  `failed`, because a refused or failed refresh leaves the live generation serving (3.3).
- **One refresh at a time.** The refresh worker is one task with one queue for every archive and the bus index, and it awaits each build to its end before it starts another (3.2, 3.10). The cold start keeps its five warmups and the bus build
  running together (`backend/main.py:343-357`), since it builds the first set; a check the
  worker starts before they return builds beside them (3.10), unless Q25 has the worker wait for
  each to return or end its first attempt and for the bus build to return.
- **Deadlines, and what a cut records.** The cold start's warmups keep `STATIC_ATTEMPT_DEADLINE_S`, 300 s (`backend/main.py:197`). A refresh check does not put it around its build, since a thread cannot be stopped (3.1): its transfer keeps `DOWNLOAD_DEADLINE_S`, 120 s (`backend/static_shared.py:523`). Today a deadline that expires inside `staged_fetch` arrives as `CancelledError`, which skips `_record` (`backend/static_shared.py:665-670`); an outer 0.05 s deadline left no entry at all (`v8_probe.py` part C, run for this design on 2026-11-28). The worker records each check's outcome itself, so a cut transfer is a `failed` with its reason, as 3.3 requires of every failure.
- **Per-component retry (Step 4).** The railroad warmup reaches `ready` when any system has
  stops (`backend/warmups.py:226`) and never retries a `None` system; the bus index builds
  once (`backend/main.py:357`, `backend/bus_static.py:262-309`). Each railroad system and each bus borough becomes its own entry in the refresh worker's queue, checked on the failure rungs (3.2; their numbers are Q20's), so a failed component retries while the others serve (F06's remedy, audit-2026-09-05.md:204).
- **Shutdown.** `backend/main.py:359-388` cancels and awaits the refresh worker as it does the warmups now.

**Asserted today that this makes false:** `backend/models.py:1334-1335`, "this process loads
one at start and never again", and the census the F07 witness prints of exactly one production
call site per loader (`backend/warmups.py:173`, `:222`, `:296`, `:348`, `:423`,
`backend/main.py:357`), which the witness printed again when run for this design on 2026-11-28.

### 4.3 The pollers and decoders

Every poll takes its archive's generation once, before its fetch, and decodes against that
generation only (3.1). Today:

| Poll | What it reads, and where | What changes |
| --- | --- | --- |
| `_refresh_njt` | `njt_stops`, `njt_trips`, `njt_lines` as arguments (`backend/pollers.py:1121-1125`), held across the POST (`backend/feeds/njt.py:1067`) | `app.state.static["njt"]` taken once; its writes (`pollers.py:1191`, `:1203`, `:1206`, `:1229`) carry the generation's id |
| `_refresh_subways` | `subway_stops` (`pollers.py:586`), held across `:610` | the same, for `subway` |
| `_refresh_railroads` | `railroad_stops` (`pollers.py:727`), both systems, held across the gather (`backend/feeds/railroad.py:1304`) | the two systems' generations, each taken once |
| `_refresh_path` | `path_stops` before the fetch (`pollers.py:873`) and `path_station_order` after it (`:933`) | one generation for both, so the decode and the matcher can no longer read two (the straddle, read from the code, not reproduced) |
| `_refresh_ferry` | `ferry_static_status` and `ferry_static` (`pollers.py:978`, `:993`), its tables taken at `backend/feeds/ferry.py:406-407` | the ferry generation, taken once |

**Live state carries the generation it was decoded under.** A swap held at the NJ Transit
poll's await today left 100 of 144 boards naming another station and 50 of 60 trains carrying a
name that was not the new generation's at their stop, measured for this design on 2026-11-28 by
`measure_njt_structures.py` part 3 and identically by `verify_m4a.py` part E (the real
`_refresh_njt` held at its await while a second real warmup assigned another publication). So
each of the products a poll writes carries the id of the generation it took: `njt_arrivals`,
the NJ Transit trains in `feed_cache`, `njt_feed_health` and `njt_behind_run`; the subway and
railroad trains with their retained copies (up to `FEED_RETENTION_MAX_S`, 600 s,
`backend/cache.py:104`), their positions (`pollers.py:706`, `:823`) and their arrivals,
`subway_arrivals`, `subway_arrivals_by_system` and `railroad_arrivals` (`pollers.py:444-445`); PATH's trains,
`path_identity` (`pollers.py:930`) and arrivals; the ferry's boats and `ferry_arrivals`, whose
buckets are keyed by the route's long name (`backend/feeds/ferry.py:266`). On a swap the
poll loop's next cycle runs at once (3.1), and until it replaces them the older rows are served only
with the names of the generation they carry, never the newest's (3.1). A failed subway group's
retained copies at the swap are dropped, or served in an envelope that names both generations, as
Q22 answers; and if Q21 says so, `_refresh_njt` also keeps its latest trip-update body for the
worker's decode of a candidate (3.2). A position anchor that
`carry_forward_prev` would keep (`backend/feeds/shared.py:335`, written into `prev_lat` at
`:345`) is rebuilt instead of carried when its generation differs; how long such an anchor
survives today is read from the code and not measured. An `njt_behind_run` held under one
generation says nothing about the next: the swap ends it, and the next decode under the new
generation starts one if the trains still contradict it, which is what Q3's rungs follow. One
consequence, stated so it is not mistaken for an accident: after a refresh that did not take,
`/healthz` names `njt-schedule-behind` 600 s after the new generation's first contradicting
decode, not before (`SCHEDULE_BEHIND_HOLD_S`, `backend/feeds/njt.py:606`).

**The decoder gains the two witnesses (Step 2).** `IdentityReport`
(`backend/feeds/njt.py:572-587`) gains the running count and the misses that are not ADDED,
told apart by the trip's `schedule_relationship` (`:941`), with the empty id of the `njt:` key an ADDED train is served under (`:853-858`) checked beside it; today no variable
holds the running count (`:930`, `:1004`). The decode also counts the stop ids the held table
lacks, which `_ordered_calls` drops silently before ordering today (`:249`), and the
consecutive-call pairs whose implied speed under the held table passes Q8's speed for the mark.
`_own_last_stop` (`:702`) and `_destination` (`:727`) withhold the last-stop name of a refuted
or unjoined trip that one of those marks (3.8).

**Asserted today that this makes false:** `backend/feeds/njt.py:690-692`, "THE NAME IS THE HELD
SCHEDULE'S ... Where NJ Transit renumbers its STOPS too, as it did on 2026-11-03, the name is
another station's, like every station name and place this process is serving."

### 4.4 The routes and models

- **The twelve static-derived endpoints carry their stamp.** They answer bare lists or
  objects today (for NJ Transit `backend/routes/njt.py:87`, `:124`), eleven under `public,
  max-age=3600` and `/api/bus-route/{id}` under no `Cache-Control` at all (all twelve read by
  read-only GETs of production for this design on 2026-11-28, from 09:43:38 to 10:26:18 UTC,
  and `/api/path-routes` at 12:50:27 UTC). Each gains the stamp of every generation it was built from as a response header (3.7): one for each of ten of them, and LIRR's and Metro-North's both for `/api/railroad-routes` and `/api/railroad-stops`, which serve the two systems in one list (`backend/routes/railroad.py:91`, `:120`). So no list changes shape and no golden moves. The bare URL is served `no-cache` with an ETag of the stamp and a hash of the serialized body, so a deploy that changes the body changes the ETag (3.7); a request whose `g` query names the live generation is served as today, `public, max-age=3600`; a request naming any other is served
  the live generation under `no-cache`, its stamp beside it (3.7: the URL names the
  generation, so an hour of caching is an hour of one generation). For the two railroad
  endpoints the ETag and `g` name both systems' stamps, so a change to either system is a new
  URL: a `g` naming one alone would let the stamped URL serve the other system's old table for
  as long as its `max-age=3600` holds (`backend/cache.py:373`).
  `cache._static_endpoint_ready` (`backend/cache.py:354-376`, its `ready` arm `:373`) carries
  the rule for the ten GTFS endpoints that call it (`backend/routes/subway.py:43`, `:55`;
  `railroad.py:79`, `:105`; `path.py:40`, `:68`; `ferry.py:40`, `:68`; `njt.py:77`, `:122`;
  a `git grep -n _static_endpoint_ready` at `16d1c19`). `/api/airtrain` sets its own header
  (`backend/routes/airtrain.py:22`) and `/api/bus-route/{route_id}` sets none
  (`backend/routes/buses.py:24-48`), so each of the two takes the rule in its own handler.
- **The stamp.** A prefix of the generation's `content` digest (3.1, 3.7): the archive's for one archive, the six borough archives' together for the bus index, and the committed file's sha256 for AirTrain (`data/airtrain_jfk.json`, read once at `backend/main.py:295`, which changes only
  with a deploy, since the file is in the repository).
- **Every live envelope carries the stamps of the archives its rows were built from.**
  `SubwayFeed`, `RailroadFeed`, `PathFeed`, `FerryFeed`, `NjtFeed` and the five arrivals
  envelopes gain a `static_generations` map (`AlertFeed` too if Q24 says so, 3.7), archive to stamp (the subway's entry naming two
  generations only if Q22's answer keeps a failed group's retained trains), optional with a
  default, the convention `SubwayFeed.systems` already follows (`backend/models.py:264`). The
  field-set locks are updated, not relaxed: `test_arrivals_envelope_field_set_is_locked` and
  `test_every_arrivals_envelope_is_locked` over `ARRIVALS_ENVELOPES`
  (`backend/tests/test_models.py:307`, `:321`, `:325`).
- **The arrivals handlers name rows by the rows' generation.** `/api/njt-arrivals` reads
  `stop_name` from the served stops this request reads (`backend/routes/njt.py:179`, `:188`),
  which need not be the stops the poll that built the rows held; under the design the handler and its four siblings read names from the generation the rows carry (4.3), and a request whose `g` names a generation other than its rows' is answered as stale rather than joined (3.7).
- **`StaticArchiveStatus`** (`backend/models.py:993-1017`) gains 3.5's fields (4.5).
- **The bus route's 503** says "restart the server to retry" (`backend/routes/buses.py:37`);
  after Step 4's retry it says the index is retrying.

**Asserted today that this makes false:** `backend/models.py:1168-1170`, "A key appears once
its archive has been downloaded in this process, or once a cached check found the cache without
its decorative member (its degraded_members say which), so on a boot with a warm cache the map
holds only those", since every held generation publishes its identity; and
`backend/routes/buses.py:37`, "restart the server to retry".

### 4.5 `routes/status.py`: `/api/status` and `/healthz`

`/api/status` serves `static_archives` from the module record (`backend/routes/status.py:224`,
`static_shared.archive_status` at `backend/static_shared.py:443`). Each archive's entry gains
3.5's fields beside the four it has, an additive change as `degraded_members`' own docstring
rules for that field (`backend/models.py:1015-1016`):

| Field | What it says | Nearest thing today |
| --- | --- | --- |
| `generation` | the identity of 3.1: archive, sha256, `content`, size, `etag` and `last_modified` when the publisher sent them, `feed_version`, the service span, `degraded_members`, `built_at` and `promoted_at`, and for the bus index `boroughs`, each borough's own identity | nothing: no hash, header, size or span is recorded anywhere in app code (a `grep -i` of `backend/*.py`, `backend/routes/*.py` and `backend/feeds/*.py` for validators, hashes and `feed_info`, run for this design on 2026-11-28) |
| `last_promoted_at`, redefined | the instant of the reference assignment, equal to `generation.promoted_at` | the instant of the rename (`backend/static_shared.py:483`, called at `:693`), at least 894.6 ms ahead of the subway's assignment in one cold run on this machine (`verify_runtime.py`, stamped at the promotion itself) and at least 9.176 s ahead of LIRR's in production (its stamp 08:00:46.056 against Metro-North's 08:00:55.232 UTC on 2026-11-28, both read from `/api/status`; the railroad warmup assigns both systems only after both promote) |
| `last_checked_at`, `next_check_at` | when the last check ran, and when the next will | nothing |
| `last_check` | `unchanged`, `promoted`, `refused`, `failed` or `deferred` (3.2; Q18) | nothing |
| `last_check_trigger` | `cold-start`, `timer`, `behind`, `miss`, `stop` or `validity` (3.2) | nothing |
| `last_refresh_error` | why the last check did not promote, sanitized as `describe_failure` sanitizes (`backend/static_shared.py:498`) | `last_download_error` |
| `consecutive_failures` | refused and failed checks since the last `unchanged` or `promoted`; a deferral does not count (Q18) | `failed_downloads`, reset by a promotion (`:488`) and counted per failure (`:495`) |

`degraded_members` becomes the held generation's own. The bus index gains an entry; today it has only `bus_route_index` `{status, partial}`
(`backend/routes/status.py:170-173`). `static_subway_gtfs` (`:130-135`) stays what it is, the
cache file's mtime, documented as the file's rather than the generation's.
`_unmapped_subway_routes` (`:450-473`) reads the station routes of the subway generation the
call takes.

`/healthz` gains Q4's code, and Q8's and Q9's if they say a code. The gating set stays the
three it is (`backend/models.py:1404-1408`) unless Q4 says otherwise; a degraded code rides the
200 (`backend/routes/status.py:828`, `:853`). `njt-schedule-behind` stays a code (`:440`,
`:826`); its model note changes (below).

**Asserted today that this makes false:** `backend/routes/status.py:462`, "The index is fixed
for the process, so it is read here rather than latched", and `backend/models.py:1343-1345`,
"WHAT CLEARS IT is a decode with trains enough to judge by that does not see it, which means a
process holding the current publication, which means a redeploy"; and
`backend/models.py:1313-1314`, "Cleared by the next start whose lines draw, which in production
is the next deploy: a running process neither re-fetches a promoted archive nor reloads its
lines", which Step 4's subway refresh makes false.

### 4.6 The monitor

- **Step 0's line.** `check_njt_static` (`backend/scripts/contract_monitor.py:2262`)
  downloads the API archive on the run's single token (`_NjtToken`, `:2074-2097`; the fetch at
  `:2345`) and reads it whole (`:2355`); its PASS prints routes, stops, the two identity rows
  and the service end and nothing else (`:2485-2491`). It gains the archive's sha256, `content` digest (3.1) and bytes, its member sizes, its trips, `stop_times` and `calendar_dates` rows, and any `ETag` or
  `Last-Modified` the response carried, on that same token, so it costs no mint. The four public static lines gain the sha256, `content`, `ETag` and `Last-Modified` of what they downloaded, which Step 5 compares.
- **3.5's words.** `production:njt-schedule`'s FAIL reads "production's schedule is behind NJ
  Transit's publication: redeploy" (`:3651`); it moves to "the refresh did not take", followed
  by `static_archives.njt`'s `last_check`, `last_refresh_error` and `last_checked_at`. Its
  refused arm stays (`:3640-3647`). The `_HEALTH_CODE_NOTES` that rest on a running process
  never re-fetching move with it: `subway-lines-degraded` (`:356-364`) and
  `njt-schedule-behind` (`:404-410`); `subway-route-unmapped` (`:396-403`) keeps its
  "redeploy", because its table is code.
- **Step 5's check.** A new production line compares the generation production holds, per
  archive, with the publication the run downloaded; today nothing in the monitor reads
  `static_archives` but one prose string (`:358`; a `grep -n` of the monitor for
  `static_archives` and its four fields).
- **Q4's code.** `static-refresh-failing` joins the monitor's mirrored `PRODUCTION_HEALTH_CODES`
  (`backend/scripts/contract_monitor.py:303-315`), and like every recognized code outside
  `_WARN_HEALTH_CODES` it FAILs `production:healthz` (`:332`, `:2856-2857`). If Q4 says so, it
  WARNs instead in a run whose own static line for that archive refused its download, as
  `production:njt-schedule`'s refused arm does (`:3640-3647`).
- **The channel.** If Q5 builds one here, the workflow's own sentence changes with it
  (`.github/workflows/contract-monitor.yml:11-15`).

**Asserted today that this makes false:** `backend/scripts/contract_monitor.py:3525`, "A
process loads NJ Transit's schedule once"; `:359-360`, "a running process never re-fetches
it"; and, if Q4 says so, `:317`, "THE TWO RECOGNIZED CODES THAT ARE A WARN, NOT A FAIL", and
`:2807`, "EVERY RECOGNIZED CODE FAILS THE RUN BUT TWO".

### 4.7 The frontend

| Surface | Change |
| --- | --- |
| The loaders | `retryUntil` returns for good at the first truthy result (`frontend/helpers.js:5480`), and the eleven loaders start once each (`frontend/map.js:281-291`). Each poll's envelope now names its archives' stamps; when one differs from the stamp the page's tables came from, the page refetches that archive's static endpoints under `?g=` with the new stamp, and only that archive's (3.7). A stamp for an archive the page's tables did not carry counts as a change. |
| The NJ Transit tables | `njtStopCoords` is a `const` Map filled per station and never cleared (`frontend/systems/njt.js:54`, `:162`); it is cleared and refilled from the new payload. The route tables are already reassigned together from one payload (`:82-86`). |
| The station registry | `stationRegistry` only grows (`frontend/systems/shared.js:2808`, push at `:2816`); a system's entries are replaced as a set, markers removed and added with them, and each station's arrivals URL, built once at load today (`frontend/systems/subway.js:575`, `railroad.js:231`, `path.js:106`, `ferry.js:94`, `njt.js:166`), is rebuilt with its entry. |
| The other tables | the subway's `routeIndex` (`frontend/systems/subway.js:220`, set at `:246`) and complexes, the railroad, PATH and ferry route maps (`frontend/systems/railroad.js:7`, `:10`, `:24`; `path.js:9`, `:10`, `:17`; `ferry.js:13`, `:14`): replaced from their new payloads. |
| The drawn lines | every route loader adds polylines to a layer group that nothing clears: the subway's `routeLinesLayer`, with the `subwayRibbons` registry route focus restyles (`frontend/systems/subway.js:280`, `:302-303`); `lirrRouteLinesLayer`, `mnrRouteLinesLayer` and `njtRouteLines` through `railDrawRibbons` (`frontend/systems/railroad.js:132-151`, called at `:68` and `njt.js:106`); `pathRouteLines` (`path.js:63`) and `ferryRouteLines` (`ferry.js:54`). The groups are declared at `frontend/systems/shared.js:1072`, `:1087`, `:1090`, `:1105`, `:1110` and `:1116`, and the only `clearLayers` call in the page's own scripts is on the bus route's group (`frontend/systems/buses.js:183`; a `git grep -n clearLayers` at `16d1c19`). Each group the refetched payload draws into is cleared before the new payload draws, `subwayRibbons` emptied with the subway's group, and the subway's ribbon tags and key are refreshed from the new list by `refreshSubwayKey` (`frontend/systems/shared.js:654-664`, retagging at `:658`), which the route list's resolution and the poll tail already call (`:652`). |
| The joins | the NJ Transit glide (`frontend/systems/njt.js:478`), the "Also here" crosslink (`:377`) and each system's route index, names and colours join a live row only when the envelope's `static_generations` entry equals the stamp of the page's tables; otherwise the row is treated as a miss is today (2.5.3), drawn where the server placed it, until the reload lands (3.7). |
| The arrivals URLs | each carries `?g=<stamp>` of the table its id came from, and is rebuilt with its registry entry (3.7, 4.4). |
| The alerts join | `matchStationAlerts` keys a station's alerts by system and station id (`frontend/helpers.js:5229-5231`) over the page-held station list, for every system with an alerts feed, NJ Transit's included (its callers at `frontend/systems/subway.js:582`, `railroad.js:242`, `ferry.js:116`, `njt.js:199`); it reads the replaced registry, with no stamp to compare unless Q24 gives `AlertFeed` one (3.7). |
| The one re-read | `fetchRoutesPayload`'s `cache: "reload"` re-read for an absent field stays (`frontend/systems/shared.js:2156-2180`), because the stamp names the publication and not the build, so it does not answer the comment's question, whether the payload predates a field (`:2147-2148`). |
| What a rider is told | nothing, or a line, by Q7. |

**Asserted today that this makes false:** `frontend/map.js:277-278`, "each loader stops for
good after its first successful populate". Not made false: `frontend/systems/shared.js:2147-2149`, "A
FOLLOW-UP, NOT A FIX: a version stamp on the static-derived endpoints would let the frontend
ASK whether the payload predates the field instead of inferring it from absence." The stamp names the publication and not the build, so that follow-up stays open (the re-read row above).

### 4.8 The tests and goldens

**Tests that pin today's one-shot shape, updated rather than relaxed:**

- `test_status_static_archives_is_empty_before_any_download`
  (`backend/tests/test_static_shared.py:1482-1489`) asserts `static_archives == {}` after a
  boot with a warm cache. Under 3.5 every held generation publishes an entry.
- `test_njt_static_warmup_loading_to_ready` (`backend/tests/test_api.py:5595`) asserts
  `app.state.njt_static["calendar_dates"]` (`:5609`) beside the comment "The full tables stay
  on app.state for 15b to consume without re-parsing" (`:5607-5608`), and the light-rail test
  asserts `app.state.njt_static["stops"]` (`:5852`). Under 3.1 the generation keeps no whole
  parse (Q14); the light-rail rule's "THE PARSE IS WHOLE" half moves to an assertion on the
  builder's parse.
- `BEHIND` (`backend/tests/test_contract_monitor.py:3219`) and
  `test_production_behind_a_clean_decode_fails_and_says_redeploy` (`:3258`) pin the word
  "redeploy" that 3.5 replaces.
- `test_the_schedule_code_is_a_warn_on_the_health_line_with_its_note`
  (`backend/tests/test_contract_monitor.py:3464-3486`) pins the `njt-schedule-behind` note
  verbatim, "production:njt-schedule says whether a redeploy fixes it" included, and
  `test_the_lines_code_is_watched_and_explained_in_words` (`:4841-4857`) asserts "never
  re-fetches" in the `subway-lines-degraded` note: the two notes 4.6 moves.

One mirror test moves with the status model rather than with the one-shot shape:
`test_the_counts_this_reads_are_the_counts_the_app_publishes` (`:3446-3453`) holds the
monitor's mirrored counts equal to `NjtFeedHealth`'s fields less `total`, `ok` and `failed`
(`:3453`), so the generation and 3.8's counts that `njt_feeds` gains (3.5) land in the
monitor's mirror or in that exclusion set in the same change.

**The goldens.** No golden should move. The stamp rides an envelope or a header and the
generation's id rides internal live state, so no decoded row gains a field, and the decoder
goldens compare rows. One exception is posed rather than predicted: if Q8 withholds the
last-stop name of an unjoined trip, `backend/tests/fixtures/njt_tu_expected.json` (68 trains, 3
of them unjoined; 648 rows, 14 unjoined; counted from the golden's `identity` keys for this
design on 2026-11-28) moves for any unjoined trip the threshold marks. On that capture's right
table, 6 of 90 trips have a pair above 250 km/h (arm A of `speed_witness.py`, re-derived by
`verify_speed.py`); whether any of them is one of the unjoined is not measured. A count that
moves is explained in the commit that regenerates it, never absorbed (PR #108's 4.6).

**New tests**, each named by what it pins: the builders are pure (no `app.state` write and no
socket, under the hermetic guard the audit scripts use); the swap is one reference (a builder made
to raise on a second build assigns nothing, the instrument of `verify_m4a.py` part F; a 1 ms
watcher cannot tell, since the block it would watch holds no await, 2.4.1); a poll that awaited
across a swap serves no row under the other generation's names; the PATH poll reads one
generation; one refresh at a time (two checks due at once build in turn, 3.10); and the refresh's NJ Transit path sends no getToken POST in each case of 2.7's tables that no test drives today (a static download on a token another caller minted, a real socket cut after a 200, a close-delimited cut, the trailing bytes, a token killed mid-download, a refresh beside polls at the ceiling), with the contract tier gaining a refresh beside both pollers and the simulator's mint count asserted (`tests/contract/test_contract_api.py:1215` and `:1871` pin today's static mint counts).

**Asserted today that this makes false:** `backend/tests/test_static_shared.py:1483-1484`, "A
cold boot with a warm cache downloads nothing, so the map is empty".

### 4.9 The audit reproductions

Under both directories' own rule, a fixed finding's script is rewritten in the change that
fixes it to pin the repair ("As fixes land, rows move to FIXED and their scripts are rewritten to pin the repair", `docs/reviews/audit-2026-10-12/README.md:34-35`; `docs/reviews/audit-2026-09-05/README.md:23-24`). Each carries its before beside its after. All
four that still reproduce exited 0 at `16d1c19` when run for this design on 2026-11-28 by
`run_witnesses.sh` (hermetic: credentials blanked, `-I -B`, a fresh `TMPDIR`), and again when
re-run by its verifier:

| Script | Reproduces today (its DISPOSITION) | Flips at |
| --- | --- | --- |
| `docs/reviews/audit-2026-09-05/f07_static_never_refreshes.py` | "all 6 static tasks return permanently, each loader runs exactly once, and a 30-day-stale, calendar-expired NJT snapshot keeps serving as ready with max-age while nothing reloads or revalidates" (its measured age is 2,678,400 s, 31 days) | Step 1 for its NJ Transit arm, Step 4 for the other five |
| `docs/reviews/audit-2026-09-05/f06_partial_startup_no_recovery.py` | one injected Metro-North failure leaves `ready` with Metro-North stops `None` after exactly 1 attempt per system across 100 shortened retry intervals, and `ensure_index` builds once | Step 4 |
| `docs/reviews/audit-2026-10-12/a06_frontend_static_once.mjs` | "All 11 frontend static loaders retried through 3 refusals, took 1 usable answer each, then made 0 requests in 25 simulated hours ... 0 of the changed coordinates reached the map" | Step 3 |
| `docs/reviews/audit-2026-10-12/a09_unbounded_upstream.py` | arms 2 and 3 stand: 536,871,856 bytes, 70,005 members and 2,147,483,720 declared bytes promoted; arms 1 and 1d pin their repairs | does not flip: its ceilings are out of scope (the list before section 1; 5.3). Its arm 1 drives the real subway warmup under a 10 ms attempt deadline, so the restructured warmup must keep it green, and its arm 1d's rule, the shortest of three attempts' longest heartbeat gaps under a quarter of the loader timed alone (89 ms against 1,170 ms when the arm was written, ledger:425), is the form Step 1's stall check takes |

The A06 frontend witness's kill record already names the repair's shape twice: among its
second fixes, each exit 1, re-reads on "a new static generation (in a body key or a header)";
and in its adversarial round, each exit 0 before the script was hardened and exit 1 after, "one
keyed on a `static_generation` body key no fixture carried"; the hardened script "gives every
live body seven announced keys" (`docs/reviews/audit-2026-10-12/README.md:117`). The rewrite serves the stamp in its fixtures,
so what it measures is the page's repair and not a fixture that happens to carry a key.

**Asserted today that this makes false:** each of the first three exits 0 because its defect
holds, so each green run is a report that a process or a tab is holding an old publication;
they are the files whose passing this build is meant to break.

### 4.10 README and the runbook

The README states the cure as a deploy: "a running process does not re-fetch an archive it has
promoted, so a corrected upstream publication is picked up by redeploying, not by waiting"
(`README.md:790-791`), and of `njt-schedule-behind`, "a redeploy is what loads the current
schedule" (`README.md:962-963`). Both are rewritten to the refresh. The README has no runbook
for the hand Redeploy, though it ended two of the three incidents (record:592). One is added, for when the refresh did not take: read
`static_archives.<archive>`'s `last_check` and `last_refresh_error`; if the publication is good
and the process will not take it, Railway's Redeploy, not Restart (the record's own words,
record:589), whose cost is measured: 28 s from `in_progress` to the first promotion
and 34.7 s to NJ Transit's on 2026-11-28, 69 to 93 s to the first on the six cold starts of
2026-11-01 to 2026-11-22 (each deployment record's statuses, read with `gh api`, set beside the
recorded promotion times by `durations.py` and again by `rederive.py`), and one cold mint
(`backend/njt_auth.py:172-173`). Whether a Restart keeps the data directory is not measured
(`railway.json` sets `restartPolicyType` `ON_FAILURE` and says nothing of the disk), which is
why the runbook names Redeploy.

**Asserted today that this makes false:** `README.md:790-791` and `README.md:962-963`, as
quoted, and `README.md:1298-1299`, which quotes the monitor's FAIL text "production's schedule
is behind NJ Transit's publication: redeploy" (`backend/scripts/contract_monitor.py:3651`), the
words 4.6 rewrites.

---

## 5. What this does not decide, and the open questions

### 5.1 Proposed in the body, not asked

Each is named here so a decision can reject it by name rather than find it in passing, as
Amendment 1's A1.5 did. Where a question in 5.2 settles part of one, it is named beside it.

1. The scope set before section 1: the age limit on a stalled feed's rows left to the freshness
   contract, one process with one worker, and A09's ceilings taken up only as far as a refresh
   needs (the list before section 1; 5.3).
2. One immutable generation per archive (`njt`, `subway`, `railroad_LIRR`, `railroad_MNR`,
   `path`, `ferry`) and one for the bus index, holding only what readers read, `njt_stop_schedule` aside (item 14; Q14 asks whether the unread structures go), with the identity
   of 3.1: archive, sha256, `content`, size, `etag`, `last_modified`, `feed_version`, service
   span, `degraded_members`, `built_at` and `promoted_at` (3.1).
3. The `content` digest beside the sha256 (each member's name, CRC-32 and size from the zip's
   central directory), with "unchanged" meaning equal `content` and the page's stamp a prefix
   of it (3.1, 3.7); whether the sha256 alone would do is what Step 0 measures (Q17).
4. `feed_info.txt` and the service span read for identity only, not as new gates (3.1, 3.3).
5. A pure builder per archive, `build(archive, path, *, now)`: no `app.state` write, no socket,
   no module-path constant, run in a worker thread, with NJ Transit's body write and the hashing
   moved into it (3.1, 4.1; a thread or a subprocess is Q16).
6. The worker awaits each build to its end, with a deadline on the transfer and none on the
   build (3.1, 4.2).
7. The swap as one assignment, `app.state.static[archive] = gen`, with three of the four
   `*_static_status` strings derived from whether a generation is held and NJ Transit's
   `not-configured` kept as the cold start's terminal state (3.1, 4.2).
8. `promoted_at` as the instant of that assignment, and `/api/status`'s `last_promoted_at`
   redefined to it; the stage renamed over the cache after the assignment, so the file on disk
   is a cache of the bytes, not the state (3.1, 3.5, 4.5).
9. The reader rule: each reader takes the reference once per poll or per request, the PATH
   poll's two reads and `/api/bus-route`'s two flags included (3.1, 4.3, 4.4).
10. The live-state rule: live rows, `njt_feed_health` and `njt_behind_run` carry their
    generation, and an NJ Transit behind run ends at the swap; anchors of another generation
    are dropped; retained trains are never merged across generations; `path_identity` starts a
    new epoch; ferry buckets carry their generation; `_ARCHIVE_STATUS` is replaced by the held
    identity (3.1, 4.3).
11. On a swap the poll loop's sleep ends early, so the next cycle, all six refreshers of
    `FEED_REFRESHERS` (`backend/pollers.py:1268-1275`), the buses' included, starts at once; a cycle in flight is neither cut short nor joined by a second poll; until the first
    poll under the new generation lands, the older rows are served only under their own
    generation's names, and the old generation lives while any live row carries it (3.1, 4.3;
    a failed subway group's retained rows at the swap are Q22).
12. The bus index's generation as a directory named by its identity, swapped by reference, the
    old one removed once no request holds it, its identity carrying one identity per borough
    archive, `boroughs`, from which each borough is checked and compared (3.1, 3.2, 3.5, 4.1;
    how one borough's change becomes a whole directory is Q23).
13. AirTrain outside the refresh, its stamp the committed file's sha256 (3.1, 4.4).
14. `njt_stop_schedule` kept in NJ Transit's generation for now (3.1, 4.1; Q14 asks whether it
    goes).
15. One refresh worker with one queue, and five outcomes for a check: `unchanged`, `promoted`,
    `refused`, `failed` and `deferred` (3.2, 4.2; how a deferral is counted is Q18).
16. A conditional GET with the held ETag and Last-Modified for every public URL, and after a 200
    the `content` compared before anything is built (3.2, 4.1).
17. NJ Transit's check as a full `getGTFS` download on the borrowed token, compared by
    `content`, and NJ Transit's public `rail_data.zip` not used as a trigger (3.2).
18. The behind-run trigger at the start of a behind run, not at the 600 s hold (3.2).
19. A validity trigger for every archive with a service span, fired when a generation enters its
    band and when its span ends (3.2; the band, and whether it fires once at each crossing
    rather than at every check while the span is past, are Q19's).
20. The backoff's shape: rising rungs jittered 0.9 to 1.1, a ceiling no longer than the
    archive's own timer period, continued while any of the evidence holds and ended only when none
    does, a run that starts just after a promotion taking the next rung rather than a check at
    once (3.2; the numbers, and whether a refused or failed NJ Transit check climbs them, are
    Q3's; a public archive's, Q20's).
21. A component that failed at the cold start, a railroad system or a bus borough, retried as its own entry on the failure rungs without a deploy (3.2, 4.2; the rungs are Q20's), which is F06's and A05's recovery.
22. A refresh runs a cold start's gates, with `cache_serves` meaning "the live generation's
    decorative member draws": replacing mode in the ordinary case, the cold-start rule when the
    live generation is itself degraded or there is none, as for a component that failed at the
    cold start (3.2, 3.3, 4.1; whether a refresh also holds back a candidate a cold start would
    take is Q12 (a), Q13 and Q21).
23. A refused or failed refresh keeps the live generation, and the worker records each check's
    outcome itself, outside `staged_fetch`, so a check cut by a deadline is recorded (3.3,
    4.2); Q4's code also names an NJ Transit generation held past its `service_through` with no
    accepted replacement (3.3, 3.5; Q4 and Q12 ask).
24. Borrow, never mint: the refresh reads only the live token and never calls `get(_mint)`,
    `invalidate` or `confirm`, and it defers when there is no token or the post is refused (3.4,
    4.1).
25. The refresh does not borrow an over-age token through a hold. This is reversible: borrowing
    it would mint nothing either (3.4).
26. The proof witness's six cases and its three named mutations (3.4, 6.1).
27. The `/api/status` fields of 3.5 for every archive and the bus index, each present from the
    cold start's first assignment, and `njt_feeds` gaining its generation and 3.8's counts (3.5,
    4.5).
28. `production:njt-schedule`'s FAIL reading "the refresh did not take" with the refresh fields
    beside it, today's words kept against an older revision, and the other notes that prescribe
    a redeploy rewritten (3.5, 4.5, 4.6, 4.10).
29. A monitor check of the held generation against upstream for every archive, the bus archives
    through their S3 ETags, with 3.5's verdict rule (3.5, 4.6, 6.5).
30. The page's stamp: a response header on the static endpoints and a `static_generations` body
    field on the envelopes and boards, an envelope's stamp being the generation its rows were
    decoded under (3.7, 4.4; whether `AlertFeed` carries one is Q24).
31. Two URLs per static endpoint: the bare one `no-cache` with an ETag of the stamp and the body's hash, the stamped
    `?g=` one `public, max-age=3600`, and any other stamp answered with the live generation under
    `no-cache`; for an endpoint built from two generations, the ETag and `g` naming both (3.7, 4.4).
32. On a stamp change the page refetches only that archive's endpoints and replaces its
    registries in one assignment, the route lines drawn from the old payload cleared before the new ones draw; it joins rows to tables only within one stamp, the station-alert join aside, which has no stamp to compare (whether `AlertFeed` gains one is Q24), and draws an
    envelope whose stamp it lacks where the server placed it (3.7, 4.7).
33. A live request naming a static id carries its table's stamp, and the server answers a
    mismatch as stale (3.7, 4.4).
34. The decode counting `running`, `missed` (not ADDED) and `added`, ADDED told by `schedule_relationship` with the empty id checked beside it, served on `njt_feeds`; the
    MISS trigger at the start of a MISS run, which holds until a decode says otherwise; and the
    MISS code being `njt-schedule-behind` itself, its run given a second
    arm (3.8, 4.3; the thresholds are Q9's).
35. The decode counting calls at stop ids the held table lacks and pairs above a speed, served
    on `njt_feeds`; past Q8's decode threshold a stop run starts, which triggers a check and holds
    until a decode says otherwise, and a refuted or unjoined train that either mark names takes
    `_destination`'s fallback (3.8, 4.3; the thresholds, what ends a stop run, and whether it also
    raises a code, are Q8's).
36. The hand Redeploy kept and documented as the fallback, with its measured cost (3.9, 4.10;
    the volume is Q11).
37. One refresh at a time across the archives and the bus index (3.10, 4.2; the memory ceiling
    is Q6, and whether the worker waits for the cold start's loads is Q25).
38. The tests and witness rewrites of 4.8 and 4.9, and the order of section 6, NJ Transit first,
    with its witnesses, mutations and watches.
39. In a refresh, an empty result from a lenient subway loader, `load_subway_stations`' `{}` or, for a `shapes.txt` the gate passed, `load_subway_route_shapes`' `[]`, is a refusal, not a promotion (3.3, 4.1, 6.4).
40. F09 and H2 taken up only where a builder raises (section 6, its last paragraph). NJ Transit's geometry builder raising on a nonfinite coordinate, as F09's witness found it does ("the real geometry builder raises", audit-2026-09-05.md:431), is a refusal that keeps the live generation (3.3); the finite and range checks H2 asks of the parsers (ledger:545-547) are not built here. A refresh, running a cold start's gates (3.3), would then promote a subway publication with a nonfinite stop coordinate and serve it as `"lat":null`, as a cold start does today (audit-2026-09-05.md:431).

### 5.2 The open questions

Each is phrased so a yes, a no or a number settles it, save the words and the surface Q7 asks for
if its answer is no, with the recommendation stated and the
evidence beside it, in PR #108's section 5 form. The questions are to be kept as posed when
they are answered, because a question rewritten after its answer stops being evidence of what
was asked.

**Q1. Should NJ Transit's check be triggered both by a timer and by the evidence, the evidence
being `njt-schedule-behind`'s run, the MISS share and the stop witness?** The evidence alone misses a
republication that renumbers nothing; the timer alone finds the same bytes while the trains
already contradict them. On 2026-11-03's shape the mismatch rule sees nothing: with the old
seven-digit ids held and the realtime on the new ones, a stand-in decode of 50 realtime ids
against the 131 held ones joined 0 and checked 0, and `behind_run` returned `None`
(`miss_share.py`, re-derived by `recount.py`); production on 2026-11-07 at 00:19 UTC
(2026-11-06 19:19 Eastern) placed 56 of 56 trains on ids its archive lacked (PR #137's body),
none of them ADDED, since all 56 matched the new `trips.txt` on trip id and an ADDED train is
served under its `njt:` key instead (`backend/feeds/njt.py:853-858`); and the code says so of
itself: "WHAT IT CANNOT SEE is a schedule behind by misses alone" (`backend/models.py:1348`).
The monitor, on misses alone, WARNs at most and only when its own decode checked 20 or more
(`backend/scripts/contract_monitor.py:3606`, `NJT_MIN_TRIPS_FOR_RATIO` at `:653`). Against a
timer alone: NJ Transit's realtime can move before its schedule endpoint. Run 445 (databaseId
42889072568, created 2026-11-10 19:49:15 UTC, 14:49:15 Eastern) counted 41 identity warnings of
three kinds, five pairs named, against a schedule it had just downloaded, and run 446's
download agreed with the trains; the realtime's lead over the API was under 32 h 10 min 48.48 s
and more than minus 19.12 s (`m3_lines_and_bounds.py` and `v3_bounds.py` over the two runs'
logs), so a lead of zero is not excluded. The bounds of 2026-11-27 also allow the API first by up
to 11 h 28 min 26.37 s (2.3), a window a timer check would promote into (3.2;
Q21). *Recommendation: both, with the MISS share counted as
evidence, because 2026-11-03's shape gives the mismatch rule nothing to count.*

**Q2. What should each archive's timer be?** A check that finds nothing costs headers only on
every public URL: all 11 the app fetches, and NJ Transit's public `rail_data.zip`, answered
both `If-None-Match` and `If-Modified-Since` with 304 and no body (`probe_headers.py` at
2026-11-28 09:41:50 to 09:42:41 UTC, and 12 of 12 again by `v_wire.py` at 10:18:07 to 10:18:22
UTC), and the app sends no validator today (`backend/static_shared.py:537-538`). NJ Transit's
token endpoint has no known validator (its response headers are discarded,
`backend/njt_auth.py:716`), so its check is a full download, 4,369,879 bytes for the pull of
the 2026-11-06 publication (ledger:653), and whether `getGTFS`
calls are metered is recorded nowhere (`backend/njt_auth.py:140-146` records only getToken's
ten, and that the usage counter cannot be read). How often each publisher moved in runs 400 to
481 (2026-10-25 to 2026-11-28): NJ Transit's API archive six dated times, with intervals
between visible changes, from creation to creation, from 24.10 h (1 d 0 h 5 min 55 s) to 169.30 h at least and 47.79 h to 192.68 h at most, and at least 24.09 h (24 h 5 min 29.17 s, the runs' logs, 2.2) from run 462's first printed line to run 464's step opening
(`cadence.py` over the `njt-static` lines of 86 attempts, re-derived by `vwindows.py`; a
seventh difference is dated only to runs 425 to 434); the subway once (its `Last-Modified`
2026-10-28 16:39:52 UTC); LIRR and Metro-North at least once each (`Last-Modified` 2026-11-25 22:10:39 and 2026-11-26 17:30:32 UTC, against the 2026-10-14 and 2026-10-15 the ledger read on 2026-10-15, ledger:825, :834-835, with other bytes and other `stop_times.txt` row counts now, 2.2); the ferry at least twice (runs 424 to 425, and
`Last-Modified` 2026-11-27 06:25:01 UTC); PATH (2025-06-06) and the six bus archives
(2026-10-16) not at all (each `Last-Modified` as read by `download.sh` and `probe_headers.py`
on 2026-11-28, and the monitor's lines). A06's repair asks for "jittered, per-source refresh"
(ledger:267), and the warmups already jitter by 0.9 to 1.1 times
(`backend/warmups.py:111`). *Recommendation: every public archive hourly with that jitter,
which is 264 conditional GETs a day for the 11 URLs, each a 304 with no body while nothing
changes; NJ Transit every 6 hours, four full downloads a day, 17,479,516 bytes at the
2026-11-07 size (arithmetic over that literal), leaving the fast cases to Q1's evidence and
Q3's rungs.*

**Q3. When an NJ Transit check finds the publication unchanged, or a new one the trains still
contradict, while the evidence holds, should it retry at 10, 20 and 40 minutes and then every
60 minutes, 60 minutes being the ceiling, for as long as the evidence holds; and should a check
that is refused or fails outright retry on the same rungs while the evidence holds, and otherwise at the next
timer tick?** The lead to cover is run 445's: under 32 h 10 min 48.48 s on 2026-11-10 (23 h 11
min 32.74 s counting run 444 as a conditional witness), and under 23 h 44 min 54.22 s on
2026-11-27 (12 h 41 min 10.89 s counting run 479) (`v3_bounds.py`). Each rung is a full
download on the live token and no mint (3.4). By arithmetic over those rungs, the longest
lead the logs allow costs 35 checks (the one at once, the three short rungs and 31 at the ceiling),
152,945,765 bytes at the 2026-11-07 size; a 120-minute ceiling would cost 19 checks and
83,027,701 bytes and leave a publication unadopted for up to two hours. Where trains are refuted, in the trip-id shapes (1.1.2, 1.1.3), riders are carried
through that time by PR #145's rule, which shows each refuted train its own number (record:589).
In 2026-11-03's shape no train is refuted (joined 0, checked 0, 2.6a): each unjoined train shows
its own number (`backend/feeds/njt.py:819`, `:825`), but it is placed on boards by the held stop
table, as when board 112, titled "Newark Penn Station", listed Morris & Essex trains (ledger:653,
1.1.1), and named by the held table's name for its last stop (`:828`), another station on 62 of
90 stand-in trips (3.8), until Q8's withholding removes that name. Nothing in this design
withholds a board row, so in that shape the time a publication waits unadopted is time riders
read boards built on the held stop table. The warmup's own rungs, 15, 30, 60 and 300 s
(`backend/main.py:163-165`), are for a process that holds nothing; a refresh holds a
generation, so a failed check need not be retried that fast. *Recommendation: yes to both, with
the 60-minute ceiling.*

**Q4. Should a refresh that is refused or keeps failing raise `static-refresh-failing` on
`/healthz`, degraded and never gating, once an archive's `consecutive_failures` reaches 3 or
its last good check is older than three of its timer periods, not counting time its checks
spent deferred (Q18); and should `production:healthz` WARN rather than FAIL on it in a run whose
own upstream static line refuses the same archive?** Never gating, for the reason
every non-gating code gives: the status code is read once, while a build is promoted
(`backend/models.py:1376-1383`), so gating would refuse every build for a fault in the world,
the build that fixes it included (`:1353-1355`); the gating set is three codes (`:1404-1408`).
The monitor already FAILs on any recognized code but the two it WARNs on
(`backend/scripts/contract_monitor.py:332`, `:2856-2857`), so the code reaches a red run
without a new check. Where the same run's upstream check refuses the same publication, the
monitor has a precedent for saying "wait for one it accepts" instead of failing:
`production:njt-schedule`'s refused arm (`:3640-3647`). Three failures is three timer periods
for a public archive at Q2's hour, and three 6-hourly NJ Transit checks or three rungs while
the evidence holds. *Recommendation: yes, that name, that band, degraded and never gating, and
`production:healthz` WARNing rather than FAILing on it in a run whose own upstream check
refuses the same archive.*

**Q5. Should Ben be told that a refresh did not take by (b), an outside poller of `/healthz`
that he sets up outside the repository, and in the repository by (a), a push from the monitor
on FAIL, built on its own small branch before Step 1 rather than in Step 0?** The channel today is the run status: "GitHub's own
scheduled-workflow-failure notifications reach the repo admins"
(`.github/workflows/contract-monitor.yml:11-13`). Runs 480 (databaseId 46070185338) and 481 (46123612408) both ended `failure`, and when Ben answered at 2026-11-28 12:43:47 UTC (07:43:47 Eastern) neither email had arrived, at least 15 h 18 min 0 s and 4 h 44 min 54 s after the two runs ended; which account GitHub notifies for a scheduled workflow is not measured (1.3). The monitor is also slow by cadence: its scheduled runs came a median 11 h 5 min 56 s apart (1.2). On 2026-11-27 `/healthz` carried `njt-schedule-behind` 1 h 2 min 44 s before run 480's FAIL, and nothing reads `/healthz` on a schedule except the monitor (record:666; 3.6). The four options, with what each needs and cannot see, are 3.6's table: (a) a push from the monitor on FAIL, which needs a secret and an outside service and alerts at the monitor's cadence; (b) an outside poller of `/healthz` alerting when `degraded` is not empty, which alerts within its own interval but must read the body, since `/healthz` answers 200 for every degraded code (`backend/routes/status.py:828`, `:853`), and needs an account outside the repository; (c) a GitHub issue opened on FAIL, which needs `issues: write` where the workflow grants only `contents: read` (`.github/workflows/contract-monitor.yml:38-39`), and whose notice rides GitHub's notifications, the path 1.3 could not measure; (d) Railway's own alerts, which read the healthcheck only at promotion (`backend/models.py:1376-1383`) and so cannot see a degraded code. *Recommendation: yes, (b) first, set up by Ben outside the repository; and in the repository (a), on its own small branch before Step 1, since it touches only the workflow and the monitor, deploys nothing, and does not ride GitHub's notifications.*

**Q6. Above what resident memory should a refresh not be attempted, and is it deferred to the
next check when it is not?** The limit is unknown: no plan or memory field is in the repository
(`railway.json`, `nixpacks.toml`, and a `git grep` for a plan or a limit), Ben was asked, and
the Railway CLI is logged out (it still answered Unauthorized at 12:50 UTC, 07:50 Eastern); a reading is
pending, and the first number to read is the Metrics series of deployment 7988955096 since
2026-11-28 08:00:16 UTC (03:00:16 Eastern), production's resident memory holding one
generation. Every figure below is macOS, this machine, not Linux. A whole second set added
202,522,768 and 202,195,088 bytes of footprint in two runs of `m1_measure.py generations plain`
and 201,212,072 in one of `v_cost.py gens plain` (`proc_pid_rusage` `ri_phys_footprint`, all
archives in sequence, gates included, cached), and 178,495,859 bytes under tracemalloc
(`m1_measure.py generations trace`). One build at a time is what 3.10 holds the addition to, beside any old generation a failing
family's polls keep alive; the largest build is NJ Transit's: on the public stand-in, alone, cached, 88,470,849 bytes retained
(tracemalloc), a footprint delta of 116.0 MB and a tracemalloc peak of 99.5 MB for gate and load; on the rail-only projection, 62,196,276 bytes retained and 95.9 MB of footprint (`m1_measure.py plain` and `trace`, each archive alone in a fresh process; 2.1's table). The API archive's
last measured size, 4,369,879 bytes, is 1.326 times the public stand-in's 3,295,223 and 1.748 times the rail-only projection's
2,499,303, the shape production serves (2.1; arithmetic over the literals), so its costs may
exceed either stand-in's. *Recommendation: once the limit L is
read, do not start a refresh while resident memory exceeds L less 170 MB (the larger of 116.0 MB scaled by 1.326, 153.8 MB, and 95.9 MB scaled by 4,369,879 / 2,499,303, 167.7 MB, rounded up), record the check as deferred (Q18), and try again at the next check; until
L is read, no ceiling, since none can be grounded.*

**Q7. Should the page replace its tables silently when a generation changes, and if it should say so, in which words and on which surface?** Today an open
tab never replaces them: the A06 witness's page took one usable answer per loader and made 0
static requests in 25 simulated hours while the backend restarted into a changed publication,
and 0 of the changed coordinates reached the map (run for this design four times on 2026-11-28 under Node v26.0.0, 2.5.4). The ledger measured what that costs on NJ Transit's
stop table: of the 172 ids served from the 2026-11-01 archive, 101 named another station in the
2026-11-06 publication and 22 were gone (ledger:610). The page
already changes what it shows every poll without a word: the live envelopes are `no-store`
(`backend/cache.py:300`), and train numbers, destinations and next stops are joined on the
server and read off the row (`frontend/helpers.js:4885`, `:4888`, `:4889`). The page never
fetches `/api/status` (`frontend/helpers.js:1859`), and the status line has no clause for a
schedule's generation. *Recommendation: yes, silently; a sentence about a schedule change gives
a rider nothing to act on, and the tables it replaces only make the map agree with the rows it
already shows.*

**Q8. Should the stop witness, beyond withholding a name, trigger a check and raise a code, at what
threshold for each and for the decode that ends a stop run, and should the name be withheld per trip, on either of that trip's own marks, a call at a stop id the held table lacks or a pair above what speed?** It was measured for this design on 2026-11-28 by `speed_witness.py`, re-derived by
`verify_speed.py`, on four arms, two right and two wrong, every new-numbering arm a stand-in
(no realtime capture with per-call times exists on the new numbering). Stop ids the held table
lacks: none in any right arm, and 20 distinct ids in every wrong arm, on 69 of 90 trips (old
realtime under the new table), 85 of 90 (2026-11-03's shape) and 1,151 of 1,387 (the new
schedule under the old table). Pairs above 250 km/h under the held table: 8 of 1,130 (0.7%) on
the right arm, and on 2026-11-03's shape 516 of 768 (67.2%) as measured and 556 of 919 (60.5%)
with absent calls dropped before pairing, as the decoder drops them (`speed_witness.py`'s
decoder-order arm, re-derived by `verify_extra.py`); per trip, 6 of 90 right trips have one,
and of 2026-11-03's shape 86 of 90 trips do under either pairing, the slowest of them at 256.1
km/h, while the other 4 (trains 3953, 3957, 3961 and 3963), which the first pairing cannot
measure, each call at 3 or 4 stop ids the held table lacks (`verify_extra.py`,
`r5_d1_trips.py`). All 14 right pairs above 160 km/h lie on express trips, of which 12
are local pairs on late trains whose predicted delay falls between two calls faster than a train
can recover it and 2 are express pairs at calls the trip's own schedule does not list
(`dump_trips.py`, `verify_express.py`). *Recommendation: yes to a check and a code, both at 5 or
more trips in one decode calling at a stop id the held table lacks, or 25% or more of a
decode's measured pairs above 250 km/h, the code held 600 s as `njt-schedule-behind` is; and the code be
`njt-schedule-behind` itself, its note widened to name which witness raised it, since the cause
is the same held schedule. A stop run ends only at a decode in which no trip calls at a stop id
the held table lacks, as no call did in any right arm (3.8), and under 25% of its measured pairs
exceed 250 km/h. Withhold a refuted or unjoined trip's last-stop name on either mark of that trip,
a call at a stop id the held table lacks or a pair above 250 km/h (6 of 90 right trips have such a
pair; of 2026-11-03's shape, 86 of 90 trips do, the smallest trip maximum 256.1 km/h, and the other
4 each call at 3 or 4 stop ids the held table lacks).*

**Q9. What should the MISS thresholds be: 3 or more misses that are not ADDED in one decode to
start a MISS run that triggers a check, the run ending only at a decode that can judge (`checked`
at least 5, `backend/feeds/njt.py:599`, `:609-613`) and counts no miss that is not ADDED, the steady
state's measured count; and 5 or more making 10% or more of the running trip updates, held 600 s, for
a code?** In every steady-state measurement, misses that were not ADDED were 0: all 108 non-empty trip ids of the committed capture named a trip in the full live publication of its run (the trim
keeps a capture trip only when that publication has it,
`backend/scripts/njt_fixture_trim.py:290`), and production in step read no unjoined train in
reads of 29, 73, 2, 6 and 8 trains (ledger:589, record:589, and two reads for this design on 2026-11-28; 2.6a). ADDED trains are
told apart by their `schedule_relationship` (`backend/feeds/njt.py:941`), the empty id of their `njt:` key (`:853`) checked beside it (3.8). The generator allows "a handful"
of misses at a publication boundary crossed between its two downloads
(`backend/scripts/gen_njt_rt_fixture.py:174-176`). On the three shapes, at train level:
2026-11-03's 56 of 56, 2026-11-22's 8 of 50 sampled trips (16.0%) and 13.5% to 16.7% per poll
over 30 polls, and 2026-11-27's 3 of 34 trains (8.8%), whose ADDED share is not recorded (PR
#137's body; `miss_share.py` over `backend/tests/fixtures/njt_drift_2026-11-22.json`; record:589, :649). A trigger costs one NJ Transit download
and no mint; a code is read by the monitor as a FAIL (Q4's evidence). *Recommendation: yes to
both numbers and to that end of a run, so a count moving about the trigger starts one run and
not one per crossing; the code again `njt-schedule-behind`, so one cause reads as one code;
2026-11-27's 8.8% stays below the code because its mismatches already raised it.*

**Q10. Should the 30-day cold-start rule survive?** `MAX_AGE_DAYS = 30` sits in six modules and
is read only inside a load, at six `cache_is_fresh` call sites from
`backend/static_data.py:403` to `backend/bus_static.py:273`. In production it is never reached:
every deploy is a cold start, since the data directory does not persist
(`backend/static_shared.py:598-600`; `backend/bus_static.py:41-42`), and a cold start always
downloads. The one path that could reach it is a restart that keeps the data directory
(`railway.json`: `restartPolicyType` `ON_FAILURE`, 10 retries), and whether a restart keeps it
is not measured. Under the design a running process checks every archive on its timer, which
does what the age rule did, more often. *Recommendation: no; remove it with Step 4, and have a
start that serves an archive from a cache run that archive's first check at once rather than
one timer period later.*

**Q11. Should there be no volume, as the design is written?** A volume would let a cold start
serve the last good generation while it checks, survive an upstream outage at deploy (today a
failed cold download leaves the subway raising, NJ Transit, PATH and the ferry empty and a
railroad system `None`, each retrying: `backend/static_data.py:412-415`,
`backend/njt_static.py:1289-1291`, `backend/path_static.py:526-528`,
`backend/ferry_static.py:439-441`, `backend/railroad_static.py:535-537`), and make the 30-day
branch live again (Q10). What a cold start costs without one is 3.9's baseline (2.1): 28 to 93 s from `in_progress` to the first promotion and 34.7 to 100 s to NJ Transit's, plus one cold mint per deploy (`backend/njt_auth.py:172-173`). No deploy on record met an upstream outage.
*Recommendation: yes, no volume; revisit it if a deploy meets one.*

**Q12. In a refresh, should NJ Transit's calendar guard (a) refuse a candidate that schedules
no service on today's Eastern date while the live generation does, and (b) keep serving a live
generation whose service has ended when no candidate passes?** (a) The guard refuses only a
span that has ended or is empty (`backend/njt_static.py:669-679`); a publication shifted 132
days into the future, scheduling 0 service ids today, was accepted (`m8_calendar_and_age.py`,
the real gate at an injected noon New York), and so was a single ADDED day of 2027-01-27 judged
on 2026-11-28 (`v8_probe.py` part A). NJ Transit's public publications each start 1 or 2 days
before their `Last-Modified` date (five read by `parse_gtfs.py` and `v_gtfs.py`, and the
2026-11-20 one by the drift fixture's recorded endpoints), so a future-only one would be
unusual; adopting one would leave today's trains joining a schedule that does not run today, by
the decoder's join rather than by measurement. (b) At a cold start an expired cache is refused
and, with no download, NJ Transit is served empty (`backend/njt_static.py:1289-1291`); the F07
witness shows today's running process keeping an expired one as `ready` with `/healthz` passing
(its run for this design on 2026-11-28). Under the design a refused candidate keeps the live
generation (3.3) and Q4's code names it. *Recommendation: (a) yes, for a refresh only, the cold
start's rule unchanged; (b) yes, under Q4's code, with the service end on `/api/status`,
because an empty NJ Transit names no station at all.*

**Q13. Should a refresh refuse a download with bytes after the archive's end?** A complete
archive followed by up to 65,535 bytes was accepted by the real gate and would be promoted, and
one byte more was refused, on both the public archive and the zipped fixture; the 33-byte
invalid-token message was accepted (`v9_witness.py`'s sweep through the real `validate_archive`
and `validate_njt_publication`). Every strict prefix checked was refused (73,001 of 73,001 on
the 3,295,223-byte public archive, the same sweep). A `getGTFS` answer with anything appended is not the publication as NJ Transit built it. Its sha256 differs from the publication's while its `content` (3.1) does not, so 3.1's comparison is not misled by the bytes, but nothing else catches them either: a new publication with bytes appended would be promoted with them, as today. *Recommendation: yes, in a refresh, where a generation is held; a cold start keeps
promoting it and records the trailing bytes, since refusing it there serves no NJ Transit.*

**Q14. Should the generation drop the structures no reader reads: the whole NJ Transit parse,
`railroad_static` and `path_static`; and should `njt_stop_schedule` stay until a branch reads it?**
No production code reads any of the four (2.1, a `git grep` at `16d1c19`; 3.1). The savings
are 50,236,023 bytes for `njt_static`'s shapes table alone (tracemalloc deletion delta,
rail-only projection, `verify_m4a.py` part C), 56,535,728 bytes for `railroad_static` and
22,651,175 for `path_static` (recursive `sys.getsizeof` walk, `verify_m4c.py`), each held once
per generation and twice during a swap. A fourth structure is held and unread,
`njt_stop_schedule`, 4,911,728 bytes on the rail-only projection (tracemalloc deletion delta,
`verify_m4a.py` part C); 3.1 keeps it, because `backend/main.py:273` reserves it "for the panel
era". *Recommendation: yes for the three; `njt_stop_schedule` stays until the branch that reads
it, or drops with the three if that branch is not planned.*

**Q15. Should the possible fourth window, 2026-11-10 to 2026-11-12, go on the ledger's live
list as unmeasured, and not into the count of three?** Production ran `590054a` from the cold start that
promoted its archives on 2026-11-08 at 17:48:40 to 17:48:54 UTC until the 2026-11-12 06:36:34 UTC
promotion (ledger:589, :655), very probably on the publication that cold start loaded through run 447's read (1.1.4), while the API's archive changed between runs 445 and 446 and run 445's realtime contradicted the schedule it had just downloaded (1.1.4). If production's held archive and run 445's download were one publication, which their printed fields allow and do not show, production's own decodes met run 445's contradiction very probably for at least 23 h 51 min 14.85 s, from run 445's first line to run 447's step opening (arithmetic over 1.1.4's stamps), and for at least 34 h 46 min 45.75 s before that promotion if no restart came after run 447's read, which no source excludes (1.1.4), and the API's move came 23 h 57 min 42.69 s to 34 h 47 min 4.87 s before that promotion (1.1.4's table, over the runs' step and line stamps). What riders read is not measured: nobody read production's count then
(ledger:589), run 446's production lines carry none (`gh run view
42973821279 --log`), and the republication record counts three (record:592).
*Recommendation: yes, as a possible window with its bounds and the word "unmeasured", for the
ledger to rule on.*

**Q16. Should the builder run in a thread rather than a subprocess?** A thread is what A09's
rule and the subway already use (`backend/warmups.py:180`, `:186`): through the real subway
warmup, with its heavy work in threads, the loop's longest gap was 16.3 to 18.3 ms
(`m1_measure.py warmup`, three runs) and 17.44 ms (`runtime_measure.py`, its cold arm). A thread cannot be cancelled (`backend/main.py:188-190`), so only a subprocess gives a hard CPU
deadline; its cost, a second interpreter and the generation copied back, is not measured. The
longest gap a gate in a thread left was 45.3 ms (`gate_thread_gaps.py`), against a 45 s poll
deadline (`backend/pollers.py:108`) and the page's 15 s fetch deadline
(`frontend/helpers.js:1702`). With one worker
(3.2), every other archive's check, NJ Transit's evidence-triggered ones included, waits for the
build in progress. A CPU-bound build ends on its own ("a CPU-bound parse finishes on its own",
`backend/main.py:192-193`), so the wait is the build's own length: the A09 witness's run, whose
arms 2 and 3 promoted its 536,871,856-byte, 70,005-member and 2,147,483,720-declared-byte archives
through the real gates, took 18 s in all (2.8), and a build over those archives is unmeasured.
What a thread cannot bound is the build's memory, since the subway's routes-per-station loader
reads `stop_times.txt` whole ("inflates it whole",
`docs/reviews/audit-2026-10-12/a09_unbounded_upstream.py:115`); Q6's ceiling addresses that, and a
subprocess would keep that memory out of the serving process. *Recommendation: a thread.*

**Q17. Should Step 0, the monitor printing the API archive's identity on the mint it already
spends, come first?** The API archive's size has not been read since 4,369,879 bytes on
2026-11-07 (ledger:653); whether `getGTFS` sends a validator is
unknown, because the app drops its headers (`backend/njt_auth.py:716`); and whether two downloads of one publication are byte-identical, which decides whether 3.1's `sha256` would do where it proposes `content`, is not measured. Each scheduled run already downloads it on its one token
(`backend/scripts/contract_monitor.py:2074-2097`, `:2345`), and a change to `backend/scripts/`
deploys nothing (`railway.json`'s watch patterns exclude `/backend/scripts/**`).
*Recommendation: yes; its first two runs say whether NJ Transit's check could compare by `sha256` alone.*

**Q18. Should a check that defers (no live token, a borrowed token refused on `getGTFS`, or memory
over Q6's ceiling) record `last_check: deferred`, counted apart from `consecutive_failures`?** 3.2 and 3.5 add a fifth outcome, `deferred`, for 3.4's two cases and, if Q6 sets a ceiling, for Q6's. A deferral can be long: while a hold runs, `get()` serves the held token whatever
its age (`backend/njt_auth.py:1087-1098`), and an allowance or quota hold lasts to the next
Eastern midnight plus 60 s (`:1107-1112`, `:1164-1165`); measured on a local fake by
`m9_token_during_refresh.py` (its case R5b), an over-age token served a refresh 23,972.5 s
before its allowance hold ended, a length set by the hour that run's clock read. Counted as failures, a deferral would raise Q4's code during a
budget hold that `njt-mint-allowance` or `njt-mint-quota` already names. A refused post is named by
no code unless the trains poll meets the same refusal and re-mints, which 3.4 assumes and nothing
has measured: `is_auth_error` matches only HTTP 500 with "Invalid token."
(`backend/njt_auth.py:529-584`). If only `getGTFS` refused a token the trains poll kept using, each
check would re-post it after the trains poll's next decode (3.2's table), once a poll cycle
(`POLL_INTERVAL_S`, 20 s between cycles, `backend/pollers.py:70`), until the token reached
`MAX_TOKEN_AGE_S` (12 h, `backend/njt_auth.py:302`). Counted apart from failures, those re-posts
could reach Q4's code only by its age arm, which 12 h does not reach at three of Q2's recommended
6-hour periods (18 h, arithmetic); if that arm leaves deferred time out, as Q4 poses it, they reach
it by neither arm, and a `getGTFS` that refused each new token as well would defer with no code at
all. A deferral, uncounted as this question asks, would still raise Q4's code through an age arm
that counted deferred time, in an allowance or quota hold that ends more than three timer periods
after the last good check, 18 h at Q2's recommended 6 h; every allowance or quota hold begun before
06:00 Eastern ends that late, since it runs to the next Eastern midnight plus 60 s
(`backend/njt_auth.py:1107-1112`, `:1164-1165`; arithmetic over those literals). Q4 therefore
asks whether that arm leaves deferred time out. *Recommendation: yes.*

**Q19. Should the validity trigger fire when today in New York comes within 30 days of a generation's `service_through`, and once more when today passes it, once per generation at each of the two crossings rather than at every check while a span is past?** No other question asks for the band, and 3.2 gives it no number. The monitor already bands NJ Transit's span at 30 days, "deliberately the same 30 days as FEED_END_WARN_DAYS" (`NJT_SERVICE_WARN_DAYS`, `backend/scripts/contract_monitor.py:485-487`). PATH's held archive runs 20250522 to 20260601 and its publisher has not republished since 2025-06-06 (3.1, 3.2), and on all 86 attempts of runs 400 to 481 the monitor printed the acknowledged `path-static` WARN on `feed_end_date 20260601` (2.2), so a trigger read as a state rather than a crossing would fire at every PATH check from the first. The F07 witness held a calendar-expired NJ Transit archive at HTTP 200 across 40 poll cycles with `/healthz` passing (`docs/reviews/audit-2026-09-05/f07_static_never_refreshes.py`, run for this design at `16d1c19`, 2.8). *Recommendation: yes, 30 days, once at each crossing, the timer otherwise.*

**Q20. Should a component that holds no generation after the cold start, a railroad system or a bus borough, be checked on the warmups' rungs, 15, 30, 60 and then 300 s, jittered 0.9 to 1.1, until it holds one, and only then join its timer; and should a refused or failed check of a public archive that holds a generation wait for its next timer tick?** Q3 asks only for NJ Transit's rungs, and the failure rungs of 3.2, 4.2 and Step 4 carry no number. The warmups' rungs are `STATIC_RETRY_SCHEDULE_S`, 15, 30, 60 and `STATIC_RETRY_S`, which is 300 (`backend/main.py:125`, `:163-165`), jittered by 0.9 to 1.1 (`backend/warmups.py:111`). Today a railroad system that failed is tried once in 100 shortened intervals (the F06 witness, `docs/reviews/audit-2026-09-05/f06_partial_startup_no_recovery.py`, run for this design at `16d1c19`, 2.8), and the bus index builds once (`backend/main.py:357`); a whole archive that fails keeps its warmup's own loop (3.2, 4.2). Q4 reads "Three failures is three timer periods for a public archive at Q2's hour", which assumes a public archive's refused or failed check waits for its timer; 3.2's outcome table and its backoff leave that wait to this question. *Recommendation: yes to both.*

**Q21. Before assigning a new NJ Transit generation, should the worker decode the trains poll's latest trip-update body against the candidate and against the live generation, and keep the live generation, recording the reason "the trains are not on this publication yet", when the trains are in step under the live generation (a decode that can judge, its misses under Q9's trigger) and not under the candidate (behind, or its misses past that trigger), retrying on Q3's rungs?** On 2026-11-27 the bounds allow the API first by up to 11 h 28 min 26.37 s, and on 2026-11-10 by under 19.12 s (2.3's table); a check in that window passes every gate of 3.3 and is promoted, and nothing then returns to the generation the trains agree with (3.2). On 2026-11-03's shape the new table under the old realtime reads 682 of 917 pairs above 160 km/h and names another station as the last stop on 63 of 90 trips (2.6b, arm B). A live decode that cannot judge (`checked` under 5, `backend/feeds/njt.py:599`, `:609-613`), as at night, would not hold the candidate back. The decode is the pure decoder's, run in the worker's thread on a body the trains poll already fetched, so it costs no request; its time is not measured. Counted as `refused`, three such checks would reach Q4's code during a lead the trains close by themselves. *Recommendation: yes, with the held check counted apart from `consecutive_failures`, as Q18 counts a deferral, so a lead raises no code.*

**Q22. At a swap of the subway's generation, should a failed feed group's retained trains, decoded under the old generation, be dropped, rather than served in an envelope that names both generations?** An envelope carries one stamp per archive (`static_generations`, archive to stamp, 4.4), and 3.1's live-state rule never merges retained trains into another generation's envelope. Today a failed group's trains are carried forward for up to `FEED_RETENTION_MAX_S`, 600 s (`backend/cache.py:104`; 2.4.7), as "honest context a rider can use" (`backend/cache.py:96-97`). Dropped, that group's trains leave the map from the swap until it decodes; kept, the envelope must name two generations for one archive, and the old one stays alive while they are served (3.1, 3.10). NJ Transit is one system, and each railroad system is its own archive (`railroad_LIRR`, `railroad_MNR`, 3.1) retained per system (`backend/pollers.py:757-771`; 2.4.7), so a failed poll keeps that archive's rows under the one generation they were decoded under and the question does not arise for them (`backend/pollers.py:379-380`). How often a swap meets a failed group is not measured; the subway changed once in runs 400 to 481 (3.2's table). *Recommendation: yes, dropped: what they lose is at most 600 s of trains already shown as stale, and the envelope keeps one stamp per archive.*

**Q23. When one bus borough's publication changes, should the build download all six and write a whole new directory, rather than keep a record of which borough wrote each route and rebuild only that borough's routes; and should a bus refresh in which any borough fails be `failed`, keeping the live directory whole?** The build keeps no borough archive (`backend/bus_static.py:211`), takes a route from the first borough that has it (`:128`), and records in its manifest only `routes`, `failed` and `built_at` (`:246-249`), so a route a changed borough drops can leave only by a rebuild from all six or by a new record (3.1). A rebuild downloads 54,633,368 bytes at today's sizes (2.1). The six archives' Last-Modified values fall within four seconds of each other (2026-10-16 15:14:41 to 15:14:45 UTC, `probe_headers.py`, 2.2), so the one change read moved all six. A borough that failed at the cold start wrote nothing, so its recovery adds its routes to the live directory's under either answer (3.1). Today a failed borough is logged and skipped and the other five are kept (`backend/bus_static.py:239-242`). *Recommendation: yes to both: rebuild all six, since they publish together, and keep the live directory over a partial one.*

**Q24. Should `AlertFeed` carry, per system, the stamp of the generation the server holds, and the page join a system's alerts to its stations only while its own stamp for that system matches?** Alerts carry no generation today: `AlertFeed` has no such field (`backend/models.py:956-968`), each alert's stops are the publisher's own `informed_entity` ids, kept as they arrive (`backend/feeds/alerts.py:349-354`), and 4.4's list of envelopes that gain `static_generations` names it only if this question says so. The page joins alerts by `system|stationId` (`frontend/helpers.js:5229-5235`) for the subway, LIRR, Metro-North, the ferry and NJ Transit (2.5.3), so NJ Transit's alerts land on whichever station the tab holds under the id they name. That id follows the publisher's current numbering, which a held table can lag: of the 172 ids production served from the 2026-11-01 archive, 101 named another station in the 2026-11-06 publication and 22 were gone (ledger:610). A stamp would tell the page that its table and the server's disagree, not that the alert's ids match either, since no generation decodes them: while the server itself holds a schedule behind the publisher, the stamps agree and the join is as exposed as today. *Recommendation: yes; while the stamps differ the page leaves that system's alerts off its stations, since an alert on another station misleads where one left off only omits, and the window lasts only until the reload lands (3.7).*

**Q25. Should the refresh worker start no build until each cold-start warmup has returned or ended its first attempt, and the bus build has returned?** The lifespan starts the five warmups and the bus build as concurrent tasks (`backend/main.py:343-357`); each warmup loops until it succeeds, on rungs of 15, 30, 60 and then 300 s (`backend/warmups.py:164`, `:215`, `:288`, `:341`, `:414`; `backend/main.py:125`, `:163-165`), and the bus index builds once (`backend/main.py:357`, `backend/bus_static.py:262-309`). Under 4.2 each warmup hands its archive to the worker where it returns, and NJ Transit's first decodes can trigger a check at once (3.2), so after a Redeploy that loads a still-behind archive the worker can build a second NJ Transit generation while the other archives still load. What that costs is not measured: 2.1 measured memory with the sets loaded one archive after another, and NJ Transit's public stand-in alone peaked at 99.5 MB under tracemalloc during gate and load (2.1, 3.10). On the eight cold starts of 2.1 the last archive was promoted 13 to 41 s after the first (3.9), and the bus build's end is dated by no record read (2.1). A failing warmup never returns, so a wait on every return alone would hold NJ Transit's checks back for as long as another archive's upstream is down. *Recommendation: yes, so 3.10's bound holds from the first refresh, with a first attempt that fails counted as ended, so an archive that is down holds no check back; a later retry of that archive can still build beside a refresh, which this leaves open.*

### 5.3 Out of scope

**It does not decide what the list before section 1 puts out of scope**: the age limit on a stalled feed's rows, which the record sends to "a later amendment of the freshness contract" (record:620); more than one worker (ledger:332; `README.md:724`), since a generation in one process's memory is this design's state and a second worker would hold its own; and A09's byte, member and expanded-size ceilings beyond what a refresh needs, whose arms 2 and 3 stay on the live list (ledger:595).

**It does not change the mint budget.** `DAILY_MINT_LIMIT` 10 and `DAILY_MINT_ALLOWANCE` 4
(`backend/njt_auth.py:183`, `:219`) are borrowed as they stand; the design's claim is only that
a refresh adds nothing to them (3.4).

**It does not re-measure the third incident.** Its numbers are the republication record's at
`a0ed9f4`.

**It does not probe NJ Transit's token-gated server.** Its token lifetime, whether it can end a
transfer when a token expires, how it frames a body, and whether an abandoned getToken is
charged are unknown, since none of them was probed, and stay so; the witness of 3.4 models both
server behaviours on a local fake.

---

## 6. The order of the build

Six steps, **NJ Transit first**, one branch each. Each states its acceptance in the auditors'
terms, because those are the sentences the work will be judged against, and paraphrasing them
loses the clause that is usually the hard one (PR #108's section 6). Steps 1 to 4 deploy. Steps
0 and 5 touch only `backend/scripts/`, `backend/tests/` and `.github/`, which `railway.json`'s
watch patterns exclude or never name, so their merges leave the skip shape on the production
deployment record: `in_progress` then `inactive`, with no `success` (the three records of
2026-11-03 to 2026-11-07 and 7674420159 for `4489202`, each read with `gh api
.../deployments/<id>/statuses`).

**The watch, for every step**, as the contract monitor's watches have been kept: two scheduled
monitor runs after the merge, first attempts only, named by run number with databaseId and
`created_at`; no `workflow_dispatch` and no re-run to fill a gap, and no mint but each run's
own. Cron slots are not run times: 58 scheduled runs came from run 422 to run 481 over 26.5844
days, 2.182 a day (`durations.py` and `rederive.py` over `gh run list`), so two runs take 22.0
h at that rate (arithmetic over the rate). A deploying step's watch also reads its deployment
record to `success` and, by read-only GETs of production, `static_archives` and `/healthz` on
its day one, with `njt_mint_allowance` beside them.

### 6.0 Step 0: the monitor's identity line (no deploy), and the channel if Q5 says here

**What it builds.** 4.6's first item: `njt-static` prints the API archive's sha256, `content` digest, bytes, member sizes, trips, `stop_times` and `calendar_dates` rows and any `ETag` or `Last-Modified`
the response carried, on the run's one token; the four public static lines print the sha256, `content`, `ETag` and `Last-Modified` of what they downloaded. If Q5's answer puts the channel in Step 0
rather than on its own branch, it lands here as its own commit. Under Q5's recommendation, (a) on its own small
branch before Step 1 (3.6), it is not one of these six steps.

**Deploys:** no.

**Acceptance.** No auditor's sentence is this step's: like PR #108's bus probe, it is a
measurement nobody has taken rather than a decision nobody has made. Its own: every scheduled
run prints the API archive's identity, and the NJ Transit mint count per run stays one.

**Witness.** `backend/tests/test_contract_monitor.py`, new cases driving `check_njt_static`
through a fake fetcher that serves a committed archive with and without `ETag` and
`Last-Modified` and counts POSTs to the token URL; they assert the printed sha256 and sizes
against the archive's own, and exactly one token POST per run across `njt-static` and
`njt-realtime`. The same file gains one case each through `check_subway_static`,
`check_railroad_static`, `check_path_static` and `check_ferry_static`
(`backend/scripts/contract_monitor.py:1809`, `:1865`, `:1931`, `:1987`) over their committed
fixtures zipped, with and without `ETag` and `Last-Modified`, asserting the printed sha256 and
`content` against the bytes the fake fetcher served; each case truncates, once it is written, the
temp copy its line reads (`:1656-1659` for PATH and the ferry, `:1678-1682` for the subway, and for
LIRR and Metro-North, which parse from memory at `:1892`, the gate's copy at `:1701-1703`), so a
hash taken from that copy differs from the served bytes'. The field witness is the run log itself.

**Mutations, each one line the witness must catch:**

1. The sha256 taken over the first 64 KiB of the body rather than the whole: the printed hash
   differs from the archive's.
2. A second `_NjtToken` built for the identity read: two token POSTs in one run.
3. The identity printed only on a non-PASS line: the PASS line carries none.
4. The `ETag` read from the request's headers rather than the response's: the case with an
   `ETag` prints none.
5. The public lines hashing the bytes after `zipfile` re-reads them from a temp file the test
   truncates: the printed hash is the truncated file's.

**Watch.** Two scheduled runs, first attempts, each printing the identity line and one mint.
Their two sha256 values for an unchanged publication settle the point Q17's recommendation names: equal, and NJ Transit's check could compare by sha256 alone; different with equal `content`, and it must compare `content`, as 3.1 proposes.

### 6.1 Step 1: NJ Transit's generation, swap and refresh on a timer, no mint

**What it builds.** For NJ Transit only: the generation and its pure builder in a thread (4.1);
`app.state.static["njt"]` and the readers that take it once (`_refresh_njt`, `/api/njt-stops`,
`/api/njt-routes`, `/api/njt-arrivals`, `/healthz`'s NJ Transit block); live state stamped with
its generation (4.3); the refresh worker on Q2's timer, comparing by `content`, under a cold start's gates (3.3), one refresh at a time; the validity trigger of 3.2 (Q19); the borrow-only token path (4.1); and
`static_archives.njt`'s fields of 3.5 (4.5), so the watch can see a check happen. The `content` digest moves from
`backend/scripts/contract_monitor.py`, where Step 0 writes it, into `backend/static_shared.py`,
which the monitor already imports (`backend/scripts/contract_monitor.py:81`), so one function
computes both sides of Step 5's comparison; Step 0 cannot put it there, since `railway.json`
watches `/backend/**` outside `backend/tests/` and `backend/scripts/` (`railway.json:6-8`). The whole
parse is dropped if Q14 says so. If Q25 says so, the worker starts no build until each cold-start
warmup has returned or ended its first attempt and the bus build has returned (3.10, 4.2).

**What it leaves to Step 3.** Until Step 3 the page does not follow a swap: an open tab keeps
the tables it loaded (2.5.4), and a reload within the hour can take `/api/njt-stops` from the
HTTP cache (`backend/cache.py:373`). After a swap of a publication that renumbers its stops, as
2026-11-03's did, such a tab opens boards by old ids; in that renumbering 101 of the 172 ids
production served named another station (ledger:610). That is the state a hand Redeploy leaves
today (3.9), and the state every tab and production's own board were in before production loaded
that renumbering: at 2026-11-07 00:18:55 UTC (2026-11-06 19:18:55 Eastern) its board at 112, titled Newark Penn Station,
listed Morris & Essex trains (ledger:653). The order is the ledger's (ledger:682-694) and 5.1's item 38.

**Deploys:** yes.

**Acceptance, in the auditors' terms.** F07's: *"simulate a validity boundary and a changed
publication in a running service; the new snapshot is adopted without restarting, while a bad
replacement leaves the prior usable snapshot intact"* (audit-2026-09-05.md:218).
A06's: *"Coordinate NJT refresh with the authentication budget. Test a process crossing a
service-day boundary and a changing upstream publication without a restart"*
(ledger:267). Concretely, in one running app over a local fake
token server and a fake `getGTFS`: publication B replaces A at a timer tick and
`static_archives.njt.generation` reads B's identity, with no restart; a bad replacement C, refused by the
gate, leaves B serving with `last_check: refused` and the gate's reason; the clock crosses B's
last service day and a check runs at once with `last_check_trigger: validity`, before the timer's
next tick, and adopts D; a swap held at the poll's await serves 0 boards
under the other generation's names, where today it served 100 of 144 (`verify_m4a.py` part E);
and the refresh sends 0 getToken POSTs in every case, where today's chain sent up to 2 in one
attempt (`v9_witness.py`, its case V6).

**Witness.** Two scripts and one test file:

- `docs/reviews/audit-2026-09-05/f07_static_never_refreshes.py`, its NJ Transit arm rewritten:
  the real lifespan and pollers with A, B, C and D served from 127.0.0.1. It prints, per tick,
  the check's outcome, the held sha256 before and after, how many times the NJ Transit loader
  ran (once in 40 polls when the witness ran for this design on 2026-11-28), and the BEFORE
  line its old record printed.
- `docs/reviews/audit-2026-10-12/a06_njt_refresh_budget.py`, new, in the pattern of
  `m9_token_during_refresh.py` and `v9_witness.py`: the real `njt_post`, `TokenCache` and
  `_httpx_post` over a real socket to a local fake, refusing any other host. Its cases are
  theirs: a token 6 h old, one at `MAX_TOKEN_AGE_S`, one refused at
  request start, one killed mid-download in both server models, bodies cut under
  `Content-Length`, chunked and close-delimited, and a complete archive with trailing bytes; and two more of 3.4's: a transfer cut by its deadline (T7's shape, 2.7), which must show 0 getToken POSTs from the refresh, a `failed` record with its reason and the live generation byte-identical, and the close-delimited cut again on a borrowed token no poll has yet seen accepted. It
  prints, per case, getToken POSTs from the refresh and in the world, the outcome, the cache's
  bytes unchanged or not, and the token state after.
- `backend/tests/test_static_generation.py`, new: the builder writes no `app.state` and opens no
  socket; a builder made to raise on a second build assigns nothing, so `app.state.static["njt"]` is
  the same object after it, the status is unchanged and the check reads `refused` (3.1, 3.3; the
  instrument of `verify_m4a.py` part F, 2.4.9); the shortest of three builds' longest heartbeat
  gaps is under a quarter of the same build timed alone, the rule A09's arm 1d asserts
  (`docs/reviews/audit-2026-10-12/a09_unbounded_upstream.py:92-96`, `max_gap_ms < s_ms / 4` at
  `:1089`), whose quarter leaves room for a garbage collection on the worker thread (89 ms
  against 1,170 ms when the arm was written, ledger:425), where today's inline stretch was 2,813.1 ms
  (`verify_m4a.py` part D); and a poll held at its await across a swap serves 0 boards under the other generation's names (the instrument of `verify_m4a.py` part E), which catches mutation 2; and, in the same test with the real poll loop running, a swap made while that poll is held starts no second NJ Transit poll: the count of `_refresh_njt` calls (`backend/pollers.py:1032`) in flight at once is asserted never to exceed one, and once the rows carry the new generation no later write carries the old one, which catches mutation 8. If Q25 says so, a check that falls due while another archive's warmup is in its first attempt, or before the bus build has returned, starts no build until that attempt has ended and the bus build has returned, the test asserting that each build began after every warmup's first return or failed attempt and after the bus build's return.

**Mutations:**

1. The generation's fields built and assigned one at a time, as `backend/warmups.py:442-473`
   does: with one builder made to raise on the second build, `app.state` holds new fields beside
   old ones (3 new and 4 old when `build_njt_route_shapes` raised, `verify_m4a.py` part F), where
   the test asserts that nothing changed. A 1 ms watcher on the loop cannot see this mutation:
   the block holds no await, and 0 of 395 ticks saw a mixed set (2.4.1).
2. The poll reading `app.state.static["njt"]` again after its await: the held swap serves a row
   under the other generation's names.
3. The refresh calling plain `njt_auth.njt_post(url, {})`, as `backend/njt_static.py:773` does:
   the `MAX_TOKEN_AGE_S` case counts 1 getToken POST from the refresh, as
   `m9_token_during_refresh.py` measured today's chain doing (its case R2).
4. `asyncio.to_thread` removed around the build: the shortest heartbeat gap passes a quarter of
   the build timed alone.
5. The decorative gate run in cold-start mode (`cache_serves` false): bad geometry C is promoted
   over B with a degraded member instead of refused.
6. A refused candidate clearing the live generation: `/api/njt-stops` serves `[]` after C.
7. The validity trigger removed: D is adopted only at the next timer tick, and `last_check_trigger`
   reads `timer`.
8. A second NJ Transit poll started at the swap beside the one in flight: the in-flight poll,
   holding the old generation, ends last and overwrites the new poll's rows.
9. The refresh calling `invalidate` on a refusal: in the refused case the cache holds no token after the refresh, before any poll has met the refusal itself (`backend/njt_auth.py:1040-1045`).
10. The refresh calling `confirm` on its 200: on the borrowed token no poll had seen accepted, the cut close-delimited 200 leaves it read as accepted and no longer fresh (`backend/njt_auth.py:1227-1231`).

**Watch.** Its deployment record to `success`, and `static_archives.njt.promoted_at` set by the
new process; then two scheduled runs, each `production:njt-schedule` PASS or a FAIL naming what
production held. Between them, read-only reads of `static_archives.njt` show `last_checked_at`
advancing by Q2's period and `njt_mint_allowance` spending no more than the cold mint and the
twelve-hour ceiling's (`backend/njt_auth.py:302`). If NJ Transit republishes inside the watch,
its promotion is read; if not, the acceptance rests on the witnesses, and the watch says which.

### 6.2 Step 2: the evidence triggers, the witnesses, and the operator surfaces

**What it builds.** The MISS count and the stop witness in the decoder (4.3); a check at once
on `njt-schedule-behind`'s run, at the start of a MISS run (Q9) and at the start of a stop run (Q8), and Q3's
rungs while the evidence
holds; the last-stop name withheld by Q8's marks; Q4's code, with `production:healthz`'s WARN
on it beside a refused upstream line if Q4 says so (4.6), and, if Q8 and Q9 say a code, the
widened `njt-schedule-behind`; the monitor's "the refresh did not take" (4.6) and the model and
README notes (4.5, 4.10); and, if Q21 says so, a candidate held back while the trains are in step
under the live generation and not under the candidate, decoded in the worker's thread on the trains
poll's latest trip-update body (3.2, 4.3).

**Deploys:** yes.

**Acceptance, in the auditors' terms.** A06's again, *"a changing upstream publication without
a restart"*, now in each of the three incidents' shapes, and F07's remedy for the operator:
*"Report snapshot version, loaded time, valid-through date and next refresh"*
(audit-2026-09-05.md:216). Concretely: on 2026-11-03's shape (joined 0, checked
0) the MISS trigger starts a check and the new publication is adopted, the mismatch rule having
seen nothing; on 2026-11-22's (42 of 50 joined trips refuted, 8 missed) the behind run starts
one; on run 445's (the publication unchanged while the trains contradict it) the rungs retry
until the fake publishes and stop when the evidence ends; on 2026-11-22's, with the misses thinned
under Q9's trigger while the trains are still refuted, the rungs continue while the behind run is
open; and in steady state (the committed
pair's 4 ADDED misses of 90, 0 stale) nothing triggers. With three successive candidates refused
by the gate, Q4's code is raised on the refusal that reaches Q4's band, the third under its
recommendation, while `/healthz` answers 200.

**Witness.**

- `backend/tests/test_njt_refresh_evidence.py`, new, over the committed fixtures:
  `njt_drift_2026-11-22.json` (8 of 50 missed, 42 refuted, as `test_njt_drift_incident.py:29`
  asserts), `njt_tu.pb` with `njt_gtfs` (4 ADDED of 90, 0 stale; 0 calls absent and 14 of 1,130 pairs
  above 160 km/h, 2.6b's arm A), 2026-11-03's stand-in (50 of 50), a stop stand-in committed
  with its generator: `njt_tu.pb` with every stop id carried into the new numbering by 2.6b's
  station join, as arm D1 carries it, and every trip id replaced by one `njt_gtfs` lacks, since
  2026-11-03 moved both (2.6a), decoded under `njt_gtfs` (arm D1: 215 of 1,231 calls absent, 20
  distinct ids, on 85 of 90 trips, 2.6b); and the same stand-in with its trip ids kept, as arm D1
  keeps them (2.6b reads the stop table, not the trip table; `speed_witness.py`'s `translate` moves
  stop ids only), so its trains join `njt_gtfs` as the committed pair's do (86 of 90 joined and the
  other 4 ADDED, 2.6a; 0 mismatches, 2.4.6) and only the stop witness can trigger, the case
  mutation 7 needs. It asserts each shape's MISS share, mismatch count and stop-witness counts,
  which trigger fires, that a trip update marked ADDED but given a trip id counts as added, and,
  on the stop stand-in whose trip ids are replaced, that every unjoined train a mark names reads
  its line and number
  (`backend/feeds/njt.py:729-732`), not the held table's name for its last stop (`:726-728`), the
  case mutation 4 needs.
- `docs/reviews/audit-2026-10-12/a06_njt_refresh_evidence.py`, new: the real app with a fake
  TripUpdates feed and a fake `getGTFS` driven through the three shapes, 2026-11-22's again with
  its misses thinned under Q9's trigger, a run of decodes whose misses that are not ADDED move
  between 2 and 3, about Q9's recommended 3, a run of decodes whose trips calling at a stop id the
  held table lacks move between 4 and 5, about Q8's recommended 5, run 445's lead and three
  successive candidates the gate refuses. It
  prints, per shape, the instant each trigger fired, every check with its outcome, the instant of
  adoption, the codes `/healthz` raised and when, and `production:njt-schedule`'s line as the
  monitor would print it over that `/api/status`. If Q21 says so, it also drives the API first:
  the fake `getGTFS` serves a candidate whose trip ids the fake feed's trains do not carry while
  those trains join the live generation in step, and it prints the candidate held back with "the
  trains are not on this publication yet", no promotion, and the promotion once the fake feed
  moves to the candidate's ids.
- `backend/tests/test_contract_monitor.py`, new cases over a fake `/healthz` carrying
  `static-refresh-failing`: `production:healthz` FAILs beside an `njt-static` line that passed
  and, if Q4 says so, WARNs beside one that refused the same publication, the case mutation 10
  needs (`backend/scripts/contract_monitor.py:2856-2857`).

**Mutations:**

1. ADDED trains counted as misses (neither `schedule_relationship` nor the `njt:` key read): steady state triggers on every
   poll. With the `njt:` key read alone, the trip update marked ADDED but given a trip id counts as a miss.
2. The MISS trigger gated on `can_judge_schedule` (`backend/feeds/njt.py:609-613`): 2026-11-03's
   shape never triggers.
3. The evidence condition dropped from the rungs: checks continue after the evidence ends.
4. The withholding reverted: on the stop stand-in whose trip ids are replaced, an unjoined train is named by the held
   table's name for its last stop, where withheld it reads its line and number
   (`backend/feeds/njt.py:726-732`); under the old table the last call names another station on
   62 of arm D1's 90 trips (`diag_last_stop.py`, 2.6b).
5. `production:njt-schedule`'s FAIL text back to "redeploy": its test fails.
6. Q4's code added to `HEALTH_GATING_CODES`: `/healthz` answers 503 from the refusal that reaches Q4's band, the
   third of the witness's refused candidates under Q4's recommendation.
7. The stop trigger dropped: the stop stand-in with its trip ids kept, whose trains join the held
   trips but whose calls name 2026-11-03's new stop ids, never triggers a check.
8. The rungs ended on one signal alone: on 2026-11-22's shape, with the misses thinned under the
   trigger while the trains are still refuted, the rungs stop and the archive waits for its timer.
9. The MISS trigger fired on every decode past its threshold rather than at a MISS run's start: a
   count moving between 2 and 3 checks at each crossing.
10. If Q4 says so, that arm dropped: `production:healthz` FAILs on
   `static-refresh-failing` in a run whose `njt-static` line refused the same publication.
11. The stop trigger fired on every decode past Q8's trigger threshold rather than at a stop run's start:
   a count moving between 4 and 5 checks at each crossing.

**Watch.** Its deployment record to `success`; two scheduled runs, first attempts, by run
number, each with `production:njt-schedule` and `production:healthz` as printed; and, between
them, `static_archives.njt.last_check` read to see whether the evidence triggered any check in
production.

### 6.3 Step 3: the page's stamp and reload

**What it builds.** 4.4's stamps where a generation or a committed file stands behind them,
which before Step 4 are `/api/njt-stops`, `/api/njt-routes` and `/api/airtrain`, the NJ Transit
envelope and its arrivals board; for those three endpoints the `?g=` rule, and for the arrivals
board 4.4's answer to a `g` other than its rows' generation, stale rather than joined (3.7; 5.1
item 33); and 4.7's page: refetch on a changed stamp, registries replaced, joins made only within
one stamp, arrivals requests carrying their table's stamp, silent or said by Q7; and, if Q24 says
so, NJ Transit's stamp on `AlertFeed`, its alerts joined to stations only while the page's stamp
matches (4.4, 4.7), the other systems' in Step 4.

**What waits for Step 4.** The other nine static endpoints (`/api/subway-stops`,
`/api/subway-routes`, `/api/railroad-stops`, `/api/railroad-routes`, `/api/path-stops`,
`/api/path-routes`, `/api/ferry-stops`, `/api/ferry-routes` and `/api/bus-route/{route_id}`) and
the subway, railroad, PATH and ferry envelopes and boards gain their stamps in Step 4, with the
generations whose `content` they carry (6.1 builds NJ Transit's alone). Until then 4.7's page,
built here for every archive, treats an archive its envelope names no stamp for as unchanged.

**Deploys:** yes (`/frontend/**` and `/backend/**` are watched).

**Acceptance, in the auditors' terms.** A06's
*"ensure browsers discover changed static generations"*
(ledger:267); and F07's *"Let clients detect changed static
versions and update their registries safely"* (audit-2026-09-05.md:216), here for NJ Transit's,
the one generation a refresh swaps at this step. F06's last clause, *"its stations/routes appear in an
already-open browser without a manual reload"* (audit-2026-09-05.md:206), needs a component
that recovers without a deployment, which Step 4 builds, so it is accepted there (6.4).
Concretely, in the A06 witness's world: after the backend swaps NJ Transit's generation, the
open page makes one request per NJ Transit static loader and none for the others, draws every
changed coordinate where today it draws 0, leaves no line of the replaced payload drawn, and
holds no entry for an id the new generation lacks.

**Witness.** `docs/reviews/audit-2026-10-12/a06_frontend_static_once.mjs`, rewritten to pin the
repair: the production page in `node:vm` as today, the fixtures carrying the stamp, the backend
swapping one archive's generation at t = 360 s and again later in its 25 simulated hours
(2.5.4), the open page kept through both swaps, a second page loaded once within the hour of the
open page's boot and after the first swap, its requests and what it draws before its first poll
printed apart from the open page's, and an HTTP cache both pages share, modelled by URL under each
response's `Cache-Control` and `ETag`. It prints, per loader, the static requests after the swap, the v2
coordinates drawn, the v1 coordinates still drawn after the swap, the stale `njtStopCoords` entries, the duplicate registry entries, the trains glided under an envelope whose stamp the page did not yet hold, the arrivals requests sent without `?g=`, the NJ Transit alerts joined to a station while the page's stamp for NJ Transit differed from `AlertFeed`'s if Q24 says so, and its
old record's BEFORE line. `frontend/helpers.test.js` gains the stamp comparison as a pure
function.

**Mutations:**

1. The page ignoring the envelope's stamp: the open page draws 0 v2 coordinates, today's finding.
2. The bare URL served `public, max-age=3600` as today (`backend/cache.py:373` left as it is):
   the second page draws the old generation's coordinates from the modelled cache until its
   first poll names the new stamp, where under `no-cache` it revalidates and draws the new at once.
3. `njtStopCoords` refilled without being cleared: a gone id keeps its coordinates.
4. The registry appended to, as `frontend/systems/shared.js:2816` does: duplicate stations.
5. Every archive refetched on any change: requests for archives whose stamp did not change.
6. The comparison made only until the first refetch: the open page never sees the second swap.
7. The glide joining across stamps: a train under an envelope whose stamp the page does not yet hold glides toward a coordinate of the old generation.
8. Arrivals URLs built without `?g=`: the witness counts them, and an id the new generation gave another station is answered under that station's names, not as stale.
9. A route layer not cleared before the redraw: v1 coordinates stay drawn after the swap, where the answer is 0.

**Watch.** Its deployment record to `success`; two scheduled runs, first attempts, by run
number; and read-only GETs of `/api/njt-stops`, `/api/njt-routes` and `/api/airtrain`, whose
stamp headers match the prefix of `static_archives.njt.generation.content` and the committed
AirTrain file's sha256 (`data/airtrain_jfk.json`, 4.4).

### 6.4 Step 4: the other five archives and the bus index, with F06/A05's per-component retry

**What it builds.** 4.1's generations for the subway, LIRR, Metro-North, PATH and the ferry,
and the bus index's directory per generation; their conditional-GET timers (Q2) and, for each
archive with a span, the validity trigger of 3.2; the
per-component retry (4.2); one generation per poll for PATH (4.3); the live-state rule of 3.1 and
4.3 for the five archives (5.1 item 10: anchors of another generation dropped, retained trains
never merged across generations, `path_identity`'s new epoch, ferry buckets and the subway,
railroad, PATH and ferry arrivals carrying their generation); 4.4's stamps and `?g=` rule on the
other nine static endpoints and on the subway, railroad, PATH and ferry envelopes and boards; the subway builders taking a
path and refusing a lenient loader's empty result (3.3; 5.1 item 39); Q10's answer on the 30-day
rule; Q22's on a failed subway group's retained trains at a swap; Q23's on how a borough's change
builds a whole directory and on a bus refresh with a failed borough; `AlertFeed`'s other stamps if
Q24 says so; and the whole parses dropped if Q14 says so.

**Deploys:** yes.

**Acceptance, in the auditors' terms.** F06's whole: *"one provider fails at boot, recovers
without a deployment, and its stations/routes appear in an already-open browser without a
manual reload"* (audit-2026-09-05.md:206). A05's: *"Verify boot with one failed
system, subsequent upstream recovery and no process restart"*
(ledger:257). F07's again, for each of the five
(audit-2026-09-05.md:218). Concretely: Metro-North failing at boot is retried on
Q20's rungs and served when it recovers, LIRR serving throughout, where today it is tried once in
100 shortened intervals (the F06 witness, run for this design on 2026-11-28); a failed bus
borough is rebuilt; and an unchanged public archive's check downloads 0 body bytes.

**Witness.**

- `docs/reviews/audit-2026-09-05/f06_partial_startup_no_recovery.py`, rewritten: the real
  railroad warmup, `ensure_index` and lifespan with one injected Metro-North failure and one
  failed borough. It prints attempts per system and per borough, when each recovered, and its old
  record's 1 attempt per system beside them.
- `docs/reviews/audit-2026-09-05/f07_static_never_refreshes.py`, its remaining five arms: per
  archive, checks made, 304s, body bytes downloaded, promotions and refusals.
- `backend/tests/test_static_generation.py`, extended to the five archives and the bus index
  with 4.8's tests: each builder, given an archive outside `data/gtfs_static`
  (`backend/static_data.py:34`), reads that archive; a subway, railroad, PATH and ferry poll held
  at its fetch across a swap serves 0 rows under the other generation's names; the PATH poll's
  decode and matcher read one generation; a bus-route request made during a build reads one
  directory's route set; a subway refresh with a fault injected inside `load_subway_stations`, so
  that it returns `{}`, reads `refused` and leaves the live generation's stations serving; a
  subway feed group failing across a swap has its retained trains dropped at the swap, or served
  in an envelope that names both generations, as Q22 answers; and a bus refresh after one
  borough's publication drops a route leaves no file for that route, and a bus refresh with a
  failed borough reads as Q23 answers.
- `docs/reviews/audit-2026-10-12/a06_frontend_static_once.mjs`, a second world: Metro-North
  absent at boot from `/api/railroad-stops`, `/api/railroad-routes` and the railroad envelope,
  and its generation assigned at t = 360 s. It prints the Metro-North stations and routes the
  open page holds after the next poll, and, if Q24 says so, the railroad alerts it joined to a
  station while its stamp for that system differed from `AlertFeed`'s; today the railroad loaders stop at their first truthy
  answer (`frontend/helpers.js:5480`, `frontend/map.js:282`, `:284`), so an answer without
  Metro-North is the last they take.
- `docs/reviews/audit-2026-10-12/a09_unbounded_upstream.py`, kept green: its arms 1c and 1d drive the
  real subway warmup, which this step restructures (`a09_unbounded_upstream.py:136-137`), and its
  arms 1d, 2 and 3 call `static_data.load_subway_route_shapes()` and
  `static_data.load_subway_stops()` with no path (`:1063`, `:1130`, `:1179`). Either those
  loaders keep their signatures beside the builder that takes a path, and the script is
  unchanged, or this step passes the path in the script, with arms 2 and 3 still reproducing.
- `backend/tests/test_static_generation.py`, a serialization case: two archives' checks fall due at once, each builder stubbed to record its entry and exit. It asserts the second builder enters after the first returns, and prints the most builds in flight at once, which one refresh at a time (3.10) holds to one.

**Mutations:**

1. The railroad warmup returning once any system has stops (`backend/warmups.py:226`):
   Metro-North is never retried.
2. The conditional headers dropped: each unchanged subway check downloads the served archive's
   whole body where it should download 0 bytes (in production 5,606,305 bytes, its
   `Content-Length`, read by `download.sh` on 2026-11-28).
3. `_refresh_path` reading `path_station_order` from `app.state` after its await
   (`backend/pollers.py:933`): the matcher reads another generation than the decode.
4. The bus build writing into the live directory, as `backend/bus_static.py:169-176` does: a
   request during a build reads a mixed route set.
5. A second refresh worker started: two archives build at once, and the serialization case counts 2 builds in flight.
6. A subway builder reading `static_data.SUBWAY_GTFS_ZIP` rather than its argument: the builder
   test, given a path elsewhere, reads the wrong archive.
7. A stamp for an archive the page's tables never carried ignored: the second world's open page
   never draws Metro-North's stations.
8. A lenient loader's empty result promoted: with a fault injected inside `load_subway_stations`
   on the second build, the refresh promotes a subway generation with 0 stations where the check
   should read `refused`.

**Watch.** Its deployment record to `success`; two scheduled runs, first attempts, by run
number; `static_archives` read for all six archives and the bus index, each `last_check`
`unchanged` or `promoted` at its period; and read-only GETs of the nine static endpoints that
gain their stamps here, whose stamp headers match `static_archives`' identities.

### 6.5 Step 5: the monitor's held-versus-upstream check for every archive

**What it builds.** 4.6's third item: one production line comparing, per archive, the identity
production holds with what the run downloaded: the `content` digest for NJ Transit and for the public archives the run already downloads, and for the bus index a conditional GET of each borough
with the `ETag` production publishes for that borough (`static_archives.bus.generation.boroughs`, 3.1), which answers 304 with no body while they match (`probe_headers.py`,
2026-11-28). Its verdict is 3.5's rule: PASS when they match; FAIL when they differ and production checked that archive after the run's download yet holds a generation promoted before it, or its last check was refused or failed, or it has not checked for more than one period; WARN otherwise, production's generation promoted after the run's download or its check of it not yet due.

**Deploys:** no.

**Acceptance.** No auditor's sentence names the monitor. Its own: every archive production
holds is compared with upstream on every scheduled run, where today only NJ Transit's trains
are (`production:njt-schedule`), and the monitor reads nothing of production but `/api/status`,
`/healthz`, `/api/njt-routes` and one subway board
(`backend/scripts/contract_monitor.py:2709-2751`).

**Witness.** `backend/tests/test_contract_monitor.py`, new cases over a fake `/api/status` and
fake upstreams: the same identity PASSes; a newer upstream that production has not checked since the download, within its period, WARNs; a newer upstream that production checked after the download FAILs, in two forms, one renumbering its trip ids under the same `service_through` and one of the held archive's byte size; a newer upstream unchecked past its period FAILs; a refused check FAILs; a production generation promoted after the download, not the run's, WARNs; a fake `/api/status` carrying the identity the app's own `content` function computed over the bytes the fake upstream serves PASSes; and the bus comparison
downloads no body.

**Mutations:**

1. NJ Transit compared by its last service day rather than `content`: a republication that
   renumbers its trips and keeps its last service day passes.
2. Archives compared by size: a same-size republication passes.
3. `railroad_LIRR` and `railroad_MNR` read crosswise: both lines report the other's archive.
4. The bus boroughs downloaded rather than conditionally fetched: the test counts the six fake
   bodies where it should count none (in production 54,633,368 bytes for today's six, their
   `Content-Length`s, read by `download.sh` on 2026-11-28).
5. FAIL on any difference whatever `last_checked_at` says: a refresh not yet due fails the run.

**Watch.** No deploy; two scheduled runs, first attempts, by run number, each printing the new
line, PASS or a finding.

### What this order leaves for later

The stalled feed's age limit, the second worker and A09's ceilings stay out (5.3). The volume
waits on Q11. AirTrain's fixture changes only with a deploy and gets its stamp in Step 3 and
nothing else. F09 and H2, which the ledger's item 6 puts with this work where they touch the
same warmups (ledger:686), ride Step 1 for NJ Transit's builder and
Step 4 for the rest, each only if the builder it touches is the one that raises.
