from conftest import agents_by_slug, idle, wait_for
from test_orchestration import chat, make_dev, runner
from test_todos import tool_results


def test_own_notes_survive_a_new_session_without_touching_the_cache(client, project):
    pid = project["id"]
    manager = agents_by_slug(client, pid)["manager"]
    dev = make_dev(client, pid)

    chat(client, dev["id"], 'TOOL remember {"text": "Use pnpm"}')
    wait_for(lambda: idle(client, pid))
    assert tool_results(client, dev["id"])[-1] == "Saved as own note 1 (8/3000 chars)."
    frozen = runner(client).specs[-1].system_prompt
    assert "Use pnpm" not in frozen

    # What the member wrote itself is not sent again, and the cached system prompt stays as it is.
    chat(client, dev["id"], "SAY hi")
    wait_for(lambda: idle(client, pid))
    spec = runner(client).specs[-1]
    assert spec.resume_session_id and spec.system_prompt == frozen and "[Briefing update]" not in spec.prompt

    # A note from the user arrives once, in the turn.
    added = client.post(f"/api/agents/{dev['id']}/notes", json={"text": "Answer in German"}).json()
    assert added["created_by_agent_id"] is None
    chat(client, dev["id"], "SAY hi")
    wait_for(lambda: idle(client, pid))
    spec = runner(client).specs[-1]
    assert spec.system_prompt == frozen and spec.prompt.startswith("[Briefing update]")
    assert "### Your own notes" in spec.prompt and "- [2] Answer in German" in spec.prompt and "- [1] Use pnpm" in spec.prompt
    chat(client, dev["id"], "SAY hi")
    wait_for(lambda: idle(client, pid))
    assert "[Briefing update]" not in runner(client).specs[-1].prompt

    # A new session (reset or compression) has them in its system prompt; they are private.
    client.post(f"/api/agents/{dev['id']}/reset-session")
    chat(client, dev["id"], "SAY hi")
    wait_for(lambda: idle(client, pid))
    spec = runner(client).specs[-1]
    assert spec.resume_session_id is None and "- [1] Use pnpm" in spec.system_prompt and "- [2] Answer in German" in spec.system_prompt
    chat(client, manager["id"], "SAY hi")
    wait_for(lambda: idle(client, pid))
    assert "Use pnpm" not in runner(client).specs[-1].system_prompt
    assert agents_by_slug(client, pid)["dev"]["own_notes"] == 2


def test_own_notes_are_private_and_limited(client, project):
    pid = project["id"]
    manager = agents_by_slug(client, pid)["manager"]
    dev = make_dev(client, pid)
    mine = client.post(f"/api/agents/{manager['id']}/notes", json={"text": "Ask before deleting"}).json()

    chat(
        client,
        dev["id"],
        f'TOOL forget {{"note_ids": [{mine["id"]}]}}\nTOOL remember {{"text": "x", "note_id": {mine["id"]}}}\n'
        'TOOL remember {"text": "First"}\nTOOL remember {"text": "Second", "note_id": 2}\nTOOL forget {"note_ids": [2]}',
    )
    wait_for(lambda: idle(client, pid))
    results = tool_results(client, dev["id"])
    assert results[0].endswith("not found") and results[1].endswith("not found")
    assert results[2:] == ["Saved as own note 2 (5/3000 chars).", "Saved as own note 2 (6/3000 chars).", "Deleted 1 own note(s)."]
    assert client.get(f"/api/agents/{dev['id']}/notes").json() == []
    assert [n["text"] for n in client.get(f"/api/agents/{manager['id']}/notes").json()] == ["Ask before deleting"]

    # A full budget never blocks: what the user saves may go over it, what an agent saves pushes out the
    # member's oldest notes it wrote itself (never the user's) and hands their text back.
    user = client.post(f"/api/agents/{dev['id']}/notes", json={"text": "u" * 500}).json()
    chat(client, dev["id"], "\n".join(f'TOOL remember {{"text": "{c * 500}"}}' for c in "abcd"))
    wait_for(lambda: idle(client, pid))
    for c in "vw":
        assert client.post(f"/api/agents/{dev['id']}/notes", json={"text": c * 500}).status_code == 201  # 3500 of 3000
    chat(client, dev["id"], 'TOOL remember {"text": "e"}')
    wait_for(lambda: idle(client, pid))
    saved = tool_results(client, dev["id"])[-1]
    assert saved.startswith("Saved as own note") and "(2501/3000 chars)" in saved
    assert "oldest own notes were removed:\n- [" in saved and "a" * 500 in saved and "b" * 500 in saved and "c" * 500 not in saved
    left = [n["text"][0] for n in client.get(f"/api/agents/{dev['id']}/notes").json()]
    assert left == ["u", "c", "d", "v", "w", "e"]
    assert client.patch(f"/api/agent-notes/{user['id']}", json={"text": "short"}).json()["text"] == "short"
    assert client.delete(f"/api/agent-notes/{user['id']}").status_code == 204


def test_old_own_notes_are_put_up_for_review_in_the_next_turn(client, project):
    from datetime import timedelta

    from sqlalchemy import update

    from plainboard.db import sessionmaker
    from plainboard.models import AgentNote, utcnow

    pid = project["id"]
    dev = make_dev(client, pid)
    chat(client, dev["id"], 'TOOL remember {"text": "Old fact"}\nTOOL remember {"text": "New fact"}')
    wait_for(lambda: idle(client, pid))
    client.post(f"/api/agents/{dev['id']}/notes", json={"text": "From the user"})

    async def age_notes():
        async with sessionmaker()() as s:
            await s.execute(update(AgentNote).where(AgentNote.id.in_([1, 3])).values(updated_at=utcnow() - timedelta(days=40)))
            await s.commit()

    client.portal.call(age_notes)
    client.portal.call(client.app_ref.state.scheduler.tick)
    client.portal.call(client.app_ref.state.scheduler.tick)  # asked once, not again
    assert idle(client, pid)  # nobody is woken for it

    chat(client, dev["id"], "SAY ok")
    wait_for(lambda: idle(client, pid))
    prompt = runner(client).specs[-1].prompt
    assert prompt.count("Your own notes 1 have not changed for over 3 days") == 1  # not the new one, not the user's


def test_manager_reads_and_edits_the_own_notes_of_members(client, project):
    pid = project["id"]
    manager = agents_by_slug(client, pid)["manager"]
    dev = make_dev(client, pid)
    chat(client, dev["id"], 'TOOL remember {"text": "Old fact"}')
    wait_for(lambda: idle(client, pid))
    client.post(f"/api/agents/{dev['id']}/notes", json={"text": "From the user"})

    chat(
        client,
        manager["id"],
        'TOOL member_notes {"agent": "dev"}\n'
        'TOOL edit_member_note {"agent": "dev", "note_id": 1, "delete": true}\n'
        'TOOL edit_member_note {"agent": "dev", "text": "Run tests in Docker"}\n'
        'TOOL edit_member_note {"agent": "manager", "text": "x"}',
    )
    wait_for(lambda: idle(client, pid))
    listed, deleted, added, own = tool_results(client, manager["id"])[-4:]
    assert "[1] by Dev, 0d old: Old fact" in listed and "[2] by the user, 0d old: From the user" in listed
    assert deleted == "Deleted note 1 of Dev." and added == "Saved note 3 for Dev."
    assert "remember and forget" in own

    chat(client, dev["id"], "SAY ok")  # the member sees the change in its next turn
    wait_for(lambda: idle(client, pid))
    prompt = runner(client).specs[-1].prompt
    assert prompt.startswith("[Briefing update]") and "- [3] Run tests in Docker" in prompt and "Old fact" not in prompt

    chat(client, dev["id"], 'TOOL member_notes {"agent": "manager"}')  # members can't
    wait_for(lambda: idle(client, pid))
    assert "not available" in tool_results(client, dev["id"])[-1]

