# ingester/embedder.py
from pathlib import Path
import yaml
from openai import OpenAI

EMBED_DIMENSIONS = 1536
_openai_client = None


def _load_config() -> dict:
    with open(Path(__file__).parent.parent / "config.yaml") as f:
        return yaml.safe_load(f)


def generate_embeddings(
    texts: list,
    model: str = "text-embedding-3-small",
    batch_size: int = 100,
) -> list:
    """Generate embeddings for a list of texts in batches."""
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI()

    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        response = _openai_client.embeddings.create(input=batch, model=model)
        all_embeddings.extend([item.embedding for item in response.data])
    return all_embeddings
