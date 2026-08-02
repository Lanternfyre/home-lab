#!/usr/bin/env bash
# Diagnose why a k3s node cannot resolve DNS, and print a verdict.
#
# READ-ONLY. Inspects state and dumps rules; changes nothing.
#
# Run it against a node WITHOUT copying it there first:
#
#     ssh 192.168.33.21 'sudo bash -s' < scripts/diagnose-node-dns.sh
#
# or, if you are already on the node:
#
#     sudo bash diagnose-node-dns.sh
#
# Needs root only for iptables-save. Everything else works unprivileged.

set -uo pipefail

bold=$'\033[1m'; red=$'\033[31m'; grn=$'\033[32m'; ylw=$'\033[33m'; dim=$'\033[2m'; rst=$'\033[0m'
[ -t 1 ] || { bold=""; red=""; grn=""; ylw=""; dim=""; rst=""; }

say()  { printf '%s\n' "$*"; }
hdr()  { printf '\n%s== %s ==%s\n' "$bold" "$*" "$rst"; }
ok()   { printf '  %sOK%s   %s\n' "$grn" "$rst" "$*"; }
bad()  { printf '  %sBAD%s  %s\n' "$red" "$rst" "$*"; }
warn() { printf '  %s??%s   %s\n' "$ylw" "$rst" "$*"; }

say "${bold}DNS diagnosis for $(hostname)${rst}  ${dim}$(date -u '+%F %T UTC')${rst}"

# ── 1. Does resolution work, and at which layer does it break? ───────────────
hdr "resolution"
if getent ahostsv4 registry.k8s.io >/dev/null 2>&1; then
  ok "getent resolves registry.k8s.io -- DNS is working, nothing to fix"
  GETENT=1
else
  bad "getent CANNOT resolve registry.k8s.io (this is what containerd uses)"
  GETENT=0
fi

# resolvectl talks to systemd-resolved over D-Bus/varlink, NOT through the
# 127.0.0.53 stub socket. If this works while getent fails, resolved's engine
# is fine and the problem is the stub being unreachable.
if command -v resolvectl >/dev/null && timeout 6 resolvectl query registry.k8s.io >/dev/null 2>&1; then
  ok "resolvectl resolves (bypasses the stub socket)"
  RESOLVECTL=1
else
  warn "resolvectl also fails -- upstream may genuinely be unreachable"
  RESOLVECTL=0
fi

# ── 2. Is the stub socket bound, and is it reachable? ────────────────────────
hdr "stub listener (127.0.0.53:53)"
# NOTE: it binds as "127.0.0.53%lo:53". Grepping for "127.0.0.53:53" MISSES it
# -- a mistake worth not repeating.
if ss -lntu 2>/dev/null | grep -q '127\.0\.0\.53'; then
  ok "socket is BOUND"
  BOUND=1
else
  bad "socket is NOT bound -- check DNSStubListener in resolved.conf"
  BOUND=0
fi

probe() {  # probe <ip> <port> <label>
  if timeout 4 bash -c "cat < /dev/null > /dev/tcp/$1/$2" 2>/dev/null; then
    ok "$3 reachable"; return 0
  else
    bad "$3 REFUSED"; return 1
  fi
}
probe 127.0.0.53 53 "127.0.0.53:53 (resolved stub)"; STUB_OK=$?
probe 127.0.0.54 53 "127.0.0.54:53 (resolved extra)"; EXTRA_OK=$?
probe 127.0.0.1  22 "127.0.0.1:22   (loopback sanity)"; LO_OK=$?

# ── 3. Is something hijacking port 53? ───────────────────────────────────────
hdr "port 53 interception"
if [ "$(id -u)" -ne 0 ]; then
  warn "not root -- rerun with sudo to inspect iptables"
  HIJACK=-1
else
  RULES=$(iptables-save 2>/dev/null)
  # k3s ServiceLB (klipper) pods take hostPort 53. The CNI portmap plugin then
  # DNATs ANY local destination on :53 to the klipper pod -- including
  # 127.0.0.53. If that pod is not running, the DNAT points nowhere and every
  # :53 connection on the node is refused, including the resolver's own stub.
  HOSTPORT=$(printf '%s\n' "$RULES" | grep -E 'CNI-HOSTPORT|CNI-DN-' | grep -E 'dpt:53|--dport 53' || true)
  KUBEDNAT=$(printf '%s\n' "$RULES" | grep -E 'KUBE-' | grep -E 'dpt:53|--dport 53' | head -20 || true)
  REJECT=$(printf '%s\n' "$RULES" | grep -E 'REJECT|DROP' | grep -E 'dpt:53|--dport 53' || true)

  if [ -n "$HOSTPORT" ]; then
    bad "CNI hostPort DNAT rules for port 53 exist:"
    printf '%s\n' "$HOSTPORT" | sed 's/^/        /'
    HIJACK=1
  else
    ok "no CNI hostPort DNAT for port 53"
    HIJACK=0
  fi
  if [ -n "$REJECT" ]; then
    bad "explicit REJECT/DROP rules for port 53:"
    printf '%s\n' "$REJECT" | sed 's/^/        /'
    HIJACK=1
  fi
  if [ -n "$KUBEDNAT" ]; then
    say "  ${dim}kube-proxy rules touching :53 (informational)${rst}"
    printf '%s\n' "$KUBEDNAT" | head -8 | sed 's/^/        /'
  fi
fi

# ── 4. Who wants port 53 on this node? ───────────────────────────────────────
hdr "klipper / ServiceLB pods claiming hostPort 53"
if command -v crictl >/dev/null 2>&1 && [ "$(id -u)" -eq 0 ]; then
  crictl ps -a 2>/dev/null | grep -iE 'svclb|klipper' | head -6 | sed 's/^/  /' || say "  none found"
else
  say "  ${dim}(needs root + crictl; check from the workstation with:${rst}"
  say "  ${dim} kubectl -n kube-system get pods -o wide | grep svclb)${rst}"
fi

# ── VERDICT ──────────────────────────────────────────────────────────────────
hdr "VERDICT"
if [ "$GETENT" -eq 1 ]; then
  say "  ${grn}DNS is working on this node. Nothing to do.${rst}"
elif [ "${HIJACK:-0}" -eq -1 ]; then
  # Not root: we have the symptom but not the cause, and saying
  # "inconclusive" here is unhelpful -- be explicit about what is missing.
  say "  ${ylw}Symptom captured, cause NOT determined -- this needs root.${rst}"
  if [ "$BOUND" -eq 1 ] && [ "$STUB_OK" -ne 0 ]; then
    say "  The stub is bound yet refuses connections, and plain loopback works."
    say "  That means traffic to :53 is being intercepted before it arrives --"
    say "  but only iptables can say by what."
  fi
  say ""
  say "  Re-run as root. Note that 'ssh host sudo bash -s < script' does NOT"
  say "  work: sudo wants a password from the terminal, but stdin is the"
  say "  script. Use one of:"
  say "    cd ansible && ansible <host> -b --ask-become-pass -m script -a '../scripts/diagnose-node-dns.sh'"
  say "    scp scripts/diagnose-node-dns.sh <host>:/tmp/ && ssh -t <host> 'sudo bash /tmp/diagnose-node-dns.sh'"
elif [ "$LO_OK" -ne 0 ]; then
  say "  ${red}Loopback itself is broken${rst} -- this is not a DNS problem."
elif [ "$BOUND" -eq 1 ] && [ "$STUB_OK" -ne 0 ] && [ "${HIJACK:-0}" -eq 1 ]; then
  say "  ${red}CONFIRMED: port 53 is intercepted by firewall/DNAT rules.${rst}"
  say "  The resolver stub is bound but unreachable because traffic to :53 is"
  say "  redirected before it arrives. If the rules point at a klipper/svclb"
  say "  pod that is not running, nothing answers and every lookup fails."
  say ""
  say "  FIX: disable k3s ServiceLB (MetalLB is the real LoadBalancer here):"
  say "    cd ansible && ansible-playbook playbooks/20-config-converge.yml --ask-become-pass"
elif [ "$BOUND" -eq 1 ] && [ "$STUB_OK" -ne 0 ] && [ "${HIJACK:-0}" -eq 0 ]; then
  say "  ${ylw}Stub is bound but refuses connections, and NO :53 DNAT/REJECT${rst}"
  say "  ${ylw}rules were found.${rst} So the hostPort theory does NOT hold here."
  say "  Next: 'systemctl restart systemd-resolved', and if that does not fix"
  say "  it, REBOOT the node -- resolved is wedged below the config layer."
elif [ "$BOUND" -eq 0 ]; then
  say "  ${red}The stub socket is not bound at all.${rst}"
  say "  Check DNSStubListener=yes and that nothing else holds 127.0.0.53:53."
elif [ "$RESOLVECTL" -eq 0 ]; then
  say "  ${ylw}Both getent and resolvectl fail -- upstream resolvers are"
  say "  unreachable. Check the uplink and the router before blaming the node.${rst}"
else
  say "  ${ylw}Inconclusive. Paste this whole output.${rst}"
fi
say ""
