"""接口截止后保留已核验的部分答案，并停止后续重试。"""
import threading

from fastapi.testclient import TestClient

from server.ai import AIConfig
from server.app import create_app
from server.domain import now


def test_timeout_preserves_verified_progress_and_signals_cancellation(tmp_path, monkeypatch):
    completed = threading.Event()
    events = []

    def slow_answer(model, question, references, history, scope, *, progress_callback, cancel_event):
        events.append(cancel_event)
        progress_callback({'answer': '已核验：每队4人[1]。', 'mode': 'ai', 'answer_partial': True, 'citations': [{'id': 'a', 'source_excerpt': '每队4人。'}], 'model_attempts': 1})
        cancel_event.wait(1)
        # 截止后线程的迟到进度不能覆盖已经返回的快照。
        progress_callback({'answer': '迟到结果', 'mode': 'ai'})
        completed.set()
        return {'answer': '迟到结果', 'mode': 'ai'}

    class FakeModel:
        config = AIConfig('https://example.invalid/v1', 'test-only', 'test')

    monkeypatch.setattr('server.app.generate_answer', slow_answer)
    monkeypatch.setattr('server.app.CHAT_TOTAL_SECONDS', 0.05)
    app = create_app(tmp_path / 'db.sqlite3', tmp_path / 'missing.json', FakeModel())
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        app.state.store.upsert({'id': 'a', 'source_id': 'info', 'source': '测试', 'source_url': 'https://example.invalid/a', 'title': '机器人竞赛', 'content': '每队4人。', 'published_at': now()})
        response = client.post('/api/chat', json={'message': '机器人竞赛要求'})
        result = response.json()
    assert completed.wait(1) and events[0].is_set()
    assert response.status_code == 200 and result['mode'] == 'ai'
    assert result['answer_partial'] is True and result['model_timeout'] is True
    assert '等待上限' in result['answer'] and '每队4人' in result['answer']
    assert '迟到结果' not in result['answer']
    assert result['references'] and result['citations']
    assert 'fallback_reason' not in result and 'model_error' not in result
