"""Reading a project's code for a doc site's assistant (#119, app orchestration/docs_assistant.py): the visitor's
model has no file tool of its own (toolset "none"), only these two.

They never leave the run's workspace (the read checkout; symlinks are followed and must stay inside), never open
what looks like a secret by its name (``.env``, keys, credentials, ``.git``) and hide what looks like one in what
they return (``plainboard_protocol.leaks``). The work runs in a thread: the runner's loop is never blocked.
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
from pathlib import Path
from typing import Any

from plainboard_protocol import ToolCallResult
from plainboard_protocol.leaks import scrub

from .tool_bridge import ToolBridge, local_tool

SKIP_DIRS = {
    ".git", ".hg", ".svn", ".ssh", ".aws", ".gnupg", ".azure", ".kube", ".docker", ".claude", ".codex", ".config",
    "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", ".next", ".terraform",
}  # fmt: skip
SECRET_NAME = re.compile(
    r"(?i)(^\.env|\.env$|\.pem$|\.key$|\.p12$|\.pfx$|\.jks$|\.keystore$|\.kdbx$|\.tfstate|\.tfvars$|^id_(rsa|dsa|ecdsa|ed25519)|"
    r"credential|secret|passw|token|\.netrc$|\.npmrc$|\.pypirc$|\.htpasswd$|auth\.json$|\.git-credentials$|\.sqlite3?$|\.db$)"
)
MAX_FILE_BYTES = 1_000_000  # bigger files are not code worth reading
READ_LINES = 400
READ_CHARS = 40_000
SEARCH_FILES = 5000
SEARCH_HITS = 60


def _allowed(root: Path, rel: str) -> Path | None:
    """The file under the workspace, or None if it is outside, hidden by its name or in a skipped folder."""
    try:
        path = (root / rel.lstrip("/")).resolve()
        path.relative_to(root)
    except (ValueError, OSError):
        return None
    parts = path.relative_to(root).parts
    if any(p in SKIP_DIRS for p in parts[:-1]) or any(SECRET_NAME.search(p) for p in parts):
        return None
    return path


def _read(root: Path, rel: str, offset: int, limit: int) -> ToolCallResult:
    path = _allowed(root, rel)
    if path is None or not path.is_file():
        return ToolCallResult(text=f"Not readable: {rel}", is_error=True)
    if path.stat().st_size > MAX_FILE_BYTES:
        return ToolCallResult(text=f"Too big to read: {rel}", is_error=True)
    data = path.read_bytes()
    if b"\0" in data[:4096]:
        return ToolCallResult(text=f"Not a text file: {rel}", is_error=True)
    lines = data.decode("utf-8", "replace").splitlines()
    start = max(0, offset - 1)
    chunk = lines[start : start + min(limit, READ_LINES)]
    text = "\n".join(f"{start + i + 1}: {line}" for i, line in enumerate(chunk))[:READ_CHARS]
    more = f"\n(lines {start + 1}-{start + len(chunk)} of {len(lines)})" if len(lines) > len(chunk) else ""
    return ToolCallResult(text=scrub(text) + more)


def _files(root: Path, glob: str):  # noqa: ANN202
    seen = 0
    for folder, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not SECRET_NAME.search(d))
        for name in sorted(names):
            if SECRET_NAME.search(name):
                continue
            path = Path(folder) / name
            rel = path.relative_to(root).as_posix()
            if glob and not (fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(name, glob)):
                continue
            if path.is_symlink():
                continue
            seen += 1
            if seen > SEARCH_FILES:
                return
            yield path, rel


def _search(root: Path, pattern: str, glob: str) -> ToolCallResult:
    try:
        found = re.compile(pattern, re.IGNORECASE)
    except re.error:
        found = re.compile(re.escape(pattern), re.IGNORECASE)
    hits: list[str] = []
    for path, rel in _files(root, glob):
        if not pattern:
            hits.append(rel)
        else:
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            if b"\0" in data[:4096]:
                continue
            for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
                if found.search(line):
                    hits.append(f"{rel}:{n}: {line.strip()[:200]}")
                    if len(hits) >= SEARCH_HITS:
                        break
        if len(hits) >= SEARCH_HITS:
            hits.append("(more: narrow the pattern or the glob)")
            break
    return ToolCallResult(text=scrub("\n".join(hits)) if hits else "Nothing found.")


@local_tool("read_code")
async def _read_code(bridge: ToolBridge, args: dict[str, Any]) -> ToolCallResult:
    root = bridge.workspace.resolve()
    return await asyncio.to_thread(_read, root, str(args.get("path", "")), int(args.get("offset") or 1), int(args.get("limit") or READ_LINES))


@local_tool("search_code")
async def _search_code(bridge: ToolBridge, args: dict[str, Any]) -> ToolCallResult:
    root = bridge.workspace.resolve()
    return await asyncio.to_thread(_search, root, str(args.get("pattern", ""))[:200], str(args.get("glob", ""))[:200])
