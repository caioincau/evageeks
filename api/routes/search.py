# api/routes/search.py
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import Optional
from ingester.embedder import generate_embeddings

router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    source_types: Optional[list[str]] = None


@router.post("/search")
def semantic_search(body: SearchRequest, request: Request):
    conn = request.app.state.db
    try:
        embeddings = generate_embeddings([body.query])
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Embedding service unavailable: {e}")
    query_vector = embeddings[0]

    where_clauses = ["a.is_redirect IS NOT TRUE"]
    params = [query_vector, query_vector]

    if body.source_types:
        where_clauses.append("a.source_type = ANY(%s)")
        params.append(body.source_types)

    params.append(body.top_k)
    where_sql = " AND ".join(where_clauses)

    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT
                c.content,
                c.section,
                a.slug,
                a.title,
                1 - (c.embedding <=> %s::vector) AS score,
                a.source_type
            FROM chunks c
            JOIN articles a ON a.id = c.article_id
            WHERE {where_sql}
            ORDER BY c.embedding <=> %s::vector
            LIMIT %s
        """, params)
        rows = cur.fetchall()

    return [
        {
            "content": r[0],
            "section": r[1],
            "article_slug": r[2],
            "article_title": r[3],
            "score": float(r[4]),
            "source_type": r[5],
        }
        for r in rows
    ]
