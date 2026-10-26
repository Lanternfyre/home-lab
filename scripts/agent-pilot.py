#!/usr/bin/env python3
r"""Run one explicit Beads pilot in its own worktree and tmux window.

Example:
  agent-pilot.py --check claude /path/to/worktree /path/to/run.py -- \
      --bead <id> --max-beads 1 --time-budget 30m --mode attended

Everything after -- goes to the shared runner. This helper never installs tools,
changes credentials or permissions, or creates/switches Git worktrees.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import uuid


PRESERVED_CONFIG = (
    'PATH', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR',
    'BEADWORK_ROLE', 'BEADWORK_ROLE_CARD', 'BEADWORK_ROLE_DIR',
    'BEADWORK_DELIVERY', 'BEADWORK_MODEL', 'BEADWORK_EFFORT',
    'BEADWORK_REVIEW_CAP', 'BEADWORK_VERIFY', 'BEADWORK_TZ', 'BEADWORK_DAY',
    'BEADWORK_NO_MEMORIES', 'BEADWORK_PROJECT_DIR',
)


def safe_error(message, env):
    for name, value in env.items():
        if value and re.search(r'TOKEN|PASSWORD|SECRET|API_KEY|CREDENTIAL', name, re.I):
            message = message.replace(value, '[redacted]')
    return message


def checked(command, *, cwd, env):
    tool = Path(command[0]).name
    try:
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                                text=True, timeout=20)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'{tool} timed out after 20 seconds') from None
    if result.returncode:
        detail = safe_error(result.stderr or result.stdout, env)
        raise RuntimeError(f'{tool} failed (exit {result.returncode}):\n{detail}')
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--check', action='store_true',
                        help='run prerequisites and shared runner dry-run; no window or model')
    parser.add_argument('engine', choices=('claude', 'codex'))
    parser.add_argument('worktree', type=Path, help='existing isolated Git worktree')
    parser.add_argument('runner', type=Path, help='exact shared beadwork scripts/run.py path')
    parser.add_argument('runner_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    forwarded = args.runner_args
    if forwarded[:1] == ['--']:
        forwarded = forwarded[1:]
    for option in ('--bead', '--time-budget', '--max-beads'):
        if not any(value == option or value.startswith(option + '=') for value in forwarded):
            parser.error(f'pass an explicit {option} to the shared runner after --')
    if any(value == '--engine' or value.startswith('--engine=') for value in forwarded):
        parser.error('select the engine with the first positional argument')
    root = args.worktree.expanduser().resolve()
    runner = args.runner.expanduser().resolve()
    if not runner.is_file():
        parser.error(f'no shared runner at {runner}')
    for tool in (args.engine, 'bd', 'git', 'tmux', 'bash', 'tee'):
        if not shutil.which(tool):
            parser.error(f'{tool} is not on PATH')
    env = os.environ.copy()
    # Golf is a clean-side box. Never inherit another caller's memory opt-in.
    env['BEADWORK_NO_MEMORIES'] = '1'
    env['BEADWORK_PROJECT_DIR'] = str(root)
    top = Path(checked(['git', 'rev-parse', '--show-toplevel'], cwd=root, env=env)).resolve()
    if top != root:
        parser.error('worktree must name the Git checkout root')
    gitdir = Path(checked(['git', 'rev-parse', '--absolute-git-dir'], cwd=root, env=env))
    common = Path(checked(['git', 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                          cwd=root, env=env))
    if gitdir.resolve() == common.resolve():
        parser.error('use a separate git worktree; the primary checkout is reserved')
    checked(['bd', '--readonly', 'ping'], cwd=root, env=env)
    if args.engine == 'claude':
        status = json.loads(checked(['claude', 'auth', 'status'], cwd=root, env=env))
        if not status.get('loggedIn'):
            raise RuntimeError('Claude is not logged in; complete login at the console')
    else:
        checked(['codex', 'login', 'status'], cwd=root, env=env)
        # Probe the host-selected sandbox without a model. The runner honors the
        # same Codex configuration; these boxes may select danger-full-access
        # because their pod cannot create the namespace workspace-write needs.
        checked(['codex', 'sandbox', '--', '/bin/true'], cwd=root, env=env)
    command = [sys.executable, str(runner), '--engine', args.engine, *forwarded]
    print(f'Checkout: {root}\nCommand: {shlex.join(command)}', flush=True)
    if args.check:
        return subprocess.call([*command, '--dry-run'], cwd=root, env=env)
    socket = os.environ.get('AGENT_TMUX_SOCKET', '/run/agent/tmux.sock')
    session = os.environ.get('AGENT_TMUX_SESSION', 'main')
    tmux = ['tmux', '-S', socket]
    checked([*tmux, 'has-session', '-t', session], cwd=root, env=env)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    window = f'beadwork-{args.engine}-{stamp}-{uuid.uuid4().hex[:6]}'
    logs = Path(os.environ.get('BEADWORK_PILOT_LOG_DIR',
                              str(Path.home() / '.local/state/beadwork/pilot')))
    logs = logs.expanduser().resolve()
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    log = logs / f'{window}.log'
    log.touch(mode=0o600, exist_ok=False)
    # tmux inherits the pod environment. Supply the clean-room opt-out explicitly
    # and leave Remote Control naming to the existing window 0 wrapper.
    # Credentials stay in the existing tmux server environment. Never put them,
    # or arbitrary caller variables, into a command string or tmux error message.
    preserved = {name: env[name] for name in PRESERVED_CONFIG if name in env}
    child = ['env', '-u', 'AGENT_REMOTE_CONTROL_NAME',
             *(f'{name}={value}' for name, value in sorted(preserved.items())), *command]
    shell = (f'set -o pipefail; {shlex.join(child)} 2>&1 | tee -a {shlex.quote(str(log))}; '
             'result=${PIPESTATUS[0]}; printf "\\nRunner exited %s. Log remains on disk.\\n" "$result"; '
             'exec bash')
    checked([*tmux, 'new-window', '-d', '-t', f'{session}:', '-n', window, '-c', str(root),
             shlex.join(['bash', '-c', shell])], cwd=root, env=env)
    print(f'Window: {session}:{window}\nLog: {log}\n'
          f'Attach: {shlex.join([*tmux, "select-window", "-t", f"{session}:{window}"])}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f'agent-pilot: {error}', file=sys.stderr)
        sys.exit(1)
