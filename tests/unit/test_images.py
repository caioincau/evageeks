import respx
import httpx
from fetcher.images import fetch_image_list, download_image

WIKI_URL = "https://wiki.evageeks.org"

ALLIMAGES_RESPONSE = {
    "query": {
        "allimages": [
            {
                "name": "Rei_Ayanami.jpg",
                "pageid": 100,
                "url": "https://wiki.evageeks.org/images/rei.jpg",
                "descriptionurl": "https://wiki.evageeks.org/File:Rei_Ayanami.jpg",
                "mime": "image/jpeg",
                "size": 45000,
                "width": 400,
                "height": 600,
                "sha1": "abc123",
                "user": "Editor1",
                "timestamp": "2020-01-01T00:00:00Z",
                "comment": "Initial upload",
            }
        ]
    }
}


@respx.mock
def test_fetch_image_list_returns_images():
    respx.get(f"{WIKI_URL}/api.php").mock(
        return_value=httpx.Response(200, json=ALLIMAGES_RESPONSE)
    )
    images = fetch_image_list(WIKI_URL, session=httpx.Client())
    assert len(images) == 1
    img = images[0]
    assert img["name"] == "Rei_Ayanami.jpg"
    assert img["mime"] == "image/jpeg"
    assert img["width"] == 400


@respx.mock
def test_download_image_saves_file(tmp_path):
    img_url = "https://wiki.evageeks.org/images/rei.jpg"
    img_bytes = b"\xff\xd8\xff"  # JPEG header
    respx.get(img_url).mock(return_value=httpx.Response(200, content=img_bytes))
    dest = tmp_path / "rei.jpg"
    download_image(img_url, str(dest), session=httpx.Client())
    assert dest.exists()
    assert dest.read_bytes() == img_bytes
