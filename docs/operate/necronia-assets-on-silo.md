# NecroniaAssets on Silo

How to run the store behind the NecroniaAssets repo: invite an author, keep the bucket healthy, and cut someone off. The repo keeps its large files (the 1.5 GB sprite pack and friends) as git-annex objects in Silo's `necronia-assets` bucket; the git history stays on GitHub. Authors reach the bucket from anywhere through `s3.lanternfyre.dev`; CI reaches it over the LAN.

What is real today: the authentik client, groups and invitation flow (home-lab #327), the admin-only bindings on every other application (#328) and the bucket, policy, CI user and public route (this change) are **in git, applied when their PRs merge**. The assets repo's login script, CI and prune job land with the repo's recreation and are **Planned** until then; this page names them so the operator side reads as one procedure.

## The names

| Name | What it is | Where it is declared |
|---|---|---|
| `necronia-assets` | the Silo bucket | `gitops/clusters/home/apps/silo/chart-values.yaml` |
| `necronia-assets-rw` | the Silo policy: read, write, delete on that bucket only | same file |
| `necronia-ci` | the Silo user CI signs with (static key) | same file; secret in 1Password `MinIO`, property `necroniaCiPassword` |
| `silo-necronia-assets-rw` | the authentik group whose members get that policy | `apps/authentik/manifests/blueprint-groups.configmap.yaml` |
| `necronia-cli` | the authentik client every command-line login uses | `apps/authentik/manifests/blueprint-necronia-cli.configmap.yaml` |
| `enrollment-necronia-assets` | the invitation flow an invitee's link opens | same file |
| `s3.lanternfyre.dev` | the public S3 API for this one bucket, plus Silo's STS on `POST /` | `apps/silo/manifests/silo-s3-tunnel.httproute.yaml` |

How the pieces fit, and how to extend them, is on [Who can reach what](who-can-reach-what.md).

## Invite an author

No email leaves authentik, so an invitation is a link you copy and send over chat. The invitee picks a username and password; the flow creates an internal user and puts them in `silo-necronia-assets-rw`.

1. In authentik (`https://authentik.techyon.dev/if/admin/`): Directory → Invitations → Create. Flow `enrollment-necronia-assets`, single use, expires in 7 days, fixed data `{"email": "<their address>"}` so the form is pre-filled.
2. Copy the link (`https://authentik.techyon.dev/if/flow/enrollment-necronia-assets/?itoken=…`) and send it.
3. After they enrol, Directory → Groups → `silo-necronia-assets-rw` lists them. A member of `authentik Admins` who also needs Silo from the command line is added to this group by hand.

What they do next, from the assets repo's README: create an app password in their user portal (`https://authentik.techyon.dev/if/user/#/settings`, Tokens and App passwords), run the login script, and `git annex` works. Credentials last 30 days; the script is re-run when git-annex reports `ExpiredToken`.

Forgot password: Users → the user → Create recovery link, sent the same way. There is no self-service reset without email.

## The admin command

Every Silo administration below runs `mc` inside the pod, so the root credential never leaves it:

```bash
silo() {
  kubectl -n silo exec deploy/silo -- sh -c \
    'export MC_HOST_local="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@127.0.0.1:9000"; exec mc --no-color "$@"' mc "$@"
}
```

## Set the quota, once

The chart cannot set it (its setup containers run concurrently, so a quota could be set before the bucket exists). After the first sync that creates the bucket:

```bash
silo quota set local/necronia-assets --size 30GiB
silo quota info local/necronia-assets
```

The volume is 50 Gi and also holds the CI baselines (`protected-volumes.yaml`); each pushed version of `runtime.cwm` is about 1.5 GiB until the prune job drops it. Grow the PVC in `silo-data.pvc.yaml` before raising the quota.

## Keep it healthy

- **What stays:** every version a release tag, `main` or an open PR points at. The assets repo's weekly `prune.yml` job (Planned) runs `git annex unused --from silo-lan`, drops the rest, then `git annex fsck --from silo-lan --fast`, which also proves every kept object is present. Run it by hand from the Actions tab after a big cleanup.
- **How full:** `silo du local/necronia-assets` and `kubectl -n silo exec deploy/silo -- df -h /export`.
- **Only server-side copy.** This bucket is not backed up (`protected-volumes.yaml`, `backup: NONE`). Released versions also live in the GHCR client images and authors keep local copies; an unreleased push exists here and on one laptop until the next release. A second git-annex remote on the already-credentialed Wasabi bucket is the open follow-up.

## Revoke or rotate

- **A person:** remove them from `silo-necronia-assets-rw` (or deactivate the user). New logins stop at once; credentials already issued keep working until they expire, at most 30 days. There is no per-person cut-off before that.
- **Everyone, now:** rename the policy in `chart-values.yaml` (for example `necronia-assets-rw` → `necronia-assets-rw2`) and the group to match. Outstanding credentials carry the old policy name and grant nothing from the next sync; everyone logs in again.
- **`necronia-ci`:** the chart creates a user only when it is absent and never updates a secret. Change `necroniaCiPassword` in 1Password, then `silo admin user rm local necronia-ci`, sync Silo (the post-job recreates the user), and update the assets repo's Actions secrets `SILO_ACCESS_KEY_ID` / `SILO_SECRET_ACCESS_KEY` in the same sitting. CI fails hard in between, by design.

## When it fails

| Symptom | Meaning | Do |
|---|---|---|
| token endpoint answers `invalid_grant` | wrong username or app password, user inactive, or user in no `silo-*` group | check group membership, mint a new app password |
| STS answers `aud claim invalid` | the token was minted for another client (for example the console's) | log in with `client_id=necronia-cli` |
| STS credentials work for nothing | the token carried an empty `policy` claim: the group name does not start with `silo-`, or the login did not request scope `minio` | fix the group name or the scope |
| `ExpiredToken` from git-annex | the 30 days are over | re-run the login script |
| HTTP 413 on upload | a single request over Cloudflare's 100 MB cap | the remote must use `partsize=64MiB`; a plain `aws s3 cp` needs `multipart_chunksize 8MB` |
| HTTP 429 on `POST /` | more than 10 STS calls a minute from one address | wait a minute; a script in a retry loop is the usual cause |
| HTTP 404 from Envoy | a path outside `/necronia-assets`, or `GET /` | nothing is wrong; only this bucket and STS are routed |
| HTTP 403 `AccessDenied` from Silo | unsigned request, or a key without this policy | sign the request; check the policy claim |
| console shows only the password form after a Silo restart | an OpenID provider's discovery URL failed at start, which disables all of them | see [Standing warnings](standing-warnings.md#silo-object-store) |

## Undo

Reverting the Silo change stops the route and the rate limit. The bucket, policy and user persist inside Silo (`silo rb --force local/necronia-assets`, `silo admin user rm local necronia-ci`, `silo admin policy rm local necronia-assets-rw`), and the DNS record `s3.lanternfyre.dev` must be deleted by hand in Cloudflare, because external-dns never deletes.
