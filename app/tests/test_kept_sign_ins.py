"""The installation's browser sign-ins are kept by the app and work on every runner (#84): taken over once from
the built-in runner's home, sent with the runs of their account only, refreshed tokens kept and handed on, and
no turn starts on a runner where the subscription has no working sign-in."""

import json

from conftest import FakeRunner, idle, wait_for
from test_runners import _add_runner, _connect, _hub

CODEX_HOME = {"auth.json": '{"auth_mode": "chatgpt", "tokens": {"access_token": "eyJ-codex-access", "refresh_token": "rt-codex-home"}}'}
CLAUDE_HOME = {".credentials.json": '{"claudeAiOauth": {"accessToken": "sk-ant-oat-home", "refreshToken": "sk-ant-ort-home"}}'}
SECRETS = ("eyJ-codex-access", "rt-codex-home", "sk-ant-oat-home", "sk-ant-ort-home", "rt-codex-new")


def home_runner(runner, busy: set[str] | None = None):
    """The built-in runner's home with the installation's sign-ins in it, as before #84."""
    from plainboard_protocol import SignInFiles, files_digest

    runner.home = {"codex": dict(CODEX_HOME), "claude_code": dict(CLAUDE_HOME)}
    runner.taken = []

    async def take_over(provider, key):
        if key in (busy or set()):
            raise RuntimeError("409 The sign-in is in use")
        runner.taken.append((provider, key))
        files = dict(runner.home[provider])  # a copy: the home keeps its files
        return SignInFiles(provider=provider, account=key, files=files, digest=files_digest(files))

    runner.take_over = take_over
    return runner


def stored(client, key: str):
    from plainboard.db import sessionmaker
    from plainboard.models import Account
    from plainboard.services import accounts as account_service
    from sqlalchemy import select

    async def read():
        async with sessionmaker()() as s:
            account = await s.scalar(select(Account).where(Account.key == key))
            return account.home_login, account_service.login_files(account)

    return client.portal.call(read)


def look(client) -> None:
    client.portal.call(client.app_ref.state.runs.check_sign_ins, True)


def no_secrets(client, *paths: str) -> None:
    for path in ("/api/accounts", "/api/limits", *paths):
        text = client.get(path).text
        assert not [s for s in SECRETS if s in text], path


def test_the_home_sign_ins_are_copied_over_once_and_left_in_the_home(client, project):
    runner = home_runner(client.app_ref.state.runner, busy={"default"})
    second = client.post("/api/accounts", json={"provider": "claude_code", "auth_mode": "runner_login", "label": "Claude 2"}).json()
    assert second["home_login"] is False  # a new one is kept here from the start

    look(client)  # Codex is in use: it waits; Claude's home sign-in goes to the first of its accounts
    assert stored(client, "default") == (True, {})
    assert stored(client, "claude") == (False, CLAUDE_HOME)
    assert runner.home == {"codex": CODEX_HOME, "claude_code": CLAUDE_HOME}  # copied, not moved
    home_runner(runner)
    look(client)
    assert stored(client, "default") == (False, CODEX_HOME)
    assert runner.taken == [("codex", "default")]  # once: Claude's was taken over before
    look(client)
    assert runner.taken == [("codex", "default")] and runner.home["codex"] == CODEX_HOME
    no_secrets(client, f"/api/projects/{project['id']}/accounts")

    # From now on its runs carry the copy, on the built-in runner too; another subscription's runs never get it.
    pid = project["id"]
    dev = client.post(f"/api/projects/{pid}/agents", json={"name": "Dev", "role": "Developer", "description": "x", "provider": "codex"}).json()
    assert client.post(f"/api/agents/{dev['id']}/messages", json={"text": "SAY hi"}).status_code == 202
    wait_for(lambda: idle(client, pid))
    creds = runner.specs[-1].credentials
    assert creds.codex_auth_mode == "own_login" and creds.login_files == CODEX_HOME and creds.login_account == "default"
    lead = next(a for a in client.get(f"/api/projects/{pid}/agents").json() if a["is_manager"])
    assert client.post(f"/api/agents/{lead['id']}/messages", json={"text": "SAY lead"}).status_code == 202
    wait_for(lambda: idle(client, pid))
    manager = runner.specs[-1].credentials
    assert manager.claude_auth_mode == "own_login" and manager.login_files == CLAUDE_HOME and manager.login_account == "claude"
    token = client.post("/api/accounts", json={"provider": "claude_code", "auth_mode": "oauth_token", "label": "Token", "secret": "tok"}).json()
    assert client.patch(f"/api/projects/{pid}", json={"account_id": token["id"]}).status_code == 200
    assert client.post(f"/api/agents/{lead['id']}/messages", json={"text": "SAY token"}).status_code == 202
    wait_for(lambda: idle(client, pid))
    assert runner.specs[-1].credentials.login_files is None and "sk-ant-ort-home" not in runner.specs[-1].model_dump_json()

    # Signed out: the copy goes; the home's backup is not touched.
    assert client.delete(f"/api/accounts/{accounts_by_key(client)['default']}/signin").json()["signed_in"] is False
    assert stored(client, "default") == (False, {}) and runner.home["codex"] == CODEX_HOME


def accounts_by_key(client) -> dict[str, int]:
    from plainboard.db import sessionmaker
    from plainboard.models import Account
    from sqlalchemy import select

    async def read():
        async with sessionmaker()() as s:
            return dict((await s.execute(select(Account.key, Account.id))).all())

    return client.portal.call(read)


def test_an_external_runner_gets_the_sign_in_and_hands_refreshed_tokens_back(client, project):
    from plainboard_protocol import SignInFiles, files_digest

    builtin = home_runner(client.app_ref.state.runner)
    look(client)
    external = FakeRunner("tunnel")
    external.updated = []

    async def update_sign_ins(entries):
        external.updated.append(entries)

    external.update_sign_ins = update_sign_ins
    hub = _hub(client)
    hub.make_client = lambda _rid: external
    rid = _add_runner(client)["runner"]["id"]
    other = client.post("/api/projects", json={"name": "Elsewhere"}).json()
    client.put(f"/api/projects/{other['id']}/runner", json={"runner_id": rid})
    dev = client.post(f"/api/projects/{other['id']}/agents", json={"name": "Dev", "role": "Developer", "description": "x", "provider": "codex"}).json()

    with _connect(client, client.post(f"/api/runners/{rid}/token").json()["token"]) as ws:
        ws.send_text(json.dumps({"t": "hello", "version": "", "protocol": 1, "features": ["signins"]}))
        wait_for(lambda: hub.connected(rid))
        assert client.post(f"/api/agents/{dev['id']}/messages", json={"text": "SAY there"}).status_code == 202
        wait_for(lambda: idle(client, other["id"]))
        creds = external.specs[-1].credentials
        assert creds.codex_auth_mode == "own_login" and creds.login_files == CODEX_HOME

        # A run there refreshed the token: kept, and handed to the built-in runner while a turn of it goes on there.
        fresh = {"auth.json": '{"auth_mode": "chatgpt", "tokens": {"refresh_token": "rt-codex-new"}}'}
        external.sign_in_outbox["default"] = SignInFiles(provider="codex", account="default", files=fresh, base=files_digest(CODEX_HOME), digest=files_digest(fresh))
        builtin.updated = []

        async def update_builtin(entries):
            builtin.updated.append(entries)

        builtin.update_sign_ins = update_builtin
        from plainboard.orchestration.run_manager import RunContext

        runs = client.app_ref.state.runs
        runs._tokens["live"] = RunContext(run_id=-5, project_id=project["id"], agent_id=0, token="live", account_key="default")
        try:
            look(client)
        finally:
            runs._tokens.pop("live")
        assert stored(client, "default") == (False, fresh) and not external.sign_in_outbox
        assert [[e.account for e in batch] for batch in builtin.updated] == [["default"]] and builtin.updated[0][0].base == files_digest(CODEX_HOME)
        assert external.updated == []  # where it came from
        no_secrets(client, f"/api/projects/{other['id']}/accounts")


def test_no_turn_starts_where_the_subscription_is_not_signed_in(client, project):
    """A runner from before kept sign-ins, or a subscription that is not signed in: another subscription that
    works there is taken, else the turn fails with a clear reason and nothing reaches the runner."""
    home_runner(client.app_ref.state.runner)
    look(client)
    external = FakeRunner("tunnel")
    hub = _hub(client)
    hub.make_client = lambda _rid: external
    rid = _add_runner(client, "megapanel")["runner"]["id"]
    other = client.post("/api/projects", json={"name": "Elsewhere"}).json()
    client.put(f"/api/projects/{other['id']}/runner", json={"runner_id": rid})
    dev = client.post(f"/api/projects/{other['id']}/agents", json={"name": "Dev", "role": "Developer", "description": "x", "provider": "codex"}).json()
    codex = accounts_by_key(client)["default"]

    with _connect(client, client.post(f"/api/runners/{rid}/token").json()["token"]) as ws:
        ws.send_text(json.dumps({"t": "hello", "version": "", "protocol": 1}))  # an old runner: no "signins"
        wait_for(lambda: hub.connected(rid))
        assert codex not in client.get(f"/api/projects/{other['id']}/accounts").json()  # not shown as usable there
        assert codex in client.get(f"/api/projects/{project['id']}/accounts").json()
        assert client.post(f"/api/agents/{dev['id']}/messages", json={"text": "SAY there"}).status_code == 202
        wait_for(lambda: idle(client, other["id"]))
        run = [r for r in client.get(f"/api/projects/{other['id']}/runs").json() if r["agent_id"] == dev["id"]][-1]
        assert run["status"] == "failed" and "Codex is not signed in on runner megapanel" in run["error"] and "newer version" in run["error"]
        assert not [s for s in external.specs if s.provider == "codex"]

        # Another Codex subscription that works there is taken instead.
        key = client.post("/api/accounts", json={"provider": "codex", "auth_mode": "api_key", "label": "Key", "secret": "sk-openai"}).json()
        assert client.post(f"/api/agents/{dev['id']}/messages", json={"text": "SAY again"}).status_code == 202
        wait_for(lambda: idle(client, other["id"]))
        assert external.specs[-1].credentials.codex_auth_mode == "api_key" and external.specs[-1].credentials.login_files is None
        assert client.delete(f"/api/accounts/{key['id']}").status_code == 204

    # Not signed in at all: not on the built-in runner either, and not shown with limits.
    assert client.delete(f"/api/accounts/{codex}/signin").status_code == 200
    assert codex not in client.get(f"/api/projects/{project['id']}/accounts").json()
    assert codex not in [a["account_id"] for a in client.get("/api/limits").json()["accounts"]]
    here = client.post(f"/api/projects/{project['id']}/agents", json={"name": "Dev", "role": "Developer", "description": "x", "provider": "codex"}).json()
    assert client.post(f"/api/agents/{here['id']}/messages", json={"text": "SAY here"}).status_code == 202
    wait_for(lambda: idle(client, project["id"]))
    run = [r for r in client.get(f"/api/projects/{project['id']}/runs").json() if r["agent_id"] == here["id"]][-1]
    assert run["status"] == "failed"
    assert "Codex is not signed in on the built-in runner: 'Codex' is not signed in" in run["error"]
    no_secrets(client)
