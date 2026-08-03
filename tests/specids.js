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

const fs = require("node:fs");
const path = require("node:path");

const E2E_DIR = path.join(__dirname, "e2e");

// Every id each spec file declares, keyed by basename. Both title forms the suites use are
// read: a plain string, and the template literal that parametrises a viewport into the
// title. The id is the leading token either way.
function declaredIds(dir = E2E_DIR) {
  const byFile = new Map();
  for (const name of fs.readdirSync(dir)) {
    if (!name.endsWith(".spec.js")) continue;
    const src = fs.readFileSync(path.join(dir, name), "utf8");
    const ids = new Set();
    for (const m of src.matchAll(/test\(\s*[`"]([A-Za-z][\w]*)\./g)) ids.add(m[1]);
    byFile.set(name, ids);
  }
  return byFile;
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

module.exports = { declaredIds, citedPairs, danglingCitations, E2E_DIR };
