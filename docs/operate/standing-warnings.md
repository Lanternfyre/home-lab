# Standing warnings: what stays true while you run this cluster

This page is for whoever operates the cluster. It collects the facts that cause damage when they are forgotten, with the few procedures that go with them, grouped by topic. Each entry says how we know it: a check, a measurement or a date.

Terms such as KPR and the storage proof are in the [glossary](../domain/glossary.md). Work that is waiting on the operator is not kept here. Each item is a bead labelled `human`: run `bd human list`, or open the beads board at <https://beads-ui.lab.lanternfyre.dev>.

## Nodes and storage

- **Never uncordon a node until a test PVC mounts on it.** `Ready` is not the gate. k8s-lab4 once sat Ready, untainted and schedulable with zero registered CSI drivers, and any pod with a PVC landing there would have hung in `ContainerCreating`. The rule is enforced: `40-add-node.yml` and `45-change-node-role.yml` keep a node cordoned until the storage proof passes.
- **A node being demoted loses its etcd snapshots**, along with the rest of `/var/lib/rancher/k3s`. `45-change-node-role.yml` takes a fresh snapshot on a peer first, and nothing is copied off the cluster.
- **There are no backups of application data.** The backup CronJobs were deleted on 2026-08-15 in favour of a Velero deployment that never happened. The operator deferred replacing them on 2026-08-31 and re-confirmed on 2026-09-06: an accepted risk, not an oversight. What can still be restored, and how: [Restoring data](restore.md).
- **Never `kubectl patch` the QNAP StorageClasses.** They are chart-managed, and `selfHeal` silently reverts the patch. Change them in git.
- **A QNAP volume freezes for up to ten minutes when the NAS is unreachable.** Every LUN carries `no_path_retry 120` (verified on all 12 LUNs on 2026-08-17), so I/O queues instead of erroring and taking the ext4 journal down with it. The cost: during a partition, pods touching those volumes hang, and a process in uninterruptible sleep cannot be killed, so an outage longer than about ten minutes can leave pods stuck until the node reboots. That is the chosen failure: a ten-minute freeze is recoverable, and an aborted journal cost a week. During such an outage expect the alert `VolumeHealthProbeStale` rather than `VolumeFilesystemStatfsFailing`; both page.
  - Check it, read-only: `cd ansible && ansible-playbook playbooks/90-preflight.yml`. `mpath queueing:` must read `mpathX|120` for every LUN (`-` means unprotected), and `wedged ext4:` must be empty.
  - Re-apply after a node rebuild or a reverted config: `ansible-playbook site.yml --tags storage --ask-become-pass`. It reloads multipathd in place, with no I/O interruption. More in [Ansible](../../ansible/README.md).
- **A volume that still returns `Input/output error` after a confirmed unmount and remount has a damaged filesystem.** The first recovery needs no sudo: get the volume unmounted, confirm it, and let it mount again; the steps are in [Modernization](../../MODERNIZATION.md). They use `kubectl scale`, which selfHeal reverts where ArgoCD owns `replicas`, so check that first. If that is not enough, repair it with root and the volume detached. Get `<mpathX>` from `findmnt -rno SOURCE,TARGET | grep <pvc-uid>` before scaling down, and never run this against a mounted filesystem:
  ```bash
  # with the workload scaled to 0 and the volume confirmed unmounted:
  ssh <node> sudo e2fsck -fy /dev/mapper/<mpathX>
  ```

## Network exposure

- **A LoadBalancer Service would also open a NodePort on every node, the public edge node included.** On 2026-09-07 such ports answered on the edge's public address. Cilium's kube-proxy replacement is on (`kube-proxy-replacement=true` and no kube-proxy DaemonSet, checked 2026-10-25), so NodePorts are handled in BPF before nftables, and no firewall rule on any node can close one: releasing the port is the only fix. The Kyverno policy `loadbalancer-no-nodeports` sets `allocateLoadBalancerNodePorts: false` on every LoadBalancer Service at admission, and every existing allocation was released on 2026-09-07. [`scripts/audit-edge-exposure.py`](../../scripts/audit-edge-exposure.py) reports any that remain; its `--probe` flag also connects to each one on the public address.
- **An allocated NodePort today means the policy missed a Service.** Either the Service was created while Kyverno was down or restarting (its webhooks are torn down during a restart, and `failurePolicy: Ignore` lets the Service through), or the policy is broken. Run the policy check at the end of this section first, then release the port.
- **To release a NodePort that is already allocated, change both fields in one update.** Setting the flag alone keeps the port, and removing the port alone gets it re-assigned while the flag is still `true`. This form keeps the MetalLB address, so no DNS record churns:
  ```sh
  kubectl -n databases patch svc postgres-ha-lb --type=merge -p \
    '{"spec":{"allocateLoadBalancerNodePorts":false,"ports":[{"name":"postgresql","port":5432,"targetPort":5432,"protocol":"TCP"}]}}'
  ```
  A merge patch replaces the whole `ports` list, so a port left out of it is deleted. Print the live list without its node ports, and put that into the patch unchanged; it keeps every port, its protocol and its `targetPort` name:
  ```sh
  kubectl -n <namespace> get svc <name> -o json | jq -c '[.spec.ports[] | del(.nodePort)]'
  kubectl -n <namespace> patch svc <name> --type=merge -p \
    '{"spec":{"allocateLoadBalancerNodePorts":false,"ports":<the list printed above>}}'
  ```
  This is the one legitimate exception to "never `kubectl patch`": `nodePort` is allocated by the API server and declared in no git file, so `selfHeal` has nothing to revert it to. Afterwards, this must print nothing:
  ```sh
  kubectl get svc -A -o json | python3 -c '
  import json,sys
  for s in json.load(sys.stdin)["items"]:
      if s["spec"]["type"] != "LoadBalancer": continue
      np = [p.get("nodePort") for p in s["spec"]["ports"] if p.get("nodePort")]
      if np: print(s["metadata"]["namespace"], s["metadata"]["name"], np)'
  ```
  Then connect to the Service on its LoadBalancer address. `Ready` is not the test; a connection is. Get the address with `kubectl -n <namespace> get svc <name> -o jsonpath='{.status.loadBalancer.ingress[0].ip}'`, then use `nc -zv <address> <port>` for TCP, or a real query for UDP (for Pi-hole's DNS, `dig @<address> techyon.dev`).
- **Check that the policy still mutates** (a server-side dry run creates nothing). It must print `False` and no `nodePort`. `True` means the policy is doing nothing, and with `failurePolicy: Ignore` a broken policy fails silently: look at `kubectl get mpol loadbalancer-no-nodeports -o yaml` and Kyverno's pods, and fix the policy before releasing ports, or the next Service gets a NodePort again.
  ```sh
  kubectl -n default create service loadbalancer np-probe --tcp=8080:8080 \
    --dry-run=server -o json | python3 -c '
  import json,sys; s=json.load(sys.stdin)["spec"]
  print("allocateLoadBalancerNodePorts:", s.get("allocateLoadBalancerNodePorts"))
  print("ports:", s["ports"])'
  ```

## DNS

- **external-dns never deletes a record.** `external-dns-cloudflare` runs `--policy=upsert-only`, so un-publishing a hostname leaves its record behind, and records created by hand (`cloudflared tunnel route dns`) are outside it altogether. The annotation `external-dns.alpha.kubernetes.io/exclude: "true"` did not stop a record being published. Treat any hostname you create as permanent until you delete it by hand in Cloudflare.

## CI runners (ARC)

- **Specialist runner labels are derived from one org variable.** `CI_RUNNER` holds the general label (`arc-linux`), and workflows add a suffix for the rest: `-dind`, `-xl`, `-android`.
- **To fall back to GitHub-hosted runners, delete `CI_RUNNER`. Never set it to `ubuntu-latest`.** That yields `ubuntu-latest-dind`, a label no runner will ever claim: an infinite queue with no error anywhere. Deleting it makes jobs that can run hosted do so, while jobs that need the cluster queue visibly. For the same reason, deleting `CI_RUNNER` is the outage switch, never cleanup.
  ```bash
  gh api -X DELETE /orgs/Lanternfyre/actions/variables/CI_RUNNER
  ```
- **A repo-level `CI_RUNNER` silently wins over the org one**, and would defeat the derivation in exactly one repo. Check before changing it:
  ```bash
  for r in Necronia Necronia2.0 claude-workbench docker-compendium \
           helm-compendium github-actions-compendium home-lab; do
    echo "== $r"; gh variable list -R "Lanternfyre/$r"
  done
  ```
- **`arc-linux-xl` had never taken a job as of 2026-09-16.** Its only consumer is the weekly `mutation.yml`, which no PR can trigger. If its first run queues instead of starting, suspect the scale set, not the suite.

## Sign-in

- **Google matches redirect URIs exactly**, scheme and trailing slash included. Headlamp and authentik's Google source build their callback from the request host, so every hostname they serve needs its own entry on the OAuth client. authentik needs both of these:
  ```
  https://authentik.lab.techyon.dev/source/oauth/callback/google/
  https://authentik.techyon.dev/source/oauth/callback/google/
  ```
  Which one a login uses depends on the application, not on where you are. The NetBird dashboard is LAN-only, but it signs in through the public authentik, so its logins use the public callback.
- **A registry token that stops working fails invisibly.** Until 2026-08-01 a GHCR token the registry rejected silently froze five apps, the storage driver among them, and the ExternalSecret reported `Ready=True` throughout, because it delivered the value correctly. If you give a token an expiry, put a reminder in your calendar.
- **Headlamp has no access of its own.** Its ServiceAccount has no binding, by design, so logging in means pasting a token. The `headlamp-admin` ServiceAccount that minted those tokens was deleted on 2026-08-03 and did not exist on 2026-10-25. Until API-server OIDC replaces the token, recreate it only when you need Headlamp, and delete it again afterwards. `create token` issues a token that expires after an hour:
  ```bash
  kubectl -n kube-system create serviceaccount headlamp-admin
  kubectl create clusterrolebinding headlamp-admin \
    --serviceaccount=kube-system:headlamp-admin --clusterrole=cluster-admin
  kubectl -n kube-system create token headlamp-admin      # paste this into Headlamp
  ```

## Silo (object store)

- **Silo keeps a second copy of its sign-in configuration on its volume**, in MinIO's own config store, outside git. Changing the environment does not clear it. In September 2026 the stored copy still pointed at Google after the move to authentik, so the console waited out a 10-second timeout and offered only the password form. If that happens again, reset the stored copy (the credential never leaves the pod):
  ```bash
  kubectl -n silo exec deploy/silo -- sh -c \
    'export MC_HOST_local="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@127.0.0.1:9000"; \
     mc admin config reset local identity_openid'
  kubectl -n silo rollout restart deploy/silo
  kubectl -n silo rollout status deploy/silo --timeout=180s
  ```
  Then check from inside the pod: `kubectl -n silo exec deploy/silo -- curl -s --max-time 25 -w "\ntotal=%{time_total}s\n" http://127.0.0.1:9001/api/v1/login` must return a redirect rule naming authentik in well under a second. Broken is `{"loginStrategy":"form","redirectRules":null}` after exactly 10 seconds. The egress policy is not the bug and must not be widened to "fix" it.
- **Silo's credentials use letters and digits only** (generator symbols off). Tools interpolate them raw into endpoint URLs, and a `/` in the secret key truncates it silently, which surfaces as `SignatureDoesNotMatch`.
- **The `silo-auth` secret must exist before the chart syncs.** Without it the chart generates its own root credential and regenerates it on some upgrades, and every stored copy elsewhere stops matching at once.
- **CI fails hard when the store is unreachable**, by design. Rotate both sides (1Password and the GitHub org secret) in one sitting.

## VPN (NetBird)

- **Two hostnames, and only one is public.** A VPN client talks to `vpn.techyon.dev` (management, signal and relay) from anywhere. The dashboard is `vpn.lab.techyon.dev`: a LAN address no route from the internet reaches, behind the authentik gate. So administer NetBird from the LAN. Allowing it over the VPN means adding `netbird-ui` to the routing peer's allow-list in `netbird-vpn-access`, and then a bad change costs you the path you would fix it from.
- **There is no STUN or TURN, so every byte goes through edge-1.** Peers cannot discover their public address, so direct peer-to-peer connections mostly do not form, and the relay on the edge node carries the traffic. That is fine for reaching cluster services from a laptop. For bulk transfer between peers it is not; the answer then is to deploy coturn.
- **Rebuilding NetBird needs one 1Password item first.** The item `netbird` must hold `datastore-encryption-key` and `relay-password`, both generated with `openssl rand -base64 32`, before management starts. The setup key is not in 1Password: since 2026-09-07 the reconciler's PostSync job creates it and writes the Secret `netbird/netbird-setup-key`. Until a valid key lands, the routing peer stays NotReady, which is its readiness probe telling the truth.

## Game ports on the edge node (ot-demo)

- `ot-login:7171`, `ot-game:7172` and a UDP echo on `7173` are published straight to the internet through the edge node: `nc edge-1.techyon.dev 7171` connects, with no tunnel and no client tooling.
- **The Open Tibia login server must hand out the public name and the edge port**, `edge-1.techyon.dev:7172`, never a cluster Service name or `127.0.0.1`. That is configured inside the application, not in the manifests, and getting it wrong gives a login that succeeds followed by a world connection that hangs.
- **These ports have no sign-in gate, by design.** OIDC is a browser redirect and cannot run over a raw TCP stream. What bounds a published service is what it is and what it can reach; see [Hardening levels](../../HARDENING.md). Publishing more ports, and why N worlds means about 2N listeners: [Edge node](../../EDGE-NODE.md).

## auq

auq is the question inbox: agent sessions post questions to it, and the operator answers them in the phone app or with the `auq` command-line client. It runs on two LAN hostnames, under `techyon.dev` only (checked 2026-10-25): `auq.lab.techyon.dev` (the broker that holds the questions) and `auq-mobile.lab.techyon.dev` (the Expo dev server that loads the mobile app).

- **Both hostnames skip the sign-in gate, by design.** The gated Gateway would answer the pairing request with a redirect to authentik, which the mobile app cannot follow. The security boundary is auq's own per-device tokens; the full reasoning is at the top of [auq.httproute.yaml](../../gitops/clusters/home/apps/auq/manifests/auq.httproute.yaml).
- **The first pairing code needs a port-forward; `kubectl exec` cannot work.** The endpoint that mints codes trusts only loopback while no device exists, and the server image has no shell to exec into. A port-forward lands in the pod's network namespace, so the server sees `127.0.0.1`. A code is good once, for five minutes:
  ```bash
  kubectl -n auq port-forward deploy/auq-auq-server 5577:5577 &
  curl -s -X POST http://127.0.0.1:5577/api/pairing-codes
  kill %1
  ```
  After the first device exists, any paired client can mint codes for other devices with `auq pair`.
- **The `auq` CLI and the MCP server `auq-mcp` (both from auq-rs) pair the same way as the phone.** There is no self-pair command, and `auq pair` is not it: that mints a code for someone else. A bearer token is a device token. Give each client its own, so unpairing one leaves the others working:
  ```bash
  # 1. Mint a code through the port-forward (or with `auq pair` on a paired client, see below).
  kubectl -n auq port-forward deploy/auq-auq-server 5577:5577 &
  CODE=$(curl -s -X POST http://127.0.0.1:5577/api/pairing-codes | jq -r .code)

  # 2. Trade it for a token, through the real hostname, which also proves the client's path.
  curl -s -X POST https://auq.lab.techyon.dev/api/devices/pair \
    -H 'Content-Type: application/json' \
    -d "{\"code\":\"$CODE\",\"device_name\":\"tarjei-laptop\"}" | jq -r .token
  kill %1

  # 3. Hand it to the client.
  export AUQ_SERVER_URL=https://auq.lab.techyon.dev
  export AUQ_BEARER_TOKEN=<the token from step 2>
  auq watch
  ```
  Step 1 can also be `auq pair` on a client that is already paired, which prints a code and a QR. The port-forward works even before any device exists, but needs kubectl access to the cluster. Steps 1 and 2 need not run on the same machine: carry the code across within its five minutes, and type it in place of `$CODE`. `auq watch` opens the inbox, a terminal UI listing the open questions; it is also what `auq` runs with no subcommand. Export both variables in every shell that runs `auq`, and keep the token out of anything you commit.
- **The token is shown once.** It is stored hashed, and no endpoint shows it again, so put it in 1Password as you read it. Lost means re-pairing.
- **A Claude box never gets the token as an environment variable.** Anything in the workspace container's environment is readable by the agent. Set `device_name` to the box, store the token in a 1Password item, and point the chart's `auqProxy.onePasswordItem` at it: the `auq-proxy` sidecar is the only container that mounts it. Renaming or deleting that item leaves the box stuck in `ContainerCreating` on its next start.
