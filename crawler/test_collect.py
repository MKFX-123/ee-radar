"""采集器离线回归：使用临时数据库与模拟公开响应，不发起联网请求。"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from collect import Collector, FetchError, Store, date_value, parse_article, record


class FakeResponse:
    def __init__(self, payload=None, text='', url='https://info.tsinghua.edu.cn/search'):
        self.payload, self.text, self.url = payload, text, url

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []
        self.session = SimpleNamespace(cookies=[SimpleNamespace(name='XSRF-TOKEN', value='测试值', domain='info.tsinghua.edu.cn')])

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def info_response(rows):
    return FakeResponse({'result': 'success', 'object': {'resultsList': rows}})


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name) / 'articles.sqlite3')

    def tearDown(self):
        self.store.db.close()
        self.temporary.cleanup()

    def stored(self):
        return [json.loads(row[0]) for row in self.store.db.execute('SELECT payload FROM articles')]

    def test_iso_dates_and_date_precision(self):
        self.assertEqual(date_value('2026-10-18T05:30:12Z', strict=True), '2026-10-18T13:30:12+08:00')
        self.assertEqual(date_value('2026-10-18T13:30:12+08:00'), '2026-10-18T13:30:12+08:00')
        self.assertEqual(date_value('2026年10月18日'), '2026-10-18')
        self.assertEqual(date_value('2026-10-18 13:30'), '2026-10-18T13:30:00+08:00')
        self.assertIsNone(date_value('2026-02-30'))
        self.assertIsNone(date_value('2026-10-18T30:00:00+08:00'))
        self.assertIsNone(date_value('未说明'))

    def test_external_id_and_url_deduplicate_without_losing_body(self):
        first = record('https://info.tsinghua.edu.cn/detail?id=one', 'info', '清华 info', '标题', '已经取得的完整正文', external_id='one')
        self.assertTrue(self.store.upsert(first))
        self.assertFalse(self.store.upsert(record('https://info.tsinghua.edu.cn/new-detail?id=one', 'info', '清华 info', '标题更新', external_id='one', content_preview='列表摘录')))
        self.assertFalse(self.store.upsert(record('https://info.tsinghua.edu.cn/new-detail?id=one&utm_source=demo', 'info', '清华 info', '标题更新', external_id='alias')))
        self.assertFalse(self.store.upsert(record('https://info.tsinghua.edu.cn/detail?id=one', 'info', '清华 info', '原始链接再次发现')))
        rows = self.stored()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], first['id'])
        self.assertEqual(rows[0]['content'], '已经取得的完整正文')
        self.assertEqual(rows[0]['content_status'], 'web_text')
        self.assertEqual(rows[0]['content_preview'], '列表摘录')

    def test_external_id_and_url_collision_merge_existing_rows(self):
        first = record('https://info.tsinghua.edu.cn/one', 'info', '清华 info', '标题', '完整正文', external_id='one')
        other = record('https://info.tsinghua.edu.cn/two', 'info', '清华 info', '标题', external_id='two')
        self.store.upsert(first)
        self.store.upsert(other)
        self.assertFalse(self.store.upsert(record('https://info.tsinghua.edu.cn/two', 'info', '清华 info', '更新标题', external_id='one')))
        self.assertEqual(len(self.stored()), 1)
        self.assertEqual(self.stored()[0]['content'], '完整正文')

    def test_failure_preserves_success_timestamp_and_cached_body(self):
        self.store.upsert(record('https://www.ee.tsinghua.edu.cn/info/1/1.htm', 'ee', '电子系', '已有资讯', '保留正文'))
        with patch('collect.now', return_value='2026-10-18T12:00:00+08:00'):
            self.store.set_status('ee', {'status': 'success', 'errors': []})
        client = FakeClient([FetchError('来源拒绝访问或需要登录', 'auth_required')])
        with patch('collect.now', return_value='2026-10-18T13:00:00+08:00'):
            result = Collector(client, self.store).run('ee', {'name': '电子系', 'kind': 'website', 'url': 'https://www.ee.tsinghua.edu.cn/', 'selector': 'a'}, 2)
        self.assertEqual(result['status'], 'auth_required')
        self.assertEqual(result['last_success_at'], '2026-10-18T12:00:00+08:00')
        self.assertEqual(result['last_attempt_at'], '2026-10-18T13:00:00+08:00')
        self.assertIn('需要登录', result['error_message'])
        self.assertEqual(self.stored()[0]['content'], '保留正文')
        self.assertEqual(len(client.calls), 1)

    def test_info_configured_two_pages_and_excerpt_is_not_body(self):
        row = {'xxid': 'one', 'bt': '测试通知', 'nr': '<p>列表摘录</p>', 'time': '2026-10-18T13:00:00+08:00', 'url': '/detail?id=one'}
        client = FakeClient([FakeResponse(), info_response([row]), info_response([row])])
        result = Collector(client, self.store).run('info', {'name': '清华 info', 'kind': 'info', 'url': 'https://info.tsinghua.edu.cn/search', 'pages': 2, 'fetch_details': False}, 2)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['pages_fetched'], 2)
        self.assertEqual(result['fetched'], 1)
        self.assertEqual(self.stored()[0]['content'], '')
        self.assertEqual(self.stored()[0]['content_preview'], '列表摘录')
        self.assertEqual([json.loads(c[2]['data']['esParamClass'])['currentPage'] for c in client.calls[1:]], [1, 2])

    def test_second_page_failure_keeps_first_page(self):
        row = {'xxid': 'one', 'bt': '测试通知', 'nr': '摘录', 'time': '2026-10-18', 'url': '/detail?id=one'}
        client = FakeClient([FakeResponse(), info_response([row]), FetchError('来源限制请求频率', 'rate_limited')])
        result = Collector(client, self.store).run('info', {'name': '清华 info', 'kind': 'info', 'url': 'https://info.tsinghua.edu.cn/search', 'pages': 2}, 2)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['fetched'], 1)
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(len(self.stored()), 1)

    def test_info_backfill_uses_requested_start_page(self):
        row = {'xxid': 'old', 'bt': '九月通知', 'time': '2026-09-08', 'url': '/detail?id=old'}
        client = FakeClient([FakeResponse(), info_response([row]), info_response([])])
        result = Collector(client, self.store, since='2026-09-01').run('info', {'name': '清华 info', 'kind': 'info', 'url': 'https://info.tsinghua.edu.cn/search', 'pages': 2, 'start_page': 8, 'fetch_details': False}, 10)
        self.assertEqual(result['fetched'], 1)
        self.assertEqual([json.loads(call[2]['data']['esParamClass'])['currentPage'] for call in client.calls[1:]], [8, 9])

    def test_history_detail_budget_prioritizes_registration(self):
        rows = [{'xxid': 'news', 'bt': '校园新闻', 'time': '2026-09-18', 'url': '/detail?id=news'},
                {'xxid': 'apply', 'bt': '项目报名通知', 'time': '2026-09-17', 'url': '/detail?id=apply'}]
        detail = FakeResponse({'result': 'success', 'object': {'xxDto': {'nr_show': '<p>报名正文</p>'}}})
        client = FakeClient([FakeResponse(), info_response(rows), detail])
        result = Collector(client, self.store, since='2026-09-01').run('info', {'name': '清华 info', 'kind': 'info', 'url': 'https://info.tsinghua.edu.cn/search', 'pages': 1, 'detail_limit': 1, 'prioritize_action_details': True}, 10)
        self.assertEqual(result['fetched'], 2)
        articles = {item['external_id']: item for item in self.stored()}
        self.assertEqual(articles['news']['content'], '')
        self.assertEqual(articles['apply']['content'], '报名正文')
        self.assertEqual(client.calls[-1][2]['params']['xxid'], 'apply')

    def test_website_follows_next_page_and_stops_at_date_boundary(self):
        first = '<ul><li><span class="date">2026-09 18</span><a href="/info/1/1.htm">九月新通知</a></li></ul><a href="news/2.htm">下页</a>'
        second = '<ul><li><span class="date">2026-08 31</span><a href="/info/1/2.htm">范围外通知</a></li></ul><a href="1.htm">下页</a>'
        client = FakeClient([FakeResponse(text=first, url='https://www.ee.tsinghua.edu.cn/news.htm'), FakeResponse(text=second, url='https://www.ee.tsinghua.edu.cn/news/2.htm')])
        links = Collector(client, self.store, since='2026-09-01').website({'name': '电子系', 'kind': 'website', 'url': 'https://www.ee.tsinghua.edu.cn/news.htm', 'selector': 'a[href]', 'listing_pages': 6}, 100)
        self.assertEqual(links, [('https://www.ee.tsinghua.edu.cn/info/1/1.htm', '九月新通知', '2026-09-18')])
        self.assertEqual(len(client.calls), 2)

    def test_public_detail_is_separate_from_list_excerpt(self):
        row = {'xxid': 'one', 'bt': '测试通知', 'nr': '200字列表摘录', 'time': '2026-10-18', 'url': '/detail?id=one'}
        detail = FakeResponse({'result': 'success', 'object': {'xxDto': {'nr_show': '<p>公开正文第一段</p><p>第二段</p>', 'fbsj': '2026-10-18T05:00:00Z'}}})
        client = FakeClient([FakeResponse(), info_response([row]), detail])
        result = Collector(client, self.store).run('info', {'name': '清华 info', 'kind': 'info', 'url': 'https://info.tsinghua.edu.cn/search'}, 1)
        self.assertEqual(result['status'], 'success')
        article = self.stored()[0]
        self.assertEqual(article['content'], '公开正文第一段\n第二段')
        self.assertEqual(article['content_preview'], '200字列表摘录')
        self.assertEqual(article['published_at'], '2026-10-18T13:00:00+08:00')

    def test_wechat_identity_does_not_use_website_source(self):
        raw = '<h1 id="activity-name">体育活动</h1><span id="js_name">清华体育</span><div id="js_content">公开正文</div>'
        article = parse_article(raw, 'https://mp.weixin.qq.com/s/example', 'sports_site', '清华大学体育部')
        self.assertEqual(article['source_id'], 'sports')
        self.assertEqual(article['source'], '清华体育')
        generic = raw.replace('清华体育', '清华大学电子工程系')
        article = parse_article(generic, 'https://mp.weixin.qq.com/s/example', 'ee_wechat', '清华大学电子工程系')
        self.assertEqual(article['source_id'], 'ee_wechat')

    def test_unverified_account_is_rejected(self):
        client = FakeClient([FakeResponse(text='<h1 id="activity-name">标题</h1><span id="js_name">其他账号</span><div id="js_content">正文</div>', url='https://mp.weixin.qq.com/s/example')])
        result = Collector(client, self.store).run('sports', {'name': '清华体育公众号', 'kind': 'wechat', 'urls': ['https://mp.weixin.qq.com/s/example'], 'expected_account': '清华体育'}, 1)
        self.assertEqual(result['status'], 'source_mismatch')
        self.assertEqual(self.stored(), [])

    def test_detail_failure_keeps_list_and_stops_auth_requests(self):
        rows = [{'xxid': str(i), 'bt': '测试通知', 'nr': '摘录', 'time': '2026-10-18', 'url': '/detail?id=' + str(i)} for i in range(2)]
        client = FakeClient([FakeResponse(), info_response(rows), FetchError('公开详情需要登录', 'auth_required')])
        result = Collector(client, self.store).run('info', {'name': '清华 info', 'kind': 'info', 'url': 'https://info.tsinghua.edu.cn/search'}, 2)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['fetched'], 2)
        self.assertTrue(all(not a['content'] for a in self.stored()))
        self.assertEqual(len(client.calls), 3)


if __name__ == '__main__':
    unittest.main()
