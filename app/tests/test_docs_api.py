"""A doc site's API with its own keys (#131, services/docs_api.py)."""

import io
import json
import zipfile
from datetime import UTC, datetime, timedelta

from conftest import ADMIN_PASSWORD, idle, wait_for
from test_docs import db, page, site
from test_flow import chat, manager_of, tool_results
from test_permissions import as_friend, friend, sign_in


def bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def make_key(client, pid: int, sid: int, **body) -> dict:
    resp = client.post(f"/api/projects/{pid}/docs/sites/{sid}/api-keys", json={"name": "Website", **body})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_a_key_reads_its_private_site_and_nothing_else_until_revoked_or_expired(client, project):
    from plainboard.models import DocApiKey, utcnow
    from sqlalchemy import update

    pid = project["id"]
    s = site(client, pid, published=True, languages=["de"])  # private
    sid = s["id"]
    (picture,) = client.post(f"/api/projects/{pid}/docs/sites/{sid}/files", files=[("files", ("arch.png", b"\x89PNG\r\n\x1a\nxx", "image/png"))]).json()
    intro = page(client, pid, sid, title="Intro", body=f"Welcome. ![arch](doc-file:{picture['id']})")
    setup = page(client, pid, sid, title="Setup", parent_id=intro["id"], body=f"Back to [intro](doc-page:{intro['id']}).")
    assert client.put(f"/api/projects/{pid}/docs/pages/{setup['id']}/translations/de", json={"title": "Einrichtung", "body": "Installieren."}).status_code == 200
    client.post(f"/api/projects/{pid}/docs/sites/{sid}/custom-pages", json={"title": "Imprint", "body": "Who we are."})
    other = site(client, pid, name="Other", published=True, public=True)

    key = make_key(client, pid, sid)
    secret = key["key"]
    assert secret.startswith("pbd_") and key["hint"] == secret[-4:] and key["created_by"]
    listed = client.get(f"/api/projects/{pid}/docs/sites/{sid}/api-keys").json()
    assert [k["name"] for k in listed] == ["Website"] and "key" not in listed[0] and listed[0]["last_used_at"] is None

    client.cookies.clear()
    api = "/api/v1/docs/handbook"
    assert client.get(api).status_code == 401
    assert client.get(api, headers=bearer("pbd_nonsense")).status_code == 401
    info = client.get(api, headers=bearer(secret)).json()
    assert info["slug"] == "handbook" and not info["public"] and info["languages"] == ["en", "de"] and not info["versioned"] and info["pages"] == 2
    tree = client.get(f"{api}/pages", headers=bearer(secret)).json()
    assert [(p["id"], p["parent_id"], p["path"]) for p in tree["items"]] == [(intro["id"], None, "intro"), (setup["id"], intro["id"], "intro/setup")]
    assert tree["next_cursor"] is None
    one = client.get(f"{api}/pages/{intro['id']}", headers=bearer(secret)).json()
    assert one["markdown"].startswith("Welcome.") and one["updated_at"].endswith("Z")
    assert [(f["id"], f["name"]) for f in one["files"]] == [(picture["id"], "arch.png")] and one["files"][0]["url"].endswith(f"{api}/files/{picture['id']}")
    linked = client.get(f"{api}/page", params={"path": "intro/setup"}, headers=bearer(secret)).json()
    assert linked["links"] == {str(intro["id"]): "intro"} and not linked["fallback"]
    german = client.get(f"{api}/pages", params={"lang": "de"}, headers=bearer(secret)).json()["items"]
    assert [(p["title"], p["fallback"]) for p in german] == [("Intro", True), ("Einrichtung", False)]
    assert client.get(f"{api}/page", params={"path": "intro/setup", "lang": "de"}, headers=bearer(secret)).json()["markdown"] == "Installieren."
    assert client.get(f"{api}/pages", params={"lang": "fr"}, headers=bearer(secret)).status_code == 404
    assert client.get(f"{api}/pages", params={"version": "1.0"}, headers=bearer(secret)).status_code == 404  # no versions on this site
    assert [f["title"] for f in client.get(f"{api}/footer", headers=bearer(secret)).json()] == ["Imprint"]
    download = client.get(f"{api}/files/{picture['id']}", headers=bearer(secret))
    assert download.status_code == 200 and download.content.startswith(b"\x89PNG")

    # Another site, also a public one, and another site's files: not with this key.
    assert client.get("/api/v1/docs/other", headers=bearer(secret)).status_code == 404
    assert client.get("/api/v1/docs/nowhere", headers=bearer(secret)).status_code == 404
    sign_in(client, "admin", ADMIN_PASSWORD)
    (elsewhere,) = client.post(f"/api/projects/{pid}/docs/sites/{other['id']}/files", files=[("files", ("x.txt", b"x", "text/plain"))]).json()
    assert client.get(f"{api}/files/{elsewhere['id']}", headers=bearer(secret)).status_code == 404
    assert client.get(f"/api/projects/{pid}/docs/sites/{sid}/api-keys").json()[0]["last_used_at"]

    # Viewers neither see nor make keys; a person's own token reads the site when the person has a role in its project.
    olga = friend(client, "olga")
    friend(client, "stranger")
    client.put(f"/api/projects/{pid}/members/{olga['id']}", json={"role": "viewer"})
    as_friend(client, "olga")
    assert client.get(f"/api/projects/{pid}/docs/sites/{sid}/api-keys").status_code == 403
    assert client.post(f"/api/projects/{pid}/docs/sites/{sid}/api-keys", json={"name": "Mine"}).status_code == 403
    token = client.post("/api/tokens", json={"label": "Script", "read_only": True}).json()["token"]
    as_friend(client, "stranger")
    stranger = client.post("/api/tokens", json={"label": "Script"}).json()["token"]
    client.cookies.clear()
    assert client.get(f"{api}/pages", headers=bearer(token)).json()["items"][0]["title"] == "Intro"
    assert client.get(api, headers=bearer(stranger)).status_code == 404

    # Expired, revoked: refused.
    sign_in(client, "admin", ADMIN_PASSWORD)
    later = make_key(client, pid, sid, name="Short", expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat())
    assert later["expires_at"]

    async def expire(session):
        await session.execute(update(DocApiKey).where(DocApiKey.id == later["id"]).values(expires_at=utcnow() - timedelta(minutes=1)))
        await session.commit()

    db(client, expire)
    assert client.get(api, headers=bearer(later["key"])).status_code == 401
    assert client.delete(f"/api/projects/{pid}/docs/sites/{sid}/api-keys/{key['id']}").status_code == 204
    assert client.get(api, headers=bearer(secret)).status_code == 401
    assert client.post(f"/api/projects/{pid}/docs/sites/{sid}/api-keys", json={"name": "Old", "expires_at": "2020-01-01T00:00:00Z"}).status_code == 400

    # The manager makes and revokes keys with its doc tool.
    manager = manager_of(client, pid)
    chat(client, manager["id"], "TOOL doc_api_keys " + json.dumps({"site": "handbook", "create": "Wiki import", "days": 30}))
    wait_for(lambda: idle(client, pid))
    made = tool_results(client, manager["id"])[-1]
    assert "pbd_" in made and "Wiki import" in made and "expires" in made
    chat(client, manager["id"], "TOOL doc_api_keys " + json.dumps({"site": "handbook", "revoke": "Wiki import"}))
    wait_for(lambda: idle(client, pid))
    assert "Revoked key Wiki import" in tool_results(client, manager["id"])[-1]


def test_a_site_of_thousands_of_pages_is_exported_streamed_and_only_what_changed_since(client, project):
    from plainboard.models import DocPage, utcnow
    from plainboard.services.docs import source_hash
    from sqlalchemy import insert, update

    pid = project["id"]
    s = site(client, pid, published=True, public=True)
    (picture,) = client.post(f"/api/projects/{pid}/docs/sites/{s['id']}/files", files=[("files", ("a.png", b"\x89PNG\r\n\x1a\nyy", "image/png"))]).json()
    count = 1500
    old = utcnow() - timedelta(days=2)

    async def fill(session):
        rows = []
        for n in range(1, count + 1):
            parent = None if n <= 50 else 200000 + (n % 50) + 1
            slug = f"p{n}"
            path = slug if parent is None else f"p{parent - 200000}/{slug}"
            body = f"Text {n}." + (f" ![a](doc-file:{picture['id']}) [first](doc-page:200001)" if n == 77 else "")
            rows.append({"id": 200000 + n, "page_key": 200000 + n, "project_id": pid, "site_id": s["id"], "parent_id": parent, "position": n, "title": f"Page {n}", "slug": slug,
                         "path": path, "depth": 0 if parent is None else 1, "body": body, "source_hash": source_hash(f"Page {n}", body), "paths": [], "edited_by": "x",
                         "edited_at": old, "drift": 0, "flag_reason": "", "created_at": old, "updated_at": old})  # fmt: skip
        for start in range(0, len(rows), 500):
            await session.execute(insert(DocPage), rows[start : start + 500])
        await session.commit()

    db(client, fill)
    secret = make_key(client, pid, s["id"])["key"]
    client.cookies.clear()
    api = "/api/v1/docs/handbook"

    seen, cursor = [], 0
    while cursor is not None:
        got = client.get(f"{api}/pages", params={"cursor": cursor, "limit": 1000}, headers=bearer(secret)).json()
        seen += got["items"]
        cursor = got["next_cursor"]
    assert len(seen) == count and len({p["id"] for p in seen}) == count

    lines = [json.loads(line) for line in client.get(f"{api}/export", headers=bearer(secret)).text.splitlines()]
    assert lines[0]["type"] == "site" and lines[-1] == {"type": "end", "pages": count, "files": 1}
    pages = [line for line in lines if line["type"] == "page"]
    assert len(pages) == count and next(p for p in pages if p["id"] == 200077)["links"] == {"200001": "p1"}
    assert [line["id"] for line in lines if line["type"] == "file"] == [picture["id"]]

    resp = client.get(f"{api}/export", params={"format": "zip"}, headers=bearer(secret))
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(resp.content))
    names = archive.namelist()
    assert len([n for n in names if n.startswith("pages/")]) == count and "site.json" in names and len(json.loads(archive.read("index.json"))) == count
    linked = archive.read("pages/p28/p77.md").decode()
    assert linked.startswith("---\nid: 200077\n") and f"](../../files/{picture['id']}-a.png)" in linked and "](../p1.md)" in linked
    assert archive.read(f"files/{picture['id']}-a.png").startswith(b"\x89PNG")

    since = utcnow() - timedelta(hours=1)

    async def touch(session):
        await session.execute(update(DocPage).where(DocPage.id.in_([200003, 201400])).values(updated_at=utcnow()))
        await session.commit()

    db(client, touch)
    changed = client.get(f"{api}/pages", params={"since": since.isoformat() + "Z"}, headers=bearer(secret)).json()["items"]
    assert [p["id"] for p in changed] == [200003, 201400]
    lines = [json.loads(line) for line in client.get(f"{api}/export", params={"since": since.isoformat() + "Z"}, headers=bearer(secret)).text.splitlines()]
    assert [line["id"] for line in lines if line["type"] == "page"] == [200003, 201400] and lines[-1]["pages"] == 2
    sign_in(client, "admin", ADMIN_PASSWORD)
