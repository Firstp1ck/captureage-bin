import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import update
import publish_aur


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in update.PACKAGE_FILES:
            shutil.copyfile(update.ROOT / name, self.root / name)
        self.current = publish_aur.package_version((self.root / '.SRCINFO').read_text())[0]
        self.new = '.'.join(map(str, (self.current[0] + 1, 0, 0)))

    def snapshot(self):
        return {name: (self.root / name).read_bytes() for name in update.PACKAGE_FILES}

    def test_signed_redirect_and_untrusted_redirect(self):
        location = 'https://captureage.azureedge.net/cade/prod/CaptureAge-1.27.0-x64.nsis.7z?sig=secret'
        self.assertEqual(update.version_from_redirect(location), '1.27.0')
        for bad in (location.replace('https:', 'http:'), location.replace('captureage.azureedge.net', 'example.com'),
                    location.replace('-x64', '-arm64'), location.replace('1.27.0', '1.27.0-beta')):
            with self.assertRaises(ValueError):
                update.version_from_redirect(bad)

    def test_unchanged_and_downgrade_do_not_download_or_modify(self):
        before = self.snapshot()
        def forbidden(*args):
            self.fail('No download should occur')
        self.assertFalse(update.update(self.root, '.'.join(map(str, self.current)), forbidden))
        self.assertFalse(update.update(self.root, '0.0.0', forbidden))
        self.assertEqual(self.snapshot(), before)

    def test_failed_download_leaves_recipe_unchanged(self):
        before = self.snapshot()
        def fail(*args):
            raise ValueError('Simulated download failure')
        with self.assertRaises(ValueError):
            update.update(self.root, self.new, fail)
        self.assertEqual(self.snapshot(), before)

    def test_download_rejects_archive_version_mismatch(self):
        payload = io.BytesIO(b'archive content')
        metadata = subprocess.CompletedProcess([], 0, stdout=json.dumps({'version': '0.0.0'}).encode())
        with patch.object(update, 'build_opener') as opener, patch.object(update.subprocess, 'run', return_value=metadata):
            opener.return_value.open.return_value = payload
            with self.assertRaisesRegex(ValueError, 'metadata does not match'):
                update.download(self.new, self.root / 'archive.7z')

    @unittest.skipUnless(shutil.which('makepkg'), 'makepkg is needed to generate .SRCINFO')
    def test_new_release_refreshes_version_release_and_all_checksums(self):
        recipe = self.root / 'PKGBUILD'
        recipe.write_text(recipe.read_text().replace('pkgrel=1', 'pkgrel=7'))
        payload = b'test archive payload'
        digest = hashlib.sha256(payload).hexdigest()
        def fetch(version, destination):
            self.assertEqual(version, self.new)
            destination.write_bytes(payload)
            return digest
        self.assertTrue(update.update(self.root, self.new, fetch))
        srcinfo = (self.root / '.SRCINFO').read_text()
        self.assertEqual(publish_aur.package_version(srcinfo), (update.version_tuple(self.new), 1))
        self.assertIn(f'CaptureAge:DE {self.new}.', (self.root / 'README.md').read_text())
        for name in update.LOCAL_SOURCES:
            self.assertIn(hashlib.sha256((self.root / name).read_bytes()).hexdigest(), srcinfo)
        self.assertIn(digest, srcinfo)
        self.assertEqual((self.root / f'CaptureAge-{self.new}-x64.nsis.7z').read_bytes(), payload)
        self.assertFalse(update.update(self.root, self.new, fetch))


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.remote = self.root / 'remote.git'
        subprocess.run(['git', 'init', '--bare', '--initial-branch=master', str(self.remote)],
                       check=True, capture_output=True)
        self.source = self.root / 'source'
        self.source.mkdir()
        for name in update.PACKAGE_FILES:
            shutil.copyfile(update.ROOT / name, self.source / name)

    def publish(self, dry_run=False):
        return publish_aur.publish(self.source, str(self.remote), 'Test Maintainer', 'test@example.invalid', dry_run)

    def test_initial_publish_whitelist_noop_and_history(self):
        (self.source / 'do-not-publish.key').write_text('test-only placeholder')
        self.assertTrue(self.publish())
        files = publish_aur.git(self.remote, 'ls-tree', '-r', '--name-only', 'master').splitlines()
        self.assertEqual(set(files), set(update.PACKAGE_FILES))
        first = publish_aur.git(self.remote, 'rev-parse', 'master')
        self.assertFalse(self.publish())
        self.assertEqual(publish_aur.git(self.remote, 'rev-parse', 'master'), first)
        (self.source / 'captureage').write_text('# updated test launcher\n')
        self.assertTrue(self.publish())
        self.assertEqual(publish_aur.git(self.remote, 'rev-parse', 'master^'), first)

    def test_dry_run_does_not_push(self):
        self.assertTrue(self.publish(True))
        result = subprocess.run(['git', '-C', str(self.remote), 'show-ref', '--heads'], capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b'')

    def test_publisher_refuses_remote_downgrade(self):
        self.publish()
        recipe = self.source / '.SRCINFO'
        import re
        recipe.write_text(re.sub(r'pkgver = [0-9.]+', 'pkgver = 0.0.0', recipe.read_text()))
        with self.assertRaisesRegex(ValueError, 'newer package'):
            self.publish()


if __name__ == '__main__':
    unittest.main()
