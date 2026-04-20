# EvaGeeks Wiki Mirror

A Python pipeline that mirrors [wiki.evageeks.org](https://wiki.evageeks.org/) into PostgreSQL with pgvector, exposing articles via REST API with semantic search (RAG).

## Architecture

```
 1. FETCHER                          2. PARSER
 XML Export + MediaWiki API          mwparserfromhell -> structured JSON
 -> articles.xml + images            -> categories, infobox, links, refs
         |                                    |
         v                                    v
 3. INGESTER                         4. API (FastAPI)
 PostgreSQL + pgvector               REST endpoints + semantic search
 -> articles, chunks, embeddings     -> /articles, /search, /categories
```

Each stage runs independently via CLI and is resumable.

## Quick Start

```bash
# Setup
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Pipeline
python cli.py fetch    # Download articles XML + images from wiki
python cli.py parse    # Parse wikitext into structured JSON
python cli.py ingest   # Load into PostgreSQL + generate embeddings
python cli.py serve    # Start API at http://localhost:8000
```

## Prerequisites

- Python 3.11+
- PostgreSQL with [pgvector](https://github.com/pgvector/pgvector) extension
- `OPENAI_API_KEY` env var (for embeddings during `ingest`)

## Configuration

Edit `config.yaml`:

```yaml
wiki_url: https://wiki.evageeks.org
batch_size: 500
image_workers: 5
chunk_size: 512          # tokens per RAG chunk
chunk_overlap: 50
embed_model: text-embedding-3-small
embed_dimensions: 1536
database_url: postgresql://postgres:postgres@localhost/evageeks
rate_limit_delay: 0.5    # seconds between wiki API requests
data_dir: data
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/articles` | Paginated article list |
| `GET` | `/articles/{slug}` | Full article with metadata |
| `GET` | `/articles/{slug}/images` | Images for an article |
| `POST` | `/search` | Semantic search (RAG) |
| `GET` | `/categories/{name}` | Articles in a category |

Swagger docs available at `http://localhost:8000/docs`.

### Search example

```bash
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query": "Who pilots Evangelion Unit-01?", "top_k": 5}'
```

## Data

The `data/` directory contains the full mirror:

| Directory | Contents | Size |
|-----------|----------|------|
| `data/raw/` | `articles.xml` (1268 articles) | 7.5M |
| `data/parsed/` | 1213 structured JSONs | 31M |
| `data/images/` | 39,732 wiki images | 1.2G |

## Project Structure

```
evageeks/
├── cli.py                  # CLI entry point (fetch/parse/ingest/serve)
├── config.yaml
├── requirements.txt
├── fetcher/
│   ├── export.py           # XML bulk export via Special:Export
│   ├── images.py           # Image enumeration + parallel download
│   └── templates.py        # Template fetch (namespace 10)
├── parser/
│   ├── wikitext.py         # mwparserfromhell -> structured dict
│   └── chunker.py          # Split text into RAG chunks
├── ingester/
│   ├── db.py               # PostgreSQL schema + connection
│   ├── embedder.py         # Batch embedding generation
│   └── loader.py           # Upsert articles + chunks to DB
├── api/
│   ├── main.py             # FastAPI app factory
│   └── routes/
│       ├── articles.py     # Article endpoints
│       ├── search.py       # Semantic search endpoint
│       └── categories.py   # Category endpoint
├── tests/
│   ├── unit/               # Parser, chunker, fetcher tests
│   ├── integration/        # DB + API tests (requires Docker)
│   └── smoke/              # Full pipeline test (requires wiki access)
└── data/
    ├── raw/                # XML dump
    ├── parsed/             # Structured JSONs
    ├── images/             # Downloaded images
    └── errors/             # Parse failure logs
```

## Tests

```bash
# Unit tests (no external deps)
pytest tests/unit/ -v

# Integration tests (requires Docker for testcontainers)
pytest tests/integration/ -v

# Smoke test (requires internet + Docker)
SMOKE_TEST=1 pytest tests/smoke/ -v -s
```
