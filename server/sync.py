"""后台定时与手动同步共用持久任务，失败保留数据库中的历史内容。"""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event, Lock, Thread

from crawler.collect import Client, Collector
from .ai import AIError, ANALYZE_SYSTEM, validate_analysis
from .domain import TZ, now

ROOT = Path(__file__).resolve().parent.parent


def env_int(name, default, minimum, maximum):
    try:
        return max(minimum, min(maximum, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


@dataclass(frozen=True)
class SyncOptions:
    enabled: bool = True
    interval_seconds: int = 900
    startup_delay_seconds: float = 5
    limit: int = 20
    ai_batch_size: int = 10

    @classmethod
    def from_environment(cls):
        return cls(
            enabled=os.getenv('EE_RADAR_SYNC_ENABLED', 'true').lower() not in ('0', 'false', 'no'),
            interval_seconds=env_int('EE_RADAR_SYNC_INTERVAL_SECONDS', 900, 60, 86400),
            startup_delay_seconds=env_int('EE_RADAR_SYNC_STARTUP_DELAY_SECONDS', 5, 0, 300),
            limit=env_int('EE_RADAR_SYNC_LIMIT', 20, 1, 100),
            ai_batch_size=env_int('EE_RADAR_AI_BATCH_SIZE', 10, 1, 100),
        )


class AnalysisBusy(Exception):
    pass


class AnalysisRunner:
    """手动与后台分析共享一把文件锁，防止同一正文重复调用模型。"""

    def __init__(self, store, model):
        self.store, self.model = store, model
        self.lock_path = store.path.with_suffix('.analysis.lock')

    @contextmanager
    def claim(self):
        with self.lock_path.open('a') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise AnalysisBusy('已有资讯分析正在执行，请稍后重试。') from None
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def analyze(self, item_id):
        with self.claim():
            item = self.store.get(item_id)
            if not item:
                raise KeyError(item_id)
            if item['processing_status'] == 'success':
                return {'item': item, 'cached': True, 'mode': 'ai'}
            if not self.model.config.configured:
                raise AIError('模型服务尚未配置；当前保留原文摘录与规则分类。')
            evidence = item['content'] or item.get('content_preview')
            if not evidence:
                raise AIError('当前仅有标题元数据，缺少足够正文，暂不执行 AI 分析。')
            expected_hash = item.get('content_hash')
            try:
                raw = self.model.complete(ANALYZE_SYSTEM, {'title': item['title'], 'content': evidence[:16000], 'published_at': item['published_at']})
                result = validate_analysis(raw, item)
            except AIError as exc:
                self.store.save_analysis(item_id, {'processing_status': 'failed', 'processing_error': str(exc)}, expected_hash=expected_hash)
                raise
            return {'item': self.store.save_analysis(item_id, result, expected_hash=expected_hash), 'cached': False, 'mode': 'ai'}


class SyncManager:
    def __init__(self, store, model, options=None, config_path=None, collector_factory=None):
        self.store, self.model = store, model
        self.options = options or SyncOptions.from_environment()
        self.config_path = Path(config_path or ROOT / 'crawler/sources.json')
        self.collector_factory = collector_factory or (lambda store: Collector(Client(interval=1.5), store))
        self.analysis = AnalysisRunner(store, model)
        self.stop_event, self.guard = Event(), Lock()
        self.scheduler = None
        self.worker = None
        self.next_run_at = None
        self.scheduler_error = None
        self.scheduler_lock_path = store.path.with_suffix('.scheduler.lock')

    def configs(self):
        value = json.loads(self.config_path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('信源配置格式无效。')
        return value

    def start(self):
        self.store.recover_jobs()
        if self.options.enabled and not self.scheduler:
            self.stop_event.clear()
            self.scheduler = Thread(target=self._schedule, name='radar-scheduler', daemon=True)
            self.scheduler.start()

    def stop(self):
        self.stop_event.set()
        if self.scheduler:
            self.scheduler.join(timeout=2)
        # 网络请求有自身超时；关闭不等待整轮采集，不清除仍在工作的任务锁。
        if self.worker:
            self.worker.join(timeout=1)

    def _schedule(self):
        # flock 随进程退出释放，多 worker / 热重载不会启动多个定时器。
        with self.scheduler_lock_path.open('a') as handle:
            while not self.stop_event.is_set():
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if self.stop_event.wait(5):
                        return
            if self.stop_event.is_set():
                return
            try:
                latest = self.store.latest_job()
                wait = self.options.startup_delay_seconds
                if latest:
                    reference = latest.get('finished_at') or latest.get('started_at')
                    if reference:
                        due = datetime.fromisoformat(reference) + timedelta(seconds=self.options.interval_seconds)
                        wait = max(wait, (due - datetime.now(TZ)).total_seconds())
                trigger = 'startup'
                while not self.stop_event.is_set():
                    self.next_run_at = (datetime.now(TZ) + timedelta(seconds=wait)).isoformat(timespec='seconds')
                    if self.stop_event.wait(wait):
                        return
                    try:
                        self.submit(trigger=trigger)
                        self.scheduler_error = None
                    except Exception:
                        self.scheduler_error = '定时同步暂未启动，将在下个周期重试；已有资讯保留。'
                    trigger, wait = 'scheduled', self.options.interval_seconds
            finally:
                self.next_run_at = None
                fcntl.flock(handle, fcntl.LOCK_UN)

    def submit(self, sources=None, limit=None, trigger='manual'):
        configs = self.configs()
        selected = list(dict.fromkeys(sources if sources is not None else [key for key, config in configs.items() if config.get('enabled')]))
        if not selected or any(source not in configs for source in selected):
            raise ValueError('没有可同步的有效信源。')
        limit = limit if limit is not None else self.options.limit
        if not 1 <= limit <= 100:
            raise ValueError('每个来源同步条数必须在 1–100 之间。')
        with self.guard:
            self.store.recover_jobs()
            job, reused = self.store.create_job(trigger, selected, limit)
            if not reused:
                try:
                    self.worker = Thread(target=self._run, args=(job['job_id'], selected, limit, configs), name='radar-sync', daemon=True)
                    self.worker.start()
                except Exception:
                    self.store.update_job(job['job_id'], status='failed', finished_at=now(), error_message='同步任务未启动，已有资讯保留。')
                    raise
            return {**job, 'reused': reused}

    def status(self):
        latest = self.store.latest_job()
        active = latest if latest and latest['status'] == 'running' else None
        return {'enabled': self.options.enabled, 'interval_seconds': self.options.interval_seconds,
                'next_run_at': self.next_run_at, 'running': active is not None, 'active_job': active,
                'latest_job': latest, 'data_revision': self.store.data_revision(),
                'scheduler_error': self.scheduler_error, 'limit': self.options.limit}

    def _run(self, job_id, sources, limit, configs):
        states = []
        totals = dict(inserted=0, updated=0, unchanged=0, stale=0, fetched=0, ai_processed=0, ai_failed=0)
        ai_skipped = None
        try:
            collector = self.collector_factory(self.store)
            for source in sources:
                if self.stop_event.is_set():
                    break
                try:
                    result = collector.run(source, configs[source], limit)
                except Exception:
                    result = {'name': configs[source].get('name', source), 'status': 'failed', 'errors': [{'message': '该来源采集失败，已有缓存已保留。'}]}
                    self.store.set_status(source, result)
                states.append({'source_id': source, **result})
                for key in ('inserted', 'updated', 'unchanged', 'stale', 'fetched'):
                    totals[key] += int(result.get(key) or 0)
                self.store.update_job(job_id, sources=states, **totals)
            if self.stop_event.is_set():
                status = 'interrupted'
            else:
                if self.model.config.configured:
                    for item in self.store.pending_analysis(self.options.ai_batch_size):
                        if self.stop_event.is_set():
                            break
                        try:
                            analyzed = self.analysis.analyze(item['id'])
                            if not analyzed['cached']:
                                totals['ai_processed'] += 1
                        except AnalysisBusy:
                            ai_skipped = '已有分析正在执行，待处理文章留待后续周期。'
                            break
                        except ValueError:
                            # 旧正文分析不覆盖新版本，下一周期处理新正文。
                            pass
                        except (AIError, KeyError):
                            totals['ai_failed'] += 1
                        self.store.update_job(job_id, **totals)
                else:
                    ai_skipped = '模型未配置，保留原文摘录与规则结果，待配置后增量分析。'
                successful = any(s['status'] == 'success' or s.get('fetched') for s in states)
                status = 'success' if states and all(s['status'] == 'success' for s in states) and not totals['ai_failed'] else 'partial' if successful else 'failed'
                if self.stop_event.is_set():
                    status = 'interrupted'
            self.store.update_job(job_id, status=status, sources=states, finished_at=now(), ai_skipped=ai_skipped, **totals)
        except Exception:
            self.store.update_job(job_id, status='failed', sources=states, finished_at=now(), error_message='本轮同步未完成，已入库资讯与历史数据均保留。', **totals)
