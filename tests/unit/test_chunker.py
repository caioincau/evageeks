# tests/unit/test_chunker.py
from parser.chunker import chunk_article

LONG_WIKITEXT = " ".join(["word"] * 600)  # 600 words > 512 token limit


def test_redirect_returns_no_chunks():
    article = {"slug": "test", "wikitext": "#REDIRECT[[Other Article]]"}
    chunks = chunk_article(article, chunk_size=512, overlap=50)
    assert len(chunks) == 0


def test_stub_returns_no_chunks():
    article = {"slug": "test", "wikitext": "tiny"}
    chunks = chunk_article(article, chunk_size=512, overlap=50)
    assert len(chunks) == 0


def test_chunk_short_article_returns_one_chunk():
    text = " ".join(["word"] * 80)  # 80 words > 50 token minimum
    article = {"slug": "test", "title": "Test", "wikitext": text, "sections": []}
    chunks = chunk_article(article, chunk_size=512, overlap=50)
    assert len(chunks) == 1
    assert chunks[0]["position"] == 0
    assert chunks[0]["article_slug"] == "test"
    assert "Article: Test" in chunks[0]["content"]


def test_chunk_long_text_splits_into_multiple():
    article = {"slug": "test", "title": "Test", "wikitext": LONG_WIKITEXT, "sections": []}
    chunks = chunk_article(article, chunk_size=512, overlap=50)
    assert len(chunks) > 1


def test_section_aware_chunking():
    intro = " ".join(["intro"] * 60)
    bg_content = " ".join(["background"] * 60)
    notes_content = " ".join(["notes"] * 60)
    wikitext = f"{intro}\n== Background ==\n{bg_content}\n== Notes ==\n{notes_content}"
    bg_offset = len(f"{intro}\n".encode("utf-8"))
    notes_offset = len(f"{intro}\n== Background ==\n{bg_content}\n".encode("utf-8"))
    sections = [
        {"line": "Background", "level": "2", "byteoffset": bg_offset},
        {"line": "Notes", "level": "2", "byteoffset": notes_offset},
    ]
    article = {"slug": "test", "title": "Test Article", "wikitext": wikitext, "sections": sections}
    chunks = chunk_article(article, chunk_size=512, overlap=50)
    sections_found = {c["section"] for c in chunks}
    assert "_intro" in sections_found or "Background" in sections_found
    assert any("Article: Test Article" in c["content"] for c in chunks)


def test_chunk_includes_section_in_header():
    wikitext = "Intro.\n== Creation ==\n" + " ".join(["detail"] * 100)
    sections = [
        {"line": "Creation", "level": "2", "byteoffset": len("Intro.\n".encode("utf-8"))},
    ]
    article = {"slug": "test", "title": "Test", "wikitext": wikitext, "sections": sections}
    chunks = chunk_article(article, chunk_size=512, overlap=50)
    creation_chunks = [c for c in chunks if c["section"] == "Creation"]
    assert len(creation_chunks) > 0
    assert "Section: Creation" in creation_chunks[0]["content"]


def test_chunk_token_count_within_limit():
    article = {"slug": "test", "title": "Test", "wikitext": LONG_WIKITEXT, "sections": []}
    chunks = chunk_article(article, chunk_size=512, overlap=50)
    for chunk in chunks:
        # Token count includes the header, so allow some overhead
        assert chunk["token_count"] <= 530


def test_chunk_overlap_content_shared():
    article = {"slug": "test", "title": "Test", "wikitext": LONG_WIKITEXT, "sections": []}
    chunks = chunk_article(article, chunk_size=200, overlap=50)
    if len(chunks) >= 2:
        words_end = chunks[0]["content"].split()[-20:]
        words_start = chunks[1]["content"].split()[:20]
        assert any(w in words_start for w in words_end)
