#!/usr/bin/env bash
# MR5's whole mutation table, re-runnable.
#
# STANDING RULE 6, WHICH THIS FILE EXISTS TO MAKE POSSIBLE: a mutation whose anchor misses is a
# mutation that did not run, so the whole table is re-run before every push and an ANCHOR MISS is a
# failure of the run rather than a survivor. MR4's F19 is what that rule cost: mutation M35 had been
# printing ANCHOR MISS and exiting without testing anything for a whole round, because round 1 turned
# the line it anchored on into a block and nobody re-ran the table. A table that lives only in a
# markdown file cannot be re-run, so it lives here.
#
#   bash docs/reviews/map-redesign/mr5/mutations.sh <sha>
#
# Each row prints the worktree's HEAD, then DIED (a gate rejected the defect) or SURVIVED (every gate
# passed with it in place) or a RUN FAILED / ANCHOR MISS, which is never a result about the code.
set -uo pipefail
SHA="${1:?usage: mutations.sh <sha>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../../.." && pwd)"
WORK="$(mktemp -d /tmp/mr5muts.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

NODE_ALL='node --test "frontend/*.test.js" "tests/*.test.js"'
PW='npx playwright test --config tests/e2e/playwright.config.js'

died=0; survived=0; broke=0; names=()
run() { # run <label> <file> <gate...>   (anchor in $WORK/a, replacement in $WORK/r)
  local label="$1" file="$2"; shift 2
  echo "=== $label  ($file)"
  cp "$WORK/a" "$WORK/anchor.$label" 2>/dev/null || true
  bash "$HERE/mutate.sh" "$SHA" "$label" "$file" "$WORK/a" "$WORK/r" "$@"
  case $? in
    0) died=$((died+1));;
    1) survived=$((survived+1)); names+=("$label SURVIVED");;
    *) broke=$((broke+1)); names+=("$label RUN FAILED");;
  esac
  echo
}

# ---- M60: the autopan padding not derived from the rendered header (the brief's own) ----
cat > "$WORK/a" <<'A'
    want: popupAutoPanWant(pageChromeBottom()),
A
cat > "$WORK/r" <<'R'
    want: popupAutoPanWant(60),
R
run M60 frontend/systems/shared.js "$PW popups.spec.js --grep 'D6d|D6e'"

# ---- M61: the clamp removed, so each padding is the README's recipe verbatim ----
cat > "$WORK/a" <<'A'
  const top = Math.min(wantTop, Math.max(0, vertical - wantBottom));
  const left = Math.min(wantLeft, Math.max(0, horizontal - wantRight));
A
cat > "$WORK/r" <<'R'
  const top = wantTop;
  const left = wantLeft;
R
run M61 frontend/helpers.js "$NODE_ALL" "$PW popups.spec.js --grep D6d"

# ---- M62: the stand-down skips rather than disarms ----
cat > "$WORK/a" <<'A'
  standDownPopupAutoPan();
A
cat > "$WORK/r" <<'R'
  /* mutated: the padding is left armed */
R
run M62 frontend/systems/shared.js "$PW popups.spec.js --grep D6f"

# ---- M63: readableInk only darkens again, as it did before MR5 ----
cat > "$WORK/a" <<'A'
  if ((contrastRatio("#ffffff", background) ?? 0) > (contrastRatio("#000000", background) ?? 0)) {
A
cat > "$WORK/r" <<'R'
  if (false) {
R
run M63 frontend/helpers.js "$NODE_ALL" "$PW a11y.spec.js --grep 'A1w'"

# ---- M64: the popup surface goes back to the design's 94% ----
# RE-ANCHORED AT THE VOCABULARY's TIP, and standing rule 6 is why this was found rather than
# reported as a survivor: the anchor named `backdrop-filter: blur(14px)` on the line after the
# background, and the footer commit DROPPED that declaration (a rule measured to paint nothing is not
# kept with a test saying so). So the anchor matched 0 times and the row ran nothing, which is
# exactly MR4's F19 one stage later. The anchor is now the three lines the rule still has.
cat > "$WORK/a" <<'A'
  background: var(--surface);
  color: var(--ink);
  border-radius: 0;
A
cat > "$WORK/r" <<'R'
  background: color-mix(in srgb, var(--surface) 94%, transparent);
  color: var(--ink);
  border-radius: 0;
R
run M64 frontend/style.css "$NODE_ALL" "$PW a11y.spec.js --grep 'A1w'"

# ---- M65: .popup-sub back to A3's #666, which was chosen for a white popup ----
cat > "$WORK/a" <<'A'
.popup-sub {
  color: var(--muted);
A
cat > "$WORK/r" <<'R'
.popup-sub {
  color: #666;
R
run M65 frontend/style.css "$NODE_ALL" "$PW a11y.spec.js --grep 'A1w'"

# ---- M66: the ferry popup head loses the surface, so it inks against the light fallback ----
# RE-ANCHORED TOO, for the same reason and found the same way: the vocabulary commit put the mark
# argument after the surface, so the four-line anchor matched 0 times. The replacement drops the
# surface and keeps the mark, which is the defect this row is about.
cat > "$WORK/a" <<'A'
      popupSurfaceColor(),
      // And the hull this boat is drawn with, off its own marker, at the title's size.
      popupMarkHtml(markerMarkHtml(record.marker)),
A
cat > "$WORK/r" <<'R'
      undefined,
      // And the hull this boat is drawn with, off its own marker, at the title's size.
      popupMarkHtml(markerMarkHtml(record.marker)),
R
# M66 SURVIVED its first run, against a11y.spec.js A1w alone, and the survivor is what found D6i:
# A1w's popup states open a SUBWAY train popup, so five of the six route-coloured heads were never
# measured anywhere. D6i sweeps all fourteen surfaces in both themes. Both gates are kept, so the
# record shows what did not catch it as well as what does.
run M66 frontend/systems/ferry.js "$PW popups.spec.js --grep D6i" "$PW a11y.spec.js --grep 'A1w'"

# ---- M67: the theme swap stops rebuilding open popups ----
cat > "$WORK/a" <<'A'
  rebuildOpenPopupsForTheme();
A
cat > "$WORK/r" <<'R'
  /* mutated: an open popup keeps the ink it was built with */
R
run M67 frontend/systems/shared.js "$PW popups.spec.js --grep D6h"

# ---- M68: autoPan left on, so Leaflet pans first and the app pans again ----
cat > "$WORK/a" <<'A'
const POPUP_OPTIONS = { maxWidth: 320, autoPan: false };
A
cat > "$WORK/r" <<'R'
const POPUP_OPTIONS = { maxWidth: 320 };
R
run M68 frontend/helpers.js "$PW motion.spec.js --grep A5e"

# ---- M69: one bind site loses POPUP_OPTIONS, which is what D6b reads every popup for ----
cat > "$WORK/a" <<'A'
        .bindPopup(() => busPopup(newRecord), POPUP_OPTIONS)
A
cat > "$WORK/r" <<'R'
        .bindPopup(() => busPopup(newRecord))
R
run M69 frontend/systems/buses.js "$PW popups.spec.js --grep D6b"

# ================================================================================================
# THE VOCABULARY's OWN GUARDS (M70 onward). One per claim the vocabulary commit makes, in the same
# form as the ten above: revert the decision, run the gate that is supposed to see it.
# ================================================================================================

# ---- M70: the popup's mark REBUILT instead of copied, which is the drift N6 is about ----
cat > "$WORK/a" <<'A'
  return `<span class="pmark" aria-hidden="true">${sized} width="${width}" height="${h}"${source.slice(end)}</span>`;
A
cat > "$WORK/r" <<'R'
  return `<span class="pmark" aria-hidden="true"><svg viewBox="0 0 ${box[1]} ${box[2]}" width="${width}" height="${h}"></svg></span>`;
R
run M70 frontend/helpers.js "$NODE_ALL"

# ---- M71: the title mark's clamp removed, so the rail tag draws SMALLER than the map draws it ----
cat > "$WORK/a" <<'A'
  const h = height == null ? Math.max(POPUP_MARK_TITLE, Number(box[2])) : Number(height);
A
cat > "$WORK/r" <<'R'
  const h = height == null ? POPUP_MARK_TITLE : Number(height);
R
run M71 frontend/helpers.js "$NODE_ALL"

# ---- M72: the grid prints a row with nothing to say, which is the silence rule Q1 kept ----
cat > "$WORK/a" <<'A'
    .filter((row) => row && row.k && row.v)
A
cat > "$WORK/r" <<'R'
    .filter((row) => row && row.k)
R
run M72 frontend/helpers.js "$NODE_ALL" "$PW pins.spec.js --grep 'P5a|P5c'"

# ---- M73: the cell separator dropped, so a popup's textContent glues its words together ----
cat > "$WORK/a" <<'A'
    .map((row) => `<div class="k">${esc(row.k)}</div>\n<div class="v">${esc(row.v)}</div>`)
    .join("\n");
A
cat > "$WORK/r" <<'R'
    .map((row) => `<div class="k">${esc(row.k)}</div><div class="v">${esc(row.v)}</div>`)
    .join("");
R
run M73 frontend/helpers.js "$NODE_ALL"

# ---- M74: a kicker word coined rather than taken from the app ----
cat > "$WORK/a" <<'A'
  ferry: "NYC Ferry",
A
cat > "$WORK/r" <<'R'
  ferry: "Ferry Service",
R
run M74 frontend/helpers.js "$NODE_ALL"

# ---- M75: a popup prints text in no named slot at all, which is P5d's whole subject ----
# M75 SURVIVED at this tip and the reason is recorded rather than smoothed over: the bus's route
# note renders only while busRouteNotes holds an entry inside NOTE_TTL_MS, and no pinned world has
# one, so the mutated div is never drawn. It is an equivalent mutant in every world this suite
# boots, and it is outside BOTH directions of the coverage test: direction A (P5d) cannot see a
# string that is not rendered, and direction B (P5b) reads LITERALS rather than classes, so a class
# removed from a literal nobody disputes is invisible to it. M77 below is the same defect in a state
# the stock world does render.
cat > "$WORK/a" <<'A'
    (showNote ? `<div class="popup-sub">${esc(note.message)}</div>\n` : "") +
A
cat > "$WORK/r" <<'R'
    (showNote ? `<div>${esc(note.message)}</div>\n` : "") +
R
run M75 frontend/systems/buses.js "$PW pins.spec.js --grep P5d"

# ---- M76: M47 AGAIN, at the tip, now that the alpha is drawn inside a popup too ----
# MR4 recorded M47 as a survivor because bestPerFamily only ever reports a family's strongest paint
# and the two element alphas on this map are both a paper backing. P4d records them directly, so this
# is the round where the same revert has a guard to meet.
cat > "$WORK/a" <<'A'
        return (Number.isFinite(own) ? own : 1) * (Number.isFinite(paint) ? paint : 1);
A
cat > "$WORK/r" <<'R'
        return 1;
R
run M76 tests/e2e/contrast.js "$PW pins.spec.js --grep P4d"

# ---- M77: the same defect where a fixture actually renders it (the AirTrain sub-line) ----
cat > "$WORK/a" <<'A'
    `<div class="popup-sub">scheduled service (no live tracking)</div>\n`;
A
cat > "$WORK/r" <<'R'
    `<div>scheduled service (no live tracking)</div>\n`;
R
run M77 frontend/helpers.js "$PW pins.spec.js --grep P5d"

echo "================================================================"
echo "died: $died   survived: $survived   run failed: $broke"
[ ${#names[@]} -gt 0 ] && printf '  %s\n' "${names[@]}"
# A SURVIVOR IS NOT A FAILURE OF THE RUN and a RUN FAILED is: the first is a finding to record, the
# second means the table did not execute and standing rule 6 says that is a red run.
[ "$broke" -eq 0 ] || exit 2
exit 0
