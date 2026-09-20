# MR5: how the frames in this directory were taken

`capture.spec.js` is the harness, temporary in the same sense MR2's, MR3's and MR4's were: it lives
here rather than in `tests/e2e/` so it is not part of the suite, and it is kept because it is what
makes a pair reproducible rather than a pair of pictures.

**This stage's subject is the popup, so the frames are popups.** MR4's six frames are already the
map, and a full-page frame photographs a popup at about a fifth of its height. What MR5 changes is
what a rider reads after they click, so each frame is clipped to `.leaflet-popup`: the wrapper, its
tip and its ink edge at 1:1. Fourteen surfaces, both themes, plus two full frames at 375 where the
popup has to clear the chrome, which is the one thing a clip cannot show.

## The after frames (this branch)

    cp docs/reviews/map-redesign/mr5/capture.spec.js tests/e2e/mr5capture.spec.js
    npx playwright test --config tests/e2e/playwright.config.js tests/e2e/mr5capture.spec.js
    rm tests/e2e/mr5capture.spec.js

## The before frames (the tree this stage started from)

    git worktree add --detach /tmp/mr5before edd6950
    cp docs/reviews/map-redesign/mr5/capture.spec.js /tmp/mr5before/tests/e2e/
    ln -s "$PWD/node_modules" /tmp/mr5before/node_modules
    cd /tmp/mr5before && MR5_TAG=before \
      MR5_OUT="$OLDPWD/docs/reviews/map-redesign/mr5" \
      npx playwright test --config tests/e2e/playwright.config.js tests/e2e/capture.spec.js

**The marker table is copied into the harness rather than imported**, and that is what makes the
second form work: `tests/e2e/popup.js` did not carry that table before this phase, so a harness that
imported it could not run in the before tree at all. A table copied with its reason is cheaper than
a pair that cannot be regenerated.

**The clock is frozen at the fixtures' instant and the wait is real.** Every age in a popup and the
header's clock are constant between two runs because `page.clock.install` pins the start, and the
settle loop advances the paused clock so a station's arrivals fetch can resolve (a frame taken
before it does is a picture of "Loading arrivals"). The 500 ms before each shot is real time and it
is not decoration: Leaflet's fade and ruling S3's clamped pan are CSS transitions on the
compositor's clock, and Playwright refused the first run of this harness with "element is not
stable".
