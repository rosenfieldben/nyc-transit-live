// A4 ROUND 1: ONE PLACE THAT KNOWS WHICH TEST IDS EXIST.
//
// Two things in this repo cite specs by id in prose: the accessibility gate's exception
// list, where every named undecidable must name the spec that decides it, and
// ACCESSIBILITY.md, where every claim names the test that proves it. Both were checked by
// REGEX before the adversarial round, which is to say neither was checked at all: a
// decider naming "nosuchfile.spec.js A0z" passed, because the pattern only asked whether
// the sentence LOOKED like a citation.
//
// A citation that resolves to nothing is worse than no citation, because it reads as
// evidence. So resolution lives here, is used by both, and both fail loudly.
//
// ROUND 3: AND ANYTHING THIS FILE CANNOT CLASSIFY IS NOW LOUD. The first version's
// collector required an id to start with a LETTER, so 35 of smoke.spec.js's 41 tests were
// never collected: a citation to one of them matched no pattern, resolved against nothing,
// and was therefore INVISIBLE rather than dangling. That is the silent-drop disease in its
// third costume, and the rule this file now enforces is the cure: an id form the collector
// does not know is an error, not an absence.

const fs = require("node:fs");
const path = require("node:path");

const E2E_DIR = path.join(__dirname, "e2e");

/* THE TWO ID FORMS THIS REPO USES, and both are collected.
   LETTERED: a phase letter, a phase number, a test letter, sometimes a disambiguating
   digit. A1w, A6p, C2c2. This is what every accessibility-arc suite uses.
   NUMERIC: the original smoke suite numbers its scenarios 1, 2, ... 29a, 29b. They are
   real test ids and they are cited nowhere today, but "cited nowhere today" is not a
   reason to be unable to see them: it is exactly how a citation to one would go unnoticed. */
const LETTERED_ID = /^[A-Z]\d+[a-z]\d*$/;
const NUMERIC_ID = /^\d+[a-z]?$/;

const isCitableId = (token) => LETTERED_ID.test(token) || NUMERIC_ID.test(token);

// Every id each spec file declares, keyed by basename. Both title forms the suites use are
// read: a plain string, and the template literal that parametrises a viewport into the
// title. The id is the leading token either way, and it may be lettered or numeric.
function declaredIds(dir = E2E_DIR) {
  const byFile = new Map();
  for (const name of fs.readdirSync(dir)) {
    if (!name.endsWith(".spec.js")) continue;
    const src = fs.readFileSync(path.join(dir, name), "utf8");
    const ids = new Set();
    for (const m of src.matchAll(/test\(\s*[`"]([A-Za-z0-9][\w]*)\./g)) ids.add(m[1]);
    byFile.set(name, ids);
  }
  return byFile;
}

// Every id declared anywhere, as "file id" strings. Used by the corpus test that asserts
// the collector and the citation grammar agree about what an id looks like.
function allDeclared(dir = E2E_DIR) {
  const out = [];
  for (const [file, ids] of declaredIds(dir)) for (const id of ids) out.push({ file, id });
  return out;
}

// Pull every "<file>.spec.js <id>" pair out of a sentence. Deliberately strict about the
// shape: a decider that gestures at a file without naming a test is not a citation.
function citedPairs(sentence) {
  return [...String(sentence).matchAll(/\b([\w.-]+\.spec\.js)\s+([A-Z]\d+[a-z]\d*)\b/g)].map((m) => ({
    file: m[1],
    id: m[2],
  }));
}

// The pairs in `sentence` that name a test which does not exist. Empty means every citation
// in it resolves.
function danglingCitations(sentence, declared = declaredIds()) {
  return citedPairs(sentence)
    .filter(({ file, id }) => !declared.has(file) || !declared.get(file).has(id))
    .map(({ file, id }) => `${file} ${id}`);
}

module.exports = {
  declaredIds,
  allDeclared,
  citedPairs,
  danglingCitations,
  isCitableId,
  LETTERED_ID,
  NUMERIC_ID,
  E2E_DIR,
};
