// Minimal static file server for the e2e suite: serves the buildless frontend/
// (index.html, map.js, helpers.js, style.css, and the self-hosted Leaflet under
// vendor/leaflet) so Playwright can load the real app. It intentionally does NOT
// run the Python backend or proxy anything: every /api/* request is intercepted in
// the browser by page.route (see mock.js). Leaflet is no longer a CDN URL to
// intercept; it is served from vendor/leaflet by this server, exactly as production
// serves it. Zero dependencies, keeping the app's no-build character.
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..", "..", "frontend");
const PORT = Number(process.env.E2E_PORT || 5173);

// Only the content types the frontend actually serves; anything else is octet.
// .png is here for the vendored Leaflet marker/layer images under vendor/leaflet.
const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".png": "image/png",
};

// Frontend security headers, MIRRORING backend/main.py's security_headers middleware
// (its _SECURITY_HEADERS). The backend is the source of truth; these must stay in
// sync, and a backend test pins the exact CSP string. They are set here so the
// Playwright browser enforces the same CSP against the real app (this static server
// stands in for production's static mount); without them the e2e would never
// exercise the CSP, so "the suite passes" would say nothing about it.
const SECURITY_HEADERS = {
  "Content-Security-Policy":
    "default-src 'self'; " +
    "img-src 'self' data: https://tile.openstreetmap.org; " +
    "connect-src 'self'; " +
    "style-src 'self' 'unsafe-inline'",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
};

const server = http.createServer((req, res) => {
  // Strip the query string and default "/" to index.html.
  let rel = decodeURIComponent((req.url || "/").split("?")[0]);
  if (rel === "/") rel = "/index.html";
  // Resolve inside ROOT and reject any traversal that escapes it.
  const filePath = path.join(ROOT, rel);
  if (!filePath.startsWith(ROOT + path.sep)) {
    res.writeHead(403).end("forbidden");
    return;
  }
  fs.readFile(filePath, (err, body) => {
    if (err) {
      res.writeHead(404).end("not found");
      return;
    }
    res.writeHead(200, {
      "Content-Type": TYPES[path.extname(filePath)] || "application/octet-stream",
      ...SECURITY_HEADERS,
    });
    res.end(body);
  });
});

server.listen(PORT, () => console.log(`e2e static server on http://127.0.0.1:${PORT}`));
