"""本地 FastAPI 服务：优先展示缓存，后台增量同步与模型分析互不阻塞。"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import threading
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from pydantic import BaseModel, ConfigDict, Field
from dotenv import load_dotenv
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool

from crawler.collect import Collector
from .ai import AIConfig, AIError, ModelClient
from .domain import CATEGORIES, now
from .store import Store
from .model_config import ConfigError, ConfigStorageError, ModelSettings
from .sync import AnalysisBusy, SyncManager
from .retrieval import retrieve
from .chat import CHAT_TOTAL_SECONDS, constraint_notice, generate_answer, source_references

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env', override=False)


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')


class SyncRequest(StrictRequest):
    sources: Optional[list[Literal['info', 'ee', 'sports_site', 'sports', 'wechat', 'ee_wechat', 'tsinghua_wechat', 'aishengquan', 'thu_su']]] = Field(None, min_length=1, max_length=9)
    limit: Optional[int] = Field(None, ge=1, le=100)


class ChatRequest(StrictRequest):
    message: str = Field(min_length=1, max_length=2000)
    history: list[dict] = Field(default_factory=list, max_length=6)
    exclude_sources: list[str] = Field(default_factory=list, max_length=30)


class ModelConfigRequest(StrictRequest):
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    api_key: Optional[str] = Field(None, max_length=8192)


def csv(value: Optional[str]) -> Optional[list[str]]:
    return [x.strip() for x in value.split(',') if x.strip()] if value is not None else None


def retrieval_answer(items: list[dict], scope=None) -> str:
    if not items:
        return '当前收录的资讯中未找到与本次问题匹配的资料。当前检索仅覆盖本地已收录内容，不能据此判断学校是否发布过相关通知。'
    notice = constraint_notice(scope or {})
    lines = [notice] if notice else []
    lines.append('已从当前收录的资讯中检索到以下原文依据：')
    for index, item in enumerate(items, 1):
        fragment = (item['content'] or item.get('content_preview') or item['title']).replace('\n', ' ')[:180]
        lines.append(f'[{index}] {item["title"]}：{fragment}')
    lines.append('以上为原文检索摘录；申请资格和截止时间请以关联原文为准。')
    return '\n\n'.join(lines)


def create_app(db_path=None, seed_path=None, model_client=None, model_config_path=None, sync_options=None, sync_config_path=None, collector_factory=None):
    db_path = Path(db_path or os.getenv('EE_RADAR_DB_PATH', ROOT / 'server/data/radar.sqlite3'))
    store = Store(db_path)
    config = AIConfig.from_environment()
    settings = ModelSettings(Path(model_config_path) if model_config_path is not None else db_path.parent / 'model-config.json', config)
    model = model_client or ModelClient(config, config_provider=settings.snapshot)
    seed_path = Path(seed_path) if seed_path is not None else ROOT / 'public/data/articles.json'
    sync_manager = SyncManager(store, model, sync_options, sync_config_path, collector_factory)

    @asynccontextmanager
    async def lifespan(app):
        try:
            app.state.seed_result = store.seed(seed_path)
        except (OSError, ValueError, TypeError, KeyError):
            app.state.seed_result = {'error': '初始缓存无法读取；已有数据库内容仍可用。'}
        try:
            store.ensure_sources(sync_manager.configs())
        except (OSError, ValueError, TypeError, KeyError):
            pass
        sync_manager.start()
        try:
            yield
        finally:
            sync_manager.stop()

    app = FastAPI(title='EE Radar 本地 API', version='1.0.0', lifespan=lifespan)
    app.state.store, app.state.model, app.state.model_settings = store, model, settings
    app.state.sync_manager = sync_manager
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', 'testserver'])
    allowed_origins = ('http://localhost:5173', 'http://127.0.0.1:5173', 'http://localhost:4173', 'http://127.0.0.1:4173', 'http://localhost:8000', 'http://127.0.0.1:8000')
    app.add_middleware(CORSMiddleware, allow_origins=list(allowed_origins), allow_methods=['GET', 'POST', 'DELETE'], allow_headers=['Content-Type'])

    @app.exception_handler(RequestValidationError)
    async def safe_validation_error(request, exc):
        if request.url.path.startswith('/api/model/'):
            return JSONResponse({'detail': '模型配置请求格式无效，请检查地址、模型名称与 API Key 字段。'}, status_code=422)
        return await request_validation_exception_handler(request, exc)

    @app.middleware('http')
    async def local_write_guard(request: Request, call_next):
        origin = request.headers.get('origin')
        sensitive = request.url.path.startswith(('/api/model/', '/api/sync/')) or request.url.path in ('/api/chat', '/api/sync') or request.url.path.endswith('/analyze')
        try:
            local_client = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
        except ValueError:
            local_client = False
        if sensitive and (not local_client or (origin and origin not in allowed_origins) or request.headers.get('sec-fetch-site') == 'cross-site'):
            return JSONResponse({'detail': '模型与同步操作仅允许来自本机 EE Radar 页面。'}, status_code=403)
        if request.method in ('POST', 'DELETE') and origin and origin not in allowed_origins:
            return JSONResponse({'detail': '仅允许本地 EE Radar 页面发起操作。'}, status_code=403)
        response = await call_next(request)
        if sensitive:
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/api/model/config')
    def model_configuration():
        return settings.public()

    @app.post('/api/model/config')
    def save_model_configuration(request: ModelConfigRequest):
        try:
            return settings.save(request.base_url, request.model, request.api_key)
        except ConfigStorageError as exc:
            raise HTTPException(500, str(exc)) from None
        except ConfigError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.delete('/api/model/config')
    @app.post('/api/model/config/reset')
    def reset_model_configuration():
        try:
            return settings.reset()
        except ConfigStorageError as exc:
            raise HTTPException(500, str(exc)) from None

    @app.post('/api/model/test')
    def test_model_configuration():
        if not model.config.configured:
            raise HTTPException(503, '尚未配置完整模型服务，未发送测试请求。')
        try:
            return model.test_connection()
        except AIError as exc:
            raise HTTPException(502, str(exc)) from None

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'database': 'sqlite', 'item_count': len(store.all()), 'ai_configured': model.config.configured, 'model_configured': model.config.configured, 'timezone': 'Asia/Shanghai', 'server_time': now(), 'data_revision': store.data_revision(), 'seed': getattr(app.state, 'seed_result', None)}

    def list_items(category=None, source=None, keyword=None, days=None, audience=None, status=None, page=1, page_size=20, exclude_sources=None, ids=None, opportunity=False):
        if category and category not in CATEGORIES:
            raise HTTPException(422, '未知分类。')
        if status and status not in ('active', 'open', 'upcoming', 'ongoing', 'closed', 'expired', 'unknown', 'pending'):
            raise HTTPException(422, '未知状态。')
        return store.query(category=category, source=source, keyword=keyword, days=days, audience=audience, status=status, page=page, page_size=page_size, exclude_sources=csv(exclude_sources), ids=csv(ids), opportunity=opportunity)

    @app.get('/api/items')
    def items(category: Optional[str] = None, source: Optional[str] = None, keyword: Optional[str] = Query(None, max_length=500), days: Optional[int] = Query(None, ge=1, le=3650), audience: Optional[str] = None, status: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), exclude_sources: Optional[str] = None, ids: Optional[str] = None):
        return list_items(category, source, keyword, days, audience, status, page, page_size, exclude_sources, ids)

    @app.get('/api/opportunities')
    def opportunities(category: Optional[str] = None, source: Optional[str] = None, keyword: Optional[str] = Query(None, max_length=500), days: Optional[int] = Query(None, ge=1, le=3650), audience: Optional[str] = None, status: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), exclude_sources: Optional[str] = None, ids: Optional[str] = None):
        return list_items(category, source, keyword, days, audience, status, page, page_size, exclude_sources, ids, True)

    @app.get('/api/items/{item_id}')
    def item(item_id: str):
        result = store.get(item_id)
        if not result:
            raise HTTPException(404, '未找到该资讯。')
        return result

    @app.get('/api/events')
    def events(start: Optional[date] = None, end: Optional[date] = None, event_type: Optional[Literal['event', 'deadline']] = None, category: Optional[str] = None, exclude_sources: Optional[str] = None):
        if start and end and start > end:
            raise HTTPException(422, '开始日期不能晚于结束日期。')
        values = store.events(start.isoformat() if start else None, end.isoformat() if end else None, event_type, category, csv(exclude_sources))
        return {'items': values, 'total': len(values)}

    @app.get('/api/sources/status')
    def source_status():
        return store.source_status()

    @app.get('/api/sync/status')
    def synchronization_status():
        return sync_manager.status()

    @app.post('/api/sync/refresh', status_code=202)
    @app.post('/api/sync', status_code=202)
    def sync(request: SyncRequest = SyncRequest()):
        try:
            return sync_manager.submit(request.sources, request.limit)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except (OSError, KeyError, TypeError):
            raise HTTPException(503, '同步配置暂不可用，已有资讯仍可浏览。') from None

    @app.get('/api/sync/{job_id}')
    def sync_status(job_id: str):
        job = store.get_job(job_id)
        if not job:
            raise HTTPException(404, '未找到该同步任务。')
        return job

    @app.post('/api/items/{item_id}/analyze')
    def analyze(item_id: str):
        item = store.get(item_id)
        if not item:
            raise HTTPException(404, '未找到该资讯。')
        if item['processing_status'] == 'success':
            return {'item': item, 'cached': True, 'mode': 'ai'}
        if not model.config.configured:
            raise HTTPException(503, '模型服务尚未配置；当前展示原文摘录和规则分类，未发起模型请求。')
        evidence = item['content'] or item.get('content_preview')
        if not evidence:
            raise HTTPException(422, '当前仅有标题元数据，缺少足够正文，暂不执行 AI 分析。')
        try:
            return sync_manager.analysis.analyze(item_id)
        except AnalysisBusy as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        except KeyError:
            raise HTTPException(404, '未找到该资讯。') from None
        except AIError as exc:
            raise HTTPException(502, str(exc)) from None

    @app.post('/api/chat')
    async def chat(request: ChatRequest):
        if not request.message.strip():
            raise HTTPException(422, '问题不能为空。')
        items, scope = await run_in_threadpool(retrieve, store, request.message.strip(), request.exclude_sources, request.history)
        references = source_references(items, request.message, scope)
        result = {'answer': retrieval_answer(items, scope), 'references': references, 'mode': 'retrieval', 'model_configured': model.config.configured, 'search_scope': {**scope, 'matched_count': len(items), 'dataset': '本地已收录真实资讯'}, 'no_results': not items}
        if not items:
            result['fallback_reason'] = 'no_results'
            return result
        if not model.config.configured:
            result['fallback_reason'] = 'unconfigured'
            return result
        cancelled = threading.Event()
        progress_lock = threading.Lock()
        progress = {}

        def save_progress(partial):
            with progress_lock:
                if not cancelled.is_set():
                    progress.clear()
                    progress.update(partial)

        try:
            generated = await asyncio.wait_for(asyncio.to_thread(generate_answer, model, request.message, references, request.history, scope, progress_callback=save_progress, cancel_event=cancelled), timeout=CHAT_TOTAL_SECONDS)
            result.update(generated)
        except asyncio.TimeoutError:
            cancelled.set()
            with progress_lock:
                partial = dict(progress)
            if partial:
                result.update(partial)
                warning = '本次生成达到等待上限，以下保留已完成原文核验的部分分析；未完成的细节请核对原文。'
                result['answer'] = warning + '\n\n' + partial['answer']
                result.update(answer_partial=True, validation_warning=warning, model_timeout=True)
            else:
                result.update(model_error=f'模型生成超过 {CHAT_TOTAL_SECONDS} 秒，已返回检索资料；请稍后重试。', model_error_code='upstream_timeout', fallback_reason='model_error')
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return result

    return app


app = create_app()
