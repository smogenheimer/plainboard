"""Shell commands a turn that only reads may run (toolset "read": a person's question, app orchestration/questions.py).

Codex and Claude Code run a question's commands in the runner container without a sandbox of their own (Codex'
bubblewrap cannot start in a container). So each command is checked here before it runs: it passes only if every
part of it is a program known to just read (cat, sed -n, rg, find, git log...) without an option that writes or
starts another program. Pipes and ``&&``/``||``/``;`` are fine; redirections (but to /dev/null), command
substitution, variables set before a command and every other program are refused.
"""

from __future__ import annotations

import re
import shlex

SEPARATORS = {"|", "||", "&&", ";"}
SHELLS = {"bash", "sh", "zsh", "/bin/bash", "/bin/sh", "/usr/bin/bash", "/usr/bin/zsh"}
READERS = {
    "cat", "head", "tail", "nl", "wc", "ls", "pwd", "echo", "printf", "grep", "egrep", "fgrep", "rg", "cut", "tr", "column",
    "diff", "cmp", "stat", "du", "basename", "dirname", "realpath", "readlink", "true", "jq", "sha256sum", "md5sum", "which",
    "cd", "sort", "uniq", "tree", "find", "sed", "git",
}  # fmt: skip
# Options that write a file or start a program, per reader.
REFUSED = {
    "sort": ("-o", "--output", "--compress-program"),
    "rg": ("--pre",),
    "tree": ("-o",),
    "find": ("-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"),
    "sed": ("-i", "--in-place", "-f", "--file"),
}
SED_PRINT = re.compile(r"^((\d+|\$)(,(\d+|\$|\+\d+))?)?p(;((\d+|\$)(,(\d+|\$|\+\d+))?)?p)*;?$")
GIT_READERS = {
    "log", "show", "diff", "status", "blame", "grep", "ls-files", "ls-tree", "rev-parse", "shortlog", "describe", "cat-file",
    "rev-list", "merge-base", "name-rev", "show-ref", "for-each-ref", "whatchanged", "branch", "tag", "remote", "stash",
}  # fmt: skip
GIT_REFUSED = ("--output", "-O", "--open-files-in-pager", "--ext-diff")
# git branch/tag only list: these options, a name only as the pattern of --list.
GIT_LISTING = {
    "branch": {"-a", "-r", "-v", "-vv", "--all", "--remotes", "--verbose", "-l", "--list", "--show-current", "--no-color", "--ignore-case"},
    "tag": {"-l", "--list", "-n", "--no-color", "--ignore-case"},
}
GIT_LIST_VALUES = ("--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--sort", "--format")


def allowed(command: str | list[str]) -> bool:
    """Whether a command (a shell line, or its arguments) only reads."""
    try:
        argv = shlex.split(command) if isinstance(command, str) else list(command)
    except ValueError:
        return False
    if len(argv) >= 3 and argv[0] in SHELLS and argv[1] in ("-c", "-lc", "-cl"):
        script = argv[2]
    elif isinstance(command, str):
        script = command
    else:
        script = shlex.join(argv)
    if "`" in script or "$(" in script or "<(" in script or ">(" in script:
        return False
    lexer = shlex.shlex(script.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return False
    parts: list[list[str]] = [[]]
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in SEPARATORS:
            parts.append([])
        elif token in (">", "&>", ">>") and tokens[i + 1 : i + 2] == ["/dev/null"] or token == ">&" and tokens[i + 1 : i + 2] in (["1"], ["2"]):
            if parts[-1] and parts[-1][-1] in ("1", "2"):  # the descriptor of 2>/dev/null
                parts[-1].pop()
            i += 1
        elif token and set(token) <= set("();<>|&"):
            return False
        else:
            parts[-1].append(token)
        i += 1
    return all(part and _reads(part) for part in parts)


def _reads(argv: list[str]) -> bool:
    name, args = argv[0], argv[1:]
    if name not in READERS:  # also a path (./cat) and a variable set before the command (A=1 cat)
        return False
    if any(a == o or a.startswith(o + "=") or (o.startswith("--") and a.startswith(o)) for a in args for o in REFUSED.get(name, ())):
        return False
    if name == "sed":
        return _sed_prints(args)
    if name == "uniq":
        return len([a for a in args if not a.startswith("-")]) <= 1  # a second name is the file it writes
    if name == "git":
        return _git_reads(args)
    return True


def _sed_prints(args: list[str]) -> bool:
    scripts, rest = [], list(args)
    while rest:
        arg = rest.pop(0)
        if arg in ("-e", "--expression"):
            if not rest:
                return False
            scripts.append(rest.pop(0))
        elif arg.startswith("-"):
            if arg.lstrip("-") not in ("n", "E", "r", "s", "u", "z", "quiet", "silent", "regexp-extended") and not re.fullmatch(r"-[nErsuz]+", arg):
                return False
        elif not scripts:
            scripts.append(arg)
    return bool(scripts) and all(SED_PRINT.match(s.replace(" ", "")) for s in scripts)


def _git_reads(args: list[str]) -> bool:
    rest = list(args)
    while rest and rest[0].startswith("-"):  # options of git itself: only these
        option = rest.pop(0)
        if option == "-C" and rest:
            rest.pop(0)
        elif option not in ("--no-pager", "-P", "--no-optional-locks") and not option.startswith("--git-dir="):
            return False
    if not rest or rest[0] not in GIT_READERS:
        return False
    sub, args = rest[0], rest[1:]
    if any(a == o or a.startswith(o + "=") for a in args for o in GIT_REFUSED):
        return False
    if sub == "remote":
        return all(a in ("-v", "--verbose") for a in args)
    if sub == "stash":
        return bool(args) and args[0] in ("list", "show")
    if sub in ("branch", "tag"):
        listing = any(a in ("-l", "--list") for a in args)
        values = iter(args)
        for a in values:
            if a in GIT_LIST_VALUES:
                next(values, None)
            elif a.startswith("-"):
                if a not in GIT_LISTING[sub] and not any(a.startswith(o + "=") for o in GIT_LIST_VALUES) and not re.fullmatch(r"-n\d*", a):
                    return False
            elif not listing:
                return False
    return True
