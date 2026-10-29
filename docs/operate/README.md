# Operate

Runbooks and standing facts for running the cluster: what can be recovered and how, and what must not be forgotten while you change things. Older operating docs, such as the join-token runbook, still sit at the repository root until they are next edited; the [root README](../../README.md) lists them all.

- [Restoring data](restore.md): what can be restored today, and how.
- [Run one Beads pilot on golf](agent-box-pilot.md): choose Claude or Codex, check prerequisites, and keep window 0 running.
- [Test native NDepend](ndepend-workspace-probe.md): run a disposable identity and activation experiment without changing an agent box.
- [A node will not rejoin after a reboot](node-will-not-rejoin.md): read-only first checks, server and agent paths, and recovery stop points.
- [Standing warnings](standing-warnings.md): the facts that cause damage when forgotten, and the procedures that go with them: storage, releasing a NodePort, DNS, CI runners, sign-in, Silo, NetBird, the edge game ports, and pairing a device with auq.
