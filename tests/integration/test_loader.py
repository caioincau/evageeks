# tests/integration/test_loader.py
from unittest.mock import patch
from ingester.loader import upsert_article, get_fetch_state, set_fetch_state

SAMPLE_ARTICLE = {
    "page_id": 42,
    "slug": "Rei_Ayanami",
    "title": "Rei Ayanami",
    "display_title": "Rei Ayanami",
    "namespace": 0,
    "content_model": "wikitext",
    "language": "en",
    "wikitext": "'''Rei''' is a character.",
    "html": "<p><b>Rei</b> is a character.</p>",
    "summary": "Rei is a character.",
    "sections": [],
    "categories": ["Characters"],
    "infobox": {"name": "Rei Ayanami"},
    "templates": ["Infobox character"],
    "internal_links": ["Neon Genesis Evangelion"],
    "external_links": [],
    "iw_links": [],
    "lang_links": [],
    "properties": {},
    "protection": [],
    "rev_id": 1001,
    "length_bytes": 512,
    "parse_warnings": [],
    "touched_at": "2024-01-15T10:00:00Z",
    "references": [{"ref_name": "anno", "content": "Anno 1997", "url": None, "position": 0}],
}


def test_upsert_article_inserts_new(db_conn):
    with patch("ingester.loader.generate_embeddings", return_value=[[0.1] * 1536]):
        article_id = upsert_article(db_conn, SAMPLE_ARTICLE, chunks=[
            {"section": "_summary", "content": "Rei is a character.", "position": 0, "token_count": 4}
        ])
    assert article_id is not None
    with db_conn.cursor() as cur:
        cur.execute("SELECT slug FROM articles WHERE id = %s", (article_id,))
        row = cur.fetchone()
    assert row[0] == "Rei_Ayanami"


def test_upsert_article_is_idempotent(db_conn):
    with patch("ingester.loader.generate_embeddings", return_value=[[0.1] * 1536]):
        id1 = upsert_article(db_conn, SAMPLE_ARTICLE, chunks=[
            {"section": "_summary", "content": "Rei is a character.", "position": 0, "token_count": 4}
        ])
        id2 = upsert_article(db_conn, SAMPLE_ARTICLE, chunks=[
            {"section": "_summary", "content": "Rei is a character.", "position": 0, "token_count": 4}
        ])
    assert id1 == id2
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM articles WHERE slug = 'Rei_Ayanami'")
        count = cur.fetchone()[0]
    assert count == 1


def test_fetch_state_roundtrip(db_conn):
    set_fetch_state(db_conn, "last_page", {"title": "Rei Ayanami", "index": 5})
    state = get_fetch_state(db_conn, "last_page")
    assert state["title"] == "Rei Ayanami"
    assert state["index"] == 5


def test_upsert_article_chunks_not_duplicated(db_conn):
    chunks = [
        {"section": "_summary", "content": "Rei is a character.", "position": 0, "token_count": 4},
        {"section": "Background", "content": "She is the First Child.", "position": 1, "token_count": 5},
    ]
    with patch("ingester.loader.generate_embeddings", return_value=[[0.1] * 1536, [0.2] * 1536]):
        article_id = upsert_article(db_conn, SAMPLE_ARTICLE, chunks=chunks)
    with patch("ingester.loader.generate_embeddings", return_value=[[0.1] * 1536, [0.2] * 1536]):
        upsert_article(db_conn, SAMPLE_ARTICLE, chunks=chunks)
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM chunks WHERE article_id = %s", (article_id,))
        count = cur.fetchone()[0]
    assert count == 2  # exactly 2 chunks, not 4
