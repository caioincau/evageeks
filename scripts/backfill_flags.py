#!/usr/bin/env python3
"""One-shot migration: flag redirects/stubs and delete their chunks."""
import sys
sys.path.insert(0, ".")

from ingester.db import get_connection


def main():
    conn = get_connection()
    with conn.cursor() as cur:
        # Add columns if they don't exist
        cur.execute("""
            ALTER TABLE articles ADD COLUMN IF NOT EXISTS is_redirect BOOLEAN DEFAULT FALSE;
            ALTER TABLE articles ADD COLUMN IF NOT EXISTS is_stub BOOLEAN DEFAULT FALSE;
        """)

        # Flag redirects
        cur.execute("""
            UPDATE articles SET is_redirect = TRUE
            WHERE UPPER(TRIM(wikitext)) LIKE '#REDIRECT%'
        """)
        print(f"Flagged {cur.rowcount} redirects")

        # Flag stubs (< 100 chars of wikitext, excluding redirects)
        cur.execute("""
            UPDATE articles SET is_stub = TRUE
            WHERE LENGTH(TRIM(COALESCE(wikitext, ''))) < 100
            AND is_redirect IS NOT TRUE
        """)
        print(f"Flagged {cur.rowcount} stubs")

        # Delete chunks from redirects and stubs
        cur.execute("""
            DELETE FROM chunks WHERE article_id IN (
                SELECT id FROM articles WHERE is_redirect = TRUE OR is_stub = TRUE
            )
        """)
        print(f"Deleted {cur.rowcount} useless chunks")

        # Stats
        cur.execute("SELECT COUNT(*) FROM articles WHERE is_redirect = TRUE")
        redirects = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM articles WHERE is_stub = TRUE")
        stubs = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM articles WHERE is_redirect IS NOT TRUE AND is_stub IS NOT TRUE")
        good = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM chunks")
        chunks = cur.fetchone()[0]

    conn.commit()
    conn.close()
    print(f"\nSummary: {redirects} redirects, {stubs} stubs, {good} good articles, {chunks} chunks remaining")


if __name__ == "__main__":
    main()
