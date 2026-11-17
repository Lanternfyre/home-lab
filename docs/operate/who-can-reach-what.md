# Who can reach what

For the operator granting or removing access: which authentik group opens which application or Silo policy, where each piece is declared, and the recipe for granting access to something new. Group membership, invitations and recovery links are made in the authentik UI; groups, bindings, the gate's claim rule and Silo policies are in git.

Status on 2026-11-17: the groups, the `necronia-cli` client and its claim mapping are in open PR #327, the admin-only bindings in #328, and Silo's claim-mode provider in #330; none is live until merged. The assets repo's login script and the browser login in necronia-cli are Planned.

## Who decides who gets in, and what they may do

authentik decides **who** may use an application: each application's blueprint binds the groups it admits, and a user outside them sees "Permission denied" in the browser, or `invalid_grant` from the token endpoint, before any token exists. Silo decides **what** a signed-in identity may do: the `necronia-cli` client's `minio` scope turns the user's `silo-<policy>` groups into a `policy` claim, and Silo applies those named policies.

Hosts on the LAN sign-in gate (`homelab-gated`: Grafana, Prometheus, Pi-hole, the Silo console and the others listed in `apps/envoy-gateway/manifests/homelab-gated.gateway.yaml`) have a second door: the gate's own `groups` claim rule in that file, and the gate application's binding, both admit only `authentik Admins`. Admitting another group there opens every gated host to it.

## Which group opens what

| Group | Opens | In Silo |
|---|---|---|
| `authentik Admins` (built-in) | Grafana (as Admin), ArgoCD, Immich, Kubernetes, the Silo console, the LAN sign-in gate | the console as `consoleAdmin` (role policy) |
| `silo-necronia-assets-rw` | the `necronia-cli` client (login script, later `necronia auth login`) | policy `necronia-assets-rw`: the `necronia-assets` bucket |
| any `silo-*` group | the `necronia-cli` client | the policy named by the suffix |

NetBird admits anyone who can log in and holds them as pending until a NetBird admin approves; it is not bound to a group on purpose, because its reconciler service account logs in through that application.

Where each piece lives (all under `gitops/clusters/home/`):

- groups: `apps/authentik/manifests/blueprint-groups.configmap.yaml`
- an application's bindings: its own `apps/authentik/manifests/blueprint-<app>` file, one `policybinding` entry per group
- the `policy` claim and the `silo-*` rule: `apps/authentik/manifests/blueprint-necronia-cli.configmap.yaml`
- the gate's claim rule: `apps/envoy-gateway/manifests/homelab-gated.gateway.yaml`
- Silo policies and buckets: `apps/silo/chart-values.yaml`; Grafana's role mapping: `apps/prometheus/chart-values.yaml` (`authentik Admins` → Admin, everyone else → Viewer)

## Grant Silo access to a new bucket or team

1. In `chart-values.yaml`, add the bucket under `buckets:` (`policy: none`) and a policy copied from `necronia-assets-rw` with both resource ARNs changed; set its quota in the pod as in [NecroniaAssets on Silo](necronia-assets-on-silo.md).
2. Add the group `silo-<that policy name>` to the groups blueprint.
3. Add members in the UI, or copy the enrollment flow in `blueprint-necronia-cli.configmap.yaml` under a new slug and new stage names, point its `create_users_group` at the new group, and confirm with the blueprint's `ak shell` check that it is not None.

No Silo restart, no new OpenID provider, no change to any login script: the claim mapping and the `silo-*` rule already cover the new group. One limit: `s3.lanternfyre.dev` serves only `necronia-assets` and must not widen (standing warning), so a team outside the LAN needs a design decision for a second public route before step 1.

## Grant a group access to an application

Add the group to the groups blueprint and one `policybinding` entry to that application's blueprint, next to the `authentik Admins` one. For a host behind the LAN gate, also add the group to the gate's claim rule in `homelab-gated.gateway.yaml` and to the gate application's binding, accepting that this opens every gated host to it. Grafana's own role mapping already gives non-admins Viewer, so a `grafana-viewers` group needs no mapping change.

## Expose a new service to the command line

Every CLI login uses the `necronia-cli` client, so a new service fits one of two patterns:

- **It speaks OIDC itself** (Silo, Grafana, ArgoCD): trust tokens for the `necronia-cli` audience, or let it run its own web login, and map groups to its roles in its own configuration.
- **It only speaks HTTP:** publish it on the tunnel behind one Envoy Gateway SecurityPolicy that validates the `necronia-cli` JWT from the `Authorization` header and authorises on the `groups` claim, passing username and groups to the backend as headers. The Phase 6 spike proved `jwt` and `authorization` compose on this gateway, but only with cookie-carried tokens; the first such service starts with a short spike against a canary route.

In both, who may log in is still the application's binding, and what they may do is still their groups.
