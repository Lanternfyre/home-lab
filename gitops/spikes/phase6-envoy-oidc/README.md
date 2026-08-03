# Phase 6 spike — does Envoy Gateway compose `oidc` + `jwt` + `authorization`?

**Deliberately NOT under `gitops/clusters/home/apps/`, so no ApplicationSet
picks it up.** These manifests are applied by hand and deleted afterwards. A
spike that quietly becomes permanent infrastructure is how you end up with the
`headlamp-admin` problem — live objects nobody declared.

```bash
kubectl apply -f gitops/spikes/phase6-envoy-oidc/
# ... test ...
kubectl delete -f gitops/spikes/phase6-envoy-oidc/
```

## Why a spike at all

`MODERNIZATION.md` records that **no upstream e2e test composes these three
filters**. The API supports it — verified against the live v1.8.3 CRD schema,
which has `oidc.cookieNames`, `jwt.providers[].extractFrom.cookies`, and
`authorization.rules[].principal.jwt.claims[]`. Whether it *works* is the
question, and it must be answered before 15 Ingresses depend on it.

## The backend is a black hole, on purpose

`spike-sink` is a Service with **no selector and no endpoints**. Envoy routes
to an empty cluster and returns **503**.

That makes 503 the "you got all the way through" signal, and it leaks nothing.
The alternative — pointing the spike at a real app — risks serving that app
unauthenticated on the LAN for as long as it takes to notice the SecurityPolicy
failed to attach. There is no reason to take that risk to read a status code.

## Reading the results

| response | meaning |
|---|---|
| **302** to accounts.google.com | `oidc` filter is engaged |
| **503** | authenticated AND authorized — reached the (empty) backend |
| **403** | authenticated but the `authorization` rule rejected the claim |
| **200** | ⚠️ should be impossible; means you are hitting something else |

## ⚠️ Test order matters, and the obvious order is wrong

`MODERNIZATION.md` says the decisive check is "log in with a `@gmail.com`
account and get 403". **That test cannot be run here**: the Google consent
screen is `orgInternalOnly: true`, so a `@gmail.com` account cannot complete
the flow at all. Google refuses before any token exists, the `authorization`
rule is never reached, and a missing or misspelled rule would look identical to
a working one.

So instead, prove it with one account and an inversion — and in **this order**:

1. **`hd == techyon.dev`** (as shipped) → sign in → expect **503**.
   Proves the whole chain: oidc redirect, callback, cookie, JWT extraction from
   that cookie, claim match, allow.
2. **Flip to `hd == nonexistent.example`**, clear cookies, sign in again →
   expect **403**. Proves the rule is actually *evaluated* rather than being
   trivially satisfied.

Doing (2) first is ambiguous: a 403 would also be produced by a completely
broken JWT filter, since no claims means no match means `defaultAction: Deny`.
Only (1)-then-(2) distinguishes "the rule works" from "nothing works".

## Manual prerequisite

Add `https://spike.lab.techyon.dev/oauth2/callback` to the Google OAuth client
before testing.

⚠️ `external-dns-cloudflare` runs `--policy=upsert-only`, so the DNS record for
`spike.lab.techyon.dev` will **survive** deleting these manifests. Remove it in
Cloudflare by hand afterwards.

⚠️ Do not resolve `spike.lab.techyon.dev` until external-dns has published it —
Pi-hole caches an NXDOMAIN for 1800s and the spike will look broken when it is
not. See the negative-caching finding in `MODERNIZATION.md`.
