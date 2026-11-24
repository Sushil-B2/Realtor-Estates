# 25 — Listing photographs

The pipeline in `docs/15` has been real since v1.9: an upload to
`properties/<id>/…` fires the `property-photos` Neon Function, which renders
`thumb` (320w), `card` (960w) and `hero` (1920w) WebP variants, writes one row
per variant into `re_property_media`, and `GET /api/v1/properties/{id}/media`
serves them through 15-minute presigned URLs. What was missing — gap **G5** — was
everything between "rows exist" and "a person sees a photo":

* nothing in the desk showed an image: not the pipeline, not the inventory panel,
  not the property picker, and not the partner catalogue that G4 had just opened;
* three variants of one upload counted as three photos;
* no rule said how many photos a listing needs before anybody may advertise it;
* and there was no answer at all for the other 43 properties — which is what an
  empty `<img>` renders as: a broken image glyph.

This document is the missing layer. It is deliberately thin, because the storage,
the signing and the variant rendering already exist and are proved elsewhere.

## 1. A photo is a stem, not a row

The function writes `derived/properties/<id>/<stem>-<variant>.webp`. The stem is
the photo's identity across its variants, so `app/media.py::photo_stem()` strips
the variant suffix and the extension, and `group()` collapses rows into photos.
Three rows of one upload are one photo; nine rows are three photos.

The grouping is deliberately forgiving: an unknown variant name
(`x-poster.png`) falls back to the bare filename, so a future pipeline change
degrades to one photo per row instead of raising on a page that used to work.

## 2. The cover is chosen, not guessed

`card` (960w) is the cover, falling back to `thumb`, then `hero`, then anything.
A table cell of forty rows that loads the `hero` variant ships 32 KB where 2.8 KB
would do — on a desk in a site office on 4G, that is the difference between a
gallery and a stall. `hero` is still exposed on each photo as `hero_key` for the
one place that needs it (a full-width brochure cover), because the choice of
variant belongs to the surface, not to the storage layer.

Gallery order is **newest stem first**: the stem carries the upload timestamp in
this pipeline, and the last thing an agent uploads is usually the photograph they
want shown.

## 3. Five slots, three to publish

| rule | value | why |
|---|---|---|
| `SLOTS` | 5 | how many photographs a listing page has room for |
| `PUBLISH_FLOOR` | 3 | below this, the listing is not advertisable |

Three is not a style preference: the portals and every MRM brief ask for at
least three, and a partner advertising a project from a single stock render is
how a lead becomes a wasted site visit. `readiness()` returns `have`, `need`,
`missing`, `ready` and a sentence — `"2 photos — 1 more before this can be
advertised"` — so the answer is actionable rather than a boolean.

**Law and polish are separate questions**, and the payload keeps them apart:

* `marketing.ready` — may this be advertised *at all*? RERA number, price, title,
  city (`docs/20` §6b).
* `marketing.photos` + `marketing.ready_to_show` — is it worth a visit? At the
  floor, and legally advertisable.

Conflating them would let a missing price pass as a photo problem, or make a
perfectly legal listing "unready" because its photographer is late.

## 4. No photo is a state, not an error

`placeholder()` returns `{initials, hue, label, kind}` — deterministic from the
property id, so the same listing always shows the same tile and a desk full of
them reads as a catalogue rather than a bug. The UI draws a monogram in the
listing's own hue with the words "no photo yet".

No stock photography, ever. A photograph of somebody else's building on a real
listing is a misrepresentation, and on a partner's Instagram it is a
misrepresentation the partner made.

## 5. Three honest states, one per reason

The desk cannot assume photographs exist, because the media table lives in
Postgres and the objects need storage credentials to sign. So every surface
distinguishes:

| state | what happened | what the user sees |
|---|---|---|
| `ok` | photos exist and the URL is signed | the cover, the gallery, the slot meter |
| `none` | the pipeline works; nobody has uploaded | monogram tile + "no photos yet — at least 3 before this can be advertised" |
| `unavailable` | this instance has no Postgres backend, or no storage credentials | "photos are served from the media pipeline; this instance answered with nothing" |

`GET /properties/{id}/media` answers `503 PIPELINE_UNAVAILABLE` on a file-store
instance — an empty list would read as "no photos", which is a lie with
consequences: an agent would go and shoot a listing that already had eight.

## 6. Surfaces

| surface | what it shows |
|---|---|
| `GET /properties/{id}/media` | `media` (rows, unchanged), plus `photos`, `photo_count`, `cover`, `readiness`, `placeholder` |
| `GET /properties/covers?ids=…` | one call for a table: per id `state`, `cover`, `photo_count`, `readiness`, `placeholder`, `note` — `not_found`, `none` and `unavailable` are all answers |
| desk · Inventory card | photo strip above the unit grid: slot meter, cover at 2× width, thumbnails, `loading="lazy"`, `onerror` → monogram |
| desk · partner catalogue | 34 px cover per row, photo count, `ready_to_show` with what is missing |
| partner catalogue detail | the signed gallery, the publish floor, the cover |
| `scripts/upload-listing-photos.mjs` | uploads through the real prefix and delivers the real trigger; **never** writes `re_property_media` |

The batch read is a **GET with ids in the query string** — it is a read, so it
needs no CSRF token and can be cached by the browser; a POST that only reads is a
write-shaped lie.

## 7. Proof

```
PROOF_PARTNER_PASSWORD=… python3 scripts/proof_property_images.py            # boots this checkout
PROOF_PARTNER_PASSWORD=… python3 scripts/proof_property_images.py --base … --live
node scripts/proof-photo-pipeline.mjs                                        # upload → variants → rows
node scripts/upload-listing-photos.mjs --property prp_xxx photos/*.jpg       # seed a demo, honestly
```

`scripts/proof_property_images.py` — **27 checks**, self-booted and live:

1. photographs counted as photographs (9 rows → 3 photos);
2. the cover is `card`, the URL is presigned, and a **ranged GET on the real URL**
   returns `206`, `image/webp`, a WebP magic number and a byte count — the object
   is really there, not just a string;
3. gallery newest-first, exactly one cover;
4. the floor is 3 of 5 slots, `ready` follows it, and a short listing states the
   shortfall;
5. the batch read answers an unknown id as `not_found` and still returns a tile;
   no ids ⇒ `400`;
6. a partner's catalogue carries the count and a signed cover, `ready_to_show`
   separates law from polish, and the upload's `source_key` is never exposed;
7. a file-store instance answers `503 PIPELINE_UNAVAILABLE` and the batch read
   degrades per id to `unavailable` — never to a silent empty list.

`tests/test_property_media.py` (13 + 1 optional Postgres) covers the same rules on
the pure layer plus the HTTP contract: auth, tenant scoping, the honest 503, the
grouping, the cover preference, the floor, the placeholder's determinism and the
batch read's three states.

`node scripts/proof-photo-pipeline.mjs` remains the only thing that proves the
*write* half (PUT → trigger delivery → three variants → rows, with idempotency and
a loop guard), because that needs object storage and the function runtime. This
document does not duplicate it.

## 7b. Two regressions this run found, and the fixes

**The catalogue went from 300 ms to a timeout.** Adding a photo count and a cover
to each row meant one query per property for the count and another for the cover:
39 listings × 2 queries over a pooled Neon connection, and a partner's catalogue
stopped finishing inside the client's timeout. Fixed with
`PgBackend.property_media_bulk(ids, tenant) ` — one query, same tenant predicate
(`property_id = any(%s)`), grouped in Python — and `_media_rows()` reads it once
per request so the cover never re-queries. The catalogue now answers 39 rows in
**0.63 s**.

**A proof that cannot be run twice is a proof nobody runs before a release.**
`proof_money.py` books a unit on every run — correctly, since the rows are
append-only — and after a handful of runs against a long-lived demo there was no
free unit left, so `next(...)` raised `StopIteration`. It now creates a
run-stamped unit (`PRF-<stamp>`) when stock is exhausted, so the demo's real
inventory is never consumed and the extra unit is visibly a proof's.

## 8. Deliberately not built

* **No `images[]` on the property document.** Adding it would create a second
  writer with a different idea of what a variant is (`docs/15`), and the property
  document is already the row every query copies.
* **No browser upload endpoint.** The browser PUTs to a presigned URL and the
  storage trigger does the rest; an app endpoint that proxies bytes would put the
  app in the middle of a file transfer for no benefit and a new failure mode.
* **No cropping, tagging, alt-text or ordering UI.** Order is "newest first"
  because it needs no state; a real ordering feature needs a `position` column
  written by the app, which means the app writes `re_property_media` — see the
  single-writer rule.
* **No EXIF stripping in the app.** The function owns the bytes; if the pipeline
  ever stores an original with GPS coordinates, that is a privacy finding for
  `docs/08`, not a UI bug.
* **No video, floor plans or brochures.** Same pipeline, different variants; there
  is no demand on the desk yet.
