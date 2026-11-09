"""汇总通过启动验证的系统镜像、AVD 模板、导入脚本和文件摘要。"""
import argparse
import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path


def package(work):
    verification = json.loads((work / 'verification.json').read_text())
    if verification.get('status') != 'passed':
        raise ValueError('打包要求启动验证通过')
    output = work / 'output'
    project = Path(__file__).resolve().parent.parent
    for name in ('manifest.json', 'build-report.json', 'verification.json'):
        shutil.copyfile(work / name, output / name)
    shutil.copyfile(project / 'avdlite/import_avd.py', output / 'import-avd.py')
    shutil.copyfile(project / 'resources/avd-config.json', output / 'avd-config.json')
    (output / '使用说明.md').write_text('# AvdLite\n\n使用 Python 3.11 或更新版本导入：\n\n```text\npython import-avd.py --sdk <Android SDK 目录>\n```\n\n通过 `--avd-home <AVD 目录>` 和 `--name <名称>` 指定数据目录与名称。默认配置为 2 核、4096 MB 内存、32 GB 存储、Resizable 和 Quick Boot。导入脚本验证文件摘要并创建独立 AVD。\n', encoding='utf-8')
    hashes = []
    for path in sorted(output.rglob('*')):
        if path.is_file() and path.name != 'SHA256SUMS':
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            hashes.append(f'{digest}  {path.relative_to(output).as_posix()}')
    (output / 'SHA256SUMS').write_text('\n'.join(hashes) + '\n', encoding='utf-8')
    manifest = json.loads((work / 'manifest.json').read_text())
    source = manifest['image']
    image_name = 'AvdLiteWithKsu' if manifest['ksu'] else 'AvdLiteWithoutKsu'
    properties = dict(line.split('=', 1) for line in (output / 'x86_64/build.prop').read_text(encoding='utf-8').splitlines() if '=' in line)
    android_version = properties['ro.build.version.release']
    if not re.fullmatch(r'\d+(?:\.\d+)*', android_version):
        raise ValueError('打包要求正式 Android 版本号：' + android_version)
    name = f'{image_name}-Android{android_version}-api{source["api"]}'
    release = work / 'release'
    release.mkdir()
    archive = release / (name + '.zip')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        for path in sorted(output.rglob('*')):
            if path.is_file():
                target.write(path, path.relative_to(output))
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    (release / (name + '.sha256')).write_text(f'{digest}  {archive.name}\n')
    print(f'交付包：{archive.name}，{archive.stat().st_size} 字节，SHA-256 {digest}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', type=Path, required=True)
    package(parser.parse_args().work)
