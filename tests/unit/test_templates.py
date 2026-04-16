import respx
import httpx
from fetcher.templates import fetch_templates

WIKI_URL = "https://wiki.evageeks.org"

TEMPLATES_RESPONSE = {
    "query": {
        "pages": {
            "301": {
                "pageid": 301,
                "ns": 10,
                "title": "Template:Infobox character",
                "revisions": [{"*": "{{Infobox|name={{{name|}}}|age={{{age|}}}}}"}]
            }
        }
    }
}


@respx.mock
def test_fetch_templates_returns_wikitext():
    respx.get(f"{WIKI_URL}/api.php").mock(
        return_value=httpx.Response(200, json=TEMPLATES_RESPONSE)
    )
    templates = fetch_templates(
        WIKI_URL,
        ["Template:Infobox character"],
        session=httpx.Client(),
    )
    assert "Template:Infobox character" in templates
    assert "Infobox" in templates["Template:Infobox character"]
