"""历史数据不删除，列表和详情使用同一可配置日期边界。"""
import pytest
from server.store import Store


@pytest.mark.parametrize('published,visible', [
    ('2026-09-30T23:59:59+08:00', False),
    ('2026-10-01T00:00:00+08:00', True),
    ('2026-09-30T16:00:00Z', True),
    (None, False),
])
def test_list_and_detail_use_same_publication_window(tmp_path, monkeypatch, published, visible):
    monkeypatch.setattr('crawler.policy.publication_since', lambda: '2026-10-01')
    store = Store(tmp_path / 'policy.sqlite3')
    store.upsert({'id': 'policy-test', 'source_id': 'info', 'source': '清华 info',
                  'source_url': 'https://info.tsinghua.edu.cn/test', 'title': '时间范围测试',
                  'content': '测试正文', 'published_at': published})
    assert bool(store.all()) is visible
    assert (store.get('policy-test') is not None) is visible
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM items').fetchone()[0] == 1
