"""A person's own sign-ins (Claude's browser sign-in, Codex's ChatGPT sign-in): never in the runner's home.

The app keeps them, encrypted like a token, and sends their files with every run, limit check and
completion of that account (``Credentials.login_files``). While those go on, the files live in one private
folder per account (0700, a random name under the temp folder), shared by the account's concurrent runs so
that one's refreshed token is the other's too; the last one to end removes it. What the CLI changed there
waits in ``outbox`` until the app has taken it (``GET /signins``, ``POST /signins/ack``), and until then it
is what the next run of the account starts from: it is newer than what the app sent. A finished sign-in
waits there as well (``base`` ""). Each entry names the version it was derived from, so the app keeps the
newest and never goes back to an older one. What another runner refreshed reaches the account's folder
here too while it is in use (``update``): the CLIs read the file again before they refresh, so no two
runners refresh one token.

The installation's sign-ins from before (#84) are in the runner's home: the app copies them over once
(``POST /providers/{name}/takeover``), while nothing uses them. From then on a mark (``TAKEN``) keeps every
use of the home's copy away for good, so it is never refreshed and cannot outdate the app's: OpenAI and
Anthropic rotate refresh tokens. The home's files themselves stay as they are, a backup.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import tempfile
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from plainboard_protocol import ACCOUNT_KEY_PATTERN, SignInAck, SignInFiles, files_digest

ROOT = Path(tempfile.gettempdir()) / "plainboard-signins"
TAKEN = Path.home() / ".plainboard" / "signins-taken"  # sign-ins of the home the app took over: one mark each
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


class Busy(RuntimeError):
    """The home's sign-in is in use right now: it is taken over once nothing uses it."""


def _mark(provider: str, key: str) -> Path:
    if not re.fullmatch(ACCOUNT_KEY_PATTERN, key) or not re.fullmatch(r"[a-z0-9_]+", provider):
        raise ValueError(f"Invalid sign-in: {provider!r} {key!r}")
    return TAKEN / f"{provider}-{key}"


def taken(provider: str, key: str) -> bool:
    return _mark(provider, key).exists()


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
        self._in_home: dict[tuple[str, str], int] = {}  # uses of a sign-in in the runner's home, until it is taken over

    @contextlib.contextmanager
    def in_home(self, provider: str, key: str, what: str) -> Iterator[None]:
        """A use of the home's sign-in ``key``: refused once the app took it over (its copy is the one in use)."""
        if taken(provider, key):
            raise RuntimeError(f"{what} is kept by PlainBoard now and comes with the run; this run came without it. Start it again.")
        self._in_home[(provider, key)] = self._in_home.get((provider, key), 0) + 1
        try:
            yield
        finally:
            self._in_home[(provider, key)] -= 1

    def take_over(self, provider: str, key: str, home: Path, names: tuple[str, ...]) -> dict[str, str]:
        """The home's sign-in files for the app (none: not signed in there), copied while nothing uses them.
        From now on nothing here uses them again (``in_home``); they stay where they are."""
        mark = _mark(provider, key)
        if self._in_home.get((provider, key)):
            raise Busy("The sign-in is in use; it is taken over once that ends.")
        files = read(home, names)
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.write_text("The app keeps this sign-in now; the files in the home are left as a backup and not used.\n")
        return files

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

    async def update(self, entries: list[SignInFiles]) -> None:
        """What another runner refreshed: into the account's folder here, while it is in use and still holds
        the version the refresh came from (``base``). The next run gets it from the app anyway."""
        async with self._lock:
            for entry in entries:
                live = self._homes.get(entry.account)
                if live is None or not entry.files or files_digest(read(live.path, tuple(entry.files))) != entry.base:
                    continue  # not in use here, or refreshed here as well: the app keeps the newest
                try:
                    write(live.path, entry.files)
                except (OSError, ValueError):
                    continue
                live.files, live.base = dict(entry.files), entry.digest or files_digest(entry.files)

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
