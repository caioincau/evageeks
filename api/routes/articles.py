# api/routes/articles.py
from fastapi import APIRouter, HTTPException, Request, Query

router = APIRouter()


@router.get("/articles")
def list_articles(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    conn = request.app.state.db
    offset = (page - 1) * page_size
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM articles WHERE namespace = 0")
        total = cur.fetchone()[0]
        cur.execute("""
            SELECT slug, title, summary, categories, touched_at
            FROM articles WHERE namespace = 0
            ORDER BY title
            LIMIT %s OFFSET %s
        """, (page_size, offset))
        rows = cur.fetchall()
    items = [
        {"slug": r[0], "title": r[1], "summary": r[2],
         "categories": r[3], "touched_at": r[4].isoformat() if r[4] else None}
        for r in rows
    ]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.get("/articles/{slug}")
def get_article(slug: str, request: Request):
    conn = request.app.state.db
    with conn.cursor() as cur:
        cur.execute("""
            SELECT page_id, slug, title, display_title, namespace, content_model,
                   language, html, summary, sections, categories, infobox,
                   templates, internal_links, external_links, iw_links,
                   lang_links, properties, protection, rev_id, length_bytes,
                   parse_warnings, touched_at, fetched_at
            FROM articles WHERE slug = %s
        """, (slug,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Article not found")
    cols = [
        "page_id", "slug", "title", "display_title", "namespace", "content_model",
        "language", "html", "summary", "sections", "categories", "infobox",
        "templates", "internal_links", "external_links", "iw_links",
        "lang_links", "properties", "protection", "rev_id", "length_bytes",
        "parse_warnings", "touched_at", "fetched_at",
    ]
    result = dict(zip(cols, row))
    for k in ("touched_at", "fetched_at"):
        if result[k]:
            result[k] = result[k].isoformat()
    return result


@router.get("/articles/{slug}/images")
def get_article_images(slug: str, request: Request):
    conn = request.app.state.db
    with conn.cursor() as cur:
        cur.execute("""
            SELECT i.filename, i.url, i.local_path, i.mime_type, i.width, i.height
            FROM images i
            JOIN article_images ai ON ai.image_id = i.id
            JOIN articles a ON a.id = ai.article_id
            WHERE a.slug = %s
        """, (slug,))
        rows = cur.fetchall()
    cols = ["filename", "url", "local_path", "mime_type", "width", "height"]
    return [dict(zip(cols, r)) for r in rows]
