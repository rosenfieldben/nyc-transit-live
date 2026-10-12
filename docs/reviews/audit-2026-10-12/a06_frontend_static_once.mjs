#!/usr/bin/env node
/*
 * A06 (Medium): "Static schedules never refresh after successful startup", the FRONTEND sentence.
 * Reproduction and regression check for finding A06 of the 2026-10-12 audit, over main at 1e3e73d.
 *
 * THE AUDIT'S CLAIM, quoted from the report:
 *
 *     "Failure. Static warmups return when ready and are started once. File-age policies are
 *      checked during loading, not periodically in a long-running process. New schedules, trip
 *      IDs, routes and service-calendar expiry can therefore outlive the data loaded into
 *      memory. Frontend static loaders also stop after their first usable result."
 *
 *     "Repair. [...] Keep last-known-good data on failure and ensure browsers discover changed
 *      static generations. [...]"
 *
 *     Cited code (where to LOOK; nothing below asserts a line, a name or a comment), all of it
 *     backend; the frontend sentence carries no code link of its own:
 *       https://github.com/rosenfieldben/nyc-transit-live/blob/1e3e73d11cedd1a12cbdb70c69b05901d66ba4b2/backend/main.py#L330
 *       https://github.com/rosenfieldben/nyc-transit-live/blob/1e3e73d11cedd1a12cbdb70c69b05901d66ba4b2/backend/warmups.py#L193
 *       https://github.com/rosenfieldben/nyc-transit-live/blob/1e3e73d11cedd1a12cbdb70c69b05901d66ba4b2/backend/warmups.py#L269
 *       https://github.com/rosenfieldben/nyc-transit-live/blob/1e3e73d11cedd1a12cbdb70c69b05901d66ba4b2/backend/warmups.py#L462
 *
 * WHAT THIS SCRIPT MEASURES: THE LAST SENTENCE ONLY. The backend half restates the 2026-09-05
 * F07, recorded VERIFIED, and docs/reviews/audit-2026-09-05/f07_static_never_refreshes.py
 * already measures it, so it is not measured again here.
 *
 * WHAT IS PRODUCTION CODE HERE. Every first-party script frontend/index.html loads, read from
 * its own <script> tags and run unmodified, in that order, in one node:vm context (helpers.js,
 * systems/shared.js, the seven systems/*.js, stations.js, map.js at 1e3e73d). Nothing calls a
 * loader by name: the page's own boot starts the static loaders, its own retry helper paces
 * them, and its own timers and animation frames run on the fake clock below. The script answers
 * requests, moves the clock, delivers the events a browser would, and counts.
 *
 * WHAT IS INJECTED, AND NAMED AS SUCH.
 *   - The transport. fetch is a stub that classifies a request by its path (the page origin and
 *     any query string ignored). The eleven static endpoints answer from the committed fixture
 *     module tests/e2e/fixtures/api.js, the one tests/e2e/mock.js serves the e2e suite from.
 *     Each one's first three requests are refused with the backend's two not-yet states (503,
 *     503, then an empty 200), and later ones get the fixture (v1). Every latitude carries a tag
 *     of a few millimetres (a multiple of 1e-8 degrees, distinct per endpoint and version, below
 *     the fixtures' 1e-6 precision), so a coordinate that reaches the map names its endpoint and
 *     its version exactly; a setup check proves no two tags collide. The seven live endpoints
 *     answer their fixtures on every poll. Any other same-origin /api/ path never settles and
 *     fails a check; any other URL is refused and fails a check, so nothing naming njtransit or
 *     raildata can be asked for unnoticed.
 *   - The clock. Date, setTimeout, setInterval and requestAnimationFrame (one frame per
 *     simulated minute) run on a fake clock from the fixture module's FROZEN_MS
 *     (2026-07-02T12:00:00Z, 08:00 in New York), advanced 25 hours, each due timer in order.
 *   - Leaflet and the DOM are one permissive stand-in: every member exists and every call
 *     answers another stand-in. That is coarser than f14's named stubs on purpose, because the
 *     claim is about requests and adoption, not paint. Its one observation: every number handed
 *     to any Leaflet call is looked up among the tagged latitudes, which is what "reached the
 *     map" means below. window forwards to the page's own globals, as a browser's does.
 *
 * WHAT A REPAIR COULD LISTEN TO, AND IS GIVEN. The claim checks count requests and drawing, not
 * a mechanism, so a repair turns this red whatever its trigger, provided the trigger happens
 * here. So every signal a real backend or browser gives a page, and that a plausible repair
 * could key a re-read on, is given:
 *   - the backend's clocks: every *_at, *_time and *timestamp number in a live payload, and the
 *     Date header, move with the fake clock, so the server crosses a New York midnight and a
 *     04:00 service-day boundary as the browser does;
 *   - the backend's cache policy, as backend/cache.py sets it: a ready static payload carries
 *     Cache-Control "public, max-age=3600", an empty one "no-cache", a live one "no-store";
 *   - a backend restart into a changed publication (F07's half: today the backend takes a new
 *     one only by restarting): every endpoint answers 503 from t = 60 s, five minutes down,
 *     and from t = 360 s the static endpoints serve v2 (every latitude 0.05 degrees further
 *     north, plus an ADDED entry per entry, its id or route suffixed). From then on every live
 *     payload announces it (each scalar under a key naming a static generation, a version, a
 *     publication or a boot changes, and so does any response header so named) and each feed's
 *     first row runs on a route and a stop only v2 has;
 *   - the tab: hidden and offline at 1 h (visibilitychange, blur, freeze, pagehide, offline),
 *     back at 13 h and at every hour after (online, resume, pageshow restored from cache,
 *     visibilitychange, focus), delivered to every listener the page put on document, window
 *     or the global, by addEventListener or by an on<event> property;
 *   - navigation: location (bare, on window and on document) records a reload, an assign, a
 *     replace or an assignment, history.go(0) is one, and so is a <meta http-equiv="refresh">
 *     in index.html when its time comes. The page then boots again from index.html in a fresh
 *     context against the same transport and clock, the old page's timers dropped, as a
 *     browser's would.
 *
 * WHAT TURNS IT RED: a static request after a loader's first usable answer, or a v2 coordinate
 * reaching the map, from either page; and, as a setup failure, any page error or console
 * warning (an unmodelled global must fail loudly rather than swallow a re-read). Measured
 * against scratch repairs that re-read on a timer (bare or on window), in the retry helper,
 * after the static max-age, on tab return (by listener, by property, after an age gate, after
 * a long absence, back online, restored from cache), on an announced generation (body key,
 * header, reload, cache-busting URL), at a service-day boundary (browser clock, server clock,
 * Date header), on an animation frame, on a live row naming an unknown route, after a backend
 * restart, and by reloading the page (history.go(0), a meta refresh). LIMITS, STATED RATHER
 * THAN GUESSED AT: a re-read slower than about daily (the window is 25 h); one fired only by a
 * rider's action on the map, panel or keys (none is replayed), or by a service worker (none
 * runs); one keyed on an announcement under a name none of the words above matches, or on a
 * field the committed live fixtures do not carry (the subway's per-group route coverage, for
 * one). Timers and frames run at full rate while the tab is hidden; a browser throttles both,
 * so a repair tied to them fires sooner here, never later.
 *
 * HERMETIC: no socket (fetch is the stub above, and the vm has no require and no process), no
 * wall clock in any measurement, nothing written. About 6 s.
 *
 * RUN IT (from the repository root):
 *     node docs/reviews/audit-2026-10-12/a06_frontend_static_once.mjs
 * Exits 0 while the record matches the code, 1 when it does not.
 *
 * RECORDED DISPOSITION: VERIFIED for the frontend sentence (the row's backend half stays a
 * restatement of F07). All eleven loaders retried through their three refusals, took one usable
 * answer each, and then made no request in 25 simulated hours while their endpoints served a
 * changed publication, none of which reached the map.
 */

import vm from "node:vm";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const FIX = createRequire(import.meta.url)(path.join(REPO, "tests/e2e/fixtures/api.js"));

// Endpoint -> the fixture accessor tests/e2e/mock.js answers it from.
const STATIC = {
  "/api/subway-routes": "subwayRoutes", "/api/subway-stops": "subwayStops", "/api/railroad-routes": "railroadRoutes",
  "/api/railroad-stops": "railroadStops", "/api/airtrain": "airtrain", "/api/path-routes": "pathRoutes",
  "/api/path-stops": "pathStops", "/api/ferry-routes": "ferryRoutes", "/api/ferry-stops": "ferryStops",
  "/api/njt-routes": "njtRoutes", "/api/njt-stops": "njtStops",
};
const LIVE = {
  "/api/buses": "buses", "/api/subways": "subways", "/api/railroads": "railroads", "/api/path": "path",
  "/api/ferry": "ferry", "/api/njt-trains": "njt", "/api/alerts": "alerts",
};
const WARMING = [503, { detail: "warming" }];
const REFUSALS = [WARMING, WARMING, [200, []]];
const ORIGIN = "http://localhost";
const [RESTART_MS, CHANGE_MS] = [60_000, 360_000]; // the backend is down between these and serves v2 after
const [HOUR_MS, FRAME_MS] = [3600_000, 60_000];
const END_MS = 25 * HOUR_MS; // a once-a-day re-read, or a day-old age gate, lands inside the window
const HIDE = ["visibilitychange", "blur", "freeze", "pagehide", "offline"];
const SHOW = ["online", "resume", "pageshow", "visibilitychange", "focus"];
const ANNOUNCED = /static|generation|version|publication|boot/i;
let now = 0; // fake ms since FROZEN_MS

/* ==== OBSERVATION: tagged latitudes, and the watch on every Leaflet call ==== */
const tags = new Map(); // tagged latitude -> "endpoint v1" | "endpoint v2"
const collisions = [];
const drawn = new Map(); // "endpoint version" -> tagged latitudes handed to Leaflet
function watch(v, depth = 0) {
  if (typeof v === "number" && tags.has(v)) drawn.set(tags.get(v), (drawn.get(tags.get(v)) ?? 0) + 1);
  else if (depth < 6 && v && typeof v === "object") for (const x of Object.values(v)) watch(x, depth + 1);
}

/* ==== ENVIRONMENT 1: what a real backend also does (see the docstring) ==== */
const suffix = (x) => `${x}-a06`;
const added = (e) => ({ ...e, ...(e.id != null && { id: suffix(e.id) }),
  ...(e.route != null && { route: suffix(e.route) }) });
const grow = (pub) => (Array.isArray(pub) ? [...pub, ...pub.map(added)]
  : Object.fromEntries(Object.entries(pub).map(([k, v]) => [k, Array.isArray(v) ? grow(v) : v])));
const serve = (v, ver, first = false) => (Array.isArray(v) ? v.map((x, i) => serve(x, ver, i === 0))
  : !v || typeof v !== "object" ? v : Object.fromEntries(Object.entries(v).map(([k, x]) => [k,
    typeof x === "number" && /(_at|_time|timestamp)$/.test(k) ? x + Math.floor(now / 1000)
      : ver === "v2" && ((ANNOUNCED.test(k) && x != null && typeof x !== "object")
        || (first && /^(route|stop)_id$/.test(k) && typeof x === "string")) ? suffix(x) : serve(x, ver)])));

/* ==== HARNESS 1: the transport ==== */
const isPair = (v) => Array.isArray(v) && v.length === 2 && v.every((x) => typeof x === "number");
const mapLats = (v, f) => (isPair(v) ? [f(v[0]), v[1]] : Array.isArray(v) ? v.map((x) => mapLats(x, f))
  : v && typeof v === "object" ? Object.fromEntries(Object.entries(v).map(([k, x]) =>
    [k, k === "lat" && typeof x === "number" ? f(x) : mapLats(x, f)])) : v);
const PUB = {};
Object.keys(STATIC).forEach((url, i) => [["v1", 0, 1], ["v2", 0.05, 2]].forEach(([ver, north, code]) => {
  PUB[`${url} ${ver}`] = mapLats((ver === "v2" ? grow : (x) => x)(FIX[STATIC[url]]()), (lat) => {
    const tagged = lat + north + (2 * i + code) * 1e-8;
    if (tags.has(tagged) && tags.get(tagged) !== `${url} ${ver}`) collisions.push(tagged);
    tags.set(tagged, `${url} ${ver}`);
    return tagged;
  });
}));
const requests = new Map(); // endpoint path -> [{ t, answer }]
const strays = [];
function fetchStub(input) {
  const url = String(input?.url ?? input).replace(ORIGIN, "");
  if (!url.startsWith("/api/")) { strays.push(url); return Promise.reject(new TypeError(`refused: ${url}`)); }
  const u = url.split("?")[0];
  const log = requests.get(u) ?? requests.set(u, []).get(u);
  const [ver, down] = [now < CHANGE_MS ? "v1" : "v2", now >= RESTART_MS && now < CHANGE_MS];
  let [status, body, answer] = [200, undefined, "unclassified"];
  if (STATIC[u] && (down || log.length < REFUSALS.length)) {
    [status, body, answer] = [...(down ? WARMING : REFUSALS[log.length]), "refused"];
  } else if (STATIC[u]) [body, answer] = [structuredClone(PUB[`${u} ${ver}`]), ver];
  else if (LIVE[u]) [status, body, answer] = down ? [...WARMING, "down"] : [200, serve(FIX[LIVE[u]](), ver), "live"];
  log.push({ t: now, answer });
  if (body === undefined) return new Promise(() => {}); // unclassified: never settles
  // The backend's own cache policy (ready static: max-age 3600; empty: no-cache; live: no-store), its clock.
  const head = { date: new Date(FIX.FROZEN_MS + now).toUTCString(), "cache-control": status !== 200 ? null
    : !STATIC[u] ? "no-store" : answer === "refused" ? "no-cache" : "public, max-age=3600" };
  const headers = { get: (name) => head[String(name).toLowerCase()]
    ?? (ANNOUNCED.test(name) ? `${name} ${ver}` : null) };
  return Promise.resolve({ ok: status === 200, status, headers, json: async () => body });
}

/* ==== HARNESS 2: Leaflet and the DOM, one permissive stand-in ==== */
function standIn(onCall, onSet) {
  const own = new Map();
  return new Proxy(function () {}, {
    get: (_t, key) => (key === "then" ? undefined : key === Symbol.toPrimitive ? () => ""
      : key === Symbol.iterator ? function* () {} : key === "length" ? 0
        : own.has(key) ? own.get(key) : own.set(key, standIn(onCall)).get(key)),
    set: (_t, key, value) => (onSet?.(key, value), own.set(key, value) !== null),
    apply: (_t, _self, args) => { onCall?.(args); return standIn(onCall); },
    construct: (_t, args) => { onCall?.(args); return standIn(onCall); },
  });
}

/* ==== ENVIRONMENT 2: what a real browser also does (see the docstring) ==== */
let [listeners, navigations, navigated, visible, calls] = [[], [], false, true, 0];
const navigate = (how) => { navigations.push(`${how} at ${now / 1000} s`); navigated = true; };
const listen = (type, fn) => listeners.push([type, fn]); // addEventListener on document, window or the global
const loc = new Proxy({ origin: ORIGIN, href: `${ORIGIN}/`, host: "localhost", pathname: "/", search: "", hash: "",
  toString: () => `${ORIGIN}/`, reload: () => navigate("reload"), assign: (u) => navigate(`assign ${u}`),
  replace: (u) => navigate(`replace ${u}`) }, { set: (t, key, value) => (key === "hash" ? (t.hash = value)
  : navigate(`${String(key)} = ${value}`), true) });
const onSet = (key, value) => (key === "location" ? value !== loc && navigate(`location = ${value}`)
  : typeof value === "function" && /^on/.test(String(key)) && listeners.push([String(key).slice(2), value]));
function dispatch(types) {
  Object.assign(doc, { hidden: !visible, visibilityState: visible ? "visible" : "hidden" });
  ctx.navigator.onLine = visible;
  for (const type of types) {
    const bare = typeof ctx[`on${type}`] === "function" ? [ctx[`on${type}`]] : [];
    for (const fn of [...listeners.filter(([t]) => t === type).map(([, f]) => f), ...bare]) {
      calls += 1;
      try { fn.call(doc, { type, target: doc, persisted: true, preventDefault() {} }); }
      catch (err) { note(`${type}: ${err}`); }
    }
  }
}

/* ==== HARNESS 3: the clock ==== */
const timers = new Map();
let [nextId, page] = [0, 0]; // page: the current boot; a navigation drops the old page's timers
const schedule = (every, owner) => (fn, ms = 0) =>
  (timers.set(++nextId, { fn, page: owner, due: now + Math.max(0, ms), every: every ? Math.max(1, ms) : 0 }), nextId);
const drop = (id) => timers.delete(id);
const pageErrors = [];
const note = (...a) => pageErrors.push(a.join(" "));
process.on("unhandledRejection", (err) => note(`${now} ms: unhandled rejection: ${err && err.message}`));
async function settle() {
  await new Promise((resolve) => setImmediate(resolve)); // drain every promise chain the last step started
  if (navigated && page <= 10) { boot(); await new Promise((resolve) => setImmediate(resolve)); }
}
async function runUntil(end) {
  for (;;) {
    const [id, tm] = [...timers].reduce((a, b) => (!a || b[1].due < a[1].due ? b : a), null) ?? [];
    if (!tm || tm.due > end) break;
    now = tm.due;
    if (tm.every && tm.page === page) tm.due += tm.every; else timers.delete(id);
    if (tm.page !== page) continue;
    try { tm.fn(); } catch (err) { note(`${now} ms: ${err && err.message}`); }
    await settle();
  }
  now = end;
}

/* ==== HARNESS 4: load the page (again after a navigation), run it 25 h, the tab away from 1 h to 13 h ==== */
const html = fs.readFileSync(path.join(REPO, "frontend/index.html"), "utf8");
const scripts = [...html.matchAll(/<script\b[^>]*\bsrc="([^"]+)"/g)].map((m) => m[1])
  .filter((s) => !s.startsWith("vendor/"));
const metaRefresh = [...html.matchAll(/<meta\b[^>]*>/gi)].map((m) => m[0])
  .find((m) => /http-equiv=["']?refresh/i.test(m));
const refreshS = Number(/content=["']?\s*(\d+)/i.exec(metaRefresh ?? "")?.[1] ?? NaN); // a browser reloads after it
let doc, ctx, loaded, frames = 0;
function boot() {
  [page, listeners, loaded, navigated] = [page + 1, [], [], false];
  const [mine, timeout, interval] = [page, schedule(false, page), schedule(true, page)]; // this page's, and only its
  doc = Object.assign(standIn(null, onSet), { location: loc, addEventListener: listen });
  const here = ctx = vm.createContext(Object.defineProperty({
    document: doc, navigator: Object.assign(standIn(), { onLine: true }), L: standIn(watch),
    history: Object.assign(standIn(), { go: (n = 0) => !n && navigate("history.go(0)") }),
    fetch: (input) => (mine === page ? fetchStub(input) : new Promise(() => {})), // a page navigated away asks nothing
    setTimeout: timeout, setInterval: interval, clearTimeout: drop, clearInterval: drop, cancelAnimationFrame: drop,
    requestAnimationFrame: (fn) => timeout(() => { frames += 1; fn(now); }, FRAME_MS),
    addEventListener: listen, AbortSignal: { timeout: () => ({}) }, URL, URLSearchParams,
    performance: { now: () => now }, console: { log() {}, info() {}, debug() {}, warn: note, error: note },
    __clock: () => FIX.FROZEN_MS + now,
  }, "location", { get: () => loc, set: (u) => navigate(`location = ${u}`) }));
  // window is the global, as in a browser: what the page reads from it is what its scripts see bare.
  here.window = here.self = new Proxy(standIn(null, onSet),
    { get: (t, k) => (typeof k === "string" && k in here ? here[k] : t[k]) });
  vm.runInContext("Date = class extends Date { constructor(...a) { super(...(a.length ? a : [__clock()])); }"
    + " static now() { return __clock(); } };", ctx);
  dispatch([]);
  if (refreshS >= 0) timeout(() => navigate(`meta refresh ${refreshS} s`), refreshS * 1000);
  for (const rel of scripts) {
    try { vm.runInContext(fs.readFileSync(path.join(REPO, "frontend", rel), "utf8"), ctx, { filename: rel }); }
    catch (err) { note(`load ${rel}: ${err && err.message}`); break; }
    loaded.push(rel);
  }
}
boot();
await settle();
let returns = 0;
for (let hour = 1; hour * HOUR_MS <= END_MS; hour += 1) {
  await runUntil(hour * HOUR_MS);
  if (hour > 1 && hour < 13) continue; // the tab is away from 1 h to 13 h
  [visible, returns] = [hour >= 13, returns + (hour >= 13)];
  dispatch(visible ? SHOW : HIDE);
  await settle();
}

/* ==== MEASUREMENT: what the page did, and the checks ==== */
const results = [];
const check = (name, ok, measured, expected) => {
  results.push(ok);
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}\n        measured: ${measured}\n        expected: ${expected}`);
};
const rows = Object.keys(STATIC).map((url) => {
  const log = requests.get(url) ?? [];
  const first = log.findIndex((r) => r.answer !== "refused");
  return {
    url, refusedFirst: first === -1 ? log.length : first, usable: log.filter((r) => r.answer !== "refused").length,
    after: first === -1 ? 0 : log.length - first - 1, times: log.map((r) => r.t / 1000),
    v1: drawn.get(`${url} v1`) ?? 0, v2: drawn.get(`${url} v2`) ?? 0,
  };
});
const live = Object.keys(LIVE).map((u) => [u, requests.get(u) ?? []]);
const unclassified = [...requests.keys()].filter((u) => !STATIC[u] && !LIVE[u]);
const across = (pred) => {
  const bad = rows.filter((r) => !pred(r)).map((r) => r.url);
  return `${rows.length - bad.length} of ${rows.length}${bad.length ? ` (not: ${bad.join(", ")})` : ""}`;
};
const sum = (key) => rows.reduce((n, r) => n + r[key], 0);
const shown = (t) => JSON.stringify(t.length > 6 ? [...t.slice(0, 5), `+${t.length - 5} more`] : t);
const down = live.reduce((n, [, log]) => n + log.filter((r) => r.answer === "down").length, 0);

console.log("A06 (Medium): frontend static loaders stop after their first usable result");
console.log(`  page scripts run in one node:vm context, in index.html order: ${loaded.join(", ")}`);
console.log(`  clock: 25 h from ${new Date(FIX.FROZEN_MS).toISOString()} (08:00 New York); backend down from`
  + ` t = ${RESTART_MS / 1000} s, back with the changed publication at t = ${CHANGE_MS / 1000} s`);
console.log("\n  static endpoint        request times (s)                 usable  after  v1 drawn  v2 drawn");
for (const r of rows) {
  console.log(`  ${r.url.padEnd(22)} ${shown(r.times).padEnd(33)} ${String(r.usable).padStart(6)}`
    + ` ${String(r.after).padStart(6)} ${String(r.v1).padStart(9)} ${String(r.v2).padStart(9)}`);
}
console.log("  (refused at 0, 1 and 3 s; a routes loader asking again at 3 s is its one cache-bypassing"
  + " re-read after the empty 200)");
console.log("\n  live endpoint      polls  last poll (s)");
for (const [u, log] of live) {
  console.log(`  ${u.padEnd(18)} ${String(log.length).padStart(5)}  ${log.length ? log.at(-1).t / 1000 : "never"}`);
}
console.log(`  live polls answered 503 during the restart: ${down}; animation frames run: ${frames}`);
console.log(`  tab events delivered to page listeners (hidden at 1 h, back at 13 h to 25 h hourly): ${calls}`);
console.log(`  navigations: ${navigations.length} ${JSON.stringify(navigations.slice(0, 3))}; page boots: ${page}`);

console.log("\nSETUP");
check("every first-party script index.html loads ran", loaded.length === scripts.length && scripts.length > 0,
  `${loaded.length} of ${scripts.length}`, "all of them (11 at 1e3e73d)");
check("the page raised no error and logged no warning (an unmodelled global must not hide a re-read)",
  pageErrors.length === 0, `${pageErrors.length}${pageErrors.length ? `, first: ${pageErrors[0]}` : ""}`, "0");
check("every tagged latitude names one endpoint and one version", collisions.length === 0 && tags.size > 0,
  `${tags.size} tagged latitudes, ${collisions.length} collisions`, "0 collisions");
check("no request left the page's origin (nothing naming njtransit or raildata)", strays.length === 0,
  `${strays.length} ${JSON.stringify(strays.slice(0, 3))}`, "0");
check("the page asked only for endpoints this script classifies (a new one needs this script re-read)",
  unclassified.length === 0, `${unclassified.length} ${JSON.stringify(unclassified.slice(0, 5))}`, "0");
check("the page stayed alive for the whole window, through the restart",
  live.every(([, log]) => log.length > 24 && log.at(-1).t >= END_MS - 60_000) && down > 0,
  `${live.map(([u, log]) => `${u.slice(5)} ${log.length}`).join(", ")}; ${down} answered 503`,
  "more than 24 polls of each live endpoint, the last in the final minute, some during the restart");

console.log("\nCONTROL: before a usable answer the loaders do retry, and what they take reaches the map");
check("each loader retried through all three refusals before its first usable answer",
  rows.every((r) => r.refusedFirst === 3 && r.usable >= 1), across((r) => r.refusedFirst === 3 && r.usable >= 1),
  "11 of 11 (spec: before a usable result the loader retries)");
check("each loader's first usable answer (v1) reached the map", rows.every((r) => r.v1 > 0),
  across((r) => r.v1 > 0), "11 of 11");

console.log('\nTHE CLAIM: "Frontend static loaders also stop after their first usable result."');
check("each loader took exactly one usable answer in 25 h", rows.every((r) => r.usable === 1),
  `${across((r) => r.usable === 1)}, ${sum("usable")} usable answers in all`,
  "11 of 11, 11 in all (audit: they stop after their first usable result)");
check("no static request after the first usable answer, through everything a repair could listen to",
  rows.every((r) => r.after === 0), `${sum("after")} requests; ${across((r) => r.after === 0)} silent`,
  "0 (spec: never re-fetched)");
check("the changed publication (v2) never reached the map", rows.every((r) => r.v2 === 0),
  `${sum("v2")} v2 latitudes drawn; ${across((r) => r.v2 === 0)} clean`, "0");

if (results.every(Boolean)) {
  const polls = live.find(([u]) => u === "/api/subways")[1].length;
  console.log(`\nDISPOSITION: VERIFIED  All 11 frontend static loaders retried through 3 refusals, took 1 usable`
    + ` answer each, then made 0 requests in 25 simulated hours while the backend restarted into a changed,`
    + ` announced publication at t = ${CHANGE_MS / 1000} s, the tab came back ${returns} times and the page polled`
    + ` /api/subways ${polls} times; 0 of the changed coordinates reached the map.`);
  process.exit(0);
}
console.log("\nDISPOSITION: NOT AS RECORDED, see the failed checks above");
process.exit(1);
