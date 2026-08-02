# Ansible — node configuration as code

Runs from this workstation only; every node is reached over SSH. Nothing is
installed on the nodes beyond normal packages.

## Why this exists

The cluster had zero node IaC. Every node-level step lived as prose in
`gitops/argo-install.md` and was applied by hand — so when `k8s-4` and `k8s-5`
joined on 2026-07-30, three steps were silently missed. The result:

* `k8s-4` cannot resolve DNS, so containerd cannot pull **any** image, so it has
  **zero registered CSI drivers** while sitting `Ready` and schedulable.
* Both new nodes have the wrong `/etc/multipath.conf` and **cannot stage a QNAP
  volume at all** — proven with a test PVC, not theorised.
* `k8s-4` also has `iscsid` inactive.

None of that is visible in `kubectl get nodes`.

## The design rule

**Assert behaviour and values, never file presence.** Two live examples:

| What | Looks fine | Actually |
|---|---|---|
| `/etc/sysctl.d/99-inotify.conf` | present on all 5 nodes | value split across two lines — `max_user_watches` reads **122547**, the kernel default, not the 1048576 the file claims |
| `/etc/multipath.conf` | present on all 5 nodes | missing `find_multipaths no` on lab4/lab5 → Trident fails with *"multipath device not found"* |

Both pass a "does the file exist" check. Neither passes `node_verify`.

## Usage

`site.yml` is the entrypoint. Run it against everything, any time.

```bash
cd ansible                       # ansible.cfg paths are relative to here

# the "plan" -- shows what WOULD change, changes nothing
ansible-playbook site.yml --ask-become-pass --check --diff

# apply
ansible-playbook site.yml --ask-become-pass

# read-only audit; no sudo, no password prompt
ansible-playbook playbooks/90-preflight.yml

# subsets
ansible-playbook site.yml --ask-become-pass --tags storage
ansible-playbook site.yml --ask-become-pass --tags verify      # assertions only
ansible-playbook site.yml --ask-become-pass --limit k8s-4.home
ansible-playbook site.yml --list-tags
```

### There is no state file, and that is the right call

Ansible does not record what ran where. Unlike Terraform there is no ledger --
and that is a feature here, because a ledger can disagree with the machine.
Instead every role **detects the node's effective state and remediates only what
is actually wrong**. "Which node needs which fix" is answered from the node, not
from memory that might be stale.

What that buys you:

* Re-running is safe and cheap; there is nothing to reconcile.
* **The PLAY RECAP is the report.** `changed=0` means that node was already
  correct. That is your "what ran where", derived rather than remembered.
* **A second run immediately after a first should be `changed=0` everywhere.**
  If it is not, some task is not idempotent -- a bug worth chasing, and the
  cheapest test in this whole repo.

Each run also prints a per-node detect line before touching anything:

```
k8s-lab1: DNS=ok, multipath=ok, sysctls=BROKEN, services=ok, liveLUNs=4
k8s-lab4: DNS=BROKEN, multipath=BROKEN, sysctls=BROKEN, services=BROKEN, liveLUNs=0
```

### Deliberately NOT in site.yml

Convergence only. These are separate because they are deliberate operations
with their own verification windows -- folding them in would mean an innocent
"converge" could drain the cluster:

| playbook | what it does |
|---|---|
| `playbooks/30-upgrade.yml` | rolling k3s upgrade (drains + reboots) |
| `playbooks/40-add-node.yml` | join a new node (installs k3s) |

## Setup

```bash
pipx install --include-deps ansible-core
ansible-galaxy collection install ansible.posix community.general
```

`sudo` needs a password on every node, so changing playbooks require
`--ask-become-pass`. Passwordless sudo is available but opt-in and off by
default (`-e configure_nopasswd_sudo=true`) — it plants a standing credential
on every control-plane node.

## Layout

```
ansible.cfg                     roles_path, no host-key checking (lab2/lab3 fail it)
inventory/homelab.yml           .home domains, per-node NIC names
inventory/group_vars/all.yml    k3s_version — the one knob — plus baseline values
roles/node_baseline/            DNS drop-in, sysctls, multipath.conf, packages
roles/node_verify/              the assertions that would have caught lab4 and lab5
playbooks/10-baseline.yml       apply + verify, serial: 1, any_errors_fatal
playbooks/90-preflight.yml      read-only audit
```

## Deliberate non-choices

* **The `/etc/resolv.conf` symlink is not managed.** All five nodes point at
  `stub-resolv.conf` and lab1/2/3 have been healthy that way for 211 days. The
  runbook's symlink step was never applied on any node; changing it now is an
  unverified behaviour change for no observed benefit.
* **No `k3s_agents` group.** All five nodes joined with `k3s server`. An agents
  group would invite a future join to use `INSTALL_K3S_EXEC="agent"`, which
  would be a silent topology change.
* **Purpose-built roles, nothing vendored.** `k3s-io/k3s-ansible` and
  `techno-tim/k3s-ansible` are greenfield installers; several ship a `reset.yml`
  that wipes `/var/lib/rancher/k3s`, and techno-tim's bundles its own kube-vip
  and MetalLB that would collide with the ones already running here.

## Not yet written

`k3s_config` (owns `/etc/rancher/k3s/config.yaml`), `k3s_manifests` (kube-vip
DaemonSet), `30-upgrade.yml` (the rolling upgrade), `40-add-node.yml`.

⚠️ When `k3s_config` lands, remember its scope must include **systemd drop-ins**,
not just `config.yaml`: lab2–lab5 have `--server https://192.168.32.2:6443`
baked into their unit files, and k3s CLI arguments take precedence over the
config file. "Ansible owns config.yaml" is not sufficient to repoint them.
