# api/routes/ask.py
import json
from collections import defaultdict
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from ingester.embedder import generate_embeddings
from api.llm import stream_answer

router = APIRouter()

SCORE_THRESHOLD = 0.3
MAX_CHUNKS_PER_ARTICLE = 2


class AskRequest(BaseModel):
    question: str
    top_k: int = 8
    model: Optional[str] = None
    session_id: Optional[str] = None


@router.post("/ask")
def ask(body: AskRequest, request: Request):
    conn = request.app.state.db
    memory = request.app.state.memory

    # 1. Embed the question
    try:
        embeddings = generate_embeddings([body.question])
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Embedding service unavailable: {e}")
    query_vector = embeddings[0]

    # 2. Retrieve relevant chunks with metadata (fetch more than top_k for diversity filtering)
    fetch_limit = body.top_k * 3
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                c.content,
                c.section,
                a.slug,
                a.title,
                1 - (c.embedding <=> %s::vector) AS score,
                a.categories,
                a.infobox
            FROM chunks c
            JOIN articles a ON a.id = c.article_id
            WHERE a.is_redirect IS NOT TRUE
            ORDER BY c.embedding <=> %s::vector
            LIMIT %s
        """, (query_vector, query_vector, fetch_limit))
        rows = cur.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="No relevant content found")

    # 3. Apply score threshold and diversity filter
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
        })
        if len(chunks) >= body.top_k:
            break

    if not chunks:
        raise HTTPException(status_code=404, detail="No relevant content found")

    # 4. Get or create session
    session_id = body.session_id
    history = []
    if session_id:
        history = memory.get_history(session_id)
    if not session_id:
        session_id = memory.create_session()

    # 5. Stream LLM response, then save to memory
    def stream_with_memory():
        full_response = []
        for event in stream_answer(body.question, chunks, model=body.model, history=history):
            yield event
            # Parse the token from the event to build full response
            if event.startswith("data: "):
                try:
                    data = json.loads(event[6:].strip())
                    if data.get("token"):
                        full_response.append(data["token"])
                    if data.get("done"):
                        # Save conversation turn to memory
                        memory.add_turn(session_id, "user", body.question)
                        memory.add_turn(session_id, "assistant", "".join(full_response))
                except (json.JSONDecodeError, KeyError):
                    pass
        # Emit session_id as final event
        yield f"data: {json.dumps({'session_id': session_id})}\n\n"

    return StreamingResponse(
        stream_with_memory(),
        media_type="text/event-stream",
    )
