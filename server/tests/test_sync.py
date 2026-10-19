"""后台增量同步集成测试：仅使用临时数据库、替身采集器和替身模型。"""
import json
import time
from threading import Event

import pytest
from fastapi.testclient import TestClient

from server.ai import AIConfig, AIError
from server.app import create_app
from server.store import Store
from server.sync import AnalysisRunner, SyncManager, SyncOptions


def article(**changes):
    return {
        'id': 'sync-a', 'source_id': 'info', 'source': '清华 info',
        'source_record_id': 'stable-a', 'source_url': 'https://info.tsinghua.edu.cn/detail?xxid=stable-a',
        'title': '科研学术讲座', 'content': '电子系举办科研学术讲座。',
        'published_at': '2026-10-18', 'fetched_at': '2026-10-18T10:00:00+08:00',
        **changes,
    }


class FakeModel:
    def __init__(self, configured=False, failing=False):
        self.config = AIConfig('https://example.invalid/v1', 'local-test-only', 'test') if configured else AIConfig('', '', '')
        self.calls, self.failing = 0, failing

    def complete(self, system, payload):
        self.calls += 1
        if self.failing:
            raise AIError('替身模型暂不可用。')
        return {
            'category': 'research', 'summary': payload['content'],
            'summary_evidence': [payload['content']], 'audience': [],
            'is_opportunity': False, 'action_items': [], 'materials': [], 'events': [],
        }


def config_file(tmp_path, sources=None):
    path = tmp_path / 'sources.json'
    path.write_text(json.dumps(sources or {'info': {'name': '清华 info', 'enabled': True}}), encoding='utf-8')
    return path


def completed(store, job_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = store.get_job(job_id)
        if job['status'] != 'running':
            return job
        time.sleep(.01)
    pytest.fail('替身同步任务未在期限内完成。')


def collecting(value):
    class FakeCollector:
        def __init__(self, store):
            self.store = store

        def run(self, source, config, limit):
            result = {'status': 'success', 'fetched': 1, 'inserted': 0, 'updated': 0, 'unchanged': 0, 'stale': 0}
            result[self.store.upsert_result(value)['change']] += 1
            self.store.set_status(source, {'name': config['name'], **result})
            return result
    return FakeCollector


def test_refresh_reuses_running_job_and_persists_after_restart(tmp_path):
    entered, release = Event(), Event()

    class BlockingCollector:
        def __init__(self, store):
            self.store = store

        def run(self, source, config, limit):
            entered.set()
            assert release.wait(2)
            return {'status': 'success', 'fetched': 0}

    db = tmp_path / 'radar.sqlite3'
    app = create_app(db, tmp_path / 'none.json', FakeModel(), sync_options=SyncOptions(enabled=False), sync_config_path=config_file(tmp_path), collector_factory=BlockingCollector)
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        first = client.post('/api/sync/refresh')
        assert first.status_code == 202 and entered.wait(1)
        job_id = first.json()['job_id']
        second = client.post('/api/sync', json={}).json()
        assert second['reused'] and second['job_id'] == job_id
        state = client.get('/api/sync/status').json()
        assert state['running'] and state['active_job']['job_id'] == job_id
        release.set()
        assert completed(app.state.store, job_id)['status'] == 'success'
    assert Store(db).get_job(job_id)['status'] == 'success'


def test_unchanged_sync_does_not_repeat_ai_and_changed_body_does(tmp_path):
    store, model = Store(tmp_path / 'radar.sqlite3'), FakeModel(configured=True)
    manager = SyncManager(store, model, SyncOptions(enabled=False), config_file(tmp_path), collecting(article()))
    first = completed(store, manager.submit()['job_id'])
    assert first['inserted'] == 1 and first['ai_processed'] == 1 and model.calls == 1
    revision = store.data_revision()
    second = completed(store, manager.submit()['job_id'])
    assert second['unchanged'] == 1 and second['ai_processed'] == 0 and model.calls == 1
    assert store.data_revision() == revision
    manager.collector_factory = collecting(article(content='电子系讲座更新了内容。', fetched_at='2026-10-18T11:00:00+08:00'))
    third = completed(store, manager.submit()['job_id'])
    assert third['updated'] == 1 and third['ai_processed'] == 1 and model.calls == 2
    assert len(store.all()) == 1 and store.data_revision() > revision


def test_unconfigured_model_keeps_pending_and_failure_uses_backoff(tmp_path):
    store, model = Store(tmp_path / 'radar.sqlite3'), FakeModel()
    manager = SyncManager(store, model, SyncOptions(enabled=False), config_file(tmp_path), collecting(article()))
    first = completed(store, manager.submit()['job_id'])
    assert first['ai_skipped'] and model.calls == 0
    assert store.get('sync-a')['processing_status'] == 'pending'
    model.config, model.failing = AIConfig('https://example.invalid/v1', 'local-test-only', 'test'), True
    second = completed(store, manager.submit()['job_id'])
    assert second['ai_failed'] == 1 and model.calls == 1
    assert store.get('sync-a')['next_analysis_at']
    completed(store, manager.submit()['job_id'])
    assert model.calls == 1 and store.get('sync-a')['content'] == article()['content']


def test_source_failure_keeps_history_and_does_not_block_other_sources(tmp_path):
    store = Store(tmp_path / 'radar.sqlite3')
    store.upsert(article())

    class PartialCollector:
        def __init__(self, store):
            self.store = store

        def run(self, source, config, limit):
            if source == 'info':
                raise RuntimeError('不应回传的内部异常')
            self.store.upsert(article(id='sync-b', source_id='ee', source_record_id='ee-b', source_url='https://www.ee.tsinghua.edu.cn/info/1/2.htm'))
            return {'status': 'success', 'fetched': 1, 'inserted': 1}

    path = config_file(tmp_path, {'info': {'name': 'info', 'enabled': True}, 'ee': {'name': '电子系', 'enabled': True}, 'sports': {'name': '体育', 'enabled': False}})
    manager = SyncManager(store, FakeModel(), SyncOptions(enabled=False), path, PartialCollector)
    job = completed(store, manager.submit()['job_id'])
    assert job['status'] == 'partial' and len(job['sources']) == 2
    assert len(store.all()) == 2 and store.get('sync-a')['content']
    assert '内部异常' not in json.dumps(job, ensure_ascii=False)


def test_analysis_result_cannot_overwrite_new_body(tmp_path):
    store, model = Store(tmp_path / 'radar.sqlite3'), FakeModel(configured=True)
    store.upsert(article())
    complete = model.complete

    def update_during_analysis(system, payload):
        store.upsert(article(content='分析期间更新的新正文。', fetched_at='2026-10-18T12:00:00+08:00'))
        return complete(system, payload)

    model.complete = update_during_analysis
    with pytest.raises(ValueError, match='正文版本已变化'):
        AnalysisRunner(store, model).analyze('sync-a')
    assert store.get('sync-a')['content'] == '分析期间更新的新正文。'
    assert store.get('sync-a')['processing_status'] == 'pending'


def test_only_one_scheduler_runs_for_same_database(tmp_path):
    started, release = Event(), Event()
    calls = []

    class BlockingCollector:
        def __init__(self, store):
            pass

        def run(self, source, config, limit):
            calls.append(source)
            started.set()
            assert release.wait(2)
            return {'status': 'success', 'fetched': 0}

    path, db = config_file(tmp_path), tmp_path / 'radar.sqlite3'
    options = SyncOptions(enabled=True, interval_seconds=3600, startup_delay_seconds=0)
    first = SyncManager(Store(db), FakeModel(), options, path, BlockingCollector)
    second = SyncManager(Store(db), FakeModel(), options, path, BlockingCollector)
    try:
        first.start()
        assert started.wait(1)
        second.start()
        reused = second.submit()
        assert reused['reused'] and calls == ['info']
        release.set()
        job = completed(first.store, reused['job_id'])
        assert job['trigger'] == 'startup' and job['status'] == 'success'
    finally:
        release.set()
        second.stop()
        first.stop()
