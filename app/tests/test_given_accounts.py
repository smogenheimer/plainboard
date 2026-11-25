"""Members give their own subscriptions to a project themselves (#93, permissions.contributed)."""

from conftest import idle, wait_for
from test_own_accounts import TOKEN, ids, inbox
from test_permissions import as_friend, friend


def given(client, pid: int) -> dict[int, dict]:
    resp = client.get(f"/api/projects/{pid}/given-accounts")
    assert resp.status_code == 200, resp.text
    return {a["id"]: a for a in resp.json()}


def pinned(client, pid: int, agent_id: int) -> int | None:
    return next(a for a in client.get(f"/api/projects/{pid}/agents").json() if a["id"] == agent_id)["account_id"]


def test_a_member_gives_and_takes_back_an_own_subscription_the_owner_switches_it_off_and_never_reads_it(client, project):
    olga = friend(client)
    friend(client, "bob")
    as_friend(client, "bob")
    pid = client.post("/api/projects", json={"name": "Bob's"}).json()["id"]
    bobs = client.post("/api/accounts", json={**TOKEN, "label": "Bob's", "secret": "tok-bob"}).json()
    assert client.put(f"/api/projects/{pid}/members/{olga['id']}", json={"role": "member"}).status_code == 200
    installation = next(i for i in ids(client, pid) if i != bobs["id"])

    as_friend(client)
    own = client.post("/api/accounts", json={**TOKEN, "label": "Olga's", "secret": "tok-olga"}).json()
    assert own["share"] == "private" and own["id"] not in ids(client, pid)
    assert not given(client, pid)[own["id"]]["given"]
    # Only one's own: not the installation's, not another member's.
    assert client.put(f"/api/projects/{pid}/given-accounts/{installation}", json={"given": True}).status_code == 403
    assert client.put(f"/api/projects/{pid}/given-accounts/{bobs['id']}", json={"given": True}).status_code == 403
    resp = client.put(f"/api/projects/{pid}/given-accounts/{own['id']}", json={"given": True})
    assert resp.status_code == 200 and resp.json()[0]["given"] and resp.json()[0]["used"]
    assert own["id"] in ids(client, pid)
    assert next(a for a in client.get("/api/accounts").json() if a["id"] == own["id"])["project_ids"] == [pid]
    dev = client.post(f"/api/projects/{pid}/agents", json={"name": "Dev", "role": "Developer", "description": "x", "account_id": own["id"]}).json()
    assert dev["account_id"] == own["id"]
    assert client.post(f"/api/agents/{dev['id']}/messages", json={"text": "SAY hi"}).status_code == 202
    wait_for(lambda: idle(client, pid))
    assert client.app_ref.state.runner.specs[-1].credentials.claude_oauth_token == "tok-olga"
    assert client.put(f"/api/projects/{pid}/blocked-accounts/{own['id']}", json={"blocked": True}).status_code == 403  # the owner's call

    # The owner sees it with its giver's name, never its secret, and switches it off; only one a member gave.
    as_friend(client, "bob")
    seen = next(a for a in client.get("/api/accounts").json() if a["id"] == own["id"])
    assert seen["owner_name"] and not seen["can_manage"] and not seen["secret_set"] and seen["project_ids"] == []
    mine = given(client, pid)
    assert mine[own["id"]]["owner_name"] and mine[own["id"]]["given"] and not mine[own["id"]]["mine"]
    assert all("tok-olga" not in r.text for r in (client.get("/api/accounts"), client.get(f"/api/projects/{pid}/given-accounts")))
    assert client.patch(f"/api/accounts/{own['id']}", json={"label": "Mine"}).status_code == 403
    assert client.put(f"/api/projects/{pid}/given-accounts/{own['id']}", json={"given": False}).status_code == 403
    assert client.put(f"/api/projects/{pid}/blocked-accounts/{installation}", json={"blocked": True}).status_code == 400
    off = client.put(f"/api/projects/{pid}/blocked-accounts/{own['id']}", json={"blocked": True}).json()
    assert next(a for a in off if a["id"] == own["id"])["blocked"] and own["id"] not in ids(client, pid)
    assert pinned(client, pid, dev["id"]) is None
    assert sum("'Olga's' was switched off for this project" in text for text in inbox(client, pid)) == 1
    client.put(f"/api/projects/{pid}/blocked-accounts/{own['id']}", json={"blocked": False})
    assert own["id"] in ids(client, pid)

    # Taken back: the project no longer uses it.
    as_friend(client)
    assert not client.put(f"/api/projects/{pid}/given-accounts/{own['id']}", json={"given": False}).json()[0]["given"]
    assert own["id"] not in ids(client, pid)


def test_who_no_longer_works_in_the_project_takes_their_subscription_along(client, project):
    olga = friend(client)
    friend(client, "bob")
    as_friend(client, "bob")
    pid = client.post("/api/projects", json={"name": "Bob's"}).json()["id"]
    client.put(f"/api/projects/{pid}/members/{olga['id']}", json={"role": "member"})
    as_friend(client)
    own = client.post("/api/accounts", json={**TOKEN, "label": "Olga's", "secret": "tok-olga"}).json()
    client.put(f"/api/projects/{pid}/given-accounts/{own['id']}", json={"given": True})
    dev = client.post(f"/api/projects/{pid}/agents", json={"name": "Dev", "role": "Developer", "description": "x", "account_id": own["id"]}).json()

    as_friend(client, "bob")
    assert client.put(f"/api/projects/{pid}/members/{olga['id']}", json={"role": "viewer"}).status_code == 200
    assert own["id"] not in ids(client, pid) and pinned(client, pid, dev["id"]) is None
    client.put(f"/api/projects/{pid}/members/{olga['id']}", json={"role": "member"})
    as_friend(client)
    client.put(f"/api/projects/{pid}/given-accounts/{own['id']}", json={"given": True})
    as_friend(client, "bob")
    assert own["id"] in ids(client, pid)
    assert client.delete(f"/api/projects/{pid}/members/{olga['id']}").status_code == 200
    assert own["id"] not in ids(client, pid) and own["id"] not in given(client, pid)
    as_friend(client)
    assert next(a for a in client.get("/api/accounts").json() if a["id"] == own["id"])["project_ids"] == []
