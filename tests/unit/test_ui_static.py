"""services/ui_static/handler.py: the interim UI server behind the HTTP API (docs/DEPLOY.md)."""

from __future__ import annotations

import base64
import gzip
import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

HANDLER = Path(__file__).resolve().parents[2] / "services" / "ui_static" / "handler.py"


def _site(root: Path) -> Path:
    site = root / "site"
    (site / "events").mkdir(parents=True)
    (site / "_next" / "static" / "chunks").mkdir(parents=True)
    (site / "_next" / "static" / "media").mkdir(parents=True)
    (site / "index.html").write_text(
        "<!doctype html><title>home</title>"
        '<script src="/ui/_next/static/chunks/main-abc.js" async=""></script>'
        "<script>(self.__next_f=self.__next_f||[]).push([0])</script>"
        '<script>self.__next_f.push([1,"a"])</script>' + "x" * 800
    )
    (site / "index.txt").write_text("rsc payload")
    (site / "events" / "index.html").write_text("<!doctype html><title>events</title>")
    (site / "404.html").write_text("<!doctype html><title>not found</title>")
    (site / "_next" / "static" / "chunks" / "main-abc.js").write_text("console.log(1);" * 100)
    (site / "_next" / "static" / "media" / "font.woff2").write_bytes(b"\x00\x01wOF2\xff")
    (root / "secret.txt").write_text("outside the site")
    return site


@pytest.fixture()
def mod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("UI_SITE_DIR", str(_site(tmp_path)))
    monkeypatch.setenv("UI_BASE_PATH", "/ui")
    spec = importlib.util.spec_from_file_location("ui_static_handler_under_test", HANDLER)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def call(mod: ModuleType, path: str, method: str = "GET", query: str = "", **headers: str) -> dict[str, Any]:
    event = {
        "rawPath": path,
        "rawQueryString": query,
        "headers": headers,
        "requestContext": {"http": {"method": method}},
    }
    result: dict[str, Any] = mod.handler(event)
    return result


def body_bytes(r: dict[str, Any]) -> bytes:
    raw = r["body"]
    data = base64.b64decode(raw) if r.get("isBase64Encoded") else raw.encode()
    return gzip.decompress(data) if r["headers"].get("content-encoding") == "gzip" else data


def test_root_and_base_redirect_to_index(mod: ModuleType) -> None:
    r = call(mod, "/")
    assert r["statusCode"] == 302 and r["headers"]["location"] == "/ui/"
    r = call(mod, "/ui", query="a=1")
    assert r["statusCode"] == 308 and r["headers"]["location"] == "/ui/?a=1"


def test_index_html_with_revalidate_and_security_headers(mod: ModuleType) -> None:
    r = call(mod, "/ui/")
    assert r["statusCode"] == 200
    assert r["headers"]["content-type"] == "text/html; charset=utf-8"
    assert r["headers"]["cache-control"] == "no-cache"
    assert r["headers"]["x-content-type-options"] == "nosniff"
    assert b"<title>home</title>" in body_bytes(r)


def test_directory_deep_link_and_trailing_slash_redirect(mod: ModuleType) -> None:
    r = call(mod, "/ui/events/", query="id=e1")
    assert r["statusCode"] == 200 and b"<title>events</title>" in body_bytes(r)
    r = call(mod, "/ui/events", query="id=e1")
    assert r["statusCode"] == 308 and r["headers"]["location"] == "/ui/events/?id=e1"


def test_hashed_assets_are_immutable_with_correct_types(mod: ModuleType) -> None:
    js = call(mod, "/ui/_next/static/chunks/main-abc.js")
    assert js["statusCode"] == 200
    assert js["headers"]["content-type"] == "text/javascript; charset=utf-8"
    assert js["headers"]["cache-control"] == "public, max-age=31536000, immutable"
    font = call(mod, "/ui/_next/static/media/font.woff2")
    assert font["headers"]["content-type"] == "font/woff2" and font["isBase64Encoded"] is True
    assert body_bytes(font) == b"\x00\x01wOF2\xff"
    assert call(mod, "/ui/index.txt")["headers"]["content-type"] == "text/plain; charset=utf-8"


def test_gzip_only_when_accepted_with_distinct_etag(mod: ModuleType) -> None:
    plain = call(mod, "/ui/_next/static/chunks/main-abc.js")
    gz = call(mod, "/ui/_next/static/chunks/main-abc.js", **{"Accept-Encoding": "gzip, br"})
    assert "content-encoding" not in plain["headers"]
    assert gz["headers"]["content-encoding"] == "gzip" and gz["isBase64Encoded"] is True
    assert body_bytes(gz) == body_bytes(plain)
    assert gz["headers"]["etag"] != plain["headers"]["etag"]
    assert gz["headers"]["vary"] == "accept-encoding"


def test_conditional_get_returns_304(mod: ModuleType) -> None:
    etag = call(mod, "/ui/")["headers"]["etag"]
    r = call(mod, "/ui/", **{"If-None-Match": etag})
    assert r["statusCode"] == 304 and r["body"] == ""


def test_missing_page_serves_export_404(mod: ModuleType) -> None:
    for p in ("/ui/nope/", "/ui/nope", "/ui/_next/static/chunks/gone.js", "/elsewhere"):
        r = call(mod, p)
        assert r["statusCode"] == 404, p
        assert b"not found" in body_bytes(r)


def test_traversal_and_outside_files_are_404(mod: ModuleType) -> None:
    for p in ("/ui/../secret.txt", "/ui/%2e%2e/secret.txt", "/ui/events/../../secret.txt", "/ui/\x00"):
        r = call(mod, p)
        assert r["statusCode"] == 404, p
        assert b"outside the site" not in body_bytes(r)


def test_head_has_no_body_and_writes_are_rejected(mod: ModuleType) -> None:
    r = call(mod, "/ui/", method="HEAD")
    assert r["statusCode"] == 200 and r["body"] == "" and int(r["headers"]["content-length"]) > 0
    assert call(mod, "/ui/", method="POST")["statusCode"] == 405


def _csp(r: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in r["headers"]["content-security-policy"].split(";"):
        name, _, value = part.strip().partition(" ")
        out[name] = value
    return out


def test_html_has_strict_csp_with_inline_script_hashes(mod: ModuleType) -> None:
    r = call(mod, "/ui/")
    csp = _csp(r)
    expected = {
        base64.b64encode(hashlib.sha256(s.encode()).digest()).decode()
        for s in ("(self.__next_f=self.__next_f||[]).push([0])", 'self.__next_f.push([1,"a"])')
    }
    script = csp["script-src"].split()
    assert script[0] == "'self'" and "'unsafe-inline'" not in script and "'unsafe-eval'" not in script
    assert {s.strip("'").removeprefix("sha256-") for s in script[1:]} == expected
    assert csp["default-src"] == "'self'"
    assert csp["img-src"] == "'self' data:"
    assert csp["style-src"] == "'self' 'unsafe-inline'"
    assert csp["connect-src"] == "'self'"
    assert csp["frame-ancestors"] == "'none'"
    assert csp["base-uri"] == "'self'"
    assert csp["object-src"] == "'none'"
    h = r["headers"]
    assert h["x-content-type-options"] == "nosniff"
    assert h["referrer-policy"] == "strict-origin-when-cross-origin"
    assert h["permissions-policy"] == "camera=(), microphone=(), geolocation=()"


def test_csp_on_every_html_response_including_404_and_304(mod: ModuleType) -> None:
    assert "content-security-policy" in call(mod, "/ui/events/")["headers"]
    assert "content-security-policy" in call(mod, "/ui/nope/")["headers"]
    etag = call(mod, "/ui/")["headers"]["etag"]
    assert "content-security-policy" in call(mod, "/ui/", **{"If-None-Match": etag})["headers"]
    # A page without inline scripts gets no hash sources at all.
    assert _csp(call(mod, "/ui/events/"))["script-src"] == "'self'"
    # Assets carry the other security headers but no CSP (it only applies to documents).
    js = call(mod, "/ui/_next/static/chunks/main-abc.js")["headers"]
    assert "content-security-policy" not in js and js["x-content-type-options"] == "nosniff"
