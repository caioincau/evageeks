# parser/wikitext.py
import re
from typing import Optional
import mwparserfromhell


def parse_article(wikitext: str, api_data: dict) -> dict:
    """Parse wikitext + API response into a structured dict matching the DB schema."""
    wikicode = mwparserfromhell.parse(wikitext)
    return {
        "page_id": api_data.get("pageid"),
        "slug": api_data.get("title", "").replace(" ", "_"),
        "title": api_data.get("title", ""),
        "display_title": api_data.get("displaytitle"),
        "namespace": api_data.get("ns", 0),
        "content_model": api_data.get("contentmodel", "wikitext"),
        "language": api_data.get("pagelanguage"),
        "wikitext": wikitext,
        "html": _extract_html(api_data),
        "summary": _extract_summary(wikicode),
        "sections": api_data.get("sections", []),
        "categories": _extract_categories(wikicode),
        "infobox": _extract_infobox(wikicode),
        "templates": _extract_templates(wikicode),
        "internal_links": _extract_internal_links(wikicode),
        "external_links": _extract_external_links(wikicode),
        "iw_links": api_data.get("iwlinks", []),
        "lang_links": api_data.get("langlinks", []),
        "properties": api_data.get("properties", {}),
        "protection": api_data.get("protection", []),
        "rev_id": api_data.get("revid"),
        "length_bytes": api_data.get("length"),
        "parse_warnings": api_data.get("parsewarnings", []),
        "touched_at": api_data.get("touched"),
        "references": _extract_references(wikicode),
    }


def _extract_html(api_data: dict) -> Optional[str]:
    text = api_data.get("text", "")
    if isinstance(text, dict):
        return text.get("*", "")
    return text or ""


def _extract_summary(wikicode) -> str:
    """Return the first non-empty plain-text paragraph."""
    plain = wikicode.strip_code().strip()
    lines = [l.strip() for l in plain.splitlines() if l.strip()]
    return lines[0][:500] if lines else ""


def _extract_categories(wikicode) -> list:
    cats = []
    for link in wikicode.filter_wikilinks():
        title = str(link.title)
        if title.startswith("Category:"):
            cats.append(title[len("Category:"):])
    return cats


def _extract_infobox(wikicode) -> dict:
    for template in wikicode.filter_templates():
        name = str(template.name).strip().lower()
        if "infobox" in name:
            result = {}
            for param in template.params:
                key = str(param.name).strip()
                value = str(param.value).strip()
                if key and value:
                    result[key] = value
            return result
    return {}


def _extract_templates(wikicode) -> list:
    return list({
        str(t.name).strip()
        for t in wikicode.filter_templates()
    })


def _extract_internal_links(wikicode) -> list:
    links = []
    for link in wikicode.filter_wikilinks():
        title = str(link.title)
        if not title.startswith(("Category:", "File:", "Image:")):
            links.append(title.split("#")[0])
    return list(set(links))


def _extract_external_links(wikicode) -> list:
    links = []
    for node in wikicode.filter_external_links():
        links.append(str(node.url))
    return links


def _extract_references(wikicode) -> list:
    refs = []
    position = 0
    raw = str(wikicode)
    for match in re.finditer(r'<ref(?:\s+name="([^"]*)")?>(.*?)</ref>', raw, re.DOTALL):
        ref_name = match.group(1)
        content = match.group(2).strip()
        url_match = re.search(r'https?://\S+', content)
        refs.append({
            "ref_name": ref_name,
            "content": content,
            "url": url_match.group(0) if url_match else None,
            "position": position,
        })
        position += 1
    return refs
