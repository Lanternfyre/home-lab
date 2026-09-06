# Hardening — what `H0`–`H5` mean

**This file exists because roughly 25 manifests reference a plan that was not in
this repository.** Every CiliumNetworkPolicy header carries `H1`, every Kyverno
pod-constraint carries `H3`, and until now `grep -rn "H1\b\|H3\b"` across the
root docs returned nothing that defined them. The definitions lived only in a
session plan file on a workstation.

`CLUSTER-JOIN-HARDENING.md` previously claimed to be the document those tags
referred to. It is not — it never uses the tokens `H1` or `H3` anywhere; its
findings are numbered 1–6 and its programme 1–5. That forward reference was
asserted, never implemented. This file implements it.

Companions: [`CLUSTER-JOIN-HARDENING.md`](CLUSTER-JOIN-HARDENING.md) (the
threat model for a cluster with a public member) and
[`EDGE-NODE.md`](EDGE-NODE.md) (how that member was built).

---

## The markers

| | what | state |
|---|---|---|
| **H0** | **Backups** — CNPG `ScheduledBackup` (barman → object store) for the databases, plus Velero for manifests and the remaining PVs. Two tools, not Velero alone | 🔴 **DEFERRED** by the operator 2026-08-31, re-confirmed 2026-09-06 |
| **H1** | **Default-deny egress, one namespace at a time.** A CiliumNetworkPolicy per namespace naming exactly what that workload may reach | ✅ 9 namespaces. `monitoring` deliberately excluded — see below |
| **H2** | **Pod Security Admission** on every namespace, level measured against the running pods rather than guessed | ✅ 30 namespaces |
| **H3** | **Kyverno pod policies** — what PSA gives up in a `privileged` namespace, asserted explicitly | ✅ all 7 `privileged` namespaces |
| **H4** | **ServiceAccount tokens and RBAC** | 🔴 **OPEN, and there is no plan behind it.** It has never been more than a heading |
| **H5** | **Tetragon** (runtime enforcement / eBPF observability) | deferred, no plan |

---

## Notes that cost real effort — do not re-derive

### H0 — why it is two tools, and what the real blocker is

The three nightly dump CronJobs were removed on 2026-08-15 (`e0c470e`)
**in favour of** Velero, and Velero never landed. `spec.backup` is empty on both
CNPG clusters and there are zero `ScheduledBackup`s.

🔴 **The design question that gates H0 is the destination, not the tool.** Every
in-cluster target — Silo/MinIO, any PVC — sits on the same QNAP that backs every
PV. Same failure domain, therefore not a backup. Decide where it goes before
designing how.

⚠️ **HA is not backup.** Three replicas replicate a `DROP TABLE` faithfully.
⚠️ **A green `audit-protected-volumes.py` means layers 1–3, not backup.** The
script says so itself.
⚠️ **Do not delete the `*-backup` PVCs** (`pg-backup`, `pihole-backup`,
`mealie-backup`, `immich-db-backup`). They are leftovers of the removed
CronJobs and hold the only dumps that exist — cold, and stale since 2026-08-16.
⚠️ `local-path` is the **default** StorageClass, so any PVC created without an
explicit class lands on unreplicated node-local disk.

### H1 — the two failure modes

Cilium's default-deny is **per direction**: a direction with any rule becomes
default-deny, and a direction with no rule stays wide open. That is correct
behaviour and completely invisible in the config — exactly the "control that
looks complete and has a hole nobody can see" shape this repo keeps meeting.
Only 2 of 16 policies set `enableDefaultDeny` explicitly; the rest rely on the
implicit form. **New policies for published namespaces must set it explicitly.**

🔴 `monitoring` is excluded on purpose, and it is not laziness. Prometheus
scrapes every namespace, blackbox probes the LAN, and speedtest-exporter reaches
arbitrary internet hosts on arbitrary ports *by design*. The resulting rule
would be "all cluster + all world", which denies nothing. The real prerequisite
is moving speedtest-exporter to its own namespace; then the rest of monitoring
can take a genuine policy.

⚠️ Narrowing a `fromEntities: [host, remote-node, world]` ingress rule is far
more dangerous than it looks. `mealie-ingress` records eight crashloops of a
healthy app from exactly that change, and a `fromCIDR` allow-list that failed
**silently**. Capture with Hubble under real load before writing any such rule —
and note that an idle capture is worthless for anything auth-related, because
OIDC token exchange only happens during a sign-in.

### H2 — the two lessons already paid for

🔴 **Namespace policy is declared once per namespace**, in
`bootstrap/namespaces/`, synced by home-root — never per app. 42 apps share 28
namespaces (`monitoring` alone is claimed by nine), so per-app labels would mean
nine Applications managing one namespace's posture and having to agree forever.

🔴 **argocd's label is set by the Ansible role, deliberately.** Declaring it in
`bootstrap/namespaces/` would put the namespace ArgoCD runs in under ArgoCD's
own prune scope.

⚠️ Declaring a namespace makes ArgoCD **own** it, so prune applies: deleting one
of those files deletes the namespace and every PVC in it, leaking backend
volumes on `Retain` storage classes.
⚠️ `warn`/`audit` must equal `enforce`. A *higher* warn hides violations of a
lower enforce.
⚠️ A cluster scan cannot see ARC runner namespaces — their pods come from
`AutoscalingRunnerSet` CRs and none run between jobs.

### H3 — what these policies do and do not buy

`privileged` means PSA asserts **nothing**, so each such namespace carries a
ValidatingPolicy asserting what PSA gave up.

⚠️ Denials must come from `ValidatingAdmissionPolicy 'vpol-…'`, not the Kyverno
webhook. That needs the **per-policy** `autogen.validatingAdmissionPolicy.
enabled: true`; without it, enforcement silently falls back to the webhook and
dies with Kyverno.

⚠️ **`serviceAccountName` is not a security boundary** — any pod author in a
namespace may name any SA in it. These policies stop privilege *creep* (chart
bumps, copied blocks, sidecars). **RBAC** stops an attacker, which is H4, which
does not exist yet.

⚠️ Repeated false alarm: a probe-namespace rejection was **twice** a missing
ServiceAccount, not a policy defect. Chase it; do not weaken the rule.

**The method that worked every time:** measure the live workloads → write the
policy → prove it in a scratch namespace with the real pod spec admitted *and*
every violation denied. A policy that never denies is worthless.

### H4 — open, and honestly so

There is no plan. It is the last node in the original dependency graph and the
place where the H3 caveat above points: policies stop privilege creep, RBAC
stops an attacker. Anyone picking this up starts from scratch.

---

## What none of this covers

**Ingress isolation for published services.** The edge Envoy runs `hostNetwork`,
so its traffic carries the *node* identity and is admitted by the same
kubelet-probe rule every namespace needs. At L3/L4 the edge data path and a
kubelet probe are indistinguishable. What bounds a published service is what it
**is** (H3, and the edge-published pod contract) and what it can **reach** (H1)
— never who can talk to it.

**A cluster-admin.** Anyone who can label a namespace can publish through the
edge. That is ArgoCD and this repository, and it is stated rather than hidden.
