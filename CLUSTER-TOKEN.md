# Cluster token — management and rotation runbook

The k3s join token is the single credential that lets a machine become part of
this cluster. Anyone holding it, with network reach to a server, can join a
**server** — which means etcd access, which means everything.

This document is the plan for managing it and the procedure for rotating it.

✅ **EXECUTED 2026-09-06.** The token was rotated, an agent token was split out,
and `k3sblog` is dead — `server-bootstrap` returns `401` for it on all three
servers. The procedure below is the corrected one; see "What 2026-09-06 taught
us" for why the original was unsafe.

---

## Where things actually stand

Established against the live cluster and the k3s docs, not from memory.

| | |
|---|---|
| Current token | 7 characters, a dictionary word plus a suffix |
| Where it lives | ⚠️ **CORRECTED 2026-09-06** — `K3S_TOKEN=` in each AGENT's `/etc/systemd/system/k3s-agent.service.env`. The three SERVERS carry no token at all outside `/var/lib/rancher/k3s/server/token`: their `ExecStart` has no arguments and their `.env` is 0 bytes. See "Where it actually lives" below |
| Who manages it | **nobody** — it is not in Ansible, not in `config.yaml`, not in any vault |
| Exposure | committed in plaintext in `gitops/argo-install.md`, in a **public** repo, and present in git history |
| k3s version | v1.35.6+k3s1 — `k3s token rotate` is available |

### ⚠️ Where it actually lives — this table used to be wrong

The row above said the token was inline in `ExecStart`. It is not, and the
difference changes what "unit normalisation is a precondition" means.

Measured from the captured unit files, 2026-09-06:

```
_captured/k8s-1.home/k3s.service          ExecStart=/usr/local/bin/k3s server \   (no args)
_captured/k8s-1.home/k3s.service.env      0 bytes
_captured/k8s-4.home/k3s-agent.service.env    K3S_TOKEN=…   96 bytes
_captured/k8s-5,6,7 …                          K3S_TOKEN=…   96 bytes
ansible/_captured/edge-1.edge/…service.env     K3S_TOKEN=…   96 bytes
```

Three consequences:

1. **The precondition still stands, but for the env file.** `K3S_TOKEN` in the
   unit environment beats `config.yaml` exactly as a CLI flag would, so
   `token-file:` is inert on an agent until that env file stops carrying one.
   Normalising `ExecStart` was never the blocker; normalising `.env` is.
2. **The servers are already clean.** Adding `token-file:` / `agent-token-file:`
   to the server `config.yaml` is genuinely additive, not a replacement.
3. 🔴 **`30-upgrade.yml` wipes `K3S_TOKEN` on every upgrade.** It re-runs the
   installer without the variable, and the installer `rm -f`s the unit and its
   `.env` before regenerating them from the invoking shell's exported `K3S_*`.
   Agents survive because they already hold registered node credentials — but it
   means the env-file path is not durable, and `config.yaml` + `token-file:` is.
   That is the strongest argument for the target state below.

⚠️ `_captured/` on the workstation therefore holds the plaintext token for every
agent and for the public VPS. It is gitignored, and it is still a copy on a
laptop.

### ✅ `k3sblog` was the token, and it is now dead

`gitops/argo-install.md` lines 33 and 110 both carried `--token k3sblog`, in a
**public** repository. Until 2026-09-06 that was the live credential, and in k3s
it joins a **server**. Those lines now take `$K3S_TOKEN` from the environment.

Rotated 2026-09-06. Proven dead rather than assumed:

```
$ curl -sk -o /dev/null -w '%{http_code}' -u "server:k3sblog" \
    https://192.168.33.{3,2,7}:6443/v1-k3s/server-bootstrap
401  401  401
```

⚠️ It stays in git history permanently and that cannot be undone in a public
repo. Rotation is what makes it moot — the reason to rotate, not a side effect.

**Live topology** (`systemctl is-active`, not the inventory file):

```
k8s-1, k8s-2, k8s-3   k3s.service         servers (etcd)
k8s-4, k8s-5, k8s-6   k3s-agent.service   agents
```

### ⚠️ Do not trust `_captured/` for this

`_captured/` holds each node's pre-install unit file, but the fetch task uses
`fail_on_missing: false`, which **silently keeps a stale local copy** when the
remote file is gone. It currently shows `k3s.service` with `--server` for k8s-4
and k8s-5 — both of which are agents today — and has no capture for k8s-6 at
all. Inventory the nodes live; treat these files as history, not truth.

---

## Why rotation alone is not the fix

Rotating replaces one weak, unmanaged secret with another weak, unmanaged
secret unless three things change with it:

1. **Strength.** Generate it, do not choose it. `k3s token generate` emits the
   secure format k3s expects.
2. **A source of truth.** 1Password, like every other credential in this
   estate. Never git — this repository is public and stays public.
3. **A delivery path that is not the unit file.** While `--token` sits in
   `ExecStart`, a token in `config.yaml` is **ignored** — this repo's own
   invariant: *CLI args in the systemd unit take precedence over config.yaml*.

That third point makes unit normalisation a **precondition**, not a nicety.
`roles/k3s_config` already anticipates it: *"only once the unit is normalised
does this file become authoritative."* Token management is the first concrete
reason to finish that work.

---

## Target state

- Token generated by `k3s token generate`, never hand-chosen
- Source of truth: a 1Password item, alongside the other cluster credentials
- On each node: `/etc/rancher/k3s/token`, mode `0600`, root-owned, referenced
  from `config.yaml` as `token-file:` — **not** inline in any unit
- Ansible receives it at run time (`vars_prompt` or `--extra-vars`) and never
  stores it. There is no `op` CLI on the control workstation and no
  ansible-vault in this repo, so a runtime prompt is the honest option; adding
  either is a separate decision.
- **Consider a separate `--agent-token`.** k3s supports one, and it defaults to
  the server token. Giving agents their own credential means a compromised
  agent cannot be used to join a *server*. This is the single biggest structural
  improvement available here, and it is worth more than the rotation itself.

---

## Rotation procedure

### Before you start

- 🔴 **Take an etcd snapshot** (`k3s etcd-snapshot save`). With H0 deferred there
  are no other backups, so this is the only rollback for a rotation gone wrong.
- 🔴 **Keep the old token.** Per the k3s docs: *"Snapshots taken before the
  rotation will require the old server token when restoring the cluster."*
  Archive it in 1Password marked as the pre-rotation value — **do not delete
  it**, or every snapshot older than the rotation becomes unrestorable.
- Confirm break-glass: `kubectl --context homelab-breakglass get nodes` works.
  It authenticates with a client certificate and is unaffected by the token.

### The rotation

🔴 **Use the playbook. Do not hand-roll the steps.**

```
openssl rand -hex 24     # server token   -> 1Password
openssl rand -hex 24     # agent token    -> 1Password
cd ansible && ansible-playbook playbooks/71-rotate-cluster-token.yml --ask-become-pass
```

Secrets are **prompted**, never passed with `-e`: that keeps the live join
credential out of shell history, the process table, and terminal scrollback.

⚠️ `openssl rand -hex 24`, **not** `k3s token generate` — the latter emits a
*bootstrap* token (`xxxxxx.xxxxxxxxxxxxxxxx`), which k3s then rejects with
`failed to normalize server token; must be in format K10<CA-HASH>::<USERNAME>:<PASSWORD>`.

The playbook runs six phases, and the split between phase 3 and phase 5 is the
entire point:

| # | phase | why |
|---|---|---|
| 1 | gate: old token `200`, new token `401`, every server | proves the starting state, and that the "new" token is new |
| 2 | pre-rotation etcd snapshot | the only rollback |
| 3 | write new token files to **every** node — **no restarts** | |
| 4 | `k3s token rotate`, once | |
| 5 | restart servers `serial: 1`, k8s-1 last, Ready + quorum gated | |
| 6 | gate: new token `200`, **old token dead**, agent token rejected for server joins | catches a server that missed its restart |

⚠️ **`20-config-converge.yml` cannot do this.** It writes the token file and
restarts k3s in the same per-node loop, so the first node restarts before the
datastore has been rotated. That is exactly how k8s-3 was lost for a day.

⚠️ Agents and the edge node are **not** restarted. A registered node holds client
certificates, not the token; it needs the token only to re-register. Their files
are updated so a future rejoin works.

### ⚠️ Verifying — the check that was missing

`k3s token rotate` printing `Token rotated` confirms the **datastore** changed.
It says nothing about what the servers authenticate with. The honest check is the
bootstrap endpoint itself — the exact request a joining node makes:

```
curl -sk -o /dev/null -w '%{http_code}' -u "server:<password>" \
  https://<server>:6443/v1-k3s/server-bootstrap
```

`200` = live, `401` = not. Run it against **every server**. A server that missed
its restart still answers to the old token and looks perfectly healthy, because a
running server never re-reads its credential.

### Afterwards

- Replace the plaintext token in `gitops/argo-install.md` with a pointer to the
  1Password item, and update MANUAL-STEPS §8.
- Note honestly: the old token remains in git history and cannot be removed
  from a public repo in any way that matters. **Rotation is what makes that
  moot** — it is the reason to rotate, not a side effect.

---

## What 2026-09-06 taught us

🔴 **`k3s token rotate` changes two things at different times.**

| what | when |
|---|---|
| datastore bootstrap data, re-encrypted with the new token | **immediately** |
| `cred/passwd` — what `:6443` authenticates against | only when each server **restarts** |

Between those moments the cluster genuinely disagrees with itself: it
authenticates with the old token while storing bootstrap under the new one. A
running server never notices — it read bootstrap once at startup. But a server
that **restarts** in that window dies with:

```
failed to save bootstrap data: bootstrap data already found and encrypted with different token
```

That is what happened: the server converge was run with the new token *before*
rotating, so k8s-3 restarted into a cluster whose datastore had not been rotated
yet. Recovery took a wipe of `/var/lib/rancher/k3s/server`, an etcd member
removal, a node-object delete, and finally rotating the datastore **back** to the
old token.

Three details that cost the most time:

* **`--token` on `k3s token rotate` is the CURRENT token**, because `rotate`
  *authenticates* with it. Passing the new one returns `FATA Error: not
  authorized`, which reads like a wrong password and is really "you gave me the
  token I am rotating *to*".
* **A server with an empty `server/` dir behaves differently from one with data.**
  Empty, it *fetches* bootstrap over HTTPS from a peer (encrypted with that
  peer's in-memory token, so it succeeds). With data, it takes the *save* path
  and must match the datastore. The same node with the same token can pass one
  and fail the other, which is why the errors kept changing.
* **Deleting the node object does not remove the etcd member.** After a wipe the
  node rejoins under a name already registered:
  `etcd cluster join failed: duplicate node name found`. See recovery below.

### Recovering a server stuck mid-rotation

```
# on the broken server
sudo systemctl stop k3s
sudo cp -a /var/lib/rancher/k3s/server /root/k3s-server-backup-$(date +%s)

# which token does the LIVE cluster actually accept? test, do not assume
curl -sk -o /dev/null -w '%{http_code}\n' -u "server:<candidate>" \
  https://<healthy-server>:6443/v1-k3s/server-bootstrap

# write the 200 one, then let it re-fetch bootstrap from the datastore
sudo rm -rf /var/lib/rancher/k3s/server
sudo systemctl start k3s
```

If it then reports `duplicate node name found`, drop the stale etcd member from a
healthy server and start it again:

```
sudo apt-get install -y etcd-client
sudo etcdctl --endpoints=https://127.0.0.1:2379 \
  --cacert=/var/lib/rancher/k3s/server/tls/etcd/server-ca.crt \
  --cert=/var/lib/rancher/k3s/server/tls/etcd/client.crt \
  --key=/var/lib/rancher/k3s/server/tls/etcd/client.key \
  member list -w table
sudo etcdctl ... member remove <ID>
```

⚠️ Removing a member takes 3 servers to 2, so quorum becomes 2-of-2 — **both
remaining servers must stay up** until the rejoin lands.

If the datastore is on a token no server has adopted, the lowest-risk repair is
to rotate it *back* to the token the servers actually hold, which restarts
nothing:

```
sudo k3s token rotate --token '<CURRENT-AUTH-TOKEN>' --new-token '<SAME-VALUE>'
```

---

## Rollback

If nodes fail to rejoin: restore the etcd snapshot taken above, **using the old
token**. That is the entire reason the old token is archived rather than
deleted.
