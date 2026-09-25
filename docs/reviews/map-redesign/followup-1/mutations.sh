#!/usr/bin/env bash
# Follow-up 1's whole mutation table, re-runnable (standing rule 6: the WHOLE table is re-run before
# every push, and an ANCHOR MISS is a failure of the run rather than a survivor).
#
#   bash docs/reviews/map-redesign/followup-1/mutations.sh <sha>
#
# Each row prints the worktree's HEAD, then DIED (a gate rejected the defect, with the assertion
# that did it) or SURVIVED (every gate passed with it in place) or a RUN FAILED / ANCHOR MISS, which
# is never a result about the code.
#
# M0 IS A CONTROL and the only row that must SURVIVE: its "mutation" replaces an anchor with itself,
# so every gate the table uses runs once against the unmutated tree. On a machine that is also
# running other people's suites, a gate that fails here would make every row gated on it look
# killed, so the control is what makes the other twenty-one verdicts mean something.
set -uo pipefail
SHA="${1:?usage: mutations.sh <sha>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$(mktemp -d "$(cd "${TMPDIR:-/tmp}" && pwd -P)/f1muts.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

NODE_ALL='node --test "frontend/*.test.js" "tests/*.test.js"'
PW='npx playwright test --config tests/e2e/playwright.config.js --workers=2 --reporter=line'

died=0; survived=0; broke=0; names=()
run() { # run <label> <file> <gate...>   (anchor in $WORK/a, replacement in $WORK/r)
  local label="$1" file="$2"; shift 2
  echo "=== $label  ($file)"
  bash "$HERE/mutate.sh" "$SHA" "$label" "$file" "$WORK/a" "$WORK/r" "$@"
  case $? in
    0) died=$((died+1)); names+=("$label died");;
    1) survived=$((survived+1)); names+=("$label SURVIVED");;
    *) broke=$((broke+1)); names+=("$label RUN FAILED");;
  esac
  echo
}

# ---- M0: the control. Every gate below, once, on the tree as committed. MUST SURVIVE. ----
cat > "$WORK/a" <<'A'
const BUS_MARKER_ZOOM = 13;
A
cp "$WORK/a" "$WORK/r"
run M0 frontend/helpers.js "$NODE_ALL" "$PW buszoom.spec.js" "$PW a11y.spec.js --grep 'buses (undrawn|drawn) at'" \
  "$PW pins.spec.js --grep 'P1e|P1g|P6'" "$PW layout.spec.js --grep A4b" "$PW families.spec.js --grep 'D4f|D4g'" \
  "$PW smoke.spec.js --grep '7\. bus route'"

# ---- M1: the band's threshold moved to 12 (the brief's first row) ----
cat > "$WORK/a" <<'A'
const BUS_MARKER_ZOOM = 13;
A
cat > "$WORK/r" <<'R'
const BUS_MARKER_ZOOM = 12;
R
run M1 frontend/helpers.js "$NODE_ALL"

# ---- M2: the stylesheet rule removed (the brief's second row) ----
cat > "$WORK/a" <<'A'
:root[data-bus-band="hidden"] .bus-marker {
  display: none;
}
A
printf '' > "$WORK/r"
run M2 frontend/style.css "$PW buszoom.spec.js --grep D7a"

# ---- M3 and M3b: aria-hidden not applied to an undrawn bus (the brief's third row), in two tiers ----
cat > "$WORK/a" <<'A'
  el.setAttribute("aria-hidden", "true");
  el.style.pointerEvents = "none";
A
cat > "$WORK/r" <<'R'
  el.style.pointerEvents = "none";
R
run M3 frontend/systems/buses.js "$PW buszoom.spec.js --grep D7b"
run M3b frontend/systems/buses.js "$PW a11y.spec.js --grep 'buses undrawn at Rail'"

# ---- M4 and M4b: the tooltip's wording removed (the brief's fourth row), in two tiers ----
cat > "$WORK/a" <<'A'
tick: "#605d5d", note: BUS_ZOOM_WORDS },
A
cat > "$WORK/r" <<'R'
tick: "#605d5d" },
R
run M4 frontend/helpers.js "$NODE_ALL"
run M4b frontend/helpers.js "$PW buszoom.spec.js --grep D7e"

# ---- M5: pointer-events not taken away from an undrawn bus ----
cat > "$WORK/a" <<'A'
  el.setAttribute("aria-hidden", "true");
  el.style.pointerEvents = "none";
A
cat > "$WORK/r" <<'R'
  el.setAttribute("aria-hidden", "true");
R
run M5 frontend/systems/buses.js "$PW buszoom.spec.js --grep D7b"

# ---- M6: the add hook removed. NOT "a rebuilt element keeps nothing", which this row's first
# ---- comment said: the feed toggle's own band repaint rewrites a rebuilt element, and D7c's
# ---- toggle half passes. What dies is the bus the POLL adds at Rail, which no zoomend follows. ----
cat > "$WORK/a" <<'A'
      marker.on("add", () => paintBusReach(marker));
A
printf '' > "$WORK/r"
run M6 frontend/systems/buses.js "$PW buszoom.spec.js --grep D7c"

# ---- M7 and M7b: paintZoomBand stops sweeping the buses, so reach does not follow the zoom ----
cat > "$WORK/a" <<'A'
  if (typeof paintBusBand === "function") paintBusBand();
A
printf '' > "$WORK/r"
run M7 frontend/systems/shared.js "$PW buszoom.spec.js --grep D7b"
run M7b frontend/systems/shared.js "$PW a11y.spec.js --grep 'buses drawn at City'"

# ---- M8 and M8b: the failure policy flipped to fail-closed, in each reader ----
cat > "$WORK/a" <<'A'
:root[data-bus-band="hidden"] .bus-marker {
A
cat > "$WORK/r" <<'R'
:root:not([data-bus-band="drawn"]) .bus-marker {
R
run M8 frontend/style.css "$PW buszoom.spec.js --grep D7g"
cat > "$WORK/a" <<'A'
  return document.documentElement.getAttribute("data-bus-band") !== "hidden";
A
cat > "$WORK/r" <<'R'
  return document.documentElement.getAttribute("data-bus-band") === "drawn";
R
run M8b frontend/systems/buses.js "$PW buszoom.spec.js --grep D7g"

# ---- M9: the Key's bus row loses the words ----
cat > "$WORK/a" <<'A'
        Bus (arrow points where it's heading); shown from City zoom
A
cat > "$WORK/r" <<'R'
        Bus (arrow points where it's heading)
R
run M9 frontend/index.html "$PW buszoom.spec.js --grep D7e"

# ---- M10: the band never written on the root ----
cat > "$WORK/a" <<'A'
  document.documentElement.setAttribute("data-bus-band", busMarkerBand(zoom));
A
printf '' > "$WORK/r"
run M10 frontend/systems/shared.js "$PW buszoom.spec.js --grep D7a"

# ---- M11: the rule widened from the buses to every marker (the pins' reason to exist) ----
cat > "$WORK/a" <<'A'
:root[data-bus-band="hidden"] .bus-marker {
A
cat > "$WORK/r" <<'R'
:root[data-bus-band="hidden"] .leaflet-marker-icon {
R
run M11 frontend/style.css "$PW pins.spec.js --grep P6b"

# ---- M12: the rule reaches the popup's own copy of the mark ----
cat > "$WORK/a" <<'A'
:root[data-bus-band="hidden"] .bus-marker {
A
cat > "$WORK/r" <<'R'
:root[data-bus-band="hidden"] .pmark,
:root[data-bus-band="hidden"] .bus-marker {
R
run M12 frontend/style.css "$PW buszoom.spec.js --grep D7f"

# ---- M13 to M15: the three measuring sentinels put back at the opening zoom ----
cat > "$WORK/a" <<'A'
  await placeView(page, "view-city");
A
printf '' > "$WORK/r"
run M13 tests/e2e/layout.spec.js "$PW layout.spec.js --grep A4b"
cat > "$WORK/a" <<'A'
  // At City, because a bus is not drawn below it (follow-up 1) and this reads the drawn mark.
  await pressView(page, "view-city");
A
printf '' > "$WORK/r"
run M14 tests/e2e/families.spec.js "$PW families.spec.js --grep D4f"
cat > "$WORK/a" <<'A'
  // At City, for D4f's reason: a dimmed bus is only a treatment where the bus is drawn.
  await pressView(page, "view-city");
A
printf '' > "$WORK/r"
run M15 tests/e2e/families.spec.js "$PW families.spec.js --grep D4g"

# ---- M16: the rule as a re-render, bus markers taken off the map below 13 (what P6a exists for) ----
cat > "$WORK/a" <<'A'
  for (const record of buses.values()) paintBusReach(record.marker);
A
cat > "$WORK/r" <<'R'
  for (const record of buses.values()) {
    if (busesDrawn()) busLayer.addLayer(record.marker);
    else busLayer.removeLayer(record.marker);
  }
R
run M16 frontend/systems/buses.js "$PW pins.spec.js --grep P6a"

# ---- M17: smoke 7 clicks a bus at the opening zoom again ----
cat > "$WORK/a" <<'A'
     would have to go to do the same. buszoom.spec.js D7b is what says a click cannot land below. */
  await pressView(page, "view-city");
A
cat > "$WORK/r" <<'R'
     would have to go to do the same. buszoom.spec.js D7b is what says a click cannot land below. */
R
run M17 tests/e2e/smoke.spec.js "$PW smoke.spec.js --grep '7\. bus route'"

# ---- M18: a drawn bus exposed as nothing, which only the axe state's per-rule check can see ----
# The Rail half of that check is dominated by its state's own aria-hidden premise (M3b dies there
# first); the City half is not, because a bus with no role and no name is visible, not aria-hidden,
# and breaks no axe rule. role-img-alt simply stops examining it, and examinedBy is what asks.
cat > "$WORK/a" <<'A'
  if (busesDrawn()) {
    el.removeAttribute("aria-hidden");
A
cat > "$WORK/r" <<'R'
  if (busesDrawn()) {
    el.removeAttribute("role");
    el.removeAttribute("aria-label");
    el.removeAttribute("aria-hidden");
R
run M18 frontend/systems/buses.js "$PW a11y.spec.js --grep 'buses drawn at City'"

echo "================================================================"
echo "sha: $(git rev-parse "$SHA")"
echo "died: $died   survived: $survived   run failed: $broke"
printf '  %s\n' "${names[@]}"
# M0 must survive and nothing else may; a RUN FAILED means the table did not execute (rule 6).
[ "$broke" -eq 0 ] || exit 2
exit 0
