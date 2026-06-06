#!/usr/bin/env bash
# Sanitizing publish job (Phase 6). Pulls the SCRUBBED dataset from the backend
# (/birds/public - species, counts, day-granular timestamps, confidence, first-
# seen only) and pushes it to Cloudflare R2. The public page reads it from there.
#
# The privacy boundary is the backend endpoint: it is the ONLY producer of public
# data, and it never emits latitude/longitude/clip paths/per-detection rows. This
# script just moves that already-safe JSON. There is no network path from the
# public bucket back to the private DB or the audio clips.
#
# Runs on the Brain (where the backend is). Cloudflare creds come from
# publish/cloudflare.env (git ignored): R2 bucket + a scoped API token.
#
#   ./publish.sh           # fetch + push
#   DRY_RUN=1 ./publish.sh # fetch + write local file only, no push
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${BIRDS_PUBLIC_OUT:-/home/clawd-a/workshop/.runtime/birds-public.json}"
BACKEND="${WORKSHOP_BACKEND:-http://127.0.0.1:8000}"

# Cloudflare config (R2). Fill cloudflare.env from cloudflare.env.example.
[ -f "$HERE/cloudflare.env" ] && set -a && . "$HERE/cloudflare.env" && set +a || true

echo "Fetching sanitized dataset from $BACKEND/birds/public ..."
mkdir -p "$(dirname "$OUT")"
curl -fsS "$BACKEND/birds/public" -o "$OUT"

# Hard guard: refuse to publish if any sensitive token leaked into the payload.
# This is belt-and-suspenders; the backend already excludes these. If this ever
# trips, STOP and fix the sanitizer, do not push.
if grep -qiE '"latitude"|"longitude"|"clip_name"|clips/' "$OUT"; then
  echo "ABORT: sanitized payload contains sensitive fields. Not publishing." >&2
  exit 1
fi

bytes=$(wc -c < "$OUT")
echo "Sanitized dataset OK ($bytes bytes) -> $OUT"

if [ "${DRY_RUN:-0}" = "1" ]; then
  echo "DRY_RUN: skipping Cloudflare push."
  exit 0
fi

: "${R2_BUCKET:?set R2_BUCKET in cloudflare.env}"
# wrangler reads CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID from env.
echo "Pushing to R2 bucket $R2_BUCKET as birds-public.json ..."
npx --yes wrangler r2 object put "$R2_BUCKET/birds-public.json" \
  --file "$OUT" --content-type application/json --remote
echo "Published."
