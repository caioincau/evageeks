# EvaGeeks Wiki Mirror — Design Document

**Date:** 2026-04-16
**Status:** Approved

---

## Objetivo

Criar uma cópia local completa de `https://wiki.evageeks.org/` em Python, capturando todos os artigos, imagens, templates e referências no estado atual (sem histórico de edições). O sistema servirá como base para:

1. **RAG (Retrieval-Augmented Generation)** — busca semântica sobre o conteúdo do wiki
2. **API REST** — endpoints para consulta programática dos artigos

---

## Arquitetura Geral

O sistema é composto por 4 camadas independentes, cada uma executável isoladamente via CLI:

```
┌─────────────────────────────────────────────────────┐
│  1. FETCHER                                         │
│  XML Export via Special:Export + MediaWiki API      │
│  → artigos.xml  +  lista de imagens                 │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│  2. PARSER                                          │
│  mwparserfromhell → texto limpo, infoboxes,         │
│  categorias, links internos/externos, templates     │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│  3. INGESTER                                        │
│  PostgreSQL + pgvector                              │
│  → artigos, chunks, embeddings, imagens, refs       │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│  4. API (FastAPI)                                   │
│  REST endpoints para consulta de artigos            │
│  + endpoint de busca semântica (RAG)                │
└─────────────────────────────────────────────────────┘
```

Cada camada tem seu próprio módulo Python e pode ser interrompida e retomada. O estado de progresso é persistido no banco de dados.

---

## Componentes Detalhados

### 1. Fetcher (`fetcher/`)

- **`export.py`** — Baixa o XML completo via `Special:Export?action=submit&allpages=1` em chunks de 500 artigos. Salva em `data/raw/articles.xml`.
- **`images.py`** — Usa `api.php?action=query&list=allimages` para enumerar todas as imagens, depois baixa cada arquivo para `data/images/`. Registra no banco quais já foram baixadas (resumível).
- **`templates.py`** — Fetch dos templates referenciados nos artigos (namespace 10) via API, necessários para resolver infoboxes.

### 2. Parser (`parser/`)

- **`wikitext.py`** — Usa `mwparserfromhell` para extrair: texto limpo por seção, infobox como dict, links internos, links externos, categorias, referências (`<ref>`), templates, seções.
- **`chunker.py`** — Divide o texto de cada artigo em chunks de ~512 tokens com overlap de 50 tokens. Preserva metadados de seção em cada chunk.

### 3. Ingester (`ingester/`)

- **`db.py`** — Gerencia conexão PostgreSQL com pgvector. Cria schema, índices.
- **`embedder.py`** — Gera embeddings dos chunks via OpenAI `text-embedding-3-small` (ou modelo local via `sentence-transformers` — configurável). Faz upsert em lote.
- **`loader.py`** — Orquestra: para cada artigo parseado, persiste artigo + chunks + embeddings + imagens + referências.

### 4. API (`api/`)

FastAPI com os seguintes endpoints:

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| `GET` | `/articles` | Lista paginada de artigos |
| `GET` | `/articles/{slug}` | Artigo completo com todos os metadados |
| `GET` | `/articles/{slug}/images` | Imagens associadas ao artigo |
| `POST` | `/search` | Busca semântica: recebe query, retorna chunks relevantes |
| `GET` | `/categories/{name}` | Artigos de uma categoria |

### 5. CLI (`cli.py`)

Ponto de entrada único:

```bash
python cli.py fetch    # baixa XML + imagens
python cli.py parse    # parseia wikitext → JSON
python cli.py ingest   # carrega no PostgreSQL + gera embeddings
python cli.py serve    # sobe a API na porta 8000
```

---

## Schema do Banco de Dados

```sql
-- Artigos completos (todos os campos da MediaWiki API)
articles (
  id              SERIAL PRIMARY KEY,
  page_id         INTEGER UNIQUE NOT NULL,   -- pageid do MediaWiki
  slug            TEXT UNIQUE NOT NULL,      -- título normalizado
  title           TEXT NOT NULL,
  display_title   TEXT,                      -- displaytitle (pode diferir do slug)
  namespace       INTEGER DEFAULT 0,         -- ns (0=artigo, 10=template, etc.)
  content_model   TEXT,                      -- "wikitext", "json", etc.
  language        TEXT,                      -- pagelanguage
  wikitext        TEXT,                      -- fonte wikitext
  html            TEXT,                      -- HTML renderizado pela API
  summary         TEXT,                      -- primeiro parágrafo extraído
  sections        JSONB,    -- [{index, title, level, anchor, byteoffset}]
  categories      TEXT[],
  infobox         JSONB,    -- dados estruturados do infobox
  templates       TEXT[],   -- templates usados no artigo
  internal_links  TEXT[],   -- links para outros artigos do wiki
  external_links  TEXT[],   -- links externos
  iw_links        JSONB,    -- interwiki links [{prefix, title, url}]
  lang_links      JSONB,    -- links para outras línguas [{lang, title, url}]
  properties      JSONB,    -- pageprops (disambiguation, featured, etc.)
  protection      JSONB,    -- proteção de edição
  rev_id          INTEGER,  -- lastrevid
  length_bytes    INTEGER,  -- tamanho da página em bytes
  parse_warnings  TEXT[],   -- warnings retornados pela API
  touched_at      TIMESTAMPTZ,
  fetched_at      TIMESTAMPTZ
)

-- Referências (<ref> tags extraídas do wikitext)
references (
  id          SERIAL PRIMARY KEY,
  article_id  INTEGER REFERENCES articles(id),
  ref_name    TEXT,
  content     TEXT,
  url         TEXT,
  position    INTEGER
)

-- Chunks para RAG
chunks (
  id          SERIAL PRIMARY KEY,
  article_id  INTEGER REFERENCES articles(id),
  section     TEXT,
  content     TEXT,
  position    INTEGER,
  token_count INTEGER,
  embedding   VECTOR(1536),
  embed_model TEXT
)

-- Imagens (todos os campos do imageinfo)
images (
  id               SERIAL PRIMARY KEY,
  filename         TEXT UNIQUE NOT NULL,
  page_id          INTEGER,
  url              TEXT,
  description_url  TEXT,
  local_path       TEXT,
  mime_type        TEXT,
  size_bytes       INTEGER,
  width            INTEGER,
  height           INTEGER,
  sha1             TEXT,
  uploader         TEXT,
  upload_comment   TEXT,
  upload_timestamp TIMESTAMPTZ,
  downloaded       BOOLEAN DEFAULT FALSE,
  fetched_at       TIMESTAMPTZ
)

-- Relação artigo ↔ imagem
article_images (
  article_id  INTEGER REFERENCES articles(id),
  image_id    INTEGER REFERENCES images(id),
  PRIMARY KEY (article_id, image_id)
)

-- Estado de progresso
fetch_state (
  key        TEXT PRIMARY KEY,
  value      JSONB,
  updated_at TIMESTAMPTZ DEFAULT NOW()
)
```

**Índices:**
- `HNSW` em `chunks.embedding` (busca semântica eficiente)
- `GIN` em `articles.categories` e `articles.templates`
- `B-tree` em `page_id`, `slug`, `touched_at`

---

## Fluxo de Dados

```
python cli.py fetch
  ├── Busca lista de todos os artigos via api.php?action=query&list=allpages
  ├── Exporta XML em batches de 500 via Special:Export
  ├── Baixa todos os templates referenciados (namespace 10)
  ├── Enumera imagens via api.php?action=query&list=allimages
  └── Baixa arquivos de imagem para data/images/ (paralelo, 5 workers)

python cli.py parse
  ├── Lê articles.xml com mwparserfromhell
  ├── Para cada artigo: extrai todos os campos do schema
  ├── Chama api.php?action=parse para HTML + metadados adicionais
  └── Serializa resultado em data/parsed/ (um JSON por artigo)

python cli.py ingest
  ├── Lê cada JSON de data/parsed/
  ├── Faz upsert no banco (idempotente)
  ├── Chunkeriza o texto e gera embeddings em lote (batch de 100)
  └── Registra progresso em fetch_state a cada 50 artigos

python cli.py serve
  └── Sobe FastAPI na porta 8000
```

---

## Tratamento de Erros e Resumabilidade

- **Rate limiting:** backoff exponencial com jitter (1s → 2s → 4s → 8s, máx 60s) em toda chamada à API
- **Interrupções:** `fetch_state` registra o último artigo processado com sucesso; ao re-executar, continua de onde parou
- **Falhas de parsing:** artigos com erro são logados em `data/errors/` e pulados sem abortar a pipeline
- **Embeddings:** se falhar para um chunk, o artigo é persistido sem embedding e re-tentado em execução futura
- **Imagens:** download com retry 3x, depois marca `downloaded=FALSE` e segue

---

## Configuração (`config.yaml`)

```yaml
wiki_url: https://wiki.evageeks.org
batch_size: 500
image_workers: 5
chunk_size: 512        # tokens
chunk_overlap: 50
embed_model: text-embedding-3-small
database_url: postgresql://localhost/evageeks
rate_limit_delay: 0.5  # segundos entre requests
```

---

## Estratégia de Testes

### Testes unitários (`tests/unit/`)
- **Parser:** fixtures de wikitext real → valida extração de infobox, categorias, links, seções
- **Chunker:** valida tamanho dos chunks, overlap, preservação de metadados de seção
- **Schema:** valida que todos os campos da API são mapeados corretamente

### Testes de integração (`tests/integration/`)
- **Fetcher:** mock via `respx` contra a API real do EvaGeeks com subconjunto pequeno
- **Ingester:** PostgreSQL efêmero via `testcontainers-python`, valida upsert idempotente e busca por embedding
- **API:** testa todos os endpoints com banco populado com fixtures de 5 artigos

### Testes de smoke (`tests/smoke/`)
- Roda a pipeline completa em um artigo real e valida que os dados chegam na API com todos os campos preenchidos

### Ferramentas
- `pytest` + `pytest-asyncio`
- `testcontainers-python` para PostgreSQL efêmero
- `respx` para mock de chamadas HTTP

---

## Estrutura de Diretórios

```
evageeks/
├── cli.py
├── config.yaml
├── fetcher/
│   ├── export.py
│   ├── images.py
│   └── templates.py
├── parser/
│   ├── wikitext.py
│   └── chunker.py
├── ingester/
│   ├── db.py
│   ├── embedder.py
│   └── loader.py
├── api/
│   ├── main.py
│   └── routes/
│       ├── articles.py
│       ├── search.py
│       └── categories.py
├── data/
│   ├── raw/
│   ├── parsed/
│   ├── images/
│   └── errors/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── smoke/
│   └── fixtures/
├── docs/
│   └── superpowers/
│       └── specs/
│           └── 2026-04-16-evageeks-mirror-design.md
└── requirements.txt
```

---

## Dependências Principais

```
mwparserfromhell      # parsing de wikitext
requests              # HTTP client
psycopg2-binary       # PostgreSQL
pgvector              # extensão pgvector para Python
fastapi               # API REST
uvicorn               # ASGI server
openai                # embeddings (opcional)
sentence-transformers # embeddings local (opcional)
pyyaml                # configuração
pytest                # testes
testcontainers        # PostgreSQL efêmero nos testes
respx                 # mock HTTP
```
