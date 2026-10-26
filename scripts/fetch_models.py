#!/usr/bin/env python3
"""Download the model files listed in config/models.lock and verify their SHA-256.

Lock format: one model per line, ``name<TAB>url<TAB>sha256<TAB>licence``; ``#`` lines are comments.
Idempotent: a file already present with the right hash is not downloaded again. A hash mismatch
is fatal (the partial/corrupt file is removed). Stdlib only so it runs in a bare build stage.

    python scripts/fetch_models.py                      # -> ./models
    python scripts/fetch_models.py --lock config/models.lock --out /opt/models
    python scripts/fetch_models.py --check              # verify only, no network
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class LockEntry:
    name: str
    url: str
    sha256: str
    licence: str


def parse_lock(path: Path) -> list[LockEntry]:
    entries: list[LockEntry] = []
    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = raw.rstrip("\n").split("\t")
        if len(parts) != 4:
            raise ValueError(f"{path}:{lineno}: expected 4 tab-separated fields, got {len(parts)}")
        name, url, sha, lic = (p.strip() for p in parts)
        if "/" in name or name.startswith("."):
            raise ValueError(f"{path}:{lineno}: bad file name {name!r}")
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError(f"{path}:{lineno}: bad sha256 for {name}")
        entries.append(LockEntry(name, url, sha, lic))
    return entries


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, retries: int = 3) -> None:
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-https url {url!r}")
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "keepframe-fetch-models/1"})
            with urllib.request.urlopen(req, timeout=120) as r, os.fdopen(fd, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            os.chmod(tmp, 0o644)  # mkstemp is 0600; runtime user is non-root
            os.replace(tmp, dest)
            return
        except Exception as e:  # noqa: BLE001 - retried, then re-raised
            last = e
            Path(tmp).unlink(missing_ok=True)
            time.sleep(2 * attempt)
    raise RuntimeError(f"download failed after {retries} attempts: {url}: {last}")


def fetch(entries: list[LockEntry], out: Path, check_only: bool = False) -> int:
    out.mkdir(parents=True, exist_ok=True)
    bad = 0
    for e in entries:
        dest = out / e.name
        if dest.exists() and sha256_of(dest) == e.sha256:
            print(f"ok      {e.name}")
            continue
        if check_only:
            print(f"MISSING {e.name}" if not dest.exists() else f"BADHASH {e.name}", file=sys.stderr)
            bad += 1
            continue
        if not e.url.startswith("https://"):
            print(f"SKIP    {e.name}: no published url ({e.url})", file=sys.stderr)
            bad += 1
            continue
        print(f"fetch   {e.name} <- {e.url}")
        _download(e.url, dest)
        got = sha256_of(dest)
        if got != e.sha256:
            dest.unlink(missing_ok=True)
            print(f"BADHASH {e.name}: expected {e.sha256} got {got}", file=sys.stderr)
            bad += 1
            continue
        print(f"ok      {e.name}")
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lock", type=Path, default=REPO / "config" / "models.lock")
    ap.add_argument("--out", type=Path, default=REPO / "models")
    ap.add_argument("--check", action="store_true", help="verify existing files only; no downloads")
    a = ap.parse_args(argv)
    bad = fetch(parse_lock(a.lock), a.out, check_only=a.check)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
