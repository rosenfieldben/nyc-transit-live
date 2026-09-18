# Archivo, self-hosted

The map redesign's one typeface (`docs/design/map-redesign/README.md`, "Design
tokens": Archivo at 400, 600 and 800, fallback `system-ui, sans-serif`).

## Why these files are in the repository at all

Because the app's Content-Security-Policy is `default-src 'self'` and that is
deliberate. `font-src` has no directive of its own, so it falls back to
`default-src`, which means a `<link>` to `fonts.googleapis.com` and the
`fonts.gstatic.com` files behind it are both refused by the browser. Two ways out
existed: widen the CSP with `font-src`/`style-src` allowances for two Google
origins, or serve the font from this origin like every other asset. The second is
what `backend/main.py`'s CSP comment already argues for Leaflet ("no CDN since H2
self-hosted Leaflet"), and it is the one taken here.

**So the CSP is untouched by the redesign**, byte for byte, and
`backend/tests/test_api.py::test_security_headers_on_frontend_document` still pins
the same string. That is the whole reason this directory exists.

The other consequence, stated because it is easy to lose: the page makes **no
third-party request for type**. A rider behind a network that blocks Google, or
reading with the font cache cold on a dead link, gets Archivo, not the fallback.

## What the files are

| File | Subset | Bytes |
| --- | --- | --- |
| `archivo-latin.woff2` | `latin` | 34,928 |
| `archivo-latin-ext.woff2` | `latin-ext` | 32,608 |

Archivo **version 2.001**, from the Google Fonts `v25` release of the family
(`fonts.gstatic.com/s/archivo/v25/`), which is the same build the design prototype
loaded. Both files are **variable fonts** carrying a single `wght` axis from 100 to
900 (default 600; the `name` table therefore reads "Archivo SemiBold", which names
the default instance rather than the file's range).

**One file serves all three weights, and that is why there are two files rather than
six.** Archivo is variable-only on Google Fonts: asking for 400, 600 and 800 returns
the same `.woff2` three times. `style.css` declares it once per subset with
`font-weight: 100 900`, so the browser instantiates 400, 600 and 800 (and anything
between) from the one file. Declaring three static `@font-face` rules over one
variable file would download it three times for no gain; shipping three instanced
statics would cost more bytes than the range does.

Two subsets, with the `unicode-range` declarations Google publishes, so the browser
fetches `latin-ext` only when a glyph needs it. `latin` carries the punctuation the
app actually renders in chrome and in feed text: the middle dot the status line
joins with, the en dash in station names like "Astoria-Ditmars Blvd", the em dash
and the ellipsis. `latin-ext` carries Latin Extended-A for the accented station and
alert strings the feeds occasionally publish. The `vietnamese` subset Google also
serves is not shipped: no feed this app reads publishes Vietnamese, and it would be
33 KB in the tree that no rider ever fetches.

## License

SIL Open Font License 1.1, in `OFL.txt` (from `google/fonts`, `ofl/archivo/OFL.txt`).
Copyright 2020 The Archivo Project Authors, https://github.com/Omnibus-Type/Archivo.

The OFL requires the license to travel with the font, which is what that file is
for; it sits beside the `.woff2` files for the same reason `vendor/leaflet/LICENSE`
sits beside Leaflet. Archivo's OFL carries **no Reserved Font Name**, so the family
name is used as published, unmodified.

## The test server needs the type

`tests/e2e/serve.js` sends `X-Content-Type-Options: nosniff`, mirroring the backend.
A font served as `application/octet-stream` under `nosniff` is refused by the
browser, so that server's `TYPES` map carries `.woff2` explicitly. The real backend
needs nothing: `StaticFiles` asks `mimetypes`, and Python already answers
`font/woff2`.
