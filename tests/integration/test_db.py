# tests/integration/test_db.py
import psycopg2
from testcontainers.postgres import PostgresContainer
from ingester.db import create_schema

def test_create_schema_creates_all_tables():
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        conn = psycopg2.connect(pg.get_connection_url(driver=None))
        create_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                ORDER BY table_name
            """)
            tables = {row[0] for row in cur.fetchall()}
        conn.close()
    assert tables == {
        "articles", "article_refs", "chunks", "images",
        "article_images", "fetch_state"
    }


def test_create_schema_creates_hnsw_index():
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        conn = psycopg2.connect(pg.get_connection_url(driver=None))
        create_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT indexname FROM pg_indexes
                WHERE tablename = 'chunks' AND indexname = 'idx_chunks_embedding'
            """)
            row = cur.fetchone()
        conn.close()
    assert row is not None
