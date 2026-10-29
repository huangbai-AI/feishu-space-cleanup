"""全部为合成数据；不访问飞书，也不使用真实文档。"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write_doc(self, name, body, refs=None, tips=''):
        path = self.root / name
        path.write_text(json.dumps({'ok': True, 'data': {'document': {
            'document_id': name, 'revision_id': 1,
            'content': '<title>示例</title>' + body,
            'reference_map': refs or {}, 'tips': tips}}}), encoding='utf-8')
        return path

    def run_tool(self, script, *args):
        result = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)],
                                capture_output=True, text=True)
        return result.returncode, json.loads(result.stdout or result.stderr)

    def compare(self, a, b, *args):
        return self.run_tool('verify_copy.py', a, b, *args)

    def test_block_identifiers_change_but_text_survives(self):
        a = self.write_doc('a.json', '<p id="old">完整内容</p>')
        b = self.write_doc('b.json', '<p id="new">完整内容</p>')
        self.assertEqual(self.compare(a, b)[0], 0)

    def test_missing_text_and_format_change_fail(self):
        a = self.write_doc('a.json', '<p><b>内容</b>补充</p>')
        for body in ['<p><b>内容</b></p>', '<p>内容补充</p>']:
            b = self.write_doc('b.json', body)
            self.assertEqual(self.compare(a, b)[0], 1)

    def test_binary_content_not_tokens_controls_media_match(self):
        (self.root / 'one.bin').write_bytes(b'original media')
        (self.root / 'two.bin').write_bytes(b'original media')
        media_map = self.root / 'media.json'
        media_map.write_text(json.dumps({'first': {'path': 'one.bin'}, 'second': {'path': 'two.bin'}}))
        a = self.write_doc('a.json', '<img src="first" name="photo.png"/>')
        b = self.write_doc('b.json', '<img src="second" name="photo.png"/>')
        args = ['--media-root', self.root, '--media-map', media_map]
        self.assertEqual(self.compare(a, b, *args)[0], 0)
        (self.root / 'two.bin').write_bytes(b'changed media!')
        self.assertEqual(self.compare(a, b, *args)[0], 1)

    def test_media_without_files_and_blank_files_block(self):
        a = self.write_doc('a.json', '<source token="media" name="movie.mp4"/>')
        self.assertEqual(self.compare(a, a)[0], 1)
        (self.root / 'empty.bin').touch()
        m = self.root / 'media.json'
        m.write_text(json.dumps({'media': {'path': 'empty.bin'}}))
        self.assertEqual(self.compare(a, a, '--media-root', self.root, '--media-map', m)[0], 2)

    def test_unknown_components_and_comments_need_review(self):
        for body, refs in [('<sheet token="table"/>', {}), ('<p>正文</p>', {'comments': {'c1': {}}}),
                           ('<future-component/>', {}), ('<img ref="r1"/>', {})]:
            a = self.write_doc('a.json', body, refs)
            self.assertEqual(self.compare(a, a)[0], 1)

    def test_fragment_and_failed_response_never_pass(self):
        a = self.write_doc('a.json', '<fragment><p>节选</p></fragment>')
        self.assertEqual(self.compare(a, a)[0], 2)
        a.write_text('{"ok":false,"error":{"message":"not available"}}')
        self.assertEqual(self.compare(a, a)[0], 2)

    def test_title_change_needs_explicit_option(self):
        a = self.write_doc('a.json', '<p>正文</p>')
        b = self.write_doc('b.json', '<p>正文</p>')
        payload = json.loads(b.read_text())
        payload['data']['document']['content'] = payload['data']['document']['content'].replace('<title>示例</title>', '<title>新标题</title>')
        b.write_text(json.dumps(payload))
        self.assertEqual(self.compare(a, b)[0], 1)
        self.assertEqual(self.compare(a, b, '--allow-title-change')[0], 0)

    def test_manifest_detects_modification_missing_and_new_files(self):
        folder = self.root / 'backup'
        folder.mkdir()
        asset = folder / 'asset.bin'
        asset.write_bytes(b'complete media')
        manifest = self.root / 'manifest.json'
        self.assertEqual(self.run_tool('backup_manifest.py', 'create', folder, manifest)[0], 0)
        self.assertEqual(self.run_tool('backup_manifest.py', 'verify', folder, manifest)[0], 0)
        asset.write_bytes(b'corrupted')
        self.assertIn('asset.bin', self.run_tool('backup_manifest.py', 'verify', folder, manifest)[1]['changed'])
        asset.unlink()
        (folder / 'unexpected.txt').write_text('new data')
        result = self.run_tool('backup_manifest.py', 'verify', folder, manifest)[1]
        self.assertIn('asset.bin', result['missing'])
        self.assertIn('unexpected.txt', result['added'])

    def test_manifest_inside_backup_does_not_include_itself(self):
        (self.root / 'asset.bin').write_bytes(b'media')
        manifest = self.root / 'manifest.json'
        self.assertEqual(self.run_tool('backup_manifest.py', 'create', self.root, manifest)[0], 0)
        self.assertEqual(self.run_tool('backup_manifest.py', 'verify', self.root, manifest)[0], 0)
        self.assertEqual(self.run_tool('backup_manifest.py', 'create', self.root, manifest)[0], 2)

    def test_empty_backup_and_symlink_are_rejected(self):
        folder = self.root / 'backup'
        folder.mkdir()
        m = self.root / 'manifest.json'
        self.assertEqual(self.run_tool('backup_manifest.py', 'create', folder, m)[0], 2)
        (self.root / 'outside.bin').write_bytes(b'private')
        (folder / 'link.bin').symlink_to(self.root / 'outside.bin')
        self.assertEqual(self.run_tool('backup_manifest.py', 'create', folder, m)[0], 2)

    def test_media_cannot_escape_the_backup_directory(self):
        a = self.write_doc('a.json', '<source token="file"/>')
        m = self.root / 'map.json'
        m.write_text(json.dumps({'file': {'path': '../outside.bin'}}))
        self.assertEqual(self.compare(a, a, '--media-root', self.root, '--media-map', m)[0], 2)

    def test_same_document_is_not_an_independent_copy(self):
        a = self.write_doc('a.json', '<p>正文</p>')
        self.assertEqual(self.compare(a, a)[0], 1)

    def test_text_outside_blocks_cannot_disappear(self):
        a = self.write_doc('a.json', '<p>正文</p>额外文字')
        b = self.write_doc('b.json', '<p>正文</p>')
        self.assertEqual(self.compare(a, b)[0], 1)

    def test_link_target_changes_are_not_silently_ignored(self):
        a = self.write_doc('a.json', '<p><a href="https://example.com/old">入口</a></p>')
        b = self.write_doc('b.json', '<p><a href="https://example.com/new">入口</a></p>')
        self.assertEqual(self.compare(a, b)[0], 1)


if __name__ == '__main__':
    unittest.main()
