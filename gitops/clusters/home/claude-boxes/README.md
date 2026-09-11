# Claude Code workspace boxes

A long-lived Claude Code box that lives in the cluster instead of on a laptop.
One directory per box; `bootstrap/claude-boxes.appset.yaml` turns each into an
ArgoCD Application. **A second box is `cp -r alpha bravo` plus a namespace file
in `bootstrap/namespaces/`** — nothing else changes.

This is the Kubernetes port of the operator's own `claude-box`
(`~/.claude/scripts/workbench/sandbox/`). That is a bubblewrap sandbox with a
host-side GitHub App token minter and a CONNECT-only egress allowlist; here the
mount/netns machinery collapses into a PodSpec and a CiliumNetworkPolicy, and
the minter becomes a sidecar.

## The three properties worth knowing

**One session, three doors.** ttyd in a browser, ssh, and `kubectl exec` all
exec `/usr/local/bin/attach`, which is `tmux new-session -A -s main` on one
socket. The entrypoint creates that session *detached, before any door starts* —
tmux captures its environment at server start, so letting the first ssh login
create it would give every Claude process sshd's stripped environment.

**No durable GitHub secret in the box.** The App PEM is mounted into the broker
sidecar only. It mints ~1h installation tokens into a memory-backed emptyDir,
scoped to the repos in `repos.configmap.yaml`, and a git credential helper
re-reads the file on every git invocation so rotation is invisible.

**Repos are values.** `repos.configmap.yaml` is the list; `seed-repos.sh`
clones what is missing at container start and **never touches an existing
checkout**. Nothing about the repos is in the image.

## Where it runs

A home agent node, scheduler's choice. No `nodeSelector`: the volume is
`qnap-iscsi`, so it follows the pod, and pinning would only create a way for a
drain to strand it. The anti-control-plane affinity is not about capacity --
`k8s-lab1/2/3` carry no taints, and this box clones ~1 GiB of repositories and
runs builds, which on a server node would put that I/O on the same filesystem
as the etcd WAL.

### It used to run on the edge node, and that cost two exceptions

Both are now closed, and the trace is kept rather than deleted:

* **Storage.** `k8s-edge1` has zero registered CSI drivers by design --
  `no-storage-on-edge.validatingpolicy.yaml` keeps the QNAP node plugin off a
  public machine -- so `local-path` was the only class available and
  `reclaimPolicy: Delete` violated CLAUDE.md's Retain invariant. The claim sat
  in `unprotected:` as a written-down exception. On a home node it is
  `qnap-iscsi`, Retain, and lives in `protected:` like everything else.
* **A firewall bug nobody had hit.** The edge's `edge_filter` INPUT chain had
  no rule for the CNI interfaces, so any pod traffic terminating on the host --
  which is what an L7 `rules.dns` redirect is, and therefore every `toFQDNs`
  policy -- was dropped silently. Fixed in #182; it applied to anything ever
  scheduled there, not just this box.

⚠️ **Moving back is not a `nodeSelector` edit.** The volume is the constraint,
not the scheduler: see "Recovery" below.

## The console

`https://box1.lab.techyon.dev`, on the **gated** Gateway.

🔴 That choice is the security decision for the whole feature. ttyd is an
interactive shell next to an Anthropic OAuth credential, a GitHub token with
`contents:write` on seven repositories, and a read-only cluster token. On the
plain Gateway that is a root-equivalent shell for anything reaching the LAN or
the VPN. ttyd keeps its own `-c user:password` underneath regardless -- that is
what still holds if the route is ever attached to the wrong Gateway, which
would otherwise present as a working page rather than an error.

It appears on the homepage dashboard under **Agents**, from the
`gethomepage.dev/*` annotations on the HTTPRoute. There is no central layout to
register a group in: the section exists because the annotation says so, and
dropping the annotations makes it vanish silently.

## Before it can start

Two 1Password items in the `Infrastructure` vault:

| item | fields |
|---|---|
| `Claude Workspace Console` | `ttyd-credential` (literal `user:password`), `ssh-authorized-keys`, `ssh-host-ed25519-key` |
| `Claude Box GitHub App` | `app-id` (4043649), `private-key` (PEM, **with** trailing newline), `installation-lanternfyre` (140425201), `installation-t3chy0n` (140425220) |

⚠️ Not `ARC-GitHub-App` — that is the CI runners' App.

⚠️ No ExternalSecret in this repo has pulled a multi-line value through
1Password Connect before. Prove the PEM survives before assuming the broker is
merely misconfigured:

```sh
kubectl -n claude-box-alpha get secret gh-app -o jsonpath='{.data.private-key}' \
  | base64 -d | openssl rsa -check -noout      # must print "RSA key ok"
```

## First run

```sh
kubectl exec -it -n claude-box-alpha claude-box-alpha-0 -- attach
```

Window 0 already runs Claude -- `--continue`, which resumes the most recent
conversation **in the current directory** (window 0 starts in
`/home/agent/work`; each repo keeps its own history). On a box with no login
yet, run `/login`: the browser shows a CODE to paste back, with no loopback
callback, which is why a TTY in a pod is sufficient.

Then prove the feature actually works -- 30 seconds, and it is the acceptance
test:

1. in the session: `echo hello-from-exec`
2. open `https://box1.lab.techyon.dev` -> the **same** scrollback
3. ssh in -> the same again

Three different scrollbacks means `AGENT_TMUX_SOCKET` is not reaching one of
the doors.

## Recovery, and moving the box

The volume is the constraint. `qnap-iscsi` detaches and re-attaches wherever
the pod lands, so a node drain is now a non-event -- but a move BETWEEN storage
classes still means a new volume, and the login lives on the old one.

```sh
# push anything unpushed FIRST -- it is the only thing on the volume that is
# not reconstructible
kubectl exec -n claude-box-alpha claude-box-alpha-0 -- \
  bash -lc 'for d in ~/work/*/; do git -C "$d" status --porcelain; done'

kubectl -n claude-box-alpha delete pod claude-box-alpha-0   # OnDelete: deliberate
kubectl -n claude-box-alpha delete pvc home-claude-box-alpha-0
# edit the storage class / placement, commit, let Argo sync, then re-login
```

What comes back by itself: the checkouts (seed-repos), `~/.claude` skills and
commands, the npm and uv caches. What does not: the Anthropic login (~2 min to
redo) and anything unpushed.

## Gates still open

| what | why it is not settled |
|---|---|
| ttyd through the gated Gateway's oauth2 filter | ttyd is a websocket, and the filter has to authenticate the upgrade GET then get out of the way. This estate has also seen oauth2 cookies exceed Envoy's 60 KB header limit on gated apps -- it surfaces as `ERR_HTTP2_PROTOCOL_ERROR` or a 431, not a login failure. Fallback is `homelab` plus the ttyd credential, written down rather than silently applied |
| ssh reachability | the VPN routes only the two Gateway `/32`s, so a ClusterIP is not reachable. Either a TCP listener on `homelab` (LAN-wide too) or a pinned ClusterIP plus a NetBird route -- three coupled files |

## Deliberately not done

**Do not port claude-box's `ghu_` device-flow refresh path.** GitHub rotates
the `ghr_` refresh token on every use, so two refreshers — or one restart
racing an in-flight refresh — brick the credential permanently. The
installation mint is stateless and safe to repeat; that is why it is the half
that was ported.

**Do not label this namespace `edge-publish`.** It looks like the right label
for a workload on the edge node and is the opposite: it forbids
`automountServiceAccountToken`, which kills the read-only cluster token, and
requires `readOnlyRootFilesystem`, which a development box cannot satisfy.
