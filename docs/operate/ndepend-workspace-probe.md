# Test native NDepend in a disposable workspace

Use this experiment to check whether NDepend can run inside a non-root agent
workspace with the workstation's identity. It needs an operator with
cluster-admin `kubectl` access. It does not update or restart any agent box.

**Live result: passed on 2026-10-29.** The operator ran workspace preview
`ghcr.io/lanternfyre/claude-workspace:0.1.22-pr36.1.g5d75364` in namespace
`ndepend-probe-20261029124924-1194591`. The reader confirmed hostname `TarjeiPC`,
MAC `50:eb:f6:77:84:3a`, the .NET machine name and its own non-root,
zero-capability restrictions. NDepend created the project, parsed source and
produced nonempty HTML and `.ndar` output with analysis exit code 0. The probe
then deleted its namespace. Native NDepend works with this image and activation;
this does not yet verify Necronia's source, coverage or quality gates.
This is not a production rollout procedure.

## Before running it

The cluster must have Cilium, External Secrets and the existing `one-password`
ClusterSecretStore. The store already selects the `Infrastructure` vault.
The operator confirmed this item layout:

| Item | Field | Format |
|---|---|---|
| `Ndepend` | `ActivationLic` | Base64 of the workstation's `~/.config/NDepend/ActivationLic` file |
| `Ndepend` | `H` | Literal contents of the workstation's `~/.config/NDepend/H` file, the hardware-ID marker used by the existing CI image |
| `GHCR` | `password` | Existing registry credential, used only by kubelet |

Use the **existing CI identity values**, not a newly selected network interface.
The expected hostname is `TarjeiPC`, recorded in `NDEPEND_FP_HOSTNAME`. Get the MAC from the GitHub organization
variable `NDEPEND_FP_MAC`; this requires your personal GitHub CLI credentials.
The agent's GitHub integration cannot read organization variables.

## Run the probe

From this repository's root in Bash or WSL:

```bash
ndepend_probe_mac=$(gh variable list --org Lanternfyre --json name,value \
  --jq '.[] | select(.name == "NDEPEND_FP_MAC") | .value')
bash scripts/probe-ndepend-workspace.sh --mac "$ndepend_probe_mac" --render
bash scripts/probe-ndepend-workspace.sh --mac "$ndepend_probe_mac"
```

The first invocation prints the manifests without contacting Kubernetes. The
second creates a unique `ndepend-probe-...` namespace. Missing or malformed MAC
values fail before any cluster changes. If GitHub CLI cannot read the variable,
copy its value from the organization's
[Actions variables page](https://github.com/organizations/Lanternfyre/settings/variables/actions)
and pass it directly to `--mac`.
`--hostname` overrides `TarjeiPC` if the CI variable has changed.

A GitHub administrator account can still get HTTP 403 here: the CLI credential
also needs permission to read organization variables. Classic/OAuth tokens need
`admin:org`; fine-grained tokens need organization Variables read permission.
An exported `GH_TOKEN` or `GITHUB_TOKEN` overrides the account stored by `gh auth
login`. Reading the value in the browser avoids changing CLI token permissions.
An empty MAC from a failed lookup stops the probe before creating a namespace.

The namespace temporarily allows the `SYS_ADMIN` capability required by
`hostname`. Only the one-shot `identity` init container receives it and runs as
UID/GID 0, with every other capability dropped. That init container has no
volume mounts or service-account token and exits before the reader starts.
The reader runs as UID 1000, with all capabilities dropped,
privilege escalation disabled and a read-only root filesystem. It explicitly
tests that changing its hostname is denied.

The pod uses Cilium's `cni.cilium.io/mac-address` annotation. Cilium isolates
that MAC to the pod interface; it does not replace the node's physical MAC.
See [Cilium's explanation](https://docs.cilium.io/en/stable/network/pod-annotations/).
The hostname experiment follows containerd 2.2.5's
[shared UTS namespace implementation](https://github.com/containerd/containerd/blob/v2.2.5/internal/cri/opts/spec_opts.go).
Neither source substitutes for the live test.

All writable files, including the activation copy, use memory-backed temporary
volumes. The pod mounts no workspace, host directory or persistent volume. Its
network policy allows DNS and the public NDepend endpoints only; no general
internet or internal service access is granted. Image pulls use kubelet's
registry credential and do not require pod egress to GHCR.

## Read the result

Success prints each of these checks and then deletes the temporary namespace:

- The shell sees the expected hostname and MAC, UID 1000, no capabilities and
  `no_new_privs=1`.
- .NET reports the expected `Environment.MachineName`.
- NDepend creates a project using the supplied activation.
- A sample analysis parses source and produces an HTML report and `.ndar`.

The sample has no test coverage. Its default quality gates can return 1; this
does **not** count as Necronia passing. Real source, coverage and gate enforcement
still need testing against Necronia after the workspace integration is ready.
The script never registers a trial or re-registers the license.

While waiting, the script prints pod status changes and a status update about
every 30 seconds. An image-pull or container-configuration error stops the wait
early and prints the reason. The analysis prints its exit code and identifies
any missing report, analysis result or source-parsing evidence.

On failure, the script prints console diagnostics and pod events and retains
the namespace. Send the printed namespace and PASS/FAIL output to the agent;
do not send the secret contents. The final line gives the cleanup command for
that exact namespace. `--keep` also retains a successful probe for inspection.
The probe refuses to adopt an existing namespace and never deletes one that it
did not create. Its pod has a five-minute active deadline.

## Alpha rollout

The merged [workspace image change](https://github.com/Lanternfyre/docker-compendium/pull/36) copies the
complete `/opt/ndepend` directory from `ghcr.io/lanternfyre/ndepend:latest` at
workspace **build time**, and sets `NDEPEND_CONSOLE`. It copies no activation
and does not execute the NDepend image's entrypoint. Publishing NDepend alone
does not rebuild the workspace; an existing workspace image keeps its bundled
version until rebuilt and rolled out.

The successful probe tested that workspace packaging. Release image `0.1.22`
used the same NDepend base digest as the successful preview, confirmed in the
[release build](https://github.com/Lanternfyre/docker-compendium/actions/runs/40877883471)
and [preview build](https://github.com/Lanternfyre/docker-compendium/actions/runs/40870040374).
The optional
[chart change](https://github.com/Lanternfyre/helm-compendium/pull/60) is now
published in chart `0.1.20`, with `ndepend.enabled: false` by default. When enabled, a completed
hostname init runs before the credential sidecars. A second, non-root init
checks identity and copies activation into a memory-backed directory mounted
at `/home/agent/.config/NDepend`. No activation is written into the home PVC.
Replacing the pod refreshes this copy after a 1Password change. The workspace
keeps its current non-root and capability settings.

The installed
[admission rule](../../gitops/clusters/home/apps/kyverno/manifests/claude-box-ndepend.validatingpolicy.yaml)
covers only `claude-box-alpha`, including debug containers. It limits the
hostname exception to `ndepend-identity` in `claude-box-alpha-0`, with an explicit
workspace image pin, exact hostname command, no mounts or environment
credentials, and only `SYS_ADMIN`. Other containers remain unprivileged.
Argo reported the preparation synced, but that alone does not prove admission
enforcement. Before merging the rollout, check the ValidatingPolicy status and
its generated ValidatingAdmissionPolicy and binding with an admin kubeconfig:
the policy must have no type-check warnings and its binding must use `Deny`
and select alpha. This box's service account cannot read those objects.

**The alpha rollout changes the running pod.** It selects chart `0.1.20`, image
`0.1.22`, `ndepend.enabled: true`, hostname `TarjeiPC` and the existing CI MAC.
It also changes the admission rule's image pin to `0.1.22` and alpha's PSA
enforcement to `privileged`, retaining restricted audit/warn labels. The other
boxes keep their existing pins and policies. Alpha uses `RollingUpdate`, so
merging these values ends its active sessions as Argo replaces the pod.

The namespace, policy and workspace are reconciled by separate Argo
applications. A new pod may briefly be denied until the updated admission
image pin and namespace label have both synced; inspect pod events and those
applications before manually restarting anything. Do not relax PSA if the
existing admission rule is absent or unverified.

After the rollout, verify the `ndepend-identity` and `ndepend-activation` init
containers completed, the workspace is Ready, `hostname` returns `TarjeiPC`,
and `NDEPEND_CONSOLE` points to the bundled console. Read neither activation
file into logs. Rollback reverts the alpha values/chart pin and restores
restricted enforcement; keep the admission rule until the replacement pod is
restricted again. If the new pod never became Ready, first confirm the reverted
StatefulSet template has synced. A StatefulSet can remain stuck after that
revert; if it does, delete only the failed `claude-box-alpha-0` pod so the
controller recreates it from the reverted template. Retain its PVC. See
[Kubernetes forced rollback](https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/#forced-rollback).
Any rollback replacement also ends alpha's sessions.

Necronia2.0 can then use its existing [verification script](https://github.com/Lanternfyre/Necronia2.0/blob/main/build/verify.sh)
with `build/verify.sh --ndepend`. Its CI currently pins the
NDepend image by digest, so check the bundled version when comparing local and
CI results. `latest` at build time does not guarantee equality with that pin.

## Local checks

```bash
python3 -B -m unittest discover -s scripts -p test_probe_ndepend_workspace.py -v
bash -n scripts/probe-ndepend-workspace.sh
```

The tests need PyYAML. They check input validation, the rendered security and
secret configuration, and behavior when a namespace already exists, a secret
fails to synchronize, an image cannot be pulled, or a run succeeds. A simulated
console checks that success requires both artifacts and source-parsing evidence,
including when the sample returns the quality-gate exit code 1. They mock
Kubernetes and NDepend; none claims to prove live activation or networking.

For the admission rule, `scripts/test_ndepend_admission_policy.py --pod
<rendered-alpha-pod.yaml>` uses the real Kyverno CLI to evaluate the enabled
chart pod and mutations of it. Supply a Pod extracted from the chart's
StatefulSet template, with name `claude-box-alpha-0` and namespace
`claude-box-alpha`; the test checks the rule's namespace scope too. This needs
Kyverno 1.18.2 (or a compatible CLI) and PyYAML. It performs no cluster writes
and does not replace API-server admission tests before rollout.
