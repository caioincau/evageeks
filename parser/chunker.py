# parser/chunker.py
import re
import tiktoken
import mwparserfromhell

_ENCODER = tiktoken.get_encoding("cl100k_base")

MIN_CONTENT_TOKENS = 50


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


def _is_redirect(wikitext: str) -> bool:
    return wikitext.strip().upper().startswith("#REDIRECT")


def _split_by_sections(wikitext: str, sections: list) -> list:
    """Split wikitext into (section_name, section_wikitext) tuples using byteoffsets."""
    if not sections:
        return [("_intro", wikitext)]

    # Sort sections by byteoffset
    sorted_sections = sorted(
        [s for s in sections if s.get("byteoffset") is not None],
        key=lambda s: s["byteoffset"],
    )

    if not sorted_sections:
        return [("_intro", wikitext)]

    result = []
    wikitext_bytes = wikitext.encode("utf-8")

    # Text before first section
    first_offset = sorted_sections[0]["byteoffset"]
    if first_offset > 0:
        intro = wikitext_bytes[:first_offset].decode("utf-8", errors="replace")
        if intro.strip():
            result.append(("_intro", intro))

    # Each section
    for i, section in enumerate(sorted_sections):
        start = section["byteoffset"]
        end = sorted_sections[i + 1]["byteoffset"] if i + 1 < len(sorted_sections) else len(wikitext_bytes)
        section_text = wikitext_bytes[start:end].decode("utf-8", errors="replace")
        section_name = section.get("line", "")

        # Strip the heading line itself (== Heading ==)
        section_text = re.sub(r'^=+\s*.*?\s*=+\s*\n?', '', section_text, count=1)

        if section_text.strip():
            result.append((section_name, section_text))

    return result


def chunk_article(article: dict, chunk_size: int = 512, overlap: int = 50) -> list:
    """Split an article into overlapping chunks for RAG.

    Section-aware: splits by wiki sections first, then chunks within each section.
    Prepends metadata headers so embeddings encode article+section context.
    Skips redirects and stubs.
    """
    wikitext = article.get("wikitext") or ""

    # Skip redirects and stubs
    if _is_redirect(wikitext):
        return []

    plain_full = _extract_plain_text(wikitext).strip()
    if _count_tokens(plain_full) < MIN_CONTENT_TOKENS:
        # Fall back to summary for stubs
        if article.get("summary") and _count_tokens(article["summary"]) >= MIN_CONTENT_TOKENS:
            plain_full = article["summary"]
        else:
            return []

    chunks = []
    position = 0
    slug = article.get("slug", "")
    title = article.get("title", slug.replace("_", " "))
    sections = article.get("sections", [])

    # Split by sections
    section_parts = _split_by_sections(wikitext, sections)

    for section_name, section_wikitext in section_parts:
        plain_text = _extract_plain_text(section_wikitext).strip()
        if not plain_text or _count_tokens(plain_text) < 10:
            continue

        # Prepend metadata header for embedding context
        header = f"Article: {title}"
        if section_name and section_name != "_intro":
            header += f" | Section: {section_name}"

        for text in _split_text(plain_text, chunk_size, overlap):
            content_with_header = f"{header}\n{text}"
            chunks.append({
                "article_slug": slug,
                "section": section_name,
                "content": content_with_header,
                "position": position,
                "token_count": _count_tokens(content_with_header),
            })
            position += 1

    return chunks
