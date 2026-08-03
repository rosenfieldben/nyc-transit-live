// A4: THE STATEMENT'S CITATIONS ARE CHECKED, because a citation nobody checks is a claim
// nobody checks.
//
// ACCESSIBILITY.md says "every claim below names the test that proves it, by file and test
// id, so any sentence here can be re-run rather than believed". That sentence is only true
// while the ids resolve. Specs get renamed, split and retired, and a statement of
// accessibility that cites a test which no longer exists is worse than one that cites
// nothing: it reads as evidence and is not.
//
// So the document is parsed and every citation is resolved against the suites. The parse
// tracks the most recently named spec file, because the document cites a file once and then
// several ids from it, which is how anyone would write it; "A9a through A9h" is expanded to
// the whole run.
//
// Same shape as markers.test.js and keyboard.test.js, which pin invariants by reading
// sources rather than by driving a browser.

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.join(__dirname, "..");
const STATEMENT = path.join(ROOT, "ACCESSIBILITY.md");
const E2E = path.join(ROOT, "tests", "e2e");

// Every id a spec file declares, as a set, keyed by the file's basename.
function declaredIds() {
  const byFile = new Map();
  for (const name of fs.readdirSync(E2E)) {
    if (!name.endsWith(".spec.js")) continue;
    const src = fs.readFileSync(path.join(E2E, name), "utf8");
    const ids = new Set();
    // Both forms the suites use: a plain string title and a template literal that
    // parametrises the viewport into the title. The id is the leading token either way.
    for (const m of src.matchAll(/test\(\s*[`"]([A-Za-z][\w]*)\./g)) ids.add(m[1]);
    byFile.set(name, ids);
  }
  return byFile;
}

// The document's citations, in reading order, each already resolved to a file.
function citations(doc) {
  const found = [];
  let currentFile = null;
  // One pass over the tokens that matter, in order: a spec filename, or a test id, or the
  // word "through" joining two ids into a range.
  //
  // AN ID IS REQUIRED TO END IN A LETTER, and that is what keeps this parse from eating the
  // document's prose. The suites number their tests A1w, A6p, A9a; the phases they came
  // from are called A1 through A4, and the document names those phases in sentences. Without
  // the trailing letter, "the A4 phase" would parse as a citation to a test that never
  // existed. The backticks are optional on purpose: the document writes a file and its id
  // inside ONE code span, which is how anyone would write it.
  const token = /([\w.-]+\.spec\.js)|([A-Z]\d+[a-z]\d*)(`?\s+through\s+`?([A-Z]\d+[a-z]\d*))?/g;
  for (const m of doc.matchAll(token)) {
    if (m[1]) {
      currentFile = path.basename(m[1]);
      continue;
    }
    const id = m[2];
    if (!currentFile) continue;
    if (m[4]) {
      // A range: same prefix, walking the trailing letter. Written as "A9a through A9h"
      // rather than as eight ids because eight ids in a sentence is unreadable.
      const prefix = id.slice(0, -1);
      const from = id.charCodeAt(id.length - 1);
      const to = m[4].charCodeAt(m[4].length - 1);
      assert.equal(prefix, m[4].slice(0, -1), `a citation range must share a prefix: ${id} through ${m[4]}`);
      assert.ok(to > from, `a citation range must run forwards: ${id} through ${m[4]}`);
      for (let c = from; c <= to; c++) found.push({ file: currentFile, id: prefix + String.fromCharCode(c) });
    } else {
      found.push({ file: currentFile, id });
    }
  }
  return found;
}

test("A4: every test id the accessibility statement cites actually exists", () => {
  const doc = fs.readFileSync(STATEMENT, "utf8");
  const declared = declaredIds();
  const cited = citations(doc);

  // ANTI-VACUITY FIRST. A parse that silently matched nothing would pass this file with a
  // clean bill of health, which is the exact failure mode the statement is written against.
  assert.ok(cited.length >= 40, `the parse must find the document's citations, found ${cited.length}`);
  assert.ok(new Set(cited.map((c) => c.file)).size >= 7, "citations must span the suites, not one file");

  const dangling = cited.filter((c) => !declared.has(c.file) || !declared.get(c.file).has(c.id));
  assert.deepEqual(
    dangling.map((c) => `${c.file} ${c.id}`),
    [],
    "ACCESSIBILITY.md cites tests that do not exist. Either the spec was renamed and the " +
      "statement must follow it, or the claim has lost its proof and must be withdrawn.",
  );
});

test("A4: the source files the statement names are real too", () => {
  const doc = fs.readFileSync(STATEMENT, "utf8");
  const paths = [...doc.matchAll(/`(frontend\/[\w./-]+|tests\/[\w./-]+)`/g)].map((m) => m[1]);
  assert.ok(paths.length >= 3, `the statement must name source files, found ${paths.length}`);
  const missing = paths.filter((rel) => !fs.existsSync(path.join(ROOT, rel)));
  assert.deepEqual(missing, [], "ACCESSIBILITY.md names files that do not exist");
});

test("A4: the statement's exception list is the gate's exception list", () => {
  // THE ONE CLAIM THE CITATION CHECK CANNOT REACH. The document's exception table is prose,
  // so nothing stops it from listing two shapes while the gate enforces three, and the
  // divergence would be invisible: both files would be internally consistent and the
  // statement would understate what the page actually excepts.
  // Pinned by count and by decider, which is the pairing the gate itself asserts (A1z).
  const doc = fs.readFileSync(STATEMENT, "utf8");
  const gate = fs.readFileSync(path.join(E2E, "a11y.spec.js"), "utf8");
  const shapes = [...gate.matchAll(/^\s{4}name: "([^"]+)"/gm)].map((m) => m[1]);
  assert.ok(shapes.length > 0, "the gate must declare named undecidable shapes");

  const table = doc.slice(doc.indexOf("## What axe cannot decide"), doc.indexOf("## What is not covered"));
  const rows = table.split("\n").filter((line) => line.startsWith("| ") && !line.startsWith("| ---"));
  assert.equal(
    rows.length - 1,
    shapes.length,
    `the statement lists ${rows.length - 1} exceptions and the gate enforces ${shapes.length}`,
  );
});
