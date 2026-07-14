# Vendored Leaflet 1.9.4

The map library, self-hosted so the app has no runtime CDN dependency (H2). These
are the **unmodified** `leaflet@1.9.4` dist files, served by the app the same way
in production (the FastAPI static mount) and in the e2e suite (`tests/e2e/serve.js`).

- `leaflet.js`, `leaflet.css`: the minified dist, byte-identical to
  `https://unpkg.com/leaflet@1.9.4/dist/...`.
- `images/`: the CSS/marker image assets Leaflet's stylesheet and default icon
  reference (`layers`, `layers-2x`, `marker-icon`, `marker-icon-2x`,
  `marker-shadow`).
- `LICENSE`: Leaflet's own BSD-2-Clause license. Leaflet stays BSD-2; this
  project's Apache-2.0 covers only its own source. See the repository README's
  License section.

**Do not edit these files.** They are third-party assets kept verbatim; changes
belong upstream, not here.

## Refreshing (only when bumping the Leaflet version)

Re-download the complete dist for the new version and replace the files here:

```
V=1.9.4   # set to the new version
base="https://unpkg.com/leaflet@${V}/dist"
curl -sS "$base/leaflet.js"  -o leaflet.js
curl -sS "$base/leaflet.css" -o leaflet.css
for img in layers.png layers-2x.png marker-icon.png marker-icon-2x.png marker-shadow.png; do
  curl -sS "$base/images/$img" -o "images/$img"
done
curl -sS "https://unpkg.com/leaflet@${V}/LICENSE" -o LICENSE
```

There is no Subresource Integrity to update: SRI guarded the third-party CDN, and
these files are now first-party (their integrity is the repository's own). If a
future audit wants a pinned checksum, record it here rather than reintroducing a
CDN.
