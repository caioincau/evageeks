# ingester/db.py
import psycopg2
from psycopg2.extensions import connection
import yaml
from pathlib import Path


def load_config(path: str = None) -> dict:
    cfg_path = path or Path(__file__).parent.parent / "config.yaml"
    with open(cfg_path) as f:
        return yaml.safe_load(f)


def get_connection(database_url: str = None) -> connection:
    config = load_config()
    conn = psycopg2.connect(database_url or config["database_url"])
    return conn


def create_schema(conn: connection) -> None:
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id              SERIAL PRIMARY KEY,
                page_id         INTEGER UNIQUE NOT NULL,
                slug            TEXT UNIQUE NOT NULL,
                title           TEXT NOT NULL,
                display_title   TEXT,
                namespace       INTEGER DEFAULT 0,
                content_model   TEXT,
                language        TEXT,
                wikitext        TEXT,
                html            TEXT,
                summary         TEXT,
                sections        JSONB,
                categories      TEXT[],
                infobox         JSONB,
                templates       TEXT[],
                internal_links  TEXT[],
                external_links  TEXT[],
                iw_links        JSONB,
                lang_links      JSONB,
                properties      JSONB,
                protection      JSONB,
                rev_id          INTEGER,
                length_bytes    INTEGER,
                parse_warnings  TEXT[],
                touched_at      TIMESTAMPTZ,
                fetched_at      TIMESTAMPTZ
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS article_refs (
                id          SERIAL PRIMARY KEY,
                article_id  INTEGER REFERENCES articles(id) ON DELETE CASCADE,
                ref_name    TEXT,
                content     TEXT,
                url         TEXT,
                position    INTEGER
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id          SERIAL PRIMARY KEY,
                article_id  INTEGER REFERENCES articles(id) ON DELETE CASCADE,
                section     TEXT,
                content     TEXT,
                position    INTEGER,
                token_count INTEGER,
                embedding   VECTOR(1536),
                embed_model TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS images (
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
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS article_images (
                article_id  INTEGER REFERENCES articles(id) ON DELETE CASCADE,
                image_id    INTEGER REFERENCES images(id) ON DELETE CASCADE,
                PRIMARY KEY (article_id, image_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS fetch_state (
                key        TEXT PRIMARY KEY,
                value      JSONB,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_articles_categories
            ON articles USING gin (categories)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_articles_templates
            ON articles USING gin (templates)
        """)
    conn.commit()
