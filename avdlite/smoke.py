"""在隔离 AVD 中冷启动、重启并验证实际运行状态，退出时关闭测试模拟器。"""
import argparse
import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path


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
                raise RuntimeError(f'模拟器提前退出，退出码 {self.process.returncode}')
            if probe():
                return
            time.sleep(3)
        raise TimeoutError(description)

    def ready(self):
        self.wait(lambda: self.shell('getprop', 'sys.boot_completed', check=False) == '1', '等待 Android 启动超时')
        self.wait(lambda: 'package:' in self.shell('pm', 'path', 'com.android.systemui', check=False), '等待包管理器超时')

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
            diagnostic = ['gdb', '--batch', '--return-child-result', '-ex', 'set pagination off',
                          '-ex', 'handle SIGUSR1 SIGUSR2 SIGPIPE nostop noprint pass',
                          '-ex', 'run', '-ex', 'thread apply all bt 12', '--args']
            device.process = subprocess.Popen(diagnostic + command, stdout=log, stderr=subprocess.STDOUT, env=env)
            device.ready()
            for package in ('com.google.android.gms', 'com.google.android.gsf', 'com.google.android.apps.nexuslauncher', 'com.google.android.inputmethod.latin'):
                if 'package:' not in device.shell('pm', 'path', package):
                    raise AssertionError('核心应用检查失败：' + package)
            report['checks']['core_packages'] = True
            if device.shell('getenforce') != 'Enforcing':
                raise AssertionError('SELinux 状态偏离 Enforcing')
            report['checks']['selinux_enforcing'] = True
            features = device.capture('features.txt', 'pm', 'list', 'features')
            for feature in ('uwb', 'nfc'):
                port = device.shell('getprop', 'vendor.qemu.vport.' + feature)
                report['checks'][feature + '_port'] = port
                if not port and re.search(r'feature:android\.hardware\.' + feature + r'(?:[.=\s]|$)', features):
                    raise AssertionError('缺少端口时仍声明了硬件功能：' + feature)
            for name in ('config_lockDayNightMode', 'config_lockUiMode'):
                value = device.shell('cmd', 'overlay', 'lookup', 'android', 'android:bool/' + name)
                if value != 'false':
                    raise AssertionError(name + ' 锁定状态校验失败：' + value)
            for name in ('status_bar_padding_start', 'status_bar_padding_end'):
                value = device.shell('cmd', 'overlay', 'lookup', 'com.android.systemui', 'com.android.systemui:dimen/' + name)
                if not re.fullmatch(r'12(?:\.0+)?(?:dip|dp)', value):
                    raise AssertionError('状态栏间距校验失败：' + value)
            report['checks']['overlays'] = True
            if manifest['ksu']:
                device.wait(lambda: device.shell('getprop', 'sys.avdlite.ksu_ready', check=False) == '1', '等待 KernelSU 初始化超时', timeout=120)
                uid = device.shell('su', '-c', 'id')
                if 'uid=0' not in uid:
                    raise AssertionError('KernelSU root 权限检查失败')
                report['checks']['ksu_version'] = device.shell('su', '-c', '/data/adb/ksud debug version')
                if not device.shell('pm', 'path', 'me.weishu.kernelsu').startswith('package:/data/app/'):
                    raise AssertionError('KernelSU 管理器的数据区安装校验失败')
            else:
                if 'package:' in device.shell('pm', 'path', 'me.weishu.kernelsu', check=False):
                    raise AssertionError('关闭 KSU 的镜像仍包含管理器')
            device.shell('cmd', 'uimode', 'night', 'custom')
            for key, value in [('dark_theme_custom_start_time', '79200000000'), ('dark_theme_custom_end_time', '21600000000')]:
                device.shell('settings', 'put', 'secure', key, value)
            before = device.shell('cat', '/proc/sys/kernel/random/boot_id')
            device.call('reboot')
            device.wait(lambda: (value := device.shell('cat', '/proc/sys/kernel/random/boot_id', check=False)) not in ('', before), '等待 boot_id 更新超时')
            device.ready()
            expected = {'ui_night_mode': '3', 'dark_theme_custom_start_time': '79200000000', 'dark_theme_custom_end_time': '21600000000'}
            actual = {key: device.shell('settings', 'get', 'secure', key) for key in expected}
            if actual != expected:
                raise AssertionError('主题定时设置的持久化检查失败：' + repr(actual))
            report['checks']['night_schedule_after_reboot'] = actual
            if manifest['ksu']:
                device.wait(lambda: device.shell('getprop', 'sys.avdlite.ksu_ready', check=False) == '1', '等待重启后的 KSU 初始化超时', timeout=120)
                if 'uid=0' not in device.shell('su', '-c', 'id'):
                    raise AssertionError('重启后的 root 权限检查失败')
            displays = device.shell('dumpsys', 'SurfaceFlinger', '--display-id')
            match = re.search(r'Display (\d+)', displays)
            if not match:
                raise AssertionError('截图查询缺少 display id')
            with (evidence / 'screen.png').open('wb') as screenshot:
                subprocess.run([device.adb, '-s', 'emulator-5554', 'exec-out', 'screencap', '-d', match[1], '-p'], stdout=screenshot, check=True, timeout=30, env=env)
            device.capture('uimode.txt', 'dumpsys', 'uimode')
            device.capture('overlays.txt', 'cmd', 'overlay', 'list')
            report['status'] = 'passed'
    finally:
        if device.process is not None:
            device.capture('logcat.txt', 'logcat', '-d', '-b', 'all')
            device.capture('properties.txt', 'getprop')
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
