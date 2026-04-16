# tests/conftest.py
import os
import pytest
import psycopg2
from testcontainers.postgres import PostgresContainer
from ingester.db import create_schema

# Ensure testcontainers can reach Docker when using colima
if not os.environ.get("DOCKER_HOST"):
    colima_sock = os.path.expanduser("~/.colima/default/docker.sock")
    if os.path.exists(colima_sock):
        os.environ["DOCKER_HOST"] = f"unix://{colima_sock}"


@pytest.fixture(scope="session")
def pg_container():
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        yield pg


@pytest.fixture
def db_conn(pg_container):
    conn = psycopg2.connect(pg_container.get_connection_url(driver=None))
    create_schema(conn)
    yield conn
    with conn.cursor() as cur:
        cur.execute("""
            TRUNCATE articles, article_refs, chunks, images,
                     article_images, fetch_state RESTART IDENTITY CASCADE
        """)
    conn.commit()
    conn.close()
