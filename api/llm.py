# api/llm.py
import os
import json
from pathlib import Path
from typing import Generator
import yaml
from openai import OpenAI


_llm_client = None

SYSTEM_PROMPT = """You are an expert scholar on Neon Genesis Evangelion and the Rebuild of Evangelion, \
drawing from the EvaGeeks wiki — the most comprehensive English-language Evangelion reference.

## Canon Hierarchy
Always specify which canon you are discussing:
- **TV Series** (Episodes 1–26, 1995–96): The original Neon Genesis Evangelion.
- **End of Evangelion** (Episodes 25'/26', 1997): The theatrical ending, distinct from TV eps 25–26.
- **Rebuild of Evangelion** (1.0, 2.0, 3.0, 3.0+1.0): A separate continuity with significant differences.
- **Manga** (Sadamoto, 1994–2013): Yoshiyuki Sadamoto's adaptation, diverges from the anime.
- **ANIMA** (light novel, 2008–2013): An alternate continuation from Episode 24.
If a topic differs across canons, explain each version separately rather than blending them.

## Attribution Rules
- When context contains staff quotes (from "Statements by Evangelion Staff"), attribute precisely: \
who said it, when, and in what publication.
- Clearly distinguish: **confirmed facts** (from the show/official materials), \
**staff statements** (from interviews/commentaries), and **fan theories/analysis** (from fan essays).
- Never present fan analysis as canon fact.

## Response Style
- Be thorough but accessible. Use specific episode numbers and scene references.
- For symbolism, psychology, and themes: present multiple interpretations rather than asserting one.
- Cite the article title and section when referencing information from context.
- Answer in the same language as the question."""

BRIEF_PROMPT = """You are an Evangelion expert. Give concise, direct answers based ONLY on the provided context.
Keep responses to 2-3 paragraphs max. Cite article titles. Answer in the same language as the question."""

ANNO_PROMPT = """You are channeling the words of Hideaki Anno and the Evangelion production staff.
Answer questions using ONLY direct quotes and paraphrases from staff statements in the context.
Always attribute: who said it, when, where. If no staff statement addresses the question, say so.
Do not add your own interpretation — let the creators speak for themselves.
Answer in the same language as the question."""

PROMPTS = {
    "scholar": SYSTEM_PROMPT,
    "brief": BRIEF_PROMPT,
    "anno": ANNO_PROMPT,
}


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


def build_prompt(
    question: str,
    chunks: list[dict],
    history: list[dict] = None,
    mode: str = "scholar",
) -> list[dict]:
    """Build chat messages from question, retrieved chunks, and conversation history."""
    system_prompt = PROMPTS.get(mode, SYSTEM_PROMPT)
    # Build context from chunks with metadata
    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        title = chunk.get("article_title", "Unknown")
        section = chunk.get("section", "")
        content = chunk.get("content", "")
        score = chunk.get("score", 0)
        categories = chunk.get("categories") or []
        infobox = chunk.get("infobox") or {}

        header = f"[{i}] Article: {title}"
        if section and section != "_intro":
            header += f" > {section}"
        if categories:
            header += f" | Categories: {', '.join(categories[:5])}"
        header += f" (relevance: {score:.2f})"

        # Add infobox data if present
        if infobox:
            infobox_str = ", ".join(f"{k}={v}" for k, v in list(infobox.items())[:8])
            header += f"\nInfobox: {infobox_str}"

        context_parts.append(f"{header}\n{content}")

    context = "\n\n---\n\n".join(context_parts)

    messages = [{"role": "system", "content": system_prompt}]

    # Add conversation history (last 3 turns = 6 messages)
    if history:
        for turn in history[-6:]:
            messages.append({"role": turn["role"], "content": turn["content"]})

    messages.append({"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"})
    return messages


def stream_answer(
    question: str,
    chunks: list[dict],
    model: str = None,
    history: list[dict] = None,
    mode: str = "scholar",
) -> Generator[str, None, None]:
    """Stream LLM response as SSE events.

    Yields lines in SSE format:
        data: {"token": "..."}
        data: {"done": true, "sources": [...]}
    """
    config = _load_config()
    model = model or config.get("llm_model", "gpt-4o")
    client = get_llm_client()
    messages = build_prompt(question, chunks, history=history, mode=mode)

    stream = client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
        max_tokens=2048,
    )

    full_response = []
    for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            full_response.append(delta.content)
            yield f"data: {json.dumps({'token': delta.content})}\n\n"

    sources = [
        {"article_title": c.get("article_title", ""), "article_slug": c.get("article_slug", ""), "score": c.get("score", 0)}
        for c in chunks
    ]
    yield f"data: {json.dumps({'done': True, 'sources': sources, 'full_response': ''.join(full_response)})}\n\n"
