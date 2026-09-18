# Audit records: repair the three harnesses' stubs, and gate them in CI

One commit. **Tests and CI configuration only, no application code**, which is checked
rather than claimed: `git diff origin/main..HEAD --name-only` matches nothing under
`frontend/` or `backend/`. **This deploys nothing.** Nothing here touches `njt_auth.py` or
any credentialed path, so **no NJ Transit mint was spent**.

## The finding

`docs/reviews/audit-2026-09-05/run_all.sh` was **at 12 of 15 on `main`**. F11, F12 and F14
had been red **through a whole stage with nothing failing, because nothing ran them**. The
suite was run by hand, and the hands that ran it were the ones told to.

That is the real defect here. The three red rows are a symptom, and the reason this branch
adds a CI job as well as repairing them: a verification record nobody runs is not a record,
it is a document that used to be true.

**F11 is the record for the station alerts join**, which is one of the three consumers the
`claude/subway-static-station-routes` branch existed to protect. The gap was directly over
something two stages had just worked on.

## What broke them

All three load the real production frontend into a Node `vm` against their own hand-written
Leaflet and DOM stubs. That is what makes them worth having: they measure the shipped files,
not a copy. It is also what makes them brittle, because the frontend is free to reach for
any browser or Leaflet surface it likes, and MR1 and MR2 reached for five new ones.

| What the app now calls | What the stubs did |
| --- | --- |
| `L.control.zoom` | MR1 repositions Leaflet's own zoom control at module scope. `L.control` was undefined, so **`systems/shared.js` threw at load in all three** and every claim below it became unreachable. This is the `Cannot read properties of undefined (reading 'zoom')` in the previous branch's PR body. |
| `button.querySelector(".feed-count").textContent = ...` | MR1's feed strip. **All three stubs answered `null` for every selector**, so the write was a null dereference. |
| `bindTooltip` on a `circleMarker` | MR2 binds a permanent tooltip to every subway station, so `loadStations` threw at the first one and registered **none** of them. |
| `style.setProperty` | MR1's chrome writes custom properties on elements. F12's `style` was a bare `{}`. |
| `dataset` | MR1's feed strip writes a dot's state through it. F12 had no `dataset`. |

**F11's `bindTooltip` gap was masking two unrelated F14 claim failures.** With zero stations
registered, F14's "picking a station pans the map" and "the panel search matches" both failed
for a reason that had nothing to do with either claim. A harness that dies early does not
report one problem; it reports a fog.

### A stub that answers `null` cannot tell "no match" from "I do not implement this"

Only the first of those is an answer. So F11's and F12's matchers now **throw on a selector
form they do not implement, naming it**, and the message says why:

```
audit DOM stub: unimplemented selector ".feed-dot[data-state]". It supports #id,
.class, .class.class and a bare tag. Teach it the new form rather than letting it
answer null, which is how this record went red once already.
```

The implemented forms are exactly the ones the loaded files use, **measured rather than
guessed**: `#id`, `.class`, `.class.class` and a bare tag. The next selector shape the
frontend adopts now fails at the stub, by name, instead of arriving as a null dereference
several frames away.

**F14 is deliberately different.** That stub synthesises elements on demand for everything
else, so its `querySelector` synthesises too, caching per selector so two calls return the
same node. Making it throw would have meant rewriting the harness's whole philosophy in a
commit about repairing it.

### F12's Leaflet recorders nest now

The old `leafletStub` returned a bare recorder function for `L.anything`, so `L.polyline(...)`
worked and `L.control.zoom(...)` did not. Each recorder is now itself a `Proxy` whose `get`
returns a deeper recorder, so **any depth answers without the stub enumerating Leaflet's
surface**, and the method name it records is the full dotted path. A top-level call still
records its own bare name, so **arm 4's `c.method === "polyline"` count is unchanged**, which
is the property that had to survive.

## Two of F14's numbers were stale, and are corrected rather than relaxed

Neither is a claim of the record. Both are premises the record measures on its way to its
four claims, and both were true when written.

**1. Three one-second tickers with a station open, not two.** MR1 added the header's blinking
clock. The two countdown repainters this record measured are still there and still exactly
two. Before changing the number I checked the thing that would make it a bug rather than a
new feature: **one registration site** (`frontend/systems/shared.js:306`) and **one vm load
pass**, so this is the app having a third ticker, not a duplicated interval.

**2. The custom-Leaflet-control guard fired on `L.control.zoom`.** That is MR1 repositioning
Leaflet's own zoom control, not a new control, and the record's own prose already accounts
for that control existing and for it zooming rather than panning. The guard is **narrowed,
not removed**:

```python
zoom_reposition = [hit for hit in custom_controls if "L.control.zoom(" in hit]
other_controls = [hit for hit in custom_controls if "L.control.zoom(" not in hit]
check(not other_controls, "the app now adds a custom Leaflet control that is not the "
      f"repositioned zoom, which could be a pan control: {other_controls}", "b")
```

A genuine pan control still fails it, and the failure now names what it found.

## The dispositions are unchanged

Which is the whole point: a repaired harness that reports a different verdict has not been
repaired, it has been rewritten.

| Record | Disposition after |
| --- | --- |
| F11 | `FIXED (claude/f11-panel-alerts)`, all **19** checks passing |
| F12 | `FIXED (claude/release1-small-fixes)`, all four arms, word for word |
| F14 | `VERIFIED  All four documented limitations still hold`, all four claims reproduced |

## And it is a CI job now

A sixth job, `audit-records`, both runtimes, dev dependencies only:

```yaml
audit-records:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v7
    - uses: actions/setup-python@v7
      with:
        python-version: "3.12"
    - uses: actions/setup-node@v7
      with:
        node-version: "22"
    - run: pip install -r backend/requirements-dev.txt
    - run: bash docs/reviews/audit-2026-09-05/run_all.sh
      env:
        PY: python
```

It runs the script and nothing else, because **`run_all.sh` already exits non-zero on any red
row**. That premise is the job, so it was confirmed both ways in a throwaway worktree rather
than assumed: **exit 0 on a green tree, exit 1 with a single row sabotaged**. A job whose
failure mode has never been observed is a job that reports green.

Nothing in the suite needs the network, a credential or a live upstream, and **the NJT scripts
refuse to run if a real credential is present**, so the job cannot spend a mint.

**This PR is the job's own first test, and the job failed it.** That is recorded here rather
than quietly amended away, because it is the same lesson as the finding.

`run_all.sh` guarded its interpreter with `[ ! -x "$PY" ]`, which is a filesystem test. The job
sets `PY: python` and lets the runner's `PATH` resolve it, exactly as the script's own `NODE`
default is a bare `node`; `-x "python"` instead asks whether `./python` exists in the repository
root, so the guard rejected a perfectly good interpreter and the job exited 2 before running a
single record. **My both-ways check had used an absolute `PY`, so it never exercised the form CI
uses.** A premise verified in one shape is not verified.

The guard is `command -v` now, which answers for both forms: it resolves a bare name on `PATH`,
and for a value containing a slash it still requires the file be executable. Verified in four
shapes, not one:

| `PY` | Result |
| --- | --- |
| `python`, the CI form, which was exit 2 | **15 of 15, exit 0** |
| an absolute path that does not exist | exit 2, guard fires |
| a file that exists and is not executable | exit 2, guard fires |
| `python` with a single row sabotaged | **exit 1**, the job's whole premise, now confirmed in the CI form |

The guard change is the only thing in this branch that was not already verified before the first
push, and it is one line plus the comment saying why.

## The ledger's standing rules

`docs/reviews/map-redesign-rounds.md` gains a numbered **"The standing rules every stage is
held to"** section, above the five-stages table. These rules existed; they were scattered
across three stages' findings, **and a rule a future stage has to go looking for is a rule it
will miss**. That is not a hypothetical, it is this branch's finding.

1. The pins land first, as their own commit, and they say what the stage may NOT change.
2. The named suites that stay green at every stage: the C6-series dimming specs, the F01 and
   F03 e2e specs, and the axe specs at desktop, 375 and 320.
3. **`run_all.sh` is a gate of every stage, at fifteen of fifteen**, with this branch's history
   written in as the evidence for why, and the CI job named.
4. Every review and probe workflow runs in its own worktree, and before any commit the tree is
   confirmed to be what the gates ran on. MR2 round 2's incident is the evidence.
5. **A fix is finished when reverting it fails something**, not when it works. MR2 round 3 had
   three fixes survive their own mutation on the first run, each already verified by hand.

MR2's row in the table moves from `in review` to `merged`.

## Gates

| Gate | |
| --- | --- |
| `run_all.sh` | **15 of 15**, up from 12 |
| `pytest` (backend) | **1729** passed |
| `ruff check` | clean |
| `ruff format --check` | clean, 79 files |
| `mypy` | clean, 30 source files |
| node unit | **312** passed |
| hermetic e2e | **270** passed |
| contract-tier lint and format | clean |
| contract API tier | **38** passed |

No em-dashes on added lines.

---
🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_017oZP4Mtd6BLoSvAeVaPrJz
