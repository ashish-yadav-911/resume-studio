"""Company research with pluggable providers.

Sources, in priority order:
1. Exa semantic search (when EXA_API_KEY is configured) for official pages,
   careers pages, engineering blogs and values pages.
2. Greenhouse / Lever public job board APIs for published roles.
3. Wikipedia REST API as a key-free fallback so research still works locally.

All retrieved content is treated as untrusted data and truncated.  No provider
result is ever executed or interpreted as instructions.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import settings
from .schemas import Source

USER_AGENT = settings.research_user_agent
MAX_CONTENT_CHARS = 4000


@dataclass
class ResearchBundle:
    company: str
    target_role: str
    sources: list[Source] = field(default_factory=list)
    job_listings: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    provider_status: dict[str, Any] = field(default_factory=dict)
    retrieved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def company_slug(company: str) -> str:
    return re.sub(r"[^a-z0-9]", "", company.lower())


NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")


def _is_blocked_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # NAT64 (RFC 6052) embeds the real IPv4 destination in the last 32 bits.
    # Python marks the well-known prefix as "reserved", so unwrap it first.
    if isinstance(address, ipaddress.IPv6Address) and address in NAT64_PREFIX:
        return _is_blocked_address(ipaddress.IPv4Address(int(address) & 0xFFFFFFFF))
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def is_public_http_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return False
    try:
        address = ipaddress.ip_address(host)
        return not _is_blocked_address(address)
    except ValueError:
        # Resolve DNS and reject private answers (best-effort SSRF guard).
        try:
            infos = socket.getaddrinfo(host, None)
            for info in infos:
                resolved = ipaddress.ip_address(info[4][0])
                if _is_blocked_address(resolved):
                    return False
        except (socket.gaierror, ValueError):
            return False
    return True


async def fetch_public_url(url: str, *, timeout: float | None = None) -> tuple[str, str]:
    """Fetch a public URL with manual redirect validation.

    Returns (final_url, text). Raises ValueError for unsafe targets.
    """
    if not is_public_http_url(url):
        raise ValueError("URL is not a permitted public HTTP(S) address")
    timeout = timeout or settings.research_timeout_seconds
    current = url
    async with httpx.AsyncClient(
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"},
        follow_redirects=False,
    ) as client:
        for _ in range(4):
            response = await client.get(current)
            if response.status_code in {301, 302, 303, 307, 308}:
                target = response.headers.get("location", "")
                current = str(httpx.URL(current).join(target))
                if not is_public_http_url(current):
                    raise ValueError("Redirect target is not permitted")
                continue
            response.raise_for_status()
            text = response.text[:200_000]
            return current, text
    raise ValueError("Too many redirects")


class ExaProvider:
    name = "exa"

    @property
    def enabled(self) -> bool:
        return settings.research_enabled

    async def search(self, query: str, *, num_results: int = 4, include_domains: list[str] | None = None) -> list[Source]:
        if not self.enabled:
            return []
        payload: dict[str, Any] = {
            "query": query,
            "numResults": num_results,
            "type": "auto",
            "contents": {"text": {"maxCharacters": MAX_CONTENT_CHARS}, "summary": {"query": query}},
        }
        if include_domains:
            payload["includeDomains"] = include_domains
        async with httpx.AsyncClient(timeout=settings.research_timeout_seconds) as client:
            response = await client.post(
                f"{settings.exa_base_url.rstrip('/')}/search",
                headers={"x-api-key": settings.exa_api_key or "", "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        sources: list[Source] = []
        for result in data.get("results", [])[:num_results]:
            url = result.get("url", "")
            if not url.startswith(("http://", "https://")):
                continue
            snippet = (result.get("summary") or result.get("text") or "")[:MAX_CONTENT_CHARS]
            domain = urlparse(url).hostname or ""
            sources.append(
                Source(
                    title=(result.get("title") or domain)[:250],
                    url=url,
                    domain=domain,
                    published_date=result.get("publishedDate"),
                    retrieved_at=now_iso(),
                    snippet=snippet,
                    kind="web",
                    official=False,
                )
            )
        return sources

    async def search_company(self, company: str, role: str) -> list[Source]:
        queries = [
            f"{company} official mission values engineering principles",
            f"{company} engineering blog technical architecture",
            f"{company} careers {role} job openings",
        ]
        results = await asyncio.gather(
            *(self.search(query, num_results=3) for query in queries), return_exceptions=True
        )
        sources: list[Source] = []
        for result in results:
            if isinstance(result, list):
                sources.extend(result)
        return sources


class WikipediaProvider:
    name = "wikipedia"

    async def search_company(self, company: str) -> list[Source]:
        from urllib.parse import quote

        title = quote(company.strip().replace(" ", "_"), safe="")
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
        async with httpx.AsyncClient(timeout=settings.research_timeout_seconds, headers={"User-Agent": USER_AGENT}) as client:
            response = await client.get(url)
            if response.status_code != 200:
                # Fall back to the search API for ambiguous names.
                search = await client.get(
                    "https://en.wikipedia.org/w/api.php",
                    params={"action": "query", "list": "search", "srsearch": company, "format": "json", "srlimit": 1},
                )
                if search.status_code != 200:
                    return []
                items = search.json().get("query", {}).get("search", [])
                if not items:
                    return []
                title = quote(items[0]["title"].replace(" ", "_"), safe="")
                url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
                response = await client.get(url)
                if response.status_code != 200:
                    return []
            data = response.json()
        extract = (data.get("extract") or "")[:MAX_CONTENT_CHARS]
        page_url = (data.get("content_urls", {}).get("desktop", {}) or {}).get("page", "")
        if not extract or not page_url:
            return []
        return [
            Source(
                title=f"{data.get('title', company)} — Wikipedia",
                url=page_url,
                domain="en.wikipedia.org",
                retrieved_at=now_iso(),
                snippet=extract,
                kind="encyclopedia",
                official=False,
            )
        ]


class GreenhouseProvider:
    name = "greenhouse"

    async def search_jobs(self, company: str, role: str) -> tuple[list[Source], list[dict[str, Any]], str | None]:
        slug = company_slug(company)
        if not slug:
            return [], [], "No company slug"
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
        try:
            async with httpx.AsyncClient(timeout=settings.research_timeout_seconds, headers={"User-Agent": USER_AGENT}) as client:
                response = await client.get(url)
                if response.status_code == 404:
                    return [], [], "No public Greenhouse board found"
                response.raise_for_status()
                jobs = response.json().get("jobs", [])
        except Exception as exc:
            return [], [], f"Greenhouse unavailable: {type(exc).__name__}"
        role_terms = [t for t in re.findall(r"[a-z]+", role.lower()) if len(t) > 2]
        matched = [
            job for job in jobs
            if not role_terms or any(term in (job.get("title", "") + " " + str(job.get("metadata", ""))).lower() for term in role_terms)
        ][:6]
        sources: list[Source] = []
        listings: list[dict[str, Any]] = []
        for job in matched:
            absolute_url = job.get("absolute_url", "")
            if not absolute_url.startswith("https://"):
                continue
            title = job.get("title", "Role")
            location = (job.get("location") or {}).get("name", "")
            snippet = f"{title} — {location}".strip(" —")
            sources.append(
                Source(
                    title=f"{company}: {title}",
                    url=absolute_url,
                    domain=urlparse(absolute_url).hostname or "greenhouse.io",
                    retrieved_at=now_iso(),
                    snippet=snippet,
                    kind="job_listing",
                    official=True,
                )
            )
            listings.append({"title": title, "location": location, "url": absolute_url, "source": "greenhouse"})
        return sources, listings, None


class LeverProvider:
    name = "lever"

    async def search_jobs(self, company: str, role: str) -> tuple[list[Source], list[dict[str, Any]], str | None]:
        slug = company_slug(company)
        if not slug:
            return [], [], "No company slug"
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        try:
            async with httpx.AsyncClient(timeout=settings.research_timeout_seconds, headers={"User-Agent": USER_AGENT}) as client:
                response = await client.get(url)
                if response.status_code == 404:
                    return [], [], "No public Lever board found"
                response.raise_for_status()
                jobs = response.json()
                if not isinstance(jobs, list):
                    return [], [], "Unexpected Lever response"
        except Exception as exc:
            return [], [], f"Lever unavailable: {type(exc).__name__}"
        role_terms = [t for t in re.findall(r"[a-z]+", role.lower()) if len(t) > 2]
        matched = [
            job for job in jobs
            if not role_terms or any(term in job.get("text", "").lower() for term in role_terms)
        ][:6]
        sources: list[Source] = []
        listings: list[dict[str, Any]] = []
        for job in matched:
            absolute_url = job.get("hostedUrl", "")
            if not absolute_url.startswith("https://"):
                continue
            title = job.get("text", "Role")
            location = (job.get("categories") or {}).get("location", "")
            sources.append(
                Source(
                    title=f"{company}: {title}",
                    url=absolute_url,
                    domain=urlparse(absolute_url).hostname or "lever.co",
                    retrieved_at=now_iso(),
                    snippet=f"{title} — {location}".strip(" —"),
                    kind="job_listing",
                    official=True,
                )
            )
            listings.append({"title": title, "location": location, "url": absolute_url, "source": "lever"})
        return sources, listings, None


def _page_title(html: str) -> str | None:
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html or "")
    if not match:
        return None
    title = re.sub(r"\s+", " ", unescape(match.group(1))).strip()
    return title[:250] or None


def _page_description(html: str) -> str | None:
    patterns = [
        r'(?is)<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
        r'(?is)<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html or "")
        if match:
            text = re.sub(r"\s+", " ", unescape(match.group(1))).strip()
            if text:
                return text[:800]
    return None


class OfficialSiteProvider:
    """Best-effort key-free discovery of a company's own public pages.

    Guesses the conventional ``<slug>.com`` hosts, verifies that the response is
    a real HTML page, then probes common careers/about paths.  Only public URLs
    are fetched (SSRF-guarded) and every result is marked ``official_site``.
    """

    name = "official_site"

    async def _probe(self, url: str) -> tuple[str, str] | None:
        try:
            final_url, html = await asyncio.wait_for(fetch_public_url(url), timeout=6.0)
        except Exception:
            return None
        if not html or "<html" not in html[:4000].lower():
            return None
        return final_url, html

    async def search_company(self, company: str) -> list[Source]:
        slug = company_slug(company)
        if not slug:
            return []
        candidates = [f"https://www.{slug}.com", f"https://{slug}.com", f"https://about.{slug}.com"]
        results = await asyncio.gather(*(self._probe(url) for url in candidates), return_exceptions=True)
        sources: list[Source] = []
        primary_host: str | None = None
        for result in results:
            if not isinstance(result, tuple):
                continue
            final_url, html = result
            if primary_host is None:
                parsed = urlparse(final_url)
                primary_host = f"{parsed.scheme}://{parsed.netloc}"
            sources.append(self._to_source(final_url, html, "homepage"))
        if primary_host:
            paths = [("/careers", "careers"), ("/jobs", "careers"), ("/about", "about")]
            probes = await asyncio.gather(*(self._probe(primary_host + path) for path, _ in paths), return_exceptions=True)
            for (_, kind), result in zip(paths, probes):
                if isinstance(result, tuple) and len(sources) < 3:
                    sources.append(self._to_source(result[0], result[1], kind))
        # Drop duplicates that redirected to the same URL, then cap.
        unique: list[Source] = []
        seen: set[str] = set()
        for source in sources:
            key = source.url.rstrip("/")
            if key not in seen:
                seen.add(key)
                unique.append(source)
        return unique[:3]

    @staticmethod
    def _to_source(url: str, html: str, kind: str) -> Source:
        host = urlparse(url).hostname or ""
        title = _page_title(html) or host
        snippet = _page_description(html) or strip_html(html)[:800]
        return Source(
            title=f"{title}"[:250],
            url=url,
            domain=host,
            retrieved_at=now_iso(),
            snippet=snippet,
            kind="official_site",
            official=True,
        )


class ResearchService:
    def __init__(self) -> None:
        self.exa = ExaProvider()
        self.wikipedia = WikipediaProvider()
        self.greenhouse = GreenhouseProvider()
        self.lever = LeverProvider()
        self.official = OfficialSiteProvider()

    async def research(self, company: str, role: str) -> ResearchBundle:
        company = company.strip()
        role = role.strip()
        bundle = ResearchBundle(company=company, target_role=role)
        if not company:
            bundle.limitations.append("No company was specified, so no company research could be performed.")
            return bundle

        tasks: dict[str, Any] = {
            "wikipedia": self.wikipedia.search_company(company),
            "greenhouse": self.greenhouse.search_jobs(company, role),
            "lever": self.lever.search_jobs(company, role),
            "official_site": self.official.search_company(company),
        }
        if self.exa.enabled:
            tasks["exa"] = self.exa.search_company(company, role)
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        by_name = dict(zip(tasks, results))

        if "exa" in tasks:
            exa_result = by_name["exa"]
            if isinstance(exa_result, list) and exa_result:
                for source in exa_result:
                    if not source.domain.endswith("wikipedia.org"):
                        source.official = _looks_official(source.url, company)
                bundle.sources.extend(exa_result)
                bundle.provider_status["exa"] = f"{len(exa_result)} results"
            else:
                bundle.provider_status["exa"] = f"unavailable: {getattr(exa_result, '__class__', type(exa_result)).__name__}"
                bundle.limitations.append("Exa web search returned no results; official-page coverage may be limited.")
        else:
            bundle.provider_status["exa"] = "not configured (EXA_API_KEY missing)"
            bundle.limitations.append(
                "Exa search is not configured, so only key-free sources (Wikipedia and public job boards) were used."
            )

        wiki_result = by_name["wikipedia"]
        if isinstance(wiki_result, list) and wiki_result:
            bundle.sources.extend(wiki_result)
            bundle.provider_status["wikipedia"] = "ok"
        else:
            bundle.provider_status["wikipedia"] = "unavailable"
            bundle.limitations.append("No reliable encyclopedia summary was found for this company name.")

        official_result = by_name["official_site"]
        if isinstance(official_result, list) and official_result:
            bundle.sources.extend(official_result)
            bundle.provider_status["official_site"] = f"{len(official_result)} pages"
        else:
            bundle.provider_status["official_site"] = "no official page found"

        for provider in (self.greenhouse, self.lever):
            result = by_name[provider.name]
            if isinstance(result, tuple):
                sources, listings, error = result
                bundle.sources.extend(sources)
                bundle.job_listings.extend(listings)
                bundle.provider_status[provider.name] = error or f"{len(listings)} published roles"
            else:
                bundle.provider_status[provider.name] = "unavailable"

        bundle.sources = _dedupe_sources(bundle.sources)[:14]
        bundle.sources.sort(key=lambda s: (not s.official, s.kind != "job_listing"))
        if not bundle.job_listings:
            bundle.limitations.append(
                "No public Greenhouse/Lever listings matched this role. Do not infer hiring preferences without evidence."
            )
        return bundle

    async def fetch_page(self, url: str) -> str:
        _, html = await fetch_public_url(url)
        return strip_html(html)[:MAX_CONTENT_CHARS]


def _looks_official(url: str, company: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    slug = company_slug(company)
    if not slug:
        return False
    labels = host.split(".")
    return slug in labels or any(slug in label for label in labels if len(label) > 3) and not host.endswith("wikipedia.org")


def _dedupe_sources(sources: list[Source]) -> list[Source]:
    seen: set[str] = set()
    output: list[Source] = []
    for source in sources:
        key = source.url.rstrip("/")
        if not key or key in seen:
            continue
        if not source.url.startswith(("http://", "https://")):
            continue
        seen.add(key)
        output.append(source)
    return output


research_service = ResearchService()
