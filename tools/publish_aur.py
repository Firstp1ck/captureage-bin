#!/usr/bin/env python3
"""Sync only packaging files to AUR, preserving its independent Git history."""
import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from update import PACKAGE_FILES, ROOT, version_tuple


def git(directory, *args):
    return subprocess.run(['git', '-C', str(directory), *args], check=True, capture_output=True, text=True).stdout.strip()


def package_version(srcinfo):
    fields = {}
    for line in srcinfo.splitlines():
        key, separator, value = line.strip().partition(' = ')
        if separator:
            fields[key] = value
    if fields.get('pkgbase') != 'captureage-bin' or fields.get('pkgname') != 'captureage-bin':
        raise ValueError('Unexpected package name')
    return version_tuple(fields['pkgver']), int(fields['pkgrel'])


def publish(source, remote, name, email, dry_run=False):
    local_version = package_version((source / '.SRCINFO').read_text())
    with tempfile.TemporaryDirectory(prefix='captureage-aur-') as temporary:
        target = Path(temporary) / 'aur'
        subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', 'clone', remote, str(target)], check=True)
        git(target, 'config', 'core.hooksPath', '/dev/null')
        if git(target, 'branch', '--list', 'master'):
            git(target, 'checkout', 'master')
        elif git(target, 'branch', '-r'):
            raise ValueError('AUR repository has branches but no master branch')
        else:
            git(target, 'symbolic-ref', 'HEAD', 'refs/heads/master')
        existing = target / '.SRCINFO'
        if existing.exists() and package_version(existing.read_text()) > local_version:
            raise ValueError('AUR has a newer package; refusing to downgrade it')
        tracked = git(target, 'ls-files', '-z').split('\0')
        for filename in filter(None, tracked):
            path = target / filename
            if path.is_dir() and not path.is_symlink():
                raise ValueError('Unexpected directory or submodule in AUR repository')
            path.unlink()
        for filename in PACKAGE_FILES:
            shutil.copyfile(source / filename, target / filename)
        git(target, 'add', '--all')
        changes = git(target, 'diff', '--cached', '--name-only')
        if not changes:
            print('AUR packaging already matches GitHub')
            return False
        if dry_run:
            print('AUR files that would change:\n' + changes)
            return True
        git(target, 'config', 'user.name', name)
        git(target, 'config', 'user.email', email)
        version, release = local_version
        git(target, 'commit', '-m', f"Update captureage-bin to {'.'.join(map(str, version))}-{release}")
        git(target, 'push', 'origin', 'HEAD:master')
        print('Published package files to AUR')
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--remote', default='ssh://aur@aur.archlinux.org/captureage-bin.git')
    args = parser.parse_args()
    publish(ROOT, args.remote, os.environ.get('AUR_COMMIT_NAME', 'Firstpick'),
            os.environ.get('AUR_COMMIT_EMAIL', 'firstpick1992@proton.me'), args.dry_run)


if __name__ == '__main__':
    main()
