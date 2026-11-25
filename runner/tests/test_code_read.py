"""The code tools of a doc site's assistant (#119): inside the workspace only, never secret files, secrets hidden."""

import asyncio

from plainboard_protocol import ToolBridgeSpec

from plainboard_runner.tool_bridge import LOCAL_TOOLS, ToolBridge


def call(bridge: ToolBridge, name: str, **args):
    return asyncio.run(LOCAL_TOOLS[name](bridge, args))


def test_it_reads_code_in_the_workspace_and_nothing_secret(tmp_path):
    root = tmp_path / "ws"
    (root / "repo" / "src").mkdir(parents=True)
    (root / "repo" / "src" / "app.py").write_text('def pay():\n    API_KEY = "abcd1234efgh5678"\n    return retry()\n')
    (root / "repo" / ".env").write_text("DB_PASSWORD=hunter2\n")
    (root / "repo" / ".git").mkdir()
    (root / "repo" / ".git" / "config").write_text("url = https://x:ghp_secret@github.com/a/b\n")
    (tmp_path / "outside.txt").write_text("retry outside")
    (root / "repo" / "link.py").symlink_to(tmp_path / "outside.txt")
    bridge = ToolBridge(ToolBridgeSpec(base_url="http://app", run_id="1", run_token="t"), root)

    read = call(bridge, "read_code", path="repo/src/app.py")
    assert not read.is_error and "def pay" in read.text and "abcd1234efgh5678" not in read.text and "[hidden]" in read.text
    for path in ("repo/.env", "repo/.git/config", "../outside.txt", "/etc/passwd", "repo/link.py"):
        assert call(bridge, "read_code", path=path).is_error, path

    found = call(bridge, "search_code", pattern="retry|hunter2|ghp_")
    assert "repo/src/app.py:3" in found.text and "hunter2" not in found.text and "ghp_" not in found.text and "outside" not in found.text
    assert call(bridge, "search_code", pattern="", glob="*.py").text == "repo/src/app.py"
