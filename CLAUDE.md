# home-lab

GitOps repo for a 5-node k3s cluster (ArgoCD app-of-apps) plus the Ansible that
manages the nodes.

## Read these first

| file | what it holds |
|---|---|
| **[MODERNIZATION.md](MODERNIZATION.md)** | **Current state, remaining phases, and hard-won findings. Start here.** |
| [MANUAL-STEPS.md](MANUAL-STEPS.md) | Actions needing a human: credentials, sudo, console, judgement calls |
| [ansible/README.md](ansible/README.md) | Node config as code; why there is no state file |

If work was interrupted, `MODERNIZATION.md` → "Immediately next" is the resume
point. Do not re-derive the "Hard-won findings" section — those cost real effort.

## Layout

```
gitops/clusters/home/apps/<name>/    app.yaml (flat 5 keys) + chart-values.yaml + manifests/
gitops/clusters/home/bootstrap/      the two ApplicationSets and the root Application
ansible/                             site.yml converges; playbooks/ for deliberate ops
scripts/audit-protected-volumes.py   read-only; verifies PV protection + Trident drift
scripts/audit-dashboard-queries.py   read-only; every dashboard expr parsed + metric names checked
scripts/diagnose-node-dns.sh         read-only; diagnoses node DNS, prints a verdict
```

Every app directory generates **two** Applications: `<name>` (Helm) and
`<name>-manifests` (directory). All sync with `ServerSideApply=true`,
`prune: true`, `selfHeal: true`.

## Invariants — violating these causes real damage

- **Never `kubectl patch` chart-managed resources.** `selfHeal` silently
  reverts it and you will believe a change landed when it did not. Change git.
- **PVs are `reclaimPolicy: Retain`.** A deleted test PVC leaks the PV *and*
  the backend volume — clean up with `tridentctl delete volume`.
- **`flannel-backend: none` is not a config change.** In k3s flannel runs
  inside the server process, so setting it removes the CNI. Cleanup phase only,
  all nodes at once.
- **k3s CLI args in the systemd unit take precedence over `config.yaml`.**
  "Ansible owns config.yaml" does not reach anything baked into `ExecStart`.
- **Do not uncordon a node until a storage proof passes on it.** `Ready` is not
  sufficient — k8s-lab4 sat Ready and schedulable with zero CSI drivers.
- **k3s upgrades stop at 1.35.** The QNAP CSI driver declares no 1.36 support.
- **Assert values and behaviour, never file presence.** Three separate bugs
  here were "the file exists and has never worked".

## Conventions

- Ansible: `cd ansible` first (paths in `ansible.cfg` are relative). Changing
  playbooks need `--ask-become-pass`; `90-preflight.yml` needs no sudo.
- Verify against the live cluster rather than assuming — most of this repo's
  history is things that looked correct and were not.
