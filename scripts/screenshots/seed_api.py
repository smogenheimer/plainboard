"""The README's screenshots: a throwaway instance with an example project of four people, photographed
with Playwright. Nothing here starts an agent or touches a real installation.

    # the app from this checkout, on SQLite, without a runner and with its watchers asleep
    cd frontend && npm ci && npm run build && cd ..
    export PLAINBOARD_DATABASE=sqlite PLAINBOARD_DATA_DIR=/tmp/pb-demo PLAINBOARD_UPLOADS_DIR=/tmp/pb-demo/uploads \
      PLAINBOARD_STATIC_DIR=$PWD/frontend/dist PLAINBOARD_RUNNER_URL=http://127.0.0.1:1
    for v in SCHEDULER JOB_WATCH LIMITS LANDING TIDY CLEANUP SEARCH STANDSTILL UPDATE; do export PLAINBOARD_${v}_INTERVAL_SECONDS=100000000; done
    (cd app && PYTHONPATH=.:../protocol uvicorn plainboard.main:app --port 8080) &
    python scripts/screenshots/seed_api.py http://127.0.0.1:8080              # people, project, agents, tickets
    PYTHONPATH=app:protocol python scripts/screenshots/seed_db.py /tmp/pb-demo/plainboard.db
    BASE=http://127.0.0.1:8080 OUT=/tmp/shots node scripts/screenshots/shoot.mjs   # needs playwright
    for f in /tmp/shots/*.png; do cwebp -q 80 -m 6 $(case $f in */phone-*) ;; *) echo -resize 2400 0;; esac) $f -o docs/screenshots/$(basename $f .png).webp; done

Desktop 1600x1000, phone 390x844, both at 2x, in the slop2gether design, dark and light.
usage: seed_api.py <base url>"""
import sys
from pathlib import Path

import httpx
B = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8080"
c = httpx.Client(base_url=B, headers={"Origin": B}, timeout=30)
def ok(r):
    if r.status_code >= 400: print("ERR", r.request.method, r.request.url, r.status_code, r.text[:300])
    return r.json() if r.content else None
ok(c.post("/api/auth/setup", json={"username": "mia", "name": "Mia", "password": "demo-pass-1234"}))
ok(c.put("/api/settings", json={"values": {"setup_done": True}}))
users = {}
for u, n in [("jonas", "Jonas"), ("lena", "Lena"), ("theo", "Theo")]:
    users[u] = ok(c.post("/api/users", json={"username": u, "name": n, "password": "demo-pass-1234"}))["id"]
p = ok(c.post("/api/projects", json={"name": "Pizza Poll", "description": "A small web app for four friends to vote on Friday's pizza."})); pid = p["id"]
ok(c.patch(f"/api/projects/{pid}", json={"auto_wake_manager": False}))
for u, role in [("jonas", "member"), ("lena", "member"), ("theo", "viewer")]:
    ok(c.put(f"/api/projects/{pid}/members/{users[u]}", json={"role": role}))
for name, role, color, desc in [
    ("Backend", "API and database", "#3b6fd4", "Owns the vote API, the pizzeria integration and the database."),
    ("Frontend", "App and UI", "#0e8f8a", "Builds the voting pages and the planner."),
    ("Tester", "Tests and checks", "#b7791f", "Writes tests and keeps the check command green."),
    ("Docs", "Readme and copy", "#c2417a", ""),
]:
    ok(c.post(f"/api/projects/{pid}/agents", json={"name": name, "role": role, "color": color, "description": desc}))
tickets = [
    ("Veto button for pineapple", ["frontend"], "medium"),
    ("Dark mode for 2 a.m. orders", ["frontend"], "low"),
    ("Rate-limit votes per roommate", ["backend"], "high"),
    ("Imprint and privacy page", ["content"], "medium"),
    ("Split the bill by slices eaten", ["backend"], "high"),
    ("Push notification: the pizza is here", ["backend"], "medium"),
    ("Show toppings and allergens", ["frontend"], "medium"),
    ("Topping picker on phones", ["frontend"], "medium"),
    ("Votes count twice after a reload", ["backend", "bug"], "urgent"),
    ("Pizza night planner", ["frontend"], "medium"),
    ("Order straight from the pizzeria API", ["backend"], "medium"),
    ("Leaderboard: most outvoted roommate", ["frontend"], "medium"),
    ("Login with magic link", ["backend"], "medium"),
    ("Results page with confetti", ["frontend"], "medium"),
    ("Set up the CI check command", ["infra"], "medium"),
    ("Garlic bread counts as pizza", ["content"], "low"),
]
for title, labels, prio in tickets:
    t = ok(c.post(f"/api/projects/{pid}/tasks", json={"title": title, "status": "backlog"}))
    ok(c.patch(f"/api/tasks/{t['id']}", json={"labels": labels, "priority": prio, "description": f"## Goal\n\n{title}.\n\n- Works on phones\n- Covered by a test\n\n```bash\nnpm run check\n```"}))
# The two layouts Frontend built for #10, to compare on its decision.
here = Path(__file__).parent
ok(c.post("/api/tasks/10/attachments", files=[("files", (f"planner-{v}.png", (here / f"planner-{v}.png").read_bytes(), "image/png")) for v in "ab"]))
ok(c.post(f"/api/projects/{pid}/notes", json={"title": "House rules", "content": "1. One vote per person per Friday\n2. Pineapple is always its own vote\n3. Whoever loses orders", "pinned": True}))
print("project", pid, users)
