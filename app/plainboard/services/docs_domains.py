"""A doc site at its own domain (#118).

The user points the domain at this PlainBoard: a CNAME to the host it is reached at, or a public hostname of the
Cloudflare tunnel in front of it. Requests then arrive with that Host, and ``api/docs_public.DocHost`` serves the
site there and nothing else. Whether the domain really reaches this PlainBoard is checked by asking it:
``https://<domain>/.well-known/plainboard-docs`` must answer with the site's token (which this host answers for
every configured domain). Only a verified domain serves the site. With a Cloudflare API token (stored encrypted
for the site, never shown again) PlainBoard creates the DNS record itself: the zone by the domain's name, a
proxied CNAME to the target.
"""

from __future__ import annotations

import logging
import time

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import DocSite, utcnow
from ..schemas import DocDnsOut, DocDomainOut, DocVerifyOut
from ..security import decrypt, encrypt
from .errors import ServiceError

log = logging.getLogger(__name__)

WELL_KNOWN = "/.well-known/plainboard-docs"
VERIFY_TIMEOUT = 8.0
CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"
CACHE_SECONDS = 30.0

# domain -> (site id, slug, token, served): read from the database at most every CACHE_SECONDS, at once after a change
_cache: dict[str, tuple[int, str, str, bool]] = {}
_cached_at = 0.0


def forget() -> None:
    global _cached_at
    _cached_at = 0.0


async def lookup(host: str) -> tuple[int, str, str, bool] | None:
    """The doc site whose domain this host is: (site id, slug, token, served). A database session only when the
    cache is stale."""
    if time.monotonic() - _cached_at > CACHE_SECONDS:
        from ..db import sessionmaker

        async with sessionmaker()() as session:
            await domains(session)
    return _cache.get(host)


async def domains(session: AsyncSession) -> dict[str, tuple[int, str, str, bool]]:
    global _cache, _cached_at
    if time.monotonic() - _cached_at > CACHE_SECONDS:
        rows = await session.execute(select(DocSite.domain, DocSite.id, DocSite.slug, DocSite.domain_token, DocSite.published, DocSite.domain_verified_at).where(DocSite.domain.is_not(None)))
        _cache = {domain: (id_, slug, token, bool(published and verified)) for domain, id_, slug, token, published, verified in rows.all()}
        _cached_at = time.monotonic()
    return _cache


def own_hosts(request_hosts: set[str] | None = None) -> set[str]:
    """Hosts that are PlainBoard itself: a doc site may never take one."""
    from ..api.guard import _allowed, preview_host

    hosts = {h.split(":", 1)[0] for h in (request_hosts or set()) | _allowed() if h}
    if preview := preview_host():
        hosts.add(preview.split(":", 1)[0])
    return hosts


async def check_free(session: AsyncSession, domain: str, site_id: int | None, request_hosts: set[str] | None = None) -> None:
    if domain in own_hosts(request_hosts):
        raise ServiceError("That is the address of this PlainBoard itself: a doc site needs a domain of its own.")
    taken = await session.scalar(select(DocSite.id).where(DocSite.domain == domain))
    if taken is not None and taken != site_id:
        raise ServiceError(f"{domain} is the domain of another doc site.", 409)


def setup(site: DocSite, target: str) -> DocDomainOut | None:
    if not site.domain:
        return None
    return DocDomainOut(
        domain=site.domain, target=target, verify_url=f"https://{site.domain}{WELL_KNOWN}", token=site.domain_token,
        verified_at=site.domain_verified_at, cloudflare_token_set=bool(site.cloudflare_token_enc),
    )  # fmt: skip


async def verify(session: AsyncSession, site: DocSite, client: httpx.AsyncClient | None = None) -> DocVerifyOut:
    """Ask the domain for the site's token, over HTTPS and then HTTP. Found: verified from now on (the site is
    served there while it is published). Not found: what came back instead; an earlier verification stays."""
    if not site.domain:
        raise ServiceError("This site has no domain.")
    domain, token = site.domain, site.domain_token
    await session.commit()  # nothing of the database is held while the domain answers
    own = client is None
    client = client or httpx.AsyncClient(timeout=VERIFY_TIMEOUT, follow_redirects=False)
    detail = ""
    try:
        for scheme in ("https", "http"):
            try:
                resp = await client.get(f"{scheme}://{domain}{WELL_KNOWN}")
            except httpx.HTTPError as exc:
                detail = f"{scheme}://{domain} could not be reached ({type(exc).__name__})."
                continue
            if resp.status_code == 200 and resp.text.strip() == token:
                site.domain_verified_at = utcnow()
                await session.commit()
                forget()
                from . import docs as docs_service

                await docs_service.publish_site(session, site)
                return DocVerifyOut(verified=True, verified_at=site.domain_verified_at)
            detail = f"{scheme}://{domain}{WELL_KNOWN} answered {resp.status_code}, not with this site's token: the domain does not reach this PlainBoard yet."
    finally:
        if own:
            await client.aclose()
    return DocVerifyOut(verified=False, detail=detail, verified_at=site.domain_verified_at)


async def set_cloudflare_token(session: AsyncSession, site: DocSite, token: str) -> None:
    site.cloudflare_token_enc = encrypt(token.strip()) if token.strip() else None
    await session.commit()


def _errors(data: dict) -> str:
    return "; ".join(str(e.get("message", "")) for e in data.get("errors") or [] if isinstance(e, dict)) or "unknown error"


async def _cf(client: httpx.AsyncClient, method: str, path: str, **kwargs) -> dict:
    try:
        resp = await client.request(method, f"{CLOUDFLARE_API}{path}", **kwargs)
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ServiceError(f"Cloudflare could not be reached: {type(exc).__name__}", 502) from exc
    if not data.get("success"):
        raise ServiceError(f"Cloudflare refused: {_errors(data)}", 400 if resp.status_code < 500 else 502)
    return data


async def create_dns(session: AsyncSession, site: DocSite, target: str, *, proxied: bool = True, client: httpx.AsyncClient | None = None) -> DocDnsOut:
    """The CNAME ``domain -> target`` in the domain's Cloudflare zone, made or changed with the site's token."""
    if not site.domain:
        raise ServiceError("This site has no domain.")
    token = decrypt(site.cloudflare_token_enc)
    if not token:
        raise ServiceError("No Cloudflare API token for this site: give one (permission Zone > DNS > Edit).")
    target = target.strip().lower().rstrip(".")
    if not target or target == site.domain:
        raise ServiceError("Give the host the domain should point to (this PlainBoard's host or the tunnel's).")
    domain = site.domain
    await session.commit()
    own = client is None
    client = client or httpx.AsyncClient(timeout=15.0)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        labels = domain.split(".")
        zone = None
        for start in range(len(labels) - 1):  # docs.a.example.com: a.example.com, then example.com
            name = ".".join(labels[start:])
            found = (await _cf(client, "GET", "/zones", params={"name": name}, headers=headers)).get("result") or []
            if found:
                zone = found[0]
                break
        if zone is None:
            raise ServiceError(f"None of the zones this token sees holds {domain}.")
        existing = (await _cf(client, "GET", f"/zones/{zone['id']}/dns_records", params={"name": domain}, headers=headers)).get("result") or []
        record = {"type": "CNAME", "name": domain, "content": target, "proxied": proxied, "ttl": 1, "comment": "PlainBoard doc site"}
        if existing:
            if any(r.get("type") != "CNAME" for r in existing):
                raise ServiceError(f"{domain} already has a DNS record of type {existing[0].get('type')}: remove it first.", 409)
            await _cf(client, "PUT", f"/zones/{zone['id']}/dns_records/{existing[0]['id']}", json=record, headers=headers)
        else:
            await _cf(client, "POST", f"/zones/{zone['id']}/dns_records", json=record, headers=headers)
    finally:
        if own:
            await client.aclose()
    log.info("Cloudflare: CNAME %s -> %s in zone %s (%s)", domain, target, zone.get("name"), "changed" if existing else "made")
    return DocDnsOut(zone=str(zone.get("name", "")), record=domain, target=target, created=not existing)
