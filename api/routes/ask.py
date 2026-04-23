# api/routes/ask.py
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from ingester.embedder import generate_embeddings
from api.llm import stream_answer

router = APIRouter()


class AskRequest(BaseModel):
    question: str
    top_k: int = 5
    model: str = None


@router.post("/ask")
def ask(body: AskRequest, request: Request):
    conn = request.app.state.db

    # 1. Embed the question
    try:
        embeddings = generate_embeddings([body.question])
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Embedding service unavailable: {e}")
    query_vector = embeddings[0]

    # 2. Retrieve relevant chunks
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                c.content,
                c.section,
                a.slug,
                a.title,
                1 - (c.embedding <=> %s::vector) AS score
            FROM chunks c
            JOIN articles a ON a.id = c.article_id
            WHERE a.is_redirect IS NOT TRUE
            ORDER BY c.embedding <=> %s::vector
            LIMIT %s
        """, (query_vector, query_vector, body.top_k))
        rows = cur.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="No relevant content found")

    chunks = [
        {
            "content": r[0],
            "section": r[1],
            "article_slug": r[2],
            "article_title": r[3],
            "score": float(r[4]),
        }
        for r in rows
    ]

    # 3. Stream LLM response
    return StreamingResponse(
        stream_answer(body.question, chunks, model=body.model),
        media_type="text/event-stream",
    )
