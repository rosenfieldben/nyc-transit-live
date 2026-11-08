# `subway_groups/` fixture: which subway feed carries which route, measured

Step 0 of Amendment 1 to the freshness contract (`docs/design/freshness-contract.md`, A1.6 and
its Q1). `capture.json` is the reduction of every sample the temporary probe workflow took,
cut off when its seventh dispatched loop finished, so the route-to-group table Step 2 writes
beside `SUBWAY_FEED_URLS` cites a committed capture rather than a document's grouping. It was
written by `backend/scripts/probe_subway_groups.py --reduce` and nothing in it is edited by
hand. Every number below is read from it, from the samples it was reduced from, from the
workflow's run, job and artifact listings in the Actions API, or, under "The archive", from the
archive's response headers and production's `/api/status`.

The acceptance, A1.6: "every one of the live archive's 29 route ids is placed in exactly one
group by a committed capture, or is stated as unplaced with the capture that shows it absent."

## Placement

| Group | Feed (`.../mtagtfsfeeds/...`) | Route ids |
| --- | --- | --- |
| 1-7+S | `nyct%2Fgtfs` | 1, 2, 3, 4, 5, 6, 6X, 7, 7X, GS |
| ACE | `nyct%2Fgtfs-ace` | A, C, E, H |
| BDFM | `nyct%2Fgtfs-bdfm` | B, D (placed here by this README; `capture.json` has D under BDFM and NQRW, see below), F, FS, FX, M |
| G | `nyct%2Fgtfs-g` | G |
| JZ | `nyct%2Fgtfs-jz` | J, Z |
| L | `nyct%2Fgtfs-l` | L |
| NQRW | `nyct%2Fgtfs-nqrw` | N, Q, R, W (`capture.json` also lists D here, one sample, recorded below as an exception) |
| SIR | `nyct%2Fgtfs-si` | SI (and SS, absent from routes.txt, see below) |

The table lists the archive's 29 route ids, plus SS where the SIR feed carries it.

**28 are placed by the capture, D is placed in BDFM by this README on the capture's evidence,
and none is unplaced.** The 28 are placed by the capture alone: every live row that named
them came from one group, and `capture.json` lists them under `placement.placed`. D is not
among them, because the capture shows D under two groups and says so
(`placement.in_more_than_one_group`); the reduction shows a route in two groups and never
resolves it, so D's placement is this README's, made on the evidence below, with one
exception recorded. `placement.unplaced` is empty: no archive route went unseen. GS sits under
1-7+S by measurement (244 samples), where the key's first character would say G.

### D: placed in BDFM, one NQRW sample recorded as an exception

- **BDFM:** all 246 samples, 8,044 trip updates and 8,044 vehicles, first 2026-11-03T22:34:52Z,
  last 2026-11-05T19:37:29Z.
- **NQRW, the exception:** one sample, 2026-11-04T07:45:24Z (02:45:24 New York, the overnight
  of Tuesday's service day), with **one trip update and one vehicle** for D beside N 15, Q 16
  and R 10 in the same row; header age 5.97 s, so live. It came from the first dispatched loop
  (`subway-groups-loop-20261104T050524Z`). D never appeared on NQRW in any other sample, and
  not once in the fully covered Wednesday window.
- **The overnight-reroute reading is unverified.** A D trip run over N, Q, R or W tracks
  during overnight work, and so published by the NQRW feed, would explain one trip seen once
  at 02:45. Nothing here shows it: the probe records route ids and counts per feed, not trip
  ids, stops or service notices, so which D trip it was and why the NQRW feed carried it are
  not in the capture. Step 2's table will carry D under BDFM; this sample is what a reviewer
  needs if D trips on NQRW recur.

### SS: under SIR, absent from routes.txt

- 81 of 246 samples, 446 trip updates and 446 vehicles, every one on the SIR feed
  (`nyct%2Fgtfs-si`). Seen only in New York hours 05 to 08 and 15 to 20 (once on Tuesday at
  17; Wednesday 05 to 08 and 15 to 20; Thursday 05 to 08), first 2026-11-03T22:34:52Z, last
  2026-11-05T13:47:01Z. Those are the hours it was seen, not every hour it runs: Tuesday was
  sampled only at 17, at 21 and from 00 to 04, and Thursday only until 14:37 New York.
- **Absent from the archive's routes.txt**, whose 29 route ids do not include it:
  `routes.SS.in_archive` is false, and the capture lists it apart, under
  `placement.seen_not_in_archive` rather than `placement.placed`, and leaves it unresolved
  there. It is not one of the 29 the acceptance counts.
- **Step 2's table will carry SS under SIR, marked absent from routes.txt**, so Q1's
  completeness check reads it as placed rather than missing.

## The failed row: 1-7+S at 2026-11-04T13:05:48Z

- **HTTP 200 with a zero-byte body** from `nyct%2Fgtfs`, fetched 13:05:48.845Z, in the second
  dispatched loop (`subway-groups-loop-20261104T103548Z`). The probe records an empty body as
  a failure (`error: "empty body"`), so the row is in `failed`, the sample is the one
  incomplete sample, and the row is evidence for nothing.
- **What it was: a headerless, entity-less generation served as a success.** Zero bytes
  parses without error in the Python bindings (`ParseFromString(b"")` returns 0) into a
  message with no header (so no `gtfs_realtime_version` and no timestamp) and no entities;
  `header` is a proto2 required field, so the result is not an initialized FeedMessage, and a
  decoder that checks required fields rejects it. The server sent it with a 200. No outage
  shows in the neighbouring samples: the 1-7+S samples ten minutes either side (12:55:48Z and
  13:15:48Z) were live, at 239,295 and 239,450 bytes, and the other seven groups in the same
  sample were live. A fault shorter than ten minutes is not ruled out.
- It is the only failure in 1,968 rows; every one of them was a 200, and the smallest body
  among the live rows was 3,805 bytes.
- **The app's handling of such a body, measured by Step 2 (`claude/a03-expected-contributors`):
  a FAILED poll, not a decoded generation.** `parse_feed` refuses a zero-byte body (its rule 1,
  "empty body served as 200 (no protobuf header)"), and the subway fold counts that group
  failed, with the reason `undecodable protobuf (empty body served as 200 (no protobuf
  header))`. Through the real `_refresh_subways` and the served JSON, in F03's world one poll
  after a healthy one: `/api/status` lists 1-7+S failed (`{"total": 8, "ok": 7, "failed":
  ["1-7+S"]}`), its block reads `ok: false` with its last decode's clocks (fetched at the
  healthy poll, content 5 s before it) and `retained_since` set, and its 12 rows at 219 are
  served `retained`, which the page's renderers word `showing last known, as of 25s ago` on
  every row, in the popup, the panel and the spoken board. So it is not a headerless,
  entity-less generation served as a success (Q6's world on the subway). Pinned by
  `backend/tests/test_a03_expected_contributors.py::test_a_zero_byte_200_is_a_failed_poll_not_an_empty_generation`.

## The window

- **246 samples, 1,968 rows** (one per group per sample), from **2026-11-03T22:34:52Z to
  2026-11-05T19:37:29Z**, the last being loop seven's final sample, the cut-off.
- **245 complete, 1 incomplete** (the failed row above). No stale row and no undated row:
  a row is stale past 600 s of header age, and the median header age per group ran from 4.2 s
  (L) to 6.7 s (G), the largest 15.1 s (G).
- Every UTC hour has at least 6 complete samples (`coverage.by_utc_hour`).

### Weekday windows

A window runs 05:00 to 05:00 New York (10:00Z to 10:00Z after the 2026-11-01 change to
standard time) and counts only on a day the archive's calendar runs a weekday service; all
three days here run its `Weekday` service.

| Service day | Samples from | Complete | Incomplete | New York hours with no complete sample |
| --- | --- | --- | --- | --- |
| Tuesday 2026-11-03 | 22:34:52Z (17:34 New York), to 09:57:12Z on 11-04 | 33 | 0 | 05 to 16, 18 to 20, 22, 23 |
| **Wednesday 2026-11-04** | 10:05:24Z, to 09:57:01Z on 11-05 | **151** | **1** | **none** |
| Thursday 2026-11-05 | 10:07:01Z, to the cut at 19:37:29Z | 61 | 0 | 00 to 04, 15 to 23 |

**Wednesday 2026-11-04 is fully covered** (`coverage.weekday_windows_fully_covered` is
`["2026-11-04"]`): 152 samples, every New York hour with 5 to 8 complete samples, and none of
the 152 more than ten minutes after the one before. Between complete samples the longest gap
is twenty minutes, 12:55:48Z to 13:15:48Z (07:55 to 08:15 New York), around the failed row.

### Gaps

Two gaps over ten minutes between consecutive samples, both before the first loop:

| From | To | Gap |
| --- | --- | --- |
| 2026-11-03T22:34:52Z | 2026-11-04T02:40:38Z | 4h05m46s |
| 2026-11-04T02:40:38Z | 2026-11-04T05:05:24Z | 2h24m46s |

From the first loop's start (2026-11-04T05:05:24Z) to the cut, no two consecutive samples are
more than ten minutes apart: each loop samples on its own ten-minute grid, and each handed off
to the next within 28 s of its last sample. Counting complete samples only, the one longer gap
is the twenty minutes around the failed row (12:55:48Z to 13:15:48Z on 11-04).

## Where the samples came from

**By provenance:** 238 dispatched (seven loops of 34), 7 scheduled, 1 unrecorded, 0 local. The
unrecorded one is run 1, a scheduled run of the workflow as #133 merged it, before rows
recorded their provenance.

The fifteen artifacts in the capture, every one downloaded and checked against the sha256
digest the Actions API gives for it:

| Run | Run id | Trigger | Artifact | Samples | sha256 |
| --- | --- | --- | --- | --- | --- |
| 1 | 41732001374 | schedule | `subway-groups-20261103T223452Z` | 1 | `1c20235e87a6924e5a5f57cc1c85efefc5c1c76b93cc6944405b498fd59e5571` |
| 2 | 41762276981 | schedule | `subway-groups-20261104T024038Z` | 1 | `d60dfa5e356861c5962563d3f1384b749936c561349160b343fe0e84a67f722c` |
| 3 | 41777060178 | dispatch | `subway-groups-loop-20261104T050524Z` | 34 | `30fa104c9e6642e8a44bb164af68d9882da1ed93e104f534ea7f915ebf7bb349` |
| 4 | 41797100169 | dispatch | `subway-groups-loop-20261104T103548Z` | 34 | `08e60f00f84e8dd258e49fea606affdb49d2357fd27d36f070410253ac1faf07` |
| 5 | 41810920425 | schedule | `subway-groups-20261104T095712Z` | 1 | `98aedbbfbdee47e9efb944662b137f4559cd7f6b54a10e245db46fb72f04bbcd` |
| 6 | 41818718358 | dispatch | `subway-groups-loop-20261104T160608Z` | 34 | `daefe544416b97e3b51c80e962491e92ef1eb36d61c2e0680131167c2d01eb34` |
| 7 | 41874562141 | dispatch | `subway-groups-loop-20261104T213624Z` | 34 | `488d3b3dc52124eb9ecdea6cf91b9a74e20729bc0f6062481cf29327dc746ca6` |
| 8 | 41881012870 | schedule | `subway-groups-20261104T175101Z` | 1 | `89a040ff4730274fd457d3ef0f7adcf1be3668974aecd71dd6c81b9a592aa0be` |
| 9 | 41930988398 | dispatch | `subway-groups-loop-20261105T030640Z` | 34 | `c3b81e2e9adb35deaa12e74e85ee267d3c5c2fb0c44a43ee38dd9f4d864d2416` |
| 10 | 41931902177 | schedule | `subway-groups-20261104T231311Z` | 1 | `630734047be1e6e9fd740351e4aa0cc767e7b0ec610b5476d415b6fd0d1df6b7` |
| 11 | 41962214346 | schedule | `subway-groups-20261105T033229Z` | 1 | `12b27171cc0dfd7b1334ec487576131deac6a63dee8f8f7b30fcee5dc4a28fbe` |
| 12 | 41971998973 | dispatch | `subway-groups-loop-20261105T083701Z` | 34 | `2029a077d132b2d813bb3eadb451731dffe4b9c8fecafcb019394b84dd054311` |
| 13 | 42016309615 | dispatch | `subway-groups-loop-20261105T140729Z` | 34 | `3ca8a6ec731575a1ae8601302eb52771352b631d7b5dc2e517b3dfa569719cb7` |
| 14 | 42019255566 | schedule | `subway-groups-20261105T112850Z` | 1 | `9967dee7d2249709e53551a18e5d074e252e11f26bd9e668ccc018d36960b113` |
| 15 | 42088910076 | schedule | `subway-groups-20261105T185544Z` | 1 | `e387c8718b7908ec28fa83b1efe99b747846d710ea0777e832cbca7051482191` |

The Actions API gives them `expires_at` times from 2027-02-01T22:34:37Z (run 1) to
2027-02-03T18:55:27Z (run 15). The four artifacts after the cut (below) were not downloaded;
the cut-off puts them outside the capture.

### Lost scheduled runs, from the run list

The schedule (`*/10 * * * *`) was on `main` from 2026-11-03T17:40:59Z, when #133 merged, to the
cut, 2026-11-05T19:37:35Z, when loop seven's job completed (the jobs API; 6 s after its final
sample): **299 ten-minute slots**, 17:50Z on 11-03 to 19:30Z on 11-05. **8 scheduled runs were
created** in that time (runs 1, 2, 5, 8, 10, 11, 14 and 15), so **291 slots had no run created
before the cut**. GitHub does not say which slot a run answered, so this counts slots against
runs and says nothing of any one run's delay; the first came 4h53m38s after the merge. For the
same reason 291 is an upper bound on the slots lost: a later run may have answered one of
them, and run 16 was created 4h18m58s after the cut, less than that first delay. No run that
started was lost: all 19 runs in the list concluded success, none was cancelled, and each
uploaded its one artifact, so the capture's eight samples from scheduled runs (7 recorded as
`scheduled`, plus run 1's `unrecorded` one) are exactly the eight runs.

**After the cut**, outside the capture: runs 16 to 19 (scheduled, 2026-11-05T23:56:33Z,
2026-11-06T05:39:21Z, 13:14:44Z and 19:58:35Z), artifacts `subway-groups-20261105T235646Z`,
`subway-groups-20261106T053932Z`, `subway-groups-20261106T131459Z` and
`subway-groups-20261106T195847Z`, and any further scheduled run until the commit that deleted
the workflow reached `main`.

## The archive

The route ids and the calendar are the live archive's, from
`https://rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip` (the app's default `SUBWAY_GTFS_URL`,
`backend/static_data.py`), downloaded 2026-11-03 13:51:52 UTC: `Last-Modified: Wed, 28 Oct 2026
16:39:52 GMT`, ETag `"de495fc9e33cb8115581ae63807a25d8"`, 5,606,305 bytes, sha256
`acb122715bd90aeacf61f4559722ccf0b7de9c63317200a6dd7480246488f1e6`, `feed_version`
`20261028-fall-2026-pick-with-long-term-supplements`. Its routes.txt (sha256
`8f1917e6fb1feee9ad0158a5414b9de4bff8ca73ddb4818f9520c9acfae644b6`, in `archive`) holds the 29
route ids. The URL served the same ETag, `Last-Modified` and length on 2026-11-06 at 21:33:21
UTC, so no publication replaced it during the collection. Production downloaded its archive on
2026-11-01 at 15:14:16 UTC (`static_subway_gtfs.mtime` on `/api/status`), after that
`Last-Modified`, and promoted it at 15:14:31: by the dates it is this publication, assuming
production reads the default URL (a seam `/api/status` does not report), and no byte
comparison was made.

## Regenerating it

From `backend/`, with every artifact above downloaded, checked against its digest, and
extracted into one directory (any layout), and the same archive:

```
python scripts/probe_subway_groups.py --reduce SAMPLES --routes gtfs_subway.zip \
    --output tests/fixtures/subway_groups/capture.json
```

The reduction reads its inputs and nothing else and writes sorted keys, so the same samples
and archive give the same bytes: the fifteen artifacts reduced per artifact directory and as
one flat directory gave identical files. A different publication of the archive changes
`archive.routes_txt_sha256`, and may change the calendar's windows.

## What removed the workflow

The commit that added this directory deleted `.github/workflows/probe-subway-groups.yml` and
the workflow section of `backend/tests/test_probe_subway_groups.py`. The script stays, with its
tests: it regenerates this file, and a later probe can sample by hand
(`--out-dir`, provenance `local`). Its docstring and comments spoke of the workflow in the
present tense until `claude/njt-stop-identity` recast them in the past tense, touching
comments and docstrings only. That includes the argument that nothing it runs can spend an
NJ Transit mint, which the deleted workflow test pinned: it now rests on the script calling
no NJ Transit path, read from the code (the tests pin only the URLs a sample asks for), which
holds on a hand run whose `.env` carries real credentials.
