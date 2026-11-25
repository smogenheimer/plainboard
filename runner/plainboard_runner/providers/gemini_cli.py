"""Gemini provider built on Google's Gemini CLI (``gemini -p ... --output-format stream-json``).

Auth: a Google account (the CLI's "Sign in with Google", so a Google AI subscription counts) or a Gemini API key.
The Google sign-in is made in the browser: the runner builds the CLI's own sign-in address (its OAuth client, read
from the installed CLI, PKCE), the page shows a code that is typed into PlainBoard, and the runner turns it into the
CLI's ``oauth_creds.json``. The app keeps that file (signins.py, ``own_login``); it never lives in the runner's home.

Each run of the CLI gets a home of its own (``GEMINI_CLI_HOME``) in a private folder: its settings (the board tools
as a stdio MCP server, mcp_stdio.py, the built-in tools of the toolset), the system prompt as the global context
file ``GEMINI.md``, and the sign-in. Its path depends on the system prompt only, so the prompt the CLI builds from it
stays the same from turn to turn (Gemini caches the prompt's start). Sessions (``tmp``) and history are shared by
every home, so a session resumes under any account (``--resume <session id>``).

The CLI has no way to add a message to a running turn: messages that arrive meanwhile go in as the next prompt of
the same session once the CLI is done; an urgent one ends a long tool call (the CLI is stopped) and goes in then.
Token use, cached tokens included, comes with the CLI's result. A Google account's quotas are read from Gemini Code
Assist (``retrieveUserQuota``, as the CLI's /stats does): one window per model, used up to ``1 - remainingFraction``;
they also name the models the account may use. An API key has no quota PlainBoard can read.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import signal
import sys
import tempfile
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from plainboard_protocol import (
    AssistantTextEvent,
    CompletionResult,
    CompletionSpec,
    Credentials,
    LimitsEvent,
    LimitWindow,
    McpServerSpec,
    ModelInfo,
    ProviderAccount,
    ProviderInfo,
    ResultEvent,
    RunEvent,
    RunSpec,
    SessionEvent,
    TextDeltaEvent,
    ToolResultEvent,
    ToolUseEvent,
    UsageEvent,
)

from .. import gemini_cli, notices, signins
from ..config import TOOL_RESULT_PREVIEW_CHARS
from ..mcp_stdio import BRIDGE_FILE_ENV, write_bridge_file
from ..signins import SIGN_INS
from ..tool_bridge import ToolBridge
from ..workspace import tool_env
from .base import Provider

log = logging.getLogger(__name__)

NAME = "gemini_cli"
SHARED = Path.home() / ".plainboard" / "gemini"  # sessions (tmp) and history of every home
BRIDGE_DIR = Path(tempfile.gettempdir()) / "plainboard-bridge"
SIGN_IN_FILES = ("oauth_creds.json", "google_accounts.json")
MCP_SERVER_NAME = "plainboard"
MAX_LINE_BYTES = 64 * 1024 * 1024
TOOL_TIMEOUT_SECONDS = 3600  # ask_agent may wait for another agent's whole turn
NOTICE_GRACE_SECONDS = 60
NOTICE_CHECK_SECONDS = 2
MODELS_TTL_SECONDS = 3600
HTTP_TIMEOUT = 20
LOGIN_TIMEOUT_SECONDS = 15 * 60

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
REDIRECT_URI = "https://codeassist.google.com/authcode"  # the page shows the code to type in (the CLI's NO_BROWSER sign-in)
SCOPES = ["https://www.googleapis.com/auth/cloud-platform", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/userinfo.profile"]
CODE_ASSIST = "https://cloudcode-pa.googleapis.com/v1internal"

MODELS = [
    ModelInfo(id="gemini-2.5-pro", label="Gemini 2.5 Pro", tier="large"),
    ModelInfo(id="gemini-2.5-flash", label="Gemini 2.5 Flash", tier="medium"),
    ModelInfo(id="gemini-2.5-flash-lite", label="Gemini 2.5 Flash Lite", tier="small"),
]
# The CLI's own tools as the transcript names them elsewhere (Claude's names), so they look the same.
TOOL_NAMES = {
    "run_shell_command": "Bash",
    "read_file": "Read",
    "read_many_files": "Read",
    "write_file": "Write",
    "replace": "Edit",
    "glob": "Glob",
    "grep_search": "Grep",
    "list_directory": "LS",
    "google_web_search": "WebSearch",
    "web_fetch": "WebFetch",
}
READ_TOOLS = ["read_file", "read_many_files", "glob", "grep_search", "list_directory"]
BOARD_TOOLS = [*READ_TOOLS, "run_shell_command", "replace", "write_file"]
# Asking the user, plan mode (waits for an approval nobody gives) and the CLI's own todo list (the PlainBoard task
# list is the one people see) make no sense for a headless agent.
EXCLUDED_TOOLS = ["ask_user", "enter_plan_mode", "exit_plan_mode", "write_todos"]
INTERRUPTED = "[PlainBoard] Your running tool call was interrupted for this message, and its work may be half done."


class GeminiError(RuntimeError):
    pass


def guess_tier(model_id: str) -> str:
    name = model_id.lower()
    if "lite" in name:
        return "small"
    if "flash" in name:
        return "medium"
    return "large"  # pro, auto


def model_infos(listed: list[dict[str, Any]]) -> list[ModelInfo]:
    """The models of ``gemini models -o json``."""
    known = {m.id: m for m in MODELS}
    out: dict[str, ModelInfo] = {}
    for m in listed:
        model_id = str(m.get("id") or "")
        if model_id:
            label = m.get("displayName") or model_id
            out.setdefault(model_id, known.get(model_id) or ModelInfo(id=model_id, label=label if label != model_id else model_id, tier=guess_tier(model_id)))
    return list(out.values())


def mcp_config(server: McpServerSpec) -> dict[str, Any]:
    if server.transport == "stdio":
        return {"command": server.command or "", "args": server.args, "env": server.env, "trust": True}
    if server.transport == "sse":
        return {"url": server.url or "", "headers": server.headers, "trust": True}
    return {"httpUrl": server.url or "", "headers": server.headers, "trust": True}


def settings(toolset: str, auth: str, servers: dict[str, Any]) -> dict[str, Any]:
    """The run's ``settings.json``: sign-in, no prompts nobody answers, the toolset's built-in tools, MCP servers."""
    tools: dict[str, Any] = {"exclude": EXCLUDED_TOOLS}
    if toolset == "read":
        tools["core"] = READ_TOOLS
    elif toolset == "board":
        tools["core"] = BOARD_TOOLS
    elif toolset == "none":
        tools["core"] = []  # none of the CLI's own
    return {
        "security": {"auth": {"selectedType": auth}, "folderTrust": {"enabled": False}},
        "general": {"enableAutoUpdate": False, "enableAutoUpdateNotification": False},
        "privacy": {"usageStatisticsEnabled": False},
        "telemetry": {"enabled": False},
        # Smaller prompt for the board's own toolsets: the repository's instructions only where the agent works on it.
        "context": {"fileName": ["GEMINI.md", "AGENTS.md"] if toolset == "full" else ["GEMINI.md"]},
        "tools": tools,
        "mcpServers": servers,
    }


def run_servers(spec: RunSpec, bridge_file: Path) -> dict[str, Any]:
    servers = {s.name: mcp_config(s) for s in spec.mcp_servers if s.name != MCP_SERVER_NAME}
    servers[MCP_SERVER_NAME] = {
        "command": sys.executable,
        "args": ["-m", "plainboard_runner.mcp_stdio"],
        "env": {BRIDGE_FILE_ENV: str(bridge_file)},
        "timeout": TOOL_TIMEOUT_SECONDS * 1000,
        "trust": True,
    }
    return servers


def mcp_tool_name(name: str, servers: list[str]) -> str:
    """``mcp_<server>_<tool>`` (the CLI's name) as ``mcp__<server>__<tool>``."""
    for server in sorted(servers, key=len, reverse=True):
        prefix = "mcp_" + re.sub(r"[^a-zA-Z0-9_\-.:]", "_", server) + "_"
        if name.startswith(prefix):
            return f"mcp__{server}__{name[len(prefix) :]}"
    return name


class Homes:
    """Homes of the CLI runs going on, each in a private folder. A run's home is named after its system prompt, so
    the prompt the CLI builds (it names the context file's path) is the same each turn; a second run with the same
    prompt at the same time gets the next name."""

    def __init__(self) -> None:
        self._used: set[str] = set()

    @contextlib.contextmanager
    def take(self, key: str) -> Iterator[Path]:
        n = 0
        while (name := f"gemini-{key}" + (f"-{n}" if n else "")) in self._used:
            n += 1
        self._used.add(name)
        signins.ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(signins.ROOT, 0o700)
        path = signins.ROOT / name
        shutil.rmtree(path, ignore_errors=True)
        (path / ".gemini").mkdir(mode=0o700, parents=True)
        try:
            yield path
        finally:
            self._used.discard(name)
            shutil.rmtree(path, ignore_errors=True)


HOMES = Homes()


def home_key(system_prompt: str) -> str:
    return hashlib.sha256(system_prompt.encode()).hexdigest()[:16]


def prepare_home(home: Path, config: dict[str, Any], memory: str, files: dict[str, str], shared: bool = True) -> None:
    gemini = home / ".gemini"
    gemini.mkdir(mode=0o700, parents=True, exist_ok=True)
    (gemini / "settings.json").write_text(json.dumps(config, indent=1))
    if memory.strip():
        (gemini / "GEMINI.md").write_text(memory)
    signins.write(gemini, files)
    if shared:  # sessions and history shared by every home: a session resumes under any account
        for name in ("tmp", "history"):
            target = SHARED / name
            target.mkdir(parents=True, exist_ok=True)
            (gemini / name).symlink_to(target, target_is_directory=True)


def context_tokens(session_id: str | None) -> int:
    """What the session's last model call took in and gave out (the CLI's session file has it, its stream not)."""
    if not session_id or not re.fullmatch(r"[0-9a-fA-F-]{8,64}", session_id):
        return 0
    found = sorted(SHARED.glob(f"tmp/*/chats/session-*-{session_id[:8]}*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not found:
        return 0
    try:
        lines = found[-1].read_text(errors="replace").splitlines()
    except OSError:
        return 0
    for line in reversed(lines):
        if '"tokens"' not in line:
            continue
        with contextlib.suppress(ValueError):
            record = json.loads(line)
            tokens = record.get("tokens") if isinstance(record, dict) else None
            if isinstance(tokens, dict) and tokens.get("input"):
                return int(tokens.get("input") or 0) + int(tokens.get("output") or 0)
    return 0


@dataclass
class Usage:
    input_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, stats: dict[str, Any]) -> None:
        cached = int(stats.get("cached") or 0)
        total_in = int(stats.get("input_tokens") or 0)
        self.input_tokens += int(stats["input"]) if stats.get("input") is not None else max(0, total_in - cached)
        self.cache_read_tokens += cached
        self.output_tokens += int(stats.get("output_tokens") or 0)
        self.calls += int(stats.get("tool_calls") or 0) + 1


@dataclass
class EventMapper:
    """Translates the CLI's stream-json lines into protocol events (kept separate for unit tests)."""

    servers: list[str] = field(default_factory=list)
    session_id: str | None = None
    text: str = ""  # the assistant's text since the last tool call
    last_text: str | None = None
    error: str | None = None
    status: str | None = None  # the result's: success, error
    stats: dict[str, Any] = field(default_factory=dict)
    open_tools: set[str] = field(default_factory=set)

    def _flush(self) -> list[RunEvent]:
        text, self.text = self.text, ""
        if not text.strip():
            return []
        self.last_text = text
        return [AssistantTextEvent(text=text)]

    def map(self, event: dict[str, Any]) -> list[RunEvent]:
        kind = event.get("type")
        if kind == "init":
            self.session_id = event.get("session_id") or self.session_id
        elif kind == "message" and event.get("role") == "assistant":
            text = str(event.get("content") or "")
            if event.get("delta"):
                self.text += text
                return [TextDeltaEvent(text=text)] if text else []
            self.text += text
        elif kind == "tool_use":
            tool_id, name = str(event.get("tool_id") or ""), str(event.get("tool_name") or "")
            self.open_tools.add(tool_id)
            params = event.get("parameters")
            name = TOOL_NAMES.get(name) or (mcp_tool_name(name, self.servers) if name.startswith("mcp_") else name)
            return [*self._flush(), ToolUseEvent(id=tool_id, name=name, input=params if isinstance(params, dict) else {})]
        elif kind == "tool_result":
            tool_id = str(event.get("tool_id") or "")
            self.open_tools.discard(tool_id)
            failed = event.get("status") != "success"
            content = event.get("output")
            if failed and isinstance(event.get("error"), dict):
                content = event["error"].get("message") or content
            return [ToolResultEvent(tool_use_id=tool_id, content=str(content or "")[:TOOL_RESULT_PREVIEW_CHARS], is_error=failed)]
        elif kind == "error":
            if event.get("severity") == "error":
                self.error = str(event.get("message") or "Gemini error")
        elif kind == "result":
            self.status = event.get("status")
            self.stats = event.get("stats") or {}
            if self.status != "success":
                self.error = str((event.get("error") or {}).get("message") or self.error or "Gemini CLI failed")
            return self._flush()
        return []


class Round:
    """One run of the CLI: a prompt into a session (a new one, or ``session_id``)."""

    def __init__(self, home: Path, env: dict[str, str], cwd: Path, model: str, session_id: str | None, prompt: str, mapper: EventMapper) -> None:
        self.home, self.env, self.cwd, self.model, self.session_id, self.prompt, self.mapper = home, env, cwd, model, session_id, prompt, mapper
        self.stderr_tail: deque[str] = deque(maxlen=30)
        self.interrupted = False
        self._proc: asyncio.subprocess.Process | None = None

    def args(self) -> list[str]:
        args = [gemini_cli.binary(), "-p", "", "--output-format", "stream-json", "--approval-mode", "yolo"]
        if self.model:
            args += ["-m", self.model]
        if self.session_id:
            args += ["--resume", self.session_id]
        return args

    async def _read_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        while line := await self._proc.stderr.readline():
            text = line.decode(errors="replace").rstrip()
            if text and not text.startswith(("YOLO mode", "Warning: 256-color", "[STARTUP]")):
                self.stderr_tail.append(text)

    def stderr(self) -> str:
        return "\n".join(self.stderr_tail)

    def stop(self) -> None:
        """The CLI and whatever it started (its own process group)."""
        if self._proc and self._proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self._proc.pid, signal.SIGKILL)

    async def events(self, run_id: str | None = None) -> AsyncIterator[dict[str, Any]]:
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self.args(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **self.env, "GEMINI_CLI_HOME": str(self.home), "NO_BROWSER": "true"},
                cwd=str(self.cwd),
                limit=MAX_LINE_BYTES,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise GeminiError("The Gemini CLI is not installed in the runner") from exc
        stderr_task = asyncio.create_task(self._read_stderr())
        try:
            assert self._proc.stdin and self._proc.stdout
            self._proc.stdin.write(self.prompt.encode())
            await self._proc.stdin.drain()
            self._proc.stdin.close()
            while True:
                try:
                    line = await asyncio.wait_for(self._proc.stdout.readline(), NOTICE_CHECK_SECONDS)
                except TimeoutError:
                    if run_id and self.mapper.open_tools and notices.pending(run_id) and notices.urgent_waiting(run_id) >= NOTICE_GRACE_SECONDS:
                        self.interrupted = True
                        self.stop()
                    continue
                if not line:
                    break
                with contextlib.suppress(ValueError):
                    event = json.loads(line)
                    if isinstance(event, dict):
                        yield event
            await self._proc.wait()
            await asyncio.wait_for(stderr_task, 5)
        finally:
            self.stop()
            stderr_task.cancel()


class GoogleLogin:
    """A Google sign-in in the browser: the address to open, then the code the page shows (``code``)."""

    def __init__(self, client_id: str, client_secret: str) -> None:
        self.client_id, self.client_secret = client_id, client_secret
        self.verifier = secrets.token_urlsafe(64)
        self.state = secrets.token_hex(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).rstrip(b"=").decode()
        query = {
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",  # a refresh token every time
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": self.state,
        }
        self.url: str | None = f"{GOOGLE_AUTH_URL}?{urlencode(query)}"
        self.started = time.monotonic()
        self.succeeded = False
        self.error: str | None = None

    @property
    def waiting(self) -> bool:
        return bool(self.url) and time.monotonic() - self.started < LOGIN_TIMEOUT_SECONDS

    async def exchange(self, code: str) -> dict[str, str]:
        """The code from the page as the CLI's sign-in files."""
        code = code.strip()
        if "code=" in code:  # the whole address pasted
            code = re.search(r"code=([^&\s]+)", code)[1]  # type: ignore[index]
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            answer = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri": REDIRECT_URI,
                    "grant_type": "authorization_code",
                    "code_verifier": self.verifier,
                },
            )
            if answer.status_code != 200:
                raise GeminiError(f"Google did not take the code: {answer.text[:300]}")
            files = {"oauth_creds.json": json.dumps(oauth_creds(answer.json()), indent=2)}
            with contextlib.suppress(httpx.HTTPError, ValueError, KeyError):
                info = await client.get(GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {answer.json()['access_token']}"})
                if email := info.json().get("email"):
                    files["google_accounts.json"] = json.dumps({"active": email, "old": []}, indent=2)
        return files


def oauth_creds(tokens: dict[str, Any], before: dict[str, Any] | None = None) -> dict[str, Any]:
    """A token answer as google-auth-library keeps it (the CLI's oauth_creds.json)."""
    out = dict(before or {})
    out.update({k: v for k, v in tokens.items() if k in ("access_token", "refresh_token", "scope", "token_type", "id_token") and v})
    if tokens.get("expires_in"):
        out["expiry_date"] = int((time.time() + int(tokens["expires_in"])) * 1000)
    return out


def client_credentials(binary: str | None = None) -> tuple[str, str]:
    """The installed CLI's OAuth client (an installed app's, public in its sources): the sign-in is the CLI's own."""
    path = shutil.which(binary or gemini_cli.binary())
    if not path:
        raise GeminiError("The Gemini CLI is not installed in the runner")
    bundle = Path(os.path.realpath(path)).parent
    for file in sorted(bundle.glob("*.js")):
        try:
            text = file.read_text(errors="replace")
        except OSError:
            continue
        client_id = re.search(r'OAUTH_CLIENT_ID\s*=\s*"([^"]+)"', text)
        client_secret = re.search(r'OAUTH_CLIENT_SECRET\s*=\s*"([^"]+)"', text)
        if client_id and client_secret:
            return client_id[1], client_secret[1]
    raise GeminiError("The Gemini CLI's sign-in client was not found in the installed CLI")


async def access_token(gemini_dir: Path, client: httpx.AsyncClient) -> str | None:
    """A valid access token of the sign-in in ``gemini_dir``, refreshed (and written back) when it ends soon.
    Google's refresh tokens stay the same, so a refresh here and one in a CLI elsewhere never clash."""
    try:
        creds = json.loads((gemini_dir / "oauth_creds.json").read_text())
    except (OSError, ValueError):
        return None
    if creds.get("access_token") and int(creds.get("expiry_date") or 0) > (time.time() + 120) * 1000:
        return creds["access_token"]
    if not creds.get("refresh_token"):
        return None
    client_id, client_secret = client_credentials()
    answer = await client.post(
        GOOGLE_TOKEN_URL, data={"refresh_token": creds["refresh_token"], "client_id": client_id, "client_secret": client_secret, "grant_type": "refresh_token"}
    )
    if answer.status_code != 200:
        raise GeminiError(f"The Google sign-in could not be refreshed: {answer.text[:300]}")
    creds = oauth_creds(answer.json(), creds)
    signins.write(gemini_dir, {"oauth_creds.json": json.dumps(creds, indent=2)})
    return creds.get("access_token")


def quota_windows(buckets: list[dict[str, Any]]) -> list[LimitWindow]:
    """One window per model of the account's quota (Gemini Code Assist), used up to ``1 - remainingFraction``."""
    windows: dict[str, LimitWindow] = {}
    for bucket in buckets:
        model, left = bucket.get("modelId"), bucket.get("remainingFraction")
        if not model or left is None:
            continue
        resets = None
        with contextlib.suppress(ValueError, TypeError):
            resets = int(datetime.fromisoformat(str(bucket.get("resetTime")).replace("Z", "+00:00")).timestamp())
        used = round(max(0.0, 1 - float(left)), 4)
        window = LimitWindow(name=str(model), utilization=used, resets_at=resets, status="rejected" if used >= 1 else None)
        if model not in windows or windows[model].utilization < used:
            windows[model] = window
    return list(windows.values())


@dataclass
class Quota:
    windows: list[LimitWindow]
    models: list[ModelInfo]
    plan: str | None


async def read_quota(gemini_dir: Path) -> Quota | None:
    """The quota and plan of the Google sign-in in ``gemini_dir``; None: not known (not signed in, not set up yet)."""
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        token = await access_token(gemini_dir, client)
        if not token:
            return None
        headers = {"Authorization": f"Bearer {token}"}
        metadata = {"ideType": "IDE_UNSPECIFIED", "platform": "PLATFORM_UNSPECIFIED", "pluginType": "GEMINI"}
        project = os.environ.get("GOOGLE_CLOUD_PROJECT") or None
        answer = await client.post(f"{CODE_ASSIST}:loadCodeAssist", headers=headers, json={"cloudaicompanionProject": project, "metadata": {**metadata, "duetProject": project}})
        answer.raise_for_status()
        loaded = answer.json()
        tier = loaded.get("paidTier") or loaded.get("currentTier") or {}
        project = loaded.get("cloudaicompanionProject") or project
        if isinstance(project, dict):
            project = project.get("id")
        if not project:
            return Quota([], [], tier.get("name"))  # set up with the first turn
        answer = await client.post(f"{CODE_ASSIST}:retrieveUserQuota", headers=headers, json={"project": project})
        answer.raise_for_status()
        buckets = [b for b in answer.json().get("buckets") or [] if isinstance(b, dict)]
    windows = quota_windows(buckets)
    return Quota(windows, model_infos([{"id": w.name} for w in windows]), tier.get("name"))


class GeminiCliProvider(Provider):
    name = NAME
    limits_complete = True  # the quota answer holds every model's

    def __init__(self) -> None:
        self._models: tuple[float, list[ModelInfo]] | None = None
        self._seen: dict[str, ModelInfo] = {}
        self._offered: dict[str, list[ModelInfo]] = {}
        self._plans: dict[str, str | None] = {}
        self._logins: dict[str, GoogleLogin] = {}

    def info(self) -> ProviderInfo:
        models = list(self._models[1] if self._models else MODELS)
        ids = {m.id for m in models}
        models += [m for m in self._seen.values() if m.id not in ids]
        return ProviderInfo(name=self.name, label="Gemini", models=models)

    async def catalog(self) -> ProviderInfo:
        if not self._models or time.monotonic() - self._models[0] > MODELS_TTL_SECONDS:
            try:
                models = model_infos(await gemini_cli.list_models(gemini_cli.binary()))
                self._models = (time.monotonic(), models or MODELS)
            except Exception as exc:
                log.warning("Could not list Gemini models: %s", str(exc)[:300])
                self._models = (time.monotonic() - MODELS_TTL_SECONDS + 300, self._models[1] if self._models else MODELS)
        return self.info()

    async def models(self, credentials: Credentials) -> list[str]:
        return [m.id for m in (await self.catalog()).models]

    @contextlib.asynccontextmanager
    async def _sign_in(self, creds: Credentials) -> AsyncIterator[tuple[str, dict[str, str], Path | None]]:
        """The run's sign-in: its kind for the CLI's settings, the files for its home, and the account's private folder
        they come from and go back to (a refreshed token), None for an API key."""
        if creds.gemini_auth_mode == "api_key":
            if not creds.gemini_api_key:
                raise RuntimeError("Gemini API key is not configured (Settings → Subscriptions).")
            yield "gemini-api-key", {}, None
            return
        if not creds.login_files:
            raise RuntimeError("This subscription is not signed in to Google yet (Settings → Subscriptions).")
        async with SIGN_INS.home(self.name, creds.login_account, creds.login_files, SIGN_IN_FILES) as folder:
            yield "oauth-personal", signins.read(folder, SIGN_IN_FILES), folder

    @staticmethod
    def _keep(home: Path, folder: Path | None) -> None:
        """What the CLI refreshed in the run's home back to the account's folder, from where the app takes it."""
        if folder is None:
            return
        now = signins.read(home / ".gemini", SIGN_IN_FILES)
        changed = {name: text for name, text in now.items() if text and text != signins.read(folder, (name,)).get(name)}
        if changed:
            signins.write(folder, changed)

    def _env(self, creds: Credentials, extra: dict[str, str]) -> dict[str, str]:
        env = {**tool_env(creds), **extra}
        if creds.gemini_auth_mode == "api_key" and creds.gemini_api_key:
            env["GEMINI_API_KEY"] = creds.gemini_api_key
        return env

    async def run(self, spec: RunSpec, bridge: ToolBridge) -> AsyncIterator[RunEvent]:
        async with self._sign_in(spec.credentials) as (auth, files, folder):
            with HOMES.take(home_key(spec.system_prompt)) as home:
                bridge_file = BRIDGE_DIR / f"{uuid.uuid4().hex}.json"
                write_bridge_file(bridge_file, spec.bridge, bridge.workspace)
                servers = run_servers(spec, bridge_file)
                prepare_home(home, settings(spec.toolset, auth, servers), spec.system_prompt, files)
                try:
                    async for event in self._turn(spec, home, bridge.workspace, list(servers)):
                        yield event
                finally:
                    bridge_file.unlink(missing_ok=True)
                    self._keep(home, folder)
            if folder is not None and (limits := await self._limits_after(spec.credentials, folder)):
                yield limits

    async def _turn(self, spec: RunSpec, home: Path, cwd: Path, servers: list[str]) -> AsyncIterator[RunEvent]:
        env = self._env(spec.credentials, spec.env)
        session_id, prompt, usage = spec.resume_session_id, spec.prompt, Usage()
        announced = False
        last_text: str | None = None
        while True:
            mapper = EventMapper(servers=servers, session_id=session_id)
            round_ = Round(home, env, cwd, spec.model, session_id, prompt, mapper)
            async for line in round_.events(spec.run_id):
                for event in mapper.map(line):
                    yield event
                if mapper.session_id and not announced:
                    announced = True
                    yield SessionEvent(session_id=mapper.session_id)
            session_id = mapper.session_id or session_id
            usage.add(mapper.stats)
            last_text = mapper.last_text or last_text
            tokens = context_tokens(session_id)
            if tokens:
                yield UsageEvent(context_tokens=tokens)
            if round_.interrupted:
                prompt = "\n\n".join([INTERRUPTED, *notices.drain(spec.run_id)])
                continue
            if mapper.status != "success":
                error = mapper.error or round_.stderr() or "The Gemini CLI ended without a result"
                if spec.resume_session_id and session_id == spec.resume_session_id and not announced and "session" in error.lower():
                    error = f"Gemini session not found: {spec.resume_session_id}\n{error}"
                yield self._result(session_id, usage, last_text, tokens, error)
                return
            if notices.pending(spec.run_id):  # messages that came meanwhile: the next prompt of the same session
                prompt = "\n\n".join(notices.drain(spec.run_id))
                continue
            yield self._result(session_id, usage, last_text, tokens, None)
            return

    @staticmethod
    def _result(session_id: str | None, usage: Usage, text: str | None, tokens: int, error: str | None) -> ResultEvent:
        return ResultEvent(
            session_id=session_id,
            is_error=error is not None,
            error=error,
            text=text,
            num_turns=usage.calls,
            input_tokens=usage.input_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            output_tokens=usage.output_tokens,
            context_tokens=tokens,
        )

    async def _limits_after(self, creds: Credentials, folder: Path) -> LimitsEvent | None:
        """The account's quota after a turn, so a used-up model is known at once (as Codex's come with its turns)."""
        try:
            quota = await read_quota(folder)
        except Exception as exc:
            log.info("Could not read the Gemini quota of account %s: %s", creds.login_account, str(exc)[:200])
            return None
        if quota is None or not quota.windows:
            return None
        self._remember(creds.login_account, quota)
        return LimitsEvent(windows=quota.windows, complete=True)

    def _remember(self, account: str, quota: Quota) -> None:
        self._offered[account] = quota.models
        self._seen.update({m.id: m for m in quota.models})
        self._plans[account] = quota.plan

    async def complete(self, spec: CompletionSpec) -> CompletionResult:
        async with self._sign_in(spec.credentials) as (auth, files, folder):
            with HOMES.take(home_key(spec.system_prompt)) as home, tempfile.TemporaryDirectory(prefix="plainboard-complete-") as cwd:
                prepare_home(home, settings("none", auth, {}), spec.system_prompt, files, shared=False)
                mapper = EventMapper()
                round_ = Round(home, self._env(spec.credentials, {}), Path(cwd), spec.model, None, spec.prompt, mapper)
                texts: list[str] = []
                try:
                    async for line in round_.events():
                        for event in mapper.map(line):
                            if isinstance(event, AssistantTextEvent):
                                texts.append(event.text)
                finally:
                    self._keep(home, folder)
        if mapper.status != "success":
            raise RuntimeError(f"Completion failed: {mapper.error or round_.stderr() or 'no result'}")
        usage = Usage()
        usage.add(mapper.stats)
        return CompletionResult(text="".join(texts).strip(), input_tokens=usage.input_tokens + usage.cache_read_tokens, output_tokens=usage.output_tokens)

    async def limits(self, credentials: Credentials) -> list[LimitWindow]:
        if credentials.gemini_auth_mode == "api_key" or not credentials.login_files:
            return []
        async with self._sign_in(credentials) as (_auth, _files, folder):
            assert folder is not None
            quota = await read_quota(folder)
        if quota is None:
            return []
        self._remember(credentials.login_account, quota)
        return quota.windows

    async def offered(self, credentials: Credentials) -> list[ModelInfo] | None:
        models = self._offered.pop(credentials.login_account, None)
        return models or None

    async def plan(self, credentials: Credentials) -> tuple[str | None, float | None]:
        return self._plans.pop(credentials.login_account, None), None

    async def account(self, key: str, own: bool = False) -> ProviderAccount | None:
        if not own:
            return None
        login = self._logins.get(key)
        if login is None:
            return ProviderAccount()
        waiting = login.waiting and not login.succeeded
        return ProviderAccount(signed_in=login.succeeded, login_url=login.url if waiting else None, needs_code=waiting, login_error=login.error)

    async def login(self, key: str, own: bool = False) -> ProviderAccount:
        if not own:
            raise NotImplementedError("Gemini's Google sign-in is kept by PlainBoard: sign in under Settings → Subscriptions")
        SIGN_INS.forget(key)
        self._logins[key] = GoogleLogin(*client_credentials())
        return await self.account(key, own=True) or ProviderAccount()

    async def login_code(self, key: str, code: str) -> ProviderAccount:
        login = self._logins.get(key)
        if login is None or not login.waiting:
            raise RuntimeError("No sign-in is waiting for a code. Start it again.")
        try:
            files = await login.exchange(code)
        except (GeminiError, httpx.HTTPError) as exc:
            login.error = str(exc)[:300]
        else:
            login.succeeded, login.error, login.url = True, None, None
            SIGN_INS.signed_in(self.name, key, files)
        return await self.account(key, own=True) or ProviderAccount()

    async def logout(self, key: str, own: bool = False) -> ProviderAccount:
        self._logins.pop(key, None)
        SIGN_INS.forget(key)
        return ProviderAccount()
