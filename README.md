# EvaGeeks Wiki Mirror + Evangelion AI Agent

A Python pipeline that mirrors [wiki.evageeks.org](https://wiki.evageeks.org/) and the [EvaGeeks forum](https://forum.evageeks.org/) into PostgreSQL with pgvector, exposing an AI-powered Evangelion expert via REST API with semantic search and streaming RAG answers.

## Architecture

```
 1. FETCHER                          2. PARSER
 Wiki XML + Forum + Interviews      mwparserfromhell -> structured JSON
 -> articles, threads, docs          -> section-aware chunks with metadata
         |                                    |
         v                                    v
 3. INGESTER                         4. API (FastAPI)
 PostgreSQL + pgvector               REST + RAG with streaming LLM
 -> 36,490 embedded chunks           -> /ask, /search, /articles
```

Each stage runs independently via CLI and is resumable.

## Quick Start

### Option A: Restore from backup (recommended)

If you have the database dump, skip the pipeline and restore directly:

```bash
# Setup
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Start PostgreSQL
brew services start postgresql@17  # or your version

# Create database
createdb -U postgres evageeks
psql -U postgres -d evageeks -c "CREATE EXTENSION vector"

# Restore dump (includes all 36,490 chunks with embeddings)
cat data/evageeks.sql.gz.* | gunzip | psql -U postgres -d evageeks

# Start API
python cli.py serve
```

### Option B: Run the full pipeline

```bash
# Setup
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Pipeline
python cli.py fetch             # Download wiki articles XML + images
python cli.py parse             # Parse wikitext into structured JSON
python cli.py fetch-forum       # Scrape EvaGeeks forum threads
python cli.py fetch-interviews  # Fetch external interviews
python cli.py ingest            # Load into PostgreSQL + generate embeddings
python cli.py serve             # Start API at http://localhost:8000
```

## Prerequisites

- Python 3.11+
- PostgreSQL 17+ with [pgvector](https://github.com/pgvector/pgvector) extension
- LLM API access for `/ask` endpoint (LiteLLM, OpenAI, or Ollama)
- Embedding API access for `ingest` (OpenAI-compatible)

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
llm_model: gpt-4o       # LLM for /ask endpoint
llm_base_url: null       # null = auto-detect, or set to http://localhost:11434/v1 for Ollama
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/articles` | Paginated article list |
| `GET` | `/articles/{slug}` | Full article with metadata |
| `GET` | `/articles/{slug}/images` | Images for an article |
| `POST` | `/search` | Semantic search (raw chunks) |
| `POST` | `/ask` | Ask a question, get a streamed answer (SSE) |
| `GET` | `/categories/{name}` | Articles in a category |

Swagger docs available at `http://localhost:8000/docs`.

### Ask endpoint

The `/ask` endpoint supports:
- **Streaming** (SSE): tokens arrive in real-time
- **Conversation memory**: pass `session_id` for follow-up questions
- **Response modes**: `scholar` (thorough), `brief` (concise), `anno` (staff quotes only)
- **Query decomposition**: complex questions are split into sub-queries automatically
- **Canon detection**: mentions of "Rebuild", "TV series", etc. filter results by canon
- **Source diversity**: max 2 chunks per article, score threshold filtering

```bash
# Scholar mode (default) - thorough, multi-perspective
curl -N -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Who is Rei Ayanami?", "top_k": 8}'

# Anno mode - only staff quotes and interviews
curl -N -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Why did Anno create Evangelion?", "mode": "anno"}'

# Brief mode - concise answers
curl -N -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Asuka vs Rei differences in Rebuild?", "mode": "brief"}'

# Follow-up question (pass session_id from previous response)
curl -N -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "And what about in the manga?", "session_id": "abc123"}'
```

### Search endpoint

```bash
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query": "Who pilots Evangelion Unit-01?", "top_k": 5}'

# Filter by source type
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query": "fan theories about Third Impact", "source_types": ["forum"]}'
```

## Knowledge Base

| Source | Articles | Chunks | Description |
|--------|----------|--------|-------------|
| Wiki | 1,213 (568 with content) | 5,239 | EvaGeeks wiki articles with section-aware chunking |
| Forum | 8,164 threads | ~31,000 | EvaGeeks forum (TV+EoE, Rebuild, General, Everything Else) |
| Interviews | 4 documents | 182 | Anno interviews and production docs from gwern.net |
| **Total** | **9,381** | **36,490** | |

Additional data:
- 39,732 wiki images (1.2G)
- Database dump with embeddings: `data/evageeks.sql.gz.*` (280M, split in 4 parts)

## Database Backup & Restore

### Backup

```bash
pg_dump -U postgres -d evageeks --format=plain | gzip > evageeks.sql.gz
```

### Restore

```bash
# Create database
createdb -U postgres evageeks
psql -U postgres -d evageeks -c "CREATE EXTENSION vector"

# Restore from split files in this repo
cat data/evageeks.sql.gz.* | gunzip | psql -U postgres -d evageeks
```

## Project Structure

```
evageeks/
├── cli.py                  # CLI: fetch, parse, ingest, serve, fetch-forum, fetch-interviews
├── config.yaml
├── requirements.txt
├── fetcher/
│   ├── export.py           # Wiki XML bulk export via Special:Export
│   ├── images.py           # Image enumeration + parallel download
│   ├── templates.py        # Template fetch (namespace 10)
│   ├── forum.py            # EvaGeeks forum scraper
│   └── interviews.py       # External interview fetcher
├── parser/
│   ├── wikitext.py         # mwparserfromhell -> structured dict
│   └── chunker.py          # Section-aware chunking with metadata headers
├── ingester/
│   ├── db.py               # PostgreSQL schema + connection
│   ├── embedder.py         # Batch embedding generation (OpenAI-compatible)
│   └── loader.py           # Upsert articles + chunks to DB
├── api/
│   ├── main.py             # FastAPI app factory
│   ├── llm.py              # LLM client, expert prompts, response modes
│   ├── memory.py           # Conversation memory store
│   ├── reasoning.py        # Query decomposition + canon detection
│   └── routes/
│       ├── articles.py     # Article endpoints
│       ├── search.py       # Semantic search (with source type filter)
│       ├── ask.py          # RAG endpoint with streaming + memory
│       └── categories.py   # Category endpoint
├── scripts/
│   └── backfill_flags.py   # Migration: flag redirects/stubs
├── tests/
│   ├── unit/               # Parser, chunker, fetcher tests (25 tests)
│   ├── integration/        # DB + API tests (requires Docker)
│   └── smoke/              # Full pipeline test
└── data/
    ├── raw/                # Wiki XML dump
    ├── parsed/             # Structured JSONs (wiki + forum + interviews)
    ├── images/             # Downloaded wiki images
    ├── errors/             # Parse failure logs
    └── evageeks.sql.gz.*   # PostgreSQL dump (4 parts, 280M total)
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
