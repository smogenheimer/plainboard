"""Small previews of attached pictures (``/api/attachments/{id}?w=``): a ticket with dozens of phone screenshots
shows them as thumbnails without loading megabytes. Made once per width, kept next to the uploads in ``.thumbs``."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ..config import get_config

log = logging.getLogger(__name__)

WIDTHS = (200, 400, 800, 1200)  # a requested width goes up to the next of these: few files per picture
_KINDS = {"image/png", "image/jpeg", "image/webp"}  # a GIF keeps its animation: served as it is
_MAX_PIXELS = 60_000_000


def width(requested: int) -> int:
    return next((w for w in WIDTHS if w >= requested), WIDTHS[-1])


def _folder() -> Path:
    return get_config().uploads_dir / ".thumbs"


def _path(storage_name: str, w: int) -> Path:
    return _folder() / f"{storage_name}-{w}.webp"


def preview(source: Path, storage_name: str, kind: str | None, requested: int) -> Path | None:
    """The preview at about this width; None: show the original (no picture, already small, or not readable).
    Blocking (decodes the picture): run it in a thread."""
    if kind not in _KINDS:
        return None
    w = width(requested)
    target = _path(storage_name, w)
    if target.exists():
        return target
    try:
        from PIL import Image, ImageOps

        with Image.open(source) as img:
            if img.width * img.height > _MAX_PIXELS:
                return None
            img.draft("RGB", (w, w * 4))  # a JPEG decodes at a smaller scale right away
            img = ImageOps.exif_transpose(img)
            if img.width <= w * 1.25 and source.stat().st_size < 200_000:
                return None  # hardly smaller: the original does
            if img.width > w:
                img = img.resize((w, max(1, round(img.height * w / img.width))), Image.Resampling.LANCZOS)
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA" if "A" in img.getbands() or "transparency" in img.info else "RGB")
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_suffix(f".{os.getpid()}.part")
            img.save(part, "WEBP", quality=80, method=4)
            part.replace(target)
        return target
    except Exception as exc:  # a broken or unusual picture: the original is shown as before
        log.info("No preview of %s: %s", storage_name, exc)
        return None


def discard(storage_name: str) -> None:
    """The previews of a file that is deleted."""
    for w in WIDTHS:
        _path(storage_name, w).unlink(missing_ok=True)
