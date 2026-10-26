"""Bearer-token auth: a read-only judge token and a team token.

Token values live in Secrets Manager (`keepframe/judge-token`, `keepframe/team-token`) and are cached
for five minutes. `CULL_JUDGE_TOKEN` / `CULL_TEAM_TOKEN` override them for local development only.
A secret may be a plain string or JSON `{"token": "...", "previous": "..."}`; `previous` keeps the old
value valid during a rotation window.
"""

from __future__ import annotations

import hmac
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, HTTPException, Request, status

from .aws import client
from .config import settings

log = logging.getLogger(__name__)

Role = Literal["judge", "team"]
CACHE_TTL_S = 300
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class Principal:
    role: Role

    @property
    def can_mutate(self) -> bool:
        return self.role == "team"


class _TokenCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[Role, tuple[str, ...]] = {}
        self._loaded_at = 0.0

    def clear(self) -> None:
        with self._lock:
            self._values = {}
            self._loaded_at = 0.0

    def tokens(self) -> dict[Role, tuple[str, ...]]:
        with self._lock:
            if self._values and time.monotonic() - self._loaded_at < CACHE_TTL_S:
                return self._values
            self._values = {
                "team": self._load(settings().team_token_override, settings().team_secret_id),
                "judge": self._load(settings().judge_token_override, settings().judge_secret_id),
            }
            self._loaded_at = time.monotonic()
            return self._values

    @staticmethod
    def _load(override: str, secret_id: str) -> tuple[str, ...]:
        if override:
            return (override,)
        try:
            raw = client("secretsmanager").get_secret_value(SecretId=secret_id)["SecretString"]
        except Exception as exc:  # fail closed: no token means nobody gets in with that role
            log.error("token secret unavailable", extra={"secret_id": secret_id, "error": type(exc).__name__})
            return ()
        return _parse_secret(raw)


def _parse_secret(raw: str) -> tuple[str, ...]:
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            return ()
        values = [doc.get("token"), doc.get("previous"), *(doc.get("tokens") or [])]
        return tuple(v for v in values if isinstance(v, str) and len(v) >= 16)
    return (raw,) if len(raw) >= 16 else ()


token_cache = _TokenCache()


def _match(presented: str) -> Role | None:
    tokens = token_cache.tokens()
    found: Role | None = None
    # Compare against every candidate so timing does not reveal which role matched.
    for role in ("team", "judge"):
        for candidate in tokens.get(role, ()):  # type: ignore[call-overload]
            if hmac.compare_digest(presented.encode(), candidate.encode()) and found is None:
                found = role  # type: ignore[assignment]
    return found


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def authenticate(request: Request) -> Principal:
    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not presented.strip():
        raise _unauthorized()
    role = _match(presented.strip())
    if role is None:
        raise _unauthorized()
    principal = Principal(role=role)
    request.state.role = role  # read by the response alias middleware (judge responses are always aliased)
    # Defence in depth: the judge token can never mutate, whatever the route declares.
    if request.method.upper() not in READ_METHODS and not principal.can_mutate:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Judge token is read-only")
    return principal


def require_reader(principal: Principal = Depends(authenticate)) -> Principal:
    return principal


def require_team(principal: Principal = Depends(authenticate)) -> Principal:
    if principal.role != "team":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Team token required")
    return principal
