#!/usr/bin/env python3
"""保守比较两个完整 XML 快照与原始媒体；结果不授权删除。"""
import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from backup_manifest import contained, digest

# 未知标签按未覆盖处理，避免新组件被当成普通文字比较。
SUPPORTED = set('title p h1 h2 h3 h4 h5 h6 h7 h8 h9 blockquote hr b em u del br span a latex '
                'ol ul li pre code checkbox table colgroup col thead tbody tr th td grid column '
                'callout img source figure bookmark cite'.split())


def load_document(path):
    payload = json.loads(path.read_text(encoding='utf-8'))
    if payload.get('ok') is not True:
        raise ValueError('输入须为 ok:true 的完整读取响应')
    doc = payload['data']['document']
    content = doc['content']
    if '<!DOCTYPE' in content.upper() or '<!ENTITY' in content.upper():
        raise ValueError('不支持 XML 外部实体或文档类型声明')
    root = ET.fromstring('<snapshot>' + content + '</snapshot>')
    if not len(root) or root[0].tag != 'title' or any(e.tag == 'fragment' for e in root.iter()):
        raise ValueError('输入必须是含标题的完整 XML，不能使用局部片段或 Markdown')
    if sum(e.tag == 'title' for e in root.iter()) != 1:
        raise ValueError('快照必须且只能有一个标题')
    if not doc.get('document_id') or doc.get('revision_id') is None:
        raise ValueError('快照缺少文档标识或版本')
    return doc, root


def signature(element, media_map, media_root, hashes, blockers):
    attrs = {k: v for k, v in element.attrib.items() if k not in {'id', 'comment-refs'}}
    if element.tag in {'img', 'source'}:
        token_key = 'src' if element.tag == 'img' else 'token'
        token = attrs.get(token_key)
        if not token or token not in media_map or media_root is None:
            blockers.add('存在未下载或未映射的原始素材')
        else:
            info = media_map[token]
            if not isinstance(info, dict) or not isinstance(info.get('path'), str):
                raise ValueError('媒体映射必须提供相对 path')
            path = contained(media_root, info['path'])
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError('原始素材不存在或为空')
            if token not in hashes:
                before = path.stat()
                hash_value = digest(path)
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError('计算校验值期间素材发生变化')
                hashes[token] = {'size': after.st_size, 'sha256': hash_value}
            expected_size = attrs.get('size')
            if expected_size and int(expected_size) != hashes[token]['size']:
                blockers.add('素材文件大小与快照不符')
            attrs[token_key] = hashes[token]
    return [element.tag, attrs, element.text,
            [[signature(child, media_map, media_root, hashes, blockers), child.tail] for child in element]]


def check(args):
    original, left = load_document(args.original)
    copied, right = load_document(args.copy)
    blockers = set()
    if original['document_id'] == copied['document_id']:
        blockers.add('原件和副本文档标识相同，不是独立副本')
    if left.text != right.text or left[0].tail != right[0].tail:
        blockers.add('块间文字不一致')
    titles_match = signature(left[0], {}, None, {}, set()) == signature(right[0], {}, None, {}, set())
    if not titles_match and not args.allow_title_change:
        blockers.add('标题不同；只有有意改名时才显式允许')
    for doc, root in [(original, left), (copied, right)]:
        for e in root.iter():
            if e.tag != 'snapshot' and e.tag not in SUPPORTED:
                blockers.add('需要专项核验的标签：' + e.tag)
            if 'ref' in e.attrib:
                blockers.add('存在未展开的引用属性')
            if e.get('comment-refs'):
                blockers.add('存在批注引用，需要专项核验')
            if e.tag == 'cite' and e.get('type') != 'doc':
                blockers.add('存在非文档引用，需要专项核验')
        if doc.get('tips'):
            blockers.add('读取提示需要检查，不能视为完整快照')
        refs = doc.get('reference_map') or {}
        if not isinstance(refs, dict):
            raise ValueError('reference_map 结构无效')
        if any(refs.values()):
            blockers.add('存在引用侧表或批注，需要专项核验完整性')
    media_map = json.loads(args.media_map.read_text(encoding='utf-8')) if args.media_map else {}
    if not isinstance(media_map, dict):
        raise ValueError('媒体映射必须是对象')
    if args.media_root and args.media_root.is_symlink():
        raise ValueError('媒体根目录不能是符号链接')
    media_root = args.media_root.resolve(strict=True) if args.media_root else None
    hashes = {}
    a = [[signature(e, media_map, media_root, hashes, blockers), e.tail] for e in list(left)[1:]]
    b = [[signature(e, media_map, media_root, hashes, blockers), e.tail] for e in list(right)[1:]]
    if a != b:
        blockers.add('正文、样式、链接或媒体内容不一致')
    return {'passed': not blockers, 'scope': '仅本次完整快照的正文与媒体比较，不是删除许可',
            'original_revision': original['revision_id'], 'copy_revision': copied['revision_id'],
            'titles_match': titles_match, 'media_file_count': len(hashes), 'blockers': sorted(blockers)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('original', type=Path)
    parser.add_argument('copy', type=Path)
    parser.add_argument('--media-root', type=Path)
    parser.add_argument('--media-map', type=Path)
    parser.add_argument('--allow-title-change', action='store_true', help='仅用于明确知道标题有意变化的比较')
    args = parser.parse_args()
    if bool(args.media_root) != bool(args.media_map):
        parser.error('--media-root 和 --media-map 必须一起提供')
    result = check(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, TypeError, KeyError, ET.ParseError) as exc:
        print(json.dumps({'passed': False, 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)
