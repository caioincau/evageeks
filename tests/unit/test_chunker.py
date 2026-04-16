# tests/unit/test_chunker.py
from parser.chunker import chunk_article

LONG_TEXT = " ".join(["word"] * 600)  # 600 words > 512 token limit


def test_chunk_short_article_returns_one_chunk():
    article = {"slug": "test", "summary": "Short text.", "sections": []}
    chunks = chunk_article(article, chunk_size=512, overlap=50)
    assert len(chunks) == 1
    assert chunks[0]["section"] == "_summary"
    assert chunks[0]["position"] == 0
    assert chunks[0]["article_slug"] == "test"


def test_chunk_long_text_splits_into_multiple():
    article = {"slug": "test", "summary": LONG_TEXT, "sections": []}
    chunks = chunk_article(article, chunk_size=512, overlap=50)
    assert len(chunks) > 1


def test_chunk_preserves_section_metadata():
    article = {
        "slug": "test",
        "summary": "Intro text.",
        "sections": [
            {"line": "Background", "content": LONG_TEXT},
        ],
    }
    chunks = chunk_article(article, chunk_size=512, overlap=50)
    section_names = {c["section"] for c in chunks}
    assert "_summary" in section_names
    assert "Background" in section_names


def test_chunk_token_count_within_limit():
    article = {"slug": "test", "summary": LONG_TEXT, "sections": []}
    chunks = chunk_article(article, chunk_size=512, overlap=50)
    for chunk in chunks:
        assert chunk["token_count"] <= 512


def test_chunk_overlap_content_shared():
    article = {"slug": "test", "summary": LONG_TEXT, "sections": []}
    chunks = chunk_article(article, chunk_size=200, overlap=50)
    if len(chunks) >= 2:
        words_end = chunks[0]["content"].split()[-20:]
        words_start = chunks[1]["content"].split()[:20]
        assert any(w in words_start for w in words_end)
