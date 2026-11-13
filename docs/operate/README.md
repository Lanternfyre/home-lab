# Operate

Runbooks and standing facts for running the cluster: what can be recovered and how, and what must not be forgotten while you change things. Older operating docs, such as the join-token runbook, still sit at the repository root until they are next edited; the [root README](../../README.md) lists them all.

- [Restoring data](restore.md): what can be restored today, and how.
- [Run one Beads pilot on golf](agent-box-pilot.md): choose Claude or Codex, check prerequisites, and keep window 0 running. golf is parked; bring it back first.
- [Test native NDepend](ndepend-workspace-probe.md): run a disposable identity and activation experiment without changing an agent box.
- [A node will not rejoin after a reboot](node-will-not-rejoin.md): read-only first checks, server and agent paths, and recovery stop points.
- [Give k8s-5's GPU to a CI runner](gpu-runner.md): the NVIDIA driver on k8s-5, the device plugin, the `arc-linux-gpu` runner, the proof that a pod can render, and how to undo each step. Prepared, not yet applied.
- [Let someone upload files into Silo from outside](silo-upload-drop.md): mint an expiring write-only key, what the uploader runs, and collecting the files.
- [Standing warnings](standing-warnings.md): the facts that cause damage when forgotten, and the procedures that go with them: storage, releasing a NodePort, DNS, CI runners, sign-in, Silo, NetBird, the edge game ports, and pairing a device with auq.
