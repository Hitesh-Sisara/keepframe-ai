"""Unit tests for the data agent's modules (synthetic data only, LOCAL_MODE, no network)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "services" / "sync"))

EV = "00000000-0000-4000-8000-000000000001"

# Synthetic source settings (keepframe/io/source.py has no defaults).
SRC_ENV = {
    "SOURCE_BUCKET_PROCESSED": "src-processed",
    "SOURCE_BUCKET_ORIGINALS": "src-originals",
    "SOURCE_BUCKET_EXIF_RICH": "src-exif-rich",
    "SOURCE_BUCKET_EXIF_SMALL": "src-exif-small",
    "SOURCE_CDN_PROCESSED": "https://cdn.example.test/",
    "SOURCE_CDN_ORIGINALS": "https://ogcdn.example.test",
    "SOURCE_DB_KEYSPACE": "src_docs",
}


@pytest.fixture(autouse=True)
def _source_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in SRC_ENV.items():
        monkeypatch.setenv(k, v)


def _doc(i: int, **kw: object) -> dict:
    d = {
        "event": EV,
        "album": "11111111-1111-4111-8111-111111111111",
        "name": f"IMG_{i:04d}.JPG",
        "clickedAt": f"2026-02-01T10:00:{i % 60:02d}Z",
        "createdAt": "2026-02-02T00:00:00Z",
        "camera": "Synthetic Cam",
        "lens": "50mm",
        "width": 2560,
        "height": "1707",
        "originalWidth": 6000,
        "originalHeight": 4000,
        "originalImageKb": 12000.5,
        "md5": f"{i:032x}_12345",
        "gcsKey": f"{EV}/p{i}.webp",
        "originalKey": f"{EV}/p{i}.JPG",
        "mfaces": 2,
        "faces": [{"confidence": 99.1, "boundingBox": {"left": 0.1, "top": 0.2, "width": 0.05, "height": 0.07}}],
        "frsInfo": {
            "status": "indexed",
            "sourceImageWidth": 2560,
            "sourceImageHeight": 1707,
            "faces": [{"detScore": 0.91, "minPx": 88, "boundingBox": {"left": 0.1, "top": 0.2, "width": 0.05, "height": 0.07}}],
        },
    }
    d.update(kw)
    return d


@pytest.fixture()
def local(tmp_path, monkeypatch):
    monkeypatch.delenv("WORK_BUCKET", raising=False)
    monkeypatch.setenv("CULL_LOCAL_ROOT", str(tmp_path))
    return tmp_path


def test_s3_local_roundtrip(local):
    from keepframe.io import s3

    assert s3.local_mode()
    s3.put_bytes("raw/e/a.webp", b"x")
    s3.put_json("manifests/e.meta.json", {"a": 1})
    assert s3.exists("raw/e/a.webp") and not s3.exists("raw/e/b.webp")
    assert s3.get_bytes("raw/e/a.webp") == b"x"
    assert s3.get_json("manifests/e.meta.json") == {"a": 1}
    assert list(s3.list_keys("raw/e/")) == ["raw/e/a.webp"]
    assert list(s3.list_keys("manifests/")) == ["manifests/e.meta.json"]
    assert s3.presign("raw/e/a.webp").startswith("file://")


def test_shape_photo_contract_fields():
    from manifest_rows import shape_photo

    from keepframe.io.manifest import PhotoRecord

    row = shape_photo("p1", _doc(1))
    contract = [
        "photo_id", "event_id", "album", "name", "clicked_at", "created_at", "camera", "lens", "width",
        "height", "original_width", "original_height", "original_kb", "md5", "processed_key",
        "original_key", "exif_key", "mfaces", "frs_status", "frs_faces", "frs_src_w", "frs_src_h",
        "rek_faces", "processed_url", "original_url",
    ]
    assert all(k in row for k in contract)
    assert row["height"] == 1707
    assert row["frs_faces"][0] == {"bbox": [0.1, 0.2, 0.05, 0.07], "det_score": 0.91, "min_px": 88.0}
    assert row["rek_faces"][0]["confidence"] == 99.1
    assert row["processed_url"] == f"https://cdn.example.test/{EV}/p1.webp"
    assert row["original_url"] == f"https://ogcdn.example.test/{EV}/p1.JPG"
    assert row["exif_key"] == f"{EV}/p1.exif.json" and row["exif_bucket"] == "src-exif-rich"
    rec = PhotoRecord.model_validate(row)
    assert rec.photo_id == "p1" and rec.frs_faces[0].det_score == 0.91


def test_shape_photo_fallbacks():
    from manifest_rows import shape_photo

    d = _doc(2, gcsKey=None, s3FileName="p2.jpg", originalKey="", frsInfo=None, faces=None, camera="")
    row = shape_photo("p2", d, exif_bucket=None)
    assert row["processed_key"] == f"{EV}/p2.jpg"
    assert row["original_key"] is None and row["original_url"] is None
    assert row["frs_faces"] == [] and row["rek_faces"] == [] and row["camera"] is None
    assert row["exif_key"] is None


@pytest.mark.parametrize(
    ("album", "excluded"),
    [
        ("generated-abc", True), ("ftp-abc", True), ("highlight-abc", True), ("abc-GUESTALBUM", True),
        ("11111111-1111-4111-8111-111111111111", False), (None, False),
    ],
)
def test_album_exclusion(album, excluded):
    from manifest_rows import album_excluded

    assert album_excluded(album) is excluded


def test_album_exclusion_by_name():
    from manifest_rows import album_excluded

    assert album_excluded("uuid", "generated-x")


def test_manifest_roundtrip_and_sort(local):
    from manifest_rows import same_second_bursts, shape_meta, shape_photo, sort_rows

    from keepframe.io import manifest

    rows = sort_rows([shape_photo(f"p{i}", _doc(i, clickedAt="2026-02-01T10:00:00Z" if i < 3 else None))
                      for i in range(5, -1, -1)])
    assert [r["photo_id"] for r in rows][:3] == ["p0", "p1", "p2"]
    assert rows[-1]["clicked_at"] is None
    manifest.write_manifest(EV, rows)
    manifest.write_meta(EV, shape_meta(EV, {"category": "Ceremony", "whitelabelId": "wl"}, rows))
    recs = manifest.read_manifest(EV)
    assert len(recs) == 6 and recs[0].photo_id == "p0"
    meta = manifest.read_meta(EV)
    assert meta.n_photos == 6 and meta.n_with_original == 6 and meta.cameras == ["Synthetic Cam"]
    b = same_second_bursts(rows)
    assert b["n_bursts"] == 1 and b["n_photos_in_bursts"] == 3


def test_sync_jobs():
    from manifest_rows import shape_photo

    from keepframe.io.sync import jobs_for_rows

    rows = [shape_photo("p1", _doc(1)), shape_photo("p2", _doc(2), exif_bucket=None)]
    jobs = jobs_for_rows(rows, {"processed", "exif"})
    dests = sorted(j.dest_key for j in jobs)
    assert dests == [f"exif/{EV}/p1.exif.json", f"raw/{EV}/p1.webp", f"raw/{EV}/p2.webp"]
    orig = jobs_for_rows(rows, {"originals"}, photo_ids={"p2"})
    assert [(j.src_bucket, j.dest_key) for j in orig] == [("src-originals", f"originals/{EV}/p2.JPG")]
    bench = jobs_for_rows(rows[:1], {"processed"}, dest_prefix="bench/5k/")
    assert bench[0].dest_key == f"bench/5k/{EV}/p1.webp"


def test_keys_from_manifest(tmp_path, capsys, monkeypatch):
    from manifest_rows import shape_photo

    import keys_from_manifest

    m = tmp_path / "m.jsonl"
    m.write_text("\n".join(json.dumps(shape_photo(f"p{i}", _doc(i))) for i in range(3)) + "\n")
    monkeypatch.setattr(sys, "argv", ["k", "--manifest", str(m), "--kind", "processed"])
    keys_from_manifest.main()
    assert capsys.readouterr().out.split() == [f"{EV}/p{i}.webp" for i in range(3)]


def test_n1ql_refuses_mutation(monkeypatch):
    monkeypatch.setenv("COUCHBASE_URL", "couchbase://127.0.0.1")
    monkeypatch.setenv("COUCHBASE_USER", "u")
    monkeypatch.setenv("COUCHBASE_PASSWORD", "p")
    from n1ql_ro import N1QL, _query_endpoint

    assert _query_endpoint("couchbases://h1.example,h2") == "http://h1.example:8093/query/service"
    with pytest.raises(ValueError):
        N1QL().query("UPSERT INTO src_docs VALUES ('x', {})")


def test_n1ql_requires_explicit_env_file(monkeypatch, tmp_path):
    """No baked-in env-file default: without COUCHBASE_* the operator must name the file."""
    for k in ("COUCHBASE_URL", "COUCHBASE_USER", "COUCHBASE_PASSWORD", "SOURCE_APIS_ENV"):
        monkeypatch.delenv(k, raising=False)
    from n1ql_ro import _creds

    with pytest.raises(RuntimeError, match="SOURCE_APIS_ENV"):
        _creds()
    monkeypatch.setenv("SOURCE_APIS_ENV", str(tmp_path / "missing.env"))
    with pytest.raises(RuntimeError, match="missing file"):
        _creds()
    env = tmp_path / "ops.env"
    env.write_text('CLUSTER_URL="couchbase://h"\nCLUSTER_USER=u\nSECRET_COUCHBASE_API_PWD=p\n')
    monkeypatch.setenv("SOURCE_APIS_ENV", str(env))
    assert _creds() == ("couchbase://h", "u", "p")


def test_source_settings_have_no_defaults(monkeypatch):
    """Platform names are operator config: unset means a hard error, never a guessed name."""
    from manifest_rows import shape_photo

    from keepframe.io import source
    from keepframe.io.sync import jobs_for_rows

    for k in SRC_ENV:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(source.SourceNotConfigured, match="SOURCE_BUCKET_EXIF_RICH"):
        shape_photo("p1", _doc(1))
    with pytest.raises(source.SourceNotConfigured, match="SOURCE_BUCKET_PROCESSED"):
        jobs_for_rows([{"event_id": EV, "photo_id": "p1", "processed_key": f"{EV}/p1.webp"}], {"processed"})
    monkeypatch.setenv("SOURCE_DB_KEYSPACE", "bad name; DROP")
    with pytest.raises(ValueError):
        source.keyspace()


def test_selection_seed_is_required(monkeypatch):
    import pick_events_n1ql

    monkeypatch.delenv("SOURCE_SELECTION_SEED", raising=False)
    monkeypatch.setattr(sys, "argv", ["pick"])
    with pytest.raises(RuntimeError, match="SOURCE_SELECTION_SEED"):
        pick_events_n1ql.main()


def test_selection_quota_from_config():
    import pick_events_n1ql as pe

    assert [(lbl, n) for lbl, _, n in pe.QUOTA] == [
        ("ceremony", 5), ("engagement", 2), ("birthday", 2), ("corporate", 1), ("other", 2)]
    assert sum(n for _, _, n in pe.QUOTA) == 12
    assert pe.BIG_POOL == {"ceremony": 2000} and pe.DISTINCT == {"other"}
    assert "{ks}" not in pe._q(pe.STATS_Q_T) and "`src_docs` p" in pe._q(pe.STATS_Q_T)
