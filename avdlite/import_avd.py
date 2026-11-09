"""将 AvdLite 交付包导入指定 Android SDK，并创建独立 AVD。"""
import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path


def install(bundle, sdk, avd_home, name=None):
    manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
    variant = 'ksu' if manifest['ksu'] else 'plain'
    image = manifest['image']
    name = name or f'AvdLite-{image["api"]}-{image["page_size"]}-{variant}'
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
        raise ValueError('AVD 名称可使用字母、数字、点、下划线和连字符')
    for line in (bundle / 'SHA256SUMS').read_text().splitlines():
        digest, filename = line.split('  ', 1)
        path = bundle / filename
        path.resolve().relative_to(bundle.resolve())
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                raise ValueError('交付文件摘要校验失败：' + filename)
    sdk, avd_home = sdk.resolve(), avd_home.resolve()
    emulator = sdk / 'emulator' / ('emulator.exe' if os.name == 'nt' else 'emulator')
    if not emulator.exists():
        raise ValueError('指定 SDK 缺少 Android Emulator')
    target = sdk / 'system-images' / ('android-' + image['api']) / ('avdlite_' + variant) / 'x86_64'
    avd = avd_home / (name + '.avd')
    registry = avd_home / (name + '.ini')
    for path in (target, avd, registry):
        if path.exists():
            raise FileExistsError('目标路径已存在，请选择独立 SDK 或 AVD 名称：' + str(path))
    shutil.copytree(bundle / 'x86_64', target)
    avd.mkdir(parents=True)
    values = json.loads((bundle / 'avd-config.json').read_text())
    values.update({'AvdId': name, 'avd.ini.displayname': name, 'image.sysdir.1': target.as_posix() + '/'})
    (avd / 'config.ini').write_text(''.join(f'{k}={v}\n' for k, v in values.items()), encoding='utf-8')
    registry.write_text(f'avd.ini.encoding=UTF-8\npath={avd}\ntarget=android-{image["api"]}\n', encoding='utf-8')
    print(f'已创建 AVD：{name}\n系统镜像：{target}\nAVD 目录：{avd}')
    print(f'启动命令："{emulator}" -avd {name}')
    if avd_home != Path(os.environ.get('ANDROID_AVD_HOME', Path.home() / '.android/avd')).resolve():
        print(f'启动前设置环境变量 ANDROID_AVD_HOME={avd_home}')
    return avd


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, required=True, help='Android SDK 目录')
    parser.add_argument('--avd-home', type=Path, default=Path(os.environ.get('ANDROID_AVD_HOME', Path.home() / '.android/avd')))
    parser.add_argument('--name')
    args = parser.parse_args()
    install(Path(__file__).resolve().parent, args.sdk, args.avd_home, args.name)
