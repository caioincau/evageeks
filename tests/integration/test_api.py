# tests/integration/test_api.py
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch
from ingester.loader import upsert_article
from api.main import create_app

SAMPLE_ARTICLE = {
    "page_id": 42, "slug": "Rei_Ayanami", "title": "Rei Ayanami",
    "display_title": "Rei Ayanami", "namespace": 0, "content_model": "wikitext",
    "language": "en", "wikitext": "'''Rei'''", "html": "<b>Rei</b>",
    "summary": "Rei is a character.", "sections": [], "categories": ["Characters"],
    "infobox": {}, "templates": [], "internal_links": [], "external_links": [],
    "iw_links": [], "lang_links": [], "properties": {}, "protection": [],
    "rev_id": 1001, "length_bytes": 100, "parse_warnings": [],
    "touched_at": None, "references": [],
}


@pytest.fixture
def client(db_conn):
    with patch("ingester.loader.generate_embeddings", return_value=[[0.1] * 1536]):
        upsert_article(db_conn, SAMPLE_ARTICLE, chunks=[])
    app = create_app(db_conn)
    return TestClient(app)


def test_list_articles(client):
    resp = client.get("/articles")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert any(a["slug"] == "Rei_Ayanami" for a in data["items"])


def test_get_article_by_slug(client):
    resp = client.get("/articles/Rei_Ayanami")
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Rei Ayanami"
    assert data["categories"] == ["Characters"]


def test_get_article_not_found(client):
    resp = client.get("/articles/Nonexistent_Page")
    assert resp.status_code == 404


def test_semantic_search(db_conn):
    chunks = [
        {"section": "_summary", "content": "Rei is the First Child.", "position": 0, "token_count": 6}
    ]
    with patch("ingester.loader.generate_embeddings", return_value=[[0.1] * 1536]):
        upsert_article(db_conn, SAMPLE_ARTICLE, chunks=chunks)
    app = create_app(db_conn)
    client = TestClient(app)
    with patch("api.routes.search.generate_embeddings", return_value=[[0.1] * 1536]):
        resp = client.post("/search", json={"query": "Who is Rei Ayanami?", "top_k": 3})
    assert resp.status_code == 200
    results = resp.json()
    assert isinstance(results, list)
    if results:
        assert "content" in results[0]
        assert "article_slug" in results[0]
        assert "score" in results[0]


def test_get_category_articles(db_conn):
    with patch("ingester.loader.generate_embeddings", return_value=[[0.1] * 1536]):
        upsert_article(db_conn, SAMPLE_ARTICLE, chunks=[])
    app = create_app(db_conn)
    client = TestClient(app)
    resp = client.get("/categories/Characters")
    assert resp.status_code == 200
    data = resp.json()
    assert any(a["slug"] == "Rei_Ayanami" for a in data)
