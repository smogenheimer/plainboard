"""A doc site's own pages for its footer and its theme (#121)."""

from test_docs import page, site
from test_permissions import sign_in

from conftest import ADMIN_PASSWORD


def test_pages_of_its_own_are_in_the_footer_per_language_and_out_of_the_search(client, project):
    pid = project["id"]
    s = site(client, pid, published=True, public=True, languages=["de"])
    page(client, pid, s["id"], title="Welcome", body="Start here.")
    (picture,) = client.post(f"/api/projects/{pid}/docs/sites/{s['id']}/files", files=[("files", ("seal.png", b"\x89PNG\r\n\x1a\nxx", "image/png"))]).json()
    url = f"/api/projects/{pid}/docs/sites/{s['id']}/custom-pages"
    imprint = client.post(url, json={"title": "Imprint", "body": f"Shop Ltd, 1 Main St. Unicorn clause.\n\n![seal](doc-file:{picture['id']})", "texts": {"de": {"title": "Impressum", "body": "Shop GmbH"}, "fr": {"title": "x", "body": "y"}}})
    assert imprint.status_code == 201, imprint.text
    imprint = imprint.json()
    assert imprint["slug"] == "imprint" and imprint["path"] == "p/imprint" and list(imprint["texts"]) == ["de"]  # only the site's languages
    privacy = client.post(url, json={"title": "Privacy", "body": "We keep nothing."}).json()
    assert client.post(url, json={"title": "Imprint"}).json()["slug"] == "imprint-2"
    assert client.patch(f"/api/projects/{pid}/docs/custom-pages/{privacy['id']}", json={"slug": "imprint"}).status_code == 409
    moved = client.patch(f"/api/projects/{pid}/docs/custom-pages/{privacy['id']}", json={"position": 0}).json()
    assert moved["position"] == 0 and [p["title"] for p in client.get(url).json()] == ["Privacy", "Imprint", "Imprint"]
    third = client.get(url).json()[2]
    assert client.delete(f"/api/projects/{pid}/docs/custom-pages/{third['id']}").status_code == 204
    client.cookies.clear()

    view = client.get("/api/public/docs/handbook").json()
    assert view["footer"] == [
        {"slug": "privacy", "title": "Privacy", "titles": {"en": "Privacy", "de": "Privacy"}, "path": "p/privacy"},
        {"slug": "imprint", "title": "Imprint", "titles": {"en": "Imprint", "de": "Impressum"}, "path": "p/imprint"},
    ]
    assert view["theme"] is None  # no theme: the site as every site looks
    assert [f["title"] for f in client.get("/api/public/docs/handbook", params={"lang": "de"}).json()["footer"]] == ["Privacy", "Impressum"]
    got = client.get("/api/public/docs/handbook/custom/imprint").json()
    assert got["title"] == "Imprint" and f'src="/api/public/docs/handbook/files/{picture["id"]}"' in got["html"] and not got["fallback"]
    assert client.get("/api/public/docs/handbook/custom/imprint", params={"lang": "de"}).json()["html"] == "<p>Shop GmbH</p>\n"
    assert client.get("/api/public/docs/handbook/custom/privacy", params={"lang": "de"}).json()["fallback"]  # no German text: the original
    assert client.get("/api/public/docs/handbook/custom/nothing").status_code == 404

    html = client.get("/docs/handbook/de/p/imprint")
    assert html.status_code == 200 and "<title>Impressum – Handbook</title>" in html.text and "Shop GmbH" in html.text
    assert '"custom": {"id"' in html.text and 'href="/docs/handbook/p/privacy"' in client.get("/docs/handbook/").text  # the footer, for readers without the script
    assert "pb-docs-theme" not in html.text and "pb-docs-css" not in html.text
    assert client.get("/api/public/docs/handbook/search", params={"q": "unicorn"}).json() == []  # not in the docs search
    sign_in(client, "admin", ADMIN_PASSWORD)


def test_a_theme_styles_the_served_site_only_and_is_checked(client, project):
    pid = project["id"]
    s = site(client, pid, published=True, public=True)
    page(client, pid, s["id"], title="Welcome", body="Start here.")
    files = client.post(
        f"/api/projects/{pid}/docs/sites/{s['id']}/files", files=[("files", ("logo.png", b"\x89PNG\r\n\x1a\nxx", "image/png")), ("files", ("notes.txt", b"hello", "text/plain"))]
    ).json()
    logo, text = files[0]["id"], files[1]["id"]
    url = f"/api/projects/{pid}/docs/sites/{s['id']}"
    assert client.get(url).json()["theme"]["light"] == {"accent": "", "page": "", "surface": "", "text": ""}

    assert client.patch(url, json={"theme": {"logo_light": text}}).status_code == 400  # not a picture
    assert client.patch(url, json={"theme": {"links": [{"label": "Home", "url": "javascript:alert(1)"}]}}).status_code == 400
    assert client.patch(url, json={"theme": {"font_url": "http://fonts.example.com/a.css"}}).status_code == 400
    assert client.patch(url, json={"theme": {"light": {"accent": "red;}"}}}).status_code == 422
    theme = {
        "logo_light": logo, "favicon": logo, "show_name": False, "light": {"accent": "#0a7d5a", "page": "#fffdf7"}, "dark": {"text": "#eeeeee"},
        "font_family": "Source Sans 3, sans-serif", "font_url": "https://fonts.example.com/css?family=Source+Sans+3", "radius": 4,
        "links": [{"label": "Website", "url": "https://shop.example.com"}], "css": ".doc-header{border:0}</style><script>alert(1)</script>",
    }  # fmt: skip
    saved = client.patch(url, json={"theme": theme})
    assert saved.status_code == 200, saved.text
    assert saved.json()["theme"]["light"]["accent"] == "#0a7d5a" and saved.json()["theme"]["links"] == [{"label": "Website", "url": "https://shop.example.com"}]
    preview = client.post(f"{url}/theme/preview", json={"light": {"accent": "#123456"}}).json()["css"]
    assert preview.startswith('.doc-tp[data-design][data-theme="slop2gether-light"]{--accent:#123456;')

    client.cookies.clear()
    view = client.get("/api/public/docs/handbook").json()
    assert view["theme"] == {"logo": f"/api/public/docs/handbook/files/{logo}", "logo_dark": f"/api/public/docs/handbook/files/{logo}", "show_name": False, "links": theme["links"]}
    html = client.get("/docs/handbook/").text
    assert '--accent:#0a7d5a' in html and '--font-sans:"Source Sans 3",sans-serif,system-ui,sans-serif' in html and "--doc-rf:0.2857" in html
    assert '<link rel="stylesheet" href="https://fonts.example.com/css?family=Source+Sans+3">' in html
    assert f'<link rel="icon" href="/api/public/docs/handbook/files/{logo}">' in html and html.count('rel="icon"') == 1
    assert "</style><script>alert(1)" not in html and ".doc-header{border:0}\\3c /style>" in html  # the custom CSS cannot end its style
    sign_in(client, "admin", ADMIN_PASSWORD)

    assert client.patch(url, json={"theme": {}}).json()["theme"]["logo_light"] is None  # back to the site's own look
    client.cookies.clear()
    assert "pb-docs-theme" not in client.get("/docs/handbook/").text
    sign_in(client, "admin", ADMIN_PASSWORD)
