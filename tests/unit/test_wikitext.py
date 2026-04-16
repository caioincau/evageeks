# tests/unit/test_wikitext.py
import json
from pathlib import Path
from parser.wikitext import parse_article

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load_fixtures():
    wikitext = (FIXTURES / "rei_ayanami.xml").read_text()
    api_data = json.loads((FIXTURES / "rei_ayanami_api.json").read_text())
    return wikitext, api_data


def test_parse_article_basic_fields():
    wikitext, api_data = _load_fixtures()
    result = parse_article(wikitext, api_data)
    assert result["page_id"] == 42
    assert result["slug"] == "Rei_Ayanami"
    assert result["title"] == "Rei Ayanami"
    assert result["namespace"] == 0
    assert result["rev_id"] == 1001


def test_parse_article_extracts_categories():
    wikitext, api_data = _load_fixtures()
    result = parse_article(wikitext, api_data)
    assert "Characters" in result["categories"]
    assert "Eva Pilots" in result["categories"]


def test_parse_article_extracts_infobox():
    wikitext, api_data = _load_fixtures()
    result = parse_article(wikitext, api_data)
    assert result["infobox"]["name"] == "Rei Ayanami"
    assert result["infobox"]["age"] == "14"


def test_parse_article_extracts_internal_links():
    wikitext, api_data = _load_fixtures()
    result = parse_article(wikitext, api_data)
    assert "Neon Genesis Evangelion" in result["internal_links"]
    assert "Evangelion Unit-00" in result["internal_links"]


def test_parse_article_extracts_references():
    wikitext, api_data = _load_fixtures()
    result = parse_article(wikitext, api_data)
    refs = result["references"]
    assert len(refs) == 1
    assert refs[0]["ref_name"] == "anno"
    assert "Anno interview" in refs[0]["content"]


def test_parse_article_extracts_sections():
    wikitext, api_data = _load_fixtures()
    result = parse_article(wikitext, api_data)
    assert result["sections"][0]["line"] == "Background"
    assert result["sections"][0]["anchor"] == "Background"


def test_parse_article_extracts_summary():
    wikitext, api_data = _load_fixtures()
    result = parse_article(wikitext, api_data)
    assert result["summary"] is not None
    assert len(result["summary"]) > 0
