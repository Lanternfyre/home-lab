# Give k8s-5's GPU to a CI runner

This runbook is for the operator. It makes the NVIDIA Quadro K2200 in k8s-5 (`k8s-lab5`) usable by one GitHub Actions runner, `arc-linux-gpu`, so that Necronia2.0's CI can render Godot frames on a real GPU. Each step gives the command, the check that proves it worked, and how to undo it. Run them in order: each step assumes the one before it passed.

**Status:** prepared on 2026-11-11, **not applied**. It lives on two unmerged branches: `feat/k8s-5-gpu-runner` in home-lab (host, cluster and this page) and `feat/actions-runner-gpu` in docker-compendium (the runner image). Read-only checks of k8s-5 and the cluster are the evidence behind the choices below. The playbook passed `ansible-playbook --syntax-check`, and both charts render with these values under `helm template`. **No step has run on k8s-5 yet.** The X server setup in step 7 is the least certain part: it rests on reading the X.Org and NVIDIA toolkit sources, not on a run on this card.

## What you end up with

- **On k8s-5:** NVIDIA driver 580.178.04 (Canonical's prebuilt kernel module, no DKMS), the NVIDIA Container Toolkit 1.20.1, and `nouveau` blacklisted. The driver, toolkit and kernel metapackages are held, so they move only together, when you move them (see [Moving the driver or the kernel later](#moving-the-driver-or-the-kernel-later)). The node carries the label `nvidia.com/gpu.present=true`.
- **In the cluster:** the NVIDIA device plugin, which advertises `nvidia.com/gpu: 1` on k8s-lab5. The runner scale set `arc-linux-gpu` has at most one runner, pinned to that GPU. An alert, `NoSchedulableGpu`, fires when no node has an allocatable GPU.
- **In GitHub:** a runner group `gpu` that only `Lanternfyre/Necronia2.0` may use.

Three things are not created, on purpose:

- **No RuntimeClass.** k3s already ships one named `nvidia` (handler `nvidia`, 313 days old on 2026-11-11), and k3s adds the matching containerd runtime itself once the toolkit is installed.
- **No GPU Operator.** It manages the driver and the toolkit, and both are host packages here. With those two switched off it would still put Node Feature Discovery on every node, the public edge node included, for no gain.
- **No privileged pod.** The runner gets the GPU from the `nvidia` runtime and its `nvidia.com/gpu` request, so the existing Kyverno policy for the runner namespace admits it unchanged.

Terms used below:

- **Device plugin:** a DaemonSet that tells kubelet how many GPUs a node has, so that pods can request `nvidia.com/gpu`.
- **Scale set** and **listener:** ARC's names for a pool of ephemeral runners and the pod that watches GitHub for its jobs.
- **Runner group:** GitHub's org-level list of which repositories may use a set of runners.
- **nouveau:** the open-source driver that holds the card today.

The [glossary](../domain/glossary.md) covers the cluster's own terms.

## Before you start

- **Has this been applied already?** `ssh k8s-5.home apt-mark showhold` lists `linux-generic` and the `nvidia-*-580` packages once step 2 has run. It lists nothing before then.
- You need: the k8s-5 sudo password, `kubectl` with cluster-admin, `gh` signed in as an org owner, and a checkout of the home-lab branch. Until step 6 merges, run every repo command from the worktree `~/Private/home-lab-gpu`. After that, use your up-to-date `~/Private/home-lab`.
- **Pick a quiet moment.** Step 2 drains and reboots k8s-5. On 2026-11-11 a drain of k8s-5 would have moved nine claude-box sessions, replicas of `postgres-ha`, `immich-db` and `code-health`, `tempo`, and any CI runner in the middle of a job. Their volumes are all `qnap-iscsi`, so they can start elsewhere; the claude-box sessions restart. See what is there now:
  ```bash
  kubectl get pods -A --field-selector spec.nodeName=k8s-lab5 -o wide | grep -v -E 'cilium|metallb|alloy|node-exporter|smartctl|volume-health|trident-node'
  ```
  Wait for any `arc-runners-*` pod on that list to finish its job.

## 1. See the plan

```bash
cd ~/Private/home-lab-gpu/ansible
ansible-playbook playbooks/51-gpu-node.yml --ask-become-pass --check --diff
```

Changes nothing. **Check:** the `detect | report` line reads `card=10de:13ba, module=ABSENT, nouveau=LOADED`, and `plan | report` says `reboot NEEDED`. The toolkit install is skipped in check mode, because its repository does not exist yet. Any other failure means stop.

**Undo:** nothing to undo.

## 2. Install the driver and toolkit on k8s-5

```bash
ansible-playbook playbooks/51-gpu-node.yml --ask-become-pass
```

The playbook installs the packages, writes the `nouveau` blacklist, rebuilds the initramfs and holds the packages. It then drains k8s-lab5 the way `45-change-node-role.yml` does: CNPG primaries first, then every PodDisruptionBudget respected. It reboots the node and checks the driver from the node itself. Only after that check does it label the node. It leaves the node **cordoned**.

**Check.** The play ends with `verify | report`: the card on driver 580.178.04, the loaded module matching it, `nouveau` absent, the packages held, and k3s's containerd config naming `nvidia-container-runtime`. Then confirm by hand:

```bash
ssh k8s-5.home nvidia-smi
ssh k8s-5.home 'sudo grep -n nvidia-container-runtime /var/lib/rancher/k3s/agent/etc/containerd/config.toml'
kubectl get node k8s-lab5 -L nvidia.com/gpu.present     # GPU.PRESENT true, SchedulingDisabled
```

`nvidia-smi` must list `Quadro K2200` with `Driver Version: 580.178.04`.

**If the drain times out:** a PodDisruptionBudget held. The node is cordoned and nothing else has changed. Find the pod with `kubectl get pods -A --field-selector spec.nodeName=k8s-lab5`, deal with it, and run step 2 again. Never add `--force`.

**Undo, back to nouveau** (needs another drained reboot). If step 6 is already merged, undo it first; otherwise `NoSchedulableGpu` fires and GPU jobs queue for ever. A drain blocks on a CNPG primary, so check that none is on the node first. The playbook moves them; this manual path does not.

```bash
kubectl get pods -A -l cnpg.io/instanceRole=primary -o wide | grep k8s-lab5   # must print nothing
kubectl label node k8s-lab5 nvidia.com/gpu.present-
kubectl drain k8s-lab5 --ignore-daemonsets --delete-emptydir-data --timeout=600s
ssh -t k8s-5.home 'sudo apt-mark unhold nvidia-headless-no-dkms-580 nvidia-utils-580 libnvidia-gl-580 \
    xserver-xorg-video-nvidia-580 nvidia-container-toolkit nvidia-container-toolkit-base \
    libnvidia-container-tools libnvidia-container1 linux-generic linux-image-generic \
    linux-headers-generic linux-modules-nvidia-580-generic \
  && sudo apt purge nvidia-headless-no-dkms-580 nvidia-utils-580 libnvidia-gl-580 \
    xserver-xorg-video-nvidia-580 linux-modules-nvidia-580-generic "linux-modules-nvidia-580-$(uname -r)" \
    nvidia-container-toolkit nvidia-container-toolkit-base libnvidia-container-tools libnvidia-container1 \
  && sudo apt autoremove --purge \
  && sudo rm -f /etc/modprobe.d/nvidia-gpu-node-blacklist-nouveau.conf \
       /etc/apt/sources.list.d/nvidia-container-toolkit.list /etc/apt/keyrings/nvidia-container-toolkit.asc \
  && sudo update-initramfs -u -k all && sudo reboot'
```

Read the list `apt autoremove` prints before you confirm it: it also offers old kernels. Then run step 3 to prove storage and uncordon. Also take k8s-5 out of `k3s_gpu` in `ansible/inventory/homelab.yml`, so nobody re-runs the playbook against it.

## 3. Prove storage, then uncordon

```bash
ansible-playbook playbooks/46-prove-node-storage.yml -e target_node=k8s-lab5
```

This is the repo's standing rule: no uncordon until a storage proof passes on the node. A reboot is exactly when multipath, iscsid and the CSI driver must come back by themselves.

**Check:** the play ends with `k8s-lab5 joined, storage-proven and uncordoned.`, and `kubectl get node k8s-lab5` shows `Ready` without `SchedulingDisabled`.

**Undo:** `kubectl cordon k8s-lab5`. If the proof fails, the node stays cordoned. Follow [A node will not rejoin after a reboot](node-will-not-rejoin.md).

## 4. Create the `gpu` runner group in GitHub

The scale set registers in the org-level runner group `gpu`. That group's repository list is what limits it to Necronia2.0. It has to exist before step 6, or the listener cannot register the scale set.

```bash
gh auth refresh -h github.com -s admin:org
gh api -X POST /orgs/Lanternfyre/actions/runner-groups \
  -f name=gpu -f visibility=selected -F 'selected_repository_ids[]=1347528935' \
  -F allows_public_repositories=false
```

`1347528935` is the ID of `Lanternfyre/Necronia2.0` (`gh api repos/Lanternfyre/Necronia2.0 --jq .id`). In the web UI, the same group is under Organization settings, Actions, Runner groups: name `gpu`, repository access "Selected repositories", Necronia2.0 only.

**Check:**

```bash
G=$(gh api /orgs/Lanternfyre/actions/runner-groups --jq '.runner_groups[] | select(.name=="gpu") | .id')
gh api "/orgs/Lanternfyre/actions/runner-groups/$G/repositories" --jq '.repositories[].full_name'
```

It must print only `Lanternfyre/Necronia2.0`.

**Undo**, only after step 6 is undone:

```bash
G=$(gh api /orgs/Lanternfyre/actions/runner-groups --jq '.runner_groups[] | select(.name=="gpu") | .id')
gh api -X DELETE "/orgs/Lanternfyre/actions/runner-groups/$G"
```

## 5. Publish the runner image

Open a pull request from `feat/actions-runner-gpu` in docker-compendium and merge it. The build workflow publishes `ghcr.io/lanternfyre/actions-runner-gpu:latest`. The image is `actions-runner-base` plus Xorg, the Vulkan loader, `vulkan-tools`, `mesa-utils` and the `gpu-display` script. It carries no NVIDIA driver: the runtime injects the host's. The pull request also builds a `pr<N>` preview tag, which step 7 can use before the merge.

**Check:**

```bash
gh api /orgs/Lanternfyre/packages/container/actions-runner-gpu/versions --jq '.[0].metadata.container.tags'
```

**Undo:** nothing uses the image until step 6, so leaving it is harmless.

## 6. Merge the home-lab branch

Open a pull request from `feat/k8s-5-gpu-runner` and merge it into `master`. ArgoCD then creates:

- the `nvidia-device-plugin` namespace and app;
- its Kyverno policy;
- the `arc-lanternfyre-gpu` scale set;
- the `gpu-runner` alert rule.

**Merge only after step 2 has passed.** The alert fires whenever no node has an allocatable GPU.

**Check:**

```bash
kubectl -n argocd get applications nvidia-device-plugin arc-lanternfyre-gpu kyverno-manifests prometheus-manifests
kubectl -n nvidia-device-plugin get pods -o wide                # one nvidia-device-plugin pod, Running, on k8s-lab5
kubectl get node k8s-lab5 -o jsonpath='{.status.allocatable.nvidia\.com/gpu}{"\n"}'   # 1
kubectl -n arc-systems get pods | grep arc-linux-gpu            # the listener, Running
```

The `nvidia-device-plugin-mps-control-daemon` DaemonSet must show `0` desired pods. It runs only on nodes labelled `nvidia.com/mps.capable`, and the Kyverno policy would refuse it anyway.

Prove the new Kyverno policy refuses what it should, and admits the plugin. The plugin pod above already shows the admit. This request must be **denied**, by a `ValidatingAdmissionPolicy 'vpol-…'` message about host namespaces:

```bash
kubectl -n nvidia-device-plugin run kyverno-probe --image=busybox:1.36 --restart=Never \
  --dry-run=server --overrides='{"spec":{"hostPID":true}}' -- true
```

**Undo:** revert the merge commit on `master`. ArgoCD prunes the scale set, the device plugin, the alert and the policy. It also deletes the `nvidia-device-plugin` namespace, which holds no data.

## 7. Prove a pod can render: the test pod

The only check that exercises the whole path: the runtime injects the driver, Xorg starts on it unprivileged, and GL and Vulkan both land on the NVIDIA card. Run it while the scale set is idle, because the pod takes the one GPU. To test before step 5 merges, replace `:latest` with the pull request's `:pr<N>` preview tag.

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: gpu-probe
  namespace: arc-runners-lanternfyre
spec:
  restartPolicy: Never
  automountServiceAccountToken: false
  runtimeClassName: nvidia
  nodeSelector: {nvidia.com/gpu.present: "true"}
  securityContext: {supplementalGroups: [44, 993]}
  imagePullSecrets: [{name: ghcr-pull}]
  containers:
    - name: probe
      image: ghcr.io/lanternfyre/actions-runner-gpu:latest
      env: [{name: NVIDIA_DRIVER_CAPABILITIES, value: all}]
      command: ["bash", "-c"]
      args:
        - |
          set -x
          nvidia-smi
          gpu-display start || { cat /tmp/gpu-display/Xorg.log; exit 1; }
          export DISPLAY=:99
          vulkaninfo --summary
          glxinfo -B
          vkcube --c 300
      resources: {limits: {nvidia.com/gpu: 1, memory: 2Gi}, requests: {cpu: 500m, memory: 512Mi}}
      securityContext: {allowPrivilegeEscalation: false, capabilities: {drop: ["ALL"]}}
EOF
kubectl -n arc-runners-lanternfyre wait --for=jsonpath='{.status.phase}'=Succeeded pod/gpu-probe --timeout=180s \
  || kubectl -n arc-runners-lanternfyre get pod gpu-probe     # Failed or Pending: read why below
kubectl -n arc-runners-lanternfyre logs gpu-probe
```

**Check:** in the log, all five of these must hold. `vkcube --c 300` renders 300 frames into a window on that display and exits, which is the presentation path Godot uses; `vulkaninfo` alone does not test it.

- `nvidia-smi` lists the Quadro K2200.
- `gpu-display` reports `:99 is up` with one connected output.
- `vulkaninfo --summary` shows `deviceName = Quadro K2200` and no `llvmpipe`.
- `glxinfo -B` shows `OpenGL vendor string: NVIDIA Corporation` and `direct rendering: Yes`.
- `vkcube` exits 0.

**If Xorg does not start:** read the `(EE)` lines in the log. "no screens found" with nothing connected means the card reports no output. Retry with the environment variable `GPU_DISPLAY_OUTPUT=none` added to the pod. If that also fails, fit a DisplayPort dummy plug to the card. A "modesetting permission" error means the pod lacks the DRM card node's group: check `ls -ln /dev/dri` on k8s-5 against the pod's `supplementalGroups`.

**Optional: settle the Xvfb question.** In the same pod, add `Xvfb :98 & sleep 2; DISPLAY=:98 vkcube --c 60` after the checks. The expectation is that it fails or falls back to no presentation, because Xvfb has no GPU behind it. If it does work, record that here.

**Undo:** `kubectl -n arc-runners-lanternfyre delete pod gpu-probe`.

## 8. Point the Godot job at it

In Necronia2.0, the job that measures frame time runs on `${{ format('{0}-gpu', vars.CI_RUNNER) }}` (it resolves to `arc-linux-gpu`). It starts the display before Godot:

```yaml
    runs-on: ${{ format('{0}-gpu', vars.CI_RUNNER) }}
    steps:
      - uses: actions/checkout@v4
      - run: gpu-display start          # exports DISPLAY=:99 to later steps
      - run: godot --path client --rendering-driver vulkan --display-driver x11 --audio-driver Dummy …
```

Run it only when the client changes: there is one GPU and no hosted fallback. Turn vsync off in the measuring run, or every frame time reads as the 60 Hz refresh interval.

**Check:** the first run's runner pod lands on k8s-lab5 (`kubectl -n arc-runners-lanternfyre get pods -o wide | grep arc-linux-gpu`). The `gpu-display start` step prints the four-line status.

**Undo:** remove the job, or point it back at its old runner.

## Moving the driver or the kernel later

On k8s-5 the kernel, the NVIDIA kernel module and the driver userspace can only move as one. Canonical's module for each kernel requires a minimum driver version, and the driver pins its own exact version. Unattended upgrades are on for k8s-5 (security pocket only, checked 2026-11-11). They still install other security updates, but not these packages, because they are held. A kernel update takes effect only at a reboot anyway, and a reboot of this node is a drained one.

To move all three to what `noble-security` offers, first read which driver the newest module needs. The `Depends:` line of the newest `Version:` names it, as `nvidia-kernel-common-580 (>= X)`:

```bash
ssh k8s-5.home 'apt-cache show linux-modules-nvidia-580-generic | grep -E "^(Version|Depends):"'
ssh k8s-5.home 'apt-cache madison nvidia-kernel-common-580'
```

If X is newer than `nvidia_driver_version` in `ansible/inventory/group_vars/k3s_gpu.yml`, set that variable to the full version `madison` lists for X, and commit it. Then:

```bash
cd ~/Private/home-lab/ansible
ansible-playbook playbooks/51-gpu-node.yml --ask-become-pass -e nvidia_gpu_move=true
ansible-playbook playbooks/46-prove-node-storage.yml -e target_node=k8s-lab5
```

The playbook is written to refuse a reboot into a kernel whose nvidia module is not installed, and to fail before the drain if apt cannot satisfy the versions. Like the rest of it, that logic has not run yet.

## When 580 ends

580 is the last NVIDIA branch for Maxwell. The 590 series dropped the K2200, and NVIDIA gives 580 critical security updates through October 2028 ([NVIDIA's Quadro support plan for Maxwell, Pascal and Volta](https://nvidia.custhelp.com/app/answers/detail/a_id/5706)). After that the card runs an unpatched driver, and newer kernels may stop building against it. At that point, retire the card or replace it with a Turing or newer GPU, and re-run this page with a new branch in `group_vars/k3s_gpu.yml`.

## If a GPU job queues and nothing fails

A job that no runner can take does not fail: it waits in GitHub's queue with no error. Work down this list.

**Is the GPU gone?** The alert `NoSchedulableGpu` fires after 15 minutes with no allocatable GPU anywhere. It arrives through Pushover like every other alert, and shows on the alerts page of `prometheus.lab.techyon.dev`. Check, in order:

```bash
ssh k8s-5.home nvidia-smi                                         # driver loaded?
ssh k8s-5.home 'cat /proc/driver/nvidia/version; dpkg -l nvidia-kernel-common-580 | tail -1'   # same version?
kubectl -n nvidia-device-plugin get pods -o wide                  # plugin running?
kubectl get node k8s-lab5 -L nvidia.com/gpu.present               # label there? node not cordoned?
cd ~/Private/home-lab/ansible && ansible-playbook playbooks/51-gpu-node.yml --ask-become-pass --tags verify
```

A node that was rejoined (`40-add-node.yml` or `45-change-node-role.yml`) comes back without the label, because k3s applies node labels only at registration. Re-run step 2 after any rejoin.

**Is the GPU taken?** There is one, and a pod that holds it blocks the runner. A leftover `gpu-probe` from step 7 is the likely one:

```bash
kubectl get pods -A -o wide --field-selector spec.nodeName=k8s-lab5 | grep -E 'gpu-probe|arc-linux-gpu'
```

**Is the runner side up?**

```bash
kubectl -n arc-systems get pods | grep arc-linux-gpu                  # the listener, Running
kubectl -n arc-runners-lanternfyre get pods | grep arc-linux-gpu      # a runner Pending? describe it
gh api "/orgs/Lanternfyre/actions/runner-groups/$G/repositories" --jq '.repositories[].full_name'   # $G as in step 4
```

The last command must still list Necronia2.0. Finally, the job's `runs-on` label is composed from the org variable `CI_RUNNER`. If that variable was deleted, or a repo-level one overrides it, the label names no runner; see [Standing warnings](standing-warnings.md#ci-runners-arc).

## Undo everything

Go backwards: remove the job (step 8), delete `gpu-probe` (7), revert the home-lab merge (6), delete the runner group (4), then put k8s-5 back on nouveau (step 2's undo, then step 3). The image from step 5 can stay.
