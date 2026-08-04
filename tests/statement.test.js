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

const { declaredIds, allDeclared, isCitableId, E2E_DIR: E2E } = require("./specids");

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
  const JOINERS = RANGE_JOINERS;
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

/* THE JOINER GRAMMAR, IN THREE CLASSES, because round 3 found the two-class version
   wrong in both directions.
   A RANGE joiner means "and every id between": it expands. A LIST separator means "these
   two, separately": it does not. Round 2 put "and" in the range set, so "A9a and A9l" would
   have expanded into fifteen fabricated citations, and the guard's own remediation message
   recommended it. And the guard exempted any span CONTAINING a range word, so "up to"
   passed on the strength of the "to" inside it while the parse understood neither.
   Both are fixed by classifying the joiner EXACTLY, after trimming, against one of the
   three sets. Exact matching is what makes "up to" unclassifiable, which is what makes it
   loud. */
const RANGE_JOINERS = ["through", "to", "-", "..", String.fromCharCode(0x2013)];
const LIST_JOINERS = ["and", "or", ",", ";", ", and", ", or"];

// The whole point of this file, applied to its own grammar: a joiner it cannot place in one
// of the two sets is neither ignored nor guessed at.
function classifyJoiner(raw) {
  const joiner = String(raw).replace(/\s+/g, " ").trim();
  if (joiner === "") return "adjacent";
  if (RANGE_JOINERS.includes(joiner)) return "range";
  if (LIST_JOINERS.includes(joiner)) return "list";
  return "unknown";
}

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
    .filter(([, , joiner]) => classifyJoiner(joiner) === "unknown")
    .map(([whole]) => whole.replace(/\s+/g, " "));
  assert.deepEqual(
    suspectedRanges,
    [],
    "a citation span uses a joiner this parse cannot classify, so its middle ids would be " +
      "silently dropped. Ranges: " + RANGE_JOINERS.join(", ") + ". Lists: " + LIST_JOINERS.join(", "),
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

/* ROUND 3: A CITATION THIS GRAMMAR CANNOT READ IS AN ERROR, NOT AN ABSENCE.
   The parse only ever saw ids of the lettered form, so a citation to one of smoke.spec.js's
   numeric scenarios ("smoke.spec.js 12") matched nothing, resolved against nothing, and was
   invisible: not dangling, not counted, not checked. Thirty-five of that file's forty-one
   tests were in that position, and the collector could not even see them to say so.
   Both halves are fixed. The collector reads numeric ids, and this asserts that anything
   written in the document immediately after a spec filename is a token this grammar can
   classify, so an unciteable citation is loud. */
test("A4: a token written as a citation must be one this grammar can read", () => {
  const doc = fs.readFileSync(STATEMENT, "utf8");
  const attempts = [...doc.matchAll(/\b([\w.-]+\.spec\.js)`?\s+`?([A-Za-z0-9][\w]*)/g)]
    .map(([, file, token]) => ({ file, token }))
    // Prose continues after a filename all the time ("layout.spec.js A4b samples ..."), so
    // only a token that could plausibly BE an id is judged: a bare English word after a
    // filename is prose, and treating it as a failed citation would make this unusable.
    .filter(({ token }) => /^[A-Za-z]?\d/.test(token));
  assert.ok(attempts.length >= 20, `the scan must find the document's citations, found ${attempts.length}`);
  const unreadable = attempts.filter(({ token }) => !isCitableId(token)).map(({ file, token }) => `${file} ${token}`);
  assert.deepEqual(
    unreadable,
    [],
    "a token follows a spec filename in a position this grammar reads as a citation, but it " +
      "is not an id form the grammar knows. Teach specids.js the form or rewrite the sentence; " +
      "leaving it is how a citation stops being checked without anyone noticing.",
  );
});

test("A4: every id the suites declare is one the citation grammar could cite", () => {
  // THE OTHER DIRECTION. The check above catches a citation the grammar cannot read; this
  // catches an id nobody COULD cite, which is the same hole seen from the suites' end.
  const uncitable = allDeclared()
    .filter(({ id }) => !isCitableId(id))
    .map(({ file, id }) => `${file} ${id}`);
  assert.deepEqual(
    uncitable,
    [],
    "a spec declares an id the citation grammar cannot express, so no claim could ever cite " +
      "it and a citation attempt would be unreadable. Teach specids.js the form.",
  );
});

/* THE SPELLING CORPUS. Every exotic form these scanners must handle, pinned as a fixture,
   so extending one starts from a failing example rather than from a hopeful regex. Each
   entry here was written by an adversarial round as an attack that worked. */
const JOINER_CORPUS = [
  ["A9a through A9l", "range", "the form the document actually uses"],
  ["A9a to A9l", "range", "round 2's attack: a shorter range word"],
  ["A9a - A9l", "range", "punctuation range"],
  ["A9a .. A9l", "range", "punctuation range"],
  ["A9a and A9l", "list", "round 3's attack: expanding this fabricated fifteen citations"],
  ["A9a, A9l", "list", "the ordinary two-item list"],
  ["A9a up to A9l", "unknown", "round 3's attack: 'to' is a whole word inside it"],
  ["A9a through-ish A9l", "unknown", "round 2's attack: contains a range word"],
  ["A9a spanning A9l", "unknown", "an ordinary unknown"],
];

test("A4: the joiner grammar classifies every spelling the rounds invented", () => {
  const wrong = [];
  for (const [phrase, expected, why] of JOINER_CORPUS) {
    const joiner = phrase.replace(/^[A-Z]\d+[a-z]\d*/, "").replace(/[A-Z]\d+[a-z]\d*$/, "");
    const got = classifyJoiner(joiner);
    if (got !== expected) wrong.push(`${phrase}: expected ${expected}, got ${got} (${why})`);
  }
  assert.deepEqual(wrong, [], "the joiner grammar has drifted from the corpus of forms it must classify");
});
