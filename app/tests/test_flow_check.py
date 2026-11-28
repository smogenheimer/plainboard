"""PlainBoard's own checks of the flow find what slows the team down, without a model."""

import json
from datetime import timedelta

from conftest import agents_by_slug, idle, wait_for
from plainboard_protocol import JobTiming, PullRequestCheck
from test_flow import chat, delegate, finish_check, hand_over, land_tick, land_tick_at, make_member, manager_of, team_with_branches, tool_results


def found(client, pid: int) -> dict[str, dict]:
    return {f["key"]: f for f in client.get(f"/api/projects/{pid}/flow-check?hours=24").json()}


def test_a_quiet_project_has_no_findings(client, project):
    pid = project["id"]
    assert found(client, pid) == {}
    chat(client, manager_of(client, pid)["id"], "TOOL flow_check")
    wait_for(lambda: idle(client, pid))
    assert tool_results(client, manager_of(client, pid)["id"])[-1] == "PlainBoard's checks found nothing in the last 24 h that slows the team down."


def test_a_base_branch_that_stays_red_and_the_checks_that_keep_failing_are_found(client, project, lands_on_red):
    from plainboard.models import utcnow

    pid = project["id"]
    fake = team_with_branches(client, pid, ["feat-a"], url="https://github.com/acme/api.git", check_command="", land_first=True)
    red = [PullRequestCheck(name="Tests", state="fail", link="https://ci/t"), PullRequestCheck(name="Emulator", state="fail")]
    start = utcnow() - timedelta(hours=3)
    for batch in range(1, 5):  # four landings, each red
        land_tick_at(client, start + timedelta(minutes=30 * batch))
        fake.commit_checks_of[f"train-{batch}"] = red
        hand_over(client, pid, fake, f"More{batch}", f"more-{batch}")
    land_tick_at(client, utcnow())
    wait_for(lambda: idle(client, pid))
    findings = found(client, pid)
    base = findings["red_base"]
    assert base["title"] == "main of api has failed its checks for 4 landings in a row" and "Tests (4x), Emulator (4x)" in base["detail"] and "Repairs so far:" in base["detail"]
    failing = findings["failing_checks"]
    assert failing["title"] == "4 of 4 check runs of api failed" and "Most often: Tests (4x), Emulator (4x). It has no quick check." in failing["detail"]
    assert "quick_check_command" in failing["action"]
    assert list(findings)[:2] == ["red_base", "failing_checks"]  # heaviest first
    # The manager did not have to ask: it read this with a turn it had anyway, when the fourth run failed,
    # and once, not with every failure after that. (Seen: it heard it at the nightly tidy-up, after the night.)
    told = [s.prompt for s in fake.specs if "check runs of api failed in the last 24 h" in s.prompt]
    assert len(told) == 1 and "4 of 4 check runs of api failed in the last 24 h. Most often: Tests (4x), Emulator (4x). It has no quick check." in told[0]
    assert "worth one assignment" in told[0]

    # The manager gets the same, as text it can act on.
    chat(client, manager_of(client, pid)["id"], "TOOL flow_check")
    wait_for(lambda: idle(client, pid))
    text = tool_results(client, manager_of(client, pid)["id"])[-1]
    assert text.startswith("PlainBoard's checks of the last 24 h, heaviest first:\n1. main of api has failed its checks for 4 landings in a row\n   Red for ")
    assert "\n   → Work that starts now builds on a broken base." in text


def test_repairs_that_wait_stuck_branches_and_full_notes_are_found_and_a_branch_can_be_forgotten(client, project):
    from plainboard.db import sessionmaker
    from plainboard.models import AgentNote, Assignment, utcnow
    from sqlalchemy import update

    pid = project["id"]
    fake = team_with_branches(client, pid, ["feat-a", "feat-b"])
    dev2, manager = agents_by_slug(client, pid)["dev2"], manager_of(client, pid)
    # feat-b conflicts; its author is in the middle of a long turn, so the repair waits behind that.
    chat(client, manager["id"], delegate("dev2", "Long work", "HOLD"))
    wait_for(lambda: any(a["agent_id"] == dev2["id"] and a["status"] == "running" for a in client.get(f"/api/projects/{pid}/assignments").json()))
    fake.conflicting = {"feat-b"}
    client.portal.call(client.app_ref.state.landing.tick)
    wait_for(lambda: len(client.get(f"/api/projects/{pid}/assignments").json()) == 2)  # the repair is queued: it waits behind the long work

    async def age_everything() -> None:
        async with sessionmaker()() as s:
            await s.execute(update(Assignment).values(created_at=utcnow() - timedelta(hours=4)))
            s.add(AgentNote(project_id=pid, agent_id=dev2["id"], text="x" * 2800, created_by_agent_id=dev2["id"]))  # its own: pushed out when needed
            s.add(AgentNote(project_id=pid, agent_id=manager["id"], text="y" * 1600))  # the user's: never
            await s.commit()

    client.portal.call(age_everything)
    findings = found(client, pid)
    assert findings["waiting_repairs"]["title"] == "1 repair for landing waits behind other work" and "(Dev2, 4 h)" in findings["waiting_repairs"]["detail"]
    assert findings["long_assignments"]["title"] == "1 assignment has been open for more than 3 h" and '"Long work"' in findings["long_assignments"]["detail"]
    assert findings["full_notes"]["detail"].startswith("Manager (1600 of 3000) characters.")

    # A branch PlainBoard stopped for: it is found, and the manager takes it off the list when its work landed another way.
    fake.branches["old-work"] = "sha-old"
    fake.conflicting.add("old-work")
    chat(client, manager["id"], "TOOL merge_when_green " + json.dumps({"repository": "api", "branch": "old-work"}))
    wait_for(lambda: "old-work" in tool_results(client, manager["id"])[-1])
    fake.jobs[fake.check_specs[-1].id] = fake.jobs[fake.check_specs[-1].id].model_copy(update={"running": False, "exit_code": 0})
    client.portal.call(client.app_ref.state.landing.tick)
    wait_for(lambda: "stuck" in found(client, pid))
    assert found(client, pid)["stuck"]["title"] == "1 branch is stuck: PlainBoard has stopped for it" and "forget_branch" in found(client, pid)["stuck"]["action"]
    chat(client, manager["id"], "TOOL forget_branch " + json.dumps({"repository": "api", "branch": "old-work"}))
    wait_for(lambda: "off the list" in tool_results(client, manager["id"])[-1])
    assert "Branch old-work is off the list (it was stuck)" in tool_results(client, manager["id"])[-1] and "stuck" not in found(client, pid)
    fake.released = True
    wait_for(lambda: idle(client, pid))


def test_refused_calls_and_a_slow_quick_check_are_found(client, project):
    pid = project["id"]
    body = {"url": "https://gitlab.com/acme/api.git", "check_command": "./ci.sh", "quick_check_command": "cargo check"}
    assert client.post(f"/api/projects/{pid}/repositories", json=body).status_code == 201
    dev = make_member(client, pid)
    # The same call refused again and again: each one a step for nothing.
    chat(client, dev["id"], "\n".join(["TOOL get_todo " + json.dumps({"todo_id": 999})] * 6))
    wait_for(lambda: idle(client, pid))
    errors = found(client, pid)["tool_errors"]
    assert errors["title"] == "6 calls of PlainBoard's tools were refused" and errors["detail"].startswith("get_todo 6x (mostly: ")

    # A NUL character in a tool's output (#120): stored without it, and a row that already holds one (\u0000 in
    # its JSON, which breaks every ->> on PostgreSQL) doesn't stop the check.
    from sqlalchemy import Text, select, text

    from plainboard.db import sessionmaker
    from plainboard.models import Conversation, Message, utcnow

    async def nul_results() -> tuple[str, dict]:
        async with sessionmaker()() as s:
            conv = await s.scalar(select(Conversation).where(Conversation.agent_id == dev["id"]))
            msg = Message(conversation_id=conv.id, role="assistant", kind="tool", content="get_todo\x00", meta={"result": "a\x00b", "is_error": True})
            s.add(msg)
            meta = json.dumps({"name": "get_todo", "result": "old\x00 output", "is_error": True})
            assert "\\u0000" in meta
            json_value = "CAST(:meta AS JSON)" if s.bind.dialect.name == "postgresql" else ":meta"
            await s.execute(
                text(f"INSERT INTO messages (conversation_id, role, kind, content, meta, created_at) VALUES (:conv, 'assistant', 'tool', 'get_todo', {json_value}, :at)"),
                {"conv": conv.id, "meta": meta, "at": utcnow()},
            )
            await s.commit()
            raw = await s.scalar(select(Message.meta.cast(Text)).where(Message.id == msg.id))
            content = await s.scalar(select(Message.content).where(Message.id == msg.id))
            return raw + content, (await s.get(Message, msg.id)).meta

    raw, meta = client.portal.call(nul_results)
    assert "\\u0000" not in raw and "\x00" not in raw and meta["result"] == "a\x00b"  # the session's own object is as it was given
    assert found(client, pid)["tool_errors"]["title"] == "8 calls of PlainBoard's tools were refused"

    # The job that runs a repository's quick check says what it is, so its time can be measured.
    from plainboard.models import Job
    from plainboard.services.flow_check import QUICK_CHECK_MARK
    from plainboard.tools.flow import quick_check_script

    script = quick_check_script("api", "feat-a", "abc1234", "cargo check")
    assert script.startswith(QUICK_CHECK_MARK + "api\n")

    async def three_slow_runs() -> None:
        async with sessionmaker()() as s:
            for n in range(3):
                s.add(Job(project_id=pid, agent_id=dev["id"], runner_id=f"q{n}", command=script, status="succeeded", created_at=utcnow() - timedelta(minutes=10), finished_at=utcnow() - timedelta(minutes=6)))
            await s.commit()

    client.portal.call(three_slow_runs)
    assert found(client, pid)["slow_quick_check"]["title"] == "The quick check of api is not quick: 4 min at the median"


def seed_trains(client, repo_id: int, seconds: list[int], days_ago: float = 0.5) -> None:
    """Check runs that finished earlier, the first one the newest."""
    from plainboard.db import sessionmaker
    from plainboard.models import Repository, Train, utcnow

    async def add() -> None:
        async with sessionmaker()() as s:
            repo = await s.get(Repository, repo_id)
            for n, took in enumerate(seconds):
                done = utcnow() - timedelta(days=days_ago, minutes=n)
                s.add(Train(project_id=repo.project_id, repository_id=repo_id, mode="command", status="landed", size=1, check_seconds=took, created_at=done, finished_at=done))
            await s.commit()

    client.portal.call(add)


def test_slow_check_runs_are_found_and_the_manager_has_them_made_faster_once(client, project):
    pid = project["id"]
    fake = team_with_branches(client, pid, ["feat-a"], url="https://github.com/acme/api.git")
    repo_id = client.get(f"/api/projects/{pid}/repositories").json()[0]["id"]
    seed_trains(client, repo_id, [600] * 6)
    slow = found(client, pid)["slow_checks"]  # no fixed limit: the first look at a repository whose checks nobody made faster yet
    assert slow["title"] == "Check runs of api take 10 min at the median, never looked at for speed" and 'one ticket "Speed up checks for api"' in slow["action"]

    # A train lands: the manager reads it with its next turn, with where the time goes, and may act on it unasked.
    fake.job_timings_of["train-1"] = [JobTiming(name="CI / tests", seconds=480, steps=[("pytest", 420), ("install", 50)]), JobTiming(name="CI / lint", seconds=60)]
    land_tick(client)
    finish_check(client, fake)
    told = [s.prompt for s in fake.specs if "Speed up checks for api" in s.prompt]
    assert len(told) == 1 and "Check runs of api take 10 min at the median, never looked at for speed." in told[0]
    assert "Slowest jobs of the last run: CI / tests 8 min (pytest 7 min, install 50 s); CI / lint 60 s." in told[0]
    assert "without the user asking" in told[0] and "no test or check removed, skipped or weakened" in told[0] and "checks_baseline_minutes" in told[0]

    # The next train: not again within the cooldown.
    hand_over(client, pid, fake, "Dev2", "feat-b")
    land_tick(client)
    finish_check(client, fake)
    assert len([s for s in fake.specs if "Speed up checks for api" in s.prompt]) == 1

    # The member's verdict, stored by the manager, is the baseline from now on; an owner may set a target.
    chat(client, manager_of(client, pid)["id"], "TOOL update_repository " + json.dumps({"repository": "api", "checks_baseline_minutes": 9.5, "checks_verdict": "near the limit: 10 min is what it takes"}))
    wait_for(lambda: idle(client, pid))
    assert client.patch(f"/api/repositories/{repo_id}", json={"check_target_seconds": 480}).status_code == 200
    repo = client.get(f"/api/projects/{pid}/repositories").json()[0]
    assert (repo["checks_baseline_seconds"], repo["checks_verdict"], repo["check_target_seconds"]) == (570, "near the limit: 10 min is what it takes", 480)
    assert "slow_checks" not in found(client, pid)  # 10 min is within 30 % of it, and over a target it was never under

    # Switched off: no finding, nothing told.
    assert client.patch(f"/api/projects/{pid}", json={"fast_checks": False}).json()["fast_checks"] is False
    assert "slow_checks" not in found(client, pid)


def test_slow_checks_grow_against_the_baseline_and_come_once_per_cooldown(client, project):
    from plainboard.db import sessionmaker
    from plainboard.models import Repository, Task, utcnow
    from plainboard.services import flow_check
    from plainboard.services import settings as settings_service
    from sqlalchemy import update

    pid = project["id"]
    assert client.post(f"/api/projects/{pid}/repositories", json={"url": "https://gitlab.com/acme/api.git", "check_command": "./ci.sh"}).status_code == 201
    repo_id = client.get(f"/api/projects/{pid}/repositories").json()[0]["id"]

    def check(**told) -> tuple:
        async def run() -> tuple:
            async with sessionmaker()() as s:
                repo = await s.get(Repository, repo_id)
                for key, value in told.items():
                    setattr(repo, key, value)
                await s.commit()
                finding, seconds = await flow_check.slow_checks(s, repo, utcnow())
                return finding.title if finding else None, seconds, await flow_check.slow_checks_due(s, repo, seconds, utcnow())

        return client.portal.call(run)

    seed_trains(client, repo_id, [200] * 4)
    assert check()[0] is None  # too few runs to say
    seed_trains(client, repo_id, [100] * 10, days_ago=1)
    seed_trains(client, repo_id, [200] * 6, days_ago=0.1)
    seed_trains(client, repo_id, [50] * 20, days_ago=20)  # too old to count
    assert check() == ("Check runs of api take 3 min at the median, up from 2 min", 200, True)  # +100 % against the runs before

    # The member's verdict is the baseline: after it, only clear growth beyond it counts.
    now = utcnow()
    assert check(slow_told_at=now - timedelta(hours=13), slow_told_seconds=200, checks_baseline_seconds=190, checks_baseline_at=now - timedelta(hours=12), checks_verdict="near the limit: 3 min is what it takes")[0] is None
    assert check(check_target_seconds=120)[0] is None  # a target under what it takes changes nothing after that verdict
    assert check(check_target_seconds=0, checks_baseline_seconds=150)[0] == "Check runs of api take 3 min at the median, up from 2 min (its baseline)"
    assert check()[2] is True  # told before the verdict: grown beyond the verdict counts at once

    async def growth(percent: int) -> None:
        async with sessionmaker()() as s:
            await settings_service.update(s, {"checks_growth_percent": percent})
            await s.commit()

    client.portal.call(growth, 50)
    assert check()[0] is None  # tunable: +33 % is not enough now
    client.portal.call(growth, 30)
    assert check(checks_baseline_seconds=None, checks_baseline_at=None, checks_verdict="", slow_told_seconds=180)[2] is False  # told a day ago, not grown since

    async def ticket(status: str, days_ago: float) -> None:
        async with sessionmaker()() as s:
            s.add(Task(project_id=pid, number=900, title="Speed up checks for api", status=status, updated_at=utcnow() - timedelta(days=days_ago)))
            await s.commit()

    async def done(days_ago: float) -> None:
        async with sessionmaker()() as s:
            await s.execute(update(Task).where(Task.project_id == pid).values(status="done", updated_at=utcnow() - timedelta(days=days_ago)))
            await s.commit()

    client.portal.call(ticket, "todo", 0)
    assert check(slow_told_at=utcnow() - timedelta(days=30))[0] and check()[2] is False  # one is open: no second one
    client.portal.call(done, 1)
    assert check()[2] is False  # done a day ago: the cooldown
    client.portal.call(done, 8)
    assert check()[2] is True  # and over


def test_slow_checks_count_only_the_runs_after_the_verdict(client, project):
    from plainboard.db import sessionmaker
    from plainboard.models import Repository, utcnow
    from plainboard.services import flow_check

    pid = project["id"]
    assert client.post(f"/api/projects/{pid}/repositories", json={"url": "https://gitlab.com/acme/api.git", "check_command": "./ci.sh"}).status_code == 201
    repo_id = client.get(f"/api/projects/{pid}/repositories").json()[0]["id"]

    def check(**told) -> tuple:
        async def run() -> tuple:
            async with sessionmaker()() as s:
                repo = await s.get(Repository, repo_id)
                for key, value in told.items():
                    setattr(repo, key, value)
                await s.commit()
                finding, seconds = await flow_check.slow_checks(s, repo, utcnow())
                return finding.title if finding else None, seconds, await flow_check.slow_checks_due(s, repo, seconds, utcnow())

        return client.portal.call(run)

    # Slow runs, then a speed-up: told once before it, the verdict stored after it.
    seed_trains(client, repo_id, [300, 480, 330, 310, 360, 350, 300, 320, 340, 330], days_ago=0.1)
    assert check()[0] == "Check runs of api take 6 min at the median, never looked at for speed"
    verdict = utcnow() - timedelta(minutes=30)
    assert check(slow_told_at=verdict - timedelta(minutes=10), slow_told_seconds=330, checks_baseline_seconds=120, checks_baseline_at=verdict, checks_verdict="sped up to 2 min")[0] is None
    seed_trains(client, repo_id, [170, 130, 160, 150], days_ago=0)
    assert check()[:2] == (None, 0)  # four fast runs since: too few to say, the slow ones before do not count
    seed_trains(client, repo_id, [140], days_ago=0)
    assert check()[:2] == (None, 150)  # within 30 % of the verdict: no notice
    seed_trains(client, repo_id, [400] * 5, days_ago=0)
    assert check()[0] == "Check runs of api take 5 min at the median, up from 2 min (its baseline)" and check()[2] is True  # grown beyond the verdict after it
