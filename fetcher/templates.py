import time
from pathlib import Path
from typing import Optional
import httpx
import yaml


def _load_config() -> dict:
    with open(Path(__file__).parent.parent / "config.yaml") as f:
        return yaml.safe_load(f)


def fetch_templates(
    wiki_url: str,
    template_names: list,
    session: Optional[httpx.Client] = None,
    rate_limit: float = 0.5,
) -> dict:
    """Fetch wikitext for a list of templates. Returns {template_name: wikitext}."""
    session = session or httpx.Client()
    result = {}
    for i in range(0, len(template_names), 50):
        batch = template_names[i : i + 50]
        params = {
            "action": "query",
            "titles": "|".join(batch),
            "prop": "revisions",
            "rvprop": "content",
            "format": "json",
        }
        resp = session.get(f"{wiki_url}/api.php", params=params)
        resp.raise_for_status()
        data = resp.json()
        for page in data["query"]["pages"].values():
            if "revisions" in page:
                result[page["title"]] = page["revisions"][0].get("*", "")
        time.sleep(rate_limit)
    return result


def collect_template_names(parsed_articles: list) -> list:
    """Collect unique template names from parsed articles."""
    names = set()
    for article in parsed_articles:
        for t in article.get("templates", []):
            if not t.startswith("Template:"):
                names.add(f"Template:{t}")
            else:
                names.add(t)
    return sorted(names)
