# Glossary

The words these docs use for this cluster, each with one meaning. Read an entry when a page uses a term it does not explain; agents read this page before naming anything.

**Contexts:** GitOps (how git becomes the running cluster) · Nodes (the k3s machines and how they join) · Storage (volumes, the NAS, databases) · Network (traffic in and out, the Gateways) · Policy (admission rules and hardening) · Tooling (the operator's and agents' own tools).

**Terms:** [App-of-apps](#app-of-apps) · [auq](#auq) · [Bootstrap data](#bootstrap-data) · [Captured unit files](#captured-unit-files) · [Claude box](#claude-box) · [CNPG](#cnpg) · [cred/passwd](#credpasswd) · [Edge node](#edge-node) · [Hardening levels](#hardening-levels) · [Human bead](#human-bead) · [KPR](#kpr) · [Kyverno](#kyverno) · [MetalLB](#metallb) · [Operator](#operator) · [Protected volume](#protected-volume) · [QNAP](#qnap) · [selfHeal](#selfheal) · [Sign-in gate](#sign-in-gate) · [Storage gate](#storage-proof) · [Storage proof](#storage-proof) · [Trident](#trident) · [VAP](#vap) · [Velero](#velero)

## App-of-apps
- **Context:** GitOps
- **Means:** the ArgoCD layout here: one root Application, `home-root`, syncs `gitops/clusters/home/bootstrap/`, whose ApplicationSets generate two Applications per app directory: `<name>` from the Helm chart and `<name>-manifests` from `manifests/`.
- **Rules:** every Application syncs with ServerSideApply, prune and [selfHeal](#selfheal) → [README](../../README.md#layout)
- **Code:** `home-root.yaml`, `home-appset.yaml`, `home-extra-manifests-appset.yaml`

## auq
- **Context:** Tooling
- **Means:** the question inbox: agent sessions post questions to it, and the operator answers in the phone app or with the `auq` CLI. Served at `auq.lab.techyon.dev`.
- **Not:** auq-rs, which is the client side: the `auq` CLI and the `auq-mcp` server.
- **Rules:** pairing a device → [Standing warnings](../operate/standing-warnings.md#auq)

## Bootstrap data
- **Context:** Nodes
- **Means:** k3s's copy of the cluster's own secrets (certificates, keys and [cred/passwd](#credpasswd)), held in the datastore encrypted with the server token, and as files under `/var/lib/rancher/k3s/server/` on each server.
- **Not:** an etcd snapshot (the whole datastore), or the join token.
- **Rules:** after a token rotation it must be re-saved, or every server's next restart fails → [Cluster join token](../../CLUSTER-TOKEN.md)

## Captured unit files
- **Context:** Nodes
- **Means:** the gitignored folders `_captured/` (from `site.yml`) and `ansible/_captured/` (from `playbooks/`) on the operator's workstation, where Ansible's `k3s_config` role saves each node's k3s unit file and env file before any installer run.
- **Not:** a backup. It is a local copy, and it holds the agents' join token in plaintext.
- **Code:** `ansible/roles/k3s_config/tasks/main.yml`

## Claude box
- **Context:** Tooling
- **Means:** a long-lived Claude Code workspace that runs in the cluster as the StatefulSet `claude-box-<dir>`, one per directory under `gitops/clusters/home/claude-boxes/`. The Lanternfyre boxes are role boxes (operator, spec-writer, oracle-ops, ops, implementers).
- **Rules:** → [Claude Code workspace boxes](../../gitops/clusters/home/claude-boxes/README.md)

## CNPG
- **Context:** Storage
- **Means:** CloudNativePG, the Kubernetes controller that runs this cluster's PostgreSQL: `databases/postgres-ha` (authentik and ReportPortal) and `immich/immich-db`.
- **Rules:** each cluster gets the Services `<cluster>-rw` (read-write: the primary), `<cluster>-ro` (read-only: the replicas) and `<cluster>-r` (any instance); its replicas are not a backup → [Restoring data](../operate/restore.md)
- **Code:** `clusters.postgresql.cnpg.io`, label `cnpg.io/cluster`

## cred/passwd
- **Context:** Nodes
- **Means:** the k3s file `/var/lib/rancher/k3s/server/cred/passwd` on each server: the server token and node passwords k3s accepts. It is part of the [bootstrap data](#bootstrap-data).
- **Rules:** k3s refuses to start when the copy on disk is "newer than datastore" → [Cluster join token](../../CLUSTER-TOKEN.md)

## Edge node
- **Context:** Nodes
- **Means:** `k8s-edge1` (inventory name `edge-1.edge`), the public VPS joined as an agent over WireGuard: the only node that is not trusted infrastructure. It is tainted, so only what tolerates the taint runs there, such as the edge Envoy and Asterisk.
- **Not:** the [sign-in gate](#sign-in-gate), which older docs call the "edge gate".
- **Rules:** → [Edge node](../../EDGE-NODE.md)

## Hardening levels
- **Context:** Policy
- **Means:** the markers H0–H5 that tag manifests: H0 backups (deferred), H1 default-deny egress, H2 Pod Security Admission, H3 Kyverno pod policies, H4 ServiceAccount tokens and RBAC (open), H5 Tetragon (deferred).
- **Example:** every CiliumNetworkPolicy header carries `H1`.
- **Rules:** → [Hardening levels H0–H5](../../HARDENING.md)

## Human bead
- **Context:** Tooling
- **Means:** a bead labelled `human`: a step only the operator can do, such as credentials, sudo, a console or a judgement call. `bd human list` shows them.
- **Not:** agent work. Agents never claim one.
- **Rules:** → [CLAUDE.md](../../CLAUDE.md#beads)

## KPR
- **Context:** Network
- **Means:** Cilium's kube-proxy replacement: Service load-balancing done in BPF instead of kube-proxy's iptables. On (`kube-proxy-replacement=true`, no kube-proxy DaemonSet; checked 2026-10-25).
- **Not:** socket LB, which is one part of it: rewriting a connection's destination when it is opened.
- **Rules:** flipping it resets every established pod connection → [README](../../README.md#invariants--violating-these-causes-real-damage), [Cilium kube-proxy replacement](../../KUBE-PROXY-REPLACEMENT.md)

## Kyverno
- **Context:** Policy
- **Means:** the admission-policy engine. For a validating policy that opts in, it generates a native [VAP](#vap) and drops its own webhook; its mutating policies change objects at admission, such as `loadbalancer-no-nodeports`.
- **Rules:** an object created while Kyverno restarts bypasses its webhooks, and admission only guards creation
- **Code:** `gitops/clusters/home/apps/kyverno/manifests/`

## MetalLB
- **Context:** Network
- **Means:** gives each `LoadBalancer` Service a LAN address, from the pool `lb-pool-32` (192.168.32.10–192.168.32.250).
- **Rules:** a LoadBalancer Service must not allocate NodePorts → [Standing warnings](../operate/standing-warnings.md#network-exposure)

## Operator
- **Context:** Tooling
- **Means:** the person who owns and runs this cluster: answers the agents' questions, does the `human` beads, merges the PRs.
- **Not:** a Kubernetes operator such as [CNPG](#cnpg); call those controllers. Not the `operator` role box, which is an agent working for the operator.

## Protected volume
- **Context:** Storage
- **Means:** a PVC that must survive mistakes. Its PV is `reclaimPolicy: Retain`; both carry `homelab.techyon.dev/protect: "true"`, so deleting them is refused at admission; and the PVC carries Argo's `Prune=false,Delete=false`. "Layers 1–3" in the docs means these three; no page numbers them.
- **Not:** a backup. None of it survives a NAS failure.
- **Code:** `scripts/audit-protected-volumes.py`

## QNAP
- **Context:** Storage
- **Means:** the NAS, a 2-bay TS-231K, behind every persistent volume: iSCSI block volumes (StorageClass `qnap-iscsi`) and SMB shares (`qnap-samba`), provisioned by [Trident](#trident).
- **Rules:** a backup target inside the cluster shares the NAS's failure domain → [Hardening levels H0–H5](../../HARDENING.md)

## selfHeal
- **Context:** GitOps
- **Means:** the ArgoCD automated-sync option that puts a live object back to what git says whenever it drifts. Every Application here has it.
- **Not:** sync on merge. It acts on any drift, including a manual `kubectl patch` or `kubectl scale`.
- **Rules:** change git, never the live object → [README](../../README.md#invariants--violating-these-causes-real-damage)

## Sign-in gate
- **Context:** Network
- **Means:** the authentik sign-in in front of every route on the gated Gateway `homelab-gated`: a request without a session is sent to authentik first.
- **Not:** "edge gate", "authentik gate", "OIDC gate" or a bare "the gate" (operator decision, 2026-10-25). Not the storage gate.

## Storage proof
- **Context:** Nodes
- **Means:** the test that a node can mount storage: a test PVC on `qnap-iscsi`, used by a pod pinned to the node, must mount. Run by `40-add-node.yml` and `45-change-node-role.yml`, or alone by `46-prove-node-storage.yml`.
- **Not:** the storage gate, which is the rule that uses it: a node stays cordoned until its storage proof passes. `Ready` is not proof.
- **Code:** `ansible/playbooks/tasks/prove-and-uncordon.yml`, which runs `storage-proof.yml` and uncordons only if it passes

## Trident
- **Context:** Storage
- **Means:** the CSI driver that provisions [QNAP](#qnap) volumes, deployed from the `qnap-trident` chart in namespace `storage`. `tridentctl` is its CLI.
- **Rules:** a deleted PVC on a `Retain` PV leaks the backend volume; clean it up with `tridentctl delete volume` → [README](../../README.md#invariants--violating-these-causes-real-damage)

## VAP
- **Context:** Policy
- **Means:** a Kubernetes ValidatingAdmissionPolicy: an admission rule the API server evaluates itself, so it holds even while [Kyverno](#kyverno) is down.
- **Example:** the VAP that refuses deleting a [protected volume](#protected-volume).

## Velero
- **Context:** Storage
- **Means:** a Kubernetes backup tool. The backup CronJobs were deleted on 2026-08-15 in favour of it, and it was never deployed.
- **Not:** anything running today. Backups (H0) are deferred.
