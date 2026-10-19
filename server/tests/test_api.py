"""只使用临时数据库和替身采集/模型，不调用付费服务。"""
import copy
import json
import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from server.ai import AIConfig, AIError, validate_analysis
from server.chat_validation import validate_chat_answer
from server.app import create_app, retrieve
from server.domain import TZ, classify, decorate, normalize
from server.store import Store


def raw(**changes):
    return {'id': 'a', 'source_id': 'info', 'source': '清华 info', 'source_record_id': 'external-a', 'source_url': 'https://info.tsinghua.edu.cn/item?a=1', 'title': '本科生海外暑校报名通知', 'content': '面向本科生开放申请。\n报名截止：2026年10月20日17:00。\n填写申请表并提交报名材料。', 'published_at': '2026-10-17T10:00:00+08:00', 'fetched_at': '2026-10-18T10:00:00+08:00', **changes}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'test.sqlite3')


@pytest.fixture
def client(tmp_path):
    seed = tmp_path / 'seed.json'
    seed.write_text(json.dumps({'articles': [raw()], 'sources': {}}))
    app = create_app(tmp_path / 'api.sqlite3', seed)
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        yield client


@pytest.mark.parametrize('title,category', [
    ('选课考试安排', 'academics'), ('学术报告通知', 'research'),
    ('奖学金申请', 'awards'), ('海外暑校报名', 'international'),
    ('社会实践志愿招募', 'practice'), ('企业实习招聘', 'career'),
    ('校园体育活动', 'campus'), ('相关情况说明', 'other'),
])
def test_categories(title, category):
    assert classify(title, '', '') == category


def test_source_external_and_url_deduplication(store):
    assert store.upsert(raw())
    assert not store.upsert(raw(id='new', source_url='https://info.tsinghua.edu.cn/other'))
    assert len(store.all()) == 1
    assert store.get('a')['source_url'].endswith('/other')
    assert not store.upsert(raw(id='third', source_record_id=None, source_url='https://info.tsinghua.edu.cn/other?utm_source=test'))
    assert len(store.all()) == 1


def test_external_id_is_scoped_to_actual_source(store):
    store.upsert(raw(id='ee', source_id='ee', source_record_id='123', source_url='https://www.ee.tsinghua.edu.cn/one'))
    store.upsert(raw(id='sports', source_id='sports_site', source_record_id='123', source_url='https://www.thsports.tsinghua.edu.cn/one'))
    assert len(store.all()) == 2


def test_dual_unique_matches_merge_without_integrity_error(store):
    store.upsert(raw(id='a', source_record_id='one', source_url='https://info.tsinghua.edu.cn/one'))
    store.upsert(raw(id='b', source_record_id='two', source_url='https://info.tsinghua.edu.cn/two'))
    store.upsert(raw(id='c', source_record_id='one', source_url='https://info.tsinghua.edu.cn/two'))
    assert len(store.all()) == 1
    assert store.get('a')['source_url'].endswith('/two')


def test_metadata_preview_update_keeps_full_text_events(store):
    store.upsert(raw())
    store.upsert(raw(content='', content_preview='新列表摘要', fetched_at='2026-10-18T12:00:00+08:00'))
    assert store.get('a')['content'].startswith('面向本科生')
    assert store.get('a')['events'][0]['start_at'] == '2026-10-20T17:00:00+08:00'


def test_metadata_update_keeps_full_content_and_dates(store):
    store.upsert(raw())
    store.upsert(raw(content='', published_at=None, fetched_at='2026-10-18T12:00:00+08:00'))
    item = store.get('a')
    assert item['content']
    assert item['published_at'] == '2026-10-17T10:00:00+08:00'
    assert item['events'][0]['start_at'] == '2026-10-20T17:00:00+08:00'


def test_source_failure_preserves_data_and_success(store):
    store.upsert(raw())
    store.set_status('info', {'name': '清华 info', 'status': 'success', 'last_attempt_at': '2026-10-17T10:00:00+08:00'})
    store.set_status('info', {'name': '清华 info', 'status': 'failed', 'errors': [{'message': '接口不可用'}]})
    assert len(store.all()) == 1
    status = store.source_status()
    assert status['partial_failure']
    assert status['sources'][0]['last_success_at'] == '2026-10-17T10:00:00+08:00'
    assert status['sources'][0]['error_message'] == '接口不可用'


def test_unknown_dates_not_invented():
    item = normalize(raw(content='本科生报名，截止时间另行通知。活动在10月22日举行。', published_at=None))
    assert item['published_at'] is None
    assert item['events'] == []
    assert decorate(item)['status'] == 'unknown'


def test_past_application_deadline_not_reopened_by_materials():
    item = normalize(raw(content='报名截止：2026年10月17日。\n材料提交截止：2026年10月20日。'))
    result = decorate(item, datetime(2026, 10, 18, 12, tzinfo=TZ))
    assert result['registration'] == 'closed'
    assert result['status'] == 'closed'


def test_statistics_date_is_not_a_deadline():
    assert normalize(raw(content='截至2026年10月18日，已举办活动20场。'))['events'] == []


def test_unknown_filter_excludes_pure_announcement(store):
    store.upsert(raw(title='公告情况说明', content='通知内容。'))
    assert store.query(status='unknown')['total'] == 0


def test_ongoing_event_has_independent_registration(store):
    store.upsert(raw())
    item = store.get('a')
    item['events'] = [{'id': 'event-one', 'event_type': 'event', 'title': '暑校说明会', 'start_at': '2026-10-18T10:00:00+08:00', 'end_at': '2026-10-18T17:00:00+08:00', 'precision': 'datetime', 'source_excerpt': '活动时间'}]
    result = decorate(item, datetime(2026, 10, 18, 12, tzinfo=TZ))
    assert result['status'] == 'ongoing'
    assert result['registration'] == 'unknown'


@pytest.mark.parametrize('content,status,precision', [
    ('报名截止：2026年10月17日。', 'closed', 'date'),
    ('报名截止：2026年10月18日。', 'unknown', 'date'),
    ('报名截止：2026年10月19日。', 'active', 'date'),
    ('报名截止：2026年10月18日09:00。', 'closed', 'datetime'),
    ('报名截止：2026年10月18日17:00。', 'active', 'datetime'),
])
def test_deadline_boundaries(content, status, precision):
    item = decorate(normalize(raw(content=content)), datetime(2026, 10, 18, 12, tzinfo=TZ))
    assert item['status'] == status
    assert item['events'][0]['precision'] == precision
    if precision == 'date':
        assert len(item['deadline_at']) == 10


def test_recency_excludes_unknown_and_future(store):
    for index, published in enumerate((None, '2026-10-19', '2026-10-18T13:00:00+08:00', '2026-10-18', '2026-10-12', '2026-10-11')):
        store.upsert(raw(id=str(index), source_record_id=str(index), source_url=f'https://info.tsinghua.edu.cn/item/{index}', published_at=published))
    result = store.query(days=7, at=datetime(2026, 10, 18, 12, tzinfo=TZ))
    assert {x['id'] for x in result['items']} == {'3', '4'}


def test_sports_site_is_separate_source():
    item = normalize(raw(source_id='sports', source='清华体育', source_url='https://www.thsports.tsinghua.edu.cn/info/1/2.htm'))
    assert item['source_id'] == 'sports_site'


def test_plain_text_sanitization():
    item = normalize(raw(content='<p>报名通知</p><script>alert(1)</script><iframe>危险内容</iframe>'))
    assert item['content'] == '报名通知'
    assert item['summary_kind'] == 'excerpt'
    assert item['category_method'] == 'rule'


def test_audience_requires_explicit_eligibility():
    assert normalize(raw(title='优质通识课程申报通知', content='面向任课教师申报。课程服务本科生。'))['audience'] == []
    assert normalize(raw(title='海外暑校申请', content='项目主要面向本科生开放申请。'))['audience'] == ['本科生']


def test_api_pagination_filters_and_detail(client):
    result = client.get('/api/items?category=international&page_size=1').json()
    assert set(result) == {'items', 'total', 'page', 'page_size'}
    assert result['total'] == 1
    assert result['items'][0]['source'] == 'info'
    assert client.get('/api/items?exclude_sources=info').json()['total'] == 0
    assert client.get('/api/items?ids=missing').json()['total'] == 0
    assert client.get('/api/items?ids=a').json()['total'] == 1
    assert client.get('/api/items?category=not-a-category').status_code == 422
    assert client.get('/api/items?page_size=101').status_code == 422
    assert client.get('/api/items/a').status_code == 200
    assert client.get('/api/items/missing').status_code == 404
    assert client.get('/api/opportunities').json()['total'] == 1


def test_events_return_linked_item_and_validate_range(client):
    result = client.get('/api/events?start=2026-10-20&end=2026-10-20&event_type=deadline').json()
    assert result['total'] == 1
    assert result['items'][0]['item']['id'] == 'a'
    assert client.get('/api/events?start=2026-10-21').json()['total'] == 0
    assert client.get('/api/events?start=2026-10-21&end=2026-10-20').status_code == 422


def test_unconfigured_model_is_clear_and_chat_stays_traceable(client):
    assert client.get('/api/health').json()['ai_configured'] is False
    assert client.post('/api/items/a/analyze').status_code == 503
    result = client.post('/api/chat', json={'message': '海外暑校有哪些'}).json()
    assert result['mode'] == 'retrieval'
    assert result['references'][0]['id'] == 'a'
    empty = client.post('/api/chat', json={'message': '不存在的量子橡皮鸭'}).json()
    assert empty['no_results'] and empty['references'] == []
    assert '未找到' in empty['answer']
    assert client.post('/api/chat', json={'message': '海外暑校', 'exclude_sources': ['info']}).json()['no_results']


@pytest.mark.parametrize('failed', [True, False])
def test_configured_chat_reports_fallback_or_validated_ai(tmp_path, failed):
    class FakeModel:
        config = AIConfig('https://provider.example/v1', 'test-only', 'test-model')
        calls = 0

        def complete(self, system, payload, **options):
            self.calls += 1
            if failed:
                raise AIError('模型服务返回 HTTP 404，请检查模型配置。')
            return {'statements': [{'text': '面向本科生开放申请。', 'evidence_ids': ['s1p2']}]}

    model = FakeModel()
    app = create_app(tmp_path / 'chat.sqlite3', tmp_path / 'missing.json', model)
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        app.state.store.upsert(raw())
        response = client.post('/api/chat', json={'message': '海外暑校有哪些'})
        result = response.json()
        assert response.status_code == 200 and model.calls == 1
        assert result['model_configured'] is True
        assert result['references'][0]['id'] == 'a'
        if failed:
            assert result['mode'] == 'retrieval'
            assert 'HTTP 404' in result['model_error']
            assert '原文检索摘录' in result['answer']
        else:
            assert result['mode'] == 'ai' and 'model_error' not in result
            assert result['answer'] == '- 面向本科生开放申请[1]。'
        empty = client.post('/api/chat', json={'message': '不存在的量子橡皮鸭'}).json()
        assert empty['no_results'] and empty['mode'] == 'retrieval'
        assert model.calls == 1


def good_analysis():
    return {'category': 'international', 'summary': '本科生可申请海外暑校。', 'summary_evidence': ['面向本科生开放申请。'], 'audience': ['本科生'], 'is_opportunity': True, 'action_items': [{'text': '填写申请表', 'source_excerpt': '填写申请表并提交报名材料。'}], 'materials': [], 'events': [{'event_type': 'deadline', 'title': '报名截止', 'start_at': '2026-10-20T17:00:00+08:00', 'end_at': None, 'precision': 'datetime', 'location': None, 'source_excerpt': '报名截止：2026年10月20日17:00。'}]}


def test_ai_validation_requires_exact_evidence():
    item = normalize(raw())
    result = validate_analysis(good_analysis(), item)
    assert result['summary_kind'] == 'ai'
    assert result['events'][0]['source_excerpt'] in item['content']
    for mutate in (
        lambda x: x.update(category='机会'),
        lambda x: x.update(audience=['博士后']),
        lambda x: x.update(is_opportunity='true'),
        lambda x: x.update(summary_evidence=['捏造的证据']),
        lambda x: x.update(summary='2028年10月20日截止申请。'),
        lambda x: x.update(summary='提供100万元资助。'),
        lambda x: x['events'][0].update(start_at='2026-10-22T17:00:00+08:00'),
        lambda x: x['action_items'][0].update(text='提交身份证'),
    ):
        payload = good_analysis()
        mutate(payload)
        with pytest.raises(AIError):
            validate_analysis(payload, item)


def test_successful_analysis_cached_and_failure_preserves_item(tmp_path):
    class FakeModel:
        config = AIConfig('https://example.invalid/v1', 'test-only', 'test')
        calls = 0
        def complete(self, system, payload):
            self.calls += 1
            return good_analysis()
    model = FakeModel()
    app = create_app(tmp_path / 'db.sqlite3', tmp_path / 'none.json', model)
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        app.state.store.upsert(raw())
        assert client.post('/api/items/a/analyze').json()['cached'] is False
        assert client.post('/api/items/a/analyze').json()['cached'] is True
        assert model.calls == 1
        app.state.store.upsert(raw(fetched_at='2026-10-18T16:00:00+08:00'))
        assert client.get('/api/items/a').json()['summary_kind'] == 'ai'
        app.state.store.upsert(raw(content='正文更新，需要重新分析。', fetched_at='2026-10-18T17:00:00+08:00'))
        assert client.post('/api/items/a/analyze').status_code == 502
        item = client.get('/api/items/a').json()
        assert item['processing_status'] == 'failed'
        assert item['content'] == '正文更新，需要重新分析。'


def test_sync_failure_does_not_clear_cache(client, monkeypatch):
    def failing(self, source_id, config, limit):
        raise RuntimeError('内部异常不回传')
    monkeypatch.setattr('server.app.Collector.run', failing)
    response = client.post('/api/sync', json={'sources': ['info'], 'limit': 1})
    assert response.status_code == 202
    job = response.json()['job_id']
    for _ in range(100):
        state = client.get('/api/sync/' + job).json()
        if state['status'] != 'running':
            break
        time.sleep(.01)
    assert state['status'] == 'failed'
    assert client.get('/api/items').json()['total'] == 1
    assert client.get('/api/sources/status').json()['partial_failure']
    assert client.post('/api/sync', json={'limit': 101}).status_code == 422


def test_cross_origin_write_rejected(client):
    response = client.post('/api/sync', json={}, headers={'Origin': 'https://unexpected.example'})
    assert response.status_code == 403


def test_chat_keeps_topic_and_uses_event_dates(store):
    today = datetime.now(TZ).date()
    next_monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
    for item_id, title, event_day in [('today', '科研学术讲座', today), ('next', '科研学术讲座', next_monday), ('old', '科研学术讲座', today - timedelta(days=20)), ('signup', '科研项目报名通知', today)]:
        store.upsert(raw(id=item_id, source_record_id=item_id, source_url=f'https://info.tsinghua.edu.cn/{item_id}', title=title, content=f'活动时间：{event_day.isoformat()}。', published_at=today.isoformat()))
    assert {x['id'] for x in retrieve(store, '今天有哪些讲座', [])[0]} == {'today'}
    assert {x['id'] for x in retrieve(store, '下周有哪些讲座', [])[0]} == {'next'}
    assert {x['id'] for x in retrieve(store, '科研讲座有哪些', [])[0]} == {'today', 'next', 'old'}


@pytest.mark.parametrize('answer', ['报名截止为2099年12月31日。', '面向本科生[99]。'])
def test_chat_invalid_dates_or_citation_fall_back(tmp_path, answer):
    class FakeModel:
        config = AIConfig('https://example.invalid/v1', 'test-only', 'test')
        def complete(self, system, payload, **options):
            return {'statements': [{'text': answer, 'evidence_ids': ['s1p2']}]}
    app = create_app(tmp_path / 'db.sqlite3', tmp_path / 'none.json', FakeModel())
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        app.state.store.upsert(raw())
        result = client.post('/api/chat', json={'message': '海外暑校'}).json()
        assert result['mode'] == 'retrieval'
        assert result['model_error']
        assert '2099' not in result['answer']


@pytest.mark.parametrize('answer,expected_mode', [
    ('报名截止：2026年10月20日17:00[1]。', 'ai'),
    ('报名截止：2026年10月20日17:00。[1]', 'ai'),
    ('报名截止：2026年10月20日17:00。 [1]\n面向本科生开放申请。[1]', 'ai'),
    ('报名截止：2026年10月20日17:00！[1]', 'ai'),
    ('报名截止：2026年10月20日17:00。', 'retrieval'),
    ('面向本科生开放申请。[1]报名截止：2026年10月20日17:00。', 'retrieval'),
    ('报名截止：2026年10月20日17:00。面向本科生开放申请。[1]', 'retrieval'),
    ('报名截止：2099年12月31日17:00。[1]', 'retrieval'),
])
def test_chat_sentence_end_citations_keep_numeric_evidence_checks(answer, expected_mode):
    # 引用排版仍由底层校验器回归；生产接口只接收逐条绑定证据的结构。
    references = [{'number': 1, 'id': 'a', 'excerpt': raw()['content']}]
    value = {'answer': answer, 'citations': [{'id': 'a', 'source_excerpt': raw()['content']}]}
    if expected_mode == 'ai':
        assert validate_chat_answer(value, references)[0] == answer
    else:
        with pytest.raises(AIError):
            validate_chat_answer(value, references)
