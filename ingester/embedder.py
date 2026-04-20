# ingester/embedder.py
import os
from pathlib import Path
import yaml
from openai import OpenAI

EMBED_DIMENSIONS = 1536
_openai_client = None


def _load_config() -> dict:
    with open(Path(__file__).parent.parent / "config.yaml") as f:
        return yaml.safe_load(f)


def _get_client() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        base_url = os.environ.get("ANTHROPIC_BASE_URL", "").rstrip("/anthropic")
        api_key = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
        if base_url and api_key:
            _openai_client = OpenAI(base_url=f"{base_url}/v1", api_key=api_key)
        else:
            _openai_client = OpenAI()
    return _openai_client


def generate_embeddings(
    texts: list,
    model: str = "text-embedding-3-small",
    batch_size: int = 100,
) -> list:
    """Generate embeddings for a list of texts in batches."""
    client = _get_client()
    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        response = client.embeddings.create(input=batch, model=model)
        all_embeddings.extend([item.embedding for item in response.data])
    return all_embeddings
