# ingester/loader.py
from pathlib import Path
from typing import Optional
from psycopg2.extensions import connection
from psycopg2.extras import Json
import numpy as np
import yaml
from pgvector.psycopg2 import register_vector
from ingester.embedder import generate_embeddings


def _load_config() -> dict:
    with open(Path(__file__).parent.parent / "config.yaml") as f:
        return yaml.safe_load(f)


def upsert_article(
    conn: connection,
    article: dict,
    chunks: list,
    embed_model: str = "text-embedding-3-small",
) -> int:
    """Insert or update an article and its chunks. Returns article id."""
    try:
        with conn.cursor() as cur:
            register_vector(conn)
            cur.execute("""
                INSERT INTO articles (
                    page_id, slug, title, display_title, namespace, content_model,
                    language, wikitext, html, summary, sections, categories,
                    infobox, templates, internal_links, external_links, iw_links,
                    lang_links, properties, protection, rev_id, length_bytes,
                    parse_warnings, touched_at, fetched_at,
                    is_redirect, is_stub
                ) VALUES (
                    %(page_id)s, %(slug)s, %(title)s, %(display_title)s,
                    %(namespace)s, %(content_model)s, %(language)s, %(wikitext)s,
                    %(html)s, %(summary)s, %(sections)s, %(categories)s,
                    %(infobox)s, %(templates)s, %(internal_links)s,
                    %(external_links)s, %(iw_links)s, %(lang_links)s,
                    %(properties)s, %(protection)s, %(rev_id)s, %(length_bytes)s,
                    %(parse_warnings)s, %(touched_at)s, NOW(),
                    %(is_redirect)s, %(is_stub)s
                )
                ON CONFLICT (page_id) DO UPDATE SET
                    slug = EXCLUDED.slug,
                    title = EXCLUDED.title,
                    display_title = EXCLUDED.display_title,
                    wikitext = EXCLUDED.wikitext,
                    html = EXCLUDED.html,
                    summary = EXCLUDED.summary,
                    sections = EXCLUDED.sections,
                    categories = EXCLUDED.categories,
                    infobox = EXCLUDED.infobox,
                    templates = EXCLUDED.templates,
                    internal_links = EXCLUDED.internal_links,
                    external_links = EXCLUDED.external_links,
                    rev_id = EXCLUDED.rev_id,
                    length_bytes = EXCLUDED.length_bytes,
                    touched_at = EXCLUDED.touched_at,
                    fetched_at = NOW(),
                    is_redirect = EXCLUDED.is_redirect,
                    is_stub = EXCLUDED.is_stub
                RETURNING id
            """, {
                "page_id": article.get("page_id"),
                "slug": article.get("slug"),
                "title": article.get("title"),
                "display_title": article.get("display_title"),
                "namespace": article.get("namespace", 0),
                "content_model": article.get("content_model"),
                "language": article.get("language"),
                "wikitext": article.get("wikitext"),
                "html": article.get("html"),
                "summary": article.get("summary"),
                "sections": Json(article.get("sections", [])),
                "categories": article.get("categories", []),
                "infobox": Json(article.get("infobox", {})),
                "templates": article.get("templates", []),
                "internal_links": article.get("internal_links", []),
                "external_links": article.get("external_links", []),
                "iw_links": Json(article.get("iw_links", [])),
                "lang_links": Json(article.get("lang_links", [])),
                "properties": Json(article.get("properties", {})),
                "protection": Json(article.get("protection", [])),
                "rev_id": article.get("rev_id"),
                "length_bytes": article.get("length_bytes"),
                "parse_warnings": article.get("parse_warnings", []),
                "touched_at": article.get("touched_at"),
                "is_redirect": (article.get("wikitext") or "").strip().upper().startswith("#REDIRECT"),
                "is_stub": len((article.get("wikitext") or "").strip()) < 100,
            })
            article_id = cur.fetchone()[0]

            # Upsert refs
            cur.execute("DELETE FROM article_refs WHERE article_id = %s", (article_id,))
            for ref in article.get("references", []):
                cur.execute("""
                    INSERT INTO article_refs (article_id, ref_name, content, url, position)
                    VALUES (%s, %s, %s, %s, %s)
                """, (article_id, ref.get("ref_name"), ref.get("content"),
                      ref.get("url"), ref.get("position")))

            # Delete old chunks and re-insert with new embeddings
            cur.execute("DELETE FROM chunks WHERE article_id = %s", (article_id,))
            if chunks:
                texts = [c["content"] for c in chunks]
                embeddings = generate_embeddings(texts, model=embed_model)
                for chunk, embedding in zip(chunks, embeddings):
                    cur.execute("""
                        INSERT INTO chunks (article_id, section, content, position, token_count, embedding, embed_model)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (
                        article_id, chunk["section"], chunk["content"],
                        chunk["position"], chunk["token_count"],
                        np.array(embedding), embed_model,
                    ))

        conn.commit()
        return article_id
    except Exception:
        conn.rollback()
        raise


def get_fetch_state(conn: connection, key: str) -> Optional[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM fetch_state WHERE key = %s", (key,))
        row = cur.fetchone()
    return row[0] if row else None


def set_fetch_state(conn: connection, key: str, value: dict) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO fetch_state (key, value, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        """, (key, Json(value)))
    conn.commit()
