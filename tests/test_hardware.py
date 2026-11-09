import hashlib
import tempfile
import unittest
from pathlib import Path
from avdlite.customize import hardware, hardware_policy


class HardwareConfigurationTests(unittest.TestCase):
    def test_uwb_requires_port_and_runs_once(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            vendor, product = root / 'vendor', root / 'product'
            (vendor / 'etc/init/hw').mkdir(parents=True)
            (vendor / 'etc/permissions').mkdir()
            product.mkdir()
            rc = vendor / 'etc/init/hw/init.ranchu.rc'
            rc.write_text('on property:vendor.qemu.vport.uwb=*\n    symlink ${vendor.qemu.vport.uwb} /dev/uwb0\n\nservice vendor.uwb_hal /vendor/bin/hw/android.hardware.uwb-service /dev/uwb0\n    class hal\n    user uwb\n\nservice unrelated /vendor/bin/example\n    class main\n')
            (vendor / 'etc/permissions/android.hardware.uwb.xml').write_text('<permissions/>')
            trees = {'vendor': vendor, 'product': product}
            hardware(trees)
            first = rc.read_text()
            self.assertIn('    start vendor.uwb_hal', first)
            self.assertIn('    disabled', first)
            self.assertIn('    oneshot', first)
            self.assertIn('service unrelated /vendor/bin/example\n    class main\n', first)
            hardware(trees)
            self.assertEqual(first, rc.read_text())
            generated = (product / 'etc/init/avdlite-hardware.rc').read_text()
            self.assertIn('on post-fs-data && property:vendor.qemu.vport.uwb=\n', generated)
            self.assertIn('/vendor/etc/permissions/android.hardware.uwb.xml bind', generated)
            self.assertIn('/apex/com.android.hardware.nfc/etc/permissions/android.hardware.nfc.prebuilt.xml bind', generated)

    def test_mount_policy_updates_platform_identity_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            policy_dir = root / 'system/etc/selinux'
            (policy_dir / 'mapping').mkdir(parents=True)
            policy = policy_dir / 'plat_sepolicy.cil'
            original = '(type vendor_configs_file)\n'
            policy.write_text(original)
            identity = policy_dir / 'plat_sepolicy_and_mapping.sha256'
            identity.write_text(hashlib.sha256(original.encode()).hexdigest())
            mapping = policy_dir / 'mapping/35.0.cil'
            mapping.write_text('(typeattribute vendor_configs_file_35_0)\n')
            old_identity = identity.read_text()
            rule = hardware_policy({'system': root})
            self.assertEqual(rule, '(allow init vendor_configs_file (file (mounton)))')
            self.assertIn(rule, policy.read_text())
            expected = hashlib.sha256(policy.read_bytes() + mapping.read_bytes()).hexdigest()
            self.assertEqual(identity.read_text().strip(), expected)
            self.assertNotEqual(old_identity, expected)
            first = policy.read_bytes()
            hardware_policy({'system': root})
            self.assertEqual(policy.read_bytes(), first)
            self.assertEqual(identity.read_text().strip(), expected)

    def test_port_trigger_is_required_when_uwb_service_exists(self):
        with tempfile.TemporaryDirectory() as folder:
            vendor = Path(folder) / 'vendor'
            (vendor / 'etc/init/hw').mkdir(parents=True)
            (vendor / 'etc/init/hw/init.ranchu.rc').write_text('service vendor.uwb_hal /vendor/bin/hw/android.hardware.uwb-service /dev/uwb0\n    class hal\n')
            with self.assertRaisesRegex(ValueError, 'trigger'):
                hardware({'vendor': vendor, 'product': Path(folder) / 'product'})


if __name__ == '__main__':
    unittest.main()
