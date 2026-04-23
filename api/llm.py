# api/llm.py
import os
import json
from pathlib import Path
from typing import Generator
import yaml
from openai import OpenAI


_llm_client = None

SYSTEM_PROMPT = """You are an expert on Neon Genesis Evangelion, drawing from the EvaGeeks wiki.
Answer the user's question based ONLY on the provided context chunks.
If the context doesn't contain enough information, say so honestly.
Cite your sources by mentioning the article title when referencing information.
Answer in the same language as the question."""


def _load_config() -> dict:
    with open(Path(__file__).parent.parent / "config.yaml") as f:
        return yaml.safe_load(f)


def get_llm_client() -> OpenAI:
    """Get or create an OpenAI-compatible client for LLM calls.

    Provider priority:
    1. llm_base_url from config.yaml (e.g. Ollama at http://localhost:11434/v1)
    2. ANTHROPIC_BASE_URL env var (LiteLLM corporate proxy)
    3. Default OpenAI
    """
    global _llm_client
    if _llm_client is not None:
        return _llm_client

    config = _load_config()
    base_url = config.get("llm_base_url")
    api_key = "not-needed"

    if base_url:
        # Explicit config (e.g. Ollama)
        api_key = os.environ.get("LLM_API_KEY", "not-needed")
    else:
        # Fall back to LiteLLM / Anthropic proxy
        anthropic_base = os.environ.get("ANTHROPIC_BASE_URL", "").rstrip("/anthropic")
        anthropic_key = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
        if anthropic_base and anthropic_key:
            base_url = f"{anthropic_base}/v1"
            api_key = anthropic_key
        else:
            # Default OpenAI
            base_url = None
            api_key = os.environ.get("OPENAI_API_KEY", "")

    _llm_client = OpenAI(base_url=base_url, api_key=api_key)
    return _llm_client


def build_prompt(question: str, chunks: list[dict]) -> list[dict]:
    """Build chat messages from question and retrieved chunks."""
    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        title = chunk.get("article_title", "Unknown")
        section = chunk.get("section", "")
        content = chunk.get("content", "")
        score = chunk.get("score", 0)
        header = f"[{i}] Article: {title}"
        if section and section != "_intro":
            header += f" > {section}"
        header += f" (relevance: {score:.2f})"
        context_parts.append(f"{header}\n{content}")

    context = "\n\n---\n\n".join(context_parts)

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
    ]


def stream_answer(
    question: str,
    chunks: list[dict],
    model: str = None,
) -> Generator[str, None, None]:
    """Stream LLM response as SSE events.

    Yields lines in SSE format:
        data: {"token": "..."}
        data: {"done": true, "sources": [...]}
    """
    config = _load_config()
    model = model or config.get("llm_model", "claude-sonnet-4-20250514")
    client = get_llm_client()
    messages = build_prompt(question, chunks)

    stream = client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
        max_tokens=1024,
    )

    for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            yield f"data: {json.dumps({'token': delta.content})}\n\n"

    sources = [
        {"article_title": c.get("article_title", ""), "article_slug": c.get("article_slug", ""), "score": c.get("score", 0)}
        for c in chunks
    ]
    yield f"data: {json.dumps({'done': True, 'sources': sources})}\n\n"
