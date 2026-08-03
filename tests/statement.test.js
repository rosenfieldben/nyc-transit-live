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

const { declaredIds, E2E_DIR: E2E } = require("./specids");

const ROOT = path.join(__dirname, "..");
const STATEMENT = path.join(ROOT, "ACCESSIBILITY.md");

// The document's citations, in reading order, each already resolved to a file.
function citations(doc) {
  const found = [];
  let currentFile = null;
  // One pass over the tokens that matter, in order: a spec filename, or a test id, or a
  // JOINER between two ids that makes them a range.
  //
  // THE JOINER SET IS CLOSED, AND ANYTHING ELSE FAILS LOUDLY. The first version understood
  // only the literal word "through". The adversarial round rewrote one range as "A9a to
  // A9h" and the parse silently dropped six citations: 81 became 75, the floors (40
  // citations, 7 files) never noticed, and a claim quietly stopped being checked while the
  // build stayed green. Silent under-collection is the disease this whole file exists to
  // treat, so an id pair that LOOKS like a range and is not written with a joiner this
  // parse knows is an error rather than two unrelated citations. See the assertion below.
  //
  // AN ID IS REQUIRED TO END IN A LETTER, and that is what keeps this parse from eating the
  // document's prose. The suites number their tests A1w, A6p, A9a; the phases they came
  // from are called A1 through A4, and the document names those phases in sentences. Without
  // the trailing letter, "the A4 phase" would parse as a citation to a test that never
  // existed. The backticks are optional on purpose: the document writes a file and its id
  // inside ONE code span, which is how anyone would write it.
  const JOINERS = JOINER_WORDS;
  const token = new RegExp(
    "([\\w.-]+\\.spec\\.js)" +
      "|([A-Z]\\d+[a-z]\\d*)(`?\\s*(" +
      JOINERS.map((j) => j.replace(/[.\\-]/g, "\\$&")).join("|") +
      ")\\s*`?([A-Z]\\d+[a-z]\\d*))?",
    "g",
  );
  for (const m of doc.matchAll(token)) {
    if (m[1]) {
      currentFile = path.basename(m[1]);
      continue;
    }
    const id = m[2];
    if (!currentFile) continue;
    const endId = m[5];
    if (endId) {
      // A range: same prefix, walking the trailing letter. Written as "A9a through A9h"
      // rather than as eight ids because eight ids in a sentence is unreadable.
      const prefix = id.slice(0, -1);
      const from = id.charCodeAt(id.length - 1);
      const to = endId.charCodeAt(endId.length - 1);
      assert.equal(prefix, endId.slice(0, -1), `a citation range must share a prefix: ${id} .. ${endId}`);
      assert.ok(to > from, `a citation range must run forwards: ${id} .. ${endId}`);
      for (let c = from; c <= to; c++) found.push({ file: currentFile, id: prefix + String.fromCharCode(c) });
    } else {
      found.push({ file: currentFile, id });
    }
  }
  return found;
}

// Shared with the parse below so the check and the grammar cannot drift apart.
const JOINER_WORDS = ["through", "to", "and", "-", "..", String.fromCharCode(0x2013)];

test("A4: every test id the accessibility statement cites actually exists", () => {
  const doc = fs.readFileSync(STATEMENT, "utf8");
  const declared = declaredIds();
  const cited = citations(doc);

  // ANTI-VACUITY FIRST. A parse that silently matched nothing would pass this file with a
  // clean bill of health, which is the exact failure mode the statement is written against.
  assert.ok(cited.length >= 40, `the parse must find the document's citations, found ${cited.length}`);
  assert.ok(new Set(cited.map((c) => c.file)).size >= 7, "citations must span the suites, not one file");

  /* AND NOTHING THAT LOOKS LIKE A RANGE MAY GO UNPARSED. The floors above cannot see a
     silent drop, because losing six of eighty-one citations leaves both of them satisfied.
     This looks for two ids separated by a short span of prose and requires the joiner to be
     one the parse understands, so an unsupported joiner is a red build rather than a quiet
     reduction in what the statement checks.
     ROUND 2 FOUND TWO HOLES IN THE FIRST VERSION OF THIS GUARD, both of which let the exact
     silent drop it was written to stop happen with a green build. It tested containment, so
     any joiner CONTAINING a known one was exempt ("A9a through-ish A9h", or the "to" inside
     "up to"); and its character class excluded newlines, so a range broken across a line
     wrap, which this document does constantly, was invisible. It now matches whole words
     against the joiner set and lets the span cross a wrap. */
  const suspectedRanges = [...doc.matchAll(/([A-Z]\d+[a-z]\d*)`?([^`]{1,16}?)`?([A-Z]\d+[a-z]\d*)/g)]
    .filter(([, from, joiner, to]) => from.slice(0, -1) === to.slice(0, -1) && !/^[\s,;]*$/.test(joiner))
    .filter(([, , joiner]) => {
      // Judged by WORDS when the joiner has any, and only by punctuation when it has none.
      // A punctuation fallback that applies to a worded joiner exempts anything CONTAINING a
      // punctuation joiner, which is how "through-ish" walked past the first version of this
      // guard: it is not a joiner the parse knows, the middle ids were dropped, and the "-"
      // inside it rescued the whole span.
      const words = joiner.split(/[^\w.-]+/).filter(Boolean);
      if (/[A-Za-z]/.test(joiner)) return !words.some((w) => JOINER_WORDS.includes(w));
      return !JOINER_WORDS.includes(joiner.trim());
    })
    .map(([whole]) => whole.replace(/\s+/g, " "));
  assert.deepEqual(
    suspectedRanges,
    [],
    "a citation range is written with a joiner this parse does not understand, so its middle " +
      "ids would be silently dropped. Use one of: " + JOINER_WORDS.join(", "),
  );

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
