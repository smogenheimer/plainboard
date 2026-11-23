"""A person's own sign-ins (Claude's browser sign-in, Codex's ChatGPT sign-in): never in the runner's home.

The app keeps them, encrypted like a token, and sends their files with every run, limit check and
completion of that account (``Credentials.login_files``). While those go on, the files live in one private
folder per account (0700, a random name under the temp folder), shared by the account's concurrent runs so
that one's refreshed token is the other's too; the last one to end removes it. What the CLI changed there
waits in ``outbox`` until the app has taken it (``GET /signins``, ``POST /signins/ack``), and until then it
is what the next run of the account starts from: it is newer than what the app sent. A finished sign-in
waits there as well (``base`` ""). Each entry names the version it was derived from, so the app keeps the
newest and never goes back to an older one.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import tempfile
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path

from plainboard_protocol import ACCOUNT_KEY_PATTERN, SignInAck, SignInFiles, files_digest

ROOT = Path(tempfile.gettempdir()) / "plainboard-signins"
NAME = re.compile(r"^\.?[A-Za-z0-9_][A-Za-z0-9._-]{0,99}$")  # a file of the folder itself, e.g. .credentials.json


def private_dir() -> Path:
    """A new folder only this user can open, outside the home every agent works in."""
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(ROOT, 0o700)
    return Path(tempfile.mkdtemp(dir=ROOT))  # 0700


def remove(path: Path | None) -> None:
    if path is not None:
        shutil.rmtree(path, ignore_errors=True)


def read(home: Path, names: tuple[str, ...]) -> dict[str, str]:
    out = {}
    for name in names:
        with contextlib.suppress(OSError, UnicodeDecodeError):
            out[name] = (home / name).read_text()
    return out


def write(home: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        if not NAME.fullmatch(name):
            raise ValueError(f"Invalid sign-in file name: {name!r}")
        path = home / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(text)


@dataclass
class _Home:
    path: Path
    users: int
    files: dict[str, str]  # as last written or kept
    base: str  # the app's version these files come from


class SignIns:
    def __init__(self) -> None:
        self._homes: dict[str, _Home] = {}
        self._lock = asyncio.Lock()
        self.outbox: dict[str, SignInFiles] = {}

    @contextlib.asynccontextmanager
    async def home(
        self, provider: str, account: str, files: dict[str, str], names: tuple[str, ...], prepare: Callable[[Path], None] | None = None
    ) -> AsyncIterator[Path]:
        """The account's private folder with its sign-in files ``names`` while the block runs."""
        if not re.fullmatch(ACCOUNT_KEY_PATTERN, account):
            raise ValueError(f"Invalid account key: {account!r}")
        async with self._lock:
            live = self._homes.get(account)
            if live is None:
                start, base = files, files_digest(files)
                waiting = self.outbox.get(account)
                if waiting is not None:
                    if waiting.digest != base and (waiting.base == base or not waiting.base):
                        start, base = waiting.files, waiting.base  # newer than what the app has
                    else:
                        del self.outbox[account]  # the app has it, or something newer
                path = private_dir()
                try:
                    write(path, start)
                    if prepare:
                        prepare(path)
                except BaseException:
                    remove(path)
                    raise
                live = self._homes[account] = _Home(path, 0, dict(start), base)
            live.users += 1
        try:
            yield live.path
        finally:
            async with self._lock:
                live.users -= 1
                now = read(live.path, names)
                if now and now != live.files:
                    live.files = now
                    self.outbox[account] = SignInFiles(provider=provider, account=account, files=now, base=live.base, digest=files_digest(now))
                if live.users <= 0:
                    self._homes.pop(account, None)
                    remove(live.path)

    def signed_in(self, provider: str, account: str, files: dict[str, str]) -> None:
        """A sign-in finished: it replaces whatever the app had."""
        self.outbox[account] = SignInFiles(provider=provider, account=account, files=files, base="", digest=files_digest(files))

    def forget(self, account: str) -> None:
        """Signed out: what waits for the app goes."""
        self.outbox.pop(account, None)

    def waiting(self) -> list[SignInFiles]:
        return list(self.outbox.values())

    def ack(self, acks: list[SignInAck]) -> None:
        for ack in acks:
            if (entry := self.outbox.get(ack.account)) is not None and entry.digest == ack.digest:
                del self.outbox[ack.account]


SIGN_INS = SignIns()
