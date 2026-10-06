"""Update clean GitHub source checkouts without resetting local work or data."""
from __future__ import annotations
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORIGINS = {'https://github.com/ryon-solos/My-ArienX.git',
           'https://github.com/ryon-solos/My-ArienX',
           'git@github.com:ryon-solos/My-ArienX.git'}

class UpdateError(RuntimeError):
    pass

def _git(root, *args):
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_SSH_COMMAND='ssh -o BatchMode=yes -o StrictHostKeyChecking=yes')
    try:
        result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null' if os.name != 'nt' else 'core.hooksPath=NUL', '-C', str(root), *args],
                                capture_output=True, text=True, timeout=45, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError('Git is unavailable or the update request timed out.') from exc
    if result.returncode:
        raise UpdateError('Git update failed. Check your connection and repository access; local work was not reset.')
    return result.stdout.strip()

def _validate(root):
    if _git(root, 'remote', 'get-url', 'origin') not in ORIGINS:
        raise UpdateError('Updates require the official My-ArienX GitHub origin.')
    if _git(root, 'branch', '--show-current') != 'main':
        raise UpdateError('Automatic source updates are supported on main only.')
    if _git(root, 'status', '--porcelain', '--untracked-files=normal'):
        raise UpdateError('Local source changes exist. Save or commit them before updating.')

def _safe_changes(root, commit):
    files = _git(root, 'diff', '--name-only', 'HEAD', commit).splitlines()
    for name in files:
        p = Path(name)
        if (name.startswith(('.venv/', 'venv/', '.git/')) or
            p.name.startswith('.env') or
            (name.startswith('config/') and name not in {'config/__init__.py', 'config/jarvis.ico', 'config/jarvis.png'}) or
            (name.startswith('memory/') and p.suffix != '.py')):
            raise UpdateError('Update touches private runtime data; refusing automatic application.')
    return any(p.endswith(('requirements.txt', 'package.json', 'pnpm-lock.yaml', 'package-lock.json')) for p in files)

def check(root=ROOT):
    root = Path(root)
    _validate(root)
    _git(root, 'fetch', '--no-tags', 'origin', 'refs/heads/main:refs/remotes/origin/main')
    commit = _git(root, 'rev-parse', 'refs/remotes/origin/main')
    if commit == _git(root, 'rev-parse', 'HEAD'):
        return None
    _git(root, 'merge-base', '--is-ancestor', 'HEAD', commit)
    needs_setup = _safe_changes(root, commit)
    return {'commit': commit, 'release_notes': _git(root, 'log', '--format=%h %s', '-8', 'HEAD..' + commit), 'needs_setup': needs_setup}

def apply(update, root=ROOT):
    root = Path(root)
    commit = update.get('commit', '')
    if not re.fullmatch(r'[0-9a-f]{40,64}', commit):
        raise UpdateError('Invalid update commit.')
    _validate(root)
    if commit != _git(root, 'rev-parse', 'refs/remotes/origin/main'):
        raise UpdateError('The offered update changed. Check again.')
    _git(root, 'merge-base', '--is-ancestor', 'HEAD', commit)
    if _safe_changes(root, commit):
        raise UpdateError('This update changes dependencies. Run the launcher setup before applying it.')
    _git(root, 'merge', '--ff-only', commit)
