# fetcher/anilist.py
"""Fetch structured metadata from AniList GraphQL API for Evangelion."""
import json
import re
import time
from pathlib import Path
from typing import Optional
import httpx


ANILIST_API_URL = "https://graphql.anilist.co"

# Default Eva media IDs on AniList
#   30  = Neon Genesis Evangelion (TV)
#   32  = End of Evangelion
#   31  = Death & Rebirth
#   2759 = Evangelion 1.0
#   3784 = Evangelion 2.0
#   3785 = Evangelion 3.0
#   21127 = Evangelion 3.0+1.0
DEFAULT_MEDIA_IDS = [30, 32, 31, 2759, 3784, 3785, 21127]

_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "EvaGeeks-Fetcher/1.0",
}

# ---- GraphQL queries ----

MEDIA_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    title {
      romaji
      english
      native
    }
    description(asHtml: false)
    format
    status
    episodes
    duration
    startDate { year month day }
    endDate { year month day }
    season
    seasonYear
    studios(isMain: true) {
      nodes { id name }
    }
    genres
    tags { name rank }
    averageScore
    popularity
    relations {
      edges {
        relationType
        node {
          id
          title { romaji english }
          type
          format
        }
      }
    }
  }
}
"""

CHARACTERS_QUERY = """
query ($mediaId: Int, $page: Int) {
  Media(id: $mediaId, type: ANIME) {
    title { romaji english }
    characters(page: $page, perPage: 25, sort: [ROLE, FAVOURITES_DESC]) {
      pageInfo { hasNextPage currentPage }
      edges {
        role
        node {
          id
          name { full native alternative }
          description(asHtml: false)
          gender
          age
          image { large }
          favourites
        }
        voiceActors(language: JAPANESE) {
          id
          name { full native }
          language
          image { large }
        }
      }
    }
  }
}
"""

STAFF_QUERY = """
query ($mediaId: Int, $page: Int) {
  Media(id: $mediaId, type: ANIME) {
    title { romaji english }
    staff(page: $page, perPage: 25) {
      pageInfo { hasNextPage currentPage }
      edges {
        role
        node {
          id
          name { full native }
          description(asHtml: false)
          primaryOccupations
          gender
          homeTown
          image { large }
          favourites
        }
      }
    }
  }
}
"""


def _make_session() -> httpx.Client:
    return httpx.Client(timeout=30.0, headers=_HEADERS)


def _run_query(
    query: str,
    variables: dict,
    session: Optional[httpx.Client] = None,
    retries: int = 3,
    rate_limit: float = 1.0,
) -> Optional[dict]:
    """Execute a GraphQL query against the AniList API."""
    session = session or _make_session()

    for attempt in range(retries):
        try:
            resp = session.post(
                ANILIST_API_URL,
                json={"query": query, "variables": variables},
            )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                print(f"    429 rate limited, waiting {retry_after}s")
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            data = resp.json()
            if "errors" in data:
                print(f"    GraphQL errors: {data['errors']}")
                return None
            return data.get("data")
        except (httpx.ConnectError, httpx.ReadTimeout) as e:
            if attempt < retries - 1:
                wait = rate_limit * (2 ** attempt) + 2
                print(f"    Connection error, waiting {wait:.0f}s: {e}")
                time.sleep(wait)
                continue
            print(f"  Failed after {retries} attempts: {e}")
            return None

    return None


def _date_str(date_obj: Optional[dict]) -> str:
    """Convert AniList date dict to YYYY-MM-DD string."""
    if not date_obj:
        return ""
    y = date_obj.get("year") or 0
    m = date_obj.get("month") or 1
    d = date_obj.get("day") or 1
    if y == 0:
        return ""
    return f"{y:04d}-{m:02d}-{d:02d}"


def _clean_description(desc: Optional[str]) -> str:
    """Strip HTML leftovers and excessive whitespace from descriptions."""
    if not desc:
        return ""
    text = re.sub(r'<[^>]+>', ' ', desc)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _safe_slug(text: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_]', '_', text)[:80]


def fetch_media_info(
    media_id: int,
    session: Optional[httpx.Client] = None,
    rate_limit: float = 1.0,
) -> Optional[dict]:
    """Fetch top-level media info for a given AniList media ID."""
    data = _run_query(MEDIA_QUERY, {"id": media_id}, session=session, rate_limit=rate_limit)
    if data and data.get("Media"):
        return data["Media"]
    return None


def fetch_characters(
    media_id: int,
    session: Optional[httpx.Client] = None,
    rate_limit: float = 1.0,
) -> list[dict]:
    """Fetch all characters (paginated) for a media ID."""
    session = session or _make_session()
    all_edges = []
    page = 1

    while True:
        data = _run_query(
            CHARACTERS_QUERY,
            {"mediaId": media_id, "page": page},
            session=session,
            rate_limit=rate_limit,
        )
        if not data or not data.get("Media"):
            break

        chars = data["Media"]["characters"]
        all_edges.extend(chars["edges"])

        if not chars["pageInfo"]["hasNextPage"]:
            break
        page += 1
        time.sleep(rate_limit)

    return all_edges


def fetch_staff(
    media_id: int,
    session: Optional[httpx.Client] = None,
    rate_limit: float = 1.0,
) -> list[dict]:
    """Fetch all staff (paginated) for a media ID."""
    session = session or _make_session()
    all_edges = []
    page = 1

    while True:
        data = _run_query(
            STAFF_QUERY,
            {"mediaId": media_id, "page": page},
            session=session,
            rate_limit=rate_limit,
        )
        if not data or not data.get("Media"):
            break

        staff = data["Media"]["staff"]
        all_edges.extend(staff["edges"])

        if not staff["pageInfo"]["hasNextPage"]:
            break
        page += 1
        time.sleep(rate_limit)

    return all_edges


def _build_media_article(media: dict, page_id: int) -> dict:
    """Build an article dict from AniList media metadata."""
    title_en = media["title"].get("english") or media["title"].get("romaji", "Unknown")
    title_romaji = media["title"].get("romaji", "")
    title_native = media["title"].get("native", "")
    desc = _clean_description(media.get("description"))
    slug = _safe_slug(f"anilist_media_{title_en}")

    studios = [s["name"] for s in (media.get("studios", {}).get("nodes") or [])]
    genres = media.get("genres") or []
    tags = [f"{t['name']} ({t['rank']}%)" for t in (media.get("tags") or [])[:15]]

    relations_text = []
    for edge in (media.get("relations", {}).get("edges") or []):
        rel_title = edge["node"]["title"].get("english") or edge["node"]["title"].get("romaji", "")
        relations_text.append(f"{edge['relationType']}: {rel_title} ({edge['node']['format']})")

    content_parts = [
        f"Title: {title_en}",
        f"Romaji: {title_romaji}",
        f"Native: {title_native}",
        f"Format: {media.get('format', '')}",
        f"Status: {media.get('status', '')}",
        f"Episodes: {media.get('episodes', 'N/A')}",
        f"Duration: {media.get('duration', 'N/A')} min/ep",
        f"Start Date: {_date_str(media.get('startDate'))}",
        f"End Date: {_date_str(media.get('endDate'))}",
        f"Studios: {', '.join(studios)}",
        f"Genres: {', '.join(genres)}",
        f"Tags: {', '.join(tags)}",
        f"Average Score: {media.get('averageScore', 'N/A')}",
        f"Popularity: {media.get('popularity', 'N/A')}",
        "",
        "Description:",
        desc,
        "",
        "Relations:",
    ] + relations_text

    wikitext = "\n".join(content_parts)

    return {
        "page_id": page_id,
        "slug": slug,
        "title": f"{title_en} (AniList)",
        "display_title": title_en,
        "namespace": 0,
        "content_model": "anilist",
        "language": "en",
        "wikitext": wikitext,
        "html": "",
        "summary": desc[:500] if desc else wikitext[:500],
        "sections": [],
        "categories": ["AniList", "Anime Metadata"] + genres,
        "infobox": {
            "format": media.get("format"),
            "episodes": media.get("episodes"),
            "studios": studios,
            "start_date": _date_str(media.get("startDate")),
            "end_date": _date_str(media.get("endDate")),
            "score": media.get("averageScore"),
        },
        "templates": [],
        "internal_links": [],
        "external_links": [f"https://anilist.co/anime/{media['id']}"],
        "iw_links": [],
        "lang_links": [],
        "properties": {},
        "protection": [],
        "rev_id": None,
        "length_bytes": len(wikitext),
        "parse_warnings": [],
        "touched_at": None,
        "references": [],
        "source_type": "anilist",
        "source_url": f"https://anilist.co/anime/{media['id']}",
        "authority": 60,
    }


def _build_character_article(edge: dict, media_title: str, page_id: int) -> dict:
    """Build an article dict from an AniList character edge."""
    node = edge["node"]
    name_full = node["name"].get("full", "Unknown")
    name_native = node["name"].get("native", "")
    alternatives = node["name"].get("alternative") or []
    desc = _clean_description(node.get("description"))
    role = edge.get("role", "")
    slug = _safe_slug(f"anilist_char_{name_full}")

    va_lines = []
    for va in (edge.get("voiceActors") or []):
        va_name = va["name"].get("full", "Unknown")
        va_native = va["name"].get("native", "")
        va_lines.append(f"  {va_name} ({va_native}) [{va.get('language', '')}]")

    content_parts = [
        f"Character: {name_full}",
        f"Native Name: {name_native}",
        f"Alternative Names: {', '.join(alternatives)}" if alternatives else "",
        f"Role: {role}",
        f"Gender: {node.get('gender', 'Unknown')}",
        f"Age: {node.get('age', 'Unknown')}",
        f"Appears in: {media_title}",
        f"Favourites: {node.get('favourites', 0)}",
        "",
        "Description:",
        desc,
        "",
        "Voice Actors (Japanese):",
    ] + va_lines

    wikitext = "\n".join(line for line in content_parts if line is not None)

    return {
        "page_id": page_id,
        "slug": slug,
        "title": f"{name_full} (AniList Character)",
        "display_title": name_full,
        "namespace": 0,
        "content_model": "anilist",
        "language": "en",
        "wikitext": wikitext,
        "html": "",
        "summary": desc[:500] if desc else f"{name_full} is a {role} character in {media_title}.",
        "sections": [],
        "categories": ["AniList", "Characters", f"Characters: {media_title}"],
        "infobox": {
            "name": name_full,
            "native_name": name_native,
            "role": role,
            "gender": node.get("gender"),
            "age": node.get("age"),
            "media": media_title,
        },
        "templates": [],
        "internal_links": [],
        "external_links": [f"https://anilist.co/character/{node['id']}"],
        "iw_links": [],
        "lang_links": [],
        "properties": {},
        "protection": [],
        "rev_id": None,
        "length_bytes": len(wikitext),
        "parse_warnings": [],
        "touched_at": None,
        "references": [],
        "source_type": "anilist",
        "source_url": f"https://anilist.co/character/{node['id']}",
        "authority": 60,
    }


def _build_staff_article(edge: dict, media_title: str, page_id: int) -> dict:
    """Build an article dict from an AniList staff edge."""
    node = edge["node"]
    name_full = node["name"].get("full", "Unknown")
    name_native = node["name"].get("native", "")
    desc = _clean_description(node.get("description"))
    role = edge.get("role", "")
    occupations = node.get("primaryOccupations") or []
    slug = _safe_slug(f"anilist_staff_{name_full}")

    content_parts = [
        f"Staff: {name_full}",
        f"Native Name: {name_native}",
        f"Role on {media_title}: {role}",
        f"Primary Occupations: {', '.join(occupations)}" if occupations else "",
        f"Gender: {node.get('gender', 'Unknown')}",
        f"Hometown: {node.get('homeTown', 'Unknown')}",
        f"Favourites: {node.get('favourites', 0)}",
        "",
        "Description:",
        desc,
    ]

    wikitext = "\n".join(line for line in content_parts if line is not None)

    return {
        "page_id": page_id,
        "slug": slug,
        "title": f"{name_full} (AniList Staff)",
        "display_title": name_full,
        "namespace": 0,
        "content_model": "anilist",
        "language": "en",
        "wikitext": wikitext,
        "html": "",
        "summary": desc[:500] if desc else f"{name_full} — {role} on {media_title}.",
        "sections": [],
        "categories": ["AniList", "Staff", f"Staff: {media_title}"],
        "infobox": {
            "name": name_full,
            "native_name": name_native,
            "role": role,
            "occupations": occupations,
            "media": media_title,
        },
        "templates": [],
        "internal_links": [],
        "external_links": [f"https://anilist.co/staff/{node['id']}"],
        "iw_links": [],
        "lang_links": [],
        "properties": {},
        "protection": [],
        "rev_id": None,
        "length_bytes": len(wikitext),
        "parse_warnings": [],
        "touched_at": None,
        "references": [],
        "source_type": "anilist",
        "source_url": f"https://anilist.co/staff/{node['id']}",
        "authority": 60,
    }


def run_anilist_fetch(
    output_dir: str,
    media_ids: Optional[list[int]] = None,
    rate_limit: float = 1.0,
) -> list[dict]:
    """Fetch AniList metadata and save as JSON articles for ingestion.

    Args:
        output_dir: Directory to write JSON article files.
        media_ids: List of AniList media IDs to fetch.
                   Defaults to DEFAULT_MEDIA_IDS if not provided.
        rate_limit: Seconds between API requests.
    """
    media_ids = media_ids or DEFAULT_MEDIA_IDS
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    session = _make_session()
    articles = []

    # Global counters for page_id offsets
    char_counter = 0
    staff_counter = 0

    for idx, media_id in enumerate(media_ids):
        print(f"Fetching AniList media ID {media_id}...")

        # --- Media info ---
        media = fetch_media_info(media_id, session=session, rate_limit=rate_limit)
        if not media:
            print(f"  Skipping media {media_id} (fetch failed)")
            continue

        media_title = media["title"].get("english") or media["title"].get("romaji", "Unknown")
        print(f"  Title: {media_title}")

        media_article = _build_media_article(media, page_id=10000000 + idx)
        media_slug = media_article["slug"]
        outfile = output_path / f"{media_slug}.json"
        outfile.write_text(json.dumps(media_article, ensure_ascii=False, default=str))
        articles.append(media_article)
        print(f"  Saved media article ({media_article['length_bytes']} chars)")
        time.sleep(rate_limit)

        # --- Characters ---
        print(f"  Fetching characters for {media_title}...")
        char_edges = fetch_characters(media_id, session=session, rate_limit=rate_limit)
        print(f"  Found {len(char_edges)} characters")

        seen_char_ids = set()
        for edge in char_edges:
            char_id = edge["node"]["id"]
            if char_id in seen_char_ids:
                continue
            seen_char_ids.add(char_id)

            char_article = _build_character_article(
                edge, media_title, page_id=10100000 + char_counter,
            )
            char_slug = char_article["slug"]
            outfile = output_path / f"{char_slug}.json"
            if not outfile.exists():
                outfile.write_text(json.dumps(char_article, ensure_ascii=False, default=str))
                articles.append(char_article)
            char_counter += 1

        print(f"  Saved {len(seen_char_ids)} character articles")
        time.sleep(rate_limit)

        # --- Staff ---
        print(f"  Fetching staff for {media_title}...")
        staff_edges = fetch_staff(media_id, session=session, rate_limit=rate_limit)
        print(f"  Found {len(staff_edges)} staff members")

        seen_staff_ids = set()
        for edge in staff_edges:
            staff_id = edge["node"]["id"]
            if staff_id in seen_staff_ids:
                continue
            seen_staff_ids.add(staff_id)

            staff_article = _build_staff_article(
                edge, media_title, page_id=10200000 + staff_counter,
            )
            staff_slug = staff_article["slug"]
            outfile = output_path / f"{staff_slug}.json"
            if not outfile.exists():
                outfile.write_text(json.dumps(staff_article, ensure_ascii=False, default=str))
                articles.append(staff_article)
            staff_counter += 1

        print(f"  Saved {len(seen_staff_ids)} staff articles")
        time.sleep(rate_limit)

    print(f"Total: {len(articles)} AniList articles saved to {output_path}")
    return articles
