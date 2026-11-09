"""Cold boot, reboot, and verify an isolated AVD, then stop the test emulator."""
import argparse
import json
import os
import re
import shlex
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from PIL import Image


class Device:
    def __init__(self, sdk, evidence, env):
        self.adb = str(sdk / 'platform-tools/adb')
        self.evidence = evidence
        self.env = env
        self.process = None

    def call(self, *args, check=True, timeout=30):
        result = subprocess.run([self.adb, '-s', 'emulator-5554', *args], capture_output=True, text=True, timeout=timeout, env=self.env)
        if check and result.returncode:
            raise RuntimeError(result.stderr + result.stdout)
        return result.stdout.strip()

    def shell(self, *args, **kwargs):
        return self.call('shell', shlex.join(args), **kwargs)

    def wait(self, probe, description, timeout=420):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f'Emulator exited early with code {self.process.returncode}')
            if probe():
                return
            time.sleep(3)
        raise TimeoutError(description)

    def ready(self):
        self.wait(lambda: self.shell('getprop', 'sys.boot_completed', check=False) == '1', 'Timed out waiting for Android boot')
        self.wait(lambda: 'package:' in self.shell('pm', 'path', 'com.android.systemui', check=False), 'Timed out waiting for the package manager')

    def open_ui(self, package, name, *intent):
        self.shell('input', 'keyevent', 'KEYCODE_WAKEUP')
        self.shell('wm', 'dismiss-keyguard')
        self.shell('am', 'start', '-W', *intent)

        def drawn():
            result = self.shell('uiautomator', 'dump', '--compressed', '/data/local/tmp/avdlite-ui.xml', check=False)
            if 'dumped to:' not in result:
                return False
            xml = self.shell('cat', '/data/local/tmp/avdlite-ui.xml')
            nodes = ET.fromstring(xml).iter('node')
            ready = any(node.get('package') == package and node.get('clickable') == 'true' for node in nodes)
            if ready:
                (self.evidence / name).write_text(xml, encoding='utf-8')
            return ready

        self.wait(drawn, 'Timed out waiting for interactive UI: ' + package, timeout=120)

    def screenshot(self, name):
        displays = self.shell('dumpsys', 'SurfaceFlinger', '--display-id')
        match = re.search(r'Display (\d+)', displays)
        if not match:
            raise AssertionError('Display ID query returned no display for the screenshot')
        path = self.evidence / name

        def visible():
            with path.open('wb') as screenshot:
                subprocess.run([self.adb, '-s', 'emulator-5554', 'exec-out', 'screencap', '-d', match[1], '-p'],
                               stdout=screenshot, check=True, timeout=30, env=self.env)
            with Image.open(path) as frame:
                # Exclude the navigation handle when checking for visible application content.
                content = frame.convert('RGB').crop((0, 0, frame.width, frame.height * 9 // 10))
                return any(high - low > 8 for low, high in content.getextrema())

        self.wait(visible, 'Timed out waiting for visible pixels in ' + name, timeout=120)

    def capture(self, name, *args):
        text = self.shell(*args, check=False, timeout=60)
        (self.evidence / name).write_text(text)
        return text


def verify(sdk, work, manifest):
    sdk, work = sdk.resolve(), work.resolve()
    image = work / 'output/x86_64'
    evidence = work / 'evidence'
    evidence.mkdir()
    avds = work / 'avds'
    avd = avds / 'AvdLiteCI.avd'
    avd.mkdir(parents=True)
    (avds / 'AvdLiteCI.ini').write_text(f'avd.ini.encoding=UTF-8\npath={avd}\ntarget=android-{manifest["image"]["api"]}\n')
    values = json.loads((Path(__file__).resolve().parent.parent / 'resources/avd-config.json').read_text())
    values.update({'AvdId': 'AvdLiteCI', 'avd.ini.displayname': 'AvdLiteCI', 'image.sysdir.1': str(image) + '/',
                   'hw.gpu.mode': 'swiftshader_indirect', 'fastboot.forceColdBoot': 'yes', 'fastboot.forceFastBoot': 'no'})
    (avd / 'config.ini').write_text(''.join(f'{k}={v}\n' for k, v in values.items()))
    emulator_home = work / 'emulator-home'
    emulator_home.mkdir()
    env = dict(os.environ, ANDROID_AVD_HOME=str(avds), ANDROID_EMULATOR_HOME=str(emulator_home),
               ANDROID_USER_HOME=str(emulator_home), QT_QPA_PLATFORM='offscreen')
    device = Device(sdk, evidence, env)
    subprocess.run([device.adb, 'start-server'], check=True, env=env)
    report = {'ksu_enabled': bool(manifest['ksu']), 'checks': {}}
    try:
        with (evidence / 'emulator.log').open('w') as log:
            command = [str(sdk / 'emulator/emulator'), '-avd', 'AvdLiteCI', '-port', '5554', '-no-window', '-no-audio',
                       '-no-boot-anim', '-no-snapshot', '-no-metrics', '-gpu', 'swiftshader_indirect', '-feature', '-Vulkan', '-accel', 'on', '-verbose']
            device.process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env)
            device.ready()
            for package in ('com.google.android.gms', 'com.google.android.gsf', 'com.google.android.apps.nexuslauncher', 'com.google.android.inputmethod.latin'):
                if 'package:' not in device.shell('pm', 'path', package):
                    raise AssertionError('Core application verification failed: ' + package)
            report['checks']['core_packages'] = True
            if device.shell('getenforce') != 'Enforcing':
                raise AssertionError('SELinux must be Enforcing')
            report['checks']['selinux_enforcing'] = True
            features = device.capture('features.txt', 'pm', 'list', 'features')
            for feature in ('uwb', 'nfc'):
                port = device.shell('getprop', 'vendor.qemu.vport.' + feature)
                report['checks'][feature + '_port'] = port
                if not port and re.search(r'feature:android\.hardware\.' + feature + r'(?:[.=\s]|$)', features):
                    raise AssertionError('Hardware feature declared with its emulator port absent: ' + feature)
            for name in ('config_lockDayNightMode', 'config_lockUiMode'):
                value = device.shell('cmd', 'overlay', 'lookup', 'android', 'android:bool/' + name)
                if value != 'false':
                    raise AssertionError(name + ' lock state verification failed: ' + value)
            for name in ('status_bar_padding_start', 'status_bar_padding_end'):
                value = device.shell('cmd', 'overlay', 'lookup', 'com.android.systemui', 'com.android.systemui:dimen/' + name)
                if not re.fullmatch(r'12(?:\.0+)?(?:dip|dp)', value):
                    raise AssertionError('Status bar padding verification failed: ' + value)
            report['checks']['overlays'] = True
            if manifest['ksu']:
                device.wait(lambda: device.shell('getprop', 'sys.avdlite.ksu_ready', check=False) == '1', 'Timed out waiting for KernelSU initialization', timeout=120)
                uid = device.shell('su', '-c', 'id')
                if 'uid=0' not in uid:
                    raise AssertionError('KernelSU root verification failed')
                report['checks']['ksu_version'] = device.shell('su', '-c', '/data/adb/ksud debug version')
                if not device.shell('pm', 'path', 'me.weishu.kernelsu').startswith('package:/data/app/'):
                    raise AssertionError('KernelSU manager must be installed in the data partition')
            else:
                if 'package:' in device.shell('pm', 'path', 'me.weishu.kernelsu', check=False):
                    raise AssertionError('Standard image unexpectedly contains the KernelSU manager')
            device.open_ui('com.google.android.apps.nexuslauncher', 'home-cold.xml',
                           '-a', 'android.intent.action.MAIN', '-c', 'android.intent.category.HOME')
            report['checks']['interactive_home_after_cold_boot'] = True
            device.shell('cmd', 'uimode', 'night', 'custom_schedule')
            for key, value in [('dark_theme_custom_start_time', '79200000000'), ('dark_theme_custom_end_time', '21600000000')]:
                device.shell('settings', 'put', 'secure', key, value)
            before = device.shell('cat', '/proc/sys/kernel/random/boot_id')
            device.call('reboot')
            device.wait(lambda: (value := device.shell('cat', '/proc/sys/kernel/random/boot_id', check=False)) not in ('', before), 'Timed out waiting for a new boot_id')
            device.ready()
            expected = {'ui_night_mode': '3', 'dark_theme_custom_start_time': '79200000000', 'dark_theme_custom_end_time': '21600000000'}
            actual = {key: device.shell('settings', 'get', 'secure', key) for key in expected}
            if actual != expected:
                raise AssertionError('Night schedule persistence verification failed: ' + repr(actual))
            report['checks']['night_schedule_after_reboot'] = actual
            if manifest['ksu']:
                device.wait(lambda: device.shell('getprop', 'sys.avdlite.ksu_ready', check=False) == '1', 'Timed out waiting for KernelSU initialization after reboot', timeout=120)
                if 'uid=0' not in device.shell('su', '-c', 'id'):
                    raise AssertionError('Root verification failed after reboot')
            device.open_ui('com.android.settings', 'settings.xml', '-a', 'android.settings.SETTINGS')
            device.screenshot('screen.png')
            device.open_ui('com.google.android.apps.nexuslauncher', 'home-reboot.xml',
                           '-a', 'android.intent.action.MAIN', '-c', 'android.intent.category.HOME')
            device.screenshot('home.png')
            report['checks']['interactive_home_and_settings_after_reboot'] = True
            device.capture('uimode.txt', 'dumpsys', 'uimode')
            device.capture('overlays.txt', 'cmd', 'overlay', 'list')
            report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        if device.process is not None:
            device.capture('logcat.txt', 'logcat', '-d', '-b', 'all')
            device.capture('properties.txt', 'getprop')
            device.capture('windows.txt', 'dumpsys', 'window', 'windows')
            device.capture('surfaces.txt', 'dumpsys', 'SurfaceFlinger')
            device.call('emu', 'kill', check=False)
            try:
                device.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                device.process.terminate()
                device.process.wait(timeout=10)
        (work / 'verification.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('sdk', 'work', 'manifest'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    verify(args.sdk, args.work, json.loads(args.manifest.read_text()))
