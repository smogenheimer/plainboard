import json
import time

from conftest import agents_by_slug, idle, runs_of, wait_for
from plainboard_protocol import LimitWindow
from test_accounts import accounts
from test_flow import flow_events
from test_orchestration import chat, make_dev, runner


def record(client, resets_in: int = 3600, **used: float) -> None:
    """A five_hour window per account label: how full it is, resetting in ``resets_in`` seconds."""
    from plainboard.db import sessionmaker
    from plainboard.models import Account
    from plainboard.services import limits as limits_service
    from sqlalchemy import select

    async def go() -> None:
        async with sessionmaker()() as s:
            for label, value in used.items():
                key = await s.scalar(select(Account.key).where(Account.label == label))
                await limits_service.record(s, key, [LimitWindow(name="five_hour", utilization=value, resets_at=int(time.time()) + resets_in)])

    client.portal.call(go)


def two_subscriptions(client) -> None:
    client.patch(f"/api/accounts/{accounts(client)['Claude']['id']}", json={"auth_mode": "oauth_token", "secret": "tok-a"})
    client.post("/api/accounts", json={"provider": "claude_code", "label": "Second", "auth_mode": "oauth_token", "secret": "tok-b"})


def tick(client) -> tuple[int, int]:
    return client.portal.call(client.app_ref.state.limit_pause.tick)


def test_a_project_pauses_before_its_subscriptions_run_out_and_goes_on_when_one_has_room(client, project):
    pid = project["id"]
    two_subscriptions(client)
    make_dev(client, pid)
    manager = agents_by_slug(client, pid)["manager"]
    fake = runner(client)
    record(client, Claude=0.5, Second=0.97)
    assert tick(client) == (0, 0)  # one still has room

    # A member works on an assignment when the last one gets nearly full: its turn wraps up, nothing is lost.
    chat(client, manager["id"], "TOOL delegate " + json.dumps({"agent": "dev", "title": "Endpoints", "instructions": "HOLD\nSAY API done"}))
    wait_for(lambda: any(r["status"] == "running" and r["assignment_id"] for r in runs_of(client, pid)))
    record(client, Claude=0.96)
    assert tick(client) == (1, 0)
    assert tick(client) == (0, 0)  # once
    body = client.get(f"/api/projects/{pid}").json()
    assert body["limit_paused_at"] and body["limit_resumes_at"] and body["paused_at"] is None  # not the person's pause
    wait_for(lambda: any("You are being paused" in text and "nearly used up" in text and "leave your background jobs running" in text for _, text in fake.notices))
    fake.released = True
    wait_for(lambda: not any(r["status"] == "running" for r in runs_of(client, pid)))
    (assignment,) = client.get(f"/api/projects/{pid}/assignments").json()
    assert assignment["status"] != "failed"

    # No new turn of the project starts, but the user's chat is answered; the manager reads the note with it.
    specs = len(fake.specs)
    chat(client, manager["id"], "SAY hello")
    wait_for(lambda: len(fake.specs) == specs + 1 and not any(r["status"] == "running" for r in runs_of(client, pid)))
    assert "PlainBoard paused this project by itself" in fake.specs[-1].prompt
    client.portal.call(client.app_ref.state.runs._wake.set)
    time.sleep(0.2)
    assert len(fake.specs) == specs + 1

    # One has room again: the project goes on by itself, and the manager hears of it.
    record(client, Second=0.2)
    assert tick(client) == (0, 1)
    body = client.get(f"/api/projects/{pid}").json()
    assert body["limit_paused_at"] is None and body["limit_resumes_at"] is None
    wait_for(lambda: idle(client, pid) and client.get(f"/api/projects/{pid}/assignments").json()[0]["status"] == "done")
    assert any("has room again" in s.prompt for s in fake.specs[specs + 1 :])
    kinds = [e["kind"] for e in flow_events(client, pid) if e["kind"].startswith("project.")]
    assert kinds == ["project.limit_paused", "project.limit_resumed"]


def test_the_limit_pause_ends_with_the_reset_and_never_holds_with_an_api_key(client, project):
    pid = project["id"]
    two_subscriptions(client)
    record(client, resets_in=2, Claude=0.99, Second=0.96)
    assert tick(client) == (1, 0)
    resumes = client.get(f"/api/projects/{pid}").json()["limit_resumes_at"]
    assert resumes and abs(time.time() + 2 - time.mktime(time.strptime(resumes[:19], "%Y-%m-%dT%H:%M:%S")) + time.timezone) < 5
    wait_for(lambda: tick(client) == (0, 1), timeout=6)  # both windows reset

    record(client, Claude=0.99, Second=0.96)
    assert tick(client) == (1, 0)
    client.post("/api/accounts", json={"provider": "claude_code", "label": "Paid", "auth_mode": "api_key", "secret": "sk-paid"})
    assert tick(client) == (0, 1)  # an API key has no plan limit
    assert tick(client) == (0, 0)


def test_a_project_that_switched_it_off_never_pauses_before_its_limits(client, project):
    pid = project["id"]
    two_subscriptions(client)
    assert client.get(f"/api/projects/{pid}").json()["limit_pause"] is True  # on by default
    record(client, Claude=0.99, Second=0.96)
    assert tick(client) == (1, 0)
    assert client.patch(f"/api/projects/{pid}", json={"limit_pause": False}).json()["limit_pause"] is False
    assert tick(client) == (0, 1)  # as before: it goes on
    assert tick(client) == (0, 0)
    assert client.get(f"/api/projects/{pid}").json()["limit_paused_at"] is None
