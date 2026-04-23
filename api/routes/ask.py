# api/routes/ask.py
import json
from collections import defaultdict
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from ingester.embedder import generate_embeddings
from api.llm import stream_answer
from api.reasoning import decompose_query, detect_canon_filters

router = APIRouter()

SCORE_THRESHOLD = 0.3
MAX_CHUNKS_PER_ARTICLE = 2


class AskRequest(BaseModel):
    question: str
    top_k: int = 8
    model: Optional[str] = None
    session_id: Optional[str] = None
    mode: str = "scholar"  # scholar, brief, anno


def _retrieve_chunks(conn, query_vector, top_k: int, canon_categories: list[str] = None):
    """Retrieve chunks with diversity and score filtering."""
    conn.rollback()  # Reset any failed transaction state
    fetch_limit = top_k * 3

    where_clauses = ["a.is_redirect IS NOT TRUE"]
    extra_params = []
    if canon_categories:
        where_clauses.append("a.categories && %s::text[]")
        extra_params.append(canon_categories)

    where_sql = " AND ".join(where_clauses)

    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT
                c.content,
                c.section,
                a.slug,
                a.title,
                1 - (c.embedding <=> %s::vector) AS score,
                a.categories,
                a.infobox,
                a.source_type
            FROM chunks c
            JOIN articles a ON a.id = c.article_id
            WHERE {where_sql}
            ORDER BY c.embedding <=> %s::vector
            LIMIT %s
        """, [query_vector] + extra_params + [query_vector, fetch_limit])
        rows = cur.fetchall()

    # Apply score threshold and diversity filter
    chunks = []
    article_count = defaultdict(int)
    for r in rows:
        score = float(r[4])
        if score < SCORE_THRESHOLD:
            continue
        slug = r[2]
        if article_count[slug] >= MAX_CHUNKS_PER_ARTICLE:
            continue
        article_count[slug] += 1
        chunks.append({
            "content": r[0],
            "section": r[1],
            "article_slug": slug,
            "article_title": r[3],
            "score": score,
            "categories": r[5] or [],
            "infobox": r[6] or {},
            "source_type": r[7] or "wiki",
        })
        if len(chunks) >= top_k:
            break

    return chunks


@router.post("/ask")
def ask(body: AskRequest, request: Request):
    conn = request.app.state.db
    memory = request.app.state.memory

    # 1. Detect canon filters from question
    canon_categories = detect_canon_filters(body.question)

    # 2. Decompose complex questions into sub-queries
    sub_queries = decompose_query(body.question)

    # 3. Retrieve chunks for each sub-query and merge
    all_chunks = {}
    for sq in sub_queries:
        try:
            embeddings = generate_embeddings([sq])
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Embedding service unavailable: {e}")
        query_vector = embeddings[0]

        retrieved = _retrieve_chunks(
            conn, query_vector, top_k=body.top_k,
            canon_categories=canon_categories or None,
        )
        for chunk in retrieved:
            key = (chunk["article_slug"], chunk["section"], chunk["content"][:100])
            if key not in all_chunks or chunk["score"] > all_chunks[key]["score"]:
                all_chunks[key] = chunk

    # Sort by score and take top_k
    chunks = sorted(all_chunks.values(), key=lambda c: c["score"], reverse=True)[:body.top_k]

    if not chunks:
        raise HTTPException(status_code=404, detail="No relevant content found")

    # 4. Get or create session
    session_id = body.session_id
    history = []
    if session_id:
        history = memory.get_history(session_id)
    if not session_id:
        session_id = memory.create_session()

    # 5. Stream LLM response with mode
    def stream_with_memory():
        full_response = []
        for event in stream_answer(body.question, chunks, model=body.model, history=history, mode=body.mode):
            yield event
            if event.startswith("data: "):
                try:
                    data = json.loads(event[6:].strip())
                    if data.get("token"):
                        full_response.append(data["token"])
                    if data.get("done"):
                        memory.add_turn(session_id, "user", body.question)
                        memory.add_turn(session_id, "assistant", "".join(full_response))
                except (json.JSONDecodeError, KeyError):
                    pass
        yield f"data: {json.dumps({'session_id': session_id})}\n\n"

    return StreamingResponse(
        stream_with_memory(),
        media_type="text/event-stream",
    )
