import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from plainboard_protocol import RunSpec

from plainboard_runner import gemini_cli, notices, signins
from plainboard_runner.providers import gemini_cli as gemini
from plainboard_runner.providers.gemini_cli import EventMapper, GeminiCliProvider, GoogleLogin, Usage, mcp_tool_name, quota_windows, settings
from plainboard_runner.tool_bridge import ToolBridge

TOOL = {"name": "create_ticket", "description": "Create a ticket.", "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}}}


def spec(base_url: str = "http://app", **overrides) -> RunSpec:
    data = {
        "run_id": "r1",
        "provider": "gemini_cli",
        "model": "gemini-2.5-pro",
        "system_prompt": "You are a test agent. MARKER-SYSTEM",
        "prompt": "Do it.",
        "workspace": {"path": "/workspaces/p/a"},
        "credentials": {"gemini_auth_mode": "api_key", "gemini_api_key": "test-key"},
        "bridge": {"base_url": base_url, "run_id": "r1", "run_token": "t", "tools": [TOOL]},
    }
    return RunSpec.model_validate({**data, **overrides})


def test_stream_json_becomes_run_events():
    mapper = EventMapper(servers=["plainboard", "my-docs"])
    lines = [
        {"type": "init", "session_id": "s-1", "model": "gemini-2.5-pro"},
        {"type": "message", "role": "user", "content": "hi"},
        {"type": "message", "role": "assistant", "content": "Let me ", "delta": True},
        {"type": "message", "role": "assistant", "content": "look.", "delta": True},
        {"type": "tool_use", "tool_name": "run_shell_command", "tool_id": "t1", "parameters": {"command": "ls"}},
        {"type": "tool_result", "tool_id": "t1", "status": "success", "output": "a.txt"},
        {"type": "tool_use", "tool_name": "mcp_my-docs_search", "tool_id": "t2", "parameters": {"q": "x"}},
        {"type": "tool_result", "tool_id": "t2", "status": "error", "error": {"type": "x", "message": "boom"}},
        {"type": "message", "role": "assistant", "content": "Done.", "delta": True},
        {"type": "result", "status": "success", "stats": {"input_tokens": 2400, "cached": 1600, "input": 800, "output_tokens": 60, "tool_calls": 2}},
    ]
    events = [e for line in lines for e in mapper.map(line)]
    kinds = [e.type for e in events]
    assert kinds == ["text_delta", "text_delta", "assistant_text", "tool_use", "tool_result", "tool_use", "tool_result", "text_delta", "assistant_text"]
    assert mapper.session_id == "s-1" and events[2].text == "Let me look."
    assert events[3].name == "Bash" and events[3].input == {"command": "ls"} and events[4].content == "a.txt"
    assert events[5].name == "mcp__my-docs__search" and events[6].is_error and events[6].content == "boom"
    assert mapper.last_text == "Done." and mapper.status == "success" and not mapper.open_tools
    usage = Usage()
    usage.add(mapper.stats)
    assert (usage.input_tokens, usage.cache_read_tokens, usage.output_tokens, usage.calls) == (800, 1600, 60, 3)


def test_board_tool_names_and_toolsets():
    assert mcp_tool_name("mcp_plainboard_create_ticket", ["plainboard"]) == "mcp__plainboard__create_ticket"
    assert settings("none", "gemini-api-key", {})["tools"]["core"] == []
    assert "run_shell_command" not in settings("read", "gemini-api-key", {})["tools"]["core"]
    full = settings("full", "oauth-personal", {"plainboard": {}})
    assert "core" not in full["tools"] and "ask_user" in full["tools"]["exclude"] and full["security"]["auth"]["selectedType"] == "oauth-personal"


def test_quota_buckets_become_windows():
    windows = quota_windows(
        [
            {"modelId": "gemini-2.5-pro", "remainingFraction": 0.25, "resetTime": "2026-11-26T00:00:00Z", "tokenType": "REQUESTS"},
            {"modelId": "gemini-2.5-flash", "remainingFraction": 0, "resetTime": "2026-11-26T00:00:00Z"},
            {"modelId": "gemini-2.5-flash", "remainingFraction": 0.5},
            {"remainingFraction": 0.5},
        ]
    )
    assert [(w.name, w.utilization, w.status) for w in windows] == [("gemini-2.5-pro", 0.75, None), ("gemini-2.5-flash", 1.0, "rejected")]
    assert windows[0].resets_at == 1795651200


def test_google_sign_in_is_the_cli_s_own(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "chunk.js").write_text('var OAUTH_CLIENT_ID = "id-1.apps.googleusercontent.com";\nvar OAUTH_CLIENT_SECRET = "s3cret";\n')
    (bundle / "gemini.js").write_text("#!/usr/bin/env node\n")
    (bundle / "gemini.js").chmod(0o755)
    assert gemini.client_credentials(str(bundle / "gemini.js")) == ("id-1.apps.googleusercontent.com", "s3cret")
    login = GoogleLogin("id-1", "s3cret")
    assert login.waiting and "code_challenge_method=S256" in login.url and "codeassist.google.com%2Fauthcode" in login.url and "access_type=offline" in login.url


# --- The real CLI against a stand-in for Gemini's API ---------------------------------------------------------


class FakeGemini(BaseHTTPRequestHandler):
    """Gemini's API as far as a turn goes: a tool call for CALLSHELL, CALLBOARD and SLEEP, else text."""

    requests: list[dict] = []

    def log_message(self, *_args):
        pass

    def _json(self, obj: dict) -> None:
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        if self.path.startswith("/api/internal/runs/"):  # the app's end of the board tools
            return self._json({"text": f"created #7 ({body.get('title')})"})
        if ":countTokens" in self.path:
            return self._json({"totalTokens": 10})
        FakeGemini.requests.append(body)
        last = (body.get("contents") or [{}])[-1]
        answered = any("functionResponse" in part for part in last.get("parts", []))
        said = " ".join(part.get("text", "") for part in last.get("parts", []))
        call = None
        if not answered:
            if "CALLBOARD" in said:
                call = {"name": "mcp_plainboard_create_ticket", "args": {"title": "x"}}
            elif "CALLSHELL" in said:
                call = {"name": "run_shell_command", "args": {"command": "echo hi-from-shell"}}
            elif "SLEEP" in said:
                call = {"name": "run_shell_command", "args": {"command": "sleep 60"}}
        parts = [{"functionCall": call}] if call else [{"text": "Done: " + ("tool ok" if answered else said[-40:])}]
        chunk = {
            "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP", "index": 0}],
            "usageMetadata": {"promptTokenCount": 1200, "candidatesTokenCount": 30, "cachedContentTokenCount": 800, "totalTokenCount": 1230},
        }
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())


needs_cli = pytest.mark.skipif(not shutil.which(gemini_cli.binary()), reason="Gemini CLI not installed")


@pytest.fixture
def fake_gemini(tmp_path, monkeypatch):
    FakeGemini.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGemini)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", url)
    monkeypatch.setattr(gemini, "SHARED", tmp_path / "shared")
    monkeypatch.setattr(signins, "ROOT", tmp_path / "signins")
    monkeypatch.setattr(gemini, "BRIDGE_DIR", tmp_path / "bridge")
    yield url
    server.shutdown()


async def run(provider: GeminiCliProvider, run_spec: RunSpec, workspace: Path) -> list:
    bridge = ToolBridge(run_spec.bridge, workspace)
    try:
        return [event async for event in provider.run(run_spec, bridge)]
    finally:
        await bridge.aclose()


@needs_cli
async def test_a_real_turn_with_board_tools_and_a_resumed_session(fake_gemini, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    provider = GeminiCliProvider()
    events = await run(provider, spec(fake_gemini, prompt="please CALLBOARD", workspace={"path": str(workspace)}), workspace)
    by_type = {e.type: e for e in events}
    session = by_type["session"].session_id
    assert by_type["tool_use"].name == "mcp__plainboard__create_ticket" and "created #7 (x)" in by_type["tool_result"].content
    result = events[-1]
    assert result.type == "result" and not result.is_error and result.session_id == session and result.text == "Done: tool ok"
    assert (result.input_tokens, result.cache_read_tokens, result.output_tokens) == (800, 1600, 60) and result.context_tokens == 1230
    assert "MARKER-SYSTEM" in json.dumps(FakeGemini.requests[0]["systemInstruction"])

    events = await run(provider, spec(fake_gemini, prompt="now CALLSHELL", resume_session_id=session, workspace={"path": str(workspace)}), workspace)
    tool = next(e for e in events if e.type == "tool_use")
    assert tool.name == "Bash" and "hi-from-shell" in next(e for e in events if e.type == "tool_result").content
    assert events[-1].session_id == session and not events[-1].is_error
    assert "CALLBOARD" in json.dumps(FakeGemini.requests[-1]["contents"])  # the session went on
    assert not list(signins.ROOT.glob("gemini-*"))  # the run's home is gone


@needs_cli
async def test_an_urgent_message_ends_a_long_tool_call_and_goes_in_next(fake_gemini, tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    notices.drain("r1")
    monkeypatch.setattr(gemini, "NOTICE_GRACE_SECONDS", 0)
    events = []
    bridge = ToolBridge(spec(fake_gemini).bridge, workspace)
    async for event in GeminiCliProvider().run(spec(fake_gemini, prompt="SLEEP please", workspace={"path": str(workspace)}), bridge):
        events.append(event)
        if event.type == "tool_use":
            notices.push("r1", "Stop now, new ticket", urgent=True)
    await bridge.aclose()
    assert not events[-1].is_error and "Stop now, new ticket" in events[-1].text
    assert gemini.INTERRUPTED in json.dumps(FakeGemini.requests[-1]["contents"])
