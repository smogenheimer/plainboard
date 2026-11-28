# Spec: clear human requests, system health, Telegram and a supervisor

Status: draft, agreed in design review 2026-11-28. Nothing is implemented yet.

## Problem

- When the user opens PlainBoard, tickets "for me" exist, but it is often unclear what exactly to do, whether
  the user is needed *now*, or whether work is still running that will need input *later*.
- Decisions often lack evidence (screenshots, diffs, links), so they are hard to make.
- There is no way to follow or steer PlainBoard from outside (phone).
- Agents cannot see why nothing runs. Example: the system is paused globally, the manager is enabled, and
  the agent wonders why it does not start. `prompts.with_preface` and `get_project` say nothing about any pause.

A Telegram bot alone would only push the same unclear data faster. So the fix starts at the data model; Telegram
and the supervisor are channels on top of it.

## Current state (facts from the code)

- `Decision` (`models.py`, `services/decisions.py`): question, Markdown details, up to 8 options, attachments per
  question and per option, status open/answered/withdrawn, answer via REST, MCP or `resolve_decision`.
  Non-blocking. Always belongs to a ticket. Only the manager may create one (`request_decision`).
- `Task.user_action`: plain string (500), no attachments, set by the manager via `update_task`, cleared via
  `user_action_done`.
- Status `review` means "user should look", but does not say whether the user is actually the one blocking.
- Pause: global (`Setting "pause"`, `RunManager.paused`), project (`Project.paused_at`), limit pause
  (`Project.limit_paused_at`). None of it reaches an agent's normal turn; MCP `list_projects` omits the global pause.
- Other silent "nothing runs" states: `Agent.enabled=false`, `auto_wake_manager=false`, runner offline,
  provider sign-in unusable, subscriptions used up.
- No outbound channel exists (no email, webhook, push, Telegram). Every `Agent` has a required `project_id`.

## Phase 1: data model and clarity

### 1.1 One kind of human request
- `Decision` gets `kind`: `decision` | `action` | `review`.
- `Task.user_action` is migrated into `Decision(kind=action)` (Alembic, SQLite and PostgreSQL); the column and
  `user_action_done` path are replaced by answering the request. UI, MCP and tools read only `Decision`.
- Actions and reviews can carry attachments like decisions do.

### 1.2 Announced requests
- New status `announced`: an agent says ahead of time "I will need X from you later". Shown separately; it does
  not count as "your turn" and does not push as a request. It turns `open` when the agent actually asks.

### 1.3 "Waiting on", derived
- Per ticket, computed from the database (never hand-maintained): `you` (open request) | `agent` (run in flight) |
  `check` | `landing` | `paused` (with which pause) | `nothing`.
- Shown on the board and used by Telegram and the supervisor. No new `TaskStatus`.

### 1.4 Evidence is required
- `request_decision` (and every request kind) rejects a call without at least one attachment or link unless the
  agent passes `no_evidence_reason`. The rejection is in the tool result, not in prompt text.

### 1.5 Attachment versions
Today `Attachment` is a flat list per ticket: a new screenshot lands next to the old ones, and reviewing means
comparing file names by hand.
- New column `Attachment.replaces_id` (self-reference, Alembic, entry in `transfer.TABLES`). A chain of versions is
  one "slot"; the newest is current.
- `attach_file` gets an optional `replaces` (attachment id). Without it, a file with the same name on the same
  ticket becomes the next version of that slot.
- The ticket shows only the current version of each slot, with a version badge ("v3"); older versions are folded
  under "Earlier versions". Telegram, the Mini App and open requests show current versions only.
- An image with a predecessor gets "Compare": side by side and a before/after slider against the previous version
  (web UI and Mini App).
- `request_decision` rejects attachment ids that were already replaced; the tool result names the current id.
- An answered request keeps showing the versions it was decided on.
- Old versions are kept; the project's retention deletes their files as before.

### 1.6 Health service: "why is nothing running"
- `services/health.py` computes a list of abnormal states, globally and per project: global/project/limit pause,
  manager disabled while tickets are open, `auto_wake_manager` off, runner offline or updating, provider sign-in
  unusable, subscriptions used up (with reset time), usage per subscription.
- Exposed as:
  - one line in `prompts.with_preface`, only when something is abnormal (keeps the system prompt stable);
  - agent tool `get_system_status` (short result);
  - MCP (and `list_projects` marks the global pause);
  - REST for a UI banner.
- `access.py` rules for every new route.

## Phase 2: Telegram (admin only)

- Single admin user. Settings: bot token (secret), the admin's Telegram user id. Every update from any other
  user id is ignored.
- Transport: long polling by a watcher in the app (no model, like `job_watcher`). No public webhook.
- **Live status message**: one pinned message, updated with `editMessageText` (edits do not notify), throttled
  to at most once a minute, plus an inline "Refresh" button. Content: health warnings, usage limits per
  subscription (percentage and reset time), per project: running / waiting on you / announced.
  Telegram's HTML subset only (`b i u s code pre a blockquote`, expandable blockquote).
- **Notifying pushes** (no quiet hours), one message each:
  - new request: question, details, evidence as photos, options as inline buttons, free-text answer by reply.
    Answers go through the same service as the UI (`decisions.answer`), so there is one truth;
  - alarms: pause on/off, runner gone, sign-in broken, limit pause, manager disabled with open tickets;
  - ticket landed;
  - failures: red check run, failed run, standstill.
- No hourly digest; the live message replaces it.
- **Mini App** in the same phase: a page in the frontend for status and requests with full evidence. The reverse
  proxy exposes only the Mini App page and `/api/telegram/*`. Authentication only by Telegram `initData`
  (HMAC with the bot token) matched against the admin's user id; the rest of PlainBoard stays internal.
- Deep links from every message into the web UI.

### Setup (what the user guide will say: "Connect Telegram")
1. Create a bot with @BotFather and copy its token.
2. In PlainBoard, Settings → Telegram: paste the token. PlainBoard starts polling; nothing has to be reachable
   from outside for the bot itself.
3. Send `/start` to the bot. PlainBoard shows a one-time code in the settings; send it to the bot to link your
   Telegram account to your admin user. Other Telegram users are ignored.
4. Optional, for the Mini App: PlainBoard must be reachable over public HTTPS. Set the public base URL in the
   settings and let your reverse proxy forward the Mini App page and `/api/telegram/*` (any proxy works; see
   "Behind a reverse proxy" in docs/guide.md). PlainBoard registers the Mini App button with the bot.

## Phase 3: supervisor

- A normal agent in a hidden system project (excluded from project lists and `transfer`), run through the runner
  like every agent, so the app stays provider-agnostic. Model configurable, default a fast and cheap one.
- **System level, never held by a project's or the installation's state.** Its moments are exactly the ones
  where nothing else runs ("why is nothing running?"), so:
  - "Pause everything", the project pause and the limit pause never hold its turns (the dispatcher exempts the
    system project);
  - it takes none of a project's or owner's parallel slots and is never stopped by `pause_team`;
  - **reserved room on the subscriptions**: the limit pause stops projects before the subscriptions are full;
    that last share is kept for the supervisor (a small, cheap model by default). An admin can pin it to its own
    account;
  - when no subscription has room at all, it still answers without a model: the health check, the live status
    and the open requests are plain data, with a note when the model is back.
- Reads across all projects (board, requests, runs, health) with admin rights.
- Never writes on its own. Every change (ticket, comment, answer to an agent, pause/resume) is a proposal with
  buttons first; only a press performs it.
- Rovo-style flow: a plain question gets only an answer. When a message sounds like work, it proposes a ticket
  (project, title, short description) with buttons Create / Change / No. Never automatic.
- One running session, summarized when long; `/neu` starts fresh. A reply to a request message refers to exactly
  that request.
- The same session is available in the web UI (admin page); Telegram is only one channel.

## Open for implementation

- Exact tool names and result texts (keep them short).
- How the hidden system project is excluded everywhere (lists, access rules, transfer, search).
- Migration details for existing `user_action` values.
