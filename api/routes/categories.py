# api/routes/categories.py
from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/categories/{name}")
def get_category(name: str, request: Request):
    conn = request.app.state.db
    with conn.cursor() as cur:
        cur.execute("""
            SELECT slug, title, summary, categories, touched_at
            FROM articles
            WHERE %s = ANY(categories) AND namespace = 0
            ORDER BY title
        """, (name,))
        rows = cur.fetchall()
    return [
        {
            "slug": r[0],
            "title": r[1],
            "summary": r[2],
            "categories": r[3],
            "touched_at": r[4].isoformat() if r[4] else None,
        }
        for r in rows
    ]
