# How the MR4 pairs were taken

`capture.spec.js` is the harness, temporary in the same sense MR3's and MR2's were: it lives
here rather than in `tests/e2e/` so it is not part of the suite, and it is kept because it is
what makes the pairs reproducible rather than a set of pictures.

It boots the stock fixture world at the frozen clock, waits for **every** family to load (two
buses, two PATH trains, three ferry boats, two railroad trains, four NJ Transit trains,
fourteen stations and the AirTrain guideways, asserted rather than slept on, because a
half-built map photographs as a design decision), sets the theme, presses a view preset and
captures.

**Six frames per tree**: the City preset at 1280 and at 375, and the Region preset at 1280
so AirTrain is in frame at all, each in both themes.

**The theme is set through the control a rider now has.** In the AFTER tree `#theme-toggle` is
visible and the harness clicks it; in the BEFORE tree it is still `hidden` by ruling R2, so the
harness falls back to `applyTheme("dark")`. Both end at the same attribute, and this is the one
difference between the two runs that is deliberate: the before tree has no control to press,
which is the thing this stage changed.

**The clock is left RUNNING and given 1.5s to settle**, which is the one thing that is not
obvious and which MR2 paid for: a canvas renderer repaints on `requestAnimationFrame` and a
paused clock never fires one, so MR2's round 3 focus screenshot showed an undimmed map while
every layer's opacity had already changed (its H13). The frozen START time is what keeps the
clock in the header and every age in a popup constant between the two runs.

## Taking them again

    # after, from the branch. The config's testDir is tests/e2e, and this spec requires
    # ./mock and ./fixtures/api relative to itself, so it is COPIED IN and removed again
    # rather than run from here: run from this directory it reports "No tests found" and
    # silently changes nothing, which is the trap MR3's round 4 corrected in its own notes.
    cp docs/reviews/map-redesign/mr4/capture.spec.js tests/e2e/mr4capture.spec.js
    npx playwright test --config tests/e2e/playwright.config.js tests/e2e/mr4capture.spec.js
    rm tests/e2e/mr4capture.spec.js

    # before, from a worktree at the commit the branch forked from
    git worktree add --detach /tmp/mr4before db73f05
    ln -s "$PWD/node_modules" /tmp/mr4before/node_modules
    cp docs/reviews/map-redesign/mr4/capture.spec.js /tmp/mr4before/tests/e2e/
    cd /tmp/mr4before && MR4_TAG=before MR4_OUT="$OLDPWD/docs/reviews/map-redesign/mr4" \
      npx playwright test --config tests/e2e/playwright.config.js tests/e2e/capture.spec.js

The two runs differ only by the tree they ran in, which is what makes each pair a measurement
of this branch rather than of two afternoons.
