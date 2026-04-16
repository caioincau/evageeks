# tests/unit/test_export.py
import respx
import httpx
from fetcher.export import fetch_page_list, fetch_xml_batch

WIKI_URL = "https://wiki.evageeks.org"

ALLPAGES_RESPONSE = {
    "query": {
        "allpages": [
            {"pageid": 1, "ns": 0, "title": "Rei Ayanami"},
            {"pageid": 2, "ns": 0, "title": "Shinji Ikari"},
        ]
    }
}

ALLPAGES_CONTINUED = {
    "continue": {"apcontinue": "Shinji_Ikari", "continue": "-||"},
    "query": {
        "allpages": [
            {"pageid": 1, "ns": 0, "title": "Rei Ayanami"},
        ]
    }
}


@respx.mock
def test_fetch_page_list_returns_pages():
    respx.get(f"{WIKI_URL}/api.php").mock(
        return_value=httpx.Response(200, json=ALLPAGES_RESPONSE)
    )
    pages = fetch_page_list(WIKI_URL, namespace=0, session=httpx.Client())
    assert len(pages) == 2
    assert pages[0]["title"] == "Rei Ayanami"


@respx.mock
def test_fetch_page_list_handles_pagination():
    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(200, json=ALLPAGES_CONTINUED)
        return httpx.Response(200, json=ALLPAGES_RESPONSE)

    respx.get(f"{WIKI_URL}/api.php").mock(side_effect=side_effect)
    pages = fetch_page_list(WIKI_URL, namespace=0, session=httpx.Client())
    assert call_count == 2


@respx.mock
def test_fetch_xml_batch_returns_xml():
    xml_content = b"<mediawiki><page><title>Rei Ayanami</title></page></mediawiki>"
    respx.post(f"{WIKI_URL}/index.php").mock(
        return_value=httpx.Response(200, content=xml_content)
    )
    result = fetch_xml_batch(
        WIKI_URL,
        titles=["Rei Ayanami"],
        session=httpx.Client(),
    )
    assert b"<mediawiki>" in result
