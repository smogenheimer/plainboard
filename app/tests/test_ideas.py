"""A project's ideas (#91): read, sorted and rearranged by a model, undoable, and turned into tickets by the manager."""

import json
import re

import pytest
from conftest import agents_by_slug, idle, runs_of, wait_for
from plainboard_protocol import CompletionResult
from test_flow import chat, tool_results
from test_transfer import bring_in, export
from test_users import new_user, sign_in

from plainboard.orchestration import ideas as idea_orchestration
from plainboard.services import ideas as idea_service

KINDS = {idea_service.SORT_SYSTEM: "sort", idea_service.REORDER_SYSTEM: "reorder", idea_service.INTENT_SYSTEM: "intent", idea_service.MERGE_SYSTEM: "merge"}


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(idea_orchestration, "SORT_DELAY", 0.0)


def answer(client, fail: bool = False, **queues) -> dict[str, list]:
    """The model's answers per kind of call (sort, reorder, intent), in turn: a text, or a function of the
    spec. An empty queue answers "". Returns the specs asked, per kind."""
    runner = client.app_ref.state.runner
    asked: dict[str, list] = {"sort": [], "reorder": [], "intent": [], "merge": [], "other": []}
    queues = {k: list(v) for k, v in queues.items()}

    async def complete(spec):
        runner.database_free("complete")
        kind = KINDS.get(spec.system_prompt, "other")
        asked[kind].append(spec)
        if fail:
            raise RuntimeError("model down")
        queue = queues.get(kind) or []
        text = queue.pop(0) if queue else ""
        return CompletionResult(text=text(spec) if callable(text) else text, input_tokens=50, output_tokens=20, cost_usd=0.001)

    runner.complete = complete
    return asked


def ids_in(spec, section: str) -> list[int]:
    return [int(n) for n in re.findall(r"^\s*i(\d+) ", spec.prompt.split(section)[1], re.M)]


def ideas_of(client, pid: int, **params) -> dict:
    resp = client.get(f"/api/projects/{pid}/ideas", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def add(client, pid: int, text: str, **more) -> dict:
    resp = client.post(f"/api/projects/{pid}/ideas", json={"text": text, **more})
    assert resp.status_code == 201, resp.text
    return resp.json()


def typed(client, pid: int, text: str) -> dict:
    resp = client.post(f"/api/projects/{pid}/ideas/input", json={"text": text})
    assert resp.status_code == 201, resp.text
    return resp.json()


def settled(client, pid: int) -> dict:
    wait_for(lambda: not (data := ideas_of(client, pid))["sorting"] and all(i["sort_state"] != "pending" for i in data["ideas"]))
    return ideas_of(client, pid)


def by_id(data: dict) -> dict[int, dict]:
    return {i["id"]: i for i in data["ideas"]}


def test_ideas_are_sorted_together_and_those_that_belong_in_one_are_marked_to_merge(client, project, monkeypatch):
    pid = project["id"]

    def first(spec) -> str:
        (idea_id,) = ids_in(spec, "Sort these:")
        return json.dumps({"ideas": [{"id": idea_id, "topic": "Payment"}], "topics": [{"name": "Payment", "hint": "1 idea about payment"}]})

    asked = answer(client, sort=[first])
    one = add(client, pid, "Pay by card")
    data = settled(client, pid)
    (topic,) = data["topics"]
    assert (topic["name"], topic["hint"], topic["color"], topic["manual"]) == ("Payment", "1 idea about payment", 0, False)
    assert asked["sort"][0].model == "claude-haiku-5-5"  # the provider's small model, by default

    monkeypatch.setattr(idea_orchestration, "SORT_DELAY", 0.5)  # ideas that come quickly are sorted together

    def second(spec) -> str:
        two, three = ids_in(spec, "Sort these:")
        reply = {
            "ideas": [
                {"id": two, "topic": "payment", "similar": [one["id"], 999], "merge_with": 999, "hint": "which providers?"},
                {"id": f"i{three}", "topic": "Payment", "merge_with": one["id"]},
            ],
            "topics": [{"name": "Payment", "hint": "3 ideas about payment"}],
        }
        return "Here you go:\n```json\n" + json.dumps(reply)[:-1] + ",}\n```"

    asked = answer(client, sort=[second])
    two, three = add(client, pid, "PayPal too", title="PayPal"), add(client, pid, "Accept credit cards")
    data = settled(client, pid)
    assert len(asked["sort"]) == 1  # both in one call
    ideas = by_id(data)
    assert {i["topic_id"] for i in ideas.values()} == {topic["id"]} and data["topics"][0]["hint"] == "3 ideas about payment"
    # No "similar" links any more (noise on every card): what belongs in one is merged, related ideas share a topic.
    assert ideas[two["id"]]["similar"] == [] and ideas[two["id"]]["duplicate_of"] is None and ideas[two["id"]]["hint"] == "which providers?"
    assert ideas[three["id"]]["duplicate_of"] == one["id"] and ideas[one["id"]]["similar"] == []
    runs = [r for r in runs_of(client, pid)]
    assert runs and all(r["trigger"] == "ideas" and r["input_text"] == "" for r in runs)
    assert client.get(f"/api/projects/{pid}/tasks").json() == []  # sorting starts nothing


def test_a_broken_answer_or_a_failing_model_leaves_the_idea_unsorted(client, project):
    pid = project["id"]
    answer(client, sort=["Sorry, I cannot do that."])
    idea = add(client, pid, "Dark mode")
    (data,) = settled(client, pid)["ideas"]
    assert data["id"] == idea["id"] and data["sort_state"] == "failed" and data["topic_id"] is None

    answer(client, sort=[json.dumps({"ideas": [{"id": "nonsense"}], "topics": "x"})])
    client.patch(f"/api/projects/{pid}/ideas/{idea['id']}", json={"text": "Dark mode for the night"})
    (data,) = settled(client, pid)["ideas"]
    assert data["sort_state"] == "failed" and data["topic_id"] is None

    answer(client, fail=True)
    client.patch(f"/api/projects/{pid}/ideas/{idea['id']}", json={"text": "Dark mode, also on phones"})
    (data,) = settled(client, pid)["ideas"]
    assert data["sort_state"] == "failed" and data["text"] == "Dark mode, also on phones"
    assert [r["status"] for r in runs_of(client, pid) if r["trigger"] == "ideas"].count("failed") == 2  # the second answered, wrongly


def test_parse_json_finds_the_object_in_what_a_model_writes():
    assert idea_service.parse_json('```json\n{"a": [1, 2,],}\n```') == {"a": [1, 2]}
    assert idea_service.parse_json('Sure! {"a": 1} Hope that helps.') == {"a": 1}
    for bad in ("", "[1, 2]", "{not json", None):
        with pytest.raises(ValueError):
            idea_service.parse_json(bad)


def test_topics_are_rearranged_after_a_few_ideas_within_the_rules_and_it_can_be_undone(client, project):
    pid = project["id"]
    answer(client)
    fixed_topic = client.post(f"/api/projects/{pid}/ideas/topics", json={"name": "Mine"}).json()
    pinned = add(client, pid, "Logo in blue", topic_id=fixed_topic["id"])
    old = client.post(f"/api/projects/{pid}/ideas/topics", json={"name": "Stuff"}).json()
    client.patch(f"/api/projects/{pid}/ideas/topics/{old['id']}", json={"x": 10})
    settled(client, pid)

    def sort(spec) -> str:
        return json.dumps({"ideas": [{"id": i, "topic": "Stuff"} for i in ids_in(spec, "Sort these:")]})

    def reorder(spec) -> str:
        assert f"id {fixed_topic['id']}: Mine (fixed)" in spec.prompt and "(fixed)" in spec.prompt.split(f"i{pinned['id']} ")[1].splitlines()[0]
        a, b, c = [i for i in ids_in(spec, "Ideas [topic id]:") if i != pinned["id"]]
        return json.dumps(
            {
                "topics": [
                    {"id": fixed_topic["id"], "name": "Renamed by AI", "ideas": [pinned["id"]]},
                    {"id": None, "name": "Payment", "hint": "2 ideas", "ideas": [a, b, pinned["id"]]},
                    {"id": None, "name": "Login", "ideas": [c]},
                ]
            }
        )

    asked = answer(client, sort=[sort] * 3, reorder=[reorder])
    new_ids = [add(client, pid, text)["id"] for text in ("Card", "PayPal", "Google login")]
    wait_for(lambda: len(asked["reorder"]) == 1 and not ideas_of(client, pid)["sorting"])
    data = ideas_of(client, pid)
    names = {t["id"]: t["name"] for t in data["topics"]}
    ideas = by_id(data)
    # A person's topic keeps its name, a person's idea its topic; the emptied topic of the model is gone.
    assert names[fixed_topic["id"]] == "Mine" and ideas[pinned["id"]]["topic_id"] == fixed_topic["id"]
    assert sorted(names.values()) == ["Login", "Mine", "Payment", "Stuff"]  # "Stuff" was made by a person, so it stays
    assert {names[ideas[i]["topic_id"]] for i in new_ids[:2]} == {"Payment"} and names[ideas[new_ids[2]]["topic_id"]] == "Login"
    (change,) = client.get(f"/api/projects/{pid}/ideas/changes").json()
    assert change["kind"] == "reorder" and change["source"] == "auto"
    assert {c["name"] for c in change["summary"]["created"]} == {"Payment", "Login"} and all(c["from"] == ["Stuff"] for c in change["summary"]["created"])
    assert len(change["summary"]["moved"]) == 3 and change["summary"]["renamed"] == []

    undone = client.post(f"/api/projects/{pid}/ideas/changes/{change['id']}/undo")
    assert undone.status_code == 200 and undone.json()["undone_at"]
    data = ideas_of(client, pid)
    assert {t["name"] for t in data["topics"]} == {"Mine", "Stuff"}
    assert all(i["topic_id"] == old["id"] for i in data["ideas"] if i["id"] in new_ids)
    assert client.post(f"/api/projects/{pid}/ideas/changes/{change['id']}/undo").status_code == 409


def test_the_button_rearranges_and_a_model_topic_is_removed_once_empty(client, project):
    pid = project["id"]

    def sort(spec) -> str:
        return json.dumps({"ideas": [{"id": i, "topic": "Misc"} for i in ids_in(spec, "Sort these:")]})

    asked = answer(client, sort=[sort] * 2)
    a, b = add(client, pid, "Card")["id"], add(client, pid, "Chat")["id"]
    misc = settled(client, pid)["topics"][0]

    def reorder(spec) -> str:
        return json.dumps({"topics": [{"id": misc["id"], "name": "Payment", "ideas": [a]}, {"id": None, "name": "Chat", "ideas": [b]}]})

    asked = answer(client, reorder=[reorder])
    assert client.post(f"/api/projects/{pid}/ideas/sort").status_code == 202
    wait_for(lambda: len(asked["reorder"]) == 1 and not ideas_of(client, pid)["sorting"])
    (change,) = client.get(f"/api/projects/{pid}/ideas/changes").json()
    assert change["source"] == "button" and change["summary"]["renamed"] == [{"topic_id": misc["id"], "from": "Misc", "to": "Payment"}]
    assert sorted(t["name"] for t in ideas_of(client, pid)["topics"]) == ["Chat", "Payment"]


def test_what_is_typed_is_read_as_intent_and_every_change_keeps_the_old_text(client, project):
    pid = project["id"]
    answer(client)
    booking = add(client, pid, "Book appointments online", title="Booking")
    other = add(client, pid, "Remind by e-mail", title="Reminder")
    settled(client, pid)

    # An addition to an existing idea: the old text stays as a version, and it can be undone.
    amend = {"action": "amend", "target": booking["id"], "title": "Booking", "text": "Book appointments online, with a reminder by SMS"}
    asked = answer(client, intent=[json.dumps(amend)])
    change = typed(client, pid, "bei der Terminbuchung auch Erinnerung per SMS")
    assert "i%d Booking: Book appointments online" % booking["id"] in asked["intent"][0].prompt
    assert change["kind"] == "amend" and change["summary"]["idea_id"] == booking["id"]
    assert change["summary"]["before"]["text"] == "Book appointments online" and change["summary"]["after"]["text"].endswith("by SMS")
    assert [i["id"] for i in change["ideas"]] == [booking["id"]] and change["ideas"][0]["text"].endswith("by SMS")
    (version,) = client.get(f"/api/projects/{pid}/ideas/{booking['id']}/versions").json()
    assert (version["text"], version["source"]) == ("Book appointments online", "ai")
    client.post(f"/api/projects/{pid}/ideas/changes/{change['id']}/undo")
    assert by_id(ideas_of(client, pid))[booking["id"]]["text"] == "Book appointments online"
    assert len(client.get(f"/api/projects/{pid}/ideas/{booking['id']}/versions").json()) == 2  # the SMS text is kept too

    # Several ideas at once.
    several = {"action": "new", "ideas": [{"title": "Dark mode", "text": "Dark mode"}, {"title": "Big fonts", "text": "Bigger fonts"}], "similar": [other["id"], 999]}
    answer(client, intent=[json.dumps(several)])
    change = typed(client, pid, "dark mode and bigger fonts")
    assert change["kind"] == "several" and len(change["summary"]["created"]) == 2
    assert all(i["similar"] == [] for i in change["ideas"])
    client.post(f"/api/projects/{pid}/ideas/changes/{change['id']}/undo")
    kept = by_id(ideas_of(client, pid, closed=True))
    assert all(kept[i]["status"] == "dismissed" and kept[i]["closed_reason"] == "undone" for i in change["summary"]["created"])

    # Merging two ideas: the second goes into the first and is kept as merged.
    merge = {"action": "merge", "ids": [booking["id"], other["id"]], "title": "Booking with reminders", "text": "Book online, reminded by e-mail"}
    answer(client, intent=[json.dumps(merge)])
    change = typed(client, pid, "leg Buchung und Erinnerung zusammen")
    assert change["kind"] == "merge" and change["summary"]["merged"] == [other["id"]]
    kept = by_id(ideas_of(client, pid, closed=True))
    assert kept[other["id"]]["status"] == "merged" and kept[other["id"]]["merged_into_id"] == booking["id"]
    assert kept[booking["id"]]["title"] == "Booking with reminders"
    client.post(f"/api/projects/{pid}/ideas/changes/{change['id']}/undo")
    kept = by_id(ideas_of(client, pid))
    assert kept[other["id"]]["status"] == "open" and kept[booking["id"]]["title"] == "Booking"

    # Nonsense, or an amend of an idea that does not exist: a new idea, nothing is overwritten.
    for reply in ("no idea", json.dumps({"action": "amend", "target": 999, "text": "x"})):
        answer(client, intent=[reply])
        change = typed(client, pid, "Something else entirely")
        assert change["kind"] == "new" and change["summary"]["fallback"] and change["ideas"][0]["text"] == "Something else entirely"


def test_an_unclear_input_becomes_a_question_and_the_answer_is_worked_in(client, project):
    pid = project["id"]
    question = {"action": "question", "question": "What should be exported?", "options": [{"label": "Invoices", "description": "PDF"}, "Customers"]}
    answer(client, intent=[json.dumps(question)])
    change = typed(client, pid, "export")
    assert change["kind"] == "question"
    (idea,) = change["ideas"]
    assert idea["question_status"] == "open" and idea["question_options"] == [{"label": "Invoices", "description": "PDF", "resolve": ""}, {"label": "Customers", "description": "", "resolve": ""}]
    assert client.post(f"/api/projects/{pid}/ideas/{idea['id']}/answer", json={"option_index": 7}).status_code == 400
    assert [i["id"] for i in client.get(f"/api/projects/{pid}/ideas/questions").json()] == [idea["id"]]

    resolved = {"action": "amend", "target": idea["id"], "title": "Export invoices", "text": "Export invoices as PDF"}
    asked = answer(client, intent=[json.dumps(resolved)])
    resp = client.post(f"/api/projects/{pid}/ideas/{idea['id']}/answer", json={"option_index": 0, "text": "monthly"})
    assert resp.status_code == 200, resp.text
    assert "Their answer: Invoices — monthly" in asked["intent"][0].prompt
    (after,) = resp.json()["ideas"]
    assert after["text"] == "Export invoices as PDF" and after["question_status"] == "answered" and after["question_answer"] == "Invoices — monthly"
    assert client.post(f"/api/projects/{pid}/ideas/{idea['id']}/answer", json={"text": "again"}).status_code == 409
    assert client.get(f"/api/projects/{pid}/ideas/questions").json() == []

    # The model may also ask while sorting, rarely: once per idea at most.
    settled(client, pid)

    def sort(spec) -> str:
        return json.dumps({"ideas": [{"id": i, "topic": "Misc", "question": {"question": "Which app?", "options": ["Web", "Phone"]}} for i in ids_in(spec, "Sort these:")]})

    answer(client, sort=[sort])
    vague = add(client, pid, "make it nicer")
    asked_idea = by_id(settled(client, pid))[vague["id"]]
    assert asked_idea["question"] == "Which app?" and asked_idea["question_status"] == "open"
    assert client.delete(f"/api/projects/{pid}/ideas/{vague['id']}/question").json()["question_status"] == "withdrawn"


def test_a_persons_edit_is_kept_as_a_version_sorted_again_and_can_be_restored(client, project):
    pid = project["id"]
    answer(client)
    idea = add(client, pid, "First text", title="One")
    settled(client, pid)
    moved = client.patch(f"/api/projects/{pid}/ideas/{idea['id']}", json={"x": 5, "y": 6}).json()
    assert moved["topic_manual"] and (moved["x"], moved["y"]) == (5, 6)
    edited = client.patch(f"/api/projects/{pid}/ideas/{idea['id']}", json={"text": "Second text"}).json()
    assert edited["text"] == "Second text" and (edited["x"], edited["y"]) == (5, 6)
    (change,) = [c for c in client.get(f"/api/projects/{pid}/ideas/changes").json() if c["kind"] == "edit"]
    assert change["summary"]["before"] == {"title": "One", "text": "First text"}
    (version,) = client.get(f"/api/projects/{pid}/ideas/{idea['id']}/versions").json()
    restored = client.post(f"/api/projects/{pid}/ideas/{idea['id']}/versions/{version['id']}/restore").json()
    assert restored["text"] == "First text"
    assert [v["text"] for v in client.get(f"/api/projects/{pid}/ideas/{idea['id']}/versions").json()] == ["Second text", "First text"]


def test_viewers_read_the_ideas_and_members_change_them(client, project):
    pid = project["id"]
    answer(client)
    viewer, member = new_user(client, "vera"), new_user(client, "max")
    for user, role in ((viewer, "viewer"), (member, "member")):
        assert client.put(f"/api/projects/{pid}/members/{user['id']}", json={"role": role}).status_code == 200
    idea = add(client, pid, "Offline mode")
    settled(client, pid)
    edit = client.patch(f"/api/projects/{pid}/ideas/{idea['id']}", json={"text": "Offline mode, sync later"})
    change_id = client.get(f"/api/projects/{pid}/ideas/changes").json()[0]["id"]
    assert edit.status_code == 200

    sign_in(client, "vera", "vera-password")
    assert [i["id"] for i in ideas_of(client, pid)["ideas"]] == [idea["id"]]
    assert client.get(f"/api/projects/{pid}/ideas/changes").status_code == 200 and client.get(f"/api/projects/{pid}/ideas/{idea['id']}/versions").status_code == 200
    for method, path, body in (
        ("post", "", {"text": "x"}),
        ("post", "/input", {"text": "x"}),
        ("patch", f"/{idea['id']}", {"votes": 3}),
        ("delete", f"/{idea['id']}", None),
        ("post", f"/{idea['id']}/ticket", {}),
        ("post", f"/{idea['id']}/answer", {"text": "x"}),
        ("post", f"/changes/{change_id}/undo", None),
        ("post", "/sort", None),
        ("post", "/topics", {"name": "x"}),
    ):
        resp = getattr(client, method)(f"/api/projects/{pid}/ideas{path}", **({"json": body} if body is not None else {}))
        assert resp.status_code == 403, (method, path)

    sign_in(client, "max", "max-password")
    mine = add(client, pid, "Push notifications")
    assert mine["author"] == "Max"
    topic = client.post(f"/api/projects/{pid}/ideas/topics", json={"name": "Mobile"}).json()
    moved = client.patch(f"/api/projects/{pid}/ideas/{mine['id']}", json={"topic_id": topic["id"], "votes": 2}).json()
    assert (moved["topic_id"], moved["votes"], moved["topic_manual"]) == (topic["id"], 2, True)
    renamed = client.patch(f"/api/projects/{pid}/ideas/topics/{topic['id']}", json={"name": "Phone"}).json()
    assert renamed["name"] == "Phone" and renamed["manual"]
    assert client.post(f"/api/projects/{pid}/ideas/changes/{change_id}/undo").status_code == 200
    assert client.delete(f"/api/projects/{pid}/ideas/{idea['id']}").status_code == 204


def test_the_manager_reads_ideas_and_marks_what_went_into_tickets(client, project):
    pid = project["id"]
    answer(client)
    topic = client.post(f"/api/projects/{pid}/ideas/topics", json={"name": "Payment"}).json()
    card = add(client, pid, "Pay by card", topic_id=topic["id"])
    paypal = add(client, pid, "PayPal", topic_id=topic["id"])
    again = add(client, pid, "Card payments please")
    settled(client, pid)
    manager = agents_by_slug(client, pid)["manager"]
    runner = client.app_ref.state.runner

    lines = [
        "TOOL list_ideas {}",
        "TOOL get_idea " + json.dumps({"idea": card["id"]}),
        'TOOL create_tasks {"tickets": [{"title": "Payments"}]}',
        "TOOL take_ideas " + json.dumps({"ticket": 1, "ideas": [card["id"], paypal["id"]]}),
        "TOOL leave_out_ideas " + json.dumps({"ideas": [again["id"]], "reason": "duplicate", "duplicate_of": card["id"]}),
        "TOOL take_ideas " + json.dumps({"ticket": 1, "ideas": [card["id"]]}),
    ]
    chat(client, manager["id"], "Make tickets from the payment ideas\n" + "\n".join(lines))
    wait_for(lambda: idle(client, pid))
    assert "Ideas: 3 open (list_ideas)." in runner.specs[-1].prompt
    listed, detail, _created, taken, left, twice = tool_results(client, manager["id"])[-6:]
    assert listed.startswith("Ideas 1-3 of 3:") and "## Payment" in listed and f"i{card['id']} (no title)\n  Pay by card" in listed and "## No topic" in listed
    assert detail.startswith(f"i{card['id']} (no title) — open") and "Pay by card" in detail
    assert taken == "2 idea(s) taken into #1." and left == "1 idea(s) left out." and "no longer open" in twice

    data = ideas_of(client, pid)
    assert data["ideas"] == [] and [t["name"] for t in data["topics"]] == ["Payment"]  # emptied, but a person made it
    kept = by_id(ideas_of(client, pid, closed=True))
    assert (kept[card["id"]]["status"], kept[card["id"]]["task_number"]) == ("taken", 1)
    assert (kept[again["id"]]["status"], kept[again["id"]]["closed_reason"]) == ("dismissed", "duplicate")


def test_one_idea_becomes_a_ticket_and_the_project_moves_with_its_ideas(client, project):
    pid = project["id"]
    answer(client)
    topic = client.post(f"/api/projects/{pid}/ideas/topics", json={"name": "Look"}).json()
    one = add(client, pid, "Dark mode\nfor the night", topic_id=topic["id"])
    two = add(client, pid, "Bigger fonts", topic_id=topic["id"])
    settled(client, pid)
    client.patch(f"/api/projects/{pid}/ideas/{two['id']}", json={"text": "Much bigger fonts"})
    resp = client.post(f"/api/projects/{pid}/ideas/{one['id']}/ticket", json={"status": "backlog"})
    assert resp.status_code == 201, resp.text
    ticket = resp.json()["ticket"]
    assert ticket["title"] == "Dark mode" and ticket["description"] == "Dark mode\nfor the night" and ticket["status"] == "backlog"
    assert resp.json()["idea"]["status"] == "taken"
    assert [i["id"] for i in ideas_of(client, pid)["ideas"]] == [two["id"]]

    moved = bring_in(client, export(client, pid))
    assert moved.status_code == 201, moved.text
    other = moved.json()["project"]["id"]
    data = ideas_of(client, other, closed=True)
    assert [t["name"] for t in data["topics"]] == ["Look"]
    texts = {i["text"]: i for i in data["ideas"]}
    assert texts["Much bigger fonts"]["topic_id"] == data["topics"][0]["id"]
    assert [v["text"] for v in client.get(f"/api/projects/{other}/ideas/{texts['Much bigger fonts']['id']}/versions").json()] == ["Bigger fonts"]
    taken = texts["Dark mode\nfor the night"]
    assert taken["status"] == "taken" and taken["task_number"] == ticket["number"] and taken["task_id"] != ticket["id"]


def test_list_ideas_gives_every_text_whole_and_pages_by_whole_ideas(client, project, monkeypatch):
    from plainboard.db import sessionmaker

    pid = project["id"]
    answer(client)
    long_text = "Line one\n" + "x" * 300
    ids = [add(client, pid, text)["id"] for text in (long_text, "Short", "Also short")]
    settled(client, pid)
    monkeypatch.setattr(idea_service, "LIST_CHARS", 200)

    async def page(offset: int) -> str:
        async with sessionmaker()() as session:
            return await idea_service.manager_list(session, pid, topic=None, query=None, status="open", offset=offset)

    first = client.portal.call(page, 0)
    assert first.startswith("Ideas 1-1 of 3:") and "  " + "x" * 300 in first and first.endswith("(continue with offset=1: 2 more)")
    second = client.portal.call(page, 1)
    assert f"i{ids[1]} (no title)\n  Short" in second and f"i{ids[2]}" in second and "continue" not in second
    assert client.portal.call(page, 5) == "No ideas at offset 5 (3 in all)."


def test_files_of_an_idea_are_shown_read_by_the_manager_kept_by_the_retention_and_moved(client, project):
    from test_retention import _age, _file, _purge

    pid = project["id"]
    answer(client)
    _file.client = client
    viewer, member = new_user(client, "vera"), new_user(client, "max")
    for user, role in ((viewer, "viewer"), (member, "member")):
        assert client.put(f"/api/projects/{pid}/members/{user['id']}", json={"role": role}).status_code == 200
    idea = add(client, pid, "Dark mode")
    other = add(client, pid, "Bigger fonts")
    settled(client, pid)

    sign_in(client, "max", "max-password")
    resp = client.post(f"/api/projects/{pid}/ideas/{idea['id']}/attachments", files=[("files", ("sketch.png", b"x" * 10, "image/png")), ("files", ("notes.txt", b"dark", "text/plain"))])
    assert resp.status_code == 201, resp.text
    sketch, notes = resp.json()
    assert (sketch["idea_id"], sketch["task_id"], sketch["filename"], sketch["size"]) == (idea["id"], None, "sketch.png", 10)
    shown = by_id(ideas_of(client, pid))
    assert [a["filename"] for a in shown[idea["id"]]["attachments"]] == ["sketch.png", "notes.txt"] and shown[other["id"]]["attachments"] == []
    assert client.get(f"/api/attachments/{notes['id']}").content == b"dark"
    assert client.delete(f"/api/attachments/{notes['id']}").status_code == 204
    assert [a["id"] for a in by_id(ideas_of(client, pid))[idea["id"]]["attachments"]] == [sketch["id"]]

    sign_in(client, "vera", "vera-password")
    assert client.get(f"/api/attachments/{sketch['id']}").status_code == 200
    assert client.post(f"/api/projects/{pid}/ideas/{idea['id']}/attachments", files=[("files", ("x.txt", b"x", "text/plain"))]).status_code == 403
    assert client.delete(f"/api/attachments/{sketch['id']}").status_code == 403
    sign_in(client, "admin", "admin-password")

    manager = agents_by_slug(client, pid)["manager"]
    chat(client, manager["id"], "Look at the ideas\nTOOL list_ideas {}\nTOOL get_idea " + json.dumps({"idea": idea["id"]}))
    wait_for(lambda: idle(client, pid))
    listed, detail = tool_results(client, manager["id"])[-2:]
    assert f"  file: {sketch['id']} sketch.png (10 B): /uploads/" in listed
    assert f"Attachments:\n- {sketch['id']} sketch.png (10 B): /uploads/" in detail

    # Taken into a ticket that is done long since: the retention leaves the idea's file alone.
    ticket = client.post(f"/api/projects/{pid}/ideas/{idea['id']}/ticket", json={}).json()["ticket"]
    assert client.patch(f"/api/tasks/{ticket['id']}", json={"status": "done"}).status_code == 200
    _age(client, pid, ticket["number"], 400)
    assert client.patch(f"/api/projects/{pid}", json={"attachment_retention_days": 30}).status_code == 200
    assert _purge(client) == {}
    assert _file(sketch).is_file()
    assert by_id(ideas_of(client, pid, closed=True))[idea["id"]]["attachments"][0]["purged_at"] is None

    moved = bring_in(client, export(client, pid))
    assert moved.status_code == 201, moved.text
    there = {i["text"]: i for i in ideas_of(client, moved.json()["project"]["id"], closed=True)["ideas"]}
    (copy,) = there["Dark mode"]["attachments"]
    assert copy["filename"] == "sketch.png" and copy["idea_id"] == there["Dark mode"]["id"]
    assert client.get(f"/api/attachments/{copy['id']}").content == b"x" * 10

    path = _file(sketch)
    assert client.delete(f"/api/projects/{pid}/ideas/{idea['id']}").status_code == 204
    assert not path.exists() and client.get(f"/api/attachments/{sketch['id']}").status_code == 404


def sort_all(spec) -> str:
    return json.dumps({"ideas": [{"id": i, "topic": "App"} for i in ids_in(spec, "Sort these:")]})


def test_duplicates_are_merged_into_one_with_their_files_and_links_and_the_merge_is_undone_as_one(client, project):
    pid = project["id"]
    answer(client, sort=[sort_all] * 3)
    fast = add(client, pid, "The app should start in under a second", title="Optimized start")
    fonts = add(client, pid, "Bigger fonts")
    settled(client, pid)

    def duplicate(spec) -> str:
        (idea_id,) = ids_in(spec, "Sort these:")
        return json.dumps({"ideas": [{"id": idea_id, "topic": "Start", "duplicate_of": fast["id"]}]})

    asked = answer(client, sort=[duplicate], merge=["no idea"])  # the first merge fails: the marker stays
    quick = add(client, pid, "Extremely fast startup, also on old phones", title="Fast startup")
    data = by_id(settled(client, pid))
    assert len(asked["merge"]) == 1 and data[quick["id"]]["duplicate_of"] == fast["id"]
    resp = client.post(f"/api/projects/{pid}/ideas/{quick['id']}/attachments", files=[("files", ("trace.txt", b"slow", "text/plain"))])
    assert resp.status_code == 201, resp.text
    (trace,) = resp.json()

    merged_text = "The app should start in under a second, also on old phones."
    asked = answer(client, sort=[sort_all] * 3, merge=[json.dumps({"title": "Fast start", "text": merged_text})])
    assert client.post(f"/api/projects/{pid}/ideas/sort").status_code == 202  # the next rearrangement merges what was marked
    wait_for(lambda: len(asked["merge"]) == 1 and not ideas_of(client, pid)["sorting"])
    data = settled(client, pid)
    (prompt,) = [spec.prompt for spec in asked["merge"]]
    assert "under a second" in prompt and "also on old phones" in prompt and "ask first" not in prompt
    ideas = by_id(data)
    assert sorted(ideas) == sorted([fast["id"], fonts["id"]])
    assert (ideas[fast["id"]]["title"], ideas[fast["id"]]["text"]) == ("Fast start", merged_text)
    assert [a["id"] for a in ideas[fast["id"]]["attachments"]] == [trace["id"]] and ideas[fonts["id"]]["similar"] == []
    gone = by_id(ideas_of(client, pid, closed=True))[quick["id"]]
    assert (gone["status"], gone["merged_into_id"], gone["text"]) == ("merged", fast["id"], quick["text"])
    versions = client.get(f"/api/projects/{pid}/ideas/{fast['id']}/versions").json()
    assert [(v["text"], v["source"]) for v in versions] == [(fast["text"], "ai")]
    change = next(c for c in client.get(f"/api/projects/{pid}/ideas/changes").json() if c["kind"] == "merge")
    assert change["source"] == "auto" and change["summary"]["merged"] == [quick["id"]] and change["summary"]["duplicates"] is True

    manager = agents_by_slug(client, pid)["manager"]
    chat(client, manager["id"], "Look at the ideas\nTOOL list_ideas {}")
    wait_for(lambda: idle(client, pid))
    listed = tool_results(client, manager["id"])[-1]
    assert listed.startswith("Ideas 1-2 of 2:") and f"i{quick['id']} " not in listed and "file: " in listed

    assert client.post(f"/api/projects/{pid}/ideas/changes/{change['id']}/undo").status_code == 200
    ideas = by_id(ideas_of(client, pid))
    assert sorted(ideas) == sorted([fast["id"], fonts["id"], quick["id"]])
    assert (ideas[fast["id"]]["title"], ideas[fast["id"]]["text"]) == (fast["title"], fast["text"])
    assert ideas[quick["id"]]["merged_into_id"] is None and [a["id"] for a in ideas[quick["id"]]["attachments"]] == [trace["id"]]
    assert ideas[fast["id"]]["attachments"] == [] and fast["id"] not in ideas[fonts["id"]]["similar"]
    assert ideas[quick["id"]]["duplicate_of"] is None and fast["id"] in ideas[quick["id"]]["similar"]  # kept apart from now on

    asked = answer(client)
    assert client.post(f"/api/projects/{pid}/ideas/sort").status_code == 202
    wait_for(lambda: len(asked["reorder"]) == 1 and not ideas_of(client, pid)["sorting"])
    assert asked["merge"] == [] and len(ideas_of(client, pid)["ideas"]) == 3  # an undone merge is not made again


def test_a_duplicate_of_an_idea_a_person_placed_is_asked_about_instead_of_merged(client, project):
    pid = project["id"]
    answer(client)
    topic = client.post(f"/api/projects/{pid}/ideas/topics", json={"name": "Mine"}).json()
    mine = add(client, pid, "Dark mode", topic_id=topic["id"])
    settled(client, pid)

    def reorder(spec) -> str:
        ids = ids_in(spec, "Ideas [topic id]:")
        return json.dumps({"topics": [{"id": topic["id"], "name": "Mine", "ideas": ids}], "duplicates": [ids]})

    question = {"question": "Merge with “Dark mode”?", "options": [{"label": "Merge"}, {"label": "Keep both"}]}
    asked = answer(client, sort=[sort_all], reorder=[reorder], merge=[json.dumps(question)])
    night = add(client, pid, "A dark theme for the night")
    assert client.post(f"/api/projects/{pid}/ideas/sort").status_code == 202
    wait_for(lambda: len(asked["merge"]) == 1 and not ideas_of(client, pid)["sorting"])
    assert "ask first" in asked["merge"][0].prompt
    ideas = by_id(ideas_of(client, pid))
    assert ideas[mine["id"]]["text"] == "Dark mode" and ideas[night["id"]]["status"] == "open"  # not rewritten unasked
    assert (ideas[night["id"]]["question"], ideas[night["id"]]["question_status"]) == (question["question"], "open")
    assert ideas[night["id"]]["duplicate_of"] is None and ideas[night["id"]]["similar"] == [mine["id"]]
    assert all(c["kind"] != "merge" for c in client.get(f"/api/projects/{pid}/ideas/changes").json())


# --- #116: splitting, conflicts, a big board ------------------------------------------------------


def test_an_idea_that_holds_several_is_split_keeping_its_words_and_files_and_undone_as_one(client, project, monkeypatch):
    pid = project["id"]
    monkeypatch.setattr(idea_orchestration, "SORT_DELAY", 0.5)
    parts = [{"title": "Dark mode", "text": "Dark mode for the whole app."}, {"title": "PDF export", "text": "Also export all invoices as PDF."}]

    def split(spec) -> str:
        (idea_id,) = ids_in(spec, "Sort these:")
        return json.dumps({"ideas": [{"id": idea_id, "topic": "App", "split": parts}]})

    asked = answer(client, sort=[split, sort_all])
    idea = add(client, pid, "Dark mode for the whole app. Also export all invoices as PDF.")
    (sketch,) = client.post(f"/api/projects/{pid}/ideas/{idea['id']}/attachments", files=[("files", ("dark.png", b"png", "image/png"))]).json()
    data = settled(client, pid)
    ideas = by_id(data)
    (part,) = [i for i in ideas.values() if i["id"] != idea["id"]]
    assert (ideas[idea["id"]]["title"], ideas[idea["id"]]["text"]) == ("Dark mode", "Dark mode for the whole app.")
    assert (part["title"], part["text"], part["topic_id"]) == ("PDF export", "Also export all invoices as PDF.", ideas[idea["id"]]["topic_id"])
    assert [a["id"] for a in ideas[idea["id"]]["attachments"]] == [sketch["id"]] and part["attachments"] == []  # files stay with the first part
    assert len(asked["sort"]) == 2 and ids_in(asked["sort"][1], "Sort these:") == [part["id"]]  # only the new part is sorted again
    assert part["similar"] == [] and part["duplicate_of"] is None  # parts are kept apart, without a link on the cards
    change = next(c for c in client.get(f"/api/projects/{pid}/ideas/changes").json() if c["kind"] == "split")
    assert change["source"] == "auto" and change["summary"]["created"] == [part["id"]] and change["summary"]["before"]["text"] == idea["text"]

    assert client.post(f"/api/projects/{pid}/ideas/changes/{change['id']}/undo").status_code == 200
    every = by_id(ideas_of(client, pid, closed=True))
    assert every[idea["id"]]["text"] == idea["text"] and every[idea["id"]]["status"] == "open"
    assert (every[part["id"]]["status"], every[part["id"]]["closed_reason"]) == ("dismissed", "undone")

    # Parts that leave much of the text out: not split.
    def lossy(spec) -> str:
        (idea_id,) = ids_in(spec, "Sort these:")
        return json.dumps({"ideas": [{"id": idea_id, "topic": "App", "split": [{"text": "Login"}, {"text": "Chat"}]}]})

    answer(client, sort=[lossy])
    long = add(client, pid, "Login with Google, Apple and a magic link by e-mail, and a chat between all users of a team")
    assert len(settled(client, pid)["ideas"]) == 2 and by_id(ideas_of(client, pid))[long["id"]]["text"] == long["text"]


def test_ideas_that_contradict_each_other_are_shown_and_the_person_decides(client, project):
    pid = project["id"]
    answer(client, sort=[sort_all])
    card = add(client, pid, "Only card payments, no cash", title="Card only")
    settled(client, pid)

    def conflict(spec) -> str:
        return json.dumps({"ideas": [{"id": i, "topic": "App", "merge_with": card["id"], "conflict": {"with": card["id"], "question": "Bargeld annehmen?"}} for i in ids_in(spec, "Sort these:")]})

    asked = answer(client, sort=[conflict, conflict])
    cash = add(client, pid, "Accept cash at the counter", title="Cash too")
    ideas = by_id(settled(client, pid))
    assert ideas[cash["id"]]["conflicts"] == [card["id"]] and ideas[card["id"]]["conflicts"] == [cash["id"]]
    assert ideas[cash["id"]]["duplicate_of"] is None and asked["merge"] == []  # never merged
    asked_idea = ideas[cash["id"]]
    assert (asked_idea["question"], asked_idea["question_status"]) == ("Bargeld annehmen?", "open")
    assert [o["label"] for o in asked_idea["question_options"]] == ["Keep “Cash too”", "Keep “Card only”", "Both, in different contexts"]
    assert [i["id"] for i in client.get(f"/api/projects/{pid}/ideas/questions").json()] == [cash["id"]]

    # Keep the card idea: done without the model, the other one is left out, kept with the reason.
    resp = client.post(f"/api/projects/{pid}/ideas/{cash['id']}/answer", json={"option_index": 1})
    assert resp.status_code == 200, resp.text
    change = resp.json()
    assert change["kind"] == "resolve" and change["summary"]["dropped"] == [cash["id"]] and asked["intent"] == []
    every = by_id(ideas_of(client, pid, closed=True))
    assert every[cash["id"]]["status"] == "dismissed" and "contradicted" in every[cash["id"]]["closed_reason"] and every[card["id"]]["conflicts"] == []
    # Undone: the idea, the question and the marker are back.
    assert client.post(f"/api/projects/{pid}/ideas/changes/{change['id']}/undo").status_code == 200
    ideas = by_id(ideas_of(client, pid))
    assert ideas[cash["id"]]["question_status"] == "open" and ideas[cash["id"]]["conflicts"] == [card["id"]]

    # Both, in different contexts: both stay, no longer a conflict; sorted again, it is not raised again.
    resp = client.post(f"/api/projects/{pid}/ideas/{cash['id']}/answer", json={"option_index": 2})
    assert resp.status_code == 200, resp.text
    ideas = by_id(ideas_of(client, pid))
    assert len(ideas) == 2 and ideas[cash["id"]]["conflicts"] == [] and ideas[cash["id"]]["question_status"] == "answered"
    assert client.patch(f"/api/projects/{pid}/ideas/{cash['id']}", json={"text": "Accept cash at the counter, in the shop only"}).status_code == 200
    ideas = by_id(settled(client, pid))
    assert ideas[cash["id"]]["conflicts"] == [] and ideas[cash["id"]]["question_status"] == "answered" and ideas[cash["id"]]["duplicate_of"] is None

    # An own answer goes to the model, which may leave one of them out.
    def coupon(spec) -> str:
        return json.dumps({"ideas": [{"id": i, "topic": "App", "conflict": {"with": card["id"]}} for i in ids_in(spec, "Sort these:")]})

    answer(client, sort=[coupon])
    paper = add(client, pid, "Paper vouchers instead of cards", title="Paper vouchers")
    asked_idea = by_id(settled(client, pid))[paper["id"]]
    assert asked_idea["question"] == "“Paper vouchers” and “Card only” contradict each other. Which one applies?"
    asked = answer(client, intent=[json.dumps({"action": "drop", "ids": [paper["id"]], "reason": "cards stay"})])
    resp = client.post(f"/api/projects/{pid}/ideas/{paper['id']}/answer", json={"text": "Karten bleiben, Gutscheine nicht"})
    assert resp.status_code == 200, resp.text
    assert "Karten bleiben" in asked["intent"][0].prompt and "Card only" in asked["intent"][0].prompt
    assert resp.json()["kind"] == "resolve" and by_id(ideas_of(client, pid, closed=True))[paper["id"]]["status"] == "dismissed"
    assert by_id(ideas_of(client, pid))[card["id"]]["conflicts"] == []


def board(client, pid: int, topics: dict[str, list[str]]) -> dict[str, int]:
    """Ideas straight into the database, sorted into these topics: a board as it grew. Returns topic ids."""
    from plainboard.db import sessionmaker
    from plainboard.models import Idea, IdeaTopic

    async def write() -> dict[str, int]:
        async with sessionmaker()() as session:
            made = {}
            for n, (name, texts) in enumerate(topics.items()):
                topic = IdeaTopic(project_id=pid, name=name, color=n % 10)
                session.add(topic)
                await session.flush()
                made[name] = topic.id
                session.add_all([Idea(project_id=pid, title=text[:40], text=text, topic_id=topic.id, sort_state="sorted") for text in texts])
            await session.commit()
            return made

    return client.portal.call(write)


def lines_in(prompt: str, section: str) -> list[int]:
    part = prompt.split(section)[1].split("\n\n")[0]
    return [int(n) for n in re.findall(r"^i(\d+) ", part, re.M)]


def test_on_a_big_board_without_semantic_search_the_model_sees_the_ideas_sharing_words(client, project, monkeypatch):
    pid = project["id"]
    monkeypatch.setattr(idea_service, "BOARD_WHOLE", 4)
    board(client, pid, {
        "Payment": ["Pay by credit card at the checkout", "Credit card refunds for customers"],
        "Look": ["Dark theme for the night", "Bigger fonts for older people"],
        "Chat": ["Chat between team members", "Push notifications for new messages"],
    })  # fmt: skip
    asked = answer(client, sort=[sort_all])
    new = add(client, pid, "Save the credit card for the next payment")
    settled(client, pid)
    prompt = asked["sort"][0].prompt
    assert "Ideas closest to these (of 7 on the board):" in prompt and "Ideas already sorted" not in prompt
    shown = {i["text"] for i in ideas_of(client, pid)["ideas"] if i["id"] in lines_in(prompt, "Ideas closest to these")}
    assert shown == {"Pay by credit card at the checkout", "Credit card refunds for customers"}
    assert ids_in(asked["sort"][0], "Sort these:") == [new["id"]] and "- Look" in prompt  # every topic is named

    asked = answer(client, intent=[json.dumps({"action": "new", "ideas": [{"title": "Themes", "text": "More themes"}]})])
    typed(client, pid, "a dark theme in blue")
    intent = asked["intent"][0].prompt
    assert "Ideas (of 7: the closest to it and the newest):" in intent and "Dark theme for the night" in intent


def test_a_board_of_hundreds_of_ideas_never_goes_to_the_model_whole(client, project):
    pid = project["id"]
    words = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec romeo sierra tango".split()
    topics = {f"Topic {w}": [f"{w} feature number {n} with {words[(i + n) % len(words)]} extras" for n in range(15)] for i, w in enumerate(words)}
    board(client, pid, topics)  # 300 ideas in 20 topics
    asked = answer(client, sort=[sort_all] * 3)
    add(client, pid, "golf feature with hotel extras and a new twist")
    settled(client, pid)
    prompt = asked["sort"][0].prompt
    assert len(lines_in(prompt, "Ideas closest to these")) <= idea_service.NEARBY_MAX and len(prompt) < 30_000

    asked = answer(client, intent=[json.dumps({"action": "new", "ideas": [{"text": "x"}]})])
    typed(client, pid, "kilo extras for lima")
    intent = asked["intent"][0].prompt
    assert 0 < len(lines_in(intent, "the closest to it and the newest):")) <= idea_service.INTENT_NEAR + idea_service.INTENT_RECENT and len(intent) < 15_000

    # The button: every idea rearranged, group by group of related topics, each call bounded.
    asked = answer(client, reorder=["{}"] * 30)
    assert client.post(f"/api/projects/{pid}/ideas/sort").status_code == 202
    wait_for(lambda: len(asked["reorder"]) > 1 and not ideas_of(client, pid)["sorting"])
    seen = [lines_in(spec.prompt, "Ideas [topic id]:") for spec in asked["reorder"]]
    assert all(len(ids) <= idea_service.REORDER_IDEAS for ids in seen) and all(len(spec.prompt) < 40_000 for spec in asked["reorder"])
    assert sorted(i for ids in seen for i in ids) == sorted(i["id"] for i in ideas_of(client, pid)["ideas"])
    assert all("Other topics:" in spec.prompt for spec in asked["reorder"])
