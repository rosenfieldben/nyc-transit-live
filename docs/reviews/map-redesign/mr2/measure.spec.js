/* MR2 measurement harness. Temporary; deleted after the numbers are taken.
   Serves the REAL subway geometry and the REAL station list (captured from the MTA
   static archive through the backend's own loaders), then measures the three things
   MR2's scope asks about, A/B in ONE page so the samples share machine conditions:
   the cost of doubling the polylines, and the cost of a permanent tooltip per station. */
const { test, expect } = require("@playwright/test");
const { installMocks, json } = require("./mock");
const realRoutes = require("./fixtures/subway_routes_real.json");
const realStops = require("./fixtures/subway_stops_real.json");

const CITY = { center: [40.7295, -73.99], zoom: 13 };
const REPEATS = 4;

async function boot(page) {
  const ctx = await installMocks(page);
  ctx.overrides.subwayRoutes = (route) => json(route, realRoutes);
  ctx.overrides.subwayStops = (route) =>
    json(route, realStops.map((s) => ({ id: s.id, name: s.name, lat: s.lat, lon: s.lon, routes: s.routes })));
  await page.goto("/");
  await page.waitForFunction(() => typeof routeLinesLayer !== "undefined" && routeLinesLayer.getLayers().length > 0, null, { timeout: 30_000 });
  await page.waitForFunction(() => stationRegistry.filter((e) => e.kind === "subway").length > 400, null, { timeout: 30_000 });
  return ctx;
}

// One animated pan at City zoom, rAF deltas collected in the page.
const SAMPLE = async ({ center, zoom }) => {
  map.setView(center, zoom, { animate: false });
  await new Promise((r) => setTimeout(r, 300));
  const deltas = [];
  let last = performance.now();
  let running = true;
  const tick = () => {
    const now = performance.now();
    deltas.push(now - last);
    last = now;
    if (running) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  map.panBy([700, 240], { animate: true, duration: 1.2 });
  await new Promise((r) => setTimeout(r, 1500));
  running = false;
  return deltas.slice(2);
};

const stats = (all) => {
  const s = [...all].sort((a, b) => a - b);
  const at = (p) => s[Math.min(s.length - 1, Math.floor(s.length * p))];
  return { n: s.length, median: +at(0.5).toFixed(2), p90: +at(0.9).toFixed(2), p95: +at(0.95).toFixed(2), max: +s[s.length - 1].toFixed(2) };
};

async function sampleN(page) {
  const all = [];
  for (let i = 0; i < REPEATS; i++) all.push(...(await page.evaluate(SAMPLE, CITY)));
  return stats(all);
}

test("ZZ. geometry, labels and frame time, A/B in one page", async ({ page }) => {
  test.setTimeout(600_000);
  await page.setViewportSize({ width: 1280, height: 720 });
  await boot(page);

  const before = await page.evaluate(() => ({
    polylines: routeLinesLayer.getLayers().length,
    points: routeLinesLayer.getLayers().reduce((n, l) => n + l.getLatLngs().length, 0),
    subwayStations: stationLayer.getLayers().length,
    tooltipEls: document.querySelectorAll(".leaflet-tooltip").length,
  }));
  console.log("A_COUNTS", JSON.stringify(before));
  console.log("A_FRAMES", JSON.stringify(await sampleN(page)));

  // B: a casing under every line, exactly as MR2 will draw it.
  await page.evaluate(() => {
    const existing = routeLinesLayer.getLayers();
    const casings = existing.map((l) =>
      L.polyline(l.getLatLngs(), {
        color: "#f3f2f2", weight: 6.5, opacity: 0.9,
        lineCap: "round", lineJoin: "round", interactive: false,
        renderer: l.options.renderer,
      }),
    );
    routeLinesLayer.clearLayers();
    for (const c of casings) c.addTo(routeLinesLayer);
    for (const l of existing) l.addTo(routeLinesLayer);
  });
  const withCasing = await page.evaluate(() => ({
    polylines: routeLinesLayer.getLayers().length,
    points: routeLinesLayer.getLayers().reduce((n, l) => n + l.getLatLngs().length, 0),
  }));
  console.log("B_COUNTS", JSON.stringify(withCasing));
  console.log("B_FRAMES", JSON.stringify(await sampleN(page)));

  // C: and a permanent tooltip on every station, as the labels will be.
  const labels = await page.evaluate(() => {
    for (const entry of stationRegistry) {
      if (entry.kind !== "subway" || !entry.marker) continue;
      entry.marker.bindTooltip(entry.name, {
        permanent: true, direction: "right", offset: [7, 0], className: "stn-label", interactive: false,
      });
    }
    return { tooltipEls: document.querySelectorAll(".leaflet-tooltip").length };
  });
  console.log("C_COUNTS", JSON.stringify(labels));
  console.log("C_FRAMES", JSON.stringify(await sampleN(page)));

  // How many of those tooltips are actually inside the viewport at zoom 14 over Midtown.
  const inView = await page.evaluate(async () => {
    map.setView([40.7566, -73.9863], 14, { animate: false });
    await new Promise((r) => setTimeout(r, 400));
    const box = { w: window.innerWidth, h: window.innerHeight };
    let n = 0;
    for (const el of document.querySelectorAll(".leaflet-tooltip")) {
      const r = el.getBoundingClientRect();
      if (r.right > 0 && r.left < box.w && r.bottom > 0 && r.top < box.h) n += 1;
    }
    return { total: document.querySelectorAll(".leaflet-tooltip").length, inViewport: n };
  });
  console.log("C_INVIEW", JSON.stringify(inView));

  // D: the design's zoom gating in CSS. At City zoom only hubs show; the rest are
  // display:none, which is not painted, so this is the state a rider is actually in.
  await page.evaluate(() => {
    const style = document.createElement("style");
    style.textContent = `.stn-label { display: none } :root[data-zoom-test="hubs"] .stn-label.hub { display: block }`;
    document.head.append(style);
    document.documentElement.setAttribute("data-zoom-test", "hubs");
    let hubs = 0;
    for (const entry of stationRegistry) {
      if (entry.kind !== "subway" || !entry.marker) continue;
      const tip = entry.marker.getTooltip && entry.marker.getTooltip();
      const el = tip && tip.getElement && tip.getElement();
      if (el && (entry.routes ?? []).length >= 2) { el.classList.add("hub"); hubs += 1; }
    }
    return hubs;
  });
  const shownHubs = await page.evaluate(() => [...document.querySelectorAll(".leaflet-tooltip")].filter((el) => getComputedStyle(el).display !== "none").length);
  console.log("D_COUNTS", JSON.stringify({ bound: 496, painted: shownHubs }));
  console.log("D_FRAMES", JSON.stringify(await sampleN(page)));

  // E: the Names toggle off, every label display:none, all 496 still bound.
  await page.evaluate(() => document.documentElement.removeAttribute("data-zoom-test"));
  const paintedNone = await page.evaluate(() => [...document.querySelectorAll(".leaflet-tooltip")].filter((el) => getComputedStyle(el).display !== "none").length);
  console.log("E_COUNTS", JSON.stringify({ bound: 496, painted: paintedNone }));
  console.log("E_FRAMES", JSON.stringify(await sampleN(page)));

  // G: the viewport gate on top of the zoom gate. Every label stays BOUND; a class
  // decides which ones paint, recomputed from map bounds in lat/lng (no layout read).
  await page.evaluate(() => {
    const style = document.createElement("style");
    style.textContent = `.stn-label { display: none } .stn-label.inview { display: block }`;
    document.head.append(style);
    window.__gate = () => {
      const t0 = performance.now();
      const bounds = map.getBounds().pad(0.15);
      for (const entry of stationRegistry) {
        if (entry.kind !== "subway" || !entry.marker) continue;
        const tip = entry.marker.getTooltip && entry.marker.getTooltip();
        const el = tip && tip.getElement && tip.getElement();
        if (!el) continue;
        el.classList.toggle("inview", bounds.contains(entry.marker.getLatLng()));
      }
      return +(performance.now() - t0).toFixed(2);
    };
    map.on("moveend zoomend", window.__gate);
    window.__gate();
  });
  const gated = await page.evaluate(() => ({
    painted: [...document.querySelectorAll(".leaflet-tooltip")].filter((el) => getComputedStyle(el).display !== "none").length,
    gateMs: window.__gate(),
  }));
  console.log("G_COUNTS", JSON.stringify(gated));
  console.log("G_FRAMES", JSON.stringify(await sampleN(page)));
  const gatedZ14 = await page.evaluate(async () => {
    map.setView([40.7566, -73.9863], 14, { animate: false });
    await new Promise((r) => setTimeout(r, 300));
    return {
      painted: [...document.querySelectorAll(".leaflet-tooltip")].filter((el) => getComputedStyle(el).display !== "none").length,
      gateMs: window.__gate(),
    };
  });
  console.log("G_Z14", JSON.stringify(gatedZ14));

  // H: ten painted labels, to see whether the cost is per element or a step.
  await page.evaluate(() => {
    map.off("moveend zoomend", window.__gate);
    let n = 0;
    for (const el of document.querySelectorAll(".leaflet-tooltip")) {
      el.classList.toggle("inview", n < 10);
      n += 1;
    }
  });
  console.log("H_COUNTS", JSON.stringify(await page.evaluate(() => ({
    painted: [...document.querySelectorAll(".leaflet-tooltip")].filter((el) => getComputedStyle(el).display !== "none").length,
  }))));
  console.log("H_FRAMES", JSON.stringify(await sampleN(page)));

  const zoomCost = await page.evaluate(async () => {
    map.setView([40.7566, -73.9863], 13, { animate: false });
    await new Promise((r) => setTimeout(r, 300));
    const t0 = performance.now();
    map.setZoom(14, { animate: false });
    await new Promise((r) => requestAnimationFrame(r));
    return { zoomendMs: +(performance.now() - t0).toFixed(2) };
  });
  console.log("F_ZOOM", JSON.stringify(zoomCost));
  expect(before.polylines).toBe(35);
});
