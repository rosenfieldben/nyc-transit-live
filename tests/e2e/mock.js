// Network interception for the hermetic smoke suite. Every request the app makes
// is fulfilled locally: the two unpkg Leaflet URLs from vendored, byte-identical
// dist files (which satisfies the SRI integrity attributes in index.html), and
// every /api/* endpoint from the handcrafted fixtures. Nothing leaves the machine,
// so CI needs no network at test time.
//
// installMocks returns a mutable ctx: ctx.counts tracks how many times each
// endpoint was hit (so a test can assert "no new fetch"), and ctx.overrides lets
// a test swap in a per-endpoint handler between polls to simulate an empty feed, a
// 502, or a delayed response. Handlers read ctx.overrides at REQUEST time, so a
// test can mutate it after navigation and have the next poll pick it up.
const fs = require("node:fs");
const path = require("node:path");
const fx = require("./fixtures/api");

const VENDOR = path.join(__dirname, "fixtures", "vendor");
const LEAFLET_JS = fs.readFileSync(path.join(VENDOR, "leaflet.js"));
const LEAFLET_CSS = fs.readFileSync(path.join(VENDOR, "leaflet.css"));

// A 1x1 transparent PNG. The basemap tile layer would otherwise reach out to
// tile.openstreetmap.org; the tiles are decorative and never asserted on, so we
// answer every tile with this stub to keep the suite fully offline (no console
// noise from aborted image loads either).
const TILE_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
  "base64",
);

// Fulfill a route with a JSON body (default 200). Exported so tests can build
// override handlers (empty feeds, error statuses) without repeating the boilerplate.
const json = (route, obj, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(obj) });

async function installMocks(page) {
  const ctx = { counts: {}, overrides: {}, leaks: [] };
  const bump = (key) => {
    ctx.counts[key] = (ctx.counts[key] || 0) + 1;
  };

  // Hermeticity guard. Registered FIRST so Playwright consults it LAST: only a
  // request that no specific route below handled reaches here. Same-origin
  // requests (the static frontend served by webServer) pass through; anything
  // else is an un-mocked escape to the network, which we record and abort so a
  // forgotten endpoint fails a test loudly instead of quietly hitting the wire.
  await page.route("**/*", (route) => {
    const url = route.request().url();
    if (url.startsWith("http://127.0.0.1:") || url.startsWith("http://localhost:")) {
      return route.continue();
    }
    ctx.leaks.push(url);
    return route.abort();
  });

  // Leaflet: vendored dist bytes, unchanged, so the SRI hashes in index.html match.
  await page.route("https://unpkg.com/leaflet@1.9.4/dist/leaflet.js", (route) =>
    route.fulfill({ contentType: "text/javascript; charset=utf-8", body: LEAFLET_JS }),
  );
  await page.route("https://unpkg.com/leaflet@1.9.4/dist/leaflet.css", (route) =>
    route.fulfill({ contentType: "text/css; charset=utf-8", body: LEAFLET_CSS }),
  );

  // Basemap tiles: stub so nothing reaches the network (see TILE_PNG). The app
  // uses the subdomain-less tile.openstreetmap.org host.
  await page.route("**tile.openstreetmap.org/**", (route) =>
    route.fulfill({ contentType: "image/png", body: TILE_PNG }),
  );

  // Register one /api endpoint: count the hit, defer to a test override if present,
  // else serve the default fixture. makeDefault gets the route so bus-route can
  // read the requested id out of the URL.
  const endpoint = (glob, key, makeDefault) =>
    page.route(glob, (route) => {
      bump(key);
      const override = ctx.overrides[key];
      if (override) return override(route, fx);
      return json(route, makeDefault(route));
    });

  await endpoint("**/api/buses", "buses", () => fx.buses());
  await endpoint("**/api/subways", "subways", () => fx.subways());
  await endpoint("**/api/railroads", "railroads", () => fx.railroads());
  await endpoint("**/api/subway-stops", "subwayStops", () => fx.subwayStops());
  await endpoint("**/api/subway-routes", "subwayRoutes", () => fx.subwayRoutes());
  await endpoint("**/api/railroad-stops", "railroadStops", () => fx.railroadStops());
  await endpoint("**/api/railroad-routes", "railroadRoutes", () => fx.railroadRoutes());
  await endpoint("**/api/airtrain", "airtrain", () => fx.airtrain());
  // "**/api/path" is end-anchored, so it cannot swallow path-stops/path-routes.
  await endpoint("**/api/path", "path", () => fx.path());
  await endpoint("**/api/path-stops", "pathStops", () => fx.pathStops());
  await endpoint("**/api/path-routes", "pathRoutes", () => fx.pathRoutes());
  await endpoint("**/api/path-arrivals/**", "pathArrivals", () => fx.pathArrivals());
  // "**/api/ferry" is end-anchored, so it cannot swallow ferry-stops/-routes/-arrivals.
  await endpoint("**/api/ferry", "ferry", () => fx.ferry());
  await endpoint("**/api/ferry-stops", "ferryStops", () => fx.ferryStops());
  await endpoint("**/api/ferry-routes", "ferryRoutes", () => fx.ferryRoutes());
  await endpoint("**/api/ferry-arrivals/**", "ferryArrivals", () => fx.ferryArrivals());
  await endpoint("**/api/alerts", "alerts", () => fx.alerts());
  await endpoint("**/api/subway-arrivals/**", "subwayArrivals", () => fx.subwayArrivals());
  await endpoint("**/api/railroad-arrivals/**", "railroadArrivals", () => fx.railroadArrivals());
  await endpoint("**/api/bus-route/**", "busRoute", (route) => {
    // Echo the requested route id so the banner label and the drawn line agree
    // with whichever bus was clicked (the geometry itself is the same stub).
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split("/").pop());
    return { ...fx.busRoute(), route: id };
  });

  return ctx;
}

// An override that returns an empty feed envelope stamped with a chosen fetched_at,
// used by the empty-feed grace test to walk the clock past FEED_STALE_AFTER_S.
const emptyFeedAt = (fetchedAt) => (route, fixtures) => json(route, fixtures.envelope([], fetchedAt));

module.exports = { installMocks, json, emptyFeedAt };
