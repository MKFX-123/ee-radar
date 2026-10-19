"""验证十月采集边界、公众号归属和公开索引过滤，全部离线运行。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collect import Collector, FetchError, Store, parse_article, record
from policy import within_publication_window
from wechat_index import discover
from test_collect import FakeClient, FakeResponse


class PublicationPolicyTests(unittest.TestCase):
    def test_missing_account_name_never_uses_configured_name(self):
        raw = '<h1 id="activity-name">测试文章</h1><div id="js_content">正文</div>'
        with self.assertRaises(FetchError) as error:
            parse_article(raw, 'https://mp.weixin.qq.com/s/unknown', 'aishengquan', '艾生权')
        self.assertEqual(error.exception.status, 'source_unknown')

    def test_wechat_short_and_long_urls_share_identity(self):
        raw = '<h1 id="activity-name">测试文章</h1><span id="js_name">清华大学</span><div id="js_content">正文</div><script>var biz="testBiz";var mid="123";var idx="1";var ct="1790812800";</script>'
        short = parse_article(raw, 'https://mp.weixin.qq.com/s/short', 'wechat', '公众号')
        long = parse_article(raw, 'https://mp.weixin.qq.com/s?__biz=testBiz&mid=123&idx=1', 'wechat', '公众号')
        self.assertEqual(short['source_id'], 'tsinghua_wechat')
        self.assertEqual(short['external_id'], long['external_id'])
        self.assertEqual(short['external_id'], 'testBiz:123:1')

    def test_beijing_midnight_and_unknown_dates(self):
        cases = [('2026-09-30T23:59:59+08:00', False),
                 ('2026-10-01T00:00:00+08:00', True),
                 ('2026-09-30T16:00:00Z', True),
                 ('2026-10-01', True), (None, False),
                 ('2026-10-22 null', False)]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(within_publication_window({'published_at': value}, '2026-10-01'), expected)

    def test_export_filters_history_without_deleting_database(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'test.sqlite3')
            try:
                for index, day in enumerate(('2026-09-30', '2026-10-01', None)):
                    store.upsert(record('https://mp.weixin.qq.com/s/' + str(index), 'wechat', '测试账号', '测试标题', published_at=day))
                result = store.export(Path(directory) / 'out.json', since='2026-10-01')
                self.assertEqual(result['count'], 1)
                self.assertEqual(result['articles'][0]['published_at'], '2026-10-01')
                self.assertEqual(store.db.execute('SELECT count(*) FROM articles').fetchone()[0], 3)
            finally:
                store.db.close()

    def test_index_exact_account_and_date_filter(self):
        items = [
            {'source_name': '艾生权', 'published_at': '2026-10-14', 'url': 'https://mp.weixin.qq.com/s/one'},
            {'source_name': '转载账号', 'published_at': '2026-10-14', 'url': 'https://mp.weixin.qq.com/s/two'},
            {'source_name': '艾生权', 'published_at': '2026-09-30', 'url': 'https://mp.weixin.qq.com/s/three'},
            {'source_name': '艾生权', 'published_at': '2026-10-14', 'url': 'https://example.com/not-wechat'},
        ]
        client = FakeClient([FakeResponse(), FakeResponse({'items': items, 'page_size': 30, 'total': 4})])
        links = discover(client, {'public_index_source': 'test', 'expected_account': '艾生权'}, '2026-10-01', 10)
        self.assertEqual(links, [('https://mp.weixin.qq.com/s/one', '', None)])
        self.assertEqual(len(client.calls), 2)

    def test_mismatched_account_is_not_saved_as_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'test.sqlite3')
            try:
                collector = Collector(FakeClient([]), store)
                wrong = record('https://mp.weixin.qq.com/s/wrong', 'wechat', '转载账号', '标题', '正文', '2026-10-14', account_name='转载账号')
                config = {'name': '艾生权', 'kind': 'wechat', 'urls': [], 'discovery_url': 'https://www.tsinghua.edu.cn/', 'expected_account': '艾生权'}
                with patch.object(collector, 'website', return_value=[(wrong['source_url'], '索引标题', '2026-10-14')]), patch.object(collector, 'article', return_value=wrong):
                    result = collector.run('aishengquan', config, 10)
                self.assertEqual(result['status'], 'source_mismatch')
                self.assertEqual(store.db.execute('SELECT count(*) FROM articles').fetchone()[0], 0)
            finally:
                store.db.close()


if __name__ == '__main__':
    unittest.main()
