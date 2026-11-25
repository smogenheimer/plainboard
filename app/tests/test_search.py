"""Semantic search: texts prepared for retrieval, the index kept current, hybrid ranking, isolation, the switches."""

import hashlib
import json
import math
import re
from dataclasses import replace
from datetime import datetime

import pytest
from conftest import POSTGRES, idle, wait_for
from plainboard_protocol import GitCommit
from test_flow import chat, delegate, make_member, manager_of, runner, tool_results

from plainboard.services.search import documents
from plainboard.services.search.documents import Doc

pg_only = pytest.mark.skipif(not POSTGRES, reason="semantic search needs PostgreSQL with pgvector")


# --- texts ----------------------------------------------------------------------------------------


def test_clean_keeps_the_words_and_drops_the_markup():
    text = "## Fehler\n\n**Login** schlägt fehl, siehe [Logs](https://example.com/x).\n\n```python\nrefresh_token()\n```\n> zitiert\n\n---\n<b>fett</b> ![bild](a.png)"
    assert documents.clean(text) == "Fehler\n\nLogin schlägt fehl, siehe Logs.\n\nrefresh_token()\nzitiert\n\nfett bild"


def test_long_texts_are_cut_between_paragraphs_with_an_overlap():
    paragraphs = [f"Absatz {i}: " + "wort " * 60 for i in range(12)]
    chunks = documents.split("\n\n".join(paragraphs), size=1000, overlap=400)
    assert len(chunks) > 1 and all(len(c) <= 1000 for c in chunks)
    assert all(c.startswith("Absatz") for c in chunks)  # never in the middle of a paragraph
    for before, after in zip(chunks, chunks[1:]):
        assert after.split("\n\n")[0] in before  # the next one starts with the end of the one before
    one_line = "x" * 5000  # a log without any break is still cut
    assert all(len(c) <= documents.CHUNK_CHARS for c in documents.split(one_line))


def test_each_chunk_says_what_it_is_and_its_hash_changes_only_with_its_text():
    doc = Doc(kind="ticket", source_id="1", ref="#12", title="Login via **SSO**", body="Fails after a token refresh.", at=datetime(2026, 11, 20), facts=["done", "labels: auth"], ticket_number=12)
    (chunk,) = documents.prepare(doc, "PlainBoard")
    assert chunk.header == "Ticket #12 · PlainBoard · 2026-11-20 · done · labels: auth\nLogin via SSO"
    assert chunk.body == "Fails after a token refresh."
    assert documents.prepare(doc, "PlainBoard")[0].hash == chunk.hash
    doc.facts = ["in_progress", "labels: auth"]
    assert documents.prepare(doc, "PlainBoard")[0].hash != chunk.hash
    long = Doc(kind="report", source_id="2", ref="assignment #5", title="Report", body="\n\n".join(["Satz. " * 150] * 4), at=datetime(2026, 11, 20))
    parts = documents.prepare(long, "P")
    assert len(parts) > 1 and parts[1].header.startswith("Report assignment #5 · P · 2026-11-20 · part 2 of ")
    assert documents.prepare(Doc(kind="comment", source_id="3", ref="comment on #1", title="", body="ok", at=datetime(2026, 11, 20)), "P") == []


def test_a_text_longer_than_the_model_reads_is_embedded_in_windows_never_cut():
    import asyncio

    import numpy as np
    from tokenizers import Tokenizer, models, pre_tokenizers, processors

    from plainboard.services.search import embedding

    words = [f"w{i}" for i in range(60)]
    tokenizer = Tokenizer(models.WordLevel({w: i + 3 for i, w in enumerate(words)} | {"[CLS]": 0, "[SEP]": 1, "<pad>": 2, "[UNK]": 3}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.post_processor = processors.TemplateProcessing(single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 0), ("[SEP]", 1)])
    widths = []

    class Session:
        def get_inputs(self):
            return [type("I", (), {"name": "input_ids"}), type("I", (), {"name": "attention_mask"})]

        def run(self, _, feed):
            widths.append(feed["input_ids"].shape[1])
            return [np.repeat(feed["input_ids"][..., None].astype(np.float32), 4, axis=2)]

    model = embedding.LocalOnnxEmbedder(replace(embedding.CARDS["multilingual-e5-small"], max_tokens=24, passage_prefix=""))
    model._tokenizer, model._session, model._state = tokenizer, Session(), "ready"
    short, long = " ".join(words[:5]), " ".join(words)
    vectors = asyncio.run(model.passages([short, long]))
    assert model.split == 1 and max(widths) <= 24 and len(vectors) == 2  # 62 tokens: in windows of at most 24
    windows = model._windows(long, tokenizer.encode(long), "")
    assert windows[0].startswith("w0 ") and windows[-1].endswith(" w59") and len(windows) >= 3  # every word is in one


def test_a_markdown_file_is_cut_at_its_headings_and_each_chunk_names_its_section():
    intro = "Worum es geht: die Suche soll Anhänge finden."
    long = "\n\n".join(f"Absatz {i} über Vektoren und Chunks, " + "Einbettung " * 30 for i in range(6))
    text = f"# Entwurf\n\n{intro}\n\n## Datenbank\n\nPostgreSQL mit pgvector.\n\n```\n# kein Titel\n```\n\n## Skalierung\n\n{long}\n\n### Grenzen\n\nHöchstens 200 Chunks."
    chunks = documents.split_sections(text)
    assert chunks[0].startswith("Entwurf\nWorum es geht") and "Entwurf › Datenbank\nPostgreSQL mit pgvector." in chunks[0] and "kein Titel" in chunks[0]
    scaling = [c for c in chunks if "Absatz" in c]
    assert len(scaling) > 1 and all(c.startswith("Entwurf › Skalierung\n") for c in scaling)
    assert chunks[-1].endswith("Entwurf › Skalierung › Grenzen\nHöchstens 200 Chunks.")
    assert all(len(c) <= documents.CHUNK_CHARS + 30 for c in chunks)
    assert len(documents.split_sections(text, max_chunks=2)) == 2
    many = "\n\n".join(f"# Teil {i}\n\n" + "Wort " * 300 for i in range(100))
    assert len(documents.split_sections(many, max_chunks=7)) == 7


def test_only_text_is_read_of_a_file(tmp_path):
    from plainboard.services.search.indexers import FILE_READ_BYTES, read_text

    (tmp_path / "a.md").write_text("# Titel\n\nÄrger mit Umlauten ist keiner.")
    (tmp_path / "b.txt").write_bytes(b"PK\x03\x04\x00\x00binary")
    (tmp_path / "c.log").write_bytes(bytes(range(128, 256)) * 50)
    (tmp_path / "d.txt").write_text("x" * (FILE_READ_BYTES + 1000))
    assert read_text(tmp_path / "a.md") == "# Titel\n\nÄrger mit Umlauten ist keiner."
    assert read_text(tmp_path / "b.txt") is None and read_text(tmp_path / "c.log") is None and read_text(tmp_path / "gone.md") is None
    assert len(read_text(tmp_path / "d.txt")) == FILE_READ_BYTES


# --- where it is absent ----------------------------------------------------------------------------


def tool_names(client) -> set[str]:
    return {t.name for t in runner(client).specs[-1].bridge.tools}


@pytest.mark.skipif(POSTGRES, reason="SQLite only")
def test_with_sqlite_there_is_no_semantic_search(client, project):
    pid = project["id"]
    assert client.get("/api/meta").json()["semantic_search"] is False
    status = client.get("/api/search/status").json()
    assert status["available"] is False and status["documents"] == 0
    assert client.portal.call(client.app_ref.state.search.tick) is False
    chat(client, manager_of(client, pid)["id"], "SAY hi")
    wait_for(lambda: idle(client, pid))
    assert "search_history" not in tool_names(client)


# --- PostgreSQL with pgvector ---------------------------------------------------------------------


class FakeEmbedder:
    """Words hashed into 256 buckets: texts that share words are close, as a real model's would be for the same topic."""

    dim, state, error, progress, rate = 256, "ready", "", None, None

    def __init__(self, name: str = "fake"):
        from plainboard.services.search.embedding import CARDS, DEFAULT

        self.name = name
        self.card = replace(CARDS[DEFAULT], name=name, hint_similarity=0.5)  # the fake's scale is not a real model's
        self.embedded: list[str] = []

    def prepare(self) -> None:
        pass

    def unload(self) -> None:
        pass

    def _vector(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for word in re.findall(r"[^\W_]{3,}", text.lower()):
            v[int(hashlib.md5(word[:6].encode()).hexdigest(), 16) % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    async def passages(self, texts):
        self.embedded += texts
        return [self._vector(t) for t in texts]

    async def query(self, text):
        return self._vector(text)


@pytest.fixture
def embedder(monkeypatch):
    from plainboard.services.search import embedding

    fake = FakeEmbedder()
    monkeypatch.setattr(embedding, "_current", {embedding.DEFAULT: fake})
    return fake


def index(client) -> None:
    while client.portal.call(client.app_ref.state.search.tick):
        pass


def ticket(client, pid: int, title: str, description: str = "", **extra) -> dict:
    resp = client.post(f"/api/projects/{pid}/tasks", json={"title": title, "description": description, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


def search(client, agent_id: int, pid: int, **args) -> str:
    chat(client, agent_id, "TOOL search_history " + json.dumps(args))
    wait_for(lambda: idle(client, pid))
    return tool_results(client, agent_id)[-1]


@pg_only
def test_the_history_is_indexed_once_and_found_by_meaning_and_by_words(client, project, embedder):
    pid = project["id"]
    assert client.get("/api/meta").json()["semantic_search"] is True
    login = ticket(client, pid, "Anmeldung schlägt fehl", "Nach dem Token-Refresh fliegt man beim SSO-Login raus.", labels=["auth"])
    ticket(client, pid, "Dunkles Design für die Einstellungen", "Ein Dark-Mode-Schalter auf der Einstellungsseite.")
    assert client.post(f"/api/tasks/{login['id']}/comments", json={"body": "Der Session-Cookie wird beim Refresh nicht erneuert."}).status_code == 201
    assert client.post(f"/api/projects/{pid}/notes", json={"title": "CI-Cache", "content": "Docker-Builds in CI nutzen den buildx-Cache."}).status_code == 201
    index(client)
    status = client.get("/api/search/status").json()
    assert status["available"] and status["documents"] == status["embedded"] == 4
    assert {k: (v["items"], v["total"], v["embedded"]) for k, v in status["by_kind"].items()} == {
        "ticket": (2, 2, 2), "report": (0, 0, 0), "decision": (0, 0, 0), "comment": (1, 1, 1), "note": (1, 1, 1), "idea": (0, 0, 0), "doc": (0, 0, 0),
        "file": (0, 0, 0),
    }  # fmt: skip
    assert status["errors"] == []
    embedded = len(embedder.embedded)

    # Nothing changed: nothing is embedded again. A ticket moved on the board only: neither.
    index(client)
    assert client.patch(f"/api/tasks/{login['id']}", json={"position": 5}).status_code == 200
    index(client)
    assert len(embedder.embedded) == embedded
    # Its text changed: that ticket alone.
    assert client.patch(f"/api/tasks/{login['id']}", json={"description": "SSO-Login: nach dem Token-Refresh abgemeldet."}).status_code == 200
    index(client)
    assert len(embedder.embedded) == embedded + 1 and "abgemeldet" in embedder.embedded[-1]

    manager = manager_of(client, pid)
    found = search(client, manager["id"], pid, query="Anmeldung Token Refresh")
    first = found.splitlines()[0]
    assert first.startswith(f"- #{login['number']} ticket, ") and "Anmeldung schlägt fehl" in first
    assert "comment on #" in found  # the comment about it, too
    # By the start of a word (keyword part) and only what was asked for.
    only_notes = search(client, manager["id"], pid, query="buildx", kinds=["note"])
    assert only_notes.startswith("- note ") and "CI-Cache" in only_notes and "#" not in only_notes.splitlines()[0]
    assert "search_history" in tool_names(client)


@pg_only
def test_an_agent_finds_only_its_own_projects_history(client, project, embedder):
    other = client.post("/api/projects", json={"name": "Other"}).json()
    ticket(client, project["id"], "Payment provider times out", "Stripe webhook retries")
    ticket(client, other["id"], "Payment provider times out", "Stripe webhook retries in the other project")
    index(client)
    found = search(client, manager_of(client, project["id"])["id"], project["id"], query="payment webhook")
    assert len(found.splitlines()) == 2 and "other project" not in found  # one hit: its title line and snippet


@pg_only
def test_switched_off_nothing_is_indexed_and_the_agents_have_no_search(client, project, embedder):
    pid = project["id"]
    ticket(client, pid, "Something to find", "with enough words in it")
    assert client.patch(f"/api/projects/{pid}", json={"semantic_search": False}).json()["semantic_search"] is False
    index(client)
    assert client.get("/api/search/status").json()["documents"] == 0
    chat(client, manager_of(client, pid)["id"], "SAY hi")
    wait_for(lambda: idle(client, pid))
    assert "search_history" not in tool_names(client)
    # On again, but off for the whole installation: the same.
    client.patch(f"/api/projects/{pid}", json={"semantic_search": True})
    assert client.put("/api/settings", json={"values": {"semantic_search": False}}).status_code == 200
    index(client)
    assert client.get("/api/search/status").json()["documents"] == 0
    client.put("/api/settings", json={"values": {"semantic_search": True}})
    index(client)
    assert client.get("/api/search/status").json()["documents"] == 1


@pg_only
def test_commits_are_read_from_the_last_one_indexed_and_deleted_items_leave(client, project, embedder):
    from datetime import timedelta

    from plainboard.models import utcnow

    pid = project["id"]
    client.post(f"/api/projects/{pid}/repositories", json={"name": "api", "url": "https://github.com/acme/api.git"})
    fake = runner(client)
    fake.commits = [GitCommit(sha="b" * 40, author="Dev", date="2026-11-22T10:00:00+01:00", message="Fix webhook retries\n\nBackoff for Stripe.", files=["app/pay.py"], file_count=1, insertions=5, deletions=1)]
    note = client.post(f"/api/projects/{pid}/notes", json={"title": "Webhooks", "content": "Stripe sends retries for six hours."}).json()
    index(client)
    assert fake.git_log_specs[-1].since is None
    found = search(client, manager_of(client, pid)["id"], pid, query="Stripe webhook backoff", kinds=["commit"])
    assert re.match(r"- bbbbbbbbbb commit in api, 2026-11-22 \((?:.+ ago|yesterday)\); now: still on main: Fix webhook retries", found), found
    assert "Changed 1 file (+5 -1): app/pay.py" in found
    fake.gone = {"b" * 40}  # pushed over: no longer on main, and its file was removed since
    found = search(client, manager_of(client, pid)["id"], pid, query="Stripe webhook backoff", kinds=["commit"])
    assert "; now: no longer on main; 1 of its files no longer exist (app/pay.py): Fix webhook retries" in found
    fake.gone = set()

    fake.commits = [GitCommit(sha="c" * 40, message="Retry webhooks with jitter"), *fake.commits]
    later = utcnow() + timedelta(minutes=11)
    client.portal.call(client.app_ref.state.search.tick, later)
    assert fake.git_log_specs[-1].since == "b" * 40 and len(fake.git_log_specs) == 2  # from the last one read
    client.portal.call(client.app_ref.state.search.tick, later)
    assert len(fake.git_log_specs) == 2  # not with every look

    assert client.delete(f"/api/projects/{pid}/notes/{note['id']}").status_code in (200, 204)
    assert {k: v["items"] for k, v in client.get("/api/search/status").json()["by_kind"].items() if v["items"]} == {"commit": 2}  # at once


@pg_only
def test_the_older_history_is_read_page_by_page_to_the_root_while_new_commits_come_in(client, project, embedder, monkeypatch):
    from datetime import timedelta

    from plainboard.models import utcnow
    from plainboard.orchestration import search_indexer

    monkeypatch.setattr(search_indexer, "COMMITS_FIRST", 3)
    pid = project["id"]
    client.post(f"/api/projects/{pid}/repositories", json={"name": "api", "url": "https://github.com/acme/api.git"})
    fake = runner(client)
    fake.commits = [GitCommit(sha=f"{i:040x}", message=f"Change number {i}") for i in range(8, 0, -1)]  # 8 commits, newest first
    index(client)
    first, older = fake.git_log_specs[0], fake.git_log_specs[1]
    assert first.since is None and first.upto is None and (older.upto, older.skip) == ("8".zfill(40), 3)
    fake.commits = [GitCommit(sha="9".zfill(40), message="Change number 9"), *fake.commits]  # new on the branch meanwhile
    now = utcnow()
    for minutes in (1, 2, 11, 12):
        client.portal.call(client.app_ref.state.search.tick, now + timedelta(minutes=minutes))
    pages = [(s.upto, s.skip) for s in fake.git_log_specs if s.upto]
    assert pages == [("8".zfill(40), 3), ("8".zfill(40), 6)]  # from the same commit down; the last one has fewer than a page: the root
    assert [s.since for s in fake.git_log_specs if not s.upto] == [None, "8".zfill(40)]  # the new one, from the head
    commits = client.get("/api/search/status").json()["by_kind"]["commit"]
    assert (commits["items"], commits["total"]) == (9, 9)


@pg_only
def test_new_work_on_a_ticket_starts_with_what_is_very_close_in_the_history(client, project, embedder):
    pid = project["id"]
    old = ticket(client, pid, "Webhook retries of Stripe time out", "Stripe webhook retries time out after the payment")
    new = ticket(client, pid, "Stripe webhook retries time out again", "")
    index(client)
    make_member(client, pid, "Dev")
    chat(client, manager_of(client, pid)["id"], delegate("dev", "Stripe webhook retries", "Stripe webhook retries time out", ticket_number=new["number"]))
    wait_for(lambda: any("Possibly related" in s.prompt for s in runner(client).specs if s.run_id and s.prompt.count("Assignment #")))
    prompt = next(s.prompt for s in runner(client).specs if "Possibly related" in s.prompt)
    assert f"#{old['number']} ticket" in prompt and f"#{new['number']} ticket" not in prompt.split("Possibly related")[1]


@pg_only
def test_a_hit_says_how_old_it_is_and_where_its_item_stands_now(client, project, embedder):
    pid = project["id"]
    login = ticket(client, pid, "Anmeldung schlägt fehl", "Nach dem Token-Refresh fliegt man beim SSO-Login raus.")
    index(client)
    manager = manager_of(client, pid)["id"]
    assert " (today); now: ticket now backlog: Anmeldung schlägt fehl" in search(client, manager, pid, query="Anmeldung")
    # Done since it was indexed: the hit says so at once, from the ticket itself.
    assert client.patch(f"/api/tasks/{login['id']}", json={"status": "done"}).status_code == 200
    assert "now: ticket now done" in search(client, manager, pid, query="Anmeldung")


@pg_only
def test_the_boards_filter_searches_the_tickets_alone(client, project, embedder):
    pid = project["id"]
    login = ticket(client, pid, "Anmeldung schlägt fehl", "Nach dem Token-Refresh fliegt man beim SSO-Login raus.")
    assert client.post(f"/api/tasks/{login['id']}/comments", json={"body": "Anmeldung: der Session-Cookie wird beim Refresh nicht erneuert."}).status_code == 201
    assert client.post(f"/api/projects/{pid}/notes", json={"title": "Anmeldung", "content": "Anmeldung per SSO und Token-Refresh."}).status_code == 201
    index(client)
    everything = client.get(f"/api/projects/{pid}/search", params={"q": "Anmeldung Refresh"}).json()["hits"]
    assert {h["kind"] for h in everything} == {"ticket", "comment", "note"}
    dark = ticket(client, pid, "Dunkles Design für die Einstellungen", "Ein Dark-Mode-Schalter auf der Einstellungsseite.")
    index(client)
    only = client.get(f"/api/projects/{pid}/search", params={"q": "Anmeldung Refresh", "kinds": "ticket", "limit": 50}).json()
    assert only["state"] == "ok" and [(h["kind"], h["ticket_number"], h["task_id"]) for h in only["hits"]] == [
        ("ticket", login["number"], login["id"]), ("ticket", dark["number"], dark["id"])
    ]  # fmt: skip  # by meaning there is always a nearest
    close = client.get(f"/api/projects/{pid}/search", params={"q": "Anmeldung Refresh", "kinds": "ticket", "close": True}).json()["hits"]
    assert [h["task_id"] for h in close] == [login["id"]]  # the filter: only what stands out


@pg_only
def test_another_model_builds_its_index_in_the_background_and_search_switches_when_it_is_complete(client, project, embedder, monkeypatch):
    from plainboard.services.search import embedding

    pid = project["id"]
    ticket(client, pid, "Anmeldung schlägt fehl", "Nach dem Token-Refresh fliegt man beim SSO-Login raus.")
    ticket(client, pid, "Dunkles Design", "Ein Dark-Mode-Schalter auf der Einstellungsseite.")
    index(client)
    other = FakeEmbedder("other")
    other.state = "absent"  # it is downloaded first
    embedding._current["multilingual-e5-small"] = other
    status = client.post("/api/search/model", json={"model": "multilingual-e5-small"}).json()
    assert status["model"]["name"] == "fake" and status["next"]["name"] == "other" and status["next_embedded"] == 0
    assert {m["name"] for m in status["models"]} >= {"multilingual-e5-small", "multilingual-e5-base"} and any(m["recommended"] for m in status["models"])
    index(client)  # the new model is not ready: search goes on with the old one
    status = client.get("/api/search/status").json()
    assert status["model"]["name"] == "fake" and status["embedded"] == 2 and status["next_embedded"] == 0
    assert "Anmeldung schlägt fehl" in search(client, manager_of(client, pid)["id"], pid, query="Anmeldung Token")
    other.state = "ready"
    index(client)
    status = client.get("/api/search/status").json()
    assert status["model"]["name"] == "other" and status["next"] is None and status["embedded"] == 2  # switched, the old vectors gone
    assert client.post("/api/search/model", json={"model": "nope"}).status_code == 400


def rows(client, model, **where) -> list:
    from sqlalchemy import select

    from plainboard.db import sessionmaker

    async def read():
        async with sessionmaker()() as session:
            query = select(model)
            for key, value in where.items():
                query = query.where(getattr(model, key) == value)
            return list(await session.scalars(query))

    return client.portal.call(read)


def add(client, *items):
    from plainboard.db import sessionmaker

    async def write():
        async with sessionmaker()() as session:
            session.add_all(items)
            await session.commit()
            return [i.id for i in items]

    return client.portal.call(write)


@pg_only
def test_deleting_an_item_or_what_holds_it_takes_its_chunks_along_at_once_and_an_edit_replaces_them(client, project, embedder):
    from datetime import timedelta

    from plainboard.models import Assignment, Decision, SearchDocument, SearchVector, utcnow

    pid = project["id"]
    login = ticket(client, pid, "Anmeldung schlägt fehl", "Nach dem Token-Refresh fliegt man beim SSO-Login raus.")
    dark = ticket(client, pid, "Dunkles Design", "Ein Dark-Mode-Schalter auf der Einstellungsseite.")
    client.post(f"/api/tasks/{login['id']}/comments", json={"body": "Der Session-Cookie wird beim Refresh nicht erneuert."})
    note = client.post(f"/api/projects/{pid}/notes", json={"title": "CI-Cache", "content": "Docker-Builds in CI nutzen den buildx-Cache."}).json()
    member = make_member(client, pid, "Dev")
    done = {"status": "done", "finished_at": utcnow()}
    decision_id, *_ = add(
        client,
        Decision(project_id=pid, task_id=login["id"], question="Cookie oder Token?", details="Wie halten wir die Sitzung?", options=[{"label": "Cookie"}, {"label": "Token"}]),
        Assignment(project_id=pid, task_id=login["id"], agent_id=member["id"], title="Login reparieren", instructions="-", result="Refresh erneuert jetzt den Cookie.", **done),
        Assignment(project_id=pid, task_id=dark["id"], agent_id=member["id"], title="Dark Mode", instructions="-", result="Schalter in den Einstellungen gebaut.", **done),
    )
    client.post(f"/api/projects/{pid}/repositories", json={"name": "api", "url": "https://github.com/acme/api.git"})
    fake = runner(client)
    fake.commits = [GitCommit(sha=c * 40, message=f"Commit {c} about webhooks") for c in "abc"]
    index(client)

    def kinds() -> dict:
        out: dict = {}
        for d in rows(client, SearchDocument, project_id=pid):
            out[d.kind] = out.get(d.kind, 0) + 1
        return out

    assert kinds() == {"ticket": 2, "comment": 1, "note": 1, "decision": 1, "report": 2, "commit": 3}

    # Edited: its chunks are replaced, not added; the old text and its vector are gone.
    long = "\n\n".join(f"Absatz {i}: " + "Token Refresh Cookie " * 40 for i in range(4))
    client.patch(f"/api/tasks/{login['id']}", json={"description": long})
    index(client)
    chunks = rows(client, SearchDocument, kind="ticket", source_id=str(login["id"]))
    assert len(chunks) > 1 and not any("fliegt man" in c.body for c in chunks)
    client.patch(f"/api/tasks/{login['id']}", json={"description": "Kurz: abgemeldet nach Refresh."})
    client.patch(f"/api/projects/{pid}/notes/{note['id']}", json={"content": "CI nutzt jetzt den Registry-Cache."})
    client.post(f"/api/decisions/{decision_id}/answer", json={"option_index": 0})
    index(client)
    (chunk,) = rows(client, SearchDocument, kind="ticket", source_id=str(login["id"]))
    assert chunk.body == "Kurz: abgemeldet nach Refresh." and len(rows(client, SearchVector, document_id=chunk.id)) == 1
    assert [n.body for n in rows(client, SearchDocument, kind="note")] == ["CI nutzt jetzt den Registry-Cache."]
    assert "Answer: Cookie" in rows(client, SearchDocument, kind="decision")[0].body
    # Archived: still found, and said.
    client.patch(f"/api/tasks/{dark['id']}", json={"status": "done"})
    client.post(f"/api/projects/{pid}/tasks/archive")
    index(client)
    assert "archived" in rows(client, SearchDocument, kind="ticket", source_id=str(dark["id"]))[0].header
    assert "Dunkles Design" in search(client, manager_of(client, pid)["id"], pid, query="Dark Mode Schalter")

    # Deleted: gone at once, without the indexer: a note; a ticket with its comment, decision and report;
    # an agent with its reports; a repository with its commits.
    assert client.delete(f"/api/projects/{pid}/notes/{note['id']}").status_code == 204
    assert client.delete(f"/api/tasks/{login['id']}").status_code == 204
    assert kinds() == {"ticket": 1, "report": 1, "commit": 3}
    assert client.delete(f"/api/agents/{member['id']}").status_code == 204
    assert kinds() == {"ticket": 1, "commit": 3}
    # Commits no longer on the branch (a force push) go with the next sweep.
    fake.gone = {"a" * 40}
    client.portal.call(client.app_ref.state.search.tick, utcnow() + timedelta(hours=1))
    assert sorted(d.source_id[-1] for d in rows(client, SearchDocument, kind="commit")) == ["b", "c"]
    (repo,) = client.get(f"/api/projects/{pid}/repositories").json()
    assert client.delete(f"/api/repositories/{repo['id']}").status_code == 204
    assert kinds() == {"ticket": 1}
    # The project: everything of it.
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert rows(client, SearchDocument) == [] and rows(client, SearchVector) == []


@pg_only
def test_each_search_and_each_hint_is_a_flow_event_with_its_scores(client, project, embedder):
    from plainboard.models import FlowEvent

    pid = project["id"]
    ticket(client, pid, "Webhook retries of Stripe time out", "Stripe webhook retries time out after the payment")
    index(client)
    assert [e.data["embedded"] for e in rows(client, FlowEvent, kind="search.indexed")] == [1]
    manager = manager_of(client, pid)["id"]
    search(client, manager, pid, query="stripe webhook", kinds=["ticket"])
    (event,) = rows(client, FlowEvent, kind="search.query")
    assert event.agent_id == manager and event.data["query"] == "stripe webhook" and event.data["filters"]["kinds"] == ["ticket"]
    (hit,) = event.data["hits"]
    assert hit["ref"].startswith("#") and hit["vector_rank"] == 1 and hit["word_rank"] == 1 and hit["vector"] > 0.5 and event.data["model"] == "fake"


@pg_only
def test_ideas_are_searchable_and_their_neighbours_are_found_by_their_vectors(client, project, embedder, monkeypatch):
    from test_ideas import add, answer, board, ideas_of, lines_in, settled, sort_all

    from plainboard.orchestration import ideas as idea_orchestration
    from plainboard.services import idea_neighbours
    from plainboard.services import ideas as idea_service

    monkeypatch.setattr(idea_orchestration, "SORT_DELAY", 0.0)
    monkeypatch.setattr(idea_service, "BOARD_WHOLE", 3)
    monkeypatch.setattr(idea_service, "NEIGHBOURS", 2)
    pid = project["id"]
    board(client, pid, {"Payment": ["Pay by credit card at checkout", "Credit card refunds"], "Look": ["Dark theme at night", "Bigger fonts everywhere"]})
    index(client)
    assert client.get("/api/search/status").json()["by_kind"]["idea"]["embedded"] == 4
    monkeypatch.setattr(idea_neighbours.Words, "nearest", lambda self, *args, **kwargs: [])  # by meaning alone

    asked = answer(client, sort=[sort_all])
    new = add(client, pid, "Save the credit card for the next payment")
    settled(client, pid)
    assert "Save the credit card for the next payment" in embedder.embedded[-1]  # embedded at once, not with the indexer's next look
    texts = {i["id"]: i["text"] for i in ideas_of(client, pid)["ideas"]}
    assert {texts[i] for i in lines_in(asked["sort"][0].prompt, "Ideas closest to these")} == {"Pay by credit card at checkout", "Credit card refunds"}

    # Found in the search (Ctrl+K), to be opened; a closed idea leaves the index.
    hits = client.get(f"/api/projects/{pid}/search", params={"q": "save credit card"}).json()["hits"]
    hit = next(h for h in hits if h["kind"] == "idea" and h["idea_id"] == new["id"])
    assert hit["ref"] == f"idea i{new['id']}"
    assert client.delete(f"/api/projects/{pid}/ideas/{new['id']}").status_code in (200, 204)
    index(client)
    assert client.get("/api/search/status").json()["by_kind"]["idea"]["items"] == 4


@pg_only
def test_text_files_of_tickets_are_found_with_their_ticket_once_and_leave_when_deleted(client, project, embedder, tmp_path):
    from sqlalchemy import update

    from plainboard.db import sessionmaker
    from plainboard.models import Attachment, SearchCursor, SearchDocument, SearchVector, utcnow
    from plainboard.services.search.indexers import FILE_MAX_CHUNKS

    pid = project["id"]
    task = ticket(client, pid, "Recherche Vektor-Datenbanken", "Ergebnisse hängen an.")
    other = ticket(client, pid, "Dunkles Design", "Ein Dark-Mode-Schalter.")
    research = "# Recherche\n\nWir vergleichen Qdrant, Weaviate und pgvector.\n\n## Ergebnis\n\npgvector reicht: HNSW-Index ab einer Million Chunks."
    huge = "\n\n".join(f"Zeile {i}: " + "Protokoll Eintrag Fehler " * 10 for i in range(3000))
    files = [
        ("files", ("recherche.md", research.encode(), "text/markdown")),
        ("files", ("bild.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, "image/png")),
        ("files", ("kaputt.txt", b"PK\x03\x04\x00\x00 binary", "text/plain")),
        ("files", ("werte.json", b'{"engine": "pgvector", "grenze": "Million Chunks"}', "application/octet-stream")),
        ("files", ("riesig.log", huge.encode(), "text/plain")),
    ]
    out = client.post(f"/api/tasks/{task['id']}/attachments", files=files).json()
    ids = {a["filename"]: a["id"] for a in out}
    index(client)

    docs = rows(client, SearchDocument, project_id=pid, kind="file")
    by_file: dict = {}
    for d in docs:
        by_file.setdefault(d.source_id, []).append(d)
    assert set(by_file) == {str(ids["recherche.md"]), str(ids["werte.json"]), str(ids["riesig.log"])}
    assert len(by_file[str(ids["riesig.log"])]) == FILE_MAX_CHUNKS  # a long file: its start
    (md,) = by_file[str(ids["recherche.md"])]
    assert md.ticket_number == task["number"] and md.task_id == task["id"] and md.attachment_id == ids["recherche.md"]
    assert md.header.startswith(f"File recherche.md · {project['name']} · ") and f"on ticket #{task['number']}" in md.header and f"attachment {ids['recherche.md']}" in md.header
    assert "Recherche › Ergebnis\npgvector reicht" in md.body
    status = client.get("/api/search/status").json()["by_kind"]["file"]
    assert (status["items"], status["total"]) == (3, 4)  # the total: what may be text by its name; kaputt.txt is not

    # Unchanged: read and embedded once; another file of the ticket: only it.
    embedded = len(embedder.embedded)
    index(client)
    assert len(embedder.embedded) == embedded
    client.post(f"/api/tasks/{other['id']}/attachments", files=[("files", ("notiz.txt", "Dark Mode für die Einstellungen".encode(), "text/plain"))])
    index(client)
    assert len(embedder.embedded) == embedded + 1

    # Found by an agent with the file's name and path, and by people with the ticket that opens it.
    found = search(client, manager_of(client, pid)["id"], pid, query="pgvector HNSW Million", kinds=["file"])
    assert f"recherche.md file on #{task['number']} (/uploads/" in found and "Recherche Vektor-Datenbanken" in found
    hits = client.get(f"/api/projects/{pid}/search", params={"q": "Qdrant Weaviate HNSW", "kinds": "file"}).json()["hits"]
    assert (hits[0]["filename"], hits[0]["attachment_id"], hits[0]["task_id"]) == ("recherche.md", ids["recherche.md"], task["id"])
    board = client.get(f"/api/projects/{pid}/search", params={"q": "Qdrant Weaviate", "kinds": ["ticket", "file"], "close": True}).json()["hits"]
    assert board[0]["task_id"] == task["id"]

    # The retention deletes a file: it leaves with the next look. Deleted by hand: at once.
    async def purge():
        async with sessionmaker()() as session:
            await session.execute(update(Attachment).where(Attachment.id == ids["werte.json"]).values(purged_at=utcnow()))
            await session.commit()

    client.portal.call(purge)
    index(client)
    assert rows(client, SearchDocument, kind="file", source_id=str(ids["werte.json"])) == []
    assert client.delete(f"/api/attachments/{ids['recherche.md']}").status_code == 204
    assert rows(client, SearchDocument, kind="file", source_id=str(ids["recherche.md"])) == []
    # Its ticket deleted: its files with it.
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 204
    assert {d.source_id for d in rows(client, SearchDocument, kind="file")} == {str(a.id) for a in rows(client, Attachment, task_id=other["id"])}
    assert len(rows(client, SearchVector, project_id=pid)) == len(rows(client, SearchDocument, project_id=pid))
    assert rows(client, SearchCursor, project_id=pid, source="file")[0].value
