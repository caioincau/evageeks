# parser/chunker.py
import tiktoken

_ENCODER = tiktoken.get_encoding("cl100k_base")


def _count_tokens(text: str) -> int:
    return len(_ENCODER.encode(text))


def _split_text(text: str, chunk_size: int, overlap: int) -> list:
    """Split text into overlapping chunks by token count."""
    tokens = _ENCODER.encode(text)
    chunks = []
    start = 0
    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        chunk_tokens = tokens[start:end]
        chunks.append(_ENCODER.decode(chunk_tokens))
        if end == len(tokens):
            break
        start += chunk_size - overlap
    return chunks


def chunk_article(article: dict, chunk_size: int = 512, overlap: int = 50) -> list:
    """
    Split an article into overlapping chunks for RAG.
    Returns list of dicts with: article_slug, section, content, position, token_count.
    """
    chunks = []
    position = 0
    slug = article["slug"]

    if article.get("summary"):
        for text in _split_text(article["summary"], chunk_size, overlap):
            chunks.append({
                "article_slug": slug,
                "section": "_summary",
                "content": text,
                "position": position,
                "token_count": _count_tokens(text),
            })
            position += 1

    for section in article.get("sections", []):
        content = section.get("content", "")
        if not content:
            continue
        for text in _split_text(content, chunk_size, overlap):
            chunks.append({
                "article_slug": slug,
                "section": section.get("line", ""),
                "content": text,
                "position": position,
                "token_count": _count_tokens(text),
            })
            position += 1

    return chunks
