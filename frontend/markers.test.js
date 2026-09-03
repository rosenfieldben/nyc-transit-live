// A2: the marker factory is a SEAM, and this test is what makes it one.
//
// Run with: node --test "frontend/*.test.js"  (from the repo root)
//
// These are source-text assertions rather than behavioural ones, deliberately. The
// property being defended is not "labeledMarker works" (the Playwright specs cover
// that against a real Leaflet); it is "there is no other way to make a marker". That
// is a property of the source, not of any single execution, and the failure it guards
// against is a future system file (Amtrak, NJ Transit, a second ferry operator)
// reaching for L.marker directly and quietly reintroducing several hundred tabbable,
// nameless buttons ahead of every control on the page.
//
// Loading shared.js in node is not an option: it runs Leaflet at import time and
// builds the whole map. Reading it is enough, because what is being asserted is a
// grep-level fact about the codebase.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const SYSTEMS_DIR = path.join(__dirname, "systems");
const SHARED = path.join(SYSTEMS_DIR, "shared.js");

function systemFiles() {
  return fs
    .readdirSync(SYSTEMS_DIR)
    .filter((name) => name.endsWith(".js"))
    .map((name) => path.join(SYSTEMS_DIR, name));
}

test("A2: the factory always passes keyboard:false, and puts the role back", () => {
  const source = fs.readFileSync(SHARED, "utf8");
  const factory = source.slice(source.indexOf("function labeledMarker("));
  assert.ok(factory.startsWith("function labeledMarker("), "labeledMarker must exist in systems/shared.js");
  const body = factory.slice(0, factory.indexOf("\n}\n") + 3);

  // The tab stop is off. This is the whole tab-order policy, in one place.
  assert.match(body, /keyboard:\s*false/, "labeledMarker must construct every marker with keyboard:false");

  // And the role is written back, because Leaflet's `keyboard` option gates the tab
  // stop and role="button" TOGETHER: turning off the stop strips the role with it, and
  // a bare div with an aria-label is not announced as anything.
  assert.match(source, /setAttribute\("role",\s*"img"\)/, "the role removed with the tab stop must be put back");
  assert.match(source, /setAttribute\("aria-label"/, "markers must carry an accessible name");
});

test("A2: no system file constructs a marker outside the factory", () => {
  // THE UNCOPYABILITY TEST. One L.marker call in the entire frontend, and it is the
  // one inside labeledMarker. A new system that writes its own gets this failure with
  // the file named, which is the moment to point it at the factory.
  const offenders = [];
  for (const file of systemFiles()) {
    const source = fs.readFileSync(file, "utf8");
    source.split("\n").forEach((line, i) => {
      if (!/\bL\.marker\s*\(/.test(line)) return;
      // The factory's own call is the single sanctioned one.
      if (file === SHARED && /const marker = L\.marker\(latlng/.test(line)) return;
      offenders.push(`${path.basename(file)}:${i + 1}: ${line.trim()}`);
    });
  }
  assert.deepEqual(
    offenders,
    [],
    "every marker must be built by labeledMarker (systems/shared.js), which owns keyboard:false and the aria name",
  );
});

test("A2: every system that builds markers gives them a name", () => {
  // A factory that accepts an empty name would be a factory that lets a system opt
  // out of being named, which is the same hole one step further in. Each file that
  // calls labeledMarker must pass one of the name builders from helpers.js.
  const builders = [
    "busName",
    "subwayTrainName",
    "railroadTrainName",
    "pathTrainName",
    "ferryBoatName",
    "airtrainStationName",
    "njtTrainName",
    "njtStationName",
  ];
  for (const file of systemFiles()) {
    const source = fs.readFileSync(file, "utf8");
    if (!/labeledMarker\s*\(/.test(source) || file === SHARED) continue;
    assert.ok(
      builders.some((name) => source.includes(`${name}(`)),
      `${path.basename(file)} builds markers but names them with none of the helpers.js builders`,
    );
  }
});

test("A2: a reused marker is relabeled, so a name cannot describe a stale vehicle", () => {
  // Every system whose markers survive between polls must call setMarkerName on the
  // reuse path, not only at creation. AirTrain is exempt and says so in its own file:
  // it is static, built once, and has no apply path at all.
  const live = ["buses.js", "subway.js", "railroad.js", "path.js", "ferry.js", "njt.js"];
  for (const name of live) {
    const source = fs.readFileSync(path.join(SYSTEMS_DIR, name), "utf8");
    assert.match(
      source,
      /setMarkerName\(/,
      `${name} reuses markers across polls, so it must refresh the label rather than name once at creation`,
    );
  }
});
