// Shared map infrastructure for the ordered-script frontend: the Leaflet map
// and every layer group, the toggle wiring, the status line, the reusable station
// popup machinery (used by subway, railroad and PATH stations), the service-alert
// index and banner, and the shared train-animation loop. Loaded as a plain
// <script> right after helpers.js and before the per-system files, so its
// top-level const/let bindings are in the shared global scope they all read (the
// same buildless model helpers.js -> map.js already uses; no bundler).

/* ---------------- A2: motion ---------------- */

// THE PRINCIPLE, and every gate below serves it: REDUCED MOTION CHANGES HOW A POSITION
// UPDATES, NEVER WHAT IS SHOWN. A gliding train and a stepping train are at the same
// place at the same time; one interpolates between polls and the other jumps when the
// truth arrives. Nothing behind this gate may hide a marker, skip a poll, freeze data,
// or change a single word of text. If a change would make the map SAY something
// different rather than MOVE differently, it does not belong here.
//
// Read once here for Leaflet, because Leaflet reads these options at construction and
// has no supported way to change them afterwards; watchMotionPreference in helpers.js
// carries the same limitation in full, and the README states it for riders.
const motionAtLoad = motionAllowed();

const map = L.map("map", {
  zoomAnimation: motionAtLoad,
  fadeAnimation: motionAtLoad,
  markerZoomAnimation: motionAtLoad,
  // MR1: the zoom control is added below instead, at bottom right, under the view-preset
  // stack (design section 3). It used to sit top left, which is why the old alert strip
  // carried `left: 54px` to clear it: before that offset, elementFromPoint over zoom-in
  // returned the banner and the map could not be zoomed while any alert was showing. The
  // header now owns the top edge, so the control moves out from under it rather than the
  // chrome being nudged around it.
  zoomControl: false,
}).setView([40.7128, -74.006], 12);
L.control.zoom({ position: "bottomright" }).addTo(map);

// Everything this app owns follows the preference LIVE. One class on the root element
// drives every css transition (see the reduced-motion rules in style.css), and one flag
// drives the glide in animateTrains, so a rider who turns the preference on mid-session
// is believed immediately rather than at their next reload.
let motionOn = motionAtLoad;

function applyMotionPreference(allowed) {
  motionOn = allowed;
  document.documentElement.classList.toggle("reduced-motion", !allowed);
}

// THE PAN IS NOT A CONSTRUCTOR OPTION, and that is exactly why the first version of
// this gate missed it. Leaflet has no map-level switch for pan animation the way it has
// for zoom and fade: panBy animates unless a caller passes animate:false, and the
// callers that matter are inside Leaflet. Popup._adjustPan calls map.panBy with no
// options whenever an opening popup would overflow the viewport, which the A2
// cross-link triggers on purpose, so opening a popup near an edge slid the entire map
// (tiles, every marker, every route line) for ~280ms. The review measured it as
// IDENTICAL with the preference on and off: 13 distinct map centres either way. That is
// full-field motion, and it is a larger motion source than the train glide the gate
// already stops.
//
// Wrapping panBy is the narrowest place that covers every caller, ours and Leaflet's
// own, including panTo and the animated branch of setView, which both route through it.
// It changes only HOW the map arrives at a position, never WHICH position: an
// unanimated pan lands on exactly the same centre.
//
// A4 ROUND 1: ANIMATE THE JOURNEY, NEVER THE ADJUSTMENT.
//
// This is the principle the map's motion now follows, and it splits the pans into two kinds
// that had been treated as one:
//
//   A JOURNEY is navigation the rider chose. Picking a station in the panel pans the map to
//   it (syncMapToStation's panTo), and the motion there carries CONTINUITY: it shows the
//   rider that this new place is that old place, moved. Journeys keep their preference gate,
//   animated unless the rider asked for reduced motion. A5g and A5h pin that pair.
//
//   AN ADJUSTMENT is the app correcting its own fit. Leaflet's _adjustPan nudging an opening
//   popup back inside the viewport is one, and so is this phase's move of a popup out from
//   under the legend. Nobody asked for it, it carries no continuity, and its ENDPOINT MUST BE
//   KNOWABLE: the app's own occlusion logic reads where the popup came to rest, and it cannot
//   read a position that is still moving. Adjustments are instant for everyone.
//
// So Leaflet's autoPan is unanimated regardless of preference, which is a rider-visible
// change from A2 and is deliberate. Measured at 1280 with the placed railroad popup:
//
//     real clock   popup settles at x 1001..1276, overlapping the legend at 1030
//     fixed clock  popup never lands at all, x 1288..1563, off the map's right edge
//
// The second row is the one that makes this structural rather than aesthetic: PosAnimation
// drives itself off `+new Date()`, so under a fixed clock (which the accessibility gate
// needs for deterministic ages) the animation never completes and the map is left mid-slide
// forever. Instant is the only setting under which the popup has a position at all, for the
// app's occlusion logic first and for any test second.
let leafletAutoPanning = false;
map.on("autopanstart", () => {
  leafletAutoPanning = true;
});
/* THE FLAG IS CLEARED AFTER THE PAN, NOT BEFORE IT, and that one line's placement is a
   defect the round-3 stand-down guard created and round 3 measured. panBy fires `movestart`,
   and the stand-down guard reads this flag inside its movestart handler to tell Leaflet's own
   adjustment apart from the rider's hand. Clearing it first meant the flag was already false
   by the time the pan it describes announced itself, so every autopan was filed as the rider
   taking over. Measured at 375: after Leaflet autopanned an overflowing popup back into
   view, a later arrivals refresh that pushed it under the legend was declined. The app
   thought the position was the rider's, and it was the app's own. A4l pins it. */
const leafletPanBy = map.panBy.bind(map);
map.panBy = (offset, options) => {
  const instant = !motionOn || leafletAutoPanning;
  try {
    return leafletPanBy(offset, instant ? { ...(options || {}), animate: false } : options);
  } finally {
    leafletAutoPanning = false;
  }
};

applyMotionPreference(motionAtLoad);
watchMotionPreference(applyMotionPreference);

/* ----- A3, EXTENDED BY MR1: the Key disclosure, and the one place the breakpoint is read
   -----------------------------------------------------------------------------------------
   The rule A3 wrote still holds: the breakpoint is read HERE rather than being split between
   a CSS rule and a click handler, because the two would drift. A rider who rotates a phone
   into landscape crosses 700px without any click, and the version of this that only listened
   for clicks left the legend hidden on a screen with room for it.

   ARIA AND THE ATTRIBUTE ARE STILL SET TOGETHER, always, so what a screen reader is told and
   what is drawn cannot disagree. FOCUS STILL STAYS ON THE BUTTON: the panel expands in place,
   so there is nowhere to send focus and nothing is destroyed.

   WHAT MR1 CHANGES, and it is a real behaviour change rather than a restyle:

   THE KEY IS A TOGGLE AT EVERY WIDTH NOW. The design's row 1 carries a Key button at every
   width and its panel is closed until pressed (README section 1, and the 05-key-open
   capture is a 1280px screen with it open). Before this, the button was display:none above
   700px and the legend was unconditionally open there. So `open` is now one boolean the
   rider owns rather than one the viewport decides, and mobile.spec.js's A6c moved with it.

   AND BELOW 700px THE SAME BOOLEAN FOLDS THE REST. The brief: "the subway key and the feed
   strip fold behind the Key button, leaving one row". One boolean, one aria-expanded, and
   everything that folds folds together, which is what makes the button's name honest: Key
   reveals the key AND the things that were folded to make room for it.

   A CONSEQUENCE WORTH STATING RATHER THAN DISCOVERING: the feed strip carries the status
   note, so below 700px the note folds with it. Today's status line does not fold (it is a
   sibling of #legend, not a child). The accessible path is unchanged either way, because
   #page-announce speaks every status transition and is never folded; what changes is that a
   sighted rider on a phone reads the note after one tap instead of at a glance. The v3.1
   amendment carved the ALERTS strip out of the fold and did not carve this out, so MR1
   implements it as specified; it is recorded as a finding in docs/reviews/map-redesign-rounds.md
   for the operator to rule on before MR2. */
const legendToggleEl = document.getElementById("legend-toggle");
const legendEl = document.getElementById("legend");
// Everything that folds behind the Key button below the breakpoint. The alerts strip is
// deliberately NOT in this list and must never be given the class: that is mutation M3.
const foldEls = [...document.querySelectorAll(".hdr-fold")];
const viewStackEl = document.getElementById("view-stack");
let keyOpen = false;

function applyHeaderDisclosure() {
  if (!legendToggleEl || !legendEl) return;
  const narrow = narrowViewport();
  /* WHAT IS ABOUT TO STOP EXISTING FOR THE RIDER, ASKED BEFORE IT DOES (round 2). Focus
     stays on the button when the rider presses it, which is A3's rule and still true; what
     that rule never covered is focus INSIDE what is being folded. Two ways to reach it: Tab
     to a feed toggle on a phone and press Key, or have focus in the strip when a resize
     crosses the breakpoint, which needs no press at all. Either way the element holding
     focus is hidden and the browser drops the rider on <body>, which this app treats as a
     defect everywhere else it can happen (applyVanishingFocus, and the popup and banner
     doors that use it).
     The Key button is where focus goes, because it is the control that did it and the one
     that undoes it. Silent, deliberately: the rider pressed a disclosure and the disclosure
     closed, which is not news, and #page-announce is for things they did not do. */
  /* AND THE VIEW STACK COUNTS AS FOLDING, because below the breakpoint it stands down while
     the Key is open (style.css says why: at 320 the presets painted over four rows of the
     key). It is hidden by a CSS rule rather than by the `hidden` attribute, so it is not in
     foldEls, and a rider who tabs to City and presses Key would have been dropped on <body>
     by a display:none they did not ask for. */
  const standingDown = narrow && !keyOpen ? [] : narrow ? [viewStackEl] : [];
  const folding = [legendEl, ...(narrow ? foldEls : []), ...standingDown];
  const losingFocus =
    document.activeElement &&
    folding.some((el) => el && el !== document.activeElement && el.contains(document.activeElement));
  legendEl.hidden = !keyOpen;
  for (const el of foldEls) el.hidden = narrow && !keyOpen;
  legendToggleEl.setAttribute("aria-expanded", String(keyOpen));
  if (losingFocus) legendToggleEl.focus();
}

if (legendToggleEl) {
  legendToggleEl.addEventListener("click", () => {
    keyOpen = !keyOpen;
    applyHeaderDisclosure();
  });
}
applyHeaderDisclosure();
if (typeof matchMedia === "function") {
  const mql = matchMedia(MOBILE_QUERY);
  if (mql.addEventListener) mql.addEventListener("change", applyHeaderDisclosure);
}

/* ----- MR1: the theme ----------------------------------------------------------------
   data-theme on the root element, which is where index.html already writes "light" so the
   page renders in a complete theme before this file runs. style.css defines one token set
   per value and one basemap filter per value; nothing else in the app reads the attribute.

   PERSISTED, AND THE READ AND THE WRITE ARE BOTH GUARDED. localStorage throws rather than
   returning null in a private window with site data blocked, and a throw here would kill
   this file and every script after it, which is the whole page. So both directions are in
   try/catch and an empty or refused store leaves the markup's own "light" standing, which
   is the state the page is authored in. Mutation M5 is the theme not persisted.

   NO MARKER IS REBUILT. The prototype tears the map down and rebuilds it at the same view
   on a theme change, because its marker SVGs embed the ink and paper tokens. MR1's markers
   are today's markers and embed none of them, so there is nothing to rebuild; the stage
   that gives a marker a token is the stage that owes the rebuild. The P1f to P1n pins are
   what keep that claim honest. */
const THEME_KEY = "nyc-transit-live.theme";
const themeToggleEl = document.getElementById("theme-toggle");

const storedTheme = () => {
  try {
    const value = localStorage.getItem(THEME_KEY);
    return value === "dark" || value === "light" ? value : null;
  } catch {
    return null; // private window, blocked site data: no stored preference, not a crash
  }
};

/* ----- MR4: the canvas families, and the swap that reaches them -------------------------

   THE ONE POPULATION A THEME CHANGE CANNOT REACH BY ITSELF. A divIcon is HTML, so a mark
   that says `style="fill: var(--paper)"` follows a theme swap through the cascade for free
   and needs nothing here; every MR4 vehicle mark is built that way on purpose. A canvas
   layer cannot: Leaflet hands a colour STRING to the 2D context, so a polyline or a
   circleMarker drawn from paperColor() or inkColor() keeps the colour it was drawn with
   until something sets it again. That is what this registry is for, and it is the debt MR3
   named beside rootToken rather than left for this stage to discover.

   A REGISTRY AND NOT A LIST OF CALLS, because a list is a thing to forget. Each family
   registers the function that PAINTS it, the draw path calls that same function, and
   applyTheme calls all of them; so "what colour is this mark" and "what colour does it
   become" are one expression per family rather than two that can drift. A family added in a
   later stage that forgets to register is caught by frontend/families.test.js, which scrapes
   every token-reading canvas style site out of systems/ and asserts each one's family is
   here: the assertion is against the SOURCE rather than against a list written by hand,
   which is what the operator asked for and what stops the test being a second copy of the
   registry.

   setStyle AND NEVER A REBUILD. The layers keep their geometry, their renderer, their
   identity and their place in their layer group; one number per option changes. The
   prototype tears the map down and rebuilds it at the same view, which would re-fetch
   nothing but would destroy every popup a rider is holding open, drop every marker's
   accessible name and reset the glide. The e2e counts markers either side of a swap for
   exactly that reason. */
const canvasThemeFamilies = [];

// `paint` takes { paper, ink } and restyles its family in place. The return value is the
// function itself so a caller can register and keep one reference in a single expression.
function registerCanvasFamily(name, paint) {
  canvasThemeFamilies.push({ name, paint });
  return paint;
}

// The tokens every canvas family is drawn from, read once per swap rather than once per
// layer: getComputedStyle is the expensive half and the answer cannot change mid-sweep.
function canvasThemeTokens() {
  return {
    paper: paperColor(),
    ink: inkColor(),
    scheduled: scheduledColor(),
    busLightness: busMarkLightness(),
  };
}

/* Every family whose paint threw on the last swap, newest run only. A rider is told nothing:
   there is no action for them in it, and a theme press that half-worked is still better than
   one that threw. A TEST is told, which is the point: theme.spec.js asserts this is empty after
   a swap, so a family that quietly keeps the previous theme's colours fails a gate instead of
   shipping. */
const canvasThemeFailures = [];

function repaintCanvasFamilies() {
  const tokens = canvasThemeTokens();
  canvasThemeFailures.length = 0;
  for (const family of canvasThemeFamilies) {
    try {
      family.paint(tokens);
    } catch (err) {
      /* ONE FAMILY MAY NOT TAKE THE SWAP DOWN WITH IT, which is why this is caught at all: a
         loader still in flight on a cold start, or a layer whose renderer was torn down, would
         otherwise leave every family after it in the previous theme.

         AND THE COMMENT THAT USED TO BE HERE WAS FALSE, which the round 1 review found: it said
         "the next draw reads the live token anyway, so a miss here is repainted by the load
         that follows it". After load there IS no next draw. loadSubwayStations, drawRibbons,
         railDrawRibbons, loadPathStops, loadFerryStops and loadAirtrain each draw once per page
         and never redraw, so a family that throws here keeps the theme it was drawn under until
         the page is reloaded, which is the 1.11-to-2.63-against-dark-paper state ruling R2 held
         the toggle back for. Recorded rather than recovered, because there is nothing here that
         could recover it. */
      canvasThemeFailures.push({ name: family.name, message: String(err && err.message) });
    }
  }
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  if (themeToggleEl) {
    /* THE LABEL IS THE ACTION, AND THERE IS NO SECOND ANSWER (round 2). This carried
       aria-pressed as well, and the two contradicted each other out loud: in the dark theme
       the button read "Light" and reported pressed, so a screen reader said "Light, pressed",
       which states that the light theme is on while the page is dark. The toggle-button
       pattern aria-pressed belongs to is one whose label does NOT change with the state, and
       the design's label does: it is "Dark" and "Light", the thing pressing it will do.
       So the name says what the press does, and nothing claims a state to disagree with it.
       A rider does not learn the current theme from this button, and did not before either:
       they learn it from the page, and the button tells them where the press leads. */
    themeToggleEl.textContent = theme === "dark" ? "Light" : "Dark";
    themeToggleEl.removeAttribute("aria-pressed");
  }
  /* AND EVERY CANVAS MARK IS REPAINTED, which is MR4's half of the theme. The attribute
     above swaps the tokens for everything that reads them through the cascade; this reaches
     the marks that resolved a token to a string when they were drawn. It runs AFTER the
     attribute, because the repaint reads the tokens the attribute just chose.

     THE REGISTRY IS DECLARED ABOVE applyTheme AND NOT BELOW IT, which is not tidiness: this
     block ends by CALLING applyTheme to apply the stored preference, and a module-scope const
     is in the temporal dead zone until its own line runs, so a registry declared after that
     call would throw on page load and take every script after this one with it. This file has
     already lost the whole page that way once, which is why the same sentence is written
     above namesToggleEl. On that first call the array exists and is empty, so nothing is
     repainted, which is correct: no system file has drawn anything yet. */
  repaintCanvasFamilies();
  /* MR5: AND EVERY OPEN POPUP BOUND AS A FUNCTION IS REBUILT, for exactly the reason above one
     surface further out. A popup's markup is HTML and follows the tokens through the cascade, with
     one exception: the six heads that print a route colour as text resolve popupSurfaceColor() to a
     STRING when the popup is built (readableInk needs a background, not a variable). So a popup
     built in the light theme keeps light-theme ink on a dark surface until something rebuilds it,
     and for a VEHICLE popup that something would otherwise be the next fifteen-second poll.

     WHICH POPUPS THIS REACHES, stated because the first version of this comment claimed it reached
     all of them and a reviewer read Leaflet's own source against it. popup.update() re-invokes the
     bound content only where that content IS a function (`"function" == typeof this._content`), so
     it rebuilds every vehicle popup and the AirTrain station popup. The five ticking station boards
     are bound with a STRING and filled by setPopupContent, so update() re-sets the identical string
     and changes nothing. That is not a hole: openStationArrivals runs renderStation on a one-second
     interval, so a station board picks the new theme up within a second on its own, which is also
     why the old claim ("nothing at all for a station") was false in the other direction.
     tests/e2e/popups.spec.js D6h measures the case this call is for, a rail train's popup.

     Guarded because this function runs once at load, before the map exists. */
  rebuildOpenPopupsForTheme();
}

applyTheme(themeChoice(storedTheme(), document.documentElement.getAttribute("data-theme")));

/* ----- MR2: the two theme colours the canvas cannot read -------------------------------

   A divIcon is HTML, so a mark drawn there says `style="fill: var(--paper)"` and follows a
   theme swap through the cascade at no cost. A canvas layer cannot: Leaflet hands a colour
   STRING to the 2D context, and `var(--paper)` is not one. So the two tokens every MR2 mark
   is drawn from are resolved here, once per call, from the root the theme is written on.

   THIS IS WHAT MAKES MR4'S SWAP A setStyle RATHER THAN A REBUILD. The ribbons and the
   station circles keep their geometry and take new colours; nothing is torn down and
   nothing is re-fetched. The fallbacks are the light theme's literals, for the one case
   where the stylesheet has not applied yet (a test that renders the markers with no
   document styles, which frontend/boards.test.js does).

   MR3 ADDED A THIRD POPULATION TO THAT SWAP: the three commuter rail families' 5px paper
   casings, on railroadCasingPane. railDrawRibbons resolves paperColor() once per draw, the
   same way drawRibbons does, so a casing drawn under the light theme keeps its light paper
   until something restyles it. That is not a defect in this stage, because the theme toggle
   is still `hidden` until MR4 unhides it (round 3's R2), and it IS one more layer group MR4's
   swap has to reach. Named here rather than left for MR4 to discover.

   Read live rather than cached, because a cache would be a second copy of the theme and the
   whole point of a token is that there is one. */
function rootToken(name, fallback) {
  try {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  } catch {
    return fallback;
  }
}

function paperColor() {
  return rootToken("--paper", "#f3f2f2");
}

function inkColor() {
  return rootToken("--ink", "#201e1d");
}

/* MR4: the third token a canvas has to be told. The design gives the AirTrain guideway a gray
   that MOVES with the theme (`#6d6e71` light, `#9a9a9a` dark), and style.css has carried both
   values as `--scheduled` since MR1 with no canvas reader. A polyline cannot resolve it, so
   it is resolved here beside paper and ink and handed to the registry with them. */
function scheduledColor() {
  return rootToken("--scheduled", "#6d6e71");
}

/* MR5: THE FOURTH TOKEN A STRING-BUILDER HAS TO BE TOLD, and this one is not about a canvas.

   Six popup heads print a route's colour as TEXT, through readableInk, which walks the colour
   toward legibility until it clears 4.5:1 against the background it is given. That background
   has always been readableInk's default, `#ffffff`, and until this stage the assumption behind
   it was true: a Leaflet popup is white. Section 5 makes it --surface, so it is not, and in the
   DARK theme it never was. (Section 5 asks for that surface at 94%; the erratum beside it records
   why the popup ships opaque, and the arithmetic below is written for the surface either way.)

   MEASURED, WHICH IS WHY THIS EXISTS AT ALL. layout.spec.js A4g renders the N train, whose
   #e6b800 the helper walks to rgb(138, 110, 0): against white that clears, and against this popup's
   surface it reads 4.02. (Against the composite the DESIGN asked for it would have read 4.05, which
   is the comparison the next paragraph turns on; the popup ships opaque, so no composite is drawn
   anywhere and the 4.02 is the number.) A4g caught it on the commit that changed the surface, which
   is the gate doing its job.

   --surface, WHICH IS NOW THE WHOLE ANSWER AND WAS ALWAYS THE PESSIMISTIC END. The popup ships
   opaque, so the surface IS what the ink is printed on and there is no composite left to reason
   about. It was the right background before that too: at the design's 94% the popup was
   --surface over --bg, and --surface is the darker of the two in the light theme and the lighter
   in the dark one, so in either case it gives dark ink and light ink respectively the least to
   work with. Ink that clears 4.5 against it cleared the composite as well.

   AND AN OPEN POPUP IS RE-RENDERED WHEN THE THEME CHANGES, in applyTheme below, for the same
   reason the canvas families are repainted there: this resolves a token to a STRING at build
   time, so a popup built under one theme keeps that theme's ink until something rebuilds it. */
function popupSurfaceColor() {
  return rootToken("--surface", "#eae9e9");
}

/* MR4 ROUND 1: THE BUS WHEEL'S LIGHTNESS AS A NUMBER, for the one bus colour a cascade cannot
   reach. The MARK is HTML and takes `var(--bus-mark-lightness)` in an inline style; the clicked
   route LINE is a canvas polyline, so Leaflet hands its colour to the 2D context as a string and
   a custom property is not one. Same token, read here and resolved at draw, so the arrow and the
   line a rider draws by clicking it are the same colour in either theme. The fallback is the
   README's light value, as the token's own is. */
function busMarkLightness() {
  const raw = rootToken("--bus-mark-lightness", `${BUS_MARK_LIGHTNESS}%`);
  const parsed = Number.parseFloat(String(raw));
  return Number.isFinite(parsed) ? parsed : BUS_MARK_LIGHTNESS;
}


if (themeToggleEl) {
  themeToggleEl.addEventListener("click", () => {
    const next = nextTheme(document.documentElement.getAttribute("data-theme"));
    applyTheme(next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      // Nothing to do and nothing to say: the theme is applied, it just will not survive a
      // reload. Telling a rider their browser refused a write would be noise about a
      // preference they can set again in one press.
    }
  });
}

/* ----- MR1: the clock --------------------------------------------------------------------
   HH:MM:SS in the rider's own locale, the same formatter the status line's clock used, so
   the two never disagreed about what time it is and still do not.

   THE DIGITS TICK UNDER REDUCED MOTION; THE DOT DOES NOT. The preference is about movement,
   and a clock that stopped would be a lie rather than a kindness. The blink is a CSS
   animation turned off by the .reduced-motion class (style.css), which is the one door
   applyMotionPreference writes, so it follows the preference live like every other animation
   this app owns rather than only at load. That gate is what mutation M6 reverts. */
const clockTimeEl = document.getElementById("clock-time");
if (clockTimeEl) {
  const tickClock = () => {
    clockTimeEl.textContent = new Date().toLocaleTimeString();
  };
  tickClock();
  setInterval(tickClock, 1000);
}

/* ----- MR1: the subway colour key ------------------------------------------------------
   Grouped by trunk, and DISPLAY ONLY: route focus arrives with the subway restyle in MR2,
   when these become buttons that do something. Spans until then, because a focusable
   control that does nothing is a tab stop a rider pays for and gets nothing back, which is
   what A1y's walk invariants are for.

   BUILT FROM lineColor() AND readableTextOn(), NOT FROM THE DESIGN'S TABLE, and this is the
   one place MR1 knowingly departs from the drawing. The design gives the official MTA trunk
   palette; this repository's standing ruling is the opposite ("The MTA's logos, official
   map, and route symbols require a license. Use your own colors and markers rather than
   official MTA branding", README Notes), and LINE_COLORS exists because of it. A key in one
   palette over lines and markers drawn in another would also be simply wrong: the key's job
   is to say which colour on THIS map is which route. So it reads the palette the map draws
   with, and it changes when MR2 changes that palette, whichever way the operator rules.
   Measured, every trunk clears 4.5:1 through readableTextOn: the lowest is 4.72 on the
   4-5-6 green and the L's grey takes dark ink at 5.00 where white would have been 3.48.
   docs/reviews/map-redesign-rounds.md carries the ruling question as a finding. */
/* ----- MR2 round 2: the key, derived and focusable ---------------------------------------

   MR1 hard-coded ten trunks and twenty-three bullets, and MR2 round 2 measured that table
   against the real static archive and found it wrong in both directions: three of its
   bullets drew nothing and four drawn routes had no bullet. helpers.js now derives the
   whole thing (subwayKeyModel, and the long comment there says how); this builds it.

   IT IS AN ARIA TOOLBAR WITH A ROVING TABINDEX, which is the other half of the same round.
   Twenty-three buttons in the header cost twenty-three tab stops: reaching the Stations
   button took 32 presses where it took nine before. A toolbar is one tab stop, and the
   arrow keys move inside it, which is the pattern for a row of related controls and the
   reason a rider is not made to walk the subway system to reach a button.

   BUILT WHEN THE DATA ARRIVES, NOT AT MODULE SCOPE. The route list is fetched, so at load
   there is nothing to derive a key from. refreshSubwayKey is called when the routes resolve
   and from the poll tail; it REBUILDS only when the universe of bullets changes and
   otherwise just repaints, so a key does not twitch under a rider's hand every fifteen
   seconds. */
const subwayKeyEl = document.getElementById("subway-key");
const subwayKeyBullets = new Map(); // bullet id -> its button
let subwayKeyUniverse = ""; // the signature of the bullets currently drawn
let subwayKeyFocusSets = new Map(); // bullet id -> the route ids it focuses

// Whatever the map can currently draw a train for. The loaded route list is subway.js's
// (routeIndex), and it loads after this file, so both are read late and by name.
function subwayTrainRoutes() {
  if (typeof trains === "undefined") return [];
  return [...new Set([...trains.values()].map((record) => record.latest?.route_id).filter(Boolean))];
}

function subwayRouteList() {
  if (typeof routeIndex === "undefined") return [];
  return [...routeIndex.entries()].map(([route, variants]) => ({ route, polylines: variants }));
}

/* THE ROVING TABINDEX. Exactly one bullet is in the tab order at a time: the focused route's
   if there is one, else the first enabled bullet. Everything else is tabbable only from
   inside, with the arrow keys. */
function paintRovingTabindex() {
  const bullets = [...subwayKeyBullets.values()];
  if (!bullets.length) return;
  const enabled = bullets.filter((b) => b.getAttribute("aria-disabled") !== "true");
  const pressed = bullets.find((b) => b.getAttribute("aria-pressed") === "true");
  const stop = pressed ?? enabled[0] ?? bullets[0];
  for (const bullet of bullets) bullet.tabIndex = bullet === stop ? 0 : -1;
}

function moveKeyFocus(from, delta) {
  const bullets = [...subwayKeyBullets.values()].filter((b) => b.getAttribute("aria-disabled") !== "true");
  if (!bullets.length) return;
  const at = bullets.indexOf(from);
  const next =
    delta === "home" ? bullets[0]
    : delta === "end" ? bullets[bullets.length - 1]
    : bullets[(at + delta + bullets.length) % bullets.length];
  for (const bullet of bullets) bullet.tabIndex = bullet === next ? 0 : -1;
  next.focus();
}

if (subwayKeyEl) {
  /* THE ONE KEYDOWN THIS FILE OWNS, and it is a control's own activation rather than a
     router: map.js has the page's only key router and frontend/keyboard.test.js fails on a
     second one. This is scoped to the toolbar, it handles only the five keys a toolbar owes,
     and it is named in that test's table with this reason. */
  subwayKeyEl.addEventListener("keydown", (event) => {
    const bullet = event.target.closest?.(".bul");
    if (!bullet || !subwayKeyEl.contains(bullet)) return;
    const move =
      event.key === "ArrowRight" || event.key === "ArrowDown" ? 1
      : event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1
      : event.key === "Home" ? "home"
      : event.key === "End" ? "end"
      : null;
    if (move === null) return;
    event.preventDefault();
    moveKeyFocus(bullet, move);
  });
}

function buildSubwayKey(model) {
  if (!subwayKeyEl) return;
  subwayKeyEl.replaceChildren();
  subwayKeyBullets.clear();
  for (const group of model) {
    const groupEl = document.createElement("span");
    groupEl.className = "bul-group";
    for (const entry of group.bullets) {
      /* THE TEXT IS THE BARE BULLET ID AND NOTHING ELSE, deliberately: chrome.spec.js D1l
         reads each bullet's textContent and resolves the colour it expects as
         lineColor(textContent), so a visually hidden label inside the chip would make that
         check compare the wrong thing. The name goes on aria-label, where it does not move
         with the state and can therefore carry aria-pressed; the detail goes on title. */
      const bullet = document.createElement("button");
      bullet.type = "button";
      bullet.className = "bul";
      bullet.style.background = group.color;
      bullet.style.color = readableTextOn(group.color);
      bullet.textContent = entry.id;
      bullet.setAttribute("aria-label", routeFocusLabel(entry.id));
      bullet.setAttribute("aria-pressed", "false");
      bullet.tabIndex = -1;
      bullet.addEventListener("click", () => toggleRouteFocus(entry.id));
      subwayKeyBullets.set(entry.id, bullet);
      groupEl.append(bullet);
    }
    subwayKeyEl.append(groupEl);
  }
  paintSubwayKeyState(model);
}

/* THE ENABLED STATE IS REPAINTED EVERY POLL, because what a bullet can light changes with
   the feed: a W bullet lights nothing at 3am and lights three ribbons at 8am.

   aria-disabled RATHER THAN disabled, and that is the accessibility difference that
   matters: a `disabled` button leaves the tab order and the accessibility tree entirely, so
   a rider who cannot see the key would never learn the route exists. aria-disabled keeps it
   announced, with its title saying why, and the click handler below is what makes it inert. */
function paintSubwayKeyState(model) {
  for (const group of model) {
    for (const entry of group.bullets) {
      const bullet = subwayKeyBullets.get(entry.id);
      if (!bullet) continue;
      bullet.setAttribute("aria-disabled", String(!entry.enabled));
      /* AND THE INLINE CHIP GETS OUT OF THE STYLESHEET'S WAY when the bullet is dark. The
         enabled chip is the route's own colour, written inline because it comes from the data;
         an inline style beats a rule, so the grey the disabled state needs (style.css,
         --chip-off) can only land if these two properties are cleared. Written back when it is
         live again, which is the half that would otherwise leave a route permanently grey. */
      if (entry.enabled) {
        bullet.style.background = group.color;
        bullet.style.color = readableTextOn(group.color);
      } else {
        bullet.style.removeProperty("background");
        bullet.style.removeProperty("color");
      }
      bullet.title = entry.title;
      subwayKeyFocusSets.set(entry.id, entry.focus);
    }
  }
  paintRovingTabindex();
}

// Called when the route list resolves and from the poll tail. Rebuilds only when the set of
// bullets changes; otherwise repaints, so the key does not twitch every fifteen seconds.
function refreshSubwayKey() {
  // The ribbons' tags and the key's sets are two halves of one answer, so they are refreshed
  // together and from the same inputs. Late-bound by name: systems/subway.js loads after
  // this file and owns the marks.
  if (typeof retagSubwayRibbons === "function") retagSubwayRibbons();
  if (!subwayKeyEl) return;
  const model = subwayKeyModel(subwayRouteList(), subwayTrainRoutes());
  const signature = model.map((g) => g.bullets.map((b) => b.id).join("")).join("|");
  if (signature !== subwayKeyUniverse) {
    subwayKeyUniverse = signature;
    buildSubwayKey(model);
    // A rebuild can take the focused bullet away with it; if it does, the focus goes too
    // rather than being held by an id nothing draws.
    if (focusedBullet && !subwayKeyBullets.has(focusedBullet)) clearRouteFocus();
    else paintRouteFocus();
    return;
  }
  paintSubwayKeyState(model);
}

/* ----- MR2: route focus ------------------------------------------------------------------

   ONE PIECE OF STATE, and every surface reads it rather than keeping its own copy: the
   bullets' aria-pressed, the ribbons' opacity and the trains' dimming base. It lives here
   rather than in systems/subway.js because the CONTROLS are here (the bullets, the Escape
   rung's entry point) and the drawing is there, and one of the two has to load first.

   FOCUS IS MEMBERSHIP, not equality (round 2). The state is the BULLET that is pressed and
   the SET of route ids it stands for; a ribbon or a train is focused when its own route is
   in that set. That is what lets Z light the J/Z ribbon and W light all three Broadway
   ones, and helpers.js's subwayKeyModel is where the sets come from.

   IT IS OPACITY AND NOTHING ELSE. No layer is added, removed or rebuilt, which is what
   subway.spec.js D2d holds by comparing layer identity across a focus and a clear. A
   rebuild would also be a correctness problem rather than only a cost: setIcon replaces a
   marker's element, so any focus state written onto the DOM would be lost the next time a
   train's route id changed, while state held in Leaflet's options survives.

   AND IT COMPOSES WITH THE FRESHNESS CONTRACT rather than competing with it. dimMarker and
   markerOpacity already take a `base` multiplier; focus supplies it. Nothing in section 3.3
   is touched, a stale train on the focused route stays dim, and the poll that re-dims every
   marker fifteen seconds later re-reads the focus instead of erasing it. */
let focusedBullet = null;
let focusedRoutes = [];

// The route ids currently focused, or an empty array. systems/subway.js asks this rather
// than comparing ids, so membership is decided in one place.
function currentFocusRoutes() {
  return focusedRoutes;
}

function currentFocusBullet() {
  return focusedBullet;
}

function paintRouteFocus() {
  for (const [id, bullet] of subwayKeyBullets) {
    bullet.setAttribute("aria-pressed", String(id === focusedBullet));
  }
  paintRovingTabindex();
  // Late-bound by name, because systems/subway.js loads after this file and owns the marks.
  if (typeof applySubwayFocus === "function") applySubwayFocus();
}

/* Press a bullet: focus it, or clear it if it was already focused. Pressing a DIFFERENT
   bullet moves the focus rather than clearing.

   A BULLET THAT WOULD LIGHT NOTHING NEVER DIMS THE MAP. That is the whole point of drawing
   it disabled: before round 2, three of twenty-three bullets dimmed the entire map and
   highlighted nothing, which is a control that appears to work and does not. */
function toggleRouteFocus(id) {
  const bullet = subwayKeyBullets.get(id);
  if (bullet && bullet.getAttribute("aria-disabled") === "true") return;
  if (focusedBullet === id) {
    focusedBullet = null;
    focusedRoutes = [];
  } else {
    focusedBullet = id;
    focusedRoutes = subwayKeyFocusSets.get(id) ?? [id];
  }
  paintRouteFocus();
  announcePage(routeFocusAnnouncement(focusedBullet));
}

// The Escape rung's entry point (the ladder is in map.js, and it is the page's only keydown
// ROUTER: frontend/keyboard.test.js fails on a second one, and a second one bound in the
// bubble phase would be inert anyway because the ladder captures and stops the event).
// Returns whether there was anything to clear, so the ladder can tell "handled" from "leave
// the event alone".
function clearRouteFocus() {
  if (!focusedBullet) return false;
  focusedBullet = null;
  focusedRoutes = [];
  paintRouteFocus();
  announcePage(routeFocusAnnouncement(null));
  return true;
}

/* ----- MR2: the label gate and the Names toggle ------------------------------------------

   THE ROOT CARRIES BOTH ATTRIBUTES. data-zoom is the design's, written as the integer zoom
   so a reader or a future rule can find it; data-label-band is labelZoomBand()'s answer and
   is what style.css actually reads. The stylesheet says why the design's enumerated zoom
   list is not safe to depend on.

   WRITTEN ON zoomend AND ONCE AT LOAD, because a map that opens at zoom 13 has never fired
   one. Math.round, because getZoom() is fractional mid-flight and data-zoom is a label for
   a settled state. */
/* DECLARED BEFORE paintZoomBand IS CALLED. A module-scope const is in the temporal dead zone
   until its own line runs, and this file has already taken the whole page down that way once
   this stage; paintZoomBand writes this button's title, so the button is looked up first and
   the first paint happens after the handler below. */
const namesToggleEl = document.getElementById("names-toggle");

function paintZoomBand() {
  const zoom = Math.round(map.getZoom());
  /* Zero hubs with stations present is the degraded backend state helpers.js describes at
     LABEL_NO_HUB_ZOOM, where the band shows every name from 13 instead of showing nothing
     from 12. Before any station has loaded there are none either way, so the first paint is
     unaffected and loadStations calls this again when they arrive. */
  /* THE REGISTRY, NOT THE DOM, AND THAT IS MR4 PAYING MR3's CARRY-FORWARD RATHER THAN
     PATCHING IT AGAIN.

     The two numbers below answer one question: has the SUBWAY loaded, and does it publish any
     interchange. For three stages they were read off the document by counting `.stn-label`,
     which was true only while the subway was the sole family in that class. MR3 put ~300
     commuter-rail labels into it and the count silently changed meaning: a page with rail
     labels and no subway labels read as "subway loaded, zero hubs", which is
     LABEL_NO_HUB_ZOOM's DEGRADED band (every subway name from 13 instead of hubs from 12) on a
     map whose subway index was merely still in flight. MR3 patched it with `:not(.rail)`, and
     MR4 was about to widen the class a third time with the ferry's dock names.

     A `:not()` list that grows with every family is a sentinel that will be wrong again, so
     the question is asked of the thing that actually knows: stationRegistry carries `kind` and
     `routes` per station, `isTransferStation` is the same predicate the hub CLASS is drawn
     from, and neither can be changed by a family joining a CSS class. No count here can be
     corrupted by any later stage's markup.

     try/catch AND NOT typeof, which is the trap this file has fallen into before:
     stationRegistry is a module-scope const in stations.js, which loads AFTER this file, and
     `typeof` on a binding in its temporal dead zone THROWS rather than returning "undefined".
     The first paint runs before stations.js has, and an empty list is the right answer then.

     AND ON THE MAP, WHICH IS THE HALF THE FIRST DRAFT DROPPED. The question is about labels a
     rider can see, and the DOM count this replaced answered it by construction: a tooltip on a
     removed layer is not in the document. A registry query is not on the map by construction,
     so it has to ask. The state that separates them is reachable and was found by the round 1
     review: serve `routes: []` for every subway station (which helpers.js documents as a real
     backend state) and press the Subway feed button OFF. The registry still holds 496 stations
     with no routes between them, so `labels` reads 496 and `hubs` reads 0, which is
     LABEL_NO_HUB_ZOOM's DEGRADED band; the DOM count read 0, so `!labels` kept the ordinary
     one. At zoom 13 those are different answers, and since MR4 the degraded one also reveals
     the ferry's dock names, so a hidden subway layer would have pulled another family's labels
     onto the screen. `map.hasLayer` is the same question the DOM was answering. */
  let subwayStations = [];
  try {
    subwayStations = stationRegistry.filter(
      (entry) => entry.kind === "subway" && map.hasLayer(entry.marker),
    );
  } catch {
    subwayStations = [];
  }
  const labels = subwayStations.length;
  // The COMPLEX, the same object the ring and the hub class were drawn from (subway.js), so a
  // hub here is a hub on the map (claude/subway-hub-definition).
  const hubs = subwayStations.filter((entry) => isTransferStation(entry.complex ?? entry.routes ?? [])).length;
  document.documentElement.setAttribute("data-zoom", String(zoom));
  document.documentElement.setAttribute("data-label-band", labelZoomBand(zoom, !labels || hubs > 0));
  // MR3's rail names, on their own attribute because the two bands overlap and one attribute
  // cannot hold two answers. No hub term: a rail station is never a subway transfer station.
  document.documentElement.setAttribute("data-rail-label-band", railLabelBand(zoom));
  // MR4's dock names, on the same principle and for a reason round 1 measured: they show at
  // the same zoom the subway's names do, but they must not inherit the subway's DEGRADED band,
  // which arrives one zoom early and for a reason that has nothing to do with the ferry.
  document.documentElement.setAttribute("data-ferry-label-band", ferryLabelBand(zoom));
  /* FOLLOW-UP 1: the bus markers' band, from the same integer zoom in the same call, so the
     stylesheet's display rule and buses.js's reach both read one answer (helpers.js says why it
     is a band). Then the reach itself, late-bound by name like applySubwayFocus, because
     systems/buses.js loads after this file and owns the marks: the first paint at load runs
     before it exists, and there are no buses to reach then anyway. */
  document.documentElement.setAttribute("data-bus-band", busMarkerBand(zoom));
  if (typeof paintBusBand === "function") paintBusBand();
  /* THE TOOLTIP IS KEYED ON THE DATA, NOT ON THE HUB COUNT, which is a distinction D2z had to
     teach me: a network can have no interchange while every station lists its routes, and over
     that map the sentence "no station lists the routes that call there" is simply false. So the
     band asks the labels (is there a hub to reveal) and the sentence asks the registry (did the
     backend serve the index at all). Late-bound by name, because stations.js loads after this. */
  if (namesToggleEl) {
    /* try/catch AND NOT typeof, which is the trap this file has already fallen into once this
       stage. stationRegistry is a module-scope const in stations.js, which loads AFTER this
       file, and `typeof` on a binding in its temporal dead zone THROWS rather than returning
       "undefined": it only answers "undefined" for a name that was never declared at all. The
       first paint runs before stations.js has, so this has to survive that. */
    // The same list the band above was decided from, so the sentence and the band cannot
    // disagree about how many subway stations there are or what they publish.
    namesToggleEl.title = namesToggleTitle(
      subwayStations.filter((entry) => (entry.routes ?? []).length > 0).length,
      subwayStations.length,
    );
  }
}
map.on("zoomend", paintZoomBand);
/* FOLLOW-UP 1: AND WHEN A MOVE ENDS AT A ZOOM THE ROOT DOES NOT SAY, which is a fly cut short.
   A drag or a touch during a preset's 0.8s fly stops it through Leaflet's _stop(), which fires
   no zoomend, so the map rests at a fractional zoom while every band keeps the zoom the fly left
   from. The review of the bus rule measured it: City, press Rail, drag 300ms in, and the map
   settled at 11.817 with data-zoom still "13" and every bus drawn, the 2136-arrow picture the
   rule exists to remove, until the rider next zoomed. The drag that interrupted it ends in a
   moveend, so this repaints then, and only when the integer zoom actually moved: an ordinary pan
   costs one attribute read. Rounded the way paintZoomBand rounds, so the root's data-zoom and
   every band on it stay one answer to one number. */
map.on("moveend", () => {
  if (document.documentElement.getAttribute("data-zoom") !== String(Math.round(map.getZoom()))) paintZoomBand();
});

if (namesToggleEl) {
  namesToggleEl.addEventListener("click", () => {
    const on = document.documentElement.getAttribute("data-labels") !== "off";
    document.documentElement.setAttribute("data-labels", on ? "off" : "on");
    namesToggleEl.setAttribute("aria-pressed", String(!on));
    /* AND IT SAYS WHAT HAPPENED, which route focus has done since this stage was written and
       this control did not. The labels are aria-hidden by design, so for a screen reader this
       sentence is the ONLY evidence the press did anything; and at a zoom where no name can
       show, it is the only thing that stops the button claiming an effect it does not have. */
    /* BOTH BANDS (MR3 round 4): rail names show from zoom 11 and are hidden by this same
       press, so the subway's band alone would have the sentence say "none at this zoom" over a
       screen full of commuter-rail names it had just switched off. */
    announcePage(
      namesToggleAnnouncement(
        !on,
        document.documentElement.getAttribute("data-label-band"),
        document.documentElement.getAttribute("data-rail-label-band"),
      ),
    );
  });
}
paintZoomBand();

/* ----- MR1: the view presets -----------------------------------------------------------
   City, Rail and Region, the design's three centres and zooms, with flyTo at 0.8s (README
   section 3). The Names toggle the design puts in this stack waits for MR2, where there are
   labels to toggle.

   FLYTO IS GATED ON THE MOTION PREFERENCE, which the design does not say and this app does
   everywhere: a 0.8s animated pan is motion, and motionAllowed() is the one door. Under
   reduced motion the view still changes, at once, because the DESTINATION is the thing the
   rider asked for and only the journey was decoration.

   THE ACTIVE PRESET IS aria-pressed, not a class alone, and it CLEARS when the map stops
   being the view it names: a highlighted "City" over a map showing New Jersey would be a lie.

   ASKED OF THE MAP, NOT OF A FLAG. The first cut set a "flying" flag and had the end handler
   ignore one event; flyTo fires BOTH zoomend and moveend when it lands, so the second one
   cleared the preset the first had just arrived at, and the button went dark the instant it
   became true. A flag counting events is a guess about Leaflet's internals. Whether the map
   IS at the preset is not a guess, and it is also the exact thing the button claims, so the
   question and the assertion are the same sentence.

   AND THE TOLERANCE IS IN PIXELS, NOT DEGREES, which is the second thing measured here. A
   degree tolerance means a different distance at every zoom and a flyTo lands through pixel
   arithmetic, so at zoom 10 it arrived 0.0007 degrees off its target and a 1e-4 allowance
   read that as the rider having moved: the button went dark at the instant it became true.
   Two pixels is a rounding allowance at any zoom, and a rider's pan is orders of magnitude
   more than two pixels. */
const VIEW_PRESETS = [
  { id: "view-city", center: [40.7295, -73.99], zoom: 13 },
  { id: "view-rail", center: [40.76, -73.96], zoom: 11 },
  { id: "view-region", center: [40.79, -73.9], zoom: 10 },
];
const VIEW_EPSILON_PX = 2;
let activeView = null;

function mapIsAt(preset) {
  if (map.getZoom() !== preset.zoom) return false;
  return map.latLngToContainerPoint(preset.center).distanceTo(map.getSize().divideBy(2)) <= VIEW_EPSILON_PX;
}

function paintViewPresets() {
  for (const preset of VIEW_PRESETS) {
    const button = document.getElementById(preset.id);
    if (button) button.setAttribute("aria-pressed", String(activeView === preset.id));
  }
}

for (const preset of VIEW_PRESETS) {
  const button = document.getElementById(preset.id);
  if (!button) continue;
  button.addEventListener("click", () => {
    activeView = preset.id;
    paintViewPresets();
    // The design's 0.8s fly, GATED ON THE MOTION PREFERENCE, which the design does not
    // mention and this app does everywhere. Under reduced motion the view still changes, at
    // once: the destination is what the rider asked for and only the journey was decoration.
    if (motionAllowed()) map.flyTo(preset.center, preset.zoom, { duration: 0.8 });
    /* MR1 ROUND 2: animate:false, AND IT IS NOT BELT AND BRACES. motionAllowed() is read live
       on every press, so a rider who turns the preference on mid-session takes this branch
       immediately; what this branch could not do until now is honour them. Leaflet reads
       zoomAnimation ONCE, when the map is constructed (helpers.js says so at
       watchMotionPreference, and it is the reason applyMotionPreference cannot reach it), so
       on a map built while motion was allowed a bare setView still animates the zoom. The
       option is the supported way to say no to that one call. A rider who set the preference
       before load is unaffected either way, which is why this needed the mid-session case to
       be seen at all. */
    else map.setView(preset.center, preset.zoom, { animate: false });
  });
}
map.on("moveend zoomend", () => {
  if (activeView == null) return;
  const preset = VIEW_PRESETS.find((p) => p.id === activeView);
  if (preset && mapIsAt(preset)) return;
  activeView = null;
  paintViewPresets();
});
paintViewPresets();

/* ===== THE PANE ORDER, IN ONE PLACE =====================================================
   Every z-index this map depends on, lowest first. Leaflet owns the ones without a
   createPane call (frontend/vendor/leaflet/leaflet.css); the three marked OURS are made here.

     200  tilePane            the basemap
     390  subwayLinePane      OURS. Subway ribbons, and nothing else.
     394  railroadCasingPane  OURS. The three rail families' paper casings, and nothing else.
     395  railroadLinePane    OURS. LIRR, Metro-North and NJ Transit branch lines.
     400  overlayPane         every remaining family's route lines, on one shared canvas
     450  stationPane         OURS. Every family's station dots, on one shared canvas.
     460  stationLabelPane    OURS. Subway station name labels.
     500  shadowPane          Leaflet's marker shadows (unused here)
     600  markerPane          every vehicle: trains, buses, boats, planes
     650  tooltipPane         Leaflet's default for tooltips; this app puts none here
     700  popupPane           the popups

   WHY THE SUBWAY'S LINES GOT A PANE OF THEIR OWN (MR2 round 3). Every family passed the same
   L.canvas to its route lines, and Leaflet's canvas draws its layers in INSERTION order
   (_initPath appends to _drawLast; _draw walks the list), independently of which LayerGroup
   they belong to. That was harmless while the subway drew 2.5px hairlines at opacity 0.5. It
   stopped being harmless when MR2 gave the subway a 6.5px casing in --paper at 0.9: a casing
   that wide, drawn later, ERASES a thin line beside it. PATH's 33rd St line runs under 6th
   Avenue at weight 2.5, the AirTrain at Howard Beach is weight 3, the LIRR Atlantic Branch
   beside the A and C is 2.5. And the order was a race: all eleven static loaders are kicked
   off together in map.js, /api/subway-routes is by far the largest payload and re-fetches on
   a warming 503, so in production the ribbons routinely landed last and whether another
   family's line survived depended on which response arrived first.

   A pane BELOW overlayPane makes the answer the same every time and the right way round: the
   subway is the base network on this map, so its ribbons go under everything, and no fetch
   order can change it. Nothing else moves, which is what MR2's pins require.

   AND MR3 BROUGHT THE SAME DEFECT BACK, WHICH IS WHY THERE IS A SECOND ONE (finding N2). Stage
   3 gave the three commuter rail families a 5px casing in --paper at 0.9, drawn on the shared
   canvas, and that is the same shape of mark that made the subway's pane necessary: PATH's
   33rd St line is weight 3.5, the AirTrain at Howard Beach is 3 and the ferry's routes are 2,
   all of them thinner than the casing and all of them on one canvas with it. NJ Transit runs
   into Newark Penn and Hoboken where PATH does, so the overlap is real rather than theoretical,
   and the arrival order is the same race it always was.

   So the railroads get railroadLinePane at 395: ABOVE the subway, which is still the base
   network under everything, and BELOW the four families whose lines a 5px casing could erase.
   The number is between the two rather than at either end because the ordering is a three-way
   one now, and a pane cannot be shared by families that must not paint over each other.

   AND A SECOND RAIL PANE AT 394, BECAUSE ONE WAS NOT ENOUGH (round 4). Putting all three rail
   families on ONE canvas closed the defect against PATH, the AirTrain and the ferry and reopened
   it INSIDE the pane. subway.js's answer to this is two passes over one payload, every casing
   then every line, and that works there because the subway's geometry arrives in a SINGLE
   response. The rail families' does not: /api/railroad-routes and /api/njt-routes are two
   endpoints, kicked off together, landing in a race, and each one can only order its own lines.
   Measured on the hermetic world: the draw chain came out [5, 5, 2.5, 2.5, 5, 5, 5, 2.5, 2.5,
   2.5], so all three NJ Transit casings were stroked after both railroad lines, and an NJT casing
   crossing an LIRR line at Penn erased it. Two passes per loader cannot fix that, because neither
   loader knows whether the other has run.

   A SECOND PANE CAN, and it is the same remedy one number down: every casing on 394 and every
   line on 395, so the relation holds in EVERY arrival order rather than in the order that
   happened. Nothing is lost by splitting them. A casing landing over another casing is invisible
   (one weight, one paper colour, one opacity), and the pane a mark belongs to is not the layer
   group that toggles it, so LIRR, Metro-North and NJ Transit each still show and hide their
   casing and their line together. Two adjacent panes for one drawing is the cost, and it is the
   only structure here that does not depend on which response arrives first.

   Station dots sit between the route lines and the vehicles so the station canvas, not the
   route-line canvas it overlaps, receives clicks. Station name labels sit just above the
   dots: a name may cover the dot it names, which is its own station, and may never cover a
   train. A permanent Leaflet tooltip defaults to tooltipPane at 650, ABOVE the vehicles, and
   measured from this stage's own committed screenshots that cost a train bullet 44% of its
   route-coloured pixels, letter and all. */
map.createPane("subwayLinePane");
map.getPane("subwayLinePane").style.zIndex = 390;

/* The rail families' two panes, per the order above: finding N2 put them above the subway and
   below the thin families, and round 4 split the casing off the line. ONE PAIR FOR ALL THREE
   FAMILIES, because they draw one grammar, and the split is what makes the answer independent of
   which of the two route endpoints answers first. The casing pane holds nothing but 5px paper at
   0.9, so order within it cannot matter; the line pane holds nothing but 2.5px branch colours, so
   no casing can be on the wrong side of a line anywhere. */
map.createPane("railroadCasingPane");
map.getPane("railroadCasingPane").style.zIndex = 394;

map.createPane("railroadLinePane");
map.getPane("railroadLinePane").style.zIndex = 395;

map.createPane("stationPane");
map.getPane("stationPane").style.zIndex = 450;

// The label pane, per the order above. Round 2's pass measured the label's BOX against the
// anchor rather than its painted text, called it a 2x3 pixel corner, and that is how a name
// painted across a train bullet got through a review that was looking for exactly this.
map.createPane("stationLabelPane");
map.getPane("stationLabelPane").style.zIndex = 460;

L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);

// Buses and subways live in separate layer groups so they toggle independently.
// Route lines are vectors (canvas), which Leaflet draws beneath marker panes.
const busLayer = L.layerGroup().addTo(map);
const subwayLayer = L.layerGroup().addTo(map);
const routeLinesLayer = L.layerGroup().addTo(map);
const busRouteLayer = L.layerGroup().addTo(map); // the one clicked bus route
const stationLayer = L.layerGroup().addTo(map);
// MR1: THE RAILROADS SPLIT PER AGENCY, because the design's feed strip has one button per
// FEED and LIRR and Metro-North are two feeds inside one source. They poll together and
// they fail apart: the per-system freshness index has always carried them separately
// (`railroads|LIRR`, `railroads|MNR`), the status line has always named them separately,
// and the C6 dimming specs exist precisely because one can go stale while the other does
// not. The single "Railroads" checkbox was the odd one out.
//
// NOTHING ABOUT A MARKER CHANGES HERE, only which group it is added to; the P1h and P1i
// pins hold every railroad mark and popup byte-identical across this commit. The three
// accessors below are what railroad.js writes through, so the choice of group is made in
// one place from the row's own `system` rather than at each call site.
const lirrLayer = L.layerGroup().addTo(map); // LIRR GPS and placed markers
const lirrRouteLinesLayer = L.layerGroup().addTo(map); // LIRR branch geometry
const lirrStationLayer = L.layerGroup().addTo(map); // LIRR clickable stations
const mnrLayer = L.layerGroup().addTo(map); // Metro-North markers
const mnrRouteLinesLayer = L.layerGroup().addTo(map); // Metro-North line geometry
const mnrStationLayer = L.layerGroup().addTo(map); // Metro-North clickable stations

// Metro-North or LIRR, and LIRR is the default rather than a third branch: the railroad
// feed serves exactly these two systems (backend RAILROAD_FRESHNESS_SYSTEMS), and a row
// naming neither would be a decoder bug rather than a rendering choice.
const railroadVehicleLayer = (system) => (system === "MNR" ? mnrLayer : lirrLayer);
const railroadLineLayer = (system) => (system === "MNR" ? mnrRouteLinesLayer : lirrRouteLinesLayer);
const railroadStopLayer = (system) => (system === "MNR" ? mnrStationLayer : lirrStationLayer);
// AirTrain JFK is static-only (no realtime feed exists). Its own layers so it
// toggles independently of the railroad group.
const airtrainRouteLinesLayer = L.layerGroup().addTo(map); // 3 branch guideways
const airtrainStationLayer = L.layerGroup().addTo(map); // 10 clickable stations
// PATH gets its own three groups (mirroring the railroad trio) so the whole
// system toggles as one.
const pathRouteLines = L.layerGroup().addTo(map); // route geometry, both directions per route
const pathStations = L.layerGroup().addTo(map); // 13 clickable parent stations
const pathTrains = L.layerGroup().addTo(map); // trains gliding between (or placed at) stations
// NYC Ferry gets its own three groups (the same trio shape) so the whole system
// toggles as one: route geometry, clickable docks, and live GPS boats.
const ferryRouteLines = L.layerGroup().addTo(map); // route geometry, modal polyline per direction
const ferryDocks = L.layerGroup().addTo(map); // clickable landing docks
const ferryBoats = L.layerGroup().addTo(map); // live GPS boat markers
// NJ Transit Rail (15c) gets the same trio, for the same reason: the whole system
// toggles as one. Its lines are drawn from shapes.txt, its stations are squares
// (systems/njt.js says why), and every train on it is a schedule estimate.
const njtRouteLines = L.layerGroup().addTo(map); // per-route geometry, branch variants kept
const njtStations = L.layerGroup().addTo(map); // clickable stations, flat (no parent stations)
const njtTrains = L.layerGroup().addTo(map); // placed trains gliding between stops

/* A4: LEAFLET'S POPUP CLOSE BUTTON IS AN ANCHOR TO NOWHERE, and page-wide scanning is
   what finally saw it. Leaflet renders
     <a class="leaflet-popup-close-button" role="button" aria-label="Close popup" href="#close">
   and axe reports it as a skip-link violation, "No skip link target", because an anchor
   with a fragment href is a link to an element that has to exist and `#close` never does.
   No scoped scan could ever have caught it: popups were outside every root A1 through A3
   included.

   The role and the name are already right, so the defect is only the vestigial href, which
   Leaflet carries to make the anchor focusable and clickable. Removing it costs the focus
   stop, so tabindex replaces it, and it costs keyboard activation, because a browser
   synthesises a click from Enter for LINKS and not for role=button anchors: that is what
   the keydown handler restores. Space is included because a rider who has been told this
   is a button will try it.

   Done on popupopen rather than by replacing the node, because Leaflet keeps its own
   reference to the button and rebuilds the popup element lazily; the guard flag is because
   Leaflet reuses that element across opens and a second listener would close the popup on
   one press and then try again on nothing. */
/* A4: A POPUP MUST NOT OPEN UNDERNEATH THE PAGE'S OWN CHROME, and page-wide scanning is
   what proved it was happening. axe reported the popup's content as undecidable,
   "background color could not be determined because it is overlapped by another element".
   Measured at 375x667: the popup occupied x 255..345 y 19..70 while #panel occupied
   x 140..365 y 10..285, and document.elementFromPoint at all nine sample points across the
   popup returned #stations-toggle or #legend-toggle. Not partially covered: entirely
   covered. A rider who opened a popup near the top of a phone screen saw the legend.

   THE STACKING FIX DOES NOT WORK, and it is worth recording why so nobody retries it.
   Leaflet's popupPane is z-index 700 and #panel is 1000, so raising the pane looks like a
   one-line answer. It is not: .leaflet-map-pane is itself positioned with z-index 400, which
   makes it a stacking context, so every pane inside it is capped at 400 relative to anything
   outside. Measured: the pane's computed z-index really was 1001 and the hit test still
   returned the legend's controls. Moving the popup to a pane outside the map pane would
   escape the cap and break worse, because a pane outside .leaflet-map-pane does not receive
   the map's transform and the popup would stop following the map.

   SO THE MAP PANS, which is what Leaflet already does when a popup would fall off the
   viewport edge; this extends the same idea to edges Leaflet cannot see.

   THE FIRST VERSION WAS WRONG IN THREE WAYS AND THE ADVERSARIAL ROUND MEASURED ALL THREE.
   It is rewritten rather than adjusted, because each error came from deciding something in
   advance that only measurement can answer.

   1. IT READ THE POPUP TOO EARLY. The old comment claimed popupopen "reads a settled
      position". False for the popups that matter: a station popup is bound with empty
      content and filled by a fetch, so at popupopen it is 29px tall, the correction is
      computed on that, and the arrivals then land and grow it back over the legend.
      Measured at 375 on the panel's own sync path: popupopen saw h=29, settled measured
      top 169.65 bottom 321 against #panel bottom 284.5, still overlapping. So the trigger
      is a ResizeObserver on the popup element as well as popupopen, and the correction
      runs again whenever the content changes size.

   2. IT ONLY EVER MOVED DOWN. Down is right at 375, where #panel is 275px of a 667px
      viewport. At 1280 the legend spans y 10..710 of a 720px map, so nothing fits below it
      and the guard bailed every time: the mechanism never fired at the width where the
      popup is largest. Measured on a real station popup at desktop, six of nine sample
      points across it returned legend-row. The clear space at desktop is to the LEFT of
      x=1030, not below. So the direction is no longer chosen in advance: all four are
      costed and the cheapest one that actually clears everything wins.

   3. IT KNEW ABOUT ONE OBSTACLE. The old guard's only other constraint was the map's
      height, so a downward move could push a tall popup under the alert banner, which is
      z-index 1001 and paints over the popup pane exactly as the legend does. Measured with
      a 300px popup at 375: pan down 368.5, popup bottom 620, banner top 595.4, the bottom
      25px covered and not hit-testable. The banner is now an obstacle like any other.

   UNANIMATED, AND THAT IS A DECISION RATHER THAN A DEFAULT. Everything else on this map
   goes through the A2 panBy wrapper, which animates unless the rider asked for reduced
   motion. This one does not, because it is not a journey: it is a correction of where the
   popup already landed, and animating a correction shows the rider the wrong position
   first and then slides the whole field away from it. */

/* The chrome that paints over the popup pane. Each is outside .leaflet-map-pane's stacking
   context and above it, which is the property that makes them obstacles rather than just
   neighbours. Listed rather than derived, so adding a third overlay is a deliberate edit
   here and not a silent regression.

   MR1 ROUND 2 ADDED THE THIRD, AND THE COMMENT ABOVE IS EXACTLY WHY IT HAD TO. The view
   preset stack is fixed to the bottom right at z-index 1000 and paints over the popup pane
   like the other two; left off this list it was an overlay the correction could not see, so
   a popup that grew under it was neither moved nor noticed, and its clicks went to the
   presets. It is a CHILD of #panel rather than a sibling of #map, which changes nothing
   here: what this list wants is a box that covers the popup, and popupObstacles reads a
   rect. */
const POPUP_OBSTACLE_IDS = ["panel", "alert-banner", "view-stack"];

function popupObstacles() {
  return POPUP_OBSTACLE_IDS.map((id) => document.getElementById(id))
    .filter((el) => el && !el.hidden)
    .map((el) => el.getBoundingClientRect())
    // A zero-size box is the dismissed banner, which reserves no space and blocks nothing.
    .filter((box) => box.width > 0 && box.height > 0);
}

/* MR5 (ruling S3): THE CHROME THE AUTOPAN RESERVES ROOM ABOVE, which is a SUBSET of the obstacle
   list and not the same question. POPUP_OBSTACLE_IDS above answers "what paints over the popup
   pane", and that includes the view preset stack at the BOTTOM right; a top padding derived from
   that stack's bottom edge would reserve the entire map. So this list is the chrome the design's
   recipe actually names, the header and the alert strip, and the control stack's share is the
   recipe's own 110px right padding plus panPopupClearOfChrome, which reads the stack's real rect.
   That is what "the two measurements compose" means.

   Listed rather than derived for the same reason POPUP_OBSTACLE_IDS is: a third overlay across
   the top of the page should be a deliberate edit here. */
const POPUP_TOP_CHROME_IDS = ["panel", "alert-banner"];

/* How far down the MAP the page's top chrome reaches, in the map's own pixels, which is the space
   Leaflet's padding is measured in. Relative to the map's top rather than the viewport's, because
   #map is not necessarily at y 0 and a padding that confused the two would be wrong by the
   difference without ever looking wrong. A hidden or zero-size element reserves nothing, which is
   the dismissed banner. */
function pageChromeBottom() {
  const container = document.getElementById("map");
  if (!container) return 0;
  const top = container.getBoundingClientRect().top;
  let bottom = 0;
  for (const id of POPUP_TOP_CHROME_IDS) {
    const el = document.getElementById(id);
    if (!el || el.hidden) continue;
    const box = el.getBoundingClientRect();
    if (box.width <= 0 || box.height <= 0) continue;
    bottom = Math.max(bottom, box.bottom - top);
  }
  return Math.max(0, bottom);
}

/* THE PADDING LEAFLET IS GIVEN, AND THE STAND-DOWN, which are one decision and so are one
   function. helpers.js's clampedAutoPanPadding holds the arithmetic and the reasoning; this is
   the half that needs a rendered page: the header's measured bottom edge, the map's box, the
   popup's box, and Leaflet's own point type.

   IT IS A WRITE ON popup.options AND THAT IS THE WHOLE RISK. Those options are read again by
   every popup.update(), which for an open vehicle popup is every fifteen seconds, so a padding
   left behind keeps pulling the map long after the open that set it. That is why the rider's
   ownership CLEARS the padding rather than merely skipping this call: skipping would leave the
   last padding armed. standDownPopupAutoPan below is the other half of that.

   Returns the padding it applied, or null when it applied none, which is what D6d through D6g
   read. */
function applyPopupAutoPan(popup) {
  const root = popup && popup.getElement ? popup.getElement() : null;
  const container = document.getElementById("map");
  if (!root || !container) return null;
  if (riderOwnsTheView) {
    clearPopupAutoPan(popup);
    return null;
  }
  const mapBox = container.getBoundingClientRect();
  const popupBox = root.getBoundingClientRect();
  const pad = clampedAutoPanPadding({
    want: popupAutoPanWant(pageChromeBottom()),
    map: { width: mapBox.width, height: mapBox.height },
    popup: { width: popupBox.width, height: popupBox.height },
  });
  if (!pad.usable) {
    clearPopupAutoPan(popup);
    return null;
  }
  popup.options.autoPanPaddingTopLeft = L.point(pad.topLeft[0], pad.topLeft[1]);
  popup.options.autoPanPaddingBottomRight = L.point(pad.bottomRight[0], pad.bottomRight[1]);
  /* _adjustPan IS LEAFLET'S OWN AND IT IS PRIVATE FOR A REASON: there is no public way to run an
     autopan after changing the padding, and the padding cannot be set before the open because it is
     derived from the popup's rendered size. Guarded on the method existing, so a Leaflet upgrade
     that renames it degrades to "no correction" rather than throwing inside a popupopen handler and
     taking the rest of it with it.

     AND autoPan IS FLIPPED ON FOR THE LENGTH OF THIS ONE CALL, because POPUP_OPTIONS turns it off.
     _adjustPan's whole body is behind `this.options.autoPan &&`, so with the option off it does
     nothing, including when we ask. Off at rest is what makes this the ONLY pan a popup gets: see
     POPUP_OPTIONS in helpers.js for the two pans it replaces and the poll it stops. Restored in a
     finally, so a throw inside Leaflet cannot leave the option on and hand the next poll an autopan
     nobody asked for. */
  if (typeof popup._adjustPan === "function") {
    const wasAutoPan = popup.options.autoPan;
    correctingNow = true;
    popup.options.autoPan = true;
    try {
      popup._adjustPan();
    } finally {
      popup.options.autoPan = wasAutoPan;
      correctingNow = false;
    }
  }
  return pad;
}

// Back to Leaflet's own default, which is a 5px strip: the padding options are DELETED rather
// than set to zero, so what is left is genuinely the library's behaviour and not a third setting
// of ours that happens to look like it.
function clearPopupAutoPan(popup) {
  if (!popup || !popup.options) return;
  delete popup.options.autoPanPaddingTopLeft;
  delete popup.options.autoPanPaddingBottomRight;
}

function panPopupClearOfChrome(popup) {
  const root = popup && popup.getElement ? popup.getElement() : null;
  const container = document.getElementById("map");
  if (!root || !container) return false;
  const shift = popupClearingShift(root.getBoundingClientRect(), popupObstacles(), container.getBoundingClientRect());
  if (!shift) return false;
  // panBy moves the VIEW, so the content moves the other way: to move the popup left by d
  // the map pans right by d. Measured rather than assumed: panBy([200, 0]) moved a popup
  // from x 234 to x 34.
  map.panBy([-shift.dx, -shift.dy], { animate: false });
  return true;
}

/* THE TRIGGER, WHICH IS THE HALF THE FIRST VERSION GOT WRONG. popupopen alone is too early
   for any popup whose content arrives later, and every station popup is one. A
   ResizeObserver on the popup element catches both moments with one mechanism: it fires
   once when the element is first laid out and again when the fetched content changes its
   size. It is disconnected on popupclose so a closed popup's corpse cannot keep panning
   the map during its fade.

   AND IT ONLY EVER CORRECTS A POSITION IT SET ITSELF, which round 2 had to teach it. The
   observer outlives the opening, so a background arrivals refresh that changes the popup's
   height re-ran the correction on a map the RIDER had since moved, and threw their position
   away. Measured at 375: the rider dragged the map to centre lat 40.65134, a refresh grew
   the popup, and the map jumped to 40.72996.

   That is the same principle as the pan animation split, one level up. An adjustment is the
   app correcting its own fit; the moment the rider takes over, the position is theirs and
   the app has no business tidying it.

   ROUND 2 EXPRESSED THAT AS "IS THE CENTRE STILL WHERE I LEFT IT", AND ROUND 3 BROKE IT.
   Comparing centres asks a question about FLOATS when the thing being decided is INTENT, and
   Leaflet moves the centre on its own: invalidateSize re-centres by
   round(oldSize/2) - round(newSize/2), so any container dimension that flips parity shifts
   the reported centre by exactly half a pixel with no input at all. Measured on a phone whose
   URL bar collapsed, 375x667 to 375x600: the centre moved 0.500003px, the guard read that as
   the rider taking over, the correction stood down for good, and the next arrivals refresh
   left 146px of the popup under a legend that paints over it. An epsilon would not have
   fixed that; it would have moved the threshold and kept the category error.

   SO THE GUARD KEYS ON INTENT, FROM THE EVENTS THAT CARRY IT. Leaflet fires dragstart when a
   rider drags and zoomstart when a rider zooms, and every other way the view moves without
   us arrives as a movestart we did not cause. Ours are bracketed while they run, and
   Leaflet's own autoPan is excluded by the same autopanstart signal the motion wrapper
   already consumes, because an autoPan is an adjustment too. What is left is the rider. */
let popupClearObserver = null;
let popupClearObserved = null;
let riderOwnsTheView = false;
let correctingNow = false;

function noteRiderTookOver() {
  riderOwnsTheView = true;
  /* MR5 (S3): AND THE PADDING IS DISARMED, not merely skipped from here on. The design's padding
     lives on popup.options, and popup.update() re-runs Leaflet's autopan against it on every
     fifteen-second poll for every open vehicle popup. Standing down by returning early from
     applyPopupAutoPan would leave the last padding in place and the next poll would pull the map
     anyway, which is the second break the README's erratum records: this app does not tidy a
     position the rider chose, and Leaflet's autopan has no such rule. */
  standDownPopupAutoPan();
}
map.on("dragstart", noteRiderTookOver);
map.on("zoomstart", noteRiderTookOver);
map.on("movestart", () => {
  // Not ours, and not Leaflet tidying its own popup: that only leaves the rider, which
  // covers keyboard panning and anything a future Leaflet handler does on their behalf.
  if (correctingNow || leafletAutoPanning) return;
  noteRiderTookOver();
});

function correctOnce(popup) {
  correctingNow = true;
  try {
    return panPopupClearOfChrome(popup);
  } finally {
    correctingNow = false;
  }
}

map.on("popupopen", (event) => {
  const root = event.popup && event.popup.getElement ? event.popup.getElement() : null;
  if (popupClearObserver) popupClearObserver.disconnect();
  // A new popup is a new placement, so the rider's ownership of the last one does not carry.
  riderOwnsTheView = false;
  // MR5 (S3): the design's padding first, clamped to what this map and popup can satisfy, and
  // then the collision solver over the real boxes. That order IS the composition: the padding
  // gets the popup out of the band the header occupies, and correctOnce answers for the
  // obstacles' actual rects, including the control stack the padding only approximates.
  applyPopupAutoPan(event.popup);
  correctOnce(event.popup);
  popupClearObserved = event.popup;
  if (!root || typeof ResizeObserver !== "function") return;
  popupClearObserver = new ResizeObserver(() => {
    // Guarded on the popup still being open: Leaflet keeps the element alive through the
    // close fade, and a resize during that fade must not move the map. Asked of hasLayer
    // rather than of map._popup, for the reason recorded at openPopupsOnMap below.
    if (!map.hasLayer(event.popup)) return;
    if (riderOwnsTheView) return;
    // A popup that grew needs the padding recomputed against its new height before the solver
    // runs, for the same reason the open does: the clamp is a function of the popup's size, and a
    // station popup is 29px tall at popupopen and 300 once its arrivals land.
    applyPopupAutoPan(event.popup);
    correctOnce(event.popup);
  });
  popupClearObserver.observe(root);
});

map.on("popupclose", (event) => {
  if (event.popup !== popupClearObserved) return;
  popupClearObserved = null;
  riderOwnsTheView = false;
  if (!popupClearObserver) return;
  popupClearObserver.disconnect();
  popupClearObserver = null;
});

/* WHICH POPUPS ARE OPEN, ASKED OF A REGISTER WE KEEP RATHER THAN OF map._popup.
   Round 2 took this question away from the close button, which had been closing "whichever
   popup the map thinks is current" instead of its own. Round 3 pointed out the same read was
   still live one file over, in the Escape ladder, where it decides which popup a keypress
   closes: with two popups open and the rider standing in the first, Escape closed the other.
   Fixing it per-file leaves the class alive, so the read is gone from the app entirely and
   this register is the single answer. map._popup remains wrong for the same reason it always
   was: it is Leaflet's idea of the most recent popup, it is not cleared on close, and it
   answers a question about RECENCY when every caller here is asking about IDENTITY.
   hasLayer stays as the truthful filter, because a popup lingers through its fade. */
const openPopups = new Set();
map.on("popupopen", (event) => openPopups.add(event.popup));
map.on("popupclose", (event) => openPopups.delete(event.popup));

function openPopupsOnMap() {
  return [...openPopups].filter((popup) => map.hasLayer(popup));
}

// The stand-down, declared here because it needs the register above, and a function declaration
// rather than a const so noteRiderTookOver (which is written before it) can call it. Every open
// popup and not just the current one: two popups can be open at once, and a padding on the one
// the rider is not standing in pulls the map just as hard.
function standDownPopupAutoPan() {
  for (const popup of openPopupsOnMap()) clearPopupAutoPan(popup);
}

/* Rebuild every open popup's content, which is what a theme swap owes the strings that resolved
   a token when they were built. popup.update() rather than a close and reopen: it re-runs the
   bound content function, keeps the popup open and keeps the rider's focus where it is, which
   closePopupReturningFocus and updatePopupKeepingFocus exist to protect.

   A FUNCTION DECLARATION, AND THE try IS THE TEMPORAL DEAD ZONE. applyTheme is written above
   this and CALLS IT, and applyTheme itself runs once at module load to apply the stored
   preference. `openPopups` is a module-scope const declared further down, which is in its
   temporal dead zone until its own line runs, and `typeof` does not rescue a TDZ binding: it
   throws for one too. So there is no cheap test for "has that line run yet", and an uncaught
   throw here would take every script after this one with it. This file has already lost the
   whole page that way once, which is the sentence written above namesToggleEl and again above
   the canvas registry. The load-time call has no popup to rebuild by construction, so swallowing
   that one ReferenceError costs nothing; anything else a popup's own update() throws is not this
   function's to handle and is re-raised. */
function rebuildOpenPopupsForTheme() {
  let open;
  try {
    open = openPopupsOnMap();
  } catch (err) {
    if (err instanceof ReferenceError) return; // the load-time call, before the register exists
    throw err;
  }
  for (const popup of open) {
    if (typeof popup.update === "function") popup.update();
  }
}

function popupContaining(node) {
  if (!node) return null;
  return (
    openPopupsOnMap().find((popup) => {
      const el = popup.getElement ? popup.getElement() : null;
      return !!el && (el === node || el.contains(node));
    }) || null
  );
}

/* A4 ROUND 1: CLOSING A POPUP THE RIDER IS STANDING IN HAS A FOCUS CONTRACT, and until the
   adversarial round it did not. Measured at 1280 with focus on the close button:

     Escape          -> {"active":"BODY","announced":""}
     the close button-> {"active":"BODY","announced":""}
     next Tab        -> #stations-skip, so the rider restarted at the top of the page

   Two reviewers found it independently, and the shape of the miss is worth recording. A4
   built the vanishing-focus door for exactly this outcome, then A4's own Escape ladder
   created a new path to it, and escape.spec.js asserted only which surface was open, never
   where the rider ended up. A spec suite that checks state and not focus will pass over a
   stranding every time.

   QUIETLY, WHICH IS THE DIFFERENCE FROM THE VANISHING DOOR. That door announces because the
   rider did not ask for anything and the thing they held disappeared. Here the rider pressed
   Escape or the close button: the move is the expected consequence of their own action, and
   narrating it every time would be noise. So focus lands on the map container with nothing
   said, which is the same contract A1 gave the panel.

   ONE HELPER, TWO CALL SITES, and the split is honest: the ladder owns the key and the
   button owns the click, but the DECISION about focus lives in one place. The plan is taken
   BEFORE the popup is destroyed, because afterwards activeElement is already BODY and the
   question cannot be asked, which is the lesson the banner rebuild taught in deliverable 2. */
function closePopupReturningFocus(popup) {
  if (!popup) return false;
  const el = popup.getElement ? popup.getElement() : null;
  const held = !!(el && document.activeElement && el.contains(document.activeElement));
  map.closePopup(popup);
  if (!held) return true;
  const container = document.getElementById("map");
  if (container) container.focus();
  return true;
}

map.on("popupopen", (event) => {
  const root = event.popup && event.popup.getElement ? event.popup.getElement() : null;
  const button = root ? root.querySelector(".leaflet-popup-close-button") : null;
  if (!button || button.dataset.a11yCloseFixed) return;
  button.dataset.a11yCloseFixed = "1";
  button.removeAttribute("href");
  button.setAttribute("tabindex", "0");
  button.addEventListener("keydown", (key) => {
    if (key.key !== "Enter" && key.key !== " ") return;
    key.preventDefault();
    button.click();
  });
  // CAPTURE AND stopImmediatePropagation, because Leaflet binds its own close handler to
  // this same button and a plain stopPropagation does not stop a sibling listener on the
  // target. Leaflet's handler is map.closePopup(popup) and nothing else, so replacing it
  // costs no cleanup; what it buys is that every close path, mouse and keyboard alike,
  // goes through the one helper that knows about focus.
  button.addEventListener(
    "click",
    // Named `press` rather than `event`: the outer parameter is the popupopen event and the
    // popup it carries is the whole point of this handler, so shadowing it silently turned
    // event.popup into undefined and the close button stopped closing anything. A9j caught
    // it in the same run it was written.
    (press) => {
      press.preventDefault();
      press.stopImmediatePropagation();
      // THE POPUP THAT OWNS THIS BUTTON, not map._popup. They are the same in this app
      // today, because Leaflet auto-closes the previous popup when a new one opens, so a
      // second live popup is not reachable. It is still wrong to ask the map which popup is
      // current when the button already knows: the handler is bound per popup and closing
      // "whichever is current" is a bug waiting for the first feature that opens two.
      closePopupReturningFocus(event.popup);
    },
    true,
  );
});


/* ---------------- MR1: the feed strip ------------------------------------------------
   The old layer checkboxes and the old status line, restyled into the design's row 2, with
   their semantics kept and two of them made stronger.

   ONE BUTTON PER FEED, IN THE DESIGN'S ORDER, from helpers.js's FEEDS. Two differences
   from the eight checkboxes it replaces, both deliberate:
     - "Railroads" becomes LIRR and Metro-North, which is why the railroad layer groups
       split above.
     - "Stations" (the subway station dots) folds into Subway. The design's feed toggle
       adds and removes that system's layer groups as one (lines, stations, vehicles), and
       station VISIBILITY becomes a zoom question in MR2 rather than a checkbox. It also
       retires the duplicate-name hazard that checkbox carried: it read "Stations" beside
       the "Stations" button that opens the panel, and needed an aria-label to tell them
       apart.

   ARIA-PRESSED IS WRITTEN BY THE CODE THAT SHOWS AND HIDES THE LAYER, in one function, on
   the same line of reasoning the legend disclosure uses for aria-expanded: what a screen
   reader is told and what is drawn cannot disagree if there is only one writer. Mutation
   M1 is aria-pressed not written on toggle, and M2 is the visible state carried by opacity
   alone; both die here because `pressed` and `off` come out of feedStripModel together.

   THE BUTTONS ARE GENERATED rather than written into index.html. The checkboxes were static
   markup so the legend disclosure had no load-order dependency, and that argument does not
   transfer: these are not a disclosure, and their count, dot and tooltip are rewritten on
   every poll anyway, so eight static copies would be eight chances to drift from FEEDS. */

// The rider's hidden set, by feed key. Not persisted: which layers are showing has never
// survived a reload in this app and MR1 is not the stage that changes that.
const hiddenFeeds = new Set();

// Whether a feed is currently showing. The one accessor for anything outside this file that
// needs to know, so a system asks the state rather than reading it back off a control: the
// old answer was `document.getElementById("toggle-buses").checked` and it died the moment
// the checkbox became a button. A listener registered in a later file runs after the strip's
// own click handler, so this is already current when it is asked.
function feedShowing(key) {
  return !hiddenFeeds.has(key);
}

// The layer groups each feed owns. Every one of these is declared in this file, so the
// table cannot name a group that does not exist.
const FEED_LAYERS = {
  subway: () => [subwayLayer, routeLinesLayer, stationLayer],
  buses: () => [busLayer, busRouteLayer],
  lirr: () => [lirrLayer, lirrRouteLinesLayer, lirrStationLayer],
  mnr: () => [mnrLayer, mnrRouteLinesLayer, mnrStationLayer],
  njt: () => [njtRouteLines, njtStations, njtTrains],
  path: () => [pathRouteLines, pathStations, pathTrains],
  ferry: () => [ferryRouteLines, ferryDocks, ferryBoats],
  airtrain: () => [airtrainRouteLinesLayer, airtrainStationLayer],
};

/* Each feed's count, FROM THE EXISTING VEHICLE REGISTRIES. These are the same Maps the
   status line's counts were built from, read here per feed instead of per source; the two
   railroads are counted out of the one `railroads` Map by the system half of its key,
   which is the same split the freshness index and the status line already make.

   AirTrain returns null rather than 0. It has no vehicles at all (static timetable data,
   no realtime feed anywhere behind it), and 0 would be a claim about a fleet that does not
   exist. A count of its stations would be a different kind of number wearing the badge.

   Read through functions because these registries are declared in the per-system files,
   which load after this one; by the time a poll calls this they all exist. */
const railroadFleet = (system) => {
  let n = 0;
  for (const key of railroads.keys()) if (key.startsWith(`${system}|`)) n += 1;
  return n;
};
const FEED_COUNTS = {
  subway: () => trains.size,
  buses: () => buses.size,
  lirr: () => railroadFleet("LIRR"),
  mnr: () => railroadFleet("MNR"),
  njt: () => njtTrainRecords.size,
  path: () => pathTrainRecords.size,
  ferry: () => ferryBoatRecords.size,
  airtrain: () => null,
};

/* A feed's own freshness, out of the index every other rendering surface reads.
   WORST-OF-SOURCE FOR A WHOLE-SOURCE FEED, and the named system only for the two
   railroads: worstSystemFreshness scans the index by source prefix, so it is right whether
   the envelope carried per-system blocks or the synthesized single one, and it does not
   depend on guessing what that synthesized system is called. Over-reporting age is the
   fail-safe direction, which is the same argument systemFreshnessOf makes. */
function feedAge(feed) {
  if (feed.source == null) return null; // AirTrain: no feed behind it to be fresh or stale
  return feed.system ? systemAgeOf(feed.source, feed.system) : worstSystemFreshness(feed.source).age;
}

/* MR5 (ruling Q2): A VEHICLE POPUP'S FRESHNESS FOOTER, which is vehicleStaleLine restyled.

   IT TAKES THE AGE THE POPUP ALREADY HAS rather than looking one up, and that is the whole design
   of this function. The first draft resolved the feed out of FEEDS and used feedAge, which is what
   the strip's dot does; measured against the call sites, that would have LOST precision on the
   subway. A subway train's stale line is its own FEED GROUP's age (subwaySystemAge reads the groups
   whose coverage lists its route), while the feed's age is the worst group in the whole source, so a
   train on a healthy group would have reported a different group's outage. Every other caller has
   the same shape: it already computes the age it means, and passing it keeps this a restyle rather
   than a re-derivation.

   THE STATE COMES FROM THAT AGE, through the same feedDotState the strip uses, so the two surfaces
   cannot disagree about what an age means. A NULL age is "stale" and says "Not reporting", which is
   feedDotState's own judgment and its comment's own reasoning: a feed that has never decoded has no
   freshness to report and "live" would be the one answer that is a lie. That is a change from
   vehicleStaleLine, which rendered nothing for a null age; the strip has always said it, and saying
   it here is the ruling's "said one way on both surfaces".

   NOT ON STATION POPUPS, and that is scope rather than oversight. A station board already carries
   its own freshness line, boardLineHtml over boardSystemLine, which answers a different question
   that building 6.2 owns: how old the ARRIVALS are, per board and per row. The ruling is that this
   footer is vehicleStaleLine restyled, and vehicleStaleLine is a vehicle popup's line, so this goes
   exactly where that went. Putting one on a station popup as well would be the third voice this
   ruling exists to prevent. */
function popupFreshLine(age, position = null) {
  return popupFreshHtml({ state: feedDotState({ age }), age, position });
}

// The eight buttons live in their own wrapper, because the wrapper is what folds below
// 700px and the note beside it does not (round 3, by ruling: index.html says why).
const feedButtonsEl = document.getElementById("feed-buttons");
const statusEl = document.getElementById("status");
const feedButtons = new Map(); // feed key -> its button element

// Show or hide one feed's layer groups. The ONE writer of both aria-pressed and the visible
// off treatment, so they cannot disagree.
function applyFeedVisibility(key) {
  const hidden = hiddenFeeds.has(key);
  for (const layer of FEED_LAYERS[key]()) {
    if (hidden) map.removeLayer(layer);
    else map.addLayer(layer);
  }
  /* AND THE LABEL BAND IS REPAINTED, because MR4 made it depend on what is ON THE MAP rather
     than on what the registry holds. The band asks whether the subway has anything on screen to
     judge; pressing a feed off is one of the two ways that answer changes (the other is a
     station load, which calls this too), and before this line it was only recomputed on
     `zoomend`, so a rider who toggled the subway at a fixed zoom kept whichever answer the last
     zoom had left. Cheap and idempotent: it writes three attributes from the live state. */
  paintZoomBand();
}

// Write one model onto the buttons. THE ONE WRITER of aria-pressed and of the off
// treatment, so the state a screen reader is told and the state a rider sees come off the
// same boolean; that is what mutations M1 and M2 have to break.
function paintFeedStrip(entries) {
  for (const entry of entries) {
    const button = feedButtons.get(entry.key);
    if (!button) continue;
    button.setAttribute("aria-pressed", String(entry.pressed));
    button.classList.toggle("feed-off", entry.off);
    button.title = entry.title;
    button.querySelector(".feed-count").textContent = entry.count ?? "";
    button.querySelector(".feed-dot").dataset.state = entry.dot;
  }
}

/* Paint the strip from the counts and ages the app already has. Called from the poll tail
   beside the status note, and from the animation tick when the stale set moves, because a
   feed crosses into stale by time passing rather than by a response arriving: a dot that
   only changed on a poll would stay green through a feed that had stopped answering.

   NEVER AT MODULE SCOPE, and that is a load-order fact rather than a preference. FEED_COUNTS
   reads the per-system registries, which are declared in the files that load AFTER this one;
   calling this while this file is still evaluating throws ReferenceError on `trains` and
   takes shared.js, stations.js and map.js down with it. Measured: it did, and the page came
   up with no markers at all. buildFeedStrip paints the count-free model instead. */
function refreshFeedStrip() {
  if (!feedButtonsEl) return;
  const counts = {};
  const ages = {};
  for (const feed of FEEDS) {
    const count = FEED_COUNTS[feed.key]();
    if (count != null) counts[feed.key] = count;
    ages[feed.key] = feedAge(feed);
  }
  paintFeedStrip(feedStripModel({ counts, ages, hidden: hiddenFeeds }));
}

function buildFeedStrip() {
  if (!feedButtonsEl) return;
  for (const feed of FEEDS) {
    const button = document.createElement("button");
    button.type = "button";
    button.id = `toggle-${feed.key}`;
    button.className = "feed";
    // The leading mark: a colour tick for the systems the design gives one, the agency
    // glyph block for LIRR, Metro-North and NJ Transit. Decorative either way, because the
    // feed's NAME is right beside it and a screen reader reading a colour swatch as well
    // would say the same thing twice.
    const mark = document.createElement("span");
    mark.setAttribute("aria-hidden", "true");
    if (feed.glyph) {
      mark.className = "feed-glyph";
      mark.textContent = feed.glyph;
    } else {
      mark.className = "feed-tick";
      mark.style.background = feed.tick;
    }
    const name = document.createElement("span");
    name.className = "feed-name";
    name.textContent = feed.name;
    const count = document.createElement("span");
    count.className = "feed-count";
    const dot = document.createElement("span");
    dot.className = "feed-dot";
    dot.setAttribute("aria-hidden", "true");
    // THE VISIBLE OFF MARK, and it is a separate mark rather than only a strike because
    // the v3 brief asks for both and neither alone is enough: a strike can be missed at 10px
    // and a fade is not state at all. aria-hidden because aria-pressed on the button is
    // already the state a screen reader is told, and saying it twice is noise.
    const off = document.createElement("span");
    off.className = "feed-offmark";
    off.setAttribute("aria-hidden", "true");
    off.textContent = "off";
    button.append(mark, name, count, dot, off);
    button.addEventListener("click", () => {
      if (hiddenFeeds.has(feed.key)) hiddenFeeds.delete(feed.key);
      else hiddenFeeds.add(feed.key);
      applyFeedVisibility(feed.key);
      refreshFeedStrip();
    });
    feedButtons.set(feed.key, button);
    feedButtonsEl.append(button);
    applyFeedVisibility(feed.key);
  }
  // The count-free model: every feed showing, no counts, and each dot in the state its
  // absent age earns (stale for a feed that has not decoded, scheduled for AirTrain). The
  // first poll paints the rest. See refreshFeedStrip for why this cannot be that.
  paintFeedStrip(feedStripModel({ hidden: hiddenFeeds }));
}
buildFeedStrip();

function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("error", isError);
}

/* ---------------- A2: the page's one live region ---------------- */

// THE PAGE DOOR. The status line and the alert banner are the two surfaces outside the
// station panel that can have something to say, and BOTH must speak through here.
//
// WHY ONE DOOR AND NOT A GUARD AT EACH SURFACE. This is the same reasoning as the
// marker factory owning keyboard:false, and as A1's announceUnlessTick before it:
// three guarded copies drift, and the one that drifts is the one nobody notices,
// because a live region failing is silent by definition. A1 learned this the
// expensive way when a copied tick guard was missing from one branch and a leaked
// timer walked straight through the gap. One function, one element, no other writer.
//
// POLITENESS IS DELIBERATE. aria-live="polite" for both: alerts are decorative by this
// project's philosophy (they never gate the arrivals a rider came for) and the status
// line is ambient. Nothing on this page is worth cutting off whatever a rider is
// currently reading, so nothing here is assertive.
//
// A NOTE FOR A FUTURE PATH THAT DOES NOT EXIST YET: if either surface is ever hidden
// and later shown again, re-announcing the CURRENT state on return is correct, not
// duplicate. The A1 panel settled the same question: while a region is out of the
// accessibility tree it cannot have been heard, so returning is a first observation
// again rather than a repeat. Today nothing hides these two, which is exactly why the
// rule belongs in writing before something does.
const pageAnnounceEl = document.getElementById("page-announce");

// 6.3: ONE WRITE PER RENDER (memo D11, and N6's prescribed shape). A poll can bring two
// of this region's writers together. refreshAll applies each source as its response
// lands (refreshSource), and a vehicle that has left the payload is removed right there,
// inside apply: if the rider's focus was in its popup, the vanishing-focus door writes
// this region at that moment (applyVanishingFocus). The same refreshAll writes it again
// from its tail when a system's degraded membership changed (announceStatusTransition).
// The age gate made that pair reachable from an ordinary poll: it takes a train off the
// map in a feed that is decoding, so a system recovering on the same poll is announced
// in the same render as the rescue. The two writes sit in different microtasks (apply's,
// and the one after `await Promise.all`), so a MutationObserver reports them as two
// batches of one record each and a per-batch count alone cannot see them collide. But
// when the railroad response is the poll's last to settle, nothing separates them but
// microtasks: no task boundary, so no rendering opportunity, and an atomic polite region
// read after that task says only the second sentence. And a task boundary is no promise
// of one either (several tasks can run inside one frame). So while a poll renders, this
// region's writes are HELD, and the poll's end speaks them once, composed in the order
// they happened.
//
// EVERY WRITER'S WRITES ARE HELD, NOT ONLY THE POLL'S OWN, and that is the correction a
// review measured. The first cut held only what ran synchronously inside the poll's
// applies and its status transition, and the animation tick slipped between them:
// refreshSource rebuilds the freshness index as each response lands, so a tick after the
// railroad's response and before the poll's last one saw Metro-North recover and said so
// at once, and the rescue the apply had held came out alone at the poll's end, second.
// The pair was split, and reversed. Whatever speaks here while a poll renders (the tick,
// a layer toggle, the alert banner, a rescue) now joins that poll's one write. Between
// polls nothing is held and every write goes at once, exactly as before.
//
// A COUNT, BECAUSE POLLS OVERLAP (refreshAll fires whichever sources are not already in
// flight, R2), and every poll's end speaks whatever is held by then, so a write waits for
// the NEXT poll end, never for a quiet moment that overlapping polls could put off
// indefinitely. The cost, stated: a write that lands while a poll renders waits for that
// end, which FETCH_DEADLINE_MS bounds at fifteen seconds; a rescue's focus move itself
// happens at once. N6 itself is not closed here: tickAlertBanner runs after the poll's
// end, and can still speak after the held write in the same stretch, as before 6.3.
let pagePollsRendering = 0;
let heldPageAnnouncements = [];

function announcePage(text) {
  if (!pageAnnounceEl || !text) return false;
  if (pagePollsRendering > 0) {
    heldPageAnnouncements.push(text);
    return true;
  }
  pageAnnounceEl.textContent = text;
  return true;
}

// A poll starts rendering: until its releasePageAnnouncements, every write to this region
// is held.
function holdPageAnnouncements() {
  pagePollsRendering += 1;
}

// A poll's render ends: everything held so far, from any caller, is spoken as one write
// (composeAnnouncements) in the order it was said, or nothing when nothing was held.
//
// THE LAST POLL OUT SPEAKS, NOT THE FIRST, and that is a review fix. This decremented and
// then wrote unconditionally, so with polls overlapping (R2 fires whichever sources are
// not already in flight, and the hold spans the whole fetch phase, up to
// FETCH_DEADLINE_MS) a short poll that started second and ended first would flush a
// still-rendering poll's held text, and that poll's own later messages then went out in a
// second write. One poll's news became two writes in the wrong order, which is exactly
// what holding exists to prevent. Writing only when the count reaches zero restores the
// invariant the paragraph above states. The cost, stated: a held write now waits for the
// last overlapping poll rather than the next poll to finish, so a chain of overlapping
// slow polls defers it further than the single FETCH_DEADLINE_MS the first cut promised.
// It still cannot wait forever, because each poll's hold is released in a finally and
// each fetch is bounded by its own AbortSignal.timeout.
function releasePageAnnouncements() {
  pagePollsRendering = Math.max(0, pagePollsRendering - 1);
  if (pagePollsRendering > 0) return false;
  const text = composeAnnouncements(heldPageAnnouncements);
  heldPageAnnouncements = [];
  if (!pageAnnounceEl || !text) return false;
  pageAnnounceEl.textContent = text;
  return true;
}

// What the page last knew, so worthiness is judged against the previous OBSERVATION
// rather than against whatever happens to be on screen. Null means nothing has been
// observed yet, which is what makes the first poll silent.
let announcedDegraded = null;
let announcedAlerts = null;

// Called from the poll tail with the freshness index this tick produced. Pure decision
// in helpers.js; this is only the plumbing that remembers and speaks.
function announceStatusTransition(freshnessIndex) {
  const next = degradedIdentities(freshnessIndex);
  const text = statusAnnouncement(announcedDegraded, next);
  announcedDegraded = next;
  if (text) announcePage(text);
  return text;
}

// Called wherever the banner's shown set is computed, with the SAME list the banner
// renders, so the spoken and the drawn banner cannot disagree about what is showing.
function announceAlertTransition(shown) {
  const next = alertIdentities(shown);
  const text = bannerAnnouncement(announcedAlerts, next);
  announcedAlerts = next;
  if (text) announcePage(text);
  return text;
}


/* ---------------- Per-system freshness (C2) ---------------- */

// "<sourceKey>|<systemName>" -> that system's current age in seconds (null when it
// has never decoded), plus the subset of those keys that are stale right now. The
// index is rebuilt from the `sources` descriptors, which is the ONE place the
// per-system blocks are ingested (refreshSource), so every rendering surface below
// reads the same numbers the status line does.
//
// Rebuilt on a clock, not only on a poll: a system crosses FEED_STALE_AFTER_S by
// time passing, not by a response arriving, so the animation tick refreshes it too
// (see animateTrains). The stale SET is compared as a string signature and the
// marker sweep runs only when it changes, so the common case (nothing stale, or
// nothing newly stale) costs one small string compare per tick rather than an
// opacity write per marker.
let systemFreshnessIndex = new Map(); // "<source>|<system>" -> { age, staleAt }
let staleSystemSignature = "";

// Rebuild the index. Deliberately does NOT touch the stale-set signature: that is
// change-detection state owned by the animation tick, and consuming a transition
// here (this runs mid-poll, per source) would make the tick miss the sweep. REVIEW
// FIX: it used to do both, so a fast source's poll could swallow a slow source's
// transition and leave those markers undimmed until the tail of refreshAll.
function refreshSystemFreshness() {
  const now = Date.now() / 1000; // RAW clock: systemAges applies the skew correction
  const index = new Map();
  for (const [sourceKey, source] of Object.entries(sources)) {
    // A source with no payload yet has nothing to say about any system. Skipping it
    // also keeps the synthesized-name mismatch in sourceSystems' boot fallback (it
    // names by label, this indexes by key) out of the index entirely.
    if (!source.systems) continue;
    const ages = systemAges(source, now);
    const staleAts = systemStaleAts(source);
    // `ok` travels WITH the age, because an age alone cannot tell "current" from
    // "never decoded anything". The status line has always known this (its `blind` set
    // is exactly ages[name] == null && !ok), and the review found the announcement
    // path did not, which let a dead system be announced as recovered.
    const blocks = sourceSystems(source);
    for (const name of Object.keys(ages)) {
      index.set(`${sourceKey}|${name}`, {
        age: ages[name],
        staleAt: staleAts[name],
        ok: (blocks[name] || {}).ok !== false,
      });
    }
  }
  systemFreshnessIndex = index;
}

// True when the set of stale systems CHANGED since the last call. Separate from the
// rebuild above so exactly one caller (the animation tick) owns the transition.
function staleSetChanged() {
  const stale = [];
  for (const [key, entry] of systemFreshnessIndex) if (staleAge(entry.age)) stale.push(key);
  const signature = stale.sort().join(",");
  const changed = signature !== staleSystemSignature;
  staleSystemSignature = signature;
  return changed;
}

// One system's freshness, for a marker's dimming / popup line / glide freeze.
//
// AN UNKNOWN SYSTEM FALLS BACK TO THE SOURCE'S WORST, which is the fail-safe
// direction: over-dimming says "some of this may be old", which is true, while
// under-dimming would present retained data as live, the exact defect the retention
// gate exists to prevent. REVIEW FIX, and it is not hypothetical: models.py lets an
// aggregate envelope serve `systems: null` (a rollback, or a browser holding the new
// frontend against the old backend), and ingestSystems then synthesizes ONE system
// named after the source while the railroad layer looks its systems up by
// train.system. Every railroad marker missed, so none of them ever dimmed while the
// status line said the whole source was stale.
function systemFreshnessOf(sourceKey, systemName) {
  const entry =
    systemName == null ? null : systemFreshnessIndex.get(`${sourceKey}|${systemName}`);
  return entry ?? worstSystemFreshness(sourceKey);
}

function systemAgeOf(sourceKey, systemName) {
  return systemFreshnessOf(sourceKey, systemName).age;
}

function systemStaleAtOf(sourceKey, systemName) {
  return systemFreshnessOf(sourceKey, systemName).staleAt;
}

// The worst of a source's systems: the largest age and the EARLIEST freeze deadline,
// both being the pessimistic answer.
function worstSystemFreshness(sourceKey) {
  const worst = { age: null, staleAt: null };
  for (const [key, entry] of systemFreshnessIndex) {
    if (!key.startsWith(`${sourceKey}|`)) continue;
    if (entry.age != null && (worst.age == null || entry.age > worst.age)) worst.age = entry.age;
    if (entry.staleAt != null && (worst.staleAt == null || entry.staleAt < worst.staleAt)) {
      worst.staleAt = entry.staleAt;
    }
  }
  return worst;
}

// Each system file registers a sweep that re-dims its own markers; applyStaleTreatment
// runs them all when the stale set changes or a poll lands. Declared here (before the
// system files load) so their top-level pushes land in an existing array.
const staleTreatments = [];

// Every sweep re-notes the observation crossings it sees (vehicleMarkerAge), so the
// earliest pending one is recomputed from scratch whenever the sweeps run.
function applyStaleTreatment() {
  nextObservationCrossing = null;
  for (const sweep of staleTreatments) sweep();
}

// setOpacity rather than a css class on the element: Leaflet stores it in the
// marker's options and re-applies it when the layer is re-added, so a marker dimmed
// while its layer is toggled off comes back still dimmed. getElement() is null in
// that state, which a class-based approach would have to guard.
// `base` is the marker's own resting opacity, which staleness compounds with rather
// than replaces (only the ferry layer has one; see markerOpacity).
// Written only when it changes: since 6.3 the sweeps also run when one observation
// crosses OBS_FRESH_S between polls, and the other few thousand markers they visit then
// already carry the opacity they would be given.
function dimMarker(marker, age, base = 1) {
  const opacity = markerOpacity(age, base);
  if (marker.options.opacity !== opacity) marker.setOpacity(opacity);
}

/* ---------------- 6.3: a vehicle, judged by its own observation ---------------- */

// The skew-corrected clock every apply path, every popup and the animation tick glide,
// age and word a vehicle by (the axis trainLatLng and servedAge share): the client's clock
// less the smallest skew-plus-latency any served_at has shown (noteClockOffset).
function correctedNow() {
  return Date.now() / 1000 - (minClockOffset ?? 0);
}

// A source's descriptor from map.js's `sources`, or null. map.js loads LAST, so every
// page has it by the time anything renders; the typeof is for the one caller that loads
// the system files without map.js at all, the F12 audit harness
// (docs/reviews/audit-2026-09-05/f12_stale_error_body_overwrites.mjs), which renders a
// bus popup and must get a board with no envelope behind it (ages from the corrected
// clock alone, served_at unknown), not a ReferenceError.
function sourceDescriptor(sourceKey) {
  return typeof sources === "undefined" ? null : sources[sourceKey] || null;
}

// The board one vehicle's words are read against (positionBoard): its source's ingested
// envelope, the systems it belongs to, and the corrected clock.
function vehicleBoard(sourceKey, names, now = correctedNow()) {
  return positionBoard(sourceDescriptor(sourceKey) || {}, names, now);
}

// THE ONE CALL every vehicle surface makes for its words (popup line, compact railroad
// line, accessible name), so no surface composes its own.
function vehiclePosition(sourceKey, names, row, now = correctedNow()) {
  return positionQualifier(row, vehicleBoard(sourceKey, names, now));
}

// The earliest instant a currently fresh observation on the map crosses OBS_FRESH_S, on
// the corrected clock, or null. Kept because a marker's own observation goes stale by
// TIME PASSING, exactly as a system does, and the stale-set signature that wakes the
// sweeps from the animation tick is a set of SYSTEMS: a fix crossing 90 s between polls
// in a healthy feed changes no system, so before this it stayed bright until the next
// poll landed, up to fifteen seconds late. The tick compares one number per frame.
let nextObservationCrossing = null;

// THE AGE A VEHICLE MARKER IS DIMMED BY, at every opacity site: markerAge over its
// system's age and its own observation's (servedAge from the envelope's served_at), and
// the note of when that observation will cross if it has not yet.
function vehicleMarkerAge(sourceKey, systemAge, row, now = correctedNow()) {
  const source = sourceDescriptor(sourceKey);
  /* THE 6.3 ERRATUM IS APPLIED HERE, at the one composition every system's dimming goes
     through, so the rule has one home for all six of them rather than a copy per file. `gated`
     comes from OBSERVATION_GATED, the contract's 3.3 table transcribed and keyed by the family
     this layer already names, so the answer is a decision rather than a side effect of which
     models happen to carry a `system` field (which is what the first cut of this line read, and
     it was right by accident). helpers.js carries the table and the argument. */
  const gated = observationGated(sourceKey, row);
  const own = observationDimAge(row, observationAge(row, source ? source.servedAt : null, now), gated);
  if (own != null && !staleAge(own)) {
    const at = observationStaleAt(row);
    if (at != null && (nextObservationCrossing == null || at < nextObservationCrossing)) {
      nextObservationCrossing = at;
    }
  }
  return markerAge(systemAge, own);
}

// Has an observation crossed since the sweeps last ran? The animation tick's question.
function observationCrossed(now) {
  return nextObservationCrossing != null && now >= nextObservationCrossing;
}

/* ---------------- MR3 (R-d): ONE RE-READ OF A STATIC PAYLOAD THAT IS MISSING A FIELD -------

   THE PROBLEM, which is a deploy problem rather than a code one. /api/railroad-routes and
   /api/njt-routes are static-derived and served under an hour-long cache. A release that adds a
   FIELD to one of them (this stage needs route_short_name on the NJT payload; the branch before
   it added color and text_color to the railroad one) ships a frontend that reads the new field
   against a response the browser or an intermediary may hold from before the backend rolled. The
   payload is well formed and the field is simply absent, so nothing errors: every NJ Transit tag
   silently prints a route id where it should print NEC, for up to an hour, on exactly the deploy
   the feature ships in.

   THE ANSWER THE OPERATOR RULED (R-d): re-read the payload ONCE with cache "reload", which
   bypasses the HTTP cache for that one request, and then fall back to the id if the field is
   still absent. Not a poll and not a retry loop: "reload" either reaches a backend that has the
   field or reaches one that does not, and a second attempt cannot change which. The fallback is
   already correct on its own terms (railBranchCode returns the id for a route it cannot name),
   so this buys correctness on the deploy boundary and costs one extra request there.

   ONCE, AND ONLY WHEN THE FIELD IS ABSENT FROM EVERY ENTRY. A payload where SOME entries carry
   it is a payload from a backend that knows the field, and the entries without it are the feed's
   own gaps: NJ Transit's route 17 has no trips in an ordinary publication and the railroads
   publish no colour for some routes. Re-reading for those would re-read forever.

   A FOLLOW-UP, NOT A FIX: a version stamp on the static-derived endpoints would let the frontend
   ASK whether the payload predates the field instead of inferring it from absence. Recorded as
   such in docs/reviews/map-redesign-rounds.md under Stage MR3. */
// The predicate is in helpers.js (staticPayloadHasField), because it is pure and node asks it
// directly; only the fetch, which needs the page, is here.

// The routes payload, re-read once past the HTTP cache when `field` is absent from every entry.
// Returns null for a state the caller should retry (a warming 503, a network error, an empty
// payload is the caller's own judgement), and the parsed payload otherwise.
async function fetchRoutesPayload(url, field) {
  const read = async (init) => {
    const res = await fetch(url, { ...init, signal: AbortSignal.timeout(FETCH_DEADLINE_MS) });
    if (!res.ok) return null; // warming 503 (or transient error): the caller retries
    return res.json();
  };
  let routes;
  try {
    routes = await read({});
  } catch {
    return null;
  }
  if (routes == null) return null;
  if (!field || staticPayloadHasField(routes, field)) return routes;
  /* THE ONE RE-READ. A failure here keeps the payload we already have rather than discarding it:
     a cached response missing one field still draws every line, every colour and every station,
     and throwing it away to retry the whole loader would trade a fallback code for no map. */
  try {
    const fresh = await read({ cache: "reload" });
    if (fresh != null) return fresh;
  } catch {
    /* keep what we have */
  }
  return routes;
}

/* ---------------- A2: the one place a map marker is born ---------------- */

// EVERY L.marker ON THIS MAP COMES FROM HERE, and new systems (Amtrak, NJ Transit)
// must use it too. It is a seam, not a convenience.
//
// WHY A FACTORY RATHER THAN AN OPTION COPIED SIX TIMES. Leaflet gates two separate
// things on ONE option: `keyboard && (tabIndex = "0", role = "button")`. So the tab
// stop and the role are inseparable, and every marker in this app arrived
// tabbable, role="button", and nameless: a keyboard rider tabbed through every
// vehicle on the map before reaching a single control, hearing "button" each time
// and nothing else. The tab-order policy is that markers are NOT the keyboard path
// (the A1 station panel is, reachable by the skip link), so `keyboard: false` is
// correct everywhere. Written as six copied lines it would be six chances to forget,
// and the seventh system would forget; A1's announceUnlessTick exists for exactly
// this reason and this is the same lesson applied to the map. Sweeping the DOM after
// each poll to fix up markers would be worse still: a compensator running after the
// mistake instead of a design that cannot make it.
//
// The role has to be put BACK by hand, because keyboard:false took it away with the
// tab stop. role="img" with an aria-label is what a marker actually is: a graphic
// that means something. Touch screen-reader users, who navigate by pointer and not
// by Tab, still find and hear it.
/* A4: THE VANISHING-FOCUS DOOR, one place, on the popup path.

   WHERE IT LIVES. labeledMarker is the single birth seam for every marker in this app,
   enforced by markers.test.js, so a `remove` hook registered here is the symmetric death
   seam and covers every destruction path at once: the five per-system departure sweeps
   (group.removeLayer), a layer toggle (map.removeLayer on the group, which fires `remove`
   on every child), clearLayers, and a bare marker.remove(). That coverage is the reason
   for the placement, and it is pinned by mutation: deleting this hook fails A8a and A8c,
   which travel two different destruction paths.

   WHAT IS *NOT* LOAD-BEARING, corrected after mutation testing said so. The first version
   of this comment claimed the hook must run BEFORE Leaflet's own `remove: this.closePopup`
   (which callers install later, via bindPopup) so that it could see the popup still open.
   Measured: forcing marker.closePopup() to run first, at the top of this handler, leaves
   every spec green. The predicate does not need the popup to be OPEN, only for its ELEMENT
   to still contain the focused node, and a closing Leaflet popup lingers in the DOM for
   its fade. So the ordering is real but incidental, and saying otherwise would have left a
   future reader defending an invariant nothing depends on.

   WHAT IT DOES NOT TRY TO DO. It does not ask why the marker is going away. Measured at
   `remove` time, a departed vehicle and a hidden layer are byte-identical in every piece
   of Leaflet state (the group still has the marker, the map still has the group, the
   registry still has the key), so any attempt to tell them apart here would be guesswork.
   It does not need to: the predicate is about the RIDER, not the cause. A layer toggle
   moves focus to the checkbox the rider just activated, so the predicate is false and the
   door stays silent; a vehicle ageing out of the feed while its popup is open leaves focus
   inside the doomed subtree, and that is the case worth rescuing. */
function planVanishingFocus(subtree, { label = null, kind = "vehicle", reason = null } = {}) {
  return vanishingFocusPlan(subtree, document.activeElement, { label, kind, reason });
}

// SPLIT FROM THE PLAN, and the split is not cosmetic. The banner is rebuilt by replacing
// its children, so by the time the rescue runs the focused button is already gone and
// document.activeElement has fallen to the body: a predicate evaluated at that moment asks
// "is the body inside the banner", which is false, and the rescue silently declines to
// fire on precisely the case it exists for. Caught by A8d failing with active=BODY. So
// callers whose destruction happens AFTER the decision plan first and apply second; the
// marker door, whose hook runs BEFORE Leaflet tears anything down, can do both at once.
function applyVanishingFocus(plan) {
  if (!plan || !plan.rescue) return false;
  // The map container is the stable ancestor and is already a labeled tab stop (Leaflet
  // writes tabindex=0 on it; index.html gives it the name). It cannot itself vanish, which
  // is the whole reason it is the destination rather than, say, the nearest sibling.
  const container = document.getElementById("map");
  if (container) container.focus();
  announcePage(plan.message);
  return true;
}

function rescueVanishingFocus(subtree, options = {}) {
  return applyVanishingFocus(planVanishingFocus(subtree, options));
}

/* ---------------- MR3: the two rail icons, one shape each for three families ----------------

   THE MARKUP IS IN helpers.js AND ONLY THE WRAPPER IS HERE, which is the seam the rest of
   this file already keeps: helpers.js never touches Leaflet (it is loaded by node with no L
   at all), so the string is pure and testable and the four lines that need a browser are
   these. That is why railTagSvg exists as a string builder rather than as an L.divIcon.

   ONE BUILDER FOR LIRR, METRO-NORTH AND NJ TRANSIT, where before MR3 there were two: the
   hollow rect in systems/railroad.js and a byte-identical copy of it in systems/njt.js, with
   no shared helper between them, so changing one did not change the other. Three families
   drawing one grammar is the whole claim of this stage, and it is only true if they call one
   function. */

// A rail train's tag. `state` is railTagState's answer and `bearing` railTrainBearing's.
//
// iconAnchor IS [w/2, 21] AND THAT IS THE WHOLE GEOMETRY: the glyph box is 30 tall, the tag
// hangs in y 0 to 13, and the head is centred on y 21, so anchoring at 21 puts the head on
// the rail and lifts the tag clear of it. iconSize is the box, which is also the click box.
function railTagIcon({ system, code, color, textColor = null, state, bearing = null }) {
  const geom = railTagGeometry(system, code);
  return L.divIcon({
    className: `rail-tag-marker ${railFamilyClass(system)} rail-tag-${state.body}`,
    html: railTagSvg({ system, code, color, textColor, state, bearing }),
    iconSize: [geom.width, 30],
    iconAnchor: [geom.width / 2, 21],
    // The popup opens off the TAG, not off the head: a popup tipped at the rail would cover
    // the track the rider is reading. Negative y is up from the anchor, and 21 is the head's
    // offset from the tag's own top.
    popupAnchor: [0, -21],
  });
}

// A rail STATION. The drawn square is 10x10 and the icon is 20x20, so the click box clears
// WCAG 2.2's 24px floor together with the map's own padding rule (style.css says how) while
// the mark on screen stays a 10px square rather than becoming a blob at city zoom.
function railStationIcon(system = null) {
  return L.divIcon({
    className: `rail-stn-marker ${railFamilyClass(system)}-stn`,
    html: railStationSvg(),
    iconSize: [RAIL_STATION_BOX, RAIL_STATION_BOX],
    iconAnchor: [RAIL_STATION_BOX / 2, RAIL_STATION_BOX / 2],
    popupAnchor: [0, 0],
  });
}

/* ---------------- MR4: the other four families' icons, one wrapper each ----------------

   THE SAME SEAM AS MR3's TWO RAIL ICONS: the markup is a pure string in helpers.js and only
   the L.divIcon that needs a browser is here. Four wrappers rather than four inline builders
   is what lets a node test ask each family's markup one state at a time.

   NONE OF THESE FOUR NEEDS A THEME REGISTRY ENTRY, and that is the point of building them
   this way. Their strokes are `style="stroke: var(--paper)"`, so the cascade repaints them on
   a theme change with no sweep, no setIcon and no rebuild: a rider holding a popup open keeps
   it. Only the canvas marks (the lines and the station circles) need registerCanvasFamily. */

// PATH's train. THE ANCHOR AND THE BOX ARE UNCHANGED FROM BEFORE MR4 and that is deliberate:
// [8, 20] on a 16px box floats the diamond's tip at the station dot beneath it, which is what
// keeps BOTH click targets alive (the dot for arrivals, the diamond for the train), and
// layout.spec.js A4c asserts the 4px of clearance that difference is. The design calls the
// mark "lifted (`v-lift`)"; this app has always spelled that as an anchor plus the
// bottom-aligned hit halo in style.css, which is the same lift in the app's own idiom.
function pathTrainIcon(color) {
  return L.divIcon({
    className: "path-marker",
    html: pathDiamondSvg(color),
    iconSize: [PATH_DIAMOND_BOX, PATH_DIAMOND_BOX],
    iconAnchor: [PATH_DIAMOND_BOX / 2, 20],
    popupAnchor: [0, -20],
  });
}

// A ferry boat. The docked/under-way state rides on the CLASS, not on the markup: the class
// is what style.css and the specs read, and the opacity that goes with it is a marker
// opacity (ferryBaseOpacity) so it compounds with the freshness contract's dimming instead of
// being overridden by it. P4b2 pins that compound at 0.55 * 0.45.
function ferryBoatIconFor(color, state) {
  return L.divIcon({
    className: `ferry-marker ferry-${state}`,
    html: ferryHullSvg(color),
    iconSize: FERRY_HULL_BOX,
    iconAnchor: [FERRY_HULL_BOX[0] / 2, FERRY_HULL_BOX[1] / 2],
  });
}

// A bus. One box for the arrow and the dot (helpers.js says why), so a bus gaining or losing
// a heading swaps its glyph without moving its anchor under the rider's pointer.
function busMarkIcon(color, bearing) {
  return L.divIcon({
    className: "bus-marker",
    html: busMarkSvg(color, bearing),
    iconSize: [BUS_MARK_BOX, BUS_MARK_BOX],
    iconAnchor: [BUS_MARK_BOX / 2, BUS_MARK_BOX / 2],
  });
}

/* ---------------- MR5: the mark a popup's title wears ----------------

   THE MARKER'S OWN ICON, NOT A SECOND CALL TO THE BUILDER THAT MADE IT. Section 5 draws a
   route mark before a popup's title, and the strongest form of "the popup shows what the rider
   clicked" is the markup that marker is wearing right now: the tag with the branch code this
   train actually has, the chevron at the bearing it is actually drawn at, the hull in the
   colour its route resolved to. Rebuilding it here would mean reassembling each family's
   arguments a second time (railroadIcon alone takes a train, its previous row and a clock) and
   the two would disagree exactly where it matters, on the trains whose state is interesting.

   ONE HELPER FOR SIX FAMILIES, and it is short because every vehicle in this app is an
   L.divIcon whose html is a string from helpers.js. That is the seam MR3 and MR4 built: the
   markup is pure, the wrapper is here.

   AND THE THREE CANVAS FAMILIES GET NOTHING, which is the rule rather than an omission: a
   subway station, a PATH station and a ferry dock are circleMarkers drawn on a shared canvas,
   they have no element and no icon, and there is no string to borrow. getIcon() on one throws
   nothing and returns undefined, so those popups print a title with no mark, which is what
   "the popup's mark is the map's mark" means when the map's mark is a painted circle. */
function markerMarkHtml(marker) {
  const icon = marker && typeof marker.getIcon === "function" ? marker.getIcon() : null;
  const html = icon && icon.options ? icon.options.html : null;
  return typeof html === "string" ? html : "";
}

/* A FERRY DOCK'S NAME, which the design asks for ("Docks: ... names shown") and which no
   dock has ever had.

   IT SHARES THE RAIL BINDER'S BODY AND CARRIES ITS OWN CLASS, `stn-label ferry`. The six
   tooltip defaults that binder takes back off (the 0.9 inline opacity Leaflet writes in
   onAdd, the pane, the interactive flag among them) are invisible in a diff and MR2 paid for
   each of them once; copying the call rather than the reasoning is how a third family would
   pay again.

   NEVER `hub`, for the rail binder's reason: a hub is a SUBWAY transfer station by one
   predicate in helpers.js, and a dock is not one.

   AND THE CLASS IS WHY paintZoomBand STOPPED COUNTING THE DOM. A dock label joins `.stn-label`
   exactly as a rail label did in MR3, and MR3's sentinel asked the DOM "how many subway
   labels are there" by counting `.stn-label:not(.rail)`, which this family would have
   silently joined. The band asks the station REGISTRY now, by kind, so no family arriving in
   this class can change the subway's answer again. That is the carry-forward, paid rather
   than patched. */
function bindFerryDockLabel(marker, text) {
  marker.on("tooltipopen", (event) => {
    const el = event.tooltip?.getElement?.();
    if (el) el.setAttribute("aria-hidden", "true");
  });
  marker.bindTooltip(text, {
    permanent: true,
    direction: "right",
    // Clear of a radius-4 dot in a 1.5 stroke, where the rail square's 9 clears a 10px box.
    offset: [8, 0],
    className: "stn-label ferry",
    interactive: false,
    pane: "stationLabelPane",
    opacity: 1,
  });
}

/* A RAIL STATION'S NAME, on the label pane the subway's names already use, with the SAME
   permanent tooltip, the same aria-hidden and the same full opacity, and NEVER the hub class.

   WHY IT SHARES subway.js's PATH RATHER THAN COPYING IT. Three of the six tooltip defaults
   that rule takes back off are invisible in a diff (the 0.9 inline opacity Leaflet writes in
   onAdd, the pane, and the interactive flag), and MR2 paid for each one once already. The
   only differences here are the class, which adds `rail` so the zoom band can gate rail
   names from 11 while the subway's start at 12, and the absence of `hub`: a hub is a subway
   transfer station by one predicate in helpers.js, and a rail station is never one of those.

   ARIA-HIDDEN FOR THE SAME REASON MR2 GAVE: a label is the first DOM these stations have
   ever had, and 300 bare place names in the reading order whose only information is WHERE
   they are would say nothing a screen reader can use. The station panel is the text surface
   and it is one Tab away. a11y.spec.js measures the ink against the halo directly, because
   axe cannot see an aria-hidden node. */
function bindRailStationLabel(marker, text) {
  marker.on("tooltipopen", (event) => {
    const el = event.tooltip?.getElement?.();
    if (el) el.setAttribute("aria-hidden", "true");
  });
  marker.bindTooltip(text, {
    permanent: true,
    direction: "right",
    // Clear of the 10px square plus its stroke, where the subway's 7 clears a 5px dot.
    offset: [9, 0],
    className: "stn-label rail",
    interactive: false,
    pane: "stationLabelPane",
    opacity: 1,
  });
}

function labeledMarker(latlng, options, name) {
  const marker = L.marker(latlng, { ...options, keyboard: false });
  // Relabel whenever Leaflet builds the element again. Toggling a layer off and on
  // DESTROYS the icon element and creates a fresh one, which restores tabindex and
  // role from marker options but loses anything we wrote as an attribute; verified
  // in the step-1 inventory. Without this, every marker on a re-shown layer would be
  // silently anonymous again, and nothing else would notice for a static system like
  // AirTrain that has no poll to re-apply the name.
  marker.on("add", () => applyMarkerName(marker));
  // A4: and the symmetric door. Registered BEFORE the caller's bindPopup so it runs
  // before Leaflet's own closePopup and can still see what the rider was holding.
  marker.on("remove", () => {
    const popup = typeof marker.getPopup === "function" ? marker.getPopup() : null;
    const el = popup && typeof popup.getElement === "function" ? popup.getElement() : null;
    // `_vanishReason` is set by a departure sweep that knows why the vehicle left (6.3: a
    // railroad fix withheld for its age); absent, the general sentence is spoken.
    rescueVanishingFocus(el, { label: marker._a11yName, kind: "vehicle", reason: marker._vanishReason ?? null });
  });
  // RELABEL AFTER A RE-SKIN TOO, so the name survives no matter what order a caller
  // does things in. Today setIcon happens to reuse the same element and attributes
  // happen to survive, but that is a Leaflet implementation detail (Icon._setIconStyles
  // reassigns className wholesale, and DivIcon.createIcon reuses the div it is handed)
  // and two systems already call setIcon AFTER setMarkerName. Making survival depend
  // on call-site ordering is the copied-guard liability again, one level down: wrap it
  // once here and no system can get the order wrong.
  const setIcon = marker.setIcon.bind(marker);
  marker.setIcon = (icon) => {
    const result = setIcon(icon);
    applyMarkerName(marker);
    return result;
  };
  setMarkerName(marker, name);
  return marker;
}

// Set or refresh a marker's accessible name. SAFE AND EXPECTED TO BE CALLED EVERY
// POLL: the name is remembered on the marker so a rebuilt element can be relabeled
// from the last known value, and it announces nothing (a marker is not a live region).
// AN UNCHANGED NAME IS NOT REWRITTEN, since 6.3: every stale sweep re-derives its
// markers' names as well as their opacity, and those sweeps visit every marker on the
// map, so a write per marker per sweep would be thousands of attribute writes for the one
// name that changed. A rebuilt element is relabeled by the `add` hook and the setIcon
// wrapper above, never by this, so skipping a same-name call loses nothing.
function setMarkerName(marker, name) {
  if (marker._a11yName === name) return;
  marker._a11yName = name;
  applyMarkerName(marker);
}

function applyMarkerName(marker) {
  const el = marker.getElement();
  if (!el || !marker._a11yName) return; // not on the map yet, or on a hidden layer
  el.setAttribute("role", "img");
  el.setAttribute("aria-label", marker._a11yName);
  // The inner svg is decoration: it repeats what the label already says, and left
  // exposed it reads as a second, nameless graphic inside the first.
  const svg = el.querySelector("svg");
  if (svg) svg.setAttribute("aria-hidden", "true");
}

/* ---------------- A2: reaching a station a vehicle is sitting on ---------------- */

// THE PRINCIPLE, AND IT LIVES HERE ONCE. Both systems that need it cite this comment
// rather than restating it.
//
// A vehicle marker sits in markerPane (z 600); station dots are drawn on a canvas in
// stationPane (z 450). A vehicle parked on its station therefore swallows every click
// meant for the station, and the arrivals a rider actually came for become unreachable
// at that pixel. Measured in the step-1 inventory: clicking a station with a train on
// it opens the TRAIN popup, and the station popup never fires.
//
// There are two honest resolutions, and where the position came from decides which
// ones are available.
//
// DERIVED POSITIONS MAY OFFSET. A subway train is placed at its stop by stop_id and a
// PATH train is interpolated along its route: neither position is a measurement, so
// drawing the marker a few pixels above the point costs nothing true. PATH set this
// precedent (iconAnchor [8, 20], path.js) and the subway follows it. Nudging a
// computation lies to no one.
//
// MEASURED POSITIONS MUST NOT OFFSET. Moving a GPS marker would make the map say the
// vehicle is somewhere it is not, which is the one thing this project does not do.
//
// A CROSS-LINK IS HONEST FOR EITHER, because it moves nothing at all: it adds a way to
// reach the station without touching where anything is drawn. So offsetting is the
// narrower permission and linking is the general one.
//
// PLACED RAILROAD TRAINS ARE DERIVED, AND TAKE THE CROSS-LINK ANYWAY. A railroad train
// served `placed` or `estimated` is drawn from a prediction, at or toward the station
// its stop_id names; a railroad train with real GPS carries no stop_id at all. So a
// placed train would qualify for the offset by the rule above. It gets the link
// instead, as the deliberate conservative choice: the link never moves a marker, and
// the subway and PATH offsets are tuned to grid geometry those two systems share and
// the commuter railroads do not. Recorded because an earlier version of this comment
// had it backwards, calling placed trains measured, and a reader who inherited that
// would draw the wrong conclusion about every system here. Since 6.3 the gate is
// railroadAtItsStation rather than stop_id, for the reason NJ Transit's is
// njtAtItsStation: a train gliding TOWARD its stop is not at it, and gets its link
// only once it is drawn there.
//
// THE LINK IS NOT A REPLACEMENT FOR THE PANE ORDERING. Vehicles still paint above
// stations, because that is the right visual layering; the link exists so the station
// under a vehicle is still reachable, not so the layering can be ignored.
//
// AND IT IS NEVER A GUESS. "At" is read from a field the payload already carries, per
// system, never from distance math. A cross-link pointing at the wrong station is
// worse than no cross-link at all: a rider who follows it gets confidently incorrect
// arrivals, and nothing on screen tells them so. A vehicle that does not name a
// station gets no link.
// MR5: SECTION 5's OWN NAME FOR THIS BUTTON. It was `popup-crosslink` from A2 until here, and the
// class is the one vocabulary item this stage could adopt by renaming alone: the rules already
// matched section 5's `.xlink` (600 11px, a --divider border, a transparent ground) after the
// chrome commit tokenised them, and the class is written once, here.
const CROSSLINK_CLASS = "xlink";

// The link's markup, or "" when this vehicle names no station. `stationKey` must be a
// SYSTEM-QUALIFIED registry key: station ids collide across systems (see the
// stationRegistry comment above, where the contract tier measured 21 of 24 ferry dock
// ids colliding with Metro-North station ids), so a bare id could resolve to a station
// in an entirely different system.
function crossLinkHtml(stationKey) {
  const entry = stationKey ? stationRegistry.find((row) => row.key === stationKey) : null;
  // No registry entry means the station layer has not loaded yet, or this id is not a
  // station we know. Either way there is nothing to link to, and inventing a
  // destination is the failure this whole comment is about.
  if (!entry) return "";
  /* A real button, not a styled span: it is keyboard reachable, it activates on Enter
     and Space without any handler of ours, and it announces as a button. The station
     name is IN the accessible name, so "Also here" is never announced on its own.

     MR5: SECTION 5's ARROW IS NOT DRAWN, AND IT WAS MEASURED BEFORE IT WAS DROPPED. The design
     draws this button as "Also here: Jamaica →". Added as an aria-hidden span (so a screen
     reader would not read "right arrow" after the station's name), axe reported a NEW
     undecidable finding on it at every width and in both themes: "Element content contains
     only non-text characters", on `button.xlink span`. The operator's ruling on this stage's
     surface is that the undecidable inventory does not grow, and a decorative glyph is the
     weakest possible reason to grow it: the button already says where it goes, and its own
     border already says it is pressable. So the arrow is recorded as a deviation beside the
     README's list rather than drawn, and the words are unchanged. */
  return (
    `<button type="button" class="${CROSSLINK_CLASS}" data-station-key="${esc(entry.key)}">` +
    `Also here: ${esc(entry.name)}</button>\n`
  );
}

// One delegated handler for every cross-link on the map, bound once. Delegation rather
// than per-popup wiring because popup content is regenerated from a function on every
// open and every update, so any listener attached to the rendered nodes would be
// discarded and re-attached constantly.
//
// BOUND IN THE CAPTURE PHASE, and it does not work otherwise. Leaflet calls
// disableClickPropagation on every popup container, which stops click events inside a
// popup from bubbling out (so a click on a popup does not also reach the map beneath
// it). A bubble-phase listener on document therefore never sees a cross-link press at
// all: the first draft used one and the button did nothing, from mouse or keyboard.
// Capture runs downward from document before the container's own listener, so it
// arrives first and is unaffected. Enter and Space on a <button> both synthesize a
// click, so this one listener is the keyboard path too.
document.addEventListener(
  "click",
  (event) => {
    const button = event.target.closest ? event.target.closest(`.${CROSSLINK_CLASS}`) : null;
    if (!button) return;
    event.preventDefault();
    openStationFromCrossLink(button.getAttribute("data-station-key"));
  },
  true,
);

// Open the linked station's popup and MOVE FOCUS INTO IT. This is the one place where
// moving focus is correct rather than rude: the rider activated a link asking to go
// somewhere, so leaving focus behind on a button whose popup just closed would strand
// them exactly as A1's closing paths would have. Opening a Leaflet popup replaces the
// popup pane's contents, so the button the rider pressed no longer exists by the time
// this returns.
function openStationFromCrossLink(stationKey) {
  const entry = stationRegistry.find((row) => row.key === stationKey);
  if (!entry || !entry.marker) return false;
  entry.marker.openPopup();
  // Ask the POPUP for its element rather than querying the document for the first
  // ".leaflet-popup-content". Leaflet fades a closing popup out, so for the length of
  // that animation the old popup is still in the DOM and a document query returns the
  // one we just closed: the first draft focused the train's dying popup and the
  // station's never received focus at all.
  const popup = entry.marker.getPopup();
  const root = popup && popup.getElement ? popup.getElement() : null;
  const content = root ? root.querySelector(".leaflet-popup-content") : null;
  if (!content) return false;
  // tabindex -1 makes it programmatically focusable without adding a tab stop: the
  // rider lands here, and Tab from here continues into the popup's own controls.
  //
  // ESCAPE NOW CLOSES THE POPUP FROM HERE, and this comment is kept rather than deleted
  // because the reasoning that deferred it is the reasoning that A4 finally acted on.
  //
  // A2 recorded the gap and declined to fix it here: Leaflet binds Escape on the MAP
  // container, focus inside a popup is outside it, so the key never reached any handler
  // (measured then: popup still open, focus unmoved). The finding's stronger claim, that
  // there was no way out, was false even then, because one Tab from this landing point
  // reaches the popup's own close button. What A2 said was that a keyboard-dismiss binding
  // is a MAP-WIDE decision and does not belong in the cross-link's landing path.
  //
  // A4 is where that map-wide decision got made. The Escape ladder in map.js is one
  // document-level handler that closes the topmost transient surface (an open popup, then
  // the station panel) from anywhere on the page, so this landing point inherits it
  // without knowing anything about keys. A9d asserts the ladder behaves identically from
  // inside a popup and from inside the panel, which is exactly the asymmetry A2 measured.
  content.setAttribute("tabindex", "-1");
  content.focus();
  return true;
}

// A2 FOLLOWUP, RESOLVED IN A4: WHERE FOCUS GOES WHEN A CONTROL IS DESTROYED WITH NO
// SUCCESSOR. Everything below restores focus to a live replacement, which was the case A2
// could answer honestly. The two cases with no replacement at all, both measured then
// landing the rider on document.body, are the ones A4's vanishing-focus door now catches:
// focus moves to the map container and the page live region says so once. See
// rescueVanishingFocus above and tests/e2e/vanish.spec.js. The original statement of both
// cases is kept below because it is the measurement that justified the fix:
//
//   1. A VEHICLE LEAVES THE FEED while its popup is open and focused. Measured on a
//      railroad train removed from one poll's payload: railroads.size 2 -> 1,
//      marker gone, zero .leaflet-popup nodes, document.activeElement === document.body.
//      Every system's apply* loop removes departed vehicles the same way, so the same
//      path exists five times over. It predates this phase; A2 neither introduced it nor
//      closes it.
//   2. THE LAST AGENCY-WIDE ALERT CLEARS while the rider is on the banner's dismiss
//      button, including when the rider is the one who dismissed it.
//
// It is one question, not two, and it is a product question rather than a mechanical
// one: silently moving focus to a landmark is a WCAG 3.2.2 change of context the rider
// did not ask for, and moving it WITH an announcement means deciding what the page says
// when a train a rider was reading about stops existing. The door for saying it already
// exists (announcePage). Fixing it badly is worse than the strand, and this phase has no
// review round left to cover a five-call-site change, so it is filed rather than guessed
// at. Until then a rider who lands on the body reaches the skip link with one Tab.
//
// UPDATING AN OPEN POPUP DESTROYS WHATEVER HAS FOCUS INSIDE IT, so every update goes
// through here. This is the THIRD Leaflet behaviour of the same family as the two above,
// and the review found it by reproduction: a rider who tabbed to a cross-link and waited
// one poll had document.activeElement drop to BODY while the button was still on screen,
// and their Enter did nothing. That is precisely the stranding the A1 focus contract
// exists to prevent, reintroduced through a feature built FOR keyboard riders.
//
// Popup content here is bound as a FUNCTION, so a refresh re-renders it wholesale and
// the old nodes are discarded. Nothing warns; focus simply lands on the body.
//
// Restores the same control when the re-rendered popup still has one, and falls back to
// the popup's content container so the rider is at worst still inside the popup they
// were reading rather than at the top of the document.
//
// THE CONTROL IS IDENTIFIED BY ITS ROLE IN THE POPUP, NOT BY WHAT IT POINTS AT, and
// round 3 of the review caught the difference by reproduction. The first version matched
// the cross-link on its data-station-key, with a comment claiming the key "survives the
// re-render because the popup describes the same vehicle". The key describes the
// STATION: railroad.js builds it from the train's current stop_id, so a train advancing
// one stop changes it by design. The rider tabbed to "Also here: Jamaica", the poll
// rendered "Also here: Hicksville", the key no longer matched, and focus was dumped on
// the inert content div with a live button on screen. Worse, it stuck: the content div
// survives later updates, so the guard below returned early on every subsequent poll and
// focus never came back. A vehicle popup has at most one cross-link (one call site, in
// railroad.js), so asking for that one is both simpler and correct.
//
// A DISSENT ON THE RECORD, because the review panel that raised this also REFUTED it and
// the disagreement is a judgment call rather than a fact. The skeptic reproduced
// everything above and then argued two things. First, that the severity is lower than
// round 1's: true, and worth stating plainly. Focus lands inside the popup, not on the
// body, so the rider is one Tab from the button rather than at the top of the document.
// Second, that this fix is worse than the defect, because restoring focus to a relabeled
// button means a rider who chose "Jamaica" and waited can press Enter and arrive at
// Hicksville without having chosen it.
//
// The fix ships anyway, for three reasons. The alternative leaves a rider holding a
// live-looking control whose Enter does nothing, which is the exact symptom this phase
// has now fixed twice and the reason the helper exists at all. The principle the skeptic
// cited (a cross-link pointing at the wrong station is worse than no cross-link) is
// about a link that names a station the vehicle is NOT at; this one names, correctly,
// where the train now is. And a popup held open across polls is live by design: its next
// stop, its countdowns and its staleness line all change underneath the rider already,
// so a control that changes with them is consistent rather than treacherous, and it
// announces its new name at the moment focus reaches it.
//
// The residual risk the skeptic names is real: a rider not listening when focus is
// restored can act on a changed destination. It is bounded by the station popup that
// opens naming itself. A3g pins the announcing half deliberately, asserting the button
// reads "Hicksville" before asserting Enter goes there.
function updatePopupKeepingFocus(marker) {
  const popup = marker.getPopup && marker.getPopup();
  if (!popup) return;
  const before = popup.getElement ? popup.getElement() : null;
  const active = document.activeElement;
  const hadFocus = !!(before && active && before.contains(active));
  const hadCrossLink = !!(hadFocus && active.classList && active.classList.contains(CROSSLINK_CLASS));

  popup.update();

  if (!hadFocus) return; // focus was elsewhere: moving it now would be the rude case
  const after = popup.getElement ? popup.getElement() : null;
  if (!after) return;
  // AND ONLY RESTORE WHAT WAS ACTUALLY LOST. Round 1 of the review shipped this helper
  // without this line and five independent lenses caught the same regression: update()
  // reassigns the CONTENT node's innerHTML and nothing else, so the popup's own close
  // button is a sibling that survives untouched. Restoring unconditionally therefore
  // yanked focus off a live control and dropped it on an inert div, on every vehicle
  // popup, every fifteen seconds, and the rider's Enter stopped closing the popup. The
  // element that still holds focus is by definition not stranded, so leave it alone.
  if (after.contains(active)) return;
  const content = after.querySelector(".leaflet-popup-content");
  // The station this now points at may differ from the one the rider tabbed to, and
  // that is the honest outcome: the button carries the station name in its accessible
  // name, so a restored focus announces the new destination before the rider can act on
  // it. A vehicle that has stopped naming a station renders no cross-link at all, and
  // then the content container below is the right landing place.
  const sameControl = hadCrossLink ? after.querySelector(`.${CROSSLINK_CLASS}`) : null;
  const destination = sameControl || content;
  if (!destination) return;
  // The content container is not naturally focusable; -1 makes it a landing place
  // without adding a tab stop, exactly as the cross-link handler does.
  if (destination === content) content.setAttribute("tabindex", "-1");
  destination.focus();
}

/* ----- Station popups + live arrivals, shared by subway, railroad and PATH ----- */

// Canvas-rendered so ~470 circle markers stay cheap and hit-testable; on its
// own pane (above the route-line canvas) so station clicks land here.
//
// A2 FOOTNOTE, because it is the reason station dots have no accessible name: a
// canvas-rendered circleMarker produces NO DOM element at all, so there is nothing to
// put a role or a label on. Naming ~470 station dots would mean abandoning the canvas
// renderer, which is the canvas work this phase deliberately does not do. Stations are
// reachable as named, keyboard-navigable text through the A1 station panel instead,
// which is the surface built for exactly that. AirTrain stations are the exception:
// they are L.marker with a divIcon (they need a shape a circle cannot draw), so they
// have an element and they get a name like any other marker.
const stationRenderer = L.canvas({ padding: 0.5, pane: "stationPane" });

// Shared popup machinery for BOTH station kinds (subway + railroad). One popup
// is open at a time (Leaflet closes others). A request token guards against a
// slow fetch landing after the user clicked a different station (of either
// kind, since the token is shared), and a 1s timer ticks countdowns down from
// absolute arrival timestamps without re-fetching. The last good arrivals
// payload lives on openStation so the tick and the 15s refresh share one source
// of truth (no captured-body closure that a later call could leave firing over
// newer state). openStation carries the station, its marker, the fetched body,
// the arrivals fetch `url`, and a kind-specific `render(station, body)`; the
// fetch/guard/timer skeleton below is otherwise kind-agnostic.
/* ---------------- The station registry (A1) ---------------- */

// EVERY STATION THE APP HAS LOADED, IN ONE PLACE. It did not exist before A1:
// each loader fetched its stops, built markers, and dropped the records on the
// floor, so the station panel had nothing to search. The loaders now register
// what they loaded as they build each marker, which keeps the arrivals URL and
// the marker written once per system rather than once per surface.
//
// Entries are appended as the loaders resolve, which they do asynchronously and
// in a race, so the panel must tolerate a partial registry and re-read it rather
// than snapshot it. searchStations sorts totally, so the display order never
// depends on which loader won.
//
// `key` is system-qualified because station ids collide ACROSS systems: the
// railroad and ferry id spaces are both bare integers, and the contract tier
// measured 21 of 24 ferry dock ids colliding with Metro-North station ids. A bare
// id would silently merge two different places.
const stationRegistry = [];

// kind drives the arrivals shaping and the vehicle noun; systemLabel is what the
// rider sees. AirTrain has no live arrivals at all, so it registers with a null
// arrivalsUrl and the panel renders its scheduled headways instead: system-shape
// honesty, not a special case, and the same branch a future system with no
// realtime feed would take.
function registerStation(entry) {
  stationRegistry.push(entry);
  // TELL THE PANEL, because it may already be on screen showing "Loading
  // stations..." from before this loader resolved. The docked-open desktop default
  // renders the panel at load time, when the registry is usually still empty, so
  // without this notification the list stayed on its loading line until the rider
  // typed something. Late-bound by name rather than wired at definition time,
  // because stations.js loads AFTER this file; the loaders all run later still, so
  // the function exists by the time any of them call it.
  if (typeof stationsRegistryChanged === "function") stationsRegistryChanged();
}

let stationSeq = 0;
let stationTimer = null;
let openStation = null; // { station, marker, body, url, render } while open

// Repaint the open popup from openStation.body. Reading the shared body (rather
// than a value captured per fetch) is what stops a stale tick from overwriting
// newer content: there is only ever one body to draw, the current one.
function renderStation() {
  if (!openStation || !openStation.body) return;
  const { station, marker, body, render } = openStation;
  if (marker.isPopupOpen()) marker.setPopupContent(render(station, body));
}


/* MR5: THE TWO STATES A BOARD PASSES THROUGH ARE IN THE SAME GRAMMAR AS THE BOARD.

   A station popup has three states, not one: loading, loaded and failed. The loaded one is section
   5's (a kicker, a title, a board); these two were still MR4's bold name over a muted line, so the
   first thing a rider saw on every station click was a 13px bold name that then jumped to a 17px
   title, and a board whose fetch failed kept the old head for as long as the popup stayed open.

   IT IS ALSO WHAT MAKES THE COVERAGE CLAIM TRUE OF STATES AND NOT ONLY OF FAMILIES. pins.spec.js
   P5d asserts that every word a rider reads in a popup belongs to a named slot, and it opens every
   surface in its LOADED state; a bare `<b>` belongs to no slot, so the claim held over popup
   families and would have failed over popup states. Now both states are a title and a note.

   NO KICKER, because these states do not know the system word: `openStation`'s descriptor carries
   the station and the renderer, and the system word is the renderer's. A title and a note is what
   this surface can say honestly. */
function stationError(station, message) {
  return (
    popupTitleHtml({ text: station.name ?? station.id }) +
    `<div class="popup-sub">${esc(message)}</div>\n`
  );
}

// refresh=false is a fresh popup open (show a Loading state, surface errors).
// refresh=true is the 15s background refresh of an already-open popup: keep the
// current arrivals ticking, swap in new data when it lands, and stay quiet on a
// failed poll rather than blanking good data with a Loading or error message.
// Reads the current openStation descriptor for the url/render, so it is the same
// skeleton for either station kind.
async function openStationArrivals({ refresh = false } = {}) {
  const open = openStation;
  if (!open) return;
  const { station, marker, url } = open;
  const seq = ++stationSeq;
  if (!refresh) {
    // Stop the previous tick up front so it cannot fire during this fetch.
    clearInterval(stationTimer);
    stationTimer = null;
    // The loading state in the same grammar as the board it becomes (stationError says why).
    marker.setPopupContent(stationError(station, "Loading arrivals…"));
  }
  let body;
  try {
    // AbortSignal.timeout bounds this arrivals fetch (R2); an abort rejects into the
    // catch below, which on a background refresh keeps the last-known arrivals
    // ticking rather than wedging the popup on "Loading…". This whole-fetch deadline
    // is ORTHOGONAL to the stationSeq guard: seq discards a fetch superseded by a
    // new click or a popup close (a user-scoped supersession), while the timeout cuts
    // off a fetch that simply never lands. A timeout is not a seq bump.
    const res = await fetch(url, { signal: AbortSignal.timeout(FETCH_DEADLINE_MS) });
    if (seq !== stationSeq) return; // superseded by another station click or a close
    if (!res.ok) {
      if (!refresh) {
        const err = await res.json().catch(() => null);
        marker.setPopupContent(
          stationError(station, err?.detail ?? `Arrivals unavailable (HTTP ${res.status})`),
        );
      }
      return; // a failed background refresh keeps the last-known arrivals ticking
    }
    body = await res.json();
  } catch {
    if (seq !== stationSeq) return;
    if (!refresh) {
      marker.setPopupContent(stationError(station, "Arrivals unavailable (network error)"));
    }
    return;
  }
  if (seq !== stationSeq) return;
  // NOTE: the skew baseline is NOT calibrated here, and the reason has CHANGED under
  // it. It used to be that the arrivals endpoints carried no served_at (only the five
  // vehicle feeds did, R1), so there was nothing to calibrate from and calibrating off
  // their fetched_at was the audit poison that PR removed. The freshness contract's
  // 6.1 gave all five arrivals envelopes a served_at, and since 6.2 every board READS
  // it, as the anchor each row's age is measured from (boardFreshness, servedAge). It
  // still does not calibrate minClockOffset: the 15s vehicle-feed poll keeps that fresh.
  // BOUNDED BOOT RACE: a client whose wall clock is materially wrong that opens a
  // station popup in the sub-second window before the first vehicle poll resolves
  // sees an uncalibrated countdown, and since 6.2 an uncalibrated "as of" on every row
  // (spoken on the panel, and spoken again when calibration lands and the qualifiers
  // clear); it self-corrects on the very next 1s tick once a poll lands. The complete
  // fix is calibrating off the arrivals served_at, or gating the countdown on a
  // settled baseline, and it stays deferred to R3's cold-start work: minClockOffset is
  // a global the countdowns and the status line share, and changing what feeds it is
  // no part of the boards' age rule.
  if (openStation === open) openStation.body = body;
  renderStation();
  if (!marker.isPopupOpen()) return;
  // (Re)start the single tick now that fresh data is in place.
  clearInterval(stationTimer);
  stationTimer = setInterval(renderStation, 1000);
}

// Wire one station circleMarker to the shared popup lifecycle. makeDescriptor(marker)
// builds the openStation descriptor (kind-specific url + render); the seq bump,
// timer teardown, and one-popup-at-a-time invalidation are identical for both
// kinds, so they live here once.
function bindStationPopup(marker, makeDescriptor) {
  return marker
    .bindPopup("", POPUP_OPTIONS)
    .on("popupopen", function () {
      openStation = makeDescriptor(this);
      openStationArrivals();
    })
    .on("popupclose", function () {
      stationSeq++; // invalidate any in-flight arrivals fetch for this popup
      clearInterval(stationTimer);
      stationTimer = null;
      if (openStation?.marker === this) openStation = null;
    });
}

/* ---------------- Service alerts (station popups) ---------------- */

// Active alerts indexed by (system, stop) and (system, route), rebuilt each poll.
// Starts empty, so a popup opened before the first fetch simply shows no alerts.
let alertsIndex = indexAlerts([]);

// Alerts freshness: the fetched_at the backend last reported, and the last banner
// set. The alerts loop swallows failures (below), so without an explicit freshness
// signal the banner and popups silently imply the alert set is current when the feed
// may have stopped updating. alertsStale() gates the honesty marker on
// alertsFetchedAt; lastBannerAlerts lets tickAlertBanner re-render the marker while
// polls are failing (loadAlerts only re-renders on success).
//
// C1: this tracks fetched_at, not served_at. fetched_at advances only on a backend
// poll that DECODED, so it stops moving during an alert-feed outage; served_at is
// stamped at response build and so stayed fresh forever behind a frozen index,
// which is why the R1 marker could never fire for that outage. See alertsStale().
let alertsFetchedAt = null;
let lastBannerAlerts = [];

// The per-system alert health from the last successful /api/alerts body, in
// ingestSystems' shape: {fetchedAt, ok, retainedSince} per alert feed. C2 already put
// this block on the wire (it rides on /api/alerts rather than only on /api/status
// precisely because the client never fetches /api/status), and until F11 the client
// collapsed it to one number and threw the rest away.
//
// WHO NEEDS THE UNCOLLAPSED VERSION: the station panel, which shows ONE system. The
// envelope minimum is right for the agency-wide banner and wrong for a station board,
// where it would hedge a current NJ Transit departure list because the ferry alerts
// feed is frozen; and retained_since is not expressible as a freshness number at all,
// because a retained set is not late, it is held. alertSourceNote reads both.
//
// INGESTED UNDER THE SOURCE KEY "alerts", which is not an alert system name. A body
// with no systems block at all makes ingestSystems synthesize a single entry under
// that key, and no lookup by a real system ("subway", "LIRR", "njt", ...) can collide
// with it, so that shape reads as "this system has no block of its own" and
// alertSourceNote falls back to the envelope basis.
let alertsSystems = {};

// When this client first ASKED for alerts. It is the age basis while alertsFetchedAt
// is still null, so a backend that has never filled its index (every feed down since
// boot, so /api/alerts errors and loadAlerts swallows it) discloses after the same
// threshold instead of showing a confident alert-free map forever. Stamped once, at
// the first attempt, not per attempt, or it would reset the grace period every poll.
let alertsFirstAttemptAt = null;

// The skew-corrected client clock, matching the arrivals-countdown basis.
function alertsClockNow() {
  return Date.now() / 1000 - (minClockOffset ?? 0);
}

// The muted "alerts may be out of date" marker, or "" when the alerts feed is fresh.
// Honesty, not alarm: shared by the banner and every popup alert block so a stale
// alerts feed is disclosed everywhere the alert set is shown (or implied absent).
function staleAlertsMarker() {
  return alertsStale(alertsFetchedAt, alertsClockNow(), alertsFirstAttemptAt)
    ? `<div class="alert-stale">alerts may be out of date</div>`
    : "";
}

// Poll /api/alerts on the alerts cadence. WHY a failed or non-200 fetch is swallowed
// and keeps the last-known index: alerts are a decorative overlay, so their
// staleness or absence must never surface an error or delay the arrivals a rider
// clicked for. There is no user-facing alerts ERROR state, by design; the freshness
// marker (R1) is the one honest hedge that the index may have stopped updating.
async function loadAlerts() {
  // Stamped BEFORE the fetch and only once, so the never-filled grace period is
  // measured from the client's first attempt and cannot be reset by later attempts.
  if (alertsFirstAttemptAt == null) alertsFirstAttemptAt = alertsClockNow();
  try {
    // AbortSignal.timeout bounds the alerts fetch (R2). A timeout aborts into the
    // catch below and is swallowed like every other alerts failure: alerts are a
    // decorative overlay, so a wedged fetch must keep the last-known index silently,
    // never surface an error or block the arrivals a rider clicked for.
    const res = await fetch("/api/alerts", { signal: AbortSignal.timeout(FETCH_DEADLINE_MS) });
    if (!res.ok) return; // keep the last-known index + banner silently
    const body = await res.json();
    const list = body.alerts ?? [];
    alertsIndex = indexAlerts(list);
    // Record the BACKEND'S last successful poll, not this fetch's arrival. A 200
    // whose fetched_at has not advanced since the previous poll means the backend is
    // serving an index it could not refresh, and that must age the marker rather
    // than reset it. As of C2 the basis is the WORST per-system fetched_at, so a
    // partial outage ages it too (see alertsFreshnessBasis).
    //
    // NO FALLBACK TO THE CLIENT CLOCK. This used to read `?? alertsClockNow()` for a
    // backend that omitted fetched_at entirely, which C2 turned into a bug: null now
    // ALSO means "a system has never decoded", and mapping that to now would have
    // reported a permanently missing alert system as perfectly fresh. Null instead
    // ages against the client's first-attempt time (alertsStale's sinceAt branch),
    // which is the same honesty rule the never-filled-index case already used.
    //
    // minClockOffset is deliberately NOT calibrated from this response: it is a
    // global shared with the arrivals countdowns and the status line, so feeding it
    // from here would change non-alert surfaces. It stays calibrated by served_at on
    // the feed responses, exactly as R1 arranged; this line only consumes the axis.
    alertsFetchedAt = alertsFreshnessBasis(body);
    alertsSystems = ingestSystems(body, "alerts");
    lastBannerAlerts = bannerAlerts(list);
    // The banner re-renders every poll (unlike popups, which render on open), so a
    // resolved agency-wide alert disappears on the next poll and a new one appears.
    renderAlertBanner(lastBannerAlerts);
  } catch {
    // network error: keep the last-known index + banner, no user-facing error
  }
}

// Re-render the banner from the last-known alerts so the "may be out of date" marker
// appears (or clears) as time crosses ALERTS_STALE_AFTER_S even while the alerts poll
// is FAILING (loadAlerts only re-renders on success). The stale flag is folded into
// the banner's dedup key, so this is a no-op until the flag actually flips. Driven by
// the 15s refreshAll tick in map.js.
function tickAlertBanner() {
  renderAlertBanner(lastBannerAlerts);
}

// The alerts block for a station popup: the shared station-alert matcher, rendered as
// HTML. Returns "" when nothing matches, so no empty container is rendered.
//
// THE MATCHING ITSELF MOVED TO helpers.js (F11), and this is now the popup's RENDERER
// rather than the popup's matcher. The station panel renders the same answer as
// elements, and the two disagreeing about which alerts apply at a station is exactly
// the defect F11 recorded: a suspension in the popup and nothing in the panel, on a
// phone where the open panel makes the map inert and the popup is unreachable. One
// matcher, two renderers, and no way for the two to drift.
//
// alertsIndex is read fresh as a global on every call, so a popup re-render picks up
// whatever the store holds now. The union rule the matcher applies (static
// routes-per-station plus the routes in the current arrivals, H5) is documented at
// stationAlertRouteIds, along with the three body shapes it now reads.
function stationAlertsBlock(system, station, body) {
  // Append the alerts-freshness marker (R1): if the alerts feed itself is stale it
  // shows even when this station currently matches no alerts (the block is ""), so a
  // rider is never shown an empty-looking station while the alert feed is down.
  return alertsBlockHtml(stationAlerts(alertsIndex, system, station, body)) +
    staleAlertsMarker();
}

// The alerts block for a route surface (bus / subway train / railroad train popup):
// match the current index against the popup's system and route. WHY read alertsIndex
// fresh each call: these popups are bound as functions and render at OPEN time (and
// on the marker poll's popup.update()), so they show the store as of open/refresh,
// not a live stream. A newly-arrived alert appears the next time the popup opens or
// updates, the same contract the arrivals popups follow.
function routeAlertsBlock(system, routeId) {
  return alertsBlockHtml(matchRouteAlerts(alertsIndex, system, routeId)) + staleAlertsMarker();
}

// Agency-wide (selector-less) alerts get a dismissible banner over the map instead
// of a popup, since they belong to no single route or station. WHY dismissal is per
// alert and in-memory for the session: dismissing hides the currently-shown alerts,
// a later poll re-showing the SAME ones keeps them hidden, but a NEW one (never
// dismissed) reopens the banner. So a rider can clear a standing incident without
// losing the next, distinct one, and a page reload starts fresh. The key is scoped by
// system like every other alert join, so a bare id reused across two feeds cannot make
// dismissing one hide an unrelated agency-wide alert.
//
// THE DISMISSAL KEY INCLUDES THE HEADER TEXT, for the same reason the render key does
// and with more at stake. The MTA revises an ongoing incident in place under one id,
// so an id-only dismissal meant that once a rider cleared "Delays on the 4 line", the
// SAME id later reading "All subway service suspended" stayed suppressed for the rest
// of the session. Agency-wide alerts have no route or stop selectors, so the banner is
// their only surface: there is no popup that would have shown it instead. Rewording
// therefore un-dismisses, which is the safe direction to err in. A rider who dismisses
// an unchanged alert still keeps it hidden, because an unchanged alert hashes the same.
const dismissedAlertIds = new Set();
const alertKey = (a) => `${a.system}|${a.id}|${hashString(String(a.header ?? ""))}`;

// Signature of the last-rendered banner, so an unchanged banner is NOT rebuilt every
// 60s poll: reassigning innerHTML would drop any text the rider has selected and
// re-parse identical markup for no visual change.
let lastBannerKey = null;

/* MR1 RETIRED --alert-banner-height AND publishBannerHeight WITH IT, and the reason it
   existed is worth keeping written down because it is the reason it can go.

   Under 700px the banner and the legend panel shared the bottom of the screen: the banner
   had moved there so it would stop covering the Stations toggle, the panel grew down from
   the top, and with the legend expanded they met. Measured at 375x667 with one agency-wide
   alert, #panel ran to y=657 while the banner occupied y 595..645. Nothing was hidden from
   VIEW (the banner painted above the panel), but two things broke: axe stopped being able to
   decide the row's contrast at all ("background color could not be determined because it
   partially overlaps other elements"), and the panel's last 62px sat behind the banner with
   no way to bring them out, because the panel scrolls its own overflow and its END is what
   landed there. On a phone during a systemwide incident, that end is the status line.

   So the height was MEASURED and published as a custom property rather than reserved in the
   stylesheet, because it grows with each alert and with how the strip wraps at a given
   width, and the panel's mobile max-height subtracted it.

   The strip is a ROW OF THE HEADER now (v3.1 point 1). Two boxes that cannot overlap need no
   arithmetic between them: nothing subtracts the strip's height because nothing is positioned
   against it, the axe undecidable is gone with the overlap, and the note that used to be the
   unreachable thing is a row above the strip rather than the tail of a scrolling panel. The
   resize republisher goes too; there is no consumer left to keep current. */

// A4: what the banner is about to destroy, captured while it still exists.
//
// The banner is REBUILT IN PLACE rather than removed, so the element the rider is holding
// is a descendant that will not survive, while #alert-banner itself does. Returning the
// container is therefore right for the predicate (it is the subtree that contains the
// doomed control) and returning the focused element itself would be wrong the moment
// Leaflet or a future rebuild reuses a node.
function bannerFocusVictim(el) {
  if (!el || !document.activeElement) return null;
  return el.contains(document.activeElement) ? el : null;
}

function renderAlertBanner(alerts) {
  const el = document.getElementById("alert-banner");
  const shown = alerts.filter((a) => a.header && !dismissedAlertIds.has(alertKey(a)));
  // R1: the banner also carries the alerts-freshness marker, so a stale alerts feed
  // is disclosed even when there are no agency-wide alerts to show. The stale flag is
  // folded into the dedup key, or the marker would never paint/clear on an unchanged
  // alert set; C1 folded in a hash of each alert's HEADER TEXT too, so a revision of
  // an ongoing incident under the same id re-renders instead of leaving stale wording
  // on screen. See bannerRenderKey().
  const stale = alertsStale(alertsFetchedAt, alertsClockNow(), alertsFirstAttemptAt);
  const key = bannerRenderKey(shown, stale);
  if (key === lastBannerKey) return; // unchanged since the last render: leave the DOM alone
  lastBannerKey = key;
  // Speak from the SAME `shown` list the rows below are built from, so the spoken and
  // the drawn banner cannot disagree about what is showing. Placed after the dedup
  // return, which costs nothing: an unchanged key means unchanged alert identities, so
  // the announcement would have been silent anyway. The stale flag IS in the key but
  // NOT in the identities, so the freshness marker appearing re-renders the strip and
  // says nothing, which is the intent: that marker is honesty about the feed, not news
  // about the transit system.
  announceAlertTransition(shown);
  if (!shown.length && !stale) {
    // A4: THE UNMOUNT PATH, which A2's own FOLLOWUP named and left open. The rider may be
    // holding the dismiss button that is about to stop existing, and measured, this branch
    // dropped them on document.body in silence. Read BEFORE the children go, because
    // afterwards there is nothing left to ask.
    const plan = planVanishingFocus(bannerFocusVictim(el), { kind: "alerts" });
    el.replaceChildren(); // nothing to show and alerts are current: no banner strip
    applyVanishingFocus(plan);
    return;
  }
  const rows = shown.map((a) => `<div class="alert-banner-row">${esc(a.header)}</div>`).join("");
  const staleRow = stale
    ? `<div class="alert-banner-row alert-stale">alerts may be out of date</div>`
    : "";
  // The dismiss button only appears when there ARE dismissible alerts; it clears the
  // shown alerts but never the freshness marker (dismissing incidents must not hide
  // the honesty hedge that the feed is down).
  const dismiss = shown.length
    ? `<button type="button" id="alert-banner-dismiss" title="Dismiss">&times;</button>`
    : "";
  // THE SAME FAMILY AS THE POPUP REFRESH, on the page's other rebuilt-in-place surface.
  // Reassigning innerHTML destroys the dismiss button, so a rider parked on it was
  // dropped to document.body the moment an ongoing incident was reworded under its own
  // id, which is precisely the case the header hash in the render key exists to catch.
  // Measured before the fix: BUTTON#alert-banner-dismiss -> BODY.
  //
  // Restores only to a LIVE successor, like updatePopupKeepingFocus. When the rebuild
  // has no dismiss button the banner itself is gone, and where focus belongs then is an
  // open question this phase does not answer; see the A2 FOLLOWUP filed above
  // updatePopupKeepingFocus.
  const hadFocus = !!(document.activeElement && el.contains(document.activeElement));
  // Captured before the rebuild for the same reason as the unmount branch: once innerHTML
  // is reassigned the old subtree is gone and cannot be asked whether it held focus.
  const rebuildPlan = planVanishingFocus(bannerFocusVictim(el), { kind: "alerts" });
  el.innerHTML =
    `<div class="alert-banner-strip">` +
    `<div class="alert-banner-rows">${rows}${staleRow}</div>` +
    dismiss +
    `</div>`;
  const dismissBtn = el.querySelector("#alert-banner-dismiss");
  if (hadFocus && dismissBtn) dismissBtn.focus();
  // A4: AND THE REBUILD THAT HAS NO SUCCESSOR TO RESTORE TO. When the alert set empties
  // on a poll whose feed is also stale, the strip is rebuilt carrying only the "alerts may
  // be out of date" row and no dismiss button, so the branch above finds nothing to focus
  // and silently gives up. Measured, that is the most confusing variant of the defect: the
  // banner is still visibly on screen with nothing focusable inside it and the rider is on
  // the body. The dismiss button also cannot survive its own click, since dismissing
  // empties `shown`, so every dismissal lands on this path or the unmount above.
  if (hadFocus && !dismissBtn) applyVanishingFocus(rebuildPlan);
  if (dismissBtn) {
    dismissBtn.addEventListener("click", () => {
      for (const alert of shown) dismissedAlertIds.add(alertKey(alert));
      renderAlertBanner(alerts); // re-render: dismissed ids drop out (marker, if any, stays)
    });
  }
}


// Glide trains between polls: recompute every marker's interpolated position
// from the current skew-corrected time. Throttled to ~10 fps (trains are slow
// and there can be a few hundred markers), and skipped entirely while the
// subway layer is hidden. rAF keeps rescheduling so it resumes on re-toggle.
const TRAIN_TICK_MS = 100;
let lastTrainTick = 0;

function animateTrains(ts) {
  // Glides subway trains, railroad trains drawn from a prediction (placed or
  // estimated), PATH trains and NJ Transit trains between polls. A reported railroad
  // position is not animated here: it moves by its reported position in
  // applyRailroads. Anchorless PATH trains cost one
  // trainLatLng fallback each and stay put, so no per-record gate is needed.
  // Each layer is gated on its own visibility; rAF keeps rescheduling so
  // animation resumes on re-toggle.
  if (ts - lastTrainTick >= TRAIN_TICK_MS) {
    lastTrainTick = ts;
    // A system goes stale by time passing, not by a response arriving, so the
    // freshness index is rebuilt here as well as on each poll: crossing the
    // threshold mid-interval must dim the markers and freeze the glide without
    // waiting up to 15s for the next poll. The sweep runs only when the stale set
    // actually changes (C2).
    refreshSystemFreshness();
    const now = correctedNow();
    // 6.3: ONE OBSERVATION crossing OBS_FRESH_S between polls wakes the sweeps too
    // (observationCrossed), so its marker dims on this tick rather than at the next
    // poll. That is no change in any SYSTEM's membership, so it re-dims and says
    // nothing: the live region below still speaks only when the stale set of systems
    // moves, exactly as before.
    const systemsMoved = staleSetChanged();
    if (systemsMoved || observationCrossed(now)) applyStaleTreatment();
    // MR1: and the feed strip's dots move with them. A feed crosses into stale by time
    // passing rather than by a response arriving, so a dot repainted only from the poll
    // tail would stay green through a feed that had stopped answering. Gated on the same
    // transition as the marker sweep, so a quiet tick costs one string compare.
    if (systemsMoved) refreshFeedStrip();
    if (systemsMoved) {
      // AND SAY SO. A system goes stale by time passing, not only by a poll landing,
      // so the tick is where a mid-interval crossing is detected; announcing only from
      // the poll tail would leave a rider up to fifteen seconds behind the dimming
      // they cannot see. announceStatusTransition compares against what was last
      // announced, so being called from here AND from the poll tail is harmless: the
      // second call finds an unchanged set and says nothing. A tick that lands while a
      // poll is rendering is held with that poll's writes and spoken in its one write, in
      // order (announcePage says why that matters).
      announceStatusTransition(systemFreshnessIndex);
    }
    // REDUCED MOTION STOPS THE GLIDE, AND NOTHING ELSE. The freshness rebuild, the
    // dimming sweep and the announcement above all still run: those are data honesty,
    // not motion, and suppressing them would be the gate changing WHAT is shown rather
    // than HOW it moves. What is skipped is only the per-frame interpolation, so every
    // marker sits where its last poll said it was and jumps to the new truth when the
    // next one lands. Same positions, same data, no tweening.
    if (!motionOn) {
      requestAnimationFrame(animateTrains);
      return;
    }
    // glideClock pins a marker at its freeze deadline instead of dead-reckoning it
    // forward: its system's, when the feed is not being refreshed, and since 6.3 its own
    // observation's, when the fix or prediction it is glided from is past OBS_FRESH_S
    // (glideDeadline takes the earlier). A marker with neither gets `now` back
    // unchanged, so healthy gliding from fresh data is untouched. Each system's glide
    // clock is one function (subwayGlideAt, railroadGlideAt, pathGlideAt, njtPointFor)
    // that its apply path calls too, so the poll and the tick cannot freeze one marker
    // at two different instants.
    if (map.hasLayer(subwayLayer)) {
      for (const record of trains.values()) {
        record.marker.setLatLng(trainLatLng(record.latest, subwayGlideAt(record.latest, now), record.fState));
      }
    }
    // MR1: PER AGENCY, because the two railroads now toggle separately. The guard used to
    // be one map.hasLayer for the pair; asking each record about its own group is the same
    // test at the resolution the feed strip introduced, and it keeps a hidden LIRR from
    // paying for Metro-North's glide or the other way round.
    for (const record of railroads.values()) {
      if (!map.hasLayer(railroadVehicleLayer(record.latest.system))) continue;
      // Only a train drawn from a prediction glides; a reported one sits where it was. A
      // retained train is drawn as it was before retention (record.drawnFrom), so a
      // retained placement keeps gliding, frozen by its system's retained_since.
      if (drawnFromPrediction(record.latest, record.drawnFrom)) {
        record.marker.setLatLng(trainLatLng(record.latest, railroadGlideAt(record.latest, now), record.fState));
      }
    }
    if (map.hasLayer(njtTrains)) {
      // Every NJT POSITION is a schedule estimate, so there is no GPS half to skip
      // the way the railroad loop skips its own. Only the IN-TRANSIT ones move,
      // though, which is the PATH sentence above rather than the stronger claim an
      // earlier draft of this comment made: a train drawn at its stop carries null
      // anchors and njtGlideTrain returns null for it, so njtPointFor hands back the
      // served position and it stays put. The freeze clock is inside njtPointFor,
      // which is also where the payload's current-position/next-station mismatch is
      // reconciled, so this loop and the poll path cannot disagree about either.
      for (const record of njtTrainRecords.values()) {
        record.marker.setLatLng(njtPointFor(record, now));
      }
    }
    if (map.hasLayer(pathTrains)) {
      // PATH is single-feed, so its system is the synthesized one named after the
      // source: it flows through the SAME freeze rule as the aggregates rather than
      // being exempted by having no systems block (see ingestSystems). Per train since
      // 6.3, because each carries its own trip update's clock (pathGlideAt).
      for (const record of pathTrainRecords.values()) {
        record.marker.setLatLng(trainLatLng(record.latest, pathGlideAt(record.latest, now), record.fState));
      }
    }
  }
  requestAnimationFrame(animateTrains);
}

