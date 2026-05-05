#!/usr/bin/env python3
# cli.py
"""EvaGeeks Wiki Mirror CLI"""
import argparse
from pathlib import Path
from ingester.db import load_config, get_connection, create_schema


def cmd_fetch(args):
    from fetcher.export import run_fetch
    from fetcher.images import run_image_fetch
    config = load_config()
    wiki_url = config["wiki_url"]
    data_dir = config["data_dir"]
    print("=== Fetching articles ===")
    run_fetch(wiki_url, f"{data_dir}/raw", batch_size=config["batch_size"])
    print("=== Fetching images ===")
    run_image_fetch(wiki_url, f"{data_dir}/images", workers=config["image_workers"])


def cmd_parse(args):
    import json
    import xml.etree.ElementTree as ET
    import httpx
    from parser.wikitext import parse_article
    from parser.chunker import chunk_article
    config = load_config()
    data_dir = config["data_dir"]
    wiki_url = config["wiki_url"]
    xml_path = Path(f"{data_dir}/raw/articles.xml")
    parsed_dir = Path(f"{data_dir}/parsed")
    parsed_dir.mkdir(exist_ok=True)
    errors_dir = Path(f"{data_dir}/errors")
    errors_dir.mkdir(exist_ok=True)

    session = httpx.Client(timeout=30.0)
    NS = "http://www.mediawiki.org/xml/export-0.11/"
    tree = ET.parse(xml_path)
    root = tree.getroot()
    pages = root.findall(f".//{{{NS}}}page")

    print(f"Parsing {len(pages)} articles...")
    for i, page in enumerate(pages):
        title_el = page.find(f"{{{NS}}}title")
        title = title_el.text if title_el is not None else ""
        text_el = page.find(f".//{{{NS}}}text")
        wikitext = text_el.text or "" if text_el is not None else ""

        try:
            resp = session.get(f"{wiki_url}/api.php", params={
                "action": "parse", "page": title,
                "prop": "text|sections|iwlinks|langlinks|properties|revid|displaytitle",
                "format": "json",
            })
            resp.raise_for_status()
            api_data = resp.json().get("parse", {})
            parsed = parse_article(wikitext, api_data)
            chunks = chunk_article(parsed, config["chunk_size"], config["chunk_overlap"])
            output = {**parsed, "chunks": chunks}
            slug = parsed.get("slug") or title.replace(" ", "_")
            slug = slug.replace("/", "_")
            (parsed_dir / f"{slug}.json").write_text(
                json.dumps(output, ensure_ascii=False, default=str)
            )
        except Exception as e:
            safe_title = title.replace("/", "_").replace("\\", "_")
            (errors_dir / f"{safe_title}.txt").write_text(str(e))
            print(f"  Error parsing {title}: {e}")

        if (i + 1) % 100 == 0:
            print(f"  Parsed {i + 1}/{len(pages)}")

    print("Parse complete.")


def cmd_ingest(args):
    import json
    from ingester.loader import upsert_article, set_fetch_state, get_fetch_state
    config = load_config()
    data_dir = config["data_dir"]
    parsed_dir = Path(f"{data_dir}/parsed")

    conn = get_connection()
    create_schema(conn)

    files = sorted(parsed_dir.glob("*.json"))
    print(f"Ingesting {len(files)} articles...")
    resume_from = get_fetch_state(conn, "last_ingested")
    start_slug = resume_from["slug"] if resume_from else None
    started = start_slug is None

    for i, f in enumerate(files):
        slug = f.stem
        if not started:
            if slug == start_slug:
                started = True
            else:
                continue
        try:
            data = json.loads(f.read_text())
            chunks = data.pop("chunks", [])
            upsert_article(conn, data, chunks, embed_model=config["embed_model"])
            if (i + 1) % 50 == 0:
                set_fetch_state(conn, "last_ingested", {"slug": slug, "index": i})
                print(f"  Ingested {i + 1}/{len(files)}")
        except Exception as e:
            error_file = Path(f"{data_dir}/errors/{slug}.txt")
            error_file.parent.mkdir(exist_ok=True)
            error_file.write_text(str(e))
            print(f"  Error ingesting {slug}: {e}")

    if files:
        set_fetch_state(conn, "last_ingested", {"slug": files[-1].stem, "index": len(files) - 1})

    conn.close()
    print("Ingest complete.")


def cmd_fetch_forum(args):
    from fetcher.forum import run_forum_fetch
    config = load_config()
    data_dir = config["data_dir"]
    print("=== Fetching forum threads ===")
    run_forum_fetch(f"{data_dir}/parsed", rate_limit=config.get("rate_limit_delay", 1.0))


def cmd_fetch_interviews(args):
    from fetcher.interviews import run_interview_fetch
    config = load_config()
    data_dir = config["data_dir"]
    print("=== Fetching external interviews ===")
    run_interview_fetch(f"{data_dir}/parsed", rate_limit=config.get("rate_limit_delay", 1.0))


def cmd_fetch_transcripts(args):
    from fetcher.transcripts import run_transcript_fetch
    config = load_config()
    data_dir = config["data_dir"]
    print("=== Fetching episode transcripts ===")
    run_transcript_fetch(f"{data_dir}/parsed")


def cmd_fetch_youtube(args):
    from fetcher.youtube import run_youtube_fetch
    config = load_config()
    data_dir = config["data_dir"]
    print("=== Fetching YouTube analysis transcripts ===")
    run_youtube_fetch(f"{data_dir}/parsed")


def cmd_fetch_tvtropes(args):
    from fetcher.tvtropes import run_tvtropes_fetch
    config = load_config()
    data_dir = config["data_dir"]
    print("=== Fetching TV Tropes pages ===")
    run_tvtropes_fetch(f"{data_dir}/parsed")


def cmd_fetch_anilist(args):
    from fetcher.anilist import run_anilist_fetch
    config = load_config()
    data_dir = config["data_dir"]
    print("=== Fetching AniList metadata ===")
    run_anilist_fetch(f"{data_dir}/parsed")


def cmd_fetch_wikidata(args):
    from fetcher.wikidata import run_wikidata_fetch
    config = load_config()
    data_dir = config["data_dir"]
    print("=== Fetching Wikidata relationships ===")
    run_wikidata_fetch(f"{data_dir}/parsed")


def cmd_serve(args):
    import uvicorn
    from api.main import create_app
    conn = get_connection()
    create_schema(conn)
    app = create_app(conn)
    uvicorn.run(app, host="0.0.0.0", port=8000)


def main():
    parser = argparse.ArgumentParser(
        description="EvaGeeks Wiki Mirror",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("fetch", help="Download articles XML and images from wiki")
    subparsers.add_parser("parse", help="Parse wikitext XML into structured JSON")
    subparsers.add_parser("ingest", help="Load parsed articles into PostgreSQL with embeddings")
    subparsers.add_parser("serve", help="Start the REST API on port 8000")
    subparsers.add_parser("fetch-forum", help="Scrape EvaGeeks forum threads")
    subparsers.add_parser("fetch-interviews", help="Fetch external interviews")
    subparsers.add_parser("fetch-transcripts", help="Fetch episode transcripts")
    subparsers.add_parser("fetch-youtube", help="Fetch YouTube analysis transcripts")
    subparsers.add_parser("fetch-tvtropes", help="Fetch TV Tropes pages")
    subparsers.add_parser("fetch-anilist", help="Fetch AniList metadata")
    subparsers.add_parser("fetch-wikidata", help="Fetch Wikidata relationships")

    args = parser.parse_args()
    commands = {
        "fetch": cmd_fetch,
        "parse": cmd_parse,
        "ingest": cmd_ingest,
        "serve": cmd_serve,
        "fetch-forum": cmd_fetch_forum,
        "fetch-interviews": cmd_fetch_interviews,
        "fetch-transcripts": cmd_fetch_transcripts,
        "fetch-youtube": cmd_fetch_youtube,
        "fetch-tvtropes": cmd_fetch_tvtropes,
        "fetch-anilist": cmd_fetch_anilist,
        "fetch-wikidata": cmd_fetch_wikidata,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
