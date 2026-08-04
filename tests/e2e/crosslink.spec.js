// A2: reaching a station that a vehicle is parked on top of.
//
// A vehicle marker lives in markerPane (z 600) and station dots are drawn on a canvas
// in stationPane (z 450), so a vehicle sitting on its station swallows every click
// meant for the station. The inventory measured it: clicking a station with a train on
// it opens the TRAIN popup and the station popup never fires, which puts the arrivals
// a rider came for out of reach at that pixel.
//
// Two resolutions, chosen by where the position came from. DERIVED positions (subway
// placed by stop_id, PATH interpolated along a route) may be drawn a few pixels off
// their point, because moving a computation states nothing false. MEASURED positions
// may not be moved at all, so their popups carry a link to the station instead. The
// principle lives once, at crossLinkHtml in systems/shared.js.
//
// Same hermetic harness as the rest of the suite.

const { test, expect } = require("@playwright/test");
const { installMocks, json } = require("./mock");
const { expectPopupState } = require("./popup");
const fx = require("./fixtures/api");

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

// The OPEN popup's text, read through Leaflet rather than by querying the document.
// A closing popup lingers in the DOM while it fades, so a document query can return
// the popup that was just dismissed; that cost a debugging round here and is the same
// trap the product code hit.
// AND NOT THROUGH map._popup, since round 3. That field answers "which opened most
// recently" and is never cleared on close, so on a closed-then-not-reopened popup it
// reports the corpse. openPopupsOnMap is the app's own register of what is actually on
// the map; asking it means this helper cannot disagree with the code it is testing.
const popupText = (page) => page.evaluate(() => {
  const [popup] = openPopupsOnMap();
  const root = popup && popup.getElement ? popup.getElement() : null;
  const el = root ? root.querySelector(".leaflet-popup-content") : null;
  return el ? el.textContent.replace(/\s+/g, " ").trim() : null;
});

test("A3a. a railroad train parked on its station links to that station's arrivals", async ({ page }) => {
  // The LIRR fixture train is PLACED: it carries a stop_id, which is what puts it at
  // the station's own coordinates and therefore on top of the station dot. That
  // stop_id is also the only station identity in the payload, and it is what the link
  // resolves through: no distance math, no nearest-station guess.
  await installMocks(page);
  await open(page);

  // Open the placed train's popup by clicking its marker directly, which is what a
  // rider does when the train is covering the station.
  const placed = await page.evaluate(() => {
    for (const [key, record] of railroads) {
      if (record.latest.stop_id != null) return { key, stop: record.latest.stop_id, system: record.latest.system };
    }
    return null;
  });
  expect(placed, "the fixture must contain a placed railroad train").not.toBeNull();
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), placed.key);

  const link = page.locator(".popup-crosslink");
  await expect(link).toHaveCount(1);
  // The station NAME is in the accessible name, so "Also here" is never announced on
  // its own with no indication of where.
  await expect(link).toContainText("Also here:");
  const label = await link.textContent();
  expect(label.replace("Also here:", "").trim().length, "the link must name the station").toBeGreaterThan(0);
  // And it resolves through a SYSTEM-QUALIFIED registry key, because LIRR and MNR id
  // spaces are independent and both are bare integers.
  await expect(link).toHaveAttribute("data-station-key", `${placed.system}|${placed.stop}`);
});

test("A3b. the cross-link works from the keyboard, and lands focus in the station popup", async ({ page }) => {
  // THE KEYBOARD PATH. A link that only works with a mouse would be a cross-link for
  // the riders who least need one: a pointer user can already zoom in and click past
  // the train.
  await installMocks(page);
  await open(page);

  const placed = await page.evaluate(() => {
    for (const [key, record] of railroads) if (record.latest.stop_id != null) return key;
    return null;
  });
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), placed);
  const before = await popupText(page);
  expect(before).toContain("Train");

  // Focus the link the way a keyboard rider reaches it, then activate with Enter. It
  // is a real <button>, so Enter activates it with no handler of ours involved.
  await page.locator(".popup-crosslink").focus();
  await expect(page.locator(".popup-crosslink")).toBeFocused();
  await page.keyboard.press("Enter");

  // The STATION popup is now open, and it is a different popup than the train's.
  await expect.poll(async () => popupText(page)).not.toBe(before);
  const after = await popupText(page);
  expect(after, "the station popup should carry arrivals, not the train's details").not.toContain("live GPS");

  // AND FOCUS FOLLOWED. Activating a link that asks to go somewhere must not leave the
  // rider on a button that no longer exists: opening a Leaflet popup replaces the
  // popup pane's contents, so the pressed button is gone by now.
  const focus = await page.evaluate(() => {
    const el = document.activeElement;
    if (!el || el === document.body) return "BODY";
    return el.className || el.tagName;
  });
  expect(focus, "focus must land inside the opened popup, never on the body").toContain("leaflet-popup-content");
});

test("A3c. a vehicle that names no station gets no link at all", async ({ page }) => {
  // A GPS railroad train carries no stop_id, so there is no station identity to
  // resolve and nothing to link to. Guessing the nearest dot would be worse than
  // silence: a rider who follows a wrong "Also here" gets confidently incorrect
  // arrivals with nothing on screen to contradict them.
  await installMocks(page);
  await open(page);

  const gps = await page.evaluate(() => {
    for (const [key, record] of railroads) if (record.latest.stop_id == null) return key;
    return null;
  });
  expect(gps, "the fixture must contain a GPS railroad train").not.toBeNull();
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), gps);

  await expect(page.locator(".leaflet-popup-content")).toContainText("live GPS");
  await expect(page.locator(".popup-crosslink"), "no station named, so no link").toHaveCount(0);
});

test("A3d. a subway train is drawn clear of its station dot, so both are clickable", async ({ page }) => {
  // The DERIVED half of the principle. A subway train is placed at its stop, so it sat
  // exactly on the station dot and swallowed its clicks; the marker is now anchored
  // above the point the way PATH's diamond already was. Asserted as geometry rather
  // than as an option value, so it fails if the offset stops having the effect it is
  // there for.
  await installMocks(page);
  await open(page);

  const boxes = await page.evaluate(() => {
    const marker = document.querySelector(".train-marker");
    const rect = marker.getBoundingClientRect();
    // The train's own anchor point on screen: where the station dot underneath it
    // would be drawn.
    const record = [...trains.values()][0];
    const point = map.latLngToContainerPoint(record.marker.getLatLng());
    const container = document.getElementById("map").getBoundingClientRect();
    return {
      markerBottom: rect.bottom - container.top,
      anchorY: point.y,
      iconAnchor: record.marker.getIcon().options.iconAnchor,
    };
  });
  // The marker's box ends above its own anchor point, which is the whole effect: the
  // pixel the station occupies is no longer covered by the train.
  expect(boxes.markerBottom, "the train must not cover its own anchor point").toBeLessThan(boxes.anchorY);
  expect(boxes.iconAnchor[1], "anchored above centre, like the PATH precedent").toBeGreaterThan(18 / 2);
});

test("A3e. focus parked on the cross-link survives a background refresh", async ({ page }) => {
  // FOUND BY REVIEW, BY REPRODUCTION. A popup's content is bound as a function, so the
  // 15s background refresh re-renders it wholesale and discards the old nodes. A rider
  // who tabbed to the cross-link and paused for one poll had focus dropped to
  // document.body while the button was still visibly on screen, and their Enter did
  // nothing. That is exactly the stranding the A1 focus contract exists to prevent,
  // reintroduced through a control built for keyboard riders.
  //
  // This is the same family as the two Leaflet behaviours already documented in
  // shared.js: the thing you are holding is quietly replaced underneath you.
  await installMocks(page);
  await open(page);

  const placed = await page.evaluate(() => {
    for (const [key, record] of railroads) if (record.latest.stop_id != null) return key;
    return null;
  });
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), placed);
  await page.locator(".popup-crosslink").focus();
  await expect(page.locator(".popup-crosslink")).toBeFocused();

  // One full poll, which re-renders the popup.
  await page.clock.runFor(15_000 + 1000);

  const after = await page.evaluate(() => {
    const el = document.activeElement;
    if (!el || el === document.body) return "BODY";
    return `${el.tagName}.${el.className}`;
  });
  expect(after, "focus must not be dropped to the body by a refresh").toContain("popup-crosslink");

  // AND IT STILL WORKS. Focus being on something that looks right is not enough: the
  // control must still be the live one, not a detached node left over from the render
  // that was thrown away.
  await page.keyboard.press("Enter");
  await expect.poll(async () => page.evaluate(() => (openStation ? openStation.station.name : null))).not.toBeNull();
});

test("A3g. the restored cross-link is the live one, even when the train has moved on", async ({ page }) => {
  // FOUND BY ROUND 3, WHICH EXISTED TO REVIEW ROUND 2's FIX. A3e restores focus to the
  // cross-link after a refresh destroys it, and the first version found the button again
  // by its data-station-key. That key is the STATION's, not the control's: railroad.js
  // builds it from the train's current stop_id, so a train advancing one stop changes it
  // by design. The rider tabbed to "Also here: Jamaica", the poll rendered "Also here:
  // Hicksville", the key did not match, and focus went to the inert content div while a
  // live button sat on screen. It stuck too, because the content div survives the next
  // update and the "leave alone what still has focus" guard then returns early forever.
  //
  // A3e pins the case where the stop is unchanged, which is why the suite stayed green
  // over this. This is the moving-train case, and the two together are what make the
  // restore mean "the control", not "that one station".
  const ctx = await installMocks(page);
  // A second LIRR station, so the train has somewhere to advance TO. Registered before
  // the page loads, because the station registry is built once from this payload.
  ctx.overrides.railroadStops = (route, fixtures) =>
    json(route, [...fixtures.railroadStops(), { system: "LIRR", id: "13", name: "Hicksville", lat: 40.7684, lon: -73.5251 }]);
  let advanced = false;
  ctx.overrides.railroads = (route, fixtures) => {
    const body = fixtures.railroads();
    if (advanced) {
      for (const train of body.data) if (train.stop_id === "12") {
        train.stop_id = "13";
        train.stop_name = "Hicksville";
      }
    }
    return json(route, body);
  };
  await open(page);

  const placed = await page.evaluate(() => {
    for (const [key, record] of railroads) if (record.latest.stop_id != null) return key;
    return null;
  });
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), placed);
  await expect(page.locator(".popup-crosslink")).toHaveAttribute("data-station-key", "LIRR|12");
  await page.locator(".popup-crosslink").focus();
  await expect(page.locator(".popup-crosslink")).toBeFocused();

  // The train reaches its next stop, and one poll re-renders the popup around it.
  advanced = true;
  await page.clock.runFor(15_000 + 1000);
  await expect(page.locator(".popup-crosslink")).toHaveAttribute("data-station-key", "LIRR|13");

  const after = await page.evaluate(() => {
    const el = document.activeElement;
    if (!el || el === document.body) return "BODY";
    return `${el.className}|${el.getAttribute("data-station-key")}|${el.textContent}`;
  });
  expect(after, "focus must be on the live cross-link, not on the content div").toContain("popup-crosslink");
  expect(after, "and it must be the button as it now reads, naming where it now goes").toContain("Hicksville");

  // The assertion that matters: the rider can still act. Focus sitting on a button that
  // looks right is not the same as the button working.
  await page.keyboard.press("Enter");
  await expect
    .poll(async () => page.evaluate(() => (openStation ? openStation.station.name : null)))
    .toBe("Hicksville");
});

test("A3f. a refresh leaves alone the controls it did not destroy", async ({ page }) => {
  // THE REGRESSION A3e's FIX INTRODUCED, found by five independent review lenses.
  // popup.update() reassigns the CONTENT node's innerHTML and nothing else, so the
  // popup's own close button is a sibling that survives every refresh. Restoring focus
  // unconditionally therefore moved it off a live control onto an inert container, on
  // every vehicle popup on every poll, and the rider's Enter stopped closing the popup.
  //
  // A restore is only ever correct for something that was actually destroyed.
  await installMocks(page);
  await open(page);

  const gps = await page.evaluate(() => {
    for (const [key, record] of railroads) if (record.latest.stop_id == null) return key;
    return null;
  });
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), gps);

  // The close button is the first tab stop inside any vehicle popup, and for a popup
  // with no cross-link it is the only one.
  await page.locator(".leaflet-popup-close-button").focus();
  await expect(page.locator(".leaflet-popup-close-button")).toBeFocused();

  await page.clock.runFor(15_000 + 1000);

  const after = await page.evaluate(() => {
    const el = document.activeElement;
    if (!el || el === document.body) return "BODY";
    return `${el.tagName}.${el.className}`;
  });
  expect(after, "a surviving control must keep focus across a refresh").toContain("leaflet-popup-close-button");

  // And it still does its job, which is the assertion that matters: focus sitting on
  // the right-looking element is not the same as the rider being able to act.
  await page.keyboard.press("Enter");
  // Through the harness helper. The measurement that made it necessary is recorded in
  // tests/e2e/popup.js: on a popup closed by its own button, isPopupOpen() is false and
  // the popup's _map is cleared, but map._popup still holds a stale reference AND one
  // ".leaflet-popup" node is still in the document, because the clock is paused and the
  // corpse never finishes fading. Two of the three obvious ways to ask report it open.
  await expectPopupState(page, { registry: "railroads", key: gps }, false);
});
