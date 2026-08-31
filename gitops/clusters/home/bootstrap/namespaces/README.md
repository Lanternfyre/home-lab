# Namespace policy, declared once per namespace

Every namespace this repo owns is declared here, with its Pod Security Admission
level. `home-root` syncs `bootstrap/` recursively, so these are picked up
automatically and owned by a single Application.

## Why here, and not per app

**A namespace's security posture is a property of the namespace, not of each app
that happens to live in it.** 42 apps share only 28 namespaces:

| namespace | apps |
|---|---|
| `monitoring` | 9 |
| `dns` | 3 |
| `arc-runners-lanternfyre` | 3 |
| `opensearch`, `secrets` | 2 each |

Putting the labels in each app — whether as a `namespace.yaml` in the app's
manifests, or via `managedNamespaceMetadata` on the ApplicationSet — means nine
Applications each managing `monitoring`'s labels and having to agree. Add a
tenth app later, forget the label, and the namespace silently drops to a level
that stops node-exporter from starting. One declaration, one owner, no
coordination problem.

## The levels were measured, not guessed

Every running pod was checked against the actual PSA rules:

- **restricted** — already non-root, ALL capabilities dropped,
  `allowPrivilegeEscalation: false`, seccomp `RuntimeDefault`.
- **baseline** — nothing privileged, no host namespaces, no hostPath, no
  unconfined profiles, but capabilities not dropped, so `restricted` would
  reject them.
- **privileged** — measured violations that cannot be removed. Each file says
  which, and why.

## ⚠️ Traps, all of them already paid for in this repo

1. **`warn` and `audit` are set to the SAME level as `enforce`.** PSA reports
   only at the level asked for, so a *higher* `warn` HIDES violations of a lower
   `enforce` — exactly how buildkit's baseline violation stayed invisible until
   it could not create a pod.
2. **PSA enforces at POD creation, not on the workload template.**
   `kubectl apply --dry-run=server` on a Deployment exercises audit/warn only.
   The acceptance test is a real pod.
3. **A cluster scan cannot see the ARC runner namespaces.** Their pods come from
   `AutoscalingRunnerSet` CRs, not Deployments, and none were running when this
   was measured. `arc-runners-*` levels come from chart values instead.
4. **`privileged` means PSA asserts nothing.** For those namespaces the
   properties that matter — not privileged, capabilities dropped, runAsNonRoot —
   have to be asserted by Kyverno instead (H3). A `privileged` label with
   nothing behind it is a genuine hole.

## ⚠️ These namespaces become ArgoCD-owned

Declaring a namespace means ArgoCD manages it, which means `prune` applies: if a
file here is deleted, **the namespace goes with it**, and with it every PVC
inside. On this cluster's `Retain` storage classes that also leaks the backend
volumes. Removing a namespace from this directory is a deliberate, destructive
act — not cleanup.
