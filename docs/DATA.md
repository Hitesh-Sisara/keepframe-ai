# DATA: evaluation data, manifests, ingest

In production a photographer uploads an event's photos to `raw/<eventId>/` in the work bucket and the `Ingest`
step builds the manifest from them (§7). The evaluation set below is real event photos, used with permission,
read-only: it was exported once from a source database (Couchbase N1QL with `readonly=true`) and a source
object store (S3-compatible, GET/HEAD/LIST only) with the optional tooling in `scripts/` and `SyncSource`. Real
event/photo ids live only in the gitignored `data/` dir and in the private work bucket. This doc refers to events
as E01..E12.

## 1. Source data model (Couchbase keyspace `$SOURCE_DB_KEYSPACE`, every doc has `type`)

| doc | fields we use |
|---|---|
| `Event` (id = `META().id`) | `category`, `eventDate`, `createdAt`, `whitelabelId`, `sortBy`, `currentPhotosCount`, `isArchived`, `isEventExpired`, `isEventDisabled`, `isPhotographerDeleted`, `facesEnabledAt`, watermark flags |
| `Album` | `event`, `name`; `Photo.album` holds the Album doc id |
| `Photo` | `event, album, name, clickedAt, createdAt, camera, lens, width, height, originalWidth, originalHeight, originalImageKb, md5 ("<hex>_<bytes>"), gcsKey, originalKey, s3FileName, mfaces, faces[], frsInfo{status, faces[]{detScore, boundingBox, minPx}, sourceImageWidth, sourceImageHeight}` |

`exifData` (large JSON string) is never selected.

Source objects: `$SOURCE_BUCKET_PROCESSED/{eventId}/{photoId}.webp` (legacy `.jpg`), long edge 2560;
`$SOURCE_BUCKET_ORIGINALS/{eventId}/{photoId}.{jpg|JPG|jpeg|png|heic}`; EXIF sidecars
`{eventId}/{photoId}.exif.json` in `$SOURCE_BUCKET_EXIF_RICH` (rich) and/or `$SOURCE_BUCKET_EXIF_SMALL` (small).

### Verified facts (2026-10-05, the 12 selected events, 13,443 photos)
- `frsInfo.sourceImageWidth/Height` == processed `width/height` in every row that has them, and
  `minPx == min(bbox.w*srcW, bbox.h*srcH)` exactly. **frsInfo boxes are ratios of the processed image.**
- Rekognition `faces[]` is empty in every row (purged), `mfaces` is null in every row: use `frs_faces`.
- frs_status: `indexed` / `no_faces`. `clickedAt` present in 100 % of rows, ISO UTC, second granularity.
- Processed long edge 2560 px in 13,436/13,443 rows.
- Album id patterns seen across 300 recent events: plain UUID (99.7 %), `highlight-{eventId}` (copies of
  photos already in a real album: same md5), `{eventId}-GUESTALBUM`, `generated-{uuid}` (AI derivatives).
  None of the 12 selected events had excluded rows.

## 2. Queries (all event-scoped, `readonly=true`, `timeout=120s`, `scan_consistency=not_bounded`)

Helper: `scripts/n1ql_ro.py` — reads `COUCHBASE_URL/USER/PASSWORD` or, without echoing, `CLUSTER_URL`,
`CLUSTER_USER`, `SECRET_COUCHBASE_API_PWD` from the source database's env file (operator-held, never committed), named by `$SOURCE_APIS_ENV`; refuses
mutating statements client-side as a second guard.

1. **Event filter** (`idx_event_consolidated`): `type="Event"`, not archived / disabled / expired /
   photographer-deleted, `createdAt >= "2026-01-01"`, `currentPhotosCount BETWEEN 500 AND 5000` → 3,203 events.
2. **Covered pre-screen** (`USE INDEX (idx_photo_event_consolidated_v2)`, no fetch, 25 events per
   `p.event IN $evs`): counts of `originalKey`, `clickedAt`, `camera`, excluded albums → 152 pass
   ≥80 % originals, ≥90 % clickedAt, ≥50 % camera. Originals are the binding constraint (most events are
   processed-only).
3. **Full stats** (`p.event = $ev`, fetch): adds `frsInfo.status`, `mfaces`, `md5`, distinct cameras;
   needs some frsInfo or mfaces. Up to 30 pre-screened events per quota bucket → 67/107 pass.
4. **Selection** (deterministic, sha256 of the original selection seed (`$SOURCE_SELECTION_SEED`, operator-held)): 5 Ceremony (one ≥ 2,000 photos),
   2 Engagement/pre-ceremony, 2 Birthday/Anniversary, 1 Corporate, 2 other (distinct categories).
   Category names are shown generically; the source database's own category strings are kept in config/source_categories.yaml.
5. **Export**: Album docs for the event, then Photo docs by keyset pagination
   `META(p).id > $after ORDER BY META(p).id LIMIT 1000` (no OFFSET). Excluded albums by id prefix/suffix
   and by Album name: `generated-*`, `ftp-*`, `highlight-*`, `*-GUESTALBUM`. Row count is validated against
   `currentPhotosCount − excluded` (±max(5, 1 %)). A LIST of both source EXIF buckets decides `exif_bucket` per row.

```bash
set -a; . ~/.config/keepframe/.env; set +a
export WORK_BUCKET=keepframe-work-<AWS_ACCOUNT_ID>-ap-south-1 PYTHONPATH=$PWD
.venv/bin/python scripts/pick_events_n1ql.py                      # data/candidates.json, data/selected_events.json
.venv/bin/python scripts/export_manifest_n1ql.py --event-id "$EV" --out "s3://$WORK_BUCKET/manifests/" --same-second-bursts
.venv/bin/python -m keepframe.io.sync --event-id "$EV"          # optional source sync: processed + exif, 32 threads
.venv/bin/python -m keepframe.io.sync --event-id "$EV" --kinds originals --photo-ids-file ids.txt
```
`scripts/export_manifest.py` is the Postgres variant (stub until prod cutover; `BEGIN TRANSACTION READ ONLY`,
`statement_timeout=120s`, ROLLBACK), sharing `scripts/manifest_rows.py`.

## 3. Manifest schema (`manifests/<eventId>.jsonl`, sorted by clicked_at then photo_id)

| field | source | notes |
|---|---|---|
| `photo_id` | `META(p).id` | |
| `event_id`, `album`, `name` | `event`, `album`, `name` | |
| `clicked_at`, `created_at` | `clickedAt`, `createdAt` | ISO-8601 UTC |
| `camera`, `lens` | | null when empty |
| `width`, `height` | processed dims | int |
| `original_width`, `original_height`, `original_kb` | `originalWidth/Height`, `originalImageKb` | |
| `md5` | `md5` | `<hex>_<bytes>` |
| `processed_key` | `gcsKey` or `{event}/{s3FileName}` | bucket `$SOURCE_BUCKET_PROCESSED` |
| `original_key` | `originalKey` | null when missing; bucket `$SOURCE_BUCKET_ORIGINALS` |
| `exif_key`, `exif_bucket` | `{event}/{photo}.exif.json` | bucket = rich if present, else small, else both null |
| `mfaces` | `mfaces` | null in all 12 events |
| `frs_status`, `frs_faces[]{bbox,det_score,min_px}`, `frs_src_w`, `frs_src_h` | `frsInfo` | bbox `[l,t,w,h]` ratios of the processed image |
| `rek_faces[]{bbox,confidence}` | `faces[]` | empty in all 12 events |
| `processed_url`, `original_url` | `$SOURCE_CDN_PROCESSED{key}`, `$SOURCE_CDN_ORIGINALS{key}` | |

`.meta.json`: `event_id, name, category, event_date, whitelabel_id, sort_by, n_photos, n_with_original,
cameras, exported_at` + `validation{...}` + `same_second_bursts{...}` + `source`.
Library: `keepframe.io.manifest.read_manifest(event_id) -> list[PhotoRecord]`, `read_meta`, `write_*`.

## 4. EXIF sidecar schemas (copied verbatim to `exif/<eventId>/<photoId>.exif.json`)

**small** — `$SOURCE_BUCKET_EXIF_SMALL`, ~370 B, flat (120/120 sample have every field below):
`DateTimeOriginal` / `DateTime` / `DateTimeDigitized` `"YYYY:MM:DD HH:MM:SS"` (local, no TZ),
`ExposureTime` float seconds (`0.0025`), `ISOSpeedRatings` int, `FNumber` float, `FocalLength` number,
`Flash` int, `Orientation` int, `Make`, `Model`, `LensModel`, plus `Artist`, `Copyright`, `Software`.
**No sub-second field.**

**rich** — `$SOURCE_BUCKET_EXIF_RICH`, ~4.4 KB, nested sections `basicExif`, `exifToolData`,
`cameraInfo`, `processingInfo` (Lightroom edits), `colorProfile`, `extractionInfo`:
| need | key (sample frequency of 120) |
|---|---|
| sub-second capture time | `exifToolData.SubSecCreateDate` `"YYYY:MM:DD HH:MM:SS.sss+HH:MM"` (119) |
| sub-second only | `exifToolData.SubSecTimeOriginal` int (111), `basicExif.SubSecTimeOriginal` str (111) |
| capture time | `exifToolData.CreateDate` (119); `DateTimeOriginal` usually absent |
| exposure | `exifToolData.ExposureTime` / `cameraInfo.ExposureTime` string `"1/160"` (119) |
| ISO | `exifToolData.ISO`, `cameraInfo.ISO` int (119); `basicExif.ISOSpeedRatings` (119) |
| aperture | `exifToolData.FNumber`, `cameraInfo.FNumber` (119) |

Contains serial numbers (`SerialNumber`, `LensSerialNumber`): never log or store them.

Coverage in the selected set: rich sidecar for E03 (663/664), E04 (835/837), E08 (722/722) only; small
sidecar for every photo with an original in the other 9 (none for originals-less rows). So sub-second
ordering exists for 3/12 events; elsewhere order bursts by `clicked_at` second + filename.

## 5. Selected events (ids only in `data/selected_events.json`)

| idx | category | n_photos | n_originals | clickedAt % | camera % | frs % | cameras | rich exif | small exif | same-second bursts / photos | brand | event month |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| E01 | Ceremony | 2442 | 2174 | 100.0 | 100.0 | 100.0 | 1 | 0 | 2174 | 237 / 494 | wl-0 | 2026-05 |
| E02 | Ceremony | 1850 | 1850 | 100.0 | 100.0 | 100.0 | 5 | 0 | 1850 | 2 / 4 | brand | 2026-04 |
| E03 | Ceremony | 664 | 664 | 100.0 | 100.0 | 100.0 | 2 | 663 | 495 | 3 / 7 | wl-0 | 2026-02 |
| E04 | Ceremony | 837 | 837 | 100.0 | 99.8 | 100.0 | 2 | 835 | 808 | 3 / 6 | wl-0 | 2026-03 |
| E05 | Ceremony | 1211 | 1211 | 100.0 | 99.2 | 100.0 | 1 | 0 | 1211 | 244 / 730 | wl-0 | 2026-04 |
| E06 | Engagement | 1841 | 1841 | 100.0 | 100.0 | 100.0 | 1 | 0 | 1841 | 464 / 1103 | wl-0 | 2026-06 |
| E07 | Engagement | 1000 | 1000 | 100.0 | 100.0 | 100.0 | 1 | 0 | 1000 | 179 / 655 | wl-0 | 2026-06 |
| E08 | Anniversary | 722 | 722 | 100.0 | 100.0 | 100.0 | 1 | 722 | 722 | 11 / 22 | wl-0 | 2026-03 |
| E09 | Birthday | 661 | 661 | 100.0 | 100.0 | 100.0 | 2 | 0 | 661 | 57 / 114 | brand | 2026-07 |
| E10 | Corporate | 625 | 625 | 100.0 | 100.0 | 100.0 | 1 | 0 | 625 | 38 / 78 | wl-0 | 2026-06 |
| E11 | Themed Photoshoot | 877 | 877 | 100.0 | 100.0 | 100.0 | 2 | 0 | 877 | 335 / 873 | brand | 2026-04 |
| E12 | Maternity Photoshoot | 713 | 713 | 100.0 | 100.0 | 100.0 | 1 | 0 | 713 | 6 / 12 | wl-0 | 2026-04 |

Total 13,443 photos, 13,175 originals, 11 distinct camera bodies. Deviation from the design: 12 events
(not 10–20 with 6/3/3/2) per the data brief; only 2 Birthday-class events in the bucket quota; E01 has
89 % originals (passes ≥80 %). Bench set: `bench/manifest_5k.jsonl` = E01, E02, E05, E06, E07 × 1,000.

## 6. Work bucket and the optional source sync

`s3://keepframe-work-<AWS_ACCOUNT_ID>-ap-south-1` (created by the data agent; infra adopts it in CDK):
BucketOwnerEnforced, Block Public Access ALL, SSE-S3 + bucket key, TLS-only policy, no versioning,
lifecycle expires `raw/`, `originals/`, `proxy/` after 7 days, aborts incomplete MPUs after 1 day.

Sync paths: `services/sync/entrypoint.sh` (rclone v1.75.1 in `Dockerfile.sync`, arm64, env-config,
`--files-from` lists from `services/sync/keys_from_manifest.py`; `SYNC_KINDS=processed,exif`
or `originals` with `PHOTO_IDS_S3`) and the pure-python fallback `python -m keepframe.io.sync`
(boto3, source GET streamed into S3 `upload_fileobj`, 32 threads, skip-if-same-size, resumable).

Measured from the x86 dev box (not AWS): source GET alone 31–37 MB/s at 32–64 threads; source→S3 end to end
1.7–11.9 MB/s (13–33 obj/s) — bound by the S3 PUT leg (~0.6 s TTFB to ap-south-1 from this box).
Expect much higher from a Fargate/c8g task in ap-south-1 (not yet measured).

### S3 inventory after the 2026-10-05 run (data-agent prefixes)

| prefix | objects | GB |
|---|---|---|
| `manifests/` | 24 (12 × jsonl + meta) | 0.020 |
| `raw/` | 13,443 (= every manifest row, verified per event) | 4.763 |
| `exif/` | 13,175 (= every row with `exif_bucket`, verified per event) | 0.014 |
| `originals/` | 20 (E09 rclone container test of the second pass) | 0.226 |
| `bench/5k/` + `bench/manifest_5k.jsonl` | 5,000 + 1 | 1.822 |

Per-event python sync (processed + exif, 0 failed, 0 missing): 0.17–1.02 GB per event,
1.7–9.1 MB/s, 13–56 obj/s; small-object events are PUT-latency bound. rclone image (QEMU arm64) copied
20 originals (226 MB) in 29 s including image start.

## 7. Ingest: photographer upload (the default path)

1. The photographer uploads the event's photos to `s3://<work bucket>/raw/<eventId>/<photoId>.<ext>` (JPEG, PNG or
   WebP). EXIF sidecars under `exif/<eventId>/<photoId>.exif.json` are optional.
2. `CullEvent` starts with `Ingest` (Lambda arm64). If `manifests/<eventId>.jsonl` is missing it lists
   `raw/<eventId>/` and writes one row per photo plus `.meta.json`. A manifest uploaded with the photos (schema in §3) is used as is.
3. The optional source sync (`SyncSource`, `sync_source: true` in the execution input) is only for importing an
   event from another S3-compatible store: `SOURCE_S3_ENDPOINT_URL` (empty = AWS S3), `SOURCE_REGION`,
   `SOURCE_BUCKET_*`, credentials `SOURCE_ACCESS_KEY_ID` / `SOURCE_SECRET_ACCESS_KEY` in Secrets Manager
   `keepframe/source` (`config/source.example.env`).
