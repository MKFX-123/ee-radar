"""SQLite 数据与采集状态；同步失败不会清空原有内容。"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock

from .domain import RULE_VERSION, TZ, decorate, moment, normalize, now, rule_events
from crawler.policy import within_publication_window
from crawler.classification import CATEGORIES, assign_category
from .evidence import extract, EXTRACTION_VERSION


CONTENT_RANK = {'metadata_only': 0, 'list_excerpt': 1, 'event_description': 1, 'full_text': 2, 'web_text': 2, 'web_text_with_attachments': 2}
ANALYSIS_FIELDS = ('summary', 'summary_kind', 'summary_evidence', 'category', 'category_method', 'rule_version', 'subcategory', 'tags', 'audience', 'target_audience', 'is_opportunity', 'action_items', 'action_evidence', 'materials', 'material_evidence', 'events', 'processed_at', 'processing_status', 'processing_error', 'analysis_attempts', 'next_analysis_at')
JOB_STATUSES = {'running', 'success', 'partial', 'failed', 'interrupted'}


def content_hash(item):
    inputs = {key: item.get(key) for key in ('title', 'published_at', 'source_category')}
    inputs['text'] = item.get('content') or item.get('content_preview') or ''
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def display_value(item):
    # 抓取与重试元数据变化不让页面把相同内容当成新版。
    ignored = {'fetched_at', 'processed_at', 'content_hash', 'content_version', 'content_status', 'coverage', 'attachments_status', 'analysis_attempts', 'next_analysis_at'}
    return {key: value for key, value in item.items() if key not in ignored}


def process_marker(pid):
    try:
        result = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='], capture_output=True, text=True, timeout=2)
        return result.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def owner_alive(pid, marker=None):
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except PermissionError:
        return True
    current = process_marker(pid) if marker else None
    return not (marker and current and marker != current)


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.owner_pid = os.getpid()
        self.owner_token = uuid.uuid4().hex
        self.owner_id = process_marker(self.owner_pid)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, source TEXT NOT NULL, external_id TEXT, canonical_url TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS source_external ON items(source,external_id) WHERE external_id IS NOT NULL;
                CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, item_id TEXT NOT NULL, event_type TEXT NOT NULL, start_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS event_time ON events(start_at);
                CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS item_urls (canonical_url TEXT PRIMARY KEY, item_id TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS item_url_owner ON item_urls(item_id);
                CREATE TABLE IF NOT EXISTS item_identifiers (source TEXT NOT NULL, external_id TEXT NOT NULL, item_id TEXT NOT NULL, PRIMARY KEY(source,external_id));
                CREATE INDEX IF NOT EXISTS item_identifier_owner ON item_identifiers(item_id);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
                INSERT OR IGNORE INTO metadata VALUES ('data_revision',0);
                CREATE TABLE IF NOT EXISTS sync_jobs (id TEXT PRIMARY KEY, status TEXT NOT NULL, owner_pid INTEGER NOT NULL, owner_token TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS single_running_job ON sync_jobs(status) WHERE status='running';
            ''')
            db.execute('BEGIN IMMEDIATE')
            # source 列用于真实信源内部唯一键；payload.source 仍为展示所需聚合类型。
            for item_id, payload in db.execute('SELECT id,payload FROM items').fetchall():
                item = json.loads(payload)
                old_display = display_value(item)
                if item.get('category') not in CATEGORIES or (item.get('category_method') != 'ai' and item.get('rule_version') != RULE_VERSION):
                    item = assign_category(item)
                if item.get('extraction_version') != EXTRACTION_VERSION:
                    updated = extract(item['id'], item['title'], item.get('content') or item.get('content_preview') or '', item.get('published_at'))
                    # 保留已验证的 AI 日程，规则补充缺失时间；不改已有摘要。
                    if item.get('summary_kind') == 'ai':
                        keys = {(e['event_type'], e['start_at'][:10]) for e in item.get('events', [])}
                        updated['events'] = [e for e in item.get('events', []) if e['event_type'] != 'deadline' or updated['is_opportunity']] + [e for e in updated['events'] if (e['event_type'], e['start_at'][:10]) not in keys]
                    item.update(updated)
                    self._save(db, item)
                item.setdefault('content_hash', content_hash(item))
                item.setdefault('content_version', 1)
                item.setdefault('analysis_attempts', 0)
                item.setdefault('next_analysis_at', None)
                db.execute('UPDATE items SET source=?,payload=? WHERE id=?', (item['source_id'], json.dumps(item, ensure_ascii=False), item_id))
                self._remember_identity(db, item)
                if display_value(item) != old_display:
                    self._bump_revision(db)

    def connect(self):
        return sqlite3.connect(self.path, timeout=20)

    def _remember_identity(self, db, item):
        db.execute('INSERT INTO item_urls VALUES (?,?) ON CONFLICT(canonical_url) DO UPDATE SET item_id=excluded.item_id', (item['canonical_url'], item['id']))
        if item.get('external_id') is not None:
            db.execute('INSERT INTO item_identifiers VALUES (?,?,?) ON CONFLICT(source,external_id) DO UPDATE SET item_id=excluded.item_id', (item['source_id'], item['external_id'], item['id']))

    def _bump_revision(self, db):
        db.execute("UPDATE metadata SET value=value+1 WHERE key='data_revision'")

    def data_revision(self):
        with self.connect() as db:
            return db.execute("SELECT value FROM metadata WHERE key='data_revision'").fetchone()[0]

    def _save(self, db, item):
        db.execute('INSERT INTO items VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET source=excluded.source, external_id=excluded.external_id,canonical_url=excluded.canonical_url,payload=excluded.payload', (item['id'], item['source_id'], item.get('external_id'), item['canonical_url'], json.dumps(item, ensure_ascii=False)))
        db.execute('DELETE FROM events WHERE item_id=?', (item['id'],))
        for event in item.get('events', []):
            event['item_id'] = item['id']
            db.execute('INSERT INTO events VALUES (?,?,?,?,?)', (event['id'], item['id'], event['event_type'], event['start_at'], json.dumps(event, ensure_ascii=False)))
        self._remember_identity(db, item)

    def upsert(self, raw: dict) -> bool:
        return self.upsert_result(raw)['change'] == 'inserted'

    def upsert_result(self, raw: dict) -> dict:
        item = normalize(raw)
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            matches = db.execute('''SELECT payload FROM items WHERE canonical_url=? OR (source=? AND external_id=?)
                OR id IN (SELECT item_id FROM item_urls WHERE canonical_url=?
                          UNION SELECT item_id FROM item_identifiers WHERE source=? AND external_id=?)''',
                (item['canonical_url'], item['source_id'], item.get('external_id'), item['canonical_url'], item['source_id'], item.get('external_id'))).fetchall()
            candidates = [json.loads(match[0]) for match in matches]
            candidates.sort(key=lambda old: (not (old['source_id'] == item['source_id'] and item.get('external_id') is not None and old.get('external_id') == item['external_id']), -len(old.get('content') or '')))
            old = candidates[0] if candidates else None
            if old and any(moment(item['fetched_at']) < moment(candidate['fetched_at']) for candidate in candidates):
                return {'id': old['id'], 'change': 'stale', 'content_changed': False}
            if old:
                item['id'] = old['id']
                item['external_id'] = item['external_id'] or old.get('external_id')
                richest = max(candidates, key=lambda value: (CONTENT_RANK.get(value.get('content_status'), 0), len(value.get('content') or '')))
                if richest.get('content') and (not item['content'] or CONTENT_RANK.get(richest.get('content_status'), 0) > CONTENT_RANK.get(item.get('content_status'), 0)):
                    for field in ('content', 'content_status', 'coverage', 'attachments', 'attachments_status'):
                        item[field] = richest.get(field)
                for field in ('content_preview', 'published_at', 'source_category', 'publisher', 'coverage'):
                    if not item.get(field):
                        item[field] = old.get(field)
                if 'attachments' not in raw:
                    item['attachments'] = old.get('attachments') or []
                    item['attachments_status'] = old.get('attachments_status') or 'not_checked'
                # 保留正文后重新生成规则字段与事件，不能沿用列表摘录的提取结果。
                item = normalize(item)
            elif db.execute('SELECT 1 FROM items WHERE id=?', (item['id'],)).fetchone():
                item['id'] = hashlib.sha256((item['source_id'] + ':' + item['canonical_url']).encode()).hexdigest()[:24]
                item['events'] = rule_events(item['id'], item['content'] or item.get('content_preview') or '')
            item['content_hash'] = content_hash(item)
            changed = old is None or item['content_hash'] != (old.get('content_hash') or content_hash(old))
            item['content_version'] = (old.get('content_version', 1) + int(changed)) if old else 1
            item['analysis_attempts'], item['next_analysis_at'] = 0, None
            if old and not changed:
                for field in ANALYSIS_FIELDS:
                    if field in old:
                        item[field] = old[field]
            if old and old.get('discovered_by'):
                item['discovered_by'] = list({json.dumps(entry, sort_keys=True): entry for entry in old['discovered_by'] + item.get('discovered_by', [])}.values())
            for duplicate in candidates[1:]:
                db.execute('UPDATE item_urls SET item_id=? WHERE item_id=?', (item['id'], duplicate['id']))
                db.execute('UPDATE item_identifiers SET item_id=? WHERE item_id=?', (item['id'], duplicate['id']))
                db.execute('DELETE FROM events WHERE item_id=?', (duplicate['id'],))
                db.execute('DELETE FROM items WHERE id=?', (duplicate['id'],))
            visible_changed = old is None or len(candidates) > 1 or display_value(item) != display_value(old)
            self._save(db, item)
            if visible_changed:
                self._bump_revision(db)
            return {'id': item['id'], 'change': 'inserted' if old is None else 'updated' if changed or visible_changed else 'unchanged', 'content_changed': changed}

    def save_analysis(self, item_id: str, result: dict, expected_hash=None) -> dict:
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM items WHERE id=?', (item_id,)).fetchone()
            if not row:
                raise KeyError(item_id)
            item = json.loads(row[0])
            if expected_hash is not None and expected_hash != item.get('content_hash'):
                raise ValueError('正文版本已变化，分析结果未写入。')
            old_display = display_value(item)
            protected = {'id', 'source', 'source_id', 'external_id', 'source_url', 'canonical_url', 'title', 'content', 'content_preview', 'published_at', 'fetched_at', 'content_hash', 'content_version'}
            if protected.intersection(result):
                raise ValueError('分析结果不能修改原始资讯或正文版本。')
            item.update(result)
            if result.get('processing_status') == 'failed':
                item['analysis_attempts'] = item.get('analysis_attempts', 0) + 1
                delay = min(86400, 300 * (2 ** min(item['analysis_attempts'] - 1, 9)))
                item['next_analysis_at'] = (datetime.now(TZ) + timedelta(seconds=delay)).isoformat(timespec='seconds')
            elif result.get('processing_status') == 'success':
                item['analysis_attempts'], item['next_analysis_at'] = 0, None
            self._save(db, item)
            if display_value(item) != old_display:
                self._bump_revision(db)
            return decorate(item)

    def pending_analysis(self, limit):
        if limit <= 0:
            return []
        at = datetime.now(TZ)
        values = []
        for item in self.all():
            if item.get('processing_status') == 'success' or not (item.get('content') or item.get('content_preview')):
                continue
            retry = item.get('next_analysis_at')
            if item.get('processing_status') == 'failed' and retry and moment(retry) > at:
                continue
            values.append(item)
        values.sort(key=lambda item: (item.get('published_at') or '', item['id']), reverse=True)
        return values[:limit]

    def create_job(self, trigger, sources, limit):
        self._refresh_owner()
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._recover_jobs(db)
            active = db.execute("SELECT payload FROM sync_jobs WHERE status='running'").fetchone()
            if active:
                return json.loads(active[0]), True
            timestamp = now()
            job = {'job_id': uuid.uuid4().hex, 'status': 'running', 'trigger': trigger,
                   'sources': list(sources), 'limit': limit, 'created_at': timestamp,
                   'started_at': timestamp, 'updated_at': timestamp, 'finished_at': None,
                   'owner_pid': self.owner_pid, 'owner_token': self.owner_token, 'owner_id': self.owner_id,
                   'inserted': 0, 'updated': 0, 'unchanged': 0, 'stale': 0, 'fetched': 0,
                   'ai_processed': 0, 'ai_failed': 0, 'error_message': None}
            db.execute('INSERT INTO sync_jobs VALUES (?,?,?,?,?)', (job['job_id'], job['status'], self.owner_pid, self.owner_token, json.dumps(job, ensure_ascii=False)))
            return job, False

    def update_job(self, job_id, **fields):
        if {'job_id', 'owner_pid', 'owner_token', 'owner_id', 'created_at', 'started_at'}.intersection(fields):
            raise ValueError('不能修改任务标识或所属进程。')
        if fields.get('status', 'running') not in JOB_STATUSES:
            raise ValueError('无效同步任务状态。')
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM sync_jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            job = json.loads(row[0])
            # 已结束任务不可被晚到的线程重新激活或覆盖。
            if job['status'] != 'running':
                return job
            job.update(fields)
            job['updated_at'] = now()
            if job['status'] != 'running':
                job['finished_at'] = job.get('finished_at') or job['updated_at']
            db.execute('UPDATE sync_jobs SET status=?,payload=? WHERE id=?', (job['status'], json.dumps(job, ensure_ascii=False), job_id))
            return job

    def get_job(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM sync_jobs WHERE id=?', (job_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def latest_job(self):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM sync_jobs ORDER BY rowid DESC LIMIT 1').fetchone()
        return json.loads(row[0]) if row else None

    def _recover_jobs(self, db, owned_only=False):
        recovered = []
        for row in db.execute("SELECT payload FROM sync_jobs WHERE status='running'").fetchall():
            job = json.loads(row[0])
            owned = job.get('owner_pid') == self.owner_pid and job.get('owner_token') == self.owner_token
            if (owned_only and not owned) or (not owned_only and owner_alive(job.get('owner_pid'), job.get('owner_id'))):
                continue
            job.update(status='interrupted', finished_at=now(), updated_at=now(), error_message='同步所属进程已结束，本轮中断；已入库资讯保留。')
            db.execute('UPDATE sync_jobs SET status=?,payload=? WHERE id=?', (job['status'], json.dumps(job, ensure_ascii=False), job['job_id']))
            recovered.append(job)
        return recovered

    def _refresh_owner(self):
        # 预加载后 fork 的 worker 必须用自己的身份登记任务。
        if self.owner_pid != os.getpid():
            self.owner_pid = os.getpid()
            self.owner_token = uuid.uuid4().hex
            self.owner_id = process_marker(self.owner_pid)

    def recover_jobs(self, owned_only=False):
        self._refresh_owner()
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            return self._recover_jobs(db, owned_only)

    def get(self, item_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute('SELECT payload FROM items WHERE id=?', (item_id,)).fetchone()
        item = json.loads(row[0]) if row else None
        return decorate(item) if item and within_publication_window(item) else None

    def all(self) -> list[dict]:
        with self.connect() as db:
            items = [json.loads(row[0]) for row in db.execute('SELECT payload FROM items')]
            return [decorate(item) for item in items if within_publication_window(item)]

    def query(self, *, category=None, source=None, keyword=None, days=None, audience=None, status=None, page=1, page_size=20, opportunity=False, exclude_sources=None, ids=None, at=None):
        at = at or datetime.now(TZ)
        values = self.all()
        values = [decorate(item, at) for item in values]
        if category:
            values = [x for x in values if x['category'] == category]
        if source:
            values = [x for x in values if source in (x['source'], x['source_id'], x['source_name']) or any(entry.get('source_id') == source for entry in x.get('discovered_by', []) if isinstance(entry, dict))]
        if exclude_sources:
            values = [x for x in values if x['source_id'] not in exclude_sources and x['source'] not in exclude_sources]
        if ids is not None:
            values = [x for x in values if x['id'] in ids]
        if opportunity:
            values = [x for x in values if x['is_opportunity']]
        if audience:
            values = [x for x in values if audience in x['audience']]
        if keyword:
            terms = keyword.casefold().split()
            values = [x for x in values if all(term in ' '.join((x['title'], x['content'], x.get('summary') or '', ' '.join(x['tags']))).casefold() for term in terms)]
        if days:
            first_day = at.date() - timedelta(days=days - 1)
            values = [x for x in values if x['published_at'] and first_day <= moment(x['published_at']).date() <= at.date() and (len(x['published_at']) == 10 or moment(x['published_at']) <= at)]
        if status:
            status = {'open': 'active', 'expired': 'closed', 'pending': 'unknown'}.get(status, status)
            if status == 'active':
                values = [x for x in values if x['registration'] == 'open']
            elif opportunity and status in ('closed', 'unknown'):
                values = [x for x in values if x['registration'] == status]
            else:
                values = [x for x in values if x['status'] == status and (status != 'unknown' or x['is_opportunity'] or x['events'])]
        values.sort(key=lambda x: (x['published_at'] or '', x['id']), reverse=True)
        if opportunity:
            values.sort(key=lambda x: (x['status'] != 'active', x.get('deadline_at') or '9999'))
        total = len(values)
        return {'items': values[(page - 1) * page_size:page * page_size], 'total': total, 'page': page, 'page_size': page_size}

    def events(self, start=None, end=None, event_type=None, category=None, exclude_sources=None):
        items = self.all()
        result = []
        for item in items:
            if category and item['category'] != category:
                continue
            if exclude_sources and (item['source_id'] in exclude_sources or item['source'] in exclude_sources):
                continue
            for event in item['events']:
                if event_type and event['event_type'] != event_type:
                    continue
                if start and event['start_at'][:10] < start:
                    continue
                if end and event['start_at'][:10] > end:
                    continue
                result.append({**event, 'item': item})
        return sorted(result, key=lambda x: x['start_at'])

    def set_status(self, source_id: str, result: dict):
        if source_id == 'sports' and result.get('name') == '清华大学体育部':
            source_id = 'sports_site'
        with self.lock, self.connect() as db:
            row = db.execute('SELECT payload FROM sources WHERE id=?', (source_id,)).fetchone()
            old = json.loads(row[0]) if row else {}
            errors = result.get('errors') or []
            attempted = result.get('last_attempt_at') or now()
            result = {**old, **result, 'id': source_id, 'source_id': source_id, 'enabled': result.get('enabled', old.get('enabled', True)), 'last_attempt_at': attempted, 'last_success_at': result.get('last_success_at') or (attempted if result['status'] == 'success' else old.get('last_success_at')), 'error_message': '; '.join(x.get('message', '') for x in errors) or None}
            db.execute('INSERT INTO sources VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (source_id, json.dumps(result, ensure_ascii=False)))

    def source_status(self):
        with self.connect() as db:
            states = [json.loads(row[0]) for row in db.execute('SELECT payload FROM sources ORDER BY id')]
        counts, linked = {}, {}
        for item in self.all():
            key = item['source_id']
            counts[key] = counts.get(key, 0) + 1
            for source_id in {entry.get('source_id') for entry in item.get('discovered_by', []) if isinstance(entry, dict)} - {key, None}:
                linked[source_id] = linked.get(source_id, 0) + 1
        known = {s['id'] for s in states}
        states.extend({'id': key, 'name': key, 'status': 'idle', 'enabled': True} for key in counts if key not in known)
        for state in states:
            state['article_count'] = counts.get(state['id'], 0)
            state['linked_count'] = linked.get(state['id'], 0)
        latest = max((s.get('last_success_at') or '' for s in states), default='') or None
        return {'sources': states, 'last_success_at': latest, 'partial_failure': any(s.get('enabled', True) and s['status'] not in ('success', 'idle', 'not_configured') for s in states)}

    def ensure_sources(self, configs):
        with self.lock, self.connect() as db:
            for key, config in configs.items():
                row = db.execute('SELECT payload FROM sources WHERE id=?', (key,)).fetchone()
                old = json.loads(row[0]) if row else {'id': key, 'source_id': key, 'status': 'not_configured' if config.get('kind') == 'wechat' and not any(config.get(field) for field in ('urls', 'discovery_url', 'public_index_source')) else 'idle', 'last_attempt_at': None, 'last_success_at': None, 'error_message': None}
                state = {**old, 'name': config['name'], 'enabled': config.get('enabled', False)}
                db.execute('INSERT INTO sources VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (key, json.dumps(state, ensure_ascii=False)))

    def seed(self, path: Path):
        if not path.exists():
            return {'imported': 0, 'skipped': 0}
        document = json.loads(path.read_text(encoding='utf-8'))
        imported, skipped = 0, 0
        for article in document.get('articles', []):
            try:
                imported += self.upsert(article)
            except (ValueError, TypeError, KeyError):
                skipped += 1
        current = {x['id']: x for x in self.source_status()['sources']}
        for key, state in document.get('sources', {}).items():
            lookup = 'sports_site' if key == 'sports' and state.get('name') == '清华大学体育部' else key
            if lookup not in current or (state.get('last_attempt_at') or '') > (current[lookup].get('last_attempt_at') or ''):
                self.set_status(key, state)
        return {'imported': imported, 'skipped': skipped}
