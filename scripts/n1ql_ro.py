"""Read-only N1QL helper for a source Couchbase query service (optional data tooling).

Credentials are never stored in the repo. They are resolved at runtime from env vars
COUCHBASE_URL / COUCHBASE_USER / COUCHBASE_PASSWORD, or else read (without echoing) from
the file named by $SOURCE_APIS_ENV (required in that case; the source database's env file,
operator-held and never committed) using the keys CLUSTER_URL, CLUSTER_USER, SECRET_COUCHBASE_API_PWD.

Every request is sent with readonly=true, so the query service rejects any mutation.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

_MUTATION = re.compile(r"^\s*(INSERT|UPSERT|UPDATE|DELETE|MERGE|CREATE|DROP|ALTER|BUILD|GRANT|REVOKE)\b", re.I)


def _read_env_file(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip().removeprefix("export ").strip()] = v.strip().strip('"').strip("'")
    return out


def _creds() -> tuple[str, str, str]:
    url = os.environ.get("COUCHBASE_URL")
    user = os.environ.get("COUCHBASE_USER")
    pwd = os.environ.get("COUCHBASE_PASSWORD")
    if not (url and user and pwd):
        env_file = os.environ.get("SOURCE_APIS_ENV")
        if not env_file:
            raise RuntimeError(
                "Couchbase credentials not set: export COUCHBASE_URL/COUCHBASE_USER/COUCHBASE_PASSWORD, "
                "or set SOURCE_APIS_ENV to the source database's env file (operator-held, never committed)"
            )
        if not Path(env_file).is_file():
            raise RuntimeError(f"SOURCE_APIS_ENV points to a missing file: {env_file}")
        env = _read_env_file(env_file)
        url = url or env.get("CLUSTER_URL")
        user = user or env.get("CLUSTER_USER")
        pwd = pwd or env.get("SECRET_COUCHBASE_API_PWD")
    if not (url and user and pwd):
        raise RuntimeError("Couchbase credentials not found (set COUCHBASE_URL/USER/PASSWORD)")
    return url, user, pwd


def _query_endpoint(cluster_url: str) -> str:
    raw = cluster_url if "://" in cluster_url else f"couchbase://{cluster_url}"
    host = urlparse(raw).hostname or cluster_url
    host = host.split(",")[0]
    return f"http://{host}:8093/query/service"


class N1QL:
    """Tiny read-only client. Usage: N1QL().query("SELECT ... WHERE x=$x", x=1)."""

    def __init__(self, timeout_s: int = 120) -> None:
        url, user, pwd = _creds()
        self._endpoint = _query_endpoint(url)
        self._auth = (user, pwd)
        self._timeout_s = timeout_s
        self._session = requests.Session()

    def query(self, statement: str, retries: int = 3, **params: Any) -> list[dict[str, Any]]:
        if _MUTATION.match(statement):
            raise ValueError("mutating statements are forbidden (prod Couchbase is read only)")
        body: dict[str, Any] = {
            "statement": statement,
            "readonly": True,
            "timeout": f"{self._timeout_s}s",
            "scan_consistency": "not_bounded",
        }
        for k, v in params.items():
            body[f"${k}"] = v
        last: Exception | None = None
        for attempt in range(retries):
            try:
                r = self._session.post(
                    self._endpoint, json=body, auth=self._auth, timeout=self._timeout_s + 15
                )
                data = r.json()
                if data.get("status") != "success":
                    raise RuntimeError(f"N1QL error: {data.get('errors')}")
                return list(data.get("results", []))
            except (requests.RequestException, ValueError) as e:  # network / bad JSON
                last = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"N1QL request failed after {retries} attempts: {last}")
