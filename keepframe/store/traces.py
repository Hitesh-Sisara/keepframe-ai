"""Trace emission, one JSON line per step, exactly docs/AGENT.md "Trace schema" shape.

Every line goes to three sinks:
  1. DynamoDB ``keepframe-traces``: pk ``RUN#<runId>``, sk = seq zero-padded to 6, ttl = now + 90 d
  2. ``results/<eventId>/<runId>/trace.jsonl`` in the work bucket (or LOCAL_MODE root), this run's lines only,
     plus the unversioned latest copy ``results/<eventId>/trace.jsonl`` (all runs appended; kept for one release)
  3. stdout as one structured log line (CloudWatch Logs; same JSON, plus ``"log": "trace"``)

``tool_result`` lines carry ``opencv`` (cv2.__version__), ``cv2_file`` and ``latency_ms``.
Sequence numbers continue across a pause/resume: the writer resumes from the highest stored seq.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import UTC, datetime
from typing import Any

import cv2

from keepframe.store import dynamodb

TTL_DAYS = 90
TRACE_TYPES = {"run_header", "tool_call", "tool_result", "model_reasoning", "interrupt", "interrupt_resumed",
               "decision", "error", "run_end", "escalation"}


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _jsonable(v: Any) -> Any:
    return json.loads(json.dumps(v, default=str))


class TraceWriter:
    """Thread-safe, monotonic seq. ``sinks`` subset of {"ddb", "s3", "stdout"} (env CULL_TRACE_SINKS)."""

    def __init__(self, run_id: str, event_id: str, sinks: set[str] | None = None, start_seq: int | None = None,
                 s3_flush_every: int = 25) -> None:
        self.run_id = run_id
        self.event_id = event_id
        env = os.environ.get("CULL_TRACE_SINKS")
        self.sinks = sinks if sinks is not None else (set(env.split(",")) if env else {"ddb", "s3", "stdout"})
        self._lock = threading.Lock()
        self.lines: list[dict[str, Any]] = []
        self._pending_s3: list[str] = []
        self._s3_flush_every = s3_flush_every
        from keepframe.worker.run_record import result_keys

        self._s3_keys = result_keys(event_id, run_id, "trace.jsonl")
        self._s3_existing: dict[str, bytes] = {}
        if start_seq is None:
            start_seq = self._last_seq() + 1
        self.seq = start_seq

    def _last_seq(self) -> int:
        if "ddb" not in self.sinks:
            return -1
        try:
            rows = dynamodb.query_trace(self.run_id)
        except Exception:  # noqa: BLE001 - a fresh table/backend has nothing yet
            return -1
        return max((int(r["seq"]) for r in rows), default=-1)

    def emit(self, type_: str, **fields: Any) -> dict[str, Any]:
        if type_ not in TRACE_TYPES:
            raise ValueError(f"unknown trace type {type_!r}")
        with self._lock:
            line: dict[str, Any] = {"run_id": self.run_id, "seq": self.seq, "ts": now_iso(), "type": type_}
            for k, v in fields.items():
                if v is not None:
                    line[k] = _jsonable(v)
            if type_ == "tool_result":
                line.setdefault("opencv", cv2.__version__)
                line.setdefault("cv2_file", cv2.__file__)
            self.seq += 1
            self.lines.append(line)
            if "ddb" in self.sinks:
                item = {"pk": f"RUN#{self.run_id}", "sk": f"{line['seq']:06d}",
                        "ttl": int(time.time()) + TTL_DAYS * 86400, "event_id": self.event_id, **line}
                dynamodb.put_trace(item)
            if "stdout" in self.sinks:
                sys.stdout.write(json.dumps({"log": "trace", **line}, separators=(",", ":")) + "\n")
                sys.stdout.flush()
            if "s3" in self.sinks:
                self._pending_s3.append(json.dumps(line, separators=(",", ":")) + "\n")
                if len(self._pending_s3) >= self._s3_flush_every or type_ in ("interrupt", "run_end", "error"):
                    self._flush_s3()
            return line

    def _flush_s3(self) -> None:
        """S3 has no append: keep each object's prior bytes once, rewrite the whole object on each flush.

        The per-run key's prior bytes are this run's lines from before a pause/resume; the latest copy also
        holds earlier runs' lines."""
        if not self._pending_s3:
            return
        from keepframe.io import proxies

        store = proxies.get_store()
        chunk = "".join(self._pending_s3).encode()
        self._pending_s3.clear()
        for key in self._s3_keys:
            if key not in self._s3_existing:
                try:
                    self._s3_existing[key] = store.get_bytes(key) if store.exists(key) else b""
                except Exception:  # noqa: BLE001
                    self._s3_existing[key] = b""
            self._s3_existing[key] += chunk
            store.put_bytes(key, self._s3_existing[key], "application/x-ndjson")

    def flush(self) -> None:
        with self._lock:
            if "s3" in self.sinks:
                self._flush_s3()
