"""Second step of the README's demo data (see seed_api.py): the project as it looks mid-work, written
straight into the database: turns running, a decision, a chat of several people with mentions,
subscriptions with their limits, branches on a train, ideas. Nothing here starts an agent.
usage (from the repository root): PYTHONPATH=app:protocol python scripts/screenshots/seed_db.py <data dir>/plainboard.db"""
import json, sys, time
from datetime import datetime, timedelta
from sqlalchemy import create_engine, select, update, delete
from sqlalchemy.orm import Session
from plainboard import models as m
from plainboard.services import ideas as idea_service

now = datetime.utcnow()
ago = lambda minutes: now - timedelta(minutes=minutes)
e = create_engine("sqlite:///" + sys.argv[1])
s = Session(e)
P = 1
agents = {a.name: a.id for a in s.scalars(select(m.Agent).where(m.Agent.project_id == P))}
MGR, BE, FE, TE = agents["Manager"], agents["Backend"], agents["Frontend"], agents["Tester"]
tasks = {t.number: t for t in s.scalars(select(m.Task).where(m.Task.project_id == P))}
s.execute(update(m.Project).where(m.Project.id == P).values(label_colors={"backend": "blue", "frontend": "teal", "content": "amber", "bug": "red", "infra": "gray"}))
status = {5: "todo", 6: "todo", 7: "todo", 8: "in_progress", 9: "in_progress", 10: "in_progress", 16: "in_progress", 11: "review", 12: "review", 13: "done", 14: "done", 15: "done"}
for n, st in status.items():
    tasks[n].status = st; tasks[n].position = n * 1024
tasks[11].user_action = "Add the pizzeria API key under Project settings, Secrets, then tell the manager."
tasks[12].customer_title = "Leaderboard"; tasks[14].customer_title = "Results page"
tasks[10].customer_title = "Plan pizza night in one place"
tasks[10].customer_description = "Who comes, which pizzeria and when to order, on one page."
tasks[10].description = "## Goal\n\nOne page to plan Friday: who comes, which pizzeria, when to order.\n\n- Works on phones\n- Pineapple is its own vote"
shots = list(s.scalars(select(m.Attachment.id).where(m.Attachment.task_id == tasks[10].id).order_by(m.Attachment.id)))
s.add(m.Decision(project_id=P, task_id=tasks[10].id, requested_by_agent_id=FE, question="Which layout for the planner?",
                 details="Both are built and running on the preview. Pick one, the other goes.",
                 options=[{"label": "A: One list", "description": "Everything on one page: guests, pizzeria, time. Quick on phones.", "attachment_ids": [shots[0]]},
                          {"label": "B: Steps", "description": "Three short steps with a summary at the end. Fewer mistakes, one more tap.", "attachment_ids": [shots[1]]}],
                 attachment_ids=[], status="open", created_at=ago(14), updated_at=ago(14)))
for task, who, body, mins in [(10, "Jonas", "@mia you have the casting vote, I am biased towards anything with a checkbox.", 11), (10, "Lena", "B looks nicer, but A is what I would use at 7 p.m. on a Friday.", 9),
                              (9, "Theo", "Can confirm, I voted once and it says 2.", 30)]:
    s.add(m.Comment(task_id=tasks[task].id, author_kind="user", author_name=who, body=body, created_at=ago(mins)))
s.add(m.Comment(task_id=tasks[10].id, author_kind="agent", author_agent_id=FE, body="Both layouts are on the preview: `/preview/5173/planner?layout=a` and `?layout=b`. The decision is on the ticket.", created_at=ago(14)))

# Running turns and assignments.
runs = {}
DOCS = agents["Docs"]
for agent, task, mins, trig in [(BE, 9, 12, "assignment"), (FE, 8, 4, "assignment"), (DOCS, 16, 2, "assignment"), (MGR, 5, 1, "ticket")]:
    r = m.Run(project_id=P, agent_id=agent, trigger=trig, status="running", task_id=tasks[task].id, context_tokens=48000 if agent == MGR else 12000, created_at=ago(mins), started_at=ago(mins))
    s.add(r); s.flush(); runs[agent] = r
def assignment(task, agent, title, st, mins, finished=None):
    a = m.Assignment(project_id=P, task_id=tasks[task].id, agent_id=agent, created_by_agent_id=MGR, title=title, instructions=title + ".", status=st,
                     run_id=runs[agent].id if st == "running" else None, created_at=ago(mins), updated_at=ago(finished if finished is not None else mins),
                     finished_at=None if finished is None else ago(finished), result=None if finished is None else "Done, handed over.")
    s.add(a); s.flush()
    if st == "running": runs[agent].assignment_id = a.id
    return a
assignment(9, BE, "Count every vote once, also after a reload", "running", 12)
assignment(8, FE, "Topping picker on phones", "running", 4)
assignment(16, DOCS, "Garlic bread FAQ on the site", "running", 2)
assignment(7, FE, "Show toppings and allergens", "queued", 3)
assignment(13, BE, "Login with magic link", "done", 95, 41)
assignment(14, FE, "Results page with confetti", "done", 120, 66)
assignment(15, TE, "Set up the CI check command", "done", 200, 150)

# The manager chat: several people, mentions.
conv = s.scalar(select(m.Conversation).where(m.Conversation.agent_id == MGR))
if not conv:
    conv = m.Conversation(agent_id=MGR, context_tokens=48000); s.add(conv); s.flush()
msgs = [
    ("user", "Last Friday the vote took 47 minutes and we ended up eating toast. Manager, please fix this.", {"trigger": "chat", "author": "Lena"}, 47),
    ("assistant", "Understood, that is a crisis. I made two tickets: #10 Pizza night planner and #5 Split the bill by slices eaten. #10 goes first: nobody pays for a pizza that never gets ordered. Frontend starts now.", {}, 46),
    ("user", "Also: Jonas somehow won the last vote with 7 votes. There are four of us.", {"trigger": "chat", "author": "Mia"}, 40),
    ("assistant", "That is #9: votes count twice after a reload. Backend is on it, it is urgent. @jonas, your 7 votes are under review.", {}, 39),
    ("user", "I want a lawyer. And extra cheese.", {"trigger": "chat", "author": "Jonas"}, 38),
    ("user", "", {"trigger": "wake", "inbox": ["Backend handed over #13 Login with magic link. Landed after one check run."]}, 30),
    ("user", "I added #16 Garlic bread counts as pizza. This is not a joke, it is a ticket.", {"trigger": "chat", "author": "Lena"}, 22),
    ("assistant", "#16 is on the board. Frontend built two layouts for the planner on #10 and needs a decision from one of you, the options are on the ticket. @mia, you have the casting vote.", {}, 14),
    ("user", "I'll pick one tonight. Pineapple stays a separate vote either way.", {"trigger": "chat", "author": "Mia"}, 8),
    ("assistant", "Noted on #10: pineapple gets its own vote, whatever the layout. Theo follows along as a viewer, so nobody can say they weren't told.", {}, 7),
]
for role, content, meta, mins in msgs:
    s.add(m.Message(conversation_id=conv.id, role=role, kind="text", content=content, meta=meta, created_at=ago(mins)))
users = {u.name: u.id for u in s.scalars(select(m.User))}
s.add(m.Mention(user_id=users["Mia"], project_id=P, kind="chat", agent_id=MGR, author="Manager", excerpt="@mia, you have the casting vote.", created_at=ago(14)))
s.add(m.Mention(user_id=users["Mia"], project_id=P, kind="comment", task_id=tasks[10].id, author="Jonas", excerpt="@mia you have the casting vote, I am biased towards anything with a checkbox.", created_at=ago(11)))

# Subscriptions and their limits; Tester runs on Codex, so both rings show.
s.execute(update(m.Agent).where(m.Agent.id == TE).values(provider="codex"))
s.execute(delete(m.Account))
s.add(m.Account(provider="claude_code", label="Claude Max", key="demo-claude", auth_mode="oauth_token", enabled=True))
s.add(m.Account(provider="codex", label="ChatGPT Pro", key="demo-codex", auth_mode="chatgpt", enabled=True))
t = int(time.time()); day = 86400
def growth(name, peak, per_day):
    return {name: {"peak": peak, "days": {(now - timedelta(days=n)).date().isoformat(): per_day for n in range(5)}}}
limits = {"accounts": {
    "demo-claude": {"windows": {"seven_day": {"name": "seven_day", "utilization": 0.46, "resets_at": t + 3 * day, "status": None, "seen_at": t - 120},
                                "seven_day_sonnet": {"name": "seven_day_sonnet", "utilization": 0.12, "resets_at": t + 3 * day, "status": None, "seen_at": t - 120}},
                    "growth": {**growth("seven_day", 0.46, 0.11), **growth("seven_day_sonnet", 0.12, 0.03)}, "updated_at": t - 120},
    "demo-codex": {"windows": {"codex_seven_day": {"name": "codex_seven_day", "utilization": 0.38, "resets_at": t + 2 * day, "status": None, "seen_at": t - 300}},
                   "growth": growth("codex_seven_day", 0.38, 0.07), "updated_at": t - 300},
}}
s.merge(m.Setting(key="provider_limits", value=json.dumps(limits), secret=False))

# Repositories and the flow: branches waiting, on a train, landed.
app = m.Repository(project_id=P, name="pizza-poll", url="https://git.example.com/friends/pizza-poll.git", default_branch="main", check_command="npm ci && npm test",
                   quick_check_command="npm run lint", check_target_seconds=300)
site = m.Repository(project_id=P, name="pizza-poll-site", url="https://git.example.com/friends/pizza-poll-site.git", default_branch="main", check_command="npm run build", land_first=True)
s.add_all([app, site]); s.flush()
train = m.Train(id=12, project_id=P, repository_id=app.id, mode="command", status="checking", branch="plainboard/train-12", base="main", size=2, created_at=ago(3), updated_at=ago(3))
s.add(train); s.flush()
for rid, branch, title, agent, task, st, mins, tr, merged in [
    (app.id, "be/vote-once", "Count every vote once", BE, 9, "open", 1, None, None),
    (app.id, "fe/toppings", "Show toppings and allergens", FE, 7, "train", 6, train.id, None),
    (app.id, "fe/results-confetti", "Results page with confetti", FE, 14, "train", 7, train.id, None),
    (app.id, "be/magic-link", "Login with magic link", BE, 13, "merged", 45, 11, 41),
    (app.id, "te/ci-check", "Set up the CI check command", TE, 15, "merged", 160, 10, 150),
    (app.id, "fe/leaderboard", "Leaderboard: most outvoted roommate", FE, 12, "open", 25, None, None),
    (site.id, "docs/imprint", "Imprint and privacy page", agents["Docs"], 4, "merged", 70, None, 68),
]:
    s.add(m.Landing(project_id=P, repository_id=rid, branch=branch, title=title, agent_id=agent, task_id=tasks[task].id, status=st, train_id=tr,
                    merged_at=ago(merged) if merged else None, created_at=ago(mins), updated_at=ago(merged or mins)))
s.flush()
s.execute(update(m.Landing).where(m.Landing.branch == "fe/leaderboard").values(hold="message", held_at=ago(24), updated_at=ago(24), hold_reason="Lena asked for a vote on the colours first"))
history = [
    (7, "landed", 300, 288, ["be/schema", "fe/app-shell", "te/lint", "docs/readme"], ""),
    (8, "failed", 240, 198, ["fe/vote-page", "be/pizzeria-stub"], "test: vote page shows the winner"),
    (9, "landed", 215, 233, ["fe/vote-page", "be/pizzeria-stub"], ""),
    (10, "landed", 150, 262, ["te/ci-check", "be/vote-api", "fe/share-link"], ""),
    (11, "landed", 41, 241, ["be/magic-link", "fe/results-page"], ""),
]
for tid, st, mins, secs, branches, failed in history:
    s.add(m.Train(id=tid, project_id=P, repository_id=app.id, mode="command", status=st, branch=f"plainboard/train-{tid}", base="main", size=len(branches), check_seconds=secs,
                  failed_checks=failed, group_no=1 if tid == 9 else None, created_at=ago(mins + secs / 60), updated_at=ago(mins), finished_at=ago(mins)))
    s.add(m.FlowEvent(project_id=P, at=ago(mins), kind=f"train.{st}", train_id=tid, data={"branches": branches}))
    if st == "failed":
        s.add(m.FlowEvent(project_id=P, at=ago(mins - 1), kind="train.repair_ordered", train_id=tid, agent_id=FE))

# Ideas: topics, cards, a question.
topics = {}
for i, (name, hint) in enumerate([("Voting", "4 ideas about who decides"), ("Ordering", "Getting the pizza here"), ("Money", "Who pays what"), ("Toppings", "What goes on it")]):
    tp = m.IdeaTopic(project_id=P, name=name, hint=hint, color=i * 3); s.add(tp); s.flush(); topics[name] = tp.id
for topic, title, text, author, votes, q in [
    ("Voting", "Ranked choice", "Rank your top three instead of one vote, so nobody ends up with their last choice.", "Lena", 3, None),
    ("Voting", "Veto once a month", "Everyone gets one veto per month. Use it wisely.", "Jonas", 2, None),
    ("Voting", "Vote closes at 6 p.m.", "No more votes after 6, whatever the score.", "Mia", 1,
     ("Should the vote close at a fixed time or once everyone has voted?", [{"label": "Fixed time"}, {"label": "When all have voted"}, {"label": "Whichever comes first"}])),
    ("Voting", "Pineapple is its own vote", "Settles the eternal debate without ruining the main vote.", "Theo", 4, None),
    ("Ordering", "Order with one tap", "Once the vote is closed, one button sends the order to the pizzeria.", "Jonas", 2, None),
    ("Ordering", "Delivery tracker", "Show where the pizza is, like a parcel.", "Lena", 0, None),
    ("Money", "Split by slices", "Count slices per person and split the bill by that.", "Mia", 2, None),
    ("Toppings", "Half-and-half pizzas", "Two toppings on one pizza, so the vote can end in a draw.", "Lena", 0, None),
    ("Toppings", "Allergy filter", "Hide every pizza someone in the group cannot eat.", "Theo", 0, None),
    ("Toppings", "Topping of the month", "One new topping each month that everyone has to try once.", "Jonas", 0, None),
    ("Money", "Tip jar", "Round up for the driver, shared evenly.", "Jonas", 1, None),
]:
    idea = m.Idea(project_id=P, title=title, text=text, author=author, status="open", topic_id=topics[topic], sort_state=idea_service.SORTED, votes=votes,
                  created_at=ago(600), updated_at=ago(600))
    if q:
        idea.question, idea.question_options, idea.question_status, idea.question_at = q[0], q[1], "open", ago(20)
    s.add(idea)
import asyncio
async def _c():
    s.commit()
asyncio.run(_c())
print("ok")
