"""Client-facing aliases for source ids (security review S-1).

Real source event and photo ids are UUIDs, and a public CDN of the source can serve `<eventId>/<photoId>.webp`
without auth, so a real id pair is an image link. Nothing the API returns may carry one. Aliasing is
presentation only: storage (S3 keys, DynamoDB items, traces) keeps the real ids, and the worker and agent
never see an alias.

    event  ev_NN              1-based manifest export order (the same N as the "Event N" label)
    photo  ph_<10 hex>        HMAC-SHA256(key, "<eventId>/<photoId>"), stable for a key; event-scoped
    group  g_<ev alias>_<n>   for worker ids g_<eventId>_<n>; any other group id gets gx_<10 hex>

The scrubber walks every JSON response (see main.alias_responses): a string that IS a known id becomes its
alias, known ids inside longer strings (S3 keys, reason codes, error text) are replaced in place, and any
UUID-shaped string still left is redacted to `<id>`. Inbound aliases are translated back by `resolve_*`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException

from .aws import client
from .config import settings

log = logging.getLogger(__name__)

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
REDACTED = "<id>"
# Storage keys are never echoed to an aliased client (S-1 item 4); the API hands out /img links instead.
DROP_FIELDS = frozenset({"contact_sheet_key", "results_run_prefix", "results_prefix", "execution_arn"})
EVENT_ALIAS_RE = re.compile(r"^ev_(\d{2,4})$")
PHOTO_ALIAS_RE = re.compile(r"^ph_[0-9a-f]{10}$")
GROUP_ALIAS_RE = re.compile(r"^g_ev_(\d{2,4})_(\d{1,6})$")
GROUPX_ALIAS_RE = re.compile(r"^gx_[0-9a-f]{10}$")
_WORKER_GROUP_RE = re.compile(r"^g_(?P<ev>[0-9a-fA-F-]{36})_(?P<n>\d{1,6})$")
PHOTO_HEX = 10  # 40 bits: ~2.7e-6 collision odds for a 2,442-photo event (6 hex would be ~18%)
KEY_TTL_S = 300.0


def event_alias(index: int) -> str:
    return f"ev_{index:02d}"


# ---------------------------------------------------------------- key


class _Key:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value: bytes | None = None
        self._at = 0.0

    def clear(self) -> None:
        with self._lock:
            self._value, self._at = None, 0.0

    def get(self) -> bytes:
        with self._lock:
            if self._value is not None and time.monotonic() - self._at < KEY_TTL_S:
                return self._value
        s = settings()
        raw = s.alias_key_override
        if not raw and s.local_mode:
            raw = "local-alias-key-not-a-secret"
        if not raw:
            try:
                raw = str(
                    client("secretsmanager").get_secret_value(SecretId=s.alias_secret_id)["SecretString"]
                )
            except Exception as exc:  # noqa: BLE001 - fail closed, below
                log.error(json.dumps({"msg": "alias key unavailable", "error": type(exc).__name__}))
                raise HTTPException(status_code=503, detail="Alias key unavailable") from exc
            try:
                doc = json.loads(raw)
                if isinstance(doc, dict) and isinstance(doc.get("key"), str):
                    raw = doc["key"]
            except ValueError:
                pass
        if len(raw) < 16:
            raise HTTPException(status_code=503, detail="Alias key unavailable")
        value = raw.encode()
        with self._lock:
            self._value, self._at = value, time.monotonic()
        return value


alias_key = _Key()


def _mac(*parts: str) -> str:
    return hmac.new(alias_key.get(), "/".join(parts).encode(), hashlib.sha256).hexdigest()


def photo_alias(event_id: str, photo_id: str) -> str:
    return f"ph_{_mac('photo', event_id, photo_id)[:PHOTO_HEX]}"


def other_group_alias(event_id: str, group_id: str) -> str:
    return f"gx_{_mac('group', event_id, group_id)[:PHOTO_HEX]}"


# ---------------------------------------------------------------- per-event maps


_version = [0]  # bumped whenever an id → alias mapping is added; invalidates the cached Scrubber


@dataclass
class EventMap:
    event_id: str
    alias: str
    photo_to_alias: dict[str, str] = field(default_factory=dict)
    alias_to_photo: dict[str, str] = field(default_factory=dict)
    group_to_alias: dict[str, str] = field(default_factory=dict)
    alias_to_group: dict[str, str] = field(default_factory=dict)

    def add_photo(self, photo_id: str) -> str:
        a = self.photo_to_alias.get(photo_id)
        if a is None:
            a = photo_alias(self.event_id, photo_id)
            self.photo_to_alias[photo_id] = a
            self.alias_to_photo.setdefault(a, photo_id)
            _version[0] += 1
        return a

    def add_group(self, group_id: str) -> str:
        a = self.group_to_alias.get(group_id)
        if a is None:
            m = _WORKER_GROUP_RE.match(group_id)
            if m and m.group("ev").lower() == self.event_id.lower():
                a = f"g_{self.alias}_{int(m.group('n'))}"
            else:
                a = other_group_alias(self.event_id, group_id)
            self.group_to_alias[group_id] = a
            self.alias_to_group.setdefault(a, group_id)
            _version[0] += 1
        return a


class Registry:
    """Event ids → aliases from the manifest index; photo/group maps filled as responses are built (every id
    the API can return comes from storage it reads first) and from the manifest on an inbound miss."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, EventMap] = {}
        self._by_alias: dict[str, str] = {}
        self._loaded: set[str] = set()

    def clear(self) -> None:
        with self._lock:
            _version[0] += 1
            self._events.clear()
            self._by_alias.clear()
            self._loaded.clear()

    def sync_events(self, event_ids: list[str]) -> None:
        with self._lock:
            for i, eid in enumerate(event_ids, start=1):
                alias = event_alias(i)
                current = self._events.get(eid)
                if current is None or current.alias != alias:
                    self._events[eid] = EventMap(eid, alias)
                    _version[0] += 1
                self._by_alias[alias] = eid

    def event(self, event_id: str) -> EventMap | None:
        with self._lock:
            return self._events.get(event_id)

    def events(self) -> list[EventMap]:
        with self._lock:
            return list(self._events.values())

    def event_for_alias(self, alias: str) -> str | None:
        with self._lock:
            return self._by_alias.get(alias)

    def mark_loaded(self, event_id: str) -> bool:
        """True the first time; the manifest is read at most once per event per container."""
        with self._lock:
            if event_id in self._loaded:
                return False
            self._loaded.add(event_id)
            return True


registry = Registry()


def ensure_events() -> None:
    from . import data  # local import: data imports nothing from here

    registry.sync_events(data.event_ids())


def event_map(event_id: str) -> EventMap | None:
    ensure_events()
    return registry.event(event_id)


def note_photos(event_id: str, photo_ids: list[str] | tuple[str, ...] | set[str]) -> None:
    em = event_map(event_id)
    if em is not None:
        for pid in photo_ids:
            if isinstance(pid, str) and pid:
                em.add_photo(pid)


def note_group(event_id: str, group_id: str) -> str | None:
    em = event_map(event_id)
    return em.add_group(group_id) if em is not None else None


def preload_event(event_id: str) -> None:
    """Learn every photo/group id of an event (manifest, groups.json, decisions) once per container."""
    if event_map(event_id) is not None:
        _load_manifest_photos(event_id)


def _load_manifest_photos(event_id: str) -> None:
    """Fill the photo map from manifests/<eventId>.jsonl once, so an inbound ph_ alias resolves even before any
    response for that event was built in this container."""
    from . import data

    if not registry.mark_loaded(event_id):
        return
    rows = data.read_jsonl(f"manifests/{event_id}.jsonl") or []
    note_photos(event_id, [str(r["photo_id"]) for r in rows if isinstance(r.get("photo_id"), str)])
    found = data.groups_from_s3(event_id)
    if found:
        for g in found[0]:
            note_group(event_id, g["group_id"])
            note_photos(event_id, g.get("members") or [])
        for p in data.decisions_from_s3(event_id):
            note_photos(event_id, [p])


# ---------------------------------------------------------------- inbound


def resolve_event(value: str, *, allow_raw: bool) -> str:
    """ev_NN → real event id. A raw id is accepted only for the team token (ops)."""
    ensure_events()
    real = registry.event_for_alias(value)
    if real is not None:
        return real
    if allow_raw and registry.event(value) is not None:
        return value
    raise HTTPException(status_code=404, detail="Unknown event")


def resolve_photo(event_id: str, value: str, *, allow_raw: bool) -> str:
    if PHOTO_ALIAS_RE.match(value):
        em = event_map(event_id)
        if em is not None:
            real = em.alias_to_photo.get(value)
            if real is None:
                _load_manifest_photos(event_id)
                real = em.alias_to_photo.get(value)
            if real is not None:
                return real
        raise HTTPException(status_code=404, detail="Unknown photo")
    if allow_raw:
        return value
    raise HTTPException(status_code=404, detail="Unknown photo")


def resolve_group(event_id: str, value: str, *, allow_raw: bool) -> str:
    em = event_map(event_id)
    if em is None:
        raise HTTPException(status_code=404, detail="Unknown group")
    m = GROUP_ALIAS_RE.match(value)
    if m:
        if f"ev_{m.group(1)}" != em.alias:
            raise HTTPException(status_code=404, detail="Unknown group")
        real = em.alias_to_group.get(value) or f"g_{event_id}_{int(m.group(2))}"
        return real
    if GROUPX_ALIAS_RE.match(value):
        real = em.alias_to_group.get(value)
        if real is None:
            _load_manifest_photos(event_id)
            real = em.alias_to_group.get(value)
        if real is not None:
            return real
        raise HTTPException(status_code=404, detail="Unknown group")
    if allow_raw:
        return value
    raise HTTPException(status_code=404, detail="Unknown group")


_EV_IN_ID = re.compile(r"ev_(\d{2,4})")


def resolve_run(value: str) -> str:
    """A run id as the client saw it. Worker/CLI ids may embed the event id (run_<eventId>_<ts>); the scrubber
    shows those as run_ev_NN_<ts>, so map any ev_NN segment back to the real id. ULID ids pass through."""
    if "ev_" not in value:
        return value
    ensure_events()

    def back(m: re.Match[str]) -> str:
        return registry.event_for_alias(m.group(0)) or m.group(0)

    return _EV_IN_ID.sub(back, value)


def resolve_approval(value: str, *, allow_raw: bool) -> str | tuple[str, str]:
    """An approval id is the group/photo id it is about. Returns the raw id, or (event_id, real id) for an
    alias, so the caller can match pending items without trusting a client-sent raw id."""
    m = GROUP_ALIAS_RE.match(value)
    if m:
        ensure_events()
        eid = registry.event_for_alias(f"ev_{m.group(1)}")
        if eid is None:
            raise HTTPException(status_code=404, detail="Unknown approval")
        return eid, resolve_group(eid, value, allow_raw=False)
    if PHOTO_ALIAS_RE.match(value) or GROUPX_ALIAS_RE.match(value):
        ensure_events()
        for em in registry.events():
            real = em.alias_to_photo.get(value) or em.alias_to_group.get(value)
            if real is not None:
                return em.event_id, real
        raise HTTPException(status_code=404, detail="Unknown approval")
    if allow_raw or not UUID_RE.search(value):
        return value  # synthetic/short approval ids carry no customer id
    raise HTTPException(status_code=404, detail="Unknown approval")


# ---------------------------------------------------------------- outbound scrub


class Scrubber:
    """One pass per string: an exact known id (any shape) → its alias; otherwise every UUID-shaped run → the
    alias of that event or photo, or `<id>`. Worker group ids g_<eventId>_<n> become g_ev_NN_<n>, the same
    as their alias. Lookups are dict hits on a per-request snapshot; nothing is recompiled."""

    def __init__(self) -> None:
        self.exact: dict[str, str] = {}
        self.uuids: dict[str, str] = {}
        for em in registry.events():
            for real, alias in (
                (em.event_id, em.alias),
                *em.photo_to_alias.items(),
                *em.group_to_alias.items(),
            ):
                self.exact[real] = alias
                if UUID_RE.fullmatch(real):
                    self.uuids[real.lower()] = alias

    def _uuid(self, m: re.Match[str]) -> str:
        return self.uuids.get(m.group(0).lower(), REDACTED)

    def text(self, s: str) -> str:
        hit = self.exact.get(s)
        if hit is not None:
            return hit
        return UUID_RE.sub(self._uuid, s) if "-" in s else s

    def value(self, v: Any) -> Any:
        if isinstance(v, str):
            return self.text(v)
        if isinstance(v, list):
            return [self.value(x) for x in v]
        if isinstance(v, dict):
            return {
                self.text(k) if isinstance(k, str) else k: self.value(x)
                for k, x in v.items()
                if k not in DROP_FIELDS
            }
        return v


_scrubber: list[Any] = [-1, None]  # (version, Scrubber)
_scrubber_lock = threading.Lock()


def scrubber() -> Scrubber:
    with _scrubber_lock:
        if _scrubber[0] != _version[0] or _scrubber[1] is None:
            v = _version[0]
            _scrubber[1] = Scrubber()
            _scrubber[0] = v
        return _scrubber[1]


def scrub(doc: Any) -> Any:
    ensure_events()
    return scrubber().value(doc)


def contains_uuid(doc: Any) -> bool:
    return bool(UUID_RE.search(json.dumps(doc)))
