// MR5 round 3: THE NODE TIER RUNS WITHOUT node_modules, AND THAT IS NOW A TEST.
//
// Run with: node --test "tests/*.test.js"  (from the repo root)
//
// WHAT WENT WRONG, because this file is a repair and should say so. Round 2 de-duplicated the mark
// normaliser `withoutMarks` into tests/e2e/popup.js and had frontend/boards.test.js require it. The
// reasoning was measured and the measurement was taken in the wrong place: popup.js requires
// @playwright/test, and a local checkout has that package installed, so `node --test` resolved it
// and all 394 tests passed. The CI job does not install it. It checks out the repo and runs
// `node --test` against the runner's preinstalled node with no `npm ci` at all, which is deliberate
// (the app is buildless and the unit tier needs nothing), so the require threw while loading
// boards.test.js. node counts a file that dies at load as ONE failing test, so thirteen tests
// stopped running and the job said "1 failed" rather than "13 missing". A green local run and a red
// CI run disagreed, and the local run was the one that was lying.
//
// WHAT THIS HOLDS. Every file the frontend-tests job loads, and everything those files reach through
// a require, may ask for nothing but node builtins. The seed list is not written out here: it is
// READ FROM THE WORKFLOW, so a glob added to the job is a glob this test covers.
//
// The rule itself was never a secret. package.json's own comment says it in one line, "the frontend
// unit tests still run with a bare `node --test`, no dependencies", and ci.yml says it again above the
// job. Both were true when they were written and neither could fail, which is the difference this file
// is for.
//
// IT ASKS NODE, NOT A REGEX, which is the one design decision in the file. A source scan for
// `require("...")` has to know which of those it finds are code: this closure is full of comments
// naming @playwright/test on purpose (marktoken.js, and the lines above), and full of regex literals
// carrying quotes, and a scraper that mis-lexed either could go quietly blind and pass over the very
// hole it was written for. So the check spawns a child node, hooks the module loader, stubs node:test
// so nothing RUNS, and requires each seed file for real. What comes back is the list of specifiers
// node was actually asked to resolve while loading the tier, which is the question CI asks.

const test = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync, readdirSync, existsSync, statSync, mkdtempSync, writeFileSync } = require("node:fs");
const { join, relative, resolve } = require("node:path");
const { tmpdir } = require("node:os");
const { execFileSync } = require("node:child_process");

const ROOT = resolve(__dirname, "..");

/* THE SEEDS COME FROM THE WORKFLOW, not from a list here. A literal would be a second statement of
   which files the tier runs, and the one that went stale would be this one. YAML comments open with
   `#`, so a commented-out job line cannot be mistaken for the live one. */
function testGlobsFromWorkflow(yml) {
  const line = yml
    .split("\n")
    .map((l) => l.trim())
    .find((l) => l.startsWith("- run: node --test"));
  assert.ok(line, "the frontend-tests job no longer runs `node --test` on a single line");
  const globs = [...line.matchAll(/"([^"]+)"/g)].map((m) => m[1]);
  assert.ok(globs.length > 0, `no quoted globs in the job's command: ${line}`);
  return globs;
}

/* The job's globs are simple by construction (`dir/*.test.js`), so an unrecognised shape THROWS
   rather than quietly matching nothing: a silently empty seed list is the failure this file exists
   to stop, and it would make every assertion below pass over air. */
function expandGlob(pattern) {
  const m = /^([^*]+)\/\*([^*/]*)$/.exec(pattern);
  assert.ok(m, `this test expands dir/*suffix patterns, and the job now uses: ${pattern}`);
  const [, dir, suffix] = m;
  const here = join(ROOT, dir);
  assert.ok(existsSync(here) && statSync(here).isDirectory(), `the job globs a missing directory: ${dir}`);
  return readdirSync(here)
    .filter((name) => name.endsWith(suffix))
    .sort()
    .map((name) => join(here, name));
}

/* THE CHILD. Written as a string because it must run in its own process: the hook has to be in place
   before the first seed loads, and this process has already loaded everything.

   node:test is replaced by a no-op so that requiring a test file REGISTERS nothing and runs nothing.
   Only the load phase matters here, and the load phase is exactly what broke. That also keeps the
   recursion shut: this very file is one of the seeds, and in the child its own test bodies never run.

   A bare specifier is RECORDED and answered with a stub rather than resolved, for two reasons: one
   run then reports every offender instead of stopping at the first, and the check does not itself
   depend on the package being installed, so it asks the same question on a bare checkout that CI
   asks. On CI the real require would throw, which is the failure being prevented. */
const CHILD = `
const Module = require("node:module");
const reached = [];
const stub = new Proxy(function () {}, {
  get: (t, k) => (typeof k === "symbol" ? undefined : stub),
  apply: () => stub,
  construct: () => stub,
});
const original = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === "node:test" || request === "test") {
    const noop = function () {};
    noop.describe = noop; noop.it = noop; noop.test = noop; noop.skip = noop; noop.todo = noop;
    noop.before = noop; noop.after = noop; noop.beforeEach = noop; noop.afterEach = noop;
    return noop;
  }
  const builtin = request.startsWith("node:") || Module.builtinModules.includes(request);
  if (!builtin && !request.startsWith(".") && !request.startsWith("/")) {
    reached.push({ from: (parent && parent.filename) || null, spec: request });
    return stub;
  }
  return original.apply(this, arguments);
};
for (const seed of process.argv.slice(1)) require(seed);
process.stdout.write("\\u0000RESULT" + JSON.stringify(reached));
`;

function packagesReachedByLoading(seeds) {
  const out = execFileSync(process.execPath, ["-e", CHILD, ...seeds], {
    cwd: ROOT,
    encoding: "utf8",
    timeout: 120000,
    maxBuffer: 32 * 1024 * 1024,
    stdio: ["ignore", "pipe", "pipe"],
  });
  const marker = out.lastIndexOf("\u0000RESULT");
  assert.notEqual(marker, -1, `the child produced no result line:\n${out}`);
  return JSON.parse(out.slice(marker + "\u0000RESULT".length));
}

test("MR5: the seeds are the workflow's own globs, and they name real files", () => {
  const globs = testGlobsFromWorkflow(readFileSync(join(ROOT, ".github", "workflows", "ci.yml"), "utf8"));
  const seeds = globs.flatMap(expandGlob).map((f) => relative(ROOT, f));
  // A wrong glob would make the check below pass over nothing, so the count is pinned loosely and
  // the file the break actually lived in is pinned by name.
  assert.ok(seeds.length >= 10, `only ${seeds.length} test files matched ${JSON.stringify(globs)}`);
  assert.ok(seeds.includes(join("frontend", "boards.test.js")), seeds.join(" "));
  assert.ok(seeds.includes(join("tests", "nodetier.test.js")), seeds.join(" "));
});

test("MR5: the loader hook sees a package a seed reaches through a local file", () => {
  /* THE CHECK IS TESTED RATHER THAN TRUSTED, on the exact shape that got past round 2: the seed
     requires nothing but a local file, and the PACKAGE is one hop away. Something that only read its
     seeds would call this clean. @playwright/test is named because it is installed here, so the
     child resolves it and the hook's report is the only thing that can fail. */
  const dir = mkdtempSync(join(tmpdir(), "nodetier-"));
  writeFileSync(join(dir, "seed.js"), 'require("node:test");\nmodule.exports = require("./middle.js");\n');
  writeFileSync(join(dir, "middle.js"), 'const { expect } = require("@playwright/test");\nmodule.exports = { expect };\n');
  const reached = packagesReachedByLoading([join(dir, "seed.js")]);
  assert.equal(reached.length, 1);
  assert.equal(reached[0].spec, "@playwright/test");
  assert.equal(reached[0].from, join(dir, "middle.js"));
  // And a clean seed reports nothing, so the check is not simply always red.
  writeFileSync(join(dir, "clean.js"), 'module.exports = require("node:path").sep;\n');
  assert.deepEqual(packagesReachedByLoading([join(dir, "clean.js")]), []);
});

test("MR5: nothing the node unit tier loads asks for a package from node_modules", () => {
  /* THE ONE THAT WOULD HAVE CAUGHT IT. The frontend-tests job installs nothing, so a package
     anywhere in this closure is not a slow test or a warning: it is a file that throws at load and
     takes every test in it with it, reported as a single failure. */
  const globs = testGlobsFromWorkflow(readFileSync(join(ROOT, ".github", "workflows", "ci.yml"), "utf8"));
  const reached = packagesReachedByLoading(globs.flatMap(expandGlob));
  assert.deepEqual(
    reached,
    [],
    `these run without node_modules and may require only node builtins:\n${reached
      .map((r) => `  ${r.from} requires ${r.spec}`)
      .join("\n")}`,
  );
});
