# NecroniaAssets on Silo

For the operator: how to run the store behind the NecroniaAssets repo, invite an author, keep the bucket healthy, and cut someone off. The repo keeps its large files, chiefly the sprite pack `client/runtime.cwm` at about 1.5 GiB per version, as git-annex objects in Silo's `necronia-assets` bucket; the git history stays on GitHub. Authors reach the bucket from anywhere through the git-annex remote `silo` (`s3.lanternfyre.dev`); CI, and a laptop on the LAN, through `silo-lan` (`s3.lab.techyon.dev`), the same bucket. A LAN clone must first allow that private address, `git config annex.security.allowed-ip-addresses 192.168.32.18` (git-annex refuses private addresses by default; the exact address, a CIDR does not satisfy it); the CI scripts do this themselves.

Live since 2026-11-17: the authentik client, groups, invitation and recovery flows (#327, #331), the admin-only bindings (#328), the bucket, policy, `necronia-ci` user, public route and rate limit (#329), Silo's second OpenID provider (#332), and the recreated assets repo with its login script, CI and weekly prune job. Verified that day end to end: a 1.5 GB upload through Cloudflare in 127 s, downloads at the uplink's ~13 MB/s, a CI fetch over the LAN.

## What each name is and where it is declared

| Name | What it is | Declared in |
|---|---|---|
| `necronia-assets` | the Silo bucket | `gitops/clusters/home/apps/silo/chart-values.yaml` |
| `necronia-assets-rw` | the Silo policy: read, write, delete on that bucket only | same file |
| `necronia-ci` | the Silo user CI signs with (static key) | same file; secret in 1Password `MinIO`, property `necroniaCiPassword` |
| `silo-necronia-assets-rw` | the authentik group whose members get that policy | `gitops/clusters/home/apps/authentik/manifests/blueprint-groups.configmap.yaml` |
| `necronia-cli` | the authentik client every command-line login uses | `gitops/clusters/home/apps/authentik/manifests/blueprint-necronia-cli.configmap.yaml` |
| `enrollment-necronia-assets` | the invitation flow an invitee's link opens | same file |
| `s3.lanternfyre.dev` | the public S3 API for this one bucket, plus Silo's STS on `POST /` | `gitops/clusters/home/apps/silo/manifests/silo-s3-tunnel.httproute.yaml` |

How the pieces fit, and how to extend them, is on [Who can reach what](who-can-reach-what.md).

## Invite an author

No email leaves authentik, so an invitation is a link you copy and send over chat. The invitee picks a username, password and name; the flow creates an internal user and puts them in `silo-necronia-assets-rw`.

1. In authentik (`https://authentik.techyon.dev/if/admin/`): Directory → Invitations → Create. Flow `enrollment-necronia-assets`, single use, expires in 7 days, fixed data `{"email": "<their address>"}` so the email field is pre-filled.
2. Copy the link (`https://authentik.techyon.dev/if/flow/enrollment-necronia-assets/?itoken=…`) and send it.
3. After they enrol, Directory → Groups → `silo-necronia-assets-rw` lists them. If it does not, the flow's user-write stage lost its group (the blueprint header has the `ak shell` check). A member of `authentik Admins` who also needs Silo from the command line is added to this group by hand.

What they do next is in the assets repo's README: create an app password in their user portal (`https://authentik.techyon.dev/if/user/#/settings`, the **Credentials** tab, the Tokens and App passwords box, intent App password; authentik 2026.8 has no tab of that name), run `scripts/silo-login.sh`, and `git annex` works. The script asks Silo's STS for 30-day credentials; the server does not enforce that limit, and a hand-made STS call may ask for up to a year. When they expire, git-annex reports `ExpiredToken` and the author re-runs the script, minting a new app password if authentik answers `invalid_grant`.

Forgot password: Directory → Users → the user → Create recovery link, sent the same way. The brand's recovery flow (`recovery-set-password`, #331) asks for a new password and logs them in.

## Run mc as root without copying the key

Every Silo administration below runs `mc` inside the pod, so the root credential never leaves it:

```bash
silo() {
  kubectl -n silo exec deploy/silo -- sh -c \
    'export MC_HOST_local="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@127.0.0.1:9000"; exec mc --no-color "$@"' mc "$@"
}
```

## Set the quota, once, and retire drop

The chart cannot set a quota (its setup containers run concurrently, so a quota could be set before the bucket exists), and it never deletes anything that leaves its values. After the first sync of #329:

```bash
silo quota set local/necronia-assets --size 30GiB
silo quota info local/necronia-assets
# drop.lanternfyre.dev, the one-off upload path this route replaced: its last
# objects are runtime.cwm version 1 and readversion.dat, both in the repo's history.
silo rb --force local/drop
silo admin user rm local drop-keys
silo admin policy rm local drop-put
```

Then delete `drop.lanternfyre.dev` and the `cname-drop.lanternfyre.dev` TXT record in Cloudflare by hand; external-dns never deletes.

The volume is 50 Gi and also holds the CI baselines (`protected-volumes.yaml`). Grow the PVC in `silo-data.pvc.yaml`, and its `size:` in `protected-volumes.yaml`, before raising the quota; check `silo du local/ci-reports` too.

## What is kept, how full it is, and what is not backed up

- **What stays:** every version a release tag, `main` or an open PR points at. The assets repo's weekly `prune.yml` drops the rest from Silo, then runs `git annex fsck --all --from silo-lan --fast`, which checks that every kept object is present. After deleting branches or tags, run it by hand from the assets repo's Actions tab to free the space at once.
- **How full:** `silo du local/necronia-assets` and `kubectl -n silo exec deploy/silo -- df -h /export`. Each pushed version of `runtime.cwm` is about 1.5 GiB until pruned.
- **Only server-side copy.** This bucket is not backed up (`protected-volumes.yaml`, `backup: NONE`). Released versions also live in the GHCR client images and authors keep local copies; an unreleased push exists here and on one laptop until the next release. A second git-annex remote on the already-credentialed Wasabi bucket is the open follow-up; it goes on the operator's open-items page when that page lands with the beads retirement.

## Revoke or rotate

- **A person:** Directory → Users → deactivate them, and delete their app passwords (Tokens). New logins stop at once. Credentials already issued keep working until they expire: 30 days if they came from the login script, longer if the person called STS by hand. To cut them off sooner, remove the policy below.
- **Everyone, now:** STS credentials carry the policy name they were minted with, and the chart never deletes a policy, so renaming it in git is not enough. Do, in this order:
  1. In `chart-values.yaml`, rename the policy (for example to `necronia-assets-rw2`) under `policies:` and in the `necronia-ci` user's `policy:`; rename the group in `blueprint-groups.configmap.yaml` and in the invitation flow's `create_users_group` in `blueprint-necronia-cli.configmap.yaml`. Merge and let both apps sync.
  2. In authentik, move the remaining members into the new group and delete the old one.
  3. In the pod: `silo admin policy detach local necronia-assets-rw --user necronia-ci`, then `silo admin policy rm local necronia-assets-rw`. That removal is what cuts outstanding credentials. Everyone logs in again.
- **`necronia-ci`:** change `necroniaCiPassword` in 1Password (letters and digits only, 40 characters). Force the secret to refresh, `kubectl -n silo annotate externalsecret silo-auth force-sync=$(date +%s) --overwrite`, and wait until its `.status.refreshTime` moves. Then `silo admin user rm local necronia-ci` and sync the `silo` Application (not `silo-manifests`); its post-job recreates the user. In the same sitting update `SILO_SECRET_ACCESS_KEY` in the assets repo's Actions secrets; the key ID stays `necronia-ci`. CI fails hard in between, by design. Check with `silo admin user info local necronia-ci`.

## When it fails

| Symptom | Meaning | Do |
|---|---|---|
| token endpoint answers `invalid_grant` | the login password typed instead of an app password (the first real login on 2026-11-17 did this), wrong username or app password, user inactive, or user in no `silo-*` group | check the user has a token with intent `app_password` (Directory → Tokens and App passwords) and is in the group; mint a new app password |
| STS answers `aud claim invalid` | the token was minted for another client (for example the console's) | log in with `client_id=necronia-cli` |
| STS refuses with a policy error (`policy claim missing`, or a policy "not defined") | the login did not request scope `minio`, or a `silo-*` group names a policy that is not in `chart-values.yaml` | fix the script's scope, or add the policy. Exact wording: Unverified until the first live test |
| `ExpiredToken` or `InvalidAccessKeyId` from git-annex | the credentials expired | re-run the login script |
| `SignatureDoesNotMatch` | a credential pasted with a stray character, or a presigned URL made for the other hostname | re-run the login script; sign for the host you call |
| HTTP 413 on upload | one request over Cloudflare's 100 MB cap | the git-annex remote must have `partsize=64MiB`; `aws s3 cp` (not `s3api put-object`) splits by itself |
| `chunk too big: choose chunk size <= 16MiB` | an aws-chunked part over 16 MiB, which Silo refuses | set the client's multipart chunk size to 8 MB |
| `XMinioAdminBucketQuotaExceeded` on push | the 30 GiB quota is full | run the prune by hand, or grow the PVC and then raise the quota |
| HTTP 429 on `POST /` | more than 10 STS calls a minute from one address | wait a minute; a script in a retry loop is the usual cause |
| HTTP 404 from Envoy | the request did not name the bucket, or named another one | use path-style addressing and `s3://necronia-assets`; only this bucket and STS are routed |
| HTTP 403 `AccessDenied` from Silo | unsigned request, or a key whose policy does not cover this bucket | sign the request; decode the id_token and check its `policy` list |
| `ConnectionRestricted … annex.security.allowed-ip-addresses` from git-annex on the LAN | git-annex refuses the private gateway address by default | `git config annex.security.allowed-ip-addresses 192.168.32.18` in that clone |
| a runner's `git annex get` from `s3.lab.techyon.dev` times out | the runner namespace's egress lacks the `toServices` rule to the Gateway on port 10443 (socket load balancing rewrites the connection before policy sees it) | add the rule as in `arc-lanternfyre/manifests/runner-egress.ciliumnetworkpolicy.yaml` (#336) |
| console shows only the password form after a Silo restart | Silo could not load an OpenID discovery document at start, which disables every provider | find the URL in `kubectl -n silo logs deploy/silo`, make it reachable, then `kubectl -n silo rollout restart deploy/silo`; if the log shows the 10-second timeout instead, follow [Standing warnings](standing-warnings.md#silo-object-store) |

## Remove the bucket and the public route

Reverting #329 removes the route, the rate limit and the gateway-wide client-IP policy, and brings back the retired drop route, its ExternalSecret and `scripts/silo-drop-key.sh`; remove those again in the same change. The bucket, policy and user persist inside Silo (`silo rb --force local/necronia-assets`, `silo admin user rm local necronia-ci`, `silo admin policy rm local necronia-assets-rw`), and the DNS record `s3.lanternfyre.dev` must be deleted by hand in Cloudflare.
