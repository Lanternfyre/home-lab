# Let someone upload files into Silo from outside

This runbook is for the operator, when someone outside the house needs to send you files too large for email: an asset pack, a dump, a video. Silo is the cluster's S3-compatible object store, a fork of MinIO. The uploader sends files to `drop.lanternfyre.dev` with a key that you mint and that expires on its own, and you collect them from the LAN. Nothing in the cluster changes for each occasion: you run one script per person.

**Status:** built on 2026-11-07, on branch `feat/silo-drop`. **Unverified** until it is merged and the end-to-end check has run: a 300 MB upload through Cloudflare, and every refusal under [What the drop refuses](#what-the-drop-refuses).

## What the drop is

- **Write-only.** A key can upload files and nothing else. The uploader cannot list the bucket, download anything (their own uploads included) or delete.
- **One folder per key.** A key minted for `alex` writes only under `drop/alex/` (S3 calls this a prefix), so two uploaders can never overwrite each other's files.
- **Temporary.** Each key expires after 3 days unless you ask for more, at most 14. A lifecycle rule on the bucket deletes every file 14 days after it lands, so copy out what you want to keep.
- **Capped.** The whole bucket has a hard quota, 10 GiB unless you raise it, shared by every key. Silo's background scanner checks the quota periodically, so an upload can overshoot it briefly before writes are refused.
- **Large files work, through `aws s3 cp`.** Cloudflare refuses any single request over 100 MB. `aws s3 cp` sends a large file as a multipart upload: the file goes up in parts of 32 MB, one request each, and Silo joins them at the end. A 2 GB file is about 64 requests, each under the cap. A browser upload, or any tool that sends the file in one request, fails at Cloudflare with a 413.

## Give someone a key

You need `kubectl` access to the `silo` namespace (no sudo), `jq`, and GNU `date`, which Linux has. From the repo root:

```bash
scripts/silo-drop-key.sh <label>                 # 3 days
scripts/silo-drop-key.sh <label> --days 7        # longer, at most 14
scripts/silo-drop-key.sh <label> --quota 20GiB   # also raise the bucket quota
```

`<label>` names the person or the occasion: lowercase letters, digits and `-`, up to 15 characters. Before minting, the script checks that the bucket has a quota and the 14-day expiry rule, and adds whichever is missing. It refuses to mint if either still isn't there. It changes an existing quota only when you pass `--quota`. The root credential stays inside the Silo pod.

It prints a block of commands with the secret in it. **Send that block through a 1Password share:** put it in a new item, choose Share, and let the link expire no later than the key does. Don't paste it into chat or email: anyone holding it can write to the drop until the key expires.

## What the uploader does

They install the [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), paste the five `aws configure set` lines once, then upload each file:

```bash
aws --profile drop --endpoint-url https://drop.lanternfyre.dev s3 cp YOUR-FILE s3://drop/<label>/
```

The commands are the same on Linux, macOS and Windows; the block also says how to set the one environment variable that might be needed, in each shell. A successful upload prints one `upload:` line. Because the key cannot list or download, they get no other confirmation; you check from your side.

## Collect the files

**In a browser, from the LAN:** sign in to the Silo console at <https://silo.lab.techyon.dev> with authentik. Everyone who can sign in there is a Silo admin, so the `drop` bucket is visible. Open it, then the `<label>` folder, and download.

**In a terminal:** list what arrived, then copy one object out by its name in the listing. Set `OBJ` to the path under `drop/` that the listing shows, for example `alex/assets.zip`:

```bash
silo() { kubectl -n silo exec deploy/silo -- sh -c \
  'export MC_HOST_local="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@127.0.0.1:9000"; exec mc "$@"' mc "$@"; }

silo ls --recursive local/drop/
OBJ='alex/assets.zip'
silo cat "local/drop/$OBJ" > "$(basename "$OBJ")"
silo stat --json "local/drop/$OBJ" | jq .size; stat -c %s "$(basename "$OBJ")"
```

The two sizes, in bytes, must match. Copy everything you need before day 14, then delete the folder rather than waiting for the rule:

```bash
silo rm --recursive --force "local/drop/<label>/"
```

## List keys, or revoke one early

```bash
scripts/silo-drop-key.sh --list            # live keys and their expiry
scripts/silo-drop-key.sh --revoke <label>  # stops that key at once
```

`--revoke` checks that the key is gone afterwards and says so. Revoke as soon as the upload is in. To give the same person a fresh key, revoke the old one first; the script refuses to mint a second key under a label that still has one.

## When an upload fails

| The uploader sees | Cause, and what to tell them |
|---|---|
| `InvalidAccessKeyId` | The key expired or was revoked. Mint a new one and send it. |
| `SignatureDoesNotMatch` | The secret was mangled when pasted, or the endpoint is not `https://drop.lanternfyre.dev`. Send the block again and ask them to paste it unchanged. |
| An error naming `drop.drop.lanternfyre.dev` | The `s3.addressing_style path` line was skipped, so the CLI put the bucket name into the hostname. Ask them to run that line. |
| HTTP 413 | The file went up in one request over 100 MB, from a tool other than `aws s3 cp` (`aws s3api put-object` does this). Ask them to use the `aws s3 cp` line as given. |
| HTTP 404 | The tool tried to read or list, and the drop doesn't route either. `rclone` and `mc` both check the bucket first; ask them to use `aws s3 cp`. |
| A checksum error | Newer AWS CLI versions add checksums to every upload by default. Ask them to set the variable from the block and retry. |
| A bucket-quota error | The drop is full. Raise it with `silo quota set local/drop --size 20GiB` (the `silo` function above), then ask them to retry. |

## What the drop refuses

These are the properties that make it safe on a public hostname with no sign-in gate:

- Envoy, the cluster's gateway, routes only `PUT`, `POST` and `DELETE` under `/drop`. Everything else gets a 404 without reaching Silo: any `GET` or `HEAD`, the `ci-reports` bucket that holds CI results, and Silo's admin API.
- Without a valid key, Silo refuses every request with `AccessDenied`.
- A key cannot write outside its own `drop/<label>/` prefix.

Never widen the route's methods or path. Reports under `ci-reports/reports/` are readable without a key, which is acceptable only while the rest of the S3 API stays on the LAN.

## Where it is defined

- The public route, with the reasoning above in full: [`silo-drop-tunnel.httproute.yaml`](../../gitops/clusters/home/apps/silo/manifests/silo-drop-tunnel.httproute.yaml).
- The `drop` bucket, the `drop-put` policy and `drop-keys`, the user every key is minted under: [`chart-values.yaml`](../../gitops/clusters/home/apps/silo/chart-values.yaml). The parent user's own secret is generated by [`silo-drop-keys.external-secret.yaml`](../../gitops/clusters/home/apps/silo/manifests/silo-drop-keys.external-secret.yaml); nobody signs with it.
- The script: [`scripts/silo-drop-key.sh`](../../scripts/silo-drop-key.sh). The quota and the expiry rule are set there, not in the chart, because the chart's setup job can't set them reliably.
