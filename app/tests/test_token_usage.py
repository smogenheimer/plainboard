"""Tokens and cost per project to compare them (GET /api/usage/tokens, services/stats.tokens)."""

from datetime import timedelta

from conftest import ADMIN_PASSWORD, agents_by_slug
from test_permissions import as_friend, friend, sign_in


def add_runs(client, pid: int, *runs: tuple[float, int, int, float]) -> None:
    """Runs of the project's manager: (days ago, input tokens, output tokens, cost)."""
    from plainboard.db import sessionmaker
    from plainboard.models import Run, utcnow

    agent = agents_by_slug(client, pid)["manager"]["id"]

    async def go() -> None:
        async with sessionmaker()() as s:
            for days, inp, out, cost in runs:
                at = utcnow() - timedelta(days=days)
                s.add(Run(project_id=pid, agent_id=agent, trigger="chat", status="done", input_tokens=inp, output_tokens=out, cache_read_tokens=7, cost_usd=cost, created_at=at))
            await s.commit()

    client.portal.call(go)


def test_admins_compare_all_projects_and_everyone_else_only_their_own(client, project):
    other = client.post("/api/projects", json={"name": "Other"}).json()
    quiet = client.post("/api/projects", json={"name": "Quiet"}).json()
    add_runs(client, project["id"], (0, 100, 10, 1.0), (2, 50, 5, 0.5), (40, 999, 999, 9.0))
    add_runs(client, other["id"], (1, 1000, 100, 3.0))
    del quiet  # no runs: not listed

    data = client.get("/api/usage/tokens?days=7").json()
    assert len(data["days"]) == 7
    assert [p["name"] for p in data["projects"]] == ["Other", "Demo Project"]
    demo = data["projects"][1]
    assert (demo["input_tokens"], demo["output_tokens"], demo["cache_read_tokens"], demo["runs"], demo["cost_usd"]) == (150, 15, 14, 2, 1.5)
    assert demo["tokens"][-1] == 110 and demo["tokens"][-3] == 55 and sum(demo["tokens"]) == 165

    user = friend(client)
    assert client.put(f"/api/projects/{other['id']}/members/{user['id']}", json={"role": "viewer"}).status_code == 200
    as_friend(client)
    assert [p["name"] for p in client.get("/api/usage/tokens").json()["projects"]] == ["Other"]
    assert client.get("/api/usage").status_code == 403  # the machine stays the admins'
    sign_in(client, "admin", ADMIN_PASSWORD)
