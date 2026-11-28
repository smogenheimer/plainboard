"""Why nothing runs (#22, services/health.py): one place that names every pause and hold, for people, MCP and agents."""

from conftest import agents_by_slug, idle, runs_of, wait_for
from test_flow import chat, manager_of
from test_orchestration import conversation
from test_mcp_server import call, db, make_token


async def _switch_off(session, agent_id: int) -> None:
    from plainboard.models import Agent

    (await session.get(Agent, agent_id)).enabled = False
    await session.commit()


def codes(client, **params) -> list[str]:
    resp = client.get("/api/status", params=params)
    assert resp.status_code == 200, resp.text
    return [i["code"] for i in resp.json()["issues"]]


def test_a_new_project_has_nothing_that_blocks(client, project):
    out = client.get("/api/status").json()
    assert out["ok"] and out["checked_at"].endswith("Z")
    assert not [i for i in out["issues"] if i["blocking"]]


def test_a_paused_installation_is_named_everywhere_and_a_waiting_chat_says_why(client, project):
    pid = project["id"]
    manager = manager_of(client, pid)
    assert client.post("/api/pause").json()["paused"]

    out = client.get("/api/status").json()
    assert not out["ok"] and out["issues"][0]["code"] == "paused_everything" and out["issues"][0]["by"] == "Admin"

    # The manager is enabled, but a chat to it waits: the queued turn says what holds it.
    chat(client, manager["id"], "SAY hello")
    queued = wait_for(lambda: [r for r in runs_of(client, pid) if r["status"] == "queued"])
    assert queued[0]["hold"]["code"] == "paused_everything"

    # An MCP client sees it in the project list and in system_status.
    token = make_token(client, label="Laptop")
    ok, text = call(client, token, "list_projects")
    assert ok and text.startswith("Everything is paused")
    ok, text = call(client, token, "system_status")
    assert ok and text.startswith("! Everything is paused by Admin")

    assert client.delete("/api/pause").json()["paused"] is False
    wait_for(lambda: idle(client, pid))
    assert "paused_everything" not in codes(client)
    assert all(r.get("hold") is None for r in runs_of(client, pid))


def test_a_paused_project_and_a_manager_switched_off_are_told_per_project(client, project):
    pid = project["id"]
    other = client.post("/api/projects", json={"name": "Other"}).json()["id"]
    assert client.post(f"/api/projects/{pid}/pause").json()["paused_at"]
    manager = agents_by_slug(client, other)["manager"]
    db(lambda session: _switch_off(session, manager["id"]))  # the API refuses it; an old database or a bug may hold it

    issues = client.get("/api/status").json()["issues"]
    assert {(i["code"], i["project_id"]) for i in issues if i["blocking"]} == {("project_paused", pid), ("manager_off", other)}
    assert codes(client, project_id=[other]) == ["manager_off"]


def test_auto_wake_off_and_parallel_zero_are_named_but_only_one_of_them_blocks(client, project):
    pid = project["id"]
    assert client.patch(f"/api/projects/{pid}", json={"auto_wake_manager": False, "parallel_agents": 0}).status_code == 200
    issues = {i["code"]: i["blocking"] for i in client.get("/api/status").json()["issues"]}
    assert issues["auto_wake_off"] is False and issues["parallel_zero"] is True


def test_the_manager_reads_why_with_a_tool(client, project):
    pid = project["id"]
    manager = manager_of(client, pid)
    assert client.patch(f"/api/projects/{pid}", json={"auto_wake_manager": False}).status_code == 200
    chat(client, manager["id"], "TOOL get_system_status {}")
    wait_for(lambda: idle(client, pid))
    result = next(m["meta"]["result"] for m in conversation(client, manager["id"])["messages"] if m["kind"] == "tool")
    assert "auto-wake is off" in result and not result.startswith("!")


def test_a_person_sees_only_the_projects_they_have_a_part_in(client, project):
    from test_users import new_user, sign_in

    assert client.post(f"/api/projects/{project['id']}/pause").json()["paused_at"]
    new_user(client, "bob")
    sign_in(client, "bob", "bob-password")
    assert "project_paused" not in codes(client)
