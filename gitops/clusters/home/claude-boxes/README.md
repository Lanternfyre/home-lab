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

## Where it runs, and the one invariant that is broken

`k8s-edge1`, the public VPS. `kubectl get csinode k8s-edge1` returns an empty
driver list — Trident is Kyverno-blocked there on purpose — so the home volume
is `local-path` with `reclaimPolicy: Delete`, **in violation of CLAUDE.md's
Retain invariant**. The exception is survivable because everything on the
volume is reconstructible; the only real loss is unpushed work, which is bounded
by the box holding a `contents: write` token and being expected to push.

Register the claim under `unprotected:` in `protected-volumes.yaml` or
`audit-protected-volumes.py` fails on a claim in neither list.

⚠️ A `local-path` PV pins itself to the node by nodeAffinity. Moving the box is
*push, delete the PVC, edit the placement block, re-login* — never just editing
`nodeSelector`, which strands the pod `Pending` forever.

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
claude
/login          # prints a URL; the browser shows a CODE to paste back.
                # No loopback callback, which is why a TTY in a pod suffices.
```

Then prove the feature actually works — it takes 30 seconds and it is the
acceptance test:

1. in the session: `echo hello-from-exec`
2. open the browser console → the **same** scrollback
3. ssh in → the same again

Three different scrollbacks means `AGENT_TMUX_SOCKET` is not reaching one of
the doors.

## Gates still open

| what | why it is not settled |
|---|---|
| `toEntities: [kube-apiserver]` from a pod on the edge node | every precedent in this repo is from a home node. Prove with `kubectl get ns` from inside the box plus a Hubble verdict |
| ttyd through the gated Gateway's oauth2 filter | ttyd is a websocket, and oauth2 cookies have blown Envoy's 60 KB header limit here before. Fallback is `homelab` + ttyd's own `-c` credential, which is set either way |
| ssh reachability | the VPN routes only the two Gateway `/32`s — a ClusterIP is not reachable. Either a TCP listener on `homelab` (LAN-wide too) or a pinned ClusterIP plus a NetBird route |

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
