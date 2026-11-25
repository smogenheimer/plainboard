"""An external runner brings itself to the app's version (orchestration/runner_updates.py does it, the
runner's selfupdate.py takes it): the app sends the runner's and the protocol's code it was built with,
with a sha256, through the runner's own connection; nothing is fetched from anywhere else.

Only the code changes. A version that needs another image (its Dockerfile or launcher changed) or
other Python packages (a dependency in runner/ or protocol/pyproject.toml whose range the installed
package does not meet, e.g. a newer claude-agent-sdk with its CLI) is refused by the runner: then the
managers of its projects hear once that ``docker compose pull && docker compose up -d`` is needed, with the
image tag that matches the app (runners.manual: latest for a release, edge for :edge or a build from source). So a
change that needs a newer package must raise its lower bound in the pyproject."""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
from dataclasses import dataclass
from pathlib import Path

from . import runners as runner_service

# Running turns, jobs, servers and check runs are waited for this long at most, then the runner restarts
# anyway (setting runner_update_drain_seconds): cut turns go on in the same session, cut check runs again.
DRAIN_SECONDS = 5 * 60
RETRY_SECONDS = 6 * 3600  # after a failed or refused update: no new try for the same version before this

# What to do with a runner now (``step``).
IDLE = "idle"  # nothing: up to date, version not known, or a try failed not long ago
STAGE = "stage"  # send it the code; it checks and unpacks it, running work goes on
WAIT = "wait"  # staged: nothing new starts there, what runs ends first
RESTART = "restart"  # idle (or waited long enough): it restarts into the new code
FAILED = "failed"  # it put its old version back: the managers hear it once


def step(
    own: str, version: str, failed: bool, blocked: bool, draining_since: float | None, turns: int, jobs: int, now: float, limit: float = DRAIN_SECONDS
) -> str:
    """``own``/``version``: the app's and the runner's commit; ``failed``: the runner put its old version back
    after trying ``own`` a short while ago; ``blocked``: ``own`` was refused or could not be sent a short while
    ago; ``draining_since``: staged then (monotonic, like ``now``); ``turns``/``jobs``: what still runs of its
    projects (jobs: background jobs, servers and check runs), waited for ``limit`` seconds at most."""
    if not own or not version or runner_service.short(version) == runner_service.short(own):
        return IDLE
    if blocked:
        return IDLE
    if failed:
        return FAILED
    if draining_since is None:
        return STAGE
    if (turns or jobs) and now - draining_since < limit:
        return WAIT
    return RESTART


@dataclass(frozen=True)
class Bundle:
    data: bytes
    sha256: str
    commit: str


def source_root() -> Path | None:
    """Where the app finds the runner's code: /src in the app's image, the repository in a checkout."""
    candidates = [Path(p) for p in (os.environ.get("PLAINBOARD_RUNNER_SOURCE"), "/src") if p] + [Path(__file__).resolve().parents[3]]
    for root in candidates:
        if (root / "runner" / "plainboard_runner").is_dir() and (root / "protocol" / "plainboard_protocol").is_dir() and (root / "runner" / "launcher.py").is_file():
            return root
    return None


_bundle: Bundle | None = None


def bundle(commit: str, root: Path | None = None) -> Bundle | None:
    """The update for runners: blocking (a thread), built once. None without the runner's code."""
    global _bundle
    if _bundle is not None and _bundle.commit == commit and root is None:
        return _bundle
    root = root or source_root()
    if root is None or not commit:
        return None
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:

        def add(name: str, content: bytes) -> None:
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(content), 0o644
            tar.addfile(info, io.BytesIO(content))

        add("COMMIT", commit.encode() + b"\n")
        for package, folder in (("plainboard_runner", root / "runner"), ("plainboard_protocol", root / "protocol")):
            for path in sorted((folder / package).rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                    add(path.relative_to(folder).as_posix(), path.read_bytes())
        add("meta/runner.toml", (root / "runner" / "pyproject.toml").read_bytes())
        add("meta/protocol.toml", (root / "protocol" / "pyproject.toml").read_bytes())
        add("meta/Dockerfile", (root / "runner" / "Dockerfile").read_bytes())
        add("meta/launcher.py", (root / "runner" / "launcher.py").read_bytes())
    data = buf.getvalue()
    made = Bundle(data=data, sha256=hashlib.sha256(data).hexdigest(), commit=commit)
    if root == source_root():
        _bundle = made
    return made


def manual_note(name: str, version: str, own: str, reason: str) -> str:
    return (
        f'The runner "{name}" runs version {runner_service.short(version)}, PlainBoard {runner_service.short(own)}, and it cannot update itself to it: {reason}. '
        f"Tell the user once, on the runner's machine: {runner_service.manual()}. Until then it works on with its version."
    )


def failed_note(name: str, own: str, reason: str) -> str:
    return (
        f'The runner "{name}" could not update itself to PlainBoard\'s version {runner_service.short(own)} ({reason}) and runs on with its old version; '
        f"it tries again in {RETRY_SECONDS // 3600} hours. Tell the user once: if it keeps failing, on the runner's machine: {runner_service.manual()}."
    )


def cut_note(name: str, jobs: int, limit: float) -> str:
    return (
        f'The runner "{name}" restarts now to update itself to PlainBoard\'s version. {jobs} job(s), server(s) or check run(s) of this project still ran '
        f"after {max(1, round(limit / 60))} minute(s) and end with it (previews too; a train's check run runs again by itself): start again what is still needed "
        "once it is back (a minute or two). Cut agent turns go on by themselves."
    )
