# parser/chunker.py
import tiktoken
import mwparserfromhell

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


def _extract_plain_text(wikitext: str) -> str:
    """Extract clean plain text from wikitext using mwparserfromhell."""
    try:
        wikicode = mwparserfromhell.parse(wikitext)
        return wikicode.strip_code()
    except Exception:
        return wikitext


def chunk_article(article: dict, chunk_size: int = 512, overlap: int = 50) -> list:
    """
    Split an article into overlapping chunks for RAG.
    Uses wikitext as source, extracting plain text via mwparserfromhell.
    Returns list of dicts with: article_slug, section, content, position, token_count.
    """
    chunks = []
    position = 0
    slug = article.get("slug", "")

    wikitext = article.get("wikitext") or ""
    plain_text = _extract_plain_text(wikitext).strip() if wikitext else ""

    if not plain_text and article.get("summary"):
        plain_text = article["summary"]

    if not plain_text:
        return chunks

    for text in _split_text(plain_text, chunk_size, overlap):
        chunks.append({
            "article_slug": slug,
            "section": "",
            "content": text,
            "position": position,
            "token_count": _count_tokens(text),
        })
        position += 1

    return chunks
