"""在临时副本中修改文件系统，重签 vbmeta 并输出独立模拟器镜像。"""
import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from . import assemble, customize, ksu, overlays, tools
from .layout import partitions, read_at, transfer


def run(*args, allowed=(0,)):
    print('+', ' '.join(map(str, args)), flush=True)
    result = subprocess.run(list(map(str, args)))
    if result.returncode not in allowed:
        raise subprocess.CalledProcessError(result.returncode, args)


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def sign_vbmeta(source, layout, work, avb):
    entry = next(p for p in layout['gpt'] if p['name'] == 'vbmeta')
    original = work / 'original-vbmeta.img'
    original.write_bytes(read_at(source, entry['offset'], entry['size']))
    tool, key = avb / 'avbtool.py', avb / 'avb-testkey.pem'
    public = work / 'avb-public.bin'
    run(sys.executable, tool, 'extract_public_key', '--key', key, '--output', public)
    data = original.read_bytes()
    if data[:4] != b'AVB0':
        raise ValueError('缺少 AVB header')
    auth_size = struct.unpack_from('>Q', data, 12)[0]
    key_offset, key_size = struct.unpack_from('>QQ', data, 64)
    if data[256 + auth_size + key_offset:256 + auth_size + key_offset + key_size] != public.read_bytes():
        raise ValueError('镜像 AVB 公钥校验失败，重签要求匹配 AOSP 测试密钥')
    spec = importlib.util.spec_from_file_location('avdlite_avbtool', tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    header = module.AvbVBMetaHeader(data[:256])
    if not module.verify_vbmeta_signature(header, data[:256 + header.authentication_data_block_size + header.auxiliary_data_block_size]):
        raise ValueError('原始 vbmeta 签名校验失败')
    output = work / 'vbmeta.img'
    rollback = struct.unpack_from('>Q', data, 112)[0]
    location = struct.unpack_from('>I', data, 124)[0]
    # 精简分区已重新布局，关闭旧哈希树校验，并用原信任公钥签名新 vbmeta。
    run(sys.executable, tool, 'make_vbmeta_image', '--output', output, '--algorithm', 'SHA256_RSA4096',
        '--key', key, '--flags', '3', '--rollback_index', rollback, '--rollback_index_location', location, '--padding_size', '4096')
    run(sys.executable, tool, 'verify_image', '--image', output, '--key', key)
    return output


@contextlib.contextmanager
def mounted(image, folder):
    folder.mkdir(parents=True)
    run('mount', '-t', 'ext4', '-o', 'loop,rw', image, folder)
    try:
        yield folder
    finally:
        run('umount', folder)


def build(manifest, cache, work, sdk):
    if os.geteuid() != 0:
        raise ValueError('构建需要 root 挂载独立文件系统副本')
    roots = list((work / 'source').rglob('system.img'))
    if len(roots) != 1:
        raise ValueError('先运行 preflight 解压官方镜像')
    root = roots[0].parent
    original = root / 'system.img'
    layout = partitions(original)
    output = work / 'output/x86_64'
    output.mkdir(parents=True)
    for path in root.iterdir():
        if path.is_file() and path.name not in ('system.img', 'package.xml'):
            shutil.copyfile(path, output / path.name)
        elif path.name == 'data' and path.is_dir():
            shutil.copytree(path, output / 'data')
    kernel_hash = sha256(root / 'kernel-ranchu')
    avb = tools.prepare(work)
    vbmeta = sign_vbmeta(original, layout, work, avb)
    files, trees, before = {}, {}, {}
    parts = work / 'partitions'
    parts.mkdir()
    with contextlib.ExitStack() as stack:
        for entry in layout['partitions']:
            path = parts / (entry['name'] + '.img')
            transfer(original, path, entry['extents'])
            files[entry['name']] = path
            if read_at(path, 1080, 2) != b'\x53\xef':
                if entry['name'] != 'system_dlkm':
                    raise ValueError('可修改分区必须是 ext4：' + entry['name'])
                continue
            run('e2fsck', '-fy', path, allowed=(0, 1))
            # 为新增资源预留空间，扩容对象为独立分区副本。
            with path.open('r+b') as stream:
                stream.truncate(path.stat().st_size + 512 * 1024 * 1024)
            run('resize2fs', path)
            if struct.unpack('<I', read_at(path, 1124, 4))[0] & 0x4000:
                # Android 共享数据块须先展开，ext4 才能以可写方式挂载。
                run('e2fsck', '-fy', '-E', 'unshare_blocks', path, allowed=(0, 1))
            tree = stack.enter_context(mounted(path, work / 'mounts' / entry['name']))
            trees[entry['name']] = tree
            before[entry['name']] = {str(p.relative_to(tree)): sha256(p) for p in tree.rglob('build.prop')}
        sdk_tools = overlays.build(work, sdk, trees)
        report = {'source': manifest['image'], 'debloat': customize.debloat(trees, sdk_tools / 'aapt2'),
                  'hardware': customize.hardware(trees), 'ksu': None}
        if manifest['ksu']:
            report['ksu'] = ksu.install(output, trees, cache, manifest['ksu'], work)
        customize.labels(trees)
        after = {name: {str(p.relative_to(tree)): sha256(p) for p in tree.rglob('build.prop')} for name, tree in trees.items()}
        if before != after:
            raise ValueError('构建意外修改了上游 build.prop')
        report['upstream_build_properties_unchanged'] = True
    for name, path in files.items():
        if name not in trees:
            continue
        run('e2fsck', '-fy', path, allowed=(0, 1))
        run('resize2fs', '-M', path)
        run('zerofree', path)
        run('e2fsck', '-fn', path)
    report['layout'] = assemble.assemble(original, files, output / 'system.img', vbmeta)
    run('sgdisk', '--verify', output / 'system.img')
    vb = vbmeta.read_bytes()
    meaningful = 256 + struct.unpack_from('>Q', vb, 12)[0] + struct.unpack_from('>Q', vb, 20)[0]
    digest = hashlib.sha256(vb[:meaningful]).hexdigest()
    (output / 'VerifiedBootParams.textproto').write_text(f'major_version: 2\nparam: "androidboot.vbmeta.size={meaningful}"\nparam: "androidboot.vbmeta.hash_alg=sha256"\nparam: "androidboot.vbmeta.digest={digest}"\n')
    props = (output / 'source.properties').read_text()
    props = re.sub(r'^Pkg.Desc=.*$', 'Pkg.Desc=AvdLite Google APIs x86_64', props, flags=re.MULTILINE)
    (output / 'source.properties').write_text(props)
    if sha256(output / 'kernel-ranchu') != kernel_hash:
        raise ValueError('原内核被意外修改')
    report['kernel_sha256'] = kernel_hash
    report['original_bytes'] = original.stat().st_size
    report['system_sha256'] = sha256(output / 'system.img')
    (work / 'build-report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps(report, indent=2, ensure_ascii=False), flush=True)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('manifest', 'cache', 'work', 'sdk'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    build(json.loads(args.manifest.read_text()), args.cache.resolve(), args.work.resolve(), args.sdk.resolve())
