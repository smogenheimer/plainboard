import pytest

from plainboard_runner.providers.claude_code import read_only_hook
from plainboard_runner.read_only import allowed

READS = [
    "/bin/bash -lc 'cat README.md'", "/bin/bash -lc 'git log --oneline -3'", "rg -n foo app", "grep -rn foo app | head", "ls -la",
    "sed -n 1,5p README.md", "sed -n '10,40p' a.py", "nl -ba README.md", "git show HEAD --stat", "cd app && ls", "wc -l README.md",
    "rg foo 2>/dev/null | head -20", "git -C plainboard log -5 --format=%h", "find . -name '*.py' -not -path './node_modules/*'",
    "git branch -a", "git branch --contains abc", "git tag -l 'v*'", "git diff HEAD~3 -- app", "/bin/bash -lc \"grep -n 'a>b' f\"",
    ["rg", "x"], "git status --short", "sort a | uniq -c", "git blame -L 1,20 a.py", "cat a 2>&1 | head",
]  # fmt: skip
WRITES = [
    "cat README.md > /tmp/X", "sed -i s/a/b/ README.md", "find . -name '*.py' -delete", "head -5 README.md; touch Y", r"find . -name x -exec rm {} \;",
    "cat $(touch Z)", 'echo "`touch Z`"', "git checkout main", "git branch foo", "git branch -D foo", "git tag v1", "git -c core.pager=x log",
    "A=1 cat x", "./cat x", "python -c 1", "sort -o out a", "rg --pre=sh x", "git diff --output=x", "sed -n 1w/tmp/x a", "sed 's/a/b/' a",
    "cat a\ntouch b", "cat a & touch b", "uniq a b", "git stash", "git stash pop", "git remote add x y", "tee x", "cat a >> b", "cat < a",
    "git push", "git fetch", "(touch a)", "git tag -a v1 -m x", "git branch -l -dx foo", "/bin/bash -lc 'cat a; rm -rf b'",
]  # fmt: skip


@pytest.mark.parametrize("command", READS)
def test_commands_that_only_read_pass(command):
    assert allowed(command)


@pytest.mark.parametrize("command", WRITES)
def test_commands_that_write_or_run_programs_are_refused(command):
    assert not allowed(command)


async def test_a_questions_bash_runs_only_what_reads():
    assert await read_only_hook({"tool_name": "Bash", "tool_input": {"command": "git log -3"}}, "t1", None) == {}
    refused = await read_only_hook({"tool_name": "Bash", "tool_input": {"command": "git commit -am x"}}, "t2", None)
    assert refused["hookSpecificOutput"]["permissionDecision"] == "deny"
