// A2: reduced motion.
//
// THE PRINCIPLE THESE SPECS DEFEND: reduced motion changes HOW a position updates,
// never WHAT is shown. A gliding train and a stepping train are at the same place at
// the same time. So every spec here pairs "it stopped moving between polls" with "it
// still arrived at the right place when the poll landed", because a gate that quietly
// froze the data would satisfy the first half and betray the rider entirely.
//
// The preference is emulated with page.emulateMedia rather than the reducedMotion
// context option, for two reasons. It must be set BEFORE goto, because Leaflet reads
// its animation options once when the map is constructed and a preference applied
// afterwards would arrive too late for exactly the thing being tested. And the context
// option did not reach the page in this harness (matchMedia reported false inside a
// test that declared it), while emulateMedia demonstrably does; both were checked
// directly rather than assumed. Same media feature either way, so the page takes the
// real path a rider's system preference would put it on.

const { test, expect } = require("@playwright/test");
const { installMocks, json } = require("./mock");
const fx = require("./fixtures/api");

const POLL_MS = 15_000;

async function open(page, { reduce = false } = {}) {
  if (reduce) await page.emulateMedia({ reducedMotion: "reduce" });
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  await expect
    .poll(async () => page.evaluate(() => document.querySelectorAll(".leaflet-marker-icon").length), {
      timeout: 15_000,
    })
    .toBeGreaterThan(5);
}

// One subway train's screen position, which is what the glide moves between polls.
const trainAt = (page) =>
  page.evaluate(() => {
    const record = [...trains.values()][0];
    const ll = record.marker.getLatLng();
    return { lat: ll.lat, lng: ll.lng };
  });

test.describe("with reduced motion requested", () => {
  test("A5a. positions step to the truth per poll instead of tweening between them", async ({ page }) => {
    await installMocks(page);
    await open(page, { reduce: true });

    const start = await trainAt(page);
    // Advance well inside one poll interval. The glide would have moved the train
    // several times over by now: the animation tick runs every TRAIN_TICK_MS and
    // interpolates toward the next stop on every one of them.
    await page.clock.runFor(POLL_MS - 3000);
    const midInterval = await trainAt(page);
    expect(midInterval, "no interpolation between polls under reduced motion").toEqual(start);

    // AND THE DATA STILL ARRIVES. This is the half that matters: the gate must not have
    // frozen anything, so when the next poll lands the train is where the feed says.
    // Without this assertion a completely broken map would pass the spec above.
    await page.clock.runFor(POLL_MS + 2000);
    await expect
      .poll(async () => (await trainAt(page)).lat !== start.lat || (await trainAt(page)).lng !== start.lng)
      .toBe(true);
  });

  test("A5b. the marker transitions and Leaflet's own animations are off", async ({ page }) => {
    await installMocks(page);
    await open(page, { reduce: true });

    // The class the whole css gate hangs on.
    await expect(page.locator("html")).toHaveClass(/reduced-motion/);

    const transitions = await page.evaluate(() => {
      const out = {};
      for (const cls of ["bus-marker", "ferry-marker"]) {
        const el = document.querySelector(`.${cls} svg`);
        if (el) out[cls] = getComputedStyle(el).transitionDuration;
      }
      return out;
    });
    for (const [cls, duration] of Object.entries(transitions)) {
      expect(parseFloat(duration), `${cls} must not transition`).toBe(0);
    }

    // Leaflet's own zoom and pan animation, read from the map it was constructed with.
    const options = await page.evaluate(() => ({
      zoom: map.options.zoomAnimation,
      fade: map.options.fadeAnimation,
      markerZoom: map.options.markerZoomAnimation,
    }));
    expect(options).toEqual({ zoom: false, fade: false, markerZoom: false });
  });

  test("A5c. the station panel appears without animating, and says the same things", async ({ page }) => {
    await installMocks(page);
    await open(page, { reduce: true });

    // The panel is docked open at this width. Close and reopen it, which is the
    // transition a rider would see.
    await page.locator("#stations-toggle").click();
    await expect(page.locator("#stations-panel")).toBeHidden();
    await page.locator("#stations-toggle").click();
    await expect(page.locator("#stations-panel")).toBeVisible();

    const style = await page.evaluate(() => {
      const el = document.getElementById("stations-panel");
      const cs = getComputedStyle(el);
      return { transition: cs.transitionDuration, animation: cs.animationDuration };
    });
    expect(parseFloat(style.transition), "the panel must not transition").toBe(0);
    expect(parseFloat(style.animation), "the panel must not animate").toBe(0);

    // AND IT STILL WORKS. Reduced motion changes how things move, not what they say:
    // the search, the results and the arrivals are all exactly as they were.
    await page.locator("#stations-search").fill("times");
    await page.locator("#stations-results button.station-row").first().click();
    await expect(page.locator("#stations-detail")).toContainText("Northbound");
    await expect(page.locator("#stations-detail")).toContainText("in 2 minutes");
  });
});

test("A5d. without the preference, the map still glides", async ({ page }) => {
  // The control. Without this, every assertion above would also pass on a map whose
  // animation had simply been deleted, and the suite would be pinning a regression
  // rather than a preference.
  await installMocks(page);
  await open(page);

  await expect(page.locator("html")).not.toHaveClass(/reduced-motion/);
  expect(await page.evaluate(() => map.options.zoomAnimation)).toBe(true);

  const start = await trainAt(page);
  await page.clock.runFor(3000); // well inside one poll interval
  const moved = await trainAt(page);
  expect(
    moved.lat !== start.lat || moved.lng !== start.lng,
    "between polls the train should be interpolating toward its next stop",
  ).toBe(true);
});

test.describe("with reduced motion requested, the map itself", () => {
  test("A5e. opening a popup near the edge does not slide the whole map", async ({ page }) => {
    // FOUND BY REVIEW, MEASURED NOT REASONED. Leaflet auto-pans when an opening popup
    // would overflow the viewport, and it does so through panBy with no options, which
    // animates. There is no constructor switch for it, so the three options the gate
    // sets do not touch it and the .reduced-motion class cannot reach it either: the
    // pan is animated in JS, not by a css transition. Before the fix this was measured
    // as identical with the preference on and off, ~280ms of the entire viewport
    // sliding, which is the largest motion source in the app.
    await installMocks(page);
    await open(page, { reduce: true });

    const result = await page.evaluate(() => {
      // A station dot close enough to the top edge that its popup must auto-pan.
      const entry = stationRegistry.find((row) => row.key.startsWith("subway|"));
      map.setView([entry.lat, entry.lon], map.getZoom(), { animate: false });
      const target = map.containerPointToLatLng([map.getSize().x / 2, 60]);
      entry.marker.setLatLng(target);

      const centres = [];
      const onMove = () => centres.push(map.getCenter().lat + "," + map.getCenter().lng);
      map.on("move", onMove);
      entry.marker.openPopup();
      const panned = document.querySelector(".leaflet-pan-anim") !== null;
      map.off("move", onMove);
      return { moves: centres.length, distinct: new Set(centres).size, panAnimClass: panned };
    });

    // A pan may still HAPPEN (the popup must be brought into view, and suppressing that
    // would change what the rider can see, which this gate must never do). What must
    // not happen is a multi-frame slide: it arrives in one step.
    expect(result.distinct, "the map must not travel through intermediate positions").toBeLessThanOrEqual(1);
    expect(result.panAnimClass, "Leaflet's pan animation class must never appear").toBe(false);
  });

  test("A5f. that pan is now unanimated for EVERYONE, and this is the record of why", async ({ page }) => {
    // THIS SPEC HAS BEEN INVERTED, deliberately, and the old assertion is quoted here so the
    // change is legible rather than silent. It used to read "an ordinary rider still gets
    // the animated pan", and it was the control that made A5e pin a PREFERENCE rather than
    // a removed feature.
    //
    // A4's adversarial round removed the feature on purpose. Leaflet's _adjustPan is a
    // CORRECTION of where a popup landed, not a journey the rider asked for, and animating
    // it made the popup's resting position unknowable: the animation lands on a later frame
    // than popupopen, so the A4 code that moves a popup out from under the legend was
    // reading a position that was still moving. Measured at 1280 with the placed railroad
    // popup: real clock, the popup settles at x 1001..1276 and overlaps the legend at 1030;
    // fixed clock, PosAnimation drives itself off `+new Date()` and never completes at all,
    // leaving the popup at x 1288..1563, off the map's right edge, forever.
    //
    // That second measurement is why this could not stay a preference. The accessibility
    // gate needs a fixed clock for deterministic ages, and under a fixed clock an animated
    // pan has no landing position for any spec to assert. Instant is the only setting under
    // which the popup HAS a position.
    //
    // THE PREFERENCE PAIR IS NOT LOST, it moved: A5g and A5h still pin the app's own panTo
    // from the station panel as animated-unless-asked-otherwise, which is the pan a keyboard
    // rider triggers most often.
    await installMocks(page);
    await open(page);

    const result = await page.evaluate(() => {
      const entry = stationRegistry.find((row) => row.key.startsWith("subway|"));
      map.setView([entry.lat, entry.lon], map.getZoom(), { animate: false });
      entry.marker.setLatLng(map.containerPointToLatLng([map.getSize().x / 2, 60]));
      const centres = [];
      const onMove = () => centres.push(map.getCenter().lat + "," + map.getCenter().lng);
      map.on("move", onMove);
      entry.marker.openPopup();
      const panned = document.querySelector(".leaflet-pan-anim") !== null;
      map.off("move", onMove);
      return {
        started: map._panAnim ? !!map._panAnim._inProgress : false,
        distinct: new Set(centres).size,
        panAnimClass: panned,
      };
    });

    expect(result.started, "no pan animation is in flight for anyone").toBe(false);
    expect(result.panAnimClass, "and Leaflet's pan animation class never appears").toBe(false);
    expect(result.distinct, "the correction arrives in one step").toBeLessThanOrEqual(1);
  });

  test("A5g. selecting a station in the panel does not slide the map either", async ({ page }) => {
    // THE SECOND PAN, raised by the review alongside A5e's and dropped by my own review
    // script before it reached a skeptic. A5e covers Leaflet's own autopan when an
    // opening popup would overflow the viewport; this is the app's own pan,
    // syncMapToStation calling map.panTo with no options every time a rider picks a
    // station in the A1 panel. It is the pan a keyboard rider triggers most, because the
    // panel is the keyboard path to the whole map.
    //
    // MEASURED ON THE LAYER-OFF BRANCH, which is a real rider path (pick a station whose
    // system you have toggled off: the map pans and deliberately opens no popup) and the
    // only one that can be measured at all. Leaflet's Popup._adjustPan opens with
    // `this._map._panAnim && this._map._panAnim.stop()`, so in the popup branch the
    // pan animation is killed by the popup one statement later and there is nothing left
    // to observe. Verified in the vendored source rather than assumed. Same panTo, same
    // gate; this branch just leaves the evidence standing.
    await installMocks(page);
    await open(page, { reduce: true });

    const result = await page.evaluate(() => {
      const entry = stationRegistry.find((row) => row.key.startsWith("subway|"));
      if (entry.layer && map.hasLayer(entry.layer)) map.removeLayer(entry.layer);
      // Far enough to be a real pan, near enough to stay inside the viewport-sized
      // offset Leaflet is willing to animate at all.
      map.setView([entry.lat + 0.02, entry.lon + 0.02], map.getZoom(), { animate: false });
      syncMapToStation(entry);
      const c = map.getCenter();
      return {
        inProgress: map._panAnim ? !!map._panAnim._inProgress : false,
        panAnimClass: document.querySelector(".leaflet-pan-anim") !== null,
        arrived: Math.abs(c.lat - entry.lat) < 1e-4 && Math.abs(c.lng - entry.lon) < 1e-4,
      };
    });

    expect(result.inProgress, "no pan animation may be running").toBe(false);
    expect(result.panAnimClass, "Leaflet's pan animation class must never appear").toBe(false);
    // AND IT STILL GOT THERE. The rider asked to be taken to a station; the gate changes
    // HOW the map arrives, never WHETHER it does. A gate that simply suppressed the pan
    // would satisfy the two assertions above and strand the rider looking at the wrong
    // part of the city.
    expect(result.arrived, "the map must be at the station, in one step").toBe(true);
  });
});

test("A5h. without the preference, that same panel pan is animated", async ({ page }) => {
  // The control for A5g, so the pair pins a preference rather than a deleted feature.
  // Under the suite's frozen clock an animation in flight has made no progress yet, so
  // "still travelling" and "already there" are cleanly distinguishable: this run is mid
  // animation and has NOT arrived, A5g's has arrived and is not animating.
  await installMocks(page);
  await open(page);
  const result = await page.evaluate(() => {
    const entry = stationRegistry.find((row) => row.key.startsWith("subway|"));
    if (entry.layer && map.hasLayer(entry.layer)) map.removeLayer(entry.layer);
    map.setView([entry.lat + 0.02, entry.lon + 0.02], map.getZoom(), { animate: false });
    syncMapToStation(entry);
    const c = map.getCenter();
    return {
      inProgress: map._panAnim ? !!map._panAnim._inProgress : false,
      panAnimClass: document.querySelector(".leaflet-pan-anim") !== null,
      arrived: Math.abs(c.lat - entry.lat) < 1e-4 && Math.abs(c.lng - entry.lon) < 1e-4,
    };
  });
  expect(result.inProgress, "an ordinary rider still gets the animated pan from the panel").toBe(true);
  expect(result.panAnimClass, "and Leaflet's pan animation class with it").toBe(true);
  expect(result.arrived, "which is exactly why it has not arrived yet").toBe(false);
});
