"""A doc site's API (#131, services/docs_api.py): the documentation of one site, public or private, for scripts and
other systems. Every request carries ``Authorization: Bearer <key>``: a key of that site (made in its settings) or a
person's own token (``pbp_``) of someone with a role in its project. Read only.

- ``GET /api/v1/docs/<slug>``: the site: languages, versions (``default_version``), page count.
- ``GET .../pages?cursor=&limit=&since=``: the pages, ``limit`` at a time (``next_cursor``), with id, parent_id,
  position, depth, path, title, updated_at: enough to rebuild the tree.
- ``GET .../pages/<id>``, ``GET .../page?path=``: one page as Markdown with its files (download addresses) and links.
- ``GET .../footer``: the site's own pages (imprint …) as Markdown. ``GET .../files/<id>``: a file.
- ``GET .../export?format=jsonl|zip&since=``: a whole version in a language, streamed.
Each takes ``version`` (a version's slug, only for a site with versions; none: its default one) and ``lang`` (one of
the site's languages; a page not translated yet comes in the original, ``fallback`` true). ``since`` (ISO time): only
pages changed or translated since then.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from ..services import board as board_service
from ..services import docs_api
from ..services.errors import NotFound
from .deps import Session
from .docs_public import _origin

router = APIRouter(prefix="/api/v1/docs", tags=["docs-api"])  # services/access.py: open, the key is checked here

Version = Query(default=None, max_length=40, description="A version's slug; none: the site's default version")
Lang = Query(default=None, max_length=20, description="One of the site's languages; none: its own")
Since = Query(default=None, description="Only pages changed since then (ISO time)")
NO_STORE = {"Cache-Control": "private, no-store"}


def _api(request: Request, slug: str) -> str:
    return f"{_origin(request)}/api/v1/docs/{slug}"


def _naive(at: datetime | None) -> datetime | None:
    return at.astimezone(UTC).replace(tzinfo=None) if at is not None and at.tzinfo else at


def _json(data: object) -> JSONResponse:
    return JSONResponse(jsonable_encoder(data, custom_encoder={datetime: lambda at: at.isoformat() + "Z"}), headers=NO_STORE)


async def _scope(session, request: Request, slug: str, version: str | None, lang: str | None):  # noqa: ANN001, ANN202
    reader = await docs_api.reader(session, slug, request.headers.get("authorization"))
    return reader, await docs_api.scope(session, reader.site, version, lang, _api(request, slug))


@router.get("/{slug}")
async def site(slug: str, request: Request, session: Session) -> JSONResponse:
    """{name, slug, description, published, public, language, languages, versioned, default_version, versions, pages, updated_at, api}"""
    reader = await docs_api.reader(session, slug, request.headers.get("authorization"))
    return _json(await docs_api.info(session, reader.site, _api(request, slug)))


@router.get("/{slug}/pages")
async def pages(
    slug: str, request: Request, session: Session, version: str | None = Version, lang: str | None = Lang, since: datetime | None = Since,
    cursor: int = Query(default=0, ge=0), limit: int = Query(default=500, ge=1, le=docs_api.TREE_LIMIT),
) -> JSONResponse:  # fmt: skip
    """{items: [{id, parent_id, position, depth, path, title, lang, fallback, version, updated_at}], next_cursor}"""
    reader, sc = await _scope(session, request, slug, version, lang)
    return _json(await docs_api.tree(session, reader.site, sc, cursor, limit, _naive(since)))


@router.get("/{slug}/pages/{page_id}")
async def page(slug: str, page_id: int, request: Request, session: Session, version: str | None = Version, lang: str | None = Lang) -> JSONResponse:
    """{id, parent_id, position, depth, path, title, lang, fallback, version, updated_at, markdown, files: [{id, name,
    content_type, size, url}], links: {page id: path}}"""
    reader, sc = await _scope(session, request, slug, version, lang)
    return _json(await docs_api.page(session, reader.site, sc, page_id, None))


@router.get("/{slug}/page")
async def page_by_path(
    slug: str, request: Request, session: Session, path: str = Query(default="", max_length=2000), version: str | None = Version, lang: str | None = Lang
) -> JSONResponse:  # fmt: skip
    """A page by its path, as ``/pages/<id>``."""
    reader, sc = await _scope(session, request, slug, version, lang)
    return _json(await docs_api.page(session, reader.site, sc, None, path))


@router.get("/{slug}/footer")
async def footer(slug: str, request: Request, session: Session, version: str | None = Version, lang: str | None = Lang) -> JSONResponse:
    """[{slug, path, title, lang, fallback, markdown, updated_at, files}]"""
    reader, sc = await _scope(session, request, slug, version, lang)
    return _json(await docs_api.footer(session, reader.site, sc))


@router.get("/{slug}/files/{attachment_id}")
async def file(slug: str, attachment_id: int, request: Request, session: Session) -> FileResponse:
    reader = await docs_api.reader(session, slug, request.headers.get("authorization"))
    attachment = await docs_api.file(session, reader.site, attachment_id)
    path = board_service.storage_path(attachment)
    if not path.exists():
        raise NotFound("Not found")
    headers = {**NO_STORE, "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox"}
    return FileResponse(path, media_type=attachment.content_type, filename=attachment.filename, content_disposition_type="attachment", headers=headers)


@router.get("/{slug}/export")
async def export(
    slug: str, request: Request, session: Session, version: str | None = Version, lang: str | None = Lang, since: datetime | None = Since,
    format: Literal["jsonl", "zip"] = "jsonl",
) -> StreamingResponse:  # fmt: skip
    """A version in a language, streamed: JSON lines (site, page …, footer …, file …, end) or a zip (site.json,
    pages/<path>.md, footer/<slug>.md, files/<id>-<name>, index.json)."""
    reader, sc = await _scope(session, request, slug, version, lang)
    site_id, who = reader.site.id, reader.who
    docs_api.start_export(who)
    await session.close()  # an export takes a while: it reads batch by batch with sessions of its own
    make = docs_api.export_zip if format == "zip" else docs_api.export_jsonl

    async def body():  # noqa: ANN202
        try:
            async for chunk in make(site_id, sc, _naive(since)):
                yield chunk
        finally:
            docs_api.end_export(who)

    name = f"{slug}{'-' + sc.version if sc.version else ''}-{sc.lang}.{format}"
    media = "application/zip" if format == "zip" else "application/x-ndjson"
    headers = {**NO_STORE, "Content-Disposition": f'attachment; filename="{name}"', "X-Accel-Buffering": "no"}
    return StreamingResponse(body(), media_type=media, headers=headers)
