#!/usr/bin/env python3
"""Update the pinned CaptureAge release without running upstream code."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import unquote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
LOCAL_SOURCES = ('captureage', 'configure_game.py', 'captureage.desktop', 'captureage.png', 'captureage.reg', 'LICENSE', 'README.md')
PACKAGE_FILES = ('PKGBUILD', '.SRCINFO', *LOCAL_SOURCES)
LATEST = 'https://captureage.com/api/cade/download/prod/latest'
MAX_ARCHIVE = 1024 * 1024 * 1024


def version_tuple(version):
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+){2,3}', version):
        raise ValueError('Unsupported upstream version format')
    return tuple(map(int, version.split('.')))


def version_from_redirect(location):
    url = urlparse(location)
    if url.scheme != 'https' or url.hostname != 'captureage.azureedge.net':
        raise ValueError('Unexpected upstream download host')
    match = re.fullmatch(r'/cade/prod/CaptureAge-([0-9.]+)-x64\.nsis\.7z', unquote(url.path))
    if not match:
        raise ValueError('Unexpected upstream download filename')
    version_tuple(match[1])
    return match[1]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OfficialRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urlparse(newurl)
        if url.scheme != 'https' or url.hostname not in ('captureage.com', 'captureage.azureedge.net'):
            raise ValueError('Unexpected upstream download redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def latest_version():
    request = Request(LATEST, headers={'User-Agent': 'captureage-aur-updater/1.0'})
    try:
        with build_opener(NoRedirect()).open(request, timeout=60):
            raise ValueError('Latest endpoint did not redirect to an archive')
    except HTTPError as error:
        try:
            if error.code != 302:
                raise ValueError(f'Upstream version check returned HTTP {error.code}') from None
            return version_from_redirect(error.headers.get('Location', ''))
        finally:
            error.close()


def download(version, destination):
    url = f'https://captureage.com/api/cade/download/prod/CaptureAge-{version}-x64.nsis.7z'
    digest = hashlib.sha256()
    size = 0
    with build_opener(OfficialRedirect()).open(url, timeout=120) as response, destination.open('wb') as output:
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_ARCHIVE:
                raise ValueError('Upstream archive exceeds 1 GiB')
            output.write(chunk)
            digest.update(chunk)
    if size == 0:
        raise ValueError('Empty upstream archive')
    metadata = subprocess.run(
        ['bsdtar', '-xOf', str(destination), 'resources/app/package.json'],
        check=True, capture_output=True, timeout=120,
    )
    if json.loads(metadata.stdout)['version'] != version:
        raise ValueError('Archive metadata does not match its requested version')
    return digest.hexdigest()


def refresh_checksums(text, directory, archive_checksum):
    checksums = [archive_checksum]
    checksums.extend(hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in LOCAL_SOURCES)
    block = 'sha256sums=(\n' + ''.join(f"  '{checksum}'\n" for checksum in checksums) + ')'
    result, count = re.subn(r'^sha256sums=\([^)]*\)', lambda _: block, text, flags=re.M)
    if count != 1:
        raise ValueError('Expected one checksum array in PKGBUILD')
    return result


def update(directory, latest, fetch=download):
    pkgbuild = (directory / 'PKGBUILD').read_text()
    match = re.search(r'^pkgver=([0-9.]+)$', pkgbuild, re.M)
    if not match:
        raise ValueError('Cannot read pkgver')
    current = match[1]
    if version_tuple(latest) <= version_tuple(current):
        return False
    # Prepare all metadata in isolation. A failed download or makepkg leaves
    # the existing recipe, README and .SRCINFO untouched.
    with tempfile.TemporaryDirectory(prefix='captureage-update-') as temporary:
        staging = Path(temporary)
        for name in PACKAGE_FILES:
            (staging / name).write_bytes((directory / name).read_bytes())
        archive_name = f'CaptureAge-{latest}-x64.nsis.7z'
        archive = staging / archive_name
        checksum = fetch(latest, archive)
        if not re.fullmatch(r'[0-9a-f]{64}', checksum):
            raise ValueError('Invalid archive checksum')
        pkgbuild = re.sub(r'^pkgver=[0-9.]+$', f'pkgver={latest}', pkgbuild, flags=re.M)
        pkgbuild, count = re.subn(r'^pkgrel=[0-9]+$', 'pkgrel=1', pkgbuild, flags=re.M)
        if count != 1:
            raise ValueError('Cannot reset pkgrel')
        readme = (staging / 'README.md').read_text()
        readme, count = re.subn(
            r'Unofficial AUR packaging of CaptureAge:DE [0-9.]+\.',
            f'Unofficial AUR packaging of CaptureAge:DE {latest}.', readme,
        )
        if count != 1:
            raise ValueError('Cannot update README version')
        (staging / 'README.md').write_text(readme)
        (staging / 'PKGBUILD').write_text(refresh_checksums(pkgbuild, staging, checksum))
        srcinfo = subprocess.run(['makepkg', '--printsrcinfo'], cwd=staging, check=True, capture_output=True)
        (staging / '.SRCINFO').write_bytes(srcinfo.stdout)
        for name in ('PKGBUILD', 'README.md', '.SRCINFO'):
            (directory / name).write_bytes((staging / name).read_bytes())
        shutil.copyfile(archive, directory / archive_name)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Report the latest version without modifying files')
    args = parser.parse_args()
    latest = latest_version()
    current = re.search(r'^pkgver=([0-9.]+)$', (ROOT / 'PKGBUILD').read_text(), re.M)[1]
    print(f'Packaged: {current}; upstream download: {latest}')
    if args.check:
        print('Update available' if version_tuple(latest) > version_tuple(current) else 'Already current')
    elif update(ROOT, latest):
        print(f'Updated package to {latest}-1')
    else:
        print('No upstream update; keeping existing package metadata')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        # Do not expose signed CDN query strings in workflow logs.
        raise SystemExit(f'Update failed ({type(error).__name__}); package was not published') from None
