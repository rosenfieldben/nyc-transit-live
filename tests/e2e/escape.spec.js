// A4: THE ESCAPE LADDER. One key, one rule, one place that knows the order.
//
// WHAT IT REPLACED. Escape used to be a focus-location switch: stations.js bound a handler
// on the panel, so focus inside the panel closed the PANEL and left an open popup alone,
// while Leaflet's own handler closed the POPUP but only while document.activeElement was
// the #map container itself. Focus in a popup, on the toggle or on the banner did nothing.
// Recon measured all four combinations at 375 and 1280 and found them identical at both
// widths, so this was never a width bug: which surface closed depended on where the rider
// happened to be standing.
//
// THE RULE, as amended by measurement: THE RIDER'S OWN SURFACE FIRST, then the topmost
// transient. Focus inside a transient closes that transient; focus anywhere else closes
// the popup if one is open, else the panel. The banner is on neither branch, because it is
// ambient status rather than a dialog and dismissing it stays a deliberate act.
//
// The phase decision said "popup first" flatly. That was written without the panel-sync
// popup in view: selecting a station in the PANEL opens that station's popup on the map
// (A1's syncMapToStation, on purpose, so a sighted keyboard rider sees one application
// rather than two). A literal popup-first rung therefore closes a popup while the rider is
// looking at the list, which broke A1a, A1b, A1m and A1q, and at 375 closes something
// behind the opaque overlay that the rider cannot see at all. Those four A1 specs are left
// exactly as A1 shipped them and now double as this ladder's regression pins.

const { test, expect } = require("@playwright/test");
const { installMocks, json } = require("./mock");
const { expectPopupState } = require("./popup");
const { expectState } = require("./state");
const fx = require("./fixtures/api");

const PHONE = { width: 375, height: 667 };

async function open(page) {
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  await expect
    .poll(async () => page.evaluate(() => document.querySelectorAll(".leaflet-marker-icon").length), {
      timeout: 15_000,
    })
    .toBeGreaterThan(5);
}

async function withBanner(page) {
  const ctx = await installMocks(page);
  ctx.overrides.alerts = (route, fixtures) => {
    const body = fixtures.alerts();
    body.alerts = [
      {
        id: "esc-1",
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
  return ctx;
}

// Asked of Leaflet, not of the DOM: a closed popup lingers through its fade, and under
// this suite's paused clock it never leaves at all.
//
// ROUND 3 SWEPT map._popup OUT OF HERE TOO. It was `map._popup && map.hasLayer(map._popup)`,
// which is the same recency-not-identity read the round-2 fix took out of the Escape rung:
// map._popup is whichever popup opened MOST RECENTLY and Leaflet never clears it on close.
// The app answers this from a register it maintains itself (openPopupsOnMap in shared.js),
// so the suite asks the same question the app does rather than keeping a second, wronger
// implementation alive next to it. The one deliberate exception is A9m, which reads
// map._popup on purpose because staging its trap IS setting map._popup to the wrong popup.
const ladderState = (page) =>
  page.evaluate(() => ({
    popupOpen: openPopupsOnMap().length > 0,
    panelOpen: !document.getElementById("stations-panel").hidden,
    bannerRows: document.querySelectorAll(".alert-banner-row").length,
  }));

test("A9a. one Escape closes the popup and leaves the panel; the second closes the panel", async ({ page }) => {
  // THE LADDER ITSELF. Both surfaces open at once is the state that used to be
  // unpredictable, and it is the state the ordering exists for.
  // RUN AT THE DOCKED WIDTH, and that is a composition fact rather than a preference: at
  // 375 the map is INERT under the overlay (A4 deliverable 1), so focus cannot be moved
  // there at all and the rider is necessarily inside the panel. The order rung therefore
  // has to be exercised where a rider can genuinely stand outside both surfaces. A9f
  // covers the same ordering at mobile, parking focus on the body instead.
  await withBanner(page);
  await open(page);
  await expect(page.locator("#stations-panel")).toBeVisible(); // docked open at this width
  const id = await page.evaluate(() => [...railroads.keys()][0]);
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), id);
  await expectPopupState(page, { registry: "railroads", key: id }, true);

  // FOCUS OUTSIDE BOTH, which is what makes this the ORDER test rather than the
  // rider's-surface test. Opening the panel leaves focus in the search box, and from
  // there the rung is the panel by rule; the topmost-transient ordering only applies to a
  // rider standing in neither surface. A9d covers the inside-a-surface positions.
  //
  // ROUND 4 MEASURED WHAT THIS SPEC AND A9c CAN AND CANNOT TELL, and the comment now says
  // so rather than implying more. Reversing the outside-both rung order reddens this spec,
  // A9c, A9f and A9l — four, so the ORDER is well pinned. What neither this spec nor A9c
  // can see is the ladder DECLINING the popup rung and letting Leaflet's own container
  // handler close the popup instead: from the map container the two are indistinguishable,
  // and it is A9f, A9h and A9l — the specs that stand the rider somewhere Leaflet's handler
  // does not reach — that catch it. That is a division of labour, not a hole, and writing
  // it down is what stops the next reader trusting this spec for a claim it cannot make.
  await page.locator("#map").focus();

  expect(await ladderState(page), "both transients open, banner showing").toEqual({
    popupOpen: true,
    panelOpen: true,
    bannerRows: 1,
  });

  await page.keyboard.press("Escape");
  expect(await ladderState(page), "first Escape takes the topmost transient only").toEqual({
    popupOpen: false,
    panelOpen: true,
    bannerRows: 1,
  });

  await page.keyboard.press("Escape");
  expect(await ladderState(page), "second Escape takes the panel; the banner survives both").toEqual({
    popupOpen: false,
    panelOpen: false,
    bannerRows: 1,
  });

  // THE A1 FOCUS CONTRACT IS INHERITED WHOLE, including the half that does nothing. The
  // ladder calls closeStationsPanel, which returns focus to the opener only when focus was
  // INSIDE the panel: closing a panel the rider was not standing in must not yank them
  // somewhere else. Here the rider is on the map, so they stay on the map. A9h asserts the
  // other half, where the rider closes the panel from inside it and does land on the
  // toggle. Asserting a return here would have been asserting the contract backwards.
  await expect(page.locator("#map"), "the rider was not in the panel, so nothing yanks them").toBeFocused();

  // And a third Escape with nothing transient left does not touch the banner.
  await page.keyboard.press("Escape");
  expect((await ladderState(page)).bannerRows, "the banner is never on the ladder").toBe(1);
});

test("A9b. Escape with nothing transient open changes nothing at all", async ({ page }) => {
  // THE NO-OP CASE, which matters because the ladder handles in the CAPTURE phase and
  // could very easily swallow a key nobody asked it to swallow. Nothing transient open
  // means the handler must not preventDefault, must not stopPropagation, and must leave
  // Leaflet's own handler reachable.
  await installMocks(page);
  await open(page);
  await page.locator("#stations-toggle").click(); // docked open at this width: close it
  await expect(page.locator("#stations-panel")).toBeHidden();

  const before = await page.evaluate(() => ({
    center: map.getCenter(),
    zoom: map.getZoom(),
    scrollY: window.scrollY,
    scrollX: window.scrollX,
  }));

  /* THE KEY IS WATCHED ON ITS WAY PAST, because "leaves the event entirely alone" is not
     something a before/after picture of the map can see. Round 4 deleted the one line that
     implements it — `if (!closed) return;` — and this spec stayed green, along with every
     other escape spec: the ladder called preventDefault and stopPropagation on a key it had
     done nothing with, and nothing noticed. Leaflet's own Escape branch happens to return
     early when there is no popup, so today there is no rider-visible consequence; what the
     property protects is the NEXT component that binds Escape.
     A bubble-phase listener on the document is exactly the observer that a capture-phase
     stopPropagation would silence, so its firing IS the assertion. */
  await page.evaluate(() => {
    window.__escapesSeen = 0;
    window.__escapesDefaultPrevented = 0;
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      window.__escapesSeen += 1;
      if (e.defaultPrevented) window.__escapesDefaultPrevented += 1;
    });
  });

  await page.locator("#map").focus();
  await page.keyboard.press("Escape");
  await page.keyboard.press("Escape");

  expect(
    await page.evaluate(() => ({
      seen: window.__escapesSeen,
      prevented: window.__escapesDefaultPrevented,
    })),
    "with nothing transient open the key must reach the rest of the page, unprevented",
  ).toEqual({ seen: 2, prevented: 0 });

  const after = await page.evaluate(() => ({
    center: map.getCenter(),
    zoom: map.getZoom(),
    scrollY: window.scrollY,
    scrollX: window.scrollX,
  }));
  expect(after, "no map reset, no scroll hijack").toEqual(before);
  expect(await ladderState(page), "and nothing opened or closed").toEqual({
    popupOpen: false,
    panelOpen: false,
    bannerRows: 0,
  });
});

test("A9c. one Escape never closes two surfaces, from the position where Leaflet also acts", async ({ page }) => {
  // THE INTERACTION, MEASURED RATHER THAN ASSUMED, and the measurement corrected the
  // claim. The worry was a race with Leaflet's own Escape-closes-popup handler, which acts
  // only while the map container holds focus: if it ran first, a bubble-phase ladder would
  // find no popup and take the next rung, closing the panel too.
  //
  // Instrumented in bubble mode, that does not happen: the event propagates untouched
  // through the container to the document, the ladder closes the popup itself, and Leaflet
  // does not act. So this spec asserts the OUTCOME that matters to a rider (one key, one
  // surface, from the one focus position where two handlers could both have a claim) and
  // not the mechanism, because the mechanism turned out not to be what was protecting it.
  // The capture flag is pinned separately, at the source, in frontend/keyboard.test.js.
  await installMocks(page);
  await open(page);
  await expect(page.locator("#stations-panel")).toBeVisible(); // docked open at this width

  const id = await page.evaluate(() => [...railroads.keys()][0]);
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), id);
  await expectPopupState(page, { registry: "railroads", key: id }, true);
  await page.locator("#map").focus();
  expect(await page.evaluate(() => document.activeElement.id), "Leaflet only acts from here").toBe("map");

  await page.keyboard.press("Escape");
  expect(await ladderState(page), "the popup closes and the panel is untouched").toEqual({
    popupOpen: false,
    panelOpen: true,
    bannerRows: 0,
  });

  // And the next rung is still available, so the first press consumed one surface only.
  await page.keyboard.press("Escape");
  expect((await ladderState(page)).panelOpen, "the second press takes the next rung").toBe(false);
});

/* A9d's THIRD LEG IS REDUNDANT, AND ROUND 4 SAYS SO OUT LOUD. Its "from NEITHER, the popup
   goes first" leg survives both the order reversal AND a rung that closes both surfaces at
   once — measured — because by the time it runs the panel is already shut and there is
   nothing left to order. The ordering it names is pinned by A9a, A9c, A9f and A9l; this leg
   is a dispatch check, not an order check. Kept because the three legs together are what
   make the "same logic from every position" claim, and corrected because a leg that reads
   as protection and is not is exactly what this round exists to find. */
test("A9d. the same ladder logic runs from every focus position, and closes what the rider is in", async ({ page }) => {
  // THE AMENDED CLAIM. The deliverable asked for "identical behaviour from inside the popup
  // and from inside the panel", and the honest form of that is identical LOGIC, not an
  // identical outcome: the rule is one rule, applied from wherever focus is, and the
  // surface it closes differs precisely because the rider's position differs. Asserting an
  // identical outcome would be asserting that Escape ignores where the rider is standing,
  // which is the behaviour this ladder exists to replace.
  //
  // Both starting points have the SAME two surfaces open, so nothing but position varies.
  await installMocks(page);
  await open(page);
  await expect(page.locator("#stations-panel")).toBeVisible();

  const id = await page.evaluate(() => [...railroads.keys()][0]);

  // From INSIDE the popup: the popup closes, the panel is untouched.
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), id);
  await expectPopupState(page, { registry: "railroads", key: id }, true);
  await page.locator(".leaflet-popup-close-button").focus();
  await page.keyboard.press("Escape");
  expect(await ladderState(page), "inside the popup, Escape takes the popup").toEqual({
    popupOpen: false,
    panelOpen: true,
    bannerRows: 0,
  });

  // From INSIDE the panel, same two surfaces open: the panel closes, the popup survives
  // for a later press from outside it.
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), id);
  await expectPopupState(page, { registry: "railroads", key: id }, true);
  await page.locator("#stations-search").focus();
  await page.keyboard.press("Escape");
  expect(await ladderState(page), "inside the panel, Escape takes the panel").toEqual({
    popupOpen: true,
    panelOpen: false,
    bannerRows: 0,
  });

  // And from NEITHER, the topmost-transient order applies: the popup goes first.
  await page.locator("#map").focus();
  await page.keyboard.press("Escape");
  expect(await ladderState(page), "outside both, the popup is the first rung").toEqual({
    popupOpen: false,
    panelOpen: false,
    bannerRows: 0,
  });
});

test("A9g. inside the popup the panel opened, Escape still takes the popup", async ({ page }) => {
  // THE COMPOSITION THE RULING NAMED. The panel's own selection opens a popup on the map,
  // and A2's cross-link lands focus INSIDE that popup's content node. A rider who followed
  // that path is standing in the popup, not in the panel, so the rung is the popup even
  // though the panel is open behind it and even though the panel is what opened it.
  await installMocks(page);
  await open(page);
  await expect(page.locator("#stations-panel")).toBeVisible();

  // Select a station in the panel: syncMapToStation opens its popup on the map.
  await page.locator("#stations-search").fill("times");
  await page.locator("#stations-results button.station-row").first().click();
  await expect.poll(async () => (await ladderState(page)).popupOpen, { timeout: 5_000 }).toBe(true);

  // Stand inside that popup, the way the cross-link landing point does (tabindex -1 on the
  // content node, which is exactly what openStationFromCrossLink focuses).
  await expectState(page, "one popup open", "A9g: the panel's own sync opens exactly one");
  await page.evaluate(() => {
    const content = openPopupsOnMap()[0].getElement().querySelector(".leaflet-popup-content");
    content.setAttribute("tabindex", "-1");
    content.focus();
  });
  await expectState(page, "focus inside the popup", "A9g: the rider must be standing in the synced popup");

  await page.keyboard.press("Escape");
  expect(await ladderState(page), "the rider is in the popup, so the popup closes").toEqual({
    popupOpen: false,
    panelOpen: true,
    bannerRows: 0,
  });
});

test("A9h. at 375 the overlay closes first and the popup it opened survives for the map rung", async ({ page }) => {
  // THE MOBILE COMPOSITION, and the reason literal popup-first was rejected. At this width
  // the panel COVERS the map, so a popup the panel opened is behind an opaque overlay. A
  // rider pressing Escape must get the thing they can see, and the popup must still be
  // there afterwards rather than silently consumed by a press aimed at the panel.
  await page.setViewportSize(PHONE);
  await installMocks(page);
  await open(page);
  await page.locator("#stations-toggle").click();
  await expect(page.locator("#stations-panel")).toBeVisible();

  await page.locator("#stations-search").fill("times");
  await page.locator("#stations-results button.station-row").first().click();
  await expect.poll(async () => (await ladderState(page)).popupOpen, { timeout: 5_000 }).toBe(true);

  // The popup really is hidden behind the overlay, which is what makes closing it first
  // the wrong answer rather than merely a different one.
  expect(
    await page.evaluate(() => {
      const el = openPopupsOnMap()[0].getElement();
      const r = el.getBoundingClientRect();
      const top = document.elementFromPoint(Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2));
      return !!(top && !el.contains(top));
    }),
    "the synced popup is behind the overlay at this width",
  ).toBe(true);

  // Focus is in the panel (the row the rider just activated), so the panel is the rung.
  await page.keyboard.press("Escape");
  expect(await ladderState(page), "the overlay closes and the popup it opened survives").toEqual({
    popupOpen: true,
    panelOpen: false,
    bannerRows: 0,
  });
  await expect(page.locator("#stations-toggle"), "with the A1 focus return intact").toBeFocused();

  // And now that the rider can see it, the next Escape takes it through the map rung.
  await page.keyboard.press("Escape");
  expect((await ladderState(page)).popupOpen, "the revealed popup is the next rung").toBe(false);
});

test("A9e. Escape reaches the panel from outside it, which only a page-level door can do", async ({ page }) => {
  // THE CONSOLIDATION'S OBSERVABLE HALF. The old handler was bound ON the panel, so it
  // could only ever fire while focus was inside the panel: a rider who opened the list,
  // clicked the map and pressed Escape got nothing. The ladder is bound at the document,
  // so the key works from anywhere.
  //
  // The other half of the consolidation, that stations.js no longer binds its own keydown,
  // is not observable from the page at all once the capture-phase door stops the event
  // before a panel-bound listener could see it. It is asserted at the SOURCE instead, in
  // frontend/keyboard.test.js, the same way markers.test.js pins the marker factory.
  await installMocks(page);
  await open(page);
  await expect(page.locator("#stations-panel")).toBeVisible();

  // Stand outside the panel with no popup open: the old panel-bound handler was unreachable
  // from here, so this press is the one that used to do nothing.
  await page.locator("#stations-toggle").focus();
  expect(
    await page.evaluate(() => document.getElementById("stations-panel").contains(document.activeElement)),
    "the rider must be standing outside the panel for this to mean anything",
  ).toBe(false);
  expect((await ladderState(page)).popupOpen, "and no popup may be open, or the popup rung would answer").toBe(
    false,
  );

  await page.keyboard.press("Escape");
  expect((await ladderState(page)).panelOpen, "the panel closes from outside itself").toBe(false);
});

test("A9f. at mobile width, a rider standing outside both still gets the popup rung first", async ({ page }) => {
  // The ORDER half at mobile, complementing A9h's rider's-surface half. Focus is moved out
  // of the overlay first, so the rule being exercised is "topmost transient" rather than
  // "the surface you are in". The two together are the whole ladder at this width.
  await page.setViewportSize(PHONE);
  await installMocks(page);
  await open(page);
  await page.locator("#stations-toggle").click();
  await expect(page.locator("#stations-panel")).toBeVisible();

  const id = await page.evaluate(() => [...railroads.keys()][0]);
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), id);
  await expectPopupState(page, { registry: "railroads", key: id }, true);

  // #map is inert under the overlay, so focus is parked on the body instead: the point is
  // only that the rider is in neither transient.
  await page.evaluate(() => document.activeElement.blur());
  expect(
    await page.evaluate(() => document.getElementById("stations-panel").contains(document.activeElement)),
    "focus must be outside the panel",
  ).toBe(false);

  await page.keyboard.press("Escape");
  expect(await ladderState(page), "outside both, the popup is still the first rung").toEqual({
    popupOpen: false,
    panelOpen: true,
    bannerRows: 0,
  });

  await page.keyboard.press("Escape");
  expect((await ladderState(page)).panelOpen, "and the panel is the second").toBe(false);
  expect(
    await page.evaluate(() => document.getElementById("panel").inert),
    "closing through the ladder still releases the overlay's inertness",
  ).toBe(false);
});

/* THE POPUP RUNG'S FOCUS CONTRACT, added in the adversarial round after two reviewers found
   independently that it did not have one. Every spec above asserts which surface is OPEN.
   None asserted where the RIDER is, and closing the popup they were standing in dropped
   them on document.body in silence: measured {"active":"BODY","announced":""}, with the
   next Tab restarting at the skip link.
   That is worth a comment rather than a quiet fix, because the gap was structural. A4 built
   the vanishing-focus door for exactly this outcome and then opened a new path to it, and a
   suite that checks state and not focus cannot see the difference. Both specs below fail
   against the pre-fix ladder. */
test("A9i. Escape from inside the popup leaves the rider on the map, not on the body", async ({ page }) => {
  await installMocks(page);
  await open(page);
  const id = await page.evaluate(() => [...railroads.keys()][0]);
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), id);
  await expectPopupState(page, { registry: "railroads", key: id }, true);

  await page.locator(".leaflet-popup-close-button").focus();
  await expectState(page, ["one popup open", "focus inside the popup"], "A9i");

  await page.keyboard.press("Escape");
  expect((await ladderState(page)).popupOpen, "the rung still closes the popup").toBe(false);
  await expect(page.locator("#map"), "and the rider lands on the map container").toBeFocused();

  // QUIETLY. The vanishing-focus door announces because the rider did not ask for anything;
  // this move is the expected consequence of their own keypress, and narrating every Escape
  // would be noise. Asserted so a later change cannot make the two doors say the same thing.
  await expect(page.locator("#page-announce")).toHaveText("");
});

test("A9j. the popup's own close button lands the rider in the same place", async ({ page }) => {
  // THE OTHER DOOR ON THE SAME SURFACE. A4 removed this button's href to fix an anchor to a
  // fragment that never existed, which made it a real control; it inherited the same missing
  // contract. Driven by Enter rather than by click(), because the keyboard path is the one
  // that strands: a mouse user is not holding a focus position to lose.
  await installMocks(page);
  await open(page);
  const id = await page.evaluate(() => [...railroads.keys()][0]);
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), id);
  await expectPopupState(page, { registry: "railroads", key: id }, true);

  await page.locator(".leaflet-popup-close-button").focus();
  await expectState(page, ["one popup open", "focus inside the popup"], "A9j");
  await page.keyboard.press("Enter");

  expect((await ladderState(page)).popupOpen, "Enter on the close button still closes it").toBe(false);
  await expect(page.locator("#map")).toBeFocused();
  await expect(page.locator("#page-announce")).toHaveText("");
});

test("A9k. from inside the panel, Escape takes the panel and leaves the popup alone", async ({ page }) => {
  /* RETITLED IN ROUND 2, BECAUSE THE OLD TITLE AND COMMENT DESCRIBED A TEST THIS IS NOT.
     It read "closing a popup the rider was NOT in does not move them" and claimed that
     without it, "always focus the map on close" would pass. Both were false and a mutation
     said so: the rider is parked in the search box, so the ladder takes the PANEL rung and
     the popup helper is never entered, and the unconditional-focus mutant passed this spec.
     A9l is the spec that kills it.
     What this actually pins is worth keeping under an honest name: the rider's-surface rung.
     Focus inside the panel closes the panel even though a popup is open, which is the
     ordering A1a, A1b, A1m and A1q depend on and the one the phase decision originally got
     backwards. */
  await installMocks(page);
  await open(page);
  const id = await page.evaluate(() => [...railroads.keys()][0]);
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), id);
  await expectPopupState(page, { registry: "railroads", key: id }, true);

  await page.locator("#stations-search").focus();
  // THE WITNESS A9k SPENT TWO ROUNDS WITHOUT. Its title once claimed it closed a popup; the
  // rung it actually exercises is the panel one, and that is only true while focus is inside
  // the panel. Stated up front, so the spec fails on the state rather than on a downstream
  // assertion that happens to notice.
  await expectState(page, ["one popup open", "focus inside the panel"], "A9k");
  await page.keyboard.press("Escape");

  expect(await ladderState(page)).toMatchObject({ popupOpen: true, panelOpen: false });
  await expect(page.locator("#stations-toggle"), "the A1 return, not the map").toBeFocused();
});

test("A9l. closing the popup from a control outside it leaves that control focused", async ({ page }) => {
  // A9k WAS NOT THE MUST-NOT-FIRE TEST IT LOOKED LIKE, and a mutation said so: making the
  // helper focus the map UNCONDITIONALLY passed all eleven specs. A9k parks the rider in
  // the panel, so Escape takes the PANEL rung and the popup helper is never reached.
  // The case that reaches it is a rider standing on a control that is in neither transient,
  // where rung one closes the popup and the rider must not be moved off what they were on.
  // The Stations toggle is that control: outside the popup, outside the panel, and not
  // #map, so an unconditional move is visible here and invisible everywhere else. Not the
  // legend disclosure, which was the first choice and is display:none above the breakpoint,
  // so focus() on it is a no-op and the spec failed against correct code.
  await installMocks(page);
  await open(page);
  const id = await page.evaluate(() => [...railroads.keys()][0]);
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), id);
  await expectPopupState(page, { registry: "railroads", key: id }, true);

  await page.locator("#stations-toggle").focus();
  await expect(page.locator("#stations-toggle")).toBeFocused();
  await expectState(page, "one popup open", "A9l");

  await page.keyboard.press("Escape");
  expect((await ladderState(page)).popupOpen, "rung one still takes the popup").toBe(false);
  await expect(page.locator("#stations-toggle"), "and the rider is left exactly where they were").toBeFocused();
});

test("A9m. the close button closes the popup that owns it, not whichever is current", async ({ page }) => {
  /* REBUILT TWICE, AND THE SECOND REBUILD IS THE ONE THAT BITES.
     Round 2 wrote it opening a second popup with openOn(map), which auto-closes the first,
     so at click time only one popup was live and the fix passed because closing a dead popup
     is a no-op. Round 3 caught that. The first rebuild then opened the second popup with
     addLayer, which keeps both alive but does NOT set map._popup, so map._popup was still
     the first popup and reverting the fix STILL passed: the state existed but did not
     discriminate.
     The state that discriminates needs map._popup to be the OTHER popup while the first is
     still open. Leaflet's openOn removes the map's current popup only when that popup's
     autoClose is set, so clearing it on the first popup and then openOn-ing the second gives
     exactly that: two live popups, and the map's idea of "current" pointing at the wrong one.
     No rider reaches this today. What it pins is that the answer comes from the button's own
     popup rather than from the map's most-recent field, which must survive the first feature
     that opens two. */
  await installMocks(page);
  await open(page);
  const id = await page.evaluate(() => [...railroads.keys()][0]);
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), id);
  await expectPopupState(page, { registry: "railroads", key: id }, true);

  const staged = await page.evaluate((key) => {
    const first = railroads.get(key).marker.getPopup();
    first.options.autoClose = false; // so openOn below leaves it alone
    const second = L.popup({ autoClose: false, closeOnClick: false })
      .setLatLng(map.getCenter())
      .setContent("second")
      .openOn(map);
    window.__secondPopup = second;
    return { mapPopupIsSecond: map._popup === second, firstStillOpen: map.hasLayer(first) };
  }, id);
  await expectState(page, "two popups open", "A9m needs both live for the question to exist");
  expect(staged.mapPopupIsSecond, "and the map's idea of current must be the OTHER one").toBe(true);
  expect(staged.firstStillOpen, "while the first is still open").toBe(true);

  const out = await page.evaluate((key) => {
    const first = railroads.get(key).marker.getPopup();
    first.getElement().querySelector(".leaflet-popup-close-button").click();
    return { firstOpen: map.hasLayer(first), secondOpen: map.hasLayer(window.__secondPopup) };
  }, id);

  expect(out.firstOpen, "the button closed its own popup").toBe(false);
  expect(out.secondOpen, "and left the other one alone").toBe(true);
});

test("A9n. with the fade off, the close button still hands the rider the map", async ({ page }) => {
  /* THE CAPTURE FLAG, PINNED, AND IT TURNS OUT TO BE A REDUCED-MOTION CONTRACT.

     The app's click handler on the popup close button is registered with capture:true and
     calls stopImmediatePropagation, so it beats Leaflet's own close handler on that same
     button. Round 3 asked what pins the flag and the answer was nothing: flipping it to
     false left all thirteen specs in this file green. Worth understanding why, because the
     reason is the whole spec.

     Leaflet binds `function (t) { stop(t); this.close(); }` to the button at _initLayout,
     which is BEFORE our popupopen handler runs, so without capture Leaflet's listener goes
     first and the popup is already closed by the time the app decides where focus goes.
     That decision needs the rider to still be inside the popup — and they usually still
     are, because DivOverlay.onRemove defers the container's removal by 200ms when the map
     is fade-animated. The focus contract was riding on a fade timeout.

     A RIDER WHO ASKED FOR REDUCED MOTION HAS NO FADE. shared.js constructs the map with
     fadeAnimation: motionAtLoad, so for exactly the riders most likely to be on a keyboard,
     the container is removed SYNCHRONOUSLY, activeElement is BODY before the app's handler
     runs, and the rider is dropped at the top of the document. Measured with capture:false:
     A9j (fade on) passes, this spec fails on `#map` not being focused.

     So this is the pair A5g/A5h are for the pan: A9j is the ordinary rider and A9n is the
     reduced-motion one, and only together do they say the contract holds for both. */
  await page.emulateMedia({ reducedMotion: "reduce" }); // before goto: Leaflet reads it once
  await installMocks(page);
  await open(page);

  // THE STATE THAT MAKES THIS SPEC DIFFERENT FROM A9j, asserted rather than assumed. If a
  // future change stops passing the preference to Leaflet, this spec would quietly become
  // a duplicate of A9j instead of failing.
  expect(
    await page.evaluate(() => map._fadeAnimated),
    "reduced motion must actually have turned Leaflet's fade off, or this is just A9j again",
  ).toBe(false);

  const id = await page.evaluate(() => [...railroads.keys()][0]);
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), id);
  await expectPopupState(page, { registry: "railroads", key: id }, true);

  await page.locator(".leaflet-popup-close-button").focus();
  await expectState(page, ["one popup open", "focus inside the popup"], "A9n");
  await page.keyboard.press("Enter");

  expect((await ladderState(page)).popupOpen, "Enter on the close button still closes it").toBe(false);
  // AND THE CORPSE IS GONE IMMEDIATELY, which is the mechanism this spec exists to exercise:
  // with the fade on, the element the rider was standing in survives the close by 200ms and
  // any focus decision taken afterwards still finds them inside it.
  expect(
    await page.evaluate(() => document.querySelectorAll(".leaflet-popup").length),
    "with no fade the popup leaves the document in the same task, taking focus with it",
  ).toBe(0);
  await expect(page.locator("#map")).toBeFocused();
  // Still quiet: the rider asked for this, so it is not announced. Same rule as A9i and A9j.
  await expect(page.locator("#page-announce")).toHaveText("");
});

test("A9o. Escape from inside the banner leaves the banner alone", async ({ page }) => {
  /* THE BANNER'S ABSENCE FROM THE LADDER, ASSERTED FROM INSIDE IT.

     A9a's last line presses Escape with the rider on the MAP and checks the banner survives,
     which catches a banner added as a third FALLBACK rung. Round 4 added it as a rider's-own-
     surface rung instead — checked before transientHoldingFocus, so it fires only when focus
     is inside the banner — and every escape and vanish spec stayed green, because no spec
     ever pressed Escape from in there.

     The phase decision is explicit that dismissing the alert strip stays a DELIBERATE act:
     the banner is ambient status rather than a dialog, and a rider who has tabbed to the ×
     to read the alert must not lose the whole strip by pressing the key that means "get me
     out of this". */
  await withBanner(page);
  await open(page);
  await expect(page.locator("#alert-banner-dismiss")).toBeVisible();

  await page.locator("#alert-banner-dismiss").focus();
  await expect(page.locator("#alert-banner-dismiss")).toBeFocused();
  expect((await ladderState(page)).bannerRows, "a row must be showing for its survival to mean anything").toBe(1);

  await page.keyboard.press("Escape");
  /* WHAT THE RULE ACTUALLY SAYS, and this spec learned it by being wrong first. The banner is
     on NEITHER branch, which does not mean Escape does nothing from inside it: focus on the
     dismiss button is focus outside both transients, so the fallback rung runs and takes the
     panel, exactly as it would from the map. The claim is only that the banner itself is
     never what closes.
     Both halves are asserted, because the mutation this exists for breaks both: a banner
     given a rider's-own-surface rung removes the rows AND returns before the panel rung. */
  expect(await ladderState(page), "the banner survives; the fallback rung takes the panel as usual").toEqual({
    popupOpen: false,
    panelOpen: false,
    bannerRows: 1,
  });
  // And nothing yanked the rider: they were not inside the panel, so the A1 return does not
  // fire and they keep the control they were on.
  await expect(page.locator("#alert-banner-dismiss"), "and the rider keeps the control they were on").toBeFocused();
});

test("A9p. Escape closes the popup the rider is standing in, not whichever is topmost", async ({ page }) => {
  /* THE ESCAPE HALF OF A9m'S CONTRACT, which round 4 found had no pin.

     closeOpenPopup asks popupContaining(document.activeElement) BEFORE falling back to the
     topmost, and map.js's own comment says why: "closing the topmost would be the recency
     answer again, one level up from the field this file stopped reading". Deleting that
     first clause — taking the topmost always — left all fourteen escape specs green, because
     no spec ever had two popups live while the rider stood in one of them. A9m pins exactly
     this for the close BUTTON; the key had nobody.

     THE STAGING IS A9m'S, and it is the only staging that works: Leaflet's openOn removes
     the map's current popup only when that popup's autoClose is set, so clearing it on the
     first and then openOn-ing the second gives two live popups with the map's idea of
     "current" pointing at the wrong one. No rider reaches this today; what it pins is that
     the answer comes from where the RIDER is rather than from what opened last. */
  await installMocks(page);
  await open(page);
  const id = await page.evaluate(() => [...railroads.keys()][0]);
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), id);
  await expectPopupState(page, { registry: "railroads", key: id }, true);

  const staged = await page.evaluate((key) => {
    const first = railroads.get(key).marker.getPopup();
    first.options.autoClose = false; // so openOn below leaves it alone
    const second = L.popup({ autoClose: false, closeOnClick: false })
      .setLatLng(map.getCenter())
      .setContent("second")
      .openOn(map);
    window.__secondPopup = second;
    // The rider stands in the FIRST popup, which is not the map's current one.
    const content = first.getElement().querySelector(".leaflet-popup-content");
    content.setAttribute("tabindex", "-1");
    content.focus();
    return { mapPopupIsSecond: map._popup === second };
  }, id);
  await expectState(page, ["two popups open", "focus inside the popup"], "A9p needs both live and the rider in one");
  expect(staged.mapPopupIsSecond, "and the map's idea of current must be the OTHER one").toBe(true);

  await page.keyboard.press("Escape");

  const out = await page.evaluate((key) => {
    const first = railroads.get(key).marker.getPopup();
    return { firstOpen: map.hasLayer(first), secondOpen: map.hasLayer(window.__secondPopup) };
  }, id);
  expect(out.firstOpen, "the rung closed the popup the rider was in").toBe(false);
  expect(out.secondOpen, "and left the topmost one alone").toBe(true);
  await expect(page.locator("#map"), "and the rider lands on the map, as A9i requires").toBeFocused();
});
