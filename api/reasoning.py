# api/reasoning.py
"""Query decomposition and canon detection for multi-step reasoning."""
import re
from api.llm import get_llm_client, _load_config


# Canon keywords → category filters
CANON_KEYWORDS = {
    "tv series": ["Neon Genesis Evangelion Episodes"],
    "tv show": ["Neon Genesis Evangelion Episodes"],
    "original series": ["Neon Genesis Evangelion Episodes"],
    "rebuild": ["Rebuild of Evangelion"],
    "rebuild films": ["Rebuild of Evangelion"],
    "new theatrical": ["Rebuild of Evangelion"],
    "1.0": ["Rebuild of Evangelion"],
    "2.0": ["Rebuild of Evangelion"],
    "3.0": ["Rebuild of Evangelion"],
    "3.0+1.0": ["Rebuild of Evangelion"],
    "thrice upon a time": ["Rebuild of Evangelion"],
    "end of evangelion": ["End of Evangelion"],
    "eoe": ["End of Evangelion"],
    "manga": ["Manga"],
    "sadamoto": ["Manga"],
    "anima": ["Anima"],
}

DECOMPOSE_PROMPT = """Break this Evangelion question into 1-3 focused sub-queries for semantic search.
Each sub-query should target a specific fact or topic.
Return ONLY the sub-queries, one per line, no numbering or bullets.
If the question is already simple and focused, return it as-is.

Question: {question}

Sub-queries:"""


def detect_canon_filters(question: str) -> list[str]:
    """Detect canon-specific keywords in the question and return category filters."""
    question_lower = question.lower()
    categories = set()
    for keyword, cats in CANON_KEYWORDS.items():
        if keyword in question_lower:
            categories.update(cats)
    return list(categories)


def decompose_query(question: str) -> list[str]:
    """Break a complex question into focused sub-queries using a cheap LLM call."""
    config = _load_config()
    model = config.get("llm_model", "gpt-4o")
    client = get_llm_client()

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": DECOMPOSE_PROMPT.format(question=question)},
            ],
            max_tokens=200,
            temperature=0,
        )
        text = resp.choices[0].message.content.strip()
        sub_queries = [q.strip() for q in text.split("\n") if q.strip()]
        # Sanity: cap at 3, ensure at least the original
        if not sub_queries:
            return [question]
        return sub_queries[:3]
    except Exception:
        return [question]
