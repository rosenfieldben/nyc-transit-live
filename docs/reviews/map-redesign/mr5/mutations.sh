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
cat > "$WORK/a" <<'A'
  background: var(--surface);
  backdrop-filter: blur(14px);
A
cat > "$WORK/r" <<'R'
  background: color-mix(in srgb, var(--surface) 94%, transparent);
  backdrop-filter: blur(14px);
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
cat > "$WORK/a" <<'A'
      ferryColorFor(b.route_id),
      position,
      popupSurfaceColor(),
    ) +
A
cat > "$WORK/r" <<'R'
      ferryColorFor(b.route_id),
      position,
    ) +
R
run M66 frontend/systems/ferry.js "$PW a11y.spec.js --grep 'A1w'"

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

echo "================================================================"
echo "died: $died   survived: $survived   run failed: $broke"
[ ${#names[@]} -gt 0 ] && printf '  %s\n' "${names[@]}"
# A SURVIVOR IS NOT A FAILURE OF THE RUN and a RUN FAILED is: the first is a finding to record, the
# second means the table did not execute and standing rule 6 says that is a red run.
[ "$broke" -eq 0 ] || exit 2
exit 0
