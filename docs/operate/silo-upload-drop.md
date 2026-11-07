# Let someone upload files into Silo from outside

This runbook is for the operator, when someone outside the house needs to send you files too large for email: an asset pack, a dump, a video. They upload to `drop.lanternfyre.dev` with a key that you mint and that expires on its own. You collect the files from the LAN. Nothing has to change in the cluster for each occasion: one script run per person.

**Status:** built on 2026-11-07. Unverified until the end-to-end check in that change has run: a 300 MB multipart upload through Cloudflare, plus the refusals listed under [What the drop refuses](#what-the-drop-refuses).

## What the drop is

- **Write-only.** A key can upload files and nothing else. The uploader cannot list the bucket, download anything (their own uploads included) or delete.
- **One prefix per key.** A key minted for `alex` writes only under `drop/alex/`, so two uploaders can never overwrite each other's files.
- **Temporary.** Each key expires after 3 days unless you ask for more, at most 14. Every file is deleted 14 days after it lands, so copy out what you want to keep.
- **Capped.** The whole bucket has a hard quota, 10 GiB unless you set another. Silo checks it periodically, so an upload can overshoot it briefly before writes are refused.
- **Large files work, through the AWS CLI.** Cloudflare refuses any single request over 100 MB. The AWS CLI sends a big file as a multipart upload in 32 MB parts, so a 2 GB file is about 64 requests, each under the cap. A browser upload, or any tool sending one big PUT, fails at Cloudflare with a 413.

## Give someone a key

You need `kubectl` access to the `silo` namespace; no sudo. From the repo root:

```bash
scripts/silo-drop-key.sh <label>                 # 3 days, 10 GiB bucket quota
scripts/silo-drop-key.sh <label> --days 7        # longer, at most 14
scripts/silo-drop-key.sh <label> --quota 20GiB   # raise the bucket quota first
```

`<label>` names the person or the occasion: lowercase letters, digits and `-`, up to 15 characters. Before minting, the script makes sure the bucket has its quota and its 14-day expiry rule, and it refuses to mint a key if either is missing. The root credential stays inside the Silo pod.

It prints a block of commands with the secret in it. **Send that block through a 1Password share**, not chat or email: anyone holding it can write to the drop until the key expires.

## What the uploader does

They install the [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), paste the five `aws configure set` lines once, then upload each file:

```bash
aws --profile drop --endpoint-url https://drop.lanternfyre.dev s3 cp YOUR-FILE s3://drop/<label>/
```

This works the same on Linux, macOS and Windows. A successful upload prints one `upload:` line. Because the key cannot list or download, there is no other confirmation on their side; you check from yours.

## Collect the files

From the LAN, sign in to the Silo console at <https://silo.lab.techyon.dev>, open the `drop` bucket and download from the `<label>/` folder.

Or from a terminal, without the console. Here `<label>/<file>` is the object to fetch:

```bash
kubectl -n silo exec deploy/silo -- sh -c \
  'export MC_HOST_local="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@127.0.0.1:9000"; mc ls --recursive local/drop/'
kubectl -n silo exec deploy/silo -- sh -c \
  'export MC_HOST_local="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@127.0.0.1:9000"; mc cat "local/drop/$1"' sh '<label>/<file>' > <file>
```

Compare the downloaded size with what `mc ls` reported. Do this before day 14; after that the file is gone.

## List keys, or revoke one early

```bash
scripts/silo-drop-key.sh --list            # live keys and their expiry
scripts/silo-drop-key.sh --revoke <label>  # stops that key at once
```

Revoke as soon as the upload is in. To give the same person a fresh key, revoke the old one first; the script refuses to mint over a label that still has a key.

## When an upload fails

| The uploader sees | Cause and fix |
|---|---|
| `InvalidAccessKeyId` | The key expired or was revoked. Mint a new one. |
| `SignatureDoesNotMatch` | The secret was mangled when pasted, or the endpoint is not `https://drop.lanternfyre.dev`. Re-send the block. |
| An error naming `drop.drop.lanternfyre.dev` | `s3.addressing_style path` was skipped, so the CLI put the bucket in the hostname. Run that configure line. |
| HTTP 413 | One request was over 100 MB: the `multipart_chunksize` line was skipped, or the tool isn't the AWS CLI. |
| HTTP 404 | The tool tried to read or list, which the drop does not route. `rclone` and `mc` both do this; use `aws s3 cp`. |
| A checksum error | Newer AWS CLI versions add checksums to every upload by default. Set `AWS_REQUEST_CHECKSUM_CALCULATION=when_required` and retry. |
| A bucket-quota error | The drop is full. Raise it, then they retry: <br>`kubectl -n silo exec deploy/silo -- sh -c 'export MC_HOST_local="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@127.0.0.1:9000"; mc quota set local/drop --size 20GiB'` |

## What the drop refuses

These are the properties that make it safe on a public hostname with no sign-in gate:

- Envoy routes only `PUT`, `POST` and `DELETE` under `/drop`. Everything else gets Envoy's 404 without reaching Silo, including any `GET` or `HEAD`, the `ci-reports` bucket and the admin API.
- Without a valid key, Silo answers every request with `AccessDenied`.
- A key cannot write outside its own `drop/<label>/` prefix.

Never widen the route's methods or path. Reports under `ci-reports/reports/` are anonymously readable, which is acceptable only while the rest of the S3 API stays on the LAN.

## Where it is defined

- The public route, with the reasoning above in full: [`silo-drop-tunnel.httproute.yaml`](../../gitops/clusters/home/apps/silo/manifests/silo-drop-tunnel.httproute.yaml).
- The `drop` bucket, the `drop-put` policy and the `drop-keys` parent user: [`chart-values.yaml`](../../gitops/clusters/home/apps/silo/chart-values.yaml). The parent's secret is generated by [`silo-drop-keys.external-secret.yaml`](../../gitops/clusters/home/apps/silo/manifests/silo-drop-keys.external-secret.yaml); nobody signs with it.
- The script: [`scripts/silo-drop-key.sh`](../../scripts/silo-drop-key.sh). The quota and the expiry rule are set there, not in the chart, because the chart's setup job can't set them reliably.
