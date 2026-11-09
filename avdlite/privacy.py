"""Check tracked files against the allowlist and scan for credentials, keys, and local paths."""
import argparse
import re
import subprocess
from pathlib import PurePosixPath


def allowed(path):
    parts = PurePosixPath(path).parts
    if len(parts) == 1:
        return path in ('.gitignore', '.gitattributes', 'README.md', 'LICENSE', 'pyproject.toml')
    if parts[:2] == ('.github', 'workflows'):
        return len(parts) == 3 and path.endswith('.yml')
    if parts[0] in ('avdlite', 'tests'):
        return len(parts) == 2 and path.endswith('.py') or parts[:2] == ('tests', 'fixtures') and len(parts) == 3 and path.endswith('.xml')
    return len(parts) == 2 and (parts[0] == 'resources' and PurePosixPath(path).suffix in ('.json', '.xml', '.sh', '.rc')
                              or parts[0] == 'docs' and path.endswith('.md'))


def scan(staged=False):
    arguments = ['git', 'diff', '--cached', '--name-only', '--diff-filter=ACM'] if staged else ['git', 'ls-files']
    paths = subprocess.check_output(arguments, text=True).splitlines()
    for path in paths:
        if not allowed(path):
            raise ValueError(f'File is outside the commit allowlist: {path}')
        data = subprocess.check_output(['git', 'show', ':' + path]) if staged else open(path, 'rb').read()
        text = data.decode('utf-8')
        normalized = text.replace(chr(92), '/')
        patterns = (r'[A-Za-z]:/(?:Users|Development|Downloads)/', r'/home/[a-zA-Z0-9_-]+/',
                    r'gh[pousr]_[A-Za-z0-9]{20,}', r'github_pat_[A-Za-z0-9_]{20,}',
                    r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')
        if any(re.search(pattern, normalized) for pattern in patterns):
            raise ValueError(f'Local path or sensitive credential detected: {path}')
    print(f'Privacy checks passed for {len(paths)} allowlisted files.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--staged', action='store_true')
    scan(parser.parse_args().staged)
