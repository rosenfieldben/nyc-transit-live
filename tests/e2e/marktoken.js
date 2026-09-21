// MR5 round 3: THE ONE MARK NORMALISER, IN A FILE THE NODE TIER CAN ACTUALLY REACH.
//
// WHY THIS FILE EXISTS AT ALL, which is a correction rather than a design. Round 2 ruled that
// `withoutMarks` be imported rather than copied, and put the one copy in popup.js on the measured
// claim that "popup.js requires nothing itself". That claim was false: popup.js requires
// @playwright/test for `expect`. It was false in a way no gate here could see, because the local
// checkout HAS @playwright/test installed, so `node --test` resolved it and stayed green. CI is the
// only place the truth lived: the frontend-tests job checks out and runs `node --test` with no
// `npm ci` at all (by design, the app is buildless), so the require threw at load, boards.test.js
// died before its first assertion, and the job reported one failure where thirteen tests had
// silently stopped existing.
//
// So the ruling stands and its premise gets fixed instead: one copy, importable from both tiers,
// in a file that requires NOTHING. The rule for this file is the whole point of it, and it is a
// rule the test tier now holds rather than the comment: nothing here may require any package from
// node_modules, because the node unit tier runs without them. tests/nodetier.test.js walks the
// closure of every file `node --test` loads and fails on the first bare specifier, so this file's
// promise is checked on every run rather than believed.

/* MR5: A MARK IS ONE TOKEN WHEN A SPEC PINS MARKUP, and the reason is length. Section 5 draws the
   map's own mark before a popup's title and beside a subway station's kicker, and one subway plate
   is four hundred characters of SVG: a spec that pins a whole popup's innerHTML would become
   unreadable, and a reader could not tell the assertion from the drawing.

   THE TOKEN CARRIES THE MARK'S IDENTITY, which is the correction a reviewer's mutations forced. The
   first version kept only the `<text>` label, so a pin could not tell a 17px plate from a 24px one
   or a route-coloured plate from a black one: drawing the subway station's kicker plates at the
   title's size, and then in flat black, both left the whole node suite green. The token now carries
   the label, the drawn size and the fills the mark declares as attributes, which is everything a
   mark's own arguments decide. Its geometry stays where marks live (pins.spec.js P1f for the drawn
   plate, frontend/popupvocab.test.js for the re-wrap).

   ONE COPY, IMPORTED, which is the other half. The node tier's board pins used a byte-identical
   copy of this function under a comment claiming nothing here is importable from a node test. Two
   implementations of one reader is the shape this phase's fifth defect is named for, and it does
   not get to be re-created in the commit that names it. The import lives in THIS file rather than
   in popup.js, for the reason the header gives: popup.js pulls in a package the node tier does not
   install, and a shared reader has to sit below both tiers to be shared by both. */
const withoutMarks = (html) =>
  html.replace(/<span class="pmark"[^>]*>[\s\S]*?<\/span>/g, (svg) => {
    /* EVERY <text>, NOT THE FIRST, which ruling R3 is what forced. A rail tag's body has TWO: the
       agency glyph and the branch CODE, and `exec` took the glyph, so every LIRR branch read as
       `[mark L ...]` and the one thing a route mark exists to say was unpinnable. Joined with a
       middle dot, which is the strip's own separator; a subway plate has one text, so its token is
       byte-identical and no existing pin moves from this half. */
    const label = [...svg.matchAll(/<text[^>]*>([^<]*)<\/text>/g)].map((m) => m[1]).filter(Boolean).join("\u00b7");
    const size = /width="([\d.]+)" height="([\d.]+)"/.exec(svg);
    /* AND A FILL DECLARED IN A style ATTRIBUTE COUNTS, for the same reason: the PATH diamond and the
       ferry hull declare theirs as `style="fill: ..."` and nothing else, so their tokens carried NO
       colour at all and a board drawing every diamond in the fallback slate would have pinned
       identically to one drawing the published reds and blues. This half DOES move the existing
       plate tokens, which gain the backing's `var(--paper)`: that is the honest direction, since the
       backing is part of what the mark declares. */
    const fills = [...svg.matchAll(/fill="([^"]+)"|style="fill:\s*([^;"]+)/g)]
      .map((m) => m[1] ?? m[2])
      .join(",");
    return [
      "[mark",
      label || null,
      size ? `${size[1]}x${size[2]}` : null,
      fills || null,
    ]
      .filter(Boolean)
      .join(" ") + "]";
  });

module.exports = { withoutMarks };
