#!/usr/bin/env bash
# Disposable native NDepend experiment. Requires cluster-admin kubectl access.
# No live workspace, host mount, PVC, GitHub token, or Docker daemon is used.
set -euo pipefail
umask 077

usage() {
  cat <<'USAGE'
Usage: probe-ndepend-workspace.sh --mac MAC [--hostname TarjeiPC] [--render]
       [--image ghcr.io/lanternfyre/ndepend:latest] [--keep]

--mac       The existing NDEPEND_FP_MAC GitHub organization variable.
--hostname  The existing NDEPEND_FP_HOSTNAME (default TarjeiPC).
--image     Published NDepend image, or a workspace preview containing NDepend.
--render    Print the complete manifests without contacting Kubernetes.
--keep      Retain the disposable namespace even after a successful test.

Reads Infrastructure/Ndepend fields ActivationLic (base64) and H through the
existing one-password ClusterSecretStore. It never prints these values.
Failure keeps the namespace for diagnosis; success removes it unless --keep.
USAGE
}

mac=''
machine=TarjeiPC
image=ghcr.io/lanternfyre/ndepend:latest
render=0
keep=0
while (($#)); do
  case "$1" in
    --mac|--hostname|--image)
      (($# >= 2)) || { usage >&2; exit 2; }
      case "$1" in
        --mac) mac=$2 ;;
        --hostname) machine=$2 ;;
        --image) image=$2 ;;
      esac
      shift 2 ;;
    --render) render=1; shift ;;
    --keep) keep=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[[ "$mac" =~ ^([[:xdigit:]]{2}:){5}[[:xdigit:]]{2}$ ]] || {
  echo 'Supply --mac with the existing NDEPEND_FP_MAC; do not guess an interface.' >&2
  exit 2
}
[[ "$machine" =~ ^[A-Za-z0-9][A-Za-z0-9.-]{0,63}$ ]] || {
  echo 'Hostname must be 1–64 letters, digits, dots or hyphens.' >&2; exit 2;
}
[[ "$image" =~ ^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$ ]] || {
  echo 'Invalid image reference.' >&2; exit 2;
}
mac=$(printf '%s' "$mac" | tr '[:upper:]' '[:lower:]')
namespace="ndepend-probe-$(date -u +%Y%m%d%H%M%S)-$$"
scratch=$(mktemp -d "${TMPDIR:-/tmp}/ndepend-probe.XXXXXXXX")
created=0
cleanup() {
  local rc=$?
  trap - EXIT
  if ((created)); then
    if ((rc == 0 && keep == 0)); then
      kubectl delete namespace "$namespace" --wait=false || rc=1
    else
      printf 'Probe retained. Cleanup: kubectl delete namespace %s\n' "$namespace" >&2
    fi
  fi
  rm -rf -- "$scratch"
  exit "$rc"
}
trap cleanup EXIT

cat > "$scratch/namespace.yaml" <<YAML
apiVersion: v1
kind: Namespace
metadata:
  name: $namespace
  labels:
    app.kubernetes.io/part-of: ndepend-workspace-probe
    pod-security.kubernetes.io/enforce: privileged
    pod-security.kubernetes.io/audit: restricted
    pod-security.kubernetes.io/warn: restricted
YAML

cat > "$scratch/support.yaml" <<YAML
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: activation
  namespace: $namespace
spec:
  secretStoreRef: {name: one-password, kind: ClusterSecretStore}
  target:
    name: activation
    creationPolicy: Owner
    template:
      engineVersion: v2
      data:
        ActivationLic: '{{ .lic | trim | b64dec }}'
        H: '{{ .marker | trim }}'
  data:
    - secretKey: lic
      remoteRef: {key: Ndepend, property: ActivationLic}
    - secretKey: marker
      remoteRef: {key: Ndepend, property: H}
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: ghcr-pull
  namespace: $namespace
spec:
  secretStoreRef: {name: one-password, kind: ClusterSecretStore}
  target:
    name: ghcr-pull
    creationPolicy: Owner
    template:
      engineVersion: v2
      type: kubernetes.io/dockerconfigjson
      data:
        .dockerconfigjson: '{"auths":{"ghcr.io":{"username":"t3chy0n","password":{{ .token | trim | toJson }}}}}'
  data:
    - secretKey: token
      remoteRef: {key: GHCR, property: password}
---
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: probe
  namespace: $namespace
spec:
  endpointSelector: {}
  enableDefaultDeny: {ingress: true, egress: true}
  ingress: []
  egress:
    - toEndpoints:
        - matchLabels:
            io.kubernetes.pod.namespace: kube-system
            k8s-app: kube-dns
      toPorts:
        - ports: [{port: "53", protocol: UDP}, {port: "53", protocol: TCP}]
          rules: {dns: [{matchPattern: "*"}]}
    # Allow the vendor's public service for licence checks. Image pulls happen
    # on the node; this pod has no general internet or cluster-service access.
    - toFQDNs:
        - matchName: ndepend.com
        - matchName: www.ndepend.com
      toPorts:
        - ports: [{port: "443", protocol: TCP}, {port: "80", protocol: TCP}]
YAML

cat > "$scratch/pod.yaml" <<YAML
apiVersion: v1
kind: Pod
metadata:
  name: probe
  namespace: $namespace
  annotations:
    cni.cilium.io/mac-address: "$mac"
spec:
  automountServiceAccountToken: false
  shareProcessNamespace: false
  hostNetwork: false
  restartPolicy: Never
  activeDeadlineSeconds: 300
  imagePullSecrets: [{name: ghcr-pull}]
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
          - matchExpressions:
              - {key: node-role.kubernetes.io/control-plane, operator: DoesNotExist}
  securityContext:
    runAsNonRoot: true
    runAsUser: 1000
    runAsGroup: 1000
    fsGroup: 1000
    seccompProfile: {type: RuntimeDefault}
  initContainers:
    # containerd joins all containers to the sandbox's UTS namespace. Test
    # that a finished init container can set it without empowering the reader.
    - name: identity
      image: $image
      imagePullPolicy: Always
      command: [hostname, "$machine"]
      securityContext:
        allowPrivilegeEscalation: true
        readOnlyRootFilesystem: true
        capabilities: {drop: [ALL], add: [SYS_ADMIN]}
      resources:
        requests: {cpu: 10m, memory: 32Mi}
        limits: {memory: 64Mi}
  containers:
    - name: reader
      image: $image
      imagePullPolicy: Always
      workingDir: /work
      command: [bash, /probe/run.sh]
      env:
        - {name: EXPECTED_HOSTNAME, value: "$machine"}
        - {name: EXPECTED_MAC, value: "$mac"}
        - {name: HOME, value: /work/home}
        - {name: DOTNET_CLI_TELEMETRY_OPTOUT, value: "1"}
        - {name: DOTNET_NOLOGO, value: "1"}
        - {name: DOTNET_SKIP_FIRST_TIME_EXPERIENCE, value: "1"}
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities: {drop: [ALL]}
      resources:
        requests: {cpu: 250m, memory: 256Mi, ephemeral-storage: 512Mi}
        limits: {memory: 2Gi, ephemeral-storage: 1Gi}
      volumeMounts:
        - {name: work, mountPath: /work}
        - {name: tmp, mountPath: /tmp}
        - {name: activation, mountPath: /run/activation, readOnly: true}
        - {name: script, mountPath: /probe, readOnly: true}
  volumes:
    - {name: work, emptyDir: {medium: Memory, sizeLimit: 512Mi}}
    - {name: tmp, emptyDir: {medium: Memory, sizeLimit: 64Mi}}
    - name: activation
      secret: {secretName: activation, defaultMode: 0440}
    - name: script
      configMap: {name: probe-script, defaultMode: 0444}
YAML

cat > "$scratch/run.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
umask 077
diagnostics() {
  local rc=$?
  if ((rc != 0)); then
    # Console diagnostics only; never dump the mounted activation files.
    for log in /work/create-project.log /work/analysis.log; do
      if [[ -f "$log" ]]; then
        printf '\nLast console diagnostics from %s:\n' "$log" >&2
        tail -n 25 "$log" >&2
      fi
    done
  fi
}
trap diagnostics EXIT
test "$(id -u)" = 1000
test "$(hostname)" = "$EXPECTED_HOSTNAME"
test "$(cat /sys/class/net/eth0/address)" = "$EXPECTED_MAC"
awk '/^Cap(Inh|Prm|Eff|Bnd|Amb):/ {if ($2 !~ /^0+$/) exit 1}' /proc/self/status
awk '/^NoNewPrivs:/ {if ($2 != 1) exit 1}' /proc/self/status
if hostname ForbiddenHostnameChange 2>/dev/null; then
  echo 'FAIL: the reader can change the hostname' >&2; exit 1
fi
printf 'PASS identity: hostname=%s MAC=%s uid=1000, no capabilities, no_new_privs=1\n' \
  "$(hostname)" "$(cat /sys/class/net/eth0/address)"

mkdir -p "$HOME/.config/NDepend" /work/src
test -s /run/activation/ActivationLic
test -s /run/activation/H
cp /run/activation/ActivationLic /run/activation/H "$HOME/.config/NDepend/"
chmod 600 "$HOME/.config/NDepend/ActivationLic" "$HOME/.config/NDepend/H"

# No NuGet downloads: use only the reference packs shipped in the SDK.
cat > /work/NuGet.Config <<'XML'
<configuration><packageSources><clear /></packageSources></configuration>
XML
cat > /work/src/Probe.csproj <<'XML'
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net10.0</TargetFramework></PropertyGroup>
</Project>
XML
cat > /work/src/Program.cs <<'CS'
using System;
if (Environment.MachineName != Environment.GetEnvironmentVariable("EXPECTED_HOSTNAME"))
    throw new InvalidOperationException(".NET sees an unexpected machine name");
Console.WriteLine("PASS .NET machine name: " + Environment.MachineName);
CS
dotnet build /work/src/Probe.csproj --configfile /work/NuGet.Config --nologo -v quiet
dotnet /work/src/bin/Debug/net10.0/Probe.dll

console=/opt/ndepend/net10.0/NDepend.Console.MultiOS.dll
if ! dotnet "$console" /CreateProject /work/Probe.ndproj \
    /work/src/bin/Debug/net10.0/Probe.dll > /work/create-project.log 2>&1; then
  echo 'FAIL: NDepend could not create a project. Its output is in /work/create-project.log.' >&2
  exit 1
fi
echo 'PASS NDepend activation: CreateProject succeeded'
rc=0
dotnet "$console" /work/Probe.ndproj /OutDir /work/report /Silent \
  > /work/analysis.log 2>&1 || rc=$?
test "$rc" -le 1 || { echo "FAIL: analyser exit $rc; see /work/analysis.log" >&2; exit 1; }
test -s /work/report/NDependReport.html
test -n "$(find /work/report -name '*.ndar' -type f -size +0c -print -quit)"
grep -qE '(^|[^0-9])[1-9][0-9]* source files? parsed' /work/analysis.log
echo "PASS native analysis: source parsed, HTML and .ndar produced (console exit $rc)."
echo 'This tests identity and activation only; it is not a Necronia quality-gate verdict.'
SH

# Serialize the script without expanding it through the caller's shell.
{
  printf 'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: probe-script\n  namespace: %s\ndata:\n  run.sh: |\n' "$namespace"
  sed 's/^/    /' "$scratch/run.sh"
} > "$scratch/script.yaml"

if ((render)); then
  cat "$scratch/namespace.yaml"
  for part in support script pod; do
    printf '\n---\n'
    cat "$scratch/$part.yaml"
  done
  exit 0
fi
command -v kubectl >/dev/null
printf 'Context: %s; disposable namespace: %s\n' "$(kubectl config current-context)" "$namespace"
# CREATE, not apply: never adopt or later delete a pre-existing namespace.
kubectl create -f "$scratch/namespace.yaml"
created=1
kubectl apply -f "$scratch/support.yaml" -f "$scratch/script.yaml"
kubectl -n "$namespace" wait --for=condition=Ready externalsecret/activation externalsecret/ghcr-pull --timeout=120s
kubectl apply -f "$scratch/pod.yaml"

phase=''
for ((attempt=0; attempt<160; attempt++)); do
  phase=$(kubectl -n "$namespace" get pod probe -o jsonpath='{.status.phase}')
  case "$phase" in Succeeded|Failed) break ;; esac
  sleep 2
done
kubectl -n "$namespace" logs probe -c identity || true
kubectl -n "$namespace" logs probe -c reader || true
if [[ "$phase" != Succeeded ]]; then
  kubectl -n "$namespace" get pod probe -o wide
  kubectl -n "$namespace" get events --field-selector involvedObject.name=probe --sort-by=.lastTimestamp
  echo "Probe did not succeed (phase=$phase). No production resources were changed." >&2
  exit 1
fi
echo 'Probe passed. No production resources were changed.'
