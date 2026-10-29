# Test native NDepend in a disposable workspace

Use this experiment to check whether NDepend can run inside a non-root agent
workspace with the workstation's identity. It needs an operator with
cluster-admin `kubectl` access. It does not update or restart any agent box.

**Live result: identity and project creation passed; full analysis is still
unverified.** In namespace `ndepend-probe-20261029123829-1192197`, the root init
set the hostname successfully. The reader confirmed the hostname, MAC, .NET
machine name and its own non-root, zero-capability restrictions, then created
an NDepend project. The probe subsequently failed with an empty analysis log.
It incorrectly used `/Silent` while requiring source-parsing evidence in that
log. The revised script retains console output and names each failed analysis
check; it needs another live run to verify the report and parsed source.
This box cannot create namespaces or pods. This is not a production rollout
procedure.

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

## What happens after a successful probe

The prepared [workspace image change](https://github.com/Lanternfyre/docker-compendium/pull/36) copies the
complete `/opt/ndepend` directory from `ghcr.io/lanternfyre/ndepend:latest` at
workspace **build time**, and sets `NDEPEND_CONSOLE`. It copies no activation
and does not execute the NDepend image's entrypoint. Publishing NDepend alone
does not rebuild the workspace; an existing workspace image keeps its bundled
version until rebuilt and rolled out.

Run the same probe again with `--image <published-workspace-preview>` to test
that packaging. Then add the optional identity init container, Cilium
annotation and mounted activation to the `claude-box` chart. A production
namespace policy exception needs a matching admission rule that restricts the
extra capability to that exact init container. Those chart and policy changes
are not included in this experiment. The workspace itself must keep its
current non-root and capability settings.

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
