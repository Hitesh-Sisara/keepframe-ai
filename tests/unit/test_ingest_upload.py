"""Ingest step: a photographer's upload in raw/<eventId>/ becomes a manifest + EXIF sidecars (LOCAL_MODE)."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from keepframe.io import ingest_upload as iu
from keepframe.io.exif import parse_exif


def _ifd(entries: list[tuple[int, int, int, bytes]], base: int, next_off: int = 0) -> tuple[bytes, bytes]:
    """Little-endian IFD at offset ``base``; values > 4 bytes go to a data area right after it."""
    head = struct.pack("<H", len(entries))
    data = b""
    data_off = base + 2 + 12 * len(entries) + 4
    for tag, typ, count, val in entries:
        if len(val) <= 4:
            head += struct.pack("<HHI", tag, typ, count) + val.ljust(4, b"\0")
        else:
            head += struct.pack("<HHII", tag, typ, count, data_off + len(data))
            data += val
    return head + struct.pack("<I", next_off), data


def jpeg_with_exif(dt: str = "2026:03:14 10:20:30", subsec: str = "25", offset: str = "+05:30",
                   model: str = "Cam X1") -> bytes:
    asc = lambda s: s.encode() + b"\0"  # noqa: E731
    exif_entries = [
        (0x829A, 5, 1, struct.pack("<II", 1, 250)),  # ExposureTime 1/250
        (0x8827, 3, 1, struct.pack("<H", 400)),     # ISO
        (0x9003, 2, len(asc(dt)), asc(dt)),
        (0x9011, 2, len(asc(offset)), asc(offset)),
        (0x9291, 2, len(asc(subsec)), asc(subsec)),
    ]
    # IFD0 at 8: Model + Exif pointer (patched below)
    ifd0_entries = [(0x0110, 2, len(asc(model)), asc(model)), (0x8769, 4, 1, struct.pack("<I", 0))]
    ifd0, ifd0_data = _ifd(ifd0_entries, 8)
    exif_off = 8 + len(ifd0) + len(ifd0_data)
    ifd0, ifd0_data = _ifd([ifd0_entries[0], (0x8769, 4, 1, struct.pack("<I", exif_off))], 8)
    exif_ifd, exif_data = _ifd(exif_entries, exif_off)
    tiff = b"II*\0" + struct.pack("<I", 8) + ifd0 + ifd0_data + exif_ifd + exif_data
    app1 = b"Exif\0\0" + tiff
    return b"\xff\xd8\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + b"\xff\xd9"


@pytest.fixture()
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("WORK_BUCKET", raising=False)
    monkeypatch.setenv("CULL_LOCAL_ROOT", str(tmp_path))
    return tmp_path


def test_parse_jpeg_exif_reads_capture_fields() -> None:
    ex = iu.parse_jpeg_exif(jpeg_with_exif())
    assert ex["Model"] == "Cam X1" and ex["ISOSpeedRatings"] == 400
    assert ex["ExposureTime"] == pytest.approx(0.004)
    assert ex["DateTimeOriginal"] == "2026:03:14 10:20:30" and ex["SubSecTimeOriginal"] == "25"
    info = parse_exif(ex)  # the sidecar is readable by the worker's EXIF reader
    assert info.iso == 400 and info.subsec == pytest.approx(0.25) and info.tz_known
    assert iu._capture_iso(ex) == "2026-03-14T04:50:30.250Z"
    assert iu.parse_jpeg_exif(b"not a jpeg") == {}
    assert iu.parse_jpeg_exif(b"\xff\xd8\xff\xd9") == {}


def test_ingest_builds_manifest_and_sidecars(root: Path) -> None:
    raw = root / "raw" / "ev1"
    raw.mkdir(parents=True)
    (raw / "B.jpg").write_bytes(jpeg_with_exif(dt="2026:03:14 10:20:31", subsec="0"))
    (raw / "A.jpg").write_bytes(jpeg_with_exif(dt="2026:03:14 10:20:30"))
    (raw / "no exif.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (raw / "notes.txt").write_text("ignored")

    out = iu.ingest_upload("ev1")
    assert out["status"] == "ingested" and out["n_photos"] == 3 and out["with_capture_time"] == 2
    assert out["renamed"] == 1
    rows = [json.loads(x) for x in (root / "manifests/ev1.jsonl").read_text().splitlines()]
    assert [r["photo_id"] for r in rows][:2] == ["A", "B"]  # capture order, undated last
    odd = rows[2]
    assert odd["photo_id"].startswith("no_exif_") and odd["clicked_at"] is None and odd["exif_key"] is None
    assert (root / "raw/ev1" / f"{odd['photo_id']}.png").exists() and (raw / "no exif.png").exists()
    assert rows[0]["processed_key"] == "ev1/A.jpg" and rows[0]["camera"] == "Cam X1"
    side = json.loads((root / "exif/ev1/A.exif.json").read_text())
    assert side["ISOSpeedRatings"] == 400
    meta = json.loads((root / "manifests/ev1.meta.json").read_text())
    assert meta["n_photos"] == 3 and meta["source"] == "upload" and meta["cameras"] == ["Cam X1"]

    # second run: the manifest exists, nothing changes unless forced
    assert iu.ingest_upload("ev1")["status"] == "manifest_present"
    assert iu.ingest_upload("ev1", force=True)["n_photos"] == 3


def test_ingest_errors(root: Path) -> None:
    with pytest.raises(FileNotFoundError, match="raw/ev_empty/"):
        iu.ingest_upload("ev_empty")
    with pytest.raises(ValueError):
        iu.ingest_upload("../x")


def test_safe_id_unique() -> None:
    taken: set[str] = set()
    assert iu.safe_id("p_1", taken) == "p_1"
    a, b = iu.safe_id("a b", taken), iu.safe_id("a b", taken)
    assert a != b and a.startswith("a_b_") and b == a + "_2"
