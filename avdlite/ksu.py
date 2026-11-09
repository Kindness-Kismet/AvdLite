"""Patch a standalone ramdisk with the latest official matching LKM and retain the stock kernel."""
import hashlib
import re
import shutil
import struct
import subprocess
import zipfile
from pathlib import Path

RESOURCES = Path(__file__).resolve().parent.parent / 'resources'


def kernel_version(path):
    with Path(path).open('rb') as stream:
        stream.seek(0x20e)
        offset = struct.unpack('<H', stream.read(2))[0]
        stream.seek(0x200 + offset)
        version = stream.read(512).split(b'\0', 1)[0].decode('ascii')
    match = re.search(r'(\d+\.\d+)\.\d+-(android\d+)-', version)
    if not match:
        raise ValueError('Kernel KMI detection from the x86 boot header failed: ' + version)
    return version, f'{match[2]}-{match[1]}'


def install(root, trees, cache, release, work):
    version, kmi = kernel_version(root / 'kernel-ranchu')
    name = f'lkm-x86_64-{kmi}_kernelsu.ko'
    if name not in {asset['name'] for asset in release['assets']}:
        raise ValueError(f'Latest KernelSU {release["tag"]} is missing a matching {kmi} module')
    tool = cache / 'ksu/ksud-x86_64-unknown-linux-musl'
    tool.chmod(0o755)
    patched = work / 'ksu-patched'
    patched.mkdir()
    subprocess.run([str(tool), 'boot-patch', '--boot', str(root / 'ramdisk.img'), '--ramdisk', '--arch', 'x86_64',
                    '--module', str(cache / 'ksu' / name), '--kmi', kmi, '--allow-shell',
                    '--out', str(patched), '--out-name', 'ramdisk.img'], check=True)
    shutil.copyfile(patched / 'ramdisk.img', root / 'ramdisk.img')
    managers = list((cache / 'ksu').glob('KernelSU_*-release.apk'))
    expected = {asset['name'] for asset in release['assets']}
    managers = [p for p in managers if p.name in expected]
    if len(managers) != 1:
        raise ValueError('The selected release must contain exactly one KernelSU manager')
    app = trees['product'] / 'app/KernelSU'
    (app / 'lib/x86_64').mkdir(parents=True)
    shutil.copyfile(managers[0], app / 'KernelSU.apk')
    with zipfile.ZipFile(managers[0]) as archive:
        (app / 'lib/x86_64/libksud.so').write_bytes(archive.read('lib/x86_64/libksud.so'))
    for resource, target in [('ksu-setup.sh', 'etc/avdlite/ksu-setup.sh'), ('ksu-setup.rc', 'etc/init/avdlite-ksu.rc')]:
        shutil.copyfile(RESOURCES / resource, trees['product'] / target)
    return {'release': release['tag'], 'kernel': version, 'kmi': kmi, 'module': name}
