"""Build settings and status bar overlays signed with a temporary build key."""
import shutil
import subprocess
from pathlib import Path


def build(work, sdk, destinations):
    sdk = Path(sdk)
    versions = [p for p in (sdk / 'build-tools').iterdir() if (p / 'aapt2').exists()]
    tools = max(versions, key=lambda p: tuple(int(x) for x in p.name.split('.') if x.isdigit()))
    android = sdk / 'platforms/android-35/android.jar'
    if not android.exists():
        raise ValueError('Android 35 platform definitions are required')
    key = work / 'overlay.keystore'
    subprocess.run(['keytool', '-genkeypair', '-keystore', str(key), '-storepass', 'android', '-keypass', 'android',
                    '-alias', 'overlay', '-dname', 'CN=AvdLite Build', '-keyalg', 'RSA', '-keysize', '2048', '-validity', '3650'], check=True)
    resources = {
        'framework': ('android', '<bool name="config_lockDayNightMode">false</bool><bool name="config_lockUiMode">false</bool><bool name="config_allowAllRotations">true</bool>'),
        'systemui': ('com.android.systemui', ''.join(f'<dimen name="{name}">12dp</dimen>' for name in ('status_bar_padding_start', 'status_bar_padding_end', 'rounded_corner_content_padding', 'status_bar_padding_without_rounded_corners'))),
    }
    for name, (target, content) in resources.items():
        folder = work / 'overlays' / name
        (folder / 'res/values').mkdir(parents=True)
        (folder / 'res/values/values.xml').write_text('<resources>' + content + '</resources>\n')
        manifest = folder / 'AndroidManifest.xml'
        manifest.write_text(f'<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="local.avdlite.{name}"><uses-sdk android:minSdkVersion="35" android:targetSdkVersion="35"/><application android:hasCode="false"/><overlay android:targetPackage="{target}" android:isStatic="true" android:priority="999"/></manifest>\n')
        compiled, apk = folder / 'compiled.zip', folder / 'overlay.apk'
        subprocess.run([str(tools / 'aapt2'), 'compile', '--dir', str(folder / 'res'), '-o', str(compiled)], check=True)
        subprocess.run([str(tools / 'aapt2'), 'link', '-o', str(apk), '-I', str(android), '--manifest', str(manifest), '-R', str(compiled), '--auto-add-overlay'], check=True)
        subprocess.run([str(tools / 'apksigner'), 'sign', '--ks', str(key), '--ks-pass', 'pass:android', '--key-pass', 'pass:android', str(apk)], check=True)
        subprocess.run([str(tools / 'apksigner'), 'verify', str(apk)], check=True)
        output = destinations['product'] / 'overlay' / ('AvdLite' + name.title()) / ('AvdLite' + name.title() + '.apk')
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(apk, output)
    return tools
