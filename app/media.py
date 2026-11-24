"""
Listing photographs — choosing which one a page shows, and admitting when there
is none.

The pipeline itself is older than this module: an upload to `properties/<id>/…`
fires the `property-photos` Neon Function, which renders `thumb` (320w), `card`
(960w) and `hero` (1920w) WebP variants and writes one row per variant into
`re_property_media` (docs/15). The app reads them through
`GET /api/v1/properties/{id}/media`, and those rows are *private objects*, so
every URL is a 15-minute presigned GET.

What was missing — gap G5 — is everything between "rows exist" and "a person
sees a photo": which row is the cover, how several photos of one property are
grouped, how many photos a listing needs before it can be advertised, and what a
card shows when the answer is "no photos at all". Those are rules, so they live
here, pure, with no storage and no HTTP:

1. **A photo is a stem, not a row.** Three variants of one upload are one photo:
   the row's `object_key` ends in `-<variant>.webp`, and the stem before it is
   the photo's identity. Counting rows would report nine photos where there are
   three.
2. **The cover is chosen, not guessed.** Prefer `card` (960w is the smallest
   variant that is not visibly soft in a table cell on a retina screen), then
   `thumb`, then `hero`, then anything. A page that picks `hero` for a 40px
   thumbnail ships 32 KB where 2.8 KB would do.
3. **Five is the slot budget, three is the publish floor.** A listing with one
   photo is a listing a buyer scrolls past; MRMs and the portals ask for at
   least three, and a partner advertising a project with one stock render is how
   a lead becomes a wasted visit.
4. **No photo is a state, not an error.** When there is nothing to show, the
   answer is a deterministic monogram (initials + hue from the property id) that
   says "no photo yet" — never a broken-image glyph, and never a stock photo of
   somebody else's building.
"""
from __future__ import annotations

from typing import Iterable

# What the pipeline renders, smallest useful first.
VARIANT_PREFERENCE = ("card", "thumb", "hero", "orig")
VARIANT_NAMES = ("thumb", "card", "hero")

SLOTS = 5                 # how many photos a listing page has room for
PUBLISH_FLOOR = 3         # below this, the listing is not advertisable

_SUFFIXES = tuple(f"-{v}" for v in VARIANT_NAMES + ("orig", "full", "large", "medium", "small"))
_EXTENSIONS = (".webp", ".jpg", ".jpeg", ".png", ".avif")


def photo_stem(object_key: str) -> str:
    """`derived/properties/prp_1/1700-abc-card.webp` → `1700-abc`.

    The identity of a photo across its variants. Deliberately forgiving: an
    unknown-named variant falls back to the bare filename, so a pipeline change
    groups one photo per row rather than crashing the page.
    """
    name = str(object_key or "").rsplit("/", 1)[-1]
    for ext in _EXTENSIONS:
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
            break
    for suffix in _SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def group(
    rows: Iterable[dict],
    *,
    photo_order: Iterable[str] | None = None,
    pinned_cover_id: str = "",
) -> list[dict]:
    """Rows → photos, respecting explicit `photo_order` & `pinned_cover_id` (`PLANS.md` G5),
    falling back to newest stem first.
    """
    photos: dict[str, dict] = {}
    for row in rows:
        key = str(row.get("object_key") or "")
        if not key:
            continue
        stem = photo_stem(key)
        photo = photos.setdefault(stem, {"photo_id": stem, "variants": {}, "source_key": ""})
        variant = str(row.get("variant") or "orig")
        photo["variants"][variant] = row
        if not photo["source_key"] and row.get("source_key"):
            photo["source_key"] = row["source_key"]
    default_ordered = sorted(photos.values(), key=lambda p: p["photo_id"], reverse=True)
    if not photo_order and not pinned_cover_id:
        return default_ordered

    seen: set[str] = set()
    ordered: list[dict] = []
    if pinned_cover_id and pinned_cover_id in photos:
        ordered.append(photos[pinned_cover_id])
        seen.add(pinned_cover_id)
    for pid in (photo_order or ()):
        spid = str(pid or "")
        if spid in photos and spid not in seen:
            ordered.append(photos[spid])
            seen.add(spid)
    for p in default_ordered:
        if p["photo_id"] not in seen:
            ordered.append(p)
    return ordered


def pick(photo: dict) -> dict | None:
    """The variant a page should actually load for this photo."""
    for name in VARIANT_PREFERENCE:
        if name in photo.get("variants"):
            return photo["variants"][name]
    variants = photo.get("variants") or {}
    return next(iter(variants.values()), None)


def cover(
    rows: Iterable[dict],
    *,
    photo_order: Iterable[str] | None = None,
    pinned_cover_id: str = "",
) -> dict | None:
    """The one photo a card, a table row or a brochure cover shows."""
    photos = group(rows, photo_order=photo_order, pinned_cover_id=pinned_cover_id)
    if not photos:
        return None
    return pick(photos[0])


def gallery(
    rows: Iterable[dict],
    *,
    photo_order: Iterable[str] | None = None,
    pinned_cover_id: str = "",
) -> list[dict]:
    """Every photo once, in display order, each with the variant to load.

    `url` is filled by the route (presigning belongs to the layer that owns the
    storage credentials, not to a pure function).
    """
    out = []
    for i, photo in enumerate(group(rows, photo_order=photo_order, pinned_cover_id=pinned_cover_id)):
        chosen = pick(photo)
        if not chosen:
            continue
        hero = photo["variants"].get("hero") or chosen
        out.append({
            "photo_id": photo["photo_id"],
            "variant": chosen.get("variant"),
            "key": chosen.get("object_key"),
            "width": chosen.get("width"),
            "height": chosen.get("height"),
            "bytes": int(chosen.get("bytes") or 0),
            "checksum": chosen.get("checksum", ""),
            "hero_key": hero.get("object_key"),
            "hero_width": hero.get("width"),
            "hero_height": hero.get("height"),
            "variants": sorted(photo["variants"].keys()),
            "is_cover": i == 0,
            "url": "",
            "hero_url": "",
            "updated_at": chosen.get("updated_at"),
        })
    return out


def readiness(rows_or_count) -> dict:
    """How far a listing is from being advertisable, in photos."""
    have = (len(group(rows_or_count)) if not isinstance(rows_or_count, int)
            else int(rows_or_count))
    missing = max(0, PUBLISH_FLOOR - have)
    if have >= PUBLISH_FLOOR:
        note = f"{have} photos — enough to advertise"
    elif have == 0:
        note = f"no photos yet — at least {PUBLISH_FLOOR} before this can be advertised"
    else:
        note = f"{have} photo{'s' if have != 1 else ''} — {missing} more before this can be advertised"
    return {
        "have": have,
        "slots": SLOTS,
        "need": PUBLISH_FLOOR,
        "missing": missing,
        "ready": have >= PUBLISH_FLOOR,
        "note": note,
    }


def placeholder(prop: dict | None) -> dict:
    """The tile a card shows when there is no photograph.

    Deterministic from the property id, so the same listing always has the same
    tile and a desk full of them looks like a catalogue rather than a bug. No
    stock photography: a photo of somebody else's building on a real listing is
    a compliance problem, not a design choice.
    """
    prop = prop or {}
    title = str(prop.get("title") or "Untitled")
    words = [w for w in title.replace("—", " ").replace("-", " ").split() if w[:1].isalnum()]
    initials = "".join(w[0] for w in words[:2]).upper() or "?"
    ident = str(prop.get("id") or title)
    hue = sum(ord(c) * (i + 7) for i, c in enumerate(ident)) % 360
    return {
        "initials": initials,
        "hue": hue,
        "label": "no photo yet",
        "kind": str(prop.get("type") or prop.get("category") or "property").replace("_", " "),
    }
