# How the MR3 pair was taken

`capture.spec.js` is the harness, temporary in the same sense MR2's `measure.spec.js` was:
it lives here rather than in `tests/e2e/` so it is not part of the suite, and it is kept
because it is what makes the pair reproducible rather than a pair of pictures.

It boots the stock fixture world at the frozen clock, waits for every rail family to load
(two railroad trains, four NJ Transit trains, fourteen stations, asserted rather than slept
on, because a half-built map photographs as a design decision), presses the **Rail** preset
and captures at 1280 and at 375.

**The clock is left RUNNING and given 1.2s to settle**, which is the one thing that is not
obvious and which MR2 paid for: a canvas renderer repaints on `requestAnimationFrame` and a
paused clock never fires one, so MR2's round 3 focus screenshot showed an undimmed map while
every layer's opacity had already changed (its H13). The frozen START time is what keeps the
clock in the header and every age in a popup constant between the two runs.

## Taking it again

    # after, from the branch. The config's testDir is tests/e2e, and this spec requires
    # ./mock and ./fixtures/api relative to itself, so it is COPIED IN and removed again
    # rather than run from here. Round 4 corrected this line: run as written above it
    # reported "No tests found" and silently changed nothing, which on a stage whose
    # drawing had changed would have shipped the previous run's pair as the new one.
    cp docs/reviews/map-redesign/mr3/capture.spec.js tests/e2e/mr3capture.spec.js
    npx playwright test --config tests/e2e/playwright.config.js tests/e2e/mr3capture.spec.js
    rm tests/e2e/mr3capture.spec.js

    # before, from a worktree at the commit the branch forked from
    git worktree add --detach /tmp/before 49c5956
    ln -s "$PWD/node_modules" /tmp/before/node_modules
    cp docs/reviews/map-redesign/mr3/capture.spec.js /tmp/before/tests/e2e/
    cd /tmp/before && MR3_TAG=before MR3_OUT="$OLDPWD/docs/reviews/map-redesign/mr3" \
      npx playwright test --config tests/e2e/playwright.config.js tests/e2e/mr3capture.spec.js

The two runs differ only by the tree they ran in, which is what makes the pair a measurement
of this branch rather than of two afternoons.
