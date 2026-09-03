// A4: VANISHING FOCUS. What happens to a rider who is holding something the data takes
// away.
//
// THE CASE. Focus can never be inside a marker element (the factory builds every marker
// with keyboard:false, and A2 pinned that as the marker exclusion policy), so the thing a
// rider can actually be holding is a POPUP: Leaflet's own close button is always in one,
// and a placed railroad train's popup carries the cross-link button too. The alert
// banner's dismiss button is the same shape of problem on a different surface. All of
// them can be destroyed by data arriving rather than by anything the rider did.
//
// Measured before this phase, on the shipped tree: a vehicle ageing out of the feed with
// its popup open left document.activeElement === document.body, silently. So did the last
// alert clearing on a poll while the dismiss button was focused, and so did dismissing an
// alert by pressing Enter on that button.
//
// WHAT IS DELIBERATELY NOT ANNOUNCED. The predicate is about the RIDER, not the cause: it
// asks whether the subtree being destroyed contained document.activeElement. A layer
// toggle destroys every marker in its group, but the rider's focus is on the checkbox
// they just activated, so nothing is said. That is the same worthiness rule the live
// regions have followed since A1: speech is earned by a transition in the rider's own
// state, not by every event that happens to be true.

const { test, expect } = require("@playwright/test");
const { installMocks, json } = require("./mock");
const { expectPopupState } = require("./popup");
const fx = require("./fixtures/api");
const { expectState } = require("./state");

async function open(page) {
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  // The STATE these specs need, not a marker count. Four of them read
  // `[...railroads.keys()][0]` on the next line, and a count of five is satisfied by
  // whichever feeds land first: a full-suite run really did fail here with
  // `railroads.get(undefined)` at A8c. See the witness's comment in state.js.
  await expectState(page, "every vehicle system loaded", "opening the map");
}

// Where focus is and what the page said, asked together because the two halves of the
// contract are one event: the rescue is only correct if the rider both lands somewhere
// usable AND is told.
const state = (page) =>
  page.evaluate(() => ({
    active: document.activeElement.id || document.activeElement.tagName,
    announced: document.getElementById("page-announce").textContent,
  }));

// Focus something inside the open popup. The close button is chosen because Leaflet puts
// one in every popup, so this works for any system rather than only for the railroad
// popups that carry a cross-link.
async function focusInsidePopup(page) {
  await page.locator(".leaflet-popup-close-button").focus();
  expect(
    await page.evaluate(() => document.activeElement.classList.contains("leaflet-popup-close-button")),
    "the rider must actually be holding something inside the popup",
  ).toBe(true);
}

test("A8a. a followed vehicle ageing out of the feed lands focus on the map, and says so", async ({ page }) => {
  // THE C2 RETENTION PATH, reached the way production reaches it: the vehicle simply
  // stops appearing in the payload. The backend's retention cap (FEED_RETENTION_MAX_S)
  // drops a failed system's carried-forward vehicles from the envelope, and the client
  // never sees a "removal" event at all, only an absence, which the per-system departure
  // sweep turns into group.removeLayer(marker).
  const ctx = await installMocks(page);
  await open(page);

  const id = await page.evaluate(() => [...railroads.keys()][0]);
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), id);
  await expectPopupState(page, { registry: "railroads", key: id }, true);
  await focusInsidePopup(page);

  // The vehicle vanishes from the DATA, and it vanishes ALONE. A wholesale empty feed
  // would be the wrong instrument twice over: the client holds an empty run under a
  // transient-blip grace (map.js) so nothing would be removed at all for FEED_STALE_AFTER_S,
  // and a whole system disappearing is a different event from one vehicle ageing out. The
  // C2 retention cap drops carried-forward vehicles individually, so this drops one and
  // leaves its neighbour, which also proves the sweep removed the RIGHT marker.
  const survivors = await page.evaluate(() => railroads.size);
  // Read while the marker still exists, because the assertion below is about whether the
  // message carries THIS vehicle's name and afterwards there is nothing left to ask.
  const vanishedName = await page.evaluate((key) => railroads.get(key).marker._a11yName, id);
  expect(vanishedName, "the fixture's marker must carry an accessible name to be named by").toBeTruthy();
  // The payload key is `data` and the registry key is `system|trip_id` (railroadKey), so
  // the filter is written against the real shape rather than a guessed one; the assertion
  // below on the registry SIZE is what catches a filter that silently matched nothing.
  ctx.overrides.railroads = (route, fixtures, dropped = id) => {
    const body = fixtures.railroads();
    body.data = body.data.filter((t) => `${t.system}|${t.trip_id}` !== dropped);
    return json(route, body);
  };
  await page.evaluate(() => refreshAll());
  await expect
    .poll(async () => page.evaluate(() => railroads.size), { timeout: 5_000 })
    .toBe(survivors - 1);

  await expect.poll(async () => (await state(page)).active, { timeout: 5_000 }).toBe("map");
  const after = await state(page);
  expect(after.active, "focus lands on the map container, which cannot itself vanish").toBe("map");
  expect(after.announced, "and the rider is told once, politely").toMatch(
    /^The .+ you were following left the feed\. Focus moved to the map\.$/,
  );
  /* AND IT NAMES THE THING THAT VANISHED, which `.+` above does not require. Round 4:
     dropping the label from the rescue call, one deleted property, still produced "The
     vehicle you were following left the feed", still matched that pattern, and left the
     whole vanish and announce suites green. The message is composed from the marker's OWN
     accessible name on purpose, because this app carries buses, boats and PATH trains as
     well as trains and a fixed noun would be false for most of them (see helpers.js). A
     rescue that has forgotten which vehicle it was about is a rescue that stopped doing the
     thing the composition exists for. */
  // The leading clause of the marker's name IS the vehicle identity ("1 train", "M15 bus",
  // "Rockaway ferry"), because that is how buildMarkerName composes it and what
  // vanishingFocusMessage takes.
  const identity = vanishedName.split(",")[0].trim();
  expect(
    after.announced,
    `the message must name the vehicle, not a generic noun (marker name was ${JSON.stringify(vanishedName)})`,
  ).toBe(`The ${identity} you were following left the feed. Focus moved to the map.`);
});

test("A8b. hiding a layer under an open popup is silent, because the rider is on the checkbox", async ({ page }) => {
  // THE ELEMENT-DESTROY PATH, and the one the door must deliberately NOT speak on. A
  // layer toggle destroys every marker in the group, so the popup is torn down exactly as
  // it is on the retention path, but the rider is holding the checkbox they just
  // activated. Announcing here would be speech nobody earned, and moving focus would take
  // it away from the control they are still using.
  //
  // The two paths are asserted SEPARATELY on purpose: they travel different code (a
  // sweep's group.removeLayer versus map.removeLayer on the group) to the same rider
  // experience, and a door that covered only one would look complete from either half.
  await installMocks(page);
  await open(page);

  const id = await page.evaluate(() => [...railroads.keys()][0]);
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), id);
  await expectPopupState(page, { registry: "railroads", key: id }, true);

  // Uncheck through the real control, so focus goes where a rider's focus really goes.
  await page.locator("#toggle-railroads").focus();
  await page.locator("#toggle-railroads").uncheck();

  const after = await state(page);
  expect(after.active, "focus stays on the control the rider is operating").toBe("toggle-railroads");
  expect(after.announced, "and nothing is announced, because nothing the rider held vanished").toBe("");

  // The marker really was destroyed, so this is not passing because nothing happened.
  expect(
    await page.evaluate((key) => railroads.get(key).marker.getElement() === null, id),
    "the marker element must actually be gone, or this spec proves nothing",
  ).toBe(true);
});

test("A8c. a layer toggle DOES rescue when the rider is somehow inside the popup", async ({ page }) => {
  // The complement of A8b, and the reason the predicate is about the rider rather than
  // about the cause. Same destruction path, focus deliberately left inside the popup:
  // now the subtree being destroyed does contain document.activeElement, so the same door
  // fires. This is what makes the door's silence in A8b a DECISION rather than a gap.
  await installMocks(page);
  await open(page);

  const id = await page.evaluate(() => [...railroads.keys()][0]);
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), id);
  await expectPopupState(page, { registry: "railroads", key: id }, true);
  await focusInsidePopup(page);

  // Driven programmatically, because a real click on the checkbox is precisely what moves
  // focus off the popup; this is the shape a future non-click layer control would have.
  await page.evaluate(() => map.removeLayer(railroadLayer));

  await expect.poll(async () => (await state(page)).active, { timeout: 5_000 }).toBe("map");
  expect((await state(page)).announced, "the same door, the same wording").toMatch(
    /left the feed\. Focus moved to the map\.$/,
  );
});

test("A8d. the last alert clearing while the dismiss button is focused rescues the rider", async ({ page }) => {
  // THE BANNER UNMOUNT PATH. Measured on the shipped tree: activeElement went to BODY and
  // nothing was said, because the unmount branch returns before the focus-restore code
  // that the rebuild branch runs.
  const ctx = await installMocks(page);
  ctx.overrides.alerts = (route, fixtures) => {
    const body = fixtures.alerts();
    body.alerts = [
      {
        id: "vanish-1",
        system: "subway",
        header: "Reduced service systemwide while crews clear a disabled train",
        description: null,
        effect: "REDUCED_SERVICE",
        cause: "OTHER_CAUSE",
        routes: [],
        stops: [],
        starts_at: fx.FROZEN_S - 600,
        ends_at: null,
      },
    ];
    return json(route, body);
  };
  await open(page);
  await page.locator("#alert-banner-dismiss").focus();
  expect(await (await state(page)).active).toBe("alert-banner-dismiss");

  // The incident ends. No rider action at all: this is a poll landing.
  ctx.overrides.alerts = (route, fixtures) => json(route, { ...fixtures.alerts(), alerts: [] });
  await page.evaluate(() => loadAlerts());

  await expect.poll(async () => (await state(page)).active, { timeout: 5_000 }).toBe("map");
  expect((await state(page)).announced, "the alerts wording, not the vehicle wording").toBe(
    "Alerts cleared. Focus moved to the map.",
  );
  await expect(page.locator("#alert-banner-dismiss"), "and the button really is gone").toHaveCount(0);
});

test("A8e. dismissing the last alert rescues the rider from the button they pressed", async ({ page }) => {
  // The rider's own action, which the A2 FOLLOWUP named as the second half of the same
  // case: the dismiss button can never survive its own click, because dismissing empties
  // the shown set, so every dismissal lands on the unmount branch or on a rebuild with no
  // successor button.
  const ctx = await installMocks(page);
  ctx.overrides.alerts = (route, fixtures) => {
    const body = fixtures.alerts();
    body.alerts = [
      {
        id: "vanish-2",
        system: "subway",
        header: "Reduced service systemwide while crews clear a disabled train",
        description: null,
        effect: "REDUCED_SERVICE",
        cause: "OTHER_CAUSE",
        routes: [],
        stops: [],
        starts_at: fx.FROZEN_S - 600,
        ends_at: null,
      },
    ];
    return json(route, body);
  };
  await open(page);

  await page.locator("#alert-banner-dismiss").focus();
  await page.keyboard.press("Enter");

  await expect.poll(async () => (await state(page)).active, { timeout: 5_000 }).toBe("map");
  expect((await state(page)).announced).toBe("Alerts cleared. Focus moved to the map.");
});

test("A8f. a banner rebuild that still has a dismiss button keeps the A2 restore", async ({ page }) => {
  // THE CONTRACT THIS MUST NOT BREAK. A2 built the restore for the reword case: an ongoing
  // incident rewritten under the same id rebuilds the strip, and focus parked on the
  // dismiss must come back to the new button rather than being sent to the map. The
  // rescue is for the case with NO successor; where a successor exists it must not fire.
  const ctx = await installMocks(page);
  const alert = (header) => ({
    id: "vanish-3",
    system: "subway",
    header,
    description: null,
    effect: "REDUCED_SERVICE",
    cause: "OTHER_CAUSE",
    routes: [],
    stops: [],
    starts_at: fx.FROZEN_S - 600,
    ends_at: null,
  });
  ctx.overrides.alerts = (route, fixtures) => json(route, { ...fixtures.alerts(), alerts: [alert("First wording")] });
  await open(page);
  await page.locator("#alert-banner-dismiss").focus();

  ctx.overrides.alerts = (route, fixtures) =>
    json(route, { ...fixtures.alerts(), alerts: [alert("All subway service suspended")] });
  await page.evaluate(() => loadAlerts());
  await expect(page.locator(".alert-banner-row")).toContainText("All subway service suspended");

  const after = await state(page);
  expect(after.active, "focus returns to the rebuilt dismiss button, not to the map").toBe(
    "alert-banner-dismiss",
  );
  expect(after.announced, "and the reword announces as an alert, not as a rescue").not.toContain(
    "Focus moved to the map",
  );
});

test("A8g. a vehicle vanishing while the rider is elsewhere says nothing and moves nothing", async ({ page }) => {
  // THE SILENCE HALF, on the retention path this time. Same destruction, same code, rider
  // parked somewhere unrelated: the door must not narrate the feed at them. Without this
  // the door could be "announce whenever a marker with an open popup is destroyed", which
  // would speak over a rider who is typing in the search box.
  const ctx = await installMocks(page);
  await open(page);

  const id = await page.evaluate(() => [...railroads.keys()][0]);
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), id);
  await expectPopupState(page, { registry: "railroads", key: id }, true);

  // NO TOGGLE CLICK. At the default width the panel is already DOCKED OPEN, so clicking
  // the toggle would close it and leave focus on the toggle rather than in the search box
  // (the same trap A3's A4b fell into). Assert the state instead of assuming it, then take
  // the control directly.
  await expect(page.locator("#stations-panel")).toBeVisible();
  await page.locator("#stations-search").focus();
  await expect(page.locator("#stations-search")).toBeFocused();

  const survivors = await page.evaluate(() => railroads.size);
  // The payload key is `data` and the registry key is `system|trip_id` (railroadKey), so
  // the filter is written against the real shape rather than a guessed one; the assertion
  // below on the registry SIZE is what catches a filter that silently matched nothing.
  ctx.overrides.railroads = (route, fixtures, dropped = id) => {
    const body = fixtures.railroads();
    body.data = body.data.filter((t) => `${t.system}|${t.trip_id}` !== dropped);
    return json(route, body);
  };
  await page.evaluate(() => refreshAll());
  await expect
    .poll(async () => page.evaluate(() => railroads.size), { timeout: 5_000 })
    .toBe(survivors - 1);

  const after = await state(page);
  expect(after.active, "the rider keeps the control they were using").toBe("stations-search");
  expect(after.announced, "and hears nothing about it").toBe("");
});

test("A8h. the strip that survives with nothing focusable in it still rescues the rider", async ({ page }) => {
  // THE PATH MUTATION TESTING FOUND UNCOVERED. Dropping the rebuild-side rescue left every
  // other spec in this file green, because all of them exercise the UNMOUNT branch. This
  // is the other one: when the alert set empties on a poll whose feed is ALSO stale, the
  // strip is rebuilt carrying only the "alerts may be out of date" row and no dismiss
  // button, so the A2 restore finds no successor and gives up.
  //
  // Measured on the shipped tree, it is the most confusing variant of the defect: the
  // banner is still visibly on screen, it has nothing focusable inside it, and the rider
  // is on the body. Nothing is removed, so an unmount-shaped spec cannot see it.
  const ctx = await installMocks(page);
  const alert = {
    id: "vanish-4",
    system: "subway",
    header: "Reduced service systemwide while crews clear a disabled train",
    description: null,
    effect: "REDUCED_SERVICE",
    cause: "OTHER_CAUSE",
    routes: [],
    stops: [],
    starts_at: fx.FROZEN_S - 600,
    ends_at: null,
  };
  ctx.overrides.alerts = (route, fixtures) => json(route, fixtures.alertsWithSystems({ alerts: [alert] }));
  await open(page);
  await page.locator("#alert-banner-dismiss").focus();

  // Alerts empty AND the feed is older than ALERTS_STALE_AFTER_S (300s), which is what
  // keeps the strip rendered while removing the only thing in it that could hold focus.
  ctx.overrides.alerts = (route, fixtures) =>
    json(route, fixtures.alertsWithSystems({ alerts: [], fetchedAt: fx.FROZEN_S - 400 }));
  await page.evaluate(() => loadAlerts());

  // The state that makes this path distinct: strip present, nothing focusable in it.
  await expect(page.locator(".alert-stale")).toBeVisible();
  await expect(page.locator("#alert-banner-dismiss")).toHaveCount(0);

  const after = await state(page);
  expect(after.active, "a visible strip with no controls must not strand the rider").toBe("map");
  expect(after.announced).toBe("Alerts cleared. Focus moved to the map.");
});

test("A8i. the last alert clearing while the rider is typing says nothing and moves nothing", async ({ page }) => {
  /* THE BANNER'S SILENCE HALF, which round 4 found had no spec at all.

     A8b and A8g pin the silence half on the VEHICLE path. On the banner path only the
     rescues were pinned (A8d, A8e, A8h), so the door could be rewritten to fire on the CAUSE
     ("a live strip was torn down") instead of on the RIDER, and nothing would notice.
     Measured: keying both branches on the strip's own state rather than on where focus is
     left all 162 e2e specs and all 167 node tests green.

     What that costs is the thing this file's header forbids in its own words: "speech is
     earned by a transition in the RIDER'S own state, not by every event that happens to be
     true". A screen-reader or keyboard rider typing a station name when the last agency-wide
     alert clears on a background poll would be yanked out of the search box onto the map and
     told "Alerts cleared. Focus moved to the map.": a WCAG 3.2.2 change of context nobody
     asked for, plus speech nobody earned.

     The rider is in #stations-search rather than on the toggle, because the search box is
     where a rider actually is while the panel is open, and because being mid-word is what
     makes the interruption cost something. */
  const ctx = await installMocks(page);
  ctx.overrides.alerts = (route, fixtures) => {
    const body = fixtures.alerts();
    body.alerts = [
      {
        id: "vanish-quiet-1",
        system: "subway",
        header: "Reduced service systemwide while crews clear a disabled train",
        description: null,
        effect: "REDUCED_SERVICE",
        cause: "OTHER_CAUSE",
        routes: [],
        stops: [],
        starts_at: fx.FROZEN_S - 600,
        ends_at: null,
      },
    ];
    return json(route, body);
  };
  await open(page);
  await expect(page.locator("#alert-banner-dismiss")).toBeVisible();

  // The panel is docked open at this width, so the search box is reachable without a click
  // that would move focus somewhere else first (the trap A8g records).
  await expect(page.locator("#stations-panel")).toBeVisible();
  await page.locator("#stations-search").fill("tim");
  await expect(page.locator("#stations-search")).toBeFocused();

  // The incident ends on a poll. No rider action at all.
  ctx.overrides.alerts = (route, fixtures) => json(route, { ...fixtures.alerts(), alerts: [] });
  await page.evaluate(() => loadAlerts());
  await expect(page.locator("#alert-banner-dismiss"), "the strip really is torn down").toHaveCount(0);

  const after = await state(page);
  expect(after.active, "the rider keeps the control they were using").toBe("stations-search");
  expect(after.announced, "and hears nothing about it").toBe("");
  expect(
    await page.locator("#stations-search").inputValue(),
    "and what they had typed is still there",
  ).toBe("tim");
});
