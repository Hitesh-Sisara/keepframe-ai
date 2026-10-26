"""Interim UI hosting: serve the Next.js static export (built with basePath /ui) from the HTTP API.

Used while CloudFront is blocked by new-account verification (docs/DEPLOY.md, "UI interim path").
The export is baked into this zip next to the handler (`site/`), so there is no bucket to expose.
Routes (UiInterimStack): GET|HEAD `/`, `/ui`, `/ui/{proxy+}`. They are unauthenticated on purpose: the
files are the same public JS/HTML a CDN would serve, and they hold no data or secrets. Every data route
stays on the API Lambda, which requires the bearer token entered in the app.

Behaviour mirrors UiStack's CloudFront setup: directory paths map to index.html, trailing-slash export
(`/ui/runs` -> 308 `/ui/runs/`), the export's 404.html with status 404, hashed `_next/static` assets cached
immutably, HTML/RSC payloads revalidated on every load (ETag + 304), gzip when the client accepts it.
HTML carries a strict Content-Security-Policy whose script-src lists the page's inline scripts by hash.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import os
import posixpath
import re
from dataclasses import dataclass
from typing import Any

BASE_PATH = os.environ.get("UI_BASE_PATH", "/ui").rstrip("/")
SITE_DIR = os.path.realpath(os.environ.get("UI_SITE_DIR", os.path.join(os.path.dirname(__file__), "site")))

TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".txt": "text/plain; charset=utf-8",  # Next RSC payloads (index.txt)
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".webmanifest": "application/manifest+json",
    ".xml": "application/xml",
}
COMPRESSIBLE = {".html", ".js", ".mjs", ".css", ".json", ".txt", ".map", ".svg", ".xml", ".webmanifest"}
IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"
SHORT = "public, max-age=3600"
SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
    "strict-transport-security": "max-age=31536000; includeSubDomains",
}
# Content-Security-Policy for HTML (security review S-14). The API, its /img proxy and the fonts are all on
# this origin, so 'self' covers them. The Next.js App Router export inlines a few `self.__next_f.push(...)`
# hydration scripts per page. Those are allowed by their SHA-256 hash, computed from the file at load
# time, instead of 'unsafe-inline'. Inline styles stay allowed because Next/React emit them.
CSP_TEMPLATE = (
    "default-src 'self'; script-src 'self'{hashes}; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; "
    "form-action 'self'"
)
_INLINE_SCRIPT = re.compile(rb"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>", re.S | re.I)


def csp_for_html(body: bytes) -> str:
    """CSP whose script-src allows exactly this page's inline scripts (by hash), nothing else inline."""
    hashes = []
    for content in _INLINE_SCRIPT.findall(body):
        if content.strip():
            digest = base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")
            hashes.append(f" 'sha256-{digest}'")
    return CSP_TEMPLATE.format(hashes="".join(dict.fromkeys(hashes)))


@dataclass(frozen=True)
class Asset:
    body: bytes
    gz: bytes | None
    etag: str
    content_type: str
    cache_control: str
    csp: str | None = None


_cache: dict[str, Asset | None] = {}


def _cache_control(rel: str, ext: str) -> str:
    if rel.startswith("_next/static/"):
        return IMMUTABLE  # content-hashed file names
    if ext in {".html", ".txt"}:
        return REVALIDATE
    return SHORT


def _load(rel: str) -> Asset | None:
    """Read `rel` (relative to SITE_DIR) once per container; None when absent or outside the site."""
    if rel in _cache:
        return _cache[rel]
    full = os.path.realpath(os.path.join(SITE_DIR, rel))
    asset: Asset | None = None
    if full.startswith(SITE_DIR + os.sep) and os.path.isfile(full):
        with open(full, "rb") as f:
            body = f.read()
        ext = posixpath.splitext(rel)[1].lower()
        gz = gzip.compress(body, 6, mtime=0) if ext in COMPRESSIBLE and len(body) > 512 else None
        asset = Asset(
            body=body,
            gz=gz,
            etag='"' + hashlib.sha256(body).hexdigest()[:32] + '"',
            content_type=TYPES.get(ext, "application/octet-stream"),
            cache_control=_cache_control(rel, ext),
            csp=csp_for_html(body) if ext == ".html" else None,
        )
    _cache[rel] = asset
    return asset


def _headers(event: dict[str, Any]) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items()}


def _redirect(location: str, status: int = 308) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"location": location, "cache-control": SHORT, **SECURITY_HEADERS},
        "body": "",
    }


def _respond(asset: Asset, status: int, event: dict[str, Any], method: str) -> dict[str, Any]:
    req = _headers(event)
    use_gz = asset.gz is not None and "gzip" in req.get("accept-encoding", "").lower()
    # Distinct validator per representation, so a cache never pairs a gzip body with the identity ETag.
    etag = asset.etag[:-1] + '-gz"' if use_gz else asset.etag
    headers = {
        "content-type": asset.content_type,
        "cache-control": asset.cache_control,
        "etag": etag,
        "vary": "accept-encoding",
        **SECURITY_HEADERS,
    }
    if asset.csp:
        headers["content-security-policy"] = asset.csp
    if status == 200 and etag in [t.strip() for t in req.get("if-none-match", "").split(",")]:
        return {"statusCode": 304, "headers": headers, "body": ""}
    body = asset.gz if use_gz and asset.gz is not None else asset.body
    if use_gz:
        headers["content-encoding"] = "gzip"
    if method == "HEAD":
        headers["content-length"] = str(len(body))
        return {"statusCode": status, "headers": headers, "body": ""}
    text = not use_gz and (
        asset.content_type.startswith("text/") or asset.content_type.startswith("application/json")
    )
    if text:
        return {
            "statusCode": status,
            "headers": headers,
            "body": body.decode("utf-8"),
            "isBase64Encoded": False,
        }
    return {
        "statusCode": status,
        "headers": headers,
        "body": base64.b64encode(body).decode("ascii"),
        "isBase64Encoded": True,
    }


def _not_found(event: dict[str, Any], method: str) -> dict[str, Any]:
    page = _load("404.html")
    if page is None:
        return {
            "statusCode": 404,
            "headers": {
                "content-type": "text/plain; charset=utf-8",
                "content-security-policy": CSP_TEMPLATE.format(hashes=""),
                **SECURITY_HEADERS,
            },
            "body": "Not found",
        }
    return _respond(page, 404, event, method)


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    method = (event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()
    path = event.get("rawPath") or "/"
    query = event.get("rawQueryString") or ""
    qs = f"?{query}" if query else ""
    if method not in {"GET", "HEAD"}:
        return {"statusCode": 405, "headers": {"allow": "GET, HEAD", **SECURITY_HEADERS}, "body": ""}
    if path in {"/", BASE_PATH}:
        return _redirect(f"{BASE_PATH}/{qs}", 302 if path == "/" else 308)
    if not path.startswith(BASE_PATH + "/"):
        return _not_found(event, method)
    rel = path[len(BASE_PATH) + 1 :]
    if "\x00" in rel or any(part == ".." for part in rel.split("/")):
        return _not_found(event, method)
    if rel == "" or rel.endswith("/"):
        target = rel + "index.html"
    else:
        target = rel
    asset = _load(target)
    if asset is not None:
        return _respond(asset, 200, event, method)
    # trailingSlash export: /ui/runs -> /ui/runs/ when that directory page exists
    if not rel.endswith("/") and "." not in rel.rsplit("/", 1)[-1] and _load(rel + "/index.html") is not None:
        return _redirect(f"{BASE_PATH}/{rel}/{qs}")
    return _not_found(event, method)
