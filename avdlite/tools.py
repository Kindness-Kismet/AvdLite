"""Download build tools and the public AVB test key matching the source image."""
import base64
import hashlib
import json
from pathlib import Path
from .sources import fetch


def prepare(work):
    config = json.loads((Path(__file__).resolve().parent.parent / 'resources/tools.json').read_text())
    tools = Path(work) / 'tools'
    tools.mkdir(exist_ok=True)
    base = f"https://android.googlesource.com/platform/external/avb/+/refs/tags/{config['avb_revision']}/"
    for filename, remote, digest in (
        ('avbtool.py', 'avbtool.py', config['avbtool_sha256']),
        ('avb-testkey.pem', 'test/data/testkey_rsa4096.pem', config['avb_testkey_sha256']),
    ):
        path = tools / filename
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            content = base64.b64decode(fetch(base + remote + '?format=TEXT'), validate=False)
            if hashlib.sha256(content).hexdigest() != digest:
                raise ValueError('AVB build tool checksum verification failed')
            path.write_bytes(content)
    return tools
