import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional
import httpx
import yaml


def _load_config() -> dict:
    with open(Path(__file__).parent.parent / "config.yaml") as f:
        return yaml.safe_load(f)


def fetch_image_list(
    wiki_url: str,
    session: Optional[httpx.Client] = None,
    rate_limit: float = 0.5,
) -> list:
    """Return all images from the wiki via api.php allimages."""
    session = session or httpx.Client()
    images = []
    params = {
        "action": "query",
        "list": "allimages",
        "ailimit": "500",
        "aiprop": "timestamp|url|size|mime|sha1|user|comment",
        "format": "json",
    }
    while True:
        resp = session.get(f"{wiki_url}/api.php", params=params)
        resp.raise_for_status()
        data = resp.json()
        images.extend(data["query"]["allimages"])
        if "continue" not in data:
            break
        params.update(data["continue"])
        time.sleep(rate_limit)
    return images


def download_image(
    url: str,
    dest_path: str,
    session: Optional[httpx.Client] = None,
    retries: int = 3,
) -> bool:
    """Download a single image. Returns True on success."""
    session = session or httpx.Client()
    for attempt in range(retries):
        try:
            resp = session.get(url, follow_redirects=True, timeout=30.0)
            resp.raise_for_status()
            Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
            Path(dest_path).write_bytes(resp.content)
            return True
        except Exception:
            if attempt == retries - 1:
                return False
            time.sleep(2 ** attempt)
    return False


def run_image_fetch(wiki_url: str, images_dir: str, workers: int = 5) -> list:
    """
    Download all images to images_dir.
    Returns list of image dicts with local_path and downloaded fields.
    """
    config = _load_config()
    rate_limit = config.get("rate_limit_delay", 0.5)
    session = httpx.Client(timeout=30.0)
    images_path = Path(images_dir)
    images_path.mkdir(parents=True, exist_ok=True)

    print("Fetching image list...")
    images = fetch_image_list(wiki_url, session=session, rate_limit=rate_limit)
    print(f"Found {len(images)} images")

    def _download(img: dict) -> dict:
        filename = img["name"]
        dest = str(images_path / filename)
        success = download_image(img["url"], dest)
        return {**img, "local_path": dest if success else None, "downloaded": success}

    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_download, img): img for img in images}
        for i, future in enumerate(as_completed(futures)):
            result = future.result()
            results.append(result)
            if (i + 1) % 50 == 0:
                print(f"  Downloaded {i + 1}/{len(images)}")
    return results
