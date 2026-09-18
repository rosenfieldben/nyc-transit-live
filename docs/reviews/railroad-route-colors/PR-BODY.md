# Railroad routes: serve the feeds' own `route_color` and `route_text_color`

One backend commit plus one README sentence. `/api/railroad-routes` carries `color` and
`text_color` for LIRR and Metro-North, read verbatim from `routes.txt` exactly as
`NjtRoute` already does. Nothing in `frontend/` changes; nothing touches `njt_auth.py` or
any credentialed path, so **no NJ Transit mint was spent**. The endpoint's polylines are
not touched.

## The finding: two claims in the code were wrong

Both were true when written, and both are **corrected rather than deleted**, because the
reasoning behind them is what a future stage will reach for again.

> `railroad_static._parse_routes`: "route_color is deliberately NOT read: the project uses
> its own palette rather than agency branding (see the README MTA-branding note), so the
> agency colors are unused."

> `models.NjtRoute`: "Mirrors RailroadRoute and adds the two colour fields, because
> **unlike the LIRR and Metro-North feeds** this one publishes route_color."

**Probed 2026-09-18 against the live archives** (`rrgtfsfeeds.s3.amazonaws.com/gtfslirr.zip`
and `/gtfsmnr.zip`), which is how the numbers below were obtained rather than recalled:

| | Routes | `route_color` | `route_text_color` | Notes |
| --- | --- | --- | --- | --- |
| **LIRR** | 13 | set on **all 13** | set on **all 13** | **no `route_short_name` column at all**; Ronkonkoma (4) and Greenport (13) share `A626AA` |
| **Metro-North** | 6 | set on **all 6** | set on **all 6** | New Haven (3), New Canaan (4), Danbury (5), Waterbury (6) **all carry one red**, `EE0034`; Hudson green, Harlem blue |

So both feeds publish a colour for every route. What was actually true is only that this
project had chosen not to read theirs, which is a decision, not a property of the feeds.

**NJ Transit does differ, in the opposite direction from the old sentence.**
`route_text_color` is empty on all twelve NJT routes while both railroad feeds fill it
everywhere. So a renderer can trust a railroad `text_color` and must compute its own ink
for NJ Transit. That is now said in both places, because the failure mode it prevents is
dark text on a dark line, which is invisible rather than obviously broken.

Each correction says **what was believed, what the feeds carry, and which branch changed
it**. The same stale claim in `test_railroad_static.py`'s `ROUTES_COLS` comment
("deliberately not parsed (own palette)") is corrected with them.

## The branding argument does not reach a `route_color`

A colour an agency publishes as `route_color` in its own GTFS is **feed data**, read like
any other column. This is not a new position: the README's NYC Ferry note already says
exactly that about the ferry's colours, "the route colors are fine because they are data in
routes.txt, not branding". The MTA-branding note under **Notes** gains one sentence putting
the two on the same footing, and says what does not change:

- **Off-limits, unchanged**: the logos, the official map, the route symbols.
- **The subway stays the exception**: its bullet keeps the app's own palette and its own
  rounded-rectangle shape rather than the authority's, which the map redesign ledger ruled
  deliberately (R1). Nothing here touches it.

## The change

**1. `_parse_routes` reads both columns**, with the NJT parser's exact expression, so the
two feeds are now read the same way:

```python
"color": (row.get("route_color") or "").strip() or None,
"text_color": (row.get("route_text_color") or "").strip() or None,
```

**Verbatim means verbatim**: whitespace stripped, nothing else. No `"#"` added or removed,
no case folded, and neither field defaulted to a fallback colour. A parser that normalises
hides what the feed said, and two feeds normalising differently is how a colour stops being
comparable across systems.

**2. `build_railroad_route_shapes` carries them**, via `.get()` so a geometry-only build
(no routes table at all) yields `None` instead of raising, exactly as `name` already does.

**3. The endpoint serves them** per `(system, route)`, which is the key this endpoint exists
to preserve: LIRR and MNR both have a route `"1"`, and they are different lines with
different greens.

**4. `RailroadRoute` gains the two fields with `None` defaults**, so the wire shape is
additive.

### Additive is not cosmetic, and the defaults are not a licence to omit

`/api/railroad-routes` is **cacheable for an hour**, so a client holding yesterday's payload
must not start failing on a field the server has only just begun to send. Hence the
defaults.

The risk they introduce is the opposite one: with a default, a builder that silently stopped
emitting a colour would still validate. **`test_models`' field-set lock is what stands in the
way, and it is updated rather than relaxed.** It compares KEY SETS:

```python
assert set(entry) | {"system"} == set(RailroadRoute.model_fields)
```

so an entry missing `color` fails there even though `model_validate` would accept it. The
lock now also runs against the geometry-only build, so the keys must be present even when
all three values are null.

## Tests, hermetic

Nothing needs the network or a credential.

### The fixture is the real table

`backend/tests/fixtures/railroad_gtfs/` commits **each system's real `routes.txt`**: 13 LIRR
rows, 6 MNR rows, each system's real header (which differ, and that difference is part of
what the tests pin). Verified **cell by cell against the live archives**, with exactly one
difference.

**The one deliberate departure**: `route_color` is blanked on LIRR 11 (Belmont Park), where
the live feed carries `60269E`; its `route_text_color` is left as published. Two reasons,
and the second is the load-bearing one:

1. Neither live feed has a blank anywhere, so this is the only way to test the `None` branch.
2. It is **the one row where the two columns disagree**, which is what catches a parser that
   read `text_color` out of `route_color`'s column.

The directory's README records the probe, the three properties the tests read it for, and
that row, so nobody later "fixes" route 11 back and quietly removes the coverage.

**Tests read the real tables rather than synthetic rows**, because the claim under test is
about what the FEEDS publish, and a synthetic row only proves the parser agrees with
whatever the test author typed.

| Claim | Test |
| --- | --- |
| the LIRR feed's 13 routes parse, both colours verbatim, upper case, no `"#"` | `test_parse_routes_reads_both_colours_verbatim_from_the_lirr_feed` |
| a colour is not a route id: Ronkonkoma and Greenport share one purple | the same test |
| LIRR publishes no `route_short_name` COLUMN, so `short_name` is `None` for all 13 (the absent-column path, not a blank cell) | the same test |
| the MNR feed's 6 routes, and the New Haven family's one shared red | `test_parse_routes_reads_both_colours_verbatim_from_the_mnr_feed` |
| a blank column is `None`, and the two columns are independent | `test_parse_routes_blank_colour_is_none_and_the_two_columns_are_independent` |
| the parser does not normalise: `"#"` and lower case survive, whitespace does not | `test_parse_routes_carries_hex_verbatim_without_normalising` |
| a `routes.txt` with no colour columns at all still parses, both `None` | `test_parse_routes_absent_colour_columns_are_none` |
| the builder carries both colours **and the polylines are identical** with and without a routes table | `test_route_builder_carries_both_colours_and_leaves_polylines_alone` |
| a route with a colour and no name keeps the colour | `test_route_builder_colour_survives_a_route_with_no_name` |
| the endpoint serves both per `(system, route)`, and the colliding route `"1"` keeps two colours apart | `test_railroad_routes_endpoint_serves_both_colours_per_system_and_route` |
| the wire shape is additive: a pre-field payload validates, both read `None` | `test_models.py::test_railroad_route_colour_fields_are_additive` |
| the field-set lock, updated | `test_railroad_route_builder_output_covers_model` |

**The verbatim-against-normalising test is synthetic on purpose, and separate from the
fixture tests.** No live row carries a `"#"` or lower case, so the only way to pin that the
parser does not "helpfully" normalise is to hand it something a normaliser would change.
Putting that in the fixture would have made the fixture stop being the live table.

**Every existing railroad golden is unchanged.** No golden carries railroad route entries
(the `railroad_*_expected.json` files are realtime feeds), and the contract tier does not
assert this endpoint's shape, so nothing there needed touching. The four existing route
builder tests (branch/express, the New Haven four-branch case, reverse-direction collapse,
degenerate shapes) are untouched and still pass, which is the geometry half of "polylines
not touched".

## Mutations, each run in an isolated worktree and recorded

Each in a real `git worktree` detached at `5601299`, applied alone, the backend tier run
against it, with the main tree verified clean before and after. That commit holds this
branch's code exactly as reviewed; the only change after it was amending this document in,
which no test reads.

| # | Guard reverted | Result | Killed by |
| --- | --- | --- | --- |
| **M1** | the parser normalises: `.strip("#").lower()` on both colours | **killed**, 5 failures | `test_parse_routes_carries_hex_verbatim_without_normalising` (written for it), both feed tests, the blank-colour test, and the names test |
| **M2** | `text_color` read from `route_color`'s column | **killed**, 5 failures | `test_parse_routes_blank_colour_is_none_and_the_two_columns_are_independent` (the blanked row is what makes it fail), both feed tests |
| **M3** | the model's two `None` defaults removed | **killed**, **1** failure | `test_railroad_route_colour_fields_are_additive`, and nothing else |

**M3's signature is the point.** Exactly one test died, which is correct: the defaults' only
job is the additive wire shape, so a mutation removing them should kill exactly the test
that asserts it and no other. A second failure would have meant something else had come to
depend on them.

## One thing left out of scope, deliberately

`frontend/systems/railroad.js:32` comments that it "Matches the endpoint's `{system, route,
name, polylines}` shape". **That comment is stale as of this commit** and is exactly the kind
of sentence that outlives its premise. It is left alone because this branch is backend plus
the README note, and MR3 is the stage that owns that file and will read these fields. It is
a one-line correction whenever that stage starts.

## Gates

| Gate | |
| --- | --- |
| `pytest` (backend) | **1738** passed |
| `ruff check` | clean |
| `ruff format --check` | clean, 79 files |
| `mypy` | clean, 30 source files |
| contract-tier lint and format | clean |
| contract API tier | **38** passed |
| `run_all.sh` | **15 of 15**, the number `main` is at, same rows, no new red |

No em-dashes on added lines.

---
🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_017oZP4Mtd6BLoSvAeVaPrJz
