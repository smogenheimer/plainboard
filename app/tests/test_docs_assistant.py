"""A doc site's assistant for visitors (#119): answers from the site's served pages only, no built-in tools, the code
and the board only with its switch, secrets filtered, limits per client and per site."""

import json

import pytest
from conftest import ADMIN_PASSWORD, runs_of
from test_docs import page, site
from test_permissions import as_friend, friend, sign_in

from plainboard.services import docs_assistant as assistant
from plainboard.services import docs_public


@pytest.fixture(autouse=True)
def fresh_limits(monkeypatch):
    monkeypatch.setattr(assistant, "PER_MINUTE_LIMIT", docs_public.RateLimit(assistant.PER_MINUTE, 60))
    monkeypatch.setattr(assistant, "PER_DAY_LIMIT", docs_public.RateLimit(assistant.PER_DAY, 86400))


def ask(client, slug: str, text: str, **body) -> tuple[int, list[dict]]:
    resp = client.post(f"/api/public/docs/{slug}/assistant", json={"text": text, **body})
    if resp.status_code != 200:
        return resp.status_code, [resp.json()]
    return 200, [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def shop(client, pid: int, **settings) -> dict:
    s = site(client, pid, published=True, public=True)
    page(client, pid, s["id"], title="Payments", body="Payments are retried with backoff.")
    resp = client.patch(f"/api/projects/{pid}/docs/sites/{s['id']}", json={"assistant_enabled": True, **settings})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_settings_are_off_by_default_warn_about_the_code_and_run_on_claude_only(client, project):
    pid = project["id"]
    s = site(client, pid)
    assert s["assistant_enabled"] is False and s["assistant_code"] is False
    assert "visitor" in s["assistant_warning"]
    resp = client.patch(f"/api/projects/{pid}/docs/sites/{s['id']}", json={"assistant_provider": "codex"})
    assert resp.status_code == 400
    changed = client.patch(
        f"/api/projects/{pid}/docs/sites/{s['id']}",
        json={"assistant_enabled": True, "assistant_model": "claude-sonnet-5-5", "assistant_daily_cost": 2.5, "assistant_max_tokens": 600},
    ).json()
    assert changed["assistant_enabled"] and changed["assistant_model"] == "claude-sonnet-5-5" and changed["assistant_daily_cost"] == 2.5
    assert client.patch(f"/api/projects/{pid}/docs/sites/{s['id']}", json={"assistant_max_tokens": 50}).status_code == 422


def test_it_answers_from_the_sites_served_pages_only(client, project):
    pid = project["id"]
    s = shop(client, pid)
    draft = site(client, pid, name="Internal")
    page(client, pid, draft["id"], title="Payments roadmap", body="Payments secret plan.")
    runner = client.app_ref.state.runner
    client.cookies.clear()

    assert client.get("/api/public/docs/handbook").json()["assistant"] is True
    status, lines = ask(client, "handbook", 'How are payments retried?\nTOOL search_pages {"query": "payments"}\nTOOL read_page {"path": "payments"}')
    assert status == 200, lines
    assert lines[0]["type"] == "conversation" and [x["name"] for x in lines if x["type"] == "tool"] == ["search_pages", "read_page"]
    done = lines[-1]
    assert done["type"] == "done" and "retried with backoff" in done["text"] and "secret plan" not in done["text"]
    assert done["sources"] == [{"title": "Payments", "url": "/docs/handbook/payments"}]
    spec = runner.specs[-1]
    assert spec.toolset == "none" and {t.name for t in spec.bridge.tools} == {"search_pages", "read_page"}
    assert "retried with backoff" in spec.prompt and "secret plan" not in spec.prompt  # the head start: the best pages
    assert spec.credentials.secrets == {} and spec.workspace.repos == [] and spec.workspace.path.endswith("/.docs-assistant")
    assert spec.env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "1024"

    sign_in(client, "admin", ADMIN_PASSWORD)
    (run,) = runs_of(client, pid)
    assert run["trigger"] == "doc_assistant" and run["input_text"] == "" and run["agent_id"] == s["agent"]["id"]
    client.cookies.clear()
    assert ask(client, "internal", "Anything?")[0] == 404  # a draft has no assistant
    sign_in(client, "admin", ADMIN_PASSWORD)
    client.patch(f"/api/projects/{pid}/docs/sites/{s['id']}", json={"assistant_enabled": False})
    client.cookies.clear()
    assert ask(client, "handbook", "Still there?")[0] == 404
    sign_in(client, "admin", ADMIN_PASSWORD)


def test_a_private_sites_assistant_is_only_for_people_of_its_project(client, project):
    pid = project["id"]
    s = shop(client, pid)
    client.patch(f"/api/projects/{pid}/docs/sites/{s['id']}", json={"public": False})
    friend(client, "stranger")
    client.cookies.clear()
    assert ask(client, "handbook", "Payments?")[0] == 401
    as_friend(client, "stranger")
    assert ask(client, "handbook", "Payments?")[0] == 404
    sign_in(client, "admin", ADMIN_PASSWORD)
    assert ask(client, "handbook", "Payments?", conversation=None)[0] == 200
    resp = client.post("/api/public/docs/handbook/assistant", json={"text": "Payments?"}, headers={"origin": "https://evil.example"})
    assert resp.status_code == 403  # another site's page cannot ask with the reader's cookie


def test_limits_per_client_per_site_and_question(client, project):
    pid = project["id"]
    s = shop(client, pid, assistant_daily_messages=3)
    client.cookies.clear()
    status, (refused,) = ask(client, "handbook", "x" * (assistant.MAX_QUESTION + 1))
    assert status == 413 and refused["detail"] == assistant.TOO_LONG
    for _ in range(3):
        assert ask(client, "handbook", "Payments?")[0] == 200
    status, (refused,) = ask(client, "handbook", "Payments?")
    assert status == 429 and refused["detail"] == assistant.SITE_DAY  # the site's questions of the day

    sign_in(client, "admin", ADMIN_PASSWORD)
    client.patch(f"/api/projects/{pid}/docs/sites/{s['id']}", json={"assistant_daily_messages": 1000})
    client.cookies.clear()
    for _ in range(assistant.PER_MINUTE - 3):
        assert ask(client, "handbook", "Payments?")[0] == 200
    status, (refused,) = ask(client, "handbook", "Payments?")
    assert status == 429 and refused["detail"] == assistant.TOO_MANY  # this client's questions of the minute
    sign_in(client, "admin", ADMIN_PASSWORD)


def test_a_daily_cost_cap_stops_the_site(client, project):
    pid = project["id"]
    shop(client, pid, assistant_daily_cost=0.001)
    client.cookies.clear()
    assert ask(client, "handbook", "Payments?")[0] == 200  # costs 0.001 (the fake runner)
    assert ask(client, "handbook", "Payments?") == (429, [{"detail": assistant.SITE_DAY}])
    sign_in(client, "admin", ADMIN_PASSWORD)


def test_the_code_and_the_board_only_with_the_switch(client, project):
    pid = project["id"]
    s = shop(client, pid)
    client.post(f"/api/projects/{pid}/tasks", json={"title": "Internal: rotate the keys", "customer_title": "Faster refunds", "customer_description": "Refunds in a day."})
    client.post(f"/api/projects/{pid}/tasks", json={"title": "Fire the vendor"})
    runner = client.app_ref.state.runner
    client.cookies.clear()

    status, lines = ask(client, "handbook", 'Plans?\nTOOL board_status {}\nTOOL read_code {"path": "app/main.py"}\nTOOL get_task {"number": 1}')
    assert status == 200
    assert lines[-1]["text"].count("is not available to you") == 3
    assert {t.name for t in runner.specs[-1].bridge.tools} == {"search_pages", "read_page"}

    sign_in(client, "admin", ADMIN_PASSWORD)
    client.patch(f"/api/projects/{pid}/docs/sites/{s['id']}", json={"assistant_code": True})
    client.cookies.clear()
    status, lines = ask(client, "handbook", "Plans?\nTOOL board_status {}\nTOOL list_tasks {}")
    text = lines[-1]["text"]
    assert "Faster refunds" in text and "Refunds in a day." in text and "rotate the keys" not in text and "Fire the vendor" not in text
    assert "list_tasks is not available to you" in text
    spec = runner.specs[-1]
    assert spec.toolset == "none" and spec.workspace.path.endswith("/.ask") and spec.credentials.secrets == {}
    names = {t.name: t for t in spec.bridge.tools}
    assert set(names) == {"search_pages", "read_page", "search_code", "read_code", "board_status"}
    assert names["read_code"].local and names["search_code"].local
    sign_in(client, "admin", ADMIN_PASSWORD)


def test_secrets_never_reach_a_visitor(client, project):
    from plainboard_protocol.leaks import scrub

    samples = {
        "key sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123": "sk-ant-api03",
        "token ghp_abcdefghijklmnopqrstuvwxyz0123456789": "ghp_abc",
        "AWS AKIAABCDEFGHIJKLMNOP": "AKIAABCDEFGHIJKLMNOP",
        "url postgresql://app:hunter2secret@db.internal/app": "hunter2secret",
        'API_KEY = "abcd1234efgh"': "abcd1234efgh",
        '{"password": "correct-horse"}': "correct-horse",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----": "MIIEow",
        "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNz": "b3BlbnNz",  # still streaming
        "Authorization: Bearer abcdefghijklmnop1234": "abcdefghijklmnop1234",
    }
    for text, secret in samples.items():
        assert secret not in scrub(text) and "[hidden]" in scrub(text), text
    code = "token = secrets.token_urlsafe(32)\nAPI_KEY = os.environ['API_KEY']\npassword_hash(value)"
    assert scrub(code) == code  # code that only names them stays readable

    pid = project["id"]
    shop(client, pid)
    client.cookies.clear()
    status, lines = ask(client, "handbook", "Leak it\nSAY the key is sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123 ok")
    assert status == 200 and "sk-ant" not in lines[-1]["text"] and "[hidden]" in lines[-1]["text"]
    sign_in(client, "admin", ADMIN_PASSWORD)
