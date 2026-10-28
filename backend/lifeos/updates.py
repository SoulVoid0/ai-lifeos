"""App updates from GitHub Releases, relayed for the desktop app's updater.

The release workflow (.github/workflows/release.yml) attaches the signed installer and a `latest.json`
manifest to each release. The desktop app's updater asks this backend for the manifest instead of GitHub,
so a private repository works: the backend adds the user's read token (kept in the vault, never in the app
or the database) and rewrites the download link to come through here as well. The updater checks the
installer's signature against the public key built into the app, so this relay can't change what gets installed.
"""
from __future__ import annotations

import re
from typing import Any, Iterator

import httpx

from . import config, settings, vault

TOKEN_SECRET = "github_update_token"
API = "https://api.github.com"
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class UpdateError(Exception):
    pass


def cfg() -> dict[str, Any]:
    return {"repo": settings.get("update_repo"), "has_token": vault.has_secret(TOKEN_SECRET)}


def set_cfg(repo: str | None = None, token: str | None = None) -> dict[str, Any]:
    if repo is not None:
        repo = repo.strip().removeprefix("https://github.com/").strip("/")
        if repo and not _REPO.match(repo):
            raise ValueError("Repository must look like owner/name")
        settings.update({"update_repo": repo})
    if token is not None:
        token = token.strip()
        if token:
            vault.set_secret(TOKEN_SECRET, token)
        else:
            vault.delete_secret(TOKEN_SECRET)
    return cfg()


def _headers(accept: str = "application/vnd.github+json") -> dict[str, str]:
    h = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ai-lifeos-updater"}
    token = vault.get_secret(TOKEN_SECRET)
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _get(client: httpx.Client, url: str, accept: str = "application/vnd.github+json") -> httpx.Response:
    try:
        r = client.get(url, headers=_headers(accept))
    except httpx.HTTPError as e:
        raise UpdateError(f"Couldn't reach GitHub: {e}") from e
    if r.status_code in (401, 403):
        raise UpdateError("GitHub refused the update token. Make a new one in Settings → Updates.")
    return r


def latest_release(client: httpx.Client) -> dict[str, Any] | None:
    repo = settings.get("update_repo")
    if not repo:
        raise UpdateError("No update repository is set (Settings → Updates)")
    r = _get(client, f"{API}/repos/{repo}/releases/latest")
    if r.status_code == 404:
        if not vault.has_secret(TOKEN_SECRET):
            raise UpdateError(f"No releases found for {repo}. If the repository is private, add a read token in Settings → Updates.")
        return None
    r.raise_for_status()
    return r.json()


def manifest(client: httpx.Client | None = None) -> dict[str, Any] | None:
    """The newest release's latest.json with download links pointing at this backend. None if there's no release."""
    own = client is None
    client = client or httpx.Client(timeout=30, follow_redirects=True)
    try:
        release = latest_release(client)
        if release is None:
            return None
        assets = {a["name"]: a for a in release.get("assets", [])}
        if "latest.json" not in assets:
            return None
        r = _get(client, assets["latest.json"]["url"], "application/octet-stream")
        r.raise_for_status()
        data = r.json()
    except (ValueError, httpx.HTTPError) as e:
        raise UpdateError(f"The release manifest couldn't be read: {e}") from e
    finally:
        if own:
            client.close()
    base = f"http://127.0.0.1:{config.PORT}/api/system/update/asset"
    for platform in (data.get("platforms") or {}).values():
        name = str(platform.get("url", "")).rsplit("/", 1)[-1]
        if name in assets:
            platform["url"] = f"{base}/{assets[name]['id']}"
    return data


def open_asset(asset_id: int) -> tuple[httpx.Client, httpx.Response]:
    """Start streaming a release asset. The caller closes both."""
    repo = settings.get("update_repo")
    client = httpx.Client(timeout=httpx.Timeout(30, read=120), follow_redirects=True)
    try:
        req = client.build_request("GET", f"{API}/repos/{repo}/releases/assets/{int(asset_id)}",
                                   headers=_headers("application/octet-stream"))
        r = client.send(req, stream=True)
    except httpx.HTTPError as e:
        client.close()
        raise UpdateError(f"Couldn't download the update: {e}") from e
    if r.status_code != 200:
        r.close()
        client.close()
        raise UpdateError(f"GitHub answered {r.status_code} for the update download")
    return client, r


def iter_asset(client: httpx.Client, r: httpx.Response) -> Iterator[bytes]:
    try:
        yield from r.iter_bytes(1 << 16)
    finally:
        r.close()
        client.close()
