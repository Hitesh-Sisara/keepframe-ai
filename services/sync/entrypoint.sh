#!/usr/bin/env bash
# SyncSource (optional): import one event from any S3-compatible store into the S3 work bucket, READ ONLY on
# the source. The default path needs no sync: photographers upload to s3://$WORK_BUCKET/raw/<eventId>/.
#
# Env (NAMES only; values come from Secrets Manager keepframe/source + the task role):
#   SOURCE_S3_ENDPOINT_URL   empty = AWS S3 (then the task role's credentials are used if no key is set)
#   SOURCE_REGION            default AWS_REGION
#   SOURCE_ACCESS_KEY_ID, SOURCE_SECRET_ACCESS_KEY   read-only key for the source (empty = task role)
#   WORK_BUCKET, AWS_REGION, EVENT_ID
#   SYNC_KINDS   comma list: processed,exif (default) | originals (second pass)
#   PHOTO_IDS_S3 for originals: s3 key in WORK_BUCKET of a newline list of photo ids
#   TRANSFERS    default 32
#   SOURCE_BUCKET_PROCESSED, SOURCE_BUCKET_ORIGINALS, SOURCE_BUCKET_EXIF_RICH, SOURCE_BUCKET_EXIF_SMALL
#                source bucket names (required, no defaults; set by the task definition)
# rclone only ever reads from the src: remote (copy src), never writes to it.
set -euo pipefail

: "${EVENT_ID:?EVENT_ID required}" "${WORK_BUCKET:?WORK_BUCKET required}"
AWS_REGION="${AWS_REGION:-ap-south-1}"
SYNC_KINDS="${SYNC_KINDS:-processed,exif}"
TRANSFERS="${TRANSFERS:-32}"
: "${SOURCE_BUCKET_PROCESSED:?}" "${SOURCE_BUCKET_ORIGINALS:?}" "${SOURCE_BUCKET_EXIF_RICH:?}" "${SOURCE_BUCKET_EXIF_SMALL:?}"
HERE="$(cd "$(dirname "$0")" && pwd)"

export RCLONE_CONFIG_SRC_TYPE=s3
export RCLONE_CONFIG_SRC_REGION="${SOURCE_REGION:-$AWS_REGION}"
export RCLONE_CONFIG_SRC_NO_CHECK_BUCKET=true
if [[ -n "${SOURCE_S3_ENDPOINT_URL:-}" ]]; then
  export RCLONE_CONFIG_SRC_PROVIDER=Other
  export RCLONE_CONFIG_SRC_ENDPOINT="${SOURCE_S3_ENDPOINT_URL}"
else
  export RCLONE_CONFIG_SRC_PROVIDER=AWS
fi
if [[ -n "${SOURCE_ACCESS_KEY_ID:-}" && -n "${SOURCE_SECRET_ACCESS_KEY:-}" ]]; then
  export RCLONE_CONFIG_SRC_ACCESS_KEY_ID="${SOURCE_ACCESS_KEY_ID}"
  export RCLONE_CONFIG_SRC_SECRET_ACCESS_KEY="${SOURCE_SECRET_ACCESS_KEY}"
else
  export RCLONE_CONFIG_SRC_ENV_AUTH=true
fi
export RCLONE_CONFIG_S3_TYPE=s3
export RCLONE_CONFIG_S3_PROVIDER=AWS
export RCLONE_CONFIG_S3_REGION="${AWS_REGION}"
export RCLONE_CONFIG_S3_ENV_AUTH=true
export RCLONE_CONFIG_S3_NO_CHECK_BUCKET=true
export RCLONE_CONFIG_S3_SERVER_SIDE_ENCRYPTION=AES256

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
rclone cat "s3:${WORK_BUCKET}/manifests/${EVENT_ID}.jsonl" > "$TMP/manifest.jsonl"

COMMON=(--transfers "$TRANSFERS" --checkers 16 --s3-upload-concurrency 8 --stats 30s --stats-one-line
        --retries 5 --low-level-retries 10 --no-traverse --size-only)

if [[ ",$SYNC_KINDS," == *",processed,"* ]]; then
  python3 "$HERE/keys_from_manifest.py" --manifest "$TMP/manifest.jsonl" --kind processed > "$TMP/keys.txt"
  rclone copy "src:${SOURCE_BUCKET_PROCESSED}" "s3:${WORK_BUCKET}/raw/" --files-from "$TMP/keys.txt" "${COMMON[@]}"
fi
if [[ ",$SYNC_KINDS," == *",exif,"* ]]; then
  for B in "$SOURCE_BUCKET_EXIF_RICH" "$SOURCE_BUCKET_EXIF_SMALL"; do
    python3 "$HERE/keys_from_manifest.py" --manifest "$TMP/manifest.jsonl" --kind exif --exif-bucket "$B" > "$TMP/exif_$B.txt"
    [[ -s "$TMP/exif_$B.txt" ]] && rclone copy "src:$B" "s3:${WORK_BUCKET}/exif/" --files-from "$TMP/exif_$B.txt" "${COMMON[@]}"
  done
fi
if [[ ",$SYNC_KINDS," == *",originals,"* ]]; then
  : "${PHOTO_IDS_S3:?PHOTO_IDS_S3 required for originals}"
  rclone cat "s3:${WORK_BUCKET}/${PHOTO_IDS_S3}" > "$TMP/ids.txt"
  python3 "$HERE/keys_from_manifest.py" --manifest "$TMP/manifest.jsonl" --kind originals --photo-ids "$TMP/ids.txt" > "$TMP/orig.txt"
  rclone copy "src:${SOURCE_BUCKET_ORIGINALS}" "s3:${WORK_BUCKET}/originals/" --files-from "$TMP/orig.txt" \
    --transfers 16 --checkers 16 --s3-upload-concurrency 8 --stats 30s --stats-one-line --retries 5 --no-traverse --size-only
fi
echo "sync done: event=${EVENT_ID} kinds=${SYNC_KINDS}"
