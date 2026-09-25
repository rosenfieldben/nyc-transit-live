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
# M0 AND M0z ARE CONTROLS and the only rows that must SURVIVE: each "mutation" replaces an anchor
# with itself, so every gate the table uses runs against the unmutated tree, once before the rows
# and once after them. On a machine that is also running other people's suites, a gate that fails
# there would make every row gated on it look killed, so the controls are what make the other
# thirty-three verdicts mean something; the closing one is there because contention that starts
# partway through the table is invisible to a control that ran only at the start.
#
# THE EXIT STATUS SAYS ALL OF IT, which the first version did not: it exited 0 with a dead control
# or a surviving row, printing the same summary a healthy run prints (the review's V1). Now:
# 2 if any row failed to run, 1 if a control died or any other row survived, 0 only for the table
# this file claims.
set -uo pipefail
SHA="${1:?usage: mutations.sh <sha>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$(mktemp -d "$(cd "${TMPDIR:-/tmp}" && pwd -P)/f1muts.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

NODE_ALL='node --test "frontend/*.test.js" "tests/*.test.js"'
PW='npx playwright test --config tests/e2e/playwright.config.js --workers=2 --reporter=line'

died=0; survived=0; broke=0; wrong=0; names=()
run() { # run <label> <file> <gate...>   (anchor in $WORK/a, replacement in $WORK/r)
  local label="$1" file="$2"; shift 2
  local control=0
  case "$label" in M0|M0z) control=1;; esac
  echo "=== $label  ($file)"
  bash "$HERE/mutate.sh" "$SHA" "$label" "$file" "$WORK/a" "$WORK/r" "$@"
  case $? in
    0) died=$((died+1)); names+=("$label died"); [ "$control" -eq 1 ] && wrong=$((wrong+1));;
    1) survived=$((survived+1)); names+=("$label SURVIVED"); [ "$control" -eq 0 ] && wrong=$((wrong+1));;
    *) broke=$((broke+1)); names+=("$label RUN FAILED");;
  esac
  echo
}

CONTROL_GATES=("$NODE_ALL" "$PW buszoom.spec.js" "$PW a11y.spec.js --grep 'buses (undrawn|drawn) at'"
  "$PW pins.spec.js --grep 'P1e|P1g|P6'" "$PW layout.spec.js --grep A4b" "$PW families.spec.js --grep 'D4f|D4g'"
  "$PW smoke.spec.js --grep '7\. bus route'" "$PW theme.spec.js --grep D5d" "$PW pins.spec.js --grep 'P4c|P4d'"
  "$PW busroute.spec.js --grep 'A7c|A7f'" "$PW chrome.spec.js --grep D1j")

# ---- M0: the control. Every gate below, once, on the tree as committed. MUST SURVIVE. ----
cat > "$WORK/a" <<'A'
const BUS_MARKER_ZOOM = 13;
A
cp "$WORK/a" "$WORK/r"
run M0 frontend/helpers.js "${CONTROL_GATES[@]}"

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
# (Gated on the Rail state since the landing ruling: buses born at the City landing are drawn and
# reachable without the sweep, so the sweep's absence shows where it would have hidden them.)
run M7b frontend/systems/shared.js "$PW a11y.spec.js --grep 'buses undrawn at Rail'"

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

# ---- M13 to M15: the three measuring sentinels made to read where a bus is NOT drawn ----
# Before the landing ruling these rows removed the move to City, which put the reading back at the
# opening zoom 12. The map now LANDS at City, so removing the move reads a drawn bus and proves
# nothing; the defect each guard exists for is a reading where the bus is undrawn, so each row now
# moves the reading to Rail instead.
cat > "$WORK/a" <<'A'
  await placeView(page, "view-city");

  // Markers are sampled by system rather than exhaustively: they share one rule, and
A
cat > "$WORK/r" <<'R'
  await placeView(page, "view-rail");

  // Markers are sampled by system rather than exhaustively: they share one rule, and
R
run M13 tests/e2e/layout.spec.js "$PW layout.spec.js --grep A4b"
cat > "$WORK/a" <<'A'
  // At City, because a bus is not drawn below it (follow-up 1) and this reads the drawn mark.
  await pressView(page, "view-city");
A
cat > "$WORK/r" <<'R'
  // At City, because a bus is not drawn below it (follow-up 1) and this reads the drawn mark.
  await pressView(page, "view-rail");
R
run M14 tests/e2e/families.spec.js "$PW families.spec.js --grep D4f"
cat > "$WORK/a" <<'A'
  // At City, for D4f's reason: a dimmed bus is only a treatment where the bus is drawn.
  await pressView(page, "view-city");
A
cat > "$WORK/r" <<'R'
  // At City, for D4f's reason: a dimmed bus is only a treatment where the bus is drawn.
  await pressView(page, "view-rail");
R
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

# ---- M17: smoke 7 clicks a bus where it is not drawn (Rail; the landing ruling put City at load) ----
cat > "$WORK/a" <<'A'
     would have to go to do the same. buszoom.spec.js D7b is what says a click cannot land below. */
  await pressView(page, "view-city");
A
cat > "$WORK/r" <<'R'
     would have to go to do the same. buszoom.spec.js D7b is what says a click cannot land below. */
  await pressView(page, "view-rail");
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

# ROWS M19 TO M25 ARE THE REVIEW'S: one per defect it found and this follow-up repaired, each
# written as the defect itself so the row dies only if the repair holds.

# ---- M19: a fly cut short no longer repaints the band (the moveend listener removed) ----
cat > "$WORK/a" <<'A'
map.on("moveend", () => {
  if (document.documentElement.getAttribute("data-zoom") !== String(Math.round(map.getZoom()))) paintZoomBand();
});
A
printf '' > "$WORK/r"
run M19 frontend/systems/shared.js "$PW buszoom.spec.js --grep D7h"

# ---- M20: theme.spec.js reads its families where the bus is not drawn (Rail) ----
cat > "$WORK/a" <<'A'
  await placeView(page, "view-city");
}
A
cat > "$WORK/r" <<'R'
  await placeView(page, "view-rail");
}
R
run M20 tests/e2e/theme.spec.js "$PW theme.spec.js --grep D5d"

# ---- M21: P4c measures where the bus is not drawn (Rail) ----
cat > "$WORK/a" <<'A'
  await busZoomViews.placeView(page, "view-city");
  const measured = {};
A
cat > "$WORK/r" <<'R'
  await busZoomViews.placeView(page, "view-rail");
  const measured = {};
R
run M21 tests/e2e/pins.spec.js "$PW pins.spec.js --grep P4c"

# ---- M22 and M22b: the rule widened to every other marker as a FADE rather than a hide ----
cat > "$WORK/a" <<'A'
:root[data-bus-band="hidden"] .bus-marker {
  display: none;
}
A
cat > "$WORK/r" <<'R'
:root[data-bus-band="hidden"] .bus-marker {
  display: none;
}
:root[data-bus-band="hidden"] .leaflet-marker-icon:not(.bus-marker) {
  opacity: 0 !important;
}
R
run M22 frontend/style.css "$PW pins.spec.js --grep P6b"
run M22b frontend/style.css "$PW buszoom.spec.js --grep D7a"

# ---- M23: a route focus that hides every bus, drawn but unreachable at City ----
cat > "$WORK/a" <<'A'
    paintSubwayFocusReach(record);
  }
}
A
cat > "$WORK/r" <<'R'
    paintSubwayFocusReach(record);
  }
  if (focused.length) {
    for (const bus of buses.values()) {
      const el = bus.marker.getElement();
      if (el) { el.setAttribute("aria-hidden", "true"); el.style.pointerEvents = "none"; }
    }
  }
}
R
run M23 frontend/systems/subway.js "$PW buszoom.spec.js --grep D7f"

# ---- M24: the Key row's clause kept in the markup and taken off the page ----
cat > "$WORK/a" <<'A'
        Bus (arrow points where it's heading); shown from City zoom
A
cat > "$WORK/r" <<'R'
        Bus (arrow points where it's heading)<span hidden>; shown from City zoom</span>
R
run M24 frontend/index.html "$PW buszoom.spec.js --grep D7e"

# ---- M25: A7c clicks a bus where it is not drawn (Rail) ----
cat > "$WORK/a" <<'A'
  await pressView(page, "view-city");
  const id = await firstBusId(page);
  const before = ctx.counts.busRoute ?? 0;
A
cat > "$WORK/r" <<'R'
  await pressView(page, "view-rail");
  const id = await firstBusId(page);
  const before = ctx.counts.busRoute ?? 0;
R
run M25 tests/e2e/busroute.spec.js "$PW busroute.spec.js --grep A7c"

# ROWS M26 AND M27 ARE THE LANDING RULING'S: the map lands at the City preset, pressed.

# ---- M26: the map opens where it used to, zoom 12 over lower Manhattan ----
cat > "$WORK/a" <<'A'
}).setView(LANDING_PRESET.center, LANDING_PRESET.zoom);
A
cat > "$WORK/r" <<'R'
}).setView([40.7128, -74.006], 12);
R
run M26 frontend/systems/shared.js "$PW buszoom.spec.js --grep D7i"

# ---- M27: the map lands at City and the City button does not say so ----
cat > "$WORK/a" <<'A'
let activeView = LANDING_PRESET.id;
A
cat > "$WORK/r" <<'R'
let activeView = null;
R
run M27 frontend/systems/shared.js "$PW buszoom.spec.js --grep D7i"

# ROW M28 IS THE REBASE ONTO #122's: the landing and #122's hub names, together.

# ---- M28: the City preset, and so the landing, at zoom 14, the band where every name shows ----
cat > "$WORK/a" <<'A'
  { id: "view-city", center: [40.7295, -73.99], zoom: 13 },
A
cat > "$WORK/r" <<'R'
  { id: "view-city", center: [40.7295, -73.99], zoom: 14 },
R
run M28 frontend/systems/shared.js "$PW buszoom.spec.js --grep D7j"

# ---- M0z: the closing control, the same identity and every gate again. MUST SURVIVE. ----
cat > "$WORK/a" <<'A'
const BUS_MARKER_ZOOM = 13;
A
cp "$WORK/a" "$WORK/r"
run M0z frontend/helpers.js "${CONTROL_GATES[@]}"

echo "================================================================"
echo "sha: $(git rev-parse "$SHA")"
echo "died: $died   survived: $survived   run failed: $broke   wrong verdicts: $wrong"
printf '  %s\n' "${names[@]}"
# A RUN FAILED means the table did not execute (rule 6). A dead control or a surviving row means it
# executed and disagreed with what this file claims.
[ "$broke" -eq 0 ] || exit 2
[ "$wrong" -eq 0 ] || exit 1
exit 0
