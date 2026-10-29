#!/usr/bin/env python3
"""建立或核验离线文件清单；不联网、不删除文件。Python 3.10+。"""
import argparse
import hashlib
import json
import sys
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def contained(root, name):
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('清单路径必须是根目录内的相对路径')
    path = root / relative
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError('不接受符号链接：' + name)
    if not path.resolve().is_relative_to(root):
        raise ValueError('路径越出备份目录：' + name)
    return path


def inventory(root, manifest):
    entries = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('备份包含符号链接：' + path.relative_to(root).as_posix())
        if not path.is_file() or path.resolve() == manifest:
            continue
        # 排除 macOS 文件系统元数据，不排除用户正文或素材文件。
        if path.name == '.DS_Store' or path.name.startswith('._'):
            continue
        stat_before = path.stat()
        hash_value = digest(path)
        stat_after = path.stat()
        if (stat_before.st_size, stat_before.st_mtime_ns) != (stat_after.st_size, stat_after.st_mtime_ns):
            raise ValueError('校验期间文件发生变化')
        entries[path.relative_to(root).as_posix()] = {'size': stat_after.st_size, 'sha256': hash_value}
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['create', 'verify'])
    parser.add_argument('root', type=Path, help='备份目录')
    parser.add_argument('manifest', type=Path, help='清单 JSON，create 不覆盖已有文件')
    args = parser.parse_args()
    if args.root.is_symlink():
        raise ValueError('根目录不能是符号链接')
    root = args.root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError('根路径不是目录')
    if args.manifest.is_symlink():
        raise ValueError('清单不能是符号链接')
    manifest = args.manifest.resolve()
    if args.command == 'create' and manifest.exists():
        raise ValueError('清单已存在；请使用新的文件名，避免覆盖旧证据')
    files = inventory(root, manifest)
    if args.command == 'create':
        if not files:
            raise ValueError('备份目录没有用户文件，不能建立空备份证明')
        with manifest.open('x', encoding='utf-8') as stream:
            json.dump({'format': 1, 'files': files}, stream, ensure_ascii=False, indent=2)
        print(json.dumps({'created': True, 'file_count': len(files)}, ensure_ascii=False))
        return 0
    expected = json.loads(manifest.read_text(encoding='utf-8'))
    if expected.get('format') != 1 or not isinstance(expected.get('files'), dict) or not expected['files']:
        raise ValueError('无效或空清单')
    expected = expected['files']
    for name, entry in expected.items():
        contained(root, name)
        if not isinstance(entry, dict) or set(entry) != {'size', 'sha256'}:
            raise ValueError('无效文件记录')
    missing = sorted(set(expected) - set(files))
    added = sorted(set(files) - set(expected))
    changed = sorted(name for name in set(files) & set(expected) if files[name] != expected[name])
    passed = not (missing or added or changed)
    print(json.dumps({'passed': passed, 'file_count': len(files), 'missing': missing,
                      'added': added, 'changed': changed}, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(json.dumps({'passed': False, 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)
