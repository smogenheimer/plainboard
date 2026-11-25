"""The Gemini CLI keeps itself current like the Codex CLI (codex_cli.py): new Gemini models often need a newer CLI
than the image has. Now and then the runner asks npm for the newest version, installs it next to the others in its
home (``ROOT/<version>``) and tries it: it must start and list models. Only then do new runs use it (``binary``).
A version that fails its try is not tried again; one that can't be installed changes nothing.

``PLAINBOARD_GEMINI_BIN`` names a CLI of one's own and turns this off, as does ``PLAINBOARD_GEMINI_UPDATE=0``."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from .codex_cli import COMMAND_TIMEOUT, _run, version_of

log = logging.getLogger(__name__)

PACKAGE = "@google/gemini-cli"
IMAGE_BIN = "gemini"
ROOT = Path.home() / ".plainboard" / "gemini-cli"
KEEP = 2
CHECK_SECONDS = 6 * 3600
FIRST_CHECK_SECONDS = 180


def own_bin() -> str | None:
    return os.environ.get("PLAINBOARD_GEMINI_BIN") or None


def enabled() -> bool:
    return own_bin() is None and os.environ.get("PLAINBOARD_GEMINI_UPDATE", "1") != "0"


def _bin(root: Path, version: str) -> Path:
    return root / version / "node_modules" / ".bin" / "gemini"


def _current(root: Path) -> str | None:
    try:
        version = (root / "current").read_text().strip()
    except OSError:
        return None
    return version if version_of(version) and _bin(root, version).exists() else None


def binary(root: Path = ROOT) -> str:
    """The CLI a new run starts with."""
    if mine := own_bin():
        return mine
    version = _current(root)
    return str(_bin(root, version)) if version else IMAGE_BIN


async def latest() -> str:
    return (await _run("npm", "view", PACKAGE, "version", timeout=60)).strip()


async def installed(bin_: str) -> str:
    return await _run(bin_, "--version", timeout=60)


async def install(version: str, root: Path = ROOT) -> Path:
    """``version`` into ``root/<version>``, through a folder of its own so a broken install leaves nothing behind."""
    root.mkdir(parents=True, exist_ok=True)
    if _bin(root, version).exists():
        return _bin(root, version)
    work = Path(tempfile.mkdtemp(prefix=f".{version}-", dir=root))
    try:
        await _run(
            "npm", "install", "--prefix", str(work / "pkg"), "--no-audit", "--no-fund", "--no-save", "--cache", str(work / "cache"), f"{PACKAGE}@{version}",
            timeout=COMMAND_TIMEOUT,
        )  # fmt: skip
        shutil.rmtree(root / version, ignore_errors=True)
        (work / "pkg").rename(root / version)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return _bin(root, version)


async def list_models(bin_: str, timeout: float = 60) -> list[dict]:
    """``gemini models -o json`` in a home of its own (no sign-in needed): the models this CLI knows."""
    with tempfile.TemporaryDirectory(prefix="plainboard-gemini-models-") as home:
        text = await _run(bin_, "models", "-o", "json", timeout=timeout, env={"GEMINI_CLI_HOME": home, "NO_BROWSER": "true"})
    start = text.find("[")
    listed = json.loads(text[start:]) if start >= 0 else []
    return [m for m in listed if isinstance(m, dict) and m.get("id")]


async def try_out(bin_: Path) -> None:
    if not await list_models(str(bin_)):
        raise RuntimeError("it lists no models")


def prune(root: Path = ROOT, keep: int = KEEP) -> None:
    if not root.is_dir():
        return
    current = _current(root)
    versions = sorted((p for p in root.iterdir() if p.is_dir() and version_of(p.name) and p.name != current), key=lambda p: version_of(p.name), reverse=True)
    for path in versions[keep - 1 if current else keep :]:
        shutil.rmtree(path, ignore_errors=True)
    for path in root.glob(".*-*"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)


async def update(root: Path = ROOT, check: Callable[[Path], Awaitable[None]] = try_out) -> str | None:
    """Install and switch to a newer CLI if there is one. Returns the version switched to, None if none."""
    if not enabled():
        return None
    newest = await latest()
    try:
        have = version_of(await installed(binary(root)))
    except (OSError, RuntimeError):
        have = None  # no CLI in the image (a runner of an older image): the newest is installed
    if version_of(newest) is None or (have and version_of(newest) <= have):
        return None
    if (root / "failed").is_file() and (root / "failed").read_text().strip() == newest:
        return None
    bin_ = await install(newest, root)
    try:
        await check(bin_)
    except Exception as exc:
        log.warning("Gemini CLI %s failed its try, staying with the one in use: %s", newest, str(exc)[:300])
        (root / "failed").write_text(newest)
        shutil.rmtree(root / newest, ignore_errors=True)
        return None
    (root / "current").write_text(newest)
    prune(root)
    log.info("Gemini CLI %s is used for new runs from now on", newest)
    return newest


async def keep_current() -> None:
    """Look for a newer CLI shortly after the start and every few hours."""
    await asyncio.sleep(FIRST_CHECK_SECONDS)
    while enabled():
        try:
            await update()
        except Exception as exc:
            log.info("No newer Gemini CLI now: %s", str(exc)[:300])
        await asyncio.sleep(CHECK_SECONDS)
