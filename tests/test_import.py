import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from avdlite.import_avd import install


class ImportTests(unittest.TestCase):
    def test_verifies_bundle_before_creating_avd_and_preserves_existing_target(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bundle, sdk, avds = root / 'bundle', root / 'sdk', root / 'avds'
            (bundle / 'x86_64').mkdir(parents=True)
            (sdk / 'emulator').mkdir(parents=True)
            (sdk / 'emulator' / ('emulator.exe' if os.name == 'nt' else 'emulator')).touch()
            (bundle / 'manifest.json').write_text(json.dumps({'image': {'api': '37.2', 'page_size': '16k'}, 'ksu': None}))
            (bundle / 'avd-config.json').write_text('{"hw.cpu.ncore":"2"}')
            image = bundle / 'x86_64/system.img'
            image.write_bytes(b'example-image')
            digest = hashlib.sha256(image.read_bytes()).hexdigest()
            (bundle / 'SHA256SUMS').write_text(digest + '  x86_64/system.img\n')
            image.write_bytes(b'corrupted')
            with self.assertRaisesRegex(ValueError, '摘要'):
                install(bundle, sdk, avds, 'check')
            self.assertFalse(avds.exists())
            image.write_bytes(b'example-image')
            target = install(bundle, sdk, avds, 'check')
            configuration = (target / 'config.ini').read_text()
            self.assertIn('hw.cpu.ncore=2', configuration)
            with self.assertRaises(FileExistsError):
                install(bundle, sdk, avds, 'check')
            self.assertEqual((target / 'config.ini').read_text(), configuration)
            self.assertTrue(install(bundle, sdk, avds, 'second').exists())


if __name__ == '__main__':
    unittest.main()
