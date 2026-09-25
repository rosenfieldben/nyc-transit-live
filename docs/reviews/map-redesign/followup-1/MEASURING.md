# How the follow-up 1 pair was taken

`capture.spec.js` is the harness and `decode_capture.py` feeds it. Both are temporary in the sense
MR2 to MR4's harnesses were: they live here rather than in `tests/e2e/` so they are not part of the
suite, and they are kept because they make the pictures reproducible.

**The buses are real, and there are 2136 of them.** The finding is about hundreds of arrows at the
Rail preset, and the fixture world's two buses would photograph as nothing on either side of the
rule. So the harness serves the committed OneBusAway capture
(`backend/tests/fixtures/bus_vehicle_positions.pb`, the poll section 6.0's probe recorded), decoded
by the backend's own `fetch_vehicle_positions` into exactly the rows `/api/buses` serves. Every
other feed is the stock fixture world.

**Nothing in it touches a network or a credential.** The decoder's HTTP client is a stub returning
the committed bytes, the way `backend/tests/test_models.py` stubs it. The bus key is set to a fake,
and the two NJ Transit names to empty, before the backend is imported, so the project `.env`, which
holds real ones on the operator's machine, cannot supply them. No NJ Transit mint was spent.

**The capture's clocks are shifted, not rewritten.** Every timestamp moves by one constant so the
capture's poll lands five seconds before the suite's frozen clock, the fixtures' own convention.
Each bus's age relative to that poll is exactly what it was, so a bus that was old in the capture
is drawn old here.

## What each frame holds

Written by the harness beside each frame (`<tag>-<preset>.json`). "Drawn" is Playwright's own
visibility over every bus marker in the document, on screen or off it.

| Frame | Buses served | Bus markers drawn |
| --- | --- | --- |
| `before-rail.png` | 2136 | 2136 |
| `after-rail.png` | 2136 | 0 |
| `before-city.png` | 2136 | 2136 |
| `after-city.png` | 2136 | 2136 |

The Rail pair is the before and after the finding is about. The City pair should be the same
picture twice, because City is where the rule draws every bus, and taking it shows that rather than
saying it. The feed strip reads **Buses 2,136** in all four frames.

Before is `d49e9a7`, the commit this branch forks from (the pins commit after it changes no
frontend file). After is this branch. Both at 1280 by 720, light theme, the clock running from the
frozen start, 1.5 seconds to settle after the preset lands, for MR4's reason: a canvas repaints on
animation frames and a paused clock runs none.

## Taking them again

    # the rows, decoded once, into a scratch file
    backend/.venv/bin/python docs/reviews/map-redesign/followup-1/decode_capture.py /tmp/f1-buses.json

    # after, from the branch. The spec requires ./mock and ./fixtures/api relative to itself, so it
    # is COPIED INTO tests/e2e and removed again: run from here it reports "No tests found".
    cp docs/reviews/map-redesign/followup-1/capture.spec.js tests/e2e/f1capture.spec.js
    CI=1 F1_TAG=after F1_OUT="$PWD/docs/reviews/map-redesign/followup-1" F1_BUSES=/tmp/f1-buses.json \
      npx playwright test --config tests/e2e/playwright.config.js tests/e2e/f1capture.spec.js
    rm tests/e2e/f1capture.spec.js

    # before, from a worktree at the base, writing into this directory
    git worktree add --detach /tmp/f1before d49e9a7
    ln -s "$PWD/node_modules" /tmp/f1before/node_modules
    cp docs/reviews/map-redesign/followup-1/capture.spec.js /tmp/f1before/tests/e2e/f1capture.spec.js
    (cd /tmp/f1before && CI=1 F1_TAG=before F1_OUT="$OLDPWD/docs/reviews/map-redesign/followup-1" \
      F1_BUSES=/tmp/f1-buses.json npx playwright test --config tests/e2e/playwright.config.js \
      tests/e2e/f1capture.spec.js)
    git worktree remove --force /tmp/f1before

## The mutation table

`mutations.sh <sha>` runs every row in a worktree detached at that sha, through `mutate.sh`. M0 is a
control that must survive, and every other row must die. `mutate.sh` says what it changed from
MR5's runner and why: the port is freed with `lsof`, the node tier gets a real-path `TMPDIR` on
macOS, and a gate that failed only on the suite's clock race is run again rather than counted as a
kill.
