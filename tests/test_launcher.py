import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import configure_game


class GamePathTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.game = self.make_game(self.root / 'Other drive' / 'Steam Library ä' / 'AoE2DE')
        self.prefix = self.root / 'Custom prefix' / 'pfx'
        (self.prefix / 'dosdevices').mkdir(parents=True)
        (self.prefix / 'dosdevices/z:').symlink_to('/')
        (self.prefix / 'drive_c').mkdir()
        (self.prefix / 'dosdevices/c:').symlink_to('../drive_c')
        self.state = self.prefix / 'drive_c/users/steamuser/AppData/Roaming/CaptureAge/persistedState_prod.json'
        self.expected = 'Z:' + str(self.game).replace('/', '\\')

    def make_game(self, path):
        (path / 'resources').mkdir(parents=True)
        (path / 'AoE2DE_s.exe').touch()
        return path

    def write_state(self, game_path):
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.write_text(json.dumps({
            'lastUsedGameDirectory': game_path,
            'volume': 0.25,
            'other': {'language': 'de', 'custom': ['unchanged']},
        }), encoding='utf-8')

    def backups(self):
        return list(self.state.parent.glob('persistedState_prod.json.backup-*'))

    def test_first_launch_sets_external_library_path_and_custom_prefix(self):
        env = dict(os.environ, STEAM_APP_PATH=str(self.game), WINEPREFIX=str(self.prefix))
        result = subprocess.run([sys.executable, str(ROOT / 'configure_game.py')], env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.state.read_text())['lastUsedGameDirectory'], self.expected)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.backups(), [])

    def test_invalid_windows_default_is_repaired_with_backup_and_other_settings_preserved(self):
        self.write_state(r'C:\\Program Files (x86)\\Steam\\steamapps\\common\\AoE2DE')
        self.state.chmod(0o640)
        before = self.state.read_bytes()
        self.assertTrue(configure_game.configure_game(self.game, self.prefix))
        repaired = json.loads(self.state.read_text())
        original = json.loads(before)
        original['lastUsedGameDirectory'] = self.expected
        self.assertEqual(repaired, original)
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(self.backups()[0].read_bytes(), before)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o640)
        after = self.state.read_bytes()
        self.assertFalse(configure_game.configure_game(self.game, self.prefix))
        self.assertEqual(self.state.read_bytes(), after)
        self.assertEqual(len(self.backups()), 1)

    def test_valid_manual_directory_on_another_drive_is_preserved(self):
        manual = self.make_game(self.root / 'Manual installation')
        (self.prefix / 'dosdevices/s:').symlink_to(manual.parent)
        self.write_state(r'S:\Manual installation')
        before = self.state.read_bytes()
        self.assertFalse(configure_game.configure_game(self.game, self.prefix))
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(self.backups(), [])

    def test_game_move_repairs_a_stale_saved_path(self):
        self.write_state('Z:' + str(self.root / 'Old drive/AoE2DE').replace('/', '\\'))
        self.assertTrue(configure_game.configure_game(self.game, self.prefix))
        self.assertEqual(json.loads(self.state.read_text())['lastUsedGameDirectory'], self.expected)

    def test_uses_existing_drive_mapping_when_z_is_unavailable(self):
        (self.prefix / 'dosdevices/z:').unlink()
        (self.prefix / 'dosdevices/s:').symlink_to(self.game.parent)
        configure_game.configure_game(self.game, self.prefix)
        self.assertEqual(json.loads(self.state.read_text())['lastUsedGameDirectory'], r'S:\AoE2DE')

    def test_unavailable_game_does_not_change_settings(self):
        self.write_state(r'C:\invalid')
        before = self.state.read_bytes()
        with self.assertRaisesRegex(ValueError, 'game files are missing'):
            configure_game.configure_game(self.root / 'Unmounted game', self.prefix)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(self.backups(), [])

    def test_directory_without_game_assets_is_rejected(self):
        (self.game / 'AoE2DE_s.exe').unlink()
        with self.assertRaisesRegex(ValueError, 'game files are missing'):
            configure_game.configure_game(self.game, self.prefix)
        self.assertFalse(self.state.exists())

    def test_unmapped_game_does_not_change_settings(self):
        self.write_state(r'C:\invalid')
        before = self.state.read_bytes()
        (self.prefix / 'dosdevices/z:').unlink()
        with self.assertRaisesRegex(ValueError, 'No Wine drive'):
            configure_game.configure_game(self.game, self.prefix)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(self.backups(), [])

    def test_missing_prefix_is_not_created(self):
        missing = self.root / 'Missing prefix/pfx'
        with self.assertRaisesRegex(ValueError, 'Proton prefix is missing'):
            configure_game.configure_game(self.game, missing)
        self.assertFalse(missing.exists())

    def test_malformed_or_unexpected_settings_are_preserved(self):
        self.state.parent.mkdir(parents=True)
        for original in ('{broken', '[]'):
            with self.subTest(original=original):
                self.state.write_text(original)
                with self.assertRaises(ValueError):
                    configure_game.configure_game(self.game, self.prefix)
                self.assertEqual(self.state.read_text(), original)
                self.assertEqual(self.backups(), [])

    def test_failed_atomic_replace_preserves_original(self):
        self.write_state(r'C:\invalid')
        before = self.state.read_bytes()
        with patch.object(Path, 'replace', side_effect=OSError('Simulated write failure')):
            with self.assertRaises(OSError):
                configure_game.configure_game(self.game, self.prefix)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(self.backups()[0].read_bytes(), before)
        self.assertEqual(set(self.state.parent.iterdir()), {self.state, *self.backups()})


if __name__ == '__main__':
    unittest.main()
