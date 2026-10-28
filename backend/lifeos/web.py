"""Read-only web access for the research agent. Off until the user enables it in Settings.

Pages are fetched with a size cap, only over http(s), never to private or loopback addresses
(so a prompt-injected URL can't reach the local LifeOS API or the LAN), and returned as
plain text. Web content is untrusted and is passed to models inside delimiters.
"""
from __future__ import annotations

import html
import ipaddress
import re
import socket
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from . import settings

MAX_BYTES = 2 * 1024 * 1024
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36 AI-LifeOS"


class WebError(Exception):
    pass


def _require_enabled() -> None:
    if not settings.get("web_access"):
        raise WebError("Web access is turned off (Settings → Crew & web)")


def _check_host(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise WebError("Only http and https URLs can be fetched")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise WebError(f"Can't resolve {parsed.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise WebError(f"{parsed.hostname} resolves to a private address; refusing to fetch it")


def html_to_text(raw: str) -> tuple[str, str]:
    title_m = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    title = html.unescape(re.sub(r"\s+", " ", title_m.group(1))).strip() if title_m else ""
    body = re.sub(r"(?is)<(script|style|noscript|svg|head|nav|footer)[^>]*>.*?</\1>", " ", raw)
    body = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", body)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    body = html.unescape(body)
    body = re.sub(r"[ \t\r\f\v]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n\n", body).strip()
    return title, body


def _get(url: str, params: dict[str, str] | None = None) -> httpx.Response:
    current = url
    with httpx.Client(timeout=20, headers={"User-Agent": UA}, follow_redirects=False) as client:
        for _ in range(5):
            _check_host(current)
            r = client.get(current, params=params)
            params = None
            if r.is_redirect and r.headers.get("location"):
                current = str(r.url.join(r.headers["location"]))
                continue
            r.raise_for_status()
            if len(r.content) > MAX_BYTES:
                raise WebError("Page is larger than 2 MB")
            return r
    raise WebError("Too many redirects")


def fetch(url: str, max_chars: int = 8000) -> dict[str, Any]:
    _require_enabled()
    try:
        r = _get(url)
    except httpx.HTTPError as exc:
        raise WebError(f"Fetch failed: {exc}") from exc
    ctype = r.headers.get("content-type", "")
    if "html" in ctype or r.text.lstrip().startswith("<"):
        title, text = html_to_text(r.text)
    elif ctype.startswith("text/") or "json" in ctype:
        title, text = "", r.text
    else:
        raise WebError(f"Unsupported content type {ctype or 'unknown'}")
    return {"url": str(r.url), "title": title, "text": text[:max_chars], "truncated": len(text) > max_chars}


def search(query: str, limit: int = 8) -> list[dict[str, str]]:
    """Keyless web search through DuckDuckGo's HTML endpoint."""
    _require_enabled()
    try:
        r = _get("https://html.duckduckgo.com/html/", {"q": query})
    except httpx.HTTPError as exc:
        raise WebError(f"Search failed: {exc}") from exc
    results = []
    for m in re.finditer(r'(?is)<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|$)',
                         r.text):
        href = html.unescape(m.group(1))
        if "uddg=" in href:
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        snippet_m = re.search(r'(?is)class="result__snippet"[^>]*>(.*?)</a>', m.group(3))
        results.append({
            "title": html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip(),
            "url": href,
            "snippet": html.unescape(re.sub(r"<[^>]+>", "", snippet_m.group(1))).strip() if snippet_m else "",
        })
        if len(results) >= limit:
            break
    return results
