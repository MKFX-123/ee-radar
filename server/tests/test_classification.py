"""旧分类升级仅改分类字段，其他可溯源内容与 AI 分析继续保留。"""
import json

from server.store import Store


def item(title='相关情况说明', **changes):
    return {'id': 'category-test', 'source_id': 'info', 'source': '清华 info',
            'source_url': 'https://info.tsinghua.edu.cn/category-test',
            'title': title, 'content': '原文内容', 'published_at': '2026-10-17', **changes}


def test_legacy_category_migration_preserves_other_payload_fields(tmp_path):
    path = tmp_path / 'test.sqlite3'
    store = Store(path)
    store.upsert(item(title='青年研究奖获得者'))
    with store.connect() as db:
        payload = json.loads(db.execute('SELECT payload FROM items').fetchone()[0])
        payload.update(category='uncategorized', rule_version=2)
        db.execute('UPDATE items SET payload=?', (json.dumps(payload),))
    migrated = Store(path).get('category-test')
    assert migrated['category'] == 'research'
    for key in payload.keys() - {'category', 'category_method', 'rule_version', 'status', 'registration', 'deadline_at', 'event_start_at'}:
        assert migrated[key] == payload[key]


def test_other_is_a_queryable_category(tmp_path):
    store = Store(tmp_path / 'test.sqlite3')
    store.upsert(item())
    result = store.query(category='other')
    assert result['total'] == 1
    assert result['items'][0]['category'] == 'other'


def test_valid_ai_classification_survives_rule_upgrade(tmp_path):
    path = tmp_path / 'test.sqlite3'
    store = Store(path)
    store.upsert(item())
    store.save_analysis('category-test', {'category': 'practice', 'category_method': 'ai', 'summary': '已核验的模型摘要', 'rule_version': 2})
    migrated = Store(path).get('category-test')
    assert migrated['category'] == 'practice'
    assert migrated['summary'] == '已核验的模型摘要'
