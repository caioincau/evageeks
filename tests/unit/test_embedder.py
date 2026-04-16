# tests/unit/test_embedder.py
from unittest.mock import patch, MagicMock
from ingester.embedder import generate_embeddings, EMBED_DIMENSIONS


def test_generate_embeddings_returns_correct_shape():
    mock_response = MagicMock()
    mock_response.data = [
        MagicMock(embedding=[0.1] * EMBED_DIMENSIONS),
        MagicMock(embedding=[0.2] * EMBED_DIMENSIONS),
    ]
    with patch("ingester.embedder._openai_client") as mock_client:
        mock_client.embeddings.create.return_value = mock_response
        texts = ["First chunk text.", "Second chunk text."]
        embeddings = generate_embeddings(texts, model="text-embedding-3-small")
    assert len(embeddings) == 2
    assert len(embeddings[0]) == EMBED_DIMENSIONS


def test_generate_embeddings_batches_large_inputs():
    mock_response = MagicMock()
    mock_response.data = [MagicMock(embedding=[0.1] * EMBED_DIMENSIONS)] * 50
    with patch("ingester.embedder._openai_client") as mock_client:
        mock_client.embeddings.create.return_value = mock_response
        texts = ["text"] * 150
        embeddings = generate_embeddings(texts, model="text-embedding-3-small", batch_size=50)
    assert mock_client.embeddings.create.call_count == 3
    assert len(embeddings) == 150
