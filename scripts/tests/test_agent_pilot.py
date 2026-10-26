"""Local-only smoke/regression checks; no live models, tmux or Beads calls."""
import json
import os
from pathlib import Path
import runpy
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HELPER = Path(__file__).resolve().parents[1] / 'agent-pilot.py'


class PilotHelper(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='pilot-helper-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.work = self.root / 'work tree'
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.calls = self.root / 'calls.jsonl'
        self.logs = self.root / 'private logs'
        self.env = os.environ.copy()
        self.env.update(GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1',
                        GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
                        GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid')
        self.git('init', '-q', '-b', 'main')
        (self.repo / 'README.md').write_text('# Fixture\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')
        self.git('worktree', 'add', '-qb', 'pilot', str(self.work))
        self.env.update(PATH=str(self.bin) + os.pathsep + self.env['PATH'],
                        TEST_CALLS=str(self.calls), BEADWORK_PILOT_LOG_DIR=str(self.logs),
                        AGENT_TMUX_SOCKET='/fixture/socket', AGENT_TMUX_SESSION='main',
                        BEADWORK_ROLE='operator-selected-role', BEADWORK_DELIVERY='local',
                        BEADWORK_TOKEN='fixture-hidden-workflow-token',
                        BEADS_DOLT_PASSWORD='fixture-hidden-database-password')
        stub = '''#!/usr/bin/env python3
import json,os,pathlib,sys
name=pathlib.Path(sys.argv[0]).name
with pathlib.Path(os.environ['TEST_CALLS']).open('a') as stream:
 stream.write(json.dumps([name,*sys.argv[1:]])+'\\n')
if name=='claude': print('{"loggedIn":true}')
if name=='codex' and 'sandbox' in sys.argv:
 print('fixture: workspace sandbox unavailable',file=sys.stderr); sys.exit(1)
if name=='tmux' and 'new-window' in sys.argv and os.environ.get('TEST_FAIL_TMUX'):
 print(os.environ['BEADS_DOLT_PASSWORD'],file=sys.stderr); sys.exit(1)
'''
        for name in ('bd', 'claude', 'codex', 'tmux'):
            path = self.bin / name
            path.write_text(stub)
            path.chmod(0o755)
        self.runner = self.root / 'shared runner.py'
        self.runner.write_text('import json,sys; print(json.dumps(sys.argv[1:]))\n')
        self.options = ['--', '--bead', 'pilot-1', '--max-beads', '1',
                        '--time-budget', '30m', '--mode', 'attended']

    def git(self, *arguments):
        subprocess.run(['git', '-C', str(self.repo), *arguments], env=self.env,
                       check=True, capture_output=True)

    def run_helper(self, engine='claude', check=False, primary=False):
        command = ['python3', str(HELPER)] + (['--check'] if check else [])
        command += [engine, str(self.repo if primary else self.work),
                    str(self.runner), *self.options]
        return subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=10)

    def invocations(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def test_check_forwards_readonly_dry_run_without_tmux_or_logs(self):
        result = self.run_helper(check=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--dry-run', result.stdout)
        self.assertFalse(self.logs.exists())
        self.assertFalse(any(call[0] == 'tmux' for call in self.invocations()))

    def test_primary_checkout_is_refused(self):
        result = self.run_helper(check=True, primary=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('primary checkout is reserved', result.stderr)
        self.assertEqual(self.invocations(), [])

    def test_codex_sandbox_failure_prevents_runner_and_tmux_launch(self):
        result = self.run_helper(engine='codex')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('workspace sandbox unavailable', result.stderr)
        self.assertNotIn('Command:', result.stdout)
        self.assertFalse(self.logs.exists())
        self.assertFalse(any(call[0] == 'tmux' for call in self.invocations()))

    def test_launch_quotes_paths_and_preserves_only_named_config(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        window = next(call for call in self.invocations() if 'new-window' in call)
        self.assertIn('-d', window)
        self.assertIn('main:', window)
        self.assertIn(str(self.work), window)
        shell = shlex.split(window[-1])
        self.assertEqual(shell[:2], ['bash', '-c'])
        subprocess.run(['bash', '-n', '-c', shell[2]], check=True)
        for setting in ('BEADWORK_NO_MEMORIES=1', 'BEADWORK_ROLE=operator-selected-role',
                        'BEADWORK_DELIVERY=local', '-u AGENT_REMOTE_CONTROL_NAME'):
            self.assertIn(setting, shell[2])
        self.assertIn(shlex.quote('BEADWORK_PROJECT_DIR=' + str(self.work)), shell[2])
        for secret in ('BEADWORK_TOKEN', 'BEADS_DOLT_PASSWORD',
                       self.env['BEADWORK_TOKEN'], self.env['BEADS_DOLT_PASSWORD']):
            self.assertNotIn(secret, shell[2])
        log, = self.logs.glob('*.log')
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('kill-session', json.dumps(self.invocations()))

    def test_command_failure_redacts_secrets_and_does_not_echo_environment(self):
        self.env['TEST_FAIL_TMUX'] = '1'
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('[redacted]', result.stderr)
        self.assertNotIn(self.env['BEADS_DOLT_PASSWORD'], result.stderr + result.stdout)
        self.assertNotIn(self.env['BEADWORK_TOKEN'], result.stderr + result.stdout)
        self.assertNotIn('new-window', result.stderr)

    def test_timeout_error_does_not_echo_command_or_environment(self):
        module = runpy.run_path(str(HELPER), run_name='fixture_module')
        secret = self.env['BEADS_DOLT_PASSWORD']
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired(['tmux', secret], 20)):
            with self.assertRaisesRegex(RuntimeError, '^tmux timed out after 20 seconds$'):
                module['checked'](['tmux', secret], cwd=self.work, env=self.env)


if __name__ == '__main__':
    unittest.main(verbosity=2)
