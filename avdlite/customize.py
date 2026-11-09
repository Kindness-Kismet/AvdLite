"""Debloat applications by package identity and gate hardware features on emulator ports."""
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
        raise ValueError(f'APK identity detection failed: {path.name}')
    return match.group(1)


def debloat(trees, aapt):
    config = json.loads((RESOURCES / 'debloat.json').read_text())
    selected = {package: group['reason'] for group in config['groups'] for package in group['packages']}
    protected = set(config['protected'])
    if protected.intersection(selected):
        raise ValueError('Debloat list contains protected system dependencies')
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
                    # Remove directories only when every APK matches; shared directories require review.
                    if not identities.issubset(selected) or identities.intersection(protected):
                        raise ValueError('Application directory contains shared content outside the debloat list')
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
    # Remove compressed Chrome preloads together with the installed application.
    product = trees['product']
    for path in list(product.rglob('*Chrome*.apk.gz')) + list(product.rglob('*Chrome*.apk.xz')):
        path.unlink()
        removed.append({'partition': 'product', 'path': str(path.relative_to(product)), 'reason': 'Remove compressed Chrome preload'})
    essentials = ('android', 'com.android.systemui', 'com.android.settings', 'com.google.android.gms', 'com.google.android.gsf',
                  'com.google.android.apps.nexuslauncher', 'com.google.android.inputmethod.latin')
    missing = set(essentials) - packages
    gms_apex = sorted(str(p.relative_to(product)) for p in (product / 'apex').glob('com.google.android.gmssystem.*.apex'))
    # Newer images package GMS in APEX; preserve the container and verify registration at boot.
    if gms_apex:
        missing.discard('com.google.android.gms')
    if missing:
        raise ValueError('Required core applications are missing: ' + ','.join(sorted(missing)))
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
            raise ValueError('UWB service requires a recognized emulator port trigger')
        if f'start {service}' not in trigger.group(0):
            insertion = trigger.group(0).rstrip() + f'\n    start {service}\n\n'
            text = text[:trigger.start()] + insertion + text[trigger.end():]
        rc.write_text(text)
        changes.append('UWB: disabled + oneshot; start when the port property is published')
    product = trees['product']
    directory = product / 'etc/avdlite'
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'empty-features.xml').write_text('<?xml version="1.0" encoding="utf-8"?><permissions/>\n')
    lines = ['# Mount port-dependent feature declarations before the framework scans them.']
    for feature in ('uwb', 'nfc'):
        # Evaluate empty port properties during post-fs-data for this boot configuration.
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
            # Newer NFC APEX packages also declare features; init records mount results.
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
    # Allow init to cover vendor and NFC APEX feature files while SELinux stays enforcing.
    if rule not in text:
        policy.write_text(text.rstrip() + '\n' + rule + '\n', encoding='utf-8')
    digest = hashlib.sha256(policy.read_bytes())
    for mapping in sorted((directory / 'mapping').glob('*.cil')):
        digest.update(mapping.read_bytes())
    # Refresh the platform policy identity so init compiles the modified CIL.
    (directory / 'plat_sepolicy_and_mapping.sha256').write_text(digest.hexdigest() + '\n', encoding='utf-8')
    return rule


def labels(trees):
    # Label added files explicitly; filesystem copies preserve existing owners and xattrs.
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
