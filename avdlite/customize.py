"""按包名精简应用，并根据模拟器端口配置硬件功能声明。"""
import contextlib
import gzip
import hashlib
import json
import lzma
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

RESOURCES = Path(__file__).resolve().parent.parent / 'resources'


def apk_package(path, aapt):
    with contextlib.ExitStack() as stack:
        if path.suffix in ('.gz', '.xz'):
            temporary = stack.enter_context(tempfile.TemporaryDirectory(prefix='avdlite-apk-'))
            unpacked = Path(temporary) / 'base.apk'
            opener = gzip.open if path.suffix == '.gz' else lzma.open
            with opener(path, 'rb') as source, unpacked.open('wb') as output:
                shutil.copyfileobj(source, output)
            path = unpacked
        result = subprocess.run([str(aapt), 'dump', 'badging', str(path)], capture_output=True, text=True)
    match = re.search(r"^package: name='([^']+)'", result.stdout, re.MULTILINE)
    if result.returncode or not match:
        raise ValueError(f'无法识别 APK：{path.name}')
    return match.group(1)


def debloat(trees, aapt):
    config = json.loads((RESOURCES / 'debloat.json').read_text())
    selected = {package: group['reason'] for group in config['groups'] for package in group['packages']}
    protected = set(config['protected'])
    if protected.intersection(selected):
        raise ValueError('精简清单包含受保护的系统依赖')
    removed, packages, inventory = [], set(), []
    for partition, tree in trees.items():
        prefix = tree / 'system' if partition == 'system' and (tree / 'system/app').exists() else tree
        if (prefix / 'framework/framework-res.apk').exists():
            packages.add(apk_package(prefix / 'framework/framework-res.apk', aapt))
        for directory in ('app', 'priv-app'):
            parent = prefix / directory
            if not parent.exists():
                continue
            for app in list(parent.iterdir()):
                if not app.is_dir() or app.is_symlink():
                    continue
                direct = [p for p in app.iterdir() if p.is_file() and p.name.endswith(('.apk', '.apk.gz', '.apk.xz'))]
                identities = {apk_package(apk, aapt) for apk in direct}
                packages.update(identities)
                inventory.append({'partition': partition, 'path': str(app.relative_to(tree)), 'packages': sorted(identities)})
                matches = identities.intersection(selected)
                if matches:
                    # 整目录移除要求目录内全部 APK 命中清单，共享目录交由人工审核。
                    if not identities.issubset(selected) or identities.intersection(protected):
                        raise ValueError('应用目录包含精简清单外的共享内容')
                    removed.append({'partition': partition, 'path': str(app.relative_to(tree)),
                                    'packages': sorted(matches), 'reason': selected[next(iter(matches))]})
                    shutil.rmtree(app)
    for entry in config['remove_paths']:
        tree = trees[entry['partition']]
        target = tree / entry['path']
        if target.exists():
            target.resolve().relative_to(tree.resolve())
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            removed.append(entry)
    # Chrome 的压缩预载副本须同步移除，以保持精简结果。
    product = trees['product']
    for path in list(product.rglob('*Chrome*.apk.gz')) + list(product.rglob('*Chrome*.apk.xz')):
        path.unlink()
        removed.append({'partition': 'product', 'path': str(path.relative_to(product)), 'reason': '移除 Chrome 压缩预载副本'})
    essentials = ('android', 'com.android.systemui', 'com.android.settings', 'com.google.android.gms', 'com.google.android.gsf',
                  'com.google.android.apps.nexuslauncher', 'com.google.android.inputmethod.latin')
    missing = set(essentials) - packages
    gms_apex = sorted(str(p.relative_to(product)) for p in (product / 'apex').glob('com.google.android.gmssystem.*.apex'))
    # 新版 GMS 主包位于 APEX，构建检查保留该容器，启动测试验证实际包注册。
    if gms_apex:
        missing.discard('com.google.android.gms')
    if missing:
        raise ValueError('镜像缺少已支持的核心应用：' + ','.join(sorted(missing)))
    return {'removed': removed, 'unavailable_candidates': sorted(set(selected) - packages),
            'protected_found': sorted(protected.intersection(packages)), 'gms_apex': gms_apex, 'original_apps': inventory}


def hardware(trees):
    vendor = trees['vendor']
    rc = vendor / 'etc/init/hw/init.ranchu.rc'
    text = rc.read_text()
    match = re.search(r'^service (\S+) .*uwb[^\n]*\n(?:(?:[ \t]+[^\n]*|[ \t]*)\n)*', text, re.MULTILINE)
    changes = []
    if match:
        service = match.group(1)
        block = match.group(0)
        for option in ('disabled', 'oneshot'):
            if not re.search(r'^\s+' + option + r'\s*$', block, re.MULTILINE):
                block = block.rstrip() + '\n    ' + option + '\n\n'
        text = text[:match.start()] + block + text[match.end():]
        trigger = re.search(r'^on property:vendor\.qemu\.vport\.uwb=\*\n(?:(?:[ \t]+[^\n]*|[ \t]*)\n)*', text, re.MULTILINE)
        if not trigger:
            raise ValueError('UWB 服务缺少可识别的模拟器端口触发器')
        if f'start {service}' not in trigger.group(0):
            insertion = trigger.group(0).rstrip() + f'\n    start {service}\n\n'
            text = text[:trigger.start()] + insertion + text[trigger.end():]
        rc.write_text(text)
        changes.append('UWB: disabled + oneshot，端口属性发布后启动')
    product = trees['product']
    directory = product / 'etc/avdlite'
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'empty-features.xml').write_text('<?xml version="1.0" encoding="utf-8"?><permissions/>\n')
    lines = ['# init 在应用框架扫描前通过内建 mount 挂载与端口状态对应的功能声明。']
    for feature in ('uwb', 'nfc'):
        # 空值属性条件在 post-fs-data 事件中求值，功能声明随本次启动的硬件配置确定。
        lines.append(f'on post-fs-data && property:vendor.qemu.vport.{feature}=')
        targets = []
        for partition, tree in trees.items():
            prefix = tree / 'system' if partition == 'system' and (tree / 'system/etc').exists() else tree
            permissions = prefix / 'etc/permissions'
            if permissions.exists():
                for path in permissions.glob(f'android.hardware.{feature}*.xml'):
                    relative = path.relative_to(prefix)
                    targets.append(f'/{partition}/{relative.as_posix()}')
        if feature == 'nfc':
            # 新版本 NFC APEX 也声明功能，挂载结果由 init 记录。
            targets += ['/apex/com.android.hardware.nfc/etc/permissions/android.hardware.nfc.prebuilt.xml',
                        '/apex/com.android.hardware.nfc/etc/permissions/android.hardware.nfc.hce.prebuilt.xml']
        for target in sorted(set(targets)):
            lines.append(f'    mount none /product/etc/avdlite/empty-features.xml {target} bind')
        lines.append('')
    init = product / 'etc/init/avdlite-hardware.rc'
    init.parent.mkdir(parents=True, exist_ok=True)
    init.write_text('\n'.join(lines))
    return changes


def hardware_policy(trees):
    system = trees['system']
    prefix = system / 'system' if (system / 'system/etc').exists() else system
    directory = prefix / 'etc/selinux'
    policy = directory / 'plat_sepolicy.cil'
    text = policy.read_text(encoding='utf-8')
    rule = '(allow init vendor_configs_file (file (mounton)))'
    # init 需在框架扫描前覆盖 vendor 和 NFC APEX 的功能声明，保持 Enforcing。
    if rule not in text:
        policy.write_text(text.rstrip() + '\n' + rule + '\n', encoding='utf-8')
    digest = hashlib.sha256(policy.read_bytes())
    for mapping in sorted((directory / 'mapping').glob('*.cil')):
        digest.update(mapping.read_bytes())
    # 更新平台策略身份，使 init 按修改后的 CIL 重新编译策略。
    (directory / 'plat_sepolicy_and_mapping.sha256').write_text(digest.hexdigest() + '\n', encoding='utf-8')
    return rule


def labels(trees):
    # 新增文件显式补标签；既有文件的 owner/xattr 随原文件系统副本保留。
    for partition in ('product',):
        root = trees[partition]
        for relative in ('overlay/AvdLiteFramework', 'overlay/AvdLiteSystemui', 'etc/avdlite', 'etc/init/avdlite-hardware.rc',
                         'etc/init/avdlite-ksu.rc', 'app/KernelSU'):
            path = root / relative
            if not path.exists():
                continue
            for item in [path, *path.rglob('*')] if path.is_dir() else [path]:
                os.chown(item, 0, 0)
                os.setxattr(item, 'security.selinux', b'u:object_r:system_file:s0\0')
                item.chmod(0o755 if item.is_dir() or item.suffix in ('.sh', '.so') else 0o644)
