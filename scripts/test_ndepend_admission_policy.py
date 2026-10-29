#!/usr/bin/env python3
"""Mutation checks against Kyverno's real CEL evaluator, without cluster writes.

Supply a Pod extracted from the enabled claude-box Helm render as --pod.
Requires PyYAML and the Kyverno CLI (tested with cluster version 1.18.2).
"""
import argparse
from copy import deepcopy
from pathlib import Path
import subprocess
import tempfile

import yaml

POLICY = Path(__file__).resolve().parents[1] / (
    "gitops/clusters/home/apps/kyverno/manifests/claude-box-ndepend.validatingpolicy.yaml"
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pod", type=Path, required=True)
    parser.add_argument("--kyverno", default="kyverno")
    args = parser.parse_args()
    original = yaml.safe_load(args.pod.read_text())
    assert original["kind"] == "Pod"
    identity = next(i for i, c in enumerate(original["spec"]["initContainers"])
                    if c["name"] == "ndepend-identity")
    workspace = next(i for i, c in enumerate(original["spec"]["containers"])
                     if c["name"] == "workspace")
    root = ["spec", "initContainers", identity]
    agent = ["spec", "containers", workspace]
    cases = [("enabled chart pod", original, "pass")]

    def change(name, path, value, expected="fail"):
        pod = deepcopy(original)
        target = pod
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = deepcopy(value)
        cases.append((name, pod, expected))

    for key in ["hostNetwork", "hostPID", "hostIPC", "shareProcessNamespace", "automountServiceAccountToken"]:
        change(key, ["spec", key], True)
    change("host volume", ["spec", "volumes"], [{"name": "host", "hostPath": {"path": "/"}}])
    change("pod UID root", ["spec", "securityContext", "runAsUser"], 0)
    change("pod non-root false", ["spec", "securityContext", "runAsNonRoot"], False)
    change("pod seccomp", ["spec", "securityContext", "seccompProfile"], {"type": "Unconfined"})
    change("pod sysctl", ["spec", "securityContext", "sysctls"], [{"name": "kernel.shm_rmid_forced", "value": "1"}])
    change("legacy AppArmor", ["metadata", "annotations"], {"container.apparmor.security.beta.kubernetes.io/workspace": "unconfined"})
    change("workspace capability", agent + ["securityContext", "capabilities", "add"], ["SYS_ADMIN"])
    change("workspace UID root", agent + ["securityContext", "runAsUser"], 0)
    change("workspace non-root false", agent + ["securityContext", "runAsNonRoot"], False)
    change("workspace escalation", agent + ["securityContext", "allowPrivilegeEscalation"], True)
    change("workspace privileged", agent + ["securityContext", "privileged"], True)
    change("workspace seccomp", agent + ["securityContext", "seccompProfile"], {"type": "Unconfined"})
    change("workspace proc mount", agent + ["securityContext", "procMount"], "Unmasked")
    change("workspace host port", agent + ["ports"], [{"containerPort": 7681, "hostPort": 7681}])
    change("workspace security context missing", agent + ["securityContext"], {})
    change("identity command", root + ["command"], ["sh", "-c", "sleep infinity"])
    change("identity args", root + ["args"], ["unexpected"])
    change("identity image", root + ["image"], "untrusted:latest")
    change("identity mount", root + ["volumeMounts"], [{"name": "gh-app", "mountPath": "/secrets"}])
    change("identity device", root + ["volumeDevices"], [{"name": "disk", "devicePath": "/dev/x"}])
    change("identity env", root + ["env"], [{"name": "LD_PRELOAD", "value": "/tmp/anything"}])
    change("identity envFrom", root + ["envFrom"], [{"secretRef": {"name": "gh-app"}}])
    change("identity sidecar", root + ["restartPolicy"], "Always")
    change("identity extra capability", root + ["securityContext", "capabilities", "add"], ["SYS_ADMIN", "NET_ADMIN"])
    change("identity writable root", root + ["securityContext", "readOnlyRootFilesystem"], False)
    change("identity privileged", root + ["securityContext", "privileged"], True)
    change("identity lifecycle", root + ["lifecycle"], {"postStart": {"exec": {"command": ["sh"]}}})
    change("wrong pod", ["metadata", "name"], "different-pod")
    change("wrong service account", ["spec", "serviceAccountName"], "different-account")

    pod = deepcopy(original)
    pod["spec"]["initContainers"] = [c for c in pod["spec"]["initContainers"] if c["name"] != "ndepend-identity"]
    cases.append(("no hostname exception requested", pod, "pass"))
    debug = {"name": "debug", "image": "busybox:1.37", "command": ["sh"],
             "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}}
    change("restricted debug container", ["spec", "ephemeralContainers"], [debug], "pass")
    debug["name"] = "ndepend-identity"
    debug["securityContext"]["runAsUser"] = 0
    debug["securityContext"]["capabilities"]["add"] = ["SYS_ADMIN"]
    change("debug cannot impersonate identity", ["spec", "ephemeralContainers"], [debug])
    pod = deepcopy(original)
    pod["spec"]["containers"].append(pod["spec"]["initContainers"].pop(identity))
    cases.append(("regular container cannot use identity exception", pod, "fail"))

    # Check scope as well as the CEL expressions. A policy that silently skips
    # alpha would otherwise make all the positive cases look successful.
    # Kyverno 1.18.2 reports zero evaluations (not skip: 1) for a namespace
    # excluded by matchConstraints.
    change("other namespace", ["metadata", "namespace"], "claude-box-bravo", "unmatched")
    with tempfile.TemporaryDirectory(prefix="ndepend-admission-") as directory:
        resource = Path(directory) / "pod.yaml"
        for name, pod, expected in cases:
            resource.write_text(yaml.safe_dump(pod))
            result = subprocess.run(
                [args.kyverno, "apply", str(POLICY), "--resource", str(resource)],
                text=True, capture_output=True, timeout=30,
            )
            output = result.stdout + result.stderr
            summary = ", ".join(f"{key}: {int(key == expected)}" for key in ("pass", "fail", "warn", "error", "skip"))
            assert summary in output, f"{name}: expected {expected}\n{output}"
            assert result.returncode == (1 if expected == "fail" else 0), output
            print(f"PASS {name}: {expected}")
    print(f"{len(cases)} admission cases passed; no cluster resources changed.")


if __name__ == "__main__":
    main()
