# Who can reach what

Which authentik group opens which application or Silo policy, where each piece is declared, and the recipe for granting access to something new. Membership is the one thing managed in the authentik UI; everything else is in git.

What is real today: the groups, the `necronia-cli` client and its claim mapping (home-lab #327) and the admin-only bindings (#328) are **in git, applied when those PRs merge**. The browser login in necronia-cli is **Planned**.

## The model

authentik decides **who** may use an application: each application's blueprint binds the groups it admits, and a user outside them gets "Permission denied" before any token exists. Silo decides **what** a signed-in identity may do: the `necronia-cli` client's `minio` scope turns the user's `silo-<policy>` groups into a `policy` claim, and Silo applies those named policies.

## The map

| Group | Opens | In Silo |
|---|---|---|
| `authentik Admins` (built-in) | Grafana (as Admin), ArgoCD, Immich, Kubernetes, the Silo console, the LAN sign-in gate | the console as `consoleAdmin` (role policy) |
| `silo-necronia-assets-rw` | the `necronia-cli` client (login script, later `necronia auth login`) | policy `necronia-assets-rw`: the `necronia-assets` bucket |
| any `silo-*` group | the `necronia-cli` client | the policy named by the suffix |

NetBird admits anyone who can log in and holds them as pending until a NetBird admin approves; it is not bound to a group on purpose, because its reconciler service account logs in through that application.

Where each piece lives:

- groups: `gitops/clusters/home/apps/authentik/manifests/blueprint-groups.configmap.yaml`
- an application's bindings: its own `blueprint-<app>` file, one `policybinding` entry per group
- the `policy` claim and the `silo-*` rule: `blueprint-necronia-cli.configmap.yaml`
- Silo policies: `gitops/clusters/home/apps/silo/chart-values.yaml`

## Grant Silo access to a new bucket or team

1. Add the policy to `chart-values.yaml` (copy `necronia-assets-rw`, change the bucket).
2. Add the group `silo-<that policy name>` to the groups blueprint.
3. Add members in the UI, or point an invitation flow's user-write stage at the group.

No Silo restart, no new OIDC provider, no change to any login script: the claim mapping and the `silo-*` rule already cover the new group.

## Grant a group access to an application

Add the group to the groups blueprint and one `policybinding` entry to that application's blueprint, next to the `authentik Admins` one. If the application maps roles itself (Grafana's `role_attribute_path`, for example), extend that mapping in the same change.

## Expose a new service to the command line

Every CLI login uses the `necronia-cli` client, so a new service fits one of two patterns:

- **It speaks OIDC itself** (Silo, Grafana, ArgoCD): trust tokens for the `necronia-cli` audience, or let it run its own web login, and map groups to its roles in its own configuration.
- **It only speaks HTTP:** publish it on the tunnel behind one Envoy Gateway SecurityPolicy that validates the `necronia-cli` JWT from the `Authorization` header and authorises on the `groups` claim, passing username and groups to the backend as headers. The Phase 6 spike proved `jwt` and `authorization` compose on this gateway, but only with cookie-carried tokens; the first such service starts with a short spike against a canary route.

In both, who may log in is still the application's binding, and what they may do is still their groups.
