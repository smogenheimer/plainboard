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


@pytest.fixture
def taken(tmp_path, monkeypatch):
    monkeypatch.setattr(signins, "TAKEN", tmp_path / "taken")
    return tmp_path


def test_the_home_sign_in_is_copied_for_the_app_left_in_place_and_never_used_again(sign_ins, taken):
    home = taken / "home"
    home.mkdir()
    (home / ".credentials.json").write_text(FILES[".credentials.json"])
    with sign_ins.in_home("claude_code", "default", "This Claude sign-in"):
        with pytest.raises(signins.Busy):  # in use: a refresh now would outdate the copy
            sign_ins.take_over("claude_code", "default", home, NAMES)
    assert not signins.taken("claude_code", "default")
    assert sign_ins.take_over("claude_code", "default", home, NAMES) == FILES
    assert (home / ".credentials.json").read_text() == FILES[".credentials.json"]  # a backup, untouched
    assert signins.taken("claude_code", "default") and not signins.taken("codex", "default")
    with pytest.raises(RuntimeError, match="kept by PlainBoard"):
        with sign_ins.in_home("claude_code", "default", "This Claude sign-in"):
            pass
    assert sign_ins.take_over("claude_code", "default", home, NAMES) == FILES  # again, if the app missed the answer
    assert signins.SignIns().take_over("claude_code", "default", taken / "empty", NAMES) == {}  # not signed in there


def test_a_runner_login_run_after_the_take_over_is_refused_not_run_on_the_home(sign_ins, taken, monkeypatch):
    monkeypatch.setattr(claude_code, "SIGN_INS", sign_ins)
    provider = ClaudeCodeProvider()
    creds = Credentials(claude_auth_mode="runner_login")

    async def go():
        async with provider._home(creds) as home:
            assert home is None  # the home's own, while it is not taken over
        (taken / "home").mkdir()
        monkeypatch.setattr(claude_code, "shared_home", lambda: taken / "home")
        await provider.take_over("anything")
        with pytest.raises(RuntimeError, match="kept by PlainBoard"):
            async with provider._home(creds):
                pass
        assert await provider.limits(creds) == []

    asyncio.run(go())


def test_what_another_runner_refreshed_reaches_a_folder_in_use_unless_it_was_refreshed_here(sign_ins):
    from plainboard_protocol import SignInFiles

    new = {".credentials.json": "from the other runner"}
    push = [SignInFiles(provider="claude_code", account="acc1", files=new, base=files_digest(FILES), digest=files_digest(new))]

    async def go():
        await sign_ins.update(push)  # nothing in use here: nothing to do
        async with sign_ins.home("claude_code", "acc1", FILES, NAMES) as home:
            await sign_ins.update(push)
            assert (home / ".credentials.json").read_text() == "from the other runner"
        assert not sign_ins.outbox  # the app has it already
        async with sign_ins.home("claude_code", "acc1", FILES, NAMES) as home:
            (home / ".credentials.json").write_text("refreshed here")
            await sign_ins.update(push)
            assert (home / ".credentials.json").read_text() == "refreshed here"

    asyncio.run(go())


def test_a_kept_sign_in_is_refreshed_ahead_only_when_it_is_due(monkeypatch):
    import time
    from datetime import datetime, timedelta, timezone

    from plainboard_runner.providers import codex

    now = datetime(2026, 11, 23, tzinfo=timezone.utc)
    auth = lambda days: {"auth.json": json.dumps({"auth_mode": "chatgpt", "tokens": {"refresh_token": "r"}, "last_refresh": (now - timedelta(days=days)).isoformat().replace("+00:00", "Z")})}
    assert not codex.refresh_due(auth(1), now) and codex.refresh_due(auth(codex.REFRESH_AFTER_DAYS + 0.1), now)
    assert not codex.refresh_due({"auth.json": '{"auth_mode": "apikey"}'}, now) and not codex.refresh_due({}, now)
    ends = lambda hours: {".credentials.json": json.dumps({"claudeAiOauth": {"refreshToken": "r", "expiresAt": (time.time() + hours * 3600) * 1000}})}
    assert not claude_code.refresh_due(ends(6)) and claude_code.refresh_due(ends(1)) and not claude_code.refresh_due(FILES)


def test_codex_leaves_a_taken_over_home_alone(sign_ins, taken, monkeypatch):
    from plainboard_runner.providers import codex

    monkeypatch.setattr(codex, "SIGN_INS", sign_ins)
    monkeypatch.setattr(codex, "SUBSCRIPTION_HOME", taken / "codex")
    monkeypatch.setattr(codex, "CATALOG_HOME", taken / "catalog")
    (taken / "codex").mkdir()
    auth = '{"auth_mode": "chatgpt", "tokens": {"refresh_token": "rt"}}'
    (taken / "codex" / "auth.json").write_text(auth)
    started: list[tuple[Path, list]] = []

    class Server:
        def __init__(self, home, **_):
            self.home, self.asked = home, []
            started.append((home, self.asked))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, params=None, timeout=None):
            self.asked.append(method)
            return {"data": []}

    monkeypatch.setattr(codex, "AppServer", Server)
    provider = codex.CodexProvider()

    async def go():
        assert await provider.take_over("default") == {"auth.json": auth}
        with pytest.raises(RuntimeError, match="kept by PlainBoard"):
            async with provider._home(Credentials(codex_auth_mode="chatgpt")):
                pass
        assert await provider.limits(Credentials(codex_auth_mode="chatgpt")) == []
        await provider.logout("default")  # signing the backup out would end the app's copy: the same tokens
        assert (await provider.account("default")).signed_in is False
        await provider.catalog()

    asyncio.run(go())
    assert [home for home, _ in started] == [taken / "catalog"] and (taken / "codex" / "auth.json").read_text() == auth
