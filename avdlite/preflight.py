"""在修改前验证官方归档、分区布局与文件系统格式。"""
import argparse
import json
import re
import zipfile
from pathlib import Path, PurePosixPath
from .layout import partitions, read_at


def extract(archive, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        for item in source.infolist():
            name = PurePosixPath(item.filename)
            if name.is_absolute() or '..' in name.parts or '\\' in item.filename:
                raise ValueError('归档包含不安全路径')
            if item.is_dir():
                continue
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('不接受归档中的符号链接')
        source.extractall(destination)
    systems = list(destination.rglob('system.img'))
    if len(systems) != 1:
        raise ValueError('归档必须包含唯一 system.img')
    return systems[0].parent


def inspect(root):
    image = Path(root) / 'system.img'
    data = partitions(image)
    for partition in data['partitions']:
        offset = partition['extents'][0]['offset']
        partition['filesystem'] = ('ext4' if read_at(image, offset + 1080, 2) == b'\x53\xef' else
                                   'erofs' if read_at(image, offset + 1024, 4) == b'\xe2\xe1\xf5\xe0' else 'unknown')
    prop = (Path(root) / 'build.prop').read_text(errors='replace')
    data['build'] = {k: v for k, v in (line.split('=', 1) for line in prop.splitlines() if '=' in line)
                     if k in ('ro.build.version.sdk', 'ro.build.version.sdk_full', 'ro.build.version.incremental', 'ro.product.cpu.abi')}
    data['kernel_bytes'] = (Path(root) / 'kernel-ranchu').stat().st_size
    if any(part['filesystem'] == 'unknown' for part in data['partitions']):
        raise ValueError('发现未支持的分区文件系统')
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    root = extract(args.archive, args.work / 'source')
    report = inspect(root)
    (args.work / 'preflight.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
