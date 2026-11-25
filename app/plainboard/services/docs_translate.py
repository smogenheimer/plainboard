"""Doc pages in further languages (#118).

A site is written in its ``language`` (English by default) and translated into its ``languages``. The translator is
chosen per site: an LLM (one completion without tools on the project's subscriptions, the model chosen per site, by
default the provider's small one), DeepL or Google Translate (an API key per site, stored encrypted, never shown).
A translation remembers the hash of the page it was made from (``DocPage.source_hash``): when the page changes, it is
outdated, still shown until the new one is there, and made again by the docs watcher in bounded batches
(``BATCH`` per site and look, the oldest problems first; a failure is tried again after ``RETRY``). A translation edited
by hand stays until its page changes again.

Markdown keeps its shape: code blocks are never sent, and for the APIs every piece of syntax (line markers, inline
code, link targets, table bars, line breaks) travels as a tag the service leaves alone. An LLM's answer must have the
Markdown skeleton of what it was given (``skeleton``: blocks, heading levels, code blocks, links, images); it is asked
once more, then the translation fails (#155).
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import httpx
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import DocPage, DocSite, DocTranslation, utcnow
from ..schemas import DocTranslationItem, DocTranslationOut
from ..security import decrypt, encrypt
from . import docs as docs_service
from .errors import NotFound, ServiceError

log = logging.getLogger(__name__)

BATCH = 20  # translations per site and look of the watcher
RETRY = timedelta(hours=1)
LLM_CHUNK = 12000  # characters of Markdown per completion
API_TEXTS = 50  # texts per request (DeepL takes 50, Google 128)
API_CHARS = 60000
TIMEOUT = 60.0
DEEPL_FREE = "https://api-free.deepl.com/v2/translate"
DEEPL_PRO = "https://api.deepl.com/v2/translate"
GOOGLE = "https://translation.googleapis.com/language/translate/v2"

Llm = Callable[[str, str], Awaitable[str]]  # (system prompt, prompt) -> answer


# --- state ----------------------------------------------------------------------------------------------------


def state(page: DocPage, t: DocTranslation | None) -> str:
    if t is None or (not t.translated_at and not t.body):
        return "failed" if t is not None and t.error else "missing"
    if t.source_hash != page.source_hash:
        return "failed" if t.error else "outdated"
    return "current"


async def of_page(session: AsyncSession, site: DocSite, page: DocPage) -> list[DocTranslationItem]:
    found = {t.lang: t for t in await session.scalars(select(DocTranslation).where(DocTranslation.page_id == page.id))}
    out = []
    for lang in site.languages or []:
        t = found.get(lang)
        out.append(DocTranslationItem(lang=lang, title=t.title if t else "", state=state(page, t), translated_by=t.translated_by if t else "", translated_at=t.translated_at if t else None, error=t.error if t else ""))  # type: ignore[arg-type]
    return out


async def get(session: AsyncSession, site: DocSite, page: DocPage, lang: str) -> DocTranslationOut:
    if lang not in (site.languages or []):
        raise NotFound(f"The site has no language {lang}")
    t = await session.scalar(select(DocTranslation).where(DocTranslation.page_id == page.id, DocTranslation.lang == lang))
    if t is None:
        raise NotFound(f"No translation into {lang} yet")
    return DocTranslationOut(lang=lang, title=t.title, body=t.body, state=state(page, t), translated_by=t.translated_by, translated_at=t.translated_at, error=t.error)  # type: ignore[arg-type]


async def save(session: AsyncSession, site: DocSite, page: DocPage, lang: str, title: str, body: str, by: str, *, error: str = "") -> DocTranslation:
    if lang not in (site.languages or []):
        raise NotFound(f"The site has no language {lang}")
    t = await session.scalar(select(DocTranslation).where(DocTranslation.page_id == page.id, DocTranslation.lang == lang))
    if t is None:
        t = DocTranslation(project_id=page.project_id, site_id=page.site_id, page_id=page.id, lang=lang)
        session.add(t)
    now = utcnow()
    t.attempted_at = now
    if error:
        t.error = error[:500]
    else:
        t.title, t.body, t.source_hash, t.translated_by, t.translated_at, t.error = title[:200], body, page.source_hash, by[:100], now, ""
        await docs_service.touch(session, page.site_id)
    await session.commit()
    return t


async def set_keys(session: AsyncSession, site: DocSite, deepl: str | None, google: str | None) -> None:
    if deepl is not None:
        site.deepl_key_enc = encrypt(deepl.strip()) if deepl.strip() else None
    if google is not None:
        site.google_key_enc = encrypt(google.strip()) if google.strip() else None
    await session.commit()


async def pending(session: AsyncSession, site: DocSite, now: datetime, limit: int = BATCH) -> list[tuple[DocPage, str]]:
    """Pages whose translation into one of the site's languages is missing or outdated (top pages first), without
    those that failed lately."""
    out: list[tuple[DocPage, str]] = []
    for lang in site.languages or []:
        if len(out) >= limit:
            break
        t = DocTranslation
        q = (
            select(DocPage)
            .outerjoin(t, and_(t.page_id == DocPage.id, t.lang == lang))
            .where(DocPage.site_id == site.id, or_(t.id.is_(None), t.source_hash != DocPage.source_hash))
            .where(or_(t.id.is_(None), t.error == "", t.attempted_at.is_(None), t.attempted_at < now - RETRY))
            .order_by(DocPage.depth, DocPage.position, DocPage.id)
            .limit(limit - len(out))
        )
        out += [(p, lang) for p in await session.scalars(q)]
    return out


async def recheck(session: AsyncSession, site: DocSite) -> int:
    """Current translations by an LLM whose Markdown skeleton differs from their page's (made before #155) are made
    again: outdated and not shown, the page in its own language meanwhile. Nothing is deleted. Returns how many."""
    rows = await session.execute(
        select(DocTranslation, DocPage.body)
        .join(DocPage, DocPage.id == DocTranslation.page_id)
        .where(DocTranslation.site_id == site.id, DocTranslation.translated_by.like("llm%"), DocTranslation.source_hash == DocPage.source_hash)
    )
    found = 0
    for t, body in rows:
        if differs(body, t.body) is not None:
            t.source_hash, t.translated_at, found = "", None, found + 1
    if found:
        log.info("%d translations of doc site %s changed the Markdown structure: made again", found, site.id)
        await session.commit()
    return found


# --- Markdown in pieces ---------------------------------------------------------------------------------------

_FENCE = re.compile(r"^(```|~~~)")
_PROTECT = re.compile(r"(`[^`\n]+`|\]\([^)\s]*(?:\s+\"[^\"]*\")?\)|<https?://[^>\s]+>|https?://\S+|doc-(?:file|page):\d+|^[ \t]*(?:[-*+]|\d+[.)]|#{1,6}|>)[ \t]+|\||\n)", re.M)


def blocks(markdown: str) -> list[tuple[bool, str]]:
    """(translate it?, text): code blocks stay as they are, the rest goes by paragraphs."""
    out: list[tuple[bool, str]] = []
    fence, buffer = None, []
    for line in (markdown or "").split("\n"):
        if fence:
            buffer.append(line)
            if line.strip().startswith(fence):
                out.append((False, "\n".join(buffer)))
                fence, buffer = None, []
            continue
        m = _FENCE.match(line.strip())
        if m:
            if buffer:
                out.append((True, "\n".join(buffer)))
            fence, buffer = m.group(1), [line]
            continue
        if not line.strip():
            if buffer:
                out.append((True, "\n".join(buffer)))
                buffer = []
            out.append((False, ""))
            continue
        buffer.append(line)
    if buffer:
        out.append((not fence, "\n".join(buffer)))
    return out


def join(pieces: list[tuple[bool, str]]) -> str:
    return "\n".join(text for _, text in pieces)


def protect(text: str, tag: Callable[[int], str]) -> tuple[str, list[str]]:
    """The text escaped for XML/HTML, each piece of syntax as a tag the service leaves alone."""
    kept: list[str] = []
    out, last = [], 0
    for m in _PROTECT.finditer(text):
        out.append(html.escape(text[last : m.start()], quote=False))
        kept.append(m.group(0))
        out.append(tag(len(kept) - 1))
        last = m.end()
    out.append(html.escape(text[last:], quote=False))
    return "".join(out), kept


def restore(text: str, kept: list[str], pattern: re.Pattern) -> str:
    marked = pattern.sub(lambda m: f"\x00{m.group(1)}\x00", text)
    plain = html.unescape(marked)
    return re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))] if int(m.group(1)) < len(kept) else "", plain)


_DEEPL_TAG = re.compile(r"<x>\s*(\d+)\s*</x>")
_GOOGLE_TAG = re.compile(r"<span translate=\"no\">\s*(\d+)\s*</span>")


async def _deepl(client: httpx.AsyncClient, key: str, texts: list[str], source: str, target: str) -> list[str]:
    url = DEEPL_FREE if key.endswith(":fx") else DEEPL_PRO
    body = {"text": texts, "source_lang": source.split("-")[0].upper(), "target_lang": target.upper(), "tag_handling": "xml", "ignore_tags": ["x"], "split_sentences": "nonewlines"}
    resp = await client.post(url, json=body, headers={"Authorization": f"DeepL-Auth-Key {key}"})
    if resp.status_code >= 400:
        raise ServiceError(f"DeepL refused ({resp.status_code}): {resp.text[:200]}")
    return [t["text"] for t in resp.json()["translations"]]


async def _google(client: httpx.AsyncClient, key: str, texts: list[str], source: str, target: str) -> list[str]:
    resp = await client.post(GOOGLE, params={"key": key}, json={"q": texts, "source": source, "target": target, "format": "html"})
    if resp.status_code >= 400:
        raise ServiceError(f"Google Translate refused ({resp.status_code}): {resp.text[:200]}")
    return [t["translatedText"] for t in resp.json()["data"]["translations"]]


async def by_api(site: DocSite, title: str, body: str, lang: str, client: httpx.AsyncClient | None = None) -> tuple[str, str]:
    """Title and Markdown through DeepL or Google, in requests of at most ``API_TEXTS`` texts and ``API_CHARS``."""
    deepl = site.translator == "deepl"
    key = decrypt(site.deepl_key_enc if deepl else site.google_key_enc)
    if not key:
        raise ServiceError(f"No {'DeepL' if deepl else 'Google Translate'} API key for this site.")
    tag = (lambda i: f"<x>{i}</x>") if deepl else (lambda i: f'<span translate="no">{i}</span>')
    pattern = _DEEPL_TAG if deepl else _GOOGLE_TAG
    pieces = [(True, title)] + blocks(body)
    todo = [(i, *protect(text, tag)) for i, (go, text) in enumerate(pieces) if go and text.strip()]
    out = [text for _, text in pieces]
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT)
    call = _deepl if deepl else _google
    try:
        start = 0
        while start < len(todo):
            batch, size = [], 0
            while start + len(batch) < len(todo) and len(batch) < API_TEXTS and (not batch or size + len(todo[start + len(batch)][1]) <= API_CHARS):
                size += len(todo[start + len(batch)][1])
                batch.append(todo[start + len(batch)])
            translated = await call(client, key, [text for _, text, _ in batch], site.language, lang)
            for (i, _, kept), text in zip(batch, translated, strict=True):
                out[i] = restore(text, kept, pattern)
            start += len(batch)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise ServiceError(f"Translation failed: {type(exc).__name__}: {exc}") from exc
    finally:
        if own:
            await client.aclose()
    return out[0].strip(), join([(go, out[i + 1]) for i, (go, _) in enumerate(pieces[1:])])


# --- the skeleton a translation keeps ---------------------------------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})[ \t]+\S")
_LIST = re.compile(r"^(?:[-*+]|\d+[.)])[ \t]+")
_RULE = re.compile(r"^(?:-[ \t]*){3,}$|^(?:\*[ \t]*){3,}$|^(?:_[ \t]*){3,}$")
_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*([^)\s]*)")
_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(\s*[^)\s]*")


def skeleton(markdown: str) -> dict:
    """What a translation keeps of a page: the order of its blocks (each heading with its level; runs of paragraph
    lines, list items, table rows, quotes or HTML as one block each), its code blocks, how many links and which
    images. Indented lines belong to the block above them."""
    kinds: list[str] = []
    code = links = 0
    images: list[str] = []
    for go, text in blocks(markdown):
        if not go:
            if text:
                kinds.append("code")
                code += 1
            continue
        for line in text.split("\n"):
            links += len(_LINK.findall(line))
            images += _IMAGE.findall(line)
            if not line.strip() or (line[:1] in " \t" and kinds):
                continue
            line = line.strip()
            if m := _HEADING.match(line):
                kind = f"h{len(m.group(1))}"
            elif _RULE.match(line):
                kind = "rule"
            elif _LIST.match(line):
                kind = "list"
            elif line.startswith("|"):
                kind = "table"
            elif line.startswith(">"):
                kind = "quote"
            elif line.startswith("<"):
                kind = "html"
            else:
                kind = "text"
            if not (kinds and kinds[-1] == kind and kind in ("text", "list", "table", "quote", "html")):
                kinds.append(kind)
    return {"blocks": kinds, "code blocks": code, "links": links, "images": sorted(images)}


def differs(original: str, translation: str) -> str | None:
    """None when the translation has the original's skeleton, else what is different, in short."""
    a, b = skeleton(original), skeleton(translation)
    for key in a:
        if a[key] != b[key]:
            if key == "blocks":
                at = next((i for i, (x, y) in enumerate(zip(a[key], b[key])) if x != y), min(len(a[key]), len(b[key])))
                return f"block {at + 1} is {b[key][at] if at < len(b[key]) else 'missing'} instead of {a[key][at] if at < len(a[key]) else 'nothing'}"
            return f"{key}: {b[key]} instead of {a[key]}"
    return None


LLM_SYSTEM = (
    "You translate software documentation. Translate the Markdown you are given from {source} into {target}. Translate "
    "the text only and keep the Markdown structure exactly, element by element: every heading stays a heading of the same "
    "level (# stays #, ## stays ##), every paragraph stays a paragraph (never turn it into a heading), lists, tables and "
    "block quotes stay as they are, blank lines stay where they are. Code blocks and inline code stay unchanged, not "
    "translated. Link and image targets stay unchanged, including references like doc-file:12 or doc-page:3 and suffixes "
    "like #light or #dark; HTML tags stay unchanged. Product names and identifiers stay untranslated. Add nothing, leave "
    "nothing out. Answer with the translation only."
)


def _llm_chunks(body: str) -> list[str]:
    chunks, current = [], ""
    for _, text in blocks(body):
        if current and len(current) + len(text) + 1 > LLM_CHUNK:
            chunks.append(current)
            current = ""
        current = f"{current}\n{text}" if current else text
    if current or not chunks:
        chunks.append(current)
    return chunks


async def by_llm(site: DocSite, title: str, body: str, lang: str, llm: Llm) -> tuple[str, str]:
    """Each chunk whose answer has another Markdown skeleton than the chunk is asked once more; then it fails."""
    system = LLM_SYSTEM.format(source=site.language, target=lang)
    head = await llm(system, f"Translate this title (one line, nothing else):\n{title}")
    parts = []
    for chunk in _llm_chunks(body):
        if not chunk.strip():
            parts.append(chunk)
            continue
        for attempt in range(2):
            answer = (await llm(system, chunk)).strip("\n")
            if (problem := differs(chunk, answer)) is None:
                break
            log.info("Translation into %s changed the Markdown structure (attempt %d): %s", lang, attempt + 1, problem)
        else:
            raise ServiceError(f"The translation changed the Markdown structure: {problem}")
        parts.append(answer)
    return head.strip().split("\n", 1)[0][:200] or title, "\n".join(parts)


async def translate(site: DocSite, page: DocPage, lang: str, llm: Llm | None) -> tuple[str, str, str]:
    """(title, body, by whom)."""
    if site.translator in ("deepl", "google"):
        title, body = await by_api(site, page.title, page.body, lang)
        return title, body, site.translator
    if site.translator == "llm" and llm is not None:
        title, body = await by_llm(site, page.title, page.body, lang, llm)
        return title, body, f"llm {site.translate_model or 'default'}"
    raise ServiceError("This site has no translator.")
