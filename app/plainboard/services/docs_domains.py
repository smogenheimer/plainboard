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

import asyncio
import logging
import re
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


# Cloudflare's own error pages (body "error code: 525"): what happened, what to do. {domain}/{target} are filled in.
CLOUDFLARE_ERRORS: dict[int, tuple[str, str]] = {
    520: ("Cloudflare reaches your server, but it answers with something empty or unexpected (Cloudflare error 520).",
          "Check that PlainBoard runs behind your reverse proxy and look at the proxy's log for {domain}."),
    521: ("Cloudflare reaches your server's address, but the connection is refused (Cloudflare error 521): the web server or reverse proxy is down or not listening.",
          "Start the reverse proxy and PlainBoard, and check that ports 80/443 are open to Cloudflare."),
    522: ("Cloudflare cannot connect to your server in time (Cloudflare error 522): the server is down, a firewall blocks Cloudflare, or the DNS record points to the wrong address.",
          "Check that the server is up, that ports 80/443 are open to Cloudflare, and that the DNS record points to the PlainBoard server{cname}."),
    523: ("Cloudflare cannot reach the address the DNS record points to (Cloudflare error 523): usually a wrong IP address or host in the record.",
          "Check that the DNS record points to the PlainBoard server{cname}."),
    524: ("Cloudflare connected to your server, but it did not answer within 100 seconds (Cloudflare error 524).",
          "Check that PlainBoard and the reverse proxy respond; look at their logs."),
    525: ("Cloudflare reaches your server, but the TLS handshake fails (Cloudflare error 525): your reverse proxy doesn't serve this hostname or has no certificate for it.",
          "Add {domain} to the proxy host of PlainBoard with a certificate covering it, and check that the DNS record points to the PlainBoard server{cname}."),
    526: ("Cloudflare reaches your server, but its certificate is not valid for {domain} (Cloudflare error 526, SSL mode \"Full (strict)\").",
          "Give the proxy host of PlainBoard a valid certificate covering {domain} (e.g. Let's Encrypt or a Cloudflare origin certificate), or set Cloudflare's SSL mode to \"Full\"."),
    527: ("Cloudflare could not connect to your server through Railgun (Cloudflare error 527).",
          "Check the Railgun connection, or turn Railgun off for {domain}."),
    530: ("Cloudflare cannot reach your server (Cloudflare error 530).",
          "Check that the DNS record points to the PlainBoard server{cname} or to a running Cloudflare tunnel."),
    1001: ("Cloudflare does not know {domain} (Cloudflare error 1001): the DNS record is missing or still new.",
           "Create the DNS record{cname} in the domain's Cloudflare zone and try again in a few minutes."),
    1014: ("Cloudflare refuses the CNAME (Cloudflare error 1014): it points to a host in another Cloudflare account.",
           "Point the record directly to the PlainBoard server's IP, or turn the proxy (orange cloud) off for it."),
    1016: ("Cloudflare cannot resolve the host the DNS record points to (Cloudflare error 1016).",
           "Check the target of the DNS record: it must be the PlainBoard server{cname}."),
    1033: ("The Cloudflare tunnel for {domain} is not connected (Cloudflare error 1033).",
           "Start cloudflared and give the tunnel a public hostname {domain} that points to PlainBoard."),
}  # fmt: skip
_CF_CODE = re.compile(r"error code:\s*(\d{3,4})", re.I)


def _is_cloudflare(resp: httpx.Response) -> bool:
    return "cf-ray" in resp.headers or resp.headers.get("server", "").lower() == "cloudflare"


async def _resolves(domain: str) -> bool:
    try:
        return bool(await asyncio.get_running_loop().getaddrinfo(domain, 443))
    except (OSError, UnicodeError):
        return False


async def _explain(domain: str, url: str, result: httpx.Response | Exception) -> tuple[str, str]:
    """What the domain's answer (or failure) means, and what to do about it: (detail, hint) with {cname}."""
    proxy = f"Check that the DNS record points to the PlainBoard server{{cname}} and that your reverse proxy forwards {domain} to PlainBoard."
    if isinstance(result, httpx.TooManyRedirects):
        return (f"{url} redirects in a loop.",
                "Behind Cloudflare this is usually SSL mode \"Flexible\" while the proxy redirects to HTTPS: set Cloudflare's SSL mode to \"Full\".")  # fmt: skip
    if isinstance(result, httpx.TimeoutException):
        return f"{url} did not answer within {VERIFY_TIMEOUT:g} seconds.", "Check that the server is up and that ports 80/443 are open."
    if isinstance(result, Exception):
        text = str(result)
        if isinstance(result, httpx.ConnectError) and not await _resolves(domain):
            return (f"{domain} does not resolve: there is no DNS record for it (yet).",
                    "Create the DNS record{cname}; a new record can take a few minutes to be seen.")  # fmt: skip
        if "CERTIFICATE" in text.upper() or "SSL" in text.upper():
            return (f"{url}: the TLS certificate is not valid for {domain} ({text[:160]}).",
                    f"Give the proxy host of PlainBoard a certificate covering {domain}.")  # fmt: skip
        return f"{url} could not be reached ({type(result).__name__}).", "Check that the server is up and that ports 80/443 are open. " + proxy
    resp = result
    where = str(resp.url)
    if where.split("?", 1)[0] != url:
        where = f"{url} (redirected to {where})"
    if _is_cloudflare(resp):
        found = _CF_CODE.search(resp.text[:2000])
        code = int(found.group(1)) if found else resp.status_code
        if code in CLOUDFLARE_ERRORS:
            detail, hint = CLOUDFLARE_ERRORS[code]
            return detail.format(domain=domain, cname="{cname}"), hint.format(domain=domain, cname="{cname}")
        if found or 520 <= resp.status_code <= 530:
            return f"Cloudflare answered {where} with error {code}: it does not reach PlainBoard.", proxy
    if resp.status_code == 200:
        return f"{where} answered, but not with this site's token: that is not this PlainBoard.", proxy
    return f"{where} answered {resp.status_code} instead of this site's token: the domain does not reach this PlainBoard.", proxy


async def verify(session: AsyncSession, site: DocSite, client: httpx.AsyncClient | None = None, target: str = "") -> DocVerifyOut:
    """Ask the domain for the site's token, over HTTPS and then HTTP, following redirects (http -> https). Found:
    verified from now on (the site is served there while it is published). Not found: what went wrong (a Cloudflare
    error page, no DNS record, a certificate, another server) and what to do; an earlier verification stays."""
    if not site.domain:
        raise ServiceError("This site has no domain.")
    domain, token = site.domain, site.domain_token
    await session.commit()  # nothing of the database is held while the domain answers
    own = client is None
    client = client or httpx.AsyncClient(timeout=VERIFY_TIMEOUT)
    results: list[tuple[str, httpx.Response | Exception]] = []
    try:
        for scheme in ("https", "http"):
            url = f"{scheme}://{domain}{WELL_KNOWN}"
            try:
                resp = await client.get(url, follow_redirects=True)
            except httpx.HTTPError as exc:
                results.append((url, exc))
                continue
            if resp.status_code == 200 and resp.text.strip() == token:
                site.domain_verified_at = utcnow()
                await session.commit()
                forget()
                from . import docs as docs_service

                await docs_service.publish_site(session, site)
                return DocVerifyOut(verified=True, verified_at=site.domain_verified_at)
            results.append((url, resp))
    finally:
        if own:
            await client.aclose()
    # the HTTPS answer tells most (http usually only redirects there); a failed connection less than any answer
    url, result = next(((u, r) for u, r in results if isinstance(r, httpx.Response)), results[0])
    detail, hint = await _explain(domain, url, result)
    cname = f" (a CNAME to {target})" if target and target != domain else ""
    return DocVerifyOut(verified=False, detail=detail, hint=hint.replace("{cname}", cname), verified_at=site.domain_verified_at)


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
