# fetcher/wikidata.py
"""Fetch entity relationships from Wikidata SPARQL endpoint for Evangelion."""
import json
import re
import time
from pathlib import Path
from typing import Optional
import httpx


SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"

_HEADERS = {
    "User-Agent": "EvaGeeks-Fetcher/1.0 (https://github.com/evageeks; evageeks@example.com)",
    "Accept": "application/sparql-results+json",
}

# Wikidata QIDs for the Evangelion franchise
# Q318975 = Neon Genesis Evangelion (anime)
DEFAULT_FRANCHISE_QID = "Q318975"

# ---- SPARQL queries ----

CHARACTERS_QUERY = """
SELECT ?character ?characterLabel ?characterDescription
       ?genderLabel ?voiceActorLabel ?voiceActorNativeLabel
       ?voiceActor
WHERE {{
  ?character wdt:P1441 wd:{qid} .
  ?character wdt:P31/wdt:P279* wd:Q95074 .
  OPTIONAL {{ ?character wdt:P21 ?gender . }}
  OPTIONAL {{
    ?character p:P725 ?vaStatement .
    ?vaStatement ps:P725 ?voiceActor .
    OPTIONAL {{ ?voiceActor wdt:P1559 ?voiceActorNativeLabel . }}
  }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,ja" . }}
}}
ORDER BY ?characterLabel
"""

STAFF_QUERY = """
SELECT ?person ?personLabel ?personDescription
       ?roleLabel ?occupationLabel ?birthDate ?nationalityLabel
WHERE {{
  {{
    wd:{qid} ?rel ?person .
    ?person wdt:P31 wd:Q5 .
    OPTIONAL {{ ?person wdt:P106 ?occupation . }}
    OPTIONAL {{ ?person wdt:P569 ?birthDate . }}
    OPTIONAL {{ ?person wdt:P27 ?nationality . }}
    BIND("related" AS ?role)
  }}
  UNION
  {{
    wd:{qid} wdt:P57 ?person .
    BIND("Director" AS ?role)
  }}
  UNION
  {{
    wd:{qid} wdt:P58 ?person .
    BIND("Screenwriter" AS ?role)
  }}
  UNION
  {{
    wd:{qid} wdt:P86 ?person .
    BIND("Composer" AS ?role)
  }}
  UNION
  {{
    wd:{qid} wdt:P170 ?person .
    BIND("Creator" AS ?role)
  }}
  UNION
  {{
    wd:{qid} wdt:P175 ?person .
    BIND("Performer" AS ?role)
  }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,ja" . }}
}}
GROUP BY ?person ?personLabel ?personDescription ?roleLabel ?occupationLabel ?birthDate ?nationalityLabel
ORDER BY ?personLabel
"""

PRODUCTION_QUERY = """
SELECT ?entity ?entityLabel ?entityDescription
       ?typeLabel ?foundedDate ?countryLabel ?officialWebsite
WHERE {{
  {{
    wd:{qid} wdt:P272 ?entity .
    BIND("Production Company" AS ?type)
  }}
  UNION
  {{
    wd:{qid} wdt:P449 ?entity .
    BIND("Broadcast Network" AS ?type)
  }}
  UNION
  {{
    wd:{qid} wdt:P750 ?entity .
    BIND("Distributor" AS ?type)
  }}
  UNION
  {{
    wd:{qid} wdt:P176 ?entity .
    BIND("Manufacturer/Studio" AS ?type)
  }}
  OPTIONAL {{ ?entity wdt:P571 ?foundedDate . }}
  OPTIONAL {{ ?entity wdt:P17 ?country . }}
  OPTIONAL {{ ?entity wdt:P856 ?officialWebsite . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,ja" . }}
}}
ORDER BY ?typeLabel ?entityLabel
"""

RELATED_WORKS_QUERY = """
SELECT ?work ?workLabel ?workDescription
       ?formatLabel ?publicationDate ?partOfLabel
WHERE {{
  {{
    ?work wdt:P144 wd:{qid} .
    BIND("Derived from" AS ?relation)
  }}
  UNION
  {{
    ?work wdt:P179 wd:{qid} .
    BIND("Part of series" AS ?relation)
  }}
  UNION
  {{
    wd:{qid} wdt:P527 ?work .
    BIND("Has part" AS ?relation)
  }}
  OPTIONAL {{ ?work wdt:P31 ?format . }}
  OPTIONAL {{ ?work wdt:P577 ?publicationDate . }}
  OPTIONAL {{ ?work wdt:P179 ?partOf . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,ja" . }}
}}
ORDER BY ?publicationDate
"""


def _make_session() -> httpx.Client:
    return httpx.Client(timeout=60.0, follow_redirects=True, headers=_HEADERS)


def _run_sparql(
    query: str,
    session: Optional[httpx.Client] = None,
    retries: int = 3,
    rate_limit: float = 5.0,
) -> Optional[list[dict]]:
    """Execute a SPARQL query against the Wikidata endpoint.

    Returns the list of bindings (result rows) or None on failure.
    """
    session = session or _make_session()

    for attempt in range(retries):
        try:
            resp = session.get(
                SPARQL_ENDPOINT,
                params={"query": query, "format": "json"},
            )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 30))
                print(f"    429 rate limited, waiting {retry_after}s")
                time.sleep(retry_after)
                continue
            if resp.status_code == 403:
                print(f"    403 Forbidden (attempt {attempt + 1}/{retries})")
                time.sleep(rate_limit * (2 ** attempt))
                continue
            if resp.status_code == 500:
                print(f"    500 Server error (attempt {attempt + 1}/{retries})")
                time.sleep(rate_limit * (2 ** attempt))
                continue
            resp.raise_for_status()
            data = resp.json()
            return data.get("results", {}).get("bindings", [])
        except (httpx.ConnectError, httpx.ReadTimeout) as e:
            if attempt < retries - 1:
                wait = rate_limit * (2 ** attempt) + 5
                print(f"    Connection error, waiting {wait:.0f}s: {e}")
                time.sleep(wait)
                continue
            print(f"  Failed after {retries} attempts: {e}")
            return None

    return None


def _val(binding: dict, key: str) -> str:
    """Extract a string value from a SPARQL binding, or empty string."""
    entry = binding.get(key)
    if entry is None:
        return ""
    return entry.get("value", "")


def _qid_from_uri(uri: str) -> str:
    """Extract a QID from a Wikidata entity URI."""
    # http://www.wikidata.org/entity/Q12345 -> Q12345
    if "/entity/" in uri:
        return uri.split("/entity/")[-1]
    return uri


def _safe_slug(text: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_]', '_', text)[:80]


def fetch_characters(
    franchise_qid: str,
    session: Optional[httpx.Client] = None,
    rate_limit: float = 5.0,
) -> list[dict]:
    """Fetch Evangelion characters from Wikidata."""
    query = CHARACTERS_QUERY.format(qid=franchise_qid)
    bindings = _run_sparql(query, session=session, rate_limit=rate_limit)
    if bindings is None:
        return []

    # Group by character URI to merge multiple voice actors
    chars = {}
    for row in bindings:
        uri = _val(row, "character")
        qid = _qid_from_uri(uri)
        if qid not in chars:
            chars[qid] = {
                "qid": qid,
                "name": _val(row, "characterLabel"),
                "description": _val(row, "characterDescription"),
                "gender": _val(row, "genderLabel"),
                "voice_actors": [],
            }
        va_name = _val(row, "voiceActorLabel")
        if va_name and va_name not in [va["name"] for va in chars[qid]["voice_actors"]]:
            chars[qid]["voice_actors"].append({
                "name": va_name,
                "native_name": _val(row, "voiceActorNativeLabel"),
                "qid": _qid_from_uri(_val(row, "voiceActor")),
            })

    return list(chars.values())


def fetch_staff(
    franchise_qid: str,
    session: Optional[httpx.Client] = None,
    rate_limit: float = 5.0,
) -> list[dict]:
    """Fetch staff/crew from Wikidata."""
    query = STAFF_QUERY.format(qid=franchise_qid)
    bindings = _run_sparql(query, session=session, rate_limit=rate_limit)
    if bindings is None:
        return []

    # Group by person URI to merge multiple roles/occupations
    staff = {}
    for row in bindings:
        uri = _val(row, "person")
        qid = _qid_from_uri(uri)
        if qid not in staff:
            staff[qid] = {
                "qid": qid,
                "name": _val(row, "personLabel"),
                "description": _val(row, "personDescription"),
                "roles": [],
                "occupations": [],
                "birth_date": _val(row, "birthDate"),
                "nationality": _val(row, "nationalityLabel"),
            }
        role = _val(row, "roleLabel")
        if role and role not in staff[qid]["roles"]:
            staff[qid]["roles"].append(role)
        occupation = _val(row, "occupationLabel")
        if occupation and occupation not in staff[qid]["occupations"]:
            staff[qid]["occupations"].append(occupation)

    return list(staff.values())


def fetch_production(
    franchise_qid: str,
    session: Optional[httpx.Client] = None,
    rate_limit: float = 5.0,
) -> list[dict]:
    """Fetch production companies and distributors from Wikidata."""
    query = PRODUCTION_QUERY.format(qid=franchise_qid)
    bindings = _run_sparql(query, session=session, rate_limit=rate_limit)
    if bindings is None:
        return []

    entities = {}
    for row in bindings:
        uri = _val(row, "entity")
        qid = _qid_from_uri(uri)
        if qid not in entities:
            entities[qid] = {
                "qid": qid,
                "name": _val(row, "entityLabel"),
                "description": _val(row, "entityDescription"),
                "type": _val(row, "typeLabel"),
                "founded": _val(row, "foundedDate"),
                "country": _val(row, "countryLabel"),
                "website": _val(row, "officialWebsite"),
            }

    return list(entities.values())


def fetch_related_works(
    franchise_qid: str,
    session: Optional[httpx.Client] = None,
    rate_limit: float = 5.0,
) -> list[dict]:
    """Fetch related works (manga, films, games, etc.) from Wikidata."""
    query = RELATED_WORKS_QUERY.format(qid=franchise_qid)
    bindings = _run_sparql(query, session=session, rate_limit=rate_limit)
    if bindings is None:
        return []

    works = {}
    for row in bindings:
        uri = _val(row, "work")
        qid = _qid_from_uri(uri)
        if qid not in works:
            works[qid] = {
                "qid": qid,
                "name": _val(row, "workLabel"),
                "description": _val(row, "workDescription"),
                "format": _val(row, "formatLabel"),
                "publication_date": _val(row, "publicationDate"),
                "part_of": _val(row, "partOfLabel"),
            }

    return list(works.values())


def _build_character_article(char: dict, page_id: int) -> dict:
    """Build an article dict from a Wikidata character."""
    name = char["name"]
    slug = _safe_slug(f"wikidata_char_{name}")

    va_lines = []
    for va in char["voice_actors"]:
        native = f" ({va['native_name']})" if va["native_name"] else ""
        va_lines.append(f"  {va['name']}{native}")

    content_parts = [
        f"Character: {name}",
        f"Wikidata ID: {char['qid']}",
        f"Gender: {char['gender']}" if char["gender"] else "",
        "",
        "Description:",
        char["description"] or "No description available.",
        "",
        "Voice Actors:",
    ] + (va_lines or ["  None listed"])

    wikitext = "\n".join(line for line in content_parts if line is not None)

    return {
        "page_id": page_id,
        "slug": slug,
        "title": f"{name} (Wikidata)",
        "display_title": name,
        "namespace": 0,
        "content_model": "wikidata",
        "language": "en",
        "wikitext": wikitext,
        "html": "",
        "summary": char["description"][:500] if char["description"] else f"{name} is a character in Neon Genesis Evangelion.",
        "sections": [],
        "categories": ["Wikidata", "Characters"],
        "infobox": {
            "name": name,
            "qid": char["qid"],
            "gender": char["gender"],
            "voice_actors": [va["name"] for va in char["voice_actors"]],
        },
        "templates": [],
        "internal_links": [],
        "external_links": [f"https://www.wikidata.org/wiki/{char['qid']}"],
        "iw_links": [],
        "lang_links": [],
        "properties": {},
        "protection": [],
        "rev_id": None,
        "length_bytes": len(wikitext),
        "parse_warnings": [],
        "touched_at": None,
        "references": [],
        "source_type": "wikidata",
        "source_url": f"https://www.wikidata.org/wiki/{char['qid']}",
        "authority": 50,
    }


def _build_staff_article(person: dict, page_id: int) -> dict:
    """Build an article dict from a Wikidata staff member."""
    name = person["name"]
    slug = _safe_slug(f"wikidata_staff_{name}")

    content_parts = [
        f"Staff: {name}",
        f"Wikidata ID: {person['qid']}",
        f"Roles: {', '.join(person['roles'])}" if person["roles"] else "",
        f"Occupations: {', '.join(person['occupations'])}" if person["occupations"] else "",
        f"Birth Date: {person['birth_date']}" if person["birth_date"] else "",
        f"Nationality: {person['nationality']}" if person["nationality"] else "",
        "",
        "Description:",
        person["description"] or "No description available.",
    ]

    wikitext = "\n".join(line for line in content_parts if line is not None)

    return {
        "page_id": page_id,
        "slug": slug,
        "title": f"{name} (Wikidata Staff)",
        "display_title": name,
        "namespace": 0,
        "content_model": "wikidata",
        "language": "en",
        "wikitext": wikitext,
        "html": "",
        "summary": person["description"][:500] if person["description"] else f"{name} — staff on Neon Genesis Evangelion.",
        "sections": [],
        "categories": ["Wikidata", "Staff"],
        "infobox": {
            "name": name,
            "qid": person["qid"],
            "roles": person["roles"],
            "occupations": person["occupations"],
            "nationality": person["nationality"],
        },
        "templates": [],
        "internal_links": [],
        "external_links": [f"https://www.wikidata.org/wiki/{person['qid']}"],
        "iw_links": [],
        "lang_links": [],
        "properties": {},
        "protection": [],
        "rev_id": None,
        "length_bytes": len(wikitext),
        "parse_warnings": [],
        "touched_at": None,
        "references": [],
        "source_type": "wikidata",
        "source_url": f"https://www.wikidata.org/wiki/{person['qid']}",
        "authority": 50,
    }


def _build_production_article(entity: dict, page_id: int) -> dict:
    """Build an article dict from a Wikidata production entity."""
    name = entity["name"]
    slug = _safe_slug(f"wikidata_prod_{name}")

    content_parts = [
        f"Entity: {name}",
        f"Wikidata ID: {entity['qid']}",
        f"Type: {entity['type']}" if entity["type"] else "",
        f"Founded: {entity['founded']}" if entity["founded"] else "",
        f"Country: {entity['country']}" if entity["country"] else "",
        f"Website: {entity['website']}" if entity["website"] else "",
        "",
        "Description:",
        entity["description"] or "No description available.",
    ]

    wikitext = "\n".join(line for line in content_parts if line is not None)

    return {
        "page_id": page_id,
        "slug": slug,
        "title": f"{name} (Wikidata)",
        "display_title": name,
        "namespace": 0,
        "content_model": "wikidata",
        "language": "en",
        "wikitext": wikitext,
        "html": "",
        "summary": entity["description"][:500] if entity["description"] else f"{name} — {entity['type']} for Neon Genesis Evangelion.",
        "sections": [],
        "categories": ["Wikidata", "Production", entity["type"]] if entity["type"] else ["Wikidata", "Production"],
        "infobox": {
            "name": name,
            "qid": entity["qid"],
            "type": entity["type"],
            "country": entity["country"],
            "website": entity["website"],
        },
        "templates": [],
        "internal_links": [],
        "external_links": [
            f"https://www.wikidata.org/wiki/{entity['qid']}",
        ] + ([entity["website"]] if entity["website"] else []),
        "iw_links": [],
        "lang_links": [],
        "properties": {},
        "protection": [],
        "rev_id": None,
        "length_bytes": len(wikitext),
        "parse_warnings": [],
        "touched_at": None,
        "references": [],
        "source_type": "wikidata",
        "source_url": f"https://www.wikidata.org/wiki/{entity['qid']}",
        "authority": 50,
    }


def _build_related_work_article(work: dict, page_id: int) -> dict:
    """Build an article dict from a Wikidata related work."""
    name = work["name"]
    slug = _safe_slug(f"wikidata_work_{name}")

    content_parts = [
        f"Title: {name}",
        f"Wikidata ID: {work['qid']}",
        f"Format: {work['format']}" if work["format"] else "",
        f"Publication Date: {work['publication_date']}" if work["publication_date"] else "",
        f"Part of: {work['part_of']}" if work["part_of"] else "",
        "",
        "Description:",
        work["description"] or "No description available.",
    ]

    wikitext = "\n".join(line for line in content_parts if line is not None)

    return {
        "page_id": page_id,
        "slug": slug,
        "title": f"{name} (Wikidata)",
        "display_title": name,
        "namespace": 0,
        "content_model": "wikidata",
        "language": "en",
        "wikitext": wikitext,
        "html": "",
        "summary": work["description"][:500] if work["description"] else f"{name} — related Evangelion work.",
        "sections": [],
        "categories": ["Wikidata", "Related Works"],
        "infobox": {
            "name": name,
            "qid": work["qid"],
            "format": work["format"],
            "publication_date": work["publication_date"],
        },
        "templates": [],
        "internal_links": [],
        "external_links": [f"https://www.wikidata.org/wiki/{work['qid']}"],
        "iw_links": [],
        "lang_links": [],
        "properties": {},
        "protection": [],
        "rev_id": None,
        "length_bytes": len(wikitext),
        "parse_warnings": [],
        "touched_at": None,
        "references": [],
        "source_type": "wikidata",
        "source_url": f"https://www.wikidata.org/wiki/{work['qid']}",
        "authority": 50,
    }


def run_wikidata_fetch(
    output_dir: str,
    franchise_qid: str = DEFAULT_FRANCHISE_QID,
    rate_limit: float = 5.0,
) -> list[dict]:
    """Fetch Wikidata entities and save as JSON articles for ingestion.

    Args:
        output_dir: Directory to write JSON article files.
        franchise_qid: Wikidata QID for the franchise root entity.
                       Defaults to Q318975 (Neon Genesis Evangelion).
        rate_limit: Seconds between SPARQL queries (Wikidata is strict,
                    5s minimum recommended).
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    session = _make_session()
    articles = []

    # --- Characters ---
    print(f"Fetching Wikidata characters for {franchise_qid}...")
    characters = fetch_characters(franchise_qid, session=session, rate_limit=rate_limit)
    print(f"  Found {len(characters)} characters")

    for i, char in enumerate(characters):
        article = _build_character_article(char, page_id=11000000 + i)
        slug = article["slug"]
        outfile = output_path / f"{slug}.json"
        if not outfile.exists():
            outfile.write_text(json.dumps(article, ensure_ascii=False, default=str))
            articles.append(article)

    print(f"  Saved {len([a for a in articles if 'Characters' in a['categories']])} character articles")
    time.sleep(rate_limit)

    # --- Staff ---
    print(f"Fetching Wikidata staff for {franchise_qid}...")
    staff = fetch_staff(franchise_qid, session=session, rate_limit=rate_limit)
    print(f"  Found {len(staff)} staff members")

    staff_start = len(articles)
    for i, person in enumerate(staff):
        article = _build_staff_article(person, page_id=11100000 + i)
        slug = article["slug"]
        outfile = output_path / f"{slug}.json"
        if not outfile.exists():
            outfile.write_text(json.dumps(article, ensure_ascii=False, default=str))
            articles.append(article)

    print(f"  Saved {len(articles) - staff_start} staff articles")
    time.sleep(rate_limit)

    # --- Production companies ---
    print(f"Fetching Wikidata production entities for {franchise_qid}...")
    production = fetch_production(franchise_qid, session=session, rate_limit=rate_limit)
    print(f"  Found {len(production)} production entities")

    prod_start = len(articles)
    for i, entity in enumerate(production):
        article = _build_production_article(entity, page_id=11200000 + i)
        slug = article["slug"]
        outfile = output_path / f"{slug}.json"
        if not outfile.exists():
            outfile.write_text(json.dumps(article, ensure_ascii=False, default=str))
            articles.append(article)

    print(f"  Saved {len(articles) - prod_start} production articles")
    time.sleep(rate_limit)

    # --- Related works ---
    print(f"Fetching Wikidata related works for {franchise_qid}...")
    works = fetch_related_works(franchise_qid, session=session, rate_limit=rate_limit)
    print(f"  Found {len(works)} related works")

    works_start = len(articles)
    for i, work in enumerate(works):
        article = _build_related_work_article(work, page_id=11300000 + i)
        slug = article["slug"]
        outfile = output_path / f"{slug}.json"
        if not outfile.exists():
            outfile.write_text(json.dumps(article, ensure_ascii=False, default=str))
            articles.append(article)

    print(f"  Saved {len(articles) - works_start} related work articles")

    print(f"Total: {len(articles)} Wikidata articles saved to {output_path}")
    return articles
