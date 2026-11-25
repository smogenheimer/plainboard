"""Subscriptions people add themselves and share (Account.owner_id, services/permissions.may_use)."""

from conftest import ADMIN_PASSWORD, idle, wait_for
from test_permissions import as_friend, friend, sign_in

TOKEN = {"provider": "claude_code", "auth_mode": "oauth_token"}


def inbox(client, project_id: int) -> list[str]:
    from plainboard.db import sessionmaker
    from plainboard.models import Agent, InboxItem
    from sqlalchemy import select

    async def read() -> list[str]:
        async with sessionmaker()() as s:
            manager = await s.scalar(select(Agent.id).where(Agent.project_id == project_id, Agent.is_manager))
            return list(await s.scalars(select(InboxItem.text).where(InboxItem.agent_id == manager)))

    return client.portal.call(read)


def ids(client, project_id: int) -> set[int]:
    return set(client.get(f"/api/projects/{project_id}/accounts").json())


def test_a_person_adds_an_own_subscription_that_only_their_projects_use_and_nobody_reads_back(client, project, monkeypatch):
    installation = {a["id"] for a in client.get("/api/accounts").json()}
    friend(client)
    friend(client, "bob")
    as_friend(client)
    resp = client.post("/api/accounts", json={**TOKEN, "label": "Olga's", "secret": "tok-olga"})
    assert resp.status_code == 201, resp.text
    own = resp.json()
    assert own["owner_id"] is not None and own["share"] == "private" and own["can_manage"] and own["secret_set"]
    assert "tok-olga" not in resp.text and all("tok-olga" not in str(a) for a in client.get("/api/accounts").json())
    pid = client.post("/api/projects", json={"name": "Olga's", "account_id": own["id"]}).json()["id"]
    assert ids(client, pid) == installation | {own["id"]}
    dev = client.post(f"/api/projects/{pid}/agents", json={"name": "Dev", "role": "Developer", "description": "x"}).json()
    assert client.post(f"/api/agents/{dev['id']}/messages", json={"text": "SAY hi"}).status_code == 202
    wait_for(lambda: idle(client, pid))
    assert client.app_ref.state.runner.specs[-1].credentials.claude_oauth_token == "tok-olga"

    # Not the installation's environment for an own subscription without a secret.
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok-installation")
    assert client.patch(f"/api/accounts/{own['id']}", json={"secret": ""}).json()["secret_from_env"] is False

    # Bob neither sees nor uses it, nor changes it; an admin's project does not run on it.
    as_friend(client, "bob")
    assert own["id"] not in {a["id"] for a in client.get("/api/accounts").json()}
    assert own["id"] not in {a["account_id"] for a in client.get("/api/limits").json()["accounts"]}
    assert client.patch(f"/api/accounts/{own['id']}", json={"label": "Mine now"}).status_code == 404
    assert client.delete(f"/api/accounts/{own['id']}").status_code == 404
    bobs = client.post("/api/projects", json={"name": "Bob's"}).json()["id"]
    assert own["id"] not in ids(client, bobs)
    assert client.post("/api/projects", json={"name": "Bob's pinned", "account_id": own["id"]}).status_code == 400
    sign_in(client, "admin", ADMIN_PASSWORD)
    assert own["id"] not in ids(client, project["id"])
    seen = next(a for a in client.get("/api/accounts").json() if a["id"] == own["id"])
    assert seen["owner_name"] and seen["can_manage"]  # an admin manages it too


def test_shared_with_people_or_everyone_and_unshared_pins_choose_again_and_managers_hear_once(client, project):
    olga = friend(client)
    bob = friend(client, "bob")
    friend(client, "eve")
    as_friend(client)
    own = client.post("/api/accounts", json={**TOKEN, "label": "Team", "secret": "tok-team"}).json()
    assert client.patch(f"/api/accounts/{own['id']}", json={"share": "people", "shared_with": [9999]}).status_code == 404
    shared = client.patch(f"/api/accounts/{own['id']}", json={"share": "people", "shared_with": [bob["id"], olga["id"]]}).json()
    assert shared["share"] == "people" and shared["shared_with"] == [bob["id"]]

    as_friend(client, "bob")
    seen = next(a for a in client.get("/api/accounts").json() if a["id"] == own["id"])
    assert not seen["can_manage"] and not seen["secret_set"] and seen["shared_with"] == [] and seen["label"] == "Team"
    assert client.patch(f"/api/accounts/{own['id']}", json={"share": "everyone"}).status_code == 403
    assert client.post(f"/api/accounts/{own['id']}/check", json={"model": "x"}).status_code == 403
    pid = client.post("/api/projects", json={"name": "Bob's", "account_id": own["id"]}).json()["id"]
    dev = client.post(f"/api/projects/{pid}/agents", json={"name": "Dev", "role": "Developer", "description": "x", "account_id": own["id"]}).json()
    assert dev["account_id"] == own["id"]
    as_friend(client, "eve")
    assert own["id"] not in {a["id"] for a in client.get("/api/accounts").json()}

    as_friend(client)
    assert client.patch(f"/api/accounts/{own['id']}", json={"share": "everyone"}).status_code == 200
    as_friend(client, "eve")
    assert own["id"] in {a["id"] for a in client.get("/api/accounts").json()}
    sign_in(client, "admin", ADMIN_PASSWORD)
    assert own["id"] in ids(client, project["id"])

    as_friend(client)
    before = len(inbox(client, pid))
    assert client.patch(f"/api/accounts/{own['id']}", json={"share": "private"}).status_code == 200
    as_friend(client, "bob")
    assert client.get(f"/api/projects/{pid}").json()["account_id"] is None
    assert next(a for a in client.get(f"/api/projects/{pid}/agents").json() if a["id"] == dev["id"])["account_id"] is None
    told = inbox(client, pid)[before:]
    assert len(told) == 1 and "'Team' is no longer shared" in told[0] and "the project, Dev" in told[0]
    assert own["id"] not in ids(client, pid)


def test_a_deleted_own_subscription_or_person_releases_what_ran_on_it(client, project):
    olga = friend(client)
    as_friend(client)
    own = client.post("/api/accounts", json={**TOKEN, "label": "Gone", "secret": "tok", "auth_mode": "api_key"}).json()
    client.patch(f"/api/accounts/{own['id']}", json={"share": "everyone"})
    later = client.post("/api/accounts", json={**TOKEN, "label": "Later", "secret": "tok2"}).json()
    sign_in(client, "admin", ADMIN_PASSWORD)
    assert client.patch(f"/api/projects/{project['id']}", json={"account_id": own["id"]}).status_code == 200
    as_friend(client)
    assert client.delete(f"/api/accounts/{own['id']}").status_code == 204
    sign_in(client, "admin", ADMIN_PASSWORD)
    assert client.get(f"/api/projects/{project['id']}").json()["account_id"] is None
    assert sum("'Gone' was deleted" in text for text in inbox(client, project["id"])) == 1
    assert client.delete(f"/api/users/{olga['id']}").status_code == 204
    assert later["id"] not in {a["id"] for a in client.get("/api/accounts").json()}


def test_own_browser_sign_ins_are_kept_per_subscription_and_only_reach_its_runs(client, project):
    """Claude's browser sign-in and Codex's ChatGPT sign-in for someone who is no admin: made in a private
    folder on the runner, kept here encrypted, never shown, and sent only with that subscription's runs."""
    from plainboard_protocol import SignInFiles, files_digest

    runner = client.app_ref.state.runner
    friend(client)
    as_friend(client)
    claude = client.post("/api/accounts", json={"provider": "claude_code", "auth_mode": "runner_login", "label": "Olga's login"})
    assert claude.status_code == 201, claude.text
    claude = claude.json()
    codex = client.post("/api/accounts", json={"provider": "codex", "auth_mode": "chatgpt"})
    assert codex.status_code == 201, codex.text
    assert client.patch(f"/api/accounts/{claude['id']}", json={"secret": "a token"}).status_code == 400  # signed in in the browser only
    token = client.post("/api/accounts", json={**TOKEN, "label": "Olga's token", "secret": "tok-olga"}).json()

    url = f"/api/accounts/{claude['id']}/signin"
    assert client.get(url).json()["signed_in"] is False
    started = client.post(url).json()
    assert started["needs_code"] and started["login_url"]
    refused = client.post(f"{url}/code", json={"code": "half"}).json()
    assert refused["login_error"] and not refused["signed_in"]
    done = client.post(f"{url}/code", json={"code": "good#code"})
    assert done.status_code == 200 and done.json()["signed_in"], done.text
    assert not runner.sign_in_outbox  # kept here, so the runner forgets it
    secrets = ("sk-ant-oat-olga", "sk-ant-ort-olga")
    for path in ("/api/accounts", url, "/api/limits", f"/api/projects/{project['id']}/accounts"):
        resp = client.get(path)
        assert not [x for x in secrets if x in resp.text], path
    assert next(a for a in client.get("/api/accounts").json() if a["id"] == claude["id"])["secret_set"]

    # Its runs get the files in a private folder ("own_login"), no token; another subscription's runs never get them.
    pid = client.post("/api/projects", json={"name": "Olga's", "account_id": claude["id"]}).json()["id"]
    dev = client.post(f"/api/projects/{pid}/agents", json={"name": "Dev", "role": "Developer", "description": "x"}).json()
    assert client.post(f"/api/agents/{dev['id']}/messages", json={"text": "SAY hi"}).status_code == 202
    wait_for(lambda: idle(client, pid))
    creds = runner.specs[-1].credentials
    assert creds.claude_auth_mode == "own_login" and creds.login_files == runner.sign_in_files and creds.login_account != "default"
    assert creds.claude_oauth_token is None and creds.anthropic_api_key is None
    assert client.patch(f"/api/projects/{pid}", json={"account_id": token["id"]}).status_code == 200
    assert client.post(f"/api/agents/{dev['id']}/messages", json={"text": "SAY again"}).status_code == 202
    wait_for(lambda: idle(client, pid))
    other = runner.specs[-1].credentials
    assert other.claude_oauth_token == "tok-olga" and other.login_files is None and "sk-ant" not in runner.specs[-1].model_dump_json()

    # A run refreshed the token: kept. One derived from an older version comes too late and is dropped.
    stored = runner.sign_in_files
    fresh = {".credentials.json": '{"claudeAiOauth": {"accessToken": "sk-ant-oat-new"}}'}
    key = creds.login_account
    runner.sign_in_outbox[key] = SignInFiles(provider="claude_code", account=key, files=fresh, base=files_digest(stored), digest=files_digest(fresh))
    client.portal.call(client.app_ref.state.runs.check_sign_ins, True)
    stale = {".credentials.json": '{"claudeAiOauth": {"accessToken": "sk-ant-oat-old"}}'}
    runner.sign_in_outbox[key] = SignInFiles(provider="claude_code", account=key, files=stale, base=files_digest(stored), digest=files_digest(stale))
    client.portal.call(client.app_ref.state.runs.check_sign_ins, True)
    assert not runner.sign_in_outbox
    assert client.patch(f"/api/projects/{pid}", json={"account_id": claude["id"]}).status_code == 200
    assert client.post(f"/api/agents/{dev['id']}/messages", json={"text": "SAY third"}).status_code == 202
    wait_for(lambda: idle(client, pid))
    assert runner.specs[-1].credentials.login_files == fresh

    # Signed out: gone here as well. Codex's own ChatGPT sign-in works the same way.
    assert client.delete(url).json()["signed_in"] is False
    assert not next(a for a in client.get("/api/accounts").json() if a["id"] == claude["id"])["secret_set"]
    assert client.post(f"/api/accounts/{codex.json()['id']}/signin").status_code == 200


def test_an_admin_turns_off_own_subscriptions_new_ones_first_then_those_there_are_reversibly(client, project):
    """#138: off, people add none; those they have keep working until the admin switches them off too:
    what was pinned to one chooses again and the manager hears it. On again, they work as before."""
    friend(client)
    as_friend(client)
    own = client.post("/api/accounts", json={**TOKEN, "label": "Olga's", "secret": "tok-olga", "auth_mode": "api_key"}).json()
    client.patch(f"/api/accounts/{own['id']}", json={"share": "everyone"})
    assert client.put("/api/settings", json={"values": {"own_accounts": False}}).status_code == 403  # admins only

    sign_in(client, "admin", ADMIN_PASSWORD)
    assert client.patch(f"/api/projects/{project['id']}", json={"account_id": own["id"]}).status_code == 200
    assert client.put("/api/settings", json={"values": {"own_accounts": False}}).status_code == 200
    assert client.post("/api/accounts", json={**TOKEN, "label": "Admin's", "secret": "tok"}).status_code == 201  # the installation's
    as_friend(client)
    refused = client.post("/api/accounts", json={**TOKEN, "label": "Another", "secret": "tok"})
    assert refused.status_code == 403 and "turned off" in refused.text
    assert client.get("/api/settings").json()["values"]["own_accounts"] is False  # the UI reads it
    sign_in(client, "admin", ADMIN_PASSWORD)
    assert own["id"] in ids(client, project["id"]) and client.get(f"/api/projects/{project['id']}").json()["account_id"] == own["id"]

    assert client.put("/api/settings", json={"values": {"own_accounts_off": True}}).status_code == 200
    assert own["id"] not in ids(client, project["id"])
    assert client.get(f"/api/projects/{project['id']}").json()["account_id"] is None
    assert sum("'Olga's' was switched off by an admin" in text for text in inbox(client, project["id"])) == 1
    assert client.patch(f"/api/projects/{project['id']}", json={"account_id": own["id"]}).status_code == 400
    as_friend(client)
    assert own["id"] in {a["id"] for a in client.get("/api/accounts").json()}  # still there, not deleted

    sign_in(client, "admin", ADMIN_PASSWORD)
    assert client.put("/api/settings", json={"values": {"own_accounts": True}}).status_code == 200
    assert own["id"] in ids(client, project["id"])
    as_friend(client)
    assert client.post("/api/accounts", json={**TOKEN, "label": "Another", "secret": "tok"}).status_code == 201
