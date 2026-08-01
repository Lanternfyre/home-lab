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

```bash
cd ansible                       # ansible.cfg paths are relative to here

# read-only audit; no sudo, safe any time, run it FIRST
ansible-playbook playbooks/90-preflight.yml

# review what would change, then apply
ansible-playbook playbooks/10-baseline.yml --ask-become-pass --check --diff
ansible-playbook playbooks/10-baseline.yml --ask-become-pass

# scope to one node
ansible-playbook playbooks/10-baseline.yml --ask-become-pass --limit k8s-4.home
```

Expect `--check` to show only the inotify sysctl fix on `k8s-1/2/3`, plus
multipath.conf and DNS on `k8s-4/5`. Once applied, a re-run should be a clean
no-op — **that no-op is the real gate**, because it proves the roles describe
reality rather than merely having been run once.

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
group_vars/all.yml              k3s_version — the one knob — plus baseline values
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
