"""Local checks for the disposable probe; these do not validate live licensing.

Run: python3 -m unittest discover -s scripts -p test_probe_ndepend_workspace.py -v
Requires PyYAML for the manifest checks.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

SCRIPT = Path(__file__).with_name("probe-ndepend-workspace.sh")


class ProbeTests(unittest.TestCase):
    def run_probe(self, *args, env=None):
        return subprocess.run(
            ["bash", str(SCRIPT), *args], text=True, capture_output=True,
            env=env, timeout=10,
        )

    def test_invalid_identity_is_rejected_before_any_cluster_access(self):
        for args in [[], ["--mac", "bad"], ["--mac", "02:11:22:33:44:55", "--hostname", "x\nmetadata:"]]:
            with self.subTest(args=args):
                result = self.run_probe(*args, "--render")
                self.assertEqual(2, result.returncode)
                self.assertNotIn("apiVersion", result.stdout)

    def test_render_preserves_workspace_boundary_and_secret_mapping(self):
        result = self.run_probe("--mac", "02:AA:22:33:44:55", "--render")
        self.assertEqual(0, result.returncode, result.stderr)
        documents = list(yaml.safe_load_all(result.stdout))
        pod = next(x for x in documents if x["kind"] == "Pod")
        spec = pod["spec"]
        self.assertEqual("02:aa:22:33:44:55", pod["metadata"]["annotations"]["cni.cilium.io/mac-address"])
        self.assertFalse(spec["automountServiceAccountToken"])
        self.assertFalse(spec["hostNetwork"])
        self.assertFalse(spec["shareProcessNamespace"])
        self.assertEqual(1000, spec["securityContext"]["runAsUser"])
        identity, = spec["initContainers"]
        self.assertEqual(["sh", "-ec"], identity["command"][:2])
        self.assertEqual(["identity", "TarjeiPC"], identity["command"][3:])
        self.assertIn('hostname "$1"', identity["command"][2])
        self.assertIn('test "$(hostname)" = "$1"', identity["command"][2])
        syntax = subprocess.run(["sh", "-n"], input=identity["command"][2], text=True, capture_output=True)
        self.assertEqual(0, syntax.returncode, syntax.stderr)
        self.assertNotIn("volumeMounts", identity)
        self.assertNotIn("restartPolicy", identity)
        self.assertFalse(identity["securityContext"]["runAsNonRoot"])
        self.assertEqual(0, identity["securityContext"]["runAsUser"])
        self.assertEqual(0, identity["securityContext"]["runAsGroup"])
        self.assertEqual(["SYS_ADMIN"], identity["securityContext"]["capabilities"]["add"])
        reader, = spec["containers"]
        self.assertTrue(spec["securityContext"]["runAsNonRoot"])
        self.assertNotIn("runAsUser", reader["securityContext"])
        self.assertNotIn("runAsNonRoot", reader["securityContext"])
        self.assertFalse(reader["securityContext"]["allowPrivilegeEscalation"])
        self.assertEqual({"drop": ["ALL"]}, reader["securityContext"]["capabilities"])
        for container in [identity, reader]:
            self.assertNotIn("privileged", container["securityContext"])
        for volume in spec["volumes"]:
            self.assertNotIn("hostPath", volume)
            self.assertNotIn("persistentVolumeClaim", volume)
        activation = next(x for x in documents if x["kind"] == "ExternalSecret" and x["metadata"]["name"] == "activation")
        self.assertEqual(
            [{"key": "Ndepend", "property": "ActivationLic"}, {"key": "Ndepend", "property": "H"}],
            [x["remoteRef"] for x in activation["spec"]["data"]],
        )
        self.assertIn("b64dec", activation["spec"]["target"]["template"]["data"]["ActivationLic"])
        config = next(x for x in documents if x["kind"] == "ConfigMap")
        syntax = subprocess.run(["bash", "-n"], input=config["data"]["run.sh"], text=True, capture_output=True)
        self.assertEqual(0, syntax.returncode, syntax.stderr)

    def fake_cluster(self, mode, *extra):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "kubectl"
            fake.write_text("""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ['PROBE_CALL_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
if args == ['config', 'current-context']:
    print('fake-cluster')
elif args[:1] == ['create'] and os.environ['PROBE_TEST_MODE'] == 'exists':
    sys.exit(1)
elif 'wait' in args and os.environ['PROBE_TEST_MODE'] == 'secret-failure':
    sys.exit(1)
elif 'get' in args and 'pod' in args:
    print('Succeeded', end='')
""")
            fake.chmod(0o755)
            log = Path(directory) / "calls.jsonl"
            env = dict(os.environ, PATH=directory + os.pathsep + os.environ["PATH"],
                       PROBE_CALL_LOG=str(log), PROBE_TEST_MODE=mode)
            result = self.run_probe("--mac", "02:11:22:33:44:55", *extra, env=env)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            return result, calls

    def test_preexisting_namespace_is_never_adopted_or_deleted(self):
        result, calls = self.fake_cluster("exists")
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(any("apply" in c or "delete" in c for c in calls))

    def test_missing_secret_keeps_namespace_and_never_starts_pod(self):
        result, calls = self.fake_cluster("secret-failure")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Probe retained", result.stderr)
        self.assertFalse(any("delete" in c for c in calls))
        self.assertFalse(any(any(arg.endswith("/pod.yaml") for arg in c) for c in calls))

    def test_success_cleans_up_only_the_new_namespace(self):
        result, calls = self.fake_cluster("success")
        self.assertEqual(0, result.returncode, result.stderr)
        deletions = [c for c in calls if "delete" in c]
        self.assertEqual(1, len(deletions))
        self.assertEqual(["delete", "namespace"], deletions[0][:2])
        self.assertTrue(deletions[0][2].startswith("ndepend-probe-"))

    def test_keep_preserves_success_for_inspection(self):
        result, calls = self.fake_cluster("success", "--keep")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(any("delete" in c for c in calls))


if __name__ == "__main__":
    unittest.main()
