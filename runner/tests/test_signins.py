"""People's own sign-ins (signins.py): a private folder per account while it is in use, never the runner's home."""

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest
from plainboard_protocol import Credentials, SignInAck, files_digest

from plainboard_runner import signins
from plainboard_runner.providers import claude_code
from plainboard_runner.providers.claude_code import ClaudeCodeProvider, auth_env, private_config

FILES = {".credentials.json": '{"claudeAiOauth": {"accessToken": "a1", "refreshToken": "r1"}}'}
NAMES = (".credentials.json",)


@pytest.fixture
def sign_ins(tmp_path, monkeypatch):
    monkeypatch.setattr(signins, "ROOT", tmp_path / "signins")
    return signins.SignIns()


def test_a_private_folder_per_account_shared_by_its_runs_and_gone_after_the_last(sign_ins):
    async def go():
        async with sign_ins.home("claude_code", "acc1", FILES, NAMES) as first:
            assert stat.S_IMODE(first.stat().st_mode) == 0o700 and stat.S_IMODE((first / ".credentials.json").stat().st_mode) == 0o600
            assert not first.is_relative_to(Path.home()) and first.parent == signins.ROOT
            async with sign_ins.home("claude_code", "acc1", {".credentials.json": "older"}, NAMES) as second:
                assert second == first  # one's refreshed token is the other's
            async with sign_ins.home("claude_code", "acc2", {".credentials.json": "b"}, NAMES) as other:
                assert other != first and (other / ".credentials.json").read_text() == "b"
            assert first.exists() and not sign_ins.outbox
            (first / ".credentials.json").write_text("refreshed")
        assert not first.exists()
        return first

    asyncio.run(go())
    (entry,) = sign_ins.waiting()
    assert entry.account == "acc1" and entry.files == {".credentials.json": "refreshed"} and entry.base == files_digest(FILES)


def test_what_waits_for_the_app_is_what_the_next_run_starts_from_until_the_app_has_it(sign_ins):
    async def run(files):
        async with sign_ins.home("claude_code", "acc1", files, NAMES) as home:
            return (home / ".credentials.json").read_text()

    async def go():
        async with sign_ins.home("claude_code", "acc1", FILES, NAMES) as home:
            (home / ".credentials.json").write_text("refreshed")
        assert await run(FILES) == "refreshed"  # the app still sends the older one
        entry = sign_ins.outbox["acc1"]
        sign_ins.ack([SignInAck(account="acc1", digest="another")])
        assert "acc1" in sign_ins.outbox
        sign_ins.ack([SignInAck(account="acc1", digest=entry.digest)])
        assert not sign_ins.outbox
        assert await run({".credentials.json": "refreshed"}) == "refreshed"
        sign_ins.signed_in("claude_code", "acc1", {".credentials.json": "new sign-in"})
        assert await run(FILES) == "new sign-in"  # a finished sign-in replaces what the app has
        assert await run({".credentials.json": "new sign-in"}) == "new sign-in" and not sign_ins.outbox  # the app has it

    asyncio.run(go())


def test_file_names_stay_in_the_folder(sign_ins):
    async def go():
        async with sign_ins.home("claude_code", "acc1", {"../escape": "x"}, NAMES):
            pass

    with pytest.raises(ValueError):
        asyncio.run(go())
    assert not list(signins.ROOT.iterdir())


def test_claude_own_login_runs_in_its_private_folder_and_never_falls_back_to_the_runners_sign_in(sign_ins, tmp_path, monkeypatch):
    shared = tmp_path / "home" / ".claude"
    (shared / "plugins").mkdir(parents=True)
    (shared / ".credentials.json").write_text("the installation's")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    monkeypatch.setattr(claude_code, "SIGN_INS", sign_ins)
    creds = Credentials(claude_auth_mode="own_login", login_account="acc1", login_files=FILES)
    with pytest.raises(RuntimeError):
        auth_env(creds)
    home = tmp_path / "private"
    home.mkdir()
    private_config(home)
    assert (home / "projects").resolve() == shared / "projects" and (home / "plugins").is_symlink()
    assert not (home / ".credentials.json").exists() and json.loads((home / ".claude.json").read_text())

    async def go():
        async with ClaudeCodeProvider()._home(creds) as found:
            env = auth_env(creds, found)
            assert env["CLAUDE_CONFIG_DIR"] == str(found) and "CLAUDE_CODE_OAUTH_TOKEN" not in env
            assert (found / ".credentials.json").read_text() == FILES[".credentials.json"] and (found / "projects").is_symlink()
            return found

    found = asyncio.run(go())
    assert not found.exists() and (shared / ".credentials.json").read_text() == "the installation's"
    assert not [p for p in shared.rglob("*") if p.is_file() and "a1" in p.read_text()]
    assert os.environ.get("CLAUDE_CONFIG_DIR") is None
