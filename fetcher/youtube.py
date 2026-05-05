# fetcher/youtube.py
"""Extract transcripts from YouTube analysis videos via youtube_transcript_api.

Requires: pip install youtube-transcript-api
"""
import json
import re
import time
from pathlib import Path

from parser.chunker import chunk_article

PAGE_ID_BASE = 8_000_000

# Notable Evangelion analysis videos from popular anime channels.
# Replace placeholder IDs with real YouTube video IDs.
EVA_VIDEOS = [
    # Mother's Basement
    {"id": "PLACEHOLDER_MB_01", "title": "What's in an OP? - A Cruel Angel's Thesis", "channel": "Mother's Basement"},
    {"id": "PLACEHOLDER_MB_02", "title": "Evangelion is (Not) Overrated", "channel": "Mother's Basement"},
    {"id": "PLACEHOLDER_MB_03", "title": "End of Evangelion Explained", "channel": "Mother's Basement"},
    # Super Eyepatch Wolf
    {"id": "PLACEHOLDER_SEW_01", "title": "The Appeal of Neon Genesis Evangelion", "channel": "Super Eyepatch Wolf"},
    {"id": "PLACEHOLDER_SEW_02", "title": "Why You Should Watch Evangelion", "channel": "Super Eyepatch Wolf"},
    # Gigguk
    {"id": "PLACEHOLDER_GIG_01", "title": "Evangelion Explained in 12 Minutes", "channel": "Gigguk"},
    {"id": "PLACEHOLDER_GIG_02", "title": "Rebuilds of Evangelion - Complete Analysis", "channel": "Gigguk"},
    # Wisecrack
    {"id": "PLACEHOLDER_WC_01", "title": "The Philosophy of Evangelion", "channel": "Wisecrack"},
    {"id": "PLACEHOLDER_WC_02", "title": "Evangelion: The Human Instrumentality Project Explained", "channel": "Wisecrack"},
    # Like Stories of Old
    {"id": "PLACEHOLDER_LSO_01", "title": "Evangelion and the Hedgehog's Dilemma", "channel": "Like Stories of Old"},
    # Folding Ideas
    {"id": "PLACEHOLDER_FI_01", "title": "End of Evangelion and Rebuilding", "channel": "Folding Ideas"},
    # Under the Scope
    {"id": "PLACEHOLDER_UTS_01", "title": "Understanding Shinji Ikari", "channel": "Under the Scope"},
    {"id": "PLACEHOLDER_UTS_02", "title": "Understanding Asuka Langley Soryu", "channel": "Under the Scope"},
    # Aleczandxr
    {"id": "PLACEHOLDER_AZ_01", "title": "Misato Katsuragi - A Character Study", "channel": "Aleczandxr"},
    {"id": "PLACEHOLDER_AZ_02", "title": "Rei Ayanami - Who Is She Really?", "channel": "Aleczandxr"},
    # Hiding in Public
    {"id": "PLACEHOLDER_HIP_01", "title": "The Cinematography of Evangelion", "channel": "Hiding in Public"},
    # Kenny Lauderdale
    {"id": "PLACEHOLDER_KL_01", "title": "The Production History of Evangelion", "channel": "Kenny Lauderdale"},
    # Neon Genesis Retrospective (misc)
    {"id": "PLACEHOLDER_NGR_01", "title": "Evangelion 3.0+1.0 - The Perfect Ending", "channel": "Various"},
    {"id": "PLACEHOLDER_NGR_02", "title": "Every Angel in Evangelion Explained", "channel": "Various"},
]


def _get_transcript(video_id: str) -> list[dict]:
    """Fetch transcript segments for a single YouTube video.

    Returns a list of dicts with keys: text, start, duration.
    Raises ImportError if youtube_transcript_api is not installed.
    """
    from youtube_transcript_api import YouTubeTranscriptApi

    return YouTubeTranscriptApi.get_transcript(video_id)


def _segments_to_text(segments: list[dict]) -> str:
    """Combine transcript segments into a single readable text.

    Joins segment texts, inserting paragraph breaks roughly every 10 segments
    to improve readability.
    """
    lines: list[str] = []
    for i, seg in enumerate(segments):
        text = seg.get("text", "").strip()
        if not text:
            continue
        lines.append(text)
        # Insert a paragraph break periodically for readability
        if (i + 1) % 10 == 0:
            lines.append("")

    return "\n".join(lines).strip()


def fetch_channel_transcripts(
    channel_videos: list[dict],
    output_dir: str,
) -> list[dict]:
    """Fetch YouTube transcripts for a list of videos and save as JSON articles.

    Args:
        channel_videos: List of dicts, each with keys: id, title, channel.
        output_dir: Directory to write JSON article files.

    Returns:
        List of article dicts that were saved.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    articles: list[dict] = []

    for seq, video in enumerate(channel_videos):
        video_id = video["id"]
        title = video.get("title", f"YouTube Video {video_id}")
        channel = video.get("channel", "Unknown")
        slug = re.sub(r'[^a-zA-Z0-9_]', '_', f"{channel}_{title}")[:80]
        outfile = output_path / f"youtube_{slug}.json"

        # Skip existing (resumable)
        if outfile.exists():
            print(f"  Skipping '{title}' (already exists)")
            continue

        print(f"  Fetching transcript: {title} [{video_id}]...")
        try:
            segments = _get_transcript(video_id)
            full_text = _segments_to_text(segments)

            if len(full_text) < 100:
                print(f"    Skipping (too short: {len(full_text)} chars)")
                continue

            article = {
                "page_id": PAGE_ID_BASE + seq,
                "slug": f"youtube_{slug}",
                "title": title,
                "display_title": f"{title} ({channel})",
                "namespace": 0,
                "content_model": "youtube_transcript",
                "language": "en",
                "wikitext": full_text,
                "html": "",
                "summary": full_text[:500],
                "sections": [],
                "categories": ["YouTube Analysis"],
                "infobox": {
                    "video_id": video_id,
                    "channel": channel,
                    "source": f"https://www.youtube.com/watch?v={video_id}",
                },
                "templates": [],
                "internal_links": [],
                "external_links": [f"https://www.youtube.com/watch?v={video_id}"],
                "iw_links": [],
                "lang_links": [],
                "properties": {},
                "protection": [],
                "rev_id": None,
                "length_bytes": len(full_text),
                "parse_warnings": [],
                "touched_at": None,
                "references": [],
                "source_type": "youtube",
                "source_url": f"https://www.youtube.com/watch?v={video_id}",
                "authority": 40,
            }

            # Generate chunks
            chunks = chunk_article(article)
            article["chunks"] = chunks

            outfile.write_text(json.dumps(article, ensure_ascii=False, default=str))
            articles.append(article)
            print(f"    Saved ({len(full_text)} chars, {len(chunks)} chunks)")

        except ImportError:
            print(
                "    Error: youtube_transcript_api not installed. "
                "Run: pip install youtube-transcript-api"
            )
            return articles
        except Exception as e:
            print(f"    Error: {e}")

    print(f"Total: {len(articles)} YouTube transcripts saved to {output_path}")
    return articles


def run_youtube_fetch(output_dir: str) -> list[dict]:
    """Entry point: fetch transcripts for all known Eva analysis videos.

    Args:
        output_dir: Directory to write JSON article files.

    Returns:
        List of article dicts that were saved.
    """
    return fetch_channel_transcripts(
        channel_videos=EVA_VIDEOS,
        output_dir=output_dir,
    )
