// A2: what the markers on the map are called, and whether the name keeps up.
//
// THE LABEL MUST TRACK THE DATA. A name written once at creation and never refreshed
// is the failure mode this file exists to catch: every system reuses its markers
// across polls (keyed diffs on trip_id, bus id, boat id, PATH's synthetic train id),
// so a marker created at 8:00 is the same DOM element at 9:00 with entirely different
// facts behind it. A stale name is worse than no name, because it is confidently
// wrong to the one rider who cannot see that it is wrong.
//
// Same hermetic harness as smoke.spec.js: mock.js intercepts every /api/* request, so
// nothing leaves the machine.

const { test, expect } = require("@playwright/test");
const { installMocks, json } = require("./mock");
const fx = require("./fixtures/api");
const { expectState } = require("./state");

// The poll cadence from map.js. Advancing the frozen clock by more than this drives
// exactly one refresh of every source.
const POLL_MS = 15_000;

async function open(page, ctx) {
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  // The STATE these specs need, not a marker count that any fast feed can satisfy;
  // see the witness's own comment in state.js for what the count cost.
  await expectState(page, "every vehicle system loaded", "opening the map");
  return ctx;
}

// The live label as the accessibility tree sees it, plus the value the factory
// remembers, so a failure distinguishes "never refreshed" from "refreshed but not
// written to the element".
//
// The record is looked up by (system, id) rather than by evaluating a source string:
// the page serves the production CSP, which has no 'unsafe-eval', so an eval-based
// probe fails inside the page for reasons that have nothing to do with the label.
function labelOf(page, system, id) {
  return page.evaluate(([which, key]) => {
    const registry = {
      subway: trains, bus: buses, ferry: ferryBoatRecords, path: pathTrainRecords,
      njt: njtTrainRecords,
    }[which];
    const marker = registry && registry.get(key) ? registry.get(key).marker : null;
    const el = marker && marker.getElement ? marker.getElement() : null;
    return {
      aria: el ? el.getAttribute("aria-label") : null,
      role: el ? el.getAttribute("role") : null,
      tabindex: el ? el.getAttribute("tabindex") : null,
      remembered: marker ? marker._a11yName : null,
    };
  }, [system, id]);
}

test("A2a. a subway trip whose route changes under the same id is relabeled", async ({ page }) => {
  // THE NAMED REQUIREMENT. trip_id is the diff key, so a mid-trip route relabel (the
  // MTA does this) reuses the very same marker element while changing the two things
  // the name is made of. The icon re-skin is already gated on this exact condition;
  // the label must not be gated more narrowly than the data it describes.
  const ctx = await installMocks(page);
  let polls = 0;
  ctx.overrides.subways = (route, fixtures) => {
    polls++;
    const body = fixtures.subways();
    if (polls > 1) {
      body.data[0].route_id = "2";
      body.data[0].stop_name = "Wall St";
      body.data[0].direction = "Southbound";
    }
    return json(route, body);
  };
  await open(page, ctx);

  const first = await labelOf(page, "subway", "sub-1");
  expect(first.aria).toBe("1 train, next stop Times Sq-42 St, Northbound");
  // The whole tab-order policy, asserted on a real marker rather than in the abstract.
  expect(first.role).toBe("img");
  expect(first.tabindex).toBeNull();

  await page.clock.runFor(POLL_MS + 1000);
  await expect.poll(async () => (await labelOf(page, "subway", "sub-1")).aria).toBe(
    "2 train, next stop Wall St, Southbound",
  );
  // Same element, not a new one: this is a relabel, not a lucky recreation. If the
  // marker had been destroyed and rebuilt the test would pass for the wrong reason.
  expect(await page.evaluate(() => trains.get("sub-1").marker.getElement().dataset.a2probe)).toBeUndefined();
});

test("A2b. a bus that turns, and a boat that docks, say so", async ({ page }) => {
  // Two systems whose rider-relevant field is NOT the route. A bus's bearing changes
  // without any route change, and buses.js deliberately mutates the svg in place
  // rather than re-iconing on a bearing-only change, so a label refresh tied to the
  // re-icon would never fire. A boat's status is the field a rider is listening for.
  const ctx = await installMocks(page);
  let busPolls = 0;
  let ferryPolls = 0;
  ctx.overrides.buses = (route, fixtures) => {
    busPolls++;
    const body = fixtures.buses();
    if (busPolls > 1) body.data[0].bearing = 270; // was 90: east becomes west
    return json(route, body);
  };
  ctx.overrides.ferry = (route, fixtures) => {
    ferryPolls++;
    const body = fixtures.ferry();
    if (ferryPolls > 1) body.boats[0].status = "STOPPED_AT";
    return json(route, body);
  };
  await open(page, ctx);

  expect((await labelOf(page, "bus", "MTA NYCT_101")).aria).toBe("M15 bus, heading east");
  const boatId = await page.evaluate(() => [...ferryBoatRecords.keys()][0]);
  expect((await labelOf(page, "ferry", boatId)).aria).toContain("under way");

  await page.clock.runFor(POLL_MS + 1000);
  await expect.poll(async () => (await labelOf(page, "bus", "MTA NYCT_101")).aria).toBe(
    "M15 bus, heading west",
  );
  await expect
    .poll(async () => (await labelOf(page, "ferry", boatId)).aria)
    .toContain("at dock");
});

test("A2c. a PATH train gets its real route name once the route table loads late", async ({ page }) => {
  // THE TRAP THE INVENTORY FOUND. applyPath re-icons ONLY when route_id changes, and
  // the static route table loads asynchronously with retry backoff. A diamond created
  // before the table lands keeps the fallback colour forever, because route_id never
  // changes afterwards. A name gated the same way would be stranded on "PATH route
  // 862" long after the real name was available, so the label is recomputed every
  // poll instead. This spec fails if anyone "optimises" it back behind the gate.
  const ctx = await installMocks(page);
  let routeCalls = 0;
  ctx.overrides.pathRoutes = (route, fixtures) => {
    routeCalls++;
    // Fail the first attempt, so trains exist before any route name does. An override
    // must serve every call itself: mock.js hands the route to the override and does
    // not fall back to the fixture, so returning nothing hangs the request.
    if (routeCalls === 1) return json(route, { detail: "warming up" }, 503);
    return json(route, fixtures.pathRoutes());
  };
  await open(page, ctx);

  const id = await page.evaluate(() => [...pathTrainRecords.keys()][0]);
  await expect.poll(async () => (await labelOf(page, "path", id)).aria).toContain("PATH route");

  // The retry lands the table, and the next poll must pick the name up.
  await page.clock.runFor(POLL_MS * 3);
  await expect
    .poll(async () => (await labelOf(page, "path", id)).aria, { timeout: 15_000 })
    .toContain("Newark - World Trade Center");
});

test("A2d. toggling a layer off and on does not leave its markers anonymous", async ({ page }) => {
  // Leaflet DESTROYS the icon element when a layer leaves the map and builds a fresh
  // one when it returns, restoring tabindex and role from marker options but losing
  // every attribute we wrote. AirTrain is the sharp case: it is static, so there is no
  // poll to re-apply anything, and without the factory's `add` hook its stations would
  // be permanently nameless after one toggle.
  await installMocks(page);
  await open(page);

  const namedAirtrain = () =>
    page.evaluate(() =>
      [...document.querySelectorAll(".airtrain-marker")].map((el) => el.getAttribute("aria-label")),
    );
  const before = await namedAirtrain();
  expect(before.length).toBeGreaterThan(0);
  expect(before.every((name) => name && name.includes("AirTrain JFK station"))).toBe(true);

  await page.locator("#toggle-airtrain").uncheck();
  await page.locator("#toggle-airtrain").check();
  await expect.poll(async () => (await namedAirtrain()).length).toBe(before.length);
  const after = await namedAirtrain();
  expect(after, "a re-shown layer keeps its names").toEqual(before);
  // And the tab-order policy survives the rebuild too, since Leaflet restores those
  // from marker options rather than from the element.
  expect(
    await page.evaluate(() =>
      [...document.querySelectorAll(".airtrain-marker")].every((el) => el.getAttribute("tabindex") === null),
    ),
  ).toBe(true);
});

test("A2e. no marker is reachable by Tab, and the map container still is", async ({ page }) => {
  // THE TAB-WALK. Before A2 this page put 14 nameless role=button markers ahead of
  // every real control; at production scale that is several hundred. The policy is
  // that markers are not the keyboard path and the station panel is.
  await installMocks(page);
  await open(page);

  const walk = await page.evaluate(() => {
    const selector = 'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])';
    return [...document.querySelectorAll(selector)]
      .filter((el) => el.offsetParent !== null || el === document.activeElement || el.id === "map")
      .map((el) => ({
        tag: el.tagName,
        id: el.id || null,
        cls: typeof el.className === "string" ? el.className.split(" ")[0] : null,
      }));
  });
  expect(walk.some((el) => el.cls && el.cls.endsWith("-marker")), "no marker may be tabbable").toBe(false);
  expect(walk.some((el) => el.id === "map"), "the map container keeps its own focus").toBe(true);

  // And the container's arrow-key panning, which is a real keyboard affordance for the
  // map itself, is untouched by the marker exclusion.
  await page.locator("#map").focus();
  const before = await page.evaluate(() => map.getCenter().lng);
  await page.keyboard.press("ArrowRight");
  // Leaflet pans with an animation, and the clock is PAUSED for determinism, so the
  // animation has to be driven forward explicitly. Without this the centre is
  // unchanged and the spec would report a missing affordance that is actually there.
  await page.clock.runFor(1000);
  await expect.poll(async () => page.evaluate(() => map.getCenter().lng)).toBeGreaterThan(before);
});

test("A2j. an NJT train's label picks up its route name late, and its delay every poll", async ({
  page,
}) => {
  // TWO GATES IN ONE SPEC, because njt.js has both of the traps the other systems
  // taught. The route table loads asynchronously with retry backoff (A2c's trap: a
  // name gated on route_id changing is stranded on the fallback forever), and the
  // DELAY moves poll to poll while nothing else about the train does (A2b's trap: a
  // label refreshed only on a re-icon never hears about it).
  const ctx = await installMocks(page);
  let routeCalls = 0;
  ctx.overrides.njtRoutes = (route, fixtures) => {
    routeCalls++;
    // An override must serve every call itself: mock.js hands it the route and does
    // not fall back to the fixture, so returning nothing hangs the request.
    if (routeCalls === 1) return json(route, { detail: "warming up" }, 503);
    return json(route, fixtures.njtRoutes());
  };
  // THE DELAY MOVES ON A POLL OF ITS OWN, after the route table and the colour have
  // both settled. The first draft flipped it on the SAME poll the table landed,
  // which is also the poll that rebuilds the icon, so a setMarkerName gated on the
  // re-skin still picked it up and the second half of this spec measured nothing: a
  // review round moved setMarkerName inside the re-skin block and A2f stayed green.
  let polls = 0;
  ctx.overrides.njt = (route, fixtures) => {
    polls++;
    const body = fixtures.njt();
    if (polls > 4) body.trains[0].delay = -180; // 4 min late becomes 3 min early
    return json(route, body);
  };
  await open(page, ctx);

  // Before the table lands: the route id, never a blank.
  await expect.poll(async () => (await labelOf(page, "njt", "NJ_3800")).aria).toContain(
    "NJ Transit route 9",
  );
  const early = await labelOf(page, "njt", "NJ_3800");
  expect(early.aria).toContain("4 min late");
  // The whole tab-order policy, asserted on a real NJT marker rather than in the
  // abstract: markers carry a name and a role and are NOT a tab stop.
  expect(early.role).toBe("img");
  expect(early.tabindex).toBeNull();

  // The retry lands the table: the name must follow, even though route_id never
  // changed, which is the gate A2c's trap is about.
  await page.clock.runFor(POLL_MS * 2);
  await expect
    .poll(async () => (await labelOf(page, "njt", "NJ_3800")).aria, { timeout: 15_000 })
    .toContain("Northeast Corridor");
  expect((await labelOf(page, "njt", "NJ_3800")).aria).toContain("4 min late");

  // NOW the delay flips, on a poll where nothing else about the train moves and the
  // icon is not rebuilt. The direction must not be rounded away into "late": NJ
  // Transit does publish early trains.
  await page.clock.runFor(POLL_MS * 3);
  await expect
    .poll(async () => (await labelOf(page, "njt", "NJ_3800")).aria, { timeout: 15_000 })
    .toContain("3 min early");
  const later = (await labelOf(page, "njt", "NJ_3800")).aria;
  expect(later).not.toContain("late");
  // Every NJT train is a schedule estimate and the name says so, on every poll.
  expect(later).toContain("scheduled position, no GPS");

  // AND THE MARKER RE-SKINS, not just the label. njt.js gates its re-icon on the
  // RESOLVED COLOUR rather than on route_id, because route_id never changes after a
  // late route table lands and a colour-blind gate would leave every train that
  // existed before the table drawn in the neutral fallback for the rest of the
  // session. Read off the rendered svg, which is the only place the rider sees it.
  const strokeOf = (id) =>
    page.evaluate(
      (key) => njtTrainRecords.get(key).marker.getElement().querySelector("rect").getAttribute("stroke"),
      id,
    );
  expect(await strokeOf("NJ_3800")).toBe("#DD3439");

  // AMENDMENT A on a live marker: route 17 never reaches the route table at all, so
  // its label is the route id rather than a hole where the name would be, and its
  // marker keeps the neutral fallback rather than losing its stroke.
  const added = (await labelOf(page, "njt", "njt:9001")).aria;
  expect(added).toContain("NJ Transit route 17");
  expect(added).not.toContain("undefined");
  expect(await strokeOf("njt:9001")).toBe("#4a4e69");
});
