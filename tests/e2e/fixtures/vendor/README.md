# Vendored Leaflet (test fixture)

`leaflet.js` and `leaflet.css` here are the **unmodified** `leaflet@1.9.4` dist
files, checked in so the e2e suite can run without network. `mock.js` fulfills
the two `https://unpkg.com/leaflet@1.9.4/dist/...` requests from these exact
bytes; because they are byte-identical to what unpkg serves, they satisfy the
Subresource Integrity (`integrity`) attributes in `frontend/index.html`.

**Do not edit these two files** (not even to add a comment): any change alters
the SHA-256 and the browser will refuse to load the resource under SRI.

- Version: `leaflet@1.9.4`
- Source: `https://unpkg.com/leaflet@1.9.4/dist/leaflet.js` and `.../leaflet.css`
- SRI (matches `index.html`):
  - `leaflet.js`  -> `sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=`
  - `leaflet.css` -> `sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=`

To refresh (only when `index.html` bumps the Leaflet version + integrity):
re-download both files from unpkg at the new version and confirm
`openssl dgst -sha256 -binary <file> | openssl base64` matches the new
`integrity` values before committing.
