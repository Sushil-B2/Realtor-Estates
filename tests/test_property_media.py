"""
Listing-media read path — the API side of the `property-photos` Neon Function.

Two halves, because they exercise different things:

  * HTTP (file store, always runs): authentication, tenant scoping, and the
    honest 503 when this instance has no Postgres backend — the media rows live
    where the function wrote them, so a file-store deployment cannot serve them.
  * Postgres (opt-in, `RE_TEST_DB_FROM_ENV=1`): the query itself, including the
    tenant predicate that keeps one workspace's photos out of another's response.

What is deliberately *not* asserted here: that an upload produces variants. That
needs object storage and a real delivery, so it is proved end-to-end by
`node scripts/proof-photo-pipeline.mjs` (real PUT → trigger delivery → three
WebP variants → rows), not by a mock.
"""
from __future__ import annotations

import os
import sys

import pytest
from conftest import Session

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ---------------------------------------------------------------------------
# HTTP path
# ---------------------------------------------------------------------------
def _first_property(session) -> str:
    status, body, _ = session.call("GET", "/api/v1/properties")
    assert status == 200, body
    assert body["properties"], "seed data should contain at least one property"
    return body["properties"][0]["id"]


def test_media_requires_auth():
    status, body, _ = Session().call("GET", "/api/v1/properties/prp_x/media", csrf=False)
    assert status == 401, body


def test_media_unknown_property_is_404(owner):
    status, body, _ = owner.call("GET", "/api/v1/properties/prp_doesnotexist/media")
    assert status == 404, body
    assert body["error"]["code"] == "NOT_FOUND"


def test_media_is_tenant_scoped(owner, other_tenant):
    pid = _first_property(owner)
    status, body, _ = other_tenant.call("GET", f"/api/v1/properties/{pid}/media")
    # Another workspace's property id must look exactly like a nonexistent one.
    assert status == 404, body
    assert body["error"]["code"] == "NOT_FOUND"


def test_media_without_postgres_says_so(owner):
    pid = _first_property(owner)
    status, body, _ = owner.call("GET", f"/api/v1/properties/{pid}/media")
    # The suite pins RE_DB=json on purpose; a silent empty list would be a lie.
    assert status == 503, body
    assert body["error"]["code"] == "PIPELINE_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Postgres path
# ---------------------------------------------------------------------------
def _pg_url() -> str:
    if os.environ.get("RE_TEST_DB_FROM_ENV", "") not in ("1", "true", "yes"):
        pytest.skip("set RE_TEST_DB_FROM_ENV=1 to run the Postgres media test")
    from app import db as dbmod

    url = dbmod.admin_dsn()
    if not url:
        pytest.skip("no admin DSN configured")
    return url


def test_property_media_query_filters_by_tenant():
    psycopg = pytest.importorskip("psycopg", reason="pip install 'psycopg[binary]'")
    from app import db as dbmod

    url = dbmod.admin_dsn()
    if not url:
        pytest.skip("no admin DSN configured")
    if not os.environ.get("RE_TEST_DB_FROM_ENV", "") in ("1", "true", "yes"):
        pytest.skip("not opted in to writing to this checkout's database (RE_TEST_DB_FROM_ENV=1)")

    backend = dbmod.PgBackend(url)
    pid = "prp_testmedia001"
    try:
        with psycopg.connect(dbmod.normalise_dsn(url)) as conn:
            with conn.cursor() as cur:
                cur.execute(open(os.path.join(ROOT, "db", "005_property_media.sql"), encoding="utf-8").read())
                cur.execute("delete from re_property_media where property_id = %s", (pid,))
                for tenant, variant, width in (("tnt_a", "thumb", 320), ("tnt_b", "thumb", 320)):
                    cur.execute(
                        """insert into re_property_media
                             (id, property_id, tenant_id, bucket, source_key, variant, object_key,
                              format, width, height, bytes, checksum, created_at, updated_at)
                           values (%s,%s,%s,'projectassets',%s,%s,%s,'webp',%s,213,1024,'c',0,0)""",
                        # one source object per tenant: (source_key, variant) is unique
                        # by design, because a redelivered trigger must upsert, not duplicate
                        (f"med_{tenant}", pid, tenant, f"properties/{pid}/{tenant}.jpg", variant,
                         f"derived/properties/{pid}/{tenant}-{variant}.webp", width),
                    )
            conn.commit()

        mine = backend.property_media(pid, "tnt_a")
        assert [r["variant"] for r in mine] == ["thumb"]
        assert mine[0]["object_key"].startswith("derived/properties/")
        assert backend.property_media(pid, "tnt_c") == []          # no such tenant
        assert backend.property_media("prp_nope", "tnt_a") == []   # no such property
    finally:
        try:
            with psycopg.connect(dbmod.normalise_dsn(url)) as conn:
                with conn.cursor() as cur:
                    cur.execute("delete from re_property_media where property_id = %s", (pid,))
                conn.commit()
        finally:
            backend.close()


# ---------------------------------------------------------------------------
# Choosing what a page shows (docs/25) — pure, so it runs everywhere
# ---------------------------------------------------------------------------
def _rows(*specs):
    """(stem, variant, width, height, bytes) → rows as the function writes them."""
    return [
        {"variant": v, "object_key": f"derived/properties/prp_1/{stem}-{v}.webp",
         "width": w, "height": h, "bytes": b, "checksum": "c", "updated_at": 1,
         "source_key": f"properties/prp_1/{stem}.jpg"}
        for stem, v, w, h, b in specs
    ]


def test_three_variants_are_one_photo_not_three():
    from app import media

    rows = _rows(("1700-a", "thumb", 320, 213, 2878), ("1700-a", "card", 960, 640, 12760),
                 ("1700-a", "hero", 1920, 1280, 32546))
    photos = media.group(rows)
    assert len(photos) == 1
    assert sorted(photos[0]["variants"]) == ["card", "hero", "thumb"]


def test_the_cover_is_the_card_not_the_hero():
    """40 KB where 12 would do, on every card in a table, is a slow desk."""
    from app import media

    rows = _rows(("1700-a", "thumb", 320, 213, 2878), ("1700-a", "card", 960, 640, 12760),
                 ("1700-a", "hero", 1920, 1280, 32546))
    cover = media.cover(rows)
    assert cover["variant"] == "card"
    assert media.cover(_rows(("1700-a", "hero", 1920, 1280, 32546)))["variant"] == "hero"
    assert media.cover([]) is None


def test_newest_upload_becomes_the_cover_and_the_rest_keep_order():
    from app import media

    rows = _rows(("1700-a", "card", 960, 640, 1), ("1800-b", "card", 960, 640, 1),
                 ("1600-c", "card", 960, 640, 1))
    gallery = media.gallery(rows)
    assert [p["photo_id"] for p in gallery] == ["1800-b", "1700-a", "1600-c"]
    assert [p["is_cover"] for p in gallery] == [True, False, False]
    assert all(p["url"] == "" for p in gallery), "the pure layer never signs a URL"


def test_the_publish_floor_is_three_photos():
    from app import media

    empty = media.readiness([])
    assert (empty["have"], empty["ready"], empty["missing"]) == (0, False, 3)
    assert media.readiness(_rows(("a", "card", 1, 1, 1)))["missing"] == 2
    two = media.readiness(_rows(("a", "card", 1, 1, 1), ("b", "card", 1, 1, 1)))
    assert two["ready"] is False and "1 more" in two["note"]
    three = media.readiness(_rows(*[(s, "card", 1, 1, 1) for s in "abc"]))
    assert three["ready"] is True and "enough to advertise" in three["note"]


def test_a_photo_key_that_names_no_known_variant_still_groups():
    """A pipeline change must degrade to one photo per row, not a traceback."""
    from app import media

    assert media.photo_stem("derived/properties/prp_1/x-poster.png") == "x-poster"
    assert media.photo_stem("properties/prp_1/plain") == "plain"
    gallery = media.gallery([{"variant": "poster", "object_key": "derived/prp_1/x-poster.png",
                              "width": 10, "height": 10}])
    assert gallery[0]["variant"] == "poster"


def test_the_empty_tile_is_deterministic_and_never_a_stock_photo():
    from app import media

    one = media.placeholder({"id": "prp_5fed28531e", "title": "Godavari Heights — 3BHK 903",
                             "type": "apartment"})
    same = media.placeholder({"id": "prp_5fed28531e", "title": "Godavari Heights — 3BHK 903",
                              "type": "apartment"})
    other = media.placeholder({"id": "prp_x", "title": "Something Else"})
    assert one == same and one["initials"] == "GH"
    assert one["hue"] != other["hue"] or one["initials"] != other["initials"]
    assert set(one) == {"initials", "hue", "label", "kind"}
    assert one["label"] == "no photo yet"


# ---------------------------------------------------------------------------
# The batch read the desk uses (one round trip for a table)
# ---------------------------------------------------------------------------
def test_covers_requires_auth_and_ids():
    status, body, _ = Session().call("GET", "/api/v1/properties/covers?ids=prp_x", csrf=False)
    assert status == 401
    owner = Session()
    owner.login("owner@realtorestates.app")
    status, body, _ = owner.call("GET", "/api/v1/properties/covers")
    assert status == 400 and body["error"]["code"] == "VALIDATION_FAILED"


def test_covers_answers_every_id_it_is_given(owner):
    """A missing id or an absent pipeline must not blank the whole table."""
    pid = _first_property(owner)
    status, body, _ = owner.call("GET", f"/api/v1/properties/covers?ids={pid},prp_nope")
    assert status == 200, body
    assert set(body["covers"]) == {pid, "prp_nope"}
    # this suite runs on the file store, so the honest answer is "unavailable" …
    assert body["covers"][pid]["state"] in ("unavailable", "none", "ok")
    assert body["covers"][pid]["placeholder"]["initials"], "a cell always has a tile to show"
    # … and another workspace's id is simply not ours
    assert body["covers"]["prp_nope"]["state"] == "not_found"


def test_covers_is_tenant_scoped(owner, other_tenant):
    pid = _first_property(owner)
    status, body, _ = other_tenant.call("GET", f"/api/v1/properties/covers?ids={pid}")
    assert status == 200
    assert body["covers"][pid]["state"] == "not_found"
