"""对 Git 管理的文件执行提交白名单、凭据、私钥和本机路径检查。"""
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
            raise ValueError(f'文件超出提交白名单：{path}')
        data = subprocess.check_output(['git', 'show', ':' + path]) if staged else open(path, 'rb').read()
        text = data.decode('utf-8')
        normalized = text.replace(chr(92), '/')
        patterns = (r'[A-Za-z]:/(?:Users|Development|Downloads)/', r'/home/[a-zA-Z0-9_-]+/',
                    r'gh[pousr]_[A-Za-z0-9]{20,}', r'github_pat_[A-Za-z0-9_]{20,}',
                    r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')
        if any(re.search(pattern, normalized) for pattern in patterns):
            raise ValueError(f'发现本机路径或敏感凭据：{path}')
    print(f'隐私检查通过：{len(paths)} 个白名单文件，无本机路径、凭据或私钥。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--staged', action='store_true')
    scan(parser.parse_args().staged)
