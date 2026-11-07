#!/usr/bin/env bash
# Mint, list or revoke an expiring upload key for Silo's `drop` bucket, so
# someone outside the house can upload files through drop.lanternfyre.dev.
#
# ⚠️ WRITES to Silo: the bucket quota, the lifecycle rule and service accounts.
# Unlike the other scripts here, this one is not read-only.
#
#     scripts/silo-drop-key.sh <label> [--days 3] [--quota SIZE]
#     scripts/silo-drop-key.sh --list
#     scripts/silo-drop-key.sh --revoke <label>
#
# <label> names the recipient or the occasion: lowercase letters, digits and
# '-', up to 15 characters. The key's access key is `drop-<label>`, and it can
# only write under drop/<label>/.
#
# --quota sets the bucket's quota, shared by every key (for example 20GiB).
# Without it, a quota already set is kept, and a bucket with none gets 10GiB.
#
# Runbook: docs/operate/silo-upload-drop.md.
#
# Needs kubectl access to the `silo` namespace, jq, and GNU date (Linux; on
# macOS install coreutils). The root credential is read from the environment
# INSIDE the Silo pod and never leaves it.

set -euo pipefail

NS=silo
BUCKET=drop
PARENT=drop-keys
HOST=drop.lanternfyre.dev
EXPIRE_DAYS=14            # lifecycle rule: uploads are deleted this long after they land
DAYS=3
QUOTA=""                  # empty: keep the bucket's quota, or set DEFAULT_QUOTA if it has none
DEFAULT_QUOTA=10GiB

die()   { printf 'error: %s\n' "$*" >&2; exit 1; }
usage() { awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"; exit "${1:-0}"; }

# Run mc inside the Silo pod against its own server. Arguments are passed
# through positionally, so nothing is re-split by the remote shell.
pod_mc() {
  # shellcheck disable=SC2016  # expanded by the pod's shell, on purpose
  kubectl -n "$NS" exec deploy/silo -- sh -c \
    'export MC_HOST_local="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@127.0.0.1:9000"; exec mc --no-color "$@"' \
    mc "$@"
}

valid_label() { [[ $1 =~ ^[a-z0-9][a-z0-9-]{0,14}$ ]] || die "label must match [a-z0-9][a-z0-9-]{0,14}, got '$1'"; }

# ── Parse arguments ──────────────────────────────────────────────────────────
MODE=mint LABEL=""
while [ $# -gt 0 ]; do
  case $1 in
    --list)    MODE=list ;;
    --revoke)  MODE=revoke; LABEL=${2:-}; shift ;;
    --days)    DAYS=${2:-}; shift ;;
    --quota)   QUOTA=${2:-}; shift ;;
    -h|--help) usage ;;
    -*)        die "unknown option $1 (see --help)" ;;
    *)         [ -z "$LABEL" ] || die "one label at a time"; LABEL=$1 ;;
  esac
  shift
done

case $MODE in
  list)
    pod_mc admin user svcacct ls local "$PARENT"
    exit 0 ;;
  revoke)
    [ -n "$LABEL" ] || die "--revoke needs a label"
    valid_label "$LABEL"
    pod_mc admin user svcacct rm local "drop-$LABEL"
    if pod_mc admin user svcacct info local "drop-$LABEL" >/dev/null 2>&1; then
      die "drop-$LABEL still exists after removing it"
    fi
    echo "Revoked drop-$LABEL: it no longer exists, and requests signed with it are refused."
    exit 0 ;;
esac

command -v jq >/dev/null || die "needs jq"
date -u -d '+1 day' >/dev/null 2>&1 || die "needs GNU date (date -d); on macOS install coreutils"

[ -n "$LABEL" ] || usage 1
valid_label "$LABEL"
if ! [[ $DAYS =~ ^[0-9]+$ ]] || [ "$DAYS" -lt 1 ]; then
  die "--days must be a whole number of days, at least 1"
fi
[ "$DAYS" -le "$EXPIRE_DAYS" ] || die "--days $DAYS outlives the ${EXPIRE_DAYS}-day lifecycle rule; files would vanish while the key still works"

# ── 1. The bucket and the parent user exist (the chart creates both) ─────────
pod_mc stat "local/$BUCKET" >/dev/null 2>&1 \
  || die "bucket $BUCKET does not exist. Has the silo app synced? (kubectl -n argocd get app silo)"
pod_mc admin user info local "$PARENT" >/dev/null 2>&1 \
  || die "user $PARENT does not exist. Has the silo app synced, and did silo-post-job succeed?"
if pod_mc admin user svcacct info local "drop-$LABEL" >/dev/null 2>&1; then
  die "drop-$LABEL already exists. Revoke it first: $0 --revoke $LABEL"
fi

# ── 2. Quota: keep or set it, then read it back and refuse a zero ────────────
# A hard quota is what stops a leaked key from filling the volume that also
# holds the CI baselines. The scanner enforces it, so writes can overshoot it
# briefly before they are refused. An existing quota is left alone unless
# --quota is given, so minting a key never undoes a raise.
quota_bytes() { pod_mc quota info "local/$BUCKET" --json | jq -r '.quota // 0'; }
if [ -n "$QUOTA" ]; then
  pod_mc quota set "local/$BUCKET" --size "$QUOTA" >/dev/null
elif [ "$(quota_bytes)" -eq 0 ]; then
  pod_mc quota set "local/$BUCKET" --size "$DEFAULT_QUOTA" >/dev/null
fi
quota=$(quota_bytes)
[ "$quota" -gt 0 ] || die "the quota on $BUCKET reads back as 0; not minting a key"

# ── 3. Lifecycle: add the expiry rule if the bucket has none ─────────────────
# `ilm rule ls` exits 1 when the bucket has no lifecycle configuration. Only
# then is a rule added: `ilm rule add` appends, so running it every time would
# pile up duplicates. Then the rules must include an expiry after exactly
# EXPIRE_DAYS; any other rule set is reported, not changed.
ilm_json() { pod_mc ilm rule ls "local/$BUCKET" --json 2>/dev/null; }
if ! ilm_json >/dev/null; then
  pod_mc ilm rule add --expire-days "$EXPIRE_DAYS" "local/$BUCKET" >/dev/null
fi
# Field names are matched case-insensitively, so this does not depend on how
# this mc release capitalises its JSON.
has_expiry=$(ilm_json | jq -s --argjson d "$EXPIRE_DAYS" '
  [ .[] | .. | objects | to_entries[] | select(.key | ascii_downcase == "expiration")
    | .value | objects | to_entries[] | select(.key | ascii_downcase == "days") | .value ]
  | any(. == $d)') || has_expiry=false
if [ "$has_expiry" != true ]; then
  pod_mc ilm rule ls "local/$BUCKET" >&2 || true
  die "no ${EXPIRE_DAYS}-day expiry rule on $BUCKET (rules above); not minting a key"
fi

# ── 4. Mint the key ──────────────────────────────────────────────────────────
# The secret is generated here, letters and digits only: tools interpolate S3
# secrets raw into URLs, and Silo's own generator can emit '+' and '/'.
secret=$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 40) || true
[ "${#secret}" -eq 40 ] || die "could not generate a secret"
expiry=$(date -u -d "+${DAYS} days" '+%Y-%m-%dT%H:%M:%SZ')

# The inline policy narrows the parent's drop-put to this label's prefix. The
# effective permission is the intersection of the two, so this cannot widen
# anything. mc reads the file inside the pod, so it is written there.
policy=$(printf '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["s3:PutObject","s3:AbortMultipartUpload"],"Resource":["arn:aws:s3:::%s/%s/*"]}]}' "$BUCKET" "$LABEL")
policy_file="/tmp/drop-$LABEL.policy.json"
# shellcheck disable=SC2016  # $1 and $2 are the pod shell's positional arguments
kubectl -n "$NS" exec deploy/silo -- sh -c 'printf "%s" "$1" > "$2"' sh "$policy" "$policy_file"
trap 'kubectl -n "$NS" exec deploy/silo -- rm -f "$policy_file" >/dev/null 2>&1 || true' EXIT

pod_mc admin user svcacct add local "$PARENT" \
  --access-key "drop-$LABEL" --secret-key "$secret" \
  --policy "$policy_file" --expiry "$expiry" \
  --name "drop-$LABEL" --description "drop upload key, prefix $BUCKET/$LABEL/" >/dev/null

# ── 5. What to send ──────────────────────────────────────────────────────────
# Part size 8MB, the AWS CLI's own default, set explicitly so a profile default
# cannot change it. Two ceilings bound it: Cloudflare refuses a request over
# 100 MB, and Silo refuses an aws-chunked chunk over 16 MiB ("chunk too big:
# choose chunk size <= 16MiB"). AWS CLI 2.39.6 sends each part as ONE such
# chunk, so 32MB parts failed on every UploadPart (measured 2026-11-07).
cat <<EOF

Minted drop-$LABEL, valid until $expiry.
Bucket quota: $(( quota / 1073741824 )) GiB, shared by every drop key. Uploads are deleted ${EXPIRE_DAYS} days after they land.
Revoke early with: $0 --revoke $LABEL

── Send everything below the line to the uploader, through a 1Password share ──

Upload instructions. Valid until $expiry (UTC). Needs the AWS CLI:
https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html

One-time setup (each line on its own):

aws configure set aws_access_key_id drop-$LABEL --profile drop
aws configure set aws_secret_access_key $secret --profile drop
aws configure set region us-east-1 --profile drop
aws configure set s3.addressing_style path --profile drop
aws configure set s3.multipart_chunksize 8MB --profile drop

Upload a file (repeat for each one):

aws --profile drop --endpoint-url https://$HOST s3 cp YOUR-FILE s3://$BUCKET/$LABEL/

You can upload but not list or download, so a successful upload ends with
"upload: YOUR-FILE to s3://$BUCKET/$LABEL/YOUR-FILE" and nothing else.
If it fails with a checksum error, set this in the same terminal, then run the
upload again:
  Linux or macOS:      export AWS_REQUEST_CHECKSUM_CALCULATION=when_required
  Windows PowerShell:  \$env:AWS_REQUEST_CHECKSUM_CALCULATION = "when_required"
  Windows cmd:         set AWS_REQUEST_CHECKSUM_CALCULATION=when_required
EOF
