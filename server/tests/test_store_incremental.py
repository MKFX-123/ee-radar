"""增量入库与任务持久化回归；仅访问临时数据库，不调用网络或真实模型。"""
import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from server.ai import AIConfig, AIError
from server.domain import moment, now
from server.store import Store
from server.sync import AnalysisRunner


def article(**changes):
    return {
        'id': 'first', 'source_id': 'info', 'source': '清华 info',
        'source_record_id': 'record-first',
        'source_url': 'https://info.tsinghua.edu.cn/incremental/first',
        'title': '本科生海外交流申请通知',
        'content': '面向本科生开放申请。\n报名截止：2026年10月20日17:00。\n填写申请表并提交材料。',
        'source_category': '国际交流', 'content_status': 'web_text',
        'published_at': '2026-10-17T10:00:00+08:00',
        'fetched_at': '2026-10-18T10:00:00+08:00', **changes,
    }


def another(name, **changes):
    return article(id=name, source_record_id='record-' + name,
                   source_url='https://info.tsinghua.edu.cn/incremental/' + name, **changes)


def analyzed(store, item_id='first'):
    return store.save_analysis(item_id, {
        'processing_status': 'success', 'summary': '已核验的交流申请摘要。',
        'summary_kind': 'ai', 'category_method': 'ai',
    }, expected_hash=store.get(item_id)['content_hash'])


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'incremental.sqlite3')


def test_upsert_result_and_boolean_compatibility(store):
    result = store.upsert_result(article())
    assert result['id'] == 'first'
    assert result['change'] == 'inserted'
    assert result['content_changed'] is True
    saved = store.get('first')
    assert saved['content_hash']
    assert saved['content_version'] >= 1
    assert store.upsert(article()) is False
    assert store.upsert(another('second')) is True


def test_content_hash_is_stable_across_record_identity_and_fetch_metadata(store):
    store.upsert_result(article())
    store.upsert_result(another('second', fetched_at='2026-10-18T15:00:00+08:00'))
    assert store.get('first')['content_hash'] == store.get('second')['content_hash']


@pytest.mark.parametrize('changes', [
    {'title': '本科生海外交流申请补充通知'},
    {'content': '面向本科生开放申请。新增了原文中的申请条件。'},
    {'published_at': '2026-10-18T09:00:00+08:00'},
    {'source_category': '教务通知'},
])
def test_effective_content_fields_increment_version_and_invalidate_analysis(store, changes):
    store.upsert_result(article())
    before = analyzed(store)
    result = store.upsert_result(article(**{**changes, 'fetched_at': '2026-10-18T11:00:00+08:00'}))
    after = store.get('first')
    assert result['change'] == 'updated'
    assert result['content_changed'] is True
    assert after['content_hash'] != before['content_hash']
    assert after['content_version'] == before['content_version'] + 1
    assert after['processing_status'] == 'pending'
    assert after['analysis_attempts'] == 0
    assert after['next_analysis_at'] is None


def test_excerpt_changes_version_when_body_is_unavailable(store):
    store.upsert_result(article(content='', content_preview='首版公开摘录', content_status='metadata_only'))
    before = store.get('first')
    result = store.upsert_result(article(content='', content_preview='更新后的公开摘录',
                                        content_status='metadata_only', fetched_at='2026-10-18T11:00:00+08:00'))
    after = store.get('first')
    assert result['content_changed'] is True
    assert after['content_version'] == before['content_version'] + 1
    assert after['content_hash'] != before['content_hash']


def test_fetch_time_and_content_status_preserve_cached_analysis(store):
    store.upsert_result(article())
    before = analyzed(store)
    result = store.upsert_result(article(fetched_at='2026-10-18T12:00:00+08:00',
                                        content_status='web_text_with_attachments'))
    after = store.get('first')
    assert result['change'] == 'unchanged'
    assert result['content_changed'] is False
    assert after['content_hash'] == before['content_hash']
    assert after['content_version'] == before['content_version']
    assert after['processing_status'] == 'success'
    assert after['summary'] == before['summary']
    assert after['fetched_at'] == '2026-10-18T12:00:00+08:00'


def test_same_text_quality_upgrade_keeps_analysis_and_content_version(store):
    store.upsert_result(article(content_status='list_excerpt'))
    before = analyzed(store)
    result = store.upsert_result(article(content_status='web_text',
                                        fetched_at='2026-10-18T12:00:00+08:00'))
    after = store.get('first')
    assert result['content_changed'] is False
    assert after['content_status'] == 'web_text'
    assert after['content_version'] == before['content_version']
    assert after['processing_status'] == 'success'
    assert after['summary'] == before['summary']


@pytest.mark.parametrize('content,content_status', [('', 'metadata_only'), ('列表中的非空短摘录', 'list_excerpt')])
def test_lower_quality_text_cannot_replace_stored_web_text(store, content, content_status):
    store.upsert_result(article())
    before = analyzed(store)
    result = store.upsert_result(article(content=content, content_status=content_status,
                                        content_preview='更新后的列表摘要',
                                        fetched_at='2026-10-18T12:00:00+08:00'))
    after = store.get('first')
    assert result['content_changed'] is False
    for field in ('content', 'content_status', 'content_hash', 'content_version', 'events', 'summary', 'processing_status'):
        assert after[field] == before[field]


def test_missing_publication_date_preserves_existing_hash_and_date(store):
    store.upsert_result(article())
    before = store.get('first')
    result = store.upsert_result(article(content='', published_at=None, content_status='metadata_only',
                                        fetched_at='2026-10-18T12:00:00+08:00'))
    after = store.get('first')
    assert result['content_changed'] is False
    assert after['published_at'] == before['published_at']
    assert after['content_hash'] == before['content_hash']


def test_previous_url_remains_an_alias_after_source_record_moves(store):
    old_url = article()['source_url']
    new_url = 'https://info.tsinghua.edu.cn/incremental/moved'
    store.upsert_result(article())
    moved = store.upsert_result(article(id='replacement', source_url=new_url,
                                       fetched_at='2026-10-18T11:00:00+08:00'))
    previous = store.upsert_result(article(id='old-url-candidate', source_record_id=None,
                                          source_url=old_url, fetched_at='2026-10-18T12:00:00+08:00'))
    assert moved['id'] == previous['id'] == 'first'
    assert len(store.all()) == 1


def test_dual_match_stale_against_either_candidate_never_deletes_rows(store):
    store.upsert_result(article())
    store.upsert_result(another('second', fetched_at='2026-10-18T15:00:00+08:00'))
    before = {item['id']: item for item in store.all()}
    result = store.upsert_result(article(id='incoming', source_url=another('second')['source_url'],
                                        content='过期采集内容不能覆盖任何候选。',
                                        fetched_at='2026-10-18T12:00:00+08:00'))
    assert result['change'] == 'stale'
    assert result['content_changed'] is False
    assert {item['id']: item for item in store.all()} == before


def test_current_dual_match_merges_and_retains_all_url_aliases(store):
    store.upsert_result(article())
    store.upsert_result(another('second'))
    result = store.upsert_result(article(source_url=another('second')['source_url'],
                                        fetched_at='2026-10-18T12:00:00+08:00'))
    assert result['id'] == 'first'
    assert len(store.all()) == 1
    alias = store.upsert_result(article(id='alias-only', source_record_id=None,
                                       fetched_at='2026-10-18T13:00:00+08:00'))
    assert alias['id'] == 'first'
    assert len(store.all()) == 1


def test_merged_record_retains_the_other_external_identifier(store):
    store.upsert_result(article())
    store.upsert_result(another('second'))
    store.upsert_result(article(source_url=another('second')['source_url'],
                               fetched_at='2026-10-18T12:00:00+08:00'))
    found = store.upsert_result(article(id='old-external-candidate', source_record_id='record-second',
                                       source_url='https://info.tsinghua.edu.cn/incremental/second-moved',
                                       fetched_at='2026-10-18T13:00:00+08:00'))
    assert found['id'] == 'first'
    assert len(store.all()) == 1


def test_old_analysis_cannot_overwrite_new_content_hash(store):
    store.upsert_result(article())
    expected = store.get('first')['content_hash']
    store.upsert_result(article(content='更新后的真实正文。', fetched_at='2026-10-18T12:00:00+08:00'))
    current = store.get('first')
    with pytest.raises(ValueError):
        store.save_analysis('first', {'summary': '旧正文的分析', 'processing_status': 'success'},
                            expected_hash=expected)
    assert store.get('first') == current


def test_failed_analysis_persists_attempts_and_retry_time_then_success_clears_backoff(store):
    store.upsert_result(article())
    expected = store.get('first')['content_hash']
    before = moment(now())
    first = store.save_analysis('first', {'processing_status': 'failed', 'processing_error': '临时失败'},
                                expected_hash=expected)
    assert first['analysis_attempts'] == 1
    assert moment(first['next_analysis_at']) > before
    second = store.save_analysis('first', {'processing_status': 'failed', 'processing_error': '再次失败'},
                                 expected_hash=expected)
    assert second['analysis_attempts'] == 2
    assert moment(second['next_analysis_at']) >= moment(first['next_analysis_at'])
    assert Store(store.path).get('first')['analysis_attempts'] == 2
    success = analyzed(store)
    assert success['processing_status'] == 'success'
    assert success['next_analysis_at'] is None


def test_new_content_resets_failed_attempts_and_retry_time(store):
    store.upsert_result(article())
    store.save_analysis('first', {'processing_status': 'failed', 'processing_error': '临时失败'})
    store.upsert_result(article(content='已更新、可以重新分析的正文。',
                               fetched_at='2026-10-18T12:00:00+08:00'))
    item = store.get('first')
    assert item['processing_status'] == 'pending'
    assert item['analysis_attempts'] == 0
    assert item['next_analysis_at'] is None


def test_pending_analysis_excludes_success_empty_body_and_backoff_and_respects_limit(store):
    for name, changes in [
        ('pending', {}), ('excerpt', {'content': '', 'content_preview': '可分析的公开摘录'}),
        ('empty', {'content': '', 'content_preview': None}), ('success', {}), ('backoff', {}),
    ]:
        store.upsert_result(another(name, **changes))
    analyzed(store, 'success')
    store.save_analysis('backoff', {'processing_status': 'failed', 'processing_error': '临时失败'})
    expected = {'pending', 'excerpt'}
    assert {item['id'] for item in store.pending_analysis(20)} == expected
    limited = store.pending_analysis(1)
    assert len(limited) == 1
    assert limited[0]['id'] in expected


def test_expired_backoff_is_eligible_again(store):
    store.upsert_result(article())
    store.save_analysis('first', {'processing_status': 'failed', 'processing_error': '临时失败'})
    with store.connect() as db:
        row = db.execute('SELECT payload FROM items WHERE id=?', ('first',)).fetchone()
        item = json.loads(row[0])
        item['next_analysis_at'] = '2000-01-01T00:00:00+08:00'
        db.execute('UPDATE items SET payload=? WHERE id=?', (json.dumps(item), 'first'))
    assert [item['id'] for item in store.pending_analysis(5)] == ['first']


def test_unconfigured_model_leaves_article_pending_without_failure(store):
    class UnconfiguredModel:
        config = AIConfig()

        def complete(self, *_args, **_kwargs):
            raise AssertionError('未配置时不能调用模型')

    store.upsert_result(article())
    before = store.get('first')
    with pytest.raises(AIError):
        AnalysisRunner(store, UnconfiguredModel()).analyze('first')
    assert store.get('first') == before
    assert before['processing_status'] == 'pending'
    assert before['analysis_attempts'] == 0


def test_data_revision_changes_only_for_content_or_visible_analysis(store):
    initial = store.data_revision()
    store.upsert_result(article())
    inserted = store.data_revision()
    assert inserted > initial
    store.upsert_result(article(fetched_at='2026-10-18T11:00:00+08:00'))
    assert store.data_revision() == inserted
    analyzed(store)
    visible = store.data_revision()
    assert visible > inserted
    analyzed(store)
    assert store.data_revision() == visible
    job, _ = store.create_job('manual', ['info'], 2)
    store.update_job(job['job_id'], fetched=10, unchanged=10)
    assert store.data_revision() == visible
    store.upsert_result(article(content='已更新的正文。', fetched_at='2026-10-18T12:00:00+08:00'))
    assert store.data_revision() > visible


def test_same_analysis_with_only_processed_time_change_preserves_revision(store):
    store.upsert_result(article())
    before = store.get('first')
    result = {'processing_status': 'success', 'summary': '已核验摘要', 'summary_kind': 'ai',
              'processed_at': '2026-10-18T10:00:00+08:00'}
    store.save_analysis('first', result, expected_hash=before['content_hash'])
    revision = store.data_revision()
    store.save_analysis('first', {**result, 'processed_at': '2026-10-18T12:00:00+08:00'},
                        expected_hash=before['content_hash'])
    assert store.data_revision() == revision
    assert store.get('first')['content_version'] == before['content_version']
    assert store.get('first')['processed_at'] == '2026-10-18T12:00:00+08:00'


def test_jobs_persist_across_store_instances_and_completed_job_allows_new_work(store):
    first, reused = store.create_job('manual', ['info'], 2)
    assert reused is False
    assert first['status'] == 'running'
    reopened = Store(store.path)
    assert reopened.get_job(first['job_id'])['trigger'] == 'manual'
    same, reused = reopened.create_job('scheduled', ['ee'], 3)
    assert reused is True
    assert same['job_id'] == first['job_id']
    store.update_job(first['job_id'], status='success', finished_at=now(), inserted=2)
    assert reopened.get_job(first['job_id'])['inserted'] == 2
    second, reused = reopened.create_job('scheduled', ['ee'], 3)
    assert reused is False
    assert second['job_id'] != first['job_id']
    assert Store(store.path).latest_job()['job_id'] == second['job_id']


def test_cross_store_concurrent_create_has_exactly_one_running_job(tmp_path):
    stores = [Store(tmp_path / 'racing.sqlite3') for _ in range(4)]
    ready = Barrier(len(stores))

    def create(index):
        ready.wait(timeout=10)
        return stores[index].create_job('manual', ['info'], index + 1)

    with ThreadPoolExecutor(max_workers=len(stores)) as pool:
        results = list(pool.map(create, range(len(stores))))
    assert len({job['job_id'] for job, _ in results}) == 1
    assert sum(not reused for _, reused in results) == 1
    assert all(store.latest_job()['status'] == 'running' for store in stores)


def _hold_job(path, queue, release):
    """让独立 owner 保持存活；所有文件都在测试临时目录中。"""
    store = Store(path)
    job, _ = store.create_job('manual', ['info'], 2)
    queue.put(job['job_id'])
    release.wait(timeout=15)


def test_recovery_preserves_live_foreign_owner_and_interrupts_after_exit(tmp_path):
    context = multiprocessing.get_context('spawn')
    queue, release = context.Queue(), context.Event()
    path = tmp_path / 'owner.sqlite3'
    store = Store(path)
    process = context.Process(target=_hold_job, args=(path, queue, release))
    process.start()
    try:
        job_id = queue.get(timeout=10)
        store.recover_jobs()
        assert store.get_job(job_id)['status'] == 'running'
        store.recover_jobs(owned_only=True)
        assert store.get_job(job_id)['status'] == 'running'
        release.set()
        process.join(timeout=10)
        assert process.exitcode == 0
        recovered = store.recover_jobs()
        assert recovered
        assert store.get_job(job_id)['status'] == 'interrupted'
        assert Store(path).get_job(job_id)['finished_at']
    finally:
        release.set()
        process.join(timeout=2)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
        queue.close()


def test_preloaded_store_refreshes_inherited_process_identity(store):
    # 模拟继承父进程登记信息；实际跨进程恢复由上方 spawn 测试验证。
    old_token = store.owner_token
    store.owner_pid = os.getpid() + 1
    store.owner_id = '继承的进程标记'
    job, reused = store.create_job('manual', ['info'], 2)
    assert reused is False
    assert job['owner_pid'] == os.getpid()
    assert job['owner_token'] != old_token
    assert job['owner_id'] != '继承的进程标记'
    assert store.recover_jobs() == []
    assert store.get_job(job['job_id'])['status'] == 'running'
