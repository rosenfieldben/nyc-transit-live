class Component extends DCLogic {
  state = { banner: true, keyOpen: false, clock: "", hidden: {}, themeOverride: null, zoom: 13, focus: null, labels: true, view: "City" };
  mapRef = React.createRef();
  layers = {};

  get theme() { return this.state.themeOverride ?? this.props.theme ?? "light"; }

  componentDidMount() {
    const tick = () => { if (window.L && window.NYC_SAMPLE && this.mapRef.current) this.build(); else setTimeout(tick, 60); };
    tick();
    this.clockTimer = setInterval(() => this.setState({ clock: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) }), 1000);
  }
  componentWillUnmount() { clearInterval(this.clockTimer); if (this.ro) this.ro.disconnect(); if (this.map) this.map.remove(); }
  componentDidUpdate() { if (this.map && this.theme !== this.builtTheme) this.rebuild(); }

  // Official MTA trunk colours (the printed map's palette). Text is black on the yellow trunk.
  static TRUNKS = [
    ["Broadway–7 Av", "#EE352E", ["1", "2", "3"]], ["Lexington Av", "#00933C", ["4", "5", "6"]], ["Flushing", "#B933AD", ["7"]],
    ["8 Av", "#0039A6", ["A", "C", "E"]], ["6 Av", "#FF6319", ["B", "D", "F", "M"]], ["Crosstown", "#6CBE45", ["G"]],
    ["Nassau St", "#996633", ["J", "Z"]], ["Canarsie", "#A7A9AC", ["L"]], ["Broadway", "#FCCC0A", ["N", "Q", "R", "W"]], ["Shuttles", "#808183", ["S"]],
  ];
  lineColor(id) { for (const [, c, rs] of Component.TRUNKS) if (rs.includes(id)) return c; return "#808183"; }
  routeColor(id) { if (!id) return "#6d6e71"; let h = 0; for (const c of id) h = (h * 31 + c.charCodeAt(0)) >>> 0; return `hsl(${h % 360}, 45%, 38%)`; }
  // Commuter rail: each agency's own branch colours and the short codes riders already know.
  static RAIL = {
    "LIRR|1": ["BAB", "#00985F"], "LIRR|2": ["MAIN", "#A626AA"], "LIRR|3": ["RON", "#A626AA"], "LIRR|4": ["PJ", "#006EC7"], "LIRR|5": ["OB", "#00AF3F"], "LIRR|6": ["HEM", "#CE8E00"], "LIRR|7": ["LB", "#FF6319"], "LIRR|8": ["FR", "#6E3219"], "LIRR|9": ["ATL", "#4D5357"], "LIRR|10": ["PW", "#C60C30"], "LIRR|11": ["GCM", "#4D5357"],
    "MNR|1": ["HUD", "#009B3A"], "MNR|2": ["HAR", "#0039A6"], "MNR|3": ["NH", "#EE0034"],
    "NJT|7": ["NEC", "#EF3E42"], "NJT|8": ["NJCL", "#00A0DF"], "NJT|9": ["RVL", "#FAA634"], "NJT|10": ["M&E", "#00A94F"], "NJT|11": ["MOBO", "#A2559D"], "NJT|12": ["MBL", "#FFD006"], "NJT|13": ["PVL", "#8E258D"],
  };
  railMeta(system, route) { return Component.RAIL[`${system}|${route}`] ?? [String(route), "#6d6e71"]; }
  railroadColor(system, route) { return this.railMeta(system, route)[1]; }
  railCode(system, route) { return this.railMeta(system, route)[0]; }
  sysGlyph(system) { return system === "LIRR" ? "L" : system === "MNR" ? "M" : "NJ"; }
  sysName(system) { return system === "LIRR" ? "LIRR" : system === "MNR" ? "Metro-North" : "NJ Transit"; }
  // Compass bearing of travel along a branch polyline at a point, or null when the direction is unknown.
  bearingAlong(pts, lat, lon, direction) {
    if (!direction || !this.map) return null;
    const L = window.L, m = this.map, p = m.latLngToLayerPoint([lat, lon]);
    let best = null;
    for (let i = 0; i < pts.length - 1; i++) { const a = m.latLngToLayerPoint(pts[i]), b = m.latLngToLayerPoint(pts[i + 1]); const d = L.LineUtil.pointToSegmentDistance(p, a, b); if (!best || d < best.d) best = { d, a, b }; }
    if (!best) return null;
    let { a, b } = best; if (direction !== "Outbound") [a, b] = [b, a];
    return (Math.atan2(b.x - a.x, -(b.y - a.y)) * 180) / Math.PI;
  }
  esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
  textOn(bg) { const m = String(bg).match(/^#([0-9a-f]{6})$/i); if (!m) return "#ffffff"; const [r, g, b] = [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16) / 255); return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.45 ? "#000000" : "#ffffff"; }
  countdown(sec) { return sec < 30 ? "now" : `${Math.round(sec / 60)} min`; }
  ink() { return this.theme === "dark" ? "#f3f2f2" : "#201e1d"; }
  paper() { return this.theme === "dark" ? "#201e1d" : "#f3f2f2"; }
  gray() { return this.theme === "dark" ? "#9a9a9a" : "#6d6e71"; }

  // ---- popup vocabulary ----
  bullet(r, size = "") { const c = this.lineColor(r); return `<span class="bul ${size}" style="background:${c};color:${this.textOn(c)}">${this.esc(r)}</span>`; }
  sq(label, color, sm) { return `<span class="sq${sm ? " sm" : ""}" style="background:${color};color:${this.textOn(color)}">${this.esc(label)}</span>`; }
  fresh(age) { return age == null ? `<div class="fresh"><i></i>Live · updated ${this.ageText(this.ages.now)} ago</div>` : `<div class="fresh stale"><i></i>As of ${this.ageText(age)} ago · feed stale</div>`; }
  ageText(a) { return a < 120 ? `${Math.round(a)}s` : `${Math.round(a / 60)}m`; }
  alerts(rows) { return rows.map((h) => `<div class="alert">${this.esc(h)}</div>`).join(""); }
  vehiclePopup({ kicker, sub, mark, title, rows, alerts = [], age = null, xlink = null }) {
    return this.alerts(alerts) + `<div class="pk"><span>${this.esc(kicker)}</span><span>${this.esc(sub ?? "")}</span></div><div class="pt">${mark ?? ""}<span>${this.esc(title)}</span></div>` +
      `<div class="kv">${rows.map(([k, v]) => `<div class="k">${this.esc(k)}</div><div class="v">${this.esc(v)}</div>`).join("")}</div>` +
      (xlink ? `<button type="button" class="xlink">Also here: ${this.esc(xlink)} →</button>` : "") + this.fresh(age);
  }
  stationPopup({ kicker, title, buckets, markFor, alerts = [], age = null, extra = "" }) {
    let html = this.alerts(alerts) + `<div class="pk"><span>${this.esc(kicker)}</span>${extra}</div><div class="pt"><span>${this.esc(title)}</span></div>`;
    const keys = Object.keys(buckets);
    if (!keys.length) html += `<div class="none">Nothing upcoming</div>`;
    for (const dir of keys) {
      html += `<div class="dir">${this.esc(dir)}</div>`;
      const rows = buckets[dir];
      if (!rows.length) { html += `<div class="none">No trains</div>`; continue; }
      html += `<div class="arr">` + rows.map(([rt, name, sec]) => `<span>${markFor(rt)}</span><span>${this.esc(name)}</span><span class="n${sec < 30 ? " now" : ""}">${this.countdown(sec)}</span>`).join("") + `</div>`;
    }
    return html + this.fresh(age);
  }

  // ---- icons ----
  svgIcon(inner, size, cls, lift, pad = 0) {
    const L = window.L, [w, h] = Array.isArray(size) ? size : [size, size];
    return L.divIcon({ className: cls + (lift ? " v-lift" : ""), html: `<svg viewBox="${-pad} ${-pad} ${w + 2 * pad} ${h + 2 * pad}" style="width:${w + 2 * pad}px;height:${h + 2 * pad}px;margin:${-pad}px">${inner}</svg>`, iconSize: [w, h], iconAnchor: lift ? [w / 2, h + 3] : [w / 2, h / 2], popupAnchor: lift ? [0, -(h + 3)] : [0, -h / 2] });
  }
  // A train is the route bullet itself, ringed in paper so it lifts off a same-colour ribbon.
  subwayIcon(route) {
    const c = this.lineColor(route), fs = route.length > 1 ? 8.5 : 10.5;
    return this.svgIcon(`<circle cx="9" cy="9" r="9.5" fill="${this.paper()}" opacity="0.95"/><circle cx="9" cy="9" r="8" fill="${c}"/><text x="9" y="9.5" text-anchor="middle" dominant-baseline="central" font-size="${fs}" font-weight="800" font-family="Archivo, system-ui, sans-serif" fill="${this.textOn(c)}">${this.esc(route)}</text>`, 18, "m-subway", true, 2);
  }
  busIcon(bus) {
    const c = this.routeColor(bus.route_id), paper = this.paper();
    if (bus.bearing == null) return this.svgIcon(`<circle cx="6" cy="6" r="3.5" fill="${c}" stroke="${paper}" stroke-width="1"/>`, 12, "m-bus");
    return this.svgIcon(`<g transform="rotate(${bus.bearing} 7 7)"><path d="M7 1 L12 13 L7 10 L2 13 Z" fill="${c}" stroke="${paper}" stroke-width="0.8"/></g>`, 14, "m-bus");
  }
  // Commuter train = a two-part tag lifted above the track (agency glyph in ink, branch code in branch colour)
  // and a chevron on the track itself pointing the way it is heading. Solid = live GPS, outlined = scheduled position.
  railTag(system, route, placed, bearing) {
    const L = window.L, ink = this.ink(), paper = this.paper(), [code, c] = this.railMeta(system, route), gl = this.sysGlyph(system);
    const sysW = gl.length > 1 ? 16 : 11, codeW = code.length * 5.6 + 7, w = Math.ceil(sysW + codeW), cx = w / 2, H = 13;
    const font = `font-size="8" font-weight="800" font-family="Archivo, system-ui, sans-serif" text-anchor="middle" dominant-baseline="central"`;
    let tag, chev;
    if (!placed) {
      tag = `<rect x="-1" y="-1" width="${w + 2}" height="${H + 2}" fill="${paper}" opacity="0.9"/><rect width="${sysW}" height="${H}" fill="${ink}"/><text x="${sysW / 2}" y="${H / 2 + 0.5}" ${font} fill="${paper}">${gl}</text><rect x="${sysW}" width="${codeW}" height="${H}" fill="${c}"/><text x="${sysW + codeW / 2}" y="${H / 2 + 0.5}" ${font} fill="${this.textOn(c)}">${this.esc(code)}</text>`;
      chev = bearing == null ? `<circle cx="${cx}" cy="21" r="3" fill="${ink}" stroke="${paper}" stroke-width="1"/>` : `<g transform="rotate(${bearing} ${cx} 21)"><path d="M${cx} 15.5 L${cx + 5} 24.5 L${cx} 22 L${cx - 5} 24.5 Z" fill="${ink}" stroke="${paper}" stroke-width="1"/></g>`;
    } else {
      tag = `<rect x="0.5" y="0.5" width="${w - 1}" height="${H - 1}" fill="${paper}" stroke="${ink}" stroke-width="1.2"/><path d="M${sysW} 0 V${H}" stroke="${ink}" stroke-width="1.2"/><text x="${sysW / 2}" y="${H / 2 + 0.5}" ${font} fill="${ink}">${gl}</text><text x="${sysW + codeW / 2}" y="${H / 2 - 0.5}" ${font} fill="${ink}">${this.esc(code)}</text><rect x="${sysW + 1}" y="${H - 3}" width="${codeW - 2}" height="2.5" fill="${c}"/>`;
      chev = bearing == null ? `<circle cx="${cx}" cy="21" r="3" fill="${paper}" stroke="${ink}" stroke-width="1.4"/>` : `<g transform="rotate(${bearing} ${cx} 21)"><path d="M${cx} 15.5 L${cx + 5} 24.5 L${cx} 22 L${cx - 5} 24.5 Z" fill="${paper}" stroke="${ink}" stroke-width="1.4"/></g>`;
    }
    const stem = `<path d="M${cx} ${H} V16.5" stroke="${ink}" stroke-width="1"/>`;
    return L.divIcon({ className: "m-rail v-lift", html: `<svg viewBox="-2 -2 ${w + 4} 30" style="width:${w + 4}px;height:30px;margin:-2px">${tag}${stem}${chev}</svg>`, iconSize: [w, 26], iconAnchor: [cx, 21], popupAnchor: [0, -21] });
  }
  railTagHtml(system, route) { const [code, c] = this.railMeta(system, route); return `<span class="rtag"><b>${this.sysGlyph(system)}</b><i style="background:${c};color:${this.textOn(c)}">${this.esc(code)}</i></span>`; }
  pathIcon(color) { return this.svgIcon(`<path d="M8 1 L15 8 L8 15 L1 8 Z" fill="${color}" stroke="${this.paper()}" stroke-width="1.2"/>`, 16, "m-path", true); }
  ferryIcon(color) { return this.svgIcon(`<path d="M1 3 H21 L17.5 11 H4.5 Z" fill="${color}" stroke="${this.paper()}" stroke-width="1"/>`, [22, 14], "m-ferry"); }
  // Stations as on the printed map: a black dot for a local stop, a ringed white dot where lines meet.
  stationMarker(lat, lon, renderer, hub, big) {
    const L = window.L;
    if (hub) return L.circleMarker([lat, lon], { radius: big ? 5 : 4.5, color: this.ink(), weight: 2, fillColor: this.paper(), fillOpacity: 1, renderer });
    return L.circleMarker([lat, lon], { radius: big ? 4 : 3.5, color: this.ink(), weight: 0, fillColor: this.ink(), fillOpacity: 1, renderer });
  }
  // Commuter rail stations are squares, so a square always means "regional rail" and a circle always means subway.
  railStationMarker(lat, lon) {
    const L = window.L, ink = this.ink(), paper = this.paper();
    return L.marker([lat, lon], { icon: L.divIcon({ className: "m-rstn", html: `<svg viewBox="0 0 10 10" style="width:10px;height:10px"><rect x="1" y="1" width="8" height="8" fill="${paper}" stroke="${ink}" stroke-width="1.6"/></svg>`, iconSize: [10, 10], iconAnchor: [5, 5], popupAnchor: [0, -5] }), pane: "stationPane", keyboard: false });
  }
  label(marker, name, hub) { marker.bindTooltip(name, { permanent: true, direction: "right", offset: [7, 0], className: "stn-label" + (hub ? " hub" : ""), opacity: 1, interactive: false }); }

  // ---- map ----
  rebuild() {
    const view = { center: this.map.getCenter(), zoom: this.map.getZoom() };
    this.map.off(); this.map.remove(); this.map = null; this.tiles = null; this.layers = {}; this.systemLayers = null;
    this.build(view);
  }
  build(view) {
    const L = window.L;
    const map = this.map = L.map(this.mapRef.current, { zoomControl: false }).setView(view ? view.center : [40.7295, -73.99], view ? view.zoom : 13);
    L.control.zoom({ position: "bottomright" }).addTo(map);
    requestAnimationFrame(() => map.invalidateSize());
    if (!this.ro && window.ResizeObserver) { this.ro = new ResizeObserver(() => this.map && this.map.invalidateSize()); this.ro.observe(this.mapRef.current); }
    map.on("popupopen", (e) => {
      const root = this.mapRef.current.parentElement, top = root.getBoundingClientRect().top;
      let bottom = 0;
      for (const el of root.querySelectorAll("header, section[aria-label='Service alerts']")) bottom = Math.max(bottom, el.getBoundingClientRect().bottom - top);
      e.popup.options.autoPanPaddingTopLeft = L.point(24, Math.ceil(bottom) + 12);
      e.popup._adjustPan();
    });
    map.createPane("stationPane"); map.getPane("stationPane").style.zIndex = 450;
    map.createPane("placeLabels"); map.getPane("placeLabels").style.zIndex = 250; map.getPane("placeLabels").style.pointerEvents = "none";
    this.setTiles();
    map.on("zoomend", () => { this.gateStations(); this.setState({ zoom: map.getZoom() }); });
    this.draw();
    this.setState({ zoom: map.getZoom() });
  }
  setTiles() {
    // The keyless OSM tiles the app already serves; the cream-land / soft-water look is a CSS filter on the tile pane (see helmet).
    this.tiles = window.L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }).addTo(this.map);
    this.builtTheme = this.theme;
  }
  stationMinZoom(l) { return this.layers.regional?.includes(l) ? 11 : 12; }
  gateStations() {
    if (!this.layers.stations) return;
    const z = this.map.getZoom();
    for (const l of this.layers.stations) { const on = z >= this.stationMinZoom(l); if (on && !this.map.hasLayer(l) && !this.hiddenFor(l)) l.addTo(this.map); if (!on && this.map.hasLayer(l)) this.map.removeLayer(l); }
  }
  hiddenFor(layer) { return Object.entries(this.systemLayers ?? {}).some(([k, ls]) => this.state.hidden[k] && ls.includes(layer)); }
  setView(preset) {
    if (!this.map) return;
    const v = preset === "Region" ? { c: [40.79, -73.90], z: 10 } : preset === "Rail" ? { c: [40.76, -73.96], z: 11 } : { c: [40.7395, -73.99], z: 13 };
    this.map.flyTo(v.c, v.z, { duration: 0.8 });
    this.setState({ view: preset });
  }

  draw() {
    const L = window.L, D = window.NYC_SAMPLE, map = this.map, esc = this.esc.bind(this);
    const lineRenderer = L.canvas({ padding: 0.3 }), stationRenderer = L.canvas({ padding: 0.3, pane: "stationPane" });
    const g = () => L.layerGroup();
    const Ly = this.layers = { subway: g(), subwayLines: g(), subwayStations: g(), bus: g(), lirr: g(), lirrLines: g(), lirrStations: g(), mnr: g(), mnrLines: g(), mnrStations: g(), path: g(), pathLines: g(), pathStations: g(), ferry: g(), ferryLines: g(), ferryDocks: g(), airLines: g(), airStations: g(), njt: g(), njtLines: g(), njtStations: g() };
    this.layers.stations = [Ly.subwayStations, Ly.lirrStations, Ly.mnrStations, Ly.pathStations, Ly.ferryDocks, Ly.airStations, Ly.njtStations];
    this.layers.regional = [Ly.lirrStations, Ly.mnrStations, Ly.njtStations, Ly.airStations];
    this.systemLayers = { subway: [Ly.subway, Ly.subwayLines, Ly.subwayStations], bus: [Ly.bus], lirr: [Ly.lirr, Ly.lirrLines, Ly.lirrStations], mnr: [Ly.mnr, Ly.mnrLines, Ly.mnrStations], path: [Ly.path, Ly.pathLines, Ly.pathStations], ferry: [Ly.ferry, Ly.ferryLines, Ly.ferryDocks], airtrain: [Ly.airLines, Ly.airStations], njt: [Ly.njt, Ly.njtLines, Ly.njtStations] };
    this.ages = { now: 12, MNR: 372 };
    const alertsFor = (sys, route) => D.alerts.filter((a) => a.system === sys && (!route || a.routes.includes(route))).map((a) => a.header);
    const popupOpts = { maxWidth: 320, autoPanPaddingTopLeft: L.point(24, 200), autoPanPaddingBottomRight: L.point(110, 40) };
    const vehicle = (latlng, icon, popup, group, opacity = 1) => L.marker(latlng, { icon, keyboard: false, opacity }).bindPopup(popup, popupOpts).addTo(group);
    this.subwayRoutes = {}; // route -> { lines: [], trains: [] } for the focus interaction

    // Subway ribbons: the printed map's colour, weight and round caps. Drawn dark-first so the yellow trunk never hides under the others.
    const order = [...D.subwayRoutes].sort((a, b) => (a.route === "N" || a.route === "R") - (b.route === "N" || b.route === "R"));
    for (const r of order) {
      const rec = this.subwayRoutes[r.route] ??= { lines: [], trains: [], color: this.lineColor(r.route) };
      for (const pts of r.polylines) {
        const casing = L.polyline(pts, { color: this.paper(), weight: 6.5, opacity: 0.9, lineCap: "round", lineJoin: "round", interactive: false, renderer: lineRenderer }).addTo(Ly.subwayLines);
        const line = L.polyline(pts, { color: rec.color, weight: 4, opacity: 1, lineCap: "round", lineJoin: "round", interactive: false, renderer: lineRenderer }).addTo(Ly.subwayLines);
        rec.lines.push(casing, line);
      }
    }
    for (const s of D.subwayStops) {
      const hub = s.routes.length > 1;
      const m = this.stationMarker(s.lat, s.lon, stationRenderer, hub).addTo(Ly.subwayStations);
      this.label(m, s.name, hub);
      m.bindPopup(() => {
        const a = D.arrivals[s.name];
        return this.stationPopup({ kicker: "Subway station", title: s.name, markFor: (rt) => this.bullet(rt, "sm"), alerts: D.alerts.filter((x) => x.system === "subway" && x.routes.some((r) => s.routes.includes(r))).map((x) => x.header),
          buckets: { Northbound: (a?.Northbound ?? []).map(([rt, sec]) => [rt, `${rt} train`, sec]), Southbound: (a?.Southbound ?? []).map(([rt, sec]) => [rt, `${rt} train`, sec]) },
          extra: `<span class="buls">${s.routes.map((r) => this.bullet(r, "sm")).join("")}</span>` });
      }, popupOpts);
    }
    for (const t of D.subways) {
      const rec = this.subwayRoutes[t.route_id];
      const m = vehicle([t.latitude, t.longitude], this.subwayIcon(t.route_id), () => this.vehiclePopup({ kicker: "Subway", sub: t.direction, mark: this.bullet(t.route_id, "lg"), title: `${t.route_id} train`, rows: [["Next stop", t.stop_name], ["Position", "Placed from arrivals"], ["Trip", t.trip_id]], alerts: alertsFor("subway", t.route_id) }), Ly.subway);
      rec?.trains.push(m);
    }
    // Buses
    for (const b of D.buses) {
      const c = this.routeColor(b.route_id);
      vehicle([b.latitude, b.longitude], this.busIcon(b), () => this.vehiclePopup({ kicker: "Bus", sub: b.bearing != null ? `Heading ${Math.round(b.bearing)}°` : "Heading unknown", mark: this.sq(b.route_id, c), title: b.route_id, rows: [["Vehicle", b.id.replace("MTA NYCT_", "")], ["Position", "Live GPS"]], alerts: alertsFor("bus", b.route_id) }), Ly.bus);
    }
    // Commuter rail: each branch in its own colour, one step thinner than the subway, with a paper casing so
    // branches sharing track still separate. Squares for stations, tags + chevrons for trains.
    const rrLine = (pts, color, group) => {
      L.polyline(pts, { color: this.paper(), weight: 5, opacity: 0.9, lineCap: "round", lineJoin: "round", interactive: false, renderer: lineRenderer }).addTo(group);
      return L.polyline(pts, { color, weight: 2.5, opacity: 1, lineCap: "round", lineJoin: "round", interactive: false, renderer: lineRenderer }).addTo(group);
    };
    const rrName = new Map(D.railroadRoutes.map((r) => [`${r.system}|${r.route}`, r.name]));
    const rrPts = new Map(D.railroadRoutes.map((r) => [`${r.system}|${r.route}`, r.polylines[0]]));
    const rrGroup = (sys) => (sys === "LIRR" ? { trains: Ly.lirr, lines: Ly.lirrLines, stations: Ly.lirrStations } : { trains: Ly.mnr, lines: Ly.mnrLines, stations: Ly.mnrStations });
    for (const r of D.railroadRoutes) for (const pts of r.polylines) rrLine(pts, this.railroadColor(r.system, r.route), rrGroup(r.system).lines);
    for (const s of D.railroadStops) {
      const m = this.railStationMarker(s.lat, s.lon).addTo(rrGroup(s.system).stations);
      this.label(m, s.name, false);
      m.bindPopup(() => {
        const a = D.arrivals[s.name];
        const buckets = {}; for (const [dir, rows] of Object.entries(a ?? {})) buckets[dir] = rows.map(([rt, name, num, sec]) => [rt, `${name} · #${num}`, sec]);
        return this.stationPopup({ kicker: `${this.sysName(s.system)} station`, title: s.name, markFor: (rt) => this.railTagHtml(s.system, rt), buckets, alerts: alertsFor(s.system), age: this.ages[s.system] ?? null });
      }, popupOpts);
    }
    for (const t of D.railroads) {
      const placed = t.stop_id != null, age = this.ages[t.system] ?? null;
      const bearing = this.bearingAlong(rrPts.get(`${t.system}|${t.route_id}`) ?? [], t.latitude, t.longitude, t.direction);
      vehicle([t.latitude, t.longitude], this.railTag(t.system, t.route_id, placed, bearing), () => this.vehiclePopup({ kicker: this.sysName(t.system), sub: t.direction ?? "", mark: this.railTagHtml(t.system, t.route_id), title: rrName.get(`${t.system}|${t.route_id}`), rows: [["Train", `#${t.train_num}`], ...(placed ? [["Next stop", t.stop_name]] : []), ["Position", placed ? "Scheduled, no GPS" : "Live GPS"]], alerts: alertsFor(t.system, t.route_id), age, xlink: placed ? t.stop_name : null }), rrGroup(t.system).trains, age ? 0.45 : 1);
    }
    // PATH: its own route colours, one step thinner than the subway.
    const pColor = new Map(D.pathRoutes.map((r) => [r.id, "#" + r.color])), pName = new Map(D.pathRoutes.map((r) => [r.id, r.name]));
    for (const r of D.pathRoutes) for (const pts of r.shape) L.polyline(pts, { color: pColor.get(r.id), weight: 3.5, opacity: 1, lineCap: "round", lineJoin: "round", interactive: false, renderer: lineRenderer }).addTo(Ly.pathLines);
    for (const s of D.pathStops) {
      const m = this.stationMarker(s.lat, s.lon, stationRenderer, false).addTo(Ly.pathStations);
      this.label(m, s.name, false);
      m.bindPopup(() => this.stationPopup({ kicker: "PATH station", title: s.name, markFor: (rt) => this.sq(pName.get(rt).split(" ")[0].slice(0, 3).toUpperCase(), pColor.get(rt), true), buckets: { "To New York": [["862", "Newark - World Trade Center", 180]], "To New Jersey": [["862", "Newark - World Trade Center", 420], ["861", "Journal Square - 33rd Street", 660]] } }), popupOpts);
    }
    for (const t of D.path) {
      const c = pColor.get(t.route_id);
      vehicle([t.latitude, t.longitude], this.pathIcon(c), () => this.vehiclePopup({ kicker: "PATH", sub: t.direction, mark: this.sq("PATH", c), title: pName.get(t.route_id), rows: [["Next stop", t.stop_name], ["Position", "Scheduled, no GPS"]] }), Ly.path);
    }
    // Ferry: dashed routes, as the map draws water crossings.
    const fColor = new Map(D.ferryRoutes.map((r) => [r.id, "#" + r.color])), fName = new Map(D.ferryRoutes.map((r) => [r.id, r.name]));
    for (const r of D.ferryRoutes) for (const pts of r.shape) L.polyline(pts, { color: fColor.get(r.id), weight: 2, opacity: 0.9, dashArray: "6 5", interactive: false, renderer: lineRenderer }).addTo(Ly.ferryLines);
    for (const s of D.ferryStops) {
      const m = L.circleMarker([s.lat, s.lon], { radius: 4, color: this.paper(), weight: 1.5, fillColor: "#00839c", fillOpacity: 1, renderer: stationRenderer }).addTo(Ly.ferryDocks);
      this.label(m, s.name, false);
      m.bindPopup(() => this.stationPopup({ kicker: "NYC Ferry dock", title: s.name, markFor: (rt) => this.sq(rt, fColor.get(rt), true), buckets: { "East River": [["ER", "arrives", 360], ["ER", "departs", 1260]] }, extra: s.wheelchair ? `<span title="Wheelchair accessible">&#9855; Accessible</span>` : "" }), popupOpts);
    }
    for (const b of D.ferry) {
      const c = fColor.get(b.route_id), docked = b.status === "STOPPED_AT";
      vehicle([b.latitude, b.longitude], this.ferryIcon(c), () => this.vehiclePopup({ kicker: "NYC Ferry", sub: docked ? "At dock" : "Under way", mark: this.sq(b.route_id, c), title: fName.get(b.route_id), rows: [["Boat", b.label], ["Position", "Live GPS"], ...(docked ? [] : [["Speed", `${(b.speed * 1.944).toFixed(1)} kn`]])] }), Ly.ferry, docked ? 0.55 : 1);
    }
    // AirTrain JFK: gray like the other non-subway rail, dashed because it is schedule-only.
    for (const r of D.airtrain.routes) L.polyline(r.polyline, { color: this.gray(), weight: 3, opacity: 1, dashArray: "8 5", interactive: false, renderer: lineRenderer }).addTo(Ly.airLines);
    for (const s of D.airtrain.stations) {
      const m = this.railStationMarker(s.lat, s.lon).addTo(Ly.airStations);
      this.label(m, s.name, false);
      m.bindPopup(() => `<div class="pk"><span>AirTrain JFK station</span><span>Scheduled</span></div><div class="pt"><span>${esc(s.name)}</span></div><div class="kv"><div class="k">Jamaica line</div><div class="v">every 8 min</div><div class="k">Howard Beach line</div><div class="v">every 12 min</div></div><div class="fresh"><i style="background:${this.gray()}"></i>Scheduled headways · no live feed</div>`, popupOpts);
    }
    // NJ Transit: same grammar, always scheduled (outlined tags).
    const nName = new Map(D.njtRoutes.map((r) => [r.id, r.name])), nPts = new Map(D.njtRoutes.map((r) => [r.id, r.shape[0]]));
    for (const r of D.njtRoutes) for (const pts of r.shape) rrLine(pts, this.railroadColor("NJT", r.id), Ly.njtLines);
    for (const s of D.njtStops) {
      const m = this.railStationMarker(s.lat, s.lon).addTo(Ly.njtStations);
      this.label(m, s.name, false);
      m.bindPopup(() => {
        const here = D.njt.filter((t) => t.stop_name === s.name);
        const rows = here.length ? here.map((t) => [t.route_id, `${t.headsign} · #${t.train_num}`, 300 + Math.abs(t.delay)]) : [["7", "New York · #3841", 1500]];
        return this.stationPopup({ kicker: "NJ Transit station", title: s.name, markFor: (id) => this.railTagHtml("NJT", id), buckets: { Departures: rows } });
      }, popupOpts);
    }
    for (const t of D.njt) {
      const name = nName.get(t.route_id), delay = Math.round(t.delay / 60);
      const bearing = this.bearingAlong(nPts.get(t.route_id) ?? [], t.latitude, t.longitude, t.headsign === "New York" ? "Inbound" : "Outbound");
      vehicle([t.latitude, t.longitude], this.railTag("NJT", t.route_id, true, bearing), () => this.vehiclePopup({ kicker: "NJ Transit", sub: `To ${t.headsign}`, mark: this.railTagHtml("NJT", t.route_id), title: name, rows: [["Train", `#${t.train_num}`], ["Next stop", t.stop_name], ["Delay", delay ? `${delay} min late` : "On time"], ["Position", "Scheduled, no GPS"]] }), Ly.njt);
    }

    for (const [k, ls] of Object.entries(this.systemLayers)) if (!this.state.hidden[k]) for (const l of ls) l.addTo(map);
    this.gateStations();
    this.applyFocus();
    this.forceUpdate();
  }

  // Focus one subway route from the header bullets: everything else on the subway layer fades back.
  applyFocus() {
    const f = this.state.focus;
    for (const [route, rec] of Object.entries(this.subwayRoutes ?? {})) {
      const on = !f || f === route;
      rec.lines.forEach((p, i) => p.setStyle({ opacity: on ? (i % 2 === 0 ? 0.9 : 1) : (i % 2 === 0 ? 0 : 0.18) }));
      rec.trains.forEach((m) => m.setOpacity(on ? 1 : 0.15));
    }
  }
  setFocus(route) { this.setState({ focus: this.state.focus === route ? null : route }, () => this.applyFocus()); }

  toggleSystem(k) {
    return () => {
      const hidden = { ...this.state.hidden, [k]: !this.state.hidden[k] };
      this.setState({ hidden }, () => { for (const l of this.systemLayers[k]) { if (hidden[k]) this.map.removeLayer(l); else if (!this.layers.stations.includes(l) || this.map.getZoom() >= this.stationMinZoom(l)) l.addTo(this.map); } });
    };
  }

  renderVals() {
    const D = window.NYC_SAMPLE, ages = this.ages ?? { now: 12 };
    const live = `Live · ${this.ageText(ages.now)}`;
    const accent = this.theme === "dark" ? "#ff563c" : "#ec3013";
    const present = new Set((D?.subwayRoutes ?? []).map((r) => r.route));
    const trunks = Component.TRUNKS.map(([name, color, routes]) => ({ name, routes: routes.filter((r) => present.has(r)).map((r) => ({ id: r, color, ink: this.textOn(color), focus: () => this.setFocus(r), title: `Show only the ${r}`, opacity: !this.state.focus || this.state.focus === r ? 1 : 0.3, ring: this.state.focus === r ? `2px solid ${accent}` : "none" })) })).filter((t) => t.routes.length);
    const sys = [
      ["subway", "Subway", D ? D.subways.length : 0, "#0039A6", live],
      ["bus", "Buses", D ? D.buses.length : 0, "#605d5d", live],
      ["lirr", "LIRR", D ? D.railroads.filter((t) => t.system === "LIRR").length : 0, "L", live],
      ["mnr", "Metro-North", D ? D.railroads.filter((t) => t.system === "MNR").length : 0, "M", `As of ${this.ageText(ages.MNR ?? 372)} ago`],
      ["njt", "NJ Transit", D ? D.njt.length : 0, "NJ", "Scheduled"],
      ["path", "PATH", D ? D.path.length : 0, "#d93a30", live],
      ["ferry", "Ferry", D ? D.ferry.length : 0, "#00839c", live],
      ["airtrain", "AirTrain", D ? D.airtrain.stations.length : 0, "#6d6e71", "Scheduled"],
    ].map(([key, name, count, dotColor, fresh]) => ({
      key, name, count, dotColor: dotColor.startsWith("#") ? dotColor : "", glyph: dotColor.startsWith("#") ? "" : dotColor, fresh, tip: `${fresh} · ${this.state.hidden[key] ? "show" : "hide"} ${name}`,
      freshColor: fresh.startsWith("Live") ? "#00933c" : fresh === "Scheduled" ? "var(--divider)" : accent,
      opacity: this.state.hidden[key] ? 0.3 : 1, toggle: this.toggleSystem(key),
    }));
    return {
      mapRef: this.mapRef, theme: this.theme, themeLabel: this.theme === "dark" ? "Light" : "Dark", zoom: String(this.state.zoom),
      toggleTheme: () => this.setState({ themeOverride: this.theme === "dark" ? "light" : "dark" }),
      keyOpen: this.state.keyOpen, toggleKey: () => this.setState({ keyOpen: !this.state.keyOpen }),
      clock: this.state.clock, systems: sys, trunks, clearFocus: () => this.setState({ focus: null }, () => this.applyFocus()), clearVis: this.state.focus ? "visible" : "hidden",
      labelsMode: (this.props.stationNames ?? this.state.labels) ? "on" : "off", toggleLabels: () => this.setState({ labels: !this.state.labels }),
      labelsBg: this.state.labels ? "var(--ink)" : "transparent", labelsFg: this.state.labels ? "var(--surface)" : "var(--ink)",
      views: ["City", "Rail", "Region"].map((v) => ({ label: v, go: () => this.setView(v), bg: this.state.view === v ? "var(--ink)" : "transparent", fg: this.state.view === v ? "var(--surface)" : "var(--ink)" })),
      staleNote: `Metro-North as of ${this.ageText(ages.MNR ?? 372)} ago`,
      showBanner: this.state.banner, dismissBanner: () => this.setState({ banner: false }),
    };
  }
}