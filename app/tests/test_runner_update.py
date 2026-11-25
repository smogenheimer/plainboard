"""External runners update themselves: when, and what the managers hear."""

from __future__ import annotations

import asyncio
import io
import json
import tarfile
import time

import pytest
from conftest import FakeRunner, agents_by_slug, runs_of, wait_for

from plainboard.orchestration.runner_client import UpdateRefused
from plainboard.services import runner_update
from plainboard.services.runner_update import DRAIN_SECONDS, FAILED, IDLE, RESTART, STAGE, WAIT, step

OWN, OLD = "a" * 40, "b" * 40


def test_a_runner_updates_only_when_its_version_differs_and_only_once_it_is_free():
    assert step(OWN, OWN, False, False, None, 0, 0, 100) == IDLE
    assert step("", OLD, False, False, None, 0, 0, 100) == IDLE  # the app does not know its own version
    assert step(OWN, "", False, False, None, 0, 0, 100) == IDLE
    assert step(OWN, OLD, False, False, None, 3, 2, 100) == STAGE  # staging touches nothing that runs
    assert step(OWN, OLD, False, False, 100, 1, 0, 100 + DRAIN_SECONDS - 1) == WAIT  # running turns get their time
    assert step(OWN, OLD, False, False, 100, 0, 2, 100 + DRAIN_SECONDS - 1) == WAIT  # jobs too
    assert step(OWN, OLD, False, False, 100, 1, 2, 100 + DRAIN_SECONDS) == RESTART  # then it goes ahead, whatever runs
    assert step(OWN, OLD, False, False, 100, 1, 0, 160, limit=60) == RESTART  # the limit is a setting
    assert step(OWN, OLD, False, False, 100, 0, 0, 101) == RESTART
    assert step(OWN, OLD, True, False, None, 0, 0, 100) == FAILED
    assert step(OWN, OLD, True, True, None, 0, 0, 100) == IDLE  # no loop: told, then left alone
    assert step(OWN, OLD, False, True, None, 0, 0, 100) == IDLE


def test_the_bundle_holds_the_runners_code_of_this_version_with_its_checksum():
    bundle = runner_update.bundle(OWN)
    assert bundle is not None and bundle.commit == OWN
    import hashlib

    assert hashlib.sha256(bundle.data).hexdigest() == bundle.sha256
    names = tarfile.open(fileobj=io.BytesIO(bundle.data), mode="r:gz").getnames()
    assert {"COMMIT", "plainboard_runner/selfupdate.py", "plainboard_protocol/tunnel.py", "meta/Dockerfile", "meta/launcher.py", "meta/runner.toml"} <= set(names)
    assert not [n for n in names if "__pycache__" in n]


class Updatee(FakeRunner):
    def __init__(self, refuse: str = "") -> None:
        super().__init__("tunnel")
        self.refuse = refuse
        self.staged: list[str] = []
        self.restarted: list[str] = []
        self.cut: list[int] = []  # turns the restart ended

    async def _events(self, spec):
        """CUT: the turn works until the runner restarts, then the runner no longer has it."""
        if "CUT" not in spec.prompt.splitlines() or self.cut:
            async for event in super()._events(spec):
                yield event
            return
        from plainboard.orchestration.runner_client import RunnerStreamBroken
        from plainboard_protocol import AssistantTextEvent, SessionEvent

        self.specs.append(spec)
        yield SessionEvent(session_id=f"session-{spec.run_id}")
        yield AssistantTextEvent(text="working on it")
        while not self.restarted:
            await asyncio.sleep(0.01)
        self.cut.append(int(spec.run_id))
        raise RunnerStreamBroken("The runner no longer has this turn (404): it was started anew", 2, lost=True)

    async def active_runs(self) -> set[int]:
        return set()

    async def stage_update(self, data: bytes, sha256: str, commit: str, timeout: float = 90) -> None:
        if self.refuse:
            raise UpdateRefused(self.refuse)
        self.staged.append(commit)

    async def restart_update(self, commit: str) -> None:
        self.restarted.append(commit)


def _setup(client, project, monkeypatch, runner):
    from plainboard.services import version as version_service

    monkeypatch.setattr(version_service, "running_commit", lambda: OWN)
    hub = client.app_ref.state.runs.runners
    hub.make_client = lambda _rid: runner
    claude = next(a for a in client.get("/api/accounts").json() if a["provider"] == "claude_code")
    client.patch(f"/api/accounts/{claude['id']}", json={"auth_mode": "oauth_token", "secret": "tok"})  # the home's sign-in is the built-in runner's
    created = client.post("/api/runners", json={"name": "build-server"}).json()
    rid = created["runner"]["id"]
    assert client.put(f"/api/projects/{project['id']}/runner", json={"runner_id": rid}).status_code == 200
    runs = client.app_ref.state.runs

    async def no_turn(*_args) -> None:
        pass

    monkeypatch.setattr(runs, "request_wake", no_turn)
    return created, rid, runs


def _notes(client, agent_id: int) -> list[str]:
    from plainboard.db import sessionmaker
    from plainboard.models import InboxItem
    from plainboard.services.runners import MOVED_NOTE
    from sqlalchemy import select

    async def read() -> list[str]:
        async with sessionmaker()() as session:
            return list(await session.scalars(select(InboxItem.text).where(InboxItem.agent_id == agent_id, InboxItem.text != MOVED_NOTE)))

    return client.portal.call(read)


def test_a_runner_with_another_version_takes_the_update_runs_idle_and_restarts_once_free(client, project, monkeypatch):
    runner = Updatee()
    created, rid, runs = _setup(client, project, monkeypatch, runner)
    manager = agents_by_slug(client, project["id"])["manager"]
    with client.websocket_connect("/api/runners/connect", headers={"authorization": f"Bearer {created['token']}"}) as ws:
        ws.send_text(json.dumps({"t": "hello", "version": OLD, "protocol": 1, "instance": "i1"}))
        wait_for(lambda: runs.runners.connected(rid))
        client.portal.call(runs.updates.look)
        assert runner.staged == [OWN] and not runs.runners.accepts(project["id"])
        assert next(r for r in client.get("/api/runners").json() if r["id"] == rid)["updating"]

        # Drained: a new turn waits in the queue, nothing reaches the runner.
        assert client.post(f"/api/agents/{manager['id']}/messages", json={"text": "SAY hello"}).status_code == 202
        time.sleep(0.3)
        assert [r["status"] for r in runs_of(client, project["id"])] == ["queued"] and runner.specs == []

        client.portal.call(runs.updates.look)  # nothing runs: it restarts
        assert runner.restarted == [OWN] and _notes(client, manager["id"]) == []


def test_a_turn_still_running_after_the_drain_limit_is_cut_and_its_assignment_goes_on_once_the_runner_is_back(client, project, monkeypatch):
    import test_orchestration as orch

    runner = Updatee()
    created, rid, runs = _setup(client, project, monkeypatch, runner)
    pid = project["id"]
    dev = orch.make_dev(client, pid)
    manager = agents_by_slug(client, pid)["manager"]
    auth = {"authorization": f"Bearer {created['token']}"}
    with client.websocket_connect("/api/runners/connect", headers=auth) as ws:
        ws.send_text(json.dumps({"t": "hello", "version": OLD, "protocol": 1, "instance": "i1"}))
        wait_for(lambda: runs.runners.connected(rid))
        orch.chat(client, manager["id"], "TOOL delegate " + json.dumps({"agent": "dev", "title": "Work", "instructions": "CUT"}))
        turn = wait_for(lambda: next((r for r in runs_of(client, pid, agent_id=dev["id"]) if r["status"] == "running"), None))
        wait_for(lambda: idle_manager(client, pid, manager["id"]))

        client.portal.call(runs.updates.look)  # staged, drained
        client.portal.call(runs.updates.look)  # the turn runs: it waits
        assert runner.staged == [OWN] and runner.restarted == []
        shown = next(r for r in client.get("/api/runners").json() if r["id"] == rid)
        assert shown["updating"] and shown["restart_by"]

        runs.updates._draining[rid] -= runner_update.DRAIN_SECONDS  # 5 minutes later
        client.portal.call(runs.updates.look)
        assert runner.restarted == [OWN]
        wait_for(lambda: runner.cut == [turn["id"]])
        lost = wait_for(lambda: next((r for r in runs_of(client, pid, agent_id=dev["id"]) if r["id"] == turn["id"] and r["status"] != "running"), None))
        assert lost["status"] == "failed" and lost["error"] == "The turn broke off: the runner no longer had it"
        assert client.get(f"/api/projects/{pid}/assignments").json()[0]["status"] == "running"  # not failed: it goes on
        time.sleep(0.2)
        assert [int(s.run_id) for s in runner.specs][-1] == turn["id"]  # the runner is not back yet: its next turn waits

        # Back with the new version: the assignment goes on in the same session.
        with client.websocket_connect("/api/runners/connect", headers=auth) as ws2:
            ws2.send_text(json.dumps({"t": "hello", "version": OWN, "protocol": 1, "instance": "i2"}))
            wait_for(lambda: client.get(f"/api/projects/{pid}/assignments").json()[0]["status"] == "done")
    again = runner.specs[-1]
    assert int(again.run_id) != turn["id"] and "Your last turn broke off" in again.prompt and again.resume_session_id == f"session-{turn['id']}"


def idle_manager(client, pid: int, manager_id: int) -> bool:
    return all(r["status"] not in ("queued", "running") for r in runs_of(client, pid, agent_id=manager_id))


def test_a_runner_that_cant_take_the_update_is_left_alone_and_the_manager_hears_it_once(client, project, monkeypatch):
    runner = Updatee(refuse="the new version needs a new image (its Dockerfile changed)")
    created, rid, runs = _setup(client, project, monkeypatch, runner)
    manager = agents_by_slug(client, project["id"])["manager"]
    with client.websocket_connect("/api/runners/connect", headers={"authorization": f"Bearer {created['token']}"}) as ws:
        ws.send_text(json.dumps({"t": "hello", "version": OLD, "protocol": 1, "instance": "i1"}))
        wait_for(lambda: runs.runners.connected(rid))
        client.portal.call(runs.updates.look)
        client.portal.call(runs.updates.look)
        assert runs.runners.accepts(project["id"]) and runner.restarted == []
    notes = _notes(client, manager["id"])
    assert len(notes) == 1 and "docker compose pull" in notes[0] and "Dockerfile" in notes[0]


@pytest.mark.parametrize("reported", [True, False])
def test_an_update_that_did_not_come_up_is_told_once_and_not_tried_again(client, project, monkeypatch, reported):
    runner = Updatee()
    created, rid, runs = _setup(client, project, monkeypatch, runner)
    manager = agents_by_slug(client, project["id"])["manager"]
    update = {"failed": {"commit": OWN, "reason": "ended before it reached PlainBoard (exit code 1)", "at": time.time()}} if reported else {}
    if not reported:
        runs.updates._restarted[rid] = OWN  # it was asked to restart into OWN and came back with OLD
    with client.websocket_connect("/api/runners/connect", headers={"authorization": f"Bearer {created['token']}"}) as ws:
        ws.send_text(json.dumps({"t": "hello", "version": OLD, "protocol": 1, "instance": "i2", "update": update}))
        wait_for(lambda: runs.runners.connected(rid))
        client.portal.call(runs.updates.look)
        client.portal.call(runs.updates.look)
        assert runner.staged == [] and runs.runners.accepts(project["id"])
    notes = _notes(client, manager["id"])
    assert len(notes) == 1 and "could not update itself" in notes[0]
