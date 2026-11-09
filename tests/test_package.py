import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from avdlite.package import package


class PackageTests(unittest.TestCase):
    def test_archive_contains_importable_config_and_verified_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / 'output/x86_64').mkdir(parents=True)
            (work / 'output/x86_64/system.img').write_bytes(b'filesystem-fixture')
            (work / 'output/x86_64/build.prop').write_text('ro.build.version.release=17\n')
            (work / 'verification.json').write_text('{"status":"passed"}')
            (work / 'manifest.json').write_text(json.dumps({'image': {'api': '37.2', 'page_size': '16k'}, 'ksu': None}))
            (work / 'build-report.json').write_text('{}')
            package(work)
            archive = next((work / 'release').glob('*.zip'))
            self.assertEqual(archive.name, 'AvdLiteWithoutKsu-Android17-api37.2.zip')
            with zipfile.ZipFile(archive) as content:
                self.assertIn('import-avd.py', content.namelist())
                self.assertEqual(json.loads(content.read('avd-config.json'))['hw.ramSize'], '4096')
                for line in content.read('SHA256SUMS').decode().splitlines():
                    digest, name = line.split('  ', 1)
                    self.assertEqual(hashlib.sha256(content.read(name)).hexdigest(), digest)
            digest, name = next((work / 'release').glob('*.sha256')).read_text().strip().split('  ', 1)
            self.assertEqual(name, archive.name)
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), digest)

    def test_publishing_package_requires_passed_boot_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / 'verification.json').write_text('{"status":"failed"}')
            with self.assertRaisesRegex(ValueError, 'boot verification'):
                package(work)
            self.assertFalse((work / 'release').exists())


if __name__ == '__main__':
    unittest.main()
